# 하늘 자동 검출 & 마스킹 (Sky Detection & Masking)

라이트룸 "Select Sky" 식 **하늘 자동 선택 + 영역별 로컬 조정**. ML 시맨틱 세그멘테이션(ONNX)으로
하늘 마스크를 만들고, 그 영역에만 노출·색·로컬대비 조정을 적용한다. 프리뷰(GPU 셰이더)와
Export(numpy)가 동일 결과를 내도록 양쪽에 같은 수식을 유지한다.

관련 파일: `sky_seg.py`(엔진) · `shaders/adjust.frag`(프리뷰/GPU export) · `pipeline.py`(CPU export) ·
`main.py`(프로바이더/Controller) · `ui/Main.qml`(Masking 패널 UI).

---

## 1. 전체 파이프라인

```
프록시 QImage(헤드룸 카메라네이티브)
  └─(중성 display sRGB 디코드: as-shot WB, 노출0, filmic)──────────────┐
                                                                        ▼
            display-sRGB RGB(uint8)  ──►  sky_seg.segment_sky()
                                              │
   ┌──────────────────────────────────────────┘
   ▼
 [1] 종횡비 유지 리사이즈(긴 변 1024, 32배수)  →  ImageNet 정규화
 [2] SegFormer-B2 ONNX 추론 → 150클래스 logits → softmax (전체 캐시)   ── infer_softmax()
 ─── 이하 후처리(추론 없이 재조합) ────────────────────────────────── compose_mask()
 [3] 선택 클래스 확률 합산(복합 마스크 = 선택 클래스 합집합) → 입력 해상도로 업샘플
 [4] 결정 곡선 smoothstep(0.02, 0.20)  → 약확신 영역까지 solid
 [5] binary_fill_holes  → 에워싸인 구멍(구름 등) 채움
 [6] guided filter(휘도 가이드)  → 나뭇가지/건물 경계 밀착
   ▼
 soft alpha 마스크 float32 [0,1] (프록시 해상도)
   │
   ├─► SkyMaskProvider(image://skymask, Grayscale8) → 셰이더 binding 9 (프리뷰/GPU export)
   └─► Controller._sky_mask(numpy 보관) → pipeline.render_full(sky_mask=) (CPU export)
```

핵심 원칙: **마스크 입력은 "중성 display sRGB"**(노출 0, as-shot WB, filmic 적용)로 만들어 화면에
표시되는 프록시와 픽셀 정렬한다. 임베드 JPEG는 방향(Orientation)이 안 맞아 부적합 →
`Controller._sky_input_rgb()` 가 `_native_to_scenelinear` + `wb.filmic`로 프록시를 직접 변환해 사용.

**라이브 갱신(추론 1회 캐시)**: `infer_softmax()` 결과(150채널 softmax)를 Controller가 캐시
(`_seg_probs`/`_seg_guide`/`_seg_size`, 이미지당 1회, 새 이미지에 무효화)하고, 체크박스 토글마다
`compose_mask()`만 다시 돌려(재추론 없이) 마스크를 재조합한다. 무거운 추론은 1회뿐.

**복합 마스킹(멀티클래스)**: 추론 출력이 150클래스 softmax 전체라, 원하는 클래스들의 확률을
**합산**하면(softmax 합 = "픽셀이 선택 클래스 중 하나일 확률") 여러 영역을 한 마스크로 합칠 수 있다.
선택 가능한 묶음은 `sky_seg.MASK_GROUPS`(key·label·ADE인덱스 목록, 자유 편집):

| 그룹 | ADE 인덱스 |
|------|-----------|
| Sky | 2 |
| Vegetation | 4, 9, 17, 66 (tree·grass·plant·flower) |
| Building | 1, 25, 48 (building·house·skyscraper) |
| Ground | 6, 11, 13, 29, 46 (road·sidewalk·earth·field·sand) |
| Water | 21, 26, 60, 109, 128 (water·sea·river·pool·lake) |
| Mountain | 16, 34 (mountain·rock) |
| Person | 12 |

UI(Masking 패널 "Create Mask")는 이 그룹들을 체크박스로 노출하고, 체크 조합을
`Controller.setMaskClasses(keys)` → `class_ids_for()` → `compose_mask()` 로 라이브 합성한다.
⚠️ person 등 미세 경계 클래스는 영역만 대략 잡힘(정밀 피사체는 전용 매팅 모델 영역).

---

## 2. 모델: SegFormer-B2 (ADE20K)

| 항목 | 값 |
|------|-----|
| 모델 | SegFormer-B2, ADE20K 150클래스 파인튜닝 |
| 출처 | `Xenova/segformer-b2-finetuned-ade-512-512` (transformers.js 사전 export ONNX) |
| 다운로드 | 최초 호출 시 `models/segformer_b2_ade.onnx`(~105MB) 자동 다운로드(원자적 tmp→rename) |
| 런타임 | `onnxruntime` (CPU), torch/transformers **불필요** |
| 하늘 클래스 | `id2label["2"] == "sky"` |
| 전처리 | /255 → ImageNet 정규화(mean `[.485,.456,.406]`, std `[.229,.224,.225]`), NCHW |
| 출력 | logits `[1,150,H/4,W/4]` (동적 입력 크기 지원) |

### 모델 크기 선택 (B0 → B2, 실측)

DSCF8012(채광창 너머 구름 하늘)에서 B0가 하늘을 못 잡아 상향. 같은 Xenova 저장소에 B1/B2/B4/B5
전부 ONNX로 존재.

| 모델 | 파라미터 | 하늘 검출 | 얼룩 | 추론(proxy) | 크기 |
|------|---------|-----------|------|-------------|------|
| B0 | ~3.7M | 24% | 9.7% | 0.3s | 14MB |
| **B2 (채택)** | ~27M | **31%** | **3.2%** | ~1.2s | 105MB |
| B5 | ~85M | 31% | 4.4% | ~3.9s | 324MB |

→ **B2가 균형점**. B5는 B2와 품질 거의 동일한데 3배 느리고 3배 무거워 비권장. 모든 변형이
sky=2·ImageNet 동일이라 `sky_seg.py`의 `_REPO`/`MODEL_PATH` 한 줄 교체로 바꿀 수 있음.

---

## 3. 검출 품질 튜닝 (단계별 개선 + 교훈)

이 기능의 검출 품질은 아래 4가지를 차례로 잡으면서 완성됐다. 각 단계는 **실제 마스크 오버레이를
눈으로 비교**해 결정했다(수치 지표만으론 판단 불가).

### 3.1 입력 해상도 — 정사각 찌부림 금지 (`INPUT_LONG_EDGE = 1024`)

- 입력을 정사각 512×512로 강제 리사이즈하면 **종횡비 왜곡 + 저해상도**라 가는 나뭇가지·전선·
  건물 경계가 뭉갠다.
- ONNX 입력이 동적 크기라 **종횡비 유지 + 긴 변 1024**(각 변 32의 배수=SegFormer stride)로 넣는다.
- 실측: 512² → 1024 종횡비로 바꾸자 나뭇가지 경계가 또렷하게 분리됨. 1536은 1024와 큰 차이
  없으면서 3배 느림(`_infer_size()`).

### 3.2 결정 곡선 — 약확신 하늘 포함 (`MASK_LO=0.02, MASK_HI=0.20`)

- 모델은 **채광창 유리 너머 밝은 구름**을 `windowpane`(클래스8)과 혼동해 sky 확률을 **0.05~0.3**로
  낮게 준다(argmax 측정 확인). 임계값이 높으면(예전 0.20/0.55) 구름이 마스크 구멍이 됨.
- `_smoothstep(MASK_LO, MASK_HI)`로 확률을 정형 → 임계값을 **낮춰** 약확신 하늘까지 solid(1.0)로
  포함. 진짜 비하늘(램프·건물·지면)은 prob≈0이라 낮춰도 제외 유지.
- 부수효과: 구름이 solid가 되면서 guided filter의 '밝은 영역 억제'(3.4)도 자연 해소.
- ⚠️ 더 낮추면 밝은 벽 등 **오검출** 위험↑.

### 3.3 구멍 채우기 (`binary_fill_holes`, `MASK_FILL_T=0.5`)

- 하늘로 **완전히 에워싸인** 구멍(하늘 속 작은 비하늘 영역)을 solid로 채움.
- 나무 줄기·프레임처럼 바깥/지면과 이어진 건 에워싸이지 않아 안 채워짐 → **안전**.
- ⚠️ 경계에 닿아 열린 구름에는 무력(그래서 3.2의 임계값 하향이 주된 구름 해법).

### 3.4 Guided Filter — 경계 정제 (`GUIDED_RADIUS_FRAC=0.012, GUIDED_EPS=1e-4`)

- He et al. guided filter(scipy `uniform_filter` 박스필터 기반, **새 의존성 없음**). 원본 휘도를
  가이드로 마스크 경계를 실제 엣지(나뭇가지·건물)에 밀착시킨다.
- ⚠️ **함정**: 휘도를 가이드로 쓰면 하늘 내부에서 "파란하늘=어두움·선택 vs 흰구름=밝음" 음의
  상관을 학습해 **밝은 구름을 끌어내린다**. → 3.2로 구름을 미리 solid(평탄 1.0)로 만들면
  평탄 영역엔 억제가 없어 해소된다(guided filter는 평탄 입력을 그대로 통과).

### 3.5 버린 접근 (기록)

- **solid-core + erosion**, **morphology closing**: 실패. 구름이 경계에 열려 있어 fill/closing이
  안 먹고, 오히려 **나무 가지 디테일을 뭉갠다**. → 채택 안 함.
- **밝기 게이팅으로 guided 억제 방지**: 효과 미미(구름 코어 확률이 근본적으로 낮아서). →
  3.2 임계값 하향이 더 단순·효과적.

---

## 4. 마스킹 조정 (9개)

마스크 영역에만 적용되는 로컬 조정. **전역 도구와 대응되는 항목은 전역과 같은 단계·같은
수식**에서 강도만 마스크로 게이팅(`전역강도 + 마스크강도·m`)해 전역 조절과 동일하게 반응한다:

| 조정 | 적용 단계/수식(요지) | 비고 |
|------|-----------|------|
| Exposure | **프론트엔드(0단계)**: `lin × 2^(exposure + skyExp·m)` | 전역 노출과 동일한 진짜 stop — filmic 하이라이트 롤오프 적용 |
| Highlights/Shadows | **tone_zones(3단계)**: `hi + skyHi·m`, `sh + skySh·m` 강도 합산 | 전역과 동일한 영역 톤맵(국소 평균 휘도 lb·넓은 범위) |
| Dehaze | **디헤이즈(6단계)**: `dehaze + skyDehaze·m` 강도 합산 → `dehazeApply()` | 전역과 동일(LUT/커브 전, '+'=DCP 물리 공유, 픽셀별 부호 분기) |

나머지는 셰이더 9.7 단계(색보정 끝난 **display sRGB, 비네팅 직전**)와 `pipeline._sky_adjust`
동일 수식(마스크 invert 는 양쪽 모두 마스크에 1회 베이크):

| 조정 | 수식(요지) | 비고 |
|------|-----------|------|
| Temp | `r*=(1+0.20·m)`, `b*=(1-0.20·m)` | +따뜻 (display 근사) |
| Tint | `g *= (1 − skyTint·0.15·m)` | +마젠타 / −녹 (Temp와 함께 WB 완성) |
| Texture | `+= (s0 − texBlur)·1.6·m` | 중주파 디테일 |
| Clarity | `+= (s0lum − claBlurlum)·0.8·mid·m` | 중간톤 로컬대비 |
| Saturation | `mix(L, rgb, 1+skySat·m)` | |

> 과거에는 Exposure/Hi/Sh/Dehaze 도 9.7 display 공간(LUT/커브 뒤)에서 적용했으나, 전역 조절과
> 반응이 달라(노출: 감마로 ~2.4배 강함·하드클립 / hi·sh: 픽셀휘도 근사로 밋밋 / dehaze: LUT 뒤
> 입력 차이) 전역 단계 합산으로 이전됨.
> 검증: 마스크=전체(1.0)일 때 마스크 조절 == 전역 조절 (±1 LSB — 톤모델/DCP/음수 방향 모두).

⚠️ **로컬대비 3종(Texture/Clarity/Dehaze)의 base = 중성 dispSrc(`s0`) + `texBlur`/`claBlur`**
(전역 텍스처/클래리티와 동일 base). CPU export는 셰이더 블러 텍스처에 대응해 `neutral_disp`의
로컬대비(`nd_texhi`/`nd_lc`)를 스트립 루프 전 1회 사전계산해 슬라이스로 전달한다.

⚠️ 계수(Temp 0.20, Tint 0.15, Dehaze 강도 등)는 **라이트룸 비교 튜닝 대상**(프로젝트 목표 규칙).

---

## 5. 통합 & 프리뷰=Export 정합

- **셰이더 binding 9 `skyMask`**: `SkyMaskProvider`(`image://skymask`, Grayscale8). 마스크 없을 땐
  1x1 검정 → 샘플러 항상 유효(`m=0`, 안전).
- ShaderEffect **2개(`pipe` 프리뷰 / `pipeFull` GPU export)** 모두 `skyMask` + 9개 uniform 바인딩.
  `skyShowMask`(선택영역 적색 오버레이)는 `pipe`만(프리뷰 전용), `pipeFull`=0.
- **CPU export(`render_full`)**: `sky_mask=` 인자로 프록시 마스크를 받아 풀해상도로 zoom 업샘플 후
  `_sky_adjust` 적용(color_grade 뒤·비네팅 앞). sky 파라미터는 dict `sky`로 전달.
- **세션 전용(미저장)**: 하늘 조정은 사이드카(`editParams`)에 저장 안 함 → 파일 로드/Reset마다
  `win.resetSky()`로 초기화(마스크는 재생성 안 하므로 슬라이더만 남으면 무의미 → 함께 클리어).

### UI (Masking 패널)

- 우측 세로 셀렉터 3번째 아이콘 ◉ → StackLayout index2(Edit 안 섹션 아님, 독립 패널).
- **Create Mask**: 클래스 체크박스(`controller.maskGroups` Repeater, 복합 선택) + `Clear`.
  토글 → `win.toggleMaskKey` → `controller.setMaskClasses(keys)` 라이브 합성. Show mask / Invert 체크.
- **Adjustments**: 9 슬라이더(-1..1, 더블클릭=리셋).
- 진행 상태: 이미지 위 스피너 오버레이(`controller.skyBusy` → "Detecting sky…").
- UX: 선택 완료(`skySelected` 시그널)→마스크 오버레이 자동 표시 / 슬라이더 `onMoved`→오버레이 off.
- 오버레이는 **마스킹 패널이 활성일 때만** 표시(`showSkyMask && activePanel === 2` 게이트).
  체크 상태는 보존되므로 다른 패널에 다녀와도 다시 보인다 — 끄는 게 아니라 숨기는 것.
  (이전엔 체크만 보고 항상 표시돼 Edit/Crop 패널에서도 빨간 마스크가 남는 버그가 있었다.)

---

## 6. 튜닝 상수 (모두 `sky_seg.py` 상단)

| 상수 | 현재값 | 의미 |
|------|--------|------|
| `INPUT_LONG_EDGE` | 1024 | 추론 입력 긴 변(↑=경계 디테일↑·속도↓) |
| `MASK_LO` / `MASK_HI` | 0.02 / 0.20 | 결정 곡선(↓=하늘 더 포함, 너무↓=오검출↑) |
| `MASK_FILL_T` | 0.5 | 구멍 채우기 판단 임계 |
| `GUIDED_RADIUS_FRAC` | 0.012 | guided filter 반경(짧은 변 비율) |
| `GUIDED_EPS` | 1e-4 | guided filter 엣지 밀착(작을수록 밀착) |

조정 계수(Temp/Tint/Dehaze 등)는 `adjust.frag`(9.7 블록)와 `pipeline._sky_adjust`에 **동시** 존재 —
한쪽 수정 시 반드시 양쪽 수정 + 셰이더 재컴파일.

---

## 7. 검증 방법 (헤드리스)

GPU 셰이더 렌더는 헤드리스로 못 보지만, 마스크 자체와 CPU export는 numpy라 헤드리스 검증 가능.

- **마스크 품질**: 프록시→중성 display sRGB→`segment_sky` → 마스크를 오버레이/이진(초록) PNG로
  저장해 눈으로 확인. ⚠️ 적색 반투명 오버레이는 흰 구름 위에서 분홍빛이라 "안 잡힌 듯" 보임 →
  **이진 오버레이(m>0.5 단색)**로 봐야 정확.
- **조정 정합**: `pipeline.render_full(..., sky_mask=mask)`로 각 슬라이더를 켜고, **마스크 영역
  평균 변화 vs 비마스크 영역(Δ≈0)** 으로 국소성 확인.
- **QML 로드**: `QT_QPA_PLATFORM=offscreen`으로 엔진 로드 후 `engine.warnings` 0 확인.
- 테스트 이미지: 기본 샘플 **DSCF1039는 하늘 없음**(0% 정상). 구름 하늘 = `128_FUJI/DSCF8012.RAF`,
  나뭇가지 = `ids2025/DSCF7127.RAF`.

---

## 8. 한계 & 향후

- **오검출 위험**: 임계값을 낮춰 약확신 하늘을 포함하는 만큼, 밝은 벽/수면 등을 하늘로 잡을
  여지가 있다. 보이면 `MASK_LO`를 약간 올리거나(예 0.05) 케이스별 튜닝.
- **모델 한계**: 매우 밝은 구름 코어(유리 너머 등)는 sky 확률이 근본적으로 낮아 잔여 누락 가능.
- **향후 후보**:
  - **수동 브러시 add/subtract** (라이트룸식 마스크 보정) — 자동 검출 보완의 정식 해법.
  - **다른 선택 방법** (Select Object / Subject / Brush) — Masking 패널 "Create Mask"에 버튼 추가,
    Controller/셰이더를 다중 마스크(마스크 리스트 + 활성 마스크)로 일반화. 현재 내부 식별자
    (`selectSky`/`skyBusy`/`skyExp` 등)는 sky 단일 마스크 기준이라 이때 함께 리팩터.
  - 배포 시 모델 번들: PyInstaller spec에 `models/*.onnx` 포함(또는 b2 quantized ~27MB/fp16 ~52MB로
    축소 옵션 — 품질 약간 저하 가능).
