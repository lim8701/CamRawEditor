"""풀해상도 export 파이프라인 (numpy).

화면 프리뷰(GPU 셰이더, 프록시)와 동일한 단계/수식을 풀해상도에 재현한다:

  WB(카메라네이티브 선형화→상대게인→cam->sRGB 매트릭스→sRGB) -> 노출 -> 톤영역
       -> 텍스처/클래리티/디헤이즈 -> 3D LUT -> 대비 -> 톤커브 -> 비네팅 -> 그레인

텍스처/클래리티는 공간(이웃) 연산이라 셰이더의 '프록시 텍셀' 반경을 풀해상도
비율(full/proxy)로 스케일해 시각적으로 맞춘다. 공간 단계는 전체 배열에서,
메모리 큰 3D LUT 단계는 가로 스트립으로 처리한다.
"""

import math
import os
import threading

import numpy as np
import rawpy
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage
from scipy.ndimage import affine_transform, gaussian_filter, map_coordinates, zoom

import coeffs
import date_stamp
import image_loader
import lens
import mist
import raw_loader
import wb
from wb import baked_wb, cam_to_srgb_matrix

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
# 프리뷰 9-tap 가우시안(shaders/blur.frag, 오프셋 1·2·3·4)의 패스당 실제 σ(탭 단위):
# √(2·(0.1945946·1+0.1216216·4+0.054054·9+0.016216·16)) = √2.854 ≈ 1.69.
# export 블러 σ = 이 값 × (프리뷰 탭 간격 px) × scale 로 맞춰야 프리뷰=Export.
_TAP_SIGMA = 1.69


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _tone_zones(c, hi, sh, wh, bl, lb=None):
    # 하이/섀도우=국소 노출(곱셈 게인, 색비·대비 보존). 마스크는 '국소 평균 휘도'(lb,
    # 블러)로 계산 = 라이트룸식 로컬 톤맵. lb 미지정 시 픽셀 휘도로 폴백(히스토그램용).
    if lb is None:
        lb = c @ LUMA
    sh_m = 1.0 - _smoothstep(0.0, 0.75, lb)   # 라이트룸식 넓은 범위(미드톤 겹침)
    hi_m = _smoothstep(0.25, 1.0, lb)
    c = c * np.exp2(sh * coeffs.TONE_HISH * sh_m + hi * coeffs.TONE_HISH * hi_m)[..., None]
    # 화이트/블랙=끝단 레벨(가산, 픽셀 휘도 기준, 좁게 유지).
    l = c @ LUMA
    wh_m = _smoothstep(0.75, 1.0, l)
    bl_m = 1.0 - _smoothstep(0.0, 0.25, l)
    return c + (wh * coeffs.TONE_WHBL * wh_m + bl * coeffs.TONE_WHBL * bl_m)[..., None]


def _blur_rgb(c, sigma):
    return gaussian_filter(c, sigma=(sigma, sigma, 0), mode="nearest")


def _blur_luma(lum, sigma):
    return gaussian_filter(lum, sigma=sigma, mode="nearest")


# ── 로컬대비 코어 (전역 _texture/_clarity/_dehaze 와 마스킹 _sky_adjust 가 공유) ──
# amt 는 스칼라(전역) 또는 (H,W) 배열(마스킹: 계수×마스크). 로컬대비 base(고주파/local-contrast)는
# 호출측이 넘긴다 — 전역·마스킹 모두 **중성(neutral) 베이스**(셰이더 dispSrc 대응)에서 뽑는다.
# ⚠️셰이더 adjust.frag 의 텍스처/클래리티/디헤이즈 분기와 동일 수식 유지(프리뷰=Export).
# dehaze 는 하이브리드('+' DCP 물리 복원 + 잔여 톤모델, '−' 흰 베일 톤모델 — CLAUDE.md 참조).
def _b3(x):
    """스칼라는 그대로, (H,W) 배열은 (H,W,1)로 — (H,W,3) 채널 연산 브로드캐스트용."""
    return x[..., None] if np.ndim(x) else x


def _texture_core(c, amt, hi):
    """텍스처(중주파 가산). hi=고주파(원본-블러, H,W,3). 계수=coeffs.TEXTURE(셰이더와 공유)."""
    return c + hi * _b3(amt) * coeffs.TEXTURE


def _clarity_core(c, amt, d):
    """클래리티(중간톤 로컬대비 가산). d=로컬대비(휘도, H,W). 중간톤 가중은 c 휘도 기준."""
    lum = c @ LUMA
    mid = 1.0 - np.abs(2.0 * lum - 1.0)
    return c + (d * amt * coeffs.CLARITY * mid)[..., None]


def _dehaze_core(c, amt, ld):
    """디헤이즈 톤모델. ld=로컬대비(휘도, H,W). 계수=coeffs.* (셰이더 dehazeTone 과 공유).
    amt<0(흰 베일) 분기는 np.minimum 으로 스칼라/배열 공통 처리."""
    a = _b3(amt)
    c = c + (ld * amt * coeffs.DEHAZE_LOCAL)[..., None]
    c = (c - 0.5) * (1.0 + a * coeffs.DEHAZE_CONTRAST) + 0.5
    neg = np.minimum(amt, 0.0)                     # amt<0 부분만(amt≥0 이면 0)
    c = c + (0.92 - c) * (_b3(-neg) * coeffs.DEHAZE_VEIL)   # 흰 베일(밝아짐)
    l = (c @ LUMA)[..., None]
    return l + (c - l) * (1.0 + a * coeffs.DEHAZE_SAT)


# ⚠️전역 텍스처/클래리티/샤프닝/디헤이즈의 하이패스 소스는 **중성 베이스**(neutral_disp
# = 셰이더 dispSrc/texBlur/claBlur, as-shot WB·노출 0)여야 한다 — 편집본 기준으로 뽑으면
# 노출을 올린 사진에서 고주파가 밝기 스케일만큼 커져 export 가 프리뷰보다 강해진다
# (NR 의 '과거 버그'와 동일 원리; 셰이더는 네 효과 모두 s0=dispSrc 에서 뽑는다).
def _sharpen(c, Ln, amt, radius_px, detail, mask, scale):
    """언샤프 마스크(휘도) — 셰이더 5.5 블록과 동일. 고주파를 중성 베이스 휘도
    Ln(=neutral_disp 휘도, 셰이더 dispSrc 대응)에서 뽑아 현상 결과 c 의 휘도에
    가산(색 불변). 반경 블러 + Detail 미세 고주파 + 엣지 마스킹."""
    Ld = Ln
    # 프리뷰 sharpBlur 탭 간격 = radius px, texBlur = 1.25px → σ = _TAP_SIGMA × 그 간격.
    Lr = _blur_luma(Ld, max(0.3, _TAP_SIGMA * radius_px * scale))   # 반경 블러(sharpBlur 대응)
    Lt = _blur_luma(Ld, max(0.3, _TAP_SIGMA * 1.25 * scale))        # 미세 블러(texBlur 대응)
    hp = (Ld - Lr) + detail * (Ld - Lt)
    step = max(1, int(round(scale)))                         # 프록시 1px ~ scale 풀px
    gx = np.roll(Ld, -step, axis=1) - np.roll(Ld, step, axis=1)
    gy = np.roll(Ld, -step, axis=0) - np.roll(Ld, step, axis=0)
    edge = _smoothstep(0.0, 0.06, np.sqrt(gx * gx + gy * gy))
    m = (1.0 - mask) + mask * edge
    return c + (hp * amt * coeffs.SHARPEN * m)[..., None]


def _dehaze_apply(c, amt, ld, t=None, A=None, conf=0.0):
    """디헤이즈 공용 — 셰이더 dehazeApply 와 동일 수식.
    amt: 스칼라(전역만) 또는 (H,W) 배열(전역+마스크 합산 — 픽셀별 부호 혼재 가능).
    ld: 로컬대비(휘도, H,W). amt>0 인 픽셀만 DCP 물리 복원(+잔여 톤모델)을 conf 로 블렌드,
    amt<=0 픽셀·t 없음·추정 실패: 톤 모델(흰 베일)."""
    tone = _dehaze_core(c, amt, ld)
    if t is None or conf <= 0.0 or not np.any(np.asarray(amt) > 0.0):
        return tone
    pos = np.maximum(amt, 0.0)
    te = np.maximum(1.0 - _b3(pos) * (1.0 - t[..., None]), coeffs.DEHAZE_TMIN)
    Av = np.asarray(A, np.float32)
    phys = _dehaze_core((c - Av) / te + Av, pos * coeffs.DEHAZE_RESID, ld)
    mixed = tone + (phys - tone) * np.float32(conf)
    if np.ndim(amt):   # 배열: 픽셀별 부호 분기(셰이더의 per-pixel if 와 동일)
        return np.where(_b3(np.asarray(amt)) > 0.0, mixed, tone)
    return mixed       # 스칼라: 위 any(amt>0) 통과 = 양수


def _dehaze(c, amt, ld, t_full=None, A=None, conf=0.0):
    """전역(+마스크 합산) 디헤이즈 (프리뷰 셰이더 6단계와 동일).
    ld=중성 로컬대비(셰이더 s0−claBlur 대응) — 호출측(render_full)이 nlum−lb 로 전달."""
    return _dehaze_apply(c, amt, ld, t=t_full, A=A, conf=conf)


def _presence(c, sat, vib):
    """바이브런스/채도 (셰이더와 동일, luma 축 mix -> 휘도 보존)."""
    if vib != 0.0:
        lum = c @ LUMA
        cur = c.max(axis=2) - c.min(axis=2)
        f = 1.0 + vib * (1.0 - np.clip(cur, 0.0, 1.0))
        c = np.clip(lum[..., None] + (c - lum[..., None]) * f[..., None], 0.0, 1.0)
    if sat != 0.0:
        lum = c @ LUMA
        c = np.clip(lum[..., None] + (c - lum[..., None]) * (1.0 + sat), 0.0, 1.0)
    return c


def _rgb2hsv(rgb):
    mx = rgb.max(-1); mn = rgb.min(-1); d = mx - mn
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.zeros_like(mx)
    nz = d > 1e-10
    im = (mx == r) & nz; h[im] = ((g[im] - b[im]) / d[im]) % 6.0
    im = (mx == g) & nz; h[im] = (b[im] - r[im]) / d[im] + 2.0
    im = (mx == b) & nz; h[im] = (r[im] - g[im]) / d[im] + 4.0
    h = (h / 6.0) % 1.0
    s = np.where(mx > 1e-10, d / np.maximum(mx, 1e-10), 0.0)
    return np.stack([h, s, mx], -1).astype(np.float32)


def _hsv2rgb(hsv):
    h = (hsv[..., 0] % 1.0) * 6.0
    s, v = hsv[..., 1], hsv[..., 2]
    i = np.floor(h).astype(np.intp) % 6
    f = h - np.floor(h)
    p = v * (1.0 - s); q = v * (1.0 - f * s); t = v * (1.0 - (1.0 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], -1).astype(np.float32)


def _hsl_mixer(c, hsl_h, hsl_s, hsl_l):
    """HSL 컬러 믹서 (셰이더 hslMixer 와 동일): 픽셀 hue 로 8색상대(45°) 삼각 가중합 → 적용."""
    H = np.asarray(hsl_h, np.float32); S = np.asarray(hsl_s, np.float32); L = np.asarray(hsl_l, np.float32)
    if not (H.any() or S.any() or L.any()):
        return c
    hsv = _rgb2hsv(np.clip(c, 0.0, 1.0))
    h = hsv[..., 0]
    centers = (np.arange(8, dtype=np.float32)) / 8.0
    d = np.abs(((h[..., None] - centers + 0.5) % 1.0) - 0.5)
    w = np.maximum(0.0, 1.0 - d * 8.0)              # (...,8) 단위분할 가중치
    eff_h = w @ H; eff_s = w @ S; eff_l = w @ L
    sat_w = hsv[..., 1]
    hsv[..., 0] = (hsv[..., 0] + eff_h * (coeffs.HSL_HUE_DEG / 360.0) * sat_w) % 1.0
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 + eff_s), 0.0, 1.0)
    hsv[..., 2] = np.clip(hsv[..., 2] * (1.0 + eff_l * coeffs.HSL_LUM), 0.0, 1.0)
    return _hsv2rgb(hsv)


def _apply_lut3d(c, lut, n):
    x = np.clip(c, 0.0, 1.0) * (n - 1)
    b0 = np.floor(x).astype(np.intp)
    b1 = np.minimum(b0 + 1, n - 1)
    f = x - b0
    r0, g0, bb0 = b0[..., 0], b0[..., 1], b0[..., 2]
    r1, g1, bb1 = b1[..., 0], b1[..., 1], b1[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c00 = lut[r0, g0, bb0] * (1 - fr) + lut[r1, g0, bb0] * fr
    c01 = lut[r0, g0, bb1] * (1 - fr) + lut[r1, g0, bb1] * fr
    c10 = lut[r0, g1, bb0] * (1 - fr) + lut[r1, g1, bb0] * fr
    c11 = lut[r0, g1, bb1] * (1 - fr) + lut[r1, g1, bb1] * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def _downscale_to_edge(rgb16, out_edge):
    """rgb16 (uint16) 을 긴 변 = out_edge 로 비율 유지 다운스케일(안티에일리어싱).
    out_edge<=0 이거나 이미 작으면 원본 반환."""
    h, w = rgb16.shape[:2]
    m = max(h, w)
    if out_edge <= 0 or m <= out_edge:
        return rgb16
    f = out_edge / float(m)
    x = rgb16.astype(np.float32)
    sigma = 0.5 * (1.0 / f - 1.0)                 # 축소비에 맞춘 안티에일리어싱
    if sigma > 0.4:
        x = gaussian_filter(x, (sigma, sigma, 0.0))
    nh, nw = max(1, int(round(h * f))), max(1, int(round(w * f)))
    x = zoom(x, (nh / h, nw / w, 1.0), order=1)
    return np.clip(x + 0.5, 0.0, 65535.0).astype(np.uint16)


def _crop_rect(arr, cx, cy, cw, ch):
    """(H,W,...) 배열을 정규화 사각형(cx,cy,cw,ch in [0,1], 좌상단 기준)으로 크롭."""
    h, w = arr.shape[:2]
    x0 = max(0, min(w - 1, int(round(cx * w))))
    y0 = max(0, min(h - 1, int(round(cy * h))))
    x1 = max(x0 + 1, min(w, int(round((cx + cw) * w))))
    y1 = max(y0 + 1, min(h, int(round((cy + ch) * h))))
    return arr[y0:y1, x0:x1]


# 원근(키스톤) 슬라이더 ±100 -> 키스톤 강도. 프리뷰(Main.qml perspMat)와 동일해야 함.
GEO_PERSP_K = 0.35


def _persp_homography(w, h, kxn, kyn, s):
    """소스→출력 호모그래피(3x3). 중심 기준 원근(kxn/kyn)+균등배율(s).
    프리뷰 perspMat 와 동일 수식. kxn/kyn 은 정규화 강도(가장자리에서 w' 가 1±k)."""
    cx, cy = w / 2.0, h / 2.0
    kx = kxn / (w / 2.0)
    ky = kyn / (h / 2.0)
    w0 = 1.0 - kx * cx - ky * cy
    return np.array([
        [s + cx * kx, cx * ky,     cx * w0 - s * cx],
        [cy * kx,     s + cy * ky, cy * w0 - s * cy],
        [kx,          ky,          w0]], dtype=np.float64)


def _warp_perspective(arr, kxn, kyn, s):
    """현상 결과에 원근+배율(중심 기준)을 적용. 출력 화소->소스 역매핑(map_coordinates)."""
    h, w = arr.shape[:2]
    H = _persp_homography(w, h, kxn, kyn, s)
    Hinv = np.linalg.inv(H)
    ys, xs = np.indices((h, w), dtype=np.float32)   # float32로 충분(6000px) — float64는 ~1.2GB
    ones = np.ones_like(xs)
    sx = Hinv[0, 0] * xs + Hinv[0, 1] * ys + Hinv[0, 2] * ones
    sy = Hinv[1, 0] * xs + Hinv[1, 1] * ys + Hinv[1, 2] * ones
    sw = Hinv[2, 0] * xs + Hinv[2, 1] * ys + Hinv[2, 2] * ones
    sx /= sw
    sy /= sw
    out = np.empty_like(arr)
    for ch in range(arr.shape[2]):
        out[..., ch] = map_coordinates(arr[..., ch], [sy, sx], order=1,
                                       mode="constant", cval=0)
    return out


def _apply_geometry(arr, p):
    """현상 결과(H,W,3 uint8)에 지오메트리 적용 — 프리뷰(QML 뷰 변환)와 동일 순서/정의:
    플립 -> 90° 회전 -> 스트레이튼(자유각 회전 + 채움 줌) -> 자유 사각 크롭.
    회전 방향은 Qt Rotation 과 동일(양수 = 시계방향). 크롭 사각형은 캔버스A(플립+90+
    스트레이튼 후) 정규화 좌표이며 프리뷰 cropX/Y/W/H 와 동일."""
    flip_h = bool(p.get("flipH", False))
    flip_v = bool(p.get("flipV", False))
    quarter = int(p.get("quarterTurns", 0)) % 4
    angle = float(p.get("rotateAngle", 0.0))      # 도, CW +
    cx = float(p.get("cropX", 0.0))
    cy = float(p.get("cropY", 0.0))
    cw = float(p.get("cropW", 1.0))
    ch = float(p.get("cropH", 1.0))
    geo_v = float(p.get("geoV", 0.0))         # 수직 원근 슬라이더 (-100..100)
    geo_h = float(p.get("geoH", 0.0))         # 수평 원근 슬라이더 (-100..100)
    geo_s = float(p.get("geoScalePct", 100.0))  # 배율 슬라이더 (50..150 %)

    if flip_h:
        arr = arr[:, ::-1]
    if flip_v:
        arr = arr[::-1, :]
    if quarter:
        arr = np.rot90(arr, k=-quarter)           # k<0 = 시계방향(= Qt 양수 회전)
    arr = np.ascontiguousarray(arr)

    h, w = arr.shape[:2]
    if abs(angle) > 1e-3:
        cA = w / float(h)
        t = math.radians(abs(angle))
        Z = math.cos(t) + max(cA, 1.0 / cA) * math.sin(t)   # 채움 줌(프리뷰 straightenZoom 과 동일)
        phi = math.radians(angle)
        cph, sph = math.cos(phi), math.sin(phi)
        pcy, pcx = (h - 1) / 2.0, (w - 1) / 2.0   # 회전 중심 px (크롭 cx/cy 와 구분)
        # 출력(y,x) -> 입력(y,x) 역매핑: 중앙 기준 (시계 회전 phi + 줌 Z) 의 역변환.
        m00, m01 = cph / Z, -sph / Z
        m10, m11 = sph / Z, cph / Z
        mat = np.array([[m00, m01, 0.0],
                        [m10, m11, 0.0],
                        [0.0, 0.0, 1.0]], dtype=np.float64)
        off = np.array([pcy - (m00 * pcy + m01 * pcx),
                        pcx - (m10 * pcy + m11 * pcx),
                        0.0], dtype=np.float64)
        # mode=nearest: 채움 줌이 사실상 정확해 경계 1~2px 만 바깥을 샘플 -> 검정 대신
        # 가장자리 색 복제(프리뷰 GPU edge-clamp 샘플링과 정합).
        arr = affine_transform(arr, mat, offset=off, order=1,
                               mode="nearest").astype(arr.dtype)

    # 원근(키스톤)+배율 — 스트레이튼 뒤, 크롭 앞(프리뷰 Matrix4x4 와 동일 순서/수식)
    if abs(geo_v) > 1e-3 or abs(geo_h) > 1e-3 or abs(geo_s - 100.0) > 1e-3:
        arr = np.ascontiguousarray(arr)
        arr = _warp_perspective(arr, (geo_h / 100.0) * GEO_PERSP_K,
                                (geo_v / 100.0) * GEO_PERSP_K, geo_s / 100.0)

    if cx > 0.0 or cy > 0.0 or cw < 1.0 or ch < 1.0:
        arr = _crop_rect(arr, cx, cy, cw, ch)
    return np.ascontiguousarray(arr)


def compose_curves(master, r, g, b):
    """채널별 톤커브를 256×3 LUT 로 합성: out_C = channelCurve_C(masterCurve(in_C)).

    master/r/g/b 는 각각 256개 출력값(0..1) — 마스터 커브를 먼저 적용하고 그 결과에
    채널별(R/G/B) 커브를 적용한 합성 LUT(R/G/B 열)를 만든다. 셰이더/export 가 채널값으로
    이 LUT 의 해당 채널을 샘플링하면 두 커브가 합성 적용된다."""
    xs = np.linspace(0.0, 1.0, 256)
    m = np.asarray(master, dtype=np.float32)
    out = np.empty((256, 3), dtype=np.float32)
    for i, ch in enumerate((r, g, b)):
        out[:, i] = np.interp(m, xs, np.asarray(ch, dtype=np.float32))
    return out


def _color_grade(c, hue_sh, sat_sh, hue_mid, sat_mid, hue_hi, sat_hi, balance):
    """컬러 그레이딩(스플릿 토닝) — 셰이더 adjust.frag 9.5 단계와 동일 수식.
    휘도 마스크(섀도/미드/하이라이트, balance 가 감마로 분포 이동) × 색조 틴트(hue 0..1, sat 0..1)."""
    if sat_sh <= 0.0 and sat_mid <= 0.0 and sat_hi <= 0.0:
        return c
    L = (c @ LUMA).astype(np.float32)
    Lb = np.clip(L, 0.0, 1.0) ** np.float32(2.0 ** (-balance))
    wsh = np.clip(1.0 - 2.0 * Lb, 0.0, 1.0)
    whi = np.clip(2.0 * Lb - 1.0, 0.0, 1.0)
    wmid = 1.0 - wsh - whi

    def _tdir(hue, sat):
        return (_hsv2rgb(np.array([hue, 1.0, 1.0], np.float32)) - 0.5) * np.float32(sat)
    dsh, dmid, dhi = _tdir(hue_sh, sat_sh), _tdir(hue_mid, sat_mid), _tdir(hue_hi, sat_hi)
    delta = (dsh * wsh[..., None] + dmid * wmid[..., None] + dhi * whi[..., None]) * np.float32(coeffs.COLOR_GRADE)
    return np.clip(c + delta, 0.0, 1.0).astype(np.float32)


def _sky_adjust(c, m, sp, nd_texhi=None, nd_lc=None):
    """하늘(로컬) 조정 — 셰이더 adjust.frag 9.7 단계와 동일 수식. m=0 인 곳은 항등.
    c=display sRGB (H,W,3), m=마스크(H,W)[0,1] (invert 는 render_full 이 이미 베이크),
    sp=파라미터 dict. nd_texhi=중성 텍스처 고주파(RGB), nd_lc=중성 로컬대비(luma).
    ⚠️노출/하이라이트/섀도/디헤이즈(sp exp/hi/sh/dehaze)는 여기가 아니라 전역과 같은
      단계(프론트엔드/tone_zones/디헤이즈 6단계)에서 강도 합산으로 적용됨 — 전역 조절과
      동일한 반응(진짜 stop·영역 톤맵·LUT 전 디헤이즈) 보장."""
    m1 = m[..., None]
    out = c.copy()
    out[..., 0] *= (1.0 + sp["temp"] * coeffs.SKY_TEMP * m)    # 색온도(+따뜻 R↑B↓)
    out[..., 2] *= (1.0 - sp["temp"] * coeffs.SKY_TEMP * m)
    out[..., 1] *= (1.0 - sp["tint"] * coeffs.SKY_TINT * m)    # 틴트(+마젠타 G↓)
    # 로컬대비 3종 — 전역과 동일 코어 공유(계수×마스크를 amt 로, 중성 base 를 로컬대비로 전달).
    if sp["texture"] != 0.0 and nd_texhi is not None:          # 텍스처(중주파, 중성 고주파)
        out = _texture_core(out, sp["texture"] * m, nd_texhi)
    if sp["clarity"] != 0.0 and nd_lc is not None:             # 클래리티(중간톤 로컬대비, 중성)
        out = _clarity_core(out, sp["clarity"] * m, nd_lc)
    if sp["contrast"] != 1.0:                                  # 대비(전역 contrast 곱수, 마스크 게이팅)
        out = (out - 0.5) * (1.0 + (sp["contrast"] - 1.0) * m1) + 0.5
    la = (out @ LUMA)[..., None]                               # 채도
    out = la + (out - la) * (1.0 + sp["sat"] * m1)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def render_full(path, kelvin, tint, p, lut_arr, lut_n, curve_rgb,
                proxy_edge=2560, strip=256, bitdepth=8, sky_masks=None, progress=None,
                haze=None):
    """풀해상도 RAW 를 조정값으로 현상해 (H,W,3) RGB 로 반환.
    bitdepth=8 -> uint8, 16 -> uint16(계조/헤드룸 보존, TIFF/PNG 16bit 저장용).
    progress: 선택적 콜백(0..1). 디코드/공간단계/스트립 루프 경계에서 호출(픽셀 결과 불변).
    haze: (t_small, A, conf) — haze.py 추정치(프록시 기준). '+' 디헤이즈의 DCP 물리 복원용.
          t 는 풀해상도로 업샘플(하늘 마스크와 동일 방식) → 프리뷰=Export 정합."""
    def _prog(f):
        if progress is not None:
            try:
                progress(f)
            except Exception:
                pass   # 진행률 보고는 부수효과일 뿐 — 실패해도 export 본체는 진행
    # 소스 디코드 — 두 갈래 모두 `nat`(카메라네이티브 scene-linear float32, 자동노출 적용 후)로
    # 수렴한다. 이후 단계는 소스 종류를 모른다.
    # ⚠️일반 이미지는 filmic⁻¹ 결과가 1.0 을 넘어(최대 2.17/3.83) uint16 캐리어로 표현이 안 되고,
    #   lens.apply 는 float 입력을 [0,1] 로 클립하며 _downscale_to_edge 는 uint16 전용이라
    #   **다운스케일/렌즈 보정은 RAW 분기 안(감마 코드 공간)에 그대로 둔다.**
    if image_loader.is_display_image(path):
        cam, ref, as_shot, as_shot_tint = image_loader.meta()
        # 축소는 display 코드 공간에서(프록시와 동일) → filmic⁻¹ → scene-linear.
        # 자동노출/렌즈 프로파일 없음(auto_exposure_gain(None)=1.0 과 동치).
        nat = image_loader.scene_linear(path, int(p.get("outEdge", 0) or 0))
    else:
        with rawpy.imread(path) as raw:
            cam = np.array(raw.rgb_xyz_matrix)[:3, :3]
            ref = np.array(raw.daylight_whitebalance, dtype=float)[:3]
            ref = ref / ref[1] if (ref[1] > 0 and np.all(np.isfinite(ref))) else np.ones(3)  # 빈/0 WB → 중성 폴백(NaN/블랙 방지)
            as_shot, as_shot_tint = wb.estimate_wb(cam, ref, raw.camera_whitebalance)  # as-shot WB(K,tint)
            target_median = raw_loader._embedded_jpeg_median(raw)   # 이미지별 자동 노출 목표(중앙값)
            # 프록시와 동일: 카메라 네이티브(매트릭스 미적용) + TREF daylight 베이크 + 감마 저장.
            rgb16 = raw.postprocess(user_wb=baked_wb(cam, ref),
                                    output_color=rawpy.ColorSpace.raw,
                                    # Bayer=AHD(고화질)/X-Trans=LINEAR(프록시 정합). raw_loader.load_full 과 동일.
                                    demosaic_algorithm=raw_loader._export_demosaic(raw),
                                    output_bps=16, no_auto_bright=True,
                                    gamma=(2.4, 12.92),
                                    highlight_mode=rawpy.HighlightMode.Clip)

        # 출력 해상도 지정(긴 변): 처리 전 다운스케일 -> 빠르고, 효과 sigma 가 해상도에
        # 비례해 룩 동일 유지(그레인/스탬프도 이미지 상대 크기라 일관).
        rgb16 = _downscale_to_edge(rgb16, int(p.get("outEdge", 0) or 0))
        if p.get("lensCorrection", True):
            rgb16 = lens.apply(rgb16, lens.load_profile(path))   # RAF 내장 샷별 보정(프록시와 동일)
        # 카메라네이티브 감마 -> 선형화 -> 자동노출(중앙값). 여기서 float32 로 승격하는 위치는
        # 예전 그대로다(디코드 직후부터 float 로 들고 가면 26MP 에서 150MB 를 더 오래 문다).
        nat = wb.srgb_to_linear(rgb16.astype(np.float32) / 65535.0)
        nat *= wb.auto_exposure_gain(target_median, cam, ref, as_shot, nat)
        del rgb16
    _prog(0.30)   # 디코드 + 다운스케일 + 렌즈 보정 완료(가장 큰 단일 비용)

    h, w, _ = nat.shape
    scale = max(h, w) / float(proxy_edge)     # 프록시 텍셀 반경 -> 풀해상도 px

    # 로컬 마스크 레이어(최대 3) — 각 레이어 파라미터 + 마스크(프록시→풀해상도 업샘플, invert 베이크).
    # p["maskLayers"]=[{skyExp,...,invert}] (신규). 구(舊) 평면 스키마는 레이어 0으로 매핑(하위호환).
    # 마스크 노출/톤존이 전역과 같은 단계(프론트엔드/tone_zones)에서 적용되므로 여기서 먼저 준비.
    _lp = p.get("maskLayers")
    if not _lp:                              # 하위호환: 구 평면(skyExp 등) → 단일 레이어로
        _flat_any = (any(p.get(k) for k in ("skyExp", "skyTemp", "skyTint", "skySat", "skyHi",
                     "skyShadows", "skyTexture", "skyClarity", "skyDehaze"))
                     or float(p.get("skyContrast", 1.0)) != 1.0)
        _lp = [p] if _flat_any else []
    _masks_in = list(sky_masks) if sky_masks is not None else []
    sky_layers = []                          # [(sky_dict, skym_full)] — 마스크 있는 활성 레이어만
    for _i, lp in enumerate(_lp[:5]):
        sky = {"exp": float(lp.get("skyExp", 0)), "temp": float(lp.get("skyTemp", 0)),
               "tint": float(lp.get("skyTint", 0)), "sat": float(lp.get("skySat", 0)),
               "hi": float(lp.get("skyHi", 0)), "sh": float(lp.get("skyShadows", 0)),
               "texture": float(lp.get("skyTexture", 0)), "clarity": float(lp.get("skyClarity", 0)),
               "dehaze": float(lp.get("skyDehaze", 0)), "contrast": float(lp.get("skyContrast", 1.0)),
               "invert": bool(lp.get("skyInvert", False))}
        sky_any = any(sky[k] for k in ("exp", "temp", "tint", "sat", "hi", "sh",
                                       "texture", "clarity", "dehaze")) or sky["contrast"] != 1.0
        m = _masks_in[_i] if _i < len(_masks_in) else None
        if not (sky_any and m is not None):
            continue
        sm = np.asarray(m, np.float32)
        mh, mw = sm.shape[:2]
        if (mh, mw) != (h, w):
            sm = zoom(sm, (h / mh, w / mw), order=1).astype(np.float32)
        sm = np.clip(sm, 0.0, 1.0)
        if sky["invert"]:
            sm = 1.0 - sm                     # 셰이더 skyM 과 동일하게 1회 베이크
        sky_layers.append((sky, sm))

    def _accum(key):                          # Group A 누적(강도×마스크 합) — 스칼라 0.0 또는 (H,W)
        acc = 0.0
        for _sky, _sm in sky_layers:
            if _sky[key] != 0.0:
                acc = acc + _sky[key] * _sm
        return acc
    exp_add, hi_add, sh_add, deh_add = (_accum("exp"), _accum("hi"), _accum("sh"), _accum("dehaze"))

    # === scene-linear 프론트엔드(셰이더 adjust.frag 와 동일 수학) ===
    # nat(카메라네이티브 scene-linear, 자동노출 적용 후 — 위 디코드 분기가 만든다)
    # -> WB(카메라공간) -> cam->sRGB 매트릭스 -> scene-linear sRGB -> 유저노출(scene-linear)
    # -> filmic(단일 톤커브) -> display sRGB.
    M = cam_to_srgb_matrix(cam).astype(np.float32)
    # 중성 display 베이스(as-shot WB, 유저노출/desat 없음) — 셰이더 dispSrc/claBlur 와 동일.
    #   hi/sh 톤영역 마스크는 이 '장면 구조' 휘도로 계산해야 프리뷰=Export(노출 무관 마스크).
    neutral_disp = wb.filmic((nat * wb.rel_gain(cam, ref, as_shot, as_shot_tint).astype(np.float32))
                             @ M.T).astype(np.float32)
    # 1) 미스트(디퓨전) 필터 — 셰이더 1단계 == mist.apply. **유저 WB/매트릭스/노출보다 앞**:
    #    그 셋은 픽셀마다 같은 선형 연산이라 블러와 정확히 교환되므로 결과는 같으면서 산란 필드가
    #    세 슬라이더와 무관해진다(프리뷰가 이미지당 1회 계산해 캐시할 수 있는 이유).
    #    ⚠️중성 베이스(neutral_disp)는 위에서 이미 만들었다 — 미스트가 로컬대비/톤마스크의
    #    '장면 구조' 기준을 흔들면 안 된다. σ 는 프레임 긴 변 비율이라 프록시/풀해상도 룩이 일치.
    _mist_amt = float(p.get("mistAmt", 0.0))
    if _mist_amt > 0.0:
        nat = mist.apply(nat, _mist_amt, float(p.get("mistChar", 0.0)),
                         float(p.get("mistRadius", 1.0)), float(p.get("mistHi", 0.8)),
                         max(nat.shape[:2]), color=float(p.get("mistColor", 0.0)))
    nat = nat * wb.rel_gain(cam, ref, kelvin, tint).astype(np.float32)   # 유저 WB(카메라공간)
    # 노출 = scene-linear 배수. 마스크 노출(skyExp)은 전역과 같은 지수에 합산(셰이더 0단계 동일)
    # → 마스크 영역도 진짜 stop + filmic 하이라이트 롤오프로 반응.
    _base_exp = float(p.get("exposure", 0.0))
    if isinstance(exp_add, np.ndarray):
        expo_gain = np.exp2(_base_exp + exp_add)[..., None]
    else:
        expo_gain = 2.0 ** _base_exp
    linsrgb = (nat @ M.T) * expo_gain
    disp = wb.filmic(linsrgb).astype(np.float32)                     # scene→display[0,1]
    del nat, linsrgb          # 이후 미사용 — 26MP 공간단계 피크에서 조기 해제(수백 MB)
    # 하이라이트 디새추레이션: near-clip 센서클립 색끼(예: 불꽃 코어 청록) 제거 → 중성(흰색).
    # ⚠️쿨(청/녹 우세) 하이라이트만 중성화한다 — 밝은 빨강/주황 광원(예: 네온·간판)은
    # 보존해야 하므로 max(G,B)-R 로 게이트(따뜻한 색은 음수→게이트 0). filmic 뒤 display 공간.
    # ⚠️hlDesat=0(일반 이미지 입력)이면 통째로 끈다 — 센서 클립이 없는 display-referred 소스에선
    # 밝은 파랑/청록이 '정상 색'이라 이 단계가 하늘·네온을 흰색으로 날린다. 셰이더 0.5 단계와 동일.
    _hld = float(p.get("hlDesat", 1.0))
    if _hld > 0.0:
        _mx = disp.max(axis=2, keepdims=True)
        _cool = np.maximum(disp[..., 1:2], disp[..., 2:3]) - disp[..., 0:1]
        disp = disp + (_mx - disp) * (_hld * _smoothstep(0.95, 1.0, _mx)
                                      * _smoothstep(0.05, 0.35, _cool))

    hi, sh = float(p.get("highlights", 0)), float(p.get("shadows", 0))
    wh, bl = float(p.get("whites", 0)), float(p.get("blacks", 0))
    tex = float(p.get("texAmt", p.get("texture", 0)))
    cla = float(p.get("clarity", 0))
    deh = float(p.get("dehaze", 0))
    vig = float(p.get("vignette", 0))
    con = float(p.get("contrast", 1.0))
    sat = float(p.get("saturation", 0))
    vib = float(p.get("vibrance", 0))
    lut_strength = float(p.get("lutStrength", 1.0))
    grain_amt = float(p.get("grainAmt", 0))
    grain_size = float(p.get("grainSize", 0.5))
    grain_rough = float(p.get("grainRough", 0.1))    # 옥타브 감쇠비(거칠기, 실측 피팅 기본값)
    grain_color = float(p.get("grainColor", 0.3))    # 3층 독립도(색 얼룩)
    grain_shape = float(p.get("grainShape", 0.0))    # 0=사각 셀(기본) / 1=원판
    sharp_amt = float(p.get("sharpenAmt", 0.0))
    sharp_radius = float(p.get("sharpenRadius", 1.0))
    sharp_detail = float(p.get("sharpenDetail", 0.25))
    sharp_mask = float(p.get("sharpenMask", 0.0))
    hsl_h = p.get("hslH", [0.0] * 8)   # HSL 컬러 믹서 8색상대 (색상/채도/휘도)
    hsl_s = p.get("hslS", [0.0] * 8)
    hsl_l = p.get("hslL", [0.0] * 8)
    stamp_text = str(p.get("stampText", "") or "")
    do_stamp = bool(p.get("dateStamp", False)) and stamp_text != ""
    stamp_rot = int(p.get("stampRot", 0))   # 촬영 방향(센서→업라이트 CW 회전) — 데이트백 회전/코너
    stamp_style = str(p.get("stampStyle", "7c_bold"))   # 폰트 방식(STYLES 키)
    stamp_size = float(p.get("stampSize", 0.032))       # 크기(숫자높이/짧은변 비율)
    stamp_margin = float(p.get("stampMargin", 0.05))    # 코너 여백/짧은변 비율
    stamp_color = str(p.get("stampColor", "#FF8A29"))   # 각인 색(중성=흑백 사진용 백색 각인)
    stamp_glow = float(p.get("stampGlow", 1.0))         # 글로우 밝기(헤일로 가중 배율)
    stamp_spread = float(p.get("stampSpread", 1.0))     # 글로우 영역(헤일로 반경 배율)
    # --- 전역/공간 단계 (전체 배열). 노출/하이라이트는 filmic 프론트엔드에서 이미 처리됨 ---
    # 프리뷰 블러(shaders/blur.frag)는 오프셋 1·2·3·4 탭의 9-tap 가우시안 → 패스당
    # 실제 σ = √(2·(w1+4w2+9w3+16w4)) = √2.854 ≈ 1.69 탭(가중치 0.1946/0.1216/0.0541/0.0162).
    # 예전 상수(1.5, 7.0)는 σ≈1.2/탭 가정에서 나온 파생 오류라 export 가 프리뷰보다 ~1.4배
    # 좁았음. 프리뷰 탭 간격: texBlur 1.25px, claBlur 1.5px×(÷4 다운샘플)=6px 프록시.
    sigma_tex = _TAP_SIGMA * 1.25 * scale   # 프리뷰 텍스처 블러(1.25px/탭) 대응 ≈ 2.11×scale
    sigma_cla = _TAP_SIGMA * 6.0 * scale     # 프리뷰 클래리티/디헤이즈/톤영역 마스크(6px/탭) ≈ 10.1×scale
    c = disp
    # hi/sh 국소 톤맵 마스크 = 중성 베이스(neutral_disp)의 국소 평균 휘도. 셰이더 claBlur(중성) 대응.
    nlum = (neutral_disp @ LUMA).astype(np.float32)
    # lb(클래리티 반경 블러, 26MP 에서 sigma_cla~25 라 무거움)는 실제 소비될 때만 1회 지연 계산.
    # 소비자: tone_zones(hi/sh/wh/bl 마스크) · 비-AI 컬러NR · 클래리티/디헤이즈 하이패스.
    _lb = [None]
    def get_lb():
        if _lb[0] is None:
            _lb[0] = _blur_luma(nlum, sigma_cla)
        return _lb[0]
    # 마스크 하이라이트/섀도(skyHi/skyShadows)는 전역과 같은 tone_zones 에서 강도 합산
    # (셰이더 3단계 동일 — 과거 9.7 픽셀휘도 근사와 달리 전역과 동일한 영역 톤맵 반응).
    hi_eff = hi + hi_add       # hi_add/sh_add = 스칼라 0.0 또는 (H,W) 누적 배열
    sh_eff = sh + sh_add
    # 전부 0이면 tone_zones 는 항등(exp2(0)=1 곱 + 0 가산) → 스킵해 무거운 lb 계산 회피.
    # (c 는 이 지점에서 이미 filmic 출력 [0,1]≥0 이라 np.maximum(_,0) 도 무동작.)
    if (hi != 0.0 or sh != 0.0 or wh != 0.0 or bl != 0.0
            or isinstance(hi_add, np.ndarray) or isinstance(sh_add, np.ndarray)):
        c = np.maximum(_tone_zones(c, hi_eff, sh_eff, wh, bl, get_lb()), 0.0)
    # 노이즈 리덕션(텍스처/샤프닝 앞) — 셰이더 3.5 단계와 동일하게 **중성 베이스**(dispSrc 대응)에서
    # 고주파/크로마를 뽑아 편집본 c 에서 뺀다. ⚠️편집본 기반으로 계산하면 노출을 올린 사진에서
    # export 의 NR 이 프리뷰보다 강해짐(과거 버그 — 밝기 스케일만큼 고주파가 커지므로).
    ln = float(p.get("lumaNR", 0)); cn = float(p.get("colorNR", 0))
    # AI 디노이즈 베이스(NAFNet, 풀해상도 타일 추론 — 프리뷰 nrBase 텍스처와 동일 모델):
    # RGB 전체를 1회 계산해 휘도/컬러 NR 이 공유(셰이더 nrBase RGBA + nrChroma 게이트 대응).
    # 해상도가 달라 프록시 프리뷰와 노이즈 통계가 약간 다른 건 AI NR 의 본질적 근사.
    # 실패 시 None → 기존 가이디드/블러 폴백(프리뷰 폴백과 동일 동작).
    den_rgb = den_l = None
    if bool(p.get("aiNr", False)) and (ln > 0.0 or cn > 0.0):
        try:
            import ai_denoise
            den_rgb = ai_denoise.denoise_rgb(
                neutral_disp, progress=lambda f: _prog(0.31 + 0.21 * f),  # 타일 → 필름 카운터
                drift_sigma=ai_denoise.DRIFT_SIGMA * scale,  # 드리프트 반경도 해상도 스케일
                pace=ai_denoise.UI_PACE)   # export 도 앱 내 백그라운드 — UI 양보(140타일 +4s)
            den_l = (den_rgb @ LUMA).astype(np.float32)
        except Exception as exc:
            print(f"[export] AI 디노이즈 실패(가이디드/블러 폴백): {exc}")
    if ln > 0.0:
        # 휘도 NR: 노이즈 성분 = 중성 luma − 디노이즈드 베이스 luma(AI 또는 가이디드 필터).
        if den_l is not None:
            nlum_dn = den_l
        else:
            from sky_seg import _guided_filter
            r = max(1, int(round(coeffs.NR_RADIUS * scale)))   # 프록시 px → 풀해상도 px
            nlum_dn = _guided_filter(nlum, nlum, r, coeffs.NR_EPS)
        noise_l = nlum - nlum_dn
        c = np.clip(c - (noise_l * ln)[..., None], 0.0, 1.0)
    if cn > 0.0:
        if den_rgb is not None:
            # AI 크로마: 중성 chroma − AI 디노이즈드 chroma(디테일 보존형 — 셰이더 nrChroma 분기)
            chroma_detail = (neutral_disp - nlum[..., None]) - (den_rgb - den_l[..., None])
        else:
            bl_ = _blur_rgb(neutral_disp, sigma_cla)           # 셰이더: claBlur(중성 RGB)
            # luma(blur_rgb) == blur(luma) (선형 연산) → lb 재사용
            chroma_detail = (neutral_disp - nlum[..., None]) - (bl_ - get_lb()[..., None])
        c = np.clip(c - chroma_detail * cn, 0.0, 1.0)
    # 중성 하이패스(셰이더 texBlur/claBlur/dispSrc 대응) — 전역과 마스크(sky) 경로가 공유.
    # ⚠️편집본(c/disp) 기준으로 뽑으면 노출 편집 시 export 효과가 프리뷰보다 강해짐(상단 주석).
    _any_tex = any(s["texture"] != 0.0 for s, _ in sky_layers)
    _any_cla = any(s["clarity"] != 0.0 for s, _ in sky_layers)
    _any_deh = any(s["dehaze"] != 0.0 for s, _ in sky_layers)
    nd_texhi = nd_lc = None
    if tex != 0.0 or _any_tex:
        nd_texhi = (neutral_disp - _blur_rgb(neutral_disp, sigma_tex)).astype(np.float32)
    if cla != 0.0 or deh != 0.0 or _any_cla or _any_deh:
        nd_lc = (nlum - get_lb()).astype(np.float32)
    if tex != 0.0:
        c = _texture_core(c, tex, nd_texhi)
    if cla != 0.0:
        c = _clarity_core(c, cla, nd_lc)
    if sharp_amt > 0.0:
        c = _sharpen(c, nlum, sharp_amt, sharp_radius, sharp_detail, sharp_mask, scale)
    # DCP t-맵 — 전역 '+' 디헤이즈와 하늘 '+' 디헤이즈(스트립 루프)가 공용. 필요 시에만 업샘플.
    haze_t_full = haze_A = None
    haze_conf = 0.0
    need_haze = (deh > 0.0) or any(s["dehaze"] > 0.0 for s, _ in sky_layers)
    if need_haze and haze is not None and haze[0] is not None and float(haze[2]) > 0.0:
        ht, haze_A, haze_conf = haze
        haze_conf = float(haze_conf)
        th, tw = np.asarray(ht).shape[:2]
        haze_t_full = np.clip(zoom(np.asarray(ht, np.float32), (h / th, w / tw), order=1),
                              0.0, 1.0)[:h, :w]
    # 마스크 디헤이즈(skyDehaze)도 전역과 같은 단계에서 강도 합산(셰이더 6단계 동일 —
    # 과거 9.7 적용은 LUT/커브 뒤라 같은 값에도 결과가 달랐음).
    deh_amt = deh + deh_add    # deh_add = 스칼라 0.0 또는 (H,W) 누적 배열
    if np.any(np.asarray(deh_amt) != 0.0):
        c = _dehaze(c, deh_amt, nd_lc, t_full=haze_t_full, A=haze_A, conf=haze_conf)
    np.clip(c, 0.0, 1.0, out=c)
    _prog(0.55)   # 전역/공간 단계(블러·텍스처·클래리티·샤프닝·디헤이즈·NR) 완료

    # 비네팅 마스크(정규화 좌표, 해상도 무관)
    if vig != 0.0:
        yy = (np.arange(h, dtype=np.float32) / max(1, h - 1)) - 0.5   # 1px 변에서 0나눗셈(NaN) 방지
        xx = (np.arange(w, dtype=np.float32) / max(1, w - 1)) - 0.5
        rr = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2) / 0.7071
        vig_mask = (1.0 + vig * coeffs.VIGNETTE * _smoothstep(0.35, 1.0, rr)).astype(np.float32)
    else:
        vig_mask = None

    # 필름 그레인 — 셰이더 12단계와 동일한 절차적 필드(_grain_field)를 스트립마다 생성.
    # 좌표가 uv 절대값 기반이라 스트립 경계 이음매 없음(예전 격자+zoom 방식과 달리 시드 불필요).
    grain_grid_n = 4500.0 + (1300.0 - 4500.0) * grain_size if grain_amt > 0.0 else 0.0
    # ⚠️셀 크기는 **긴 변** 기준 — 격자 좌표가 가로폭 기준(u*gridN)이라 보정 없이는 세로 사진의
    #   셀이 가로 대비 1.5배 잘아져(0.88→0.59px) 서브픽셀 평균에 σ 17% 를 잃고 입자가 픽셀
    #   아래로 내려가 결정이 안 보였다(실측: 같은 설정 σ 0.210 vs 0.175). 필름은 방향과 무관하고,
    #   원래 캘리브레이션도 필름 스캔(가로 3024)과 '같은 출력 폭' 비교였다. min(1,W/H) 를 곱해
    #   cellPx = 긴변/gridN 으로 고정한다(가로는 불변, 세로만 굵어짐). ⚠️셰이더 12단계와 동일.

    # 하늘(로컬) 조정용 중성 하이패스(nd_texhi/nd_lc)는 전역 단계에서 이미 계산·공유됨
    # (전역 텍스처/클래리티/디헤이즈와 동일한 중성 베이스 — 셰이더 texBlur/claBlur 대응).

    # --- LUT/대비/커브/비네팅 (메모리 큰 LUT 는 스트립) ---
    maxv = 65535.0 if bitdepth == 16 else 255.0
    dt = np.uint16 if bitdepth == 16 else np.uint8
    out = np.empty((h, w, 3), dtype=dt)
    xs = np.linspace(0.0, 1.0, 256)
    crgb = np.asarray(curve_rgb, dtype=np.float32)   # (256,3) 합성 채널 커브
    # 컬러 그레이딩 파라미터(hue 슬라이더는 도(0..360) → 0..1 정규화). 셰이더 cg* uniform 과 동일.
    cg = (float(p.get("cgShadowHue", 0.0)) / 360.0, float(p.get("cgShadowSat", 0.0)),
          float(p.get("cgMidHue", 0.0)) / 360.0, float(p.get("cgMidSat", 0.0)),
          float(p.get("cgHighHue", 0.0)) / 360.0, float(p.get("cgHighSat", 0.0)),
          float(p.get("cgBalance", 0.0)))
    for y in range(0, h, strip):
        blk = c[y:y + strip]
        if lut_arr is not None:
            looked = _apply_lut3d(blk, lut_arr, lut_n)
            blk = blk * (1.0 - lut_strength) + looked * lut_strength   # 강도 블렌딩
        if sat != 0.0 or vib != 0.0:
            blk = _presence(blk, sat, vib)                             # 바이브런스/채도
        blk = _hsl_mixer(blk, hsl_h, hsl_s, hsl_l)                     # HSL 컬러 믹서
        blk = np.clip((blk - 0.5) * con + 0.5, 0.0, 1.0)
        for ch in range(3):
            blk[..., ch] = np.interp(blk[..., ch], xs, crgb[:, ch])
        blk = _color_grade(blk, *cg)                                   # 컬러 그레이딩(톤커브 뒤)
        for _sky, _sm in sky_layers:                                   # 로컬 조정 — 레이어 0→1→2 순서(셰이더와 동일)
            blk = _sky_adjust(blk, _sm[y:y + strip], _sky,
                              None if nd_texhi is None else nd_texhi[y:y + strip],
                              None if nd_lc is None else nd_lc[y:y + strip])
        if vig_mask is not None:
            blk = blk * vig_mask[y:y + strip, :, None]
        out[y:y + strip] = np.rint(np.clip(blk, 0.0, 1.0) * maxv).astype(dt)
        _prog(0.55 + 0.40 * min(1.0, (y + strip) / float(h)))   # LUT/대비/커브/비네팅 스트립 진행

    # 필름 그레인 — 장면(에멀전 입자, 셰이더와 동일). 스탬프는 크롭 후 최종 프레임에 찍는다.
    grain_grid_n *= min(1.0, w / h)          # 긴 변 기준 셀 크기(위 주석)
    if grain_grid_n > 0.0:
        gk = grain_amt * coeffs.GRAIN

        def _grain_strip(y):
            y1 = min(y + strip, h)
            f = out[y:y1].astype(np.float32) / maxv
            # 왜도 계수 — 섀도는 밝은 점(+), 하이라이트는 어두운 점(−). skew≈6cσ, σ=1/√12
            # (샘플 단위 필드의 해석적 표준편차). ⚠️셰이더 12단계와 동일 식.
            l0 = np.clip(f @ LUMA, 0.0, 1.0)
            skew_c = (np.float32(coeffs.GRAIN_SKEW) * (1.0 - 2.0 * l0)
                      * np.float32(np.sqrt(12.0) / 6.0))[..., None]
            g = _grain_field(y, y1, h, w, grain_grid_n, w / h, grain_rough, grain_color,
                             skew_c, grain_shape)
            # 노출 의존 진폭 — 특성곡선 기울기 벨(미드톤 w=1).
            # ⚠️K>1(실측 1.29)이면 끝단에서 음수 → 클램프 필수(음수면 노이즈 반전).
            #   하한은 0 이 아니라 GRAIN_TONE_FLOOR — 실측이 밝은 끝에서 0 으로 가지 않는다.
            lg = l0 ** np.float32(coeffs.GRAIN_TONE_GAMMA)
            wt = np.maximum(coeffs.GRAIN_TONE_FLOOR, 1.0 + coeffs.GRAIN_TONE
                            * (np.sqrt(4.0 * lg * (1.0 - lg)) - 1.0))
            f += g * (gk * wt[..., None])
            out[y:y1] = np.rint(np.clip(f, 0.0, 1.0) * maxv).astype(dt)

        # 스트립 병렬 — 필드가 좌표 결정론이라 스트립 간 완전 독립이고 쓰기 행도 서로소라
        # 결과가 직렬과 **비트 동일**. numpy 가 대형 배열 연산에서 GIL 을 풀어 스레드로 실효
        # 병렬이 된다(실측 26MP: 사각 셀 20.6→5.8s, 원판 258→64s — 6워커 4.0배).
        # 6워커 이상은 수확 체감 → min(6, 코어-2) 상한.
        # ⚠️ThreadPoolExecutor 를 쓰면 안 된다 — 워커가 **non-daemon** 이고 atexit 훅이 큐를
        #   전부 비운 뒤 join 하므로, 그레인 도중 앱을 닫으면 남은 스트립이 끝날 때까지(26MP
        #   원판 ~64s) 창 없는 프로세스가 살아남는다. daemon 스레드를 직접 띄워 즉시 종료 가능하게.
        workers = max(1, min(6, (os.cpu_count() or 4) - 2))
        ys = list(range(0, h, strip))                      # 스트립 = 26MP 풀 float 사본 회피
        if workers <= 1:
            for y in ys:
                _grain_strip(y)
        else:
            errors = []

            def _worker(k):                                # 라운드로빈 분할(스트립 비용 균일)
                try:
                    for y in ys[k::workers]:
                        _grain_strip(y)
                except BaseException as exc:               # noqa: BLE001 (스레드 예외 전파용)
                    errors.append(exc)

            ts = [threading.Thread(target=_worker, args=(k,), daemon=True)
                  for k in range(workers)]
            started = []
            try:
                for t in ts:
                    t.start()
                    started.append(t)
            finally:
                # ⚠️중간 start() 가 실패해도 **이미 뜬 워커는 반드시 회수**한다 — 안 하면
                #   render_full 이 예외로 빠져나간 뒤에도 고아 워커가 버려진 out 을 계속 쓴다
                #   (ThreadPoolExecutor 의 with-블록이 해주던 shutdown(wait=True) 대체).
                for t in started:
                    t.join()
            if errors:
                raise errors[0]                            # 조용한 누락 방지

    _prog(0.97)   # 그레인 완료 — 남은 건 지오메트리/스탬프/저장(빠름)
    # === 지오메트리(회전/크롭) — 현상 끝난 이미지에 마지막 적용(프리뷰 뷰 변환과 동일) ===
    out = _apply_geometry(out, p)

    # 날짜 스탬프(필름 데이트백) — 크롭/회전까지 끝난 '최종 프레임'의 우하단에 찍는다.
    #   → 위치·크기가 최종(크롭) 사이즈 기준이 됨. (크롭 전 원본 코너 기준이면 크롭 시 어긋남)
    #   비네팅 뒤(LED는 렌즈를 거치지 않음). 프리뷰는 cropClip 위 오버레이로 동일 위치/합성.
    if do_stamp:
        # ⚠️인자를 하나 빠뜨리면 **CPU export 만** 기본 룩으로 찍힌다(프리뷰·GPU export 와
        #   불일치). 실제로 색/글로우/영역을 추가할 때 이 호출을 빠뜨려 걸렸다 — 스탬프
        #   파라미터를 늘리면 여기·main._finish_gpu_export·_update_stamp_layer 세 곳을 함께 볼 것.
        date_stamp.stamp_export(out, stamp_text, rot=stamp_rot,   # dtype 자동, 회전·코너 in-place
                                style=stamp_style, size_frac=stamp_size, margin_frac=stamp_margin,
                                color=stamp_color, glow=stamp_glow, spread=stamp_spread,
                                grain_amt=float(p.get("grainAmt", 0.0)))   # 스탬프 그레인=사진 그레인 연동

    return out


# ---------- 필름 그레인 필드 (셰이더 adjust.frag 12단계의 numpy 판) ----------
# 예전엔 export 가 np.random 격자+zoom 이라 프리뷰와 '성격'만 맞췄지만, 옥타브 회전이
# 들어가면서 격자 방식으로는 재현이 불가능해져 절차적 해시를 그대로 이식했다.
# → 이제 양쪽이 **동일한 연속 노이즈 필드**를 샘플한다(구조 일치). 다만 프리뷰는 프록시,
#   export 는 풀해상도라 '샘플링 밀도'가 달라 픽셀 단위 일치는 여전히 아니다.
# ⚠️ 오프셋/옥타브 배율(0.5)은 adjust.frag 의 GRAIN_OFF1/2 리터럴과 반드시 일치.
# 오프셋이 없으면 세 격자가 원점에서 같은 정수 셀을 공유 → hash12 가 같은 값 → 옥타브가
# 독립이 아니게 되고 1/√(1+r²+r⁴) 정규화(독립 가정)가 어긋난다.
_GRAIN_OFFSETS = ((0.0, 0.0), (37.1, 17.3), (91.7, 63.9))

# 컬러 필름 3층(R/G/B 발색층)의 상대 입자 크기·진폭·오프셋. 청감층(옐로)은 고감도가 필요해
# 은염 결정이 크고 → 염료 구름도 가장 굵다. 적감층(시안)이 가장 곱다(녹감층 RMS 의 20~90%).
# 염료 확산은 별도 블러가 아니라 '유효 입자 크기 증가'로 흡수 — 확산의 물리적 결과가 그것.
# ⚠️ adjust.frag 의 GRAIN_LSIZE/GRAIN_LAMP/GRAIN_LOFF0..2 리터럴과 반드시 일치.
# 서브픽셀 샘플 오프셋(픽셀 단위). 실제 필름에 맞춘 셀 크기가 픽셀보다 작아 점 샘플링하면
# 에일리어싱 → 스캐너가 픽셀 면적을 적분하듯 2x2 로 평균한다. ⚠️ adjust.frag GRAIN_SS 와 일치.
_GRAIN_SS = (-0.25, 0.25)
_GRAIN_LSIZE = (0.85, 1.00, 1.35)
_GRAIN_LAMP = (0.70, 1.00, 1.30)
_GRAIN_LOFF = ((0.0, 0.0), (11.3, 47.9), (73.5, 29.1))


def _grain_color_norm(k):
    """3층 혼합 mix(mono, e, k) 후 **휘도** 그레인 σ 를 k 와 무관하게 유지하는 계수.
    e=층별 필드(진폭 aᵢ), mono=Σeᵢ/|a| 라 mono 와 층은 상관이 있고, 그래서 교차항이 붙는다:
        Var(dot(LUMA,n)) = (1−k)² + k²·Σ(Lᵢaᵢ)² + 2(1−k)k·Σ(Lᵢaᵢ²)/|a|
    aᵢ=1 이면 이전(층 동일) 식으로 환원된다. ⚠️ 셰이더 12단계와 동일 식(양쪽 동시 수정)."""
    a = np.asarray(_GRAIN_LAMP, np.float64)
    a_len = float(np.sqrt((a * a).sum()))
    la = LUMA.astype(np.float64) * a
    return float(((1.0 - k) ** 2 + k * k * float((la * la).sum())
                  + 2.0 * (1.0 - k) * k * float((LUMA * a * a).sum()) / a_len) ** -0.5)


def _grain_hash12(x, y):
    """GLSL hash12(Dave Hoskins) 와 동일. p3=(X,Y,X) 라 z 성분은 항상 x 성분과 같다."""
    X = x * 0.1031; X -= np.floor(X)
    Y = y * 0.1031; Y -= np.floor(Y)
    d = X * (Y + 33.33) + Y * (X + 33.33) + X * (X + 33.33)   # dot(p3, p3.yzx + 33.33)
    a = X + d; b = Y + d
    r = (a + b) * a                                            # (p3.x + p3.y) * p3.z
    return r - np.floor(r)


def _grain_grid(gx, gy, pad):
    """분리형 격자 인덱싱 공용부 — 픽셀 좌표(1-D gx/gy)가 걸치는 셀 격자를 pad 셀 여유를
    두고 만들고, 픽셀→격자 정수 인덱스를 돌려준다. _grain_cell(pad=0)과 _grain_disk(pad=1,
    3x3 이웃)가 공유 — 원점 오프셋/arange 경계/intp 캐스트가 두 벌로 갈라지지 않게."""
    ix, iy = np.floor(gx), np.floor(gy)
    X = np.arange(ix[0] - pad, ix[-1] + pad + 1.0, dtype=np.float32)
    Y = np.arange(iy[0] - pad, iy[-1] + pad + 1.0, dtype=np.float32)
    cx = (ix - X[0]).astype(np.intp)
    cy = (iy - Y[0]).astype(np.intp)
    return X, Y, cx, cy


def _grain_cell(gx, gy):
    """GLSL cellNoise — 셀마다 난수 하나, **보간 없음**. 반환 (rows,w).
    gx=(w,) 열 좌표, gy=(rows,) 행 좌표 — 좌표가 분리형이라 해시를 격자(ny×nx)에서 1회만
    구하고 정수 인덱싱으로 픽셀에 펼친다.
    ⚠️보간(smoothstep/선형)을 넣으면 실측 필름 대비 결이 뭉개진다 — acf lag1(gridN 2900):
    보간없음 0.391 / smoothstep 0.504 / 선형 0.553, 실측 필름 0.234. 서브픽셀 평균에서 σ 도
    깎여 곱기와 진하기를 맞바꾸게 된다. 셰이더 cellNoise 주석 참조."""
    X, Y, cx, cy = _grain_grid(gx, gy, 0)
    return _grain_hash12(X[None, :], Y[:, None])[np.ix_(cy, cx)]


_GRAIN_DISK_R = 0.55
"""원판 원시체의 반지름(**셀 단위**). 셀 원시체와 acf lag1(곱기)이 맞도록 고른 값 —
1.44 px/셀에서 셀 0.404 vs 원판 0.383, 3.21 px/셀에서 0.720 vs 0.727. 즉 모양을 바꿔도
입자 굵기는 그대로고 분포만 달라진다. ⚠️셰이더 GRAIN_DISK_R 과 일치."""


def _grain_disk(gx, gy):
    """원판 원시체 — 셀마다 해시로 **위치와 진폭**을 뽑아 반지름 _GRAIN_DISK_R 원판을 하나
    뿌리고 3x3 이웃을 합산. 반환 (rows,w), 평균 0 · **분산 1/12**(= _grain_cell 과 동일).

    정규화가 해석적이다: 셀당 점 1개(밀도 1)라 한 점을 덮는 원판 수의 기대값이 πR², 진폭이
    U[-0.5,0.5](분산 1/12)이고 독립이라 Var = πR²/12. 1/(R√π) 를 곱하면 정확히 1/12
    (수치 검증 비 0.999~1.002). → **σ 가 보존되고 왜도 보정 v2 의 전제도 그대로 유효**.
    ⚠️R<1 이면 3x3 밖 셀의 점은 거리가 1 을 넘어 못 덮는다 — 이웃 범위를 넓힐 필요 없음.
    ⚠️해시는 **셀 격자에서 1회만** 구한다 — 좌표가 분리형이고 9개 이웃이 같은 격자를 공유하므로
      픽셀마다 27번 해시하면 크게 손해다(_grain_cell 과 같은 이유의 최적화)."""
    X, Y, cx, cy = _grain_grid(gx, gy, 1)                        # 3x3 이웃까지 덮게 한 칸 확장
    px = _grain_hash12(X[None, :], Y[:, None])                   # 셀 내 x 위치
    py = _grain_hash12(X[None, :] + 17.3, Y[:, None] + 29.7)     # 셀 내 y 위치
    am = _grain_hash12(X[None, :] + 53.1, Y[:, None] + 71.9) - np.float32(0.5)
    out = np.zeros((gy.size, gx.size), np.float32)
    r2 = np.float32(_GRAIN_DISK_R * _GRAIN_DISK_R)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            jx, jy = cx + dx, cy + dy
            sel = np.ix_(jy, jx)
            ddx = gx[None, :] - (X[jx][None, :] + px[sel])
            ddy = gy[:, None] - (Y[jy][:, None] + py[sel])
            out += am[sel] * ((ddx * ddx + ddy * ddy) < r2)
    return out * np.float32(1.0 / (_GRAIN_DISK_R * math.sqrt(math.pi)))


def _grain_field(y0, y1, h, w, grid_n, aspect, clump, color, skew_c, shape=0.0):
    """(y1-y0, w, 3) float32 그레인 필드(채널별, 평균 0). 셰이더 12단계와 동일 수식.
    실제 필름에 맞춰 셀이 픽셀보다 작아졌으므로 **2x2 서브픽셀 평균**(스캐너의 면적 적분에
    대응)으로 샘플한다 — 점 샘플링하면 접힌다(⚠️_GRAIN_SS 는 셰이더와 일치)."""
    out = None
    for sy in _GRAIN_SS:
        for sx in _GRAIN_SS:
            o = _grain_field_1(y0, y1, h, w, grid_n, aspect, clump, color, skew_c,
                               sx, sy, shape)
            out = o if out is None else out + o
    return (out / np.float32(len(_GRAIN_SS) ** 2)).astype(np.float32)


def _grain_field_1(y0, y1, h, w, grid_n, aspect, clump, color, skew_c, sx, sy, shape=0.0):
    """서브픽셀 오프셋 (sx,sy) 픽셀 단위에서의 단일 샘플. uv=(i+0.5+sx)/N = qt_TexCoord0 + sx·texel."""
    u = (np.arange(w, dtype=np.float32) + np.float32(0.5 + sx)) / w
    v = (np.arange(y0, y1, dtype=np.float32) + np.float32(0.5 + sy)) / h
    gx0 = u * np.float32(grid_n)                # 정사각 셀 좌표(셰이더 g0 와 동일), 1-D
    gy0 = v * np.float32(grid_n / aspect)
    r = float(clump)
    oct_norm = np.float32((1.0 + r * r + r ** 4) ** -0.5)
    layers = []
    for (lsize, lamp, (lox, loy)) in zip(_GRAIN_LSIZE, _GRAIN_LAMP, _GRAIN_LOFF):
        gx = gx0 / np.float32(lsize) + np.float32(lox)   # 굵은 입자 = 셀 좌표 축소
        gy = gy0 / np.float32(lsize) + np.float32(loy)
        acc, amp = None, np.float32(1.0)
        for i, (ox, oy) in enumerate(_GRAIN_OFFSETS):
            if i and amp < 1e-4:
                break                            # clump=0 이면 단일 옥타브 — 나머지 계산 생략
            s = np.float32(0.5 ** i)             # f, f/2, f/4 (거친 쪽 — 미세 쪽은 에일리어싱)
            # 원시체 선택 — 둘 다 평균 0 · 분산 1/12 이라 이후 체인이 완전히 동일하다.
            o = ((_grain_disk(gx * s + np.float32(ox), gy * s + np.float32(oy)) if shape > 0.5
                  else _grain_cell(gx * s + np.float32(ox),
                                   gy * s + np.float32(oy)) - np.float32(0.5)) * amp)
            acc = o if acc is None else acc + o
            amp = amp * np.float32(clump)
        layers.append(acc * (oct_norm * np.float32(lamp)))
    e = np.stack(layers, axis=-1)
    # 3층 혼합 — mono(층 합 = 휘도 요동)와 층 자체를 k 로 섞고, 휘도 σ 를 보존하도록 정규화.
    a_len = np.float32(np.sqrt(sum(x * x for x in _GRAIN_LAMP)))
    k = np.float32(color)
    mono = e.sum(axis=-1, keepdims=True) / a_len
    nrm = np.float32(_grain_color_norm(color))
    n = (mono + (e - mono) * k) * nrm                                    # = mix(mono, e, k)
    # 왜도(3차) — **서브픽셀 평균 전, 샘플 단위**. 이 지점의 채널별 분산은 해석적으로 알 수
    # 있어(셀 값이 정확히 균일분포 → 분산 1/12) 평균이 **정확히 0**. 공칭 σ 를 쓰면 어긋난
    # 만큼 그레인이 밝기를 옮긴다. ⚠️셰이더 grainField 와 동일 식.
    #   Var_i = [(1−k)² + k²aᵢ² + 2(1−k)k·aᵢ²/|a|] · nrm²/12
    aa = np.asarray(_GRAIN_LAMP, np.float32) ** 2
    v2 = (((1.0 - k) ** 2 + k * k * aa + 2.0 * (1.0 - k) * k * aa / a_len)
          * (nrm * nrm / np.float32(12.0)))
    return (n + skew_c * (n * n - v2)).astype(np.float32)


JPEG_QUALITY = 95
"""jpg 저장 품질. ⚠️Qt 기본값은 **75** 인데 그레인처럼 화면 전체가 고주파인 이미지에서는
8x8 DCT 블록 경계가 격자로 드러난다 — 측정(열 경계별 |ΔI| 비, 1.00=격자 없음):
**그레인 최대 설정 export** 에서 무손실 1.00 / q75 1.34 / q95 1.02(CLAUDE.md Export 절과 동일 기준),
완만한 그레인 사진에서는 q75 1.20 / q85 1.03 / q92 0.98. 최악 케이스 기준 q92 위로 여유를 둬 95.
⚠️PNG 에는 주면 안 된다 — Qt 에서 PNG 의 quality 는 '압축 레벨'이라 의미가 반대고,
95 를 주면 거의 무압축이 되어 파일이 몇 배로 커진다. 그래서 확장자로 게이팅한다."""


JPEG_EXTS = ("jpg", "jpeg", "jfif")   # ⚠️Qt 가 JPEG 핸들러로 매핑하는 확장자 전부
                                     #   (jfif 누락 시 그 경로만 Qt 기본 품질 75 로 저장된다)


def save_image(arr, path) -> bool:
    """(H,W,3) RGB 저장. dtype 으로 비트깊이 결정:
    - uint8  -> RGB888 (jpg/png/tif 8bit)
    - uint16 -> RGBX64 (png/tif 16bit, 알파 없음). jpg 는 8bit 만 가능(Qt 가 자동 강등)."""
    arr = np.ascontiguousarray(arr)
    h, w, _ = arr.shape
    if arr.dtype == np.uint16:
        rgbx = np.empty((h, w, 4), np.uint16)
        rgbx[..., :3] = arr
        rgbx[..., 3] = 65535                       # X 채널(미사용) — RGBX64 는 알파 무시
        rgbx = np.ascontiguousarray(rgbx)
        img = QImage(rgbx.data, w, h, 8 * w, QImage.Format.Format_RGBX64).copy()
    else:
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    # ⚠️포맷을 **명시**해서 넘긴다 — 임시 이름이 `<path>.part` 라 Qt 가 확장자로 추론하면
    #   모르는 형식이라 무조건 실패한다(확장자가 없는 게 아니라 Qt 가 `.part` 를 모르는 것).
    #   확장자가 아예 없으면 fmt=None 으로 넘겨 **예전처럼 실패**시킨다 — 임의 형식으로
    #   저장해버리면 '저장됨' 이라 알리고도 열리지 않는 파일이 남는다.
    fmt = ext.upper() or None
    quality = JPEG_QUALITY if ext in JPEG_EXTS else -1     # -1 = 포맷 기본값
    # ⚠️인코딩은 **메모리에서** 끝내고 파일은 한 번에 쓴다 — 대상 파일에 바로 인코딩하면
    #   26MP 에서 1.4~7.4s 동안 파일이 열려 있고, 그 사이 앱이 종료되면(export 중 창 닫기)
    #   같은 이름의 기존 파일이 잘린 채 남는다. 실측 쓰기 구간은 0.08~0.19s 로 19~40배 짧다.
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, fmt, quality):
        return False                                       # 인코딩 실패 — 디스크는 손대지 않음
    data = buf.data()
    buf.close()
    # 임시 파일 → os.replace 로 원자적 교체(같은 디렉터리라 항상 동일 볼륨).
    # ⚠️대상 파일을 다른 프로그램이 열고 있으면 Windows 에서 replace 가 막힌다
    #   (실측 PermissionError WinError 5 — 뷰어로 결과를 열어둔 채 재export 하는 흔한 흐름).
    #   제자리 쓰기는 그 상황에서도 되므로 폴백한다(= v1.8.1 동작).
    tmp = path + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        with open(path, "wb") as f:                        # ⚠️여기서 실패하면 예외를 그대로
            f.write(data)                                  #   올린다 — 호출처가 'Failed: <원인>' 표시
    return True


def compose_wallpaper(panels, canvas_w, canvas_h, gap, offsets):
    """3분할 트립틱 배경화면 합성. panels: [uint8 (H,W,3)]*3(좌/중/우),
    offsets: [-1..1]*3 가로 크롭 오프셋(-1=왼쪽 끝, 0=중앙, +1=오른쪽 끝).
    각 패널을 cover-fit(스케일 후 크롭)하므로 입력 해상도/비율과 무관하게 정확.
    갭·배경은 검정 고정. -> (canvas_h, canvas_w, 3) uint8"""
    avail = canvas_w - 2 * gap
    base, rem = avail // 3, avail % 3
    widths = [base + (1 if i < rem else 0) for i in range(3)]   # 나머지 px는 왼쪽부터
    canvas = np.zeros((canvas_h, canvas_w, 3), np.uint8)        # 검정 = 갭
    x = 0
    for arr, pw, off in zip(panels, widths, offsets):
        h, w = arr.shape[:2]
        s = max(canvas_h / h, pw / w)                           # cover-fit
        nh, nw = max(canvas_h, round(h * s)), max(pw, round(w * s))
        if (nh, nw) != (h, w):
            f32 = arr.astype(np.float32)
            if s < 1.0:
                sig = 0.5 * (1.0 / s - 1.0)                     # _downscale_to_edge 와 동일 AA
                if sig > 0.4:
                    f32 = gaussian_filter(f32, (sig, sig, 0.0))
            f32 = zoom(f32, (nh / h, nw / w, 1.0), order=1)
            arr = np.clip(f32 + 0.5, 0, 255).astype(np.uint8)
        x0 = min(max(int(round((nw - pw) * (off + 1.0) * 0.5)), 0), nw - pw)
        y0 = (nh - canvas_h) // 2
        canvas[:, x:x + pw] = arr[y0:y0 + canvas_h, x0:x0 + pw]
        x += pw + gap
    return canvas


# ---------------------------------------------------------------- 잡지 레이아웃
# 에디토리얼 스프레드: 한쪽은 히어로 사진 풀블리드, 반대쪽은 종이 면에 타이포그래피
# (키커/헤드라인/리드문/인덱스) + 작은 사진 2장. 텍스트 렌더는 앱의 기존 방식과 같은
# Qt QPainter(date_stamp 와 동일) — QImage 대상 페인팅이라 워커 스레드에서 안전.
MAG_PAPER = (246, 245, 241)
MAG_INK = (22, 22, 26)
MAG_GRAY = (118, 118, 124)
MAG_HAIR = (205, 203, 197)
MAG_RUST = (156, 59, 46)
# (헤드라인 후보, 본문 후보, 강조색, 헤드라인 자간, 대문자화, 줄높이 계수)
MAG_FACES = {
    "serif": (["Constantia", "Cambria", "Georgia", "Times New Roman"],
              ["Constantia", "Cambria", "Georgia", "Times New Roman"],
              MAG_INK, 0.0, False, 1.12),
    "sans": (["Franklin Gothic Medium Cond", "Arial Narrow", "Bahnschrift SemiCondensed",
              "Segoe UI"],
             ["Arial Narrow", "Bahnschrift Condensed", "Segoe UI"],
             MAG_RUST, 0.015, True, 1.08),
    # 한글: 대문자화·자간 확대는 한글에 의미가 없어 끄고, 줄높이만 조금 넉넉하게.
    # ⚠️뒤쪽 두 개는 macOS 폰트다 — 앞의 Windows 후보가 macOS 에 **하나도 없어서**
    #   _pick_family 가 마지막 후보(없는 폰트)를 그대로 돌려주고, Qt 가 임의로 대체한
    #   서체로 한글이 나가고 있었다(실측). Windows 우선순위는 그대로 두고 뒤에만 덧붙인다.
    "serif_ko": (["Noto Serif KR", "Batang", "Gungsuh", "Nanum Myeongjo", "AppleMyungjo"],
                 ["Noto Serif KR", "Batang", "Gungsuh", "Nanum Myeongjo", "AppleMyungjo"],
                 MAG_INK, 0.0, False, 1.18),
    "sans_ko": (["Noto Sans KR", "Malgun Gothic", "Dotum", "Nanum Gothic", "Apple SD Gothic Neo"],
                ["Noto Sans KR", "Malgun Gothic", "Dotum", "Nanum Gothic", "Apple SD Gothic Neo"],
                MAG_RUST, 0.0, False, 1.14),
}


def _pick_family(candidates):
    from PySide6.QtGui import QFontDatabase
    have = set(QFontDatabase.families())
    for c in candidates:
        if c in have:
            return c
    return candidates[-1]


def _qfont(family, px, bold=False, italic=False):
    from PySide6.QtGui import QFont
    f = QFont(family)
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bold)
    f.setItalic(italic)
    return f


def _np_to_qimage(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def _draw_text(p, x, y_top, s, font, color, tracking_px=0.0, upper=False):
    """좌상단 기준 텍스트(자간 지원). 반환=그린 폭."""
    from PySide6.QtGui import QColor, QFontMetricsF
    if upper:
        s = s.upper()
    p.setFont(font)
    p.setPen(QColor(*color))
    fm = QFontMetricsF(font)
    base = y_top + fm.ascent()
    if tracking_px <= 0:
        p.drawText(int(round(x)), int(round(base)), s)
        return fm.horizontalAdvance(s)
    cx = float(x)
    for ch in s:
        p.drawText(int(round(cx)), int(round(base)), ch)
        cx += fm.horizontalAdvance(ch) + tracking_px
    return cx - x


def _wrap(font, s, max_w):
    from PySide6.QtGui import QFontMetricsF
    fm = QFontMetricsF(font)
    lines, cur = [], ""
    for word in s.split():
        t = (cur + " " + word).strip()
        if fm.horizontalAdvance(t) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _text_w(font, s, tracking_px=0.0, upper=False):
    from PySide6.QtGui import QFontMetricsF
    if upper:
        s = s.upper()
    fm = QFontMetricsF(font)
    return fm.horizontalAdvance(s) + tracking_px * max(0, len(s) - 1)


def compose_magazine(panels, canvas_w, canvas_h, opts):
    """에디토리얼 스프레드 합성 -> QImage.

    panels: [좌, 중(메인 사진), 우] uint8 (H,W,3). 메인은 cover 크롭, 작은 2장은 크롭 0%.
    opts: mainSide('left'|'right'), typeface('serif'|'sans'|'serif_ko'|'sans_ko'),
          kicker/headline/deck/place/date, titles[3], shots[3](EXIF 요약), indexLabel,
          mainFrac(0.5~0.75; 메인 사진이 차지하는 폭 비율),
          safeAspects(예: [16/9, 16/10]) — 이 비율들에서 모두 살아남는 영역에만 글자를 둔다.
    사진은 캔버스를 꽉 채우고(풀블리드) **타이포그래피만 안전영역 안**에 배치하므로 한 장으로
    16:9·16:10 양쪽에서 잘림 없이 읽힌다. 좌표는 안전영역 높이 2160 기준으로 스케일."""
    from PySide6.QtGui import QColor, QPainter

    # 안전영역 = 지정한 화면 비율들에서 '채우기'로 보이는 영역의 교집합(중앙 정렬).
    # 배경화면을 채우기로 깔면 이미지보다 납작한 화면은 좌우를, 홀쭉한 화면은 위아래를
    # 잘라낸다. 각 비율에서 보이는 부분은 '이미지 안에 들어가는 최대 중앙 사각형'이라
    # 폭·높이를 각각 최솟값으로 모으면 모든 비율에서 안전한 사각형이 된다.
    safe_w, safe_h = float(canvas_w), float(canvas_h)
    for a in (opts.get("safeAspects") or []):
        try:
            a = float(a)
        except (TypeError, ValueError):
            continue
        if a <= 0:
            continue
        vw = min(canvas_w, canvas_h * a)
        safe_w = min(safe_w, vw)
        safe_h = min(safe_h, vw / a)
    safe_w, safe_h = max(1.0, safe_w), max(1.0, safe_h)
    sx0 = int(round((canvas_w - safe_w) / 2.0))
    sy0 = int(round((canvas_h - safe_h) / 2.0))
    sx1, sy1 = sx0 + int(round(safe_w)), sy0 + int(round(safe_h))

    s = safe_h / 2160.0                                  # 글자 크기는 안전영역 기준

    def S(v):
        return int(round(v * s))

    face = MAG_FACES.get(str(opts.get("typeface", "serif")), MAG_FACES["serif"])
    head_fams, body_fams, accent, track_frac, upper, lh = face
    fam_h = _pick_family(head_fams)
    fam_b = _pick_family(body_fams)
    # 구 키(heroSide/heroFrac/heroCaption)는 이전에 저장된 프리셋 호환용 폴백
    main_left = str(opts.get("mainSide", opts.get("heroSide", "right"))) == "left"
    main_frac = float(opts.get("mainFrac", opts.get("heroFrac", 0.61)))
    main_w = max(1, int(round(canvas_w * main_frac)))     # 메인 사진은 캔버스 기준 풀블리드

    # 프레임 번호는 **화면에서 보이는 좌→우 순서**로 매긴다(슬롯 순서로 매기면 메인이 가운데
    # 슬롯인데 좌/우 끝에 놓여 01·03 이 뒤섞여 헷갈린다).
    # slot_order = 왼쪽부터의 슬롯 인덱스, no[slot] = 그 슬롯의 번호(1..3).
    slot_order = [1, 0, 2] if main_left else [0, 2, 1]
    no = {sl: i + 1 for i, sl in enumerate(slot_order)}

    canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_RGB888)
    canvas.fill(QColor(*MAG_PAPER))
    p = QPainter(canvas)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # ── 메인 사진: cover 크롭 후 풀블리드. 오프셋 슬라이더(-1..+1)는 **실제로 잘리는 축**에
        # 적용한다 — 세로 사진이 가로형 메인 칸에 들어가면 폭은 딱 맞고 위아래가 잘리므로
        # 가로 오프셋은 움직일 여지가 0이다(슬라이더가 안 먹는 것처럼 보였던 원인).
        main_img = _np_to_qimage(panels[1]).scaled(
            main_w, canvas_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        try:
            off = float(list(opts.get("offsets", [0.0, 0.0, 0.0]))[1])
        except (IndexError, TypeError, ValueError):
            off = 0.0
        slack_x = max(0, main_img.width() - main_w)
        slack_y = max(0, main_img.height() - canvas_h)
        t = (off + 1.0) * 0.5                       # -1..+1 → 0..1 (0=위/왼쪽 끝)
        if slack_x >= slack_y:
            hx, hy = int(round(slack_x * t)), slack_y // 2
        else:
            hx, hy = slack_x // 2, int(round(slack_y * t))
        p.drawImage(0 if main_left else canvas_w - main_w, 0,
                    main_img.copy(hx, hy, main_w, canvas_h))

        # ── 텍스트 칼럼 기준선(안전영역 안쪽에서 잡는다)
        m_out = S(170)                                    # 바깥 여백
        m_in = S(190)                                     # 메인 사진 쪽 여백
        col_l = max(sx0, main_w if main_left else 0)
        col_r = min(sx1, canvas_w if main_left else canvas_w - main_w)
        cx = col_l + m_out
        cw = max(S(200), col_r - cx - m_in)
        tr = track_frac * S(130)                          # 헤드라인 자간(px)

        # 키커
        f_kick = _qfont(fam_b, S(30), bold=True)
        y = sy0 + S(170)
        _draw_text(p, cx, y, str(opts.get("kicker", "")), f_kick, accent, S(6), True)

        # 헤드라인
        f_head = _qfont(fam_h, S(130), bold=True)
        y = sy0 + S(258)
        for ln in _wrap(f_head, str(opts.get("headline", "")), cw):
            _draw_text(p, cx, y, ln, f_head, MAG_INK, tr, upper)
            y += S(130) * lh
        y = int(y)
        p.fillRect(cx, y + S(34), S(130), max(1, S(3)), QColor(*MAG_INK))

        # 리드문
        f_deck = _qfont(fam_b, S(36))
        y += S(96)
        for ln in _wrap(f_deck, str(opts.get("deck", "")), cw):
            _draw_text(p, cx, y, ln, f_deck, MAG_GRAY)
            y += S(48)

        # 인덱스(번호 · 제목 · 촬영정보) — 빈 공간을 채우는 지면 장치
        titles = list(opts.get("titles", ["", "", ""]))[:3]
        shots = list(opts.get("shots", ["", "", ""]))[:3]
        if any(t for t in titles) or any(t for t in shots):
            y += S(74)
            _draw_text(p, cx, y, str(opts.get("indexLabel", "In this set")),
                       _qfont(fam_b, S(26), bold=True), MAG_GRAY, S(5), True)
            y += S(52)
            f_num = _qfont(fam_h, S(40), bold=True)
            f_tit = _qfont(fam_b, S(34))
            f_shot = _qfont(fam_b, S(25))
            for pos, sl in enumerate(slot_order):      # 좌→우 순서로 나열
                p.fillRect(cx, y, cw, 1, QColor(*MAG_HAIR))
                _draw_text(p, cx, y + S(22), f"0{pos + 1}", f_num, accent)
                _draw_text(p, cx + S(90), y + S(24), titles[sl] if sl < len(titles) else "",
                           f_tit, MAG_INK)
                sh = shots[sl] if sl < len(shots) else ""
                if sh:
                    _draw_text(p, cx + cw - _text_w(f_shot, sh), y + S(34), sh,
                               f_shot, MAG_GRAY)
                y += S(92)
            p.fillRect(cx, y, cw, 1, QColor(*MAG_HAIR))

        # ── 작은 사진 2장(크롭 0%) + 캡션.
        # 헤드라인 줄 수(서체·문장 길이)에 따라 위 블록 높이가 변하므로 남은 높이에
        # 맞춰 크기를 정한다(고정 크기면 겹침).
        cap_band = S(66)
        gap = S(40)
        avail_h = (sy1 - S(170) - cap_band) - (y + S(60))
        smalls = [_np_to_qimage(panels[0]), _np_to_qimage(panels[2])]
        if avail_h > S(120):
            ws = [avail_h * im.width() / im.height() for im in smalls]
            hh = avail_h
            if sum(ws) + gap > cw:                       # 폭이 넘치면 폭 기준 축소
                hh = avail_h * (cw - gap) / sum(ws)
            sy = sy1 - S(170) - cap_band - int(round(hh))
            sx = cx
            for i, im in enumerate(smalls):
                sw = max(1, int(round(hh * im.width() / im.height())))
                sc = im.scaled(sw, int(round(hh)), Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                p.drawImage(sx, sy, sc)
                lab = f"Frame 0{no[0 if i == 0 else 2]}"
                _draw_text(p, sx, sy + sc.height() + S(18), lab,
                           _qfont(fam_b, S(23), bold=True), accent, S(3), True)
                sx += sc.width() + gap

        # ── 폴리오(지면 하단 러닝풋): 장소 · 날짜.
        # 장소/날짜는 사진별 정보가 아니라 지면 전체 정보라 프레임 라벨 옆이 아니라 여기에
        # 둔다(라벨 옆에 두면 '그 사진의 장소/날짜'처럼 읽혔다).
        place = str(opts.get("place", "")).strip()
        date = str(opts.get("date", "")).strip()
        if place or date:
            f_fol = _qfont(fam_b, S(26))
            fy = sy1 - S(104)
            p.fillRect(cx, sy1 - S(126), cw, 1, QColor(*MAG_HAIR))
            if place:
                _draw_text(p, cx, fy, place, f_fol, MAG_GRAY, S(4), upper)
            if date:
                _draw_text(p, cx + cw - _text_w(f_fol, date, S(4), upper), fy, date,
                           f_fol, MAG_GRAY, S(4), upper)

        # ── 메인 사진 위 캡션(흰 글씨, 사진 하단 바깥쪽 모서리). 번호는 위 좌→우 규칙과
        # 동일하게 여기서 만든다 — 호출측에서 조립하면 번호 규칙이 두 곳으로 갈라진다.
        cap = str(opts.get("mainCaption", opts.get("heroCaption", ""))).strip()
        if not cap:
            bits = [f"0{no[1]}"]
            if len(titles) > 1 and titles[1]:
                bits.append(str(titles[1]))
            if len(shots) > 1 and shots[1]:
                bits.append(str(shots[1]))
            cap = "   ·   ".join(bits) if len(bits) > 1 else ""
        if cap:
            f_cap = _qfont(fam_b, S(27))
            cwid = _text_w(f_cap, cap)
            edge = min(main_w, sx1) if main_left else min(canvas_w, sx1)
            _draw_text(p, edge - S(120) - cwid, sy1 - S(108), cap, f_cap, (255, 255, 255))
    finally:
        p.end()
    return canvas
