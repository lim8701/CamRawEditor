"""현상 계수 단일 진실원 — 셰이더(uniform 주입)와 pipeline.py(numpy)가 공유한다.

여기 값을 바꾸면 프리뷰(GPU 셰이더)와 CPU export(pipeline.py) 양쪽에 동시 반영된다
(예전엔 셰이더 리터럴 ↔ pipeline 리터럴을 따로 고쳐야 했고, 한쪽을 빠뜨리면 프리뷰≠export).
계수 변경 시 셰이더 재컴파일도 불필요(uniform 주입) — 라이트룸 비교 튜닝 반복이 빨라짐.

전역 톤(Highlights/Shadows 1.0, Whites/Blacks 0.3, Vignette 0.8, Grain 0.12 등)도 여기 정의돼
셰이더에는 uniform, pipeline 에는 coeffs.* 로 주입된다(리터럴 중복 없음).
"""

# 디헤이즈 톤모델 — '−'(흰 베일) 방향 + 물리 모델 폴백(어두운 장면)용. 셰이더 dehazeTone == pipeline._dehaze_core.
DEHAZE_LOCAL = 0.4      # 로컬대비 가산
DEHAZE_CONTRAST = 0.25  # 대비
DEHAZE_VEIL = 0.22      # 흰 베일(amt<0, 밝아짐)
DEHAZE_SAT = 0.3        # 채도

# 디헤이즈 물리 모델(DCP, '+' 방향 — haze.py 가 이미지당 t-맵/대기광/conf 추정). 셰이더 6단계 == pipeline._dehaze.
DEHAZE_TMIN = 0.15      # 유효 투과율 하한(짙은 안개서 0-나눗셈/노이즈 증폭 방지)
DEHAZE_RESID = 0.35     # 물리 복원 위에 남기는 톤모델 비율(라이트룸 체감의 대비/채도 '펀치' 보정)

CLARITY = 0.8           # 클래리티(중간톤 로컬대비)
TEXTURE = 1.6           # 텍스처(중주파)

# 휘도 노이즈 리덕션(가이디드 필터) — 중성 베이스 luma 를 CPU 로 1회 디노이즈해
# 프리뷰는 텍스처(main.py NR 워커), export 는 pipeline 이 직접 같은 필터로 계산.
# 셰이더 uniform 아님(텍스처에 베이크) — 값 변경 시 재시작만 하면 양쪽 반영.
NR_RADIUS = 4           # 가이디드 필터 반경(프록시 px, export 는 해상도비로 스케일)
NR_EPS = 0.0015         # 정규화 분산 임계(작을수록 엣지 보존↑·디노이즈↓)

SKY_TEMP = 0.20         # 하늘 색온도 채널 게인
SKY_TINT = 0.15         # 하늘 틴트(녹-마젠타) 채널 게인

# 전역 톤(tone_zones / 비네팅 / 그레인). 셰이더 tone_zones·10·12단계 == pipeline._tone_zones / render_full.
TONE_HISH = 1.0         # Highlights/Shadows 국소 노출 stop 스케일
TONE_WHBL = 0.3         # Whites/Blacks 끝단 레벨 이동
VIGNETTE = 0.8          # 비네팅 방사 강도
# 필름 그레인 강도(슬라이더 1.0 에서의 천장).
# 목표는 실측 **9.22/255** — Noritsu 스캔 4롤의 미드톤 σ 가 9.65~10.41 인데 스캐너 샤프닝이 그걸
# 부풀렸을 것(1.2~1.5× 추정)이라 정확히 맞추는 값 대신 그 아래로 잡았다.
# 이 값이 **grainSize=0 에서 정확히 재현**된다(σ 9.21, 그 지점의 acf lag1 0.242 도 실측 0.234 와
# 일치) — 즉 **슬라이더 최저 크기 = 실측 35mm 컬러 네거**. 굵어질수록 진해진다(기본 0.5 에서
# 11.04, 최대 1.0 에서 13.36) → 슬라이더 최대가 실제 필름을 **넘어서므로** 고를 여지가 생긴다
# (이전엔 최대가 필름에 못 미쳤다).
# ⚠️Size 를 움직이면 세기가 1.45배 변한다. Roughness/Color 와 달리 정규화하지 않은 이유:
#   방향이 Selwyn 법칙(고운 입자=낮은 granularity)과 같아 물리적으로 그럴듯하고, 완전 정규화하려면
#   σ비가 cellPx×Roughness 2변수 함수라 셰이더·numpy 양쪽에 피팅을 박아야 해 비용 대비 이득이 없다.
GRAIN = 0.21
# 그레인 노출 의존(에멀전 물리): 보이는 톤 요동 = 입자 밀도 요동 × 특성곡선 기울기.
# 기울기는 미드톤(직선부) 최대, toe(섀도)/shoulder(하이라이트)에서 0 → 양 끝에서 그레인이 사라진다.
# 0=균일, 1=완전 변조. 미드톤 진폭은 값과 무관하게 항상 1.0(룩 보존).
# ★실측 기반: Noritsu 필름 스캔 4롤·151프레임의 평탄 패치에서 σ vs 휘도를 재고
#   w = max(0, mix(1, √(4·(l^γ)·(1−l^γ)), K)) 를 피팅. 롤별 K = 1.27~1.36(평균 1.29, 편차 3%).
# ⚠️γ(비대칭)를 한 번 기각했다가 되살렸다. 처음엔 필름 여백을 피하려 **휘도 18 미만을 제외**하고
#   피팅했는데, γ 를 결정하는 게 바로 그 섀도 구간이라 롤마다 0.64~0.98 로 흔들려 보였다. 섀도를
#   포함해 다시 재니 K 가 오히려 안정되고(±0.07→±0.04) **네 롤 모두 γ<1** 로 같은 방향이며 잔차가
#   0.053→0.027 로 반감. 대칭 벨은 섀도에서 10~15% 부족하고 상위 미드톤에서 7~12% 과했다.
#   ("바닥값 wmin" 도 시도했으나 측정 구간에서 한 번도 발동하지 않아 무효 — 형태 문제였다.)
# ⚠️K>1 이면 끝단에서 w<0 → 셰이더·pipeline 양쪽에서 max(0,·) 클램프 필수.
GRAIN_TONE = 1.29
GRAIN_TONE_GAMMA = 0.88   # l^γ (γ<1 = 곡선을 섀도 쪽으로, 피크가 display 128→116)
# 그레인 3차 모멘트(왜도) — ★실측: **섀도에서는 밝은 점, 하이라이트에서는 어두운 점**.
# 스캔 왜도 중앙값이 휘도에 대해 거의 선형(l=0.12 → +0.43, 0.39 → +0.11, 0.86 → −0.39)이라
#   skew(l) ≈ GRAIN_SKEW·(1−2l)
# 로 두고 2차 왜곡 n += c·(n²−σ²), c = skew/(6σ) 로 부과한다(가우시안 근사에서 skew≈6cσ).
# ⚠️상하위 1% 를 잘라내도 남으므로(+0.43→+0.31, −0.39→−0.31) 먼지 등 오염이 아니라 분포의 성질.
#   부호가 톤에 따라 뒤집히는 것이 결정적 — 먼지는 스캔에서 항상 밝은 점이라 −쪽을 설명 못 한다.
# 물리적으로는 진폭과 같은 원인(끝단 톤 압축)에서 나오는데, 우리는 진폭만 벨로 '부과'했기 때문에
# 왜도가 따라오지 않았다. 그래서 별도로 넣는다.
# ⚠️서브픽셀 평균이 왜도를 희석하므로(입자 크기에만 의존: grainSize 0/0.5/1.0 에서 2.11/1.68/1.47배,
#   휘도와는 무관) **기본 크기 기준으로 역보정**한 값이다: 0.55 × 1.68 = 0.92.
#   따라서 Size 를 움직이면 달성 왜도가 0.44~0.63 으로 변한다(목표 0.55, ±20%). 세기가 Size 에 따라
#   1.45배 변하는 것과 같은 성격이라 그대로 둔다 — 없애려면 희석배율을 cellPx 함수로 피팅해
#   셰이더·numpy 양쪽에 박아야 하는데 3차 모멘트에 그만한 값어치가 없다.
GRAIN_SKEW = 0.92
# ⚠️왜곡은 **서브픽셀 평균 전, 샘플 단위**에 건다. 그 지점의 채널별 분산은 해석적으로 알 수 있어
#   (셀 값이 정확히 균일분포 → 분산 1/12) 평균이 **정확히 0** 이 된다. 공칭 σ 를 쓰면 실제 σ 와
#   어긋난 만큼 그레인이 밝기를 옮긴다(측정: grainSize 1.0 에서 0.6/255) — 그레인이 노출을
#   바꾸면 안 되므로 해석적 분산을 쓴다. 대신 평균 후 왜도가 약간 희석되는데 그건 무해하다.
# ⚠️그레인 Roughness(옥타브 감쇠비)/Color(층 독립도)는 **계수가 아니라 사진별 슬라이더 값**
#   (`grainRough`/`grainColor`)이다. 기본값은 다른 슬라이더와 동일하게 QML `value:` 와
#   pipeline `p.get(...)` 에 리터럴로 둔다(0.5 / 0.3). 여기 상수로 만들면 안 됨.
# ⚠️그레인 모델의 **구조** 상수(옥타브 오프셋·층별 크기/진폭·정규화)는 계수가 아니라
#   셰이더와 짝을 이루는 모델 정의라 `pipeline.py` 의 그레인 블록에 모여 있다.

# 기타 강도 계수 (샤프닝 / HSL 믹서 / 컬러 그레이딩)
SHARPEN = 1.5           # 언샤프 마스크 강도
HSL_HUE_DEG = 30.0      # HSL 색상대 hue 시프트 최대(도)
HSL_LUM = 0.5           # HSL 휘도 조정 스케일
COLOR_GRADE = 0.5       # 컬러 그레이딩(스플릿 토닝) 강도


def as_qml_dict():
    """QML ShaderEffect uniform 바인딩용 (controller.adjustCoeffs). 셰이더 uniform 이름과 일치."""
    return {
        "dehazeKLocal": DEHAZE_LOCAL, "dehazeKContrast": DEHAZE_CONTRAST,
        "dehazeKVeil": DEHAZE_VEIL, "dehazeKSat": DEHAZE_SAT,
        "dehazeKTmin": DEHAZE_TMIN, "dehazeKResid": DEHAZE_RESID,
        "clarityK": CLARITY, "textureK": TEXTURE,
        "skyTempK": SKY_TEMP, "skyTintK": SKY_TINT,
        "toneHiShK": TONE_HISH, "toneWhBlK": TONE_WHBL,
        "vignetteK": VIGNETTE, "grainK": GRAIN,
        "grainToneK": GRAIN_TONE, "grainToneGammaK": GRAIN_TONE_GAMMA,
        "grainSkewK": GRAIN_SKEW,
        "sharpenK": SHARPEN, "hslHueDegK": HSL_HUE_DEG,
        "hslLumK": HSL_LUM, "colorGradeK": COLOR_GRADE,
    }
