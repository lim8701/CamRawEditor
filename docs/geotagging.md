# 지오태깅 — 사진에 위치를 사람이 붙인다 🇰🇷

## 왜 만들었나

카메라의 블루투스 연결이 지속되기 어려워 **촬영 시점 지오태깅이 사실상 불가능**하다. 그래서
위치를 **현상 단계에서** 붙인다. 붙인 좌표는 사진별 사이드카에 저장되고, **export 한 JPEG 의
표준 EXIF GPS IFD** 로만 나간다. **원본 RAW 는 건드리지 않는다.**

## 설계의 축 — 위치는 '룩'이 아니다

`cropX` · `stampText` 와 같은 등급의 **사진별 메타데이터**다. 이 한 줄이 나머지를 전부 결정한다.

- **셰이더 uniform 0개** → `adjust.frag` · `displaycm.frag` · `pipe`/`pipeFull`/`pipeAnim` 무관.
  CLAUDE.md 의 ★렌더 경로 4중 계약에 **들어가지 않는다**(픽셀에 영향이 없다).
- **`_PRESET_KEYS` · `LOOK_DEFAULTS` 에 넣지 않는다.** 레시피가 남의 좌표를 옮기면 사고다.
  `presets.look_hash` 가 `_PRESET_KEYS` 로 걸러 주므로 룩 지문·레시피 배지가 자동으로 안전하고,
  `presets.validate_edits` 가 손편집된 `.frpreset` 의 gps 키를 조용히 버린다.
- **`_copyExclude` 에는 넣는다** — 룩 복사/붙여넣기가 좌표를 옮기면 안 된다.

사이드카 키: `gpsLat` · `gpsLon` · `gpsAlt`(없을 수 있음) · `gpsSrc`(`map`/`gpx`/`manual`/`exif`).

⚠️**위치가 없어도 키를 빼지 않고 `null` 을 넣는다.** 키가 없으면 다음 로드에서 `_load` 가
파일의 EXIF GPS 로 폴백해 **사용자가 지운 위치가 되살아난다**(지우기가 안 먹는 것으로 보인다).

## EXIF GPS 라이터 (`pipeline._exif_app1`)

원래 이 함수는 `Software`/`DateTime` **ASCII 두 개 전용**이었고 모든 값을 무조건 오프셋으로
적었다. GPS 를 넣으려면 세 가지가 필요했다.

1. **타입 일반화** — 엔트리를 `(tag, type, count, payload)` 로 바꾸고 BYTE/ASCII/LONG/RATIONAL
   크기표(`_TIFF_TYPE_SIZE`)를 뒀다.
2. ★**인라인 값 경로** — 4바이트 이하는 오프셋이 아니라 값을 그대로(좌측 정렬) 적어야 한다.
   `GPSVersionID`(BYTE×4)와 `0x8825` GPSInfo 포인터(LONG×1)는 **인라인이 아니면 안 된다.**
3. **2-패스 레이아웃** — `0x8825` 의 값은 GPS IFD 의 TIFF 기준 절대 오프셋이라 IFD0 크기가
   정해진 뒤에야 안다. `gps_off = 8 + ifd0_size`, `data_off = gps_off + gps_ifd_size`,
   두 IFD 가 **하나의 꼬리 데이터 블록**을 공유한다.

그 밖의 함정:

- ⚠️`_insert_app1` 은 APP1 을 **하나만** 끼운다 → GPS 는 반드시 **같은 `_exif_app1` 출력 안**에
  들어가야 한다. 별도 세그먼트로 붙이면 안 된다.
- ⚠️**RATIONAL 은 부호가 없다.** 위/경도는 절댓값 + Ref 문자(N/S/E/W), 고도는 절댓값 +
  `GPSAltitudeRef`(0=해수면 위, 1=아래).
- ⚠️도分秒 변환은 **정수 산술**로 쪼갠다(`_dms_rational`). 부동소수로 나눈 뒤 반올림하면 초가
  60.0000 으로 밀려 `31° 59' 60"` 같은 값이 나온다.
- 꼬리 데이터는 짝수 경계로 패딩한다(홀수 길이 ASCII 뒤 RATIONAL 정렬).

### 실측

| 항목 | 값 |
|---|---|
| 좌표 왕복 오차 | Δ ≤ 2.8e-14 도 (서울/시드니/뉴욕/극단값 4케이스) |
| 파일 증가분 | **+178 B** (Software+DateTime 만인 파일 대비) |
| 픽셀 | **비트 동일** — 인코딩이 끝난 바이트에 끼우므로 인코더 호출이 안 바뀐다 |
| 외부 리더 | macOS **ImageIO**(미리보기·Photos·Finder 가 쓰는 엔진)가 `{GPS}` 딕셔너리로 읽음 |

## 왜 JPEG 만인가

- **PNG**: EXIF 를 담을 표준 자리가 사실상 없다. `tEXt` 로 넣어 봐야 읽는 뷰어가 드물어
  '남겼다'는 착각만 준다.
- **TIFF**: Qt 가 뱉은 IFD0 을 **파싱해 GPS IFD 를 덧붙이고 엔트리 수·오프셋을 다시 쓰는**
  진짜 TIFF 리라이터가 필요하다(JPEG 처럼 세그먼트를 끼우는 수준이 아니다). `tifffile` 을
  들이면 16bit TIFF 미지원도 같이 풀리지만 별건이다.

UI 가 "GPS is written to JPEG exports only" 를 명시한다.

## GPX 트랙 매칭 (`gpx.py`)

휴대폰으로 기록한 트랙과 **EXIF 촬영시각**을 맞춰 한 롤을 한꺼번에 태깅한다. 표준 라이브러리
(`xml.etree`)만 쓴다 — 새 의존성 없음.

★⚠️**EXIF 촬영시각에는 시간대가 없다**(`DateTimeOriginal` 은 카메라 로컬시다). 그래서 UI 가
**UTC 오프셋**과 **시계 오차 보정(초)** 을 받는다. 이 값 없이는 매칭이 원리적으로 성립하지
않는다 — 추정하지 않는다.

- 앞뒤 두 점 사이는 **선형 보간**(고도 포함).
- 가장 가까운 점이 `DEFAULT_TOLERANCE_SEC`(120초)보다 멀면 **None** — 로거를 껐던 구간이나
  다른 날 사진에 엉뚱한 좌표를 붙이는 것보다 비어 있는 편이 낫다. **못 맞춘 사진은 건드리지
  않는다.**
- ⚠️두 점의 경도 차가 180도를 넘으면(날짜변경선) 보간하지 않고 **가까운 점을 그대로 쓴다** —
  179 → −179 를 지구 반 바퀴로 읽는 것을 막는다.
- `<trkpt>` 와 `<wpt>` 를 모두 읽고, 네임스페이스는 **태그 로컬명**으로 비교한다(GPX 1.0/1.1).
- 소수 초 자릿수가 7 이상인 로거를 위해 6자리로 잘라 파싱하는 폴백이 있다.

### 실측 (합성 트랙 5점, 60초 간격)

| 촬영시각(카메라 로컬, UTC+9) | 결과 |
|---|---|
| 트랙 첫 점과 동시 | 첫 점 그대로 |
| 점 사이 30초 | 정확히 중간값으로 보간(고도 포함) |
| 트랙 밖 60초 | 끝점(허용 범위 안) |
| 트랙 밖 6분 | **None** |
| 하루 전 | **None** |
| 시간대를 UTC+0 으로 잘못 주면 | **None** (틀린 오프셋은 조용히 통과하지 않는다) |

## 장소 검색 (지오코딩)

번들 OSM 플러그인의 **`GeocodeModel`(Nominatim)** 을 그대로 쓴다 — **새 의존성 0**.
검색어를 넣고 Enter/`Go` 를 누르면 후보를 최대 6개 보여주고, 고르면 **지도가 그리로 이동하고
초안 핀도 그 자리에 놓인다**(저장은 여전히 `Apply` 를 눌러야 한다 — 아래 규율).

★⚠️**질의는 Enter/버튼으로만 한다.** Nominatim 사용 정책이 식별 가능한 User-Agent(위
`osm.useragent`)와 **초당 1회 이하**를 요구하므로, 타건마다 부르는 자동완성은 정책 위반이다.

실측(헤드리스, 실제 질의): `"Eiffel Tower, Paris"` → 1건, `48.85826, 2.29450`;
`"Gyeongbokgung Palace, Seoul"` → `37.57975, 126.97668`. 선택 후 center 가 그리로 이동하고
초안이 같은 좌표로 서며 **사진의 저장값은 그대로**인 것까지 확인했다.

역지오코딩(좌표 → 지명)은 여전히 **안 한다** — 우리가 쓰는 EXIF 태그는 좌표뿐이라 지명은
export 파일에 남지도 않는다.

## UI 규율 — 클릭은 저장이 아니다

★**지도 클릭은 '초안(draft)'만 바꾼다.** `Apply` 를 눌러야 사진에 붙는다.

처음에는 클릭이 곧 `controller.setGps` 였는데, 그러면 실수로 누른 좌표가 **그대로 사이드카에
저장되고 undo 스텝까지 쌓인다**("사용자가 실수로 클릭한 부분이 바로 반영되어버리면 혼란"). 지금은

- 지도 클릭 · 좌표칸 입력 → **초안**(`draftLat`/`draftLon`/`hasDraft`)만 바뀐다
- `Apply to this photo` → `controller.setGps` (= 사이드카 저장 + undo 스텝 1개)
- `Apply to N checked` → 초안 좌표를 체크된 사진들에 쓴다
- 초안이 저장값과 다르면 상태 줄이 **"Not applied yet"** 를 띄운다
- 사진을 넘기면 `gpsChanged` 로 초안이 새 사진 값에 다시 맞춰진다(= 초안 폐기)

★⚠️**`map.center` 를 좌표에 바인딩하지 말 것.** 바인딩하면 클릭할 때마다 지도가 핀을 가운데로
끌어와 화면이 튄다("매우 불편함"). 핀을 찍는 것과 시야를 옮기는 것은 다른 동작이다 —
시야 이동은 `recenter()` 를 **명시적으로 부를 때만**(지도를 처음 켤 때 · 탭에 들어올 때 ·
사진이 바뀌어 그 사진의 위치가 있을 때) 일어난다.

## UI

**Location 탭(`Ctrl+6`)** — 지도 픽커 + 좌표칸 + 체크한 여러 장 일괄 적용 + GPX 로드.

- ★**`import QtLocation` 을 `ui/LocationMap.qml` 한 파일에 가둔다.** `Main.qml` 최상단에 두면
  프리즌 빌드에서 모듈이 빠졌을 때 **앱 전체가 안 뜬다**("EditedBadge is not a type" 과 같은
  부류). 가둬 두고 `Loader` 로 늦게 켜면 최악이 '탭이 비어 있음'으로 끝난다.
- `Loader` 는 **탭을 처음 열 때** 켜진다 — 앱 시작 때 타일을 받으러 나가지 않는다.
- 좌표칸은 지도가 주 입력인데도 남겨 뒀다 — **오프라인에서 타일이 안 뜰 때 유일한 폴백**이다.

### ⚠️핀은 `MapView` 의 자식으로 선언하면 안 보인다

`MapView` 는 `Map` 을 감싼 **평범한 `Item`** 이다. 그 안에 적은 `MapQuickItem` 은 그 Item 의
자식으로 들어갈 뿐 **지도의 `mapItems` 에 등록되지 않는다** — 클릭 좌표는 바뀌는데 핀만 안
그려진다(실제로 그랬다). `view.map.addMapItem(pinItem)` 으로 등록한다(`mapItems` 0 → 1 확인).

⚠️핀 그림에 **`Canvas` 를 sourceItem 으로 쓰지 않는다** — 지도 아이템은 소스를 텍스처로 굽는데
Canvas 의 첫 paint 가 그보다 늦어 빈 텍스처가 남을 수 있다. `Rectangle` 도형으로 그린다.

### ⚠️파이썬의 `None` 은 QML 에서 `undefined` 다

`gpsAlt`(`QVariant`, 고도 없음 = `None`)를 QML 에서 `!== null` 로만 검사했다가
`toFixed of undefined` 로 터졌다. 더 조용한 쪽이 문제다: **`JSON.stringify` 는 값이
`undefined` 인 키를 통째로 버려서** 사이드카·undo 스냅샷에서 `gpsAlt` 가 소리 없이 사라진다.
`win.gpsAltOrNull()` / 패널의 `photoAlt` 로 정규화해 쓴다.

### ⚠️셀렉터 레일의 인덱스 함정

레일은 원래 **Repeater 의 `index`** 로 패널을 정했는데 Wallpaper 항목이 `.env` 플래그로
조건부다. 뒤에 그냥 append 하면 Wallpaper 유무에 따라 Location 의 index 가 4↔5 로 **밀린다**
(StackLayout 페이지 번호는 5로 고정인데). → 모델 각 항목에 **명시적 `panel:` 필드**를 넣고
`modelData.panel` 로 판정/대입한다.

## 타일 소스 — ★Qt 기본 설정을 쓰면 "API Key Required" 가 뜬다

Qt 의 OSM 플러그인은 시작할 때 `maps-redirect.qt.io` 에 제공자를 물어본다. **실측 결과 전 타입이
Thunderforest 로 리디렉트된다** — `street` 도 마찬가지다:

```
$ curl -sL http://maps-redirect.qt.io/osm/5.8/street
{ "UrlTemplate" : "http://a.tile.thunderforest.com/atlas/%z/%x/%y.png", ... }
```

Thunderforest 는 키가 필요한 상용 서비스이고, 키 없는 요청은 **IP 단위 허용량**이 있다. 넘으면
지도 대신 **"API Key Required" 워터마크 타일**이 온다(사용자 보고). 그래서:

```qml
PluginParameter { name: "osm.mapping.providersrepository.disabled"; value: true }
PluginParameter { name: "osm.mapping.custom.host"; value: "https://tile.openstreetmap.org/" }
```

⚠️**리디렉트를 껐다고 끝이 아니다.** 하드코딩 폴백 제공자(Street Map 등)가 그대로 남아 있고
`activeMapType` 은 여전히 그쪽이다 → **`Custom URL Map`(style 100)으로 직접 바꿔야 한다**
(`useCustomTiles()`).

⚠️`supportedMapTypes` 는 **비동기로 채워진다.** `Component.onCompleted` 에서 한 번만 부르면
목록이 비어 있어 놓친다(실측: 타일 요청 0건). 그리고 **`map.onSupportedMapTypesChanged:` 같은
점 표기 시그널 핸들러는 조용히 안 걸린다**(경고도 안 난다) — `Connections` 로 명시할 것.

★⚠️**타일 디스크 캐시가 문제를 살려 둔다.** 리디렉트 시절 받은 워터마크 타일이
`~/Library/Caches/QtLocation`(Windows 는 로컬 캐시)에 저장돼 있어, 타일 소스를 고쳐도 계속 그
그림이 보인다 — 고친 뒤 **네트워크 요청이 0건**이었던 것이 그 증거다. **앱 전용 캐시 폴더**
(`osm.mapping.cache.directory` = `controller.mapCacheDir`)를 써서 오염된 캐시를 아예 읽지 않는다.
상한 20 MiB.

### 실측(로컬 타일 서버로 확인)

`tileHost` 만 로컬 서버로 돌려 실제 `LocationMap.qml` 이 보내는 요청을 받아 봤다:

```
GET /13/6985/3171.png
UA: FilmRawstery/1.11.1 (+https://github.com/lim8701/FilmRawstery)
```

정확한 `{z}/{x}/{y}` 경로와 **우리 User-Agent** 가 나가고, 앱 캐시 폴더에 파일이 쌓인다.

### OSM 본 서버를 쓰는 것에 대해

OSM 타일 서버는 **가벼운 사용**을 전제로 한 공용 자원이다. 사진 한 장에 지도를 몇 번 보는
용도는 그 범위지만, **사용자가 늘면 자기 키를 쓰는 제공자로 갈아 끼우는 것이 정도다.**
`LocationMap.qml` 의 `tileHost` 가 그 자리다(플러그인 파라미터라 **생성 시점에만** 유효).

## 네트워크·프라이버시

- ★⚠️**OSM 타일 정책은 식별 가능한 User-Agent 를 요구한다.** Qt 기본값
  ("Qt Location based application")으로 배포하면 정책 위반이고 차단될 수 있다 →
  `PluginParameter { name: "osm.useragent"; value: "FilmRawstery/<ver>" }`.
  ⚠️바인딩에 폴백을 둘 것 — 컴포넌트 파괴 시점에 `controller` 가 null 이 되어 TypeError 가 났다.
- Qt 의 OSM 플러그인은 시작 시 `maps-redirect.qt.io` 에서 제공자 목록을 받는다(실측: 정상 해석,
  satellite 만 upstream 에서 비활성). 오프라인이면 타일이 비고, 그때 좌표칸이 폴백이다.
- 위치는 **레시피(.frpreset)와 룩 복사에 절대 실리지 않는다**(위 '설계의 축').
- 사용자가 명시적으로 붙인 사진만 export 에 좌표가 나간다.

## 패키징

- `FilmRawstery.spec` 의 `excludes` 에서 `PySide6.QtPositioning`/`QtLocation` **제거**.
- ⚠️그것만으로는 부족하다 — **QML 모듈과 geoservices 플러그인은 데이터**라 파이썬 import
  탐지에 안 걸린다. `qml/QtLocation` · `qml/QtPositioning` · `qml/Qt/labs/animation`
  (`MapView.qml` 이 import 한다) · `plugins/geoservices` 를 `datas` 에 명시했다.
  빠지면 **소스 실행은 멀쩡하고 배포본에서만** 지도가 빈다.
- ★⚠️**경로를 `PySide6/Qt/...` 로 박지 말 것 — 그건 macOS/Linux 레이아웃이다.** Windows 휠은
  `PySide6/qml`·`PySide6/plugins` 로 **중간 `Qt/` 가 없다.** 박아 두면 `isdir` 이 False 가 되어
  **아무 말 없이 하나도 수집하지 않고**, 윈도우 배포본만 지도가 빈다. `QLibraryInfo` 로 실제
  경로를 얻고 목적지는 PySide6 패키지 기준 상대경로를 그대로 쓴다. 못 찾으면 **빌드를 세운다**
  (조용히 빠지는 것이 최악이다).
- 실측 데이터 1.29MB + Qt 프레임워크(QtLocation 3.2M / QtPositioning 1.1M /
  QtPositioningQuick 0.7M) ≈ **6MB**. .app 457MB 대비 무시할 수준이다.
  ⚠️218MB 짜리는 **QtWebEngine** 이고 여기서 쓰지 않는다.

## 기각한 것

- **역지오코딩(좌표 → 지명)**: 지명은 export 파일에 남지도 않는다(우리가 쓰는 태그는
  좌표뿐). 정방향 검색(지명 → 좌표)은 '지도를 찾아가는' 실제 용도가 있어 넣었지만, 역방향은
  보여줄 자리가 없다.
- **타건 자동완성 검색**: Nominatim 정책(초당 1회 이하) 위반이다. Enter/버튼 질의만 한다.
- **PNG/TIFF 지원**: 위 '왜 JPEG 만인가'.
- **원본 RAF 수정**: 논외. 사이드카가 사진과 함께 이동한다.
