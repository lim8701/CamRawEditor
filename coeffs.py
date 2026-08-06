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
GRAIN = 0.12            # 필름 그레인 강도
# 그레인 노출 의존(에멀전 물리): 보이는 톤 요동 = 입자 밀도 요동 × 특성곡선 기울기.
# 기울기는 미드톤(직선부) 최대, toe(섀도)/shoulder(하이라이트)에서 0 → 양 끝에서 그레인이 사라진다.
# 0=균일(옛 동작), 1=완전 변조. 미드톤 진폭은 값과 무관하게 항상 1.0(기존 룩 보존).
GRAIN_TONE = 0.7
# ⚠️그레인 Roughness(옥타브 감쇠비)/Color(층 독립도)는 **계수가 아니라 사진별 슬라이더 값**
#   (`grainRough`/`grainColor`)이다. 기본값은 다른 슬라이더와 동일하게 QML `value:` 와
#   pipeline `p.get(...)` 에 리터럴로 둔다(0.5 / 0.3). 여기 상수로 만들면 안 됨.
_LUMA_SQ = 0.299 ** 2 + 0.587 ** 2 + 0.114 ** 2   # |LUMA|² = 0.446966
_INV_SQRT3 = 3.0 ** -0.5                          # 0.57735 = Cov(mono, 층)


def grain_color_norm(k):
    """3층 혼합 mix(mono, e, k) 후 **휘도** 그레인 σ 를 k 와 무관하게 유지하는 계수.
    Var(dot(LUMA, n)) = (1−k)² + k²·|LUMA|² + 2(1−k)k/√3  (mono 는 층 합이라 층과 상관 있음).
    → k 를 돌려도 그레인 '세기'는 그대로고 색 얼룩만 늘어난다(옥타브 정규화와 같은 원칙).
    셰이더는 같은 식을 인라인 계산(슬라이더라 실시간 변동) — 수정 시 양쪽 동시."""
    return ((1.0 - k) ** 2 + k * k * _LUMA_SQ + 2.0 * (1.0 - k) * k * _INV_SQRT3) ** -0.5

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
        "vignetteK": VIGNETTE, "grainK": GRAIN, "grainToneK": GRAIN_TONE,
        "sharpenK": SHARPEN, "hslHueDegK": HSL_HUE_DEG,
        "hslLumK": HSL_LUM, "colorGradeK": COLOR_GRADE,
    }
