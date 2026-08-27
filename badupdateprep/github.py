from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from .catalog import PICKERS, Artifact

USER_AGENT = "WlfRyt-BadUpdate-Prep (homebrew USB helper)"
TIMEOUT = 600


class DownloadError(RuntimeError):
    pass


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, context=_ctx(), timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"Network error for {url}: {exc.reason}") from exc


def latest_release(owner: str, repo: str) -> dict:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        return json.loads(http_get(url).decode("utf-8"))
    except DownloadError as exc:
        if "404" not in str(exc):
            raise
        listing = json.loads(http_get(f"https://api.github.com/repos/{owner}/{repo}/releases").decode("utf-8"))
        if not listing:
            raise DownloadError(f"No GitHub releases for {owner}/{repo}")
        return listing[0]


def resolve_artifact(art: Artifact) -> tuple[str, str]:
    if art.source == "url":
        if not art.url:
            raise DownloadError(f"No URL for {art.title}")
        return art.url, "direct"
    rel = latest_release(art.owner, art.repo)
    tag = rel.get("tag_name") or rel.get("name") or "latest"
    picker = PICKERS[art.pick]
    url = picker(rel.get("assets") or [])
    if not url:
        names = [a.get("name") for a in rel.get("assets") or []]
        raise DownloadError(f"No zip asset for {art.title} ({tag}). Assets: {names}")
    return url, tag


def filename_from_url(url: str, fallback: str) -> str:
    name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return name or fallback


def download_to(url: str, dest: Path, progress=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, context=_ctx(), timeout=TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code} downloading {url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"Network error downloading {url}: {exc.reason}") from exc
    return dest
