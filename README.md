# WlfRyt BadUpdate Prep

A desktop wizard that prepares a USB stick for [grimdoomer/Xbox360BadUpdate](https://github.com/grimdoomer/Xbox360BadUpdate). It downloads the right files, **verifies them**, builds the correct folder layout, copies it to your FAT32 stick, **reads it back to check**, and ends with an on-console checklist. The full Bad Update wiki (LED patterns, error codes, Blitz troubleshooting, boot-animation recovery, FAQ) is bundled for offline use.

Bad Update is a **non-persistent** hypervisor exploit for dashboard **2.0.17559.0**. Power off and you start over. It is **not** a replacement for RGH.

**Not affiliated** with grimdoomer, XeUnshackle, FreeMyXe, Internet Archive, or Microsoft.

## The five steps

1. **Welcome** - the three things that matter (17559, stay off Xbox Live, it's temporary).
2. **Choose** - *Recommended* (Rock Band Blitz pack + arcade trial + XeUnshackle, tested versions) or *Custom* (ABadAvatar, FreeMyXe, NAND dump tool, XexMenu, a hotkey to start a tool).
3. **Prepare** - downloads with per-file status, speed and ETA; resumes interrupted transfers; verifies SHA-256; builds the stick layout. Cancel any time.
4. **USB** - detects your stick (name, format, size, free space), warns about anything unsafe, copies with progress, then re-reads every file from the stick.
5. **Console** - a checklist of what to do on the Xbox 360.

The **Guide** button (or F1) opens the offline wiki at any time.

## Requirements

- Python 3.10+ **with Tk 8.6**
  - Windows: the python.org installer includes it.
  - Linux: `python3-tk` (Debian/Ubuntu), `tk` (Arch: `sudo pacman -S tk`), `python3-tkinter` (Fedora).
  - macOS: python.org's installer, or `brew install python-tk`. (Apple's system Python ships Tk 8.5, which is too old.)
- An Xbox 360 on **2.0.17559.0**
- A FAT32 USB stick, 512 MB or larger if you include the Blitz trial (about 410 MB used) (format sticks over 32 GB on the 360 dashboard or with Rufus / GUIformat)
- Optional: 7-Zip, only to import XexMenu's `.7z`

No pip packages are needed at runtime.

## Run from source

```sh
python app.py            # or: python -m badupdateprep
```

## Build a portable Windows .exe

```powershell
.\build.ps1              # or double-click build.bat
.\build.ps1 -Clean       # wipe build/ and dist/ first
.\build.ps1 -SkipTests
```

Output: `dist\WlfRyt-BadUpdate-Prep.exe`. Each build stamps `APP_VERSION` as `yyyy.MMdd.HHmm`, then restores the source to `dev`.

## Tests

```sh
python -m unittest discover -s tests -t . -v
```

Everything except the UI runs headless. To also check the pinned checksums and install layouts against the real upstream files, put the pinned release files in a folder (under their release asset names) and run:

```sh
WLFRYT_REAL_ZIPS=/path/to/folder python -m unittest tests.test_real_zips -v
```

## What it puts on the stick

| Item | Source | Where it goes |
| --- | --- | --- |
| Xbox360BadUpdate Retail USB | GitHub `grimdoomer/Xbox360BadUpdate` | `BadUpdatePayload/`, `Content/...` (the **Rock Band Blitz** folder of the pack) |
| XeUnshackle (default payload) | GitHub `Byrom90/XeUnshackle` | `BadUpdatePayload/default.xex` (+ `BadStorage.xex.dll`); `launch.ini`, `Xbdm.xex`, `JRPC2.xex` in the root |
| FreeMyXe (optional payload) | GitHub `FreeMyXe/FreeMyXe` | **everything** in `BadUpdatePayload/` (`FreeMyXe.xex` becomes `default.xex`; `FreeMyXe.ini`, `xell-2f.bin`, `BadStorage.dll` beside it), as its own docs say |
| ABadAvatar (optional entry, **beta**) | GitHub `shutterbug2000/ABadAvatar` | `BadUpdatePayload/`, `Content/...` in the root; needs a payload |
| Simple 360 NAND Flasher, read-only (optional) | GitHub `alex-free/XDK_Projects` | `Apps/Simple360NANDFlasher/` - dump only; writing NAND on Bad Update can brick |
| Rock Band Blitz arcade trial | [Internet Archive](https://archive.org/details/rbblitz-trial) (delisted free XBLA demo) | `Content/0000000000000000/5841122D/000D0000/` so Games can launch it without a disc |
| XexMenu 1.2 (optional) | **you** download it (the host blocks scripted downloads) and use *Import* | `Apps/XexMenu/` |

Keeping the stock `default.xex` is only for **boot-animation recovery** (hold Y + A on Rock Band Blitz "Press A To Start").

### Tested versus latest

*Tested versions* (the default) are the exact releases this app's layouts were checked against, each verified by a SHA-256 pinned in the source, with no GitHub API calls (so no rate limit, and it works offline once cached). *Latest from GitHub* fetches the newest releases; the zip layout is checked before anything is written, and if upstream changed it you get a clear message and a one-click switch back to tested versions instead of a broken stick. Anonymous GitHub API use is limited to 60 requests/hour; set a `GITHUB_TOKEN` environment variable to raise it (it is only ever sent to `api.github.com`).

A checksum published next to a file proves it arrived intact, not that the project is trustworthy; only the pins in `badupdateprep/catalog.py` are an independent anchor.

## Safety

- It **never formats** a drive and only lists removable media by default. System drives (`/`, `/boot`, `/home`, your Windows system drive) and the app's own data folder are refused.
- It only adds or replaces its own files. A hidden `.wlfryt-prep.json` on the stick records what it wrote, so a later run removes only *its own* leftovers - never your other files and never XeUnshackle's MAC-address backup in `BadUpdatePayload/`.
- If you customised `launch.ini` (XeUnshackle's DashLaunch plugins) or `FreeMyXe.ini`, your copy is **kept**. If you chose a hotkey, your old `launch.ini` is saved as `launch.ini.bak-<date>` before the edited one is written; the exact change is shown as a diff.
- Files are written to a temporary name, flushed, then renamed, so a pulled stick never leaves a half-written `default.xex`.

## Where files live

Downloads, staging and settings are under `~/.wlfrit-badupdate` if that folder already exists, otherwise `%LOCALAPPDATA%\WlfRyt-BadUpdate-Prep` (Windows), `~/Library/Application Support/WlfRyt-BadUpdate-Prep` (macOS) or `~/.cache/wlfryt-badupdate-prep` (Linux). Set `WLFRYT_HOME` to use another folder. Deleting it is always safe; it only costs a re-download.

## On the console

1. System Info must show **17559**.
2. Unplug Ethernet and forget Wi-Fi. Running this on Xbox Live can ban the console.
3. Plug in **only** this USB. My Xbox > Games > Rock Band Blitz > press A (or ABadAvatar on profile select - don't sign into the exploit profile).
4. A full green ring of light = your unsigned `default.xex` is running.
5. Keep the USB plugged in (DashLaunch needs it). Power-off drops the hack. Dump NAND read-only if you included the flasher; never write NAND on Bad Update.

Details are in the in-app **Guide**, adapted from [the official wiki](https://github.com/grimdoomer/Xbox360BadUpdate/wiki/How-To-Use).
