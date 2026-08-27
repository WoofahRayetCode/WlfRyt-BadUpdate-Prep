from __future__ import annotations

import os
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

from badupdateprep import APP_NAME, APP_VERSION, REPO_URL
from badupdateprep.assemble import assemble_usb, copy_tree_to_usb, list_windows_removable
from badupdateprep.catalog import ABADAVATAR, BADUPDATE, FREEMYXE, NAND_RO, RBB_TRIAL, XEUNSHACKLE, Artifact
from badupdateprep.github import DownloadError, download_to, filename_from_url, resolve_artifact
from badupdateprep.ui import theme as T
from badupdateprep.wiki import PAGES as WIKI_PAGES, load_page

STEPS = ("Welcome", "Setup", "Download", "USB", "Guide")


class HoverButton(tk.Button):
    def __init__(self, master, hover=None, **kw):
        self._bg = kw.get("bg", T.CARD)
        self._hover = hover or T.ACCENT_HOVER
        super().__init__(master, **kw)
        self.bind("<Enter>", lambda _e: self.configure(bg=self._hover) if self["state"] != "disabled" else None)
        self.bind("<Leave>", lambda _e: self.configure(bg=self._bg))


class Wizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry("1000x740")
        self.minsize(880, 640)
        self.configure(bg=T.BG)
        self.cache = Path.home() / ".wlfrit-badupdate"
        self.dl_dir = self.cache / "downloads"
        self.staging = self.cache / "usb_staging"
        self.downloads: dict[str, Path] = {}
        self.entry = tk.StringVar(value="rbb")
        self.payload = tk.StringVar(value="xeunshackle")
        self.nand_tool = tk.BooleanVar(value=True)
        self.rbb_trial = tk.BooleanVar(value=True)
        self.usb_path = tk.StringVar(value="")
        self._step = 0
        self._busy = False
        self._pulse = 0
        self._build()
        self._show_step(0)
        self._tick_pulse()

    def _btn(self, primary: bool) -> dict:
        if primary:
            return dict(
                bg=T.ACCENT, fg="white", activebackground=T.ACCENT_HOVER, activeforeground="white",
                relief="flat", font=T.FONT_H, padx=16, pady=7, cursor="hand2", bd=0,
            )
        return dict(
            bg=T.CARD, fg=T.TEXT, activebackground=T.BORDER, activeforeground=T.TEXT,
            relief="flat", font=T.FONT, padx=12, pady=6, cursor="hand2", bd=0,
        )

    def _build(self) -> None:
        header = tk.Frame(self, bg=T.PANEL, height=70)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=APP_NAME, bg=T.PANEL, fg=T.GLOW, font=T.FONT_TITLE).pack(side="left", padx=22)
        tk.Label(header, text="Xbox 360  ·  dashboard 17559  ·  non-persistent", bg=T.PANEL, fg=T.MUTED, font=T.FONT_SMALL).pack(
            side="left", padx=8
        )

        nav = tk.Frame(self, bg=T.CARD, height=46)
        nav.pack(fill="x")
        nav.pack_propagate(False)
        self._nav: list[tk.Label] = []
        for i, name in enumerate(STEPS):
            lbl = tk.Label(nav, text=f"  {i + 1}  {name}  ", bg=T.CARD, fg=T.MUTED, font=T.FONT_SMALL)
            lbl.pack(side="left", padx=6, pady=10)
            self._nav.append(lbl)

        self.body = tk.Frame(self, bg=T.BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=14)

        foot = tk.Frame(self, bg=T.PANEL, height=62)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        self.back_btn = HoverButton(foot, text="Back", command=self._back, hover=T.BORDER, **self._btn(False))
        self.back_btn.pack(side="left", padx=16, pady=12)
        self.next_btn = HoverButton(foot, text="Continue", command=self._next, **self._btn(True))
        self.next_btn.pack(side="right", padx=16, pady=12)
        self.status = tk.Label(foot, text="", bg=T.PANEL, fg=T.MUTED, font=T.FONT_SMALL)
        self.status.pack(side="right")

        self.pages = [
            self._page_welcome(),
            self._page_setup(),
            self._page_download(),
            self._page_usb(),
            self._page_playbook(),
        ]

    def _card(self, parent) -> tk.Frame:
        return tk.Frame(parent, bg=T.CARD, highlightbackground=T.BORDER, highlightthickness=1)

    def _radio(self, parent, text, var, value):
        return tk.Radiobutton(
            parent, text=text, value=value, variable=var, bg=T.CARD, fg=T.TEXT, selectcolor=T.PANEL,
            activebackground=T.CARD, activeforeground=T.TEXT, font=T.FONT, anchor="w", cursor="hand2",
        )

    def _page_welcome(self) -> tk.Frame:
        p = tk.Frame(self.body, bg=T.BG)
        c = self._card(p)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=28, pady=22)
        tk.Label(inner, text="Software hypervisor exploit for every 360", bg=T.CARD, fg=T.TEXT, font=T.FONT_H).pack(anchor="w")
        copy = (
            "Bad Update (grimdoomer) patches the hypervisor on dashboard 2.0.17559.0 so unsigned code can run "
            "until you power off. It is not a persistent softmod and is not a substitute for RGH.\n\n"
            "This app pulls the latest official GitHub zips, writes default.xex, and copies a USB tree. "
            "Tony Hawk's American Wasteland was dropped in v1.3 — Rock Band Blitz is the supported game pack.\n\n"
            "Stay offline (unplug Ethernet / forget Wi-Fi) before you trigger the exploit. Running it on Xbox Live can ban the console."
        )
        tk.Label(inner, text=copy, bg=T.CARD, fg=T.MUTED, font=T.FONT, justify="left", wraplength=860).pack(anchor="w", pady=12)
        link = tk.Label(inner, text=REPO_URL, bg=T.CARD, fg=T.GLOW, font=T.FONT, cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))
        self.ack = tk.BooleanVar(value=False)
        tk.Checkbutton(
            inner,
            text="I will disconnect from Xbox Live, I am on 17559, and I understand this unlock lasts only until reboot.",
            variable=self.ack, bg=T.CARD, fg=T.TEXT, selectcolor=T.PANEL, activebackground=T.CARD,
            activeforeground=T.TEXT, font=T.FONT, wraplength=840, justify="left",
        ).pack(anchor="w", pady=18)
        return p

    def _page_setup(self) -> tk.Frame:
        p = tk.Frame(self.body, bg=T.BG)
        c = self._card(p)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=28, pady=22)
        tk.Label(inner, text="Entry point", bg=T.CARD, fg=T.TEXT, font=T.FONT_H).pack(anchor="w")
        self._radio(inner, "Rock Band Blitz (official v1.3 USB pack — trial or full game)", self.entry, "rbb").pack(anchor="w", pady=3)
        self._radio(inner, "ABadAvatar (no disc; needs avatar extra data; GitHub beta if published)", self.entry, "avatar").pack(anchor="w")
        tk.Checkbutton(
            inner,
            text="Download Rock Band Blitz arcade trial (~273 MB, delisted free XBLA) onto the USB so you can launch the exploit with no disc.",
            variable=self.rbb_trial, bg=T.CARD, fg=T.TEXT, selectcolor=T.PANEL, activebackground=T.CARD,
            activeforeground=T.TEXT, font=T.FONT, wraplength=840, justify="left",
        ).pack(anchor="w", pady=(8, 4))
        tk.Label(inner, text="Post-exploit payload  (becomes BadUpdatePayload/default.xex)", bg=T.CARD, fg=T.TEXT, font=T.FONT_H).pack(
            anchor="w", pady=(18, 6)
        )
        self._radio(inner, "XeUnshackle — full patchset, DashLaunch plugins (recommended)", self.payload, "xeunshackle").pack(anchor="w", pady=2)
        self._radio(inner, "FreeMyXe — lighter unlock, shows CPU key", self.payload, "freemyxe").pack(anchor="w")
        self._radio(inner, "Keep stock default.xex — boot-animation recovery mode", self.payload, "stock").pack(anchor="w")
        tk.Checkbutton(
            inner,
            text="Include Simple 360 NAND Flasher (read-only dump). Do not write NAND on BadUpdate.",
            variable=self.nand_tool, bg=T.CARD, fg=T.TEXT, selectcolor=T.PANEL, activebackground=T.CARD,
            activeforeground=T.TEXT, font=T.FONT, wraplength=840, justify="left",
        ).pack(anchor="w", pady=16)
        tk.Label(
            inner,
            text="USB must be FAT32. Drives over 32 GB: format on the 360 dashboard or with Rufus/GUIformat. This app never formats.",
            bg=T.CARD, fg=T.WARN, font=T.FONT, wraplength=840, justify="left",
        ).pack(anchor="w")
        return p

    def _page_download(self) -> tk.Frame:
        p = tk.Frame(self.body, bg=T.BG)
        c = self._card(p)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=28, pady=22)
        top = tk.Frame(inner, bg=T.CARD)
        top.pack(fill="x")
        tk.Label(top, text="Downloads", bg=T.CARD, fg=T.TEXT, font=T.FONT_H).pack(side="left")
        HoverButton(top, text="Download all", command=self._start_dl, **self._btn(True)).pack(side="right")
        self.dl_status = tk.Label(inner, text="Nothing downloaded yet.", bg=T.CARD, fg=T.MUTED, font=T.FONT)
        self.dl_status.pack(anchor="w", pady=8)
        self.pbar = tk.Canvas(inner, height=12, bg=T.PANEL, highlightthickness=0)
        self.pbar.pack(fill="x", pady=6)
        self.log = tk.Text(inner, bg=T.PANEL, fg=T.GLOW, insertbackground=T.TEXT, relief="flat", font=("Consolas", 9), height=18)
        self.log.pack(fill="both", expand=True, pady=8)
        self.log.configure(state="disabled")
        return p

    def _page_usb(self) -> tk.Frame:
        p = tk.Frame(self.body, bg=T.BG)
        c = self._card(p)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=28, pady=22)
        tk.Label(inner, text="Stage files and copy to USB", bg=T.CARD, fg=T.TEXT, font=T.FONT_H).pack(anchor="w")
        row = tk.Frame(inner, bg=T.CARD)
        row.pack(fill="x", pady=10)
        tk.Entry(row, textvariable=self.usb_path, bg=T.PANEL, fg=T.TEXT, insertbackground=T.TEXT, relief="flat", font=T.FONT).pack(
            side="left", fill="x", expand=True, ipady=6, padx=(0, 8)
        )
        HoverButton(row, text="Browse", command=self._browse, hover=T.BORDER, **self._btn(False)).pack(side="left", padx=4)
        HoverButton(row, text="Refresh drives", command=self._refresh_drives, hover=T.BORDER, **self._btn(False)).pack(side="left")
        self.drive_box = tk.Listbox(
            inner, bg=T.PANEL, fg=T.TEXT, height=6, font=T.FONT, selectbackground=T.ACCENT, relief="flat",
            highlightthickness=0,
        )
        self.drive_box.pack(fill="x", pady=8)
        self.drive_box.bind("<<ListboxSelect>>", self._on_drive)
        btnrow = tk.Frame(inner, bg=T.CARD)
        btnrow.pack(fill="x", pady=8)
        HoverButton(btnrow, text="Build USB staging", command=self._build_staging, **self._btn(True)).pack(side="left")
        HoverButton(btnrow, text="Copy to USB", command=self._copy_usb, hover=T.BORDER, **self._btn(False)).pack(side="left", padx=8)
        HoverButton(btnrow, text="Open staging", command=self._open_staging, hover=T.BORDER, **self._btn(False)).pack(side="left")
        self.usb_msg = tk.Label(inner, text="", bg=T.CARD, fg=T.MUTED, font=T.FONT, wraplength=840, justify="left")
        self.usb_msg.pack(anchor="w", pady=10)
        self._refresh_drives()
        return p

    def _page_playbook(self) -> tk.Frame:
        p = tk.Frame(self.body, bg=T.BG)
        c = self._card(p)
        c.pack(fill="both", expand=True)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(inner, text="Local guide (offline copy of the Bad Update wiki)", bg=T.CARD, fg=T.TEXT, font=T.FONT_H).pack(anchor="w")
        tabs = tk.Frame(inner, bg=T.CARD)
        tabs.pack(fill="x", pady=(10, 8))
        self._wiki_tab_btns: list[tk.Button] = []
        for i, (_fn, title) in enumerate(WIKI_PAGES):
            b = HoverButton(
                tabs, text=title, hover=T.ACCENT_HOVER, **self._btn(False),
                command=lambda idx=i: self._show_wiki(idx),
            )
            b.pack(side="left", padx=3, pady=2)
            self._wiki_tab_btns.append(b)
        wrap = tk.Frame(inner, bg=T.CARD)
        wrap.pack(fill="both", expand=True)
        self.wiki_view = tk.Text(
            wrap, bg=T.PANEL, fg=T.TEXT, insertbackground=T.TEXT, relief="flat",
            font=T.FONT, wrap="word", highlightthickness=0, padx=14, pady=12, cursor="arrow",
        )
        scroll = tk.Scrollbar(wrap, bg=T.CARD, troughcolor=T.PANEL, command=self.wiki_view.yview)
        self.wiki_view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.wiki_view.pack(side="left", fill="both", expand=True)
        self.wiki_view.tag_configure("h1", font=T.FONT_TITLE, foreground=T.GLOW, spacing3=10)
        self.wiki_view.tag_configure("h2", font=T.FONT_H, foreground=T.GLOW, spacing1=12, spacing3=6)
        self.wiki_view.tag_configure("h3", font=T.FONT_H, foreground=T.TEXT, spacing1=8, spacing3=4)
        self.wiki_view.tag_configure("warn", foreground=T.WARN, font=T.FONT)
        self.wiki_view.tag_configure("code", font=("Consolas", 10), foreground=T.GLOW, background=T.BG)
        self.wiki_view.tag_configure("body", font=T.FONT, foreground=T.TEXT, spacing3=4)
        self._show_wiki(0)
        return p

    def _show_wiki(self, index: int) -> None:
        filename, _title = WIKI_PAGES[index]
        for i, b in enumerate(getattr(self, "_wiki_tab_btns", [])):
            b.configure(bg=T.ACCENT if i == index else T.CARD, fg="white" if i == index else T.TEXT)
            b._bg = T.ACCENT if i == index else T.CARD
        self.wiki_view.configure(state="normal")
        self.wiki_view.delete("1.0", "end")
        _render_wiki(self.wiki_view, load_page(filename))
        self.wiki_view.configure(state="disabled")
        self.wiki_view.see("1.0")

    def _tick_pulse(self) -> None:
        self._pulse = (self._pulse + 1) % 40
        if self._nav:
            lbl = self._nav[self._step]
            lbl.configure(fg=T.GLOW if self._pulse < 22 else T.TEXT)
        self.after(80, self._tick_pulse)

    def _show_step(self, i: int) -> None:
        for page in self.pages:
            page.pack_forget()
        self._step = i
        self.pages[i].pack(fill="both", expand=True)
        for n, lbl in enumerate(self._nav):
            if n == i:
                lbl.configure(bg=T.ACCENT, fg=T.TEXT)
            elif n < i:
                lbl.configure(bg=T.CARD, fg=T.GLOW)
            else:
                lbl.configure(bg=T.CARD, fg=T.MUTED)
        self.back_btn.configure(state="normal" if i else "disabled")
        self.next_btn.configure(text="Close" if i == len(STEPS) - 1 else "Continue")

    def _back(self) -> None:
        if not self._busy and self._step:
            self._show_step(self._step - 1)

    def _next(self) -> None:
        if self._busy:
            return
        if self._step == 0 and not self.ack.get():
            messagebox.showinfo(APP_NAME, "Confirm the Live / 17559 / reboot warnings first.")
            return
        if self._step == len(STEPS) - 1:
            self.destroy()
            return
        self._show_step(self._step + 1)

    def _wanted(self) -> list[Artifact]:
        arts: list[Artifact] = []
        if self.entry.get() == "avatar":
            arts.append(ABADAVATAR)
        else:
            arts.append(BADUPDATE)
        if self.payload.get() == "xeunshackle":
            arts.append(XEUNSHACKLE)
        elif self.payload.get() == "freemyxe":
            arts.append(FREEMYXE)
        if self.nand_tool.get():
            arts.append(NAND_RO)
        if self.rbb_trial.get():
            arts.append(RBB_TRIAL)
        return arts

    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _bar(self, frac: float) -> None:
        self.pbar.delete("all")
        w = max(self.pbar.winfo_width(), 20)
        self.pbar.create_rectangle(0, 0, int(w * min(1.0, frac)), 12, fill=T.GLOW, width=0)

    def _start_dl(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        threading.Thread(target=self._dl_worker, daemon=True).start()

    def _dl_worker(self) -> None:
        arts = self._wanted()
        try:
            for i, art in enumerate(arts):
                self.after(0, lambda a=art, i=i: self.dl_status.configure(text=f"[{i + 1}/{len(arts)}] {a.title}", fg=T.GLOW))
                url, ver = resolve_artifact(art)
                name = filename_from_url(url, art.key)
                dest = self.dl_dir / art.key / name
                self.after(0, lambda a=art, v=ver, n=name: self._log(f"-> {a.title}  {v}  {n}"))

                def prog(got, total, i=i, n=len(arts)):
                    part = (got / total) if total else 0
                    self.after(0, lambda f=(i + part) / n: self._bar(f))

                min_ok = 50 * 1024 * 1024 if art.key == "rbb_trial" else 1024
                if dest.exists() and dest.stat().st_size > min_ok:
                    self.after(0, lambda: self._log("   cached"))
                else:
                    download_to(url, dest, progress=prog)
                self.downloads[art.key] = dest
                self.after(0, lambda i=i, n=len(arts): self._bar((i + 1) / n))
            self.after(0, lambda: self.dl_status.configure(text="Ready.", fg=T.OK))
            self.after(0, lambda: self._log("Done."))
        except Exception as exc:
            tb = traceback.format_exc() if not isinstance(exc, DownloadError) else str(exc)
            self.after(0, lambda: self.dl_status.configure(text=str(exc), fg=T.DANGER))
            self.after(0, lambda: self._log(tb))
        finally:
            self._busy = False

    def _refresh_drives(self) -> None:
        self.drive_box.delete(0, "end")
        self._drives = list_windows_removable()
        if not self._drives:
            self.drive_box.insert("end", "No FAT/removable volumes — use Browse.")
            return
        for path, label in self._drives:
            self.drive_box.insert("end", f"{label}    {path}")

    def _on_drive(self, _e=None) -> None:
        sel = self.drive_box.curselection()
        if sel and getattr(self, "_drives", None) and sel[0] < len(self._drives):
            self.usb_path.set(self._drives[sel[0]][0])

    def _browse(self) -> None:
        path = filedialog.askdirectory(title="USB root (FAT32)")
        if path:
            self.usb_path.set(path)

    def _build_staging(self) -> None:
        try:
            assemble_usb(
                self.downloads,
                self.staging,
                entry=self.entry.get(),
                payload=self.payload.get(),
                include_nand_tool=self.nand_tool.get(),
                include_rbb_trial=self.rbb_trial.get(),
            )
            self.usb_msg.configure(text=f"Staging ready: {self.staging}", fg=T.OK)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _copy_usb(self) -> None:
        dest = Path(self.usb_path.get().strip())
        if not dest.exists():
            messagebox.showwarning(APP_NAME, "Pick a valid USB / folder.")
            return
        if not self.staging.exists():
            self._build_staging()
        if not self.staging.exists():
            return
        if not messagebox.askyesno(APP_NAME, f"Merge staged files into:\n{dest}"):
            return
        try:
            copy_tree_to_usb(self.staging, dest)
            self.usb_msg.configure(text=f"Copied to {dest}", fg=T.OK)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _open_staging(self) -> None:
        self.staging.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(self.staging))  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(str(self.staging))


def _render_wiki(widget: tk.Text, markdown: str) -> None:
    in_code = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            widget.insert("end", line + "\n", "code")
            continue
        if line.startswith("# "):
            widget.insert("end", line[2:] + "\n", "h1")
        elif line.startswith("## "):
            widget.insert("end", line[3:] + "\n", "h2")
        elif line.startswith("### "):
            widget.insert("end", line[4:] + "\n", "h3")
        elif line.startswith("WARNING:") or line.startswith("> [!WARNING]") or line.upper().startswith("WARNING"):
            widget.insert("end", line.lstrip("> ").replace("[!WARNING]", "WARNING") + "\n", "warn")
        elif line.startswith("|"):
            widget.insert("end", line + "\n", "code")
        else:
            widget.insert("end", line + "\n", "body")


def main() -> None:
    Wizard().mainloop()
