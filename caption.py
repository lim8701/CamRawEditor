# -*- coding: utf-8 -*-
"""사진 영어 캡션 생성 — Florence-2-base-ft ONNX (MIT).

RAW 내장 JPEG 프리뷰(호출측이 768x768 RGB 로 축소해 전달)를 입력으로
영어 캡션 문장을 생성한다. numpy + onnxruntime 만 사용(Qt 독립).

- 모델: onnx-community/Florence-2-base-ft (fp32 4파일 ~1.1GB) — git 미커밋,
  최초 사용 시 ensure_model() 이 Hugging Face 에서 자동 다운로드(원자적 tmp→rename).
  파일명은 전부 `florence2_` 접두사(.gitignore 의 `models/florence2_*` 커버).
- 토크나이저: vocab.json + merges.txt 의 GPT-2식 byte-level BPE 직접 구현
  (BART 토크나이저 호환, 의존성 추가 없음).
- 생성: greedy + 공식 generation_config 의 no_repeat_ngram_size(3)·forced_bos —
  반복 금지가 없으면 문단 캡션이 같은 문장 반복으로 퇴화함(실측). 공식 기본은
  beam=3 이지만 무캐시 디코더로는 ~19s 라 미적용(KV-cache 전환 시 재고).
  무캐시 디코더 실측 CPU: 비전 ~1.3s + 짧은 캡션 ~1.4s, 문단 ~5s.
- ⚠️한계: 사람 수 등 '세기(counting)'는 이 크기(0.23B) VLM 의 알려진 약점 —
  4명을 5명으로 등 ±1 오류 빈번(빔에서도 동일 = 디코딩으로 못 고침). 수를 잘
  언급하지 않는 짧은 캡션(기본값)이 실질 노출이 적다.
"""
import hashlib
import json
import os
import re
import threading
import urllib.request

import numpy as np

import app_dirs

# 이동 참조(main) 대신 커밋 리비전 고정. onnx 파일은 다운로드 후 SHA-256 검증(HF LFS oid)
# — 업스트림 변조된 .onnx 가 조용히 ort.InferenceSession(네이티브 파서, RCE 표면)에 넘어가는
# 것 방지. 모델 업그레이드 시 리비전 + _SHA256 을 함께 갱신한다. (json/txt 는 Python 파싱이라
# 저위험 — 리비전 고정으로 드리프트만 막고 해시 검증은 생략.)
_REPO = "https://huggingface.co/onnx-community/Florence-2-base-ft/resolve/e88a44eaf3791a35eae0c5a47b3dbcd36e67eb6f"
_SHA256 = {
    "onnx/vision_encoder.onnx": "d67258cdfdebfa21285dad9e7bd4bd99725236d0aaef9e474a1b24a6ec471351",
    "onnx/embed_tokens.onnx":   "90cae3deb6406938c676a35b5246db02b478c9cc8cf93508361be80c05babf95",
    "onnx/encoder_model.onnx":  "cb0bccc232c64290397f5e1235eb3e1fa6ccf8c5afed9216480ee4eed80737fc",
    "onnx/decoder_model.onnx":  "16b40ea746ea09802a549be74c2eedc937c76025a1ea9baa040617ba0605306d",
}
# 저장 위치: 항상 OS 사용자 데이터 디렉터리(app_dirs — 버전/실행환경 무관 유지).
# 예전 위치(구버전 frozen lib/models, dev 저장소 models/)에 받아둔 파일은
# ensure_model 이 재다운로드 대신 복사(app_dirs.materialize).
MODEL_DIR = app_dirs.MODELS_DIR

# 로컬 파일명 -> repo 상대경로. onnx 4개 + 토크나이저/설정 4개.
_FILES = {
    "florence2_vision_encoder.onnx": "onnx/vision_encoder.onnx",
    "florence2_embed_tokens.onnx": "onnx/embed_tokens.onnx",
    "florence2_encoder_model.onnx": "onnx/encoder_model.onnx",
    "florence2_decoder_model.onnx": "onnx/decoder_model.onnx",
    "florence2_vocab.json": "vocab.json",
    "florence2_merges.txt": "merges.txt",
    "florence2_preprocessor_config.json": "preprocessor_config.json",
    "florence2_generation_config.json": "generation_config.json",
}
_TOTAL_BYTES = 1_090_000_000     # 진행률 표시용 대략 총량(fp32 4파일 합)

# ── 모델 관리 화면(AI Models)용 메타데이터 — sky_seg 와 동일 계약 ────────────
MODEL_LABEL = "Photo caption"
MODEL_NOTE = "English captions → hashtags and folder search (Florence-2 base-ft). Opt-in only"
MODEL_FILES = list(_FILES.keys())
TOTAL_BYTES = _TOTAL_BYTES
_DL_TIMEOUT = 30                 # 소켓 읽기 타임아웃(초)

# 캡션 상세도(Florence-2 태스크) -> 프롬프트 (processing_florence2.py 의 매핑과 동일)
TASKS = {
    "<CAPTION>": "What does the image describe?",
    "<DETAILED_CAPTION>": "Describe in detail what is shown in the image.",
    "<MORE_DETAILED_CAPTION>": "Describe with a paragraph what is shown in the image.",
}
INPUT_EDGE = 768                 # 모델 입력 크기(preprocessor_config 와 동일, 고정)

_dl_lock = threading.Lock()
_sess_lock = threading.Lock()
_state = None                    # (sessions dict, Bpe, gen config) 캐시


def _path(name: str) -> str:
    return os.path.join(MODEL_DIR, name)


def is_ready() -> bool:
    """모든 모델/토크나이저 파일을 확보 가능한지(새 경로 or 구버전 폴더 — 둘 다 없으면
    첫 사용 시 대용량 다운로드가 필요하다는 뜻). 부작용 없음 — UI 스레드 안전."""
    return all(app_dirs.have(n) for n in _FILES)


def ensure_model(progress=None) -> None:
    """모델 파일 보장(구버전 폴더에서 복사 or 다운로드, 총 ~1.1GB). progress(0..1) 콜백
    옵션. 락으로 동시 다운로드 방지. 복사/다운로드 모두 원자적 tmp→rename(부분파일 방지)."""
    with _dl_lock:
        done = sum(os.path.getsize(_path(n)) for n in _FILES if os.path.exists(_path(n)))
        for name, rel in _FILES.items():
            dst = _path(name)
            if os.path.exists(dst):
                continue
            if app_dirs.materialize(name):     # 구버전 폴더 복사(재다운로드 생략)
                done += os.path.getsize(dst)
                if progress is not None:
                    progress(min(1.0, done / _TOTAL_BYTES))
                continue
            os.makedirs(MODEL_DIR, exist_ok=True)
            tmp = dst + ".part"
            try:
                # 소켓 타임아웃 — 멈춘 연결이 워커/락을 영구 점유하지 않게(_DL_TIMEOUT).
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
                        # 짧은 read/CDN 절단/200 에러본문이 성공으로 위장돼 승격되면
                        # 이후 세션 로드가 매번 실패(수동 삭제 전까지 영구 불능)한다.
                        raise IOError(f"incomplete download: {got}/{total} bytes ({rel})")
                    if h is not None and h.hexdigest() != want:
                        # 고정 리비전과 다른 onnx 내용(변조/교체) → 네이티브 파서 전에 차단.
                        raise IOError(f"sha256 mismatch ({rel}): {h.hexdigest()} != {want}")
            except BaseException:
                try:
                    os.remove(tmp)             # 실패 시 부분 파일 정리(잔류물 방지)
                except OSError:
                    pass
                raise
            os.replace(tmp, dst)
        if progress is not None:
            progress(1.0)


# ---------------- GPT-2 byte-level BPE (BART 토크나이저 호환) ----------------
def _bytes_to_unicode():
    bs = (list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1))
          + list(range(ord("®"), ord("ÿ") + 1)))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


class _Bpe:
    # 프롬프트는 ASCII 고정 문장 → GPT-2 정규식의 ASCII 근사로 충분
    _PAT = re.compile(
        r"'s|'t|'re|'ve|'m|'ll|'d| ?[A-Za-z]+| ?[0-9]+| ?[^\sA-Za-z0-9]+|\s+(?!\S)|\s+")

    def __init__(self, vocab_path, merges_path):
        with open(vocab_path, encoding="utf-8") as f:
            self.enc = json.load(f)
        self.dec = {v: k for k, v in self.enc.items()}
        # HF 규약: 첫 줄(#version 헤더)만 건너뛰고 나머지는 모두 병합 규칙.
        # ⚠️`#`-시작 라인 전체를 버리면 `##`/`###` 토큰을 만드는 정당한 규칙까지 사라져
        #   임의 텍스트가 off-distribution 으로 토큰화됨(고정 프롬프트엔 무해했으나 정정).
        lines = [ln for ln in open(merges_path, encoding="utf-8").read().split("\n") if ln]
        if lines and lines[0].startswith("#version"):
            lines = lines[1:]
        self.ranks = {tuple(m.split()): i for i, m in enumerate(lines)}
        self.b2u = _bytes_to_unicode()
        self.u2b = {v: k for k, v in self.b2u.items()}
        self.cache = {}

    def _bpe(self, tok):
        if tok in self.cache:
            return self.cache[tok]
        word = list(tok)
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda p: self.ranks.get(p, 1 << 30))
            if best not in self.ranks:
                break
            a, b = best
            out, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == a and word[i + 1] == b:
                    out.append(a + b)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = out
        self.cache[tok] = word
        return word

    def encode(self, text):
        ids = []
        for tok in self._PAT.findall(text):
            u = "".join(self.b2u[b] for b in tok.encode("utf-8"))
            # vocab 밖 서브워드는 건너뜀(KeyError 방지) — 고정 프롬프트엔 없지만 임의 입력 대비.
            ids.extend(tid for p in self._bpe(u)
                       if (tid := self.enc.get(p)) is not None)
        return ids

    def decode(self, ids, skip=()):
        parts = [self.dec.get(int(i)) for i in ids
                 if i not in skip and self.dec.get(int(i)) is not None]
        data = bytes(self.u2b.get(ch, ord(" ")) for ch in "".join(parts))
        return data.decode("utf-8", errors="replace")


_provider_label = None   # 세션 생성 후 실제 EP: "GPU" | "CPU"
_state_cpu = None        # CPU 전용 세션(배치 인덱싱용 — GPU VRAM 경합 크래시 방지)


def _providers(ort):
    """GPU EP 우선(DirectML 최속 디바이스/CoreML) → CPU 폴백. ai_denoise 와 동일 방침.
    DirectML 실측(Florence-2, RTX 3050 Ti): 비전 4.8×·디코더 2.2× 가속(SCUNet 케이스 아님)."""
    avail = set(ort.get_available_providers())
    if "DmlExecutionProvider" in avail:
        dev = None
        try:                                  # ai_denoise 가 실측·캐시한 최속 device 재사용(머신 공용)
            import app_dirs
            with open(app_dirs.model_path("ai_denoise_device.json"), encoding="utf-8") as f:
                dev = int(json.load(f)["device_id"])
        except Exception:
            pass
        dml = "DmlExecutionProvider" if dev is None else ("DmlExecutionProvider", {"device_id": dev})
        return [dml, "CPUExecutionProvider"]
    if "CoreMLExecutionProvider" in avail:    # macOS 표준 휠(속도는 기기별 — Mac 실측 필요)
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def provider_label() -> str:
    """실제(세션 생성 후) 실행 장치 라벨: 'GPU' | 'CPU' (생성 전엔 'CPU' 가정)."""
    return _provider_label or "CPU"


def _load_state(cpu: bool = False):
    """세션/토크나이저 lazy 로드(1회). cpu=True 면 **CPU 전용 세션**(폴더 배치 인덱싱용 —
    GPU 는 프리뷰/편집 전용으로 두어 DirectML VRAM 경합 크래시를 원천 차단). GPU/CPU 세션은
    각각 캐시(_state / _state_cpu). 파일이 없으면 ensure_model 로 먼저 확보."""
    global _state, _state_cpu, _provider_label
    cur = _state_cpu if cpu else _state
    if cur is not None:
        return cur
    with _sess_lock:
        cur = _state_cpu if cpu else _state
        if cur is not None:
            return cur
        ensure_model()
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        # cpu=True → CPU 강제(배치). 아니면 GPU EP(DirectML/CoreML) 우선 → CPU 폴백.
        prov = ["CPUExecutionProvider"] if cpu else _providers(ort)

        def sess(name):
            return ort.InferenceSession(_path(name), opts, providers=prov)

        sessions = {
            "vis": sess("florence2_vision_encoder.onnx"),
            "emb": sess("florence2_embed_tokens.onnx"),
            "enc": sess("florence2_encoder_model.onnx"),
            "dec": sess("florence2_decoder_model.onnx"),
        }
        bpe = _Bpe(_path("florence2_vocab.json"), _path("florence2_merges.txt"))
        with open(_path("florence2_generation_config.json"), encoding="utf-8") as f:
            gen = json.load(f)
        st = (sessions, bpe, gen)
        ep = sessions["vis"].get_providers()[0]
        print(f"[caption] EP={ep}{' (cpu-forced/batch)' if cpu else ''}")
        if cpu:
            _state_cpu = st
            if _provider_label is None:   # GPU 세션이 아직 없어도 가용 EP로 라벨 추정(표시 정확도)
                avail = set(ort.get_available_providers())
                _provider_label = ("GPU" if ("DmlExecutionProvider" in avail
                                              or "CoreMLExecutionProvider" in avail) else "CPU")
        else:
            _state = st                   # 실제 GPU 세션 = 권위 있는 라벨(추정값 덮어씀)
            _provider_label = ("GPU" if ep in ("DmlExecutionProvider", "CoreMLExecutionProvider")
                               else "CPU")
        return st


def generate(rgb: np.ndarray, task: str = "<CAPTION>", max_new_tokens: int = 120,
             cpu: bool = False) -> str:
    """768x768x3 uint8 RGB(정방향) -> 영어 캡션 문장.

    호출측(main.py)이 내장 JPEG 를 EXIF 회전 반영 후 768x768 로 축소해 전달
    (IgnoreAspectRatio — Florence-2 processor 와 동일하게 비율 무시 스케일).
    """
    if rgb.shape != (INPUT_EDGE, INPUT_EDGE, 3):
        raise ValueError(f"expected {INPUT_EDGE}x{INPUT_EDGE}x3, got {rgb.shape}")
    prompt = TASKS[task]
    sessions, bpe, gen = _load_state(cpu)
    vis, emb, enc, dec = (sessions[k] for k in ("vis", "emb", "enc", "dec"))
    bos = gen.get("bos_token_id", 0)
    eos = gen.get("eos_token_id", 2)
    pad = gen.get("pad_token_id", 1)
    dec_start = gen.get("decoder_start_token_id", 2)
    forced_bos = gen.get("forced_bos_token_id")          # 공식 설정: 첫 토큰 = <s>
    ngram_n = int(gen.get("no_repeat_ngram_size", 0))    # 공식 설정: 3(반복 퇴화 방지)

    # 전처리: ImageNet 정규화 (preprocessor_config 기본값)
    arr = rgb.astype(np.float32) / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406], np.float32)) \
        / np.array([0.229, 0.224, 0.225], np.float32)
    px = arr.transpose(2, 0, 1)[None]

    img_feat = vis.run(None, {"pixel_values": px})[0]
    ids = [bos] + bpe.encode(prompt) + [eos]
    txt_emb = emb.run(None, {"input_ids": np.array([ids], np.int64)})[0]
    merged = np.concatenate([img_feat, txt_emb], axis=1).astype(np.float32)
    mask = np.ones(merged.shape[:2], np.int64)
    enc_out = enc.run(None, {"inputs_embeds": merged, "attention_mask": mask})[0]

    dec_ids = [dec_start]
    for step in range(max_new_tokens):
        if step == 0 and forced_bos is not None:
            dec_ids.append(int(forced_bos))              # forced_bos_token_id
            continue
        d_emb = emb.run(None, {"input_ids": np.array([dec_ids], np.int64)})[0]
        logits = dec.run(None, {"inputs_embeds": d_emb,
                                "encoder_hidden_states": enc_out,
                                "encoder_attention_mask": mask})[0]
        row = logits[0, -1]
        # no_repeat_ngram: 기존 n-gram 을 반복하게 될 토큰 금지(문단 반복 퇴화 방지)
        if ngram_n and len(dec_ids) >= ngram_n:
            prefix = tuple(dec_ids[-(ngram_n - 1):])
            for i in range(len(dec_ids) - ngram_n + 1):
                if tuple(dec_ids[i:i + ngram_n - 1]) == prefix:
                    row[dec_ids[i + ngram_n - 1]] = -np.inf
        nxt = int(np.argmax(row))
        dec_ids.append(nxt)
        if nxt == eos and len(dec_ids) > 2:
            break
    return bpe.decode(dec_ids, skip={bos, eos, pad, dec_start}).strip()
