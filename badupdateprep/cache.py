"""On-disk download cache: `downloads/<key>/<tag>/<asset>` plus an `index.json` of what was verified.

A file only counts as cached if it passes the same size/hash checks a fresh download would. This replaces
the old "exists and bigger than 1 KB" test that treated truncated downloads as complete forever.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .downloader import DownloadError, file_digest, verify_file
from .github import Resolved

_SAFE = re.compile(r"[^A-Za-z0-9._+-]+")


def _safe(part: str) -> str:
    return _SAFE.sub("_", part).strip("._") or "_"


@dataclass(frozen=True)
class CachedEntry:
    key: str
    tag: str
    asset: str
    path: Path
    size: int
    sha256: str
    url: str


LookupState = Literal["hit", "miss", "corrupt"]


class DownloadCache:
    def __init__(self, root: Path):
        self.root = root
        self.index_path = root / "index.json"

    # -- paths ----------------------------------------------------------------------------------
    def path_for(self, key: str, tag: str, asset: str) -> Path:
        return self.root / _safe(key) / _safe(tag) / _safe(asset)

    def path_for_resolved(self, r: Resolved) -> Path:
        return self.path_for(r.art.key, r.tag, r.asset)

    # -- index ----------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.index_path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        os.replace(tmp, self.index_path)

    def record(self, r: Resolved, path: Path, sha256: str | None = None) -> None:
        st = path.stat()
        data = self._load()
        entries = data.setdefault("entries", {}).setdefault(r.art.key, [])
        entries[:] = [e for e in entries if not (e["tag"] == r.tag and e["asset"] == r.asset)]
        entries.append(
            {
                "tag": r.tag,
                "asset": r.asset,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "sha256": sha256 or file_digest(path),
                "url": r.url,
                "path": str(path),
            }
        )
        self._save(data)

    def _entry_for(self, r: Resolved) -> dict | None:
        for e in self._load().get("entries", {}).get(r.art.key, []):
            if e["tag"] == r.tag and e["asset"] == r.asset:
                return e
        return None

    # -- lookup ---------------------------------------------------------------------------------
    def lookup(self, r: Resolved) -> LookupState:
        path = self.path_for_resolved(r)
        if not path.is_file():
            return "miss"
        st = path.stat()
        if r.size is not None and st.st_size != r.size:
            return "corrupt"
        entry = self._entry_for(r)
        # Fast path: we verified this exact file before and it has not been touched since.
        if entry and entry["size"] == st.st_size and entry["mtime_ns"] == st.st_mtime_ns:
            if not r.hashes or any(n.lower() == "sha256" and h.lower() == entry["sha256"].lower() for n, h in r.hashes):
                return "hit"
        try:
            verify_file(path, r.size, r.hashes, key=r.art.key)
        except DownloadError:
            return "corrupt"
        if not r.hashes and path.suffix.lower() == ".zip":
            # No upstream checksum: at least make sure it is a complete zip (central directory present).
            if not zipfile.is_zipfile(path) or not _zip_ok(path):
                return "corrupt"
        return "hit"

    def discard(self, r: Resolved) -> None:
        path = self.path_for_resolved(r)
        for p in (path, path.with_name(path.name + ".part"), path.with_name(path.name + ".part.json")):
            try:
                p.unlink()
            except OSError:
                pass

    # -- legacy + offline -----------------------------------------------------------------------
    def adopt_legacy(self, r: Resolved) -> bool:
        """Move a file downloaded by an older version (`downloads/<key>/<asset>`) into place if it verifies."""
        legacy = self.root / r.art.key / r.asset
        target = self.path_for_resolved(r)
        if target.exists() or not legacy.is_file():
            return False
        if not r.hashes and r.size is None:
            return False
        try:
            verify_file(legacy, r.size, r.hashes, key=r.art.key)
        except DownloadError:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
        return True

    def latest_cached(self, key: str) -> CachedEntry | None:
        """Newest verified entry whose file still exists (offline / rate-limited fallback)."""
        for e in reversed(self._load().get("entries", {}).get(key, [])):
            p = Path(e["path"])
            if p.is_file() and p.stat().st_size == e["size"]:
                return CachedEntry(key, e["tag"], e["asset"], p, e["size"], e["sha256"], e.get("url", ""))
        return None

    # -- housekeeping ---------------------------------------------------------------------------
    def prune(self, keep: int = 2) -> int:
        """Delete all but the newest `keep` recorded versions per item. Returns bytes freed."""
        data = self._load()
        freed = 0
        for key, entries in data.get("entries", {}).items():
            for e in entries[:-keep] if keep else entries:
                p = Path(e["path"])
                try:
                    freed += p.stat().st_size
                    p.unlink()
                except OSError:
                    pass
            if keep:
                entries[:] = entries[-keep:]
            else:
                entries.clear()
        self._save(data)
        return freed

    def total_size(self) -> int:
        total = 0
        if self.root.exists():
            for p in self.root.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
        return total


def _zip_ok(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False
