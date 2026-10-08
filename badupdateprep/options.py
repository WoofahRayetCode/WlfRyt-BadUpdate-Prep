"""What the user chose, whether it makes sense, and what to tell them to do on the console."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Literal

from .catalog import (
    ABADAVATAR,
    BADUPDATE,
    CATALOG,
    FREEMYXE,
    NAND_RO,
    RBB_TRIAL,
    XEUNSHACKLE,
    XEXMENU,
    Artifact,
    Channel,
)

# DashLaunch hotkey slots that are safe to offer; Default/Guide/Power change normal console behaviour.
SAFE_SLOTS = ("BUT_A", "BUT_B", "BUT_X", "BUT_Y", "Start", "Back", "LBump", "RThumb", "LThumb")
ADVANCED_SLOTS = ("Default", "Guide", "Power")

NAND_ENTRY = "Apps/Simple360NANDFlasher/Default.xex"


@dataclass
class AutoLaunch:
    slot: str = "BUT_Y"
    target: Literal["xexmenu", "nand"] = "nand"


@dataclass
class Options:
    entry: Literal["rbb", "avatar"] = "rbb"
    payload: Literal["xeunshackle", "freemyxe", "stock"] = "xeunshackle"
    rbb_trial: bool = True
    nand_tool: bool = False
    xexmenu: bool = False
    channel: Channel = Channel.PINNED
    autolaunch: AutoLaunch | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["channel"] = self.channel.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        """Tolerant loader: unknown keys and bad values fall back to defaults."""
        base = cls()
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in known}
        try:
            kw["channel"] = Channel(kw.get("channel", base.channel.value))
        except ValueError:
            kw["channel"] = base.channel
        al = kw.get("autolaunch")
        kw["autolaunch"] = AutoLaunch(**{k: v for k, v in al.items() if k in ("slot", "target")}) if isinstance(al, dict) else None
        out = cls(**kw)
        if out.entry not in ("rbb", "avatar"):
            out.entry = base.entry
        if out.payload not in ("xeunshackle", "freemyxe", "stock"):
            out.payload = base.payload
        return out


@dataclass(frozen=True)
class Problem:
    level: Literal["error", "warn"]
    code: str
    text: str


def effective_trial(o: Options) -> bool:
    """The Blitz trial is only meaningful for the Rock Band Blitz entry."""
    return o.rbb_trial and o.entry == "rbb"


def wanted(o: Options) -> list[Artifact]:
    """Artifacts to fetch/import, in the order they are layered onto the stick."""
    arts: list[Artifact] = [ABADAVATAR if o.entry == "avatar" else BADUPDATE]
    if effective_trial(o):
        arts.append(RBB_TRIAL)
    if o.payload == "xeunshackle":
        arts.append(XEUNSHACKLE)
    elif o.payload == "freemyxe":
        arts.append(FREEMYXE)
    if o.nand_tool:
        arts.append(NAND_RO)
    if o.xexmenu:
        arts.append(XEXMENU)
    return arts


def downloadable(o: Options) -> list[Artifact]:
    return [a for a in wanted(o) if a.source != "manual"]


def validate(o: Options, imported: set[str] | frozenset[str] = frozenset()) -> list[Problem]:
    ps: list[Problem] = []
    if o.entry == "avatar" and o.payload == "stock":
        ps.append(Problem("error", "avatar_needs_payload", "ABadAvatar doesn't ship a default.xex - pick XeUnshackle or FreeMyXe."))
    if o.xexmenu and "xexmenu" not in imported:
        ps.append(Problem("error", "xexmenu_not_imported", "Import XexMenu first (Open download page, then Import file/folder)."))
    al = o.autolaunch
    if al:
        if o.payload == "stock":
            ps.append(Problem("error", "autolaunch_needs_payload", "Hotkey launching needs XeUnshackle or FreeMyXe."))
        if al.target == "nand" and not o.nand_tool:
            ps.append(Problem("error", "autolaunch_target_missing", "The hotkey points at the NAND tool, which isn't included."))
        if al.target == "xexmenu" and not o.xexmenu:
            ps.append(Problem("error", "autolaunch_target_missing", "The hotkey points at XexMenu, which isn't included."))
        if o.payload == "xeunshackle" and al.slot not in SAFE_SLOTS + ADVANCED_SLOTS:
            ps.append(Problem("error", "autolaunch_bad_slot", f"Unknown hotkey slot {al.slot!r}."))
        if o.payload == "freemyxe":
            ps.append(Problem("warn", "autolaunch_experimental", "FreeMyXe auto-launch (after_patch.xex) is experimental: tools that need sibling files may not start."))
    if o.nand_tool and not o.xexmenu and not (al and al.target == "nand"):
        ps.append(
            Problem(
                "warn",
                "tool_unreachable",
                "The NAND tool is on the stick but nothing will launch it. Add XexMenu or set a hotkey for it.",
            )
        )
    if o.entry == "avatar" and o.rbb_trial:
        ps.append(Problem("warn", "trial_ignored", "The Rock Band Blitz trial isn't used with ABadAvatar and will be skipped."))
    return ps


def has_errors(problems: list[Problem]) -> bool:
    return any(p.level == "error" for p in problems)


# --- the "on the console" checklist ---------------------------------------------------------------------
@dataclass(frozen=True)
class ChecklistItem:
    text: str
    detail: str = ""
    important: bool = False


def checklist_for(o: Options, hotkey_hint: str = "") -> list[ChecklistItem]:
    items = [
        ChecklistItem("Check System Info shows 2.0.17559.0", "Settings > System > Console Settings > System Info. Any other version won't work."),
        ChecklistItem(
            "Disconnect from Xbox Live",
            "Unplug Ethernet and forget saved Wi-Fi. Running the exploit while connected can permanently ban the console.",
            important=True,
        ),
        ChecklistItem("Unplug every other USB / external drive", "Use one USB only. Rock Band 1/2/3 DLC drives especially must be disconnected."),
    ]
    if o.entry == "rbb":
        where = "My Xbox > Games > Rock Band Blitz" + (" (the trial on your USB)" if effective_trial(o) else " (your disc or HDD copy)")
        items.append(ChecklistItem("Plug in the USB and start the game", f"{where}. At the title screen wait a few seconds, then press A."))
        items.append(ChecklistItem("Watch for the ring of light", "The menu and music keep moving while it runs. If they stop, power off and retry."))
    else:
        items.append(ChecklistItem("Plug in the USB and open the Avatar editor entry", "Don't sign into the exploit profile."))
    items.append(ChecklistItem("Success = all four ring-of-light segments green", "Then the payload takes over."))
    if o.payload == "xeunshackle":
        items.append(ChecklistItem("Keep the USB plugged in", "DashLaunch copies a key executable to your storage device; it must stay connected.", important=True))
        items.append(
            ChecklistItem(
                "Keep the MAC address backup",
                "XeUnshackle saves one into BadUpdatePayload/ the first time it runs. Copy it somewhere safe.",
            )
        )
    elif o.payload == "freemyxe":
        items.append(ChecklistItem("Write down the CPU key", "FreeMyXe shows it front and centre."))
    if hotkey_hint:
        items.append(ChecklistItem("Launch your tool", hotkey_hint))
    if o.nand_tool:
        items.append(ChecklistItem("NAND tool: dump only", "Never write NAND on a Bad Update console - it can brick.", important=True))
    items.append(ChecklistItem("It's temporary", "Power off or reboot and the hack is gone. Repeat these steps next time.", important=True))
    return items


def estimate_bytes(o: Options) -> tuple[int, int]:
    """(download size, space needed on the stick) from the tested pins - a pre-download estimate for the UI."""
    dl = stick = 0
    for a in wanted(o):
        if a.pin is None:
            continue
        dl += a.pin.size
        stick += a.installed_bytes or a.pin.size
    return dl, stick
