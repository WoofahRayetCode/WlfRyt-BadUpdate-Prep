from dataclasses import dataclass
from typing import Callable


def _n(asset: dict) -> str:
    return (asset.get("name") or "").lower()


def pick_zip_containing(*needles: str):
    def _pick(assets: list[dict]) -> str | None:
        for a in assets:
            name = _n(a)
            if name.endswith(".zip") and all(n in name for n in needles):
                return a["browser_download_url"]
        for a in assets:
            if _n(a).endswith(".zip"):
                return a["browser_download_url"]
        return None

    return _pick


def pick_first_zip(assets: list[dict]) -> str | None:
    for a in assets:
        if _n(a).endswith(".zip"):
            return a["browser_download_url"]
    return None


PICKERS: dict[str, Callable] = {
    "badupdate_usb": pick_zip_containing("usb"),
    "xeunshackle": pick_first_zip,
    "freemyxe": pick_first_zip,
    "abadavatar": pick_first_zip,
    "nand_ro": pick_zip_containing("read", "only"),
}


@dataclass(frozen=True)
class Artifact:
    key: str
    title: str
    owner: str = ""
    repo: str = ""
    pick: str = ""
    notes: str = ""
    source: str = "github"  # github | url
    url: str = ""


BADUPDATE = Artifact(
    "badupdate",
    "Xbox360BadUpdate Retail USB",
    "grimdoomer",
    "Xbox360BadUpdate",
    "badupdate_usb",
    "Official exploit pack (v1.3+ is Rock Band Blitz only).",
)
XEUNSHACKLE = Artifact(
    "xeunshackle",
    "XeUnshackle",
    "Byrom90",
    "XeUnshackle",
    "xeunshackle",
    "Full HV/kernel patchset + DashLaunch plugins. Recommended payload.",
)
FREEMYXE = Artifact(
    "freemyxe",
    "FreeMyXe",
    "FreeMyXe",
    "FreeMyXe",
    "freemyxe",
    "Lighter hypervisor unlock. CPU key on screen.",
)
ABADAVATAR = Artifact(
    "abadavatar",
    "ABadAvatar",
    "shutterbug2000",
    "ABadAvatar",
    "abadavatar",
    "Avatar dashboard entry (no disc). Needs avatar extra data on 17559.",
)
NAND_RO = Artifact(
    "nand_ro",
    "Simple 360 NAND Flasher (read-only)",
    "alex-free",
    "XDK_Projects",
    "nand_ro",
    "Dump-only NAND tool. Writing NAND on BadUpdate can brick.",
)
# Delisted XBLA arcade trial (was free). Needed on USB so the 360 can launch RBB
# without a disc. Title ID 5841122D.
RBB_TRIAL = Artifact(
    key="rbb_trial",
    title="Rock Band Blitz arcade trial",
    notes="Delisted free XBLA demo. Merges into Content/ so Games on the 360 can launch it.",
    source="url",
    url="https://archive.org/download/rbblitz-trial/RBBlitz_Trial.zip",
)

RBB_TITLE_ID = "5841122D"
