from pathlib import Path

PAGES = (
    ("overview.md", "Overview"),
    ("how_to_use.md", "How to use"),
    ("rock_band_blitz.md", "Rock Band Blitz"),
    ("led_and_errors.md", "LEDs & errors"),
    ("boot_recovery.md", "Boot animation recovery"),
    ("faq.md", "FAQ"),
)


def wiki_dir() -> Path:
    return Path(__file__).resolve().parent


def load_page(filename: str) -> str:
    path = wiki_dir() / filename
    if not path.is_file():
        return f"(Missing wiki page: {filename})"
    return path.read_text(encoding="utf-8")
