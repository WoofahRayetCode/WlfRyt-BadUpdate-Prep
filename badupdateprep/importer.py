"""Guided import of an extra the user downloaded themselves (XexMenu).

The only listed source for XexMenu blocks scripted downloads, so the app opens that page in the user's browser and
imports the file from disk: a .zip (stdlib), a folder, or a .7z (needs a system 7-Zip). No scraping, no mirrors.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths
from .errors import AssembleError
from .plan import fat_problems
from .sources import normalize_member

MAX_IMPORT_BYTES = 512 * 1024 * 1024
SEVENZ_HINT = (
    "Reading .7z files needs 7-Zip. Install it (Windows: 7-zip.org; Linux: p7zip / 7zip package; "
    "macOS: `brew install sevenzip`), or extract the archive yourself and import the folder instead."
)


class ImportProblem(RuntimeError):
    """User-facing import failure."""


@dataclass(frozen=True)
class ImportedExtra:
    key: str
    root: Path
    entry_xex: str  # relative to root, '/' separators
    source_sha256: str
    file_count: int


def imports_root(app_dir: Path | None = None) -> Path:
    return paths.imports_dir(app_dir)


def find_7z(which: Callable[[str], str | None] = shutil.which) -> str | None:
    for name in ("7z", "7zz", "7za", "7zr"):
        found = which(name)
        if found:
            return found
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                p = Path(base) / "7-Zip" / "7z.exe"
                if p.is_file():
                    return str(p)
    return None


def find_in_downloads(patterns: tuple[str, ...] = ("xexmenu*", "xexmen*")) -> list[Path]:
    """Look in the user's Downloads folder - only ever called from a button press."""
    d = Path.home() / "Downloads"
    out: list[Path] = []
    if d.is_dir():
        for pat in patterns:
            out.extend(p for p in d.glob(pat) if p.suffix.lower() in (".zip", ".7z") or p.is_dir())
    return sorted(set(out), key=lambda p: p.stat().st_mtime, reverse=True)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(_sha256_file(p).encode())
    return h.hexdigest()


def _copy_tree(src: Path, dst: Path) -> None:
    total = 0
    for p in sorted(src.rglob("*")):
        if p.is_symlink():
            raise ImportProblem(f"Refusing to import a folder containing a symlink: {p.name}")
        if not p.is_file():
            continue
        rel = normalize_member(p.relative_to(src).as_posix())
        if rel is None:
            continue
        total += p.stat().st_size
        if total > MAX_IMPORT_BYTES:
            raise ImportProblem("That folder is much larger than XexMenu should be; wrong selection?")
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out)


def _extract_zip(src: Path, dst: Path) -> None:
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise ImportProblem(f"{src.name} isn't a valid zip file.") from exc
    with zf:
        total = 0
        for info in zf.infolist():
            try:
                rel = normalize_member(info.filename)
            except AssembleError as exc:
                raise ImportProblem(str(exc)) from exc
            if rel is None or info.is_dir():
                continue
            total += info.file_size
            if total > MAX_IMPORT_BYTES:
                raise ImportProblem("That archive expands to far more than XexMenu should; wrong file?")
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as fin, out.open("wb") as fout:
                shutil.copyfileobj(fin, fout)


def _run_7z(args: list[str], run) -> subprocess.CompletedProcess:
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flash from a windowed exe
    try:
        return run(args, capture_output=True, text=True, timeout=180, **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ImportProblem(f"Couldn't run 7-Zip: {exc}") from exc


def _extract_7z(src: Path, dst: Path, run, which) -> None:
    exe = find_7z(which)
    if not exe:
        raise ImportProblem(SEVENZ_HINT)
    # Ask 7-Zip what the archive would expand to BEFORE extracting, so a hostile archive can't fill the disk.
    listing = _run_7z([exe, "l", "-slt", "--", str(src)], run)
    if listing.returncode != 0:
        raise ImportProblem(f"7-Zip couldn't read {src.name}: {(listing.stderr or listing.stdout or '').strip()[:300]}")
    total = sum(int(m.group(1)) for m in re.finditer(r"(?m)^Size = (\d+)\s*$", listing.stdout or ""))
    if total > MAX_IMPORT_BYTES:
        raise ImportProblem("That archive expands to much larger than XexMenu should; wrong file?")
    proc = _run_7z([exe, "x", "-y", f"-o{dst}", "--", str(src)], run)
    if proc.returncode != 0:
        raise ImportProblem(f"7-Zip couldn't extract {src.name}: {(proc.stderr or proc.stdout or '').strip()[:300]}")


def _unwrap(root: Path) -> Path:
    while True:
        kids = [c for c in root.iterdir() if c.name not in (".DS_Store", "__MACOSX", "Thumbs.db")]
        if len(kids) == 1 and kids[0].is_dir():
            root = kids[0]
        else:
            return root


def _discover_entry(root: Path) -> str:
    xexes = sorted(p.name for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".xex")
    if not xexes:
        deeper = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.xex"))
        if len(deeper) == 1:
            return deeper[0]
        raise ImportProblem("No .xex file found at the top of that import - is this really XexMenu?")
    if len(xexes) == 1:
        return xexes[0]
    for pref in ("default.xex", "xexmenu.xex"):
        for n in xexes:
            if n.lower() == pref:
                return n
    named = [n for n in xexes if "xexmenu" in n.lower()]
    if len(named) == 1:
        return named[0]
    raise ImportProblem(f"Several .xex files found ({', '.join(xexes)}); can't tell which one starts the app.")


def import_extra(
    key: str,
    src: Path,
    *,
    app_dir: Path | None = None,
    run=subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> ImportedExtra:
    src = Path(src)
    if not src.exists():
        raise ImportProblem(f"{src} doesn't exist.")
    base = imports_root(app_dir)
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / f".tmp-{key}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        if src.is_dir():
            _copy_tree(src, tmp)
            sha = _sha256_tree(src)
        else:
            suffix = src.suffix.lower()
            if suffix == ".zip":
                _extract_zip(src, tmp)
            elif suffix == ".7z":
                _extract_7z(src, tmp, run, which)
            else:
                raise ImportProblem("Pick a .zip, a .7z, or a folder.")
            sha = _sha256_file(src)
        for p in tmp.rglob("*"):
            if p.is_symlink():
                raise ImportProblem("The import contains a symlink; refusing it.")
        root = _unwrap(tmp)
        entry = _discover_entry(root)
        files = [p for p in root.rglob("*") if p.is_file()]
        for p in files:
            bad = fat_problems(p.relative_to(root).as_posix(), p.stat().st_size)
            if bad:
                raise ImportProblem("; ".join(bad))
        final = base / key
        shutil.rmtree(final, ignore_errors=True)
        shutil.move(str(root), str(final))
        meta = {"entry_xex": entry, "source_sha256": sha, "file_count": len(files)}
        (base / f"{key}.json").write_text(json.dumps(meta), "utf-8")
        return ImportedExtra(key, final, entry, sha, len(files))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_imported(key: str, app_dir: Path | None = None) -> ImportedExtra | None:
    base = imports_root(app_dir)
    try:
        meta = json.loads((base / f"{key}.json").read_text("utf-8"))
        root = base / key
        if not (root / meta["entry_xex"]).is_file():
            return None
        return ImportedExtra(key, root, meta["entry_xex"], meta["source_sha256"], int(meta["file_count"]))
    except (OSError, ValueError, KeyError):
        return None
