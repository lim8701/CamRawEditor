// Location 패널(Ctrl+6) — 사진에 붙일 위치를 사람이 정한다.
//
// 배경: 카메라의 블루투스 연결이 끊기기 쉬워 촬영 시점 지오태깅이 사실상 불가능하다.
// 그래서 현상 단계에서 붙이고, `pipeline.save_image` 가 **export JPEG 의 EXIF GPS** 로만
// 내보낸다(원본 RAW 는 건드리지 않는다).
//
// ★위치는 **룩이 아니라 사진별 메타데이터**다 — 레시피(.frpreset)와 룩 복사에는 실리지 않는다.
// ⚠️QtLocation import 는 `LocationMap.qml` 에 가둬 두고 Loader 로 늦게 켠다(그 파일 주석).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Controls.Basic as B

Flickable {
    id: root

    // 탐색기에서 체크된 사진 수 / 일괄 적용 요청(경로 수집은 Main.qml 이 한다).
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
            text: "Click the map to place this photo. The coordinates are written to exported "
                  + "JPEGs as standard EXIF GPS - the RAW file is never modified."
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
                    item.lat = Qt.binding(function () { return controller.gpsSet ? controller.gpsLat : 37.5665 })
                    item.lon = Qt.binding(function () { return controller.gpsSet ? controller.gpsLon : 126.9780 })
                    item.hasPin = Qt.binding(function () { return controller.gpsSet })
                    item.picked.connect(function (la, lo) {
                        controller.setGps({ "lat": la, "lon": lo, "alt": null, "src": "map" })
                    })
                }
            }
            Connections {
                target: root
                function onPanelActiveChanged() {
                    if (root.panelActive) mapLoader.everActive = true
                }
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

        // ── 좌표 ──
        // 지도가 주 입력이지만, 타일이 안 뜨는(오프라인) 상황에서 유일한 폴백이다.
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            B.Label { text: "Lat"; color: "#9a9a9a"; font.pixelSize: 11 }
            B.TextField {
                id: latField
                objectName: "gpsLatField"
                Layout.fillWidth: true
                enabled: root.enabledForPhoto
                font.pixelSize: 12
                placeholderText: "37.566500"
                text: controller.gpsSet ? controller.gpsLat.toFixed(6) : ""
                onAccepted: { root.commitFields(); focus = false }
                Keys.onEscapePressed: focus = false
                // ⚠️인라인 `text:` 바인딩은 첫 사용자 편집에서 끊긴다 → 다시 맞춰 준다.
                Connections {
                    target: controller
                    function onGpsChanged() {
                        var v = controller.gpsSet ? controller.gpsLat.toFixed(6) : ""
                        if (!latField.activeFocus && latField.text !== v) latField.text = v
                    }
                }
            }
            B.Label { text: "Lon"; color: "#9a9a9a"; font.pixelSize: 11 }
            B.TextField {
                id: lonField
                objectName: "gpsLonField"
                Layout.fillWidth: true
                enabled: root.enabledForPhoto
                font.pixelSize: 12
                placeholderText: "126.978000"
                text: controller.gpsSet ? controller.gpsLon.toFixed(6) : ""
                onAccepted: { root.commitFields(); focus = false }
                Keys.onEscapePressed: focus = false
                Connections {
                    target: controller
                    function onGpsChanged() {
                        var v = controller.gpsSet ? controller.gpsLon.toFixed(6) : ""
                        if (!lonField.activeFocus && lonField.text !== v) lonField.text = v
                    }
                }
            }
        }

        B.Label {
            Layout.fillWidth: true
            color: "#7a7a7a"; font.pixelSize: 10
            text: controller.gpsSet
                  ? ("Set" + (controller.gpsSrc !== "" ? " from " + controller.gpsSrc : "")
                     + (controller.gpsAlt !== null ? "  -  " + controller.gpsAlt.toFixed(0) + " m" : ""))
                  : "No location on this photo"
        }

        // ── 동작 ──
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            DarkButton {
                text: "Clear"
                enabled: root.enabledForPhoto && controller.gpsSet
                onClicked: { latField.text = ""; lonField.text = ""; controller.clearGps() }
            }
            DarkButton {
                text: root.checkedCount > 0
                      ? "Apply to " + root.checkedCount + " checked" : "Apply to checked"
                enabled: root.enabledForPhoto && controller.gpsSet && root.checkedCount > 0
                onClicked: root.applyToCheckedRequested()
            }
            Item { Layout.fillWidth: true }
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
            B.Label { text: "Camera clock"; color: "#9a9a9a"; font.pixelSize: 11 }
            B.ComboBox {
                id: tzCombo
                Layout.preferredWidth: 110
                font.pixelSize: 12
                // -12..+14 정시 오프셋. 30/45분 지역은 아래 미세 보정으로 맞춘다.
                model: {
                    var a = []
                    for (var h = -12; h <= 14; h++)
                        a.push((h >= 0 ? "UTC+" : "UTC") + h)
                    return a
                }
                currentIndex: 12 + Math.round(root.localUtcOffsetHours())
            }
            B.Label { text: "Shift"; color: "#9a9a9a"; font.pixelSize: 11 }
            B.SpinBox {
                id: shiftSpin
                Layout.preferredWidth: 110
                from: -3600; to: 3600; stepSize: 10; value: 0
                font.pixelSize: 12
                textFromValue: function (v) { return v + " s" }
                valueFromText: function (t) { return parseInt(t) || 0 }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            DarkButton {
                text: root.checkedCount > 0
                      ? "Load GPX for " + root.checkedCount + " checked..." : "Load GPX..."
                enabled: root.checkedCount > 0
                onClicked: root.loadGpxRequested()
            }
            Item { Layout.fillWidth: true }
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

    // 좌표칸 -> 컨트롤러. 못 읽는 값이면 아무것도 하지 않는다(칸은 다음 gpsChanged 에 복구된다).
    function commitFields() {
        var la = parseFloat(latField.text), lo = parseFloat(lonField.text)
        if (isNaN(la) || isNaN(lo)) return
        if (la < -90 || la > 90 || lo < -180 || lo > 180) return
        controller.setGps({ "lat": la, "lon": lo, "alt": null, "src": "manual" })
    }
}
