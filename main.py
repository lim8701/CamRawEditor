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
from PySide6.QtGui import (QDesktopServices, QGuiApplication, QIcon, QImage,
                           QImageReader, QTransform)
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider, QQuickItem

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
SHADER_NAMES = ["adjust.frag", "blur.frag", "convert.frag", "displaycm.frag", "stamp.frag"]
LUTS_DIR = BASE / "luts"
APP_VERSION = "1.8.2"   # SemVer(MAJOR.MINOR.PATCH). 올릴 때 packaging/version_info.txt(exe 버전 리소스)도 수동으로 맞출 것


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

# ---------- 시스템 슬립 방지 (Windows SetThreadExecutionState) ----------
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def _set_keep_awake(on: bool) -> None:
    """export 류 긴 작업 동안 Windows 시스템 슬립 방지.
    ⚠️**ES_DISPLAY_REQUIRED 가 반드시 함께 있어야 한다** — 요즘 PC 는 대부분
    **Modern Standby(S0 저전력 대기)** 이고(`powercfg /a` 에 'Standby (S0 Low Power Idle)'
    가 보이면 해당), 그 환경에서 ES_SYSTEM_REQUIRED 는 문서상 **무효**다
    (PowerRequestSystemRequired 도 동일). Modern Standby 는 '화면 꺼짐 = 대기 진입' 이라
    화면을 붙잡는 것 말고는 막을 방법이 없다 — 실제로 SYSTEM_REQUIRED 만 걸었을 때 긴
    export 가 디스플레이 타임아웃(기본 10분) 뒤 대기로 들어가며 멈췄다(사용자 보고).
    대가로 export 중에는 화면이 안 꺼진다(끝나면 해제되어 원래 전원 정책으로 복귀).
    ⚠️ES_CONTINUOUS 상태는 '호출한 스레드'에 귀속(스레드 종료 시 자동 해제)이라 반드시
    메인 스레드에서만 호출할 것 — 워커에서는 Controller._keepAwakeSig 로 큐잉."""
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

# 업데이트 확인: GitHub 릴리스 목록(공개 repo, 무인증 60회/시간 — 시작 시 1회면 충분)
_RELEASES_API = "https://api.github.com/repos/lim8701/FilmRawstery/releases"

# 필름 시뮬레이션 카탈로그 (key, 표시명, 그룹). 실제 luts/<key>.cube 가 있는 것만 UI 에 노출
# (identity=None 은 LUT 미적용이라 항상 포함). 흑백 등은 .cube 를 넣으면 자동으로 다시 나타남.
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


def available_film_sims():
    """카탈로그 중 luts/<key>.cube 가 실제 존재하는 것만 [{key,label,group}] 로. identity 는 항상 포함."""
    out = []
    for key, label, group in FILM_SIM_CATALOG:
        if key == "identity" or (LUTS_DIR / f"{key}.cube").exists():
            out.append({"key": key, "label": label, "group": group})
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


def _pair_flags(folder: str, names: list) -> list:
    """파일명 리스트 → 탐색기 항목 + **RAW/JPEG 페어 표식**.

    카메라가 RAW+JPEG 를 동시 기록하면 같은 사진이 목록에 두 번 나온다(실측: X100V 폴더에서
    RAF 503 / JPG 497 이 **stem 기준 497쌍 정확히 일치**, JPEG 단독 0장). 같은 폴더·같은 stem 에
    RAW 가 있는 일반 이미지에 `paired` 를 달아 기본으로 접고, 짝을 가진 RAW 행에는 배지용
    `pair`("JPG")를 단다.
    ⚠️목록에서 **빼지 않고 플래그만** 단다 — QML 토글(P)이 재스캔 없이 즉시 펼칠 수 있게.
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


class LutProvider(QQuickImageProvider):
    """필름 시뮬레이션 LUT 아틀라스를 'image://lut/<key>' 로 제공.

    key 는 luts/<key>.cube 파일명(확장자 제외). 모든 LUT 는 같은 크기 N 을 가정.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._atlases: dict[str, QImage] = {}
        self.size = 0  # LUT 한 변 크기 N

    def load_dir(self, luts_dir: Path) -> None:
        for cube in sorted(luts_dir.glob("*.cube")):
            # 사용자 교체 .cube(손상/헤더누락/1D 등) 하나가 앱 시작을 통째로 막지 않도록
            # 파일별로 방어 — 실패는 스킵+경고(해당 필름룩만 미로드, 나머지는 정상).
            try:
                lut, n = load_cube(str(cube))
            except Exception as exc:
                print(f"[lut] ⚠️로드 실패로 스킵: {cube.name} ({exc})")
                continue
            self._atlases[cube.stem] = atlas_qimage(lut, n)
            self.size = n
        print(f"[lut] {len(self._atlases)}개 로드, N={self.size}")

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        key = image_id.split("?", 1)[0]  # 쿼리스트링 제거
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
                        if im.loadFromData(thumb):
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
            reader = QImageReader(buf, b"jpeg")
            reader.setAutoTransform(True)            # EXIF 방향 반영
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
            reader = QImageReader(buf, b"jpeg")
            reader.setAutoTransform(True)             # EXIF 방향 반영
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
    stampChanged = Signal()     # 날짜 스탬프 오버레이 갱신 알림
    editsReady = Signal()       # 새 파일 디코딩 완료 -> QML 이 저장 편집 복원(또는 기본값 리셋)
    histogramChanged = Signal()  # 톤커브 배경 히스토그램 갱신 알림
    lensChanged = Signal()       # 렌즈 보정 on/off 변경 알림
    busyChanged = Signal()       # 디코딩(렌즈 보정 포함) 진행 중 표시
    folderChanged = Signal()     # 좌측 file explorer 현재 폴더/파일목록 갱신 알림
    likesChanged = Signal()      # 좋아요(셀렉트) 상태 변경 알림 (썸네일 하트 반영용)
    editsChanged = Signal()      # 편집 사이드카 유무 변경 알림 (썸네일 편집 배지 반영용)
    flushEdits = Signal()        # 이미지 전환 직전: QML 이 *이전* 파일로 편집 저장(플러시)
    fullChanged = Signal()       # GPU export: 풀해상도 src URL 갱신(QML Image 재로드용)
    fullReady = Signal()         # GPU export: 풀해상도 디코드 완료(QML 이 grab 준비)
    fullAborted = Signal()       # GPU export: 파이썬 측 디코드 실패 → QML 로더 해제(active=false)
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
    aiNrChanged = Signal()       # AI 디노이즈(NAFNet) 사용 여부/상태 문구 갱신 알림
    captionChanged = Signal()    # 캡션 텍스트/생성 상태 갱신 알림(Florence-2)
    searchChanged = Signal()     # 탐색기 캡션 검색어 변경 알림(explorerFiles 재평가)
    indexChanged = Signal()      # 폴더 배치 인덱싱 busy/진행/상태 갱신
    updateChanged = Signal()     # 새 버전 발견 알림(updateVersion/updateUrl 갱신)
    screenSizeChanged = Signal()  # 창이 놓인 화면의 픽셀 크기 갱신(배경화면 'Match screen')
    # 깊이 범위 자동 시드 확정 → QML 이 'depth@auto' 센티넬을 실제 값으로 교체하고 슬라이더에 반영.
    # (켜는 순간엔 거리 맵이 없어 범위를 정할 수 없다 — 맵이 나온 뒤에야 분포에서 시드된다)
    depthAutoResolved = Signal(int, float, float, float)   # layer, near, far, feather
    _renderReady = Signal(object)  # (내부) 워커 스레드 -> 메인 스레드 결과 전달
    _fullDecoded = Signal(bool)  # (내부) 풀해상도 디코드 워커 -> 메인 스레드
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
    _keepAwakeSig = Signal(bool)  # (내부) export 워커 -> 메인 스레드 슬립 방지 해제(스레드 귀속 API)
    _hazeReady = Signal(object)  # (내부) 디헤이즈 추정 워커 -> 메인 스레드 (seq, (t, A, conf))
    _nrReady = Signal(object)    # (내부) NR 베이스 워커 -> 메인 스레드 (seq, 디노이즈드 luma)
    _aiNrStatusSig = Signal(object)  # (내부) AI NR 워커 -> 메인 스레드 (seq, 상태 문구)
    _aiNrDlSig = Signal(object)      # (내부) AI 모델 다운로드 워커 -> 메인 (downloading, 진행률 0..1)
                                     #  ⚠️seq 없음 — 다운로드는 모델 전역(이미지 무관), finally 로 항상 해제
    _aiNrInitSig = Signal(bool)      # (내부) ORT 세션 초기화(GPU 점유) 오버레이 ON/OFF — 세션 전역
    _updateSig = Signal(object)      # (내부) 업데이트 확인 워커 -> 메인 (새 버전 태그, 릴리스 URL)
    _folderScanSig = Signal(object)  # (내부) 폴더 스캔 워커 -> 메인 (seq, folder, items, likes, edited, force)
    _indexProgressSig = Signal(object)  # (내부) 폴더 배치 인덱싱 워커 -> 메인 (seq, done, total, status)

    def __init__(self, provider: RawProvider, curve_provider: "CurveProvider",
                 stamp_provider: "StampProvider" = None,
                 full_provider: "RawFullProvider" = None,
                 sky_provider: "SkyMaskProvider" = None,
                 cm_provider: "DisplayCmProvider" = None,
                 haze_provider: "HazeProvider" = None,
                 nr_provider: "NrBaseProvider" = None,
                 face_provider: "FaceThumbProvider" = None):
        super().__init__()
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
        self._sky_provider = sky_provider        # 하늘 마스크 텍스처
        self._haze_provider = haze_provider      # 디헤이즈 투과율 맵 텍스처(DCP)
        self._haze_url = "image://haze/h?v=0"
        self._haze_counter = 0
        self._haze_seq = 0          # 비동기 추정 순번(이미지 전환 레이스 방지)
        self._haze_t = None         # 투과율 맵(numpy float32, 소형) — CPU export 용
        self._haze_A = [1.0, 1.0, 1.0]   # 대기광(display sRGB)
        self._haze_conf = 0.0       # 추정 신뢰도(0=물리 모델 미사용 → 톤모델 폴백)
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
        self._stamp_text = ""       # 날짜 스탬프 텍스트 ('YY MM DD)
        self._stamp_url = "image://stamp/s?v=0"
        self._stamp_counter = 0
        self._stamp_wr = 0.0        # 스프라이트 (W,H)/짧은변 비율 — QML 오버레이 크기 산출용
        self._stamp_hr = 0.0
        self._stamp_rot = 0         # 촬영 방향(센서→업라이트 CW 회전, 0/90/180/270) — 데이트백 배치
        self._stamp_font = "7c_bold"   # 데이트백 폰트 방식(date_stamp.STYLES 키)
        self._stamp_size = 0.032       # 데이트백 크기 = 숫자높이/짧은변 비율(슬라이더, date_stamp.DEFAULT_SIZE_FRAC)
        self._stamp_margin = 0.05      # 데이트백 여백 = 코너 안쪽 여백/짧은변 비율 — 슬라이더(date_stamp.MARGIN_FRAC)
        self._stamp_grain_src = 0.0    # 스탬프 그레인 소스 = 전체 grainAmt(QML 이 push) — 스탬프는 사진 필름 그레인에 연동
        self._proxy_w = 0           # 마지막 프록시 크기(스탬프 레이어 재렌더용)
        self._proxy_h = 0
        self._histogram = []        # 256-bin 휘도 히스토그램(0..1 정규화)
        self._proxy_small = None    # 히스토그램 재계산용 축소 프록시(float32 0..1)
        self._lut_cache = {}        # simKey -> (lut_arr, n)
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
        self._renderReady.connect(self._on_render_ready)
        self._fullDecoded.connect(self._on_full_decoded)
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
        self._aiNrStatusSig.connect(self._on_ai_nr_status)
        self._aiNrDlSig.connect(self._on_ai_nr_dl)
        self._aiNrInitSig.connect(self._on_ai_nr_init)
        self._updateSig.connect(self._on_update_found)
        self._folderScanSig.connect(self._on_folder_scanned)
        self._indexProgressSig.connect(self._on_index_progress)
        self._scan_seq = 0            # 폴더 스캔 순번(빠른 탐색 시 오래된 결과 폐기)
        self._skip_rescan_once = False  # 우리 자신의 사이드카 저장으로 인한 watcher 재스캔 1회 무시
        # 현재 폴더 자동 감시: 디렉터리 변화 -> 디바운스 -> 재스캔(변경분 있을 때만 갱신)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(400)   # 연속 변화/중복 이벤트 합치기
        self._rescan_timer.timeout.connect(self._do_auto_rescan)
        # 마지막 탐색 폴더 영구 저장(재시작 시 복원 + 폴더 대화상자 시작 위치)
        self._settings = QSettings("FilmRawstery", "FilmRawstery")
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
            reader = QImageReader(buf, b"jpeg")
            reader.setAutoTransform(True)    # EXIF 회전 → 정방향 입력(세로사진 정확도)
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
            self._caption_status = f"Failed: {exc}"
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
            # 썸네일 편집 배지 즉시 반영(현재 탐색기 폴더 파일일 때)
            if str(p.parent) == self._edited_folder and p.name not in self._edited:
                self._edited.add(p.name)
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
        # 썸네일 편집 배지(파일명 앰버) 해제 — 현재 폴더 파일이면 캐시에서 제거 + 리비전 증가
        if str(p.parent) == self._edited_folder and p.name in self._edited:
            self._edited.discard(p.name)
            self._edit_rev += 1
            self.editsChanged.emit()

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
        """Export 기본 파일명: 원본과 같은 폴더의 '<원본이름>_exported.png'."""
        if not self._path:
            return QUrl()
        p = Path(self._path)
        return QUrl.fromLocalFile(str(p.with_name(p.stem + "_exported.png")))

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
        pdict = {k: params[k] for k in params}     # QVariantMap -> 평범한 dict
        pdict["proxyEdge"] = max(self._proxy_w, self._proxy_h)   # 공간 반경 스케일 기준(스냅샷)
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
            lut_arr, lut_n = load_cube(str(LUTS_DIR / f"{params.get('simKey','identity')}.cube"))
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
            ok = pipeline.save_image(arr, path)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._exportProgressSig.emit(0.0)   # 진행률 리셋(실패 시 stale 값이 오버레이에 남는 것 방지)
            # 완료 상태를 먼저 확정한 뒤 _exporting 해제 — 순서가 반대면 배치 폴러가 exporting=false
            # 를 보는 순간 exportStatus 가 아직 "Exporting…" 이라 저장된 파일을 실패로 오카운트함.
            self._set_export_status(msg)   # 워커 스레드 -> 시그널은 메인으로 큐잉됨
            self._exporting = False
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
            self._set_export_status(msg)           # 반드시 _exporting 해제보다 먼저(_do_export 참조)
            self._exporting = False
            self._keepAwakeSig.emit(False)

    @Slot()
    def wallpaperClearPanels(self) -> None:  # noqa: N802 (QML 슬롯)
        self._wall_panels = [None, None, None]

    @Slot(QUrl, "QVariantMap")
    def wallpaperCompose(self, file_url: QUrl, opts) -> None:  # noqa: N802 (QML 슬롯)
        """opts: canvasW, canvasH, layout('triptych'|'magazine'), 그리고
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
            if str(o.get("layout", "triptych")) == "magazine":
                paths = [str(x) for x in o.get("paths", ["", "", ""])]
                shots = [self._shot_summary(p)[0] for p in paths]
                mo = dict(o)
                if not str(mo.get("date", "")).strip():     # 비어 있으면 히어로 EXIF 로 채움
                    mo["date"] = self._shot_summary(paths[1])[1] if len(paths) > 1 else ""
                mo["shots"] = shots
                titles = [str(t) for t in mo.get("titles", ["", "", ""])]
                # 메인 사진 캡션은 compose_magazine 이 조립한다(프레임 번호 규칙 단일화)
                img = pipeline.compose_magazine(panels, int(o["canvasW"]),
                                                int(o["canvasH"]), mo)
                ok = bool(img.save(path))
            else:
                canvas = pipeline.compose_wallpaper(
                    panels, int(o["canvasW"]), int(o["canvasH"]), int(o.get("gap", 18)),
                    [float(v) for v in o.get("offsets", [0.0, 0.0, 0.0])])
                ok = pipeline.save_image(canvas, path)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._set_export_status(msg)
            self._exporting = False
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
        """배경화면 기본 파일명: <현재 탐색기 폴더>/wallpaper_{w}x{h}.jpg"""
        folder = self._folder or (str(Path(self._path).parent) if self._path else "")
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
        self._gpu_params = {k: params[k] for k in params}
        self._exporting = True
        self._apply_keep_awake(True)
        self._export_progress = 0.0   # GPU 는 진행률 콜백 없음 → 0 유지(오버레이는 인디터미닛 표시)
        self.exportProgressChanged.emit()
        self._set_export_status("GPU exporting… (full-resolution decode)")
        # 소스 경로 스냅샷 — 디코드 중 다른 사진을 로드해도 요청 시점 파일을 디코드(CPU export 동일).
        threading.Thread(target=self._do_full_decode, args=(self._path,), daemon=True).start()

    def _do_full_decode(self, src_path: str) -> None:
        try:
            lens_on = bool(self._gpu_params.get("lensCorrection", True))
            img, *_ = (image_loader.load_full(src_path, lens_on)
                       if image_loader.is_display_image(src_path)
                       else load_full(src_path, lens_on))
            self._full_provider.set_image(img)
            self._fullDecoded.emit(True)
        except Exception as exc:
            print(f"[export-gpu] 디코드 실패: {exc}")
            self._fullDecoded.emit(False)

    @Slot(bool)
    def _on_full_decoded(self, ok: bool) -> None:
        """메인 스레드: 풀해상도 src 준비됨 → URL 갱신(QML Image 재로드) + grab 트리거."""
        if not ok:
            self._exporting = False
            self._apply_keep_awake(False)
            self._set_export_status("GPU export failed (decode)")
            # 디코드 실패는 QML 이 감지 못 함(fullChanged/fullReady 미발화 → srcFull 상태변화
            # 없음). 명시적으로 로더 해제 신호를 보내지 않으면 gpuExportLoader 가 active=true
            # 로 남아 pipeFull(모든 슬라이더 바인딩) 파이프라인이 계속 재평가된다.
            if self._full_provider is not None:
                self._full_provider.clear()
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
        self._exporting = False
        self._apply_keep_awake(False)
        self._set_export_status("GPU export failed (image load)")
        if self._full_provider is not None:
            self._full_provider.clear()

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
            if self._full_provider is not None:
                self._full_provider.clear()          # 풀해상도 소스 메모리 해제(QML 로더도 곧 해제)
            # ⚠️스레드 생성까지 try 안에 둔다 — 밖에 두면 start() 실패(RuntimeError: can't start
            #   new thread) 시 _exporting 이 True 로 남아 이후 모든 export 가 조용히 무시된다.
            threading.Thread(target=self._finish_gpu_export,
                             args=(arr, dict(self._gpu_params), self._gpu_path, expected),
                             daemon=True).start()
        except Exception as exc:
            self._exporting = False
            self._apply_keep_awake(False)
            if self._full_provider is not None:
                self._full_provider.clear()
            self._set_export_status(f"Failed: {exc}")
            return

    def _finish_gpu_export(self, arr, params: dict, path: str, expected=None) -> None:
        """GPU export 후처리(워커 스레드) — DPR 정규화 → 지오메트리 → 스탬프 → 저장."""
        try:
            import pipeline
            import numpy as np
            # HiDPI 정규화 — grab 이 기대 치수(×DPR 전)와 다르면 먼저 되돌린다. 지오메트리(크롭)
            # 전에 해야 이후 단계의 치수 기준이 CPU export 와 같아진다. 배율 100% 면 no-op.
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
            ok = pipeline.save_image(arr, path)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            print(f"[export-gpu] {msg}")
            # CPU export 와 동일 순서: 상태 확정 → _exporting 해제(반대면 배치 폴러 오카운트).
            self._set_export_status(msg)             # 워커 → 시그널은 메인으로 큐잉됨
            self._exporting = False
            self._keepAwakeSig.emit(False)           # 스레드 귀속 API → 메인으로 큐잉

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

    # 개인용 Wallpaper 패널 노출 여부(.env 플래그, 시작 시 고정) — 릴리즈 기본 숨김
    wallpaperEnabled = Property(bool, _get_wallpaper_enabled, constant=True)

    def _get_curve_url(self) -> str:
        return self._curve_url

    curveUrl = Property(str, _get_curve_url, notify=curveChanged)

    def _get_exif(self) -> list:
        return self._exif_fields

    def _get_exif_summary(self) -> str:
        return self._exif_summary

    shootingInfo = Property("QVariantList", _get_exif, notify=exifChanged)
    shootingSummary = Property(str, _get_exif_summary, notify=exifChanged)

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

    stampUrl = Property(str, _get_stamp_url, notify=stampChanged)
    stampText = Property(str, _get_stamp_text, notify=stampChanged)
    stampWRatio = Property(float, _get_stamp_wr, notify=stampChanged)   # 스프라이트 W/짧은변
    stampHRatio = Property(float, _get_stamp_hr, notify=stampChanged)   # 스프라이트 H/짧은변
    stampRot = Property(int, _get_stamp_rot, notify=stampChanged)       # 촬영 방향 CW 회전(export 전달)
    stampCorner = Property(str, _get_stamp_corner, notify=stampChanged)  # 데이트백 코너(프리뷰 배치)
    stampFont = Property(str, _get_stamp_font, notify=stampChanged)       # 폰트 방식(STYLES 키)
    stampSize = Property(float, _get_stamp_size, notify=stampChanged)     # 크기(숫자높이/짧은변 비율)
    stampMargin = Property(float, _get_stamp_margin, notify=stampChanged) # 코너 여백/짧은변 비율(프리뷰 배치용)

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
                self._lut_cache[key] = load_cube(str(LUTS_DIR / f"{key}.cube"))
            except Exception:
                self._lut_cache[key] = (None, 0)
        return self._lut_cache[key]

    @Slot("QVariantMap")
    def updateHistogram(self, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조절값을 축소 프록시에 numpy 로 적용해 '조절 반영' 히스토그램을 재계산.
        라이트룸처럼 색 단계 전부 반영: 노출/톤/LUT/채도·바이브런스/HSL/대비/커브/컬러그레이딩/비네팅.
        (그레인은 노이즈라 제외, 로컬대비/샤프닝 등 공간 단계는 생략)"""
        if self._proxy_small is None:
            return
        import numpy as np
        import pipeline
        c = self._proxy_small.copy()                       # scene-linear sRGB
        # 노출 = scene-linear 배수 → filmic(단일 톤커브) → display. (셰이더/export 와 동일 순서)
        c = wb.filmic(c * (2.0 ** float(params.get("exposure", 0.0))))
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
        date_val = next((f["value"] for f in self._exif_fields
                         if f["label"] == "Date"), "")
        self._stamp_text = date_stamp.stamp_text_from_date(date_val)
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
        self._settings.setValue("explorer/lastFolder", folder)   # 재시작 복원용   # 재시작 복원용

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
        self._update_stamp_layer()

    @Slot(str)
    def setStampFont(self, style: str) -> None:  # noqa: N802 (QML 슬롯)
        """데이트백 폰트 방식(classic/modern/14seg) 변경 — 레이어만 재렌더."""
        style = str(style or "7c_bold")
        if style == self._stamp_font:
            return
        self._stamp_font = style
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
        ⚠️GUI 스레드에서 date_stamp.sprite_layer 를 동기 실행한다(실측 2.5/21.3/60.3ms —
        size_frac 0.012/0.032/0.050). 호출자 쪽에서 동일값 가드로 걸러줄 것."""
        if self._stamp_provider is None:
            return
        if self._stamp_text:
            layer, wr, hr = date_stamp.sprite_layer(
                self._stamp_text, rot=self._stamp_rot,
                style=self._stamp_font, size_frac=self._stamp_size,
                grain_amt=self._stamp_grain_src)
            self._stamp_wr, self._stamp_hr = wr, hr
            # 프리뷰 스탬프도 사진과 동일한 디스플레이 색관리(광색역 보정)를 거치게 한다 —
            # 안 하면 사진만 보정되고 스탬프는 raw sRGB 라 프리뷰에서 스탬프 색감이 어긋난다.
            # export 는 표준 sRGB 라 stamp_export 는 미적용(원본 sRGB 유지).
            if self._cm_enabled and self._cm_dst is not None:
                import display_cm
                display_cm.apply_display_cm(layer, self._cm_dst)
        else:
            layer = QImage(1, 1, QImage.Format.Format_ARGB32)
            layer.fill(0)            # 투명 1x1 — sampler/Image 항상 유효하게 유지
            self._stamp_wr = self._stamp_hr = 0.0
        self._stamp_provider.set_image(layer)
        self._stamp_counter += 1
        self._stamp_url = f"image://stamp/s?v={self._stamp_counter}"
        self.stampChanged.emit()

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

    def _update_check_worker(self) -> None:
        """릴리스 목록에서 `v메이저.마이너.패치` **정확 일치** 태그만 골라 최신 버전 판단.
        - 자산 릴리스(models-v1)·postfix 태그(v1.2.0_deprecated)·2파트(v1.0)는 정규식으로 제외
        - prerelease/draft 제외
        - 목록 순서(생성일)는 신뢰하지 않고 파싱 후 max 비교(태그 이동/재게시에 안전)
        - 실패(오프라인/한도 초과)는 조용히 무시 — 알림은 최선 노력 기능"""
        import json as _json
        import re
        import urllib.request
        try:
            req = urllib.request.Request(_RELEASES_API, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"FilmRawstery/{APP_VERSION}",   # GitHub API 는 UA 필수
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                rels = _json.load(r)
            best = None   # ((maj,min,pat), "vX.Y.Z", html_url)
            for rel in rels:
                if rel.get("prerelease") or rel.get("draft"):
                    continue
                m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", str(rel.get("tag_name", "")))
                if not m:
                    continue
                ver = tuple(int(g) for g in m.groups())
                if best is None or ver > best[0]:
                    best = (ver, m.group(0), str(rel.get("html_url", "")))
            cur = tuple(int(x) for x in APP_VERSION.split("."))
            if best is not None and best[0] > cur:
                self._updateSig.emit((best[1], best[2]))
        except Exception:
            pass

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

    def _get_adjust_coeffs(self):
        import coeffs
        return coeffs.as_qml_dict()

    # 현상 계수(coeffs.py 단일 진실원) → 셰이더 uniform 주입. 값 바꾸면 프리뷰=export 동시 반영.
    adjustCoeffs = Property("QVariantMap", _get_adjust_coeffs, constant=True)

    def _get_film_sims(self):
        return available_film_sims()

    # 사용 가능한 필름시뮬 목록(luts/*.cube 존재 기준) → QML 이 콤보/simKeys/구분선 구성. 시작 시 1회.
    filmSims = Property("QVariantList", _get_film_sims, constant=True)

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
            # 일반 이미지(JPG/PNG/TIFF)는 display-referred 어댑터로 — 반환 계약은 동일한 6-튜플.
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
        img, as_shot, as_shot_tint, cam, ref, cam2srgb = res
        if self._kelvin is None:
            self._kelvin = as_shot          # as-shot 으로 디코딩됨 -> 현재값 동기화
            self._tint = as_shot_tint       # as-shot tint 도 함께 동기화(새 파일)
        self._cam = cam
        self._ref = ref
        self._cam2srgb = cam2srgb
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
        print(f"[load] {self._path}  ({img.width()}x{img.height()})  "
              f"kelvin={self._kelvin} tint={self._tint:.2f} as_shot={as_shot}")
        # 새 파일의 첫 디코딩이 끝났을 때만 복원 트리거(WB 커밋 등 재디코딩에는 발화 안 함).
        # 이 시점에 UI 가 이 파일을 반영하게 되므로 _ui_path 갱신(저장 귀속 기준).
        if self._fresh_load:
            self._fresh_load = False
            self._ui_path = self._path
            self.editsReady.emit()
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
    global image_loader
    import date_stamp, image_loader, make_luts, wb                    # noqa: E401
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

    controller = Controller(provider, curve_provider, stamp_provider, full_provider,
                            sky_provider, cm_provider, haze_provider, nr_provider,
                            face_provider)
    ctx = engine.rootContext()
    ctx.setContextProperty("controller", controller)
    ctx.setContextProperty("lutN", lut_provider.size)

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
        last = str(QSettings("FilmRawstery", "FilmRawstery")
                   .value("explorer/lastFolder", "") or "")
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
