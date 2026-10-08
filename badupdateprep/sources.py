"""Uniform, safe access to the files inside a zip or a folder.

`members()` yields '/'-normalised relative paths (files only). Anything that could escape the destination
(absolute paths, drive letters, '..') is rejected up front - the zip-slip class of bugs.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol

from .errors import AssembleError

_DRIVE = re.compile(r"^[A-Za-z]:")
_JUNK_PARTS = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}


def normalize_member(name: str) -> str | None:
    """Return a safe '/'-separated relative path, None for entries to ignore, or raise AssembleError."""
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    if not n or n.endswith("/"):
        return None
    if n.startswith("/") or _DRIVE.match(n):
        raise AssembleError(f"Unsafe absolute path in archive: {name!r}")
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise AssembleError(f"Unsafe path in archive: {name!r}")
    if any(p.lower() in _JUNK_PARTS for p in parts):
        return None
    return "/".join(parts)


class Source(Protocol):
    def members(self) -> Iterator[tuple[str, int]]: ...

    def open(self, rel: str) -> BinaryIO: ...

    def close(self) -> None: ...


class ZipSource:
    def __init__(self, path: Path):
        try:
            self._zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise AssembleError(f"{Path(path).name} is not a valid zip (re-download it).") from exc
        self._map: dict[str, zipfile.ZipInfo] = {}
        for info in self._zf.infolist():
            rel = normalize_member(info.filename)
            if rel is None or info.is_dir():
                continue
            if rel in self._map:
                raise AssembleError(f"Archive lists {rel!r} twice.")
            self._map[rel] = info

    def members(self) -> Iterator[tuple[str, int]]:
        for rel, info in self._map.items():
            yield rel, info.file_size

    def open(self, rel: str) -> BinaryIO:
        return self._zf.open(self._map[rel])

    def close(self) -> None:
        self._zf.close()


class DirSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def members(self) -> Iterator[tuple[str, int]]:
        for p in sorted(self.root.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            rel = normalize_member(p.relative_to(self.root).as_posix())
            if rel is not None:
                yield rel, p.stat().st_size

    def open(self, rel: str) -> BinaryIO:
        p = self.root / rel
        if p.is_symlink():
            raise AssembleError(f"Refusing to follow symlink {rel!r}")
        return p.open("rb")

    def close(self) -> None:
        pass
