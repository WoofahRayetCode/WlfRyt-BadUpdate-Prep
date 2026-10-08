"""Minimal, comment-preserving editor for DashLaunch's launch.ini.

configparser would drop XeUnshackle's 12 KB of explanatory comments and mangle the format, so this works on
raw lines instead: a no-op edit round-trips byte-for-byte, and an edit changes only the targeted line(s).
Bytes are decoded as latin-1 so every byte survives untouched.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEYLINE = re.compile(r"^(?P<indent>\s*)(?P<key>[^;#=\s\[][^=]*?)(?P<sep>\s*=\s*)(?P<rest>.*)$")
_VALUE = re.compile(r"^(?P<val>[^;]*?)(?P<ws>\s*)(?P<cmt>;.*)?$")
_DEVICE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*:)[\\/]")
_BOM = "\xef\xbb\xbf"  # UTF-8 BOM as seen through latin-1


@dataclass(frozen=True)
class IniEdit:
    section: str
    key: str
    value: str


@dataclass(frozen=True)
class Slot:
    key: str
    value: str
    hint: tuple[str, ...]


def _strip_bom(s: str) -> str:
    return s[len(_BOM):] if s.startswith(_BOM) else s


class IniDoc:
    def __init__(self, lines: list[list[str]], trailing_newline: bool, eol: str):
        self._lines = lines  # [[content, eol], ...]
        self._trailing_newline = trailing_newline
        self._eol = eol

    # -- parse / render ----------------------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes) -> "IniDoc":
        text = data.decode("latin-1")
        parts = re.split(r"(\r\n|\r|\n)", text)
        lines: list[list[str]] = []
        for i in range(0, len(parts), 2):
            lines.append([parts[i], parts[i + 1] if i + 1 < len(parts) else ""])
        trailing = bool(lines) and lines[-1][0] == "" and lines[-1][1] == ""
        if trailing:
            lines.pop()
        first_eol = next((e for _, e in lines if e), "\r\n" if "\r\n" in text else "\n")
        return cls(lines, trailing, first_eol)

    def to_bytes(self) -> bytes:
        out = []
        for idx, (content, eol) in enumerate(self._lines):
            if not eol and idx != len(self._lines) - 1:
                eol = self._eol
            out.append(content + eol)
        return "".join(out).encode("latin-1")

    # -- inspection -------------------------------------------------------------------------
    def _section_range(self, section: str) -> tuple[int, int] | None:
        """(first line after header, index of next header or end)."""
        want = section.lower()
        start = None
        for i, (content, _) in enumerate(self._lines):
            m = _SECTION.match(_strip_bom(content))
            if m:
                if start is not None:
                    return start, i
                if m.group(1).strip().lower() == want:
                    start = i + 1
        return (start, len(self._lines)) if start is not None else None

    def slots(self, section: str = "Paths") -> list[Slot]:
        rng = self._section_range(section)
        if not rng:
            return []
        out: list[Slot] = []
        for i in range(*rng):
            m = _KEYLINE.match(self._lines[i][0])
            if not m:
                continue
            val = _VALUE.match(m.group("rest")).group("val")  # type: ignore[union-attr]
            hint: list[str] = []
            j = i - 1
            while j >= rng[0] and len(hint) < 6 and self._lines[j][0].lstrip().startswith((";", "#")):
                hint.insert(0, self._lines[j][0].lstrip().lstrip(";#").strip())
                j -= 1
            out.append(Slot(m.group("key").strip(), val, tuple(h for h in hint if h)))
        return out

    def get(self, section: str, key: str) -> str | None:
        for s in self.slots(section):
            if s.key.lower() == key.lower():
                return s.value
        return None

    def device_prefix(self) -> str:
        """The drive alias DashLaunch uses for the USB stick in this file, e.g. 'Usb:\\'."""
        seen: list[str] = []
        for content, _ in self._lines:
            m = _KEYLINE.match(content)
            if not m:
                continue
            d = _DEVICE.match(_VALUE.match(m.group("rest")).group("val").strip())  # type: ignore[union-attr]
            if d:
                seen.append(d.group(1))
        for pref in seen:
            if pref.lower().startswith("usb"):
                return pref + "\\"
        return "Usb:\\"

    # -- editing -----------------------------------------------------------------------------
    def apply(self, edits: list[IniEdit] | tuple[IniEdit, ...]) -> "IniDoc":
        lines = [list(x) for x in self._lines]
        doc = IniDoc(lines, self._trailing_newline, self._eol)
        for e in edits:
            doc._apply_one(e)
        return doc

    def _apply_one(self, e: IniEdit) -> None:
        rng = self._section_range(e.section)
        if rng is None:
            self._append_section(e)
            return
        start, end = rng
        hit = False
        for i in range(start, end):
            m = _KEYLINE.match(self._lines[i][0])
            if m and m.group("key").strip().lower() == e.key.lower():
                v = _VALUE.match(m.group("rest"))
                tail = (v.group("ws") + v.group("cmt")) if v and v.group("cmt") else ""
                sep = m.group("sep")
                if not sep.endswith((" ", "\t")):
                    sep += " "  # "BUT_A =" (value-less, no trailing space) must not become "BUT_A =value"
                self._lines[i][0] = m.group("indent") + m.group("key") + sep + e.value + tail
                hit = True
        if hit:
            return
        tmpl = re.compile(r"^\s*[;#]\s*" + re.escape(e.key) + r"\s*=", re.IGNORECASE)
        for i in range(start, end):
            if tmpl.match(self._lines[i][0]):
                self._insert(i + 1, f"{e.key} = {e.value}")
                return
        last = end
        while last > start and not self._lines[last - 1][0].strip():
            last -= 1
        self._insert(last, f"{e.key} = {e.value}")

    def _insert(self, index: int, content: str) -> None:
        """Insert a line; the last line only carries a terminator if the original file did."""
        if index >= len(self._lines):
            if self._lines and not self._lines[-1][1]:
                self._lines[-1][1] = self._eol
            self._lines.append([content, self._eol if self._trailing_newline else ""])
        else:
            self._lines.insert(index, [content, self._eol])

    def _append_section(self, e: IniEdit) -> None:
        if self._lines and self._lines[-1][0].strip():
            self._insert(len(self._lines), "")
        self._insert(len(self._lines), f"[{e.section}]")
        self._insert(len(self._lines), f"{e.key} = {e.value}")


def usb_path(prefix: str, usb_rel: str) -> str:
    """'Apps/XexMenu/default.xex' -> 'Usb:\\Apps\\XexMenu\\default.xex' (prefix already ends with a backslash)."""
    if not prefix.endswith("\\"):
        prefix += "\\"
    return prefix + usb_rel.strip("/").replace("/", "\\")


def unified_diff(old: bytes, new: bytes, name: str = "launch.ini") -> str:
    a = old.decode("latin-1").splitlines(keepends=True)
    b = new.decode("latin-1").splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=f"a/{name}", tofile=f"b/{name}", n=2))
