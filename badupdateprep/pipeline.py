"""Thread-agnostic orchestration: resolve -> download -> verify -> build staging -> copy to USB.

Nothing here touches Tk. Long operations take an `emit(event)` callback and a `CancelToken`; the UI runs them
on a worker thread and drains events from a queue (see ui/worker.py), so Tk is only ever called from its own thread.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Mapping

from . import paths
from .assemble import StagingInfo, build_staging, fingerprint, load_staging, staging_is_current
from .cache import DownloadCache
from .catalog import CATALOG, Artifact, Channel
from .downloader import DownloadError, FetchSpec, RetryPolicy, fetch
from .drives import Drive, assess, has_errors as drive_has_errors
from .errors import AssembleError
from .github import API_BASE, RateLimited, Resolved, resolve, resolve_pinned
from .importer import ImportedExtra, load_imported
from .launch_ini import IniDoc, IniEdit, unified_diff, usb_path
from .options import NAND_ENTRY, Options, downloadable, has_errors, validate, wanted
from .plan import InstallPlan, build_plan
from .progress import CancelToken, Cancelled, Progress, WeightedTotal
from .sources import DirSource, Source, ZipSource
from .usbcopy import CopyPlan, copy_to_usb, plan_copy, verify_usb


# --- events ----------------------------------------------------------------------------------------
ItemStatus = Literal["queued", "checking", "downloading", "verifying", "cached", "ready", "failed", "skipped"]


@dataclass(frozen=True)
class ItemUpdate:
    key: str
    status: ItemStatus
    done: int = 0
    total: int | None = None
    bps: float = 0.0
    eta_s: float | None = None
    version: str = ""
    provenance: str = ""
    note: str = ""


@dataclass(frozen=True)
class StageChange:
    stage: Literal["resolve", "download", "build", "copy", "verify"]
    note: str = ""


@dataclass(frozen=True)
class OverallProgress:
    done: int
    total: int
    eta_s: float | None = None


@dataclass(frozen=True)
class Log:
    text: str


@dataclass(frozen=True)
class Finished:
    result: object = None


@dataclass(frozen=True)
class Failed:
    error: BaseException
    key: str = ""


Emit = Callable[[object], None]


@dataclass
class Env:
    """Everything that varies between the real app and tests."""

    app_dir: Path = field(default_factory=paths.app_dir)
    channel: Channel = Channel.PINNED
    api_base: str = API_BASE
    token: str | None = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN") or None)
    opener: Callable | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep
    timeout: float = 20.0
    catalog: Mapping[str, Artifact] = field(default_factory=lambda: CATALOG)
    # test hook: replace the catalog's resolved URL (e.g. point pins at a local server)
    rewrite_url: Callable[[Resolved], Resolved] | None = None

    @property
    def staging_root(self) -> Path:
        return paths.staging_dir(self.app_dir)

    @property
    def cache(self) -> DownloadCache:
        return DownloadCache(paths.downloads_dir(self.app_dir))

    @property
    def meta_dir(self) -> Path:
        return paths.meta_dir(self.app_dir)


@dataclass(frozen=True)
class Acquired:
    art: Artifact
    resolved: Resolved
    path: Path
    cached: bool
    offline: bool = False


def _with_title(exc: BaseException, title: str) -> BaseException:
    """Prefix a failure with the item it belongs to ("XeUnshackle: HTTP 503...").

    Our own error types keep their identity (kind/status/retryable) and just get the prefix. Anything else (PermissionError,
    UnicodeDecodeError, ...) is wrapped: OSError.__str__ ignores `args`, so editing args would silently do nothing.
    """
    msg = str(exc)
    if msg.startswith(f"{title}: "):
        return exc
    if isinstance(exc, (DownloadError, AssembleError)) and exc.args:
        exc.args = (f"{title}: {msg}",) + tuple(exc.args[1:])
        return exc
    return DownloadError(f"{title}: {msg}", kind="disk" if isinstance(exc, OSError) else "network")


def _sha256_of(r: Resolved) -> str | None:
    for name, h in r.hashes:
        if name.lower() == "sha256":
            return h
    return None


def resolve_artifact(art: Artifact, env: Env, emit: Emit | None = None) -> Resolved:
    """Resolve what to fetch; on network trouble fall back to a previously verified cached copy."""
    emit = emit or (lambda e: None)
    cache = env.cache
    try:
        r = resolve(art, env.channel, meta_dir=env.meta_dir, api_base=env.api_base, token=env.token, opener=env.opener)
    except DownloadError as exc:
        hit = cache.latest_cached(art.key)
        if hit is None:
            raise
        if isinstance(exc, RateLimited):
            why = "GitHub's rate limit was hit"
        elif exc.kind == "layout":
            why = "the newest release has a layout this app doesn't recognise"
        else:
            why = "couldn't reach GitHub"
        emit(Log(f"{art.title}: {why}; using your cached {hit.tag} ({hit.asset}) instead."))
        r = Resolved(art, hit.tag, hit.asset, hit.url, hit.size, (("sha256", hit.sha256),), "cached", offline=True)
    if env.rewrite_url:
        r = env.rewrite_url(r)
    return r


def ensure_artifact(
    art: Artifact,
    env: Env,
    emit: Emit,
    cancel: CancelToken | None = None,
    *,
    resolved: Resolved | None = None,
    on_progress: Callable[[str, int], None] | None = None,
) -> Acquired:
    """Make sure a verified copy of `art` is in the cache and return it."""
    emit(ItemUpdate(art.key, "checking"))
    r = resolved or resolve_artifact(art, env, emit)
    cache = env.cache
    version = r.tag
    cache.adopt_legacy(r)
    state = cache.lookup(r)
    path = cache.path_for_resolved(r)
    if state == "hit":
        emit(ItemUpdate(art.key, "cached", r.size or 0, r.size, version=version, provenance=r.provenance))
        if on_progress:
            on_progress(art.key, r.size or 0)
        return Acquired(art, r, path, cached=True, offline=r.offline)
    if state == "corrupt":
        emit(Log(f"{art.title}: cached file failed verification - downloading again."))
        cache.discard(r)

    if r.offline:
        raise DownloadError(f"{art.title}: cached copy is unusable and the server can't be reached.", kind="network")

    def progress(p: Progress) -> None:
        status: ItemStatus = "verifying" if p.phase == "verify" else "downloading"
        emit(ItemUpdate(art.key, status, p.done, p.total or r.size, p.bps, p.eta_s, version, r.provenance))
        if on_progress and p.phase == "download":
            on_progress(art.key, p.done)

    spec = FetchSpec(art.key, r.url, path, r.size, r.hashes)
    fetch(spec, progress=progress, cancel=cancel, retry=env.retry, timeout=env.timeout, sleep=env.sleep, opener=env.opener)
    cache.record(r, path, _sha256_of(r))
    emit(ItemUpdate(art.key, "ready", r.size or 0, r.size, version=version, provenance=r.provenance))
    if on_progress:
        on_progress(art.key, r.size or 0)
    return Acquired(art, r, path, cached=False)


# ===================================================================================================
# Prepare: download everything, plan the stick, build staging
# ===================================================================================================
@dataclass
class PrepareResult:
    staging: StagingInfo
    plan: InstallPlan
    ini_edits: list[IniEdit]
    ini_diff: str
    fingerprint: str
    up_to_date: bool
    acquired: dict[str, Acquired]
    warnings: list[str] = field(default_factory=list)


SLOT_NAMES = {
    "BUT_A": "A", "BUT_B": "B", "BUT_X": "X", "BUT_Y": "Y",
    "Start": "Start", "Back": "Back", "LBump": "left bumper", "RThumb": "right stick click", "LThumb": "left stick click",
    "Default": "(automatic)", "Guide": "Guide", "Power": "Power",
}


def imported_extras(env: Env) -> dict[str, ImportedExtra]:
    out: dict[str, ImportedExtra] = {}
    for key, art in env.catalog.items():
        if art.source == "manual":
            got = load_imported(key, env.app_dir)
            if got:
                out[key] = got
    return out


def _target_rel(opts: Options, imported: Mapping[str, ImportedExtra]) -> str | None:
    al = opts.autolaunch
    if not al:
        return None
    if al.target == "nand":
        return NAND_ENTRY
    ex = imported.get("xexmenu")
    return f"Apps/XexMenu/{ex.entry_xex}" if ex else None


def compute_ini_edits(
    opts: Options, plan: InstallPlan, sources: Mapping[str, Source], imported: Mapping[str, ImportedExtra]
) -> tuple[list[IniEdit], bytes | None]:
    """launch.ini edits for XeUnshackle's hotkey launcher (and the original bytes, for a diff)."""
    if opts.payload != "xeunshackle" or not opts.autolaunch:
        return [], None
    entry = plan.find("launch.ini")
    target = _target_rel(opts, imported)
    if entry is None or target is None:
        raise AssembleError("Hotkey launching needs XeUnshackle's launch.ini and a tool to launch.")
    with sources[entry.src_key].open(entry.member) as f:
        original = f.read()
    prefix = IniDoc.parse(original).device_prefix()
    return [IniEdit("Paths", opts.autolaunch.slot, usb_path(prefix, target))], original


def hotkey_hint(opts: Options, imported: Mapping[str, ImportedExtra] | None = None) -> str:
    al = opts.autolaunch
    if not al:
        return ""
    tool = "the NAND flasher" if al.target == "nand" else "XexMenu"
    if opts.payload == "freemyxe":
        return f"FreeMyXe launches {tool} by itself right after it patches the console (it was staged as after_patch.xex)."
    slot = SLOT_NAMES.get(al.slot, al.slot)
    return (
        f"launch.ini maps the {slot} button to {tool}. After XeUnshackle patches the console, hold {slot} as the "
        f"dashboard loads. If nothing starts, read the comments next to {al.slot} in launch.ini."
    )


def _sha_for(a: Acquired) -> str:
    for name, h in a.resolved.hashes:
        if name.lower() == "sha256":
            return h
    from .downloader import file_digest

    return file_digest(a.path)


def run_prepare(
    opts: Options,
    env: Env,
    emit: Emit,
    cancel: CancelToken | None = None,
    *,
    force: bool = False,
) -> PrepareResult:
    imported = imported_extras(env)
    problems = validate(opts, frozenset(imported))
    if has_errors(problems):
        raise AssembleError(" ".join(p.text for p in problems if p.level == "error"))
    env.channel = opts.channel
    arts = [env.catalog.get(a.key, a) for a in wanted(opts)]
    dl = [a for a in arts if a.source != "manual"]

    emit(StageChange("resolve", "Checking versions"))
    resolved: dict[str, Resolved] = {}
    for a in dl:
        emit(ItemUpdate(a.key, "queued"))
    for a in dl:
        if cancel:
            cancel.check()
        try:
            resolved[a.key] = resolve_artifact(a, env, emit)
        except Cancelled:
            raise
        except Exception as exc:
            err = _with_title(exc, a.title)
            emit(ItemUpdate(a.key, "failed", note=str(err)))
            if err is exc:
                raise
            raise err from exc
        r = resolved[a.key]
        emit(ItemUpdate(a.key, "queued", 0, r.size, version=r.tag, provenance=r.provenance))
    for a in arts:
        if a.source == "manual":
            emit(ItemUpdate(a.key, "ready", version="imported", provenance="imported", note="Imported from your computer"))

    cache = env.cache
    # Small items first, the big trial last, so cheap failures surface before a long transfer.
    order = sorted(dl, key=lambda a: resolved[a.key].size or 0)
    need = sum((resolved[a.key].size or 0) for a in order if cache.lookup(resolved[a.key]) != "hit")
    env.app_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(env.app_dir).free
    if need and free < need + 64 * 1024 * 1024:
        raise DownloadError(
            f"Not enough free disk space in {env.app_dir}: need about {need // (1024 * 1024)} MB for downloads.", kind="disk"
        )

    weights = WeightedTotal({a.key: resolved[a.key].size or 1 for a in order})
    t0 = time.monotonic()

    def on_progress(key: str, done: int) -> None:
        d, t = weights.update(key, done)
        el = time.monotonic() - t0
        eta = ((t - d) / (d / el)) if d > 0 and el > 1 else None
        emit(OverallProgress(d, t, eta))

    emit(StageChange("download", "Downloading and verifying"))
    acquired: dict[str, Acquired] = {}
    for a in order:
        if cancel:
            cancel.check()
        try:
            acquired[a.key] = ensure_artifact(a, env, emit, cancel, resolved=resolved[a.key], on_progress=on_progress)
        except Cancelled:
            emit(ItemUpdate(a.key, "queued", note="cancelled"))
            raise
        except Exception as exc:
            err = _with_title(exc, a.title)
            emit(ItemUpdate(a.key, "failed", version=resolved[a.key].tag, note=str(err)))
            if err is exc:
                raise
            raise err from exc

    with contextlib.ExitStack() as stack:
        sources: dict[str, Source] = {}
        for a in arts:
            if a.source == "manual":
                sources[a.key] = DirSource(imported[a.key].root)
            else:
                sources[a.key] = stack.enter_context(contextlib.closing(ZipSource(acquired[a.key].path)))
        entry_overrides = {k: v.entry_xex for k, v in imported.items()}
        plan = build_plan(opts, sources, env.catalog, entry_overrides)
        edits, original_ini = compute_ini_edits(opts, plan, sources, imported)
        ini_diff = ""
        if edits and original_ini is not None:
            ini_diff = unified_diff(original_ini, IniDoc.parse(original_ini).apply(edits).to_bytes())
        source_sha = {a.key: _sha_for(acquired[a.key]) for a in dl}
        source_sha.update({k: v.source_sha256 for k, v in imported.items() if any(a.key == k for a in arts)})
        fp = fingerprint(plan, opts, source_sha, edits)

        existing = load_staging(env.staging_root)
        if not force and staging_is_current(existing, fp):
            emit(Log("Staging is already up to date."))
            res = PrepareResult(existing, plan, edits, ini_diff, fp, True, acquired, plan.warnings)  # type: ignore[arg-type]
            emit(Finished(res))
            return res

        need_stage = int(plan.total_bytes * 1.05) + 64 * 1024 * 1024
        free = shutil.disk_usage(env.app_dir).free
        if free < need_stage:
            raise DownloadError(f"Not enough free disk space to stage files: need about {need_stage // (1024 * 1024)} MB.", kind="disk")

        emit(StageChange("build", "Building the USB folder"))

        def build_progress(done: int, total: int, dest: str) -> None:
            emit(OverallProgress(done, total, None))

        info = build_staging(
            plan, sources, env.staging_root, fingerprint_value=fp, ini_edits=edits, progress=build_progress, cancel=cancel
        )
    cache.prune(keep=2)
    res = PrepareResult(info, plan, edits, ini_diff, fp, False, acquired, plan.warnings)
    emit(Finished(res))
    return res


def current_fingerprint(opts: Options, env: Env) -> str | None:
    """Fingerprint of what Prepare would build if everything needed is already cached, else None.

    Offline and cheap (no network): pinned items come from their pin, latest-channel items from the verified
    cache. The Prepare page uses it to say "up to date" without doing any work.
    """
    imported = imported_extras(env)
    if has_errors(validate(opts, frozenset(imported))):
        return None
    arts = [env.catalog.get(a.key, a) for a in wanted(opts)]
    cache = env.cache
    acquired: dict[str, Acquired] = {}
    for a in arts:
        if a.source == "manual":
            if a.key not in imported:
                return None
            continue
        if opts.channel == Channel.PINNED or a.source == "url":
            r = resolve_pinned(a)
        else:
            hit = cache.latest_cached(a.key)
            if not hit:
                return None
            r = Resolved(a, hit.tag, hit.asset, hit.url, hit.size, (("sha256", hit.sha256),), "cached", True)
        if env.rewrite_url:
            r = env.rewrite_url(r)
        if cache.lookup(r) != "hit":
            return None
        acquired[a.key] = Acquired(a, r, cache.path_for_resolved(r), True)
    with contextlib.ExitStack() as stack:
        sources: dict[str, Source] = {}
        for a in arts:
            sources[a.key] = (
                DirSource(imported[a.key].root)
                if a.source == "manual"
                else stack.enter_context(contextlib.closing(ZipSource(acquired[a.key].path)))
            )
        try:
            plan = build_plan(opts, sources, env.catalog, {k: v.entry_xex for k, v in imported.items()})
            edits, _ = compute_ini_edits(opts, plan, sources, imported)
        except AssembleError:
            return None
        sha = {a.key: _sha_for(acquired[a.key]) for a in arts if a.source != "manual"}
        sha.update({k: v.source_sha256 for k, v in imported.items() if any(a.key == k for a in arts)})
        return fingerprint(plan, opts, sha, edits)


# ===================================================================================================
# Copy to the stick
# ===================================================================================================
@dataclass
class CopyReport:
    usb: Path
    copied: int
    bytes: int
    replaced: int
    stale_removed: int
    verified: int
    bad: list[tuple[str, str]]
    written: dict[str, str]
    unchanged: int = 0  # files that were already on the stick, identical (hash-checked, not rewritten)
    left_alone: int = 0  # old files not removed because they changed since this app wrote them

    @property
    def ok(self) -> bool:
        return not self.bad


def preview_copy(st: StagingInfo, usb: Path) -> CopyPlan:
    return plan_copy(st, usb)


def run_copy(
    st: StagingInfo,
    usb: Path,
    env: Env,
    emit: Emit,
    cancel: CancelToken | None = None,
    *,
    drive: Drive | None = None,
    verify: bool = True,
) -> CopyReport:
    plan = plan_copy(st, usb)
    if drive is not None:
        findings = assess(drive, plan.space_needed, 0, protected=[env.app_dir, Path.home()])
        errs = [f.text for f in findings if f.level == "error"]
        if errs:
            raise AssembleError(" ".join(errs))
    emit(StageChange("copy", "Copying to the USB stick"))
    t0 = time.monotonic()

    def prog(done: int, total: int, rel: str) -> None:
        el = time.monotonic() - t0
        eta = ((total - done) / (done / el)) if done and el > 1 else None
        emit(OverallProgress(done, total, eta))

    result = copy_to_usb(plan, st, usb, progress=prog, cancel=cancel)
    bad: list[tuple[str, str]] = []
    verified = len(result.skipped)  # hash-checked against the stick while deciding to skip them
    if verify:
        emit(StageChange("verify", "Reading the files back from the stick"))

        def vprog(done: int, total: int, rel: str) -> None:
            emit(OverallProgress(done, total, None))

        rep = verify_usb(usb, result.written, progress=vprog, cancel=cancel)
        bad, verified = rep.bad, verified + rep.checked
    report = CopyReport(
        usb, result.count, plan.bytes_total, len(plan.replace), len(result.removed), verified, bad,
        {**result.skipped, **result.written}, len(result.skipped), len(result.left_alone),
    )
    emit(Finished(report))
    return report
