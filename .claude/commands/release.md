---
description: Cut a Film Rawstery release — ask which version component to bump, sync versions, clean-build the installer, tag, publish notes, and finish by fast-forwarding main.
---

Cut a new Film Rawstery release. Work in this order and report each step concisely.
A release is **not done** until dev is merged into main (final step) — stopping earlier leaves a half-release.

## 0. Preconditions
- On `dev` with a clean working tree (uncommitted changes → stop and ask).
- `git fetch --tags origin` so the latest release tag is known.

## 1. Version — ALWAYS ask the user first
Read the current `APP_VERSION` from `main.py`, then ask the user (AskUserQuestion) which component
to bump — showing the resulting version for each choice:
- **patch** (X.Y.Z+1) — fixes/small improvements
- **minor** (X.Y+1.0) — new user-facing features
- **major** (X+1.0.0) — breaking/milestone
- **keep** (X.Y.Z as-is) — version was already bumped manually; verify tag `vX.Y.Z` does NOT already exist before proceeding (if it exists, stop and ask)

Do NOT pick a bump level yourself, even if the changes obviously look like a patch.

## 2. Sync the version (3 places)
- `main.py` `APP_VERSION`
- `packaging/version_info.txt` — `filevers`, `prodvers`, `FileVersion`, `ProductVersion` (static literals; edit all four)
- `packaging/FilmRawstery.iss` needs no edit — `build.ps1` passes `/DAppVersion` automatically

## 3. Spec check + clean build
Run the spec completeness check from the `package` command (QML list, native deps, hidden imports, ARR LUT exclusion, no models/). Then:
```
Remove-Item -Recurse -Force build   # REQUIRED: PyInstaller misses icon/version_info CONTENT changes
.\packaging\build.ps1
```
Verify: the log contains `Building EXE`; the smoke test passed; `dist\FilmRawstery-v<ver>-setup.exe` exists and its file properties show FileVersion `<ver>` (`(Get-Item ...setup.exe).VersionInfo`).

## 4. Release notes
Write `dist\RELEASE_NOTES_v<ver>.md` (English) summarizing `git log v<prev>..HEAD` — user-facing changes first (New / Fixed), skip internal-only or hidden-flag work. dist/ is gitignored; the file is only a paste source for the GitHub release body.

## 5. Commit, tag, push
- Commit the version bump (English message, no `Co-Authored-By`).
- `git tag v<ver>` — the tag MUST be exactly `v<major>.<minor>.<patch>`; the in-app update check only recognizes that form.
- `git push origin dev v<ver>`

## 6. GitHub release (user does the upload)
No `gh` CLI on this machine. Give the user:
- the link `https://github.com/lim8701/FilmRawstery/releases/new?tag=v<ver>`
- the notes file path to paste, and the asset to upload: `dist\FilmRawstery-v<ver>-setup.exe` (installer only — zip is not uploaded since v1.7.1)
Wait for the user to say it's up, then verify via `curl https://api.github.com/repos/lim8701/FilmRawstery/releases/latest` (tag matches, not draft/prerelease, setup asset present).

## 7. Finish: fast-forward main
```
git checkout main && git merge --ff-only dev && git push origin main && git checkout dev
```
Report the final state: tag, release URL, asset size, and that main == dev == v<ver>.
