"""미스트(디퓨전) 필터 — scene-linear 산란 모델. 셰이더 adjust.frag 1단계 == 이 모듈.

렌즈 앞 미세 입자가 빛의 일부를 산란시키는 광학을 그대로 옮긴다:

    out = (1-k)·L  +  k·(P ⊗ (L·E))

  L = **카메라네이티브 scene-linear** (헤드룸 디코드 직후 — 유저 WB·cam→sRGB 매트릭스·노출 전)
  k = 산란 비율 (Amount)
  P = 정규화된 산란 커널(∫P=1) — 가우시안 3개 + 균일항의 합
  E = 하이라이트 보상 (아래)

**왜 프론트엔드 맨 앞(카메라네이티브)인가.** 산란은 렌즈에서 일어나므로 filmic 앞이어야 한다는
것만으로는 위치가 안 정해진다. 유저 WB·매트릭스·노출은 모두 **픽셀마다 같은 선형 연산**이라
블러와 정확히 교환되므로(blur∘linear == linear∘blur), 미스트를 그 셋보다 앞에 두면 결과는
같으면서 **산란 필드가 세 슬라이더와 무관**해진다. 그래서 프리뷰 산란 필드를 이미지당 1회
계산해 캐시할 수 있고, 균일항(프레임 평균)도 근사가 아니라 정확해진다.
⚠️E 의 임계만 공간에 의존한다(max 채널이 어느 공간인지). 그래서 프리뷰와 export 가 **같은
공간(카메라네이티브)** 에서 E 를 계산해야 하고, `MIST_HL*` 도 그 공간 기준으로 튜닝된 값이다.

**단일 가우시안이 아닌 이유.** 실제 디퓨전 필터의 산란 프로파일은 좁은 코어 + 아주 넓고
옅은 스커트(글레어 산란 계열의 1/θ² 꼬리)의 합이다. 단일 가우시안은 두 갈래로만 실패한다 —
σ 를 좁게 잡으면 소프트포커스(해상력 손실), 넓게 잡으면 아무 변화가 안 보인다. σ 를 기하
간격(비 4)으로 두고 무게를 나누면 그 구간에서 멱함수 꼬리를 근사한다(점광원 실측 프로파일:
화이트 무게에서 r^-2.1, 블랙 무게에서 r^-3.3 — 블랙이 더 급한 것이 의도한 방향).

⚠️선명한 코어는 커널이 아니라 **(1−k)·L 항**이 담당한다. 그래서 커널의 가장 좁은 성분도
'보이는 후광' 크기여야 한다 — σ 를 1~2px 로 두면 산란이 아니라 미세 블러가 되어 고주파만
깎인다(첫 시도에서 실측으로 확인). 고주파 유지율이 대략 (1−k) 로 떨어지는 것은 정상이며
물리적으로 맞다(산란된 비율만큼 모든 주파수의 대비가 준다).

**블랙 vs 화이트 미스트.** 별개 알고리즘이 아니라 **같은 커널의 무게 배분**이다. 화이트는
투명 입자만이라 산란광이 다 살아남아 넓은 성분·균일항이 커지고(→ 화면 전체 베일, 블랙 상승),
블랙은 검은 입자가 넓게 퍼진 산란광을 흡수해 후광이 하이라이트 주변에 머문다(→ 블랙 유지).
`char` 0=블랙 / 1=화이트 가 두 무게 세트를 보간한다. 균일항(=σ→∞)은 프레임 평균이며
베일링 글레어의 표준 모델이다.

⚠️ **미측정 모델이다.** 커널 모양은 글레어 산란 문헌의 형상(1/θ² 꼬리)을 prior 로 쓴 것이고,
실제 필터를 재서 피팅한 값이 아니다(그레인/디헤이즈와 다른 지위). 실측 A/B 페어가 확보되면
`coeffs.MIST_*` 만 교체하면 되도록 계수를 전부 밖으로 빼 뒀다.

**하이라이트 보상(E)이 필요한 이유.** 현실에서 창문·간판·태양은 미드톤의 수십~수백 배이고
후광 밝기는 그 초과 에너지에서 나온다. 그런데 센서에서 클리핑된 픽셀은 기록값이 실제보다
한참 낮아, 그대로 블러하면 후광이 아니라 '회색 뿌연 막'이 된다. E 는 클립 근처 픽셀의
잃어버린 초과 에너지를 근사 복원한다(스칼라 → 색 불변). E=1 로 두면 순수 물리 모델.

⚠️ E 의 임계는 **유저 노출과 무관**해야 한다 — 클리핑은 촬영 시점에 센서에서 일어난 사건이고,
나중에 노출 슬라이더를 올렸다고 더/덜 클리핑된 것이 되지는 않는다. 프론트엔드 맨 앞에서 계산하는
것이 곧 그 뜻이다(자동노출 게인은 이미 `nat` 에 들어 있으므로 기준은 카메라 측광).
"""

import numpy as np
from scipy.ndimage import gaussian_filter, zoom

import coeffs

# 색 복원(tint_scatter)에서 '받는 픽셀 휘도'를 재는 가중치. 셰이더 LUMA 와 동일해야 한다.
# ⚠️카메라네이티브 공간이라 엄밀한 휘도는 아니다 — 룩 노브의 스칼라라 일관성만 있으면 된다.
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _blur_small(img, sigma_px):
    """(축소 블러본, 배율 f). σ 가 크면 1/f 해상도에서 계산(비용이 면적×σ 라 그대로는 못 쓴다).

    f = σ/8 — 오차가 σ/f 하나로 정해진다(date_stamp 글로우와 같은 규칙, 피크오차 ~2%).
    ⚠️축소는 **면적평균**이어야 한다. 점샘플은 하이라이트를 에일리어싱해 같은 비용에 오차가
      2~3배가 된다(date_stamp 에서 실측된 함정).
    프리뷰는 이 축소본을 그대로 텍스처로 올려 **GPU 가 bilinear 업샘플**한다(메모리·대역폭).
    """
    h, w = img.shape[:2]
    f = max(1, int(sigma_px / 8.0))
    hh, ww = (h // f) * f, (w // f) * f
    if f == 1 or hh < f or ww < f:             # 너무 작아 축소 불가 → 원해상도
        return gaussian_filter(img, sigma=(sigma_px, sigma_px, 0), mode="nearest"), 1
    small = img[:hh, :ww].reshape(hh // f, f, ww // f, f, 3).mean(axis=(1, 3))
    return gaussian_filter(small, sigma=(sigma_px / f, sigma_px / f, 0), mode="nearest"), f


def _blur(img, sigma_px):
    """`_blur_small` 을 원해상도로 되돌린 것(export 경로 — numpy 안에서 합성한다)."""
    small, f = _blur_small(img, sigma_px)
    if f == 1:
        return small
    h, w = img.shape[:2]
    up = zoom(small, (h / small.shape[0], w / small.shape[1], 1.0), order=1, mode="nearest")
    return up[:h, :w]


def weights(char):
    """산란 커널 무게 (narrow, mid, wide, uniform) — 합 1. char 0=블랙 / 1=화이트."""
    c = float(np.clip(char, 0.0, 1.0))
    wb = np.array(coeffs.MIST_W_BLACK, dtype=np.float64)
    ww = np.array(coeffs.MIST_W_WHITE, dtype=np.float64)
    w = wb + (ww - wb) * c
    return w / w.sum()


def sigmas(radius, long_edge_px):
    """가우시안 3성분의 σ(px). 프레임 긴 변 비율 × Radius → 해상도 무관(프록시=export 정합)."""
    return [s * float(radius) * float(long_edge_px) for s in coeffs.MIST_SIGMA]


def hl_boost(lin, hi):
    """하이라이트 보상 E (스칼라, 색 불변). hi=0 이면 1.0(순수 물리)."""
    if hi <= 0.0:
        return None
    mx = lin.max(axis=2, keepdims=True)
    return 1.0 + hi * _smoothstep(coeffs.MIST_HL0, coeffs.MIST_HL1, mx)


def scatter_source(nat, hi):
    """산란 소스 L·E (카메라네이티브 scene-linear). 커널을 걸기 전 단계."""
    e = hl_boost(nat, float(hi))
    return nat if e is None else (nat * e).astype(np.float32)


def scatter_fields(nat, hi, radius, long_edge_px):
    """[프리뷰] 3개 산란 필드(**축소본**)와 균일항(프레임 평균)을 돌려준다.

    무게 섞기는 셰이더가 한다 — Amount/Character 는 uniform 이라 실시간이고, 여기 인자인
    Radius/Highlight 만 재계산을 부른다. 반환 배열은 각기 `_blur_small` 의 자연 해상도라
    셰이더 쪽에서 bilinear 업샘플된다(필드가 매끄러워 무해).
    """
    src = scatter_source(nat, hi)
    fields = [_blur_small(src, sg)[0] for sg in sigmas(radius, long_edge_px)]
    return fields, src.reshape(-1, 3).mean(axis=0)


def tint_scatter(lin, scat, color):
    """산란광의 **휘도는 유지하고 색만** 받는 픽셀 쪽으로 되돌린다. color=0 이면 무동작(물리).

    ⚠️물리가 아니라 **룩 노브**다. 산란광은 원래 다른 곳에서 온 빛이므로 받는 면의 색을 가질
    이유가 없다. 다만 근거가 아주 없지는 않다 — 우리 산란은 **이미지 공간 블러**라 실제 산란이
    넘지 않는 깊이·가림 경계를 넘어 색을 섞는다. 그 한계를 거칠게 보정하는 셈이다.

    왜 필요한가(실측, DSCF1662): 차가운 LED 스트립 + 따뜻한 천장 장면에서 산란광은 국소 이웃
    평균과 사실상 같고(sRGB R/G 1.143 vs 박스평균 1.183), 그 청색 빛이 따뜻한 면에 섞여
    **창백해진다**(중간톤 채도 −0.038 @ amt 0.7). 이 항이 1.0 이면 채도가 0.286→0.322(OFF
    0.324)로 돌아오고 밝기 증분은 그대로(+0.008) — 글로우는 두고 창백함만 뺀다.

    ⚠️`nl`(받는 픽셀 휘도)이 0 에 가까우면 색비가 노이즈뿐이라 그걸 증폭하게 된다. 그래서
    `MIST_COLOR_FLOOR` 아래에서는 물리(산란광 그대로)로 부드럽게 되돌린다.
    """
    color = float(color)
    if color <= 0.0:
        return scat
    nl = lin @ LUMA
    sl = scat @ LUMA
    g = _smoothstep(0.0, coeffs.MIST_COLOR_FLOOR, nl) * min(color, 1.0)
    safe = np.maximum(nl, 1e-6)[..., None]
    tinted = lin * (sl[..., None] / safe)          # 휘도 = sl, 색비 = lin
    return (scat + (tinted - scat) * g[..., None]).astype(np.float32)


def apply(lin, amt, char, radius, hi, long_edge_px, color=0.0):
    """[export] 카메라네이티브 scene-linear (H,W,3) 에 미스트를 적용. amt<=0 이면 무동작.

    long_edge_px: σ 기준이 되는 프레임 긴 변(px). export 는 풀해상도, 프리뷰는 프록시 —
    같은 프레임 비율을 쓰므로 양쪽 룩이 일치한다.
    color: 산란광 색 복원(0=물리). `tint_scatter` 참조 — 셰이더는 uniform 이라 실시간이다.
    """
    amt = float(amt)
    if amt <= 0.0:
        return lin
    k = amt * coeffs.MIST_K
    w = weights(char)
    src = scatter_source(lin, hi)

    scat = np.zeros_like(lin)
    for wi, sg in zip(w[:3], sigmas(radius, long_edge_px)):
        if wi > 0.0:
            scat += (wi * _blur(src, sg)).astype(np.float32)
    if w[3] > 0.0:                                  # 균일항(σ→∞) = 프레임 평균 = 베일링 글레어
        scat += (w[3] * src.reshape(-1, 3).mean(axis=0)).astype(np.float32)
    scat = tint_scatter(lin, scat, color)
    return ((1.0 - k) * lin + k * scat).astype(np.float32)
