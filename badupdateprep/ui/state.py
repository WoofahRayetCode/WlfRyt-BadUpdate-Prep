"""Wizard state and navigation rules - pure Python so they are unit-tested without Tk."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import settings as settings_mod
from ..drives import Drive
from ..importer import ImportedExtra
from ..options import Options, Problem, has_errors, validate
from ..paths import settings_path
from ..pipeline import CopyReport, Env, PrepareResult, imported_extras

STEPS = ("Welcome", "Choose", "Prepare", "USB", "Console")
WELCOME, CHOOSE, PREPARE, USB, CONSOLE = range(5)


@dataclass
class AppState:
    env: Env
    settings: settings_mod.Settings = field(default_factory=settings_mod.Settings)
    ack: bool = False
    imported: dict[str, ImportedExtra] = field(default_factory=dict)
    prepared: PrepareResult | None = None
    prepared_options: dict | None = None
    drive: Drive | None = None
    copy_report: CopyReport | None = None
    busy: bool = False
    max_step: int = 0  # furthest step the user has legitimately reached

    @classmethod
    def load(cls, env: Env) -> "AppState":
        st = cls(env=env, settings=settings_mod.load(settings_path(env.app_dir)))
        st.imported = imported_extras(env)
        return st

    # -- options ------------------------------------------------------------------------------
    @property
    def options(self) -> Options:
        return self.settings.options

    def problems(self) -> list[Problem]:
        return validate(self.options, frozenset(self.imported))

    def save(self) -> bool:
        return settings_mod.save(self.settings, settings_path(self.env.app_dir))

    # -- prepare ------------------------------------------------------------------------------
    def prepare_is_current(self) -> bool:
        """The staged tree matches the options on screen (and the same imports)."""
        if self.prepared is None or self.prepared_options is None:
            return False
        return self.prepared_options == _snapshot(self.options, self.imported)

    def set_prepared(self, result: PrepareResult) -> None:
        self.prepared = result
        self.prepared_options = _snapshot(self.options, self.imported)
        self.copy_report = None  # a new build invalidates any earlier copy

    def invalidate_prepared(self) -> None:
        self.prepared = None
        self.prepared_options = None
        self.copy_report = None

    # -- navigation ---------------------------------------------------------------------------
    def can_continue(self, step: int) -> tuple[bool, str]:
        if self.busy:
            return False, "Working..."
        if step == WELCOME:
            return (True, "") if self.ack else (False, "Tick the box to confirm you understand.")
        if step == CHOOSE:
            errs = [p.text for p in self.problems() if p.level == "error"]
            return (False, errs[0]) if errs else (True, "")
        if step == PREPARE:
            return (True, "") if self.prepare_is_current() else (False, "Press Prepare first.")
        if step == USB:
            if self.copy_report and self.copy_report.ok:
                return True, ""
            return False, "Copy to your USB stick first."
        return True, ""

    def can_goto(self, step: int) -> bool:
        """Clicking a step label is allowed for any step the user has already passed through."""
        return not self.busy and 0 <= step <= self.max_step

    def reached(self, step: int) -> None:
        self.max_step = max(self.max_step, step)


def _snapshot(opts: Options, imported: dict[str, ImportedExtra]) -> dict:
    return {"opts": opts.to_dict(), "imports": sorted((k, v.source_sha256) for k, v in imported.items())}


def usb_candidates(drives: list[Drive], last_mount: str, last_label: str) -> int | None:
    """Index of the drive to preselect: the last used one if it is still there (same mount and label), else a lone stick."""
    if last_mount:
        for i, d in enumerate(drives):
            if d.mount == last_mount and (not last_label or d.label == last_label):
                return i
    usable = [i for i, d in enumerate(drives) if d.mounted and not d.system]
    return usable[0] if len(usable) == 1 else None
