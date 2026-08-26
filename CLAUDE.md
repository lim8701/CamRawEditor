# RAW Editor (Fujifilm RAF) — 프로젝트 가이드

PySide6 + QML + GPU 셰이더 기반 RAW(.RAF) 현상/보정 에디터. 후지 전 기종 RAF 지원
(색 매트릭스/WB/렌즈 보정 모두 파일 메타데이터 기반 — 기종 등록 불필요. 주 개발 기준: X100V).

⚠️**이 파일은 매 세션 컨텍스트에 통째로 올라간다 — 가볍게 유지한다.** 측정·경위·기각 기록 같은
상세는 `docs/` 로 분리하고 여기엔 **요약과 포인터만** 둔다(색인: `docs/README.md`).

## 커밋 규칙

- 커밋 메시지에 **`Co-Authored-By` 항목을 넣지 않는다.**
- 커밋 메시지는 **영문으로 작성한다.** (대화는 한글, 커밋 메시지만 영문)

## 개발 원칙

- **오버엔지니어링 지양**: 요구된 것만 가장 단순하게 구현한다. 불필요한 추상화·범용화·미래
  대비 코드를 피하고, 필요해질 때 추가한다.

## 목표 (가장 중요)

**물리적으로 정확한 알고리즘을 따르는 것을 우선으로 하면서, 그 위에서 Adobe Lightroom이 내는
느낌/반응(세부 파라미터·시각적 결과)을 따라간다.** 즉 기반 알고리즘은 올바른(물리/색과학적으로
타당한) 방식으로 구현하고, 강도·곡선·체감은 라이트룸과 비교해 튜닝한다.

- 두 목표가 충돌하면: 먼저 **올바른 알고리즘**으로 구현하고, 계수/곡선으로 라이트룸 느낌에 맞춘다.
  단순 흉내(작위적 근사)는 정식 구현 전의 **임시(stopgap)** 로만 둔다.
- **디헤이즈는 하이브리드**: `+` 방향 = DCP 물리 복원(haze.py 가 이미지당 t-맵/대기광/conf 추정,
  셰이더·pipeline 이 I=J·t+A(1−t) 역산 + 잔여 톤모델 DEHAZE_RESID), `−` 방향 = 흰 베일 톤 모델
  (물리에 역이 없음). 어두운 장면은 conf→0 으로 톤 모델 폴백(과거 DCP 가 야경에서 파탄났던
  문제의 가드). t-맵은 중성 베이스에서 추정 → 슬라이더와 무관, 드래그 실시간.
  전역(6단계)과 마스킹 로컬(9.7단계, 강도×마스크)이 **같은 코어를 공유**(셰이더 dehazeApply
  == pipeline._dehaze_apply) — 한쪽만 고치면 안 됨.
- 각 효과의 **계수(강도)** 는 라이트룸과 나란히 비교하며 계속 튜닝하는 값이다(아래 표 참조).
  사용자 피드백("너무 강하다/예민하다")이 오면 해당 계수를 조정한다.
- 슬라이더 범위 `-1..1` ↔ 라이트룸 `-100..+100` 대응. ±1에서 "강하지만 비상식적이지 않게",
  ±0.2에서 "미묘하게".

## 실행 / 환경

- 전용 venv 사용:
  ```
  cd C:\California\TEST36\CamRawEditor
  .\.venv\Scripts\python.exe main.py
  ```
  (macOS: `.venv/bin/python main.py` — 패키징은 `## macOS 패키징` 참조)
- venv = Python 3.13. 의존성: `requirements.txt` (PySide6, rawpy, numpy, scipy).
- 시작 동작: 사진을 자동 로드하지 않고 **폴더만 탐색기에 연다**(마지막 탐색 폴더 복원 >
  개발 샘플 폴더 > Pictures 순). 개발 샘플 상수 `DEFAULT_RAF = C:\Pic\x100v\128_FUJI\DSCF8035.RAF`
  는 그 부모 폴더를 여는 용도로만 쓰임(자동 로드 X). 테스트 시 사진은 탐색기에서 더블클릭.

## 셰이더 컴파일 (필수)

`shaders/*.frag`(adjust, blur)를 수정하면 **.qsb 로 재컴파일**해야 반영된다. 앱이 시작 시
`ensure_shader()`로 **mtime 비교 후 자동 재컴파일**하므로(번들 qsb 사용), 보통은 그냥 앱을
다시 실행하면 된다. 수동 컴파일 시:
```
.venv/Lib/site-packages/PySide6/qsb.exe --glsl 120,150,300es --hlsl 50 --msl 12 -o shaders/adjust.frag.qsb shaders/adjust.frag
```
⚠️ **`pyside6-qsb.exe`(console-script 래퍼)는 절대경로가 박혀 있어 폴더 이동/rename 시 깨진다**
(에러 메시지 없이 exit 1). 위처럼 **번들 `PySide6/qsb.exe`** 를 직접 쓰는 게 안전하다.
`ensure_shader()`도 번들 qsb를 우선 사용하도록 돼 있다. (venv 자체를 옮겼다면 console-script
들이 전부 깨지니, 깔끔히 하려면 venv 재생성 권장.)

## 검증 방법

- **QML 로드/경고**: `QT_QPA_PLATFORM=offscreen` 으로 엔진 로드 후 `engine.warnings` 수집 →
  경고 0 확인. (예시는 기존 커밋/대화 참조)
- **수치 검증**: `pipeline.render_full(...)` 로 export 결과 배열을 만들어 평균밝기/채널비 등으로
  효과 방향·강도를 확인(헤드리스 가능).
- **GUI 인터랙션(드래그/더블클릭/실시간 미리보기)은 헤드리스로 검증 불가** → 사용자가 직접 실행해
  확인. 마우스 좌표 기반 QTest는 오프스크린에서 레이아웃이 안 잡혀 신뢰 불가(과거 확인됨).
- ★**새 파라미터를 넣었으면 프리뷰·CPU export·GPU export 를 나란히 재서 같은 값인지 확인**할 것
  (아래 `### ★ 렌더 경로`). 함수를 직접 부르는 테스트는 배선 누락을 못 잡는다.
- ★⚠️**`.qml` 파일을 새로 만들면 `FilmRawstery.spec` 의 `QML` 목록에 등록**할 것. 소스 실행은
  같은 폴더라 그냥 되고 **배포본만 깨진다**("EditedBadge is not a type" → 메인 창이 아예 안 뜸).
  한 줄 검사: `ui/*.qml` 집합 == spec 의 `QML` 집합.

## 아키텍처

```
RAF ─rawpy(절대 Kelvin WB, auto-bright OFF, half_size)─> 프록시 QImage(max_edge 2560)
   │  (wb.py: Planckian+daylight 앵커로 Kelvin→user_wb, as-shot 추정)
   ▼
QML ShaderEffect 파이프라인 (프록시 해상도 FBO에 렌더 → 화면크기로 스케일 표시)
   순서: 노출 → **미스트(scene-linear, filmic 앞)** → WB프리뷰게인 → 톤영역(hi/sh/wh/bl)
        → 텍스처/클래리티/디헤이즈 → 필름시뮬 3D LUT → 대비 → 톤커브 → 그레인 → 비네팅
   ▼
화면(프록시·실시간 GPU)  /  Export(pipeline.py: 풀해상도 numpy, 동일 단계 재현)
```

### 핵심 설계 결정

- **처리 해상도 ≠ 표시 해상도**: 파이프라인은 프록시(~2560px) 고정 FBO에서만 렌더하고
  ShaderEffectSource로 화면 크기에 스케일. → GPU 부하가 모니터 해상도와 무관(외장 4K 대응).
- **WB는 하이브리드**: 절대 Kelvin은 디코딩(rawpy user_wb)이 담당(정확). 슬라이더 드래그 중에는
  셰이더가 baked→target 상대 게인으로 실시간 프리뷰, 손 떼면(onPressedChanged !pressed)
  재디코딩 확정 → 게인 (1,1,1) 수렴(이중적용 없음). 드래그 프리뷰는 display-space 근사라
  극단 색온도에서 ~10% 오차, 커밋 시 정확값 스냅.
- **로컬 대비(텍스처/클래리티)**: 멀티패스 분리형 가우시안 블러(`blur.frag`). 텍스처=작은반경
  풀해상도, 클래리티=큰반경 1/4 다운샘플. 블러 체인은 srcImage 에만 의존 → 로드 시 1회 계산
  (슬라이더 조작 시 재계산 안 함). 메인 셰이더가 texBlur(b4)/claBlur(b5) 샘플링.
- **프리뷰/Export 일치 원칙**: 셰이더와 pipeline.py 는 같은 단계·수식·계수를 유지해야 한다.
  한쪽 수정 시 반드시 양쪽 모두 수정. (export 공간반경 sigma는 full/proxy 비율로 스케일)
  **파라미터를 새로 만들면 바로 아래 '렌더 경로' 체크리스트를 반드시 훑을 것.**

### ★ 렌더 경로 — 새 파라미터는 전부 통과시켜야 한다 (CPU=GPU=프리뷰)

같은 사진을 그리는 경로가 넷이고 **서로 다른 코드**다. 하나라도 빠뜨리면 "화면은 맞는데 내보낸
파일만 다르다" 또는 "CPU 로 내보내면 맞는데 GPU 로 내보내면 다르다"가 된다. 이 실수가 **실제로
반복해서 났다**(맨 아래 사례).

| 경로 | 코드 | 무엇을 읽나 |
|------|------|------------|
| 프리뷰 | `ui/Main.qml` `pipe` → `shaders/adjust.frag` | QML 프로퍼티(=셰이더 uniform) |
| GPU export | `ui/Main.qml` `pipeFull` → 같은 셰이더 | **`pipe` 와 같은 바인딩을 따로 적어 둔 것** |
| CPU export | `pipeline.render_full` | `params` dict |
| 비교창(`\`) | `comparePipe` → `shaders/displaycm.frag` | 무편집 렌더용 일부 uniform |

**① 룩 파라미터(셰이더 uniform)** → `adjust.frag` + `pipeline.render_full` + QML `pipe`/`pipeFull`
**둘 다**. 무편집 렌더에도 걸리는 것(예: `hlDesat`)이면 `displaycm.frag`/`comparePipe` 까지 넷.

**② 디코드에 영향을 주는 파라미터**(재디코드를 유발하는 것 — `lensCorrection` 류)
→ 셰이더가 아니라 **디코드 3곳**이다:

- 프리뷰: `main._render_worker` → `raw_loader.load_proxy` / `image_loader.load_proxy`
- CPU export: `pipeline.render_full` 이 **자체 디코드**(`params` 에서 읽는다)
- GPU export: `main._do_full_decode` → `load_full` (**`self._gpu_params` 에서 읽는다**)

⚠️GPU export 는 `params` dict 를 안 거치고 `_gpu_params` 를 따로 읽으므로 **가장 잘 빠진다.**

**③ 값의 저장·복원 배선**(①②와 별개로 항상): QML `editParams()`(사이드카) · `applyEdits()`
(체크박스는 **명시 대입 + 슬롯 호출** — 대입만으론 슬롯이 안 불린다) · `resetAllEdits()` ·
**export 파라미터 dict**(사이드카와 별개다) · `_PRESET_KEYS` 에 넣을지 판단.

**검증은 세 경로를 실제로 태워서** 한다 — 함수 직접 호출은 배선 누락을 못 잡는다(위 `## 검증 방법`).
예: 같은 사진에서 `load_proxy` 반환값 / `render_full` 결과 통계 / `load_full` 반환값을 나란히
찍어 비교. `autoExposure` 는 그렇게 세 줄을 찍어 GPU 경로 누락을 잡았다.

**실제로 났던 누락**: 스탬프 색·글로우를 추가할 때 **CPU export 만** 빠져 프리뷰·GPU 와 다른 룩으로
찍혔다 · `hlDesat` 게이트에서 **`displaycm.frag` 만** 빠져 비교창만 파랑을 날렸다 · 스탬프
파라미터가 `editParams()` 에는 있는데 **export dict 에 없어** 내보낸 파일에만 새 룩이 빠졌다 ·
`autoExposure` 를 ②(재디코드)로 만들었을 때 **GPU export 만** 빠질 뻔했다(구현 중 발견 — 지금은
①로 바뀌어 그 경로를 안 탄다).

⚠️**②로 만들 수 있다고 ②여야 하는 것은 아니다.** `autoExposure` 는 디코드 게인이라 ②가 자연스러워
보였지만, 곱셈이라 **노출 지수로 옮길 수 있었고** 그러자 토글이 2~4초에서 24ms 가 됐다. 재디코드를
유발하는 설계를 잡기 전에 "셰이더에서 상쇄 가능한 선형 연산인가"를 먼저 볼 것.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `main.py` | 앱 진입점, 이미지 프로바이더(Raw/Lut/Curve), Controller(로드·WB·export) |
| `raw_loader.py` | RAF → 프록시 QImage (절대 Kelvin WB, half_size, max_edge=2560) |
| `image_loader.py` | **일반 이미지(JPG/PNG/TIFF) → 같은 프록시 계약** (display-referred 어댑터). 프론트엔드가 `filmic()` 을 거는 것을 로드 시 `filmic⁻¹` 로 상쇄 → **중립 설정 export 가 원본과 비트 동일**(실측). 카메라공간=선형 sRGB(cam2srgb 항등, Temp/Tint 는 유효), 자동노출/렌즈보정 없음. 순백은 역함수가 발산해 최상단 코드의 1/4 빈 아래에서 클램프(8bit 2.171 / 16bit 3.834 < PROXY_HEADROOM). ⚠️`hlDesat=0` 필수 — 하이라이트 디새추는 센서 클립 보정이라 display-referred 소스에선 하늘·네온을 흰색으로 날린다. **같은 수식이 `adjust.frag`·`displaycm.frag`(Compare original 패스)·`pipeline.render_full` 세 곳에 있고 QML 은 `pipe`/`pipeFull`/`comparePipe` 세 ShaderEffect 에 게이트를 물려야 한다** — `displaycm.frag` 를 빠뜨려 `\` 비교창만 파랑을 날리는 버그가 있었다. Temp 는 일반 이미지에서 하한 2500K(선형 sRGB 원색의 저색온도 폭발 회피 — Bradford 교체는 프록시 정밀도 4배 악화로 기각) |
| `wb.py` | Kelvin(+tint) → rawpy user_wb 배수, as-shot 색온도 추정 |
| `lut.py` | `.cube` 3D LUT 파서 → 2D 아틀라스(셰이더용) + **사용자 LUT 저장소**(`user:<파일명>`, app_dirs `luts/`). ★사용자 LUT 은 **`simExpEV` 보정을 받지 않는다**(그 보정은 번들 후지 LUT 의 톤커브 이중적용을 상쇄하는 것 — 남의 큐브엔 상쇄할 게 없고 밝기가 룩이다). 게이트가 `main._update_sim_ev` 와 `pipeline.render_full` **두 곳**에 있다. ⚠️**LUT 마다 N 이 다를 수 있다** — 셰이더 `lutSize` 는 `controller.lutSizeFor(key)` 로 키별 N 을 받고, QML 은 텍스처 소스와 **같은 식**(`win.curSimKey`)에서 파생시켜야 한다 |
| `exif_info.py` | RAF 임베드 JPEG에서 EXIF 촬영정보 추출(exifread) → 패널/오버레이 |
| `haze.py` | 디헤이즈 물리(DCP): 이미지당 투과율 t-맵/대기광 A/신뢰도 conf 추정(numpy 독립) |
| `mist.py` | 미스트(디퓨전) 산란 모델 `out=(1−k)L+k(P⊗(L·E))` — **프론트엔드 맨 앞**(카메라네이티브 scene-linear = 유저 WB·매트릭스·노출보다 앞이라 산란 필드가 슬라이더와 무관해진다). 프리뷰는 **3단**: CPU 필드 3장 → `mistfield.frag` 합성 → `adjust.frag` 가 그 한 장만 섞는다. ⚠️**미측정 모델**(글레어 문헌의 1/θ² prior — 그레인·디헤이즈와 지위가 다르다). ★⚠️`adjust.frag` 에 **샘플러를 늘리지 말 것** — D3D11 은 스테이지당 16개뿐인데 이미 다 쓴다(늘렸다가 파이프라인 생성 실패로 죽었고 **qsb 컴파일은 통과한다**). 계수·실측·기각 기록은 `docs/mist_filter.md` |
| `depth.py` | 거리 범위 마스킹(Depth Anything V3 Small ONNX, log-depth 정규화, DirectML 우선) — 상대 거리 맵 → near/far 밴드 마스크. 셰이더/pipeline 무변경(기존 마스크 경로 재사용). `docs/depth_masking.md` |
| `ai_denoise.py` | AI 디노이즈(NAFNet ONNX, 고정 512 타일, DirectML 우선) — nrBase 대체용 luma(numpy 독립) |
| `lens.py` | RAF 내장 샷별 렌즈 보정(FujiIFD 0xf00b/0f/10 파싱 — 후지 전 기종, 기종 등록 불필요) |
| `date_stamp.py` | 필름 데이트백: DSEG7 날짜+글로우 렌더, 프리뷰/export 합성. 색·글로우·영역은 **사진별 슬라이더**이고 색은 한 색에서 3색 램프를 파생한다. ⚠️**프리뷰와 export 의 합성식이 다르다**(export 는 screen 70%+source-over 30%) — 산출물이 정확한 쪽. ⚠️파라미터를 늘리면 **호출부 3곳**(CPU export·GPU export·프리뷰)과 **export dict** 를 함께 볼 것. 상세는 `docs/date_stamp.md` |
| `make_luts.py` | 근사 필름룩을 .cube 로 베이크(폴백용) |
| `shortcuts.py` | **단축키·마우스 조작 목록의 단일 진실원**. 앱 안 `?`/`F1` 오버레이(`ui/ShortcutHelp.qml`)가 `controller.shortcutHelp`/`mouseHelp` 로 받아 그린다 — 목록을 QML/문서에 옮겨 적지 말 것. ★**단축키를 추가/변경하면 `python shortcuts.py`** — 표와 `ui/Main.qml` 의 실제 `Shortcut{}` 선언(+ `PreviewWindow.qml` 의 `Keys.on*Pressed`)을 토큰 단위로 대조한다. ⚠️`MOUSE` 는 파싱 대상이 아니라 수동 목록이라 검사기가 못 잡는다 |
| `presets.py` | 레시피 프리셋(`.frpreset`) — 룩만 담는 JSON + 출처 기록, 파일명 새니타이저, 검증기. ★**`LOOK_DEFAULTS` = 룩 키 44개의 공장 기본값 단일 진실원**(QML `applyEdits` 폴백 == `controller.lookDefaults` == 룩 지문 보정). ⚠️**한 키에 기본값은 하나**여야 이 구조가 성립한다. ★**룩 키를 추가하면 `python presets.py`** — 키 집합·QML 기본값·`applyEdits` 폴백·`resetAllEdits` 네 면을 대조해 드리프트를 잡는다. 설계 경위는 `docs/recipe_presets.md` |
| `pipeline.py` | **풀해상도 export** (numpy, 셰이더와 동일 파이프라인 재현) |
| `ui/Main.qml` | 전체 UI (좌: 이미지 / 우: 스크롤 패널) |
| `ui/CurveEditor.qml` | 톤 커브 위젯(드래그/추가/삭제, Catmull-Rom) |
| `shaders/adjust.frag` | 메인 파이프라인 프래그먼트 셰이더 |
| `shaders/blur.frag` | 분리형 가우시안 블러(로컬대비용) |
| `luts/*.cube` | 필름 시뮬레이션 LUT (abpy/FujifilmCameraProfiles sRGB, N=32) |

## 톤 파이프라인 — 베이스가 어디에 앉는가

세 가지가 한 덩어리로 얽혀 있다. **경위·실측·기각 기록 전체는 `docs/tone_pipeline.md`.**

- **자동노출**: 디코드 때 평평한 베이스의 **중앙값이 임베드 JPEG 과 같아지도록** scene-linear
  게인을 곱한다(`wb.auto_exposure_gain`, 실측 **+0.9~2.2EV**). 적용값은 Exposure 줄에 표시하고
  (`autoExposureEV`), 끌 수 있다(`autoExposure`). ⚠️**끄기는 재디코드가 아니라 노출 오프셋**
  (uniform `autoExpEV` = −log2(게인)) — 재디코드로 만들었다가 2~4초가 걸려 바꿨다(24ms).
- **필름시뮬 LUT**: 번들 `.cube` 는 룩이 아니라 **후지 톤커브 전체**를 담고 있어 `filmic()` 위에
  그냥 얹으면 톤커브가 두 번 걸린다(중앙값 **+0.8~1.4EV**). `pipeline.film_sim_ev` 가 LUT 통과 후
  중앙값을 베이스로 되돌리는 노출을 풀어 uniform `simExpEV` 로 넣는다. ⚠️**자동노출 솔버는
  무죄다**(임베드 JPEG 중앙값 편차 0.000~0.001) — 밝기 문제를 그쪽에서 다시 찾지 말 것.
  ★⚠️**보정은 상한 0 이다**(들어올린 것만 되돌린다). 베이스 중앙값이 LUT 그레이 전달의 교차점
  (≈0.23) 아래면 중앙값을 지키려다 입력을 **거꾸로 밀어 올린다** — 실측 0.012 에서 +2.00EV,
  하이라이트 p95 가 0.011→0.473 으로 터진다. 코퍼스의 6.9% 가 그 구간이다.
  ★⚠️**양쪽 경계는 '보정 없음'으로 떨어뜨린다** — 탐색 경계값을 그대로 돌려주면 안 된다.
  하한 −4EV 를 돌려주던 시절 블랙이 들린 LUT(bleach_bypass·nostalgic_neg·classic_neg)은
  자기 블랙포인트보다 어두운 중앙값에 **도달 자체가 불가능**해 그 사진이 통째로 검정이 됐다.
  ★⚠️바닥값 `FILM_SIM_EV_FLOOR`(0.05) 아래는 **아예 풀지 않는다** — 중앙값 보존 앵커는
  중간톤에서만 성립한다(0.026 에서 −0.12~−0.18 인데 0.019 에서 −1.21~−1.59). 코퍼스 853장의
  p1 이 0.046 이라 ~1% 가 여기 걸리고, 0.05~0.12 구간 보정은 어차피 0 이라 잃는 것이 없다.
- **하이라이트 디새추(`hlDesat`)**: 센서 클립의 색끼를 중성화하는 단계. ⚠️밝기 게이트는
  **display 값이 아니라 센서 클립 근접도**다(`clipLevel` = `raw_loader.clip_level(자동게인)`).
  display 로 재면 센서 포화의 20~51% 뿐인 파란 하늘을 흰색으로 날린다. ⚠️게인이
  PROXY_HEADROOM 을 넘는 사진은 프록시가 이미 뭉개 클립을 구분할 수 없어 **게이트를 끈다**
  (예전 min(g,H) 상한은 g=5.4 에서 센서 0.67 부터 열려 같은 오탐을 냈다).
- ★**게인과 거기서 파생되는 수치는 반드시 `load_proxy` 경로(=렌즈 보정 포함)에서 잴 것.**
  `_decode_native` 로 직접 재면 비네팅 보정이 빠져 0.2~0.4EV 과대평가된다(한 번 그렇게 적혀
  나중에 그대로 인용됐다).
- ⚠️`simExpEV`·`autoExpEV` 는 QML `pipe`/`pipeFull` **둘 다**, `hlDesat` 게이트는 `adjust.frag`·
  `displaycm.frag`·`pipeline` **세 곳**이 같아야 한다(위 '렌더 경로' 체크리스트).

## 조정 계수 (라이트룸 맞춤 튜닝 대상)

셰이더(`adjust.frag`)와 `pipeline.py` 양쪽에 동일하게 존재. 사용자 피드백으로 계속 조정한다.

| 도구 | 계수(현재) | 위치/비고 |
|------|-----------|-----------|
| Highlights/Shadows | **1.0 (stop)** | tone_zones, 국소 노출(곱셈 게인 c*2^g) — 색비·대비 보존, 회색화 방지 |
| Whites/Blacks | **0.3** | tone_zones, 끝단 좁은 마스크 (가산=화이트/블랙 포인트 이동) |
| Texture | **1.6** | 작은반경 블러 하이패스 |
| Clarity | **0.8** | 큰반경 블러, 중간톤 가중 |
| Dehaze | 톤: 로컬대비 **0.4** / 대비 **0.25** / 흰베일 **0.22** / 채도 **0.3** · 물리: TMIN **0.15** / RESID **0.35** | +=DCP 물리(t-맵·대기광, conf 게이팅) / −=흰 베일 톤모델. haze.py + 셰이더 6단계 == pipeline._dehaze |
| Vignette | **0.8** | 방사형, − 가장자리 어둡게 |
| 미스트 | k **0.42**(Amount=1) / σ **(0.25%, 1%, 4%)** 긴변비 / 무게 블랙 (0.68,0.27,0.05,0)·화이트 (0.30,0.22,0.30,0.18) / 보상 무릎 **0.90~2.20** | `MIST_*` == `mist.py`. 실측(outEdge 1600): 블랙 0.7 에서 섀도 +2.6코드 vs 화이트 +20.5 — Character 가 '블랙 유지 vs 안개'를 8배로 가른다. 점광원 꼬리 화이트 r^-2.1(=목표 1/θ²) / 블랙 r^-3.3. 고주파 유지율 ≈ (1−k) 는 정상(산란된 비율만큼 전 주파수 대비가 준다) |
| 휘도 NR | 가이디드 필터 반경 **4**(프록시px) / eps **0.0015** | 노이즈=중성 luma−디노이즈드. 프리뷰=nrBase 텍스처(main.py NR 워커, binding 12) / export=pipeline 이 반경 스케일해 동일 필터. 셰이더 uniform 아님(텍스처 베이크) |
| AI 디노이즈 | NAFNet-SIDD w32, 512 타일 / OVERLAP **64** / DRIFT_SIGMA **16**(프록시px, export ×scale — 모델이 바꾼 저주파 색/밝기 복원, 없으면 colorNR 이 색감을 옮김) | aiNr 체크 시 nrBase(RGBA64) 를 NAFNet RGB 결과로 교체(온디맨드, 완료까지 가이디드 폴백) — **luma=Luminance 슬라이더, chroma=Color 슬라이더**(nrChroma 게이트; 색얼룩 제거가 체감 핵심). GPU EP(DirectML 최속 디바이스 프로빙/CoreML) 우선 — CPU 폴백이면 QML 이 진행 여부를 물음(aiCpuDialog, 세션 기억). export 는 풀해상도 타일 추론. 모델은 런타임 다운로드(models/, 번들 금지). ⚠️SCUNet 은 DML 가속 불능으로 기각(models/README.md 참조 — 재조사 금지) |
| Grain ⚠️**현상론적 모델**(물리 시뮬 아님) | 강도 **0.24** / 셀수 `gridN=mix(4500,1300,size)` / 노출의존 `GRAIN_TONE=1.29`·γ **0.86**·바닥값 **0.20** / 왜도 `GRAIN_SKEW=0.92` / 원판 `GRAIN_DISK_R=0.55` · 사진별 슬라이더 **Roughness**(.1)·**Color**(.3)·`Round grains`(기본 꺼짐) | 흑백 휘도 **셀 노이즈(보간 없음)**, 톤커브 뒤·비네팅 앞. 셀 크기는 **긴 변 기준**. 실측 피팅(Noritsu 4롤·151프레임)·기각 기록·**재시도 금지** 항목 전체는 **`docs/film_grain.md`**. ⚠️`GRAIN` 을 바꾸면 `date_stamp.STAMP_GRAIN_K` 도 같은 비율로 재환산할 것. ⚠️그레인 통계(σ·첨도)를 **JPEG 저장본에서 재지 말 것**(블로킹이 첨도를 부풀려 판정을 뒤집는다). ⚠️보간(smoothstep/선형) 재도입 금지 |
| Temp/Tint | Planckian, TREF=5500 | 절대 Kelvin, 디코딩 단계 |

값을 바꿀 때는 **셰이더 + pipeline.py 동시 수정 + 셰이더 재컴파일** 후, `render_full` 로
응답 곡선(예: 평균밝기 vs 슬라이더)을 측정해 라이트룸과 비교한다.

## UI 규칙 / 주의사항

- 모든 슬라이더 **더블클릭 → 기본값 리셋**. Slider의 native `pressed` 신호로 더블프레스를 감지하고
  **release 시점에 리셋**(press 중에는 Slider가 value를 커서위치로 덮어쓰므로). `win.isDblPress()`.
  TapHandler 방식은 Slider grab과 충돌해 안 됨(과거 확인).
- 우측 패널은 **ScrollView**. **커브 에디터 높이는 반드시 고정값**(`Layout.preferredHeight: 240`).
  너비기반(정사각형)으로 두면 스크롤바↔availableWidth 레이아웃 루프로 창 전체가 느려짐(과거 버그).
- 커브 에디터 MouseArea는 `preventStealing: true`(ScrollView가 드래그 가로채는 것 방지).
- **디바운스는 "프레임 예산 초과"가 아니라 "체감"으로 판단할 것.** `date_stamp.sprite_layer`
  실측 **2.5 / 20.2 / 56.5ms**(size_frac 0.012 / 0.032 / 0.050) — 크기 **제곱**에 비례하고
  최대에서 예산의 3.4배(≈18fps)다. ⚠️**Stamp size 슬라이더에 150ms 디바운스를 넣었다가 철회했다
  — 재시도 금지**: 초당 6~7회로 떨어져 오히려 뚝뚝 끊긴다(사용자 확인). 예산 초과 수치만 보고
  디바운스를 넣지 말 것.
  ★해법은 디바운스가 아니라 **워커 스레드 + 코얼레싱**이었다(`_stamp_worker`/`_on_stamp_sprite`).
  픽셀은 비트 동일하고(동시 3워커 검증, 최대차 0코드) GUI 점유가 **평균 0.11ms·최악 3.52ms** 로
  떨어진다. ⚠️드래그 중 축소 렌더(드래프트)는 **기각** — 폰트가 정수 픽셀로 래스터돼 놓는 순간
  4~11px 튄다. ⚠️`_wide_blur` 근사를 기본 spread 로 넓히는 것도 **기각** — 예전에 저장한 스탬프의
  모습이 바뀐다(그 함수 주석). Grain 슬라이더가 디바운스인 것은 장면 그레인(GPU 라이브)과 스탬프
  스프라이트(CPU)를 동시에 물고 있어서고, 텍스트 입력(200ms)은 타건마다 전체 재렌더라서다.
- ★⚠️**신호 핸들러 안에서 파생 프로퍼티를 읽으면 갱신 전 값이 나온다.** `simCombo`
  `onCurrentIndexChanged` 에서 `win.curSimKey`(= `simKeys[currentIndex]` 바인딩)를 읽으면
  **직전 키**가 돌아온다(실측: velvia 로 바꿨는데 이전 키). 바인딩 재평가보다 핸들러가 먼저
  돈다 — 핸들러에서는 **원천 값으로 직접 계산**할 것(바인딩으로 쓰는 것 자체는 정상).
  이걸로 '떠나는 키'를 검사해 사용자의 새 선택이 목록 갱신에 휩쓸리는 버그가 한 번 났다.
- 셰이더 텍스처는 image provider 경로(Image→sampler)가 검증됨. Canvas→ShaderEffectSource 직접
  바인딩은 과거 검정화면 유발(커브 LUT를 provider 방식으로 전환해 해결).
- **날짜 스탬프**: 좌측 셀렉터의 **독립 탭**(`Ctrl+4`). '내 기본값'(`stamp.json`)을 기억하고
  사용자 폰트를 추가할 수 있다. ⚠️**프리뷰와 export 의 합성식이 다르다**(export 가 정확한 쪽).
  ⚠️파라미터를 늘리면 **호출부 3곳 + export dict** 를 함께 볼 것. 규칙·함정 전체는
  `docs/date_stamp.md`.
- **컨택트 시트**(빈 캔버스의 폴더 격자): **클릭=선택 / 더블클릭=열기**(탐색기와 같은 규칙),
  선택 상태는 탐색기 `currentIndex` 에서 **파생**한다(진실원 하나). 뜨는 규칙은 **두 줄뿐** —
  `G`(또는 경로 표시줄 ▦)로 켜고 끄며, 아직 사진을 안 열었으면 켜져 있다. ⚠️**조건을 늘리지
  말 것**(늘렸다가 "경우마다 달라 혼란스럽다"는 보고를 받고 걷어냈다).
  ★**썸네일 요청 크기 160 은 임의 값이 아니다** — 그 이하는 EXIF 썸네일(1.4ms/장), 넘으면
  임베드 프리뷰 축소 디코딩(**73.9ms/장, 50배**). 셀을 키우려면 이 비용을 먼저 정할 것.
  상세는 `docs/ui_notes.md`.
- **'편집됨' 표시는 파일명 색 + 썸네일 배지 둘 다**(`ui/EditedBadge.qml` = 앱 아이콘,
  `packaging/make_icon.py --badge` 가 굽는다). ⚠️배지는 칸이 아니라 **사진이 그려진 사각형**
  모서리에 붙이고(♥와 같은 14px), 썸네일 로드 전에는 감춘다(`paintedWidth` 가 0 이라 한가운데로
  간다). 도안을 두 번 갈아엎은 경위는 `docs/ui_notes.md`.
- **얼굴 부위 전체 선택/해제**(`win.setAllFaceParts`): ⚠️`toggleMaskKey` 를 11번 부르지 않고
  키 목록을 한 번에 만들어 **한 번만** 커밋한다. 그래서 '첫 부위의 기본 대상 / 마지막 부위의
  memo 백업' 규칙이 **두 곳에 있다** — `toggleMaskKey` 와 같아야 한다. 상세는 `docs/ui_notes.md`.

## 레시피 프리셋 (`.frpreset`)

편집 '룩'만 저장/공유하고 **출처(카메라·렌즈·촬영일·appVersion)를 함께 기록**한다.
설계 경위·함정 전체는 **`docs/recipe_presets.md`**.

- **담는 키의 단일 진실원 = `main.Controller._PRESET_KEYS`**(QML `presetKeys`). 저장/로드 필터와
  '기본값으로 되돌릴 키' 목록이 반드시 이것 하나를 봐야 한다. 새 슬라이더를 추가하면 넣을지
  판단할 것 — `python presets.py` 가 `_PRESET_KEYS + 제외 == editParams()` 를 대조한다.
- **제외**: `exposure`·WB·크롭/기하·스탬프 텍스트·`maskLayers`·`aiNr`·`lensCorrection`·NR
  (촬영 조건이거나 그 사진 구도/장비에 묶이거나 부작용을 유발한다 — 이유는 문서).
- ⚠️적용은 **`applyPresetEdits` 의 3단 병합**이다. 프리셋 dict 를 `applyEdits` 에 그대로 넘기면
  대상 사진의 마스크가 삭제되고 WB·크롭도 초기화된다.
- **배지 = 지금 룩이 그 레시피와 같은가, 그것뿐**(`look_hash` 비교). ⚠️'기반했으나 수정됨'
  2번째 상태를 사이드카 계보로 만들었다가 **전부 제거했다 — 되살리지 말 것**(룩이 같은 두
  사진이 다른 배지를 보였다). 지문 갱신은 `histPush`/`histReset` 두 곳에서만.
- 저장 위치: `%LOCALAPPDATA%/FilmRawstery/presets/`.

## 설정 저장 위치 (레지스트리 금지)

⚠️**앱 설정은 레지스트리에 쓰지 않는다.** Windows 전용이라 크로스 플랫폼에서 못 쓰고
백업·이전·삭제가 어렵다. 모든 설정은 **OS 공통 사용자 데이터 폴더**(`app_dirs`)의 JSON 이다.

| 파일 | 내용 |
|------|------|
| `prefs.json` | 앱 전역 설정 — `export`(lastExt·lastEdge·lastRender·last16Bit·lastFolder) · `explorer`(lastFolder). 모듈 레벨 `pref_get`/`pref_set`(원자적 쓰기, 같은 값이면 디스크 미접촉) |
| `stamp.json` | 날짜 스탬프 '내 기본값' |
| `wallpaper.json` | 배경화면 패널 설정 |
| `presets/*.frpreset` | 레시피 프리셋 |
| `fonts/*.ttf` | 사용자가 추가한 스탬프 폰트 |
| `luts/*.cube` | 사용자가 추가한 LUT(Film Simulation → Add LUT…) |
| `models/` | AI 모델(런타임 다운로드) |

사진별 편집은 설정이 아니라 **사이드카**다: `<사진 폴더>/.filmrawsteryedits/<파일명>.json`
(사진과 함께 이동·백업돼야 하므로 앱 설정과 섞지 않는다).

⚠️`QSettings` 는 **구버전 값 1회 이관 전용**으로만 남아 있다 — `_migrate_registry_prefs`
(export·explorer 그룹)와 `_migrate_wall_prefs_from_registry`(wallpaper 그룹). 둘 다 이관 후
레지스트리 그룹을 **제거**한다. 새 설정을 QSettings 에 추가하지 말 것.

## Export

- `pipeline.py` 가 풀해상도(6246×4170)를 동일 파이프라인으로 현상 → jpg/png/tif(8bit) 저장.
- **저장 = 메모리 인코딩 → 임시 파일 → `os.replace`(실패 시 제자리 폴백)**. 셋 다 이유가 있다:
  ①**메모리 인코딩**(`QBuffer`) — 대상 파일에 바로 인코딩하면 26MP 에서 파일이 열린 채
  1.4~7.4s(jpg q95 1.36 / png8 3.69 / png16 7.41, 최악=전화면 그레인)가 흘러, 그 사이 앱을
  닫으면 **같은 이름의 기존 파일이 잘린 채** 남는다. 쓰기 구간은 0.08~0.19s 로 19~40배 짧다
  (무압축 tif 만 인코딩 0.19s 라 2배).
  ②**임시 파일 + `os.replace`** — 같은 디렉터리라 항상 동일 볼륨이고 교체가 원자적.
  ③⚠️**replace 실패 시 제자리 쓰기 폴백 필수** — Windows 에서 대상 파일을 **다른 프로그램이
  열고 있으면** `os.replace` 가 `PermissionError [WinError 5]` 로 막힌다(실측). 제자리
  `img.save` 는 같은 상황에서 성공하므로, 폴백이 없으면 *결과를 뷰어로 열어보고 재export* 하는
  흔한 흐름에서 40~60초 렌더가 통째로 버려진다.
  ⚠️포맷은 `img.save(dev, EXT, q)` 로 **명시**할 것 — 임시 이름이 `<path>.part` 라 Qt 가
  확장자로 추론하면 모르는 형식이라 무조건 실패한다(확장자가 *없는* 게 아니라 Qt 가 `.part` 를
  모르는 것). 확장자가 아예 없으면 fmt=None 으로 넘겨 **예전처럼 실패**시킨다 — 임의 형식으로
  저장하면 '저장됨' 이라 알리고도 열리지 않는 파일이 남는다.
  ⚠️인코딩 중 강제 종료 시 `.part` 가 남을 수 있다(daemon 워커라 정리 코드가 안 돈다). 창은
  0.08~0.19s 뿐이고 앱 탐색기는 `RAW_EXTS` 만 나열해 UI 에는 안 보인다.
- **jpg 품질 = `pipeline.JPEG_QUALITY` (95)**, 게이팅은 `pipeline.JPEG_EXTS`
  (**jpg/jpeg/jfif** — Qt 가 JPEG 핸들러로 매핑하는 확장자 전부. jfif 를 빼면 그 경로만
  기본 품질 75 로 저장된다). ⚠️Qt 기본값 75 를 쓰면 그레인처럼 화면 전체가
  고주파인 이미지에서 **8×8 DCT 블록이 격자로 보인다** — 측정(열 경계별 |ΔI| 비, 1.00=격자 없음):
  무손실 1.00 / q75 1.34 / q95 1.02. 최악(그레인 Size 최대) 파일 크기는 0.7→2.9MB.
  ⚠️PNG 에는 quality 를 주면 안 된다(Qt 에서 PNG 의 quality 는 '압축 레벨'이라 의미가 반대 —
  95 면 거의 무압축) → `save_image` 가 **확장자로 게이팅**해 jpg 에만 적용, 나머지는 −1.
  ⚠️**그레인 통계(σ·첨도)를 JPEG 저장본에서 재지 말 것** — 블로킹이 첨도를 부풀려 판정을
  뒤집는다(실측: 원판 비교에서 3.38 vs 3.32 '차이 없음' → 무손실 재측정 3.03 vs 3.28 '차이 있음').
- **백그라운드 threading.Thread**(데몬)로 실행 → UI 안 멈춤. 26MP 전효과 ~40–50초(순수 CPU numpy,
  가우시안/LUT가 무거움). 메모리 위해 LUT 단계는 가로 스트립 처리, 공간단계는 전체 배열.
- **그레인 스트립 병렬**(**daemon 스레드** `min(6, 코어-2)` 개, 스트립 라운드로빈 분배): 필드가 좌표 결정론이라
  스트립 독립·쓰기 서로소 → **직렬과 비트 동일**(검증). numpy 대형 배열 연산이 GIL 을 풀어
  실효 병렬 — 실측 26MP 그레인 사각 셀 20.6→5.8s, 원판 258→64s(4.0배), 2560 원판 전체 렌더
  50.6→21.3s. 6워커 초과는 수확 체감 + 스트립당 ~100MB 작업 메모리라 상한. 배경: CPU export
  중 그레인 구간만 점유율이 낮다는 사용자 보고(단일 코어였음).
  ⚠️**ThreadPoolExecutor 금지** — 워커가 non-daemon 이고 `_register_atexit` 훅이 큐를 비운 뒤
  join 해서, 그레인 도중 앱을 닫으면 남은 스트립이 끝날 때까지(26MP 원판 ~64s) 창 없는
  프로세스가 살아 있는다. daemon 스레드 직접 생성으로 교체(실측 종료 지연 ~0.3s, 결과는
  여전히 직렬과 비트 동일 — cell/disk 양쪽 검증).
- **GPU export(pipeFull grab)**: ①`saveGrab` 은 QImage→numpy 복사와 프로바이더 해제만 메인
  스레드에서 하고, 지오메트리/스탬프/인코딩은 `_finish_gpu_export` 워커로 — 전에는 전부 GUI
  스레드라 grab 후 저장까지 앱이 멈췄다(v1.8.0 사용자 보고). ②해상도 프리셋은 **pipeFull 이
  처음부터 그 크기로 렌더**(`fullScale`, `win.gpuExportEdge` 요청 시점 스냅샷 + `srcFull`
  mipmap) — 예전 '풀해상도 렌더 후 CPU 축소'는 그레인이 평균돼 CPU 경로보다 약했고
  (σ 12.7→10.5, JPG −10%) 26MP 축소가 멈춤을 키웠다. 그레인이 출력 해상도에서 계산되므로
  긴 변 보정에 의해 CPU 프리셋과 셀 크기·σ 정합. ③⚠️**HiDPI**: Windows 배율 >100% 면
  `grabToImage` 가 요청 크기 ×DPR 이미지를 돌려준다(실측 DSCF8482: 4080×6111 요청 →
  5100×7639 = ×1.25 — 'GPU export 가 CPU 보다 크다' 보고의 원인, Original 포함 전 해상도).
  → `saveGrab` 이 소스 원본 크기에서 기대 치수를 계산해 워커에서 **지오메트리 전에**
  정규화(배율 100% 면 no-op, 동일 객체). ⚠️CPU 점유율 질문(커뮤니티): CPU export 는
  numpy 단일 코어 위주라 12스레드 CPU 에서 8~10% 로 보이는 게 정상 — iGPU 유무와 무관.
- **현상 크레딧**: export 파일에 `Software = Film Rawstery v<ver>` 를 남긴다
  (`main.EXPORT_SOFTWARE` → `pipeline.save_image(..., software=)`). JPEG 은 **직접 만든 최소
  EXIF APP1**(`_exif_app1`/`_insert_app1`), PNG 은 tEXt. ⚠️**TIFF 는 남지 않는다** — Qt 의
  TIFF 핸들러가 `setText` 를 조용히 버린다(실측). ⚠️Qt 의 `setText` 는 JPEG 에서 **COM(주석)**
  으로 나가 `exifread` 태그가 0개다 — 탐색기·라이트룸의 Software 칸에 뜨게 하려면 EXIF 세그먼트를
  직접 넣어야 한다(그래서 그렇게 했다). 실측: 픽셀 비트동일, JPEG +58B, 16bit PNG depth 유지.
  ⚠️export 경로를 새로 만들면 `EXPORT_SOFTWARE` 를 함께 넘길 것(호출부 3곳 — 순환 임포트 때문에
  pipeline 이 APP_VERSION 을 직접 못 읽는다).
- 16bit TIFF 미지원(QImage 8bit). 필요 시 tifffile/imageio 추가.

## macOS 패키징 (.app + DMG)

`packaging/build_mac.sh` (앱 종료 → 클린 → PyInstaller → 재서명 → 스모크 → 선택적 공증 → DMG).
산출물 `dist/FilmRawstery.app` / `dist/FilmRawstery-v<ver>-macos-arm64.dmg`.
실측 .app 457MB / DMG 169MB. 빌드 도구는 venv 에 따로: `pip install pyinstaller pillow`.

**함정·경위·검증 결과 전체는 `docs/packaging_macos.md`.** 여기서는 세 가지만:

- **arm64 전용 · 최소 macOS 15**(휠 태그가 아니라 번들 Mach-O 의 `minos` 실측으로 정한 값).
- **배포는 ad-hoc 서명 + 공증 없음**(2026-08 결정). 릴리스 본문에 차단 해제 절차 + SHA256 필수.
- ⚠️**mac DMG 를 올릴 때 GitHub 릴리스를 pre-release 로 표시하지 말 것** — 인앱 업데이터가
  `prerelease`/`draft` 를 건너뛰어 **Windows 사용자 전체의 업데이트 알림이 조용히 멈춘다.**

## 향후 후보

디헤이즈 물리모델 계수 튜닝(라이트룸 나란히 비교 — DEHAZE_TMIN/RESID),
16bit GPU export, export 속도 최적화, 범위 마스크(휘도/색상), 그라디언트 필터.
