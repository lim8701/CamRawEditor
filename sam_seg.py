# -*- coding: utf-8 -*-
"""클릭 선택 세그멘테이션 — SlimSAM-77 ONNX (Xenova/slimsam-77-uniform, Apache-2.0).

이미지 위 한 점을 클릭하면 그 피사체의 픽셀 정밀 마스크를 만든다(Segment Anything 계열).
Florence-2 세그(폴리곤, 거침)를 대체 — SAM 은 단일 forward pass 라 빠르고 경계가 깔끔하다.
결과 마스크는 하늘 마스크와 동일 파이프라인(binding9 로컬 조정)으로 흐른다.

numpy + scipy + onnxruntime 만 사용(Qt 독립). 공개 계약은 두 묶음이다:
  · 모델 다운로드 3종 is_ready() / ensure_model(progress) / provider_label() — caption.py 와 동형.
    ⚠️sky_seg.py 와 같다고 읽지 말 것: 그쪽은 ensure_model 만 같고 is_ready 대신 model_available
    이며 provider_label 이 없다(되살릴 때 없는 함수를 찾게 된다).
  · 세그 2단 encode(rgb) / decode(enc, points, labels) — SAM 고유(아래 인코더 주석).
모델(~40MB, 2파일)은 app_dirs.MODELS_DIR 에 런타임 다운로드(번들 금지). onnx 는 SHA-256 검증.

인코더는 이미지당 1회(비쌈) → encode() 결과를 호출측이 캐시하고, 클릭마다 decode() 만 재실행
(sky_seg.infer_softmax/compose_mask 분리와 동형).
"""
import hashlib
import os
import threading
import urllib.request

import numpy as np
from scipy.ndimage import zoom

import app_dirs

# Xenova/slimsam-77-uniform 커밋 고정. onnx 는 다운로드 후 SHA-256 검증(네이티브 파서 전 변조 차단).
_REV = "5850ab45f587c112167512ffef949107115e26a0"
_REPO = f"https://huggingface.co/Xenova/slimsam-77-uniform/resolve/{_REV}"
_FILES = {
    "slimsam_vision_encoder.onnx": "onnx/vision_encoder.onnx",
    "slimsam_decoder.onnx": "onnx/prompt_encoder_mask_decoder.onnx",
}
_SHA256 = {
    "onnx/vision_encoder.onnx": "9f8433273a6750b587779baa0cf5508111001bf7e7acfcf585d370139fd366d0",
    "onnx/prompt_encoder_mask_decoder.onnx": "f4514391764fbd56e08e119060d874ecd7d52994bfb1968af159e12d4943b5bb",
}
_TOTAL_BYTES = 40_000_000
_DL_TIMEOUT = 30
_EDGE = 1024                         # SamImageProcessor: longest_edge 1024, 1024x1024 패딩
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)

_dl_lock = threading.Lock()
_sess_lock = threading.Lock()
_state = None                        # (enc_session, dec_session) 캐시


def _path(name: str) -> str:
    return os.path.join(app_dirs.MODELS_DIR, name)


def is_ready() -> bool:
    """모델 파일 확보 가능 여부(부작용 없음 — UI 스레드 안전)."""
    return all(app_dirs.have(n) for n in _FILES)


def provider_label() -> str:
    return "CPU"                     # 라이브 셰이더(GPU) 경합 회피 위해 CPU 고정 — 소형이라 충분히 빠름


def ensure_model(progress=None) -> None:
    """모델 파일 보장(legacy 복사 or 다운로드, ~40MB). onnx 는 SHA-256 검증. 원자적 tmp→rename."""
    with _dl_lock:
        done = sum(os.path.getsize(_path(n)) for n in _FILES if os.path.exists(_path(n)))
        for name, rel in _FILES.items():
            dst = _path(name)
            if os.path.exists(dst):
                continue
            if app_dirs.materialize(name):
                done += os.path.getsize(dst)
                if progress is not None:
                    progress(min(1.0, done / _TOTAL_BYTES))
                continue
            os.makedirs(app_dirs.MODELS_DIR, exist_ok=True)
            tmp = dst + ".part"
            try:
                want = _SHA256.get(rel)
                h = hashlib.sha256() if want else None
                with urllib.request.urlopen(f"{_REPO}/{rel}", timeout=_DL_TIMEOUT) as r, \
                        open(tmp, "wb") as f:
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
                        done += len(chunk)
                        if progress is not None:
                            progress(min(1.0, done / _TOTAL_BYTES))
                    if total > 0 and got != total:
                        raise IOError(f"incomplete download: {got}/{total} ({rel})")
                    if h is not None and h.hexdigest() != want:
                        raise IOError(f"sha256 mismatch ({rel})")
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            os.replace(tmp, dst)
        if progress is not None:
            progress(1.0)


def _load():
    """enc/dec 세션 lazy 로드(1회, CPU). caption._load_state 패턴."""
    global _state
    if _state is not None:
        return _state
    with _sess_lock:
        if _state is not None:
            return _state
        ensure_model()
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        prov = ["CPUExecutionProvider"]
        enc = ort.InferenceSession(_path("slimsam_vision_encoder.onnx"), opts, providers=prov)
        dec = ort.InferenceSession(_path("slimsam_decoder.onnx"), opts, providers=prov)
        _state = (enc, dec)
        return _state


def encode(rgb_u8: np.ndarray) -> dict:
    """프록시 RGB(H,W,3 uint8) → 인코더 임베딩 + 좌표 메타. 이미지당 1회(호출측 캐시).
    전처리: longest edge 1024 리사이즈(비율 유지) → ImageNet 정규화 → 1024x1024 우/하 0패딩."""
    enc, _ = _load()
    H, W = rgb_u8.shape[:2]
    scale = _EDGE / max(H, W)
    small = zoom(rgb_u8.astype(np.float32) / 255.0, (scale, scale, 1), order=1)
    rh, rw = small.shape[:2]                      # zoom 반올림 반영 실제 크기
    small = (small - _MEAN) / _STD
    pad = np.zeros((_EDGE, _EDGE, 3), np.float32)
    pad[:rh, :rw] = small
    px = pad.transpose(2, 0, 1)[None]
    outs = dict(zip([o.name for o in enc.get_outputs()], enc.run(None, {"pixel_values": px})))
    return {"emb": outs["image_embeddings"], "pos": outs["image_positional_embeddings"],
            "scale": scale, "rw": rw, "rh": rh, "H": H, "W": W}


def decode(enc_state: dict, points, labels=None) -> np.ndarray:
    """encode() 결과 + 클릭 점 목록(프록시 좌표) → 알파 마스크(H,W float32 0/1).
    points: [(x,y), ...] 프록시 픽셀. labels: 1=포함/0=제외(기본 전부 1). 멀티마스크 3개 중 iou 최고."""
    _, dec = _load()
    pts = [(float(x), float(y)) for x, y in points]
    if not pts:
        return np.zeros((enc_state["H"], enc_state["W"]), np.float32)
    s = enc_state["scale"]
    p = np.array([[[[x * s, y * s] for x, y in pts]]], np.float32)     # (1,1,N,2) 1024 공간
    lab = np.array([[labels if labels is not None else [1] * len(pts)]], np.int64)  # (1,1,N)
    out = dict(zip([o.name for o in dec.get_outputs()],
                   dec.run(None, {"input_points": p, "input_labels": lab,
                                  "image_embeddings": enc_state["emb"],
                                  "image_positional_embeddings": enc_state["pos"]})))
    iou = out["iou_scores"][0, 0]
    best = int(np.argmax(iou))
    low = out["pred_masks"][0, 0, best]                               # (256,256) logits
    rh, rw, H, W = enc_state["rh"], enc_state["rw"], enc_state["H"], enc_state["W"]
    vh = max(1, round(rh / 4)); vw = max(1, round(rw / 4))            # 256=1024/4, 유효(비패딩) 영역
    crop = low[:vh, :vw]
    full = zoom(crop, (H / vh, W / vw), order=1)                      # 프록시 해상도로
    return (full > 0.0).astype(np.float32)                           # SAM logit>0 = 전경
