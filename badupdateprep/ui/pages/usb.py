from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ...drives import Drive, assess, describe_path, has_errors, list_drives
from ...pipeline import CopyReport, Failed, Log, OverallProgress, StageChange, preview_copy, run_copy
from ...platform_util import eject_hint, open_path
from ...progress import Cancelled, fmt_bytes, fmt_eta
from .. import theme as T
from ..state import usb_candidates
from ..widgets import card, wrapped
from ..worker import Done, Worker
from . import Page

REFRESH_MS = 3000
ICON = {"error": "✖", "warn": "⚠", "info": "✓"}
STYLE = {"error": "CardDanger.TLabel", "warn": "CardWarn.TLabel", "info": "CardOk.TLabel"}


class UsbPage(Page):
    continue_label = "Continue"

    def build(self) -> None:
        self.scan = Worker(self.app.after, self._on_scan)
        self.copier = Worker(self.app.after, self._on_copy_event)
        self._visible = False
        self._drives: list[Drive] = []
        self._rows: list[Drive] = []
        self._selected: Drive | None = None
        self._last_sig: tuple | None = None
        self._timer = None

        c = card(self.frame)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=26, pady=20)

        head = tk.Frame(inner, bg=T.CARD)
        head.pack(fill="x")
        ttk.Label(head, text="Choose your USB stick", style="CardHead.TLabel").pack(side="left")
        ttk.Button(head, text="Browse folder...", command=self._browse).pack(side="right")
        ttk.Button(head, text="Refresh", command=self._scan_now).pack(side="right", padx=8)
        self.show_all = tk.BooleanVar(value=self.state.settings.show_fixed)
        ttk.Checkbutton(head, text="Show internal drives", variable=self.show_all, command=self._toggle_fixed).pack(side="right", padx=8)
        wrapped(inner, "Plug the stick in; it appears below by itself. It must be FAT32. This app never formats a drive and only ever adds or replaces its own files.", "CardMuted.TLabel").pack(anchor="w", fill="x", pady=(4, 10))

        cols = (("drive", "Drive", 270), ("fs", "Format", 80), ("size", "Size", 90), ("free", "Free", 90), ("note", "", 300))
        self.tree = ttk.Treeview(inner, columns=[c[0] for c in cols], show="headings", height=4, selectmode="browse")
        for key, label, width in cols:
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=width, anchor="w", stretch=key in ("drive", "note"))
        self.tree.pack(fill="x")
        self.tree.tag_configure("bad", foreground=T.DANGER)
        self.tree.tag_configure("warn", foreground=T.WARN)
        self.tree.tag_configure("good", foreground=T.OK)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.empty = ttk.Label(inner, text="", style="CardMuted.TLabel")
        self.empty.pack(anchor="w", pady=(6, 0))

        self.findings = tk.Frame(inner, bg=T.CARD)
        self.findings.pack(fill="x", pady=(10, 0))

        ttk.Separator(inner).pack(fill="x", pady=14)
        row = tk.Frame(inner, bg=T.CARD)
        row.pack(fill="x")
        self.btn_copy = ttk.Button(row, text="Copy to USB", style="Accent.TButton", command=self._copy)
        self.btn_copy.pack(side="left")
        self.btn_cancel = ttk.Button(row, text="Cancel", command=self._cancel)
        self.btn_cancel.pack(side="left", padx=8)
        self.btn_staging = ttk.Button(row, text="Open staged folder", command=lambda: self.state.prepared and open_path(self.state.prepared.staging.path))
        self.btn_staging.pack(side="right")
        bar = tk.Frame(inner, bg=T.CARD)
        bar.pack(fill="x", pady=(12, 2))
        self.stage = ttk.Label(bar, text="", style="CardBold.TLabel")
        self.stage.pack(side="left")
        self.overall = ttk.Label(bar, text="", style="CardMuted.TLabel")
        self.overall.pack(side="right")
        self.pbar = ttk.Progressbar(inner, mode="determinate", maximum=1000)
        self.pbar.pack(fill="x", pady=(2, 6))
        self.result = wrapped(inner, "", "Card.TLabel")
        self.result.pack(anchor="w", fill="x")

    # -- lifecycle -------------------------------------------------------------------------------
    def on_show(self) -> None:
        self._visible = True
        self._update_buttons()
        if self.state.copy_report and self.state.copy_report.ok:
            self._show_success(self.state.copy_report)
        self._scan_now()
        self._schedule()

    def on_hide(self) -> None:
        self._visible = False

    def shutdown(self) -> None:
        self._visible = False
        self.copier.cancel()

    def _schedule(self) -> None:
        if self._timer is None:
            self._timer = self.app.after(REFRESH_MS, self._tick)

    def _tick(self) -> None:
        self._timer = None
        if not self._visible:
            return
        if not self.copier.running:
            self._scan_now()
        self._schedule()

    # -- scanning --------------------------------------------------------------------------------
    def _scan_now(self) -> None:
        if self.scan.running:
            return
        fixed = bool(self.show_all.get())
        self.scan.start(lambda emit, cancel: list_drives(include_fixed=fixed), "scan")

    def _toggle_fixed(self) -> None:
        self.state.settings.show_fixed = bool(self.show_all.get())
        self.state.save()
        self._last_sig = None
        self._scan_now()

    def _on_scan(self, ev: object) -> None:
        if isinstance(ev, Done) and isinstance(ev.result, list):
            self._set_drives(ev.result)
        elif isinstance(ev, Failed):
            self.empty.configure(text=f"Couldn't list drives: {ev.error}")

    def _set_drives(self, drives: list[Drive]) -> None:
        # keep a browsed folder visible alongside detected drives
        extra = [d for d in self._rows if d.mount and d.device == "folder"]
        sig = tuple((d.mount, d.label, d.fs, d.size, d.free, d.mounted, d.system) for d in drives + extra)
        self._drives = drives
        if sig == self._last_sig:
            return
        self._last_sig = sig
        prev = self._selected.mount if self._selected else None
        self._rows = drives + extra
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        need, repl = self._need()
        for i, d in enumerate(self._rows):
            fs = assess(d, need, repl, protected=self._protected())
            tag = "bad" if has_errors(fs) else "warn" if any(f.level == "warn" for f in fs) else "good"
            note = next((f.text for f in fs if f.level == "error"), next((f.text for f in fs if f.level == "warn"), "Ready"))
            name = d.display if d.mounted else f"{d.label or d.device} (not mounted)"
            self.tree.insert("", "end", iid=str(i), values=(name, d.fs or "?", fmt_bytes(d.size) if d.size else "?", fmt_bytes(d.free) if d.free is not None else "?", note[:70]), tags=(tag,))
        self.tree.configure(height=max(3, min(6, len(self._rows) or 3)))
        if not self._rows:
            self.empty.configure(text="No USB stick found yet. Plug one in (FAT32), or use Browse folder...")
        else:
            self.empty.configure(text="")
        pick: int | None = None
        if prev:
            pick = next((i for i, d in enumerate(self._rows) if d.mount == prev), None)
        if pick is None and not self._selected:
            pick = usb_candidates(self._rows, self.state.settings.last_usb, self.state.settings.last_usb_label)
        if pick is not None:
            self.tree.selection_set(str(pick))
        else:
            self._selected = None
            self._render_findings()
            self._update_buttons()

    def _protected(self) -> list[Path]:
        return [self.state.env.app_dir, Path.home()]

    def _need(self) -> tuple[int, int]:
        st = self.state.prepared.staging if self.state.prepared else None
        return (st.total_bytes, 0) if st else (0, 0)

    # -- selection -------------------------------------------------------------------------------
    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        self._selected = self._rows[int(sel[0])] if sel and int(sel[0]) < len(self._rows) else None
        self._render_findings()
        self._update_buttons()

    def _browse(self) -> None:
        p = filedialog.askdirectory(title="Folder on your USB stick (its root)")
        if not p:
            return
        d = describe_path(Path(p), list_drives(include_fixed=True))  # all drives: a folder on an internal disk must be recognised as such
        from dataclasses import replace

        d = replace(d, device="folder", label=d.label or Path(p).name)
        self._rows = [r for r in self._rows if r.device != "folder"] + [d]
        self._last_sig = None
        self._set_drives(self._drives)
        for i, r in enumerate(self._rows):
            if r.device == "folder":
                self.tree.selection_set(str(i))

    def _plan(self):
        if not (self.state.prepared and self._selected and self._selected.mounted and self._selected.mount):
            return None
        try:
            return preview_copy(self.state.prepared.staging, Path(self._selected.mount))
        except Exception:
            return None

    def _render_findings(self) -> None:
        for w in self.findings.winfo_children():
            w.destroy()
        d = self._selected
        if not d:
            return
        plan = self._plan()
        need, repl = (plan.space_needed, 0) if plan else self._need()
        for f in assess(d, need, repl, protected=self._protected()):
            ttk.Label(self.findings, text=f"{ICON[f.level]}  {f.text}", style=STYLE[f.level], wraplength=880, justify="left").pack(anchor="w")
        if plan:
            ttk.Label(self.findings, text="\u2022  " + "; ".join(self._plan_bits(plan)) + ".", style="CardMuted.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(4, 0))

    @staticmethod
    def _plan_bits(plan) -> list[str]:
        writing = len(plan.write) - len(plan.unchanged)
        bits = [f"{writing} files ({fmt_bytes(plan.bytes_needed)}) will be written"] if writing else ["nothing new to write"]
        if plan.unchanged:
            bits.append(f"{len(plan.unchanged)} files already match and are skipped")
        if plan.replace:
            bits.append(f"{len(plan.replace)} existing files replaced")
        if plan.stale:
            bits.append(f"up to {len(plan.stale)} old files from a previous run removed (only if you haven't changed them)")
        bits.append("everything else on the stick is left alone")
        if plan.kept:
            bits.append("your existing " + ", ".join(plan.kept) + " is kept as it is")
        if plan.backups:
            bits.append("your existing " + ", ".join(plan.backups) + " is backed up (.bak-date) before being replaced")
        return bits

    def _update_buttons(self) -> None:
        running = self.copier.running
        ok = (
            not running and self._selected is not None and self.state.prepared is not None
            and not has_errors(assess(self._selected, *self._plan_numbers(), protected=self._protected()))
        )
        self.btn_copy.state(["!disabled"] if ok else ["disabled"])
        self.btn_cancel.state(["!disabled"] if running else ["disabled"])
        self.btn_copy.configure(text="Copy again" if (self.state.copy_report and self.state.copy_report.ok) else "Copy to USB")

    def _plan_numbers(self) -> tuple[int, int]:
        plan = self._plan()
        return (plan.space_needed, 0) if plan else self._need()

    # -- copying ---------------------------------------------------------------------------------
    def _copy(self) -> None:
        d, prepared = self._selected, self.state.prepared
        if not d or not prepared or self.copier.running or self.state.busy:
            return
        plan = self._plan()
        if plan is None:
            return
        msg = (
            f"Copy to:\n\n  {d.display}\n  {d.fs or 'unknown format'}, {fmt_bytes(d.size) if d.size else '?'} total, {fmt_bytes(d.free) if d.free is not None else '?'} free\n\n"
            + "\n".join("\u2022 " + b[0].upper() + b[1:] + "." for b in self._plan_bits(plan))
            + "\n\nNothing is formatted."
        )
        if not messagebox.askokcancel("Copy to USB", msg):
            return
        self.state.busy = True
        self.state.copy_report = None
        self.result.configure(text="", style="Card.TLabel")
        self.pbar.configure(value=0)
        env, st, dest = self.state.env, prepared.staging, Path(d.mount)
        self.copier.start(lambda emit, cancel: run_copy(st, dest, env, emit, cancel, drive=d), "copy")
        self._update_buttons()
        self.app.refresh_nav()

    def _cancel(self) -> None:
        if self.copier.running:
            self.copier.cancel()
            self.stage.configure(text="Cancelling...")

    def _on_copy_event(self, ev: object) -> None:
        if isinstance(ev, StageChange):
            self.stage.configure(text=ev.note or ev.stage)
            self.pbar.configure(value=0)
        elif isinstance(ev, OverallProgress):
            self.pbar.configure(value=int(1000 * ev.done / ev.total) if ev.total else 0)
            self.overall.configure(text=f"{fmt_bytes(ev.done)} of {fmt_bytes(ev.total)}" + (f"  ·  about {fmt_eta(ev.eta_s)} left" if ev.eta_s else ""))
        elif isinstance(ev, Done):
            self.state.busy = False
            rep = ev.result
            if isinstance(rep, CopyReport):
                self.state.copy_report = rep
                if rep.ok:
                    d = self._selected
                    if d:
                        self.state.settings.last_usb, self.state.settings.last_usb_label = d.mount, d.label
                        self.state.save()
                    self._show_success(rep)
                else:
                    self.stage.configure(text="Problem")
                    lines = "\n".join(f"✖ {rel}: {why}" for rel, why in rep.bad[:6])
                    self.result.configure(text=f"Some files didn't read back correctly:\n{lines}\nTry copying again, or use a different stick.", style="CardDanger.TLabel")
            self._last_sig = None
            self._render_findings()
            self._update_buttons()
            self.app.refresh_nav()
        elif isinstance(ev, Failed):
            self.state.busy = False
            if isinstance(ev.error, Cancelled):
                self.stage.configure(text="Cancelled")
                self.result.configure(text="Cancelled. Files already copied are complete; nothing half-written is left behind.", style="CardMuted.TLabel")
            else:
                self.stage.configure(text="Stopped")
                self.result.configure(text=f"✖ {ev.error}", style="CardDanger.TLabel")
            self._update_buttons()
            self.app.refresh_nav()

    def _show_success(self, rep: CopyReport) -> None:
        self.pbar.configure(value=1000)
        self.stage.configure(text="Done")
        self.overall.configure(text="")
        extra = (f" {rep.unchanged} were already up to date." if rep.unchanged else "") + (f" {rep.stale_removed} old files removed." if rep.stale_removed else "")
        if rep.left_alone:
            extra += f" {rep.left_alone} old files were left alone because you had changed them."
        self.result.configure(
            text=f"✓ Copied {rep.copied} files ({fmt_bytes(rep.bytes)}) and read them all back successfully.{extra}\n\n"
                 f"Now eject the stick safely. {eject_hint()}",
            style="CardOk.TLabel",
        )
