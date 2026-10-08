from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...catalog import Channel, display_version
from ...downloader import DownloadError
from ...options import has_errors, wanted
from ...pipeline import (
    Failed,
    Finished,
    ItemUpdate,
    Log,
    OverallProgress,
    PrepareResult,
    StageChange,
    run_prepare,
)
from ...platform_util import open_path
from ...progress import Cancelled, fmt_bytes, fmt_eta
from .. import theme as T
from ..dialogs import TextWindow
from ..widgets import card, wrapped
from ..worker import Done, Worker
from . import Page

STATUS_TEXT = {
    "queued": "Waiting", "checking": "Checking...", "downloading": "Downloading", "verifying": "Verifying...",
    "cached": "Ready (cached)", "ready": "Ready", "failed": "Failed", "skipped": "Skipped",
}
# What we will check against (before the file is verified) and what we did check (after). A tick means verified.
CHECK_EXPECTED = {"pinned": "SHA-256 pinned", "github-digest": "GitHub digest", "unverified": "no checksum", "imported": "imported by you"}
CHECK_VERIFIED = {"pinned": "\u2713 SHA-256 verified", "github-digest": "\u2713 digest verified", "unverified": "\u26a0 unverified", "imported": "imported by you"}


def check_text(provenance: str, done: bool) -> str:
    return (CHECK_VERIFIED if done else CHECK_EXPECTED).get(provenance, "")


STAGE_TEXT = {"resolve": "Checking versions...", "download": "Downloading and verifying...", "build": "Building the USB folder...",}


class PreparePage(Page):
    continue_label = "Continue"

    def build(self) -> None:
        self.worker = Worker(self.app.after, self._on_event)
        self._arrived_for: dict | None = None
        c = card(self.frame)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=26, pady=20)

        head = tk.Frame(inner, bg=T.CARD)
        head.pack(fill="x")
        ttk.Label(head, text="Download, verify and build", style="CardHead.TLabel").pack(side="left")
        self.btn_prepare = ttk.Button(head, text="Prepare", style="Accent.TButton", command=self._start)
        self.btn_prepare.pack(side="right")
        self.btn_cancel = ttk.Button(head, text="Cancel", command=self._cancel)
        self.btn_cancel.pack(side="right", padx=8)
        self.summary = wrapped(inner, "", "CardMuted.TLabel")
        self.summary.pack(anchor="w", fill="x", pady=(6, 10))

        cols = (("item", "Item", 200, "w"), ("version", "Version", 85, "w"), ("size", "Size", 70, "e"),
                ("status", "Status", 120, "w"), ("detail", "Progress", 250, "w"), ("check", "Checksum", 140, "w"))
        self.tree = ttk.Treeview(inner, columns=[c[0] for c in cols], show="headings", height=6, selectmode="none")
        for key, label, width, anchor in cols:
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ("item", "detail"))
        self.tree.pack(fill="x")
        self.tree.tag_configure("ok", foreground=T.OK)
        self.tree.tag_configure("bad", foreground=T.DANGER)
        self.tree.tag_configure("busy", foreground=T.GLOW)

        bar = tk.Frame(inner, bg=T.CARD)
        bar.pack(fill="x", pady=(14, 2))
        self.stage = ttk.Label(bar, text="", style="CardBold.TLabel")
        self.stage.pack(side="left")
        self.overall = ttk.Label(bar, text="", style="CardMuted.TLabel")
        self.overall.pack(side="right")
        self.pbar = ttk.Progressbar(inner, mode="determinate", maximum=1000)
        self.pbar.pack(fill="x", pady=(2, 8))

        self.result = wrapped(inner, "", "Card.TLabel")
        self.result.pack(anchor="w", fill="x")
        self.fix_row = tk.Frame(inner, bg=T.CARD)
        self.fix_row.pack(anchor="w", pady=(6, 0))
        self.btn_fix = ttk.Button(self.fix_row, text="Switch to tested versions and retry", command=self._use_pinned)

        tools = tk.Frame(inner, bg=T.CARD)
        tools.pack(fill="x", pady=(10, 0))
        self.btn_open = ttk.Button(tools, text="Open the staged folder", command=self._open_staging)
        self.btn_open.pack(side="left")
        self.btn_diff = ttk.Button(tools, text="View launch.ini changes", command=self._show_diff)
        self.btn_diff.pack(side="left", padx=8)
        self._log_open = False
        self.btn_log = ttk.Button(tools, text="Show details", command=self._toggle_log)
        self.btn_log.pack(side="right")
        self.log_wrap = tk.Frame(inner, bg=T.PANEL)
        self.log = tk.Text(self.log_wrap, bg=T.PANEL, fg=T.MUTED, font=self.fonts.mono_small, height=7, relief="flat", wrap="word", highlightthickness=0, padx=8, pady=6)
        sb = ttk.Scrollbar(self.log_wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    # -- table -----------------------------------------------------------------------------------
    def _populate(self, ready: bool) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        o = self.state.options
        names = []
        for a in wanted(o):
            ver = "imported" if a.source == "manual" else (display_version(a.pin.tag) if (a.pin and (o.channel == Channel.PINNED or a.source == "url")) else "latest")
            size = fmt_bytes(a.pin.size) if a.pin else ""
            if a.source == "manual":
                status, check = "Ready", check_text("imported", True)
            elif ready:
                status, check = "Ready", check_text("pinned", True) if ver != "latest" else ""
            else:
                status, check = "Waiting", ""
            self.tree.insert("", "end", iid=a.key, values=(a.title, ver, size, status, "", check), tags=("ok",) if ready else ())
            names.append(a.title)
        self.summary.configure(text="Files: " + ", ".join(names) + f"  ·  {'tested versions' if o.channel == Channel.PINNED else 'latest versions'}")
        self.tree.configure(height=max(3, min(8, len(names))))

    def _row(self, key: str, **cols) -> None:
        if not self.tree.exists(key):
            return
        tag = cols.pop("_tag", None)
        for name, val in cols.items():
            self.tree.set(key, name, val)
        if tag is not None:
            self.tree.item(key, tags=(tag,) if tag else ())

    # -- run -------------------------------------------------------------------------------------
    def on_show(self) -> None:
        self._set_idle_buttons()
        if self.worker.running:
            return
        current = self.state.prepare_is_current()
        self._populate(ready=current)
        if current:
            self._finished_ui(self.state.prepared, from_cache=True)
            return
        self.result.configure(text="", style="Card.TLabel")
        self.stage.configure(text="")
        self.overall.configure(text="")
        self.pbar.configure(value=0)
        snap = self.state.options.to_dict()
        if self._arrived_for != snap and not has_errors(self.state.problems()):
            self._arrived_for = snap  # auto-start once per configuration; Retry is manual after that
            self.app.after(250, self._start)

    def _set_idle_buttons(self) -> None:
        running = self.worker.running
        self.btn_prepare.state(["disabled"] if running else ["!disabled"])
        self.btn_cancel.state(["!disabled"] if running else ["disabled"])
        has = self.state.prepared is not None and self.state.prepare_is_current()
        self.btn_open.state(["!disabled"] if has else ["disabled"])
        if has and self.state.prepared.ini_diff:
            self.btn_diff.pack(side="left", padx=8)
        else:
            self.btn_diff.pack_forget()
        self.btn_prepare.configure(text="Prepare again" if has else "Prepare")

    def _start(self) -> None:
        if self.worker.running or self.state.busy or self.app.current_step != 2:
            return
        self.state.busy = True
        self.state.invalidate_prepared()
        self._populate(ready=False)
        self.result.configure(text="", style="Card.TLabel")
        self.btn_fix.pack_forget()
        self.pbar.configure(value=0)
        self._set_idle_buttons()
        self.app.refresh_nav()
        opts, env = self.state.options, self.state.env
        self.worker.start(lambda emit, cancel: run_prepare(opts, env, emit, cancel), "prepare")
        self._set_idle_buttons()

    def _cancel(self) -> None:
        if self.worker.running:
            self.worker.cancel()
            self.stage.configure(text="Cancelling...")
            self.btn_cancel.state(["disabled"])

    def shutdown(self) -> None:
        self.worker.cancel()

    # -- events ----------------------------------------------------------------------------------
    def _on_event(self, ev: object) -> None:
        if isinstance(ev, ItemUpdate):
            self._on_item(ev)
        elif isinstance(ev, StageChange):
            self.stage.configure(text=STAGE_TEXT.get(ev.stage, ev.note or ev.stage))
            if ev.stage == "build":
                self.pbar.configure(value=0)
        elif isinstance(ev, OverallProgress):
            frac = ev.done / ev.total if ev.total else 0
            self.pbar.configure(value=int(frac * 1000))
            self.overall.configure(text=f"{fmt_bytes(ev.done)} of {fmt_bytes(ev.total)}" + (f"  ·  about {fmt_eta(ev.eta_s)} left" if ev.eta_s else ""))
        elif isinstance(ev, Log):
            self._log(ev.text)
        elif isinstance(ev, Done):
            self.state.busy = False
            if isinstance(ev.result, PrepareResult):
                self.state.set_prepared(ev.result)
                self._populate_ready_from(ev.result)
                self._finished_ui(ev.result)
            self._set_idle_buttons()
            self.app.refresh_nav()
        elif isinstance(ev, Failed):
            self.state.busy = False
            self._failed_ui(ev.error)
            self._set_idle_buttons()
            self.app.refresh_nav()

    def _on_item(self, ev: ItemUpdate) -> None:
        text = STATUS_TEXT.get(ev.status, ev.status)
        detail = ""
        tag = "busy"
        if ev.status == "downloading":
            detail = f"{fmt_bytes(ev.done)} / {fmt_bytes(ev.total)}"
            if ev.bps:
                detail += f"  {fmt_bytes(ev.bps)}/s"
            if ev.eta_s:
                detail += f"  {fmt_eta(ev.eta_s)}"
        elif ev.status == "verifying":
            detail = f"{fmt_bytes(ev.done)} / {fmt_bytes(ev.total)}"
        elif ev.status in ("ready", "cached"):
            tag = "ok"
        elif ev.status == "failed":
            tag = "bad"
            detail = ev.note[:60]
        elif ev.status == "queued":
            tag = ""
        cols = {"status": text, "detail": detail, "_tag": tag}
        if ev.version:
            cols["version"] = display_version(ev.version)
        if ev.total:
            cols["size"] = fmt_bytes(ev.total)
        if ev.provenance:
            cols["check"] = check_text(ev.provenance, ev.status in ("ready", "cached"))
        if ev.note and ev.status == "queued":
            cols["detail"] = ev.note
        self._row(ev.key, **cols)

    def _populate_ready_from(self, res: PrepareResult) -> None:
        for key, acq in res.acquired.items():
            self._row(key, status="Ready (cached)" if acq.cached else "Ready", detail="", _tag="ok")

    def _finished_ui(self, res: PrepareResult | None, from_cache: bool = False) -> None:
        self.pbar.configure(value=1000)
        self.stage.configure(text="Ready")
        self.overall.configure(text="")
        if res is None:
            return
        n = len(res.staging.files)
        mb = fmt_bytes(res.staging.total_bytes)
        extra = ""
        if res.warnings:
            extra = "\n⚠ " + "\n⚠ ".join(res.warnings)
        what = "Everything is already prepared" if (from_cache or res.up_to_date) else "Prepared"
        self.result.configure(text=f"✓ {what}: {n} files, {mb}. Press Continue to copy them to your USB stick.{extra}", style="CardOk.TLabel")
        self.btn_fix.pack_forget()

    def _failed_ui(self, err: BaseException) -> None:
        self.pbar.configure(value=0)
        if isinstance(err, Cancelled):
            self.stage.configure(text="Cancelled")
            self.result.configure(text="Cancelled. Anything already downloaded is kept, and a half-finished download will resume.", style="CardMuted.TLabel")
            return
        self.stage.configure(text="Stopped")
        self.result.configure(text=f"✖ {err}", style="CardDanger.TLabel")
        kind = getattr(err, "kind", "")
        if self.state.options.channel == Channel.LATEST and kind in ("rate_limit", "layout", "http", "network"):
            self.btn_fix.pack(side="left")
        else:
            self.btn_fix.pack_forget()
        self._log(f"ERROR: {err}")

    def _use_pinned(self) -> None:
        self.state.options.channel = Channel.PINNED
        self.state.save()
        self.btn_fix.pack_forget()
        self._start()

    # -- helpers ---------------------------------------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _toggle_log(self) -> None:
        self._log_open = not self._log_open
        if self._log_open:
            self.log_wrap.pack(fill="both", expand=True, pady=(8, 0))
            self.btn_log.configure(text="Hide details")
        else:
            self.log_wrap.pack_forget()
            self.btn_log.configure(text="Show details")

    def _open_staging(self) -> None:
        if self.state.prepared:
            open_path(self.state.prepared.staging.path)

    def _show_diff(self) -> None:
        if self.state.prepared and self.state.prepared.ini_diff:
            TextWindow(self.app, self.fonts, "launch.ini changes", self.state.prepared.ini_diff,
                       "These are the only changes made to XeUnshackle's launch.ini. Everything else, including its comments, is untouched.")
