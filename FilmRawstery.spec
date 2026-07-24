# FilmRawstery.spec — PyInstaller onedir build
# Build:  .\.venv\Scripts\pyinstaller.exe FilmRawstery.spec --noconfirm
# 디버그 시 아래 CONSOLE = True 로 두고 빌드(누락 DLL/플러그인 에러를 콘솔로 확인),
# 검증 후 CONSOLE = False 로 바꿔 재빌드(릴리스: 콘솔창 없음).
import os
from PyInstaller.utils.hooks import collect_data_files, collect_all

CONSOLE = False

# --- QML (개별 명시: 새 .qml 추가 시 여기에 등록. 위치: ui/ — frozen 도 lib/ui/ 로 동형) ---
QML = ["Main.qml", "Splash.qml", "PreviewWindow.qml", "CurveEditor.qml", "FilmStrip.qml"]
datas = [(os.path.join("ui", q), "ui") for q in QML]
datas += [
    ("shaders", "shaders"),   # .frag + 미리 컴파일된 .qsb (frozen 은 런타임 재컴파일 안 함)
    ("fonts", "fonts"),       # DSEG7Classic-Bold.ttf
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
    "sky_seg", "coeffs", "display_cm", "haze", "ai_denoise", "caption", "hashtags",  # main/pipeline 에서 지연 import 되는 로컬 모듈(명시로 보장)
    "app_dirs",          # 모델 저장 위치(OS 사용자 데이터 디렉터리) — 위 모듈들이 사용

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
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FilmRawstery",
    debug=False,
    strip=False,
    upx=False,          # UPX off — Qt DLL 손상 방지
    console=CONSOLE,
    icon=None,
    version=os.path.join("packaging", "version_info.txt"),   # exe 속성>세부정보 버전 표시
    contents_directory="lib",   # onedir 하위폴더 이름(기본 _internal → lib)
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="FilmRawstery",   # → dist/FilmRawstery/
)
