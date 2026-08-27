import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep.assemble import assemble_usb, find_game_folder
from badupdateprep.catalog import pick_zip_containing


class Pickers(unittest.TestCase):
    def test_usb_zip(self):
        assets = [
            {"name": "Xbox360BadUpdate-Retail-USB-v1.3.zip", "browser_download_url": "http://u"},
            {"name": "source.zip", "browser_download_url": "http://s"},
        ]
        self.assertEqual(pick_zip_containing("usb")(assets), "http://u")


class Assemble(unittest.TestCase):
    def _zip(self, path: Path, files: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            for n, d in files.items():
                zf.writestr(n, d)

    def test_rbb_xeunshackle_layout(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            bu = td / "bu.zip"
            self._zip(
                bu,
                {
                    "Rock Band Blitz/BadUpdatePayload/default.xex": b"stock",
                    "Rock Band Blitz/Content/0000000000000000/save.bin": b"save",
                    "Rock Band Blitz/name.txt": b"rbb",
                },
            )
            xe = td / "xe.zip"
            self._zip(
                xe,
                {
                    "XeUnshackle.xex": b"unshackle",
                    "launch.ini": b"[Plugins]\n",
                    "DashLaunch/launch.xex": b"dl",
                },
            )
            nand = td / "nand.zip"
            self._zip(nand, {"Simple360NANDFlasher.xex": b"dump"})
            trial = td / "rbb.zip"
            self._zip(
                trial,
                {
                    "Content/0000000000000000/5841122D/000D0000/god.bin": b"x" * 2_000_000,
                },
            )
            staging = td / "usb"
            assemble_usb(
                {"badupdate": bu, "xeunshackle": xe, "nand_ro": nand, "rbb_trial": trial},
                staging,
                entry="rbb",
                payload="xeunshackle",
                include_nand_tool=True,
                include_rbb_trial=True,
            )
            self.assertEqual((staging / "BadUpdatePayload" / "default.xex").read_bytes(), b"unshackle")
            self.assertTrue((staging / "Content" / "0000000000000000" / "save.bin").is_file())
            self.assertTrue((staging / "Content" / "0000000000000000" / "5841122D" / "000D0000" / "god.bin").is_file())
            self.assertTrue((staging / "launch.ini").is_file())
            self.assertTrue((staging / "Applications" / "Simple360NANDFlasher" / "Simple360NANDFlasher.xex").is_file())
            self.assertFalse((staging / "_work").exists())

    def test_find_game_folder(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "Rock Band Blitz" / "BadUpdatePayload").mkdir(parents=True)
            self.assertEqual(find_game_folder(root).name, "Rock Band Blitz")


if __name__ == "__main__":
    unittest.main()
