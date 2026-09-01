// 다크 패널용 스핀박스. ★`QtQuick.Controls.Basic` 베이스여야 하는 이유는 `DarkButton.qml`
// 주석과 같다(네이티브 스타일은 커스터마이즈를 거부한다).
//
// 색은 `DarkButton`/`DarkComboBox` 와 동일. 입력칸 면은 `DarkTextField` 와 같은 #232323 을
// 써서 '여기는 타이핑하는 곳'임을 버튼과 구분해 보여준다.
//
// ⚠️**새 .qml 은 `FilmRawstery.spec` 의 `QML` 목록에 등록**할 것(배포본만 깨진다).
import QtQuick
import QtQuick.Controls.Basic

SpinBox {
    id: root
    font.pixelSize: 12
    implicitHeight: 26
    editable: true

    contentItem: TextInput {
        text: root.displayText
        font: root.font
        color: root.enabled ? "#e6e6e6" : "#777"
        selectionColor: "#8ab4f8"
        selectedTextColor: "#1a1a1a"
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Qt.AlignVCenter
        readOnly: !root.editable
        validator: root.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        selectByMouse: true
    }

    background: Rectangle {
        implicitWidth: 110
        radius: 3
        color: root.enabled ? "#232323" : "#1c1c1c"
        border.color: root.activeFocus ? "#8ab4f8" : "#555"
        border.width: 1
    }

    // ⚠️두 버튼은 `up.`/`down.` 붙은 프로퍼티로 상태를 읽는다(`root.hovered` 가 아니다) —
    //   안 그러면 한쪽에 올려도 양쪽이 같이 밝아진다.
    up.indicator: Rectangle {
        x: root.width - width - 1
        y: 1
        height: root.height - 2
        implicitWidth: 24
        radius: 3
        color: !root.enabled ? "transparent"
             : (root.up.pressed ? "#4a4f5b" : (root.up.hovered ? "#3a3f4b" : "#3a3a3a"))
        Text {
            anchors.centerIn: parent
            text: "+"                      // 도형 대신 ASCII — 폰트가 없어도 두부가 안 된다
            color: root.enabled ? "#c8c8c8" : "#777"
            font.pixelSize: 14
        }
    }

    down.indicator: Rectangle {
        x: 1
        y: 1
        height: root.height - 2
        implicitWidth: 24
        radius: 3
        color: !root.enabled ? "transparent"
             : (root.down.pressed ? "#4a4f5b" : (root.down.hovered ? "#3a3f4b" : "#3a3a3a"))
        Text {
            anchors.centerIn: parent
            text: "−"                      // U+2212 (하이픈보다 굵고 + 와 폭이 맞는다)
            color: root.enabled ? "#c8c8c8" : "#777"
            font.pixelSize: 14
        }
    }
}
