"""단안 깊이 마스킹 엔진 (ONNX / Depth Anything V3 Small).

Scene(의미)·Face(부위) 에 이어 **거리** 축의 마스크 소스. "같은 나무인데 앞쪽만", "인물 뒤 배경
전체", "원거리 건물만" 처럼 시맨틱 세그가 못 가르는 영역을 near~far 범위로 고른다.
PySide6/QML 비의존 — numpy in/out 독립 모듈.

2단계 사용(sky_seg 와 같은 이유 — 무거운 추론은 이미지당 1회):
  infer_distance(rgb, guide) → distance[0..1] (프록시 해상도, 캐시)   # 추론+정제 ~0.7s
  compose_mask(distance, near, far, feather) → soft alpha[0,1]        # 밴드패스만 ~46ms

⚠️ **가이디드 필터를 마스크가 아니라 깊이 맵에 1회 적용**한다(haze.py 가 t-맵에 하는 순서).
   sky_seg 는 클래스 확률을 합산한 '뒤' 정제하지만, 깊이는 정제 결과가 near/far 와 무관하므로
   미리 한 번 해두면 슬라이더 드래그가 smoothstep 비용만 남는다(실측 155ms → 46ms).

⚠️ **정규화 공간이 핵심이다.** V3 는 실제 depth(z)를 내놓는데, z 를 선형 정규화하면 원거리
   꼬리가 길어 근거리가 하위 몇 %로 압축되고(실측 엔트로피 4.22/6), 반대로 disparity(1/z)로
   정규화하면 원거리가 포화한다(V2 가 구조적으로 겪는 문제 — 산맥 계조가 뭉친다, 3.98/6).
   **log z** 가 양쪽을 피하고 최악 케이스가 가장 좋다(4.78/6). 사람의 깊이 지각·피사계심도가
   대략 log 스케일인 것과도 맞는다. models/README.md 의 비교표 참조 — 재조사 금지.

⚠️ 출력은 **이미지별로 정규화된 상대 거리**다. 절대 스케일이 없어 near/far 값이 사진 간에
   그대로 옮겨가지 않는다(copy/paste-edits 로 붙이면 다른 영역이 잡힘). UI 에 고지 필요.

모델: onnx-community/depth-anything-v3-small 의 사전 export ONNX(fp32, ~105MB, Apache-2.0).
최초 호출 시 자동 다운로드. torch/transformers 불필요(onnxruntime 만 사용).
"""

import hashlib
import json
import os
import threading
import urllib.request

import numpy as np

import app_dirs
# 머신 전역 DML 직렬화 락 — NAFNet 타일 추론과 이 모듈의 추론이 **동시에** DirectML 에
# 제출되면 NVIDIA 드라이버(nvwgf2umx.dll)가 죽는 것이 재현됨(정의부 주석 참조).
from ai_denoise import GPU_LOCK
from face_seg import _resize          # cv2 판 리사이즈(scipy zoom 보다 빠름 — face_seg 주석 참조)
from sky_seg import _MEAN, _STD, _guided_filter

# ── 모델 ────────────────────────────────────────────────────────────────────
# Depth Anything V3 **Small**. Small 만 Apache-2.0 이라는 제약은 V2 와 같지만, V3 는 실제
# depth 를 내놓아 **정규화 공간을 우리가 고를 수 있다**(V2 는 disparity 가 구워져 있어 원거리
# 포화를 되돌릴 수 없다). 코드(MIT)와 충돌 없음 — 하늘 세그·얼굴 파싱이 안고 있는
# '상업 배포 시 교체' 부담이 없는 유일한 모델이다.
_REPO = "onnx-community/depth-anything-v3-small"
# sky_seg 와 같은 방침: 이동 참조(main) 대신 커밋 리비전 고정 + SHA-256 검증(HF LFS oid).
# 모델 교체 시 _REV + _SHA256 + _BYTES + TOTAL_BYTES 를 함께 갱신한다.
_REV = "0b6a7f3bf5595f9950b91389e0da3a0de130324c"

# ⚠️**하위 디렉터리에 받는다.** 이 모델은 ONNX 외부 데이터(2파일)를 쓰고, 데이터 파일 이름
#   `model.onnx_data` 가 .onnx 프로토 안에 박혀 있어(ORT 가 모델 파일과 **같은 디렉터리**에서
#   그 이름으로 찾는다) 이름을 바꿀 수 없다. 플랫한 MODELS_DIR 에 `model.onnx_data` 라는
#   일반적인 이름을 두면 나중에 외부 데이터를 쓰는 다른 모델과 충돌한다.
_SUBDIR = "depth_anything_v3_small"
# 로컬 상대경로 → repo 내 경로 (caption.py 의 다중 파일 번들과 같은 방식)
_FILES = {
    f"{_SUBDIR}/model.onnx": "onnx/model.onnx",
    f"{_SUBDIR}/model.onnx_data": "onnx/model.onnx_data",
}
_SHA256 = {
    f"{_SUBDIR}/model.onnx":
        "396008798244a074297fd88e450433b1357fc687f534939375c804ded86e7b2a",
    f"{_SUBDIR}/model.onnx_data":
        "802bb24741e67f5bb2b369fc64d40afe11439cc895d676d658d65cfb75c9860f",
}
_BYTES = {f"{_SUBDIR}/model.onnx": 640_691, f"{_SUBDIR}/model.onnx_data": 104_702_464}
_MODEL_NAME = f"{_SUBDIR}/model.onnx"           # 세션이 여는 파일(외부 데이터는 ORT 가 따라간다)
MODEL_DIR = app_dirs.MODELS_DIR
# normpath: _FILES 의 키는 이식성 있게 '/' 로 적었지만, ORT 에 넘기는 실제 경로는 OS 구분자로
# 정규화한다(안 하면 Windows 에서 `...\models\depth_anything_v3_small/model.onnx` 처럼 섞인다).
MODEL_PATH = os.path.normpath(app_dirs.model_path(_MODEL_NAME))

# ── 모델 관리 화면(AI Models)용 메타데이터 ──────────────────────────────────
MODEL_LABEL = "Depth masking"
MODEL_NOTE = "Distance-range masks — near/far selection (Depth Anything V3 Small)"
MODEL_FILES = list(_FILES.keys())
TOTAL_BYTES = sum(_BYTES.values())              # 105,343,155

# ── 전처리 (preprocessor_config.json 와 일치) ────────────────────────────────
# DPTImageProcessor: keep_aspect_ratio=True + size 518 + ensure_multiple_of=14(ViT 패치).
# '가능한 적게 스케일' 규칙이 결국 **짧은 변 518 맞춤**으로 귀결된다(2560×1709 → 770×518).
# ⚠️686 으로 올려도 품질 이득이 없고 추론이 2.5~3× 느려진다(실측) → 518 고정.
# 정규화 상수는 sky_seg 와 동일한 ImageNet 값이라 재사용.
INPUT_SHORT_EDGE = 518
_PATCH = 14

# ── 정제 계수 (튜닝 대상) ────────────────────────────────────────────────────
# 퍼센타일 정규화: 모델 출력은 스케일이 임의라 절대값을 못 쓴다. 상·하위 이상치를 잘라
# 슬라이더 범위(0=가장 가까움, 1=가장 멂)를 장면과 무관하게 안정화한다. **log z 공간에서** 한다.
PCT_LO, PCT_HI = 1.0, 99.0
LOG_FLOOR = 1e-4             # log(0) 발산 방지(실측 z 최소는 0.2 대라 실제로 걸리지 않음)
GUIDED_RADIUS_FRAC = 0.012   # sky_seg 와 동일 — 짧은 변 × 비율(해상도 독립)
GUIDED_EPS = 1e-3            # 깊이는 매끄러운 신호라 sky(1e-4)보다 크게 — 작으면 텍스처가 배어든다
FEATHER_MIN = 1e-3           # feather=0 에서 0-나눗셈 방지
DEFAULT_FEATHER = 0.10       # 자동 시드가 쓰는 feather(사용자가 조절)
# 자동 시드 경계 클램프 — Otsu 가 극단으로 치우쳐도 기본값이 '전부/전무'가 되지 않게.
AUTO_MIN, AUTO_MAX = 0.15, 0.85

_session_obj = None
_provider_label = None
_dl_lock = threading.Lock()
_sess_lock = threading.Lock()
_DL_TIMEOUT = 30


def _download(url, dst, progress=None, sha256=None) -> None:
    """URL → dst 청크 다운로드. sky_seg._download 와 동일 규칙(타임아웃 · Content-Length
    확인 · SHA-256 검증 · 실패 시 부분 파일 정리)."""
    try:
        h = hashlib.sha256() if sha256 else None
        with urllib.request.urlopen(url, timeout=_DL_TIMEOUT) as r, open(dst, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                if h is not None:
                    h.update(chunk)
                got += len(chunk)
                if progress is not None and total > 0:
                    progress(min(1.0, got / total))
            if total > 0 and got != total:
                raise IOError(f"incomplete download: {got}/{total} bytes from {url}")
            if h is not None and h.hexdigest() != sha256:
                raise IOError(f"sha256 mismatch: got {h.hexdigest()} expected {sha256}")
    except BaseException:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise


def model_available() -> bool:
    """다운로드 없이 확보 가능한지(사용자 디렉터리 또는 legacy 에 존재). 부작용 없음."""
    return all(app_dirs.have(n) for n in _FILES)


def is_ready() -> bool:
    return model_available()


def ensure_model(progress=None) -> str:
    """모델 파일 2개 보장(legacy 복사 or 다운로드). 세션이 열 .onnx 경로 반환.

    진행률은 **전체 파일 누적 바이트** 기준이라(caption.py 와 같은 이유) 중간에 끊겼다 재개해도
    진행 바가 뒤로 가지 않는다. 락으로 동시 다운로드 방지(워커가 겹쳐 같은 .part 를 덮어쓰는 것 차단)."""
    with _dl_lock:
        todo = [n for n in _FILES if not os.path.exists(app_dirs.model_path(n))
                and not app_dirs.materialize(n)]
        if todo:
            done = sum(_BYTES[n] for n in _FILES if n not in todo)   # 이미 확보한 몫
            for name in todo:
                path = app_dirs.model_path(name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                base = done

                def _cb(f, _b=base, _n=name):
                    if progress is not None:
                        progress(min(1.0, (_b + f * _BYTES[_n]) / TOTAL_BYTES))

                _download(f"https://huggingface.co/{_REPO}/resolve/{_REV}/{_FILES[name]}",
                          path + ".part", _cb, _SHA256[name])
                os.replace(path + ".part", path)                    # 원자적 교체
                done += _BYTES[name]
    return MODEL_PATH


def _providers(ort):
    """GPU EP 우선 → CPU 폴백. caption._providers 와 동일 방침.
    DirectML 실측(RTX 3050 Ti, V3-small): CPU 1.02s → DML 0.50s(2.0×). plain ViT + conv
    헤드라 SCUNet(swin 소형 attention 파편화로 DML 가속 불능) 케이스가 아님.
    ⚠️device_id 는 반드시 ai_denoise 캐시를 따라야 한다 — 이 머신에서 device 0(내장)이
    device 1(외장)의 약 2배 느리다(실측 0.89s vs 0.46s, V2 기준 — 비율은 V3 도 동일)."""
    avail = set(ort.get_available_providers())
    if "DmlExecutionProvider" in avail:
        dev = None
        try:
            with open(app_dirs.model_path("ai_denoise_device.json"), encoding="utf-8") as f:
                dev = int(json.load(f)["device_id"])
        except Exception:
            pass
        dml = "DmlExecutionProvider" if dev is None else ("DmlExecutionProvider", {"device_id": dev})
        return [dml, "CPUExecutionProvider"]
    if "CoreMLExecutionProvider" in avail:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def provider_label() -> str:
    """실제(세션 생성 후) 실행 장치 라벨: 'GPU' | 'CPU'."""
    return _provider_label or "CPU"


def _session():
    """캐시된 ONNX Runtime 세션. 락으로 이중 생성 방지(~105MB 세션 중복 누수 차단)."""
    global _session_obj, _provider_label
    if _session_obj is None:
        with _sess_lock:
            if _session_obj is None:
                import onnxruntime as ort
                ensure_model()
                # 세션 생성(=DML 그래프/셰이더 컴파일, 실측 ~3.6s)도 GPU 제출 — NAFNet 타일과
                # 겹치면 드라이버 크래시(GPU_LOCK 정의부 주석). CPU 폴백이어도 생성은 1회라 무해.
                with GPU_LOCK:
                    sess = ort.InferenceSession(MODEL_PATH, providers=_providers(ort))
                used = sess.get_providers()
                _provider_label = "CPU" if used[:1] == ["CPUExecutionProvider"] else "GPU"
                _session_obj = sess
    return _session_obj


def _infer_size(h: int, w: int):
    """종횡비 유지 + 짧은 변=INPUT_SHORT_EDGE, 각 변을 14의 배수(ViT 패치)로 라운딩."""
    se = min(h, w)
    s = INPUT_SHORT_EDGE / float(se) if se > 0 else 1.0
    ih = max(_PATCH, int(round(h * s / _PATCH)) * _PATCH)
    iw = max(_PATCH, int(round(w * s / _PATCH)) * _PATCH)
    return ih, iw


def _preprocess(rgb_u8: np.ndarray) -> np.ndarray:
    """RGB(H,W,3 uint8) → NCHW float32. /255 + ImageNet 정규화.
    리샘플은 preprocessor_config 의 resample=3(bicubic)에 맞춘다."""
    import cv2
    ih, iw = _infer_size(*rgb_u8.shape[:2])
    x = cv2.resize(rgb_u8, (iw, ih), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    x = (x - _MEAN) / _STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)


def infer_distance(rgb_u8: np.ndarray, guide_luma=None) -> np.ndarray:
    """추론 1회 → **거리 맵**(float32 [0,1], 입력 해상도. 0=가장 가까움, 1=가장 멂).

    guide_luma: 원본 휘도(H,W)[0,1] — 주어지면 guided filter 로 엣지 정제(권장).

    출력 `predicted_depth` 는 실제 depth z(클수록 멂)라 반전하지 않는다. **log z** 로 정규화하는
    이유는 모듈 docstring 참조(선형 z=근거리 압축, disparity=원거리 포화)."""
    sess = _session()
    inp = sess.get_inputs()[0]
    # V3 는 다중 뷰 모델이라 입력이 [B, num_images, 3, H, W] — 단일 이미지는 뷰 축 1개.
    # 출력도 4개(predicted_depth / confidence / extrinsics / intrinsics)라 이름으로 골라야 한다.
    blob = _preprocess(rgb_u8)
    if len(inp.shape) == 5:
        blob = blob[:, None]
    if provider_label() == "GPU":       # DML 동시 제출 = 드라이버 크래시(GPU_LOCK 주석)
        with GPU_LOCK:
            out = sess.run(["predicted_depth"], {inp.name: blob})
    else:
        out = sess.run(["predicted_depth"], {inp.name: blob})
    z = np.squeeze(np.asarray(out[0], dtype=np.float32))

    lz = np.log(np.maximum(z, LOG_FLOOR))
    lo, hi = np.percentile(lz, PCT_LO), np.percentile(lz, PCT_HI)
    dist = np.clip((lz - lo) / max(1e-6, float(hi - lo)), 0.0, 1.0).astype(np.float32)

    h, w = rgb_u8.shape[:2]
    dist = np.ascontiguousarray(_resize(dist, (h, w)))   # 프록시 해상도 업샘플(cv2 bilinear)
    if guide_luma is not None:
        r = max(1, int(min(h, w) * GUIDED_RADIUS_FRAC))
        dist = np.clip(_guided_filter(guide_luma, dist, r, GUIDED_EPS), 0.0, 1.0).astype(np.float32)
    return dist


def auto_range(dist: np.ndarray, feather: float = DEFAULT_FEATHER):
    """거리 맵의 분포에서 근/원 자연 경계를 찾아 **원거리 쪽** 기본 범위를 돌려준다.

    고정 상수는 원리적으로 맞을 수 없다 — 퍼센타일 정규화를 거쳐도 장면마다 분포가 크게
    다르다(실측 평균 0.478~0.679). 그래서 이미지 자신의 히스토그램에서 시드한다:
    1-D Otsu(클래스 간 분산 최대화)로 두 덩어리를 가르고, '배경만 손보기'가 가장 흔한
    의도이므로 **먼 덩어리**를 고른다(near=경계, far=1).

    반환: (near, far, feather)"""
    # 히스토그램은 4픽셀 간격 표본으로 충분하다 — 프록시 4.4Mpx 를 전부 세면 40.7ms 인데
    # stride 4(274k 표본, 256빈이면 빈당 ~1000개)면 3.4ms 이고 **경계가 완전히 동일**했다
    # (실측 6장 모두 diff 0.00000).
    d = dist[::4, ::4] if dist.ndim == 2 else dist
    hist, edges = np.histogram(d, bins=256, range=(0.0, 1.0))
    p = hist.astype(np.float64)
    total = p.sum()
    if total <= 0:
        return 0.5, 1.0, float(feather)
    p /= total
    centers = (edges[:-1] + edges[1:]) * 0.5
    w0 = np.cumsum(p)                       # 누적 가중치(경계 이하 클래스)
    m0 = np.cumsum(p * centers)             # 누적 1차 모멘트
    mt = m0[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        sigma_b = (mt * w0 - m0) ** 2 / (w0 * (1.0 - w0))   # 클래스 간 분산
    sigma_b[~np.isfinite(sigma_b)] = -1.0   # 한쪽 클래스가 빈 구간(0-나눗셈) 제외
    thr = float(centers[int(np.argmax(sigma_b))])
    # 평면적인 장면에서 Otsu 가 끝으로 치우치면 기본값이 '전부/전무'가 된다 → 클램프.
    return float(np.clip(thr, AUTO_MIN, AUTO_MAX)), 1.0, float(feather)


# ── 레이어 keys 인코딩 ──────────────────────────────────────────────────────
# 깊이 범위를 **기존 keys 목록의 한 항목**으로 인코딩한다(`depth@near,far,feather`).
# face_seg 의 `face@cx,cy` 와 같은 방식이고, 이렇게 하면 사이드카 저장·undo·재오픈 복원이
# 기존 keys 직렬화에 그대로 얹혀 새 필드가 필요 없다.
_KEY_PREFIX = "depth@"
# 켜는 순간엔 아직 거리 맵이 없어(=추론 전) 범위를 정할 수 없다 → 센티넬을 넣어두고
# 워커가 맵을 만든 뒤 auto_range 로 확정, Controller 가 실제 값 키로 교체한다.
AUTO = "auto"


def range_from_keys(keys):
    """keys → (near, far, feather) | AUTO('auto' 센티넬) | None(깊이 선택 없음)."""
    for k in keys:
        s = str(k)
        if not s.startswith(_KEY_PREFIX):
            continue
        payload = s[len(_KEY_PREFIX):]
        if payload == AUTO:
            return AUTO
        try:
            near, far, feather = (float(v) for v in payload.split(","))
        except ValueError:
            continue                    # 손상된 사이드카 — 조용히 건너뛴다
        return near, far, feather
    return None


def range_key(near: float, far: float, feather: float) -> str:
    """(near, far, feather) → keys 항목. 소수 4자리로 고정 — 문자열이 곧 캐시 키라
    같은 슬라이더 위치가 항상 같은 key 를 만들어야 setMaskClasses 의 no-op 이 동작한다."""
    return f"{_KEY_PREFIX}{float(near):.4f},{float(far):.4f},{float(feather):.4f}"


def compose_mask(dist: np.ndarray, near: float, far: float, feather: float) -> np.ndarray:
    """거리 맵 → near..far 밴드 soft alpha(float32 [0,1]). 추론 없이 빠르게 재조합.

    양 끝을 feather 폭 smoothstep 으로 진입/이탈시킨다. near<=0 이면 하한 없음,
    far>=1 이면 상한 없음(= 근거리 전부 / 원거리 전부 선택)."""
    f = max(FEATHER_MIN, float(feather))
    near, far = float(near), float(far)
    if far < near:
        near, far = far, near

    if near <= 0.0:
        m = np.ones_like(dist)
    else:
        m = _smoothstep(near - f, near + f, dist)
    if far < 1.0:
        m = m * (1.0 - _smoothstep(far - f, far + f, dist))
    return np.clip(m, 0.0, 1.0, out=m)


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    """t*t*(3-2t). 프록시 전체(4.4M px)라 임시 배열을 아끼려 in-place 로 접는다.
    ⚠️순서 주의 — t 를 먼저 제곱하면 뒤의 (3-2t) 가 (3-2t²) 가 된다."""
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    s = 3.0 - 2.0 * t          # t 를 덮어쓰기 전에 계산
    t *= t
    t *= s
    return t
