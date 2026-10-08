from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...pipeline import hotkey_hint
from ...options import checklist_for
from .. import theme as T
from ..state import USB
from ..widgets import ScrollFrame, wrapped
from . import Page


class ConsolePage(Page):
    continue_label = "Close"

    def build(self) -> None:
        top = tk.Frame(self.frame, bg=T.BG)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Your stick is ready. Now, on the Xbox 360:", style="BgHead.TLabel").pack(side="left")
        ttk.Button(top, text="Open the guide", command=lambda: self.app.open_guide(1)).pack(side="right")
        ttk.Button(top, text="Prepare another USB", command=self._again).pack(side="right", padx=8)
        self.scroll = ScrollFrame(self.frame, bg=T.BG)
        self.scroll.pack(fill="both", expand=True)
        self._checks: list[tk.BooleanVar] = []

    def on_show(self) -> None:
        for w in self.scroll.inner.winfo_children():
            w.destroy()
        self._checks.clear()
        items = checklist_for(self.state.options, hotkey_hint(self.state.options, self.state.imported))
        for i, it in enumerate(items, 1):
            outer = tk.Frame(self.scroll.inner, bg=T.CARD, highlightbackground=T.WARN if it.important else T.BORDER, highlightthickness=1)
            outer.pack(fill="x", pady=(0, 8))
            row = tk.Frame(outer, bg=T.CARD)
            row.pack(fill="x", padx=16, pady=12)
            var = tk.BooleanVar(value=False)
            self._checks.append(var)
            ttk.Checkbutton(row, variable=var).pack(side="left", anchor="n", padx=(0, 6))
            tk.Label(row, text=str(i), bg=T.ACCENT, fg="white", font=self.fonts.bold, width=3).pack(side="left", anchor="n", padx=(0, 12))
            col = tk.Frame(row, bg=T.CARD)
            col.pack(side="left", fill="x", expand=True)
            ttk.Label(col, text=("⚠ " if it.important else "") + it.text, style="CardWarn.TLabel" if it.important else "CardBold.TLabel").pack(anchor="w")
            if it.detail:
                wrapped(col, it.detail, "CardMuted.TLabel").pack(anchor="w", fill="x")
        ttk.Label(self.scroll.inner, text="Tick items as you go. Something not working? The LED and error-code pages in the guide explain every pattern.", style="BgSmall.TLabel").pack(anchor="w", pady=(4, 12))

    def _again(self) -> None:
        self.state.copy_report = None
        self.app.goto(USB, force=True)
