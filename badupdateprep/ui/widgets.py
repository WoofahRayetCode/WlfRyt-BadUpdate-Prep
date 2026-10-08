"""Reusable Tk widgets: step navigation, scroll frame, rich-text viewer, wrapped labels."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..platform_util import open_url
from . import markdown as md
from . import theme as T


def card(parent: tk.Misc) -> tk.Frame:
    """A bordered panel. ttk can't draw a coloured 1px border, so this one is a plain tk.Frame."""
    f = tk.Frame(parent, bg=T.CARD, highlightbackground=T.BORDER, highlightthickness=1)
    return f


def wrapped(parent: tk.Misc, text: str = "", style: str = "Card.TLabel", width: int = 820, **kw) -> ttk.Label:
    """A label that re-wraps to its own width. Pack it with fill="x" so its width follows its container."""
    lbl = ttk.Label(parent, text=text, style=style, wraplength=width, justify="left", **kw)
    lbl.bind("<Configure>", lambda e: lbl.configure(wraplength=max(120, e.width)))
    return lbl


class StepNav(tk.Frame):
    """1 Welcome > 2 Choose > ... Done steps show a check; reachable steps are clickable."""

    def __init__(self, parent: tk.Misc, steps: tuple[str, ...], on_click: Callable[[int], None], fonts: T.Fonts):
        super().__init__(parent, bg=T.CARD)
        self._fonts = fonts
        self._on_click = on_click
        self._labels: list[tk.Label] = []
        for i, name in enumerate(steps):
            lbl = tk.Label(self, text=f"  {i + 1}  {name}  ", bg=T.CARD, fg=T.MUTED, font=fonts.ui, padx=6, pady=8)
            lbl.pack(side="left", padx=(8 if i == 0 else 2, 2))
            lbl.bind("<Button-1>", lambda _e, n=i: self._on_click(n))
            self._labels.append(lbl)
            if i < len(steps) - 1:
                tk.Label(self, text="›", bg=T.CARD, fg=T.BORDER, font=fonts.ui).pack(side="left")
        self._steps = steps

    def update_state(self, current: int, can_goto: Callable[[int], bool]) -> None:
        for i, lbl in enumerate(self._labels):
            name = self._steps[i]
            clickable = i != current and can_goto(i)
            if i == current:
                lbl.configure(text=f"  {i + 1}  {name}  ", bg=T.ACCENT, fg="white", cursor="")
            elif i < current:
                lbl.configure(text=f"  ✓  {name}  ", bg=T.CARD, fg=T.GLOW, cursor="hand2" if clickable else "")
            else:
                lbl.configure(text=f"  {i + 1}  {name}  ", bg=T.CARD, fg=T.GLOW if clickable else T.MUTED, cursor="hand2" if clickable else "")


class ScrollFrame(tk.Frame):
    """A vertically scrolling container. Put content in `.inner`."""

    def __init__(self, parent: tk.Misc, bg: str = T.BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _on_inner(self, _e: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        need = self.inner.winfo_reqheight() > self.canvas.winfo_height()
        if need and not self.scroll.winfo_ismapped():
            self.scroll.pack(side="right", fill="y", before=self.canvas)
        elif not need and self.scroll.winfo_ismapped():
            self.scroll.pack_forget()

    def _bind_wheel(self, _e: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self, _e: tk.Event) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, e: tk.Event) -> None:
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        step = -1 * (e.delta // 120) if sys.platform != "darwin" else -1 * e.delta
        self.canvas.yview_scroll(step * 3 if sys.platform != "darwin" else step, "units")


class MarkdownView(tk.Frame):
    """Read-only rich text for the guide, built from markdown.parse()."""

    def __init__(self, parent: tk.Misc, fonts: T.Fonts, on_link: Callable[[str], None] | None = None):
        super().__init__(parent, bg=T.PANEL)
        self._fonts = fonts
        self._on_link = on_link or open_url
        self.text = tk.Text(
            self, bg=T.PANEL, fg=T.TEXT, relief="flat", wrap="word", highlightthickness=0, padx=18, pady=14,
            font=fonts.ui, cursor="arrow", spacing1=1, spacing3=3, insertwidth=0, borderwidth=0,
        )
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        t = self.text
        t.tag_configure("h1", font=fonts.title, foreground=T.GLOW, spacing1=2, spacing3=10)
        t.tag_configure("h2", font=fonts.heading, foreground=T.GLOW, spacing1=14, spacing3=6)
        t.tag_configure("h3", font=fonts.bold, foreground=T.TEXT, spacing1=10, spacing3=4)
        t.tag_configure("p", spacing3=7)
        t.tag_configure("b", font=fonts.bold)
        t.tag_configure("i", font=fonts.italic)
        t.tag_configure("code", font=fonts.mono, foreground=T.GLOW, background=T.BG)
        t.tag_configure("codeblock", font=fonts.mono, foreground=T.GLOW, background=T.BG, lmargin1=18, lmargin2=18, spacing1=0, spacing3=0)
        t.tag_configure("warn", foreground=T.WARN, background="#2a2410", lmargin1=10, lmargin2=10, spacing1=4, spacing3=6)
        t.tag_configure("table", font=fonts.mono, foreground=T.TEXT, lmargin1=10, lmargin2=10)
        t.tag_configure("tablehead", font=fonts.mono, foreground=T.GLOW, lmargin1=10, lmargin2=10)
        t.tag_configure("blank", font=fonts.small, spacing1=0, spacing3=0)
        t.tag_configure("link", foreground=T.GLOW, underline=True)
        t.tag_bind("link", "<Enter>", lambda e: t.configure(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda e: t.configure(cursor="arrow"))
        for lvl in range(4):
            t.tag_configure(f"li{lvl}", lmargin1=14 + 22 * lvl, lmargin2=32 + 22 * lvl, spacing3=3)
        self._links: dict[str, str] = {}

    def set_markdown(self, source: str) -> None:
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        self._links.clear()
        for b in md.parse(source):
            self._block(b)
        t.configure(state="disabled")
        t.yview_moveto(0)

    def _spans(self, spans, base: str = "") -> None:
        for s in spans:
            tags = [x for x in (base, s.style if s.style in ("b", "i", "code", "link") else "") if x]
            if s.style == "link":
                tag = f"link{len(self._links)}"
                self._links[tag] = s.href
                self.text.tag_bind(tag, "<Button-1>", lambda _e, h=s.href: self._on_link(h))
                self.text.tag_configure(tag, foreground=T.GLOW, underline=True)
                tags.append(tag)
            self.text.insert("end", s.text, tuple(tags))

    def _block(self, b: md.Block) -> None:
        t = self.text
        if b.kind in ("h1", "h2", "h3"):
            self._spans(b.spans, b.kind)
            t.insert("end", "\n", b.kind)
        elif b.kind == "p":
            self._spans(b.spans, "p")
            t.insert("end", "\n", "p")
        elif b.kind == "li":
            lvl = f"li{min(b.level, 3)}"
            t.insert("end", f"{b.marker}  ", lvl)
            self._spans(b.spans, lvl)
            t.insert("end", "\n", lvl)
        elif b.kind == "code":
            for line in (b.text or " ").split("\n"):
                t.insert("end", (line or " ") + "\n", "codeblock")
            t.insert("end", "\n", "blank")
        elif b.kind == "warn":
            self._spans(b.spans, "warn")
            t.insert("end", "\n", "warn")
        elif b.kind == "table":
            rows = [["".join(s.text for s in cell) for cell in row] for row in b.rows]
            ncols = max(len(r) for r in rows)
            widths = [max((len(r[c]) if c < len(r) else 0) for r in rows) for c in range(ncols)]
            for ri, r in enumerate(rows):
                line = "   ".join((r[c] if c < len(r) else "").ljust(widths[c]) for c in range(ncols)).rstrip()
                t.insert("end", line + "\n", "tablehead" if (b.header and ri == 0) else "table")
        elif b.kind == "blank":
            pass  # spacing comes from the block tags; literal blank lines made the guide feel loose
