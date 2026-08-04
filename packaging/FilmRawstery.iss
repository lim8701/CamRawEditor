; Film Rawstery — Inno Setup 설치 패키지 스크립트.
; build.ps1 [5/5] 가 호출한다:  ISCC.exe /DAppVersion=<ver> packaging\FilmRawstery.iss
; 입력: ..\dist\FilmRawstery\ (PyInstaller onedir 산출물 — 빌드/스모크 통과본)
; 출력: ..\dist\FilmRawstery-v<ver>-setup.exe
; 단독 컴파일 시 AppVersion 정의가 없으면 아래 기본값을 쓴다(파일명에만 영향, 실제 배포는 build.ps1 경유).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId 는 업그레이드 식별자 — 버전이 올라가도 절대 바꾸지 말 것(바꾸면 기존 설치 위에
; 새 프로그램으로 중복 설치된다).
AppId={{B7C31C4E-2A9D-4F5B-9E62-D18A40F3C7A2}
AppName=Film Rawstery
AppVersion={#AppVersion}
AppPublisher=Film Rawstery
; setup.exe 자체의 버전 리소스(우클릭>속성>세부 정보). AppVersion 은 '프로그램 추가/제거'
; 표시용이라 이걸 따로 안 주면 setup.exe 는 0.0.0.0 으로 나온다.
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Film Rawstery
VersionInfoDescription=Film Rawstery Setup
; OS 드라이브 \Program Files\Film Rawstery (64bit 모드라 (x86) 아님). 관리자 설치(UAC 1회).
; 설치 폴더는 읽기 전용이어도 안전 — 모델 다운로드는 %LocalAppData%\FilmRawstery\models,
; 사이드카는 사진 폴더 옆이라 앱이 설치 폴더에 쓸 일이 없다(app_dirs.py).
DefaultDirName={autopf}\Film Rawstery
DefaultGroupName=Film Rawstery
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=FilmRawstery-v{#AppVersion}-setup
; onedir 가 수백 MB(PySide6+onnxruntime) — solid lzma2 로 zip 보다 작게 나온다.
Compression=lzma2
SolidCompression=yes
; 실행 중인 앱을 설치 전에 정중히 닫는다(교체 실패 방지).
CloseApplications=yes
; setup.exe 자체의 아이콘(앱 아이콘과 동일 — packaging/make_icon.py 생성물)
SetupIconFile=..\icons\app.ico
UninstallDisplayIcon={app}\FilmRawstery.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; 언어 선택 대화상자 없이 OS UI 언어로 자동 선택(한국어 Windows→한국어, 그 외→영어).
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; dist 통째 복사 — 무엇을 담을지(LUT 열거·ARR 제외·models 미포함)는 전부 PyInstaller spec 이
; 결정하고 여기서는 재열거하지 않는다(두 곳 관리 방지). build.ps1 이 스모크 통과본만 넘긴다.
Source: "..\dist\FilmRawstery\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Film Rawstery"; Filename: "{app}\FilmRawstery.exe"
Name: "{group}\{cm:UninstallProgram,Film Rawstery}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Film Rawstery"; Filename: "{app}\FilmRawstery.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\FilmRawstery.exe"; Description: "{cm:LaunchProgram,Film Rawstery}"; Flags: nowait postinstall skipifsilent
