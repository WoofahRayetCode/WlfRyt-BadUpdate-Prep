"""Small OS differences in one place: opening folders/URLs, DPI awareness, eject wording, UI fonts."""
from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path


def open_path(path: Path | str) -> bool:
    """Open a folder in the system file manager."""
    p = str(path)
    try:
        if sys.platform == "win32":
            import os

            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        return False


def open_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except webbrowser.Error:
        return False


def enable_dpi_awareness() -> None:
    """Windows: render crisp instead of bitmap-scaled. Must run before the first Tk window exists."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


def eject_hint() -> str:
    if sys.platform == "win32":
        return "In File Explorer, right-click the drive and choose Eject (or use the tray icon > Eject) before unplugging."
    if sys.platform == "darwin":
        return "In Finder, click the eject icon next to the drive (or drag it to the Trash) before unplugging."
    return "Unmount it in your file manager (or run `sync` and then `udisksctl power-off -b /dev/sdX`) before unplugging."


def font_candidates() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(UI font families, monospace families) in order of preference; the theme picks the first installed."""
    if sys.platform == "win32":
        return ("Segoe UI", "Tahoma", "Arial"), ("Consolas", "Courier New")
    if sys.platform == "darwin":
        return ("SF Pro Text", "Helvetica Neue", "Helvetica"), ("Menlo", "Monaco", "Courier")
    return ("Noto Sans", "DejaVu Sans", "Cantarell", "Ubuntu", "Liberation Sans"), ("DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", "Monospace")
