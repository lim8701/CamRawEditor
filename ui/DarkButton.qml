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
    // ★⚠️**`checked` 를 직접 그려야 한다.** `AbstractButton.down` 은 `pressed` 만 따라가고
    //   `checked` 를 반영하지 않는다(실측: checked=true 로 둬도 down=false). 네이티브 스타일은
    //   스타일이 대신 그려줬으므로, Basic 으로 옮기면서 `checkable` 버튼들(Landscape/Portrait,
    //   Flip horizontal/vertical)의 활성 표시가 통째로 사라졌다 — 어느 쪽이 켜졌는지 알 수
    //   없게 된다(사이드카에서 복원한 상태도 마찬가지).
    //   켜짐은 이 앱의 선택 강조색 #8ab4f8 테두리 + 파랑 기운 면으로 표시한다.
    background: Rectangle {
        radius: 3
        color: root.flat
               ? (root.down ? "#4a4f5b" : root.hovered ? "#3a3f4b" : "transparent")
               : !root.enabled ? "#2f2f2f"
                 : root.down ? "#4a4f5b"
                   : root.checked ? (root.hovered ? "#46577a" : "#3d4a63")
                     : root.hovered ? "#3a3f4b" : "#3a3a3a"
        border.width: root.flat ? 0 : 1
        border.color: !root.enabled ? "#3f3f3f"
                      : root.checked ? "#8ab4f8" : "#555"
    }
}
