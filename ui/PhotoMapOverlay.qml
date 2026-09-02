// Photo map (`M`) — 이 폴더의 사진이 어디서 찍혔는지 한 화면에서 본다.
//
// Photo tags(`H`) 오버레이와 **같은 성격**이다: 폴더 단위 읽기 전용 둘러보기. 그래서 몰입형
// 풀블리드 + 배경 프로스티드 글래스라는 같은 형태를 쓴다(`Main.qml` 의 `tagCloudOverlay`).
// 좌표를 **붙이는** 일은 Location 패널(`Ctrl+6`)이 계속 단독으로 담당한다 — 여기서는 아무것도
// 쓰지 않는다(셰이더 uniform 0개, 사이드카 무변경).
//
// ⚠️**QtLocation import 가 없다.** 지도는 `FolderMap.qml` 에 가둬 두고 `Loader` 로 켠다 —
//   모듈이 빠진 배포본에서도 최악이 '액자가 빈다'로 끝난다.
// ⚠️`FilmRawstery.spec` 의 `QML` 목록 등록 필수(소스 실행은 되고 배포본만 깨진다).
import QtQuick
import QtQuick.Controls          // ToolTip 첨부 프로퍼티(.Basic 만으론 안 붙는다)
import QtQuick.Controls.Basic as B
import QtQuick.Effects

Rectangle {
    id: root

    // 배경 블러의 소스(= Main.qml 의 mainContent). 없으면 어두운 스크림으로 degrade.
    property Item bgSource: null
    signal closeRequested()
    signal openRequested(string path)

    // 지금 고른 스택(FolderMap 이 넘겨준 클러스터). null 이면 아직 아무것도 안 골랐다.
    property var stack: null

    readonly property var stats: controller.folderMapStats
    // 이 결과가 지금 보는 폴더의 것인가 — 인덱싱 진행 표시와 같은 규율(어긋남 방지).
    readonly property bool ready: !controller.folderMapBusy
                                 && controller.folderMapFolder === controller.currentFolder
    readonly property int pointCount: controller.folderMapPoints.length

    color: "#e6121212"                       // 블러 실패 시 폴백(평소엔 아래 블러+틴트가 덮음)
    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    focus: visible
    onVisibleChanged: {
        if (visible) {
            forceActiveFocus()
            bgSnap.scheduleUpdate()               // 배경 스냅샷은 열 때 1회
            // ★지도 Loader 는 **처음 열 때** 켜고 그 뒤에는 유지한다(여닫을 때마다 타일 재요청 X).
            if (!mapLoader.everActive) mapLoader.everActive = true
        } else {
            // 닫으면 선택을 버린다(다음에 열 때 깨끗하게). 지도 쪽이 진실원이므로 거기서 비운다.
            if (mapLoader.item) mapLoader.item.selected = null
            root.stack = null
        }
    }
    Keys.onEscapePressed: root.closeRequested()

    // 배경 프로스티드 글래스 — 열 때 1회 스냅샷(정지 배경) → 블러 + 어두운 틴트.
    // `live: false` 라 per-frame 캡처가 없다(발열/부하 없음). tagCloudOverlay 와 같은 방식.
    ShaderEffectSource {
        id: bgSnap
        anchors.fill: parent
        sourceItem: root.bgSource
        live: false; hideSource: false; visible: false
    }
    MultiEffect {
        anchors.fill: parent
        source: bgSnap
        visible: root.bgSource !== null
        blurEnabled: true; blur: 0.7; blurMax: 28; autoPaddingEnabled: false
    }
    Rectangle { anchors.fill: parent; color: "#b8101014" }      // 어두운 틴트(대비 확보)

    // ⚠️**'빈 곳 클릭 = 닫기' 를 액자 위에 깔면 안 된다** — 지도 드래그를 먹는다. 그래서 이
    //   MouseArea 는 맨 아래에 두고, 액자·스트립이 그 위에서 자기 입력을 가져간다.
    MouseArea { anchors.fill: parent; onClicked: root.closeRequested() }

    // ---------- 헤더 ----------
    Column {
        id: header
        anchors.left: parent.left; anchors.top: parent.top
        anchors.leftMargin: 44; anchors.topMargin: 34
        anchors.right: parent.right; anchors.rightMargin: 44
        spacing: 6
        B.Label {
            text: "Photo map"
            color: "#f2f2f2"; font.pixelSize: 26; font.bold: true
        }
        B.Label {
            width: parent.width
            textFormat: Text.RichText
            color: "#9a9a9a"; font.pixelSize: 12
            text: root.statsText()
        }
    }

    // 헤더 한 줄. ★좌표가 없는 사진은 **개수만** 말한다 — 붙이는 기능은 Location 패널에 있고
    //   같은 일을 두 곳에 두지 않는다. 그래도 개수를 감추면 "왜 내 사진이 안 보이지"가 된다.
    function statsText() {
        if (controller.folderMapBusy)
            return "Reading locations from this folder..."
        if (!root.ready)
            return "Reading locations..."
        var s = root.stats
        if (!s || s.photos === undefined) return ""
        var t = s.photos + " photos  ·  " + s.located + " located  ·  "
                + s.places + (s.places === 1 ? " place" : " places")
        var missing = s.photos - s.located
        if (missing > 0)
            t += "  ·  <font color='#7a7a7a'>" + missing + " without a location</font>"
        return t
    }

    // ---------- 지도 액자 ----------
    Rectangle {
        id: frame
        anchors.left: parent.left; anchors.right: parent.right
        anchors.leftMargin: 44; anchors.rightMargin: 44
        anchors.top: header.bottom; anchors.topMargin: 18
        anchors.bottom: strip.top; anchors.bottomMargin: 16
        color: "#141414"
        border.color: "#3d3d40"; border.width: 1
        radius: 8
        clip: true

        Loader {
            id: mapLoader
            anchors.fill: parent
            anchors.margins: 1
            // ★오버레이를 **처음 열 때** 켜고, 켜진 뒤에는 유지한다(여닫을 때마다 타일 재요청 X).
            property bool everActive: false
            active: everActive && root.pointCount > 0
            source: "FolderMap.qml"
            onLoaded: {
                item.points = Qt.binding(function () { return controller.folderMapPoints })
                // ★`stackPicked`(탭) 가 아니라 **`selected` 를 따라간다.** 지도는 줌에 따라
                //   클러스터를 다시 묶으면서 선택을 옮기거나(mergeForZoom) 비우는데
                //   (onPointsChanged), 탭 신호만 들으면 그때 스트립이 마커와 어긋난다 —
                //   합쳐진 마커 12장을 고른 뒤 확대해 셋으로 갈라져도 머리글은 계속 "12 photos
                //   here" 이고 스트립도 12장을 보여 준다.
                item.selectedChanged.connect(function () {
                    root.stack = mapLoader.item ? mapLoader.item.selected : null
                })
                root.stack = item.selected
            }
        }

        // 지도 저작권 — ⚠️**표기는 의무다**(User-Agent 와 같은 급). Qt 가 자체 표기를 그리지만
        //   액자 안에서 잘릴 수 있어 여기에 한 줄 못 박아 둔다.
        B.Label {
            anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: 6
            color: "#8a8a8a"; font.pixelSize: 10
            text: "Map data © OpenStreetMap contributors"
        }

        // 빈 상태 / 스캔 중 — 액자를 비워 두지 않는다.
        B.Label {
            anchors.centerIn: parent
            width: parent.width - 80
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            color: "#8a8a8a"; font.pixelSize: 13
            visible: !mapLoader.active
            text: controller.folderMapBusy
                  ? "Reading this folder..."
                  : (mapLoader.status === Loader.Error
                     ? "Map component unavailable in this build."
                     : "No photo in this folder has a location yet.\n"
                       + "Open the Location panel (Ctrl+6) to put one on the map.")
        }
    }

    // ---------- 선택한 스택 ----------
    // 클릭 = 탐색기에서 선택 / 더블클릭 = 열기. **컨택트 시트·탐색기와 같은 규약**이라
    // 새로 배울 것이 없다.
    Rectangle {
        id: strip
        anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 44; anchors.rightMargin: 44; anchors.bottomMargin: 30
        height: root.stack ? 150 : 0
        visible: height > 0
        color: "transparent"

        // 입력이 아래 '닫기' MouseArea 로 새지 않게(스트립 여백을 눌러도 안 닫힌다).
        MouseArea { anchors.fill: parent }

        Column {
            anchors.fill: parent
            spacing: 8

            B.Label {
                width: parent.width
                elide: Text.ElideRight
                color: "#c8c8c8"; font.pixelSize: 12
                text: root.stackText()
            }

            ListView {
                id: stripView
                width: parent.width
                height: 112
                orientation: ListView.Horizontal
                spacing: 8
                clip: true
                // ⚠️`model` 에 QVariantList 를 그대로 물린다(파이썬 리스트 → QML 에서는
                //   `Array.isArray()` 가 false 지만 model 로는 잘 쓰인다).
                model: root.stack ? root.stack.paths : []
                boundsBehavior: Flickable.StopAtBounds
                B.ScrollBar.horizontal: B.ScrollBar { policy: B.ScrollBar.AsNeeded }

                delegate: Rectangle {
                    required property string modelData
                    width: 112; height: 106
                    radius: 5
                    color: thumbHover.hovered ? "#2b2f38" : "#1c1f25"
                    border.width: 1
                    border.color: controller.imagePath === modelData ? "#8ab4f8" : "#3d3d40"

                    Image {
                        anchors.fill: parent
                        anchors.margins: 4
                        // 수백 장을 그리는 곳이라 캡을 거친다(CLAUDE.md 썸네일 항).
                        sourceSize.width: win.thumbPx(96)
                        source: "image://thumb/" + encodeURIComponent(modelData)
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        cache: true
                    }
                    HoverHandler { id: thumbHover }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        // 클릭 = 선택만(탐색기 하이라이트) — 오버레이는 열린 채로 둔다.
                        onClicked: win.selectInExplorer(modelData, false)
                        // 더블클릭 = 이 사진을 연다 → 오버레이는 닫는다(볼 것이 바뀌었으니).
                        onDoubleClicked: root.openRequested(modelData)
                    }
                    ToolTip.visible: thumbHover.hovered
                    ToolTip.delay: 500
                    ToolTip.text: modelData.split(/[\\/]/).pop()
                }
            }
        }
    }

    // 스트립 머리글 — 합쳐진 마커에는 좌표를 쓰지 않는다(거짓말이 된다).
    function stackText() {
        var s = root.stack
        if (!s) return ""
        var t = s.count + (s.count === 1 ? " photo here" : " photos here")
        if (s.places > 1)
            t += "  ·  " + s.places + " nearby places  ·  zoom in to separate them"
        else if (!isNaN(s.exactLat))
            t += "  ·  " + s.exactLat.toFixed(6) + ", " + s.exactLon.toFixed(6)
        t += "  ·  click to select, double-click to open"
        return t
    }

    // 닫기 — 우상단. `Esc`/`M`/빈 곳 클릭과 같은 동작(발견 가능하게 눈에 보이는 것도 둔다).
    Rectangle {
        anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 26
        width: 30; height: 30; radius: 15
        color: closeHover.hovered ? "#3a3a3d" : "#00000000"
        border.color: "#5a5a5f"; border.width: 1
        B.Label {
            anchors.centerIn: parent
            text: "✕"; color: "#c8c8c8"; font.pixelSize: 14
        }
        HoverHandler { id: closeHover }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.closeRequested()
        }
    }
}
