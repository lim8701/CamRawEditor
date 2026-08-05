"""브러시 획 래스터라이저 — 레이어 마스크 위 수동 추가/빼기(마스킹 디테일 수정).

획(stroke)은 픽셀이 아니라 **벡터**로 저장·리플레이된다(사이드카 경량·해상도 독립·
자동 마스크 재추론과 공존): {sign(+1 추가/-1 빼기), radius(코어 반경, 프록시 짧은 변
대비 비율), feather(0..1, 코어 **바깥** falloff 폭 = radius×feather — 라이트룸 모델),
points([x0,y0,x1,y1,...] 프록시 정규화 0..1)}.

적용 = 자동 마스크(장면∪얼굴∪깊이) 위에 순서대로: 추가 획은 union(max),
빼기 획은 곱(1-α) — 가감을 덧셈으로 하면 겹침에서 오버슈트 밴드가 생긴다.

래스터: 획 경로(폴리라인+점)를 1px 로 그리고 distanceTransform 으로 경로까지의
거리 → 반경 안쪽 smoothstep falloff. 획 바운딩 박스 ROI 에서만 계산(전체 프레임
distanceTransform 은 획당 ~15ms 로 리플레이 누적 시 아까움).

PySide6/QML 비의존 — numpy in/out 독립 모듈(export 파이프라인에서도 동일 재사용 가능
하지만, 마스크 배열 자체가 이미 획 포함이라 pipeline.py 는 이 모듈을 몰라도 된다).
"""

import numpy as np

# 반경 하한(px) — 0 반경 획이 distanceTransform 에서 사라지는 것 방지
MIN_RADIUS_PX = 1.5
# 페더 외곽 배율: 외곽 = 코어×(1 + FEATHER_SCALE×feather). 사용자 확정 2.0
# (1.0 = 너무 약함, 3.0 = 과함 — 비교 후 결정). feather 1.0 에서 코어의 3배 반경까지 번짐.
# ⚠️Main.qml 브러시 커서의 리터럴 2.0 과 반드시 일치(점선 외곽 원 = 실제 영향 범위).
FEATHER_SCALE = 2.0


def _stroke_geom(stroke, hw):
    """획의 픽셀 좌표/코어·외곽 반경과 ROI(y0,y1,x0,x1). 점 없음/퇴화 ROI 면 None.

    **라이트룸 모델**: radius = 코어(불투명 1.0) 반경, feather = 코어 **바깥**으로 번지는
    falloff 폭(코어 반경 대비 비율, 외곽 = radius×(1+feather)). 페더를 올리면 영향 범위가
    커진다 — 반경을 고정하고 안쪽을 깎는 방식은 휠 조작 방향이 Size 와 반대로 느껴져 기각.

    ROI 는 획이 변경할 수 있는 전체 영역(bbox+외곽 반경 여유) — 래스터와 패치 스냅샷 undo
    (main.addStroke)가 **같은 식**을 봐야 복원 누락이 없다(단일 진실원)."""
    h, w = int(hw[0]), int(hw[1])
    pts = np.asarray(stroke.get("points") or [], dtype=np.float32).reshape(-1, 2)
    if len(pts) == 0 or h < 1 or w < 1:
        return None
    r_core = max(MIN_RADIUS_PX, float(stroke.get("radius", 0.05)) * min(h, w))
    f = min(1.0, max(0.0, float(stroke.get("feather", 0.5))))
    r_out = r_core * (1.0 + FEATHER_SCALE * f)
    px = np.stack([np.clip(pts[:, 0], -0.5, 1.5) * w,
                   np.clip(pts[:, 1], -0.5, 1.5) * h], axis=1)
    # ROI(획 bbox + 외곽 반경 여유) — 캔버스 밖으로 나간 점은 ROI 클램프로 잘려도
    # 경로가 프레임 안을 지나는 구간은 보존된다.
    m = int(np.ceil(r_out)) + 2
    x0 = max(0, int(np.floor(px[:, 0].min())) - m)
    y0 = max(0, int(np.floor(px[:, 1].min())) - m)
    x1 = min(w, int(np.ceil(px[:, 0].max())) + m)
    y1 = min(h, int(np.ceil(px[:, 1].max())) + m)
    if x1 <= x0 or y1 <= y0:
        return None
    return px, r_core, r_out, (y0, y1, x0, x1)


def stroke_bbox(stroke, hw):
    """획이 변경할 수 있는 영역 (y0,y1,x0,x1) — 패치 스냅샷 undo 용. 무효 획이면 None."""
    g = _stroke_geom(stroke, hw)
    return g[3] if g is not None else None


def _rasterize(stroke, hw):
    """획 1개 → soft alpha(H,W float32 [0,1]). 점 없으면 None."""
    import cv2
    h, w = int(hw[0]), int(hw[1])
    g = _stroke_geom(stroke, hw)
    if g is None:
        return None
    px, r_core, r_out, (y0, y1, x0, x1) = g
    rw, rh = x1 - x0, y1 - y0

    canvas = np.full((rh, rw), 255, dtype=np.uint8)
    ip = np.round(px - [x0, y0]).astype(np.int32)
    if len(ip) >= 2:
        cv2.polylines(canvas, [ip.reshape(-1, 1, 2)], False, 0, 1, cv2.LINE_8)
    for p in (ip[:1] if len(ip) < 2 else ip[[0, -1]]):   # 단일점/양끝 보강
        cv2.circle(canvas, tuple(p), 0, 0, -1)

    dist = cv2.distanceTransform(canvas, cv2.DIST_L2, 5)
    # 코어(dist<=r_core)=1.0, 코어~외곽(r_out)은 smoothstep 으로 0 까지 falloff
    t = np.clip((r_out - dist) / max(r_out - r_core, 1e-3), 0.0, 1.0)
    alpha_roi = (t * t * (3.0 - 2.0 * t)).astype(np.float32)

    alpha = np.zeros((h, w), dtype=np.float32)
    alpha[y0:y1, x0:x1] = alpha_roi
    return alpha


def apply_strokes(mask, hw, strokes):
    """자동 마스크(또는 None=빈 레이어) 위에 획 목록을 순서대로 적용한 마스크 반환.

    반환은 항상 새 배열(입력/캐시 비오염). 획이 전부 무효면 입력 그대로(복사) 반환.
    """
    h, w = int(hw[0]), int(hw[1])
    out = (np.zeros((h, w), dtype=np.float32) if mask is None
           else np.clip(mask.astype(np.float32, copy=True), 0.0, 1.0))
    for s in strokes or []:
        alpha = _rasterize(s, hw)
        if alpha is None:
            continue
        if float(s.get("sign", 1)) >= 0:
            np.maximum(out, alpha, out=out)      # 추가 = union
        else:
            out *= (1.0 - alpha)                 # 빼기 = soft erase
    return out
