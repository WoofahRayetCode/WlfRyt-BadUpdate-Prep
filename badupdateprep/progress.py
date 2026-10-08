"""Cancellation and progress primitives shared by the downloader, builder and USB copier."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal


class Cancelled(Exception):
    """Raised inside a worker when the user pressed Cancel."""


def _run_closers(closers: list[Callable[[], None]]) -> None:
    for fn in closers:
        try:
            fn()
        except Exception:
            pass


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()
        self._closers: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self._ev.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        """Flag cancellation and tear down bound resources WITHOUT blocking the caller.

        Closers run on a helper thread: closing an HTTP response waits for the reader's lock, which the download thread
        holds while blocked in recv(). Doing that on the UI thread froze the window until the socket timed out.
        """
        self._ev.set()
        with self._lock:
            closers, self._closers = self._closers, []
        if closers:
            threading.Thread(target=_run_closers, args=(closers,), name="cancel-closers", daemon=True).start()

    def bind(self, closer: Callable[[], None]) -> Callable[[], None]:
        """Register something to close on cancel (e.g. a live HTTP response). Returns an unbind callable."""
        with self._lock:
            self._closers.append(closer)
        if self._ev.is_set():
            try:
                closer()
            except Exception:
                pass

        def unbind() -> None:
            with self._lock:
                if closer in self._closers:
                    self._closers.remove(closer)

        return unbind


@dataclass(frozen=True)
class Progress:
    key: str
    phase: Literal["connect", "download", "verify"]
    done: int
    total: int | None
    bps: float = 0.0
    eta_s: float | None = None


class ThroughputMeter:
    """Moving-window bytes/second."""

    def __init__(self, window: float = 5.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._window = window
        self._clock = clock
        self._samples: deque[tuple[float, int]] = deque()

    def add(self, total_done: int) -> None:
        now = self._clock()
        self._samples.append((now, total_done))
        while len(self._samples) > 2 and now - self._samples[0][0] > self._window:
            self._samples.popleft()

    @property
    def bps(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        return (b1 - b0) / dt if dt > 0 else 0.0

    def eta(self, done: int, total: int | None) -> float | None:
        if not total or self.bps <= 0:
            return None
        return max(0.0, (total - done) / self.bps)


class WeightedTotal:
    """Byte-weighted overall progress across several items (so a 286 MB item dominates a 1 MB one)."""

    def __init__(self, sizes: dict[str, int]) -> None:
        self._sizes = {k: max(int(v), 1) for k, v in sizes.items()}
        self._done = {k: 0 for k in sizes}

    @property
    def total(self) -> int:
        return sum(self._sizes.values())

    def update(self, key: str, done: int) -> tuple[int, int]:
        if key in self._sizes:
            self._done[key] = max(0, min(int(done), self._sizes[key]))
        return self.snapshot()

    def mark_done(self, key: str) -> tuple[int, int]:
        if key in self._sizes:
            self._done[key] = self._sizes[key]
        return self.snapshot()

    def snapshot(self) -> tuple[int, int]:
        return sum(self._done.values()), self.total


def fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"
