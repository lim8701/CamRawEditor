# Film-simulation LUTs (`luts/`)

The film-simulation list is **dynamic**: a look appears in the app only if its
`<key>.cube` file exists in this folder, and is hidden otherwise. So you can add
or replace LUTs without touching code — just match the filename (key) below.
Any cube size works (the loader auto-detects N per file).

> ### Adding your own LUTs — use the app, not this folder
>
> To use a `.cube` you made or downloaded, open **Film Simulation → Add LUT…**
> in the app. The file is copied to your user data folder
> (`%LOCALAPPDATA%\FilmRawstery\luts` on Windows) and appears under **My LUTs**
> in the selector, right away — no restart, no admin rights.
>
> This folder is for **replacing the bundled film simulations** under the exact key
> names listed below. In an installed build it lives inside the install directory,
> which is read-only and **is replaced on every update** — anything you drop here
> by hand is lost when you upgrade. Your own LUTs belong in the user folder.
>
> Two things to know about imported LUTs:
> - **They are applied exactly as authored.** The bundled Fuji cubes get an automatic
>   brightness correction (they carry the whole Fuji tone curve, which would otherwise
>   be applied twice); imported LUTs do not, because their brightness *is* the look.
> - **Log-input LUTs are not supported.** A LUT expecting S-Log3 / V-Log / Cineon input
>   (any `DOMAIN_MIN`/`DOMAIN_MAX` outside `[0,1]`) is rejected on import — the app feeds
>   display-referred sRGB into the LUT, so such a LUT would silently produce wrong colour.
>   1D-only LUTs are rejected too. Cubes larger than 64³ are resampled to 64³ on import
>   (the atlas is N² pixels wide, so bigger cubes exceed common GPU texture limits).
>
> To exercise all of that without hunting for downloads, run
> [`make_test_luts.py`](make_test_luts.py) — it writes a fixture set to `testluts/`
> (7 valid LUTs across N=17/32/33/64/65, plus the three that must be rejected).

## Keys (filename → simulation)

**Color** — bundled, licensed **CC BY-NC-SA 4.0** (see [`LICENSE`](LICENSE)):

```
provia  velvia  astia  classic_chrome  classic_neg  nostalgic_neg
pro_neg_hi  pro_neg_std  eterna  reala_ace  bleach_bypass
```
(`identity` = "None" / no LUT applied)

**B&W** — **NOT bundled**, add your own:

```
acros  acros_ye  acros_r  acros_g  monochrome  sepia
```

## Adding the B&W simulations

These are not shipped (license, see below). To enable them, drop matching
`.cube` files into this folder using the key filenames above
(e.g. `acros.cube`, `acros_ye.cube`, `monochrome.cube`, `sepia.cube`).
They appear in the **Film Simulation** selector automatically on next launch.

A good source is **Stuart Sowerby's Fuji film-simulation profiles**:
<https://blog.sowerby.me/fuji-film-simulation-profiles/>

> ⚠️ Stuart Sowerby's profiles are © Stuart Sowerby, **All Rights Reserved**, and
> are **not redistributed by this project**. Obtain them yourself and keep them
> local (they're git-ignored here).

### Converting them to `.cube` (step by step)

Sowerby distributes the profiles as **HaldCLUT PNG** images (plus 3DL and
other formats), not `.cube` — so one conversion step is needed. This folder
ships a converter, [`hald_to_cube.py`](hald_to_cube.py), that runs in the same
Python environment as the app (see the README's *Install & Run*; no extra
packages needed):

1. Download the **HaldCLUT (PNG)** pack from the page above (the XTrans III
   set includes Acros, Acros+Ye/R/G, Mono, and Sepia).
2. From the project folder, with the venv active, convert each PNG to its
   matching key filename:

   ```bash
   python luts/hald_to_cube.py "Fuji XTrans III - Acros.png"    luts/acros.cube
   python luts/hald_to_cube.py "Fuji XTrans III - Acros+Ye.png" luts/acros_ye.cube
   python luts/hald_to_cube.py "Fuji XTrans III - Acros+R.png"  luts/acros_r.cube
   python luts/hald_to_cube.py "Fuji XTrans III - Acros+G.png"  luts/acros_g.cube
   python luts/hald_to_cube.py "Fuji XTrans III - Mono.png"     luts/monochrome.cube
   python luts/hald_to_cube.py "Fuji XTrans III - Sepia.png"    luts/sepia.cube
   ```

   (Adjust the input filenames to whatever the downloaded pack uses.)
3. Restart the app — the B&W looks appear in the Film Simulation selector.

The converter auto-detects the Hald level and **downsamples anything larger
than 64³ to 64³** (RawTherapee-style level-12 HaldCLUTs are 144³, whose LUT
atlas would exceed common GPU texture-size limits in this app); pass
`--size N` to override. Verified end-to-end against the app's own LUT loader.

## Replacing / updating the color LUTs

To use different or more accurate color LUTs, just overwrite the same
`<key>.cube` filename — no code change needed. The bundled color cubes are
derived from [FujifilmCameraProfiles](https://github.com/abpy/FujifilmCameraProfiles)
(CC BY-NC-SA 4.0). `make_luts.py` can also bake approximate fallbacks.
