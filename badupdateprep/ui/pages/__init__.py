from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

from .. import theme as T

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Wizard


class Page:
    """One wizard step. `frame` is packed into the wizard body when shown."""

    continue_label = "Continue"

    def __init__(self, app: "Wizard"):
        self.app = app
        self.state = app.model
        self.fonts = app.fonts
        self.frame = tk.Frame(app.body, bg=T.BG)
        self.build()

    def build(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass

    def shutdown(self) -> None:
        """Stop timers/threads; called when the window closes."""
