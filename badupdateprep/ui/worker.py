"""Run long operations off the UI thread without ever touching Tk from that thread.

The worker thread puts events on a queue.Queue; the UI thread drains it from an `after()` timer. (Calling
`widget.after()` from a worker thread is unsafe on non-threaded Tcl builds, and deferred lambdas that capture an
`except ... as exc` name raise NameError - the old downloader's silent failure. Neither can happen here.)
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable

from ..pipeline import Failed
from ..progress import CancelToken

Emit = Callable[[object], None]


@dataclass(frozen=True)
class Done:
    result: object = None


class Worker:
    def __init__(self, schedule: Callable[[int, Callable[[], None]], object], on_event: Callable[[object], None], poll_ms: int = 40):
        self._schedule = schedule
        self._on_event = on_event
        self._poll_ms = poll_ms
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._cancel: CancelToken | None = None
        self._polling = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def cancel_token(self) -> CancelToken | None:
        return self._cancel

    def start(self, fn: Callable[[Emit, CancelToken], object], name: str = "worker") -> None:
        if self.running:
            raise RuntimeError("a task is already running")
        token = CancelToken()
        self._cancel = token

        def run() -> None:
            try:
                result = fn(self._q.put, token)
            except BaseException as exc:  # noqa: BLE001 - everything must reach the UI
                self._q.put(Failed(exc))
            else:
                self._q.put(Done(result))

        self._thread = threading.Thread(target=run, name=name, daemon=True)
        self._thread.start()
        if not self._polling:
            self._polling = True
            self._schedule(self._poll_ms, self._poll)

    def cancel(self) -> None:
        if self._cancel:
            self._cancel.cancel()

    def drain(self) -> None:
        """Deliver everything queued so far (also used directly by tests)."""
        while True:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                return
            self._on_event(ev)

    def _poll(self) -> None:
        self.drain()
        if self.running or not self._q.empty():
            self._schedule(self._poll_ms, self._poll)
        else:
            self.drain()
            self._polling = False

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)
