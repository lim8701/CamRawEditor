"""필름 'Date Stamp'(쿼츠 데이트백) 렌더 — 물리 과정 재현.

날짜를 사진 위에 얹는 게 아니라, 데이트백 LED 가 사진과 '같은 필름 에멀전'을 빛으로
노광하는 물리 과정을 재현한다: 가산(screen) 합성(밝은 곳 씻김/어두운 곳 선명), 사진
필름 그레인 연동, 강한 빛의 할레이션(핫코어→앰버→적주황 번짐), 센서 프레임 기준 코너
배치(세로 사진 회전). Export(stamp_export)는 screen+source-over 혼합(SCREEN_MIX)으로 합성.
⚠️프리뷰는 QML Image source-over 오버레이(opacity=STAMP_STRENGTH) — 어두운 배경에선 export
와 사실상 같고, 밝은 배경에선 screen 씻김이 없어 '의도적으로' 조금 다르다(프리뷰 단순성 우선).
shaders/stamp.frag(배경을 읽어 프리뷰도 screen 으로 정합시키는 경로)는 예약해 두었으나 현재
미배선(QML 은 평범한 오버레이 사용). 위치/크기는 최종 프레임 짧은 변 대비 비율(크롭 무관).
설계·물리 매핑 상세는 docs/date_stamp.md 참조.

폰트: DSEG 7/14-세그 Classic(Regular/Bold, 정체/이탤릭) + Doto 도트매트릭스 (모두 SIL OFL).
아포스트로피(')·슬래시(/)는 세그먼트 폰트에 없어 Qt 폴백으로 렌더되나 글로우에 묻혀 무방.
"""
import colorsys
import os
import shutil
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, grey_dilation, zoom
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QImage,
                           QPainter)


def _asset_base() -> Path:
    """폰트 등 번들 자산 위치. frozen(PyInstaller/Nuitka) 인식. (main.app_base 와 동일 로직,
    순환 임포트 방지를 위해 모듈 내부에 둠.)"""
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", None)
        return Path(mp) if mp else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_FONTS_DIR = _asset_base() / "fonts"
# 필름 데이트백 대표 3방식(모두 앰버 글로우, DSEG SIL OFL — keshikan/DSEG):
#   classic=7-세그 클래식(기본), modern=7-세그 모던, 14seg=14-세그 스타버스트.
# 스타일 -> (폰트파일, italic, weight). ⚠️italic/light 는 별도 패밀리가 아니라 같은
# 패밀리(예: "DSEG7 Classic")의 face 라, 패밀리명만으론 구분 안 됨 → QFont 에 italic·weight
# 를 직접 지정해야 해당 face 가 선택된다.
STYLES = {
    # 7-seg Classic — Regular/Bold × 정체/이탤릭
    "7c_reg":      ("DSEG7Classic-Regular.ttf",     False, "regular"),
    "7c_reg_it":   ("DSEG7Classic-Italic.ttf",      True,  "regular"),
    "7c_bold":     ("DSEG7Classic-Bold.ttf",        False, "bold"),
    "7c_bold_it":  ("DSEG7Classic-BoldItalic.ttf",  True,  "bold"),
    # 14-seg Classic — 동일 매트릭스
    "14c_reg":     ("DSEG14Classic-Regular.ttf",    False, "regular"),
    "14c_reg_it":  ("DSEG14Classic-Italic.ttf",     True,  "regular"),
    "14c_bold":    ("DSEG14Classic-Bold.ttf",       False, "bold"),
    "14c_bold_it": ("DSEG14Classic-BoldItalic.ttf", True,  "bold"),
    # 도트매트릭스
    "dotmatrix":   ("Doto.ttf",                     False, "regular"), # Doto 원형 도트(OFL)
    # 세그먼트가 아닌 '필름에 어울리는' 계열(사용자 요청). 각인이 아니라 '찍어 넣은 글자'
    # 성격이라, 날짜 외 자유 텍스트를 넣는 용도에도 맞다. 모두 SIL OFL.
    "typewriter":  ("CourierPrime-Regular.ttf",     False, "regular"), # 인덱스 프린트/타자기
    "terminal":    ("VT323-Regular.ttf",            False, "regular"), # 구형 LED/터미널
    "condensed":   ("Oswald-Variable.ttf",          False, "regular"), # 패키지·라벨 인쇄
}
USER_PREFIX = "user:"       # 사용자가 추가한 폰트의 스타일 키 접두사(user:<파일명>)
DEFAULT_STYLE = "7c_bold"   # 아이코닉 쿼츠 데이트백 = 7-seg Classic Bold
_families = {}          # style -> 등록된 패밀리명 캐시(스타일별 1회 등록)
_font_ids = {}          # style -> addApplicationFont 반환 id(삭제 전 등록 해제에 필요)

# --- 이미지 상대 기하/룩 (프리뷰·export 단일 소스) ---
# 기준은 '짧은 변' -> 가로/세로 방향 무관하게 같은 상대 크기.
# 숫자 높이 = 짧은 변 대비 '비율'을 슬라이더로 직접 지정(절대 pt 아님 — 프록시/풀해상도
# 무관하게 프리뷰=export 유지). 기본 3.2%(기존 룩), 안전 범위로 클램프.
DEFAULT_SIZE_FRAC = 0.032
SIZE_FRAC_MIN, SIZE_FRAC_MAX = 0.012, 0.050
TEXT_FRAC = DEFAULT_SIZE_FRAC   # 하위호환 기본값
MARGIN_FRAC = 0.050     # 우/하 여백 = 짧은 변의 5.0% (⚠️ui/Main.qml stampOverlay.margin 과 동기 유지)
CORE_BLUR_FRAC = 0.010  # 코어 가우시안 반경/텍스트높이 (고정) — 숫자 본체 선명도
STAMP_BRIGHTNESS = 0.85  # 스탬프 전체 밝기(불투명도) 배율 (고정)
# 필름 광학 각인의 색: 핫코어(밝은 주황-노랑) → 앰버 → 적주황 헤일로로 번짐.
C_CORE = np.array([1.00, 0.95, 0.76], np.float32)   # 노출 과다된 뜨거운 중심(흰빛쪽, 더 밝게)
C_MID = np.array([1.00, 0.54, 0.16], np.float32)    # 앰버
C_HALO = np.array([0.94, 0.24, 0.06], np.float32)   # 적주황 외곽 번짐
# 흑백 사진 등에 맞춰 각인 색을 바꿀 수 있게 위 3색 램프를 **한 색에서 파생**한다.
# 사용자가 고르는 색 = 중간(LED) 색이고, 핫코어는 색상이 노랑쪽(+20°)으로 돌며 채도가
# 크게 떨어지고, 헤일로는 적색쪽(-15°)으로 돌며 살짝 진해진다 — 흑체 hot→cool 진행과 같은
# 방향이다. ⚠️계수를 위 리터럴에서 **유도**하므로 색=C_MID 이면 세 색이 정확히 재현된다
# (기존 사진의 룩 불변 — 하드코딩 전사로 두면 반올림으로 어긋난다).
# 채도 0(중성)을 고르면 세 색 모두 무채색이 되어 흑백 사진용 백색 각인이 된다.
DEFAULT_COLOR = "#FF8A29"       # = C_MID (앰버 LED). QML 스와치 기본값과 동기 유지.
_H0, _S0, _V0 = colorsys.rgb_to_hsv(*(float(x) for x in C_MID))
_RAMP = {}      # 'core'/'halo' -> (색상 오프셋, 채도 배율, 명도 배율)
for _n, _c in (("core", C_CORE), ("halo", C_HALO)):
    _h, _sa, _v = colorsys.rgb_to_hsv(*(float(x) for x in _c))
    _RAMP[_n] = (_h - _H0, _sa / _S0, _v / _V0)


def color_ramp(color=DEFAULT_COLOR):
    """각인 색(hex 또는 (r,g,b) [0,1]) → (핫코어, 중간, 헤일로) 3색.
    색=DEFAULT_COLOR 이면 C_CORE/C_MID/C_HALO 를 정확히 돌려준다."""
    if isinstance(color, str):
        c = QColor(color)
        if not c.isValid():
            c = QColor(DEFAULT_COLOR)
        # ⚠️기본 앰버는 **리터럴 3색을 그대로** 돌려준다 — hex 는 8bit 라 C_MID(0.54,0.16)를
        #   정확히 표현하지 못하고, HSV 왕복까지 겹쳐 0.002(8bit 0.5코드) 차이가 남았다.
        #   기존 사진의 스탬프가 이유 없이 바뀌지 않게 하는 것이 이 분기의 목적이다.
        if c.rgb() == QColor(DEFAULT_COLOR).rgb():
            return C_CORE.copy(), C_MID.copy(), C_HALO.copy()
        mid = (c.redF(), c.greenF(), c.blueF())
    else:
        mid = tuple(min(1.0, max(0.0, float(x))) for x in tuple(color)[:3])
        if np.array_equal(np.asarray(mid, np.float32), C_MID):
            return C_CORE.copy(), C_MID.copy(), C_HALO.copy()
    h, sa, v = colorsys.rgb_to_hsv(*mid)
    out = []
    for n in ("core", "halo"):
        dh, ks, kv = _RAMP[n]
        # 채도 0(중성)이면 색상은 무의미 — 배율만 적용해 무채색을 유지한다.
        out.append(np.array(colorsys.hsv_to_rgb((h + dh) % 1.0, min(1.0, sa * ks),
                                                min(1.0, v * kv)), np.float32))
    return out[0], np.array(mid, np.float32), out[1]


# 글로우 손잡이(사진별 슬라이더). 밝기=헤일로 가중 배율, 영역=헤일로 반경 배율.
# ⚠️영역을 키우면 가우시안 비용이 면적×σ 라 **배율의 약 3승**으로 늘어난다(실측: 최대
#   크기에서 ×1 351ms → ×3 2051ms) → 아래 _wide_blur 의 1/4 해상도 계산이 전제다.
# 각인 색 팔레트(QML 스와치). 자유 색 선택은 'Custom' 에서 ColorDialog 로.
#  - 앰버=쿼츠 데이트백 기본  - 적주황=더 오래된/뜨거운 LED  - 웜화이트/화이트=흑백 사진용
#    (중성색은 램프가 통째로 무채색이 되어 흑백 프레임에서 색이 튀지 않는다)
COLORS = [DEFAULT_COLOR, "#FF5A28", "#FFD8A8", "#FFFFFF", "#7FE0FF", "#FF4FD8"]

DEFAULT_GLOW = 1.0
GLOW_MIN, GLOW_MAX = 0.0, 2.0
DEFAULT_SPREAD = 1.0
PREVIEW_REF_SHORT = 1000.0   # 프리뷰 스프라이트 기준 짧은 변(sprite_layer 기본값과 동일)
SPREAD_MIN, SPREAD_MAX = 0.4, 2.0
# 축소 배율은 상수가 아니라 **σ 에 비례**해 정한다 — 오차가 σ/k 하나로 정해지기 때문이다
# (실측 피크오차: σ/k≈12 → 2.1% / ≈8 → 2.3% / ≈3.4 → 10%). 고정 k 를 쓰면 좁은 블러에서
# 오차가 커지고 넓은 블러에서 비용을 못 줄인다.
_WIDE_SIGMA_PER_K = 8.0     # 목표 σ/k — 피크오차 약 2~4%
_WIDE_K_MAX = 8


def _glow_pad_px(text_h_px, spread=DEFAULT_SPREAD):
    """스프라이트 사방에 붙는 글로우 여유(px). render_sprite 와 **같은 식**이어야 한다.
    ⚠️`max(6.0, ...)` 하한까지 같아야 한다 — render_sprite 는 하한을 걸고 pad 를 구하는데
    여기서 안 걸면 아주 작은 출력(짧은 변 500px 미만 + 최소 크기)에서 상쇄가 실제 pad
    증가보다 커져 **글자가 2px 움직인다**(실측 600x400 프레임). 이 함수가 존재하는 이유가
    바로 '글자는 영역과 무관하게 제자리'이므로 하한 누락은 그 보장을 깬다."""
    return int(round(max(6.0, float(text_h_px)) * 1.6 * float(spread)))


def bleed_px(text_h_px, spread=DEFAULT_SPREAD):
    """기본 영역 대비 **늘어난(줄어든) 글로우 여유**(px, 정수).
    ⚠️pad 는 사방에 붙는데 마진은 '스프라이트 전체'를 프레임 끝에서 띄우므로, 이 값을
    마진에서 빼주지 않으면 **영역 슬라이더가 글자 위치를 밀어버린다**(사용자 보고).
    데이트백은 고정된 자리에 각인하고 빛만 번지므로 글자는 제자리에 있어야 한다.
    ⚠️**실제 pad(정수)의 차이**여야 한다 — 연속값(`th*1.6*(spread-1)`)으로 상쇄했더니
    반올림 잔차 ±0.5px 가 영역 값에 따라 뒤집혀 **드래그 중 1px 진동**으로 보였다
    (사용자 보고). 정수 차이를 쓰면 `pad - bleed == pad(기본)` 이 되어 글자 위치가
    영역과 **정확히 무관**해진다."""
    sp = min(SPREAD_MAX, max(SPREAD_MIN, float(spread)))
    return _glow_pad_px(text_h_px, sp) - _glow_pad_px(text_h_px, DEFAULT_SPREAD)


def bleed_frac(size_frac=DEFAULT_SIZE_FRAC, spread=DEFAULT_SPREAD, ref_short=PREVIEW_REF_SHORT):
    """bleed_px 를 짧은 변 대비 비율로 — QML 오버레이 배치용.
    ⚠️`ref_short` 는 프리뷰 스프라이트를 만든 기준(sprite_layer 의 ref_short)과 **같아야**
    한다. 스프라이트 폭도 그 기준으로 나눈 비율(stampWRatio)이라, 둘이 같은 기준일 때만
    글자 위치가 영역과 무관해진다."""
    return bleed_px(_clamp_frac(size_frac) * float(ref_short), spread) / float(ref_short)


def _wide_blur(m, sigma, approx=True):
    """넓고 매끄러운 글로우 전용 블러 — 1/k 해상도에서 계산 후 되돌린다.
    ⚠️축소는 **면적평균**이어야 한다 — 점샘플(`m[::k,::k]`)은 선명한 글자 마스크를
    에일리어싱해 같은 비용에 오차가 2~3배 커진다(실측 k=4: 점샘플 12.6~20.7% vs
    면적평균 5.9~7.0%). 오차의 원인은 업샘플이 아니라 축소였다 — 업샘플 차수를 3차로
    올리는 것은 오차가 동일하고 비용만 3배라 기각.
    비용은 넓은 블러에서 3~5배 줄어 '영역' 슬라이더를 실시간으로 둘 수 있게 된다.
    ⚠️`approx=False` 는 풀해상도(정확값)다 — **기본 영역(spread≤1)에서는 반드시 이쪽**을
    쓴다. 최적화는 3~8코드(8bit 피크, 평균 0.2~0.35)의 룩 차이를 내는데, 그 대가로 얻는
    것은 '새로 생긴 넓은 영역'의 속도뿐이다. 기존 사진에까지 적용하면 아무 이득 없이
    예전에 저장한 스탬프의 모습만 바꾸게 된다."""
    k = int(min(_WIDE_K_MAX, max(1, round(sigma / _WIDE_SIGMA_PER_K)))) if approx else 1
    if k <= 1:                     # 좁은 블러 — 축소 이득이 없다(정확값 사용)
        return gaussian_filter(m, sigma)
    H, W = m.shape
    h, w = max(1, H // k), max(1, W // k)
    small = gaussian_filter(m[:h * k, :w * k].reshape(h, k, w, k).mean(axis=(1, 3)), sigma / k)
    out = zoom(small, (H / h, W / w), order=1)[:H, :W]
    if out.shape != (H, W):        # zoom 라운딩 언더슈트 → edge 패드(위 노이즈와 같은 처리)
        out = np.pad(out, ((0, H - out.shape[0]), (0, W - out.shape[1])), mode="edge")
    return out
# source-over(알파) 합성의 불투명도 배율. 배경 밝기와 무관하게 일정한 룩.
# 스탬프는 크롭/회전이 끝난 '최종 프레임'에 source-over 로 찍는다(export=numpy, 프리뷰=QML
# Image 오버레이 동일 합성). 스프라이트 RGBA 에 핫코어→앰버→헤일로 글로우가 이미 베이크돼 있어
# 단순 source-over 로도 빛나는 데이트백 룩이 난다.
STAMP_STRENGTH = 0.92   # 프리뷰 stampOverlay.opacity 와 일치
STAMP_GRAIN_K = 0.27    # 스탬프 그레인 = 전체 grainAmt × 이 계수(같은 에멀전 → 사진 필름 그레인에 연동).
                        # ⚠️coeffs.GRAIN 에 비례해 튜닝된 값 — GRAIN 0.21 기준 0.24 였고, GRAIN 이
                        #   0.24 로 오르며 같은 비율(×1.143)로 0.27 재환산. GRAIN 변경 시 여기도 갱신.
                        # 곱셈 변조 진폭(×0.5)이 사진 그레인(add ∝ grainAmt)과 대략 맞도록 튜닝. grainAmt=0 → 매끈.
SCREEN_MIX = 0.7        # 합성 블렌드: 1.0=순수 screen(밝은 배경서 많이 사라짐), 0.0=source-over(스티커).
                        # 중간값=밝은 배경 과다 소멸 완화. ⚠️ui/Main.qml stampOverlay.screenMix 와 동기 유지.


def user_fonts_dir(create=False):
    """사용자가 추가한 폰트 폴더. 앱 폴더가 아니라 사용자 데이터 폴더에 두는 이유는 models
    와 동일(설치 폴더 무쓰기·업데이트에도 보존). app_dirs 는 지연 임포트.
    ⚠️`create` 는 **추가할 때만** True — 읽기 경로(`has_font`)는 `stampChanged` 마다 불리고
    그건 슬라이더 드래그 중 매 프레임이다. 거기서 mkdir 를 돌리면 프레임마다 디스크를
    건드리고, 사용자가 폴더를 지워도 즉시 되살아난다."""
    import app_dirs
    d = Path(app_dirs.user_data_path("fonts"))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def user_font_styles():
    """추가된 사용자 폰트의 스타일 키 목록(정렬). 키 = 'user:<파일명>'."""
    try:
        return sorted(USER_PREFIX + f.name for f in user_fonts_dir().iterdir()
                      if f.suffix.lower() in (".ttf", ".otf") and f.is_file())
    except Exception:
        return []


def font_path(style):
    """스타일 키 → 폰트 파일 경로. 사용자 폰트는 사용자 폴더에서 찾는다.
    ⚠️경로는 항상 **파일명만** 이어붙인다 — 키에 담긴 문자열을 그대로 경로로 쓰면
    'user:../../x' 같은 값이 폴더 밖을 가리킬 수 있다."""
    if str(style).startswith(USER_PREFIX):
        name = os.path.basename(str(style)[len(USER_PREFIX):])
        # 빈 이름이면 폴더 자체를 가리키게 된다 — 파일이 아닌 이름으로 바꿔 폴백을 타게 한다.
        return user_fonts_dir() / (name or "_")
    spec = STYLES.get(style, STYLES[DEFAULT_STYLE])
    return _FONTS_DIR / spec[0]


def font_family(style=DEFAULT_STYLE):
    """스타일별 폰트를 1회 등록하고 패밀리명 반환. 번들 폰트는 fonts/, 사용자 폰트는
    사용자 데이터 폴더에서 찾는다. **파일이 없으면 기본 스타일로 폴백**한다 — 사용자
    폰트로 만든 사이드카/레시피를 다른 기계에서 열면 그 파일이 없는 것이 정상이고,
    그때 monospace 로 떨어지면 각인이 전혀 다른 모습이 된다.
    italic/weight 는 render_sprite 에서 QFont 에 지정(같은 패밀리 내 face 구분)."""
    fam = _families.get(style)
    if fam is None:
        path = font_path(style)
        fid = QFontDatabase.addApplicationFont(str(path)) if path.is_file() else -1
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fid >= 0:
            _font_ids[style] = fid          # 삭제할 때 등록 해제하려면 id 가 필요하다
        if not fams and style != DEFAULT_STYLE:
            fam = font_family(DEFAULT_STYLE)       # 없는 폰트 → 기본 데이트백 폰트로
        else:
            fam = fams[0] if fams else "monospace"
        _families[style] = fam
    return fam


def has_font(style) -> bool:
    """그 스타일의 폰트 파일이 실제로 있는가(누락 안내 배너 판정용)."""
    try:
        return font_path(style).is_file()
    except Exception:
        return False


def remove_user_font(style) -> bool:
    """추가한 사용자 폰트를 지운다.
    ⚠️**등록 해제(removeApplicationFont)가 먼저**여야 한다 — Windows 는
    `addApplicationFont` 한 파일을 잠그기 때문에, 그 폰트를 한 번이라도 쓴 세션에서는
    곧바로 unlink 하면 `PermissionError [WinError 32]` 로 삭제가 조용히 실패한다
    (export 의 os.replace 폴백과 같은 계열의 함정 — 실측으로 걸렸다)."""
    if not str(style).startswith(USER_PREFIX):
        return False
    path = font_path(style)
    fid = _font_ids.pop(style, None)
    _families.pop(style, None)
    if fid is not None:
        QFontDatabase.removeApplicationFont(fid)
    try:
        path.unlink()
        return True
    except Exception as exc:
        print(f"[stamp] 폰트 삭제 실패: {exc}")
        return False


def add_user_font(src):
    """사용자가 고른 폰트 파일을 사용자 폴더로 **복사**하고 스타일 키를 돌려준다.
    복사하는 이유: 원본이 옮겨지거나 지워져도 사이드카가 계속 열려야 한다.
    같은 이름이 있으면 덮어쓴다(같은 폰트를 다시 고른 흔한 경우 — 새 키를 만들면
    목록에 중복이 쌓인다). 실패 시 예외 대신 빈 문자열."""
    try:
        srcp = Path(str(src))
        if srcp.suffix.lower() not in (".ttf", ".otf") or not srcp.is_file():
            return ""
        dst = user_fonts_dir(create=True) / srcp.name
        # 같은 이름이 이미 등록돼 있으면 **먼저 등록 해제**한다 — 덮어쓰기 자체는 잠금에
        # 걸리지 않지만(실측), Qt 가 이미 로드한 옛 글리프를 계속 쓰면 '같은 이름으로
        # 고친 폰트를 다시 추가'가 아무 효과 없는 것처럼 보인다.
        old_style = USER_PREFIX + dst.name
        old_fid = _font_ids.pop(old_style, None)
        _families.pop(old_style, None)
        if old_fid is not None:
            QFontDatabase.removeApplicationFont(old_fid)
        if not dst.exists() or srcp.resolve() != dst.resolve():
            shutil.copyfile(srcp, dst)      # 이미 그 폴더의 파일을 고른 경우는 복사 생략
        style = USER_PREFIX + dst.name
        _families.pop(style, None)          # 덮어썼으면 패밀리 캐시도 버린다
        _font_ids.pop(style, None)
        # ⚠️Qt 등록 결과로 직접 판정한다. 예전엔 `font_family(style) != "monospace"` 로 봤는데,
        #   font_family 가 실패 시 **기본 데이트백 폰트로 폴백**하도록 바뀌면서 이 검사가
        #   죽은 코드가 됐다 — 폰트가 아닌 파일도 '성공'으로 보고되어 목록에 남고, 파일은
        #   존재하니 누락 배너도 안 뜨고, 각인만 조용히 기본 폰트로 그려졌다.
        fid = QFontDatabase.addApplicationFont(str(dst))
        if fid < 0 or not QFontDatabase.applicationFontFamilies(fid):
            if fid >= 0:
                QFontDatabase.removeApplicationFont(fid)
            try:
                dst.unlink()                # 못 읽는 파일을 폴더에 남기지 않는다
            except Exception:
                pass
            return ""
        _font_ids[style] = fid
        return style
    except Exception as exc:
        print(f"[stamp] 폰트 추가 실패: {exc}")
        return ""


def _alpha_from_qimage(img):
    """ARGB32 QImage → (H,W) float alpha [0,1]. (ARGB32 는 bytesPerLine=4w, 패딩 없음)"""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = (np.frombuffer(ptr, np.uint8)
           .reshape(h, img.bytesPerLine())[:, :w * 4]
           .reshape(h, w, 4))
    return arr[..., 3].astype(np.float32) / 255.0   # ARGB32(LE)=B,G,R,A


def render_sprite(text, text_h_px, style=DEFAULT_STYLE, grain=0.0,
                  color=DEFAULT_COLOR, glow=DEFAULT_GLOW, spread=DEFAULT_SPREAD):
    """필름 광학 각인 스타일 날짜 스프라이트를 RGBA float (H,W,4) [0,1] 로 반환.
    코어(살짝 번짐)→다층 헤일로, 핫코어→중간→외곽 색 그라데이션, 불규칙 번짐.
    style=폰트 방식, color=각인 색(중성=흑백 사진용 백색 각인), glow=헤일로 밝기 배율,
    spread=헤일로 영역(반경) 배율. 기본값은 기존 앰버 룩을 정확히 재현한다."""
    text_h_px = max(6.0, float(text_h_px))
    c_core, c_mid, c_halo = color_ramp(color)
    glow = min(GLOW_MAX, max(GLOW_MIN, float(glow)))
    spread = min(SPREAD_MAX, max(SPREAD_MIN, float(spread)))
    spec = STYLES.get(style, STYLES[DEFAULT_STYLE])
    fam = font_family(style)
    f = QFont(fam)
    f.setPixelSize(int(round(text_h_px)))
    f.setItalic(bool(spec[1]))                                  # 기울임 face 선택
    _w = {"light": QFont.Weight.Light, "regular": QFont.Weight.Normal}
    f.setWeight(_w.get(spec[2], QFont.Weight.Bold))             # 도트매트릭스=Normal(가짜 볼드 방지)
    fm = QFontMetrics(f)
    tw = fm.horizontalAdvance(text)
    th = fm.height()
    pad = _glow_pad_px(text_h_px, spread)   # 넓은 글로우 여유(영역 배율만큼 함께 확장 —
                                            # 안 늘리면 넓힌 헤일로가 캔버스에서 잘린다)
    W, H = tw + 2 * pad, th + 2 * pad

    canvas = QImage(W, H, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(0, 0, 0, 0))
    p = QPainter(canvas)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 255))   # 흰색으로 그려 알파만 사용
    p.drawText(pad, pad + fm.ascent(), text)
    p.end()
    m = _alpha_from_qimage(canvas)         # 숫자 알파 마스크

    # 빛이 에멀전에 스며든 듯: 코어도 살짝 번지게 + 다중 반경 헤일로(멀리 퍼지는 번짐)
    # 영역을 기본보다 넓힌 경우에만 축소 근사를 쓴다 — 기본 이하는 예전과 비트 동일.
    _apx = spread > DEFAULT_SPREAD
    core = gaussian_filter(m, text_h_px * CORE_BLUR_FRAC)   # 코어 블러(고정, 항상 풀해상도)
    gnear = _wide_blur(m, text_h_px * 0.080 * spread, _apx)
    gfar = _wide_blur(m, text_h_px * 0.300 * spread, _apx)
    # 사이드까지 균일한 글로우: 글자열을 가로로 팽창해 연속 띠로 만든 뒤 블러
    # -> 단일 가우시안(중앙만 밝음)보다 끝단까지 고르게 채워짐.
    dil = grey_dilation(m, size=(max(1, int(text_h_px * 0.22)),
                                 max(1, int(text_h_px * 0.95))))
    gband = _wide_blur(dil, text_h_px * 0.42 * spread, _apx)

    # 헤일로는 피크 정규화 -> 넓게 퍼져도 밝기를 유지(가우시안 진폭 급감 보정 = 번짐 가시화)
    def _nrm(x):
        return x / (float(x.max()) + 1e-6)
    w_core = core * 1.0
    w_mid = _nrm(gnear) * 0.50 * glow
    w_halo = _nrm(gfar) * 0.30 * glow
    w_band = _nrm(gband) * 0.42 * glow   # 균일한 사이드 글로우(가로 팽창 띠)
    wsum = w_core + w_mid + w_halo + w_band + 1e-6
    rgb = (w_core[..., None] * c_core
           + w_mid[..., None] * c_mid
           + (w_halo + w_band)[..., None] * c_halo) / wsum[..., None]   # 코어=핫, 외곽=진한색

    # 불규칙한 번짐(유기적): 저주파 노이즈로 헤일로 강도만 변조(코어는 또렷이 유지)
    rng = np.random.default_rng(7)
    gh, gw = max(2, H // 36), max(2, W // 36)
    nlow = zoom(rng.random((gh, gw), dtype=np.float32),
                (H / gh, W / gw), order=1)[:H, :W]
    if nlow.shape != (H, W):     # zoom 라운딩이 언더슈트하면 슬라이스로 못 채움 → edge 패드
        nlow = np.pad(nlow, ((0, H - nlow.shape[0]), (0, W - nlow.shape[1])), mode="edge")
    # ⚠️이름을 glow 로 쓰지 않는다 — 인자 `glow`(헤일로 밝기 배율, 스칼라)를 덮어써서,
    #   이 아래에 그 인자를 읽는 코드를 추가하면 (H,W) 배열이 조용히 브로드캐스트된다.
    glow_field = (w_mid + w_halo + w_band) * (0.78 + 0.22 * nlow)
    inten = np.clip(w_core + glow_field, 0.0, 1.0)
    col = np.clip(rgb, 0.0, 1.0)

    # 단순 source-over(프리뷰 QML Image + export numpy 동일 합성)만으로도 예전 '하이브리드'(코어
    # source-over + screen 글로우) 룩이 나도록, '검은 배경 위 하이브리드 결과'를 스프라이트에 미리
    # 베이크한다. 알파 = 그 결과의 밝기(피크 채널) → 어두운 배경에선 예전과 동일, 밝은 배경에선
    # screen 처럼 빛을 더한다. STAMP_STRENGTH 는 합성 때 알파에 곱해지므로 여기서 함께 반영.
    s = STAMP_STRENGTH
    aa = np.clip(inten * s, 0.0, 1.0)[..., None]
    t = np.clip((aa - 0.45) / 0.40, 0.0, 1.0)
    coreA = (t * t * (3.0 - 2.0 * t)) * 0.70                       # smoothstep(0.45,0.85,aa)*0.70
    core_black = col * coreA                                       # 코어 over black
    g = col * np.clip(aa * (1.0 - coreA * 0.5) * 1.2, 0.0, 1.0)    # screen 글로우 항
    ob = 1.0 - (1.0 - core_black) * (1.0 - g)                      # 예전 하이브리드 over black
    # 필름 그레인: 날짜도 그레인 있는 에멀전에 각인된 것처럼 — over-black 결과를 고주파 노이즈로
    # 변조. 모든 채널 동일 배율이라 아래 peak 정규화에서 col2(핫 휴)는 불변이고 알파(A2=밝기)에만
    # 그레인이 실린다. render_sprite 는 프리뷰(sprite_layer)·export(stamp_export) 공용 → 양쪽 동일
    # 성격. 셀은 텍스트 높이 비례라 스탬프 대비 밀도 일관. 장면 그레인과 픽셀일치는 기대 안 함.
    gcell = max(1.0, text_h_px / 12.0)
    ggh, ggw = max(2, int(round(H / gcell))), max(2, int(round(W / gcell)))
    grng = np.random.default_rng(11)
    gn = zoom(grng.random((ggh, ggw), dtype=np.float32),
              (H / ggh, W / ggw), order=1)[:H, :W]
    if gn.shape != (H, W):
        gn = np.pad(gn, ((0, H - gn.shape[0]), (0, W - gn.shape[1])), mode="edge")
    ob = np.clip(ob * (1.0 + float(grain) * (gn[..., None] - 0.5)), 0.0, 1.0)
    A2 = np.clip(ob.max(axis=2, keepdims=True), 0.0, 1.0)         # 알파 = 밝기(피크 채널)
    col2 = ob / np.maximum(A2, 1e-4)                              # 색(피크 정규화 → 핫 휴 유지)

    rgba = np.empty((H, W, 4), np.float32)
    rgba[..., :3] = np.clip(col2, 0.0, 1.0)
    rgba[..., 3] = np.clip(A2[..., 0] / s * STAMP_BRIGHTNESS, 0.0, 1.0)   # 합성 때 ×s → 실효 알파 = A2×밝기(고정)
    return rgba


# --- 촬영 방향(데이트백 현실 반영) ---
# 실제 쿼츠 데이트백은 '센서(가로) 프레임의 우하단'에 각인된다. 세로로 촬영하면(센서를 회전)
# 업라이트로 볼 때 각인이 90° 돌아간 채 대응 코너로 이동한다. EXIF Orientation 으로 센서→업라이트
# 회전(CW)을 구해 스프라이트를 같은 각도로 돌리고 대응 코너에 배치한다(프리뷰=export 동일).
_ROT_FROM_ORI = {1: 0, 3: 180, 6: 90, 8: 270}      # EXIF Orientation -> 업라이트로 만든 CW 회전(도)
_ROT_CORNER = {0: "br", 90: "bl", 180: "tl", 270: "tr"}  # 그 회전 후 센서 우하단이 오는 코너


def rot_from_orientation(ori) -> int:
    """EXIF Orientation(1~8) -> 센서(가로)를 업라이트로 만든 CW 회전(0/90/180/270). 미러는 0 폴백."""
    try:
        return _ROT_FROM_ORI.get(int(ori), 0)
    except Exception:
        return 0


def corner_for_rot(rot) -> str:
    """CW 회전(도) -> 업라이트 프레임에서 데이트백이 오는 코너('br'/'bl'/'tl'/'tr')."""
    return _ROT_CORNER.get(int(rot) % 360, "br")


def _rotate_sprite(sprite, rot):
    """스프라이트(가로 텍스트)를 CW 회전(도)만큼 회전. np.rot90 은 CCW 라 k=-회전/90."""
    k = (int(rot) // 90) % 4
    return sprite if k == 0 else np.ascontiguousarray(np.rot90(sprite, k=-k))


def _placement(sprite, img_w, img_h, margin_px, corner="br"):
    """sprite 를 지정 코너에 둘 때의 (x0,y0,sprite_clipped). corner='br'/'bl'/'tl'/'tr'.
    ⚠️마진은 글로우 상쇄(bleed_frac) 때문에 **음수가 될 수 있다** — 그 경우 프레임 밖으로
    나가는 글로우를 잘라내고 0 에 붙인다(실제로도 프레임 밖 빛은 기록되지 않는다)."""
    sh, sw, _ = sprite.shape
    right = corner in ("br", "tr")
    bottom = corner in ("br", "bl")
    x0 = (img_w - margin_px - sw) if right else (
        margin_px if margin_px < 0 else min(margin_px, max(0, img_w - sw)))
    y0 = (img_h - margin_px - sh) if bottom else (
        margin_px if margin_px < 0 else min(margin_px, max(0, img_h - sh)))
    cx, cy = max(0, -x0), max(0, -y0)          # 좌/상단으로 넘친 만큼 스프라이트를 잘라낸다
    if cx or cy:
        sprite = sprite[cy:, cx:]
    x0, y0 = max(0, x0), max(0, y0)
    sp = sprite[:img_h - y0, :img_w - x0]
    return x0, y0, sp


def _clamp_frac(size_frac):
    try:
        return min(SIZE_FRAC_MAX, max(SIZE_FRAC_MIN, float(size_frac)))
    except (TypeError, ValueError):
        return DEFAULT_SIZE_FRAC


def stamp_export(out, text, rot=0, style=DEFAULT_STYLE, size_frac=DEFAULT_SIZE_FRAC,
                 margin_frac=None, grain_amt=0.0, color=DEFAULT_COLOR,
                 glow=DEFAULT_GLOW, spread=DEFAULT_SPREAD):
    """크롭/회전까지 끝난 '최종 프레임' out (H,W,3) 의 코너에 날짜 스프라이트를 source-over
    합성(in-place). rot=촬영 방향(센서→업라이트 CW 회전) — 데이트백을 센서 우하단 각인처럼
    회전·코너 배치(세로 사진은 90° 돌아간 코너). 위치/크기는 out 짧은 변 기준(크롭 후에도 일정).
    style=폰트 방식(STYLES 키), size_frac=숫자높이/짧은변 비율(슬라이더).
    프리뷰 QML Image 오버레이와 동일 합성·회전(프리뷰=export). dtype 으로 비트깊이 자동 인식."""
    mx = 65535.0 if out.dtype == np.uint16 else 255.0
    H, W, _ = out.shape
    short = min(H, W)
    sprite = _rotate_sprite(render_sprite(text, _clamp_frac(size_frac) * short, style,
                                          float(grain_amt) * STAMP_GRAIN_K,
                                          color, glow, spread), rot)
    mf = MARGIN_FRAC if margin_frac is None else float(margin_frac)
    # 글로우 여유가 커진 만큼 마진을 빼서 **글자 위치를 고정**한다(bleed_px 주석 참조).
    # 반올림을 마진에만 적용하고 상쇄는 정수 그대로 — 연속값으로 빼면 1px 진동이 생긴다.
    margin_px = int(round(mf * short)) - bleed_px(_clamp_frac(size_frac) * short, spread)
    x0, y0, sp = _placement(sprite, W, H, margin_px, corner_for_rot(rot))
    sh, sw, _ = sp.shape
    col = sp[..., :3]
    a = np.clip(sp[..., 3:4] * STAMP_STRENGTH, 0.0, 1.0)     # (h,w,1)
    region = out[y0:y0 + sh, x0:x0 + sw, :].astype(np.float32) / mx
    # screen(가산, LED 빛이 필름을 노광)과 source-over 를 혼합: 순수 screen 은 밝은 하이라이트
    # 에서 과하게 사라지므로 SCREEN_MIX 로 source-over 를 일부 섞어 완화(어두운 곳은 거의 동일).
    over = region * (1.0 - a) + col * a                      # source-over
    screen = 1.0 - (1.0 - region) * (1.0 - col * a)          # screen
    region = over * (1.0 - SCREEN_MIX) + screen * SCREEN_MIX
    out[y0:y0 + sh, x0:x0 + sw, :] = np.rint(np.clip(region, 0.0, 1.0) * mx).astype(out.dtype)
    return out


def sprite_layer(text, ref_short=PREVIEW_REF_SHORT, rot=0, style=DEFAULT_STYLE, size_frac=DEFAULT_SIZE_FRAC,
                 grain_amt=0.0, color=DEFAULT_COLOR, glow=DEFAULT_GLOW, spread=DEFAULT_SPREAD):
    """프리뷰 오버레이용 '타이트' 날짜 스프라이트(글로우 패딩 포함) → (QImage, wRatio, hRatio).
    rot=촬영 방향(센서→업라이트 CW 회전)으로 스프라이트를 미리 회전(export 와 동일 픽셀).
    style=폰트 방식(STYLES 키), size_frac=숫자높이/짧은변 비율 — export(stamp_export)와 동일 인자.
    wRatio/hRatio = (회전 후) 스프라이트 (W,H) / 짧은 변. QML 이 cropClip 짧은 변에 이 비율을 곱해
    Image 크기를, controller.stampCorner 코너에 MARGIN_FRAC 마진으로 배치하면 export(stamp_export,
    동일 TEXT_FRAC/MARGIN_FRAC·회전·코너)와 같은 위치/상대크기·source-over 합성이 된다(프리뷰=export)."""
    sp = _rotate_sprite(render_sprite(text, _clamp_frac(size_frac) * ref_short, style,
                                      float(grain_amt) * STAMP_GRAIN_K,
                                      color, glow, spread), rot)   # (H,W,4) float
    sh, sw, _ = sp.shape
    u8 = np.empty((sh, sw, 4), np.uint8)              # ARGB32(LE)=B,G,R,A
    u8[..., 0] = np.clip(sp[..., 2], 0, 1) * 255      # B
    u8[..., 1] = np.clip(sp[..., 1], 0, 1) * 255      # G
    u8[..., 2] = np.clip(sp[..., 0], 0, 1) * 255      # R
    u8[..., 3] = np.clip(sp[..., 3], 0, 1) * 255      # A
    u8 = np.ascontiguousarray(u8)
    img = QImage(u8.data, sw, sh, 4 * sw, QImage.Format.Format_ARGB32).copy()
    return img, sw / float(ref_short), sh / float(ref_short)


def stamp_text_from_date(date_str):
    """exif_info 의 'YYYY-MM-DD HH:MM:SS' → 클래식 "'YY MM DD" (예: '24 05 12).
    파싱 실패 시 빈 문자열."""
    if not date_str:
        return ""
    try:
        d = date_str.split()[0]                # YYYY-MM-DD
        y, m, day = d.split("-")[:3]
        return f"'{y[-2:]} {int(m):02d} {int(day):02d}"
    except Exception:
        return ""
