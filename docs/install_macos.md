# Running Film Rawstery on macOS

Two ways: from source (works today, no Apple Developer account involved) or the prebuilt DMG. For the
source setup common to every platform, see [Install & Run](../README.md#install--run) in the README.

## From source

Runs from source with the common setup linked above — all dependencies ship prebuilt macOS wheels (Apple Silicon included), so no Xcode/compiler is needed. Notes:

- macOS ships an older system `python3`; create the venv with an explicit `python3.13` (from [python.org](https://www.python.org/downloads/)) as shown above.
- No `git`? Either accept the Command Line Tools popup when first running `git`, or use **Code → Download ZIP** on GitHub instead.
- Shaders are precompiled with Metal (MSL) included; if a recompile is triggered, the `pyside6-qsb` tool installed with PySide6 handles it automatically.
- Display color management (preview-only monitor-profile correction) is Windows-only and silently disabled on macOS — everything else works the same.
- AI denoise uses the CoreML execution provider (included in the standard `onnxruntime` macOS wheel, Apple Silicon included) and falls back to CPU — with a confirm prompt — if unavailable.
- **Status**: runs on Apple Silicon (M1 Pro, macOS 15) — the Qt RHI picks Metal, the precompiled
  shaders load without a recompile, and `xplat_check.py` reproduces the numpy export pipeline here.
  Windows is still the primary development/test platform, so the mac side sees far less mileage: [feedback is very welcome](https://github.com/lim8701/FilmRawstery/issues).
  Known gaps: the display stays awake protection during long exports is Windows-only (your Mac can
  sleep mid-export), and double-clicking a RAF in Finder does not hand it to the app yet (open photos
  from the app's own file explorer).
## Download the prebuilt app (experimental)

`FilmRawstery-vX.Y.Z-macos-arm64.dmg` on the [Releases](https://github.com/lim8701/FilmRawstery/releases)
page. It needs **Apple Silicon** (M1 or newer — Apple menu → About This Mac → Chip) and
**macOS 15 Sequoia or newer**; PySide6 6.10 and later ship binaries built for macOS 15, which sets that
floor. Intel Macs are not supported.

The build carries only an ad-hoc signature and is **not notarized by Apple** — notarization needs a paid
Apple Developer membership, and this is a donation-funded hobby project. macOS therefore blocks the
first launch, and opening it is a one-time detour:

1. Open the DMG and drag **FilmRawstery** into **Applications** (the shortcut is right there in the window).
2. Double-click it once. macOS says it *"cannot verify … is free of malware"* — click **Done**.
3. Open **System Settings › Privacy & Security**, scroll down to the **Security** section, and click
   **Open Anyway** on the FilmRawstery line, then confirm with Touch ID or your password.
4. It launches — and every launch after that is normal. You do this once per version.

If you prefer the terminal, this replaces steps 2–4:
```bash
xattr -dr com.apple.quarantine /Applications/FilmRawstery.app
```
⚠️ macOS 15 removed the old Control-click → **Open** shortcut, so the System Settings route above is the
only click-through way. Verify the download against the SHA256 published in the release notes if you
want to be careful (`shasum -a 256 <file>.dmg`) — that is the check a signature would otherwise do for you.

**Uninstall**: drag the app to the Trash. Per-user data — settings, recipes, added fonts and the
downloaded AI models — lives in `~/Library/Application Support/FilmRawstery` and can be deleted separately.

**Building it yourself** (no membership needed): `packaging/build_mac.sh` produces the same
`dist/FilmRawstery.app` + DMG from source.
