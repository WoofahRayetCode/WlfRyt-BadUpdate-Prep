"""Colours, fonts and ttk styles. One place that knows what the app looks like."""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk

from ..platform_util import font_candidates

BG = "#0b0f0c"
PANEL = "#121a14"
CARD = "#18221b"
ACCENT = "#107c10"
ACCENT_HOVER = "#16a316"
GLOW = "#5ad45a"
TEXT = "#e8f0e8"
MUTED = "#8aa08a"
WARN = "#e3b341"
OK = "#3ddc84"
DANGER = "#e5534b"
BORDER = "#2a3d2e"
DISABLED = "#4a5a4c"


@dataclass
class Fonts:
    ui: tkfont.Font
    bold: tkfont.Font
    small: tkfont.Font
    heading: tkfont.Font
    title: tkfont.Font
    mono: tkfont.Font
    mono_small: tkfont.Font
    italic: tkfont.Font


def _pick(root: tk.Misc, candidates: tuple[str, ...]) -> str:
    have = {f.lower(): f for f in tkfont.families(root)}
    for c in candidates:
        if c.lower() in have:
            return have[c.lower()]
    return tkfont.nametofont("TkDefaultFont", root=root).actual("family")


def make_fonts(root: tk.Misc) -> Fonts:
    ui_fams, mono_fams = font_candidates()
    ui, mono = _pick(root, ui_fams), _pick(root, mono_fams)
    return Fonts(
        ui=tkfont.Font(root=root, family=ui, size=11),
        bold=tkfont.Font(root=root, family=ui, size=11, weight="bold"),
        small=tkfont.Font(root=root, family=ui, size=9),
        heading=tkfont.Font(root=root, family=ui, size=13, weight="bold"),
        title=tkfont.Font(root=root, family=ui, size=21, weight="bold"),
        mono=tkfont.Font(root=root, family=mono, size=10),
        mono_small=tkfont.Font(root=root, family=mono, size=9),
        italic=tkfont.Font(root=root, family=ui, size=11, slant="italic"),
    )


def apply(root: tk.Tk) -> Fonts:
    """Install the dark theme on `root` and return the named fonts."""
    f = make_fonts(root)
    # tkfont.Font deletes its Tk font when garbage-collected; pin them to the root so styles never lose their font.
    root._wlfryt_fonts = f  # type: ignore[attr-defined]
    root.configure(bg=BG)
    # NOT option_add("*Font", ...): that sets every widget's -font resource, including ttk's, and silently
    # overrides the per-style fonts below (every label ended up the same size). Retune Tk's defaults instead.
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkTooltipFont"):
        tkfont.nametofont(name, root=root).configure(family=f.ui.actual("family"), size=11)
    tkfont.nametofont("TkFixedFont", root=root).configure(family=f.mono.actual("family"), size=10)
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    root.option_add("*TCombobox*Listbox.font", f.ui)

    st = ttk.Style(root)
    st.theme_use("clam")  # the only built-in theme that honours custom colours on every OS
    st.configure(
        ".", background=CARD, foreground=TEXT, fieldbackground=PANEL, bordercolor=BORDER, lightcolor=CARD,
        darkcolor=CARD, troughcolor=PANEL, focuscolor=CARD, font=f.ui, selectbackground=ACCENT, selectforeground="white",
        insertcolor=TEXT,
    )
    for name, bg in (("Card", CARD), ("Panel", PANEL), ("Bg", BG)):
        st.configure(f"{name}.TFrame", background=bg)
        st.configure(f"{name}.TLabel", background=bg, foreground=TEXT, font=f.ui)
        st.configure(f"{name}Muted.TLabel", background=bg, foreground=MUTED, font=f.ui)
        st.configure(f"{name}Small.TLabel", background=bg, foreground=MUTED, font=f.small)
        st.configure(f"{name}Head.TLabel", background=bg, foreground=TEXT, font=f.heading)
        st.configure(f"{name}Glow.TLabel", background=bg, foreground=GLOW, font=f.bold)
        st.configure(f"{name}Ok.TLabel", background=bg, foreground=OK, font=f.ui)
        st.configure(f"{name}Warn.TLabel", background=bg, foreground=WARN, font=f.ui)
        st.configure(f"{name}Danger.TLabel", background=bg, foreground=DANGER, font=f.ui)
        st.configure(f"{name}Bold.TLabel", background=bg, foreground=TEXT, font=f.bold)
    st.configure("Title.TLabel", background=PANEL, foreground=GLOW, font=f.title)

    st.configure("TButton", background=BORDER, foreground=TEXT, bordercolor=BORDER, padding=(14, 7), relief="flat", font=f.ui)
    st.map("TButton", background=[("disabled", PANEL), ("pressed", ACCENT), ("active", "#35503a")],
           foreground=[("disabled", DISABLED)])
    st.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT, padding=(18, 8), font=f.bold)
    st.map("Accent.TButton", background=[("disabled", PANEL), ("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER)],
           foreground=[("disabled", DISABLED)])
    st.configure("Link.TButton", background=CARD, foreground=GLOW, bordercolor=CARD, padding=(2, 0), font=f.ui)
    st.map("Link.TButton", background=[("active", CARD)], foreground=[("active", TEXT)])

    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=CARD, foreground=TEXT, font=f.ui, indicatorcolor=PANEL, indicatorbackground=PANEL,
                     upperbordercolor=BORDER, lowerbordercolor=BORDER, padding=(2, 3))
        st.map(w, background=[("active", CARD)], foreground=[("disabled", DISABLED)],
               indicatorcolor=[("selected", ACCENT), ("disabled", PANEL)],
               indicatorbackground=[("selected", ACCENT)])
    st.configure("Toggle.TRadiobutton", background=PANEL, foreground=MUTED, padding=(16, 6), font=f.bold)
    st.map("Toggle.TRadiobutton", background=[("selected", ACCENT), ("active", BORDER)],
           foreground=[("selected", "white"), ("active", TEXT)])

    st.configure("TCombobox", fieldbackground=PANEL, background=BORDER, foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                 selectbackground=PANEL, selectforeground=TEXT, padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("disabled", DISABLED)],
           selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
    st.configure("TEntry", fieldbackground=PANEL, foreground=TEXT, bordercolor=BORDER, padding=5)

    st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, bordercolor=BORDER, rowheight=30,
                 font=f.ui, relief="flat")
    st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    st.configure("Treeview.Heading", background=BORDER, foreground=TEXT, relief="flat", font=f.bold, padding=(8, 6))
    st.map("Treeview.Heading", background=[("active", "#35503a")])

    st.configure("Horizontal.TProgressbar", background=GLOW, troughcolor=PANEL, bordercolor=PANEL, lightcolor=GLOW,
                 darkcolor=GLOW, thickness=14)
    st.configure("Vertical.TScrollbar", background=BORDER, troughcolor=PANEL, bordercolor=PANEL, arrowcolor=TEXT,
                 lightcolor=BORDER, darkcolor=BORDER)
    st.map("Vertical.TScrollbar", background=[("active", "#35503a")])
    st.configure("TSeparator", background=BORDER)
    st.configure("TLabelframe", background=CARD, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    st.configure("TLabelframe.Label", background=CARD, foreground=GLOW, font=f.bold)
    return f
