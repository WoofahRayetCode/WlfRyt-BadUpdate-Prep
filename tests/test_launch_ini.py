import unittest

from badupdateprep.launch_ini import IniDoc, IniEdit, unified_diff, usb_path
from tests.fixtures import LAUNCH_INI

RAW = LAUNCH_INI.encode("latin-1")


class RoundTrip(unittest.TestCase):
    def test_noop_is_byte_identical(self):
        self.assertEqual(IniDoc.parse(RAW).to_bytes(), RAW)
        self.assertEqual(IniDoc.parse(RAW).apply([]).to_bytes(), RAW)

    def test_crlf_bom_and_no_trailing_newline_survive(self):
        for raw in (
            RAW.replace(b"\n", b"\r\n"),
            b"\xef\xbb\xbf" + RAW,
            RAW.rstrip(b"\n"),
            b"",
            b"[Paths]",
            b"a\rb\r",
        ):
            self.assertEqual(IniDoc.parse(raw).to_bytes(), raw, raw[:30])

    def test_non_ascii_bytes_survive(self):
        raw = b"[Paths]\n; caf\xe9 \x85 \xff\nBUT_A = \n"
        self.assertEqual(IniDoc.parse(raw).to_bytes(), raw)
        self.assertEqual(IniDoc.parse(raw).apply([IniEdit("Paths", "BUT_A", "x")]).to_bytes(), b"[Paths]\n; caf\xe9 \x85 \xff\nBUT_A = x\n")


class Edits(unittest.TestCase):
    def apply(self, *edits, raw=RAW):
        return IniDoc.parse(raw).apply(list(edits)).to_bytes().decode("latin-1")

    def test_replaces_only_the_target_line(self):
        out = self.apply(IniEdit("Paths", "BUT_X", r"Usb:\Apps\XexMenu\default.xex"))
        old, new = LAUNCH_INI.splitlines(), out.splitlines()
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(old), len(new))
        self.assertEqual(changed, [(old[[i for i, l in enumerate(old) if l.startswith("BUT_X")][0]], r"BUT_X = Usb:\Apps\XexMenu\default.xex")])

    def test_backslashes_are_not_treated_as_regex_escapes(self):
        out = self.apply(IniEdit("Paths", "BUT_A", r"Usb:\1\g<0>\n\x"))
        self.assertIn(r"BUT_A = Usb:\1\g<0>\n\x", out)

    def test_inline_comment_and_spacing_preserved(self):
        out = self.apply(IniEdit("Paths", "BUT_Y", r"Usb:\Apps\Tool\default.xex"))
        self.assertIn(r"BUT_Y = Usb:\Apps\Tool\default.xex ; system dash", out)

    def test_key_match_is_case_insensitive_and_section_scoped(self):
        raw = b"[A]\nkey = 1\n[B]\nKEY = 2\n"
        out = self.apply(IniEdit("b", "Key", "9"), raw=raw)
        self.assertEqual(out, "[A]\nkey = 1\n[B]\nKEY = 9\n")

    def test_duplicate_keys_all_updated(self):
        out = self.apply(IniEdit("S", "k", "v"), raw=b"[S]\nk = 1\nk = 2\n")
        self.assertEqual(out, "[S]\nk = v\nk = v\n")

    def test_insert_after_commented_template(self):
        raw = b"[Paths]\n; BUT_Z = Usb:\\example.xex\nOther = 1\n"
        out = self.apply(IniEdit("Paths", "BUT_Z", "v"), raw=raw)
        self.assertEqual(out, "[Paths]\n; BUT_Z = Usb:\\example.xex\nBUT_Z = v\nOther = 1\n")

    def test_append_to_section_end_before_next_section(self):
        raw = b"[Paths]\nA = 1\n\n\n[Settings]\nx = y\n"
        out = self.apply(IniEdit("Paths", "New", "v"), raw=raw)
        self.assertEqual(out, "[Paths]\nA = 1\nNew = v\n\n\n[Settings]\nx = y\n")

    def test_append_to_last_section_without_trailing_newline(self):
        out = self.apply(IniEdit("Paths", "New", "v"), raw=b"[Paths]\nA = 1")
        self.assertEqual(out, "[Paths]\nA = 1\nNew = v")

    def test_creates_missing_section(self):
        out = self.apply(IniEdit("Paths", "BUT_A", "v"), raw=b"[Settings]\nx = y\n")
        self.assertEqual(out, "[Settings]\nx = y\n\n[Paths]\nBUT_A = v\n")
        self.assertEqual(self.apply(IniEdit("Paths", "A", "v"), raw=b""), "[Paths]\nA = v\n")

    def test_crlf_edits_use_crlf(self):
        out = self.apply(IniEdit("Paths", "New", "v"), raw=b"[Paths]\r\nA = 1\r\n")
        self.assertEqual(out, "[Paths]\r\nA = 1\r\nNew = v\r\n")

    def test_idempotent(self):
        e = IniEdit("Paths", "BUT_X", r"Usb:\a\b.xex")
        once = IniDoc.parse(RAW).apply([e]).to_bytes()
        twice = IniDoc.parse(once).apply([e]).to_bytes()
        self.assertEqual(once, twice)

    def test_bom_on_first_section_header(self):
        raw = b"\xef\xbb\xbf[Paths]\nA = 1\n"
        out = IniDoc.parse(raw).apply([IniEdit("Paths", "A", "2")]).to_bytes()
        self.assertEqual(out, b"\xef\xbb\xbf[Paths]\nA = 2\n")


class Inspect(unittest.TestCase):
    def test_slots_have_hints(self):
        doc = IniDoc.parse(RAW)
        slots = {s.key: s for s in doc.slots("Paths")}
        self.assertIn("BUT_X", slots)
        self.assertIn("X button: run an application from the USB stick", slots["BUT_X"].hint)
        self.assertEqual(slots["BUT_Y"].value, "Sfc:\\dash.xex")
        self.assertEqual(slots["BUT_A"].value, "")

    def test_device_prefix_inferred_from_plugins(self):
        self.assertEqual(IniDoc.parse(RAW).device_prefix(), "Usb:\\")
        self.assertEqual(IniDoc.parse(b"[Plugins]\nplugin1 = Hdd:\\x.xex\n").device_prefix(), "Usb:\\")
        self.assertEqual(IniDoc.parse(b"[Plugins]\np = USB0:\\x.xex\n").device_prefix(), "USB0:\\")

    def test_usb_path(self):
        self.assertEqual(usb_path("Usb:\\", "Apps/XexMenu/default.xex"), "Usb:\\Apps\\XexMenu\\default.xex")
        self.assertEqual(usb_path("Usb:", "a/b"), "Usb:\\a\\b")

    def test_diff_shows_only_the_change(self):
        new = IniDoc.parse(RAW).apply([IniEdit("Paths", "BUT_A", "v")]).to_bytes()
        d = unified_diff(RAW, new)
        self.assertIn("-BUT_A =\n", d)
        self.assertIn("+BUT_A = v\n", d)
        self.assertEqual(unified_diff(RAW, RAW), "")


if __name__ == "__main__":
    unittest.main()
