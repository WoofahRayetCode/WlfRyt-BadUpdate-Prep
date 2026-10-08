"""Resumable, verified, atomic file downloads (stdlib only).

Guarantees:
  * the final path never exists unless the file is complete and matches size + hashes;
  * an interrupted transfer leaves a `.part` file that the next attempt resumes with an HTTP Range request;
  * cancel closes the live connection immediately and keeps the `.part` for resuming later.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import re
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .progress import CancelToken, Progress, ThroughputMeter

USER_AGENT = "WlfRyt-BadUpdate-Prep (homebrew USB helper)"
CHUNK = 256 * 1024
DISK_MARGIN = 64 * 1024 * 1024

ErrorKind = Literal["network", "http", "rate_limit", "checksum", "disk", "layout"]


class DownloadError(RuntimeError):
    def __init__(self, message: str, *, kind: ErrorKind = "network", status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class FetchSpec:
    key: str
    url: str
    dest: Path
    size: int | None = None
    hashes: tuple[tuple[str, str], ...] = ()  # (("sha256", "..."), ("md5", "..."))


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 5
    base: float = 1.0
    cap: float = 30.0


ProgressFn = Callable[[Progress], None]


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


class _StripCredentialsOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies every header (including Authorization) onto the redirected request; never hand credentials to another host.
    Range / If-Range are deliberately kept so resuming works across GitHub's redirect to its CDN."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc.lower() != urllib.parse.urlsplit(req.full_url).netloc.lower():
            for name in list(new.headers):
                if name.lower() in ("authorization", "cookie"):
                    del new.headers[name]
            for name in list(new.unredirected_hdrs):
                if name.lower() in ("authorization", "cookie"):
                    del new.unredirected_hdrs[name]
        return new


def default_opener(req: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()), _StripCredentialsOnCrossHostRedirect()
    )
    return opener.open(req, timeout=timeout)


def _hasher(name: str):
    try:
        return hashlib.new(name)
    except ValueError as exc:  # pragma: no cover - unknown algorithm in a pin is a programming error
        raise DownloadError(f"Unsupported hash algorithm {name!r}", kind="checksum") from exc


def _shown_name(path: Path) -> str:
    return path.name[: -len(".part")] if path.name.endswith(".part") else path.name


def verify_file(
    path: Path,
    size: int | None,
    hashes: tuple[tuple[str, str], ...] = (),
    *,
    key: str = "",
    progress: ProgressFn | None = None,
    cancel: CancelToken | None = None,
) -> None:
    """Raise DownloadError(kind="checksum") unless `path` has the expected size and every hash."""
    actual = path.stat().st_size
    if size is not None and actual != size:
        raise DownloadError(
            f"The file {_shown_name(path)} is the wrong size (expected {size} bytes, got {actual}).", kind="checksum"
        )
    if not hashes:
        return
    hs = {name.lower(): _hasher(name.lower()) for name, _ in hashes}
    done = 0
    last = 0.0
    with path.open("rb") as f:
        while True:
            if cancel:
                cancel.check()
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            for h in hs.values():
                h.update(chunk)
            done += len(chunk)
            now = time.monotonic()
            if progress and now - last > 0.1:
                last = now
                progress(Progress(key, "verify", done, actual))
    for name, want in hashes:
        got = hs[name.lower()].hexdigest()
        if got.lower() != want.lower():
            raise DownloadError(
                f"The download of {_shown_name(path)} is corrupt: its {name.upper()} doesn't match "
                f"(expected {want[:10]}..., got {got[:10]}...). Try again; if it keeps happening, your connection or the host may be having trouble.",
                kind="checksum",
            )


def file_digest(path: Path, algo: str = "sha256") -> str:
    h = _hasher(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------------
_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)")


def _retry_after(headers) -> float | None:
    val = headers.get("Retry-After") if headers else None
    if not val:
        return None
    try:
        return max(0.0, float(val))
    except ValueError:
        return None


def _classify_http(exc: urllib.error.HTTPError, url: str) -> DownloadError:
    code = exc.code
    retryable = code in (408, 429, 500, 502, 503, 504)
    kind: ErrorKind = "http"
    if code == 429 or (code == 403 and exc.headers and exc.headers.get("X-RateLimit-Remaining") == "0"):
        kind = "rate_limit"
        retryable = code == 429
    err = DownloadError(f"HTTP {code} for {url}", kind=kind, status=code, retryable=retryable)
    err.retry_after = _retry_after(exc.headers)  # type: ignore[attr-defined]
    return err


def _read_some(resp) -> bytes:
    """read1 returns whatever has arrived (at most one underlying recv), so a trickling server never makes us wait for a full chunk."""
    read1 = getattr(resp, "read1", None)
    return read1(CHUNK) if read1 else resp.read(CHUNK)


CERT_HELP = (
    "Couldn't verify the server's security certificate, so the download was refused. Check your computer's date and time; "
    "on macOS with python.org Python run 'Install Certificates.command'; and if you're on a work or school network, a proxy may "
    "be intercepting HTTPS."
)


def _is_cert_problem(exc: BaseException) -> bool:
    return isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError)


def _hard_close(resp) -> None:
    """Close a response even while another thread is blocked reading it.

    shutdown() on the underlying socket wakes the blocked recv() immediately; a plain close() would wait for the
    reader's lock (i.e. for the socket timeout). Falls back gracefully for objects that aren't real http responses.
    """
    try:
        sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        resp.close()
    except Exception:
        pass


def _sidecar(part: Path) -> Path:
    return part.with_name(part.name + ".json")


def _read_sidecar(part: Path) -> dict:
    try:
        return json.loads(_sidecar(part).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_sidecar(part: Path, data: dict) -> None:
    try:
        _sidecar(part).write_text(json.dumps(data), "utf-8")
    except OSError:
        pass


def _drop_part(part: Path) -> None:
    for p in (part, _sidecar(part)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _replace_with_retry(src: Path, dst: Path) -> None:
    # Antivirus / indexers on Windows often hold a freshly written file for a moment.
    for i in range(8):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == 7:
                raise
            time.sleep(0.25 * (i + 1))


def _attempt(
    spec: FetchSpec,
    part: Path,
    *,
    timeout: float,
    opener,
    progress: ProgressFn | None,
    cancel: CancelToken | None,
    meter: ThroughputMeter,
) -> None:
    if cancel:
        cancel.check()
    offset = part.stat().st_size if part.exists() else 0
    side = _read_sidecar(part) if offset else {}
    if offset and spec.size is not None and offset > spec.size:
        _drop_part(part)
        offset, side = 0, {}

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
        validator = side.get("etag") or side.get("last_modified")
        if validator:
            headers["If-Range"] = validator
    req = urllib.request.Request(spec.url, headers=headers)

    if progress:
        progress(Progress(spec.key, "connect", offset, spec.size))
    try:
        resp = opener(req, timeout)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 416:
            # Range not satisfiable: either we already have everything, or the part is junk.
            if spec.size is not None and offset == spec.size:
                return
            _drop_part(part)
            raise DownloadError("Server rejected resume range; restarting", kind="http", status=416, retryable=True) from exc
        raise _classify_http(exc, spec.url) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException) as exc:
        if _is_cert_problem(exc):
            raise DownloadError(CERT_HELP, kind="network", retryable=False) from exc  # retrying can never fix this
        reason = getattr(exc, "reason", exc)
        raise DownloadError(f"Network error for {spec.url}: {reason}", kind="network", retryable=True) from exc

    unbind = cancel.bind(lambda: _hard_close(resp)) if cancel else (lambda: None)
    try:
        status = getattr(resp, "status", None) or resp.getcode()
        rh = resp.headers
        total: int | None
        if status == 206:
            m = _CONTENT_RANGE.match(rh.get("Content-Range") or "")
            if not m or int(m.group(1)) != offset:
                _drop_part(part)
                raise DownloadError("Server sent an unexpected byte range; restarting", kind="http", retryable=True)
            total = int(m.group(3)) if m.group(3) != "*" else spec.size
            etag = rh.get("ETag")
            if side.get("etag") and etag and side["etag"] != etag:
                _drop_part(part)
                raise DownloadError("File changed on the server; restarting", kind="http", retryable=True)
            mode = "ab"
            got = offset
        else:
            # 200: the server ignored Range (or we asked for the whole thing). Never append to a stale part.
            cl = rh.get("Content-Length")
            total = int(cl) if cl and cl.isdigit() else spec.size
            mode = "wb"
            got = 0
        if spec.size is not None and total is not None and total != spec.size:
            raise DownloadError(
                f"Server reports {total} bytes but {spec.size} were expected for {spec.key}", kind="checksum"
            )
        _write_sidecar(part, {"url": spec.url, "etag": rh.get("ETag"), "last_modified": rh.get("Last-Modified")})
        free = shutil.disk_usage(part.parent).free
        remaining = (total - got) if total else 0
        if remaining and free < remaining + DISK_MARGIN:
            raise DownloadError(
                f"Not enough free disk space in {part.parent} (need ~{(remaining + DISK_MARGIN) // (1024 * 1024)} MB)",
                kind="disk",
            )

        last_emit = 0.0
        try:
            with part.open(mode) as f:
                while True:
                    if cancel:
                        cancel.check()
                    try:
                        chunk = _read_some(resp)
                    except Exception as exc:  # noqa: BLE001
                        if cancel and cancel.cancelled:
                            # cancel() tore the connection down under us; http.client then fails in assorted ways
                            # (AttributeError on its closed file, ValueError, OSError...). All of them mean "cancelled".
                            cancel.check()
                        if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException, OSError)):
                            raise DownloadError(f"Connection lost: {exc}", kind="network", retryable=True) from exc
                        raise
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    meter.add(got)
                    now = time.monotonic()
                    if progress and now - last_emit > 0.1:
                        last_emit = now
                        progress(Progress(spec.key, "download", got, total, meter.bps, meter.eta(got, total)))
        except DownloadError:
            raise
        except OSError as exc:
            if cancel and cancel.cancelled:
                cancel.check()
            raise DownloadError(f"Disk write failed: {exc}", kind="disk") from exc
        if cancel:
            cancel.check()  # a cancel() that shut the socket down looks like a clean EOF here
        if total is not None and got < total:
            raise DownloadError(f"Connection closed early ({got}/{total} bytes)", kind="network", retryable=True)
        if progress:
            progress(Progress(spec.key, "download", got, total, meter.bps, 0.0))
    finally:
        unbind()
        try:
            resp.close()
        except Exception:
            pass


def fetch(
    spec: FetchSpec,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelToken | None = None,
    retry: RetryPolicy = RetryPolicy(),
    timeout: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
    opener=None,
) -> Path:
    """Download `spec.url` to `spec.dest` (resuming, retrying, verifying). Returns `spec.dest`."""
    opener = opener or default_opener
    dest = spec.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    meter = ThroughputMeter()
    attempt = 0
    checksum_failures = 0
    while True:
        attempt += 1
        try:
            _attempt(spec, part, timeout=timeout, opener=opener, progress=progress, cancel=cancel, meter=meter)
            if not part.exists():
                raise DownloadError("Download produced no data", kind="network", retryable=True)
            try:
                verify_file(part, spec.size, spec.hashes, key=spec.key, progress=progress, cancel=cancel)
            except DownloadError as exc:
                _drop_part(part)
                checksum_failures += 1
                if checksum_failures >= 2:
                    raise
                exc.retryable = True  # one clean re-download, then give up
                raise
            _replace_with_retry(part, dest)
            try:
                _sidecar(part).unlink()
            except OSError:
                pass
            return dest
        except DownloadError as exc:
            if not exc.retryable or attempt >= retry.attempts:
                raise
            delay = getattr(exc, "retry_after", None)
            if delay is None:
                delay = min(retry.cap, retry.base * (2 ** (attempt - 1))) * random.uniform(0.5, 1.0)
            delay = min(delay, retry.cap)
            # Sleep in small slices so Cancel stays responsive (counted, not timed, so tests can inject sleep).
            remaining = delay
            while remaining > 0:
                if cancel:
                    cancel.check()
                step = min(0.2, remaining)
                sleep(step)
                remaining -= step
