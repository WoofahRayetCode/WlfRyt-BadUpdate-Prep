import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from badupdateprep.assemble import StagingInfo
from badupdateprep.errors import AssembleError
from badupdateprep.progress import CancelToken, Cancelled
from badupdateprep.usbcopy import MARKER, CopyPlan, copy_to_usb, plan_copy, verify_usb


def make_staging(root: Path, files: dict[str, bytes]) -> StagingInfo:
    cur = root / "current"
    for rel, data in files.items():
        p = cur / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (cur / "_manifest.json").write_text("{}")
    hashes = tuple(sorted((r, hashlib.sha256(d).hexdigest()) for r, d in files.items()))
    return StagingInfo(cur, "fp", tuple(sorted((r, len(d)) for r, d in files.items())), sum(len(d) for d in files.values()), hashes=hashes)


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
        result = copy_to_usb(plan, self.st, self.usb)
        written = result.written
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
        self.assertIsNone(plan.preserved, "walking the whole stick is slow; the plan must not do it")

    def test_marker_lists_written_files(self):
        copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        data = json.loads((self.usb / MARKER).read_text())
        self.assertEqual(set(data["files"]), set(FILES))
        self.assertTrue(all(len(v) == 64 for v in data["files"].values()), "marker records a sha256 per file")

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
        st = StagingInfo(st.path, st.fingerprint, st.files, st.total_bytes, edited=("launch.ini",), hashes=st.hashes)
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
        written = copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb).written
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
        self.assertTrue((self.usb / MARKER).exists(), "a cancelled copy still records what it did write (see ReCopyAndSpace)")
        for rel in calls[:1]:
            p = self.usb / rel
            if p.exists():
                self.assertEqual(p.read_bytes(), FILES[rel])  # complete or absent, never truncated

    def test_works_when_chmod_and_utime_are_forbidden(self):
        # vfat mounts without 'quiet' make copystat/chmod raise PermissionError; we must not depend on them.
        with mock.patch("os.chmod", side_effect=PermissionError), mock.patch("os.utime", side_effect=PermissionError):
            written = copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb).written
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


class MarkerIsUntrusted(unittest.TestCase):
    """The stick is untrusted input; a crafted .wlfryt-prep.json must never make us touch anything outside it."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.td = Path(self._td.name)
        self.usb = self.td / "usb"
        self.usb.mkdir()
        self.st = make_staging(self.td / "stage", FILES)

    def tearDown(self):
        self._td.cleanup()

    def test_traversal_and_absolute_paths_in_marker_are_ignored(self):
        victim_dir = self.td / "victim"
        victim_dir.mkdir()
        (victim_dir / "important.txt").write_text("precious")
        abs_victim = self.td / "abs_victim.txt"
        abs_victim.write_text("precious2")
        sha = hashlib.sha256(b"precious").hexdigest()
        for entry in (
            {"../victim/important.txt": sha, str(abs_victim): hashlib.sha256(b"precious2").hexdigest()},
            ["../victim/important.txt", str(abs_victim)],  # v1 list form
            {"a/../../victim/important.txt": sha, "..\\victim\\important.txt": sha, "C:/Windows/x": sha},
        ):
            (self.usb / MARKER).write_text(json.dumps({"files": entry}))
            plan = plan_copy(self.st, self.usb)
            self.assertEqual(plan.stale, (), entry)
            copy_to_usb(plan, self.st, self.usb)
            self.assertTrue((victim_dir / "important.txt").exists(), entry)
            self.assertTrue(abs_victim.exists(), entry)
            self.assertTrue(victim_dir.exists(), entry)

    def test_symlink_escape_is_ignored(self):
        outside = self.td / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("x")
        try:
            (self.usb / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        sha = hashlib.sha256(b"x").hexdigest()
        (self.usb / MARKER).write_text(json.dumps({"files": {"link/file.txt": sha}}))
        plan = plan_copy(self.st, self.usb)
        self.assertEqual(plan.stale, ())
        copy_to_usb(plan, self.st, self.usb)
        self.assertTrue((outside / "file.txt").exists())

    def test_malformed_markers_never_crash_planning(self):
        for bad in ('{"files": null}', '{"files": 5}', '{"files": "abc"}', "[1,2]", '{"files": [1, null, {"a": 1}]}', "not json", "", '{"files": {"x": 5}}'):
            (self.usb / MARKER).write_text(bad)
            plan = plan_copy(self.st, self.usb)  # must not raise
            self.assertEqual(plan.stale, (), bad)
            copy_to_usb(plan, self.st, self.usb)

    def test_v1_marker_without_hashes_never_deletes(self):
        (self.usb / "old.bin").write_bytes(b"old")
        (self.usb / MARKER).write_text(json.dumps({"files": ["old.bin"]}))
        plan = plan_copy(self.st, self.usb)
        self.assertEqual(plan.stale, ())
        copy_to_usb(plan, self.st, self.usb)
        self.assertTrue((self.usb / "old.bin").exists())

    def test_stale_file_the_user_changed_is_not_deleted(self):
        # reviewer scenario: app wrote launch.ini; the user (or DashLaunch) edited it; the next build no longer ships it
        st1 = make_staging(self.td / "s1", {**FILES, "launch.ini": b"default"})
        copy_to_usb(plan_copy(st1, self.usb), st1, self.usb)
        (self.usb / "launch.ini").write_bytes(b"[Plugins]\nplugin1 = MINE.xex\n")
        st2 = make_staging(self.td / "s2", FILES)
        plan = plan_copy(st2, self.usb)
        self.assertIn("launch.ini", plan.stale, "it is only a candidate at planning time")
        res = copy_to_usb(plan, st2, self.usb)
        self.assertEqual((self.usb / "launch.ini").read_bytes(), b"[Plugins]\nplugin1 = MINE.xex\n")
        self.assertEqual(res.left_alone, ("launch.ini",))
        self.assertEqual(res.removed, ())
        self.assertNotIn("launch.ini", json.loads((self.usb / MARKER).read_text())["files"], "it's the user's file now")

    def test_unmodified_stale_file_is_removed_with_its_empty_folders(self):
        st1 = make_staging(self.td / "s1", {**FILES, "Apps/Tool/Default.xex": b"tool"})
        copy_to_usb(plan_copy(st1, self.usb), st1, self.usb)
        st2 = make_staging(self.td / "s2", FILES)
        res = copy_to_usb(plan_copy(st2, self.usb), st2, self.usb)
        self.assertEqual(res.removed, ("Apps/Tool/Default.xex",))
        self.assertFalse((self.usb / "Apps").exists(), "emptied folders go too")
        self.assertTrue(self.usb.exists())

    def test_stale_deletion_rechecked_at_copy_time(self):
        st1 = make_staging(self.td / "s1", {**FILES, "extra.bin": b"mine"})
        copy_to_usb(plan_copy(st1, self.usb), st1, self.usb)
        st2 = make_staging(self.td / "s2", FILES)
        plan = plan_copy(st2, self.usb)
        (self.usb / "extra.bin").write_bytes(b"changed between planning and copying")
        res = copy_to_usb(plan, st2, self.usb)
        self.assertTrue((self.usb / "extra.bin").exists())
        self.assertEqual(res.left_alone, ("extra.bin",))


class PlanningIsCheap(unittest.TestCase):
    def test_plan_copy_never_walks_the_stick(self):
        # Reviewer finding: plan_copy rglob'd the whole stick (2-3x per selection) on the Tk thread; on a cold USB 2 stick
        # with tens of thousands of files that freezes the window. Planning may only stat the handful of files it manages.
        with TemporaryDirectory() as td:
            td = Path(td)
            usb = td / "usb"
            (usb / "photos").mkdir(parents=True)
            for i in range(50):
                (usb / "photos" / f"{i}.jpg").write_bytes(b"x")
            st = make_staging(td / "s", FILES)
            with mock.patch.object(Path, "rglob", side_effect=AssertionError("walked the stick")), \
                 mock.patch.object(Path, "iterdir", side_effect=AssertionError("listed a directory")), \
                 mock.patch("os.walk", side_effect=AssertionError("walked the stick")), \
                 mock.patch("os.scandir", side_effect=AssertionError("listed a directory")):
                plan = plan_copy(st, usb)
            self.assertEqual(len(plan.write), len(FILES))
            self.assertIsNone(plan.preserved)


class ReCopyAndSpace(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.td = Path(self._td.name)
        self.usb = self.td / "usb"
        self.usb.mkdir()
        self.big = os.urandom(300_000)
        self.st = make_staging(self.td / "stage", {**FILES, "Content/big": self.big})

    def tearDown(self):
        self._td.cleanup()

    def test_identical_rerun_writes_nothing_and_needs_no_temp_space(self):
        copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        plan = plan_copy(self.st, self.usb)
        self.assertEqual(set(plan.unchanged), set(FILES) | {"Content/big"})
        self.assertEqual((plan.bytes_needed, plan.growth, plan.peak_extra, plan.space_needed), (0, 0, 0, 0))
        before = {p: p.stat().st_mtime_ns for p in self.usb.rglob("*") if p.is_file() and p.name != MARKER}
        res = copy_to_usb(plan, self.st, self.usb)
        self.assertEqual((res.written, len(res.skipped)), ({}, len(FILES) + 1))
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before}, "unchanged files must not be rewritten")

    def test_changed_file_is_rewritten_and_budgets_peak_temp_space(self):
        copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        st2 = make_staging(self.td / "stage2", {**FILES, "Content/big": os.urandom(300_000)})
        plan = plan_copy(st2, self.usb)
        self.assertEqual(plan.replace, ("Content/big",))
        self.assertEqual(plan.growth, 0, "same size: no net growth")
        self.assertEqual(plan.peak_extra, 300_000, "the new copy sits beside the old one until the rename")
        self.assertEqual(plan.space_needed, 300_000)
        res = copy_to_usb(plan, st2, self.usb)
        self.assertEqual(set(res.written), {"Content/big"})
        self.assertEqual((self.usb / "Content/big").read_bytes(), (st2.path / "Content/big").read_bytes())

    def test_tampered_file_with_same_size_is_detected_and_rewritten(self):
        copy_to_usb(plan_copy(self.st, self.usb), self.st, self.usb)
        (self.usb / "Content/big").write_bytes(os.urandom(300_000))  # same size, different bytes
        plan = plan_copy(self.st, self.usb)
        self.assertIn("Content/big", plan.unchanged, "the cheap check can't see it ...")
        res = copy_to_usb(plan, self.st, self.usb)  # ... but the copy-time hash does
        self.assertIn("Content/big", res.written)
        self.assertEqual((self.usb / "Content/big").read_bytes(), self.big)

    def test_cancelled_copy_still_records_what_it_wrote(self):
        token = CancelToken()
        plan = plan_copy(self.st, self.usb)
        seen = []

        def prog(done, total, rel):
            seen.append(rel)
            if len(set(seen)) >= 2:
                token.cancel()

        with self.assertRaises(Cancelled):
            copy_to_usb(plan, self.st, self.usb, progress=prog, cancel=token)
        recorded = json.loads((self.usb / MARKER).read_text())["files"]
        on_stick = {p.relative_to(self.usb).as_posix() for p in self.usb.rglob("*") if p.is_file() and p.name != MARKER}
        self.assertTrue(on_stick)
        self.assertEqual(set(recorded), on_stick, "everything copied before the cancel must be tracked, so it can be cleaned up later")


if __name__ == "__main__":
    unittest.main()
