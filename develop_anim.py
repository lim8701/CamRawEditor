# -*- coding: utf-8 -*-
"""Develop 애니메이션 — RAW 에서 최종까지의 단계 스케줄 (단일 진실원).

RAW Peek(`R`) 의 `Develop` 탭이 쓴다. 하는 일은 하나뿐이다: **정규화 시간 t(0..1) 에서 셰이더
uniform 이 어떤 값이어야 하는지** 계산한다. 렌더는 기존 `adjust.frag` 가 그대로 한다.

## 왜 이렇게 하는가

`adjust.frag` 의 거의 모든 현상 단계는 **이미 uniform 으로 노출**돼 있다. 그래서 "단계를 하나씩
켜는 애니메이션" = **uniform 을 중립값에서 실제값으로 천천히 옮기는 것**이고, 셰이더도
`pipeline.py` 도 건드릴 필요가 없다.

⚠️**슬라이더를 움직이면 안 된다.** 룩 파라미터는 `win.*` 이 아니라 슬라이더 객체에서 직접
`pipe` 로 들어가고, 슬라이더 값을 건드리면 `editSaveWatch` → 사이드카 저장 + undo push,
`onValueChanged` → 파이썬 호출(WB 는 **RAW 재디코드**)까지 줄줄이 발동한다. 그래서 QML 은
**별도 인스턴스(`pipeAnim`)의 자체 프로퍼티**에만 이 값을 써 넣는다.

## 이 단계들은 애니메이션하지 않는다 (uniform 이 없다)

| 단계 | 왜 |
|---|---|
| 디모자이크 | 다이얼이 아니다 → CFA 모자이크 그림과 **교차 페이드** 로 표현 |
| WB(화이트밸런스) | 디코드에 TREF 로 **베이크**돼 있다. 커밋된 상태의 셰이더 게인은 (1,1,1) 이라 되돌릴 손잡이가 없다 |
| 렌즈 보정 | 디코드에 이미 적용(프록시가 보정된 상태) |
| 톤 커브 | 값이 아니라 **텍스처**(`curve` 샘플러)라 uniform 으로 못 섞는다 |

→ 애니메이션은 "디모자이크된 카메라 원본(매트릭스 항등 · 자동노출 되돌림)" 에서 시작한다.
   캡션이 이 사실을 말해 주어야 한다 — 안 보여주는 것과 없는 것은 다르다.

## filmic 은 셰이더에 손잡이를 넣어 **보여준다**

처음엔 보류했다가("연결성이 떨어진다" 는 사용자 지적) `adjust.frag` 에 `filmicMix` uniform 을
추가했다. `pipe`/`pipeFull` 은 **1.0 리터럴 고정**이라 프리뷰·export 는 무영향이고
`pipeline.render_full` 도 손댈 것이 없다 — 움직이는 것은 `pipeAnim` 뿐이다.

⚠️**롤오프만 끄는 방식(OETF 유지)은 기각**했다. 실측 평균절대차 **0.0코드**, |Δ|>2코드 픽셀
**0.2%** 로 보이지 않는다. 선형까지 내려가야 보인다(평균 **61.5코드 = +0.94EV**, 95.4% 픽셀).

→ 그래서 **머리 구간(Gray/CFA)도 감마를 걸지 않고 선형으로 그린다**
(`raw_peek.develop_mosaic`). 밝은 모자이크에서 어두운 선형 렌더로 넘어가면 밝기가 튀어
연결이 끊긴다. 선형끼리 이어야 filmic 단계가 "선형 → 눈이 보는 밝기" 라는 제 몫을 한다.
머리 구간이 어두운 것은 버그가 아니라 **센서 데이터가 원래 그렇다는 사실**이다.
"""

# 벡터 uniform(길이 4). QML 은 이 리스트를 Qt.vector4d 로 만든다.
_VEC4 = frozenset((
    "hslHa", "hslHb", "hslSa", "hslSb", "hslLa", "hslLb",
    "skyA0", "skyB0", "skyC0", "skyA1", "skyB1", "skyC1",
    "skyA2", "skyB2", "skyC2", "skyA3", "skyB3", "skyC3",
    "skyA4", "skyB4", "skyC4",
))

# 3x3 매트릭스의 대각 성분(항등이 1.0)
_CAM_DIAG = frozenset(("camM0", "camM4", "camM8"))

# ★단계 순서는 `shaders/adjust.frag` main() 의 번호 주석과 같아야 한다.
#   (label = 타임라인/캡션 표시, note = "무엇을 하는가" 한 줄)
STAGES = [
    # 머리 세 단계는 uniform 이 없다 — 파이썬이 그린 그림 두 장(Gray/CFA)과 셰이더 렌더를
    # 겹쳐 놓고 **차례로 교차 페이드**한다(값이 아니라 그림이 바뀌는 구간).
    dict(key="readout", label="Sensor readout", uniforms=[],
         note="What the sensor recorded: one brightness per photosite. "
              "This is linear light, so it looks dark."),
    dict(key="cfa", label="Colour filter array", uniforms=[],
         note="Each photosite sits under a single R, G or B filter (X-Trans 6x6 / Bayer 2x2)."),
    dict(key="demosaic", label="Demosaic", uniforms=[],
         note="The two missing colours are interpolated from neighbours - "
              "camera-native RGB from here on."),
    dict(key="filmic", label="Tone mapping", uniforms=["filmicMix"],
         note="Linear light to display brightness: highlight roll-off + sRGB encoding. "
              "Always applied."),
    # ⚠️"노출보다 앞이라니 어색하다" 는 반응을 받았지만 **코드가 그렇다** — 셰이더와
    #   `pipeline.render_full` 둘 다 미스트를 `cam`(카메라네이티브)에 걸고 그 뒤에 WB·매트릭스·
    #   노출이 온다. 캡션이 그 이유를 말해 주어야 어색함이 정보가 된다.
    dict(key="mist", label="Mist", uniforms=["mistAmt"],
         note="Scatter is computed in camera-native space, before white balance and exposure, "
              "so the field never depends on the sliders."),
    dict(key="matrix", label="Colour matrix",
         note="The camera's own colour response, converted to sRGB.",
         uniforms=["camM0", "camM1", "camM2", "camM3", "camM4",
                   "camM5", "camM6", "camM7", "camM8"]),
    dict(key="autoexp", label="Auto exposure", uniforms=["autoExpEV"],
         note="A per-image gain that matches the embedded JPEG's median - "
              "the camera's own exposure."),

    # ★★auto exposure 이후는 **세 덩이**다(사용자 요청). 단계를 잘게 쪼개 놓으면 전체 프레임
    #   축소본에서 각자 티가 안 나 빈 구간처럼 느껴진다 — 필름 룩을 기준으로 '전 / 룩 / 후' 로
    #   묶는 쪽이 이야기가 된다.
    #   ⚠️uniform 이 여러 셰이더 위치에 걸치지만 **덩이끼리는 겹치지 않는다**
    #     (전 ≤137줄 < filmsim 149 < 후 ≤208 < grain 219) → 순서 검사가 그대로 성립한다.
    dict(key="predev", label="Tone & detail",
         uniforms=["exposure", "highlights", "shadows", "whites", "blacks",
                   "lumaNR", "colorNR", "texAmt", "clarity", "sharpenAmt", "dehaze"],
         note="Exposure, tone zones, noise reduction, texture, clarity, sharpening and dehaze - "
              "the corrections that shape the frame before the film look goes on."),
    dict(key="filmsim", label="Film simulation", uniforms=["lutStrength", "simExpEV"],
         note="The 3D LUT. simExpEV cancels the tone curve the LUT would apply a second time."),
    dict(key="finish", label="Colour & finishing",
         uniforms=["saturation", "vibrance",
                   "hslHa", "hslHb", "hslSa", "hslSb", "hslLa", "hslLb",
                   "contrast", "cgSatSh", "cgSatMid", "cgSatHi",
                   "skyA0", "skyB0", "skyC0", "skyA1", "skyB1", "skyC1",
                   "skyA2", "skyB2", "skyC2", "skyA3", "skyB3", "skyC3",
                   "skyA4", "skyB4", "skyC4",
                   "vignette"],
         note="Saturation, HSL, contrast, split toning, local masks and vignette - "
              "everything that lands on top of the film look."),
    dict(key="grain", label="Film grain", uniforms=["grainAmt"],
         note="Emulsion grain - the last thing the shader does."),
    # 날짜 스탬프는 셰이더가 아니라 QML 오버레이가 그린다(`ui/Main.qml` 의 `stampOverlay`).
    # 그래서 uniform 이 없고 **불투명도로 페이드인**한다. 프리뷰에서는 오버레이가 셰이더 위에
    # 얹히므로 **그레인 뒤**가 화면과 맞는 순서다(export 는 그레인이 스탬프에도 얹힌다 —
    # `docs/date_stamp.md` 의 '프리뷰와 export 의 합성식이 다르다').
    # ⚠️스탬프가 없는 사진에서는 단계 자체가 안 나온다(`_hasStamp`).
    dict(key="stamp", label="Date stamp", uniforms=[],
         note="The quartz date-back is composited over the finished render, as an overlay."),
]



# ★단계 순서의 기준 = `shaders/adjust.frag` main() 의 **실제 실행 순서**다(주석의 번호가 아니다 —
#   미스트는 `1)` 인데 `0)` 프론트엔드보다 앞에서 돌고, 하이라이트 디새추는 `0.5)` 인데 톤 앞이다).
#   `python develop_anim.py` 가 셰이더에서 각 단계 대표 uniform 의 첫 등장 줄을 찾아 이 목록의
#   순서와 대조한다 — 손으로 옮겨 적은 순서가 조용히 어긋나는 것을 막는다.
#   예외는 `filmic` 하나뿐이다(아래 _ORDER_EXEMPT).
# main() 밖 헬퍼에서 uniform 을 쓰는 단계는 **호출 지점**으로 순서를 잰다.
_ORDER_MARK = {
    "matrix": "applyCamMat(cam)",
    # `hsl` 단계는 `finish` 로 병합됐지만, 쪼갤 경우를 위해 남겨 둔다(hslH* 는 main() 밖
    # 헬퍼에서 소비돼 이름으로는 못 찾는다).
    "hsl": "hslMixer(rgb)",
}
_ORDER_EXEMPT = {
    # filmic 은 값 손잡이가 아니라 **표시 변환**이고 셰이더에서는 노출 뒤에 온다. 하지만 거기서
    # 보여주면 그 앞의 네 단계(미스트·매트릭스·자동노출·노출)가 전부 캄캄한 선형 화면이 된다.
    # 그래서 디모자이크 직후로 끌어올렸다 — 그 지점의 상태도 실재하는 셰이더 상태다
    # (filmicMix=1, camM 항등, autoExpEV=-autoEV).
    "filmic",
}


def neutral(name, snap):
    """uniform `name` 의 **시작값**. `snap` = 실제(최종) 값 dict.

    ⚠️`skyC*` 는 (텍스처, 클래리티, invert, hasMask) 다 — 앞 둘만 조정이고 뒤 둘은 설정/게이트다.
      뒤 둘을 0 으로 만들면 레이어가 꺼져 **팝** 이 생기므로 실제 값을 그대로 둔다.
    """
    if name in _CAM_DIAG:
        return 1.0
    if name.startswith("camM"):
        return 0.0
    if name == "contrast":
        return 1.0
    if name == "autoExpEV":
        # 자동노출은 디코드에 이미 곱해져 있다. `autoExpEV` 는 원래 '끄기' 오프셋이므로
        # −autoEV 에서 0 으로 옮기면 자동노출이 걸리는 과정이 그대로 보인다.
        return -float(snap.get("_autoEV", 0.0))
    if name.startswith("skyC"):
        # skyC = (텍스처, 클래리티, invert, hasMask) — 앞 둘만 조정, 뒤 둘은 설정/게이트다.
        v = snap.get(name) or [0.0, 0.0, 0.0, 0.0]
        return [0.0, 0.0, float(v[2]), float(v[3])]
    if name.startswith("skyB"):
        # skyB = (temp, tint, sat, **contrast**) — ★contrast 는 **곱셈자라 중립이 1.0** 이다
        #   (전역 `contrast` 와 동일 — `ui/Main.qml` 의 "skyContrast 는 곱셈자라 중립=1.0" 주석).
        #   0 으로 두면 마스크 안쪽 대비가 무너진다 — 검증에서 잡힌 실제 버그다.
        return [0.0, 0.0, 0.0, 1.0]
    if name in _VEC4:
        return [0.0, 0.0, 0.0, 0.0]
    return 0.0


def _same(a, b, eps=1e-6):
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        aa = list(a) if isinstance(a, (list, tuple)) else [a] * 4
        bb = list(b) if isinstance(b, (list, tuple)) else [b] * 4
        return all(abs(float(x) - float(y)) <= eps for x, y in zip(aa, bb))
    return abs(float(a) - float(b)) <= eps


def active_stages(snap):
    """이 사진에서 **실제로 무언가 바뀌는** 단계만. 나머지는 건너뛴다.

    편집하지 않은 파라미터의 단계를 다 보여주면 대부분이 '아무 일도 안 일어나는 구간'이 된다.
    """
    out = []
    for st in STAGES:
        if not st["uniforms"]:
            # 교차 페이드 구간은 항상 남긴다. 단 스탬프는 그 사진에 스탬프가 있을 때만.
            on = bool(snap.get("_hasStamp")) if st["key"] == "stamp" else True
            out.append(dict(st, active=on))
            continue
        moved = any(not _same(neutral(u, snap), snap.get(u, 0.0)) for u in st["uniforms"])
        out.append(dict(st, active=bool(moved)))
    return out


def schedule(snap):
    """[(단계, t0, t1)] — 활성 단계를 [0,1] 에 **균등 배분**한다. 표시용 목록도 겸한다.

    ★단계마다 길이가 같다. 예전엔 교차 페이드 구간에 가중치를 더 주고 앞뒤에 정지 구간까지
      뒀는데, 첫 단계(Sensor readout)가 유독 길게 느껴졌다(사용자 보고) — 앞 정지 구간의
      캡션이 **첫 단계 이름**이라 체감 길이가 두 배였다. 정지 구간도 없앴다: 재생은 t=1 에서
      멈춰 그대로 머문다(`RawPeekWindow.qml` 의 devTimer).
    """
    stages = [s for s in active_stages(snap) if s["active"]]
    n = max(1, len(stages))
    return [(s, i / n, (i + 1) / n) for i, s in enumerate(stages)]


def _smoothstep(x):
    x = 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)
    return x * x * (3.0 - 2.0 * x)


def _lerp(a, b, w):
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        aa = list(a) if isinstance(a, (list, tuple)) else [a] * 4
        bb = list(b) if isinstance(b, (list, tuple)) else [b] * 4
        return [float(x) + (float(y) - float(x)) * w for x, y in zip(aa, bb)]
    return float(a) + (float(b) - float(a)) * w


def values(t, snap):
    """시간 t(0..1) 에서의 uniform 값 dict + 표시 정보.

    반환: `{"uniforms": {...}, "stage": <key>, "label": ..., "note": ...,
             "gray": 0..1(Gray 그림 불투명도), "mosaic": 0..1(CFA 그림 불투명도),
             "stamp": 0..1(날짜 스탬프 불투명도)}`
    """
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    sch = schedule(snap)
    # ★**모든** 단계의 uniform 을 먼저 실제값으로 채운다. 스킵된 단계(중립==실제)의 값이 dict 에
    #   빠지면 `pipeAnim` 의 그 프로퍼티가 기본값(0)으로 남아 `contrast`(1 이어야) 나
    #   `skyC*`(invert/hasMask) 가 틀어진다 — 검증에서 걸린 실제 버그다.
    uni = {}
    for st in STAGES:
        for u in st["uniforms"]:
            uni[u] = snap.get(u, neutral(u, snap))
    cur = sch[0][0] if sch else None
    for st, t0, t1 in sch:
        w = 0.0 if t <= t0 else (1.0 if t >= t1 else _smoothstep((t - t0) / max(t1 - t0, 1e-9)))
        for u in st["uniforms"]:
            uni[u] = _lerp(neutral(u, snap), snap.get(u, 0.0), w)
        if t >= t0:
            cur = st

    # ---- 머리 구간 두 그림 레이어의 불투명도 ----
    # 쌓임(아래→위) = 셰이더 렌더 → CFA 그림 → Gray 그림. 위에 있는 것이 먼저 사라진다.
    #   readout  : gray 1 · cfa 1        → Gray 만 보인다
    #   cfa      : gray 1→0 · cfa 1      → CFA 색이 드러난다
    #   demosaic : gray 0 · cfa 1→0      → 셰이더의 디모자이크 결과가 드러난다
    span = {st["key"]: (t0, t1) for st, t0, t1 in sch}

    def _fade_out(key):
        """그 단계 이전엔 1.0, 단계 중 1→0, 이후 0.0."""
        if key not in span:
            return 0.0
        t0, t1 = span[key]
        if t <= t0:
            return 1.0
        if t >= t1:
            return 0.0
        return 1.0 - _smoothstep((t - t0) / max(t1 - t0, 1e-9))

    def _fade_in(key):
        """그 단계 이전 0.0, 단계 중 0→1, 이후 1.0."""
        if key not in span:
            return 0.0
        t0, t1 = span[key]
        if t <= t0:
            return 0.0
        if t >= t1:
            return 1.0
        return _smoothstep((t - t0) / max(t1 - t0, 1e-9))

    return dict(uniforms=uni, stage=(cur or {}).get("key", ""),
                label=(cur or {}).get("label", ""), note=(cur or {}).get("note", ""),
                gray=_fade_out("cfa"), mosaic=_fade_out("demosaic"),
                stamp=_fade_in("stamp"))


def marks(snap):
    """타임라인 눈금 — 활성/스킵 전부 (스킵은 흐리게 표시)."""
    sch = {s["key"]: (t0, t1) for s, t0, t1 in schedule(snap)}
    out = []
    for st in active_stages(snap):
        t0, t1 = sch.get(st["key"], (-1.0, -1.0))
        out.append(dict(key=st["key"], label=st["label"], note=st["note"],
                        active=st["active"], t0=t0, t1=t1))
    return out


def _order_report(root=None):
    """STAGES 순서가 `adjust.frag` main() 의 실행 순서와 같은지 대조. 불일치 개수를 반환."""
    import os
    root = root or os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(root, "shaders", "adjust.frag"), encoding="utf-8").read()
    body = src[src.index("void main"):]
    bad = 0
    seen = []
    for st in STAGES:
        if not st["uniforms"] or st["key"] in _ORDER_EXEMPT:
            continue
        mark = _ORDER_MARK.get(st["key"])
        if mark:
            # uniform 이 main() 밖 헬퍼에서 소비되는 단계는 **호출 지점**으로 잰다.
            pos = body.find(mark)
            pos = None if pos < 0 else pos
        else:
            # ⚠️**첫 등장 중 가장 늦은 것**을 쓴다. 일부 uniform 은 자기 단계보다 앞에서 한 번
            #   쓰인다(`simExpEV` 는 노출 지수 안, `skyA0.x` 는 마스크 노출 합산). 첫 등장만
            #   보면 filmsim 이 노출 위치로, masks 가 맨 앞으로 잡힌다(실제로 그렇게 나왔다).
            found = [body.find("ubuf." + u) for u in st["uniforms"]]
            found = [f for f in found if f >= 0]
            pos = max(found) if found else None
        if pos is None:
            print(f"[X] {st['key']}: 셰이더에서 {mark or st['uniforms']} 를 못 찾았다")
            bad += 1
            continue
        seen.append((st["key"], body[:pos].count(chr(10)) + 1))
    for (k0, l0), (k1, l1) in zip(seen, seen[1:]):
        if l1 < l0:
            print(f"[X] 순서 역전: {k0}(셰이더 {l0}줄) 뒤에 {k1}({l1}줄)")
            bad += 1
    print(f"[..] 셰이더 실행 순서 대조 {len(seen)}단계: "
          + " -> ".join(f"{k}@{ln}" for k, ln in seen))
    if _ORDER_EXEMPT:
        print(f"[..] 예외(의도적으로 앞으로 끌어올림): {sorted(_ORDER_EXEMPT)}")
    return bad


if __name__ == "__main__":
    import sys as _sys
    _bad = _order_report()
    print("[OK] 순서 일치" if not _bad else f"[X] 불일치 {_bad}건")
    _sys.exit(1 if _bad else 0)
