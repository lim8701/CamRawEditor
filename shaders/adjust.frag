#version 440

// Qt6 ShaderEffect 용 프래그먼트 셰이더 (Vulkan 스타일 GLSL).
// pyside6-qsb 로 .qsb 로 컴파일한 뒤 ShaderEffect.fragmentShader 에서 참조한다.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float exposure;     // 노출 (stop)
    // 필름시뮬 보정 노출(stop, 음수). 번들 LUT 은 룩이 아니라 후지 톤커브 전체를 담고 있어
    // filmic 위에 얹으면 톤커브가 두 번 걸린다(+0.8~1.4EV) — 그만큼 되돌린다. 이미지×시뮬
    // 상수로 CPU(pipeline.film_sim_ev)가 풀어 주고, 유저 Exposure 슬라이더와 별개다.
    float simExpEV;
    float contrast;     // 대비 (1.0=무변화)
    float wbR;          // WB 프리뷰 게인 (커밋되면 1)
    float wbG;
    float wbB;
    float highlights;   // 톤 영역 (-1..1)
    float shadows;
    float whites;
    float blacks;
    float texAmt;       // 텍스처 (-1..1) 중주파 로컬대비
    float clarity;      // 클래리티 (-1..1) 중간톤 로컬대비(큰 반경)
    float dehaze;       // 디헤이즈 (-1..1) +대비/채도/로컬대비 / -흰베일·플랫
    float vignette;     // 비네팅 (-1..1) 음수=가장자리 어둡게
    float lutSize;      // 3D LUT 한 변 N
    float lutStrength;  // 필름시뮬 강도 0..1 (1=LUT 그대로, 0=미적용)
    int   lutEnabled;   // 0=미적용
    float grainAmt;     // 필름 그레인 강도 0..1 (0=미적용)
    float grainSize;    // 입자 크기 0..1 (0=미세, 1=굵음)
    float grainRough;   // 입자 거칠기 0..1 = 옥타브 감쇠비(0=단일 옥타브, ↑=뭉침/불규칙)
    float grainColor;   // 층 독립도 0..1 (0=흑백 단층 필름, 1=R/G/B 3층 완전 독립=컬러 필름)
    float grainShape;   // 입자 모양 0=사각 셀(기본) / 1=원판. ⚠️pipeline grain_shape 와 일치
    float grainAspect;  // 프록시 가로/세로비 W/H (정사각 입자용)
    // ⚠️그레인 서브픽셀 평균용 **출력 텍셀**(1/렌더폭). texelW 는 샤프닝 공간스케일을 맞추려고
    // pipeFull(풀해상도)에서도 '프록시' 텍셀이라 여기 쓰면 안 된다 — 인스턴스별 1/width.
    float grainTexelW;
    float grainTexelH;
    float stampOn;      // 날짜 스탬프 표시 1/0
    float stampStrength;// 날짜 스탬프 가산 강도
    float saturation;   // 채도 (-1..1, 0=무변화, -1=흑백)
    float vibrance;     // 바이브런스 (-1..1, 저채도 우선 보정)
    float sharpenAmt;   // 샤프닝 강도 0..1 (USM, 휘도)
    float sharpenDetail;// 샤프닝 디테일 0..1 (미세 고주파 추가 강조)
    float sharpenMask;  // 샤프닝 마스킹 0..1 (1=강한 엣지 한정)
    float texelW;       // 1/procW (마스킹 그래디언트 스텝)
    float texelH;       // 1/procH
    // 카메라 네이티브 RGB -> 선형 sRGB 매트릭스 (행우선 9개, wb.cam_to_srgb_matrix)
    float camM0; float camM1; float camM2;
    float camM3; float camM4; float camM5;
    float camM6; float camM7; float camM8;
    // HSL 컬러 믹서: 8색상대(45° 균등) × 색상/채도/휘도 조정(-1..1). a=대역0..3, b=대역4..7.
    vec4 hslHa; vec4 hslHb;
    vec4 hslSa; vec4 hslSb;
    vec4 hslLa; vec4 hslLb;
    float clipWarn;     // 클리핑 경고 오버레이(1=표시): 하이라이트=빨강, 섀도=파랑. 프리뷰 전용.
    // 컬러 그레이딩(스플릿 토닝): 섀도/미드/하이라이트 색조 틴트. hue=0..1, sat=0..1, balance=-1..1.
    float cgHueSh; float cgSatSh;
    float cgHueMid; float cgSatMid;
    float cgHueHi; float cgSatHi;
    float cgBalance;
    float lumaNR;       // 휘도 노이즈 리덕션 0..1 (평탄부 고주파 억제, 엣지 보존)
    float colorNR;      // 컬러(chroma) 노이즈 리덕션 0..1 (색얼룩 제거, 휘도 불변)
    // 로컬 마스크 레이어(최대 5) — 각 skyMaskN(binding 9/13/14/15/16) 게이팅, 레이어별 독립 보정.
    //   A=(exp, hi, sh, dehaze)               전역 단계(0/3/6)에 강도 합산(전역과 동일 반응)
    //   B=(temp, tint, sat, contrast[중립 1.0]) 후반 9.7 블록
    //   C=(texture, clarity, invert[1/0], hasMask[1/0])
    // (비활성 레이어는 hasMask=0 → m=0 → 모든 항 항등. QML 은 미사용 레이어 값을 0/contrast=1 로 전달)
    vec4 skyA0; vec4 skyB0; vec4 skyC0;
    vec4 skyA1; vec4 skyB1; vec4 skyC1;
    vec4 skyA2; vec4 skyB2; vec4 skyC2;
    vec4 skyA3; vec4 skyB3; vec4 skyC3;
    vec4 skyA4; vec4 skyB4; vec4 skyC4;
    float skyShowLayer; // 마스크 오버레이(프리뷰 전용): 표시할 레이어 0..4, 미표시 = -1
    // 현상 계수(coeffs.py 단일 진실원, uniform 주입 — pipeline.py 와 값 공유, 셰이더 리터럴 제거).
    float dehazeKLocal; float dehazeKContrast; float dehazeKVeil; float dehazeKSat;
    float clarityK;     // 클래리티 강도(전역+하늘 공용)
    float textureK;     // 텍스처 강도(전역+하늘 공용)
    float skyTempK;     // 하늘 색온도 채널 게인
    float skyTintK;     // 하늘 틴트 채널 게인
    float toneHiShK;    // Highlights/Shadows 국소 노출 stop 스케일
    float toneWhBlK;    // Whites/Blacks 끝단 레벨 이동
    float vignetteK;    // 비네팅 방사 강도
    float grainK;       // 필름 그레인 강도
    float grainToneK;   // 그레인 노출 의존(0=균일, 1=미드톤 집중 — 특성곡선 기울기 모사)
    float grainToneGammaK;  // 위 벨의 비대칭 지수 γ (l^γ, γ<1 = 섀도 쪽으로 치우침)
    float grainToneFloorK;  // 톤 독립 바닥값 — 벨이 끝에서 0 이 되지만 실측은 0 이 아니다
    float grainSkewK;       // 그레인 왜도(3차): skew(l)=K·(1−2l) — 섀도 밝은점 / 하이라이트 어두운점
    float sharpenK;     // 언샤프 마스크 강도
    float hslHueDegK;   // HSL hue 시프트 최대(도)
    float hslLumK;      // HSL 휘도 조정 스케일
    float colorGradeK;  // 컬러 그레이딩 강도
    float displayCM;    // [프리뷰 전용] 1=디스플레이 색관리(sRGB→광색역 패널 보정). export=0.
    float cmLutSize;    // 디스플레이 색관리 LUT 한 변 N (0=미적용)
    // 디헤이즈 물리(DCP, '+' 방향): hazeT(binding 11)=투과율 맵, A=대기광(display sRGB),
    // conf=A-추정 신뢰도(어두운 장면 0 → dehazeTone 폴백). haze.py 가 이미지당 1회 추정.
    float hazeAr; float hazeAg; float hazeAb;
    float hazeConf;
    float dehazeKTmin;   // 유효 투과율 하한(0-나눗셈/노이즈 증폭 방지)
    float dehazeKResid;  // 물리 복원 위 잔여 톤모델 비율(라이트룸 체감 보정)
    float nrOn;          // 1=nrBase(디노이즈드 중성 luma) 준비됨. 0이면 휘도 NR 무동작(로드 직후 잠깐)
    float nrChroma;      // 1=nrBase 가 AI RGB 베이스(크로마 유효) → 컬러 NR 이 AI 크로마 사용
    float zoneShow;      // [프리뷰 전용] 1=존 시스템 오버레이(휘도를 존 0..X 로 양자화 표시). export=0.
    float hlDesat;       // 하이라이트 디새추 강도: 1=RAW(센서클립 색끼 제거), 0=일반 이미지 입력
    // 미스트(디퓨전) 필터 — 1단계. 모델/계수는 mist.py + coeffs.MIST_* (docs/mist_filter.md).
    // 커널 합성은 여기서 하지 않는다 — `mistfield.frag` 패스가 산란 필드 3장을 Character 무게로
    // 섞어 `mistScat`(프록시 해상도 RGBA16F)에 굽고, 여기서는 그것 하나만 섞는다.
    // ⚠️샘플러 슬롯을 아끼기 위한 구조다(아래 D3D11 16슬롯 주석 참조).
    float mistAmt;       // 강도 0..1 (0=미적용). k = mistAmt × mistK
    float mistK;         // Amount=1 에서의 산란 비율(coeffs.MIST_K)
    float mistOn;        // 1=산란 필드 준비됨. 0이면 무동작(로드 직후/재계산 중)
    float mistLogA;      // 산란 합성본 저장 코덱: v = (2^(code·logK) − 1)·A
    float mistLogK;      // = log2(1 + MIST_TEX_MAX / A). coeffs.MIST_TEX_* 주석 참조
    float mistColor;     // 산란광 색 복원 0..1 (0=물리). mist.tint_scatter 와 동일 수식
    float mistColorFloor;// 색 복원이 죽는 휘도 바닥(coeffs.MIST_COLOR_FLOOR)
} ubuf;

layout(binding = 1) uniform sampler2D src;       // 원본(카메라네이티브 감마 인코딩)
layout(binding = 2) uniform sampler2D lut;       // 3D LUT 아틀라스 (N*N x N)
layout(binding = 3) uniform sampler2D curve;     // 톤 커브 1D LUT (256x1)
layout(binding = 4) uniform sampler2D texBlur;   // dispSrc 가우시안 블러(작은 반경)
layout(binding = 5) uniform sampler2D claBlur;   // dispSrc 가우시안 블러(큰 반경)
// ⚠️**D3D11 은 스테이지당 샘플러가 16개뿐이다**(HLSL SM5.0 s0..s15). 아래 목록이 정확히 16개라
// 한 개라도 더 늘리면 런타임에 "maximum sampler register index exceeded" 로 파이프라인 생성이
// 실패한다(qsb 컴파일은 통과하므로 앱을 띄워야 드러난다 — 실제로 그렇게 한 번 터졌다).
// 늘려야 하면 단일채널인 skyMask0..4 를 RGBA 채널에 패킹해 3슬롯을 회수하는 것이 먼저다.
// (binding 6 은 원래 stampTex 였다 — 날짜 스탬프는 QML stampOverlay 가 그리므로 셰이더 경로가
//  죽은 코드였고, 그 슬롯을 미스트가 물려받았다.)
layout(binding = 6) uniform sampler2D mistScat;  // 미스트 산란 필드 합성본(mistfield.frag, 선형 float)
layout(binding = 7) uniform sampler2D dispSrc;   // src 의 display sRGB 변환본(블러/로컬대비 base)
layout(binding = 8) uniform sampler2D sharpBlur; // dispSrc 가우시안 블러(샤프닝 반경, 가변)
layout(binding = 9) uniform sampler2D skyMask0;  // 로컬 마스크 레이어0(단일채널 R, 프록시 해상도). 없으면 1x1 검정
layout(binding = 10) uniform sampler2D cmLut;    // 디스플레이 색관리 LUT 아틀라스(sRGB→모니터). 프리뷰 전용
layout(binding = 11) uniform sampler2D hazeT;    // 디헤이즈 투과율 맵(단일채널 R, 소형 — bilinear 업샘플). 없으면 1x1 흰색(t=1)
layout(binding = 12) uniform sampler2D nrBase;   // 디노이즈드 중성 베이스(프록시, RGBA64). 가이디드=luma 복제 그레이, AI=RGB(크로마 포함, nrChroma=1)
layout(binding = 13) uniform sampler2D skyMask1; // 로컬 마스크 레이어1(단일채널 R). 없으면 1x1 검정
layout(binding = 14) uniform sampler2D skyMask2; // 로컬 마스크 레이어2(단일채널 R). 없으면 1x1 검정
layout(binding = 15) uniform sampler2D skyMask3; // 로컬 마스크 레이어3(단일채널 R). 없으면 1x1 검정
layout(binding = 16) uniform sampler2D skyMask4; // 로컬 마스크 레이어4(단일채널 R). 없으면 1x1 검정


const vec3 LUMA = vec3(0.299, 0.587, 0.114);

// 로컬 조정 후반 블록(9.7) — 레이어별 호출. B=(temp,tint,sat,contrast), C=(texture,clarity,..).
// m=0 이면 모든 항 항등(비선택 픽셀 무영향). pipeline._sky_adjust 와 반드시 동일 수식.
vec3 applyLocalB(vec3 rgb, vec3 s0, vec2 uv, vec4 B, vec4 C, float m) {
    rgb.r *= (1.0 + B.x * ubuf.skyTempK * m);            // 색온도(+따뜻 R↑B↓)
    rgb.b *= (1.0 - B.x * ubuf.skyTempK * m);
    rgb.g *= (1.0 - B.y * ubuf.skyTintK * m);            // 틴트(+마젠타 G↓)
    if (C.x != 0.0)                                      // 텍스처(전역과 동일 base·계수)
        rgb += (s0 - texture(texBlur, uv).rgb) * C.x * ubuf.textureK * m;
    if (C.y != 0.0) {                                    // 클래리티(중간톤 가중)
        float d = dot(s0, LUMA) - dot(texture(claBlur, uv).rgb, LUMA);
        float l = dot(rgb, LUMA);
        rgb += d * C.y * ubuf.clarityK * (1.0 - abs(2.0 * l - 1.0)) * m;
    }
    rgb = (rgb - 0.5) * (1.0 + (B.w - 1.0) * m) + 0.5;   // 대비(전역 contrast 곱수)
    float la = dot(rgb, LUMA);
    rgb = mix(vec3(la), rgb, 1.0 + B.z * m);             // 채도
    return clamp(rgb, 0.0, 1.0);
}

// sRGB <-> linear (정확 EOTF, rawpy gamma=(2.4,12.92) 와 정합)
vec3 srgbToLinear(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}
vec3 linearToSrgb(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}
// 행우선 9-float 매트릭스 적용 (GLSL mat3 열우선 혼동 회피)
vec3 applyCamMat(vec3 v) {
    return vec3(dot(vec3(ubuf.camM0, ubuf.camM1, ubuf.camM2), v),
                dot(vec3(ubuf.camM3, ubuf.camM4, ubuf.camM5), v),
                dot(vec3(ubuf.camM6, ubuf.camM7, ubuf.camM8), v));
}

// 프록시 헤드룸(raw_loader.PROXY_HEADROOM 와 동일해야 함) 및 단일 필름릭 베이스 톤커브.
const float PROXY_HEADROOM = 4.0;
const float HL_KNEE = 0.7;   // wb.HL_KNEE 와 동일(하이라이트 숄더 시작, 선형)
// scene-linear sRGB(≥0) -> display sRGB[0,1]. knee 이상을 1.0 으로 점근 압축 후 OETF.
// wb.filmic / wb.highlight_rolloff 와 동일 수식(채널별).
vec3 filmic(vec3 x) {
    vec3 hi = max(x - HL_KNEE, 0.0);
    vec3 rolled = 1.0 - (1.0 - HL_KNEE) * exp(-hi / (1.0 - HL_KNEE));
    vec3 shoulder = mix(x, rolled, step(vec3(HL_KNEE), x));
    return linearToSrgb(shoulder);
}

// HSV <-> RGB (Sam Hocevar). hue 0..1.
vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    float e = 1.0e-10;
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
}
vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}
// HSL 컬러 믹서: 픽셀 hue 로 8색상대(45° 균등) 삼각 가중치 → 각 대역 조정의 가중합 적용.
// hueShift ±30°, sat/lum 은 채도·명도 배수. 가중치 합=1(균등 삼각 → 단위분할).
vec3 hslMixer(vec3 rgb) {
    if (ubuf.hslHa == vec4(0.0) && ubuf.hslHb == vec4(0.0)
        && ubuf.hslSa == vec4(0.0) && ubuf.hslSb == vec4(0.0)
        && ubuf.hslLa == vec4(0.0) && ubuf.hslLb == vec4(0.0)) return rgb;
    vec3 hsv = rgb2hsv(rgb);
    float h = hsv.x;
    // 8 대역 중심 c=i/8 의 삼각 가중치(wrap)
    vec4 ca = vec4(0.0, 1.0, 2.0, 3.0) / 8.0;
    vec4 cb = vec4(4.0, 5.0, 6.0, 7.0) / 8.0;
    vec4 wa = max(vec4(0.0), 1.0 - abs(fract(h - ca + 0.5) - 0.5) * 8.0);
    vec4 wb = max(vec4(0.0), 1.0 - abs(fract(h - cb + 0.5) - 0.5) * 8.0);
    float effH = dot(wa, ubuf.hslHa) + dot(wb, ubuf.hslHb);
    float effS = dot(wa, ubuf.hslSa) + dot(wb, ubuf.hslSb);
    float effL = dot(wa, ubuf.hslLa) + dot(wb, ubuf.hslLb);
    float satW = hsv.y;   // 무채색(회색)엔 색상/채도 조정 영향 최소화
    hsv.x = fract(hsv.x + effH * (ubuf.hslHueDegK / 360.0) * satW);
    hsv.y = clamp(hsv.y * (1.0 + effS), 0.0, 1.0);
    hsv.z = clamp(hsv.z * (1.0 + effL * ubuf.hslLumK), 0.0, 1.0);
    return hsv2rgb(hsv);
}

// 의사난수 해시 + 셀 노이즈 (필름 그레인용, 절차적·결정적)
// hash12: Dave Hoskins (https://www.shadertoy.com/view/4djSRW) — 곱셈해시의
// 세로/대각 줄무늬 아티팩트를 피한 고품질 해시.
float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
// 셀마다 난수 하나, **보간 없음**(경계가 딱 떨어진다).
// ⚠️ 보간을 넣으면 결이 뭉개진다. 실측 필름 acf lag1 0.234 에 대해 (gridN 2900 기준)
//    보간 없음 0.391 / smoothstep 0.504 / 선형 0.553 — 보간할수록 멀어진다. 게다가 보간은
//    서브픽셀 평균에서 σ 까지 깎아(보간없음 대비 smoothstep 0.81배) 곱기와 진하기를 맞바꾸게
//    만든다. 실제 염료 구름도 경계가 뚜렷한 덩어리다. 모서리 4개 해시+보간이 사라져 더 빠르다.
float cellNoise(vec2 p) { return hash12(floor(p)); }
// 원판 원시체 — 셀마다 해시로 **위치와 진폭**을 뽑아 반지름 GRAIN_DISK_R(셀 단위) 원판을 하나
// 뿌리고 3x3 이웃을 합산한다. 반환 평균 0 · **분산 1/12** 로 cellNoise-0.5 와 동일하다.
// 정규화가 해석적이다: 셀당 점 1개(밀도 1)라 한 점을 덮는 원판 수의 기대값 πR², 진폭 U[-0.5,0.5]
// (분산 1/12)가 독립 → Var = πR²/12. 1/(R√π) 를 곱하면 정확히 1/12.
//   → **σ 가 보존되고 아래 왜도 보정 v2 의 '분산 1/12' 전제도 그대로 유효**(경험 상수 없음).
// ⚠️R<1 이면 3x3 밖 셀의 점은 거리가 1 을 넘어 못 덮는다 — 이웃 범위를 넓힐 필요 없음.
// ⚠️pipeline._grain_disk 와 동일 수식(해시 오프셋 17.3/29.7, 53.1/71.9 포함).
// ⚠️샘플당 해시가 1 → 27 개로 는다. 체크했을 때만 드는 비용이지만 프리뷰가 무거워질 수 있다.
const float GRAIN_DISK_R = 0.55;   // ⚠️pipeline._GRAIN_DISK_R 과 일치(셀과 acf lag1 이 맞는 값)
float diskNoise(vec2 p) {
    vec2 ip = floor(p);
    float acc = 0.0;
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            vec2 c = ip + vec2(float(dx), float(dy));
            vec2 ctr = c + vec2(hash12(c), hash12(c + vec2(17.3, 29.7)));   // 셀 내 무작위 위치
            vec2 d = p - ctr;
            acc += (hash12(c + vec2(53.1, 71.9)) - 0.5)
                 * (1.0 - step(GRAIN_DISK_R * GRAIN_DISK_R, dot(d, d)));    // d² < R² 이면 1
        }
    }
    return acc * (1.0 / (GRAIN_DISK_R * 1.7724539));   // 1/(R√π) → 분산 1/12
}
// 원시체 선택. uniform 분기라 드로우 전체에서 조건이 같아 워프 발산이 없다.
float grainPrim(vec2 g) {
    if (ubuf.grainShape > 0.5) return diskNoise(g);
    return cellNoise(g) - 0.5;
}
// 그레인 옥타브 오프셋 — 없으면 세 격자가 원점에서 같은 정수 셀을 공유해 hash12 가 **같은 값**을
// 내고 옥타브가 독립이 아니게 된다(RMS 정규화 전제가 깨짐). ⚠️ pipeline._GRAIN_OFFSETS 와 일치.
const vec2 GRAIN_OFF1 = vec2(37.1, 17.3);
const vec2 GRAIN_OFF2 = vec2(91.7, 63.9);

// ── 컬러 필름 3층(R/G/B 발색층) — 층마다 입자 크기와 진폭이 다르다 ──────────────
// 청감층(옐로 발색): 고감도가 필요해 은염 결정이 크고 → 염료 구름도 가장 굵다
//   ("고감도 에멀전을 얻는 주된 방법이 큰 은염 결정이고, 이는 청감층에 필요" — Weaver & Long)
// 적감층(시안 발색): 가장 곱다 (실측 스펙상 녹감층 RMS 의 20~90%, 여기선 중간값 채택)
// **염료 확산은 별도 블러가 아니라 '유효 입자 크기 증가'로 흡수**했다 — 확산의 물리적 결과가
// 현상 단위가 커지는 것이므로. 그래서 컬러 그레인이 흑백보다 부드럽게 나온다.
// ⚠️ 아래 세 상수는 pipeline._GRAIN_LSIZE/_GRAIN_LAMP/_GRAIN_LOFF 와 반드시 일치.
// ⚠️ 층별 크기가 다르므로 한 좌표에서 난수 3개를 뽑는 hash23 트릭은 쓸 수 없다(격자가 다름).
const vec3 GRAIN_LSIZE = vec3(0.85, 1.00, 1.35);   // R,G,B 상대 입자 크기
const vec3 GRAIN_LAMP  = vec3(0.70, 1.00, 1.30);   // R,G,B 상대 진폭
const vec2 GRAIN_LOFF0 = vec2(0.0, 0.0);           // 층별 오프셋(크기가 같아도 독립 보장)
const vec2 GRAIN_LOFF1 = vec2(11.3, 47.9);
const vec2 GRAIN_LOFF2 = vec2(73.5, 29.1);

// 한 발색층의 멀티옥타브 필드(단위분산). sizeMul>1 = 셀 좌표 축소 = 입자가 굵어짐.
float grainLayer(vec2 g0, float r, float sizeMul, vec2 off) {
    vec2 g = g0 / sizeMul + off;
    float acc = grainPrim(g);
    // 기여가 없는 옥타브는 건너뛴다 — numpy(_grain_field_1 의 amp<1e-4 break)와 같은 임계라
    // 결과가 정확히 같고, r 은 uniform 이라 워프 발산도 없다. Roughness 0 일 때 원시체 호출이
    // 3→1 로 줄어 특히 원판(diskNoise, 해시 27배)에서 프리뷰가 크게 가벼워진다.
    if (r >= 1e-4) {
        acc += grainPrim(g * 0.5 + GRAIN_OFF1) * r;
        if (r * r >= 1e-4)
            acc += grainPrim(g * 0.25 + GRAIN_OFF2) * (r * r);
    }
    return acc * inversesqrt(1.0 + r * r + r * r * r * r);   // 옥타브 RMS 보존
}

// 한 서브픽셀 샘플의 3층 그레인(채널별, 평균 0). ⚠️ pipeline._grain_field_1 과 동일 수식.
// 멀티 옥타브(fBm) — 단일 옥타브는 셀 크기가 하나뿐이라 규칙적/디지털하다. 실제 에멀전은
// 결정 크기 분포 + 뭉침(clumping)으로 Wiener 스펙트럼이 넓다.
// ⚠️ 옥타브는 '거친 쪽'(f/2, f/4)으로만 쌓는다 — 미세 쪽은 셀이 더 작아져 서브픽셀 평균으로도
//    감당이 안 되고, 프록시/풀해상도에서 다르게 접혀 프리뷰≠export 가 된다.
// 3층 에멀전(R/G/B 발색층) — 컬러 필름엔 '휘도 입자'가 따로 없고 휘도 요동은 세 층의 합이다.
// 그래서 층을 먼저 만들고 공통 성분(mono)을 거기서 뽑는다. 층마다 입자 크기·진폭이 다르다.
vec3 grainField(vec2 suv, float gridN, float r, float k, float skewC) {
    vec2 g0 = suv * vec2(gridN, gridN / ubuf.grainAspect);   // 정사각 셀 좌표
    vec3 e = vec3(grainLayer(g0, r, GRAIN_LSIZE.r, GRAIN_LOFF0),
                  grainLayer(g0, r, GRAIN_LSIZE.g, GRAIN_LOFF1),
                  grainLayer(g0, r, GRAIN_LSIZE.b, GRAIN_LOFF2)) * GRAIN_LAMP;
    //   k=0 → 색 성분 제거(휘도 요동만) · k=1 → 세 층 완전 독립 = 컬러 필름의 물리
    float aLen = length(GRAIN_LAMP);
    float mono = (e.r + e.g + e.b) / aLen;               // 층 합 = 휘도 요동(단위분산)
    // 휘도 σ 보존 정규화 — Var(dot(LUMA,n)) = (1−k)² + k²·Σ(Lᵢaᵢ)² + 2(1−k)k·Σ(Lᵢaᵢ²)/|a|.
    // mono 가 층들의 합이라 층과 상관이 있어 교차항이 붙는다 → k 를 돌려도 그레인 '세기'는
    // 불변, 색 얼룩만 늘어난다. 상수는 LUMA·GRAIN_LAMP 에서 유도(리터럴 중복 없음, 상수
    // 폴딩됨). ⚠️ pipeline._grain_color_norm 과 동일 식.
    vec3 la = LUMA * GRAIN_LAMP;
    float nrm = inversesqrt((1.0 - k) * (1.0 - k) + k * k * dot(la, la)
                            + 2.0 * (1.0 - k) * k * dot(LUMA, GRAIN_LAMP * GRAIN_LAMP) / aLen);
    vec3 n = mix(vec3(mono), e, k) * nrm;
    // 왜도(3차) — **서브픽셀 평균 전, 샘플 단위**로 건다. 이 지점의 채널별 분산은 해석적으로
    // 알 수 있어(셀 값이 정확히 균일분포 → 분산 1/12) 평균이 **정확히 0** 이 된다. 평균을
    // 모르면 그레인이 미세한 밝기 이동을 만든다(측정: 공칭 σ 쓰면 최대 0.6/255).
    //   Var_i = [(1−k)² + k²aᵢ² + 2(1−k)k·aᵢ²/|a|] · nrm²/12
    vec3 aa = GRAIN_LAMP * GRAIN_LAMP;
    vec3 v2 = ((1.0 - k) * (1.0 - k) + k * k * aa + 2.0 * (1.0 - k) * k * aa / aLen)
              * (nrm * nrm / 12.0);
    return n + skewC * (n * n - v2);
}

// 디헤이즈 톤모델 — 전역 dehaze(6단계) + 하늘 dehaze(9.7단계) 공용. amt=강도(하늘은 ×마스크),
// lc=로컬대비(휘도). 계수는 uniform(coeffs.py). pipeline._dehaze_core 와 동일 수식.
vec3 dehazeTone(vec3 rgb, float amt, float lc) {
    rgb += lc * amt * ubuf.dehazeKLocal;                       // 로컬 대비
    rgb = (rgb - 0.5) * (1.0 + amt * ubuf.dehazeKContrast) + 0.5;  // 대비
    rgb = mix(rgb, vec3(0.92), max(-amt, 0.0) * ubuf.dehazeKVeil); // 흰 베일(amt<0)
    float l = dot(rgb, LUMA);
    return mix(vec3(l), rgb, 1.0 + amt * ubuf.dehazeKSat);     // 채도
}

// 디헤이즈 공용 — 전역(6단계) + 하늘 로컬(9.7단계, amt=강도×마스크) 공유.
// amt>0 & conf>0: DCP 물리 복원 (c−A)/te+A + 잔여 톤모델을 conf 로 톤모델과 블렌드.
// 그 외(음수·어두운 장면·t-맵 준비 전): 톤 모델. pipeline._dehaze_apply 와 동일 수식.
vec3 dehazeApply(vec3 rgb, float amt, float lc, float t) {
    vec3 tone = dehazeTone(rgb, amt, lc);
    if (amt > 0.0 && ubuf.hazeConf > 0.0) {
        float te = max(1.0 - amt * (1.0 - t), ubuf.dehazeKTmin);
        vec3 A = vec3(ubuf.hazeAr, ubuf.hazeAg, ubuf.hazeAb);
        vec3 phys = dehazeTone((rgb - A) / te + A, amt * ubuf.dehazeKResid, lc);
        return mix(tone, phys, ubuf.hazeConf);
    }
    return tone;
}

// 톤 영역별
//  - 하이라이트/섀도우 = 국소 노출(멀티플리커티브 게인 c*2^g): 색비·대비 보존, 회색화 방지.
//    ★마스크는 '국소 평균 휘도'(lb=큰반경 블러 휘도)로 계산 → 라이트룸식 로컬 톤맵.
//    픽셀 휘도로 마스킹하면 어두운 영역 속 밝은 디테일이 안 올라가 밋밋·이질적이라
//    라이트룸과 느낌이 다름. 주변 밝기로 판단해 영역째 들어올리고 로컬 대비는 보존한다.
//  - 화이트/블랙 = 끝단 레벨(가산, 픽셀 휘도): 화이트/블랙 포인트(클리핑 지점) 이동.
vec3 tone_zones(vec3 c, float lb, float hi, float sh, float wh, float bl) {
    // 라이트룸식: 범위를 넓혀 미드톤(0.25~0.75)에서 shadows/highlights 가 겹치게.
    float shMask = 1.0 - smoothstep(0.0, 0.75, lb);
    float hiMask = smoothstep(0.25, 1.0, lb);
    c *= exp2(sh * ubuf.toneHiShK * shMask + hi * ubuf.toneHiShK * hiMask);   // 국소 노출(stop)
    float l = dot(c, LUMA);
    float whMask = smoothstep(0.75, 1.0, l);              // 화이트/블랙은 끝단(좁게) 유지
    float blMask = 1.0 - smoothstep(0.0, 0.25, l);
    c += vec3(wh * ubuf.toneWhBlK * whMask + bl * ubuf.toneWhBlK * blMask);   // 끝단 레벨 이동
    return c;
}

vec3 lut_texel(float ri, float gi, float bi, float N) {
    float x = (bi * N + ri + 0.5) / (N * N);
    float y = (gi + 0.5) / N;
    return texture(lut, vec2(x, y)).rgb;
}

// 디스플레이 색관리 LUT 샘플(cmLut 전용, lut_texel/apply_lut 와 동일 좌표 규약).
vec3 cm_texel(float ri, float gi, float bi, float N) {
    float x = (bi * N + ri + 0.5) / (N * N);
    float y = (gi + 0.5) / N;
    return texture(cmLut, vec2(x, y)).rgb;
}
vec3 apply_cm_lut(vec3 col, float N) {
    vec3 c = clamp(col, 0.0, 1.0) * (N - 1.0);
    vec3 b0 = floor(c);
    vec3 b1 = min(b0 + 1.0, N - 1.0);
    vec3 f  = c - b0;
    vec3 c000 = cm_texel(b0.r, b0.g, b0.b, N);
    vec3 c100 = cm_texel(b1.r, b0.g, b0.b, N);
    vec3 c010 = cm_texel(b0.r, b1.g, b0.b, N);
    vec3 c110 = cm_texel(b1.r, b1.g, b0.b, N);
    vec3 c001 = cm_texel(b0.r, b0.g, b1.b, N);
    vec3 c101 = cm_texel(b1.r, b0.g, b1.b, N);
    vec3 c011 = cm_texel(b0.r, b1.g, b1.b, N);
    vec3 c111 = cm_texel(b1.r, b1.g, b1.b, N);
    vec3 c00 = mix(c000, c100, f.r);
    vec3 c01 = mix(c001, c101, f.r);
    vec3 c10 = mix(c010, c110, f.r);
    vec3 c11 = mix(c011, c111, f.r);
    vec3 c0  = mix(c00, c10, f.g);
    vec3 c1  = mix(c01, c11, f.g);
    return mix(c0, c1, f.b);
}

vec3 apply_lut(vec3 col, float N) {
    vec3 c = clamp(col, 0.0, 1.0) * (N - 1.0);
    vec3 b0 = floor(c);
    vec3 b1 = min(b0 + 1.0, N - 1.0);
    vec3 f  = c - b0;
    vec3 c000 = lut_texel(b0.r, b0.g, b0.b, N);
    vec3 c100 = lut_texel(b1.r, b0.g, b0.b, N);
    vec3 c010 = lut_texel(b0.r, b1.g, b0.b, N);
    vec3 c110 = lut_texel(b1.r, b1.g, b0.b, N);
    vec3 c001 = lut_texel(b0.r, b0.g, b1.b, N);
    vec3 c101 = lut_texel(b1.r, b0.g, b1.b, N);
    vec3 c011 = lut_texel(b0.r, b1.g, b1.b, N);
    vec3 c111 = lut_texel(b1.r, b1.g, b1.b, N);
    vec3 c00 = mix(c000, c100, f.r);
    vec3 c01 = mix(c001, c101, f.r);
    vec3 c10 = mix(c010, c110, f.r);
    vec3 c11 = mix(c011, c111, f.r);
    vec3 c0  = mix(c00, c10, f.g);
    vec3 c1  = mix(c01, c11, f.g);
    return mix(c0, c1, f.b);
}

void main() {
    vec2 uv = qt_TexCoord0;

    // 하늘 마스크(0=비선택) — 마스크 노출/톤존을 전역과 **같은 단계·같은 수식**으로 적용하기
    // 위해 상단에서 1회 계산(강도만 마스크로 게이팅). 마스크 없으면 1x1 검정 텍스처 → 0.
    // 레이어별 마스크 알파(0=비선택). C.w=hasMask, C.z=invert.
    float skyM0 = (ubuf.skyC0.w > 0.5) ? mix(texture(skyMask0, uv).r, 1.0 - texture(skyMask0, uv).r, ubuf.skyC0.z) : 0.0;
    float skyM1 = (ubuf.skyC1.w > 0.5) ? mix(texture(skyMask1, uv).r, 1.0 - texture(skyMask1, uv).r, ubuf.skyC1.z) : 0.0;
    float skyM2 = (ubuf.skyC2.w > 0.5) ? mix(texture(skyMask2, uv).r, 1.0 - texture(skyMask2, uv).r, ubuf.skyC2.z) : 0.0;
    float skyM3 = (ubuf.skyC3.w > 0.5) ? mix(texture(skyMask3, uv).r, 1.0 - texture(skyMask3, uv).r, ubuf.skyC3.z) : 0.0;
    float skyM4 = (ubuf.skyC4.w > 0.5) ? mix(texture(skyMask4, uv).r, 1.0 - texture(skyMask4, uv).r, ubuf.skyC4.z) : 0.0;

    // 0) scene-linear 프론트엔드: 헤드룸 디코드 → WB(카메라공간) → cam→sRGB 매트릭스
    //    → 유저 노출(scene-linear 배수) → filmic(단일 베이스 톤커브) → display sRGB.
    //    src 는 헤드룸 인코딩(code=oetf(L/H)) 카메라네이티브 → ×H 로 scene-linear 복원.
    //    마스크 노출(skyExp)도 여기서 합산 — 전역 노출과 동일한 진짜 stop(filmic 롤오프 적용).
    vec3 cam = srgbToLinear(texture(src, uv).rgb) * PROXY_HEADROOM;

    // 1) 미스트(디퓨전) 필터 — **WB/매트릭스/노출보다 앞**(카메라네이티브 scene-linear).
    //    그 셋은 픽셀마다 같은 선형 연산이라 블러와 정확히 교환되므로 결과는 같으면서 산란
    //    필드가 세 슬라이더와 무관해진다 → 이미지당 1회 계산으로 충분하고 균일항(프레임 평균)이
    //    근사가 아니라 정확해진다. ⚠️pipeline.py 1단계(mist.apply)와 동일 수식이어야 한다.
    if (ubuf.mistAmt > 0.0 && ubuf.mistOn > 0.5) {
        vec3 sc = clamp(texture(mistScat, uv).rgb, 0.0, 1.0);
        // 코덱 해제(로그 인코딩) → 선형광. mistfield.frag 의 엔코드와 짝이어야 한다.
        sc = (exp2(sc * ubuf.mistLogK) - 1.0) * ubuf.mistLogA;
        // 색 복원(룩 노브, 0=물리) — 산란광의 **휘도는 두고 색만** 받는 픽셀 쪽으로.
        // ⚠️필드 패스가 아니라 여기서 해야 한다: 블러된 필드가 아니라 **원래 픽셀**이 필요하다.
        //   그 덕에 uniform 이라 실시간이다. mist.tint_scatter 와 동일 수식.
        if (ubuf.mistColor > 0.0) {
            float nl = dot(cam, LUMA);
            float g = clamp(ubuf.mistColor, 0.0, 1.0)
                    * smoothstep(0.0, ubuf.mistColorFloor, nl);   // 준-흑색은 색비가 노이즈뿐
            sc = mix(sc, cam * (dot(sc, LUMA) / max(nl, 1e-6)), g);
        }
        float mk = ubuf.mistAmt * ubuf.mistK;
        cam = (1.0 - mk) * cam + mk * sc;
    }

    cam *= vec3(ubuf.wbR, ubuf.wbG, ubuf.wbB);
    vec3 lin = applyCamMat(cam) * pow(2.0, ubuf.exposure + ubuf.simExpEV
                 + ubuf.skyA0.x * skyM0 + ubuf.skyA1.x * skyM1 + ubuf.skyA2.x * skyM2
                 + ubuf.skyA3.x * skyM3 + ubuf.skyA4.x * skyM4);
    vec3 rgb = filmic(lin);                                  // → display sRGB[0,1]

    // 0.5) 하이라이트 디새추레이션: near-clip 센서클립 색끼(예: 불꽃 코어 청록) 제거 → 중성.
    //      ⚠️쿨(청/녹 우세) 하이라이트만 중성화 — 밝은 빨강/주황 광원(네온·간판)은 보존.
    //      max(G,B)-R 게이트(따뜻한 색은 음수→0). filmic 뒤 display 공간.
    //      ⚠️hlDesat=0(일반 이미지 입력)이면 통째로 끈다 — 센서 클립이 없는 display-referred
    //      소스에선 밝은 파랑/청록이 '정상 색'이라 이 단계가 하늘·네온을 흰색으로 날린다
    //      (실측: 실사진 최대 11 code / 채도 높은 파랑은 흰색까지). pipeline._dehaze 옆 동일 게이트.
    if (ubuf.hlDesat > 0.0) {
        float mx = max(rgb.r, max(rgb.g, rgb.b));
        float cool = max(rgb.g, rgb.b) - rgb.r;
        rgb = mix(rgb, vec3(mx), ubuf.hlDesat
                  * smoothstep(0.95, 1.0, mx) * smoothstep(0.05, 0.35, cool));
    }

    // 3) 톤 영역별 — hi/sh 마스크 = 중성 dispSrc(claBlur) 국소 평균 휘도(노출 무관, 장면 구조 기준).
    //    마스크 하이라이트/섀도(skyHi/skyShadows)도 여기서 강도 합산 — 전역과 동일한
    //    영역 톤맵(국소 평균 휘도·넓은 범위)으로 반응(과거 9.7 픽셀휘도 근사는 이질적이라 폐기).
    float lb = dot(texture(claBlur, uv).rgb, LUMA);
    rgb = max(tone_zones(rgb, lb,
                         ubuf.highlights + ubuf.skyA0.y * skyM0 + ubuf.skyA1.y * skyM1 + ubuf.skyA2.y * skyM2
                                         + ubuf.skyA3.y * skyM3 + ubuf.skyA4.y * skyM4,
                         ubuf.shadows    + ubuf.skyA0.z * skyM0 + ubuf.skyA1.z * skyM1 + ubuf.skyA2.z * skyM2
                                         + ubuf.skyA3.z * skyM3 + ubuf.skyA4.z * skyM4,
                         ubuf.whites, ubuf.blacks), 0.0);

    vec3 s0 = texture(dispSrc, uv).rgb;          // display sRGB 변환본(블러 비교용)

    // 3.5) 노이즈 리덕션 (텍스처/샤프닝 앞 — 노이즈를 먼저 줄이고 디테일 강조). 중성 베이스 기반.
    if (ubuf.lumaNR > 0.0 || ubuf.colorNR > 0.0) {
        // 휘도 NR: 노이즈 성분 = 중성 luma − 디노이즈드 베이스 luma(nrBase, CPU/AI 1회 계산).
        // 가이디드 베이스는 luma 복제 그레이라 dot(nb,LUMA)==luma — 두 베이스 공용 수식.
        vec3 nb = texture(nrBase, uv).rgb;
        if (ubuf.lumaNR > 0.0 && ubuf.nrOn > 0.5) {
            float noiseL = dot(s0, LUMA) - dot(nb, LUMA);
            rgb -= vec3(noiseL * ubuf.lumaNR);
        }
        // 컬러 NR: chroma 노이즈(색얼룩) 제거. AI 베이스(nrChroma=1)면 중성 chroma −
        // AI 디노이즈드 chroma(디테일 보존형), 아니면 기존 큰반경(claBlur) 기준.
        if (ubuf.colorNR > 0.0) {
            vec3 chromaDetail;
            if (ubuf.nrChroma > 0.5 && ubuf.nrOn > 0.5) {
                chromaDetail = (s0 - dot(s0, LUMA)) - (nb - dot(nb, LUMA));
            } else {
                vec3 bl = texture(claBlur, uv).rgb;
                chromaDetail = (s0 - dot(s0, LUMA)) - (bl - dot(bl, LUMA));
            }
            rgb -= chromaDetail * ubuf.colorNR;
        }
        rgb = clamp(rgb, 0.0, 1.0);
    }

    // 4) 텍스처 — 중주파 디테일 (원본 - 작은반경 블러)
    if (ubuf.texAmt != 0.0) {
        rgb += (s0 - texture(texBlur, uv).rgb) * ubuf.texAmt * ubuf.textureK;
    }

    // 5) 클래리티 — 중간톤 로컬대비 (휘도, 큰 반경 블러)
    if (ubuf.clarity != 0.0) {
        float d = dot(s0, LUMA) - dot(texture(claBlur, uv).rgb, LUMA);
        float l = dot(rgb, LUMA);
        float mid = 1.0 - abs(2.0 * l - 1.0);    // 중간톤 가중
        rgb += d * ubuf.clarity * ubuf.clarityK * mid;
    }

    // 5.5) 샤프닝 — 언샤프 마스크(휘도). 반경 블러 고주파 + Detail 미세 고주파,
    //      Masking 으로 엣지 한정(평탄부 노이즈 증폭 억제). 휘도에만 가산 → 색 불변.
    if (ubuf.sharpenAmt > 0.0) {
        float Ld = dot(s0, LUMA);                            // dispSrc 휘도
        float Lr = dot(texture(sharpBlur, uv).rgb, LUMA);    // 반경 블러 휘도
        float Lt = dot(texture(texBlur, uv).rgb, LUMA);      // 미세 블러 휘도
        float hp = (Ld - Lr) + ubuf.sharpenDetail * (Ld - Lt);
        float gx = dot(texture(dispSrc, uv + vec2(ubuf.texelW, 0.0)).rgb, LUMA)
                 - dot(texture(dispSrc, uv - vec2(ubuf.texelW, 0.0)).rgb, LUMA);
        float gy = dot(texture(dispSrc, uv + vec2(0.0, ubuf.texelH)).rgb, LUMA)
                 - dot(texture(dispSrc, uv - vec2(0.0, ubuf.texelH)).rgb, LUMA);
        float edge = smoothstep(0.0, 0.06, length(vec2(gx, gy)));
        float mask = mix(1.0, edge, ubuf.sharpenMask);
        rgb += vec3(hp * ubuf.sharpenAmt * ubuf.sharpenK * mask);
    }

    // 6) 디헤이즈 — '+': DCP 물리 복원 I=J·t+A(1−t) 역산(t-맵/대기광, conf 게이팅) 위에
    //    잔여 톤모델(라이트룸 '펀치'). '−'·어두운 장면(conf→0)·t-맵 준비 전: 톤모델(흰 베일).
    //    t-맵은 슬라이더와 무관(이미지당 1회) → 드래그 실시간.
    //    마스크 디헤이즈(skyDehaze)도 여기서 강도 합산 — 전역과 같은 단계(LUT/커브 전)에서
    //    동일하게 반응(과거 9.7 적용은 LUT/커브 뒤라 같은 값에도 결과가 달랐음).
    {
        float dAmt = ubuf.dehaze + ubuf.skyA0.w * skyM0 + ubuf.skyA1.w * skyM1 + ubuf.skyA2.w * skyM2
                                 + ubuf.skyA3.w * skyM3 + ubuf.skyA4.w * skyM4;
        if (dAmt != 0.0) {
            float ld = dot(s0, LUMA) - dot(texture(claBlur, uv).rgb, LUMA);
            rgb = dehazeApply(rgb, dAmt, ld, texture(hazeT, uv).r);
        }
    }
    rgb = clamp(rgb, 0.0, 1.0);

    // 7) 필름 시뮬레이션 3D LUT (강도 블렌딩 = 라이트룸 프로파일 Amount)
    if (ubuf.lutEnabled != 0) {
        vec3 looked = apply_lut(rgb, ubuf.lutSize);
        rgb = mix(rgb, looked, ubuf.lutStrength);
    }

    // 7.5) 바이브런스/채도 (luma 축 mix -> 휘도 보존)
    if (ubuf.vibrance != 0.0) {
        float l = dot(rgb, LUMA);
        float cur = max(rgb.r, max(rgb.g, rgb.b)) - min(rgb.r, min(rgb.g, rgb.b));
        float f = 1.0 + ubuf.vibrance * (1.0 - clamp(cur, 0.0, 1.0));  // 저채도일수록 강하게
        rgb = clamp(mix(vec3(l), rgb, f), 0.0, 1.0);
    }
    if (ubuf.saturation != 0.0) {
        float l = dot(rgb, LUMA);
        rgb = clamp(mix(vec3(l), rgb, 1.0 + ubuf.saturation), 0.0, 1.0);
    }

    // 7.6) HSL 컬러 믹서 (색상대별 색상/채도/휘도) — 필름시뮬/채도 뒤, 대비 앞
    rgb = clamp(hslMixer(rgb), 0.0, 1.0);

    // 8) 대비
    rgb = clamp((rgb - 0.5) * ubuf.contrast + 0.5, 0.0, 1.0);

    // 9) 톤 커브 (채널별: LUT R/G/B 열에 마스터→채널 합성 커브가 구워져 있음)
    rgb.r = texture(curve, vec2(rgb.r, 0.5)).r;
    rgb.g = texture(curve, vec2(rgb.g, 0.5)).g;
    rgb.b = texture(curve, vec2(rgb.b, 0.5)).b;

    // 9.5) 컬러 그레이딩(스플릿 토닝): 휘도 마스크(섀도/미드/하이라이트) × 색조 틴트.
    //      balance 는 휘도 감마로 마스크 분포를 이동(+ = 하이라이트 쪽으로).
    if (ubuf.cgSatSh > 0.0 || ubuf.cgSatMid > 0.0 || ubuf.cgSatHi > 0.0) {
        float L = dot(rgb, LUMA);
        float Lb = pow(clamp(L, 0.0, 1.0), exp2(-ubuf.cgBalance));
        float wsh = clamp(1.0 - 2.0 * Lb, 0.0, 1.0);
        float whi = clamp(2.0 * Lb - 1.0, 0.0, 1.0);
        float wmid = 1.0 - wsh - whi;
        vec3 dsh  = (hsv2rgb(vec3(ubuf.cgHueSh,  1.0, 1.0)) - 0.5) * ubuf.cgSatSh;
        vec3 dmid = (hsv2rgb(vec3(ubuf.cgHueMid, 1.0, 1.0)) - 0.5) * ubuf.cgSatMid;
        vec3 dhi  = (hsv2rgb(vec3(ubuf.cgHueHi,  1.0, 1.0)) - 0.5) * ubuf.cgSatHi;
        rgb = clamp(rgb + (dsh * wsh + dmid * wmid + dhi * whi) * ubuf.colorGradeK, 0.0, 1.0);
    }

    // 9.7) 하늘(로컬) 조정 — skyM(상단 계산) 게이팅. display sRGB 공간.
    //      ⚠️노출(skyExp)·하이라이트/섀도(skyHi/skyShadows)·디헤이즈(skyDehaze)는 여기가 아니라
    //        전역과 같은 단계(0/3/6)에서 강도 합산으로 적용됨 — 전역 조절과 동일한 반응 보장.
    //      m 스케일이 국소화하므로 별도 mix 불필요(m=0 인 곳은 모든 항이 항등 → 영향 없음).
    // 마스크가 실제로 있을 때만 적용(export 의 sky_mask is not None 게이트와 정합).
    //      레이어 0→1→2→3→4 순서로 적용(pipeline._sky_adjust 순서와 동일 — 파리티). m=0 이면 항등.
    if (ubuf.skyC0.w > 0.5) rgb = applyLocalB(rgb, s0, uv, ubuf.skyB0, ubuf.skyC0, skyM0);
    if (ubuf.skyC1.w > 0.5) rgb = applyLocalB(rgb, s0, uv, ubuf.skyB1, ubuf.skyC1, skyM1);
    if (ubuf.skyC2.w > 0.5) rgb = applyLocalB(rgb, s0, uv, ubuf.skyB2, ubuf.skyC2, skyM2);
    if (ubuf.skyC3.w > 0.5) rgb = applyLocalB(rgb, s0, uv, ubuf.skyB3, ubuf.skyC3, skyM3);
    if (ubuf.skyC4.w > 0.5) rgb = applyLocalB(rgb, s0, uv, ubuf.skyB4, ubuf.skyC4, skyM4);
    // 마스크 오버레이(프리뷰 전용): 지정 레이어의 선택 영역을 붉게(export 는 skyShowLayer=-1)
    if (ubuf.skyShowLayer >= 0.0) {
        float ms = (ubuf.skyShowLayer < 0.5) ? skyM0 : (ubuf.skyShowLayer < 1.5) ? skyM1
                 : (ubuf.skyShowLayer < 2.5) ? skyM2 : (ubuf.skyShowLayer < 3.5) ? skyM3 : skyM4;
        rgb = mix(rgb, vec3(0.95, 0.25, 0.25), ms * 0.5);
    }

    // 10) 비네팅 (방사형)
    if (ubuf.vignette != 0.0) {
        float r = length(uv - 0.5) / 0.7071;
        rgb *= 1.0 + ubuf.vignette * ubuf.vignetteK * smoothstep(0.35, 1.0, r);
    }

    // 11) 날짜 스탬프는 여기서 그리지 않는다 — QML `stampOverlay` 가 최종 프레임(크롭/회전 적용
    //     후) 기준으로 그린다. 셰이더 경로는 원본 코너 기준이라 프리뷰=export 정합이 성립하지
    //     않아 폐기됐고(pipe/pipeFull 둘 다 stampOn=0 고정), 그 sampler 슬롯은 미스트가 쓴다.
    //     ⚠️되살리려 하지 말 것 — 슬롯이 없고, 정합 불가는 이미 결론이 난 사안이다.

    // 12) 필름 그레인 (에멀전 입자) — 맨 끝: 장면과 날짜 스탬프 모두에 입혀짐.
    if (ubuf.grainAmt > 0.0) {
        // 실제 필름에 맞춘 셀 크기는 프록시 픽셀보다 작다(측정: 실측 필름 ≈ gridN 4000).
        // 점 샘플링하면 접히므로 **2x2 서브픽셀 평균** — 스캐너가 픽셀 면적을 적분하는 것에 대응.
        // ⚠️ GRAIN_SS / gridN 범위는 pipeline._GRAIN_SS 및 grain_grid_n 과 반드시 일치.
        // 셀 크기는 긴 변 기준(min(1,aspect) 보정) — 세로 사진에서 셀이 잘아져 그레인이
        // 약해지고(σ −17%) 결정이 픽셀 아래로 내려가는 문제의 수정. ⚠️pipeline 과 동일.
        float gridN = mix(4500.0, 1300.0, ubuf.grainSize) * min(1.0, ubuf.grainAspect);
        // 왜도 계수 — 실측: 섀도는 밝은 점(+), 하이라이트는 어두운 점(−). skew≈6cσ (가우시안 근사),
        // σ=1/√12 (샘플 단위 필드의 해석적 표준편차). ⚠️pipeline 과 동일 식.
        float l0 = clamp(dot(rgb, LUMA), 0.0, 1.0);
        float skewC = ubuf.grainSkewK * (1.0 - 2.0 * l0) * sqrt(12.0) / 6.0;
        vec3 n = vec3(0.0);
        for (int syi = 0; syi < 2; ++syi) {
            for (int sxi = 0; sxi < 2; ++sxi) {
                vec2 suv = uv + vec2((sxi == 0 ? -0.25 : 0.25) * ubuf.grainTexelW,
                                     (syi == 0 ? -0.25 : 0.25) * ubuf.grainTexelH);
                n += grainField(suv, gridN, ubuf.grainRough, ubuf.grainColor, skewC);
            }
        }
        n *= 0.25;
        // 노출 의존 진폭(에멀전 물리): 보이는 톤 요동 = 밀도 요동 × 특성곡선 기울기.
        // 기울기가 미드톤 최대·양 끝 0 이므로 벨 곡선으로 변조 — 흰 하늘/검은 그림자에
        // 노이즈가 뿌려지던 '디지털' 느낌 제거. 미드톤(l=0.5)은 w=1 이라 기존 룩 그대로.
        // display 공간 휘도라 벨이 이미 섀도 쪽으로 치우침(disp 0.5 ≈ linear 0.21)
        // = 실제 필름에서 그레인이 하이라이트보다 섀도에 더 보이는 것과 일치.
        // ⚠️K>1(실측 1.29)이면 끝단에서 음수가 되므로 클램프 필수 — 음수면 노이즈가 반전된다.
        //   하한은 0 이 아니라 grainToneFloorK(실측 바닥값) — pipeline 그레인 블록과 동일.
        float lg = pow(l0, ubuf.grainToneGammaK);
        float w = max(ubuf.grainToneFloorK, mix(1.0, sqrt(4.0 * lg * (1.0 - lg)), ubuf.grainToneK));
        rgb += n * ubuf.grainAmt * ubuf.grainK * w;
    }

    rgb = clamp(rgb, 0.0, 1.0);

    // 클리핑 경고(프리뷰 전용 진단 오버레이): 어느 채널이든 클리핑되면 표시.
    //   하이라이트(>=254/255)=빨강, 섀도(<=1/255)=파랑. export(pipeFull)에선 clipWarn=0.
    if (ubuf.clipWarn > 0.5) {
        if (max(max(rgb.r, rgb.g), rgb.b) >= 0.9961)      rgb = vec3(1.0, 0.0, 0.0);
        else if (min(min(rgb.r, rgb.g), rgb.b) <= 0.0039) rgb = vec3(0.1, 0.45, 1.0);
    }

    // 존 시스템 오버레이(프리뷰 전용 진단): 최종 display 휘도를 안셀 아담스 존 0..X 로 양자화.
    //   1존 = 1스톱(선형 log2), 존 V = 18% 미들그레이 기준. 표시는 균등 11단계 그레이 램프(존/10),
    //   양 끝단만 유채색(존 0=파랑, 존 X=빨강 — 클리핑 경고와 동일 색 언어). export 는 zoneShow=0.
    if (ubuf.zoneShow > 0.5) {
        float linL = dot(srgbToLinear(rgb), vec3(0.2126, 0.7152, 0.0722));
        float z = 5.0 + log2(max(linL, 1e-6) / 0.18);
        float zi = clamp(floor(z + 0.5), 0.0, 10.0);
        rgb = (zi < 0.5) ? vec3(0.10, 0.25, 0.62)
            : (zi > 9.5) ? vec3(0.82, 0.16, 0.16)
            : vec3(zi / 10.0);
    }

    // [프리뷰 전용] 디스플레이 색관리: sRGB 출력을 광색역 패널(Display-P3) 보정.
    //   비색관리 표시 경로에서 sRGB 값이 P3 패널에 과포화로 나오는 것을 사전 보정(채도↓)해
    //   화면이 정확한 sRGB(=export)로 보이게 한다. P3 는 sRGB 와 동일 TRC → primaries 행렬만.
    //   export(pipeFull/render_full)는 displayCM=0 으로 미적용(표준 sRGB 유지).
    //   변환은 모니터 ICC 프로파일에서 구운 3D LUT(cmLut, display_cm.py) — 색역·화이트·TRC 정확.
    if (ubuf.displayCM > 0.5 && ubuf.cmLutSize > 1.5) {
        rgb = clamp(apply_cm_lut(rgb, ubuf.cmLutSize), 0.0, 1.0);
    }

    fragColor = vec4(rgb, 1.0) * ubuf.qt_Opacity;
}
