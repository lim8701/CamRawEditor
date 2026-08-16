"""display-referred 이미지(JPG/PNG/TIFF) -> 프록시 QImage 디코더 (raw_loader 의 형제).

라이트룸 등에서 이미 현상·톤매핑을 끝낸 사진을 그대로 받아, 우리 파이프라인의 입력 계약
(카메라네이티브 scene-linear 의 헤드룸 인코딩)으로 되돌려 준다. 반환 계약은
`raw_loader.load_proxy` / `load_full` 과 **동일한 6-튜플**이라 호출부는 분기만 하면 된다.

핵심: 셰이더/pipeline 프론트엔드는 `srgbToLinear(src)*PROXY_HEADROOM -> WB -> cam2srgb ->
filmic()` 로 display 를 만든다. 이미 display 인 이미지를 그냥 넣으면 `filmic()` 이 한 번 더
걸린다. 그런데 `wb.filmic = linear_to_srgb(highlight_rolloff(x))` 는 단조라 **해석적 역함수**가
있으므로, 로드 시 `filmic⁻¹` 을 구워 넣으면 프론트엔드가 정확히 그 역을 수행해 **중립 설정에서
원본 픽셀이 복원**된다(실측: export 경로 전 코드 왕복 오차 0). 그 위에서 노출/톤/커브/
필름시뮬/마스킹/그레인이 평소대로 동작한다.

메타데이터는 '카메라 공간 = 선형 sRGB' 로 둔다(_CAM_XYZ 주석) — cam2srgb 가 항등이 되고
Temp/Tint 는 기본값에서 게인 1, 움직이면 선형 sRGB 상의 화이트밸런스가 된다.
"""

import os

import numpy as np
from scipy.ndimage import zoom
from PySide6.QtGui import QColorSpace, QImage, QImageReader

import raw_loader
import wb
from raw_loader import PROXY_HEADROOM

# 탐색기에 노출/디코딩할 일반 이미지 확장자(Qt 이미지 플러그인이 디코딩).
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# 순백(코드 = maxcode)은 filmic 역함수가 ∞ 라 **양자화 빈 안**에서 상한을 잡는다.
# 빈 분율은 0.25 — 실측으로 8bit/16bit 전 코드가 rint 왕복 일치하는 값이다.
# ⚠️0.5(빈 중앙)면 8bit 최상단이 정확히 254.5 로 떨어져 np.rint(짝수 반올림)가 254 를 내놓아
#   순백만 1 code 어긋난다. 반대로 상한을 크게(예: 4.0) 잡으면 254↔255 간격이 벌어져
#   노출을 내릴 때 하이라이트 경계가 포스터라이즈된다. cap: 8bit 2.171 / 16bit 3.834.
_CAP_BIN_FRAC = 0.25

# cam_xyz 는 wb.py 전반에서 `cam_xyz @ XYZ` (=XYZ->카메라RGB)로 쓰인다. 카메라 공간을
# 선형 sRGB 로 두면 wb.cam_to_srgb_matrix 안에서 cam_rgb = cam_xyz @ _XYZ_RGB_D65 = I 가 되어
# 행합 1, 역행렬도 항등 → 매트릭스 단계가 정확히 무동작이 된다.
# ⚠️항등행렬로 두면 안 된다 — compute_user_wb 가 cam_xyz @ XYZ_planckian 을 계산하므로
#   항등이면 CIE XYZ 를 카메라 응답으로 오해해 Temp/Tint 를 움직이는 순간 색이 틀어진다.
# (알려진 한계: 선형 sRGB 원색은 von-Kries 에 좋은 공간이 아니라 ~3000K 아래에서 Temp 가
#  실제 카메라보다 과민하다. 중립 ±1000K 는 10% 이내 — v1 은 이대로 간다.)
_CAM_XYZ = np.linalg.inv(wb._XYZ_RGB_D65)
_REF = np.ones(3)                      # daylight_whitebalance 대응 — TREF 에서 rel_gain 이 정확히 1

_INV_LUT = {}                          # maxcode -> filmic⁻¹ LUT(65536, float32)


def is_display_image(path) -> bool:
    """일반(display-referred) 이미지 파일인가 — 로더/탐색기 분기용."""
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXTS


def meta():
    """일반 이미지의 중립 메타 (cam_xyz, ref, as_shot_kelvin, as_shot_tint).
    pipeline.render_full 과 load_proxy/load_full 이 공유한다."""
    return _CAM_XYZ.copy(), _REF.copy(), int(wb.TREF), 0.0


def _finv(s):
    """선형 display 값 -> scene-linear (wb.filmic 의 해석적 역함수, 클램프 없음).

    filmic = linear_to_srgb(highlight_rolloff(x)) 이고 rolloff 는
    s = 1 - (1-k)exp(-(x-k)/(1-k))  (x>k) 이므로  x = k - (1-k)ln((1-s)/(1-k)).
    s->1 에서 발산하므로 호출부가 반드시 상한을 건다(headroom_cap)."""
    k = np.float32(wb.HL_KNEE)
    s = np.asarray(s, np.float32)
    hi = k - (1.0 - k) * np.log(np.maximum(1.0 - s, 1e-12) / (1.0 - k))
    return np.where(s <= k, s, hi).astype(np.float32)


def headroom_cap(maxcode: int) -> float:
    """소스 비트깊이별 scene-linear 상한(_CAP_BIN_FRAC 주석 참조). 둘 다 PROXY_HEADROOM 아래."""
    d = np.float32((maxcode - _CAP_BIN_FRAC) / maxcode)
    return float(_finv(wb.srgb_to_linear(d)))


def _inv_lut(maxcode: int):
    """16bit 코드 -> scene-linear LUT(raw_loader._srgb2lin_lut 과 같은 관용구).

    8bit 소스는 코드를 ×257 로 16bit 에 정확히 실어 같은 LUT 를 쓰되(255→65535),
    **상한만 소스 비트깊이 기준**이라 maxcode 별로 캐시한다."""
    lut = _INV_LUT.get(maxcode)
    if lut is None:
        codes = np.arange(65536, dtype=np.float32) / 65535.0
        lut = np.minimum(_finv(wb.srgb_to_linear(codes)),
                         np.float32(headroom_cap(maxcode))).astype(np.float32)
        _INV_LUT[maxcode] = lut
    return lut


def filmic_inv(disp16, maxcode: int):
    """16bit 코드 배열 -> scene-linear sRGB float32 (상한 클램프 포함)."""
    return _inv_lut(maxcode)[disp16]


def _read_display(path: str):
    """파일 -> (display 코드 uint16 0..65535 (H,W,3), maxcode).

    ⚠️세 가지가 전부 필요하다:
    ① `setAutoTransform` — EXIF Orientation 적용. RAW 는 raw.postprocess 가 자동 회전하지만
       QImage 는 안 해서 없으면 세로 사진이 눕는다. **프록시와 export 가 같은 한 줄을 쓰므로**
       회전 로직이 두 경로로 갈라질 일이 없다.
    ② 컬러스페이스 → sRGB. Qt 는 색관리를 안 해서, AdobeRGB/P3/ProPhoto 로 태그된 export
       (16bit TIFF 의 통상 케이스)를 sRGB 로 오해하면 첫 픽셀부터 색이 틀린다.
       태그가 없으면(대부분의 JPEG) sRGB 로 간주 — 관례대로.
    ③ 16bit 소스(PNG16/TIFF16)는 16bit 그대로 — 상한이 3.834 로 올라가 하이라이트에 유리.
    """
    rd = QImageReader(str(path))
    rd.setAutoTransform(True)
    img = rd.read()
    if img.isNull():
        raise ValueError(rd.errorString() or "cannot decode image")
    cs = img.colorSpace()
    srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    if cs.isValid() and cs != srgb:
        img.convertToColorSpace(srgb)
    deep = img.format() in (QImage.Format.Format_RGBX64, QImage.Format.Format_RGBA64,
                            QImage.Format.Format_RGBA64_Premultiplied,
                            QImage.Format.Format_Grayscale16)
    if deep:
        img = img.convertToFormat(QImage.Format.Format_RGBX64)
        w, h = img.width(), img.height()
        a = (np.frombuffer(img.constBits(), np.uint16)
             .reshape(h, img.bytesPerLine() // 2)[:, :w * 4].reshape(h, w, 4)[..., :3])
        return np.ascontiguousarray(a), 65535
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    a = (np.frombuffer(img.constBits(), np.uint8)
         .reshape(h, img.bytesPerLine())[:, :w * 3].reshape(h, w, 3))
    return a.astype(np.uint16) * np.uint16(257), 255       # 255→65535 정확히 매핑


def _downscale(a16, max_edge: int):
    """긴 변 = max_edge 로 축소(display 코드 공간, uint16). raw_loader.load_proxy 와 같은 방식 —
    정수 2× 박스평균 반복(빠른 AA) 후 남은 분수배만 bilinear. 감마 공간 축소인 것도 RAW 와 동일."""
    if max_edge <= 0 or max(a16.shape[:2]) <= max_edge:
        return a16
    x = a16.astype(np.float32)
    while max(x.shape[0] // 2, x.shape[1] // 2) >= max_edge and min(x.shape[:2]) >= 2:
        hh, ww = (x.shape[0] // 2) * 2, (x.shape[1] // 2) * 2
        x = (x[0:hh:2, 0:ww:2] + x[1:hh:2, 0:ww:2]
             + x[0:hh:2, 1:ww:2] + x[1:hh:2, 1:ww:2]) * 0.25
    f = max_edge / float(max(x.shape[:2]))
    if f < 1.0:
        x = zoom(x, (f, f, 1.0), order=1)
    return np.clip(x + 0.5, 0.0, 65535.0).astype(np.uint16)


def _encode_headroom(x):
    """scene-linear -> 헤드룸 인코딩 float[0,1]. raw_loader._encode_headroom 의 마지막 두 줄과
    **같은 식**(LUT 게더)이라 RAW 프록시와 인코딩 계약이 정확히 일치한다."""
    idx = (np.clip(x * (1.0 / PROXY_HEADROOM), 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    return raw_loader._lin2srgb_lut()[idx]


def scene_linear(path: str, out_edge: int = 0):
    """export(pipeline.render_full) 용 — 축소 후 scene-linear float32 (H,W,3) 반환.
    프록시와 달리 헤드룸 8bit 인코딩을 거치지 않으므로 왕복 손실이 없다(실측 오차 0)."""
    disp16, maxcode = _read_display(path)
    return filmic_inv(_downscale(disp16, int(out_edge or 0)), maxcode)


def load_proxy(path: str, max_edge: int = 2560, lens_correct: bool = True):
    """일반 이미지 -> (QImage(8bit), as_shot, as_shot_tint, cam_xyz(9), ref(3), cam2srgb(9)).

    lens_correct 는 계약 일치를 위한 자리만 지킨다(일반 이미지엔 샷별 렌즈 프로파일이 없음).
    """
    disp16, maxcode = _read_display(path)
    code = _encode_headroom(filmic_inv(_downscale(disp16, max_edge), maxcode))
    dth = raw_loader._dither(code.shape)          # ±0.5 LSB 디더(8bit 양자화 밴딩 제거)
    rgb = np.ascontiguousarray(np.clip(code * 255.0 + 0.5 + dth, 0.0, 255.0).astype(np.uint8))
    h, w, _ = rgb.shape
    return _result(QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())


def load_full(path: str, lens_correct: bool = True):
    """GPU export 용: 다운스케일 없는 풀해상도 + 16bit(RGBA64) 헤드룸 인코딩.
    raw_loader.load_full 과 동형(셰이더 src 입력 규약 동일)."""
    disp16, maxcode = _read_display(path)
    code = (np.clip(_encode_headroom(filmic_inv(disp16, maxcode)), 0.0, 1.0)
            * 65535.0 + 0.5).astype(np.uint16)
    h, w, _ = code.shape
    rgba = np.empty((h, w, 4), np.uint16)
    rgba[..., :3] = code
    rgba[..., 3] = 65535                          # alpha=불투명(RGBA64 포맷)
    rgba = np.ascontiguousarray(rgba)
    return _result(QImage(rgba.data, w, h, 8 * w, QImage.Format.Format_RGBA64).copy())


def _result(img: QImage):
    cam, ref, as_shot, as_shot_tint = meta()
    return (img, int(as_shot), float(as_shot_tint), cam.flatten().tolist(),
            ref.tolist(), wb.cam_to_srgb_matrix(cam).flatten().tolist())
