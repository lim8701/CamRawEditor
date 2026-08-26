// 다크 패널용 버튼. ★**`QtQuick.Controls.Basic` 을 베이스로 써야 한다** — 기본(네이티브
// Windows) 스타일은 `background`/`contentItem` 커스터마이즈를 **거부하고**
// "The current style does not support customization of this control" 경고만 남긴다.
// 그래서 plain `Button` 에 색을 줘도 밝은 회색 네이티브 버튼이 그대로 그려진다(실측).
//
// 색은 이 앱이 이미 쓰는 값들: 면 #3a3a3a / 호버 #3a3f4b(필름시뮬 콤보 delegate 와 동일) /
// 눌림 #4a4f5b / 테두리 #555 / 글자 #e8e8e8 / 비활성 #2f2f2f·#777.
//
// 쓰는 쪽에서 `contentItem` 을 따로 주면 그쪽이 이긴다(예: '✕' 아이콘 버튼).
import QtQuick
import QtQuick.Controls.Basic

Button {
    id: root
    font.pixelSize: 12
    implicitHeight: 26
    leftPadding: 10
    rightPadding: 10
    topPadding: 0
    bottomPadding: 0

    contentItem: Text {
        text: root.text
        font: root.font
        color: root.enabled ? "#e8e8e8" : "#777"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    // `flat: true` 는 존중한다 — 아이콘 버튼('✕' 레이어 삭제 등)은 면과 테두리 없이 떠 있어야
    // 하고, 호버/눌림에만 반응한다. 무시하면 촘촘한 행에 박스가 줄줄이 생긴다.
    background: Rectangle {
        radius: 3
        color: root.flat
               ? (root.down ? "#4a4f5b" : root.hovered ? "#3a3f4b" : "transparent")
               : !root.enabled ? "#2f2f2f"
                 : root.down ? "#4a4f5b"
                   : root.hovered ? "#3a3f4b" : "#3a3a3a"
        border.width: root.flat ? 0 : 1
        border.color: root.enabled ? "#555" : "#3f3f3f"
    }
}
