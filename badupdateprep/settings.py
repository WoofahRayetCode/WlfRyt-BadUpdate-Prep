"""Remembered choices (JSON in the app dir). Loading never raises; a corrupt file is set aside as .bak."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .options import Options

VERSION = 1
_KNOWN = {"version", "options", "last_usb", "last_usb_label", "show_fixed", "geometry"}


@dataclass
class Settings:
    version: int = VERSION
    options: Options = field(default_factory=Options)
    last_usb: str = ""
    last_usb_label: str = ""
    show_fixed: bool = False
    geometry: str = ""
    extra: dict = field(default_factory=dict)  # unknown keys, preserved for forward compatibility

    def to_dict(self) -> dict:
        d = {
            "version": VERSION,
            "options": self.options.to_dict(),
            "last_usb": self.last_usb,
            "last_usb_label": self.last_usb_label,
            "show_fixed": self.show_fixed,
            "geometry": self.geometry,
        }
        d.update(self.extra)
        return d


def load(path: Path) -> Settings:
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings root must be an object")
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError):
        try:
            os.replace(path, Path(str(path) + ".bak"))
        except OSError:
            pass
        return Settings()
    s = Settings()
    try:
        s.options = Options.from_dict(raw.get("options") or {})
    except (TypeError, ValueError):
        s.options = Options()
    s.last_usb = str(raw.get("last_usb") or "")
    s.last_usb_label = str(raw.get("last_usb_label") or "")
    s.show_fixed = bool(raw.get("show_fixed", False))
    s.geometry = str(raw.get("geometry") or "")
    s.extra = {k: v for k, v in raw.items() if k not in _KNOWN}
    return s


def save(s: Settings, path: Path) -> bool:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.to_dict(), indent=1), "utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
