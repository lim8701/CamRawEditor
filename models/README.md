# models/ — ONNX 모델

이 폴더의 `*.onnx` 파일은 **용량이 커서 git에 커밋하지 않는다**(`.gitignore`). 최초 사용 시
각 모듈의 `ensure_model()` 이 아래 출처에서 자동 다운로드한다(`urllib`, 원자적 tmp→rename).

## 저장 위치 (`app_dirs.py`)

dev/배포 구분 없이 **항상 OS 사용자 데이터 디렉터리**에 저장한다(일관성 — zip 업데이트마다
새 폴더에 풀려도, dev 폴더를 지워도 재다운로드 없음):

- Windows: `%LOCALAPPDATA%\FilmRawstery\models` (머신 전용 대용량 — Roaming 제외)
- macOS: `~/Library/Application Support/FilmRawstery/models` (Caches 는 OS 가 지울 수 있음)
- Linux: `${XDG_DATA_HOME:-~/.local/share}/FilmRawstery/models` (XDG 규약)

**legacy 마이그레이션**: 예전 위치(구버전 frozen 은 exe 옆 `lib/models`, dev 는 이 폴더)에
받아둔 파일은 첫 사용 시 재다운로드 대신 새 위치로 **복사**된다(`app_dirs.materialize`).
GPU 프로빙 캐시(`ai_denoise_device.json`)도 동일. 복사가 끝난 뒤 이 폴더의 대용량 파일은
지워도 된다(.gitignore 규칙은 legacy 안전망으로 유지).

앱을 삭제해도 사용자 디렉터리의 모델은 남는다 — 완전 제거하려면 위 경로를 함께 삭제.

## ⚠️ GPU(DirectML) 추론은 반드시 직렬화 (`ai_denoise.GPU_LOCK`)

서로 다른 ORT 세션이 DirectML 에 **동시에** 제출하면 NVIDIA 드라이버(nvwgf2umx.dll)가
access violation 으로 프로세스째 죽는다. DSCF1962(aiNr=True + depth 마스크 사이드카) 로드에서
100% 재현됐고, 헤드리스(offscreen)에서도 동일 — Qt 렌더링과 무관한 **DML↔DML 경합**이다
(조합 분리 실측: NAFNet 단독 OK · depth 단독 OK · 둘 동시 = 크래시).

규칙: **GPU EP 세션의 `run()` 과 세션 생성(=DML 셰이더 컴파일)은 `ai_denoise.GPU_LOCK` 안에서만.**
- NAFNet: 타일(GPU 146ms)마다 잡았다 놓아 다른 추론과 인터리브
- depth: 추론 1회(~0.5s)와 세션 생성(~3.6s)을 각각 락 안에서
- Florence-2: 스텝(비전/인코더/디코더 토큰)마다 락 — CPU 강제 배치(cpu=True)는 잡지 않음
- CPU 세션은 잡지 않는다(경합 없음 + CPU 타일 ~5s 가 GPU 작업을 막으면 안 됨)

새 ONNX 모델을 추가할 때 GPU EP 를 쓴다면 같은 규칙을 따라야 한다. 과거 주간 빈도로 있던
동일 시그니처 크래시(2026-07-20/23, DirectML 로드 상태)도 같은 기전으로 추정된다.

## 하늘 세그멘테이션 (Sky segmentation)

- **모델**: SegFormer-B2, ADE20K 150클래스 시맨틱 세그멘테이션 (하늘 = 클래스 2)
- **사용 파일**: `models/segformer_b2_ade.onnx` (~105 MB)
- **출처(Hugging Face)**: [`Xenova/segformer-b2-finetuned-ade-512-512`](https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512)
  (transformers.js 용 사전 export ONNX)
- **다운로드 URL**:
  `https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512/resolve/main/onnx/model.onnx`
- 코드 상수: `sky_seg.py` 의 `_REPO` / `_MODEL_URL` / `MODEL_PATH`

### 모델 변형 (필요 시 교체)

같은 Xenova 계열에 B0~B5 ONNX가 모두 있다. `sky_seg.py` 의 `_REPO`·`MODEL_PATH` 한 줄만 바꾸면
교체된다(모든 변형이 sky=클래스2·ImageNet 정규화 동일).

| 변형 | repo (`Xenova/...`) | 크기(fp32) | 추론(proxy) | 비고 |
|------|---------------------|-----------|-------------|------|
| B0 | `segformer-b0-finetuned-ade-512-512` | ~14 MB | ~0.3 s | 채광창+구름 등에서 하늘 누락 |
| **B2** | `segformer-b2-finetuned-ade-512-512` | ~105 MB | ~1.2 s | **현재 채택(균형점)** |
| B4 | `segformer-b4-finetuned-ade-512-512` | ~260 MB | 느림 | |
| B5 | `segformer-b5-finetuned-ade-640-640` | ~324 MB | ~3.9 s | B2와 품질 거의 동일 → 비권장 |

각 repo의 `onnx/` 폴더에는 `model.onnx`(fp32) 외에 `model_fp16.onnx`, `model_quantized.onnx` 도
있다(배포 용량 축소 옵션, 품질 약간 저하 가능).

### 라이선스 (⚠️ 상업 배포 시 확인)

SegFormer 가중치는 **NVIDIA 원본 라이선스**(연구용 위주, 상업적 사용 제한)에서 유래한다. 앱을
상업적으로 배포할 경우 모델 라이선스를 반드시 확인하고, 필요하면 상업적으로 자유로운 하늘
세그멘테이션 모델로 교체할 것. (자세한 검출 기술 내용: `docs/sky_masking.md`)

## AI 디노이즈 (AI denoise)

- **모델**: NAFNet-SIDD width32 (conv 전용 UNet, 실카메라 노이즈 SIDD 학습)
- **사용 파일**: `models/nafnet_sidd_width32_512.onnx` (~117 MB, 고정 512×512 입력)
- **원 출처**: [megvii-research/NAFNet](https://github.com/megvii-research/NAFNet) — 공식
  가중치 `NAFNet-SIDD-width32.pth`(repo docs/SIDD.md 의 Google Drive 링크)를 값 무변경
  1:1 ONNX 변환한 것(torch↔ort 최대 오차 ~1e-5 검증). LayerNorm2d 는 custom autograd
  Function 이라 수학적으로 동일한 추론용 모듈로 치환 후 export.
- **다운로드 URL** (코드 상수: `ai_denoise.py` 의 `_MODEL_URL` / `MODEL_PATH`):
  `https://github.com/lim8701/FilmRawstery/releases/download/models-v1/nafnet_sidd_width32_512.onnx`
- **고정 512 인 이유**: 타일 크기 통일 + 고정 크기가 EP 그래프 최적화에 유리(NAFNet 자체는
  conv 전용이라 동적도 가능). 512 타일 + 겹침 램프 블렌딩으로 임의 해상도 처리.
- **실행 장치**: GPU EP 우선(`onnxruntime-directml` 의 DirectML — DX12 GPU 전반, macOS 는
  표준 onnxruntime 의 CoreML) → 없거나 초기화 실패 시 CPU 폴백(느려서 앱이 진행 여부를
  사용자에게 확인). 듀얼 GPU 는 최초 1회 디바이스 프로빙 후 `models/ai_denoise_device.json`
  에 캐시(GPU 구성 변경 시 이 파일 삭제 → 재프로빙).

### ⚠️ SCUNet 을 쓰지 않는 이유 (재조사 방지)

처음엔 SCUNet(Apache-2.0, 순수 합성 학습 — 라이선스 최상)을 채택했으나, swin attention 의
소형 연산 수백 개가 **DirectML 에서 가속 불능**으로 실측 판명(RTX 3050 Ti: DML 4.5~82초/타일
vs CPU 5초; 그래프 분할 아님 — DML 단독 실행 성공에도 느림, fp16 도 17% 개선뿐).
conv 전용 NAFNet 은 동일 GPU 146ms/타일(35×). CUDA EP 는 NVIDIA 전용 + 의존성 1~2GB 라 기각.

### 라이선스

NAFNet 코드·가중치는 **MIT License**(+ BasicSR 부분 Apache-2.0). 학습 데이터 SIDD 도
**MIT** — 공식 페이지(abdokamel.github.io/sidd)에 "The dataset and the associated code
repositories are under the MIT License" 명시(1차 출처 확인). 따라서 ONNX 변환본의
자체 재배포(GitHub Releases 호스팅)에 라이선스 제약이 없다(고지 의무만 — NOTICE.txt).
인공 가우시안 노이즈에는 SCUNet 보다 보수적으로 반응하지만 실카메라 고ISO 노이즈가
본래 학습 도메인.

## 얼굴 마스킹 (Face masking — 검출 + 부위 파싱)

모델 두 개가 짝으로 동작한다. 검출기는 **어디를 자를지**만 정하고, 경계 정밀도는 전적으로
파싱이 만든다(박스가 곧 마스크가 아님). 파싱 모델은 정렬된 얼굴 크롭으로만 학습돼서 전체
사진을 넣으면 배경을 피부/머리카락으로 오분류한다 → 검출 → 크롭 → 파싱 순서가 필수.

### 1) 검출 — YuNet

- **사용 파일**: `models/yunet_face_2023mar.onnx` (232 KB)
- **출처(HF)**: [`opencv/face_detection_yunet`](https://huggingface.co/opencv/face_detection_yunet)
  (OpenCV Zoo 미러). 코드 상수: `face_seg.py` 의 `_DET_REPO`/`_DET_REV`/`_DET_SHA256`
- **실행**: onnxruntime 이 아니라 **`cv2.FaceDetectorYN`(OpenCV DNN)**. 이 ONNX 는 입력이
  640×640 **고정**이라 ORT 로 돌리려면 letterbox + 앵커프리 디코드 + NMS 를 직접 구현해야 하는데,
  OpenCV 가 그 전부를 C++ 로 갖고 있다. 그래서 `opencv-python-headless` 가 의존성에 있다.
- **2스케일 필수**: YuNet 은 네트워크 입력 기준 약 10~300px 얼굴로 학습됐다. 긴 변 640(s=0.25)만
  쓰면 프록시 얼굴 40~1200px 만 커버해 클로즈업을 놓친다 → 640 + 320 두 패스 교차 NMS.
  (실측: 2560 프레임의 954px 얼굴은 640 단독으로 미검출, 320 패스가 잡음)
- **속도**: 2패스 합쳐 프록시 1장당 ~60 ms (CPU)
- **라이선스**: **MIT** — 코드(MIT)와 충돌 없음

### 2) 부위 파싱 — SegFormer-B5 / CelebAMask-HQ

- **사용 파일**: `models/face_parsing_b5.onnx` (~340 MB, fp32)
- **출처(HF)**: [`Xenova/face-parsing`](https://huggingface.co/Xenova/face-parsing)
  = [`jonathandinu/face-parsing`](https://huggingface.co/jonathandinu/face-parsing) 의 ONNX export.
  코드 상수: `face_seg.py` 의 `_PARSE_REPO`/`_PARSE_REV`/`_PARSE_SHA256`
- **19클래스**: 0 background, 1 skin, 2 nose, 3 eye_g(안경), 4 l_eye, 5 r_eye, 6 l_brow, 7 r_brow,
  8 l_ear, 9 r_ear, 10 mouth, 11 u_lip, 12 l_lip, 13 hair, 14 hat, 15 ear_r(귀걸이),
  16 neck_l(목걸이), 17 neck, 18 cloth
- **UI 그룹**: 좌/우를 병합해 11개(Skin·Nose·Eyes·Brows·Glasses·Lips·Mouth·Ears·Hair·Hat·Neck).
  `cloth`(18)는 제외 — 크롭(얼굴 박스 1.9배) 밖까지 이어져 구조적으로 항상 일부만 잡힌다.
- **전처리**: sky_seg 와 동일 계열(/255 → ImageNet 정규화 → NCHW). 단 512×512 **정사각**
  (`preprocessor_config.json`) — 크롭이 정사각이라 왜곡 없음.
- **속도**: 얼굴당 ~0.8 s (CPU). 얼굴 상한 5개. 파싱 결과는 이미지당 캐시 → 부위 토글 ~10 ms.

#### 변형 (fp32 를 쓰는 이유 — 재조사 방지)

같은 repo 의 `onnx/` 에 `model_fp16.onnx`(172MB)·`model_quantized.onnx`(89MB)도 있지만 **둘 다
CPU EP 에는 부적합**하다: fp16 은 ORT 가 cast 노드를 끼워 넣어 결국 fp32 로 돌고, int8 은 동적
양자화라 `MatMulInteger` 융합이 안 돼 오히려 느려질 수 있으며 per-pixel 경계가 뭉갠다.
바꾸려면 `face_seg.py` 의 `_PARSE_NAME`/`_PARSE_URL`/`_PARSE_SHA256`/`_PARSE_BYTES` 네 상수만 교체.

#### 후처리 (하늘과 반대로 튜닝)

- 결정 곡선 `FACE_LO/HI = 0.35/0.65` — 하늘의 0.02/0.20 은 약확신 구름을 끌어올리려는 값이라
  얼굴에 쓰면 옆 부위로 번진다(입술 선택인데 턱까지 물듦).
- **구멍 채우기 안 함** — skin 마스크의 구멍을 메우면 눈·입이 삼켜진다(부위 분리가 존재 이유).
- 가이디드 필터는 **크롭 공간**에서 크롭 변의 2%. 프록시 기준(sky 의 0.012)을 쓰면 입술보다 커진다.
- 크롭 경계 6% 페더 — neck/긴 머리가 크롭 밖까지 이어져 모서리에서 잘리면 **직사각형 자국**이 남는다.
- 리사이즈/가이디드필터는 scipy 대신 cv2 (1898² 크롭 기준 0.45 s → 0.13 s).

#### 롤 정렬을 하지 않는 이유 (재조사 방지)

CelebAMask-HQ 가 정렬된 얼굴로 학습됐으니 눈 랜드마크로 크롭을 회전시키는 게 이론상 유리하지만,
**90° 누운 얼굴도 정렬 없이 제대로 파싱됐다**(실측). 게다가 크게 기울어진 얼굴에서는 랜드마크
자체가 부정확해(박스는 정확한데 두 눈 점이 한쪽에 몰림) 각도를 믿을 수 없다. 도입하지 않음.

#### 얼굴 선택을 인덱스로 저장하지 않는 이유 (재조사 방지)

여러 명 중 누구를 마스킹할지는 레이어의 `keys` 목록에 **정규화 중심좌표**(`face@0.412,0.318`)로
저장하고, 합성 시점에 현재 검출 결과와 최근접 매칭한다(`face_seg.match_faces`).

`detect_faces` 가 면적 내림차순으로 정렬하므로 "N번째 얼굴"이 안정적인 식별자처럼 보이지만,
**같은 프록시에서만** 그렇다. 실제로는 일상적인 재디코딩에서 순서가 흔들린다:

- **색온도 커밋** — 슬라이더를 놓으면 user_wb 로 재디코딩되어 `_seg_rgb8` 자체가 달라진다
  → YuNet 점수가 바뀌고 검출 개수·순서도 바뀔 수 있다
- **렌즈 보정 토글** — 기하가 비균일하게 변해 비슷한 크기의 두 얼굴 순위가 뒤집힐 수 있다

인덱스로 저장하면 "A를 골랐는데 조용히 B가 보정되는" 무증상 버그가 **색온도만 만져도** 발생한다.
좌표 방식은 순서 변경과 개수 변경 양쪽에 강하고, 허용 반경(`FACE_MATCH_TOL = 0.08`) 안에 없으면
가장 큰 얼굴로 폴백한다(빈 선택이면 마스크가 전부 0 이 되고 호출측이 '마스크 없음'으로 바꿔
레이어가 조용히 마스크를 잃는다).

검증: 검출 목록을 강제로 뒤집고 렌즈 보정을 토글해 재디코딩시켰을 때 마스크 중심 이동 0.0px.

`keys` 에 `face@` 가 하나도 없으면 **전체 얼굴**이다 — 얼굴 선택이 없던 버전의 사이드카가 그대로
호환되고, UI 의 'All' 버튼도 key 를 지우는 것으로 구현된다(별도 센티넬 불필요).

### 라이선스 (⚠️ 상업 배포 시 확인)

- 검출(YuNet): **MIT**
- 파싱: 업스트림(`jonathandinu/face-parsing`)이 **라이선스를 명시하지 않았고**, `nvidia/mit-b5`
  (NVIDIA 연구용)를 **CelebAMask-HQ** 로 파인튜닝한 것이다. CelebAMask-HQ 는 **비상업 연구 전용**.
  → 하늘 세그와 마찬가지로 상업 배포 시 교체 대상.

### 알려진 한계

- 천 마스크(KF94 등)를 **skin 으로 분류**한다 — CelebAMask-HQ 가 코로나 이전 데이터셋이라 해당
  클래스가 없다. 스킨 톤 보정 시 천까지 물든다(모델 한계, 우회 불가).
- 작은 얼굴일수록 마스크가 소프트하다(213px 얼굴 기준 >0.5 영역 평균 알파 0.845 = 실효 84%).
  피부는 눈·코·입 구멍이 뚫린 얇은 형태라 페더 밴드가 영역 대부분을 차지하기 때문.
  더 강하게 하려면 `FACE_GUIDED_R` 을 0.020 → 0.010 (0.90) / 0.005 (0.92) 로. 경계 밀착은 약해진다.
- `MAX_FACES = 5` 상한 — 6명 이상 찍힌 사진은 큰 얼굴 5명만 잡히고 나머지는 조용히 버려진다
  (UI 문구에 "Up to 5 largest faces" 명시). 파싱이 얼굴당 ~0.8s 라 둔 값이고, 올리려면
  `face_seg.MAX_FACES` 와 `main.MAX_FACE_SLOTS`(썸네일 프로바이더 슬롯)를 **함께** 올려야 한다.
  타일이 한 줄(패널 콘텐츠 폭 ~268px)에 들어가는 상한이기도 하다: 5×46 + 4×6 = 254px.
- 검출기 다운로드가 실패하면 그 사진에서는 썸네일 줄이 안 뜬다(부위를 체크하면 마스크 워커가
  스스로 재검출을 시도하므로 마스킹 자체는 동작). 실패를 '얼굴 없음'으로 캐시하지 않는다.

## 거리 범위 마스킹 (Depth masking — Depth Anything V3 Small)

Scene(의미)·Face(부위)에 이어 **거리** 축의 마스크 소스. 시맨틱 세그가 못 가르는 영역
("같은 나무인데 앞쪽만", "인물 뒤 배경 전체")을 near~far 범위로 고른다.

- **모델**: Depth Anything V3 **Small** — 단안 깊이(실제 depth 출력)
- **사용 파일**(하위 폴더): `models/depth_anything_v3_small/model.onnx` (640,691 B) +
  `model.onnx_data` (104,702,464 B) — 합 105,343,155 B, fp32
- **출처(HF)**: [`onnx-community/depth-anything-v3-small`](https://huggingface.co/onnx-community/depth-anything-v3-small)
  (원본: [ByteDance-Seed/Depth-Anything-3](https://github.com/ByteDance-Seed/Depth-Anything-3)).
  코드 상수: `depth.py` 의 `_REPO`/`_REV`/`_FILES`/`_SHA256`/`_BYTES`
- **⚠️왜 하위 폴더인가**: 이 모델은 ONNX 외부 데이터(2파일)를 쓰고, 데이터 파일 이름
  `model.onnx_data` 가 .onnx 프로토 안에 박혀 있다(ORT 가 모델과 **같은 디렉터리**에서 그 이름으로
  찾으므로 개명 불가). 플랫한 `MODELS_DIR` 에 그런 일반적인 이름을 두면 나중에 외부 데이터를 쓰는
  다른 모델과 충돌한다. `app_dirs.materialize` 가 `dirname(path)` 를 makedirs 하도록 한 줄 고쳤다.
  (부수효과: orphan 스캔은 `os.path.isfile` 로 걸러 하위 폴더를 건너뛴다 — 오탐은 없고, 대신 이
  폴더는 orphan 집계에도 안 잡힌다)
- **입력**: `/255` → ImageNet 정규화(sky_seg 와 같은 값) → **짧은 변 518**, 각 변 14의 배수
  (ViT 패치), bicubic. **다중 뷰 모델이라 입력이 `[B, num_images, 3, H, W]`** — 단일 이미지는
  뷰 축 1개를 끼운다. 출력도 4개(`predicted_depth`/`confidence`/`extrinsics`/`intrinsics`)라
  이름으로 골라야 한다(`confidence` 는 현재 미사용 — 저확신 영역 게이팅에 쓸 여지 있음).
- **출력**: `predicted_depth` = **실제 depth z(클수록 멂)** → 반전하지 않는다.
  **log z 공간**에서 1~99 퍼센타일 정규화해 '거리'(0=가까움, 1=멂)로 쓴다(아래 비교표 참조).
- **실행**: DirectML 우선 → CPU 폴백. `device_id` 는 `ai_denoise_device.json` 재사용.
- **속도**(RTX 3050 Ti / 2560px 프록시): 추론 CPU 1.02s · **DML 0.50s(2.0×)**, +정제 0.17s
  = 이미지당 **~0.75s**. 범위 슬라이더 재조합은 밴드패스 46ms + uint8/QImage 30ms = **~76ms**
  (재추론 없음). 이 값이 UI 갱신 주기를 정한다 — `docs/depth_masking.md` §3-1(스로틀) 참조.
- **초기 범위**: 고정 상수가 원리적으로 맞을 수 없어(정규화 후에도 분포 평균이 0.478~0.679로
  흩어진다) 켜는 순간 히스토그램 1-D Otsu 로 시드한다(`auto_range`, 기본은 배경 쪽).
  히스토그램은 stride 4 표본 — 전체 40.7ms vs 3.4ms 이고 **경계는 6장 모두 완전히 동일**했다.

### 파이프라인 상의 위치

거리 맵은 `haze.py` 의 t-맵과 **배관이 동일**하다(소형 단채널 → 프록시 업샘플 → 정제). 다만
셰이더 바인딩이 아니라 **기존 마스크 경로**를 탄다: `Controller._depth_map` 캐시(이미지당 1회,
레이어 5개 공유) → `depth.compose_mask` → 기존 `np.maximum` 합집합 → `SkyMaskProvider`(binding
9/13~16) 및 `pipeline.render_full(sky_masks=)`. **셰이더·pipeline·coeffs 변경 0.**

깊이 범위는 레이어의 `keys` 목록에 **`depth@near,far,feather` 한 항목**으로 저장한다
(`face@cx,cy` 와 같은 방식). 덕분에 사이드카 저장·undo·재오픈 복원이 기존 keys 직렬화에
그대로 얹혀 새 필드가 필요 없다. 소수 4자리 고정 — QML `toFixed(4)` == `depth.range_key`
여야 `setMaskClasses` 의 no-op(같은 값 재요청 차단)이 동작한다.

### 후처리 (하늘과 다르게 튜닝)

- **가이디드 필터를 마스크가 아니라 깊이 맵에 1회 적용**한다(sky_seg 는 확률 합산 뒤 정제).
  정제 결과가 near/far 와 무관하므로 미리 해두면 슬라이더 드래그가 smoothstep 비용만 남는다.
  실측: 프록시 전체 정제 155ms vs 밴드패스 46ms — 매 프레임 정제는 불가능한 비용.
- **cv2 판 `_guided`/`_resize`(face_seg)를 재사용**한다. scipy `uniform_filter` 는 같은 연산이
  612ms, cv2 `boxFilter` 는 155ms(**4.0×**, 수치 동일 — max|diff| 1.2e-7).
- `GUIDED_EPS = 1e-3` — 하늘의 `1e-4` 보다 크게. 깊이는 매끄러운 신호라 eps 가 작으면
  텍스처가 깊이 맵에 배어든다.
- **구멍 채우기 안 함** — 깊이 밴드는 구멍이 정상이다(원거리 밴드 안에 가까운 나뭇가지가
  뚫려 있는 게 맞음). 얼굴 파싱이 구멍을 안 메우는 것과 같은 이유.

### ⚠️ 정규화 공간이 모델 선택보다 중요하다 (재조사 방지)

**처음에 V2-small 을 골랐다가 V3-small 로 바꿨다. 첫 비교가 틀렸기 때문이다** — V2 출력은
disparity, V3 출력은 실제 depth 인데 양쪽에 똑같이 **선형** 퍼센타일 정규화를 걸었다. z 는 원거리
꼬리가 길어 선형 정규화하면 근거리가 하위 몇 %로 압축된다. 그래서 "V3 는 깊이가 이진에 가깝게
압축된다"고 관찰한 것은 **모델 성질이 아니라 전처리 산물**이었다. 교훈: 출력의 물리량(disparity
vs depth)이 다르면 같은 정규화를 쓸 수 없다.

정규화 공간별 분포 엔트로피(64빈, 6.00 만점 — 높을수록 범위 슬라이더가 쓸 만하다):

| 방식 | 평균 | **최저(최악 케이스)** | 6장 승패 |
|---|---|---|---|
| V2 선형 disparity | 5.23 | 3.98 | 4승 |
| V2 `-log(disparity)` | 4.38 | 2.89 | 기각 — disparity 가 원거리에서 0 에 붙어 `-log` 가 발산, 하한 클램프가 거대한 스파이크를 만든다 |
| **V3 log z (채택)** | **5.37** | **4.78** | 2승 |
| V3 선형 z | 4.55 | 4.22 | 근거리 압축 |
| V3 1/z (disparity) | 5.12 | 4.93 | V2 와 같은 원거리 포화 |

**평균이 아니라 최악 케이스로 골랐다.** V2 는 대부분의 사진에서 근소하게 낫지만 **원거리 풍경에서
구조적으로 실패한다**(disparity 포화 → 산맥 계조가 뭉치고, 산을 고르면 가까운 나무 난간까지
물든다. 엔트로피 3.98). "먼 산맥만 어둡게"가 이 기능의 대표 사용 사례라 이 실패는 치명적이다.
V3 는 어떤 사진에서도 4.78 이하로 떨어지지 않는다.

**대가**: V3 는 근거리·실내 장면에서 덜 일관적이다 — 전경 화단까지 번지거나(오두막: 커버리지
0.652 vs V2 0.412) 반대로 배경 벽을 놓친다(유모차: 0.219 vs 0.386). 6장 승패는 V2 4 : V3 2 다.
가족·실내 사진 위주라면 V2 가 나을 수 있다는 뜻이므로, 되돌릴 때는 아래를 함께 바꿔야 한다:
`_REPO`/`_REV`/`_FILES`/`_SHA256`/`_BYTES`, **입력 뷰 축 제거**, **출력 반전 부활**(V2 는
disparity 라 `1 - pct(d)`), 하위 폴더 → 플랫 단일 파일.

V2-small 참고값: `onnx/model.onnx` 99,060,839 B,
sha256 `afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c`,
rev `4472b7362082ad9968fee890ca0f1e5aca36b93d`.

### ⚠️ 왜 메트릭 깊이·디퓨전이 아닌가 (재조사 방지)

- **메트릭 깊이**(ZoeDepth / Metric3D v2 / UniDepth / Apple Depth Pro): 사진 보정에는 절대
  거리가 필요 없다(마스크·그라데이션은 상대 깊이로 충분). Depth Pro 는 Apple 연구 라이선스라
  상업 배포에도 부적합.
- **디퓨전 계열**(Marigold / Lotus): 경계가 가장 깨끗하지만 장당 수십 초 → 이 앱의 인터랙션
  모델과 맞지 않는다.
- **입력 해상도 상향**: 짧은 변 686 으로 올려도 품질 이득이 없고 추론이 2.5~3× 느려진다
  (실측 0.5s → 1.9~2.9s). 오히려 얇은 구조물 분리가 518 보다 나빠졌다 → 518 고정.

### 라이선스

**Apache-2.0** — 하늘 세그(NVIDIA 연구용)·얼굴 파싱(CelebAMask-HQ 비상업)과 달리 **상업 배포
제약이 없다**(코드 MIT 와 충돌 없음). V2·V3 모두 **Small 만** Apache-2.0 이고 Base/Large 는
CC-BY-NC-4.0 이므로 **상향 교체 시 라이선스가 바뀐다** — 주의.

### 알려진 한계

- 출력은 **이미지별로 정규화된 상대 거리**다. 절대 스케일이 없어 near/far 값이 사진 간에 그대로
  옮겨가지 않는다(copy/paste-edits 로 붙이면 다른 영역이 잡힘). UI 에 문구로 고지한다.
- 유리·수면·거울 같은 투명/반사면은 깊이가 물리적으로 모호해 결과가 장면 의존적이다
  (수족관 사진에서 확인).
- 위에서 내려찍은 평면적인 장면(예: 욕조 속 아이)은 깊이 분산이 작아 범위 마스크의 분리력이
  약하다 — 이때는 Scene/Face 탭이 맞는 도구다.

## 사진 캡션 (Photo caption — Florence-2)

- **모델**: Microsoft Florence-2-base-ft (비전-언어, 영어 캡션 생성)
- **사용 파일** (fp32, 총 ~1.1GB — 최초 캡션 생성 시 `caption.ensure_model()` 자동 다운로드):
  `florence2_vision_encoder.onnx`(367MB) / `florence2_embed_tokens.onnx`(158MB) /
  `florence2_encoder_model.onnx`(173MB) / `florence2_decoder_model.onnx`(388MB)
  + 토크나이저/설정 `florence2_vocab.json`·`florence2_merges.txt`·
  `florence2_preprocessor_config.json`·`florence2_generation_config.json`
- **출처(Hugging Face)**: [`onnx-community/Florence-2-base-ft`](https://huggingface.co/onnx-community/Florence-2-base-ft)
  (transformers.js 용 사전 export ONNX). 코드 상수: `caption.py` 의 `_REPO` / `_FILES`.
- **입력**: RAF 내장 JPEG 프리뷰를 EXIF 회전 반영 후 768×768(비율 무시)로 축소.
  토크나이저는 GPT-2식 byte-level BPE 를 `caption.py` 가 직접 구현(의존성 추가 없음).
- **실행**: CPU EP, greedy, 무캐시 디코더 — 짧은 캡션 기준 장당 ~2.7초(비전 1.3s+생성 1.4s).
  가속 여지: `decoder_model_merged.onnx`(KV-cache) + DirectML EP, int8/q4 변형(용량 ~1/4).
- **라이선스**: **MIT** (모델 카드 명시) — 코드(MIT)와 충돌 없음.
- 캡션은 폴더당 `.filmrawsterycaptions.json` 사이드카에 저장(영어; 앱 UI 에서 수정 가능).
