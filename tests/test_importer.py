import shutil
import subprocess
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep.importer import (
    ImportProblem,
    find_7z,
    import_extra,
    load_imported,
)
from tests import fixtures as fx


class Import(unittest.TestCase):
    def test_zip_with_wrapper_is_unwrapped(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            z = fx.make_zip(td / "x.zip", {"XeXmenu 1.2/default.xex": b"X", "XeXmenu 1.2/skin/a.bin": b"s"})
            imp = import_extra("xexmenu", z, app_dir=td / "app")
            self.assertEqual(imp.entry_xex, "default.xex")
            self.assertEqual(imp.file_count, 2)
            self.assertTrue((imp.root / "skin" / "a.bin").is_file())
            again = load_imported("xexmenu", td / "app")
            self.assertEqual(again, imp)

    def test_folder_import(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            src = fx.xexmenu_dir(td / "src")
            imp = import_extra("xexmenu", src, app_dir=td / "app")
            self.assertEqual(imp.entry_xex, "default.xex")
            self.assertEqual(len(imp.source_sha256), 64)

    def test_reimport_replaces_previous(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            import_extra("xexmenu", fx.make_zip(td / "a.zip", {"default.xex": b"1", "old.bin": b"o"}), app_dir=td / "app")
            imp = import_extra("xexmenu", fx.make_zip(td / "b.zip", {"default.xex": b"2"}), app_dir=td / "app")
            self.assertFalse((imp.root / "old.bin").exists())
            self.assertEqual((imp.root / "default.xex").read_bytes(), b"2")

    def test_zip_slip_rejected(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            z = fx.make_zip(td / "evil.zip", {"../../evil.xex": b"x"})
            with self.assertRaises(ImportProblem):
                import_extra("xexmenu", z, app_dir=td / "app")
            self.assertFalse((td / "evil.xex").exists())

    def test_no_xex_is_a_clear_error(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            z = fx.make_zip(td / "x.zip", {"readme.txt": b"hi"})
            with self.assertRaises(ImportProblem) as cm:
                import_extra("xexmenu", z, app_dir=td / "app")
            self.assertIn("xex", str(cm.exception).lower())

    def test_ambiguous_entry(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            z = fx.make_zip(td / "x.zip", {"a.xex": b"1", "b.xex": b"2"})
            with self.assertRaises(ImportProblem) as cm:
                import_extra("xexmenu", z, app_dir=td / "app")
            self.assertIn("a.xex", str(cm.exception))

    def test_prefers_default_or_xexmenu_name(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            z = fx.make_zip(td / "x.zip", {"helper.xex": b"1", "XexMenu.xex": b"2"})
            self.assertEqual(import_extra("xexmenu", z, app_dir=td / "app").entry_xex, "XexMenu.xex")

    def test_7z_without_7zip_gives_install_hint(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            p = td / "XeXmenu_12.7z"
            p.write_bytes(b"7z\xbc\xaf\x27\x1c")
            with self.assertRaises(ImportProblem) as cm:
                import_extra("xexmenu", p, app_dir=td / "app", which=lambda n: None)
            self.assertIn("7-Zip", str(cm.exception))

    def test_7z_runs_with_argument_list_and_no_shell(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            p = td / "XeXmenu_12.7z"
            p.write_bytes(b"fake")
            calls = []

            def fake_run(cmd, **kw):
                calls.append((cmd, kw))
                if cmd[1] == "l":
                    return subprocess.CompletedProcess(cmd, 0, "Path = XeXMenu/default.xex\nSize = 12345\n", "")
                out = Path([a for a in cmd if a.startswith("-o")][0][2:])
                (out / "XeXMenu").mkdir()
                (out / "XeXMenu" / "default.xex").write_bytes(b"X")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            imp = import_extra("xexmenu", p, app_dir=td / "app", run=fake_run, which=lambda n: "/usr/bin/7z" if n == "7z" else None)
            self.assertEqual(imp.entry_xex, "default.xex")
            self.assertEqual([c[0][1] for c in calls], ["l", "x"], "list first, extract second")
            for cmd, kw in calls:
                self.assertIsInstance(cmd, list)
                self.assertNotIn("shell", kw)
                self.assertEqual(cmd[-2:], ["--", str(p)])

    def test_7z_that_would_expand_beyond_the_cap_is_refused_before_extracting(self):
        # Reviewer finding: MAX_IMPORT_BYTES was enforced for zip and folder imports but not 7z, so only the 180 s timeout
        # bounded how much a hostile archive could expand into the app dir.
        with TemporaryDirectory() as td:
            td = Path(td)
            p = td / "bomb.7z"
            p.write_bytes(b"fake")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd[1])
                return subprocess.CompletedProcess(cmd, 0, "Path = a\nSize = 400000000\nPath = b\nSize = 400000000\n", "")

            with self.assertRaises(ImportProblem) as cm:
                import_extra("xexmenu", p, app_dir=td / "app", run=fake_run, which=lambda n: "/usr/bin/7z")
            self.assertIn("much larger", str(cm.exception))
            self.assertEqual(calls, ["l"], "must refuse before extracting anything")
            self.assertFalse((td / "app" / "xexmenu").exists())

    def test_7z_listing_failure_is_reported(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            p = td / "x.7z"
            p.write_bytes(b"x")
            run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", "Can not open file as archive")
            with self.assertRaises(ImportProblem) as cm:
                import_extra("xexmenu", p, app_dir=td / "app", run=run, which=lambda n: "/x/7z")
            self.assertIn("Can not open", str(cm.exception))

    def test_7z_failure_surfaces_stderr(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            p = td / "x.7z"
            p.write_bytes(b"x")
            run = lambda cmd, **kw: (
                subprocess.CompletedProcess(cmd, 0, "Size = 10\n", "") if cmd[1] == "l" else subprocess.CompletedProcess(cmd, 2, "", "Headers Error")
            )
            with self.assertRaises(ImportProblem) as cm:
                import_extra("xexmenu", p, app_dir=td / "app", run=run, which=lambda n: "/x/7z")
            self.assertIn("Headers Error", str(cm.exception))

    @unittest.skipUnless(shutil.which("7z"), "7z not installed")
    def test_real_7z_roundtrip(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            src = fx.xexmenu_dir(td / "src" / "XeXMenu")
            arch = td / "XeXmenu_12.7z"
            subprocess.run(["7z", "a", str(arch), str(td / "src" / "XeXMenu")], check=True, capture_output=True)
            imp = import_extra("xexmenu", arch, app_dir=td / "app")
            self.assertEqual(imp.entry_xex, "default.xex")

    def test_wrong_type(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "x.rar"
            p.write_bytes(b"x")
            with self.assertRaises(ImportProblem):
                import_extra("xexmenu", p, app_dir=Path(td) / "app")

    def test_find_7z_uses_which(self):
        self.assertEqual(find_7z(lambda n: "/opt/7zz" if n == "7zz" else None), "/opt/7zz")


if __name__ == "__main__":
    unittest.main()
