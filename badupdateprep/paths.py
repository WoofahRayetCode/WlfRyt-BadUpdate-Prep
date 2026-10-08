"""Where the app keeps downloads, staging and settings.

Pure Python, no Tk. `WLFRYT_HOME` overrides everything (used by tests).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_HOME = "WLFRYT_HOME"
LEGACY_DIRNAME = ".wlfrit-badupdate"
APP_DIRNAME = "WlfRyt-BadUpdate-Prep"


def app_dir() -> Path:
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env)
    # Keep using the old location when it already holds a cache so nobody re-downloads 286 MB.
    legacy = Path.home() / LEGACY_DIRNAME
    if legacy.exists():
        return legacy
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_DIRNAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRNAME
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "wlfryt-badupdate-prep"


def downloads_dir(root: Path | None = None) -> Path:
    return (root or app_dir()) / "downloads"


def staging_dir(root: Path | None = None) -> Path:
    return (root or app_dir()) / "usb_staging"


def imports_dir(root: Path | None = None) -> Path:
    return (root or app_dir()) / "imports"


def meta_dir(root: Path | None = None) -> Path:
    return (root or app_dir()) / "meta"


def settings_path(root: Path | None = None) -> Path:
    return (root or app_dir()) / "settings.json"
