# FilmRawstery.spec — PyInstaller onedir build
# Build:  .\.venv\Scripts\pyinstaller.exe FilmRawstery.spec --noconfirm
# 디버그 시 아래 CONSOLE = True 로 두고 빌드(누락 DLL/플러그인 에러를 콘솔로 확인),
# 검증 후 CONSOLE = False 로 바꿔 재빌드(릴리스: 콘솔창 없음).
import os
import re
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_all

CONSOLE = False
IS_MAC = sys.platform == "darwin"

# --- macOS 번들 메타데이터 ---
# 버전은 main.py 를 단일 진실원으로 읽는다(Windows 는 packaging/version_info.txt 가 담당 —
# mac 용 수동 동기화 지점을 새로 만들지 않기 위해 파싱한다).
APP_VERSION = re.search(r'APP_VERSION = "([^"]+)"',
                        open("main.py", encoding="utf-8").read()).group(1)
# ⚠️번들 식별자는 한 번 정하면 바꾸지 않는다(.iss 의 AppId 와 같은 성격 — LaunchServices
#   등록·환경설정 도메인·공증 이력이 이 값에 붙는다).
BUNDLE_ID = "io.github.lim8701.FilmRawstery"

# --- QML (개별 명시: 새 .qml 추가 시 여기에 등록. 위치: ui/ — frozen 도 lib/ui/ 로 동형) ---
QML = ["Main.qml", "Splash.qml", "PreviewWindow.qml", "CurveEditor.qml", "FilmStrip.qml",
       "EditedBadge.qml", "DarkButton.qml",
       "ShortcutHelp.qml", "RawPeekWindow.qml"]
datas = [(os.path.join("ui", q), "ui") for q in QML]
datas += [
    ("shaders", "shaders"),   # .frag + 미리 컴파일된 .qsb (frozen 은 런타임 재컴파일 안 함)
    ("fonts", "fonts"),       # DSEG7Classic-Bold.ttf
    ("assets", "assets"),     # 후원 팝업의 카카오페이 QR (Main.qml 이 ../assets 로 참조)
    (os.path.join("icons", "app.ico"), "icons"),   # 창/작업표시줄 아이콘(main.py setWindowIcon)
    # 라이선스/고지(비상업 배포 시 동봉 의무) — MIT + 제3자 라이선스 + 종합 NOTICE.
    ("LICENSE", "."),
    ("NOTICE.txt", "."),
    ("THIRD_PARTY_LICENSES", "THIRD_PARTY_LICENSES"),
]

# --- LUT: ARR(Stuart Sowerby) 흑백 LUT 는 재배포 금지 → 번들에서 제외 ---
_ARR_LUTS = {"acros.cube", "acros_g.cube", "acros_r.cube", "acros_ye.cube",
             "monochrome.cube", "sepia.cube"}
for fn in sorted(os.listdir("luts")):
    if fn.endswith(".cube") and fn not in _ARR_LUTS:
        datas.append((os.path.join("luts", fn), "luts"))
for extra in ("LICENSE", "README.md"):       # LUT 출처/라이선스 동봉(있으면)
    p = os.path.join("luts", extra)
    if os.path.exists(p):
        datas.append((p, "luts"))

# ⚠️ models/ 는 번들하지 않음 — sky_seg/ai_denoise/caption 각각의 ensure_model() 이
#    최초 사용 시 자동 다운로드(zip 배포 → 압축 푼 폴더가 쓰기 가능하므로 성공).
#    캡션(Florence-2, models/florence2_*)은 특히 ~1.1GB 라 번들 금지(다운로드는 앱 내 옵트인).

datas += collect_data_files("rawpy")          # libraw 네이티브 DLL

# onnxruntime(하늘 세그 sky_seg 런타임) — 네이티브 DLL + capi 전량 수집
ort_datas, ort_binaries, ort_hidden = collect_all("onnxruntime")
datas += ort_datas

hiddenimports = [
    "scipy.ndimage",     # lazy `from scipy.ndimage import ...`
    "sky_seg", "face_seg", "depth", "coeffs", "display_cm", "haze", "mist", "ai_denoise", "caption", "hashtags",  # main/pipeline 에서 지연 import 되는 로컬 모듈(명시로 보장)
    "image_loader",      # main._load_heavy_modules 의 지연 import(일반 이미지 JPG/PNG/TIFF 어댑터)
    "raw_peek", "develop_anim", "brush",  # RAW Peek(R)·현상 애니메이션(5번 탭)·브러시 마스킹 — 전부 함수 안 import
                                          # (v1.11.0 에서 처음 번들. PyInstaller 가 바이트코드로 잡아내긴 하지만
                                          #  이 파일의 기존 관습대로 명시한다 — 빌드 후 .toc 로 실제 포함을 확인)
    "app_dirs",          # 모델 저장 위치(OS 사용자 데이터 디렉터리) — 위 모듈들이 사용
    "cv2",               # face_seg/depth 가 함수 안에서 지연 import(얼굴 검출 FaceDetectorYN + 리샘플/가이디드필터)
] + ort_hidden
# numpy / rawpy / exifread / onnxruntime 본체는 일반 import → 자동 탐지

excludes = [  # 미사용 Qt 모듈 제거(용량↓). 문제 생기면 먼저 excludes 완화
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebView", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtTest", "PySide6.QtSql",
    "PySide6.QtHelp", "PySide6.QtDesigner", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtSpatialAudio",
    "tkinter",
    # ⚠️ unittest/pydoc/test 는 제외 금지 — numpy.testing 이 scipy 경유로 unittest 를 끌어옴
    # QtNetwork / QtOpenGL 도 제외 금지(Qt Quick 가 끌어올 수 있음)
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=ort_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
# OpenCV 의 videoio ffmpeg DLL(29MB) 제외 — hooks-contrib 의 hook-cv2 가 무조건 넣지만
# 이 앱은 cv2 를 얼굴 검출(FaceDetectorYN)과 리샘플(resize/boxFilter)에만 쓴다(영상 없음).
# videoio 는 지연 로드라 파일이 없어도 나머지 기능에 영향 없음.
# ⚠️**macOS 로 확장하면 안 된다** — mac 휠은 `cv2.abi3.so` 가 `@loader_path/.dylibs/
#   libavcodec…` 를 **로드타임 링크**한다(otool -L 확인). 지우면 import cv2 자체가 실패한다.
#   그래서 mac 번들은 cv2/.dylibs(약 78MB)를 그대로 안고 간다.
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0].lower()]

# --- macOS: 미사용 Qt 프레임워크 제거 ---
# ⚠️**excludes 는 macOS 에서 Qt 프레임워크를 걸러내지 못한다.** PySide6 훅이 `PySide6/Qt/lib`
#   의 프레임워크 **120개를 전량 수집**하기 때문이다(Windows 는 포함된 확장 모듈의 의존
#   DLL 만 수집돼 excludes 로 충분하다). 실측: 걸러내기 전 .app 676MB 중 Qt/lib 가 322MB.
# 의존 그래프 전수 검사(otool -L, 523개 수집 바이너리) 결과 아무것도 링크하지 않는
# 프레임워크가 47개(250MB)였고 그 중 **QtWebEngineCore 한 개가 218MB(87%)** 다.
# 나머지 44개는 합쳐 32MB 뿐이라(개당 1MB 미만) 실행 실패 위험을 지고 건드리지 않는다 —
# 예: QtSql 은 QtQmlLocalStorage 가, QtMultimedia 는 Qt/plugins 의 미디어 플러그인이 링크한다.
# WebEngine/WebView 는 **자기 계열 + 자기 qml 플러그인만** 참조하는 것을 전수 확인했다.
# 재검증: dist 를 만든 뒤 아래 grep 이 이 계열 밖에서 걸리는지 보면 된다.
#   find dist/FilmRawstery.app -name '*.dylib' -o -name '*.so' | xargs -n1 otool -L \
#     | grep -E '@rpath/(QtWebEngine|QtWebView)'
if IS_MAC:
    def _drop_webengine(toc):
        return [e for e in toc
                if "QtWebEngine" not in e[0] and "QtWebView" not in e[0]]
    a.binaries = _drop_webengine(a.binaries)
    a.datas = _drop_webengine(a.datas)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FilmRawstery",
    debug=False,
    strip=False,
    upx=False,          # UPX off — Qt DLL 손상 방지
    console=CONSOLE,
    # 아이콘: Windows=.ico / macOS=.icns (둘 다 packaging/make_icon.py 생성물)
    icon=os.path.join("icons", "app.icns" if IS_MAC else "app.ico"),
    # version(exe 속성>세부정보)은 **Windows 전용 인자** — mac 은 Info.plist 가 담당한다.
    version=None if IS_MAC else os.path.join("packaging", "version_info.txt"),
    # universal2 로 배포되는 PySide6 프레임워크를 arm64 로 thin 시킨다(나머지 휠은 arm64 전용).
    target_arch="arm64" if IS_MAC else None,
    contents_directory="lib",   # onedir 하위폴더 이름(기본 _internal → lib). ⚠️.app 번들에는
                                # 무효 — mac 은 Contents/Frameworks + Contents/Resources 고정.
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="FilmRawstery",   # → dist/FilmRawstery/
)

# --- macOS .app 번들 ---
# ⚠️NS*UsageDescription: 이 앱은 **사진 폴더 옆에 사이드카**(.filmrawsteryedits/)를 쓰고
#   폴더를 스캔한다. Desktop/Documents/Downloads/외장볼륨은 TCC 보호 대상이라 문구가 없으면
#   정체불명 동의 다이얼로그가 뜬다. SD 카드에서 RAF 를 여는 것이 주 사용 흐름이므로
#   NSRemovableVolumesUsageDescription 은 사실상 필수.
# ⚠️LSMinimumSystemVersion 은 **번들 Mach-O 의 minos 최대값**을 그대로 적은 것이다(측정:
#   `vtool -show-build` 를 517개 바이너리에 돌려 최대 15.0). 휠 태그를 믿으면 안 된다 —
#   PySide6 6.11.2 는 `macosx_13_0_universal2` 태그인데 바인딩(`QtCore.abi3.so`,
#   `libpyside6`, `libshiboken6`)의 minos 가 **15.0** 이다(Qt CI 가 6.10 부터 macOS 15 에서
#   배포 타깃 없이 빌드한다. shiboken6 실측: 6.9.1=12.0 / 6.10.0=15.0 / 6.11.2=15.0).
#   ⚠️여기를 실제 minos 보다 낮게 적으면 Finder 가 실행을 허용한 뒤 dyld 오류로 죽는다 —
#   낮게 적어서 지원 범위가 늘어나지는 않는다.
#   하한을 macOS 14 로 내리려면 **둘 다** 필요하다: PySide6 6.9.x 고정(그 위 하한은
#   numpy/scipy/onnxruntime 의 14.0) + python.org 파이썬(Homebrew 파이썬의 libpython·
#   libmpdec 가 15.0). Qt 버전이 Windows 빌드와 갈라지는 대가를 치를 값인지 판단할 것.
if IS_MAC:
    app = BUNDLE(
        coll,
        name="FilmRawstery.app",
        icon=os.path.join("icons", "app.icns"),
        bundle_identifier=BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            "CFBundleName": "Film Rawstery",
            "CFBundleDisplayName": "Film Rawstery",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSMinimumSystemVersion": "15.0",   # = 번들 minos 최대값(위 주석)
            "LSApplicationCategoryType": "public.app-category.photography",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,   # 앱 UI 가 다크 전제
            "NSHumanReadableCopyright": "MIT License. See LICENSE / NOTICE.txt.",
            "NSDesktopFolderUsageDescription":
                "Film Rawstery opens photos and stores per-photo edits next to them.",
            "NSDocumentsFolderUsageDescription":
                "Film Rawstery opens photos and stores per-photo edits next to them.",
            "NSDownloadsFolderUsageDescription":
                "Film Rawstery opens photos and stores per-photo edits next to them.",
            "NSRemovableVolumesUsageDescription":
                "Film Rawstery reads RAW files directly from memory cards.",
            "NSNetworkVolumesUsageDescription":
                "Film Rawstery reads photos from network volumes.",
            "NSPhotoLibraryUsageDescription":
                "Film Rawstery can browse photos stored in your Photos library.",
        },
    )
