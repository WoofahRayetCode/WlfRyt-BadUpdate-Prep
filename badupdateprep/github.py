"""Release metadata: which file do we download, from where, and what checksum do we expect?

Pinned channel  -> derived straight from the catalog `Pin`; makes no API call (no rate limit, works offline).
Latest channel  -> one `GET /releases` per repo, ETag-cached, prerelease-aware, strict asset matching.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .catalog import Artifact, Channel
from .downloader import USER_AGENT, DownloadError, default_opener

API_BASE = "https://api.github.com"
API_TIMEOUT = 20.0


class RateLimited(DownloadError):
    def __init__(self, message: str, reset_epoch: int | None = None):
        super().__init__(message, kind="rate_limit", status=403, retryable=False)
        self.reset_epoch = reset_epoch


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str | None  # from GitHub's `digest: "sha256:<hex>"`


@dataclass(frozen=True)
class Release:
    tag: str
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class Resolved:
    art: Artifact
    tag: str
    asset: str
    url: str
    size: int | None
    hashes: tuple[tuple[str, str], ...]
    provenance: str  # "pinned" | "github-digest" | "unverified"
    offline: bool = False


# ---------------------------------------------------------------------------------------------------
def parse_releases(payload: list[dict]) -> list[Release]:
    out: list[Release] = []
    for rel in payload:
        if rel.get("draft"):
            continue
        assets = []
        for a in rel.get("assets") or []:
            digest = a.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            assets.append(
                ReleaseAsset(
                    name=a.get("name") or "",
                    url=a.get("browser_download_url") or "",
                    size=int(a.get("size") or 0),
                    sha256=sha,
                )
            )
        out.append(Release(tag=rel.get("tag_name") or rel.get("name") or "?", prerelease=bool(rel.get("prerelease")), assets=tuple(assets)))
    return out


def pick_release(rels: list[Release], allow_prerelease: bool) -> Release:
    for r in rels:
        if allow_prerelease or not r.prerelease:
            return r
    raise DownloadError("No suitable GitHub release found.", kind="http")


def pick_asset(rel: Release, pattern: str) -> ReleaseAsset:
    rx = re.compile(pattern)
    hits = [a for a in rel.assets if rx.search(a.name)]
    names = [a.name for a in rel.assets]
    if not hits:
        raise DownloadError(
            f"Release {rel.tag} has no asset matching {pattern!r}. Assets: {names}. Use 'Tested versions'.", kind="layout"
        )
    if len(hits) > 1:
        raise DownloadError(
            f"Release {rel.tag} has several assets matching {pattern!r}: {[a.name for a in hits]}. Use 'Tested versions'.",
            kind="layout",
        )
    return hits[0]


def _rate_limit_error(exc: urllib.error.HTTPError) -> RateLimited:
    reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
    reset_epoch = int(reset) if reset and reset.isdigit() else None
    when = ""
    if reset_epoch:
        mins = max(1, int((reset_epoch - time.time()) / 60) + 1)
        when = f" It resets in about {mins} min."
    return RateLimited(
        "GitHub's anonymous API limit (60 requests/hour) is used up." + when
        + " Switch to 'Tested versions' (no API calls), or set a GITHUB_TOKEN environment variable.",
        reset_epoch,
    )


def _api_headers(token: str | None, etag: str | None) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if etag:
        h["If-None-Match"] = etag
    return h


def list_releases(
    owner: str,
    repo: str,
    *,
    cache_dir: Path | None = None,
    api_base: str = API_BASE,
    token: str | None = None,
    opener=None,
) -> list[Release]:
    """GET /releases?per_page=15 with ETag caching (a 304 does not count against the rate limit)."""
    opener = opener or default_opener
    cache_file = (cache_dir / f"{owner}_{repo}.json") if cache_dir else None
    cached: dict = {}
    if cache_file and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
        except (OSError, ValueError):
            cached = {}
    url = f"{api_base}/repos/{owner}/{repo}/releases?per_page=15"
    req = urllib.request.Request(url, headers=_api_headers(token, cached.get("etag")))
    try:
        resp = opener(req, API_TIMEOUT)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 304 and cached.get("body") is not None:
            return parse_releases(cached["body"])
        if exc.code in (403, 429) and (exc.headers.get("X-RateLimit-Remaining") == "0" or exc.code == 429):
            raise _rate_limit_error(exc) from exc
        raise DownloadError(f"HTTP {exc.code} for {url}", kind="http", status=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DownloadError(f"Network error for {url}: {getattr(exc, 'reason', exc)}", kind="network", retryable=True) from exc
    with resp:
        etag = resp.headers.get("ETag")
        body = json.loads(resp.read().decode("utf-8"))
    if cache_file and etag:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"etag": etag, "body": body}), "utf-8")
            tmp.replace(cache_file)
        except OSError:
            pass
    return parse_releases(body)


# ---------------------------------------------------------------------------------------------------
def pin_url(art: Artifact) -> str:
    pin = art.pin
    assert pin is not None
    return pin.url or f"https://github.com/{art.owner}/{art.repo}/releases/download/{pin.tag}/{pin.asset}"


def resolve_pinned(art: Artifact) -> Resolved:
    pin = art.pin
    if pin is None:
        raise DownloadError(f"{art.title} has no tested version pinned.", kind="layout")
    hashes = (("sha256", pin.sha256),) + pin.extra_hashes
    return Resolved(art, pin.tag, pin.asset, pin_url(art), pin.size, hashes, "pinned")


def resolve_latest(
    art: Artifact,
    *,
    meta_dir: Path | None = None,
    api_base: str = API_BASE,
    token: str | None = None,
    opener=None,
) -> Resolved:
    rels = list_releases(art.owner, art.repo, cache_dir=meta_dir, api_base=api_base, token=token, opener=opener)
    rel = pick_release(rels, art.allow_prerelease)
    asset = pick_asset(rel, art.asset_re)
    hashes = (("sha256", asset.sha256),) if asset.sha256 else ()
    return Resolved(art, rel.tag, asset.name, asset.url, asset.size or None, hashes, "github-digest" if asset.sha256 else "unverified")


def resolve(
    art: Artifact,
    channel: Channel,
    *,
    meta_dir: Path | None = None,
    api_base: str = API_BASE,
    token: str | None = None,
    opener=None,
) -> Resolved:
    """Decide exactly which file to fetch. Items hosted outside GitHub always use their pin."""
    if art.source == "manual":
        raise DownloadError(f"{art.title} is imported manually, not downloaded.", kind="layout")
    if art.source == "url" or channel == Channel.PINNED:
        return resolve_pinned(art)
    return resolve_latest(art, meta_dir=meta_dir, api_base=api_base, token=token, opener=opener)
