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


# ---------- 룩 기본값(공장값) 단일 진실원 ----------
# `main.Controller._PRESET_KEYS` 의 모든 키에 대한 **공장 기본값**. QML `applyEdits` 가 저장된
# 값이 없을 때 쓰는 폴백이 바로 이 값이고(`controller.lookDefaults`), `look_hash` 도 옛 레시피에
# 없는 키를 이 값으로 채운다.
#
# ★왜 표가 필요한가: 룩 키가 늘어날 때마다 **그 키를 모르는 옛 레시피**가 생긴다. 예전에는
#   배지 비교를 '그 레시피가 지정한 키'로 좁혀서 넘겼는데, 그러면 나중에 추가된 키를 만져도
#   배지가 안 꺼져 거짓을 말한다(미스트를 추가하며 실제로 드러났다). 빠진 키를 기본값으로
#   **채워서 전체 키로** 비교하면, 그게 곧 '그 레시피를 적용했을 때 나오는 상태'와 같아진다
#   (`applyPresetEdits` 가 프리셋 소유 키를 지우고 폴백에 맡기므로).
#
# ⚠️키를 추가하면 여기에도 넣어야 한다. `python presets.py` 가 `_PRESET_KEYS`·QML 슬라이더
#   기본값과 대조해 누락/불일치를 보고한다.
# ⚠️한 키에 기본값은 **하나**여야 한다. 미스트 Color 를 '공장 0.5 / 사이드카 폴백 0.0' 으로
#   두었다가 이 구조가 성립하지 않아 0.5 로 통일했다.
_IDENTITY_CURVE = [[{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}] for _ in range(4)]

LOOK_DEFAULTS = {
    "contrast": 1.0, "highlights": 0.0, "shadows": 0.0, "whites": 0.0, "blacks": 0.0,
    # ⚠️applyEdits 의 리터럴은 `""` 지만 그건 '미지정 → simIndex 로 폴백' 신호이지 룩 값이
    #   아니다. editParams() 가 실제로 내보내는 값은 "identity" 다.
    "simKey": "identity", "simStrength": 1.0,
    "texture": 0.0, "clarity": 0.0, "dehaze": 0.0, "vibrance": 0.0, "saturation": 0.0,
    "hslH": [0.0] * 8, "hslS": [0.0] * 8, "hslL": [0.0] * 8,
    "cgShadowHue": 0.0, "cgShadowSat": 0.0, "cgMidHue": 0.0, "cgMidSat": 0.0,
    "cgHighHue": 0.0, "cgHighSat": 0.0, "cgBalance": 0.0,
    "vignette": 0.0,
    "mistAmt": 0.0, "mistChar": 0.0, "mistRadius": 1.0, "mistHi": 0.8, "mistColor": 0.5,
    "grainAmt": 0.0, "grainSize": 0.5, "grainRough": 0.1, "grainColor": 0.3,
    "grainShape": False,
    "sharpenAmt": 0.0, "sharpenRadius": 1.0, "sharpenDetail": 0.25, "sharpenMask": 0.0,
    # ⚠️applyEdits 리터럴은 `null`(=resetAll() 하라)이고, 저장되는 값은 그 결과인 제어점이다.
    "curves": _IDENTITY_CURVE,
    "stampStyle": "7c_bold", "stampSize": 0.032, "stampMargin": 0.05,
    "stampColor": "#ff8a29", "stampGlow": 1.0, "stampSpread": 1.0,
}


# ---------- 룩 지문(사이드카 <-> 배지 상태 판정) ----------

def look_hash(edits, allowed) -> str:
    """룩의 지문. 같은 룩이면 같은 문자열, 다르면 다른 문자열.

    사이드카에 이 값을 함께 저장해 두면, 사진을 다시 열 때 **레시피 파일을 읽지 않고도**
    "지금 이 사진의 룩이 그 레시피와 아직 같은가"를 판정할 수 있다. 배지를 '참조'가 아니라
    **'값'** 으로 켜는 것이 요점이다 — 슬라이더를 만졌는데도 활성으로 남으면 배지가 거짓을
    말하게 되고, 출처 배너를 정직하게 만든 이 기능의 전제가 무너진다.

    ⚠️`allowed`(=_PRESET_KEYS) 로 한정한다 — 사진별 값(크롭·WB·마스크)은 룩이 아니므로
      그것 때문에 지문이 달라지면 안 된다.
    ⚠️부동소수는 6자리로 반올림한다. 슬라이더 왕복은 보통 정확히 일치하지만, 커브 보간이나
      배열 마샬링에서 마지막 비트가 흔들리면 '같은 룩'이 다른 지문을 갖게 된다.

    ★**없는 키는 `LOOK_DEFAULTS` 로 채운다.** 그래서 지문은 항상 `allowed` 전체를 덮는다 —
      룩 키가 나중에 늘어나도 그 키를 모르는 옛 레시피가 '그 키는 기본값' 으로 해석되고, 그게
      곧 그 레시피를 적용했을 때 나오는 상태다(LOOK_DEFAULTS 주석 참조). 예전에는 비교 집합을
      레시피가 가진 키로 좁혔는데, 그러면 나중에 추가된 키를 만져도 배지가 안 꺼졌다."""
    import hashlib

    def norm(v):
        if isinstance(v, bool) or isinstance(v, str):
            return v
        if isinstance(v, (int, float)):
            return round(float(v), 6)
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, dict):
            return {k: norm(v[k]) for k in sorted(v)}
        return v

    if not isinstance(edits, dict):
        return ""
    keep = {k: norm(edits[k] if k in edits else LOOK_DEFAULTS.get(k))
            for k in sorted(allowed)}
    blob = json.dumps(keep, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# ---------- 읽기/쓰기 ----------

def build(name: str, color: str, source: dict, edits: dict, app_version: str,
          created: str, description: str = "", pid: str = "") -> dict:
    """저장할 프리셋 dict. `color`/`description` 은 **메타데이터이지 룩 값이 아니다** — 루트에
    두고 edits 에는 절대 넣지 않는다(넣으면 슬라이더로 흘러간다).

    `id`: 이름·파일명과 무관한 **안정 식별자**. 사이드카가 "이 사진은 어느 레시피에서 왔나"를
    기록할 때 이것을 쓴다 — 경로는 이름을 바꾸면 깨지고(파일명이 이름에서 파생됨) 이름 자체도
    바뀌므로, 둘 중 어느 것으로도 계보를 안정적으로 가리킬 수 없다. 없으면 새로 만든다."""
    import uuid
    return {
        "kind": KIND,
        "v": SCHEMA_V,
        "id": str(pid or uuid.uuid4().hex[:12]),
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
        "id": str(d.get("id") or ""),      # 옛/외부 파일엔 없을 수 있다 → 호출측이 폴백
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


# ---------- 기본값 표 대조기 (`python presets.py`) ----------
# 룩 키를 추가하거나 기본값을 바꿨을 때 **여기로 확인한다.** 기본값이 흩어져 있으면(슬라이더
# `value:` / `defaultValue:` / applyEdits 폴백) 조용히 갈라지고, 갈라지면 배지가 거짓을 말한다.
# 앱은 이 블록을 실행하지 않는다(`__main__` 가드).

def _drift_report(root: str = None) -> int:
    """LOOK_DEFAULTS ↔ main._PRESET_KEYS ↔ QML 슬라이더 기본값 대조. 불일치 개수를 반환."""
    import io as _io
    import re as _re
    root = root or os.path.dirname(os.path.abspath(__file__))
    main_src = _io.open(os.path.join(root, "main.py"), encoding="utf-8").read()
    qml = _io.open(os.path.join(root, "ui", "Main.qml"), encoding="utf-8").read()
    blk = main_src[main_src.index("_PRESET_KEYS = ("):]
    keys = _re.findall(r'"([^"]+)"', blk[:blk.index(")")])

    bad = 0
    miss = [k for k in keys if k not in LOOK_DEFAULTS]
    extra = [k for k in LOOK_DEFAULTS if k not in keys]
    if miss:
        print(f"[X] LOOK_DEFAULTS 에 없는 _PRESET_KEYS: {miss}")
        bad += len(miss)
    if extra:
        print(f"[X] _PRESET_KEYS 에 없는 LOOK_DEFAULTS: {extra}")
        bad += len(extra)
    if not miss and not extra:
        print(f"[OK] 키 집합 일치 ({len(keys)}개)")

    # QML 슬라이더: `id: xSlider` 블록의 value:/defaultValue: 가 표와 같은지.
    # 슬라이더 id ↔ 프리셋 키 대응은 applyEdits 의 `xSlider.value = _ev(p, "key", ...)` 에서 얻는다.
    pairs = _re.findall(r'(\w+)\.value\s*=\s*_ev\(\s*p,\s*"([^"]+)"', qml)
    seen, nofind = 0, []
    for sid, key in pairs:
        if key not in LOOK_DEFAULTS or isinstance(LOOK_DEFAULTS[key], bool) \
                or not isinstance(LOOK_DEFAULTS[key], (int, float)):
            continue
        # ⚠️`id: x` 뒤가 개행이 아닐 수 있다 — 컬러 그레이딩 슬라이더는 `id: x; from: 0; ...`
        #   처럼 세미콜론으로 한 줄에 쓴다.
        # ⚠️`\b` 를 앞에도 붙인다 — `cgHueMid: cgMidHueSlider` 의 꼬리가 `id: ` 로 읽혀
        #   엉뚱한 위치를 잡았다(그래서 두 슬라이더가 '못 읽음' 으로 나왔다).
        mid = _re.search(r'\bid:\s*' + sid + r'\b', qml)
        if not mid:
            nofind.append(sid)
            continue
        i = mid.start()
        blk2 = qml[i:i + 700]              # 선언 블록 — value:/defaultValue: 가 이 안에 있다
        hit = False
        for prop in ("value", "defaultValue"):
            mm = _re.search(r'\b' + prop + r':\s*(-?[\d.]+)', blk2)
            if not mm:
                continue
            hit = True
            seen += 1
            got, want = float(mm.group(1)), float(LOOK_DEFAULTS[key])
            if abs(got - want) > 1e-9:
                print(f"[X] {sid}.{prop} = {got} 인데 LOOK_DEFAULTS['{key}'] = {want}")
                bad += 1
        if not hit:
            nofind.append(sid)
    # ⚠️'못 찾은 것'을 반드시 보고한다 — 조용히 건너뛰면 8/35 만 검사하고도 통과로 읽힌다.
    if nofind:
        print(f"[X] 기본값을 못 읽은 슬라이더 {len(nofind)}개: {sorted(set(nofind))}")
        bad += len(set(nofind))
    print(f"[..] QML 슬라이더 기본값 {seen}개 확인")

    # applyEdits 는 표를 읽어야 한다(리터럴이 남아 있으면 갈라진다). 센티널 둘은 예외 —
    # `""`(simKey: 미지정→simIndex 폴백)와 `null`(curves: resetAll 하라)은 룩 값이 아니다.
    apply_src = qml[qml.index("function applyEdits("):qml.index("function resetSky(")]
    lits = [(k, v) for k, v in _re.findall(r'_ev\(\s*p,\s*"([^"]+)"\s*,\s*([^()]*?)\s*\)',
                                           apply_src)
            if k in LOOK_DEFAULTS and "lookDef" not in v]
    for k, v in lits:
        if (k, v) in (("simKey", '""'), ("curves", "null")):
            continue
        print(f"[X] applyEdits 가 '{k}' 를 리터럴 {v} 로 폴백 — win.lookDef(\"{k}\") 를 쓸 것")
        bad += 1
    print(f"[..] applyEdits 폴백 {len(lits)}개가 리터럴(센티널 예상 2개)")

    # resetEdits 의 직접 대입도 표와 같아야 한다(리셋이 공장값과 갈라지면 배지가 거짓이 된다).
    # ⚠️함수 이름을 상수로 박아 두면 이름이 바뀔 때 **조용히 0개 확인**이 되어 통과한다
    #   (실제로 `resetEdits` 로 찾다가 그렇게 됐다). 그래서 못 찾으면 실패로 만든다.
    RESET_FN = "function resetAllEdits("
    if RESET_FN not in qml:
        print(f"[X] QML 에서 {RESET_FN} 를 못 찾았다 — 이름이 바뀌었으면 여기도 고칠 것")
        return bad + 1
    reset = qml[qml.index(RESET_FN):]
    reset = reset[:reset.index("\n    function ")] if "\n    function " in reset else reset
    rseen = 0
    for sid, key in pairs:
        want = LOOK_DEFAULTS.get(key)
        if not isinstance(want, (int, float)) or isinstance(want, bool):
            continue
        mm = _re.search(r'\b' + sid + r'\.value\s*=\s*(-?[\d.]+)', reset)
        if not mm:
            continue
        rseen += 1
        if abs(float(mm.group(1)) - float(want)) > 1e-9:
            print(f"[X] resetEdits: {sid} = {mm.group(1)} 인데 "
                  f"LOOK_DEFAULTS['{key}'] = {want}")
            bad += 1
    if rseen == 0:
        print("[X] resetAllEdits 에서 슬라이더 대입을 하나도 못 읽었다 — 검사가 헛돌고 있다")
        bad += 1
    print(f"[..] resetAllEdits 대입 {rseen}개 확인")
    print("[OK] 불일치 없음" if bad == 0 else f"[X] 불일치 {bad}건")
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if _drift_report() else 0)
