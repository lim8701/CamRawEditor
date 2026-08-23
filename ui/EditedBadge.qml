import QtQuick

// '이 사진은 편집본이 있다' 배지 — 썸네일 모서리에 붙는 **앱 아이콘**(타이틀바와 같은 소형 아트,
// R + 스프로킷 홀 3개). 도안은 `packaging/make_icon.py --badge` 가 굽는다(assets/icons/edited.png,
// 48px). 아이콘을 다시 만들면 그 명령도 같이 돌릴 것 — .ico/.icns 와 같은 원본에서 나온다.
//
// 예전에는 **파일명 글자색만** 앰버로 바꿨는데, 12px 글자의 색 하나뿐이라 잘 안 보인다는
// 피드백이 왔다(색각 이상이면 사실상 안 보인다). 같은 화면의 좋아요(♥)·짝 JPEG(+JPG)은
// 이미 배지인데 편집 표시만 그 언어를 안 쓰고 있었다.
//
// ⚠️도안을 두 번 바꿔 여기까지 왔다 — ①`FilmStrip` 스프로킷 홀 축소판: 이 크기에서 구멍 3개가
//   가로로 놓이면 **말줄임표(…)로 읽힌다** ②조정 슬라이더(Edit 패널 아이콘) 축소판: 뜻은 맞지만
//   여전히 어색하다. 결론은 **앱 아이콘 그대로**(사용자 지정). 새로 그리지 말 것.
// ⚠️글리프(✎ 등)는 쓰지 않는다 — 폰트에 없으면 두부(□)가 뜨는데 확인할 방법이 없다.
Image {
    id: root
    property string path: ""
    // 썸네일이 아직 안 뜬 동안 감추기 위한 게이트. ⚠️필요하다 — 위치를 `paintedWidth/Height`
    // 로 잡는데 로드 전엔 그 값이 0 이라 배지가 **칸 한가운데**로 간다(사용자 보고).
    property bool ready: true

    visible: {
        controller.editsRevision            // 저장/폴더 변경 시 재평가(목록의 파일명 색과 동일 의존)
        return ready && path !== "" && controller.hasEdits(path)
    }
    // ⚠️♥(font.pixelSize 14) 와 **같은 크기**여야 한 모서리 안에서 따로 놀지 않는다(사용자 보고).
    width: 14
    height: 14
    // 48px 원본을 축소해 그린다(업스케일 없음 — HiDPI 배율에서도 선명).
    // 스프로킷 홀은 투명 컷아웃이라 밝은 썸네일에서는 사진이 비친다(아이콘 원래 의도).
    source: "../assets/icons/edited.png"
    smooth: true
    mipmap: true
}
