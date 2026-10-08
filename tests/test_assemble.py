"""Plan + assemble against fixtures that mirror the REAL upstream zip layouts."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep.assemble import (
    ASSEMBLER_REV,
    build_staging,
    fingerprint,
    load_staging,
    staging_is_current,
)
from badupdateprep.catalog import CATALOG
from badupdateprep.errors import AssembleError
from badupdateprep.launch_ini import IniEdit
from badupdateprep.options import AutoLaunch, Options
from badupdateprep.plan import build_plan, fat_problems, resolve_content_root
from badupdateprep.progress import CancelToken, Cancelled
from badupdateprep.sources import DirSource, ZipSource, normalize_member
from tests import fixtures as fx

BU = "BadUpdatePayload/"
SAVE = "Content/0000000000000000/5841122D/00000001/DaTArrest_Xbox360BadUpdate"
TRIAL = "Content/0000000000000000/5841122D/000D0000/DD774F20C36263F2"
BU_FILES = [
    BU + "BadUpdateExploit-2ndStage.bin",
    BU + "BadUpdateExploit-3rdStage.bin",
    BU + "BadUpdateExploit-4thStage.bin",
    BU + "BadUpdateExploit-Data.bin",
    BU + "bootanim.xex",
    BU + "default.xex",
    BU + "update_data.bin",
    BU + "xke_update.bin",
    SAVE,
]


class Env:
    def __init__(self, td, opts):
        self.td = Path(td)
        self.zips = fx.all_zips(self.td)
        self.sources = {k: ZipSource(p) for k, p in self.zips.items()}
        self.opts = opts

    def plan(self, **kw):
        return build_plan(self.opts, self.sources, **kw)

    def close(self):
        for s in self.sources.values():
            s.close()


def plan_for(td, **opt_kw):
    env = Env(td, Options(**opt_kw))
    try:
        return env.plan()
    finally:
        env.close()


class RealLayouts(unittest.TestCase):
    def test_rbb_xeunshackle_trial(self):
        with TemporaryDirectory() as td:
            plan = plan_for(td, entry="rbb", payload="xeunshackle", rbb_trial=True)
            want = sorted(
                BU_FILES
                + [TRIAL, BU + "BadStorage.xex.dll", "JRPC2.xex", "Xbdm.xex", "launch.ini"]
            )
            self.assertEqual(plan.dests(), want)
            self.assertEqual(plan.find(BU + "default.xex").layer, "xeunshackle", "payload must replace stock default.xex")

    def test_freemyxe_goes_entirely_into_badupdatepayload(self):
        # Regression: the old assembler put FreeMyXe.ini / xell-2f.bin / BadStorage.dll in the USB root.
        with TemporaryDirectory() as td:
            plan = plan_for(td, entry="rbb", payload="freemyxe", rbb_trial=False)
            want = sorted(BU_FILES + [BU + "FreeMyXe.ini", BU + "xell-2f.bin", BU + "BadStorage.dll"])
            self.assertEqual(plan.dests(), want)
            self.assertEqual(plan.find(BU + "default.xex").layer, "freemyxe")
            self.assertIsNone(plan.find("FreeMyXe.ini"))
            self.assertIsNone(plan.find("BadStorage.dll"))

    def test_stock_payload_keeps_pack_default_xex(self):
        with TemporaryDirectory() as td:
            plan = plan_for(td, entry="rbb", payload="stock", rbb_trial=False)
            self.assertEqual(plan.dests(), sorted(BU_FILES))
            self.assertEqual(plan.find(BU + "default.xex").layer, "badupdate")

    def test_avatar_with_payload(self):
        with TemporaryDirectory() as td:
            plan = plan_for(td, entry="avatar", payload="xeunshackle")
            dests = plan.dests()
            self.assertIn("Content/E0002FF78DFBDE7B/FFFE07D1/00010000/E0002FF78DFBDE7B", dests)
            self.assertEqual(plan.find(BU + "default.xex").layer, "xeunshackle")
            self.assertFalse(any("5841122D" in d for d in dests), "RBB content must not leak into the avatar stick")

    def test_avatar_with_stock_is_an_error_not_a_silent_stick(self):
        with TemporaryDirectory() as td:
            with self.assertRaises(AssembleError) as cm:
                plan_for(td, entry="avatar", payload="stock")
            self.assertIn("default.xex", str(cm.exception))

    def test_trial_and_save_coexist_and_content_is_not_unwrapped(self):
        with TemporaryDirectory() as td:
            dests = plan_for(td, payload="stock", rbb_trial=True).dests()
            self.assertIn(SAVE, dests)
            self.assertIn(TRIAL, dests)

    def test_nand_tool_lands_under_apps_with_real_filename(self):
        with TemporaryDirectory() as td:
            dests = plan_for(td, nand_tool=True).dests()
            self.assertIn("Apps/Simple360NANDFlasher/Default.xex", dests)
            self.assertFalse(any(d.startswith("Applications/") for d in dests))

    def test_xeunshackle_readme_is_not_copied_to_the_stick(self):
        with TemporaryDirectory() as td:
            self.assertFalse(any("README" in d for d in plan_for(td).dests()))

    def test_freemyxe_autolaunch_copies_nand_tool_to_after_patch(self):
        with TemporaryDirectory() as td:
            plan = plan_for(td, payload="freemyxe", nand_tool=True, autolaunch=AutoLaunch("BUT_Y", "nand"))
            e = plan.find(BU + "after_patch.xex")
            self.assertEqual(e.member.rsplit("/", 1)[-1], "Default.xex")


class LayoutDriftIsLoud(unittest.TestCase):
    def test_missing_required_file(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            env = Env(td, Options(payload="stock", rbb_trial=False))
            env.sources["badupdate"].close()
            env.sources["badupdate"] = ZipSource(
                fx.make_zip(td / "bad.zip", {"Rock Band Blitz/BadUpdatePayload/default.xex": b"x"})
            )
            with self.assertRaises(AssembleError) as cm:
                env.plan()
            self.assertIn("Tested versions", str(cm.exception))
            env.close()

    def test_unexpected_conflict(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            env = Env(td, Options(payload="xeunshackle", rbb_trial=False, nand_tool=False))
            env.sources["xeunshackle"].close()
            # a payload zip that also ships the exploit's own 2nd stage -> not allowed to silently clobber it
            env.sources["xeunshackle"] = ZipSource(
                fx.make_zip(
                    td / "x.zip",
                    {
                        "BadUpdatePayload/default.xex": b"x",
                        "BadUpdatePayload/BadUpdateExploit-2ndStage.bin": b"evil",
                        "launch.ini": b"[Paths]\n",
                    },
                )
            )
            with self.assertRaises(AssembleError) as cm:
                env.plan()
            self.assertIn("both want to write", str(cm.exception))
            env.close()

    def test_pack_without_badupdatepayload_dir(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            env = Env(td, Options(payload="stock", rbb_trial=False))
            env.sources["badupdate"].close()
            env.sources["badupdate"] = ZipSource(fx.make_zip(td / "b.zip", {"readme.txt": b"nope"}))
            with self.assertRaises(AssembleError):
                env.plan()
            env.close()

    def test_missing_source(self):
        with self.assertRaises(AssembleError):
            build_plan(Options(), {})

    def test_case_insensitive_collision(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            env = Env(td, Options(payload="stock", rbb_trial=False))
            env.sources["badupdate"].close()
            env.sources["badupdate"] = ZipSource(
                fx.make_zip(
                    td / "b.zip",
                    {
                        "BadUpdatePayload/default.xex": b"x",
                        "BadUpdatePayload/BadUpdateExploit-2ndStage.bin": b"1",
                        "badupdatepayload/BADUPDATEEXPLOIT-2NDSTAGE.BIN": b"2",
                        SAVE: b"s",
                    },
                )
            )
            with self.assertRaises(AssembleError):
                env.plan()
            env.close()


class SafetyChecks(unittest.TestCase):
    def test_normalize_member(self):
        self.assertEqual(normalize_member("a\\b\\c.txt"), "a/b/c.txt")
        self.assertIsNone(normalize_member("dir/"))
        self.assertIsNone(normalize_member("__MACOSX/._x"))
        self.assertIsNone(normalize_member("a/.DS_Store"))
        for bad in ("../evil", "a/../../evil", "/abs", "C:\\win", "C:/win"):
            with self.assertRaises(AssembleError, msg=bad):
                normalize_member(bad)

    def test_zip_slip_rejected_when_opening_the_zip(self):
        with TemporaryDirectory() as td:
            z = fx.make_zip(Path(td) / "evil.zip", {"../../evil.txt": b"x"})
            with self.assertRaises(AssembleError):
                ZipSource(z)

    def test_not_a_zip(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "x.zip"
            p.write_bytes(b"this is not a zip")
            with self.assertRaises(AssembleError):
                ZipSource(p)

    def test_fat_problems(self):
        self.assertEqual(fat_problems("ok/file.bin"), [])
        self.assertTrue(fat_problems('bad/na"me.bin'))
        self.assertTrue(fat_problems("trailing./x"))
        self.assertTrue(fat_problems("big.bin", 5 * 1024**3))
        self.assertTrue(fat_problems("x" * 300))

    def test_content_root_modes(self):
        spec = CATALOG["xeunshackle"].install
        self.assertEqual(resolve_content_root(["W/a", "W/b/c"], spec), "W")
        self.assertEqual(resolve_content_root(["a", "W/b"], spec), "")
        self.assertEqual(resolve_content_root(["only.txt"], spec), "")
        self.assertEqual(resolve_content_root(["Content/x"], CATALOG["rbb_trial"].install), "")

    def test_dirsource_skips_symlinks(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_bytes(b"1")
            try:
                (root / "link").symlink_to(root / "a.txt")
            except OSError:
                self.skipTest("symlinks unavailable")
            self.assertEqual([m for m, _ in DirSource(root).members()], ["a.txt"])


class Staging(unittest.TestCase):
    def _build(self, td, opts, edits=()):
        env = Env(td, opts)
        plan = env.plan()
        fp = fingerprint(plan, opts, {"badupdate": "aa"}, edits)
        info = build_staging(plan, env.sources, Path(td) / "usb", fingerprint_value=fp, ini_edits=edits)
        env.close()
        return plan, fp, info

    def test_builds_tree_with_exact_contents_and_no_work_dir(self):
        with TemporaryDirectory() as td:
            plan, fp, info = self._build(td, Options(payload="xeunshackle", rbb_trial=True))
            root = info.path
            self.assertEqual((root / BU / "default.xex").read_bytes(), b"XEUNSHACKLE")
            self.assertEqual((root / SAVE).read_bytes(), b"save")
            self.assertEqual((root / TRIAL).read_bytes(), b"T" * 5000)
            self.assertEqual((root / "launch.ini").read_bytes(), fx.LAUNCH_INI.encode("latin-1"))
            self.assertFalse(list(Path(td, "usb").glob(".build-*")))
            self.assertFalse((root / "_work").exists())
            self.assertEqual(sorted(p for p, _ in info.files), plan.dests())

    def test_launch_ini_edits_are_applied_to_staged_copy_only(self):
        with TemporaryDirectory() as td:
            edits = (IniEdit("Paths", "BUT_X", r"Usb:\Apps\XexMenu\default.xex"),)
            _, _, info = self._build(td, Options(payload="xeunshackle", rbb_trial=False), edits)
            staged = (info.path / "launch.ini").read_text("latin-1")
            self.assertIn(r"BUT_X = Usb:\Apps\XexMenu\default.xex", staged)
            self.assertIn("BUT_Y = Sfc:\\dash.xex ; system dash", staged)
            self.assertNotIn("C0DE9999", staged)

    def test_fingerprint_sensitivity(self):
        with TemporaryDirectory() as td:
            env = Env(td, Options(payload="xeunshackle", rbb_trial=False))
            plan = env.plan()
            base = fingerprint(plan, env.opts, {"a": "1"})
            self.assertEqual(base, fingerprint(plan, env.opts, {"a": "1"}))
            self.assertNotEqual(base, fingerprint(plan, env.opts, {"a": "2"}), "source checksum")
            self.assertNotEqual(base, fingerprint(plan, Options(payload="stock", rbb_trial=False), {"a": "1"}), "options")
            self.assertNotEqual(base, fingerprint(plan, env.opts, {"a": "1"}, [IniEdit("Paths", "BUT_X", "v")]), "ini edits")
            import badupdateprep.assemble as asm

            old = asm.ASSEMBLER_REV
            try:
                asm.ASSEMBLER_REV = old + 1
                self.assertNotEqual(base, fingerprint(plan, env.opts, {"a": "1"}), "assembler revision")
            finally:
                asm.ASSEMBLER_REV = old
            env.close()

    def test_fingerprint_ignores_app_version(self):
        import badupdateprep

        with TemporaryDirectory() as td:
            env = Env(td, Options(rbb_trial=False))
            plan = env.plan()
            a = fingerprint(plan, env.opts, {})
            old = badupdateprep.APP_VERSION
            try:
                badupdateprep.APP_VERSION = "2099.1.1.0000"
                self.assertEqual(a, fingerprint(plan, env.opts, {}))
            finally:
                badupdateprep.APP_VERSION = old
            env.close()

    def test_staging_is_current_detects_tampering(self):
        with TemporaryDirectory() as td:
            _, fp, info = self._build(td, Options(rbb_trial=False))
            loaded = load_staging(Path(td) / "usb")
            self.assertTrue(staging_is_current(loaded, fp))
            self.assertFalse(staging_is_current(loaded, "other"))
            self.assertFalse(staging_is_current(None, fp))
            (info.path / BU / "default.xex").write_bytes(b"changed length")
            self.assertFalse(staging_is_current(load_staging(Path(td) / "usb"), fp))
            (info.path / BU / "default.xex").unlink()
            self.assertFalse(staging_is_current(load_staging(Path(td) / "usb"), fp))

    def test_changed_options_replace_staging(self):
        with TemporaryDirectory() as td:
            self._build(td, Options(payload="xeunshackle", rbb_trial=False))
            _, _, info = self._build(td, Options(payload="freemyxe", rbb_trial=False))
            self.assertTrue((info.path / BU / "xell-2f.bin").exists())
            self.assertFalse((info.path / "launch.ini").exists(), "old payload's files must not linger")

    def test_cancelled_build_keeps_previous_staging(self):
        with TemporaryDirectory() as td:
            _, fp, first = self._build(td, Options(payload="xeunshackle", rbb_trial=False))
            env = Env(td, Options(payload="freemyxe", rbb_trial=False))
            plan = env.plan()
            token = CancelToken()
            token.cancel()
            with self.assertRaises(Cancelled):
                build_staging(plan, env.sources, Path(td) / "usb", fingerprint_value="x", cancel=token)
            env.close()
            self.assertTrue(staging_is_current(load_staging(Path(td) / "usb"), fp))
            self.assertFalse(list(Path(td, "usb").glob(".build-*")))

    def test_failed_build_cleans_up(self):
        with TemporaryDirectory() as td:
            env = Env(td, Options(payload="stock", rbb_trial=False))
            plan = env.plan()
            env.sources["badupdate"].close()  # reading a closed zip fails mid-build
            with self.assertRaises(Exception):
                build_staging(plan, env.sources, Path(td) / "usb", fingerprint_value="x")
            self.assertFalse(list(Path(td, "usb").glob(".build-*")))
            self.assertIsNone(load_staging(Path(td) / "usb"))


if __name__ == "__main__":
    unittest.main()
