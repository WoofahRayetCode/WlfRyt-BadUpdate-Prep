# WlfRyt BadUpdate Prep

Desktop helper for [grimdoomer/Xbox360BadUpdate](https://github.com/grimdoomer/Xbox360BadUpdate). It downloads the current Retail USB pack, a post-exploit payload, and (optionally) the Rock Band Blitz arcade trial, then stages a FAT32 USB tree you can copy in one shot.

The last wizard step is a **local copy** of the official How To Use wiki (LEDs, errors, Blitz troubleshooting, boot-anim recovery, FAQ). No browser required.

Bad Update is a **non-persistent** hypervisor exploit for dashboard **2.0.17559.0**. Power off and you start over. It is **not** a replacement for RGH.

**Not affiliated** with grimdoomer, XeUnshackle, FreeMyXe, Internet Archive, or Microsoft.

## Requirements

- Windows, Python 3.10+ (Tkinter included with the official installer)
- Xbox 360 on **2.0.17559.0**
- FAT32 USB (512 MB+ if you include the Blitz trial; format >32 GB sticks on the 360 dashboard or with Rufus / GUIformat)

This app **never formats** the drive.

## Run from source

```powershell
cd C:\Users\ericp\OneDrive\Documents\GitHub\WlfRyt-BadUpdate-Prep
python app.py
```

or:

```powershell
python -m badupdateprep
```

No extra pip packages.

## Build a portable .exe

```powershell
.\build.ps1
```

Or double-click `build.bat`. Output: `dist\WlfRyt-BadUpdate-Prep.exe`

Each build stamps `APP_VERSION` as `yyyy.MMdd.HHmm` in the window title, then restores source to `dev`.

```powershell
.\build.ps1 -Clean       # wipe build/ and dist/ first
.\build.ps1 -SkipTests   # skip unit tests
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

## What it downloads

| Item | Source | USB placement |
| --- | --- | --- |
| Xbox360BadUpdate Retail USB | GitHub latest `*USB*.zip` | `BadUpdatePayload/`, `Content/`, `name.txt` from the **Rock Band Blitz** folder (v1.3 dropped Tony Hawk) |
| XeUnshackle (default) | `Byrom90/XeUnshackle` | `BadUpdatePayload/default.xex` plus `launch.ini` / DashLaunch extras |
| FreeMyXe (optional) | `FreeMyXe/FreeMyXe` | `BadUpdatePayload/default.xex` |
| ABadAvatar (optional entry) | `shutterbug2000/ABadAvatar` if a release exists | USB root |
| Simple 360 NAND Flasher read-only | `alex-free/XDK_Projects` | `Applications/` — dump only; writing NAND on BadUpdate can brick |
| Rock Band Blitz arcade trial | [Internet Archive rbblitz-trial](https://archive.org/details/rbblitz-trial) (delisted free XBLA) | `Content/0000000000000000/5841122D/` so Games can launch it with no disc |

Stock `default.xex` can be kept for **boot animation recovery** (hold Y + A on Rock Band Blitz “Press A To Start”).

Cached downloads live under `%USERPROFILE%\.wlfrit-badupdate\`.

## On the console

1. System Info must show **17559**.
2. Unplug Ethernet and forget Wi-Fi. Running this on Xbox Live can ban the console.
3. Plug in the USB. My Xbox → Games → Rock Band Blitz → press A (or ABadAvatar on profile select — do not sign into the exploit profile).
4. Full green ring of light = unsigned `default.xex` is running.
5. Power-off drops the hack. Dump NAND read-only if you included the flasher; never write NAND on Bad Update.

Details are in the in-app **Guide** tab, adapted from [the official wiki](https://github.com/grimdoomer/Xbox360BadUpdate/wiki/How-To-Use).
