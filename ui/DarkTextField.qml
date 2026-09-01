// 앱 공용 어두운 입력칸 — 탐색기 캡션 검색바와 **같은 모양**이다.
//
// ★왜 `TextField` 가 아니라 `Rectangle` + 코어 `TextInput` 인가:
//   네이티브/Basic 스타일의 `TextField` 는 `background` 를 갈아끼우면 경고를 내고 스타일마다
//   다르게 그려진다. 탐색기 검색바가 이미 이 조합을 쓰고 있어(`Main.qml` 의 `searchInput`)
//   같은 관용구로 맞춘다 — 패널마다 입력칸 모양이 다르면 그게 바로 눈에 띈다.
//
// ⚠️**새 .qml 은 `FilmRawstery.spec` 의 `QML` 목록에 등록**해야 한다. 소스 실행은 같은 폴더라
//   그냥 되고 **배포본만 깨진다**("DarkTextField is not a type" → 패널이 통째로 안 뜬다).
import QtQuick

Rectangle {
    id: root

    // --- 바깥에서 쓰는 것 ---
    property alias text: input.text
    property alias input: input                 // 포커스 제어 등 세부 접근용
    property string placeholderText: ""
    property bool clearable: false              // ✕ 지우기 버튼(검색바용)
    property int fontSize: 12
    // ⚠️`horizontalAlignment` 는 좌표칸처럼 숫자를 오른쪽에 붙이고 싶을 때 쓴다(기본 왼쪽).
    property int horizontalAlignment: TextInput.AlignLeft

    signal accepted()                           // Enter / 숫자패드 Enter
    signal cleared()                            // ✕ 를 눌러 비웠을 때
    signal escaped()                            // Esc

    function selectAllAndFocus() { input.forceActiveFocus(); input.selectAll() }

    implicitHeight: 28
    implicitWidth: 120
    radius: 5
    color: root.enabled ? "#232323" : "#1c1c1c"
    border.color: input.activeFocus ? "#8ab4f8" : "#555555"
    border.width: 1
    opacity: root.enabled ? 1.0 : 0.55

    TextInput {
        id: input
        anchors.fill: parent
        anchors.leftMargin: 8
        // ✕ 가 보일 때만 오른쪽을 비운다 — 좌표칸처럼 ✕ 가 없는 곳까지 여백을 두면
        // 긴 숫자가 괜히 일찍 잘린다.
        anchors.rightMargin: (root.clearable && input.text !== "") ? 26 : 8
        verticalAlignment: TextInput.AlignVCenter
        horizontalAlignment: root.horizontalAlignment
        enabled: root.enabled
        color: "#e6e6e6"
        font.pixelSize: root.fontSize
        clip: true
        selectByMouse: true

        Keys.onReturnPressed: root.accepted()
        Keys.onEnterPressed: root.accepted()    // 숫자패드
        Keys.onEscapePressed: root.escaped()

        Text {                                   // placeholder
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.right: parent.right
            visible: input.text === "" && !input.activeFocus
            text: root.placeholderText
            color: "#777"
            font.pixelSize: root.fontSize
            horizontalAlignment: root.horizontalAlignment
            elide: Text.ElideRight
        }
    }

    // ✕ 지우기 — 탐색기 검색바와 같은 규격(18px 원, hover 시 배경).
    Rectangle {
        anchors.right: parent.right; anchors.rightMargin: 5
        anchors.verticalCenter: parent.verticalCenter
        width: 18; height: 18; radius: 9
        visible: root.clearable && input.text !== "" && root.enabled
        color: clrHover.hovered ? "#3a3f4b" : "transparent"
        Text { anchors.centerIn: parent; text: "✕"; color: "#aaa"; font.pixelSize: 10 }
        HoverHandler { id: clrHover }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: { input.text = ""; input.forceActiveFocus(); root.cleared() }
        }
    }
}
