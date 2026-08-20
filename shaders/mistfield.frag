#version 440

// 미스트 산란 필드 합성 패스 — CPU 가 만든 3개 스케일 필드를 Character 무게로 섞어
// `mistScat` 하나로 굽는다. 무게 혼합은 **디코드한 선형광**에서 하고, 출력은 같은 로그 코덱으로
// 다시 담으므로 항상 [0,1] 코드다 — 즉 FBO 가 RGBA8 로 떨어져도 **잘리지 않는다**.
// RGBA16F 를 쓰는 것은 정밀도 선택이지 클리핑 회피가 아니다(코덱이 이미 막는다).
//
// 왜 별도 패스인가: D3D11 은 스테이지당 샘플러가 16개뿐이고 adjust.frag 가 이미 16개를 쓴다.
// 3장을 거기서 직접 읽으면 런타임에 파이프라인 생성이 실패한다(실측). 여기서 미리 섞으면
// adjust.frag 는 슬롯 하나로 끝나고, Character 는 여전히 uniform 이라 실시간이다.
//
// ⚠️필드 3장을 그대로 노출하지 않는 대가: Character 를 움직이면 이 패스만 다시 돈다(프록시
//   해상도 1패스라 사실상 공짜). Radius/Highlight 는 필드 자체가 바뀌므로 CPU 재계산이 필요하다.
// ⚠️수식은 mist.apply(pipeline) 의 커널 합성부와 동일해야 한다 — 한쪽만 고치면 프리뷰≠export.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float mistChar;      // 성격 0=블랙 미스트 / 1=화이트 미스트 (커널 무게 배분)
    float mistLogA;      // 저장 코덱: v = (2^(code·logK) − 1)·A   (coeffs.MIST_TEX_* 주석)
    float mistLogK;      // = log2(1 + MIST_TEX_MAX / A)
    float mistMeanR;     // 균일항 = 산란 소스의 프레임 평균(σ→∞, 베일링 글레어)
    float mistMeanG;
    float mistMeanB;
    vec4  mistWBlack;    // 무게 (narrow, mid, wide, uniform) — 블랙 미스트
    vec4  mistWWhite;    // 같은 순서 — 화이트 미스트
} ubuf;

// 산란 필드 — **로그 코덱**으로 인코딩돼 있다(coeffs.MIST_TEX_* 주석). 각기 σ 에 맞는 축소
// 해상도라 **bilinear 업샘플 전제**(필드가 매끄러워 무해).
// 준비 전엔 1x1 검정 → adjust.frag 의 mistOn=0 이 미스트를 끈다.
layout(binding = 1) uniform sampler2D mistS0;    // narrow (σ = 긴변 0.25%)
layout(binding = 2) uniform sampler2D mistS1;    // mid    (σ = 긴변 1%)
layout(binding = 3) uniform sampler2D mistS2;    // wide   (σ = 긴변 4%)

vec3 decode(vec3 c) {                            // 코덱 해제 → 선형광
    return (exp2(clamp(c, 0.0, 1.0) * ubuf.mistLogK) - 1.0) * ubuf.mistLogA;
}

// ±0.5 LSB(8bit) 디더 — 양자화 오차를 '등고선'에서 '노이즈'로 바꾼다. 프래그먼트 좌표만의
// 함수라 프레임마다 흔들리지 않는다. ⚠️채널마다 다른 해시여야 한다(하나면 휘도 노이즈만 되고
// 색 등고선이 남는다).
float hashD(vec2 p) {
    vec3 q = fract(vec3(p.xyx) * 0.1031);
    q += dot(q, q.yzx + 33.33);
    return fract((q.x + q.y) * q.z);
}

void main() {
    vec2 uv = qt_TexCoord0;
    vec4 mw = mix(ubuf.mistWBlack, ubuf.mistWWhite, clamp(ubuf.mistChar, 0.0, 1.0));
    mw /= max(mw.x + mw.y + mw.z + mw.w, 1e-6);           // 커널 정규화(∫P=1)
    // ⚠️무게 혼합은 **디코드한 선형광**에서 한다(코덱 공간 평균은 물리적으로 틀리다).
    vec3 scat = mw.x * decode(texture(mistS0, uv).rgb)
              + mw.y * decode(texture(mistS1, uv).rgb)
              + mw.z * decode(texture(mistS2, uv).rgb)
              + mw.w * vec3(ubuf.mistMeanR, ubuf.mistMeanG, ubuf.mistMeanB);
    // 같은 코덱으로 다시 담는다 — 이 FBO 가 RGBA8 로 떨어져도 (a) 1.0 에서 잘리지 않고
    // (b) 어두운 쪽 정밀도가 유지된다. RGBA16F 면 사실상 무손실.
    vec3 code = log2(1.0 + max(scat, 0.0) / ubuf.mistLogA) / ubuf.mistLogK;
    vec2 fc = gl_FragCoord.xy;
    vec3 d = vec3(hashD(fc), hashD(fc + vec2(37.1, 17.3)), hashD(fc + vec2(91.7, 53.9)));
    code = clamp(code + (d - 0.5) * (1.0 / 255.0), 0.0, 1.0);
    fragColor = vec4(code, 1.0) * ubuf.qt_Opacity;
}
