"""Secondary windows: the offline guide and the launch.ini diff viewer."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..wiki import PAGES, load_page
from . import theme as T
from .widgets import MarkdownView


class GuideWindow(tk.Toplevel):
    """Offline copy of the Bad Update wiki. Opens at any time from the header."""

    def __init__(self, master: tk.Misc, fonts: T.Fonts, index: int = 0):
        super().__init__(master)
        self.title("Bad Update guide (offline copy)")
        self.configure(bg=T.BG)
        self.geometry("980x680")
        self.minsize(720, 480)
        self.transient(master)
        side = tk.Frame(self, bg=T.PANEL, width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Guide", bg=T.PANEL, fg=T.GLOW, font=fonts.heading, anchor="w").pack(fill="x", padx=16, pady=(16, 8))
        self._btns: list[tk.Label] = []
        for i, (_fn, title) in enumerate(PAGES):
            b = tk.Label(side, text=title, bg=T.PANEL, fg=T.TEXT, font=fonts.ui, anchor="w", padx=16, pady=7, cursor="hand2")
            b.pack(fill="x")
            b.bind("<Button-1>", lambda _e, n=i: self.show(n))
            self._btns.append(b)
        self.view = MarkdownView(self, fonts)
        self.view.pack(side="left", fill="both", expand=True)
        self.show(index)

    def show(self, index: int) -> None:
        filename, _title = PAGES[index]
        for i, b in enumerate(self._btns):
            b.configure(bg=T.ACCENT if i == index else T.PANEL, fg="white" if i == index else T.TEXT)
        self.view.set_markdown(load_page(filename))


class TextWindow(tk.Toplevel):
    """Monospace viewer; lines starting with + / - are coloured (used for the launch.ini diff)."""

    def __init__(self, master: tk.Misc, fonts: T.Fonts, title: str, body: str, intro: str = ""):
        super().__init__(master)
        self.title(title)
        self.configure(bg=T.BG)
        self.geometry("820x460")
        self.transient(master)
        if intro:
            ttk.Label(self, text=intro, style="BgMuted.TLabel", wraplength=780, justify="left").pack(anchor="w", padx=16, pady=(14, 6))
        wrap = tk.Frame(self, bg=T.PANEL)
        wrap.pack(fill="both", expand=True, padx=16, pady=(6, 12))
        txt = tk.Text(wrap, bg=T.PANEL, fg=T.TEXT, font=fonts.mono, relief="flat", wrap="none", highlightthickness=0, padx=10, pady=8)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_configure("add", foreground=T.OK)
        txt.tag_configure("del", foreground=T.DANGER)
        txt.tag_configure("hunk", foreground=T.MUTED)
        for line in body.splitlines():
            tag = "add" if line.startswith("+") and not line.startswith("+++") else "del" if line.startswith("-") and not line.startswith("---") else "hunk" if line.startswith(("@@", "---", "+++")) else ""
            txt.insert("end", line + "\n", tag)
        txt.configure(state="disabled")
        ttk.Button(self, text="Close", command=self.destroy).pack(anchor="e", padx=16, pady=(0, 14))
