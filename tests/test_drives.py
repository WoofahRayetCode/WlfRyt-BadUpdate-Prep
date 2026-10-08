import json
import plistlib
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep.drives import Drive, assess, describe_path, has_errors, is_windows_system_path, looks_like_system_mount, normalize_fs
from badupdateprep.drives import linux, macos, windows

GB = 1024**3

LSBLK = {
    "blockdevices": [
        {  # internal NVMe: EFI system partition (vfat!) + btrfs root
            "name": "nvme0n1", "path": "/dev/nvme0n1", "rm": False, "hotplug": False, "tran": "nvme", "type": "disk",
            "size": 1024 * GB, "fstype": None, "mountpoints": [None],
            "children": [
                {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "rm": False, "hotplug": False, "tran": "nvme", "type": "part",
                 "size": 2 * GB, "fstype": "vfat", "fsver": "FAT32", "label": None, "mountpoints": ["/boot"], "fsavail": 1 * GB},
                {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "rm": False, "hotplug": False, "tran": "nvme", "type": "part",
                 "size": 900 * GB, "fstype": "btrfs", "label": None, "mountpoints": ["/", "/home"], "fsavail": 500 * GB},
            ],
        },
        {"name": "zram0", "path": "/dev/zram0", "rm": False, "hotplug": False, "tran": None, "type": "disk",
         "size": 8 * GB, "fstype": "swap", "mountpoints": ["[SWAP]"]},
        {  # a USB stick: parent disk has no fs, the partition is FAT32 and mounted by udisks
            "name": "sdb", "path": "/dev/sdb", "rm": True, "hotplug": True, "tran": "usb", "type": "disk",
            "size": 16 * GB, "fstype": None, "mountpoints": [None],
            "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "rm": True, "hotplug": True, "tran": "usb", "type": "part",
                 "size": 16 * GB - 1048576, "fstype": "vfat", "fsver": "FAT32", "label": "XBOX USB",
                 "mountpoints": ["/run/media/eric/XBOX USB"], "fsavail": 15 * GB},
            ],
        },
        {  # a second stick, NTFS, unmounted
            "name": "sdc", "path": "/dev/sdc", "rm": True, "hotplug": True, "tran": "usb", "type": "disk",
            "size": 64 * GB, "fstype": "ntfs", "label": "BIGSTICK", "mountpoints": [None],
        },
    ]
}


class LinuxParsing(unittest.TestCase):
    def test_usb_stick_found_and_esp_flagged_as_system(self):
        drives = {d.mount or d.device: d for d in linux.parse_lsblk(json.dumps(LSBLK))}
        usb = drives["/run/media/eric/XBOX USB"]
        self.assertEqual((usb.fs, usb.label, usb.removable, usb.system, usb.bus), ("FAT32", "XBOX USB", True, False, "usb"))
        self.assertEqual(usb.free, 15 * GB)
        esp = drives["/boot"]
        self.assertTrue(esp.system, "the EFI partition is vfat but must never be offered as a USB target")
        self.assertFalse(esp.removable)
        self.assertNotIn("[SWAP]", drives)

    def test_unmounted_removable_listed_but_unmounted_internal_hidden(self):
        ds = linux.parse_lsblk(json.dumps(LSBLK))
        unm = [d for d in ds if not d.mounted]
        self.assertEqual([d.device for d in unm], ["/dev/sdc"])
        self.assertEqual(unm[0].fs, "NTFS")
        self.assertEqual(assess(unm[0])[0].level, "error")

    def test_old_lsblk_singular_mountpoint_and_string_bools(self):
        data = {"blockdevices": [{"name": "sdb1", "path": "/dev/sdb1", "rm": "1", "hotplug": "1", "tran": "usb", "size": "8000000000",
                                  "fstype": "vfat", "label": "OLD", "mountpoint": "/media/OLD"}]}
        (d,) = linux.parse_lsblk(json.dumps(data))
        self.assertEqual((d.mount, d.removable, d.fs, d.size), ("/media/OLD", True, "FAT", 8_000_000_000))

    def test_collect_falls_back_to_minimal_columns_then_proc_mounts(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[-1])
            if "FSVER" in cmd[-1]:
                return subprocess.CompletedProcess(cmd, 1, "", "lsblk: unknown column: FSVER")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"blockdevices": []}), "")

        self.assertEqual(linux.collect(run=run), [])
        self.assertEqual(len(calls), 2)

        def missing(cmd, **kw):
            raise FileNotFoundError("lsblk")

        text = "/dev/sdb1 /run/media/eric/My\\040Stick vfat rw 0 0\n/dev/nvme0n1p1 /boot vfat rw 0 0\ntmpfs /tmp tmpfs rw 0 0\n"
        ds = linux.collect(run=missing, read_text=lambda p: text)
        self.assertEqual({d.mount for d in ds}, {"/run/media/eric/My Stick", "/boot"})
        stick = next(d for d in ds if d.mount.endswith("Stick"))
        self.assertTrue(stick.removable)
        self.assertTrue(next(d for d in ds if d.mount == "/boot").system)

    def test_proc_mounts_octal_unescape(self):
        (e,) = linux.parse_proc_mounts("/dev/sdb1 /media/a\\040b\\011c vfat rw 0 0\n")
        self.assertEqual(e.mount, "/media/a b\tc")


class MacParsing(unittest.TestCase):
    def test_external_fat32(self):
        plist = plistlib.dumps({"MountPoint": "/Volumes/XBOX", "VolumeName": "XBOX", "FilesystemType": "msdos",
                                "FilesystemName": "MS-DOS FAT32", "Internal": False, "RemovableMediaOrExternalDevice": True,
                                "TotalSize": 16 * GB, "FreeSpace": 15 * GB, "DeviceNode": "/dev/disk4s1", "BusProtocol": "USB"})
        d = macos.parse_diskutil_info(plist)
        self.assertEqual((d.fs, d.removable, d.system, d.free, d.bus), ("FAT32", True, False, 15 * GB, "usb"))

    def test_internal_apfs_is_system(self):
        plist = plistlib.dumps({"MountPoint": "/", "VolumeName": "Macintosh HD", "FilesystemType": "apfs", "Internal": True,
                                "RemovableMediaOrExternalDevice": False, "TotalSize": 500 * GB, "FreeSpace": 100 * GB})
        d = macos.parse_diskutil_info(plist)
        self.assertTrue(d.system and not d.removable)

    def test_exfat_and_garbage(self):
        plist = plistlib.dumps({"MountPoint": "/Volumes/E", "FilesystemType": "exfat", "Internal": False, "FreeSpace": 1})
        self.assertEqual(macos.parse_diskutil_info(plist).fs, "EXFAT")
        self.assertIsNone(macos.parse_diskutil_info(b"not a plist"))


class FakeWin32:
    def __init__(self):
        self.vols = {"C:\\": (3, "Windows", "NTFS"), "D:\\": (2, "XBOX", "FAT32"), "E:\\": (2, "", ""), "F:\\": (3, "BIGUSB", "exFAT"), "G:\\": (5, "DVD", "UDF")}

    def logical_drives_mask(self):
        return sum(1 << (ord(k[0]) - 65) for k in self.vols)

    def drive_type(self, root):
        return self.vols[root][0]

    def volume_info(self, root):
        return self.vols[root][1], self.vols[root][2]

    def disk_space(self, root):
        return 10 * GB, 16 * GB


class WindowsDetection(unittest.TestCase):
    def test_default_lists_only_removable_with_media(self):
        ds = windows.collect(FakeWin32(), include_fixed=False, system_drive="C:")
        self.assertEqual([d.mount for d in ds], ["D:\\"])
        self.assertEqual(ds[0].fs, "FAT32")

    def test_include_fixed_marks_system_drive(self):
        ds = {d.mount: d for d in windows.collect(FakeWin32(), include_fixed=True, system_drive="C:")}
        self.assertTrue(ds["C:\\"].system)
        self.assertFalse(ds["F:\\"].system)
        self.assertNotIn("G:\\", ds)  # optical
        self.assertNotIn("E:\\", ds)  # empty card reader


class Assess(unittest.TestCase):
    def d(self, **kw):
        base = dict(mount="/run/media/x/USB", label="USB", fs="FAT32", size=16 * GB, free=8 * GB, removable=True)
        base.update(kw)
        return Drive(**base)

    def levels(self, d, **kw):
        return [f.level for f in assess(d, **kw)]

    def test_good_stick(self):
        self.assertFalse(has_errors(assess(self.d(), needed=500 * 1024**2)))

    def test_non_fat_is_an_error_with_format_hint(self):
        f = assess(self.d(fs="NTFS", size=64 * GB), needed=1)
        self.assertTrue(has_errors(f))
        self.assertIn("FAT32", f[0].text)
        self.assertIn("Rufus", f[0].text)

    def test_fat16_and_unknown_are_warnings(self):
        for fs in ("FAT16", "FAT", ""):
            lv = self.levels(self.d(fs=fs))
            self.assertIn("warn", lv)
            self.assertNotIn("error", lv)

    def test_space_accounts_for_replaced_files(self):
        d = self.d(free=300 * 1024**2)
        self.assertTrue(has_errors(assess(d, needed=400 * 1024**2)))
        self.assertFalse(has_errors(assess(d, needed=400 * 1024**2, replaced=200 * 1024**2)))

    def test_system_drive_blocked(self):
        self.assertTrue(has_errors(assess(self.d(system=True))))

    def test_protected_paths_block(self):
        with TemporaryDirectory() as td:
            d = self.d(mount=td)
            self.assertTrue(has_errors(assess(d, protected=[Path(td) / "app" / "data"])))
            self.assertFalse(has_errors(assess(d, protected=[Path("/definitely/elsewhere")])))

    def test_unmounted(self):
        self.assertTrue(has_errors(assess(Drive("", "X", "FAT32", 1, None, True, mounted=False))))

    def test_not_removable_warns(self):
        self.assertIn("warn", self.levels(self.d(removable=False)))

    def test_normalize_fs(self):
        self.assertEqual(normalize_fs("vfat", "FAT32"), "FAT32")
        self.assertEqual(normalize_fs("vfat", "FAT16"), "FAT16")
        self.assertEqual(normalize_fs("vfat"), "FAT")
        self.assertEqual(normalize_fs("exfat"), "EXFAT")
        self.assertEqual(normalize_fs("ntfs3"), "NTFS")

    def test_system_mount_heuristic(self):
        for m in ("/", "/boot", "/boot/efi", "/home", "/root", "/var/log", "/run/user/1000"):
            self.assertTrue(looks_like_system_mount(m), m)
        for m in ("/run/media/eric/KINGSTON", "/media/usb", "/mnt/games", "/Volumes/XBOX"):
            self.assertFalse(looks_like_system_mount(m), m)

    def test_windows_system_drive_paths(self):
        self.assertTrue(is_windows_system_path("C:\\Users\\me\\Desktop", "C:"))
        self.assertTrue(is_windows_system_path("c:\\", "C:"))
        self.assertFalse(is_windows_system_path("E:\\", "C:"))
        self.assertFalse(is_windows_system_path("/run/media/x", "C:"))
        self.assertTrue(is_windows_system_path("D:\\stuff", "D:"), "the system drive isn't always C:")

    def test_describe_path_uses_containing_drive(self):
        with TemporaryDirectory() as td:
            td = Path(td).resolve()
            sub = td / "sub"
            sub.mkdir()
            known = Drive(str(td), "STICK", "FAT32", 16 * GB, 1, True)
            got = describe_path(sub, [known, Drive("/", "root", "BTRFS", 1, 1, False, system=True)])
            self.assertEqual((got.fs, got.label, got.mount), ("FAT32", "STICK", str(sub)))
            self.assertIsNotNone(got.free)


if __name__ == "__main__":
    unittest.main()
