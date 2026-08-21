#!/usr/bin/env bash
# Film Rawstery — macOS 패키징(.app + DMG). build.ps1 의 mac 대응물.
#
# 사용법:
#   packaging/build_mac.sh                          # ad-hoc 서명 + DMG (기본)
#   packaging/build_mac.sh --sign "Developer ID Application: 이름 (TEAMID)"
#   packaging/build_mac.sh --sign "..." --notarize   # 공증 + 스테이플까지
#   packaging/build_mac.sh --no-dmg --smoke 6
#
# 하는 일: 실행 중인 앱 종료 -> dist 정리 -> PyInstaller -> (재)서명 -> 다른 디렉터리에서
#          스모크 테스트 -> DMG. 어느 단계든 실패하면 즉시 중단한다.
#
# ⚠️배포 빌드는 **python.org 파이썬**으로 만든 venv 를 쓸 것 — Homebrew 파이썬은 호스트 OS
#   배포 타깃으로 빌드돼 있어(sysconfig.get_platform() 확인) 결과물이 구버전 macOS 에서
#   dyld 오류로 죽는다. VENV 환경변수로 다른 venv 를 지정할 수 있다.
# ⚠️공증에는 **Developer ID Application** 인증서가 필요하다(App Store 용 Apple Distribution
#   인증서로는 안 된다). notarytool 자격증명은 `xcrun notarytool store-credentials` 로
#   키체인 프로필("FilmRawstery-notary")에 저장해 둔다.
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJ"                    # spec 이 상대경로(luts/shaders/fonts)를 쓴다
VENV="${VENV:-$PROJ/.venv}"
PY="$VENV/bin/python"
APP="$PROJ/dist/FilmRawstery.app"
SMOKE=10
IDENTITY="-"                  # 기본 ad-hoc
NOTARIZE=0
MAKE_DMG=1
NOTARY_PROFILE="${NOTARY_PROFILE:-FilmRawstery-notary}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sign) IDENTITY="$2"; shift 2 ;;
    --notarize) NOTARIZE=1; shift ;;
    --no-dmg) MAKE_DMG=0; shift ;;
    --smoke) SMOKE="$2"; shift 2 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

[[ -x "$PY" ]] || { echo "venv 파이썬 없음: $PY" >&2; exit 1; }
VER="$(sed -n 's/^APP_VERSION = "\([^"]*\)".*/\1/p' main.py | head -1)"
[[ -n "$VER" ]] || { echo "main.py 에서 APP_VERSION 을 못 읽었습니다" >&2; exit 1; }
DMG="$PROJ/dist/FilmRawstery-v${VER}-macos-arm64.dmg"

echo "[1/6] 실행 중인 앱 종료 + dist 정리..."
# 개발 인스턴스(python main.py)도 함께 종료 — 살아 있으면 단일 인스턴스 소켓을 잡고 있어
# 스모크 테스트가 산출물과 무관하게 항상 실패한다(build.ps1 과 같은 이유).
pkill -f "FilmRawstery.app/Contents/MacOS/FilmRawstery" 2>/dev/null || true
pkill -f "[Pp]ython.* main\.py" 2>/dev/null || true
sleep 0.5
rm -rf "$PROJ/build" "$PROJ/dist"

echo "[2/6] 빌드(PyInstaller)..."
"$PY" -m PyInstaller FilmRawstery.spec --noconfirm --log-level=WARN
[[ -d "$APP" ]] || { echo ".app 이 생성되지 않았습니다" >&2; exit 1; }
# COLLECT 중간 산출물(.app 과 같은 내용의 onedir) 제거 — 디스크 절반 절약
rm -rf "$PROJ/dist/FilmRawstery"

echo "[3/6] 서명(${IDENTITY})..."
# 안쪽부터 바깥쪽으로 개별 서명한다(--deep 은 Apple 이 권장하지 않는다).
SIGN_ARGS=(--force --timestamp --sign "$IDENTITY")
if [[ "$IDENTITY" != "-" ]]; then
  SIGN_ARGS+=(--options runtime --entitlements "$PROJ/packaging/entitlements.plist")
else
  SIGN_ARGS=(--force --sign "-")     # ad-hoc: 타임스탬프 서버/하드닝 없음
fi
find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" \) -type f -print0 |
  xargs -0 -n1 codesign "${SIGN_ARGS[@]}" 2>/dev/null
find "$APP/Contents/Frameworks" -maxdepth 1 -name "*.framework" -print0 |
  xargs -0 -n1 codesign "${SIGN_ARGS[@]}"
codesign "${SIGN_ARGS[@]}" "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "[4/6] 스모크 테스트(다른 디렉터리에서 ${SMOKE}초)..."
ERR="$(mktemp)"
# ⚠️exec 필수 — 없으면 $! 가 서브셸 PID 라 아래 kill 이 앱을 남긴다(실측: 다음 실행이
#   단일 인스턴스 가드에 걸려 '이미 실행 중'으로 즉시 종료됐다).
( cd /tmp && exec "$APP/Contents/MacOS/FilmRawstery" >"$ERR" 2>&1 ) &
PID=$!
sleep "$SMOKE"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "  스모크 실패 — ${SMOKE}초 전에 종료됨. 출력:" >&2
  tail -30 "$ERR" >&2
  exit 1
fi
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true
pkill -f "FilmRawstery.app/Contents/MacOS/FilmRawstery" 2>/dev/null || true   # 안전망
echo "  OK (출력 마지막 3줄)"; tail -3 "$ERR" | sed 's/^/    /'

if [[ "$NOTARIZE" == 1 ]]; then
  echo "[5/6] 공증(notarytool, 수 분 소요)..."
  ZIP="$(mktemp -d)/FilmRawstery.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"        # ⚠️ditto 여야 서명이 보존된다
  xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
else
  echo "[5/6] 공증 생략(--notarize 아님)."
fi

if [[ "$MAKE_DMG" == 1 ]]; then
  echo "[6/6] DMG..."
  STAGE="$(mktemp -d)/Film Rawstery"
  mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"     # 드래그 설치용
  rm -f "$DMG"
  hdiutil create -volname "Film Rawstery" -srcfolder "$STAGE" \
                 -fs HFS+ -format UDZO -quiet "$DMG"
  rm -rf "$STAGE"
  # 사용자가 실제로 받는 것은 DMG 다 — 그것도 서명·공증·스테이플해야 다운로드 직후
  # (오프라인 포함) 경고 없이 열린다. .app 은 위에서 이미 스테이플됐고, 여기서 DMG 를 한 번 더
  # 공증한다(라운드트립 2회. Apple 권장 흐름이고 각각 보통 수 분).
  if [[ "$IDENTITY" != "-" ]]; then
    codesign --force --timestamp --sign "$IDENTITY" "$DMG"
    if [[ "$NOTARIZE" == 1 ]]; then
      xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
      xcrun stapler staple "$DMG"
      xcrun stapler validate "$DMG"
      spctl -a -vvv --type install "$DMG" || true      # 기대: accepted / Notarized Developer ID
    fi
  fi
else
  echo "[6/6] --no-dmg — DMG 생략."
fi

echo
printf 'OK  ->  %s  (%s)\n' "$APP" "$(du -sh "$APP" | cut -f1)"
[[ -f "$DMG" ]] && printf 'OK  ->  %s  (%s)\n' "$DMG" "$(du -sh "$DMG" | cut -f1)"
codesign -dv "$APP" 2>&1 | sed -n 's/^\(Authority\|Signature\|Identifier\|TeamIdentifier\)/  &/p'
