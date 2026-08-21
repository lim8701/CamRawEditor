---
description: Analyze the project for packaging-impacting changes, update FilmRawstery.spec if needed, then build the platform distributable (Windows: onedir zip + Inno Setup installer; macOS: .app + DMG) and verify.
---

Package the Film Rawstery app. Work in this order and report concisely.

**Pick the platform by the host OS** — the spec is shared (`IS_MAC` branches inside it) but the build script and the checks differ:

| | Windows | macOS |
|---|---|---|
| build | `.\packaging\build.ps1` | `packaging/build_mac.sh` |
| output | `dist\FilmRawstery-v<ver>-win64.zip` + `-setup.exe` | `dist/FilmRawstery.app` + `dist/FilmRawstery-v<ver>-macos-arm64.dmg` |
| icon | `icons/app.ico` | `icons/app.icns` |
| version resource | `packaging/version_info.txt` (manual) | `Info.plist` (spec parses `APP_VERSION`) |

Cross-compiling is not possible — each platform's artifact is built on that platform.

## 1. Spec completeness check — compare the CURRENT tree to `FilmRawstery.spec`
A plain rebuild silently ships a broken/illegal bundle if the spec is stale. Verify, and edit `FilmRawstery.spec` if anything is off (explain each change):

- **QML**: every `*.qml` in `ui/` must be in the spec's `QML` list. Glob `ui/*.qml`; add any missing.
- **Dependencies that ship native code/data**: check `requirements.txt`. Any dep with native DLLs or data files (e.g. `onnxruntime`, `rawpy`, `scipy`) must be collected (`collect_all` / `collect_data_files`) and have needed `hiddenimports`. A newly added such dep → wire it in.
- **Lazy/local imports**: new local modules imported inside functions (grep `import ` in `main.py`/`pipeline.py`) should be in `hiddenimports` if PyInstaller might miss them (currently `sky_seg`, `coeffs`).
- **Licensing — never bundle non-redistributable assets**: the spec must ENUMERATE redistributable `luts/*.cube`, not copy the whole `luts/` folder. Confirm the ARR (Stuart Sowerby) B&W set is still excluded: `acros*.cube, monochrome.cube, sepia.cube`. Re-check `.gitignore` for any new "do-not-redistribute" entries and make sure none can leak into the bundle.
- **Large optional assets**: `models/*.onnx` stays OUT of the bundle (downloaded at runtime by `sky_seg.ensure_model()`). Do not bundle it unless the user explicitly asks for an offline build.
- Keep `contents_directory="lib"` and `CONSOLE=False` (set `CONSOLE=True` only for a debug build when diagnosing missing-DLL errors).

**macOS-only spec checks** (all documented in CLAUDE.md `## macOS 패키징`):
- The `if IS_MAC:` WebEngine/WebView filter after `Analysis` must still be there — `excludes` does NOT
  keep Qt frameworks out on macOS (the PySide6 hook collects all 120 of them), and QtWebEngineCore
  alone is 218 MB of the 676 MB unfiltered bundle. Do not extend that filter to the other unused
  frameworks (44 of them, 32 MB total, some load-time linked — no gain, real breakage risk).
- Never extend the `opencv_videoio_ffmpeg` exclusion to macOS: `cv2.abi3.so` load-time links
  `@loader_path/.dylibs/libavcodec…`, so dropping them breaks `import cv2` outright.
- `target_arch="arm64"` stays (thins universal2 Qt: Qt/lib 322 → 103 MB). `version=` must remain
  `None` on macOS (Windows-only argument).
- `BUNDLE` metadata: bundle identifier is an upgrade identity — never change `BUNDLE_ID`. If a new
  feature touches new user folders, add the matching `NS*UsageDescription` (TCC prompts show that
  text).
- If the icon design changed, regenerate BOTH containers: `packaging/make_icon.py --ico --icns`.

## 2. Build + smoke test
Run the deterministic script for the host platform. Both clean `dist/`, build, smoke-test the app from
another directory, and produce the installer/disk image.

Windows (zip + Inno Setup installer from `packaging/FilmRawstery.iss`):
```
.\packaging\build.ps1
```
`-NoInstaller` skips the installer step (zip only); if Inno Setup isn't installed the script warns and skips it — say so in the report instead of treating it as success.

macOS (ad-hoc signature + DMG by default):
```
packaging/build_mac.sh
```
- `--sign "Developer ID Application: … (TEAMID)" --notarize` for a public build; without a Developer ID
  certificate the default ad-hoc signature is `spctl`-rejected and users must allow it in System
  Settings (macOS 15 removed the Ctrl-click bypass) — say which one you produced.
- ⚠️ The Homebrew Python this repo's venv uses targets the host OS. That is fine while the floor is
  macOS 15 (PySide6 sets it — see the `minos` check below), but if the floor is ever lowered, the
  distributable must be built from a **python.org** Python venv (`VENV=… packaging/build_mac.sh`).

If either throws, read the error / smoke output and fix (often a missing data file or hidden import), then re-run.

⚠️ If the app icon (`icons/app.ico` / `icons/app.icns`) was regenerated since the last build, delete the `build/` cache first — PyInstaller does not detect icon *content* changes and will reuse the cached EXE with the old embedded icon (the build log will show `checking EXE` without a subsequent `Building EXE`). Verify the log contains `Building EXE` whenever the icon changed.

## 3. Verify the bundle
Windows (`dist/FilmRawstery/lib/`) — confirm: all `*.qml` present (incl. any new ones), ARR cubes NOT present, `onnxruntime` DLLs present, no `models/` dir, exe launched in the smoke test.

macOS (`dist/FilmRawstery.app/Contents/Resources/` for data, `…/Frameworks/` for binaries — they are cross-symlinked) — confirm the same four, plus:
```
plutil -p dist/FilmRawstery.app/Contents/Info.plist | grep -E "Bundle(Identifier|ShortVersion)|LSMinimum"
lipo -info dist/FilmRawstery.app/Contents/Frameworks/PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore   # arm64, non-fat
codesign --verify --strict dist/FilmRawstery.app
find dist/FilmRawstery.app \( -name '*.dylib' -o -name '*.so' \) | xargs -n1 otool -L | grep -c '@rpath/QtWebEngine'   # must be 0
```
⚠️ **Re-measure the OS floor whenever a dependency is upgraded** — wheel tags lie (PySide6 6.11.2 is
tagged `macosx_13_0` but its bindings are `minos 15.0`). Walk every Mach-O in the bundle with
`vtool -show-build`, take the maximum `minos`, and make `LSMinimumSystemVersion` in the spec equal to
it. Declaring less does not widen support; it just turns a clean "requires macOS X" dialog into a dyld
crash.
Then mount the DMG and launch the app from the read-only volume for a few seconds — that is what catches anything writing inside the bundle. Report the `.app` and DMG sizes (457 MB / 169 MB at v1.9.0; a sudden jump usually means the WebEngine filter stopped matching).

Windows installer (`dist/FilmRawstery-v<ver>-setup.exe`): confirm it exists and its size is plausible (same order as the zip — solid LZMA2 usually comes out smaller). The installer just wraps `dist/FilmRawstery/` (contents are decided solely by the spec — `.iss` re-enumerates nothing), so no separate content check is needed. Do NOT bump or reuse the `AppId` GUID in `packaging/FilmRawstery.iss` — it is the upgrade identity across versions.

## 4. Report
State the artifact paths + sizes (Windows: zip and installer, or why the installer was skipped; macOS: `.app`, DMG, and which signature it carries), and exactly what (if anything) changed in the spec. If the spec changed, ask whether to commit + push it (English commit message, no `Co-Authored-By`, per CLAUDE.md). Do not commit the zip/dist (gitignored).
