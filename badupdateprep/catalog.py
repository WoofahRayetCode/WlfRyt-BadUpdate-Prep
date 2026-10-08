"""Declarative description of everything the app can put on the USB stick.

Pure data: where each item comes from (a tested `Pin` and/or the latest GitHub release) and exactly
how its zip maps onto the stick (`InstallSpec`). No name-guessing heuristics live here any more; if an
upstream zip stops matching its spec, `plan.build_plan` fails loudly instead of building a broken stick.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Channel(str, Enum):
    PINNED = "pinned"  # versions this app was layout-tested against (checksum anchored in this file)
    LATEST = "latest"  # newest GitHub release; layout is checked, not guaranteed


@dataclass(frozen=True)
class Pin:
    """A tested release. `sha256` is the independent trust anchor."""

    tag: str
    asset: str
    size: int
    sha256: str
    url: str = ""  # explicit download URL (non-GitHub); otherwise derived from owner/repo/tag/asset
    extra_hashes: tuple[tuple[str, str], ...] = ()  # e.g. (("md5", "..."), ("sha1", "..."))


@dataclass(frozen=True)
class Rule:
    """Maps zip members onto the stick. The first rule whose `src` matches a member wins."""

    src: str = "*"  # case-insensitive fnmatch on the '/'-normalised path inside the content root
    dest: str = ""  # USB-relative directory the member is placed under
    rename_to: str = ""  # rename the matched file (only meaningful for single-file globs)
    skip: bool = False  # drop matching members
    overrides: bool = False  # may replace a file an earlier layer placed (stock default.xex)


@dataclass(frozen=True)
class InstallSpec:
    content_root: str = "auto"  # "auto" = strip a sole wrapper dir | "none" | "contains:<DirName>"
    prefer_dir: str = ""  # tie-break for "contains:" (e.g. "Rock Band Blitz")
    rules: tuple[Rule, ...] = (Rule(),)
    must_provide: tuple[str, ...] = ()  # globs that must be hit by the final plan, else layout drift error
    expect: tuple[str, ...] = ()  # missing -> warning only


@dataclass(frozen=True)
class Artifact:
    key: str
    title: str
    kind: Literal["exploit", "payload", "extra", "content"]
    owner: str = ""
    repo: str = ""
    asset_re: str = ""  # regex that must match exactly one release asset (latest channel)
    allow_prerelease: bool = False
    source: Literal["github", "url", "manual"] = "github"
    pin: Pin | None = None
    install: InstallSpec = field(default_factory=InstallSpec)
    ships_default_xex: bool = False  # the zip itself provides BadUpdatePayload/default.xex
    notes: str = ""
    guided_url: str = ""  # page to open in the user's browser (manual items)
    beta: bool = False
    installed_bytes: int = 0  # size on the stick when it differs a lot from the zip (0 = use the pin's size)

    @property
    def project_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}" if self.owner else self.guided_url


_VERSION_RX = re.compile(r"v?\d[\w.\-]*$")


def display_version(tag: str) -> str:
    """'BadUpdate-v1.3' -> 'v1.3', '1.2' -> '1.2', 'archive.org' -> 'archive.org'."""
    m = _VERSION_RX.search(tag)
    return m.group(0) if m else tag


RBB_TITLE_ID = "5841122D"
RBB_TRIAL_URL = "https://archive.org/download/rbblitz-trial/RBBlitz_Trial.zip"

# --- Exploit packs ---------------------------------------------------------------------------------
BADUPDATE = Artifact(
    key="badupdate",
    title="Xbox360BadUpdate Retail USB",
    kind="exploit",
    owner="grimdoomer",
    repo="Xbox360BadUpdate",
    asset_re=r"(?i)usb.*\.zip$",
    pin=Pin(
        tag="BadUpdate-v1.3",
        asset="Xbox360BadUpdate-Retail-USB-v1.3.zip",
        size=27_996_626,
        sha256="cfd56bca11ec94bbb132a29633eeea82c0f02c016a83f9a8e6c9df2714dfe003",
    ),
    install=InstallSpec(
        content_root="contains:BadUpdatePayload",
        prefer_dir="Rock Band Blitz",
        must_provide=(
            "BadUpdatePayload/default.xex",
            "BadUpdatePayload/BadUpdateExploit-2ndStage.bin",
            f"Content/0000000000000000/{RBB_TITLE_ID}/00000001/*",
        ),
    ),
    ships_default_xex=True,
    notes="Official exploit pack (v1.3+ is Rock Band Blitz only).",
)
ABADAVATAR = Artifact(
    key="abadavatar",
    title="ABadAvatar",
    kind="exploit",
    owner="shutterbug2000",
    repo="ABadAvatar",
    asset_re=r"(?i)\.zip$",
    allow_prerelease=True,
    pin=Pin(
        tag="vPB1.0",
        asset="ABadAvatar-publicbeta1.0.zip",
        size=204_279,
        sha256="036718c53e76c8845e9459fc414b5e74b9a6f45a31e7ecf7de561d84bab51d66",
    ),
    install=InstallSpec(
        content_root="auto",
        must_provide=("BadUpdatePayload/update_data.bin", "Content/E0002FF78DFBDE7B/*"),
    ),
    ships_default_xex=False,
    beta=True,
    notes="Avatar dashboard entry (no disc). Public beta; needs avatar extra data on 17559.",
)

# --- Payloads (become BadUpdatePayload/default.xex) --------------------------------------------------
XEUNSHACKLE = Artifact(
    key="xeunshackle",
    title="XeUnshackle",
    kind="payload",
    owner="Byrom90",
    repo="XeUnshackle",
    asset_re=r"(?i)\.zip$",
    pin=Pin(
        tag="v1.03",
        asset="XeUnshackle-BETA-v1_03.zip",
        size=4_062_601,
        sha256="dfc8cbc8ed2ddb6d2f453a52ade2079d5f0d804bbd9a4635de00bcbd236f792c",
    ),
    install=InstallSpec(
        content_root="auto",  # strips XeUnshackle-BETA-v1_03/
        rules=(
            Rule(src="README*", skip=True),
            Rule(src="BadUpdatePayload/default.xex", overrides=True),
            Rule(),
        ),
        must_provide=("BadUpdatePayload/default.xex", "launch.ini"),
    ),
    notes="Full HV/kernel patchset + DashLaunch plugins. Recommended payload.",
)
FREEMYXE = Artifact(
    key="freemyxe",
    title="FreeMyXe",
    kind="payload",
    owner="FreeMyXe",
    repo="FreeMyXe",
    asset_re=r"(?i)\.zip$",
    pin=Pin(
        tag="1.2",
        asset="FreeMyXe-1.2-xell-a36ed6b.zip",
        size=255_116,
        sha256="3030fb9f55b03862d7375785f2563fe3d715fe1e0d50c37b066c78585377a62e",
    ),
    install=InstallSpec(
        content_root="none",  # flat zip; its own docs say: extract into BadUpdatePayload/, rename FreeMyXe.xex
        rules=(
            Rule(src="*.txt", skip=True),
            Rule(src="FreeMyXe.xex", dest="BadUpdatePayload", rename_to="default.xex", overrides=True),
            Rule(dest="BadUpdatePayload"),
        ),
        must_provide=("BadUpdatePayload/default.xex",),
        expect=("BadUpdatePayload/FreeMyXe.ini",),
    ),
    notes="Lighter hypervisor unlock. Shows the CPU key. XeLL launcher included.",
)

# --- Extras ----------------------------------------------------------------------------------------
NAND_RO = Artifact(
    key="nand_ro",
    title="Simple 360 NAND Flasher (read-only)",
    kind="extra",
    owner="alex-free",
    repo="XDK_Projects",
    asset_re=r"(?i)read.?only.*\.zip$",
    # alex-free publishes no digest for this asset; this hash is trust-on-first-use (computed 2026-10-07).
    pin=Pin(
        tag="v1.5b",
        asset="simple-360-nand-flasher-v1.5b-read-only.zip",
        size=1_068_038,
        sha256="2effcc2862cd7a160f5bc5d966d5b912d2ee1d4579d4d10183c231868470b7c7",
    ),
    install=InstallSpec(
        content_root="auto",
        rules=(Rule(dest="Apps/Simple360NANDFlasher"),),
        must_provide=("Apps/Simple360NANDFlasher/Default.xex",),
    ),
    notes="Dump-only NAND tool. Never write NAND on a Bad Update console - it can brick.",
)
XEXMENU = Artifact(
    key="xexmenu",
    title="XexMenu (homebrew launcher)",
    kind="extra",
    source="manual",
    guided_url="https://consolemods.org/wiki/File:XeXmenu_12.7z",
    install=InstallSpec(content_root="none", rules=(Rule(dest="Apps/XexMenu"),)),
    notes="Recommended by XeUnshackle. The host blocks scripted downloads, so you download it in your browser "
    "and import it here.",
)
RBB_TRIAL = Artifact(
    key="rbb_trial",
    title="Rock Band Blitz arcade trial",
    kind="content",
    source="url",
    pin=Pin(
        tag="archive.org",
        asset="RBBlitz_Trial.zip",
        size=286_678_579,
        sha256="0219d184ce6a6118d2951af1d8c2c21e5777ffc5f7dc740d8a1420b09a3c7ebc",
        url=RBB_TRIAL_URL,
        extra_hashes=(
            ("md5", "9511d5a87caeca0f70183c06b22bcd3e"),
            ("sha1", "e0818b9d71d2994d22154ab9366cd6eec5aa1262"),
        ),
    ),
    installed_bytes=373_694_464,
    install=InstallSpec(
        content_root="none",  # the zip's only top-level dir *is* Content/; never unwrap it
        must_provide=(f"Content/0000000000000000/{RBB_TITLE_ID}/000D0000/*",),
    ),
    notes="Delisted free XBLA demo (Internet Archive). Lets Games launch Rock Band Blitz with no disc.",
)

CATALOG: dict[str, Artifact] = {
    a.key: a for a in (BADUPDATE, ABADAVATAR, XEUNSHACKLE, FREEMYXE, NAND_RO, XEXMENU, RBB_TRIAL)
}
