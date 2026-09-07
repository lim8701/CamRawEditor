"""RAW 에디터 최소 동작 스켈레톤.

  RAW 디코딩(rawpy/LibRaw) -> 프록시 QImage -> QML ShaderEffect(GPU) 파이프라인.
  프래그먼트 셰이더는 시작 시 번들 qsb 로 자동 컴파일한다(ensure_shader).

사용:
  pip install -r requirements.txt
  python main.py [선택: 열어둘 RAW 경로]
"""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (Property, QBuffer, QEvent, QFileSystemWatcher, QObject,
                            QPointF, QSettings, QSize, Qt, QTimer, Signal, Slot, QUrl)
from PySide6.QtGui import (QColor, QDesktopServices, QGuiApplication, QIcon, QImage,
                           QImageReader, QTransform)
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider, QQuickItem

from decode_lock import QT_IMG_LOCK   # Qt 디코드/인코드 직렬화(교착 방지 — 모듈 주석 참조)

# ⚠️ numpy/scipy/rawpy 등을 끌어오는 무거운 모듈(date_stamp, make_luts, exif_info, wb,
#    lut, raw_loader)은 여기서 임포트하지 않는다. 최상단에 두면 QGuiApplication/splash 가
#    뜨기 전에 전부 로드돼 '아무 동작 없는' 대기 구간이 길어진다. main() 에서 splash 를
#    띄운 *직후* _load_heavy_modules() 로 로드한다(체감 시작 시간 단축).
image_loader = None   # 지연 로드되는 일반 이미지(display-referred) 어댑터 — 위 규약대로

def app_base() -> Path:
    """번들 자산(qml/shaders/luts/fonts)이 위치한 디렉터리.

    - PyInstaller onedir: 자산이 sys._MEIPASS 아래로 해제됨
    - Nuitka standalone(pyside6-deploy): 자산이 exe 옆에 위치
    - dev(비-frozen): 소스 디렉터리(기존과 동일)
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)                          # PyInstaller
        return Path(sys.executable).resolve().parent      # Nuitka standalone
    return Path(__file__).resolve().parent


BASE = app_base()
SHADERS_DIR = BASE / "shaders"
SHADER_NAMES = ["adjust.frag", "blur.frag", "convert.frag", "displaycm.frag", "mistfield.frag",
                "stamp.frag"]
LUTS_DIR = BASE / "luts"
APP_VERSION = "1.11.2"   # SemVer(MAJOR.MINOR.PATCH). 올릴 때 packaging/version_info.txt(exe 버전 리소스)도 수동으로 맞출 것
# export 파일에 남기는 현상 크레딧(JPEG=EXIF Software 태그 / PNG=tEXt).
# ⚠️`pipeline` 이 `main.APP_VERSION` 을 직접 읽으면 순환 임포트라 **호출측이 넘긴다** —
#   export 경로를 새로 만들면 이 값을 함께 넘길 것(빠뜨리면 그 파일만 크레딧이 없다).
EXPORT_SOFTWARE = f"Film Rawstery v{APP_VERSION}"


def _feature_flags() -> dict:
    """개인용 기능 플래그: .env 파일의 KEY=VALUE (한 줄씩, '#' 주석 허용).

    위치: dev=소스 폴더, frozen=exe 옆 폴더(_MEIPASS 아님 — 사용자가 릴리즈 빌드에서도
    .env 를 exe 옆에 두면 켤 수 있게). 배포 spec 은 .env 를 번들하지 않으므로 릴리즈
    기본값은 '파일 없음'=모든 플래그 꺼짐. OS 환경변수 FILMRAWSTERY_<KEY> 가 파일보다 우선."""
    flags: dict[str, str] = {}
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    try:
        p = base / ".env"
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                flags[k.strip()] = v.strip()
    except Exception:
        pass                            # 플래그 파일 문제는 조용히 무시(기능 숨김이 기본)
    for k, v in os.environ.items():
        if k.startswith("FILMRAWSTERY_"):
            flags[k[len("FILMRAWSTERY_"):]] = v
    return flags


def _flag_on(flags: dict, key: str) -> bool:
    return flags.get(key, "").strip().lower() in ("1", "true", "on", "yes")


FEATURE_FLAGS = _feature_flags()
# Wallpaper 패널(3분할 트립틱 합성): 개인용 — 릴리즈에선 .env 부재로 자동 숨김
WALLPAPER_PANEL = _flag_on(FEATURE_FLAGS, "WALLPAPER_PANEL")
# Photo map(탐색기 🗺, 폴더 좌표를 지도 위 썸네일로): 개인용 — 같은 이유로 릴리즈에서 자동 숨김.
# ★숨긴 이유는 기능이 미완이라서가 아니라 **타일이 HiDPI 에서 흐리기 때문**이다(실측: 250%
#   배율에서 256px 타일이 640 device px 로 2.5배 확대. osm.org 표준 레이어에는 @2x 판이 없어
#   `@2x.png` 는 HTTP 400). 키를 쓰는 제공자로 갈아 끼우는 것이 답인데 Qt OSM 플러그인의
#   `custom.host` 템플릿이 `%z/%x/%y.png` 로 **고정**이라 키(쿼리스트링)도 `@2x` 도 실을 수
#   없다 — 우회로와 재개 조건은 `docs/photo_map.md` 의 '왜 숨겨 두는가'.
PHOTO_MAP = _flag_on(FEATURE_FLAGS, "PHOTO_MAP")

# ---------- 시스템 슬립 방지 (Windows SetThreadExecutionState / macOS IOKit 어서션) ----------
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002

_mac_sleep_assertion = None      # IOPMAssertionID — 홀드 중일 때만 not None


def _mac_keep_awake(on: bool) -> None:
    """macOS 유휴 시스템 슬립 방지(IOKit 전원 어서션).

    ⚠️**디스플레이는 붙잡지 않는다** — Windows 와 다른 판단이다. 거기서 화면까지 붙잡는 것은
    Modern Standby 가 '화면 꺼짐 = 대기 진입' 이라 어쩔 수 없어서인데, macOS 는 화면이 꺼져도
    프로세스가 계속 돌아 export 가 멈추지 않는다. 그래서 필요한 최소인 시스템 슬립만 막는다.
    ⚠️`caffeinate` 자식 프로세스 대신 어서션을 쓴다 — 어서션은 **프로세스에 귀속**돼 앱이
    강제 종료돼도 커널이 회수하지만, 자식 프로세스는 살아남아 절전을 영영 막을 수 있다.
    ⚠️뚜껑을 닫으면(clamshell) 어서션과 무관하게 잔다 — 막을 방법이 없다."""
    global _mac_sleep_assertion
    import ctypes
    iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    if not on:
        if _mac_sleep_assertion is None:
            return
        iokit.IOPMAssertionRelease(ctypes.c_uint32(_mac_sleep_assertion))
        _mac_sleep_assertion = None
        return
    if _mac_sleep_assertion is not None:
        return                       # 이미 홀드 중 — 어서션이 쌓이면 해제가 짝이 안 맞는다
    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    iokit.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    _UTF8 = 0x08000100                                   # kCFStringEncodingUTF8
    kind = cf.CFStringCreateWithCString(None, b"PreventUserIdleSystemSleep", _UTF8)
    name = cf.CFStringCreateWithCString(None, b"FilmRawstery export", _UTF8)  # pmset -g assertions
    aid = ctypes.c_uint32(0)
    rc = iokit.IOPMAssertionCreateWithName(kind, 255, name, ctypes.byref(aid))  # 255 = Level On
    cf.CFRelease(kind)
    cf.CFRelease(name)
    if rc == 0:                                          # kIOReturnSuccess
        _mac_sleep_assertion = aid.value


def _set_keep_awake(on: bool) -> None:
    """export 류 긴 작업 동안 시스템 슬립 방지(Windows/macOS).
    ⚠️**ES_DISPLAY_REQUIRED 가 반드시 함께 있어야 한다** — 요즘 PC 는 대부분
    **Modern Standby(S0 저전력 대기)** 이고(`powercfg /a` 에 'Standby (S0 Low Power Idle)'
    가 보이면 해당), 그 환경에서 ES_SYSTEM_REQUIRED 는 문서상 **무효**다
    (PowerRequestSystemRequired 도 동일). Modern Standby 는 '화면 꺼짐 = 대기 진입' 이라
    화면을 붙잡는 것 말고는 막을 방법이 없다 — 실제로 SYSTEM_REQUIRED 만 걸었을 때 긴
    export 가 디스플레이 타임아웃(기본 10분) 뒤 대기로 들어가며 멈췄다(사용자 보고).
    대가로 export 중에는 화면이 안 꺼진다(끝나면 해제되어 원래 전원 정책으로 복귀).
    ⚠️ES_CONTINUOUS 상태는 '호출한 스레드'에 귀속(스레드 종료 시 자동 해제)이라 반드시
    메인 스레드에서만 호출할 것 — 워커에서는 Controller._keepAwakeSig 로 큐잉.
    macOS 는 IOKit 어서션(_mac_keep_awake) — 스레드가 아니라 프로세스 귀속이지만 호출 규약은
    같게 둔다(홀드/해제가 짝을 이뤄야 하므로)."""
    if sys.platform == "darwin":
        try:
            _mac_keep_awake(on)
        except Exception:
            pass                        # 실패해도 기능 자체는 무영향(슬립만 못 막음)
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes
        state = _ES_CONTINUOUS
        if on:
            state |= _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(state)
    except Exception:
        pass                            # 실패해도 기능 자체는 무영향(슬립만 못 막음)

def wallpaper_prefs_path() -> str:
    """배경화면 패널 설정(잡지 텍스트·슬롯·옵션) 저장 파일 — OS 공통 사용자 데이터 폴더.
    앱 폴더가 아니라 여기 두는 이유는 models 와 동일(설치 폴더 무쓰기·업데이트에도 보존).
    app_dirs 는 파일 상단 규약대로 지연 임포트."""
    import app_dirs
    return app_dirs.user_data_path("wallpaper.json")

# ---------- 앱 설정(prefs.json) — 크로스 플랫폼 단일 저장소 ----------
# ⚠️레지스트리(QSettings)는 쓰지 않는다. Windows 전용이고 백업·이전·삭제가 어려워서,
#   배경화면 설정에 이어 나머지 앱 설정도 **OS 공통 사용자 데이터 폴더의 JSON** 으로 옮겼다
#   (mac/Linux 에서 같은 코드가 그대로 동작한다). 구버전 레지스트리 값은 1회 이관 후 삭제한다.
#   Controller 밖(`main()` 의 시작 폴더 복원)에서도 읽으므로 모듈 레벨에 둔다.
_PREFS_GROUPS = ("export", "explorer")     # 이관 대상 그룹(구 QSettings 그룹명과 동일)
_prefs_cache = None


def app_prefs_path() -> str:
    import app_dirs
    return app_dirs.user_data_path("prefs.json")


def _migrate_registry_prefs() -> dict:
    """구버전 레지스트리 값 1회 이관 후 그 그룹을 제거. 없으면 빈 dict.
    (wallpaper 그룹은 _migrate_wall_prefs_from_registry 가 따로 담당한다.)"""
    data = {}
    try:
        # 모듈 상단의 QSettings 를 쓴다(함수 안 재임포트 금지 — 테스트가 이 이름을 교체한다)
        st = QSettings("FilmRawstery", "FilmRawstery")
        for g in _PREFS_GROUPS:
            st.beginGroup(g)
            grp = {}
            for k in st.childKeys():
                v = st.value(k, "")
                if v not in (None, ""):
                    grp[str(k)] = v
            st.endGroup()
            if grp:
                data[g] = grp
        if data:
            _atomic_write_json(app_prefs_path(), data)
            for g in _PREFS_GROUPS:
                st.remove(g)              # 레지스트리에는 남기지 않는다
            st.sync()
            n = sum(len(v) for v in data.values())
            print(f"[prefs] 설정 {n}개를 {app_prefs_path()} 로 이관(레지스트리에서 제거)")
    except Exception as exc:
        print(f"[prefs] 레지스트리 이관 실패(무시): {exc}")
        data = {}
    return data


def load_prefs() -> dict:
    global _prefs_cache
    if _prefs_cache is None:
        data = {}
        try:
            f = Path(app_prefs_path())
            if f.is_file():
                with open(f, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            data = {}                     # 손상 시 기본값으로 시작(다음 저장에 덮어씀)
        if not data:
            data = _migrate_registry_prefs()
        _prefs_cache = data
    return _prefs_cache


def pref_get(group: str, key: str, default=None):
    return load_prefs().get(group, {}).get(key, default)


def pref_set(group: str, key: str, value) -> None:
    """값 하나 저장(원자적 쓰기). 값이 같으면 디스크를 건드리지 않는다."""
    d = load_prefs()
    grp = d.setdefault(group, {})
    if grp.get(key) == value:
        return
    grp[key] = value
    try:
        _atomic_write_json(app_prefs_path(), d)
    except Exception as exc:
        print(f"[prefs] 저장 실패: {exc}")


def stamp_prefs_path() -> str:
    """날짜 스탬프 '내 기본값'(폰트·크기·여백·켜짐) 저장 파일 — 사용자 데이터 폴더.
    사진 여러 장을 연속 작업할 때 매번 다시 설정하지 않도록 마지막 사용값을 남긴다
    (피드백 발단). ⚠️사진별 값은 여전히 사이드카가 진실원이고, 이 파일은 **사이드카가
    없는 새 사진의 출발점**일 뿐이다."""
    import app_dirs
    return app_dirs.user_data_path("stamp.json")


# 업데이트 확인: GitHub 릴리스 목록(공개 repo, 무인증 60회/시간 — 시작 시 1회면 충분)
_RELEASES_API = "https://api.github.com/repos/lim8701/FilmRawstery/releases"

# 필름 시뮬레이션 카탈로그 (key, 표시명, 그룹). 실제 luts/<key>.cube 가 있는 것만 UI 에 노출
# (identity=None 은 LUT 미적용이라 항상 포함). 흑백 등은 .cube 를 넣으면 자동으로 다시 나타남.
# ⚠️여기 없는 파일명은 UI 에 절대 안 뜬다 — 사용자가 임의 이름으로 넣는 .cube 는 카탈로그가
#   아니라 `lut.user_lut_keys()`(=`user:` 접두사, 사용자 데이터 폴더)를 통해 합류한다.
FILM_SIM_CATALOG = [
    ("identity", "None", 0),
    ("provia", "Provia / Standard", 1), ("velvia", "Velvia", 1), ("astia", "Astia", 1),
    ("classic_chrome", "Classic Chrome", 2), ("classic_neg", "Classic Negative", 2),
    ("nostalgic_neg", "Nostalgic Neg", 2), ("pro_neg_hi", "PRO Neg. Hi", 2),
    ("pro_neg_std", "PRO Neg. Std", 2),
    ("eterna", "Eterna", 3), ("reala_ace", "Reala Ace", 3), ("bleach_bypass", "Bleach Bypass", 3),
    ("acros", "ACROS", 4), ("acros_ye", "ACROS + Ye", 4), ("acros_r", "ACROS + R", 4),
    ("acros_g", "ACROS + G", 4), ("monochrome", "Monochrome", 4), ("sepia", "Sepia", 4),
]


USER_SIM_GROUP = 5   # 'My LUTs' — 사용자가 추가한 .cube (콤보에서 번들 뒤, 구분선 자동)

# LutProvider 인스턴스(main() 에서 채운다). 목록을 '파싱 성공한 것'으로 좁히고 키별 N 을
# 돌려주기 위해 모듈 전역으로 둔다 — Controller 가 LUT 프로바이더를 들고 있지 않기 때문.
LUT_PROVIDER = None


def available_film_sims():
    """UI 에 노출할 필름시뮬 목록 [{key,label,group}]. identity(None)는 항상 포함.

    ① 번들: 카탈로그 중 `luts/<key>.cube` 가 실제 있는 것
    ② 사용자: `user:<파일명>` (사용자 데이터 폴더의 .cube) → group 5 'My LUTs'

    ⚠️존재만 보지 않고 **아틀라스까지 구워진 것**으로 좁힌다. 예전엔 `.exists()` 만 봤는데,
      그러면 파싱 실패한 .cube 가 콤보에 뜨고 프리뷰는 빈 텍스처, CPU export 는 예외가 된다
      (번들은 전부 정상이라 잠재적이었지만 사용자 LUT 에서는 흔해진다)."""
    import lut as lut_mod
    ok = LUT_PROVIDER.keys() if LUT_PROVIDER is not None else None
    out = []
    for key, label, group in FILM_SIM_CATALOG:
        for_bundle = (key in ok) if ok is not None else (LUTS_DIR / f"{key}.cube").exists()
        if key == "identity" or for_bundle:
            out.append({"key": key, "label": label, "group": group})
    for key in lut_mod.user_lut_keys():
        if ok is not None and key not in ok:
            continue
        label = key[len(lut_mod.USER_PREFIX):]
        if label.lower().endswith(".cube"):
            label = label[:-5]
        out.append({"key": key, "label": label, "group": USER_SIM_GROUP})
    return out

# 사이드카(폴더당 데이터) 파일/폴더 이름. 구 이름(.camraw*)은 폴더 접근 시 1회 자동 마이그레이션.
EDITS_DIR_NAME = ".filmrawsteryedits"
LIKES_FILE_NAME = ".filmrawsterylikes.json"
CAPTIONS_FILE_NAME = ".filmrawsterycaptions.json"
_OLD_SIDECARS = [(".camrawedits", EDITS_DIR_NAME), (".camrawlikes.json", LIKES_FILE_NAME)]

# 얼굴 썸네일 슬롯 수. face_seg.MAX_FACES 와 같아야 하지만 face_seg 는 지연 import(무거움)라
# 프로바이더 생성 시점에 못 읽는다 — 값이 갈리면 초과분 썸네일이 조용히 버려진다.
MAX_FACE_SLOTS = 5


def _atomic_write_json(path, data) -> None:
    """사이드카 JSON 원자적 쓰기(tmp→os.replace). open("w") 직접 쓰기는 truncate 후
    크래시/전원단절 시 파일이 통째로 비어버리고, 로더가 조용히 빈 값으로 폴백해
    폴더 전체의 likes/캡션(또는 그 파일의 편집)이 소실된다 — 모델 다운로드와 동일한
    tmp→rename 패턴으로 방지."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _migrate_sidecars(folder: str) -> None:
    """구 사이드카 이름(.camraw*)을 신 이름(.filmrawstery*)으로 1회 이동(신 이름이 없을 때만).
    이미 신 이름이 있거나 구 이름이 없으면 아무 것도 안 함(멱등)."""
    try:
        base = Path(folder)
        for old, new in _OLD_SIDECARS:
            op, npath = base / old, base / new
            if op.exists() and not npath.exists():
                op.rename(npath)
    except Exception:
        pass

# GPU export grab 의 허용 여유분(px). QML 이 요청 크기를 DPR 로 나눌 때 홀수 치수에서
# 축당 최대 DPR-1 px 이 더 온다 — 이만큼은 잘라내고, 그 이상이면 재샘플 폴백(_finish_gpu_export).
_GRAB_SLACK_PX = 4

# 시작 시 자동으로 열어볼 샘플 RAF (명령줄 인자가 없을 때 사용)
DEFAULT_RAF = r"C:\Pic\x100v\128_FUJI\DSCF8035.RAF"
# DEFAULT_RAF = r"C:\Pic\x100v\131_FUJI\DSCF1039.RAF"  # 임시 비활성

# 탐색기에 노출/디코딩할 RAW 확장자(rawpy/LibRaw 가 현상). 후지 RAF 외 타 제조사 포함 —
# 색 매트릭스/WB/블랙·화이트레벨을 파일 메타에서 읽으므로 기종 등록 없이 동작한다.
# 목록은 넓게 두고, LibRaw 가 실제로 못 여는 파일/기종은 디코드 시 예외 → UI 에 '미지원 RAW'
# 안내로 처리한다(_render_worker → loadError). 샘플 검증필: raf/cr2/cr3/crw/nef/arw/srw/dng/
# orf/rw2/pef/rwl/dcr. 나머지(nrw/sr2/srf/3fr/iiq/mrw/kdc/erf)는 LibRaw 지원 포맷이나 미검증.
RAW_EXTS = {
    ".raf",                        # Fujifilm
    ".cr2", ".cr3", ".crw",        # Canon
    ".nef", ".nrw",                # Nikon
    ".arw", ".sr2", ".srf",        # Sony
    ".srw",                        # Samsung
    ".dng",                        # Adobe / generic (Leica·폰·드론 DNG 포함)
    ".orf",                        # Olympus / OM System
    ".rw2",                        # Panasonic
    ".pef",                        # Pentax
    ".rwl",                        # Leica
    ".3fr",                        # Hasselblad
    ".iiq",                        # Phase One
    ".mrw",                        # Minolta
    ".kdc", ".dcr",                # Kodak
    ".erf",                        # Epson
}


# Export 로 저장 가능한 확장자(FileDialog name filter 와 1:1). 저장 포맷은 pipeline.save_image 가
# **파일명 확장자**로 결정하므로, 이 목록이 곧 유효한 형식 목록이다.
# jpeg/tiff 도 받는다 — 사용자가 이름을 직접 타이핑하는 경우가 있고 Qt·save_image 가
# 둘 다 처리한다(JPEG_EXTS 참조). 대화상자 필터는 png/jpg/tif 3종만 노출.
_EXPORT_EXTS = ("png", "jpg", "jpeg", "tif", "tiff")


def _gps_for_file(path: str, edits: dict):
    """사진 한 장의 위치 -> `(lat, lon, alt|None)` 또는 None.

    ★**좌표 우선순위 규칙의 단일 진실원**이다 — `Controller._load`(사진을 열 때)와
    `_map_scan_worker`(Photo map 이 폴더를 훑을 때)가 **둘 다 이것을 부른다.** 규칙을 두 곳에
    복사해 두면 반드시 갈라진다(지도가 패널과 다른 좌표를 보여주는 형태로 드러난다).

    ⚠️**사이드카에 `gpsLat` 키가 있으면(값이 `null` 이어도) 그것이 답이다.** 키를 빼는 것과
      `null` 은 다른 뜻이다 — `null` 은 "사용자가 일부러 지웠다"이므로 파일의 EXIF GPS 로
      폴백하면 지운 위치가 되살아난다(지우기가 안 먹는 것으로 보인다).
    """
    if "gpsLat" in (edits or {}):
        # ★파싱·검증(범위 ±90/±180, alt 처리)은 `Controller._gps_tuple` 이 유일하게 안다 —
        #   여기서 다시 구현하면 손상된 사이드카에서 `_load` 와 지도가 다르게 판단한다.
        return Controller._gps_tuple({"lat": edits.get("gpsLat"),
                                      "lon": edits.get("gpsLon"),
                                      "alt": edits.get("gpsAlt")})
    try:
        return exif_info.read_gps(path)      # 카메라가 남긴 좌표(대개 없다)
    except Exception:
        return None


def _pair_flags(folder: str, names: list) -> list:
    """파일명 리스트 → 탐색기 항목 + **RAW/JPEG 페어 표식**.

    카메라가 RAW+JPEG 를 동시 기록하면 같은 사진이 목록에 두 번 나온다(실측: X100V 폴더에서
    RAF 503 / JPG 497 이 **stem 기준 497쌍 정확히 일치**, JPEG 단독 0장). 같은 폴더·같은 stem 에
    RAW 가 있는 일반 이미지에 `paired` 를 달아 기본으로 접고, 짝을 가진 RAW 행에는 배지용
    `pair`("JPG")를 단다.
    ⚠️목록에서 **빼지 않고 플래그만** 단다 — QML 토글(탐색기 ⧉)이 재스캔 없이 즉시 펼칠 수 있게.
    ⚠️'RAW 만 보기' 같은 포맷 필터가 아니라 **중복 필터**다. 그래서 이미지 전용 폴더(필름 스캔·
      export 결과 등, 이 라이브러리의 절반 이상)는 접을 짝이 없어 자동으로 무영향이고,
      RAW 단독 사진도 그대로 남는다.
    """
    raw_stems = set()
    for n in names:
        stem, ext = os.path.splitext(n)
        if ext.lower() in RAW_EXTS:
            raw_stems.add(stem.lower())
    pair_exts = {}
    for n in names:
        stem, ext = os.path.splitext(n)
        if ext.lower() not in RAW_EXTS and stem.lower() in raw_stems:
            pair_exts.setdefault(stem.lower(), set()).add(ext.lstrip(".").upper())
    out = []
    for n in names:
        stem, ext = os.path.splitext(n)
        key, is_raw = stem.lower(), ext.lower() in RAW_EXTS
        it = {"name": n, "path": os.path.join(folder, n), "isDir": False}
        if not is_raw and key in raw_stems:
            it["paired"] = True                          # 짝 RAW 가 있는 일반 이미지 → 기본 접힘
        elif is_raw and key in pair_exts:
            it["pair"] = "+".join(sorted(pair_exts[key]))  # RAW 행 배지("JPG")
        out.append(it)
    return out


def _openable_exts() -> set:
    """탐색기에 나열/열기 가능한 확장자 = RAW + 일반 이미지(display-referred 어댑터).
    image_loader 는 지연 임포트(_load_heavy_modules)라 로드 전에는 RAW 만 — 실사용에서는
    폴더 스캔이 항상 그 뒤라 문제되지 않고, 방어적으로 폴백만 둔다.
    ⚠️일반 이미지가 목록에 들어오면 **우리가 내보낸 `<원본>_exported.jpg` 도 같이 보인다**
      (의도된 트레이드오프 — 원본 옆에서 결과를 바로 비교할 수 있고, 사이드카는 파일명
      기준이라 충돌하지 않는다)."""
    if image_loader is None:
        return RAW_EXTS
    return RAW_EXTS | image_loader.IMAGE_EXTS

# GPU 고성능(외장 GPU) 강제: Windows 그래픽 설정과 동일하게 이 실행파일(python.exe)의
# GPU 환경설정을 '고성능'으로 레지스트리에 기록한다. False 면 Windows 기본(보통 내장) 사용.
PREFER_HIGH_PERF_GPU = False


# 외장 GPU 어댑터 인덱스를 직접 지정하려면 정수로(예: 1). None=자동 탐지(전용 VRAM 최대).
GPU_ADAPTER_INDEX = None


def _list_d3d_adapters():
    """DXGI 로 어댑터 (index, name, dedicated_vram_bytes, vendor_id) 목록 반환. 실패 시 []."""
    import ctypes
    from ctypes import (POINTER, Structure, WINFUNCTYPE, byref, c_long, c_size_t,
                        c_ubyte, c_uint, c_ushort, c_void_p, c_wchar, wintypes)

    class GUID(Structure):
        _fields_ = [("Data1", c_uint), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]

    class LUID(Structure):
        _fields_ = [("Low", wintypes.DWORD), ("High", c_long)]

    class DESC(Structure):
        _fields_ = [("Description", c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint),
                    ("SubSysId", c_uint), ("Revision", c_uint), ("DedicatedVideoMemory", c_size_t),
                    ("DedicatedSystemMemory", c_size_t), ("SharedSystemMemory", c_size_t), ("AdapterLuid", LUID)]
    out = []
    try:
        iid = GUID(0x7b7166ec, 0x21c7, 0x44ae, (0xb2, 0x1a, 0xc9, 0xae, 0x32, 0x1a, 0xe3, 0x69))  # IDXGIFactory
        fac = c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory(byref(iid), byref(fac)) != 0:
            return []
        vt = ctypes.cast(fac, POINTER(POINTER(c_void_p))).contents
        enum_adapters = WINFUNCTYPE(c_long, c_void_p, c_uint, POINTER(c_void_p))(vt[7])  # EnumAdapters
        i = 0
        while True:
            ad = c_void_p()
            if enum_adapters(fac, i, byref(ad)) != 0:
                break
            avt = ctypes.cast(ad, POINTER(POINTER(c_void_p))).contents
            get_desc = WINFUNCTYPE(c_long, c_void_p, POINTER(DESC))(avt[8])  # GetDesc
            d = DESC()
            get_desc(ad, byref(d))
            out.append((i, d.Description, int(d.DedicatedVideoMemory), int(d.VendorId)))
            i += 1
    except Exception:
        return []
    return out


def _find_discrete_adapter_index():
    """전용 VRAM 이 가장 큰 비-소프트웨어 어댑터(=외장 GPU) 인덱스. 내장만 있으면 None."""
    ads = [a for a in _list_d3d_adapters() if a[3] != 0x1414]   # 0x1414=Microsoft Basic Render 제외
    if not ads:
        return None
    best = max(ads, key=lambda a: a[2])                          # DedicatedVideoMemory 최대
    if best[2] < 512 * 1024 * 1024:                             # <512MB 면 외장 없음(내장만)으로 판단
        return None
    return best[0]


def _prefer_high_performance_gpu() -> None:
    """외장(고성능) GPU 강제 사용. ⚠️QGuiApplication 생성 *전* 호출해야 함.

    핵심: QT_D3D_ADAPTER_INDEX 로 Qt D3D11 백엔드의 어댑터를 **직접 지정**(이번 실행부터 즉시).
    보조: Windows GPU 환경설정(UserGpuPreferences)도 '고성능' 기록(다음 실행/전원관리용).
    하이브리드 노트북에서 기본값(내장 Intel)으로 도는 것을 외장(NVIDIA/AMD)으로 전환한다.
    """
    if sys.platform != "win32":
        return
    idx = GPU_ADAPTER_INDEX if GPU_ADAPTER_INDEX is not None else _find_discrete_adapter_index()
    if idx is not None:
        os.environ.setdefault("QSG_RHI_BACKEND", "d3d11")   # QT_D3D_ADAPTER_INDEX 는 D3D11 전용
        os.environ["QT_D3D_ADAPTER_INDEX"] = str(idx)
        names = {a[0]: a[1] for a in _list_d3d_adapters()}
        print(f"[gpu] 외장 GPU 강제: adapter[{idx}] {names.get(idx, '?')}")
    else:
        print("[gpu] 외장 GPU 미발견 -> 기본 어댑터 사용")
    # 보조: Windows 고성능 GPU 환경설정(실패 무시)
    try:
        import winreg
        exe = sys.executable
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\DirectX\UserGpuPreferences",
                                0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur, _ = winreg.QueryValueEx(k, exe)
            except FileNotFoundError:
                cur = None
            if cur != "GpuPreference=2;":
                winreg.SetValueEx(k, exe, 0, winreg.REG_SZ, "GpuPreference=2;")
    except Exception:
        pass


def _find_qsb():
    """셰이더 컴파일러(qsb) 경로. PySide6 번들 qsb 우선 — venv 폴더 rename 에도 안전
    (console-script 래퍼 pyside6-qsb 는 절대경로가 박혀 폴더 이동 시 깨질 수 있음)."""
    try:
        import PySide6
        exe = "qsb.exe" if sys.platform == "win32" else "qsb"
        cand = Path(PySide6.__file__).resolve().parent / exe
        if cand.exists():
            return str(cand)
    except Exception:
        pass
    return shutil.which("pyside6-qsb") or shutil.which("qsb")


def ensure_shader() -> None:
    """frag 셰이더들을 .qsb 로 컴파일 (이미 최신이면 건너뜀)."""
    if getattr(sys, "frozen", False):
        return  # frozen: 미리 컴파일된 .qsb 동봉, qsb.exe 미번들 + 설치 폴더 무쓰기
    qsb = None
    for name in SHADER_NAMES:
        src = SHADERS_DIR / name
        out = SHADERS_DIR / (name + ".qsb")
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            continue
        if qsb is None:
            qsb = _find_qsb()
            if not qsb:
                raise RuntimeError("qsb(PySide6 셰이더 컴파일러)를 찾을 수 없습니다.")
        subprocess.run(
            [qsb, "--glsl", "120,150,300es", "--hlsl", "50", "--msl", "12",
             "-o", str(out), str(src)],
            check=True,
        )
        print(f"[shader] compiled -> {out.name}")


class RawProvider(QQuickImageProvider):
    """디코딩한 QImage 를 QML 'image://raw/...' 로 제공."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage()

    def set_image(self, img: QImage) -> None:
        self._img = img

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._img


class RawPeekProvider(QQuickImageProvider):
    """RAW Peek(디모자이크 이전 센서 뷰)의 그림들을 'image://rawpeek/<kind>?v=N' 로 제공.

    kind = main(모자이크/디모자이크/경계) · pattern · hist. `SkyMaskProvider` 와 동형이고,
    오버레이를 닫으면 clear() 로 참조를 버린다(26MP 기준 수십 MB)."""

    KINDS = ("main", "pattern", "hist", "mini", "develop", "developgray")

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._imgs = {}

    def set_image(self, kind: str, img: QImage) -> None:
        self._imgs[kind] = img

    def clear(self) -> None:
        self._imgs = {}

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        im = self._imgs.get(image_id.split("?", 1)[0])
        if im is None:                       # 아직 없음 → 유효한 1x1(깨진 이미지 아이콘 방지)
            im = QImage(1, 1, QImage.Format.Format_RGB888)
            im.fill(0x1a1a1c)
        return im


class RawFullProvider(QQuickImageProvider):
    """GPU export 용 풀해상도 16bit(RGBA64) 헤드룸 인코딩 이미지를 'image://rawfull/...' 로 제공.

    export(GPU) 시에만 set_image 로 채워지고, 끝나면 clear()로 메모리 해제."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage()

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._img


class NrFullProvider(QQuickImageProvider):
    """GPU export 용 **노이즈 항 텍스처**(출력 해상도 RGBA64)를 'image://nrfull/...' 로 제공.

    셰이더 `nrNoise=1` 분기가 읽는다: 화소값 = t·0.5+0.5, t = chromaDetail + noiseL
    (`pipeline.nr_terms` — CPU export 와 같은 함수). 프록시 `nrbase` 와 **다른 텍스처**이고
    (프록시를 풀해상도 소스에 쓰면 NR 이 성립하지 않는다 — 셰이더 nrNoise 주석) export 시에만
    채워지고 끝나면 clear() 로 해제한다(26MP 기준 208MB)."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage()

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._img


class LutProvider(QQuickImageProvider):
    """필름 시뮬레이션 LUT 아틀라스를 'image://lut/<key>' 로 제공.

    key 는 번들 `luts/<key>.cube` 의 파일명(확장자 제외), 또는 사용자 LUT 의 `user:<파일명>`.

    ★⚠️**LUT 마다 N 이 다를 수 있다.** 아틀라스 좌표가 N 에 의존하므로(`lut.py` 규약) 셰이더
      uniform 은 **키별 N**(`size_of`)으로 줘야 한다. 예전엔 전역 하나(`self.size`, 마지막 로드
      승)였는데 번들이 전부 N=32 라 잠재적이었을 뿐이다 — N 이 다른 LUT 이 섞이면 프리뷰·GPU
      export 만 색이 깨지고 CPU export(파일에서 자기 N 을 다시 읽는다)는 멀쩡한, 가장 찾기
      어려운 형태의 불일치가 된다.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._atlases: dict[str, QImage] = {}
        self._sizes: dict[str, int] = {}
        self.size = 0  # 번들 LUT 의 N (알 수 없는 키의 폴백용)

    def load_dir(self, luts_dir: Path, prefix: str = "") -> None:
        """폴더의 .cube 를 전부 굽는다. `prefix` 를 주면 키가 `<prefix><파일명>`(사용자 LUT).
        폴더가 없으면 glob 이 빈 결과라 그냥 넘어간다(사용자 폴더는 없을 수 있다)."""
        loaded = 0
        for cube in sorted(Path(luts_dir).glob("*.cube")):
            key = (prefix + cube.name) if prefix else cube.stem
            if self.load_one(cube, key):
                loaded += 1
                if not prefix:
                    self.size = self._sizes[key]
        # 번들/사용자 두 번 불리므로 어느 쪽인지, 그 폴더에서 몇 개인지 찍는다
        # (예전엔 같은 줄이 두 번 나왔고, 그다음엔 누적 개수가 나와 헷갈렸다).
        print(f"[lut] {'사용자' if prefix else '번들'} {loaded}개 로드, 번들 N={self.size}")

    def load_one(self, path, key: str) -> bool:
        """.cube 하나를 아틀라스로 굽는다. 실패는 스킵+경고(그 룩만 미로드, 나머지는 정상) —
        손상/헤더누락/1D 파일 하나가 앱 시작이나 목록을 통째로 막지 않게 한다."""
        import lut as lut_mod
        try:
            arr, n = load_cube(str(path))
        except Exception as exc:
            print(f"[lut] ⚠️로드 실패로 스킵: {Path(path).name} ({exc})")
            return False
        # ⚠️가져오기(add_user_lut)는 MAX_N 초과를 리샘플하지만, 사용자가 폴더에 **직접 넣은**
        #   파일은 그 경로를 안 탄다. 그대로 구우면 아틀라스 폭이 N²px 라 GPU 업로드가 실패해
        #   프리뷰는 빈 LUT, CPU export 는 파일을 다시 읽어 정상 적용 → 프리뷰≠export 가 된다.
        #   목록에 올리지 않으면 선택 자체가 불가능해져 두 경로가 함께 '미적용'으로 맞는다.
        if n > lut_mod.MAX_N:
            print(f"[lut] ⚠️N={n} 은 상한 {lut_mod.MAX_N} 초과(아틀라스 폭 {n * n}px) — 스킵: "
                  f"{Path(path).name}. 앱의 Add LUT… 으로 가져오면 리샘플된다.")
            return False
        self._atlases[key] = atlas_qimage(arr, n)
        self._sizes[key] = n
        return True

    def drop_one(self, key: str) -> None:
        self._atlases.pop(key, None)
        self._sizes.pop(key, None)

    def keys(self):
        """아틀라스가 실제로 구워진 키 집합(=UI 에 내보내도 안전한 것)."""
        return set(self._atlases)

    def size_of(self, key: str) -> int:
        """그 키의 한 변 N. 모르는 키는 번들 N 으로 폴백 — 짝이 되는 텍스처가 빈 이미지라
        화면이 깨지지 않고 '적용 안 됨'으로 보인다."""
        return self._sizes.get(key, self.size)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        # ⚠️Qt 는 URL 경로의 `%` 를 `%25` 로 **인코딩한 채** 넘긴다(실측: 'user:100% pro.cube'
        #   -> 'user:100%25 pro.cube'). 콜론·공백은 그대로 온다. 사용자가 폴더에 직접 넣은
        #   파일명에 `%` 가 있을 수 있으므로 되살린다 — `%` 없는 키에는 무동작이다.
        from urllib.parse import unquote
        key = unquote(image_id.split("?", 1)[0])  # 쿼리스트링 제거 + 퍼센트 디코드
        return self._atlases.get(key, QImage())


class DisplayCmProvider(QQuickImageProvider):
    """디스플레이 색관리 LUT 아틀라스를 'image://displaycm/...' 로 제공(프리뷰 전용).

    현재 모니터 ICC 에서 구운 sRGB→디스플레이 3D LUT(아틀라스). 색관리 불필요(sRGB
    모니터/프로파일 없음)면 1x1 더미를 두고 size=0 → 셰이더가 미적용."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._atlas = QImage(1, 1, QImage.Format.Format_RGB888)
        self.size = 0  # LUT 한 변 N (0=항등/미적용)

    def set_atlas(self, atlas: QImage, n: int) -> None:
        if atlas is None or n <= 1:
            self._atlas = QImage(1, 1, QImage.Format.Format_RGB888)
            self.size = 0
        else:
            self._atlas = atlas
            self.size = n

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._atlas


class CurveProvider(QQuickImageProvider):
    """톤 커브 1D LUT(256x1 RGB)를 'image://curve/...' 로 제공.

    R/G/B 열에 채널별 합성 커브(마스터→채널 적용)를 담는다. 셰이더가 입력 채널값으로
    해당 채널(.r/.g/.b)을 샘플링해 마스터+채널 톤커브를 합성 적용한다.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        import numpy as np
        ident = np.linspace(0.0, 1.0, 256, dtype=np.float32)
        self._img = self._make(np.stack([ident, ident, ident], axis=1))  # identity

    @staticmethod
    def _make(combined) -> QImage:
        import numpy as np
        v = np.clip(np.rint(np.asarray(combined, float) * 255.0), 0, 255).astype(np.uint8)
        if v.shape != (256, 3):
            ident = np.linspace(0, 255, 256).astype(np.uint8)
            v = np.stack([ident, ident, ident], axis=1)
        arr = np.ascontiguousarray(v.reshape(1, 256, 3))
        return QImage(arr.data, 256, 1, 256 * 3, QImage.Format.Format_RGB888).copy()

    def set_lut(self, combined) -> None:
        self._img = self._make(combined)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class StampProvider(QQuickImageProvider):
    """날짜 스탬프 오버레이(프록시 크기 RGBA)를 'image://stamp/...' 로 제공."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_ARGB32)
        self._img.fill(0)            # 시작 시에도 유효한 투명 텍스처

    def set_image(self, img: QImage) -> None:
        self._img = img

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class SkyMaskProvider(QQuickImageProvider):
    """로컬 마스크 레이어(최대 3, 프록시 크기 Grayscale8)를 'image://skymask/<layer>?v=N' 로 제공."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._imgs = []
        for _ in range(5):
            im = QImage(1, 1, QImage.Format.Format_Grayscale8)
            im.fill(0)               # 시작 시에도 유효한 검정(마스크 없음) 텍스처
            self._imgs.append(im)

    def set_image(self, layer: int, img: QImage) -> None:
        if 0 <= layer < len(self._imgs):
            self._imgs[layer] = img

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        key = image_id.split("?", 1)[0]      # "<layer>" (쿼리스트링 제거)
        try:
            i = int(key)
        except ValueError:
            i = 0
        return self._imgs[i] if 0 <= i < len(self._imgs) else self._imgs[0]


class FaceThumbProvider(QQuickImageProvider):
    """검출된 얼굴 썸네일을 'image://facethumb/<i>?v=N' 로 제공(SkyMaskProvider 와 동형).

    Masking 패널의 얼굴 선택 타일용. 이미지가 바뀔 때마다 통째로 교체되므로 QML 쪽은
    cache: false 필수(?v= 만으로는 Qt 가 옛 텍스처를 재사용할 수 있음)."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._imgs = [None] * MAX_FACE_SLOTS

    def set_image(self, i: int, img) -> None:
        if 0 <= i < len(self._imgs):
            self._imgs[i] = img

    def clear(self) -> None:
        self._imgs = [None] * len(self._imgs)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        try:
            i = int(image_id.split("?", 1)[0])
        except ValueError:
            i = -1
        im = self._imgs[i] if 0 <= i < len(self._imgs) else None
        if im is None:                       # 아직 없음 → 유효한 1x1(깨진 이미지 아이콘 방지)
            im = QImage(1, 1, QImage.Format.Format_RGB888)
            im.fill(0x2b2b2b)
        return im


class NrBaseProvider(QQuickImageProvider):
    """디노이즈드 중성 베이스(프록시 해상도 RGBA64)를 'image://nrbase/...' 로 제공.
    가이디드=luma 복제 그레이, AI=RGB(크로마 포함 — 셰이더 nrChroma 게이트로 구분).
    준비 전에는 1x1(셰이더가 nrOn 게이트로 무시)이라 내용 무관."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_RGBA64)
        self._img.fill(0)

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage(1, 1, QImage.Format.Format_RGBA64)
        self._img.fill(0)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class HazeProvider(QQuickImageProvider):
    """디헤이즈 투과율 맵(소형 단일채널 Grayscale8)을 'image://haze/...' 로 제공.
    기본/클리어 = 1x1 흰색(t=1, 안개 없음) → 셰이더 물리 분기가 항등이 되어 안전."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale8)
        self._img.fill(255)

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale8)
        self._img.fill(255)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class MistProvider(QQuickImageProvider):
    """미스트 산란 필드 3장(narrow/mid/wide)을 'image://mist/<i>?v=N' 로 제공.

    카메라네이티브 scene-linear ÷ `coeffs.MIST_TEX_MAX` 를 **선형** RGBA64 로 담는다(HDR 이라
    [0,1] 8bit 로는 못 담고, 감마 인코딩 블러는 물리적으로 틀리다 — coeffs 주석 참조).
    각 장은 σ 에 맞는 축소 해상도이고 셰이더가 bilinear 업샘플한다. 준비 전에는 1x1 검정이며
    셰이더 `mistOn` 게이트가 미스트를 끈다(내용 무관)."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._imgs = [self._blank() for _ in range(3)]

    @staticmethod
    def _blank() -> QImage:
        im = QImage(1, 1, QImage.Format.Format_RGBA64)
        im.fill(0)
        return im

    def set_images(self, imgs) -> None:
        self._imgs = list(imgs)

    def clear(self) -> None:
        self._imgs = [self._blank() for _ in range(3)]

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        try:
            i = int(str(image_id).split("?")[0])
        except ValueError:
            i = 0
        return self._imgs[i] if 0 <= i < len(self._imgs) else self._blank()


class ThumbProvider(QQuickImageProvider):
    """RAW 임베드 프리뷰 -> 썸네일을 'image://thumb/<percent-encoded-path>' 로 제공.

    ForceAsynchronousImageLoading 으로 requestImage 가 항상 Qt 워커 스레드에서
    호출되므로 GUI 가 안 멈춘다(폴더에 파일이 많아도). QML 쪽은 ListView 로
    화면에 보이는 delegate 만 요청 -> 지연 로딩. 디코딩 결과는 경로별 캐시.
    """

    # 크기별 캐시 상한(LRU). 384px ARGB ≈ 0.4MB/장 → 최대 ~160MB.
    _MAX_ENTRIES = 400

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self._cache = OrderedDict()      # (abs_path, edge) -> QImage (LRU)
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        raw = image_id.split("?", 1)[0]              # 쿼리스트링 제거(혹시 모를 대비)
        path = QUrl.fromPercentEncoding(raw.encode("utf-8"))  # encodeURIComponent 역변환
        edge = (requested_size.width()
                if (requested_size is not None and requested_size.width() > 0) else 96)
        key = (path, edge)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and not cached.isNull():
                self._cache.move_to_end(key)
                return cached
        img = self._make_thumb(path, edge)
        with self._lock:
            self._cache[key] = img
            self._cache.move_to_end(key)
            while len(self._cache) > self._MAX_ENTRIES:
                self._cache.popitem(last=False)
        return img

    @staticmethod
    def _make_thumb(path, edge: int) -> QImage:
        # 1차: RAF 내장 JPEG 안의 EXIF 썸네일(~160px, 수 KB) — 초경량/고속.
        #      EXIF/썸네일은 JPEG 선두라 앞부분 512KB 만 읽으면 충분.
        #      단 요청 크기가 원본(160px)을 넘으면 업스케일로 흐려지므로
        #      2차(내장 풀 프리뷰 축소 디코딩)로 넘어간다(그리드 썸네일 확대용).
        #      ⚠️non-RAF 는 _read_embedded_jpeg 가 None → 2차로 감. extract_thumb 이
        #      프리뷰 '바이트만' 추출(디코드 X)이라 이미 ~1-5ms 로 충분히 빠름(벤치 확인).
        if edge <= 160:
            try:
                jpeg = _read_embedded_jpeg(path)
                if jpeg:
                    import exifread
                    tags = exifread.process_file(io.BytesIO(jpeg), details=False)
                    thumb = tags.get("JPEGThumbnail")
                    if thumb:
                        im = QImage()
                        # ⚠️loadFromData 는 GIL 을 쥔 채 플러그인 뮤텍스를 기다린다 —
                        #   실측 교착의 한쪽 다리(decode_lock 모듈 주석).
                        with QT_IMG_LOCK:
                            ok = im.loadFromData(thumb)
                        if ok:
                            ori = tags.get("Image Orientation")
                            ori_v = ori.values[0] if ori and ori.values else 1
                            im = ThumbProvider._apply_orientation(im, ori_v)
                            # 후지 EXIF 썸네일(4:3)에 구워진 레터박스 띠 제거 — 실제 사진 비율
                            # (EXIF 치수)로 중앙 크롭. 90/270°는 표시 비율 반전. 없으면 스킵(현행 유지).
                            tw, tl = tags.get("EXIF ExifImageWidth"), tags.get("EXIF ExifImageLength")
                            if tw and tl and tw.values and tl.values:
                                target = float(tw.values[0]) / max(1.0, float(tl.values[0]))
                                if ori_v in (5, 6, 7, 8):
                                    target = 1.0 / target
                                im = ThumbProvider._crop_to_aspect(im, target)
                            # 원본보다 크게 요청돼도 업스케일 안 함(호버 피크가 160
                            # 요청 시 세로사진은 회전 후 120px 폭 원본 그대로 반환).
                            if im.width() > edge:
                                im = im.scaledToWidth(
                                    edge, Qt.TransformationMode.SmoothTransformation)
                            return im
            except Exception:
                pass
        # 2차: EXIF 썸네일이 없거나 큰 썸네일(>160px) 요청이면 내장 풀 프리뷰를
        #      요청 크기로 축소 디코딩(libjpeg 스케일드 디코딩, 13MP 풀디코딩 회피).
        try:
            # edge 를 넘겨야 임베드 프리뷰가 없는 PNG/TIFF 를 **축소 디코딩**한다
            # (없으면 96px 타일 하나에 12MP 풀디코드 + 10.7MB JPEG 재인코딩 — exif_info 주석 참조).
            jpeg = embedded_preview_jpeg(path, edge=edge)
            if not jpeg:
                return QImage()                      # null -> QML status=Error -> placeholder
            buf = QBuffer()
            buf.setData(jpeg)                        # 내부 QByteArray 로 복사(수명 안전)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            with QT_IMG_LOCK:                        # 파이썬제 QBuffer 디코드(decode_lock)
                reader = QImageReader(buf, b"jpeg")
                reader.setAutoTransform(True)        # EXIF 방향 반영
                full = reader.size()
                if full.isValid() and full.width() > 0:
                    h = max(1, round(edge * full.height() / full.width()))
                    reader.setScaledSize(QSize(edge, h))
                img = reader.read()
            buf.close()
            return img if not img.isNull() else QImage()
        except Exception:
            return QImage()

    @staticmethod
    def _crop_to_aspect(img: QImage, target: float) -> QImage:
        """이미지를 target 종횡비(가로/세로)로 중앙 크롭. 후지 EXIF 썸네일은 4:3 컨테이너에
        실제 사진 비율을 레터박스(검은 띠)로 담으므로, 실제 비율로 잘라 띠를 제거한다
        (정사각형 PreserveAspectCrop 채움이 깔끔해짐). 오차 작으면 원본 반환."""
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0 or target <= 0:
            return img
        cur = w / h
        if abs(cur - target) < 0.02:
            return img
        if cur > target:                                    # 너무 넓음 → 폭 크롭(좌우 띠)
            nw = max(1, round(h * target))
            return img.copy((w - nw) // 2, 0, nw, h)
        nh = max(1, round(w / target))                      # 너무 높음 → 높이 크롭(상하 띠)
        return img.copy(0, (h - nh) // 2, w, nh)

    @staticmethod
    def _apply_orientation(img: QImage, ori: int) -> QImage:
        """EXIF Orientation(1~8)을 썸네일에 반영. IFD1 썸네일은 회전 안 된 채
        저장되므로 메인 이미지 방향값을 그대로 적용한다(세로 사진 바로 세움)."""
        if ori in (1, None):
            return img
        t = QTransform()
        if ori == 2:                       # 좌우 반전
            return img.transformed(t.scale(-1, 1))
        if ori == 3:                       # 180°
            return img.transformed(t.rotate(180))
        if ori == 4:                       # 상하 반전
            return img.transformed(t.scale(1, -1))
        if ori == 5:                       # 좌우 반전 + 90°CW
            return img.transformed(t.rotate(90).scale(-1, 1))
        if ori == 6:                       # 90°CW
            return img.transformed(t.rotate(90))
        if ori == 7:                       # 좌우 반전 + 270°CW
            return img.transformed(t.rotate(270).scale(-1, 1))
        if ori == 8:                       # 270°CW(=90°CCW)
            return img.transformed(t.rotate(270))
        return img


class PreviewProvider(QQuickImageProvider):
    """RAW 내장 풀 프리뷰 -> 큰 프리뷰를 'image://preview/<percent-encoded-path>' 로 제공.

    프리뷰 모드(PreviewWindow.qml)용. ThumbProvider 의 2차 폴백과 동일한 경로
    (내장 풀 프리뷰 JPEG 를 QImageReader.setScaledSize 로 축소 디코딩)를 쓰되,
    요청 크기(~2048px)가 커서 결과 QImage 가 장당 ~11MB → 무제한 캐시 금지.
    최근 N 개만 유지하는 LRU 로 좌/우 인접 이동 시 재디코딩을 최소화한다.
    """

    _CACHE_MAX = 5

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self._cache = OrderedDict()       # "path|edge" -> QImage (LRU)
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        raw = image_id.split("?", 1)[0]               # 쿼리스트링(?v=) 제거
        path = QUrl.fromPercentEncoding(raw.encode("utf-8"))
        edge = (requested_size.width()
                if (requested_size is not None and requested_size.width() > 0) else 2048)
        key = f"{path}|{edge}"
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and not cached.isNull():
                self._cache.move_to_end(key)          # 최근 사용 표시
                return cached
        img = self._make_preview(path, edge)
        with self._lock:
            self._cache[key] = img
            self._cache.move_to_end(key)
            while len(self._cache) > self._CACHE_MAX:
                self._cache.popitem(last=False)       # 가장 오래된 것 제거
        return img

    @staticmethod
    def _make_preview(path, edge) -> QImage:
        try:
            jpeg = embedded_preview_jpeg(path, edge=edge)   # PNG/TIFF 축소 디코딩(_make_thumb 동일)
            if not jpeg:
                return QImage()
            buf = QBuffer()
            buf.setData(jpeg)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            with QT_IMG_LOCK:                         # 파이썬제 QBuffer 디코드(decode_lock)
                reader = QImageReader(buf, b"jpeg")
                reader.setAutoTransform(True)         # EXIF 방향 반영
                full = reader.size()
                if full.isValid() and full.width() > 0 and full.width() > edge:
                    h = max(1, round(edge * full.height() / full.width()))
                    reader.setScaledSize(QSize(edge, h))
                img = reader.read()
            buf.close()
            return img if not img.isNull() else QImage()
        except Exception:
            return QImage()


class WallThumbProvider(QQuickImageProvider):
    """Wallpaper 패널 썸네일: 내장 프리뷰 JPEG 에 **사이드카 지오메트리**(플립/90°/스트레이튼/
    원근/크롭)를 적용해 제공 — export(render_full→_apply_geometry)와 같은 프레이밍이라
    패널 목업 프리뷰의 오프셋 조절 기준이 실제 결과와 일치한다. 톤/색 편집은 미적용
    (프레이밍 판단에 불필요 — 지오메트리만 pipeline._apply_geometry 로 재현).
    URL: 'image://wallthumb/<percent-encoded-path>?r=<editsRevision>' — r 은 QML Image
    URL 캐시 무효화용 쿼리이고, 내부 캐시는 사이드카 mtime 을 키에 포함해 따로 무효화."""

    _CACHE_MAX = 8
    _GEO_KEYS = ("flipH", "flipV", "quarterTurns", "rotateAngle",
                 "cropX", "cropY", "cropW", "cropH", "geoV", "geoH", "geoScale")

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self._cache = OrderedDict()      # (path, edge, edits_mtime) -> QImage (LRU)
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        raw = image_id.split("?", 1)[0]               # 쿼리스트링(?r=) 제거
        path = QUrl.fromPercentEncoding(raw.encode("utf-8"))
        edge = (requested_size.width()
                if (requested_size is not None and requested_size.width() > 0) else 512)
        p = Path(path)
        ep = Controller._edits_path(str(p.parent), p.name)
        try:
            mtime = ep.stat().st_mtime_ns if ep.is_file() else 0
        except OSError:
            mtime = 0
        key = (path, edge, mtime)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and not cached.isNull():
                self._cache.move_to_end(key)
                return cached
        img = self._make(path, edge)
        with self._lock:
            self._cache[key] = img
            self._cache.move_to_end(key)
            while len(self._cache) > self._CACHE_MAX:
                self._cache.popitem(last=False)
        return img

    @staticmethod
    def _make(path, edge) -> QImage:
        img = PreviewProvider._make_preview(path, edge)
        if img.isNull():
            return img
        try:
            e = Controller._read_edits(path)
            if not any(k in e for k in WallThumbProvider._GEO_KEYS):
                return img                            # 사이드카 없음 → 원본 프레이밍 그대로
            import numpy as np
            import pipeline
            img = img.convertToFormat(QImage.Format.Format_RGB888)
            w, h, bpl = img.width(), img.height(), img.bytesPerLine()
            arr = np.frombuffer(img.constBits(), np.uint8, h * bpl).reshape(h, bpl)
            arr = np.ascontiguousarray(arr[:, :w * 3].reshape(h, w, 3))
            gp = dict(e)
            gp["geoScalePct"] = float(e.get("geoScale", 100.0))   # 사이드카 키 → export 키
            arr = np.ascontiguousarray(pipeline._apply_geometry(arr, gp))
            h2, w2 = arr.shape[:2]
            return QImage(arr.data, w2, h2, 3 * w2,
                          QImage.Format.Format_RGB888).copy()
        except Exception:
            return img                                # 지오메트리 실패 시 원본 썸네일 폴백


class Controller(QObject):
    imageChanged = Signal()
    asShotKelvinChanged = Signal()
    wbBaked = Signal()          # 재디코딩 완료(=baked WB 갱신) 알림
    curveChanged = Signal()     # 톤 커브 LUT 갱신 알림
    exportStatusChanged = Signal()
    loadErrorChanged = Signal()        # 디코드 실패(미지원/손상 RAW) 사용자 안내 갱신 알림
    exportProgressChanged = Signal()   # CPU export 진행률(0..1) 갱신 알림(필름 카운터 오버레이용)
    exifChanged = Signal()      # 촬영정보(EXIF) 갱신 알림
    gpsChanged = Signal()       # 사진에 붙은 위치(지오태그) 변경 알림
    folderMapChanged = Signal()  # Photo map: 폴더 좌표 스캔 결과/진행 갱신 알림
    stampChanged = Signal()     # 날짜 스탬프 **편집값**(텍스트/폰트/크기/색/글로우/영역/회전…) 변경
    stampSpriteChanged = Signal()  # 스프라이트(url·wr·hr·bleed) 갱신 — ⚠️**편집이 아니다**
    #   ⚠️둘을 합치지 말 것. QML `editSaveWatch` 가 스탬프 편집값들을 보고 자동저장을 예약하는데,
    #   스프라이트는 워커에서 늦게 오므로(_stamp_worker) 같은 시그널로 알리면 **편집을 하나도
    #   안 했는데 사이드카가 생기고**(로드 직후 edited 배지), **Reset 직후 다시 edited 가 된다**
    #   (실측 재현: 동기 시절엔 안 생기고 비동기로 바꾼 뒤 생김). 파생 렌더 결과와 편집값은
    #   서로 다른 알림이어야 한다.
    wallShotsChanged = Signal()       # 배경화면 슬롯 EXIF 요약이 워커에서 도착(미리보기 재평가)
    stampDefaultsChanged = Signal()   # 스탬프 '내 기본값' 갱신 알림
    exportOptsChanged = Signal()      # 기억된 export 옵션 갱신 알림
    stampFontsChanged = Signal()      # 폰트 목록(사용자 추가/삭제) 갱신 알림
    filmSimsChanged = Signal()        # 필름시뮬 목록(사용자 LUT 추가/삭제) 갱신 알림
    editsReady = Signal()       # 새 파일 디코딩 완료 -> QML 이 저장 편집 복원(또는 기본값 리셋)
    histogramChanged = Signal()  # 톤커브 배경 히스토그램 갱신 알림
    simExpEVChanged = Signal()   # 필름시뮬 보정 노출(EV) 갱신 알림 — 셰이더 simExpEV 유니폼
    clipLevelChanged = Signal()  # 센서 포화 레벨 갱신 알림 — 셰이더 clipLevel 유니폼
    autoExpChanged = Signal()    # 자동노출 on/off + 적용된 EV 갱신 알림
    lensChanged = Signal()       # 렌즈 보정 on/off 변경 알림
    busyChanged = Signal()       # 디코딩(렌즈 보정 포함) 진행 중 표시
    folderChanged = Signal()     # 좌측 file explorer 현재 폴더/파일목록 갱신 알림
    likesChanged = Signal()      # 좋아요(셀렉트) 상태 변경 알림 (썸네일 하트 반영용)
    editsChanged = Signal()      # 편집 사이드카 유무 변경 알림 (썸네일 편집 배지 반영용)
    flushEdits = Signal()        # 이미지 전환 직전: QML 이 *이전* 파일로 편집 저장(플러시)
    fullChanged = Signal()       # GPU export: 풀해상도 src URL 갱신(QML Image 재로드용)
    fullReady = Signal()         # GPU export: 풀해상도 디코드 완료(QML 이 grab 준비)
    fullAborted = Signal()       # GPU export: 파이썬 측 디코드 실패 → QML 로더 해제(active=false)
    nrFullChanged = Signal()     # GPU export: 노이즈 항 텍스처 URL/준비상태 갱신(QML Image 재로드용)
    skyMaskChanged = Signal()    # 하늘 마스크 텍스처 갱신 알림(생성/클리어 모두)
    skySelected = Signal()       # 하늘 마스크 '생성 완료'만(클리어 제외) → QML 이 오버레이 자동 표시
    skyBusyChanged = Signal()    # 하늘 세그멘테이션(추론) 진행 중 표시
    segStatusChanged = Signal()  # 세그 상태 문구(예: 모델 다운로드 중) 갱신 알림
    facesChanged = Signal()      # 얼굴 검출 결과/썸네일/스캔 상태 갱신 알림
    modelsChanged = Signal()     # AI 모델 설치 상태 변화(다운로드 시작/완료) — 목록 재평가
    # ⚠️진행률은 별도 시그널 — modelsChanged 로 묶으면 틱마다 modelCatalog 가 새 리스트를
    #   반환해 Repeater 가 델리게이트를 전부 재생성한다(1GB 다운로드 = 200회, 버튼 깜빡임·클릭 유실).
    modelProgressChanged = Signal()
    cmChanged = Signal()         # 디스플레이 색관리 LUT 갱신 알림(모니터 전환/로드)
    hazeChanged = Signal()       # 디헤이즈 투과율 맵/대기광/conf 갱신 알림(DCP)
    nrChanged = Signal()         # 휘도 NR 베이스 텍스처/준비 상태 갱신 알림
    mistChanged = Signal()       # 미스트 산란 필드 텍스처/균일항/준비 상태 갱신 알림
    aiNrChanged = Signal()       # AI 디노이즈(NAFNet) 사용 여부/상태 문구 갱신 알림
    captionChanged = Signal()    # 캡션 텍스트/생성 상태 갱신 알림(Florence-2)
    searchChanged = Signal()     # 탐색기 캡션 검색어 변경 알림(explorerFiles 재평가)
    indexChanged = Signal()      # 폴더 배치 인덱싱 busy/진행/상태 갱신
    updateChanged = Signal()     # 새 버전 발견 알림(updateVersion/updateUrl 갱신)
    screenSizeChanged = Signal()  # 창이 놓인 화면의 픽셀 크기 갱신(배경화면 'Match screen')
    # 깊이 범위 자동 시드 확정 → QML 이 'depth@auto' 센티넬을 실제 값으로 교체하고 슬라이더에 반영.
    # (켜는 순간엔 거리 맵이 없어 범위를 정할 수 없다 — 맵이 나온 뒤에야 분포에서 시드된다)
    depthAutoResolved = Signal(int, float, float, float)   # layer, near, far, feather
    rawPeekChanged = Signal()
    rawPeekAvailChanged = Signal()    # RAW Peek(디모자이크 이전 뷰) 그림/정보/상태 갱신 알림
    developChanged = Signal()    # Develop 애니메이션 스냅샷/단계 목록 갱신 알림
    _rawPeekSig = Signal(object)  # (내부) RAW Peek 워커 -> 메인 (seq, kind, payload)
    _renderReady = Signal(object)  # (내부) 워커 스레드 -> 메인 스레드 결과 전달
    _fullDecoded = Signal(bool)  # (내부) 풀해상도 디코드 워커 -> 메인 스레드
    _exportStatusSig = Signal(str)  # (내부) export 워커 -> 메인 스레드 상태 문구
    _nrFullSig = Signal()        # (내부) NR 노이즈 항 텍스처 굽기 완료 -> 메인 스레드
    _skyReady = Signal(object)   # (내부) 마스크 워커 -> 메인 스레드 (img_gen, layer, lseq, mask, strokes)
    _segStatusSig = Signal(str)  # (내부) 세그 워커 -> 메인 스레드 상태 문구 전달
    _segDlSig = Signal(object)   # (내부) 세그 워커 -> 메인 스레드 (downloading, 진행률 0..1)
    _depthAutoSig = Signal(object)  # (내부) 마스크 워커 -> 메인 (img_gen, layer, near, far, feather)
    _modelDlSig = Signal(object)  # (내부) AI Models 수동 다운로드 워커 -> 메인 (key, 0..1, state)
    # (내부) 얼굴 검출 워커 -> 메인 (img_gen, dets, thumbs). ⚠️_skyReady 재사용 금지 —
    # 그쪽은 _mask_ran 을 세워 maskSettled 를 참으로 만들고, 배치 export 가 그걸 기다린다.
    # 검출만 끝난 걸 '마스크 준비 완료'로 오해해 마스크 없이 저장되는 사고가 난다.
    _facesReady = Signal(object)
    _exportProgressSig = Signal(float)  # (내부) export 워커 -> 메인 스레드 진행률(0..1)
    exportExtChanged = Signal()
    _keepAwakeSig = Signal(bool)  # (내부) export 워커 -> 메인 스레드 슬립 방지 해제(스레드 귀속 API)
    _hazeReady = Signal(object)  # (내부) 디헤이즈 추정 워커 -> 메인 스레드 (seq, (t, A, conf))
    _nrReady = Signal(object)    # (내부) NR 베이스 워커 -> 메인 스레드 (seq, 디노이즈드 luma)
    _mistReady = Signal(object)  # (내부) 미스트 필드 워커 -> 메인 (seq, (radius,hi), 필드3, 평균)
    _aiNrStatusSig = Signal(object)  # (내부) AI NR 워커 -> 메인 스레드 (seq, 상태 문구)
    _aiNrDlSig = Signal(object)      # (내부) AI 모델 다운로드 워커 -> 메인 (downloading, 진행률 0..1)
                                     #  ⚠️seq 없음 — 다운로드는 모델 전역(이미지 무관), finally 로 항상 해제
    _aiNrInitSig = Signal(bool)      # (내부) ORT 세션 초기화(GPU 점유) 오버레이 ON/OFF — 세션 전역
    _stampSpriteSig = Signal(object)  # (내부) 스탬프 스프라이트 워커 -> 메인 (seq, layer, wr, hr)
    _shotInfoSig = Signal()           # (내부) EXIF 요약 워커 -> 메인(큐잉 연결로 스레드 경계 통과)
    _updateSig = Signal(object)      # (내부) 업데이트 확인 워커 -> 메인 (새 버전 태그, 릴리스 URL)
    _folderScanSig = Signal(object)  # (내부) 폴더 스캔 워커 -> 메인 (seq, folder, items, likes, edited, force)
    _indexProgressSig = Signal(object)  # (내부) 폴더 배치 인덱싱 워커 -> 메인 (seq, done, total, status)
    _mapScanSig = Signal(object)     # (내부) Photo map 좌표 스캔 워커 -> 메인 (seq, folder, raw, total)

    def __init__(self, provider: RawProvider, curve_provider: "CurveProvider",
                 stamp_provider: "StampProvider" = None,
                 full_provider: "RawFullProvider" = None,
                 sky_provider: "SkyMaskProvider" = None,
                 cm_provider: "DisplayCmProvider" = None,
                 haze_provider: "HazeProvider" = None,
                 nr_provider: "NrBaseProvider" = None,
                 face_provider: "FaceThumbProvider" = None,
                 mist_provider: "MistProvider" = None,
                 peek_provider: "RawPeekProvider" = None,
                 nrfull_provider: "NrFullProvider" = None):
        super().__init__()
        # --- RAW Peek(디모자이크 이전 센서 뷰) — 진단 전용, 룩/export 와 무관 ---
        self._peek_provider = peek_provider
        self._peek = None            # raw_peek.RawPeek (오버레이 열려 있는 동안만)
        self._peek_path = ""
        self._peek_info = ""
        self._peek_busy = False
        self._peek_seq = 0           # 오래된 워커 결과 폐기용
        self._peek_counter = 0       # 'image://rawpeek/...?v=N' 캐시버스트
        self._peek_url = "image://rawpeek/main?v=0"
        self._peek_pattern_url = "image://rawpeek/pattern?v=0"
        self._peek_hist_url = "image://rawpeek/hist?v=0"
        self._peek_mini_url = "image://rawpeek/mini?v=0"
        self._peek_job = None        # 대기 중인 최신 무거운 렌더 요청(코얼레싱)
        self._peek_running = False
        self._peek_lock = threading.Lock()   # ⚠️`_peek_job`/`_peek_running` 은 **짝으로** 본다
        self._peek_req = 0           # 요청 일련번호(늦게 온 워커 결과를 거르는 기준)
        self._peek_pub = 0           # 마지막으로 화면에 올린 요청 번호
        self._peek_last_mode = -1    # 직전 요청의 모드 — 탭 전환 판정(줄 서지 않기)
        # 메인 뷰 캡션 — ★이미지에 굽지 않고 QML 이 고정 높이 밴드에 그린다(raw_peek.py 주석 참조)
        self._peek_caption = ""
        self._peek_status = ""       # 후보 디코드 진행 표시(오래 걸리는 첫 렌더)
        self._peek_prog = 0.0        # 후보 디코드 진행분율(done/total) — QML 진행 바
        self._peek_center = (0.5, 0.5)   # 오픈 시 기본 팬 위치(디테일 있는 곳)
        # --- Develop 애니메이션(RAW Peek 의 Develop 탭) ---
        # 스냅샷 = 애니메이션 시작 시 QML 이 읽어 보낸 **최종 uniform 값**. 여기서 슬라이더를
        # 읽거나 쓰지 않는다 — 쓰면 사이드카 저장·undo·RAW 재디코드가 발동한다(develop_anim 주석).
        self._dev_snap = {}
        self._dev_marks = []
        self._dev_mosaic_url = "image://rawpeek/develop?v=0"
        self._dev_gray_url = "image://rawpeek/developgray?v=0"
        self._dev_mosaic_size = None      # develop 그림을 마지막으로 만든 크기(재요청 스킵용)
        self._face_provider = face_provider      # 얼굴 선택 타일 썸네일
        self._provider = provider
        self._cm_provider = cm_provider          # 디스플레이 색관리 LUT(프리뷰 전용)
        self._cm_n = 0                           # CM LUT 한 변 N (0=미적용)
        self._has_cm = False                     # 유효 CM LUT 존재(=광색역 모니터)
        self._cm_url = "image://displaycm/c?v=0"
        self._cm_counter = 0
        self._cm_dst = None                      # sRGB→모니터 QColorSpace(스탬프 오버레이 CM 용)
        self._cm_enabled = True                  # displayCM 토글(win.displayCM) — 스탬프 CM 게이트
        self._curve_provider = curve_provider
        self._stamp_provider = stamp_provider
        self._full_provider = full_provider     # GPU export 풀해상도 src
        self._nrfull_provider = nrfull_provider  # GPU export 노이즈 항 텍스처(셰이더 nrNoise=1)
        self._nrfull_url = "image://nrfull/n?v=0"
        self._nrfull_counter = 0
        self._nrfull_ready = False   # 이번 GPU export 에 NR 텍스처가 준비됨(셰이더 nrOn 게이트)
        self._sky_provider = sky_provider        # 하늘 마스크 텍스처
        self._haze_provider = haze_provider      # 디헤이즈 투과율 맵 텍스처(DCP)
        self._haze_url = "image://haze/h?v=0"
        self._haze_counter = 0
        self._haze_seq = 0          # 비동기 추정 순번(이미지 전환 레이스 방지)
        self._haze_t = None         # 투과율 맵(numpy float32, 소형) — CPU export 용
        self._haze_A = [1.0, 1.0, 1.0]   # 대기광(display sRGB)
        self._haze_conf = 0.0       # 추정 신뢰도(0=물리 모델 미사용 → 톤모델 폴백)
        self._mist_provider = mist_provider      # 미스트 산란 필드 3장(narrow/mid/wide)
        self._mist_urls = [f"image://mist/{i}?v=0" for i in range(3)]
        self._mist_counter = 0
        self._mist_seq = 0          # 비동기 계산 순번(이미지 전환/파라미터 변경 레이스 방지)
        self._mist_ready = False    # 준비 전 셰이더 미스트 무동작(mistOn 게이트)
        self._mist_amt = 0.0        # 현재 Amount(QML 이 밀어준다). **0 이면 필드를 만들지 않는다**
        self._mist_mean = [0.0, 0.0, 0.0]   # 균일항(산란 소스 프레임 평균, 카메라네이티브 선형)
        # 필드가 어떤 (Radius, Highlight) 로 계산됐는지 — 이 둘만 재계산을 부른다.
        # Amount/Character 는 셰이더 uniform 이라 실시간(필드 무관).
        self._mist_want = (1.0, 0.8)   # 원하는 키(QML 이 설정)
        self._mist_field = None        # 계산됐거나 계산 중인 키. None = 없음
        self._nr_provider = nr_provider          # 디노이즈드 중성 luma 텍스처(휘도 NR 베이스)
        self._nr_url = "image://nrbase/n?v=0"
        self._nr_counter = 0
        self._nr_seq = 0            # 비동기 계산 순번(이미지 전환 레이스 방지)
        self._nr_ready = False      # 준비 전 셰이더 휘도 NR 무동작(nrOn 게이트)
        self._ai_nr = False         # AI 디노이즈 베이스 사용(파일별 편집값, 사이드카 저장)
        self._ai_status = ""        # AI NR 상태 문구(다운로드/타일 진행/오류). 빈 문자열=없음
        self._ui_busy = False       # 사용자 드래그 중(QML editDragActive) — AI 타일 루프 일시정지
        self._update_version = ""   # 새 버전 태그("v1.3.0"). 빈 문자열=최신이거나 미확인
        self._update_url = ""       # 새 버전 릴리스 페이지 URL
        self._ai_downloading = False  # AI 모델 다운로드 중(이미지 영역 차단 오버레이 + 프로그레스바)
        self._ai_dl_prog = 0.0      # 다운로드 진행률 0..1
        self._ai_initializing = False  # ORT 세션 초기화 중(GPU 점유 → 차단 오버레이 'Preparing…')
        self._nr_chroma = False     # 현재 nrBase 가 AI RGB(크로마 유효) 베이스인지 — 셰이더 게이트
        self._nr_ai_seq = -1        # AI(RGB) 베이스가 적용된 seq — 뒤늦은 가이디드 폴백의 덮어쓰기 방지
        self._layer_urls = [f"image://skymask/{i}?v=0" for i in range(5)]  # 레이어별 마스크 URL
        self._layer_counters = [0] * 5     # 레이어별 URL 버전(해당 레이어만 QML 재로드)
        self._img_gen = 0           # 이미지 세대 — 이미지 바뀜 시 in-flight 워커/_seg_probs 캐시 무효화
        self._layer_seq = [0] * 5   # 레이어별 마스크 워커 순번(레이어 독립 staleness — 전역 seq 아님)
        self._sky_pending = 0       # in-flight 마스크 워커 수(busy = pending>0)
        self._sky_busy = False      # 세그 추론/재조합 진행 중
        self._seg_status = ""       # 세그 상태 문구(모델 다운로드 중 등). 빈 문자열=없음
        self._model_dl_key = ""     # AI Models 화면에서 수동 다운로드 중인 모듈 key("" = 없음)
        self._model_dl_prog = 0.0   # 그 진행률 0..1
        self._model_error = ""      # 마지막 다운로드 실패 메시지(성공 시 "")
        self._seg_downloading = False   # 마스킹 모델 다운로드 중(전용 프로그레스바 표시)
        self._seg_dl_prog = 0.0         # 다운로드 진행률 0..1
        self._layer_masks = [None] * 5  # 레이어별 마스크(numpy [0,1], 프록시) — CPU export 용
        self._proxy_img = None      # 마지막 프록시 QImage(세그 입력 디코드용)
        self._seg_probs = None      # 캐시된 150클래스 softmax(저해상도) — 이미지당 추론 1회(레이어 공유)
        self._seg_guide = None      # 캐시된 원본 휘도(guided filter 가이드)
        self._seg_size = None       # 캐시된 마스크 출력 크기(H,W)
        self._layer_keys = [[] for _ in range(5)]  # 레이어별 선택 클래스 그룹 key 목록
        # 레이어별 브러시 획(벡터 목록, brush.py 참조). 영속화 진실원은 QML win.layers[i].strokes
        # (사이드카 skyEditParams) — 여기는 워커 래스터용 미러(setStrokes/addStroke 로 동기).
        # _layer_mask_strokes = 현재 _layer_masks[i] 를 만든 획 스냅샷 — setMaskClasses no-op
        # 판정용(undo 가 setStrokes+같은 keys 로 와도 획이 다르면 재생성돼야 한다).
        self._layer_strokes = [[] for _ in range(5)]
        self._layer_mask_strokes = [[] for _ in range(5)]
        # 자동(장면∪얼굴∪깊이, 획 적용 **전**) 마스크 캐시 — Undo/Clear stroke 가 세그 워커
        # (세그 입력 재구성 ~1s 가능 → dim/프로그레스) 없이 동기 리플레이하기 위한 base.
        # 워커 완료(_on_sky_ready)가 채우고, 이미지 전환/레이어 해제가 무효화.
        self._layer_automask = [None] * 5
        self._layer_automask_valid = [False] * 5
        # 획별 패치 스냅샷(꼬리 정렬) — addStroke(증분)가 획이 바꿀 bbox 의 **이전 픽셀**을
        # 저장, popStroke 는 되돌려쓰기만(래스터 0회 = 획 수 무관 즉각). 항목 =
        # (y0,y1,x0,x1, region float32 | None=마스크가 없었음(0)). 워커 리플레이/복원/이미지
        # 전환은 스택을 비움(그 후 pop 은 automask 리플레이 폴백). 메모리 상한 초과 시 오래된
        # 것부터 폐기(꼬리 정렬이라 최근 undo 는 계속 즉각).
        self._stroke_patches = [[] for _ in range(5)]
        self._PATCH_CAP_BYTES = 96 * 1024 * 1024   # 레이어당 상한(풀프레임 float32 ≈ 17.5MB)
        self._mask_ran = False      # 이 이미지에서 마스크 워커가 한 번이라도 끝났는지(maskSettled)
        self._seg_rgb8 = None       # 캐시된 세그 입력(중성 display sRGB) — 장면/얼굴/깊이 공용
        # 캐시된 거리 맵(프록시 해상도 float32, 정제 완료) — 이미지당 추론 1회(레이어 5개 공유).
        # near/far 슬라이더는 이 맵에 밴드패스만 걸어(~46ms) 재추론하지 않는다.
        self._depth_map = None
        self._depth_lock = threading.Lock()   # 레이어 동시 복원 시 중복 추론 방지(face 와 같은 이유)
        # 레이어별 Scene∪Face 마스크 캐시(깊이 제외) + 그것을 만든 비-깊이 key 목록.
        # ⚠️깊이 범위 슬라이더는 깊이 성분만 바꾸는데, 캐시가 없으면 매 커밋이 sky_seg.compose_mask
        #   (프록시 전체 scipy 가이디드필터+fill_holes = ~870ms)를 통째로 다시 돌려 드래그마다
        #   dim(350ms 문턱)이 떴다. 실측: Depth+Scene 947ms → 캐시 후 ~70ms.
        # 메모리: 레이어당 프록시 float32 1장(2560×1709 ≈ 17.6MB) → 5레이어 다 쓰면 최대 ~88MB.
        # (_layer_masks 가 이미 같은 규모를 들고 있고, 깊이 없는 레이어는 두 배열이 같은 객체다)
        self._layer_segmask = [None] * 5
        self._layer_segkeys = [None] * 5
        self._face_parsed = None    # 캐시된 얼굴별 파싱 확률맵 [(geom, probs19)] — 약 1.2MB/얼굴
        self._face_dets = None      # 캐시된 검출 결과(썸네일·선택 매칭용) — 파싱보다 훨씬 쌈
        self._face_thumb_urls = []  # 얼굴 썸네일 URL(개수 = 검출 수)
        self._face_counters = [0] * MAX_FACE_SLOTS   # 썸네일별 URL 버전(캐시 무력화)
        self._face_scanning = False  # 검출 워커 진행 중 → Face 체크박스 잠깐 비활성
        # 이 이미지에 대해 썸네일까지 만들었는지. ⚠️_face_dets 유무로 판단하면 안 된다 —
        # 사이드카 복원은 마스크 워커가 먼저 돌아 _face_dets 만 채우므로(썸네일·notify 없음),
        # 그 뒤 Face 탭을 열어도 requestFaces 가 '이미 있다'고 판단해 타일이 영영 안 뜬다.
        self._face_scanned = False
        # 검출+파싱 compute-once. 사진 복원 시 applySkyEdits 가 5개 레이어의 setMaskClasses 를
        # 한 틱에 호출 → 워커 5개가 동시에 캐시 miss 를 보고 각자 파싱(얼굴당 0.8s×5)한다.
        self._face_lock = threading.Lock()
        self._active_layer = 0      # 편집 중인 활성 레이어(오버레이/슬라이더 대상)
        self._full_url = "image://rawfull/f?v=0"
        self._full_counter = 0
        self._gpu_path = ""                      # GPU export 대상 파일
        self._gpu_params = {}                    # GPU export 파라미터(지오메트리 등)
        self._url = ""
        self._path = ""
        self._kelvin = None     # None = as-shot 사용
        self._tint = 0.0
        self._asshot = 5500
        self._asshot_tint = 0.0  # as-shot 추정 tint(off-locus 광원 대응)
        self._cam = []          # cam_xyz 3x3 평탄화 (9개)
        self._ref = [1.0, 1.0, 1.0]
        self._cam2srgb = []     # 카메라네이티브->선형 sRGB 매트릭스 평탄화 (9개)
        self._counter = 0
        self._curve_url = "image://curve/c?v=0"
        self._curve_counter = 0
        self._export_status = ""
        self._load_error = ""         # 디코드 실패 시 사용자 안내(빈 문자열=정상)
        self._export_progress = 0.0   # CPU export 진행률(0..1). 워커가 _exportProgressSig 로 갱신.
        self._exporting = False
        self._wall_panels = [None, None, None]   # 배경화면 패널 렌더 결과(uint8) 슬롯별 보관
        # 슬립 방지 2계층: export=단일 렌더 구간(Python), ui=배치/배경화면 전체 구간(QML 상태머신).
        # OR 로 합산 — 배치 중 파일 사이 로드/마스킹 갭에서도 ui 홀드가 유지돼 끊기지 않는다.
        self._keep_awake_export = False
        self._keep_awake_ui = False
        self._keep_awake_cur = False
        self._screen_w, self._screen_h = 3840, 2160   # main 의 _refresh_cm 이 실제값으로 갱신
        self._exif_fields = []      # [{"label","value"}, ...] 패널용
        self._exif_summary = ""     # 오버레이용 2줄 요약
        # 지오태그 — `(lat, lon, alt|None)` 십진 도, 없으면 None. ★**룩이 아니라 사진별
        # 메타데이터다**(크롭·스탬프 텍스트와 같은 등급): 셰이더 uniform 이 0개고 레시피에도
        # 안 실린다. 원본 RAW 는 절대 건드리지 않고, 사이드카에 저장돼 export JPEG 로만 나간다.
        self._gps = None
        self._gps_src = ""          # "map" / "gpx" / "exif" — 어디서 온 좌표인지(표시용)
        # ---- Photo map: 폴더 전체의 좌표 — 읽기 전용이고 아무것도 안 쓴다 ----
        self._map_raw = {}          # {abs_path: (lat, lon)} — 워커가 디스크에서 읽은 생값
        self._map_groups = []       # 좌표별 스택(QML 이 보는 면)
        self._map_folder = ""       # 이 결과가 어느 폴더의 것인가(다른 폴더로 옮겼을 때 어긋남 방지)
        self._map_total = 0         # 그 폴더의 사진 수(커버리지 분모)
        # ★'지금 훑고 있는 폴더'를 따로 든다. 예전엔 스캔 중임을 `_map_folder=""` 로 표현했는데,
        #   그러면 같은 폴더의 감시 재스캔이 '폴더가 바뀌었다'로 읽혀 **진행 중인 스캔을 죽였다**
        #   (지도가 "위치 없음"으로 굳는다). 연타 중복 실행·스캔 중 쓰기 유실도 같은 뿌리였다.
        self._map_scanning = ""
        self._map_paths = set()     # 이번 스캔의 대상(normcase) — 짝 JPEG 은 여기 없다
        self._map_pending = {}      # 스캔 중 들어온 좌표 쓰기 {path: (lat,lon)|None} → 결과에 합침
        self._map_busy = False
        self._map_seq = 0
        self._stamp_text = ""       # 날짜 스탬프 텍스트 ('YY MM DD)
        self._stamp_url = "image://stamp/s?v=0"
        self._stamp_counter = 0
        self._stamp_wr = 0.0        # 스프라이트 (W,H)/짧은변 비율 — QML 오버레이 크기 산출용
        self._stamp_hr = 0.0
        self._stamp_bleed = 0.0     # 글로우 여유 변화분/짧은변 — ⚠️**wr/hr 과 반드시 같은 세대**
        self._stamp_seq = 0         # 스프라이트 워커 세대(늦게 온 결과 버리기)
        self._stamp_busy = False    # 워커 1개만 — 그 사이 들어온 요청은 _stamp_job 으로 코얼레싱
        self._stamp_job = None      # 대기 중인 최신 파라미터(None=없음)
        self._stamp_rot = 0         # 촬영 방향(센서→업라이트 CW 회전, 0/90/180/270) — 데이트백 배치
        self._stamp_font = "7c_bold"   # 데이트백 폰트 방식(date_stamp.STYLES 키)
        self._stamp_size = 0.032       # 데이트백 크기 = 숫자높이/짧은변 비율(슬라이더, date_stamp.DEFAULT_SIZE_FRAC)
        self._stamp_margin = 0.05      # 데이트백 여백 = 코너 안쪽 여백/짧은변 비율 — 슬라이더(date_stamp.MARGIN_FRAC)
        # 각인 색(= date_stamp.DEFAULT_COLOR). ⚠️**소문자 #rrggbb 표준 표기**로 둔다 —
        # setStampColor 가 정규화하므로, 초기값만 표기가 다르면 '손대지 않은 사진'과
        # '기본값으로 리셋한 사진'의 editParams 문자열이 달라져 레시피 룩 지문이 갈린다.
        self._stamp_color = "#ff8a29"
        self._stamp_glow = 1.0         # 글로우 밝기(헤일로 가중 배율)
        self._stamp_spread = 1.0       # 글로우 영역(헤일로 반경 배율)
        self._stamp_prefs_cache = None  # 스탬프 '내 기본값' 캐시(_stamp_prefs)
        self._stamp_grain_src = 0.0    # 스탬프 그레인 소스 = 전체 grainAmt(QML 이 push) — 스탬프는 사진 필름 그레인에 연동
        self._proxy_w = 0           # 마지막 프록시 크기(스탬프 레이어 재렌더용)
        self._proxy_h = 0
        self._histogram = []        # 256-bin 휘도 히스토그램(0..1 정규화)
        self._proxy_small = None    # 히스토그램 재계산용 축소 프록시(float32 0..1)
        self._lut_cache = {}        # simKey -> (lut_arr, n)
        self._sim_key = "identity"  # 현재 필름시뮬 키(QML setFilmSim 이 알려줌)
        self._sim_strength = 1.0    # 현재 필름시뮬 강도
        self._sim_exp_ev = 0.0      # 필름시뮬 보정 노출(EV) — pipeline.film_sim_ev
        self._clip_level = 1.0      # 센서 포화 레벨(scene-linear) — raw_loader.clip_level
        self._auto_exp = True       # 자동노출(임베드 JPEG 중앙값 매칭) on/off — 사진별
        self._auto_ev = 0.0         # 실제로 적용된 자동노출(EV) — UI 표시용
        self._lens = True           # 렌즈 보정 on/off (RAF 내장 샷별 프로파일)
        self._busy = False          # 디코딩 진행 중(스피너)
        self._render_seq = 0        # 비동기 렌더 순번(오래된 결과 폐기용)
        self._folder = ""           # 좌측 file explorer 현재 폴더
        # 캡션(Florence-2): 폴더당 .filmrawsterycaptions.json {파일명: {상세도: 문장}}
        self._captions = {}
        self._captions_folder = ""
        self._search = ""            # 탐색기 캡션 검색어(소문자)
        self._search_tokens = []     # 토큰화된 검색어(접두 일치용)
        self._kw_index = {}          # 워드클라우드 역인덱스 {내용어: [사진경로...]} — ☁ 열 때 구축
        self._kw_index_liked = {}    # 좋아요 사진만의 역인덱스(♥ 그룹용) — 같은 패스로 구축
        self._index_seq = 0          # 폴더 배치 인덱싱 순번(취소=증가)
        self._index_busy = False
        self._index_done = 0
        self._index_total = 0
        self._index_status = ""
        self._index_folder = ""      # 현재 배치가 인덱싱 중인 폴더(진행 표시를 이 폴더에만 연동)
        self._caption_lock = threading.Lock()   # 워커(생성)↔메인(표시/편집) 동시 접근 보호
        self._caption_busy = False
        self._caption_status = ""
        self._caption_level = 0     # 상세도 콤보 기본값 = Short(0)
        self._caption_model_ready = False   # 모델 파일 존재 캐시(True 후엔 재검사 생략)
        self._caption_enabled = True        # 오버레이 표시 중일 때만 자동 생성(C 토글 연동)
        # ⚠️캡션 재평가 시그널은 imageChanged 체인이 아니라 fresh_load 블록에서 직접 발화 —
        # imageChanged 는 _ui_path 갱신 *전*에 emit 되어 이전 사진 기준으로 읽혀버림
        # (사이드카 저장 캡션이 로드 시 표시 안 되던 버그).
        self._files = []            # [{"name","path","isDir"}, ...] 현재 폴더 항목
        self._likes = set()         # 현재 폴더에서 좋아요된 파일명 집합
        self._likes_folder = ""     # _likes 가 속한 폴더(저장 대상 경로)
        self._like_rev = 0          # 좋아요 변경 리비전(QML 바인딩 재평가용)
        self._edited = set()        # 현재 폴더에서 편집 사이드카가 있는 파일명 집합(썸네일 배지)
        self._edited_folder = ""    # _edited 가 속한 폴더
        self._edit_rev = 0          # 편집 사이드카 유무 변경 리비전(QML 바인딩 재평가용)
        self._pending_edits = {}    # 현재 파일의 사이드카 편집(로드 시 1회 읽어 둠, editsForCurrent 반환용)
        self._ui_path = ""          # UI 가 현재 반영 중인 파일(=복원 완료된 파일). 저장은 이 경로 기준.
        self._fresh_load = False    # 새 파일 로드의 첫 디코딩 대기 중(완료 시 editsReady 발화)
        self._rawPeekSig.connect(self._on_raw_peek)
        self._renderReady.connect(self._on_render_ready)
        self._fullDecoded.connect(self._on_full_decoded)
        self._nrFullSig.connect(self.nrFullChanged)
        self._exportStatusSig.connect(self._set_export_status)
        self._skyReady.connect(self._on_sky_ready)
        self._segStatusSig.connect(self._on_seg_status)
        self._segDlSig.connect(self._on_seg_dl)
        self._depthAutoSig.connect(self._on_depth_auto)
        self._modelDlSig.connect(self._on_model_dl)
        self._facesReady.connect(self._on_faces_ready)
        self._exportProgressSig.connect(self._on_export_progress)
        self._keepAwakeSig.connect(self._apply_keep_awake)
        self._hazeReady.connect(self._on_haze_ready)
        self._nrReady.connect(self._on_nr_ready)
        self._mistReady.connect(self._on_mist_ready)
        self._aiNrStatusSig.connect(self._on_ai_nr_status)
        self._aiNrDlSig.connect(self._on_ai_nr_dl)
        self._aiNrInitSig.connect(self._on_ai_nr_init)
        self._stampSpriteSig.connect(self._on_stamp_sprite)
        self._shotInfoSig.connect(self.wallShotsChanged)   # 워커 → GUI 스레드에서 알림
        self._shot_cache = {}        # 경로 -> (촬영정보 1줄, 촬영월) — 배경화면 미리보기용
        self._shot_pending = set()   # 워커가 읽는 중인 경로(중복 스레드 방지)
        self._updateSig.connect(self._on_update_found)
        self._folderScanSig.connect(self._on_folder_scanned)
        self._indexProgressSig.connect(self._on_index_progress)
        self._mapScanSig.connect(self._on_map_scanned)
        self._scan_seq = 0            # 폴더 스캔 순번(빠른 탐색 시 오래된 결과 폐기)
        self._skip_rescan_once = False  # 우리 자신의 사이드카 저장으로 인한 watcher 재스캔 1회 무시
        # 현재 폴더 자동 감시: 디렉터리 변화 -> 디바운스 -> 재스캔(변경분 있을 때만 갱신)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(400)   # 연속 변화/중복 이벤트 합치기
        self._rescan_timer.timeout.connect(self._do_auto_rescan)
        # 앱 설정은 prefs.json(사용자 데이터 폴더) — 레지스트리는 쓰지 않는다(위 주석 참조).
        # wallpaper 그룹만 별도 파일(wallpaper.json)이고, 그 이관도 별도 함수가 담당한다.
        self._settings = QSettings("FilmRawstery", "FilmRawstery")   # wallpaper 이관 전용
        # 마지막으로 저장에 쓴 export 확장자(png/jpg/tif). 제안 파일명·defaultSuffix·
        # name filter 가 모두 이 값을 따라 서로 어긋나지 않게 한다.
        _ext = str(pref_get("export", "lastExt", "png") or "png").lower()
        self._export_ext = _ext if _ext in _EXPORT_EXTS else "png"
        # 나머지 export 옵션도 함께 기억한다 — "파일 크기·형식을 미리 정해두고 export
        # 버튼만 누르고 싶다"는 피드백. 저장소는 prefs.json(모듈 상단 pref_get/pref_set).
        self._export_edge = self._sane_export_edge(pref_get("export", "lastEdge", 0))
        self._export_render = 1 if str(pref_get("export", "lastRender", 0)) == "1" else 0
        self._export_16bit = str(pref_get("export", "last16Bit", False)).lower() == "true"
        # 원본 EXIF 의 GPS 를 export 에 실을지. **기본 ON** — 지도 뷰가 동작하려면 필요하고,
        # 끄는 쪽이 '기능을 포기하는' 선택이라 기본값이 될 수 없다. 끄면 위치만 빠지고 나머지
        # EXIF(카메라·렌즈·촬영일)는 그대로 나간다.
        # ⚠️사용자가 Location 탭에서 **직접 붙인** 좌표는 이 설정과 무관하게 항상 나간다
        #   (붙이는 행위 자체가 의사표시다 — `exif_pass.build_app1` 의 우선순위).
        self._export_keep_gps = str(pref_get("export", "keepGps", True)).lower() != "false"
        # 대화상자별 마지막 폴더 — **목적마다 따로** 기억한다(`_DIALOG_FOLDER_KEYS`).
        # export·배경화면·배치 목적지는 서로 다른 곳에 모으는 게 보통이라, 한 캐시를 공유하면
        # 매번 남의 트리에서 열린다(사용자 요청, 2026-09-03).
        # ⚠️export 는 예전 키(`export/lastFolder`)에 이미 값이 있다 → 1회 승계한다.
        self._dlg_folders = {k: str(pref_get("dialogs", k, "") or "")
                             for k in self._DIALOG_FOLDER_KEYS}
        if not self._dlg_folders["export"]:
            legacy = str(pref_get("export", "lastFolder", "") or "")
            if legacy:
                self._dlg_folders["export"] = legacy
                pref_set("dialogs", "export", legacy)
        # 배경화면 설정은 레지스트리가 아니라 사용자 데이터 폴더의 JSON(_wall_prefs).
        self._wall_prefs_cache = None
        self._wall_prefs_timer = QTimer(self)
        self._wall_prefs_timer.setSingleShot(True)
        self._wall_prefs_timer.setInterval(500)   # 타이핑 중 연속 저장 합치기
        self._wall_prefs_timer.timeout.connect(self._flush_wall_prefs)

    def _update_watcher(self, folder: str) -> None:
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if folder and Path(folder).is_dir():
            self._watcher.addPath(folder)

    def _on_dir_changed(self, _path: str) -> None:
        self._rescan_timer.start()            # 디바운스(재시작)

    def _do_auto_rescan(self) -> None:
        if self._skip_rescan_once:
            self._skip_rescan_once = False   # 우리 좋아요/사이드카 저장이 유발한 재스캔 1회 무시(불필요 스핀업 방지)
            return
        if self._folder:
            self._scan_folder(self._folder, force=False)

    # ---------- 좋아요(셀렉트) 영속화: 폴더당 .filmrawsterylikes.json ----------
    @staticmethod
    def _likes_path(folder: str) -> Path:
        return Path(folder) / LIKES_FILE_NAME

    @staticmethod
    def _load_likes(folder: str) -> set:
        """폴더의 .filmrawsterylikes.json 에서 좋아요(True)된 파일명 집합을 읽음(없으면 빈 집합)."""
        try:
            _migrate_sidecars(folder)   # 구 .camraw* → 신 이름 1회 이동
            p = Controller._likes_path(folder)
            if not p.is_file():
                return set()
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {name for name, liked in data.items() if liked}
        except Exception:
            return set()

    @staticmethod
    def _save_likes(folder: str, liked_set: set) -> None:
        """좋아요 집합을 {파일명: true} JSON 으로 폴더에 저장(원자적 쓰기)."""
        try:
            data = {name: True for name in sorted(liked_set)}
            _atomic_write_json(Controller._likes_path(folder), data)
        except Exception as exc:
            print(f"[likes] 저장 실패: {exc}")

    @Slot(str, result=bool)
    def isLiked(self, path: str) -> bool:  # noqa: N802 (QML 슬롯)
        # 캐시(self._likes)는 탐색기 폴더 전용 — 탐색기/프리뷰가 그 폴더면 O(1).
        # 다른 폴더(프리뷰가 외부 폴더일 때) 질의는 디스크에서 읽어 배지 오염 방지
        # (파일명만 비교하면 DSCF####.RAF 가 폴더마다 충돌).
        if str(Path(path).parent) == self._likes_folder:
            return Path(path).name in self._likes
        return Path(path).name in self._load_likes(str(Path(path).parent))

    @Slot(str)
    def toggleLike(self, path: str) -> None:  # noqa: N802 (QML 슬롯)
        """파일의 좋아요 상태를 토글하고 즉시 폴더 JSON 에 저장(크래시 안전).
        ⚠️탐색기 폴더 캐시(self._likes)는 절대 다른 폴더로 바꾸지 않는다 — 예전엔
        프리뷰가 외부 폴더 파일을 토글하면 캐시가 그 폴더로 스왑돼, likesChanged 후
        탐색기 하트가 통째로 다른 폴더 기준으로 오염됐음."""
        if not path:
            return
        name = Path(path).name
        folder = str(Path(path).parent)
        if folder == self._likes_folder:
            s = self._likes                       # 탐색기 폴더 = 캐시 직접 갱신
        else:
            s = self._load_likes(folder)          # 외부 폴더 = 별도 로드(캐시 불변)
        s.discard(name) if name in s else s.add(name)
        self._save_likes(folder, s)
        if folder == self._folder:
            self._skip_rescan_once = True   # 이 저장이 watcher 를 깨워 폴더 재스캔(드라이브 스핀업)하는 것 방지
        self._like_rev += 1
        self.likesChanged.emit()

    # ---------- 캡션 기반 폴더 검색 ----------
    # 저장된 캡션(.filmrawsterycaptions.json) 텍스트를 토큰화해 탐색기 필터(explorerFiles)에서
    # 대조. 인덱싱된(캡션 저장된) 파일만 검색 대상 — 미인덱싱은 on-demand/배치로 채워짐.
    @Slot(str)
    def setSearchQuery(self, q: str) -> None:  # noqa: N802 (QML 슬롯)
        import re
        q = (q or "").strip().lower()
        toks = [t for t in re.split(r"[^a-z0-9]+", q) if t]
        if q == self._search and toks == self._search_tokens:
            return
        self._search = q
        self._search_tokens = toks
        self.searchChanged.emit()

    def _get_search_query(self) -> str:
        return self._search

    searchQuery = Property(str, _get_search_query, notify=searchChanged)

    @Slot(str, result=bool)
    def matchesSearch(self, path: str) -> bool:  # noqa: N802 (QML 슬롯)
        """파일의 캡션 **내용어(해시태그 기준)** 에 검색 토큰이 (접두)일치하면 True. 빈 검색=전체
        True, 미인덱싱(캡션 없음)=False. 모든 상세도 텍스트를 합쳐 hashtags.keywords 로 추출
        (불용어/숫자/3글자미만 제외 — 표시 해시태그와 동일 규칙). 저장 원문 그대로라 재인덱싱 불요."""
        if not self._search_tokens:
            return True
        import hashtags
        with self._caption_lock:
            self._ensure_caption_cache(self._folder)   # 탐색기 폴더 기준(경로 구분자 파싱 회피)
            entry = self._captions.get(Path(path).name)
        if not entry:
            return False
        words = set(hashtags.keywords(" ".join(str(v) for v in entry.values())))
        return all(any(w.startswith(tok) for w in words) for tok in self._search_tokens)

    def _get_indexed_count(self) -> int:
        """현재 폴더에서 캡션(=검색 인덱스)이 하나라도 저장된 파일 수. captionChanged 로 갱신되며,
        배치 중에는 QML 라벨이 indexDone(indexChanged) 을 함께 참조해 실시간 재평가."""
        with self._caption_lock:
            self._ensure_caption_cache(self._folder)
            caps = self._captions
        return sum(1 for f in self._files
                   if not f.get("isDir") and not f.get("paired") and caps.get(f.get("name")))

    indexedCount = Property(int, _get_indexed_count, notify=captionChanged)

    def _get_photo_count(self) -> int:
        # 짝 JPEG(paired)은 세지 않는다 — '이 폴더의 사진 수'는 파일 수가 아니라 사진 수여야
        # 한다(RAW+JPEG 동시기록 폴더에서 1000 이 아니라 503). 펼침 토글과 무관하게 고정.
        return sum(1 for f in self._files if not f.get("isDir") and not f.get("paired"))

    photoCount = Property(int, _get_photo_count, notify=folderChanged)

    def _get_folder_has_pairs(self) -> bool:
        """이 폴더에 RAW+JPEG 동시기록 짝이 있나 — 있을 때만 펼치기 토글을 노출한다
        (이미지 전용/RAW 전용 폴더에서는 쓸모없는 버튼이라 아예 안 보이게)."""
        return any(f.get("paired") for f in self._files)

    folderHasPairs = Property(bool, _get_folder_has_pairs, notify=folderChanged)

    def _build_kw_index(self) -> dict:
        """현재 폴더의 역인덱스 {내용어: [사진경로...]} 구축 후 self._kw_index 에 캐시.
        ☁ 열 때 1회 패스로 만들어(≈62ms/999장) folderKeywords·filesWithKeyword 가 공유 →
        호버 조회가 O(1)(희소 단어도 즉시). 캡션당 단어는 set 으로 중복 제거(count=사진 수)."""
        import hashtags
        with self._caption_lock:
            self._ensure_caption_cache(self._folder)
            caps = dict(self._captions)          # 스냅샷(락 밖에서 집계)
        likes = self._likes if self._likes_folder == self._folder else set()
        idx = {}
        idx_liked = {}
        for f in self._files:
            if f.get("isDir") or f.get("paired"):   # 짝 JPEG 은 같은 사진 — 워드클라우드 이중 계수 방지
                continue
            name = f.get("name")
            entry = caps.get(name)
            if not entry:
                continue
            path = f.get("path")
            is_liked = name in likes
            for w in set(hashtags.keywords(" ".join(str(v) for v in entry.values()))):
                idx.setdefault(w, []).append(path)
                if is_liked:
                    idx_liked.setdefault(w, []).append(path)
        self._kw_index = idx
        self._kw_index_liked = idx_liked
        return idx

    @Slot(int, result="QVariantList")
    def folderKeywords(self, top: int = 60):  # noqa: N802 (QML 슬롯)
        """현재 폴더 내용어 빈도 상위 top개 → [{word, count}] (count=그 단어가 나온 사진 수).
        워드 클라우드용 — 역인덱스를 재구축(현재 폴더 반영)하고 그 크기로 순위 산출."""
        idx = self._build_kw_index()
        ranked = sorted(idx.items(), key=lambda kv: len(kv[1]), reverse=True)[:max(1, int(top))]
        return [{"word": w, "count": len(paths)} for w, paths in ranked]

    @Slot(int, result="QVariantList")
    def likedKeywords(self, top: int = 40):  # noqa: N802 (QML 슬롯)
        """좋아요된 사진들의 내용어 빈도 상위 top개 → [{word, count}]. ♥ 그룹 표시용(전체 클라우드와
        동일 규칙, 데이터만 좋아요로 한정). folderKeywords 가 만든 liked 서브인덱스 사용(없으면 구축)."""
        if not self._kw_index:
            self._build_kw_index()
        ranked = sorted(self._kw_index_liked.items(), key=lambda kv: len(kv[1]), reverse=True)[:max(1, int(top))]
        return [{"word": w, "count": len(p)} for w, p in ranked]

    @Slot(str, int, int, result="QVariantList")
    def filesWithKeyword(self, word: str, limit: int = 8, roll: int = 0):  # noqa: N802 (QML 슬롯)
        """word 를 포함한 사진 경로(최대 limit개) — 워드클라우드 호버 미리보기용. 역인덱스 O(1) 조회
        (folderKeywords 가 ☁ 열 때 이미 구축). 방어적으로 미구축이면 1회 구축.

        사진이 limit 보다 많으면 앞을 자르지 않고(=먼저 찍은 것만 보이던 문제) 전체에서 무작위
        표본을 뽑아 **원래 순서로 되돌려** 반환한다(그리드가 시간순으로 읽히게).
        시드는 (word, roll) 고정 — 창 리사이즈·재호버로 refreshPreview 가 다시 불려도 같은 표본이라
        썸네일이 튀지 않고, roll 을 올리면(⟳ 버튼) 다른 표본이 나온다."""
        word = (word or "").strip().lower()
        if not word:
            return []
        paths = self._kw_index.get(word)
        if paths is None:
            paths = self._build_kw_index().get(word, [])
        n = max(1, int(limit))
        if len(paths) <= n:
            return list(paths)
        import random
        pick = random.Random(f"{word}/{int(roll)}").sample(range(len(paths)), n)
        return [paths[i] for i in sorted(pick)]

    @Slot(str, result=int)
    def keywordCount(self, word: str) -> int:  # noqa: N802 (QML 슬롯)
        """word 를 포함한 폴더 내 사진 수 — 미리보기 헤더의 '표본 n / 전체 N' 표시용
        (무작위 표본이라 보이는 개수가 전체가 아님을 알려야 함)."""
        word = (word or "").strip().lower()
        if not word:
            return 0
        paths = self._kw_index.get(word)
        if paths is None:
            paths = self._build_kw_index().get(word, [])
        return len(paths)

    @Slot(result="QVariantMap")
    def folderTagStats(self):  # noqa: N802 (QML 슬롯)
        """워드클라우드 헤더 통계 → {photos, indexed, tags, liked}. photos=폴더 사진 수,
        indexed=캡션 저장된 사진 수, tags=고유 내용어 수(역인덱스 크기), liked=좋아요 사진 수.
        ☁ 열 때 folderKeywords 가 이미 역인덱스를 구축하므로 그대로 재사용(없으면 1회 구축)."""
        idx = self._kw_index if self._kw_index else self._build_kw_index()
        with self._caption_lock:
            self._ensure_caption_cache(self._folder)
            caps = self._captions
        photos = 0
        indexed = 0
        for f in self._files:
            if f.get("isDir") or f.get("paired"):   # photoCount 와 같은 기준(사진 수 = 파일 수 아님)
                continue
            photos += 1
            if caps.get(f.get("name")):
                indexed += 1
        liked = len(self._likes) if self._likes_folder == self._folder else 0
        return {"photos": photos, "indexed": indexed, "tags": len(idx), "liked": liked}

    # ---------- 폴더 배치 인덱싱(백그라운드 캡션 생성 → 검색 커버리지) ----------
    # caption-worker 모델을 단일 데몬 큐로 확장: 파일 리스트 직접 순회, 임베드 프리뷰(full RAW
    # 디코드 0)로 GPU 캡션, 파일마다 사이드카 저장(체크포인트=재개). 이미 있는 레벨 캡션은 skip.
    # throttle: 파일 사이 pace + 조작 중(_ui_busy) hold. 취소=seq 증가. 메인 이미지 파이프라인을
    # 건드리지 않아 인덱싱 중에도 편집/브라우징 가능(비블로킹).
    def _caption_input_rgb(self, path: str):
        """RAW 임베드 JPEG → EXIF 회전 → 768² RGB numpy(캡션 입력). full RAW 디코드 없음.
        실패 시 예외. (_caption_worker 의 디코드와 동일 — 배치가 재사용)."""
        import numpy as np
        import caption as cap
        jpeg = embedded_preview_jpeg(path)
        if not jpeg:
            raise RuntimeError("no embedded preview")
        buf = QBuffer()
        buf.setData(jpeg)
        buf.open(QBuffer.OpenModeFlag.ReadOnly)
        with QT_IMG_LOCK:               # 파이썬제 QBuffer 디코드 — 실측 교착의 M쪽 다리(decode_lock)
            reader = QImageReader(buf, b"jpeg")
            reader.setAutoTransform(True)
            img = reader.read()
        buf.close()
        if img.isNull():
            raise RuntimeError("preview decode failed")
        e = cap.INPUT_EDGE
        img = img.scaled(e, e, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        img = img.convertToFormat(QImage.Format.Format_RGB888)
        return np.frombuffer(img.constBits(), np.uint8).reshape(
            e, img.bytesPerLine())[:, : e * 3].reshape(e, e, 3).copy()

    @Slot("QVariantList", bool)
    def startFolderIndex(self, paths, quiet: bool = False) -> None:  # noqa: N802 (QML 슬롯)
        """paths 를 현재 상세도 캡션으로 배치 인덱싱(이미 있으면 skip=재개). quiet=저부하(pace↑).
        데몬 스레드 — UI 비블로킹. 모델 미보유 시 다운로드(배치=명시 실행이라 허용)."""
        if self._index_busy or not paths:
            return
        plist = [str(p) for p in paths]
        self._index_seq += 1
        seq = self._index_seq
        self._index_busy = True
        self._index_done = 0
        self._index_total = len(plist)
        self._index_status = "Starting…"
        self._index_folder = self._folder     # 이 배치가 속한 폴더(진행 표시 연동용)
        self.indexChanged.emit()
        pace = 0.4 if quiet else 0.08   # 파일 사이 양보(발열/UI). quiet=조용·시원(느림)
        threading.Thread(target=self._index_worker,
                         args=(seq, plist, int(self._caption_level), pace), daemon=True).start()

    @Slot()
    def cancelFolderIndex(self) -> None:  # noqa: N802 (QML 슬롯)
        """진행 중 인덱싱 취소 — seq 증가로 워커가 다음 파일 경계에서 중단, busy 즉시 해제."""
        if not self._index_busy:
            return
        self._index_seq += 1        # 워커 루프가 seq 불일치로 중단(다음 경계)
        self._index_busy = False
        self._index_status = "Cancelled"
        self._skip_rescan_once = False
        self._scan_folder(self._folder, force=False)   # 취소 시점까지 추가된 파일 반영
        self.indexChanged.emit()

    def _index_worker(self, seq, paths, level, pace) -> None:
        import time
        import caption as cap
        tasks = ("<CAPTION>", "<DETAILED_CAPTION>", "<MORE_DETAILED_CAPTION>")
        task = tasks[max(0, min(2, level))]
        key = self._CAPTION_KEYS[max(0, min(2, level))]
        total = len(paths)
        done = 0
        try:
            if not cap.is_ready():   # 모델 다운로드(배치=명시 실행) — 진행률 표시
                cap.ensure_model(lambda v: self._indexProgressSig.emit(
                    (seq, 0, total, f"Downloading model… {int(v * 100)}%")))
                self._caption_model_ready = True
            # 재개 효율: 이미 이 레벨 캡션이 있는 파일은 미리 제외 → 스킵에 pace/추론 0.
            # (dict 조회만이라 수백 개도 즉시. 예: 300/500 완료분 재개 시 대기 없이 200만 처리.)
            todo = []
            for path in paths:
                p = Path(path)
                with self._caption_lock:
                    self._ensure_caption_cache(str(p.parent))
                    if not bool((self._captions.get(p.name) or {}).get(key)):
                        todo.append(path)
            done = total - len(todo)                       # 이미 완료분(진행률 시작점)
            self._indexProgressSig.emit((seq, done, total, "Indexing…"))
            for path in todo:
                if seq != self._index_seq:
                    break                                  # 취소
                # 이미지 로드/익스포트/조작 중엔 일시정지 — 배치 CPU 추론이 인터랙티브
                # 작업과 겹쳐 UI 가 버벅이는 것 방지(CPU 오버구독 완화).
                while ((self._ui_busy or self._busy or self._exporting)
                       and seq == self._index_seq):
                    time.sleep(0.1)
                p = Path(path)
                folder = str(p.parent)
                try:
                    # cpu=True: 배치는 CPU 전용 세션 — GPU 는 프리뷰/편집 전용으로 두어
                    # DirectML VRAM 경합(동시 이미지 로드 시) 네이티브 크래시를 원천 차단.
                    text = cap.generate(self._caption_input_rgb(path), task, cpu=True).strip()
                    if text:
                        with self._caption_lock:
                            self._ensure_caption_cache(folder)
                            entry = dict(self._captions.get(p.name) or {})
                            entry[key] = text
                            self._captions[p.name] = entry
                            snapshot = dict(self._captions)   # 락 안에선 스냅샷만
                        if folder == self._folder:
                            self._skip_rescan_once = True   # 디스크 쓰기 전 설정(watcher 재스캔 방지)
                        # 쓰기는 락 밖에서 — GUI 스레드(indexedCount/matchesSearch 등)가 같은 락에
                        # 디스크 I/O 동안 막히지 않게. 파일마다 저장=체크포인트(재개).
                        self._save_captions(folder, snapshot)
                except Exception as exc:
                    print(f"[index] {p.name} 실패(건너뜀): {exc}")
                done += 1
                self._indexProgressSig.emit((seq, done, total, "Indexing…"))
                if pace > 0 and seq == self._index_seq:
                    time.sleep(pace)                       # 파일 사이 양보(발열/UI)
        except Exception as exc:
            print(f"[index] 중단: {exc}")
        finally:
            if seq == self._index_seq:                     # 정상 완료(취소면 seq 바뀜 → cancel 이 해제)
                self._index_busy = False
                self._index_status = f"Indexed {done}/{total}"
                # 배치 중 우리 사이드카 저장이 _skip_rescan_once 로 watcher 재스캔을 억제했으므로,
                # 완료 시 강제 재스캔 → 배치 도중 폴더에 추가된 파일을 목록/카운트에 반영.
                self._skip_rescan_once = False
                self._scan_folder(self._folder, force=False)
                self.indexChanged.emit()
                self.captionChanged.emit()                 # indexedCount/캡션바 갱신

    @Slot(object)
    def _on_index_progress(self, payload) -> None:
        seq, done, total, status = payload
        if seq != self._index_seq:
            return                                          # 취소된 이전 실행 → 폐기
        self._index_done = done
        self._index_total = total
        self._index_status = status
        self.indexChanged.emit()

    def _get_index_busy(self) -> bool:
        return self._index_busy

    indexBusy = Property(bool, _get_index_busy, notify=indexChanged)

    def _get_index_status(self) -> str:
        return self._index_status

    indexStatus = Property(str, _get_index_status, notify=indexChanged)

    def _get_index_progress(self) -> float:
        return (self._index_done / self._index_total) if self._index_total else 0.0

    indexProgress = Property(float, _get_index_progress, notify=indexChanged)

    def _get_index_done(self) -> int:
        return self._index_done

    indexDone = Property(int, _get_index_done, notify=indexChanged)

    def _get_index_total(self) -> int:
        return self._index_total

    indexTotal = Property(int, _get_index_total, notify=indexChanged)

    def _get_index_folder(self) -> str:
        return self._index_folder

    indexFolder = Property(str, _get_index_folder, notify=indexChanged)

    # ---------- 캡션(Florence-2) 영속화: 폴더당 .filmrawsterycaptions.json ----------
    # 좋아요와 동일 패턴({파일명: {상세도키: 문장}}, 변경 즉시 저장=크래시 안전). 생성은
    # 백그라운드 워커(임베드 JPEG→768² 정방향→caption.generate)라 UI 안 멈춤. 사진 로드
    # 완료(editsReady) 시 현재 상세도의 저장본이 없으면 자동 생성 → 이미지 하단 캡션 바
    # 표시. 상세도(콤보) 전환도 저장본 없으면 자동 생성(있으면 즉시 표시). 자동 감시 폴더의
    # json 생성/수정은 likes 와 같은 이유(목록 불변)로 재스캔 깜빡임 없음.
    _CAPTION_KEYS = ("short", "detailed", "paragraph")   # 콤보 인덱스 0/1/2 ↔ 사이드카 키

    @staticmethod
    def _captions_path(folder: str) -> Path:
        return Path(folder) / CAPTIONS_FILE_NAME

    @staticmethod
    def _load_captions(folder: str) -> dict:
        try:
            p = Controller._captions_path(folder)
            if not p.is_file():
                return {}
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            return {}

    @staticmethod
    def _save_captions(folder: str, captions: dict) -> None:
        try:
            data = {k: captions[k] for k in sorted(captions)}
            _atomic_write_json(Controller._captions_path(folder), data)
        except Exception as exc:
            print(f"[caption] 저장 실패: {exc}")

    def _ensure_caption_cache(self, folder: str) -> None:
        """(caption_lock 안에서 호출) 현재 캐시가 다른 폴더면 해당 폴더 json 로드."""
        if folder != self._captions_folder:
            self._captions = self._load_captions(folder)
            self._captions_folder = folder

    def _get_caption(self) -> str:
        path = self._ui_path
        if not path:
            return ""
        p = Path(path)
        key = self._CAPTION_KEYS[self._caption_level]
        with self._caption_lock:
            self._ensure_caption_cache(str(p.parent))
            entry = self._captions.get(p.name)
            return entry.get(key, "") if isinstance(entry, dict) else ""

    def _get_hashtags(self) -> str:
        """현재 캡션 문장의 주요 단어로 만든 해시태그(표시용). 캡션의 순수 파생물이라
        별도 상태/저장 없이 매번 계산 — captionChanged 에 묶여 자동 갱신."""
        import hashtags
        return hashtags.from_caption(self._get_caption(), 15)   # 표시 상위 15개(검색은 무제한)

    def _get_caption_busy(self) -> bool:
        return self._caption_busy

    def _get_caption_status(self) -> str:
        return self._caption_status

    def _get_caption_level(self) -> int:
        return self._caption_level

    def _get_caption_model_ready(self) -> bool:
        """캡션 모델 파일이 로컬에 있는지(다운로드 여부 선택권용 — 없으면 자동 생성을
        하지 않고 캡션 바에 '클릭해서 다운로드' 안내만 표시). True 이후엔 캐시."""
        if not self._caption_model_ready:
            try:
                import caption as cap
                self._caption_model_ready = cap.is_ready()
            except Exception:
                return False
        return self._caption_model_ready

    @Slot(int)
    def setCaptionLevel(self, level: int) -> None:  # noqa: N802 (QML 슬롯)
        """상세도(0=Short/1=Detailed/2=Paragraph) 변경 — 저장본 있으면 즉시 표시,
        없으면 자동 생성."""
        level = max(0, min(2, int(level)))
        if level == self._caption_level:
            return
        self._caption_level = level
        self.captionChanged.emit()
        self._maybe_auto_caption()

    @Slot(bool)
    def setCaptionEnabled(self, on: bool) -> None:  # noqa: N802 (QML 슬롯)
        """캡션 오버레이 토글(C) 연동 — 꺼진 동안엔 로드 시 자동 생성 안 함(연산 낭비
        방지). 다시 켜면 현재 사진 캡션이 없을 때 즉시 이어서 생성."""
        on = bool(on)
        if on == self._caption_enabled:
            return
        self._caption_enabled = on
        if on:
            self._maybe_auto_caption()

    def _maybe_auto_caption(self) -> None:
        """현재 사진·상세도의 저장 캡션이 없으면 백그라운드 생성 시작(있으면 no-op).
        오버레이가 꺼져 있으면(setCaptionEnabled) 안 함. 모델 미다운로드 PC 에서도
        자동 시작 안 함(~1.1GB 는 사용자 선택 — 캡션 바 클릭 = generateCaption 명시
        호출 시에만 다운로드)."""
        if (self._caption_enabled and not self._caption_busy and self._ui_path
                and self._get_caption() == "" and self._get_caption_model_ready()):
            self.generateCaption(self._caption_level)

    @Slot(str)
    def setCaption(self, text: str) -> None:  # noqa: N802 (QML 슬롯)
        """현재 상세도의 캡션 저장(빈 문자열=삭제). 즉시 폴더 json 에 저장."""
        path = self._ui_path
        if not path:
            return
        p = Path(path)
        folder = str(p.parent)
        key = self._CAPTION_KEYS[self._caption_level]
        text = text.strip()
        with self._caption_lock:
            self._ensure_caption_cache(folder)
            entry = dict(self._captions.get(p.name) or {})
            if entry.get(key, "") == text:
                return
            if text:
                entry[key] = text
            else:
                entry.pop(key, None)
            if entry:
                self._captions[p.name] = entry
            else:
                self._captions.pop(p.name, None)
            self._save_captions(folder, self._captions)
        self.captionChanged.emit()

    @Slot(int)
    def generateCaption(self, level: int = 0) -> None:  # noqa: N802 (QML 슬롯)
        """현재 사진의 영어 캡션 생성(level: 0=짧게/1=상세/2=문단). 백그라운드 실행.
        최초 1회는 모델 다운로드(~1.1GB, 진행률=captionStatus)."""
        path = self._ui_path
        if not path:
            return
        # busy 체크-후-설정을 락으로 원자화 — 워커 finally 의 _maybe_auto_caption(워커
        # 스레드)과 메인 스레드 호출이 겹쳐 두 워커가 동시에 도는 레이스 방지.
        with self._caption_lock:
            if self._caption_busy:
                return
            self._caption_busy = True
        self._caption_status = "Preparing…"
        self.captionChanged.emit()
        threading.Thread(target=self._caption_worker,
                         args=(path, int(level)), daemon=True).start()

    def _caption_worker(self, path: str, level: int) -> None:
        import traceback
        try:
            import caption as cap
            tasks = ("<CAPTION>", "<DETAILED_CAPTION>", "<MORE_DETAILED_CAPTION>")
            task = tasks[max(0, min(2, level))]
            # 항상 ensure — 파일이 다 있으면 즉시 통과, legacy(구버전/저장소 models)에만
            # 있으면 사용자 디렉터리로 복사, 아예 없으면(옵트인 클릭) 다운로드.
            downloading = not cap.is_ready()
            last = [-1]

            def prog(v):
                pct = int(v * 100)
                if pct != last[0]:      # 1% 단위로만 시그널(과도 emit 방지)
                    last[0] = pct
                    self._caption_status = (f"Downloading model… {pct}% of ~1.1 GB"
                                            if downloading else f"Preparing model… {pct}%")
                    self.captionChanged.emit()
            cap.ensure_model(prog)
            self._caption_model_ready = True   # 이후 로드부터 자동 캡션 활성
            self._caption_status = "Generating…"
            self.captionChanged.emit()

            import numpy as np
            jpeg = embedded_preview_jpeg(path)
            if not jpeg:
                # 임베드 프리뷰가 없는 RAW(일부 폰 DNG 등) → 캡션 입력 불가. 깨끗이 실패 처리
                # (QBuffer.setData(None) 예외 대신 명시적으로, 무한 재시도 방지).
                raise RuntimeError("no embedded preview for caption input")
            buf = QBuffer()
            buf.setData(jpeg)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            with QT_IMG_LOCK:                # 파이썬제 QBuffer 디코드(decode_lock)
                reader = QImageReader(buf, b"jpeg")
                reader.setAutoTransform(True)  # EXIF 회전 → 정방향 입력(세로사진 정확도)
                img = reader.read()
            buf.close()
            if img.isNull():
                raise RuntimeError("embedded preview decode failed")
            e = cap.INPUT_EDGE
            img = img.scaled(e, e, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            img = img.convertToFormat(QImage.Format.Format_RGB888)
            rgb = np.frombuffer(img.constBits(), np.uint8).reshape(
                e, img.bytesPerLine())[:, : e * 3].reshape(e, e, 3).copy()
            text = cap.generate(rgb, task)
            if not text.strip():
                # 빈 결과를 저장하면 _maybe_auto_caption 가드(캡션=="")가 계속 통과해
                # 같은 사진을 영원히 재추론함 → 실패로 처리(재시도 안 함).
                raise RuntimeError("caption model returned empty text")

            # 저장은 '생성을 시작한 파일·상세도' 기준 — 생성 중 사진/상세도를 바꿔도 안전
            p = Path(path)
            folder = str(p.parent)
            key = self._CAPTION_KEYS[max(0, min(2, level))]
            with self._caption_lock:
                self._ensure_caption_cache(folder)
                entry = dict(self._captions.get(p.name) or {})
                entry[key] = text
                self._captions[p.name] = entry
                self._save_captions(folder, self._captions)
            self._caption_status = ""
            ok = True
        except Exception as exc:
            traceback.print_exc()
            # 사유는 **그 사진이 아직 화면에 있을 때만** 표시한다 — 생성 중에 다른 사진으로
            # 넘어갔으면 남의 실패를 그 사진의 캡션 바에 붙이는 셈이다(위 `_caption_status = ""`
            # 주석과 같은 결함의 다른 경로). 로그(traceback)에는 항상 남는다.
            shown = (os.path.normcase(os.path.abspath(self._ui_path)) if self._ui_path else "")
            same = shown == os.path.normcase(os.path.abspath(path))
            self._caption_status = f"Failed: {exc}" if same else ""
            ok = False
        finally:
            self._caption_busy = False
            self.captionChanged.emit()
            # 생성 중 사진/상세도가 바뀌어 현재 표시분이 아직 없으면 이어서 자동 생성.
            # 실패 시엔 재시도 안 함(무한 루프 방지 — 상태 라벨에 사유 표시).
            if ok:
                self._maybe_auto_caption()

    # ---------- RAW별 편집 영속화: 폴더/.filmrawsteryedits/<파일명>.json (이미지당 사이드카) ----------
    @staticmethod
    def _edits_dir(folder: str) -> Path:
        return Path(folder) / EDITS_DIR_NAME

    @staticmethod
    def _edits_path(folder: str, name: str) -> Path:
        return Controller._edits_dir(folder) / f"{name}.json"

    @staticmethod
    def _read_edits(path: str) -> dict:
        """RAW 경로의 사이드카 편집 dict 를 읽음(없거나 오류면 빈 dict)."""
        try:
            p = Path(path)
            _migrate_sidecars(str(p.parent))   # 구 .camraw* → 신 이름 1회 이동
            ep = Controller._edits_path(str(p.parent), p.name)
            if not ep.is_file():
                return {}
            with open(ep, "r", encoding="utf-8") as f:
                data = json.load(f)
            # top-level 이 dict 가 아니면(손상/수기편집으로 [] 나 숫자 등) 이후 _load 의
            # e.get(...) 가 AttributeError 로 터져 파일이 조용히 안 열림 → 빈 dict 로 폴백.
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _load_edited_names(folder: str) -> set:
        """폴더의 .filmrawsteryedits/ 에 사이드카(<파일명>.json)가 있는 RAW 파일명 집합을 반환.
        썸네일 '편집됨' 배지 표시용(없거나 오류면 빈 집합)."""
        try:
            d = Controller._edits_dir(folder)
            if not d.is_dir():
                return set()
            return {f.name[:-5] for f in d.glob("*.json")}   # "DSCF1.RAF.json" → "DSCF1.RAF"
        except Exception:
            return set()

    @Slot(str, result=bool)
    def hasEdits(self, path: str) -> bool:  # noqa: N802 (QML 슬롯)
        """파일에 저장된 편집 사이드카가 있는지. 썸네일 배지용.
        캐시(_edited)는 탐색기 폴더 전용 — 다른 폴더 질의는 사이드카 존재를 직접 확인
        (파일명만 비교하면 폴더 간 DSCF####.RAF 충돌)."""
        p = Path(path)
        if str(p.parent) == self._edited_folder:
            return p.name in self._edited
        return Controller._edits_path(str(p.parent), p.name).is_file()

    @Slot("QVariantMap")
    def saveEdits(self, params) -> None:  # noqa: N802 (QML 슬롯)
        """UI 가 반영 중인 파일(_ui_path)의 편집을 사이드카 JSON 으로 저장. 크래시 안전.
        ⚠️ self._path 가 아니라 _ui_path 기준 — 새 파일 로드 중에는 _path 가 이미 바뀌었지만
        UI/editParams 는 아직 이전(반영 완료된) 파일을 나타내므로, 엉뚱한 파일에 덮어쓰기 방지."""
        if not self._ui_path:
            return
        try:
            p = Path(self._ui_path)
            d = self._edits_dir(str(p.parent))
            d.mkdir(parents=True, exist_ok=True)
            data = {k: params[k] for k in params}   # QVariantMap -> dict
            data["appVersion"] = APP_VERSION        # 이 편집을 만든 앱 버전(추후 지원/디버깅용, 참고용 기록 — 읽어서 되돌리지 않음)
            _atomic_write_json(d / f"{p.name}.json", data)
            self._pending_edits = data               # 현재 파일 캐시 동기화
            # 썸네일 편집 배지 즉시 반영(현재 탐색기 폴더 파일일 때). 집합이라 재추가는 무해.
            if str(p.parent) == self._edited_folder:
                self._edited.add(p.name)
            # ★리비전은 **저장할 때마다** 올린다 — 배지 상태가 안 바뀌어도 올려야 한다.
            #   Wallpaper 패널의 `image://wallthumb/<path>?r=<editsRevision>` 이 이 값으로
            #   QML 이미지 캐시를 무효화하는데, 예전처럼 '사이드카가 처음 생길 때만' 올리면
            #   **이미 편집이 있던 사진**의 크롭·회전을 바꿔도 URL 이 그대로라 QML 이 프로바이더를
            #   다시 부르지 않고, 템플릿 미리보기가 편집 전 프레이밍에 멈춘다(프로바이더 내부
            #   캐시는 사이드카 mtime 이 키라 무죄 — 재요청만 되면 최신이다).
            #   ⚠️저장은 디바운스(editSaveTimer)와 드래그 릴리즈 커밋을 거쳐 제스처당 1회라
            #     이 알림이 프레임마다 돌지 않는다. 다른 소비자(탐색기 파일명 앰버·컨택트 시트
            #     라벨)는 `hasEdits` 집합 조회뿐이라 재평가가 싸다.
            self._edit_rev += 1
            self.editsChanged.emit()
        except Exception as exc:
            print(f"[edits] 저장 실패: {exc}")

    @Slot()
    def deleteEdits(self) -> None:  # noqa: N802 (QML 슬롯)
        """현재 UI 파일(_ui_path)의 편집 사이드카를 삭제(수동 Reset). 캐시/썸네일 배지도 갱신.
        ⚠️ saveEdits 와 동일하게 _ui_path 기준(반영 완료된 파일)."""
        if not self._ui_path:
            return
        p = Path(self._ui_path)
        try:
            ep = self._edits_path(str(p.parent), p.name)
            if ep.is_file():
                ep.unlink()
        except Exception as exc:
            print(f"[edits] 삭제 실패: {exc}")
        self._pending_edits = {}                  # 현재 파일 편집 캐시 비움
        # 썸네일 편집 배지(파일명 앰버) 해제 — 현재 폴더 파일이면 캐시에서 제거
        if str(p.parent) == self._edited_folder:
            self._edited.discard(p.name)
        # 리비전은 저장과 같은 이유로 **항상** 올린다(saveEdits 주석) — Reset 으로 되돌아간
        # 프레이밍도 Wallpaper 미리보기에 반영돼야 한다.
        self._edit_rev += 1
        self.editsChanged.emit()

    # ---------- 레시피 프리셋(.frpreset) — 룩만 저장/공유 + 출처 기록 ----------
    # 프리셋이 담는 키의 **단일 진실원**. 저장 필터·로드 필터·QML 의 '기본값으로 되돌릴 키' 목록이
    # 모두 이 하나를 본다(셋으로 갈라지면 반드시 어긋난다).
    # ⚠️여기 없는 것은 의도적으로 뺀 것이다 — 각각 이유가 다르다:
    #   · temp/tint — WB 는 장면 조명에 따라 사진마다 달라, 남의 레시피가 가져오면 대개 이상해진다
    #   · exposure — WB 와 같은 성격의 **촬영 조건 보정**이다(룩이 아니다). 실측: 사이드카
    #     245개에서 범위 −0.81~+3.00 스톱, 71%가 기본값 아님(WB 는 100%, 대비는 60%).
    #     저노출 프레임을 +3 올려 만든 레시피를 정상 노출 사진에 적용하면 하얗게 날아간다.
    #     ⚠️밝기 성격이 있는 룩은 whites/blacks/커브로 담는다. 옛 레시피의 exposure 값은
    #     read() 가 이 목록으로 걸러 자동으로 무시된다(별도 마이그레이션 불필요)
    #   · 크롭/기하/회전/플립, stampText, dateStamp — 사진별(복사/붙여넣기의 _copyExclude 와 동일)
    #   · maskLayers — 마스크 선택(세그 클래스)과 브러시 획은 그 사진의 구도에 묶여 있다.
    #     숫자 파라미터만 싣는 것은 **더 나쁘다**: 마스크가 없으면 레이어 기여가 0으로 게이팅돼
    #     "슬라이더는 0이 아닌데 효과가 0인" 설명 불가한 상태가 된다
    #   · aiNr — 값이 아니라 부작용이다(수신자 기계에서 117MB 모델 다운로드·모달·ONNX 세션)
    #   · lensCorrection — 그 렌즈·그 샷의 성질이고 끄면 풀 재디코드를 유발한다
    #   · lumaNR/colorNR — 적정량이 그 사진의 ISO·노이즈에 묶여 있다.
    #     ⚠️복사/붙여넣기는 이 둘을 **싣는다**(_copyExclude 에 없음) — 프리셋과 의도적으로 다르다
    #   · simIndex/v — 위치 의존 폴백과 사이드카 스키마 마커
    _PRESET_KEYS = (
        "contrast", "highlights", "shadows", "whites", "blacks",
        "simKey", "simStrength",
        "texture", "clarity", "dehaze", "vibrance", "saturation",
        "hslH", "hslS", "hslL",
        "cgShadowHue", "cgShadowSat", "cgMidHue", "cgMidSat",
        "cgHighHue", "cgHighSat", "cgBalance",
        "vignette",
        "mistAmt", "mistChar", "mistRadius", "mistHi", "mistColor",
        "grainAmt", "grainSize", "grainRough", "grainColor", "grainShape",
        "sharpenAmt", "sharpenRadius", "sharpenDetail", "sharpenMask",
        "curves",
        "stampStyle", "stampSize", "stampMargin", "stampColor", "stampGlow", "stampSpread")

    def _get_preset_keys(self) -> list:
        return list(self._PRESET_KEYS)

    def _get_preset_palette(self) -> list:
        import presets
        return list(presets.PALETTE)

    presetKeys = Property("QStringList", _get_preset_keys, constant=True)
    presetPalette = Property("QStringList", _get_preset_palette, constant=True)

    def _presets_dir(self) -> str:
        """프리셋 폴더. ⚠️app_dirs.user_data_path 는 **최상위 디렉터리만** 만든다."""
        import app_dirs
        d = app_dirs.user_data_path("presets")
        os.makedirs(d, exist_ok=True)
        return d

    # ---------- 레시피 사용자 정렬 ----------
    # ⚠️순서는 **prefs.json** 에 둔다. 레시피 파일에 넣으면 공유받은 파일이 **남의 순서**를
    #   들고 온다 — 순서는 내 로컬 취향이고 레시피의 일부가 아니다.
    # 키는 `id`, 없으면 파일명. (id 를 도입하기 전에 저장된 파일이 실제로 있다.)
    def _order_key(self, d: dict) -> str:
        return str(d.get("id") or "") or ("file:" + Path(str(d.get("file") or "")).name)

    def _preset_order(self) -> list:
        v = pref_get("recipes", "order", [])
        return [str(x) for x in v] if isinstance(v, list) else []

    def _remember_order(self, keys) -> None:
        pref_set("recipes", "order", [str(k) for k in keys])

    @Slot("QVariantList")
    def setPresetOrder(self, keys) -> None:  # noqa: N802 (QML 슬롯)
        """드래그로 바뀐 순서를 저장한다. QML 이 보내는 것은 **화면에 보이는 전체 순서**다."""
        self._remember_order(keys)

    def _prepend_order(self, path: str) -> None:
        """새로 저장·가져온 레시피를 **맨 위**로(사용자 결정). 방금 만든 것이 위치로 드러난다."""
        import presets
        d, err = presets.read(str(path), self._PRESET_KEYS)
        if err:
            return
        d["file"] = str(path)
        k = self._order_key(d)
        order = [x for x in self._preset_order() if x != k]
        self._remember_order([k] + order)

    @Slot(result="QVariantList")
    def presetList(self):  # noqa: N802 (QML 슬롯)
        """저장된 프리셋 목록(이름순). 배지 그리드용 — **edits 는 담지 않는다**(적용할 때만
        loadPreset 으로 읽는다). 읽을 수 없는 파일은 건너뛴다."""
        import presets
        out = []
        for d in presets.listdir(self._presets_dir(), self._PRESET_KEYS):
            row = {k: d[k] for k in ("id", "name", "description", "color", "createdAt",
                                     "appVersion", "source", "file")}
            # 룩 지문 — QML 이 현재 편집값의 지문과 비교해 배지 활성 여부를 정한다(값 기준).
            row["lookHash"] = presets.look_hash(d["edits"], self._PRESET_KEYS)
            row["orderKey"] = self._order_key(d)
            out.append(row)
        # 사용자 정렬 적용. 목록에 없는 레시피(방금 만든 것·폴더에 직접 넣은 것)는 **맨 위**로,
        # 그들끼리는 이름순. presets.listdir 가 이미 이름순이라 stable sort 로 유지된다.
        order = self._preset_order()
        rank = {k: i for i, k in enumerate(order)}
        out.sort(key=lambda r: rank.get(r["orderKey"], -1))
        # 사라진 레시피의 항목은 정리해 목록이 무한히 자라지 않게 한다(파일이 있는 것만 남긴다).
        alive = {r["orderKey"] for r in out}
        pruned = [k for k in order if k in alive]
        if pruned != order:
            self._remember_order(pruned)
        return out

    # 사용자가 대화상자에서 고친 카메라/렌즈를 원래 출처에 덮어쓴다.
    # ⚠️렌즈는 EXIF 로 거의 얻을 수 없다(고정렌즈 바디는 태그를 안 쓰고 MakerNote 는 미파싱).
    #   이 기능의 목적이 "레시피는 장비에 묶여 있다"를 알리는 것인데 렌즈가 늘 비어 있으면
    #   그 목적이 반쪽이라, 손으로 채울 수 있게 했다(사용자 요청).
    _SRC_TEXT_MAX = 60      # 배지 툴팁·배너 한 줄에 들어갈 만큼만

    def _merge_source(self, base: dict, override) -> dict:
        out = dict(base or {})
        for k in ("camera", "lens"):
            if not override or k not in override:
                continue
            v = " ".join(str(override[k] or "").split())      # 공백 정리(줄바꿈·중복 공백)
            out[k] = v[:self._SRC_TEXT_MAX]
        return out

    @Slot(str, str, str, "QVariantMap", "QVariantMap", result=str)
    def savePreset(self, name: str, color: str, description: str, edits,
                   src_override=None) -> str:  # noqa: N802
        """현재 편집의 '룩'만 프리셋으로 저장. 성공 시 파일 경로, 실패 시 "".
        edits 는 QML 이 넘긴 전체 편집값 — 허용 목록으로 걸러서 사진별 값이 새지 않게 한다."""
        import datetime
        import presets
        name = str(name or "").strip()
        if not name:
            return ""
        raw = {k: edits[k] for k in edits}
        keep, err = presets.validate_edits(raw, self._PRESET_KEYS)
        if err:
            print(f"[preset] 저장 거부: {err}")
            return ""
        doc = presets.build(name, str(color or ""),
                            self._merge_source(self.presetSource(), src_override), keep,
                            APP_VERSION, datetime.date.today().isoformat(),
                            str(description or ""))
        try:
            path = presets.write(self._presets_dir(), doc)
        except Exception as exc:
            print(f"[preset] 저장 실패: {exc}")
            return ""
        self._prepend_order(path)          # 새로 만든 것은 맨 위(사용자 결정)
        print(f"[preset] 저장: {path}")
        return path

    @Slot(str, result="QVariantMap")
    def loadPreset(self, file: str):  # noqa: N802 (QML 슬롯)
        """프리셋 1개를 읽어 반환. 실패 시 {"error": 문구} — QML 이 배너로 보여준다.
        ⚠️적용 전에 여기서 검증이 끝나야 한다(QML applyEdits 안에서 던지면 자동저장/undo 가 죽는다)."""
        import presets
        d, err = presets.read(str(file), self._PRESET_KEYS)
        if err:
            print(f"[preset] 읽기 실패 {file}: {err}")
            return {"error": err}
        d["error"] = ""
        return d

    @Slot("QVariantMap", result=str)
    def lookHash(self, edits) -> str:  # noqa: N802 (QML 슬롯)
        """편집값의 룩 지문. 배지가 '이 사진의 룩 == 이 레시피' 를 값으로 판정하는 데 쓴다.

        항상 `_PRESET_KEYS` **전체**로 계산한다. 레시피에 없는 키는 `presets.LOOK_DEFAULTS`
        로 채워지므로(look_hash 안에서), 옛 레시피는 '그 키는 공장 기본값' 으로 해석된다 —
        그게 바로 그 레시피를 적용했을 때 나오는 상태다.
        ⚠️예전에는 '그 레시피가 지정한 키' 로 비교 집합을 좁혔다. 그러면 나중에 추가된 키를
          만져도 배지가 안 꺼져 **거짓을 말한다**(미스트를 추가하며 드러났다)."""
        import presets
        return presets.look_hash({k: edits[k] for k in edits}, self._PRESET_KEYS)

    @Slot(str, "QVariantMap", result=str)
    def updatePresetLook(self, file: str, edits) -> str:  # noqa: N802 (QML 슬롯)
        """기존 레시피의 **룩을 현재 편집값으로 덮어쓴다**. 이름·색·설명은 그대로.

        ⚠️출처(`source`)·`appVersion`·`createdAt` 도 **함께 갱신**한다. 담긴 룩이 바뀌었으니
          그 룩이 어느 장비·어느 버전에서 만들어졌는지도 바뀌는 것이 맞다 — 안 그러면 배너가
          "이 레시피는 X100V 에서 만들어졌다"고 **거짓을 말하게 된다**(이 기능 전체가 그 정직함에
          기대고 있다). 같은 이유로 `createdAt` 은 '지금 담긴 룩'의 날짜로 둔다(그레인 계수처럼
          버전에 따라 결과가 달라지므로 appVersion 과 짝이 맞아야 한다)."""
        import datetime
        import presets
        d, err = presets.read(str(file), self._PRESET_KEYS)
        if err:
            print(f"[preset] 룩 갱신 실패(읽기) {file}: {err}")
            return ""
        raw = {k: edits[k] for k in edits}
        keep, err = presets.validate_edits(raw, self._PRESET_KEYS)
        if err:
            print(f"[preset] 룩 갱신 거부: {err}")
            return ""
        doc = presets.build(d["name"], d["color"], self.presetSource(), keep,
                            APP_VERSION, datetime.date.today().isoformat(),
                            d.get("description", ""), d.get("id", ""))
        try:
            path = presets.write(self._presets_dir(), doc)
        except Exception as exc:
            print(f"[preset] 룩 갱신 실패(쓰기): {exc}")
            return ""
        print(f"[preset] 룩 갱신: {path}")
        return path

    @Slot(str, str, str, str, "QVariantMap", result=str)
    def editPreset(self, file: str, name: str, color: str, description: str,
                   src_override=None) -> str:  # noqa: N802
        """이름/구분색/설명만 수정. **룩과 출처는 그대로 유지**한다 — 룩을 바꾸려면 같은 이름으로
        다시 저장하면 된다(내부 name 이 같으면 덮어쓰는 규약).
        ⚠️이름이 바뀌면 파일명도 바뀌므로 **이전 파일을 지워야** 한다(안 그러면 둘로 늘어난다)."""
        import presets
        name = str(name or "").strip()
        if not name:
            return ""
        d, err = presets.read(str(file), self._PRESET_KEYS)
        if err:
            print(f"[preset] 수정 실패(읽기) {file}: {err}")
            return ""
        doc = presets.build(name, str(color or ""),
                            self._merge_source(d["source"], src_override), d["edits"],
                            d["appVersion"] or APP_VERSION, d["createdAt"],
                            str(description or ""), d.get("id", ""))
        try:
            path = presets.write(self._presets_dir(), doc)
        except Exception as exc:
            print(f"[preset] 수정 실패(쓰기): {exc}")
            return ""
        old = Path(str(file))
        if Path(path) != old and old.is_file():
            try:
                old.unlink()
            except Exception as exc:
                print(f"[preset] 이전 파일 삭제 실패(중복 남음): {exc}")
        print(f"[preset] 수정: {path}")
        return path

    @Slot(str, result=bool)
    def deletePreset(self, file: str) -> bool:  # noqa: N802 (QML 슬롯)
        """⚠️프리셋 폴더 안의 파일만 지운다 — QML 이 넘긴 경로를 그대로 믿지 않는다."""
        try:
            f = Path(str(file)).resolve()
            if f.parent != Path(self._presets_dir()).resolve() or f.suffix.lower() != ".frpreset":
                print(f"[preset] 삭제 거부(폴더 밖): {file}")
                return False
            f.unlink()
            print(f"[preset] 삭제: {f}")
            return True
        except Exception as exc:
            print(f"[preset] 삭제 실패: {exc}")
            return False

    @Slot(QUrl, result=str)
    def importPreset(self, url: QUrl) -> str:  # noqa: N802 (QML 슬롯)
        """공유받은 .frpreset 을 검증한 뒤 프리셋 폴더에 복사. 성공 시 새 경로, 실패 시 "".
        ⚠️파일명은 **검증된 내부 name 에서 다시 파생**한다 — 들어온 파일명을 쓰면
          `name: "../../foo"` 같은 값이 경로 탈출 쓰기가 된다(presets.write 가 담당)."""
        import presets
        src = url.toLocalFile()
        d, err = presets.read(src, self._PRESET_KEYS)
        if err:
            print(f"[preset] 가져오기 거부 {src}: {err}")
            return ""
        doc = presets.build(d["name"], d["color"], d["source"], d["edits"],
                            d["appVersion"] or APP_VERSION, d["createdAt"],
                            d.get("description", ""), d.get("id", ""))
        try:
            path = presets.write(self._presets_dir(), doc)
        except Exception as exc:
            print(f"[preset] 가져오기 실패: {exc}")
            return ""
        self._prepend_order(path)          # 가져온 것도 맨 위
        print(f"[preset] 가져옴: {path}")
        return path

    @Slot(str, QUrl, result=bool)
    def exportPreset(self, file: str, url: QUrl) -> bool:  # noqa: N802 (QML 슬롯)
        """프리셋을 사용자가 고른 위치로 내보낸다(공유용). 내용은 그대로 복사."""
        import shutil
        dst = url.toLocalFile()
        try:
            shutil.copyfile(str(file), dst)
            print(f"[preset] 내보냄: {dst}")
            return True
        except Exception as exc:
            print(f"[preset] 내보내기 실패: {exc}")
            return False

    @Slot(str, result=QUrl)
    def suggestedPresetShareUrl(self, file: str) -> QUrl:  # noqa: N802 (QML 슬롯)
        """Export 대화상자의 제안 파일명 — **공유본에만** 출처를 파일명에 넣는다(presets 주석)."""
        import presets
        d, err = presets.read(str(file), self._PRESET_KEYS)
        if err:
            return QUrl()
        # ⚠️괄호 필수 — 없으면 `(A or B) if cond else ""` 로 묶여, 폴더만 열고 사진을
        #   안 열었을 때 _folder 가 있는데도 빈 문자열이 된다.
        folder = self._folder or (str(Path(self._path).parent) if self._path else "")
        fn = presets.share_filename(d["name"], d["source"], d["createdAt"])
        return QUrl.fromLocalFile(str(Path(folder or self._presets_dir()) / fn))

    @Slot(str, str, str, result=str)
    def batchExportUrl(self, folder_url: str, src_path: str, ext: str) -> str:  # noqa: N802
        """배치 export 대상 파일 URL: <선택 폴더>/<원본이름>_exported.<ext>.
        경로 조립(백슬래시/URL 인코딩)은 Python 이 담당 — QML 문자열 연산의 함정 회피."""
        try:
            folder = QUrl(folder_url).toLocalFile() if folder_url else ""
            name = f"{Path(src_path).stem}_exported.{ext}"
            return QUrl.fromLocalFile(str(Path(folder) / name)).toString()
        except Exception as exc:
            print(f"[batch] URL 조립 실패: {exc}")
            return ""

    @Slot(result="QVariantMap")
    def editsForCurrent(self):  # noqa: N802 (QML 슬롯)
        """현재 파일의 저장된 편집 dict 반환(없으면 빈 dict). _load 에서 읽어둔 캐시 사용."""
        return self._pending_edits

    @Slot("QVariantList")
    def setCurve(self, curves) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 계산한 4개 채널 커브([master, r, g, b], 각 256값)로 LUT 텍스처 갱신.
        마스터→채널 합성을 256×3 LUT 로 구워 R/G/B 열에 저장."""
        import pipeline
        if curves is None or len(curves) < 4:
            return                    # 잘못된 QVariantList → IndexError 로 슬롯 밖 전파 방지
        m, r, g, b = curves[0], curves[1], curves[2], curves[3]
        self._curve_provider.set_lut(pipeline.compose_curves(m, r, g, b))
        self._curve_counter += 1
        self._curve_url = f"image://curve/c?v={self._curve_counter}"
        self.curveChanged.emit()

    @Slot(result=QUrl)
    def suggestedExportUrl(self) -> QUrl:  # noqa: N802 (QML 슬롯)
        """Export 기본 파일명: 원본과 같은 폴더의 '<원본이름>_exported.<마지막 사용 형식>'.

        ⚠️확장자를 png 로 고정하면 안 된다 — 저장 포맷은 `pipeline.save_image` 가 **파일명
        확장자**로 결정하는데, FileDialog 는 같은 객체라 이전에 고른 name filter(예: JPEG)를
        기억한다. 그래서 이름만 png 로 되돌리면 '필터는 JPEG 인데 파일은 PNG' 가 된다
        (사용자 보고: jpg 로 저장한 뒤 다른 사진을 저장하니 png 로 나옴)."""
        if not self._path:
            return QUrl()
        p = Path(self._path)
        name = f"{p.stem}_exported.{self._export_ext}"
        # 마지막으로 저장한 폴더에서 열되, 없거나 사라졌으면 원본 폴더로 폴백한다
        # (외장 드라이브를 뽑은 뒤에도 대화상자가 정상적으로 열려야 한다).
        last = self._dlg_folder("export")
        if last:
            return QUrl.fromLocalFile(str(Path(last) / name))
        return QUrl.fromLocalFile(str(p.with_name(name)))

    # ---------- export 옵션 기억(해상도·렌더·16bit·폴더) ----------
    # ⚠️허용 목록은 QML win.exportEdges 와 같아야 한다 — 어긋나면 저장된 값이 콤보에서
    #   인덱스 -1 이 되어 조용히 Original 로 되돌아간다.
    _EXPORT_EDGES = (0, 4096, 3840, 2560, 2048, 1920, 1280)

    def _sane_export_edge(self, v) -> int:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return 0
        return iv if iv in self._EXPORT_EDGES else 0

    def _get_export_edge(self) -> int:
        return self._export_edge

    def _get_export_render(self) -> int:
        return self._export_render

    def _get_export_16bit(self) -> bool:
        return self._export_16bit

    def _get_export_keep_gps(self) -> bool:
        return self._export_keep_gps

    exportEdge = Property(int, _get_export_edge, notify=exportOptsChanged)
    exportRender = Property(int, _get_export_render, notify=exportOptsChanged)
    export16Bit = Property(bool, _get_export_16bit, notify=exportOptsChanged)
    exportKeepGps = Property(bool, _get_export_keep_gps, notify=exportOptsChanged)

    @Slot("QVariantMap")
    def rememberExportOpts(self, opts: dict) -> None:  # noqa: N802 (QML 슬롯)
        """사용자가 export 옵션을 직접 바꿨을 때 호출(변화 없으면 디스크에 쓰지 않는다)."""
        o = dict(opts or {})
        changed = False
        if "edge" in o:
            v = self._sane_export_edge(o["edge"])
            if v != self._export_edge:
                self._export_edge = v
                pref_set("export", "lastEdge", v)
                changed = True
        if "render" in o:
            v = 1 if int(o["render"] or 0) == 1 else 0
            if v != self._export_render:
                self._export_render = v
                pref_set("export", "lastRender", v)
                changed = True
        if "bit16" in o:
            v = bool(o["bit16"])
            if v != self._export_16bit:
                self._export_16bit = v
                pref_set("export", "last16Bit", "true" if v else "false")
                changed = True
        if "keepGps" in o:
            v = bool(o["keepGps"])
            if v != self._export_keep_gps:
                self._export_keep_gps = v
                pref_set("export", "keepGps", "true" if v else "false")
                changed = True
        if changed:
            self.exportOptsChanged.emit()

    # ---------- 대화상자별 경로 캐시(목적마다 따로) ----------
    # export=Export(파일) · wallpaper=Export Wallpaper(파일) · batch=배치 목적지(폴더).
    # ⚠️Select Folder(사진 폴더)는 여기 없다 — 고르는 순간 탐색기 폴더가 되므로 `currentFolderUrl`
    #   이 곧 그 대화상자의 기억이다(사용자 결정: 현행 유지).
    _DIALOG_FOLDER_KEYS = ("export", "wallpaper", "batch")

    def _dlg_folder(self, key: str) -> str:
        """기억된 폴더(없거나 사라졌으면 "") — 호출부가 각자의 폴백을 쓴다.
        ⚠️존재 검사를 여기서 한다: 외장 드라이브를 뽑은 뒤에도 대화상자는 열려야 한다."""
        d = self._dlg_folders.get(str(key), "")
        try:
            return d if d and Path(d).is_dir() else ""
        except OSError:
            return ""

    @Slot(str, result=str)
    def dialogFolderUrl(self, key: str) -> str:  # noqa: N802 (QML 슬롯)
        """QML 이 `currentFolder` 에 바로 넣을 QUrl 문자열. 기억이 없으면 ""."""
        d = self._dlg_folder(key)
        return QUrl.fromLocalFile(d).toString() if d else ""

    @Slot(str, QUrl)
    def rememberDialogFolder(self, key: str, url: QUrl) -> None:  # noqa: N802 (QML 슬롯)
        """방금 쓴 경로의 **폴더**를 그 대화상자 몫으로 기억한다(파일명은 기억하지 않는다).

        url 은 파일(Export/Wallpaper)일 수도, 폴더(배치 목적지)일 수도 있다 — 폴더면 그대로,
        파일이면 부모를 쓴다. ⚠️저장 직전이라 파일이 아직 없을 수 있어 `is_dir()` 로 가른다."""
        k = str(key)
        if k not in self._DIALOG_FOLDER_KEYS:
            return
        try:
            p = Path(url.toLocalFile())
            d = p if p.is_dir() else p.parent
            if not d.is_dir():
                return
        except OSError:
            return
        folder = str(d)
        if folder == self._dlg_folders.get(k):
            return
        self._dlg_folders[k] = folder
        pref_set("dialogs", k, folder)

    @Slot(QUrl)
    def rememberExportFolder(self, file_url: QUrl) -> None:  # noqa: N802 (QML 슬롯)
        """방금 저장에 쓴 파일의 **폴더**를 기억한다 — 다음 export 대화상자가 그 폴더에서
        열린다(export 전용 폴더에 모으는 흔한 작업 방식). 파일명은 기억하지 않는다."""
        self.rememberDialogFolder("export", file_url)

    # ---------- 마지막 사용 export 형식(확장자) — 이름/필터/defaultSuffix 의 단일 출처 ----------
    def _get_export_ext(self) -> str:
        return self._export_ext

    @Slot(str)
    def setExportExt(self, ext: str) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 name filter 를 바꿨을 때 호출. 영구 저장해 다음 실행에서도 유지."""
        e = str(ext or "").lstrip(".").lower()
        # ⚠️jpeg/tiff 는 jpg/tif 로 접는다. 대화상자의 name filter 목록은 png/jpg/tif 셋뿐이라,
        #   'jpeg' 를 그대로 기억하면 indexOf 가 -1 이 되어 **필터는 PNG 인데 파일명은 .jpeg**
        #   인 모순 상태로 열린다(예전에 사용자 보고로 고친 그 증상).
        e = {"jpeg": "jpg", "tiff": "tif"}.get(e, e)
        if e not in _EXPORT_EXTS or e == self._export_ext:
            return
        self._export_ext = e
        pref_set("export", "lastExt", e)
        self.exportExtChanged.emit()

    def _remember_export_ext(self, path: str) -> None:
        """실제 저장 경로에서 확장자를 기억 — 사용자가 이름을 직접 타이핑한 경우까지 포함."""
        self.setExportExt(os.path.splitext(path)[1])

    # ---------- 슬립 방지: export/배치 중 Windows 시스템 슬립으로 작업이 멈추는 것 방지 ----------
    def _update_keep_awake(self) -> None:
        """메인 스레드 전용(SetThreadExecutionState 가 스레드 귀속). 상태 변화 시에만 호출."""
        want = self._keep_awake_export or self._keep_awake_ui
        if want != self._keep_awake_cur:
            self._keep_awake_cur = want
            _set_keep_awake(want)

    @Slot(bool)
    def _apply_keep_awake(self, on: bool) -> None:
        """단일 렌더 구간 홀드. 워커 finally 는 _keepAwakeSig.emit(False) 로 여기에 큐잉."""
        self._keep_awake_export = on
        self._update_keep_awake()

    @Slot(bool)
    def setKeepAwake(self, on: bool) -> None:  # noqa: N802 (QML 슬롯)
        """배치/배경화면 상태머신용 — 실행 전체 구간(로드/마스킹 갭 포함) 홀드."""
        self._keep_awake_ui = bool(on)
        self._update_keep_awake()

    @Slot(QUrl, "QVariantMap")
    def exportImage(self, file_url: QUrl, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조정값으로 풀해상도 현상 후 파일 저장 (백그라운드 스레드)."""
        if not self._path or self._exporting:
            return
        path = file_url.toLocalFile()
        self._remember_export_ext(path)   # 다음 export 의 제안 이름/필터가 이 형식을 따라간다
        pdict = {k: params[k] for k in params}     # QVariantMap -> 평범한 dict
        pdict["proxyEdge"] = max(self._proxy_w, self._proxy_h)   # 공간 반경 스케일 기준(스냅샷)
        # 하이라이트 디새추 게이트 기준(센서 포화 레벨). ⚠️**여기서** 스냅샷해야 한다 —
        # `_render_array` 는 워커 스레드라 거기서 self._clip_level 을 읽으면 export 중에 다른
        # 사진을 열었을 때 그 사진의 값이 섞인다(`src` 를 스냅샷하는 이유와 똑같다).
        # ⚠️프리뷰가 쓰는 값을 넘기는 것 자체가 목적이다 — render_full 이 자체 계산하면
        # 프록시/풀해상도 게인 차이(실측 1.8786 vs 1.8722, 0.3%)가 `clip_level` 의
        # 불연속(g==PROXY_HEADROOM)을 건드려 게이트가 프리뷰와 export 에서 갈릴 수 있다.
        # 프리뷰·GPU export(셰이더 유니폼)·CPU export 가 이걸로 한 값을 공유한다.
        pdict["clipLevel"] = float(self._clip_level)
        # 요청 시점 스냅샷 — export 중 마스크 변경/이미지 전환과 분리.
        # ⚠️소스 경로/WB 도 반드시 스냅샷: 워커에서 self._path 를 읽으면 export 중 다른
        # 사진을 로드했을 때 '새 사진 + 이전 편집값'이 이전 파일명으로 저장되는 버그.
        src = (self._path, self._kelvin, self._tint)
        sky_masks = list(self._layer_masks)   # 레이어별 마스크 스냅샷(export 는 p["maskLayers"] 조정값과 zip)
        haze = (self._haze_t, list(self._haze_A), self._haze_conf)   # DCP 추정 스냅샷(동일 이유)
        self._exporting = True
        self._apply_keep_awake(True)
        self._export_progress = 0.0
        self.exportProgressChanged.emit()
        self._set_export_status("Exporting… (full resolution, may take tens of seconds)")
        threading.Thread(target=self._do_export, args=(path, pdict, src, sky_masks, haze),
                         daemon=True).start()

    def _render_array(self, params: dict, src, sky_masks, haze):
        """export 렌더 본체(저장 제외) — 워커 스레드에서 호출. uint8/uint16 (H,W,3) 반환."""
        import pipeline
        lut_arr, lut_n = None, 0
        if params.get("lutEnabled", False):
            import lut as lut_mod
            lut_arr, lut_n = load_cube(
                str(lut_mod.lut_path(params.get("simKey", "identity"), LUTS_DIR)))
        ident = [i / 255.0 for i in range(256)]
        curves = params.get("curves") or [ident, ident, ident, ident]
        curve_rgb = pipeline.compose_curves(*curves)
        src_path, src_kelvin, src_tint = src   # 요청 시점 스냅샷(라이브 self._path 금지)
        # 하이라이트 디새추는 센서 클립 보정 → display-referred 소스에선 끈다(셰이더 hlDesat 와 동기).
        params = dict(params)
        params["hlDesat"] = 0.0 if image_loader.is_display_image(src_path) else 1.0
        # ⚠️proxy_edge = 실제 프록시의 긴 변. 기본값 2560 을 그대로 쓰면 프록시가 2560 보다
        #   작은 소스(웹 크기 JPEG 등)에서 공간 반경(블러/샤프닝/NR)이 프리뷰와 어긋난다
        #   — RAW 는 항상 2560 이라 드러나지 않던 문제. 값은 요청 시점 스냅샷(params) 에서
        #   가져온다 — 워커에서 self._proxy_* 를 읽으면 export 중 다른 사진을 로드했을 때 틀어진다.
        return pipeline.render_full(
            src_path, src_kelvin, src_tint, params, lut_arr, lut_n, curve_rgb,
            proxy_edge=int(params.get("proxyEdge", 0) or 0) or 2560,
            bitdepth=int(params.get("bitDepth", 8)), sky_masks=sky_masks,
            progress=lambda f: self._exportProgressSig.emit(f), haze=haze)

    def _do_export(self, path: str, params: dict, src, sky_masks=None, haze=None) -> None:
        try:
            import pipeline
            arr = self._render_array(params, src, sky_masks, haze)
            # 지오태그는 픽셀과 무관한 메타데이터라 렌더가 아니라 저장 단계에서 실린다(JPEG 만).
            # ⚠️`src[0]` = 요청 시점 스냅샷 경로. 워커에서 `self._path` 를 읽으면 export 중에
            #   다른 사진을 열었을 때 **남의 EXIF 가 박힌다**(WB·경로 스냅샷과 같은 이유).
            ok = pipeline.save_image(arr, path, EXPORT_SOFTWARE,
                                     gps=pipeline.gps_from_params(params),
                                     src_path=src[0], keep_gps=self._export_keep_gps)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._exportProgressSig.emit(0.0)   # 진행률 리셋(실패 시 stale 값이 오버레이에 남는 것 방지)
            self._finish_export(msg)   # 상태+_exporting 확정 후 1회 통지(순서 사유는 그쪽 독스트링)
            self._keepAwakeSig.emit(False)   # 슬립 방지 해제(스레드 귀속 API → 메인으로 큐잉)
        print(f"[export] {msg}")

    # ---------- Wallpaper: 3분할 트립틱 합성 export ----------
    # 배치 export 와 동일한 이유(픽셀 마스크 미영속·커브 평가기 QML 전용)로 QML 상태머신이
    # 슬롯 사진을 하나씩 라이브 로드한 뒤 이 슬롯을 호출해 렌더 배열만 모으고, 마지막에
    # wallpaperCompose 로 합성/저장한다. _wall_panels 는 워커가 쓰고 _exporting=False 이후에만
    # QML 이 다음 단계를 호출하므로 락 불요(배치와 동일 규율).
    @Slot(int, "QVariantMap")
    def wallpaperRenderPanel(self, slot: int, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 로드된 사진을 편집값으로 렌더해 저장 대신 _wall_panels[slot] 에 보관."""
        if not self._path or self._exporting or not (0 <= slot < 3):
            return
        pdict = {k: params[k] for k in params}
        pdict["bitDepth"] = 8                      # 패널은 항상 8bit(합성 캔버스가 uint8)
        pdict["proxyEdge"] = max(self._proxy_w, self._proxy_h)   # exportImage 와 동일(스냅샷)
        pdict["clipLevel"] = float(self._clip_level)             # exportImage 와 동일(스냅샷)
        src = (self._path, self._kelvin, self._tint)   # exportImage 와 동일 스냅샷
        sky_masks = list(self._layer_masks)
        haze = (self._haze_t, list(self._haze_A), self._haze_conf)
        self._exporting = True
        self._apply_keep_awake(True)
        self._export_progress = 0.0
        self.exportProgressChanged.emit()
        self._set_export_status(f"Rendering wallpaper panel {slot + 1}/3…")
        threading.Thread(target=self._do_wall_panel,
                         args=(slot, pdict, src, sky_masks, haze), daemon=True).start()

    def _do_wall_panel(self, slot, params, src, sky_masks, haze) -> None:
        try:
            self._wall_panels[slot] = self._render_array(params, src, sky_masks, haze)
            msg = f"PanelReady: {slot}"            # QML wallTick 이 접두사로 성공 판정
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._exportProgressSig.emit(0.0)
            self._finish_export(msg)               # 상태+_exporting 확정 후 1회 통지
            self._keepAwakeSig.emit(False)

    @Slot()
    def wallpaperClearPanels(self) -> None:  # noqa: N802 (QML 슬롯)
        self._wall_panels = [None, None, None]

    @Slot(QUrl, "QVariantMap")
    def wallpaperCompose(self, file_url: QUrl, opts) -> None:  # noqa: N802 (QML 슬롯)
        """opts: canvasW, canvasH, layout('triptych'|'magazine'|'index'|'fullbleed'), 그리고
        트립틱=gap/offsets[3], 잡지=mainSide/typeface/kicker/headline/deck/titles[3]/
        place/date/paths[3]. 3패널 합성 → 저장(스레드)."""
        if self._exporting:
            return
        panels = list(self._wall_panels)
        if any(p is None for p in panels):
            # exporting 을 올리지 않고 실패 상태만 → QML phase4 가 즉시 실패 판정
            self._set_export_status("Failed: missing wallpaper panel")
            return
        path = file_url.toLocalFile()
        o = {k: opts[k] for k in opts}
        self._exporting = True
        self._apply_keep_awake(True)
        self._export_progress = 0.0
        self.exportProgressChanged.emit()
        self._set_export_status("Composing wallpaper…")
        threading.Thread(target=self._do_wall_compose, args=(path, panels, o),
                         daemon=True).start()

    @staticmethod
    def _shot_summary(path: str) -> tuple:
        """(촬영정보 1줄, 'September 2023' 형태 날짜) — 잡지 레이아웃 캡션용."""
        try:
            fields, _ = read_shooting_info(path)
        except Exception:
            return "", ""
        d = {f["label"]: f["value"] for f in fields}
        line = "  ·  ".join(v for v in (d.get("Focal Length"), d.get("Aperture"),
                                        d.get("Shutter"), d.get("ISO")) if v)
        month = ""
        raw = d.get("Date", "")
        try:
            from datetime import datetime
            month = datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%B %Y")
        except Exception:
            month = raw[:7].replace("-", ". ")
        return line, month

    def _do_wall_compose(self, path: str, panels, o: dict) -> None:
        try:
            import pipeline
            # 글자가 들어가는 지면 계열 — 셋 다 같은 opts 를 먹고 QImage 를 돌려준다.
            _EDITORIAL = {"magazine": pipeline.compose_magazine,
                          "index": pipeline.compose_index,
                          "fullbleed": pipeline.compose_fullbleed}
            layout = str(o.get("layout", "triptych"))
            if layout in _EDITORIAL:
                paths = [str(x) for x in o.get("paths", ["", "", ""])]
                mo = dict(o)
                if not str(mo.get("date", "")).strip():     # 비어 있으면 메인 EXIF 로 채움
                    mo["date"] = self._shot_summary(paths[1])[1] if len(paths) > 1 else ""
                # ⚠️`shots` 는 compose_magazine 만 읽는다. `_shot_summary` 는 CR3/DNG 에서
                #   rawpy 디코드 + QT_IMG_LOCK 까지 가므로 안 쓰는 레이아웃에서 3회를 돌면
                #   export 스레드가 그만큼 늦고 썸네일 디코드와 락을 다툰다.
                #   (위 날짜 폴백 1회는 셋 다 폴리오에 쓰므로 남긴다.)
                mo["shots"] = ([self._shot_summary(p)[0] for p in paths]
                               if layout == "magazine" else ["", "", ""])
                # 메인 사진 캡션은 compose_magazine 이 조립한다(프레임 번호 규칙 단일화)
                img = _EDITORIAL[layout](panels, int(o["canvasW"]),
                                         int(o["canvasH"]), mo)
                # ⚠️**`img.save(path)` 로 직접 저장하지 말 것** — jpg 가 Qt 기본 품질 75 로
                #   나가고(그레인 같은 고주파에서 8x8 DCT 격자가 보인다) 메모리 인코딩 →
                #   `.part` → `os.replace` 원자 경로도 건너뛴다(인코딩 중 종료 시 목적지에
                #   잘린 파일). 아래 트립틱 분기와 같은 함수를 쓴다. 락은 그 안에 있다.
                ok = pipeline.save_image(img, path, EXPORT_SOFTWARE)
            else:
                canvas = pipeline.compose_wallpaper(
                    panels, int(o["canvasW"]), int(o["canvasH"]), int(o.get("gap", 18)),
                    [float(v) for v in o.get("offsets", [0.0, 0.0, 0.0])])
                ok = pipeline.save_image(canvas, path, EXPORT_SOFTWARE)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._finish_export(msg)
            self._keepAwakeSig.emit(False)
        print(f"[wallpaper] {msg}")

    # ---------- 배경화면 설정 영구 저장 (사용자 데이터 폴더의 JSON) ----------
    # 잡지 텍스트·슬롯 사진 경로·오프셋·레이아웃 옵션을 매번 다시 지정하지 않도록 보존한다.
    # 레지스트리(QSettings) 대신 **OS 공통 사용자 데이터 폴더의 JSON**(app_dirs.user_data_path)
    # 에 남긴다 — Win/mac/Linux 동일 방식, 백업·이전·삭제가 쉽다. 값은 전부 문자열.
    def _wall_prefs(self) -> dict:
        if self._wall_prefs_cache is None:
            data = {}
            try:
                p = Path(wallpaper_prefs_path())
                if p.is_file():
                    with open(p, encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        # 값은 문자열로 통일하되 "presets"(이름→설정 dict)만 중첩 유지
                        data = {str(k): (v if k == "presets" and isinstance(v, dict)
                                         else str(v))
                                for k, v in raw.items()}
            except Exception:
                data = {}                      # 손상 시 기본값으로 시작(다음 저장에 덮어씀)
            if not data:
                data = self._migrate_wall_prefs_from_registry()

            # 용어 변경(hero → main) 이전에 저장된 값 이관: 마지막 상태 + 프리셋 전부.
            # 구 키는 항상 제거하고, 새 키가 없을 때만 값을 물려준다.
            def _rename(d):
                if isinstance(d, dict) and "heroSide" in d:
                    old = d.pop("heroSide")
                    d.setdefault("mainSide", old)
            _rename(data)
            for pre in (data.get("presets") or {}).values():
                _rename(pre)
            self._wall_prefs_cache = data
        return self._wall_prefs_cache

    def _migrate_wall_prefs_from_registry(self) -> dict:
        """구버전(레지스트리 QSettings) 값 1회 이관 후 그 그룹을 제거. 없으면 빈 dict."""
        data = {}
        try:
            self._settings.beginGroup("wallpaper")
            for k in self._settings.childKeys():
                v = self._settings.value(k, "")
                if v not in (None, ""):
                    data[str(k)] = str(v)
            self._settings.endGroup()
            if data:
                _atomic_write_json(wallpaper_prefs_path(), data)
                self._settings.remove("wallpaper")   # 레지스트리에는 남기지 않는다
                self._settings.sync()
                print(f"[wallpaper] 설정 {len(data)}개를 {wallpaper_prefs_path()} 로 이관")
        except Exception as exc:
            print(f"[wallpaper] 레지스트리 이관 실패(무시): {exc}")
        return data

    def _flush_wall_prefs(self) -> None:
        try:
            _atomic_write_json(wallpaper_prefs_path(), self._wall_prefs())
        except Exception as exc:
            print(f"[wallpaper] 설정 저장 실패: {exc}")

    @Slot(str, result=str)
    def wallpaperText(self, key: str) -> str:  # noqa: N802 (QML 슬롯)
        return self._wall_prefs().get(key, "")

    @Slot(str, result=str)
    def wallpaperSlotPath(self, key: str) -> str:  # noqa: N802 (QML 슬롯)
        """저장된 슬롯 경로 — 파일이 사라졌으면 빈 문자열(빈 슬롯으로 복원)."""
        p = self._wall_prefs().get(key, "")
        return p if p and Path(p).is_file() else ""

    @Slot(str, str)
    def setWallpaperText(self, key: str, value: str) -> None:  # noqa: N802 (QML 슬롯)
        # 타이핑마다 디스크를 때리지 않도록 500ms 디바운스 후 한 번에 기록.
        self._wall_prefs()[key] = str(value)
        self._wall_prefs_timer.start()

    # ---------- 배경화면 프리셋(이름 붙인 설정 묶음) ----------
    # 같은 wallpaper.json 의 "presets" 아래에 이름→설정 dict 로 저장. 사진 슬롯 경로까지
    # 포함해 구성 전체를 되살린다(불러올 때 사라진 파일은 빈 슬롯으로).
    _WALL_PRESET_KEYS = (
        "layout", "typeface", "mainSide", "resIndex", "gap", "dual",
        "off0", "off1", "off2", "slot0", "slot1", "slot2",
        "kicker", "headline", "deck", "place", "date", "title0", "title1", "title2")

    def _wall_presets(self) -> dict:
        pres = self._wall_prefs().get("presets")
        if not isinstance(pres, dict):
            pres = {}
            self._wall_prefs()["presets"] = pres
        return pres

    @Slot(result="QStringList")
    def wallpaperPresetNames(self) -> list:  # noqa: N802 (QML 슬롯)
        return sorted(self._wall_presets().keys(), key=str.lower)

    @Slot(str, "QVariantMap")
    def saveWallpaperPreset(self, name: str, values) -> None:  # noqa: N802 (QML 슬롯)
        name = str(name).strip()
        if not name:
            return
        self._wall_presets()[name] = {k: str(values[k]) for k in values
                                      if k in self._WALL_PRESET_KEYS}
        self._flush_wall_prefs()          # 프리셋은 디바운스 없이 즉시 기록
        print(f"[wallpaper] 프리셋 저장: {name}")

    @Slot(str, result="QVariantMap")
    def loadWallpaperPreset(self, name: str) -> dict:  # noqa: N802 (QML 슬롯)
        p = self._wall_presets().get(str(name))
        if not isinstance(p, dict):
            return {}
        out = {k: str(v) for k, v in p.items() if k in self._WALL_PRESET_KEYS}
        for k in ("slot0", "slot1", "slot2"):        # 사라진 사진은 빈 슬롯으로
            if out.get(k) and not Path(out[k]).is_file():
                out[k] = ""
        return out

    @Slot(str)
    def deleteWallpaperPreset(self, name: str) -> None:  # noqa: N802 (QML 슬롯)
        if self._wall_presets().pop(str(name), None) is not None:
            self._flush_wall_prefs()

    @Slot(str, result="QVariantList")
    def wallShotInfo(self, path: str):  # noqa: N802 (QML 슬롯)
        """배경화면 잡지 **미리보기**용: [촬영정보 1줄, 'September 2023' 월].
        합성(_do_wallpaper)이 쓰는 _shot_summary 와 같은 원천이라 미리보기 텍스트가
        실제 출력과 같다. 빈 경로/실패면 ["", ""].

        ★⚠️**여기서 EXIF 를 읽지 않는다 — 캐시에 없으면 워커로 넘기고 빈 값을 즉시 돌려준다.**
          이 슬롯은 QML 바인딩(`win.wallShots`)이 부르므로 **GUI 스레드**다. `_shot_summary`
          는 RAF 면 임베드 JPEG 만 보지만, 그 외(CR3·비트맵썸 DNG)에서는 `rawpy.imread` 와
          Qt 인코딩(`_encode_bitmap_jpeg`)까지 가고 그 인코딩은 `QT_IMG_LOCK` 을 잡는다 —
          슬롯 지정·시작만으로 창이 멈추고, export 인코딩이 진행 중이면 그 뒤로 줄까지 선다.
          완료되면 `wallShotsChanged` 로 알리고 QML 이 바인딩을 다시 굽는다(`wallShotsRev`)."""
        key = str(path or "")
        if not key:
            return ["", ""]
        hit = self._shot_cache.get(key)
        if hit is not None:
            return list(hit)
        if key not in self._shot_pending:
            self._shot_pending.add(key)
            threading.Thread(target=self._shot_worker, args=(key,), daemon=True).start()
        return ["", ""]

    def _shot_worker(self, path: str) -> None:
        """EXIF 요약을 워커에서 읽어 캐시에 넣고 QML 에 알린다(위 wallShotInfo 주석)."""
        try:
            line, month = self._shot_summary(path)
        except Exception:
            line, month = "", ""
        self._shot_cache[path] = (line, month)
        self._shot_pending.discard(path)
        self._shotInfoSig.emit()

    @Slot(str, result=str)
    def captionTitle(self, path: str) -> str:  # noqa: N802 (QML 슬롯)
        """임의 파일의 저장된 캡션 → 제목용 한 줄(첫 글자 대문자, 끝 마침표 제거).
        짧은 캡션 우선(가장 정확). 캡션이 없으면 빈 문자열.
        ⚠️다른 폴더면 캐시를 갈아끼우지 않고 그 폴더 사이드카만 직접 읽는다(탐색기 검색/
        인덱스 카운터가 남의 폴더 캡션으로 오염되는 것 방지)."""
        p = Path(path)
        folder = str(p.parent)
        with self._caption_lock:
            if folder == self._captions_folder:
                entry = self._captions.get(p.name)
            else:
                entry = self._load_captions(folder).get(p.name)
        if not isinstance(entry, dict):
            return ""
        text = ""
        for k in self._CAPTION_KEYS:                 # short → detailed → paragraph
            if entry.get(k):
                text = str(entry[k])
                break
        text = text.strip().split("\n")[0].strip()
        if text.endswith("."):
            text = text[:-1]
        return text[:1].upper() + text[1:] if text else ""

    @Slot(int, int, result=QUrl)
    def suggestedWallpaperUrl(self, w: int, h: int) -> QUrl:  # noqa: N802 (QML 슬롯)
        """배경화면 기본 파일명: <마지막 배경화면 저장 폴더>/wallpaper_{w}x{h}.jpg.

        ⚠️배경화면은 사진 폴더가 아니라 **모아 두는 폴더**에 저장하는 게 보통이라 export 와도,
        탐색기 폴더와도 따로 기억한다(`_DIALOG_FOLDER_KEYS`). 기억이 없거나 그 폴더가
        사라졌으면 예전 동작대로 현재 탐색기 폴더(없으면 원본 폴더)로 폴백한다."""
        folder = (self._dlg_folder("wallpaper") or self._folder
                  or (str(Path(self._path).parent) if self._path else ""))
        if not folder:
            return QUrl()
        return QUrl.fromLocalFile(str(Path(folder) / f"wallpaper_{w}x{h}.jpg"))

    # ---------- GPU export: 프리뷰와 동일한 셰이더로 풀해상도 렌더(프리뷰=Export 보장) ----------
    @Slot(QUrl, "QVariantMap")
    def exportImageGpu(self, file_url: QUrl, params) -> None:  # noqa: N802 (QML 슬롯)
        """풀해상도 16bit src 를 백그라운드 디코드 → 완료 시 QML 이 GPU 셰이더로 grab/저장.
        무거운 디코드만 스레드에서; GPU 렌더/grab 은 GUI 스레드(QML)에서 수행."""
        if not self._path or self._exporting or self._full_provider is None:
            return
        self._gpu_path = file_url.toLocalFile()
        self._remember_export_ext(self._gpu_path)   # CPU 경로와 동일(exportImage 참조)
        self._gpu_params = {k: params[k] for k in params}
        # ★소스 경로 스냅샷 — EXIF 통과(`exif_pass`)가 워커에서 쓴다. **요청 시점**에 떠야
        #   한다: 워커가 `self._path` 를 읽으면 export 중 다른 사진을 열었을 때 남의 EXIF 가
        #   박힌다. CPU export 는 `src` 튜플로 같은 스냅샷을 이미 갖고 있다.
        self._gpu_params["srcPath"] = self._path
        self._exporting = True
        self._apply_keep_awake(True)
        # GPU 는 렌더 진행률이 없다 → 0 유지(오버레이는 인디터미닛). 예외로 NR 이 켜져 있으면
        # `_build_nr_full` 의 AI 타일 진행률이 여기로 들어온다(수십 초라 표시가 있어야 한다).
        self._export_progress = 0.0
        self.exportProgressChanged.emit()
        self._set_export_status("GPU exporting… (full-resolution decode)")
        # 소스 경로 스냅샷 — 디코드 중 다른 사진을 로드해도 요청 시점 파일을 디코드(CPU export 동일).
        threading.Thread(target=self._do_full_decode, args=(self._path,), daemon=True).start()

    def _do_full_decode(self, src_path: str) -> None:
        try:
            lens_on = bool(self._gpu_params.get("lensCorrection", True))
            # ⚠️자동노출 토글은 여기가 아니라 **셰이더 uniform**(pipeFull autoExpEV)이 처리한다
            #   — 디코드는 항상 게인을 적용한다(setAutoExposure 주석 참조).
            img, *_ = (image_loader.load_full(src_path, lens_on)
                       if image_loader.is_display_image(src_path)
                       else load_full(src_path, lens_on))
            self._full_provider.set_image(img)
            # NR 노이즈 항 텍스처(셰이더 nrNoise=1). ⚠️여기가 CPU export 대비 GPU export 의
            # 유일한 추가 비용이고, 없으면 NR 이 조용히 빠진다(프록시 nrBase 로는 성립 안 함 —
            # NrFullProvider/셰이더 nrNoise 주석). 실패해도 export 자체는 계속한다.
            self._build_nr_full(img)
            self._fullDecoded.emit(True)
        except Exception as exc:
            print(f"[export-gpu] 디코드 실패: {exc}")
            self._fullDecoded.emit(False)

    @staticmethod
    def _qimage_rgb16(qimg):
        """RGBA64 QImage → (H,W,3) uint16 (자체 소유 복사본). `_qimage_to_rgb` 의 16bit 판 —
        헤드룸 코드의 하위 8bit 를 버리면 노이즈 항이 양자화 잡음에 묻힌다."""
        import numpy as np
        im = qimg.convertToFormat(QImage.Format.Format_RGBA64)
        w, h = im.width(), im.height()
        if w == 0 or h == 0:
            return np.zeros((max(h, 0), max(w, 0), 3), np.uint16)
        return (np.frombuffer(im.constBits(), np.uint16)
                .reshape(h, im.bytesPerLine() // 2)[:, :w * 4]
                .reshape(h, w, 4)[..., :3].copy())

    def _build_nr_full(self, img) -> None:
        """GPU export 용 노이즈 항 텍스처를 굽는다(워커 스레드). 준비되면 `_nrfull_ready=True`.

        ★**pipeFull 이 렌더하는 출력 해상도**에서 굽는다 — NR 이 노리는 것은 그 해상도의 픽셀
          노이즈다. 다만 실제로 여기 오는 것은 **Original(축소 없음)뿐**이다: 해상도 프리셋 +
          NR 조합은 QML `win.nrForcesCpu` 가 CPU 로 넘긴다(축소 필터가 셰이더 밉맵과 달라
          뺄셈이 반만 맞는다 — 그쪽 주석의 실측). 프리셋이 와도 틀리지 않게 여기서도 같은
          `_downscale_to_edge` 를 적용해 두지만, 그 경로의 정확도는 보장 대상이 아니다.
        항 계산은 `pipeline.nr_terms` — CPU export 와 **같은 함수**라 두 경로가 갈리지 않는다.
        인코딩: 화소 = t·0.5+0.5 (16bit). t 는 [0,1] 값들의 차의 합이라 실측 |t| ≪ 1 이고,
        16bit 양자화 σ 는 0.0009% 로 NR 후 잔여 노이즈(~0.14%)보다 두 자릿수 작다.
        NR 이 꺼져 있으면 아무것도 굽지 않는다(추가 비용 0 — GPU 경로의 속도를 그대로 유지)."""
        import numpy as np
        self._nrfull_ready = False
        if self._nrfull_provider is None:
            return
        p = self._gpu_params
        ln = float(p.get("lumaNR", 0) or 0)
        cn = float(p.get("colorNR", 0) or 0)
        if ln <= 0.0 and cn <= 0.0:
            self._nrfull_provider.clear()
            return
        import time
        t_start = time.perf_counter()
        try:
            import pipeline
            u16 = self._qimage_rgb16(img)                  # 헤드룸 코드(= 셰이더가 보는 값)
            u16 = pipeline._downscale_to_edge(u16, int(p.get("outEdge", 0) or 0))
            h, w = u16.shape[:2]
            proxy_edge = max(self._proxy_w, self._proxy_h) or 2560
            scale = max(h, w) / float(proxy_edge)          # 프록시 텍셀 반경 → 이 해상도 px
            # 중성 display 베이스 — 셰이더 dispSrc(convert.frag) / pipeline neutral_disp 와 동형.
            code = u16.astype(np.float32) / 65535.0
            del u16                                        # 26MP 에서 156MB — 여기서 놓는다
            neutral = np.clip(wb.filmic(self._native_to_scenelinear(code)),
                              0.0, 1.0).astype(np.float32)
            del code
            nlum = (neutral @ np.array([0.299, 0.587, 0.114], np.float32)).astype(np.float32)
            ai = bool(p.get("aiNr", False))
            if ai:
                import ai_denoise
                self._exportStatusSig.emit(
                    f"GPU exporting… (AI denoise, {ai_denoise.provider_label()})")
            else:
                self._exportStatusSig.emit("GPU exporting… (noise reduction)")
            noise_l, chroma = pipeline.nr_terms(
                neutral, nlum, ln, cn, ai, scale,
                progress=lambda f: self._exportProgressSig.emit(f))   # AI 타일 진행률
            del neutral, nlum
            # t = chroma + noiseL. 26MP 에서 배열 하나가 313MB 라 **제자리로** 합친다
            # (새로 할당하면 그만큼 피크가 올라간다 — 이 함수가 export 중 메모리 정점이다).
            if chroma is not None:
                t = chroma
                if noise_l is not None:
                    t += noise_l[..., None]
            else:
                t = np.repeat(noise_l[..., None], 3, axis=2)
            del noise_l, chroma
            peak = float(np.abs(t).max())
            if peak > 1.0:      # 인코딩 범위 초과(이론상만 — 실측 ≪1). 잘리면 그만큼 NR 이 약해진다.
                print(f"[export-gpu] NR 노이즈 항이 인코딩 범위를 넘음(|t|max={peak:.3f}) — 클립됨")
            rgba = np.empty((h, w, 4), np.uint16)          # 중간 uint16 배열 없이 바로 채운다
            np.clip(t, -1.0, 1.0, out=t)
            t *= 0.5
            t += 0.5
            rgba[..., :3] = (t * 65535.0 + 0.5).astype(np.uint16)
            del t
            rgba[..., 3] = 65535                           # alpha=불투명(RGBA64 포맷)
            self._nrfull_provider.set_image(
                QImage(rgba.data, w, h, w * 8, QImage.Format.Format_RGBA64).copy())
            self._nrfull_ready = True
            # NR 이 GPU export 의 유일한 추가 비용이라 시간을 남긴다(CPU export 도 같은 값을 낸다).
            print(f"[export-gpu] NR 텍스처 {w}x{h} "
                  f"{'AI' if ai else 'guided'} {time.perf_counter() - t_start:.1f}s")
        except Exception as exc:
            # NR 없이라도 내보내는 편이 낫다(사용자는 저장을 요청했다) — 대신 조용히 넘어가지
            # 않도록 상태 문구를 남긴다(그냥 두면 'NR 이 안 걸린 파일'이 말없이 나간다).
            print(f"[export-gpu] NR 텍스처 생성 실패(NR 없이 진행): {exc}")
            self._exportStatusSig.emit("Noise reduction skipped (out of memory?) - exporting anyway")
            self._nrfull_provider.clear()
        self._nrfull_counter += 1
        self._nrfull_url = f"image://nrfull/n?v={self._nrfull_counter}"
        self._nrFullSig.emit()          # 메인 스레드에서 nrFullChanged 발화(QML Image 재로드)

    def _clear_nr_full(self) -> None:
        """NR 노이즈 항 텍스처 해제(26MP 208MB). GPU export 가 끝나거나 실패하는 모든 경로에서
        `_full_provider.clear()` 와 짝으로 호출한다 — 안 놓으면 export 사이에 계속 물고 있다."""
        if self._nrfull_provider is None:
            return
        self._nrfull_provider.clear()
        self._nrfull_ready = False
        self._nrfull_counter += 1
        self._nrfull_url = f"image://nrfull/n?v={self._nrfull_counter}"
        self.nrFullChanged.emit()

    @Slot()
    def nrFullLoadFailed(self) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 NR 노이즈 항 텍스처를 못 올렸다 → **NR 없이 계속 간다.**

        ★⚠️여기서 export 를 중단하면 안 된다. `nrFullReady` 가 참인 동안 QML `texReady` 가
          거짓으로 굳어 grab 이 영영 안 일어나고, `_exporting` 이 True 로 남아 **이후 모든
          export 가 조용히 무시**된다(배치는 `!controller.exporting` 을 기다리다 같이 멈춘다).
          `srcFull` 에는 에러 복구가 있는데 이쪽만 없었다.
        ⚠️**URL 을 바꾸지 않는다**(`_clear_nr_full` 과 다른 점) — 지금 실패한 그 Image 를 다시
          로드시키면 같은 자리를 맴돈다. 플래그만 내려 `texReady` 를 풀어 준다.
        ★플래그를 내리는 것이 곧 `saveGrab` 의 `_nrApplied` 스냅샷을 거짓으로 만들어,
          저장 문구에 "(noise reduction skipped)" 가 붙는다 — NR 이 말없이 빠지지 않는다.
        """
        if not self._nrfull_ready:
            return
        print("[export-gpu] NR 텍스처 로드 실패 — NR 없이 진행")
        self._nrfull_ready = False
        self._set_export_status("Noise reduction skipped (texture load failed) - exporting anyway")
        self.nrFullChanged.emit()

    @Slot(bool)
    def _on_full_decoded(self, ok: bool) -> None:
        """메인 스레드: 풀해상도 src 준비됨 → URL 갱신(QML Image 재로드) + grab 트리거."""
        if not ok:
            self._apply_keep_awake(False)
            self._finish_export("GPU export failed (decode)")
            # 디코드 실패는 QML 이 감지 못 함(fullChanged/fullReady 미발화 → srcFull 상태변화
            # 없음). 명시적으로 로더 해제 신호를 보내지 않으면 gpuExportLoader 가 active=true
            # 로 남아 pipeFull(모든 슬라이더 바인딩) 파이프라인이 계속 재평가된다.
            if self._full_provider is not None:
                self._full_provider.clear()
                self._clear_nr_full()          # NR 노이즈 항 텍스처도 같이 해제(26MP 208MB)
            self.fullAborted.emit()
            return
        self._full_counter += 1
        self._full_url = f"image://rawfull/f?v={self._full_counter}"
        self.fullChanged.emit()   # QML srcFull.source 갱신 → 재로드
        self.fullReady.emit()     # QML: 로드 완료 시 grab

    @Slot()
    def abortGpuExport(self) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 풀해상도 src 로드에 실패(Image.Error)했을 때 호출 — export 상태를
        복구한다. 없으면 _exporting 이 영구 True 로 남아 이후 모든 export 가 무시됐음."""
        if not self._exporting:
            return
        self._apply_keep_awake(False)
        self._finish_export("GPU export failed (image load)")
        if self._full_provider is not None:
            self._full_provider.clear()
            self._clear_nr_full()          # NR 노이즈 항 텍스처도 같이 해제(26MP 208MB)

    @Slot("QImage")
    def saveGrab(self, qimg) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 grab 한 GPU 결과(QImage) 저장. **QImage 접근(→numpy 복사)과 프로바이더
        해제만 메인 스레드에서** 하고, 나머지(지오메트리/스탬프/축소/인코딩)는 워커로 넘긴다.
        전에는 전부 이 슬롯(GUI 스레드)에서 해서 grab 후 저장까지 UI 가 멈췄다(사용자 보고:
        '점유율 상승 → 잠시 멈춤 → 저장'). CPU export(_do_export)와 같은 스레딩 구조."""
        try:
            arr = self._qimage_to_rgb(qimg)          # QImage 는 여기까지만(메인 스레드)
            # 의도한 렌더 치수 — 소스 원본 크기에 QML pipeFull.fullScale 과 같은 식을 적용.
            # ⚠️HiDPI(디스플레이 배율 125% 등)에서 grabToImage 가 요청 크기에 DPR 을 곱한
            #   이미지를 돌려준다(실측: 4080×6111 요청 → 5100×7639 = ×1.25). 워커에서 이
            #   기대 치수로 정규화한다 — Original 포함 모든 해상도에서 CPU export 와 치수 일치.
            src = self._full_provider._img if self._full_provider is not None else None
            if src is not None and not src.isNull():
                w0, h0 = src.width(), src.height()
            else:                                    # 폴백: grab 치수 그대로(정규화 생략)
                w0, h0 = qimg.width(), qimg.height()
            edge = int(self._gpu_params.get("outEdge", 0) or 0)
            long_e = max(w0, h0)
            f = (edge / long_e) if (0 < edge < long_e) else 1.0
            expected = (int(round(h0 * f)), int(round(w0 * f)))      # (H, W)
            # ⚠️`_clear_nr_full()` 이 `_nrfull_ready` 를 내리므로 **비우기 전에** 스냅샷을 뜬다
            #   — 저장 문구가 "NR 이 실제로 걸렸는지"를 이 값으로 판정한다(_finish_gpu_export).
            self._gpu_params["_nrApplied"] = bool(self._nrfull_ready)
            if self._full_provider is not None:
                self._full_provider.clear()          # 풀해상도 소스 메모리 해제(QML 로더도 곧 해제)
                self._clear_nr_full()          # NR 노이즈 항 텍스처도 같이 해제(26MP 208MB)
            # ⚠️스레드 생성까지 try 안에 둔다 — 밖에 두면 start() 실패(RuntimeError: can't start
            #   new thread) 시 _exporting 이 True 로 남아 이후 모든 export 가 조용히 무시된다.
            threading.Thread(target=self._finish_gpu_export,
                             args=(arr, dict(self._gpu_params), self._gpu_path, expected),
                             daemon=True).start()
        except Exception as exc:
            self._apply_keep_awake(False)
            if self._full_provider is not None:
                self._full_provider.clear()
                self._clear_nr_full()          # NR 노이즈 항 텍스처도 같이 해제(26MP 208MB)
            self._finish_export(f"Failed: {exc}")
            return

    def _finish_gpu_export(self, arr, params: dict, path: str, expected=None) -> None:
        """GPU export 후처리(워커 스레드) — DPR 정규화 → 지오메트리 → 스탬프 → 저장."""
        try:
            import pipeline
            import numpy as np
            # HiDPI 정규화 — grab 이 기대 치수와 다르면 먼저 되돌린다. 지오메트리(크롭)
            # 전에 해야 이후 단계의 치수 기준이 CPU export 와 같아진다. 배율 100% 면 no-op.
            # QML doGrab 이 요청 크기를 DPR 로 나누므로 보통 여유분은 축당 DPR-1 px 뿐 —
            # ⚠️그 경우 **잘라낸다(재샘플 금지)**. 축소 재샘플은 그레인을 평균해 세기를 깎는다
            #   (Retina 실측: 평탄부 σ 가 CPU export 대비 −22%, 문서의 '풀해상도 후 CPU 축소'
            #   실패와 같은 형태). zoom 폴백은 그 가정이 깨졌을 때만 쓴다.
            if expected is not None and tuple(arr.shape[:2]) != tuple(expected):
                dh, dw = arr.shape[0] - expected[0], arr.shape[1] - expected[1]
                if 0 <= dh <= _GRAB_SLACK_PX and 0 <= dw <= _GRAB_SLACK_PX:
                    arr = arr[:expected[0], :expected[1]]
            if expected is not None and tuple(arr.shape[:2]) != tuple(expected):
                from scipy.ndimage import zoom as _zoom, gaussian_filter as _gf
                fh = expected[0] / arr.shape[0]
                fw = expected[1] / arr.shape[1]
                x = arr.astype(np.float32)
                s = 0.5 * (1.0 / min(fh, fw) - 1.0)
                if s > 0.4:
                    x = _gf(x, (s, s, 0.0))
                x = _zoom(x, (fh, fw, 1.0), order=1)
                x = x[:expected[0], :expected[1]]             # zoom 반올림 여유분 절단
                arr = np.clip(x + 0.5, 0, 255).astype(np.uint8)
            arr = pipeline._apply_geometry(arr, params)   # 프리뷰/CPU export 와 동일
            # 날짜 스탬프 — 크롭/회전 후 '최종 프레임'에 찍는다(CPU export·프리뷰와 동일 위치/합성).
            #   pipeFull 셰이더는 스탬프를 굽지 않음(stampOn=0).
            import date_stamp
            _st = str(params.get("stampText", "") or "")
            if bool(params.get("dateStamp", False)) and _st:
                date_stamp.stamp_export(
                    arr, _st, rot=int(params.get("stampRot", 0)),
                    style=str(params.get("stampStyle", "7c_bold")),
                    size_frac=float(params.get("stampSize", 0.032)),
                    margin_frac=float(params.get("stampMargin", 0.05)),
                    color=str(params.get("stampColor", date_stamp.DEFAULT_COLOR)),
                    glow=float(params.get("stampGlow", 1.0)),
                    spread=float(params.get("stampSpread", 1.0)),
                    grain_amt=float(params.get("grainAmt", 0.0)))
            # 해상도 프리셋 — pipeFull 이 이제 프리셋 크기로 직접 렌더하므로(그레인이 출력
            # 해상도에서 계산돼 CPU 경로와 정합) 보통 여긴 no-op. 혹시 grab 이 더 크게 온
            # 경우의 안전망으로만 남긴다.
            out_edge = int(params.get("outEdge", 0) or 0)
            if out_edge > 0 and max(arr.shape[:2]) > out_edge:
                from scipy.ndimage import zoom, gaussian_filter
                f = out_edge / float(max(arr.shape[:2]))
                x = arr.astype(np.float32)
                s = 0.5 * (1.0 / f - 1.0)
                if s > 0.4:
                    x = gaussian_filter(x, (s, s, 0.0))
                arr = np.clip(zoom(x, (f, f, 1.0), order=1) + 0.5, 0, 255).astype(np.uint8)
            # ⚠️소스 경로는 `params`(= `_gpu_params` 사본)에서 읽는다 — GPU export 는 CPU 와
            #   **다른 dict** 를 보므로 `exportImageGpu` 가 요청 시점에 `srcPath` 를 넣어 둔다.
            #   CLAUDE.md 가 "가장 잘 빠진다"고 적은 경로가 여기다.
            ok = pipeline.save_image(arr, path, EXPORT_SOFTWARE,
                                     gps=pipeline.gps_from_params(params),
                                     src_path=str(params.get("srcPath", "") or ""),
                                     keep_gps=self._export_keep_gps)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
            # NR 이 켜져 있는데 노이즈 텍스처를 못 구웠으면 **결과에 NR 이 없다** — 최종 문구에
            # 남긴다(조용히 빠지는 것이 이 기능의 원래 버그였다. `_build_nr_full` 참조).
            if ok and not params.get("_nrApplied") and (float(params.get("lumaNR", 0) or 0) > 0
                                                        or float(params.get("colorNR", 0) or 0) > 0):
                msg += "  (noise reduction skipped)"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._finish_export(msg)                 # 상태+_exporting 확정 후 1회 통지
            self._keepAwakeSig.emit(False)           # 스레드 귀속 API → 메인으로 큐잉
            # ⚠️print 는 **맨 마지막** — 여기서 UnicodeEncodeError 가 나도 상태는 이미 확정됐다
            #   (cp949 콘솔 + 인코딩 불가 문자. 예전엔 이 줄이 finally 첫 줄이라 저장은 됐는데
            #    _exporting 이 True 로 남아 진행 표시가 안 사라졌다. CPU export 와 동일 위치.)
            print(f"[export-gpu] {msg}")

    @Slot(str)
    def refreshDisplayCm(self, device_name: str = "") -> None:  # noqa: N802 (QML 슬롯)
        """현재 모니터의 ICC 프로파일로 sRGB→디스플레이 CM LUT 재생성(프리뷰 전용).
        device_name 예: '\\\\.\\DISPLAY1'(QScreen.name()). 모니터 전환/시작 시 호출."""
        if self._cm_provider is None:
            return
        try:
            import display_cm
            icc = display_cm.display_icc_path(device_name or None)
            atlas, n = display_cm.build_cm_atlas(icc, 33)
            self._cm_dst = display_cm.dst_colorspace(icc)   # 스탬프 오버레이도 동일 변환 적용
        except Exception as exc:
            print(f"[display-cm] 실패: {exc}")
            atlas, n, icc = None, 0, None
            self._cm_dst = None
        self._cm_provider.set_atlas(atlas, n)
        self._cm_n = self._cm_provider.size
        self._has_cm = self._cm_n > 1
        self._cm_counter += 1
        self._cm_url = f"image://displaycm/c?v={self._cm_counter}"
        self.cmChanged.emit()
        self._update_stamp_layer()   # 모니터 전환 → 스탬프 오버레이도 새 CM 으로 재보정
        print(f"[display-cm] {'적용' if self._has_cm else '항등(sRGB/없음)'} "
              f"N={self._cm_n} dev={device_name or 'primary'} icc={icc}")

    @Slot(bool)
    def setDisplayCmEnabled(self, on) -> None:  # noqa: N802 (QML 슬롯)
        """win.displayCM 토글 반영 — 스탬프 오버레이 CM 게이트(사진 셰이더와 동기). 즉시 재보정."""
        on = bool(on)
        if on == self._cm_enabled:
            return
        self._cm_enabled = on
        self._update_stamp_layer()

    def _get_cm_n(self) -> int:
        return self._cm_n

    def _get_has_cm(self) -> bool:
        return self._has_cm

    def _get_cm_url(self) -> str:
        return self._cm_url

    cmLutN = Property(int, _get_cm_n, notify=cmChanged)
    hasDisplayCM = Property(bool, _get_has_cm, notify=cmChanged)
    cmLutUrl = Property(str, _get_cm_url, notify=cmChanged)

    def _get_full_url(self) -> str:
        return self._full_url

    fullUrl = Property(str, _get_full_url, notify=fullChanged)

    def _get_nrfull_url(self) -> str:
        return self._nrfull_url

    def _get_nrfull_ready(self) -> bool:
        return self._nrfull_ready

    # GPU export 전용 — `pipeFull` 이 `nrBase`(binding 12)를 프록시 대신 이쪽으로 물고
    # `nrNoise=1` 로 해석한다. `nrFullReady` 가 곧 pipeFull 의 `nrOn` 이다(NR 이 꺼져 있거나
    # 굽기에 실패하면 False → 셰이더가 NR 을 건너뛴다).
    nrFullUrl = Property(str, _get_nrfull_url, notify=nrFullChanged)
    nrFullReady = Property(bool, _get_nrfull_ready, notify=nrFullChanged)

    def _finish_export(self, msg: str) -> None:
        """워커 종료 공통 처리 — 상태와 `_exporting` 을 **알림 전에 모두 확정**한 뒤 한 번만 통지.

        ★⚠️`exporting` 의 notify 가 `exportStatusChanged` **하나뿐**이라, emit 을 두 값 사이에
        두면(예전 코드) 메인 스레드가 그 틈에 '상태=Saved, exporting=True' 를 읽고 **영구히
        굳는다** — 실측 800회 중 454~470회(57~59%). 워커가 emit 직전까지 numpy/파일 IO 로 GIL 을
        놓고 메인은 `app.exec()` 에 파킹돼 있어, 큐잉된 통지가 워커의 다음 줄보다 먼저 처리된다.
        ⚠️한 번 굳으면 `busyChanged`·`batchChanged` 로도 **안 풀린다** — 진행 오버레이 식
        `exporting || batchActive || wallActive || busy` 에서 QML 이 `||` 를 단축평가하므로 첫 항이
        True 인 동안 뒤 항이 의존성에서 빠진다. **다음 export 가 시작될 때만** 복구된다(= 저장은
        끝났는데 진행 표시가 계속 도는 사용자 보고의 정체).
        ⚠️반대 순서(`_exporting` 먼저 해제 → 상태 확정)도 안 된다 — 배치 폴러가 exporting=false 를
        보는 순간 exportStatus 가 아직 "Exporting…" 이라 저장된 파일을 실패로 오카운트한다
        (`ui/Main.qml` batchTick). 그래서 **둘 다 emit 앞**에 둔다. 수정 후 실측 1600회 래치 0회.
        ⚠️**export 를 끝내는 모든 경로가 이걸 쓴다** — 메인 스레드 슬롯(`_on_full_decoded` 실패·
        `abortGpuExport`·`saveGrab` except)도 포함. 거기선 두 값 사이에 관측자가 못 끼어들어
        예전 순서도 안전했지만, "이 순서는 위험" 이라고 적어둔 바로 옆에 반례를 두지 않는다.
        ⚠️`print` 를 이 앞에 두지 말 것 — cp949 콘솔에서 인코딩 불가 문자가 섞이면
        UnicodeEncodeError 로 상태 확정 자체가 건너뛰어져 같은 증상이 **결정적으로** 난다.
        """
        self._export_status = msg
        self._exporting = False
        self.exportStatusChanged.emit()

    def _set_export_status(self, s: str) -> None:
        self._export_status = s
        self.exportStatusChanged.emit()

    def _get_export_status(self) -> str:
        return self._export_status

    exportStatus = Property(str, _get_export_status, notify=exportStatusChanged)

    def _set_load_error(self, s: str) -> None:
        if s != self._load_error:
            self._load_error = s
            self.loadErrorChanged.emit()

    def _get_load_error(self) -> str:
        return self._load_error

    loadError = Property(str, _get_load_error, notify=loadErrorChanged)

    @Slot(float)
    def _on_export_progress(self, frac: float) -> None:
        """워커 스레드의 render_full progress 콜백 → 메인 스레드에서 진행률 갱신."""
        self._export_progress = max(0.0, min(1.0, float(frac)))
        self.exportProgressChanged.emit()

    def _get_export_progress(self) -> float:
        return self._export_progress

    # CPU export 진행률(0..1). GPU export 는 갱신 안 함(빠른 경로) → 0 유지.
    exportProgress = Property(float, _get_export_progress, notify=exportProgressChanged)

    def _get_exporting(self) -> bool:
        return self._exporting

    # 내보내는 중 여부(스피너 표시용). 상태 변경과 동시에 갱신되므로 같은 시그널로 통지.
    exporting = Property(bool, _get_exporting, notify=exportStatusChanged)

    @Slot(int, int)
    def setScreenSize(self, w: int, h: int) -> None:  # noqa: N802 (main 에서 호출)
        """창이 놓인 화면의 실제 픽셀 크기(배경화면 'Match screen' 해상도용)."""
        if (int(w), int(h)) != (self._screen_w, self._screen_h):
            self._screen_w, self._screen_h = int(w), int(h)
            self.screenSizeChanged.emit()

    def _get_screen_w(self) -> int:
        return self._screen_w

    def _get_screen_h(self) -> int:
        return self._screen_h

    screenW = Property(int, _get_screen_w, notify=screenSizeChanged)
    screenH = Property(int, _get_screen_h, notify=screenSizeChanged)

    def _get_wallpaper_enabled(self) -> bool:
        return WALLPAPER_PANEL

    def _get_photo_map_enabled(self) -> bool:
        """Photo map(탐색기 🗺) 노출 여부 — 개인용 플래그(.env `PHOTO_MAP`). 시작 시 고정."""
        return PHOTO_MAP

    # 개인용 Wallpaper 패널 노출 여부(.env 플래그, 시작 시 고정) — 릴리즈 기본 숨김
    wallpaperEnabled = Property(bool, _get_wallpaper_enabled, constant=True)
    photoMapEnabled = Property(bool, _get_photo_map_enabled, constant=True)

    def _get_curve_url(self) -> str:
        return self._curve_url

    curveUrl = Property(str, _get_curve_url, notify=curveChanged)

    def _get_exif(self) -> list:
        return self._exif_fields

    def _get_exif_summary(self) -> str:
        return self._exif_summary

    shootingInfo = Property("QVariantList", _get_exif, notify=exifChanged)
    shootingSummary = Property(str, _get_exif_summary, notify=exifChanged)

    # ---------- 지오태그(사진에 사람이 붙이는 위치) ----------
    # ★설계의 축: **룩이 아니라 사진별 메타데이터**다. 그래서 `_PRESET_KEYS`/`LOOK_DEFAULTS`
    #   에 넣지 않고(레시피가 남의 좌표를 옮기면 사고다), 셰이더 uniform 도 만들지 않는다.
    #   `presets.look_hash` 가 `_PRESET_KEYS` 로 걸러 주므로 룩 지문·레시피 배지도 자동으로 안전하다.
    def _get_gps_set(self) -> bool:
        return self._gps is not None

    def _get_gps_lat(self) -> float:
        return float(self._gps[0]) if self._gps else 0.0

    def _get_gps_lon(self) -> float:
        return float(self._gps[1]) if self._gps else 0.0

    def _get_gps_alt(self):
        # ⚠️`QVariant` — 고도는 '없음'이 정상값이라 0.0 으로 뭉개면 안 된다(QML 에서 null).
        return None if (not self._gps or self._gps[2] is None) else float(self._gps[2])

    def _get_gps_src(self) -> str:
        return self._gps_src

    def _get_gps_text(self) -> str:
        return exif_info.format_gps(self._gps[0], self._gps[1]) if self._gps else ""

    gpsSet = Property(bool, _get_gps_set, notify=gpsChanged)
    gpsLat = Property(float, _get_gps_lat, notify=gpsChanged)
    gpsLon = Property(float, _get_gps_lon, notify=gpsChanged)
    gpsAlt = Property("QVariant", _get_gps_alt, notify=gpsChanged)
    gpsSrc = Property(str, _get_gps_src, notify=gpsChanged)
    gpsText = Property(str, _get_gps_text, notify=gpsChanged)

    def _get_map_cache_dir(self) -> str:
        """지도 타일 디스크 캐시 폴더(QML `osm.mapping.cache.directory` 로 넘긴다).

        ★**Qt 기본 캐시를 쓰면 안 된다.** Qt 의 OSM 플러그인은 제공자 목록을
        `maps-redirect.qt.io` 에서 받는데 그게 **`street` 까지 Thunderforest 로 넘긴다** —
        키 없는 요청은 허용량을 넘으면 **"API Key Required" 워터마크 타일**이 오고, 그게
        `~/Library/Caches/QtLocation`(Windows 는 로컬 캐시)에 **그대로 저장된다.**
        타일 소스를 OSM 본 서버로 바꿔도 그 캐시가 살아 있으면 계속 그 그림이 보인다(실측).
        앱 전용 폴더를 쓰면 그 오염된 캐시를 아예 읽지 않는다.
        """
        import app_dirs
        d = app_dirs.user_data_path(os.path.join("cache", "maptiles"))
        os.makedirs(d, exist_ok=True)
        return d

    mapCacheDir = Property(str, _get_map_cache_dir, constant=True)

    @staticmethod
    def _gps_tuple(g):
        """`{lat, lon, alt}` 스러운 것 -> `(lat, lon, alt|None)`. 좌표가 못 쓸 값이면 None.
        QML dict / 사이드카 dict / 파이썬 tuple 을 모두 같은 규칙으로 받는다."""
        if g is None:
            return None
        try:
            if isinstance(g, (tuple, list)):
                lat, lon = float(g[0]), float(g[1])
                alt = g[2] if len(g) > 2 else None
            else:
                lat, lon = float(g["lat"]), float(g["lon"])
                alt = g.get("alt")
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                return None
            return (lat, lon, float(alt) if alt is not None else None)
        except (TypeError, ValueError, KeyError, IndexError):
            return None

    def _set_gps(self, gps, src: str) -> None:
        """내부 갱신 — 값이 같으면 시그널을 쏘지 않는다(자동저장이 헛돌지 않게)."""
        if self._gps == gps and self._gps_src == src:
            return
        self._gps, self._gps_src = gps, src
        self._refresh_gps_field()
        # Photo map: 디스크를 다시 읽지 않고 그룹만 다시 만든다 — 사이드카는 아직 안
        #   써졌을 수 있다(그 함수 주석의 '열린 사진 덮어쓰기').
        self._regroup_map_points()
        self.gpsChanged.emit()

    def _refresh_gps_field(self) -> None:
        """촬영정보 목록의 'GPS' 행을 지금 붙은 위치로 맞춘다(`I` 오버레이가 이걸 그린다).

        ⚠️`exif_info.read_shooting_info` 는 **파일에 적힌 것**만 안다 — 사용자가 앱에서 붙인
          위치가 우선이므로 행 관리는 여기서 한다. Date 앞에 끼워 촬영정보 뒤쪽에 붙게 둔다."""
        self._exif_fields = [f for f in self._exif_fields if f["label"] != "GPS"]
        if self._gps:
            row = {"label": "GPS", "value": exif_info.format_gps(self._gps[0], self._gps[1])}
            i = next((k for k, f in enumerate(self._exif_fields) if f["label"] == "Date"),
                     len(self._exif_fields))
            self._exif_fields.insert(i, row)
        self.exifChanged.emit()

    @Slot("QVariantMap")
    def setGps(self, g) -> None:  # noqa: N802 (QML 슬롯)
        """현재 사진의 위치를 설정. `{lat, lon, alt(선택), src(선택)}`.
        ⚠️사이드카 저장은 QML `editSaveWatch` -> `commitEditSnapshot` 이 맡는다(여기서 안 쓴다)."""
        gps = self._gps_tuple(g)
        src = ""
        try:
            src = str(g.get("src") or "") if not isinstance(g, (tuple, list)) else ""
        except Exception:
            src = ""
        self._set_gps(gps, src if gps else "")

    @Slot()
    def restoreGpsFromFile(self) -> None:  # noqa: N802 (QML 슬롯)
        """사이드카에 위치 키가 **없을 때** 쓰는 복원 — 파일에 적힌 EXIF GPS 로 되돌린다.

        ★`clearGps()` 와 다르다. 규약(`docs/geotagging.md`)상 **키가 있으면(값이 null 이어도)
        사용자의 뜻**이고, **키가 아예 없으면** 아직 아무도 정한 적이 없다는 뜻이라 파일에 적힌
        좌표를 쓰는 것이 맞다 — `_load` 가 처음 세우는 값과 같아진다.
        ⚠️예전에는 이 자리도 `clearGps()` 여서, 지오태깅 이전 사이드카나 사이드카 없는 사진에서
          **카메라가 남긴 좌표가 로드 직후 지워졌다**(실측 재현).
        ⚠️호출부는 `_applying` 가드 안이라 자동저장이 돌지 않는다 — 안 그러면 편집한 적 없는
          사진에 사이드카가 생겨 '편집됨' 배지가 켜진다.
        """
        path = self._ui_path or self._path
        # 규칙 자체는 `_gps_for_file` 하나뿐이다 — 빈 dict 를 주어 **EXIF 분기**를 태운다.
        gps = _gps_for_file(path, {}) if path else None
        self._set_gps(gps, "exif" if gps else "")

    @Slot()
    def clearGps(self) -> None:  # noqa: N802 (QML 슬롯)
        self._set_gps(None, "")

    @Slot("QVariantList", QUrl, int, result="QVariantMap")
    def applyGpxToPaths(self, paths, gpx_url, utc_offset_sec):  # noqa: N802 (QML 슬롯)
        """GPX 트랙을 체크된 사진들의 **촬영시각**에 맞춰 각 사진의 사이드카에 써 넣는다.

        -> `{matched, unmatched, error}`.

        ⚠️**못 맞춘 사진은 건드리지 않는다** — 로거를 껐던 구간이나 다른 날 사진에 엉뚱한
          좌표가 붙는 것보다 비어 있는 편이 낫다(`gpx.match` 의 tolerance).
        ⚠️`utc_offset_sec` 는 **카메라 시계의 시간대 + 시계 오차**다. EXIF 촬영시각에는
          시간대가 없어(gpx.py 모듈 주석) 이 값 없이는 매칭이 성립하지 않는다.
        ⚠️`applyGpsToPaths` 와 같은 이유로 **지금 열려 있는 사진은 건너뛴다**(디스크만 고치면
          다음 자동저장이 덮는다) — 그 한 장은 매칭 결과를 `setGps` 로 반영한다.
        """
        import gpx as gpx_mod
        try:
            track = gpx_mod.parse(gpx_url.toLocalFile() if isinstance(gpx_url, QUrl)
                                  else str(gpx_url))
        except Exception as exc:
            return {"matched": 0, "unmatched": 0, "error": f"Could not read the GPX: {exc}"}
        if not track:
            return {"matched": 0, "unmatched": 0,
                    "error": "That GPX has no track points with timestamps."}

        cur = os.path.normcase(os.path.abspath(self._ui_path)) if self._ui_path else ""
        matched = unmatched = 0
        for raw in paths:
            path = str(raw)
            if not path:
                continue
            try:
                fields, _ = read_shooting_info(path)
                date = next((f["value"] for f in fields if f["label"] == "Date"), "")
                when = gpx_mod.shot_epoch(date, utc_offset_sec)
                hit = gpx_mod.match(track, when) if when is not None else None
            except Exception as exc:
                print(f"[gpx] {path}: {exc}")
                hit = None
            if hit is None:
                unmatched += 1
                continue
            if os.path.normcase(os.path.abspath(path)) == cur:
                self._set_gps(self._gps_tuple(hit), "gpx")   # 열려 있는 사진은 메모리로
                matched += 1
                continue
            if self._write_gps_sidecar(path, self._gps_tuple(hit), "gpx"):
                matched += 1
            else:
                unmatched += 1
        if matched:
            self._edit_rev += 1
            self.editsChanged.emit()
            self._regroup_map_points()   # Photo map: 루프 뒤 한 번만
        return {"matched": matched, "unmatched": unmatched, "error": ""}

    def _write_gps_sidecar(self, path: str, gps, src: str) -> bool:
        """사이드카의 gps 키만 갈아 끼운다(나머지 편집은 보존). 성공 여부 반환.

        ⚠️`v` 마커를 함께 넣어야 한다 — 없으면 QML `onEditsReady` 가 `e.v === undefined` 로 보고
          **`resetAllEdits()` 로 떨어져** 방금 쓴 위치가 무시된다.
        """
        try:
            p = Path(path)
            data = self._read_edits(path)
            data.setdefault("v", 1)
            data["gpsLat"] = gps[0] if gps else None
            data["gpsLon"] = gps[1] if gps else None
            data["gpsAlt"] = gps[2] if gps else None
            data["gpsSrc"] = src if gps else ""
            data["appVersion"] = APP_VERSION
            d = self._edits_dir(str(p.parent))
            d.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(d / f"{p.name}.json", data)
            # Photo map: 이 폴더를 이미 훑어 뒀으면(또는 훑는 중이면) 방금 쓴 값으로 맞춘다.
            #   ⚠️`_regroup_map_points()` 는 여기서 부르지 않는다 — 일괄 적용이 N번 돌므로
            #     호출부가 루프 뒤에 **한 번만** 부른다.
            #   ⚠️**스캔 중이면 `_map_pending` 에 모은다** — 워커가 이미 지나간 파일이면 결과가
            #     쓰기 이전 스냅샷이라 그냥 `_map_raw` 에 넣어 봐야 곧 덮인다.
            #   ⚠️`_map_paths` 에 있는 경로만 — 짝 JPEG 은 세지 않으므로(한 컷을 두 번 세면
            #     커버리지가 거짓이 된다) 여기서 끼워 넣어도 안 된다.
            target = self._map_folder or self._map_scanning
            if (target and os.path.normcase(str(p.parent)) == os.path.normcase(target)
                    and os.path.normcase(path) in self._map_paths):
                val = (gps[0], gps[1]) if gps else None
                if self._map_busy:
                    self._map_pending[path] = val
                elif val is None:
                    self._map_raw.pop(path, None)
                else:
                    self._map_raw[path] = val
            # 썸네일 '편집됨' 배지 — 위치도 편집이므로 켜지는 게 맞다(사이드카가 생겼다).
            if str(p.parent) == self._edited_folder and p.name not in self._edited:
                self._edited.add(p.name)
            return True
        except Exception as exc:
            print(f"[gps] {path}: {exc}")
            return False

    @Slot("QVariantList", "QVariantMap", result=int)
    def applyGpsToPaths(self, paths, g) -> int:  # noqa: N802 (QML 슬롯)
        """체크한 여러 사진의 **사이드카에 직접** 위치를 써 넣는다. 성공 건수 반환.

        그 사진들은 로드돼 있지 않으므로 QML 편집 파이프라인을 태울 수 없다 — 사이드카를
        읽어 gps 키만 병합하고 다시 쓴다.

        ⚠️**지금 열려 있는 사진은 사이드카에 직접 쓰지 않는다** — 메모리 상태가 그대로라
          다음 자동저장이 방금 쓴 값을 덮는다. 그 한 장은 `_set_gps` 로 반영해 평소의 저장
          경로(editSaveWatch -> commitEditSnapshot)를 타게 한다. ★판정을 QML 로 빼지 말 것 —
          거기서 보이는 `imagePath` 는 `_path` 라 로드 중에는 `_ui_path` 와 다르다.
        ⚠️`v` 마커를 함께 넣어야 한다 — 없으면 QML `onEditsReady` 가 `e.v === undefined` 로 보고
          **`resetAllEdits()` 로 떨어져** 방금 쓴 위치가 무시된다.
        """
        gps = self._gps_tuple(g)
        src = ""
        try:
            src = str(g.get("src") or "")
        except Exception:
            pass
        # ⚠️`"exif"` 는 **그 파일에 카메라가 직접 남긴 좌표**라는 뜻이고, `pipeline.gps_from_params`
        #   가 그 의미에 기대어 export 에서 걸러 낸다("Keep original GPS" 가 판정하도록). 일괄
        #   적용은 사람이 남의 사진에 위치를 붙이는 행위라 출처가 다르다 — 열린 사진의 EXIF
        #   좌표를 퍼뜨릴 때 라벨까지 따라가면(패널 초안이 `controller.gpsSrc` 를 물고 있다)
        #   받은 사진들이 '카메라가 남긴 좌표'로 위장돼 export 에서 조용히 빠진다.
        if src == "exif":
            src = "manual"
        cur = os.path.normcase(os.path.abspath(self._ui_path)) if self._ui_path else ""
        n = 0
        for raw in paths:
            path = str(raw)
            if not path:
                continue
            if os.path.normcase(os.path.abspath(path)) == cur:
                self._set_gps(gps, src if gps else "")   # 열려 있는 사진은 메모리로(위 주석)
                n += 1
                continue
            if self._write_gps_sidecar(path, gps, src):
                n += 1
        if n:
            self._edit_rev += 1
            self.editsChanged.emit()
            self._regroup_map_points()   # Photo map: 루프 뒤 한 번만(위 주석)
        return n

    # ---------- Photo map — 폴더의 좌표를 지도 위 썸네일로 보기 ----------
    #
    # ★**읽기 전용이다.** 셰이더 uniform 0개 · `_PRESET_KEYS`/`LOOK_DEFAULTS`/`editParams()`/
    #   export dict 무변경 → CLAUDE.md 의 ★렌더 경로 4중 계약에 **들어가지 않는다**.
    #   좌표를 **붙이는** 일은 Location 패널(`Ctrl+6`)이 계속 단독으로 담당한다.
    #
    # ★사진 한 장의 좌표를 결정하는 규칙은 모듈 레벨 `_gps_for_file` **하나**다(`_load` 와 공유).
    #   여기에 그 규칙을 다시 적지 말 것 — 복사본은 반드시 갈라진다.

    @Slot()
    def scanFolderGps(self) -> None:  # noqa: N802 (QML 슬롯)
        """현재 폴더의 사진 좌표를 백그라운드로 읽어 `folderMapPoints` 를 채운다.

        ⚠️**디스크 읽기를 메인 스레드에서 하지 않는다** — 실측(840장 폴더): 사이드카 420개가
          cold **1.85s** / warm 119ms 이고, 사이드카가 없는 파일은 EXIF 를 읽는다(**1.2ms/장**,
          840장 ≈ 1.0s). `_scan_worker` 와 같은 이유다(자는 외장 HDD 스핀업이 GUI 를 멈춘다).
        같은 폴더를 이미 읽어 뒀으면 그대로 알리고 끝낸다(오버레이를 여닫을 때마다 재스캔 X).
        """
        folder = self._folder
        if not folder:
            return
        if folder == self._map_scanning:
            return                                # 같은 폴더를 이미 훑는 중 — 연타로 중복 실행 금지
        if folder == self._map_folder and not self._map_busy:
            self.folderMapChanged.emit()          # 이미 가지고 있다(캐시)
            return
        # ★**짝 JPEG(`paired`)은 세지 않는다.** 카메라 RAW+JPEG 동시기록은 **한 컷**이고
        #   사이드카는 RAW 쪽에만 붙는다 — 둘 다 세면 실측 폴더가 "840장 중 420장 위치"로
        #   읽히는데 사실은 **420컷 전부 위치가 있다**(커버리지가 거짓이 된다). 탐색기도
        #   기본으로 접는 항목이라(탐색기 ⧉ 토글) 눈에 보이는 것과도 이쪽이 맞는다.
        #   ⚠️배치 인덱서('항상 폴더 전체')와 규칙이 다르다 — 그쪽은 캡션을 **생성**하므로
        #     빠짐이 손해지만, 여기는 **세는 일**이라 중복이 손해다.
        paths = [it["path"] for it in self._files
                 if not it.get("isDir") and not it.get("paired")]
        self._map_seq += 1
        self._map_busy = True
        self._map_scanning = folder
        self._map_total = len(paths)
        self._map_paths = {os.path.normcase(p) for p in paths}
        self._map_pending = {}
        self._map_raw = {}                        # ⚠️생값도 비운다 — 안 비우면 스캔 중에
        self._map_groups = []                     #   `setGps` 가 들어올 때 이전 폴더의
        self._map_folder = ""                     #   경로들이 다시 묶인다.
        self.folderMapChanged.emit()
        threading.Thread(target=self._map_scan_worker,
                         args=(self._map_seq, folder, paths), daemon=True).start()

    def _map_scan_worker(self, seq: int, folder: str, paths: list) -> None:
        """사진마다 사이드카/EXIF 를 읽어 `{path: (lat, lon)}` 을 만들어 메인에 넘긴다.

        대상은 **항상 폴더 전체**다(배치 인덱서와 같은 규칙 — 검색/좋아요 필터로 좁히지 않고
        짝 JPEG 접기도 보지 않는다. 그래서 카운트는 폴더 기준이다).
        ⚠️`decode_lock` 은 거치지 않는다 — Qt 이미지 디코드를 하지 않고 `exifread` 는 순수
          파이썬이다(그 락이 지키는 이미지 플러그인 뮤텍스가 여기엔 없다).
        """
        raw = {}
        for path in paths:
            if seq != self._map_seq:
                return                            # 폴더를 오가며 더 새 스캔이 시작됨 → 폐기
            try:
                gps = _gps_for_file(path, self._read_edits(path))
            except Exception as exc:
                print(f"[map] {path}: {exc}")
                continue
            if gps:
                raw[path] = (gps[0], gps[1])
        self._mapScanSig.emit((seq, folder, raw, len(paths)))

    @Slot(object)
    def _on_map_scanned(self, payload) -> None:
        seq, folder, raw, total = payload
        if seq != self._map_seq:
            return                                # 더 최신 스캔 진행 중 → 폐기
        # ★스캔이 도는 동안 일괄 적용·GPX 로 쓴 좌표를 결과에 **덮어씌운다.** 워커가 그 파일을
        #   이미 읽고 지나갔으면 결과는 쓰기 이전 스냅샷이라, 그대로 두면 방금 붙인 위치가
        #   지도에서 사라진다(재스캔 전까지).
        for path, val in self._map_pending.items():
            if val is None:
                raw.pop(path, None)
            else:
                raw[path] = val
        self._map_pending = {}
        self._map_raw = raw
        self._map_folder = folder
        self._map_scanning = ""
        self._map_total = total
        self._map_busy = False
        self._regroup_map_points()

    def _regroup_map_points(self) -> None:
        """`_map_raw` → 좌표별 스택. 그룹핑은 **메인 스레드**에서 한다.

        ★⚠️**지금 열린 사진은 디스크 값을 덮어쓴다.** `setGps` 는 메모리만 바꾸고 사이드카는
          QML 의 `saveEdits` 가 나중에 쓴다 — 그냥 재스캔하면 **옛 값**을 읽어 지도가 패널과
          다른 자리를 가리킨다. 그래서 `gpsChanged` 에서는 디스크를 다시 읽지 않고 이 함수만
          다시 돌린다(지도가 열린 채 `Ctrl+Z` 를 눌러도 맞는다).

        키 = 소수점 6자리 ≈ 0.1m (`exif_info.format_gps` 와 같은 정밀도). 일괄 적용된 좌표는
        어차피 비트 동일하다 — 실측 폴더(840장)에서 420장이 겹친 좌표 **4개**에 모인다.
        """
        pts = dict(self._map_raw)
        # 열린 사진 덮어쓰기(위 주석). 그 사진이 이 폴더에 있을 때만.
        # ⚠️`_map_paths` 안의 사진만 — 짝 JPEG 을 끼워 넣으면 한 컷이 두 번 세어져
        #   "420장 중 421장 위치" 같은 카운트가 나온다(스캔 대상에서 뺀 이유와 같다).
        cur = self._ui_path or self._path
        if cur and self._map_folder and os.path.normcase(cur) in self._map_paths and (
                os.path.normcase(str(Path(cur).parent))
                == os.path.normcase(self._map_folder)):
            if self._gps:
                pts[cur] = (self._gps[0], self._gps[1])
            else:
                pts.pop(cur, None)
        groups = {}
        for path, (lat, lon) in pts.items():
            groups.setdefault((round(lat, 6), round(lon, 6)), []).append(path)
        liked = self._likes if self._likes_folder == self._map_folder else set()
        out = []
        for (lat, lon), paths in groups.items():
            paths.sort(key=lambda p: os.path.basename(p).lower())
            # 대표 썸네일 = 그룹 안 **좋아요된 첫 사진**, 없으면 파일명 첫 사진. 장소를 대표하는
            #   그림이 내가 고른 한 장이면 지도가 훨씬 읽힌다(`self._likes` 재사용, 새 I/O 0).
            rep = next((p for p in paths if os.path.basename(p) in liked), paths[0])
            out.append({"lat": lat, "lon": lon, "count": len(paths),
                        "rep": rep, "paths": paths})
        # count 내림순 — 큰 스택이 위에 그려지고 화면좌표 병합에서 대표로 살아남는다.
        out.sort(key=lambda g: (-g["count"], g["lat"], g["lon"]))
        self._map_groups = out
        self.folderMapChanged.emit()

    def _get_map_points(self) -> list:
        return self._map_groups

    def _get_map_busy(self) -> bool:
        return self._map_busy

    def _get_map_folder(self) -> str:
        return self._map_folder

    def _get_map_stats(self) -> dict:
        located = sum(g["count"] for g in self._map_groups)
        return {"photos": self._map_total, "located": located,
                "places": len(self._map_groups)}

    folderMapPoints = Property("QVariantList", _get_map_points, notify=folderMapChanged)
    folderMapStats = Property("QVariantMap", _get_map_stats, notify=folderMapChanged)
    folderMapBusy = Property(bool, _get_map_busy, notify=folderMapChanged)
    folderMapFolder = Property(str, _get_map_folder, notify=folderMapChanged)

    def _exif_field(self, label: str) -> str:
        """캐시된 촬영정보에서 라벨 하나 — 없으면 빈 문자열.
        read_shooting_info 가 값이 있는 행만 담으므로, 없는 라벨은 자연히 빈 값이 된다."""
        return next((f["value"] for f in self._exif_fields if f["label"] == label), "")

    @Slot(result="QVariantMap")
    def presetSource(self):  # noqa: N802 (QML 슬롯)
        """레시피 프리셋에 기록할 **출처**. 저장 시 기록과 불러올 때의 비교가 같은 것을 쓴다.

        ⚠️EXIF 를 다시 읽지 않는다 — `_load` 가 이미 `_exif_fields` 에 캐시해 뒀다.
        ⚠️값이 없으면 빈 문자열이고 **그게 정상인 경우가 많다**: 우리가 export 한 JPEG 은 Qt 가
          EXIF 를 안 써서 태그가 0개이고, PNG 은 애초에 없다. 렌즈는 고정렌즈 바디·구형 RAW·
          MakerNote 전용 기록에서 비므로 초점거리가 실질적인 식별자다(exif_info 주석 참조).
        ⚠️필름 스캔은 `camera` 가 스캐너 이름(`NORITSU KOKI EZ Controller`)이다. 가공하지 않고
          그대로 기록한다 — 실제로 그 장비에서 나온 것이 맞고, 임의로 바꾸면 더 헷갈린다."""
        return {
            "camera": self._exif_field("Camera"),
            "lens": self._exif_field("Lens"),
            "focalLength": self._exif_field("Focal Length"),
            "aperture": self._exif_field("Aperture"),
            "iso": self._exif_field("ISO"),
            # 촬영일은 날짜만(시각은 출처 표시에 불필요하고 파일명에도 못 쓴다)
            "shotDate": self._exif_field("Date")[:10],
        }

    def _get_stamp_url(self) -> str:
        return self._stamp_url

    def _get_stamp_text(self) -> str:
        return self._stamp_text

    def _get_stamp_wr(self) -> float:
        return self._stamp_wr

    def _get_stamp_hr(self) -> float:
        return self._stamp_hr

    def _get_stamp_rot(self) -> int:
        return self._stamp_rot

    def _get_stamp_corner(self) -> str:
        import date_stamp
        return date_stamp.corner_for_rot(self._stamp_rot)

    def _get_stamp_font(self) -> str:
        return self._stamp_font

    def _get_stamp_size(self) -> float:
        return self._stamp_size

    def _get_stamp_margin(self) -> float:
        return self._stamp_margin

    stampUrl = Property(str, _get_stamp_url, notify=stampSpriteChanged)
    stampText = Property(str, _get_stamp_text, notify=stampChanged)
    stampWRatio = Property(float, _get_stamp_wr, notify=stampSpriteChanged)   # 스프라이트 W/짧은변
    stampHRatio = Property(float, _get_stamp_hr, notify=stampSpriteChanged)   # 스프라이트 H/짧은변
    # ⚠️회전/코너는 **스프라이트 세대**에 속한다 — 스프라이트가 rot 로 미리 회전돼 구워지므로
    #   (sprite_layer(rot=...)), 코너만 먼저 새 사진 값으로 바뀌면 이전 사진의 스프라이트가
    #   새 코너에 잠깐 그려진다(가로→세로 전환에서 수십 ms). bleed 와 같은 부류다.
    stampRot = Property(int, _get_stamp_rot, notify=stampSpriteChanged)     # 촬영 방향 CW 회전(export 전달)
    stampCorner = Property(str, _get_stamp_corner, notify=stampSpriteChanged)  # 데이트백 코너(프리뷰 배치)
    stampFont = Property(str, _get_stamp_font, notify=stampChanged)       # 폰트 방식(STYLES 키)
    stampSize = Property(float, _get_stamp_size, notify=stampChanged)     # 크기(숫자높이/짧은변 비율)
    def _get_stamp_color(self) -> str:
        return self._stamp_color

    def _get_stamp_glow(self) -> float:
        return self._stamp_glow

    def _get_stamp_spread(self) -> float:
        return self._stamp_spread

    def _get_stamp_bleed(self) -> float:
        """글로우 영역 변화분(짧은 변 대비 비율) — QML 오버레이가 마진에서 빼서 글자 위치를
        고정한다. export(stamp_export)가 쓰는 date_stamp.bleed_frac 과 **같은 값**이라야
        프리뷰=export 가 유지된다.

        ⚠️**지금 화면에 떠 있는 스프라이트의 값**이다(`_on_stamp_sprite` 가 wr/hr 과 함께 심는다).
        여기서 `self._stamp_spread` 로 즉시 계산하면 안 된다 — 스프라이트는 워커에서 오므로
        최대 56ms 늦고, 그동안 '새 마진 + 옛 스프라이트' 조합이 되어 **Area 를 끄는 동안 글자가
        진동한다**(실제로 그렇게 만들었다가 사용자 보고로 잡았다). 넷(url·wr·hr·bleed)은 항상
        한 세대로 함께 바뀌어야 한다."""
        return self._stamp_bleed

    def _get_stamp_fonts(self) -> list:
        """폰트 콤보 모델: 번들 + 사용자 추가. 각 항목 {key, label, user}.
        ⚠️키(=사이드카에 저장되는 값)와 표시명을 **한 곳에서** 만든다 — QML 에 라벨 배열과
        키 배열을 따로 두면 순서가 어긋나 다른 폰트가 저장되던 과거 방식의 재발을 막는다."""
        import date_stamp
        out = [{"key": k, "label": v, "user": False}
               for k, v in self._STAMP_FONT_LABELS.items() if k in date_stamp.STYLES]
        for st in date_stamp.user_font_styles():
            out.append({"key": st, "label": st[len(date_stamp.USER_PREFIX):], "user": True})
        return out

    def _get_stamp_font_missing(self) -> bool:
        """현재 폰트 파일이 없는가 — 남의 사용자 폰트로 만든 사이드카/레시피를 열면 발생한다.
        QML 이 이때 안내를 띄우고, 렌더는 기본 데이트백 폰트로 폴백한다."""
        import date_stamp
        return not date_stamp.has_font(self._stamp_font)

    def _get_stamp_colors(self) -> list:
        """각인 색 팔레트(QML 스와치). 기본 앰버 = 쿼츠 데이트백, 중성 백색 = 흑백 사진용."""
        import date_stamp
        return list(date_stamp.COLORS)

    stampMargin = Property(float, _get_stamp_margin, notify=stampChanged) # 코너 여백/짧은변 비율(프리뷰 배치용)
    stampColor = Property(str, _get_stamp_color, notify=stampChanged)     # 각인 색(hex)
    stampGlow = Property(float, _get_stamp_glow, notify=stampChanged)     # 글로우 밝기 배율
    stampSpread = Property(float, _get_stamp_spread, notify=stampChanged) # 글로우 영역 배율
    stampBleed = Property(float, _get_stamp_bleed, notify=stampSpriteChanged)  # 글로우 여유 변화분/짧은변
    stampColors = Property(list, _get_stamp_colors, constant=True)        # 색 팔레트
    stampFonts = Property(list, _get_stamp_fonts, notify=stampFontsChanged)   # 폰트 목록(번들+사용자)
    stampFontMissing = Property(bool, _get_stamp_font_missing, notify=stampChanged)

    # 폰트 표시명(키 = date_stamp.STYLES 키). 사용자 폰트는 파일명을 그대로 쓴다.
    _STAMP_FONT_LABELS = {
        "7c_reg": "7-seg Classic Regular", "7c_reg_it": "7-seg Classic Regular Italic",
        "7c_bold": "7-seg Classic Bold", "7c_bold_it": "7-seg Classic Bold Italic",
        "14c_reg": "14-seg Classic Regular", "14c_reg_it": "14-seg Classic Regular Italic",
        "14c_bold": "14-seg Classic Bold", "14c_bold_it": "14-seg Classic Bold Italic",
        "dotmatrix": "Dot-matrix", "typewriter": "Typewriter (Courier Prime)",
        "terminal": "Terminal (VT323)", "condensed": "Condensed (Oswald)",
    }

    @Slot(QUrl, result=bool)
    def addStampFont(self, url: QUrl) -> bool:  # noqa: N802 (QML 슬롯)
        """사용자가 고른 .ttf/.otf 를 사용자 폰트 폴더로 복사하고 곧바로 선택한다.
        윈도우 폰트를 골라도 같은 경로다(복사하므로 원본과 무관해진다)."""
        import date_stamp
        style = date_stamp.add_user_font(url.toLocalFile() if url.isLocalFile() else str(url))
        if not style:
            return False
        self.stampFontsChanged.emit()
        self.setStampFont(style)
        return True

    @Slot(str, result=bool)
    def removeStampFont(self, style: str) -> bool:  # noqa: N802 (QML 슬롯)
        """추가한 사용자 폰트를 지운다. 그 폰트를 쓰던 사진은 기본 폰트로 폴백해 열린다."""
        import date_stamp
        if not date_stamp.remove_user_font(style):
            return False
        self._stamp_prefs_cache = None      # 내 기본값이 이 폰트를 가리켰다면 위 검증으로 되돌린다
        self.stampDefaultsChanged.emit()
        if self._stamp_font == style:
            self.setStampFont(date_stamp.DEFAULT_STYLE)
        self.stampFontsChanged.emit()
        return True

    # ---------- 스탬프 '내 기본값'(사용자 데이터 폴더 JSON) ----------
    # 사진 여러 장을 연속 작업할 때 폰트·크기·여백을 매번 다시 잡는 것이 힘들다는 피드백에서
    # 나왔다. ⚠️우선순위는 **사이드카 > 이 기본값 > 공장 기본값** — 사이드카가 있는 사진의
    # 룩은 절대 바뀌지 않고, 이 값은 '사이드카가 없는 새 사진의 출발점'만 정한다.
    # ⚠️QML 이 이 값을 읽는 곳은 **사이드카 없는 새 사진의 로드 경로 하나뿐**이다
    # (`resetAllEdits()` 인자 없이). **Reset 버튼(`resetAllEdits(true)`)·슬라이더 더블클릭·
    # `applyEdits` 의 `_ev` 폴백은 공장 기본값**을 쓴다 — 폴백을 내 기본값으로 두면 스탬프 키가
    # 없던 시절의 옛 사이드카를 열 때 없던 각인이 켜지고, 더블클릭·Reset 을 내 기본값으로 두면
    # 슬라이더 릴리즈마다 그 값이 기억되므로 '기본값 == 현재값'이 되어 무동작이 된다.
    # ⚠️Reset 은 이 값을 **쓰기도 한다**(리셋 결과가 다음 사진으로 이어지도록) — 단 폰트는
    # 리셋 대상이 아니라 기억도 하지 않는다(QML `rememberStamp(true)`).
    _STAMP_PREF_DEFAULTS = {"stampOn": False, "stampStyle": "7c_bold",
                            "stampSize": 0.032, "stampMargin": 0.05,
                            "stampColor": "#ff8a29", "stampGlow": 1.0, "stampSpread": 1.0}

    def _stamp_prefs(self) -> dict:
        if self._stamp_prefs_cache is None:
            import date_stamp
            USER_PREFIX_, _has_font = date_stamp.USER_PREFIX, date_stamp.has_font
            d = dict(self._STAMP_PREF_DEFAULTS)
            try:
                p = Path(stamp_prefs_path())
                if p.is_file():
                    with open(p, encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        d.update(self._sane_stamp_prefs(raw))
            except Exception:
                pass                      # 손상 시 공장 기본값(다음 저장에 덮어씀)
            # ⚠️사용자 폰트가 사라진 경우(Remove 로 지웠거나 폴더에서 직접 지웠거나) 그 키를
            #   기본값에 남겨두면 **사이드카 없는 새 사진마다** 누락 배너가 뜬다 — 정작 그
            #   폰트를 참조하는 사진은 없는데도. 읽는 시점에 걸러 기본 폰트로 되돌린다.
            st = str(d.get("stampStyle", ""))
            if st.startswith(USER_PREFIX_) and not _has_font(st):
                d["stampStyle"] = self._STAMP_PREF_DEFAULTS["stampStyle"]
            self._stamp_prefs_cache = d
        return self._stamp_prefs_cache

    def _sane_stamp_prefs(self, raw: dict) -> dict:
        """저장 파일은 사용자가 손댈 수 있다 — 타입·범위를 통과한 키만 받는다.
        (범위를 벗어난 값이 슬라이더/스프라이트로 들어가면 클램프 위치가 UI 와 어긋난다.)"""
        import date_stamp
        out = {}
        if "stampOn" in raw:
            out["stampOn"] = bool(raw["stampOn"])
        st = str(raw.get("stampStyle", ""))
        # 사용자 추가 폰트(user:<파일명>)도 받는다 — 안 받으면 추가한 폰트를 '내 기본값'으로
        # 기억할 수 없다. 파일이 사라진 경우는 font_family 가 기본 폰트로 폴백하고 QML 이 알린다.
        if st in date_stamp.STYLES or (st.startswith(date_stamp.USER_PREFIX)
                                       and len(st) > len(date_stamp.USER_PREFIX)):
            out["stampStyle"] = st
        if "stampColor" in raw:
            c = QColor(str(raw["stampColor"]))
            if c.isValid():
                out["stampColor"] = c.name()      # #rrggbb 로 정규화(알파·표기 흔들림 제거)
        for key, lo, hi in (("stampSize", date_stamp.SIZE_FRAC_MIN, date_stamp.SIZE_FRAC_MAX),
                            ("stampMargin", 0.0, 0.10),
                            ("stampGlow", date_stamp.GLOW_MIN, date_stamp.GLOW_MAX),
                            ("stampSpread", date_stamp.SPREAD_MIN, date_stamp.SPREAD_MAX)):
            try:
                v = float(raw[key])
            except (KeyError, TypeError, ValueError):
                continue
            if lo <= v <= hi:
                out[key] = v
        return out

    def _get_stamp_defaults(self) -> dict:
        return dict(self._stamp_prefs())

    stampDefaults = Property("QVariantMap", _get_stamp_defaults, notify=stampDefaultsChanged)

    @Slot("QVariantMap")
    def rememberStampPrefs(self, prefs: dict) -> None:  # noqa: N802 (QML 슬롯)
        """사용자가 스탬프 컨트롤을 **직접** 바꿨을 때만 호출된다(QML 이 _applying 중에는 호출
        하지 않는다). ⚠️로드/리셋의 프로그램 대입까지 저장하면 사이드카가 있는 옛 사진을
        열기만 해도 '내 기본값'이 그 사진 값으로 덮여, 기능의 목적이 무너진다."""
        cur = self._stamp_prefs()
        new = self._sane_stamp_prefs(dict(prefs or {}))
        if not new or all(cur.get(k) == v for k, v in new.items()):
            return                        # 변화 없음 — 디스크에 쓰지 않는다
        cur.update(new)
        try:
            _atomic_write_json(stamp_prefs_path(), cur)
        except Exception as exc:
            print(f"[stamp] 기본값 저장 실패: {exc}")
        self.stampDefaultsChanged.emit()

    def _compute_histogram(self, img: QImage) -> None:
        """프록시 QImage → 히스토그램용 축소본 캐시 + 기준(입력) 히스토그램.

        프록시는 헤드룸 인코딩 카메라네이티브라, 셰이더 프론트엔드와 동일하게
        scene-linear sRGB(as-shot WB)로 디코드해 캐시하고, 기준 히스토그램은 filmic 적용본."""
        import numpy as np
        im = img.convertToFormat(QImage.Format.Format_RGB888)
        w, h = im.width(), im.height()
        if w == 0 or h == 0:
            self._proxy_small = None
            self._histogram = []
        else:
            arr = (np.frombuffer(im.constBits(), np.uint8)
                   .reshape(h, im.bytesPerLine())[:, :w * 3].reshape(h, w, 3))
            step = max(1, max(h, w) // 128)          # 히스토그램용 소형 축소본(드래그 중 가벼움)
            small = arr[::step, ::step].astype(np.float32)
            # ±0.5LSB 디더: 프록시는 8bit(코드 256단계)라 그대로 비닝하면, 기울기>1 인 구간
            # (filmic 그림자부·필름시뮬 LUT)에서 입력 단계가 벌어져 빈 bin 이 생긴다 = 빗살 스파이크.
            # 양자화 전 연속 분포를 복원해 준다. 고정 시드 + 로드 시 1회 → 드래그 중 깜빡임 없음.
            small += np.random.default_rng(0).uniform(-0.5, 0.5, small.shape).astype(np.float32)
            small = np.clip(small, 0.0, 255.0) / 255.0
            self._proxy_small = self._native_to_scenelinear(small)   # scene-linear sRGB
            self._histogram = self._hist_of(wb.filmic(self._proxy_small))  # 기준(노출0) display
        self.histogramChanged.emit()

    def _native_to_scenelinear(self, arr, u8=None):
        """헤드룸 인코딩 카메라네이티브(0..1) → scene-linear sRGB(filmic 전). 셰이더 프론트엔드와 동일.

        u8: arr 의 원본 uint8 배열(있으면 arr 은 무시 가능). 프록시는 8bit 라 srgb_to_linear 의
        입력이 256가지뿐이므로 `raw_loader._srgb2lin_lut()` 조회로 대체한다 — 프록시 전체 기준
        float 변환+pow 273ms → LUT 76ms 이고 **오차 0**이다(LUT 은 srgb_to_linear(i/65535) 이고
        u8*257/65535 == u8/255 가 정확히 성립 — 257×255 = 65535).

        ⚠️ as-shot 게인은 반드시 **tint 포함**(convert.frag 의 relR/G/B = wbPreview(asShotKelvin,
        asShotTint) 와 일치). 과거 tint=0 으로 계산해 off-locus 광원(tint≠0)에서 이 함수의 결과와
        셰이더 dispSrc 가 채널별 게인만큼 어긋났고, AI RGB 베이스(nrBase)의 chroma 를 s0 와 빼는
        컬러 NR 에서 청록 캐스트로 드러났음(pipeline 의 neutral_disp 는 원래 tint 포함 — export 정상)."""
        import numpy as np
        if not self._cam2srgb or not self._cam or not self._ref:
            return arr if arr is not None else (u8.astype(np.float32) / 255.0)
        M = np.asarray(self._cam2srgb, float).reshape(3, 3)
        cam = np.asarray(self._cam, float).reshape(3, 3)
        rel = wb.rel_gain(cam, np.asarray(self._ref, float), self._asshot, self._asshot_tint)
        if u8 is not None:                                    # 8bit 입력 → LUT 조회(오차 0, 3.6×)
            import raw_loader                                 # 모듈 레벨 import 아님(_load_heavy_modules)
            lin0 = raw_loader._srgb2lin_lut()[u8.astype(np.uint16) * 257]
        else:
            lin0 = wb.srgb_to_linear(arr)
        lin = lin0 * PROXY_HEADROOM * rel                     # 헤드룸 디코드 + as-shot WB
        return (lin @ M.T).astype(np.float32)                 # scene-linear sRGB

    @staticmethod
    def _hist_of(c) -> list:
        """R/G/B 3채널 히스토그램(각 256-bin)을 공통 최대값으로 정규화해 [R,G,B] 반환.
        공통 정규화라 채널 간 상대 크기 비교 가능(라이트룸식 중첩 표시).

        표본이 소형 축소본(~1만 px)이라 bin 당 계수가 40 내외 → 포아송 잡음(±15%)이 그대로
        뾰족한 스파이크로 보인다. 내부 bin 만 가우시안(σ=1.5bin)으로 평활해 분포 추정치로 표시.
        0/255 는 클리핑 경고라 평활 대상에서 제외(끝단 스파이크 보존, 안쪽으로 번지지도 않음)."""
        import numpy as np
        hists = [np.histogram(c[..., ch], bins=256, range=(0.0, 1.0))[0].astype(np.float32)
                 for ch in range(3)]
        k = np.exp(-0.5 * (np.arange(-5, 6, dtype=np.float32) / 1.5) ** 2)
        k /= k.sum()
        for hh in hists:
            hh[1:255] = np.convolve(np.pad(hh[1:255], 5, mode="edge"), k, "valid")
        m = max(float(h.max()) for h in hists)
        return [(h / m).tolist() for h in hists] if m > 0 else []

    def _get_lut(self, key):
        if key not in self._lut_cache:
            try:
                import lut as lut_mod
                self._lut_cache[key] = load_cube(str(lut_mod.lut_path(key, LUTS_DIR)))
            except Exception:
                self._lut_cache[key] = (None, 0)
        return self._lut_cache[key]

    @Slot(str, float)
    def setFilmSim(self, key: str, strength: float) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 필름시뮬 선택/강도를 알려준다 → 보정 노출 재계산(pipeline.film_sim_ev)."""
        key = key or "identity"
        if key == self._sim_key and abs(float(strength) - self._sim_strength) < 1e-6:
            return
        self._sim_key = key
        self._sim_strength = float(strength)
        self._update_sim_ev()

    def _update_sim_ev(self) -> None:
        """필름시뮬 보정 노출 재계산 — 이미지/시뮬/강도가 바뀔 때만(다른 슬라이더와 무관).

        번들 LUT 이 담고 있는 후지 톤커브가 filmic 위에 두 번 걸리는 것을 상쇄한다
        (pipeline.film_sim_ev 주석 참조 — 그게 없으면 필름시뮬만 켜도 +0.8~1.4EV 밝아진다).
        ⚠️앵커는 유저 편집 전 as-shot 베이스(`_proxy_small`)라 export(render_full)가 스스로
        계산하는 값과 표본만 다르다(실측 차 ≤0.005EV) — 프리뷰=Export 가 유지된다."""
        import lut as lut_mod
        ev = 0.0
        # ★**사용자 LUT 은 보정하지 않는다.** `film_sim_ev` 는 *번들 후지 LUT 이 들고 있는
        #   톤커브*가 filmic 위에 두 번 걸리는 것을 상쇄하는 함수다(그쪽 주석). 남의 .cube 에는
        #   상쇄할 그 톤커브가 없고, 밝기 자체가 작가의 룩이다. 게다가 그 솔버는 med(ev) 의
        #   **단조증가를 가정**하므로(pipeline 탐색 범위 주석) 크로스프로세싱 류 LUT 에서는
        #   안 거는 쪽이 더 안전하다. → LUT 의 밝기가 그 파일에 남고, `simKey` 가 이미
        #   `_PRESET_KEYS` 에 있으므로 **레시피를 통해 그대로 전달된다**(exposure 를 룩 키로
        #   만들 필요가 없어지는 지점 — docs/recipe_presets.md).
        #   ⚠️pipeline.render_full 에도 같은 게이트가 있어야 한다(프리뷰=CPU export).
        if (self._proxy_small is not None and self._sim_key not in ("", "identity")
                and self._sim_strength > 0.0 and not lut_mod.is_user(self._sim_key)):
            try:
                import pipeline
                arr, n = self._get_lut(self._sim_key)
                # ⚠️자동노출을 끈 상태면 베이스가 그만큼 어둡다 — 그 베이스에서 풀어야 맞는다.
                off = self._get_auto_off_ev()
                sample = (self._proxy_small if off == 0.0
                          else self._proxy_small * float(2.0 ** off))
                ev = pipeline.film_sim_ev(sample, arr, n, self._sim_strength)
            except Exception as exc:                 # 보정 실패는 룩만 놓칠 뿐 — 로드를 막지 않는다
                print(f"[filmsim] 보정 노출 계산 실패(무시): {exc}")
                ev = 0.0
        if abs(ev - self._sim_exp_ev) > 1e-4:
            self._sim_exp_ev = ev
            self.simExpEVChanged.emit()

    def _get_sim_exp_ev(self) -> float:
        return self._sim_exp_ev

    # 셰이더 uniform(pipe/pipeFull simExpEV). export 는 render_full 이 자체 계산한다.
    simExpEV = Property(float, _get_sim_exp_ev, notify=simExpEVChanged)

    def _get_clip_level(self) -> float:
        return self._clip_level

    # 센서 포화 레벨(scene-linear) — 하이라이트 디새추가 '진짜 클립'에서만 걸리게 하는 게이트 기준.
    # 셰이더 uniform(pipe/pipeFull/comparePipe). export 는 render_full 이 자체 계산한다.
    clipLevel = Property(float, _get_clip_level, notify=clipLevelChanged)

    @Slot("QVariantMap")
    def updateHistogram(self, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조절값을 축소 프록시에 numpy 로 적용해 '조절 반영' 히스토그램을 재계산.
        라이트룸처럼 색 단계 전부 반영: 미스트/노출/톤/LUT/채도·바이브런스/HSL/대비/커브/
        컬러그레이딩/비네팅. (그레인은 노이즈라 제외, 로컬대비/샤프닝 등 공간 단계는 생략)"""
        if self._proxy_small is None:
            return
        import numpy as np
        import pipeline
        c = self._proxy_small.copy()                       # scene-linear sRGB
        # 미스트(1단계) — 노출 **앞**. 베일이 블랙을 들어올리므로 히스토그램에 반영해야 한다.
        # ⚠️두 가지를 근사한다. ① `_proxy_small` 은 WB·매트릭스가 **이미 적용된** 선형 sRGB 라
        #   실제 단계(카메라네이티브)와 공간이 다르다 — 미스트 자체는 선형이라 교환되지만
        #   하이라이트 보상 E 의 임계는 공간 의존이다. ② 축소본이 긴 변 128px 라 σ 가
        #   (0.3, 1.3, 5.1)px 로 좁은 성분은 사실상 항등이다 — 분포를 움직이는 넓은 성분·균일항은
        #   살아 있으므로 '블랙이 얼마나 뜨는가' 는 맞는다. 분포 추정치로는 충분하고, 공간
        #   단계(로컬대비·샤프닝)를 생략하는 기존 태도와 같다.
        _mist_amt = float(params.get("mistAmt", 0.0))
        if _mist_amt > 0.0:
            import mist
            c = mist.apply(c, _mist_amt, float(params.get("mistChar", 0.0)),
                           float(params.get("mistRadius", 1.0)),
                           float(params.get("mistHi", 0.8)), max(c.shape[:2]),
                           color=float(params.get("mistColor", 0.5)))
        # 노출 = scene-linear 배수 → filmic(단일 톤커브) → display. (셰이더/export 와 동일 순서)
        c = wb.filmic(c * (2.0 ** (float(params.get("exposure", 0.0)) + self._sim_exp_ev
                                   + self._get_auto_off_ev())))
        c = np.maximum(pipeline._tone_zones(
            c, float(params.get("highlights", 0)), float(params.get("shadows", 0)),
            float(params.get("whites", 0)), float(params.get("blacks", 0))), 0.0)
        c = np.clip(c, 0.0, 1.0)
        if params.get("lutEnabled", False):
            arr, n = self._get_lut(params.get("simKey", "identity"))
            if arr is not None:
                looked = pipeline._apply_lut3d(c, arr, n)
                st = float(params.get("lutStrength", 1.0))
                c = c * (1.0 - st) + looked * st
        # 바이브런스/채도 → HSL 컬러 믹서 (셰이더/export 와 동일: 대비 앞)
        sat = float(params.get("saturation", 0)); vib = float(params.get("vibrance", 0))
        if sat != 0.0 or vib != 0.0:
            c = pipeline._presence(c, sat, vib)
        c = pipeline._hsl_mixer(c, params.get("hslH", [0.0] * 8),
                                params.get("hslS", [0.0] * 8), params.get("hslL", [0.0] * 8))
        c = np.clip((c - 0.5) * float(params.get("contrast", 1.0)) + 0.5, 0.0, 1.0)
        curves = params.get("curves", None)
        if curves and len(curves) == 4:
            crgb = pipeline.compose_curves(*curves)
            xs = np.linspace(0.0, 1.0, 256)
            for ch in range(3):
                c[..., ch] = np.interp(c[..., ch], xs, crgb[:, ch])
        # 컬러 그레이딩(톤커브 뒤) — render_full 과 동일(hue 도→0..1)
        c = pipeline._color_grade(
            c, float(params.get("cgShadowHue", 0)) / 360.0, float(params.get("cgShadowSat", 0)),
            float(params.get("cgMidHue", 0)) / 360.0, float(params.get("cgMidSat", 0)),
            float(params.get("cgHighHue", 0)) / 360.0, float(params.get("cgHighSat", 0)),
            float(params.get("cgBalance", 0)))
        # 비네팅(정규화 좌표 — render_full 과 동일 공식). 그레인은 노이즈라 제외.
        vig = float(params.get("vignette", 0))
        if vig != 0.0:
            h2, w2 = c.shape[:2]
            yy = (np.arange(h2, dtype=np.float32) / (h2 - 1)) - 0.5
            xx = (np.arange(w2, dtype=np.float32) / (w2 - 1)) - 0.5
            rr = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2) / 0.7071
            import coeffs
            c = np.clip(c * (1.0 + vig * coeffs.VIGNETTE * pipeline._smoothstep(0.35, 1.0, rr))[..., None], 0.0, 1.0)
        self._histogram = self._hist_of(c)
        self.histogramChanged.emit()

    def _get_histogram(self) -> list:
        return self._histogram

    histogram = Property("QVariantList", _get_histogram, notify=histogramChanged)

    # ---------------------------------------------------------------- RAW Peek
    # 디모자이크 **이전**(pre-demosaic) 센서 데이터 뷰. 읽기 전용 진단이라 룩 파라미터·셰이더
    # uniform·export dict 를 하나도 건드리지 않는다(CLAUDE.md '★ 렌더 경로' 체크리스트 무관).

    def _get_raw_peek_available(self) -> bool:
        """현재 사진이 RAW 인가 — 일반 이미지(JPG/PNG/TIFF)는 CFA 가 없어 뷰가 성립하지 않는다."""
        p = self._ui_path or self._path
        return bool(p) and os.path.splitext(p)[1].lower() in RAW_EXTS

    # ⚠️notify 를 `imageChanged` 로 두면 **한 장 뒤처진다** — 그 시그널은 `_ui_path` 갱신
    #   전에 나가고 갱신 뒤에는 아무 시그널이 없다. 전용 시그널로 확정 시점에 알린다.
    rawPeekAvailable = Property(bool, _get_raw_peek_available,
                                notify=rawPeekAvailChanged)

    def _get_raw_peek_open(self) -> bool:
        # ★"데이터 준비됨"만 뜻한다(로딩 중은 rawPeekBusy). 여기에 `or self._peek_busy` 를
        #   넣었더니 QML 이 '열림' 전이를 로딩 중에 감지해 첫 렌더를 요청하고, 그때 _peek 이
        #   아직 None 이라 rawPeekView 가 조용히 반환해 **화면이 비었다.**
        return self._peek is not None

    def _get_raw_peek_busy(self) -> bool:
        return self._peek_busy

    def _get_raw_peek_info(self) -> str:
        return self._peek_info

    def _get_raw_peek_url(self) -> str:
        return self._peek_url

    def _get_raw_peek_pattern_url(self) -> str:
        return self._peek_pattern_url

    def _get_raw_peek_hist_url(self) -> str:
        return self._peek_hist_url

    # visible 크기 — QML 팬이 화면 이동량을 센서 픽셀로 환산할 때 쓴다.
    def _get_raw_peek_vis_w(self) -> int:
        return int(self._peek.vis_w) if self._peek is not None else 0

    def _get_raw_peek_vis_h(self) -> int:
        return int(self._peek.vis_h) if self._peek is not None else 0

    rawPeekVisW = Property(int, _get_raw_peek_vis_w, notify=rawPeekChanged)
    rawPeekVisH = Property(int, _get_raw_peek_vis_h, notify=rawPeekChanged)

    def _get_raw_peek_caption(self) -> str:
        return self._peek_caption

    rawPeekCaption = Property(str, _get_raw_peek_caption, notify=rawPeekChanged)

    def _get_raw_peek_status(self) -> str:
        return self._peek_status

    rawPeekStatus = Property(str, _get_raw_peek_status, notify=rawPeekChanged)

    def _get_raw_peek_progress(self) -> float:
        return self._peek_prog

    rawPeekProgress = Property(float, _get_raw_peek_progress, notify=rawPeekChanged)

    def _get_raw_peek_default_cx(self) -> float:
        return float(self._peek_center[0])

    def _get_raw_peek_default_cy(self) -> float:
        return float(self._peek_center[1])

    # 오픈 시 기본 팬 위치 — 화면 중앙은 평탄면일 때가 많고, 그러면 Demosaic 후보 4개가
    # 똑같아 보여 비교가 무의미해진다. 미니맵용 축소본에서 디테일 있는 중간톤을 고른다.
    rawPeekDefaultCx = Property(float, _get_raw_peek_default_cx, notify=rawPeekChanged)
    rawPeekDefaultCy = Property(float, _get_raw_peek_default_cy, notify=rawPeekChanged)

    def _get_raw_peek_mini_url(self) -> str:
        return self._peek_mini_url

    rawPeekMiniUrl = Property(str, _get_raw_peek_mini_url, notify=rawPeekChanged)

    def _get_raw_peek_rect(self) -> list:
        """미니맵의 '지금 보는 영역' — 센서 픽셀 [x, y, w, h]. 해당 없으면 빈 리스트.

        ⚠️QML 이 zoom/뷰크기로 추정하면 모드마다 틀린다(Planes 는 폭을 색 수로 나누고,
        Demosaic 는 정사각, Boundary 는 크롭 개념이 없다) → raw_peek 이 실제로 자른 값을 쓴다."""
        r = getattr(self._peek, "last_rect", None) if self._peek is not None else None
        return [int(v) for v in r] if r else []

    rawPeekRect = Property("QVariantList", _get_raw_peek_rect, notify=rawPeekChanged)

    def _get_raw_peek_scale(self) -> float:
        """지금 그려진 배율(표시 픽셀 / 센서 픽셀) — QML 드래그가 화면 이동량을 센서 픽셀로
        환산할 때 쓴다. ⚠️`zoom` 으로 환산하면 안 된다: Demosaic 은 패널이 화면의 1/n 이고
        고배율에서 캡도 걸려 요청 zoom 과 실제 배율이 다르다."""
        v = getattr(self._peek, "last_scale", 1.0) if self._peek is not None else 1.0
        return float(v) if v else 1.0

    rawPeekScale = Property(float, _get_raw_peek_scale, notify=rawPeekChanged)

    # 모드·뷰포트별로 **실제로 서로 다른 결과가 나오는** 줌 범위. QML 이 휠/버튼을 이 안으로
    # 클램프한다 — 안 하면 무동작 칸이나 같은 상태가 두 번 생긴다(raw_peek.zoom_range 주석).
    # 상태 순서 문제를 피하려고 프로퍼티가 아니라 인자를 받는 슬롯으로 둔다.
    @Slot(int, int, int, result=int)
    def rawPeekZoomMin(self, mode: int, w: int, h: int) -> int:  # noqa: N802
        return self._raw_peek_zoom_range(mode, w, h)[0]

    @Slot(int, int, int, result=int)
    def rawPeekZoomMax(self, mode: int, w: int, h: int) -> int:  # noqa: N802
        return self._raw_peek_zoom_range(mode, w, h)[1]

    def _raw_peek_zoom_range(self, mode: int, w: int, h: int):
        if self._peek is None:
            return 1, 32
        import raw_peek
        try:
            return raw_peek.zoom_range(self._peek, int(mode), int(w), int(h))
        except Exception:
            return 1, 32

    rawPeekOpened = Property(bool, _get_raw_peek_open, notify=rawPeekChanged)
    rawPeekBusy = Property(bool, _get_raw_peek_busy, notify=rawPeekChanged)
    rawPeekInfo = Property(str, _get_raw_peek_info, notify=rawPeekChanged)
    rawPeekUrl = Property(str, _get_raw_peek_url, notify=rawPeekChanged)
    rawPeekPatternUrl = Property(str, _get_raw_peek_pattern_url, notify=rawPeekChanged)
    rawPeekHistUrl = Property(str, _get_raw_peek_hist_url, notify=rawPeekChanged)

    @Slot()
    def rawPeekOpen(self) -> None:  # noqa: N802 (QML 슬롯)
        """현재 사진의 디모자이크 이전 데이터를 워커에서 읽어들인다(rawpy.imread ~0.6s)."""
        path = self._ui_path or self._path
        if not path or not self._get_raw_peek_available():
            return
        if self._peek is not None and self._peek_path == path:
            self.rawPeekChanged.emit()        # 같은 사진 → 이미 로드된 것을 그대로 쓴다
            return
        self._peek_seq += 1
        seq = self._peek_seq
        self._peek = None
        self._peek_path = path
        self._peek_busy = True
        self._peek_info = ""
        self.rawPeekChanged.emit()
        threading.Thread(target=self._raw_peek_load_worker, args=(seq, path),
                         daemon=True).start()

    def _raw_peek_load_worker(self, seq: int, path: str) -> None:
        try:
            import raw_peek
            st = raw_peek.RawPeek(path)
            # 오픈당 1회짜리 것들을 여기서 미리 만든다(패턴 21ms / 히스토그램 795ms).
            payload = (st, raw_peek.summary(st), raw_peek.pattern_chart(st),
                       raw_peek.histogram(st), raw_peek.minimap(st),
                       raw_peek.default_center(st))
            self._rawPeekSig.emit((seq, "loaded", payload))
        except Exception as e:
            self._rawPeekSig.emit((seq, "error", f"{type(e).__name__}: {e}"))

    # ------------------------------------------------- Develop 애니메이션
    # 단계 스케줄은 `develop_anim.py` 가 단일 진실원이다. 여기서는 스냅샷을 보관하고
    # 시간 t 의 uniform 값을 돌려주기만 한다(렌더는 기존 `adjust.frag` 가 한다).

    @Slot("QVariantMap")
    def developBegin(self, snap) -> None:  # noqa: N802 (QML 슬롯)
        """애니메이션 시작 — QML 이 읽은 **최종 uniform 값**을 받아 스케줄을 만든다.

        ⚠️vector4d 는 QML 이 `[x, y, z, w]` 배열로 보낸다(QVector4D 로 오면 보간을 못 한다)."""
        import develop_anim
        out = {}
        for k, v in dict(snap).items():
            try:
                out[str(k)] = [float(x) for x in v] if isinstance(v, (list, tuple)) \
                    else float(v)
            except (TypeError, ValueError):
                continue
        self._dev_snap = out
        try:
            self._dev_marks = develop_anim.marks(out)
        except Exception as e:
            print(f"[develop] 스케줄 실패: {type(e).__name__}: {e}")
            self._dev_marks = []
        self.developChanged.emit()

    @Slot()
    def developEnd(self) -> None:  # noqa: N802 (QML 슬롯)
        self._dev_snap = {}
        self._dev_marks = []
        self.developChanged.emit()

    @Slot(float, result="QVariantMap")
    def developValues(self, t: float):  # noqa: N802 (QML 슬롯)
        """시간 t(0..1) 의 uniform 값 + 표시 정보. 스냅샷이 없으면 빈 dict."""
        if not self._dev_snap:
            return {}
        import develop_anim
        try:
            return develop_anim.values(float(t), self._dev_snap)
        except Exception as e:
            print(f"[develop] values 실패: {type(e).__name__}: {e}")
            return {}

    def _get_develop_marks(self) -> list:
        return list(self._dev_marks)

    def _get_develop_ready(self) -> bool:
        return bool(self._dev_snap)

    def _get_develop_mosaic_url(self) -> str:
        return self._dev_mosaic_url

    def _get_develop_gray_url(self) -> str:
        return self._dev_gray_url

    developMarks = Property("QVariantList", _get_develop_marks, notify=developChanged)
    developReady = Property(bool, _get_develop_ready, notify=developChanged)
    developMosaicUrl = Property(str, _get_develop_mosaic_url, notify=developChanged)
    developGrayUrl = Property(str, _get_develop_gray_url, notify=developChanged)

    # ⚠️`@Slot` 서명이 QML 이 넘길 수 있는 인자를 정한다 — 파이썬 쪽에만 더하면 조용히 버려진다
    #   (`rawPeekView` 에서 실제로 그렇게 눌러도 아무 일이 없었다).
    @Slot(int, int, bool)
    def developMosaic(self, w: int, h: int, gain: bool = False) -> None:  # noqa: N802
        """애니메이션 머리 프레임 **두 장**(Gray / CFA 모자이크)을 만들어 provider 에 올린다.

        RAW Peek 이 열려 있어야 한다(`_peek` 재사용) — Develop 은 그 탭이므로 항상 열려 있다.
        ⚠️셰이더 렌더와 **교차 페이드**하므로 라벨을 굽지 않는다(크기·프레이밍이 어긋나면 안 된다).
        ⚠️두 장 모두 **선형**이다(감마 없음) — 셰이더가 `filmicMix=0` 으로 시작하므로 감마를
          걸면 밝기가 튄다. `raw_peek.develop_mosaic` 주석 참조.
        """
        st = self._peek
        if st is None or self._peek_provider is None:
            return
        import raw_peek
        # ★같은 크기 재요청은 건너뛴다 — 창 리사이즈·정보 패널 토글이 같은 크기로 되돌아오는
        #   경우가 흔하고, 한 번이 전체 프레임 패스라 싸지 않다.
        # 게인 플래그도 키에 넣는다 — 안 넣으면 토글해도 캐시된 그림이 그대로 온다.
        if getattr(self, "_dev_mosaic_size", None) == (w, h, bool(gain)):
            return
        try:
            gray, cfa = raw_peek.develop_pair(st, w, h, gain=gain)
        except Exception as e:
            print(f"[develop] 모자이크 실패: {type(e).__name__}: {e}")
            return
        self._dev_mosaic_size = (w, h, bool(gain))
        self._peek_provider.set_image("develop", cfa)
        self._peek_provider.set_image("developgray", gray)
        self._peek_counter += 1
        self._dev_mosaic_url = f"image://rawpeek/develop?v={self._peek_counter}"
        self._dev_gray_url = f"image://rawpeek/developgray?v={self._peek_counter}"
        # ★재진입 방지 — 동기 렌더 경로가 QML 핸들러 안에서 여기까지 들어오므로 즉시 emit 하면
        #   바인딩이 재평가되지 않는다(RAW Peek 에서 실제로 났던 버그).
        QTimer.singleShot(0, self.developChanged.emit)

    @Slot()
    def rawPeekClose(self) -> None:  # noqa: N802 (QML 슬롯)
        """오버레이를 닫을 때 배열을 놓아준다(26MP 기준 raw+colors ≈ 80MB)."""
        self._dev_mosaic_size = None      # 다음 오픈은 새로 그린다(사진이 바뀔 수 있다)
        self._peek_seq += 1                   # 진행 중 워커 결과 무효화
        self._peek = None
        self._peek_path = ""
        self._peek_info = ""
        self._peek_caption = ""
        self._peek_status = ""
        self._peek_prog = 0.0
        self._peek_center = (0.5, 0.5)
        self._dev_snap = {}
        self._dev_marks = []
        self._peek_busy = False
        self._peek_job = None
        self._peek_pub = self._peek_req      # 남아 있던 워커 결과를 전부 옛것으로 만든다
        self._peek_last_mode = -1
        # URL 도 되돌린다 — 프로바이더를 비웠으므로 옛 URL 이 남으면 QML 이 재요청하지 않아
        # 다음 오픈에서 빈 텍스처를 그대로 들고 있게 된다.
        self._peek_counter += 1
        self._peek_url = f"image://rawpeek/main?v={self._peek_counter}"
        if self._peek_provider is not None:
            self._peek_provider.clear()
        self.rawPeekChanged.emit()

    # ⚠️`@Slot` 의 서명이 QML 이 넘길 수 있는 인자를 정한다 — 파이썬 쪽에만 인자를 더하면
    #   QML 의 7번째 인자가 **조용히 버려진다**(체크박스를 눌러도 그림이 안 바뀌었다).
    @Slot(int, float, float, int, int, int, bool)
    def rawPeekView(self, mode: int, cx: float, cy: float, zoom: int,  # noqa: N802
                    w: int, h: int, gain: bool = True) -> None:
        """현재 모드/팬/줌으로 main 그림을 갱신한다.

        ★판정은 `raw_peek.is_heavy` 한 곳이다. 가벼운 것(작은 크롭)은 **동기** — 드래그가
          즉시 따라온다. 무거운 것(전체보기 250~385ms, 디모자이크 재디코드 1.3~3.7s, 그리고
          **크롭이 큰 저배율 줌**)은 워커로 보내고 **코얼레싱**한다(`_stamp_worker` 와 같은
          패턴). 드래그마다 스레드를 띄우면 요청이 쌓여 오히려 늦는다.
          ⚠️예전엔 `zoom > 1` 이면 무조건 동기였는데, 2배 줌은 크롭이 커서 CFA 25ms 라
          드래그가 끊겼다(사용자 보고). 크기 기준은 `raw_peek._SYNC_CROP_PX` 주석 참조.
        """
        st = self._peek
        if st is None:
            return
        import raw_peek
        self._peek_req += 1
        rid = self._peek_req
        heavy = raw_peek.is_heavy(st, mode, zoom, w, h, cx, cy, gain)
        dm_cached = None                      # 디모자이크: 디코드 창이 이미 있나(아래 두 곳이 본다)
        if mode == raw_peek.MODE_DEMOSAIC:
            dm_cached = raw_peek.demosaic_cached(
                st, raw_peek.demosaic_crop(st, cx, cy, zoom, w, h))
        # ★**탭 전환은 줄을 서지 않는다.** 디모자이크 재디코드(1.3~3.7s)가 도는 중에 다른 탭으로
        #   가면 워커 큐 뒤에 붙어 **디코드가 끝날 때까지 기다려야 했다**(사용자 보고). 모드가
        #   바뀌는 요청은 **비용에 상한이 있을 때만** 동기로 돌린다(크롭 렌더 20~30ms).
        #   ⚠️`zoom <= 1`(전체보기 0.25~0.38s)은 제외 — 전환 한 번에 GUI 가 그만큼 멈춘다.
        #   ⚠️디모자이크는 **디코드 창이 이미 캐시된 경우만** — 벗어났으면 수 초짜리다.
        if heavy and zoom > 1 and mode != self._peek_last_mode:
            if mode != raw_peek.MODE_DEMOSAIC or dm_cached:
                heavy = False
        self._peek_last_mode = mode
        if not heavy:
            # ⚠️**대기 중인 옛 요청을 버린다.** 동기로 그린 이 화면이 그보다 새것이다. 안 버리면
            #   워커가 나중에 그걸 집어 **아무도 안 보는 탭을 위해 수 초짜리 디코드**를 한 번 더
            #   돌리고, 그 동안 'decoding…' 배지가 이미 끝난 화면 위에 남는다.
            with self._peek_lock:
                self._peek_job = None
            try:
                img, cap = raw_peek.render(st, mode, cx, cy, zoom, w, h, gain=gain)
            except Exception as e:
                print(f"[rawpeek] render 실패: {type(e).__name__}: {e}")
                return
            self._raw_peek_publish(img, cap, rid)
            return

        # ⚠️'rendering…' 배지는 **오래 걸리는 것에만** 띄운다(전체보기 0.25~0.38s / 디모자이크
        #   재디코드 수 초). 줌 크롭은 워커로 보내도 20~30ms 라, 여기서 busy 를 켜면 드래그
        #   내내 배지가 깜박이기만 한다.
        #   ⚠️디모자이크도 **디코드가 필요한 경우만** — 창이 이미 있으면 8~27ms 짜리 패널
        #     재조립이라, 드래그 내내 배지가 켜져 있게 된다.
        slow = zoom <= 1 or (mode == raw_peek.MODE_DEMOSAIC and not dm_cached)
        with self._peek_lock:
            self._peek_job = (mode, cx, cy, zoom, w, h, gain, rid)
            queued = self._peek_running       # 이미 도는 워커가 이 요청을 이어받는다
            if not queued:
                self._peek_running = True
        # ★⚠️**배지는 큐에 넣을 때 켠다 — 워커를 띄우는 요청에서만 켜면 안 된다.**
        #   드래그 중에는 앞 요청의 워커가 거의 항상 돌고 있어서, 창을 벗어나 **재디코드가
        #   필요해진 그 요청**은 위에서 큐에 담기기만 하고 배지를 못 켠다 → "디모자이크
        #   드래그에서 프로그레스가 안 뜰 때가 있다"(사용자 보고). 판정은 요청 자체로 한다.
        if slow and not self._peek_busy:
            self._peek_busy = True
        self.rawPeekChanged.emit()
        if queued:
            return                            # 진행 중 — 최신 요청만 남기고 이어 받는다
        threading.Thread(target=self._raw_peek_render_worker,
                         args=(self._peek_seq,), daemon=True).start()

    def _raw_peek_render_worker(self, seq: int) -> None:
        import raw_peek
        try:
            while True:
                with self._peek_lock:
                    job, self._peek_job = self._peek_job, None
                if job is None or seq != self._peek_seq:
                    break
                st = self._peek
                if st is None:
                    break
                mode, cx, cy, zoom, w, h, gain, rid = job

                def _prog(done, total, name, _seq=seq):
                    # 후보 디코드는 종당 ~1.1s(LINEAR)/~4s(Markesteijn 3-pass) — 침묵하면 멈춘
                    # 것처럼 보인다. 텍스트 + 진행분율(done/total)을 함께 보낸다(QML 진행 바).
                    txt = "" if done >= total else f"decoding {name} ({done + 1}/{total})…"
                    self._rawPeekSig.emit((_seq, "status", (txt, done / max(total, 1))))

                out = raw_peek.render(st, mode, cx, cy, zoom, w, h,
                                      progress=_prog, gain=gain)
                self._rawPeekSig.emit((seq, "view", (out, rid)))
        except Exception as e:
            self._rawPeekSig.emit((seq, "error", f"{type(e).__name__}: {e}"))
        finally:
            # ⚠️'실행 중' 을 내리는 것과 '남은 요청' 을 보는 것은 **한 임계구역**이어야 한다.
            #   따로 하면 그 사이에 들어온 요청이 — 호출측은 아직 `_peek_running` 이 True 라
            #   워커를 안 띄우고 이 워커는 이미 큐를 비웠으므로 — 아무도 안 받는다(드래그의
            #   마지막 위치가 안 그려지고 다음 조작 때까지 옛 프레임이 남는 증상).
            with self._peek_lock:
                again = self._peek_job is not None
                self._peek_running = again
                nseq = self._peek_seq
            if again:                       # 이어서 굽는다 — busy 는 그대로 두고 idle 은 안 쏜다
                threading.Thread(target=self._raw_peek_render_worker,
                                 args=(nseq,), daemon=True).start()
            else:
                self._rawPeekSig.emit((seq, "idle", None))

    def _raw_peek_publish(self, img, caption=None, rid: int = 0) -> None:
        """⚠️**늦게 온 결과가 새 그림을 덮지 않게** 요청 번호로 거른다 — 탭 전환이 큐를
        건너뛰므로(rawPeekView 주석) 디모자이크 디코드가 끝나면서 이미 바뀐 화면을 옛 패널로
        덮는 일이 생긴다."""
        if self._peek_provider is None:
            return
        if rid and rid < self._peek_pub:
            return                                   # 이미 더 새 그림이 올라가 있다
        self._peek_pub = max(self._peek_pub, rid)
        if caption is not None:
            self._peek_caption = "\n".join(caption)
        self._peek_provider.set_image("main", img)
        self._peek_counter += 1
        self._peek_url = f"image://rawpeek/main?v={self._peek_counter}"
        # ★알림을 **다음 이벤트 루프 턴으로 미룬다.** 동기 렌더 경로는 QML 의
        #   `onModeChanged`/`onRawPeekChanged` 핸들러 **안에서** 여기까지 들어오는데, 그 상태로
        #   같은 시그널을 다시 쏘면 재진입이라 QML 이 바인딩을 재평가하지 않는다 — 실측으로
        #   `Image.source` 가 `?v=112` 에 멈춘 채 controller 쪽만 113·114·115 로 올라갔다
        #   ("버튼은 반응하는데 화면이 안 바뀐다"의 원인). 한 턴 미루면 사람이 느낄 지연은 없다.
        QTimer.singleShot(0, self.rawPeekChanged.emit)

    def _on_raw_peek(self, msg) -> None:
        seq, kind, payload = msg
        if seq != self._peek_seq:
            return                            # 다른 사진/닫힌 뒤의 결과 — 버린다
        if kind == "loaded":
            st, info, pattern_img, hist_img, mini_img, center = payload
            self._peek_center = center
            # 새 사진의 상태다 — develop 그림 캐시 키를 버린다(크기가 같아도 내용이 다르다).
            self._dev_mosaic_size = None
            self._peek = st
            self._peek_info = info["text"]
            self._peek_busy = False
            if self._peek_provider is not None:
                self._peek_provider.set_image("pattern", pattern_img)
                self._peek_provider.set_image("hist", hist_img)
                self._peek_provider.set_image("mini", mini_img)
                self._peek_counter += 1
                self._peek_pattern_url = f"image://rawpeek/pattern?v={self._peek_counter}"
                self._peek_hist_url = f"image://rawpeek/hist?v={self._peek_counter}"
                self._peek_mini_url = f"image://rawpeek/mini?v={self._peek_counter}"
            self.rawPeekChanged.emit()
        elif kind == "view":
            (img, cap), rid = payload
            self._raw_peek_publish(img, cap, rid)
        elif kind == "status":
            self._peek_status, self._peek_prog = payload
            self.rawPeekChanged.emit()
        elif kind == "idle":
            self._peek_busy = False
            self._peek_status = ""
            self._peek_prog = 0.0
            self.rawPeekChanged.emit()
        elif kind == "error":
            self._peek_busy = False
            self._peek_status = ""
            self._peek_prog = 0.0
            self._peek_info = f"RAW Peek failed\n{payload}"
            self.rawPeekChanged.emit()

    @Slot(QUrl)
    def load(self, file_url: QUrl) -> None:  # noqa: N802 (QML 슬롯, FileDialog QUrl)
        path = file_url.toLocalFile() if file_url.isLocalFile() else file_url.toString()
        self._load(path)

    @Slot(str)
    def loadPath(self, path: str) -> None:  # noqa: N802 (QML 슬롯, explorer 로컬 경로)
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        # 이미지 전환 직전: QML 이 *이전* 파일(self._path 아직 이전값)로 편집을 플러시 저장.
        if self._path and self._path != path:
            self.flushEdits.emit()
        self._path = path
        self._set_load_error("")   # 새 로드 시작 → 이전 파일의 에러 안내 제거
        self._fresh_load = True   # 디코딩 완료(_on_render_ready) 시 editsReady 1회 발화 → 복원
        # 이 파일의 사이드카 편집을 1회 읽어 둠(QML editsForCurrent 가 반환).
        self._pending_edits = self._read_edits(path)
        # 저장된 WB(temp/tint)가 있으면 절대값으로 선설정 → 초기 렌더가 저장 WB 로 디코딩
        # (없으면 as-shot 으로 시작). setWb 재디코딩 이중작업 회피.
        e = self._pending_edits
        # ⚠️손상/수동편집 사이드카(temp="auto" 등)의 타입 오류로 로드가 통째로
        # 실패하지 않도록 방어 — 파싱 실패 시 as-shot 으로 폴백.
        try:
            self._kelvin = float(e["temp"]) if e.get("temp") is not None else None
            self._tint = float(e.get("tint", 0.0)) if self._kelvin is not None else 0.0
        except (TypeError, ValueError):
            self._kelvin = None
            self._tint = 0.0
        # 저장된 렌즈보정 상태도 첫 디코드 전에 선설정 → 이전 이미지 상태가 새기고 즉시
        # 재디코딩되는 이중작업/기하 흔들림 방지(WB 프리시드와 동일 취지, 기본값 True).
        lc = e.get("lensCorrection")
        self._lens = bool(lc) if lc is not None else True
        ae = e.get("autoExposure")           # 렌즈 보정과 같은 이유로 첫 디코드 전에 선설정
        self._auto_exp = bool(ae) if ae is not None else True
        # 저장된 aiNr 이미지면 ORT 세션을 아래 _render() 디코드와 병렬로 미리 워밍 →
        # 로드 완료 직후 세션 초기화(GPU 점유) freeze 를 로드 대기 안으로 흡수(모델 있을 때만).
        if e.get("aiNr"):
            try:
                import ai_denoise
                ai_denoise.prewarm()
            except Exception:
                pass
        # 촬영정보는 경로에만 의존 -> 로드 시 1회 읽음(WB 변경 재디코딩과 무관)
        # EXIF 는 부가정보 — 손상/변칙 EXIF(예: ExposureTime 0/1)로 예외가 나도
        # 사진 로드 자체를 막지 않는다(과거: 예외가 슬롯을 탈출해 파일이 안 열렸음).
        try:
            self._exif_fields, self._exif_summary = read_shooting_info(path)
        except Exception as exc:
            print(f"[exif] 촬영정보 읽기 실패(무시): {exc}")
            self._exif_fields, self._exif_summary = [], ""
        # 촬영 방향(EXIF Orientation) → 데이트백을 센서 우하단 각인처럼 회전/코너 배치(세로 사진).
        try:
            self._stamp_rot = date_stamp.rot_from_orientation(read_orientation(path))
        except Exception:
            self._stamp_rot = 0
        self._stamp_text = date_stamp.stamp_text_from_date(self._exif_field("Date"))
        # 지오태그 — **사이드카 우선, 없으면 파일에 적힌 EXIF GPS**.
        # ⚠️사이드카에 `gpsLat` 키가 **있으면**(값이 null 이어도) 그것이 답이다 — 사용자가
        #   일부러 지운 위치가 다음 로드에서 EXIF 로 되살아나면 지우기가 안 먹는 것이 된다.
        # ★규칙 자체는 `_gps_for_file` 하나에만 있다(Photo map 워커와 공유 — 그 함수 주석).
        self._gps = _gps_for_file(path, e)
        if "gpsLat" in e:
            self._gps_src = str(e.get("gpsSrc") or "") if self._gps else ""
        else:
            self._gps_src = "exif" if self._gps else ""
        # ⚠️`_refresh_gps_field` 는 exifChanged 만 쏜다 — **여기서 gpsChanged 를 쏘면 안 된다.**
        #   아래 stampChanged 주석과 같은 함정이다: 아직 디코드 전이라 QML `_ui_path` 는 이전
        #   사진인데 gps 는 `editSaveWatch` 에 들어 있어, 알리면 **빠져나온 사진에 새 사진의
        #   좌표가 저장된다.** 알림은 `_on_render_ready` 의 `_ui_path` 확정 뒤로 미룬다.
        self._refresh_gps_field()      # 촬영정보 목록의 GPS 행(=`I` 오버레이) 갱신
        # ⚠️여기서 stampChanged 를 쏘지 말 것 — 아직 **디코드 전**이라 QML 의 `_ui_path` 는
        #   여전히 **이전 사진**이고 `_applying` 도 꺼져 있다. 알리면 editSaveWatch 가 흔들려
        #   자동저장이 예약되고, 500ms 뒤 **빠져나온 사진에 사이드카가 써진다**(편집을 하나도
        #   안 했는데 edited). 새 파일의 날짜/회전 알림은 `_on_render_ready` 로 미룬다.
        self.exifChanged.emit()
        # 좌측 file explorer 를 이 파일의 폴더로 동기화(다른 폴더 파일을 열어도 따라옴).
        parent = str(Path(path).parent)
        if parent != self._folder:
            self._scan_folder(parent)
        self._render()
        # 복원(편집 반영)은 디코딩 완료 후 _on_render_ready 에서 editsReady 로 트리거한다
        # (로드 진행 중 이전 이미지에 새 파일 편집이 잘못 반영되는 것 방지).

    # ---------- 좌측 File Explorer (폴더/파일 모델) ----------
    def _scan_folder(self, folder: str, force: bool = True) -> None:
        """폴더 스캔을 백그라운드 스레드에서 수행 → 결과만 메인(_on_folder_scanned)에 적용.
        디렉터리 나열/타입 확인·사이드카 읽기가 자는 외장 HDD 스핀업 대기로 GUI 를 멈추지
        않게(과거: iterdir+stat 를 메인 스레드에서 → 스핀업 동안 freeze). seq 로 오래된 스캔 폐기.

        force=True: 탐색기 탐색(폴더 이동) — 항상 갱신.
        force=False: 자동 감시 재스캔 — 목록이 그대로면 UI 갱신 생략(.json 저장 등으로 안 깜빡임).
        """
        self._scan_seq += 1
        threading.Thread(target=self._scan_worker,
                         args=(self._scan_seq, str(folder), force), daemon=True).start()

    def _scan_worker(self, seq: int, folder: str, force: bool) -> None:
        # ⚠️파일 I/O 는 여기(워커)서만 — 자는 외장 드라이브 스핀업 대기가 메인 스레드를 막지 않게.
        # os.scandir: 디렉터리 1회 나열로 dir/file 타입을 캐시(항목당 stat 회피, Windows).
        dirs, raws = [], []
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        if e.is_dir():
                            if not e.name.startswith("."):   # .filmrawsteryedits 등 숨김
                                dirs.append(e.name)
                        elif e.is_file() and (os.path.splitext(e.name)[1].lower()
                                              in _openable_exts()):
                            raws.append(e.name)
                    except OSError:
                        pass
        except Exception:
            pass
        dirs.sort(key=str.lower)
        raws.sort(key=str.lower)
        items = [{"name": n, "path": os.path.join(folder, n), "isDir": True} for n in dirs]
        items += _pair_flags(folder, raws)
        likes = self._load_likes(folder)          # 사이드카 읽기(off-thread)
        edited = self._load_edited_names(folder)   # 편집 배지용(off-thread)
        self._folderScanSig.emit((seq, folder, items, likes, edited, force))

    @Slot(object)
    def _on_folder_scanned(self, payload) -> None:
        seq, folder, items, likes, edited, force = payload
        if seq != self._scan_seq:
            return                               # 더 최신 스캔 진행 중 → 폐기
        if not force and folder == self._folder and items == self._files:
            return                               # 변화 없음(우리 .json 저장 등) → UI 갱신 생략
        # Photo map: **폴더가 바뀌면** 캐시를 버린다(다음에 열 때 새로 훑는다). 같은 폴더의
        #   감시 재스캔(파일 추가/사이드카 저장)에서는 버리지 않는다 — 좌표는 우리가 이미
        #   `_write_gps_sidecar`/`_set_gps` 에서 따라가고 있고, 여기서 버리면 지도가 열린 채
        #   자동저장 한 번에 통째로 비었다가 다시 차서 깜빡인다.
        # ★비교 대상에 **훑는 중인 폴더**도 넣는다 — 안 그러면 같은 폴더의 감시 재스캔
        #   (파일 추가·사이드카 저장)이 '폴더가 바뀌었다'로 읽혀 **진행 중인 스캔을 죽이고**
        #   지도가 "위치 없음"으로 굳는다(닫았다 다시 열기 전까지).
        if folder != (self._map_folder or self._map_scanning):
            self._map_raw = {}
            self._map_groups = []
            self._map_folder = ""
            self._map_scanning = ""
            self._map_paths = set()
            self._map_pending = {}
            self._map_total = 0
            self._map_seq += 1          # 진행 중이던 이전 폴더 스캔 결과를 폐기
            self._map_busy = False
            self.folderMapChanged.emit()
        self._folder = folder
        self._files = items
        self._update_watcher(folder)             # QFileSystemWatcher — 메인 스레드에서만
        self._likes = likes                      # 폴더 진입 시 좋아요 → 썸네일 하트
        self._likes_folder = folder
        self._like_rev += 1
        self._edited = edited                    # 편집 사이드카 유무 → 썸네일 배지
        self._edited_folder = folder
        self._edit_rev += 1
        self.folderChanged.emit()
        self.likesChanged.emit()
        self.editsChanged.emit()
        pref_set("explorer", "lastFolder", folder)   # 재시작 복원용

    @Slot(QUrl)
    def setFolder(self, url: QUrl) -> None:  # noqa: N802 (QML 슬롯, FolderDialog)
        folder = url.toLocalFile() if url.isLocalFile() else url.toString()
        if folder:
            self._scan_folder(folder)

    @Slot(str)
    def setFolderPath(self, folder: str) -> None:  # noqa: N802 (QML 슬롯, 폴더 더블클릭)
        if folder:
            self._scan_folder(folder)

    @Slot()
    def goUp(self) -> None:  # noqa: N802 (QML 슬롯, 상위 폴더)
        if self._folder:
            parent = Path(self._folder).parent
            if str(parent) != self._folder:   # 루트면 변화 없음
                self._scan_folder(str(parent))

    def _get_folder(self) -> str:
        return self._folder

    def _get_files(self) -> list:
        return self._files

    def _get_like_rev(self) -> int:
        return self._like_rev

    def _get_edit_rev(self) -> int:
        return self._edit_rev

    def _get_folder_url(self) -> str:
        """현재 폴더의 QUrl 문자열 — FolderDialog.currentFolder 시작 위치용."""
        return QUrl.fromLocalFile(self._folder).toString() if self._folder else ""

    currentFolder = Property(str, _get_folder, notify=folderChanged)
    currentFolderUrl = Property(str, _get_folder_url, notify=folderChanged)
    fileList = Property("QVariantList", _get_files, notify=folderChanged)
    likeRevision = Property(int, _get_like_rev, notify=likesChanged)
    editsRevision = Property(int, _get_edit_rev, notify=editsChanged)

    @Slot(float, float)
    def setWb(self, kelvin: float, tint: float) -> None:  # noqa: N802 (QML 슬롯)
        """절대 색온도(Kelvin) + Tint 저장(export 용). WB 는 셰이더가 실시간 적용 →
        재디코딩 없음. 프리뷰는 QML wbGain 바인딩이 매 프레임 갱신."""
        self._kelvin = kelvin
        self._tint = tint

    @Slot(str)
    def setStampText(self, text: str) -> None:  # noqa: N802 (QML 슬롯)
        """사용자가 입력한 날짜 스탬프 텍스트 반영(재디코딩 없이 레이어만 재렌더)."""
        text = text or ""
        if text == self._stamp_text:      # 형제 슬롯들과 같은 동일값 가드 — 아래 주석 참조
            return
        self._stamp_text = text
        self.stampChanged.emit()
        self._update_stamp_layer()

    @Slot(str)
    def setStampFont(self, style: str) -> None:  # noqa: N802 (QML 슬롯)
        """데이트백 폰트 방식(classic/modern/14seg) 변경 — 레이어만 재렌더."""
        style = str(style or "7c_bold")
        if style == self._stamp_font:
            return
        self._stamp_font = style
        self.stampChanged.emit()
        self._update_stamp_layer()

    @Slot(float)
    def setStampSize(self, size_frac: float) -> None:  # noqa: N802 (QML 슬롯)
        """데이트백 크기(숫자높이/짧은변 비율) 변경 — 레이어만 재렌더."""
        try:
            size_frac = float(size_frac)
        except (TypeError, ValueError):
            return
        if size_frac == self._stamp_size:
            return
        self._stamp_size = size_frac
        self.stampChanged.emit()
        self._update_stamp_layer()

    @Slot(float)
    def setStampMargin(self, v: float) -> None:  # noqa: N802 (QML 슬롯)
        """데이트백 코너 여백 비율 변경 — 위치만 바뀌므로 재렌더 없이 알림만(프리뷰 QML 이 재배치)."""
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if v == self._stamp_margin:
            return
        self._stamp_margin = v
        self.stampChanged.emit()

    @Slot(str)
    def setStampColor(self, color: str) -> None:  # noqa: N802 (QML 슬롯)
        """각인 색. ⚠️형제 슬롯들과 같은 동일값 가드 — _update_stamp_layer 가 GUI 스레드에서
        스프라이트를 동기 재렌더하므로, 로드 1회에 여러 번 같은 값이 들어오는 것을 접는다.
        ⚠️표기를 **정규화**한다(`#RRGGBB`/`#rrggbb`/이름 → 소문자 `#rrggbb`) — 그러지 않으면
        같은 색이 다른 문자열로 editParams 에 실려 **레시피 룩 지문이 갈리고** 배지가 켜지지
        않는다. 잘못된 색은 기본 앰버로 되돌린다(이전 사진 색이 남는 것보다 예측 가능하다)."""
        import date_stamp
        qc = QColor(str(color or ""))
        if not qc.isValid():
            qc = QColor(date_stamp.DEFAULT_COLOR)
        color = qc.name()          # 항상 소문자 #rrggbb
        if color == self._stamp_color:
            return
        self._stamp_color = color
        self.stampChanged.emit()
        self._update_stamp_layer()

    @Slot(float)
    def setStampGlow(self, v: float) -> None:  # noqa: N802 (QML 슬롯)
        import date_stamp
        v = min(date_stamp.GLOW_MAX, max(date_stamp.GLOW_MIN, float(v)))
        if v == self._stamp_glow:
            return
        self._stamp_glow = v
        self.stampChanged.emit()
        self._update_stamp_layer()

    @Slot(float)
    def setStampSpread(self, v: float) -> None:  # noqa: N802 (QML 슬롯)
        import date_stamp
        v = min(date_stamp.SPREAD_MAX, max(date_stamp.SPREAD_MIN, float(v)))
        if v == self._stamp_spread:
            return
        self._stamp_spread = v
        self.stampChanged.emit()
        self._update_stamp_layer()

    @Slot(float)
    def setStampGrainSrc(self, grain_amt: float) -> None:  # noqa: N802 (QML 슬롯)
        """전체 필름 그레인(grainAmt)을 스탬프 프리뷰에 반영 — 스탬프 그레인은 사진 그레인에 연동."""
        try:
            grain_amt = float(grain_amt)
        except (TypeError, ValueError):
            return
        if grain_amt == self._stamp_grain_src:
            return
        self._stamp_grain_src = grain_amt
        self._update_stamp_layer()

    def _update_stamp_layer(self) -> None:
        """현재 _stamp_text 로 타이트 스프라이트 + 크기 비율을 갱신. 프록시 크기와 무관(비율 기반).
        QML 이 cropClip(=최종 프레임) 위에 source-over 오버레이로 표시 → 위치/크기 최종 사이즈 기준.

        ⚠️**워커 스레드에서 굽는다.** `date_stamp.sprite_layer` 는 실측 2.5 / 20.2 / 56.5ms
        (size_frac 0.012 / 0.032 / 0.050)로 크기 제곱에 비례하고, 비용의 대부분은 넓은 헤일로
        블러다. GUI 스레드에서 돌리면 Size/Glow/Spread 를 끄는 동안 **입력이 그만큼 멈춘다**
        (최대 3.4배 프레임 예산 초과 = 18fps). 픽셀은 워커에서도 비트 동일함을 확인했다
        (동시 3워커까지 예외 없음, 최대차 0코드). 축소 렌더(드래프트)는 폰트가 정수 픽셀로
        래스터돼 놓는 순간 4~11px 튀어서 기각했고, `_wide_blur` 근사를 기본 spread 로 넓히는
        것은 **예전에 저장한 스탬프의 모습을 바꾸므로** 기각(그 함수 주석 참조).

        결과는 `_stampSpriteSig` 로 GUI 스레드에 돌아온다. 소비자는 전부 `stampChanged` 를
        보는 QML 프로퍼티(stampUrl/stampWRatio/stampHRatio)라 비동기여도 안전하다 —
        **동기 완료를 기대하고 `_stamp_wr` 을 바로 읽는 호출부는 없다**(추가하지 말 것)."""
        if self._stamp_provider is None:
            return
        if not self._stamp_text:
            # 빈 텍스트 = 스탬프 끔. 1x1 투명이라 워커를 태울 이유가 없다(즉시 반영).
            self._stamp_seq += 1          # 진행 중인 워커 결과를 무효화
            self._stamp_job = None
            layer = QImage(1, 1, QImage.Format.Format_ARGB32)
            layer.fill(0)                 # 투명 1x1 — sampler/Image 항상 유효하게 유지
            self._stamp_wr = self._stamp_hr = self._stamp_bleed = 0.0
            self._publish_stamp_layer(layer)
            return
        # 워커에 넘길 스냅샷 — self 를 워커에서 읽지 않는다(끄는 중에 값이 바뀐다).
        job = (self._stamp_text, self._stamp_rot, self._stamp_font, self._stamp_size,
               self._stamp_grain_src, self._stamp_color, self._stamp_glow, self._stamp_spread,
               self._cm_enabled, self._cm_dst)
        if self._stamp_busy:
            self._stamp_job = job         # 코얼레싱 — 중간 값은 버리고 **마지막 것만** 굽는다
            return
        self._start_stamp_worker(job)

    def _start_stamp_worker(self, job) -> None:
        self._stamp_seq += 1
        self._stamp_busy = True
        threading.Thread(target=self._stamp_worker, args=(self._stamp_seq, job),
                         daemon=True).start()

    def _stamp_worker(self, seq: int, job) -> None:
        (text, rot, style, size, grain, color, glow, spread, cm_on, cm_dst) = job
        try:
            layer, wr, hr = date_stamp.sprite_layer(
                text, rot=rot, style=style, size_frac=size,
                grain_amt=grain, color=color, glow=glow, spread=spread)
            # 프리뷰 스탬프도 사진과 동일한 디스플레이 색관리(광색역 보정)를 거치게 한다 —
            # 안 하면 사진만 보정되고 스탬프는 raw sRGB 라 프리뷰에서 스탬프 색감이 어긋난다.
            # export 는 표준 sRGB 라 stamp_export 는 미적용(원본 sRGB 유지).
            if cm_on and cm_dst is not None:
                import display_cm
                display_cm.apply_display_cm(layer, cm_dst)
            # ⚠️bleed 를 **이 렌더의 인자로** 함께 낸다(위 _get_stamp_bleed 주석의 진동 방지).
            self._stampSpriteSig.emit((seq, layer, wr, hr, date_stamp.bleed_frac(size, spread)))
        except Exception as exc:
            print(f"[stamp] 스프라이트 렌더 실패: {exc}")
            self._stampSpriteSig.emit((seq, None, 0.0, 0.0, 0.0))

    def _on_stamp_sprite(self, payload) -> None:
        """워커 결과를 GUI 스레드에서 반영. 대기 중인 최신 요청이 있으면 이어서 굽는다."""
        seq, layer, wr, hr, bleed = payload
        self._stamp_busy = False
        if seq == self._stamp_seq and layer is not None:
            self._stamp_wr, self._stamp_hr, self._stamp_bleed = wr, hr, bleed
            self._publish_stamp_layer(layer)
        job, self._stamp_job = self._stamp_job, None
        if job is not None:
            self._start_stamp_worker(job)

    def _publish_stamp_layer(self, layer) -> None:
        self._stamp_provider.set_image(layer)
        self._stamp_counter += 1
        self._stamp_url = f"image://stamp/s?v={self._stamp_counter}"
        self.stampSpriteChanged.emit()      # ⚠️stampChanged 가 아니다 — 위 시그널 정의 주석 참조

    # ---------- 시맨틱 마스킹 (ONNX SegFormer, 복합 클래스) ----------
    #   추론 1회로 150클래스 softmax 를 캐시(_seg_probs)해 두고, 체크된 클래스들을 합산해
    #   라이브로 재조합한다(재추론 없음). 마스크 적용/조정/export 는 클래스 무관(단일 알파).
    def _get_mask_groups(self):
        import sky_seg
        return sky_seg.groups_for_qml()

    maskGroups = Property("QVariantList", _get_mask_groups, constant=True)

    # 얼굴 부위 그룹(Face 탭). 장면 클래스와 **같은 keys 목록**에 섞여 setMaskClasses 로 오고,
    # _mask_worker 가 접두사로 갈라 각각 마스크를 만든 뒤 합집합(np.maximum)한다.
    def _get_face_groups(self):
        import face_seg
        return face_seg.groups_for_qml()

    faceGroups = Property("QVariantList", _get_face_groups, constant=True)

    # ---------- AI 모델 관리(설치 현황 + 수동 다운로드) ----------
    # 모델이 늘어나면서 "무엇이 얼마나 받아졌는지"가 안 보이고, 다운로드가 기능을 처음 쓰는
    # 순간에만 암묵적으로 일어난다. 여기서 현황을 모아 보여주고 미리 받을 수 있게 한다.
    # ⚠️크기·파일명·설명은 각 엔진 모듈이 소유한다(MODEL_LABEL/NOTE/FILES/TOTAL_BYTES).
    #   여기에 복제하면 모델을 교체할 때 한쪽만 고쳐져 표시가 어긋난다.
    MODEL_MODULES = ("sky_seg", "face_seg", "depth", "ai_denoise", "caption")

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"

    def _get_model_catalog(self):
        """[{key,label,note,sizeText,totalBytes,installed,partial,haveText}] — 모듈 순서 유지."""
        import importlib
        import app_dirs
        out = []
        for key in self.MODEL_MODULES:
            try:
                mod = importlib.import_module(key)
                files = list(getattr(mod, "MODEL_FILES", []))
                total = int(getattr(mod, "TOTAL_BYTES", 0))
                have = [f for f in files if app_dirs.have(f)]
                out.append({
                    "key": key,
                    "label": getattr(mod, "MODEL_LABEL", key),
                    "note": getattr(mod, "MODEL_NOTE", ""),
                    "totalBytes": total,
                    # 항상 모델 전체 크기. 실제 디스크 사용량을 쓰면 일부만 받힌 상태에서
                    # "340 MB"(=이미 있는 파싱 모델)로 보여 남은 0.2MB 를 오해하게 된다.
                    "sizeText": self._fmt_bytes(total),
                    "installed": len(have) == len(files) and bool(files),
                    # 일부만 받힌 상태(중간 취소·실패) — 다시 받으면 있는 파일은 건너뛴다
                    "partial": 0 < len(have) < len(files),
                    "haveText": f"{len(have)}/{len(files)} files",
                })
            except Exception as exc:            # 모듈 하나가 깨져도 나머지는 보여준다
                print(f"[models] {key} 정보 읽기 실패: {exc}")
        return out

    modelCatalog = Property("QVariantList", _get_model_catalog, notify=modelsChanged)

    def _get_model_summary(self):
        """{installedText, missingText, missingBytes, dirPath, orphanText} — 헤더/푸터 요약."""
        import importlib
        import app_dirs
        cat = self._get_model_catalog()
        inst = sum(c["totalBytes"] for c in cat if c["installed"])
        miss = sum(c["totalBytes"] for c in cat if not c["installed"])
        # 어느 모듈도 청구하지 않는 파일(기각·대체된 모델 잔재) — 사용자가 직접 지울 수 있게 안내만.
        # ⚠️청구 목록은 **MODEL_MODULES 전체** 기준으로 모은다(카탈로그 기준 금지). 카탈로그는
        #   import 에 실패한 모듈을 조용히 빼므로, 예컨대 caption 이 깨지면 그 8파일 1.09GB 가
        #   '미사용 — 지워도 됨'으로 안내된다(멀쩡한 기능의 모델을 지우게 만드는 오안내).
        #   하나라도 못 읽으면 무엇이 청구됐는지 알 수 없으므로 아예 표시하지 않는다.
        known = {"ai_denoise_device.json"}
        all_known = True
        for key in self.MODEL_MODULES:
            try:
                known |= set(getattr(importlib.import_module(key), "MODEL_FILES", []))
            except Exception:
                all_known = False
        orphan = 0
        if all_known:
            try:
                for fn in os.listdir(app_dirs.MODELS_DIR):
                    p = os.path.join(app_dirs.MODELS_DIR, fn)
                    if fn not in known and not fn.endswith(".part") and os.path.isfile(p):
                        orphan += os.path.getsize(p)
            except OSError:
                pass
        return {"installedText": self._fmt_bytes(inst), "missingText": self._fmt_bytes(miss),
                "missingBytes": miss,          # QML 이 "0 MB" 문자열 비교를 안 하도록 수치도 제공
                "dirPath": app_dirs.MODELS_DIR,
                "orphanText": self._fmt_bytes(orphan) if (all_known and orphan) else ""}

    modelSummary = Property("QVariant", _get_model_summary, notify=modelsChanged)

    @Slot(str)
    def downloadModel(self, key) -> None:  # noqa: N802 (QML 슬롯)
        """모델 하나를 백그라운드 다운로드. 동시 1개만 — 대역폭 분산과 진행률 혼선 방지."""
        key = str(key)
        if self._model_dl_key or key not in self.MODEL_MODULES:
            return
        self._model_dl_key, self._model_dl_prog, self._model_error = key, 0.0, ""
        self.modelsChanged.emit()            # 버튼 잠금/진행 표시 시작
        self.modelProgressChanged.emit()
        threading.Thread(target=self._model_dl_worker, args=(key,), daemon=True).start()

    def _model_dl_worker(self, key: str) -> None:
        err = ""
        try:
            import importlib
            mod = importlib.import_module(key)
            last = [0.0]

            def _prog(f):
                if f - last[0] >= 0.005 or f >= 1.0:     # 0.5% 스로틀
                    last[0] = f
                    self._modelDlSig.emit((key, float(f), ""))
            mod.ensure_model(_prog)
        except Exception as exc:
            err = str(exc)
            print(f"[models] {key} 다운로드 실패: {exc}")
        self._modelDlSig.emit((key, 1.0, err or "done"))

    @Slot(object)
    def _on_model_dl(self, payload) -> None:
        key, frac, state = payload
        if key != self._model_dl_key:
            return                       # 취소/중복 워커의 뒤늦은 진행률 무시
        self._model_dl_prog = float(frac)
        if state:                        # "done" 또는 에러 메시지 — 설치 상태가 바뀐 시점
            self._model_dl_key = ""
            self._model_error = "" if state == "done" else state
            self.modelsChanged.emit()
        self.modelProgressChanged.emit()   # 진행률 틱은 목록을 건드리지 않는다

    def _get_model_dl_key(self) -> str:
        return self._model_dl_key

    def _get_model_dl_prog(self) -> float:
        return self._model_dl_prog

    def _get_model_error(self) -> str:
        return self._model_error

    modelDownloading = Property(str, _get_model_dl_key, notify=modelsChanged)   # 시작/종료 시만 변함
    modelProgress = Property(float, _get_model_dl_prog, notify=modelProgressChanged)
    modelError = Property(str, _get_model_error, notify=modelsChanged)

    @Slot()
    def refreshModels(self) -> None:  # noqa: N802 (QML 슬롯) — 대화상자 열 때 호출
        """설치 현황 재평가. 기능 첫 사용으로 받힌 모델(캡션 등)이나 사용자가 폴더에서 직접
        지운 파일이 목록에 반영되지 않는 경로가 남아 있어, 열 때마다 한 번 강제로 갱신한다."""
        self.modelsChanged.emit()

    @Slot()
    def openModelsFolder(self) -> None:  # noqa: N802 (QML 슬롯)
        """models 폴더를 파일 탐색기로 연다 — 미사용 파일 정리는 사용자가 직접(삭제 미구현)."""
        import app_dirs
        os.makedirs(app_dirs.MODELS_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(app_dirs.MODELS_DIR))

    # ---------- 앱 버전(제목표시줄 표시용) ----------
    def _get_app_version(self) -> str:
        return APP_VERSION

    appVersion = Property(str, _get_app_version, constant=True)

    @Slot(int, "QVariantList")
    def setMaskClasses(self, layer, keys) -> None:  # noqa: N802 (QML 슬롯)
        """레이어의 체크된 클래스 그룹 key 목록으로 마스크 생성(백그라운드). 추론은 이미지당 1회
        캐시라 클래스 재조합만. 같은 조합의 마스크가 이미 있으면 no-op(중복 호출 방어)."""
        layer = int(layer)
        if not (0 <= layer < 5):
            return
        keys_list = [str(k) for k in keys]
        # no-op 은 keys 뿐 아니라 획 목록도 마스크와 일치해야 한다 — undo/복원이 setStrokes 후
        # 같은 keys 로 재커밋할 때 획만 달라진 경우 재생성돼야 함(값 비교, 목록이 작아 저렴).
        if (keys_list == self._layer_keys[layer] and self._layer_masks[layer] is not None
                and self._layer_strokes[layer] == self._layer_mask_strokes[layer]):
            return
        self._layer_keys[layer] = keys_list
        self._spawn_mask_worker(layer)

    def _spawn_mask_worker(self, layer: int) -> None:
        """현재 keys+strokes 로 레이어 마스크 워커 스폰(공통 경로 — 클래스 커밋/브러시 획)."""
        if self._proxy_img is None:
            return
        self._layer_seq[layer] += 1
        self._sky_pending += 1
        self._sky_busy = True
        self.skyBusyChanged.emit()
        threading.Thread(target=self._mask_worker,
                         args=(self._img_gen, layer, self._layer_seq[layer],
                               list(self._layer_keys[layer]),
                               list(self._layer_strokes[layer])),
                         daemon=True).start()

    @staticmethod
    def _sanitize_stroke(stroke):
        """QML JS 객체 → 순수 dict(사이드카와 동형). 형 강제 + 홀수 좌표 절단."""
        pts = [float(v) for v in (stroke.get("points") or [])]
        return {"sign": 1.0 if float(stroke.get("sign", 1)) >= 0 else -1.0,
                "radius": float(stroke.get("radius", 0.05)),
                "feather": float(stroke.get("feather", 0.5)),
                "points": pts[: (len(pts) // 2) * 2]}

    def _proxy_hw(self):
        """프록시 마스크 해상도 (H,W). 프록시 없으면 None."""
        img = self._proxy_img
        if img is None or img.width() < 1 or img.height() < 1:
            return None
        return (img.height(), img.width())

    def _apply_stroke_incremental(self, layer: int, stroke) -> "object":
        """현재 canonical 마스크 위에 획 1개를 얹은 새 마스크 반환(획 수와 무관한 상수 비용).

        새 획은 항상 목록의 **맨 끝**이므로 전체 리플레이(워커)와 수학적으로 동일하다 —
        추가 = max, 빼기 = ×(1-α) 모두 마지막 적용이 결합법칙을 만족. 전부 0 이면 None."""
        import brush
        hw = self._proxy_hw()
        if hw is None:
            return None
        m = brush.apply_strokes(self._layer_masks[layer], hw, [stroke])
        return m if m.any() else None

    @Slot(int, "QVariantMap")
    def addStroke(self, layer, stroke) -> None:  # noqa: N802 (QML 슬롯)
        """브러시 획 1개 추가(릴리즈 커밋). canonical 마스크에 **증분 적용**이라 획 수와
        무관하게 즉시 반영 — 전체 리플레이 워커는 pop/clear/복원/재디코딩만 담당.
        예외: 워커 진행 중이면 증분 기반(canonical)이 유동적이라 리플레이 경로로 폴백
        (스폰 스냅샷에 방금 획이 포함되므로 순서 안전)."""
        layer = int(layer)
        if not (0 <= layer < 5):
            return
        s = self._sanitize_stroke(stroke)
        self._layer_strokes[layer].append(s)
        if self._sky_pending > 0 or self._proxy_hw() is None:
            self._stroke_patches[layer] = []     # 워커가 마스크를 재구성 → 패치 정렬 깨짐
            self._spawn_mask_worker(layer)
            return
        self._push_stroke_patch(layer, s)        # 적용 **전** 픽셀 저장(즉각 undo 용)
        self._mask_ran = True                # 워커 없이 확정 — maskSettled 게이트 충족
        self._layer_mask_strokes[layer] = list(self._layer_strokes[layer])
        self._set_layer_mask(layer, self._apply_stroke_incremental(layer, s))

    def _push_stroke_patch(self, layer: int, stroke) -> None:
        """획이 변경할 bbox 의 현재(적용 전) 픽셀을 패치 스택에 저장 + 메모리 상한 관리."""
        import brush
        hw = self._proxy_hw()
        bbox = brush.stroke_bbox(stroke, hw) if hw is not None else None
        if bbox is None:
            self._stroke_patches[layer].append((0, 0, 0, 0, None))   # 무효 획 = 빈 패치
            return
        y0, y1, x0, x1 = bbox
        base = self._layer_masks[layer]
        region = None if base is None else base[y0:y1, x0:x1].copy()
        patches = self._stroke_patches[layer]
        patches.append((y0, y1, x0, x1, region))
        total = sum(r.nbytes for *_, r in patches if r is not None)
        while patches and total > self._PATCH_CAP_BYTES:
            old = patches.pop(0)                 # 오래된 것부터 폐기(최근 undo 는 유지)
            if old[4] is not None:
                total -= old[4].nbytes

    @Slot(int)
    def popStroke(self, layer) -> None:  # noqa: N802 (QML 슬롯) — 마지막 획 취소
        layer = int(layer)
        if not (0 <= layer < 5) or not self._layer_strokes[layer]:
            return
        self._layer_strokes[layer].pop()
        # 즉각 경로: 마지막 획의 패치(적용 전 픽셀)를 되돌려쓰기 — 래스터 0회, 획 수 무관.
        # 패치 스택이 비었으면(워커 재구성 후 등) automask 리플레이 폴백.
        patches = self._stroke_patches[layer]
        if patches and self._sky_pending == 0:
            import numpy as np
            y0, y1, x0, x1, region = patches.pop()
            hw = self._proxy_hw()
            if hw is not None:
                base = self._layer_masks[layer]
                m = (np.zeros(hw, np.float32) if base is None
                     else base.astype(np.float32, copy=True))
                m[y0:y1, x0:x1] = 0.0 if region is None else region
                if not m.any():
                    m = None
                self._mask_ran = True
                self._layer_mask_strokes[layer] = list(self._layer_strokes[layer])
                self._set_layer_mask(layer, m)
                return
        self._replay_strokes_fast(layer)

    @Slot(int)
    def clearStrokes(self, layer) -> None:  # noqa: N802 (QML 슬롯) — 획 전체 삭제
        layer = int(layer)
        if 0 <= layer < 5 and self._layer_strokes[layer]:
            self._layer_strokes[layer] = []
            self._stroke_patches[layer] = []
            self._replay_strokes_fast(layer)

    # 동기 리플레이 획 수 상한 — 이 이상이면(패치 상한 초과 후 undo 등 드문 폴백) 메인
    # 스레드가 획×ROI 래스터로 수백 ms 굳을 수 있어 워커(비동기+busy 표시)로 넘긴다.
    _REPLAY_SYNC_MAX = 24

    def _replay_strokes_fast(self, layer: int) -> None:
        """획 편집(pop/clear) 반영: 자동 마스크 캐시 위에 남은 획을 동기 리플레이 —
        세그 워커를 안 태우므로 dim/프로그레스 없음(사용자 보고: undo stroke 지연).
        캐시 미확보(keys 있는데 워커가 아직 안 돌았음)·워커 진행 중·획 과다면 워커 폴백.
        keys 없는(브러시 전용) 레이어는 자동 마스크가 정의상 None 이라 캐시 불필요."""
        import brush
        hw = self._proxy_hw()
        auto_ok = self._layer_automask_valid[layer] or not self._layer_keys[layer]
        if (hw is None or self._sky_pending > 0 or not auto_ok
                or len(self._layer_strokes[layer]) > self._REPLAY_SYNC_MAX):
            self._spawn_mask_worker(layer)
            return
        base = self._layer_automask[layer] if self._layer_automask_valid[layer] else None
        strokes = self._layer_strokes[layer]
        if base is None and not strokes:
            m = None
        else:
            m = brush.apply_strokes(base, hw, strokes)
            if not m.any():
                m = None
        self._mask_ran = True
        self._layer_mask_strokes[layer] = list(strokes)
        self._set_layer_mask(layer, m)

    @Slot(int, "QVariantList")
    def setStrokes(self, layer, strokes) -> None:  # noqa: N802 (QML 슬롯)
        """획 목록 통째 설정(사이드카 복원·레이어 시프트 재동기용). 재생성은 안 함 —
        복원 경로가 이어서 부르는 setMaskClasses 가 담당(setLayerRefine 과 같은 분업)."""
        layer = int(layer)
        if 0 <= layer < 5:
            self._layer_strokes[layer] = [self._sanitize_stroke(s) for s in (strokes or [])]
            self._stroke_patches[layer] = []   # 목록 교체 → 증분 이력과 정렬 깨짐

    @staticmethod
    def _qimage_to_rgb(qimg):
        """QImage → (H,W,3) uint8 RGB numpy (자체 소유 복사본). bytesPerLine 스트라이드 패딩 처리."""
        import numpy as np
        im = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = im.width(), im.height()
        if w == 0 or h == 0:
            return np.zeros((max(h, 0), max(w, 0), 3), np.uint8)
        return (np.frombuffer(im.constBits(), np.uint8)
                .reshape(h, im.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy())

    def _sky_input_rgb(self):
        """프록시(헤드룸 카메라네이티브) → 중성(노출0·as-shot WB) display sRGB uint8. 세그 입력.

        Scene/Face/Depth 세 소스가 공유하며 마스크 캐시 미스마다 다시 만든다(실측 785~1190ms)
        → uint8 을 그대로 넘겨 srgb_to_linear 를 LUT 조회로 대체한다(_native_to_scenelinear 참조)."""
        import numpy as np
        u8 = self._qimage_to_rgb(self._proxy_img)
        disp = np.clip(wb.filmic(self._native_to_scenelinear(None, u8=u8)), 0.0, 1.0)
        return (disp * 255.0 + 0.5).astype(np.uint8)

    # ---------- 디헤이즈 물리(DCP): 이미지당 1회 투과율/대기광 추정 ----------
    def _haze_worker(self, seq: int) -> None:
        """백그라운드: 중성 display 베이스(축소본)에서 (t, A, conf) 추정 → 메인으로 전달.
        입력이 노출0·as-shot 베이스라 슬라이더 값과 무관 — 디코딩당 1회면 충분."""
        import numpy as np
        import haze
        res = None
        try:
            u8 = self._qimage_to_rgb(self._proxy_img)
            step = max(1, max(u8.shape[:2]) // 640)    # 추정은 소형으로 충분(속도)
            disp = np.clip(wb.filmic(self._native_to_scenelinear(None, u8=u8[::step, ::step])),
                           0.0, 1.0)
            res = haze.estimate(disp)
        except Exception as exc:
            print(f"[haze] 추정 실패(톤모델 폴백): {exc}")
        self._hazeReady.emit((seq, res))

    @Slot(object)
    def _on_haze_ready(self, payload) -> None:
        import numpy as np
        seq, res = payload
        if seq != self._haze_seq:
            return                       # 이미지 전환됨 → 낡은 추정 폐기
        if res is None:
            self._haze_t, self._haze_A, self._haze_conf = None, [1.0, 1.0, 1.0], 0.0
            if self._haze_provider is not None:
                self._haze_provider.clear()
        else:
            t, A, conf = res
            self._haze_t = t
            self._haze_A = [float(x) for x in A]
            self._haze_conf = float(conf)
            if self._haze_provider is not None:
                u8 = np.ascontiguousarray((np.clip(t, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
                hh, ww = u8.shape
                self._haze_provider.set_image(
                    QImage(u8.data, ww, hh, ww, QImage.Format.Format_Grayscale8).copy())
        self._haze_counter += 1
        self._haze_url = f"image://haze/h?v={self._haze_counter}"
        self.hazeChanged.emit()

    def _get_haze_url(self) -> str:
        return self._haze_url

    def _get_haze_A(self) -> list:
        return self._haze_A

    def _get_haze_conf(self) -> float:
        return self._haze_conf

    hazeUrl = Property(str, _get_haze_url, notify=hazeChanged)
    hazeA = Property("QVariantList", _get_haze_A, notify=hazeChanged)
    hazeConf = Property(float, _get_haze_conf, notify=hazeChanged)

    # ---------- 미스트 산란 필드: (Radius, Highlight) 당 1회 CPU 계산 → 셰이더 텍스처 3장 ----------
    def _mist_worker(self, seq: int, radius: float, hi: float) -> None:
        """백그라운드: 프록시의 카메라네이티브 scene-linear → 산란 필드 3장 + 균일항.

        입력이 WB/매트릭스/노출 **앞**의 공간이라(mist.py 참조) 그 슬라이더들과 무관하다 —
        Amount/Character 도 셰이더 uniform 이라 무관. 그래서 재계산을 부르는 것은 이 두 인자뿐.
        """
        import numpy as np
        import mist
        res = None
        try:
            import raw_loader
            u8 = self._qimage_to_rgb(self._proxy_img)
            if u8.size:
                # 헤드룸 디코드만(유저 WB·매트릭스 없음). 8bit 입력이라 LUT 조회로 오차 0.
                nat = (raw_loader._srgb2lin_lut()[u8.astype(np.uint16) * 257]
                       * PROXY_HEADROOM).astype(np.float32)
                fields, mean = mist.scatter_fields(nat, hi, radius, max(nat.shape[:2]))
                # 인코딩(로그 코덱 + 디더)도 **여기서** 한다 — 예전엔 메인 스레드에서 해서
                # 4.4MP 프록시에서 275ms 동안 UI 가 얼었다. 디더는 원해상도 필드만
                # (축소 필드에 걸면 업샘플되어 블롭이 된다 — mist.encode_field 주석).
                imgs = []
                for f in fields:
                    code = mist.encode_field(f, dither=(f.shape[:2] == nat.shape[:2]))
                    hh, ww = code.shape[:2]
                    rgba = np.empty((hh, ww, 4), np.uint16)
                    rgba[..., :3] = code
                    rgba[..., 3] = 65535
                    rgba = np.ascontiguousarray(rgba)
                    imgs.append(QImage(rgba.data, ww, hh, 8 * ww,
                                       QImage.Format.Format_RGBA64).copy())
                res = (imgs, [float(x) for x in mean])
        except Exception as exc:
            print(f"[mist] 산란 필드 계산 실패(미스트 무동작): {exc}")
        self._mistReady.emit((seq, (float(radius), float(hi)), res))

    @Slot(object)
    def _on_mist_ready(self, payload) -> None:
        seq, field_key, res = payload
        if seq != self._mist_seq:
            return                       # 이미지 전환/파라미터 재변경 → 낡은 결과 폐기
        if res is None:
            self._mist_ready = False
            self._mist_field = None      # 실패 → 다음 요청이 다시 시도할 수 있게
            self._mist_mean = [0.0, 0.0, 0.0]
            if self._mist_provider is not None:
                self._mist_provider.clear()
        else:
            # 워커가 QImage 까지 다 만들어 온다(인코딩은 무겁다 — 메인 스레드 금지).
            imgs, mean = res
            if self._mist_provider is not None:   # 실패 분기와 같은 방어(프로바이더 없는 구성도 있다)
                self._mist_provider.set_images(imgs)
            self._mist_mean = mean
            self._mist_field = field_key
            self._mist_ready = True
        self._mist_counter += 1
        self._mist_urls = [f"image://mist/{i}?v={self._mist_counter}" for i in range(3)]
        self.mistChanged.emit()

    def _maybe_start_mist(self) -> None:
        """필드 계산이 필요하고 **실제로 쓰이는 중**일 때만 시작한다.

        ⚠️Amount 게이트가 핵심이다. 예전엔 디코드마다 무조건 시작해서, 미스트를 안 쓰는 사진도
          장당 프록시 3× 가우시안(8스레드 ~0.55s)과 ~75MB 를 태우고 셰이더 게이트에서 버려졌다 —
          폴더를 화살표로 넘길 때와 배치 export 에서 NR/haze/세그 워커와 코어를 다퉜다."""
        if self._mist_amt <= 0.0 or self._proxy_img is None:
            return
        if self._mist_field == self._mist_want and self._mist_ready:
            return
        if self._mist_field == self._mist_want:
            return                       # 같은 키로 이미 계산 중
        self._mist_field = self._mist_want
        self._mist_seq += 1
        try:
            threading.Thread(target=self._mist_worker,
                             args=(self._mist_seq, self._mist_want[0], self._mist_want[1]),
                             daemon=True).start()
        except Exception as exc:                 # 스레드 생성 실패(RuntimeError 등)
            # ⚠️키를 되돌려 놓지 않으면 위의 '같은 키로 이미 계산 중' 분기에 영영 걸려
            #   그 이미지는 미스트가 다시는 안 나온다(saveGrab 과 같은 방어).
            self._mist_field = None
            print(f"[mist] 워커 시작 실패(미스트 무동작): {exc}")

    @Slot(float)
    def setMistAmount(self, amt: float) -> None:  # noqa: N802 (QML 슬롯)
        """Amount 를 컨트롤러에 알린다 — 필드를 만들 가치가 있는지 판단하는 유일한 근거다.
        (Amount 자체는 셰이더 uniform 이라 렌더에는 이 값을 쓰지 않는다.)"""
        self._mist_amt = float(amt)
        self._maybe_start_mist()

    @Slot(float, float, float)
    def requestMistField(self, radius: float, hi: float, amt: float) -> None:  # noqa: N802
        """Radius/Highlight 가 바뀌었을 때 산란 필드 재계산(QML 이 디바운스해서 부른다).

        ⚠️Amount 를 **같이** 받는다 — 따로 밀면 값이 안 바뀐 대입에서 `onValueChanged` 가 안
          울려 이전 이미지의 Amount 가 남는다. 한 호출로 상태를 함께 맞춘다.
        Amount 가 0 이면 키만 기억하고 계산은 미룬다 — Amount 가 0 을 벗어나는 순간
        `setMistAmount` 가 그 키로 시작한다."""
        self._mist_want = (float(radius), float(hi))
        self._mist_amt = float(amt)
        self._maybe_start_mist()

    def _get_mist_url0(self) -> str:
        return self._mist_urls[0]

    def _get_mist_url1(self) -> str:
        return self._mist_urls[1]

    def _get_mist_url2(self) -> str:
        return self._mist_urls[2]

    def _get_mist_on(self) -> float:
        return 1.0 if self._mist_ready else 0.0

    def _get_mist_mean_r(self) -> float:
        return self._mist_mean[0]

    def _get_mist_mean_g(self) -> float:
        return self._mist_mean[1]

    def _get_mist_mean_b(self) -> float:
        return self._mist_mean[2]

    mistUrl0 = Property(str, _get_mist_url0, notify=mistChanged)
    mistUrl1 = Property(str, _get_mist_url1, notify=mistChanged)
    mistUrl2 = Property(str, _get_mist_url2, notify=mistChanged)
    mistOn = Property(float, _get_mist_on, notify=mistChanged)
    mistMeanR = Property(float, _get_mist_mean_r, notify=mistChanged)
    mistMeanG = Property(float, _get_mist_mean_g, notify=mistChanged)
    mistMeanB = Property(float, _get_mist_mean_b, notify=mistChanged)

    # ---------- 휘도 NR 베이스: 이미지당 1회 가이디드 필터 디노이즈(중성 luma) ----------
    def _nr_worker(self, seq: int) -> None:
        """백그라운드: 중성 display luma 에 가이디드 필터(coeffs.NR_*) → 셰이더 nrBase 텍스처.
        입력이 노출0·as-shot 베이스라 슬라이더와 무관 — 디코딩당 1회면 충분(haze 워커와 동형)."""
        import numpy as np
        import coeffs
        from sky_seg import _guided_filter
        res = None
        try:
            u8 = self._qimage_to_rgb(self._proxy_img)      # LUT 경로(_sky_input_rgb 와 동일, 오차 0)
            disp = np.clip(wb.filmic(self._native_to_scenelinear(None, u8=u8)), 0.0, 1.0)
            lum = (disp @ np.array([0.299, 0.587, 0.114], np.float32)).astype(np.float32)
            res = np.clip(_guided_filter(lum, lum, coeffs.NR_RADIUS, coeffs.NR_EPS), 0.0, 1.0)
        except Exception as exc:
            print(f"[nr] 베이스 계산 실패(휘도 NR 비활성): {exc}")
        self._nrReady.emit((seq, None if res is None else self._pack_nr_qimage(res)))

    @staticmethod
    def _pack_nr_qimage(res):
        """NR 베이스 배열 → (RGBA64 QImage, has_chroma). **워커 스레드에서 호출** — 프록시
        해상도 35MB 패킹을 메인(UI) 스레드에서 하면 완료 순간 프레임이 걸린다(버벅임).
        res: (H,W)=가이디드 luma → 그레이 복제 / (H,W,3)=AI RGB(크로마 유효).
        텍스처는 항상 RGBA64 — Grayscale16 은 샘플링 시 .gb=0 이라 dot(nb,LUMA) 공용
        수식이 깨진다(셰이더가 .rgb 를 읽음)."""
        import numpy as np
        u16 = (np.asarray(res) * 65535.0 + 0.5).astype(np.uint16)
        has_chroma = u16.ndim == 3
        if not has_chroma:
            u16 = np.repeat(u16[..., None], 3, axis=2)
        hh, ww = u16.shape[:2]
        rgba = np.empty((hh, ww, 4), dtype=np.uint16)
        rgba[..., :3] = u16
        rgba[..., 3] = 65535
        rgba = np.ascontiguousarray(rgba)
        return (QImage(rgba.data, ww, hh, ww * 8, QImage.Format.Format_RGBA64).copy(),
                has_chroma)

    @Slot(object)
    def _on_nr_ready(self, payload) -> None:
        seq, packed = payload
        if seq != self._nr_seq:
            return                       # 이미지 전환됨 → 낡은 결과 폐기
        has_chroma = bool(packed is not None and packed[1])
        if not has_chroma and self._nr_ai_seq == seq:
            # AI(RGB) 베이스가 이미 이 seq 로 적용됨 — 가이디드는 AI 완료 전 폴백일 뿐.
            # 뒤늦게 도착한 가이디드(luma-only/None)가 AI 베이스를 덮어써 조용히
            # 크로마 NR 을 잃는(품질 저하) 레이스 방지.
            return
        if packed is None:
            self._nr_ready = False
            self._nr_chroma = False
            if self._nr_provider is not None:
                self._nr_provider.clear()
        else:
            qimg, has_chroma = packed
            if self._nr_provider is not None:
                self._nr_provider.set_image(qimg)
            self._nr_chroma = has_chroma
            self._nr_ready = True
            if has_chroma:
                self._nr_ai_seq = seq
        self._nr_counter += 1
        self._nr_url = f"image://nrbase/n?v={self._nr_counter}"
        self.nrChanged.emit()

    def _get_nr_url(self) -> str:
        return self._nr_url

    def _get_nr_ready(self) -> bool:
        return self._nr_ready

    def _get_nr_chroma(self) -> bool:
        return self._nr_chroma

    nrBaseUrl = Property(str, _get_nr_url, notify=nrChanged)
    nrReady = Property(bool, _get_nr_ready, notify=nrChanged)
    nrChroma = Property(bool, _get_nr_chroma, notify=nrChanged)   # AI RGB 베이스(크로마 유효)

    # ---------- 업데이트 확인: GitHub 릴리스 목록 vs APP_VERSION ----------
    @Slot()
    def startUpdateCheck(self) -> None:
        """앱 시작 수 초 후 1회 호출(main 의 QTimer). 백그라운드라 UI 무영향."""
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    # 조회 실패 시 재시도 간격(초). 확인은 실행당 1회뿐이라, 그 순간의 일시 장애가 세션 전체를
    # 놓치게 만든다 — 한 번만 더 시도해 흔한 blip 을 흡수한다(계속 두드리지는 않는다).
    _UPDATE_RETRY_SEC = 60

    def _release_candidates(self, url: str) -> list:
        """릴리스 JSON(목록 또는 단일 객체)에서 유효 후보만 파싱.

        `v메이저.마이너.패치` **정확 일치** 태그만 채택 — 자산 릴리스(models-v1)·postfix 태그
        (v1.2.0_deprecated)·2파트(v1.0)는 정규식으로 걸러진다. prerelease/draft 제외.
        반환 [] 은 '조회 실패' 와 '해당 릴리스 없음' 을 구분하지 않는다(호출측이 폴백으로 처리)."""
        import json as _json
        import re
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"FilmRawstery/{APP_VERSION}",   # GitHub API 는 UA 필수
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                data = _json.load(r)
        except Exception:
            return []                       # 오프라인·타임아웃·5xx·한도초과 전부 여기로
        rels = data if isinstance(data, list) else [data]
        out = []                            # [((maj,min,pat), "vX.Y.Z", html_url), ...]
        for rel in rels:
            if not isinstance(rel, dict) or rel.get("prerelease") or rel.get("draft"):
                continue
            m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", str(rel.get("tag_name", "")))
            if m:
                out.append((tuple(int(g) for g in m.groups()), m.group(0),
                            str(rel.get("html_url", ""))))
        return out

    def _latest_release(self):
        """게시된 릴리스 중 semver 최대 → ((maj,min,pat), tag, url), 조회 불가면 None.

        ⚠️목록(/releases)을 먼저 쓴다 — 전체에서 max 를 취하므로 생성 순서에 의존하지 않는다
          (태그 이동/재게시에 안전). 목록이 비면 /releases/latest 로 폴백한다:
          **GitHub 장애 시 목록이 `200 + []` 를 돌려주는 것을 실측**했고(504 와 번갈아 발생),
          그건 예외가 아니라 '릴리스 없음' 처럼 보여서 알림이 조용히 사라졌다.
          폴백의 한계: /releases/latest 는 **생성 시각** 기준이라 구버전 계열 핫픽스를 나중에
          올리면 semver 최대보다 낮게 나올 수 있다(과대 보고는 아래 `>` 비교가 막는다).
        ⚠️/tags 는 폴백으로 쓰지 않는다 — 릴리즈 절차가 태그를 먼저 push 하고 릴리스를 나중에
          만들므로, 그 사이엔 '태그는 있는데 받을 게 없는' 상태를 새 버전으로 알리게 된다."""
        cands = self._release_candidates(_RELEASES_API)
        if not cands:
            cands = self._release_candidates(_RELEASES_API + "/latest")
        return max(cands) if cands else None

    def _update_check_worker(self) -> None:
        """새 버전이 있으면 _updateSig 로 알린다. 실패는 조용히 무시 — 최선 노력 기능."""
        import time
        for attempt in range(2):
            if attempt:
                time.sleep(self._UPDATE_RETRY_SEC)
            best = self._latest_release()
            if best is None:
                continue                    # 조회 자체가 실패 → 한 번 더
            cur = tuple(int(x) for x in APP_VERSION.split("."))
            if best[0] > cur:
                self._updateSig.emit((best[1], best[2]))
            return                          # 조회 성공(최신이어도) → 재시도 없음

    @Slot(object)
    def _on_update_found(self, payload) -> None:
        self._update_version, self._update_url = payload
        print(f"[update] 새 버전 {self._update_version} -> {self._update_url}")
        self.updateChanged.emit()

    def _get_update_version(self) -> str:
        return self._update_version

    def _get_update_url(self) -> str:
        return self._update_url

    updateVersion = Property(str, _get_update_version, notify=updateChanged)
    updateUrl = Property(str, _get_update_url, notify=updateChanged)

    # ---------- AI 디노이즈(NAFNet): 온디맨드 타일 추론으로 nrBase 를 교체 ----------
    @Slot(result=bool)
    def aiNrGpuAvailable(self) -> bool:
        """GPU 가속 EP(DirectML/CoreML) 사용 가능 여부. QML 이 토글 시 확인 —
        CPU 폴백이면 느린 계산(프리뷰 수 분, export 수십 분)을 진행할지 사용자에게 묻는다."""
        try:
            import ai_denoise
            return ai_denoise.gpu_available()
        except Exception:
            return False

    @Slot(bool)
    def setUiBusy(self, busy: bool) -> None:
        """QML editDragActive → 드래그 중 AI 타일 루프 일시정지(denoise_rgb hold 콜백).
        타일 1개가 도는 동안 GPU 가 통째로 점유돼 UI 프레임이 밀리므로, pace(타일 사이
        양보)로는 부족하고 조작 중엔 아예 멈추는 것이 근본적."""
        self._ui_busy = bool(busy)

    @Slot(bool)
    def setAiNr(self, on: bool) -> None:
        """AI 디노이즈 베이스 토글. on=백그라운드 NAFNet 타일 추론 시작 — 완료까지는 기존
        가이디드 베이스가 그대로 동작(완료 시 nrBase 텍스처만 교체, 셰이더 무변경).
        off=가이디드 베이스 재계산으로 즉시 복귀. 파일별 편집값(사이드카 aiNr)."""
        on = bool(on)
        if on == self._ai_nr:
            return
        self._ai_nr = on
        self._ai_status = ""
        self.aiNrChanged.emit()
        if self._proxy_img is None:
            return
        self._nr_seq += 1        # 진행 중이던 AI 타일 루프 취소(cancel 콜백이 seq 비교)
        # 가이디드를 항상 먼저(수 초 내 완료) — 켜는 경우엔 AI 완료까지의 폴백 베이스,
        # 끄는 경우엔 복귀 베이스. seq 를 올렸으므로 이전 결과는 폐기되어 재계산이 필요.
        threading.Thread(target=self._nr_worker, args=(self._nr_seq,), daemon=True).start()
        if on:
            threading.Thread(target=self._ai_nr_worker, args=(self._nr_seq,), daemon=True).start()

    def _ai_nr_worker(self, seq: int) -> None:
        """백그라운드: NAFNet 타일 추론으로 중성 베이스(RGB) 디노이즈 → nrBase 교체(_on_nr_ready 공용).
        최초 사용 시 모델 자동 다운로드(~117MB). 이미지 전환/토글 해제(seq 변경)면 타일 경계에서
        중단, 실패 시 기존(가이디드) 베이스 유지 + 오류 문구만 표시."""
        import numpy as np
        import ai_denoise
        try:
            u8 = self._qimage_to_rgb(self._proxy_img)      # LUT 경로(_sky_input_rgb 와 동일, 오차 0)
            disp = np.clip(wb.filmic(self._native_to_scenelinear(None, u8=u8)), 0.0, 1.0)
            if not ai_denoise.model_available():
                # 다운로드 중엔 이미지 영역 차단 오버레이 + 프로그레스바(하늘 모델과 동일 UX).
                # reporthook 은 8KB 단위(~1.4만 회)라 1% 단위로 스로틀해 시그널 폭주 방지.
                self._aiNrStatusSig.emit((seq, "Downloading AI model… (first use, ~117MB)"))
                self._aiNrDlSig.emit((True, 0.0))
                _last = [0.0]

                def _dl_prog(f):
                    if f - _last[0] >= 0.01 or f >= 1.0:
                        _last[0] = f
                        self._aiNrDlSig.emit((True, f))
                try:
                    ai_denoise.ensure_model(progress=_dl_prog)
                finally:
                    self._aiNrDlSig.emit((False, 1.0))   # 실패해도 오버레이 반드시 해제
            dev = ai_denoise.provider_label()    # "GPU" | "CPU"
            if ai_denoise._session_obj is None:
                # 최초 1회: onnxruntime DLL 로드 + (DML) 디바이스 프로빙/셰이더 컴파일에
                # 수 초 — GPU 를 점유해 화면이 잠깐 멈춘다. 차단 오버레이를 먼저 켜고 한 프레임
                # 그려질 시간을 준 뒤 세션을 만든다 → GPU stall 중 마지막 프레임('Preparing…')이
                # 화면에 남아 '정체불명 freeze' 대신 '준비 중' 화면으로 보인다. (로드 시 prewarm
                # 이 이미 만들었으면 이 블록은 건너뜀 → 오버레이 안 뜸.)
                self._aiNrStatusSig.emit((seq, f"AI denoise: initializing ({dev}, first use)…"))
                self._aiNrInitSig.emit(True)
                import time
                time.sleep(0.2)          # 오버레이 프레임이 present 될 시간(GPU stall 전)
                try:
                    ai_denoise._session()
                finally:
                    self._aiNrInitSig.emit(False)   # 실패해도 오버레이 반드시 해제
            self._aiNrStatusSig.emit((seq, f"AI denoise: computing… 0% ({dev})"))
            res = ai_denoise.denoise_rgb(        # RGB 전체 — luma(휘도)+chroma(컬러) NR 베이스
                disp,
                progress=lambda f: self._aiNrStatusSig.emit(
                    (seq, f"AI denoise: computing… {int(f * 100)}% ({dev})")),
                cancel=lambda: seq != self._nr_seq,
                pace=ai_denoise.UI_PACE,         # 타일 사이 양보 — UI 버벅임 완화
                hold=lambda: self._ui_busy)      # 드래그 중 일시정지 — 조작 중 버벅임 제거
            self._aiNrStatusSig.emit((seq, f"AI denoise: active ({ai_denoise.provider_label()})"))
            self._nrReady.emit((seq, self._pack_nr_qimage(res)))   # 패킹도 워커 스레드에서
        except ai_denoise.Cancelled:
            pass                                       # 이미지 전환/해제 → 조용히 폐기
        except Exception as exc:
            print(f"[ai-nr] 계산 실패(가이디드 베이스 유지): {exc}")
            # (문구에 em-dash 등 cp949 비인코딩 문자 금지 — 콘솔로 흘러갈 수 있는 문자열 공통 규칙)
            self._aiNrStatusSig.emit((seq, "AI denoise failed - using standard NR"))

    @Slot(object)
    def _on_ai_nr_status(self, payload) -> None:
        seq, text = payload
        if seq != self._nr_seq:
            return                       # 이미지 전환/재토글됨 → 낡은 상태 문구 폐기
        self._ai_status = str(text)
        self.aiNrChanged.emit()

    @Slot(object)
    def _on_ai_nr_dl(self, payload) -> None:
        downloading, prog = payload
        was = self._ai_downloading
        self._ai_downloading = bool(downloading)
        self._ai_dl_prog = float(prog)
        self.aiNrChanged.emit()
        if was and not self._ai_downloading:
            self.modelsChanged.emit()   # 기능 첫 사용으로 받힌 것도 AI Models 목록에 반영

    @Slot(bool)
    def _on_ai_nr_init(self, on) -> None:
        self._ai_initializing = bool(on)
        self.aiNrChanged.emit()

    def _get_ai_nr(self) -> bool:
        return self._ai_nr

    def _get_ai_status(self) -> str:
        return self._ai_status

    def _get_ai_downloading(self) -> bool:
        return self._ai_downloading

    def _get_ai_dl_prog(self) -> float:
        return self._ai_dl_prog

    def _get_ai_initializing(self) -> bool:
        return self._ai_initializing

    aiNr = Property(bool, _get_ai_nr, notify=aiNrChanged)
    aiNrStatus = Property(str, _get_ai_status, notify=aiNrChanged)
    aiNrDownloading = Property(bool, _get_ai_downloading, notify=aiNrChanged)
    aiNrDlProgress = Property(float, _get_ai_dl_prog, notify=aiNrChanged)
    aiNrInitializing = Property(bool, _get_ai_initializing, notify=aiNrChanged)

    def _dl_progress_cb(self):
        """모델 다운로드 진행률 콜백 — 1% 스로틀로 _segDlSig 에 전달. 호출측이 finally 로 해제."""
        self._segDlSig.emit((True, 0.0))
        last = [0.0]

        def _cb(f):
            if f - last[0] >= 0.01 or f >= 1.0:
                last[0] = f
                self._segDlSig.emit((True, f))
        return _cb

    def _seg_input(self, img_gen: int):
        """세그 입력 3종 스냅샷 (rgb8, hw, guide). 이미지 전환 중이면 (None, None, None).

        마스크 워커와 얼굴 검출 워커가 공유한다. ⚠️세 값을 한 번에 잡되 **셋 다** 검사한다 —
        메인 스레드가 이미지 전환으로 캐시를 비우는 중이면(_on_render_ready 가 guide→size→rgb8
        순으로 None 대입) rgb8 만 살아 있는 찢긴 조합을 읽을 수 있다. 하나라도 비면 다시 만든다."""
        import numpy as np
        import sky_seg
        rgb8, hw, guide = self._seg_rgb8, self._seg_size, self._seg_guide
        if rgb8 is None or hw is None or guide is None:
            rgb8 = self._sky_input_rgb()
            hw = tuple(rgb8.shape[:2])
            guide = (rgb8.astype(np.float32) / 255.0) @ sky_seg._LUMA
            if img_gen != self._img_gen:             # 준비 중 이미지 전환 → 캐시 안 함(stale)
                return None, None, None
            self._seg_rgb8, self._seg_size, self._seg_guide = rgb8, hw, guide
        return rgb8, hw, guide

    @Slot()
    def requestFaces(self) -> None:  # noqa: N802 (QML 슬롯) — Face 탭을 열 때 호출
        """얼굴 **검출만** 백그라운드 실행(~60ms, 232KB 모델). 340MB 파싱 모델은 안 건드린다.

        부위 체크박스를 누르기 전에 얼굴 목록이 있어야 한다 — 없으면 '선택 없음 = 전체 얼굴'
        경로로 빠져서 기본값(가장 큰 얼굴 1명)이 적용되지 않는다."""
        if self._face_scanning or self._face_scanned or self._proxy_img is None:
            return
        self._face_scanning = True
        self.facesChanged.emit()
        threading.Thread(target=self._face_scan_worker, args=(self._img_gen,),
                         daemon=True).start()

    def _face_scan_worker(self, img_gen: int) -> None:
        # ⚠️dets=None 은 '실패/중단'을 뜻한다. 빈 리스트([])는 '검출했는데 얼굴이 없음'이라
        #   캐시해도 되지만, 실패를 []로 캐시하면 얼굴 없는 사진으로 굳어져 이 사진에서는
        #   얼굴 마스킹이 영영 안 된다(네트워크가 잠깐 끊겨 모델을 못 받은 경우 등).
        dets, thumbs = None, []
        try:
            import face_seg
            # 검출기(232KB)만 확보 — 파싱 모델(340MB)은 실제로 부위를 고를 때 받는다.
            face_seg.ensure_detector()
            rgb8, _hw, _g = self._seg_input(img_gen)
            if rgb8 is None or img_gen != self._img_gen:
                return
            # 마스크 워커가 먼저 돌아 이미 검출해 뒀으면 **그 목록을 그대로 쓴다** — 다시 검출하면
            # 새 리스트가 되어 _face_parsed 의 인덱스와 어긋날 여지가 생긴다(같은 입력이라 순서는
            # 같겠지만, 캐시 키가 인덱스인 이상 굳이 새로 만들 이유가 없다).
            found = self._face_dets
            if found is None:
                found = face_seg.detect_faces(rgb8)
            thumbs = [face_seg.face_thumb(rgb8, d) for d in found]
            dets = found
        except Exception as exc:
            print(f"[mask] 얼굴 검출 실패: {exc}")
        finally:
            self._facesReady.emit((img_gen, dets, thumbs))

    @Slot(object)
    def _on_faces_ready(self, payload) -> None:
        import numpy as np
        img_gen, dets, thumbs = payload
        self._face_scanning = False
        if img_gen != self._img_gen:        # 이전 이미지의 늦은 결과 → 버림
            self.facesChanged.emit()
            return
        # 성공·실패 모두 scanned 를 세운다 — QML 이 facesChanged 마다 재시도하므로 안 세우면
        # 실패가 무한 재시도 루프가 된다(매번 다운로드 타임아웃).
        self._face_scanned = True
        if dets is None:                    # 실패 → _face_dets 는 None 그대로 둔다.
            # 썸네일은 못 띄우지만, 부위를 체크하면 마스크 워커가 스스로 다시 검출을 시도한다.
            # 여기서 []로 캐시하면 '얼굴 없는 사진'으로 굳어 마스킹 자체가 막힌다.
            self.facesChanged.emit()
            return
        self._face_dets = dets
        urls = []
        for i, t in enumerate(thumbs[:MAX_FACE_SLOTS]):
            a = np.ascontiguousarray(t)
            h, w = a.shape[:2]
            qi = QImage(a.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            if self._face_provider is not None:
                self._face_provider.set_image(i, qi)
            self._face_counters[i] += 1
            urls.append(f"image://facethumb/{i}?v={self._face_counters[i]}")
        self._face_thumb_urls = urls
        self.facesChanged.emit()

    def _face_keys(self):
        """검출 결과 → 선택 key 목록. **QML 이 좌표를 직접 포맷하지 않게** 여기서 만든다 —
        JS toFixed 와 파이썬 f-string 의 반올림이 경계값에서 갈리면 매칭이 조용히 어긋난다."""
        if not self._face_dets or not self._seg_size:
            return []
        import face_seg
        return [face_seg.face_key(*face_seg.det_center(d, self._seg_size))
                for d in self._face_dets]

    def _get_face_count(self) -> int:
        return len(self._face_dets or [])

    def _get_face_thumb_urls(self):
        return list(self._face_thumb_urls)

    def _get_face_scanning(self) -> bool:
        return self._face_scanning

    faceCount = Property(int, _get_face_count, notify=facesChanged)
    faceThumbUrls = Property("QVariantList", _get_face_thumb_urls, notify=facesChanged)
    faceKeys = Property("QVariantList", _face_keys, notify=facesChanged)
    faceScanning = Property(bool, _get_face_scanning, notify=facesChanged)

    def _mask_worker(self, img_gen: int, layer: int, lseq: int, keys, strokes) -> None:
        """레이어 마스크 생성. keys 는 장면(sky_seg)·얼굴(face_seg) 그룹과 깊이 범위
        (depth `depth@near,far,feather`)가 섞여 올 수 있고, 각 소스가 만든 알파를
        np.maximum 으로 합집합한다. 추론 결과는 이미지당 캐시 —
        체크박스/슬라이더 재조합만으로는 재추론하지 않는다.
        strokes(브러시 획)는 합집합 **최종** 마스크 위에 리플레이(brush.apply_strokes) —
        keys 없이 획만 있는 레이어 = 순수 수동 마스크(닷징/버닝)."""
        mask = None
        automask = None      # 획 적용 전 자동 마스크 스냅샷(pop/clear 동기 리플레이 base)
        status_set = False
        try:
            # ⚠️import 는 반드시 try 안에서 — 여기서 예외가 나면 _skyReady 가 발화하지 않아
            #   _sky_pending 이 영영 안 줄고 UI 가 busy 로 굳는다(스피너·체크박스 잠김).
            import numpy as np
            import depth
            import face_seg
            import sky_seg
            ade_ids = sky_seg.class_ids_for(keys)
            face_ids = face_seg.class_ids_for(keys)
            drange = depth.range_from_keys(keys)
            if not ade_ids and not face_ids and drange is None and not strokes:
                self._skyReady.emit((img_gen, layer, lseq, None, strokes, None))
                return

            # 세그 입력(중성 display sRGB)과 휘도 가이드는 두 소스 공용 — 예전엔 ADE 추론 분기
            # 안에서 만들어서, 얼굴만 선택하면 쓸 수 없었다.
            # ⚠️세 값을 한 번에 스냅샷하되 **셋 다** 검사한다 — 메인 스레드가 이미지 전환으로
            #   캐시를 비우는 중이면(_on_render_ready 가 guide→size→rgb8 순으로 None 대입)
            #   rgb8 만 살아 있는 찢긴 조합을 읽을 수 있다. 하나라도 비면 다시 만든다.
            rgb8, hw, guide = self._seg_input(img_gen)
            if rgb8 is None:                         # 준비 중 이미지 전환 → stale
                self._skyReady.emit((img_gen, layer, lseq, None, strokes, None))
                return

            # ── Scene∪Face 성분 캐시 ──────────────────────────────────────────
            # 깊이 범위 슬라이더는 깊이 성분만 바꾼다. 그런데 마스크가 합집합이라 캐시가 없으면
            # 매 커밋이 sky_seg.compose_mask 를 통째로 다시 돌린다(프록시 전체 scipy 가이디드필터
            # + binary_fill_holes = ~870ms) → 드래그마다 dim. 비-깊이 key 가 그대로면 재사용한다.
            # ⚠️캐시 배열을 그대로 mask 에 대입하고 아래에서 np.maximum 으로 합치는데,
            #   np.maximum 은 새 배열을 반환하므로 캐시가 오염되지 않는다.
            seg_keys = sorted(str(k) for k in keys if not str(k).startswith("depth@"))
            seg_cached = (self._layer_segkeys[layer] == seg_keys
                          and self._layer_segmask[layer] is not None)
            if seg_cached:
                mask = self._layer_segmask[layer]
                ade_ids = face_ids = []              # 재계산 건너뛰기

            if ade_ids:
                if self._seg_probs is None:              # 이미지당 추론 1회 → 캐시
                    # 모델이 아직 없으면 최초 1회 다운로드(~105MB) → 진행률 % 문구 표시.
                    # (legacy 에 있으면 ensure 가 복사만 하므로 '다운로드' 문구는 진짜 없을 때만)
                    if not os.path.exists(sky_seg.MODEL_PATH):
                        if not sky_seg.model_available():
                            # 진짜 다운로드일 때만 전용 프로그레스바(AI 디노이즈와 동일 UX).
                            # 명칭 주의: 하늘 전용이 아니라 150클래스 세그멘테이션.
                            try:
                                sky_seg.ensure_model(self._dl_progress_cb())
                            finally:
                                self._segDlSig.emit((False, 1.0))   # 실패해도 반드시 해제
                        else:
                            sky_seg.ensure_model()       # legacy 복사(순간, 표시 없음)
                    probs = sky_seg.infer_softmax(rgb8)[0]
                    # ⚠️캐시 쓰기는 세대 가드 필수 — 추론 중 이미지가 바뀌면(_on_render_ready 가
                    # 캐시를 비움) 이전 이미지의 softmax 를 되살려 다음 워커가 '이전 이미지
                    # 마스크를 현재 이미지에' 합성하는 레이스가 있었음. stale 워커는 여기서 종료.
                    if img_gen != self._img_gen:
                        self._skyReady.emit((img_gen, layer, lseq, None, strokes, None))
                        return
                    self._seg_probs = probs
                else:
                    probs = self._seg_probs
                if probs is not None:
                    mask = sky_seg.compose_mask(probs, hw, ade_ids, guide)

            if face_ids:
                # 파싱은 얼굴당 ~0.8s 로 비싸다 → 락으로 이미지당 1회만(레이어 5개가 동시에
                # 복원돼도 중복 추론 없음). 부위 조합 변경은 락 밖 recompose(~10ms).
                # ⚠️**선택된 얼굴만 파싱**한다. 기본값이 '가장 큰 얼굴 1명'이라 5인 사진에서
                #   전부 파싱하면 4초, 필요한 것만 하면 0.8초다. 캐시는 인덱스별 dict.
                with self._face_lock:
                    if not face_seg.is_ready():          # 최초 1회 다운로드(~340MB)
                        try:
                            face_seg.ensure_model(self._dl_progress_cb())
                        finally:
                            self._segDlSig.emit((False, 1.0))
                    else:
                        face_seg.ensure_model()          # legacy 복사(순간, 표시 없음)
                    dets = self._face_dets
                    if dets is None:                     # Face 탭을 안 거치고 왔으면 여기서 검출
                        dets = face_seg.detect_faces(rgb8)
                    cache = self._face_parsed if isinstance(self._face_parsed, dict) else {}
                    # 얼굴 선택: keys 의 face@ 중심좌표를 현재 검출에 최근접 매칭(None = 전체).
                    # 인덱스가 아니라 좌표인 이유는 face_seg.face_key 주석 참조.
                    sel = face_seg.match_faces(face_seg.face_sel_from_keys(keys), dets, hw)
                    want = list(range(len(dets))) if sel is None else list(sel)
                    # 선택 크롭에 걸치는 라이벌 얼굴도 파싱 — 접촉부 소유권(확률 비교)에 필요.
                    # 떨어져 있는 얼굴은 안 파싱하고, 결과는 같은 캐시라 선택을 바꿔도 재사용된다.
                    need = want + face_seg.rivals_for(want, dets)
                    todo = [i for i in need if i not in cache]
                    if todo:
                        status_set = True

                        def _fp(i, n):
                            self._segStatusSig.emit(f"Analyzing face {i + 1} of {n}…")
                        fresh = face_seg.parse_faces(rgb8, [dets[i] for i in todo], on_face=_fp)
                        if img_gen != self._img_gen:     # 파싱 중 이미지 전환 → 캐시 안 함
                            self._skyReady.emit((img_gen, layer, lseq, None, strokes, None))
                            return
                        for i, pr in zip(todo, fresh):
                            cache[i] = pr
                    self._face_dets = dets
                    self._face_parsed = cache            # 빈 dict(얼굴 없음)도 캐시 — 재검출 방지
                    # ⚠️락 안에서 지역 리스트로 잡고 나간다. 메인 스레드가 이미지 전환 시 락 없이
                    #   재바인딩하므로, 나간 뒤 self. 로 다시 읽으면 옛 이미지 좌표가 섞인다.
                    parsed = [cache[i] for i in want if i in cache]
                    own_dets = [dets[i] for i in want if i in cache]
                    all_parsed = dict(cache)
                # own/all 검출 + 파싱 캐시를 같이 넘겨 크롭에 걸친 **선택 안 된** 얼굴을 감쇠한다
                # (붙어 있는 두 얼굴에서 남의 피부까지 마스크에 들어오는 것 방지, 경계는 양쪽
                # 파싱 확률 비교로 — face_seg._rival_suppress).
                fm = face_seg.compose_face_mask(parsed, rgb8, face_ids, own_dets, dets,
                                                all_parsed)
                mask = fm if mask is None else np.maximum(mask, fm)

            # 방금 만든 Scene∪Face 성분을 캐시(깊이 합집합 **전** 상태여야 재사용 가능).
            # ⚠️img_gen 가드 — 합성 중 이미지가 바뀌면 이전 이미지 성분을 되살리게 된다(_seg_probs 와 동일).
            if not seg_cached and seg_keys:
                if img_gen != self._img_gen:
                    self._skyReady.emit((img_gen, layer, lseq, None, strokes, None))
                    return
                self._layer_segmask[layer] = mask
                self._layer_segkeys[layer] = seg_keys
                # ⚠️깊이 없는 레이어에서는 이 배열이 _layer_masks[layer] 와 **같은 객체**가 된다
                #   (아래 깊이 합집합이 없으면 mask 가 그대로 전달됨). 소비측이 전부 새 배열을
                #   만들어 쓰므로(_set_layer_mask 의 clip/astype, pipeline 의 zoom/clip/1-sm)
                #   현재는 안전하지만, 마스크를 in-place 로 고치는 코드를 추가하면 캐시가 오염된다.

            if drange is not None:
                # 거리 맵은 이미지당 1회(추론+정제 ~0.65s) → 락으로 레이어 동시 복원 시 중복 방지.
                # 범위 슬라이더 드래그는 락 밖 밴드패스(~46ms)라 재추론이 없다.
                with self._depth_lock:
                    if self._depth_map is None:
                        if not depth.is_ready():         # 최초 1회 다운로드(~105MB, 2파일)
                            try:
                                depth.ensure_model(self._dl_progress_cb())
                            finally:
                                self._segDlSig.emit((False, 1.0))   # 실패해도 반드시 해제
                        else:
                            depth.ensure_model()         # legacy 복사(순간, 표시 없음)
                        dm = depth.infer_distance(rgb8, guide)
                        # ⚠️캐시 쓰기 세대 가드 — sky_seg 캐시와 같은 레이스(추론 중 이미지 전환 시
                        #   이전 이미지의 거리 맵을 되살려 다음 워커가 현재 이미지에 합성).
                        if img_gen != self._img_gen:
                            self._skyReady.emit((img_gen, layer, lseq, None, strokes, None))
                            return
                        self._depth_map = dm
                    dm = self._depth_map
                if drange == depth.AUTO:
                    # 켤 때는 범위를 정할 근거(거리 맵)가 아직 없었다 → 지금 분포에서 시드하고
                    # 메인 스레드가 센티넬 키를 실제 값으로 교체한다(다음 재조합부터 고정).
                    drange = depth.auto_range(dm)
                    self._depthAutoSig.emit((img_gen, layer) + tuple(drange))
                dmask = depth.compose_mask(dm, *drange)
                mask = dmask if mask is None else np.maximum(mask, dmask)
            # ── 브러시 획 리플레이(자동 마스크 합집합 **뒤**) ──────────────────
            # mask=None(빈 레이어)이면 0 캔버스에서 시작 = 순수 수동 마스크.
            # apply_strokes 는 항상 새 배열 반환 → _layer_segmask 캐시 비오염.
            automask = mask                  # 획 적용 전 스냅샷(아래 페이로드로 전달·캐시)
            if strokes:
                import brush
                mask = brush.apply_strokes(mask, hw, strokes)

            # 전부 0(예: 얼굴 없는 사진에 Face 선택, 빼기 획만 있는 빈 레이어) → 마스크 없음.
            # 안 그러면 레이어에 ● 가 붙고 빈 오버레이가 번쩍이며 export 가 0 배열을 들고 다닌다.
            if mask is not None and not mask.any():
                mask = None
        except Exception as exc:
            print(f"[mask] 세그 실패: {exc}")
        finally:
            # 이 워커가 띄운 문구만 지운다 — 무조건 지우면 동시 실행 중인 다른 레이어의
            # "Analyzing N face(s)…" 를 먼저 끝난 워커가 꺼버린다.
            if status_set:
                self._segStatusSig.emit("")
        self._skyReady.emit((img_gen, layer, lseq, mask, strokes, automask))

    @Slot(object)
    def _on_depth_auto(self, payload) -> None:
        """자동 시드된 범위를 확정: `depth@auto` 센티넬 → 실제 값 키로 교체 후 QML 에 통보.

        ⚠️컨트롤러의 _layer_keys 도 **같이** 갱신해야 한다 — QML 쪽만 바꾸면 저장된 키가
        여전히 'auto' 라서, 같은 값으로 다시 오는 setMaskClasses 가 no-op 으로 걸러지지 않고
        워커를 한 번 더 돈다(마스크는 같으므로 무해하지만 낭비)."""
        import depth
        img_gen, layer, near, far, feather = payload
        if img_gen != self._img_gen or not (0 <= layer < 5):
            return                          # 이미지 전환 중 도착 → 폐기
        # ⚠️센티넬이 **아직 그대로일 때만** 적용한다. 첫 사용 추론(0.7~6s)이 도는 동안 사용자가
        #   슬라이더를 움직이면 keys 는 이미 수동값(depth@0.30,…)으로 바뀌어 있다 — 그때 이 늦은
        #   시드가 덮어쓰면 방금 조작한 슬라이더가 시드값으로 '튕겨 돌아가고', 마스크(lseq 가드로
        #   수동값 워커가 만든 것)와 keys 가 어긋난다. img_gen 가드로는 못 막는다(같은 이미지).
        if "depth@auto" not in (str(k) for k in self._layer_keys[layer]):
            return
        keys = [k for k in self._layer_keys[layer] if not str(k).startswith("depth@")]
        keys.append(depth.range_key(near, far, feather))
        keys.sort()                         # QML _commitMaskKeys 와 같은 정규화(직렬화 일치)
        self._layer_keys[layer] = keys
        self.depthAutoResolved.emit(layer, float(near), float(far), float(feather))

    @Slot(object)
    def _on_sky_ready(self, payload) -> None:
        img_gen, layer, lseq, mask, strokes, automask = payload
        self._sky_pending -= 1
        if self._sky_pending <= 0:       # 모든 in-flight 워커 종료 → busy 해제
            self._sky_pending = 0
            self._sky_busy = False
            self.skyBusyChanged.emit()
        if img_gen != self._img_gen or lseq != self._layer_seq[layer]:
            return                       # stale(이미지 전환 or 레이어 재요청) → 마스크 반영 안 함
        # ⚠️_mask_ran 은 반드시 stale 가드 **뒤**에서 — 이전 이미지의 늦은 워커가 여기서
        #   켜버리면 새 이미지가 아직 마스크를 만들기도 전에 maskSettled 가 참이 돼,
        #   배치 export 가 마스크 없이 저장해 버린다.
        self._mask_ran = True            # 결과가 None 이어도 '요청은 끝났다'(maskSettled)
        self._layer_mask_strokes[layer] = strokes   # 이 마스크가 어떤 획 목록으로 만들어졌나
        self._layer_automask[layer] = automask      # 획 적용 전 자동 마스크(pop/clear 동기 base)
        self._layer_automask_valid[layer] = True
        self._stroke_patches[layer] = []            # 재구성 → 증분 패치 이력 무효(pop 은 리플레이 폴백)
        self._set_layer_mask(layer, mask)
        if mask is not None:
            self.skySelected.emit()      # 갱신 완료 → QML 이 마스크 오버레이 자동 표시

    def _set_layer_mask(self, layer: int, mask) -> None:
        """레이어 마스크(numpy [0,1] 또는 None)를 프로바이더/캐시에 반영. None=1x1 검정(sampler 유효)."""
        import numpy as np
        if not (0 <= layer < 5):
            return
        self._layer_masks[layer] = mask  # CPU export 용(프록시 해상도 보관)
        if mask is None:
            qi = QImage(1, 1, QImage.Format.Format_Grayscale8)
            qi.fill(0)
        else:
            g = np.ascontiguousarray((np.clip(mask, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
            h, w = g.shape
            qi = QImage(g.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        if self._sky_provider is not None:
            self._sky_provider.set_image(layer, qi)
        self._layer_counters[layer] += 1
        self._layer_urls[layer] = f"image://skymask/{layer}?v={self._layer_counters[layer]}"
        self.skyMaskChanged.emit()

    @Slot()
    def clearSky(self) -> None:  # noqa: N802 (QML 슬롯) — 전 레이어 해제
        self._clear_sky()

    @Slot(int)
    def clearLayer(self, layer) -> None:  # noqa: N802 (QML 슬롯) — 한 레이어만 해제
        layer = int(layer)
        if 0 <= layer < 5:
            self._layer_seq[layer] += 1      # 해당 레이어 in-flight 워커만 무효화(전역 아님)
            self._layer_keys[layer] = []
            self._layer_strokes[layer] = []
            self._layer_mask_strokes[layer] = []
            self._layer_automask[layer] = None
            self._layer_automask_valid[layer] = False
            self._stroke_patches[layer] = []
            self._set_layer_mask(layer, None)

    def _clear_sky(self) -> None:
        """전 레이어 마스크 해제(1x1 검정). 캐시(_seg_probs)는 유지 — 같은 이미지 재선택은 재추론 불필요.
        레이어별 seq 증가 — 진행 중이던 워커 결과가 해제 직후 도착해 되살리는 레이스 방지."""
        for i in range(5):
            self._layer_seq[i] += 1
            self._layer_keys[i] = []
            self._layer_strokes[i] = []
            self._layer_mask_strokes[i] = []
            self._layer_automask[i] = None
            self._layer_automask_valid[i] = False
            self._stroke_patches[i] = []
            self._set_layer_mask(i, None)

    def _get_layer_urls(self):
        return list(self._layer_urls)

    layerMaskUrls = Property("QVariantList", _get_layer_urls, notify=skyMaskChanged)

    def _get_layer_has_mask(self):
        return [m is not None for m in self._layer_masks]

    # 레이어별 실제 마스크 존재 — 셰이더 hasMask 게이트(invert 를 마스크 없을 때 전체 적용 방지).
    layerHasMask = Property("QVariantList", _get_layer_has_mask, notify=skyMaskChanged)

    def _get_has_sky_mask(self) -> bool:
        return any(m is not None for m in self._layer_masks)

    hasSkyMask = Property(bool, _get_has_sky_mask, notify=skyMaskChanged)   # 아무 레이어나 마스크 있음

    def _get_mask_settled(self) -> bool:
        """이 이미지의 마스크 요청이 전부 끝났는지 — **결과가 마스크 없음이어도 True**.
        배치 export 가 '아직 안 옴'과 '없는 게 결과'를 구분하는 데 쓴다. 없으면 얼굴 없는
        사진에 Face 부위가 선택돼 있을 때 hasSkyMask 가 영영 False 라 장당 20초를 기다린다."""
        return self._sky_pending == 0 and self._mask_ran

    maskSettled = Property(bool, _get_mask_settled, notify=skyBusyChanged)

    def _get_sky_busy(self) -> bool:
        return self._sky_busy

    skyBusy = Property(bool, _get_sky_busy, notify=skyBusyChanged)

    @Slot(str)
    def _on_seg_status(self, s: str) -> None:
        """워커 스레드 → 메인 스레드: 세그 상태 문구 갱신(모델 다운로드 중 등)."""
        if s != self._seg_status:
            self._seg_status = s
            self.segStatusChanged.emit()

    @Slot(object)
    def _on_seg_dl(self, payload) -> None:
        """워커 스레드 → 메인 스레드: 마스킹 모델 다운로드 (진행중, 진행률) 갱신."""
        downloading, frac = payload
        was = self._seg_downloading
        self._seg_downloading = bool(downloading)
        self._seg_dl_prog = float(frac)
        self.segStatusChanged.emit()
        if was and not self._seg_downloading:
            self.modelsChanged.emit()   # 기능 첫 사용으로 받힌 것도 AI Models 목록에 반영

    def _get_seg_status(self) -> str:
        return self._seg_status

    def _get_seg_downloading(self) -> bool:
        return self._seg_downloading

    def _get_seg_dl_prog(self) -> float:
        return self._seg_dl_prog

    segStatus = Property(str, _get_seg_status, notify=segStatusChanged)
    segDownloading = Property(bool, _get_seg_downloading, notify=segStatusChanged)
    segDlProgress = Property(float, _get_seg_dl_prog, notify=segStatusChanged)

    def _get_look_defaults(self):
        import presets
        return dict(presets.LOOK_DEFAULTS)

    # 룩 키의 **공장 기본값 단일 진실원**(presets.LOOK_DEFAULTS). QML applyEdits 의 폴백과
    # 룩 지문 채우기가 같은 표를 봐야 배지가 정직해진다 — 이유는 그쪽 주석.
    lookDefaults = Property("QVariantMap", _get_look_defaults, constant=True)

    def _get_shortcut_help(self):
        import shortcuts
        return [{"title": g, "rows": [{"keys": k, "desc": d} for k, _t, d in rows]}
                for g, rows in shortcuts.KEYS]

    def _get_mouse_help(self):
        import shortcuts
        return [{"title": g, "rows": [{"keys": k, "desc": d} for k, d in rows]}
                for g, rows in shortcuts.MOUSE]

    # 단축키/마우스 조작 목록(`shortcuts.py` 단일 진실원) → `?`·F1 오버레이가 그린다.
    # ⚠️목록을 QML 에 옮겨 적지 말 것 — `python shortcuts.py` 가 이 표와 실제 `Shortcut{}`
    #   선언을 대조하므로, 표를 우회하면 검사가 무력해진다.
    shortcutHelp = Property("QVariantList", _get_shortcut_help, constant=True)
    mouseHelp = Property("QVariantList", _get_mouse_help, constant=True)

    def _get_adjust_coeffs(self):
        import coeffs
        return coeffs.as_qml_dict()

    # 현상 계수(coeffs.py 단일 진실원) → 셰이더 uniform 주입. 값 바꾸면 프리뷰=export 동시 반영.
    adjustCoeffs = Property("QVariantMap", _get_adjust_coeffs, constant=True)

    def _get_film_sims(self):
        return available_film_sims()

    # 사용 가능한 필름시뮬 목록(번들 luts/*.cube + 사용자 LUT) → QML 이 콤보/simKeys/구분선 구성.
    # ⚠️constant 가 아니다 — Add/Remove LUT 이 **재시작 없이** 반영돼야 한다.
    filmSims = Property("QVariantList", _get_film_sims, notify=filmSimsChanged)

    @Slot(str, result=int)
    def lutSizeFor(self, key: str) -> int:      # noqa: N802 (QML 슬롯)
        """그 LUT 의 한 변 N → 셰이더 uniform `lutSize`. LUT 마다 N 이 다를 수 있다.
        ⚠️QML 은 이 값을 **텍스처 소스와 같은 식**에서 파생시켜야 한다 — 둘이 한 프레임
        어긋나면 그 프레임의 색이 깨진다."""
        return LUT_PROVIDER.size_of(key) if LUT_PROVIDER is not None else 0

    @Slot(QUrl, result="QVariantMap")
    def addUserLut(self, url: QUrl) -> dict:    # noqa: N802 (QML 슬롯)
        """사용자가 고른 .cube 를 사용자 폴더로 복사·검증하고 목록에 넣는다(재시작 불필요).
        반환 `{key, error, note}` — QML 이 error/note 를 배너로 보여준다."""
        import lut as lut_mod
        res = lut_mod.add_user_lut(url.toLocalFile() if url.isLocalFile() else str(url))
        if res["key"]:
            self._lut_cache.pop(res["key"], None)   # 같은 이름을 덮어썼으면 옛 파싱을 버린다
            if LUT_PROVIDER is not None and not LUT_PROVIDER.load_one(
                    lut_mod.lut_path(res["key"]), res["key"]):
                # 파서는 통과했는데 아틀라스 생성이 실패한 경우 — 목록에 남기지 않는다.
                # ⚠️**덮어쓴 경우엔 파일을 지우지 않는다** — 사용자가 이미 쓰고 있던 LUT 을
                #   없애게 된다(그 키를 저장한 사진·레시피가 통째로 룩을 잃는다). 그때는 새
                #   파일이 디스크에 남고 프로바이더는 옛 아틀라스를 들고 있으므로, 사용자가
                #   멀쩡한 파일로 다시 가져오면 정리된다.
                if not res.get("replaced"):
                    lut_mod.remove_user_lut(res["key"])
                return {"key": "", "note": "",
                        "error": "Could not build a GPU texture from that LUT."}
            self.filmSimsChanged.emit()
        return res

    @Slot(str, result=bool)
    def reconcileLut(self, key: str) -> bool:   # noqa: N802 (QML 슬롯)
        """선택된 LUT 의 파일이 아직 있는지 확인하고, **없으면 목록에서 내린다**.

        앱이 모르게 파일이 지워질 수 있다(탐색기에서 삭제·폴더 정리). 그러면 목록에는 남아 있고
        프로바이더는 시작 시 구워둔 아틀라스를 메모리에 들고 있어서 **프리뷰만 계속 그 룩으로
        그려지는데 CPU export 는 파일이 없어 실패**한다 — 프리뷰≠export 다.
        ⚠️폴더 감시(watcher)는 두지 않는다. 이 확인은 **선택이 바뀌는 시점에만** 도는
        `is_file()` 한 번이라 슬라이더 드래그 같은 프레임 경로에 걸리지 않는다(사용자 폰트가
        `has_font` 를 매 프레임 부르다 mkdir 로 문제가 됐던 것과 같은 이유로 경로를 고른 것).

        반환: True = 사라져서 내렸음(QML 이 배너를 켜고 None 으로 떨어진다)."""
        import lut as lut_mod
        if not key or key == "identity":
            return False
        try:
            if lut_mod.lut_path(key, LUTS_DIR).is_file():
                return False
        except Exception:
            return False        # 경로를 못 만들면 판단하지 않는다(기존 거동 유지)
        if LUT_PROVIDER is not None:
            LUT_PROVIDER.drop_one(key)
        self._lut_cache.pop(key, None)
        print(f"[lut] 파일이 사라져 목록에서 내림: {key}")
        # 목록이 바뀌면 ComboBox 가 인덱스를 0 으로 되돌리고 그 핸들러가 다시 돌지만,
        # 그때 키는 'identity' 라 여기서 즉시 False 로 끝난다(재귀 종료).
        self.filmSimsChanged.emit()
        return True

    @Slot(str, result=bool)
    def removeUserLut(self, key: str) -> bool:  # noqa: N802 (QML 슬롯)
        """추가한 LUT 을 지운다. 그 LUT 을 쓰던 사진은 목록에 없는 키가 되므로 경고와 함께
        None(필름시뮬 미적용)으로 열린다 — 번들에서 빠진 ARR 흑백 LUT 과 같은 경로."""
        import lut as lut_mod
        if not lut_mod.remove_user_lut(key):
            return False
        if LUT_PROVIDER is not None:
            LUT_PROVIDER.drop_one(key)
        self._lut_cache.pop(key, None)
        self.filmSimsChanged.emit()
        return True


    @Slot(bool)
    def setLensCorrection(self, on: bool) -> None:  # noqa: N802 (QML 슬롯)
        """렌즈 보정 on/off (RAF 내장 샷별 프로파일, 재디코딩)."""
        if self._lens == on:
            return
        self._lens = on
        self.lensChanged.emit()
        if self._path:
            self._render()

    def _get_lens(self) -> bool:
        return self._lens

    lensCorrection = Property(bool, _get_lens, notify=lensChanged)

    @Slot(bool)
    def setAutoExposure(self, on: bool) -> None:  # noqa: N802 (QML 슬롯)
        """자동노출 on/off. 끄면 톤 가공 없는 선형 출발점(⚠️타 현상기의 '선형' 옵션과 같은
        자리인지는 **미검증** — `docs/tone_pipeline.md` 참조. 등가로 설명하지 말 것).

        ⚠️**재디코드가 아니다** — 디코드는 항상 자동노출을 적용하고, 끄기는 셰이더/pipeline 의
        노출 지수에서 −log2(게인) 을 빼는 것으로 처리한다(곱셈이라 수학적으로 동일, 토글 즉시
        반응). 재디코드로 만들었다가 2~4초가 걸려 바꿨다.
        ⚠️사진별 설정이고 **기본은 켬** — 후지 raw 는 하이라이트를 지키느라 낮게 노출돼 있어
        끄면 0.9~2.2스톱 어둡게 열린다(실측, 렌즈 보정 포함 경로). 알고 쓰는 옵션이다."""
        if self._auto_exp == on:
            return
        self._auto_exp = on
        self.autoExpChanged.emit()
        self._update_sim_ev()      # 필름시뮬 보정은 베이스 밝기 기준이라 오프셋이 바뀌면 다시 푼다

    def _get_auto_exp(self) -> bool:
        return self._auto_exp

    def _get_auto_ev(self) -> float:
        """화면에 표시할 '실제로 적용 중인' 자동노출(EV). 끄면 0(오프셋이 상쇄한다)."""
        return self._auto_ev if self._auto_exp else 0.0

    def _get_auto_off_ev(self) -> float:
        """자동노출을 끌 때 노출 지수에 더할 오프셋(= −log2(게인)). 켜져 있으면 0.

        디코드는 항상 게인을 적용하므로, 끄기는 여기서 도로 빼는 것으로 구현한다."""
        return 0.0 if self._auto_exp else -self._auto_ev

    autoExposure = Property(bool, _get_auto_exp, notify=autoExpChanged)
    autoExposureEV = Property(float, _get_auto_ev, notify=autoExpChanged)

    def _get_auto_decode_ev(self) -> float:
        """디코드에 **항상** 적용된 자동노출(EV) — 토글과 무관하다.

        ★Develop 애니메이션의 `autoExpEV` 중립값(= −이 값)이다. 표시용 `autoExposureEV` 는
          토글이 꺼지면 0 을 돌려주므로 그걸 쓰면 **끈 사진에서 단계가 거꾸로 돈다**
          (중립 0 → 실제 −게인 이라 자동노출 단계가 어두워지고, 그 앞 단계들은 게인이 남아
          그만큼 밝다). 검토에서 잡힌 실제 버그다.
        """
        return float(self._auto_ev)

    autoExposureDecodeEV = Property(float, _get_auto_decode_ev, notify=autoExpChanged)
    # 셰이더 uniform(pipe/pipeFull autoExpEV). export 는 render_full 이 자체 계산한다.
    autoExposureOffsetEV = Property(float, _get_auto_off_ev, notify=autoExpChanged)

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=busyChanged)

    def _render(self) -> None:
        """디코딩(+렌즈 보정)을 백그라운드 스레드에서 수행. UI 안 멈추고 스피너 표시."""
        if not self._path:
            return
        self._render_seq += 1
        seq = self._render_seq
        if not self._busy:
            self._busy = True
            self.busyChanged.emit()
        args = (seq, self._path, self._lens)
        threading.Thread(target=self._render_worker, args=args, daemon=True).start()

    def _render_worker(self, seq, path, lens_on) -> None:
        err = ""
        try:
            # 일반 이미지(JPG/PNG/TIFF)는 display-referred 어댑터로 — 반환 계약은 동일한 7-튜플.
            res = (image_loader.load_proxy(path, lens_correct=lens_on)
                   if image_loader.is_display_image(path)
                   else load_proxy(path, lens_correct=lens_on))
        except Exception as exc:
            res = None
            err = self._decode_error_message(exc, path)
            print(f"[load] 실패: {type(exc).__name__}: {exc}")
        self._renderReady.emit((seq, res, err))   # 메인 스레드로 큐잉

    @staticmethod
    def _decode_error_message(exc, path: str = "") -> str:
        """디코드 예외 → 사용자 안내 문구. LibRaw 가 못 여는 포맷/기종은 '미지원'으로 구분."""
        # ⚠️image_loader None 가드 필수 — 이 함수는 _render_worker 의 except 안에서 돌기 때문에
        #   여기서 AttributeError 가 나면 _renderReady 를 못 emit 하고 busy 가 영구 True 로 남는다.
        if path and image_loader is not None and image_loader.is_display_image(path):
            return "Cannot open this image (corrupt, or an unsupported variant)."
        try:
            import rawpy
            if isinstance(exc, rawpy.LibRawFileUnsupportedError):
                return "Unsupported RAW format or camera — this build's LibRaw can't decode it."
            if isinstance(exc, getattr(rawpy, "LibRawIOError", ())):
                return "Cannot read file (missing, unreadable, or truncated)."
        except Exception:
            pass
        return "Cannot open this file (corrupt or unsupported RAW)."

    @Slot(object)
    def _on_render_ready(self, payload) -> None:
        seq, res, err = payload
        if seq != self._render_seq:
            return                            # 더 최신 렌더 진행 중 -> 폐기(busy 유지)
        self._busy = False
        self.busyChanged.emit()
        if res is None:
            # 디코드 실패(미지원/손상 RAW) — 크래시 없이 사용자에게 안내(이전 이미지는 유지).
            self._set_load_error(err or "Cannot open this file (unsupported or corrupt RAW).")
            return
        self._set_load_error("")
        img, as_shot, as_shot_tint, cam, ref, cam2srgb, auto_gain = res
        if self._kelvin is None:
            self._kelvin = as_shot          # as-shot 으로 디코딩됨 -> 현재값 동기화
            self._tint = as_shot_tint       # as-shot tint 도 함께 동기화(새 파일)
        self._cam = cam
        self._ref = ref
        self._cam2srgb = cam2srgb
        # 디코드가 돌려준 자동노출 게인에서 둘을 유도한다: 하이라이트 디새추 게이트(셰이더
        # uniform)와 화면에 보여줄 EV. ⚠️자동노출은 **보이지 않는 보정**이라 Exposure 가 0.00
        # 인데 뒤에서 +2EV 가 걸려 있다 — 그게 "왜 내가 찍은 것보다 밝지"의 정체였다.
        import math
        import raw_loader                     # 지연 임포트 모듈(_load_heavy_modules 참조)
        _clip = raw_loader.clip_level(float(auto_gain))
        if abs(_clip - self._clip_level) > 1e-6:
            self._clip_level = _clip
            self.clipLevelChanged.emit()
        # ⚠️**무조건 emit** 한다 — `autoExposureOffsetEV` 는 `_auto_ev`(디코드)와 `_auto_exp`
        #   (사이드카, `_load` 에서 조용히 선설정)의 함수다. 값이 바뀐 쪽만 보고 걸렀더니,
        #   **같은 게인**의 다른 사진으로 넘어가며 토글만 달라지는 경우(연사 컷) 신호가 안 나가
        #   프리뷰가 이전 오프셋을 그대로 쓰고 export 와 갈라졌다.
        self._auto_ev = float(math.log2(max(float(auto_gain), 1e-6)))
        self.autoExpChanged.emit()
        if as_shot != self._asshot or as_shot_tint != self._asshot_tint:
            self._asshot = as_shot
            self._asshot_tint = as_shot_tint
            self.asShotKelvinChanged.emit()
        self._provider.set_image(img)
        self._counter += 1
        # 쿼리스트링으로 캐시 무력화 -> Image 가 새로 로드됨
        self._url = f"image://raw/photo?v={self._counter}"
        self.imageChanged.emit()
        self.wbBaked.emit()              # baked kelvin/tint/matrix 갱신 알림
        self._proxy_w, self._proxy_h = img.width(), img.height()
        self._proxy_img = img            # 세그 입력 디코드용(display sRGB 변환 base)
        self._seg_probs = None           # 프록시 바뀜 → 추론 캐시 무효화(재추론 필요)
        self._seg_guide = self._seg_size = self._seg_rgb8 = None
        self._depth_map = None           # 거리 맵도 프록시 좌표계 기준 → 같이 무효화
        self._layer_segmask = [None] * 5  # Scene∪Face 성분 캐시도 프록시 기준
        self._layer_segkeys = [None] * 5
        # 얼굴 검출/파싱도 프록시 좌표계 기준 → 렌즈 보정·기하 변경이면 반드시 재실행
        self._face_parsed = None
        self._face_dets = None
        self._face_scanned = False
        self._face_thumb_urls = []
        if self._face_provider is not None:
            self._face_provider.clear()
        self.facesChanged.emit()
        self._mask_ran = False           # 새 프록시 → 마스크 요청 결과 '아직 없음'
        self.skyBusyChanged.emit()       # maskSettled 의 notify — 값은 그대로여도 재평가시킨다
        prev_layer_keys = [list(k) for k in self._layer_keys]   # 레이어별 선택 스냅샷(재정렬용)
        self._img_gen += 1               # 이미지 세대↑ → 이전 이미지의 진행 중 세그 워커 결과 무효화
        for _i in range(5):
            self._layer_automask[_i] = None       # 자동 마스크 캐시도 프록시 기준 → 무효
            self._layer_automask_valid[_i] = False
            self._stroke_patches[_i] = []         # 패치 스냅샷도 프록시 기준
            self._set_layer_mask(_i, None)   # 새 프록시 → 이전 마스크 무효(곧 재생성/복원)
        # 디헤이즈 물리(DCP): 이전 추정 무효화(준비 전엔 conf=0 → 톤모델 폴백) 후 백그라운드 재추정.
        self._haze_seq += 1
        self._haze_t, self._haze_A, self._haze_conf = None, [1.0, 1.0, 1.0], 0.0
        if self._haze_provider is not None:
            self._haze_provider.clear()
        self._haze_counter += 1
        self._haze_url = f"image://haze/h?v={self._haze_counter}"
        self.hazeChanged.emit()
        threading.Thread(target=self._haze_worker, args=(self._haze_seq,), daemon=True).start()
        # 휘도 NR 베이스: 이전 텍스처 무효화(준비 전엔 nrOn=0 → 휘도 NR 무동작) 후 재계산.
        # AI 디노이즈(파일별 편집값)는 fresh load 에서만 끔 — 사이드카에 aiNr 이 저장돼 있으면
        # QML applyEdits 가 setAiNr(true) 로 다시 켠다. 재디코딩(WB 커밋·렌즈 토글 등)은 편집
        # 상태 유지 → AI 도 유지하고 새 프록시로 재계산(마스크 재생성과 동형).
        if self._fresh_load and (self._ai_nr or self._ai_status):
            self._ai_nr = False
            self._ai_status = ""
            self.aiNrChanged.emit()
        self._nr_seq += 1
        self._nr_ready = False
        self._nr_chroma = False
        if self._nr_provider is not None:
            self._nr_provider.clear()
        self._nr_counter += 1
        self._nr_url = f"image://nrbase/n?v={self._nr_counter}"
        self.nrChanged.emit()
        # 가이디드는 항상 먼저(1초 내 임시 베이스). AI 유지 중이면 이어서 AI 워커 — 완료 시
        # 같은 seq 로 나중에 emit 되므로 베이스만 교체된다(가이디드가 훨씬 먼저 끝남).
        threading.Thread(target=self._nr_worker, args=(self._nr_seq,), daemon=True).start()
        if self._ai_nr:
            threading.Thread(target=self._ai_nr_worker, args=(self._nr_seq,), daemon=True).start()
        # 미스트 산란 필드: 새 프록시 → 무효화 후 현재 (Radius, Highlight) 로 재계산.
        # 준비 전에는 mistOn=0 이라 미스트가 무동작(NR 과 동형).
        self._mist_seq += 1
        self._mist_ready = False
        self._mist_field = None          # 새 프록시 → 이전 필드 무효
        self._mist_mean = [0.0, 0.0, 0.0]
        if self._mist_provider is not None:
            self._mist_provider.clear()
        self._mist_counter += 1
        self._mist_urls = [f"image://mist/{i}?v={self._mist_counter}" for i in range(3)]
        self.mistChanged.emit()
        # ⚠️**fresh 로드에서는 여기서 시작하지 않는다.** 곧 QML applyEdits/resetAllEdits 가
        #   사이드카 값으로 setMistAmount + requestMistField 를 부른다. 여기서 미리 띄우면
        #   이전 이미지의 키로 계산하고 그 결과는 항상 버려져 **로드당 두 번** 돌았다.
        #   비-fresh 재디코딩(WB 커밋·렌즈 토글)은 편집 복원을 안 거치므로 여기서 시작한다.
        if not self._fresh_load:
            self._maybe_start_mist()
        # 비-fresh 재디코딩(렌즈 보정·WB 커밋 등)은 editsReady(복원)를 안 거친다 → 활성 마스크가
        # 있었으면 같은 클래스로 새 프록시에 재생성(렌즈 보정은 기하 변경 → 정렬 위해 재생성 필수).
        # fresh load 는 applyEdits 가 저장본에서 복원하므로 여기선 건드리지 않는다.
        if not self._fresh_load:         # 비-fresh 재디코딩 → 각 레이어 마스크 재정렬(렌즈/기하 변경)
            for _i in range(5):
                if prev_layer_keys[_i] or self._layer_strokes[_i]:   # 획만 있는 레이어도 재정렬
                    self.setMaskClasses(_i, prev_layer_keys[_i])
        else:
            self._layer_keys = [[] for _ in range(5)]
            self._layer_strokes = [[] for _ in range(5)]     # 복원은 applySkyEdits→setStrokes
            self._layer_mask_strokes = [[] for _ in range(5)]
        self._update_stamp_layer()       # 날짜 스탬프 프리뷰 레이어(프록시, 우하단)
        self._compute_histogram(img)     # 톤커브 배경 히스토그램(디코딩된 프록시)
        self._update_sim_ev()            # 새 표본(_proxy_small) 기준 필름시뮬 보정 노출
        print(f"[load] {self._path}  ({img.width()}x{img.height()})  "
              f"kelvin={self._kelvin} tint={self._tint:.2f} as_shot={as_shot}")
        # 새 파일의 첫 디코딩이 끝났을 때만 복원 트리거(WB 커밋 등 재디코딩에는 발화 안 함).
        # 이 시점에 UI 가 이 파일을 반영하게 되므로 _ui_path 갱신(저장 귀속 기준).
        if self._fresh_load:
            self._fresh_load = False
            self._ui_path = self._path
            # ★`rawPeekAvailable` 은 `_ui_path` 로 판정하므로 **여기서** 알려야 한다
            #   (위 imageChanged 시점엔 아직 이전 사진 경로다 — 그래서 한 장 뒤처졌다).
            self.rawPeekAvailChanged.emit()
            # 새 파일의 날짜/회전을 QML 에 알린다. ⚠️**여기여야 한다** — ①디코드 전(EXIF 단계)에
            # 알리면 아직 이전 사진의 편집 맥락이라 **빠져나온 사진이 edited 가 된다**(3645행 주석)
            # ②재디코딩(렌즈보정 토글 등)에는 editsReady 가 안 나므로, 밖에 두면 이 알림이 예약한
            # 자동저장을 아무도 취소하지 않아 500ms 뒤 불필요한 저장이 한 번 더 돈다.
            # `_ui_path` 확정 **뒤**, `editsReady` **직전**이라 예약돼도 곧바로 취소된다.
            self.stampChanged.emit()
            self.gpsChanged.emit()   # 지오태그도 같은 이유로 여기서(위 ①② 그대로 적용된다)
            self.editsReady.emit()
            # ⚠️직전 사진의 실패 사유(`Failed: …`)를 물고 가지 않는다 — 이 상태가 캡션 라벨의
            #   **색**을 정하는데 라벨 텍스트는 캡션이 있으면 캡션이라, 지우지 않으면
            #   **정상 캡션이 빨간색**으로 나온다(사용자 보고 → 재현, 2026-09-03).
            self._caption_status = ""
            self.captionChanged.emit()   # _ui_path 확정 후 캡션 재평가(사이드카 저장분 표시)
            self._maybe_auto_caption()   # 저장된 캡션 없으면 자동 생성(하단 캡션 패널)

    def _get_url(self) -> str:
        return self._url

    def _get_path(self) -> str:
        return self._path

    def _get_asshot(self) -> int:
        return self._asshot

    def _get_asshot_tint(self) -> float:
        return self._asshot_tint

    def _get_cam(self) -> list:
        return self._cam

    def _get_ref(self) -> list:
        return self._ref

    def _get_cam2srgb(self) -> list:
        return self._cam2srgb

    def _get_baked_k(self) -> float:
        return float(wb.TREF)    # 프록시는 항상 TREF daylight 베이크(셰이더가 상대게인)

    def _get_baked_t(self) -> float:
        return 0.0

    def _get_is_display_image(self) -> bool:
        """현재 사진이 일반 이미지(JPG/PNG/TIFF)인가 — 셰이더 hlDesat 게이트용.
        RAW 는 센서 클립 색끼를 지워야 하지만 display-referred 소스는 그게 '정상 색'이다."""
        return bool(self._path) and image_loader is not None \
            and image_loader.is_display_image(self._path)

    exportExt = Property(str, _get_export_ext, notify=exportExtChanged)
    imageUrl = Property(str, _get_url, notify=imageChanged)
    imagePath = Property(str, _get_path, notify=imageChanged)
    isDisplayImage = Property(bool, _get_is_display_image, notify=imageChanged)
    caption = Property(str, _get_caption, notify=captionChanged)
    hashtags = Property(str, _get_hashtags, notify=captionChanged)
    captionBusy = Property(bool, _get_caption_busy, notify=captionChanged)
    captionStatus = Property(str, _get_caption_status, notify=captionChanged)
    captionLevel = Property(int, _get_caption_level, notify=captionChanged)
    captionModelReady = Property(bool, _get_caption_model_ready, notify=captionChanged)
    asShotKelvin = Property(int, _get_asshot, notify=asShotKelvinChanged)
    asShotTint = Property(float, _get_asshot_tint, notify=asShotKelvinChanged)
    camMatrix = Property("QVariantList", _get_cam, notify=wbBaked)
    daylightRef = Property("QVariantList", _get_ref, notify=wbBaked)
    camToSrgb = Property("QVariantList", _get_cam2srgb, notify=wbBaked)
    bakedKelvin = Property(float, _get_baked_k, notify=wbBaked)
    bakedTint = Property(float, _get_baked_t, notify=wbBaked)


def ensure_luts() -> None:
    """luts/ 에 .cube 가 없으면 근사 LUT 를 생성."""
    if getattr(sys, "frozen", False):
        return  # frozen: .cube 동봉, 설치 폴더에 절대 쓰지 않음
    if not LUTS_DIR.exists() or not any(LUTS_DIR.glob("*.cube")):
        make_luts.generate_all()


def _load_heavy_modules() -> None:
    """numpy/scipy/rawpy 등을 끌어오는 무거운 모듈을 splash 표시 *후* 로드한다.

    이 임포트들을 모듈 최상단에 두면 splash 가 뜨기 전에 다 로드돼 대기 구간이
    길어진다(특히 콜드 스타트). splash 가 보인 뒤로 미뤄 체감 시작 시간을 줄인다.
    여기서 module-global 로 바인딩하므로 이후 Controller/provider 들이 그대로 참조한다."""
    global date_stamp, make_luts, read_shooting_info, read_orientation, _read_embedded_jpeg
    global embedded_preview_jpeg
    global wb, atlas_qimage, load_cube, PROXY_HEADROOM, load_full, load_proxy
    global image_loader, exif_info
    import date_stamp, exif_info, image_loader, make_luts, wb        # noqa: E401
    from exif_info import (read_shooting_info, read_orientation, _read_embedded_jpeg,
                           embedded_preview_jpeg)
    from lut import atlas_qimage, load_cube
    from raw_loader import PROXY_HEADROOM, load_full, load_proxy


def _show_splash(app):
    """콜드 스타트 동안 보일 가벼운 스플래시 창을 띄워 즉시 그린다.

    QQuickView 로 Splash.qml 을 로드 → 화면 중앙에 frameless 로 표시.
    이 첫 GPU 창 생성이 RHI(D3D11) 디바이스 초기화를 대부분 떠안으므로,
    뒤따르는 메인 창은 더 빨리 뜬다. processEvents 로 즉시 페인트한다.
    실패해도 앱 동작에는 영향 없도록 None 반환."""
    try:
        from PySide6.QtQuick import QQuickView
        view = QQuickView()
        view.setFlags(Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        view.setResizeMode(QQuickView.ResizeMode.SizeViewToRootObject)
        view.setColor(Qt.GlobalColor.transparent)
        view.rootContext().setContextProperty("appVersion", APP_VERSION)   # setSource 전에 바인딩
        view.setSource(QUrl.fromLocalFile(str(BASE / "ui" / "Splash.qml")))
        scr = app.primaryScreen().geometry()
        view.setPosition((scr.width() - view.width()) // 2,
                         (scr.height() - view.height()) // 2)
        view.show()
        app.processEvents()    # 이벤트 루프 시작 전이라 강제로 한 번 그린다
        return view
    except Exception as exc:
        print(f"[splash] 표시 실패(무시): {exc}")
        return None


def _close_splash_when_ready(root, splash) -> None:
    """메인 창의 첫 프레임이 화면에 올라오면(frameSwapped) 스플래시를 닫는다."""
    if splash is None:
        return
    done = {"v": False}

    def _close():
        if done["v"]:
            return
        done["v"] = True
        splash.close()
        splash.deleteLater()

    # frameSwapped 는 매 프레임 발생 -> 가드로 1회만 닫는다.
    root.frameSwapped.connect(_close)
    # 혹시 frameSwapped 가 안 와도(드문 경우) 폴백으로 닫기.
    QTimer.singleShot(3000, _close)


def apply_dark_titlebar(window) -> None:
    """Windows OS 타이틀바를 다크 모드로(DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        v = ctypes.c_int(1)
        # 20 = Win10 2004+/Win11, 19 = 이전 빌드 (둘 다 시도)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception as exc:
        print(f"[theme] 다크 타이틀바 적용 실패: {exc}")


class _ClickOutsideFocusFilter(QObject):
    """날짜 입력칸(stampField) 편집 중, 필드 바깥을 마우스로 누르면 포커스를 해제한다
    (단축키 _typing 가드 복귀). 앱 레벨 이벤트 필터라 프리뷰 팬/줌·Compare 버튼·슬라이더
    처럼 QML MouseArea 가 클릭을 exclusive grab 으로 가로채는 곳도 press 를 먼저 관찰한다.
    이벤트는 소비하지 않아(return False) 커서/전달에 간섭하지 않는다 — 필드 위 HoverHandler
    의 I-beam 커서와 정상 클릭이 그대로 유지된다. 필드가 없거나 미포커스면 완전 무동작."""

    def __init__(self, field, parent=None):
        super().__init__(parent)
        self._field = field

    def eventFilter(self, watched, event):
        f = self._field
        if f is not None and event.type() == QEvent.Type.MouseButtonPress:
            try:
                if f.property("activeFocus"):
                    tl = f.mapToGlobal(QPointF(0.0, 0.0))
                    w = float(f.property("width") or 0.0)
                    h = float(f.property("height") or 0.0)
                    gp = event.globalPosition()
                    inside = (tl.x() <= gp.x() <= tl.x() + w
                              and tl.y() <= gp.y() <= tl.y() + h)
                    if not inside:
                        f.setProperty("focus", False)
            except Exception:
                pass
        return False


class _FreezeWatchdog:
    """GUI 스레드 정지('응답 없음') 진단용 워치독 — 계측 전용, 동작 개입 없음.

    메인 스레드 QTimer 하트비트가 STALL_SEC 이상 끊기면 데몬 스레드가 **전 스레드
    파이썬 스택**을 로그 파일(사용자 데이터 폴더 freeze_dumps.log)에 남긴다(faulthandler).
    정지가 계속되면 REDUMP_SEC 간격으로 추가 덤프(스택이 움직이면 라이브락/느린 작업,
    안 움직이면 교착) — 정지 1회당 MAX_DUMPS 회 상한. 회복되면 지속 시간을 기록한다.

    한계(그 자체가 판정 정보다):
    - 메인 스레드 스택이 app.exec() 안이면 Qt 네이티브(렌더/드라이버)에서 멈춘 것.
    - 어떤 스레드가 GIL 을 쥔 채 네이티브에서 멈추면 덤프 자체가 안 남는다 —
      그 경우는 외부에서 `py-spy dump --pid <PID> --native` 로 잡아야 한다.
    오탐 가드: 시스템 절전 복귀(워치독 자신도 같이 잤던 경우)는 기준만 재설정하고 넘어간다.
    장시간 정상 블록(대형 폴더 스캔 등)이 오탐돼도 로그 한 건이 남을 뿐 무해하다."""

    STALL_SEC = 10.0     # 이 시간 이상 하트비트 없음 = 정지로 판정
    REDUMP_SEC = 30.0    # 정지 지속 시 추가 덤프 간격
    MAX_DUMPS = 5        # 정지 1회당 덤프 상한(로그 폭주 방지)
    _TICK = 1.0          # 하트비트/감시 주기

    def __init__(self, parent) -> None:
        import time
        import app_dirs
        self.log_path = app_dirs.user_data_path("freeze_dumps.log")
        try:                                   # 시작 시 1MB 넘으면 .1 로 밀어 새로 시작
            if os.path.getsize(self.log_path) > 1_000_000:
                os.replace(self.log_path, self.log_path + ".1")
        except OSError:
            pass
        self._beat = time.monotonic()
        t = QTimer(parent)
        t.setInterval(int(self._TICK * 1000))
        t.setTimerType(Qt.TimerType.CoarseTimer)   # 정밀도 불필요 — 절전/타이머 병합 허용
        t.timeout.connect(self._on_beat)
        t.start()
        self._timer = t                        # 참조 유지(GC 방지)
        threading.Thread(target=self._watch, daemon=True, name="freeze-watchdog").start()

    def _on_beat(self) -> None:
        import time
        self._beat = time.monotonic()          # float 대입은 GIL 하에서 원자적

    def _watch(self) -> None:
        import time
        last_wake = time.monotonic()
        stall_start = 0.0                      # 0 = 정지 아님
        next_dump = 0.0
        dumps = 0
        while True:
            time.sleep(self._TICK)
            now = time.monotonic()
            overslept = (now - last_wake) > self._TICK * 5
            last_wake = now
            if overslept:                      # 시스템 절전 등 — 우리도 같이 멈췄었다 → 오탐 방지
                self._beat = now
                continue
            gap = now - self._beat
            if gap < self.STALL_SEC:
                if stall_start:                # 회복 — 지속 시간 기록
                    self._append(f"----- recovered after ~{now - stall_start:.0f}s "
                                 f"({dumps} dump(s))\n")
                    stall_start = 0.0
                    dumps = 0
                continue
            if not stall_start:
                stall_start = self._beat
                next_dump = now
            if dumps < self.MAX_DUMPS and now >= next_dump:
                dumps += 1
                next_dump = now + self.REDUMP_SEC
                self._dump(gap, dumps)

    def _dump(self, gap: float, nth: int) -> None:
        import datetime
        import faulthandler
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n===== GUI stall ~{gap:.0f}s (dump {nth}/{self.MAX_DUMPS}) "
                        f"{ts}  v{APP_VERSION} =====\n")
                main_id = threading.main_thread().ident
                for th in threading.enumerate():   # faulthandler 는 ident 만 찍는다 → 이름 대조표
                    f.write(f"  0x{th.ident:x} = {th.name}"
                            f"{' [MAIN/GUI]' if th.ident == main_id else ''}\n")
                f.flush()                          # faulthandler 는 fd 직접 쓰기 — 순서 보장
                faulthandler.dump_traceback(file=f, all_threads=True)
            print(f"[watchdog] GUI {gap:.0f}s 무응답 - 스택 덤프: {self.log_path}")
        except Exception as exc:                   # 진단 실패가 앱을 흔들지 않게
            print(f"[watchdog] 덤프 실패(무시): {exc}")

    def _append(self, line: str) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def _print_banner() -> None:
    """터미널에서 실행할 때만 보이는 필름-스트립 시작 배너(개발자 이스터에그).
    GUI 더블클릭 실행 사용자는 콘솔이 없어 못 본다. 버전/PySide 정보는 디버깅에도 약간 유용.
    ⚠️ 어떤 경우에도 시작을 막지 않도록 전부 try/except — cp949 등 콘솔은 유니코드(●/☕) 인코딩 실패."""
    try:
        try:
            import PySide6
            pv = PySide6.__version__
        except Exception:
            pv = "?"
        py = "%d.%d.%d" % sys.version_info[:3]
        # 색은 터미널(tty)일 때만 — 파이프/리다이렉트나 VT 미지원이면 평문(이스케이프 깨짐 방지).
        color = sys.stdout.isatty()
        if color and os.name == "nt":               # Windows: VT 처리 활성화 시도
            try:
                import ctypes
                h = ctypes.windll.kernel32.GetStdHandle(-11)
                mode = ctypes.c_uint()
                color = bool(ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode))) and \
                    bool(ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004))
            except Exception:
                color = False
        amber = "\033[38;5;214m" if color else ""
        dim = "\033[2m" if color else ""
        rst = "\033[0m" if color else ""

        def emit(holes, sep, tail):
            sys.stdout.write(
                f"\n   {amber}{holes}{rst}\n"
                f"\n       {amber}F I L M   R A W S T E R Y{rst}"
                f"\n       {dim}slow-roasted light, developed into film{rst}\n"
                f"\n   {amber}{holes}{rst}\n"
                f"\n   {dim}v{APP_VERSION} {sep} PySide6 {pv} {sep} Python {py}{tail}{rst}\n\n")
            sys.stdout.flush()

        try:
            emit(" ".join(["●"] * 22), "·", "  ☕")          # 유니코드(필름 퍼포레이션 + 커피)
        except UnicodeEncodeError:
            emit(" ".join(["o"] * 22), "-", "")             # cp949 등 → ASCII 폴백
    except Exception:
        pass                                                 # 배너는 부가 기능 — 절대 시작을 막지 않음


# ---------- 단일 인스턴스 ----------
_SINGLE_INSTANCE_NAME = "FilmRawstery-single-instance"


def _acquire_single_instance(argv_path: str):
    """단일 인스턴스 확보. 반환 (proceed, server):
    - 이미 실행 중: 그 인스턴스에 '창 활성화(+열 경로)' 메시지를 보내고 (False, None) → 즉시 종료.
    - 첫 인스턴스: QLocalServer 를 점유하고 (True, server). 크래시 잔재(유닉스 소켓 파일)는
      removeServer 로 정리(Windows named pipe 는 프로세스와 함께 사라져 무해).
    - 서버 생성 실패(비정상 환경): 가드 없이 계속 실행(앱을 못 켜는 것보단 낫다)."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(_SINGLE_INSTANCE_NAME)
    if sock.waitForConnected(300):
        sock.write((argv_path or "").encode("utf-8") + b"\n")
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        # ⚠️ em-dash 등 cp949 비인코딩 문자 금지 — 콘솔 리다이렉트(cp949) 시 UnicodeEncodeError 로
        #    두 번째 인스턴스가 경로 전달 전에 죽는다(한글은 OK, '—' 가 문제였음).
        print("[single-instance] 이미 실행 중 -> 기존 창 활성화 요청 후 종료")
        return False, None
    QLocalServer.removeServer(_SINGLE_INSTANCE_NAME)
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_NAME):
        print(f"[single-instance] 서버 생성 실패(가드 없이 계속): {server.errorString()}")
        return True, None
    return True, server


def _serve_single_instance(server, root, controller) -> None:
    """두 번째 실행이 보낸 메시지 수신 → 창 복원/활성화 + (있으면) 전달된 경로 열기."""
    if server is None:
        return

    def on_conn():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def handle():
            data = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
            try:
                if root.windowStates() & Qt.WindowState.WindowMinimized:
                    root.showNormal()
                root.show()
                root.raise_()
                root.requestActivate()
                if data:
                    p = Path(data)
                    if p.is_file():
                        controller.loadPath(str(p))
                    elif p.is_dir():
                        controller.setFolderPath(str(p))
            except Exception as exc:      # 외부 메시지 처리 실패가 앱을 흔들지 않게
                print(f"[single-instance] 메시지 처리 실패(무시): {exc}")

        conn.readyRead.connect(handle)
        if conn.bytesAvailable():   # 클라이언트가 이미 쓰고 끊었으면 readyRead 를 놓침 → 즉시 처리
            handle()

    server.newConnection.connect(on_conn)


def main() -> int:
    _print_banner()
    if PREFER_HIGH_PERF_GPU:
        _prefer_high_performance_gpu()   # 외장 GPU 우선(다음 실행부터). Windows 한정.

    app = QGuiApplication(sys.argv)
    # Qt 기본 이미지 할당 한도는 256MB — 16bit 이미지 기준 32MP 를 넘으면 **예외 없이 null
    # QImage** 를 돌려준다(45MP TIFF 등). 큰 사진을 열 수 있게 올리되 **끄지는 않는다(0 금지)** —
    # 이 한도는 사용자가 고른 사진에만 걸리는 게 아니라 폴더를 열면 자동으로 도는 썸네일/호버
    # 프리뷰 경로에도 걸린다. 무제한이면 손상 파일이나 치수를 크게 선언한 파일 하나로 프로세스가
    # OOM 으로 죽는다(가드가 있으면 null QImage → placeholder 로 끝난다).
    QImageReader.setAllocationLimit(2048)
    # 창/작업표시줄 아이콘. exe 리소스 아이콘(spec icon=)과 같은 파일 — dev 실행에서도 동일하게.
    _icon = BASE / "icons" / "app.ico"
    if _icon.is_file():
        app.setWindowIcon(QIcon(str(_icon)))
    # 단일 인스턴스 가드 — splash/무거운 임포트 *전*에 확인해 두 번째 실행은 즉시 끝나게.
    proceed, si_server = _acquire_single_instance(sys.argv[1] if len(sys.argv) > 1 else "")
    if not proceed:
        return 0
    splash = _show_splash(app)   # 콜드 스타트 동안 표시(아래 무거운 초기화를 덮는다)

    _load_heavy_modules()        # numpy/scipy/rawpy 등은 splash 표시 후 로드(앞 구간 단축)
    ensure_shader()
    ensure_luts()
    import app_dirs
    app_dirs.migrate_legacy_async()   # legacy(구버전/저장소 models)→사용자 디렉터리 일괄 복사(백그라운드)
    date_stamp.font_family()   # 번들 DSEG7 폰트 1회 등록(메인 스레드)
    engine = QQmlApplicationEngine()

    provider = RawProvider()
    engine.addImageProvider("raw", provider)

    lut_provider = LutProvider()
    lut_provider.load_dir(LUTS_DIR)
    # 사용자가 추가한 .cube (설치 폴더가 아니라 사용자 데이터 폴더 — 업데이트에도 보존).
    import lut as _lut_mod
    lut_provider.load_dir(_lut_mod.user_luts_dir(), prefix=_lut_mod.USER_PREFIX)
    globals()["LUT_PROVIDER"] = lut_provider   # available_film_sims/lutSizeFor 가 본다
    engine.addImageProvider("lut", lut_provider)

    curve_provider = CurveProvider()
    engine.addImageProvider("curve", curve_provider)

    stamp_provider = StampProvider()
    engine.addImageProvider("stamp", stamp_provider)

    thumb_provider = ThumbProvider()
    engine.addImageProvider("thumb", thumb_provider)

    preview_provider = PreviewProvider()
    engine.addImageProvider("preview", preview_provider)

    wallthumb_provider = WallThumbProvider()
    engine.addImageProvider("wallthumb", wallthumb_provider)

    full_provider = RawFullProvider()
    engine.addImageProvider("rawfull", full_provider)

    nrfull_provider = NrFullProvider()
    engine.addImageProvider("nrfull", nrfull_provider)

    sky_provider = SkyMaskProvider()
    engine.addImageProvider("skymask", sky_provider)

    cm_provider = DisplayCmProvider()
    engine.addImageProvider("displaycm", cm_provider)

    haze_provider = HazeProvider()
    engine.addImageProvider("haze", haze_provider)

    nr_provider = NrBaseProvider()
    engine.addImageProvider("nrbase", nr_provider)

    face_provider = FaceThumbProvider()
    engine.addImageProvider("facethumb", face_provider)

    mist_provider = MistProvider()
    engine.addImageProvider("mist", mist_provider)

    peek_provider = RawPeekProvider()          # RAW Peek(디모자이크 이전 센서 뷰)
    engine.addImageProvider("rawpeek", peek_provider)

    controller = Controller(provider, curve_provider, stamp_provider, full_provider,
                            sky_provider, cm_provider, haze_provider, nr_provider,
                            face_provider, mist_provider, peek_provider, nrfull_provider)
    ctx = engine.rootContext()
    ctx.setContextProperty("controller", controller)
    # ⚠️예전엔 여기서 `lutN`(전역 하나)을 넘겼다 — LUT 마다 N 이 다를 수 있으므로
    #   QML 이 `controller.lutSizeFor(key)` 로 **키별 N** 을 읽는다(LutProvider 주석).

    engine.load(QUrl.fromLocalFile(str(BASE / "ui" / "Main.qml")))
    if not engine.rootObjects():
        return -1

    root = engine.rootObjects()[0]
    apply_dark_titlebar(root)                      # OS 타이틀바 다크 모드(Windows)
    # 날짜 입력칸 편집 중 필드 바깥 클릭 시 포커스 해제(단축키 복귀). controller 에 부모로
    # 물려 수명 유지. 필드는 objectName 으로 탐색(없으면 필터가 무동작).
    _stamp_field = root.findChild(QQuickItem, "stampField")
    app.installEventFilter(_ClickOutsideFocusFilter(_stamp_field, controller))
    _close_splash_when_ready(root, splash)         # 메인 창 첫 프레임에 스플래시 닫기
    _serve_single_instance(si_server, root, controller)   # 재실행 → 이 창 활성화(+경로 열기)
    # 배경화면 설정은 500ms 디바운스로 저장하므로, 직후 종료 시 미기록분이 남지 않게 flush.
    app.aboutToQuit.connect(controller._flush_wall_prefs)

    # 디스플레이 색관리(프리뷰 전용): 현재 모니터 ICC 로 CM LUT 생성 + 모니터 전환 시 재생성.
    def _refresh_cm(*_):
        scr = root.screen()
        controller.refreshDisplayCm(scr.name() if scr is not None else "")
        # 배경화면 패널의 'Match screen' 해상도용 — 창이 있는 화면의 실제 픽셀 크기.
        if scr is not None:
            g = scr.geometry()
            r = scr.devicePixelRatio()
            controller.setScreenSize(round(g.width() * r), round(g.height() * r))
    _refresh_cm()
    root.screenChanged.connect(_refresh_cm)

    # 업데이트 확인(1회): 시작 몇 초 뒤 백그라운드로 — 콜드 스타트/첫 디코드와 경합 안 하게 지연.
    QTimer.singleShot(4000, controller.startUpdateCheck)

    # freeze('응답 없음') 진단 워치독 — GUI 하트비트가 끊기면 전 스레드 스택을 로그로 남긴다.
    # 무거운 초기화가 다 끝난 여기서 시작(콜드 스타트 오탐 방지). 계측 전용, 동작 개입 없음.
    watchdog = _FreezeWatchdog(app)                                        # noqa: F841 (참조 유지)

    # 시작 동작: 인자로 파일/폴더를 주면 그대로 따르고, 인자가 없으면 **사진을 자동 로드하지 않고**
    # 폴더만 탐색기에 연다(사용자가 직접 더블클릭해 로드). 기본 폴더 = 개발 샘플 폴더(있으면) > Pictures.
    if len(sys.argv) > 1:
        start_path = sys.argv[1]
        if Path(start_path).is_file():
            controller.load(QUrl.fromLocalFile(start_path))   # load() 가 부모폴더도 scan
        elif Path(start_path).is_dir():
            controller.setFolderPath(start_path)
        else:
            print(f"[init] 시작 경로 없음: {start_path}")
            controller.setFolderPath(str(Path(start_path).parent))
    else:
        # 마지막 탐색 폴더 복원(종료 후에도 기억) > 개발 샘플 폴더 > Pictures.
        last = str(pref_get("explorer", "lastFolder", "") or "")
        if last and Path(last).is_dir():
            start_folder = last
        elif Path(DEFAULT_RAF).is_file():
            start_folder = str(Path(DEFAULT_RAF).parent)      # 개발 샘플 폴더만 열기(자동 로드 X)
        else:
            from PySide6.QtCore import QStandardPaths
            pics = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation)
            start_folder = pics or str(Path.home())
        controller.setFolderPath(start_folder)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
