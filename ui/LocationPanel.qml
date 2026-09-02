// Location 패널(Ctrl+6) — 사진에 붙일 위치를 사람이 정한다.
//
// 배경: 카메라의 블루투스 연결이 끊기기 쉬워 촬영 시점 지오태깅이 사실상 불가능하다.
// 그래서 현상 단계에서 붙이고, `pipeline.save_image` 가 **export JPEG 의 EXIF GPS** 로만
// 내보낸다(원본 RAW 는 건드리지 않는다).
//
// ★위치는 **룩이 아니라 사진별 메타데이터**다 — 레시피(.frpreset)와 룩 복사에는 실리지 않는다.
//
// ★⚠️**지도를 더블클릭해도 사진에 바로 반영하지 않는다.** 그게 곧 저장이면 실수로 누른 좌표가
//   그대로 사이드카에 들어가고 undo 스텝까지 쌓여 혼란스럽다(사용자 보고). 더블클릭은 **초안**만
//   바꾸고, `Apply` 를 눌러야 사진에 붙는다.
// ⚠️핀 지정은 **더블클릭**이다(단일 클릭 아님) — 지도를 훑어보려고 누르기만 해도 핀이 옮겨져
//   초안이 조용히 바뀌었다. 초안은 `Ctrl+Z` 가 안 닿으므로(아래 Revert 주석) 특히 나빴다.
// ⚠️QtLocation import 는 `LocationMap.qml` 에 가둬 두고 Loader 로 늦게 켠다(그 파일 주석).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Controls.Basic as B

Flickable {
    id: root
    objectName: "locationPanel"   // 헤드리스 레이아웃 측정용(폭이 300px 패널에 들어가는지)

    // 탐색기에서 체크된 사진 수 / 일괄 적용·GPX 요청(경로 수집은 Main.qml 이 한다).
    property int checkedCount: 0
    // 이 패널이 화면에 있는가 — 지도 Loader 를 여기에 물려 **탭을 열 때만** 타일을 받으러 간다.
    property bool panelActive: false
    signal applyToCheckedRequested()
    signal loadGpxRequested()

    clip: true
    contentWidth: width
    contentHeight: col.height + 32
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: B.ScrollBar { width: 12; policy: ScrollBar.AsNeeded }

    readonly property bool enabledForPhoto: controller.imagePath !== ""

    // ⚠️파이썬의 `None` 은 QML 에서 `undefined` 다 — `null` 비교만으로는 안 걸린다(실측 TypeError).
    readonly property var photoAlt: {
        var a = controller.gpsAlt
        return (a === undefined || a === null) ? null : a
    }

    // ---------- 장소 검색 ----------
    // 지도 컴포넌트(QtLocation)가 질의를 맡고, 여기는 결과만 받아 그린다.
    property var searchResults: []
    property string searchNote: ""
    property bool searching: false

    function runSearch() {
        if (!mapLoader.item) return
        var q = searchField.text.trim()
        if (q === "") return
        root.searchResults = []
        root.searchNote = "Searching..."
        root.searching = true
        mapLoader.item.search(q)
    }

    // 결과를 고르면 **시야를 옮기고 초안도 그 자리로** 둔다. 저장은 여전히 Apply 를 눌러야
    // 일어나므로(위 초안 규율) 잘못 고른 결과가 사진에 남지 않는다.
    function pickResult(r) {
        if (mapLoader.item) mapLoader.item.goTo(r.lat, r.lon)
        root.setDraft(r.lat, r.lon, "search")
        root.searchResults = []
        root.searchNote = ""
    }

    // ---------- 초안(draft) ----------
    // 지도 클릭·좌표 입력이 바꾸는 값. 사진에 붙은 값(controller.gps*)과는 **별개**이고,
    // `Apply` 를 눌러야 넘어간다. 사진을 넘기면 새 사진의 값으로 다시 맞춰진다(초안 폐기).
    property bool hasDraft: false
    property real draftLat: 37.5665
    property real draftLon: 126.9780
    property var  draftAlt: null
    property string draftSrc: ""

    // 초안이 사진에 붙은 값과 다른가 = '아직 적용 안 됨'. 1e-7도 ~= 1cm.
    readonly property bool draftDiffers:
        root.hasDraft !== controller.gpsSet
        || (root.hasDraft && controller.gpsSet
            && (Math.abs(root.draftLat - controller.gpsLat) > 1e-7
                || Math.abs(root.draftLon - controller.gpsLon) > 1e-7))

    function syncDraftFromPhoto() {
        root.hasDraft = controller.gpsSet
        if (controller.gpsSet) {
            root.draftLat = controller.gpsLat
            root.draftLon = controller.gpsLon
            root.draftAlt = root.photoAlt
            root.draftSrc = controller.gpsSrc
        } else {
            root.draftAlt = null
            root.draftSrc = ""
        }
        // ★사진이 바뀌면 **시야도 그 사진으로** 옮긴다. 여기서 하는 이유가 둘 있다:
        //   ① 이 함수가 사진 쪽 값이 바뀌는 **모든 경로**(로드·undo·프리셋·Apply)를 지난다.
        //   ② ⚠️`centerOn` 에 **방금 세운 원천 값**을 직접 넘긴다 — 별도 핸들러에서
        //      `recenter()` 를 부르면 지도의 `lat` 바인딩이 아직 갱신 전이라 **시야가 한 장
        //      뒤처진다**(실측으로 잡은 버그. `LocationMap.centerOn` 주석 참조).
        // ⚠️**`panelActive` 가드는 필수다.** `mapLoader.active` 는 한 번 켜지면 다시 꺼지지
        //   않으므로, 이걸 빼면 탭을 떠난 뒤에도 사진을 넘길 때마다 **숨은 지도의 카메라가
        //   움직인다** — QtLocation 은 보임 여부가 아니라 카메라로 타일을 받으므로 그대로
        //   타일 요청이 된다(이 파일과 `OsmPlugin.qml` 이 못 박은 '탭을 열 때만 받는다' 정책 위반).
        //   탭에 없을 때의 시야는 `onPanelActiveChanged`/`onLoaded` 가 들어올 때 맞춘다.
        if (root.panelActive && root.hasDraft && mapLoader.item)
            mapLoader.item.centerOn(root.draftLat, root.draftLon)
    }
    // 사진 전환·undo·프리셋 적용 등 **사진 쪽 값이 바뀌는 모든 경로**가 이 시그널을 지난다.
    Connections {
        target: controller
        function onGpsChanged() { root.syncDraftFromPhoto() }
    }
    Component.onCompleted: root.syncDraftFromPhoto()

    function setDraft(la, lo, src) {
        root.draftLat = la; root.draftLon = lo
        root.draftAlt = null            // 지도/수동 입력에는 고도가 없다
        root.draftSrc = src
        root.hasDraft = true
    }

    function applyToPhoto() {
        if (!root.hasDraft) return
        controller.setGps({ "lat": root.draftLat, "lon": root.draftLon,
                            "alt": root.draftAlt, "src": root.draftSrc })
    }

    // 초안과 사진의 위치를 모두 없앤다. 확인 대화상자(`clearConfirm`)를 거쳐서만 불린다.
    function doClear() {
        root.hasDraft = false
        root.draftAlt = null; root.draftSrc = ""
        latField.text = ""; lonField.text = ""
        if (controller.gpsSet) controller.clearGps()
    }

    ColumnLayout {
        id: col
        width: root.width - 24
        x: 12
        y: 12
        spacing: 12

        B.Label {
            text: "Location"
            color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
            font.capitalization: Font.AllUppercase
        }

        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: "#9a9a9a"; font.pixelSize: 11
            text: "Double-click the map to choose a spot, then Apply. The coordinates are written "
                  + "to exported JPEGs as standard EXIF GPS - the RAW file is never modified."
        }

        // ── 장소 검색 ──
        // ⚠️Enter/Go 로만 질의한다 — Nominatim 정책이 초당 1회 이하를 요구하므로 타건마다
        //   부르는 자동완성은 쓰지 않는다(LocationMap.qml 주석).
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            DarkTextField {
                id: searchField
                objectName: "gpsSearchField"
                Layout.fillWidth: true
                enabled: mapLoader.status === Loader.Ready && !root.searching
                placeholderText: "Search a place"
                clearable: true                  // 탐색기 캡션 검색바와 같은 ✕
                onAccepted: root.runSearch()
                // ✕ 는 입력만 비우는 게 아니라 **결과 목록과 안내문까지** 치운다 — 검색 상태를
                // 통째로 되돌리는 버튼이라야 "지웠는데 결과가 남아 있다"가 안 생긴다.
                onCleared: { root.searchResults = []; root.searchNote = "" }
                onEscaped: { root.searchResults = []; searchField.input.focus = false }
            }
            DarkButton {
                text: "Go"
                enabled: mapLoader.status === Loader.Ready && !root.searching
                         && searchField.text.trim() !== ""
                onClicked: root.runSearch()
            }
        }
        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            visible: root.searchNote !== ""
            color: "#9a9a9a"; font.pixelSize: 10
            text: root.searchNote
        }
        // 결과 — 고르면 지도가 그리로 가고 초안 핀이 놓인다(저장은 Apply 를 눌러야 한다).
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            visible: root.searchResults.length > 0
            Repeater {
                model: root.searchResults
                delegate: Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: resLabel.implicitHeight + 10
                    radius: 3
                    color: resMouse.containsMouse ? "#3a3f4b" : "#333"
                    B.Label {
                        id: resLabel
                        anchors.fill: parent
                        anchors.margins: 5
                        text: modelData.label
                        color: "#d8d8d8"; font.pixelSize: 10
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                    MouseArea {
                        id: resMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.pickResult(modelData)
                    }
                }
            }
        }

        // ── 지도 ──
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 260          // ⚠️고정 높이 — 너비 기반은 스크롤바와 레이아웃 루프
            color: "#141414"
            border.color: "#444"; border.width: 1
            clip: true

            Loader {
                id: mapLoader
                anchors.fill: parent
                anchors.margins: 1
                // ★탭을 처음 열 때 켜고, 켜진 뒤에는 유지한다(탭을 오갈 때마다 타일 재요청 방지).
                property bool everActive: false
                active: everActive
                source: "LocationMap.qml"
                onLoaded: {
                    // 핀은 **초안**을 가리킨다 — 사용자가 지금 고르는 자리다.
                    item.lat = Qt.binding(function () { return root.draftLat })
                    item.lon = Qt.binding(function () { return root.draftLon })
                    item.hasPin = Qt.binding(function () { return root.hasDraft })
                    item.picked.connect(function (la, lo) { root.setDraft(la, lo, "map") })
                    item.searchDone.connect(function (n) {
                        root.searching = false
                        root.searchResults = (n > 0) ? item.results : []
                        root.searchNote = (n < 0) ? ("Search failed: " + item.searchError)
                                                  : (n === 0 ? "Nothing found." : "")
                    })
                    // 바인딩이 방금 걸렸으므로 여기서도 원천 값으로 넘긴다(위와 같은 이유).
                    if (root.hasDraft) item.centerOn(root.draftLat, root.draftLon)
                }
            }
            Connections {
                target: root
                function onPanelActiveChanged() {
                    if (!root.panelActive) return
                    if (!mapLoader.everActive) { mapLoader.everActive = true; return }
                    if (mapLoader.item) mapLoader.item.recenter()   // 탭에 들어올 때만 시야 이동
                }
            }
            // ⚠️여기에 있던 `onGpsChanged -> recenter()` 블록은 **제거했다.** 그것이 바로
            //   시야가 한 장 뒤처지던 원인이다(핸들러가 지도의 `lat` 바인딩보다 먼저 돈다).
            //   시야 이동은 이제 `syncDraftFromPhoto()` 가 원천 값으로 직접 한다.

            // ── 시야를 핀으로 되돌리기 ──
            // 지도를 끌어 옮기면 핀이 화면 밖으로 나갈 수 있는데, `recenter()` 는 사진을 열거나
            // 탭에 들어올 때만 불려서 **손으로 되돌릴 방법이 없었다**(지도 중심을 좌표에
            // 바인딩하지 않는 것은 의도다 — 클릭마다 화면이 튀지 않게 하려고, LocationMap 주석).
            // ⚠️패널 세로 공간을 안 쓰도록 지도 위에 얹는다(패널 폭 300px · 지도 높이 260 고정).
            Rectangle {
                anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 8
                width: 26; height: 26; radius: 5
                visible: mapLoader.status === Loader.Ready && root.hasDraft
                color: recenterHover.hovered ? "#3a3f4b" : "#232323cc"
                border.color: "#555"; border.width: 1
                Text {
                    anchors.centerIn: parent
                    text: "⌖"                     // 조준점 — '여기로 돌아오기'
                    color: "#d8d8d8"; font.pixelSize: 15
                }
                HoverHandler { id: recenterHover }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (mapLoader.item) mapLoader.item.recenter()
                }
                ToolTip.visible: recenterHover.hovered
                ToolTip.delay: 400
                ToolTip.text: root.draftDiffers
                              ? "Back to the pin you are placing"
                              : "Back to this photo's location"
            }

            // QtLocation 이 없는 빌드에서도 앱은 살아 있다 — 여기만 비고 좌표칸은 그대로 쓴다.
            B.Label {
                anchors.centerIn: parent
                width: parent.width - 32
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: "#9a9a9a"; font.pixelSize: 11
                visible: mapLoader.status === Loader.Error
                text: "Map component unavailable in this build. Enter coordinates below."
            }
        }

        // ⚠️**OSM 저작권 표기는 의무다**(User-Agent 와 같은 급). Qt 의 지도가 자체 표기를
        //   그리지만 274px 상자 안에서 잘릴 수 있어(실측: 오프스크린에서 상자 밖에 놓였다)
        //   여기에 한 줄로 못 박아 둔다.
        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: "#6a6a6a"; font.pixelSize: 10
            text: "Map data © OpenStreetMap contributors"
        }

        // ── 좌표 ──
        // 지도가 주 입력이지만, 타일이 안 뜨는(오프라인) 상황에서 유일한 폴백이다.
        // ⚠️패널은 300px 고정이다(스크롤바 12 + 여백 24 → 쓸 수 있는 폭 약 264).
        //   라벨과 필드를 한 줄에 넷 늘어놓으면 잘린다 — 라벨 열을 좁게 고정하고 한 줄에 하나씩.
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            B.Label {
                text: "Lat"; color: "#9a9a9a"; font.pixelSize: 11
                Layout.preferredWidth: 26
            }
            DarkTextField {
                id: latField
                objectName: "gpsLatField"
                Layout.fillWidth: true
                enabled: root.enabledForPhoto
                placeholderText: "37.566500"
                text: root.hasDraft ? root.draftLat.toFixed(6) : ""
                onAccepted: { root.commitFields(); latField.input.focus = false }
                onEscaped: latField.input.focus = false
                // ⚠️인라인 `text:` 바인딩은 첫 사용자 편집에서 끊긴다 → 초안이 바뀌면 다시 맞춘다.
                Connections {
                    target: root
                    function onDraftLatChanged() { latField.resync() }
                    function onHasDraftChanged() { latField.resync() }
                }
                function resync() {
                    var v = root.hasDraft ? root.draftLat.toFixed(6) : ""
                    if (!latField.input.activeFocus && latField.text !== v) latField.text = v
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            B.Label {
                text: "Lon"; color: "#9a9a9a"; font.pixelSize: 11
                Layout.preferredWidth: 26
            }
            DarkTextField {
                id: lonField
                objectName: "gpsLonField"
                Layout.fillWidth: true
                enabled: root.enabledForPhoto
                placeholderText: "126.978000"
                text: root.hasDraft ? root.draftLon.toFixed(6) : ""
                onAccepted: { root.commitFields(); lonField.input.focus = false }
                onEscaped: lonField.input.focus = false
                Connections {
                    target: root
                    function onDraftLonChanged() { lonField.resync() }
                    function onHasDraftChanged() { lonField.resync() }
                }
                function resync() {
                    var v = root.hasDraft ? root.draftLon.toFixed(6) : ""
                    if (!lonField.input.activeFocus && lonField.text !== v) lonField.text = v
                }
            }
        }

        // 지금 상태를 한 줄로 — '아직 적용 안 됨'을 눈에 띄게 말한다.
        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            font.pixelSize: 10
            color: root.draftDiffers ? "#e0c07a" : "#7a7a7a"
            text: root.draftDiffers
                  ? "Not applied yet - press Apply to attach it to this photo."
                  : (controller.gpsSet
                     ? ("On this photo"
                        + (controller.gpsSrc !== "" ? "  -  from " + controller.gpsSrc : "")
                        + (root.photoAlt !== null
                           ? "  -  " + root.photoAlt.toFixed(0) + " m" : ""))
                     : "No location on this photo")
        }

        // ── 동작 ──
        // ★두 Apply 는 **범위만 다른 같은 동작**이다(이 사진 / 탐색기에서 체크한 N장).
        //   예전에는 "Apply to this photo" 와 "Apply to N checked" 를 서로 다른 행에 폭도
        //   다르게 놓아 **중복 버튼처럼 보이고 배치도 사선으로 어긋났다**(사용자 보고).
        //   그래서 "Apply to" 를 **그룹 라벨로 올리고 버튼에는 범위만** 남긴다 — 문구 중복이
        //   사라지고, 둘 다 같은 폭으로 쌓여 정렬이 맞는다.
        // ⚠️라벨은 섹션 제목(LOCATION/GPX TRACK)이 아니라 **필드 라벨(Lat/Clock)과 같은 급**으로
        //   둔다 — 같은 굵기로 하면 Location 섹션 안에서 제목이 둘로 보인다.
        B.Label {
            text: "Apply to"
            color: "#9a9a9a"; font.pixelSize: 11
        }
        DarkButton {
            Layout.fillWidth: true
            text: "This photo"
            enabled: root.enabledForPhoto && root.hasDraft && root.draftDiffers
            onClicked: root.applyToPhoto()
        }
        DarkButton {
            Layout.fillWidth: true
            // 체크가 없으면 숫자를 못 쓰니 문구만 — 비활성이라 눌리지도 않는다.
            text: root.checkedCount > 0
                  ? root.checkedCount + " checked photos" : "Checked photos"
            enabled: root.enabledForPhoto && root.hasDraft && root.checkedCount > 0
            onClicked: root.applyToCheckedRequested()
        }
        // Revert = 초안을 사진에 붙어 있던 값으로 되돌린다 / Clear = 위치 자체를 없앤다.
        // ★⚠️**Revert 가 필요한 이유: 초안 이동은 `Ctrl+Z` 로 못 되돌린다.** 지도 클릭은
        //   사진을 건드리지 않으므로(초안 규율) undo 스냅샷이 쌓이지 않는다 — 위치가 이미
        //   있는 사진에서 지도를 잘못 눌러 핀이 옮겨지면, Revert 가 없으면 **되돌릴 방법이
        //   전혀 없다**(Apply 해서 덮어쓰거나 사진을 넘겼다 오는 것뿐이었다).
        // ⚠️둘 다 `fillWidth` 로 **정확히 반씩** 나눈다(한쪽만 fill 하면 다시 사선이 된다).
        //   덜 파괴적인 Revert 를 왼쪽에 둔다.
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            DarkButton {
                Layout.fillWidth: true
                Layout.preferredWidth: 1     // Clear 와 같은 값 = 남는 공간을 정확히 반씩
                text: "Revert"
                // 되돌릴 차이가 있을 때만. 사진에 위치가 없는데 핀만 찍은 경우도 여기 해당하며,
                // 그때는 `syncDraftFromPhoto` 가 hasDraft 를 false 로 만들어 핀이 사라진다.
                enabled: root.enabledForPhoto && root.draftDiffers
                // 값만 되돌리고 시야를 두면 핀이 화면 밖에 있을 수 있다 —
                // `syncDraftFromPhoto()` 가 원천 값으로 시야까지 같이 옮긴다.
                onClicked: root.syncDraftFromPhoto()
            }
            DarkButton {
                Layout.fillWidth: true
                // ⚠️Revert 와 **같아야** 한다 — `fillWidth` 는 implicitWidth 위에 남는 공간을
                //   나눠 주므로, 글자가 긴 쪽이 넓어진다(실측 Revert 143 / Clear 125).
                Layout.preferredWidth: 1
                text: "Clear"
                enabled: root.enabledForPhoto && (controller.gpsSet || root.hasDraft)
                // ⚠️바로 지우지 않는다 — 아래 확인 대화상자를 거친다(Revert 와 달리 사진에
                //   붙은 값을 없애는 동작이라, 오조작이면 되돌리려고 undo 를 찾아야 한다).
                onClicked: clearConfirm.open()
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

        // ── GPX 트랙 매칭 ──
        B.Label {
            text: "GPX Track"
            color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
            font.capitalization: Font.AllUppercase
        }
        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: "#9a9a9a"; font.pixelSize: 11
            text: "Match a phone-recorded track against each photo's capture time. "
                  + "EXIF has no time zone, so tell it which one the camera clock was in."
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            B.Label {
                text: "Clock"; color: "#9a9a9a"; font.pixelSize: 11
                Layout.preferredWidth: 40
            }
            DarkComboBox {
                id: tzCombo
                Layout.fillWidth: true
                // -12..+14 정시 오프셋. 30/45분 지역은 아래 미세 보정으로 맞춘다.
                model: {
                    var a = []
                    for (var h = -12; h <= 14; h++)
                        a.push((h >= 0 ? "UTC+" : "UTC") + h)
                    return a
                }
                currentIndex: 12 + Math.round(root.localUtcOffsetHours())
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            B.Label {
                text: "Shift"; color: "#9a9a9a"; font.pixelSize: 11
                Layout.preferredWidth: 40
            }
            DarkSpinBox {
                id: shiftSpin
                Layout.fillWidth: true
                from: -3600; to: 3600; stepSize: 10; value: 0
                textFromValue: function (v) { return v + " s" }
                valueFromText: function (t) { return parseInt(t) || 0 }
            }
        }
        DarkButton {
            Layout.fillWidth: true
            text: root.checkedCount > 0
                  ? "Load GPX for " + root.checkedCount + " checked..." : "Load GPX..."
            enabled: root.checkedCount > 0
            onClicked: root.loadGpxRequested()
        }
        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            visible: root.gpxStatus !== ""
            color: "#7a7a7a"; font.pixelSize: 10
            text: root.gpxStatus
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

        B.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: "#7a7a7a"; font.pixelSize: 10
            text: "GPS is written to JPEG exports only - PNG and TIFF have no standard place for it."
        }
    }

    property string gpxStatus: ""
    readonly property int utcOffsetSec: (tzCombo.currentIndex - 12) * 3600 + shiftSpin.value

    // 기기 시간대를 초기값으로 — 대개 카메라 시계와 같은 지역이다.
    function localUtcOffsetHours() {
        return -(new Date().getTimezoneOffset()) / 60
    }

    // 좌표칸 -> **초안**. 못 읽는 값이면 아무것도 하지 않는다(칸은 다음 초안 변경에 복구된다).
    function commitFields() {
        var la = parseFloat(latField.text), lo = parseFloat(lonField.text)
        if (isNaN(la) || isNaN(lo)) return
        if (la < -90 || la > 90 || lo < -180 || lo > 180) return
        root.setDraft(la, lo, "manual")
    }

    // ── Clear 확인 ──
    // 모양은 앱의 다른 확인 대화상자(`Main.qml` 의 `quitDialog`·`aiCpuDialog`)와 같은 컨셉이다:
    // 어두운 라운드 카드 + 상단 필름 퍼포레이션 + 반씩 나눈 두 버튼.
    // ⚠️`Overlay.overlay` 에 중앙 정렬한다 — 이 패널이 `Flickable` 안이라 여기에 그리면
    //   스크롤을 따라 움직이고 폭 300px 에 갇힌다.
    Popup {
        id: clearConfirm
        objectName: "clearConfirm"   // 헤드리스 확인용(패널의 다른 objectName 과 같은 목적)
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

        // 사진에 이미 붙어 있던 값을 지우는가(= 되돌리려면 undo 가 필요) / 초안만 버리는가.
        readonly property bool removesApplied: controller.gpsSet

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

                B.Label {
                    text: clearConfirm.removesApplied ? "Remove this photo's location?"
                                                      : "Discard the pin?"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                B.Label {
                    // ★GPS 는 `editParams()` 에 들어가므로 undo 스냅샷에 실린다 — 지운 뒤에도
                    //   Ctrl+Z 로 돌아온다(확인함). 초안만 버리는 경우는 애초에 사진이 안 바뀐다.
                    text: clearConfirm.removesApplied
                          ? "The location is removed from this photo and will not be written to "
                            + "exports. Ctrl+Z brings it back."
                          : "The pin you placed is discarded. This photo has no location saved, "
                            + "so nothing on it changes."
                    color: "#9a9a9a"; font.pixelSize: 13
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 12
                    Rectangle {                      // Cancel
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: ccCancel.containsMouse ? "#3a3a3d" : "#2e2e31"
                        border.color: "#55555a"; border.width: 1
                        B.Label {
                            anchors.centerIn: parent; text: "Cancel"
                            color: "#e6e6e6"; font.pixelSize: 13
                        }
                        MouseArea {
                            id: ccCancel
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: clearConfirm.close()
                        }
                    }
                    Rectangle {                      // 확인 — 지우는 쪽이라 붉은 기운
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: ccOk.containsMouse ? "#8a3b3b" : "#6f3030"
                        border.color: "#a24a4a"; border.width: 1
                        B.Label {
                            anchors.centerIn: parent
                            text: clearConfirm.removesApplied ? "Remove" : "Discard"
                            color: "#f2f2f2"; font.pixelSize: 13; font.bold: true
                        }
                        MouseArea {
                            id: ccOk
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: { root.doClear(); clearConfirm.close() }
                        }
                    }
                }
            }
        }
    }
}
