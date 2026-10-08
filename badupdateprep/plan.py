"""Turn (options + the files inside each downloaded zip) into an exact list of "member -> USB path" entries.

`build_plan` is pure and cheap: it only reads archive directories, never file contents. It is where layout
drift is caught - if an upstream zip stops matching its `InstallSpec`, the user gets a clear error and a hint to
use the tested versions instead of a stick that silently doesn't work.
"""
from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .catalog import CATALOG, Artifact, InstallSpec, Rule
from .errors import AssembleError
from .options import NAND_ENTRY, Options, wanted
from .sources import Source

FAT_MAX_FILE = 4 * 1024**3 - 1
_FAT_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
LAYOUT_HINT = " If you chose 'Latest from GitHub', switch to 'Tested versions'."

AFTER_PATCH = "BadUpdatePayload/after_patch.xex"


@dataclass(frozen=True)
class PlanEntry:
    layer: str  # artifact key
    src_key: str  # key into the sources mapping
    member: str  # full normalised path inside the source (includes the content-root prefix)
    dest: str  # USB-relative path
    size: int
    overrides: bool = False


@dataclass
class InstallPlan:
    entries: list[PlanEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def dests(self) -> list[str]:
        return sorted(e.dest for e in self.entries)

    def find(self, dest: str) -> PlanEntry | None:
        d = dest.casefold()
        return next((e for e in self.entries if e.dest.casefold() == d), None)


# --- helpers ---------------------------------------------------------------------------------------
def fat_problems(dest: str, size: int = 0) -> list[str]:
    out: list[str] = []
    for seg in dest.split("/"):
        if _FAT_BAD_CHARS.search(seg):
            out.append(f"{dest}: '{seg}' has characters FAT32 can't store")
        elif seg != seg.rstrip(" ."):
            out.append(f"{dest}: '{seg}' ends with a space or dot")
        elif len(seg) > 255:
            out.append(f"{dest}: a name is longer than 255 characters")
    if size > FAT_MAX_FILE:
        out.append(f"{dest}: files of 4 GiB or more don't fit on FAT32")
    return out


def _match(pattern: str, path: str) -> bool:
    return fnmatch.fnmatchcase(path.casefold(), pattern.casefold())


def resolve_content_root(names: Iterable[str], spec: InstallSpec, title: str = "archive") -> str:
    """Directory prefix inside the source that corresponds to the USB root ('' for none)."""
    names = list(names)
    mode = spec.content_root
    if mode == "none":
        return ""
    if mode == "auto":
        firsts = {n.split("/", 1)[0] for n in names}
        if len(firsts) == 1 and names and all("/" in n for n in names):
            return firsts.pop()
        return ""
    if mode.startswith("contains:"):
        want = mode.split(":", 1)[1].casefold()
        prefixes: set[str] = set()
        for n in names:
            segs = n.split("/")
            for i, seg in enumerate(segs[:-1]):
                if seg.casefold() == want:
                    prefixes.add("/".join(segs[:i]))
                    break
        if not prefixes:
            raise AssembleError(f"{title} is missing a {mode.split(':', 1)[1]} folder." + LAYOUT_HINT)
        if spec.prefer_dir:
            pref = [p for p in prefixes if spec.prefer_dir.casefold() in p.rsplit("/", 1)[-1].casefold()]
            if pref:
                return sorted(pref)[0]
        return sorted(prefixes, key=lambda p: (p.count("/"), p))[0]
    raise AssembleError(f"Unknown content_root {mode!r} for {title}")


def _apply_rules(rel: str, rules: tuple[Rule, ...]) -> tuple[str, bool] | None:
    for rule in rules:
        if not _match(rule.src, rel):
            continue
        if rule.skip:
            return None
        out = posixpath.join(posixpath.dirname(rel), rule.rename_to) if rule.rename_to else rel
        if rule.dest:
            out = posixpath.join(rule.dest, out)
        return out, rule.overrides
    return None


def _layer_entries(art: Artifact, src_key: str, source: Source) -> list[PlanEntry]:
    members = {m: s for m, s in source.members()}
    if not members:
        raise AssembleError(f"{art.title} is empty.")
    prefix = resolve_content_root(members, art.install, art.title)
    out: list[PlanEntry] = []
    for member, size in sorted(members.items()):
        if prefix:
            if not member.startswith(prefix + "/"):
                continue
            rel = member[len(prefix) + 1:]
        else:
            rel = member
        placed = _apply_rules(rel, art.install.rules)
        if placed is None:
            continue
        dest, overrides = placed
        problems = fat_problems(dest, size)
        if problems:
            raise AssembleError("; ".join(problems))
        out.append(PlanEntry(art.key, src_key, member, dest, size, overrides))
    return out


def build_plan(
    opts: Options,
    sources: Mapping[str, Source],
    catalog: Mapping[str, Artifact] = CATALOG,
    entry_overrides: Mapping[str, str] | None = None,
) -> InstallPlan:
    """`entry_overrides` maps an imported extra's key to its discovered entry xex (relative to its folder)."""
    plan = InstallPlan()
    by_dest: dict[str, int] = {}
    layers = wanted(opts)
    for art in layers:
        art = catalog.get(art.key, art)
        if art.key not in sources:
            raise AssembleError(f"{art.title} hasn't been downloaded/imported yet.")
        for e in _layer_entries(art, art.key, sources[art.key]):
            k = e.dest.casefold()
            if k in by_dest:
                prev = plan.entries[by_dest[k]]
                if not e.overrides:
                    raise AssembleError(
                        f"{art.title} and {prev.layer} both want to write {e.dest}. This layout isn't one this app knows." + LAYOUT_HINT
                    )
                plan.entries[by_dest[k]] = e
            else:
                by_dest[k] = len(plan.entries)
                plan.entries.append(e)

    # auto-launch for FreeMyXe: copy the chosen tool's xex to BadUpdatePayload/after_patch.xex
    al = opts.autolaunch
    if al and opts.payload == "freemyxe":
        target = NAND_ENTRY if al.target == "nand" else _xexmenu_entry(entry_overrides)
        src = plan.find(target) if target else None
        if src is None:
            raise AssembleError("Couldn't find the tool to auto-launch on the stick.")
        plan.entries.append(PlanEntry("autolaunch", src.src_key, src.member, AFTER_PATCH, src.size))
        by_dest[AFTER_PATCH.casefold()] = len(plan.entries) - 1

    all_dests = [e.dest for e in plan.entries]
    for art in layers:
        for glob in art.install.must_provide:
            if not any(_match(glob, d) for d in all_dests):
                raise AssembleError(
                    f"{art.title} doesn't contain the expected file {glob}. Its zip layout has changed." + LAYOUT_HINT
                )
        for glob in art.install.expect:
            if not any(_match(glob, d) for d in all_dests):
                plan.warnings.append(f"{art.title}: expected {glob} wasn't in the download.")
    if not plan.find("BadUpdatePayload/default.xex"):
        raise AssembleError("The plan has no BadUpdatePayload/default.xex - pick XeUnshackle or FreeMyXe.")
    return plan


def _xexmenu_entry(entry_overrides: Mapping[str, str] | None) -> str | None:
    rel = (entry_overrides or {}).get("xexmenu")
    return f"Apps/XexMenu/{rel}" if rel else None
