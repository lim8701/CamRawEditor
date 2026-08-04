---
description: Analyze the project for packaging-impacting changes, update FilmRawstery.spec if needed, then build the onedir distributable + Inno Setup installer (packaging/build.ps1) and verify.
---

Package the Film Rawstery app (PyInstaller onedir → zip + Inno Setup installer). Work in this order and report concisely.

## 1. Spec completeness check — compare the CURRENT tree to `FilmRawstery.spec`
A plain rebuild silently ships a broken/illegal bundle if the spec is stale. Verify, and edit `FilmRawstery.spec` if anything is off (explain each change):

- **QML**: every `*.qml` in `ui/` must be in the spec's `QML` list. Glob `ui/*.qml`; add any missing.
- **Dependencies that ship native code/data**: check `requirements.txt`. Any dep with native DLLs or data files (e.g. `onnxruntime`, `rawpy`, `scipy`) must be collected (`collect_all` / `collect_data_files`) and have needed `hiddenimports`. A newly added such dep → wire it in.
- **Lazy/local imports**: new local modules imported inside functions (grep `import ` in `main.py`/`pipeline.py`) should be in `hiddenimports` if PyInstaller might miss them (currently `sky_seg`, `coeffs`).
- **Licensing — never bundle non-redistributable assets**: the spec must ENUMERATE redistributable `luts/*.cube`, not copy the whole `luts/` folder. Confirm the ARR (Stuart Sowerby) B&W set is still excluded: `acros*.cube, monochrome.cube, sepia.cube`. Re-check `.gitignore` for any new "do-not-redistribute" entries and make sure none can leak into the bundle.
- **Large optional assets**: `models/*.onnx` stays OUT of the bundle (downloaded at runtime by `sky_seg.ensure_model()`). Do not bundle it unless the user explicitly asks for an offline build.
- Keep `contents_directory="lib"` and `CONSOLE=False` (set `CONSOLE=True` only for a debug build when diagnosing missing-DLL errors).

## 2. Build + smoke test
Run the deterministic script (cleans dist, builds, smoke-tests the exe from another dir, zips, then compiles the Inno Setup installer from `packaging/FilmRawstery.iss`):
```
.\packaging\build.ps1
```
If it throws, read the error / smoke stderr and fix (often a missing data file or hidden import), then re-run. `-NoInstaller` skips the installer step (zip only); if Inno Setup isn't installed the script warns and skips it — say so in the report instead of treating it as success.

⚠️ If `icons/app.ico` was regenerated since the last build, delete the `build/` cache first — PyInstaller does not detect icon *content* changes and will reuse the cached EXE with the old embedded icon (the build log will show `checking EXE` without a subsequent `Building EXE`). Verify the log contains `Building EXE` whenever the icon changed.

## 3. Verify the bundle (`dist/FilmRawstery/lib/`)
Confirm: all `*.qml` present (incl. any new ones), ARR cubes NOT present, `onnxruntime` DLLs present, no `models/` dir, exe launched in the smoke test.

Installer (`dist/FilmRawstery-v<ver>-setup.exe`): confirm it exists and its size is plausible (same order as the zip — solid LZMA2 usually comes out smaller). The installer just wraps `dist/FilmRawstery/` (contents are decided solely by the spec — `.iss` re-enumerates nothing), so no separate content check is needed. Do NOT bump or reuse the `AppId` GUID in `packaging/FilmRawstery.iss` — it is the upgrade identity across versions.

## 4. Report
State the zip path + size, the installer path + size (or why it was skipped), and exactly what (if anything) changed in the spec. If the spec changed, ask whether to commit + push it (English commit message, no `Co-Authored-By`, per CLAUDE.md). Do not commit the zip/dist (gitignored).
