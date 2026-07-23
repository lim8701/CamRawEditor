import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Effects

ApplicationWindow {
    id: win
    visible: true
    visibility: Window.Maximized   // 시작 시 최대화(타이틀바·작업표시줄 유지)
    width: 1280
    height: 820                     // 복원(restore) 시 사용할 크기
    title: "FILM RAWSTERY  v" + controller.appVersion   // OS 타이틀바/작업표시줄 상시 노출(버그 제보 스크린샷에 자동 포함)
           + (controller.updateVersion !== "" ? "   -  new " + controller.updateVersion + " available" : "")
    color: "#1a1a1a"

    // 텍스트 입력(날짜 필드 등)이나 콤보박스가 포커스를 가지면 단일문자 단축키
    // (I/D/C/B/J/L)를 비활성화 — 입력/타입어헤드 글자가 전역 토글로 새는 것 방지.
    // Controls 2 TextField/TextArea 는 TextInput/TextEdit 파생이라 타입으로 판별.
    readonly property bool _typing: {
        var it = activeFocusItem
        return !!it && (it instanceof TextInput || it instanceof TextEdit
                        || it instanceof ComboBox)
    }

    // === 종료 확인 ===
    // X/Alt+F4 로 닫을 때 한 번 확인. allowClose 가 true 면(확인 후) 그대로 닫힘.
    property bool allowClose: false
    onClosing: function(close) {
        if (!win.allowClose) {
            close.accepted = false
            quitDialog.open()
        }
    }

    // === WB 실시간 프리뷰 (드래그 중) ===
    // baked 색온도로 디코딩된 프록시에 "baked->target" 상대 게인만 셰이더로 입힌다.
    // 손을 떼면 target 색온도로 재디코딩(확정)하고 게인은 (1,1,1) 로 수렴 -> 이중적용 없음.
    // 유도상 daylight_ref·기준온도가 약분돼 카메라 매트릭스(camMatrix)만 있으면 계산 가능.
    readonly property int wbTRef: 5500

    // 촬영정보 플로팅 패널 표시 여부 (I 키로 토글)
    property bool infoOverlay: true
    Shortcut { sequence: "I"; enabled: !win._typing; onActivated: win.infoOverlay = !win.infoOverlay }

    // 날짜 스탬프(필름 데이트백) 표시 여부 (D 키로 토글). 기본 off.
    property bool dateStamp: false
    Shortcut { sequence: "D"; enabled: !win._typing; onActivated: win.dateStamp = !win.dateStamp }

    // AI 캡션 오버레이 표시 여부 (C 키로 토글). 끄면 로드 시 자동 생성도 중단(연산 낭비 방지).
    property bool captionOverlay: true
    onCaptionOverlayChanged: controller.setCaptionEnabled(captionOverlay)
    Shortcut { sequence: "C"; enabled: !win._typing; onActivated: win.captionOverlay = !win.captionOverlay }

    // 좌측 File Explorer 패널 표시 여부 (B 키로 토글)
    property bool showExplorer: true
    Shortcut { sequence: "B"; enabled: !win._typing; onActivated: win.showExplorer = !win.showExplorer }

    // 원본 비교(Before/After): true 면 프리뷰가 무편집 현상(dispPre)으로 전환. 버튼/\ 키로 토글.
    property bool compareOn: false
    Shortcut { sequence: "\\"; onActivated: win.compareOn = !win.compareOn }

    // 디스플레이 색관리(프리뷰 전용 sRGB→모니터 색역 보정, display_cm.py). Ctrl+Shift+M 토글. export 불변.
    property bool displayCM: true
    // 스탬프 오버레이도 사진과 같이 CM 을 거치게 — 토글을 컨트롤러에 전달(스프라이트 재보정).
    onDisplayCMChanged: controller.setDisplayCmEnabled(displayCM)

    // 클리핑 경고 오버레이(프리뷰): 하이라이트=빨강 / 섀도=파랑. J 키로 토글(라이트룸과 동일).
    property bool clipWarn: false
    Shortcut { sequence: "J"; enabled: !win._typing; onActivated: win.clipWarn = !win.clipWarn }
    // Undo / Redo (편집 스냅샷)
    Shortcut { sequences: [StandardKey.Undo]; onActivated: win.undo() }                    // Ctrl+Z
    Shortcut { sequences: [StandardKey.Redo, "Ctrl+Shift+Z"]; onActivated: win.redo() }    // Ctrl+Y / Ctrl+Shift+Z
    // 우측 패널 전환: Edit / Crop·Geometry / Masking
    Shortcut { sequence: "Ctrl+1"; onActivated: win.activePanel = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: win.activePanel = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: win.activePanel = 2 }

    // 디스플레이 색관리(프리뷰 전용 sRGB→모니터 색역 보정) 토글.
    Shortcut { sequence: "Ctrl+Shift+M"; onActivated: win.displayCM = !win.displayCM }


    // 컬러 그레이딩 Hue 슬라이더 위에 두는 무지개 스펙트럼 막대(슬라이더 위치↔색상 가이드).
    // (네이티브 스타일은 Slider.background 커스터마이즈 미지원 → 별도 막대로 표시)
    component HueBar: Rectangle {
        implicitHeight: 8; radius: 4
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0;    color: "#ff0000" }
            GradientStop { position: 0.1667; color: "#ffff00" }
            GradientStop { position: 0.3333; color: "#00ff00" }
            GradientStop { position: 0.5;    color: "#00ffff" }
            GradientStop { position: 0.6667; color: "#0000ff" }
            GradientStop { position: 0.8333; color: "#ff00ff" }
            GradientStop { position: 1.0;    color: "#ff0000" }
        }
    }

    // 우측 활성 패널: 0=Edit, 1=Crop/Rotate/Geometry (우측 끝 세로 셀렉터 바로 전환)
    property int activePanel: 0

    // HSL 컬러 믹서: 8색상대(45° 균등) × 색상/채도/휘도 조정값(-1..1), 선택 대역 hslBand.
    property var hslH: [0, 0, 0, 0, 0, 0, 0, 0]
    property var hslS: [0, 0, 0, 0, 0, 0, 0, 0]
    property var hslL: [0, 0, 0, 0, 0, 0, 0, 0]
    property int hslBand: 0
    function setHslBandValue(arr, v) {     // arr: "hslH"|"hslS"|"hslL" — 선택 대역값 갱신
        var a = win[arr].slice(); a[win.hslBand] = v; win[arr] = a
    }
    function resetHsl() {
        win.hslH = [0, 0, 0, 0, 0, 0, 0, 0]
        win.hslS = [0, 0, 0, 0, 0, 0, 0, 0]
        win.hslL = [0, 0, 0, 0, 0, 0, 0, 0]
    }

    // Edit 패널 섹션 접기 상태(인덱스=표시순서: 0필름 1라이트 2톤커브 3WB 4컬러 5컬러믹서
    // 11컬러그레이딩 6디테일&비네팅 7그레인 8샤프닝 12노이즈리덕션 9렌즈 10날짜). 헤더 클릭으로 토글.
    // 기본 접힘: 5 Color Mixer, 8 Sharpening, 12 Noise Reduction, 9 Lens, 11 Color Grading.
    property var secOpen: [true, true, true, true, true, false, true, true, false, false, true, false, false]
    function toggleSec(i) { var a = secOpen.slice(); a[i] = !a[i]; secOpen = a }

    // 마스크 선택영역 오버레이 표시(프리뷰 전용 시각화)
    property bool showSkyMask: false
    // 현재 체크된 마스크 클래스 그룹 key 목록(복합 선택). 토글 시 라이브 재조합.
    property var maskKeys: []
    // SAM 클릭 선택: 모드 on 이면 이미지 클릭이 대상 마스크 생성. maskPoint=정규화 좌표[nx,ny](저장/복원).
    property bool samSelectMode: false
    property var maskPoint: null
    // 사이드카 복원으로 마스크 재생성 중 — 완료 시 오버레이 자동 표시 억제(로드 시 갑자기 적색 방지).
    property bool _maskRestore: false
    function toggleMaskKey(key, on) {
        maskPoint = null; samSelectMode = false   // 클래스 조작 → SAM 점 소스 해제(상호배타)
        var a = maskKeys.slice()
        var i = a.indexOf(key)
        if (on && i < 0) a.push(key)
        else if (!on && i >= 0) a.splice(i, 1)
        maskKeys = a
        maskApplyTimer.restart()   // 디바운스: 빠른 연속 토글을 한 번의 재조합으로 합침
    }
    // SAM 클릭 선택: 이미지 클릭(정규화 프록시 좌표) → 대상 마스크. 클래스 해제(상호배타).
    function selectMaskByPoint(nx, ny) {
        maskKeys = []
        maskPoint = [nx, ny]
        win.showSkyMask = false
        samSelectMode = false      // 한 번 클릭하면 모드 해제(다시 고르려면 버튼 재토글)
        controller.selectMaskAtPoint(nx, ny)
    }
    // 체크박스 토글 코얼레싱 — 마지막 토글 후 잠깐 뒤 한 번만 세그/재조합 실행(스레드 폭증 방지).
    Timer {
        id: maskApplyTimer
        interval: 220
        onTriggered: controller.setMaskClasses(win.maskKeys)
    }

    // 마스킹 조정 슬라이더(라벨 + -1..1 슬라이더 + 더블클릭 리셋 + 조정 중 오버레이 끄기) 공용 컴포넌트.
    // host=win 주입(인라인 컴포넌트는 외부 id 접근 불가). value 는 alias 라 id 로 .value 참조 가능.
    component SkySlider: ColumnLayout {
        id: skyRoot
        // value 는 alias 가 아닌 '실 프로퍼티'(의도값 보존). alias 면 인스턴스가 from/to 보다 먼저
        // value 를 적용할 때 좁은 기본 [-1,1]로 클램프됨(초기값 손실). 아래 Binding 이 from/to 확정
        // 후 내부 슬라이더에 재대입해 순서 무관하게 올바른 범위로 반영.
        property real value: 0.0
        // 내부 Slider 의 pressed 노출 — undo 릴리즈 커밋 게이트(editDragActive)가 참조.
        // ⚠️래퍼라 이게 없으면 skyXxxSlider.pressed 가 조용히 undefined(falsy)로 평가돼
        //   마스킹 슬라이더만 게이트가 안 걸림(실제 발생했던 버그).
        readonly property alias pressed: skySld.pressed
        property string label: ""
        property string suffix: ""
        property real defaultValue: 0.0
        property alias from: skySld.from
        property alias to: skySld.to
        property var host: null
        Layout.fillWidth: true
        spacing: 2
        Label {
            text: skyRoot.label + ":  " + skySld.value.toFixed(2) + skyRoot.suffix
            color: "white"
        }
        Slider {
            id: skySld
            Layout.fillWidth: true
            from: -1.0; to: 1.0
            property real _lastPressMs: 0
            property bool _pendingReset: false
            onPressedChanged: {
                if (pressed) _pendingReset = skyRoot.host.isDblPress(skySld)
                else if (_pendingReset) { skyRoot.value = skyRoot.defaultValue; _pendingReset = false }
            }
            onMoved: { skyRoot.value = value; skyRoot.host.showSkyMask = false }  // 드래그 → 외부 value 동기 + 오버레이 끔
        }
        // 독립 Binding: from/to 확정 뒤(및 이후 변경마다) skyRoot.value 를 내부 슬라이더에 재대입.
        // 드래그의 내부 write 로 바인딩이 깨져도 외부 value 변경(리셋/복원)이 계속 반영(체크박스 Binding 패턴).
        Binding { target: skySld; property: "value"; value: skyRoot.value }
    }

    // 마스킹 조정 직렬화 — 단일 진실원(아래 키 목록). editParams/exportParams/applyEdits/editSaveWatch
    // 가 이 헬퍼로 파생되어 한 곳만 고치면 됨(예전엔 네 곳에 따로 나열 → 누락 시 저장/export 불일치).
    readonly property var skyAdjustKeys: ["skyExp", "skyTemp", "skyTint", "skySat", "skyHi",
                                          "skyShadows", "skyContrast", "skyTexture", "skyClarity", "skyDehaze"]
    function _skySlider(key) {
        switch (key) {
        case "skyExp": return skyExpSlider;        case "skyTemp": return skyTempSlider
        case "skyTint": return skyTintSlider;      case "skySat": return skySatSlider
        case "skyHi": return skyHiSlider;          case "skyShadows": return skyShadowsSlider
        case "skyTexture": return skyTextureSlider; case "skyClarity": return skyClaritySlider
        case "skyDehaze": return skyDehazeSlider;  case "skyContrast": return skyContrastSlider
        }
        return null
    }
    // 저장/export 페이로드 조각(maskKeys + invert + 9개 조정값). render_full 은 maskKeys 무시.
    function skyEditParams() {
        var o = { "maskKeys": win.maskKeys, "maskPoint": win.maskPoint, "skyInvert": skyInvertCheck.checked }
        for (var i = 0; i < win.skyAdjustKeys.length; i++) {
            var k = win.skyAdjustKeys[i]; o[k] = win._skySlider(k).value
        }
        return o
    }
    // 복원: 조정값 + 선택 클래스. 마스크는 클래스로부터 재생성(setMaskClasses → 재추론).
    // skyContrast 는 곱셈자라 중립=1.0(전역 Contrast 와 동일), 나머지는 0.0.
    function _skyDefault(k) { return k === "skyContrast" ? 1.0 : 0.0 }
    function applySkyEdits(p) {
        for (var i = 0; i < win.skyAdjustKeys.length; i++) {
            var k = win.skyAdjustKeys[i]; win._skySlider(k).value = win._ev(p, k, win._skyDefault(k))
        }
        skyInvertCheck.checked = win._ev(p, "skyInvert", false)
        win.showSkyMask = false
        var mpt = win._ev(p, "maskPoint", null)
        var mk = win._ev(p, "maskKeys", [])
        if (mpt && mpt.length === 2) {           // SAM 점 마스크 우선 — 좌표로 재생성
            var samept = controller.hasSkyMask && win.maskPoint
                         && mpt[0] === win.maskPoint[0] && mpt[1] === win.maskPoint[1]
            win.maskKeys = []
            win.maskPoint = mpt.slice()
            if (!samept) { win._maskRestore = true; controller.selectMaskAtPoint(mpt[0], mpt[1]) }
        } else {
            win.maskPoint = null
            // 같은 클래스 조합 + 마스크 이미 존재(undo/redo·paste 등) → 재조합 생략(세그 후처리 비쌈).
            var same = controller.hasSkyMask && JSON.stringify(mk) === JSON.stringify(win.maskKeys)
            win.maskKeys = mk.slice()
            if (mk.length > 0) {
                if (!same) { win._maskRestore = true; controller.setMaskClasses(mk) }
            } else controller.clearSky()
        }
    }

    // === 회전/크롭(지오메트리) 상태 — 프리뷰 뷰변환과 export numpy 양쪽에서 사용 ===
    property int quarterTurns: 0        // 90° 단위 회전 (⟳ CW +1, ⟲ CCW -1, mod 4)
    // 종횡비 콤보 인덱스 -> 비율(가로/세로). aspectCombo 모델과 순서 일치.
    // [0]원본=원본비율잠금(cropAspect 에서 viewport.cA), [1]자유=무잠금(-1), 나머지=고정비율.
    readonly property var aspectRatios: [-1, -1, 1.0, 1.5, 4.0 / 3.0, 16.0 / 9.0, 1.25]
    // 최종 크롭 비율(가로/세로). 방향 토글이 '세로'면 역수. <=0 이면 무잠금(자유).
    readonly property real cropAspect: {
        var idx = aspectCombo.currentIndex
        if (idx === 0) return viewport.cA        // 원본 = 원본 비율(캔버스 비율) 잠금
        var r = win.aspectRatios[idx]
        if (r <= 0) return -1                    // 자유 = 무잠금
        return cropPortraitBtn.checked ? (1.0 / r) : r
    }
    // 자유 크롭 박스(정규화, 캔버스A=flip+90+스트레이튼 후 기준). 기본 = 전체.
    property real cropX: 0.0
    property real cropY: 0.0
    property real cropW: 1.0
    property real cropH: 1.0
    function resetCropRect() { cropX = 0; cropY = 0; cropW = 1; cropH = 1 }
    // 박스 설정: [0,1] 및 최소크기(0.05)로 클램프.
    function setCropRect(nx, ny, nw, nh) {
        var minS = 0.05
        nw = Math.max(minS, Math.min(1.0, nw))
        nh = Math.max(minS, Math.min(1.0, nh))
        nx = Math.max(0.0, Math.min(1.0 - nw, nx))
        ny = Math.max(0.0, Math.min(1.0 - nh, ny))
        win.cropX = nx; win.cropY = ny; win.cropW = nw; win.cropH = nh
    }
    // 종횡비 잠금이면 그 비율의 중앙 최대 박스로 맞춤(자유/원본이면 유지).
    function applyCropAspect() {
        var a = win.cropAspect
        if (a <= 0) return
        var kn = a / Math.max(0.0001, viewport.cA)   // 정규화 가로/세로(nw/nh)
        var nw, nh
        if (kn >= 1.0) { nw = 1.0; nh = 1.0 / kn }
        else { nh = 1.0; nw = kn }
        win.setCropRect((1.0 - nw) / 2.0, (1.0 - nh) / 2.0, nw, nh)
    }
    // 새 파일 로드 / 전체 초기화 시 회전·크롭·지오메트리 리셋.
    function resetGeometry() {
        rotAngleSlider.value = 0.0
        win.quarterTurns = 0
        flipHBtn.checked = false
        flipVBtn.checked = false
        aspectCombo.currentIndex = 0
        cropLandscapeBtn.checked = true
        win.resetCropRect()
        geoVSlider.value = 0
        geoHSlider.value = 0
        geoScaleSlider.value = 100
    }

    // === RAW별 편집 자동 저장/복원 (사이드카 .filmrawsteryedits/<파일명>.json) ===
    property bool _applying: false       // 복원 중 — 자동저장/WB 재디코딩 억제
    function _hasSavedEdits() { var e = controller.editsForCurrent(); return e && e.v !== undefined }

    // 저장 페이로드(원시 컨트롤 값) — 저장/복원의 단일 진실원.
    function editParams() {
        var o = {
            "v": 1,
            "exposure": expSlider.value, "contrast": conSlider.value,
            "highlights": hiSlider.value, "shadows": shSlider.value,
            "whites": whSlider.value, "blacks": blSlider.value,
            "temp": tempSlider.value, "tint": tintSlider.value,
            // simKey(문자열)=복원 기준(목록 변동에 안전). simIndex=구버전 폴백용 유지.
            "simKey": (simCombo.currentIndex >= 0 && simCombo.currentIndex < win.simKeys.length)
                      ? win.simKeys[simCombo.currentIndex] : "identity",
            "simIndex": simCombo.currentIndex, "simStrength": simStrengthSlider.value,
            "texture": texSlider.value, "clarity": claritySlider.value, "dehaze": dehazeSlider.value,
            "vibrance": vibSlider.value, "saturation": satSlider.value,
            "hslH": win.hslH, "hslS": win.hslS, "hslL": win.hslL,
            "cgShadowHue": cgShHueSlider.value, "cgShadowSat": cgShSatSlider.value,
            "cgMidHue": cgMidHueSlider.value, "cgMidSat": cgMidSatSlider.value,
            "cgHighHue": cgHiHueSlider.value, "cgHighSat": cgHiSatSlider.value,
            "cgBalance": cgBalanceSlider.value,
            "vignette": vignetteSlider.value, "grainAmt": grainSlider.value, "grainSize": grainSizeSlider.value,
            "sharpenAmt": sharpAmtSlider.value, "sharpenRadius": sharpRadiusSlider.value,
            "sharpenDetail": sharpDetailSlider.value, "sharpenMask": sharpMaskSlider.value,
            "lumaNR": lumaNrSlider.value, "colorNR": colorNrSlider.value, "aiNr": aiNrCheck.checked,
            "lensCorrection": lensCheck.checked, "dateStamp": win.dateStamp, "stampText": stampField.text,
            "stampStyle": controller.stampFont, "stampSize": controller.stampSize,
            "stampMargin": controller.stampMargin,
            "curves": curveEditor.channelPoints,
            "quarterTurns": win.quarterTurns, "rotateAngle": rotAngleSlider.value,
            "flipH": flipHBtn.checked, "flipV": flipVBtn.checked,
            "aspectIndex": aspectCombo.currentIndex, "cropLandscape": cropLandscapeBtn.checked,
            "cropX": win.cropX, "cropY": win.cropY, "cropW": win.cropW, "cropH": win.cropH,
            "geoV": geoVSlider.value, "geoH": geoHSlider.value, "geoScale": geoScaleSlider.value
        }
        // 마스킹(선택 클래스 + 로컬 조정) 병합. 마스크 픽셀은 저장 안 함 — 로드 시 클래스로 재생성.
        var sk = win.skyEditParams()
        for (var k in sk) o[k] = sk[k]
        return o
    }
    function _ev(p, k, d) { return p[k] !== undefined ? p[k] : d }

    // 저장된 편집을 컨트롤에 복원. 반드시 _applying 가드 안에서 호출(자동저장/WB 재디코딩 방지).
    function applyEdits(p) {
        expSlider.value = _ev(p, "exposure", 0.0); conSlider.value = _ev(p, "contrast", 1.0)
        hiSlider.value = _ev(p, "highlights", 0.0); shSlider.value = _ev(p, "shadows", 0.0)
        whSlider.value = _ev(p, "whites", 0.0); blSlider.value = _ev(p, "blacks", 0.0)
        tempSlider.value = _ev(p, "temp", controller.asShotKelvin)
        tintSlider.value = _ev(p, "tint", controller.asShotTint)
        // 필름시뮬 복원: simKey(문자열) 우선 → 현재 목록에서 인덱스 역추적(없으면 None). 구버전은 simIndex.
        var _sk = _ev(p, "simKey", "")
        var _si
        if (_sk !== "") { _si = win.simKeys.indexOf(_sk); if (_si < 0) _si = 0 }   // 목록에 없는 LUT → None
        else { _si = _ev(p, "simIndex", 0); if (_si < 0 || _si >= win.simKeys.length) _si = 0 }
        simCombo.currentIndex = _si
        simStrengthSlider.value = _ev(p, "simStrength", 1.0)
        texSlider.value = _ev(p, "texture", 0.0); claritySlider.value = _ev(p, "clarity", 0.0)
        dehazeSlider.value = _ev(p, "dehaze", 0.0)
        vibSlider.value = _ev(p, "vibrance", 0.0); satSlider.value = _ev(p, "saturation", 0.0)
        win.hslH = _ev(p, "hslH", [0,0,0,0,0,0,0,0]).slice()
        win.hslS = _ev(p, "hslS", [0,0,0,0,0,0,0,0]).slice()
        win.hslL = _ev(p, "hslL", [0,0,0,0,0,0,0,0]).slice()
        cgShHueSlider.value = _ev(p, "cgShadowHue", 0.0); cgShSatSlider.value = _ev(p, "cgShadowSat", 0.0)
        cgMidHueSlider.value = _ev(p, "cgMidHue", 0.0); cgMidSatSlider.value = _ev(p, "cgMidSat", 0.0)
        cgHiHueSlider.value = _ev(p, "cgHighHue", 0.0); cgHiSatSlider.value = _ev(p, "cgHighSat", 0.0)
        cgBalanceSlider.value = _ev(p, "cgBalance", 0.0)
        hslHueSlider.value = win.hslH[win.hslBand]
        hslSatSlider.value = win.hslS[win.hslBand]
        hslLumSlider.value = win.hslL[win.hslBand]
        vignetteSlider.value = _ev(p, "vignette", 0.0)
        grainSlider.value = _ev(p, "grainAmt", 0.0); grainSizeSlider.value = _ev(p, "grainSize", 0.5)
        controller.setStampGrainSrc(grainSlider.value)   // 스탬프 그레인 연동(프리뷰)
        sharpAmtSlider.value = _ev(p, "sharpenAmt", 0.0); sharpRadiusSlider.value = _ev(p, "sharpenRadius", 1.0)
        sharpDetailSlider.value = _ev(p, "sharpenDetail", 0.25); sharpMaskSlider.value = _ev(p, "sharpenMask", 0.0)
        lumaNrSlider.value = _ev(p, "lumaNR", 0.0); colorNrSlider.value = _ev(p, "colorNR", 0.0)
        // AI 디노이즈: 프로그램적 checked 변경은 onToggled 미발화 → 명시 전달.
        // 켜져 있으면 requestAiNr(비대화형) 경유 — GPU 면 즉시, CPU 폴백이면 세션 선택 정책
        // (미선택=1회 질문, no=자동 해제, yes=진행). 로드 직후엔 가이디드 베이스로 동작.
        aiNrCheck.checked = _ev(p, "aiNr", false)
        if (aiNrCheck.checked) win.requestAiNr(false)
        else controller.setAiNr(false)
        win.dateStamp = _ev(p, "dateStamp", false)
        stampField.text = _ev(p, "stampText", controller.stampText)
        // 프로그램으로 text 를 바꾸면 onTextEdited 가 안 불리므로 직접 push(스탬프 렌더 갱신).
        controller.setStampText(stampField.text)
        controller.setStampFont(_ev(p, "stampStyle", "7c_bold"))
        var _sz = _ev(p, "stampSize", 0.032)
        if (typeof _sz === "string") _sz = ({S: 0.024, M: 0.032, L: 0.044})[_sz] || 0.032  // 구 사이드카 호환
        stampSizeSlider.value = _sz
        controller.setStampSize(_sz)
        var _mg = _ev(p, "stampMargin", 0.05);    stampMarginSlider.value = _mg; controller.setStampMargin(_mg)
        // 체크박스도 명시 대입(aiNrCheck 동일) — 사용자가 한 번이라도 클릭하면
        // `checked: controller.lensCorrection` 바인딩이 파괴되어, 이후 사이드카 복원이
        // 박스에 반영되지 않고 낡은 값이 자동저장으로 역전파되던 버그 방지.
        lensCheck.checked = _ev(p, "lensCorrection", true)
        controller.setLensCorrection(lensCheck.checked)
        var cp = _ev(p, "curves", null)
        if (cp) { curveEditor.setChannelPoints(cp); controller.setCurve(curveEditor.allLuts()) }
        else curveEditor.resetAll()
        win.quarterTurns = _ev(p, "quarterTurns", 0); rotAngleSlider.value = _ev(p, "rotateAngle", 0.0)
        flipHBtn.checked = _ev(p, "flipH", false); flipVBtn.checked = _ev(p, "flipV", false)
        var land = _ev(p, "cropLandscape", true)
        cropLandscapeBtn.checked = land; cropPortraitBtn.checked = !land
        aspectCombo.currentIndex = _ev(p, "aspectIndex", 0)
        win.setCropRect(_ev(p,"cropX",0.0), _ev(p,"cropY",0.0), _ev(p,"cropW",1.0), _ev(p,"cropH",1.0))
        geoVSlider.value = _ev(p, "geoV", 0); geoHSlider.value = _ev(p, "geoH", 0)
        geoScaleSlider.value = _ev(p, "geoScale", 100)
        win.applySkyEdits(p)   // 마스킹(선택 클래스 + 조정) 복원 — 마스크는 클래스로부터 재생성
    }

    // 하늘(로컬) 조정 초기화 — 슬라이더 + 마스크 + 오버레이. 새 파일 로드/Reset 에서 호출.
    function resetSky() {
        skyExpSlider.value = 0.0; skyTempSlider.value = 0.0; skyTintSlider.value = 0.0
        skySatSlider.value = 0.0; skyHiSlider.value = 0.0; skyShadowsSlider.value = 0.0
        skyTextureSlider.value = 0.0; skyClaritySlider.value = 0.0; skyDehazeSlider.value = 0.0
        skyContrastSlider.value = 1.0
        skyInvertCheck.checked = false
        win.showSkyMask = false
        win.maskKeys = []
        win.maskPoint = null
        win.samSelectMode = false
        controller.clearSky()
    }

    // 전체 초기화(편집 + 지오메트리). 수동 Reset 버튼 & 저장본 없는 파일 로드에서 호출.
    function resetAllEdits() {
        expSlider.value = 0.0; conSlider.value = 1.0
        hiSlider.value = 0.0; shSlider.value = 0.0; whSlider.value = 0.0; blSlider.value = 0.0
        texSlider.value = 0.0; claritySlider.value = 0.0; dehazeSlider.value = 0.0
        satSlider.value = 0.0; vibSlider.value = 0.0
        win.resetHsl(); hslHueSlider.value = 0.0; hslSatSlider.value = 0.0; hslLumSlider.value = 0.0
        cgShHueSlider.value = 0.0; cgShSatSlider.value = 0.0; cgMidHueSlider.value = 0.0
        cgMidSatSlider.value = 0.0; cgHiHueSlider.value = 0.0; cgHiSatSlider.value = 0.0
        cgBalanceSlider.value = 0.0
        sharpAmtSlider.value = 0.0; sharpRadiusSlider.value = 1.0
        sharpDetailSlider.value = 0.25; sharpMaskSlider.value = 0.0
        lumaNrSlider.value = 0.0; colorNrSlider.value = 0.0
        aiNrCheck.checked = false; controller.setAiNr(false)
        vignetteSlider.value = 0.0; grainSlider.value = 0.0; grainSizeSlider.value = 0.5
        controller.setStampGrainSrc(0.0)
        tempSlider.value = controller.asShotKelvin; tintSlider.value = controller.asShotTint
        simCombo.currentIndex = 0; simStrengthSlider.value = 1.0
        // 날짜 스탬프/렌즈 보정도 초기화 — 누락 시 이전 사진의 상태가 무편집 사진으로
        // 누수되고(editParams 는 저장하는데 reset 은 안 지움), Reset 버튼으로도 안 지워졌음.
        win.dateStamp = false
        stampField.text = controller.stampText
        controller.setStampText(stampField.text)
        controller.setStampFont("7c_bold")
        stampSizeSlider.value = 0.032
        controller.setStampSize(0.032)
        stampMarginSlider.value = 0.05; controller.setStampMargin(0.05)
        lensCheck.checked = true
        controller.setLensCorrection(true)
        curveEditor.resetAll()
        win.resetGeometry()
        win.resetSky()
    }

    // 수동 Reset 버튼: 모든 편집 초기화 + 사이드카 삭제(+썸네일 파일명 앰버 해제).
    // 자동저장(editSaveWatch→editSaveTimer)이 기본값 사이드카를 다시 만들지 않도록 _applying 으로
    // 감싸고(변경 onChanged 동기 억제) 보류 중 저장 타이머도 멈춘다. _applying 중 막힌 WB/커브는
    // paste/undo 와 동일하게 직접 반영. 리셋 상태는 undo 스텝으로 push(되돌리면 사이드카 복원).
    function resetAndClearEdits() {
        win._applying = true
        win.resetAllEdits()
        win._applying = false
        editSaveTimer.stop()                              // 보류 중 자동저장 취소(기본값 재생성 방지)
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.deleteEdits()                          // 사이드카 삭제 + 썸네일 배지(파일명 앰버) 해제
        win.refreshHistogram()
        win.histPush(JSON.stringify(win.editParams()))    // 리셋 상태 = undo 스텝(undo 시 편집 복원)
    }

    // ===== 편집 복사/붙여넣기 (이미지 간) =====
    // 클립보드는 editParams 스냅샷(JSON 딥카피 — 이후 원본 편집 변경에 영향 안 받게).
    property var _editClipboard: null
    // excludeWb=true 면 temp/tint 를 뺀 스냅샷 → 붙여넣을 때 대상의 WB 유지.
    // 사진별 고유 항목은 복사에서 제외 → 붙여넣을 때 대상 이미지의 값이 유지됨.
    // (date stamp + geometry. WB·Tint 는 excludeWb 일 때 추가 제외)
    readonly property var _copyExclude: ["dateStamp", "stampText",
        "quarterTurns", "rotateAngle", "flipH", "flipV", "aspectIndex", "cropLandscape",
        "cropX", "cropY", "cropW", "cropH", "geoV", "geoH", "geoScale"]
    function copyEdits(excludeWb) {
        if (controller.imagePath === "") return
        var snap = JSON.parse(JSON.stringify(win.editParams()))
        var ex = win._copyExclude.slice()
        if (excludeWb) { ex.push("temp"); ex.push("tint") }
        for (var i = 0; i < ex.length; i++) delete snap[ex[i]]
        _editClipboard = snap
    }
    function pasteEdits() {
        if (!_editClipboard || controller.imagePath === "") return
        // 현재 이미지 편집값을 기준으로, 클립보드에 담긴 항목만 덮어씀 →
        // 복사에서 제외된 항목(date stamp·geometry·WB)은 대상 값 그대로 유지.
        var p = win.editParams()
        for (var k in _editClipboard) p[k] = _editClipboard[k]
        win._applying = true
        win.applyEdits(p)
        win._applying = false
        // _applying 중엔 WB 커밋이 막히므로 직접 반영(export 가 쓰는 _kelvin/_tint 갱신).
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.saveEdits(win.editParams())   // 붙여넣은 편집을 현재 이미지 사이드카에 저장
        win.refreshHistogram()
        win.histPush(JSON.stringify(win.editParams()))   // undo 스텝 기록(붙여넣기 되돌리기 가능)
    }

    // ===== 배치 export (탐색기 체크박스로 선택한 파일들, 순차) =====
    // 기존 단일 흐름을 파일마다 그대로 재사용: loadPath(사이드카 WB 선설정·디코딩)
    // → editsReady(편집 복원 or 기본값·마스크 재생성) → exportParams() → exportImage(CPU).
    // 별도 파라미터 재구성 경로가 없어 프리뷰=Export 정합이 단일 export 와 동일하게 유지.
    // 편집 없는 파일은 기존 핸들러가 기본값으로 초기화 → 기본 현상으로 export 됨.
    property bool batchSelectMode: false      // 탐색기 체크박스 모드 토글
    property var batchChecked: ({})           // path -> true (체크된 파일)
    property int batchCheckedRev: 0           // 변경 리비전(카운트/체크표시 재평가용)
    readonly property int batchCheckedCount: { batchCheckedRev; return Object.keys(batchChecked).length }
    function batchToggle(path) {
        if (batchChecked[path]) delete batchChecked[path]
        else batchChecked[path] = true
        batchCheckedRev++
    }
    function batchClearChecked() { batchChecked = ({}); batchCheckedRev++ }

    property bool batchActive: false
    property var batchQueue: []
    property int batchIndex: 0
    property int batchFails: 0
    property bool batchCancel: false          // 요청 시 현재 파일까지만 하고 중단
    property string batchDestUrl: ""          // 저장 폴더(QUrl 문자열)
    property string batchExt: "jpg"
    // 단계: 1=디코딩/복원 대기(editsReady) → 2=마스크/재디코딩 대기 → 3=export 완료 대기
    property int batchPhase: 0
    property real batchPhaseT0: 0
    property string batchResult: ""           // 완료 요약("Batch: 5 saved, 1 failed")

    function batchStart(destUrl, ext) {
        if (win.batchActive) return
        var q = Object.keys(win.batchChecked).sort()
        if (q.length === 0) return
        win.batchQueue = q; win.batchIndex = 0; win.batchFails = 0
        win.batchCancel = false; win.batchDestUrl = destUrl; win.batchExt = ext
        win.batchResult = ""
        win.batchActive = true
        win.batchLoadNext()
    }
    function batchLoadNext() {
        if (win.batchCancel || win.batchIndex >= win.batchQueue.length) { win.batchFinish(); return }
        win.batchPhase = 1; win.batchPhaseT0 = Date.now()
        controller.loadPath(win.batchQueue[win.batchIndex])
    }
    function batchFinish() {
        var attempted = win.batchIndex
        var saved = attempted - win.batchFails
        win.batchActive = false; win.batchPhase = 0
        win.batchResult = "Batch: " + saved + " saved"
                        + (win.batchFails > 0 ? ", " + win.batchFails + " failed" : "")
                        + (win.batchCancel ? " (cancelled)" : "")
    }
    // editsReady = 이 파일의 복원 완료 신호. 위 메인 핸들러(편집 복원)가 같은 시그널로 먼저
    // 실행되므로 callLater 로 그 뒤에 단계 전환(선언 순서 의존 제거).
    Connections {
        target: controller
        function onEditsReady() {
            if (win.batchActive && win.batchPhase === 1)
                Qt.callLater(function() { win.batchPhase = 2; win.batchPhaseT0 = Date.now() })
        }
    }
    Timer {
        id: batchTick
        interval: 250; repeat: true
        running: win.batchActive
        onTriggered: {
            var waited = Date.now() - win.batchPhaseT0
            if (win.batchPhase === 1) {
                // 디코딩 실패 등으로 editsReady 가 안 오면 30초 후 실패 처리하고 다음으로.
                if (waited > 30000) { win.batchFails++; win.batchIndex++; win.batchLoadNext() }
            } else if (win.batchPhase === 2) {
                // 재디코딩(WB/렌즈)·마스크 재생성(세그) 완료 대기. 마스크가 있어야 하는데
                // 20초 내 안 오면(세그 실패) 마스크 없이 진행(단일 export 와 동일 폴백).
                var maskPending = win.maskKeys.length > 0 && !controller.hasSkyMask
                if (!controller.busy && !controller.skyBusy && (!maskPending || waited > 20000)) {
                    var url = controller.batchExportUrl(
                        win.batchDestUrl, win.batchQueue[win.batchIndex], win.batchExt)
                    if (url === "") { win.batchFails++; win.batchIndex++; win.batchLoadNext(); return }
                    controller.exportImage(url, win.exportParams())
                    if (!controller.exporting) {   // 슬롯 가드에 걸림(비정상) → 실패 처리
                        win.batchFails++; win.batchIndex++; win.batchLoadNext(); return
                    }
                    win.batchPhase = 3; win.batchPhaseT0 = Date.now()
                }
            } else if (win.batchPhase === 3) {
                if (!controller.exporting) {
                    if (controller.exportStatus.indexOf("Saved:") !== 0) win.batchFails++
                    win.batchIndex++
                    win.batchLoadNext()
                }
            }
        }
    }

    // ===== Undo / Redo (편집 스냅샷 스택) =====
    // editParams() JSON 스냅샷을 쌓는다. 자동저장(editSaveTimer 디바운스) 시점마다 1개 push
    // → 슬라이더 드래그 1회 = 1 스텝(중간 프레임 무시). 새 파일 로드 시 baseline 으로 리셋.
    property var undoHist: []           // JSON 문자열 배열
    property int undoPos: -1            // 현재 상태 인덱스
    readonly property bool canUndo: undoPos > 0
    readonly property bool canRedo: undoPos >= 0 && undoPos < undoHist.length - 1

    function histReset(snapStr) { win.undoHist = [snapStr]; win.undoPos = 0 }
    function histPush(snapStr) {
        if (win.undoPos >= 0 && win.undoHist[win.undoPos] === snapStr) return   // 변화 없음
        var h = win.undoHist.slice(0, win.undoPos + 1)                          // redo 꼬리 버림
        h.push(snapStr)
        if (h.length > 100) h = h.slice(h.length - 100)                         // 상한
        win.undoHist = h; win.undoPos = h.length - 1
    }
    // 스냅샷 적용(undo/redo 공통) — paste 와 동일 경로: _applying 가드로 자동저장/WB 재디코딩
    // 억제 후 WB·커브 직접 반영 + 사이드카 저장 + 히스토그램 갱신.
    function applySnapshot(snapStr) {
        var p = JSON.parse(snapStr)
        win._applying = true
        win.applyEdits(p)
        win._applying = false
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.saveEdits(win.editParams())
        win.refreshHistogram()
    }
    function undo() { if (win.canUndo) { win.undoPos = win.undoPos - 1; win.applySnapshot(win.undoHist[win.undoPos]) } }
    function redo() { if (win.canRedo) { win.undoPos = win.undoPos + 1; win.applySnapshot(win.undoHist[win.undoPos]) } }

    // 자동저장: 편집 변화를 단일 바인딩(editSaveWatch)으로 감지 → 디바운스 후 1회 저장.
    function scheduleSave() {
        if (win._applying || controller.imagePath === "") return
        editSaveTimer.restart()
    }
    // 편집 커밋(사이드카 저장 + undo 스텝 push). 드래그 진행 중에는 보류하고 릴리즈 시점에
    // 1회 커밋 — 느린 드래그 중 디바운스가 여러 번 만료돼 undo 스텝이 쪼개지는 것 방지
    // (드래그 1회 = 스텝 1개 보장).
    function commitEditSnapshot() {
        if (win._applying || controller.imagePath === "") return
        if (win.editDragActive) { editSaveTimer.restart(); return }   // 드래그 중 → 릴리즈 후
        var snap = win.editParams()
        controller.saveEdits(snap)
        win.histPush(JSON.stringify(snap))   // 커밋된 편집 1개 = undo 스텝 1개
    }
    // 드래그 진행 중 여부 — 편집에 관여하는 모든 드래그 소스를 **명시적으로 열거**(결정론적).
    // 과거 전역 PointHandler(패시브 감시)만으로는 일부 컨트롤의 press 를 이벤트 전달 경로에 따라
    // 놓칠 수 있었음 → 컨트롤들의 pressed 를 직접 참조. PointHandler 는 보조 안전망으로 유지.
    readonly property bool editDragActive:
        globalPress.active
        || expSlider.pressed || conSlider.pressed || hiSlider.pressed || shSlider.pressed
        || whSlider.pressed || blSlider.pressed || tempSlider.pressed || tintSlider.pressed
        || simStrengthSlider.pressed || texSlider.pressed || claritySlider.pressed
        || dehazeSlider.pressed || vibSlider.pressed || satSlider.pressed
        || hslHueSlider.pressed || hslSatSlider.pressed || hslLumSlider.pressed
        || cgShHueSlider.pressed || cgShSatSlider.pressed || cgMidHueSlider.pressed
        || cgMidSatSlider.pressed || cgHiHueSlider.pressed || cgHiSatSlider.pressed
        || cgBalanceSlider.pressed || vignetteSlider.pressed || grainSlider.pressed
        || grainSizeSlider.pressed || sharpAmtSlider.pressed || sharpRadiusSlider.pressed
        || sharpDetailSlider.pressed || sharpMaskSlider.pressed || lumaNrSlider.pressed
        || colorNrSlider.pressed || rotAngleSlider.pressed || geoVSlider.pressed
        || geoHSlider.pressed || geoScaleSlider.pressed
        || skyExpSlider.pressed || skyTempSlider.pressed || skyTintSlider.pressed
        || skySatSlider.pressed || skyHiSlider.pressed || skyShadowsSlider.pressed
        || skyTextureSlider.pressed || skyClaritySlider.pressed || skyDehazeSlider.pressed
        || skyContrastSlider.pressed
        || stampSizeSlider.pressed || stampMarginSlider.pressed
        || curveEditor.dragging || cropOverlay.dragging
    // 릴리즈 순간(어떤 소스든 드래그 종료) 보류 중 커밋이 있으면 즉시 실행 — 릴리즈 = undo 스텝.
    // + 드래그 상태를 컨트롤러에 전달 — AI 디노이즈 타일 루프가 조작 중 일시정지(버벅임 제거).
    onEditDragActiveChanged: {
        controller.setUiBusy(editDragActive)
        if (!editDragActive && editSaveTimer.running) {
            editSaveTimer.stop()
            win.commitEditSnapshot()
        }
    }
    // 전역 프레스 감시(패시브) — 열거에서 빠진 미래의 드래그 소스에 대한 안전망.
    PointHandler {
        id: globalPress
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    }
    Timer {
        id: editSaveTimer
        interval: 500
        onTriggered: win.commitEditSnapshot()
    }
    // 모든 편집 컨트롤 값을 참조 → 무엇이든 바뀌면 바인딩 재평가 → onChanged 로 저장 예약.
    property var editSaveWatch: [
        expSlider.value, conSlider.value, hiSlider.value, shSlider.value, whSlider.value, blSlider.value,
        tempSlider.value, tintSlider.value, simCombo.currentIndex, simStrengthSlider.value,
        texSlider.value, claritySlider.value, dehazeSlider.value, vibSlider.value, satSlider.value,
        win.hslH, win.hslS, win.hslL,
        cgShHueSlider.value, cgShSatSlider.value, cgMidHueSlider.value, cgMidSatSlider.value,
        cgHiHueSlider.value, cgHiSatSlider.value, cgBalanceSlider.value,
        vignetteSlider.value, grainSlider.value, grainSizeSlider.value,
        sharpAmtSlider.value, sharpRadiusSlider.value, sharpDetailSlider.value, sharpMaskSlider.value,
        lumaNrSlider.value, colorNrSlider.value, aiNrCheck.checked,
        lensCheck.checked, win.dateStamp, stampField.text,
        controller.stampFont, controller.stampSize, controller.stampMargin,
        curveEditor.channelPoints,
        win.quarterTurns, rotAngleSlider.value, flipHBtn.checked, flipVBtn.checked,
        aspectCombo.currentIndex, cropLandscapeBtn.checked,
        win.cropX, win.cropY, win.cropW, win.cropH,
        geoVSlider.value, geoHSlider.value, geoScaleSlider.value,
        JSON.stringify(win.skyEditParams())   // 마스킹 값 변경 추적(함수 내부 프로퍼티 읽기까지 추적됨)
    ]
    onEditSaveWatchChanged: win.scheduleSave()

    // 히스토그램 갱신 watcher: 색 단계(채도/바이브런스/HSL/컬러그레이딩)+비네팅이 바뀌면 재계산.
    // (노출/톤/대비/커브 슬라이더는 자체 onMoved 로 이미 refreshHistogram 호출함)
    property var histWatch: [
        satSlider.value, vibSlider.value, win.hslH, win.hslS, win.hslL,
        cgShHueSlider.value, cgShSatSlider.value, cgMidHueSlider.value, cgMidSatSlider.value,
        cgHiHueSlider.value, cgHiSatSlider.value, cgBalanceSlider.value, vignetteSlider.value
    ]
    onHistWatchChanged: win.refreshHistogram()

    // Export 파라미터(현상 전효과 + 지오메트리 + 해상도). CPU/GPU export 공용.
    function exportParams() {
        var o = {
            "exposure": expSlider.value, "contrast": conSlider.value,
            "highlights": hiSlider.value, "shadows": shSlider.value,
            "whites": whSlider.value, "blacks": blSlider.value,
            "texAmt": texSlider.value, "clarity": claritySlider.value, "dehaze": dehazeSlider.value,
            "saturation": satSlider.value, "vibrance": vibSlider.value,
            "hslH": win.hslH, "hslS": win.hslS, "hslL": win.hslL,
            "cgShadowHue": cgShHueSlider.value, "cgShadowSat": cgShSatSlider.value,
            "cgMidHue": cgMidHueSlider.value, "cgMidSat": cgMidSatSlider.value,
            "cgHighHue": cgHiHueSlider.value, "cgHighSat": cgHiSatSlider.value,
            "cgBalance": cgBalanceSlider.value,
            "sharpenAmt": sharpAmtSlider.value, "sharpenRadius": sharpRadiusSlider.value,
            "sharpenDetail": sharpDetailSlider.value, "sharpenMask": sharpMaskSlider.value,
            "lumaNR": lumaNrSlider.value, "colorNR": colorNrSlider.value, "aiNr": aiNrCheck.checked,
            "vignette": vignetteSlider.value, "grainAmt": grainSlider.value, "grainSize": grainSizeSlider.value,
            "lutEnabled": simCombo.currentIndex !== 0, "simKey": win.simKeys[simCombo.currentIndex],
            "lutStrength": simStrengthSlider.value, "curves": curveEditor.allLuts(),
            "dateStamp": win.dateStamp, "stampText": stampField.text, "stampRot": controller.stampRot,
            "stampStyle": controller.stampFont, "stampSize": controller.stampSize,
            "stampMargin": controller.stampMargin,
            "outEdge": win.exportEdges[resCombo.currentIndex], "lensCorrection": lensCheck.checked,
            "bitDepth": bitDepth16Check.checked ? 16 : 8,   // 16=TIFF/PNG 16bit(CPU 전용)
            // 지오메트리(현상 뒤 적용): 플립 -> 90° -> 스트레이튼(회전+채움줌) -> 종횡비 중앙크롭
            "flipH": flipHBtn.checked, "flipV": flipVBtn.checked,
            "quarterTurns": win.quarterTurns, "rotateAngle": rotAngleSlider.value,
            "cropX": win.cropX, "cropY": win.cropY, "cropW": win.cropW, "cropH": win.cropH,
            "geoV": geoVSlider.value, "geoH": geoHSlider.value, "geoScalePct": geoScaleSlider.value
        }
        // 하늘(로컬) 조정 병합 — CPU render_full 이 보관된 마스크(controller._sky_mask)와 함께 적용.
        var sk = win.skyEditParams()
        for (var k in sk) o[k] = sk[k]
        return o
    }

    // (앱 종료 시 편집 플러시 저장은 quitDialog 확인 후 onAccepted 에서 수행)

    // 탐색기 "좋아요만 보기" 필터 (L 키로 토글)
    property bool showLikedOnly: false
    property string _revealAfterUnfilter: ""   // 좋아요만 보기 해제 시 스크롤 복원할 선택 경로
    // 좋아요만 보기 토글. 해제(→일반 모드) 시 현재 선택 항목 경로를 목록 재평가 전에 확보해 두고,
    // onShowLikedOnlyChanged 에서 갱신된 목록 기준으로 그 항목까지 스크롤한다.
    function toggleLikedOnly() {
        if (win.showLikedOnly) {   // 켜짐 → 꺼짐: 선택 항목(하이라이트 우선, 없으면 열린 이미지) 확보
            var sel = ""
            if (fileListView.currentIndex >= 0 && win.explorerFiles[fileListView.currentIndex])
                sel = win.explorerFiles[fileListView.currentIndex].path
            if (!sel) sel = controller.imagePath
            win._revealAfterUnfilter = sel
        }
        win.showLikedOnly = !win.showLikedOnly
    }
    onShowLikedOnlyChanged: {
        if (win.showLikedOnly) { win._revealAfterUnfilter = ""; return }
        var sel = win._revealAfterUnfilter
        win._revealAfterUnfilter = ""
        if (sel)
            Qt.callLater(function() { win.selectInExplorer(sel) })   // 목록 바인딩 갱신 뒤 스크롤
    }
    Shortcut { sequence: "L"; enabled: !win._typing; onActivated: win.toggleLikedOnly() }
    // H = 폴더 태그 워드 클라우드 토글(열기/닫기). 폴더가 있어야 열림.
    Shortcut {
        sequence: "H"; enabled: !win._typing
        onActivated: {
            if (win.showTagCloud) win.showTagCloud = false
            else if (controller.currentFolder !== "") win.openTagCloud()
        }
    }
    // 필터 적용된 표시 목록: 좋아요만 보기면 폴더(탐색용) + 좋아요된 RAW 만.
    //  - controller.fileList(1회만 마샬링)·likeRevision·showLikedOnly 변경 시 자동 재평가
    property var explorerFiles: {
        controller.likeRevision               // 좋아요 토글 시 재평가용 의존
        controller.searchQuery                // 캡션 검색어 변경 시 재평가용 의존
        var files = controller.fileList        // folderChanged 시 재평가 + 1회만 읽기
        var q = controller.searchQuery
        if (!win.showLikedOnly && q === "")
            return files
        var out = []
        for (var i = 0; i < files.length; i++) {
            var it = files[i]
            if (it.isDir) { out.push(it); continue }        // 폴더는 항상 표시(탐색용)
            if (win.showLikedOnly && !controller.isLiked(it.path)) continue
            if (q !== "" && !controller.matchesSearch(it.path)) continue
            out.push(it)
        }
        return out
    }

    // Export 해상도 프리셋(긴 변 px, 0=원본). resCombo 모델 순서와 일치.
    readonly property var exportEdges: [0, 4096, 3840, 2560, 2048, 1920, 1280]

    // 콤보 인덱스 -> luts/<key>.cube 파일명. 0(identity)=필름시뮬 미적용.
    // controller.filmSims(=luts/*.cube 존재하는 것만)에서 파생 → 흑백 등 .cube 넣으면 자동 노출.
    readonly property var simKeys: {
        var k = []; var sims = controller.filmSims
        for (var i = 0; i < sims.length; i++) k.push(sims[i].key)
        return k
    }
    readonly property var simLabels: {
        var l = []; var sims = controller.filmSims
        for (var i = 0; i < sims.length; i++) l.push(sims[i].label)
        return l
    }

    function planckXY(T) {
        var x
        if (T < 4000) x = -0.2661239e9/(T*T*T) - 0.2343589e6/(T*T) + 0.8776956e3/T + 0.179910
        else          x = -3.0258469e9/(T*T*T) + 2.1070379e6/(T*T) + 0.2226347e3/T + 0.240390
        var y
        if (T < 2222)      y = -1.1063814*x*x*x - 1.34811020*x*x + 2.18555832*x - 0.20219683
        else if (T < 4000) y = -0.9549476*x*x*x - 1.37418593*x*x + 2.09137015*x - 0.16748867
        else               y =  3.0817580*x*x*x - 5.87338670*x*x + 3.75112997*x - 0.37001483
        return [x, y]
    }
    function planckCam(T) {
        var xy = planckXY(T)
        var X = xy[0]/xy[1], Y = 1.0, Z = (1.0 - xy[0] - xy[1])/xy[1]
        var m = controller.camMatrix
        return [Math.max(m[0]*X+m[1]*Y+m[2]*Z, 1e-6),
                Math.max(m[3]*X+m[4]*Y+m[5]*Z, 1e-6),
                Math.max(m[6]*X+m[7]*Y+m[8]*Z, 1e-6)]
    }
    function userWb(K, t) {           // wb.py compute_user_wb 와 동일
        var pr = planckCam(wbTRef), pc = planckCam(K), ref = controller.daylightRef
        var m = [ref[0]*pr[0]/pc[0], ref[1]*pr[1]/pc[1], ref[2]*pr[2]/pc[2]]
        m[0] /= m[1]; m[2] /= m[1]; m[1] = 1.0
        m[1] *= (1.0 - 0.3 * t)
        return m
    }
    function wbPreview(targetK, targetT) {   // baked->target 상대 게인
        var m = controller.camMatrix
        if (!m || m.length < 9) return Qt.vector3d(1, 1, 1)
        var t = userWb(targetK, targetT)
        var b = userWb(controller.bakedKelvin, controller.bakedTint)
        var g = [t[0]/b[0], t[1]/b[1], t[2]/b[2]]
        g[0] /= g[1]; g[2] /= g[1]; g[1] = 1.0    // green 정규화(노출 보존)
        return Qt.vector3d(g[0], g[1], g[2])
    }

    // 카메라 네이티브 -> 선형 sRGB 매트릭스(행우선 9개). 로드 전엔 identity.
    readonly property var camM: (controller.camToSrgb && controller.camToSrgb.length >= 9)
                                ? controller.camToSrgb : [1,0,0, 0,1,0, 0,0,1]
    // dispSrc(블러 base + 원본 비교)용 as-shot WB 상대게인(TREF 대비).
    // ⚠️as-shot tint 도 포함해야 함 — pipe 의 기본 WB(tempSlider=asShotKelvin,
    //   tintSlider=asShotTint)와 일치(편집 없을 때 원본=편집본). off-locus 광원(tint≠0)에서
    //   tint=0 으로 두면 색끼 차이 발생.
    readonly property vector3d asShotRelGain: win.wbPreview(controller.asShotKelvin, controller.asShotTint)

    // 슬라이더 더블클릭 리셋: press 중에는 Slider 가 value 를 커서 위치로 덮어쓰므로
    // press 시점엔 '더블 여부'만 판정하고, 실제 리셋은 release 때 수행한다(아래 슬라이더들).
    // 두 번째 press 가 400ms 안이면 true.
    function isDblPress(slider) {
        var now = Date.now()
        var dbl = (now - slider._lastPressMs < 400)
        slider._lastPressMs = dbl ? 0 : now
        return dbl
    }

    // 비-드래그(키보드) WB 변경 커밋용 디바운스.
    Timer {
        id: wbTimer
        interval: 150
        onTriggered: controller.setWb(tempSlider.value, tintSlider.value)
    }

    // 톤커브 배경 히스토그램 재계산(스로틀). 드래그 중 주기적 갱신(메인 스레드 부담 완화).
    Timer {
        id: histTimer
        interval: 130
        onTriggered: controller.updateHistogram(win.curParams())
    }
    // 스로틀: 실행 중이 아니면 시작 -> 연속 드래그 중에도 interval 마다 갱신(디바운스와 달리 멈춤 없음).
    function refreshHistogram() { if (!histTimer.running) histTimer.start() }

    function curParams() {
        return {
            "exposure": expSlider.value, "contrast": conSlider.value,
            "highlights": hiSlider.value, "shadows": shSlider.value,
            "whites": whSlider.value, "blacks": blSlider.value,
            "lutEnabled": simCombo.currentIndex !== 0,
            "simKey": win.simKeys[simCombo.currentIndex],
            "lutStrength": simStrengthSlider.value,
            "curves": curveEditor.allLuts(),
            // 라이트룸식 전체 반영: 색 단계 + 비네팅(그레인 제외)
            "saturation": satSlider.value, "vibrance": vibSlider.value,
            "hslH": win.hslH, "hslS": win.hslS, "hslL": win.hslL,
            "cgShadowHue": cgShHueSlider.value, "cgShadowSat": cgShSatSlider.value,
            "cgMidHue": cgMidHueSlider.value, "cgMidSat": cgMidSatSlider.value,
            "cgHighHue": cgHiHueSlider.value, "cgHighSat": cgHiSatSlider.value,
            "cgBalance": cgBalanceSlider.value,
            "vignette": vignetteSlider.value
        }
    }

    // 새 파일 로드 시 추정된 as-shot 색온도로 Temp 슬라이더 초기화.
    Connections {
        target: controller
        function onAsShotKelvinChanged() {
            // 저장된 편집이 있는 파일은 복원될 WB 를 유지(as-shot 으로 덮어쓰지 않음).
            if (win._hasSavedEdits()) return
            // _applying 가드: as-shot 으로 슬라이더를 맞추는 것은 '편집'이 아니므로 자동저장
            // 예약(editSaveWatch→scheduleSave)·WB 재디코딩(wbTimer)을 억제 → 새 사진에 불필요한
            // 사이드카(주황 배지)가 생기지 않게 한다.
            win._applying = true
            tempSlider.value = controller.asShotKelvin
            tintSlider.value = controller.asShotTint   // off-locus(불빛 등) as-shot tint 반영
            win._applying = false
        }
        // 로드/WB 커밋(재디코딩)으로 프록시가 갱신되면 조절 반영 히스토그램 재계산.
        function onImageChanged() { win.refreshHistogram(); viewport.resetZoom() }
        // 이미지 전환 직전: 이전 파일(controller._ui_path)로 편집 플러시 저장.
        // ⚠️보류 중(editSaveTimer.running=미저장 변경 있음)일 때만 저장 — 그렇지 않으면 이미
        // 저장됐거나 reset 으로 삭제된 상태라, 무조건 저장하면 기본값 사이드카를 되살린다(주황 재발).
        function onFlushEdits() {
            if (editSaveTimer.running && controller.imagePath !== "")
                controller.saveEdits(win.editParams())
            editSaveTimer.stop()
            // 대기 중인 키보드 WB 커밋도 취소 — 아니면 사진 전환 직후 발화해 이전 파일의
            // Kelvin 을 새 파일에 setWb(잘못된 WB 재디코딩)로 밀어넣는다.
            wbTimer.stop()
        }
    }

    FolderDialog {
        id: folderDialog
        title: "Select Folder"
        onAccepted: controller.setFolder(selectedFolder)   // QUrl -> Python .toLocalFile()
    }

    // 배치 export 저장 폴더 선택 → 즉시 시작
    FolderDialog {
        id: batchDestDialog
        title: "Select Export Destination"
        onAccepted: win.batchStart(selectedFolder.toString(), batchFmtCombo.currentText)
    }

    // AI 디노이즈 CPU 폴백 선택: GPU EP(DirectML) 없을 때 느린 CPU 계산 진행 여부.
    // 세션 동안 선택 기억("yes"/"no") — 사이드카 복원(비대화형)은 기억된 선택을 그대로 따르고,
    // 수동 토글은 "no" 였어도 다시 묻는다(마음 바꿀 기회). ""=아직 안 물음.
    property string aiCpuChoice: ""
    function requestAiNr(interactive) {
        if (controller.aiNrGpuAvailable() || win.aiCpuChoice === "yes") {
            controller.setAiNr(true)
            return
        }
        if (!interactive && win.aiCpuChoice === "no") {
            aiNrCheck.checked = false      // 이 머신에선 안 쓰기로 함 → 편집값도 끔(export 일관)
            controller.setAiNr(false)
            return
        }
        aiCpuDialog.open()
    }

    // AI 디노이즈 CPU 폴백 확인 대화상자 (quitDialog 와 동일 컨셉 스타일)
    Popup {
        id: aiCpuDialog
        modal: true
        dim: true
        width: 380
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }
        property bool chosen: false
        onOpened: chosen = false
        // Esc 등 선택 없이 닫힘 = 이번만 취소(선택 기억 안 함)
        onClosed: if (!chosen) { aiNrCheck.checked = false; controller.setAiNr(false) }

        contentItem: ColumnLayout {
            spacing: 0
            FilmStrip {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                Layout.preferredHeight: 26
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.margins: 24
                spacing: 12
                Label {
                    text: "Run AI Denoise on CPU?"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "No GPU acceleration (DirectML) is available on this system.\nCPU is slow: preview ≈ 2 min, full-resolution export can take 15–20 min.\nYour choice is remembered for this session."
                    color: "#9a9a9a"; font.pixelSize: 13
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 12
                    Rectangle {        // No — AI 디노이즈 사용 안 함
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: aiCpuNoMA.containsMouse ? "#3a3a3d" : "#2e2e31"
                        border.color: "#55555a"; border.width: 1
                        Label { anchors.centerIn: parent; text: "No"; color: "#e6e6e6"; font.pixelSize: 13 }
                        MouseArea {
                            id: aiCpuNoMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                aiCpuDialog.chosen = true
                                win.aiCpuChoice = "no"
                                aiNrCheck.checked = false
                                controller.setAiNr(false)
                                aiCpuDialog.close()
                            }
                        }
                    }
                    Rectangle {        // Proceed (앰버 강조) — 느려도 CPU 로 진행
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: aiCpuYesMA.containsMouse ? "#f0b945" : "#E0A226"
                        Label { anchors.centerIn: parent; text: "Proceed"; color: "#1a1a1a"; font.pixelSize: 13; font.bold: true }
                        MouseArea {
                            id: aiCpuYesMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                aiCpuDialog.chosen = true
                                win.aiCpuChoice = "yes"
                                aiNrCheck.checked = true
                                controller.setAiNr(true)
                                aiCpuDialog.close()
                            }
                        }
                    }
                }
            }
            FilmStrip {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                Layout.preferredHeight: 26
            }
        }
    }

    // 종료 확인 대화상자 (앱 컨셉: 다크 + 필름 퍼포레이션 + 앰버 강조, 커스텀 스타일)
    Popup {
        id: quitDialog
        modal: true
        dim: true
        width: 380
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }

        function doQuit() {
            // 보류 중(editSaveTimer.running=미저장 변경 있음)일 때만 저장 — 편집이 전혀 없거나
            // reset 으로 삭제된 사진에 종료 시 기본값 사이드카가 생기지 않게 한다(주황 배지 오발 방지).
            if (editSaveTimer.running && controller.imagePath !== "")
                controller.saveEdits(win.editParams())
            editSaveTimer.stop()
            win.allowClose = true
            Qt.quit()
        }

        contentItem: ColumnLayout {
            spacing: 0

            // 상단 필름 퍼포레이션 스트립(앰버) — 대화상자 폭을 가득 채움(좌우 여백은 둥근 모서리 회피).
            FilmStrip {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                Layout.preferredHeight: 26
            }

            ColumnLayout {
                Layout.fillWidth: true
                // 위/아래 여백 동일(24) → 콘텐츠가 상·하 필름 스트립 사이 중앙에 위치(위쏠림 방지)
                Layout.margins: 24
                spacing: 12

                Label {
                    text: "Quit FILM RAWSTERY?"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "Your current edits are saved before exit."
                    color: "#9a9a9a"; font.pixelSize: 13
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 12

                    Rectangle {        // Cancel
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: cancelMA.containsMouse ? "#3a3a3d" : "#2e2e31"
                        border.color: "#55555a"; border.width: 1
                        Label { anchors.centerIn: parent; text: "Cancel"; color: "#e6e6e6"; font.pixelSize: 13 }
                        MouseArea {
                            id: cancelMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: quitDialog.close()
                        }
                    }
                    Rectangle {        // Quit (앰버 강조)
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: okMA.containsMouse ? "#f0b945" : "#E0A226"
                        Label { anchors.centerIn: parent; text: "Quit"; color: "#1a1a1a"; font.pixelSize: 13; font.bold: true }
                        MouseArea {
                            id: okMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: quitDialog.doQuit()
                        }
                    }
                }
            }

            // 하단 필름 퍼포레이션 스트립 — 상단과 대칭(필름 프레임)
            FilmStrip {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                Layout.preferredHeight: 26
            }
        }
    }

    // 프리뷰 모드 오버레이(탐색기에서 RAW 우클릭 → 메뉴 Preview 로 염). 메인 창 위를 꽉 덮음.
    // 닫으면 마지막으로 보던 사진을 탐색기에서 선택(하이라이트+스크롤)만 한다 — 로드는 안 함.
    PreviewWindow {
        id: previewWin
        onClosedAt: (path) => win.selectInExplorer(path)
    }

    // Alt+↑: 상위 폴더로 이동(Windows 탐색기 관례). 위로가기 버튼과 동일하게 직전 폴더 선택 유지.
    Shortcut {
        sequence: "Alt+Up"
        enabled: !win.batchActive && !previewWin.visible
        onActivated: {
            win._selectAfterScan = controller.currentFolder
            controller.goUp()
        }
    }

    // 탐색기 선택 항목 + Enter: 파일=프리뷰 진입, 폴더=진입. 텍스트 입력(날짜)·프리뷰 표시 중·
    // 배치 중에는 비활성(Enter 가 각자의 용도로 쓰이거나 조작 차단 상태).
    Shortcut {
        sequences: ["Return", "Enter"]
        enabled: win.showExplorer && fileListView.currentIndex >= 0
                 && !previewWin.visible && !stampField.activeFocus && !win.batchActive
        onActivated: {
            var it = win.explorerFiles[fileListView.currentIndex]
            if (!it) return
            if (it.isDir) controller.setFolderPath(it.path)
            else win.openPreview(it.path)
        }
    }

    // 탐색기에서 해당 경로 항목을 선택(하이라이트)하고 보이도록 스크롤. 없으면(필터 등) 무시.
    // 포커스도 리스트로 → 이어서 방향키 탐색 가능(위로가기/프리뷰 닫기 직후 흐름).
    function selectInExplorer(path, focus) {
        if (!path) return
        var files = win.explorerFiles
        for (var i = 0; i < files.length; i++) {
            if (files[i].path === path) {
                fileListView.currentIndex = i
                fileListView.positionViewAtIndex(i, ListView.Center)
                if (focus === undefined || focus)   // 검색 복원 등에선 focus=false(검색창 포커스 유지)
                    fileListView.forceActiveFocus()
                return
            }
        }
    }

    // 검색어 변경(입력/삭제) 처리: 모델(explorerFiles) 재평가로 currentIndex 가 다른 항목을
    // 가리켜 선택이 풀리는 것을 방지. 변경 전 선택 항목 경로를 확보 → 재평가 후 그 항목을 다시
    // 선택+가운데 스크롤(선택/페이징 유지). 검색창 포커스는 뺏지 않아 타이핑이 끊기지 않는다.
    function applySearch(text) {
        var sel = ""
        if (fileListView.currentIndex >= 0 && win.explorerFiles[fileListView.currentIndex])
            sel = win.explorerFiles[fileListView.currentIndex].path
        if (!sel) sel = controller.imagePath
        controller.setSearchQuery(text)
        if (sel)
            Qt.callLater(function() { win.selectInExplorer(sel, false) })   // 모델 갱신 뒤 복원(포커스 유지)
    }

    // 폴더 태그 워드 클라우드 — 열 때 현재 폴더 키워드 빈도를 집계해 담고, 폰트 스케일용 min/max 계산.
    property bool showTagCloud: false
    property var tagCloudData: []
    property int _tagMinCount: 1
    property int _tagMaxCount: 1
    property var likedTags: []               // ♥ 그룹: 좋아요 사진의 키워드
    property int _likedMin: 1
    property int _likedMax: 1
    property string _hoverTag: ""            // 호버 미리보기 대상 태그
    property string _pendingTag: ""          // 디바운스 대기 태그
    property var tagPreviewPaths: []         // 미리보기 썸네일 경로
    property var tagStats: ({})              // 헤더 통계 {photos, indexed, tags, liked}
    property bool _idxWasBusy: false         // 인덱싱 busy 이전 상태(완료 에지 감지용)
    // 인덱싱이 방금 끝났고(busy true→false) 팝업이 열려 있으면 최종 태그로 1회 자동 갱신.
    Connections {
        target: controller
        function onIndexChanged() {
            // 방금 끝났고(busy true→false) + 팝업 열림 + 끝난 폴더가 지금 보는 폴더일 때만 갱신
            // (다른 폴더가 끝났으면 현재 폴더 태그는 그대로라 재구성이 낭비).
            if (win._idxWasBusy && !controller.indexBusy && win.showTagCloud
                    && controller.indexFolder === controller.currentFolder)
                win.refreshTagCloud()
            win._idxWasBusy = controller.indexBusy
        }
    }
    function _minmax(arr) {
        var mn = 1000000, mx = 1
        for (var i = 0; i < arr.length; i++) { var c = arr[i].count; if (c < mn) mn = c; if (c > mx) mx = c }
        return [arr.length ? mn : 1, mx]
    }
    function openTagCloud() {
        win.showTagCloud = true
        win._loadTags(false)                           // 첫 오픈: 미리보기=최상위 단어
    }
    // 인덱싱 완료 시 1회 자동 갱신 — 팝업이 열려 있으면 최종 태그로 다시 채우되 보던 키워드는 유지.
    function refreshTagCloud() {
        if (win.showTagCloud) win._loadTags(true)
    }
    // 공용 로더. keepHover=true 면 현재 보던 키워드를 유지(새 데이터에도 있으면), 없으면 최상위.
    function _loadTags(keepHover) {
        var prev = win._hoverTag
        var kw = controller.folderKeywords(60)
        var m = win._minmax(kw); win._tagMinCount = m[0]; win._tagMaxCount = m[1]
        win.tagCloudData = kw
        var lk = controller.likedKeywords(40)          // ♥ 좋아요 그룹(데이터만 다름, 동작 동일)
        var lm = win._minmax(lk); win._likedMin = lm[0]; win._likedMax = lm[1]
        win.likedTags = lk
        win.tagStats = controller.folderTagStats()     // 헤더 통계
        var want = ""
        if (keepHover && prev)
            for (var i = 0; i < kw.length; i++) if (kw[i].word === prev) { want = prev; break }
        if (!want) want = kw.length > 0 ? kw[0].word : ""
        win._hoverTag = want; win._pendingTag = want
        win.refreshPreview()                           // 그리드 레이아웃 크기에 맞춰 채움
    }
    function previewTag(word) { win._pendingTag = word; tagPreviewTimer.restart() }
    // 미리보기 그리드에 '채울 만큼'의 썸네일 수 — 보이는 열×행(완전히 들어가는 셀)만큼. 남는
    // 여백은 유지(부분 행은 안 채움). 레이아웃 전(크기 0)이면 최소치 방어.
    function previewLimit() {
        var cell = 132 + 10                            // 썸네일 132 + spacing 10
        var cols = Math.max(1, Math.floor((tcGridFlick.width + 10) / cell))
        var rows = Math.max(1, Math.floor((tcGridFlick.height + 10) / cell))
        return Math.max(8, cols * rows)
    }
    function refreshPreview() {
        if (!win.showTagCloud) return                  // 닫혀 있으면(리사이즈 등) 무시
        win.tagPreviewPaths = win._hoverTag ? controller.filesWithKeyword(win._hoverTag, win.previewLimit()) : []
    }
    // 헤더/빈상태 공용 통계 문자열 (사진·인덱싱·고유 태그·좋아요)
    function tagStatsText() {
        var s = win.tagStats
        if (!s || s.photos === undefined) return ""
        var t = s.photos + " photos  ·  " + s.indexed + " indexed  ·  " + s.tags + " tags"
        if (s.liked > 0) t += "  ·  " + s.liked + " <font color='#ff8a8a'>♥</font>"
        return t
    }

    // 탐색기에서 우클릭한 파일을 프리뷰 창으로 연다.
    // 현재 폴더의 RAW(디렉터리 제외)만 경로 배열로 만들어 좌/우 네비 대상으로 넘긴다.
    function openPreview(path) {
        win.peekHide()                      // 호버 피크가 떠 있으면 닫고 프리뷰 진입
        var files = win.explorerFiles       // 현재 보이는(필터 반영) 목록 기준으로 좌/우 이동
        var list = []
        var start = 0
        for (var i = 0; i < files.length; i++) {
            var it = files[i]
            if (!it.isDir) {
                if (it.path === path) start = list.length
                list.push(it.path)
            }
        }
        if (list.length > 0)
            previewWin.open(list, start)
    }

    // ─── 폴더 태그 워드 클라우드 (몰입형 풀블리드 — 경계 없는 반투명 전면, 단어 클릭 = 검색 필터) ───
    Rectangle {
        id: tagCloudOverlay
        visible: win.showTagCloud
        anchors.fill: parent
        z: 1000
        color: "#e6121212"                                   // 블러 실패 시 폴백(평소엔 아래 블러+틴트가 덮음)
        opacity: win.showTagCloud ? 1 : 0                    // 페이드 등장(다이얼로그 팝 아님)
        Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        focus: win.showTagCloud                              // 열릴 때 키 입력 받기 → Esc 닫기
        onVisibleChanged: if (visible) { forceActiveFocus(); tcBgSource.scheduleUpdate() }   // 열 때 배경 1회 스냅샷
        Keys.onEscapePressed: win.showTagCloud = false

        // 배경 프로스티드 글래스 — 열 때 1회 스냅샷(정지 배경) → 블러 + 어두운 틴트(가독성).
        // live:false 라 per-frame 캡처 없음(발열/부하 없음). 배경이 바뀌면 다시 열 때 갱신.
        ShaderEffectSource {
            id: tcBgSource
            anchors.fill: parent
            sourceItem: mainContent
            live: false; hideSource: false; visible: false
        }
        MultiEffect {
            anchors.fill: parent
            source: tcBgSource
            blurEnabled: true; blur: 0.7; blurMax: 28; autoPaddingEnabled: false
        }
        Rectangle { anchors.fill: parent; color: "#b8101014" }   // 어두운 틴트(글자 대비 확보)

        MouseArea { anchors.fill: parent; onClicked: win.showTagCloud = false }   // 빈 곳 클릭=닫기

        // 호버 dwell 타이머 — 단어에 200ms 머물러야 미리보기 전환. 스쳐 지나가는 단어는
        // 머무르지 않아(벗어날 때 stop) 전환 안 됨 → 썸네일로 내려가는 길에 바뀌는 문제 해결.
        Timer {
            id: tagPreviewTimer; interval: 200
            onTriggered: { win._hoverTag = win._pendingTag; win.refreshPreview() }
        }

        // ✕ 닫기 (우상단에 떠 있음, 박스 없음)
        Text {
            anchors.top: parent.top; anchors.right: parent.right
            anchors.topMargin: 26; anchors.rightMargin: 34
            z: 2
            text: "✕"; color: tcX.hovered ? "#f0f0f0" : "#8892a0"; font.pixelSize: 22
            HoverHandler { id: tcX }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: win.showTagCloud = false }
        }

        // 빈 상태 — 캡션 0개: 헤더 그룹(타이틀+통계+안내)을 통째로 화면 정중앙 정렬(2단 프레임 없음).
        Column {
            visible: win.tagCloudData.length === 0
            anchors.centerIn: parent
            spacing: 3
            Label {
                text: "Photo tags"
                color: "#ffffff"; font.pixelSize: 30; font.weight: Font.Bold; font.letterSpacing: 0.5
            }
            Label {
                text: win.tagStatsText()
                textFormat: Text.StyledText; color: "#8892a0"; font.pixelSize: 13
            }
            Label {
                topPadding: 14
                text: "No captions indexed in this folder yet.\nClose this and use the ⚙ Index button to index the folder first."
                color: "#8892a0"; font.pixelSize: 14; lineHeight: 1.35
            }
        }

        // 본문 = [헤더] 위에 [2단 본문] 스택. 2단 = 좌 태그 클라우드 / 우 사진 미리보기 그리드
        // (풀블리드 캔버스의 가로·세로 여백을 콘텐츠로 채워 허전함 완화).
        ColumnLayout {
            visible: win.tagCloudData.length > 0     // 캡션 0개면 좌상단 헤더 대신 위 중앙 그룹 표시
            anchors.fill: parent
            anchors.topMargin: 30; anchors.bottomMargin: 22
            anchors.leftMargin: 44; anchors.rightMargin: 44
            spacing: 0

            // 헤더 — 대표 타이틀에 무게(크게·굵게·밝게) + 안내 부제
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Label {
                    text: "Photo tags"
                    color: "#ffffff"; font.pixelSize: 30; font.weight: Font.Bold
                    font.letterSpacing: 0.5
                }
                Label {   // 폴더 요약 통계 (사진·인덱싱·고유 태그·좋아요)
                    text: win.tagStatsText()
                    textFormat: Text.StyledText
                    color: "#8892a0"; font.pixelSize: 13
                }
            }

            // 2단 본문 — [왼쪽: 태그 클라우드(좁은 열 → 세로로 길어짐)] | [오른쪽: 큰 사진 그리드(상시 미리보기)].
            // 풀블리드 캔버스의 가로·세로 여백을 콘텐츠로 채우고, 호버(왼)↔미리보기(오)가 분리돼 스쳐 지나침도 없음.
            RowLayout {
                id: tcBody
                visible: win.tagCloudData.length > 0     // 캡션 0개면 숨김(빈 프레임/구분선 안 띄움)
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.topMargin: 20
                spacing: 30
                opacity: win.showTagCloud ? 1 : 0
                transform: Translate {
                    y: win.showTagCloud ? 0 : 16
                    Behavior on y { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
                }
                Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }

                // ── 왼쪽: 태그 클라우드 (좁은 열, 콘텐츠가 짧으면 세로 중앙·길면 스크롤) ──
                Flickable {
                    id: tcFlick
                    Layout.preferredWidth: (tagCloudOverlay.width - 88) * 0.42
                    Layout.fillHeight: true
                    contentWidth: width; contentHeight: bodyCol.height
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    Column {
                        id: bodyCol
                        width: tcFlick.width
                        y: Math.max(0, (tcFlick.height - height) / 2)
                        spacing: 30

                        // ── All tags 섹션 (전체 키워드 — ♥ 그룹과 대칭되는 섹션 제목) ──
                        Column {
                            visible: win.tagCloudData.length > 0
                            width: parent.width
                            spacing: 8
                            Label { text: "All tags"; color: "#8fb4e8"; font.pixelSize: 12; font.bold: true }
                            Flow {
                                x: 10; width: parent.width - 20            // 안쪽 여백 → 호버 확대 시 잘림 방지
                                spacing: 16
                                Repeater {
                                    model: win.tagCloudData
                                    delegate: Text {
                                        // 빈도 정규화 t(로그) — 크기·굵기·색을 일관 강조(시퀀셜).
                                        property real t: {
                                            var mn = win._tagMinCount, mx = win._tagMaxCount
                                            return (mx <= mn) ? 0.5
                                                : (Math.log(modelData.count) - Math.log(mn)) / (Math.log(mx) - Math.log(mn))
                                        }
                                        text: modelData.word
                                        font.pixelSize: Math.round(14 + t * 26)      // 14~40px
                                        font.weight: 400 + Math.round(t * 300)        // 400~700(빈도로 굵기)
                                        // 시퀀셜: muted grey-blue(희소) → accent blue(빈번). 호버/선택=밝게.
                                        color: (wHover.hovered || win._hoverTag === modelData.word) ? "#cfe0ff"
                                            : Qt.rgba(0.55 + t * 0.11, 0.58 + t * 0.20, 0.63 + t * 0.37, 1)
                                        scale: wHover.hovered ? 1.12 : 1.0
                                        Behavior on scale { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
                                        Behavior on color { ColorAnimation { duration: 110 } }
                                        HoverHandler {
                                            id: wHover
                                            // 진입=dwell 시작, 이탈=대기 전환 취소(스쳐 지나가면 안 바뀜, 기존 미리보기 유지)
                                            // leave 는 '떠나는 단어가 아직 대기 대상일 때'만 취소 — 다른 단어가
                                            // 이미 대기 중(enter 가 먼저 온 경우)이면 그 타이머를 죽이지 않음(드래그 시 안 바뀌던 버그).
                                            onHoveredChanged: { if (hovered) win.previewTag(modelData.word); else if (win._pendingTag === modelData.word) tagPreviewTimer.stop() }
                                        }
                                        ToolTip.visible: wHover.hovered
                                        ToolTip.text: modelData.count + " photos"
                                        MouseArea {
                                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                            onClicked: {
                                                searchInput.text = modelData.word   // 검색창 동기화(✕로 해제)
                                                win.applySearch(modelData.word)     // 탐색기 필터(선택/스크롤 유지)
                                                win.showTagCloud = false
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // ── ♥ Liked 태그 섹션 (좋아요 사진 키워드 — 표시/색만 다르고 동작은 전체와 동일) ──
                        Column {
                            visible: win.likedTags.length > 0
                            width: parent.width
                            spacing: 8
                            Label { text: "♥ In liked photos"; color: "#ff8a8a"; font.pixelSize: 12; font.bold: true }
                            Flow {
                                x: 10; width: parent.width - 20
                                spacing: 14
                                Repeater {
                                    model: win.likedTags
                                    delegate: Text {
                                        property real t: {
                                            var mn = win._likedMin, mx = win._likedMax
                                            return (mx <= mn) ? 0.5
                                                : (Math.log(modelData.count) - Math.log(mn)) / (Math.log(mx) - Math.log(mn))
                                        }
                                        text: modelData.word
                                        font.pixelSize: Math.round(13 + t * 14)      // 13~27px(부그룹이라 약간 작게)
                                        font.weight: 400 + Math.round(t * 300)
                                        // 빨강 계열 시퀀셜(♥). 호버/선택=밝게. 동작은 전체 클라우드와 동일.
                                        color: (lHover.hovered || win._hoverTag === modelData.word) ? "#ffd0d0"
                                            : Qt.rgba(0.69 + t * 0.31, 0.42 + t * 0.12, 0.45 + t * 0.09, 1)
                                        scale: lHover.hovered ? 1.12 : 1.0
                                        Behavior on scale { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
                                        Behavior on color { ColorAnimation { duration: 110 } }
                                        HoverHandler {
                                            id: lHover
                                            // leave 는 '떠나는 단어가 아직 대기 대상일 때'만 취소 — 다른 단어가
                                            // 이미 대기 중(enter 가 먼저 온 경우)이면 그 타이머를 죽이지 않음(드래그 시 안 바뀌던 버그).
                                            onHoveredChanged: { if (hovered) win.previewTag(modelData.word); else if (win._pendingTag === modelData.word) tagPreviewTimer.stop() }
                                        }
                                        ToolTip.visible: lHover.hovered
                                        ToolTip.text: modelData.count + " liked"
                                        MouseArea {
                                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                            onClicked: {   // 전체 클라우드와 동일 동작(그 키워드로 필터)
                                                searchInput.text = modelData.word
                                                win.applySearch(modelData.word)
                                                win.showTagCloud = false
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // 세로 구분선 (좌 클라우드 / 우 미리보기)
                Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: "#26ffffff" }

                // ── 오른쪽: 상시 사진 미리보기 그리드 (호버한 단어의 사진, 클릭=그 사진 필터+선택) ──
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    spacing: 12
                    Label {   // 미리보기 제목 (열 때 최상위 단어 자동)
                        visible: win.tagCloudData.length > 0
                        Layout.fillWidth: true
                        text: win._hoverTag
                            ? ("“" + win._hoverTag + "”  ·  " + win.tagPreviewPaths.length
                               + (win.tagPreviewPaths.length >= win.previewLimit() ? "+ photos" : " photos"))
                            : "Hover a tag to preview its photos"
                        color: win._hoverTag ? "#cfe0ff" : "#5f6b7a"
                        font.pixelSize: 14; font.bold: win._hoverTag.length > 0
                    }
                    Flickable {
                        id: tcGridFlick
                        visible: win.tagCloudData.length > 0
                        Layout.fillWidth: true; Layout.fillHeight: true
                        contentWidth: width; contentHeight: gridFlow.height
                        clip: true; boundsBehavior: Flickable.StopAtBounds
                        // 크기 확정/창 리사이즈 시 채울 만큼 다시 로드(동적 개수)
                        onWidthChanged: win.refreshPreview()
                        onHeightChanged: win.refreshPreview()
                        Flow {
                            id: gridFlow
                            width: parent.width; spacing: 10
                            Repeater {
                                model: win.tagPreviewPaths
                                delegate: Rectangle {
                                    width: 132; height: 132; radius: 6; color: "#222"; clip: true
                                    border.color: gThumbHover.hovered ? "#8ab4f8" : "transparent"; border.width: 2
                                    Image {
                                        anchors.fill: parent; anchors.margins: 2
                                        source: "image://thumb/" + encodeURIComponent(modelData)
                                        sourceSize.width: 220; fillMode: Image.PreserveAspectCrop
                                        asynchronous: true; cache: true
                                    }
                                    HoverHandler { id: gThumbHover }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {   // 로드하지 않음: 그 키워드로 필터 + 그 사진까지 선택(하이라이트/스크롤)
                                            var p = modelData
                                            searchInput.text = win._hoverTag
                                            controller.setSearchQuery(win._hoverTag)
                                            Qt.callLater(function() { win.selectInExplorer(p) })
                                            win.showTagCloud = false
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 폴더가 바뀌면 좌측 리스트 선택 하이라이트 초기화(잔상 방지).
    // 단, 위로가기로 올라온 경우엔 방금 있던 폴더 항목을 선택+스크롤(어디서 왔는지 유지).
    property string _selectAfterScan: ""   // goUp 직전의 현재 폴더 경로
    Connections {
        target: controller
        function onFolderChanged() {
            var want = win._selectAfterScan
            win._selectAfterScan = ""
            fileListView.currentIndex = -1
            // 폴더가 바뀌면 배치 체크 목록 초기화 — 이전 폴더에서 체크한 파일이 화면에
            // 안 보인 채 다음 배치 export 에 몰래 포함되던 문제 방지.
            if (win.batchCheckedCount > 0) win.batchClearChecked()
            if (want !== "")
                Qt.callLater(function() { win.selectInExplorer(want) })   // 목록 바인딩 갱신 뒤
        }
    }

    FileDialog {
        id: saveDialog
        title: "Export (Full Resolution)"
        fileMode: FileDialog.SaveFile
        nameFilters: ["PNG (*.png)", "JPEG (*.jpg)", "TIFF (*.tif)"]
        defaultSuffix: "png"
        // 렌더 모드: 0=CPU(render_full), 1=GPU(프리뷰 셰이더로 풀해상도 렌더 → 프리뷰=Export)
        onAccepted: {
            var p = win.exportParams()
            // GPU grab 은 8bit 라 16bit 선택 시 무조건 CPU 경로 사용.
            if (renderModeCombo.currentIndex === 1 && !bitDepth16Check.checked) {
                controller.exportImageGpu(selectedFile, p)
                // 실제로 진행됐을 때만 로더 활성 — 슬롯 가드에 걸려 시작 안 됐는데 active=true 로
                // 두면 grab 을 구동할 디코드가 없어 pipeFull 이 떠 있게 됨(버튼은 위에서 재진입 차단).
                if (controller.exporting) gpuExportLoader.active = true
            } else {
                controller.exportImage(selectedFile, p)
            }
        }
    }

    // ---------- 썸네일 호버 피크 ----------
    // 탐색기 파일 행에 마우스를 올리고 잠깐(250ms) 멈추면 EXIF 썸네일을 원본 크기
    // (최대 160px, 업스케일 없음)로 행 우측에 팝업 표시. 행을 벗어나면 즉시 닫히고,
    // 다음 행에서도 250ms 멈춰야 다시 뜸(즉시 추종 없음 — 빠른 이동 중 번쩍임 방지,
    // 행 안 이동은 리셋 안 함). 더블클릭 로드/우클릭 메뉴/스크롤/프리뷰 진입 시도 닫힘.
    property var _peekRow: null            // 팝업 소유 delegate(이탈 이벤트 소유자 판별용)
    function _peekPlace(it) {              // 행 우측 중앙(씬 좌표)에 팝업 배치+표시
        var pos = it.mapToItem(null, it.width, it.height / 2)
        thumbPeek.anchorX = pos.x
        thumbPeek.anchorY = pos.y
        thumbPeek.visible = true
    }
    function peekShow(item, path) {
        if (previewWin.visible) return
        win._peekRow = item
        thumbPeek.pathKey = path           // 대기 중 미리 로드(팝업 시 즉시 표시)
        thumbPeek.visible = false          // 직전 행 팝업은 즉시 닫음(행 이탈=닫힘)
        peekTimer.restart()                // 새 행에 250ms 멈추면 다시 띄움
    }
    function peekHide() {
        peekTimer.stop()
        win._peekRow = null
        thumbPeek.visible = false
    }
    Timer {
        id: peekTimer
        interval: 250
        onTriggered: {
            if (win._peekRow && !previewWin.visible)
                win._peekPlace(win._peekRow)
        }
    }
    Rectangle {
        id: thumbPeek
        property string pathKey: ""
        property real anchorX: 0
        property real anchorY: 0
        visible: false
        z: 900                             // 프리뷰 오버레이(1000)보다 아래, 나머지 위
        // 이미지 비동기 로드로 크기가 늦게 확정돼도 따라가도록 바인딩으로 배치
        width: peekImg.width + 8
        height: peekImg.height + 8
        x: Math.min(anchorX + 4, win.width - width - 8)
        y: Math.max(8, Math.min(anchorY - height / 2, win.height - height - 8))
        color: "#1e1e1e"
        border.color: "#555555"
        border.width: 1
        radius: 4
        Image {
            id: peekImg
            x: 4; y: 4
            asynchronous: true
            cache: true
            sourceSize.width: 160          // EXIF 썸네일 원본(세로사진은 120px 그대로)
            source: thumbPeek.pathKey === "" ? ""
                    : "image://thumb/" + encodeURIComponent(thumbPeek.pathKey)
        }
    }

    // 날짜 입력칸(stampField) 편집 중 필드 바깥 클릭 시 포커스 해제는 앱 레벨 이벤트 필터
    // (_ClickOutsideFocusFilter, main.py)가 처리 — 프리뷰/버튼/슬라이더 grab 무관하게 포착하고
    // 커서/전달에 간섭 없음. 필드는 objectName: "stampField" 로 파이썬에서 찾는다.

    RowLayout {
        id: mainContent                          // 태그 클라우드 오버레이 배경 블러의 소스
        anchors.fill: parent
        spacing: 0

        // ---------- 좌측: File Explorer ----------
        Rectangle {
            visible: win.showExplorer      // B 키 / 토글 버튼으로 show/hide
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            color: "#2b2b2b"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6
                enabled: !win.batchActive   // 배치 중 파일 전환/폴더 변경 차단(취소는 오버레이 버튼)

                // 헤더 1줄: [⬆ 상위 폴더] + [현재 폴더 경로(클릭=폴더 선택 대화상자)]
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    // 위로가기 — 평상시 투명·호버만 강조(♥/☑ 와 동일 톤, 기본 Button 회색 배경이 튀어 배제).
                    // 글리프: U+2B06+U+FE0E(텍스트 프레젠테이션 강제) = 꼬리 있는 솔리드 화살표를
                    // 흑백 심볼로 렌더(FE0E 없이는 파란 이모지化).
                    Rectangle {
                        id: upBtn
                        Layout.preferredWidth: 30
                        Layout.preferredHeight: 28
                        radius: 5
                        color: upHover.hovered ? "#3a3f4b" : "transparent"
                        border.color: "#555555"     // 경로 필드·♥/☑ 와 동일 테두리(헤더 균형)
                        border.width: 1
                        ToolTip.visible: upHover.hovered
                        ToolTip.text: "Parent folder (Alt+↑)"
                        // U+2794(굵은 머리+꼬리, 이모지 대상 아님 → 항상 단색 텍스트 렌더)를
                        // -90° 회전해 위 방향으로. color 로 흰색 지정 가능(이모지 글리프는 불가).
                        Text {
                            anchors.centerIn: parent
                            text: "➔"
                            rotation: -90
                            color: "#e6e6e6"
                            font.pixelSize: 12
                        }
                        HoverHandler { id: upHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                // 올라간 뒤 방금 있던 폴더를 목록에서 선택+스크롤(onFolderChanged)
                                win._selectAfterScan = controller.currentFolder
                                controller.goUp()
                            }
                        }
                    }
                    // 현재 폴더 경로 자체가 폴더 선택 버튼(별도 Folder… 버튼 일원화)
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: upBtn.height
                        radius: 5
                        color: fpHover.hovered ? "#3a3f4b" : "transparent"
                        border.color: "#555555"
                        border.width: 1
                        ToolTip.visible: fpHover.hovered
                        ToolTip.delay: 800
                        ToolTip.text: "Change folder…"
                        Label {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            verticalAlignment: Text.AlignVCenter
                            text: controller.currentFolder || "Select a folder…"
                            color: fpHover.hovered ? "#e6e6e6" : "#b8b8b8"
                            font.pixelSize: 11
                            elide: Text.ElideMiddle
                        }
                        HoverHandler { id: fpHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                // 현재 폴더에서 시작(폴더 미선택 시 Qt 기본 위치)
                                if (controller.currentFolderUrl !== "")
                                    folderDialog.currentFolder = controller.currentFolderUrl
                                folderDialog.open()
                            }
                        }
                    }
                }

                // 헤더 2줄: 폴더 통계(좌) + ♥ 필터 / ☑ 배치 선택(우, 컴팩트)
                // 통계는 전체 폴더 기준(좋아요 필터 무관). fileList(folderChanged)·editsRevision·
                // likeRevision 참조로 변경 시 자동 재계산.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label {
                        objectName: "folderStatsLabel"
                        Layout.fillWidth: true
                        visible: controller.currentFolder !== ""
                        readonly property var stats: {
                            controller.likeRevision; controller.editsRevision
                            var files = controller.fileList
                            var n = 0, liked = 0, edited = 0
                            for (var i = 0; i < files.length; i++) {
                                if (files[i].isDir) continue
                                n++
                                if (controller.hasEdits(files[i].path)) edited++
                                if (controller.isLiked(files[i].path)) liked++
                            }
                            return [n, edited, liked]
                        }
                        textFormat: Text.StyledText
                        text: stats[0] + " photos" +
                              (stats[1] > 0 ? "  ·  <font color='#E0A226'>" + stats[1] + " edited</font>" : "") +
                              (stats[2] > 0 ? "  ·  <font color='#ff6b6b'>" + stats[2] + " ♥</font>" : "")
                        color: "#7f7f7f"
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                    Item { visible: controller.currentFolder === ""; Layout.fillWidth: true }
                    // "좋아요만 보기" 토글 — ♥(채움)/♡(빈) 글리프로 활성/비활성 표시
                    Rectangle {
                        id: likeFilterBtn
                        Layout.preferredWidth: 30
                        Layout.preferredHeight: 22
                        radius: 4
                        color: win.showLikedOnly ? "#3a2a2e"
                             : (lfHover.hovered ? "#3a3f4b" : "transparent")
                        border.color: win.showLikedOnly ? "#ff6b6b" : "#555555"
                        border.width: 1
                        ToolTip.visible: lfHover.hovered
                        ToolTip.text: "Show liked only (L)"
                        Text {
                            anchors.centerIn: parent
                            text: win.showLikedOnly ? "♥" : "♡"
                            color: win.showLikedOnly ? "#ff6b6b" : "#cfcfcf"
                            font.pixelSize: 14
                        }
                        HoverHandler { id: lfHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.toggleLikedOnly()
                        }
                    }
                    // 배치 export 선택(체크박스) 모드 토글 — 켜면 파일 클릭=체크, 하단에 Export 바.
                    Rectangle {
                        id: selModeBtn
                        Layout.preferredWidth: 30
                        Layout.preferredHeight: 22
                        radius: 4
                        color: win.batchSelectMode ? "#2e3a2a"
                             : (smHover.hovered ? "#3a3f4b" : "transparent")
                        border.color: win.batchSelectMode ? "#9fd39f" : "#555555"
                        border.width: 1
                        ToolTip.visible: smHover.hovered
                        ToolTip.text: "Select files for batch export"
                        Text {
                            anchors.centerIn: parent
                            text: "☑"
                            color: win.batchSelectMode ? "#9fd39f" : "#cfcfcf"
                            font.pixelSize: 13
                        }
                        HoverHandler { id: smHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                win.batchSelectMode = !win.batchSelectMode
                                if (!win.batchSelectMode) win.batchClearChecked()
                            }
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                // 캡션 검색 — 저장된 캡션/태그 단어로 폴더 필터(인덱싱된 사진만 검색됨).
                // TextInput(코어) + Rectangle: 네이티브 스타일에서 background 커스텀 경고 회피.
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 28
                    visible: controller.currentFolder !== ""
                    radius: 5; color: "#232323"
                    border.color: searchInput.activeFocus ? "#8ab4f8" : "#555555"
                    border.width: 1
                    TextInput {
                        id: searchInput
                        anchors.fill: parent
                        anchors.leftMargin: 8; anchors.rightMargin: 26   // ✕ 버튼 공간 확보
                        verticalAlignment: TextInput.AlignVCenter
                        color: "#e6e6e6"; font.pixelSize: 12
                        clip: true; selectByMouse: true
                        onTextChanged: searchDebounce.restart()
                        onActiveFocusChanged: win._typing = activeFocus   // 타이핑 중 단축키(L/B/C) 충돌 방지
                        Keys.onEscapePressed: text = ""                    // 비우면 onTextChanged→debounce→applySearch
                        Timer { id: searchDebounce; interval: 180; onTriggered: win.applySearch(searchInput.text) }
                        Text {   // placeholder
                            anchors.verticalCenter: parent.verticalCenter
                            visible: searchInput.text === "" && !searchInput.activeFocus
                            text: "Search captions"
                            color: "#777"; font.pixelSize: 12
                        }
                    }
                    // ✕ 텍스트 삭제 (내용 있을 때만) — 비우면 onTextChanged→debounce→applySearch(선택/스크롤 복원)
                    Rectangle {
                        anchors.right: parent.right; anchors.rightMargin: 5
                        anchors.verticalCenter: parent.verticalCenter
                        width: 18; height: 18; radius: 9
                        visible: searchInput.text !== ""
                        color: clrHover.hovered ? "#3a3f4b" : "transparent"
                        Text { anchors.centerIn: parent; text: "✕"; color: "#aaa"; font.pixelSize: 10 }
                        HoverHandler { id: clrHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: { searchInput.text = ""; searchInput.forceActiveFocus() }
                        }
                    }
                }

                // 폴더 배치 인덱싱 — 한 행: [⚙ 시작 / ✕ 취소] [진행·커버리지 바] [N/M 카운트].
                // 인덱스 상태(N/M)는 여기 한 곳에만(중복 없음). 현재 표시목록(좋아요/검색 필터
                // 반영)을 백그라운드 캡션 생성, 이미 인덱싱된 사진은 skip(재개), UI 비블로킹.
                RowLayout {
                    id: idxRow
                    Layout.fillWidth: true
                    Layout.preferredHeight: 22
                    spacing: 6
                    visible: controller.currentFolder !== ""
                    // 진행 표시는 '지금 보는 폴더가 곧 인덱싱 중인 폴더'일 때만. 다른 폴더로 옮기면
                    // 이 폴더의 커버리지만 보이고(어긋남 방지), 배치는 원래 폴더에서 계속 돎.
                    readonly property bool indexingHere: controller.indexBusy
                        && controller.indexFolder === controller.currentFolder
                    function folderName(p) {
                        if (!p) return "another folder"
                        var parts = p.replace(/\\/g, "/").split("/").filter(function (s) { return s.length > 0 })
                        return parts.length ? parts[parts.length - 1] : p
                    }
                    // 인덱싱 대상: 항상 폴더 전체(검색/필터로 좁히지 않음 — 일관 동작).
                    //   ⚠️검색 필터의 보이는 목록은 '이미 인덱싱된 매칭 파일'뿐이라 그걸 대상으로
                    //   삼으면 전부 스킵되어 아무것도 안 됨. 그래서 controller.fileList(전체) 사용.
                    // 단, show liked only 면 좋아요된 사진을 먼저 처리(우선순위). 재개 필터가 이미 된 건 스킵.
                    function targetPaths() {
                        var files = controller.fileList
                        var liked = [], rest = []
                        for (var i = 0; i < files.length; i++) {
                            var it = files[i]
                            if (it.isDir) continue
                            if (win.showLikedOnly && controller.isLiked(it.path)) liked.push(it.path)
                            else rest.push(it.path)
                        }
                        return liked.concat(rest)   // 좋아요 우선(showLikedOnly 시) + 폴더 전체
                    }
                    // 진행/커버리지 바 (busy=진행률, idle=커버리지 비율)
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: 6
                        radius: 3; color: "#333"
                        Rectangle {
                            height: parent.height; radius: 3
                            // 이 폴더를 인덱싱 중이면 진행률(파랑), 아니면 이 폴더의 커버리지 비율(초록).
                            color: idxRow.indexingHere ? "#8ab4f8" : "#4a5a3a"
                            width: parent.width * (idxRow.indexingHere ? controller.indexProgress
                                   : (controller.photoCount > 0 ? controller.indexedCount / controller.photoCount : 0))
                        }
                    }
                    // 공유 카운트 (한 곳) — 캡션 저장/폴더 변경 + 배치 진행 시 실시간 갱신
                    Label {
                        text: {
                            var _t = controller.indexDone       // indexChanged 의존(배치 중 실시간)
                            return controller.indexedCount + "/" + controller.photoCount
                        }
                        color: "#aaa"; font.pixelSize: 10
                    }
                    // ⚙ 시작 / ✕ 취소(이 폴더) / ⋯ 다른 폴더 인덱싱 중(정보만) (오른쪽 끝, 작은 아이콘)
                    Rectangle {
                        Layout.preferredWidth: 20; Layout.preferredHeight: 20
                        radius: 4
                        color: idxHover.hovered ? "#33373f" : "transparent"
                        border.color: idxRow.indexingHere ? "#ff8080" : "#555"; border.width: 1
                        Text {
                            anchors.centerIn: parent
                            // 이 폴더 인덱싱 중=✕, 다른 폴더 인덱싱 중=⋯(정보), 유휴=⚙
                            text: idxRow.indexingHere ? "✕" : (controller.indexBusy ? "⋯" : "⚙")
                            color: idxRow.indexingHere ? "#ff8080" : (controller.indexBusy ? "#888" : "#cfcfcf")
                            font.pixelSize: idxRow.indexingHere ? 9 : (controller.indexBusy ? 12 : 9)
                        }
                        HoverHandler { id: idxHover }
                        ToolTip.visible: idxHover.hovered
                        ToolTip.text: idxRow.indexingHere ? "Cancel indexing"
                            : (controller.indexBusy
                               ? ("Indexing “" + idxRow.folderName(controller.indexFolder) + "” in the background  ·  "
                                  + controller.indexDone + "/" + controller.indexTotal)
                               : "Index listed photos in the background (skips already-indexed) to enable search")
                        MouseArea {
                            anchors.fill: parent
                            // 다른 폴더 인덱싱 중이면 정보만(클릭 불가) — 그 폴더로 가서 ✕로 관리.
                            cursorShape: (idxRow.indexingHere || !controller.indexBusy)
                                ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: {
                                if (idxRow.indexingHere) controller.cancelFolderIndex()
                                else if (!controller.indexBusy) controller.startFolderIndex(idxRow.targetPaths(), true)
                                // else: 다른 폴더 인덱싱 중 → 아무 동작 안 함(정보 표시만)
                            }
                        }
                    }
                    // 🏷 폴더 태그 (단어 클릭 = 검색 필터). 단축키 H.
                    Rectangle {
                        Layout.preferredWidth: 20; Layout.preferredHeight: 20
                        radius: 4
                        color: cloudHover.hovered ? "#33373f" : "transparent"
                        border.color: "#555"; border.width: 1
                        Text { anchors.centerIn: parent; text: "🏷"; color: "#cfcfcf"; font.pixelSize: 12 }
                        HoverHandler { id: cloudHover }
                        ToolTip.visible: cloudHover.hovered
                        ToolTip.text: "Photo tags (H) — click a word to filter"
                        MouseArea {
                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: win.openTagCloud()
                        }
                    }
                }

                // 파일/폴더 리스트 (ListView = 화면에 보이는 항목만 썸네일 요청 → 지연 로딩)
                ListView {
                    id: fileListView
                    objectName: "fileListView"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 2
                    cacheBuffer: 400
                    model: win.explorerFiles      // "좋아요만 보기" 필터 반영
                    currentIndex: -1
                    boundsBehavior: Flickable.StopAtBounds

                    // 키보드 탐색(리스트 클릭으로 포커스 획득 시): ↑/↓ 한 칸, Home/End 처음/끝,
                    // PgUp/PgDn 한 화면. 전역 Shortcut 이 아니라 포커스 기반이라 콤보박스·입력칸과
                    // 충돌하지 않음. 이동 후 항목이 보이도록 스크롤. (Enter=프리뷰는 전역 Shortcut)
                    Keys.onPressed: (e) => {
                        var n = count
                        if (n <= 0) return
                        var page = Math.max(1, Math.floor(height / 66))   // 파일 행(64+2) 기준 한 화면
                        var cur = currentIndex
                        var next = -2
                        if (e.key === Qt.Key_Down)          next = Math.min(n - 1, cur < 0 ? 0 : cur + 1)
                        else if (e.key === Qt.Key_Up)       next = Math.max(0, cur < 0 ? 0 : cur - 1)
                        else if (e.key === Qt.Key_Home)     next = 0
                        else if (e.key === Qt.Key_End)      next = n - 1
                        else if (e.key === Qt.Key_PageDown) next = Math.min(n - 1, (cur < 0 ? 0 : cur) + page)
                        else if (e.key === Qt.Key_PageUp)   next = Math.max(0, (cur < 0 ? 0 : cur) - page)
                        if (next !== -2) {
                            currentIndex = next
                            positionViewAtIndex(next, ListView.Contain)
                            e.accepted = true
                        }
                    }
                    enabled: !controller.busy      // 로드 진행 중엔 사진 변경 차단
                    opacity: controller.busy ? 0.5 : 1.0   // 비활성 시각 표시
                    // 스크롤 시 호버 피크 처리: 팝업 소유 행이 아직 마우스 아래면
                    //  - 팝업이 떠 있음 → 행 새 위치로 이동(유지)
                    //  - 대기(타이머) 중 → 취소하지 말고 재시작(스크롤 멈춘 뒤부터 250ms).
                    //    휠 스크롤로 커서 아래 들어온 행은 진입 이벤트가 스크롤 중 1회뿐이라,
                    //    여기서 취소해버리면 행을 나갔다 다시 들어와야만 뜨는 버그가 됨.
                    // 행이 마우스를 벗어났으면 닫기. (클릭이 currentIndex 를 바꾸면 ListView 가
                    // 가장자리 행 정렬로 contentY 를 미세 이동 — 무조건 닫기면 그때도 사라졌음.)
                    onContentYChanged: {
                        var it = win._peekRow
                        if (it && it.peekHovered) {
                            if (thumbPeek.visible)
                                win._peekPlace(it)
                            else
                                peekTimer.restart()
                        } else {
                            win.peekHide()
                        }
                    }

                    B.ScrollBar.vertical: B.ScrollBar {
                        id: fileVbar
                        width: 10
                        policy: B.ScrollBar.AsNeeded
                        contentItem: Rectangle {
                            implicitWidth: 6
                            radius: 3
                            color: fileVbar.pressed ? "#cfcfcf" : "#9a9a9a"
                        }
                        background: Rectangle { radius: 3; color: "#3a3a3a" }
                    }

                    delegate: Item {
                        id: row
                        required property var modelData
                        required property int index
                        width: ListView.view ? ListView.view.width : 0
                        height: modelData.isDir ? 28 : 64
                        readonly property bool isLoaded:
                            !modelData.isDir && modelData.path === controller.imagePath
                        // 호버 피크 소유자 판별용(스크롤 시 팝업 유지/닫기 결정)
                        readonly property bool peekHovered: rowMouse.containsMouse

                        Rectangle {
                            anchors.fill: parent
                            anchors.rightMargin: 12      // 스크롤바 영역 비움
                            radius: 4
                            color: row.isLoaded ? "#2d4a6b"
                                 : (fileListView.currentIndex === row.index ? "#3a3f4b"
                                                                            : "transparent")
                            border.color: row.isLoaded ? "#8ab4f8" : "transparent"
                            border.width: row.isLoaded ? 1 : 0

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 8

                                // 썸네일(파일) 또는 폴더 아이콘
                                Item {
                                    Layout.preferredWidth: modelData.isDir ? 20 : 84
                                    Layout.preferredHeight: modelData.isDir ? 20 : 56
                                    Layout.alignment: Qt.AlignVCenter

                                    Text {
                                        visible: modelData.isDir
                                        anchors.centerIn: parent
                                        text: "📁"
                                        font.pixelSize: 16
                                    }
                                    Rectangle {     // 로딩중/실패 placeholder
                                        visible: !modelData.isDir && thumbImg.status !== Image.Ready
                                        anchors.fill: parent
                                        color: "#1e1e1e"
                                        radius: 2
                                    }
                                    Image {
                                        id: thumbImg
                                        visible: !modelData.isDir
                                        anchors.fill: parent
                                        fillMode: Image.PreserveAspectFit
                                        asynchronous: true
                                        cache: true
                                        sourceSize.width: 96    // → requestImage requested_size
                                        source: modelData.isDir ? ""
                                                : "image://thumb/" + encodeURIComponent(modelData.path)
                                    }
                                    // 임베드 프리뷰가 없는 RAW(일부 폰 DNG 등)는 provider 가 null 반환
                                    // → status=Error. 빈 회색 대신 '미리보기 없음'을 표시(편집/export 는 정상).
                                    Text {
                                        visible: !modelData.isDir && thumbImg.status === Image.Error
                                        anchors.centerIn: parent
                                        width: parent.width - 6
                                        horizontalAlignment: Text.AlignHCenter
                                        wrapMode: Text.WordWrap
                                        text: "No preview"
                                        color: "#888888"
                                        font.pixelSize: 10
                                    }
                                    // 좋아요(셀렉트) 하트 배지 — likeRevision 참조로 토글/폴더변경 시 갱신
                                    Text {
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        anchors.margins: 1
                                        text: "♥"
                                        color: "#ff6b6b"
                                        style: Text.Outline
                                        styleColor: "#000000"
                                        font.pixelSize: 14
                                        visible: {
                                            controller.likeRevision
                                            return !modelData.isDir
                                                   && controller.isLiked(modelData.path)
                                        }
                                    }
                                    // 배치 선택 체크박스(선택 모드에서만, 파일 전용) — 좌상단
                                    Rectangle {
                                        visible: win.batchSelectMode && !modelData.isDir
                                        anchors.left: parent.left
                                        anchors.top: parent.top
                                        width: 16; height: 16; radius: 3
                                        readonly property bool checked: {
                                            win.batchCheckedRev
                                            return win.batchChecked[modelData.path] === true
                                        }
                                        color: checked ? "#9fd39f" : "#cc1e1e1e"
                                        border.color: checked ? "#9fd39f" : "#888888"
                                        border.width: 1
                                        Text {
                                            anchors.centerIn: parent
                                            visible: parent.checked
                                            text: "✓"
                                            color: "#1e1e1e"
                                            font.pixelSize: 12
                                            font.weight: Font.Bold
                                        }
                                    }
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.name
                                    // 편집 사이드카(.filmrawsteryedits/<name>.json)가 있으면 파일명을 앰버로
                                    // 표시(저장된 편집 표시). editsRevision 참조로 저장/폴더 변경 시 갱신.
                                    color: {
                                        controller.editsRevision
                                        return (!modelData.isDir && controller.hasEdits(modelData.path))
                                               ? "#E0A226" : "#e6e6e6"
                                    }
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                    maximumLineCount: 2
                                    wrapMode: Text.WrapAnywhere
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }

                        // 우클릭 컨텍스트 메뉴(파일 전용): Preview 항목
                        B.Menu {
                            id: ctxMenu
                            B.MenuItem {
                                text: "Preview"
                                onTriggered: win.openPreview(row.modelData.path)
                                contentItem: Text {
                                    text: parent.text
                                    color: "#e6e6e6"
                                    font.pixelSize: 12
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: 8
                                }
                                background: Rectangle {
                                    implicitWidth: 140
                                    implicitHeight: 28
                                    color: parent.highlighted ? "#3a4a6b" : "transparent"
                                }
                            }
                            background: Rectangle {
                                implicitWidth: 140
                                color: "#2b2b2b"
                                border.color: "#444"
                                border.width: 1
                                radius: 4
                            }
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            hoverEnabled: true
                            // 파일 행 마우스 인 → 피크 즉시 표시, 아웃 → 닫기.
                            // 닫기는 팝업 소유 행(_peekRow===row)일 때만 — 행 간 빠른 이동 시
                            // 이전 행의 이탈(false)이 새 행의 진입(true)보다 늦게 도착해
                            // 방금 띄운 팝업을 꺼버리는 이벤트 순서 경쟁 방지.
                            onContainsMouseChanged: {
                                if (containsMouse && !row.modelData.isDir)
                                    win.peekShow(row, row.modelData.path)
                                else if (win._peekRow === row)
                                    win.peekHide()
                            }
                            onClicked: (mouse) => {
                                if (mouse.button === Qt.RightButton)
                                    win.peekHide()          // 메뉴와 겹치지 않게(좌클릭 선택은 유지)
                                fileListView.forceActiveFocus()     // 이후 방향키 탐색 활성화
                                if (mouse.button === Qt.RightButton) {
                                    fileListView.currentIndex = row.index
                                    if (!row.modelData.isDir)
                                        ctxMenu.popup()             // 우클릭 = 컨텍스트 메뉴
                                } else if (win.batchSelectMode && !row.modelData.isDir) {
                                    win.batchToggle(row.modelData.path)       // 선택 모드 = 체크 토글
                                } else {
                                    fileListView.currentIndex = row.index     // 좌클릭 = 선택만
                                }
                            }
                            onDoubleClicked: {
                                win.peekHide()
                                if (row.modelData.isDir)
                                    controller.setFolderPath(row.modelData.path)
                                else if (!win.batchSelectMode)
                                    controller.loadPath(row.modelData.path)    // 로컬경로 디코딩 로드
                            }
                        }
                    }
                }

                // 배치 export 바(선택 모드에서만): 체크 수 + Export(포맷 → 폴더 → 시작)
                Rectangle {
                    Layout.fillWidth: true; height: 1; color: "#444"
                    visible: win.batchSelectMode
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: win.batchSelectMode
                    spacing: 6
                    Label {
                        Layout.fillWidth: true
                        text: win.batchCheckedCount + " selected"
                        color: win.batchCheckedCount > 0 ? "#9fd39f" : "#9a9a9a"
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                    Button {
                        text: "Export…"
                        enabled: win.batchCheckedCount > 0 && !win.batchActive
                                 && !controller.exporting
                        onClicked: batchFmtPopup.open()
                        Popup {
                            id: batchFmtPopup
                            y: -height - 4
                            x: parent.width - width
                            width: 210
                            padding: 10
                            modal: false
                            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                            background: Rectangle { color: "#2b2b2b"; border.color: "#555"; border.width: 1; radius: 6 }
                            contentItem: ColumnLayout {
                                spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: "Format"; color: "white"; font.pixelSize: 12 }
                                    ComboBox {
                                        id: batchFmtCombo
                                        Layout.fillWidth: true
                                        currentIndex: 0
                                        model: ["jpg", "png", "tif"]
                                        // 드롭다운 닫히면 포커스 해제 → win._typing 이 콤보에 물려 단축키가
                                        // 죽는 것 방지(captionLevelCombo 와 동일).
                                        Connections {
                                            target: batchFmtCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "Uses current Export options (resolution · 16-bit). Saved as <name>_exported." + batchFmtCombo.currentText
                                    color: "#9a9a9a"; font.pixelSize: 10
                                    wrapMode: Text.WordWrap
                                }
                                Button {
                                    Layout.fillWidth: true
                                    text: "Choose folder && start"
                                    onClicked: {
                                        batchFmtPopup.close()
                                        if (controller.currentFolderUrl !== "")
                                            batchDestDialog.currentFolder = controller.currentFolderUrl
                                        batchDestDialog.open()
                                    }
                                }
                            }
                        }
                    }
                }
                // 완료 요약("Batch: 5 saved, 1 failed")
                Label {
                    Layout.fillWidth: true
                    visible: win.batchSelectMode && win.batchResult !== ""
                    text: win.batchResult
                    color: "#9fd39f"
                    font.pixelSize: 11
                    wrapMode: Text.WrapAnywhere
                }

                // 푸터: GitHub 저장소 링크 + (있으면) 새 버전 배지 (클릭 시 외부 브라우저로 열기)
                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 24
                    color: "transparent"
                    ToolTip.visible: ghHover.hovered
                    ToolTip.text: "Open GitHub repository — lim8701/FilmRawstery"
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "GitHub ↗"
                        color: ghHover.hovered ? "#8ab4f8" : "#8a8a8a"
                        font.pixelSize: 12
                        font.underline: ghHover.hovered
                    }
                    HoverHandler { id: ghHover }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Qt.openUrlExternally("https://github.com/lim8701/FilmRawstery")
                    }
                    // 새 버전 배지(앰버) — 시작 시 GitHub 릴리스 확인(controller.updateVersion).
                    // 전체영역 MouseArea 보다 뒤(위) 선언이라 클릭이 배지로 감.
                    Text {
                        visible: controller.updateVersion !== ""
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: "↑ " + controller.updateVersion + " available"
                        color: updHover.hovered ? "#f0b945" : "#E0A226"
                        font.pixelSize: 12
                        font.underline: updHover.hovered
                        ToolTip.visible: updHover.hovered
                        ToolTip.text: "New version available — open the release page"
                        HoverHandler { id: updHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: Qt.openUrlExternally(controller.updateUrl)
                        }
                    }
                }
            }
        }

        // ---------- 탐색기 show/hide 핸들 (세로로 꽉 찬 얇은 바) ----------
        // 패널이 숨겨져도 항상 보여 다시 열 수 있게 한다.
        Rectangle {
            Layout.preferredWidth: 12
            Layout.fillHeight: true
            color: handleArea.containsMouse ? "#3a3f4b" : "#222"

            Text {
                anchors.centerIn: parent
                text: win.showExplorer ? "‹" : "›"
                color: "#cfcfcf"
                font.pixelSize: 16
            }

            MouseArea {
                id: handleArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: win.showExplorer = !win.showExplorer
            }

            ToolTip.visible: handleArea.containsMouse
            ToolTip.delay: 1500        // 호버 즉시 말고 1.5초 뒤 표시
            ToolTip.text: (win.showExplorer ? "Hide explorer" : "Show explorer") + " (B)"
        }

        // ---------- 이미지 영역 ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#1e1e1e"

            // (날짜 입력칸 포커스 해제는 창 전체 TapHandler 로 통합 — RowLayout 상단 참조)

            // 텍스처 소스 (화면에는 직접 안 보임, ShaderEffect 입력으로만 사용)
            Image {
                id: srcImage
                visible: false
                cache: false
                source: controller.imageUrl
            }

            // LUT 아틀라스 텍스처. nearest 필터를 위해 smooth:false 필수.
            Image {
                id: lutImage
                visible: false
                cache: false
                smooth: false
                source: "image://lut/" + win.simKeys[simCombo.currentIndex]
            }

            // 디스플레이 색관리 LUT 아틀라스(sRGB→모니터). 수동 트라이리니어라 smooth:false 필수.
            Image {
                id: cmLutImage
                visible: false
                cache: false
                smooth: false
                source: controller.cmLutUrl
            }

            // 톤 커브 1D LUT 텍스처 (256x1). 보간 위해 smooth:true.
            Image {
                id: curveImage
                visible: false
                cache: false
                smooth: true
                source: controller.curveUrl
            }

            // 날짜 스탬프 오버레이 텍스처(프록시 RGBA). 셰이더가 가산 합성.
            Image {
                id: stampImage
                visible: false
                cache: false
                smooth: true
                source: controller.stampUrl
            }

            // 하늘 마스크 텍스처(프록시 크기 단일채널). 셰이더가 하늘 로컬조정에 게이팅.
            Image {
                id: skyMaskImage
                visible: false
                cache: false
                smooth: true
                source: controller.skyMaskUrl
            }

            // 디헤이즈 투과율 맵(DCP, 소형 단일채널 — bilinear 업샘플 위해 smooth:true).
            // 없으면 1x1 흰색(t=1) → 물리 분기 항등. 이미지당 1회 갱신(hazeChanged).
            Image {
                id: hazeImage
                visible: false
                cache: false
                smooth: true
                source: controller.hazeUrl
            }

            // 휘도 NR 베이스(가이디드 필터 디노이즈드 중성 luma, 프록시 해상도 16bit 그레이).
            // 준비 전(1x1)엔 셰이더 nrOn 게이트가 휘도 NR 을 끔. 이미지당 1회 갱신(nrChanged).
            Image {
                id: nrBaseImage
                visible: false
                cache: false
                smooth: true
                source: controller.nrBaseUrl
            }

            // ── GPU export: 풀해상도를 프리뷰와 **동일한 adjust.frag** 로 렌더(프리뷰=Export) ──
            //   온디맨드(렌더=GPU 일 때만 active). src 만 풀해상도, 블러 텍스처는 프록시 것 재사용
            //   (로컬대비/톤마스크 성격을 프리뷰와 동일하게). uniform 바인딩은 pipe 와 반드시 동일.
            Loader {
                id: gpuExportLoader
                active: false
                sourceComponent: Component { Item {
                    property bool grabPending: false
                    function doGrab() {
                        pipeFull.grabToImage(function(res) {
                            controller.saveGrab(res.image)
                            Qt.callLater(function() { gpuExportLoader.active = false })
                        }, Qt.size(pipeFull.width, pipeFull.height))
                    }
                    Image {
                        id: srcFull; visible: false; cache: false; smooth: true
                        source: controller.fullUrl
                        onStatusChanged: {
                            if (status === Image.Ready && grabPending) {
                                grabPending = false; doGrab()
                            } else if (status === Image.Error && grabPending) {
                                // 풀해상도 로드 실패 → export 상태 복구(멈춤 방지) + 로더 해제
                                grabPending = false
                                controller.abortGpuExport()
                                Qt.callLater(function() { gpuExportLoader.active = false })
                            }
                        }
                    }
                    Connections {
                        target: controller
                        function onFullReady() {
                            if (srcFull.status === Image.Ready) doGrab()
                            else if (srcFull.status === Image.Error) {
                                controller.abortGpuExport()
                                Qt.callLater(function() { gpuExportLoader.active = false })
                            } else grabPending = true
                        }
                        // 파이썬 측 디코드 실패 — QML 은 감지 못 하므로 여기서 로더 해제
                        // (안 하면 pipeFull 파이프라인이 계속 살아있어 재평가됨).
                        function onFullAborted() {
                            grabPending = false
                            Qt.callLater(function() { gpuExportLoader.active = false })
                        }
                    }
                    ShaderEffect {
                        id: pipeFull
                        width: srcFull.implicitWidth > 0 ? srcFull.implicitWidth : 1
                        height: srcFull.implicitHeight > 0 ? srcFull.implicitHeight : 1
                        visible: false
                        // ⚠️아래 uniform 바인딩은 pipe 와 동일하게 유지해야 함(프리뷰=Export).
                        property variant src: srcFull
                        property variant dispSrc: dispSrcTex
                        property variant lut: lutImage
                        property variant curve: curveImage
                        property variant texBlur: texBlurTex
                        property variant claBlur: claBlurTex
                        property variant sharpBlur: sharpBlurTex
                        property variant stampTex: stampImage
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: 0.0   // 스탬프는 셰이더(원본 코너)가 아니라 cropClip 위 stampOverlay 가 최종 프레임 기준으로 그림
                        property real stampStrength: 0.92
                        property real exposure: expSlider.value
                        property real contrast: conSlider.value
                        property real highlights: hiSlider.value
                        property real shadows: shSlider.value
                        property real whites: whSlider.value
                        property real blacks: blSlider.value
                        property real texAmt: texSlider.value
                        property real clarity: claritySlider.value
                        property real dehaze: dehazeSlider.value
                        property real saturation: satSlider.value
                        property real vibrance: vibSlider.value
                        property vector4d hslHa: Qt.vector4d(win.hslH[0], win.hslH[1], win.hslH[2], win.hslH[3])
                        property vector4d hslHb: Qt.vector4d(win.hslH[4], win.hslH[5], win.hslH[6], win.hslH[7])
                        property vector4d hslSa: Qt.vector4d(win.hslS[0], win.hslS[1], win.hslS[2], win.hslS[3])
                        property vector4d hslSb: Qt.vector4d(win.hslS[4], win.hslS[5], win.hslS[6], win.hslS[7])
                        property vector4d hslLa: Qt.vector4d(win.hslL[0], win.hslL[1], win.hslL[2], win.hslL[3])
                        property vector4d hslLb: Qt.vector4d(win.hslL[4], win.hslL[5], win.hslL[6], win.hslL[7])
                        property real sharpenAmt: sharpAmtSlider.value
                        property real sharpenDetail: sharpDetailSlider.value
                        property real sharpenMask: sharpMaskSlider.value
                        property real texelW: 1.0 / Math.max(1, width)
                        property real texelH: 1.0 / Math.max(1, height)
                        property real vignette: vignetteSlider.value
                        property real grainAmt: grainSlider.value
                        property real grainSize: grainSizeSlider.value
                        property real grainAspect: width / Math.max(1, height)
                        property real clipWarn: 0.0   // export 는 클리핑 오버레이 미적용
                        property real displayCM: 0.0  // export 는 디스플레이 색관리 미적용(표준 sRGB)
                        property variant cmLut: cmLutImage
                        property real cmLutSize: controller.cmLutN
                        // 컬러 그레이딩 — 프리뷰(pipe)와 동일 바인딩(export 일치).
                        property real cgHueSh: cgShHueSlider.value / 360.0
                        property real cgSatSh: cgShSatSlider.value
                        property real cgHueMid: cgMidHueSlider.value / 360.0
                        property real cgSatMid: cgMidSatSlider.value
                        property real cgHueHi: cgHiHueSlider.value / 360.0
                        property real cgSatHi: cgHiSatSlider.value
                        property real cgBalance: cgBalanceSlider.value
                        property real lumaNR: lumaNrSlider.value
                        property real colorNR: colorNrSlider.value
                        property vector3d wbGain: win.wbPreview(tempSlider.value, tintSlider.value)
                        property real wbR: wbGain.x
                        property real wbG: wbGain.y
                        property real wbB: wbGain.z
                        property real lutSize: lutN
                        property real lutStrength: simStrengthSlider.value
                        property int lutEnabled: simCombo.currentIndex === 0 ? 0 : 1
                        // 하늘(로컬) 조정 — 프리뷰(pipe)와 동일 바인딩. 오버레이는 export 미적용(0).
                        property variant skyMask: skyMaskImage
                        property real skyExp: skyExpSlider.value
                        property real skyTemp: skyTempSlider.value
                        property real skyTint: skyTintSlider.value
                        property real skySat: skySatSlider.value
                        property real skyHi: skyHiSlider.value
                        property real skyShadows: skyShadowsSlider.value
                        property real skyTexture: skyTextureSlider.value
                        property real skyClarity: skyClaritySlider.value
                        property real skyDehaze: skyDehazeSlider.value
                        property real skyContrast: skyContrastSlider.value
                        property real skyInvert: skyInvertCheck.checked ? 1.0 : 0.0
                        property real skyHasMask: controller.hasSkyMask ? 1.0 : 0.0
                        property real skyShowMask: 0.0
                        // 현상 계수(coeffs.py 단일 진실원) uniform 주입 — pipeline.py 와 값 공유.
                        property real dehazeKLocal: controller.adjustCoeffs["dehazeKLocal"]
                        property real dehazeKContrast: controller.adjustCoeffs["dehazeKContrast"]
                        property real dehazeKVeil: controller.adjustCoeffs["dehazeKVeil"]
                        property real dehazeKSat: controller.adjustCoeffs["dehazeKSat"]
                        property real clarityK: controller.adjustCoeffs["clarityK"]
                        property real textureK: controller.adjustCoeffs["textureK"]
                        property real skyTempK: controller.adjustCoeffs["skyTempK"]
                        property real skyTintK: controller.adjustCoeffs["skyTintK"]
                        property real toneHiShK: controller.adjustCoeffs["toneHiShK"]
                        property real toneWhBlK: controller.adjustCoeffs["toneWhBlK"]
                        property real vignetteK: controller.adjustCoeffs["vignetteK"]
                        property real grainK: controller.adjustCoeffs["grainK"]
                        property real sharpenK: controller.adjustCoeffs["sharpenK"]
                        property real hslHueDegK: controller.adjustCoeffs["hslHueDegK"]
                        property real hslLumK: controller.adjustCoeffs["hslLumK"]
                        property real colorGradeK: controller.adjustCoeffs["colorGradeK"]
                        // 디헤이즈 물리(DCP) — 프리뷰(pipe)와 동일 바인딩(프리뷰=Export).
                        property variant hazeT: hazeImage
                        property real hazeAr: controller.hazeA[0]
                        property real hazeAg: controller.hazeA[1]
                        property real hazeAb: controller.hazeA[2]
                        property real hazeConf: controller.hazeConf
                        property real dehazeKTmin: controller.adjustCoeffs["dehazeKTmin"]
                        property real dehazeKResid: controller.adjustCoeffs["dehazeKResid"]
                        // NR 베이스 — 프리뷰(pipe)와 동일 바인딩(프리뷰=Export).
                        property variant nrBase: nrBaseImage
                        property real nrOn: controller.nrReady ? 1.0 : 0.0
                        property real nrChroma: controller.nrChroma ? 1.0 : 0.0
                        fragmentShader: "../shaders/adjust.frag.qsb"
                    }
                }}
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // 상단: 열린 파일 경로\파일명 표시줄
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 26
                    color: "#252525"

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        color: "#cfcfcf"
                        font.pixelSize: 12
                        elide: Text.ElideMiddle
                        width: parent.width - 20
                        text: controller.imagePath !== ""
                              ? controller.imagePath
                              : "No file open"
                    }
                }

                Item {
                    id: viewport
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    property real margin: 12
                    property real availW: width - margin * 2
                    property real availH: height - margin * 2
                    // 처리 해상도 = 프록시 native (모니터 해상도와 무관하게 GPU 부하 고정)
                    property real procW: srcImage.implicitWidth > 0 ? srcImage.implicitWidth : 1
                    property real procH: srcImage.implicitHeight > 0 ? srcImage.implicitHeight : 1
                    property real claW: Math.max(1, Math.round(procW / 4))   // 클래리티 블러 다운샘플
                    property real claH: Math.max(1, Math.round(procH / 4))

                    // === 회전/크롭(지오메트리) 미리보기 기하 (export numpy 와 동일 정의) ===
                    // 크롭 패널(activePanel===1)에서는 전체 캔버스+편집 박스를, 그 외엔 크롭 결과를 표시.
                    property bool cropEdit: win.activePanel === 1
                    property bool geoOdd: (win.quarterTurns % 2) !== 0
                    property real caW: geoOdd ? procH : procW     // 90° 회전 후 캔버스 크기
                    property real caH: geoOdd ? procW : procH
                    property real cA: caW / Math.max(1, caH)       // 캔버스 비율(가로/세로)
                    // 크롭 결과 비율(가로/세로)
                    property real cropDispAspect: (win.cropW * caW) / Math.max(1e-4, win.cropH * caH)
                    // 스트레이튼(자유각) 채움 줌: 회전해도 빈 모서리가 안 생기게 캔버스를 채움.
                    property real straightenZoom: {
                        var t = Math.abs(rotAngleSlider.value) * Math.PI / 180.0
                        return Math.cos(t) + Math.max(cA, 1.0 / cA) * Math.sin(t)
                    }
                    // 캔버스 전체 표시 크기: 편집=캔버스 fit, 결과=크롭이 viewport 를 채우게 캔버스 확대
                    property real canvasDispW: cropEdit
                        ? Math.min(availW, availH * cA)
                        : Math.min(availW, availH * cropDispAspect) / Math.max(1e-4, win.cropW)
                    property real canvasDispH: canvasDispW / Math.max(1e-4, cA)
                    // 캔버스 px -> 화면 fit 스케일(스트레이튼 줌은 원근 앞에 별도 적용 → export 와 순서 일치)
                    property real fitScale: canvasDispW / Math.max(1, caW)
                    // 표시 클립 박스: 편집=캔버스 전체, 결과=크롭 영역
                    property real clipW: cropEdit ? canvasDispW : (canvasDispW * win.cropW)
                    property real clipH: cropEdit ? canvasDispH : (canvasDispH * win.cropH)

                    // === 1:1 확대 & 패닝(핀트 확인). 프록시(≤2560) 기준 1:1(proxy px:screen px). ===
                    clip: true                       // 확대 시 이미지가 패널을 침범하지 않게
                    property bool zoomed: false
                    property real panX: 0
                    property real panY: 0
                    property real zoomFactor: 1.0 / Math.max(1e-4, fitScale)
                    function clampPan() {
                        var mx = Math.max(0, (clipW * zoomFactor - width) / 2)
                        var my = Math.max(0, (clipH * zoomFactor - height) / 2)
                        panX = Math.max(-mx, Math.min(mx, panX))
                        panY = Math.max(-my, Math.min(my, panY))
                    }
                    function zoomToPoint(px, py) {     // 클릭점을 중앙으로 → 확대
                        panX = -(px - width / 2) * zoomFactor
                        panY = -(py - height / 2) * zoomFactor
                        zoomed = true; clampPan()
                    }
                    function resetZoom() { zoomed = false; panX = 0; panY = 0 }
                    onCropEditChanged: if (cropEdit) resetZoom()   // 크롭 패널 진입 시 확대 해제

                    // 원근(키스톤)+배율 호모그래피 (export pipeline._persp_homography 와 동일 수식).
                    // GEO_PERSP_K=0.35 강도 일치 필수. 중심 기준, 소스(procW/procH) 정규화.
                    property matrix4x4 perspMat: {
                        var cx = procW / 2, cy = procH / 2
                        var s = geoScaleSlider.value / 100.0
                        var kxn = (geoHSlider.value / 100.0) * 0.35
                        var kyn = (geoVSlider.value / 100.0) * 0.35
                        var kx = kxn / Math.max(1, procW / 2)
                        var ky = kyn / Math.max(1, procH / 2)
                        var w0 = 1.0 - kx * cx - ky * cy
                        var h00 = s + cx * kx, h01 = cx * ky, h02 = cx * w0 - s * cx
                        var h10 = cy * kx, h11 = s + cy * ky, h12 = cy * w0 - s * cy
                        return Qt.matrix4x4(h00, h01, 0, h02,
                                            h10, h11, 0, h12,
                                            0, 0, 1, 0,
                                            kx, ky, 0, w0)
                    }

                    // --- dispSrc: 카메라네이티브 src -> display sRGB(as-shot WB) 변환 ---
                    // 블러 체인과 메인 셰이더의 로컬대비 base. srcImage·asShot 에만 의존.
                    ShaderEffect {
                        id: dispPre; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: srcImage
                        property real relR: win.asShotRelGain.x
                        property real relG: win.asShotRelGain.y
                        property real relB: win.asShotRelGain.z
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        fragmentShader: "../shaders/convert.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: dispSrcTex; sourceItem: dispPre; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true; smooth: true
                    }

                    // Compare original 모드용: 무편집 display sRGB(dispPre)에 디스플레이 색관리만 적용.
                    // pipe 와 동일한 CM 을 거쳐 'before' 도 광색역 패널에서 정확히 표시(프리뷰 일관).
                    ShaderEffect {
                        id: comparePipe; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: dispSrcTex
                        property variant cmLut: cmLutImage
                        property real displayCM: (win.displayCM && controller.hasDisplayCM) ? 1.0 : 0.0
                        property real cmLutSize: controller.cmLutN
                        fragmentShader: "../shaders/displaycm.frag.qsb"
                    }

                    // --- 로컬대비용 가우시안 블러 (dispSrc 에만 의존 -> 로드 시 1회 계산) ---
                    // 텍스처: 작은 반경, 풀 프록시 해상도
                    ShaderEffect {
                        id: texBlurH; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: dispSrcTex
                        property vector2d dir: Qt.vector2d(1.25 / viewport.procW, 0)
                        fragmentShader: "../shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: texBlurHSrc; sourceItem: texBlurH; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true
                    }
                    ShaderEffect {
                        id: texBlurV; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: texBlurHSrc
                        property vector2d dir: Qt.vector2d(0, 1.25 / viewport.procH)
                        fragmentShader: "../shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: texBlurTex; sourceItem: texBlurV; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true; smooth: true
                    }
                    // 클래리티: 큰 반경, 1/4 다운샘플
                    ShaderEffect {
                        id: claBlurH; visible: false
                        width: viewport.claW; height: viewport.claH
                        property variant src: dispSrcTex
                        property vector2d dir: Qt.vector2d(1.5 / viewport.claW, 0)
                        fragmentShader: "../shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: claBlurHSrc; sourceItem: claBlurH; visible: false
                        textureSize: Qt.size(viewport.claW, viewport.claH)
                        hideSource: true; live: true
                    }
                    ShaderEffect {
                        id: claBlurV; visible: false
                        width: viewport.claW; height: viewport.claH
                        property variant src: claBlurHSrc
                        property vector2d dir: Qt.vector2d(0, 1.5 / viewport.claH)
                        fragmentShader: "../shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: claBlurTex; sourceItem: claBlurV; visible: false
                        textureSize: Qt.size(viewport.claW, viewport.claH)
                        hideSource: true; live: true; smooth: true
                    }
                    // 샤프닝: 가변 반경 블러(Radius 슬라이더에 dir 바인딩 → 반경 변경 시만 재계산)
                    ShaderEffect {
                        id: sharpBlurH; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: dispSrcTex
                        property vector2d dir: Qt.vector2d(sharpRadiusSlider.value / viewport.procW, 0)
                        fragmentShader: "../shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: sharpBlurHSrc; sourceItem: sharpBlurH; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true
                    }
                    ShaderEffect {
                        id: sharpBlurV; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: sharpBlurHSrc
                        property vector2d dir: Qt.vector2d(0, sharpRadiusSlider.value / viewport.procH)
                        fragmentShader: "../shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: sharpBlurTex; sourceItem: sharpBlurV; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true; smooth: true
                    }

                    // 파이프라인 셰이더: 프록시 해상도에서만 렌더(직접 표시 안 함)
                    ShaderEffect {
                        id: pipe
                        width: viewport.procW
                        height: viewport.procH
                        visible: false

                        // 셰이더 uniform 과 이름이 일치해야 함
                        property variant src: srcImage
                        property variant dispSrc: dispSrcTex
                        property variant lut: lutImage
                        property variant curve: curveImage
                        property variant texBlur: texBlurTex
                        property variant claBlur: claBlurTex
                        property variant sharpBlur: sharpBlurTex
                        property variant stampTex: stampImage
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: 0.0   // 스탬프는 셰이더(원본 코너)가 아니라 cropClip 위 stampOverlay 가 최종 프레임 기준으로 그림
                        property real stampStrength: 0.92
                        property real exposure: expSlider.value
                        property real contrast: conSlider.value
                        property real highlights: hiSlider.value
                        property real shadows: shSlider.value
                        property real whites: whSlider.value
                        property real blacks: blSlider.value
                        property real texAmt: texSlider.value
                        property real clarity: claritySlider.value
                        property real dehaze: dehazeSlider.value
                        property real saturation: satSlider.value
                        property real vibrance: vibSlider.value
                        // HSL 컬러 믹서 (8색상대 → vec4 ×2씩: a=0..3, b=4..7)
                        property vector4d hslHa: Qt.vector4d(win.hslH[0], win.hslH[1], win.hslH[2], win.hslH[3])
                        property vector4d hslHb: Qt.vector4d(win.hslH[4], win.hslH[5], win.hslH[6], win.hslH[7])
                        property vector4d hslSa: Qt.vector4d(win.hslS[0], win.hslS[1], win.hslS[2], win.hslS[3])
                        property vector4d hslSb: Qt.vector4d(win.hslS[4], win.hslS[5], win.hslS[6], win.hslS[7])
                        property vector4d hslLa: Qt.vector4d(win.hslL[0], win.hslL[1], win.hslL[2], win.hslL[3])
                        property vector4d hslLb: Qt.vector4d(win.hslL[4], win.hslL[5], win.hslL[6], win.hslL[7])
                        property real sharpenAmt: sharpAmtSlider.value
                        property real sharpenDetail: sharpDetailSlider.value
                        property real sharpenMask: sharpMaskSlider.value
                        property real texelW: 1.0 / Math.max(1, viewport.procW)
                        property real texelH: 1.0 / Math.max(1, viewport.procH)
                        property real vignette: vignetteSlider.value
                        property real grainAmt: grainSlider.value
                        property real grainSize: grainSizeSlider.value
                        property real grainAspect: viewport.procW / Math.max(1, viewport.procH)
                        property real clipWarn: win.clipWarn ? 1.0 : 0.0   // 클리핑 경고 오버레이(프리뷰 전용)
                        // 디스플레이 색관리(프리뷰 전용): 토글 ON + 유효 CM LUT 있을 때만.
                        property real displayCM: (win.displayCM && controller.hasDisplayCM) ? 1.0 : 0.0
                        property variant cmLut: cmLutImage
                        property real cmLutSize: controller.cmLutN
                        // 컬러 그레이딩(스플릿 토닝): hue 슬라이더(도) → 0..1 정규화.
                        property real cgHueSh: cgShHueSlider.value / 360.0
                        property real cgSatSh: cgShSatSlider.value
                        property real cgHueMid: cgMidHueSlider.value / 360.0
                        property real cgSatMid: cgMidSatSlider.value
                        property real cgHueHi: cgHiHueSlider.value / 360.0
                        property real cgSatHi: cgHiSatSlider.value
                        property real cgBalance: cgBalanceSlider.value
                        property real lumaNR: lumaNrSlider.value
                        property real colorNR: colorNrSlider.value
                        // WB 게인: TREF 베이크 대비 상대게인(카메라공간). 재디코딩 없이 실시간.
                        property vector3d wbGain: win.wbPreview(tempSlider.value, tintSlider.value)
                        property real wbR: wbGain.x
                        property real wbG: wbGain.y
                        property real wbB: wbGain.z
                        property real lutSize: lutN             // context property (LUT 크기 N)
                        property real lutStrength: simStrengthSlider.value
                        property int lutEnabled: simCombo.currentIndex === 0 ? 0 : 1
                        // 하늘(로컬) 조정 — ML 세그 마스크에만 적용. showSkyMask=선택영역 시각화(프리뷰 전용).
                        property variant skyMask: skyMaskImage
                        property real skyExp: skyExpSlider.value
                        property real skyTemp: skyTempSlider.value
                        property real skyTint: skyTintSlider.value
                        property real skySat: skySatSlider.value
                        property real skyHi: skyHiSlider.value
                        property real skyShadows: skyShadowsSlider.value
                        property real skyTexture: skyTextureSlider.value
                        property real skyClarity: skyClaritySlider.value
                        property real skyDehaze: skyDehazeSlider.value
                        property real skyContrast: skyContrastSlider.value
                        property real skyInvert: skyInvertCheck.checked ? 1.0 : 0.0
                        property real skyHasMask: controller.hasSkyMask ? 1.0 : 0.0
                        property real skyShowMask: win.showSkyMask ? 1.0 : 0.0
                        // 현상 계수(coeffs.py 단일 진실원) uniform 주입 — pipeline.py 와 값 공유.
                        property real dehazeKLocal: controller.adjustCoeffs["dehazeKLocal"]
                        property real dehazeKContrast: controller.adjustCoeffs["dehazeKContrast"]
                        property real dehazeKVeil: controller.adjustCoeffs["dehazeKVeil"]
                        property real dehazeKSat: controller.adjustCoeffs["dehazeKSat"]
                        property real clarityK: controller.adjustCoeffs["clarityK"]
                        property real textureK: controller.adjustCoeffs["textureK"]
                        property real skyTempK: controller.adjustCoeffs["skyTempK"]
                        property real skyTintK: controller.adjustCoeffs["skyTintK"]
                        property real toneHiShK: controller.adjustCoeffs["toneHiShK"]
                        property real toneWhBlK: controller.adjustCoeffs["toneWhBlK"]
                        property real vignetteK: controller.adjustCoeffs["vignetteK"]
                        property real grainK: controller.adjustCoeffs["grainK"]
                        property real sharpenK: controller.adjustCoeffs["sharpenK"]
                        property real hslHueDegK: controller.adjustCoeffs["hslHueDegK"]
                        property real hslLumK: controller.adjustCoeffs["hslLumK"]
                        property real colorGradeK: controller.adjustCoeffs["colorGradeK"]
                        // 디헤이즈 물리(DCP): 투과율 맵 + 대기광 + conf(어두운 장면 0 → 톤모델 폴백).
                        property variant hazeT: hazeImage
                        property real hazeAr: controller.hazeA[0]
                        property real hazeAg: controller.hazeA[1]
                        property real hazeAb: controller.hazeA[2]
                        property real hazeConf: controller.hazeConf
                        property real dehazeKTmin: controller.adjustCoeffs["dehazeKTmin"]
                        property real dehazeKResid: controller.adjustCoeffs["dehazeKResid"]
                        // NR 베이스: 디노이즈드 중성(준비 전엔 nrOn=0 → 무동작).
                        // 가이디드=luma 그레이 / AI=RGB(nrChroma=1 → 컬러 NR 이 AI 크로마 사용)
                        property variant nrBase: nrBaseImage
                        property real nrOn: controller.nrReady ? 1.0 : 0.0
                        property real nrChroma: controller.nrChroma ? 1.0 : 0.0

                        fragmentShader: "../shaders/adjust.frag.qsb"
                    }

                    // 고정 크기 FBO(프록시 해상도)에 렌더 -> 회전/크롭(지오메트리)을 뷰 변환으로
                    // 적용. cropClip 이 표시 영역(편집=캔버스 전체 / 결과=크롭)으로 잘라낸다.
                    // export numpy 와 동일 기하 순서: 플립 -> 90° -> 스트레이튼 -> 자유 사각 크롭.
                    Item {
                        id: cropClip
                        visible: srcImage.status === Image.Ready
                        anchors.centerIn: parent
                        width: viewport.clipW
                        height: viewport.clipH
                        clip: true
                        // 1:1 확대 & 패닝 — 중앙 기준 스케일 후 팬(translate). 핀트 확인용.
                        scale: viewport.zoomed ? viewport.zoomFactor : 1.0
                        transform: Translate {
                            x: viewport.zoomed ? viewport.panX : 0
                            y: viewport.zoomed ? viewport.panY : 0
                        }

                        // 캔버스 홀더: 편집모드=(0,0)으로 캔버스 전체가 cropClip 채움,
                        // 결과모드=크롭 영역의 좌상단이 cropClip 좌상단에 오도록 음수 오프셋.
                        Item {
                            id: canvasHolder
                            width: viewport.canvasDispW
                            height: viewport.canvasDispH
                            x: viewport.cropEdit ? 0 : -win.cropX * viewport.canvasDispW
                            y: viewport.cropEdit ? 0 : -win.cropY * viewport.canvasDispH
                            // 회전/원근으로 변환된 텍스처 가장자리 안티엘리어싱:
                            // 자식(transform 적용된 pipeView)을 멀티샘플 FBO 에 렌더.
                            layer.enabled: true
                            layer.smooth: true
                            layer.samples: 4
                            // 1:1 확대 시 FBO 를 프록시 native 해상도로 렌더(아니면 fit 해상도라 확대=흐릿).
                            // 평소엔 Qt.size(0,0)=아이템 크기(기존 동작 유지).
                            layer.textureSize: viewport.zoomed ? Qt.size(viewport.caW, viewport.caH)
                                                               : Qt.size(0, 0)

                            ShaderEffectSource {
                                id: pipeView
                                // 원본 비교 중에는 무편집 현상(dispPre)을 같은 변환/크롭으로 표시.
                                sourceItem: win.compareOn ? comparePipe : pipe
                                textureSize: Qt.size(viewport.procW, viewport.procH)
                                width: viewport.procW
                                height: viewport.procH
                                anchors.centerIn: parent
                                hideSource: true
                                smooth: true
                                live: true
                                // transform 리스트는 나열 순서대로 적용(앞=먼저=안쪽): 플립 -> 회전 -> 줌.
                                transform: [
                                    Scale {
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        xScale: flipHBtn.checked ? -1 : 1
                                        yScale: flipVBtn.checked ? -1 : 1
                                    },
                                    Rotation {
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        angle: win.quarterTurns * 90 + rotAngleSlider.value
                                    },
                                    Scale {   // 스트레이튼 채움 줌(원근 앞 — export 와 동일 순서 H∘Z∘R)
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        xScale: viewport.straightenZoom; yScale: viewport.straightenZoom
                                    },
                                    Matrix4x4 { matrix: viewport.perspMat },   // 원근(키스톤)+배율
                                    Scale {   // 화면 fit (최외곽)
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        xScale: viewport.fitScale; yScale: viewport.fitScale
                                    }
                                ]
                            }
                        }

                        // 날짜 스탬프(필름 데이트백) 오버레이 — cropClip(=최종 크롭 프레임) 코너에 배치.
                        // 스프라이트(image://stamp)에 '검정 위 글로우 하이브리드'가 이미 베이크돼 있어
                        // (date_stamp.render_sprite), 배경 재캡처 없이 QML 기본 source-over 합성만으로 데이트백
                        // 룩이 난다. 과거엔 screen 합성을 위해 배경(canvasHolder)을 ShaderEffect 로 다시 캡처했으나
                        // (bgTex), 그 재캡처가 줌/레이어 조건에서 배경을 밀고 가장자리 검정선을 만들어 제거함.
                        //   - 트레이드오프: 밝은 배경에서 export(screen 70%+over 30%)보다 아주 약간 더 또렷
                        //     (프리뷰 전용 — date_stamp.stamp_export = 최종 결과물은 그대로 정확).
                        //   - wRatio/hRatio=스프라이트(W,H)/짧은변, 마진=stampMargin. 크롭편집·비교 중 숨김.
                        Image {
                            id: stampOverlay
                            source: controller.stampUrl
                            cache: false; smooth: true; asynchronous: false
                            // 스프라이트 알파는 render_sprite 에서 A2/s 로 구워져 있어(합성 때 ×s 가정),
                            // export(stamp_export)는 ×STAMP_STRENGTH 로 상쇄한다. 프리뷰도 동일하게
                            // opacity=STAMP_STRENGTH 를 곱해야 밝기가 맞는다(없으면 ~8.7% 더 진함).
                            opacity: 0.92     // = date_stamp.STAMP_STRENGTH
                            visible: win.dateStamp && controller.stampText !== ""
                                     && !viewport.cropEdit && !win.compareOn
                            property real shortEdge: Math.min(cropClip.width, cropClip.height)
                            width: controller.stampWRatio * shortEdge
                            height: controller.stampHRatio * shortEdge
                            property string corner: controller.stampCorner   // br/bl/tl/tr
                            property real margin: controller.stampMargin * shortEdge
                            x: (corner === "br" || corner === "tr") ? parent.width - width - margin : margin
                            y: (corner === "br" || corner === "bl") ? parent.height - height - margin : margin
                        }
                    }

                    // 1:1 확대 & 패닝 입력(크롭 패널 외): 더블클릭=확대/해제(클릭점 중앙), 드래그=팬.
                    MouseArea {
                        anchors.fill: parent
                        enabled: !viewport.cropEdit && cropClip.visible
                        cursorShape: viewport.zoomed ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                                                     : Qt.ArrowCursor
                        property real _px: 0
                        property real _py: 0
                        onPressed: (m) => { _px = m.x; _py = m.y }
                        onPositionChanged: (m) => {
                            if (!pressed || !viewport.zoomed) return
                            viewport.panX += m.x - _px; viewport.panY += m.y - _py
                            _px = m.x; _py = m.y; viewport.clampPan()
                        }
                        onDoubleClicked: (m) => {
                            if (viewport.zoomed) viewport.resetZoom()
                            else viewport.zoomToPoint(m.x, m.y)
                        }
                    }

                    // === SAM 클릭 선택(Masking 탭 선택 모드): 이미지 대상 클릭 → 마스크 ===
                    // pan/zoom MouseArea 뒤(위)에 선언 → 선택 모드일 때 클릭을 먼저 받는다.
                    // pipeView.mapFromItem 으로 화면좌표→프록시 픽셀 역변환(회전/줌/팬/원근 반영).
                    MouseArea {
                        id: samClickArea
                        anchors.fill: parent
                        enabled: win.samSelectMode && cropClip.visible
                                 && controller.imagePath !== "" && !controller.skyBusy
                        cursorShape: Qt.CrossCursor
                        onClicked: (m) => {
                            var p = pipeView.mapFromItem(samClickArea, m.x, m.y)
                            var nx = p.x / Math.max(1, viewport.procW)
                            var ny = p.y / Math.max(1, viewport.procH)
                            if (nx >= 0 && nx <= 1 && ny >= 0 && ny <= 1)
                                win.selectMaskByPoint(nx, ny)
                        }
                    }

                    // === 미니맵(확대 시): 전체(크롭 결과) 중 현재 보이는 영역 표시. 우하단. ===
                    Item {
                        id: minimap
                        visible: viewport.zoomed && !viewport.cropEdit && cropClip.visible
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 12
                        property real maxMM: 180                       // 긴 변 최대 px
                        property real crW: Math.max(1, viewport.clipW) // 크롭 결과 표시 폭(zoom=1)
                        property real crH: Math.max(1, viewport.clipH)
                        property real mmScale: maxMM / Math.max(crW, crH)
                        width: crW * mmScale
                        height: crH * mmScale

                        // 배경: 전체 크롭 결과 썸네일(canvasHolder 를 줌/팬 없이 작게 복제)
                        Rectangle {
                            anchors.fill: parent
                            color: "#000000"
                            border.color: "#80ffffff"; border.width: 1
                            radius: 3
                            clip: true
                            ShaderEffectSource {
                                sourceItem: canvasHolder      // 지오메트리 변환된 캔버스(줌/팬은 cropClip 에 있어 미반영)
                                live: true
                                smooth: true
                                width: viewport.canvasDispW * minimap.mmScale
                                height: viewport.canvasDispH * minimap.mmScale
                                x: -win.cropX * viewport.canvasDispW * minimap.mmScale   // 크롭 영역만 보이게 오프셋
                                y: -win.cropY * viewport.canvasDispH * minimap.mmScale
                            }
                        }
                        // 현재 보이는 영역 사각형 (pan/zoom 으로부터 콘텐츠 대비 분율 계산)
                        Rectangle {
                            color: "#33ffd24a"
                            border.color: "#ffd24a"; border.width: 1.5
                            radius: 1
                            property real cw: viewport.clipW * viewport.zoomFactor   // 줌 콘텐츠 폭(px)
                            property real ch: viewport.clipH * viewport.zoomFactor
                            property real lf: Math.max(0, 0.5 - (viewport.panX + viewport.width / 2) / cw)
                            property real tf: Math.max(0, 0.5 - (viewport.panY + viewport.height / 2) / ch)
                            property real wf: Math.min(1, viewport.width / cw)
                            property real hf: Math.min(1, viewport.height / ch)
                            x: lf * minimap.width
                            y: tf * minimap.height
                            width: Math.min(minimap.width - x, wf * minimap.width)
                            height: Math.min(minimap.height - y, hf * minimap.height)
                        }
                    }

                    // === 크롭 편집 오버레이 (크롭 패널에서만): 핸들=리사이즈, 내부=이동,
                    //     네 꼭짓점 외곽 부근 드래그=회전(스트레이튼). 캔버스 위에 정렬. ===
                    Item {
                        id: cropOverlay
                        visible: viewport.cropEdit && cropClip.visible
                        anchors.centerIn: parent
                        width: viewport.canvasDispW
                        height: viewport.canvasDispH

                        property real bl: win.cropX * width      // 박스 px 경계
                        property real bt: win.cropY * height
                        property real bw: win.cropW * width
                        property real bh: win.cropH * height
                        property bool rotating: false            // 회전 드래그 중(촘촘한 격자)
                        property bool rotHover: false            // 회전 영역 호버(회전 커서)
                        property real rotPx: 0                   // 회전 커서 위치(오버레이 좌표)
                        property real rotPy: 0
                        property int rotCorner: 0                // 활성 회전 코너(0=NW,1=NE,2=SW,3=SE)
                        // 크롭 조작(이동/리사이즈/회전) 진행 중 — undo 커밋 게이트(editDragActive)가 참조.
                        property int resizeDrags: 0              // 리사이즈 핸들(Repeater) press 카운터
                        readonly property bool dragging: rotating || cropMoveArea.pressed || resizeDrags > 0
                        // 크롭 패널을 벗어나면(핸들을 쥔 채 패널 전환 등) release 가 안 와 카운터가
                        // 양수로 고착 → dragging/editDragActive 가 영구 true(스냅샷·저장 중단, AI-NR
                        // 정지 latched)가 될 수 있다. 숨김 시 드래그 상태를 리셋해 방지.
                        onVisibleChanged: if (!visible) { resizeDrags = 0; rotating = false }

                        // (1) 바깥 어둡게(시각용, 마우스 비소비 -> 아래 회전 영역이 받음)
                        Rectangle { color: "#88000000"; x: 0; y: 0; width: parent.width; height: parent.bt }
                        Rectangle { color: "#88000000"; x: 0; y: parent.bt + parent.bh
                                    width: parent.width; height: parent.height - parent.bt - parent.bh }
                        Rectangle { color: "#88000000"; x: 0; y: parent.bt; width: parent.bl; height: parent.bh }
                        Rectangle { color: "#88000000"; x: parent.bl + parent.bw; y: parent.bt
                                    width: parent.width - parent.bl - parent.bw; height: parent.bh }

                        // (2) 회전 영역: 박스 네 꼭짓점 외곽 부근 드래그 -> 캔버스 중심 기준 각도변화.
                        //     박스 안쪽(이동)·정확한 코너(리사이즈 핸들)는 위에 있어 그쪽이 우선.
                        Repeater {
                            model: 4
                            delegate: MouseArea {
                                property int ci: index   // 0=NW,1=NE,2=SW,3=SE
                                // 드래그 중엔 영역을 크게 확장 -> 커서가 영역 밖으로 안 나가
                                // BlankCursor 가 끝까지 유지(OS 커서 재출현으로 인한 이중커서 방지).
                                property bool dragging: cropOverlay.rotating && cropOverlay.rotCorner === ci
                                property real cornerX: (ci === 1 || ci === 3) ? cropOverlay.bl + cropOverlay.bw : cropOverlay.bl
                                property real cornerY: (ci === 2 || ci === 3) ? cropOverlay.bt + cropOverlay.bh : cropOverlay.bt
                                width: dragging ? 8000 : 80
                                height: dragging ? 8000 : 80
                                x: dragging ? (cropOverlay.width / 2 - width / 2) : (cornerX - width / 2)
                                y: dragging ? (cropOverlay.height / 2 - height / 2) : (cornerY - height / 2)
                                hoverEnabled: true
                                cursorShape: Qt.BlankCursor     // 곡선 화살표(rotCursor)로 대체
                                property real startAng: 0
                                property real baseVal: 0
                                function angAt(m) {
                                    var p = mapToItem(cropOverlay, m.x, m.y)
                                    cropOverlay.rotPx = p.x; cropOverlay.rotPy = p.y
                                    return Math.atan2(p.y - cropOverlay.height / 2, p.x - cropOverlay.width / 2)
                                }
                                onEntered: {
                                    cropOverlay.rotCorner = ci
                                    cropOverlay.rotPx = x + width / 2; cropOverlay.rotPy = y + height / 2
                                    cropOverlay.rotHover = true
                                }
                                onExited: if (!pressed) cropOverlay.rotHover = false
                                onPressed: (mouse) => {
                                    cropOverlay.rotCorner = ci
                                    startAng = angAt(mouse)
                                    baseVal = rotAngleSlider.value
                                    cropOverlay.rotating = true
                                    cropOverlay.rotHover = true
                                }
                                onPositionChanged: (mouse) => {
                                    var a = angAt(mouse)
                                    if (pressed) {
                                        var d = (a - startAng) * 180.0 / Math.PI
                                        rotAngleSlider.value = Math.max(-45, Math.min(45, baseVal + d))
                                    }
                                }
                                onReleased: { cropOverlay.rotating = false; cropOverlay.rotHover = containsMouse }
                            }
                        }

                        // (3) 크롭 박스 테두리 + 격자 + 내부 이동
                        Rectangle {
                            id: boxRect
                            x: cropOverlay.bl; y: cropOverlay.bt
                            width: cropOverlay.bw; height: cropOverlay.bh
                            color: "transparent"; border.color: "#f0ffffff"; border.width: 1

                            // 기본 3분할 격자(회전 중에는 숨김 -> 촘촘한 격자만 표시)
                            Repeater { model: 2
                                Rectangle { visible: !cropOverlay.rotating; color: "#55ffffff"
                                            width: 1; height: boxRect.height
                                            x: boxRect.width * (index + 1) / 3 } }
                            Repeater { model: 2
                                Rectangle { visible: !cropOverlay.rotating; color: "#55ffffff"
                                            height: 1; width: boxRect.width
                                            y: boxRect.height * (index + 1) / 3 } }

                            // 회전 중에만: 촘촘한 정사각 격자(수평/수직 정렬 보조). 고정 px 셀 = 정사각.
                            Item {
                                anchors.fill: parent
                                visible: cropOverlay.rotating
                                property int cell: 26
                                Repeater { model: Math.max(0, Math.floor(boxRect.width / 26))
                                    Rectangle { color: "#33ffffff"; width: 1; height: boxRect.height; x: (index + 1) * 26 } }
                                Repeater { model: Math.max(0, Math.floor(boxRect.height / 26))
                                    Rectangle { color: "#33ffffff"; height: 1; width: boxRect.width; y: (index + 1) * 26 } }
                            }

                            MouseArea {
                                id: cropMoveArea
                                anchors.fill: parent
                                hoverEnabled: true       // 박스 내부 호버 소비 -> 회전 커서 안 뜸
                                cursorShape: Qt.SizeAllCursor
                                property real ox: 0
                                property real oy: 0
                                onPressed: (mouse) => { ox = mouse.x; oy = mouse.y }
                                onPositionChanged: (mouse) => {
                                    if (!pressed) return        // 호버만으로는 이동 안 함(클릭&드래그 전용)
                                    var dx = (mouse.x - ox) / cropOverlay.width
                                    var dy = (mouse.y - oy) / cropOverlay.height
                                    win.setCropRect(win.cropX + dx, win.cropY + dy, win.cropW, win.cropH)
                                }
                            }
                        }

                        // (4) 핸들: 자유=8(모서리+변), 종횡비 잠금=4(모서리만). 정확한 코너 = 리사이즈.
                        Repeater {
                            model: win.cropAspect > 0 ? 4 : 8
                            delegate: Rectangle {
                                property int hi: index
                                property bool hl: hi === 0 || hi === 2 || hi === 6   // left
                                property bool hr: hi === 1 || hi === 3 || hi === 7   // right
                                property bool ht: hi === 0 || hi === 1 || hi === 4   // top
                                property bool hb: hi === 2 || hi === 3 || hi === 5   // bottom
                                width: 13; height: 13; radius: 2
                                color: "#f0ffffff"; border.color: "#333"; border.width: 1
                                x: (hl ? cropOverlay.bl : hr ? cropOverlay.bl + cropOverlay.bw
                                                              : cropOverlay.bl + cropOverlay.bw / 2) - width / 2
                                y: (ht ? cropOverlay.bt : hb ? cropOverlay.bt + cropOverlay.bh
                                                              : cropOverlay.bt + cropOverlay.bh / 2) - height / 2

                                MouseArea {
                                    anchors.fill: parent
                                    anchors.margins: -6     // 잡기 쉽게 확장
                                    hoverEnabled: true      // 코너 호버 소비 -> 회전 커서 안 뜸(여기선 리사이즈)
                                    cursorShape: (parent.hl && parent.ht) || (parent.hr && parent.hb) ? Qt.SizeFDiagCursor
                                               : (parent.hr && parent.ht) || (parent.hl && parent.hb) ? Qt.SizeBDiagCursor
                                               : (parent.hl || parent.hr) ? Qt.SizeHorCursor : Qt.SizeVerCursor
                                    // Repeater delegate 라 외부에서 pressed 참조 불가 → 카운터로 집계
                                    onPressedChanged: cropOverlay.resizeDrags =
                                        Math.max(0, cropOverlay.resizeDrags + (pressed ? 1 : -1))
                                    onPositionChanged: (mouse) => {
                                        if (!pressed) return    // 호버만으로는 리사이즈 안 함(클릭&드래그 전용)
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var nx = Math.max(0, Math.min(1, p.x / cropOverlay.width))
                                        var ny = Math.max(0, Math.min(1, p.y / cropOverlay.height))
                                        if (win.cropAspect > 0) {
                                            // 잠금(모서리): 반대 코너 고정, 너비로 높이 결정.
                                            // ⚠️클램프는 여기서 '비율 보존형'으로 — setCropRect 의
                                            // 축별 클램프에 맡기면 한 축만 잘려 잠금 비율이 깨졌음
                                            // (예: 가로 캔버스에 세로 3:2 박스를 크게 끌 때).
                                            var ax = parent.hl ? (win.cropX + win.cropW) : win.cropX
                                            var ay = parent.ht ? (win.cropY + win.cropH) : win.cropY
                                            var kn = win.cropAspect / Math.max(0.0001, viewport.cA)
                                            var maxW = Math.min(1.0, kn,
                                                                parent.hl ? ax : 1.0 - ax,
                                                                (parent.ht ? ay : 1.0 - ay) * kn)
                                            var minW = Math.max(0.05, 0.05 * kn)
                                            var nw = Math.max(minW, Math.min(maxW, Math.abs(nx - ax)))
                                            var nh = nw / kn
                                            var newL = parent.hl ? (ax - nw) : ax
                                            var newT = parent.ht ? (ay - nh) : ay
                                            win.setCropRect(newL, newT, nw, nh)
                                        } else {
                                            var L = win.cropX, T = win.cropY
                                            var R = win.cropX + win.cropW, B = win.cropY + win.cropH
                                            if (parent.hl) L = nx
                                            if (parent.hr) R = nx
                                            if (parent.ht) T = ny
                                            if (parent.hb) B = ny
                                            win.setCropRect(Math.min(L, R), Math.min(T, B),
                                                            Math.abs(R - L), Math.abs(B - T))
                                        }
                                    }
                                }
                            }
                        }

                        // (5) 회전 커서: 곡선 화살표(BlankCursor 대체). 호는 짧고(~150°), 열린 구간이
                        //     코너별 바깥 대각선(박스 반대쪽)을 향함. 회전영역 호버/드래그 시 마우스 추적.
                        Canvas {
                            id: rotCursor
                            visible: cropOverlay.rotHover || cropOverlay.rotating
                            width: 30; height: 30; z: 100
                            x: cropOverlay.rotPx - width / 2
                            y: cropOverlay.rotPy - height / 2
                            property int corner: cropOverlay.rotCorner
                            onCornerChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var cc = width / 2, r = 8.5
                                // 바깥 대각선 방향(코너별로 다름) = 호의 중심, 열린 구간은 반대(박스쪽).
                                var dx = (corner === 1 || corner === 3) ? 1 : -1
                                var dy = (corner === 2 || corner === 3) ? 1 : -1
                                var base = Math.atan2(dy, dx)
                                var span = 2.4                     // ~138°
                                var a0 = base - span / 2, a1 = base + span / 2
                                ctx.lineCap = "round"
                                for (var pass = 0; pass < 2; pass++) {
                                    ctx.lineWidth = (pass === 0) ? 3.0 : 1.6
                                    ctx.strokeStyle = (pass === 0) ? "#202020" : "#ffffff"
                                    ctx.beginPath(); ctx.arc(cc, cc, r, a0, a1); ctx.stroke()
                                    // 호 양 끝 화살촉(접선 방향) -> 회전 의미
                                    var ends = [[a1, a1 + Math.PI / 2], [a0, a0 - Math.PI / 2]]
                                    for (var i = 0; i < 2; i++) {
                                        var ea = ends[i][0], ta = ends[i][1], s = 3.8, b = 0.40
                                        var ex = cc + r * Math.cos(ea), ey = cc + r * Math.sin(ea)
                                        ctx.beginPath()
                                        ctx.moveTo(ex, ey); ctx.lineTo(ex - s * Math.cos(ta - b), ey - s * Math.sin(ta - b))
                                        ctx.moveTo(ex, ey); ctx.lineTo(ex - s * Math.cos(ta + b), ey - s * Math.sin(ta + b))
                                        ctx.stroke()
                                    }
                                }
                            }
                        }
                    }


                    Text {
                        visible: srcImage.status !== Image.Ready
                        anchors.centerIn: parent
                        color: "#888"
                        font.pixelSize: 16
                        text: "Double-click a RAW file in the explorer on the left to open it"
                    }

                    // 원본 비교 버튼: 클릭(또는 \ 키)으로 원본↔편집본 토글(좌하단). 크롭 페이지에선 숨김.
                    // 하단 AI 캡션 패널(전체 폭)이 보이면 항상 그 위에 배치(일관 규칙).
                    Rectangle {
                        id: cmpBtn
                        visible: controller.imagePath !== "" && win.activePanel === 0
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.margins: 12
                        anchors.bottomMargin: 12 + (captionBar.visible ? captionBar.height : 0)
                        radius: 6
                        color: win.compareOn ? "#cc8ab4f8" : "#cc1e1e1e"
                        border.color: "#55ffffff"; border.width: 1
                        width: cmpRow.implicitWidth + 20
                        height: cmpRow.implicitHeight + 14
                        RowLayout {
                            id: cmpRow
                            anchors.centerIn: parent
                            spacing: 6
                            Label {
                                text: win.compareOn ? "Viewing original" : "Compare original"
                                color: win.compareOn ? "#10243f" : "#e6e6e6"
                                font.pixelSize: 11; font.bold: true
                            }
                            Label {
                                text: "(\\)"
                                color: win.compareOn ? "#10243f" : "#9a9a9a"
                                font.pixelSize: 10
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: win.compareOn = !win.compareOn
                        }
                    }

                    // 해시태그(AI 캡션의 주요 단어): 캡션 바 바로 위 우하단에 우측정렬로 나열.
                    // Compare original(좌하단)과 같은 높이. 캡션 없으면 숨김, C 토글로 함께 켜고 꺼짐.
                    Rectangle {
                        id: hashtagBar
                        visible: win.captionOverlay && cropClip.visible
                                 && controller.hashtags !== ""
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 12
                        anchors.bottomMargin: 12 + (captionBar.visible ? captionBar.height : 0)
                        radius: 6
                        color: "#cc1e1e1e"
                        border.color: "#55ffffff"; border.width: 1
                        width: hashtagLabel.implicitWidth + 20
                        height: hashtagLabel.implicitHeight + 14
                        Label {
                            id: hashtagLabel
                            anchors.centerIn: parent
                            text: controller.hashtags
                            color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                            horizontalAlignment: Text.AlignRight
                        }
                    }

                    // 원본 표시 배지: 원본 보는 중 상단중앙에 표시.
                    Rectangle {
                        visible: win.compareOn
                        anchors.top: parent.top
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.topMargin: 12
                        radius: 6
                        color: "#cc1e1e1e"
                        border.color: "#8ab4f8"; border.width: 1
                        width: cmpBadge.implicitWidth + 20
                        height: cmpBadge.implicitHeight + 12
                        Label {
                            id: cmpBadge
                            anchors.centerIn: parent
                            text: "Original · BEFORE"
                            color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                            font.capitalization: Font.AllUppercase
                        }
                    }

                    // AI 캡션 패널(하단 전체 폭, 외곽선 없는 반투명 바, C 키 토글):
                    // [AI CAPTION | 상세도 콤보 | 캡션]. Compare original 은 항상 이 패널 위에
                    // 배치(일관 규칙). 사진 로드 시 자동 생성(저장본 있으면 즉시 표시), 콤보
                    // 변경 시 해당 상세도 자동 생성/표시. 생성 중엔 상태 문구(모델 다운로드 %/
                    // Generating…) 표시.
                    Rectangle {
                        id: captionBar
                        visible: win.captionOverlay && cropClip.visible
                                 && controller.imagePath !== ""
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        color: "#cc1e1e1e"
                        height: capRow.implicitHeight + 16
                        // 상단 구분선만(외곽선 대신) — 이미지와 패널 경계 표시
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            height: 1
                            color: "#55ffffff"
                        }
                        RowLayout {
                            id: capRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 10
                            // 타이틀 — 무슨 UI 인지 인지용(촬영정보 오버레이와 동일 톤)
                            Label {
                                text: "AI Caption  (C)"
                                color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            ComboBox {
                                id: captionLevelCombo
                                Layout.preferredWidth: 120
                                model: ["Short", "Detailed", "Paragraph"]
                                currentIndex: controller.captionLevel   // 기본 Short(0)
                                onActivated: controller.setCaptionLevel(currentIndex)
                                // 드롭다운이 닫히면 포커스를 이미지 뷰로 넘긴다 — 콤보가 활성 포커스를
                                // 쥔 채 남으면 win._typing 이 true 로 유지돼 단축키(C/I/D/…)가 콤보
                                // 타입어헤드로 새며 먹통이 됨. 선택·취소 모두 커버(popup.onClosed).
                                Connections {
                                    target: captionLevelCombo.popup
                                    function onClosed() { viewport.forceActiveFocus() }
                                }
                            }
                            BusyIndicator {
                                visible: controller.captionBusy
                                running: visible
                                Layout.preferredWidth: 20
                                Layout.preferredHeight: 20
                            }
                            Label {
                                id: captionText
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                maximumLineCount: 3
                                elide: Text.ElideRight
                                // 모델 미다운로드 PC: 자동 다운로드 대신 안내 + 클릭 옵트인
                                // (팝업 없음 — 원치 않는 유저는 그냥 두면 다시 묻지 않음)
                                readonly property bool offerDownload:
                                    !controller.captionBusy && controller.caption === ""
                                    && !controller.captionModelReady
                                text: controller.captionBusy
                                      ? (controller.captionStatus || "Generating…")
                                      : (offerDownload
                                         ? "AI captions are off — click to download the model (~1.1 GB, one-time)"
                                         : (controller.caption || controller.captionStatus))
                                color: controller.captionStatus.indexOf("Failed") === 0
                                       ? "#ff6b6b"
                                       : (offerDownload ? "#8ab4f8"
                                          : (controller.captionBusy ? "#9a9a9a" : "#e6e6e6"))
                                font.pixelSize: 12
                                font.italic: controller.captionBusy
                                font.underline: offerDownload && capDlHover.hovered
                                HoverHandler {
                                    id: capDlHover
                                    enabled: captionText.offerDownload
                                    cursorShape: Qt.PointingHandCursor
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: captionText.offerDownload
                                    cursorShape: Qt.PointingHandCursor
                                    // 명시 클릭 = 다운로드 승인 → 이후 이 PC 에선 항상 자동
                                    onClicked: controller.generateCaption(captionLevelCombo.currentIndex)
                                }
                            }
                        }
                    }

                    // 촬영정보 플로팅 패널 (I 키 토글) — 좌측 뷰 왼쪽 끝에 고정
                    Rectangle {
                        visible: win.infoOverlay && cropClip.visible
                                 && controller.shootingInfo.length > 0
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.margins: 12
                        radius: 6
                        color: "#cc1e1e1e"
                        border.color: "#55ffffff"; border.width: 1
                        width: ovCol.implicitWidth + 24
                        height: ovCol.implicitHeight + 20
                        ColumnLayout {
                            id: ovCol
                            anchors.centerIn: parent
                            spacing: 3
                            Label {
                                text: "Shooting Info  (I)"
                                color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                                font.capitalization: Font.AllUppercase
                                Layout.bottomMargin: 3
                            }
                            Repeater {
                                model: controller.shootingInfo
                                delegate: RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 16
                                    Label {
                                        text: modelData.label
                                        color: "#9a9a9a"; font.pixelSize: 11
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: modelData.value
                                        color: "#e6e6e6"; font.pixelSize: 11
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // 진행 중 오버레이 (이미지 위): export / 배치 / 디코딩(렌즈 보정) / 하늘 세그멘테이션
            Rectangle {
                anchors.fill: parent
                visible: controller.exporting || win.batchActive || controller.busy
                         || controller.skyBusy || controller.aiNrDownloading
                         || controller.aiNrInitializing
                color: "#aa000000"
                MouseArea { anchors.fill: parent }   // 진행 중 이미지 입력 차단

                // ── Export: 필름 프레임 카운터 (실제 진행률 controller.exportProgress 반영) ──
                // 위/아래 앰버 퍼포레이션이 끊김없이 와인딩(필름 감기는 느낌). 가운데 'DEVELOPING'
                // 라벨 + 큰 % 카운터 + 진행 바. 진행률 모르는 구간(디코드·GPU)은 인디터미닛 스윕.
                // 배치 중엔 파일 전환(디코드/마스크) 구간에도 유지되고 FRAME i/N 카운트업.
                Rectangle {
                    id: filmCell
                    visible: controller.exporting || win.batchActive
                    anchors.centerIn: parent
                    width: 320; height: win.batchActive ? 176 : 156
                    radius: 10
                    color: "#1b1b1d"
                    border.color: "#E0A226"; border.width: 1

                    // 끊김없는 스프로킷 행: 한 피치(구멍폭+간격)만큼 무한 이동 → 패턴이 주기적이라 이음매 X.
                    component Perforation: Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 11
                        clip: true
                        Row {
                            id: holesRow
                            spacing: 9
                            readonly property real pitch: 14 + spacing   // 구멍폭 + 간격
                            Repeater {
                                model: Math.ceil(filmCell.width / holesRow.pitch) + 2
                                Rectangle { width: 14; height: 9; radius: 2; color: "#E0A226" }
                            }
                            NumberAnimation on x {
                                running: controller.exporting || win.batchActive
                                from: 0; to: -holesRow.pitch
                                duration: 650; loops: Animation.Infinite
                            }
                        }
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10
                        Perforation {}
                        ColumnLayout {
                            id: devInfo
                            Layout.alignment: Qt.AlignHCenter
                            spacing: 6
                            // 진행률이 알려진 상태(export 중 & >0)면 결정형(%·채움), 아니면 인디터미닛.
                            // (배치의 디코드/마스크 구간은 exporting=false — 이전 파일 % 잔상 방지)
                            readonly property bool determinate: controller.exporting
                                                                && controller.exportProgress > 0.0
                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                visible: win.batchActive
                                text: "FRAME " + Math.min(win.batchIndex + 1, win.batchQueue.length)
                                      + " / " + win.batchQueue.length
                                color: "#E0A226"; font.pixelSize: 12; font.letterSpacing: 2
                                font.weight: Font.Bold
                            }
                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                text: "DEVELOPING"
                                color: "#9a9a9a"; font.pixelSize: 11; font.letterSpacing: 4
                                font.weight: Font.Bold
                            }
                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                visible: devInfo.determinate
                                text: Math.round(controller.exportProgress * 100) + "%"
                                color: "#f2f2f2"; font.pixelSize: 34; font.weight: Font.Bold
                                font.letterSpacing: 1
                            }
                            // 진행 바: 결정형=앰버 채움(부드럽게), 인디터미닛=앰버 세그먼트 좌→우 반복.
                            Rectangle {
                                id: progTrack
                                Layout.alignment: Qt.AlignHCenter
                                width: 200; height: 4; radius: 2; color: "#3a3a3d"; clip: true
                                Rectangle {   // 결정형 채움
                                    visible: devInfo.determinate
                                    width: progTrack.width * Math.max(0, Math.min(1, controller.exportProgress))
                                    height: parent.height; radius: 2; color: "#E0A226"
                                    Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                                }
                                Rectangle {   // 인디터미닛 스윕
                                    id: sweepSeg
                                    visible: !devInfo.determinate
                                    width: 64; height: parent.height; radius: 2; color: "#E0A226"
                                    NumberAnimation on x {
                                        running: (controller.exporting || win.batchActive) && !devInfo.determinate
                                        from: -sweepSeg.width; to: progTrack.width
                                        duration: 1000; loops: Animation.Infinite
                                    }
                                }
                            }
                        }
                        Perforation {}
                    }
                }

                // 배치 취소 — 현재 파일까지 마치고 중단(진행 중 render_full 은 중단 불가)
                Button {
                    visible: win.batchActive
                    anchors.top: filmCell.bottom
                    anchors.topMargin: 12
                    anchors.horizontalCenter: filmCell.horizontalCenter
                    text: win.batchCancel ? "Cancelling…" : "Cancel batch"
                    enabled: !win.batchCancel
                    onClicked: win.batchCancel = true
                }

                // ── AI 모델 다운로드: 실제 진행률 프로그레스바(하늘 모델 오버레이와 동일 UX) ──
                ColumnLayout {
                    visible: controller.aiNrDownloading && !controller.exporting && !win.batchActive
                    anchors.centerIn: parent
                    spacing: 12
                    Label {
                        text: "Downloading AI denoise model…  "
                              + Math.round(controller.aiNrDlProgress * 100) + "%"
                        color: "white"; font.pixelSize: 14
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Rectangle {   // 진행 바(앰버) — 필름 카운터와 같은 컨셉 컬러
                        Layout.alignment: Qt.AlignHCenter
                        width: 280; height: 8; radius: 4
                        color: "#333333"
                        Rectangle {
                            width: parent.width * Math.min(1.0, controller.aiNrDlProgress)
                            height: parent.height; radius: 4; color: "#E0A226"
                        }
                    }
                    Label {
                        text: "first use only · ~117 MB"
                        color: "#9a9a9a"; font.pixelSize: 11
                        Layout.alignment: Qt.AlignHCenter
                    }
                }

                // ── AI 세션 초기화(GPU 점유로 화면이 잠깐 멈춤): 정적 안내 ──
                //   GPU stall 중엔 새 프레임이 present 안 되어 스피너가 정지해 보이므로,
                //   애니메이션 대신 명확한 정적 메시지로 '준비 중'임을 알린다(마지막 프레임 유지).
                ColumnLayout {
                    visible: controller.aiNrInitializing && !controller.aiNrDownloading
                             && !controller.exporting && !win.batchActive
                    anchors.centerIn: parent
                    spacing: 8
                    Label {
                        text: "Preparing AI denoise…"
                        color: "white"; font.pixelSize: 14
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Label {
                        text: "first use — may pause briefly"
                        color: "#9a9a9a"; font.pixelSize: 11
                        Layout.alignment: Qt.AlignHCenter
                    }
                }

                // ── 마스킹 모델 다운로드: 실제 진행률 프로그레스바(AI 디노이즈와 동일 UX) ──
                ColumnLayout {
                    visible: controller.segDownloading && !controller.aiNrDownloading
                             && !controller.exporting && !win.batchActive
                    anchors.centerIn: parent
                    spacing: 12
                    Label {
                        text: "Downloading masking model…  "
                              + Math.round(controller.segDlProgress * 100) + "%"
                        color: "white"; font.pixelSize: 14
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Rectangle {   // 진행 바(앰버) — AI 디노이즈/필름 카운터와 같은 컨셉 컬러
                        Layout.alignment: Qt.AlignHCenter
                        width: 280; height: 8; radius: 4
                        color: "#333333"
                        Rectangle {
                            width: parent.width * Math.min(1.0, controller.segDlProgress)
                            height: parent.height; radius: 4; color: "#E0A226"
                        }
                    }
                    Label {
                        text: "first use only · ~105 MB"
                        color: "#9a9a9a"; font.pixelSize: 11
                        Layout.alignment: Qt.AlignHCenter
                    }
                }

                // ── 그 외(디코드·세그): 기존 스피너 ──
                ColumnLayout {
                    visible: !controller.exporting && !win.batchActive && !controller.aiNrDownloading
                             && !controller.segDownloading
                    anchors.centerIn: parent
                    spacing: 12
                    BusyIndicator {
                        running: (controller.busy || controller.skyBusy) && !controller.exporting
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: 64; implicitHeight: 64
                    }
                    Label {
                        text: controller.segStatus !== "" ? controller.segStatus
                              : (controller.skyBusy ? "Detecting mask…" : "Processing…")
                        color: "white"; font.pixelSize: 14
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }

        // ---------- 우측 패널 (헤더 고정 + 패널 전환 스택) ----------
        Rectangle {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            color: "#2b2b2b"
            enabled: !win.batchActive   // 배치 중 슬라이더 변경 → 배치 파일 사이드카 오염 방지

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // ── 고정 헤더: 패널과 무관한 전역 동작(Export/해상도/상태). 항상 보임 ──
                ColumnLayout {
                    id: panelHeader
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Layout.topMargin: 16
                    spacing: 12

                // 편집 도구 줄(맨 위): Undo/Redo(좌) — 스페이서 — Reset/복사붙여넣기(우)
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Button {
                        text: "↶"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                        Layout.alignment: Qt.AlignVCenter; padding: 0; font.pixelSize: 14
                        enabled: win.canUndo
                        ToolTip.visible: hovered; ToolTip.text: "Undo (Ctrl+Z)"
                        onClicked: win.undo()
                    }
                    Button {
                        text: "↷"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                        Layout.alignment: Qt.AlignVCenter; padding: 0; font.pixelSize: 14
                        enabled: win.canRedo
                        ToolTip.visible: hovered; ToolTip.text: "Redo (Ctrl+Shift+Z)"
                        onClicked: win.redo()
                    }
                    Item { Layout.fillWidth: true }      // 좌(이력) ↔ 우(초기화/기타) 분리 스페이서
                    Button {
                        id: resetBtn
                        text: "↺"                       // Reset 아이콘(조절 초기화)
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 26       // 작은 정사각
                        Layout.alignment: Qt.AlignVCenter
                        padding: 0
                        font.pixelSize: 14
                        ToolTip.visible: hovered
                        ToolTip.text: "Reset (clear adjustments — including geometry)"
                        onClicked: win.resetAndClearEdits()   // 모든 편집 초기화 + 사이드카 삭제(파일명 앰버 해제)
                    }
                    // 편집 복사/붙여넣기 메뉴(이미지 간) — Reset 우측 "⋯" 드롭다운.
                    Button {
                        id: editClipBtn
                        text: "⋯"
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 26
                        Layout.alignment: Qt.AlignVCenter
                        padding: 0
                        font.pixelSize: 14
                        enabled: controller.imagePath !== ""
                        ToolTip.visible: hovered
                        ToolTip.text: "Copy / paste edits (between images)"
                        onClicked: editClipMenu.popup(0, height)
                        Menu {
                            id: editClipMenu
                            MenuItem { text: "Copy all"; onTriggered: win.copyEdits(false) }
                            MenuItem { text: "Copy (excluding WB · Tint)"; onTriggered: win.copyEdits(true) }
                            MenuItem {
                                text: "Paste"
                                enabled: win._editClipboard !== null
                                onTriggered: win.pasteEdits()
                            }
                        }
                    }
                }

                // 출력: 주 버튼 + 옵션(⚙) 팝업
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Button {
                        id: exportMainBtn
                        text: "Export…"
                        Layout.fillWidth: true
                        enabled: controller.imagePath !== "" && !controller.exporting
                        onClicked: {
                            // 기본 파일명 = '<원본이름>_exported.png' (원본과 같은 폴더)
                            var u = controller.suggestedExportUrl()
                            if (u != "") saveDialog.selectedFile = u
                            saveDialog.open()
                        }
                    }
                    Button {
                        id: exportOptBtn
                        text: "▾"                       // Export 옵션 토글(펼치기)
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: exportMainBtn.height   // Export 버튼과 높이 동일하게 고정
                        padding: 0; font.pixelSize: 14
                        ToolTip.visible: hovered
                        ToolTip.text: "Export options (resolution · render · 16-bit)"
                        onClicked: exportOptPopup.opened ? exportOptPopup.close() : exportOptPopup.open()
                        Popup {
                            id: exportOptPopup
                            y: exportOptBtn.height + 4
                            x: exportOptBtn.width - width    // 버튼 오른쪽에 맞춰 좌측으로 펼침(패널 안)
                            width: 230
                            padding: 10
                            modal: false
                            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                            background: Rectangle { color: "#2b2b2b"; border.color: "#555"; border.width: 1; radius: 6 }
                            contentItem: ColumnLayout {
                                spacing: 10
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: "Resolution"; color: "white"; font.pixelSize: 12; Layout.preferredWidth: 72 }
                                    ComboBox {
                                        id: resCombo
                                        Layout.fillWidth: true
                                        currentIndex: 0     // 원본
                                        model: ["Original (Full)", "4096", "3840 (4K)",
                                                "2560", "2048", "1920 (FHD)", "1280"]
                                        // 드롭다운 닫히면 포커스 해제(단축키 복구 — captionLevelCombo 와 동일)
                                        Connections {
                                            target: resCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: "Render"; color: "white"; font.pixelSize: 12; Layout.preferredWidth: 72 }
                                    ComboBox {
                                        id: renderModeCombo
                                        Layout.fillWidth: true
                                        // 16bit 는 CPU 전용(GPU grab 은 8bit) → 16bit 체크 시 GPU 비활성/CPU 고정
                                        enabled: !bitDepth16Check.checked
                                        currentIndex: 0     // 기본 CPU
                                        model: ["CPU", "GPU"]
                                        // 드롭다운 닫히면 포커스 해제(단축키 복구 — captionLevelCombo 와 동일)
                                        Connections {
                                            target: renderModeCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    CheckBox {
                                        id: bitDepth16Check
                                        ToolTip.visible: hovered
                                        ToolTip.text: "Save 16-bit/channel (preserves gradation · headroom). TIFF recommended. CPU render only."
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "16-bit (TIFF/PNG · CPU)"
                                        color: "white"; font.pixelSize: 12
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                }
                            }
                        }
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: controller.exportStatus !== ""
                    color: "#9fd39f"
                    font.pixelSize: 11
                    wrapMode: Text.WrapAnywhere
                    text: controller.exportStatus
                }

                Label {
                    Layout.fillWidth: true
                    visible: controller.loadError !== ""
                    color: "#e08a8a"
                    font.pixelSize: 11
                    wrapMode: Text.WrapAnywhere
                    text: controller.loadError
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }
                }   // end panelHeader

                // ── 패널 전환 스택 (Edit / Rotation / Crop) ──
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: win.activePanel

                    // ===== index 0: Edit (기존 편집 컨트롤 전부, 스크롤) =====
                    Flickable {
                        id: panelScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: panelCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        // 다크 테마 스크롤바 (Flickable + 명시적 Basic ScrollBar -> 확실히 표시)
                        ScrollBar.vertical: B.ScrollBar {
                            id: vbar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle {
                                implicitWidth: 8
                                radius: 4
                                color: vbar.pressed ? "#cfcfcf" : "#9a9a9a"   // 밝게(항상 보임)
                            }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: panelCol
                            x: 16; y: 16
                            width: panelScroll.width - 32
                            spacing: 12

                // ── 접이식 섹션: 헤더 클릭으로 내용 토글 ──
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[0] ? "▾  " : "▸  ") + "Film Simulation"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(0) }
                }
                ColumnLayout {
                    visible: win.secOpen[0]
                    Layout.fillWidth: true
                    spacing: 12
                ComboBox {
                    id: simCombo
                    Layout.fillWidth: true
                    currentIndex: 0
                    onActivated: win.refreshHistogram()
                    // 드롭다운 닫히면 포커스 해제(단축키 복구 — captionLevelCombo 와 동일)
                    Connections {
                        target: simCombo.popup
                        function onClosed() { viewport.forceActiveFocus() }
                    }
                    // 라벨은 win.simLabels(= controller.filmSims 파생). 인덱스→simKeys[i]→image://lut/<key>
                    model: win.simLabels
                    // 그룹 구분선: 행(인덱스)을 추가하지 않고 그룹 시작 항목 위에 선만 그림
                    // → simKeys 매핑·저장된 simIndex(사이드카) 그대로 호환.
                    // 그룹 구분선: controller.filmSims 의 group 이 바뀌는 인덱스(존재하는 시뮬 기준 자동).
                    readonly property var simGroupStarts: {
                        var arr = []; var sims = controller.filmSims
                        for (var i = 1; i < sims.length; i++)
                            if (sims[i].group !== sims[i - 1].group) arr.push(i)
                        return arr
                    }
                    delegate: ItemDelegate {
                        id: simDel
                        width: ListView.view ? ListView.view.width : simCombo.width
                        required property int index
                        required property var modelData
                        text: modelData
                        highlighted: simCombo.highlightedIndex === index
                        property bool groupStart: simCombo.simGroupStarts.indexOf(index) !== -1
                        contentItem: Text {
                            text: simDel.text
                            color: "#e8e8e8"; font.pixelSize: 13
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: simDel.highlighted ? "#3a3f4b" : "#2b2b2b"
                            Rectangle {                       // 그룹 구분선(항목 상단)
                                visible: simDel.groupStart
                                anchors { top: parent.top; left: parent.left; right: parent.right }
                                height: 1; color: "#555"
                            }
                        }
                    }
                    // 팝업도 다크로 직접 스타일(네이티브 팝업은 커스텀 delegate 와 안 맞음)
                    popup: Popup {
                        y: simCombo.height
                        width: simCombo.width
                        implicitHeight: Math.min(contentItem.implicitHeight + 2, 380)
                        padding: 1
                        background: Rectangle { color: "#2b2b2b"; border.color: "#555"; radius: 3 }
                        contentItem: ListView {
                            clip: true
                            implicitHeight: contentHeight
                            model: simCombo.delegateModel
                            currentIndex: simCombo.highlightedIndex
                            ScrollIndicator.vertical: ScrollIndicator {}
                        }
                    }
                }

                Label {
                    text: "Strength:  " + Math.round(simStrengthSlider.value * 100) + "%"
                    color: "white"
                    enabled: simCombo.currentIndex !== 0
                }
                Slider {
                    id: simStrengthSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: 0.0; to: 1.0; value: 1.0
                    enabled: simCombo.currentIndex !== 0   // None 이면 비활성
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(simStrengthSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[1] ? "▾  " : "▸  ") + "Light"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(1) }
                }
                ColumnLayout {
                    visible: win.secOpen[1]
                    Layout.fillWidth: true
                    spacing: 12

                Label {
                    text: "Exposure:  " + expSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: expSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -3.0; to: 3.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(expSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Contrast:  " + conSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: conSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: 0.5; to: 2.0; value: 1.0
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(conSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Highlights:  " + hiSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: hiSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hiSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Shadows:  " + shSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: shSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(shSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Whites:  " + whSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: whSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(whSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Blacks:  " + blSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: blSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(blSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[2] ? "▾  " : "▸  ") + "Tone Curve"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(2) }
                }
                ColumnLayout {
                    visible: win.secOpen[2]
                    Layout.fillWidth: true
                    spacing: 12
                // 채널 선택: RGB(마스터) / R / G / B
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Repeater {
                        model: [{t: "RGB", c: "#e8e8e8"}, {t: "R", c: "#ff6b6b"},
                                {t: "G", c: "#5fd16a"}, {t: "B", c: "#5b9cff"}]
                        delegate: Rectangle {
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 26
                            radius: 4
                            color: curveEditor.channel === index ? "#3a3a3a" : "#2a2a2a"
                            border.color: curveEditor.channel === index ? modelData.c : "#444"
                            Text {
                                anchors.centerIn: parent
                                text: modelData.t; color: modelData.c
                                font.pixelSize: 12; font.bold: curveEditor.channel === index
                            }
                            TapHandler { onTapped: curveEditor.channel = index }
                        }
                    }
                }
                CurveEditor {
                    id: curveEditor
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240     // 고정 높이(너비에서 분리: 레이아웃 루프 방지)
                    histogram: controller.histogram
                    onEdited: { controller.setCurve(allLuts()); win.refreshHistogram() }
                }

                // 클리핑 경고 오버레이 토글(프리뷰 전용): 하이라이트=빨강 / 섀도=파랑.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: clipWarnCheck
                        onToggled: win.clipWarn = checked
                    }
                    // J 단축키가 win.clipWarn 을 바꿔도 첫 클릭 후엔 인라인 바인딩이
                    // 파괴돼 박스가 추종 못 함 → 독립 Binding 으로 재푸시.
                    Binding { target: clipWarnCheck; property: "checked"; value: win.clipWarn }
                    Label {
                        Layout.fillWidth: true
                        text: "Clipping warning  (J) — highlights red / shadows blue"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                // 디스플레이 색관리(프리뷰 전용): 광색역 모니터에서 sRGB 를 정확히 표시.
                // 모니터 ICC 프로파일이 광색역일 때만 노출(sRGB 모니터에선 무의미).
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: controller.hasDisplayCM
                    CheckBox {
                        id: displayCmCheck
                        onToggled: win.displayCM = checked
                    }
                    // Ctrl+Shift+M 단축키가 win.displayCM 을 바꿔도 첫 클릭 후엔 인라인
                    // 바인딩이 파괴돼 박스가 추종 못 함 → 독립 Binding 으로 재푸시.
                    Binding { target: displayCmCheck; property: "checked"; value: win.displayCM }
                    Label {
                        Layout.fillWidth: true
                        text: "Display color management  (Ctrl+Shift+M) — match monitor gamut"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[3] ? "▾  " : "▸  ") + "White Balance"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(3) }
                }
                ColumnLayout {
                    visible: win.secOpen[3]
                    Layout.fillWidth: true
                    spacing: 12

                Label {
                    text: "Temp:  " + Math.round(tempSlider.value) + " K"
                            + "   (as-shot " + controller.asShotKelvin + "K)"
                    color: "white"
                }
                Slider {
                    id: tempSlider
                    Layout.fillWidth: true
                    from: 2000; to: 12000; value: 6500
                    stepSize: 50
                    // 더블클릭 -> as-shot 색온도로 리셋
                    property real defaultValue: controller.asShotKelvin
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    // press: 더블 여부 판정. release: (더블이면 리셋 후) 재디코딩 커밋.
                    onPressedChanged: {
                        if (pressed) {
                            _pendingReset = win.isDblPress(tempSlider)
                        } else {
                            if (_pendingReset) { value = defaultValue; _pendingReset = false }
                            controller.setWb(tempSlider.value, tintSlider.value)
                        }
                    }
                    onValueChanged: if (!pressed && !win._applying) wbTimer.restart()
                }

                Label {
                    text: "Tint:  " + tintSlider.value.toFixed(2) + "  (− green / + magenta)"
                    color: "white"
                }
                Slider {
                    id: tintSlider
                    Layout.fillWidth: true
                    from: -1.5; to: 1.5; value: 0.0    // as-shot 추정 tint(최대 ±1.5) 수용
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) {
                            _pendingReset = win.isDblPress(tintSlider)
                        } else {
                            if (_pendingReset) { value = defaultValue; _pendingReset = false }
                            controller.setWb(tempSlider.value, tintSlider.value)
                        }
                    }
                    onValueChanged: if (!pressed && !win._applying) wbTimer.restart()
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[4] ? "▾  " : "▸  ") + "Color"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(4) }
                }
                ColumnLayout {
                    visible: win.secOpen[4]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Vibrance:  " + vibSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: vibSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(vibSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Saturation:  " + satSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: satSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(satSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[5] ? "▾  " : "▸  ") + "Color Mixer"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(5) }
                }
                ColumnLayout {
                    visible: win.secOpen[5]
                    Layout.fillWidth: true
                    spacing: 12
                // 8색상대 스와치(클릭=선택). 선택 대역은 흰 테두리.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Repeater {
                        model: 8
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 22
                            radius: 3
                            color: Qt.hsva(index / 8.0, 0.85, 0.95, 1.0)
                            border.width: win.hslBand === index ? 2 : 0
                            border.color: "#ffffff"
                            MouseArea { anchors.fill: parent; onClicked: win.hslBand = index }
                        }
                    }
                }
                Label { text: "Hue:  " + Math.round(win.hslH[win.hslBand] * 100); color: "white" }
                Slider {
                    id: hslHueSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0
                    Component.onCompleted: value = win.hslH[win.hslBand]
                    Connections { target: win; function onHslBandChanged() { hslHueSlider.value = win.hslH[win.hslBand] } }
                    onMoved: win.setHslBandValue("hslH", value)
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hslHueSlider)
                        else if (_pendingReset) { value = 0.0; win.setHslBandValue("hslH", 0.0); _pendingReset = false }
                    }
                }
                Label { text: "Saturation:  " + Math.round(win.hslS[win.hslBand] * 100); color: "white" }
                Slider {
                    id: hslSatSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0
                    Component.onCompleted: value = win.hslS[win.hslBand]
                    Connections { target: win; function onHslBandChanged() { hslSatSlider.value = win.hslS[win.hslBand] } }
                    onMoved: win.setHslBandValue("hslS", value)
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hslSatSlider)
                        else if (_pendingReset) { value = 0.0; win.setHslBandValue("hslS", 0.0); _pendingReset = false }
                    }
                }
                Label { text: "Luminance:  " + Math.round(win.hslL[win.hslBand] * 100); color: "white" }
                Slider {
                    id: hslLumSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0
                    Component.onCompleted: value = win.hslL[win.hslBand]
                    Connections { target: win; function onHslBandChanged() { hslLumSlider.value = win.hslL[win.hslBand] } }
                    onMoved: win.setHslBandValue("hslL", value)
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hslLumSlider)
                        else if (_pendingReset) { value = 0.0; win.setHslBandValue("hslL", 0.0); _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                // ===== Color Grading (스플릿 토닝) — 섹션 인덱스 11 =====
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[11] ? "▾  " : "▸  ") + "Color Grading"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(11) }
                }
                ColumnLayout {
                    visible: win.secOpen[11]
                    Layout.fillWidth: true
                    spacing: 6

                    // 섀도 — Hue(0..360°) + Sat(0..100). Sat=0 이면 무효과. 스와치=적용 색 미리보기.
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        Label { text: "Shadows"; color: "white"; font.pixelSize: 12; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: "H " + Math.round(cgShHueSlider.value) + "°  S " + Math.round(cgShSatSlider.value*100); color: "#aaa"; font.pixelSize: 11 }
                        Rectangle { width: 26; height: 14; radius: 3; border.color: "#666"; border.width: 1
                                    color: Qt.hsva(cgShHueSlider.value/360, cgShSatSlider.value, 1, 1) }
                    }
                    HueBar { Layout.fillWidth: true; Layout.preferredHeight: 8 }
                    Slider {
                        id: cgShHueSlider; Layout.fillWidth: true; from: 0; to: 360; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgShHueSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    Slider {
                        id: cgShSatSlider; Layout.fillWidth: true; from: 0; to: 1; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgShSatSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    // 미드톤
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        Label { text: "Midtones"; color: "white"; font.pixelSize: 12; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: "H " + Math.round(cgMidHueSlider.value) + "°  S " + Math.round(cgMidSatSlider.value*100); color: "#aaa"; font.pixelSize: 11 }
                        Rectangle { width: 26; height: 14; radius: 3; border.color: "#666"; border.width: 1
                                    color: Qt.hsva(cgMidHueSlider.value/360, cgMidSatSlider.value, 1, 1) }
                    }
                    HueBar { Layout.fillWidth: true; Layout.preferredHeight: 8 }
                    Slider {
                        id: cgMidHueSlider; Layout.fillWidth: true; from: 0; to: 360; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgMidHueSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    Slider {
                        id: cgMidSatSlider; Layout.fillWidth: true; from: 0; to: 1; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgMidSatSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    // 하이라이트
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        Label { text: "Highlights"; color: "white"; font.pixelSize: 12; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: "H " + Math.round(cgHiHueSlider.value) + "°  S " + Math.round(cgHiSatSlider.value*100); color: "#aaa"; font.pixelSize: 11 }
                        Rectangle { width: 26; height: 14; radius: 3; border.color: "#666"; border.width: 1
                                    color: Qt.hsva(cgHiHueSlider.value/360, cgHiSatSlider.value, 1, 1) }
                    }
                    HueBar { Layout.fillWidth: true; Layout.preferredHeight: 8 }
                    Slider {
                        id: cgHiHueSlider; Layout.fillWidth: true; from: 0; to: 360; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgHiHueSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    Slider {
                        id: cgHiSatSlider; Layout.fillWidth: true; from: 0; to: 1; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgHiSatSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    // 밸런스: 섀도↔하이라이트 마스크 분포 이동(+ = 하이라이트 쪽).
                    Label { text: "Balance   " + cgBalanceSlider.value.toFixed(2); color: "white"; font.pixelSize: 12 }
                    Slider {
                        id: cgBalanceSlider; Layout.fillWidth: true; from: -1.0; to: 1.0; value: 0.0; property real _lastPressMs: 0
                        property real defaultValue: 0.0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgBalanceSlider); else if (_pendingReset) { value = defaultValue; _pendingReset = false } }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[6] ? "▾  " : "▸  ") + "Detail & Vignette"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(6) }
                }
                ColumnLayout {
                    visible: win.secOpen[6]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Texture:  " + texSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: texSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(texSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Clarity:  " + claritySlider.value.toFixed(2); color: "white" }
                Slider {
                    id: claritySlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(claritySlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Dehaze:  " + dehazeSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: dehazeSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(dehazeSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label {
                    text: "Vignette:  " + vignetteSlider.value.toFixed(2) + "  (− darker)"
                    color: "white"
                }
                Slider {
                    id: vignetteSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(vignetteSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[7] ? "▾  " : "▸  ") + "Grain"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(7) }
                }
                ColumnLayout {
                    visible: win.secOpen[7]
                    Layout.fillWidth: true
                    spacing: 12
                Label {
                    text: "Grain:  " + grainSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: grainSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    // 스탬프 그레인은 사진 필름 그레인에 연동. 스탬프 스프라이트 재렌더는 CPU(numpy
                    // gaussian/zoom)라 드래그 delta 마다 동기 실행하면 잰크 → 디바운스(멈추면 1회).
                    // 장면 그레인 프리뷰(GPU, grainAmt 바인딩)는 영향 없이 라이브 유지.
                    Timer {
                        id: stampGrainTimer; interval: 150
                        onTriggered: controller.setStampGrainSrc(grainSlider.value)
                    }
                    onMoved: stampGrainTimer.restart()
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(grainSlider)
                        else if (_pendingReset) { value = defaultValue; controller.setStampGrainSrc(defaultValue); _pendingReset = false }
                    }
                }

                Label {
                    text: "Grain Size:  " + grainSizeSlider.value.toFixed(2) + "  (fine ↔ coarse)"
                    color: "white"
                }
                Slider {
                    id: grainSizeSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.5
                    property real defaultValue: 0.5
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(grainSizeSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[8] ? "▾  " : "▸  ") + "Sharpening"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(8) }
                }
                ColumnLayout {
                    visible: win.secOpen[8]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Amount:  " + Math.round(sharpAmtSlider.value * 100); color: "white" }
                Slider {
                    id: sharpAmtSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpAmtSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Radius:  " + sharpRadiusSlider.value.toFixed(1) + " px"; color: "white" }
                Slider {
                    id: sharpRadiusSlider
                    Layout.fillWidth: true
                    from: 0.5; to: 3.0; value: 1.0
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpRadiusSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Detail:  " + Math.round(sharpDetailSlider.value * 100); color: "white" }
                Slider {
                    id: sharpDetailSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.25
                    property real defaultValue: 0.25
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpDetailSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Masking:  " + Math.round(sharpMaskSlider.value * 100); color: "white" }
                Slider {
                    id: sharpMaskSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpMaskSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                // ===== Noise Reduction — 섹션 인덱스 12 (텍스처/샤프닝 앞 단계) =====
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[12] ? "▾  " : "▸  ") + "Noise Reduction"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(12) }
                }
                ColumnLayout {
                    visible: win.secOpen[12]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Luminance:  " + Math.round(lumaNrSlider.value * 100); color: "white" }
                Slider {
                    id: lumaNrSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(lumaNrSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Color:  " + Math.round(colorNrSlider.value * 100); color: "white" }
                Slider {
                    id: colorNrSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(colorNrSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                // AI 디노이즈(SCUNet): Luminance 의 노이즈 베이스를 AI 추론 결과로 교체(온디맨드).
                // 계산 완료까지는 기존 가이디드 필터 베이스로 동작 → 체감은 완료 시점에 바뀜.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: aiNrCheck
                        checked: false
                        // 켤 때는 GPU 확인 경유(CPU 폴백이면 진행 여부 대화상자)
                        onToggled: checked ? win.requestAiNr(true) : controller.setAiNr(false)
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "AI denoise (NAFNet)"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                        wrapMode: Text.WordWrap
                        ToolTip.visible: aiNrLblHover.hovered
                        ToolTip.delay: 600
                        ToolTip.text: "Replaces the Luminance and Color denoise bases with an AI model\n(NAFNet). Runs on GPU when available (DirectML) — a few seconds.\nOn CPU it is slower (preview ≈ ½ min, full-res export ≈ 2–3 min)."
                        HoverHandler { id: aiNrLblHover }
                        TapHandler {
                            onTapped: {
                                aiNrCheck.checked = !aiNrCheck.checked
                                if (aiNrCheck.checked) win.requestAiNr(true)
                                else controller.setAiNr(false)
                            }
                        }
                    }
                }
                Label {
                    visible: controller.aiNrStatus !== ""
                    text: controller.aiNrStatus
                    color: "#9a9a9a"; font.pixelSize: 11
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[9] ? "▾  " : "▸  ") + "Lens Corrections"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(9) }
                }
                ColumnLayout {
                    visible: win.secOpen[9]
                    Layout.fillWidth: true
                    spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: lensCheck
                        checked: controller.lensCorrection
                        onToggled: controller.setLensCorrection(checked)
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Lens profile (embedded)"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                        wrapMode: Text.WordWrap          // 패널 폭 초과 시 잘림 대신 줄바꿈
                        ToolTip.visible: lensLblHover.hovered
                        ToolTip.delay: 600
                        ToolTip.text: "Distortion · vignetting · chromatic aberration —\nper-shot correction tables embedded in the RAW by the camera (currently Fujifilm RAF)."
                        HoverHandler { id: lensLblHover }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[10] ? "▾  " : "▸  ") + "Date Stamp"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(10) }
                }
                ColumnLayout {
                    visible: win.secOpen[10]
                    Layout.fillWidth: true
                    spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: stampCheck
                        enabled: controller.imagePath !== ""
                        onToggled: win.dateStamp = checked
                    }
                    // 인라인 checked: 바인딩은 첫 클릭 시 컨트롤 내부 write 로 파괴돼
                    // 이후 D 단축키/로드/리셋의 win.dateStamp 변경이 박스에 반영 안 됨.
                    // 독립 Binding 은 win.dateStamp 변경마다 재푸시하므로 desync 없음.
                    Binding { target: stampCheck; property: "checked"; value: win.dateStamp }
                    Label {
                        Layout.fillWidth: true
                        text: "Film date stamp  (D)"
                        color: stampCheck.enabled ? "white" : "#777"
                        font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                // 날짜 직접 입력(기본값=EXIF). 변경 시 디바운스 후 프리뷰 재렌더.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label { text: "Date"; color: "white"; font.pixelSize: 12 }
                    TextField {
                        id: stampField
                        objectName: "stampField"   // 앱 레벨 포커스아웃 필터(main.py)가 탐색
                        Layout.fillWidth: true
                        enabled: win.dateStamp && controller.imagePath !== ""
                        placeholderText: "'YY MM DD  (e.g. '24 05 12)"
                        onTextEdited: stampDebounce.restart()
                        // 포커스가 잡히면 알파벳 단축키(I/D/B/L 등)를 입력으로 먹으므로,
                        // Enter=확정/Esc=취소 시 포커스를 풀어 단축키가 다시 동작하게 함.
                        onAccepted: { stampDebounce.stop(); controller.setStampText(text); focus = false }
                        Keys.onEscapePressed: focus = false
                        // hover 시 텍스트(I-beam) 커서. HoverHandler 는 hover 만 관찰하므로
                        // 클릭/드래그 선택/편집에 일절 관여하지 않음(MouseArea 는 드래그를 가로챔).
                        HoverHandler {
                            enabled: stampField.enabled
                            cursorShape: Qt.IBeamCursor
                        }
                    }
                }
                // 폰트 방식(필름 데이트백 대표 8종, 모두 DSEG OFL). 저장은 이미지별.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label { text: "Style"; color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12 }
                    ComboBox {
                        id: stampFontCombo
                        Layout.fillWidth: true
                        enabled: win.dateStamp && controller.imagePath !== ""
                        model: ["7-seg Classic Regular", "7-seg Classic Regular Italic",
                                "7-seg Classic Bold", "7-seg Classic Bold Italic",
                                "14-seg Classic Regular", "14-seg Classic Regular Italic",
                                "14-seg Classic Bold", "14-seg Classic Bold Italic", "Dot-matrix"]
                        readonly property var keys: ["7c_reg", "7c_reg_it", "7c_bold", "7c_bold_it",
                                "14c_reg", "14c_reg_it", "14c_bold", "14c_bold_it", "dotmatrix"]
                        onActivated: controller.setStampFont(keys[currentIndex])
                        // 드롭다운 닫힘 → 포커스 해제(단축키 복귀 — captionLevelCombo 와 동일)
                        Connections {
                            target: stampFontCombo.popup
                            function onClosed() { viewport.forceActiveFocus() }
                        }
                    }
                    // 인라인 currentIndex 바인딩은 첫 선택 시 파괴되므로 독립 Binding 으로
                    // 로드/리셋 시 controller 값 재푸시(stampCheck 와 동일 desync 방지).
                    Binding {
                        target: stampFontCombo; property: "currentIndex"
                        value: Math.max(0, stampFontCombo.keys.indexOf(controller.stampFont))
                    }
                }
                // 크기 = 숫자높이/짧은변 비율 직접 지정(슬라이더). 더블클릭=기본 3.2% 리셋.
                Label {
                    text: "Stamp size:  " + (stampSizeSlider.value * 100).toFixed(1) + "%"
                    color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12
                }
                Slider {
                    id: stampSizeSlider
                    Layout.fillWidth: true
                    enabled: win.dateStamp && controller.imagePath !== ""
                    from: 0.012; to: 0.050; value: 0.032
                    property real defaultValue: 0.032
                    property real _lastPressMs: 0     // isDblPress 가 읽고 씀(없으면 더블클릭 리셋 무동작)
                    property bool _pendingReset: false
                    // 드래그(user)만 controller 로 push — 프로그램 대입(로드/리셋)은 onMoved 안 불림.
                    onMoved: controller.setStampSize(value)
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(stampSizeSlider)
                        else if (_pendingReset) { value = defaultValue; controller.setStampSize(defaultValue); _pendingReset = false }
                    }
                }
                // 여백 = 코너 안쪽 여백/짧은변 비율. 더블클릭=기본 5.0% 리셋.
                Label {
                    text: "Margin:  " + (stampMarginSlider.value * 100).toFixed(1) + "%"
                    color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12
                }
                Slider {
                    id: stampMarginSlider
                    Layout.fillWidth: true
                    enabled: win.dateStamp && controller.imagePath !== ""
                    from: 0.0; to: 0.10; value: 0.05
                    property real defaultValue: 0.05
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onMoved: controller.setStampMargin(value)
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(stampMarginSlider)
                        else if (_pendingReset) { value = defaultValue; controller.setStampMargin(defaultValue); _pendingReset = false }
                    }
                }
                Timer {
                    id: stampDebounce
                    interval: 200
                    onTriggered: controller.setStampText(stampField.text)
                }
                Connections {
                    target: controller
                    // 새 파일 디코딩 완료 후 편집 복원/초기화(controller 가 fresh-load 1회만 발화).
                    function onEditsReady() {
                        // 새 파일 *디코딩 완료* 후: 저장된 편집이 있으면 복원, 없으면 기본값으로 초기화.
                        // (디코딩 전 트리거 금지 — 이전 이미지에 새 편집이 잘못 반영되는 것 방지)
                        win._applying = true
                        var e = controller.editsForCurrent()
                        if (e && e.v !== undefined) {
                            win.applyEdits(e)
                        } else {
                            win.resetAllEdits()
                            stampField.text = controller.stampText
                        }
                        win._applying = false
                        // 로드 전환 중 예약됐을 수 있는 자동저장 취소(fresh-load 는 사이드카를 새로
                        // 만들지 않는다 — 저장본 있으면 복원만, 없으면 기본값 유지). 주황 배지 오발 방지.
                        editSaveTimer.stop()
                        win.refreshHistogram()
                        win.histReset(JSON.stringify(win.editParams()))   // 로드 상태 = undo baseline
                    }
                }
                }   // end Date Stamp section

                        }   // end panelCol
                    }       // end Flickable (Edit 페이지)

                    // ===== index 1: Crop / Rotate / Geometry (UI 골격만, 변환은 다음 단계) =====
                    Flickable {
                        id: geoScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: geoCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: B.ScrollBar {
                            id: geoBar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle { implicitWidth: 8; radius: 4; color: geoBar.pressed ? "#cfcfcf" : "#9a9a9a" }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: geoCol
                            x: 16; y: 16
                            width: geoScroll.width - 32
                            spacing: 12

                            // ---- Crop ----
                            Label {
                                text: "Crop"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label { text: "Aspect Ratio"; color: "white"; font.pixelSize: 12 }
                            ComboBox {
                                id: aspectCombo
                                Layout.fillWidth: true
                                currentIndex: 0
                                model: ["Original", "Free", "1:1",
                                        "3:2", "4:3", "16:9", "5:4"]
                                // 고정 비율 선택 -> 박스를 그 비율 중앙 최대로. 원본/자유 -> 전체로.
                                onActivated: {
                                    if (win.cropAspect > 0) win.applyCropAspect()
                                    else win.resetCropRect()
                                }
                                // 드롭다운 닫히면 포커스 해제(단축키 복구 — captionLevelCombo 와 동일)
                                Connections {
                                    target: aspectCombo.popup
                                    function onClosed() { viewport.forceActiveFocus() }
                                }
                            }
                            Label { text: "Orientation"; color: "white"; font.pixelSize: 12 }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button { id: cropLandscapeBtn; text: "Landscape"; checkable: true; checked: true; autoExclusive: true; Layout.fillWidth: true; Layout.preferredWidth: 0
                                         onClicked: win.applyCropAspect() }
                                Button { id: cropPortraitBtn; text: "Portrait"; checkable: true; autoExclusive: true; Layout.fillWidth: true; Layout.preferredWidth: 0
                                         onClicked: win.applyCropAspect() }
                            }
                            // 안내
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: "On the image: box handles = resize, drag inside = move, drag near a corner = rotate."
                                color: "#9a9a9a"; font.pixelSize: 11
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Rotate ----
                            Label {
                                text: "Rotate"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }

                            Label {
                                text: "Angle (Straighten):  "
                                      + (rotAngleSlider.value >= 0 ? "+" : "")
                                      + rotAngleSlider.value.toFixed(1) + "°"
                                color: "white"
                            }
                            Slider {
                                id: rotAngleSlider
                                Layout.fillWidth: true
                                from: -45.0; to: 45.0; value: 0.0
                                property real defaultValue: 0.0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(rotAngleSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }

                            Label {
                                text: "Rotate 90°" + (win.quarterTurns !== 0 ? "  (" + (win.quarterTurns * 90) + "°)" : "")
                                color: "white"; font.pixelSize: 12
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button {
                                    text: "⟲ 90°"
                                    Layout.fillWidth: true
                                    ToolTip.visible: hovered
                                    ToolTip.text: "90° CCW"
                                    onClicked: { win.quarterTurns = (win.quarterTurns + 3) % 4; win.applyCropAspect() }
                                }
                                Button {
                                    text: "⟳ 90°"
                                    Layout.fillWidth: true
                                    ToolTip.visible: hovered
                                    ToolTip.text: "90° CW"
                                    onClicked: { win.quarterTurns = (win.quarterTurns + 1) % 4; win.applyCropAspect() }
                                }
                            }

                            Label { text: "Flip"; color: "white"; font.pixelSize: 12 }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button {
                                    id: flipHBtn
                                    text: "Flip horizontal"
                                    checkable: true
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 0
                                }
                                Button {
                                    id: flipVBtn
                                    text: "Flip vertical"
                                    checkable: true
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 0
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Geometry (원근/왜곡 보정) ----
                            Label {
                                text: "Geometry"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label { text: "Vertical perspective:  " + geoVSlider.value.toFixed(0); color: "white" }
                            Slider {
                                id: geoVSlider
                                Layout.fillWidth: true
                                from: -100; to: 100; value: 0
                                property real defaultValue: 0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(geoVSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }
                            Label { text: "Horizontal perspective:  " + geoHSlider.value.toFixed(0); color: "white" }
                            Slider {
                                id: geoHSlider
                                Layout.fillWidth: true
                                from: -100; to: 100; value: 0
                                property real defaultValue: 0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(geoHSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }
                            Label { text: "Scale:  " + geoScaleSlider.value.toFixed(0) + "%"; color: "white" }
                            Slider {
                                id: geoScaleSlider
                                Layout.fillWidth: true
                                from: 50; to: 150; value: 100
                                property real defaultValue: 100
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(geoScaleSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            Button {
                                text: "Reset Crop · Rotate · Geometry"
                                Layout.fillWidth: true
                                onClicked: win.resetGeometry()
                            }

                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: "Note: crop · rotate · geometry (vertical/horizontal perspective · scale) all apply to both preview and export. Trim the empty areas left after perspective correction with the crop tool."
                                color: "#888"; font.pixelSize: 11
                            }
                        }
                    }

                    // ===== index 2: Masking (영역별 로컬 조정 — ML 세그 마스크) =====
                    Flickable {
                        id: maskScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: maskCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: B.ScrollBar {
                            id: maskBar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle { implicitWidth: 8; radius: 4; color: maskBar.pressed ? "#cfcfcf" : "#9a9a9a" }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: maskCol
                            x: 16; y: 16
                            width: maskScroll.width - 32
                            spacing: 12

                            // ---- Create Mask: 클래스 체크박스(복합 선택, 라이브 재조합) ----
                            Label {
                                text: "Create Mask"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Check one or more — the mask is the union of the selected classes."
                                color: "#888"; font.pixelSize: 11
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 4; rowSpacing: 2
                                Repeater {
                                    model: controller.maskGroups
                                    delegate: RowLayout {
                                        Layout.fillWidth: true; spacing: 6
                                        CheckBox {
                                            id: maskKeyCheck
                                            enabled: controller.imagePath !== "" && !controller.skyBusy
                                            onToggled: win.toggleMaskKey(modelData.key, checked)
                                        }
                                        // 인라인 checked: 바인딩은 첫 클릭 시 파괴 → Clear(resetSky)
                                        // 나 이미지 로드(applySkyEdits)의 maskKeys 변경이 박스에
                                        // 반영 안 됨. 독립 Binding 이 변경마다 재푸시(desync 방지).
                                        Binding {
                                            target: maskKeyCheck; property: "checked"
                                            value: win.maskKeys.indexOf(modelData.key) >= 0
                                        }
                                        Label {
                                            Layout.fillWidth: true; text: modelData.label
                                            color: "white"; font.pixelSize: 12
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }
                                }
                            }
                            // ---- 또는: 이미지에서 대상 클릭 선택 (SAM, 픽셀 정밀) ----
                            Label {
                                text: "Or click a subject"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                                Layout.topMargin: 4
                            }
                            Button {
                                Layout.fillWidth: true
                                enabled: controller.imagePath !== "" && !controller.skyBusy
                                checkable: true
                                checked: win.samSelectMode
                                text: win.samSelectMode ? "Now click the subject on the image…"
                                                        : "Select subject by click"
                                onToggled: win.samSelectMode = checked
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Turn on, then click any object in the photo to mask it. Downloads a small model on first use."
                                color: "#888"; font.pixelSize: 11
                            }
                            Button {
                                text: "Clear"
                                Layout.fillWidth: true
                                enabled: controller.imagePath !== ""
                                onClicked: win.resetSky()
                            }
                            // 선택 진행 중/완료 상태는 이미지 위 스피너 오버레이가 표시(controller.skyBusy).
                            // 선택 '완료'(클리어 제외) → 마스크 오버레이 자동 표시
                            Connections {
                                target: controller
                                // 사용자 선택 → 오버레이 자동 표시. 단 사이드카 복원 재생성은 제외.
                                function onSkySelected() {
                                    if (win._maskRestore) win._maskRestore = false
                                    else win.showSkyMask = true
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                CheckBox {
                                    id: skyShowCheck
                                    onToggled: win.showSkyMask = checked
                                }
                                // 인라인 checked: 바인딩은 첫 클릭 시 파괴 → 이후 슬라이더
                                // 드래그/resetSky/onSkySelected 의 showSkyMask 변경이 박스에
                                // 반영 안 됨. 독립 Binding 이 변경마다 재푸시(desync 방지).
                                Binding {
                                    target: skyShowCheck; property: "checked"
                                    value: win.showSkyMask
                                }
                                Label {
                                    Layout.fillWidth: true; text: "Show mask overlay (red)"
                                    color: "white"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                CheckBox { id: skyInvertCheck }
                                Label {
                                    Layout.fillWidth: true; text: "Invert mask (everything but the selection)"
                                    color: "white"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Adjustments (활성 마스크 영역 전용) ----
                            Label {
                                text: "Adjustments"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            SkySlider { id: skyExpSlider;     host: win; label: "Exposure"; suffix: "  (stop)" }
                            SkySlider { id: skyContrastSlider; host: win; label: "Contrast"; from: 0.5; to: 2.0; value: 1.0; defaultValue: 1.0 }
                            SkySlider { id: skyTempSlider;    host: win; label: "Temp"; suffix: "  (− cool / + warm)" }
                            SkySlider { id: skyTintSlider;    host: win; label: "Tint"; suffix: "  (− green / + magenta)" }
                            SkySlider { id: skyHiSlider;      host: win; label: "Highlights" }
                            SkySlider { id: skyShadowsSlider; host: win; label: "Shadows" }
                            SkySlider { id: skyTextureSlider; host: win; label: "Texture" }
                            SkySlider { id: skyClaritySlider; host: win; label: "Clarity" }
                            SkySlider { id: skyDehazeSlider;  host: win; label: "Dehaze" }
                            SkySlider { id: skySatSlider;     host: win; label: "Saturation" }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Check one or more classes above to build the mask; the sliders apply only to the masked region. Applies to both preview and export."
                                color: "#888"; font.pixelSize: 11
                            }
                        }
                    }
                }   // end StackLayout
            }       // end 우측 패널 outer ColumnLayout
        }           // end 우측 패널 Rectangle

        // ---------- 우측 끝 세로 패널 셀렉터 바 ----------
        Rectangle {
            Layout.preferredWidth: 44
            Layout.fillHeight: true
            color: "#222"

            Column {
                anchors.top: parent.top
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.topMargin: 8
                spacing: 4

                Repeater {
                    model: [
                        { icon: "edit", tip: "Edit", key: "Ctrl+1" },
                        { icon: "crop", tip: "Crop / Rotate / Geometry", key: "Ctrl+2" },
                        { icon: "mask", tip: "Masking", key: "Ctrl+3" }
                    ]
                    delegate: Rectangle {
                        width: 40; height: 40
                        radius: 6
                        color: win.activePanel === index ? "#3a4a6b"
                               : (selMouse.containsMouse ? "#33373f" : "transparent")
                        border.width: win.activePanel === index ? 1 : 0
                        border.color: "#8ab4f8"

                        // 기능 아이콘(편집=연필, 크롭=크롭 브래킷). 활성=accent, 비활성=회색.
                        Canvas {
                            anchors.fill: parent
                            property string ic: modelData.icon
                            property color col: win.activePanel === index ? "#8ab4f8"
                                                : (selMouse.containsMouse ? "#e6e6e6" : "#cfcfcf")
                            onColChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var o = 8                       // 40px 버튼 안 24px 아이콘 오프셋
                                function P(x, y) { return [o + x, o + y] }
                                ctx.lineWidth = 2
                                ctx.lineJoin = "round"; ctx.lineCap = "round"
                                ctx.strokeStyle = col; ctx.fillStyle = col
                                if (ic === "edit") {
                                    // 조정 슬라이더 아이콘(가로선 3 + 노브) — 사진 보정 패널
                                    var rows = [[6, 16], [12, 9], [18, 14]]   // [y, knobX]
                                    for (var i = 0; i < 3; i++) {
                                        var a = P(3, rows[i][0]), b = P(21, rows[i][0])
                                        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke()
                                        var k = P(rows[i][1], rows[i][0])
                                        ctx.beginPath(); ctx.arc(k[0], k[1], 2.6, 0, 2 * Math.PI); ctx.fill()
                                    }
                                } else if (ic === "crop") {
                                    // 크롭 브래킷(└ 좌하 + ┐ 우상)
                                    ctx.lineCap = "butt"; ctx.lineJoin = "miter"
                                    function seg(a, b) { ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke() }
                                    seg(P(7,2), P(7,17));  seg(P(7,17), P(22,17))
                                    seg(P(2,7), P(17,7));  seg(P(17,7), P(17,22))
                                } else {
                                    // 마스킹: 프레임(이미지) + 채운 원(선택 영역) — 영역별 보정
                                    ctx.strokeRect(o + 3, o + 4, 18, 16)
                                    var c = P(9, 13)
                                    ctx.beginPath(); ctx.arc(c[0], c[1], 5, 0, 2 * Math.PI); ctx.fill()
                                }
                            }
                        }
                        MouseArea {
                            id: selMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.activePanel = index
                        }
                        ToolTip.visible: selMouse.containsMouse
                        ToolTip.delay: 1500
                        ToolTip.text: modelData.tip + "  (" + modelData.key + ")"
                    }
                }
            }
        }
    }

}
