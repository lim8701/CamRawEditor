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

                    // 아이콘 버튼(우측 패널 공용) — ♥/☑/태그/위로가기와 같은 커스텀 패턴.
    // ⚠️네이티브 Button 을 쓰지 않는다: macOS 스타일이 **베젤을 아이템 안에서 치우쳐** 그린다
    //   (실측 26x26 은 y 5.0~24.5 로 2.25px 아래, 26x32 는 pill 이 x +7px 밀림). 스타일의
    //   비대칭 padding(13/12)이 그 보정값이라 padding 을 건드리면 아이콘이 어긋나고, 보정값을
    //   박아도 실행 상태에 따라 pill 위치가 변해 안정적이지 않다. 크롬을 직접 그리면 위치가
    //   우리 손에 있고 Windows 와도 동일하다. 아이콘은 SVG(assets/icons/, 잉크가 viewBox 정중앙).
    component IconBtn: Rectangle {
        property alias icon: img.source
        property bool active: true      // false = 비활성(흐리게 + 클릭 무시)
        property string tip: ""
        signal clicked()
        Layout.preferredWidth: 26; Layout.preferredHeight: 26
        Layout.alignment: Qt.AlignVCenter
        radius: 5
        color: hov.hovered && active ? "#3a3f4b" : "transparent"
        border.color: "#555555"; border.width: 1
        opacity: active ? 1.0 : 0.4
        ToolTip.visible: hov.hovered && tip !== ""
        ToolTip.text: tip
        Image {
            id: img
            anchors.centerIn: parent
            width: 16; height: 16
            sourceSize.width: 32; sourceSize.height: 32   // HiDPI 선명도
            smooth: true
        }
        HoverHandler { id: hov }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            enabled: parent.active
            onClicked: parent.clicked()
        }
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
    property string stampFontError: ""    // 폰트 추가 실패 안내(빈 문자열=숨김)
    // 스탬프 '내 기본값' — 사진 여러 장을 연속 작업할 때 폰트·크기·여백을 매번 다시 잡지
    // 않도록 마지막 사용값을 기억한다(controller 가 사용자 데이터 폴더 JSON 으로 보존).
    // ⚠️`applyEdits` 의 `_ev` 폴백은 이 함수가 아니라 **`lookDef`(=공장 기본값)**를 쓴다.
    //   Reset 버튼도 공장값이라(resetAllEdits(true)) 셋이 일치한다 —
    //   "적용 기본값 = 리셋 값" 불변식은 그 세 곳을 같이 볼 것.
    function stampDef(key) {
        var d = controller.stampDefaults
        return (d && d[key] !== undefined) ? d[key]
             : ({ stampOn: false, stampStyle: "7c_bold", stampSize: 0.032, stampMargin: 0.05,
                  stampColor: "#ff8a29", stampGlow: 1.0, stampSpread: 1.0 })[key]
    }
    // 사용자가 스탬프 컨트롤을 **직접** 바꿨을 때만 기억한다. 로드/리셋의 프로그램 대입까지
    // 저장하면 옛 사진을 열기만 해도 내 기본값이 그 사진 값으로 덮인다.
    function rememberStamp() {
        if (win._applying) return
        // 값은 슬라이더가 아니라 **controller** 에서 읽는다 — editParams()/사이드카가 보는
        // 것과 같은 하나의 진실원이라, 슬라이더와 controller 가 어긋나도 기억값이 안 튄다.
        controller.rememberStampPrefs({ "stampOn": win.dateStamp,
                                        "stampStyle": controller.stampFont,
                                        "stampSize": controller.stampSize,
                                        "stampMargin": controller.stampMargin,
                                        "stampColor": controller.stampColor,
                                        "stampGlow": controller.stampGlow,
                                        "stampSpread": controller.stampSpread })
    }
    Shortcut { sequence: "D"; enabled: !win._typing
               onActivated: { win.dateStamp = !win.dateStamp; win.rememberStamp() } }

    // AI 캡션 오버레이 표시 여부 (C 키로 토글). 끄면 로드 시 자동 생성도 중단(연산 낭비 방지).
    property bool captionOverlay: true
    onCaptionOverlayChanged: controller.setCaptionEnabled(captionOverlay)
    Shortcut { sequence: "C"; enabled: !win._typing; onActivated: win.captionOverlay = !win.captionOverlay }

    // 좌측 File Explorer 패널 표시 여부 (B 키로 토글)
    property bool showExplorer: true
    Shortcut { sequence: "B"; enabled: !win._typing; onActivated: win.showExplorer = !win.showExplorer }

    // 컨택트 시트(폴더 격자) 모드. ★규칙은 **두 줄뿐**이다:
    //   ① G 키(또는 상단 표시줄 ▦ 버튼)로 켜고 끈다.  ② 아직 사진을 안 열었으면 켜져 있다.
    // ⚠️예전에는 '다른 폴더로 이동하면 자동으로 켜기'까지 있었는데 **켜지고 꺼지는 상황이
    //   경우마다 달라 혼란스럽다**는 보고를 받고 걷어냈다(폴더를 옮겨도 편집 중인 사진은 그대로).
    //   조건을 하나 더 붙이고 싶어지면 이 보고를 먼저 떠올릴 것.
    property bool gridPinned: false
    Shortcut { sequence: "G"; enabled: !win._typing; onActivated: win.gridPinned = !win.gridPinned }
    // **다른 사진을 열면** 격자를 닫는다(격자/탐색기/프리뷰 어디서 열든 동일).
    // ⚠️경로 비교 없이 imageChanged 만 보면 WB 커밋 같은 재디코딩에도 닫혀 '왜 꺼졌지'가 된다.
    property string _gridLastPath: ""
    Connections {
        target: controller
        function onImageChanged() {
            if (controller.imagePath !== win._gridLastPath) {
                win._gridLastPath = controller.imagePath
                win.gridPinned = false
            }
        }
    }


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
    // 존 시스템 오버레이(프리뷰): 휘도를 안셀 아담스 존 0..X(1존=1스톱, V=18% 그레이)로
    // 양자화 표시. Z 키 토글. export 불변(진단 전용).
    property bool zoneOverlay: false
    Shortcut { sequence: "Z"; enabled: !win._typing; onActivated: win.zoneOverlay = !win.zoneOverlay }
    // Undo / Redo (편집 스냅샷)
    Shortcut { sequences: [StandardKey.Undo]; onActivated: win.undo() }                    // Ctrl+Z
    Shortcut { sequences: [StandardKey.Redo, "Ctrl+Shift+Z"]; onActivated: win.redo() }    // Ctrl+Y / Ctrl+Shift+Z
    // 우측 패널 전환: Edit / Crop·Geometry / Masking / Wallpaper
    Shortcut { sequence: "Ctrl+1"; onActivated: win.activePanel = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: win.activePanel = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: win.activePanel = 2 }
    Shortcut { sequence: "Ctrl+4"; onActivated: win.activePanel = 3 }
    Shortcut { sequence: "Ctrl+5"; enabled: controller.wallpaperEnabled; onActivated: win.activePanel = 4 }

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
    // 13 = Recipes(레시피 프리셋). 배지 그리드가 세로 공간을 먹으므로 기본 접힘.
    property var secOpen: [true, true, true, true, true, false, true, true, false, false, true, false, false, false, false]
    function toggleSec(i) { var a = secOpen.slice(); a[i] = !a[i]; secOpen = a }

    // 마스크 선택영역 오버레이 표시(프리뷰 전용, 활성 레이어 마스크)
    property bool showSkyMask: false

    // ---- 브러시(수동 마스킹) — 활성 레이어 마스크 위에 획 추가/빼기 ----
    // 획은 벡터로 레이어 dict(strokes)에 저장(사이드카/undo 는 skyEditParams 로 자동 편승),
    // 컨트롤러 미러(addStroke 등)가 자동 마스크 위에 리플레이한다(brush.py).
    property int brushMode: 0          // 0=끔, 1=추가(+), 2=지우기(−). 마스킹 패널에서만.
    property real brushSize: 0.06      // 반경(프록시 짧은 변 대비 비율)
    property real brushFeather: 0.5    // 반경 중 falloff 비중(0=하드, 1=전부 소프트)
    readonly property int activeStrokeCount: {
        win.layers                      // 재대입 추적(획 커밋마다 slice 재대입됨)
        return (win.layers[win.activeLayer].strokes || []).length
    }
    function setBrushMode(m) {          // 같은 버튼 재클릭 = 끄기(토글)
        win.brushMode = (win.brushMode === m) ? 0 : m
        if (win.brushMode !== 0) win.showSkyMask = true   // 칠하는 대상이 보이게
    }
    // ⚠️획 undo 는 **전역 Ctrl+Z 하나로 통일**(라이트룸식). 별도 "Undo stroke" 버튼은
    // 이중 undo 체계(버튼의 되돌림 자체가 또 전역 스텝이 됨 → Ctrl+Z 가 지운 획을 되살리는
    // 꼬임)라 기각·제거. 전역 undo 의 획 변경은 applySkyEdits 가 tail-diff 로 감지해
    // popStroke/addStroke 즉각 경로를 탄다. 획 1개 = 스냅샷 1개(commitEditSnapshot 즉시).
    function commitBrushStroke(stroke) {
        var L = win.layers[win.activeLayer]
        var s = (L.strokes || []).slice(); s.push(stroke); L.strokes = s
        win.layers = win.layers.slice()   // notify → 자동저장 watch
        controller.addStroke(win.activeLayer, stroke)
        win.commitEditSnapshot()          // 디바운스 코얼레싱 방지 — 획 1개 = undo 스텝 1개
    }
    function clearBrushStrokes() {
        var L = win.layers[win.activeLayer]
        if ((L.strokes || []).length === 0) return
        L.strokes = []
        win.layers = win.layers.slice()
        controller.clearStrokes(win.activeLayer)
        win.commitEditSnapshot()
    }
    // 브러시 모드 단축키(마스킹 패널 + 사진 로드 시에만): A=Add, S=Subtract — 재입력=끄기
    // (버튼 토글과 동일), ESC=끄기. A/S 는 나란한 키라 확대/패닝과 오가는 한 손 워크플로우에
    // 최적. 용어는 라이트룸 마스킹(Add/Subtract)과 동일 — Erase 는 도구 은유가 섞여 기각.
    Shortcut {
        sequence: "A"
        enabled: !win._typing && win.activePanel === 2 && controller.imagePath !== ""
        onActivated: win.setBrushMode(1)
    }
    Shortcut {
        sequence: "S"
        enabled: !win._typing && win.activePanel === 2 && controller.imagePath !== ""
        onActivated: win.setBrushMode(2)
    }
    // O = 마스크 오버레이(빨강) 토글 — 라이트룸 Show Overlay 와 동일 키. 마스킹 패널 한정.
    Shortcut {
        sequence: "O"
        enabled: !win._typing && win.activePanel === 2 && controller.imagePath !== ""
        onActivated: win.showSkyMask = !win.showSkyMask
    }
    // ESC = 브러시 끄기(브러시 켜졌을 때만 — 다른 ESC 소비자와 enabled 로 비충돌)
    Shortcut { sequence: "Escape"; enabled: win.brushMode !== 0; onActivated: win.brushMode = 0 }
    // 마스킹 패널을 떠나면 브러시도 끔(다른 패널에서 오조작 방지)
    onActivePanelChanged: if (activePanel !== 2) win.brushMode = 0
    // 로컬 마스크 레이어(동적 생성/삭제, 최대 5) — 각 {keys, invert, 10 조정}. 슬라이더/체크박스는
    // activeLayer 를 편집. layers 는 항상 길이 5(고정 슬롯=셰이더/컨트롤러 정합), layerCount 개만 활성.
    property int activeLayer: 0
    property int layerCount: 1               // 현재 존재하는 레이어 수(1..5) — UI/저장 대상
    property int maxLayers: 5
    function _newLayer() {
        // faceSelMemo: 마지막 부위를 끌 때 버려지는 face@ 선택의 백업(세션 한정, 직렬화 안 됨 —
        // skyEditParams 가 필드를 명시 나열하므로 사이드카에 새지 않는다). 부위를 다시 켤 때
        // '가장 큰 얼굴' 대신 직전 선택을 복원하는 용도(toggleMaskKey).
        return { keys: [], invert: false, strokes: [], faceSelMemo: [],
                 skyExp: 0, skyTemp: 0, skyTint: 0, skySat: 0, skyHi: 0,
                 skyShadows: 0, skyContrast: 1.0, skyTexture: 0, skyClarity: 0, skyDehaze: 0 }
    }
    property var layers: [ _newLayer(), _newLayer(), _newLayer(), _newLayer(), _newLayer() ]
    // 컨트롤러 마스크를 현재 layers 슬롯에 재동기(삭제로 시프트된 뒤 등). 캐시된 확률이라 재조합 저렴.
    function _resyncLayers() {
        for (var q = 0; q < win.maxLayers; q++) {
            var hasContent = q < win.layerCount
                && (win.layers[q].keys.length > 0 || (win.layers[q].strokes || []).length > 0)
            if (hasContent) {
                // 획을 먼저 밀어야 setMaskClasses 워커가 옳은 획으로 돈다(시프트 후 슬롯 불일치 방지)
                controller.setStrokes(q, win.layers[q].strokes || [])
                controller.setMaskClasses(q, win.layers[q].keys)
            } else controller.clearLayer(q)
        }
    }
    function addLayer() {                     // 새 빈 레이어 추가 → 그 레이어로 전환
        if (win.layerCount >= win.maxLayers) return
        win.layerCount += 1
        win.selectLayer(win.layerCount - 1)
    }
    function deleteLayer(i) {                 // 레이어 i 삭제(뒤 슬롯 앞으로 시프트, 빈 슬롯으로 끝 채움)
        if (win.layerCount <= 1) return       // 최소 1개 유지
        win.saveActiveFromSliders()
        var ls = win.layers.slice()
        ls.splice(i, 1); ls.push(win._newLayer())
        win._loadingLayer = true
        win.layers = ls
        win.layerCount -= 1
        if (win.activeLayer >= win.layerCount) win.activeLayer = win.layerCount - 1
        win._loadingLayer = false
        win._resyncLayers()                   // 시프트된 슬롯에 마스크 재생성
        win.loadActiveToSliders()
    }
    property bool _loadingLayer: false      // 레이어 로드 중 저장 억제(루프 방지)
    // 활성 레이어의 선택 클래스(체크박스 편의 미러). toggleMaskKey 가 layers[active].keys 와 동기.
    property var maskKeys: []
    // Create Mask 탭: 0=Scene(장면 클래스) 1=Face(얼굴 부위). 표시만 나누고 마스크는 **두 탭의
    // 합집합** — 얼굴 부위 key 는 "face:" 접두사로 구분되고 컨트롤러가 갈라서 처리한다.
    property int maskTab: 0
    // 숨은 탭의 선택이 안 보여 혼란스럽지 않도록 탭 라벨에 개수를 띄운다.
    // ⚠️얼굴 '선택' key(face@…)는 부위가 아니라 대상이라 양쪽 카운트 모두에서 뺀다 —
    //   안 그러면 face: 로 시작하지 않아 Scene 쪽으로 잘못 세어진다.
    function maskTabCount(tab) {
        if (tab === 2) return win.depthOn ? 1 : 0     // 깊이는 항목이 하나(범위) — 켜짐/꺼짐
        var n = 0
        for (var i = 0; i < win.maskKeys.length; i++) {
            var k = String(win.maskKeys[i])
            // face@(얼굴 선택)·depth@(거리 범위)는 '클래스'가 아니라 대상/범위 → 양쪽 카운트에서 뺀다.
            if (k.indexOf("face@") === 0 || win._isDepth(k)) continue
            if ((k.indexOf("face:") === 0) === (tab === 1)) n++
        }
        return n
    }
    function _isFacePart(k) { return String(k).indexOf("face:") === 0 }
    // 얼굴 부위가 하나라도 켜져 있는지 = 실제로 얼굴이 마스킹되는 상태인지.
    // 타일 표시는 이걸 따라야 한다 — 부위가 없으면 마스킹되는 얼굴도 없다.
    function hasFacePart() {
        for (var i = 0; i < win.maskKeys.length; i++)
            if (win._isFacePart(win.maskKeys[i])) return true
        return false
    }
    function _isFaceSel(k)  { return String(k).indexOf("face@") === 0 }
    function _hasFaceSel(a) {
        for (var i = 0; i < a.length; i++) if (win._isFaceSel(a[i])) return true
        return false
    }
    // ---- Depth 탭: 거리 범위 마스크 ----
    // 상태의 단일 진실원은 **maskKeys 안의 `depth@near,far,feather` 항목 하나**다(face@ 가 얼굴
    // 선택을 좌표로 담는 것과 같은 방식). 아래 4개는 슬라이더 표시용 미러 — 이렇게 두면 사이드카
    // 저장·undo·재오픈 복원이 기존 keys 직렬화에 그대로 얹혀 새 필드가 필요 없다.
    // 기본값은 **배경 방향**(near=경계, far=1). '배경만 손보기'가 가장 흔한 의도이고,
    // 실제 값은 켜는 순간 이미지 히스토그램에서 자동 시드된다(depth.auto_range) — 정규화 후에도
    // 분포가 장면마다 크게 달라(실측 평균 0.478~0.679) 고정 상수는 원리적으로 맞을 수 없다.
    property bool depthOn: false
    property real depthNear: 0.5
    property real depthFar: 1.0
    property real depthFeather: 0.10
    function _isDepth(k) { return String(k).indexOf("depth@") === 0 }
    // maskKeys → 미러 갱신. keys 가 통째로 바뀌는 자리(레이어 전환·사이드카 복원)에서 호출.
    function _syncDepthFromKeys() {
        win.depthOn = false
        for (var i = 0; i < win.maskKeys.length; i++) {
            var k = String(win.maskKeys[i])
            if (!win._isDepth(k)) continue
            var payload = k.substring(6)
            if (payload === "auto") { win.depthOn = true; break }   // 확정 전 — 값은 그대로 두고 기다린다
            var v = payload.split(",")
            if (v.length !== 3) continue          // 손상된 사이드카 — 조용히 무시
            win.depthNear = parseFloat(v[0]); win.depthFar = parseFloat(v[1])
            win.depthFeather = parseFloat(v[2]); win.depthOn = true
            break
        }
    }
    // 미러 → maskKeys(depth 항목 교체) + 스로틀 재조합. 슬라이더 드래그마다 호출된다.
    // ⚠️소수 4자리 고정 — depth.range_key 와 같은 포맷이어야 setMaskClasses 의 no-op 이 동작한다.
    function _commitDepth() {
        if (win._loadingLayer) return
        win._commitMaskKeys(win._keysWithDepth(
            win.depthOn ? ("depth@" + win.depthNear.toFixed(4) + "," + win.depthFar.toFixed(4)
                           + "," + win.depthFeather.toFixed(4)) : ""), true)
    }
    // 켤 때는 아직 거리 맵이 없어 범위를 정할 수 없다 → 센티넬을 보내고, 워커가 맵을 만든 뒤
    // 분포에서 시드해 depthAutoResolved 로 실제 값이 돌아온다.
    // ⚠️depthOn 을 스스로 세운다 — 호출자(체크박스)가 먼저 세우는 것에 의존하면 다른 경로에서
    //   센티넬만 들어가고 UI 는 꺼진 상태로 남는다.
    function _commitDepthAuto() {
        win.depthOn = true
        win._commitMaskKeys(win._keysWithDepth("depth@auto"), true)
    }
    // 현재 keys 에서 depth 항목만 교체(빈 문자열 = 제거).
    function _keysWithDepth(key) {
        var a = []
        for (var i = 0; i < win.maskKeys.length; i++)
            if (!win._isDepth(win.maskKeys[i])) a.push(win.maskKeys[i])
        if (key !== "") a.push(key)
        return a
    }
    // ---- 깊이 범위 재조합 스로틀 ----
    // 깊이 범위는 **연속 값**이라 디바운스(멈춘 뒤 발화)가 구조적으로 안 맞는다 — 드래그 중엔
    // 아무 것도 안 보이고 손을 놓아야 갱신돼 실시간으로 느껴질 수 없다(220ms 가 체감 지연의 74%).
    // 대신 즉시 한 번 보내고, 이후에는 **워커가 끝나는 대로 가장 최신 값만** 보낸다:
    // 스레드가 쌓이지 않고 갱신 주기가 실제 비용(밴드패스 46ms + uint8/QImage 30ms)에 스스로 맞춰진다.
    readonly property int _depthThrottleMs: 60
    property double _depthLastMs: 0
    function _throttleMask() {
        if (controller.skyBusy || Date.now() - win._depthLastMs < win._depthThrottleMs) {
            // ⚠️running 검사 없이 restart() 하면 드래그가 이어지는 동안 계속 밀려 디바운스로 되돌아간다.
            if (!depthTrailTimer.running) depthTrailTimer.start()
            return
        }
        maskApplyTimer.stop()          // 체크박스 디바운스 예약이 남아 있으면 중복 발화 방지
        win._depthLastMs = Date.now()
        controller.setMaskClasses(win.activeLayer, win.maskKeys)
    }
    Timer {
        id: depthTrailTimer
        interval: win._depthThrottleMs
        onTriggered: {
            if (controller.skyBusy) { restart(); return }   // 워커 진행 중 → 끝나면 최신 값으로
            win._depthLastMs = Date.now()
            controller.setMaskClasses(win.activeLayer, win.maskKeys)
        }
    }
    // 자동 시드 확정 → 센티넬을 실제 값으로 교체. 마스크는 이미 이 값으로 만들어졌고 컨트롤러의
    // _layer_keys 도 같이 갱신됐으므로, 재조합을 유발하지 않도록 _commitMaskKeys 를 거치지 않는다.
    Connections {
        target: controller
        function onDepthAutoResolved(layer, near, far, feather) {
            // ⚠️QML 쪽 키에도 센티넬이 남아 있을 때만 적용. 추론 중(skyBusy)의 수동 드래그는
            //   트레일링 타이머에 걸려 컨트롤러에 아직 안 갔을 수 있다 — 컨트롤러 가드만으로는
            //   그 틈의 시드가 방금 조작한 값을 덮는다(수동값이 통째로 사라짐).
            if (win.layers[layer].keys.indexOf("depth@auto") < 0) return
            var ls = win.layers.slice()
            var a = []
            for (var i = 0; i < ls[layer].keys.length; i++)
                if (!win._isDepth(ls[layer].keys[i])) a.push(ls[layer].keys[i])
            a.push("depth@" + near.toFixed(4) + "," + far.toFixed(4) + "," + feather.toFixed(4))
            a.sort()                      // 컨트롤러 쪽 정규화와 일치(직렬화 동일)
            ls[layer].keys = a
            win.layers = ls
            if (layer !== win.activeLayer) return
            win._loadingLayer = true      // 슬라이더 대입이 _commitDepth 를 다시 부르지 않게
            win.maskKeys = a
            win.depthOn = true
            win.depthNear = near; win.depthFar = far; win.depthFeather = feather
            depthNearSlider.value = near
            depthFarSlider.value = far
            depthFeatherSlider.value = feather
            win._loadingLayer = false
        }
    }
    // 얼굴 선택 토글. 마지막 하나는 해제 불가 — 0개가 되면 '선택 없음 = 전체'와 구분이 안 되고
    // 마스크가 통째로 사라진다(레이어 삭제 버튼이 최소 1개를 남기는 것과 같은 규칙).
    function toggleFaceKey(key) {
        var a = win.maskKeys.slice()
        // 명시 선택이 없는 상태 = '전체 사용'이고 타일도 전부 켜져 보인다. 그 상태의 첫 클릭은
        // '그 얼굴만 고르기'가 아니라 **'그 얼굴 빼기'** 여야 화면과 동작이 일치한다.
        // (전부 켜진 걸 눌렀는데 그것만 남으면 반대로 동작하는 것처럼 보인다)
        if (!win._hasFaceSel(a)) {
            var all = controller.faceKeys
            var added = 0
            for (var k = 0; k < all.length; k++)
                if (all[k] !== key) { a.push(all[k]); added++ }
            if (added === 0) return          // 얼굴이 하나뿐 → 뺄 수 없음(줄 자체가 숨겨져 있음)
            win._commitMaskKeys(a)
            return
        }
        var i = a.indexOf(key)
        if (i >= 0) {
            var cnt = 0
            for (var j = 0; j < a.length; j++) if (win._isFaceSel(a[j])) cnt++
            if (cnt <= 1) return                  // 마지막 하나 → 무시
            a.splice(i, 1)
        } else {
            a.push(key)
        }
        win._commitMaskKeys(a)
    }
    // 전부 사용 = 선택 key 를 모두 제거(= '선택 없음'). 별도 센티넬이 필요 없다.
    function selectAllFaces() {
        var a = []
        for (var i = 0; i < win.maskKeys.length; i++)
            if (!win._isFaceSel(win.maskKeys[i])) a.push(win.maskKeys[i])
        win._commitMaskKeys(a)
    }
    // throttle=true 면 디바운스(체크박스 연타 코얼레싱용) 대신 스로틀 — 연속 슬라이더용(_throttleMask).
    function _commitMaskKeys(a, throttle) {
        // 검출된 얼굴을 전부 고른 상태 == '선택 없음(전체 사용)'. 같은 화면이 두 가지로
        // 직렬화되면 사이드카가 달라지고 되돌리기에 의미 없는 단계가 하나 끼어든다 → 정규화.
        if (controller.faceCount > 0) {
            var n = 0
            for (var s = 0; s < a.length; s++) if (win._isFaceSel(a[s])) n++
            if (n === controller.faceCount) {
                var b = []
                for (var t = 0; t < a.length; t++) if (!win._isFaceSel(a[t])) b.push(a[t])
                a = b
            }
        }
        a.sort()                    // 순서 차이로 같은 상태가 다르게 직렬화되는 것 방지
        win.maskKeys = a
        win.layers[win.activeLayer].keys = a; win.layers = win.layers.slice()
        if (throttle) win._throttleMask()
        else maskApplyTimer.restart()
    }
    // 마스크 작업 진행 오버레이용 지연 플래그. 얼굴 부위 토글의 재조합은 크롭 단위라 ~70ms 라서,
    // (⚠️Scene 클래스 재조합은 실측 ~900ms — 프록시 전체 scipy 가이디드필터 566ms + fill_holes
    //  154ms. 즉 Scene 토글은 오늘도 dim 이 뜬다. 깊이 범위 슬라이더는 Scene 성분을 캐시해 피한다)
    // skyBusy 를 그대로 쓰면 누를 때마다 어두워졌다 밝아지는 깜빡임만 남는다. 실제로 오래 걸리는
    // 작업(첫 추론 ~1s, 얼굴 파싱, 모델 다운로드)에서만 뜨도록 문턱을 둔다.
    property bool skyBusySlow: false
    Timer {
        id: skyBusyDelay
        interval: 350
        onTriggered: win.skyBusySlow = true
    }
    Connections {
        target: controller
        function onSkyBusyChanged() {
            if (controller.skyBusy) skyBusyDelay.restart()
            else { skyBusyDelay.stop(); win.skyBusySlow = false }
        }
        // Face 탭을 열어둔 채 사진을 넘기면 탭 클릭이 없어 검출이 안 돈다 → 얼굴 줄이 빈 채로 남는다.
        // ⚠️imageChanged 는 프록시·얼굴 캐시가 준비되기 **전에** 발화해 requestFaces 가 즉시
        //   반환된다. 캐시 리셋 직후에 나오는 facesChanged 를 써야 한다.
        //   재귀 걱정 없음 — requestFaces 가 _face_scanned/_face_scanning 으로 자체 차단한다.
        function onFacesChanged() {
            if (win.maskTab === 1 && controller.imagePath !== ""
                    && controller.faceCount === 0 && !controller.faceScanning)
                controller.requestFaces()
        }
    }
    // 사이드카 복원으로 마스크 재생성 중 — 완료 시 오버레이 자동 표시 억제(로드 시 갑자기 적색 방지).
    property bool _maskRestore: false
    function toggleMaskKey(key, on) {
        var a = maskKeys.slice()
        // 기본 선택을 넣을지 판단은 **변경 전** 상태로 한다 — 얼굴 부위가 이미 하나라도
        // 켜져 있었다면 사용자가 정한 대상(명시 선택이든 '전체'든)이 있는 것이므로 건드리면 안 된다.
        var hadFacePart = false
        for (var p = 0; p < a.length; p++) if (win._isFacePart(a[p])) { hadFacePart = true; break }
        var i = a.indexOf(key)
        if (on && i < 0) a.push(key)
        else if (!on && i >= 0) a.splice(i, 1)
        // 얼굴 부위를 **처음** 켤 때만 기본 대상을 넣는다: 직전 선택(faceSelMemo — 마지막 부위를
        // 끌 때 백업해 둔 것) 우선, 없으면 가장 큰 얼굴 1명(faceKeys[0]). 기본 없이 두면
        // '선택 없음 = 전체'가 되어 배경 인물까지 딸려 들어간다. memo 는 현재 검출 목록에 있는
        // key 만 복원한다 — 사진이 바뀌면 좌표가 안 맞아 자연히 기본(가장 큰 얼굴)으로 떨어진다.
        // ⚠️hadFacePart 검사가 없으면 두 명을 고른 상태에서 부위를 하나 더 켜는 순간
        //   '선택 없음'으로 보여 한 명으로 접힌다.
        if (on && win._isFacePart(key) && !hadFacePart && !win._hasFaceSel(a)
                && controller.faceCount > 1 && controller.faceKeys.length > 0) {
            var all = controller.faceKeys
            var memo = win.layers[win.activeLayer].faceSelMemo || []
            var restored = 0
            for (var r = 0; r < memo.length; r++)
                if (all.indexOf(memo[r]) >= 0) { a.push(memo[r]); restored++ }
            if (restored === 0) a.push(all[0])
        }
        // 마지막 부위를 끄면 얼굴 선택 key 도 같이 버린다 — 남겨두면 keys 가 안 비어서
        // clearLayer 대신 setMaskClasses 가 불리고 배치 export 가 마스크를 기다린다.
        // 버리기 전에 faceSelMemo 에 백업 — 부위를 다시 켤 때 위에서 그대로 복원한다.
        // (명시 선택이 없던 '전체' 상태면 memo 를 건드리지 않는다 — 전체는 커밋 정규화로
        //  face@ 가 안 남는 상태라 구분할 실체가 없고, 기본 폴백이 종전과 같은 동작을 준다)
        if (!on) {
            var part = false
            for (var j = 0; j < a.length; j++) if (win._isFacePart(a[j])) { part = true; break }
            if (!part) {
                var b = [], drop = []
                for (var m = 0; m < a.length; m++)
                    if (win._isFaceSel(a[m])) drop.push(a[m])
                    else b.push(a[m])
                if (drop.length > 0) win.layers[win.activeLayer].faceSelMemo = drop
                a = b
            }
        }
        win._commitMaskKeys(a)     // 정렬 + 활성 레이어 반영 + 디바운스 재조합
    }
    // 얼굴 부위 전체 선택/해제 — 부위가 11개(Skin/Nose/Eyes/…/Neck)라 얼굴 전체를 잡으려면
    // 11번 눌러야 한다는 피드백. ⚠️`toggleMaskKey` 를 11번 부르지 않는다 — 호출마다 재조합
    // 디바운스가 돌고 '첫 부위' 기본 대상 로직이 매번 재평가된다. 키 목록을 한 번에 만들고
    // **한 번만** 커밋한다. 규칙(첫 부위의 기본 대상 / 마지막 부위의 memo 백업)은 toggleMaskKey
    // 와 같아야 한다 — 한쪽만 고치면 얼굴 선택이 어긋난다.
    function setAllFaceParts(on) {
        var groups = controller.faceGroups
        if (!groups || groups.length === 0) return
        var a = []
        for (var i = 0; i < win.maskKeys.length; i++)      // 부위 외(얼굴 선택·Scene 클래스)는 보존
            if (!win._isFacePart(win.maskKeys[i])) a.push(win.maskKeys[i])
        var hadFacePart = win.hasFacePart()                // ⚠️변경 **전** 상태로 판단
        if (on) {
            for (var g = 0; g < groups.length; g++) a.push(groups[g].key)
            // 부위를 처음 켤 때만 기본 대상: 직전 선택(faceSelMemo) → 없으면 가장 큰 얼굴 1명.
            if (!hadFacePart && !win._hasFaceSel(a)
                    && controller.faceCount > 1 && controller.faceKeys.length > 0) {
                var all = controller.faceKeys
                var memo = win.layers[win.activeLayer].faceSelMemo || []
                var restored = 0
                for (var r = 0; r < memo.length; r++)
                    if (all.indexOf(memo[r]) >= 0) { a.push(memo[r]); restored++ }
                if (restored === 0) a.push(all[0])
            }
        } else {
            // 부위가 하나도 안 남으므로 얼굴 선택 key 도 버린다(버리기 전에 memo 백업).
            var b = [], drop = []
            for (var m = 0; m < a.length; m++) {
                if (win._isFaceSel(a[m])) drop.push(a[m])
                else b.push(a[m])
            }
            if (drop.length > 0) win.layers[win.activeLayer].faceSelMemo = drop
            a = b
        }
        win._commitMaskKeys(a)
    }
    // 체크박스 토글 코얼레싱 — 마지막 토글 후 잠깐 뒤 한 번만 세그/재조합 실행(스레드 폭증 방지).
    Timer {
        id: maskApplyTimer
        interval: 220
        onTriggered: controller.setMaskClasses(win.activeLayer, win.maskKeys)
    }
    // 활성 레이어 값 → 슬라이더/체크박스 로드(레이어 전환·사이드카 복원). _loadingLayer 로 저장 억제.
    function loadActiveToSliders() {
        win._loadingLayer = true
        var L = win.layers[win.activeLayer]
        for (var i = 0; i < win.skyAdjustKeys.length; i++) { var k = win.skyAdjustKeys[i]; win._skySlider(k).value = L[k] }
        skyInvertCheck.checked = L.invert
        win.maskKeys = L.keys.slice()
        win._syncDepthFromKeys()          // keys 안의 depth@ → 미러
        depthNearSlider.value = win.depthNear
        depthFarSlider.value = win.depthFar
        depthFeatherSlider.value = win.depthFeather
        win._loadingLayer = false
    }
    // 슬라이더/invert → 활성 레이어 저장(+ 셰이더 유니폼 notify). 슬라이더 워처(skyLayerWatch)가 호출.
    function saveActiveFromSliders() {
        if (win._loadingLayer) return
        var L = win.layers[win.activeLayer]
        for (var i = 0; i < win.skyAdjustKeys.length; i++) { var k = win.skyAdjustKeys[i]; L[k] = win._skySlider(k).value }
        L.invert = skyInvertCheck.checked
        L.keys = win.maskKeys.slice()
        win.layers = win.layers.slice()
    }
    // 대기 중인 마스크 커밋(체크박스 220ms 디바운스 / 깊이 60ms 스로틀)을 **현재 레이어로** 즉시
    // 발사. 레이어 전환 전에 안 하면 타이머가 새 레이어의 keys 로 발화해(no-op) 옛 레이어의
    // 마지막 변경이 컨트롤러에 영영 안 간다 — 사이드카(신값)와 프리뷰/export 마스크(구값)가
    // 이미지 재로드 전까지 조용히 어긋난다. setMaskClasses 는 동일 keys 면 no-op 이라 안전.
    function _flushMaskTimers() {
        if (maskApplyTimer.running || depthTrailTimer.running) {
            maskApplyTimer.stop(); depthTrailTimer.stop()
            controller.setMaskClasses(win.activeLayer, win.maskKeys)
        }
    }
    function selectLayer(i) {           // 레이어 전환: 현재값 저장 → 활성 변경 → 새 레이어 로드
        win._flushMaskTimers()          // 옛 레이어의 대기 중 커밋을 먼저 배달
        win.saveActiveFromSliders()     // showSkyMask 는 유지 → 오버레이가 새 활성 레이어 마스크를 따라감
        win.activeLayer = i
        win.loadActiveToSliders()
    }
    // 셰이더 유니폼용 레이어 vec4 (A=exp/hi/sh/dehaze, B=temp/tint/sat/contrast, C=texture/clarity/invert/hasMask)
    function _layerA(i) { var L = win.layers[i]; return Qt.vector4d(L.skyExp, L.skyHi, L.skyShadows, L.skyDehaze) }
    function _layerB(i) { var L = win.layers[i]; return Qt.vector4d(L.skyTemp, L.skyTint, L.skySat, L.skyContrast) }
    function _layerC(i) { var L = win.layers[i]
        return Qt.vector4d(L.skyTexture, L.skyClarity, L.invert ? 1.0 : 0.0,
                           ((i < win.layerCount && controller.layerHasMask[i]) ? 1.0 : 0.0)) }
    property vector4d skyA0: win._layerA(0); property vector4d skyB0: win._layerB(0); property vector4d skyC0: win._layerC(0)
    property vector4d skyA1: win._layerA(1); property vector4d skyB1: win._layerB(1); property vector4d skyC1: win._layerC(1)
    property vector4d skyA2: win._layerA(2); property vector4d skyB2: win._layerB(2); property vector4d skyC2: win._layerC(2)
    property vector4d skyA3: win._layerA(3); property vector4d skyB3: win._layerB(3); property vector4d skyC3: win._layerC(3)
    property vector4d skyA4: win._layerA(4); property vector4d skyB4: win._layerB(4); property vector4d skyC4: win._layerC(4)

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
        // 조정 슬라이더는 드래그 중 빨간 오버레이를 끈다(보정 결과를 봐야 하므로). 반대로 Depth
        // 범위 슬라이더는 **무엇이 선택되는지**가 목적이라 오버레이를 켜둔 채 움직여야 한다.
        property bool keepOverlay: false
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
            onMoved: {                                   // 드래그 → 외부 value 동기 + 오버레이 끔
                skyRoot.value = value
                if (!skyRoot.keepOverlay) skyRoot.host.showSkyMask = false
            }
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
    // 저장/export 페이로드 — 레이어 3개(각 keys + invert + 10 조정값). render_full 은 keys 무시(마스크는 sky_masks).
    // win.layers 는 슬라이더 워처(skyLayerWatch)가 활성 레이어를 항상 동기화하므로 그대로 읽는다.
    function skyEditParams() {
        var out = []
        for (var i = 0; i < win.layerCount; i++) {   // 존재하는 레이어만 저장 → 재로드 시 개수 복원
            var L = win.layers[i]
            var o = { "keys": (L.keys || []).slice(), "skyInvert": L.invert,
                      "strokes": (L.strokes || []).slice() }
            for (var j = 0; j < win.skyAdjustKeys.length; j++) { var k = win.skyAdjustKeys[j]; o[k] = L[k] }
            out.push(o)
        }
        return { "maskLayers": out }
    }
    // skyContrast 는 곱셈자라 중립=1.0(전역 Contrast 와 동일), 나머지는 0.0.
    function _skyDefault(k) { return k === "skyContrast" ? 1.0 : 0.0 }
    // 획 목록 tail-diff: b == a 에서 마지막 1개 뺀 것 → "pop", a == b 에서 마지막 1개 뺀 것 → "push".
    // 전역 undo/redo 의 획 한 개 변경을 감지해 워커 리플레이 대신 즉각 경로(popStroke/addStroke)로.
    function _strokeTailDiff(a, b) {
        if (a.length === b.length + 1
            && JSON.stringify(a.slice(0, b.length)) === JSON.stringify(b)) return "pop"
        if (b.length === a.length + 1
            && JSON.stringify(b.slice(0, a.length)) === JSON.stringify(a)) return "push"
        return null
    }
    // 복원: 레이어별 조정값 + 선택 클래스. 마스크는 클래스로부터 재생성. 구 평면 스키마는 레이어0 매핑(하위호환).
    // fastMasks=true(undo/redo 한정): 획 tail-diff 즉각 경로 허용. ⚠️fresh 파일 로드 복원은
    // prevLayers 가 **이전 사진** 것이라 절대 fast 금지(같아 보여도 컨트롤러는 초기화 상태).
    function applySkyEdits(p, fastMasks) {
        var prevLayers = win.layers          // 획 tail-diff 비교용(교체 전 상태)
        var prevCount = win.layerCount
        var ml = win._ev(p, "maskLayers", null)
        if (!ml) {                          // 하위호환: 구 평면(maskKeys + sky*) → 레이어 0
            var flat = { keys: (win._ev(p, "maskKeys", []) || []).slice(), invert: win._ev(p, "skyInvert", false) }
            for (var j = 0; j < win.skyAdjustKeys.length; j++) { var kf = win.skyAdjustKeys[j]; flat[kf] = win._ev(p, kf, win._skyDefault(kf)) }
            ml = [flat]
        }
        var newLayers = [win._newLayer(), win._newLayer(), win._newLayer(), win._newLayer(), win._newLayer()]
        var cnt = Math.max(1, Math.min(win.maxLayers, ml.length))
        for (var i = 0; i < cnt; i++) {
            var src = ml[i]; var L = newLayers[i]
            L.keys = (win._ev(src, "keys", []) || []).slice()
            L.invert = win._ev(src, "skyInvert", false)
            L.strokes = (win._ev(src, "strokes", []) || []).slice()
            for (var m = 0; m < win.skyAdjustKeys.length; m++) { var kk = win.skyAdjustKeys[m]; L[kk] = win._ev(src, kk, win._skyDefault(kk)) }
        }
        win._loadingLayer = true
        win.layers = newLayers
        win.layerCount = cnt
        win.activeLayer = 0
        // 오버레이는 undo/redo(fastMasks)에서는 현재 상태 유지 — 켠 채로 획을 되돌리는
        // 흐름이 끊기지 않게(사용자 요청). 새 파일 복원/붙여넣기만 초기화.
        if (fastMasks !== true) win.showSkyMask = false
        win._loadingLayer = false
        win.loadActiveToSliders()           // 레이어0 값을 슬라이더/체크박스로
        for (var q = 0; q < win.maxLayers; q++) {   // 각 레이어 마스크 재생성(클래스+획으로부터)/비활성 슬롯 해제
            var hasContent = q < cnt
                && (newLayers[q].keys.length > 0 || newLayers[q].strokes.length > 0)
            if (!hasContent) { controller.clearLayer(q); continue }
            // 전역 undo/redo 즉각 경로: keys 동일 + 획만 꼬리 1개 차이(pop/push)면 컨트롤러의
            // 패치/증분 경로 사용(워커 리플레이·dim 없음). keys 동일 + 획도 동일이면 재생성
            // 자체를 생략(마스크 무관 편집의 undo 가 마스크를 건드리지 않게).
            var prev = (fastMasks === true && q < prevCount) ? prevLayers[q] : null
            if (prev && JSON.stringify((prev.keys || [])) === JSON.stringify(newLayers[q].keys)) {
                var od = prev.strokes || []
                var nd = newLayers[q].strokes
                if (JSON.stringify(od) === JSON.stringify(nd)) continue
                var td = win._strokeTailDiff(od, nd)
                if (td === "pop") { controller.popStroke(q); continue }
                if (td === "push") { controller.addStroke(q, nd[nd.length - 1]); continue }
            }
            controller.setStrokes(q, newLayers[q].strokes)   // 획 먼저(워커 스폰 전 동기)
            win._maskRestore = true
            controller.setMaskClasses(q, newLayers[q].keys)
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
            "mistAmt": mistAmtSlider.value, "mistChar": mistCharSlider.value,
            "mistRadius": mistRadiusSlider.value, "mistHi": mistHiSlider.value,
            "mistColor": mistColorSlider.value,
            "vignette": vignetteSlider.value, "grainAmt": grainSlider.value, "grainSize": grainSizeSlider.value,
            "grainRough": grainRoughSlider.value, "grainColor": grainColorSlider.value,
            "grainShape": grainShapeCheck.checked,
            "sharpenAmt": sharpAmtSlider.value, "sharpenRadius": sharpRadiusSlider.value,
            "sharpenDetail": sharpDetailSlider.value, "sharpenMask": sharpMaskSlider.value,
            "lumaNR": lumaNrSlider.value, "colorNR": colorNrSlider.value, "aiNr": aiNrCheck.checked,
            "lensCorrection": lensCheck.checked, "autoExposure": autoExpCheck.checked,
            "dateStamp": win.dateStamp, "stampText": stampField.text,
            "stampStyle": controller.stampFont, "stampSize": controller.stampSize,
            "stampMargin": controller.stampMargin, "stampColor": controller.stampColor,
            "stampGlow": controller.stampGlow, "stampSpread": controller.stampSpread,
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
    // 룩 키의 공장 기본값 — presets.LOOK_DEFAULTS 단일 진실원. applyEdits 의 폴백이 이것을
    // 쓰고, 룩 지문도 없는 키를 같은 값으로 채운다(배지가 정직해지는 근거 — presets.py 주석).
    // ⚠️여기에 리터럴을 다시 쓰지 말 것. 키를 추가하면 `python presets.py` 가 누락을 잡아준다.
    function lookDef(k) { return controller.lookDefaults[k] }

    function _ev(p, k, d) { return p[k] !== undefined ? p[k] : d }

    // 저장된 편집을 컨트롤에 복원. 반드시 _applying 가드 안에서 호출(자동저장/WB 재디코딩 방지).
    // fastMasks: applySkyEdits 의 획 즉각 경로 허용(undo/redo 전용 — applySnapshot 만 true).
    function applyEdits(p, fastMasks) {
        expSlider.value = _ev(p, "exposure", 0.0); conSlider.value = _ev(p, "contrast", win.lookDef("contrast"))
        hiSlider.value = _ev(p, "highlights", win.lookDef("highlights")); shSlider.value = _ev(p, "shadows", win.lookDef("shadows"))
        whSlider.value = _ev(p, "whites", win.lookDef("whites")); blSlider.value = _ev(p, "blacks", win.lookDef("blacks"))
        tempSlider.value = _ev(p, "temp", controller.asShotKelvin)
        tintSlider.value = _ev(p, "tint", controller.asShotTint)
        // 필름시뮬 복원: simKey(문자열) 우선 → 현재 목록에서 인덱스 역추적(없으면 None). 구버전은 simIndex.
        // ⚠️여기 `""` 는 **센티널**(미지정 → 아래 simIndex 폴백)이지 룩 값이 아니다. 그래서
        //   lookDefaults 를 쓰지 않는다 — 표의 값("identity")을 넣으면 구버전 사이드카의
        //   simIndex 폴백 분기가 죽는다. 표는 룩 지문 채우기 전용으로 "identity" 를 갖는다.
        var _sk = _ev(p, "simKey", "")
        var _si
        if (_sk !== "") { _si = win.simKeys.indexOf(_sk); if (_si < 0) _si = 0 }   // 목록에 없는 LUT → None
        else { _si = _ev(p, "simIndex", 0); if (_si < 0 || _si >= win.simKeys.length) _si = 0 }
        simCombo.currentIndex = _si
        simStrengthSlider.value = _ev(p, "simStrength", win.lookDef("simStrength"))
        texSlider.value = _ev(p, "texture", win.lookDef("texture")); claritySlider.value = _ev(p, "clarity", win.lookDef("clarity"))
        dehazeSlider.value = _ev(p, "dehaze", win.lookDef("dehaze"))
        vibSlider.value = _ev(p, "vibrance", win.lookDef("vibrance")); satSlider.value = _ev(p, "saturation", win.lookDef("saturation"))
        win.hslH = _ev(p, "hslH", win.lookDef("hslH")).slice()
        win.hslS = _ev(p, "hslS", win.lookDef("hslS")).slice()
        win.hslL = _ev(p, "hslL", win.lookDef("hslL")).slice()
        cgShHueSlider.value = _ev(p, "cgShadowHue", win.lookDef("cgShadowHue")); cgShSatSlider.value = _ev(p, "cgShadowSat", win.lookDef("cgShadowSat"))
        cgMidHueSlider.value = _ev(p, "cgMidHue", win.lookDef("cgMidHue")); cgMidSatSlider.value = _ev(p, "cgMidSat", win.lookDef("cgMidSat"))
        cgHiHueSlider.value = _ev(p, "cgHighHue", win.lookDef("cgHighHue")); cgHiSatSlider.value = _ev(p, "cgHighSat", win.lookDef("cgHighSat"))
        cgBalanceSlider.value = _ev(p, "cgBalance", win.lookDef("cgBalance"))
        hslHueSlider.value = win.hslH[win.hslBand]
        hslSatSlider.value = win.hslS[win.hslBand]
        hslLumSlider.value = win.hslL[win.hslBand]
        vignetteSlider.value = _ev(p, "vignette", win.lookDef("vignette"))
        // 미스트 — 폴백은 공장 기본값(이 키가 없던 사이드카는 미스트 없음으로 열려야 한다).
        // ⚠️Amount 는 **맨 나중에** 밀고, 먼저 requestMistField 로 키를 맞춘다 — 먼저 밀면
        //   onValueChanged → setMistAmount 가 아직 **이전 사진의 Radius/Highlight** 가 남은 상태에서
        //   워커를 시작시켜(프록시 3× 가우시안 ~0.5s) 곰바로 seq 불일치로 버려진다.
        mistCharSlider.value = _ev(p, "mistChar", win.lookDef("mistChar"))
        mistRadiusSlider.value = _ev(p, "mistRadius", win.lookDef("mistRadius")); mistHiSlider.value = _ev(p, "mistHi", win.lookDef("mistHi"))
        // ⚠️한때 폴백만 0.0(공장값 0.5) 으로 뒀는데, 그러면 룩 지문이 성립하지 않는다 —
        //   **한 키에 기본값은 하나**여야 한다(presets.LOOK_DEFAULTS 주석). 0.5 로 통일했고,
        //   그 대가는 이 키가 없던 시절 사이드카가 Color 0.5 로 열린다는 것뿐이다.
        mistColorSlider.value = _ev(p, "mistColor", win.lookDef("mistColor"))
        controller.requestMistField(mistRadiusSlider.value, mistHiSlider.value,
                                   _ev(p, "mistAmt", win.lookDef("mistAmt")))   // 복원은 즉시 1회
        mistAmtSlider.value = _ev(p, "mistAmt", win.lookDef("mistAmt"))   // uniform 갱신(키는 위에서 맞췄다)
        grainSlider.value = _ev(p, "grainAmt", win.lookDef("grainAmt")); grainSizeSlider.value = _ev(p, "grainSize", win.lookDef("grainSize"))
        grainRoughSlider.value = _ev(p, "grainRough", win.lookDef("grainRough"))
        grainColorSlider.value = _ev(p, "grainColor", win.lookDef("grainColor"))
        grainShapeCheck.checked = _ev(p, "grainShape", win.lookDef("grainShape"))
        controller.setStampGrainSrc(grainSlider.value)   // 스탬프 그레인 연동(프리뷰)
        sharpAmtSlider.value = _ev(p, "sharpenAmt", win.lookDef("sharpenAmt")); sharpRadiusSlider.value = _ev(p, "sharpenRadius", win.lookDef("sharpenRadius"))
        sharpDetailSlider.value = _ev(p, "sharpenDetail", win.lookDef("sharpenDetail")); sharpMaskSlider.value = _ev(p, "sharpenMask", win.lookDef("sharpenMask"))
        lumaNrSlider.value = _ev(p, "lumaNR", 0.0); colorNrSlider.value = _ev(p, "colorNR", 0.0)
        // AI 디노이즈: 프로그램적 checked 변경은 onToggled 미발화 → 명시 전달.
        // 켜져 있으면 requestAiNr(비대화형) 경유 — GPU 면 즉시, CPU 폴백이면 세션 선택 정책
        // (미선택=1회 질문, no=자동 해제, yes=진행). 로드 직후엔 가이디드 베이스로 동작.
        aiNrCheck.checked = _ev(p, "aiNr", false)
        if (aiNrCheck.checked) win.requestAiNr(false)
        else controller.setAiNr(false)
        // ⚠️폴백은 **공장 기본값** — 사이드카가 있는 사진(=이 경로)의 룩은 내 기본값에
        //   영향받아선 안 된다. 아주 옛 사이드카(스탬프 키가 없던 시절)를 열었을 때
        //   스탬프가 켜지거나 폰트가 바뀌면 기존 사진의 룩이 변한다.
        win.dateStamp = _ev(p, "dateStamp", false)
        stampField.text = _ev(p, "stampText", controller.stampText)
        // 프로그램으로 text 를 바꾸면 onTextEdited 가 안 불리므로 직접 push(스탬프 렌더 갱신).
        controller.setStampText(stampField.text)
        controller.setStampFont(_ev(p, "stampStyle", win.lookDef("stampStyle")))
        var _sz = _ev(p, "stampSize", win.lookDef("stampSize"))
        if (typeof _sz === "string") _sz = ({S: 0.024, M: 0.032, L: 0.044})[_sz] || 0.032  // 구 사이드카 호환
        stampSizeSlider.value = _sz
        controller.setStampSize(_sz)
        var _mg = _ev(p, "stampMargin", win.lookDef("stampMargin"))
        stampMarginSlider.value = _mg; controller.setStampMargin(_mg)
        // 색/글로우도 공장 기본값 폴백 — 이 키가 없던 시절 사이드카가 예전 앰버 룩 그대로
        // 열려야 한다(date_stamp 가 기본 색·기본 영역에서 예전과 비트 동일하게 렌더한다).
        controller.setStampColor(_ev(p, "stampColor", win.lookDef("stampColor")))
        var _gl = _ev(p, "stampGlow", win.lookDef("stampGlow"));   stampGlowSlider.value = _gl; controller.setStampGlow(_gl)
        var _sp = _ev(p, "stampSpread", win.lookDef("stampSpread")); stampSpreadSlider.value = _sp; controller.setStampSpread(_sp)
        // 체크박스도 명시 대입(aiNrCheck 동일) — 사용자가 한 번이라도 클릭하면
        // `checked: controller.lensCorrection` 바인딩이 파괴되어, 이후 사이드카 복원이
        // 박스에 반영되지 않고 낡은 값이 자동저장으로 역전파되던 버그 방지.
        lensCheck.checked = _ev(p, "lensCorrection", true)
        autoExpCheck.checked = _ev(p, "autoExposure", true)
        controller.setLensCorrection(lensCheck.checked)
        controller.setAutoExposure(autoExpCheck.checked)   // 박스 대입만으론 슬롯이 안 불린다
        // ⚠️여기 `null` 도 **센티널**(=resetAll() 하라)이라 lookDefaults 를 쓰지 않는다.
        //   표는 그 결과인 identity 제어점을 갖는다(룩 지문 채우기 전용).
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
        win.applySkyEdits(p, fastMasks === true)   // 마스킹 복원 — fast 는 undo/redo 한정
    }

    // 하늘(로컬) 조정 초기화 — 슬라이더 + 마스크 + 오버레이. 새 파일 로드/Reset 에서 호출.
    function resetSky() {           // 전 레이어 초기화
        win._loadingLayer = true
        win.layers = [win._newLayer(), win._newLayer(), win._newLayer(), win._newLayer(), win._newLayer()]
        win.layerCount = 1
        win.activeLayer = 0
        skyExpSlider.value = 0.0; skyTempSlider.value = 0.0; skyTintSlider.value = 0.0
        skySatSlider.value = 0.0; skyHiSlider.value = 0.0; skyShadowsSlider.value = 0.0
        skyTextureSlider.value = 0.0; skyClaritySlider.value = 0.0; skyDehazeSlider.value = 0.0
        skyContrastSlider.value = 1.0
        skyInvertCheck.checked = false
        win.maskKeys = []
        win.depthOn = false                      // 깊이 범위도 기본값으로(미러 + 슬라이더)
        win.depthNear = 0.5; win.depthFar = 1.0; win.depthFeather = 0.10
        depthNearSlider.value = win.depthNear
        depthFarSlider.value = win.depthFar
        depthFeatherSlider.value = win.depthFeather
        win.showSkyMask = false
        win._loadingLayer = false
        controller.clearSky()
    }
    // 마스킹 슬라이더/invert 변경 → 활성 레이어 저장(셰이더 라이브 갱신). _loadingLayer 시 무시.
    Item {
        visible: false
        property string skySig: JSON.stringify([skyExpSlider.value, skyContrastSlider.value, skyTempSlider.value,
            skyTintSlider.value, skyHiSlider.value, skyShadowsSlider.value, skyTextureSlider.value,
            skyClaritySlider.value, skyDehazeSlider.value, skySatSlider.value, skyInvertCheck.checked])
        onSkySigChanged: win.saveActiveFromSliders()
    }

    // 전체 초기화(편집 + 지오메트리). 수동 Reset 버튼 & 저장본 없는 파일 로드에서 호출.
    // factoryStamp=true 면 스탬프 설정을 **공장 기본값**으로 되돌린다(Reset 버튼).
    // 생략하면 '내 기본값' — 사이드카 없는 새 사진의 로드 경로가 그쪽이다(아래 주석).
    function resetAllEdits(factoryStamp) {
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
        mistAmtSlider.value = 0.0; mistCharSlider.value = 0.0
        mistRadiusSlider.value = 1.0; mistHiSlider.value = 0.8; mistColorSlider.value = 0.5
        controller.requestMistField(1.0, 0.8, 0.0)
        vignetteSlider.value = 0.0; grainSlider.value = 0.0; grainSizeSlider.value = 0.5
        grainRoughSlider.value = 0.1; grainColorSlider.value = 0.3
        grainShapeCheck.checked = false
        controller.setStampGrainSrc(0.0)
        tempSlider.value = controller.asShotKelvin; tintSlider.value = controller.asShotTint
        simCombo.currentIndex = 0; simStrengthSlider.value = 1.0
        // 날짜 스탬프/렌즈 보정도 초기화 — 누락 시 이전 사진의 상태가 무편집 사진으로
        // 누수되고(editParams 는 저장하는데 reset 은 안 지움), Reset 버튼으로도 안 지워졌음.
        // 스탬프는 '내 기본값'으로 되돌린다(공장 기본값 아님). ⚠️이 함수는 Reset 버튼과
        // 이 함수는 **사이드카 없는 새 사진의 로드 경로**(onEditsReady 의 else)를 겸한다 —
        // 거기서는 '내 기본값'이 맞다(연속 작업에서 매번 다시 잡지 않는 것이 이 기능의 목적).
        // ⚠️**Reset 버튼은 공장 기본값**이다(factoryStamp=true) — 스탬프 슬라이더는 놓을 때마다
        //   그 값을 내 기본값으로 기억하므로, Reset 도 내 기본값이면 **방금 만진 값이 그대로
        //   돌아와 Reset 이 무동작으로 보인다**(사용자 보고). 그러면 슬라이더 더블클릭·
        //   applyEdits 의 `_ev` 폴백(둘 다 공장값)과도 어긋난다 — 이제 셋이 같다.
        // ⚠️`dateStamp`(켜짐 여부)만 내 기본값을 따른다 — Reset 이 각인을 통째로 없애는 것은
        //   보고된 문제가 아니고, 새 사진을 열었을 때와 켜짐 상태가 달라지면 더 혼란스럽다.
        var _sd = function (k) { return factoryStamp === true ? win.lookDef(k) : win.stampDef(k) }
        win.dateStamp = win.stampDef("stampOn")
        stampField.text = controller.stampText
        controller.setStampText(stampField.text)
        // ⚠️**폰트만은 Reset 에서 건드리지 않는다**(사용자 요청) — 각인 폰트는 '이 사진의
        //   편집'이라기보다 쓰는 사람의 취향이라, 리셋할 때마다 공장 폰트로 튀면 매번 다시
        //   고르게 된다. 사이드카 없는 새 사진의 로드 경로에서는 지금까지처럼 내 기본값을 쓴다.
        if (factoryStamp !== true)
            controller.setStampFont(_sd("stampStyle"))
        stampSizeSlider.value = _sd("stampSize")
        controller.setStampSize(stampSizeSlider.value)
        stampMarginSlider.value = _sd("stampMargin")
        controller.setStampMargin(stampMarginSlider.value)
        controller.setStampColor(_sd("stampColor"))
        stampGlowSlider.value = _sd("stampGlow")
        controller.setStampGlow(stampGlowSlider.value)
        stampSpreadSlider.value = _sd("stampSpread")
        controller.setStampSpread(stampSpreadSlider.value)
        lensCheck.checked = true
        controller.setLensCorrection(true)
        autoExpCheck.checked = true
        controller.setAutoExposure(true)
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
        win.resetAllEdits(true)        // Reset 버튼 = 공장 기본값(위 주석)
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

    // ===== 레시피 프리셋 =====
    property var presetItems: []          // controller.presetList() 캐시(배지 그리드 모델)
    // 배지 재평가 트리거. 지문은 항상 `_PRESET_KEYS` **전체**로 계산된다(main.lookHash 주석)
    // — 레시피마다 비교 집합을 좁히던 예전 방식은 버렸으므로, 커밋당 지문은 **한 번**이면 된다.
    // 여기서는 '언제 다시 계산할지'만 알린다.
    property int lookRev: 0
    // 편집이 커밋될 때만 계산한다(histPush/histReset/applySnapshot + refreshPresets).
    // ⚠️`lookRev` 카운터만 올리고 delegate 에서 badgeOn 을 호출하는 방식이었는데 **스로틀이
    //   전혀 듣지 않았다**(실측: 슬라이더 20프레임에 lookHash 476회). QML 은 바인딩이 호출한
    //   함수 내부에서 읽은 프로퍼티까지 의존성으로 잡으므로, badgeOn -> editParams() 가
    //   모든 슬라이더·브러시 획을 읽어 배지마다 매 프레임 재평가됐다. 그래서 **여기서 미리
    //   계산해 배열에 담고** delegate 는 그 배열만 읽는다(프레임당 0회).
    property var recipeOn: []
    function refreshLookHash() {
        win.lookRev = win.lookRev + 1
        var out = []
        if (controller.imagePath !== "") {
            var ep = win.editParams()          // 한 번만 만든다
            var h = controller.lookHash(ep)    // ⚠️루프 밖으로 — 레시피와 무관한 값이다
            for (var i = 0; i < win.presetItems.length; i++)
                out.push(h === win.presetItems[i].lookHash)
        }
        win.recipeOn = out
    }
    // 배지 활성 = **지금 룩이 이 레시피와 같은가**. 그것뿐이다.
    // ⚠️예전에 '이 레시피에 기반했으나 수정됨'(흐린 앰버) 상태를 사이드카 계보로 표시했다가
    //   **제거했다 — 되살리지 말 것.** 배지가 '보이지 않는 이력'의 함수가 되면 **룩이 완전히
    //   같은 두 사진이 서로 다른 배지를 보인다**(레시피로 만든 쪽 vs 붙여넣기로 만든 쪽).
    //   화면에서 구분할 근거가 없는 차이라 사용자에게 설명할 방법이 없었다(사용자 보고:
    //   "경우에 따라 활성화가 되고 안되고의 차이가 혼란을 준다"). 배지는 눈에 보이는 것만의
    //   함수여야 한다 — 그러면 규칙이 한 줄로 끝나고 예외가 없다.
    // ── 레시피 순서 드래그 ──
    // 행 높이가 고정(44+5)이라 목표 인덱스는 나눗셈으로 나온다. 드래그 중에는 레이아웃을
    // 건드리지 않고 ①집은 행만 Translate 로 따라오게 하고 ②들어갈 자리에 앰버 선을 그린다.
    // (ColumnLayout 안에서 y 를 바꾸면 레이아웃과 싸우므로 transform 을 쓴다.)
    property int recipeDragIdx: -1       // 집고 있는 행(-1=없음)
    property int recipeDropIdx: -1       // 들어갈 **최종 인덱스**(0..count-1)
    property real recipeDragDy: 0
    property int recipeHoverIdx: -1       // 호버 중인 행(입력 레이어가 하나라 여기서 관리)
    readonly property int recipeRowStride: 49    // 행 44 + spacing 5
    // ⚠️recipeDropIdx 는 **최종 인덱스**(0..count-1)다. 예전엔 '삽입 간격(gap)'으로 두고
    //   내려갈 때 -1 보정을 했는데, 그러면 **아래로 한 칸 옮기려면 1.5행을 끌어야** 했고
    //   위로는 0.5행이면 됐다(실측: dy=+49 에서 이동 없음). 최종 인덱스로 두면 대칭이다.
    function recipeDrop() {
        var from = win.recipeDragIdx, to = win.recipeDropIdx
        win.recipeDragIdx = -1; win.recipeDropIdx = -1; win.recipeDragDy = 0
        if (from < 0 || to < 0 || to === from) return
        var a = win.presetItems.slice()
        a.splice(to, 0, a.splice(from, 1)[0])
        win.presetItems = a                        // 화면은 즉시 새 순서
        // ⚠️recipeOn 은 **위치 대응 배열**이라 같이 옴기지 않으면 앞버 테두리가 다른 레시피에
        // 남는다(다음 편집 커밋 전까지). 다시 계산할 것이 없으므로 같은 순서로 섮어만 준다.
        if (win.recipeOn.length === a.length) {
            var b = win.recipeOn.slice()
            b.splice(to, 0, b.splice(from, 1)[0])
            win.recipeOn = b
        }
        var keys = []
        for (var i = 0; i < a.length; i++) keys.push(a[i].orderKey)
        controller.setPresetOrder(keys)             // 저장(prefs.json)
    }
    // 단발 조회용(툴팁·테스트). ⚠️**delegate 바인딩에서 부르지 말 것** — 위 주석의 이유로
    //   매 프레임 재평가된다. 화면 표시는 win.recipeOn[index] 를 읽는다.
    function badgeOn(item) {
        if (controller.imagePath === "") return false
        return controller.lookHash(win.editParams()) === item.lookHash
    }
    // 우클릭 컨텍스트 대상(수정/내보내기/삭제 공용)
    property string _presetCtxFile: ""
    property string _presetCtxName: ""
    property string _presetCtxColor: ""
    property string _presetCtxDesc: ""
    property var _presetCtxSrc: ({})           // 그 레시피에 저장된 출처(카메라/렌즈 수정용)
    property string _presetConfirmMode: ""     // "delete" | "update" — 확인 대화상자 공용
    function refreshPresets() {
        win.presetItems = controller.presetList()
        win.refreshLookHash()          // 목록이 바뀌면 배지 판정도 다시(항목 수가 달라진다)
    }

    // 배너 문구. "" = 배너 없음. presetNoticeWarn=true 면 앰버 경고, false 면 회색 정보.
    // ⚠️'비교 불가(회색)'와 '다른 기종(앰버)'이 똑같이 보이면 둘 다 안 읽힌다 → 시각 비중을 나눈다.
    property string presetNotice: ""
    property bool presetNoticeWarn: false
    function clearPresetNotice() {
        win.presetNotice = ""; win.presetNoticeWarn = false
    }

    // 프리셋의 출처와 현재 사진을 비교해 배너 문구를 만든다.
    // ⚠️초점거리는 **비교하지 않는다** — 줌 렌즈면 같은 바디·같은 렌즈의 두 컷도 초점거리가 달라
    //   매번 불일치 배너가 뜨고, 그러면 아무도 배너를 읽지 않는다. 표시용 문맥으로만 쓴다.
    //   렌즈는 **양쪽에 다 있을 때만** 비교한다(대개 비어 있다 — exif_info 주석 참조).
    function presetMessage(d, missingSim) {
        var src = d.source || {}
        var cur = controller.presetSource()
        function ident(o) {
            var a = []
            if (o.camera) a.push(o.camera)
            if (o.lens) a.push(o.lens)
            else if (o.focalLength) a.push(o.focalLength)
            return a.join(" · ")
        }
        var made = ident(src), mine = ident(cur)
        var extra = missingSim !== ""
            ? " Film simulation '" + missingSim + "' isn't installed — not applied." : ""
        if (!src.camera || !cur.camera) {
            // 한쪽에 촬영정보가 없으면 **불일치라고 단정하지 않는다.** 우리가 export 한 파일은
            // EXIF 가 아예 없어(Qt 가 안 씀) 이 경로가 정상 흐름에서 자주 걸린다.
            var head = !cur.camera
                ? "This photo has no camera info (exported or edited files usually lose EXIF), so it can't be checked against this recipe."
                : "This recipe has no camera info recorded, so it can't be checked against this photo."
            return { warn: false, text: head + (made ? " The recipe was made on " + made + "." : "") + extra }
        }
        var lensDiff = src.lens && cur.lens && src.lens !== cur.lens
        if (src.camera !== cur.camera || lensDiff) {
            return { warn: true, text: "Made on " + made
                + (d.appVersion ? " · v" + d.appVersion : "")
                + ". This photo: " + mine
                + ". Colour response, noise and lens rendering differ — treat this as a starting point, not a copy."
                + extra }
        }
        return { warn: missingSim !== "", text: extra.replace(/^ /, "") }
    }

    // 프리셋 파일 1개를 읽어 적용. 검증은 Python(loadPreset)에서 이미 끝나 있다.
    function applyPresetFile(file, label) {
        if (controller.imagePath === "") return
        var d = controller.loadPreset(file)
        if (d.error !== "") {
            win.presetNotice = "This preset could not be loaded: " + d.error
            win.presetNoticeWarn = true
            return
        }
        // ⚠️없는 필름시뮬은 applyEdits 가 **조용히** None 으로 떨어뜨린다(:796) — 배포본에서 ARR
        //   흑백 LUT 을 뺐으므로 실제로 발생하고, 그러면 룩의 대부분을 잃는다. 적용 전에 잡아 알린다.
        var sk = d.edits["simKey"]
        var missing = (sk !== undefined && sk !== "" && win.simKeys.indexOf(sk) < 0) ? sk : ""
        win.applyPresetEdits(d.edits)
        var msg = win.presetMessage(d, missing)
        win.presetNotice = msg.text
        win.presetNoticeWarn = msg.warn
    }

    // 프리셋의 '룩'을 현재 사진에 적용. 붙여넣기(pasteEdits)와 같은 커밋 경로를 쓰되 **3단**이다.
    // ⚠️① 프리셋 dict 를 applyEdits 에 그대로 넘기면 안 된다 — applyEdits 는 무조건
    //    applySkyEdits 를 부르고 maskLayers 가 없으면 모든 레이어를 clearLayer 한다.
    //    즉 대상 사진의 **마스크가 삭제**되고, temp/tint 누락은 as-shot 리셋, 크롭·기하·스탬프
    //    텍스트도 초기화된다. 그래서 현재 편집값 위에 병합해야 '건드리지 않음'이 된다.
    // ⚠️② 그런데 병합만 하면 **이전 프리셋의 룩이 남는다**(A 적용 후 B 적용 시 B 에 없는 키는
    //    A 값 유지). 붙여넣기는 클립보드가 항상 전체 룩을 담아 괜찮았을 뿐이다. → 프리셋이
    //    소유하는 키를 먼저 지워 기본값으로 되돌린 뒤 덮어쓴다. 지우는 목록은 **정확히
    //    presetKeys 여야 한다** — 프리셋이 소유하지 않은 키를 지우면 그 키가 기본값으로 강제된다
    //    (예: lensCorrection 은 기본이 true 라 조용히 켜지면서 풀 재디코드까지 유발).
    function applyPresetEdits(edits) {
        if (controller.imagePath === "") return
        var p = win.editParams()                       // ① 대상 사진의 현재 전체 상태
        var K = controller.presetKeys
        for (var i = 0; i < K.length; i++) delete p[K[i]]   // ② 프리셋 소유 키 → 기본값
        delete p["simIndex"]                           // ★ 안 지우면 이전 필름시뮬이 부활(:796)
        for (var k in edits) p[k] = edits[k]           // ③ 프리셋 값
        win._applying = true
        // ⚠️try/finally 필수 — 예외가 나면 _applying 이 영구 true 로 남아 그 세션의 자동저장과
        //   undo 가 조용히 죽는다(scheduleSave/commitEditSnapshot 이 early-return).
        //   프리셋은 applyEdits 에 들어오는 첫 '앱 외부에서 편집·공유되는' 입력이다.
        try { win.applyEdits(p) } finally { win._applying = false }
        controller.setWb(tempSlider.value, tintSlider.value)   // _applying 중 막힌 커밋 직접 반영
        controller.setCurve(curveEditor.allLuts())
        controller.saveEdits(win.editParams())
        win.refreshHistogram()
        win.histPush(JSON.stringify(win.editParams()))   // undo 스텝 1개(프리셋 적용 되돌리기)
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
    // shift+클릭 연속 선택의 기준 행. 인덱스가 아니라 **경로**로 들고 있는다 — 폴더 이동·검색
    // 필터·좋아요만 보기로 explorerFiles 가 재구성되면 인덱스는 다른 파일을 가리키기 때문.
    property string batchAnchorPath: ""
    function batchToggle(path) {
        if (batchChecked[path]) delete batchChecked[path]
        else batchChecked[path] = true
        batchAnchorPath = path                 // 단독 클릭 = 다음 shift 범위의 기준
        batchCheckedRev++
    }
    // 기준 행 ~ 클릭 행 사이를 모두 체크(폴더는 건너뜀). 체크만 하고 해제는 안 하는 **가산**이라,
    // 기준을 그대로 둔 채 shift 를 다시 누르면 같은 시작점에서 범위를 넓혀 갈 수 있다.
    // (범위를 줄여도 이미 체크된 건 남는다 — 빼려면 그 행을 단독 클릭해 토글)
    function batchSelectRange(toIndex) {
        var files = win.explorerFiles
        var from = -1
        for (var i = 0; i < files.length; i++)
            if (files[i].path === win.batchAnchorPath) { from = i; break }
        if (from < 0) from = toIndex           // 기준이 현재 목록에 없으면(필터 등) 클릭 행만
        var a = Math.min(from, toIndex), b = Math.max(from, toIndex)
        for (var j = a; j <= b; j++) {
            var it = files[j]
            if (it && !it.isDir) win.batchChecked[it.path] = true
        }
        batchCheckedRev++
    }
    function batchClearChecked() { batchChecked = ({}); batchAnchorPath = ""; batchCheckedRev++ }

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
        controller.setKeepAwake(true)    // 배치 전 구간(로드/마스킹 갭 포함) 시스템 슬립 방지
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
        controller.setKeepAwake(false)
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
                // maskSettled: 워커가 끝났고 결과가 '마스크 없음'인 경우도 대기 종료 —
                // 얼굴 없는 사진에 Face 부위가 선택돼 있으면 hasSkyMask 가 영영 False 라
                // 이게 없으면 장당 20초 타임아웃을 그대로 기다린다.
                var maskPending = win.maskKeys.length > 0 &&
                                  !controller.hasSkyMask && !controller.maskSettled
                // GPU 엔진은 셰이더가 nrBase 텍스처를 그대로 쓰므로(CPU 처럼 직접 계산 안 함)
                // 준비 전에 grab 하면 그 장만 휘도 NR 이 조용히 빠진다 → 마스크와 같이 대기.
                var nrPending = win.useGpuExport && !controller.nrReady
                if (!controller.busy && !controller.skyBusy
                        && ((!maskPending && !nrPending) || waited > 20000)) {
                    var url = controller.batchExportUrl(
                        win.batchDestUrl, win.batchQueue[win.batchIndex], win.batchExt)
                    if (url === "") { win.batchFails++; win.batchIndex++; win.batchLoadNext(); return }
                    // 설정(Render 콤보 CPU/GPU + 16bit)을 단일 export 와 동일하게 따른다.
                    if (!win.startExport(url, win.exportParams())) {   // 슬롯 가드에 걸림(비정상) → 실패 처리
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

    // ===== Wallpaper (3분할 트립틱 배경화면) =====
    // 배치 export 와 동일한 이유(마스크 미영속·커브 평가기 QML 전용)로 슬롯 사진을 하나씩
    // 라이브 로드 → editsReady → wallParams() → wallpaperRenderPanel 로 렌더해 모은 뒤
    // wallpaperCompose 로 합성/저장. 트립틱은 3장 전부 필요하므로 한 장이라도 실패하면 전체 중단
    // (배치의 파일별 실패 허용과 다른 점).
    property var wallSlots: ["", "", ""]          // 슬롯별 경로("" = 비어있음), 좌/중/우
    property var wallOffsets: [0.0, 0.0, 0.0]     // 가로 크롭 오프셋(-1 왼쪽끝..+1 오른쪽끝)
    property int wallGap: 18                      // 패널 사이 검정 갭(px, 캔버스 기준)
    property int wallResIndex: 0
    // 레이아웃: 0=트립틱(3분할), 1=잡지 스프레드(메인 사진 풀블리드 + 타이포 칼럼)
    property int wallLayout: 0
    readonly property var wallLayoutKeys: ["triptych", "magazine"]
    property int wallTypeface: 0                  // 0=Serif, 1=Sans, 2=Serif(KR), 3=Sans(KR)
    readonly property var wallTypefaceKeys: ["serif", "sans", "serif_ko", "sans_ko"]
    property int wallMainSide: 1                  // 0=Left, 1=Right
    // 화면에서 보이는 좌→우 슬롯 순서(compose_magazine 과 동일 규칙). 트립틱은 슬롯 순서가
    // 곧 좌→우이고, 잡지는 메인 사진(가운데 슬롯)이 좌/우 끝에 놓이므로 순서가 달라진다.
    // 패널의 슬롯 카드도 이 순서로 나열해 번호(Frame 0N)와 위치가 어긋나지 않게 한다.
    readonly property var wallSlotOrder: win.wallLayout === 0 ? [0, 1, 2]
                                         : (win.wallMainSide === 0 ? [1, 0, 2] : [0, 2, 1])
    function wallFrameNo(slot) { return win.wallSlotOrder.indexOf(slot) + 1 }
    // 잡지 레이아웃 텍스트(사용자 입력) — controller 가 QSettings 에 영구 저장
    property string wallKicker: ""
    property string wallHeadline: ""
    property string wallDeck: ""
    property string wallPlace: ""
    property string wallDate: ""                  // 비우면 메인 사진 EXIF 촬영월로 자동
    property var wallTitles: ["", "", ""]
    function wallSetTitle(i, v) {
        var a = win.wallTitles.slice(); a[i] = v; win.wallTitles = a
        controller.setWallpaperText("title" + i, v)
    }
    // 마지막 작업 상태 복원(패널이 켜진 경우에만 — .env 플래그 off 면 불필요):
    // 텍스트 + 슬롯 사진/오프셋 + 레이아웃 옵션. 사라진 파일은 빈 슬롯으로 복원된다
    // (controller.wallpaperSlotPath 가 존재 확인).
    Component.onCompleted: {
        // ⚠️아래 배경화면 조기 return **앞**에 둘 것 — 릴리즈 빌드는 wallpaperEnabled 가 false 라
        //   뒤에 두면 프리셋 목록이 영영 로드되지 않는다(섹션 헤더의 개수도 안 보인다).
        win.refreshPresets()
        if (!controller.wallpaperEnabled) return
        win.wallKicker = controller.wallpaperText("kicker") || "Photo Essay"
        win.wallHeadline = controller.wallpaperText("headline")
        win.wallDeck = controller.wallpaperText("deck")
        win.wallPlace = controller.wallpaperText("place")
        win.wallDate = controller.wallpaperText("date")
        win.wallTitles = [controller.wallpaperText("title0"),
                          controller.wallpaperText("title1"),
                          controller.wallpaperText("title2")]
        win.wallSlots = [controller.wallpaperSlotPath("slot0"),
                         controller.wallpaperSlotPath("slot1"),
                         controller.wallpaperSlotPath("slot2")]
        function num(key, dflt, lo, hi) {
            var v = parseFloat(controller.wallpaperText(key))
            return isNaN(v) ? dflt : Math.max(lo, Math.min(hi, v))
        }
        win.wallOffsets = [num("off0", 0, -1, 1), num("off1", 0, -1, 1),
                           num("off2", 0, -1, 1)]
        win.wallLayout = num("layout", 0, 0, 1)
        win.wallTypeface = num("typeface", 0, 0, 3)
        win.wallMainSide = num("mainSide", 1, 0, 1)
        win.wallResIndex = num("resIndex", 0, 0, 6)
        win.wallGap = num("gap", 18, 0, 60)
        win.wallDualAspect = controller.wallpaperText("dual") !== "0"
        win.wallRefreshPresets()
    }
    // 앞 3개는 기존 순서 유지(저장된 resIndex 호환) + 16:10 3종 + 현재 화면 크기
    readonly property var wallResW: [3840, 2560, 1920, 3840, 2560, 1920, controller.screenW]
    readonly property var wallResH: [2160, 1440, 1080, 2400, 1600, 1200, controller.screenH]
    // 한 파일로 16:9·16:10 양쪽 대응: 사진은 꽉 채우고 글자는 두 비율 공통 안전영역 안에만
    // 배치(잡지 레이아웃에만 의미 있음 — 트립틱은 보호할 글자가 없다).
    property bool wallDualAspect: true
    readonly property var wallSafeAspects: win.wallDualAspect ? [16 / 9, 16 / 10] : []
    readonly property int wallFilled: {
        var n = 0
        for (var i = 0; i < 3; i++) if (wallSlots[i] !== "") n++
        return n
    }
    property bool wallActive: false
    property var wallQueue: []                    // [{slot, path}]
    property int wallIndex: 0
    // 단계: 1=로드 대기(editsReady) → 2=마스크 settle 대기 → 3=패널 렌더 대기 → 4=합성 대기
    property int wallPhase: 0
    property real wallPhaseT0: 0
    property bool wallCancel: false
    property string wallDestUrl: ""
    property string wallResult: ""                // 패널 내 결과 문구

    // 설정 영구 저장(controller → 사용자 데이터 폴더의 wallpaper.json). 값은 문자열로 넘긴다.
    function wallSave(key, v) { controller.setWallpaperText(key, String(v)) }
    property var wallPresets: []                  // 프리셋 이름 목록(콤보 모델)
    function wallRefreshPresets() { win.wallPresets = controller.wallpaperPresetNames() }
    // 현재 패널 상태 → 프리셋 저장용 맵(사진 슬롯 포함)
    function wallCurrentState() {
        return {
            "layout": win.wallLayout, "typeface": win.wallTypeface,
            "mainSide": win.wallMainSide, "resIndex": win.wallResIndex, "gap": win.wallGap,
            "off0": win.wallOffsets[0], "off1": win.wallOffsets[1], "off2": win.wallOffsets[2],
            "slot0": win.wallSlots[0], "slot1": win.wallSlots[1], "slot2": win.wallSlots[2],
            "kicker": win.wallKicker, "headline": win.wallHeadline, "deck": win.wallDeck,
            "place": win.wallPlace, "date": win.wallDate,
            "dual": win.wallDualAspect ? 1 : 0,
            "title0": win.wallTitles[0], "title1": win.wallTitles[1], "title2": win.wallTitles[2]
        }
    }
    // 맵 → 패널 상태 반영 + 같은 값을 '마지막 상태'로도 저장(재시작 시 그대로 복원)
    function wallApplyState(m) {
        function num(k, dflt, lo, hi) {
            var v = parseFloat(m[k])
            return isNaN(v) ? dflt : Math.max(lo, Math.min(hi, v))
        }
        function str(k) { return m[k] === undefined ? "" : String(m[k]) }
        win.wallLayout = num("layout", win.wallLayout, 0, 1)
        win.wallTypeface = num("typeface", win.wallTypeface, 0, 3)
        win.wallMainSide = num("mainSide", win.wallMainSide, 0, 1)
        win.wallResIndex = num("resIndex", win.wallResIndex, 0, 6)
        win.wallGap = num("gap", win.wallGap, 0, 60)
        if (m["dual"] !== undefined) win.wallDualAspect = String(m["dual"]) !== "0"
        win.wallOffsets = [num("off0", 0, -1, 1), num("off1", 0, -1, 1), num("off2", 0, -1, 1)]
        win.wallSlots = [str("slot0"), str("slot1"), str("slot2")]
        win.wallKicker = str("kicker"); win.wallHeadline = str("headline")
        win.wallDeck = str("deck"); win.wallPlace = str("place"); win.wallDate = str("date")
        win.wallTitles = [str("title0"), str("title1"), str("title2")]
        var cur = win.wallCurrentState()
        for (var k in cur) win.wallSave(k, cur[k])
    }
    function wallAssign(slot) {
        var it = win.explorerFiles[fileListView.currentIndex]
        if (!it || it.isDir) return
        var a = win.wallSlots.slice(); a[slot] = it.path; win.wallSlots = a
        win.wallSave("slot" + slot, it.path)
        // 사진 제목은 그 사진의 저장된 캡션(Florence-2)으로 자동 채움 — 캡션이 없으면
        // 비운다(이전 사진의 제목이 남아 엉뚱한 캡션이 인쇄되는 것 방지). 이후 수정 자유.
        win.wallSetTitle(slot, controller.captionTitle(it.path))
    }
    function wallClearSlot(slot) {
        var a = win.wallSlots.slice(); a[slot] = ""; win.wallSlots = a
        var o = win.wallOffsets.slice(); o[slot] = 0.0; win.wallOffsets = o
        win.wallSave("slot" + slot, "")
        win.wallSave("off" + slot, 0)
        win.wallSetTitle(slot, "")
    }
    // 드래그 중(onMoved)엔 값만 갱신하고, 저장은 릴리스 때 1회(wallSaveOffset).
    function wallSetOffset(slot, v) {
        var o = win.wallOffsets.slice(); o[slot] = v; win.wallOffsets = o
    }
    function wallSaveOffset(slot) { win.wallSave("off" + slot, win.wallOffsets[slot]) }
    // 패널 렌더 파라미터: 현재(슬롯 사진 복원 후) 편집값 + 긴변/비트깊이 오버라이드.
    // outEdge 는 속도 최적화일 뿐 — 합성이 항상 cover-fit 재스케일하므로 정확도와 무관.
    // 크롭된 사진은 크롭 후 세로가 캔버스 높이 이상 되도록 긴 변을 키워 업스케일 열화 방지.
    function wallParams() {
        var p = win.exportParams()
        var frac = ((((win.quarterTurns % 4) + 4) % 4) % 2 === 1) ? win.cropW : win.cropH
        if (!(frac > 0.05)) frac = 1.0
        p["outEdge"] = Math.min(6000, Math.round(win.wallResH[win.wallResIndex] / frac))
        p["bitDepth"] = 8
        return p
    }
    function wallStart(destUrl) {
        if (win.wallActive || win.batchActive || controller.exporting) return
        if (win.wallFilled !== 3) return
        var q = []
        for (var i = 0; i < 3; i++) q.push({ slot: i, path: win.wallSlots[i] })
        win.wallQueue = q; win.wallIndex = 0
        win.wallCancel = false; win.wallDestUrl = destUrl; win.wallResult = ""
        controller.wallpaperClearPanels()
        win.wallActive = true
        controller.setKeepAwake(true)    // 배경화면 실행 전 구간 시스템 슬립 방지
        win.wallLoadNext()
    }
    function wallLoadNext() {
        if (win.wallCancel) { win.wallAbort("cancelled"); return }
        if (win.wallIndex >= win.wallQueue.length) {      // 3장 완료 → 합성
            win.wallPhase = 4; win.wallPhaseT0 = Date.now()
            controller.wallpaperCompose(win.wallDestUrl, {
                "canvasW": win.wallResW[win.wallResIndex],
                "canvasH": win.wallResH[win.wallResIndex],
                "layout": win.wallLayoutKeys[win.wallLayout],
                "gap": win.wallGap, "offsets": win.wallOffsets,
                // 잡지 레이아웃용(트립틱이면 Python 이 무시)
                "typeface": win.wallTypefaceKeys[win.wallTypeface],
                "mainSide": win.wallMainSide === 0 ? "left" : "right",
                "safeAspects": win.wallSafeAspects,
                "kicker": win.wallKicker, "headline": win.wallHeadline,
                "deck": win.wallDeck, "place": win.wallPlace, "date": win.wallDate,
                "titles": win.wallTitles, "paths": win.wallSlots })
            return
        }
        win.wallPhase = 1; win.wallPhaseT0 = Date.now()
        controller.loadPath(win.wallQueue[win.wallIndex].path)
    }
    function wallAbort(why) {
        win.wallActive = false; win.wallPhase = 0
        controller.setKeepAwake(false)
        controller.wallpaperClearPanels()
        win.wallResult = "Wallpaper " + why
    }
    function wallFinish(ok) {
        win.wallActive = false; win.wallPhase = 0
        controller.setKeepAwake(false)
        controller.wallpaperClearPanels()                 // 패널 배열 메모리 해제
        win.wallResult = ok ? "Wallpaper saved" : ("Wallpaper failed (" + controller.exportStatus + ")")
    }
    Connections {
        target: controller
        function onEditsReady() {
            if (win.wallActive && win.wallPhase === 1)
                Qt.callLater(function() { win.wallPhase = 2; win.wallPhaseT0 = Date.now() })
        }
    }
    Timer {
        id: wallTick
        interval: 250; repeat: true
        running: win.wallActive
        onTriggered: {
            var waited = Date.now() - win.wallPhaseT0
            if (win.wallPhase === 1) {
                if (waited > 30000) win.wallAbort("failed (load timeout)")
            } else if (win.wallPhase === 2) {
                // batchTick phase 2 와 동일한 마스크 settle 대기(폴백 포함, 그쪽 주석 참조)
                var maskPending = win.maskKeys.length > 0 &&
                                  !controller.hasSkyMask && !controller.maskSettled
                if (!controller.busy && !controller.skyBusy && (!maskPending || waited > 20000)) {
                    controller.wallpaperRenderPanel(
                        win.wallQueue[win.wallIndex].slot, win.wallParams())
                    if (!controller.exporting) { win.wallAbort("failed (render refused)"); return }
                    win.wallPhase = 3; win.wallPhaseT0 = Date.now()
                }
            } else if (win.wallPhase === 3) {
                if (!controller.exporting) {
                    if (controller.exportStatus.indexOf("PanelReady:") !== 0) {
                        win.wallAbort("failed (" + controller.exportStatus + ")"); return
                    }
                    win.wallIndex++
                    win.wallLoadNext()
                }
            } else if (win.wallPhase === 4) {
                if (!controller.exporting)
                    win.wallFinish(controller.exportStatus.indexOf("Saved:") === 0)
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

    // ⚠️룩 지문(배지 활성 판정)은 **여기 두 함수에서만** 갱신한다. 개별 경로(commit/paste/
    //   reset/apply/forget)에 흩어 뒀더니 **붙여넣기와 Reset 버튼에서 빠져** 룩이 레시피와
    //   같아졌는데도 배지가 안 켜졌다(사용자 보고). 룩을 바꾸는 경로는 예외 없이 히스토리에
    //   스냅샷을 남기므로 이 둘이 유일한 공통 지점이다.
    //   (undo/redo 는 push 하지 않으므로 applySnapshot 이 따로 부른다 — 그 한 곳만 예외다.)
    function histReset(snapStr) {
        win.undoHist = [snapStr]; win.undoPos = 0
        win.refreshLookHash()
    }
    function histPush(snapStr) {
        win.refreshLookHash()   // 아래 '변화 없음' 조기반환보다 앞 — 지문이 낡은 채 남지 않게
        if (win.undoPos >= 0 && win.undoHist[win.undoPos] === snapStr) return   // 변화 없음
        var h = win.undoHist.slice(0, win.undoPos + 1)                          // redo 꼬리 버림
        h.push(snapStr)
        if (h.length > 100) h = h.slice(h.length - 100)                         // 상한
        win.undoHist = h; win.undoPos = h.length - 1
    }
    // 스냅샷 적용(undo/redo 공통) — paste 와 동일 경로: _applying 가드로 자동저장/WB 재디코딩
    // 억제 후 WB·커브 직접 반영 + 사이드카 저장 + 히스토그램 갱신.
    function applySnapshot(snapStr) {
        // ⚠️프리셋 배너를 지운다 — Ctrl+Z 로 되돌린 뒤에도 배너가 남아 있으면 "다른 기종의
        //   레시피가 적용된 상태"라고 거짓말을 한다. 정직함이 전부인 기능이라 치명적이다.
        win.clearPresetNotice()
        var p = JSON.parse(snapStr)
        win._applying = true
        win.applyEdits(p, true)          // undo/redo = 획 tail-diff 즉각 경로 허용
        win._applying = false
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.saveEdits(win.editParams())
        win.refreshHistogram()
        win.refreshLookHash()
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
        win.histPush(JSON.stringify(snap))   // 커밋된 편집 1개 = undo 스텝 1개(지문도 여기서 갱신)
    }
    // 드래그 진행 중 여부 — 편집에 관여하는 모든 드래그 소스를 **명시적으로 열거**(결정론적).
    // 과거 전역 PointHandler(패시브 감시)만으로는 일부 컨트롤의 press 를 이벤트 전달 경로에 따라
    // 놓칠 수 있었음 → 컨트롤들의 pressed 를 직접 참조. PointHandler 는 보조 안전망으로 유지.
    // 값이 변하는 편집 드래그만(globalPress 제외) — 원판 그레인 폴백 게이트용.
    // globalPress 는 창 어디든 프레스면 활성이라, 여기에 걸면 모든 클릭에서 결이 깜빡인다.
    readonly property bool editSliderDragActive:
        expSlider.pressed || conSlider.pressed || hiSlider.pressed || shSlider.pressed
        || whSlider.pressed || blSlider.pressed || tempSlider.pressed || tintSlider.pressed
        || simStrengthSlider.pressed || texSlider.pressed || claritySlider.pressed
        || dehazeSlider.pressed || vibSlider.pressed || satSlider.pressed
        || hslHueSlider.pressed || hslSatSlider.pressed || hslLumSlider.pressed
        || cgShHueSlider.pressed || cgShSatSlider.pressed || cgMidHueSlider.pressed
        || cgMidSatSlider.pressed || cgHiHueSlider.pressed || cgHiSatSlider.pressed
        || cgBalanceSlider.pressed || vignetteSlider.pressed || grainSlider.pressed
        || mistAmtSlider.pressed || mistCharSlider.pressed
        || mistRadiusSlider.pressed || mistHiSlider.pressed || mistColorSlider.pressed
        || grainSizeSlider.pressed || grainRoughSlider.pressed || grainColorSlider.pressed
        || sharpAmtSlider.pressed || sharpRadiusSlider.pressed
        || sharpDetailSlider.pressed || sharpMaskSlider.pressed || lumaNrSlider.pressed
        || colorNrSlider.pressed || rotAngleSlider.pressed || geoVSlider.pressed
        || geoHSlider.pressed || geoScaleSlider.pressed
        || skyExpSlider.pressed || skyTempSlider.pressed || skyTintSlider.pressed
        || skySatSlider.pressed || skyHiSlider.pressed || skyShadowsSlider.pressed
        || skyTextureSlider.pressed || skyClaritySlider.pressed || skyDehazeSlider.pressed
        || skyContrastSlider.pressed
        || depthNearSlider.pressed || depthFarSlider.pressed || depthFeatherSlider.pressed
        || stampSizeSlider.pressed || stampMarginSlider.pressed
        || stampGlowSlider.pressed || stampSpreadSlider.pressed
        || curveEditor.dragging || cropOverlay.dragging
    readonly property bool editDragActive: globalPress.active || editSliderDragActive
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
        mistAmtSlider.value, mistCharSlider.value, mistRadiusSlider.value, mistHiSlider.value,
        mistColorSlider.value,
        vignetteSlider.value, grainSlider.value, grainSizeSlider.value,
        grainRoughSlider.value, grainColorSlider.value, grainShapeCheck.checked,
        sharpAmtSlider.value, sharpRadiusSlider.value, sharpDetailSlider.value, sharpMaskSlider.value,
        lumaNrSlider.value, colorNrSlider.value, aiNrCheck.checked,
        lensCheck.checked, autoExpCheck.checked, win.dateStamp, stampField.text,
        controller.stampFont, controller.stampSize, controller.stampMargin,
        controller.stampColor, controller.stampGlow, controller.stampSpread,
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
        cgHiHueSlider.value, cgHiSatSlider.value, cgBalanceSlider.value, vignetteSlider.value,
        mistAmtSlider.value, mistCharSlider.value, mistRadiusSlider.value,
        mistHiSlider.value, mistColorSlider.value,
        autoExpCheck.checked          // 베이스 밝기가 통째로 바뀐다 — 분포도 다시 그려야 한다
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
            "mistAmt": mistAmtSlider.value, "mistChar": mistCharSlider.value,
            "mistRadius": mistRadiusSlider.value, "mistHi": mistHiSlider.value,
            "mistColor": mistColorSlider.value,
            "vignette": vignetteSlider.value, "grainAmt": grainSlider.value, "grainSize": grainSizeSlider.value,
            "grainRough": grainRoughSlider.value, "grainColor": grainColorSlider.value,
            "grainShape": grainShapeCheck.checked,
            "lutEnabled": simCombo.currentIndex !== 0, "simKey": win.simKeys[simCombo.currentIndex],
            "lutStrength": simStrengthSlider.value, "curves": curveEditor.allLuts(),
            "dateStamp": win.dateStamp, "stampText": stampField.text, "stampRot": controller.stampRot,
            "stampStyle": controller.stampFont, "stampSize": controller.stampSize,
            "stampMargin": controller.stampMargin, "stampColor": controller.stampColor,
            "stampGlow": controller.stampGlow, "stampSpread": controller.stampSpread,
            "outEdge": win.exportEdges[resCombo.currentIndex], "lensCorrection": lensCheck.checked,
            "autoExposure": autoExpCheck.checked,
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

    // URL/경로의 확장자를 ext 로 바꿔서 돌려준다(확장자가 없으면 붙인다). Export 대화상자에서
    // name filter 와 파일명을 같은 형식으로 묶는 데 쓴다.
    function withExt(u, ext) {
        var s = String(u)
        var slash = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"))
        var dot = s.lastIndexOf(".")
        return (dot > slash ? s.substring(0, dot) : s) + "." + ext
    }

    // 렌더 엔진 = Export 패널의 Render 콤보(0=CPU, 1=GPU). 16bit 는 GPU grab 이 8bit 라 항상 CPU.
    // 단일/배치가 같은 규칙을 쓰도록 여기 한 곳에만 둔다.
    readonly property bool useGpuExport: renderModeCombo.currentIndex === 1 && !bitDepth16Check.checked
    // Export 실행 진입점(단일 저장·배치 공용). 반환값 = 실제로 시작됐는지(슬롯 재진입 가드 결과).
    function startExport(url, p) {
        if (win.useGpuExport) {
            win.gpuExportEdge = p["outEdge"]   // 요청 시점 스냅샷(디코드 중 콤보 변경과 분리)
            controller.exportImageGpu(url, p)
            // 실제로 진행됐을 때만 로더 활성 — 슬롯 가드에 걸려 시작 안 됐는데 active=true 로
            // 두면 grab 을 구동할 디코드가 없어 pipeFull 이 떠 있게 됨.
            if (controller.exporting) gpuExportLoader.active = true
        } else {
            controller.exportImage(url, p)
        }
        return controller.exporting
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
    // 짝 JPEG 펼치기/접기 — 선택 항목이 사라져 인덱스가 다른 파일을 가리키는 것 방지(L 과 동일 규율)
    Shortcut {
        sequence: "P"; enabled: !win._typing
        onActivated: {
            var sel = ""
            if (fileListView.currentIndex >= 0 && win.explorerFiles[fileListView.currentIndex])
                sel = win.explorerFiles[fileListView.currentIndex].path
            win.showPairedImages = !win.showPairedImages
            if (sel !== "") Qt.callLater(function () { win.selectInExplorer(sel) })
        }
    }
    // H = 폴더 태그 워드 클라우드 토글(열기/닫기). 폴더가 있어야 열림.
    Shortcut {
        sequence: "H"; enabled: !win._typing
        onActivated: {
            if (win.showTagCloud) win.showTagCloud = false
            else if (controller.currentFolder !== "") win.openTagCloud()
        }
    }
    // 카메라 RAW+JPEG 동시기록에서 짝 JPEG 을 별도 행으로 볼지(기본 꺼짐 = 접어서 중복 제거).
    // 라이트룸의 'Treat JPEG files next to raw files as separate photos' 와 같은 의미.
    property bool showPairedImages: false
    // 다시 접으면 화면에서 사라지므로 배치 체크도 함께 푼다 — 안 보이는 파일이 Export 에
    // 섞여 나가는 것 방지(폴더 이동 시 초기화되는 batchClearChecked 와 같은 취지).
    onShowPairedImagesChanged: {
        if (win.showPairedImages || win.batchCheckedCount === 0) return
        var files = controller.fileList
        var changed = false
        for (var i = 0; i < files.length; i++) {
            var it = files[i]
            if (it.paired && win.batchChecked[it.path] === true) {
                delete win.batchChecked[it.path]
                changed = true
            }
        }
        if (changed) win.batchCheckedRev++
    }

    // 필터 적용된 표시 목록: 좋아요만 보기면 폴더(탐색용) + 좋아요된 RAW 만.
    //  - controller.fileList(1회만 마샬링)·likeRevision·showLikedOnly 변경 시 자동 재평가
    property var explorerFiles: {
        controller.likeRevision               // 좋아요 토글 시 재평가용 의존
        controller.searchQuery                // 캡션 검색어 변경 시 재평가용 의존
        var files = controller.fileList        // folderChanged 시 재평가 + 1회만 읽기
        var q = controller.searchQuery
        // 접을 짝이 없는 폴더(이미지 전용·RAW 전용)는 예전처럼 원본 배열을 그대로 — 999장 폴더에서
        // 좋아요를 누를 때마다 전체를 순회하지 않게(기존 fast path 유지).
        if (!win.showLikedOnly && q === ""
                && (win.showPairedImages || !controller.folderHasPairs))
            return files
        var out = []
        for (var i = 0; i < files.length; i++) {
            var it = files[i]
            if (it.isDir) { out.push(it); continue }        // 폴더는 항상 표시(탐색용)
            if (!win.showPairedImages && it.paired) continue  // 짝 RAW 가 있는 JPEG → 접기
            if (win.showLikedOnly && !controller.isLiked(it.path)) continue
            if (q !== "" && !controller.matchesSearch(it.path)) continue
            out.push(it)
        }
        return out
    }

    // Export 해상도 프리셋(긴 변 px, 0=원본). resCombo 모델 순서와 일치.
    readonly property var exportEdges: [0, 4096, 3840, 2560, 2048, 1920, 1280]
    // GPU export 의 해상도 프리셋(요청 시점 스냅샷). pipeFull 이 이 크기로 직접 렌더한다 —
    // 풀해상도로 렌더 후 CPU 축소하면 그레인이 씻겨(σ 12.7→10.5) CPU export 와 갈라졌었다.
    property int gpuExportEdge: 0

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
    // coeffs 의 리스트 계수 → vec4 uniform. 길이가 모자라면 항등 커널(narrow 만)로 안전 폴백.
    function vec4Of(a) {
        return (a && a.length >= 4) ? Qt.vector4d(a[0], a[1], a[2], a[3]) : Qt.vector4d(1, 0, 0, 0)
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

    // 필름시뮬 보정 노출 재계산 요청(강도 드래그용 디바운스 — solve 가 표본 2.8k px × 12회라
    // ~20ms 다. 프레임마다 돌리면 드래그가 무거워진다).
    Timer {
        id: simEvTimer
        interval: 120
        onTriggered: win.pushFilmSim()
    }
    function pushFilmSim() {
        var i = simCombo.currentIndex
        var k = (i > 0 && i < win.simKeys.length) ? win.simKeys[i] : "identity"
        controller.setFilmSim(k, simStrengthSlider.value)
    }
    // now=true(시뮬 교체·프로그램 대입)면 즉시 — 한 프레임이라도 보정 전 룩이 보이면 번쩍인다.
    function syncFilmSim(now) {
        simEvTimer.stop()
        if (now) win.pushFilmSim(); else simEvTimer.start()
    }
    // 보정 노출이 확정되면 히스토그램도 다시(먼저 그려진 것은 보정 전 분포다).
    Connections {
        target: controller
        function onSimExpEVChanged() { win.refreshHistogram() }
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
            // 라이트룸식 전체 반영: 색 단계 + 비네팅 + 미스트(그레인만 제외 — 노이즈다).
            // 미스트는 톤 단계다 — 베일이 블랙을 들어올리는데 히스토그램이 그걸 안 보여주면
            // 클리핑 판단에 쓰는 그림이 화면과 달라진다.
            "mistAmt": mistAmtSlider.value, "mistChar": mistCharSlider.value,
            "mistRadius": mistRadiusSlider.value, "mistHi": mistHiSlider.value,
            "mistColor": mistColorSlider.value,
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

    // AI 모델 관리 — 좌측 패널 푸터의 'AI Models' 에서 열림. 사진과 무관한 앱 전역 화면이라
    // 우측 편집 패널(Edit/Crop/Masking)이 아니라 모달로 둔다(사진 없이도 열려야 함).
    // 목록/크기/설명은 각 엔진 모듈이 소유하고 controller.modelCatalog 가 취합한다.
    Popup {
        id: modelDialog
        modal: true
        dim: true
        // 창이 좁으면 넘치지 않게 — 시작은 최대화지만 사용자가 줄일 수 있다
        width: Math.min(620, win.width - 48)
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }
        // 폴더에서 직접 지웠거나 다른 경로로 받힌 모델이 있을 수 있으니 열 때마다 재평가.
        onOpened: controller.refreshModels()

        contentItem: ColumnLayout {
            spacing: 0

            RowLayout {                                   // 헤더
                Layout.fillWidth: true
                Layout.margins: 20
                Layout.bottomMargin: 12
                Label {
                    Layout.fillWidth: true
                    text: "AI Models"
                    color: "white"; font.pixelSize: 16; font.bold: true
                }
                Label {
                    text: controller.modelSummary.installedText + " installed"
                          + (controller.modelSummary.missingBytes > 0
                             ? "   ·   " + controller.modelSummary.missingText + " missing" : "")
                    color: "#8a8a8a"; font.pixelSize: 11
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#3d3d40" }

            ColumnLayout {                                // 모델 목록
                Layout.fillWidth: true
                Layout.margins: 20
                spacing: 14
                Repeater {
                    model: controller.modelCatalog
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Rectangle {                       // 상태 점: 초록=설치, 앰버=일부, 회색=없음
                            Layout.preferredWidth: 8; Layout.preferredHeight: 8; radius: 4
                            Layout.alignment: Qt.AlignTop
                            Layout.topMargin: 5
                            color: modelData.installed ? "#6fbf73"
                                   : (modelData.partial ? "#E0A226" : "#5a5a5a")
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: modelData.label
                                color: "#eee"; font.pixelSize: 13
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.note
                                color: "#8a8a8a"; font.pixelSize: 11; wrapMode: Text.WordWrap
                            }
                            Label {                       // 일부만 받힌 경우에만 파일 진행 표시
                                visible: modelData.partial
                                text: modelData.haveText + " downloaded"
                                color: "#E0A226"; font.pixelSize: 10
                            }
                        }
                        Label {
                            Layout.preferredWidth: 62
                            Layout.alignment: Qt.AlignTop
                            text: modelData.sizeText
                            color: "#9a9a9a"; font.pixelSize: 12
                            horizontalAlignment: Text.AlignRight
                        }
                        Item {                            // 설치됨 표시 / 다운로드 버튼 자리
                            Layout.preferredWidth: 96
                            Layout.preferredHeight: 28
                            Layout.alignment: Qt.AlignTop
                            Label {
                                anchors.centerIn: parent
                                visible: modelData.installed
                                text: "Installed"; color: "#6fbf73"; font.pixelSize: 12
                            }
                            Button {
                                anchors.fill: parent
                                visible: !modelData.installed
                                // 동시 1개만 — 진행 중에는 나머지 버튼 잠금
                                enabled: controller.modelDownloading === ""
                                text: controller.modelDownloading === modelData.key
                                      ? Math.round(controller.modelProgress * 100) + "%"
                                      : (modelData.partial ? "Resume" : "Download")
                                onClicked: controller.downloadModel(modelData.key)
                            }
                        }
                    }
                }
            }

            ColumnLayout {                                // 진행률 / 에러
                Layout.fillWidth: true
                Layout.leftMargin: 20; Layout.rightMargin: 20
                Layout.bottomMargin: 12
                spacing: 6
                visible: controller.modelDownloading !== "" || controller.modelError !== ""
                Label {
                    Layout.fillWidth: true
                    visible: controller.modelDownloading !== ""
                    text: "Downloading " + controller.modelDownloading + "…  "
                          + Math.round(controller.modelProgress * 100) + "%"
                    color: "#E0A226"; font.pixelSize: 11
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 4
                    visible: controller.modelDownloading !== ""
                    color: "#3a3a3a"; radius: 2
                    Rectangle {
                        height: parent.height; radius: 2; color: "#E0A226"
                        width: parent.width * Math.min(1.0, controller.modelProgress)
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: controller.modelError !== ""
                    text: "Download failed — " + controller.modelError
                    color: "#e07a7a"; font.pixelSize: 11; wrapMode: Text.WordWrap
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#3d3d40" }
            RowLayout {                                   // 푸터: 저장 위치 / 미사용 파일 / 닫기
                Layout.fillWidth: true
                Layout.margins: 16
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Label {
                        Layout.fillWidth: true
                        text: controller.modelSummary.dirPath
                        color: "#7a7a7a"; font.pixelSize: 10; elide: Text.ElideMiddle
                    }
                    Label {
                        visible: controller.modelSummary.orphanText !== ""
                        // 기각·대체된 모델의 잔재. 삭제는 구현하지 않고 폴더만 열어준다
                        // (사용자 데이터 삭제는 되돌릴 수 없어 앱이 임의로 하지 않는다).
                        text: controller.modelSummary.orphanText
                              + " unused (superseded models) — delete manually if you want the space"
                        color: "#8a8a8a"; font.pixelSize: 10; wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
                Button { text: "Open folder"; onClicked: controller.openModelsFolder() }
                Button { text: "Close"; onClicked: modelDialog.close() }
            }
        }
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

        // 진행 중인 작업 — 내보내기 외에 배치/배경화면도 포함(파일 내 다른 busy 표시와
        // 같은 조건). export 만 보면 배치 1·2단계(로드/마스크 재생성)에서 '그냥 종료'
        // 문구가 떠서 수백 장 배치가 경고 없이 중단된다.
        readonly property bool busy: controller.exporting || win.batchActive || win.wallActive

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
                    // 진행 중이면 경고 문구로 — 지금 쓰이는 파일은 만들어지지 않는다
                    // (기존 파일은 pipeline.save_image 의 원자적 교체가 보호한다).
                    text: quitDialog.busy ? "Export in progress" : "Quit FILM RAWSTERY?"
                    color: quitDialog.busy ? "#E0A226" : "#f2f2f2"
                    font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: quitDialog.busy
                          ? "Quitting now cancels it — the file being written will not be saved.\n"
                            + "Your current edits are saved before exit."
                          : "Your current edits are saved before exit."
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
                        Label {
                            anchors.centerIn: parent
                            text: quitDialog.busy ? "Quit anyway" : "Quit"
                            color: "#1a1a1a"; font.pixelSize: 13; font.bold: true
                        }
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

    // 후원 대화상자 (우측 셀렉터 바 맨 하단 ♥ → 카카오페이 QR + 용도 안내).
    // 종료/CPU 대화상자와 동일 컨셉(다크 + 필름 퍼포레이션 + 앰버 강조).
    Popup {
        id: donateDialog
        modal: true
        dim: true
        width: 440        // QR 을 크게 보여주려 종료/CPU 대화상자(380)보다 넓게
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }

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
                spacing: 10

                Label {
                    text: "Support / 후원"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "Donations go toward buying a MacBook, so macOS can actually be tested instead of only being written to be platform-clean."
                    color: "#9a9a9a"; font.pixelSize: 12
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
                Label {
                    text: "후원금은 맥북을 구입하는 데 사용하려 합니다. 개발은 Windows에서만 진행하고 있어 macOS는 한 번도 제대로 테스트해 보지 못했습니다. 물론 이와 상관없이 개발은 그대로 계속 이어집니다. :)"
                    color: "#9a9a9a"; font.pixelSize: 12
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                // 카카오페이 수신 QR — 링크는 모바일 전용이라 스캔이 유일한 데스크톱 경로.
                Image {
                    source: "../assets/donate_kakaopay.jpg"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 4
                    Layout.preferredWidth: 260
                    Layout.preferredHeight: 260 * (sourceSize.height / Math.max(1, sourceSize.width))
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                }
                Label {
                    text: "KakaoPay — scan with your phone\n카카오페이로 QR을 스캔해주세요"
                    color: "#6a6a6a"; font.pixelSize: 11
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                }

                Rectangle {        // Close (앰버 강조)
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    Layout.preferredHeight: 40; radius: 8
                    color: donateCloseMA.containsMouse ? "#f0b945" : "#E0A226"
                    Label {
                        anchors.centerIn: parent; text: "Close"
                        color: "#1a1a1a"; font.pixelSize: 13; font.bold: true
                    }
                    MouseArea {
                        id: donateCloseMA; anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: donateDialog.close()
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

    // 프리뷰 모드 오버레이(탐색기에서 RAW 우클릭 → 메뉴 Preview 로 염). 메인 창 위를 꽉 덮음.
    // 닫으면 마지막으로 보던 사진을 탐색기에서 선택(하이라이트+스크롤)만 한다 — 로드는 안 함.
    PreviewWindow {
        id: previewWin
        onClosedAt: (path) => win.selectInExplorer(path)
    }

    // Alt+↑: 상위 폴더로 이동(Windows 탐색기 관례). 위로가기 버튼과 동일하게 직전 폴더 선택 유지.
    Shortcut {
        sequence: "Alt+Up"
        enabled: !win.batchActive && !win.wallActive && !previewWin.visible
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
                 && !win.wallActive
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
        // 목록에 없음(필터/접기로 사라짐) → currentIndex 를 그대로 두면 **다른 사진**을 가리킨
        // 채 남아서 Return/방향키가 엉뚱한 파일에 작동한다. 선택을 명시적으로 해제한다.
        // (짝 접기는 기본 상태이고 행의 절반을 없애므로 좋아요 필터보다 훨씬 자주 걸린다.)
        fileListView.currentIndex = -1
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
    property int tagPreviewTotal: 0          // 그 태그의 전체 사진 수(보이는 건 무작위 표본)
    property int _tagRoll: 0                 // 표본 시드 — ⟳ 로 증가시켜 다른 사진들을 뽑는다
    property int _tagRollBase: 0             // 이번 ☁ 오픈의 시드 출발점(열 때마다 무작위 → 재시작해도 다른 사진)
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
        // ☁ 열 때마다 표본 시드의 출발점을 새로 뽑는다 — 이게 없으면 시드가 (단어, roll) 이고
        // roll 이 항상 0부터라 앱을 재시작해도 늘 같은 사진만 나온다.
        win._tagRollBase = Math.floor(Math.random() * 1000000)
        win._tagRoll = win._tagRollBase
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
        if (want !== prev) win._tagRoll = win._tagRollBase   // 다른 태그로 바뀔 때만 시드 리셋(이번 오픈 기준)
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
        win.tagPreviewPaths = win._hoverTag
            ? controller.filesWithKeyword(win._hoverTag, win.previewLimit(), win._tagRoll) : []
        win.tagPreviewTotal = win._hoverTag ? controller.keywordCount(win._hoverTag) : 0
    }
    // ⟳ 다시 뽑기 — 같은 태그에서 다른 무작위 표본(뒤에 찍은 사진들)을 본다.
    function rerollPreview() { win._tagRoll = win._tagRoll + 1; win.refreshPreview() }
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
            // 태그가 실제로 바뀔 때만 표본 시드 리셋(같은 태그면 ⟳ 로 뽑은 표본 유지).
            onTriggered: {
                if (win._hoverTag !== win._pendingTag) win._tagRoll = win._tagRollBase
                win._hoverTag = win._pendingTag; win.refreshPreview()
            }
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
                    // 미리보기 제목 (열 때 최상위 단어 자동) + ⟳ 다시 뽑기.
                    // 사진이 화면에 들어가는 수보다 많으면 전체에서 무작위 표본을 보여주므로,
                    // 'n of N' 으로 일부임을 알리고 ⟳ 로 다른 사진들을 볼 수 있게 한다.
                    RowLayout {
                        visible: win.tagCloudData.length > 0
                        Layout.fillWidth: true
                        spacing: 10
                        Label {
                            Layout.fillWidth: true
                            text: win._hoverTag
                                ? ("“" + win._hoverTag + "”  ·  "
                                   + (win.tagPreviewTotal > win.tagPreviewPaths.length
                                      ? win.tagPreviewPaths.length + " of " + win.tagPreviewTotal + " photos"
                                      : win.tagPreviewPaths.length + " photos"))
                                : "Hover a tag to preview its photos"
                            color: win._hoverTag ? "#cfe0ff" : "#5f6b7a"
                            font.pixelSize: 14; font.bold: win._hoverTag.length > 0
                            elide: Text.ElideRight
                        }
                        Text {   // ⟳ 다른 무작위 표본(표본이 전체보다 적을 때만 의미 있음)
                            visible: win.tagPreviewTotal > win.tagPreviewPaths.length
                            text: "⟳ shuffle"
                            color: tcRoll.hovered ? "#cfe0ff" : "#8892a0"
                            font.pixelSize: 13
                            HoverHandler { id: tcRoll }
                            MouseArea {
                                anchors.fill: parent; anchors.margins: -6
                                cursorShape: Qt.PointingHandCursor
                                onClicked: win.rerollPreview()
                            }
                        }
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
                                        // 160=EXIF 썸네일 고속 경로 한계(탐색기와 동일 경로, ~1-5ms).
                                        // >160 이면 풀 프리뷰 축소 디코딩으로 넘어가 느려짐(128px 셀엔 160 충분).
                                        sourceSize.width: 160; fillMode: Image.PreserveAspectCrop
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

    // Export 대화상자에서 name filter 를 바꾸면 파일명 확장자도 따라가게 한다.
    // ⚠️`saveDialog.onSelectedNameFilterChanged` 는 쓸 수 없다 — QML 타입 정의상 그 프로퍼티는
    //   isPropertyConstant 라 변경 시그널이 아예 없다(핸들러가 조용히 죽는다). 실제로 발화하는
    //   것은 QQuickFileNameFilter.indexChanged 이므로 index 를 프로퍼티로 끌어와 감시한다.
    readonly property int exportFilterIndex: saveDialog.selectedNameFilter.index
    onExportFilterIndexChanged: {
        if (!saveDialog.visible) return          // 열기 전 프로그램적 설정은 이름을 이미 맞춰 둔 상태
        var i = win.exportFilterIndex
        if (i < 0 || i >= saveDialog.filterExts.length) return
        var e = saveDialog.filterExts[i]
        controller.setExportExt(e)
        saveDialog.selectedFile = win.withExt(saveDialog.selectedFile, e)
    }

    FileDialog {
        id: saveDialog
        title: "Export (Full Resolution)"
        fileMode: FileDialog.SaveFile
        nameFilters: ["PNG (*.png)", "JPEG (*.jpg)", "TIFF (*.tif)"]
        // ⚠️저장 포맷은 pipeline.save_image 가 **파일명 확장자**로 정한다. 그래서 필터·파일명·
        //   defaultSuffix 가 어긋나면 '필터는 JPEG 인데 PNG 로 저장' 이 된다(실제 사용자 보고).
        //   controller.exportExt(마지막 사용 형식, 영구 저장)를 단일 출처로 세 곳을 묶는다.
        readonly property var filterExts: ["png", "jpg", "tif"]
        defaultSuffix: controller.exportExt
        // 렌더 모드: 0=CPU(render_full), 1=GPU(프리뷰 셰이더로 풀해상도 렌더 → 프리뷰=Export)
        onAccepted: {
            // 다음 export 대화상자가 같은 폴더에서 열리게 기억한다(파일명은 기억하지 않는다).
            controller.rememberExportFolder(selectedFile)
            win.startExport(selectedFile, win.exportParams())   // 엔진 선택은 win.startExport
        }
    }

    // 배경화면 저장도 같은 결함을 가진다 — 제안 이름은 항상 .jpg 인데 필터를 PNG 로 바꿔도
    // 이름이 그대로라 jpg 로 저장된다(포맷은 확장자가 결정). 여기선 세션 내 일치만 맞춘다
    // (Export 와 달리 형식을 기억할 필요는 없어 QSettings 는 쓰지 않는다).
    readonly property int wallFilterIndex: wallpaperSaveDialog.selectedNameFilter.index
    onWallFilterIndexChanged: {
        if (!wallpaperSaveDialog.visible) return
        var exts = ["jpg", "png"]                     // wallpaperSaveDialog.nameFilters 순서와 일치
        var i = win.wallFilterIndex
        if (i < 0 || i >= exts.length) return
        wallpaperSaveDialog.selectedFile = win.withExt(wallpaperSaveDialog.selectedFile, exts[i])
    }

    // ── 레시피 우클릭 메뉴 ──
    Menu {
        id: presetCtxMenu
        MenuItem {
            // 현재 편집값으로 이 레시피의 룩을 덮어쓴다. 되돌릴 수 없어 확인을 받는다.
            text: "Update look\u2026"
            enabled: controller.imagePath !== ""
            onTriggered: {
                win._presetConfirmMode = "update"
                presetConfirmDialog.open()
            }
        }
        MenuItem {
            text: "Edit properties\u2026"
            onTriggered: presetSaveDialog.openForEdit(
                win._presetCtxFile, win._presetCtxName, win._presetCtxColor,
                win._presetCtxDesc, win._presetCtxSrc)
        }
        MenuItem {
            text: "Export\u2026"
            onTriggered: {
                presetExportDialog.sourceFile = win._presetCtxFile
                var u = controller.suggestedPresetShareUrl(win._presetCtxFile)
                if (String(u) !== "") presetExportDialog.selectedFile = u
                presetExportDialog.open()
            }
        }
        MenuSeparator {}
        MenuItem {
            text: "Delete\u2026"
            onTriggered: {
                win._presetConfirmMode = "delete"
                presetConfirmDialog.open()
            }
        }
    }

    // ── 레시피 저장 / 수정 ──
    // 종료·후원 대화상자와 같은 컨셉(다크 + 상·하 필름 퍼포레이션 + 앰버 강조).
    // 폭 520: 구분색 12칸이 **한 줄**에 들어가야 한다(28px×12 + 8px 간격×11 = 424, 여백 48).
    Popup {
        id: presetSaveDialog
        modal: true
        dim: true
        width: 520
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }
        property string chosenColor: controller.presetPalette[0]
        property var src: ({})
        property string editFile: ""       // "" = 새로 저장 / 경로 = 그 레시피 수정
        property string errorText: ""      // 쓰기 실패 안내(비면 숨김)
        readonly property bool editing: presetSaveDialog.editFile !== ""
        readonly property bool nameOk: presetNameInput.text.trim() !== ""
        // 같은 이름으로 저장하면 그 레시피를 덮어쓴다(파일명이 내부 name 에서 파생되므로).
        // 조용히 덮어쓰면 데이터 손실이라 버튼 라벨과 안내로 드러낸다.
        // ⚠️수정(이름 변경)에서도 반드시 말해야 한다 — 다른 레시피의 이름으로 바꾸면
        // 파일명이 그 레시피와 같아져 **그쪽을 덮어쓰고** 원래 파일은 지워진다(editPreset).
        // 경고 없이는 레시피 하나가 조용히 사라진다. 자기 자신과의 충돌만 제외한다.
        readonly property bool willOverwrite: {
            var n = presetNameInput.text.trim()
            if (n === "") return false
            for (var i = 0; i < win.presetItems.length; i++) {
                var it = win.presetItems[i]
                if (it.name !== n) continue
                if (presetSaveDialog.editing && it.file === presetSaveDialog.editFile) continue
                return true
            }
            return false
        }

        // 새로 저장(현재 편집의 룩을 담는다)
        function openForSave() {
            presetSaveDialog.editFile = ""
            presetSaveDialog.errorText = ""
            presetNameInput.text = ""
            presetDescInput.text = ""
            presetSaveDialog.src = controller.presetSource()
            // 자동 기입(EXIF). 렌즈는 대개 비어 있어 사용자가 채우는 자리가 된다.
            presetCamInput.text = presetSaveDialog.src.camera || ""
            presetLensInput.text = presetSaveDialog.src.lens || ""
            presetSaveDialog.chosenColor = controller.presetPalette[0]
            presetSaveDialog.open()
        }
        // 수정(이름·색·설명만. 룩과 출처는 저장돼 있던 것을 그대로 유지한다)
        function openForEdit(file, name, color, desc, src) {
            presetSaveDialog.editFile = file
            presetSaveDialog.errorText = ""
            presetNameInput.text = name
            presetDescInput.text = desc || ""
            presetSaveDialog.chosenColor = color
            // 수정 모드에선 **저장돼 있던 출처**를 보여주고 고칠 수 있게 한다(룩은 불변).
            presetSaveDialog.src = src || ({})
            presetCamInput.text = presetSaveDialog.src.camera || ""
            presetLensInput.text = presetSaveDialog.src.lens || ""
            presetSaveDialog.open()
        }
        onOpened: presetNameInput.forceActiveFocus()
        onClosed: win._typing = false

        function commit() {
            if (!presetSaveDialog.nameOk) return
            var nm = presetNameInput.text.trim()
            var ds = presetDescInput.text.trim()
            var so = { "camera": presetCamInput.text.trim(),
                       "lens": presetLensInput.text.trim() }
            var f = presetSaveDialog.editing
                ? controller.editPreset(presetSaveDialog.editFile, nm,
                                        presetSaveDialog.chosenColor, ds, so)
                : controller.savePreset(nm, presetSaveDialog.chosenColor, ds,
                                        win.editParams(), so)
            // ⚠️실패 시 닫지 않는다 — 닫으면 입력한 이름·설명·장비가 사라지고, 화면상
            //   "저장됐다가 사라진 레시피"와 구분이 안 된다(쓰기 실패는 읽기전용 폴더·디스크
            //   가득·동명 파일 과다에서 실제로 난다). 콘솔 로그만 남는 것은 안내가 아니다.
            if (f === "") {
                presetSaveDialog.errorText = presetSaveDialog.editing
                    ? "Couldn't save the changes. The recipe folder may be read-only."
                    : "Couldn't save the recipe. The recipe folder may be read-only."
                return
            }
            presetSaveDialog.errorText = ""
            presetSaveDialog.close()
            win.refreshPresets()
            if (!win.secOpen[13]) win.toggleSec(13)
        }

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
                spacing: 10

                Label {
                    Layout.fillWidth: true
                    visible: presetSaveDialog.errorText !== ""
                    text: "⚠ " + presetSaveDialog.errorText
                    color: "#e08a8a"; font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
                Label {
                    text: presetSaveDialog.editing ? "Edit recipe" : "Save as recipe"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    Layout.fillWidth: true
                    text: presetSaveDialog.willOverwrite
                          ? (presetSaveDialog.editing
                             ? "Another recipe already has this name \u2014 saving replaces that recipe."
                             : "A recipe with this name already exists \u2014 saving replaces its look and origin.")
                          : (presetSaveDialog.editing
                             ? "The look stays as saved \u2014 name, colour, description, camera and lens are editable."
                             : "A recipe stores the look only \u2014 not white balance, crop or masks.")
                    color: presetSaveDialog.willOverwrite ? "#E0A226" : "#9a9a9a"
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                // ⚠️입력칸마다 **라벨을 붙인다** — 값이 채워지면 placeholder 가 사라져 어떤
                //   항목인지 알 수 없다는 지적을 받았다(자동 기입되는 카메라/렌즈는 특히).
                Label { text: "Name"; color: "#9a9a9a"; font.pixelSize: 11; Layout.topMargin: 4 }
                // 이름 — TextInput(코어)+Rectangle. 네이티브 TextField 는 배경이 밝아 밝은 글자가
                // 묻힌다(캡션 검색창과 같은 패턴으로 통일).
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    Layout.topMargin: 4
                    radius: 6; color: "#1b1b1c"
                    border.color: presetNameInput.activeFocus ? "#E0A226" : "#55555a"
                    border.width: 1
                    TextInput {
                        id: presetNameInput
                        anchors.fill: parent
                        anchors.leftMargin: 10; anchors.rightMargin: 10
                        verticalAlignment: TextInput.AlignVCenter
                        color: "#f2f2f2"; font.pixelSize: 14
                        clip: true; selectByMouse: true
                        onActiveFocusChanged: win._typing = activeFocus
                        onAccepted: presetDescInput.forceActiveFocus()
                        Keys.onEscapePressed: presetSaveDialog.close()
                        HoverHandler { cursorShape: Qt.IBeamCursor }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: presetNameInput.text === "" && !presetNameInput.activeFocus
                            text: "Recipe name"
                            color: "#6f6f6f"; font.pixelSize: 14
                        }
                    }
                }

                // 카메라 / 렌즈 — EXIF 에서 자동 기입되고 사용자가 고칠 수 있다.
                //   렌즈는 대개 비어 있다(고정렌즈 바디는 태그를 안 쓰고 MakerNote 는 미파싱)
                //   → 손으로 적는 자리. 이 기능의 목적이 "레시피는 장비에 묶여 있다"를 알리는
                //   것이라, 렌즈가 늘 비면 목적이 반쪽이 된다(사용자 요청).
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label { text: "Camera"; color: "#9a9a9a"; font.pixelSize: 11 }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 30
                            radius: 6; color: "#1b1b1c"
                            border.color: presetCamInput.activeFocus ? "#E0A226" : "#55555a"
                            border.width: 1
                            TextInput {
                                id: presetCamInput
                                anchors.fill: parent
                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                verticalAlignment: TextInput.AlignVCenter
                                color: "#e6e6e6"; font.pixelSize: 12
                                clip: true; selectByMouse: true
                                maximumLength: 60          // Controller._SRC_TEXT_MAX 와 같은 값
                                onActiveFocusChanged: win._typing = activeFocus
                                onAccepted: presetLensInput.forceActiveFocus()
                                Keys.onEscapePressed: presetSaveDialog.close()
                                HoverHandler { cursorShape: Qt.IBeamCursor }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: presetCamInput.text === "" && !presetCamInput.activeFocus
                                    text: "not recorded"
                                    color: "#6f6f6f"; font.pixelSize: 12
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label { text: "Lens"; color: "#9a9a9a"; font.pixelSize: 11 }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 30
                            radius: 6; color: "#1b1b1c"
                            border.color: presetLensInput.activeFocus ? "#E0A226" : "#55555a"
                            border.width: 1
                            TextInput {
                                id: presetLensInput
                                anchors.fill: parent
                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                verticalAlignment: TextInput.AlignVCenter
                                color: "#e6e6e6"; font.pixelSize: 12
                                clip: true; selectByMouse: true
                                maximumLength: 60
                                onActiveFocusChanged: win._typing = activeFocus
                                onAccepted: presetDescInput.forceActiveFocus()
                                Keys.onEscapePressed: presetSaveDialog.close()
                                HoverHandler { cursorShape: Qt.IBeamCursor }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: presetLensInput.text === "" && !presetLensInput.activeFocus
                                    text: "e.g. 23mm f/2"
                                    color: "#6f6f6f"; font.pixelSize: 12
                                }
                            }
                        }
                    }
                }

                Label { text: "Description"; color: "#9a9a9a"; font.pixelSize: 11 }
                // 설명 — 이 레시피가 어떤 사진에 맞는지 등을 남기는 자리(공유 시 특히 유용).
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 62
                    radius: 6; color: "#1b1b1c"
                    border.color: presetDescInput.activeFocus ? "#E0A226" : "#55555a"
                    border.width: 1
                    // 줄이 길어지면 스크롤. TextEdit 은 스크롤을 스스로 못 하므로 Flickable 로
                    // 감싼다(ScrollView+TextArea 는 네이티브 스타일 커스터마이즈 경고가 난다).
                    Flickable {
                        id: descFlick
                        anchors.fill: parent
                        anchors.margins: 8
                        anchors.rightMargin: 14        // 스크롤바 자리
                        clip: true
                        contentWidth: width
                        contentHeight: presetDescInput.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {
                            policy: descFlick.contentHeight > descFlick.height
                                    ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                        }
                        // 커서가 보이는 영역 밖으로 나가면 따라 스크롤(타이핑 중 커서 유실 방지)
                        function ensureCursorVisible(r) {
                            if (r.y < contentY) contentY = r.y
                            else if (r.y + r.height > contentY + height)
                                contentY = r.y + r.height - height
                        }
                        TextEdit {
                            id: presetDescInput
                            width: descFlick.width
                            color: "#e6e6e6"; font.pixelSize: 12
                            wrapMode: TextEdit.Wrap
                            selectByMouse: true
                            onActiveFocusChanged: win._typing = activeFocus
                            Keys.onEscapePressed: presetSaveDialog.close()
                            HoverHandler { cursorShape: Qt.IBeamCursor }
                            // 280자 상한(presets.build 와 같은 값) — 붙여넣기까지 막는다
                            onTextChanged: if (length > 280) remove(280, length)
                            onCursorRectangleChanged: descFlick.ensureCursorVisible(cursorRectangle)
                            Text {
                                visible: presetDescInput.text === ""
                                         && !presetDescInput.activeFocus
                                text: "Description (optional) \u2014 e.g. what light or subject it suits"
                                color: "#6f6f6f"; font.pixelSize: 12
                            }
                        }
                    }
                }

                // 구분색 — 지정 팔레트 12색을 한 줄로. 자유 색 선택은 두지 않는다(참고 디자인의
                // 통일감이 제한된 팔레트에서 나오고, 공유받은 레시피도 같은 팔레트가 된다).
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 2
                    spacing: 8
                    Label { text: "Colour"; color: "#9a9a9a"; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    Repeater {
                        model: controller.presetPalette
                        delegate: Rectangle {
                            width: 28; height: 28; radius: 6
                            color: modelData
                            border.color: presetSaveDialog.chosenColor === modelData
                                          ? "#ffffff" : "#00000000"
                            border.width: 2
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: presetSaveDialog.chosenColor = modelData
                            }
                        }
                    }
                }

                // 기록될 출처 미리보기(새로 저장할 때만) — 저장되는 내용을 숨기지 않는다.
                // 스캔이면 스캐너 이름이 그대로 나오는데, 그것이 재현 불가라는 사실을 알려주는
                // 것이 이 기능의 목적이다.
                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    visible: !presetSaveDialog.editing
                    Layout.preferredHeight: srcLabel.implicitHeight + 16
                    radius: 6
                    color: "#1e1e20"
                    border.color: "#3d3d40"; border.width: 1
                    Label {
                        id: srcLabel
                        anchors.fill: parent
                        anchors.margins: 8
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        wrapMode: Text.WordWrap
                        font.pixelSize: 12
                        color: (presetSaveDialog.src && presetSaveDialog.src.camera)
                               ? "#8a8a8a" : "#E0A226"
                        text: {
                            var t = presetSaveDialog.src || {}
                            var a = []
                            if (t.camera) a.push(t.camera)
                            if (t.lens) a.push(t.lens)
                            else if (t.focalLength) a.push(t.focalLength)
                            return a.length > 0
                                ? "Recording:  " + a.join("   \u00b7   ")
                                : "This photo has no camera info, so this recipe will record none."
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    spacing: 12
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: psCancelMA.containsMouse ? "#3a3a3d" : "#2e2e31"
                        border.color: "#55555a"; border.width: 1
                        Label {
                            anchors.centerIn: parent; text: "Cancel"
                            color: "#e6e6e6"; font.pixelSize: 13
                        }
                        MouseArea {
                            id: psCancelMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: presetSaveDialog.close()
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        opacity: presetSaveDialog.nameOk ? 1.0 : 0.45
                        color: psOkMA.containsMouse && presetSaveDialog.nameOk
                               ? "#f0b945" : "#E0A226"
                        Label {
                            anchors.centerIn: parent
                            text: presetSaveDialog.willOverwrite
                                  ? (presetSaveDialog.editing ? "Replace" : "Overwrite")
                                  : (presetSaveDialog.editing ? "Update" : "Save")
                            color: "#1a1a1a"; font.pixelSize: 13; font.bold: true
                        }
                        MouseArea {
                            id: psOkMA; anchors.fill: parent; hoverEnabled: true
                            enabled: presetSaveDialog.nameOk
                            cursorShape: Qt.PointingHandCursor
                            onClicked: presetSaveDialog.commit()
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

    // ── 레시피 확인 대화상자(삭제 / 룩 덮어쓰기 공용) ──
    // 둘 다 되돌릴 수 없는 동작이라 같은 형태로 묻는다(종료 대화상자와 동일 컨셉).
    Popup {
        id: presetConfirmDialog
        modal: true
        dim: true
        width: 400
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }
        readonly property bool isDelete: win._presetConfirmMode === "delete"
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
                    text: presetConfirmDialog.isDelete ? "Delete recipe?" : "Overwrite this recipe?"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    Layout.fillWidth: true
                    text: presetConfirmDialog.isDelete
                          ? "\u201c" + win._presetCtxName + "\u201d will be removed. "
                            + "Photos already edited with it keep their edits."
                          : "\u201c" + win._presetCtxName + "\u201d will store the look you have "
                            + "now, and record this photo as its origin. The previous look is lost."
                    color: "#9a9a9a"; font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 12
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: pcCancelMA.containsMouse ? "#3a3a3d" : "#2e2e31"
                        border.color: "#55555a"; border.width: 1
                        Label {
                            anchors.centerIn: parent; text: "Cancel"
                            color: "#e6e6e6"; font.pixelSize: 13
                        }
                        MouseArea {
                            id: pcCancelMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: presetConfirmDialog.close()
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: pcOkMA.containsMouse ? "#f0b945" : "#E0A226"
                        Label {
                            anchors.centerIn: parent
                            text: presetConfirmDialog.isDelete ? "Delete" : "Overwrite"
                            color: "#1a1a1a"; font.pixelSize: 13; font.bold: true
                        }
                        MouseArea {
                            id: pcOkMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (presetConfirmDialog.isDelete) {
                                    controller.deletePreset(win._presetCtxFile)
                                } else {
                                    controller.updatePresetLook(win._presetCtxFile,
                                                               win.editParams())
                                }
                                presetConfirmDialog.close()
                                win.refreshPresets()
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

    // ── 레시피 가져오기/내보내기(공유) ──
    // \u26a0 필터\u00b7제안 파일명\u00b7defaultSuffix 를 함께 맞춘다 \u2014 확장자가 곧 정체성이다(saveDialog 주석).
    FileDialog {
        id: presetImportDialog
        title: "Import recipe"
        fileMode: FileDialog.OpenFile
        nameFilters: ["Film Rawstery recipe (*.frpreset)"]
        onAccepted: {
            var f = controller.importPreset(selectedFile)
            if (f !== "") {
                win.refreshPresets()
                if (!win.secOpen[13]) win.toggleSec(13)
            } else {
                win.presetNotice = "That file is not a valid Film Rawstery recipe."
                win.presetNoticeWarn = true
            }
        }
    }
    FileDialog {
        id: presetExportDialog
        title: "Export recipe"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Film Rawstery recipe (*.frpreset)"]
        defaultSuffix: "frpreset"
        property string sourceFile: ""
        onAccepted: controller.exportPreset(presetExportDialog.sourceFile, selectedFile)
    }

    FileDialog {
        id: wallpaperSaveDialog
        title: "Export Wallpaper"
        fileMode: FileDialog.SaveFile
        nameFilters: ["JPEG (*.jpg)", "PNG (*.png)"]
        defaultSuffix: "jpg"
        onAccepted: win.wallStart(selectedFile)
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
                enabled: !win.batchActive && !win.wallActive   // 배치/배경화면 실행 중 파일 전환·폴더 변경 차단(취소는 오버레이 버튼)

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

                // 헤더 2줄: 폴더 통계(좌) + ♥ 좋아요만 / ⧉ 짝 펼치기 / ☑ 배치 선택(우, 컴팩트)
                // 통계는 전체 폴더 기준(좋아요 필터 무관). fileList(folderChanged)·editsRevision·
                // likeRevision 참조로 변경 시 자동 재계산.
                // ⚠️패널 폭 260 고정이라 이 줄은 늘 빠듯하다. **글자 크기(11px)는 줄이지 말 것** —
                //   대신 구분자 여백('  ·  '→' · ')과 버튼 폭(24px)에서 벌어 뒀다. 실측(실폰트):
                //   버튼 3개 기준 가용 160px vs 최악 문구 157.2px("1000 photos · 999 edited · 999 ♥").
                //   버튼을 더 붙이려면 24×n+4×n 만큼 다시 계산할 것(하나 더 = 28px 부족).
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Label {
                        objectName: "folderStatsLabel"
                        Layout.fillWidth: true
                        visible: controller.currentFolder !== ""
                        readonly property var stats: {
                            controller.likeRevision; controller.editsRevision
                            var files = controller.fileList
                            var n = 0, liked = 0, edited = 0
                            for (var i = 0; i < files.length; i++) {
                                // 짝 JPEG 은 같은 사진 — 목록에 접혀 있으므로 수에도 넣지 않는다
                                // (RAW+JPEG 폴더에서 1000 이 아니라 503 이 '사진 수'다).
                                if (files[i].isDir || files[i].paired) continue
                                n++
                                if (controller.hasEdits(files[i].path)) edited++
                                if (controller.isLiked(files[i].path)) liked++
                            }
                            return [n, edited, liked]
                        }
                        textFormat: Text.StyledText
                        text: stats[0] + " photos" +
                              (stats[1] > 0 ? " · <font color='#E0A226'>" + stats[1] + " edited</font>" : "") +
                              (stats[2] > 0 ? " · <font color='#ff6b6b'>" + stats[2] + " ♥</font>" : "")
                        color: "#7f7f7f"
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                    Item { visible: controller.currentFolder === ""; Layout.fillWidth: true }
                    // "좋아요만 보기" 토글 — ♥(채움)/♡(빈) 글리프로 활성/비활성 표시
                    Rectangle {
                        id: likeFilterBtn
                        Layout.preferredWidth: 24     // 폭 24 = 통계 문구 자리 확보(위 주석). 글리프는 그대로 14px
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
                    // 짝 JPEG 펼치기 토글 — 카메라 RAW+JPEG 동시기록 폴더에서만 보인다.
                    // 접힌 상태(기본)에서는 RAW 행에 JPG 배지가 붙어 짝이 있음을 알려준다.
                    Rectangle {
                        id: pairBtn
                        visible: controller.folderHasPairs
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 22
                        radius: 4
                        color: win.showPairedImages ? "#2a3340"
                             : (pbHover.hovered ? "#3a3f4b" : "transparent")
                        border.color: win.showPairedImages ? "#7fb3e0" : "#555555"
                        border.width: 1
                        ToolTip.visible: pbHover.hovered
                        ToolTip.text: win.showPairedImages
                            ? "Paired JPEGs shown as separate photos (P)"
                            : "Paired JPEGs folded into their RAW (P)"
                        Text {
                            anchors.centerIn: parent
                            text: "⧉"
                            color: win.showPairedImages ? "#7fb3e0" : "#cfcfcf"
                            font.pixelSize: 14
                        }
                        HoverHandler { id: pbHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.showPairedImages = !win.showPairedImages
                        }
                    }
                    // 배치 export 선택(체크박스) 모드 토글 — 켜면 파일 클릭=체크, 하단에 Export 바.
                    Rectangle {
                        id: selModeBtn
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 22
                        radius: 4
                        color: win.batchSelectMode ? "#2e3a2a"
                             : (smHover.hovered ? "#3a3f4b" : "transparent")
                        border.color: win.batchSelectMode ? "#9fd39f" : "#555555"
                        border.width: 1
                        ToolTip.visible: smHover.hovered
                        ToolTip.text: "Select files for batch export  (Shift+click = range)"
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
                            // 짝 JPEG 은 같은 사진 — 캡션을 두 번 만들지 않는다(접힘 상태와 무관).
                            if (it.paired) continue
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
                        // 이 폴더 인덱싱 중=✕, 다른 폴더 인덱싱 중=⋯(정보), 유휴=⚙
                        // ⚠️글리프 대신 SVG — 기존 pixelSize 9 는 macOS 폴백 폰트에서 특히 작고
                        //   얇아 잘 안 보였다(Windows 의 Segoe UI Symbol 기준으로 맞춰진 값).
                        //   옆 태그 버튼과 같은 13px 로 통일한다. ⋯ 는 흰색 파일을 재사용하고
                        //   '다른 폴더 진행 중'의 흐린 톤은 opacity 로 낸다(#888 상당).
                        Image {
                            anchors.centerIn: parent
                            source: idxRow.indexingHere ? "../assets/icons/close.svg"
                                    : (controller.indexBusy ? "../assets/icons/more.svg"
                                                            : "../assets/icons/gear.svg")
                            opacity: (!idxRow.indexingHere && controller.indexBusy) ? 0.55 : 1.0
                            width: 14; height: 14
                            sourceSize.width: 28; sourceSize.height: 28   // HiDPI 선명도
                            smooth: true
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
                        // ⚠️U+1F3F7 은 macOS 에서 Apple Color Emoji 로만 그려져(단색 글리프 없음,
                        //   VS15 도 무효) color 지정이 무시되고 회색 툴바에 주황 이모지가 박혔다.
                        //   → SVG 아이콘으로 교체(양 OS 동일, 색은 파일에 #cfcfcf 로 고정).
                        Image { anchors.centerIn: parent
                                source: "../assets/icons/tag.svg"
                                width: 14; height: 14                          // 좌측 인덱스 버튼과 동일 크기
                                sourceSize.width: 28; sourceSize.height: 28   // HiDPI 선명도
                                smooth: true }
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
                                    id: rowThumb
                                    Layout.preferredWidth: modelData.isDir ? 20 : 84
                                    Layout.preferredHeight: modelData.isDir ? 20 : 56
                                    Layout.alignment: Qt.AlignVCenter
                                    // 배지 기준 사각형 — 사진이 그려진 영역. ⚠️디코드 실패
                                    // (`Image.Error`)면 painted 가 0 이라 배지가 칸 한가운데로
                                    // 간다 → 그때는 칸 전체로 폴백(컨택트 시트와 같은 규칙).
                                    readonly property real pw: thumbImg.paintedWidth > 0
                                                               ? thumbImg.paintedWidth : width
                                    readonly property real ph: thumbImg.paintedHeight > 0
                                                               ? thumbImg.paintedHeight : height

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
                                    // 짝 JPEG 배지 — 이 RAW 옆에 같은 이름의 JPEG 이 접혀 있다는 표시.
                                    // 펼친 상태에서는 실제 행이 따로 보이므로 배지를 숨긴다.
                                    // ⚠️'JPG' 만 쓰면 이 행이 **JPEG 파일처럼** 읽힌다(파일명에는
                                    //   .RAF 가 붙어 있는데 배지가 더 눈에 띔) → 앞에 '+' 를 붙여
                                    //   '덤으로 딸려 있다'로 읽히게 하고 색도 회색으로 낮춘다.
                                    Rectangle {
                                        visible: !modelData.isDir && !win.showPairedImages
                                                 && modelData.pair !== undefined
                                        // ⚠️이것만 **칸 모서리** 기준으로 남긴다. 셋 다 사진 사각형에
                                        //   붙였더니 세로 사진(사진 폭 37px)에서 아래 줄의 +JPG(22px)와
                                        //   ♥(14px)가 겹쳤다. 좌하단에 남겨 두면 ♥ 가 사진 오른쪽 끝으로
                                        //   와도 22px 이상 벌어진다(세로 1:3 극단에서도 안전).
                                        anchors.left: parent.left
                                        anchors.bottom: parent.bottom
                                        anchors.margins: 1
                                        width: pairTxt.implicitWidth + 6
                                        height: pairTxt.implicitHeight + 2
                                        radius: 2
                                        color: "#99000000"
                                        border.color: "#6a6a6a"
                                        border.width: 1
                                        Text {
                                            id: pairTxt
                                            anchors.centerIn: parent
                                            text: modelData.pair !== undefined ? "+" + modelData.pair : ""
                                            color: "#9a9a9a"
                                            font.pixelSize: 9
                                        }
                                    }
                                    // 좋아요(셀렉트) 하트 배지 — likeRevision 참조로 토글/폴더변경 시 갱신
                                    Text {
                                        // 사진이 그려진 사각형 기준(편집 배지와 같은 규칙) — 칸 기준이면
                                        // 가로/세로 사진에서 붙는 자리가 달라 보인다(사용자 보고).
                                        x: (rowThumb.width + rowThumb.pw) / 2 - width - 1
                                        y: (rowThumb.height + rowThumb.ph) / 2 - height - 1
                                        text: "♥"
                                        color: "#ff6b6b"
                                        style: Text.Outline
                                        styleColor: "#000000"
                                        font.pixelSize: 14
                                        // 로드 전에는 감춘다(편집 배지와 같은 이유 — 위치가 사진
                                        // 기준이라 paintedWidth=0 이면 칸 한가운데로 간다).
                                        // ⚠️`Image.Error`(임베드 프리뷰 없는 DNG 등)는 **영원히**
                                        //   Ready 가 아니다 — 그 파일만 배지가 통째로 사라지므로
                                        //   'No preview' 자리 위에라도 띄운다.
                                        visible: {
                                            controller.likeRevision
                                            return thumbImg.status !== Image.Loading
                                                   && !modelData.isDir
                                                   && controller.isLiked(modelData.path)
                                        }
                                    }
                                    // 편집됨 배지(우상단) — 파일명 앰버만으로는 약하다는 피드백.
                                    // 도안·근거는 EditedBadge.qml 주석.
                                    EditedBadge {
                                        // ⚠️**사진이 그려진 사각형** 기준. 칸 모서리에 붙이면 가로
                                        //   사진은 사진 위, 세로 사진은 사진 밖(빈 칸)에 놓여 같은
                                        //   배지가 사진마다 다르게 붙은 것처럼 보인다(사용자 보고).
                                        x: (rowThumb.width + rowThumb.pw) / 2 - width - 1
                                        y: (rowThumb.height - rowThumb.ph) / 2 + 1
                                        ready: thumbImg.status !== Image.Loading
                                        path: modelData.isDir ? "" : modelData.path
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
                                    if (mouse.modifiers & Qt.ShiftModifier)
                                        win.batchSelectRange(row.index)       // shift = 기준 행부터 연속 체크
                                    else
                                        win.batchToggle(row.modelData.path)   // 선택 모드 = 체크 토글
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
                            // 위 exportOptPopup 과 같은 비모달 인라인 팝업 — 시인성 톤 통일
                            background: Rectangle { color: "#2f3238"; border.color: "#6f737a"; border.width: 1; radius: 8 }
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

                // 푸터: AI 모델 현황 + GitHub 저장소 링크 + (있으면) 새 버전 배지
                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }
                // AI 모델 관리 진입점 — 사진과 무관한 앱 전역 항목이라 업데이트 배지와 같은 자리.
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 24
                    color: "transparent"
                    ToolTip.visible: mdlHover.hovered
                    ToolTip.text: "Downloaded AI models — check status and pre-download"
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "AI Models ↗"
                        color: mdlHover.hovered ? "#8ab4f8" : "#8a8a8a"
                        font.pixelSize: 12
                        font.underline: mdlHover.hovered
                    }
                    Text {                       // 우측: 진행률(다운로드 중) 또는 미설치 용량
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: controller.modelDownloading !== ""
                              ? Math.round(controller.modelProgress * 100) + "%"
                              : (controller.modelSummary.missingBytes === 0
                                 ? controller.modelSummary.installedText
                                 : controller.modelSummary.missingText + " missing")
                        color: controller.modelDownloading !== "" ? "#E0A226" : "#6a6a6a"
                        font.pixelSize: 11
                    }
                    HoverHandler { id: mdlHover }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: modelDialog.open()
                    }
                }
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

            // 로컬 마스크 텍스처(레이어별, 프록시 크기 단일채널). 셰이더가 레이어별 로컬조정에 게이팅.
            Image { id: skyMaskImage0; visible: false; cache: false; smooth: true; source: controller.layerMaskUrls[0] }
            Image { id: skyMaskImage1; visible: false; cache: false; smooth: true; source: controller.layerMaskUrls[1] }
            Image { id: skyMaskImage2; visible: false; cache: false; smooth: true; source: controller.layerMaskUrls[2] }
            Image { id: skyMaskImage3; visible: false; cache: false; smooth: true; source: controller.layerMaskUrls[3] }
            Image { id: skyMaskImage4; visible: false; cache: false; smooth: true; source: controller.layerMaskUrls[4] }

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

            // 미스트 산란 필드 3장(narrow/mid/wide). 카메라네이티브 scene-linear 를 **로그 코덱**
            // 으로 담은 16bit(coeffs.MIST_TEX_* 주석 — 8bit 로 떨어져도 등고선이 안 생기게).
            // 각기 σ 에 맞는 축소 해상도라 **smooth:true(bilinear 업샘플) 전제**다.
            // 준비 전(1x1)엔 셰이더 mistOn 게이트가 미스트를 끔. (Radius, Highlight) 당 1회 갱신.
            Image { id: mistImage0; visible: false; cache: false; smooth: true; source: controller.mistUrl0 }
            Image { id: mistImage1; visible: false; cache: false; smooth: true; source: controller.mistUrl1 }
            Image { id: mistImage2; visible: false; cache: false; smooth: true; source: controller.mistUrl2 }

            // ── GPU export: 풀해상도를 프리뷰와 **동일한 adjust.frag** 로 렌더(프리뷰=Export) ──
            //   온디맨드(렌더=GPU 일 때만 active). src 만 풀해상도, 블러 텍스처는 프록시 것 재사용
            //   (로컬대비/톤마스크 성격을 프리뷰와 동일하게). uniform 바인딩은 pipe 와 반드시 동일.
            Loader {
                id: gpuExportLoader
                active: false
                sourceComponent: Component { Item {
                    property bool grabPending: false
                    function doGrab() {
                        // ⚠️grabToImage 는 **요청 크기 × DPR** 픽셀의 이미지를 돌려준다
                        //   (실측 dpr=2: 301x203 요청 → 602x406). 논리 크기를 그대로 요청하면
                        //   FBO 가 출력 픽셀의 DPR 배가 되어 ①그레인이 DPR 배 밀도로 계산되고
                        //   ②2x2 서브픽셀 오프셋(grainTexelW/H = 1/width)이 출력 1px 이 아니라
                        //   DPR px 을 덮고 ③파이썬이 사후 축소하며 평균돼, Retina 에서 평탄부
                        //   σ 가 CPU export 대비 −22% 로 약해졌다(문서의 '풀해상도 후 CPU 축소'
                        //   실패와 같은 형태). DPR 로 나눠 요청해 **FBO = 출력 픽셀 수**로 맞춘다
                        //   → 그레인이 출력 해상도에서 계산되고 축소가 사라진다(배율 100% 면 no-op).
                        //   홀수 치수는 축당 최대 DPR-1 px 더 오고, 그 여유분은 파이썬이 잘라낸다
                        //   (_GRAB_SLACK_PX — 재샘플 금지).
                        var dpr = Math.max(1, Screen.devicePixelRatio)
                        pipeFull.grabToImage(function(res) {
                            controller.saveGrab(res.image)
                            Qt.callLater(function() { gpuExportLoader.active = false })
                        }, Qt.size(Math.ceil(pipeFull.width / dpr), Math.ceil(pipeFull.height / dpr)))
                    }
                    Image {
                        // mipmap: 프리셋 렌더에서 셰이더가 풀해상도 소스를 축소 샘플링하므로
                        // 트라이리니어 필수(없으면 이미지 내용이 에일리어싱).
                        id: srcFull; visible: false; cache: false; smooth: true; mipmap: true
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
                        // 해상도 프리셋이 있으면 **처음부터 그 크기로 렌더** — 그레인이 출력
                        // 해상도에서 계산돼 CPU 프리셋 경로와 셀 크기·σ 가 일치한다(긴 변 보정).
                        // 풀해상도 렌더 후 CPU 축소 방식은 그레인이 평균돼 약해졌고(σ −18%),
                        // 26MP 축소·인코딩이 메인 스레드를 잡아 멈춤의 주범이기도 했다.
                        readonly property real fullScale: {
                            var w0 = srcFull.implicitWidth, h0 = srcFull.implicitHeight
                            if (w0 <= 0 || h0 <= 0) return 1.0
                            var e = win.gpuExportEdge, l = Math.max(w0, h0)
                            return (e > 0 && e < l) ? e / l : 1.0
                        }
                        width: Math.max(1, Math.round(srcFull.implicitWidth * fullScale))
                        height: Math.max(1, Math.round(srcFull.implicitHeight * fullScale))
                        visible: false
                        // ⚠️아래 uniform 바인딩은 pipe 와 동일하게 유지해야 함(프리뷰=Export).
                        property variant src: srcFull
                        property variant dispSrc: dispSrcTex
                        property variant lut: lutImage
                        property variant curve: curveImage
                        property variant texBlur: texBlurTex
                        property variant claBlur: claBlurTex
                        property variant sharpBlur: sharpBlurTex
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: 0.0   // 스탬프는 셰이더(원본 코너)가 아니라 cropClip 위 stampOverlay 가 최종 프레임 기준으로 그림
                        property real stampStrength: 0.92
                        property real exposure: expSlider.value
                        // 필름시뮬 보정 노출 — LUT 에 든 후지 톤커브가 filmic 위에 두 번 걸리는
                        // 것을 상쇄(pipeline.film_sim_ev). 이미지×시뮬 상수라 슬라이더가 아니다.
                        // ⚠️pipe/pipeFull/pipeline 세 곳 동일해야 함(프리뷰=Export).
                        property real simExpEV: controller.simExpEV
                        // 자동노출 끄기 오프셋 — 재디코드 대신 노출 지수에서 뺀다.
                        // ⚠️pipe/pipeFull/pipeline 세 곳 동일해야 함(프리뷰=Export).
                        property real autoExpEV: controller.autoExposureOffsetEV
                        // 센서 포화 레벨 — 하이라이트 디새추를 '진짜 클립'에서만 걸리게 하는 게이트
                        // 기준(raw_loader.clip_level). ⚠️pipe/pipeFull/comparePipe/pipeline 네 곳 동일.
                        property real clipLevel: controller.clipLevel
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
                        property real grainRough: grainRoughSlider.value
                        property real grainColor: grainColorSlider.value
                        // ⚠️export 는 폴백 없이 항상 체크값 — 이 인스턴스의 grab 이 저장
                        // 파일이 된다. 드래그 폴백을 여기 걸면 grab 순간 마우스가 눌려 있을 때
                        // 사각 그레인이 조용히 구워진다(프리뷰 전용 근사가 결과물에 새는 버그).
                        property real grainShape: grainShapeCheck.checked ? 1.0 : 0.0
                        property real grainAspect: width / Math.max(1, height)
                        // 그레인 서브픽셀 평균용 출력 텍셀 — **이 인스턴스의 실제 렌더 크기** 기준.
                        // (texelW 는 샤프닝 공간스케일용이라 pipeFull 에서도 프록시 텍셀 → 쓰면 안 됨)
                        property real grainTexelW: 1.0 / Math.max(1, width)
                        property real grainTexelH: 1.0 / Math.max(1, height)
                        property real clipWarn: 0.0   // export 는 클리핑 오버레이 미적용
                        property real zoneShow: 0.0   // export 는 존 시스템 오버레이 미적용
                        property real displayCM: 0.0  // export 는 디스플레이 색관리 미적용(표준 sRGB)
                        // 하이라이트 디새추(센서 클립 색끼 제거)는 RAW 전용 — 일반 이미지 입력에서는
                        // 밝은 파랑/청록이 정상 색이라 끈다. ⚠️pipe/pipeFull/pipeline 세 곳 동일해야 함.
                        property real hlDesat: controller.isDisplayImage ? 0.0 : 1.0
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
                        // 로컬 마스크 레이어(5) — win.layers 에서 vec4 유니폼. export 는 오버레이 없음(-1).
                        property variant skyMask0: skyMaskImage0
                        property variant skyMask1: skyMaskImage1
                        property variant skyMask2: skyMaskImage2
                        property variant skyMask3: skyMaskImage3
                        property variant skyMask4: skyMaskImage4
                        property vector4d skyA0: win.skyA0; property vector4d skyB0: win.skyB0; property vector4d skyC0: win.skyC0
                        property vector4d skyA1: win.skyA1; property vector4d skyB1: win.skyB1; property vector4d skyC1: win.skyC1
                        property vector4d skyA2: win.skyA2; property vector4d skyB2: win.skyB2; property vector4d skyC2: win.skyC2
                        property vector4d skyA3: win.skyA3; property vector4d skyB3: win.skyB3; property vector4d skyC3: win.skyC3
                        property vector4d skyA4: win.skyA4; property vector4d skyB4: win.skyB4; property vector4d skyC4: win.skyC4
                        property real skyShowLayer: -1.0
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
                        property real grainToneK: controller.adjustCoeffs["grainToneK"]
                        property real grainToneGammaK: controller.adjustCoeffs["grainToneGammaK"]
                        property real grainToneFloorK: controller.adjustCoeffs["grainToneFloorK"]
                        property real grainSkewK: controller.adjustCoeffs["grainSkewK"]
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
                        // 미스트(1단계) — 커널 합성은 mistFieldPass 가 이미 했다(샘플러 슬롯 절약).
                        // ⚠️binding 6 은 원래 stampTex 였다. adjust.frag 는 D3D11 상한인 16개를
                        //   정확히 쓰고 있으니 새 sampler 를 추가하지 말 것(셰이더 주석 참조).
                        property variant mistScat: mistFieldTex
                        property real mistAmt: mistAmtSlider.value
                        property real mistOn: controller.mistOn
                        property real mistK: controller.adjustCoeffs["mistK"]
                        property real mistLogA: controller.adjustCoeffs["mistLogA"]
                        property real mistLogK: controller.adjustCoeffs["mistLogK"]
                        property real mistColor: mistColorSlider.value
                        property real mistColorFloor: controller.adjustCoeffs["mistColorFloor"]
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
                        width: parent.width - 20 - (gridToggle.visible ? gridToggle.width + 8 : 0)
                        text: controller.imagePath !== ""
                              ? controller.imagePath
                              : "No file open"
                    }
                    // 격자 토글 — ⚠️예전에는 캔버스 좌하단에 떠 있는 버튼이었는데 **사진 위에
                    // 떠 있는 게 어색하다**는 보고로 여기(창 크롬)로 옮겼다. 자리가 고정이고
                    // 사진을 가리지 않는다. 탐색기 툴바(♥/⧉/☑) 옆은 통계 문구 폭 예산이 이미
                    // 버튼 3개 기준이라 넣지 않았다(위 '버튼 3개 기준 가용 160px' 주석).
                    Rectangle {
                        id: gridToggle
                        visible: contactSheet.photos.length > 0
                        anchors.right: parent.right
                        anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        width: 22; height: 20; radius: 4
                        color: win.gridPinned ? "#2a3340"
                             : (gtHover.hovered ? "#3a3f4b" : "transparent")
                        border.color: win.gridPinned ? "#7fb3e0" : "#555555"
                        border.width: 1
                        ToolTip.visible: gtHover.hovered
                        ToolTip.text: "Browse this folder as a grid (G)"
                        // ⚠️글리프(▦ 등) 대신 사각형 4개로 직접 그린다 — 폰트에 그 문자가 없으면
                        //   두부(□)가 뜬다. 여기 아이콘은 폴백을 확인할 방법이 마땅치 않다.
                        Grid {
                            anchors.centerIn: parent
                            columns: 2; spacing: 2
                            Repeater {
                                model: 4
                                Rectangle {
                                    width: 5; height: 4; radius: 1
                                    color: win.gridPinned ? "#7fb3e0" : "#cfcfcf"
                                }
                            }
                        }
                        HoverHandler { id: gtHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.gridPinned = !win.gridPinned
                        }
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
                        // pipe/pipeFull 과 동일한 게이트 — 이 값이 어긋나면 Compare original 이
                        // 편집하지 않은 차이를 보여준다(displaycm.frag 주석 참조).
                        property real hlDesat: controller.isDisplayImage ? 0.0 : 1.0
                        // 디새추 게이트가 센서 클립 근접도라 원본(카메라네이티브) 프록시가 필요하다.
                        property variant rawSrc: srcImage
                        property real clipLevel: controller.clipLevel
                        fragmentShader: "../shaders/displaycm.frag.qsb"
                    }

                    // --- 미스트 산란 필드 합성 (mistfield.frag) ---
                    // CPU 가 만든 3개 스케일 필드를 Character 무게로 섞어 한 장으로 굽는다.
                    // 출력은 로그 코덱 코드라 항상 [0,1] — RGBA8 로 떨어져도 **잘리지 않는다**.
                    // RGBA16F 는 정밀도 선택이다(코덱+디더가 8bit 에서도 견디게 해 두었으므로
                    // 룩의 정확성이 이 포맷에 의존하지 않는다 — coeffs.MIST_TEX_* 주석).
                    // Character 를 움직이면 이 패스만 다시 돈다(프록시 1패스 = 사실상 공짜).
                    ShaderEffect {
                        id: mistFieldPass
                        visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant mistS0: mistImage0
                        property variant mistS1: mistImage1
                        property variant mistS2: mistImage2
                        property real mistChar: mistCharSlider.value
                        property real mistLogA: controller.adjustCoeffs["mistLogA"]
                        property real mistLogK: controller.adjustCoeffs["mistLogK"]
                        property real mistMeanR: controller.mistMeanR
                        property real mistMeanG: controller.mistMeanG
                        property real mistMeanB: controller.mistMeanB
                        property vector4d mistWBlack: win.vec4Of(controller.adjustCoeffs["mistWBlack"])
                        property vector4d mistWWhite: win.vec4Of(controller.adjustCoeffs["mistWWhite"])
                        fragmentShader: "../shaders/mistfield.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: mistFieldTex; sourceItem: mistFieldPass; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        format: ShaderEffectSource.RGBA16F
                        hideSource: true; live: true
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
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: 0.0   // 스탬프는 셰이더(원본 코너)가 아니라 cropClip 위 stampOverlay 가 최종 프레임 기준으로 그림
                        property real stampStrength: 0.92
                        property real exposure: expSlider.value
                        // 필름시뮬 보정 노출 — LUT 에 든 후지 톤커브가 filmic 위에 두 번 걸리는
                        // 것을 상쇄(pipeline.film_sim_ev). 이미지×시뮬 상수라 슬라이더가 아니다.
                        // ⚠️pipe/pipeFull/pipeline 세 곳 동일해야 함(프리뷰=Export).
                        property real simExpEV: controller.simExpEV
                        // 자동노출 끄기 오프셋 — 재디코드 대신 노출 지수에서 뺀다.
                        // ⚠️pipe/pipeFull/pipeline 세 곳 동일해야 함(프리뷰=Export).
                        property real autoExpEV: controller.autoExposureOffsetEV
                        // 센서 포화 레벨 — 하이라이트 디새추를 '진짜 클립'에서만 걸리게 하는 게이트
                        // 기준(raw_loader.clip_level). ⚠️pipe/pipeFull/comparePipe/pipeline 네 곳 동일.
                        property real clipLevel: controller.clipLevel
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
                        property real grainRough: grainRoughSlider.value
                        property real grainColor: grainColorSlider.value
                        // 드래그 중엔 사각 셀로 폴백 — 원판은 계산이 무거워 어떤 슬라이더를
                        // 움직여도 매 프레임 재계산돼 끊긴다. WB 드래그 근사와 같은 패턴
                        // (드래그=빠른 근사, 릴리즈=정확). ⚠️게이트는 editSliderDragActive —
                        // editDragActive(globalPress 포함)를 쓰면 모든 클릭에서 결이 깜빡인다.
                        property real grainShape: (grainShapeCheck.checked && !win.editSliderDragActive) ? 1.0 : 0.0
                        property real grainAspect: viewport.procW / Math.max(1, viewport.procH)
                        // 그레인 서브픽셀 평균용 출력 텍셀 — **이 인스턴스의 실제 렌더 크기** 기준.
                        // (texelW 는 샤프닝 공간스케일용이라 pipeFull 에서도 프록시 텍셀 → 쓰면 안 됨)
                        property real grainTexelW: 1.0 / Math.max(1, width)
                        property real grainTexelH: 1.0 / Math.max(1, height)
                        property real clipWarn: win.clipWarn ? 1.0 : 0.0   // 클리핑 경고 오버레이(프리뷰 전용)
                        property real zoneShow: win.zoneOverlay ? 1.0 : 0.0 // 존 시스템 오버레이(프리뷰 전용)
                        // 하이라이트 디새추 게이트 — pipeFull(GPU export)/pipeline 과 동일해야 함(위 주석).
                        property real hlDesat: controller.isDisplayImage ? 0.0 : 1.0
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
                        // 로컬 마스크 레이어(3) — win.layers vec4 유니폼. 오버레이=활성 레이어(프리뷰 전용).
                        property variant skyMask0: skyMaskImage0
                        property variant skyMask1: skyMaskImage1
                        property variant skyMask2: skyMaskImage2
                        property variant skyMask3: skyMaskImage3
                        property variant skyMask4: skyMaskImage4
                        property vector4d skyA0: win.skyA0; property vector4d skyB0: win.skyB0; property vector4d skyC0: win.skyC0
                        property vector4d skyA1: win.skyA1; property vector4d skyB1: win.skyB1; property vector4d skyC1: win.skyC1
                        property vector4d skyA2: win.skyA2; property vector4d skyB2: win.skyB2; property vector4d skyC2: win.skyC2
                        property vector4d skyA3: win.skyA3; property vector4d skyB3: win.skyB3; property vector4d skyC3: win.skyC3
                        property vector4d skyA4: win.skyA4; property vector4d skyB4: win.skyB4; property vector4d skyC4: win.skyC4
                        // 빨간 오버레이는 **마스킹 패널이 활성일 때만** — 체크(showSkyMask)는
                        // 보존되므로 패널로 돌아오면 다시 보인다(끄는 게 아니라 숨기는 것).
                        property real skyShowLayer: (win.showSkyMask && win.activePanel === 2)
                                                    ? win.activeLayer : -1.0
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
                        property real grainToneK: controller.adjustCoeffs["grainToneK"]
                        property real grainToneGammaK: controller.adjustCoeffs["grainToneGammaK"]
                        property real grainToneFloorK: controller.adjustCoeffs["grainToneFloorK"]
                        property real grainSkewK: controller.adjustCoeffs["grainSkewK"]
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
                        // 미스트(1단계) — 커널 합성은 mistFieldPass 가 이미 했다(샘플러 슬롯 절약).
                        // ⚠️binding 6 은 원래 stampTex 였다. adjust.frag 는 D3D11 상한인 16개를
                        //   정확히 쓰고 있으니 새 sampler 를 추가하지 말 것(셰이더 주석 참조).
                        property variant mistScat: mistFieldTex
                        property real mistAmt: mistAmtSlider.value
                        property real mistOn: controller.mistOn
                        property real mistK: controller.adjustCoeffs["mistK"]
                        property real mistLogA: controller.adjustCoeffs["mistLogA"]
                        property real mistLogK: controller.adjustCoeffs["mistLogK"]
                        property real mistColor: mistColorSlider.value
                        property real mistColorFloor: controller.adjustCoeffs["mistColorFloor"]

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
                        // 날짜 스탬프(필름 데이트백) — cropClip(=최종 크롭 프레임) 코너에 배치.
                        // ⚠️합성은 **export 와 같은 식**(screen 70% + source-over 30%)이어야 한다. 예전엔
                        //   평범한 source-over Image 였는데, 실측하면 밝은 배경에서 프리뷰가 export 보다
                        //   최대 105코드(41%) 진하다 — 어두운 배경에선 2코드라 오래 눈에 안 띈 것이다.
                        //   그래서 shaders/stamp.frag(배경을 읽어 screen 혼합)을 배선한다.
                        // ⚠️예전 배선이 철회된 원인은 **전체 배경 재캡처**였다(줌/레이어에서 배경이 밀리고
                        //   가장자리 검정선). 여기서는 배경을 스탬프 사각형만 sourceRect 로 캡처하고,
                        //   스탬프를 canvasHolder 의 **형제**로 두어 캡처에 자기 자신이 들어가지 않게 한다
                        //   (피드백 없음). 소스 사각형 = 스탬프 사각형이므로 bgMap 은 항등이다.
                        // 날짜 스탬프(필름 데이트백) 오버레이 — cropClip(=최종 크롭 프레임) 코너에 배치.
                        // 스프라이트(image://stamp)에 '검정 위 글로우 하이브리드'가 이미 베이크돼 있어
                        // (date_stamp.render_sprite), 배경 재캡처 없이 QML 기본 source-over 합성만으로 데이트백
                        // 룩이 난다.
                        // ⚠️**export 와 합성식이 다르다** — export 는 screen 70% + source-over 30%
                        //   (date_stamp.SCREEN_MIX), 프리뷰는 순수 source-over 다. 실측 차이(각인 화소 최대,
                        //   8bit 코드): 배경 0.02 → 2 / 0.5 → 58 / 0.9 → 105. **어두운 배경에선 사실상 같고
                        //   밝은 배경(하늘·구름)에선 프리뷰가 더 진하다.** 글로우를 키우면 커진다(배경 0.6 에서
                        //   글로우 0 → 13, 글로우 2 → 73). 산출물(export)이 정확한 쪽이다.
                        // ⚠️**배경을 읽어 screen 으로 정합시키는 배선(shaders/stamp.frag)은 두 번 시도해 두 번
                        //   철회했다 — 재시도 금지.** 1차는 canvasHolder 전체 재캡처였고, 2차는 스탬프 사각형만
                        //   sourceRect 로 캡처 + bgMap 항등 + ClampToEdge + 형제 배치(피드백 없음) + 줌 시 캡처
                        //   해상도 상향까지 갖췄는데도 **같은 증상이 재발**했다(사용자 확인). 수식 자체는 정합이
                        //   확인됐으므로(numpy 재현 결과 전 배경에서 0.000코드) 문제는 수식이 아니라 **라이브
                        //   재캡처를 프리뷰 트리에 끼우는 구조**다. 다시 하려면 다른 구조(예: 스탬프를 파이프라인
                        //   안에서 크롭 후 좌표로 굽기)를 먼저 설계할 것 — 같은 형태의 재캡처는 답이 아니다.
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
                            // 글로우 여유(pad)가 스프라이트 사방에 붙으므로, 영역을 키우면 그만큼 마진에서
                            // 빼야 **글자가 제자리에 남는다**(export 의 date_stamp.bleed_frac 과 같은 값 —
                            // 이 둘이 어긋나면 글자 위치가 프리뷰≠export).
                            property real margin: (controller.stampMargin - controller.stampBleed) * shortEdge
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
                                 && contactSheet.photos.length === 0
                        anchors.centerIn: parent
                        color: "#888"
                        font.pixelSize: 16
                        text: "Double-click a RAW file in the explorer on the left to open it"
                    }

                    // ---- 컨택트 시트 — 사진을 안 연 상태의 빈 캔버스에 현재 폴더를 격자로 ----
                    // "폴더를 골랐는데 가운데가 텅 비어 어색하다, 사진이 나오고 그걸 누르면 바로
                    // 열렸으면" 이라는 피드백. ⚠️첫 사진 자동 로드는 하지 않는다 — 시작 동작이
                    // '폴더만 연다'인 것은 설계 결정이고(CLAUDE.md), 원하지도 않은 사진을 2~4초
                    // 디코딩하게 된다. 고를 수 있게 **보여주기만** 한다. 모델·썸네일 모두 탐색기와
                    // 같은 것을 쓴다(검색·좋아요 필터 그대로 반영). ⚠️**썸네일 캐시는 공유되지
                    // 않는다** — ThumbProvider 의 LRU 키가 `(경로, 긴변)` 인데 탐색기는 96px,
                    // 격자는 160px 을 요청한다(파일당 슬롯 2개).
                    Item {
                        id: contactSheet
                        objectName: "contactSheet"     // 헤드리스 검증용(stampField 와 같은 용도)
                        anchors.fill: parent
                        // ★두 줄 규칙(win.gridPinned 주석 참조): 켠 상태이거나, 아직 사진을 안 열었을 때.
                        // ⚠️예전에는 '지금 열린 사진이 이 폴더의 사진이 아닐 때'도 켜는 조건이
                        //   **있었다가 걷어냈다** — "켜지고 꺼지는 상황이 경우마다 달라
                        //   혼란스럽다"는 보고 때문이다. 폴더를 옮겨 격자를 다시 보고 싶으면
                        //   G(또는 ▦)를 누른다. 조건을 하나 더 붙이고 싶어지면 그 보고를 먼저
                        //   떠올릴 것.
                        // ⚠️디코딩 중(busy)에는 감춘다 — 더블클릭 후 2~4초 동안 격자가 그대로
                        //   떠 있으면 '눌렀는데 아무 일도 안 난' 것처럼 보인다.
                        visible: photos.length > 0 && !controller.busy
                                 && (win.gridPinned || controller.imagePath === "")

                        // 폴더 항목은 뺀다(여기서 폴더 이동까지 하지는 않는다 — 탐색기의 몫).
                        readonly property var photos: {
                            var out = []
                            var f = win.explorerFiles
                            for (var i = 0; i < f.length; i++)
                                if (!f[i].isDir) out.push(f[i])
                            return out
                        }
                        // ⚠️썸네일 요청 크기 160 은 임의 값이 아니다 — ThumbProvider 는 160 이하면
                        //   RAF 임베드 **EXIF 썸네일**(실측 1.4ms/장), 넘으면 임베드 풀 프리뷰
                        //   축소 디코딩(**73.9ms/장**, 50배)으로 간다. 셀을 키우려면 그 비용을
                        //   감당할 방법(선캐시 등)을 먼저 정할 것.
                        readonly property int thumbEdge: 160
                        // 선택 상태는 **좌측 탐색기의 현재 항목에서 파생**한다(별도 상태 아님).
                        // 격자 클릭은 `selectInExplorer` 로 탐색기 선택을 옮기고, 탐색기에서
                        // 고르면 여기가 따라온다 — 진실원이 하나라 양방향을 맞출 일이 없다.
                        readonly property string selectedPath: {
                            var i = fileListView.currentIndex
                            var f = win.explorerFiles
                            return (i >= 0 && i < f.length && !f[i].isDir) ? f[i].path : ""
                        }
                        // 탐색기에서 화면 밖 사진을 고르면 격자도 그 자리로 스크롤(동기화가 보이게).
                        onSelectedPathChanged: {
                            if (!visible || selectedPath === "") return
                            for (var i = 0; i < photos.length; i++)
                                if (photos[i].path === selectedPath) {
                                    sheetGrid.positionViewAtIndex(i, GridView.Contain)
                                    return
                                }
                        }

                        // ⚠️불투명 배경 필수 — 사진이 열린 채로 격자를 켜면(G) 이 Item 은 렌더
                        //   트리보다 뒤(=위)에 그려지므로, 배경이 없으면 셀 사이로 편집 중인
                        //   사진이 비친다. 창 배경과 같은 색.
                        Rectangle { anchors.fill: parent; color: "#1a1a1a" }
                        // ⚠️입력도 막아야 한다 — `Rectangle` 은 클릭을 안 받으므로 힌트 줄과
                        //   격자 바깥 여백으로 클릭이 새어 **아래 사진이 1:1 확대·팬** 된다
                        //   (뒤의 팬/줌 MouseArea 로 전달). 셀보다 먼저 선언해 셀이 위에 오게 한다.
                        MouseArea { anchors.fill: parent }

                        Label {
                            id: sheetHint
                            objectName: "contactSheetHint"   // 헤드리스 검증용(contactSheet 와 같은 용도)
                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.margins: 14
                            text: "Double-click a photo to open  ·  "
                                  + contactSheet.photos.length
                                  + (contactSheet.photos.length === 1 ? " photo" : " photos")
                                  // ⚠️사진을 아직 안 열었으면 G 는 `gridPinned` 만 토글하고
                                  //   격자는 그대로다(visible 의 두 번째 항). 닫힌다고 적으면
                                  //   눌러도 아무 일이 없어 고장으로 보인다.
                                  + (controller.imagePath === ""
                                     ? "" : "  ·  G closes this grid")
                            color: "#8a8a8a"
                            font.pixelSize: 12
                        }

                        GridView {
                            id: sheetGrid
                            objectName: "contactSheetGrid"
                            anchors.top: sheetHint.bottom
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 10
                            anchors.topMargin: 8
                            clip: true
                            // 썸네일 칸을 **정사각 160**으로 둔다 = 탐색기 호버 피크와 같은 크기
                            // (피크도 `sourceSize.width: 160`). 세로/가로 어느 쪽이든 긴 변이 160.
                            // ⚠️예전엔 160×112(가로 기준)라 **세로 사진만 75px 폭으로 쪼그라들어**
                            //   "피크만큼 크게 해달라"는 보고가 나왔다. 가로 사진은 그때도 피크와
                            //   같은 크기였다 — 문제는 세로였다.
                            // ⚠️160 을 넘기면 썸네일 비용이 50배가 된다(위 ThumbProvider 절벽).
                            cellWidth: 178
                            cellHeight: 198
                            model: contactSheet.photos
                            // ⚠️폴더가 바뀔 때만 맨 위로. `photos` 는 검색어·좋아요·짝 토글에도
                            //   재평가되므로 `onModelChanged` 에 걸면 900장 폴더에서 P 를 누르거나
                            //   검색어를 치는 순간 보던 자리를 잃는다(탐색기는 선택을 보존한다).
                            Connections {
                                target: controller
                                function onFolderChanged() { sheetGrid.positionViewAtBeginning() }
                            }
                            B.ScrollBar.vertical: B.ScrollBar {           // 탐색기 목록과 같은 스타일
                                id: sheetVbar
                                width: 10
                                policy: B.ScrollBar.AsNeeded
                                contentItem: Rectangle {
                                    implicitWidth: 6
                                    radius: 3
                                    color: sheetVbar.pressed ? "#cfcfcf" : "#9a9a9a"
                                }
                                background: Rectangle { radius: 3; color: "#3a3a3a" }
                            }

                            delegate: Item {
                                id: cell
                                required property int index
                                required property var modelData
                                width: sheetGrid.cellWidth
                                height: sheetGrid.cellHeight

                                readonly property bool picked:
                                    contactSheet.selectedPath === modelData.path

                                Rectangle {
                                    anchors.fill: parent
                                    anchors.margins: 4
                                    radius: 4
                                    color: cell.picked ? "#2d4a6b"
                                         : (cellMouse.containsMouse ? "#3a3f4b" : "transparent")
                                    border.color: cell.picked ? "#8ab4f8"
                                                : (cellMouse.containsMouse ? "#55606f" : "transparent")
                                    border.width: cell.picked ? 2 : 1

                                    Item {                    // 썸네일 영역(고정 높이 — 이름 자리 확보)
                                        id: cellThumb
                                        anchors.top: parent.top
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.margins: 5
                                        height: 160          // 폭도 160(cellWidth 178 − 여백 18)
                                        // 배지 기준 사각형 — 사진이 그려진 영역. ⚠️디코드 실패
                                        // (`Image.Error`, 임베드 프리뷰 없는 DNG 등)면 painted 가
                                        // 0 이라 배지가 한가운데로 간다 → 그때는 칸 전체로 폴백.
                                        readonly property real pw: cellImg.paintedWidth > 0
                                                                   ? cellImg.paintedWidth : width
                                        readonly property real ph: cellImg.paintedHeight > 0
                                                                   ? cellImg.paintedHeight : height

                                        Rectangle {           // 로딩중/실패 placeholder(탐색기 행과 동일)
                                            anchors.fill: parent
                                            visible: cellImg.status !== Image.Ready
                                            color: "#1e1e1e"; radius: 2
                                        }
                                        Image {
                                            id: cellImg
                                            anchors.fill: parent
                                            fillMode: Image.PreserveAspectFit
                                            asynchronous: true      // provider 가 워커 스레드에서 디코딩
                                            cache: true
                                            sourceSize.width: contactSheet.thumbEdge
                                            // ⚠️격자가 닫혀 있으면 요청하지 않는다. GridView 는
                                            //   자기 **지오메트리**로 delegate 를 채우고 visible
                                            //   은 보지 않아서(시트가 anchors.fill 이라 항상 크기가
                                            //   있다), 이게 없으면 폴더를 옮길 때마다 안 보이는
                                            //   썸네일을 디코딩한다(실측: 격자 닫힌 채 폴더 이동에
                                            //   160px 요청 6건). model 을 비우지 않는 이유는
                                            //   스크롤 위치를 잃지 않기 위해서다.
                                            source: contactSheet.visible
                                                    ? "image://thumb/"
                                                      + encodeURIComponent(cell.modelData.path)
                                                    : ""
                                        }
                                        Text {                // 임베드 프리뷰가 없는 RAW(일부 DNG 등)
                                            visible: cellImg.status === Image.Error
                                            anchors.centerIn: parent
                                            text: "No preview"
                                            color: "#888888"; font.pixelSize: 10
                                        }
                                        // ⚠️배지는 셀 모서리가 아니라 **사진이 실제로 그려진 사각형**
                                        //   모서리에 붙인다 — 셀은 폭이 고정이라 세로 사진에서는
                                        //   좌우가 크게 비고, 모서리에 붙이면 배지만 허공에 뜬다.
                                        //   (탐색기 행은 칸이 작고 기존 ♥/+JPG 와 규칙을 맞춰야 해서
                                        //    그대로 칸 모서리에 둔다.)
                                        EditedBadge {
                                            x: (cellThumb.width + cellThumb.pw) / 2 - width - 2
                                            y: (cellThumb.height - cellThumb.ph) / 2 + 2
                                            // ⚠️`Image.Error` 는 **영원히** Ready 가 아니다 — 그
                                            //   파일만 배지가 통째로 사라지므로 로딩 중에만 감춘다.
                                            ready: cellImg.status !== Image.Loading
                                            path: cell.modelData.path
                                        }
                                        Text {                // 좋아요(셀렉트) — 탐색기 행과 같은 표기
                                            x: (cellThumb.width + cellThumb.pw) / 2 - width - 2
                                            y: (cellThumb.height + cellThumb.ph) / 2 - height - 2
                                            text: "♥"
                                            color: "#ff6b6b"
                                            style: Text.Outline; styleColor: "#000000"
                                            font.pixelSize: 14
                                            visible: {
                                                controller.likeRevision
                                                return cellImg.status !== Image.Loading
                                                       && controller.isLiked(cell.modelData.path)
                                            }
                                        }
                                    }
                                    Label {
                                        anchors.top: cellThumb.bottom
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.margins: 5
                                        anchors.topMargin: 3
                                        text: cell.modelData.name
                                        // 편집됨 = 앰버(탐색기 파일명과 같은 규칙)
                                        color: {
                                            controller.editsRevision
                                            return controller.hasEdits(cell.modelData.path)
                                                   ? "#E0A226" : "#cfcfcf"
                                        }
                                        font.pixelSize: 11
                                        elide: Text.ElideMiddle
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                    MouseArea {
                                        id: cellMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        // ⚠️한 번 클릭 = 열기로 뒀다가 되돌렸다 — 잘못 누르면
                                        //   2~4초 디코딩을 그대로 기다려야 한다(사용자 보고).
                                        //   탐색기 목록과 같은 규칙(클릭=선택 / 더블클릭=열기).
                                        // 선택은 탐색기 currentIndex 가 진실원이라 여기서
                                        // 그것만 옮기면 `selectedPath` 가 따라온다.
                                        // focus=false — 격자에서 방향키를 쓰려는 게 아니라
                                        // 목록 하이라이트만 맞추는 것.
                                        onClicked: win.selectInExplorer(cell.modelData.path, false)
                                        // ⚠️여기서도 닫는다 — `onImageChanged` 는 **경로가 바뀔 때만**
                                        //   닫으므로(재디코딩에 꺼지지 않게 한 장치), 지금 열려 있는
                                        //   사진을 다시 더블클릭하면 격자가 그대로 남는다.
                                        onDoubleClicked: {
                                            win.gridPinned = false
                                            // 클릭과 **같은 처리**를 여기서도 한 번 더 한다.
                                            // ⚠️첫 클릭의 onClicked 가 먼저 오므로 대개는 이미
                                            //   맞춰져 있지만, 그건 이벤트 순서에 기대는 것이다
                                            //   — 여는 경로에서 선택이 따라오는 것은 보장이어야
                                            //   한다(멱등하니 중복 호출은 무해).
                                            win.selectInExplorer(cell.modelData.path, false)
                                            controller.loadPath(cell.modelData.path)
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // 원본 비교 버튼: 클릭(또는 \ 키)으로 원본↔편집본 토글(좌하단). 크롭 페이지에선 숨김.
                    // 하단 AI 캡션 패널(전체 폭)이 보이면 항상 그 위에 배치(일관 규칙).
                    Rectangle {
                        id: cmpBtn
                        // ⚠️격자가 덮고 있으면 감춘다 — 안 보이는 사진의 원본 비교 버튼이다.
                        visible: controller.imagePath !== "" && win.activePanel === 0
                                 && !contactSheet.visible
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
                                 && controller.hashtags !== "" && !contactSheet.visible
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
                        visible: win.compareOn && !contactSheet.visible
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
                        // ⚠️격자가 덮고 있으면 감춘다 — 안 보이는 사진의 캡션이 격자 위에 뜬다.
                        visible: win.captionOverlay && cropClip.visible
                                 && controller.imagePath !== "" && !contactSheet.visible
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
                        // ⚠️격자가 덮고 있으면 감춘다(캡션 바와 같은 이유).
                        visible: win.infoOverlay && cropClip.visible
                                 && controller.shootingInfo.length > 0 && !contactSheet.visible
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

                    // 존 시스템 범례 (Z 키 토글 시) — 하단 중앙, 존 0..X 스와치.
                    // 셰이더 zoneShow 표시색과 동일(0=파랑, X=빨강, 나머지 존/10 그레이).
                    Rectangle {
                        visible: win.zoneOverlay && cropClip.visible && !contactSheet.visible
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.margins: 12
                        radius: 6
                        color: "#cc1e1e1e"
                        border.color: "#55ffffff"; border.width: 1
                        width: zoneLegendCol.implicitWidth + 24
                        height: zoneLegendCol.implicitHeight + 16
                        ColumnLayout {
                            id: zoneLegendCol
                            anchors.centerIn: parent
                            spacing: 4
                            Label {
                                text: "Zone System  (Z) — 1 zone = 1 stop, V = mid gray"
                                color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                            }
                            Row {
                                spacing: 2
                                Repeater {
                                    model: ["0","I","II","III","IV","V","VI","VII","VIII","IX","X"]
                                    delegate: Column {
                                        spacing: 2
                                        Rectangle {
                                            width: 30; height: 14; radius: 2
                                            color: index === 0 ? Qt.rgba(0.10, 0.25, 0.62, 1)
                                                 : index === 10 ? Qt.rgba(0.82, 0.16, 0.16, 1)
                                                 : Qt.rgba(index / 10, index / 10, index / 10, 1)
                                            border.color: "#33ffffff"; border.width: 1
                                        }
                                        Label {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: modelData
                                            color: "#c9c9c9"; font.pixelSize: 10
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ---- 브러시 페인트 서피스 (마스킹 패널 + 브러시 모드에서만) ----
                    // 좌표는 mapToItem(pipeView)가 뷰 변환 체인(플립/회전/원근/fit/줌·팬)을 전부
                    // 역산해 주므로 프록시 정규화 좌표가 바로 나온다. 릴리즈 시 획 1개 커밋
                    // (= undo 스텝 1개), 드래그 중엔 반투명 트레일만 표시.
                    Item {
                        id: brushSurface
                        anchors.fill: parent
                        // ⚠️`!contactSheet.visible` 필수 — 격자는 이 항목보다 **앞**에 선언돼
                        //   있어 브러시 표면이 그 위를 덮는다. 빠뜨리면 격자에서 셀을 못 고르고
                        //   드래그가 뒤에 가려진 사진에 **보이지 않는 획**을 남긴다.
                        visible: win.activePanel === 2 && win.brushMode !== 0 && cropClip.visible
                                 && !contactSheet.visible
                        // 화면상 브러시 반경(px): 프록시 px 반경을 pipeView→화면 스케일로 환산
                        function screenRadius() {
                            var rpx = win.brushSize * Math.min(viewport.procW, viewport.procH)
                            var p0 = pipeView.mapToItem(brushSurface, 0, 0)
                            var p1 = pipeView.mapToItem(brushSurface, rpx, 0)
                            return Math.hypot(p1.x - p0.x, p1.y - p0.y)
                        }
                        // 커서용 캐시 반경 — 함수는 리액티브하지 않아 프로퍼티로 미러(같은 값
                        // 재대입은 notify 를 안 울려 커서 repaint 가 실제 변경 때만 일어난다).
                        property real curRc: 0
                        MouseArea {
                            id: brushArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.BlankCursor
                            acceptedButtons: Qt.LeftButton
                            property var pts: []          // 현재 획(프록시 정규화 xy 평탄 배열)
                            property real lastX: -1e9
                            property real lastY: -1e9
                            property real mx: -1000       // 커서 표시 위치(서피스 좌표)
                            property real my: -1000
                            function addPoint(x, y) {
                                // 화면 이동량 < 반경/4 이면 스킵(획당 점 수 제한 — 사이드카 경량)
                                var minStep = Math.max(2, brushSurface.screenRadius() / 4)
                                if (pts.length > 0 && Math.hypot(x - lastX, y - lastY) < minStep) return
                                lastX = x; lastY = y
                                var p = brushArea.mapToItem(pipeView, x, y)
                                pts.push(p.x / Math.max(1, pipeView.width))
                                pts.push(p.y / Math.max(1, pipeView.height))
                                trailCanvas.trail.push(Qt.point(x, y))
                                trailCanvas.requestPaint()
                            }
                            onPressed: (mouse) => {
                                pts = []; trailCanvas.trail = []; lastX = -1e9; lastY = -1e9
                                addPoint(mouse.x, mouse.y)
                            }
                            onPositionChanged: (mouse) => {
                                mx = mouse.x; my = mouse.y                       // 커서 이동 = 아이템 x/y (repaint 없음)
                                brushSurface.curRc = brushSurface.screenRadius() // 줌/핏 변화 추종(동값이면 no-op)
                                if (pressed) addPoint(mouse.x, mouse.y)
                            }
                            onReleased: {
                                // 드래그 중엔 트레일만 표시, 마스크 반영은 릴리즈 1회(증분
                                // 커밋 ~50ms 상수). 실시간 마스크 미리보기는 시도 후 제거 —
                                // 빨간 오버레이+트레일과 중복이고 미세한 부자연스러움만 남았음.
                                if (pts.length >= 2)
                                    win.commitBrushStroke({ sign: win.brushMode === 1 ? 1 : -1,
                                                            radius: win.brushSize,
                                                            feather: win.brushFeather,
                                                            points: pts })
                                pts = []; trailCanvas.trail = []; trailCanvas.requestPaint()
                            }
                            onExited: { mx = -1000; my = -1000 }   // visible 바인딩이 커서 숨김
                        }
                        // 휠 = 브러시 크기, Shift+휠 = 페더(라이트룸 관례). 슬라이더에도 반영.
                        // ⚠️Shift+휠은 장치/OS 에 따라 가로축(angleDelta.x)으로 올 수 있어 y→x 폴백.
                        WheelHandler {
                            onWheel: (ev) => {
                                var d = ev.angleDelta.y !== 0 ? ev.angleDelta.y : ev.angleDelta.x
                                if (d === 0) return
                                if (ev.modifiers & Qt.ShiftModifier) {
                                    win.brushFeather = Math.max(0.0, Math.min(1.0,
                                        win.brushFeather + (d > 0 ? 0.05 : -0.05)))
                                    brushFeatherSlider.value = win.brushFeather
                                } else {
                                    var f = d > 0 ? 1.12 : 1.0 / 1.12
                                    win.brushSize = Math.max(0.003, Math.min(0.20, win.brushSize * f))
                                    brushSizeSlider.value = win.brushSize
                                }
                                // 커서 갱신은 curRc/ro 바인딩(크기 변화)이 처리 — 수동 repaint 불필요
                            }
                        }
                        // 드래그 중 임시 트레일(커밋되면 실제 마스크 오버레이가 이어받음)
                        Canvas {
                            id: trailCanvas
                            anchors.fill: parent
                            property var trail: []
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                if (trail.length === 0) return
                                ctx.strokeStyle = win.brushMode === 2 ? "rgba(80,160,255,0.55)"
                                                                      : "rgba(255,80,80,0.55)"
                                ctx.lineWidth = Math.max(2, brushSurface.screenRadius() * 2)
                                ctx.lineCap = "round"; ctx.lineJoin = "round"
                                ctx.beginPath()
                                ctx.moveTo(trail[0].x, trail[0].y)
                                if (trail.length === 1) ctx.lineTo(trail[0].x + 0.1, trail[0].y)
                                for (var ti = 1; ti < trail.length; ti++) ctx.lineTo(trail[ti].x, trail[ti].y)
                                ctx.stroke()
                            }
                        }
                        // 브러시 커서 — 얇은 흰 링 + 부드러운 그림자(PS/LR 식). 라이트룸 모델:
                        // 안쪽 실선=Size(코어), 바깥 점선=페더 외곽. 중앙 글리프=＋빨강/−파랑.
                        // ⚠️Canvas 는 **커서 크기만큼만** 잡고 이동은 아이템 x/y 로 — 화면 전체
                        // Canvas 를 마우스 이동마다 repaint 하면 페더(shadowBlur 면적)가 클수록
                        // 커서가 버벅인다(실측). repaint 는 크기/페더/모드 변경 때만.
                        Item {
                            id: brushCursorItem
                            visible: brushArea.mx > -100
                            // 외곽 반경 — 2.0 = brush.py FEATHER_SCALE (반드시 일치)
                            readonly property real ro: brushSurface.curRc * (1.0 + 2.0 * win.brushFeather)
                            width: 2 * ro + 16                 // 여유 8px(그림자 블러+글리프)
                            height: width
                            x: brushArea.mx - width / 2
                            y: brushArea.my - height / 2
                            Canvas {
                                id: brushCursor
                                anchors.fill: parent
                                onWidthChanged: requestPaint()   // 크기/페더 변경 → 캔버스 크기 변화로 갱신
                                onPaint: {
                                    var ctx = getContext("2d")
                                    ctx.clearRect(0, 0, width, height)
                                    var x = width / 2, y = height / 2
                                    var rc = brushSurface.curRc
                                    var ro = brushCursorItem.ro
                                    ctx.shadowColor = "rgba(0,0,0,0.65)"   // 어두운 배경 가시성은 그림자로
                                    ctx.shadowBlur = 4
                                    ctx.lineCap = "round"
                                    ctx.strokeStyle = "rgba(255,255,255,0.95)"
                                    ctx.lineWidth = 1.2
                                    ctx.beginPath(); ctx.arc(x, y, rc, 0, Math.PI * 2); ctx.stroke()
                                    if (ro > rc + 2) {                     // 페더 외곽(은은한 점선)
                                        ctx.setLineDash([3, 5])
                                        ctx.strokeStyle = "rgba(255,255,255,0.55)"
                                        ctx.lineWidth = 1.0
                                        ctx.beginPath(); ctx.arc(x, y, ro, 0, Math.PI * 2); ctx.stroke()
                                        ctx.setLineDash([])
                                    }
                                    var g = 4.5                            // 중앙 모드 글리프(고정 소형)
                                    ctx.strokeStyle = win.brushMode === 2 ? "#7db8ff" : "#ff7d7d"
                                    ctx.lineWidth = 1.6
                                    ctx.beginPath(); ctx.moveTo(x - g, y); ctx.lineTo(x + g, y)
                                    if (win.brushMode === 1) { ctx.moveTo(x, y - g); ctx.lineTo(x, y + g) }
                                    ctx.stroke()
                                    ctx.shadowBlur = 0
                                }
                            }
                        }
                        // 모드 전환(크기 동일) → 글리프만 갱신 / Size 변경 → 화면 반경 재계산
                        Connections {
                            target: win
                            function onBrushModeChanged() { brushCursor.requestPaint() }
                            function onBrushSizeChanged() { brushSurface.curRc = brushSurface.screenRadius() }
                        }
                    }
                }
            }

            // 진행 중 오버레이 (이미지 위): export / 배치 / 디코딩(렌즈 보정) / 하늘 세그멘테이션
            Rectangle {
                anchors.fill: parent
                visible: controller.exporting || win.batchActive || win.wallActive || controller.busy
                         || win.skyBusySlow || controller.aiNrDownloading
                         || controller.aiNrInitializing
                color: "#aa000000"
                MouseArea { anchors.fill: parent }   // 진행 중 이미지 입력 차단

                // ── Export: 필름 프레임 카운터 (실제 진행률 controller.exportProgress 반영) ──
                // 위/아래 앰버 퍼포레이션이 끊김없이 와인딩(필름 감기는 느낌). 가운데 'DEVELOPING'
                // 라벨 + 큰 % 카운터 + 진행 바. 진행률 모르는 구간(디코드·GPU)은 인디터미닛 스윕.
                // 배치 중엔 파일 전환(디코드/마스크) 구간에도 유지되고 FRAME i/N 카운트업.
                Rectangle {
                    id: filmCell
                    visible: controller.exporting || win.batchActive || win.wallActive
                    anchors.centerIn: parent
                    width: 320; height: (win.batchActive || win.wallActive) ? 176 : 156
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
                                running: controller.exporting || win.batchActive || win.wallActive
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
                                visible: win.batchActive || win.wallActive
                                text: win.wallActive
                                      ? (win.wallPhase === 4 ? "COMPOSING"
                                         : "PANEL " + Math.min(win.wallIndex + 1, 3) + " / 3")
                                      : "FRAME " + Math.min(win.batchIndex + 1, win.batchQueue.length)
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
                                        running: (controller.exporting || win.batchActive || win.wallActive) && !devInfo.determinate
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

                // 배경화면 취소 — 현재 패널까지 마치고 중단(배치와 동일 의미론)
                Button {
                    visible: win.wallActive
                    anchors.top: filmCell.bottom
                    anchors.topMargin: 12
                    anchors.horizontalCenter: filmCell.horizontalCenter
                    text: win.wallCancel ? "Cancelling…" : "Cancel wallpaper"
                    enabled: !win.wallCancel
                    onClicked: win.wallCancel = true
                }

                // ── AI 모델 다운로드: 실제 진행률 프로그레스바(하늘 모델 오버레이와 동일 UX) ──
                ColumnLayout {
                    visible: controller.aiNrDownloading && !controller.exporting && !win.batchActive
                             && !win.wallActive
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
                        // 이 바는 장면(105MB)·얼굴(341MB)·깊이(105MB) 모델이 공유한다 →
                        // 특정 크기를 박아두면 나머지 둘에서 거짓말이 된다(정확한 크기는 AI Models 화면).
                        text: "first use only"
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
                        running: (controller.busy || win.skyBusySlow) && !controller.exporting
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: 64; implicitHeight: 64
                    }
                    Label {
                        text: controller.segStatus !== "" ? controller.segStatus
                              : (win.skyBusySlow ? "Detecting mask…" : "Processing…")
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
            enabled: !win.batchActive && !win.wallActive   // 배치/배경화면 실행 중 슬라이더 변경 → 사이드카 오염 방지

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
                // ⚠️네 버튼 모두 네이티브 Button 을 쓰지 않는다 — ♥/☑/태그/위로가기와 같은
                //   커스텀 패턴(투명 배경 + #555 테두리 + 호버 강조)이다. 이유:
                //   ①macOS 네이티브 스타일은 26px 버튼의 **베젤을 아이템 안에서 아래로 치우쳐**
                //     그린다(실측 y 5.0~24.5 = 높이 19.5px, 중심이 2.25px 아래). 그래서 라벨을
                //     아이템 중앙에 맞추면 눈에는 베젤 위로 2px 뜬 것처럼 보인다.
                //   ②베젤 모양 자체가 Windows 와 달라 네이티브를 쓰는 한 '양 OS 동일'이 불가능하다.
                //   아이콘도 텍스트 글리프가 아니라 **SVG**(assets/icons/) — 글리프는 OS 마다 폴백
                //   폰트가 달라 잉크가 제각각 앉지만(macOS 실측: ↶/↷ 2.7px 위, ↺/⋯ 중앙), SVG 는
                //   잉크를 viewBox 정중앙·같은 크기(12/16)로 맞춰 두어 플랫폼 분기가 필요 없다.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    IconBtn {
                        icon: "../assets/icons/undo.svg"
                        active: win.canUndo
                        tip: "Undo (Ctrl+Z)"
                        onClicked: win.undo()
                    }
                    IconBtn {
                        icon: "../assets/icons/redo.svg"
                        active: win.canRedo
                        tip: "Redo (Ctrl+Shift+Z)"
                        onClicked: win.redo()
                    }
                    Item { Layout.fillWidth: true }      // 좌(이력) ↔ 우(초기화/기타) 분리 스페이서
                    IconBtn {
                        id: resetBtn
                        icon: "../assets/icons/reset.svg"
                        tip: "Reset (clear adjustments — including geometry)"
                        onClicked: win.resetAndClearEdits()   // 모든 편집 초기화 + 사이드카 삭제(파일명 앰버 해제)
                    }
                    // 편집 복사/붙여넣기 메뉴(이미지 간) — Reset 우측 "⋯" 드롭다운.
                    IconBtn {
                        id: editClipBtn
                        icon: "../assets/icons/more.svg"
                        active: controller.imagePath !== ""
                        tip: "Copy / paste edits (between images)"
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
                            MenuSeparator {}
                            MenuItem {
                                text: "Save as recipe…"
                                enabled: controller.imagePath !== ""
                                onTriggered: presetSaveDialog.openForSave()
                            }
                            MenuItem {
                                text: "Import recipe…"
                                onTriggered: presetImportDialog.open()
                            }
                        }
                    }
                }

                // 출력: 주 버튼 + 옵션(⚙) 팝업
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    // 주 동작 버튼 — 옆 옵션 버튼(IconBtn)과 같은 커스텀 톤. 네이티브 Button 을
                    // 쓰면 macOS 에서 베젤이 아이템 안에서 치우쳐 그려져(IconBtn 주석 참조) 옆
                    // 버튼과 높이·정렬이 어긋나고, 흰 pill 이라 어두운 패널에서 톤도 튄다.
                    // 보조(옵션)는 투명+테두리, 주 동작은 채움으로 위계를 준다.
                    Rectangle {
                        id: exportMainBtn
                        Layout.fillWidth: true
                        Layout.preferredHeight: 32
                        radius: 5
                        readonly property bool ready: controller.imagePath !== "" && !controller.exporting
                        color: !ready ? "#2f333a"
                               : (expMainMa.pressed ? "#33373f"
                                  : (expMainHover.hovered ? "#4a5060" : "#3a3f4b"))
                        border.color: "#5a5f6b"; border.width: 1
                        opacity: ready ? 1.0 : 0.45
                        Text {
                            anchors.centerIn: parent
                            text: "Export…"
                            color: "#e6e6e6"
                            font.pixelSize: 13
                        }
                        HoverHandler { id: expMainHover }
                        MouseArea {
                            id: expMainMa
                            anchors.fill: parent
                            enabled: exportMainBtn.ready
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                // 기본 파일명 = '<원본이름>_exported.<마지막 사용 형식>' (원본과 같은 폴더)
                                var u = controller.suggestedExportUrl()
                                if (u != "") saveDialog.selectedFile = u
                                // 필터도 같은 형식으로 맞춘다 — 안 맞추면 대화상자에 이전 필터가
                                // 남아 '필터 JPEG / 이름 .png' 같은 모순 상태로 열린다.
                                var k = saveDialog.filterExts.indexOf(controller.exportExt)
                                if (k >= 0 && saveDialog.selectedNameFilter.index !== k)
                                    saveDialog.selectedNameFilter.index = k
                                saveDialog.open()
                            }
                        }
                    }
                    IconBtn {
                        id: exportOptBtn
                        icon: "../assets/icons/chevron_down.svg"
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: exportMainBtn.height   // Export 버튼과 높이 동일하게 고정
                        tip: "Export options (resolution · render · 16-bit)"
                        onClicked: exportOptPopup.opened ? exportOptPopup.close() : exportOptPopup.open()
                        Popup {
                            id: exportOptPopup
                            y: exportOptBtn.height + 4
                            x: exportOptBtn.width - width    // 버튼 오른쪽에 맞춰 좌측으로 펼침(패널 안)
                            width: 230
                            padding: 10
                            modal: false
                            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                            // 시인성 — 기존 #2b2b2b/#555 는 패널(#1a1a1a) 과 명도 차이가 작아 경계가
                            // 흐렸다. 채움을 한 단 올리고(#2f3238) 테두리를 밝게(#6f737a) 해 패널
                            // 위에서 윤곽이 끊기지 않게 한다. 모달 대화상자는 dim 오버레이가 이 역할을
                            // 하지만 이 팝업은 비모달이라 스스로 경계를 세워야 한다.
                            // ⚠️MultiEffect 드롭섀도를 시도했으나 무효라 제거했다 — 배경 Item 이
                            //   팝업 크기에 정확히 맞아 바깥으로 그릴 여유가 없다(실측: 캡처의
                            //   반투명 픽셀이 코너 안티에일리어싱뿐인 0.2%).
                            background: Rectangle {
                                radius: 8
                                color: "#2f3238"
                                border.color: "#6f737a"; border.width: 1
                            }
                            contentItem: ColumnLayout {
                                spacing: 10
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: "Resolution"; color: "white"; font.pixelSize: 12; Layout.preferredWidth: 72 }
                                    ComboBox {
                                        id: resCombo
                                        Layout.fillWidth: true
                                        model: ["Original (Full)", "4096", "3840 (4K)",
                                                "2560", "2048", "1920 (FHD)", "1280"]
                                        // 기억된 값에서 시작(피드백: "미리 설정해두고 export 만").
                                        // ⚠️인라인 currentIndex 바인딩은 첫 선택 시 파괴되므로
                                        //   독립 Binding 으로 둔다(stampCheck 와 같은 이유).
                                        onActivated: controller.rememberExportOpts(
                                            { "edge": win.exportEdges[currentIndex] })
                                        // 드롭다운 닫히면 포커스 해제(단축키 복구 — captionLevelCombo 와 동일)
                                        Connections {
                                            target: resCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                    Binding {
                                        target: resCombo; property: "currentIndex"
                                        value: Math.max(0, win.exportEdges.indexOf(controller.exportEdge))
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
                                        model: ["CPU", "GPU"]
                                        onActivated: controller.rememberExportOpts({ "render": currentIndex })
                                        // 드롭다운 닫히면 포커스 해제(단축키 복구 — captionLevelCombo 와 동일)
                                        Connections {
                                            target: renderModeCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                    Binding {
                                        target: renderModeCombo; property: "currentIndex"
                                        value: controller.exportRender
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    CheckBox {
                                        id: bitDepth16Check
                                        onToggled: controller.rememberExportOpts({ "bit16": checked })
                                        ToolTip.visible: hovered
                                        ToolTip.text: "Save 16-bit/channel (preserves gradation · headroom). TIFF recommended. CPU render only."
                                    }
                                    // 기억된 값 재푸시(인라인 checked 바인딩은 첫 클릭에 파괴된다)
                                    Binding {
                                        target: bitDepth16Check; property: "checked"
                                        value: controller.export16Bit
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

                // ── 레시피 출처 배너 ──
                // 이 기능의 목적이 "레시피는 장비에 묶여 있다"를 알리는 것이므로, 기록만 하지 않고
                // 적용 시점에 여기서 보여준다. 고정 헤더에 두어 패널 스크롤과 무관하게 남고,
                // 숨을 때는 Layout 이 invisible 항목을 무시해 높이를 전혀 먹지 않는다.
                // ⚠️앰버(다른 기종)와 회색(비교 불가)의 **시각 비중을 반드시 다르게** 한다 —
                //   똑같이 보이면 사용자가 둘 다 읽지 않게 되고, 그러면 배너가 무의미해진다.
                Rectangle {
                    Layout.fillWidth: true
                    visible: win.presetNotice !== ""
                    Layout.preferredHeight: presetNoticeRow.implicitHeight + 12
                    radius: 4
                    color: win.presetNoticeWarn ? "#3a2f1e" : "#2a2a2c"
                    border.color: win.presetNoticeWarn ? "#E0A226" : "#4a4a4c"
                    border.width: 1
                    RowLayout {
                        id: presetNoticeRow
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 6
                        Label {
                            text: win.presetNoticeWarn ? "\u26a0" : "\u2139"
                            color: win.presetNoticeWarn ? "#E0A226" : "#8a8a8a"
                            font.pixelSize: 12
                            Layout.alignment: Qt.AlignTop
                        }
                        Label {
                            Layout.fillWidth: true
                            text: win.presetNotice
                            color: win.presetNoticeWarn ? "#e8d5b0" : "#9a9a9a"
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }
                        Label {              // 닫기
                            text: "\u2715"
                            color: "#7f7f7f"
                            font.pixelSize: 10
                            Layout.alignment: Qt.AlignTop
                            TapHandler { onTapped: win.clearPresetNotice() }
                        }
                    }
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

                // ── Recipes: 저장된 레시피 프리셋 배지 그리드 ──
                //   참고 디자인(바탕화면/film_recipe.png) 구조: 출처 한 줄 + 구분색 바 + 이름.
                //   배지가 출처를 직접 드러내는 것이 이 기능의 목적이다 — 레시피는 장비에 묶여 있다.
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[13] ? "▾  " : "▸  ") + "Recipes"
                              + (win.presetItems.length > 0 ? "  (" + win.presetItems.length + ")" : "")
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler {
                        onTapped: { win.toggleSec(13); if (win.secOpen[13]) win.refreshPresets() }
                    }
                }
                ColumnLayout {
                    visible: win.secOpen[13]
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        Layout.fillWidth: true
                        visible: win.presetItems.length === 0
                        text: "No recipes yet \u2014 use \u201cSave as recipe\u2026\u201d in the \u22ef menu."
                        color: "#7f7f7f"; font.pixelSize: 11; wrapMode: Text.WordWrap
                    }
                    // 레시피 목록 = **1열 행 리스트**(행 44px + 간격 5 = 스트라이드 49).
                    // 구성: 왼쪽 구분색 세로 줄 + 이름(대문자) + 장비 한 줄. 적용 중이면 앰버 외곽선.
                    // ⚠️예전엔 3열 87×56 배지 그리드였다(참고 이미지 film_recipe.png 밀도). 카메라/렌즈를
                    //   손으로 입력할 수 있게 되자 87px 에 이름과 장비를 함께 담을 수 없었다.
                    //
                    // ⚠️**입력(마우스)은 이 Item 하나에서만 받는다.** 행마다 MouseArea 를 두고 그 행을
                    //   Translate 로 끌었더니 **좌표 피드백 루프**로 위치가 요동쳤다(사용자 보고): mouse.y 는
                    //   끌리는 행의 로컬 좌표라, 행이 내려가면 로컬 y 가 줄어 dy 가 줄고 → 행이 되올라간다.
                    //   입력은 움직이지 않는 프레임에서 받아야 한다 — 행 높이가 고정이라 인덱스는 y/스트라이드
                    //   나눗셈으로 나오므로 오히려 더 단순하다.
                    Item {
                        id: recipeList
                        Layout.fillWidth: true
                        readonly property int stride: 49
                        readonly property int count: win.presetItems.length
                        Layout.preferredHeight: Math.max(0, recipeList.count * recipeList.stride - 5)
                        function idxAt(y) {
                            return Math.max(0, Math.min(recipeList.count - 1,
                                                        Math.floor(y / recipeList.stride)))
                        }
                        ColumnLayout {
                            id: badgeFlow
                            anchors.fill: parent
                            spacing: 5
                            Repeater {
                                model: win.presetItems
                                delegate: Rectangle {
                                    id: badge
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 44
                                    radius: 5
                                    readonly property bool hovered: win.recipeHoverIdx === index
                                                                    && win.recipeDragIdx < 0
                                    color: badge.hovered ? "#242424" : "#1e1e1e"
                                    // 집은 행만 떠서 커서를 따라온다(레이아웃 불변 — transform 을 쓴다)
                                    z: win.recipeDragIdx === index ? 2 : 0
                                    opacity: win.recipeDragIdx === index ? 0.85 : 1.0
                                    transform: Translate {
                                        y: win.recipeDragIdx === index ? win.recipeDragDy : 0
                                    }
                                    // 지금 룩이 이 레시피와 같으면 앰버 실선, 아니면 무테(위 badgeOn 주석)
                                    // 미리 계산된 배열만 읽는다(위 refreshLookHash 주석 참조)
                                readonly property bool isOn: win.recipeOn[index] === true
                                    border.color: badge.isOn ? "#E0A226"
                                                : (badge.hovered ? "#5a5a5a" : "#3a3a3a")
                                    border.width: badge.isOn ? 2 : 1
                                    // 장비 한 줄 = **대화상자에서 입력된 카메라 + 렌즈**.
                                    // ⚠️예전에는 렌즈가 비면 EXIF 초점거리로 대체했는데, 그건 없는 값을
                                    //   초점거리로 **위장**하는 것이었다. 행 폭이 넉넉해 제조사도 줄이지 않는다.
                                    readonly property string gear: {
                                        var src = modelData.source || {}
                                        var a = []
                                        if (String(src.camera || "")) a.push(String(src.camera))
                                        if (String(src.lens || "")) a.push(String(src.lens))
                                        return a.join("  \u00b7  ")
                                    }
                                    // 들어갈 자리 — recipeDropIdx 는 **최종 인덱스**다. 올라오는 중이면 그 행의 위,
                                    // 내려가는 중이면 그 행의 아래에 선을 그린다(간격 인덱스가 아니라 결과 위치를 보여준다).
                                    Rectangle {
                                        visible: win.recipeDragIdx >= 0 && win.recipeDropIdx === index
                                                 && win.recipeDropIdx <= win.recipeDragIdx
                                        anchors.left: parent.left; anchors.right: parent.right
                                        y: -3.5; height: 2; radius: 1
                                        color: "#E0A226"
                                    }
                                    Rectangle {
                                        visible: win.recipeDragIdx >= 0 && win.recipeDropIdx === index
                                                 && win.recipeDropIdx > win.recipeDragIdx
                                        anchors.left: parent.left; anchors.right: parent.right
                                        y: parent.height + 1.5; height: 2; radius: 1
                                        color: "#E0A226"
                                    }
                                    Rectangle {      // 구분색 — 행 왼쪽 세로 줄
                                        x: 6
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: 4; height: parent.height - 14
                                        radius: 2
                                        color: modelData.color
                                    }
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 18; anchors.rightMargin: 10
                                        anchors.topMargin: 6; anchors.bottomMargin: 6
                                        spacing: 1
                                        Label {
                                            Layout.fillWidth: true
                                            text: modelData.name
                                            color: "#e6e6e6"; font.pixelSize: 11; font.bold: true
                                            font.capitalization: Font.AllUppercase
                                            font.letterSpacing: 0.4
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            // 장비가 없으면 빈 줄로 두지 않고 그 사실을 적는다(행 높이 고정)
                                            text: badge.gear !== "" ? badge.gear : "no camera or lens recorded"
                                            color: badge.gear !== "" ? "#8a8a8a" : "#5f5f5f"
                                            font.pixelSize: 9
                                            font.italic: badge.gear === ""
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                        }
                        // 목록 전체를 덮는 단일 입력 레이어(고정 프레임) — 클릭/우클릭/드래그를 모두 여기서.
                        MouseArea {
                            id: recipeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            // ⚠️바깥 Flickable 이 세로 드래그를 가로채지 못하게(커브 에디터와 같은 이유)
                            preventStealing: true
                            property real pressY: 0
                            property int pressIdx: -1
                            property bool dragging: false
                            onPositionChanged: function (mouse) {
                                win.recipeHoverIdx = recipeList.idxAt(mouse.y)
                                if (!pressed || !(mouse.buttons & Qt.LeftButton)) return
                                var dy = mouse.y - recipeMouse.pressY
                                // 4px 문턱을 넘어야 드래그 — 넘지 않으면 클릭(적용)이다.
                                if (!recipeMouse.dragging && Math.abs(dy) < 4) return
                                if (!recipeMouse.dragging) {
                                    recipeMouse.dragging = true
                                    win.recipeDragIdx = recipeMouse.pressIdx
                                }
                                win.recipeDragDy = dy
                                var slot = recipeMouse.pressIdx + Math.round(dy / recipeList.stride)
                                win.recipeDropIdx = Math.max(0, Math.min(recipeList.count - 1, slot))
                            }
                            onExited: if (!recipeMouse.dragging) win.recipeHoverIdx = -1
                            onPressed: function (mouse) {
                                recipeMouse.pressY = mouse.y
                                recipeMouse.pressIdx = recipeList.idxAt(mouse.y)
                                recipeMouse.dragging = false
                                if (mouse.button === Qt.RightButton) {
                                    // 우클릭은 곧바로 실행하지 않는다 — 어느 동작인지 고를 수 있게 메뉴를 띄운다.
                                    var it = win.presetItems[recipeMouse.pressIdx]
                                    if (!it) return
                                    win._presetCtxFile = it.file
                                    win._presetCtxName = it.name
                                    win._presetCtxColor = it.color
                                    win._presetCtxDesc = it.description || ""
                                    win._presetCtxSrc = it.source || ({})
                                    presetCtxMenu.popup()
                                }
                            }
                            onReleased: function (mouse) {
                                if (recipeMouse.dragging) {
                                    win.recipeDrop()
                                    recipeMouse.dragging = false
                                    return
                                }
                                if (mouse.button !== Qt.LeftButton || controller.imagePath === "") return
                                var it = win.presetItems[recipeMouse.pressIdx]
                                if (it) win.applyPresetFile(it.file, it.name)
                            }
                            onCanceled: {
                                recipeMouse.dragging = false
                                win.recipeDragIdx = -1; win.recipeDropIdx = -1; win.recipeDragDy = 0
                            }
                            // 툴팁 — 행 위에 떠 있을 때 그 행의 정보를 보여준다.
                            ToolTip.visible: recipeMouse.containsMouse && win.recipeDragIdx < 0
                                             && win.recipeHoverIdx >= 0
                            ToolTip.delay: 600
                            ToolTip.text: {
                                var it = win.presetItems[win.recipeHoverIdx]
                                if (!it) return ""
                                return it.name + (win.recipeOn[win.recipeHoverIdx] === true
                              ? "  \u2713 applied" : "")
                                     + (it.description ? "\n" + it.description : "")
                                     + "\nCreated " + it.createdAt
                                     + (it.appVersion ? "  \u00b7  v" + it.appVersion : "")
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: win.presetItems.length > 0
                        horizontalAlignment: Text.AlignRight
                        text: "Click to apply  \u00b7  right-click for options"
                        color: "#6f6f6f"; font.pixelSize: 10
                    }
                }

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
                    // 시뮬이 바뀌면 보정 노출 재계산(프로그램 복원 포함 → currentIndexChanged).
                    onCurrentIndexChanged: win.syncFilmSim(true)
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
                    // 강도도 보정 노출에 들어간다(강도 0 = 보정 0). 드래그 중에만 스로틀.
                    onValueChanged: win.syncFilmSim(!pressed)
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

                // 노출 줄 — 라벨 왼쪽, 자동노출 토글은 **같은 줄 오른쪽 빈 공간**에 둔다.
                // ⚠️슬라이더 밑에 별도 행으로 넣었다가 옮겼다 — Light 섹션의 행 간격(12)이
                //   한 칸 벌어져 리듬이 깨졌다(사용자 지적). 체크박스는 padding 0 + 18px 로
                //   줄여 라벨 높이를 넘지 않게 한다.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0        // 좁아지면 이쪽이 줄어든다(체크박스 고정)
                        elide: Text.ElideRight
                        text: "Exposure:  " + expSlider.value.toFixed(2)
                        color: "white"
                    }
                    Label {
                        // ⚠️자동노출은 **보이지 않는 보정**이라 슬라이더가 0.00 인데 뒤에서
                        //   +2EV 가 걸려 있을 수 있다 — "왜 내가 찍은 것보다 밝지"의 정체였다
                        //   (커뮤니티 피드백). 적용된 값을 여기 붙여 둔다. 끄면 0 이라 사라진다.
                        objectName: "autoExpLabel"
                        text: "Auto" + (Math.abs(controller.autoExposureEV) >= 0.005
                              ? " " + (controller.autoExposureEV >= 0 ? "+" : "")
                                + controller.autoExposureEV.toFixed(2) + "EV" : "")
                        color: "#9a9a9a"; font.pixelSize: 11
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                        ToolTip.visible: autoExpLblHover.hovered
                        ToolTip.delay: 600
                        ToolTip.text: "Matches the render to the camera's own JPEG brightness.
Turn it off for a linear starting point (no tone shaping) —
RAW is exposed to protect highlights, so it opens 1-2 stops darker."
                        HoverHandler { id: autoExpLblHover }
                    }
                    // ⚠️체크박스는 **줄의 맨 끝**(오른쪽 고정)이다. 글자 앞에 두면 켬/끔에 따라
                    //   `Auto +0.91EV` ↔ `Auto` 로 폭이 달라져 **체크박스가 51px 좀우로 움직인다**
                    //   — 연달아 껐다 켜기가 불편하다(사용자 지적, 실측 1459→1510). 늘었다 줄었다 하는
                    //   것은 글자 쪽이어야 한다. 밀리는 폭은 fillWidth 인 Exposure 라벨이 흡수한다.
                    CheckBox {
                        id: autoExpCheck
                        objectName: "autoExpCheck"      // 헤드리스 레이아웃 검증용
                        padding: 0
                        Layout.preferredWidth: 18
                        Layout.preferredHeight: 18
                        checked: controller.autoExposure
                        onToggled: controller.setAutoExposure(checked)
                    }
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
                        text: "Clipping warning  (J)"
                        color: "white"; font.pixelSize: 12
                        elide: Text.ElideRight    // 라벨은 한 줄 유지, 상세 설명은 툴팁
                        verticalAlignment: Text.AlignVCenter
                        HoverHandler { id: clipWarnHover }
                        ToolTip.visible: clipWarnHover.hovered
                        ToolTip.delay: 500
                        ToolTip.text: "Highlights red / shadows blue"
                    }
                }

                // 존 시스템 오버레이 토글(프리뷰 전용): 휘도를 존 0..X 11단계로 양자화 표시.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: zoneOverlayCheck
                        onToggled: win.zoneOverlay = checked
                    }
                    // J 토글과 동일 사유: 단축키 변경을 독립 Binding 으로 재푸시.
                    Binding { target: zoneOverlayCheck; property: "checked"; value: win.zoneOverlay }
                    Label {
                        Layout.fillWidth: true
                        text: "Zone System overlay  (Z)"
                        color: "white"; font.pixelSize: 12
                        elide: Text.ElideRight    // 라벨은 한 줄 유지, 상세 설명은 툴팁
                        verticalAlignment: Text.AlignVCenter
                        HoverHandler { id: zoneOverlayHover }
                        ToolTip.visible: zoneOverlayHover.hovered
                        ToolTip.delay: 500
                        ToolTip.text: "Luminance zones 0–X (1 zone = 1 stop, V = mid gray)"
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
                        text: "Display color management  (Ctrl+Shift+M)"
                        color: "white"; font.pixelSize: 12
                        elide: Text.ElideRight    // 라벨은 한 줄 유지, 상세 설명은 툴팁
                        verticalAlignment: Text.AlignVCenter
                        HoverHandler { id: displayCmHover }
                        ToolTip.visible: displayCmHover.hovered
                        ToolTip.delay: 500
                        ToolTip.text: "Match monitor gamut (preview only, export stays sRGB)"
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
                    // ⚠️일반 이미지(display-referred) 는 **하한 2500K** — 카메라 공간이 선형 sRGB
                    //   원색이라 그 아래에서 blue 게인이 실제 카메라 대비 폭발한다(실측 rel_gain
                    //   blue: 2000K 28.7 vs 카메라 2.57 = 11.2배 / 2500K 4.87 vs 2.04 = 2.4배 /
                    //   3000K 이상은 1.6배 이내). 8bit 로 구워진 사진을 2000K 로 재밸런싱하는 건
                    //   양자화 노이즈를 ×28 증폭하는 것이라 의미도 없다(라이트룸도 비-RAW 에는
                    //   절대 켈빈 대신 제한된 상대 범위를 준다). 고역은 오히려 실제 카메라보다
                    //   순해서(12000K 0.56 vs 0.71) 클램프 불필요.
                    //   ⚠️Bradford LMS 를 카메라 공간으로 쓰면 Temp 는 고쳐지지만(2000K 5.50)
                    //   8bit 프록시 왕복 오차가 실사진에서 2.0→8.3 code 로 4배가 된다 → 기각.
                    from: controller.isDisplayImage ? 2500 : 2000
                    to: 12000; value: 6500
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
                        text: (win.secOpen[14] ? "▾  " : "▸  ") + "Mist"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(14) }
                }
                ColumnLayout {
                    visible: win.secOpen[14]
                    Layout.fillWidth: true
                    spacing: 12

                    // 미스트(디퓨전) 필터 — 렌즈 앞 미세 입자의 산란. docs/mist_filter.md
                    // Amount/Character 는 셰이더 uniform 이라 실시간. Radius/Highlight 는 산란
                    // 필드(CPU, 프록시 3× 가우시안)를 다시 만들어야 하므로 드래그를 디바운스한다.
                    Timer {
                        id: mistFieldTimer; interval: 160
                        onTriggered: controller.requestMistField(mistRadiusSlider.value,
                                                                 mistHiSlider.value,
                                                                 mistAmtSlider.value)
                    }

                    Label {
                        text: "Mist Amount:  " + mistAmtSlider.value.toFixed(2)
                              + "  (0.4 ≈ 1/4, 0.6 ≈ 1/2)"
                        color: "white"
                    }
                    Slider {
                        id: mistAmtSlider
                        Layout.fillWidth: true
                        from: 0.0; to: 1.0; value: 0.0
                        property real defaultValue: 0.0
                        property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: {
                            if (pressed) _pendingReset = win.isDblPress(mistAmtSlider)
                            else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                        }
                        // Amount 가 0 을 벗어나는 순간 산란 필드가 필요해진다. 컨트롤러는
                        // 이 값으로만 '필드를 만들 가치가 있는가' 를 판단한다(main 주석).
                        onValueChanged: controller.setMistAmount(value)
                    }

                    Label {
                        text: "Mist Character:  " + mistCharSlider.value.toFixed(2) + "  (black ↔ white)"
                        color: "white"
                    }
                    Slider {
                        id: mistCharSlider
                        Layout.fillWidth: true
                        from: 0.0; to: 1.0; value: 0.0
                        property real defaultValue: 0.0
                        property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: {
                            if (pressed) _pendingReset = win.isDblPress(mistCharSlider)
                            else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                        }
                    }

                    Label {
                        text: "Mist Radius:  " + mistRadiusSlider.value.toFixed(2)
                        color: "white"
                    }
                    Slider {
                        id: mistRadiusSlider
                        Layout.fillWidth: true
                        from: 0.5; to: 2.0; value: 1.0
                        property real defaultValue: 1.0
                        property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onMoved: mistFieldTimer.restart()
                        onPressedChanged: {
                            if (pressed) _pendingReset = win.isDblPress(mistRadiusSlider)
                            else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                            if (!pressed) mistFieldTimer.restart()
                        }
                    }

                    // 하이라이트 보상 — 센서에서 클리핑돼 사라진 초과 에너지를 근사 복원한다.
                    // 0 = 순수 물리(에너지 보존). 이게 없으면 후광이 아니라 회색 막이 된다.
                    Label {
                        text: "Mist Highlight:  " + mistHiSlider.value.toFixed(2)
                              + (mistHiSlider.value === 0.0 ? "  (pure physics)" : "")
                        color: "white"
                    }
                    Slider {
                        id: mistHiSlider
                        Layout.fillWidth: true
                        from: 0.0; to: 2.0; value: 0.8
                        property real defaultValue: 0.8
                        property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onMoved: mistFieldTimer.restart()
                        onPressedChanged: {
                            if (pressed) _pendingReset = win.isDblPress(mistHiSlider)
                            else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                            if (!pressed) mistFieldTimer.restart()
                        }
                    }

                    // 산란광의 휘도는 둔 채 **색만** 받는 화면 쪽으로 되돌린다. 0=물리(산란광 그대로).
                    // 차가운 광원(LED)이 따뜻한 면에 섞여 창백해지는 것을 되돌릴 때 쓴다.
                    // ⚠️물리가 아니라 룩 노브다 — 근거와 실측은 mist.tint_scatter 도크스트링.
                    Label {
                        text: "Mist Color:  " + mistColorSlider.value.toFixed(2)
                              + (mistColorSlider.value === 0.0 ? "  (physical)" : "  (restore hue)")
                        color: "white"
                    }
                    Slider {
                        id: mistColorSlider
                        Layout.fillWidth: true
                        from: 0.0; to: 1.0; value: 0.5
                        property real defaultValue: 0.5
                        property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: {
                            if (pressed) _pendingReset = win.isDblPress(mistColorSlider)
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

                // 거칠기 = 멀티 옥타브(fBm) 감쇠비. 0=단일 옥타브, ↑=거친 옥타브가 섞여
                // 결정 뭉침(clumping)처럼 불규칙해진다. 세기(σ)는 불변.
                // 기본 0.1 = **실측 피팅값**: 필름 스캔 acf(lag1..8)에 (gridN, Roughness, USM)을
                // 동시 피팅하니 가정한 샤프닝과 무관하게 0~0.2 로 수렴(0.5 는 잔차 8배).
                // 즉 실제 필름은 이 해상도에서 픽셀 너머 구조가 거의 없다. 굵은 뭉침을 원하면 올린다.
                Label {
                    text: "Grain Roughness:  " + grainRoughSlider.value.toFixed(2) + "  (even ↔ clumpy)"
                    color: "white"
                }
                Slider {
                    id: grainRoughSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.1
                    property real defaultValue: 0.1
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(grainRoughSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                // 층 독립도 = R/G/B 발색층이 각자 독립 현상되는 정도. 0=흑백 단층 필름,
                // 1=컬러 필름의 물리(단, 층별 입상도차·염료확산이 없어 색얼룩이 과함). 세기(σ)는 불변.
                Label {
                    text: "Grain Color:  " + grainColorSlider.value.toFixed(2) + "  (mono ↔ 3-layer)"
                    color: "white"
                }
                Slider {
                    id: grainColorSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.3
                    property real defaultValue: 0.3
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(grainColorSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                // 입자 모양 — 사각 셀(기본) vs 원판. 세기(σ)·굵기(acf lag1)는 그대로고 분포만
                // 바뀐다: 첨도가 실측 필름(3.4~4.1) 쪽으로 간다(측정 3.03→3.28 / 2.80→3.34).
                // ⚠️export 가 크게 느려진다(그레인 단계 22배 — 2560 에서 22s→112s). 프리뷰도
                //    샘플당 해시가 27배라 무거워질 수 있다. 그래서 기본 꺼짐 + 옵트인.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: grainShapeCheck
                        checked: false
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Round grains — much slower export
(preview shows square grain while dragging)"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                        wrapMode: Text.WordWrap
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

                // 날짜 스탬프 컨트롤은 좌측 **독립 탭**(Ctrl+4)으로 옮겼다 — 컨트롤이 8개로
                // 늘어 Edit 목록을 밀어냈기 때문. 아래 로드 핸들러는 스탬프와 무관해 여기 남는다.
                Connections {
                    target: controller
                    // 새 파일 디코딩 완료 후 편집 복원/초기화(controller 가 fresh-load 1회만 발화).
                    function onEditsReady() {
                        // 새 파일 *디코딩 완료* 후: 저장된 편집이 있으면 복원, 없으면 기본값으로 초기화.
                        // (디코딩 전 트리거 금지 — 이전 이미지에 새 편집이 잘못 반영되는 것 방지)
                        win.clearPresetNotice()   // 사진이 바뀌면 이전 사진의 프리셋 배너는 무효
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
                        win.histReset(JSON.stringify(win.editParams()))   // 로드 상태 = undo baseline(지문도 갱신)
                    }
                }

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

                            // ---- 레이어 선택(최대 3, 각 독립 마스크+보정) ----
                            Label {
                                text: "Mask Layers"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 6
                                // 고정 높이 리스트뷰 — 항목당 [레이어 이름 | ✕]. 개수와 무관하게 높이 일정.
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 168
                                    color: "#242424"; radius: 4; border.color: "#444"; border.width: 1
                                    clip: true
                                    ListView {
                                        id: layerList
                                        anchors.fill: parent; anchors.margins: 3
                                        model: win.layerCount        // 동적: 존재하는 레이어만(1..5)
                                        spacing: 2
                                        currentIndex: win.activeLayer
                                        interactive: false           // 최대 5개라 스크롤 불필요 → 패널 스크롤과 충돌 방지
                                        delegate: Rectangle {
                                            width: ListView.view ? ListView.view.width : 0
                                            height: 30; radius: 3
                                            color: index === win.activeLayer ? "#3d5a80"
                                                   : (rowMouse.containsMouse ? "#333" : "transparent")
                                            // 행 선택(라벨/여백 클릭). ✕ 버튼이 위에 있어 삭제 클릭은 버튼이 소비.
                                            MouseArea {
                                                id: rowMouse; anchors.fill: parent; hoverEnabled: true
                                                onClicked: win.selectLayer(index)
                                            }
                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.leftMargin: 10; anchors.rightMargin: 4; spacing: 4
                                                Label {
                                                    Layout.fillWidth: true
                                                    text: "Layer " + (index + 1) + (controller.layerHasMask[index] ? "   ●" : "")
                                                    color: "#eee"; font.pixelSize: 12; elide: Text.ElideRight
                                                    verticalAlignment: Text.AlignVCenter
                                                }
                                                Button {                 // 레이어 삭제(최소 1개는 유지 — 비활성)
                                                    id: delBtn
                                                    text: "✕"; flat: true
                                                    implicitWidth: 26; implicitHeight: 24
                                                    enabled: win.layerCount > 1
                                                    ToolTip.text: "Delete this layer"; ToolTip.visible: hovered; ToolTip.delay: 500
                                                    onClicked: win.deleteLayer(index)
                                                    contentItem: Text {   // ✕ 흰색(비활성 시 흐림)
                                                        text: delBtn.text; color: "white"
                                                        opacity: delBtn.enabled ? 1.0 : 0.35
                                                        font.pixelSize: 13
                                                        horizontalAlignment: Text.AlignHCenter
                                                        verticalAlignment: Text.AlignVCenter
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                Button {                         // 레이어 추가(상한 5)
                                    Layout.fillWidth: true
                                    text: "+ Add Layer  (" + win.layerCount + "/" + win.maxLayers + ")"
                                    enabled: controller.imagePath !== "" && win.layerCount < win.maxLayers
                                    onClicked: win.addLayer()
                                }
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Each layer has its own mask + adjustments (e.g. Layer 1 = sky brighter, Layer 2 = mountains darker). ● = layer has a mask."
                                color: "#888"; font.pixelSize: 11
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Create Mask: 클래스 체크박스(복합 선택, 라이브 재조합) ----
                            Label {
                                text: "Create Mask"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: win.maskTab === 2
                                      ? "Pick a distance range — it joins whatever Scene / Face classes are ticked."
                                      : "Check one or more — the mask is the union of the selected classes."
                                color: "#888"; font.pixelSize: 11
                            }
                            // Scene / Face 탭. 표시만 전환하고 선택은 양쪽 모두 살아 있으므로
                            // 라벨에 ●개수를 띄워 숨은 탭의 선택이 보이게 한다.
                            // Controls 의 TabBar/Button 대신 Item+Rectangle 로 직접 그린다 —
                            // Windows 네이티브 스타일이 contentItem 커스터마이즈를 막아
                            // (콘솔 경고) 다크 테마 색을 못 맞추기 때문.
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 30
                                color: "transparent"
                                Rectangle {                    // 탭 스트립 하단 기준선
                                    anchors.bottom: parent.bottom
                                    width: parent.width; height: 1; color: "#444"
                                }
                                Row {
                                    anchors.fill: parent
                                    Repeater {
                                        model: ["Scene", "Face", "Depth"]
                                        delegate: Item {
                                            width: parent.width / 3
                                            height: parent.height
                                            property bool active: win.maskTab === index
                                            Text {
                                                anchors.centerIn: parent
                                                text: modelData + (win.maskTabCount(index) > 0
                                                      ? "   ● " + win.maskTabCount(index) : "")
                                                color: active ? "#8ab4f8"
                                                       : (tabHover.containsMouse ? "#ddd" : "#8a8a8a")
                                                font.pixelSize: 12; font.bold: active
                                            }
                                            Rectangle {        // 활성 탭 강조 밑줄
                                                anchors.bottom: parent.bottom
                                                width: parent.width; height: 2
                                                color: "#8ab4f8"; visible: parent.active
                                            }
                                            MouseArea {
                                                id: tabHover
                                                anchors.fill: parent; hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                // 탭 전환은 표시만 바꾸므로 세그 진행 중에도 허용.
                                                // Face 탭을 열 때 검출을 미리 돌린다(232KB, ~60ms) —
                                                // 부위를 체크하는 시점에 얼굴 목록이 있어야 기본값
                                                // (가장 큰 얼굴 1명)이 적용된다. 파싱 모델(340MB)은
                                                // 실제로 부위를 고를 때까지 안 받는다.
                                                onClicked: {
                                                    win.maskTab = index
                                                    if (index === 1 && controller.imagePath !== "")
                                                        controller.requestFaces()
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            // ---- 얼굴 선택(2명 이상일 때만) ----
                            // 검출된 얼굴 썸네일을 켜고/끈다. 배경 인물 제외 + 전경 특정 인물 지정용.
                            // 1명 이하면 고를 게 없어 통째로 숨긴다(패널 공간 0).
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                visible: win.maskTab === 1 && controller.faceCount > 1
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: "FACES  " + controller.faceCount
                                        color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                                    }
                                    Label {         // 전부 사용으로 되돌리기(= 선택 key 제거)
                                        text: "All"
                                        color: allFaceHover.containsMouse ? "#8ab4f8" : "#8a8a8a"
                                        font.pixelSize: 11; font.underline: allFaceHover.containsMouse
                                        MouseArea {
                                            id: allFaceHover
                                            anchors.fill: parent; anchors.margins: -4
                                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onClicked: win.selectAllFaces()
                                        }
                                    }
                                }
                                Row {               // 최대 5개 상한이라 줄바꿈이 불가능 → Flow 불필요
                                    spacing: 6
                                    Repeater {
                                        model: controller.faceThumbUrls
                                        delegate: Rectangle {
                                            width: 46; height: 46; radius: 4; clip: true
                                            color: "#1e1e1e"
                                            // 부위를 하나도 안 골랐으면 마스킹되는 얼굴이 없다 →
                                            // 전부 꺼진 것으로 표시(이때 전부 켜 보이면 거짓말).
                                            // 부위가 있고 명시 선택이 없으면 = 전체 사용.
                                            property bool picked: win.hasFacePart()
                                                && (!win._hasFaceSel(win.maskKeys)
                                                    || win.maskKeys.indexOf(controller.faceKeys[index]) >= 0)
                                            border.width: 2
                                            border.color: picked ? "#8ab4f8"
                                                          : (faceHover.containsMouse ? "#777" : "transparent")
                                            Image {
                                                anchors.fill: parent; anchors.margins: 2
                                                source: modelData
                                                cache: false        // 사진마다 교체됨 → 캐시 금지
                                                fillMode: Image.PreserveAspectCrop
                                                asynchronous: true
                                                opacity: parent.picked ? 1.0 : 0.35
                                            }
                                            MouseArea {
                                                id: faceHover
                                                anchors.fill: parent; hoverEnabled: true
                                                // 부위가 없으면 고를 대상이 없다 → 클릭 무의미
                                                enabled: !win.skyBusySlow && win.hasFacePart()
                                                cursorShape: enabled ? Qt.PointingHandCursor
                                                                     : Qt.ArrowCursor
                                                // faceKeys 와 썸네일 개수가 어긋난 순간(이미지
                                                // 전환 중 등)에 undefined 를 keys 에 넣지 않게 방어
                                                onClicked: {
                                                    var k = controller.faceKeys[index]
                                                    if (k) win.toggleFaceKey(k)
                                                }
                                            }
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    text: win.hasFacePart()
                                          ? "Click to include or exclude a face. Up to 5 largest faces."
                                          : "Check a face part below to start. Up to 5 largest faces."
                                    color: "#888"; font.pixelSize: 10
                                }
                            }
                            // ---- Depth 탭: 거리 범위 ----
                            // 세그가 못 가르는 축. 체크박스가 아니라 범위라서 클래스 그리드 대신
                            // 이 블록이 뜬다. 오버레이(빨강)를 켜둔 채 슬라이더를 움직이면
                            // 어디가 잡히는지 바로 보인다(SkySlider.keepOverlay).
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                visible: win.maskTab === 2
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    CheckBox {
                                        id: depthOnCheck
                                        enabled: controller.imagePath !== "" && !win.skyBusySlow
                                        // 켤 때는 이미지 히스토그램에서 시드(고정 상수는 장면마다
                                        // 어긋난다). 끌 때는 키만 제거.
                                        onToggled: {
                                            win.depthOn = checked
                                            if (checked) win._commitDepthAuto()
                                            else win._commitDepth()
                                        }
                                    }
                                    // 인라인 checked: 바인딩이 첫 클릭에 파괴돼 Clear/레이어 전환이
                                    // 반영 안 된다 → 독립 Binding(클래스 체크박스와 같은 이유).
                                    Binding {
                                        target: depthOnCheck; property: "checked"; value: win.depthOn
                                    }
                                    Label {
                                        Layout.fillWidth: true; text: "Use distance range"
                                        color: "white"; font.pixelSize: 12
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                }
                                SkySlider {
                                    id: depthNearSlider
                                    host: win; keepOverlay: true; enabled: win.depthOn
                                    label: "Near"; suffix: "  (0 = closest)"
                                    from: 0.0; to: 1.0; value: 0.5; defaultValue: 0.5
                                    onValueChanged: { win.depthNear = value; win._commitDepth() }
                                }
                                SkySlider {
                                    id: depthFarSlider
                                    host: win; keepOverlay: true; enabled: win.depthOn
                                    label: "Far"; suffix: "  (1 = farthest)"
                                    from: 0.0; to: 1.0; value: 1.0; defaultValue: 1.0
                                    onValueChanged: { win.depthFar = value; win._commitDepth() }
                                }
                                SkySlider {
                                    id: depthFeatherSlider
                                    host: win; keepOverlay: true; enabled: win.depthOn
                                    label: "Feather"
                                    from: 0.005; to: 0.4; value: 0.10; defaultValue: 0.10
                                    onValueChanged: { win.depthFeather = value; win._commitDepth() }
                                }
                                Label {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    text: "Starts on the background, seeded from this photo's own "
                                          + "distance histogram. Distance is relative per photo — the "
                                          + "same Near/Far lands differently on another shot, so "
                                          + "pasted edits may need a nudge."
                                    color: "#888"; font.pixelSize: 10
                                }
                            }
                            // ---- 얼굴 부위 머리글 + 전체 선택/해제 ----
                            // 부위가 11개라 얼굴 전체를 잡으려면 11번 눌러야 한다는 피드백.
                            // ⚠️아래 `Clear` 버튼은 대안이 아니다 — 그건 **전 레이어 초기화**
                            //   (마스크·로컬 조정 슬라이더까지 전부)라 훨씬 센 동작이다.
                            RowLayout {
                                id: facePartsRow
                                Layout.fillWidth: true
                                spacing: 10          // All/None 이 한 낱말로 붙어 읽히지 않게
                                visible: win.maskTab === 1
                                readonly property bool canEdit: controller.imagePath !== ""
                                        && !win.skyBusySlow && !controller.faceScanning
                                Label {
                                    Layout.fillWidth: true
                                    text: "FACE PARTS"
                                    color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                                }
                                Label {                       // 전체 체크(FACES 줄의 All 과 같은 표기)
                                    text: "All"
                                    opacity: facePartsRow.canEdit ? 1.0 : 0.4
                                    color: allPartsHover.containsMouse ? "#8ab4f8" : "#8a8a8a"
                                    font.pixelSize: 11; font.underline: allPartsHover.containsMouse
                                    MouseArea {
                                        id: allPartsHover
                                        anchors.fill: parent; anchors.margins: -4
                                        enabled: facePartsRow.canEdit
                                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: win.setAllFaceParts(true)
                                    }
                                }
                                Label {                       // 전체 해제(부위만 — 조정값은 그대로)
                                    text: "None"
                                    opacity: facePartsRow.canEdit ? 1.0 : 0.4
                                    color: nonePartsHover.containsMouse ? "#8ab4f8" : "#8a8a8a"
                                    font.pixelSize: 11; font.underline: nonePartsHover.containsMouse
                                    MouseArea {
                                        id: nonePartsHover
                                        anchors.fill: parent; anchors.margins: -4
                                        enabled: facePartsRow.canEdit
                                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: win.setAllFaceParts(false)
                                    }
                                }
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 4; rowSpacing: 2
                                visible: win.maskTab !== 2      // Depth 탭은 클래스가 아니라 범위
                                Repeater {
                                    model: win.maskTab === 1 ? controller.faceGroups
                                                             : controller.maskGroups
                                    delegate: RowLayout {
                                        Layout.fillWidth: true; spacing: 6
                                        CheckBox {
                                            id: maskKeyCheck
                                            // faceScanning 중 잠금(~60ms) — 검출 전에 체크하면
                                            // faceCount 가 0 이라 기본 얼굴 선택이 안 붙는다.
                                            // 얼굴 재조합은 ~70ms 라 그때마다 잠그면 클릭이 씹힌다
                                            // → 실제로 오래 걸릴 때(skyBusySlow)만 비활성.
                                            enabled: controller.imagePath !== "" && !win.skyBusySlow
                                                     && !controller.faceScanning
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
                            Button {
                                text: "Clear"
                                Layout.fillWidth: true
                                enabled: controller.imagePath !== ""
                                onClicked: win.resetSky()
                            }
                            // 선택 진행 중/완료 상태는 이미지 위 스피너 오버레이가 표시(win.skyBusySlow).
                            // 선택 '완료'(클리어 제외) → 마스크 오버레이 자동 표시
                            Connections {
                                target: controller
                                // 사용자 선택 → 오버레이 자동 표시. 단 사이드카 복원 재생성은 제외.
                                function onSkySelected() {
                                    if (win._maskRestore) win._maskRestore = false
                                    else win.showSkyMask = true
                                }
                            }

                            // 마스크 전체 옵션(오버레이/반전) — Brush 섹션 **위**에 배치(브러시
                            // 하위 기능으로 오독 방지). 설명은 라벨 대신 툴팁으로.
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
                                    Layout.fillWidth: true; text: "Show mask overlay  (O)"
                                    color: "white"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter
                                    HoverHandler { id: skyShowHover }
                                    ToolTip.visible: skyShowHover.hovered
                                    ToolTip.delay: 500
                                    ToolTip.text: "Highlight the selected area in red (preview only)"
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                CheckBox { id: skyInvertCheck }
                                Label {
                                    Layout.fillWidth: true; text: "Invert mask"
                                    color: "white"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter
                                    HoverHandler { id: skyInvertHover }
                                    ToolTip.visible: skyInvertHover.hovered
                                    ToolTip.delay: 500
                                    ToolTip.text: "Apply the adjustments to everything but the selection"
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Brush: 활성 레이어 마스크에 수동 획 추가/빼기 ----
                            // AI 마스크 디테일 수정 + 빈 레이어에 칠하면 순수 수동 마스크.
                            Label {
                                text: "Brush"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            // 세그먼트 토글(톤커브 RGB 채널 셀렉터와 동일 스타일) — 재클릭=끄기
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Repeater {
                                    model: [{ m: 1, t: "＋ Add  (A)", c: "#ff7d7d",
                                              tip: "Paint to add to the mask (A to toggle, Esc to stop)" },
                                            { m: 2, t: "− Subtract  (S)", c: "#7db8ff",
                                              tip: "Paint to subtract from the mask (S to toggle, Esc to stop)" }]
                                    delegate: Rectangle {
                                        required property var modelData
                                        readonly property bool active: win.brushMode === modelData.m
                                        Layout.fillWidth: true
                                        implicitHeight: 28
                                        radius: 4
                                        opacity: controller.imagePath !== "" ? 1.0 : 0.4
                                        color: active ? "#3a4a6b" : (segHover.hovered ? "#333333" : "#2a2a2a")
                                        border.color: active ? modelData.c : "#444"
                                        border.width: 1
                                        Text {
                                            anchors.centerIn: parent
                                            text: modelData.t
                                            color: active ? modelData.c : "#c9c9c9"
                                            font.pixelSize: 12; font.bold: active
                                        }
                                        HoverHandler { id: segHover; cursorShape: Qt.PointingHandCursor }
                                        TapHandler {
                                            enabled: controller.imagePath !== ""
                                            onTapped: win.setBrushMode(modelData.m)
                                        }
                                        ToolTip.visible: segHover.hovered
                                        ToolTip.delay: 500
                                        ToolTip.text: modelData.tip
                                    }
                                }
                            }
                            ColumnLayout {
                                visible: win.brushMode !== 0
                                Layout.fillWidth: true
                                spacing: 8
                                SkySlider {
                                    id: brushSizeSlider
                                    host: win; label: "Size"; suffix: "  (wheel)"
                                    keepOverlay: true    // 브러시 크기 조절 중에도 마스크 오버레이 유지
                                    // 하한 0.003 ≈ 프록시 짧은 변의 0.3%(코어 ~5px) — 미세 디테일용
                                    from: 0.003; to: 0.20; value: 0.06; defaultValue: 0.06
                                    onValueChanged: win.brushSize = value
                                }
                                SkySlider {
                                    id: brushFeatherSlider
                                    host: win; label: "Feather"; suffix: "  (Shift+wheel)"
                                    keepOverlay: true
                                    from: 0.0; to: 1.0; value: 0.5; defaultValue: 0.5
                                    onValueChanged: win.brushFeather = value
                                }
                                Button {
                                    Layout.fillWidth: true
                                    text: "Clear strokes"
                                    enabled: win.activeStrokeCount > 0
                                    onClicked: win.clearBrushStrokes()
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: win.activeStrokeCount + " stroke" + (win.activeStrokeCount === 1 ? "" : "s")
                                          + " on this layer  ·  undo: Ctrl+Z"
                                    color: "#9a9a9a"; font.pixelSize: 11
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
                                text: "Check classes (Scene / Face) or set a distance range (Depth) above to build the mask; the sliders apply only to the masked region. Applies to both preview and export."
                                color: "#888"; font.pixelSize: 11
                            }
                        }
                    }

                    // ===== index 3: Date Stamp (필름 데이트백 — 각인 텍스트·폰트·위치·색·글로우) =====
                    // Edit 안 접이식 섹션에서 독립 탭으로 분리(컨트롤 8개가 Edit 목록을 밀어냈다).
                    Flickable {
                        id: stampScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: stampCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: B.ScrollBar {
                            id: stampBar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle { implicitWidth: 8; radius: 4; color: stampBar.pressed ? "#cfcfcf" : "#9a9a9a" }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }
                        ColumnLayout {
                            id: stampCol
                            x: 16; y: 16
                            width: stampScroll.width - 32
                            spacing: 12
                            Label {
                                Layout.fillWidth: true
                                text: "DATE STAMP"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                CheckBox {
                                    id: stampCheck
                                    enabled: controller.imagePath !== ""
                                    onToggled: { win.dateStamp = checked; win.rememberStamp() }
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
                            // 폰트 방식. 번들(세그먼트 8종 + 타자기/터미널/콘덴스드, 모두 OFL) + 사용자 추가.
                            // ⚠️표시명과 키는 controller.stampFonts 한 곳에서 온다 — QML 에 라벨/키 배열을
                            //   따로 두면 순서가 어긋나 다른 폰트가 저장된다.
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label { text: "Style"; color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12 }
                                ComboBox {
                                    id: stampFontCombo
                                    Layout.fillWidth: true
                                    enabled: win.dateStamp && controller.imagePath !== ""
                                    model: controller.stampFonts
                                    textRole: "label"
                                    readonly property var keys: {
                                        var a = [], m = controller.stampFonts
                                        for (var i = 0; i < m.length; i++) a.push(m[i].key)
                                        return a
                                    }
                                    onActivated: { controller.setStampFont(keys[currentIndex]); win.rememberStamp() }
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
                                    // 누락 폰트(-1)면 **실제 렌더에 쓰이는** 기본 데이트백 폰트를 가리킨다.
                                    // 0 으로 접으면 Regular 를 보여주면서 Bold 로 그려져 콤보가 거짓을 말한다
                                    // (사이드카에 저장된 값은 그대로 user:… 이고, 배너가 그 사실을 알린다).
                                    value: {
                                        var i = stampFontCombo.keys.indexOf(controller.stampFont)
                                        return i >= 0 ? i : Math.max(0, stampFontCombo.keys.indexOf("7c_bold"))
                                    }
                                }
                            }
                            // 원하는 폰트가 없을 때를 위한 추가 경로(사용자 요청). 대화상자가 윈도우 폰트
                            // 폴더에서 열리므로 '윈도우 폰트 가져오기'와 '내 폰트 추가'를 하나로 덮는다.
                            // 고른 파일은 사용자 데이터 폴더로 **복사**한다 — 원본이 없어져도 사이드카가 열려야 한다.
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button {
                                    text: "Add font…"
                                    font.pixelSize: 11
                                    enabled: win.dateStamp
                                    onClicked: stampFontDialog.open()
                                }
                                Button {
                                    text: "Remove"
                                    font.pixelSize: 11
                                    // 번들 폰트는 지울 수 없다(설치 자산) — 추가한 폰트에만 활성.
                                    enabled: win.dateStamp && controller.stampFont.indexOf("user:") === 0
                                    onClicked: controller.removeStampFont(controller.stampFont)
                                }
                                Item { Layout.fillWidth: true }
                            }
                            // 남의 사용자 폰트로 만든 사이드카/레시피를 열면 그 파일이 없는 것이 정상이다.
                            // 조용히 다른 모습으로 렌더되지 않도록 알린다(렌더는 기본 데이트백 폰트로 폴백).
                            Label {
                                Layout.fillWidth: true
                                visible: win.stampFontError !== ""
                                text: "⚠ " + win.stampFontError
                                color: "#e08a8a"; font.pixelSize: 10; wrapMode: Text.WordWrap
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: win.dateStamp && controller.stampFontMissing
                                text: "⚠ This photo uses an added font that isn't on this machine "
                                      + "— drawn with the default date-back font. Add the file to restore it."
                                color: "#e8d5b0"; font.pixelSize: 10; wrapMode: Text.WordWrap
                            }
                            FileDialog {
                                id: stampFontDialog
                                title: "Add a stamp font"
                                nameFilters: ["Font files (*.ttf *.otf)"]
                                currentFolder: "file:///C:/Windows/Fonts"
                                onAccepted: {
                                    // 실패(폰트로 읽히지 않는 파일)를 조용히 넘기지 않는다 — 손상 폰트를
                                    // 거부하게 된 뒤로는 실제로 일어나는 경로다.
                                    if (controller.addStampFont(selectedFile)) {
                                        win.stampFontError = ""
                                        win.rememberStamp()
                                    } else {
                                        win.stampFontError = "Couldn't read that file as a font."
                                    }
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
                                // ⚠️더블클릭은 **공장 기본값**으로 되돌린다(앱 전체 슬라이더 관습).
                                //   내 기본값으로 되돌리게 했다가 철회했다 — 슬라이더를 놓을 때마다 그 값이
                                //   내 기본값으로 기억되므로 '기본값 == 현재값'이 되어 더블클릭이 무동작이 된다.
                                property real defaultValue: 0.032
                                property real _lastPressMs: 0     // isDblPress 가 읽고 씀(없으면 더블클릭 리셋 무동작)
                                property bool _pendingReset: false
                                // ⚠️여기에 디바운스를 넣지 말 것 — 한 번 넣었다가 철회했다. 스프라이트 재렌더는
                                // 21.3ms(기본 3.2%, 실측)라 그대로 두면 약 47fps 로 **실시간으로 따라온다**.
                                // 150ms Timer 를 끼우면 초당 6~7회로 떨어져 오히려 뚝뚝 끊긴다(사용자 확인).
                                // Grain 슬라이더가 디바운스인 것은 거기가 장면 그레인(GPU 라이브)과 스탬프
                                // 스프라이트(CPU)를 동시에 물고 있어서지, 스프라이트 비용 자체 때문이 아니다.
                                // 드래그(user)만 controller 로 push — 프로그램 대입(로드/리셋)은 onMoved 안 불림.
                                onMoved: controller.setStampSize(value)
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(stampSizeSlider)
                                    var _wasReset = false
                                    if (!pressed && _pendingReset) {
                                        value = defaultValue; controller.setStampSize(defaultValue)
                                        _pendingReset = false; _wasReset = true
                                    }
                                    // 드래그 중이 아니라 **릴리즈 때** 기억(매 프레임 디스크 쓰기 방지).
                                    // ⚠️더블클릭 리셋은 제외 — 공장 기본값을 잠깐 보려던 것이 내 기본값을
                                    //   조용히 덮으면, 다음 사진부터 설정이 사라진 것처럼 보인다.
                                    if (!pressed && !_wasReset) win.rememberStamp()
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
                                property real defaultValue: 0.05      // 더블클릭=공장 기본값(위 주석 참조)
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onMoved: controller.setStampMargin(value)
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(stampMarginSlider)
                                    var _wasReset = false
                                    if (!pressed && _pendingReset) {
                                        value = defaultValue; controller.setStampMargin(defaultValue)
                                        _pendingReset = false; _wasReset = true
                                    }
                                    if (!pressed && !_wasReset) win.rememberStamp()
                                }
                            }
                            // 각인 색 — 흑백 사진에서 앰버가 튀는 것을 피할 수 있게(피드백). 중성색을 고르면
                            // 핫코어→중간→헤일로 램프가 통째로 무채색이 되어 백색 각인이 된다.
                            Label {
                                text: "Colour"
                                color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12
                                ToolTip.visible: stampColLblHover.hovered
                                ToolTip.delay: 600
                                ToolTip.text: "White or grey gives a colourless imprint — good for black-and-white."
                                    + "\n\nStrong colours can look a little stronger on screen than in the"
                                    + "\nsaved file, over bright areas. The saved file is the accurate one."
                                HoverHandler { id: stampColLblHover }
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 6
                                Repeater {
                                    model: controller.stampColors
                                    delegate: Rectangle {
                                        width: 26; height: 22; radius: 4
                                        color: modelData
                                        opacity: win.dateStamp ? 1.0 : 0.45
                                        readonly property bool sel: controller.stampColor.toLowerCase()
                                                                    === String(modelData).toLowerCase()
                                        border.color: sel ? "#8ab4f8" : "#555"
                                        border.width: sel ? 2 : 1
                                        TapHandler {
                                            enabled: win.dateStamp && controller.imagePath !== ""
                                            onTapped: { controller.setStampColor(modelData); win.rememberStamp() }
                                        }
                                    }
                                }
                                // 팔레트에 없는 색: 표준 색 선택 대화상자. 현재 색이 팔레트 밖이면 여기에 보인다.
                                Rectangle {
                                    width: 26; height: 22; radius: 4
                                    color: stampColorDialog.customShown ? controller.stampColor : "#2b2b2b"
                                    opacity: win.dateStamp ? 1.0 : 0.45
                                    border.color: stampColorDialog.customShown ? "#8ab4f8" : "#555"
                                    border.width: stampColorDialog.customShown ? 2 : 1
                                    Label {
                                        anchors.centerIn: parent
                                        visible: !stampColorDialog.customShown
                                        text: "+"; color: "#cfcfcf"; font.pixelSize: 13
                                    }
                                    TapHandler {
                                        enabled: win.dateStamp && controller.imagePath !== ""
                                        onTapped: {
                                            stampColorDialog.selectedColor = controller.stampColor
                                            stampColorDialog.open()
                                        }
                                    }
                                }
                            }
                            // 글로우 밝기 = 헤일로 가중 배율(0 이면 번짐 없는 또렷한 각인).
                            Label {
                                text: "Glow:  " + stampGlowSlider.value.toFixed(2) + "×"
                                color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12
                            }
                            Slider {
                                id: stampGlowSlider
                                Layout.fillWidth: true
                                enabled: win.dateStamp && controller.imagePath !== ""
                                from: 0.0; to: 2.0; value: 1.0
                                property real defaultValue: 1.0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onMoved: controller.setStampGlow(value)
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(stampGlowSlider)
                                    var _wasReset = false
                                    if (!pressed && _pendingReset) {
                                        value = defaultValue; controller.setStampGlow(defaultValue)
                                        _pendingReset = false; _wasReset = true
                                    }
                                    if (!pressed && !_wasReset) win.rememberStamp()
                                }
                            }
                            // 글로우 영역 = 헤일로 반경 배율. ⚠️키우면 스프라이트 캔버스가 함께 커져
                            // 재렌더가 무거워진다(실측: 최대 크기·최대 영역에서 기본의 약 2.3배).
                            // 기본값 이하는 예전과 **비트 동일**한 정확 경로로 렌더한다(date_stamp 주석 참조).
                            Label {
                                text: "Glow area:  " + stampSpreadSlider.value.toFixed(2) + "×"
                                color: win.dateStamp ? "white" : "#777"; font.pixelSize: 12
                            }
                            Slider {
                                id: stampSpreadSlider
                                Layout.fillWidth: true
                                enabled: win.dateStamp && controller.imagePath !== ""
                                from: 0.4; to: 2.0; value: 1.0
                                property real defaultValue: 1.0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onMoved: controller.setStampSpread(value)
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(stampSpreadSlider)
                                    var _wasReset = false
                                    if (!pressed && _pendingReset) {
                                        value = defaultValue; controller.setStampSpread(defaultValue)
                                        _pendingReset = false; _wasReset = true
                                    }
                                    if (!pressed && !_wasReset) win.rememberStamp()
                                }
                            }
                            ColorDialog {
                                id: stampColorDialog
                                title: "Stamp colour"
                                // 팔레트 밖의 색이 선택돼 있으면 'Custom' 스와치에 그 색을 보여준다.
                                readonly property bool customShown: {
                                    var cs = controller.stampColors, c = controller.stampColor.toLowerCase()
                                    for (var i = 0; i < cs.length; i++)
                                        if (String(cs[i]).toLowerCase() === c) return false
                                    return true
                                }
                                onAccepted: {
                                    // ColorDialog 는 #AARRGGBB 를 줄 수 있다 — 알파를 버리고 #RRGGBB 로 넘긴다
                                    // (스프라이트 알파는 글로우 세기가 정하므로 색의 알파는 의미가 없다).
                                    var h = selectedColor.toString()
                                    if (h.length === 9) h = "#" + h.substring(3)
                                    controller.setStampColor(h)
                                    win.rememberStamp()
                                }
                            }
                            Timer {
                                id: stampDebounce
                                interval: 200
                                onTriggered: controller.setStampText(stampField.text)
                            }
                        }
                    }

                    // ===== index 4: Wallpaper (3분할 트립틱 배경화면 합성) =====
                    Flickable {
                        id: wallScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: wallCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: B.ScrollBar {
                            id: wallBar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle { implicitWidth: 8; radius: 4; color: wallBar.pressed ? "#cfcfcf" : "#9a9a9a" }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: wallCol
                            x: 16; y: 16
                            width: wallScroll.width - 32
                            spacing: 12

                            Label {
                                text: "Wallpaper"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: win.wallLayout === 0
                                      ? "Select a photo in the explorer (single click), then click a slot below. Each photo is developed with its own edits, then composed side by side."
                                      : "Magazine spread: the center slot becomes the full-bleed main photo; the other two appear as small frames in the text column."
                                color: "#888"; font.pixelSize: 11
                            }

                            ComboBox {
                                id: wallLayoutCombo
                                Layout.fillWidth: true
                                model: ["Triptych (3-up)", "Magazine spread"]
                                currentIndex: win.wallLayout
                                onActivated: { win.wallLayout = currentIndex; win.wallSave("layout", currentIndex) }
                                // ⚠️사용자 조작 시 currentIndex 바인딩이 끊기므로 프리셋 적용을
                                // 반영하려면 명시적 동기화가 필요(아래 입력 위젯들도 동일).
                                Connections {
                                    target: win
                                    function onWallLayoutChanged() { wallLayoutCombo.currentIndex = win.wallLayout }
                                }
                                Connections {
                                    target: wallLayoutCombo.popup
                                    function onClosed() { viewport.forceActiveFocus() }
                                }
                            }

                            // ---- 프리셋: 이름 붙인 설정 묶음(사진 슬롯·텍스트·옵션 전체) ----
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                ComboBox {
                                    id: wallPresetCombo
                                    Layout.fillWidth: true
                                    model: win.wallPresets
                                    displayText: currentIndex < 0 || win.wallPresets.length === 0
                                                 ? "Presets…" : currentText
                                    onActivated: {
                                        var m = controller.loadWallpaperPreset(currentText)
                                        if (m && m.layout !== undefined) {
                                            win.wallApplyState(m)
                                            win.wallResult = "Preset loaded: " + currentText
                                        }
                                    }
                                    Connections {
                                        target: wallPresetCombo.popup
                                        function onClosed() { viewport.forceActiveFocus() }
                                    }
                                }
                                // flat Button 글리프는 어두워 안 보임 → 흰 글리프 Rectangle 패턴
                                Rectangle {
                                    readonly property bool canDelete: wallPresetCombo.currentIndex >= 0
                                                                      && win.wallPresets.length > 0
                                    width: 28; height: 28; radius: 14
                                    color: (canDelete && wallPresetDelHover.hovered) ? "#3a3f4b" : "transparent"
                                    Text {
                                        anchors.centerIn: parent
                                        text: "✕"
                                        color: parent.canDelete ? "#e6e6e6" : "#5a5a5a"
                                        font.pixelSize: 13
                                    }
                                    HoverHandler { id: wallPresetDelHover; enabled: parent.canDelete }
                                    ToolTip.visible: wallPresetDelHover.hovered
                                    ToolTip.delay: 500
                                    ToolTip.text: "Delete this preset"
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: parent.canDelete
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            var n = wallPresetCombo.currentText
                                            controller.deleteWallpaperPreset(n)
                                            win.wallRefreshPresets()
                                            win.wallResult = "Preset deleted: " + n
                                        }
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextField {
                                    id: wallPresetName
                                    Layout.fillWidth: true
                                    placeholderText: "New preset name"
                                    font.pixelSize: 12
                                    onAccepted: wallPresetSave.clicked()
                                }
                                Button {
                                    id: wallPresetSave
                                    text: "Save"
                                    enabled: wallPresetName.text.trim() !== ""
                                    onClicked: {
                                        var n = wallPresetName.text.trim()
                                        controller.saveWallpaperPreset(n, win.wallCurrentState())
                                        win.wallRefreshPresets()
                                        wallPresetName.text = ""
                                        win.wallResult = "Preset saved: " + n
                                    }
                                }
                            }

                            // ---- 목업 프리뷰: 합성 수학(compose_wallpaper / compose_magazine) 미러 ----
                            // 썸네일을 cover-fit 스케일 + 가로 오프셋으로 clip — 실제 합성과 동일 기하.
                            // (PreserveAspectCrop 은 크롭 위치 지정 불가라 Stretch+수동 배치 필수)
                            Rectangle {
                                id: wallPreview
                                Layout.fillWidth: true
                                // 선택한 캔버스 비율을 그대로 반영(16:9 / 16:10 / 화면 비율)
                                Layout.preferredHeight: width * win.wallResH[win.wallResIndex]
                                                        / Math.max(1, win.wallResW[win.wallResIndex])
                                color: win.wallLayout === 0 ? "black" : "#f6f5f1"
                                radius: 4; clip: true
                                readonly property real gapPx: win.wallGap * width / win.wallResW[win.wallResIndex]
                                readonly property real cellW: (width - 2 * gapPx) / 3
                                // 잡지: 메인 사진(가운데 슬롯) 61% 풀블리드 + 텍스트 칼럼의 작은 사진 2장
                                readonly property real mainW: width * 0.61
                                readonly property real mainX: win.wallMainSide === 0 ? 0 : width - mainW
                                // 안전영역(compose_magazine 과 동일 수식): 지정 비율들의 가시영역 교집합
                                readonly property var safeRect: {
                                    var W = width, H = height, sw = W, sh = H
                                    var arr = win.wallSafeAspects
                                    for (var i = 0; i < arr.length; i++) {
                                        var a = arr[i]
                                        var vw = Math.min(W, H * a)
                                        sw = Math.min(sw, vw); sh = Math.min(sh, vw / a)
                                    }
                                    return { w: sw, h: sh, x: (W - sw) / 2, y: (H - sh) / 2 }
                                }
                                readonly property real mOut: safeRect.h * (170 / 2160)
                                readonly property real mIn: safeRect.h * (190 / 2160)
                                readonly property real colL: Math.max(safeRect.x,
                                                                      win.wallMainSide === 0 ? mainW : 0)
                                readonly property real colR: Math.min(safeRect.x + safeRect.w,
                                                                      win.wallMainSide === 0 ? width : width - mainW)
                                readonly property real colW: Math.max(10, colR - colL - mOut - mIn)
                                readonly property real smallGap: safeRect.h * (40 / 2160)
                                readonly property real smallW: (colW - smallGap) / 2

                                Repeater {
                                    model: 3
                                    Item {
                                        id: wallCell
                                        required property int index
                                        readonly property bool mag: win.wallLayout === 1
                                        readonly property bool isMain: index === 1
                                        // 트립틱: 3등분 셀 / 잡지: 메인은 풀블리드(캔버스 기준),
                                        // 0·2 는 텍스트 칼럼 안 작은 판(안전영역 기준 — 합성과 동일)
                                        readonly property real smallH: wallPreview.safeRect.h * 0.34
                                        x: !mag ? index * (wallPreview.cellW + wallPreview.gapPx)
                                                : (isMain ? wallPreview.mainX
                                                          : wallPreview.colL + wallPreview.mOut
                                                            + (index === 0 ? 0 : wallPreview.smallW + wallPreview.smallGap))
                                        y: !mag || isMain ? 0
                                           : wallPreview.safeRect.y + wallPreview.safeRect.h
                                             - wallPreview.mOut - wallPreview.safeRect.h * (66 / 2160) - smallH
                                        width: !mag ? wallPreview.cellW
                                                    : (isMain ? wallPreview.mainW : wallPreview.smallW)
                                        height: !mag || isMain ? wallPreview.height : smallH
                                        clip: true

                                        // 빈 슬롯: 어디에 어떤 크기로 들어갈지 보이도록 자리 표시
                                        Rectangle {
                                            anchors.fill: parent
                                            visible: win.wallSlots[wallCell.index] === ""
                                            color: win.wallLayout === 0 ? "#141414" : "#e7e5df"
                                            border.width: 1
                                            border.color: win.wallLayout === 0 ? "#4a4a4a" : "#c9c7c1"
                                            Text {
                                                anchors.centerIn: parent
                                                width: parent.width - 8
                                                horizontalAlignment: Text.AlignHCenter
                                                elide: Text.ElideRight
                                                text: "Frame 0" + win.wallFrameNo(wallCell.index)
                                                color: win.wallLayout === 0 ? "#7a7a7a" : "#9a978f"
                                                font.pixelSize: 10
                                            }
                                        }

                                        Image {
                                            readonly property string p: win.wallSlots[parent.index]
                                            visible: p !== ""
                                            // wallthumb = 사이드카 지오메트리(크롭/회전/원근) 적용 썸네일
                                            // → 오프셋 조절 기준이 실제 export 프레이밍과 일치.
                                            // ?r= 은 편집 저장 시 URL 을 바꿔 QML 이미지 캐시 무효화.
                                            source: p !== "" ? "image://wallthumb/" + encodeURIComponent(p)
                                                               + "?r=" + controller.editsRevision : ""
                                            sourceSize.width: 256
                                            asynchronous: true
                                            fillMode: Image.Stretch
                                            // 잡지의 작은 판은 크롭 0%(fit), 그 외는 cover
                                            readonly property bool fitMode: parent.mag && !parent.isMain
                                            readonly property real s: (implicitWidth > 0 && implicitHeight > 0)
                                                ? (fitMode ? Math.min(parent.height / implicitHeight, parent.width / implicitWidth)
                                                           : Math.max(parent.height / implicitHeight, parent.width / implicitWidth))
                                                : 1
                                            width: implicitWidth * s
                                            height: implicitHeight * s
                                            // 합성과 동일: 오프셋은 실제로 잘리는 축(여유가 큰 쪽)에 적용
                                            readonly property real slackX: Math.max(0, width - parent.width)
                                            readonly property real slackY: Math.max(0, height - parent.height)
                                            readonly property real t: (win.wallOffsets[parent.index] + 1) / 2
                                            x: fitMode ? 0 : -(slackX >= slackY ? slackX * t : slackX / 2)
                                            y: fitMode ? 0 : -(slackX >= slackY ? slackY / 2 : slackY * t)
                                        }
                                    }
                                }
                                // 잡지 텍스트 칼럼 자리 표시(제목·리드문 위치 감만 잡는 플레이스홀더)
                                Column {
                                    visible: win.wallLayout === 1
                                    x: wallPreview.colL + wallPreview.mOut
                                    y: wallPreview.safeRect.y + wallPreview.mOut
                                    width: wallPreview.colW
                                    spacing: 6
                                    Text {
                                        text: win.wallKicker
                                        color: "#9c3b2e"; font.pixelSize: 7; font.bold: true
                                    }
                                    Text {
                                        width: parent.width; wrapMode: Text.WordWrap
                                        text: win.wallHeadline !== "" ? win.wallHeadline : "Headline"
                                        color: "#16161a"; font.pixelSize: 16; font.bold: true
                                        font.family: ["Constantia", "Franklin Gothic Medium Cond",
                                                      "Noto Serif KR", "Noto Sans KR"][win.wallTypeface]
                                    }
                                    Text {
                                        width: parent.width; wrapMode: Text.WordWrap
                                        maximumLineCount: 3; elide: Text.ElideRight
                                        text: win.wallDeck
                                        color: "#76767c"; font.pixelSize: 8
                                    }
                                }
                                // 안전영역 가이드 — 다른 비율에서 잘려나가는 띠를 눈으로 확인
                                Rectangle {
                                    visible: win.wallDualAspect
                                             && (wallPreview.safeRect.x > 0.5 || wallPreview.safeRect.y > 0.5)
                                    x: wallPreview.safeRect.x; y: wallPreview.safeRect.y
                                    width: wallPreview.safeRect.w; height: wallPreview.safeRect.h
                                    color: "transparent"
                                    border.width: 1
                                    border.color: win.wallLayout === 0 ? "#6688cc" : "#8ab4f8"
                                    opacity: 0.75
                                }
                            }

                            // ---- 슬롯 카드 x3: **화면 좌→우 순서로 나열**(Frame 01/02/03) ----
                            // index=목록상 위치, slot=실제 슬롯 인덱스(순서가 다를 수 있음)
                            Repeater {
                                model: win.wallSlotOrder
                                Rectangle {
                                    required property int index
                                    required property var modelData
                                    id: wallCard
                                    readonly property int slot: modelData
                                    readonly property string cardLabel:
                                        "Frame 0" + (index + 1)
                                        + (win.wallLayout === 1 && slot === 1 ? " · Main" : "")
                                    Layout.fillWidth: true
                                    implicitHeight: cardCol.implicitHeight + 20
                                    color: "#242424"; radius: 4
                                    border.color: wallCardMouse.containsMouse ? "#8ab4f8" : "#444"
                                    border.width: 1
                                    readonly property string slotPath: win.wallSlots[slot]

                                    // 카드 클릭 = 탐색기 현재 선택 파일 할당(✕/슬라이더가 위에서 우선)
                                    MouseArea {
                                        id: wallCardMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: win.wallAssign(wallCard.slot)
                                    }

                                    ColumnLayout {
                                        id: cardCol
                                        x: 10; y: 10
                                        width: parent.width - 20
                                        spacing: 6

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            Rectangle {
                                                width: 48; height: 48; radius: 3
                                                color: "#1e1e1e"; clip: true
                                                Image {
                                                    id: wallCardThumb
                                                    anchors.fill: parent
                                                    fillMode: Image.PreserveAspectCrop
                                                    asynchronous: true
                                                    sourceSize.width: 96
                                                    source: wallCard.slotPath !== ""
                                                            ? "image://wallthumb/" + encodeURIComponent(wallCard.slotPath)
                                                              + "?r=" + controller.editsRevision : ""
                                                }
                                                Text {
                                                    anchors.centerIn: parent
                                                    visible: wallCard.slotPath === ""
                                                    text: "+"
                                                    color: "#777"; font.pixelSize: 22
                                                }
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: 2
                                                Label {
                                                    Layout.fillWidth: true
                                                    elide: Text.ElideMiddle
                                                    text: wallCard.slotPath !== ""
                                                          ? wallCard.slotPath.split(/[\\/]/).pop()
                                                          : wallCard.cardLabel + " — click to assign"
                                                    color: wallCard.slotPath !== "" ? "#e6e6e6" : "#888"
                                                    font.pixelSize: 12
                                                }
                                                Label {
                                                    // 가로 사진 경고(강한 크롭). 썸네일 로드 후에만 판단 가능.
                                                    visible: wallCard.slotPath !== ""
                                                             && wallCardThumb.status === Image.Ready
                                                             && wallCardThumb.implicitWidth > wallCardThumb.implicitHeight
                                                    text: "⚠ landscape — heavy crop"
                                                    color: "#E0A226"; font.pixelSize: 10
                                                }
                                            }
                                            // ✕ 는 flat Button 글리프가 어두워 안 보임 → 검색창 ✕ 와 같은
                                            // Rectangle+Text 패턴(흰 글리프, 호버 배경)으로 직접 구성.
                                            Rectangle {
                                                visible: wallCard.slotPath !== ""
                                                width: 24; height: 24; radius: 12
                                                color: wallClrHover.hovered ? "#3a3f4b" : "transparent"
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: "✕"; color: "#e6e6e6"; font.pixelSize: 12
                                                }
                                                HoverHandler { id: wallClrHover }
                                                ToolTip.visible: wallClrHover.hovered
                                                ToolTip.delay: 500
                                                ToolTip.text: "Clear this slot"
                                                MouseArea {
                                                    anchors.fill: parent
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: win.wallClearSlot(wallCard.slot)
                                                }
                                            }
                                        }

                                        // 잡지 레이아웃의 사진 제목(인덱스 줄에 인쇄됨) — 사용자 입력
                                        // 또는 캡션 자동 채우기/프리셋 로드로 갱신
                                        TextField {
                                            id: wallTitleField
                                            Layout.fillWidth: true
                                            visible: win.wallLayout === 1 && wallCard.slotPath !== ""
                                            placeholderText: "Frame title (printed in the index)"
                                            text: win.wallTitles[wallCard.slot]
                                            font.pixelSize: 12
                                            onTextEdited: win.wallSetTitle(wallCard.slot, text)
                                            Connections {
                                                target: win
                                                function onWallTitlesChanged() {
                                                    var v = win.wallTitles[wallCard.slot]
                                                    if (wallTitleField.text !== v) wallTitleField.text = v
                                                }
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            // 오프셋은 cover 크롭에만 의미 있음 — 잡지의 작은 판은 크롭 0%
                                            visible: wallCard.slotPath !== ""
                                                     && (win.wallLayout === 0 || wallCard.slot === 1)
                                            Label {
                                                // 잡지 메인 사진는 세로 사진이면 위아래가 잘린다 → 축을 알려줌
                                                text: (win.wallLayout === 1 && wallCard.slot === 1)
                                                      ? "Crop" : "Offset"
                                                color: "#aaa"; font.pixelSize: 11
                                            }
                                            Slider {
                                                id: wallOffSlider
                                                Layout.fillWidth: true
                                                from: -1.0; to: 1.0
                                                value: win.wallOffsets[wallCard.slot]
                                                onMoved: win.wallSetOffset(wallCard.slot, value)
                                                Connections {
                                                    target: win
                                                    function onWallOffsetsChanged() {
                                                        var v = win.wallOffsets[wallCard.slot]
                                                        if (wallOffSlider.value !== v) wallOffSlider.value = v
                                                    }
                                                }
                                                property real defaultValue: 0.0
                                                property real _lastPressMs: 0
                                                property bool _pendingReset: false
                                                onPressedChanged: {
                                                    if (pressed) {
                                                        _pendingReset = win.isDblPress(wallOffSlider)
                                                        return
                                                    }
                                                    if (_pendingReset) {
                                                        _pendingReset = false
                                                        win.wallSetOffset(wallCard.slot, defaultValue)
                                                    }
                                                    win.wallSaveOffset(wallCard.slot)
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- 잡지 레이아웃 전용: 텍스트(사용자 입력) + 서체/메인 사진 위치 ----
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: win.wallLayout === 1
                                spacing: 8
                                Label {
                                    text: "Text"
                                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                    font.capitalization: Font.AllUppercase
                                }
                                TextField {
                                    id: wallKickerField
                                    Layout.fillWidth: true
                                    placeholderText: "Kicker (e.g. Photo Essay)"
                                    text: win.wallKicker; font.pixelSize: 12
                                    onTextEdited: { win.wallKicker = text; controller.setWallpaperText("kicker", text) }
                                    Connections {
                                        target: win
                                        function onWallKickerChanged() {
                                            if (wallKickerField.text !== win.wallKicker) wallKickerField.text = win.wallKicker
                                        }
                                    }
                                }
                                TextField {
                                    id: wallHeadlineField
                                    Layout.fillWidth: true
                                    placeholderText: "Headline"
                                    text: win.wallHeadline; font.pixelSize: 12
                                    onTextEdited: { win.wallHeadline = text; controller.setWallpaperText("headline", text) }
                                    Connections {
                                        target: win
                                        function onWallHeadlineChanged() {
                                            if (wallHeadlineField.text !== win.wallHeadline) wallHeadlineField.text = win.wallHeadline
                                        }
                                    }
                                }
                                ScrollView {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 84
                                    TextArea {
                                        id: wallDeckField
                                        placeholderText: "Deck (lead paragraph)"
                                        text: win.wallDeck; font.pixelSize: 12
                                        wrapMode: TextArea.Wrap
                                        onTextChanged: {
                                            if (text === win.wallDeck) return
                                            win.wallDeck = text; controller.setWallpaperText("deck", text)
                                        }
                                        Connections {
                                            target: win
                                            function onWallDeckChanged() {
                                                if (wallDeckField.text !== win.wallDeck) wallDeckField.text = win.wallDeck
                                            }
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    TextField {
                                        id: wallPlaceField
                                        Layout.fillWidth: true
                                        placeholderText: "Place"
                                        text: win.wallPlace; font.pixelSize: 12
                                        onTextEdited: { win.wallPlace = text; controller.setWallpaperText("place", text) }
                                        Connections {
                                            target: win
                                            function onWallPlaceChanged() {
                                                if (wallPlaceField.text !== win.wallPlace) wallPlaceField.text = win.wallPlace
                                            }
                                        }
                                    }
                                    TextField {
                                        id: wallDateField
                                        Layout.fillWidth: true
                                        placeholderText: "Date (auto from EXIF)"
                                        text: win.wallDate; font.pixelSize: 12
                                        onTextEdited: { win.wallDate = text; controller.setWallpaperText("date", text) }
                                        Connections {
                                            target: win
                                            function onWallDateChanged() {
                                                if (wallDateField.text !== win.wallDate) wallDateField.text = win.wallDate
                                            }
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    text: "Frame titles are filled from each photo's caption when you assign it; edit them freely. Shooting info comes from EXIF — leave the date empty to use the main photo's capture month."
                                    color: "#888"; font.pixelSize: 11
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    ComboBox {
                                        id: wallFaceCombo
                                        Layout.fillWidth: true
                                        model: ["Serif", "Sans", "Serif (한글)", "Sans (한글)"]
                                        currentIndex: win.wallTypeface
                                        onActivated: { win.wallTypeface = currentIndex; win.wallSave("typeface", currentIndex) }
                                        Connections {
                                            target: win
                                            function onWallTypefaceChanged() { wallFaceCombo.currentIndex = win.wallTypeface }
                                        }
                                        Connections {
                                            target: wallFaceCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                    ComboBox {
                                        id: wallMainCombo
                                        Layout.fillWidth: true
                                        model: ["Main left", "Main right"]
                                        currentIndex: win.wallMainSide
                                        onActivated: { win.wallMainSide = currentIndex; win.wallSave("mainSide", currentIndex) }
                                        Connections {
                                            target: win
                                            function onWallMainSideChanged() { wallMainCombo.currentIndex = win.wallMainSide }
                                        }
                                        Connections {
                                            target: wallMainCombo.popup
                                            function onClosed() { viewport.forceActiveFocus() }
                                        }
                                    }
                                }
                            }

                            // ---- 트립틱 전용: 갭 ----
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: win.wallLayout === 0
                                spacing: 8
                                Label {
                                    text: "Gap:  " + win.wallGap + " px"
                                    color: "white"
                                }
                                Slider {
                                    id: wallGapSlider
                                    Layout.fillWidth: true
                                    from: 0; to: 60; stepSize: 1
                                    value: win.wallGap
                                    onMoved: win.wallGap = Math.round(value)
                                    Connections {
                                        target: win
                                        function onWallGapChanged() {
                                            if (wallGapSlider.value !== win.wallGap) wallGapSlider.value = win.wallGap
                                        }
                                    }
                                    property real defaultValue: 18
                                    property real _lastPressMs: 0
                                    property bool _pendingReset: false
                                    onPressedChanged: {
                                        if (pressed) { _pendingReset = win.isDblPress(wallGapSlider); return }
                                        if (_pendingReset) { _pendingReset = false; win.wallGap = defaultValue }
                                        win.wallSave("gap", win.wallGap)      // 드래그 끝난 뒤 1회 저장
                                    }
                                }
                            }

                            Label {
                                text: "Resolution"
                                color: "white"
                            }
                            ComboBox {
                                id: wallResCombo
                                Layout.fillWidth: true
                                model: ["3840 × 2160 (4K · 16:9)", "2560 × 1440 (QHD · 16:9)",
                                        "1920 × 1080 (FHD · 16:9)", "3840 × 2400 (4K · 16:10)",
                                        "2560 × 1600 (16:10)", "1920 × 1200 (16:10)",
                                        "Match screen (" + controller.screenW + " × " + controller.screenH + ")"]
                                currentIndex: win.wallResIndex
                                onActivated: { win.wallResIndex = currentIndex; win.wallSave("resIndex", currentIndex) }
                                Connections {
                                    target: win
                                    function onWallResIndexChanged() { wallResCombo.currentIndex = win.wallResIndex }
                                }
                                Connections {
                                    target: wallResCombo.popup
                                    function onClosed() { viewport.forceActiveFocus() }
                                }
                            }

                            // 16:9·16:10 겸용: 사진은 꽉 채우고 글자만 공통 안전영역 안에.
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                CheckBox {
                                    id: wallDualCheck
                                    checked: win.wallDualAspect
                                    onToggled: {
                                        win.wallDualAspect = checked
                                        win.wallSave("dual", checked ? 1 : 0)
                                    }
                                    Connections {
                                        target: win
                                        function onWallDualAspectChanged() {
                                            wallDualCheck.checked = win.wallDualAspect
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    text: "Keep text safe on 16:9 and 16:10"
                                    color: "white"; font.pixelSize: 12
                                }
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                visible: win.wallLayout === 1
                                text: "Photos still bleed to the edges; only the typography stays inside the area both aspect ratios show, so one file works on either monitor."
                                color: "#888"; font.pixelSize: 11
                            }

                            Button {
                                text: "Export Wallpaper…"
                                Layout.fillWidth: true
                                enabled: win.wallFilled === 3 && !win.wallActive && !win.batchActive
                                         && !controller.exporting && !controller.busy
                                onClicked: {
                                    var u = controller.suggestedWallpaperUrl(
                                        win.wallResW[win.wallResIndex], win.wallResH[win.wallResIndex])
                                    if (u != "") wallpaperSaveDialog.selectedFile = u
                                    wallpaperSaveDialog.open()
                                }
                            }
                            Label {
                                visible: win.wallResult !== ""
                                Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
                                text: win.wallResult
                                color: win.wallResult === "Wallpaper saved" ? "#9fd39f" : "#e08a8a"
                                font.pixelSize: 11
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
                    // Wallpaper 는 개인용 기능 — .env 플래그(controller.wallpaperEnabled, 시작 시 고정)
                    // 가 켜졌을 때만 항목 노출. 릴리즈 빌드는 .env 미포함이라 자동 숨김.
                    model: {
                        var m = [
                            { icon: "edit", tip: "Edit", key: "Ctrl+1" },
                            { icon: "crop", tip: "Crop / Rotate / Geometry", key: "Ctrl+2" },
                            { icon: "mask", tip: "Masking", key: "Ctrl+3" },
                            { icon: "stamp", tip: "Date Stamp", key: "Ctrl+4" }
                        ]
                        if (controller.wallpaperEnabled)
                            m.push({ icon: "wall", tip: "Wallpaper", key: "Ctrl+5" })
                        return m
                    }
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
                                } else if (ic === "stamp") {
                                    // 데이트백 각인 = **7세그 두 자리('24')**. 이 기능이 실제로
                                    // 찍는 글자꼴이고(DSEG), 24px 에서 읽힌다.
                                    // ⚠️예전 시안(프레임+우하단 짧은 획 3개)은 이 크기에서 뭉개져
                                    //   "직관적이지 않다"는 지적을 받았다. 프레임·필름스트립을
                                    //   같이 넣는 안도 24px 에서 전부 뭉갰다 — 숫자만 남길 것.
                                    ctx.lineWidth = 1.6
                                    ctx.lineCap = "round"
                                    // 세그먼트 a,b,c,d,e,f,g 점등표(7세그 표준 배치)
                                    function seg7(sx, sy, sw, sh, on) {
                                        var m = 0.9   // 세그먼트 끝 여유(모서리에서 선이 겹치지 않게)
                                        var L = [[sx+m, sy, sx+sw-m, sy],                       // a 위
                                                 [sx+sw, sy+m, sx+sw, sy+sh/2-m],               // b 우상
                                                 [sx+sw, sy+sh/2+m, sx+sw, sy+sh-m],            // c 우하
                                                 [sx+m, sy+sh, sx+sw-m, sy+sh],                 // d 아래
                                                 [sx, sy+sh/2+m, sx, sy+sh-m],                  // e 좌하
                                                 [sx, sy+m, sx, sy+sh/2-m],                     // f 좌상
                                                 [sx+m, sy+sh/2, sx+sw-m, sy+sh/2]]             // g 중간
                                        for (var k = 0; k < 7; k++) {
                                            if (on.charAt(k) !== "1") continue
                                            ctx.beginPath()
                                            ctx.moveTo(L[k][0], L[k][1]); ctx.lineTo(L[k][2], L[k][3])
                                            ctx.stroke()
                                        }
                                    }
                                    // 어포스트로피 — 실제 각인이 "'YY MM DD" 라 연도 앞에 붙는다.
                                    // (DSEG 에는 이 글자가 없어 스탬프에서는 Qt 폴백으로 그려지지만,
                                    //  아이콘에서는 우리가 직접 그리므로 같은 느낌을 낸다.)
                                    ctx.beginPath()
                                    ctx.moveTo(o + 2.6, o + 7.0); ctx.lineTo(o + 2.6, o + 10.0)
                                    ctx.stroke()
                                    seg7(o + 6.0,  o + 7.5, 5.5, 9, "1101101")   // 2
                                    seg7(o + 15.0, o + 7.5, 5.5, 9, "1011111")   // 6
                                } else if (ic === "wall") {
                                    // 배경화면: 세로 패널 3개(트립틱)
                                    ctx.fillRect(o + 3, o + 4, 5, 16)
                                    ctx.fillRect(o + 9.5, o + 4, 5, 16)
                                    ctx.fillRect(o + 16, o + 4, 5, 16)
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

            // ── 후원 버튼: 셀렉터 바 맨 하단(패널 선택과 무관 → 위쪽 그룹과 떨어뜨림) ──
            Rectangle {
                width: 40; height: 40
                radius: 6
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 8
                anchors.horizontalCenter: parent.horizontalCenter
                color: donateMouse.containsMouse ? "#33373f" : "transparent"
                Label {
                    anchors.centerIn: parent
                    text: "♥"
                    color: donateMouse.containsMouse ? "#E0A226" : "#8a8a8a"
                    font.pixelSize: 24
                }
                MouseArea {
                    id: donateMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: donateDialog.open()
                }
                ToolTip.visible: donateMouse.containsMouse
                ToolTip.delay: 1500
                ToolTip.text: "Support this project — saving for a Mac"
            }
        }
    }

}
