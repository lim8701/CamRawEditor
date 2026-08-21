# How it works

```
RAF ──rawpy──► camera-native proxy (≤2560px, headroom-encoded)
                     │
       QML ShaderEffect pipeline (GPU, proxy-resolution FBO → scaled to screen)
       headroom-decode → mist scatter → WB → cam→sRGB matrix → ×2^exposure → filmic
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

## Project structure

| Path | Role |
|------|------|
| `main.py` | App entry point, controller, image providers (raw / lut / curve / stamp / thumb) |
| `raw_loader.py` | RAW → display proxy (X-Trans-safe / Bayer-AHD decode, headroom encoding, lens correction) |
| `pipeline.py` | Full-resolution export — numpy reproduction of the shader pipeline |
| `sky_seg.py` | Scene masking engine — ONNX SegFormer multi-class segmentation → composite soft mask |
| `face_seg.py` | Face masking engine — YuNet detection (OpenCV DNN) + ONNX SegFormer face parsing → per-part soft mask |
| `depth.py` | Depth masking engine — ONNX Depth Anything V3 Small → log-depth distance map → Near/Far band mask |
| `ai_denoise.py` | AI denoise engine — ONNX NAFNet tiled inference, DirectML-accelerated (luminance NR base) |
| `caption.py` | AI caption engine — ONNX Florence-2 on-device English captions (self-contained BPE tokenizer) |
| `app_dirs.py` | Per-OS user-data model store (survives updates; migrates legacy downloads by copy) |
| `coeffs.py` | Single source of truth for adjustment strength coefficients (shader uniforms + pipeline) |
| `wb.py` | White balance (Kelvin/tint), cam→sRGB matrix, filmic curve, auto-exposure |
| `lens.py` | Lens corrections from RAF-embedded per-shot metadata (distortion / vignetting / CA) |
| `lut.py`, `make_luts.py` | `.cube` 3D LUT loading / baking |
| `mist.py` | Mist (diffusion) filter — scattering field, computed once per photo and cached |
| `presets.py` | Recipe presets — `.frpreset` format, sanitiser, validator, look fingerprint |
| `date_stamp.py`, `exif_info.py` | Film date-back rendering / EXIF extraction |
| `ui/*.qml` | UI (Main / CurveEditor / PreviewWindow / Splash / FilmStrip) |
| `shaders/adjust.frag` | Main develop pipeline (fragment shader) |
| `shaders/blur.frag`, `shaders/convert.frag` | Separable blur (local contrast) / display-space base |
| `luts/*.cube` | Film-simulation LUTs |
