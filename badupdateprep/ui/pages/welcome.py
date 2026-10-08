from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ... import REPO_URL
from ...platform_util import open_url
from .. import theme as T
from ..widgets import card, wrapped
from . import Page

POINTS = (
    ("1", "Your console must be on dashboard 2.0.17559.0.", "Check Settings > System > Console Settings > System Info."),
    ("2", "Stay off Xbox Live.", "Unplug Ethernet and forget saved Wi-Fi before you run the exploit. Running it while connected can permanently ban the console."),
    ("3", "It only lasts until you power off.", "Bad Update is a non-persistent hypervisor exploit - not a softmod and not a replacement for RGH. You repeat it every boot."),
)


class WelcomePage(Page):
    def build(self) -> None:
        c = card(self.frame)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=32, pady=26)
        ttk.Label(inner, text="Get a USB stick ready for Bad Update", style="CardHead.TLabel").pack(anchor="w")
        wrapped(
            inner,
            "This wizard downloads the official files, checks they are intact, builds the right folder layout, and copies it to "
            "your FAT32 USB stick. You then run the exploit on the console. It never formats a drive and never touches anything "
            "it didn't put there.",
            "CardMuted.TLabel",
        ).pack(anchor="w", pady=(8, 18), fill="x")

        for num, head, body in POINTS:
            row = tk.Frame(inner, bg=T.CARD)
            row.pack(fill="x", pady=5)
            tk.Label(row, text=num, bg=T.ACCENT, fg="white", font=self.fonts.bold, width=3, pady=4).pack(side="left", anchor="n", padx=(0, 14))
            col = tk.Frame(row, bg=T.CARD)
            col.pack(side="left", fill="x", expand=True)
            ttk.Label(col, text=head, style="CardBold.TLabel").pack(anchor="w")
            wrapped(col, body, "CardMuted.TLabel").pack(anchor="w", fill="x")

        ttk.Separator(inner).pack(fill="x", pady=18)
        self.ack = tk.BooleanVar(value=self.state.ack)
        self.ack.trace_add("write", self._changed)
        ttk.Checkbutton(
            inner, variable=self.ack,
            text="I'm on 17559, I'll stay offline while I run it, and I understand it lasts only until reboot.",
        ).pack(anchor="w")

        links = tk.Frame(inner, bg=T.CARD)
        links.pack(anchor="w", pady=(16, 0))
        ttk.Button(links, text="Project page", style="Link.TButton", command=lambda: open_url(REPO_URL), cursor="hand2").pack(side="left")
        ttk.Label(links, text="  ·  not affiliated with grimdoomer, XeUnshackle, FreeMyXe, Internet Archive or Microsoft", style="CardSmall.TLabel").pack(side="left")

    def _changed(self, *_a) -> None:
        self.state.ack = bool(self.ack.get())
        self.app.refresh_nav()

    def on_show(self) -> None:
        self.ack.set(self.state.ack)
