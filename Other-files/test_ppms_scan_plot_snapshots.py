"""Offline regression tests for PPMS scan plot snapshot saving (T1.2 / A1-2).

Restored from main:Other-files/test_scan_plot_snapshots.py (RoutineTests and
PpmsTests.test_a_scan_saves_one_picture_of_its_plot_box) for standalone
Transport-PPMS. Window-free: tkinter.Tk raises, and screen capture is mocked.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_ppms_run_save_zero as ppms_runs
from test_nonlocal_keithley import bare_app
from test_ppms_motor_debug import app

NOT_SHOWN = "The plot is not visible; cannot capture a snapshot"


def _events(recorder):
    return [json.loads(line) for line in recorder.log_path.read_text(encoding="utf-8").splitlines() if line]


def last_events(recorder_or_events, count=3):
    events = recorder_or_events if isinstance(recorder_or_events, list) else _events(recorder_or_events)
    return [event["event"] for event in events if event["event"] != "note"][-count:]


def snapshot_event(events):
    (event,) = [event for event in events if event["event"] == "plot_snapshots"]
    return event


class Grabs:
    """In place of save_plot_canvas_snapshot (the screen grab): records calls
    in order and writes a small file; widgets in ``hidden`` fail as off-screen."""

    def __init__(self, test, hidden=()):
        self.calls, self.hidden = [], list(hidden)
        patcher = mock.patch.object(
            app.CombinedControlApp,
            "save_plot_canvas_snapshot",
            lambda page, widget, path: self.grab(widget, path),
        )
        patcher.start()
        test.addCleanup(patcher.stop)

    def grab(self, widget, path):
        self.calls.append((widget, Path(path)))
        if any(widget is item for item in self.hidden):
            raise RuntimeError(NOT_SHOWN)
        Path(path).write_bytes(b"PNG")

    def names(self):
        return [path.name for _widget, path in self.calls]


class RoutineTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        for patcher in (
            mock.patch.object(app.tk, "Tk", side_effect=AssertionError("tests must not open a window")),
            mock.patch.object(app.tk, "Toplevel", side_effect=AssertionError("tests must not open a window")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.a = bare_app()
        self.a.closing = False
        self.a.root = mock.Mock()
        self.log = []
        self.a.thread_log = self.log.append
        self.recorder = app.ScanRunRecorder(self.folder, "PAGE", "run", {}, label="Page")
        self.addCleanup(lambda: self.recorder.close("completed"))
        self.grabs = Grabs(self)

    def test_each_plot_is_saved_under_the_runs_name_after_a_redraw(self):
        canvas, box = object(), object()
        order = []
        self.grabs.grab = lambda widget, path, real=self.grabs.grab: (order.append("grab"), real(widget, path))
        note = self.a.save_scan_plot_snapshots(
            self.recorder,
            (("plot", canvas), ("plots", box)),
            redraw=lambda: order.append("redraw"),
        )
        self.assertEqual(note, "")
        self.assertEqual(order, ["redraw", "grab", "grab"])
        self.assertEqual(self.grabs.calls, [
            (canvas, self.folder / "run" / "run_plot_snapshot.png"),
            (box, self.folder / "run" / "run_plots_snapshot.png"),
        ])
        self.assertTrue((self.folder / "run" / "run_plot_snapshot.png").exists())
        self.assertTrue((self.folder / "run" / "run_plots_snapshot.png").exists())
        event = snapshot_event(_events(self.recorder))
        self.assertEqual(
            (event["saved"], event["files"], event["error"]),
            (True, ["run_plot_snapshot.png", "run_plots_snapshot.png"], ""),
        )
        self.assertEqual(self.log, ["Page plot snapshot: run_plot_snapshot.png, run_plots_snapshot.png saved"])

    def test_a_name_in_use_is_never_overwritten(self):
        taken = self.folder / "run" / "run_plot_snapshot.png"
        taken.write_bytes(b"an earlier picture")
        self.a.save_scan_plot_snapshots(self.recorder, (("plot", object()),))
        self.assertEqual(self.grabs.names(), ["run_plot_snapshot_01.png"])
        self.assertEqual(taken.read_bytes(), b"an earlier picture")

    def test_a_plot_that_is_not_shown_is_reported_and_the_others_still_saved(self):
        hidden, shown = object(), object()
        self.grabs.hidden.append(hidden)
        note = self.a.save_scan_plot_snapshots(self.recorder, (("plot", hidden), ("plots", shown)))
        self.assertEqual(note, f"; plot snapshot save failed: plot: {NOT_SHOWN}")
        self.assertEqual(self.grabs.names(), ["run_plot_snapshot.png", "run_plots_snapshot.png"])
        self.assertFalse((self.folder / "run" / "run_plot_snapshot.png").exists())
        event = snapshot_event(_events(self.recorder))
        self.assertEqual(
            (event["saved"], event["files"], event["error"]),
            (False, ["run_plots_snapshot.png"], f"plot: {NOT_SHOWN}"),
        )
        self.assertEqual(self.log, [f"Page plot snapshot: run_plots_snapshot.png saved; save failed: plot: {NOT_SHOWN}"])

    def test_nothing_without_a_point_a_run_folder_a_plot_or_while_closing(self):
        plots = (("plot", object()),)
        closed = app.ScanRunRecorder(self.folder, "PAGE", "closed", {}, label="Page")
        closed.close("completed")
        cases = (
            ("no run folder", dict(recorder=None, plots=plots)),
            ("the run log already closed", dict(recorder=closed, plots=plots)),
            ("no saved point", dict(recorder=self.recorder, plots=plots, points=0)),
            ("the plots not built", dict(recorder=self.recorder, plots=(("plot", None), ("plots", None)))),
        )
        for label, case in cases:
            with self.subTest(label):
                self.assertEqual(self.a.save_scan_plot_snapshots(case.pop("recorder"), case.pop("plots"), **case), "")
        self.a.closing = True
        self.assertEqual(self.a.save_scan_plot_snapshots(self.recorder, plots), "", "the program is closing")
        self.assertEqual(self.grabs.calls, [])
        self.assertNotIn("plot_snapshots", last_events(self.recorder, 10))
        self.assertEqual(self.log, [])

    def test_it_never_raises(self):
        def failing():
            raise RuntimeError("redraw failed")

        self.a.thread_log = mock.Mock(side_effect=RuntimeError("log failed"))
        self.assertEqual(self.a.save_scan_plot_snapshots(self.recorder, (("plot", object()),), redraw=failing), "")
        self.assertEqual(self.grabs.names(), ["run_plot_snapshot.png"], "saved as shown")
        bare = bare_app()
        self.assertEqual(bare.save_scan_plot_snapshots(self.recorder, (("plot", object()),)), "")
        self.assertEqual(self.a.save_scan_plot_snapshots(self.recorder, None), "")


class PpmsTests(unittest.TestCase):
    def test_a_scan_saves_one_picture_of_its_plot_box(self):
        grabs = Grabs(self)
        page = ppms_runs.Page(self)
        box = page.panel.__dict__["ppms_plot_frame"]
        page.start()
        folder = page.panel.ppms_run_folder
        self.assertEqual(grabs.calls, [(box, folder / "run1_plots_snapshot.png")])
        events = page.events()
        self.assertEqual(last_events(events), ["outputs_zeroed", "plot_snapshots", "run_finished"])
        self.assertEqual(
            (snapshot_event(events)["saved"], snapshot_event(events)["files"]),
            (True, ["run1_plots_snapshot.png"]),
        )
        self.assertEqual(page.panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed")
        self.assertEqual(page.dialogs, [])
        self.assertTrue(page.panel.ppms_macro_scan_result, "a Macro goes on as before")


if __name__ == "__main__":
    unittest.main()
