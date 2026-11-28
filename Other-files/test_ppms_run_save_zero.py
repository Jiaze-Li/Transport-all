"""PPMS page: one run folder and log per scan, and the 6221 zeroed at every
end (user, 2026-10-18), with the PPMS review findings of that day (P1-P9).

No Tk window and no instrument: the real PPMSPanel is built on stand-in
widgets (test_ppms_sr830_tc), its rack holds the fake 6221 and SR830s of
test_ppms_frequency_scan, every dialog is mocked, and a fake clock moves only
when the scan waits.  The real Start, worker, zeroing and finish run.
"""
import csv
import json
import math
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import transport.ppms.engine.motor as motor_mod
import transport.ppms.engine.runner as runner_mod
import transport.ppms.ui.panel as panel_mod
from transport.ppms.controllers.environment import PPMS_QDINSTRUMENT_BACKEND
from transport.ppms.controllers.sr830 import NONLOCAL_SR830_FULL_SCALE_V, STFMR_LOCKIN_SR830_TIME_CONSTANTS
from transport.ppms.engine.planning import parse_ppms_macro
from transport.ppms.engine.errors import PPMS_FIELD_NOT_ZEROED_TEXT, PPMSFieldScanError
from transport.ppms.engine.macro import MacroRunner
from transport.ppms.engine.runner import PPMSScanRunner, ScanRequest, ppms_field_is_ready
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMSPanelView
from test_ppms_two_panels import Var
from test_ppms_frequency_scan import ClockEvent, Motor, Rig
from test_ppms_sr830_tc import build_ppms_panel


class Page:
    """A built PPMS page on the fake rack: SR830-1 and SR830-3 measured, no
    Zurich, the rotator at 26°, 300 K and 1234 Oe read back."""

    def __init__(self, test, mode="Current", main_row=("0.5", "1", "0.5")):
        self.test = test
        self.rig = rig = Rig(test, mode, None, (0.5, 1.0), zurich=False)
        panel, _ttk = build_ppms_panel()
        self.lines = []
        panel.shell.append_log = self.lines.append
        panel.state.ppms_instrument_controllers = rig.page.state.ppms_instrument_controllers
        panel.state.sr830_range_states = rig.page.state.sr830_range_states
        panel.runner._autorange_sr830 = rig.page.runner._autorange_sr830
        panel.runner._read_live_override = rig.page.runner._read_live_override
        panel._read_ppms_live = rig.page.runner.read_live
        panel._apply_ppms_environment_readback = rig.page._apply_ppms_environment_readback
        rig.page.state.ppms_environment_controller.backend = PPMS_QDINSTRUMENT_BACKEND
        rig.page.state.ppms_environment_controller.connected = True
        panel.state.ppms_environment_controller = rig.page.state.ppms_environment_controller
        panel.state.scan_stop_event = rig.stop
        panel._require_ppms_control_ready = mock.Mock()
        panel.ppms_lockin_selection["zurich"].set(False)
        panel.ppms_lockin_selection["830_b"].set(False)
        panel.ppms_motor_confirm_var.set(True)
        panel.ppms_motor_target_var.set("26")
        panel.ppms_temperature_target_var.set("300")
        panel.ppms_field_target_var.set("50000")    # not used: the field is not commanded
        panel.ppms_transport_frequency_var.set("317.3")
        panel.ppms_acquisition_time_var.set("1")
        panel.state.last_readback = dict(temperature_k=300.0, field_oe=1234.0, angle_deg=26.0)
        panel.ppms_simple_mode.set(mode)
        for var, value in zip(panel.ppms_simple_range, main_row):
            var.set(value)
        self.dialogs, self.later = [], []
        panel.root = types.SimpleNamespace(after=lambda _ms, show: (self.later.append(show), show()))
        panel.shell.root = panel.root
        self.panel, self.folder = panel, tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)

    def start(self, name="run1", answer=True, folder=True):
        import transport.ppms.ui.dialogs as dialogs_mod

        def error(title, text, **_options):
            self.dialogs.append((title, text))
        with mock.patch.object(runner_mod.time, "monotonic", self.rig.clock), \
                mock.patch.object(runner_mod.time, "sleep", lambda _s: None), \
                mock.patch.object(runner_mod, "PPMSMotorDebug", Motor), \
                mock.patch.object(panel_mod.messagebox, "askyesno", return_value=answer), \
                mock.patch.object(panel_mod, "ask_scan_start", return_value=answer) as ask, \
                mock.patch.object(panel_mod.messagebox, "showerror", side_effect=error), \
                mock.patch.object(panel_mod.filedialog, "askdirectory",
                                  return_value=self.folder.name if folder else "") as directory, \
                mock.patch.object(dialogs_mod.simpledialog, "askstring", return_value=name):
            self.panel.start_ppms_field_scan()
        self.ask, self.directory = ask, directory
        return self

    def events(self, path=None):
        path = path or self.panel.state.run_folder / f"{self.panel.state.run_folder.name}.log"
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]

    def rows(self):
        path = self.panel.state.run_folder / f"{self.panel.state.run_folder.name}.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def header(self):
        path = self.panel.state.run_folder / f"{self.panel.state.run_folder.name}.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return next(csv.reader(handle))

    def sent_6221(self):
        return [entry for entry in self.rig.log if entry[0] in ("6221", "6221?")]


ZERO = [("6221", "SOUR:WAVE:ABOR"), ("6221", "OUTP OFF"), ("6221?", "OUTP?")]


class RunFolderAndZeroTests(unittest.TestCase):
    def test_a_completed_scan_saves_in_its_run_folder_and_zeroes_the_6221(self):
        page = Page(self).start()
        panel = page.panel
        self.assertFalse(panel.state.scan_running)
        self.assertEqual(panel.state.run_folder, Path(page.folder.name) / "run1")
        self.assertEqual(sorted(p.name for p in panel.state.run_folder.iterdir()), ["run1.csv", "run1.log"])
        rows = page.rows()
        self.assertEqual([float(r["6221_current_rms_ma"]) for r in rows], [0.5, 1.0])
        # The 6221 zeroed and read back last, after the last point.
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(page.rig.source.output, 0)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed")
        self.assertEqual(panel.ppms_transport_status_var.get(), "Transport acquisition: 6221 output OFF (read back)")
        self.assertEqual(page.dialogs, [])
        self.assertTrue(panel.macro_runner.scan_result)
        # The temperature was held, the field never commanded.
        self.assertFalse(any(entry[:2] == ("ppms", "set_field") for entry in page.rig.log))
        events = page.events()
        names = [e["event"] for e in events if e["event"] != "note"]
        # plot_snapshots: the end's picture of the plots, before the log is
        # closed (user, 2026-11-13); this page has no screen, so the entry
        # says why none was saved.
        self.assertEqual(names, ["run_prepared", "scan_started", "harmonic_pass", "point_saved", "point_saved",
                                 "outputs_zeroed", "plot_snapshots", "run_finished"])
        prepared, finished = events[0], events[-1]
        self.assertEqual(prepared["settings"]["mode"], "Current")
        self.assertEqual(prepared["file_name"], "run1")
        self.assertFalse(prepared["settings"]["field"]["commanded"])
        self.assertEqual(finished["outcome"], "completed")
        self.assertEqual(finished["points_saved"], 2)
        self.assertEqual(finished["outputs"], {"6221": "zeroed"})
        self.assertEqual(finished["status"], "Completed; 6221 zeroed")
        saved = [e for e in events if e["event"] == "point_saved"]
        self.assertEqual([(e["x_column"], e["x"]) for e in saved], [("6221_current_rms_ma", 0.5),
                                                                     ("6221_current_rms_ma", 1.0)])
        self.assertEqual(next(e for e in events if e["event"] == "outputs_zeroed")["6221"], "zeroed")
        # The page's log lines while the scan ran are notes.
        notes = [e["text"] for e in events if e["event"] == "note"]
        self.assertIn("PPMS scan end: 6221 zeroed", notes)
        self.assertTrue(any(text.startswith("Scan temperature segment: 300 K") for text in notes))
        self.assertIsNone(panel.state.run_recorder, "closed and released")
        # The Yes/No says how every end leaves the outputs.
        question = page.ask.call_args.args[1]
        self.assertIn("At every end (completed, stopped or failed) the 6221 is switched off and read back as off.",
                      question)
        self.assertIn("the field is not commanded (the CSV's target_field_oe is the field read now, 1234 Oe)",
                      question)
        self.assertNotIn("kept", question)

    def test_the_columns_are_the_live_csvs_of_before(self):
        """The run folder's CSV has the columns, in order, that the page's own
        live CSV had (the worker on its old path)."""
        page = Page(self).start()
        old = Rig(self, "Current", None, (0.5, 1.0), zurich=False)
        old.run()
        self.assertEqual(page.header(), old.header())
        self.assertEqual([r["target_field_oe"] for r in page.rows()], ["1234.0", "1234.0"])

    def test_the_stop_button_ends_with_the_6221_zeroed(self):
        page = Page(self)
        rig = page.rig
        # Stop during the second current's settle (the wait of 1 s, the TC).

        def on_wait(seconds):
            if len(page.panel.state.scan_rows) == 1 and math.isclose(seconds, 1.0):
                page.panel.stop_ppms_field_scan()
        rig.stop.on_wait = on_wait
        page.start()
        panel = page.panel
        self.assertEqual(len(page.rows()), 1)
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(rig.source.output, 0)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed")
        self.assertEqual(page.dialogs, [])
        self.assertFalse(panel.macro_runner.scan_result)
        events = page.events()
        names = [e["event"] for e in events if e["event"] != "note"]
        self.assertEqual(names[-5:], ["point_saved", "stop_requested", "outputs_zeroed", "plot_snapshots",
                                      "run_finished"])
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertTrue(events[-1]["stop_reason"].startswith("Stop"))
        self.assertEqual(next(e for e in events if e["event"] == "stop_requested")["button"], "Stop")

    def test_i_off_ends_with_the_6221_zeroed_and_logged(self):
        page = Page(self)

        def on_wait(seconds):
            if page.panel.state.scan_rows and not page.panel.state.scan_stop_event.is_set():
                page.panel.ppms_sources_off()
        page.rig.stop.on_wait = on_wait
        page.start()
        self.assertEqual(page.panel.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed")
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        events = page.events()
        self.assertEqual(next(e for e in events if e["event"] == "stop_requested")["button"], "I off")
        self.assertTrue(events[-1]["stop_reason"].startswith("I off"))

    def test_a_failure_ends_with_the_6221_zeroed_and_its_dialog(self):
        page = Page(self)
        page.rig.sr830["830_a"].never_locks_at = 317.3
        page.start()
        panel = page.panel
        self.assertEqual(page.rig.source.output, 0)
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        status = panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.startswith("Failed: Connected SR830 failed to lock"), status)
        self.assertEqual(len(page.dialogs), 1)
        title, text = page.dialogs[0]
        self.assertEqual(title, "PPMS Current scan")
        self.assertTrue(text.endswith("\n\nAt the end: 6221 zeroed"), text)
        self.assertEqual(len(page.later), 1, "opened from the Tk event loop")
        events = page.events()
        self.assertEqual([e["event"] for e in events if e["event"] != "note"][-2:], ["outputs_zeroed", "run_finished"])
        self.assertEqual(events[-1]["outcome"], "error")
        self.assertIn("failed to lock", events[-1]["error"])
        self.assertEqual(events[-1]["points_saved"], 0)

    def test_a_6221_that_does_not_read_back_off_is_checked(self):
        page = Page(self)
        source = page.rig.source

        def stuck(command):
            if command == "OUTP OFF" and len(page.panel.state.scan_rows) == 2:
                source.output = 1
        source.on_write = stuck
        page.start()
        panel = page.panel
        self.assertEqual(panel.state.scan_outputs,
                         {"6221": "zero FAILED: the 6221 output did not read back OFF (OUTP? 1)"})
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; 6221 zero FAILED: check it")
        self.assertEqual(page.dialogs[0][0], "PPMS outputs")
        self.assertIn("The scan completed, but the 6221 was not confirmed at zero: CHECK IT.", page.dialogs[0][1])
        self.assertFalse(panel.macro_runner.scan_result, "a Macro does not go on")
        self.assertTrue(page.events()[-1]["outputs"]["6221"].startswith("zero FAILED"))

    def test_a_failed_log_write_ends_the_scan_through_its_failure_path(self):
        page = Page(self)
        log = page.rig.log

        def hook(entry):
            recorder = page.panel.state.run_recorder
            if entry == ("sr830_8?", "SNAP? 1,2,3,4,9") and recorder is not None and recorder.failure is None:
                recorder.failure = OSError("No space left on device")
        log.hook = hook
        page.start()
        status = page.panel.ppms_field_scan_status_var.get()
        # The status line ends at "; " (short_status_error); the dialog has it all.
        self.assertEqual(status, "Failed: The PPMS log could not be written (No space left on device);")
        self.assertTrue(page.dialogs[0][1].startswith(
            "The PPMS log could not be written (No space left on device); the scan stopped"))
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(len(page.rows()), 1, "the point read before the check was saved")

    def test_a_cancelled_name_makes_nothing_and_touches_no_output(self):
        for kwargs in (dict(name=None), dict(folder=False), dict(answer=False)):
            with self.subTest(**kwargs):
                page = Page(self).start(**kwargs)
                self.assertEqual(list(Path(page.folder.name).iterdir()), [])
                self.assertFalse(page.panel.state.scan_running)
                self.assertEqual(page.sent_6221(), [])
                self.assertIsNone(page.panel.state.run_recorder)

    def test_save_csv_as_opens_in_the_run_folder(self):
        page = Page(self).start()
        with mock.patch.object(panel_mod.filedialog, "asksaveasfilename", return_value="") as save:
            page.panel.save_ppms_field_scan_csv()
        self.assertEqual(save.call_args.kwargs["initialdir"], str(page.panel.state.run_folder))

    def test_a_second_scan_gets_its_own_folder_and_the_dialog_opens_beside_the_last(self):
        page = Page(self).start()
        first = page.panel.state.run_folder
        page.start()
        self.assertEqual(page.panel.state.run_folder, first.parent / "run1_01")
        self.assertEqual(page.directory.call_args.kwargs["initialdir"], str(first.parent))
        self.assertTrue((first / "run1.csv").is_file(), "nothing overwritten")

    def test_the_start_reads_the_sr830_tc_choice_for_the_worker(self):
        page = Page(self)
        page.panel.ppms_sr830_time_constant_var.set("3 s")
        page.start()
        self.assertEqual(page.panel.state.scan_sr830_tc_code, STFMR_LOCKIN_SR830_TIME_CONSTANTS["3 s"])


class FieldScanEndTests(unittest.TestCase):
    """A field scan's own field rules are unchanged; the 6221 is zeroed too."""

    def harness(self, refuse_zero=False):
        import test_ppms_nested as nested
        case = nested.NestedTests()
        obj, motor, settings, events = case.harness()
        obj.runner._zero_field_after_interrupted_scan = types.MethodType(
            PPMSScanRunner._zero_field_after_interrupted_scan, obj.runner
        )
        remote = obj.state.ppms_environment_controller
        send = remote.set_field          # the harness's: it logs and moves the field

        def set_field(target, *args):
            if refuse_zero and target == 0.0:
                events.append(("field", target))
                raise RuntimeError("MultiVu refused")
            send(target, *args)
        remote.set_field = set_field
        settings.update(scan_mode="Field", nested=dict(angle=(26.,), temperature=(300.,)))

        def run():
            clock = iter(range(10000))
            with mock.patch.object(runner_mod, 'PPMSMotorDebug', motor), \
                    mock.patch.object(runner_mod.time, 'monotonic', lambda: next(clock)):
                obj.runner.nested_scan_worker([(1, 1, 1, 100.)], 100., 0., 1, settings, sink=obj)
        case.run_worker = lambda *_args: run()
        return obj, motor, settings, events, case

    def test_a_field_failure_zeroes_the_field_once(self):
        obj, motor, settings, events, case = self.harness()

        def fail(*args, **kwargs):
            raise PPMSFieldScanError("PPMS timed out waiting for +100 Oe to settle")
        obj.runner.field_scan_worker = fail
        with self.assertRaisesRegex(PPMSFieldScanError, "^PPMS timed out waiting for \\+100 Oe to settle$"):
            case.run_worker(obj, motor, settings)
        self.assertEqual(events.count(("field", 0.0)), 1)
        self.assertEqual(obj.state.scan_field_note, "field ramping to 0 Oe")

    def test_a_refused_field_zero_is_in_the_error_and_the_status(self):
        obj, motor, settings, events, case = self.harness(refuse_zero=True)
        # The real inner worker: its field command fails, and both workers
        # meet the failure.
        obj.runner.field_scan_worker = types.MethodType(PPMSScanRunner.field_scan_worker, obj.runner)
        obj.state.scan_rows, obj.state.scan_total, obj.state.scan_started_at = [], 1, 0.
        obj.state.last_readback = None
        obj.runner.configure_transport_scan = lambda config, sink=None: None
        remote = obj.state.ppms_environment_controller
        refuse = remote.set_field

        def set_field(target, *args):
            if target != 0.0:
                raise RuntimeError("busy")
            refuse(target, *args)
        remote.set_field = set_field
        with self.assertRaises(PPMSFieldScanError) as caught:
            case.run_worker(obj, motor, settings)
        self.assertTrue(str(caught.exception).endswith("; field NOT zeroed: check MultiVu"), str(caught.exception))
        self.assertEqual(str(caught.exception).count("field NOT zeroed"), 1, "the zero is tried once")
        # The finish keeps it on the status line, which ends at "; ".
        obj.ppms_field_scan_status_var, obj.ppms_field_scan_eta_var = Var(""), Var("")
        obj.macro_runner = mock.Mock()
        obj.ppms_field_scan_stop_button = mock.Mock()
        obj.ppms_field_scan_save_button = mock.Mock()
        obj.ppms_control_widgets = []
        obj.state.ppms_environment_controller.connected = True
        obj.ppms_control_safety_var = mock.Mock(get=lambda: False)
        obj.state.scan_outputs = {"6221": "zeroed"}
        dialogs = []
        obj.shell.root = types.SimpleNamespace(after=lambda _ms, show: show())
        obj.root = obj.shell.root
        with mock.patch.object(panel_mod.messagebox, "showerror", side_effect=lambda *a, **k: dialogs.append(a)):
            obj.on_scan_finished(
                mode="Field",
                outcome="error",
                status="Failed: Field set failed; field NOT zeroed: check MultiVu",
                results=obj.state.scan_outputs,
                outputs_confirmed=True,
                field_note="field NOT zeroed: check MultiVu",
                error=caught.exception,
                problems=[],
            )
        self.assertTrue(obj.ppms_field_scan_status_var.get().startswith("Failed: Field set failed"))
        self.assertTrue(obj.ppms_field_scan_status_var.get().endswith("; field NOT zeroed: check MultiVu"))
        self.assertIn("field NOT zeroed: check MultiVu", dialogs[0][1])
        self.assertIn("At the end: 6221 zeroed", dialogs[0][1])

    def test_a_stopped_field_scan_says_what_its_field_did(self):
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        obj.state.ppms_environment_controller = types.SimpleNamespace(connected=True, backend="fake")
        obj.shell = types.SimpleNamespace(
            closing=False, root=types.SimpleNamespace(after=lambda _ms, show: show()), thread_log=mock.Mock()
        )
        obj.root = obj.shell.root
        obj.thread_log = obj.shell.thread_log
        obj.macro_runner = mock.Mock()
        obj.ppms_field_scan_stop_button = mock.Mock()
        obj.ppms_field_scan_save_button = mock.Mock()
        obj.ppms_control_widgets = []
        obj.state.ppms_environment_controller.connected = True
        obj.ppms_control_safety_var = mock.Mock(get=lambda: False)
        obj.ppms_field_scan_status_var, obj.ppms_field_scan_eta_var = Var(""), Var("")
        obj.state.scan_outputs = {"6221": "zeroed"}
        for note, expected in (("", "Stopped; 6221 zeroed; field kept at its setpoint"),
                               ("field ramping to 0 Oe", "Stopped; 6221 zeroed; field ramping to 0 Oe"),
                               (PPMS_FIELD_NOT_ZEROED_TEXT,
                                "Stopped; 6221 zeroed; field NOT zeroed: check MultiVu")):
            with self.subTest(note=note):
                obj.state.scan_field_note = note
                obj.on_scan_finished(
                    mode="Field",
                    outcome="stopped",
                    status=expected,
                    results=obj.state.scan_outputs,
                    outputs_confirmed=True,
                    field_note=note,
                    error=None,
                    problems=[],
                )
                self.assertEqual(obj.ppms_field_scan_status_var.get(), expected)
        obj.state.scan_field_note = ""
        obj.state.zero_persist_completed = True
        obj.on_scan_finished(
            mode="Field",
            outcome="completed",
            status="Completed; 6221 zeroed; field zeroed and confirmed Persistent",
            results=obj.state.scan_outputs,
            outputs_confirmed=True,
            field_note="",
            error=None,
            problems=[],
        )
        self.assertEqual(obj.ppms_field_scan_status_var.get(),
                         "Completed; 6221 zeroed; field zeroed and confirmed Persistent")

    def test_the_6221_is_zeroed_before_a_queued_zero_and_persist(self):
        state = PPMSAppState()
        state.zero_persist_pending = True
        order = []
        runner = PPMSScanRunner(state, post_ui=lambda f: None)
        runner.nested_scan_worker = lambda *args, **kwargs: order.append("scan")
        runner._zero_outputs_after_scan = lambda sink=None: order.append("6221 zeroed") or {"6221": "zeroed"}
        remote = state.ppms_environment_controller = mock.Mock()
        remote.set_field.side_effect = lambda *args: order.append(("field", args[3]))
        remote.read_environment.side_effect = [{'field_oe': 0., 'field_status': 'StableDriven'},
                                               {'field_oe': 0., 'field_status': 'StablePersistent'}]
        runner.run(ScanRequest(sequence=[], rate_oe_s=100.0, stable_s=0.0, approach_code=1, transport_settings={}))
        self.assertEqual(order, ["scan", "6221 zeroed", ("field", 1), ("field", 0)])
        self.assertTrue(state.zero_persist_completed)


class ZeroPersistButtonTests(unittest.TestCase):
    """P5: queued only while a scan runs; a second click cancels; Start
    clears it."""

    def test_the_button(self):
        panel, _ttk = build_ppms_panel()
        button = panel.ppms_zero_persist_button
        self.assertEqual(button.options["state"], "disabled", "idle: not usable")
        panel._queue_ppms_zero_persist()
        self.assertFalse(panel.state.zero_persist_pending, "nothing queued while idle")
        panel.state.scan_running = True
        panel._update_ppms_control_ui()
        self.assertEqual(button.options["state"], "normal")
        panel._queue_ppms_zero_persist()
        self.assertTrue(panel.state.zero_persist_pending)
        self.assertEqual(button.options["text"], "Queued: zero + persist")
        panel._queue_ppms_zero_persist()
        self.assertFalse(panel.state.zero_persist_pending, "a second click cancels")
        self.assertEqual(button.options["text"], "Zero field + persist after scan")
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Zero field + persist after this scan: cancelled")
        panel.state.scan_running = False
        panel._update_ppms_control_ui()
        self.assertEqual(button.options["state"], "disabled")

    def test_start_clears_one_queued_before(self):
        page = Page(self)
        page.panel.state.zero_persist_pending = True     # left from before (the old idle click)
        page.start()
        self.assertFalse(page.panel.state.zero_persist_pending)
        self.assertFalse(any(entry[:2] == ("ppms", "set_field") for entry in page.rig.log),
                         "no zero + persist after this scan")


class CurrentTemperatureTests(unittest.TestCase):
    """P1: each temperature block's ramp runs at its first current, and that
    first point settles."""

    def test_the_6221_returns_to_the_first_current_before_each_ramp(self):
        import test_ppms_nested as nested
        case = nested.NestedTests()
        obj, motor, settings, events = case.harness()
        settings.update(scan_mode='Current+Temperature', nested=dict(angle=(26.,), temperature=(300., 295.)),
                        currents=(1., 2., 3.))
        remote = obj.state.ppms_environment_controller
        set_temperature = remote.set_temperature
        remote.set_temperature = lambda target, *args: (events.append(('temperature', target)),
                                                         set_temperature(target, *args))
        points = []
        obj.runner.field_scan_worker = lambda seq, rate, stable, approach, config, sink=None: points.append(
            (config['current_rms_ma'], obj.state.source_fresh)) or setattr(obj.state, 'source_fresh', False)
        case.run_worker(obj, motor, settings)
        sources = [e for e in events if e[0] in ('configure', 'amplitude', 'temperature')]
        self.assertEqual(sources, [('temperature', 300.), ('configure', 1.), ('amplitude', 2.), ('amplitude', 3.),
                                   ('amplitude', 1.), ('temperature', 295.), ('amplitude', 2.), ('amplitude', 3.)])
        self.assertEqual(points, [(1., True), (2., False), (3., False), (1., True), (2., False), (3., False)])


class AutorangeTests(unittest.TestCase):
    """P2 (shared with SHHV): a range that overloaded is a floor until the
    next pass, and only a point's first reading steps a range down."""

    def sr830(self, index=13):
        state_obj = PPMSAppState()
        runner = PPMSScanRunner(state_obj, post_ui=lambda cb: None)
        state = {"SENS": index}
        instrument = mock.Mock()
        instrument.write.side_effect = lambda command: state.update(SENS=int(command.split()[1]))
        instrument.query.side_effect = lambda command: str(state["SENS"])
        state_obj.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(
            instrument=instrument, lock=threading.RLock(), label="SR830-1")}
        runner.query_numbers = lambda key, command, n: ((state["SENS"],) if command == "SENS?" else (10,))
        return state_obj, runner, state

    def test_no_flip_back_to_a_range_that_overloaded(self):
        state_obj, runner, state = self.sr830()
        small = 0.01 * NONLOCAL_SR830_FULL_SCALE_V[13]
        self.assertEqual(runner._autorange_sr830("830_a", 0., 4), 5.)    # overload: 13 -> 14
        self.assertEqual(state["SENS"], 14)
        for _ in range(20):
            runner._autorange_sr830("830_a", small, 0)
        self.assertEqual(state["SENS"], 14, "never back down to the range that overloaded")
        runner.clear_sr830_range_floor()
        for _ in range(3):
            runner._autorange_sr830("830_a", small, 0)
        self.assertEqual(state["SENS"], 13, "after the floor is cleared it steps down again")

    def test_later_readings_of_a_point_do_not_step_down_or_count(self):
        state_obj, runner, state = self.sr830()
        small = 0.01 * NONLOCAL_SR830_FULL_SCALE_V[13]
        for _ in range(10):
            runner._autorange_sr830("830_a", small, 0, allow_down=False)
        self.assertEqual(state["SENS"], 13)
        self.assertEqual(state_obj.sr830_range_states["830_a"].low_range_count, 0)
        # An overload still ranges up on a later reading.
        runner._autorange_sr830("830_a", 0., 4, allow_down=False)
        self.assertEqual(state["SENS"], 14)

    def test_only_the_first_reading_of_a_point_may_step_down(self):
        state_obj = PPMSAppState()
        state_obj.scan_stop_event = mock.Mock(is_set=mock.Mock(return_value=False),
                                              wait=mock.Mock(return_value=False))
        runner = PPMSScanRunner(state_obj, post_ui=lambda cb: None)
        flags = []
        good = dict(sr830_8_x_v=1., sr830_8_y_v=0., sr830_8_frequency_hz=317.3, sr830_8_reference_valid=True)

        def read(frequency, lockins, allow_down=True, sink=None):
            flags.append(allow_down)
            return dict(good, range_settle_s=5. if len(flags) == 2 else 0.)
        runner.read_transport_sample = read
        with mock.patch.object(runner_mod.time, "monotonic", side_effect=[0., 0., .5, 5., 5., 5.5, 6.5, 6.5]):
            runner.average_transport_sample(317.3, ("830_a",), 1.)
        self.assertEqual(flags[0], True)
        self.assertFalse(any(flags[1:]), "not after a range change's restart either")

    def test_the_floor_ends_with_the_pass(self):
        state_obj, runner, _state = self.sr830()
        runner._autorange_sr830("830_a", 0., 4)
        self.assertEqual(state_obj.sr830_range_states["830_a"].overload_floor, 14)
        runner.average_transport_sample = lambda f, l, d, sink=None: dict(sr830_8_x_v=1., sr830_8_y_v=0.)
        runner._acquire_first_point(317.3, ("830_a",), 1.)
        self.assertIsNone(state_obj.sr830_range_states["830_a"].overload_floor)


class SettleWithoutTkTests(unittest.TestCase):
    """P6: the worker's wait reads the 'SR830 TC' choice Start read."""

    def test_the_worker_never_reads_the_choice(self):
        state_obj = PPMSAppState()
        state_obj.scan_sr830_tc_code = STFMR_LOCKIN_SR830_TIME_CONSTANTS["3 s"]
        runner = PPMSScanRunner(state_obj, post_ui=lambda callback: None)
        self.assertEqual(runner._point_settle(0.5, ("830_a",)), (3.0, 3.0))


class SetIZurichTests(unittest.TestCase):
    """P7: the PPMS page's Set I never configures the Zurich from an older
    Start's copied settings."""

    def test_set_i_sets_the_6221_alone(self):
        panel, _ttk = build_ppms_panel()
        panel.state.zurich_scan_enabled, panel.state.zurich_scan_configured = True, False   # a Start that returned early
        panel.state.zurich_scan_harmonic, panel.state.zurich_scan_reference, panel.state.zurich_scan_tau = "1", "Trigger In 1", 0.1
        panel._require_ppms_control_ready = mock.Mock()
        panel._ppms_direct_transport_settings = mock.Mock(return_value=dict(current_rms_ma=1.0, frequency_hz=317.3))
        commands = []
        panel._run_ppms_command = lambda label, operation, **options: commands.append(operation)
        panel._ppms_set_current_direct()
        configure = mock.Mock()
        with mock.patch.object(panel.runner, "configure_transport_scan", configure):
            commands[0]()
        configure.assert_called_once()
        panel.state.zurich_controller.configure_mfli.assert_not_called()


class FieldReadAtStartTests(unittest.TestCase):
    """P4: modes that never command the field record the field read at Start
    and count no field ramp; P8: field ramps per harmonic pass."""

    def test_target_field_is_the_field_read_at_start(self):
        page = Page(self).start()
        self.assertEqual([float(r["target_field_oe"]) for r in page.rows()], [1234.0, 1234.0])
        settings = page.events()[0]["settings"]
        self.assertEqual(settings["field"]["target_field_oe"], 1234.0)

    def test_no_reading_refuses(self):
        page = Page(self)
        page.panel.state.last_readback = None
        page.start()
        self.assertEqual(page.dialogs[0][0], "PPMS scan parameters")
        self.assertIn("No PPMS field reading yet", page.dialogs[0][1])
        self.assertFalse(page.panel.state.scan_running)

    def test_the_estimate_has_no_phantom_field_ramp(self):
        estimates = []
        for box in ("50000", "1234"):
            page = Page(self)
            page.panel.ppms_field_target_var.set(box)
            page.start(answer=False)
            estimates.append(page.ask.call_args.args[2].split("Estimated duration: ")[1].split("\n")[0])
        self.assertEqual(estimates[0], estimates[1], "the H Oe box is not a ramp")


class MacroScanTests(unittest.TestCase):
    """The next Macro line runs from the Tk event loop, so a SCAN line's
    dialogs never open inside drain_ui_queue (README rule, 2026-10-14)."""

    def test_the_next_line_after_a_finished_command_waits_for_the_event_loop(self):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = PPMSAppState()
        queued = []
        panel.shell = types.SimpleNamespace(
            closing=False,
            root=types.SimpleNamespace(after=lambda _ms, callback: queued.append(callback)),
            post_ui=lambda callback: callback(),
            thread_log=mock.Mock(),
        )
        panel.root = panel.shell.root
        panel.thread_log = panel.shell.thread_log
        panel.ppms_macro_status, panel.ppms_macro_editor = mock.Mock(), mock.Mock()
        panel.state.ppms_environment_controller = mock.Mock()
        from test_ppms_two_panels import ImmediateExecutor
        panel.state.ppms_executor = ImmediateExecutor()
        panel._set_ppms_environment_ui = mock.Mock()
        panel.state.scan_running = False
        panel.start_ppms_field_scan = mock.Mock()
        runner = MacroRunner(
            environment_controller=panel.state.ppms_environment_controller,
            executor=panel.state.ppms_executor,
            macro_stop_event=panel.state.macro_stop_event,
            is_closed=lambda: False,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=Mock(),
            post_ui=panel.shell.post_ui,
        )
        runner.start(
            parse_ppms_macro("WAIT 0\nSCAN"),
            panel,
            start_scan=lambda: panel.start_ppms_field_scan() is not False,
            is_scan_running=lambda: panel.state.scan_running,
            schedule_poll=lambda ms, cb: panel.root.after(ms, cb),
        )
        panel.start_ppms_field_scan.assert_not_called()
        self.assertEqual(len(queued), 1)
        queued.pop()()
        panel.start_ppms_field_scan.assert_called_once()


class ProgramCloseTests(unittest.TestCase):
    def test_the_worker_closes_the_run_log_when_the_program_closes(self):
        page = Page(self)
        panel = page.panel

        def on_wait(seconds):
            if panel.state.scan_rows and not panel.shell.closing:
                panel.shell.closing = True
                recorder = panel.state.run_recorder
                recorder.event("stop_requested", button="the program closing")
                panel.state.scan_stop_event.set()
        page.rig.stop.on_wait = on_wait
        page.panel.shell.closing = False
        page.start()
        events = page.events(next(Path(page.folder.name).iterdir()) / "run1.log")
        self.assertEqual(events[-1]["event"], "run_finished")
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertEqual(events[-1]["stop_reason"], "the program closing")
        self.assertEqual(events[-1]["outputs"], {"6221": "zeroed"})
        self.assertEqual(page.rig.source.output, 0)


if __name__ == "__main__":
    unittest.main()
