"""macOS: enumerate /Volumes and ask `diskutil info -plist` about each (plistlib is stdlib)."""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from typing import Callable

from . import Drive, free_and_total


def _fs(info: dict) -> str:
    t = (info.get("FilesystemType") or "").lower()
    name = (info.get("FilesystemName") or "").upper()
    if t == "msdos":
        if "FAT32" in name:
            return "FAT32"
        if "FAT16" in name:
            return "FAT16"
        if "FAT12" in name:
            return "FAT12"
        return "FAT"
    if t == "exfat":
        return "EXFAT"
    return t.upper() or name


def parse_diskutil_info(plist: bytes, mount_hint: str = "") -> Drive | None:
    try:
        info = plistlib.loads(plist)
    except Exception:
        return None
    mount = info.get("MountPoint") or mount_hint
    if not mount:
        return None
    internal = bool(info.get("Internal"))
    removable = bool(info.get("RemovableMediaOrExternalDevice")) or not internal
    free = info.get("FreeSpace")
    free = int(free) if isinstance(free, int) else free_and_total(mount)[0]
    size = info.get("TotalSize") or info.get("Size")
    return Drive(
        mount=mount,
        label=info.get("VolumeName") or "",
        fs=_fs(info),
        size=int(size) if size else None,
        free=free,
        removable=removable,
        device=info.get("DeviceNode") or "",
        bus=(info.get("BusProtocol") or "").lower(),
        system=mount == "/" or bool(info.get("SystemImage")) or (internal and not removable),
    )


def collect(run: Callable = subprocess.run, volumes: Path = Path("/Volumes")) -> list[Drive]:
    out: list[Drive] = []
    try:
        entries = sorted(volumes.iterdir())
    except OSError:
        return out
    for p in entries:
        if p.is_symlink() or not p.is_dir():
            continue  # "Macintosh HD" is a symlink to /
        try:
            proc = run(["diskutil", "info", "-plist", str(p)], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            d = parse_diskutil_info(proc.stdout, str(p))
            if d:
                out.append(d)
    return out
