"""Tiny stand-ins for the real upstream zips, with the *exact internal paths* inspected on 2026-10-07.

The old tests invented layouts (e.g. `Simple360NANDFlasher.xex`); that hid a real FreeMyXe bug. Keep these in
sync with reality, not with the code under test.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

LAUNCH_INI = """\
; DashLaunch configuration (test copy, same structure as XeUnshackle's launch.ini)
; Paths are launched when the matching button is held as the dashboard starts.
[Paths]
; Example: BUT_A = Usb:\\Aurora\\Aurora.xex
BUT_A =
BUT_B =
; X button: run an application from the USB stick
BUT_X = Usb:\\Content\\0000000000000000\\C0DE9999\\00080000\\C0DE99990F586558
BUT_Y = Sfc:\\dash.xex ; system dash
Start =
Back =
LBump =
RThumb =
LThumb =
Default =
Guide =
Power =

[Plugins]
plugin1 = Usb:\\Xbdm.xex
plugin2 =
plugin3 = Usb:\\JRPC2.xex

[Settings]
nxemini = true
pingpatch = true
"""


def make_zip(path: Path, files: dict[str, bytes], dirs: tuple[str, ...] = ()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in dirs:
            zf.writestr(d if d.endswith("/") else d + "/", b"")
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def badupdate_zip(path: Path) -> Path:
    r = "Rock Band Blitz/"
    return make_zip(
        path,
        {
            r + "BadUpdatePayload/BadUpdateExploit-2ndStage.bin": b"2nd",
            r + "BadUpdatePayload/BadUpdateExploit-3rdStage.bin": b"3rd",
            r + "BadUpdatePayload/BadUpdateExploit-4thStage.bin": b"4th",
            r + "BadUpdatePayload/BadUpdateExploit-Data.bin": b"data",
            r + "BadUpdatePayload/bootanim.xex": b"bootanim",
            r + "BadUpdatePayload/default.xex": b"STOCK-default",
            r + "BadUpdatePayload/update_data.bin": b"upd",
            r + "BadUpdatePayload/xke_update.bin": b"xke",
            r + "Content/0000000000000000/5841122D/00000001/DaTArrest_Xbox360BadUpdate": b"save",
        },
        dirs=(r + "BadUpdatePayload", r + "Content", r + "Content/0000000000000000"),
    )


def xeunshackle_zip(path: Path) -> Path:
    w = "XeUnshackle-BETA-v1_03/"
    return make_zip(
        path,
        {
            w + "BadUpdatePayload/default.xex": b"XEUNSHACKLE",
            w + "BadUpdatePayload/BadStorage.xex.dll": b"badstorage",
            w + "JRPC2.xex": b"jrpc2",
            w + "Xbdm.xex": b"xbdm",
            w + "launch.ini": LAUNCH_INI.encode("latin-1"),
            w + "README - IMPORTANT.txt": b"read me",
        },
    )


def freemyxe_zip(path: Path) -> Path:  # FLAT: no wrapper directory
    return make_zip(
        path,
        {
            "FreeMyXe-1.2-xell-a36ed6b.txt": b"docs",
            "FreeMyXe.ini": b"[Auto]\nDefault=off\n",
            "FreeMyXe.xex": b"FREEMYXE",
            "xell-2f.bin": b"xell",
            "BadStorage.dll": b"bs",
        },
    )


def abadavatar_zip(path: Path) -> Path:  # no default.xex inside
    return make_zip(
        path,
        {
            "BadUpdatePayload/BadUpdateExploit-2ndStage.bin": b"a2",
            "BadUpdatePayload/BadUpdateExploit-3rdStage.bin": b"a3",
            "BadUpdatePayload/BadUpdateExploit-4thStage.bin": b"a4",
            "BadUpdatePayload/update_data.bin": b"upd",
            "BadUpdatePayload/xke_update.bin": b"xke",
            "Content/E0002FF78DFBDE7B/FFFE07D1/00010000/E0002FF78DFBDE7B": b"profile-save",
        },
    )


def nand_zip(path: Path) -> Path:
    w = "simple-360-nand-flasher-v1.5b-read-only/"
    return make_zip(path, {w + "Default.xex": b"NANDFLASHER", w + "readme.txt": b"ro", w + "readme-orig.txt": b"orig"})


def trial_zip(path: Path, size: int = 5000) -> Path:  # one deep file; the only top-level dir IS Content/
    return make_zip(path, {"Content/0000000000000000/5841122D/000D0000/DD774F20C36263F2": b"T" * size})


def all_zips(td: Path) -> dict[str, Path]:
    return {
        "badupdate": badupdate_zip(td / "bu.zip"),
        "xeunshackle": xeunshackle_zip(td / "xe.zip"),
        "freemyxe": freemyxe_zip(td / "fmx.zip"),
        "abadavatar": abadavatar_zip(td / "av.zip"),
        "nand_ro": nand_zip(td / "nand.zip"),
        "rbb_trial": trial_zip(td / "trial.zip"),
    }


def xexmenu_dir(root: Path) -> Path:
    (root / "Apps_unused").mkdir(parents=True, exist_ok=True)
    (root / "default.xex").write_bytes(b"XEXMENU")
    (root / "skin.bin").write_bytes(b"skin")
    return root
