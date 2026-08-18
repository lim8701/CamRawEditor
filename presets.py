"""레시피 프리셋(.frpreset) — 편집 '룩'의 저장/불러오기 + 출처(카메라·렌즈·날짜) 기록.

왜 출처를 기록하나: 같은 레시피라도 다른 기종·다른 렌즈에서는 색과 효과가 다르게 나오는데,
"레시피만 있으면 그 사진이 나온다"고 생각하는 사람이 많다는 사용자 피드백에서 출발했다.
그래서 이 모듈은 **출처를 파일에 남기고**, UI 는 적용 시점에 그것을 보여준다(main/QML 담당).

파일은 사람이 읽는 JSON 이다 — 포럼·메신저에 그대로 붙여 공유하는 것이 이 기능의 목적이라,
바이너리나 압축 포맷을 쓰지 않는다.

⚠️`.frpreset` 은 **앱 외부에서 편집·공유되는 첫 신뢰할 수 없는 입력**이다. 손상/조작된 파일이
QML `applyEdits` 안에서 예외를 던지면 `_applying` 가드가 영구히 켜진 채 남아 그 세션의 자동저장과
undo 가 조용히 죽는다. 그래서 **적용 전에 여기서 전부 검증**하고, 의심스러우면 파일을 거부한다.
"""

import json
import math
import os
import re
import unicodedata

FILE_EXT = ".frpreset"
KIND = "filmrawstery-preset"
SCHEMA_V = 1

# 구분색 팔레트 — 어두운 패널 배경(#2b2b2b)에서 서로 구분되는 12색. 자유 색 선택을 두지 않는
# 이유는 참고 디자인(film_recipe.png)의 통일감이 제한된 팔레트에서 나오고, 공유받은 프리셋도
# 같은 팔레트라 배지 그리드가 지저분해지지 않기 때문이다.
PALETTE = (
    "#E0A226",  # amber
    "#D9722B",  # orange
    "#C4462F",  # red
    "#B0447A",  # magenta
    "#7E5AA8",  # violet
    "#4A6FB5",  # blue
    "#3E8FA8",  # teal
    "#3F8F5E",  # green
    "#8A9A3B",  # olive
    "#A8823C",  # bronze
    "#8A8A8A",  # grey
    "#D8D8D8",  # white
)
FALLBACK_COLOR = "#8A8A8A"      # 팔레트 밖 값/누락 시 — 중립 회색

# 프리셋에 실리는 출처 키(presetSource 와 1:1). 값은 전부 문자열이고 없으면 빈 문자열.
SOURCE_KEYS = ("camera", "lens", "focalLength", "aperture", "iso", "shotDate")

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
_STEM_MAX = 64                  # NTFS 255 가 아니라, 공유 후 깊은 폴더에서의 전체 경로 여유


# ---------- 파일명 ----------

def sanitize_stem(name: str) -> str:
    """표시명 → 파일명 stem. **항상 내부 name 에서 파생**하고 들어온 파일명은 신뢰하지 않는다
    (`name: "../../foo"` 가 경로 탈출 쓰기가 된다).

    ⚠️끝의 점·공백을 반드시 떼야 한다 — Windows 가 그것을 조용히 버려서 `"warm."` 이 디스크에서
      `"warm"` 이 되고, 존재 확인 로직이 OS 와 어긋난다.
    ⚠️예약 장치명(CON/NUL/COM1…)은 NTFS 에서 그 이름으로 파일을 만들 수 없다 → 앞에 `_`.
    """
    s = unicodedata.normalize("NFC", str(name or ""))
    s = _BAD_CHARS.sub("_", s)
    s = re.sub(r"_{2,}", "_", s)
    s = s.strip().strip(".").strip()
    if len(s) > _STEM_MAX:
        s = s[:_STEM_MAX].rstrip().rstrip(".").rstrip()
    if not s:
        return "preset"
    if s.upper() in _RESERVED or s.upper().split(".")[0] in _RESERVED:
        s = "_" + s
    return s


def _unique_path(folder: str, stem: str, name: str) -> str:
    """`<folder>/<stem>.frpreset`. 이미 있고 **내부 name 이 같으면 덮어쓰기**(의도된 갱신),
    다르면 `-2`,`-3`… 를 붙인다.

    ⚠️같은 이름으로 조용히 덮어쓰면 안 된다 — `"Warm/Soft"` 와 `"Warm\\Soft"` 는 둘 다
      `Warm_Soft` 로 새니타이즈되므로 서로를 지우게 된다."""
    first = os.path.join(folder, stem + FILE_EXT)
    if not os.path.exists(first):
        return first
    try:
        with open(first, encoding="utf-8") as f:
            if str(json.load(f).get("name", "")) == name:
                return first          # 같은 프리셋을 갱신
    except Exception:
        pass                          # 읽을 수 없는 파일은 건드리지 않고 옆에 새로 만든다
    for i in range(2, 100):
        cand = os.path.join(folder, f"{stem}-{i}{FILE_EXT}")
        if not os.path.exists(cand):
            return cand
    raise OSError("too many presets with the same name")


def share_filename(name: str, source: dict, created: str) -> str:
    """공유(Export)용 제안 파일명 — **여기서만** 출처를 파일명에 넣는다.

    앱 내부 보관 파일명은 짧게(`<name>.frpreset`) 두고 출처는 배지/툴팁으로 보여준다. 패널이
    300px 라 파일명에 출처를 넣으면 `Portra warm_FUJIFILM X10…` 로 잘려 **출처를 가장 잘 보여줄
    자리에서 출처가 파괴**된다. 반대로 공유 파일은 아무도 JSON 을 열지 않으므로 파일명이 유일한
    단서다 — 그래서 둘을 다르게 둔다."""
    src = source or {}
    cam = _short_camera(str(src.get("camera") or ""))
    lens = str(src.get("lens") or "") or str(src.get("focalLength") or "")
    parts = [p for p in (name, cam, lens, created) if p]
    return sanitize_stem(" - ".join(parts)) + FILE_EXT


def _short_camera(camera: str) -> str:
    """파일명·배지용 짧은 바디명. `FUJIFILM X100V` → `X100V`.
    exif_info._camera_name 이 이미 제조사 중복을 없앴으므로, 여기서는 잘 알려진 제조사 접두만
    떼어 폭을 아낀다(못 알아보는 값은 그대로 둔다 — 잘못 자르는 것보다 낫다)."""
    c = (camera or "").strip()
    for maker in ("FUJIFILM", "NIKON", "CANON", "SONY", "OLYMPUS", "PANASONIC",
                  "PENTAX", "SAMSUNG", "LEICA", "RICOH", "SIGMA"):
        if c.upper().startswith(maker + " "):
            return c[len(maker) + 1:].strip()
    return c


# ---------- 검증 ----------

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _valid_hsl(v) -> bool:
    """HSL 밴드는 **정확히 8개 유한 수**. 아니면 QML 에서 `.slice` TypeError 가 나거나
    NaN 이 셰이더로 흘러간다."""
    return isinstance(v, list) and len(v) == 8 and all(_finite(x) for x in v)


def _valid_curves(v) -> bool:
    """커브는 4채널(master,R,G,B) 제어점. `movePoint`/`addPoint` 가 유지하는 불변식과 같은 것을
    요구한다 — CurveEditor.setChannelPoints 는 검증이 전혀 없어 형태가 틀리면 onPaint/allLuts
    에서 던진다."""
    if not isinstance(v, list) or len(v) != 4:
        return False
    for ch in v:
        if not isinstance(ch, list) or len(ch) < 2:
            return False
        xs = []
        for pt in ch:
            if not isinstance(pt, dict) or not _finite(pt.get("x")) or not _finite(pt.get("y")):
                return False
            if not (0.0 <= float(pt["x"]) <= 1.0 and 0.0 <= float(pt["y"]) <= 1.0):
                return False
            xs.append(float(pt["x"]))
        if xs[0] != 0.0 or xs[-1] != 1.0 or any(b <= a for a, b in zip(xs, xs[1:])):
            return False
    return True


def validate_edits(edits, allowed) -> tuple:
    """(정제된 edits, 오류 문구). 오류가 있으면 **프리셋 전체를 거부**한다 — 문제 있는 키만
    버리면 병합이 대상 사진의 값을 남겨 '레시피가 레시피가 아닌' 상태가 된다.

    allowed: main._PRESET_KEYS (허용 키 단일 진실원). 그 밖의 키는 조용히 버린다 —
    손편집 프리셋이 `cropX`/`maskLayers`/`temp` 같은 사진별 값을 밀어넣는 것을 막는다."""
    if not isinstance(edits, dict):
        return None, "edits is not an object"
    out = {}
    for k, v in edits.items():
        if k not in allowed:
            continue                      # 허용 목록 밖 — 조용히 무시
        if k in ("hslH", "hslS", "hslL"):
            if not _valid_hsl(v):
                return None, f"{k} must be 8 finite numbers"
        elif k == "curves":
            if not _valid_curves(v):
                return None, "curves are malformed"
        elif isinstance(v, bool) or isinstance(v, str):
            pass                          # 체크박스/문자열(simKey, stampStyle…)
        elif not _finite(v):
            # ⚠️json.load 는 기본적으로 NaN/Infinity 를 받아들인다. float() 로는 안 잡히므로
            #   isfinite 로 걸러야 한다 — 그대로 두면 슬라이더·export 파이프라인까지 흘러간다.
            return None, f"{k} is not a finite number"
        out[k] = v
    return out, ""


# ---------- 읽기/쓰기 ----------

def build(name: str, color: str, source: dict, edits: dict, app_version: str,
          created: str, description: str = "") -> dict:
    """저장할 프리셋 dict. `color`/`description` 은 **메타데이터이지 룩 값이 아니다** — 루트에
    두고 edits 에는 절대 넣지 않는다(넣으면 슬라이더로 흘러간다)."""
    return {
        "kind": KIND,
        "v": SCHEMA_V,
        "name": str(name),
        "description": str(description or "")[:280],   # 배지 툴팁/공유용 한두 줄
        "color": color if color in PALETTE else FALLBACK_COLOR,
        "createdAt": created,
        "appVersion": str(app_version),
        "source": {k: str((source or {}).get(k) or "") for k in SOURCE_KEYS},
        "edits": dict(edits),
    }


def read(path: str, allowed) -> tuple:
    """(프리셋 dict, 오류 문구). 실패는 예외가 아니라 문구로 돌려준다 — 목록을 그리는 쪽이
    파일 하나 때문에 깨지면 안 된다(`_read_edits` 와 같은 태도)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as exc:
        return None, f"cannot read: {type(exc).__name__}"
    if not isinstance(d, dict):
        return None, "not an object"
    if str(d.get("kind", "")) != KIND:
        return None, "not a Film Rawstery preset"
    edits, err = validate_edits(d.get("edits"), allowed)
    if err:
        return None, err
    src = d.get("source")
    return {
        "name": str(d.get("name") or os.path.splitext(os.path.basename(path))[0]),
        "description": str(d.get("description") or "")[:280],
        "color": d["color"] if d.get("color") in PALETTE else FALLBACK_COLOR,
        "createdAt": str(d.get("createdAt") or ""),
        "appVersion": str(d.get("appVersion") or ""),
        "source": {k: str((src or {}).get(k) or "") for k in SOURCE_KEYS}
        if isinstance(src, dict) else {k: "" for k in SOURCE_KEYS},
        "edits": edits,
        "file": path,
    }, ""


def write(folder: str, preset: dict) -> str:
    """프리셋을 폴더에 쓰고 경로 반환. 쓰기는 호출부의 원자적 쓰기 함수에 맡기지 않고
    여기서 tmp→replace 를 직접 한다(모듈이 main 에 의존하지 않게)."""
    os.makedirs(folder, exist_ok=True)
    stem = sanitize_stem(preset.get("name"))
    path = _unique_path(folder, stem, str(preset.get("name") or ""))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(preset, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def listdir(folder: str, allowed) -> list:
    """폴더의 프리셋 목록(이름순). ⚠️읽을 수 없는 파일은 **건너뛴다** — 하나가 깨져도 패널의
    나머지가 보여야 한다."""
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except Exception:
        return []
    out = []
    for n in names:
        if not n.lower().endswith(FILE_EXT):
            continue
        p, err = read(os.path.join(folder, n), allowed)
        if p is not None:
            out.append(p)
        else:
            print(f"[preset] 건너뜀 {n}: {err}")
    out.sort(key=lambda d: d["name"].lower())
    return out
