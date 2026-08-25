# macOS 패키징 (.app + DMG)

> `CLAUDE.md` 에서 분리했다(그 파일은 매 세션 컨텍스트에 통째로 올라가므로 가볍게
> 유지한다). CLAUDE.md 에는 요약과 이 문서 포인터만 남아 있다.

`packaging/build_mac.sh` = `build.ps1` 의 mac 대응물(앱 종료 → 클린 → PyInstaller → 개별
재서명 → 다른 디렉터리 스모크 → 선택적 공증 → DMG). 산출물은 `dist/FilmRawstery.app` 과
`dist/FilmRawstery-v<ver>-macos-arm64.dmg`(드래그 설치용 `/Applications` 심볼릭 포함).
`FilmRawstery.spec` 은 `IS_MAC` 로 분기하고 버전은 `main.py` 의 `APP_VERSION` 을 파싱한다
(mac 용 수동 동기화 지점을 만들지 않는다 — `version_info.txt` 는 Windows 전용).
실측(v1.9.0, M1 Pro): **.app 457MB / DMG 169MB**, 빌드 약 1분.
빌드 도구는 런타임 의존이 아니라 `requirements.txt` 에 없다 — venv 에 따로 넣는다:
`pip install pyinstaller pillow`(pillow 는 아이콘 컨테이너 생성용).

- ⚠️**`excludes` 는 macOS 에서 Qt 프레임워크를 못 막는다.** PySide6 훅이 `PySide6/Qt/lib` 를
  **120개 전량** 수집한다(Windows 는 포함된 확장 모듈의 의존 DLL 만 수집되므로 excludes 로
  충분하다). 첫 빌드 676MB 중 Qt/lib 이 322MB 였다. `otool -L` 로 수집 바이너리 523개를 전수
  검사해 **아무것도 링크하지 않는 프레임워크 47개(250MB)** 를 찾았고 그중 **QtWebEngineCore
  하나가 218MB(87%)** 다. WebEngine/WebView 계열은 자기 계열 + 자기 qml 플러그인만 참조하는
  것이 확인돼 그 계열만 제거한다(676→457MB, dangling 0). **남은 44개는 합쳐 32MB 뿐이라
  건드리지 않는다** — QtSql 은 QtQmlLocalStorage 가, QtMultimedia 는 `Qt/plugins` 의 미디어
  플러그인이 링크하므로 지우면 실행이 깨질 수 있고 이득이 없다. 재검증:
  `find dist/FilmRawstery.app -name '*.dylib' -o -name '*.so' | xargs -n1 otool -L | grep '@rpath/QtWebEngine'`
- ⚠️**cv2 의 ffmpeg 제외 트릭을 mac 에 복사하면 안 된다** — mac 휠은 `cv2.abi3.so` 가
  `@loader_path/.dylibs/libavcodec…` 를 **로드타임 링크**한다(Windows 는 videoio 지연 로드).
  지우면 `import cv2` 자체가 실패한다. 그래서 mac 은 cv2 119MB 를 그대로 안고 간다(두 번째로
  큰 덩어리이고 줄일 방법이 없다).
- ⚠️**하한은 휠 태그가 아니라 `minos` 실측으로 정한다 — 현재 macOS 15.** 번들 Mach-O 517개에
  `vtool -show-build` 를 돌린 최대값이 하한이고, 그 값을 `LSMinimumSystemVersion` 에 적는다.
  낮게 적으면 지원 범위가 늘지 않고 **Finder 가 실행을 허용한 뒤 dyld 오류로 죽는다**.
  실측 분포: PySide6 바인딩(`QtCore.abi3.so`/`libpyside6`/`libshiboken6`) **15.0** ·
  Homebrew libpython·libmpdec 15.0 · numpy/scipy/onnxruntime 14.0 · Qt 프레임워크 13.0 ·
  cv2 13.0 · rawpy 11.0.
  ⚠️**휠 태그는 거짓말이다** — PySide6 6.11.2 는 `macosx_13_0_universal2` 태그인데 minos 15.0
  이다(Qt CI 가 6.10 부터 macOS 15 에서 배포 타깃 없이 빌드. shiboken6 실측 **6.9.1=12.0 /
  6.10.0=15.0 / 6.11.2=15.0**). 처음엔 'Homebrew 파이썬(타깃 15) 때문'이라 보고 python.org
  파이썬 교체를 계획했는데, **측정해 보니 PySide6 가 진짜 원인**이어서 파이썬만 바꾸면 하한이
  안 내려간다. 하한을 14 로 내리려면 **PySide6 6.9.x 고정 + python.org 파이썬** 둘 다 필요하고,
  Qt 버전이 Windows 빌드와 갈라지는 대가를 치를 값인지 판단해야 한다(현재는 15 를 받아들였다).
- ⚠️**arm64 전용 빌드만 현실적**이다. PySide6 만 universal2 이고 numpy/scipy/onnxruntime/
  opencv/rawpy 는 arm64 전용 휠이다. spec 의 `target_arch="arm64"` 가 Qt 프레임워크를 thin
  시킨다(Qt/lib 322→103MB). Intel 지원은 `arch -x86_64` 별도 venv·별도 DMG 가 필요.
- ⚠️**서명/공증**: 키체인의 GENORAY `Apple Distribution` 인증서로는 **외부 배포 공증이 안 된다**
  (App Store/사내용). 필요한 것은 **`Developer ID Application`**(개인 등록 권장 — 후원 QR 이
  있는 개인 프로젝트를 회사 인증서로 서명하면 배포 주체가 회사가 된다). 기본 ad-hoc 서명은
  `codesign --verify` 는 통과하지만 `spctl` 은 **rejected** 이고, **macOS 15+ 는 Ctrl+클릭
  '열기' 우회가 제거**돼 사용자가 시스템 설정 › 개인정보 보호 및 보안에서 허용해야 한다.
  공증까지: `build_mac.sh --sign "Developer ID Application: …" --notarize`
  (`xcrun notarytool store-credentials` 로 키체인 프로필 저장 선행, 엔타이틀먼트는
  `packaging/entitlements.plist`). ⚠️zip 은 **`ditto -c -k --keepParent`** 로 만들어야 서명이
  보존된다.
- **Hardened Runtime 은 검증됨** — 공증에 필수인 `--options runtime` +
  `packaging/entitlements.plist`(library validation off, dyld 환경변수 허용)를 ad-hoc 서명에
  걸어도(`flags=0x10002 adhoc,runtime`) 앱이 정상 실행된다(14초 스모크, 라이브러리 검증/dyld
  오류 0). PyInstaller 앱이 하드닝에서 죽는 경우가 흔한데 이 앱은 해당 없음 — 인증서를 산 뒤에
  발견하지 않도록 미리 재 봤다. ad-hoc 에도 같은 플래그를 걸 수 있으므로 엔타이틀먼트를
  바꿨으면 이 방법으로 먼저 확인할 것.
- ★**배포 결정(2026-08): ad-hoc 서명 + 공증 없음, GitHub Releases 의 experimental 자산으로.**
  공증에는 Apple Developer Program $99/년 이 필수이고 무료 경로가 없다(무료 Apple ID 의 Personal
  Team 은 Apple Development 인증서만 발급 — Developer ID 발급·notarytool 사용 불가). mac 사용자
  규모를 모르는 상태에서 먼저 지출하지 않고, **다운로드 수·반응을 보고 그 해에 공증으로 승급**하는
  순서를 택했다. 타임스탬프된 서명과 공증 티켓은 멤버십이 끝나도 유효하므로 '공개할 해에만' 내는
  것이 가능하다. ⚠️App Store 는 선택지가 아니다 — **PySide6/Qt LGPLv3 · LibRaw LGPL-2.1 의 재링크
  조항**(NOTICE.txt 가 onedir 로 충족시키는 그 조항)을 스토어에서는 보장할 수 없고(Qt Company 도
  상용 라이선스를 요구), App Sandbox 개조와 후원 화면 제거(심사 3.1.1: IAP 외 외부 결제 유도 금지,
  자선 예외는 등록 비영리 한정)까지 얹히면 다른 제품이 된다.
- ⚠️**mac DMG 를 올릴 때 GitHub 릴리스를 pre-release 로 표시하지 말 것** — 인앱 업데이터
  (`Controller._release_candidates`)가 `prerelease`/`draft` 를 건너뛰므로 **Windows 사용자 전체의
  업데이트 알림이 조용히 멈춘다**. '실험적'은 릴리스가 아니라 **자산 설명**에 적는다.
  무서명 배포는 릴리스 본문에 **차단 해제 절차 + SHA256** 이 반드시 함께 가야 한다(없으면 사용자가
  앱을 아예 열 수 없다). 절차 원문은 README 의 macOS 절, 릴리스 본문 템플릿은 release 스킬 5.5.
- ⚠️**스모크 테스트는 `exec` 로 띄울 것** — `( cd /tmp && app ) &` 의 `$!` 는 서브셸 PID 라
  kill 이 앱을 남기고, 다음 실행이 단일 인스턴스 가드에 걸려 '이미 실행 중'으로 즉시 종료된다
  (실측으로 걸렸다). `build.ps1` 이 개발 인스턴스까지 죽이는 것과 같은 계열의 함정.
- **.app 레이아웃**: PyInstaller 6 은 바이너리를 `Contents/Frameworks`, 데이터를
  `Contents/Resources` 에 두고 **교차 심볼릭**(실측 231/238개)을 만든다. `sys._MEIPASS` 는
  Frameworks 를 가리키므로 `main.app_base()` 는 **수정 없이 동작**한다(`shaders/`·`luts/`·`ui/`
  가 심볼릭으로 해석됨). `contents_directory="lib"` 는 .app 에서는 무효.
  ⚠️`_feature_flags()` 의 `.env` 는 `sys.executable` 옆 = **`FilmRawstery.app/Contents/MacOS/`
  안**이다(앱 옆에 두면 안 읽힌다). 환경변수 `FILMRAWSTERY_*` 는 그대로 동작.
- **아이콘**: `packaging/make_icon.py --icns` (1024 마스터 → `iconutil`). 도형 상수는 `k=size/256`
  스케일이고 **256px 렌더가 리팩터 전과 픽셀 단위로 동일함을 검증**했다(diff 0바이트 → Windows
  `.ico` 무영향). 128px 이상에만 **사방 여백**을 준다(Apple 그리드 824/1024 = 80.5%; 16/32 에
  주면 글자가 뭉갠다). 레터마크 폰트는 mac 에서 **Arial Black**(Segoe UI Black 은 Windows 외
  재배포 불가) — 글자 모양이 미세하게 다르므로 두 아이콘을 완전히 맞추려면 Windows 에서
  `--icns` 까지 함께 생성할 것.
- **검증된 것**(v1.9.0 시험 빌드): Metal RHI 정상(`QRhi backend Metal / Apple M1 Pro`,
  셰이더 오류 0 — 커밋된 `.qsb` 의 MSL 12 가 재컴파일 없이 로드됨), 메인 창
  `CAMetalLayer 3456x1946 scale 2.00`(Retina DPR 2), 일반 이미지 로드 경로 통과,
  **읽기 전용 DMG 볼륨에서 실행 성공**(번들에 쓰지 않음), 번들에 ARR LUT·models 없음.
- **슬립 방지는 IOKit 어서션**(`_mac_keep_awake`): export 중 `PreventUserIdleSystemSleep` 을
  홀드한다(`pmset -g assertions` 에 `"FilmRawstery export"` 로 보인다). ⚠️Windows 와 달리
  **디스플레이는 붙잡지 않는다** — 화면이 꺼져도 프로세스가 계속 돌아 export 가 멈추지 않으므로
  Modern Standby 때문에 화면까지 붙잡아야 하는 Windows 와 이유가 성립하지 않는다.
  ⚠️`caffeinate` 자식 프로세스를 쓰지 않는다 — 어서션은 프로세스 귀속이라 앱이 강제 종료돼도
  커널이 회수하지만, 자식은 살아남아 절전을 영영 막을 수 있다. 뚜껑을 닫으면 그래도 잔다.
- **남은 mac 이슈**(패키징 아님): 폰트 추가 대화상자가 `C:/Windows/Fonts` 를 연다 ·
  `QFileOpenEvent` 핸들러가 없어 Finder 더블클릭/Dock 드롭으로 사진이 안 열린다(argv 만 처리) ·
  `pipeline.py` serif 후보에 mac 폰트가 없어 시작 시 **폰트 별칭 채우기 105ms**(missing
  "Constantia").

## 크로스 플랫폼 렌더 일치(`xplat_check.py`)

양쪽에서 `python xplat_check.py <상대 JSON>` 을 돌려 19케이스 해시를 비교한다. **1.10.1 시점
실측: 17/19 일치**, 나머지 둘은 코드 문제가 아니라 환경 차이다 — 재조사하지 말 것.

| 케이스 | 차이 | 원인 |
|---|---|---|
| `vignette` | **sha 만** 다르고 min/max/mean/std/shape 는 전부 동일 | 부동소수 반올림(x86_64 vs arm64, numpy 2.4.6 vs 2.5.2). 몇 픽셀이 ±1코드 |
| `datestamp` | mean 176.382 vs 176.399(Δ0.017/255), std 63.691 vs 63.681 | 폰트 래스터라이저가 다르다(Windows vs CoreText). `render_sprite` 가 `QFont`/`QFontMetrics` 로 글자를 굽는 이상 피할 수 없다 |

⚠️`xplat_check.py` 는 `pipeline`/`lut`/`image_loader` 만 import 한다 — `main.py`/QML 변경은 이
결과에 영향을 주지 않으므로, 불일치가 늘면 **현상 파이프라인 쪽**을 볼 것.
