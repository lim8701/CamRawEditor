"""절대 색온도(Kelvin) -> 카메라 RAW 화이트밸런스 배수(user_wb).

rawpy 의 카메라 색매트릭스(rgb_xyz_matrix)와 카메라가 보정해 둔
daylight_whitebalance 에 앵커링한다. TREF(데이라이트 기준 색온도)에서
daylight_whitebalance 가 그대로 나오고, 그로부터 Planckian locus 비율로
스케일해 임의의 Kelvin 을 만든다.
"""

import numpy as np

TREF = 5500  # daylight_whitebalance 가 중립이 되는 기준 색온도(K)

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)  # Rec.601 휘도(자동노출 통계용)


def planckian_xy(T: float):
    """색온도 T(K) -> CIE xy 색도 (Planckian locus 근사)."""
    T = float(T)
    if T < 4000:
        x = (-0.2661239e9 / T**3 - 0.2343589e6 / T**2
             + 0.8776956e3 / T + 0.179910)
    else:
        x = (-3.0258469e9 / T**3 + 2.1070379e6 / T**2
             + 0.2226347e3 / T + 0.240390)
    if T < 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif T < 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def _planck_cam(cam_xyz: np.ndarray, T: float) -> np.ndarray:
    """색온도 T 백색의 카메라 native RGB 응답."""
    x, y = planckian_xy(T)
    XYZ = np.array([x / y, 1.0, (1.0 - x - y) / y])
    return np.clip(cam_xyz @ XYZ, 1e-6, None)


def compute_user_wb(cam_xyz, daylight_ref, kelvin, tint: float = 0.0):
    """Kelvin(+tint) -> rawpy user_wb 배수 [R, G, B, G2].

    tint: + 마젠타(녹↓) / - 그린(녹↑)
    """
    cam_xyz = np.asarray(cam_xyz, float)
    gain = _planck_cam(cam_xyz, TREF) / _planck_cam(cam_xyz, kelvin)
    m = np.asarray(daylight_ref, float) * gain
    m = m / m[1]                  # green 정규화
    m[1] *= (1.0 - 0.3 * tint)    # tint 적용
    return [float(m[0]), float(m[1]), float(m[2]), float(m[1])]


# 선형 sRGB -> XYZ (D65) 표준 매트릭스 (dcraw xyz_rgb 와 동일)
_XYZ_RGB_D65 = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])


def cam_to_srgb_matrix(cam_xyz):
    """카메라 네이티브 RGB -> 선형 sRGB 3x3 매트릭스 (dcraw/LibRaw 방식).

    cam_xyz 는 XYZ->cameraRGB (wb.py 전반에서 `cam_xyz @ XYZ` 로 사용).
    dcraw `cam_xyz_coeff`: cam_rgb = cam_xyz @ (sRGB->XYZ) [=sRGB->cam], 각 행을
    행합=1 로 정규화(=pre_mul, 화이트 D65->중성), 그 역행렬이 cam->sRGB(linear).
    rawpy output_color=sRGB 의 매트릭스와 일치(잔차 ~1/255 는 rawpy 독자 비선형
    색렌더이며 매트릭스로 재현 불가 — 표준 colorimetry 를 따른다).
    """
    cam_xyz = np.asarray(cam_xyz, float).reshape(3, 3)
    cam_rgb = cam_xyz @ _XYZ_RGB_D65                       # sRGB -> cam
    rowsum = cam_rgb.sum(axis=1, keepdims=True)            # 행합(=pre_mul 정규화 분모)
    # 일부 DNG(폰/드론 등)는 rgb_xyz_matrix 가 비어(0) 있어 행합=0 → NaN → 렌더가 검정이 된다.
    # 카메라 컬러 매트릭스를 못 얻으면 '카메라공간=sRGB'(항등)로 폴백해 최소한 정상 밝기로 현상.
    if not np.all(np.isfinite(rowsum)) or np.any(np.abs(rowsum) < 1e-8):
        return np.eye(3)
    cam_rgb = cam_rgb / rowsum                             # 행합=1 (pre_mul)
    try:
        inv = np.linalg.inv(cam_rgb)                       # cam -> sRGB(linear)
    except np.linalg.LinAlgError:
        return np.eye(3)
    return inv if np.all(np.isfinite(inv)) else np.eye(3)


def cam_xyz_from_raw(raw) -> np.ndarray:
    """rawpy RAW -> cam_xyz(XYZ->카메라RGB, 3x3). rgb_xyz_matrix 가 비었을 때 복원한다.

    LibRaw `rgb_xyz_matrix`(=color.cam_xyz)는 **adobe_coeff 테이블에 등재된 기종만** 채워진다.
    테이블에 없는 신기종/폰 DNG(실측: Lightroom mobile 의 iPhone 17 = "iPhone18,3 back camera")는
    0 행렬이 되고, 그러면
      · compute_user_wb 의 planck 응답이 전부 1e-6 로 클립 → 모든 Kelvin 의 배수가 동일
        → **Temp 슬라이더가 완전 무동작**(tint 만 먹는다),
      · cam_to_srgb_matrix 는 항등 폴백 → 카메라공간을 sRGB 로 오해한 색.
    다행히 LibRaw 는 DNG 의 ColorMatrix1/2 로 `color_matrix`(cam->선형sRGB, 행합=1)는 채워 두므로
    그걸 역산해 cam_xyz 를 되살린다: cam_rgb=inv(color_matrix) 가 sRGB->cam 이고
    cam_xyz = cam_rgb @ inv(sRGB->XYZ). 행별 스케일은 소실되지만 rel_gain(TREF 대비 비율에서 상쇄)
    과 cam_to_srgb_matrix(행합 정규화)에는 영향이 없다 — DNG ColorMatrix2 직접 파싱과 수치 동일(실측).
    둘 다 없으면 기존과 같은 축퇴 행렬(0)을 그대로 반환한다(중성 폴백 유지).
    """
    cam = np.asarray(raw.rgb_xyz_matrix, float)[:3, :3]
    if np.all(np.isfinite(cam)) and abs(np.linalg.det(cam)) > 1e-8:
        return cam
    cm = np.asarray(getattr(raw, "color_matrix", np.zeros((3, 4))), float)[:3, :3]
    if np.all(np.isfinite(cm)) and abs(np.linalg.det(cm)) > 1e-8:
        try:
            return np.linalg.inv(cm) @ np.linalg.inv(_XYZ_RGB_D65)
        except np.linalg.LinAlgError:
            pass
    return cam


def baked_wb(cam_xyz, daylight_ref):
    """프록시 디코딩에 베이크할 TREF(daylight) 기준 WB 배수."""
    return compute_user_wb(cam_xyz, daylight_ref, TREF, 0.0)


def srgb_to_linear(c):
    """sRGB(또는 rawpy gamma=(2.4,12.92)) -> 선형. 셰이더 srgbToLinear 와 정합.
    float32 유지(모든 호출부가 float32 이미지) — float64 승격 시 26MP export 배열이 2배."""
    c = np.clip(np.asarray(c, np.float32), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    """선형 -> sRGB. 셰이더 linearToSrgb 와 정합. float32 유지(위 사유 동일)."""
    c = np.clip(np.asarray(c, np.float32), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1.0 / 2.4)) - 0.055)


HL_KNEE = 0.7   # 하이라이트 롤오프 시작(선형). 이 위는 1.0 으로 부드럽게 점근 압축.


def highlight_rolloff(lin, knee=HL_KNEE):
    """선형광 하이라이트(>knee)를 1.0 으로 부드럽게 점근 압축(C1 연속, 채널별).

    베이스라인 게인이 고휘도 장면을 선형으로 밀면 하이라이트가 프록시 1.0 에서 하드클립
    되어 블로우아웃·과채도(플레어)로 보인다. 카메라 S커브 숄더처럼 끝단을 눌러 디테일·
    그라데이션을 보존하고, 채널별 압축이라 밝을수록 흰색으로 수렴(과채도 완화).
    knee 에서 기울기 1(연속), x→∞ 에서 1.0 점근."""
    lin = np.asarray(lin, np.float32)
    out = lin.copy()
    hi = lin > knee
    if np.any(hi):
        e = lin[hi] - knee
        out[hi] = 1.0 - (1.0 - knee) * np.exp(-e / (1.0 - knee))
    return out


def filmic(x):
    """scene-linear sRGB(≥0, 헤드룸 >1 가능) → display sRGB[0,1] 단일 베이스 톤커브.

    하이라이트 숄더(highlight_rolloff: knee 이상을 1.0 으로 점근 압축)로 밝은 영역을
    부드럽게 롤오프한 뒤 sRGB OETF 로 인코딩. 이 곡선 하나가 scene→display 변환과
    하이라이트 처리를 담당한다(기존 linear_to_srgb + rolloff + 게인캡 + 디새추 대체)."""
    return linear_to_srgb(highlight_rolloff(x))


def auto_exposure_gain(target_median, cam_xyz, daylight_ref, as_shot, lin_native):
    """이미지별 자동 노출(scene-linear 게인). 렌더(as-shot WB → 매트릭스 → filmic) 후
    display 휘도의 **중앙값**이 target_median 이 되도록 solve(로그공간 이분법).

    중앙값 기반이라 밝은 하늘이 큰 면적이어도 안 끌려감(평균매칭+게인캡 휴리스틱 대체).
    target 없으면 1.0 폴백."""
    if not target_median or not np.isfinite(target_median) or target_median <= 0:
        return 1.0
    M = cam_to_srgb_matrix(cam_xyz).astype(np.float32)
    rel = rel_gain(cam_xyz, daylight_ref, as_shot, 0.0).astype(np.float32)
    s = np.asarray(lin_native, np.float32)[::8, ::8].reshape(-1, 3)   # 통계용 서브샘플

    def disp_median(g):
        d = filmic((s * (g * rel)) @ M.T)
        return float(np.median(d @ _LUMA))

    lo, hi = 0.05, 64.0
    for _ in range(24):
        g = (lo * hi) ** 0.5
        if disp_median(g) < target_median:
            lo = g
        else:
            hi = g
    return (lo * hi) ** 0.5


def rel_gain(cam_xyz, daylight_ref, kelvin, tint=0.0):
    """TREF 베이크 대비 상대 WB 게인(카메라공간, green 정규화). 셰이더 wbPreview 와 동일."""
    t = np.asarray(compute_user_wb(cam_xyz, daylight_ref, kelvin, tint)[:3], float)
    b = np.asarray(baked_wb(cam_xyz, daylight_ref)[:3], float)
    g = t / b
    g = g / g[1]
    return g


def estimate_cct(cam_xyz, daylight_ref, camera_wb) -> int:
    """카메라 as-shot WB 배수에 가장 가까운 Kelvin 을 탐색해 반환(tint 무시, 호환용)."""
    return estimate_wb(cam_xyz, daylight_ref, camera_wb)[0]


def estimate_wb(cam_xyz, daylight_ref, camera_wb):
    """카메라 as-shot WB 배수에 맞는 (Kelvin, tint) 추정.

    Kelvin 1자유도(R:B 축)만으론 텅스텐/불빛처럼 데이라이트 궤적을 벗어난(off-locus)
    광원의 R/G·B/G 2자유도를 못 맞춘다. → Kelvin 으로 R:B 비를 맞추고, tint(green 축)로
    잔여 green 레벨을 맞춰 camera_whitebalance 를 충실히 재현(모닥불 등 정확).
    tint: + 마젠타(녹↓) / - 그린(녹↑), compute_user_wb 와 동일 정의.
    """
    cw = np.asarray(camera_wb, float)[:3]
    if not (cw[1] > 0 and cw[2] > 0 and np.all(np.isfinite(cw))):
        return TREF, 0.0                   # 카메라 WB 없음/비정상(제네릭 DNG 등) → 중성(daylight) 폴백
    cw = cw / cw[1]                         # green 정규화 -> (R/G, 1, B/G)
    target_rb = cw[0] / cw[2]              # R:B 비 (= 따뜻-차가움 축)
    Ts, ms = _wb_table(cam_xyz, daylight_ref)   # T별 green-정규화 user_wb (cam 고정이라 캐시)
    i = int(np.argmin(np.abs(ms[:, 0] / ms[:, 2] - target_rb)))
    best_t = int(Ts[i])
    m = ms[i]
    # 선택된 Kelvin 의 green-정규화 배수에서 tint 산출:
    #   user_wb green = (1-0.3·tint), R,B 불변 → (1-0.3·tint)=mR/cwR=mB/cwB(평균으로 견고).
    g = 0.5 * (m[0] / cw[0] + m[2] / cw[2])
    tint = float(max(-1.5, min(1.5, (1.0 - g) / 0.3)))
    return best_t, tint


_WB_TABLE_CACHE = {}


def _wb_table(cam_xyz, daylight_ref):
    """T(2000..12000) -> green-정규화 user_wb(tint=0) 테이블. cam_xyz/ref 고정이라 1회 구축·캐시."""
    cam_xyz = np.asarray(cam_xyz, float)
    daylight_ref = np.asarray(daylight_ref, float)
    key = (cam_xyz.tobytes(), daylight_ref.tobytes())
    cached = _WB_TABLE_CACHE.get(key)
    if cached is None:
        Ts = np.arange(2000, 12001, 25)
        ms = np.empty((len(Ts), 3))
        for j, T in enumerate(Ts):
            m = np.asarray(compute_user_wb(cam_xyz, daylight_ref, int(T), 0.0)[:3])
            ms[j] = m / m[1]
        _WB_TABLE_CACHE[key] = (Ts, ms)
        cached = _WB_TABLE_CACHE[key]
    return cached
