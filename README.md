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

The short version. The full reference — screenshots, every control, and the reasoning behind each
model — is in [`docs/features.md`](docs/features.md).

### Develop
Scene-linear + filmic base render, absolute-Kelvin white balance via the Planckian locus,
Lightroom-style tone zones, per-channel tone curves, an 8-band HSL mixer, texture / clarity / dehaze /
sharpening, edge-preserving noise reduction with an optional **AI denoise** base (NAFNet), and
hue-aware highlight reconstruction.

### Looks
- **Film simulations** — Fujifilm looks as 3D LUTs (Provia, Velvia, Astia, Classic Chrome, Classic
  Negative, Nostalgic Neg, PRO Neg Hi/Std, Eterna, Reala Ace, Bleach Bypass) with adjustable strength.
- **Film grain** — an emulsion model rather than sprinkled noise: amplitude follows the
  characteristic curve, so grain peaks in the midtones and fades out of blown highlights, and the
  three dye layers speckle in colour. Fitted to 11,512 patches from four rolls of scanned negative.
  → [`docs/film_grain.md`](docs/film_grain.md)
- **Mist filter** — a diffusion filter modelled as scattering, so fine detail stays sharp while
  highlights bloom around it. White mist through black mist on one axis.
  → [`docs/mist_filter.md`](docs/mist_filter.md)
- **Film date stamp** — a quartz date-back imprint that exposes the same emulsion as the photo:
  additive blend, the photo's own grain, halation, and segment / dot / typewriter fonts (bring your
  own too). → [`docs/date_stamp.md`](docs/date_stamp.md)
- **Recipe presets** — save a look and put it on another photo, with the camera and lens it came from
  recorded. Applying one keeps that photo's own white balance, crop and masks.

### Masking (local adjustments)
AI selection in three families — **Scene** (sky / vegetation / building / ground / water / mountain /
person), **Face** (19 parts, and you choose which face), and **Depth** (select by *distance* instead
of by what a thing is). Paint on top with an add / subtract **brush**. Up to 5 layers, each with its
own mask *and* its own develop settings — sky brighter, skin warmer, mountains darker in one photo.
→ [`docs/sky_masking.md`](docs/sky_masking.md) · [`docs/depth_masking.md`](docs/depth_masking.md)

### Browse a whole folder
On-device English captions (Florence-2, downloaded only after an explicit opt-in), caption **search**
over the file explorer, resumable background indexing, and a **Photo tags** view that sizes each
keyword by how many photos carry it. → [`docs/folder_index_search.md`](docs/folder_index_search.md)

### Geometry & optics
Crop, rotate / straighten, flip and perspective — plus distortion, vignetting and chromatic
aberration corrected from the **per-shot tables Fujifilm embeds in every RAF**, so no profile database
is needed for any body or lens.

### Workflow
Before / after compare, undo / redo, a **Zone System overlay**, a live histogram, non-destructive
per-image sidecars, a file explorer with RAF thumbnails and a likes filter, full-resolution export to
JPEG / PNG / TIFF, and an **AI Models** screen showing what has been downloaded.

---

## How it works

RAW is decoded to a camera-native proxy; a QML `ShaderEffect` pipeline develops it on the GPU at a
fixed proxy resolution, and `pipeline.py` reproduces the same steps in numpy at full resolution for
export. Same formulas, same strength coefficients from a single `coeffs.py` injected as shader
uniforms — so **what you see is what you get**.

The pipeline diagram, the design decisions behind it and a map of every module are in
[`docs/architecture.md`](docs/architecture.md).

---

## Requirements

- Python 3.13 (3.11+ should work), and a GPU/driver supporting the Qt RHI (OpenGL / Direct3D / Metal / Vulkan)
- `PySide6`, `rawpy`, `numpy`, `scipy`, `exifread`, `opencv-python-headless`, `onnxruntime-directml`
  (Windows; plain `onnxruntime` elsewhere) — see [`requirements.txt`](requirements.txt).
  ⚠️ OpenCV must be **`headless` and 5.0+**: the full package ships its own Qt plugins and clashes
  with PySide6, and 4.x would downgrade the project's numpy.
- AI models download on first use (~105–341 MB each) into a per-user folder that survives app updates;
  the 1.1 GB caption model only after an explicit in-app opt-in. The **AI Models** screen lists what is
  installed and can pre-download the rest. See [`models/README.md`](models/README.md).

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

The primary development/test platform. A prebuilt installer (no Python required) is available on the [Releases](https://github.com/lim8701/FilmRawstery/releases) page — run `FilmRawstery-vX.Y.Z-setup.exe` and launch **Film Rawstery** from the Start menu. It installs to `Program Files`, adds an uninstaller, and upgrades in place when a newer setup is run.

### macOS

Runs from source with the common setup above, and there is an experimental prebuilt DMG on the
[Releases](https://github.com/lim8701/FilmRawstery/releases) page (Apple Silicon, macOS 15+, not
notarized — it needs a one-time unblock). Setup notes, the unblock steps and the current status:
[`docs/install_macos.md`](docs/install_macos.md).

---

## Documentation

[`docs/`](docs/README.md) — the full feature reference, the architecture, and the working notes behind
each model: what was measured, what was rejected, and why.

---

## Support / 후원

**Donations go toward buying a MacBook.** Development happens on Windows; the macOS side has only had brief testing, so it sees far less mileage. I'd like to raise a little toward doing macOS development right. Development continues regardless, of course. :)

**후원금은 맥북을 구입하는 데 사용하려 합니다.** 개발은 Windows에서 진행하고 있고, macOS 쪽은 짧게 확인해 본 정도라 검증이 많이 부족합니다. 제대로 된 mac용 개발을 위해 조금의 모금을 해보려 합니다. 물론 이와 상관없이 개발은 그대로 계속 이어집니다. :)

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
