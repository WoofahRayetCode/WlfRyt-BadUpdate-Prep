"""Windows: logical drives via kernel32. `ctypes.windll` is touched only inside RealWin32 so this module imports anywhere."""
from __future__ import annotations

import os
import string
from typing import Protocol

from . import Drive

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
# GetVolumeInformation reports FAT12/FAT16 as plain "FAT".
_WIN_FS = {"FAT32": "FAT32", "FAT": "FAT", "EXFAT": "EXFAT", "NTFS": "NTFS"}


class Win32Api(Protocol):
    def logical_drives_mask(self) -> int: ...

    def drive_type(self, root: str) -> int: ...

    def volume_info(self, root: str) -> tuple[str, str]: ...  # (label, filesystem)

    def disk_space(self, root: str) -> tuple[int | None, int | None]: ...  # (free, total)


class RealWin32:
    def __init__(self) -> None:
        import ctypes

        self._ct = ctypes
        self._k = ctypes.windll.kernel32  # type: ignore[attr-defined]

    def logical_drives_mask(self) -> int:
        return int(self._k.GetLogicalDrives())

    def drive_type(self, root: str) -> int:
        return int(self._k.GetDriveTypeW(root))

    def volume_info(self, root: str) -> tuple[str, str]:
        ct = self._ct
        name, fs = ct.create_unicode_buffer(261), ct.create_unicode_buffer(261)
        ok = self._k.GetVolumeInformationW(root, name, 261, None, None, None, fs, 261)
        return (name.value, fs.value) if ok else ("", "")

    def disk_space(self, root: str) -> tuple[int | None, int | None]:
        ct = self._ct
        free, total, _ = ct.c_ulonglong(0), ct.c_ulonglong(0), ct.c_ulonglong(0)
        ok = self._k.GetDiskFreeSpaceExW(root, ct.byref(free), ct.byref(total), None)
        return (free.value, total.value) if ok else (None, None)


def collect(api: Win32Api | None = None, include_fixed: bool = False, system_drive: str | None = None) -> list[Drive]:
    api = api or RealWin32()
    sysdrive = (system_drive or os.environ.get("SystemDrive") or "C:").upper().rstrip("\\")
    mask = api.logical_drives_mask()
    out: list[Drive] = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        dtype = api.drive_type(root)
        if dtype not in (DRIVE_REMOVABLE, DRIVE_FIXED):
            continue
        if dtype == DRIVE_FIXED and not include_fixed:
            continue
        label, fs = api.volume_info(root)
        if not fs and dtype == DRIVE_REMOVABLE:
            continue  # empty card reader slot
        free, total = api.disk_space(root)
        out.append(
            Drive(
                mount=root,
                label=label,
                fs=_WIN_FS.get(fs.upper(), fs.upper()),
                size=total,
                free=free,
                removable=dtype == DRIVE_REMOVABLE,
                device=f"{letter}:",
                system=f"{letter}:" == sysdrive,
            )
        )
    return out
