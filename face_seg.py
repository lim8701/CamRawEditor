# -*- coding: utf-8 -*-
"""얼굴 마스킹 엔진 — 검출(YuNet) + 부위 파싱(SegFormer/CelebAMask-HQ 19클래스).

라이트룸의 People 마스크에 해당. 2단 구성이고 모델도 두 개다:

  1) detect_faces(rgb)          → 얼굴 박스 + 5점 랜드마크        (YuNet, 232KB, MIT)
  2) parse_faces(rgb, dets)     → 얼굴별 19클래스 확률맵          (SegFormer-B5, 340MB)
     compose_face_mask(...)     → 선택 부위 합집합 soft alpha     (추론 없이 재조합, 빠름)

검출기는 '어디를 자를지'만 정한다. 경계 정밀도는 전적으로 파싱이 만든다 — 박스가 곧 마스크가
아니다. 파싱 모델은 **정렬된 얼굴 크롭**으로 학습돼서 전체 사진을 넣으면 배경을 피부/머리카락으로
오분류한다. 그래서 검출 → 크롭 → 파싱 순서가 필수.

sky_seg.py 와 같은 SegFormer 계열이라 전처리(/255 → ImageNet 정규화 → NCHW)와 출력(입력의 1/4
해상도 logits)이 동일하고 정규화 상수·smoothstep 은 sky_seg 것을 그대로 쓴다.
⚠️단 후처리 **상수**는 하늘과 반대로 잡고(FACE_LO/FACE_HI 주석), 리사이즈·가이디드필터는
  scipy 대신 cv2 로 따로 구현했다(_resize/_guided 주석 — 크롭이 커서 scipy 로는 느림).

검출 추론만 onnxruntime 이 아니라 **cv2.FaceDetectorYN**(OpenCV DNN) 을 쓴다. YuNet ONNX 는 입력이
640x640 고정이라 ORT 로는 letterbox+앵커프리 디코드+NMS 를 직접 구현해야 하는데, OpenCV 가 그
전부를 C++ 로 이미 갖고 있다. 파싱은 다른 모듈과 동일하게 onnxruntime(CPU).

PySide6/QML 비의존 — numpy in / numpy out 독립 모듈(sky_seg.py 계약 미러).
"""

import hashlib
import os
import threading
import urllib.request

import numpy as np

import app_dirs
import sky_seg     # _resize / _smoothstep / _guided_filter / _MEAN / _STD / _LUMA 재사용

# ── 모델 ────────────────────────────────────────────────────────────────────
# 이동 참조(main) 대신 커밋 리비전 고정 + 다운로드 후 SHA-256 검증(HF LFS oid).
# 업스트림 변조 시 조작된 .onnx 가 조용히 네이티브 파서로 넘어가는 것 방지(sky_seg 와 동일 규칙).
_DET_REPO = "opencv/face_detection_yunet"
_DET_REV = "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"
_DET_NAME = "yunet_face_2023mar.onnx"
_DET_URL = f"https://huggingface.co/{_DET_REPO}/resolve/{_DET_REV}/face_detection_yunet_2023mar.onnx"
_DET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
_DET_BYTES = 232_589

# fp32(340MB). 같은 리포에 model_fp16(172MB)/model_quantized(89MB)도 있지만 둘 다 CPU EP 에선
# 부적합 — fp16 은 ORT 가 cast 를 끼워 넣어 결국 fp32 로 돌고, int8 은 동적 양자화라
# MatMulInteger 융합이 안 돼 오히려 느려질 수 있고 per-pixel 경계가 뭉갠다. 품질/속도 재측정 후
# 바꾸려면 _PARSE_NAME/_PARSE_URL/_PARSE_SHA256/_PARSE_BYTES 네 상수만 교체하면 된다.
_PARSE_REPO = "Xenova/face-parsing"
_PARSE_REV = "f25b9b521a8783d4e78e80e026ef4c2a15f821e0"
_PARSE_NAME = "face_parsing_b5.onnx"
_PARSE_URL = f"https://huggingface.co/{_PARSE_REPO}/resolve/{_PARSE_REV}/onnx/model.onnx"
_PARSE_SHA256 = "6d4e67af60ff78184745ebf74cc15163c0adc27d45cdeba31e3a03d1096fb8c3"
_PARSE_BYTES = 340_316_611

MODEL_DIR = app_dirs.MODELS_DIR
DET_PATH = app_dirs.model_path(_DET_NAME)
PARSE_PATH = app_dirs.model_path(_PARSE_NAME)
TOTAL_BYTES = _DET_BYTES + _PARSE_BYTES              # 최초 사용 시 받아야 할 총량(진행률 표시용)

# ── 모델 관리 화면(AI Models)용 메타데이터 — sky_seg 와 동일 계약 ────────────
MODEL_LABEL = "Face masking"
MODEL_NOTE = "Face part masks — skin, hair, eyes, lips (YuNet detection + SegFormer parsing)"
MODEL_FILES = [_DET_NAME, _PARSE_NAME]

# ── 검출 파라미터 ────────────────────────────────────────────────────────────
# YuNet 은 **네트워크 입력 기준** 약 10~300px 얼굴로 학습됐다. 프록시(긴 변 ~2560)를 한 가지
# 크기로만 넣으면 한쪽 끝이 잘린다:
#   긴 변 640 (s=0.25)  → 프록시 얼굴 40~1200px 커버   (일반 인물/단체)
#   긴 변 320 (s=0.125) → 프록시 얼굴 80~2400px 커버   (타이트한 클로즈업)
# 두 스케일을 돌려 교차 NMS 로 합친다. 232KB 모델이라 2패스 합쳐도 수십 ms.
# ⚠️실측(DSCF1039, 2560 프레임에 954px 얼굴)에서 640 단독은 놓치고 320 패스가 잡았다 — 필수.
DET_LONG_EDGES = (640, 320)
SCORE_T = 0.70      # OpenCV 데모 기본값은 0.9 — 사진 편집엔 과하게 보수적(측면/저조도 누락)
NMS_T = 0.30
TOP_K = 5000

# ── 파싱 파라미터 ────────────────────────────────────────────────────────────
PARSE_EDGE = 512    # preprocessor_config.json 이 512x512 정사각. 크롭이 정사각이라 왜곡 없음.
                    # ↑키우면 경계 디테일↑ 시간↑(logits 는 입력의 1/4 → 512면 128², 768이면 192²)
CROP_SCALE = 1.9    # 검출 박스 대비 크롭 정사각 변. CelebAMask-HQ 크롭이 머리카락+목을 포함한다.
CROP_UP = 0.10      # 크롭 중심을 위로 이동(변 대비 비율) — 머리카락이 잘리지 않게
MAX_FACES = 5       # 파싱 비용이 얼굴 수에 비례(실측 얼굴당 ~0.8s CPU) → 상한 필수

# 결정 곡선: 하늘(0.02/0.20)과 **반대로** argmax 근처로 잡는다. 하늘은 약확신 구름까지 solid 로
# 끌어올려야 했지만, 얼굴 파싱은 확신이 뚜렷하고 부위끼리 붙어 있어 낮은 임계값을 쓰면 옆 부위로
# 번진다(입술 선택인데 턱까지 물듦).
FACE_LO, FACE_HI = 0.35, 0.65
# ⚠️구멍 채우기(binary_fill_holes) 안 함 — skin 마스크의 구멍을 메우면 눈·입이 삼켜진다.
#   부위 분리가 이 기능의 존재 이유라 하늘의 fill_holes 를 그대로 가져오면 안 된다.
# 가이디드 필터는 **크롭 공간**에서(프록시 아님). sky 의 '짧은 변 × 0.012'는 프록시 기준 ~20px 라
# 입술 하나보다 크다. 크롭 변 대비 비율로 잡아야 부위 크기에 맞는다.
FACE_GUIDED_R = 0.02
FACE_GUIDED_EPS = 1e-4
# 크롭 경계 페더(변 대비 폭). 크롭은 **얼굴** 기준으로 잡히는데 neck/hair(긴 머리)는 그 밖까지
# 이어진다 → 크롭 모서리에서 뚝 잘려 화면에 **직사각형 자국**이 남는다(실측 DSCF1039: 옷 마스크가
# 완벽한 사각형이었고, 그래서 cloth 는 목록에서 제외했다). 경계에서 0 으로 감쇠시켜 잘림을 눈에
# 안 띄게 한다. 크롭 안에서 끝나는 부위(skin/eyes/lips 등)는 경계에 값이 없으므로 영향 없음.
CROP_FEATHER = 0.06

# ── 부위 그룹 (UI 체크박스) ──────────────────────────────────────────────────
# CelebAMask-HQ 19클래스: 0 background, 1 skin, 2 nose, 3 eye_g(안경), 4 l_eye, 5 r_eye,
# 6 l_brow, 7 r_brow, 8 l_ear, 9 r_ear, 10 mouth, 11 u_lip, 12 l_lip, 13 hair, 14 hat,
# 15 ear_r(귀걸이), 16 neck_l(목걸이), 17 neck, 18 cloth.
# 좌/우는 병합한다 — 사진 보정에서 왼쪽 눈썹만 만지는 작업은 없고, 좌/우 판정이 모델 기준이라
# 얼굴 각도에 따라 헷갈린다.
# 제외: background(0), cloth(18). cloth 는 크롭(얼굴 기준 1.9배) 밖까지 이어져 구조적으로 항상
#      일부만 잡힌다 — '옷 선택'으로 쓸 수 없어 목록에서 뺐다. 나머지 1~17 은 전부 포함.
FACE_GROUPS = [
    ("face:skin",    "Skin",     [1]),
    ("face:nose",    "Nose",     [2]),
    ("face:eyes",    "Eyes",     [4, 5]),
    ("face:brows",   "Brows",    [6, 7]),
    ("face:glasses", "Glasses",  [3]),
    ("face:lips",    "Lips",     [11, 12]),
    ("face:mouth",   "Mouth",    [10]),
    ("face:ears",    "Ears",     [8, 9, 15]),
    ("face:hair",    "Hair",     [13]),
    ("face:hat",     "Hat",      [14]),
    ("face:neck",    "Neck",     [16, 17]),
]
_GROUP_IDS = {k: ids for k, _, ids in FACE_GROUPS}


def class_ids_for(keys):
    """그룹 key 목록 → CelebAMask 인덱스 합집합(정렬). face: 접두사가 아닌 key 는 무시 —
    같은 체크박스 목록에 sky_seg 의 장면 클래스가 섞여 오기 때문."""
    out = set()
    for k in keys:
        out.update(_GROUP_IDS.get(str(k), []))
    return sorted(out)


def groups_for_qml():
    """QML 체크박스용 [{key,label}, ...] (Face 탭)."""
    return [{"key": k, "label": lbl} for k, lbl, _ in FACE_GROUPS]


_dl_lock = threading.Lock()
_det_lock = threading.Lock()    # FaceDetectorYN 은 setInputSize 로 상태가 바뀜 → 직렬화
_sess_lock = threading.Lock()
_det_obj = None
_parse_sess = None

_DL_TIMEOUT = 30


# ── 모델 확보 (sky_seg._download 와 동일 규칙: 타임아웃·완결성·해시·원자적 승격) ──────
def _download(url, dst, progress=None, sha256=None) -> None:
    """URL → dst 청크 다운로드. 소켓 타임아웃 + 실패 시 부분 파일 정리. progress(0..1).
    sha256 주어지면 내용 해시를 대조 — 불일치 시 raise(부분/조작 파일 승격 차단)."""
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


def _ensure(name, url, sha256, progress=None) -> str:
    """모델 파일 하나 보장(구버전 폴더에서 복사 or 다운로드). 락으로 동시 다운로드 방지 —
    워커가 겹치면 같은 .part 에 겹쳐 써서 손상본이 영구 설치된다."""
    path = app_dirs.model_path(name)
    with _dl_lock:
        if not os.path.exists(path) and not app_dirs.materialize(name):
            os.makedirs(MODEL_DIR, exist_ok=True)
            tmp = path + ".part"
            _download(url, tmp, progress, sha256)
            os.replace(tmp, path)       # 원자적 교체(부분파일 방지)
    return path


def is_ready() -> bool:
    """검출+파싱 둘 다 확보 가능한지(다운로드 불필요). 부작용 없음 — UI 스레드 안전."""
    return app_dirs.have(_DET_NAME) and app_dirs.have(_PARSE_NAME)


def ensure_model(progress=None) -> None:
    """검출+파싱 모델 보장. progress(0..1)는 **두 파일 합산 바이트 기준**(sam_seg 패턴) —
    340MB 뒤에 232KB 가 붙어도 진행률이 튀지 않는다."""
    done = [0]

    def _mk(nbytes):
        if progress is None:
            return None
        base = done[0]
        return lambda f: progress(min(1.0, (base + f * nbytes) / TOTAL_BYTES))

    _ensure(_DET_NAME, _DET_URL, _DET_SHA256, _mk(_DET_BYTES))
    done[0] += _DET_BYTES
    _ensure(_PARSE_NAME, _PARSE_URL, _PARSE_SHA256, _mk(_PARSE_BYTES))
    if progress is not None:
        progress(1.0)


def _detector():
    """캐시된 FaceDetectorYN. 입력 크기는 호출마다 setInputSize 로 바꾼다(_det_lock 아래에서)."""
    global _det_obj
    if _det_obj is None:
        import cv2
        # create() 내부의 setPreferableTarget 이 OpenCV 5 새 graph engine 에서
        # "Targets are not supported…" 경고를 stderr 로 뿜는다(무해). 조용히.
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        _ensure(_DET_NAME, _DET_URL, _DET_SHA256)
        _det_obj = cv2.FaceDetectorYN.create(
            DET_PATH, "", (320, 320),
            score_threshold=SCORE_T, nms_threshold=NMS_T, top_k=TOP_K)
    return _det_obj


def _parse_session():
    """캐시된 ONNX Runtime 세션(CPU). 락으로 이중 생성 방지 — 워커가 겹치면 340MB 세션이
    중복 생성돼 한쪽이 프로세스 수명 동안 누수된다(sky_seg 와 동일 규칙)."""
    global _parse_sess
    if _parse_sess is None:
        with _sess_lock:
            if _parse_sess is None:
                import onnxruntime as ort
                _ensure(_PARSE_NAME, _PARSE_URL, _PARSE_SHA256)
                opts = ort.SessionOptions()
                opts.log_severity_level = 3
                _parse_sess = ort.InferenceSession(
                    PARSE_PATH, opts, providers=["CPUExecutionProvider"])
    return _parse_sess


# ── 검출 ────────────────────────────────────────────────────────────────────
def _detect_one(bgr_u8, long_edge):
    """단일 스케일 검출. 반환: (N,15) float32 — 원본 bgr 좌표계로 역스케일 완료.
    열 구성: x, y, w, h, kps(5x2), score."""
    import cv2
    h, w = bgr_u8.shape[:2]
    s = long_edge / float(max(h, w))
    if s < 1.0:
        iw, ih = max(1, int(round(w * s))), max(1, int(round(h * s)))
        small = cv2.resize(bgr_u8, (iw, ih), interpolation=cv2.INTER_AREA)
    else:                                   # 프록시가 이미 작으면 확대하지 않음
        s, iw, ih = 1.0, w, h
        small = bgr_u8
    det = _detector()
    det.setInputSize((iw, ih))
    _, faces = det.detect(small)
    if faces is None or len(faces) == 0:
        return np.zeros((0, 15), np.float32)
    faces = np.asarray(faces, np.float32).reshape(-1, 15).copy()
    faces[:, :14] /= s                      # 박스+랜드마크만 역스케일(14번 열은 score)
    return faces


def _nms(faces, thr=NMS_T):
    """스케일 간 교차 NMS. faces: (N,15). 반환: 남길 인덱스(점수 내림차순)."""
    if len(faces) <= 1:
        return list(range(len(faces)))
    x1, y1 = faces[:, 0], faces[:, 1]
    x2, y2 = x1 + faces[:, 2], y1 + faces[:, 3]
    area = np.maximum(faces[:, 2], 0) * np.maximum(faces[:, 3], 0)
    order, keep = faces[:, 14].argsort()[::-1], []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (area[i] + area[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= thr]
    return keep


def detect_faces(rgb_u8: np.ndarray, max_faces: int = MAX_FACES, long_edges=DET_LONG_EDGES):
    """RGB(H,W,3 uint8) → 얼굴 목록. 좌표는 입력 이미지(프록시) 픽셀 기준.

    반환: [{"box": (x, y, w, h), "kps": (5,2) float32, "score": float}, ...]
      kps 순서(OpenCV YuNet): [오른눈, 왼눈, 코끝, 오른입꼬리, 왼입꼬리]
      면적 내림차순 정렬 후 max_faces 개로 제한(파싱 비용이 얼굴 수에 비례하므로 상한 필수).

    ⚠️kps 는 현재 파이프라인이 쓰지 않는다(크롭 롤 정렬은 불필요로 판명 — 90° 누운 얼굴도
      정렬 없이 제대로 파싱됨). 나중에 쓰더라도 각도를 그대로 믿지 말 것: 크게 기울어진
      얼굴에서는 박스는 정확한데 두 눈 점이 한쪽에 몰린다(DSCF1039 실측).
    """
    if rgb_u8 is None or rgb_u8.ndim != 3 or rgb_u8.shape[2] != 3 or min(rgb_u8.shape[:2]) < 1:
        return []
    bgr = np.ascontiguousarray(rgb_u8[:, :, ::-1])      # YuNet 은 BGR/0~255/정규화 없음
    with _det_lock:                                     # setInputSize 상태 공유 → 직렬화
        allf = [_detect_one(bgr, le) for le in long_edges]
    faces = np.concatenate([f for f in allf if len(f)], axis=0) if any(
        len(f) for f in allf) else np.zeros((0, 15), np.float32)
    if not len(faces):
        return []
    faces = faces[_nms(faces)]
    faces = faces[np.argsort(faces[:, 2] * faces[:, 3])[::-1]][:max(0, int(max_faces))]
    out = []
    for f in faces:
        # 프레임 밖으로 나간 박스는 그대로 둔다 — 크롭이 edge-pad 로 처리하고,
        # 여기서 자르면 정사각 크롭 기하가 깨진다. 랜드마크도 원값 유지.
        out.append({"box": (float(f[0]), float(f[1]), float(f[2]), float(f[3])),
                    "kps": f[4:14].reshape(5, 2).astype(np.float32),
                    "score": float(f[14])})
    return out


# ── 파싱 ────────────────────────────────────────────────────────────────────
# 리사이즈/가이디드필터는 sky_seg 것 대신 cv2 로 한다 — 얼굴 크롭은 최대 ~1900px 정사각이라
# scipy 로는 재조합 한 번에 0.45s 가 걸린다(실측: zoom 0.119s + guided 0.332s @ 1898²).
# cv2 는 같은 연산이 0.13s. 마스크 배열은 프리뷰와 export 가 **같은 것**을 쓰므로(main.py 가 한 번
# 계산해 양쪽에 전달) 셰이더/pipeline 정합 문제는 발생하지 않는다.
# ⚠️scipy zoom(order=1)은 align_corners 방식이고 cv2 는 half-pixel center 방식이라 업샘플
#   그리드가 반 픽셀 어긋난다. 리샘플링 표준은 half-pixel 쪽이라 cv2 가 오히려 정확하다.
def _resize(a, out_hw, area=False):
    import cv2
    oh, ow = int(out_hw[0]), int(out_hw[1])
    if a.shape[:2] == (oh, ow):
        return a
    interp = cv2.INTER_AREA if area else cv2.INTER_LINEAR
    return cv2.resize(a, (ow, oh), interpolation=interp)


def _guided(guide, src, radius, eps):
    """He et al. guided filter (cv2.boxFilter 판). sky_seg._guided_filter 와 동일 수식."""
    import cv2
    k = (2 * max(1, int(radius)) + 1,) * 2

    def bf(x):
        return cv2.boxFilter(x, -1, k, borderType=cv2.BORDER_REFLECT)

    mean_g, mean_s = bf(guide), bf(src)
    cov = bf(guide * src) - mean_g * mean_s
    var = bf(guide * guide) - mean_g * mean_g
    a = cov / (var + eps)
    return bf(a) * guide + bf(mean_s - a * mean_g)


def _border_ramp(si):
    """크롭 경계에서 0 으로 떨어지는 1D 감쇠 창(길이 si).

    2D 창을 만들지 않는 이유: si 는 최대 ~1900 이라 (si,si) float32 = 14MB 다. 분리형이라
    행·열에 차례로 곱하면 같은 결과를 추가 할당 없이 얻는다."""
    wpx = max(1, int(si * CROP_FEATHER))
    r = np.ones(si, np.float32)
    ramp = np.linspace(0.0, 1.0, wpx + 2, dtype=np.float32)[1:-1]
    r[:wpx], r[-wpx:] = ramp, ramp[::-1]
    return r


def crop_geom(det):
    """검출 박스 → 파싱용 정사각 크롭 기하 (x0, y0, side). 프레임 밖으로 나갈 수 있다."""
    x, y, w, h = det["box"]
    side = max(w, h) * CROP_SCALE
    cx, cy = x + w * 0.5, y + h * 0.5 - side * CROP_UP
    return (cx - side * 0.5, cy - side * 0.5, side)


def _crop_square(rgb_u8, geom):
    """(x0,y0,side) 정사각 크롭. 프레임을 벗어난 부분은 **edge 패딩**으로 채운다 —
    잘라내면 정사각이 깨져 512 리사이즈에서 얼굴이 찌부러진다(클로즈업에선 거의 항상 벗어남)."""
    x0, y0, side = geom
    h, w = rgb_u8.shape[:2]
    xi, yi, si = int(round(x0)), int(round(y0)), max(1, int(round(side)))
    # ⚠️패딩은 **잘라낸 조각에만** 건다. 원본을 통째로 np.pad 하면 프록시 한 장(≈20MB)을
    #   호출마다 복사해 부위 토글 재조합이 0.5s 씩 걸린다(실측).
    sx0, sy0 = max(0, xi), max(0, yi)
    sx1, sy1 = min(w, xi + si), min(h, yi + si)
    if sx0 >= sx1 or sy0 >= sy1:                    # 크롭이 프레임 완전 바깥
        return np.zeros((si, si, 3), np.uint8)
    sub = rgb_u8[sy0:sy1, sx0:sx1]
    pl, pt, pr, pb = sx0 - xi, sy0 - yi, xi + si - sx1, yi + si - sy1
    if pl or pt or pr or pb:
        sub = np.pad(sub, ((pt, pb), (pl, pr), (0, 0)), mode="edge")
    return sub


def parse_faces(rgb_u8, dets, on_face=None):
    """얼굴별 파싱 추론(비쌈, 실측 얼굴당 ~0.8s CPU). 반환: [(geom, probs(19,hm,wm) float32), ...].

    probs 는 크롭 좌표계의 **저해상도**(입력 512 → 128²) 확률맵. 이미지당 1회 캐시해 두고
    compose_face_mask 로 부위 조합만 바꾸면 추론 없이 즉시 재조합된다(sky_seg 의
    infer_softmax/compose_mask 2단계 구조와 동일).
    on_face(i, n): 진행률 콜백(얼굴 단위)."""
    if not dets:
        # 얼굴이 없으면 여기서 끝 — _parse_session() 을 부르면 쓰지도 않을 340MB ORT 세션을
        # 2.6초 걸려 만들고 프로세스 수명 내내 물고 있게 된다(풍경 사진에 Face 체크 시).
        return []
    sess = _parse_session()
    inp = sess.get_inputs()[0].name
    outn = sess.get_outputs()[0].name
    out = []
    n = len(dets)
    for i, det in enumerate(dets):
        if on_face is not None:
            on_face(i, n)
        geom = crop_geom(det)
        crop = _crop_square(rgb_u8, geom)
        # 크롭은 대개 512보다 커서 축소 — INTER_AREA 가 에일리어싱이 적다
        x = _resize(crop.astype(np.float32), (PARSE_EDGE, PARSE_EDGE),
                    area=crop.shape[0] > PARSE_EDGE) / 255.0
        x = (x - sky_seg._MEAN) / sky_seg._STD
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)
        logits = sess.run([outn], {inp: x})[0][0]            # (19, hm, wm)
        logits = logits - logits.max(axis=0, keepdims=True)  # softmax 수치안정
        e = np.exp(logits)
        out.append((geom, (e / e.sum(axis=0)).astype(np.float32)))
    return out


def compose_face_mask(parsed, rgb_u8, class_ids):
    """선택 부위 합산 → 프록시 해상도 soft alpha float32 [0,1]. 추론 없이 빠르게 재조합.

    부위 확률을 크롭 해상도로 올려 결정 곡선 → **크롭 휘도 기준** guided filter 로 엣지 정제 →
    원위치에 np.maximum 누적. 정제를 크롭 공간에서 하는 이유는 FACE_GUIDED_R 주석 참조."""
    h, w = rgb_u8.shape[:2]
    canvas = np.zeros((h, w), np.float32)
    if not class_ids or not parsed:
        return canvas
    ids = list(class_ids)
    for geom, probs in parsed:
        x0, y0, side = geom
        si = max(1, int(round(side)))
        m = _resize(probs[ids].sum(axis=0), (si, si)).astype(np.float32)
        m = sky_seg._smoothstep(FACE_LO, FACE_HI, m)
        guide = (_crop_square(rgb_u8, geom).astype(np.float32) / 255.0) @ sky_seg._LUMA
        m = np.clip(_guided(guide, m, int(si * FACE_GUIDED_R), FACE_GUIDED_EPS), 0.0, 1.0)
        ramp = _border_ramp(si)         # 크롭 모서리 직사각형 자국 방지(분리형 → 행·열 각각)
        m *= ramp[:, None]
        m *= ramp[None, :]
        # 크롭이 프레임 밖으로 나간 만큼 잘라 캔버스에 겹친다(패딩 영역은 버림).
        xi, yi = int(round(x0)), int(round(y0))
        sx, sy = max(0, -xi), max(0, -yi)
        dx, dy = max(0, xi), max(0, yi)
        cw, ch = min(si - sx, w - dx), min(si - sy, h - dy)
        if cw > 0 and ch > 0:
            np.maximum(canvas[dy:dy + ch, dx:dx + cw], m[sy:sy + ch, sx:sx + cw],
                       out=canvas[dy:dy + ch, dx:dx + cw])
    return canvas
