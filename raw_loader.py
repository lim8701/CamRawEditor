"""RAW -> 화면 표시용 프록시 QImage 디코더 (후지 RAF 및 타 제조사 RAW 공용, rawpy/LibRaw).

편집은 축소된 프록시로 하고(인터랙티브용), 풀해상도는 나중에 export 단계에서만
처리한다. 디코딩엔 고정 TREF(daylight) WB 만 베이크하고, 사용자 절대 색온도(Kelvin)는
셰이더/pipeline 프론트엔드에서 상대 게인으로 적용한다(재디코딩 없는 실시간 WB).
"""

import numpy as np
import rawpy
from scipy.ndimage import zoom
from PySide6.QtGui import QImage

import lens
from wb import (auto_exposure_gain, baked_wb, cam_to_srgb_matrix, estimate_wb,
                linear_to_srgb, srgb_to_linear)

# 프리뷰 프록시 헤드룸: scene-linear 를 8bit 에 담을 때 code=oetf(L/H), 셰이더가 ×H 로 복원.
# H 만큼(여기 4× ≈ 2스톱) 하이라이트 헤드룸 확보 → filmic 톤커브가 누를 여지 보존.
# ⚠️adjust.frag/convert.frag 의 PROXY_HEADROOM 와 반드시 동일해야 함.
PROXY_HEADROOM = 4.0

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def clip_level(auto_gain: float) -> float:
    """센서 포화(=디코드 상한)가 나타나는 scene-linear 값 — 하이라이트 디새추 게이트 기준.

    디코드는 카메라네이티브 1.0 = 센서 포화이고 거기에 자동노출 게인 g 를 곱하므로 포화 레벨은 g 다.

    ⚠️**g > PROXY_HEADROOM 이면 게이트를 끈다**(도달 불가능한 큰 값을 돌려준다). 프록시 인코딩이
    L/H 를 [0,1] 로 클램프하므로 그런 사진(후지에선 흔하다: +2.4EV = 5.4배)은 **센서 H/g 이상이
    이미 한 값으로 뭉개져 클립 여부를 구분할 수 없다.** 예전에는 min(g, H) 로 상한을 뒀는데,
    그러면 g=5.4 에서 게이트가 **센서의 0.67 부터** 열려 클립되지도 않은 하늘을 흰색으로 날린다
    — 이 게이트를 만든 이유 그 자체다(코드 검토에서 잡혔다). 끄는 쪽의 손해는 그 파일에서
    '색끼가 남은 부분 클립'을 못 잡는 것인데, 실측 발동률이 이미 0.00% 라 잃는 것이 없다.
    """
    g = float(max(auto_gain, 1e-6))
    return g if g <= PROXY_HEADROOM else 1e9


def _embedded_jpeg_lum(raw):
    """RAW 임베드 JPEG(카메라 현상본)의 휘도(0..1) 1D 배열. 실패 시 None.
    이미지별 자동 노출의 '목표 밝기'(카메라의 샷별 측광/톤 의도)를 통계로 쓴다."""
    try:
        th = raw.extract_thumb()
        if th.format != rawpy.ThumbFormat.JPEG:
            return None
        qi = QImage()
        from decode_lock import QT_IMG_LOCK
        with QT_IMG_LOCK:      # loadFromData 는 GIL 을 쥔 채 플러그인 뮤텍스 대기(decode_lock)
            ok = qi.loadFromData(th.data)
        if not ok:
            return None
        qi = qi.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qi.width(), qi.height()
        if w == 0 or h == 0:
            return None
        a = (np.frombuffer(qi.constBits(), np.uint8)
             .reshape(h, qi.bytesPerLine())[:, :w * 3].reshape(h, w, 3).astype(np.float32) / 255.0)
        return (a @ LUMA).ravel()
    except Exception:
        return None


def _embedded_jpeg_median(raw):
    """임베드 JPEG 중앙값 휘도(0..1). 고휘도(하늘 등)에 강건 → scene-linear 자동노출 목표."""
    lum = _embedded_jpeg_lum(raw)
    return None if lum is None else float(np.median(lum))


# 감마 변환 LUT(pow 대신 게더). wb 의 정확 함수로 1회 구축 → 값 동일, 속도만 향상.
_SRGB2LIN = None
_LIN2SRGB = None


def _dither(shape):
    """사각형 디더(±0.5 LSB) — 8bit 양자화 밴딩 제거(확률적 반올림). 시드 고정(재로드 동일)."""
    return np.random.default_rng(12345).random(shape, dtype=np.float32) - 0.5


def _srgb2lin_lut():
    global _SRGB2LIN
    if _SRGB2LIN is None:
        _SRGB2LIN = srgb_to_linear(np.arange(65536, dtype=np.float32) / 65535.0).astype(np.float32)
    return _SRGB2LIN


def _lin2srgb_lut():
    global _LIN2SRGB
    if _LIN2SRGB is None:
        _LIN2SRGB = linear_to_srgb(np.arange(65536, dtype=np.float32) / 65535.0).astype(np.float32)
    return _LIN2SRGB


def _export_demosaic(raw):
    """Export(풀해상도) 디모자이크 선택: X-Trans(raw_pattern 6x6)=LINEAR(프록시와 정합 유지),
    그 외 Bayer=AHD(고화질 — 쌍선형 대비 색모아레/지퍼링/무름 개선). 프록시는 항상 LINEAR.
    ⚠️정합 트레이드오프: Bayer 는 프록시(LINEAR 축소)와 export(AHD)의 미세 디테일이 살짝 달라짐
      (프록시가 2560 축소라 체감 작음). Fuji(X-Trans)는 양쪽 LINEAR 라 무변경.
      → 화질/정합 재검토 시 docs/raw_demosaic.md 참고(추후 조정 후보)."""
    # 명확한 2x2 Bayer 만 AHD. X-Trans(6x6)·None(Foveon/모노 등 CFA 없음)·이형은 LINEAR(안전 폴백).
    rp = getattr(raw, "raw_pattern", None)
    is_bayer = rp is not None and getattr(rp, "shape", None) == (2, 2)
    return rawpy.DemosaicAlgorithm.AHD if is_bayer else rawpy.DemosaicAlgorithm.LINEAR


def _decode_native(path: str, bayer_ahd: bool = False):
    """RAW -> 카메라네이티브 16bit(TREF 베이크, 매트릭스 미적용) + 메타. load_proxy/load_full 공용.

    WB 는 디코딩에 베이크하지 않는다(셰이더가 카메라공간 상대게인으로 실시간 적용). TREF(daylight)만
    베이크. X-Trans 는 half_size 격자 회피를 위해 full + LINEAR 디모자이크.
    bayer_ahd=True(export 경로): Bayer 는 AHD, X-Trans 는 LINEAR(_export_demosaic). False(프록시): 항상 LINEAR.
    반환: (rgb16, cam_xyz(3x3), ref(3), as_shot, as_shot_tint, target_median)
    """
    with rawpy.imread(path) as raw:
        cam_xyz = np.array(raw.rgb_xyz_matrix)[:3, :3]
        ref = np.array(raw.daylight_whitebalance, dtype=float)[:3]
        # daylight_whitebalance 가 비어있음/0/비유한(제네릭·폰·드론 DNG 등)이면 ref/ref[1] 가 NaN →
        # user_wb NaN → 블랙 프레임. 중성 [1,1,1] 로 폴백(최소한 정상 밝기로 현상). estimate_wb 도 방어.
        ref = ref / ref[1] if (ref[1] > 0 and np.all(np.isfinite(ref))) else np.ones(3)
        as_shot, as_shot_tint = estimate_wb(cam_xyz, ref, raw.camera_whitebalance)
        target_median = _embedded_jpeg_median(raw)   # 이미지별 자동 노출 목표(중앙값, 고휘도 강건)
        rgb16 = raw.postprocess(
            user_wb=baked_wb(cam_xyz, ref),     # TREF daylight 베이크(고정)
            output_color=rawpy.ColorSpace.raw,  # 카메라 네이티브(매트릭스 미적용)
            demosaic_algorithm=(_export_demosaic(raw) if bayer_ahd
                                else rawpy.DemosaicAlgorithm.LINEAR),
            output_bps=16,                      # 게인/감마를 numpy 로 적용
            no_auto_bright=True,                # 자동 밝기 보정 OFF(상대노출 보존)
            gamma=(2.4, 12.92),                 # 감마 인코딩
            highlight_mode=rawpy.HighlightMode.Clip,
        )
    return rgb16, cam_xyz, ref, as_shot, as_shot_tint, target_median


def _encode_headroom(rgb16, cam_xyz, ref, as_shot, target_median, lens_profile):
    """카메라네이티브 16bit -> (렌즈) -> 선형 -> 자동노출 -> (헤드룸 인코딩 disp float[0,1], 자동노출 게인).

    code = oetf(L/H): scene-linear L 을 H 로 나눠 [0,1] 감마로 인코딩(셰이더가 ×H 복원).
    ⚠️렌즈 보정을 현상 전 카메라네이티브에 먼저 적용해야 export(render_full)와 정합(자동노출이
      렌즈 적용 후 통계로 계산됨). load_proxy(8bit)·load_full(16bit) 공용 — 동일 scene-linear 보장.
    lens_profile: lens.load_profile() 결과(RAF 내장 샷별 보정) 또는 None(보정 안 함)."""
    if lens_profile is not None:
        rgb16 = np.clip(lens.apply(rgb16, lens_profile), 0.0, 65535.0).astype(np.uint16)
    lin = _srgb2lin_lut()[rgb16]                         # (H,W,3) float32 선형(카메라네이티브)
    # ⚠️디코드는 **항상** 자동노출을 적용한다. 끄기(Auto exposure 체크 해제)는 재디코드가 아니라
    #   셰이더/pipeline 의 **노출 지수에서 −log2(g) 를 빼는 것**으로 처리한다 — 곱셈이라 수학적으로
    #   같고 토글이 즉시 반응한다(재디코드는 2~4초). 유일한 차이는 프록시 헤드룸 클램프로 센서
    #   H/g 이상이 뭉개지는 것인데 실측 0.000~0.388%(게인 +2EV 초과 파일만, 그것도 near-clip).
    g = auto_exposure_gain(target_median, cam_xyz, ref, as_shot, lin)
    lin *= g
    idx = (np.clip(lin * (1.0 / PROXY_HEADROOM), 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    # float32 [0,1] 헤드룸 인코딩(카메라네이티브) + **자동노출 게인**(소비자가 clip_level()·EV 로 유도)
    return _lin2srgb_lut()[idx], float(g)


def load_proxy(path: str, max_edge: int = 2560, lens_correct: bool = True):
    """RAW 를 디코딩해 (QImage(8bit), as_shot, as_shot_tint, cam_xyz(9), ref(3), cam2srgb(9), 자동노출게인) 반환.

    프록시는 카메라 네이티브 RGB(매트릭스 미적용)를 TREF WB 베이크 + 헤드룸 감마 인코딩(8bit).
    셰이더가 [선형화→WB 상대게인→cam2srgb 매트릭스→filmic] 로 변환한다.
    """
    rgb16, cam_xyz, ref, as_shot, as_shot_tint, target_median = _decode_native(path)

    # full 디코딩(X-Trans 격자 회피)이라 무거운 numpy 전에 먼저 max_edge 로 축소(반응성).
    # 정수 2× 박스평균(빠른 AA) 반복 후 남은 분수배만 가벼운 bilinear zoom.
    if max(rgb16.shape[:2]) > max_edge:
        x = rgb16.astype(np.float32)
        while max(x.shape[0] // 2, x.shape[1] // 2) >= max_edge and min(x.shape[:2]) >= 2:
            hh, ww = (x.shape[0] // 2) * 2, (x.shape[1] // 2) * 2
            x = (x[0:hh:2, 0:ww:2] + x[1:hh:2, 0:ww:2]
                 + x[0:hh:2, 1:ww:2] + x[1:hh:2, 1:ww:2]) * 0.25
        f = max_edge / float(max(x.shape[:2]))
        if f < 1.0:
            x = zoom(x, (f, f, 1.0), order=1)
        rgb16 = np.clip(x + 0.5, 0.0, 65535.0).astype(np.uint16)

    prof = lens.load_profile(path) if lens_correct else None
    disp, gain = _encode_headroom(rgb16, cam_xyz, ref, as_shot, target_median, prof)
    dth = _dither(disp.shape)               # ±0.5 LSB 디더(8bit 양자화 밴딩 제거)
    rgb = np.clip(disp * 255.0 + 0.5 + dth, 0.0, 255.0).astype(np.uint8)
    rgb = np.ascontiguousarray(rgb)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    cam2srgb = cam_to_srgb_matrix(cam_xyz)
    return (img, int(as_shot), float(as_shot_tint), cam_xyz.flatten().tolist(),
            ref.tolist(), cam2srgb.flatten().tolist(), gain)


def load_full(path: str, lens_correct: bool = True):
    """GPU export 용: 다운스케일 없는 풀해상도 + 16bit(RGBA64) 헤드룸 인코딩. 메타는 load_proxy 와 동형.

    프록시(8bit, 프리뷰용)와 동일 인코딩 규약(셰이더 src 입력)이되 다운스케일 없음 + 16bit.
    """
    rgb16, cam_xyz, ref, as_shot, as_shot_tint, target_median = _decode_native(path, bayer_ahd=True)
    prof = lens.load_profile(path) if lens_correct else None
    disp, gain = _encode_headroom(rgb16, cam_xyz, ref, as_shot, target_median, prof)
    code = (np.clip(disp, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    h, w, _ = code.shape
    rgba = np.empty((h, w, 4), np.uint16)
    rgba[..., :3] = code
    rgba[..., 3] = 65535                     # alpha=불투명(RGBA64 포맷)
    rgba = np.ascontiguousarray(rgba)
    img = QImage(rgba.data, w, h, 8 * w, QImage.Format.Format_RGBA64).copy()
    cam2srgb = cam_to_srgb_matrix(cam_xyz)
    return (img, int(as_shot), float(as_shot_tint), cam_xyz.flatten().tolist(),
            ref.tolist(), cam2srgb.flatten().tolist(), gain)
