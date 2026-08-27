// RAW Peek — 디모자이크 **이전**(pre-demosaic) 센서 데이터 뷰 (`R` 로 열고 Esc 로 닫기).
//
// `PreviewWindow.qml` 과 같은 **인앱 전체화면 오버레이**다(별도 OS 창이 아니라 메인 창
// contentItem 을 덮는 Item). 씬그래프를 재사용하므로 오픈이 즉시고 창 생성·포커스 처리가 없다.
//
// ★그림은 전부 파이썬(`raw_peek.py`)이 QImage 로 그려 provider 로 넘긴다 — 셰이더를 쓰지 않는다.
//   `adjust.frag` 는 D3D11 샘플러 16/16 을 이미 다 써서 샘플러를 늘리면 파이프라인 생성에서
//   죽는다(qsb 컴파일은 통과한다). 그리고 이 뷰는 룩/export 와 접점이 없어야 한다.
// ★크롭·확대는 파이썬이 요청된 center/zoom 으로 잘라서 만든다 — 26MP 를 32× 로 올린 텍스처는
//   존재할 수 없으므로 QML 쪽에서 스케일하지 않는다.
import QtQuick
import QtQuick.Controls.Basic as B

Item {
    id: peekWin
    anchors.fill: parent
    visible: false
    z: 1000

    // 표시 모드 — raw_peek.MODE_* 와 같은 순서/값이어야 한다.
    readonly property var modeNames: ["Gray", "CFA", "Planes", "Demosaic"]
    property int mode: 1                  // 기본 CFA (이 기능의 핵심 컷)
    property int zoom: 8
    property real cx: 0.5                 // visible 안의 정규화 팬 중심(0..1)
    property real cy: 0.5
    property bool infoOpen: true

    readonly property bool isMosaic: mode <= 2   // Gray/CFA/Planes 만 팬·줌이 의미 있다

    // 데이터 준비 완료(false→true) 전이를 감지해 첫 그림을 요청하기 위한 래치.
    property bool _wasReady: false

    function open() {
        mode = 1
        zoom = 8
        cx = 0.5
        cy = 0.5
        _wasReady = false
        visible = true
        controller.rawPeekOpen()
        keyScope.forceActiveFocus()
    }
    function close() {
        visible = false
        _wasReady = false
        controller.rawPeekClose()
    }

    // 모드·뷰포트에서 실제로 의미 있는 줌 범위로 클램프한다.
    // ★안 하면 휠이 무동작하는 칸이 생기고(크롭 하한 때문에 16x·32x 가 같은 결과),
    //   Demosaic 은 내부에서 zoom 을 2 로 올려 잡아 **zoom 1 과 2 가 같은 그림**인데
    //   패닝 가능 여부만 달라 "2x 인데 패닝 안 되는 상태" 가 하나 더 보였다(사용자 보고).
    function setZoom(z) {
        if (!controller.rawPeekOpened) { zoom = z; return }
        var vw = Math.max(64, Math.round(view.width))
        var vh = Math.max(64, Math.round(view.height))
        var lo = controller.rawPeekZoomMin(mode, vw, vh)
        var hi = controller.rawPeekZoomMax(mode, vw, vh)
        zoom = Math.max(lo, Math.min(hi, z))
    }

    // 파이썬에 그림을 요청한다. 뷰포트 크기를 함께 넘겨 화면에 들어갈 픽셀만 만들게 한다.
    function refresh() {
        if (!visible || !controller.rawPeekOpened) return
        controller.rawPeekView(mode, cx, cy, zoom,
                               Math.max(64, Math.round(view.width)),
                               Math.max(64, Math.round(view.height)))
    }

    onModeChanged: {
        var before = zoom
        setZoom(zoom)
        if (zoom === before) refresh()      // 값이 바뀌면 onZoomChanged 가 대신 부른다
    }
    onZoomChanged: refresh()
    // 로드가 끝나는 순간(rawPeekOpened false→true) 첫 그림을 요청한다.
    // ⚠️URL 문자열로 "아직 안 그렸음"을 판정하면 안 된다 — 같은 사진에서 닫고 다시 열면 URL 이
    //   초기값이 아니라서 첫 렌더가 안 걸리고 화면이 빈다(실제로 그렇게 짰다가 고쳤다).
    Connections {
        target: controller
        function onRawPeekChanged() {
            if (!peekWin.visible) return
            var ready = controller.rawPeekOpened
            if (ready && !peekWin._wasReady) {
                // 데이터가 준비된 시점에만 기본 팬 위치를 알 수 있다(축소본에서 고른다).
                peekWin.cx = controller.rawPeekDefaultCx
                peekWin.cy = controller.rawPeekDefaultCy
                peekWin.refresh()
            }
            peekWin._wasReady = ready
        }
    }

    Item {
        id: keyScope
        anchors.fill: parent
        focus: peekWin.visible
        // ★모드 전환은 **이름 있는 핸들러**로 적는다 — `shortcuts.py` 의 검사기가
        //   `Keys.on<X>Pressed` 를 토큰으로 파싱하므로, 이렇게 두면 표와 자동 대조된다.
        //   (+/− 는 Qt 에 이름 있는 핸들러가 없어 아래 onPressed 로 남는다)
        Keys.onEscapePressed: peekWin.close()
        Keys.onDigit1Pressed: peekWin.mode = 0
        Keys.onDigit2Pressed: peekWin.mode = 1
        Keys.onDigit3Pressed: peekWin.mode = 2
        Keys.onDigit4Pressed: peekWin.mode = 3
        Keys.onPressed: function (e) {
            if (e.key === Qt.Key_Plus || e.key === Qt.Key_Equal) {
                peekWin.setZoom(peekWin.zoom * 2); e.accepted = true
            } else if (e.key === Qt.Key_Minus) {
                peekWin.setZoom(peekWin.zoom / 2); e.accepted = true
            } else if (e.key === Qt.Key_R) {
                peekWin.close(); e.accepted = true
            }
        }

        // 배경 — 뒤(편집 화면)로 클릭이 새지 않게 흡수
        Rectangle {
            anchors.fill: parent
            color: "#141416"
            MouseArea { anchors.fill: parent }
        }

        // ---------------- 상단 바 ----------------
        Rectangle {
            id: topBar
            anchors { top: parent.top; left: parent.left; right: parent.right }
            height: 44
            color: "#232326"

            Row {
                anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
                spacing: 6
                Repeater {
                    model: peekWin.modeNames
                    Rectangle {
                        width: mLabel.implicitWidth + 22; height: 28; radius: 4
                        color: peekWin.mode === index ? "#3d6fb5"
                                                      : (mMa.containsMouse ? "#3a3a3e" : "#2c2c30")
                        border.color: peekWin.mode === index ? "#5c8fd6" : "#3f3f44"
                        Text {
                            id: mLabel
                            anchors.centerIn: parent
                            text: modelData
                            color: "#e8e8e8"; font.pixelSize: 12
                        }
                        MouseArea {
                            id: mMa
                            anchors.fill: parent; hoverEnabled: true
                            onClicked: peekWin.mode = index
                        }
                    }
                }
            }

            Row {
                anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                spacing: 8

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    // 요청 zoom 이 아니라 그려진 실제 배율 — Demosaic 은 크롭 하한 때문에 다르다.
                    text: (peekWin.isMosaic && peekWin.zoom <= 1) ? "whole frame"
                          : (controller.rawPeekScale > 0
                             ? Math.round(controller.rawPeekScale) + "x" : peekWin.zoom + "x")
                    color: "#9a9a9a"; font.pixelSize: 12
                }
                component BarButton: Rectangle {
                    id: bb
                    property string label: ""
                    signal clicked()
                    width: 28; height: 28; radius: 4
                    color: bbMa.containsMouse ? "#3a3a3e" : "#2c2c30"
                    border.color: "#3f3f44"
                    Text {
                        anchors.centerIn: parent
                        text: bb.label; color: "#e8e8e8"; font.pixelSize: 14
                    }
                    MouseArea {
                        id: bbMa
                        anchors.fill: parent; hoverEnabled: true
                        onClicked: bb.clicked()
                    }
                }
                BarButton {
                    label: "−"
                    onClicked: peekWin.setZoom(peekWin.zoom / 2)
                }
                BarButton {
                    label: "+"
                    onClicked: peekWin.setZoom(peekWin.zoom * 2)
                }
                BarButton {
                    label: "i"
                    onClicked: peekWin.infoOpen = !peekWin.infoOpen
                }
                BarButton { label: "✕"; onClicked: peekWin.close() }
            }
        }

        // ---------------- 캡션 밴드 (이미지에 굽지 않는다) ----------------
        // ★파이썬이 캡션을 문자열로 돌려주고 여기서 그린다. 굽던 시절엔 이미지 높이가
        //   "크롭 + 라벨밴드" 라 항상 뷰포트를 넘어 **상단 텍스트가 잘렸다**(사용자 보고).
        //   높이를 **고정**해 두는 것이 핵심 — 내용에 따라 높이가 변하면 view.height 가 바뀌고
        //   그게 재렌더를 유발해 진동한다.
        Rectangle {
            id: caption
            anchors { top: topBar.bottom; left: parent.left; right: parent.right }
            height: 40
            color: "#1b1b1e"
            Text {
                anchors { fill: parent; leftMargin: 12; rightMargin: 12; topMargin: 3 }
                text: controller.rawPeekCaption
                color: "#d8d8d8"
                font.family: "Consolas"
                font.pixelSize: 12
                lineHeight: 1.15
                maximumLineCount: 2
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
            }
        }

        // ---------------- 좌: 모자이크 뷰 ----------------
        Item {
            id: view
            anchors {
                top: caption.bottom; bottom: parent.bottom
                left: parent.left; right: peekWin.infoOpen ? infoPane.left : parent.right
            }
            // 안전망: 파이썬이 뷰포트보다 큰 그림을 돌려주더라도 정보 패널 위로 넘치지 않게.
            clip: true

            // 뷰포트 크기가 곧 요청 픽셀 수다 — 창 리사이즈나 정보 패널 토글이면 다시 그려야 한다.
            // 리사이즈는 매 프레임 발화하므로 디바운스한다(무거운 모드는 재디코드까지 간다).
            onWidthChanged: resizeDebounce.restart()
            onHeightChanged: resizeDebounce.restart()
            Timer {
                id: resizeDebounce
                interval: 120
                onTriggered: peekWin.refresh()
            }

            Image {
                id: peekImg
                objectName: "rawPeekImage"      // 헤드리스 검증에서 찾기 위한 이름
                // ★들어가면 가운데, 넘치면 좌상단 정렬 — centerIn 으로 두면 넘칠 때 위아래가
                //   똑같이 잘려 **상단 라벨이 사라진다**(사용자 보고).
                x: Math.max(0, (view.width - width) / 2)
                y: Math.max(0, (view.height - height) / 2)
                // ⚠️provider 가 그림을 통째로 교체하므로 cache: false 필수(?v= 만으로는 Qt 가
                //   옛 텍스처를 재사용할 수 있다 — FaceThumbProvider 주석과 같은 함정).
                cache: false
                // ⚠️asynchronous: true 면 소스가 바뀔 때마다 Loading 상태로 가며 이전 픽스맵을
                //   버려 **빈 프레임이 끼어 드래그가 깜빡인다**(사용자 보고). provider 는 이미
                //   메모리에 있는 QImage 를 그대로 돌려주므로(수 MB memcpy) 동기가 맞다.
                asynchronous: false
                fillMode: Image.Pad
                source: controller.rawPeekOpened ? controller.rawPeekUrl : ""
            }

            // 드래그 팬 — 화면 이동량을 센서 픽셀로 환산해 정규화 중심을 옮긴다.
            MouseArea {
                id: pan
                anchors.fill: parent
                // ★★`enabled` 로 막지 않는다. 예전엔 `enabled: isMosaic && zoom > 1` 이었는데,
                //   줌을 1까지 내리면 이 영역이 비활성화돼 **휠 이벤트가 아예 안 와서 다시
                //   확대할 수 없었다**(사용자 보고: "scale down 은 되는데 scale up 안 됨").
                //   드래그만 핸들러 안에서 게이팅한다.
                // ★모드나 zoom 이 아니라 **지금 그려진 것이 실제 크롭인가**로 판정한다.
                //   `zoom > 1` 로 보던 시절, Demosaic 은 내부에서 zoom 을 2 로 올려 잡으므로
                //   zoom 1 에서도 크롭을 그리는데 패닝만 막혀 "2x 인데 안 움직이는" 상태가 있었다.
                readonly property bool canPan: {
                    var r = controller.rawPeekRect
                    return r.length === 4
                           && (r[2] < controller.rawPeekVisW || r[3] < controller.rawPeekVisH)
                }
                cursorShape: canPan ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                                    : Qt.ArrowCursor
                property real px: 0
                property real py: 0
                onPressed: function (e) { px = e.x; py = e.y }
                onPositionChanged: function (e) {
                    if (!pressed || !canPan) return
                    var visW = controller.rawPeekVisW
                    var visH = controller.rawPeekVisH
                    if (visW <= 0 || visH <= 0) return
                    // ⚠️zoom 이 아니라 **실제로 그려진 배율**로 환산한다 — Demosaic 은 패널이
                    //   화면의 1/n 이고 고배율에서 캡도 걸려 zoom 과 다르다.
                    var sc = Math.max(0.0001, controller.rawPeekScale)
                    peekWin.cx = Math.max(0, Math.min(1, peekWin.cx
                                  - (e.x - px) / sc / visW))
                    peekWin.cy = Math.max(0, Math.min(1, peekWin.cy
                                  - (e.y - py) / sc / visH))
                    px = e.x; py = e.y
                    peekWin.refresh()
                }
                onWheel: function (e) {
                    if (e.angleDelta.y > 0) peekWin.setZoom(peekWin.zoom * 2)
                    else if (e.angleDelta.y < 0) peekWin.setZoom(peekWin.zoom / 2)
                }
            }

            // ---------------- 미니맵 (확대 중 '지금 보는 곳') ----------------
            // 미니맵 이미지는 사진당 1회만 만들면 되고(팬/줌과 무관), 표시 사각형은
            // controller.rawPeekRect(= raw_peek 이 실제로 자른 센서 픽셀 x,y,w,h)에서 나온다.
            Rectangle {
                id: minimap
                readonly property var r: controller.rawPeekRect
                readonly property int visW: controller.rawPeekVisW
                readonly property int visH: controller.rawPeekVisH
                // 전체를 보고 있으면(>=95%) 표시할 게 없으므로 숨긴다.
                visible: controller.rawPeekOpened && r.length === 4 && visW > 0 && visH > 0
                         && (r[2] * r[3]) < (visW * visH * 0.95)
                anchors { right: parent.right; bottom: parent.bottom; margins: 12 }
                width: mini.paintedWidth + 2
                height: mini.paintedHeight + 2
                color: "#000000B0"
                border.color: "#5a5a5e"
                border.width: 1

                Image {
                    id: mini
                    x: 1; y: 1
                    cache: false
                    asynchronous: false
                    source: controller.rawPeekOpened ? controller.rawPeekMiniUrl : ""
                }

                // 현재 보고 있는 영역.
                // ⚠️정직하게 비율대로만 그리면 8~32× 에서 **3~4px 점**이 된다(크롭이 전체의 2%).
                //   그래서 ①최소 크기를 주고 ②십자선을 함께 그린다 — 위치는 십자선이,
                //   실제 크기는 박스가 알려준다.
                Item {
                    id: marker
                    objectName: "rawPeekMarker"     // 헤드리스 검증에서 찾기 위한 이름
                    visible: mini.paintedWidth > 0 && ok
                    // ⚠️rect 가 빈 배열일 때 NaN 이 바인딩으로 새지 않게 가드한다.
                    readonly property bool ok: minimap.r.length === 4
                                               && minimap.visW > 0 && minimap.visH > 0
                    readonly property real cxp: ok ? 1 + (minimap.r[0] + minimap.r[2] / 2)
                                                     / minimap.visW * mini.paintedWidth : 0
                    readonly property real cyp: ok ? 1 + (minimap.r[1] + minimap.r[3] / 2)
                                                     / minimap.visH * mini.paintedHeight : 0
                    readonly property real bw: ok ? Math.max(9, minimap.r[2] / minimap.visW
                                                             * mini.paintedWidth) : 0
                    readonly property real bh: ok ? Math.max(9, minimap.r[3] / minimap.visH
                                                             * mini.paintedHeight) : 0
                    anchors.fill: parent

                    // 십자선 — 미니맵 전체를 가로/세로로 가로지른다
                    Rectangle {
                        x: 1; y: marker.cyp - 0.5
                        width: mini.paintedWidth; height: 1
                        color: "#8ab4f870"
                    }
                    Rectangle {
                        x: marker.cxp - 0.5; y: 1
                        width: 1; height: mini.paintedHeight
                        color: "#8ab4f870"
                    }
                    // 실제 크롭 영역
                    Rectangle {
                        x: marker.cxp - marker.bw / 2
                        y: marker.cyp - marker.bh / 2
                        width: marker.bw; height: marker.bh
                        color: "#8ab4f833"
                        border.color: "#c8dcff"
                        border.width: 1
                    }
                }

                // 클릭·드래그로 그 위치로 이동
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    function jump(mx, my) {
                        if (mini.paintedWidth <= 0 || mini.paintedHeight <= 0) return
                        peekWin.cx = Math.max(0, Math.min(1, (mx - 1) / mini.paintedWidth))
                        peekWin.cy = Math.max(0, Math.min(1, (my - 1) / mini.paintedHeight))
                        peekWin.refresh()
                    }
                    onPressed: function (e) { jump(e.x, e.y) }
                    onPositionChanged: function (e) { if (pressed) jump(e.x, e.y) }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: controller.rawPeekBusy && !controller.rawPeekOpened
                text: "Reading sensor data…"
                color: "#8a8a8a"; font.pixelSize: 15
            }
            // 무거운 렌더(전체보기 / 디모자이크 재디코드) 중 표시 — 그림은 그대로 두고 알린다.
            Rectangle {
                anchors { top: parent.top; right: parent.right; margins: 10 }
                visible: controller.rawPeekBusy && controller.rawPeekOpened
                width: bLabel.implicitWidth + 18; height: 24; radius: 12
                color: "#000000AA"
                Text {
                    id: bLabel
                    anchors.centerIn: parent
                    // 후보 디모자이크는 종당 1.1~3.6s 다 — 어느 알고리즘을 받고 있는지 알린다.
                    text: controller.rawPeekStatus !== "" ? controller.rawPeekStatus
                                                          : "rendering…"
                    color: "#dddddd"; font.pixelSize: 11
                }
            }
        }

        // ---------------- 우: 패턴 / 히스토그램 / 메타 ----------------
        Rectangle {
            id: infoPane
            anchors { top: topBar.bottom; bottom: parent.bottom; right: parent.right }
            // 고정 500px 은 기본 창(1280)에서 뷰를 780px 로 만든다 → 창 폭에 비례시키고 상한만 둔다.
            width: peekWin.infoOpen ? Math.min(500, Math.round(peekWin.width * 0.34)) : 0
            visible: peekWin.infoOpen
            color: "#1b1b1e"

            B.ScrollView {
                anchors.fill: parent
                anchors.margins: 12
                clip: true
                contentWidth: availableWidth

                Column {
                    width: infoPane.width - 24
                    spacing: 14

                    Image {
                        cache: false
                        source: controller.rawPeekOpened ? controller.rawPeekPatternUrl : ""
                        fillMode: Image.PreserveAspectFit
                        width: Math.min(implicitWidth, parent.width)
                    }
                    Image {
                        cache: false
                        source: controller.rawPeekOpened ? controller.rawPeekHistUrl : ""
                        fillMode: Image.PreserveAspectFit
                        width: Math.min(implicitWidth, parent.width)
                    }
                    Text {
                        width: parent.width
                        text: controller.rawPeekInfo
                        color: "#c8c8c8"
                        font.family: "Consolas"
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    Text {
                        width: parent.width
                        color: "#7a7a7a"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                        text: "1..4 = mode   +/− or wheel = zoom   drag = pan   "
                              + "click the minimap to jump   Esc or R = close"
                    }
                }
            }
        }
    }
}
