# Documentation

The README is the short version. Everything below is the long one.

## For using the app

| Document | What's in it |
|---|---|
| [Features](features.md) | The full feature reference — every panel, what each control does and why |
| [Running on macOS](install_macos.md) | Source setup notes, the experimental DMG, and how to open an unsigned app |

## How it works

| Document | What's in it |
|---|---|
| [Architecture](architecture.md) | The render pipeline, the preview-equals-export rule, and what each module does |
| [RAW demosaic policy](raw_demosaic.md) 🇰🇷 | Why X-Trans and Bayer take different decode paths |
| [RAW Peek](raw_peek.md) 🇰🇷 | The sensor mosaic before demosaic: the `R` overlay, its timings, and what the masked margins actually contain |
| [Develop animation](develop_anim.md) 🇰🇷 | Playing the whole develop chain as one timeline: why it moves uniforms instead of sliders, and which neutrals are not zero |

## The models behind the looks

Deep dives on the parts that were measured, fitted or argued over. These are working notes written in
Korean (🇰🇷) — they carry the numbers, the rejected alternatives and the reasons, not just the result.

| Document | What's in it |
|---|---|
| [Film grain](film_grain.md) 🇰🇷 | The emulsion model, fitted to 11,512 patches across four rolls of scanned negative |
| [Mist filter](mist_filter.md) 🇰🇷 | The scattering model, its log codec, and the route to replacing the prior with measurement |
| [Film date stamp](date_stamp.md) 🇰🇷 | The quartz date-back as an additive exposure on the same emulsion |
| [Sky masking](sky_masking.md) 🇰🇷 | Scene segmentation → soft mask → per-mask develop |
| [Depth masking](depth_masking.md) 🇰🇷 | Selecting by distance: monocular depth, log-z, and the per-photo seed |
| [Geotagging](geotagging.md) 🇰🇷 | Putting a location on a photo after the fact: the EXIF GPS writer, GPX-to-capture-time matching, and why JPEG only |
| [Folder indexing & search](folder_index_search.md) 🇰🇷 | On-device captions turned into a searchable folder index |
| [Tone pipeline](tone_pipeline.md) 🇰🇷 | Where the base render sits: auto exposure, the film-simulation LUTs' own tone curve, and the highlight desaturation gate |
| [Preview vs export (resolved)](KNOWN_ISSUE_preview_vs_export.md) 🇰🇷 | A colour mismatch traced to wide-gamut monitor profiles |

## Building and shipping

| Document | What's in it |
|---|---|
| [Recipe presets](recipe_presets.md) 🇰🇷 | What a `.frpreset` carries, what it deliberately leaves out, and how the badge decides |
| [UI notes](ui_notes.md) 🇰🇷 | Contact sheet, edited badge and face-part selection — including the versions that were rolled back |
| [macOS packaging](packaging_macos.md) 🇰🇷 | Bundle surgery, the minimum-OS measurement, and the signing decision |

## Elsewhere in the repo

- [`../luts/README.md`](../luts/README.md) — film-simulation LUT filenames, sources and licences
- [`../models/README.md`](../models/README.md) — the AI models, their sizes and their licences
- [`../CLAUDE.md`](../CLAUDE.md) — conventions and hard-won constraints for anyone (or anything) editing the code
