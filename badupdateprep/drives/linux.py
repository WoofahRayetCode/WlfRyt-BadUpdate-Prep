"""Linux: `lsblk -J` (preferred) with a /proc/mounts fallback. Never mounts anything and never uses sudo."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import Drive, free_and_total, looks_like_system_mount, normalize_fs

FULL_COLUMNS = "NAME,PATH,RM,HOTPLUG,TRAN,TYPE,SIZE,FSTYPE,FSVER,LABEL,MOUNTPOINTS,FSAVAIL"
MIN_COLUMNS = "NAME,PATH,RM,HOTPLUG,TRAN,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINT"
_REMOVABLE_MOUNT_PREFIXES = ("/media/", "/run/media/", "/mnt/usb")


def _truthy(v) -> bool:
    return v in (True, 1, "1", "true", "True")


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_lsblk(text: str) -> list[Drive]:
    data = json.loads(text)
    out: list[Drive] = []

    def walk(node: dict, inherited: dict) -> None:
        rm = _truthy(node.get("rm")) or inherited.get("rm", False)
        hot = _truthy(node.get("hotplug")) or inherited.get("hotplug", False)
        tran = node.get("tran") or inherited.get("tran") or ""
        removable = rm or hot or tran == "usb"
        fstype = node.get("fstype")
        mounts = node.get("mountpoints")
        if mounts is None:  # old lsblk: singular key
            mounts = [node.get("mountpoint")]
        mounts = [m for m in mounts if m and not m.startswith("[")]
        if fstype and fstype not in ("swap", "crypto_LUKS", "LVM2_member", "linux_raid_member"):
            if mounts:
                for m in mounts:
                    out.append(
                        Drive(
                            mount=m,
                            label=node.get("label") or "",
                            fs=normalize_fs(fstype, node.get("fsver")),
                            size=_int(node.get("size")),
                            free=_int(node.get("fsavail")) if _int(node.get("fsavail")) is not None else free_and_total(m)[0],
                            removable=removable,
                            device=node.get("path") or ("/dev/" + (node.get("name") or "")),
                            bus=tran,
                            system=looks_like_system_mount(m),
                        )
                    )
            elif removable:
                out.append(
                    Drive(
                        mount="",
                        label=node.get("label") or "",
                        fs=normalize_fs(fstype, node.get("fsver")),
                        size=_int(node.get("size")),
                        free=None,
                        removable=True,
                        device=node.get("path") or ("/dev/" + (node.get("name") or "")),
                        bus=tran,
                        mounted=False,
                    )
                )
        for child in node.get("children") or []:
            walk(child, {"rm": rm, "hotplug": hot, "tran": tran})

    for dev in data.get("blockdevices", []):
        walk(dev, {})
    return out


@dataclass(frozen=True)
class MountEntry:
    device: str
    mount: str
    fstype: str


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_proc_mounts(text: str) -> list[MountEntry]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            out.append(MountEntry(_unescape(parts[0]), _unescape(parts[1]), parts[2]))
    return out


_FS_OF_INTEREST = {"vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk", "ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs"}


def drives_from_mounts(entries: list[MountEntry]) -> list[Drive]:
    out = []
    for e in entries:
        if e.fstype not in _FS_OF_INTEREST or not e.device.startswith("/dev/"):
            continue
        free, total = free_and_total(e.mount)
        out.append(
            Drive(
                mount=e.mount,
                label=Path(e.mount).name,
                fs=normalize_fs(e.fstype),
                size=total,
                free=free,
                removable=e.mount.startswith(_REMOVABLE_MOUNT_PREFIXES),
                device=e.device,
                system=looks_like_system_mount(e.mount),
            )
        )
    return out


def collect(run: Callable = subprocess.run, read_text: Callable[[str], str] | None = None) -> list[Drive]:
    for cols in (FULL_COLUMNS, MIN_COLUMNS):
        try:
            proc = run(["lsblk", "-J", "-b", "-o", cols], capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            break
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                return parse_lsblk(proc.stdout)
            except (ValueError, KeyError):
                continue
    try:
        text = read_text("/proc/mounts") if read_text else Path("/proc/mounts").read_text("utf-8", "replace")
    except OSError:
        return []
    return drives_from_mounts(parse_proc_mounts(text))
