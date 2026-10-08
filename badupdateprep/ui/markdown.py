"""A small Markdown parser for the in-app guide - pure Python, no Tk (the Text-widget renderer lives in widgets.py).

Handles what the bundled wiki pages actually use: headings, **bold**, `code`, *italic*, [links](url), bullet and
numbered lists, indented/fenced code, pipe tables, and WARNING callouts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    text: str
    style: str = ""  # "" | "b" | "i" | "code" | "link"
    href: str = ""


@dataclass(frozen=True)
class Block:
    kind: str  # h1 h2 h3 p li code table warn blank
    spans: tuple[Span, ...] = ()
    marker: str = ""  # list bullet/number
    level: int = 0  # list indent level
    text: str = ""  # code
    rows: tuple[tuple[tuple[Span, ...], ...], ...] = field(default_factory=tuple)  # table: rows of cells of spans
    header: bool = False


_INLINE = re.compile(
    r"\*\*(?P<b>.+?)\*\*"
    r"|`(?P<code>[^`]+)`"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<href>[^)\s]+)\)"
    r"|(?<![\w*])\*(?P<i>[^*\s][^*]*?)\*(?![\w*])"
)


def parse_inline(text: str) -> tuple[Span, ...]:
    out: list[Span] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(Span(text[pos:m.start()]))
        if m.group("b") is not None:
            out.append(Span(m.group("b"), "b"))
        elif m.group("code") is not None:
            out.append(Span(m.group("code"), "code"))
        elif m.group("ltext") is not None:
            out.append(Span(m.group("ltext"), "link", m.group("href")))
        else:
            out.append(Span(m.group("i"), "i"))
        pos = m.end()
    if pos < len(text):
        out.append(Span(text[pos:]))
    return tuple(out) or (Span(""),)


_LIST = re.compile(r"^(?P<indent>\s*)(?P<mark>[-*+]|\d+[.)])\s+(?P<body>.*)$")
_SEP_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _cells(line: str) -> tuple[tuple[Span, ...], ...]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return tuple(parse_inline(c.strip()) for c in inner.split("|"))


def parse(md: str) -> list[Block]:
    blocks: list[Block] = []
    lines = md.splitlines()
    i = 0
    fenced = False
    code: list[str] = []

    def flush_code() -> None:
        nonlocal code
        if code:
            while code and not code[-1].strip():
                code.pop()
            blocks.append(Block("code", text="\n".join(code)))
            code = []

    while i < len(lines):
        raw = lines[i].rstrip()
        i += 1
        if raw.lstrip().startswith("```"):
            if fenced:
                flush_code()
            fenced = not fenced
            continue
        if fenced:
            code.append(raw)
            continue
        is_indented_code = raw.startswith(("    ", "\t")) and not _LIST.match(raw)
        if is_indented_code:
            code.append(raw[4:] if raw.startswith("    ") else raw[1:])
            continue
        flush_code()
        stripped = raw.strip()
        if not stripped:
            if blocks and blocks[-1].kind != "blank":
                blocks.append(Block("blank"))
            continue
        if stripped.startswith("### "):
            blocks.append(Block("h3", parse_inline(stripped[4:])))
        elif stripped.startswith("## "):
            blocks.append(Block("h2", parse_inline(stripped[3:])))
        elif stripped.startswith("# "):
            blocks.append(Block("h1", parse_inline(stripped[2:])))
        elif stripped.upper().startswith("WARNING:") or stripped.startswith("> [!WARNING]"):
            body = stripped.replace("> [!WARNING]", "WARNING:").lstrip("> ").strip()
            blocks.append(Block("warn", parse_inline(body)))
        elif stripped.startswith(">"):
            blocks.append(Block("warn", parse_inline(stripped.lstrip("> "))))
        elif stripped.startswith("|"):
            rows = [stripped]
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            has_header = len(rows) > 1 and bool(_SEP_ROW.match(rows[1]))
            body = [r for r in rows if not _SEP_ROW.match(r)]
            blocks.append(Block("table", rows=tuple(_cells(r) for r in body), header=has_header))
        else:
            m = _LIST.match(raw)
            if m:
                mark = m.group("mark")
                blocks.append(
                    Block(
                        "li",
                        parse_inline(m.group("body")),
                        marker="•" if mark in "-*+" else mark,
                        level=len(m.group("indent").replace("\t", "    ")) // 2,
                    )
                )
            else:
                blocks.append(Block("p", parse_inline(stripped)))
    flush_code()
    while blocks and blocks[-1].kind == "blank":
        blocks.pop()
    return blocks


def plain_text(blocks: list[Block]) -> str:
    """Rendered text without markup - used by tests to prove no raw '**' survives."""
    out: list[str] = []
    for b in blocks:
        if b.kind == "code":
            out.append(b.text)
        elif b.kind == "table":
            out.extend(" | ".join("".join(s.text for s in c) for c in row) for row in b.rows)
        elif b.kind == "blank":
            out.append("")
        else:
            out.append((b.marker + " " if b.marker else "") + "".join(s.text for s in b.spans))
    return "\n".join(out)
