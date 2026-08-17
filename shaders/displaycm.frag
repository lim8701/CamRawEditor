#version 440

// 디스플레이 색관리 전용 패스(프리뷰 한정). src(=display sRGB)에 sRGB→모니터 3D LUT 를
// 적용해 광색역 패널에서 정확한 sRGB 로 보이게 한다. `Compare original` 모드에서 dispPre(=무편집
// display sRGB, convert.frag 출력)에 CM 을 입히는 데 쓴다. adjust.frag 의 apply_cm_lut 과 동일 수식.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float displayCM;    // 1=색관리 적용
    float cmLutSize;    // CM LUT 한 변 N (0=미적용)
    float hlDesat;      // 하이라이트 디새추 강도: 1=RAW / 0=일반 이미지 입력(adjust.frag 와 동일 의미)
} ubuf;

layout(binding = 1) uniform sampler2D src;     // display sRGB 입력(dispPre)
layout(binding = 2) uniform sampler2D cmLut;   // sRGB→모니터 LUT 아틀라스

// adjust.frag 의 cm_texel/apply_cm_lut 와 동일 좌표 규약(트라이리니어).
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

void main() {
    vec3 rgb = texture(src, qt_TexCoord0).rgb;

    // 하이라이트 디새추레이션(쿨 하이라이트 → 중성): adjust.frag 0.5 단계와 동일 수식.
    // src(dispPre/convert.frag)는 이 단계가 없으므로 Compare original 이 무편집 pipe 와 어긋났다
    // (밝은 쿨 하이라이트=하늘/구름에서 색끼 차이). 여기서 같은 디새추를 거쳐 정합시킨다.
    // ⚠️hlDesat 게이트도 adjust.frag/pipeline 과 **똑같이** 받아야 한다 — 일반 이미지 입력에서
    //   여기만 디새추가 살아 있으면 Compare original 창만 밝은 파랑을 흰색으로 날려
    //   '있지도 않은 편집 차이'를 보여준다(실측 최대 250/255).
    if (ubuf.hlDesat > 0.0) {
        float mx = max(rgb.r, max(rgb.g, rgb.b));
        float cool = max(rgb.g, rgb.b) - rgb.r;
        rgb = mix(rgb, vec3(mx), ubuf.hlDesat
                  * smoothstep(0.95, 1.0, mx) * smoothstep(0.05, 0.35, cool));
    }

    if (ubuf.displayCM > 0.5 && ubuf.cmLutSize > 1.5) {
        rgb = clamp(apply_cm_lut(rgb, ubuf.cmLutSize), 0.0, 1.0);
    }
    fragColor = vec4(rgb, 1.0) * ubuf.qt_Opacity;
}
