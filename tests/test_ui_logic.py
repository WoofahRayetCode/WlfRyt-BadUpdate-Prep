"""UI logic that must work without Tk: settings, markdown parsing, the worker, and wizard state rules."""
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep import settings as S
from badupdateprep.drives import Drive
from badupdateprep.options import AutoLaunch, Options
from badupdateprep.pipeline import CopyReport, Env, Failed
from badupdateprep.progress import Cancelled
from badupdateprep.ui import markdown as md
from badupdateprep.ui.state import CHOOSE, CONSOLE, PREPARE, USB, WELCOME, AppState, usb_candidates
from badupdateprep.ui.worker import Done, Worker
from badupdateprep.wiki import PAGES, load_page


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_unknown_keys_preserved(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            s = S.Settings(options=Options(payload="freemyxe", nand_tool=True, autolaunch=AutoLaunch("BUT_X", "nand")), last_usb="/media/x", last_usb_label="X")
            s.extra = {"future_flag": 7}
            self.assertTrue(S.save(s, p))
            raw = json.loads(p.read_text())
            self.assertEqual(raw["future_flag"], 7)
            back = S.load(p)
            self.assertEqual(back.options.payload, "freemyxe")
            self.assertEqual(back.options.autolaunch, AutoLaunch("BUT_X", "nand"))
            self.assertEqual((back.last_usb, back.last_usb_label), ("/media/x", "X"))
            self.assertEqual(back.extra, {"future_flag": 7})

    def test_missing_file_gives_defaults(self):
        with TemporaryDirectory() as td:
            self.assertEqual(S.load(Path(td) / "nope.json").options, Options())

    def test_corrupt_file_is_set_aside_not_raised(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            p.write_text("{ not json")
            got = S.load(p)
            self.assertEqual(got.options, Options())
            self.assertTrue(Path(str(p) + ".bak").exists())
            self.assertFalse(p.exists())

    def test_bad_values_fall_back(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            p.write_text(json.dumps({"options": {"entry": "wat", "payload": 5, "channel": "nope", "autolaunch": "x", "bogus": 1}}))
            o = S.load(p).options
            self.assertEqual((o.entry, o.payload, o.channel.value, o.autolaunch), ("rbb", "xeunshackle", "pinned", None))

    def test_save_failure_returns_false(self):
        with TemporaryDirectory() as td:
            blocker = Path(td) / "file"
            blocker.write_text("x")
            self.assertFalse(S.save(S.Settings(), blocker / "sub" / "s.json"))


class MarkdownTests(unittest.TestCase):
    def test_inline(self):
        spans = md.parse_inline("a **bold** and `code` and *it* and [link](http://x) end")
        self.assertEqual([(s.text, s.style) for s in spans], [
            ("a ", ""), ("bold", "b"), (" and ", ""), ("code", "code"), (" and ", ""), ("it", "i"), (" and ", ""), ("link", "link"), (" end", ""),
        ])
        self.assertEqual(spans[7].href, "http://x")

    def test_blocks(self):
        blocks = md.parse("# T\n\nPara with **b**.\n\n- one\n- two\n  - nested\n1. first\n\n    code line\n    more\n\nWARNING: careful\n")
        kinds = [b.kind for b in blocks]
        self.assertEqual(kinds, ["h1", "blank", "p", "blank", "li", "li", "li", "li", "blank", "code", "blank", "warn"])
        self.assertEqual(blocks[6].level, 1)
        self.assertEqual(blocks[7].marker, "1.")
        self.assertEqual(blocks[9].text, "code line\nmore")

    def test_table(self):
        (t,) = md.parse("| Name | Meaning |\n| --- | --- |\n| **A** | one |\n| B | two |\n")
        self.assertEqual(t.kind, "table")
        self.assertTrue(t.header)
        self.assertEqual(len(t.rows), 3)
        self.assertEqual(t.rows[1][0][0].style, "b")

    def test_fenced_code(self):
        (c,) = md.parse("```\nx = 1\n\n  y\n```\n")
        self.assertEqual((c.kind, c.text), ("code", "x = 1\n\n  y"))

    def test_no_literal_markup_survives_in_any_wiki_page(self):
        # Regression: the old renderer printed literal "**bold**" in every page.
        for filename, title in PAGES:
            text = md.plain_text(md.parse(load_page(filename)))
            self.assertNotIn("**", text, title)
            self.assertNotIn("`", text, title)
            self.assertTrue(text.strip(), title)

    def test_led_page_has_a_table_and_code_blocks(self):
        kinds = {b.kind for b in md.parse(load_page("led_and_errors.md"))}
        self.assertIn("code", kinds)


class WorkerTests(unittest.TestCase):
    def make(self):
        events, sched = [], []
        return Worker(lambda ms, fn: sched.append(fn), events.append), events, sched

    def test_events_delivered_in_order_on_the_draining_thread(self):
        w, events, sched = self.make()
        main = threading.get_ident()
        threads = []
        w.on_thread = None

        def fn(emit, cancel):
            threads.append(threading.get_ident())
            for i in range(3):
                emit(i)
            return "result"

        w.start(fn)
        w.join(5)
        w.drain()
        self.assertEqual(events, [0, 1, 2, Done("result")])
        self.assertNotEqual(threads[0], main)
        self.assertTrue(sched, "a poll must have been scheduled")

    def test_exception_becomes_failed_event_with_the_exception(self):
        w, events, _ = self.make()

        def fn(emit, cancel):
            raise RuntimeError("HTTP 503")

        w.start(fn)
        w.join(5)
        w.drain()
        (ev,) = events
        self.assertIsInstance(ev, Failed)
        self.assertEqual(str(ev.error), "HTTP 503")  # the old lambda-over-`exc` pattern lost this message

    def test_cancel_reaches_the_task(self):
        w, events, _ = self.make()
        started = threading.Event()

        def fn(emit, cancel):
            started.set()
            while True:
                cancel.check()
                time.sleep(0.005)

        w.start(fn)
        started.wait(5)
        self.assertTrue(w.running)
        w.cancel()
        w.join(5)
        w.drain()
        self.assertIsInstance(events[-1], Failed)
        self.assertIsInstance(events[-1].error, Cancelled)
        self.assertFalse(w.running)

    def test_cannot_start_twice(self):
        w, _, _ = self.make()
        gate = threading.Event()
        w.start(lambda emit, cancel: gate.wait(5))
        with self.assertRaises(RuntimeError):
            w.start(lambda emit, cancel: None)
        gate.set()
        w.join(5)

    def test_poll_reschedules_until_finished(self):
        events, sched = [], []
        w = Worker(lambda ms, fn: sched.append(fn), events.append)
        gate = threading.Event()
        w.start(lambda emit, cancel: gate.wait(5))
        sched.pop()()  # poll while running -> reschedules
        self.assertEqual(len(sched), 1)
        gate.set()
        w.join(5)
        sched.pop()()
        self.assertEqual(sched, [])
        self.assertEqual(events, [Done(True)])


class StateTests(unittest.TestCase):
    def state(self, td):
        return AppState.load(Env(app_dir=Path(td)))

    def test_navigation_rules(self):
        with TemporaryDirectory() as td:
            st = self.state(td)
            self.assertFalse(st.can_continue(WELCOME)[0])
            st.ack = True
            self.assertTrue(st.can_continue(WELCOME)[0])
            self.assertTrue(st.can_continue(CHOOSE)[0])
            st.options.entry, st.options.payload = "avatar", "stock"
            ok, why = st.can_continue(CHOOSE)
            self.assertFalse(ok)
            self.assertIn("default.xex", why)
            st.options.payload = "xeunshackle"
            self.assertTrue(st.can_continue(CHOOSE)[0])
            self.assertFalse(st.can_continue(PREPARE)[0])
            self.assertFalse(st.can_continue(USB)[0])
            self.assertTrue(st.can_continue(CONSOLE)[0])
            st.busy = True
            self.assertFalse(st.can_continue(WELCOME)[0])

    def test_prepared_result_is_invalidated_by_option_changes(self):
        with TemporaryDirectory() as td:
            st = self.state(td)
            st.set_prepared(object())  # type: ignore[arg-type]
            self.assertTrue(st.prepare_is_current())
            st.options.nand_tool = True
            self.assertFalse(st.prepare_is_current(), "changing Choose after Prepare must force a re-prepare")
            st.options.nand_tool = False
            self.assertTrue(st.prepare_is_current())

    def test_new_build_clears_old_copy_report(self):
        with TemporaryDirectory() as td:
            st = self.state(td)
            st.copy_report = CopyReport(Path("/x"), 1, 1, 0, 0, 1, [], {})
            st.set_prepared(object())  # type: ignore[arg-type]
            self.assertIsNone(st.copy_report)

    def test_usb_step_needs_a_clean_verified_copy(self):
        with TemporaryDirectory() as td:
            st = self.state(td)
            st.copy_report = CopyReport(Path("/x"), 3, 3, 0, 0, 3, [("a", "differs")], {})
            self.assertFalse(st.can_continue(USB)[0])
            st.copy_report = CopyReport(Path("/x"), 3, 3, 0, 0, 3, [], {})
            self.assertTrue(st.can_continue(USB)[0])

    def test_goto_only_visited_steps(self):
        with TemporaryDirectory() as td:
            st = self.state(td)
            st.reached(2)
            self.assertTrue(st.can_goto(1))
            self.assertTrue(st.can_goto(2))
            self.assertFalse(st.can_goto(3))
            st.busy = True
            self.assertFalse(st.can_goto(1))

    def test_usb_preselection(self):
        a = Drive("/run/media/a", "A", "FAT32", 1, 1, True)
        b = Drive("/run/media/b", "B", "FAT32", 1, 1, True)
        sys_ = Drive("/boot", "", "FAT32", 1, 1, False, system=True)
        self.assertEqual(usb_candidates([a, b], "/run/media/b", "B"), 1)
        self.assertIsNone(usb_candidates([a, b], "/run/media/b", "OTHER"), "same mount, different label = different stick")
        self.assertIsNone(usb_candidates([a, b], "", ""), "two candidates: never guess")
        self.assertEqual(usb_candidates([sys_, a], "", ""), 1, "a lone non-system stick is preselected")
        self.assertIsNone(usb_candidates([], "/x", "X"))


if __name__ == "__main__":
    unittest.main()
