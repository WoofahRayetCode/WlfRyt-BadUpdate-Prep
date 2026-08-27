from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .catalog import RBB_TITLE_ID

GAME_HINTS = ("rock band blitz", "rockband", "tony hawk")


class AssembleError(RuntimeError):
    pass


def merge_copy(src: Path, dest: Path) -> None:
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            merge_copy(child, dest / child.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def extract_zip(zpath: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(dest)
    return dest


def _unwrap_single_root(root: Path) -> Path:
    children = [c for c in root.iterdir() if c.name not in {".", ".."}]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return root


def find_game_folder(extracted: Path) -> Path:
    """Locate the Rock Band Blitz (or THAW) pack that contains BadUpdatePayload."""
    hits = list(extracted.rglob("BadUpdatePayload"))
    dirs = [p.parent for p in hits if p.is_dir()]
    if not dirs:
        raise AssembleError("BadUpdate zip is missing a BadUpdatePayload folder.")
    for d in dirs:
        if any(h in d.name.lower() for h in GAME_HINTS):
            return d
    return dirs[0]


def find_xex(root: Path, prefer_names: tuple[str, ...]) -> Path | None:
    lower = [n.lower() for n in prefer_names]
    for p in root.rglob("*.xex"):
        if p.name.lower() in lower:
            return p
    found = list(root.rglob("*.xex"))
    return found[0] if found else None


def list_windows_removable() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        import string
        from ctypes import create_unicode_buffer, windll

        kernel32 = windll.kernel32
        mask = kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if not mask & (1 << i):
                continue
            root = f"{letter}:\\"
            dtype = kernel32.GetDriveTypeW(root)
            if dtype not in (2, 3):
                continue
            name_buf = create_unicode_buffer(261)
            fs_buf = create_unicode_buffer(261)
            kernel32.GetVolumeInformationW(root, name_buf, 261, None, None, None, fs_buf, 261)
            label = name_buf.value or letter
            fs = (fs_buf.value or "").upper()
            if dtype == 2 or fs in {"FAT32", "FAT", "EXFAT"}:
                kind = "removable" if dtype == 2 else fs or "fixed"
                out.append((root, f"{letter}: {label} ({kind})"))
    except Exception:
        pass
    return out


def assemble_usb(
    downloads: dict[str, Path],
    staging: Path,
    *,
    entry: str = "rbb",
    payload: str = "xeunshackle",
    include_nand_tool: bool = True,
    include_rbb_trial: bool = True,
) -> Path:
    """
    Build USB root:
      BadUpdatePayload/default.xex  (payload)
      Content/                      (game save exploit)
      name.txt                      (if present)
      plus XeUnshackle extras on root (launch.ini, DashLaunch, ...)
    """
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    work = staging / "_work"
    work.mkdir()

    pack_key = "abadavatar" if entry == "avatar" else "badupdate"
    pack = downloads.get(pack_key)
    if not pack:
        raise AssembleError(f"Missing exploit pack ({pack_key}). Download first.")

    extract_zip(pack, work / "pack")
    pack_root = _unwrap_single_root(work / "pack")
    if entry == "avatar":
        src = pack_root
        if not (src / "BadUpdatePayload").exists():
            src = find_game_folder(work / "pack")
    else:
        src = find_game_folder(work / "pack")

    for child in src.iterdir():
        merge_copy(child, staging / child.name)

    payload_dir = staging / "BadUpdatePayload"
    payload_dir.mkdir(parents=True, exist_ok=True)

    if payload == "stock":
        pass
    else:
        zkey = "xeunshackle" if payload == "xeunshackle" else "freemyxe"
        pz = downloads.get(zkey)
        if not pz:
            raise AssembleError(f"Missing payload zip ({zkey}).")
        extract_zip(pz, work / "payload")
        proot = _unwrap_single_root(work / "payload")
        prefer = ("xeunshackle.xex", "freemyxe.xex", "default.xex")
        xex = find_xex(proot, prefer)
        if not xex:
            raise AssembleError(f"No .xex found inside {zkey} zip.")
        shutil.copy2(xex, payload_dir / "default.xex")
        for child in proot.iterdir():
            if child.resolve() == xex.resolve():
                continue
            merge_copy(child, staging / child.name)

    if include_nand_tool and downloads.get("nand_ro"):
        apps = staging / "Applications" / "Simple360NANDFlasher"
        extract_zip(downloads["nand_ro"], work / "nand")
        nroot = _unwrap_single_root(work / "nand")
        merge_copy(nroot, apps)

    if include_rbb_trial and downloads.get("rbb_trial"):
        extract_zip(downloads["rbb_trial"], work / "rbb")
        merge_rbb_trial(work / "rbb", staging)

    shutil.rmtree(work, ignore_errors=True)
    return staging


def merge_rbb_trial(extracted: Path, staging: Path) -> None:
    """Merge the arcade trial into USB Content/ (title 5841122D) without clobbering the save exploit."""
    root = _unwrap_single_root(extracted)
    nested = [p for p in root.glob("*.zip") if p.is_file()]
    if nested and not any(p.is_dir() and p.name.upper() == RBB_TITLE_ID for p in root.rglob("*")):
        inner = extracted / "_inner"
        extract_zip(nested[0], inner)
        root = _unwrap_single_root(inner)

    contents = [p for p in root.rglob("Content") if p.is_dir()]
    if contents:
        merge_copy(contents[0], staging / "Content")
        return

    tid_dirs = [p for p in root.rglob(RBB_TITLE_ID) if p.is_dir()]
    if not tid_dirs:
        tid_dirs = [p for p in root.rglob(RBB_TITLE_ID.lower()) if p.is_dir()]
    if tid_dirs:
        dest = staging / "Content" / "0000000000000000" / RBB_TITLE_ID
        merge_copy(tid_dirs[0], dest)
        return

    big = [p for p in root.rglob("*") if p.is_file() and p.stat().st_size > 1_000_000]
    if big:
        dest = staging / "Content" / "0000000000000000" / RBB_TITLE_ID / "000D0000"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(big[0], dest / big[0].name)
        return

    raise AssembleError("Rock Band Blitz trial zip did not contain a 360 Content package.")


def copy_tree_to_usb(staging: Path, usb_root: Path) -> None:
    if not usb_root.exists():
        raise AssembleError(f"USB path does not exist: {usb_root}")
    for child in staging.iterdir():
        if child.name.startswith("_"):
            continue
        merge_copy(child, usb_root / child.name)
