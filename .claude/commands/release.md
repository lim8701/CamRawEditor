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

## 2. Sync the version (2 files)
- `main.py` `APP_VERSION`
- `packaging/version_info.txt` — `filevers`, `prodvers`, `FileVersion`, `ProductVersion` (static literals; edit all four)
- `packaging/FilmRawstery.iss` needs no edit — `build.ps1` passes `/DAppVersion` automatically
- macOS needs no edit either — `FilmRawstery.spec` parses `APP_VERSION` out of `main.py` for `Info.plist`

## 3. Spec check + clean build
Run the spec completeness check from the `package` command (QML list, native deps, hidden imports, ARR LUT exclusion, no models/). Then:
```powershell
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

## 5.5 macOS asset (optional, only when a Mac build is part of this release)
⚠️**Two machines, one owner of the version.** The release is cut on Windows: this command bumps
`APP_VERSION`, tags and pushes (steps 2–5). The Mac never bumps the version — it checks out the tag
that Windows pushed and builds from it, so both artifacts come from identical source:
```
git fetch --tags && git checkout v<ver>        # on the Mac
packaging/build_mac.sh
gh release upload v<ver> dist/FilmRawstery-v<ver>-macos-arm64.dmg   # after step 6 created the release
```
(`gh` is installed and authenticated on the Mac — that is where the mac asset gets uploaded from. Add
`--clobber` to replace an asset already attached. Assets live in GitHub's release storage, never in
the repo: `dist/` is gitignored.)
Produces `dist/FilmRawstery-v<ver>-macos-arm64.dmg` (arm64 only; macOS 15+ — the floor is measured, not
guessed: see the `minos` check in the `package` command) and prints its SHA256. Skip this step for a
Windows-only release and say so in the report — never block the release on it.

**Current distribution decision: ad-hoc signed, NOT notarized** (notarization needs the $99/year Apple
Developer membership; revisit when the mac download actually gets traffic). That makes two things
mandatory:

- ⚠️ **Do NOT flag the GitHub release as a pre-release.** The in-app updater skips
  `prerelease`/`draft` releases (`main.Controller._release_candidates`), so flagging it would silently
  stop update notifications for every Windows user. Mark the *asset* as experimental in the notes
  instead, exactly as below.
- The notes must carry the unblock steps, or users cannot open the app at all. Paste this block into
  the release body under the macOS heading:

```markdown
### macOS (experimental, Apple Silicon)

`FilmRawstery-v<ver>-macos-arm64.dmg` — requires macOS 15 (Sequoia) or newer on an Apple Silicon Mac.
Not notarized, so macOS blocks the first launch: drag the app to Applications, double-click once and
press **Done**, then **System Settings › Privacy & Security → Open Anyway**. Terminal equivalent:
`xattr -dr com.apple.quarantine /Applications/FilmRawstery.app`.
SHA256: `<paste from build_mac.sh>`
```

Once a Developer ID certificate exists, switch to
`packaging/build_mac.sh --sign "Developer ID Application: … (TEAMID)" --notarize`, drop the unblock
paragraph from the notes, and keep the SHA256 line.

## 6. GitHub release — ⚠️ALWAYS ASK BEFORE PUBLISHING
`gh` is installed and authenticated on Windows (and on the Mac — see 5.5), so you *can* publish
without the user. **Do not.** Publishing is outward-facing and effectively irreversible: the in-app
updater notifies every user as soon as the release goes live.

**Ask for explicit confirmation every time, even when the user said "proceed" earlier in the
session** — approval to build, to tag, or to push is NOT approval to publish. Show what is about to
go out and wait for a clear yes:
- tag and the commit it points at
- the notes file, and a short summary of what the notes say
- the asset filename and its size
- that it will be published as a normal (non-pre-, non-draft) release

Only after the user agrees:
⚠️ 경로는 **슬래시**로 쓴다 — 이 세션엔 PowerShell 과 Git Bash 가 둘 다 있고,
백슬래시는 Bash 에서 이스케이프로 먹혀 `distRELEASE_NOTES_...` 로 붙어버린다
(하필 되돌릴 수 없는 단계다). gh 와 Windows 모두 슬래시를 받는다.
```
gh release create v<ver> --title "v<ver>" --notes-file dist/RELEASE_NOTES_v<ver>.md --verify-tag --latest dist/FilmRawstery-v<ver>-setup.exe
```
Add `FilmRawstery-v<ver>-macos-arm64.dmg` when a Mac build was made — the in-app updater only opens
the release *page*, so a second asset needs no code change.
⚠️**`--prerelease` is forbidden** — the in-app updater skips prerelease/draft, see 5.5.
If the user would rather do it by hand, give them
`https://github.com/lim8701/FilmRawstery/releases/new?tag=v<ver>`, the notes path and the asset path,
and wait for them to say it is up.

Either way, verify afterwards with
`curl https://api.github.com/repos/lim8701/FilmRawstery/releases/latest`
(tag matches, not draft/prerelease, setup asset present and `state=uploaded`).

## 7. Finish: fast-forward main
```
git checkout main && git merge --ff-only dev && git push origin main && git checkout dev
```
Report the final state: tag, release URL, asset size, and that main == dev == v<ver>.
