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
    readonly property var modeNames: ["Gray", "CFA", "Planes", "Demosaic", "Develop"]

    // ---- Develop(현상 과정 재생) ----
    // ★렌더는 `ui/Main.qml` 의 `pipeAnim`(같은 adjust.frag)이 하고, 값은 `develop_anim.py` 가
    //   계산한다. 이 탭은 그 결과를 보여주고 타임라인을 조작할 뿐이다.
    readonly property bool isDevelop: mode === 4
    property real devT: 0.0                  // 0..1 타임라인 위치
    property bool devPlaying: false
    property real devSeconds: 12.0           // 전체 재생 시간
    property real devMosaicOpacity: 0.0      // CFA 모자이크 그림 불투명도(교차 페이드)
    // ★표시 게인 — 센서값 그림의 밝기를 올릴지. 캡션에 `display gain x5.2` 로 찍히는 그 값이다
    //   (끄면 `display gain off (as recorded)`). 켜면 보이고, 끄면 **센서가 적어 둔 밝기 그대로**다.
    //   Gray/CFA/Planes/Demosaic 네 탭과 **Develop 탭의 머리 프레임**이 같이 따른다 — 머리는
    //   원래 항상 '적힌 그대로'였고, 이제 이 체크박스가 그것까지 정한다.
    property bool gainOn: true

    property real devGrayOpacity: 0.0        // Gray(센서 밝기) 그림 불투명도 — CFA 위에 얹힌다
    property real devStampOpacity: 0.0       // 날짜 스탬프 페이드인 (Date stamp 단계)
    // ★그림이 **움직이는 중**인가(재생 또는 스크럽 드래그). `pipe` 가 슬라이더 드래그 중에
    //   그레인 원판을 끄는 것과 같은 용도다 — Main.qml 의 `pipeAnim.grainShape` 가 읽는다.
    readonly property bool devMoving: devPlaying || devScrubMa.pressed
    // "4 / 8" — 활성 단계 중 지금이 몇 번째인가. 스킵된 단계는 세지 않는다(타임라인 눈금과 같다).
    readonly property string devStepText: {
        if (!isDevelop) return ""
        var ms = controller.developMarks || []
        var n = 0, cur = 0
        for (var i = 0; i < ms.length; i++) {
            if (!ms[i].active) continue
            n += 1
            if (ms[i].label === devLabel) cur = n
        }
        return n > 0 && cur > 0 ? (cur + " / " + n) : ""
    }
    property string devLabel: ""
    property string devNote: ""
    // ⚠️pipeAnim 아이템 참조는 **프로퍼티에 담아 둔다**. 바인딩에서 함수를 부르면 재평가
    //   시점이 불안정해 소스가 null 로 남는다.
    property var devSrcItem: null
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
        devEnd()
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

    // ★**연속 제스처(드래그·미니맵 끌기)는 `refreshSoon()` 을 쓴다** — 마우스 이동마다
    //   `refresh()` 를 부르면 이벤트 1건마다 동기 렌더 + 텍스처 업로드가 GUI 스레드에서 돌아
    //   빠른 드래그에서 프레임을 흘린다(8배 줌 CFA 실측 이벤트당 2.9ms + 업로드, 이동은
    //   초당 60~120건). 한 이벤트 루프 턴에 **1회**로 묶으면 밀린 요청이 쌓이지 않는다.
    //   ⚠️모드·줌 전환처럼 **단발** 이벤트는 그대로 `refresh()` — 지연 없이 즉시 그린다.
    property bool _refreshQueued: false
    function refreshSoon() {
        if (peekWin._refreshQueued)
            return
        peekWin._refreshQueued = true
        Qt.callLater(peekWin._runQueuedRefresh)
    }
    function _runQueuedRefresh() {
        peekWin._refreshQueued = false
        peekWin.refresh()
    }

    // 파이썬에 그림을 요청한다. 뷰포트 크기를 함께 넘겨 화면에 들어갈 픽셀만 만들게 한다.
    function refresh() {
        if (!visible || !controller.rawPeekOpened) return
        // Develop 은 셰이더 렌더라 provider 모자이크 경로를 타지 않는다(mode 4 는 그쪽 모드가
        // 아니다 — 넘기면 raw_peek.render 의 디스패치에 없는 값이 들어간다).
        if (mode === 4) return
        controller.rawPeekView(mode, cx, cy, zoom,
                               Math.max(64, Math.round(view.width)),
                               Math.max(64, Math.round(view.height)), gainOn)
    }

    onModeChanged: {
        // Develop 은 셰이더 렌더(pipeAnim)를 쓰므로 provider 렌더 경로를 타지 않는다.
        // ★⚠️핸들러 안에서 파생 프로퍼티(`isDevelop`)를 읽으면 **갱신 전 값**이 온다
        //   (CLAUDE.md 의 그 함정). 실제로 그렇게 짰다가 **탭을 나갈 때 애니메이션이 시작**됐다.
        //   → 원천 값(`mode`)으로 직접 판정한다.
        if (mode === 4) { devBegin(); return }
        devEnd()
        var before = zoom
        setZoom(zoom)
        if (zoom === before) refresh()      // 값이 바뀌면 onZoomChanged 가 대신 부른다
    }

    // ---- Develop 시작/종료 ----
    function devBegin() {
        if (!controller.rawPeekOpened) return
        controller.developBegin(win.morphSnapshot())   // 최종 값 스냅샷(읽기만 한다)
        // ★여기서는 그림을 요청하지 않는다. `devFrame` 은 `devSrcItem` 이 정해진 **다음**
        //   턴에 크기가 잡히므로, 지금 요청하면 뷰 크기로 한 번 그리고 100ms 뒤 올바른 크기로
        //   또 그린다 — 전체 프레임 패스를 두 번 버리는 셈이다(검토에서 지적).
        //   `devFrame` 의 onWidthChanged/onHeightChanged 가 devReq 로 한 번만 요청한다.
        win.morphOn = true                             // Loader 가 pipeAnim 을 만든다
        // Loader 가 아이템을 만든 **다음** 턴에 참조를 잡는다(같은 턴엔 아직 null).
        Qt.callLater(function () {
            peekWin.devSrcItem = peekWin.pipeAnimItem()
            peekWin.devT = 0.0
            peekWin.devApply()
            peekWin.devPlaying = true
        })
    }
    function devEnd() {
        devPlaying = false
        devSrcItem = null
        if (win.morphOn) {
            win.morphOn = false
            controller.developEnd()
        }
    }
    // 현재 t 의 uniform 값을 pipeAnim 에 넣는다.
    function devApply() {
        var r = controller.developValues(devT)
        if (!r || !r.uniforms) return
        // ★표시 상태를 **먼저** 세운다 — uniform 대입에서 예외가 나도 캡션·타임라인은 살아 있게.
        //   (예전에 applyValues 가 중간에 던져 캡션이 통째로 빈 채였다)
        devMosaicOpacity = r.mosaic
        devGrayOpacity = r.gray
        devStampOpacity = r.stamp
        devLabel = r.label
        devNote = r.note
        // ⚠️매 16ms 프레임이다 — 여기서 트리를 다시 훑으면 안 된다(`pipeAnimItem()` 은
        //   11k 줄 UI 전체를 재귀 탐색한다). `devBegin` 이 잡아 둔 참조를 쓴다.
        if (peekWin.devSrcItem) peekWin.devSrcItem.applyValues(r.uniforms)
    }
    // pipeAnim 은 Main.qml 의 Loader 안에 있다 — objectName 으로 찾는다.
    function pipeAnimItem() {
        return win.contentItem ? _findByName(win.contentItem, "pipeAnim") : null
    }
    function _findByName(node, nm) {
        if (!node) return null
        if (node.objectName === nm) return node
        for (var i = 0; i < node.children.length; i++) {
            var r = _findByName(node.children[i], nm)
            if (r) return r
        }
        return null
    }

    // 이전/다음 **활성 단계**의 끝으로 점프(단계별로 비교하기 위한 것).
    // ⚠️단계 경계(t1)에 정확히 착지하면 `values()` 가 **다음 단계**를 현재로 고른다
    //   (`t >= t0` 판정이고 t1_i == t0_{i+1}) — 캡션과 n/N 이 한 칸씩 밀렸다(검토에서 잡힘).
    //   그래서 경계 **바로 앞**에 세운다. 0.001 은 단계 길이의 1% 남짓이라(12단계면 8.3%)
    //   smoothstep 값이 0.9996 — 적용은 사실상 끝난 상태다.
    readonly property real devEdgeEps: 0.001
    function devStep(dir) {
        devPlaying = false
        var ms = controller.developMarks
        var ts = []
        for (var i = 0; i < ms.length; i++) if (ms[i].active) ts.push(ms[i].t1)
        if (ts.length === 0) return
        if (dir > 0) {
            for (var a = 0; a < ts.length; a++)
                if (ts[a] - devEdgeEps > devT + 1e-4) { devT = ts[a] - devEdgeEps; return }
            devT = 1.0
        } else {
            for (var b = ts.length - 1; b >= 0; b--)
                if (ts[b] - devEdgeEps < devT - 1e-4) { devT = ts[b] - devEdgeEps; return }
            devT = 0.0
        }
    }

    onDevTChanged: if (isDevelop) devApply()

    // 재생 — 프레임 동기 타이머. 값 대입만이라 가볍다.
    Timer {
        id: devTimer
        interval: 16
        repeat: true
        running: peekWin.visible && peekWin.isDevelop && peekWin.devPlaying
        onTriggered: {
            var step = (interval / 1000.0) / Math.max(0.5, peekWin.devSeconds)
            var nt = peekWin.devT + step
            if (nt >= 1.0) { peekWin.devT = 1.0; peekWin.devPlaying = false }
            else peekWin.devT = nt
        }
    }
    // ⚠️`refresh()` 는 Develop 에서 곧바로 빠져나간다(provider 모자이크 경로가 아니다).
    //   머리 그림은 `devReq` 로 다시 요청해야 한다.
    onGainOnChanged: {
        if (isDevelop) devReq.restart()
        else refresh()
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
                // ★"reading sensor data" 가 끝나기 **전에** Develop 탭으로 옮기면 `devBegin` 이
                //   `rawPeekOpened` 가 false 라 그냥 빠져나가고 다시 시도하지 않아 애니메이션이
                //   아예 안 돌았다(사용자 보고). 준비되는 이 순간에 다시 시작한다.
                if (peekWin.mode === 4 && !win.morphOn) peekWin.devBegin()
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
        Keys.onDigit5Pressed: peekWin.mode = 4
        Keys.onSpacePressed: if (peekWin.isDevelop) {
            if (peekWin.devT >= 1.0) peekWin.devT = 0.0
            peekWin.devPlaying = !peekWin.devPlaying
        }
        Keys.onLeftPressed: if (peekWin.isDevelop) peekWin.devStep(-1)
        Keys.onRightPressed: if (peekWin.isDevelop) peekWin.devStep(1)
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
            // ⚠️`hoverEnabled: true` 가 있어야 **hover 도** 흡수된다. 없으면 오버레이 아래
            //   패널의 HoverHandler 가 계속 반응해 **ToolTip 이 오버레이 위로 떠오른다**
            //   (사용자 보고). 툴팁은 87곳에 흩어져 있어 개별로 막을 수 없다 — 이 한 줄이
            //   전부를 막는 지점이다(클릭을 흡수하는 것과 같은 이유).
            MouseArea { anchors.fill: parent; hoverEnabled: true }
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
                // 모드 줄과 떨어뜨린다 — 6번째 탭처럼 보이면 안 된다.
                Item { width: 10; height: 1 }
                // 표시 게인 토글 — "무엇을 보고 있는가" 쪽이라 모드 줄 뒤에 둔다.
                // ⚠️오른쪽 줄(8x 읽기값 + 28px 정사각 −/+/i)에 뒀더니 줌 묶음을
                //   갈라 어색했다(사용자 보고).
                // Develop 탭에서도 보인다 — 머리 프레임(모자이크)이 같은 플래그를 따른다.
                Rectangle {
                    id: gainToggle
                    implicitWidth: gainRow.implicitWidth + 16
                    implicitHeight: 26
                    radius: 4
                    color: gainMa.containsMouse ? "#3a3a3e" : "#2c2c30"
                    border.color: peekWin.gainOn ? "#3d6fb5" : "#3f3f44"
                    Row {
                        id: gainRow
                        anchors.centerIn: parent
                        spacing: 6
                        Rectangle {              // 체크 표시
                            width: 12; height: 12; radius: 2
                            anchors.verticalCenter: parent.verticalCenter
                            color: peekWin.gainOn ? "#3d6fb5" : "transparent"
                            border.color: peekWin.gainOn ? "#5c8fd6" : "#6a6a70"
                            Text {
                                anchors.centerIn: parent
                                visible: peekWin.gainOn
                                text: "\u2713"; color: "#ffffff"; font.pixelSize: 9
                            }
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Display gain"
                            color: peekWin.gainOn ? "#e8e8e8" : "#9a9a9a"
                            font.pixelSize: 12
                        }
                    }
                    MouseArea {
                        id: gainMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: peekWin.gainOn = !peekWin.gainOn
                    }
                    // ⚠️이 파일은 Controls 를 `as B` 로 별칭 임포트해서 무한정 `ToolTip` 첨부
                    //   객체가 없다("Non-existent attached object" 로 QML 로드가 통째로 실패한다).
                    B.ToolTip.visible: gainMa.containsMouse
                    B.ToolTip.delay: 800
                    B.ToolTip.text: peekWin.gainOn
                                  ? "Brightness is lifted so the sensor values are visible"
                                  : "Showing the level the sensor recorded"
                }
            }

            Row {
                anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                spacing: 8

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: !peekWin.isDevelop
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
                    label: "−"; visible: !peekWin.isDevelop
                    onClicked: peekWin.setZoom(peekWin.zoom / 2)
                }
                BarButton {
                    label: "+"; visible: !peekWin.isDevelop
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
            // Develop 은 단계 이름과 설명을 **2단**으로 쓴다. 한 줄에 몰아넣으면 elide 로 설명이
            // 잘려 "왜 이 순서인가" 가 사라진다 — 그게 이 화면의 요점이다.
            height: peekWin.isDevelop ? 64 : 40
            color: "#1b1b1e"

            // 다른 탭 — 파이썬이 만든 캡션 한 덩어리
            Text {
                visible: !peekWin.isDevelop
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

            // Develop — [n / N] 칩 + 단계 이름(크게) + 설명(작게, 줄바꿈)
            Item {
                visible: peekWin.isDevelop
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }

                Rectangle {
                    id: stepChip
                    anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    width: 52; height: 22; radius: 3
                    color: "#26303f"; border.color: "#3d6fb5"
                    Text {
                        anchors.centerIn: parent
                        text: peekWin.devStepText
                        color: "#a8c4f0"; font.family: "Consolas"; font.pixelSize: 12
                    }
                }
                Column {
                    anchors {
                        left: stepChip.right; leftMargin: 10
                        right: parent.right
                        verticalCenter: parent.verticalCenter
                    }
                    spacing: 3
                    Text {
                        objectName: "devLabelText"
                        width: parent.width
                        text: peekWin.devLabel
                        color: "#f2f2f2"; font.pixelSize: 15; font.bold: true
                        elide: Text.ElideRight
                    }
                    Text {
                        objectName: "devNoteText"
                        width: parent.width
                        text: peekWin.devNote
                        color: "#9a9a9a"; font.pixelSize: 12
                        // ⚠️줄바꿈 + 2줄 허용. 예전엔 NoWrap + elide 라 긴 설명이 통째로 잘렸다.
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                }
            }
        }

        // ---------------- 좌: 모자이크 뷰 ----------------
        Item {
            id: view
            anchors {
                top: caption.bottom
                bottom: peekWin.isDevelop ? devBar.top : parent.bottom
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

            // ---- Develop: 셰이더 렌더 + 머리 그림 두 장(Gray/CFA)의 순차 교차 페이드 ----
            // `pipeView` 는 Main.qml 이 이미 pipeAnim 을 그리고 있으므로 그 텍스처를 그대로 쓴다.
            //
            // ★⚠️세 레이어가 **정확히 같은 사각형**을 차지해야 한다. 예전에는 그림 쪽 크기를
            //   축마다 따로 `Math.min(implicit, parent)` 로 잡았는데, 그러면 아이템 상자의 비율이
            //   그림 비율과 달라지고 `PreserveAspectFit` 의 레터박스가 **투명하게** 남는다.
            //   그 틈으로 아래 셰이더가 비쳐 "뒷 배경이 겹쳐 보여 시각화가 애매하다"는 보고를
            //   받았다. → 프레임 Item 하나를 비율에 맞춰 두고 전부 `anchors.fill` 로 채운다.
            Item {
                id: devFrame
                anchors.centerIn: parent
                visible: peekWin.isDevelop && peekWin.devSrcItem !== null
                // 프록시(=pipeAnim) 비율을 기준으로 뷰에 맞춘다.
                readonly property real srcW: (peekWin.devSrcItem && peekWin.devSrcItem.width > 0)
                        ? peekWin.devSrcItem.width : parent.width
                readonly property real srcH: (peekWin.devSrcItem && peekWin.devSrcItem.height > 0)
                        ? peekWin.devSrcItem.height : parent.height
                readonly property real fit: (srcW > 0 && srcH > 0)
                        ? Math.min(parent.width / srcW, parent.height / srcH) : 1.0
                width: Math.max(1, Math.round(srcW * fit))
                height: Math.max(1, Math.round(srcH * fit))

                // 표시 사각형이 정해진 뒤(그리고 창 리사이즈마다) 그 크기로 다시 요청한다.
                // devBegin 의 첫 요청은 devSrcItem 이 아직 null 이라 뷰 크기 폴백이다.
                onWidthChanged: devReq.restart()
                onHeightChanged: devReq.restart()
                Timer {
                    id: devReq
                    interval: 60          // 첫 그림이 이만큼 늦게 뜬다 — 짧게 잡는다
                    onTriggered: if (peekWin.isDevelop && controller.rawPeekOpened)
                        controller.developMosaic(Math.max(64, Math.round(devFrame.width)),
                                                 Math.max(64, Math.round(devFrame.height)),
                                                 peekWin.gainOn)
                }

                // 불투명 바닥 — 레이어가 반투명한 순간에도 창 배경이 비치지 않게.
                Rectangle { anchors.fill: parent; color: "#000000" }

                ShaderEffectSource {
                    id: devShader
                    anchors.fill: parent
                    sourceItem: peekWin.devSrcItem
                    textureSize: Qt.size(width, height)
                    live: true
                    hideSource: false
                    smooth: true
                    mipmap: false
                }
                // 아래: CFA 색 모자이크 / 위: Gray(센서 밝기). 위에 있는 것이 먼저 사라진다.
                // ⚠️`smooth: false` — 파이썬이 **nearest** 로 축소해 남긴 CFA 반점이 요점이다.
                //   보간을 켜면 반점이 평균돼 그냥 사진처럼 보이고 다음 단계와 구분이 안 된다.
                Image {
                    id: devMosaic
                    anchors.fill: parent
                    visible: opacity > 0.001
                    opacity: peekWin.devMosaicOpacity
                    cache: false
                    asynchronous: false
                    smooth: false
                    fillMode: Image.Stretch
                    source: (peekWin.isDevelop && controller.rawPeekOpened)
                            ? controller.developMosaicUrl : ""
                }
                Image {
                    id: devGray
                    anchors.fill: parent
                    visible: opacity > 0.001
                    opacity: peekWin.devGrayOpacity
                    cache: false
                    asynchronous: false
                    smooth: false
                    fillMode: Image.Stretch
                    source: (peekWin.isDevelop && controller.rawPeekOpened)
                            ? controller.developGrayUrl : ""
                }
                // 날짜 스탬프 — 셰이더가 아니라 오버레이다(`ui/Main.qml` 의 `stampOverlay`).
                // ⚠️지오메트리 식은 그쪽이 진실원이다. 여기서는 기준 사각형만 `devFrame` 으로
                //   바꿔 같은 비율을 다시 쓴다 — 값이 갈라지면 프리뷰와 위치가 달라진다.
                // ⚠️Develop 은 **크롭 전 전체 프레임**을 보여주므로 크롭이 걸린 사진에서는
                //   메인 프리뷰와 스탬프 위치가 다르다(뷰가 다른 것이지 값이 틀린 게 아니다).
                Image {
                    id: devStamp
                    source: controller.stampUrl
                    cache: false; smooth: true; asynchronous: false
                    visible: opacity > 0.001 && controller.stampText !== ""
                    opacity: peekWin.devStampOpacity * 0.92   // = date_stamp.STAMP_STRENGTH
                    property real shortEdge: Math.min(parent.width, parent.height)
                    width: controller.stampWRatio * shortEdge
                    height: controller.stampHRatio * shortEdge
                    property string corner: controller.stampCorner   // br/bl/tl/tr
                    property real margin: (controller.stampMargin - controller.stampBleed) * shortEdge
                    x: (corner === "br" || corner === "tr") ? parent.width - width - margin : margin
                    y: (corner === "br" || corner === "bl") ? parent.height - height - margin : margin
                }
            }

            Image {
                id: peekImg
                objectName: "rawPeekImage"      // 헤드리스 검증에서 찾기 위한 이름
                visible: !peekWin.isDevelop
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
                    // ⚠️Develop 은 줌/팬이 없다. 막지 않으면 드래그가 **다른 탭의 팬 위치**를
                    //   몰래 바꾼다(화면은 그대로라 알 수도 없다).
                    if (peekWin.isDevelop) return
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
                    peekWin.refreshSoon()      // 프레임당 1회로 묶는다(위 주석)
                }
                onWheel: function (e) {
                    // ⚠️막지 않으면 스크럽하려고 휠을 돌린 것이 **다른 탭의 줌**을 몰래 바꾼다.
                    if (peekWin.isDevelop) { e.accepted = true; return }
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
                visible: !peekWin.isDevelop && controller.rawPeekOpened
                         && r.length === 4 && visW > 0 && visH > 0
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
                    // ★위치는 **지금 끌고 있는 값**(`peekWin.cx/cy`)에서 낸다 — 마지막으로
                    //   그려진 rect 로만 그리면 디모자이크 재디코드(1.3~3.7s) 동안 십자선이
                    //   얼어붙어 **프로그램이 멈춘 것처럼 보인다**(사용자 보고). 크기(bw/bh)는
                    //   rect 를 그대로 쓴다 — 그건 실제로 그려진 크롭을 뜻해야 한다.
                    //   ⚠️렌더가 크롭을 가장자리에서 클램프하므로 여기서도 같은 클램프를 건다
                    //   (안 하면 사진 끝에서 십자선만 화면 밖으로 나간다).
                    readonly property real halfW: ok ? minimap.r[2] / 2 / minimap.visW : 0
                    readonly property real halfH: ok ? minimap.r[3] / 2 / minimap.visH : 0
                    readonly property real liveX: Math.max(halfW, Math.min(1 - halfW, peekWin.cx))
                    readonly property real liveY: Math.max(halfH, Math.min(1 - halfH, peekWin.cy))
                    readonly property real cxp: ok ? 1 + liveX * mini.paintedWidth : 0
                    readonly property real cyp: ok ? 1 + liveY * mini.paintedHeight : 0
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
                        peekWin.refreshSoon()  // 끌기도 연속 제스처다(위 주석)
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
                width: Math.max(bLabel.implicitWidth + 20, 170); height: 36; radius: 10
                color: "#000000AA"
                onVisibleChanged: if (visible) bBar.sync()
                Text {
                    id: bLabel
                    anchors { top: parent.top; topMargin: 6; horizontalCenter: parent.horizontalCenter }
                    // 후보 디모자이크는 종당 ~1.1s(LINEAR)/~4s(Markesteijn 3-pass) —
                    // 어느 알고리즘을 받고 있는지 알린다.
                    text: controller.rawPeekStatus !== "" ? controller.rawPeekStatus
                                                          : "rendering…"
                    color: "#dddddd"; font.pixelSize: 11
                }
                // 진행 바 — 실제 값은 스텝 경계(done/total)뿐이다(LibRaw 디코드는 중간 콜백이
                // 없다). 스텝 사이에는 다음 경계 직전까지 서서히 기어가(OutCubic) 멈춘 느낌을
                // 없애고, 실제 경계가 오면 그 값으로 스냅한다.
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 7 }
                    height: 4; radius: 2; color: "#44ffffff"
                    Rectangle {
                        id: bBar
                        property real frac: 0
                        property real step: controller.rawPeekProgress
                        function sync() { creep.stop(); frac = step; creep.restart() }
                        onStepChanged: sync()
                        height: parent.height; radius: 2; color: "#e8e8e8"
                        width: parent.width * Math.max(0, Math.min(1, frac))
                        NumberAnimation {
                            id: creep
                            target: bBar; property: "frac"
                            // ⚠️`step` 아래로 내려가면 안 된다 — 마지막 콜백(frac=1.0)에서
                            //   목표가 0.97 이면 막대가 100%→97% 로 **뒷걸음질**한다.
                            to: Math.max(bBar.step, Math.min(bBar.step + 0.45, 0.97))
                            duration: 5000; easing.type: Easing.OutCubic
                        }
                    }
                }
            }
        }


        // ---------------- Develop 타임라인 ----------------
        Rectangle {
            id: devBar
            visible: peekWin.isDevelop
            // ★정보 패널이 열려 있으면 그만큼 줄인다 — `view` 와 같은 폭이라야 타임라인의
            //   눈금 위치가 그림 위 어디를 가리키는지 읽힌다(사용자 보고).
            anchors {
                left: parent.left
                right: peekWin.infoOpen ? infoPane.left : parent.right
                bottom: parent.bottom
            }
            height: 56
            color: "#1b1b1e"

            Rectangle {                                  // 상단 구분선
                anchors { top: parent.top; left: parent.left; right: parent.right }
                height: 1; color: "#33333a"
            }

            // 재생/정지
            Rectangle {
                id: playBtn
                anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
                width: 30; height: 30; radius: 15
                color: playMa.containsMouse ? "#3a3a3e" : "#2c2c30"
                border.color: "#3f3f44"
                Text {
                    anchors.centerIn: parent
                    text: peekWin.devPlaying ? "\u2016" : "\u25B6"
                    color: "#e8e8e8"; font.pixelSize: 12
                }
                MouseArea {
                    id: playMa
                    anchors.fill: parent; hoverEnabled: true
                    onClicked: {
                        if (peekWin.devT >= 1.0) peekWin.devT = 0.0
                        peekWin.devPlaying = !peekWin.devPlaying
                    }
                }
            }

            // ⚠️퍼센트 폭을 **고정**한다. 내용에 맞춰 두면 "5%"->"100%" 로 글자가 늘 때마다
            //   `track` 의 오른쪽 끝이 밀려 트랙 길이가 재생 중에 계속 변한다(사용자 보고).
            //   가장 긴 문자열("100%")의 실제 폭을 재서 그만큼 잡아 둔다.
            TextMetrics {
                id: devPctMetrics
                font.family: "Consolas"
                font.pixelSize: 12
                text: "100%"
            }
            Text {
                id: devPct
                anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                width: Math.ceil(devPctMetrics.width)
                horizontalAlignment: Text.AlignRight
                text: Math.round(peekWin.devT * 100) + "%"
                color: "#9a9a9a"; font.pixelSize: 12
                font.family: "Consolas"
            }

            // 트랙 + 단계 눈금 + 핸들
            Item {
                id: track
                anchors {
                    left: playBtn.right; leftMargin: 12
                    right: devPct.left; rightMargin: 12
                    verticalCenter: parent.verticalCenter
                }
                height: 30

                Rectangle {                              // 트랙
                    id: trackBg
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
                    height: 4; radius: 2; color: "#33333a"
                }
                Rectangle {                              // 진행
                    anchors { left: trackBg.left; verticalCenter: trackBg.verticalCenter }
                    width: trackBg.width * peekWin.devT
                    height: 4; radius: 2; color: "#3d6fb5"
                }
                // 단계 눈금 — 그 사진에서 아무 일도 안 일어나는 단계는 눈금이 없다
                Repeater {
                    model: controller.developMarks
                    Rectangle {
                        // ⚠️`marks()` 는 **스킵된 단계까지** 돌려주고(active=false) 그쪽 t0/t1 은
                        //   -1 이다 — 줄 위에 자리가 없기 때문이다. 걸러내지 않으면 눈금이
                        //   x = width*(-1) 로 트랙 왼쪽 밖에 쌓인다(중립 스냅샷에서 9개).
                        //   지금까지 안 보인 것은 화면 밖 배치에 기댄 것뿐이다.
                        visible: modelData.active
                        x: trackBg.width * modelData.t1 - width / 2
                        anchors.verticalCenter: trackBg.verticalCenter
                        width: 2; height: 12; radius: 1
                        color: "#7a7a80"
                        opacity: 0.9
                    }
                }
                Rectangle {                              // 핸들
                    x: trackBg.width * peekWin.devT - width / 2
                    anchors.verticalCenter: trackBg.verticalCenter
                    width: 12; height: 12; radius: 6
                    color: "#c8dcff"; border.color: "#3d6fb5"
                }
                MouseArea {
                    id: devScrubMa
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    function scrub(mx) {
                        peekWin.devPlaying = false
                        peekWin.devT = Math.max(0, Math.min(1, mx / Math.max(1, trackBg.width)))
                    }
                    onPressed: function (e) { scrub(e.x) }
                    onPositionChanged: function (e) { if (pressed) scrub(e.x) }
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
                        text: peekWin.isDevelop
                              ? "Space = play/pause   \u2190/\u2192 = step   drag the timeline to scrub   Esc or R = close"
                              : "1..5 = mode   +/− or wheel = zoom   drag = pan   "
                              + "click the minimap to jump   Esc or R = close"
                    }
                }
            }
        }
    }
}
