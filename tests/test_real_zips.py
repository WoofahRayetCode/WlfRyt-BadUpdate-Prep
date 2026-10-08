"""Opt-in smoke test against the REAL upstream archives.

    WLFRYT_REAL_ZIPS=/path/to/dir python -m unittest tests.test_real_zips -v

The directory must contain the pinned files under their release asset names (e.g.
`Xbox360BadUpdate-Retail-USB-v1.3.zip`). Files that are missing are skipped, so you can test a subset.
This checks the sha256 pins in catalog.py and that the install specs still match what upstream really ships.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep.assemble import build_staging, fingerprint
from badupdateprep.catalog import CATALOG
from badupdateprep.downloader import verify_file
from badupdateprep.options import Options
from badupdateprep.plan import build_plan
from badupdateprep.sources import ZipSource

REAL = os.environ.get("WLFRYT_REAL_ZIPS")


@unittest.skipUnless(REAL and Path(REAL).is_dir(), "set WLFRYT_REAL_ZIPS to a folder of the pinned release files")
class RealZips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(REAL)
        cls.paths = {}
        for key, art in CATALOG.items():
            if art.pin and (cls.dir / art.pin.asset).is_file():
                cls.paths[key] = cls.dir / art.pin.asset

    def test_pins_match_the_files(self):
        if not self.paths:
            self.skipTest("no pinned files found in WLFRYT_REAL_ZIPS")
        for key, p in self.paths.items():
            pin = CATALOG[key].pin
            hashes = (("sha256", pin.sha256),) + pin.extra_hashes
            verify_file(p, pin.size, hashes, key=key)

    def _sources(self, keys):
        missing = [k for k in keys if k not in self.paths]
        if missing:
            self.skipTest(f"missing pinned files: {missing}")
        return {k: ZipSource(self.paths[k]) for k in keys}

    def _plan(self, opts, keys):
        srcs = self._sources(keys)
        try:
            return build_plan(opts, srcs), srcs
        except BaseException:
            for s in srcs.values():
                s.close()
            raise

    def test_rbb_xeunshackle(self):
        plan, srcs = self._plan(Options(entry="rbb", payload="xeunshackle", rbb_trial=False), ["badupdate", "xeunshackle"])
        dests = plan.dests()
        for need in ("BadUpdatePayload/default.xex", "BadUpdatePayload/BadStorage.xex.dll", "launch.ini", "Xbdm.xex", "JRPC2.xex"):
            self.assertIn(need, dests)
        self.assertEqual(plan.find("BadUpdatePayload/default.xex").layer, "xeunshackle")
        self.assertFalse(any(d.lower().startswith("readme") for d in dests))
        for s in srcs.values():
            s.close()

    def test_rbb_freemyxe_is_all_inside_badupdatepayload(self):
        plan, srcs = self._plan(Options(entry="rbb", payload="freemyxe", rbb_trial=False), ["badupdate", "freemyxe"])
        top_level_files = [d for d in plan.dests() if "/" not in d]
        self.assertEqual(top_level_files, [], f"nothing from FreeMyXe may land in the USB root: {top_level_files}")
        for need in ("default.xex", "FreeMyXe.ini", "xell-2f.bin", "BadStorage.dll"):
            self.assertIn(f"BadUpdatePayload/{need}", plan.dests())
        self.assertEqual(plan.warnings, [])
        for s in srcs.values():
            s.close()

    def test_avatar_xeunshackle(self):
        plan, srcs = self._plan(Options(entry="avatar", payload="xeunshackle"), ["abadavatar", "xeunshackle"])
        self.assertIsNotNone(plan.find("BadUpdatePayload/default.xex"))
        for s in srcs.values():
            s.close()

    def test_trial_and_nand_full_build(self):
        keys = ["badupdate", "rbb_trial", "xeunshackle", "nand_ro"]
        plan, srcs = self._plan(Options(entry="rbb", payload="xeunshackle", rbb_trial=True, nand_tool=True), keys)
        self.assertTrue(any(d.startswith("Content/0000000000000000/5841122D/000D0000/") for d in plan.dests()))
        self.assertIn("Apps/Simple360NANDFlasher/Default.xex", plan.dests())
        with TemporaryDirectory() as td:
            info = build_staging(plan, srcs, Path(td), fingerprint_value=fingerprint(plan, Options(), {}))
            self.assertEqual(sum(s for _, s in info.files), plan.total_bytes)
        for s in srcs.values():
            s.close()


if __name__ == "__main__":
    unittest.main()
