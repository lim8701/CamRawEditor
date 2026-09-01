# -*- coding: utf-8 -*-
"""단축키·마우스 조작 목록의 **단일 진실원**.

앱 안 `?`/`F1` 오버레이(`ui/ShortcutHelp.qml`)가 이 표를 그대로 렌더한다. 문서에 같은 목록을
또 두지 않는다 — 두 곳에 두면 반드시 갈라진다.

★**`python shortcuts.py`** 를 돌리면 이 표와 **QML 의 실제 선언**을 대조한다
(`presets.py` 의 드리프트 검사와 같은 역할). 단축키를 추가/변경하면 그때 걸린다.
`KEYS` 항목의 `tokens` 는 QML 에 적힌 시퀀스 문자열을 **그대로** 담는다 — 그래야 대조가
문자 단위로 정확하다(예: `StandardKey.Undo`, `\\\\`).

⚠️`MOUSE` 는 파싱 대상이 아니라 **수동 목록**이다(마우스 조작은 선언 형태가 아니다).
그래서 검사기가 못 잡는다 — 상호작용을 바꾸면 여기도 직접 손봐야 한다.
"""

import os
import re
import sys

# ---------- 키보드 (검사 대상) ----------
# (그룹, [(표시, QML 시퀀스 토큰들, 설명), ...])
KEYS = [
    ("View", [
        ("I",  ("I",),   "Shooting info overlay"),
        ("C",  ("C",),   "Caption overlay"),
        ("Z",  ("Z",),   "Zone system overlay"),
        ("J",  ("J",),   "Clipping warning"),
        ("\\", ("\\\\",), "Compare with the original"),
        ("B",  ("B",),   "Show or hide the file explorer"),
        ("G",  ("G",),   "Contact sheet (the folder as a grid)"),
        ("R",  ("R",),   "RAW Peek - the sensor data before demosaic, and the develop animation (RAW only)"),
    ]),
    ("Panels", [
        ("Ctrl+1", ("Ctrl+1",), "Edit"),
        ("Ctrl+2", ("Ctrl+2",), "Crop / Rotate / Geometry"),
        ("Ctrl+3", ("Ctrl+3",), "Masking"),
        ("Ctrl+4", ("Ctrl+4",), "Date Stamp"),
        ("Ctrl+5", ("Ctrl+5",), "Wallpaper (when enabled)"),
        ("Ctrl+6", ("Ctrl+6",), "Location (geotag)"),
    ]),
    ("Masking brush", [
        ("A",      ("A",),      "Paint into the mask"),
        ("S",      ("S",),      "Paint out of the mask"),
        ("O",      ("O",),      "Show the mask overlay"),
        ("Esc",    ("Escape",), "Leave brush mode"),
    ]),
    ("Explorer", [
        ("Alt+↑",  ("Alt+Up",),           "Go to the parent folder"),
        ("Enter",  ("Return", "Enter"),   "Open the selected photo, or enter the folder"),
        ("L",      ("L",),                "Show liked photos only"),
        ("P",      ("P",),                "Expand or collapse paired JPEGs"),
        ("H",      ("H",),                "Photo tags for this folder"),
    ]),
    ("Editing", [
        ("Ctrl+Z",       ("StandardKey.Undo",),                  "Undo"),
        ("Ctrl+Y",       ("StandardKey.Redo", "Ctrl+Shift+Z"),   "Redo"),
        ("D",            ("D",),                                 "Date stamp on or off (also kept as your default)"),
        ("Ctrl+Shift+M", ("Ctrl+Shift+M",),                      "Display colour management"),
    ]),
    ("Preview window", [
        ("← →",  ("Keys.onLeftPressed", "Keys.onRightPressed"), "Previous or next photo"),
        ("Space", ("Keys.onSpacePressed",),                      "Like or unlike"),
        ("Esc",   ("Keys.onEscapePressed",),                     "Close the preview"),
    ]),
    ("RAW Peek", [
        ("1 ~ 5", ("Keys.onDigit1Pressed", "Keys.onDigit2Pressed", "Keys.onDigit3Pressed",
                   "Keys.onDigit4Pressed", "Keys.onDigit5Pressed"),
         "Gray / CFA / Planes / Demosaic / Develop"),
        ("+ -",   (), "Zoom in or out (1x to 32x)"),
        ("Space", ("Keys.onSpacePressed",), "On the Develop tab, play or pause"),
        ("← →",   ("Keys.onLeftPressed", "Keys.onRightPressed"),
         "On the Develop tab, step to the previous or next stage"),
        ("Esc",   ("Keys.onEscapePressed",), "Close RAW Peek"),
    ]),
    ("Help", [
        ("? / F1", ("?", "F1"), "This list"),
    ]),
]

# ---------- 마우스·제스처 (수동 목록 — 검사 대상 아님) ----------
MOUSE = [
    ("Photo", [
        ("Double-click", "Zoom to 1:1 and back, centred where you clicked"),
        ("Drag",         "Pan while zoomed in"),
    ]),
    ("RAW Peek", [
        ("Wheel", "Zoom the sensor mosaic in or out"),
        ("Drag",  "Pan while zoomed in"),
        ("Click", "On the minimap, jump to that part of the frame"),
    ]),
    ("Sliders", [
        ("Double-click", "Reset that slider to its default"),
    ]),
    ("Explorer · contact sheet", [
        ("Double-click", "Open a photo"),
        ("Click",        "In the contact sheet, select without opening"),
    ]),
    ("Tone curve", [
        ("Click",              "Add a point"),
        ("Drag",               "Move a point"),
        ("Double-click point", "Remove it"),
    ]),
    ("Recipes", [
        ("Drag a row", "Reorder your recipes"),
        ("Right-click", "Update the look, edit properties, export or delete"),
    ]),
]


# ---------- QML → 실제 선언 파싱 ----------

def _root():
    return os.path.dirname(os.path.abspath(__file__))


def declared_tokens():
    """QML 에 **실제로 선언된** 시퀀스 토큰 집합.

    - `ui/Main.qml` 의 `Shortcut { sequence(s): … }`
    - `ui/PreviewWindow.qml` 의 `Keys.on<Key>Pressed:` 핸들러(프리뷰 창은 별도 키 처리)
    """
    out = set()
    main = os.path.join(_root(), "ui", "Main.qml")
    with open(main, encoding="utf-8") as f:
        src = f.read()
    for m in re.finditer(r"Shortcut \{", src):
        seg = src[m.start():m.start() + 500]
        q = re.search(r"sequences?:\s*(\[[^\]]*\]|\"(?:[^\"\\]|\\.)*\")", seg)
        if not q:
            continue
        body = q.group(1)
        # "…" 문자열들 + StandardKey.X 식별자들
        out.update(re.findall(r'"((?:[^"\\]|\\.)*)"', body))
        out.update(re.findall(r"StandardKey\.\w+", body))
    # 전체화면 오버레이들은 Shortcut{} 이 아니라 Keys.on*Pressed 로 키를 받는다.
    # ⚠️새 오버레이 .qml 을 만들면 여기에 추가할 것 — 안 넣으면 그 파일의 키는 검사에서 빠진다.
    for fn in ("PreviewWindow.qml", "RawPeekWindow.qml"):
        with open(os.path.join(_root(), "ui", fn), encoding="utf-8") as f:
            out.update(re.findall(r"Keys\.on\w+Pressed", f.read()))
    return out


# ★`Shortcut{}` 은 앱 전역이라 전체화면 오버레이의 `Keys` 핸들러보다 **먼저** 잡는다. 그래서
#   모든 선언이 `win._keysBlocked` 를 봐야 한다. 개별 조건만 적어 뒀다가 RAW Peek 이 떠 있는데
#   `Enter` 로 프리뷰가 열리는 사고가 났다(그리고 `Alt+Up` 도 같이 새고 있었다).
#   예외는 **토글**인 둘뿐 — `R`(RAW Peek)과 `?`/F1(단축키 도움말)은 자기 오버레이가 떠 있을
#   때도 살아 있어야 닫힌다(`_keysBlocked` 가 그 둘의 visible 을 포함한다).
GUARD_EXEMPT = {'"R"', '["?", "F1"]'}


def guard_report(root=None):
    """모든 `Shortcut{}` 이 `_keysBlocked` 가드를 쓰는지 확인. 누락 개수를 반환."""
    root = root or _root()
    src = open(os.path.join(root, "ui", "Main.qml"), encoding="utf-8").read()
    miss, total, pos = [], 0, 0
    while True:
        k = src.find("Shortcut {", pos)
        if k < 0:
            break
        i = src.index("{", k)
        d, j = 0, i
        while j < len(src):
            if src[j] == "{":
                d += 1
            elif src[j] == "}":
                d -= 1
                if d == 0:
                    break
            j += 1
        blk = src[k:j + 1]
        pos = j + 1
        total += 1
        # ⚠️주석을 먼저 걷어낸다 — 주석에 든 '_keysBlocked' 를 세면 검사가 헛돈다(실제로 그랬다).
        code = chr(10).join(ln.split("//")[0] for ln in blk.splitlines())
        seq = next((ln.strip() for ln in blk.splitlines() if "sequence" in ln), "?")
        tok = seq.split(":", 1)[1].strip() if ":" in seq else seq
        if "_keysBlocked" not in code and tok not in GUARD_EXEMPT:
            miss.append((src[:k].count(chr(10)) + 1, seq))
    for ln, seq in miss:
        print(f"[X] Main.qml:{ln} 의 {seq} 가 _keysBlocked 가드를 쓰지 않는다")
    print(f"[..] Shortcut {total}개 가드 확인 (예외 {sorted(GUARD_EXEMPT)})")
    return len(miss)


def documented_tokens():
    return {t for _, rows in KEYS for _, toks, _ in rows for t in toks}


def main() -> int:
    dec, doc = declared_tokens(), documented_tokens()
    bad = 0
    missing = sorted(dec - doc)
    extra = sorted(doc - dec)
    if missing:
        bad += len(missing)
        print(f"[X] QML 에 있는데 목록에 없는 단축키 {len(missing)}개: {missing}")
        print("    → shortcuts.KEYS 에 추가할 것(오버레이에 안 뜬다)")
    if extra:
        bad += len(extra)
        print(f"[X] 목록에만 있는 단축키 {len(extra)}개: {extra}")
        print("    → QML 에서 없어졌거나 토큰 표기가 다르다(오버레이가 거짓을 말한다)")
    if not bad:
        print(f"[OK] 단축키 {len(dec)}개 일치 (그룹 {len(KEYS)}개 / 마우스 항목 "
              f"{sum(len(r) for _, r in MOUSE)}개)")
    bad += guard_report()
    # 표시 문자열이 비어 있지 않은지도 본다(오버레이가 빈 칸을 그린다)
    for g, rows in KEYS + MOUSE:
        for row in rows:
            if not row[0] or not row[-1]:
                print(f"[X] '{g}' 에 빈 표시/설명: {row}")
                bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
