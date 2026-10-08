import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from badupdateprep.assemble import StagingInfo
from badupdateprep.errors import AssembleError
from badupdateprep.progress import CancelToken, Cancelled
from badupdateprep.usbcopy import MARKER, copy_to_usb, plan_copy, verify_usb


def make_staging(root: Path, files: dict[str, bytes]) -> StagingInfo:
    cur = root / "current"
    for rel, data in files.items():
        p = cur / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (cur / "_manifest.json").write_text("{}")
    return StagingInfo(cur, "fp", tuple(sorted((r, len(d)) for r, d in files.items())), sum(len(d) for d in files.values()))


FILES = {
    "BadUpdatePayload/default.xex": b"NEW-PAYLOAD",
    "BadUpdatePayload/BadUpdateExploit-2ndStage.bin": b"2nd" * 1000,
    "Content/0000000000000000/5841122D/00000001/save": b"save",
}


class Copy(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.td = Path(self._td.name)
        self.usb = self.td / "usb"
        self.usb.mkdir()
        self.st = make_staging(self.td / "stage", FILES)

    def tearDown(self):
        self._td.cleanup()

    def test_fresh_copy_and_verify(self):
        plan = plan_copy(self.st, self.usb)
        self.assertEqual(plan.replace, ())
        self.assertEqual(plan.bytes_needed, sum(len(d) for d in FILES.values()))
        written = copy_to_usb(plan, self.st, self.usb)
        for rel, data in FILES.items():
            self.assertEqual((self.usb / rel).read_bytes(), data)
        self.assertFalse((self.usb / "_manifest.json").exists(), "internal manifest must not reach the stick")
        self.assertFalse(list(self.usb.rglob("*.wlfryt-tmp")))
        self.assertEqual(set(written), set(FILES))
        rep = verify_usb(self.usb, written)
        self.assertTrue(rep.ok)
        self.assertEqual(rep.checked, 3)

    def test_overwrite_counts_replaced_files(self):
        (self.usb / "BadUpdatePayload").mkdir()
        (self.usb / "BadUpdatePayload/default.xex").write_bytes(b"OLD")
        plan = plan_copy(self.st, self.usb)
        self.assertEqual(plan.replace, ("BadUpdatePayload/default.xex",))
        self.assertEqual(plan.bytes_replaced, 3)
        copy_to_usb(plan, self.st, self.usb)
        self.assertEqual((self.usb / "BadUpdatePayload/default.xex").read_bytes(), b"NEW-PAYLOAD")

    def test_stale_files_removed_only_if_this_app_wrote_them(self):
        copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        # the user's stick now also has XeUnshackle's MAC backup and an unrelated file
        (self.usb / "BadUpdatePayload/MACBackup.bin").write_bytes(b"mac")
        (self.usb / "photos").mkdir()
        (self.usb / "photos/cat.jpg").write_bytes(b"jpg")
        # next build no longer ships the 2nd stage file
        smaller = {k: v for k, v in FILES.items() if "2ndStage" not in k}
        st2 = make_staging(self.td / "stage2", smaller)
        plan = plan_copy(st2, self.usb)
        self.assertEqual(plan.stale, ("BadUpdatePayload/BadUpdateExploit-2ndStage.bin",))
        copy_to_usb(plan, st2, self.usb)
        self.assertFalse((self.usb / "BadUpdatePayload/BadUpdateExploit-2ndStage.bin").exists())
        self.assertEqual((self.usb / "BadUpdatePayload/MACBackup.bin").read_bytes(), b"mac")
        self.assertEqual((self.usb / "photos/cat.jpg").read_bytes(), b"jpg")
        self.assertTrue((self.usb / "BadUpdatePayload").is_dir())

    def test_no_marker_means_nothing_is_deleted(self):
        (self.usb / "BadUpdatePayload").mkdir()
        (self.usb / "BadUpdatePayload/old-leftover.bin").write_bytes(b"x")
        plan = plan_copy(self.st, self.usb)
        self.assertEqual(plan.stale, ())
        copy_to_usb(plan, self.st, self.usb)
        self.assertTrue((self.usb / "BadUpdatePayload/old-leftover.bin").exists())
        self.assertGreaterEqual(plan.preserved, 1)

    def test_marker_lists_written_files(self):
        copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        data = json.loads((self.usb / MARKER).read_text())
        self.assertEqual(set(data["files"]), set(FILES))

    def test_users_customised_launch_ini_is_kept(self):
        # XeUnshackle's README tells people to keep their DashLaunch plugins in launch.ini on the stick.
        st = make_staging(self.td / "s", {**FILES, "launch.ini": b"[Plugins]\nplugin1 = default\n"})
        copy_to_usb(plan_copy(st, self.usb), st, self.usb)  # first run writes the default
        (self.usb / "launch.ini").write_bytes(b"[Plugins]\nplugin1 = MY-PLUGIN.xex\n")
        plan = plan_copy(st, self.usb)
        self.assertEqual(plan.kept, ("launch.ini",))
        self.assertNotIn("launch.ini", plan.stale, "a kept file must not be treated as stale and deleted")
        self.assertNotIn("launch.ini", [r for r, _ in plan.write])
        copy_to_usb(plan, st, self.usb)
        self.assertEqual((self.usb / "launch.ini").read_bytes(), b"[Plugins]\nplugin1 = MY-PLUGIN.xex\n")
        self.assertFalse(list(self.usb.glob("launch.ini.bak-*")))
        self.assertNotIn("launch.ini", json.loads((self.usb / MARKER).read_text())["files"])
        # and it stays safe on every later run, even when the build no longer ships launch.ini
        st2 = make_staging(self.td / "s2", FILES)
        self.assertNotIn("launch.ini", plan_copy(st2, self.usb).stale)

    def test_identical_launch_ini_is_just_rewritten(self):
        st = make_staging(self.td / "s", {**FILES, "launch.ini": b"same"})
        copy_to_usb(plan_copy(st, self.usb), st, self.usb)
        plan = plan_copy(st, self.usb)
        self.assertEqual((plan.kept, plan.backups), ((), ()))

    def test_deliberately_edited_launch_ini_is_backed_up_then_replaced(self):
        st = make_staging(self.td / "s", {**FILES, "launch.ini": b"[Paths]\nBUT_X = Usb:\\Apps\\X\\default.xex\n"})
        st = StagingInfo(st.path, st.fingerprint, st.files, st.total_bytes, edited=("launch.ini",))
        (self.usb / "launch.ini").write_bytes(b"[Plugins]\nplugin1 = MY-PLUGIN.xex\n")
        plan = plan_copy(st, self.usb)
        self.assertEqual((plan.kept, plan.backups), ((), ("launch.ini",)))
        copy_to_usb(plan, st, self.usb)
        self.assertIn(b"BUT_X", (self.usb / "launch.ini").read_bytes())
        (bak,) = list(self.usb.glob("launch.ini.bak-*"))
        self.assertEqual(bak.read_bytes(), b"[Plugins]\nplugin1 = MY-PLUGIN.xex\n")

    def test_freemyxe_ini_is_also_protected(self):
        st = make_staging(self.td / "s", {**FILES, "BadUpdatePayload/FreeMyXe.ini": b"[Auto]\nDefault=off\n"})
        (self.usb / "BadUpdatePayload").mkdir(exist_ok=True)
        (self.usb / "BadUpdatePayload" / "FreeMyXe.ini").write_bytes(b"[Auto]\nDefault=patch\n")
        self.assertEqual(plan_copy(st, self.usb).kept, ("BadUpdatePayload/FreeMyXe.ini",))

    def test_verify_detects_corruption_and_missing(self):
        written = copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        (self.usb / "BadUpdatePayload/default.xex").write_bytes(b"NEW-PAYLOAX")  # same length, flipped byte
        (self.usb / "Content/0000000000000000/5841122D/00000001/save").unlink()
        rep = verify_usb(self.usb, written)
        self.assertFalse(rep.ok)
        self.assertEqual({r for r, _ in rep.bad}, {"BadUpdatePayload/default.xex", "Content/0000000000000000/5841122D/00000001/save"})

    def test_cancel_leaves_no_temp_or_truncated_target(self):
        token = CancelToken()
        plan = plan_copy(self.st, self.usb)
        calls = []

        def prog(done, total, rel):
            calls.append(rel)
            token.cancel()

        with self.assertRaises(Cancelled):
            copy_to_usb(plan, self.st, self.usb, progress=prog, cancel=token)
        self.assertFalse(list(self.usb.rglob("*.wlfryt-tmp")))
        self.assertFalse((self.usb / MARKER).exists())
        for rel in calls[:1]:
            p = self.usb / rel
            if p.exists():
                self.assertEqual(p.read_bytes(), FILES[rel])  # complete or absent, never truncated

    def test_works_when_chmod_and_utime_are_forbidden(self):
        # vfat mounts without 'quiet' make copystat/chmod raise PermissionError; we must not depend on them.
        with mock.patch("os.chmod", side_effect=PermissionError), mock.patch("os.utime", side_effect=PermissionError):
            written = copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        self.assertEqual(set(written), set(FILES))

    def test_missing_usb(self):
        with self.assertRaises(AssembleError):
            plan_copy(self.st, self.td / "nope")

    def test_write_error_is_reported_with_the_file_name(self):
        plan = plan_copy(self.st, self.usb)
        real_replace = os.replace

        def boom(src, dst):
            if str(dst).endswith("default.xex"):
                raise OSError(28, "No space left on device")
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=boom):
            with self.assertRaises(AssembleError) as cm:
                copy_to_usb(plan, self.st, self.usb)
        self.assertIn("default.xex", str(cm.exception))
        self.assertFalse(list(self.usb.rglob("*.wlfryt-tmp")))


if __name__ == "__main__":
    unittest.main()
