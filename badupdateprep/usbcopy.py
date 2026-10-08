"""Copy the staged tree to the stick safely, then read it back.

* Own streaming copy (not shutil.copy2): copystat/chmod commonly raises PermissionError on Linux vfat mounts.
* Each file goes to a temp name, is fsync'd, then os.replace'd - an interrupted copy never leaves a truncated default.xex.
* sha256 is computed from the source stream while copying; verify_usb() re-reads the stick and compares.
* Files already on the stick with identical content are verified by hash and skipped (fast re-runs, no temp-file space).

The stick is UNTRUSTED input. `.wlfryt-prep.json` on it records {path: sha256} of what this app wrote, and is used only to
clean up after ourselves. A recorded path is honoured only if it is a plain relative path that stays inside the stick, and a
file is removed only if it is still byte-for-byte what we wrote: never a file the user changed (a launch.ini with their
DashLaunch plugins), never XeUnshackle's MAC-address backup, never anything outside the stick.
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
from .sources import normalize_member

MARKER = ".wlfryt-prep.json"
MARKER_VERSION = 2
# Files users customise on the stick (XeUnshackle's README tells them to keep their DashLaunch plugins in launch.ini).
USER_CONFIG = ("launch.ini", "badupdatepayload/freemyxe.ini")
CHUNK = 1024 * 1024
TMP_SUFFIX = ".wlfryt-tmp"
SMALL_FILE = 4 * 1024 * 1024

CopyProgress = Callable[[int, int, str], None]  # (bytes_done, bytes_total, rel path)


@dataclass(frozen=True)
class CopyPlan:
    write: tuple[tuple[str, int], ...]  # (rel, size) every staged file we manage (user-kept config files excluded)
    replace: tuple[str, ...]  # of those, already on the stick and will really be rewritten
    stale: tuple[str, ...]  # recorded as ours, no longer shipped: removed at copy time IF still unmodified
    preserved: int | None  # files left alone; None = not counted (walking a whole USB stick is slow)
    bytes_needed: int  # bytes that will really be written
    bytes_replaced: int  # sizes of the files being overwritten
    kept: tuple[str, ...] = ()  # user config files already on the stick that we leave alone
    backups: tuple[str, ...] = ()  # user config files we deliberately changed: old copy saved as <name>.bak-<time>
    unchanged: tuple[str, ...] = ()  # already on the stick, identical per the marker: hashed and skipped at copy time
    bytes_total: int = 0  # progress denominator: everything written or checked
    growth: int = 0  # net extra space the stick needs
    peak_extra: int = 0  # temp-file headroom while the largest replaced file is being written

    @property
    def space_needed(self) -> int:
        return self.growth + self.peak_extra


@dataclass
class CopyResult:
    written: dict[str, str] = field(default_factory=dict)  # newly written: rel -> sha256 (to be verified)
    skipped: dict[str, str] = field(default_factory=dict)  # already identical on the stick (hashed during the copy)
    removed: tuple[str, ...] = ()
    left_alone: tuple[str, ...] = ()  # stale candidates we did NOT remove because they changed since we wrote them

    @property
    def count(self) -> int:
        return len(self.written) + len(self.skipped)


@dataclass
class VerifyReport:
    checked: int = 0
    bad: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.bad


# --- marker -----------------------------------------------------------------------------------------
def _safe_target(usb: Path, rel: object) -> tuple[str, Path] | None:
    """(normalised rel, absolute path) if `rel` is a plain relative path that stays inside the stick, else None."""
    if not isinstance(rel, str):
        return None
    try:
        norm = normalize_member(rel)
    except AssembleError:
        return None
    if not norm or norm.casefold() == MARKER.casefold():
        return None
    target = usb / norm
    try:
        if usb.resolve() not in target.resolve().parents:
            return None
    except OSError:
        return None
    return norm, target


def _read_marker(usb: Path) -> dict[str, str]:
    """{rel: sha256} of what this app wrote earlier. Anything malformed or unsafe is dropped, never trusted."""
    try:
        data = json.loads((usb / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, dict):
        items = list(files.items())
    elif isinstance(files, list):  # v1 markers: names only -> never auto-deleted (no hash to prove they're unchanged)
        items = [(f, "") for f in files]
    else:
        return {}
    out: dict[str, str] = {}
    for rel, sha in items:
        safe = _safe_target(usb, rel)
        if safe is None or not isinstance(sha, str):
            continue
        out[safe[0]] = sha.lower() if len(sha) == 64 else ""
    return out


def _set_entry(entries: dict[str, str], rel: str, sha: str) -> None:
    for k in [k for k in entries if k.casefold() == rel.casefold()]:
        del entries[k]
    entries[rel] = sha


def _forget(entries: dict[str, str], rel: str) -> None:
    for k in [k for k in entries if k.casefold() == rel.casefold()]:
        del entries[k]


def _write_marker(usb: Path, entries: dict[str, str], fingerprint: str) -> None:
    marker = usb / MARKER
    tmp = marker.with_name(MARKER + TMP_SUFFIX)
    try:
        tmp.write_text(
            json.dumps(
                {"version": MARKER_VERSION, "fingerprint": fingerprint, "written": int(time.time()), "files": dict(sorted(entries.items()))},
                indent=1,
            ),
            "utf-8",
        )
        os.replace(tmp, marker)
    except OSError:
        pass  # not fatal: the next copy simply won't remove leftovers


# --- planning ---------------------------------------------------------------------------------------
def _is_internal(rel: str) -> bool:
    return rel == MANIFEST or rel.startswith("_") or rel == MARKER


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.stat().st_size < SMALL_FILE and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _small_sha(path: Path) -> str | None:
    try:
        if path.stat().st_size >= SMALL_FILE:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def plan_copy(st: StagingInfo, usb: Path) -> CopyPlan:
    """Cheap and side-effect free (safe on the UI thread): stats and small config reads only, never a walk of the stick."""
    if not usb.exists() or not usb.is_dir():
        raise AssembleError(f"USB path does not exist: {usb}")
    previous = _read_marker(usb)
    prev_cf = {k.casefold(): v for k, v in previous.items()}
    edited = {e.casefold() for e in st.edited}
    staged_hash = st.hash_map()
    files = [(rel, size) for rel, size in st.files if not _is_internal(rel)]

    kept: list[str] = []
    backups: list[str] = []
    for rel, _size in files:
        if rel.casefold() not in USER_CONFIG:
            continue
        dst = usb / rel
        if not dst.is_file() or _same_bytes(st.path / rel, dst):
            continue
        recorded = prev_cf.get(rel.casefold())
        if recorded and _small_sha(dst) == recorded:
            continue  # we wrote this exact file and nobody changed it since: a normal overwrite
        (backups if rel.casefold() in edited else kept).append(rel)

    skip = {k.casefold() for k in kept}
    write = tuple((rel, size) for rel, size in files if rel.casefold() not in skip)
    wanted = {rel.casefold() for rel, _ in write} | skip  # a kept config file is not stale

    replace: list[str] = []
    unchanged: list[str] = []
    needed = replaced = total = growth = peak = 0
    for rel, size in write:
        total += size
        p = usb / rel
        if p.is_file():
            try:
                cur = p.stat().st_size
            except OSError:
                cur = -1
            rec = prev_cf.get(rel.casefold())
            if cur == size and rec and rec == staged_hash.get(rel):
                unchanged.append(rel)  # same bytes as last time: nothing to write, no temp space needed
                continue
            replace.append(rel)
            replaced += max(cur, 0)
            growth += max(0, size - max(cur, 0))
            peak = max(peak, size)  # the new copy exists next to the old one until the rename
        else:
            growth += size
        needed += size

    stale = tuple(sorted(r for r, sha in previous.items() if sha and r.casefold() not in wanted and (usb / r).is_file()))
    return CopyPlan(
        write, tuple(replace), stale, None, needed, replaced, tuple(kept), tuple(backups), tuple(unchanged), total, growth, peak
    )


# --- copying ----------------------------------------------------------------------------------------
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


def _hash_file(path: Path, on_bytes: Callable[[int], None] | None = None, cancel: CancelToken | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            if cancel:
                cancel.check()
            chunk = f.read(CHUNK)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))


def _remove_empty_parents(usb: Path, start: Path) -> None:
    root = usb.resolve()
    parent = start.parent
    while True:
        try:
            if parent.resolve() == root or root not in parent.resolve().parents:
                return
            parent.rmdir()  # only succeeds when empty
        except OSError:
            return
        parent = parent.parent


def copy_to_usb(
    plan: CopyPlan,
    st: StagingInfo,
    usb: Path,
    *,
    progress: CopyProgress | None = None,
    cancel: CancelToken | None = None,
) -> CopyResult:
    if not usb.is_dir():
        raise AssembleError(f"USB path does not exist: {usb}")
    entries = _read_marker(usb)
    recorded_before = dict(entries)
    staged = st.hash_map()
    unchanged = set(plan.unchanged)
    total = plan.bytes_total or plan.bytes_needed
    done = 0
    result = CopyResult()
    removed: list[str] = []
    left: list[str] = []
    stamp = time.strftime("%Y%m%d-%H%M%S")

    def bump(n: int, rel: str) -> None:
        nonlocal done
        done += n
        if progress:
            progress(done, total, rel)

    try:
        for rel in plan.backups:
            old = usb / rel
            try:
                shutil.copyfile(old, old.with_name(f"{old.name}.bak-{stamp}"))
            except OSError as exc:
                raise AssembleError(f"Couldn't back up your existing {rel} before replacing it: {exc}") from exc
        for rel in plan.kept:
            _forget(entries, rel)  # it is the user's file now; never ours to clean up

        for rel, size in plan.write:
            if cancel:
                cancel.check()
            src = st.path / rel
            dst = usb / rel
            if rel in unchanged:
                try:
                    sha = _hash_file(dst, lambda n, r=rel: bump(n, r), cancel)
                except OSError:
                    sha = ""
                if sha and sha == staged.get(rel):
                    result.skipped[rel] = sha
                    _set_entry(entries, rel, sha)
                    continue
                done -= min(done, size)  # it differed after all: rewrite it and count the bytes again
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
                        bump(len(chunk), rel)
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
            result.written[rel] = h.hexdigest()
            _set_entry(entries, rel, result.written[rel])

        for rel in plan.stale:
            safe = _safe_target(usb, rel)
            want = recorded_before.get(safe[0]) if safe else None
            if safe is None or not want:
                continue
            try:
                unchanged_since = safe[1].is_file() and _hash_file(safe[1], cancel=cancel) == want
            except OSError:
                unchanged_since = False
            if not unchanged_since:
                left.append(rel)  # the user changed it (or we can't read it): it's theirs now
                _forget(entries, rel)
                continue
            try:
                safe[1].unlink()
            except OSError:
                left.append(rel)
                continue
            removed.append(rel)
            _forget(entries, rel)
            _remove_empty_parents(usb, safe[1])
    finally:
        # Written even when cancelled or failed, so files copied so far are still recognised as ours next time.
        _write_marker(usb, entries, st.fingerprint)
        _fsync_dir(usb)
    result.removed = tuple(removed)
    result.left_alone = tuple(left)
    return result


# --- verification -----------------------------------------------------------------------------------
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

    def bump(n: int, rel: str) -> None:
        nonlocal done
        done += n
        if progress:
            progress(done, total, rel)

    for rel, want in written.items():
        if cancel:
            cancel.check()
        p = usb / rel
        _drop_cache(p)
        try:
            got = _hash_file(p, lambda n, r=rel: bump(n, r), cancel)
        except OSError as exc:
            rep.bad.append((rel, f"unreadable: {exc}"))
            continue
        rep.checked += 1
        if got != want:
            rep.bad.append((rel, "contents differ from what was written"))
    return rep
