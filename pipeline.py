"""풀해상도 export 파이프라인 (numpy).

화면 프리뷰(GPU 셰이더, 프록시)와 동일한 단계/수식을 풀해상도에 재현한다:

  WB(카메라네이티브 선형화→상대게인→cam->sRGB 매트릭스→sRGB) -> 노출 -> 톤영역
       -> 텍스처/클래리티/디헤이즈 -> 3D LUT -> 대비 -> 톤커브 -> 비네팅 -> 그레인

텍스처/클래리티는 공간(이웃) 연산이라 셰이더의 '프록시 텍셀' 반경을 풀해상도
비율(full/proxy)로 스케일해 시각적으로 맞춘다. 공간 단계는 전체 배열에서,
메모리 큰 3D LUT 단계는 가로 스트립으로 처리한다.
"""

import math

import numpy as np
import rawpy
from PySide6.QtGui import QImage
from scipy.ndimage import affine_transform, gaussian_filter, map_coordinates, zoom

import coeffs
import date_stamp
import lens
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
    _prog(0.30)   # 디코드 + 다운스케일 + 렌즈 보정 완료(가장 큰 단일 비용)

    h, w, _ = rgb16.shape
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
    for _i, lp in enumerate(_lp[:3]):
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
    # 카메라네이티브 감마 -> 선형화 -> 자동노출(중앙값) -> WB(카메라공간) -> cam->sRGB 매트릭스
    # -> scene-linear sRGB -> 유저노출(scene-linear) -> filmic(단일 톤커브) -> display sRGB.
    nat = wb.srgb_to_linear(rgb16.astype(np.float32) / 65535.0)
    nat *= wb.auto_exposure_gain(target_median, cam, ref, as_shot, nat)  # 카메라네이티브(자동노출 후)
    M = cam_to_srgb_matrix(cam).astype(np.float32)
    # 중성 display 베이스(as-shot WB, 유저노출/desat 없음) — 셰이더 dispSrc/claBlur 와 동일.
    #   hi/sh 톤영역 마스크는 이 '장면 구조' 휘도로 계산해야 프리뷰=Export(노출 무관 마스크).
    neutral_disp = wb.filmic((nat * wb.rel_gain(cam, ref, as_shot, as_shot_tint).astype(np.float32))
                             @ M.T).astype(np.float32)
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
    del rgb16, nat, linsrgb   # 이후 미사용 — 26MP 공간단계 피크에서 조기 해제(수백 MB)
    # 하이라이트 디새추레이션: near-clip 센서클립 색끼(예: 불꽃 코어 청록) 제거 → 중성(흰색).
    # ⚠️쿨(청/녹 우세) 하이라이트만 중성화한다 — 밝은 빨강/주황 광원(예: 네온·간판)은
    # 보존해야 하므로 max(G,B)-R 로 게이트(따뜻한 색은 음수→게이트 0). filmic 뒤 display 공간.
    _mx = disp.max(axis=2, keepdims=True)
    _cool = np.maximum(disp[..., 1:2], disp[..., 2:3]) - disp[..., 0:1]
    disp = disp + (_mx - disp) * (_smoothstep(0.95, 1.0, _mx) * _smoothstep(0.05, 0.35, _cool))

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

    # 필름 그레인 필드(흑백 단색, 전체 H,W 1회 생성 -> 스트립 시드 이음매 방지).
    # 셰이더 value-noise 와 '성격(셀 크기/강도)' 일치: gridN=mix(1500,500,size),
    # 정사각 입자(gridN/aspect)로 거친 그리드를 bilinear 업샘플. 패턴 픽셀일치는 기대 안 함.
    if grain_amt > 0.0:
        gridN = int(round(1500.0 + (500.0 - 1500.0) * grain_size))  # 셰이더 mix 와 동일
        gx, gy = gridN, max(1, int(round(gridN * h / w)))           # gridN / aspect(W/H)
        rng = np.random.default_rng(12345)                          # 재export 결정적
        grid = (rng.random((gy + 1, gx + 1), dtype=np.float32) - 0.5)
        grain2d = zoom(grid, (h / (gy + 1), w / (gx + 1)), order=1)
        grain2d = grain2d[:h, :w].astype(np.float32)
    else:
        grain2d = None

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
    if grain2d is not None:
        f = out.astype(np.float32) / maxv
        f += grain2d[..., None] * grain_amt * coeffs.GRAIN
        out = np.rint(np.clip(f, 0.0, 1.0) * maxv).astype(dt)

    _prog(0.97)   # 그레인 완료 — 남은 건 지오메트리/스탬프/저장(빠름)
    # === 지오메트리(회전/크롭) — 현상 끝난 이미지에 마지막 적용(프리뷰 뷰 변환과 동일) ===
    out = _apply_geometry(out, p)

    # 날짜 스탬프(필름 데이트백) — 크롭/회전까지 끝난 '최종 프레임'의 우하단에 찍는다.
    #   → 위치·크기가 최종(크롭) 사이즈 기준이 됨. (크롭 전 원본 코너 기준이면 크롭 시 어긋남)
    #   비네팅 뒤(LED는 렌즈를 거치지 않음). 프리뷰는 cropClip 위 오버레이로 동일 위치/합성.
    if do_stamp:
        date_stamp.stamp_export(out, stamp_text, rot=stamp_rot,   # dtype 자동, 회전·코너 in-place
                                style=stamp_style, size_frac=stamp_size, margin_frac=stamp_margin,
                                grain_amt=float(p.get("grainAmt", 0.0)))   # 스탬프 그레인=사진 그레인 연동

    return out


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
    return bool(img.save(path))
