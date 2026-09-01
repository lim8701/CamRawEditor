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

    // ---------- 장소 검색(지오코딩) ----------
    // ★번들 OSM 플러그인의 `GeocodeModel` = Nominatim. **새 의존성이 없다.**
    // ⚠️Nominatim 사용 정책은 식별 가능한 User-Agent(위 `osm.useragent`)와 **초당 1회 이하**를
    //   요구한다 → 질의는 **Enter/버튼으로만** 한다. 타건마다 부르는 자동완성은 정책 위반이다.
    property int searchStatus: GeocodeModel.Null
    property string searchError: ""
    property var results: []
    signal searchDone(int count)      // -1 = 오류

    GeocodeModel {
        id: geo
        plugin: osmPlugin
        autoUpdate: false
        limit: 6
        onStatusChanged: {
            root.searchStatus = status
            if (status === GeocodeModel.Ready) {
                var a = []
                for (var i = 0; i < count; i++) {
                    var loc = geo.get(i)
                    a.push({ "label": loc.address.text,
                             "lat": loc.coordinate.latitude,
                             "lon": loc.coordinate.longitude })
                }
                root.results = a
                root.searchError = ""
                root.searchDone(a.length)
            } else if (status === GeocodeModel.Error) {
                root.results = []
                root.searchError = geo.errorString
                root.searchDone(-1)
            }
        }
    }

    function search(text) {
        var q = String(text).trim()
        if (q === "") return
        root.results = []
        geo.query = q
        geo.update()
    }

    // 검색 결과로 시야를 옮긴다(핀은 호출부가 따로 정한다 — 시야와 핀은 다른 동작이다).
    function goTo(la, lo) {
        view.map.center = QtPositioning.coordinate(la, lo)
    }

    // 지도를 핀 위치로 옮긴다 — **명시적 호출로만** 한다(사진을 열 때, 탭에 들어올 때).
    // ★⚠️`map.center` 를 좌표에 **바인딩하지 말 것** — 클릭할 때마다 지도가 핀을 가운데로
    //   끌어와 화면이 튄다(사용자 보고). 핀을 찍는 것과 시야를 옮기는 것은 다른 동작이다.
    function recenter() {
        view.map.center = QtPositioning.coordinate(root.lat, root.lon)
    }

    MapView {
        id: view
        anchors.fill: parent
        map.plugin: osmPlugin
        map.zoomLevel: 13

        // 클릭 = 그 지점으로 핀. MapView 가 드래그/휠을 이미 쓰므로 탭만 받는다.
        TapHandler {
            onTapped: function (evt, btn) {
                var c = view.map.toCoordinate(evt.position)
                if (c.isValid) root.picked(c.latitude, c.longitude)
            }
        }
    }

    // ★⚠️**핀은 `MapView` 의 자식으로 선언하면 안 보인다.** `MapView` 는 `Map` 을 감싼 평범한
    //   `Item` 이라 그 안에 적은 `MapQuickItem` 은 Item 의 자식으로 들어갈 뿐 **지도의 mapItems
    //   에 등록되지 않는다**(클릭 좌표는 바뀌는데 핀만 안 그려진다 — 실제로 그랬다).
    //   `Map` 에 직접 넣거나 `addMapItem` 으로 등록해야 한다.
    MapQuickItem {
        id: pinItem
        visible: root.hasPin
        coordinate: QtPositioning.coordinate(root.lat, root.lon)
        anchorPoint.x: pin.width / 2
        anchorPoint.y: pin.height              // 핀 끝이 좌표를 가리킨다
        // ⚠️`Canvas` 를 sourceItem 으로 쓰지 않는다 — 지도 아이템은 소스를 텍스처로 굽는데
        //   Canvas 는 첫 paint 시점이 그보다 늦어 빈 텍스처가 남을 수 있다. 도형으로 그린다.
        sourceItem: Item {
            id: pin
            width: 22; height: 30
            Rectangle {                        // 꼬리(머리보다 먼저 — 뒤에 깔린다)
                x: 10; y: 12; width: 2; height: 18
                color: "#1a1a1a"
            }
            Rectangle {                        // 머리
                x: 2; y: 0; width: 18; height: 18; radius: 9
                color: "#8ab4f8"
                border.color: "#1a1a1a"; border.width: 2
            }
            Rectangle {                        // 가운데 구멍
                x: 8; y: 6; width: 6; height: 6; radius: 3
                color: "#1a1a1a"
            }
        }
    }
    Component.onCompleted: { view.map.addMapItem(pinItem); root.recenter() }

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
