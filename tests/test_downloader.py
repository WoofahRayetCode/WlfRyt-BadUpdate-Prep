import hashlib
import os
import ssl
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep.downloader import DownloadError, FetchSpec, RetryPolicy, fetch, verify_file
from badupdateprep.progress import Cancelled, CancelToken, Progress
from tests.httpd import FaultyServer

DATA = os.urandom(900_000)
SHA = hashlib.sha256(DATA).hexdigest()
NOSLEEP = dict(sleep=lambda s: None)


def spec(srv, td, **kw):
    kw.setdefault("size", len(DATA))
    kw.setdefault("hashes", (("sha256", SHA),))
    return FetchSpec(key="t", url=srv.url("/f.zip"), dest=Path(td) / "f.zip", **kw)


class Fetch(unittest.TestCase):
    def test_happy_path_is_atomic(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            seen_dest: list[bool] = []
            s = spec(srv, td)

            def prog(p: Progress):
                seen_dest.append(s.dest.exists())

            fetch(s, progress=prog, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)
            self.assertFalse(any(seen_dest), "dest must not exist until the download is verified")
            self.assertFalse(s.dest.with_name("f.zip.part").exists())

    def test_cut_then_resume_uses_range(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.cut_after = 300_000
            s = spec(srv, td)
            fetch(s, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)
            ranges = [r["headers"].get("range") for r in srv.requests]
            self.assertIsNone(ranges[0])
            self.assertEqual(len(ranges), 2)
            self.assertTrue(ranges[1].startswith("bytes="), ranges)
            self.assertGreater(int(ranges[1][6:-1]), 0)

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.cut_after = 200_000
            srv.ignore_range = True
            s = spec(srv, td)
            fetch(s, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)

    def test_retry_after_503(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.fail_first = 2
            s = spec(srv, td)
            fetch(s, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)
            self.assertEqual(len(srv.requests), 3)

    def test_404_is_not_retried(self):
        with FaultyServer({}) as srv, TemporaryDirectory() as td:
            s = spec(srv, td)
            with self.assertRaises(DownloadError) as cm:
                fetch(s, **NOSLEEP)
            self.assertEqual(cm.exception.status, 404)
            self.assertEqual(len(srv.requests), 1)

    def test_checksum_mismatch_never_leaves_dest(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.corrupt = True
            s = spec(srv, td)
            with self.assertRaises(DownloadError) as cm:
                fetch(s, **NOSLEEP)
            self.assertEqual(cm.exception.kind, "checksum")
            self.assertFalse(s.dest.exists())
            self.assertFalse(s.dest.with_name("f.zip.part").exists())
            self.assertEqual(len(srv.requests), 2, "exactly one clean re-download is attempted")

    def test_checksum_error_is_readable_and_hides_the_temp_name(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.corrupt = True
            with self.assertRaises(DownloadError) as cm:
                fetch(spec(srv, td), **NOSLEEP)
            msg = str(cm.exception)
            self.assertNotIn(".part", msg)
            self.assertIn("f.zip", msg)
            self.assertIn("corrupt", msg)

    def test_wrong_size_from_server_is_rejected(self):
        with FaultyServer({"/f.zip": DATA[:1000]}) as srv, TemporaryDirectory() as td:
            s = spec(srv, td)
            with self.assertRaises(DownloadError) as cm:
                fetch(s, **NOSLEEP)
            self.assertEqual(cm.exception.kind, "checksum")
            self.assertFalse(s.dest.exists())

    def test_416_when_part_is_already_complete(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            s = spec(srv, td)
            part = s.dest.with_name("f.zip.part")
            part.write_bytes(DATA)
            fetch(s, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)

    def test_garbage_part_is_replaced(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            s = spec(srv, td)
            s.dest.with_name("f.zip.part").write_bytes(b"x" * 10)
            fetch(s, **NOSLEEP)  # resume sends Range from byte 10, server appends the tail -> hash fails once -> clean retry
            self.assertEqual(s.dest.read_bytes(), DATA)

    def test_redirect_is_followed(self):
        with FaultyServer({"/real.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.redirect_to["/f.zip"] = "/real.zip"
            s = spec(srv, td)
            fetch(s, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)

    def test_never_asks_for_compression(self):
        # Range byte math is only valid on identity-encoded bodies (urllib sends "identity" by default).
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            fetch(spec(srv, td), **NOSLEEP)
            self.assertIn(srv.requests[0]["headers"].get("accept-encoding", "identity"), ("identity", ""))

    def test_cancel_keeps_part_and_resumes_later(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            s = spec(srv, td)
            token = CancelToken()

            def prog(p: Progress):
                if p.phase == "download" and p.done > 100_000:
                    token.cancel()

            with self.assertRaises(Cancelled):
                fetch(s, progress=prog, cancel=token, **NOSLEEP)
            self.assertFalse(s.dest.exists())
            part = s.dest.with_name("f.zip.part")
            self.assertTrue(part.exists())
            fetch(s, **NOSLEEP)
            self.assertEqual(s.dest.read_bytes(), DATA)
            self.assertTrue(any(r["headers"].get("range") for r in srv.requests))

    def test_cancel_interrupts_a_stalled_connection_quickly(self):
        # Reviewer finding: cancel() ran resp.close() on the caller (UI) thread, which blocks on the reader's lock until the
        # stalled read times out (20 s in the app). Both the cancel call and the unwinding of the download must be near-instant.
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.stall_after = 1000
            token = CancelToken()
            out: dict = {}

            def run():
                try:
                    fetch(spec(srv, td), cancel=token, timeout=20, **NOSLEEP)
                except BaseException as exc:  # noqa: BLE001
                    out["exc"] = exc
                out["done"] = time.monotonic()

            t = threading.Thread(target=run)
            t.start()
            self.assertTrue(srv.stalled.wait(5), "server should be mid-body")
            time.sleep(0.2)
            t0 = time.monotonic()
            token.cancel()
            cancel_took = time.monotonic() - t0
            t.join(8)
            self.assertFalse(t.is_alive(), "download thread must unwind promptly, not wait for the 20 s socket timeout")
            self.assertLess(cancel_took, 0.5, "cancel() must not block the caller")
            self.assertLess(out["done"] - t0, 3.0)
            self.assertIsInstance(out["exc"], Cancelled)

    def test_certificate_failure_is_not_retried_and_explains_itself(self):
        # Reviewer finding: SSLCertVerificationError was treated as retryable, so a Mac without root certificates burned ~15 s
        # of backoff before failing with a cryptic message.
        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate"))

        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            with self.assertRaises(DownloadError) as cm:
                fetch(spec(srv, td), opener=opener, retry=RetryPolicy(attempts=5, base=0), sleep=lambda s: None)
        self.assertEqual(len(calls), 1, "retrying can never fix a certificate problem")
        self.assertFalse(cm.exception.retryable)
        self.assertIn("certificate", str(cm.exception).lower())

    def test_gives_up_after_attempts(self):
        with FaultyServer({"/f.zip": DATA}) as srv, TemporaryDirectory() as td:
            srv.always_status = 503
            s = spec(srv, td)
            with self.assertRaises(DownloadError):
                fetch(s, retry=RetryPolicy(attempts=3, base=0.0), **NOSLEEP)
            self.assertEqual(len(srv.requests), 3)

    def test_verify_file_reports_which_hash_failed(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "x.bin"
            p.write_bytes(b"hello")
            verify_file(p, 5, (("sha256", hashlib.sha256(b"hello").hexdigest()), ("md5", hashlib.md5(b"hello").hexdigest())))
            with self.assertRaises(DownloadError) as cm:
                verify_file(p, 5, (("md5", "0" * 32),))
            self.assertIn("MD5", str(cm.exception))
            with self.assertRaises(DownloadError):
                verify_file(p, 6)


if __name__ == "__main__":
    unittest.main()
