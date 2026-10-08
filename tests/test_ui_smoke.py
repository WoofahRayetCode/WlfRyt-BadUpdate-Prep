"""Drive the real Tk wizard end to end (withdrawn window, local server). Skipped without Tk or a display."""
import os
import time
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests.test_pipeline import Base

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    tk = None


def _display_ok() -> bool:
    if tk is None:
        return False
    if os.name != "nt" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


HAVE_UI = _display_ok()


def pump(app, until=None, seconds=0.0, timeout=30):
    end = time.time() + (timeout if until else seconds)
    while time.time() < end:
        app.update()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until()) if until else True


@unittest.skipUnless(HAVE_UI, "needs Tk and a display")
class WizardSmoke(Base):
    def setUp(self):
        super().setUp()
        self.errors: list[str] = []
        from badupdateprep.ui.app import Wizard

        self.app = Wizard(self.env())
        self.app.withdraw()
        self.app.report_callback_exception = lambda e, v, tb: self.errors.append("".join(traceback.format_exception(e, v, tb)))

    def tearDown(self):
        try:
            self.app.model.busy = False
            self.app._on_close()
        except Exception:
            pass
        super().tearDown()
        self.assertEqual(self.errors, [], "Tk callbacks raised")

    def test_full_walkthrough(self):
        app = self.app
        # Welcome: Continue is disabled until the safety box is ticked
        self.assertIn("disabled", app.next_btn.state())
        app.pages[0].ack.set(True)
        pump(app, seconds=0.05)
        self.assertNotIn("disabled", app.next_btn.state())
        app.next_btn.invoke()
        self.assertEqual(app.current_step, 1)

        # Choose: contradictory combinations are corrected, not nagged about
        ch = app.pages[1]
        ch.mode.set("custom")
        ch._mode_changed()
        ch.v_entry.set("avatar")
        ch.v_payload.set("stock")
        ch._changed()
        self.assertEqual((app.model.options.entry, app.model.options.payload), ("avatar", "xeunshackle"))
        ch.mode.set("recommended")
        ch._mode_changed()
        self.assertEqual(app.model.options.entry, "rbb")

        # Prepare: starts by itself, finishes, enables Continue
        app.next_btn.invoke()
        self.assertEqual(app.current_step, 2)
        self.assertTrue(pump(app, until=app.model.prepare_is_current), "prepare should finish")
        self.assertNotIn("disabled", app.next_btn.state())
        rows = {i: app.pages[2].tree.set(i, "check") for i in app.pages[2].tree.get_children()}
        self.assertTrue(all("verified" in v for v in rows.values()), rows)

        # Changing options afterwards invalidates the prepared build and blocks jumping ahead
        app.goto(1)
        app.model.options.nand_tool = True
        self.assertFalse(app.model.prepare_is_current())
        self.assertFalse(app._may_goto(3))
        app.model.options.nand_tool = False
        app.goto(2)

        # USB: pretend a plain folder is a FAT32 stick, copy, verify
        usb = self.td / "STICK"
        usb.mkdir()
        (usb / "BadUpdatePayload").mkdir()
        (usb / "BadUpdatePayload" / "MACBackup.bin").write_bytes(b"mac")
        app.next_btn.invoke()
        self.assertEqual(app.current_step, 3)
        page = app.pages[3]
        d = replace(self._describe(usb), fs="FAT32", removable=True, system=False, device="folder")
        page._rows, page._selected, page._last_sig = [d], d, None
        page._set_drives([])
        page.tree.selection_set("0")
        pump(app, seconds=0.05)
        self.assertNotIn("disabled", page.btn_copy.state())
        with mock.patch("badupdateprep.ui.pages.usb.messagebox.askokcancel", return_value=True) as ask:
            page.btn_copy.invoke()
        shown = ask.call_args[0][1]
        self.assertIn("will be written", shown)
        self.assertIn("left alone", shown)
        self.assertIn("Nothing is formatted", shown)
        self.assertTrue(pump(app, until=lambda: app.model.copy_report is not None), "copy should finish")
        self.assertTrue(app.model.copy_report.ok)
        self.assertEqual((usb / "BadUpdatePayload" / "default.xex").read_bytes(), b"XEUNSHACKLE")
        self.assertEqual((usb / "BadUpdatePayload" / "MACBackup.bin").read_bytes(), b"mac")

        # Console: checklist, and Continue becomes Close
        app.next_btn.invoke()
        self.assertEqual(app.current_step, 4)
        self.assertEqual(app.next_btn.cget("text"), "Close")
        self.assertGreaterEqual(len(app.pages[4].scroll.inner.winfo_children()), 8)

    def test_switching_to_tested_versions_is_reflected_on_the_choose_page(self):
        # Reviewer finding: "Switch to tested versions and retry" changed the option but not the Choose radio, so the next
        # unrelated edit silently flipped the channel back to Latest and saved it.
        from badupdateprep.catalog import Channel

        app = self.app
        app.pages[0].ack.set(True)
        app.next_btn.invoke()
        ch = app.pages[1]
        ch.mode.set("custom")
        ch._mode_changed()
        ch.v_channel.set("latest")
        ch._changed()
        self.assertEqual(app.model.options.channel, Channel.LATEST)
        app.model.options.channel = Channel.PINNED  # what PreparePage._use_pinned does
        app.goto(1, force=True)
        self.assertEqual(ch.v_channel.get(), "pinned", "Choose must re-read the options when it is shown again")
        ch.v_nand.set(True)
        ch._changed()
        self.assertEqual(app.model.options.channel, Channel.PINNED, "an unrelated edit must not flip the channel back")

    def _describe(self, path: Path):
        from badupdateprep.drives import describe_path

        return describe_path(path, [])

    def test_failed_download_shows_a_readable_error_and_can_retry(self):
        app = self.app
        app.pages[0].ack.set(True)
        app.next_btn.invoke()
        self.srv.corrupt = True
        app.next_btn.invoke()
        page = app.pages[2]
        self.assertTrue(pump(app, until=lambda: page.result.cget("text").startswith("✖")))
        text = page.result.cget("text")
        self.assertIn("corrupt", text)
        self.assertNotIn(".part", text)
        self.assertIn("disabled", app.next_btn.state())
        self.srv.corrupt = False
        page.btn_prepare.invoke()
        self.assertTrue(pump(app, until=app.model.prepare_is_current))

    def test_cancel_during_download(self):
        app = self.app
        # incompressible data below, so the transfer is slow enough to cancel
        from tests import fixtures as fx

        fx.make_zip(self.zips["rbb_trial"], {"Content/0000000000000000/5841122D/000D0000/DD774F20C36263F2": os.urandom(1_500_000)})
        self.cat = self._rebuild_catalog()
        self.srv.chunk_delay = 0.08
        app.model.env.catalog = self.cat
        app.pages[0].ack.set(True)
        app.next_btn.invoke()
        app.next_btn.invoke()
        page = app.pages[2]
        self.assertTrue(pump(app, until=lambda: any("MB" in page.tree.set(i, "detail") or "KB" in page.tree.set(i, "detail") for i in page.tree.get_children())))
        page.btn_cancel.invoke()
        self.assertTrue(pump(app, until=lambda: not app.model.busy))
        self.assertEqual(page.stage.cget("text"), "Cancelled")
        self.assertFalse(app.model.prepare_is_current())

    def _rebuild_catalog(self):
        from tests.test_pipeline import local_catalog

        return local_catalog(self.srv, self.zips)


if __name__ == "__main__":
    unittest.main()
