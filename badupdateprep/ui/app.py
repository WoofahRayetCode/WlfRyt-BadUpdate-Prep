from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from badupdateprep import APP_NAME, APP_VERSION
from badupdateprep.pipeline import Env
from badupdateprep.platform_util import enable_dpi_awareness
from badupdateprep.ui import theme as T
from badupdateprep.ui.dialogs import GuideWindow
from badupdateprep.ui.pages.choose import ChoosePage
from badupdateprep.ui.pages.console import ConsolePage
from badupdateprep.ui.pages.prepare import PreparePage
from badupdateprep.ui.pages.usb import UsbPage
from badupdateprep.ui.pages.welcome import WelcomePage
from badupdateprep.ui.state import CONSOLE, STEPS, AppState
from badupdateprep.ui.widgets import StepNav

DEFAULT_GEOMETRY = "1060x780"


class Wizard(tk.Tk):
    def __init__(self, env: Env | None = None) -> None:
        super().__init__()
        if tk.TkVersion < 8.6:
            messagebox.showerror(APP_NAME, f"This app needs Tk 8.6 or newer (found {tk.TkVersion}).\nInstall a current Python from python.org (or your package manager's python3-tk).")
            self.destroy()
            raise SystemExit(1)
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.minsize(960, 660)
        self.fonts = T.apply(self)
        self.model = AppState.load(env or Env())
        self._restore_geometry()
        self.current_step = 0
        self._guide: GuideWindow | None = None

        header = tk.Frame(self, bg=T.PANEL, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left", padx=22)
        ttk.Label(header, text="Xbox 360  ·  dashboard 17559  ·  non-persistent", style="PanelSmall.TLabel").pack(side="left", padx=6, pady=(10, 0))
        ttk.Button(header, text="Guide", command=self.open_guide).pack(side="right", padx=18)

        self.nav = StepNav(self, STEPS, self._nav_click, self.fonts)
        self.nav.pack(fill="x")
        tk.Frame(self, bg=T.BORDER, height=1).pack(fill="x")

        foot = tk.Frame(self, bg=T.PANEL, height=62)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        self.back_btn = ttk.Button(foot, text="Back", command=self._back)
        self.back_btn.pack(side="left", padx=16, pady=12)
        self.next_btn = ttk.Button(foot, text="Continue", style="Accent.TButton", command=self._next)
        self.next_btn.pack(side="right", padx=16, pady=12)
        self.hint = ttk.Label(foot, text="", style="PanelMuted.TLabel")
        self.hint.pack(side="right", padx=8)

        self.body = tk.Frame(self, bg=T.BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=14)

        self.pages: list = []  # pages call refresh_nav() while building; it no-ops until they all exist
        self.pages.extend([WelcomePage(self), ChoosePage(self), PreparePage(self), UsbPage(self), ConsolePage(self)])
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Alt-Right>", lambda _e: self._next())
        self.bind("<Alt-Left>", lambda _e: self._back())
        self.bind("<F1>", lambda _e: self.open_guide())
        self.goto(0, force=True)

    # -- window ----------------------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        geo = self.model.settings.geometry
        try:
            if geo:
                self.geometry(geo)
                return
        except tk.TclError:
            pass
        self.geometry(DEFAULT_GEOMETRY)

    def _on_close(self) -> None:
        if self.model.busy and not messagebox.askyesno(APP_NAME, "Something is still running. Cancel it and quit?\n\nDownloads can be resumed next time."):
            return
        for p in self.pages:
            try:
                p.shutdown()
            except Exception:
                pass
        try:
            self.model.settings.geometry = self.geometry()
            self.model.save()
        except Exception:
            pass
        self.destroy()

    def open_guide(self, index: int = 0) -> None:
        if self._guide is not None and self._guide.winfo_exists():
            self._guide.show(index)
            self._guide.lift()
            return
        self._guide = GuideWindow(self, self.fonts, index)

    # -- navigation ------------------------------------------------------------------------------
    def goto(self, step: int, force: bool = False) -> None:
        if not force and not self._may_goto(step):
            return
        self.pages[self.current_step].on_hide()
        for p in self.pages:
            p.frame.pack_forget()
        self.current_step = step
        self.model.reached(step)
        self.pages[step].frame.pack(fill="both", expand=True)
        self.pages[step].on_show()
        self.refresh_nav()

    def _may_goto(self, step: int) -> bool:
        """Clicking a step label: any visited step is reachable, but never past an incomplete one."""
        if step == self.current_step or not self.model.can_goto(step):
            return False
        if step < self.current_step:
            return True
        return all(self.model.can_continue(s)[0] for s in range(self.current_step, step))

    def _nav_click(self, step: int) -> None:
        self.goto(step)

    def _back(self) -> None:
        if not self.model.busy and self.current_step > 0:
            self.goto(self.current_step - 1, force=True)

    def _next(self) -> None:
        ok, _why = self.model.can_continue(self.current_step)
        if not ok:
            return
        if self.current_step == CONSOLE:
            self._on_close()
            return
        self.goto(self.current_step + 1, force=True)

    def refresh_nav(self) -> None:
        if not self.pages:
            return
        step = self.current_step
        self.nav.update_state(step, lambda s: s != step and self._may_goto(s))
        ok, why = self.model.can_continue(step)
        self.next_btn.configure(text=self.pages[step].continue_label)
        self.next_btn.state(["!disabled"] if ok else ["disabled"])
        self.back_btn.state(["!disabled"] if (step > 0 and not self.model.busy) else ["disabled"])
        self.hint.configure(text="" if ok else why)


def main() -> None:
    enable_dpi_awareness()
    try:
        app = Wizard()
    except tk.TclError as exc:
        print(f"Couldn't open a window: {exc}", file=sys.stderr)
        raise SystemExit(1)
    app.mainloop()
