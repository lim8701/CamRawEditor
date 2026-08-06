# RAW Editor (Fujifilm RAF) — 프로젝트 가이드

PySide6 + QML + GPU 셰이더 기반 RAW(.RAF) 현상/보정 에디터. 후지 전 기종 RAF 지원
(색 매트릭스/WB/렌즈 보정 모두 파일 메타데이터 기반 — 기종 등록 불필요. 주 개발 기준: X100V).

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

## 아키텍처

```
RAF ─rawpy(절대 Kelvin WB, auto-bright OFF, half_size)─> 프록시 QImage(max_edge 2560)
   │  (wb.py: Planckian+daylight 앵커로 Kelvin→user_wb, as-shot 추정)
   ▼
QML ShaderEffect 파이프라인 (프록시 해상도 FBO에 렌더 → 화면크기로 스케일 표시)
   순서: 노출 → WB프리뷰게인 → 톤영역(hi/sh/wh/bl)
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

## 파일 구조

| 파일 | 역할 |
|------|------|
| `main.py` | 앱 진입점, 이미지 프로바이더(Raw/Lut/Curve), Controller(로드·WB·export) |
| `raw_loader.py` | RAF → 프록시 QImage (절대 Kelvin WB, half_size, max_edge=2560) |
| `wb.py` | Kelvin(+tint) → rawpy user_wb 배수, as-shot 색온도 추정 |
| `lut.py` | `.cube` 3D LUT 파서 → 2D 아틀라스(셰이더용) |
| `exif_info.py` | RAF 임베드 JPEG에서 EXIF 촬영정보 추출(exifread) → 패널/오버레이 |
| `haze.py` | 디헤이즈 물리(DCP): 이미지당 투과율 t-맵/대기광 A/신뢰도 conf 추정(numpy 독립) |
| `depth.py` | 거리 범위 마스킹(Depth Anything V3 Small ONNX, log-depth 정규화, DirectML 우선) — 상대 거리 맵 → near/far 밴드 마스크. 셰이더/pipeline 무변경(기존 마스크 경로 재사용). `docs/depth_masking.md` |
| `ai_denoise.py` | AI 디노이즈(NAFNet ONNX, 고정 512 타일, DirectML 우선) — nrBase 대체용 luma(numpy 독립) |
| `lens.py` | RAF 내장 샷별 렌즈 보정(FujiIFD 0xf00b/0f/10 파싱 — 후지 전 기종, 기종 등록 불필요) |
| `date_stamp.py` | 필름 데이트백: DSEG7 7-세그 날짜+글로우 렌더, 프리뷰/export 합성 |
| `make_luts.py` | 근사 필름룩을 .cube 로 베이크(폴백용) |
| `pipeline.py` | **풀해상도 export** (numpy, 셰이더와 동일 파이프라인 재현) |
| `ui/Main.qml` | 전체 UI (좌: 이미지 / 우: 스크롤 패널) |
| `ui/CurveEditor.qml` | 톤 커브 위젯(드래그/추가/삭제, Catmull-Rom) |
| `shaders/adjust.frag` | 메인 파이프라인 프래그먼트 셰이더 |
| `shaders/blur.frag` | 분리형 가우시안 블러(로컬대비용) |
| `luts/*.cube` | 필름 시뮬레이션 LUT (abpy/FujifilmCameraProfiles sRGB, N=32) |

## 필름 시뮬레이션 LUT

- `luts/` 의 `.cube`(sRGB, N=32)는 abpy/FujifilmCameraProfiles 출처. 12종(identity, provia,
  velvia, astia, classic_chrome, classic_neg, nostalgic_neg, pro_neg_hi, pro_neg_std, eterna,
  reala_ace, bleach_bypass). 콤보 인덱스↔키 순서는 `ui/Main.qml win.simKeys` 와 일치해야 함.
- 더 정확한 룩이 필요하면 **같은 키 이름으로 .cube 덮어쓰기**만 하면 됨(코드 변경 불필요, 크기
  자동 인식). 근사 베이크본 백업: `luts/_approx_backup/`.
- 주의: `make_luts.py` 실행 시 실제 LUT를 근사본으로 덮어쓴다.

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
| 휘도 NR | 가이디드 필터 반경 **4**(프록시px) / eps **0.0015** | 노이즈=중성 luma−디노이즈드. 프리뷰=nrBase 텍스처(main.py NR 워커, binding 12) / export=pipeline 이 반경 스케일해 동일 필터. 셰이더 uniform 아님(텍스처 베이크) |
| AI 디노이즈 | NAFNet-SIDD w32, 512 타일 / OVERLAP **64** / DRIFT_SIGMA **16**(프록시px, export ×scale — 모델이 바꾼 저주파 색/밝기 복원, 없으면 colorNR 이 색감을 옮김) | aiNr 체크 시 nrBase(RGBA64) 를 NAFNet RGB 결과로 교체(온디맨드, 완료까지 가이디드 폴백) — **luma=Luminance 슬라이더, chroma=Color 슬라이더**(nrChroma 게이트; 색얼룩 제거가 체감 핵심). GPU EP(DirectML 최속 디바이스 프로빙/CoreML) 우선 — CPU 폴백이면 QML 이 진행 여부를 물음(aiCpuDialog, 세션 기억). export 는 풀해상도 타일 추론. 모델은 런타임 다운로드(models/, 번들 금지). ⚠️SCUNet 은 DML 가속 불능으로 기각(models/README.md 참조 — 재조사 금지) |
| Grain ⚠️**현상론적 모델**(물리 시뮬 아님 — 아래 '남은 비물리' 참조) | 강도 **0.21 — grainSize=0 에서 σ 9.21 & acf 0.242 로 실측 필름(9.22 / 0.234) 양축 일치. 즉 슬라이더 최저크기=실측 35mm, 굵을수록 진해짐(기본 11.0, 최대 13.4)이라 최대가 필름을 넘어 선택 여지 확보** / 셀수 **gridN=mix(4500,1300,size)**(실측: 우리 그레인이 필름보다 굵었음 — 출력 3024px 에서 acf lag1 옛기본 0.79 vs 실측필름 0.23. 새 기본 2900→0.52, 최고고움 4500→0.34. 슬라이더 1.0 이 대략 옛 기본) / 노출의존 **GRAIN_TONE=1.28(실측)** · 사진별 슬라이더 **Roughness**(기본 **.1 실측피팅**)/**Color**(기본 .3 눈대중) | 흑백 휘도 **셀 노이즈(보간 없음)**, 톤커브 뒤·비네팅 앞. ⚠️보간(smoothstep/선형) 재도입 금지 — 결이 뭉개지고(acf lag1 보간없음 0.391 / smoothstep 0.504 / 선형 0.553, 실측필름 0.234) 서브픽셀 평균에서 σ 도 깎여 곱기·진하기를 맞바꾸게 된다. 없앤 뒤 26MP 22.7s→16.8s. **노출 의존 진폭**: `w=max(0, mix(1, sqrt(4·lg·(1−lg)), GRAIN_TONE))`, `lg=l^GRAIN_TONE_GAMMA` (l=display 휘도) — 보이는 톤 요동 = 밀도 요동 × 특성곡선 기울기(미드톤 최대, toe/shoulder 0). 미드톤 w=1 이라 **룩 불변**, 끝단만 감쇠. ★**이 모델에서 유일하게 실측 기반 계수**: Noritsu 스캔 4롤·151프레임·평탄패치 11,512개 피팅 → **K=1.29, γ=0.88**(롤별 K 1.27~1.36, 편차 3%). ⚠️γ 를 한 번 기각했다가 되살렸다 — 처음엔 필름 여백을 피하려 **휘도 18 미만 제외**하고 피팅했는데 γ 를 결정하는 게 바로 그 섀도 구간이라 롤마다 0.64~0.98 로 흔들려 보였다. 섀도(하한 5)를 넣어 다시 재니 K 가 오히려 안정되고(±0.07→±0.04) **네 롤 모두 γ<1**, 잔차 0.053→0.027. 대칭 벨은 섀도 10~15% 부족·상위미드톤 7~12% 과다라 체계적 불일치였다. '바닥값 wmin' 은 측정 구간에서 미발동이라 무효(형태 문제였음). 피크 display 128→116, 완전소멸 휘도 3→1. ⚠️K>1 이라 **max(0,·) 클램프 필수**(음수면 노이즈 반전, 휘도 1.2%↓·98.8%↑에서 발동) — 셰이더·pipeline 양쪽. **멀티 옥타브(fBm)**: 진폭 1,r,r² × 주파수 f,f/2,f/4(`r`=**Roughness 슬라이더** `grainRough`) 를 `1/√(1+r²+r⁴)` 로 정규화 → **r 을 바꿔도 세기(σ) 불변, 결만 변함**(측정: 방향 이방성 lag2 7.2→2.6). ⚠️**옥타브는 거친 쪽으로만** — f×2 는 프록시에서 셀<2px 라 Nyquist 미만, 프록시/풀해상도에서 다르게 접혀 프리뷰≠export. ⚠️옥타브 오프셋(`_GRAIN_OFFSETS` ↔ 셰이더 `GRAIN_OFF1/2`) 필수: 없으면 세 격자가 원점 셀을 공유해 hash23 이 같은 값 → 옥타브 비독립 → 정규화 전제 붕괴. **3층 에멀전**(**Color 슬라이더** `grainColor`=k): 컬러 필름엔 휘도 입자가 따로 없고 휘도 요동 = R/G/B 발색층의 합 → `hash23` 으로 층 3개를 만들고 `mono=(Σ층)/√3`, `n=mix(mono, 층, k)`. `(1−k)²+k²|LUMA|²+2(1−k)k/√3` 의 역제곱근으로 **휘도 σ 를 k 와 무관하게 고정** → k 는 색얼룩만 키운다(측정 채도/휘도 비 k=0→0, 0.3→0.48, 1.0→2.11). k=1 이 물리적으로 옳지만 층별 입상도 차·염료 확산이 없어 색얼룩이 과함 → 기본 0.3. ⚠️정규화는 셰이더가 인라인 계산(슬라이더라 실시간 변동), numpy 는 `coeffs.grain_color_norm(k)` — **양쪽 동시 수정**. 셰이더 상수는 `dot(LUMA,LUMA)`/`inversesqrt(3.0)` 로 유도해 리터럴 중복 없음. **Roughness 기본 0.1 = 실측**: acf lag1..8 에 (gridN, Roughness, USM)을 동시 피팅 — 스캔에 음수 lag 가 있어 **샤프닝을 같이 모델링해야** 편향이 안 생긴다. 가정한 a 와 무관하게 Roughness 0~0.2 수렴(0.5 는 잔차 8배). 실제 필름은 이 해상도에서 픽셀 너머 구조가 거의 없다. ⚠️샤프닝 강도 자체는 a 가 범위 상단까지 올라가 신뢰 불가. ⚠️Roughness 를 내려도 격자 재출현 없음(보간 제거 후 1px 너머 상관≈0 이라 이방성 가질 구조 없음. 이방성 비 지표는 분모≈0 으로 폭발하니 사용 금지). ⚠️문서의 '실측 lag1 0.234' 는 20241006 롤 부분집합 값이고 4롤 전체는 0.147(롤별 0.142~0.224). 샤프닝 포함 피팅은 gridN 2200~3000 을 내놓아 현재 기본 2900 이 타당 — 샤프닝 무시 피팅이 3800~4500 으로 편향됐던 것. ⚠️**Roughness/Color 는 coeffs 계수가 아니라 사진별 슬라이더**다(사이드카 키 `grainRough`/`grainColor`, 구버전 사이드카는 기본값 폴백 — 검증됨). export 는 셰이더 `hash23`/`valueNoise3` 를 numpy 로 이식(`pipeline._grain_field`, 스트립·이음매 없음, 26MP +5.0s) → **동일 연속 필드**를 샘플. ⚠️`_grain_vnoise3` 는 회전이 없어 좌표가 **분리형**인 점을 이용해 해시를 격자(ny×nx)에서 1회만 구하고 x 보간까지 격자 행에서 끝낸 뒤 픽셀로 펼친다 — 순진한 픽셀별 평가 대비 13.7s→5.0s 이고 **비트 단위 동일**(검증됨). 옥타브 회전을 다시 넣으면 이 최적화가 깨진다. **2×2 서브픽셀 평균**: 셀이 픽셀보다 작아 점샘플링하면 접힘 → 스캐너의 면적적분에 대응(`_GRAIN_SS` ↔ 셰이더 루프). ⚠️오프셋 텍셀은 **인스턴스별 1/렌더폭**(`grainTexelW/H`) — 기존 `texelW` 는 샤프닝 공간스케일용이라 pipeFull(풀해상도)에서도 프록시 텍셀이라 쓰면 GPU export 오프셋이 2.4배 틀어짐. ⚠️부작용: 고와질수록 픽셀 내 평균이 늘어 **세기 하락** → GRAIN 재환산(0.17→0.21) 후 size 0/0.5/1 = 7.6/9.0/10.8. ⚠️Size 는 Roughness/Color 와 달리 **정규화 안 함**(1.42배 변동): 방향이 Selwyn 과 같아 그럴듯하고(크기는 1.4 vs 3.5 로 안 맞음), 완전 정규화하려면 σ비가 cellPx×Roughness 2변수 함수라 비용 대비 이득 없음. 26MP 4.93s→22.7s. ⚠️**이산 입자(Boolean/Poisson) 모델은 스파이크 후 기각 — 재시도 금지**: 픽셀당 염료구름 12~127개라 CLT 구간(개별 분해엔 프레임폭 36,000px 필요) · 첨도가 실측 3.42/현재 2.81/이산 2.16~2.70 으로 **오히려 멀어짐** · 26MP 27~362s. docs/film_grain.md '기각된 시도'. ⚠️단 프리뷰=프록시·export=풀해상도라 **샘플링 밀도가 달라 픽셀일치는 여전히 아님**(구조/성격만 일치 — 해상도 상대 그레인의 본질적 한계). **남은 비물리**(향후 정공법 후보, 재검토 시 여기서 출발): ①노이즈 원시체가 **정규 격자 value-noise** — 실제는 무작위 위치 은염 결정의 이산 확률과정(푸아송/이항). 격자 흔적이 방향 이방성으로 잔존(멀티옥타브로 7.2→2.6 완화했을 뿐 제거 아님). ②**톤커브 뒤 display 공간 가산** — 실제는 톤커브 *앞* 의 밀도/노광 요동. GRAIN_TONE 벨은 이 위치 오류의 사후 보정. ⚠️**'filmic 앞 scene-linear 주입' 은 수치검증 결과 기각 — 재시도 금지**: 곱셈 주입 잔차 1.09 / 가산 3.21 / 곱셈×이항 0.75 vs 현재 display 벨 0.01. 원인은 `filmic()` 에 **toe 가 없어서**(sRGB OETF+숄더라 로그노광 기울기가 knee 까지 단조증가) 기울기 최대가 display 218 인데 실측 피크는 display 125 — 곱셈 주입하면 그레인이 하이라이트 최대가 됨. toe 도입은 전체 톤 파이프라인 변경이라 그레인 범위 밖. docs/film_grain.md '기각된 시도'. ③층별 크기·진폭 차등은 반영됨. 다만 **수치가 특정 스톡에 피팅되지 않음**(순서만 물리, 크기는 인용 범위 내 선택) — Color 슬라이더가 k=1 색얼룩 과잉을 누르는 인위적 다이얼인 이유. ④Selwyn 법칙(σ∝1/√면적)·감도↔입자크기 결합 없음(Amount/Size 독립). ⑤옥타브 가중은 실측 Wiener 스펙트럼 피팅이 아니라 눈대중 fBm |
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
- 셰이더 텍스처는 image provider 경로(Image→sampler)가 검증됨. Canvas→ShaderEffectSource 직접
  바인딩은 과거 검정화면 유발(커브 LUT를 provider 방식으로 전환해 해결).

## Export

- `pipeline.py` 가 풀해상도(6246×4170)를 동일 파이프라인으로 현상 → jpg/png/tif(8bit) 저장.
- **백그라운드 threading.Thread**(데몬)로 실행 → UI 안 멈춤. 26MP 전효과 ~40–50초(순수 CPU numpy,
  가우시안/LUT가 무거움). 메모리 위해 LUT 단계는 가로 스트립 처리, 공간단계는 전체 배열.
- 16bit TIFF 미지원(QImage 8bit). 필요 시 tifffile/imageio 추가.

## 향후 후보

디헤이즈 물리모델 계수 튜닝(라이트룸 나란히 비교 — DEHAZE_TMIN/RESID), 프리셋 저장·불러오기,
16bit GPU export, export 속도 최적화, 범위 마스크(휘도/색상), 그라디언트 필터.
