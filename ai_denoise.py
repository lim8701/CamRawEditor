"""AI 디노이즈 엔진 (ONNX / NAFNet-SIDD width32).

NAFNet(megvii, MIT)의 실카메라 노이즈(SIDD 학습) 모델로 중성 display sRGB 이미지를
디노이즈한다. PySide6/QML 비의존 — numpy in/out 독립 모듈(프리뷰 nrBase 워커와
export 파이프라인이 공유).

⚠️ 모델 선택 이력(재조사 방지): 처음엔 SCUNet(Apache-2.0, 순수 합성 학습)을 채택했으나
swin attention(수백 개 소형 Transpose/Gather)이 DirectML 에서 가속 불능으로 판명
(RTX 3050 Ti 실측: SCUNet DML 4.5~82초/타일 vs CPU 5초 — 분할 아님, 커널 자체가 느림).
conv 전용 NAFNet 은 동일 GPU 에서 146ms/타일(35×). 품질은 SIDD(실촬영) 학습이라 실사진
노이즈에 적합하되 인공 가우시안엔 SCUNet 보다 보수적.

용법: denoise_rgb(rgb[0,1]) → 디노이즈드 RGB. luma 는 휘도 NR 베이스(가이디드 필터 자리),
chroma 는 컬러 NR 베이스(기존 큰반경 블러 자리 — 색얼룩 제거가 AI 체감의 핵심)로 쓰인다
(셰이더 nrBase RGBA 텍스처 + nrChroma 게이트 / pipeline 동일 수식). 노이즈 성분을 중성
베이스에서 1회 추출하고 슬라이더(lumaNR/colorNR)는 혼합비만 조절하므로 재추론 불필요.

타일링: ONNX 는 고정 512×512 로 export(고정 크기가 EP 최적화에도 유리). OVERLAP 겹침 +
경사(ramp) 가중 블렌딩으로 이음매 제거. 입력 크기 임의 OK(작으면 reflect 패딩).

모델(~117MB)은 번들하지 않고 최초 사용 시 자동 다운로드(models/ 캐시) — sky_seg 와 동일
방침(재배포 대신 원 출처 유지, models/README.md 고지).
"""

import json
import os
import sys
import threading
import time
import urllib.request

import numpy as np

import app_dirs

# 프로젝트 GitHub Releases 의 모델 전용 태그에 업로드된 자체 변환본(NAFNet 공식 가중치
# NAFNet-SIDD-width32.pth 를 그대로 ONNX 로 변환 — 값 무변경, torch↔ort 오차 ~1e-5).
_MODEL_URL = ("https://github.com/lim8701/FilmRawstery/releases/download/"
              "models-v1/nafnet_sidd_width32_512.onnx")
# 저장 위치: 항상 OS 사용자 데이터 디렉터리(app_dirs — 버전/실행환경 무관 유지).
# 예전 위치(구버전 frozen lib/models, dev 저장소 models/)에 받아둔 파일은
# ensure_model 이 재다운로드 대신 복사(app_dirs.materialize).
_MODEL_NAME = "nafnet_sidd_width32_512.onnx"
MODEL_DIR = app_dirs.MODELS_DIR
MODEL_PATH = app_dirs.model_path(_MODEL_NAME)

# ── 모델 관리 화면(AI Models)용 메타데이터 — sky_seg 와 동일 계약 ────────────
MODEL_LABEL = "AI denoise"
MODEL_NOTE = "High-ISO noise reduction (NAFNet-SIDD w32). GPU (DirectML / CoreML) if available, else CPU"
MODEL_FILES = [_MODEL_NAME]
TOTAL_BYTES = 117_090_403

TILE = 512      # ONNX export 고정 입력 크기(변경 시 재-export 필요)
OVERLAP = 64    # 타일 겹침(px) — 경계 컨텍스트 확보 + 램프 블렌딩 폭
# 모델 드리프트 제거 반경(프록시 px, 가우시안 σ). NAFNet 은 노이즈 외에 넓은 영역의
# 색/밝기도 미세하게 바꾼다(실측: 저주파 chroma 평균 0.26%·국소 5%, luma 최대 1.4%) —
# 그대로 빼면 colorNR 이 화면 색감을 통째로 옮긴다(사용자 확인). 디노이즈드 결과에 원본의
# 저주파를 복원해 고주파(노이즈 스케일) 차이만 남긴다. 호출측이 해상도에 맞게 스케일
# (export 는 ×(full/proxy)).
# ⚠️ 값은 기존(비 AI) 컬러 NR 의 블러 반경 sigma_cla(=7, pipeline/셰이더 claBlur)와 동일하게
# 유지 — 그래야 AI/기존이 '같은 주파수 대역'만 조작해 색감 이동 체감이 기존과 같거나 적다.
# 16 이었을 때 7~16px 스케일의 색까지 빼서 기존 대비 색감이 미세하게 변했음(사용자 확인).
DRIFT_SIGMA = 7.0

# 타일 사이 양보 시간(초) — 추론이 GPU(또는 CPU 코어)를 연속 점유하면 앱 UI 렌더가
# 끼어들 틈이 없어 버벅인다(사용자 확인). 타일마다 잠깐 쉬어 프레임이 그려질 창을 준다.
# GPU 타일 ~150ms 기준 duty ~83%: 프리뷰 24타일에 +0.7s, export 140타일에 +4s 수준.
UI_PACE = 0.03

# 타일 발산 게이트: NAFNet 은 극암부·저대비(거의 균일한 luma≲0.05) 타일에서 발산해 줄무늬
# 패턴을 생성한다(DSCF8018 좌하단 실측: 평균 |출력−입력| 41%, 최대 169% — CPU/DML 동일이라
# 모델 자체 문제. SIDD 학습 분포에 이런 입력이 없음). 정상 타일의 평균 보정량은 ISO4000
# 실측 0.7~1.2%, 초고ISO 라도 ~4% 수준 → 발산(41%)과의 사이 0.08 로 게이트. 넘으면 그
# 타일은 모델 출력을 버리고 입력 유지(점 노이즈 등 국소 대형 보정은 '평균'에 안 걸려 보존).
DIVERGE_MEAN = 0.08

# GPU 가속 실행 프로바이더(존재하면 우선 사용, 실패 시 CPU 폴백).
#  - DirectML: Windows + onnxruntime-directml 패키지(DX12 GPU면 내장 그래픽도 동작)
#  - CoreML: macOS 표준 onnxruntime 에 포함
_ACCEL_PROVIDERS = ("DmlExecutionProvider", "CoreMLExecutionProvider")

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
_session_obj = None
_provider_label = None      # 세션 생성 후 실제 사용 EP: "GPU" | "CPU"


class Cancelled(Exception):
    """타일 루프 중단(이미지 전환/토글 해제). 호출측이 잡아서 조용히 폐기."""


def model_available() -> bool:
    # legacy(구버전 폴더)에만 있어도 True — 다운로드 없이 확보 가능(ensure 시 복사)
    return app_dirs.have(_MODEL_NAME)


def _dml_dll_present() -> bool:
    """onnxruntime 패키지 폴더에 DirectML.dll 존재 여부(= onnxruntime-directml 설치).
    개발 venv 와 PyInstaller 번들(lib/onnxruntime/capi/) 모두 커버."""
    cands = []
    try:
        import importlib.util
        spec = importlib.util.find_spec("onnxruntime")
        for loc in (spec.submodule_search_locations or []) if spec else []:
            cands.append(os.path.join(str(loc), "capi", "DirectML.dll"))
    except Exception:
        pass
    base = getattr(sys, "_MEIPASS", None)          # frozen: 번들 콘텐츠 디렉터리
    if base:
        cands.append(os.path.join(base, "onnxruntime", "capi", "DirectML.dll"))
    return any(os.path.exists(p) for p in cands)


def gpu_available() -> bool:
    """GPU 가속 EP 존재 여부 — 토글 시 QML 이 메인 스레드에서 부르므로 **onnxruntime 을
    import 하지 않고**(DLL ~40MB 로드에 수 초 → 첫 토글 프리즈의 원인이었음) 파일/플랫폼
    검사만으로 판단. 실제 장치 초기화는 워커의 세션 생성에서 하고 실패 시 CPU 폴백."""
    if sys.platform == "darwin":
        return True                                 # 표준 macOS 휠에 CoreML EP 포함
    if sys.platform == "win32":
        return _dml_dll_present()
    return False


def provider_label() -> str:
    """실제(세션 생성 후) 또는 예상(생성 전) 실행 장치 라벨: 'GPU' | 'CPU'."""
    if _provider_label is not None:
        return _provider_label
    return "GPU" if gpu_available() else "CPU"


_dl_lock = threading.Lock()
_sess_lock = threading.Lock()


_DL_TIMEOUT = 30    # 소켓 읽기 타임아웃(초) — 멈춘 연결이 워커/락을 영구 점유하는 것 방지


def _download(url, dst, progress=None) -> None:
    """URL → dst 청크 다운로드(원자적 아님 — 호출측이 .part→rename). 소켓 타임아웃 적용,
    실패 시 부분 파일(dst) 정리. progress(0..1) 콜백 옵션."""
    try:
        with urllib.request.urlopen(url, timeout=_DL_TIMEOUT) as r, open(dst, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress is not None and total > 0:
                    progress(min(1.0, got / total))
            if total > 0 and got != total:
                # 짧은 read/CDN 절단/200 에러본문이 성공으로 위장돼 최종 파일명으로
                # 승격되면 이후 세션 생성이 매번 실패(수동 삭제 전까지 영구 불능)한다.
                raise IOError(f"incomplete download: {got}/{total} bytes from {url}")
    except BaseException:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise


def ensure_model(progress=None) -> str:
    """모델 파일 보장(없으면 다운로드, ~117MB). progress(0..1) 콜백 옵션. 경로 반환.
    락으로 동시 다운로드 방지(이미지 전환 등으로 워커 두 개가 겹칠 때 .part 충돌 방지)."""
    with _dl_lock:
        if not os.path.exists(MODEL_PATH) and not app_dirs.materialize(_MODEL_NAME):
            os.makedirs(MODEL_DIR, exist_ok=True)
            tmp = MODEL_PATH + ".part"
            _download(_MODEL_URL, tmp, progress)   # 타임아웃 + 실패 시 .part 정리
            os.replace(tmp, MODEL_PATH)    # 원자적 교체(부분파일 방지)
    return MODEL_PATH


# GPU 프로빙 캐시(머신 전용) — 모델과 같은 곳에 두어 업데이트에도 유지(재프로빙 방지).
_DEVICE_NAME = "ai_denoise_device.json"
_DEVICE_CACHE = app_dirs.model_path(_DEVICE_NAME)


def _probe_dml_device(ort, fresh=False):
    """DML 디바이스 0..3 을 1회 추론으로 실측해 가장 빠른 device_id 반환(없으면 None).
    듀얼 GPU 노트북은 기본(0)이 느린 쪽일 수 있고 편차가 큼(실측 dev0 485ms vs dev1 146ms).
    결과는 json 으로 캐시(1회 프로빙 ~수 초). fresh=True 면 캐시 무시하고 재실측
    (드라이버/어댑터 변경으로 캐시된 device_id 가 무효해진 경우 — _session 이 감지)."""
    import time
    if not fresh:
        try:
            app_dirs.materialize(_DEVICE_NAME)   # 구버전 폴더의 프로빙 캐시 재사용(워커 스레드)
            with open(_DEVICE_CACHE, encoding="utf-8") as f:
                return int(json.load(f)["device_id"])
        except Exception:
            pass
    best, best_t = None, float("inf")
    x = np.zeros((1, 3, TILE, TILE), dtype=np.float32)
    for dev in range(4):
        try:
            if dev > 0:
                time.sleep(0.1)         # 디바이스 사이 양보 — GPU 연속 점유로 UI 렌더 정지 완화
            # 존재하지 않는 device_id 를 시험하면 ORT 파이썬 래퍼가 "EP Error … Falling
            # back to CPU and retrying" 블록을 콘솔에 출력(정상 탐색 과정) — 사용자가
            # 오류로 오인하므로 프로빙 중에는 출력을 삼킨다(결과는 아래서 직접 판정).
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                s = ort.InferenceSession(
                    MODEL_PATH, providers=[("DmlExecutionProvider", {"device_id": dev})])
            # 존재하지 않는 device_id 는 예외 대신 조용히 CPU 로 폴백됨(ORT 동작) → 감지해 중단
            if s.get_providers()[0] != "DmlExecutionProvider":
                del s
                break
            inp = s.get_inputs()[0].name
            s.run(None, {inp: x})                       # 워밍업(컴파일)
            t0 = time.perf_counter()
            s.run(None, {inp: x})
            dt = time.perf_counter() - t0
            if dt < best_t:
                best, best_t = dev, dt
            del s
        except Exception:
            break                                       # 존재하지 않는 device_id → 중단
    if best is not None:
        try:
            with open(_DEVICE_CACHE, "w", encoding="utf-8") as f:
                json.dump({"device_id": best, "tile_ms": round(best_t * 1000)}, f)
        except Exception:
            pass
        print(f"[ai-nr] DML device {best} 선택 ({best_t*1000:.0f} ms/타일)")
    return best


def _session():
    """캐시된 ONNX Runtime 세션 — GPU EP(DirectML 최속 디바이스/CoreML) 우선, 실패 시 CPU.
    락으로 이중 초기화 방지 — 프리뷰 워커와 export 스레드가 동시에 들어오면 DML 프로빙이
    서로 간섭해 느린 디바이스가 영구 캐시되고 GPU 세션 하나가 누수됐음."""
    global _session_obj, _provider_label
    if _session_obj is not None:
        return _session_obj
    with _sess_lock:
        if _session_obj is not None:
            return _session_obj
        import onnxruntime as ort
        ensure_model()
        avail = set(ort.get_available_providers())
        try:
            if "DmlExecutionProvider" in avail:
                # 1차=캐시된 디바이스. 캐시가 현재 GPU 구성에서 무효(드라이버 업데이트/어댑터
                # 열거 변경)면 ORT 가 예외 없이 세션 안에서 CPU 로 폴백(EP Error 로그만 출력)
                # → 실제 EP 를 확인해 감지하고, 캐시 삭제 후 재실측으로 1회 재시도.
                import contextlib
                import io
                for attempt in (0, 1):
                    dev = _probe_dml_device(ort, fresh=(attempt == 1))
                    if dev is None:
                        break
                    # 무효 device 면 ORT 가 "EP Error" 블록을 출력하며 CPU 폴백 —
                    # 노이즈는 삼키고 아래서 실제 EP 를 판정해 명확한 메시지로 처리.
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        s = ort.InferenceSession(
                            MODEL_PATH,
                            providers=[("DmlExecutionProvider", {"device_id": dev}),
                                       "CPUExecutionProvider"])
                    if s.get_providers()[0] == "DmlExecutionProvider":
                        _session_obj = s
                        break
                    del s                       # DML 이 안 잡힘 → 폴백 세션은 버림
                    if attempt == 0:
                        print(f"[ai-nr] 캐시된 DML device {dev} 무효 → 캐시 삭제 후 재프로빙")
                        try:
                            os.remove(_DEVICE_CACHE)
                        except OSError:
                            pass
            elif "CoreMLExecutionProvider" in avail:
                _session_obj = ort.InferenceSession(
                    MODEL_PATH, providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
        except Exception as exc:
            print(f"[ai-nr] GPU EP 초기화 실패 → CPU 폴백: {exc}")
        if _session_obj is None:
            # CPU 폴백: 전 코어 점유 시 UI 이벤트 처리까지 굶는다 → 2코어 여유(버벅임 완화)
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(2, (os.cpu_count() or 8) - 2)
            _session_obj = ort.InferenceSession(
                MODEL_PATH, sess_options=so, providers=["CPUExecutionProvider"])
        _provider_label = ("GPU" if _session_obj.get_providers()[0] in _ACCEL_PROVIDERS
                           else "CPU")
    return _session_obj


def prewarm() -> None:
    """모델이 이미 있으면(=이전에 AI denoise 사용) 백그라운드로 ORT 세션을 미리 생성한다.
    저장된 aiNr 이미지 로드가 시작될 때 호출 → 세션 초기화(DML 프로빙/그래프 컴파일, GPU 점유)를
    RAW 디코드와 병렬로 진행해 '로드 직후' 초기화 freeze 를 로드 대기 안으로 흡수. 모델 미다운로드
    (신규 사용자)면 no-op(다운로드 유발 금지). _sess_lock 으로 이후 워커와 안전 공유(이중 생성 없음)."""
    if _session_obj is not None or not model_available():
        return

    def _warm():
        try:
            _session()
        except Exception as exc:
            print(f"[ai-nr] prewarm 실패(무시): {exc}")

    threading.Thread(target=_warm, daemon=True).start()


def _ramp_weight() -> np.ndarray:
    """타일 블렌딩 가중(TILE,TILE): 가장자리 OVERLAP 폭에서 선형 경사, 중앙 1.
    단독 커버 픽셀은 정규화로 어차피 1 — 경사는 겹침 구간의 크로스페이드만 담당."""
    r = np.ones(TILE, dtype=np.float32)
    ramp = (np.arange(OVERLAP, dtype=np.float32) + 1.0) / OVERLAP
    r[:OVERLAP] = ramp
    r[-OVERLAP:] = ramp[::-1]
    return np.outer(r, r).astype(np.float32)


def denoise_rgb(rgb: np.ndarray, progress=None, cancel=None,
                drift_sigma: float = DRIFT_SIGMA, pace: float = 0.0,
                hold=None) -> np.ndarray:
    """display sRGB RGB(H,W,3 float32 [0,1]) → 디노이즈드 RGB(H,W,3 float32 [0,1]).

    luma(휘도 NR 베이스)와 chroma(컬러 NR 베이스)를 모두 담는 풀 RGB 결과 — 크로마가
    AI 디노이즈 체감의 핵심(색얼룩)이라 luma 만 취하면 안 된다.
    drift_sigma>0 이면 모델 드리프트 제거(원본 저주파 복원 — DRIFT_SIGMA 주석 참조).
    pace>0 이면 타일마다 그만큼 쉼(UI_PACE 주석 참조 — 앱 내 실행 시 버벅임 완화).
    hold(): truthy 인 동안 타일 루프 일시정지(사용자 조작 중 GPU 양보 — 타일 1개가 도는
    동안은 GPU 가 통째로 점유되므로, 조작 중엔 아예 멈추는 것이 pace 보다 근본적).
    progress(0..1): 타일 진행 콜백. cancel(): truthy 반환 시 Cancelled 발생(타일 경계 체크).
    여분 메모리: 풀해상도(26MP) 기준 acc (H,W,3)+wsum (H,W) float32 ≈ 400MB(일시).
    """
    sess = _session()
    inp = sess.get_inputs()[0].name
    h, w = rgb.shape[:2]
    # TILE 미만이면 패딩(끝에 크롭). 512 가 곧 최소 처리 단위.
    # mode="symmetric"(경계 픽셀 포함 대칭) — 짧은 변에서 pad 폭이 변보다 커도 안전하고
    # 경계 이중 픽셀이 없어 reflect 보다 타일 경계가 깔끔.
    ph, pw = max(h, TILE), max(w, TILE)
    src = rgb
    if (ph, pw) != (h, w):
        src = np.pad(rgb, ((0, ph - h), (0, pw - w), (0, 0)), mode="symmetric")
    stride = TILE - OVERLAP
    ys = list(range(0, max(ph - TILE, 0) + 1, stride))
    xs = list(range(0, max(pw - TILE, 0) + 1, stride))
    if ys[-1] != ph - TILE:
        ys.append(ph - TILE)              # 마지막 타일은 끝에 맞춤(겹침만 커짐)
    if xs[-1] != pw - TILE:
        xs.append(pw - TILE)
    acc = np.zeros((ph, pw, 3), dtype=np.float32)
    wsum = np.zeros((ph, pw), dtype=np.float32)
    wt = _ramp_weight()
    total = len(ys) * len(xs)
    done = 0
    for y0 in ys:
        for x0 in xs:
            if cancel is not None and cancel():
                raise Cancelled()
            while hold is not None and hold():      # 조작 중 일시정지(취소는 계속 감시)
                if cancel is not None and cancel():
                    raise Cancelled()
                time.sleep(0.05)
            tile = src[y0:y0 + TILE, x0:x0 + TILE]
            x = np.ascontiguousarray(tile.transpose(2, 0, 1)[None], dtype=np.float32)
            out = sess.run(None, {inp: x})[0][0].transpose(1, 2, 0)   # (TILE, TILE, 3)
            if float(np.abs(out - tile).mean()) > DIVERGE_MEAN:
                out = tile          # 모델 발산(극암부 타일) → 원본 유지(DIVERGE_MEAN 주석 참조)
            acc[y0:y0 + TILE, x0:x0 + TILE] += out * wt[..., None]
            wsum[y0:y0 + TILE, x0:x0 + TILE] += wt
            done += 1
            if progress is not None:
                progress(done / total)
            if pace > 0.0 and done < total:
                time.sleep(pace)        # GPU/CPU 양보 — UI 프레임이 그려질 창
    out = np.clip(acc[:h, :w] / np.maximum(wsum[:h, :w, None], 1e-6), 0.0, 1.0)
    if drift_sigma and drift_sigma > 0.0:
        # 드리프트 제거: 원본−디노이즈드 차이의 저주파(=모델이 바꾼 넓은 영역 색/밝기)를
        # 되돌린다 → NR 빼기 수식에는 고주파(노이즈)만 남아 색감/톤 이동이 없다.
        from scipy.ndimage import gaussian_filter
        out += gaussian_filter(rgb - out, (drift_sigma, drift_sigma, 0))
        np.clip(out, 0.0, 1.0, out=out)
    return out


def denoise_luma(rgb: np.ndarray, progress=None, cancel=None) -> np.ndarray:
    """denoise_rgb 의 luma 축약(테스트/luma 전용 호출용)."""
    return np.ascontiguousarray(denoise_rgb(rgb, progress, cancel) @ _LUMA)
