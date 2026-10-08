"""End-to-end, headless: run_prepare against a local server serving fixture zips with the real layouts."""
import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep import catalog
from badupdateprep.assemble import load_staging, staging_is_current
from badupdateprep.catalog import Channel, Pin
from badupdateprep.downloader import DownloadError, RetryPolicy
from badupdateprep.drives import Drive
from badupdateprep.importer import import_extra
from badupdateprep.options import AutoLaunch, Options
from badupdateprep.pipeline import (
    Env,
    Finished,
    ItemUpdate,
    OverallProgress,
    StageChange,
    current_fingerprint,
    hotkey_hint,
    run_copy,
    run_prepare,
)
from badupdateprep.progress import Cancelled, CancelToken
from tests import fixtures as fx
from tests.httpd import FaultyServer

BU = "BadUpdatePayload/"


def local_catalog(srv: FaultyServer, zips: dict[str, Path]):
    cat = dict(catalog.CATALOG)
    for key, zp in zips.items():
        art = catalog.CATALOG[key]
        data = zp.read_bytes()
        path = "/" + art.pin.asset
        srv.files[path] = data
        pin = Pin(art.pin.tag, art.pin.asset, len(data), hashlib.sha256(data).hexdigest(), url=srv.url(path))
        cat[key] = replace(art, pin=pin)
    return cat


class Base(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.td = Path(self._td.name)
        self.zips = fx.all_zips(self.td / "zips")
        self.srv = FaultyServer({}).__enter__()
        self.cat = local_catalog(self.srv, self.zips)

    def tearDown(self):
        self.srv.__exit__(None, None, None)
        self._td.cleanup()

    def env(self, **kw):
        return Env(app_dir=self.td / "app", catalog=self.cat, sleep=lambda s: None, retry=RetryPolicy(attempts=3, base=0), **kw)


class PrepareE2E(Base):
    def test_full_run_then_up_to_date_without_network(self):
        env, events = self.env(), []
        opts = Options(entry="rbb", payload="xeunshackle", rbb_trial=True)
        self.assertIsNone(current_fingerprint(opts, env), "nothing cached yet")
        res = run_prepare(opts, env, events.append)
        self.assertFalse(res.up_to_date)
        root = res.staging.path
        self.assertEqual((root / BU / "default.xex").read_bytes(), b"XEUNSHACKLE")
        self.assertTrue((root / "Content/0000000000000000/5841122D/000D0000/DD774F20C36263F2").is_file())
        stages = [e.stage for e in events if isinstance(e, StageChange)]
        self.assertEqual(stages, ["resolve", "download", "build"])
        self.assertTrue(any(isinstance(e, Finished) for e in events))
        self.assertTrue(any(isinstance(e, OverallProgress) for e in events))
        keys = {e.key for e in events if isinstance(e, ItemUpdate)}
        self.assertEqual(keys, {"badupdate", "rbb_trial", "xeunshackle"})

        n = len(self.srv.requests)
        self.assertEqual(current_fingerprint(opts, env), res.fingerprint)
        again = run_prepare(opts, env, lambda e: None)
        self.assertTrue(again.up_to_date)
        self.assertEqual(len(self.srv.requests), n, "an up-to-date rerun must not download anything")

    def test_smallest_first_trial_last(self):
        events = []
        run_prepare(Options(rbb_trial=True), self.env(), events.append)
        first_downloads = [e.key for e in events if isinstance(e, ItemUpdate) and e.status in ("downloading", "cached", "ready")]
        order = list(dict.fromkeys(first_downloads))
        self.assertEqual(order[-1], "badupdate" if len(self.zips["badupdate"].read_bytes()) > len(self.zips["rbb_trial"].read_bytes()) else "rbb_trial")
        sizes = [len(self.zips[k].read_bytes()) for k in order if k in self.zips]
        self.assertEqual(sizes, sorted(sizes))

    def test_changed_options_rebuild_and_drop_old_files(self):
        env = self.env()
        a = run_prepare(Options(payload="xeunshackle", rbb_trial=False), env, lambda e: None)
        self.assertTrue((a.staging.path / "launch.ini").exists())
        b = run_prepare(Options(payload="freemyxe", rbb_trial=False), env, lambda e: None)
        self.assertFalse(b.up_to_date)
        self.assertTrue((b.staging.path / BU / "xell-2f.bin").exists())
        self.assertFalse((b.staging.path / "launch.ini").exists())

    def test_interrupted_download_resumes(self):
        env = self.env()
        self.srv.files["/RBBlitz_Trial.zip"] = self.zips["rbb_trial"].read_bytes()
        self.srv.cut_after = 200
        res = run_prepare(Options(payload="stock", rbb_trial=True), env, lambda e: None)
        self.assertTrue((res.staging.path / "Content/0000000000000000/5841122D/000D0000/DD774F20C36263F2").is_file())
        self.assertTrue(any(r["headers"].get("range") for r in self.srv.requests))

    def test_corrupt_download_fails_with_checksum_and_leaves_no_cache(self):
        self.srv.corrupt = True
        events = []
        with self.assertRaises(Exception) as cm:
            run_prepare(Options(payload="stock", rbb_trial=False), self.env(), events.append)
        self.assertEqual(getattr(cm.exception, "kind", None), "checksum")
        failed = [e for e in events if isinstance(e, ItemUpdate) and e.status == "failed"]
        self.assertTrue(failed)
        titles = [a.title for a in catalog.CATALOG.values()]
        self.assertTrue(any(str(cm.exception).startswith(t) for t in titles), "failure must name the item: " + str(cm.exception))
        self.assertNotIn(".part", str(cm.exception))
        self.assertIsNone(load_staging(self.env().staging_root))

    def test_cancel_stops_cleanly(self):
        token = CancelToken()
        events = []

        def emit(e):
            events.append(e)
            if isinstance(e, ItemUpdate) and e.status == "downloading":
                token.cancel()

        with self.assertRaises(Cancelled):
            run_prepare(Options(payload="stock", rbb_trial=True), self.env(), emit, token)
        self.assertIsNone(load_staging(self.env().staging_root))

    def test_invalid_options_rejected_before_any_network(self):
        with self.assertRaises(Exception) as cm:
            run_prepare(Options(entry="avatar", payload="stock"), self.env(), lambda e: None)
        self.assertIn("default.xex", str(cm.exception))
        self.assertEqual(self.srv.requests, [])

    def test_launch_ini_helper_with_imported_xexmenu(self):
        env = self.env()
        src = fx.xexmenu_dir(self.td / "xm_src")
        imp = import_extra("xexmenu", src, app_dir=env.app_dir)
        self.assertEqual(imp.entry_xex, "default.xex")
        opts = Options(payload="xeunshackle", rbb_trial=False, xexmenu=True, autolaunch=AutoLaunch("BUT_X", "xexmenu"))
        res = run_prepare(opts, env, lambda e: None)
        ini = (res.staging.path / "launch.ini").read_text("latin-1")
        self.assertIn(r"BUT_X = Usb:\Apps\XexMenu\default.xex", ini)
        self.assertTrue((res.staging.path / "Apps/XexMenu/default.xex").is_file())
        self.assertIn("-BUT_X = Usb:\\Content", res.ini_diff)
        self.assertIn("+BUT_X = Usb:\\Apps\\XexMenu\\default.xex", res.ini_diff)
        self.assertEqual(res.staging.edited, ("launch.ini",), "copy step needs to know launch.ini was edited on purpose")
        from badupdateprep.assemble import load_staging

        self.assertEqual(load_staging(env.staging_root).edited, ("launch.ini",), "and it must survive reload from disk")
        self.assertIn("X", hotkey_hint(opts))

    def test_freemyxe_autolaunch_stages_after_patch(self):
        env = self.env()
        opts = Options(payload="freemyxe", rbb_trial=False, nand_tool=True, autolaunch=AutoLaunch("BUT_Y", "nand"))
        res = run_prepare(opts, env, lambda e: None)
        self.assertEqual((res.staging.path / BU / "after_patch.xex").read_bytes(), b"NANDFLASHER")

    def test_latest_channel_offline_uses_cache(self):
        env = self.env()
        run_prepare(Options(payload="stock", rbb_trial=False), env, lambda e: None)
        import urllib.error

        def dead(req, timeout):
            raise urllib.error.URLError("offline")

        off = self.env(opener=dead)
        logs = []
        res = run_prepare(Options(payload="stock", rbb_trial=False, channel=Channel.LATEST), off, logs.append)
        self.assertTrue(res.staging.path.exists())


class ErrorTitles(unittest.TestCase):
    def test_titles_are_added_to_every_kind_of_failure(self):
        # Reviewer finding: OSError.__str__ ignores args, so mutating args left PermissionError without the item name.
        from badupdateprep.errors import AssembleError
        from badupdateprep.pipeline import _with_title

        cases = [PermissionError(13, "Permission denied", "/x/y"), OSError("disk exploded"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
                 DownloadError("HTTP 503", kind="http", status=503), AssembleError("layout changed"), RuntimeError("boom")]
        for exc in cases:
            with self.subTest(type(exc).__name__):
                out = _with_title(exc, "XeUnshackle")
                self.assertTrue(str(out).startswith("XeUnshackle: "), str(out))
        d = DownloadError("HTTP 503", kind="http", status=503, retryable=True)
        out = _with_title(d, "XeUnshackle")
        self.assertIs(out, d, "our own errors keep their identity (kind/status/retryable)")
        self.assertEqual((out.kind, out.status, out.retryable), ("http", 503, True))
        self.assertEqual(str(_with_title(_with_title(d, "XeUnshackle"), "XeUnshackle")).count("XeUnshackle"), 1, "no double prefix")


class CopyE2E(Base):
    def test_prepare_copy_verify_roundtrip(self):
        env = self.env()
        res = run_prepare(Options(payload="freemyxe", rbb_trial=True), env, lambda e: None)
        usb = self.td / "usb"
        usb.mkdir()
        (usb / "BadUpdatePayload").mkdir()
        (usb / "BadUpdatePayload" / "MACBackup.bin").write_bytes(b"mac")
        events = []
        rep = run_copy(res.staging, usb, env, events.append)
        self.assertTrue(rep.ok)
        self.assertEqual(rep.verified, rep.copied)
        self.assertEqual((usb / BU / "default.xex").read_bytes(), b"FREEMYXE")
        self.assertEqual((usb / BU / "xell-2f.bin").read_bytes(), b"xell")
        self.assertEqual((usb / BU / "MACBackup.bin").read_bytes(), b"mac")
        self.assertEqual([e.stage for e in events if isinstance(e, StageChange)], ["copy", "verify"])

    def test_refuses_unsafe_drive(self):
        env = self.env()
        res = run_prepare(Options(payload="stock", rbb_trial=False), env, lambda e: None)
        usb = self.td / "usb"
        usb.mkdir()
        bad = Drive(str(usb), "NTFS stick", "NTFS", 16 * 1024**3, 8 * 1024**3, True)
        with self.assertRaises(Exception) as cm:
            run_copy(res.staging, usb, env, lambda e: None, drive=bad)
        self.assertIn("FAT32", str(cm.exception))
        self.assertEqual(list(usb.iterdir()), [])
        sysd = Drive(str(usb), "boot", "FAT32", 2 * 1024**3, 1 * 1024**3, False, system=True)
        with self.assertRaises(Exception):
            run_copy(res.staging, usb, env, lambda e: None, drive=sysd)



if __name__ == "__main__":
    unittest.main()
