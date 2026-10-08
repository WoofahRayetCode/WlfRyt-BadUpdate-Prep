from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ...catalog import BADUPDATE, FREEMYXE, RBB_TRIAL, XEUNSHACKLE, XEXMENU, Channel, display_version
from ...importer import ImportProblem, find_in_downloads, import_extra
from ...options import ADVANCED_SLOTS, SAFE_SLOTS, AutoLaunch, Options, effective_trial, estimate_bytes, wanted
from ...pipeline import Failed, imported_extras
from ...platform_util import open_url
from ...progress import fmt_bytes
from .. import theme as T
from ..widgets import ScrollFrame, wrapped
from ..worker import Done, Worker
from . import Page

SLOT_LABELS = {
    "BUT_A": "A button", "BUT_B": "B button", "BUT_X": "X button", "BUT_Y": "Y button", "Start": "Start", "Back": "Back",
    "LBump": "Left bumper", "RThumb": "Right stick click", "LThumb": "Left stick click",
    "Default": "Default (advanced)", "Guide": "Guide button (advanced)", "Power": "Power button (advanced)",
}


def _recommended() -> Options:
    return Options()


class ChoosePage(Page):
    def build(self) -> None:
        self._loading = True
        self._import_worker = Worker(self.app.after, self._on_import_event)
        o = self.state.options

        top = tk.Frame(self.frame, bg=T.BG)
        top.pack(fill="x", pady=(0, 10))
        self.mode = tk.StringVar(value="recommended" if o.to_dict() == _recommended().to_dict() else "custom")
        for text, val in (("Recommended", "recommended"), ("Custom", "custom")):
            ttk.Radiobutton(top, text=text, value=val, variable=self.mode, style="Toggle.TRadiobutton", command=self._mode_changed).pack(side="left", padx=(0, 4))
        self.estimate = ttk.Label(top, text="", style="BgMuted.TLabel")
        self.estimate.pack(side="right")

        self.scroll = ScrollFrame(self.frame, bg=T.BG)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.inner
        self.rec_card = self._section(body)
        self._build_recommended(self.rec_card)
        self.custom = tk.Frame(body, bg=T.BG)
        self._build_custom(self.custom)

        self.problems = tk.Frame(self.frame, bg=T.BG)
        self.problems.pack(fill="x", pady=(8, 0))

        self._push_options_to_vars(o)
        self._loading = False
        self._mode_changed()

    # -- layout helpers -------------------------------------------------------------------------
    def _section(self, parent: tk.Misc, title: str = "") -> tk.Frame:
        outer = tk.Frame(parent, bg=T.CARD, highlightbackground=T.BORDER, highlightthickness=1)
        outer.pack(fill="x", pady=(0, 10))
        inner = tk.Frame(outer, bg=T.CARD)
        inner.pack(fill="x", padx=22, pady=16)
        if title:
            ttk.Label(inner, text=title, style="CardGlow.TLabel").pack(anchor="w", pady=(0, 6))
        return inner

    def _build_recommended(self, parent: tk.Misc) -> None:
        ttk.Label(parent, text="The setup most people want", style="CardHead.TLabel").pack(anchor="w")
        self.rec_lines = tk.Frame(parent, bg=T.CARD)
        self.rec_lines.pack(fill="x", pady=(10, 4))
        for text in (
            f"{BADUPDATE.title}  {display_version(BADUPDATE.pin.tag)}  -  the official Rock Band Blitz exploit pack",
            f"{RBB_TRIAL.title}  -  so you don't need the disc",
            f"{XEUNSHACKLE.title}  {display_version(XEUNSHACKLE.pin.tag)}  -  runs unsigned code once the exploit succeeds",
            "Tested versions, each one checked against a known SHA-256",
        ):
            row = tk.Frame(self.rec_lines, bg=T.CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="✓", bg=T.CARD, fg=T.OK, font=self.fonts.bold).pack(side="left", padx=(0, 10))
            wrapped(row, text, "Card.TLabel").pack(side="left", fill="x", expand=True)
        wrapped(parent, "Want ABadAvatar, FreeMyXe, a NAND dump tool or a homebrew launcher? Switch to Custom.", "CardMuted.TLabel").pack(anchor="w", pady=(8, 0), fill="x")

    def _build_custom(self, parent: tk.Misc) -> None:
        self.v_entry = tk.StringVar()
        self.v_trial = tk.BooleanVar()
        self.v_payload = tk.StringVar()
        self.v_nand = tk.BooleanVar()
        self.v_xexmenu = tk.BooleanVar()
        self.v_channel = tk.StringVar()
        self.v_launch = tk.BooleanVar()
        self.v_slot = tk.StringVar()
        self.v_target = tk.StringVar()

        s = self._section(parent, "How you start the exploit")
        ttk.Radiobutton(s, text="Rock Band Blitz  (official v1.3 pack - works with the free arcade trial or the full game)", value="rbb", variable=self.v_entry, command=self._changed).pack(anchor="w")
        self.rb_avatar = ttk.Radiobutton(s, text="ABadAvatar  (no disc; public beta; needs avatar extra data on 17559)", value="avatar", variable=self.v_entry, command=self._changed)
        self.rb_avatar.pack(anchor="w")
        self.cb_trial = ttk.Checkbutton(s, text=f"Put the Rock Band Blitz arcade trial on the stick  (about {fmt_bytes(RBB_TRIAL.installed_bytes)}, so you don't need the disc)", variable=self.v_trial, command=self._changed)
        self.cb_trial.pack(anchor="w", pady=(8, 0))

        s = self._section(parent, "Payload  (becomes BadUpdatePayload/default.xex)")
        self.rb_payload: dict[str, ttk.Radiobutton] = {}
        for val, text in (
            ("xeunshackle", f"XeUnshackle {display_version(XEUNSHACKLE.pin.tag)}  -  full patchset + DashLaunch plugins (recommended)"),
            ("freemyxe", f"FreeMyXe {display_version(FREEMYXE.pin.tag)}  -  lighter unlock, shows your CPU key, includes XeLL"),
            ("stock", "Keep the pack's stock default.xex  -  only for boot-animation recovery"),
        ):
            rb = ttk.Radiobutton(s, text=text, value=val, variable=self.v_payload, command=self._changed)
            rb.pack(anchor="w")
            self.rb_payload[val] = rb

        s = self._section(parent, "Extras")
        ttk.Checkbutton(s, text="Simple 360 NAND Flasher, read-only  -  dump your NAND (never write it on Bad Update)", variable=self.v_nand, command=self._changed).pack(anchor="w")
        self.cb_xexmenu = ttk.Checkbutton(s, text="XexMenu  -  homebrew file manager / launcher (you download it yourself, see below)", variable=self.v_xexmenu, command=self._changed)
        self.cb_xexmenu.pack(anchor="w", pady=(6, 0))
        xm = tk.Frame(s, bg=T.CARD)
        xm.pack(fill="x", padx=(26, 0), pady=(2, 4))
        self.xm_status = ttk.Label(xm, text="", style="CardMuted.TLabel")
        self.xm_status.pack(anchor="w")
        btns = tk.Frame(xm, bg=T.CARD)
        btns.pack(anchor="w", pady=(6, 0))
        ttk.Button(btns, text="Open download page", command=lambda: open_url(XEXMENU.guided_url)).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Find in Downloads", command=self._find_download).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Import file...", command=self._import_file).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Import folder...", command=self._import_folder).pack(side="left")
        wrapped(xm, "That site blocks automated downloads, so grab XexMenu 1.2 in your browser, then import the .zip / .7z (or its folder) here.", "CardSmall.TLabel").pack(anchor="w", pady=(6, 0), fill="x")

        s = self._section(parent, "Start a tool automatically")
        self.cb_launch = ttk.Checkbutton(s, text="Set up a way to start my extra tool after the console is patched", variable=self.v_launch, command=self._changed)
        self.cb_launch.pack(anchor="w")
        self.launch_row = tk.Frame(s, bg=T.CARD)
        self.launch_row.pack(anchor="w", padx=(26, 0), pady=(6, 0))
        ttk.Label(self.launch_row, text="Start", style="Card.TLabel").pack(side="left")
        self.cmb_target = ttk.Combobox(self.launch_row, textvariable=self.v_target, state="readonly", width=16, values=("NAND flasher", "XexMenu"))
        self.cmb_target.pack(side="left", padx=8)
        self.lbl_slot = ttk.Label(self.launch_row, text="when I hold", style="Card.TLabel")
        self.lbl_slot.pack(side="left")
        self.cmb_slot = ttk.Combobox(self.launch_row, textvariable=self.v_slot, state="readonly", width=22, values=[SLOT_LABELS[k] for k in SAFE_SLOTS + ADVANCED_SLOTS])
        self.cmb_slot.pack(side="left", padx=8)
        self.launch_note = wrapped(s, "", "CardSmall.TLabel")
        self.launch_note.pack(anchor="w", fill="x", padx=(26, 0), pady=(6, 0))
        for cb in (self.cmb_target, self.cmb_slot):
            cb.bind("<<ComboboxSelected>>", lambda _e: self._changed())

        s = self._section(parent, "Versions")
        ttk.Radiobutton(s, text="Tested versions (recommended)  -  exact releases this app was checked against, verified by SHA-256; no GitHub API calls", value="pinned", variable=self.v_channel, command=self._changed).pack(anchor="w")
        ttk.Radiobutton(s, text="Latest from GitHub  -  newest releases; the layout is checked but not guaranteed, and GitHub limits anonymous requests", value="latest", variable=self.v_channel, command=self._changed).pack(anchor="w")

    # -- option <-> widget sync -----------------------------------------------------------------
    def _push_options_to_vars(self, o: Options) -> None:
        self.v_entry.set(o.entry)
        self.v_trial.set(o.rbb_trial)
        self.v_payload.set(o.payload)
        self.v_nand.set(o.nand_tool)
        self.v_xexmenu.set(o.xexmenu)
        self.v_channel.set(o.channel.value)
        al = o.autolaunch
        self.v_launch.set(al is not None)
        self.v_slot.set(SLOT_LABELS.get(al.slot if al else "BUT_Y", "Y button"))
        self.v_target.set("XexMenu" if (al and al.target == "xexmenu") else "NAND flasher")

    def _options_from_vars(self) -> Options:
        slot = next((k for k, v in SLOT_LABELS.items() if v == self.v_slot.get()), "BUT_Y")
        al = AutoLaunch(slot, "xexmenu" if self.v_target.get() == "XexMenu" else "nand") if self.v_launch.get() else None
        return Options(
            entry=self.v_entry.get() or "rbb", payload=self.v_payload.get() or "xeunshackle", rbb_trial=bool(self.v_trial.get()),
            nand_tool=bool(self.v_nand.get()), xexmenu=bool(self.v_xexmenu.get()), channel=Channel(self.v_channel.get() or "pinned"), autolaunch=al,
        )

    def _mode_changed(self) -> None:
        if self.mode.get() == "recommended":
            self.state.settings.options = _recommended()
            self._loading = True
            self._push_options_to_vars(self.state.options)
            self._loading = False
            self.custom.pack_forget()
            self.rec_card.master.pack(fill="x", pady=(0, 10))
        else:
            self.rec_card.master.pack_forget()
            self.custom.pack(fill="x")
        self._changed()

    def _changed(self) -> None:
        if self._loading:
            return
        if self.mode.get() == "custom":
            self.state.settings.options = self._normalise(self._options_from_vars())
            self._loading = True
            self._push_options_to_vars(self.state.options)
            self._loading = False
        self.state.save()
        self._refresh()
        self.app.refresh_nav()

    def _normalise(self, o: Options) -> Options:
        """Make the combination sensible instead of nagging about it."""
        if o.entry == "avatar" and o.payload == "stock":
            o.payload = "xeunshackle"
        if o.payload == "stock":
            o.autolaunch = None
        if o.autolaunch:
            # the hotkey needs a tool to launch
            avail = [t for t, on in (("nand", o.nand_tool), ("xexmenu", o.xexmenu)) if on]
            if not avail:
                o.autolaunch = None
            elif o.autolaunch.target not in avail:
                o.autolaunch.target = avail[0]
        if o.xexmenu and "xexmenu" not in self.state.imported:
            o.xexmenu = False
        return o

    # -- enable/disable + messages ---------------------------------------------------------------
    def _refresh(self) -> None:
        o = self.state.options
        avatar = o.entry == "avatar"
        self.cb_trial.state(["disabled"] if avatar else ["!disabled"])
        self.rb_payload["stock"].state(["disabled"] if avatar else ["!disabled"])
        has_xm = "xexmenu" in self.state.imported
        self.cb_xexmenu.state(["!disabled"] if has_xm else ["disabled"])
        if has_xm:
            ex = self.state.imported["xexmenu"]
            self.xm_status.configure(text=f"✓ Imported: {ex.entry_xex}  ({ex.file_count} files)", style="CardOk.TLabel")
        else:
            self.xm_status.configure(text="Not imported yet.", style="CardMuted.TLabel")
        tools = o.nand_tool or o.xexmenu
        launch_ok = o.payload != "stock" and tools
        self.cb_launch.state(["!disabled"] if launch_ok else ["disabled"])
        for w in (self.cmb_target, self.cmb_slot):
            w.state(["!disabled"] if (o.autolaunch and launch_ok) else ["disabled"])
        values = [n for n, on in (("NAND flasher", o.nand_tool), ("XexMenu", o.xexmenu)) if on] or ["NAND flasher", "XexMenu"]
        self.cmb_target.configure(values=values)
        if o.payload == "freemyxe":
            self.cmb_slot.pack_forget()
            self.lbl_slot.pack_forget()
            self.launch_note.configure(text="FreeMyXe starts it by itself right after patching (staged as after_patch.xex). Experimental: tools that need files next to them may not start.")
        else:
            if not self.cmb_slot.winfo_ismapped():
                self.lbl_slot.pack(side="left")
                self.cmb_slot.pack(side="left", padx=8)
            self.launch_note.configure(
                text="Shown as a diff before anything is written. If a tool doesn't start, the comments in launch.ini next to that button explain how DashLaunch uses it."
                if o.autolaunch else
                ("Tick an extra tool above first." if not tools else "")
            )
        dl, stick = estimate_bytes(o)
        self.estimate.configure(text=f"Download about {fmt_bytes(dl)}  ·  needs about {fmt_bytes(stick)} on the stick")
        for w in self.problems.winfo_children():
            w.destroy()
        for p in self.state.problems():
            style = "BgDanger.TLabel" if p.level == "error" else "BgWarn.TLabel"
            ttk.Label(self.problems, text=("✖ " if p.level == "error" else "⚠ ") + p.text, style=style, wraplength=900, justify="left").pack(anchor="w")

    # -- XexMenu import ---------------------------------------------------------------------------
    def _find_download(self) -> None:
        found = find_in_downloads()
        if not found:
            messagebox.showinfo("XexMenu", "Nothing that looks like XexMenu in your Downloads folder yet.\n\nUse 'Open download page', then try again.")
            return
        pick = found[0]
        if messagebox.askyesno("XexMenu", f"Import this file?\n\n{pick}"):
            self._start_import(pick)

    def _import_file(self) -> None:
        p = filedialog.askopenfilename(title="XexMenu archive", filetypes=[("Archives", "*.zip *.7z"), ("All files", "*.*")])
        if p:
            self._start_import(Path(p))

    def _import_folder(self) -> None:
        p = filedialog.askdirectory(title="XexMenu folder")
        if p:
            self._start_import(Path(p))

    def _start_import(self, path: Path) -> None:
        if self._import_worker.running or self.state.busy:
            return
        self.state.busy = True
        self.xm_status.configure(text=f"Importing {path.name}...", style="CardMuted.TLabel")
        self.app.refresh_nav()
        env = self.state.env
        self._import_worker.start(lambda emit, cancel: import_extra("xexmenu", path, app_dir=env.app_dir), "import")

    def _on_import_event(self, ev: object) -> None:
        if isinstance(ev, Done):
            self.state.busy = False
            self.state.imported = imported_extras(self.state.env)
            self.state.invalidate_prepared()
            self.state.settings.options.xexmenu = True
            self._loading = True
            self._push_options_to_vars(self.state.options)
            self._loading = False
            self.state.save()
            self._refresh()
            self.app.refresh_nav()
        elif isinstance(ev, Failed):
            self.state.busy = False
            self._refresh()
            self.app.refresh_nav()
            msg = str(ev.error) if isinstance(ev.error, ImportProblem) else f"Import failed: {ev.error}"
            messagebox.showerror("XexMenu import", msg)

    def on_show(self) -> None:
        self.state.imported = imported_extras(self.state.env)
        self._refresh()
