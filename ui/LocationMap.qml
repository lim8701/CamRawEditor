// 지도 픽커 — ★**QtLocation import 를 이 파일 하나에 가둔다.**
// Main.qml 최상단에 import 하면 프리즌 빌드에서 모듈이 빠졌을 때 **앱 전체가 안 뜬다**
// ("EditedBadge is not a type → 메인 창이 아예 안 뜸" 과 같은 부류). 여기 가두고
// LocationPanel 이 Loader 로 늦게 켜면 최악의 경우가 '탭이 비어 있음'으로 끝난다.
// ⚠️`FilmRawstery.spec` 의 `QML` 목록 등록 필수(소스 실행은 되고 배포본만 깨진다).
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtLocation
import QtPositioning

Item {
    id: root

    // 핀 위치(없으면 hasPin=false). 바깥에서 바꾸면 지도가 그리로 이동한다.
    property real lat: 37.5665
    property real lon: 126.9780
    property bool hasPin: false
    signal picked(real lat, real lon)

    Plugin {
        id: osmPlugin
        name: "osm"
        // ⚠️**식별 가능한 User-Agent 는 선택이 아니다** — OSM 타일 사용 정책이 요구한다.
        //   Qt 기본값("Qt Location based application")으로 배포하면 정책 위반이고
        //   타일 서버가 차단할 수 있다.
        // ⚠️`controller` 가 아직/이미 없는 순간에 바인딩이 재평가되면 TypeError 가 난다(실측:
        //   컴포넌트 파괴 시점). 폴백을 둔다 — UA 는 비면 안 되고 버전은 부가 정보다.
        PluginParameter {
            name: "osm.useragent"
            value: "FilmRawstery/" + (controller ? controller.appVersion : "")
        }
    }

    MapView {
        id: view
        anchors.fill: parent
        map.plugin: osmPlugin
        map.zoomLevel: 13
        map.center: QtPositioning.coordinate(root.lat, root.lon)

        // 클릭 = 그 지점으로 핀. MapView 가 드래그/휠을 이미 쓰므로 탭만 받는다.
        TapHandler {
            onTapped: function (evt, btn) {
                var c = view.map.toCoordinate(evt.position)
                if (c.isValid) root.picked(c.latitude, c.longitude)
            }
        }

        MapQuickItem {
            visible: root.hasPin
            coordinate: QtPositioning.coordinate(root.lat, root.lon)
            anchorPoint.x: pin.width / 2
            anchorPoint.y: pin.height          // 핀 끝이 좌표를 가리킨다
            sourceItem: Canvas {
                id: pin
                width: 22; height: 30
                onPaint: {
                    var ctx = getContext("2d"); ctx.reset()
                    ctx.strokeStyle = "#1a1a1a"; ctx.lineWidth = 2
                    ctx.fillStyle = "#8ab4f8"
                    ctx.beginPath()
                    ctx.arc(11, 11, 9, Math.PI * 0.82, Math.PI * 0.18)
                    ctx.lineTo(11, 29)
                    ctx.closePath(); ctx.fill(); ctx.stroke()
                    ctx.fillStyle = "#1a1a1a"
                    ctx.beginPath(); ctx.arc(11, 11, 3.2, 0, 2 * Math.PI); ctx.fill()
                }
            }
        }
    }

    // 타일은 온라인이라야 온다 — 안 뜨는 상황을 침묵으로 두지 않는다(좌표칸이 폴백).
    B.Label {
        anchors.centerIn: parent
        visible: view.map.error !== Map.NoError
        width: parent.width - 32
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        color: "#9a9a9a"; font.pixelSize: 11
        text: "Map tiles unavailable (offline?). You can still type coordinates below."
    }
}
