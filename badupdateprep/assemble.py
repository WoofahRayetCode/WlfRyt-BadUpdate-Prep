"""Build the staged USB tree from an `InstallPlan`.

Members stream straight from their zip into `.build-*/` (no intermediate extraction), then the finished tree is
swapped in as `current/`. A failed or cancelled build leaves the previous staging untouched. A fingerprint over
(options, plan, source checksums, launch.ini edits, ASSEMBLER_REV) lets the UI tell whether staging is stale.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .errors import AssembleError
from .launch_ini import IniDoc, IniEdit
from .options import Options
from .plan import InstallPlan
from .progress import CancelToken
from .sources import Source

ASSEMBLER_REV = 1  # bump when the layout logic changes. NOT the app version: build.ps1 stamps that.
MANIFEST = "_manifest.json"
CURRENT = "current"
CHUNK = 1024 * 1024

BuildProgress = Callable[[int, int, str], None]  # (bytes_done, bytes_total, current dest)


@dataclass(frozen=True)
class StagingInfo:
    path: Path
    fingerprint: str
    files: tuple[tuple[str, int], ...]  # (usb-relative path, size)
    total_bytes: int
    edited: tuple[str, ...] = ()  # staged files this app modified on purpose (e.g. launch.ini hotkey)


def fingerprint(
    plan: InstallPlan,
    opts: Options,
    source_sha: Mapping[str, str],
    ini_edits: Sequence[IniEdit] = (),
) -> str:
    blob = {
        "rev": ASSEMBLER_REV,
        "opts": opts.to_dict(),
        "plan": sorted((e.dest, e.src_key, e.member, e.size, e.overrides) for e in plan.entries),
        "src": sorted(source_sha.items()),
        "ini": [(e.section, e.key, e.value) for e in ini_edits],
    }
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()


def _safe_join(root: Path, rel: str) -> Path:
    out = (root / rel).resolve()
    if root.resolve() not in out.parents:
        raise AssembleError(f"Refusing to write outside the staging folder: {rel}")
    return out


def build_staging(
    plan: InstallPlan,
    sources: Mapping[str, Source],
    staging_root: Path,
    *,
    fingerprint_value: str,
    ini_edits: Sequence[IniEdit] = (),
    progress: BuildProgress | None = None,
    cancel: CancelToken | None = None,
) -> StagingInfo:
    staging_root.mkdir(parents=True, exist_ok=True)
    # clean up leftovers from crashed builds
    for stale in staging_root.glob(".build-*"):
        shutil.rmtree(stale, ignore_errors=True)
    for stale in staging_root.glob(".old-*"):
        shutil.rmtree(stale, ignore_errors=True)
    build = staging_root / f".build-{os.getpid()}-{int(time.time())}"
    build.mkdir()
    total = plan.total_bytes
    done = 0
    transforms = {}
    if ini_edits:
        transforms["launch.ini"] = lambda data: IniDoc.parse(data).apply(list(ini_edits)).to_bytes()
    try:
        files: list[tuple[str, int]] = []
        for e in plan.entries:
            if cancel:
                cancel.check()
            out = _safe_join(build, e.dest)
            out.parent.mkdir(parents=True, exist_ok=True)
            src = sources[e.src_key]
            written = 0
            tf = transforms.get(e.dest.lower())
            with src.open(e.member) as fin:
                if tf:
                    data = tf(fin.read())
                    out.write_bytes(data)
                    written = len(data)
                    done += e.size
                else:
                    with out.open("wb") as fout:
                        while True:
                            if cancel:
                                cancel.check()
                            chunk = fin.read(CHUNK)
                            if not chunk:
                                break
                            fout.write(chunk)
                            written += len(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total, e.dest)
                    if written != e.size:
                        raise AssembleError(f"{e.dest}: wrote {written} bytes but the archive says {e.size}.")
            files.append((e.dest, written))
            if progress:
                progress(done, total, e.dest)
        info_files = tuple(sorted(files))
        manifest = {
            "fingerprint": fingerprint_value,
            "rev": ASSEMBLER_REV,
            "files": info_files,
            "total_bytes": sum(s for _, s in info_files),
            "edited": sorted(e.dest for e in plan.entries if e.dest.lower() in transforms),
            "warnings": plan.warnings,
        }
        (build / MANIFEST).write_text(json.dumps(manifest, indent=1), "utf-8")
    except BaseException:
        shutil.rmtree(build, ignore_errors=True)
        raise

    current = staging_root / CURRENT
    if current.exists():
        old = staging_root / f".old-{os.getpid()}-{int(time.time())}"
        os.replace(current, old)
        os.replace(build, current)
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.replace(build, current)
    edited = tuple(sorted(e.dest for e in plan.entries if e.dest.lower() in transforms))
    return StagingInfo(current, fingerprint_value, info_files, sum(s for _, s in info_files), edited)


def load_staging(staging_root: Path) -> StagingInfo | None:
    cur = staging_root / CURRENT
    try:
        m = json.loads((cur / MANIFEST).read_text("utf-8"))
        files = tuple((str(p), int(s)) for p, s in m["files"])
        return StagingInfo(cur, m["fingerprint"], files, int(m["total_bytes"]), tuple(m.get("edited", ())))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def staging_is_current(info: StagingInfo | None, fp: str) -> bool:
    """Fingerprint matches and every staged file still exists with the right size."""
    if info is None or info.fingerprint != fp:
        return False
    for rel, size in info.files:
        p = info.path / rel
        try:
            if p.stat().st_size != size:
                return False
        except OSError:
            return False
    return True
