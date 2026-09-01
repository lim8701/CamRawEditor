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

    // 타일 소스. ★⚠️**Qt 의 기본 설정을 쓰면 안 된다** — Qt 의 OSM 플러그인은 시작 시
    // `maps-redirect.qt.io` 에 제공자를 물어보는데, 실측 결과 **`street` 를 포함한 전 타입이
    // Thunderforest 로 리디렉트된다**(키가 필요한 상용 서비스). 키 없는 요청은 IP 단위로
    // 허용량이 있고, 넘으면 지도 대신 **"API Key Required" 워터마크 타일**이 온다(사용자 보고).
    //   → 리디렉트를 끄고(`providersrepository.disabled`) OSM 본 서버를 직접 지정한다.
    // ⚠️하드코딩 폴백 제공자(Street Map 등)도 그대로 남아 있으므로 **`activeMapType` 을
    //   Custom URL Map 으로 직접 바꿔야 한다**(`useCustomTiles`). 안 그러면 여전히 Thunderforest 다.
    // 타일 서버를 바꿔 끼우는 자리(생성 시점에만 유효 — 플러그인 파라미터라 나중에 바꿔도
    // 반영되지 않는다). OSM 본 서버는 **가벼운 사용**을 전제로 한 공용 자원이라, 사용자가
    // 늘면 자기 키를 쓰는 제공자(Thunderforest·MapTiler 등)로 갈아 끼우는 것이 정도다.
    property string tileHost: "https://tile.openstreetmap.org/"

    Plugin {
        id: osmPlugin
        name: "osm"
        // ⚠️**식별 가능한 User-Agent 는 선택이 아니다** — OSM 타일 사용 정책이 요구한다.
        //   Qt 기본값("Qt Location based application")으로 배포하면 정책 위반이고
        //   타일 서버가 차단할 수 있다. (실측: 이 값이 실제 타일 요청 헤더로 나간다.)
        // ⚠️`controller` 가 아직/이미 없는 순간에 바인딩이 재평가되면 TypeError 가 난다(실측:
        //   컴포넌트 파괴 시점). 폴백을 둔다 — UA 는 비면 안 되고 버전은 부가 정보다.
        PluginParameter {
            name: "osm.useragent"
            value: "FilmRawstery/" + (controller ? controller.appVersion : "")
                   + " (+https://github.com/lim8701/FilmRawstery)"
        }
        PluginParameter { name: "osm.mapping.providersrepository.disabled"; value: true }
        PluginParameter { name: "osm.mapping.custom.host"; value: root.tileHost }
        PluginParameter {
            name: "osm.mapping.custom.mapcopyright"
            value: "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
        }
        PluginParameter {
            name: "osm.mapping.custom.datacopyright"
            value: "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
        }
        // ★⚠️**앱 전용 캐시 폴더를 쓴다.** Qt 기본 캐시에는 리디렉트 시절 받은
        //   "API Key Required" 워터마크 타일이 그대로 저장돼 있어, 타일 소스를 고쳐도
        //   **계속 그 그림이 보인다**(실측: 캐시를 지우기 전에는 요청이 0건이었다).
        PluginParameter {
            name: "osm.mapping.cache.directory"
            value: controller ? controller.mapCacheDir : ""
        }
        // 사진 위치를 고르는 용도라 이 정도면 넉넉하다(무한히 커지지 않게 상한을 둔다).
        PluginParameter { name: "osm.mapping.cache.disk.size"; value: 20971520 }   // 20 MiB
    }

    // Custom URL Map 을 활성 타입으로 만든다. ⚠️`supportedMapTypes` 는 **비동기로 채워지므로**
    //   완료 시점과 변경 시점 양쪽에서 시도해야 한다(한 번만 부르면 놓친다).
    function useCustomTiles() {
        var ts = view.map.supportedMapTypes
        for (var i = 0; i < ts.length; i++) {
            if (ts[i].style === MapType.CustomMap) {
                if (view.map.activeMapType !== ts[i]) view.map.activeMapType = ts[i]
                return true
            }
        }
        return false
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
    // ⚠️`supportedMapTypes` 는 **비동기로 채워진다** — 완료 시점에 한 번만 부르면 목록이
    //   아직 비어 있어 놓친다(실측: 타일 요청 0건). 목록이 바뀔 때마다 다시 시도한다.
    //   ⚠️`map.onSupportedMapTypesChanged:` 같은 점 표기 시그널 핸들러는 **조용히 안 걸린다**
    //     (경고도 안 난다) — `Connections` 로 명시할 것.
    Connections {
        target: view.map
        function onSupportedMapTypesChanged() { root.useCustomTiles() }
    }

    Component.onCompleted: {
        view.map.addMapItem(pinItem)
        root.useCustomTiles()
        root.recenter()
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
