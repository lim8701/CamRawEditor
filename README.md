# Film Rawstery

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6-Qt-41CD52?logo=qt&logoColor=white)
![RAW](https://img.shields.io/badge/RAW-Fujifilm%20%2B%20multi--vendor-EB0A1E)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555)

A GPU-accelerated RAW developer and film-simulation editor built with **PySide6 (QML) + GLSL shaders**. Tuned for **Fujifilm** (`.RAF`) and — as of **v1.4.0** — reads **most other RAW formats too** (Canon, Nikon, Sony, DNG, and more) via rawpy/LibRaw.

Edit interactively on a real-time, shader-driven preview, then export at full resolution through a numpy pipeline that mirrors the shader exactly — *what you see is what you get*.

<p align="center">
  <img src="docs/screenshot.png" alt="Film Rawstery — develop: file explorer, real-time preview, and develop panel" width="100%">
  <br><br>
  <img src="docs/screenshot2.png" alt="Film Rawstery — masking: AI multi-class selection (people masked) with per-mask adjustments" width="100%">
</p>

> **Fujifilm RAF** is the primary, look-tuned target (developed on an X100V): color matrices, white balance, and **lens corrections are read from each file's own metadata**, so no per-model profiles are needed. **Other brands** (Canon / Nikon / Sony / DNG / …) decode through the same metadata-driven color pipeline; lens corrections are Fujifilm-only (they come from Fuji's embedded tables) and the film-simulation looks are, of course, Fuji.

---

## Why I built this

A hobby project, built for my own use.

I shoot a lot with the **Fujifilm X100V** and edit in Lightroom — but I only use a few of its features, so paying for a subscription felt hard to justify. Film Rawstery bundles just the features I actually need into a workflow tuned the way I like it.

Any Fujifilm body works out of the box (everything is driven by per-file metadata). As of v1.4.0 other brands (Canon, Nikon, Sony, DNG, …) are supported too — decoded through the same metadata-driven color pipeline — though the film-simulation looks and lens corrections remain Fuji-centric.

### The name

*Film Rawstery* is a play on **roastery**. A coffee roastery takes raw beans and roasts them into something worth drinking; this app does the same with **RAW** files — raw sensor data developed and refined into a finished photo — hence **Raw**stery. The *Film* nods to the Fujifilm film-simulation looks at its heart.

---

## Features

### Develop
- **Scene-linear + filmic** tone pipeline — physically-grounded base render with a single highlight-rolloff tone curve (no per-scene heuristics)
- **White balance** — absolute Kelvin + tint via the Planckian locus, with as-shot estimation for off-locus illuminants
- **Light** — exposure (scene-linear stops), contrast, highlights / shadows / whites / blacks (Lightroom-style local tone zones)
- **Tone curve** — Catmull-Rom editor with **per-channel RGB** curves (master + R/G/B) for color grading
- **HSL color mixer** — 8 hue bands × hue / saturation / luminance
- **Color** — vibrance & saturation
- **Detail** — texture, clarity, dehaze, and **sharpening** (amount / radius / detail / masking)
- **Noise reduction** — edge-preserving luminance NR (guided filter) + color NR, with an optional **AI denoise** base (NAFNet, auto-downloaded ~117 MB; GPU-accelerated via DirectML when available, with a confirm prompt before falling back to the much slower CPU path)
- **Effects** — film grain, vignette
- **Highlight reconstruction** — hue-aware desaturation that neutralizes clipped-highlight color casts (e.g. a fire core) while preserving saturated colored light sources (neon, signs)

### Masking (local adjustments)
- **AI selection, two families** — a **Scene** tab (SegFormer-B2 / ADE20K) and a **Face** tab; models auto-download on first use
  - **Scene** — tick any combination of **Sky / Vegetation / Building / Ground / Water / Mountain / Person**
  - **Face** — pixel-precise face parts: **Skin / Nose / Eyes / Brows / Glasses / Lips / Mouth / Ears / Hair / Hat / Neck**, for up to 5 faces per photo
- **Composite** — the mask is the union of everything ticked across both tabs, recomposed live from cached inference (switching parts is instant; no re-inference)
- **Up to 5 layers** — create and delete mask layers, each with its own mask *and* its own adjustments (e.g. sky brighter, skin warmer, mountains darker in one photo)
- **Edge-refined soft mask** — guided-filter refinement against image luminance for clean branch/hairline boundaries, plus invert and a red mask overlay
- **Per-mask develop** — Exposure / Temp / Tint / Contrast / Highlights / Shadows / Texture / Clarity / Dehaze / Saturation, applied only to the masked region in both preview and export
- Masks persist per-image (regenerated from the saved classes on reopen)

Face masking is two models working together: **YuNet** locates faces (232 KB), then **SegFormer-B5
trained on CelebAMask-HQ** parses each face crop into 19 classes. The detector only decides where to
crop — boundary precision comes from parsing plus the guided filter, so masks follow the real hairline
and jaw rather than a box. Detection takes ~60 ms, parsing ~0.8 s per face, and part toggles ~10 ms.

### AI Caption
- **On-device English captions** — Microsoft **Florence-2** running locally via ONNX (MIT-licensed model); no cloud, no account
- Captions generate automatically when a photo finishes loading and appear in a bar under the preview (toggle with `C`)
- Three detail levels — **Short / Detailed / Paragraph** — switch via the combo; each level is generated once and cached in the folder sidecar (`.filmrawsterycaptions.json`)
- The ~1.1 GB model never downloads silently: the bar offers a one-time **click-to-download opt-in**, and captions stay automatic afterwards
- ⚠️ Small-model honesty: object/people **counts can be off by one** and long captions may embellish details — treat it as a browsing aid, not ground truth

<p align="center">
  <img src="docs/screenshot3.png" alt="Film Rawstery — AI caption: on-device Florence-2 description bar under the preview, with detail-level selector (Short / Detailed / Paragraph)" width="100%">
</p>

### Folder search & Photo tags
Turns the on-device captions into a way to **browse a whole folder by content**:
- **Caption search** — a search box over the file explorer filters the folder by caption **keywords** (the content words behind the captions — hashtag-style, with stopwords / numbers / very short tokens dropped). Terms are prefix-matched and AND-combined.
- **Background indexing** — one click on **⚙ Index** captions the whole folder in the background (**CPU-only**, so it never contends with the GPU preview/edit and can't crash it), **resumable** (already-captioned photos are skipped), with a live coverage bar. Browsing and editing stay responsive throughout, and progress stays tied to the folder it started on if you navigate away.
- **Photo tags** (`H`, or the 🏷 button) — an immersive, frosted-glass **tag view** of the folder: each keyword's size scales with how many photos carry it (a single-hue sequential ramp), with a separate **♥ In liked photos** group. Hover a tag to preview its photos in a grid on the right; **click a tag** (or any preview thumbnail) to filter the explorer to it. A header line summarizes the folder — photos · indexed · unique tags · liked.

Captions are stored once as their raw text, so the search and tag rules are derived at query time — changing them needs no re-indexing.

<p align="center">
  <img src="docs/screenshot5.png" alt="Film Rawstery — Photo tags: frosted-glass tag view of a folder, keyword sizes scaled by how many photos carry each tag, with a live photo preview grid; click a tag to filter the folder" width="100%">
</p>

### Film Simulations
Fujifilm looks as 3D LUTs: Provia, Velvia, Astia, Classic Chrome, Classic Negative, Nostalgic Neg, PRO Neg. Hi/Std, Eterna, Reala Ace, Bleach Bypass — with adjustable strength. The list is driven by the `.cube` files present in `luts/`, so any known LUT you drop in (e.g. B&W ACROS / Monochrome / Sepia) appears automatically, and missing ones are hidden. See [`luts/README.md`](luts/README.md) for the key filenames and where to get the B&W LUTs.

### Film date stamp
Reproduces a film **quartz date-back** — not text pasted on top, but a simulation of the LED imprint that exposed the *same emulsion* as the photo:
- **Additive (screen) blend** — the imprint adds light the way the LED exposes film: it glows over dark areas and washes out over bright highlights, instead of sitting on top like a sticker (mixed with a touch of source-over so highlights don't erase it entirely)
- **Same-emulsion grain** — the stamp carries the photo's film grain (linked to the Grain amount), so it's never cleanly digital
- **Halation** — hot-core → amber → red-orange bloom, the way bright light scatters in the emulsion
- **Segment / dot fonts** — DSEG seven-/fourteen-segment (Regular / Bold, upright / italic) plus a round-dot matrix (Doto) — all SIL OFL
- **Frame-relative placement** — imprinted in the sensor's bottom-right corner via EXIF orientation, so portrait shots rotate it into the matching corner
- Style / size / margin adjust and persist per-image; the date defaults to the EXIF capture date and is editable. Toggle with `D`.
- The final **export** uses the screen/source-over blend above; the live **preview** uses a lightweight source-over overlay, so on very bright highlights the previewed stamp reads a touch more solid than the exported file — a deliberate simplicity trade-off (they match on normal/dark backgrounds)

See [`docs/date_stamp.md`](docs/date_stamp.md) for the physical model and implementation.

<p align="center">
  <img src="docs/screenshot4.png" alt="Film Rawstery — film date stamp: quartz date-back imprint (seven-segment) screen-blended into the photo, with style / size / margin controls" width="100%">
</p>

### Geometry
Crop (aspect-ratio presets + free drag), rotate / straighten, flip, and perspective (vertical / horizontal keystone + scale) — applied identically in preview and export.

### Lens Corrections
Distortion, vignetting, and chromatic aberration — applied from the **per-shot correction tables Fujifilm embeds in every RAF** (focus/aperture-aware, works for any body and lens, fixed or interchangeable). No profile database needed; files without the tags are simply left uncorrected.

### Workflow
- **Before / After compare** — toggle the unedited original (button or `\` key)
- **Undo / redo** — snapshot history of all adjustments (`Ctrl+Z` / `Ctrl+Shift+Z`)
- **Non-destructive, per-image persistence** — edits autosave to a `.filmrawsteryedits/<file>.json` sidecar and restore when you reopen the image
- **File explorer** with RAF thumbnails and a likes/favorites filter
- **Live histogram** reflecting current adjustments
- **Full-resolution export** to JPEG / PNG / TIFF (background-threaded, UI stays responsive)
- **AI Models screen** — the left panel footer shows what has been downloaded; open it to see each model's size and status, pre-download anything missing instead of waiting on first use, and spot files no longer claimed by any feature

---

## How it works

```
RAF ──rawpy──► camera-native proxy (≤2560px, headroom-encoded)
                     │
       QML ShaderEffect pipeline (GPU, proxy-resolution FBO → scaled to screen)
       headroom-decode → WB → cam→sRGB matrix → ×2^exposure → filmic
       → tone zones → texture/clarity/dehaze → sharpen → film-sim LUT
       → vibrance/sat → HSL mixer → contrast → tone curve → mask local adjust → vignette → grain
                     │
   live preview (GPU)        Export: pipeline.py (full-res numpy, same steps)
```

Key design decisions:
- **Processing resolution ≠ display resolution** — the pipeline always renders at a fixed proxy resolution and scales to screen, so GPU load is independent of monitor size.
- **Preview = Export parity** — the GLSL shaders (`shaders/adjust.frag`) and the numpy export (`pipeline.py`) implement the same steps and formulas; strength coefficients live in a single `coeffs.py`, injected into the shader as uniforms so a change updates preview and export together.
- **Color science first, look-matching second** — algorithms are physically/colorimetrically correct; strengths and curves are then tuned to feel like Adobe Lightroom.

---

## Requirements

- Python 3.13 (3.11+ should work)
- `PySide6`, `rawpy`, `numpy`, `scipy`, `exifread`, `opencv-python-headless`, `onnxruntime-directml` (Windows; plain `onnxruntime` elsewhere — see [`requirements.txt`](requirements.txt))
  - ⚠️ OpenCV must be **`headless` and 5.0+**: the full package ships its own Qt plugins and clashes with PySide6, and 4.x pins `numpy<2.3` so it would downgrade the project's numpy. It is used only for face detection and mask resampling.
- A GPU/driver supporting the Qt RHI (OpenGL / Direct3D / Metal / Vulkan)
- AI models download on first use into a per-user data folder that survives app updates (`%LOCALAPPDATA%\FilmRawstery\models` on Windows) — needs an internet connection the first time. Scene masking (SegFormer-B2, ~105 MB), face masking (YuNet + SegFormer-B5, ~341 MB) and AI denoise (NAFNet, ~117 MB) fetch automatically when you first use the feature; the caption model (Florence-2, ~1.1 GB) **only after an explicit in-app opt-in**. The **AI Models** screen (left panel footer) shows what is installed and lets you pre-download any of them (see [`models/README.md`](models/README.md))

## Install & Run

### Common setup (all platforms)

```bash
# 1. Get the source (requires git — or skip git entirely with
#    GitHub's "Code → Download ZIP" and unzip instead)
git clone https://github.com/lim8701/FilmRawstery.git
cd FilmRawstery

# 2. Create a virtual environment (recommended)
python -m venv .venv          # on macOS/Linux: python3.13 -m venv .venv

# 3. Activate it
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux

# 4. Install dependencies and run
pip install -r requirements.txt
python main.py
```

Open a `.RAF` from the left file explorer (double-click). Shaders auto-recompile from `shaders/*.frag` on launch when changed.

### Windows

The primary development/test platform. A prebuilt zip (no Python required) is available on the [Releases](https://github.com/lim8701/FilmRawstery/releases) page — extract and run `FilmRawstery.exe`.

### macOS

Runs from source with the common setup above — all dependencies ship prebuilt macOS wheels (Apple Silicon included), so no Xcode/compiler is needed. Notes:

- macOS ships an older system `python3`; create the venv with an explicit `python3.13` (from [python.org](https://www.python.org/downloads/)) as shown above.
- No `git`? Either accept the Command Line Tools popup when first running `git`, or use **Code → Download ZIP** on GitHub instead.
- Shaders are precompiled with Metal (MSL) included; if a recompile is triggered, the `pyside6-qsb` tool installed with PySide6 handles it automatically.
- Display color management (preview-only monitor-profile correction) is Windows-only and silently disabled on macOS — everything else works the same.
- AI denoise uses the CoreML execution provider (included in the standard `onnxruntime` macOS wheel, Apple Silicon included) and falls back to CPU — with a confirm prompt — if unavailable.
- ⚠️ **Untested in practice** — the code is written to be platform-clean, but no one has verified a real macOS run yet (I develop on Windows and don't own a Mac — see [Support](#support--후원)). If you try it, [feedback is very welcome](https://github.com/lim8701/FilmRawstery/issues).

---

## Project structure

| Path | Role |
|------|------|
| `main.py` | App entry point, controller, image providers (raw / lut / curve / stamp / thumb) |
| `raw_loader.py` | RAW → display proxy (X-Trans-safe / Bayer-AHD decode, headroom encoding, lens correction) |
| `pipeline.py` | Full-resolution export — numpy reproduction of the shader pipeline |
| `sky_seg.py` | Scene masking engine — ONNX SegFormer multi-class segmentation → composite soft mask |
| `face_seg.py` | Face masking engine — YuNet detection (OpenCV DNN) + ONNX SegFormer face parsing → per-part soft mask |
| `ai_denoise.py` | AI denoise engine — ONNX NAFNet tiled inference, DirectML-accelerated (luminance NR base) |
| `caption.py` | AI caption engine — ONNX Florence-2 on-device English captions (self-contained BPE tokenizer) |
| `app_dirs.py` | Per-OS user-data model store (survives updates; migrates legacy downloads by copy) |
| `coeffs.py` | Single source of truth for adjustment strength coefficients (shader uniforms + pipeline) |
| `wb.py` | White balance (Kelvin/tint), cam→sRGB matrix, filmic curve, auto-exposure |
| `lens.py` | Lens corrections from RAF-embedded per-shot metadata (distortion / vignetting / CA) |
| `lut.py`, `make_luts.py` | `.cube` 3D LUT loading / baking |
| `date_stamp.py`, `exif_info.py` | Film date-back rendering / EXIF extraction |
| `ui/*.qml` | UI (Main / CurveEditor / PreviewWindow / Splash / FilmStrip) |
| `shaders/adjust.frag` | Main develop pipeline (fragment shader) |
| `shaders/blur.frag`, `shaders/convert.frag` | Separable blur (local contrast) / display-space base |
| `luts/*.cube` | Film-simulation LUTs |

---

## Support / 후원

**Donations go toward buying a MacBook.** Development happens on Windows only; the macOS side is written to match but has never been properly tested. I'd like to raise a little toward doing macOS development right. Development continues regardless, of course. :)

**후원금은 맥북을 구입하는 데 사용하려 합니다.** 개발은 Windows에서만 진행하고 있으며 macOS는 코드만 맞춰놨을 뿐 한 번도 제대로 테스트해 보지 못하였습니다. 제대로 된 mac용 개발을 위해 조금의 모금을 해보려 합니다. 물론 이와 상관없이 개발은 그대로 계속 이어집니다. :)

[![KakaoPay](https://img.shields.io/badge/KakaoPay-donate%20%C2%B7%20%ED%9B%84%EC%9B%90-FFCD00?style=flat-square&logo=kakaotalk&logoColor=3C1E1E)](https://qr.kakaopay.com/281006011121697761001224)

<a href="https://qr.kakaopay.com/281006011121697761001224"><img src="assets/donate_kakaopay.jpg" width="180" alt="KakaoPay donation QR"></a>

*KakaoPay (Korea) — mobile only. On a desktop, scan the QR with your phone; on a phone, tap the badge.*
*카카오페이는 모바일 전용입니다. PC에서는 휴대폰으로 QR을 찍고, 휴대폰에서는 위 배지를 눌러주세요.*

---

## License

A hobby project — shared so others can use and learn from it.

- **Source code & original assets** — [MIT](LICENSE). Use, modify, and redistribute freely (including commercially).
- **Film-simulation LUTs** (`luts/*.cube`) — **CC BY-NC-SA 4.0** (attribution · **non-commercial** · share-alike), derived from [FujifilmCameraProfiles](https://github.com/abpy/FujifilmCameraProfiles); see [`luts/LICENSE`](luts/LICENSE). The code is reusable commercially, but the bundled LUTs are not.
- **AI models** — downloaded at runtime under their own licenses: the scene masking model is research-oriented and the face parsing model derives from **CelebAMask-HQ (non-commercial research only)** — verify both before commercial use; the face detector (YuNet) is MIT, and the caption model (Florence-2) and denoise model (NAFNet) are MIT. See [`models/README.md`](models/README.md).

> Bundled third-party components keep their own licenses; the MIT grant covers this project's own code and assets only.

---

## Credits

- **Film-simulation LUTs** — derived from the [*FujifilmCameraProfiles*](https://github.com/abpy/FujifilmCameraProfiles) project (sRGB `.cube`), licensed CC BY-NC-SA 4.0
- **Date-back fonts** — [DSEG](https://github.com/keshikan/DSEG) by Keshikan (seven-/fourteen-segment) and [Doto](https://github.com/oliverlalan/Doto) by the Doto Project Authors (round-dot matrix) — both SIL Open Font License 1.1
- **Scene masking model** — SegFormer-B2 finetuned on ADE20K, ONNX export by [Xenova](https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512) (transformers.js). ⚠️ Research-oriented license — verify before commercial use; see [`models/README.md`](models/README.md)
- **Face detection model** — [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) from OpenCV Zoo (MIT), run through OpenCV's `FaceDetectorYN`
- **Face parsing model** — [face-parsing](https://huggingface.co/jonathandinu/face-parsing) by Jonathan Dinu (SegFormer finetuned on CelebAMask-HQ), ONNX export by [Xenova](https://huggingface.co/Xenova/face-parsing). ⚠️ CelebAMask-HQ is **non-commercial research only**; see [`models/README.md`](models/README.md)
- **Caption model** — [Florence-2-base-ft](https://huggingface.co/microsoft/Florence-2-base-ft) by Microsoft (MIT), ONNX export by [onnx-community](https://huggingface.co/onnx-community/Florence-2-base-ft)
- **Denoise model** — [NAFNet](https://github.com/megvii-research/NAFNet) SIDD-width32 by megvii-research (MIT), converted to ONNX for this project
- **ONNX inference** — [ONNX Runtime](https://onnxruntime.ai/)
- **Face detection runtime** — [OpenCV](https://opencv.org/) (Apache-2.0), via `opencv-python-headless` (MIT packaging)
- **RAW decoding** — [rawpy](https://github.com/letmaik/rawpy) / LibRaw
- **UI & GPU pipeline** — [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/)
- Plus [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), and [ExifRead](https://github.com/ianare/exif-py)
