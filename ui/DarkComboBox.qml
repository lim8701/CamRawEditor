// 다크 패널용 콤보박스. ★**`QtQuick.Controls.Basic` 베이스여야 한다** — 기본(네이티브
// Windows) 스타일은 `background`/`contentItem`/`popup` 커스터마이즈를 **거부하고**
// "The current style does not support customization of this control" 경고만 남긴다
// (`DarkButton.qml` 의 같은 주석 참조 — 거기서 실측으로 확인했다).
//
// 색은 `DarkButton` 과 같은 값으로 맞춘다: 면 #3a3a3a / 호버·선택 #3a3f4b / 테두리 #555 /
// 글자 #e8e8e8 / 비활성 #2f2f2f·#777. 팝업은 패널 배경보다 살짝 밝은 #2b2b2b.
//
// ⚠️**새 .qml 은 `FilmRawstery.spec` 의 `QML` 목록에 등록**할 것(배포본만 깨진다).
import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: root
    font.pixelSize: 12
    implicitHeight: 26
    leftPadding: 10
    rightPadding: 26                       // 화살표 자리

    contentItem: Text {
        text: root.displayText
        font: root.font
        color: root.enabled ? "#e8e8e8" : "#777"
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    // 화살표 — 글리프 대신 도형이다(폰트 없는 환경에서 두부가 되지 않게).
    indicator: Canvas {
        x: root.width - width - 9
        y: root.topPadding + (root.availableHeight - height) / 2
        width: 9; height: 5
        contextType: "2d"
        Connections {
            target: root
            function onPressedChanged() { root.indicator.requestPaint() }
        }
        onPaint: {
            var c = getContext("2d")
            c.reset()
            c.moveTo(0, 0); c.lineTo(width, 0); c.lineTo(width / 2, height)
            c.closePath()
            c.fillStyle = root.enabled ? "#c8c8c8" : "#777"
            c.fill()
        }
    }

    background: Rectangle {
        radius: 3
        color: !root.enabled ? "#2f2f2f"
             : (root.pressed ? "#4a4f5b" : (root.hovered ? "#3a3f4b" : "#3a3a3a"))
        border.color: root.activeFocus ? "#8ab4f8" : "#555"
        border.width: 1
    }

    popup: Popup {
        y: root.height + 2
        width: root.width
        implicitHeight: Math.min(contentItem.implicitHeight + 2, 240)
        padding: 1

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            color: "#2b2b2b"
            border.color: "#555"; border.width: 1
            radius: 3
        }
    }

    delegate: ItemDelegate {
        width: root.width
        height: 24
        // ⚠️`model[root.textRole]` 은 문자열 모델에서 undefined 다 — 문자열 배열도 쓰므로
        //   `modelData` 를 우선한다(이 패널의 시간대 목록이 그 경우다).
        contentItem: Text {
            text: modelData !== undefined ? modelData : model[root.textRole]
            color: "#e8e8e8"
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: (root.highlightedIndex === index) ? "#3a3f4b"
                 : (root.currentIndex === index ? "#33383f" : "transparent")
        }
        padding: 0
        leftPadding: 10
    }
}
