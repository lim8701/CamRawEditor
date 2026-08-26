// 단축키·마우스 조작 목록 오버레이 (`?` 또는 F1).
//
// ★목록은 여기서 만들지 않는다 — `shortcuts.py` 가 단일 진실원이고 `controller.shortcutHelp` /
//   `controller.mouseHelp` 로 받아 그리기만 한다. `python shortcuts.py` 가 그 표와 QML 의 실제
//   `Shortcut{}` 선언을 대조하므로, 단축키를 추가하고 목록을 안 고치면 검사에서 걸린다.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: root
    modal: true
    dim: true
    padding: 0
    anchors.centerIn: Overlay.overlay
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }

    // 키보드 그룹 + 마우스 그룹을 한 줄기로 잇는다. 마우스 쪽은 키 칸에 "Double-click"/"Drag"
    // 가 그대로 들어가 스스로 설명되므로 따로 표시를 붙이지 않는다.
    readonly property var groups: {
        var out = []
        var kb = controller.shortcutHelp
        for (var i = 0; i < kb.length; i++) out.push(kb[i])
        var ms = controller.mouseHelp
        for (var j = 0; j < ms.length; j++) out.push(ms[j])
        return out
    }
    // 두 열로 나눈다. 그룹 수가 아니라 **줄 수**로 갈라야 한쪽이 길어지지 않는다
    // (제목 1줄 + 항목 수를 무게로 센다).
    readonly property int splitAt: {
        var total = 0, k
        for (k = 0; k < groups.length; k++) total += groups[k].rows.length + 1
        var acc = 0
        for (k = 0; k < groups.length; k++) {
            acc += groups[k].rows.length + 1
            if (acc * 2 >= total) return k + 1
        }
        return groups.length
    }
    readonly property var leftGroups: groups.slice(0, splitAt)
    readonly property var rightGroups: groups.slice(splitAt)

    // ⚠️창보다 커지지 않게 **폭·높이를 모두 제약**한다. 실측(오프스크린 796px 창)에서 폭을
    //   내용에 맡겼더니 팝업이 창 폭에 눌려 **오른쪽 열 설명이 잘렸다**. 폭은 상한을 두고,
    //   설명은 잘라내지 않고 **줄바꿈**시킨다(설명이 곧 내용이라 elide 는 답이 아니다).
    //   높이는 넘치면 스크롤한다.
    readonly property int availW: (Overlay.overlay ? Overlay.overlay.width : 1200) - 64
    readonly property int availH: (Overlay.overlay ? Overlay.overlay.height : 900) - 64
    width: Math.min(940, availW)
    height: Math.min(head.height + 1 + body.implicitHeight + 32, availH)

    background: Rectangle { color: "#2b2b2b"; border.color: "#555"; radius: 8 }

    contentItem: ColumnLayout {
        spacing: 0

        // ── 머리글 ──
        RowLayout {
            id: head
            Layout.fillWidth: true
            Layout.margins: 16
            Label {
                Layout.fillWidth: true
                text: "Shortcuts"
                color: "#e8e8e8"; font.pixelSize: 15; font.bold: true
            }
            Label {
                text: "Esc to close"
                color: "#8a8a8a"; font.pixelSize: 11
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

        // ── 두 열 (넘치면 스크롤) ──
        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 16
            contentWidth: width
            contentHeight: body.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

        RowLayout {
            id: body
            width: parent.width
            spacing: 28
            Repeater {
                model: [root.leftGroups, root.rightGroups]
                ColumnLayout {
                    required property var modelData
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0     // 두 열을 같은 폭으로
                    spacing: 10
                    Repeater {
                        model: parent.modelData
                        ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 3
                            Label {
                                text: modelData.title
                                color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Repeater {
                                model: modelData.rows
                                RowLayout {
                                    required property var modelData
                                    spacing: 10
                                    // 키 칸은 폭 고정 — 설명이 세로로 정렬돼야 읽힌다.
                                    Rectangle {
                                        Layout.preferredWidth: 124
                                        Layout.preferredHeight: 20
                                        radius: 3
                                        color: "#343434"
                                        border.color: "#4d4d4d"
                                        Label {
                                            anchors.centerIn: parent
                                            width: parent.width - 8
                                            text: modelData.keys
                                            color: "#e8e8e8"; font.pixelSize: 11
                                            horizontalAlignment: Text.AlignHCenter
                                            elide: Text.ElideRight
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.desc
                                        color: "#c8c8c8"; font.pixelSize: 11
                                        wrapMode: Text.WordWrap
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
}
