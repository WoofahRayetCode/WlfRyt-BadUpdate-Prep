"""Copy the staged tree to the stick safely, then read it back.

* Own streaming copy (not shutil.copy2): copystat/chmod commonly raises PermissionError on Linux vfat mounts.
* Each file goes to a temp name, is fsync'd, then os.replace'd - an interrupted copy never leaves a truncated default.xex.
* sha256 is computed from the source stream while copying; verify_usb() re-reads the stick and compares.
* Stale-file cleanup is manifest-driven: only files THIS app wrote earlier (recorded in `.wlfryt-prep.json` on the stick)
  and that the new build no longer ships are removed. Anything else - e.g. XeUnshackle's MAC-address backup in
  BadUpdatePayload/ - is never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .assemble import MANIFEST, StagingInfo
from .errors import AssembleError
from .progress import CancelToken

MARKER = ".wlfryt-prep.json"
# Files users customise on the stick (XeUnshackle's README tells them to keep their DashLaunch plugins in launch.ini).
USER_CONFIG = ("launch.ini", "badupdatepayload/freemyxe.ini")
CHUNK = 1024 * 1024
TMP_SUFFIX = ".wlfryt-tmp"

CopyProgress = Callable[[int, int, str], None]  # (bytes_done, bytes_total, rel path)


@dataclass(frozen=True)
class CopyPlan:
    write: tuple[tuple[str, int], ...]  # (rel, size) every staged file
    replace: tuple[str, ...]  # of those, already on the stick (will be overwritten)
    stale: tuple[str, ...]  # written by this app before, not in this build -> will be removed
    preserved: int  # files on the stick we will not touch
    bytes_needed: int  # sum of everything we write
    bytes_replaced: int  # sizes of the files being overwritten (net space = needed - replaced)
    kept: tuple[str, ...] = ()  # user config files already on the stick that we leave alone
    backups: tuple[str, ...] = ()  # user config files we deliberately changed: old copy saved as <name>.bak-<time>


@dataclass
class VerifyReport:
    checked: int = 0
    bad: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.bad


def _read_marker(usb: Path) -> set[str]:
    try:
        data = json.loads((usb / MARKER).read_text("utf-8"))
        return {str(p) for p in data.get("files", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def _is_internal(rel: str) -> bool:
    return rel == MANIFEST or rel.startswith("_") or rel == MARKER


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.stat().st_size < 4 * 1024 * 1024 and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def plan_copy(st: StagingInfo, usb: Path) -> CopyPlan:
    if not usb.exists() or not usb.is_dir():
        raise AssembleError(f"USB path does not exist: {usb}")
    edited = {e.casefold() for e in st.edited}
    kept: list[str] = []
    backups: list[str] = []
    for rel, _size in st.files:
        if rel.casefold() in USER_CONFIG and (usb / rel).is_file() and not _same_bytes(st.path / rel, usb / rel):
            (backups if rel.casefold() in edited else kept).append(rel)
    skip = {k.casefold() for k in kept}
    write = tuple((rel, size) for rel, size in st.files if not _is_internal(rel) and rel.casefold() not in skip)
    wanted = {rel.casefold() for rel, _ in write} | skip  # a kept config file is not stale
    replace: list[str] = []
    replaced_bytes = 0
    for rel, _ in write:
        p = usb / rel
        if p.is_file():
            replace.append(rel)
            try:
                replaced_bytes += p.stat().st_size
            except OSError:
                pass
    previously = _read_marker(usb)
    stale = tuple(sorted(r for r in previously if r.casefold() not in wanted and (usb / r).is_file()))
    touched = {r.casefold() for r in replace} | {r.casefold() for r in stale}
    preserved = 0
    try:
        for p in usb.rglob("*"):
            if p.is_file():
                rel = p.relative_to(usb).as_posix()
                if rel != MARKER and rel.casefold() not in touched:
                    preserved += 1
    except OSError:
        pass
    return CopyPlan(write, tuple(replace), stale, preserved, sum(s for _, s in write), replaced_bytes, tuple(kept), tuple(backups))


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return  # not possible on Windows; harmless
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fsync_file(f) -> None:
    f.flush()
    try:
        import fcntl  # macOS: real flush to the device

        if hasattr(fcntl, "F_FULLFSYNC"):
            fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
            return
    except (ImportError, OSError):
        pass
    try:
        os.fsync(f.fileno())
    except OSError:
        pass


def copy_to_usb(
    plan: CopyPlan,
    st: StagingInfo,
    usb: Path,
    *,
    progress: CopyProgress | None = None,
    cancel: CancelToken | None = None,
) -> dict[str, str]:
    """Copy and return {rel: sha256 of what was written}."""
    if not usb.is_dir():
        raise AssembleError(f"USB path does not exist: {usb}")
    total = plan.bytes_needed
    done = 0
    written: dict[str, str] = {}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for rel in plan.backups:
        old = usb / rel
        try:
            shutil.copyfile(old, old.with_name(f"{old.name}.bak-{stamp}"))
        except OSError as exc:
            raise AssembleError(f"Couldn't back up your existing {rel} before replacing it: {exc}") from exc
    for rel, size in plan.write:
        if cancel:
            cancel.check()
        src = st.path / rel
        dst = usb / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name("." + dst.name + TMP_SUFFIX)
        h = hashlib.sha256()
        try:
            with src.open("rb") as fin, tmp.open("wb") as fout:
                while True:
                    if cancel:
                        cancel.check()
                    chunk = fin.read(CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    fout.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total, rel)
                _fsync_file(fout)
            os.replace(tmp, dst)
        except OSError as exc:
            raise AssembleError(f"Couldn't write {rel} to the USB stick: {exc}") from exc
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        try:
            st_src = src.stat()
            os.utime(dst, (st_src.st_atime, st_src.st_mtime))
        except OSError:
            pass  # vfat mounted without permission to set times is fine
        written[rel] = h.hexdigest()

    for rel in plan.stale:
        try:
            (usb / rel).unlink()
        except OSError:
            continue
        parent = (usb / rel).parent
        while parent != usb:
            try:
                parent.rmdir()  # only succeeds when empty
            except OSError:
                break
            parent = parent.parent

    marker = usb / MARKER
    tmp = marker.with_name(MARKER + TMP_SUFFIX)
    try:
        tmp.write_text(
            json.dumps({"version": 1, "fingerprint": st.fingerprint, "written": int(time.time()), "files": sorted(written)}, indent=1),
            "utf-8",
        )
        os.replace(tmp, marker)
    except OSError:
        pass  # not fatal: the next copy simply won't remove stale files
    _fsync_dir(usb)
    return written


def _drop_cache(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    finally:
        os.close(fd)


def verify_usb(
    usb: Path,
    written: dict[str, str],
    *,
    progress: CopyProgress | None = None,
    cancel: CancelToken | None = None,
) -> VerifyReport:
    """Re-read every written file from the stick and compare sha256 (best effort vs. OS caching; eject is the real flush)."""
    rep = VerifyReport()
    total = 0
    for rel in written:
        try:
            total += (usb / rel).stat().st_size
        except OSError:
            pass
    done = 0
    for rel, want in written.items():
        if cancel:
            cancel.check()
        p = usb / rel
        _drop_cache(p)
        h = hashlib.sha256()
        try:
            with p.open("rb") as f:
                while True:
                    if cancel:
                        cancel.check()
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total, rel)
        except OSError as exc:
            rep.bad.append((rel, f"unreadable: {exc}"))
            continue
        rep.checked += 1
        if h.hexdigest() != want:
            rep.bad.append((rel, "contents differ from what was written"))
    return rep
