// 폴더 지도 — 좌표별 사진 스택을 지도 위 썸네일 마커로 그린다(Photo map, 탐색기 🗺).
//
// ★**QtLocation import 를 가진 파일이다.** `PhotoMapOverlay.qml` 이 `Loader` 로 늦게 켜므로
//   모듈이 빠진 배포본에서도 최악이 '지도만 빈다'로 끝난다(`Main.qml` 에 두면 앱이 통째로
//   안 뜬다 — `LocationMap.qml` 주석과 같은 규율).
// ⚠️`FilmRawstery.spec` 의 `QML` 목록 등록 필수(소스 실행은 되고 배포본만 깨진다).
//
// ★이 파일은 **읽기 전용**이다 — 좌표를 쓰지 않는다. 핀을 찍어 사진에 붙이는 일은
//   `LocationMap.qml`(Location 패널)이 단독으로 담당한다.
import QtQuick
import QtQuick.Controls.Basic as B
import QtLocation
import QtPositioning

Item {
    id: root

    // controller.folderMapPoints — [{lat, lon, count, rep, paths[]}, …] (count 내림순)
    property var points: []
    // 선택된 스택(클러스터). null 이면 아무것도 안 골랐다.
    property var selected: null
    signal stackPicked(var cluster)

    // 화면좌표 병합 기준. ⚠️마커 폭(76)보다 조금 넓게 — 마커가 서로 닿기 **전에** 합쳐야
    //   겹쳐 보이는 구간이 생기지 않는다.
    readonly property int mergePx: 84
    // 표시 마커 상한. `points` 가 count 내림순이라 큰 스택이 살아남는다(잘려도 큰 장소가 보인다).
    readonly property int maxMarkers: 60
    readonly property int markerW: 76
    readonly property int markerH: 92

    property var clusters: []
    // 타일이 안 뜨는 상황을 침묵으로 두지 않는다 — 마커는 우리 것이라 오프라인에도 그려진다.
    readonly property bool tilesFailed: view.map.error !== Map.NoError

    // 타일 소스·UA·앱 전용 캐시·Custom URL Map — 정책은 `OsmPlugin.qml` 한 곳에만 있다.
    OsmPlugin { id: osmPlugin }

    MapView {
        id: view
        anchors.fill: parent
        map.plugin: osmPlugin
        map.zoomLevel: 12
        // ★상한을 19로 두는 것은 실측 결과다 — 실제 폴더의 두 좌표가 **90m** 떨어져
        //   있는데 84px 병합 반경을 벗어나려면 줌 ≳17.2 가 필요하다. 상한 17이면
        //   **끝까지 확대해도 그 스택을 쪼갤 수 없다**(구현 중 헤드리스 검증에서 걸렸다).
        //   ⚠️뷰포트당 타일 수는 줌과 무관하니(같은 화면 = 같은 타일 수) 올리는 비용은
        //     없다. 타일 예절은 **첫 화면 줌**을 묶는 쪽으로 지킨다(`fitAll` 의 16 상한).
        map.maximumZoomLevel: 19

        // ★**클릭 판정을 `MapQuickItem` 안의 `MouseArea` 로 하지 않는다.** 지도 아이템은 소스를
        //   텍스처로 굽는 경로가 있어(같은 이유로 `Canvas` 를 sourceItem 에 못 쓴다 —
        //   `LocationMap.qml` 주석) 입력이 닿는지가 Qt 버전·상황에 달려 있다. 여기서는 이미
        //   병합 때문에 `map.fromCoordinate` 를 쓰고 있으므로, **같은 변환으로 직접 히트테스트**
        //   하는 편이 확실하고 짧다(마커 ≤60개라 비용이 없다).
        // ⚠️`MapView` 는 드래그/휠/핀치를 이미 쓰지만 **탭에는 기본 동작이 없다** — 충돌 없음.
        TapHandler {
            onSingleTapped: function (evt, btn) {
                var c = root.hitTest(evt.position)
                if (c) {
                    root.selected = c
                    root.stackPicked(c)
                }
            }
        }
    }

    // 지금 화면에서 이 점이 어느 마커에 떨어지나 — 겹치면 **중심에 가장 가까운** 것.
    function hitTest(pos) {
        var best = null, bestD = Infinity
        for (var i = 0; i < root.clusters.length; i++) {
            var c = root.clusters[i]
            var p = view.map.fromCoordinate(QtPositioning.coordinate(c.lat, c.lon), false)
            if (isNaN(p.x) || isNaN(p.y)) continue
            var cx = p.x, cy = p.y - root.markerH / 2      // 마커는 좌표 위에 선다
            if (Math.abs(pos.x - cx) > root.markerW / 2) continue
            if (Math.abs(pos.y - cy) > root.markerH / 2) continue
            var dx = pos.x - cx, dy = pos.y - cy
            var d = dx * dx + dy * dy
            if (d < bestD) { bestD = d; best = c }
        }
        return best
    }

    // ---------- 줌별 화면좌표 병합 ----------
    // ★파이썬은 **정확히 같은 좌표**만 묶는다(일괄 적용된 좌표는 비트 동일하다). 그것으로
    //   부족한 경우가 첫 프레임부터 나온다: 실측 폴더의 서울 두 좌표는 565m 떨어져 있는데
    //   폴더 전체(서울↔강원 130km)에 맞춘 시야에서는 **4px 안에 겹친다.** 그래서 화면
    //   픽셀 거리로 한 번 더 묶는다 — 줌을 올리면 저절로 갈라진다.
    // ⚠️`clipToViewPort=false` 로 부른다 — 화면 밖 점도 좌표를 받아야 병합이 일관된다.
    function mergeForZoom() {
        var src = root.points || []
        var n = src.length
        var used = []
        var out = []
        var i, j
        for (i = 0; i < n; i++) used.push(false)
        for (i = 0; i < n && out.length < root.maxMarkers; i++) {
            if (used[i]) continue
            used[i] = true
            var pi = view.map.fromCoordinate(
                        QtPositioning.coordinate(src[i].lat, src[i].lon), false)
            var members = [src[i]]
            var wlat = src[i].lat * src[i].count
            var wlon = src[i].lon * src[i].count
            var total = src[i].count
            var paths = src[i].paths.slice()
            if (!isNaN(pi.x)) {
                for (j = i + 1; j < n; j++) {
                    if (used[j]) continue
                    // ⚠️날짜변경선: 경도차가 180도를 넘으면 묶지 않는다(gpx.py 와 같은 가드) —
                    //   179 ↔ -179 를 지구 반 바퀴로 읽는 것을 막는다.
                    if (Math.abs(src[i].lon - src[j].lon) > 180) continue
                    var pj = view.map.fromCoordinate(
                                QtPositioning.coordinate(src[j].lat, src[j].lon), false)
                    if (isNaN(pj.x)) continue
                    var dx = pi.x - pj.x, dy = pi.y - pj.y
                    if (dx * dx + dy * dy >= root.mergePx * root.mergePx) continue
                    used[j] = true
                    members.push(src[j])
                    wlat += src[j].lat * src[j].count
                    wlon += src[j].lon * src[j].count
                    total += src[j].count
                    paths = paths.concat(src[j].paths)
                }
            }
            out.push({
                // 무게중심(사진 수 가중) — 사진이 많은 쪽에 마커가 붙는다.
                "lat": wlat / total,
                "lon": wlon / total,
                "count": total,
                "places": members.length,
                // 대표는 **가장 큰 자식의 것** — `points` 가 count 내림순이라 members[0] 이다.
                "rep": members[0].rep,
                "paths": paths,
                // 병합 전 단일 좌표일 때만 좌표를 말한다(합쳐진 것에 좌표를 쓰면 거짓말이 된다).
                "exactLat": members.length === 1 ? members[0].lat : NaN,
                "exactLon": members.length === 1 ? members[0].lon : NaN
            })
        }
        root.clusters = out
        // 고른 스택이 병합으로 사라졌으면 같은 자리를 담은 클러스터로 옮겨 붙인다(선택 유지).
        if (root.selected) {
            var keep = null
            for (i = 0; i < out.length; i++)
                if (out[i].paths.indexOf(root.selected.paths[0]) >= 0) { keep = out[i]; break }
            root.selected = keep
        }
    }

    // ⚠️줌/이동 중에 매 프레임 다시 묶으면 마커가 춤춘다 — 코얼레싱한다.
    Timer {
        id: mergeTimer
        interval: 120
        onTriggered: root.mergeForZoom()
    }
    Connections {
        target: view.map
        function onZoomLevelChanged() { mergeTimer.restart() }
        function onCenterChanged() { mergeTimer.restart() }
        // ⚠️`supportedMapTypes` 는 비동기로 채워진다 — 점 표기 핸들러는 조용히 안 걸리므로
        //   `Connections` 로 명시할 것(`OsmPlugin.useCustomTiles` 주석).
        function onSupportedMapTypesChanged() { osmPlugin.useCustomTiles(view.map) }
    }
    // ★⚠️**같은 내용이 다시 와도 `onPointsChanged` 는 발화한다.** `points` 는 파이썬의
    //   `QVariantList` 라 읽을 때마다 **새 JS 배열**이 되고, `folderMapChanged` 는 사진을
    //   열 때마다(`_set_gps` → `_regroup_map_points`) 나온다. 그대로 두면 지도를 확대해
    //   가까운 두 지점을 갈라 놓은 사용자가 스트립에서 사진 하나를 여는 순간 **시야가 폴더
    //   전체로 되돌아간다**. 그래서 내용 지문이 실제로 바뀌었을 때만 선택 해제 + 시야 맞춤을
    //   한다(마커 재계산은 값이 같아도 싸고 안전하므로 항상).
    property string _pointsSig: ""
    function _sigOf(p) {
        var s = []
        for (var i = 0; i < (p ? p.length : 0); i++)
            s.push(p[i].lat + "," + p[i].lon + "x" + p[i].count)
        return s.join("|")
    }
    onPointsChanged: {
        var sig = root._sigOf(root.points)
        var changed = (sig !== root._pointsSig)
        root._pointsSig = sig
        if (changed)
            root.selected = null
        root.mergeForZoom()
        if (changed)
            root.fitAll()
    }
    onWidthChanged: mergeTimer.restart()
    onHeightChanged: mergeTimer.restart()

    // 폴더 전체가 한 화면에 들어오게. 점이 하나면 그 자리로.
    // ⚠️크기가 아직 0이면 `visibleRegion` 이 아무 일도 하지 않는다 — 다음 프레임으로 미룬다.
    function fitAll() {
        var p = root.points || []
        if (p.length === 0) return
        if (root.width < 2 || root.height < 2) { Qt.callLater(root.fitAll); return }
        if (p.length === 1) {
            view.map.center = QtPositioning.coordinate(p[0].lat, p[0].lon)
            view.map.zoomLevel = 14
            return
        }
        var minLat = p[0].lat, maxLat = p[0].lat, minLon = p[0].lon, maxLon = p[0].lon
        for (var i = 1; i < p.length; i++) {
            minLat = Math.min(minLat, p[i].lat); maxLat = Math.max(maxLat, p[i].lat)
            minLon = Math.min(minLon, p[i].lon); maxLon = Math.max(maxLon, p[i].lon)
        }
        // 마커가 가장자리에 물리지 않게 여유(마커는 좌표 위로 92px 서 있다).
        var padLat = Math.max((maxLat - minLat) * 0.25, 0.002)
        var padLon = Math.max((maxLon - minLon) * 0.25, 0.002)
        view.map.visibleRegion = QtPositioning.rectangle(
            QtPositioning.coordinate(Math.min(85, maxLat + padLat), Math.max(-180, minLon - padLon)),
            QtPositioning.coordinate(Math.max(-85, minLat - padLat), Math.min(180, maxLon + padLon)))
        // 한 장소에 다 모여 있으면 위 사각형이 아주 작아 최대 줌까지 당겨진다 — 타일 예절과
        // 보기 편함을 위해 상한을 둔다(`docs/photo_map.md` 의 타일 사용량 항).
        if (view.map.zoomLevel > 16) view.map.zoomLevel = 16
    }

    // ---------- 마커 ----------
    // ★⚠️**`MapItemView` 를 `MapView` 의 자식으로 선언하면 안 보인다.** `MapView` 는 `Map` 을
    //   감싼 평범한 `Item` 이라 그 안의 지도 아이템은 Item 의 자식으로 들어갈 뿐 지도의
    //   `mapItems` 에 등록되지 않는다(핀에서 실제로 그랬다 — 좌표는 바뀌는데 안 그려짐).
    //   `map.addMapItemView()` 로 등록한다.
    MapItemView {
        id: markerView
        model: root.clusters
        delegate: MapQuickItem {
            required property var modelData
            coordinate: QtPositioning.coordinate(modelData.lat, modelData.lon)
            anchorPoint.x: cell.width / 2
            anchorPoint.y: cell.height          // 꼭지가 좌표를 가리킨다
            // ⚠️소스에 `Canvas` 를 쓰지 않는다 — 지도 아이템은 소스를 텍스처로 굽고 Canvas 의
            //   첫 paint 가 그보다 늦어 빈 텍스처가 남을 수 있다. 도형·Image 만 쓴다.
            sourceItem: Item {
                id: cell
                width: root.markerW
                height: root.markerH
                readonly property bool isStack: modelData.count > 1
                readonly property bool isSel:
                    root.selected && root.selected.paths[0] === modelData.paths[0]

                // 덱(스택) 느낌 — 뒤에 살짝 어긋난 카드 두 장. 한 장뿐이면 안 그린다.
                Rectangle {
                    visible: cell.isStack
                    x: 12; y: 8; width: 60; height: 60; radius: 5
                    color: "#20242c"; border.color: "#5a606c"; border.width: 1
                }
                Rectangle {
                    visible: cell.isStack
                    x: 8; y: 4; width: 60; height: 60; radius: 5
                    color: "#262b34"; border.color: "#6a707c"; border.width: 1
                }
                // 대표 썸네일
                Rectangle {
                    id: card
                    x: 2; y: 0; width: 68; height: 68; radius: 5
                    color: "#141414"
                    border.color: cell.isSel ? "#8ab4f8" : "#e8e8e8"
                    border.width: cell.isSel ? 3 : 2
                    clip: true
                    Image {
                        anchors.fill: parent
                        anchors.margins: card.border.width
                        // ★`win.thumbPx` 를 반드시 거친다 — 요청은 논리 픽셀이라 Qt 가 DPR 을
                        //   곱하고, 160 을 넘으면 0.6ms → 43.5ms(72배) 경로로 넘어간다.
                        sourceSize.width: win.thumbPx(72)
                        source: "image://thumb/" + encodeURIComponent(modelData.rep)
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: true
                    }
                }
                // 꼬리 — 카드 아래 좌표를 가리키는 침
                Rectangle {
                    x: 35; y: 66; width: 2; height: root.markerH - 66
                    color: cell.isSel ? "#8ab4f8" : "#1a1a1a"
                }
                // 개수 배지(스택일 때만)
                Rectangle {
                    visible: cell.isStack
                    anchors.right: card.right; anchors.top: card.top
                    anchors.margins: -6
                    width: Math.max(20, cnt.implicitWidth + 10); height: 20; radius: 10
                    color: "#8ab4f8"; border.color: "#12151a"; border.width: 2
                    B.Label {
                        id: cnt
                        anchors.centerIn: parent
                        text: modelData.count
                        color: "#12151a"; font.pixelSize: 11; font.bold: true
                    }
                }
            }
        }
    }

    Component.onCompleted: {
        view.map.addMapItemView(markerView)
        osmPlugin.useCustomTiles(view.map)
        root.mergeForZoom()
        root.fitAll()
    }

    // 타일은 온라인이라야 온다 — 마커는 그대로 뜨므로 좌표 산포는 여전히 읽힌다.
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 12
        visible: root.tilesFailed
        width: Math.min(parent.width - 32, msg.implicitWidth + 24)
        height: msg.implicitHeight + 14
        radius: 6
        color: "#cc1a1a1a"; border.color: "#4a4a4a"; border.width: 1
        B.Label {
            id: msg
            anchors.centerIn: parent
            color: "#c8c8c8"; font.pixelSize: 11
            text: "Map tiles unavailable (offline?) - the markers still show where your photos are."
        }
    }
}
