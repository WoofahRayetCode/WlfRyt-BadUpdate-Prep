"""Find the USB stick on any OS and say, in plain terms, whether it is safe and suitable to write to.

`list_drives()` runs subprocesses / system calls, so call it from a worker thread, never the Tk thread.
Default is removable media only: a stick is never silently confused with an internal disk or the EFI partition.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Literal

GB = 1024**3
FREE_MARGIN = 2 * 1024 * 1024  # leave a little slack for FAT overhead


@dataclass(frozen=True)
class Drive:
    mount: str  # root path to copy into ('' when not mounted)
    label: str
    fs: str  # normalised upper-case: FAT32, FAT16, FAT, EXFAT, NTFS, EXT4, ... ('' unknown)
    size: int | None
    free: int | None
    removable: bool
    device: str = ""
    bus: str = ""
    system: bool = False
    mounted: bool = True

    @property
    def display(self) -> str:
        name = self.label or Path(self.mount).name or self.mount or self.device
        return f"{name} ({self.mount or 'not mounted'})"


@dataclass(frozen=True)
class Finding:
    level: Literal["error", "warn", "info"]
    text: str


def mb(n: float) -> int:
    return int(n / (1024 * 1024)) + 1


def normalize_fs(fstype: str | None, fsver: str | None = None) -> str:
    t = (fstype or "").lower()
    v = (fsver or "").upper()
    if t in ("vfat", "msdos", "fat", "fat32", "fat16"):
        if "32" in v or t == "fat32":
            return "FAT32"
        if "16" in v or t == "fat16":
            return "FAT16"
        if "12" in v:
            return "FAT12"
        return "FAT"
    if t in ("exfat",):
        return "EXFAT"
    if t in ("ntfs", "ntfs3", "ntfs-3g"):
        return "NTFS"
    return t.upper()


_SYSTEM_PREFIXES = (
    "/boot", "/efi", "/home", "/usr", "/var", "/opt", "/nix", "/etc", "/srv", "/root", "/tmp", "/snap",
    "/proc", "/sys", "/dev", "/System", "/Library", "/Users", "/private",
)
_USER_MEDIA_PREFIXES = ("/media/", "/run/media/", "/mnt/", "/Volumes/")


def looks_like_system_mount(mount: str) -> bool:
    m = mount.rstrip("/") or "/"
    if m == "/":
        return True
    if m.startswith(_USER_MEDIA_PREFIXES):
        return False
    if m == "/run" or m.startswith("/run/"):
        return True
    return any(m == p or m.startswith(p + "/") for p in _SYSTEM_PREFIXES)


def is_windows_system_path(path: str, system_drive: str | None = None) -> bool:
    """True for anything on the Windows system drive (C:\\...). Pure string logic so it is testable anywhere."""
    sysd = (system_drive or os.environ.get("SystemDrive") or "C:").upper().rstrip("\\/")
    drive = path[:2].upper() if len(path) >= 2 and path[1] == ":" else ""
    return bool(drive) and drive == sysd


def free_and_total(mount: str) -> tuple[int | None, int | None]:
    try:
        u = shutil.disk_usage(mount)
        return u.free, u.total
    except OSError:
        return None, None


def list_drives(include_fixed: bool = False) -> list[Drive]:
    """All candidate drives, newest-OS-specific detection. Fixed disks are hidden unless `include_fixed`."""
    if sys.platform == "win32":
        from . import windows

        drives = windows.collect(include_fixed=include_fixed)
    elif sys.platform == "darwin":
        from . import macos

        drives = macos.collect()
    else:
        from . import linux

        drives = linux.collect()
    out = [d for d in drives if include_fixed or d.removable]
    out.sort(key=lambda d: (not d.removable, d.mount))
    return out


def describe_path(path: Path, drives: Iterable[Drive]) -> Drive:
    """Describe an arbitrary folder (from Browse) using the drive that contains it, if we know it."""
    p = str(path.resolve())
    best: Drive | None = None
    for d in drives:
        if d.mount and (p == d.mount or p.startswith(d.mount.rstrip("/\\") + os.sep)):
            if best is None or len(d.mount) > len(best.mount):
                best = d
    free, total = free_and_total(p)
    if best is None:
        system = looks_like_system_mount(p) if sys.platform != "win32" else is_windows_system_path(p)
        return Drive(p, Path(p).name or p, "", total, free, False, system=system)
    return replace(best, mount=p, free=free if free is not None else best.free)


def assess(d: Drive, needed: int = 0, replaced: int = 0, protected: Iterable[Path] = ()) -> list[Finding]:
    """Everything the user should know before writing to `d`. Any 'error' blocks the copy."""
    out: list[Finding] = []
    if not d.mounted or not d.mount:
        return [Finding("error", "This stick isn't mounted. Mount it in your file manager, then press Refresh.")]
    if d.system:
        out.append(Finding("error", "This looks like a system or boot drive. Pick your USB stick instead."))
    mount = Path(d.mount)
    for prot in protected:
        try:
            prot_r = prot.resolve()
            if prot_r == mount.resolve() or mount.resolve() in prot_r.parents:
                out.append(Finding("error", "This app's own data (or your home folder) lives on this drive. Pick the USB stick."))
                break
        except OSError:
            pass
    fs = d.fs
    if fs == "FAT32":
        out.append(Finding("info", "FAT32 - good."))
    elif fs in ("FAT", "FAT16", "FAT12"):
        out.append(Finding("warn", f"{fs}: the Xbox 360 expects FAT32. It may still work on a small stick, but FAT32 is safest."))
    elif fs == "":
        out.append(Finding("warn", "Can't tell the file system. Make sure it is FAT32."))
    else:
        big = " Drives over 32 GB: format on the 360 dashboard or with Rufus/GUIformat." if (d.size or 0) > 32 * GB else ""
        out.append(Finding("error", f"{fs} won't work - the Xbox 360 needs FAT32. This app never formats.{big}"))
    net = max(0, needed - replaced)
    if d.free is None:
        out.append(Finding("warn", "Couldn't read free space."))
    elif d.free < net + FREE_MARGIN:
        out.append(Finding("error", f"Not enough free space: need {mb(net)} MB more, {mb(d.free)} MB free."))
    if not d.removable and not d.system:
        out.append(Finding("warn", "This isn't marked as removable. Double-check it is your USB stick."))
    return out


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
