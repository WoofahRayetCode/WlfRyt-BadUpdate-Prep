"""A tiny threaded HTTP server for downloader tests: real Range support plus fault injection."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FaultyServer:
    """Serves `files` ({path: bytes}). Fault knobs are plain attributes you can flip mid-test.

    cut_after:       close the connection after sending this many body bytes (once, then cleared)
    ignore_range:    always reply 200 with the whole body
    fail_first:      reply 503 (with Retry-After: 0) this many times before succeeding
    corrupt:         flip the first body byte
    redirect_to:     map {path: other_path} answered with 302
    etag:            send an ETag header (changing it between requests simulates a changed file)
    chunk_delay:     seconds to sleep after every 64 KB written (simulates a slow link, for cancel/progress tests)
    """

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.cut_after: int | None = None
        self.ignore_range = False
        self.fail_first = 0
        self.corrupt = False
        self.redirect_to: dict[str, str] = {}
        self.etag = '"v1"'
        self.always_status: int | None = None
        self.chunk_delay = 0.0
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def do_GET(self):  # noqa: N802
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                if self.path in outer.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.always_status:
                    self.send_response(outer.always_status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.fail_first > 0:
                    outer.fail_first -= 1
                    self.send_response(503)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = outer.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.corrupt and body:
                    body = bytes([body[0] ^ 0xFF]) + body[1:]
                start = 0
                status = 200
                rng = self.headers.get("Range")
                if rng and not outer.ignore_range and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0])
                    if start >= len(body):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(body)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
                chunk = body[start:]
                self.send_response(status)
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                if outer.etag:
                    self.send_header("ETag", outer.etag)
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                cut, outer.cut_after = outer.cut_after, None
                if cut is not None:
                    self.wfile.write(chunk[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.close()
                    return
                if outer.chunk_delay:
                    import time

                    try:
                        for i in range(0, len(chunk), 65536):
                            self.wfile.write(chunk[i:i + 65536])
                            self.wfile.flush()
                            time.sleep(outer.chunk_delay)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                self.wfile.write(chunk)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=lambda: self._httpd.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self) -> "FaultyServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    @property
    def base(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return self.base + path
