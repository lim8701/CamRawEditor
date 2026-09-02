// OSM 타일 제공자 설정 — ★**타일 정책의 단일 진실원.**
//
// 원래 이 블록은 `LocationMap.qml` 안에 있었다. 폴더 지도(`FolderMap.qml`)가 생기면서 두 지도가
// 각자 복사본을 들게 되는데, 아래 파라미터들은 **하나하나 사고를 겪고 얻은 값**이라 한쪽만
// 고쳐지는 날이 반드시 온다. 그래서 컴포넌트로 뽑았다 — 두 지도가 이 파일을 인스턴스화한다.
// 경위·실측은 `docs/geotagging.md`.
//
// ⚠️QtLocation import 를 가진 파일이므로 `Main.qml` 이 직접 import 하면 안 된다(모듈이 빠진
//   배포본에서 앱이 통째로 안 뜬다). 지도 파일들만 쓰고, 그 파일들은 `Loader` 로 늦게 켠다.
// ⚠️`FilmRawstery.spec` 의 `QML` 목록 등록 필수(소스 실행은 되고 배포본만 깨진다).
import QtQuick
import QtLocation

Plugin {
    id: root
    name: "osm"

    // 타일 서버를 바꿔 끼우는 자리. ★⚠️**플러그인 파라미터는 생성 시점에만 유효하다** —
    //   나중에 바꿔도 반영되지 않는다. OSM 본 서버는 **가벼운 사용**을 전제로 한 공용 자원이라,
    //   사용자가 늘면 자기 키를 쓰는 제공자(Thunderforest·MapTiler 등)로 갈아 끼우는 것이 정도다.
    property string tileHost: "https://tile.openstreetmap.org/"

    // ⚠️**식별 가능한 User-Agent 는 선택이 아니다** — OSM 타일 사용 정책이 요구한다.
    //   Qt 기본값("Qt Location based application")으로 배포하면 정책 위반이고 타일 서버가
    //   차단할 수 있다. (실측: 이 값이 실제 타일 요청 헤더로 나간다.)
    // ⚠️`controller` 가 아직/이미 없는 순간에 바인딩이 재평가되면 TypeError 가 난다(실측:
    //   컴포넌트 파괴 시점). 폴백을 둔다 — UA 는 비면 안 되고 버전은 부가 정보다.
    PluginParameter {
        name: "osm.useragent"
        value: "FilmRawstery/" + (controller ? controller.appVersion : "")
               + " (+https://github.com/lim8701/FilmRawstery)"
    }

    // ★⚠️**Qt 의 기본 설정을 쓰면 안 된다** — Qt 의 OSM 플러그인은 시작 시
    //   `maps-redirect.qt.io` 에 제공자를 물어보는데, 실측 결과 **`street` 를 포함한 전 타입이
    //   Thunderforest 로 리디렉트된다**(키가 필요한 상용 서비스). 키 없는 요청은 IP 단위로
    //   허용량이 있고, 넘으면 지도 대신 **"API Key Required" 워터마크 타일**이 온다(사용자 보고).
    //     → 리디렉트를 끄고 OSM 본 서버를 직접 지정한다.
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
    // 캐시를 크게 잡는 이유는 용량이 아니라 **남의 서버를 덜 두드리기 위해서**다. 자주 다니는
    // 지역을 재요청하지 않는 것이 그대로 정책 준수다. 실측 타일 ~29KB → 200MiB ≈ 7,000장.
    // ⚠️Photo map(전체화면)이 생기면서 뷰포트당 타일이 패널의 ~8배가 됐다 — 캐시가 그만큼
    //   더 중요해졌다(`docs/photo_map.md` 의 타일 사용량 항).
    PluginParameter { name: "osm.mapping.cache.disk.size"; value: 209715200 }   // 200 MiB

    // Custom URL Map 을 활성 타입으로 만든다.
    // ⚠️**리디렉트를 껐다고 끝이 아니다** — 하드코딩 폴백 제공자(Street Map 등)가 그대로 남아
    //   있고 `activeMapType` 은 여전히 그쪽이다. 직접 바꿔야 한다.
    // ⚠️`supportedMapTypes` 는 **비동기로 채워지므로** 완료 시점과 변경 시점 양쪽에서 시도해야
    //   한다(한 번만 부르면 목록이 비어 있어 놓친다 — 실측: 타일 요청 0건).
    //   ⚠️`map.onSupportedMapTypesChanged:` 같은 점 표기 시그널 핸들러는 **조용히 안 걸린다**
    //     (경고도 안 난다) — 호출부가 `Connections` 로 명시할 것.
    function useCustomTiles(map) {
        var ts = map.supportedMapTypes
        for (var i = 0; i < ts.length; i++) {
            if (ts[i].style === MapType.CustomMap) {
                if (map.activeMapType !== ts[i]) map.activeMapType = ts[i]
                return true
            }
        }
        return false
    }
}
