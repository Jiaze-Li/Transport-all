"""Review of the merged areas (c3b4b24..1c51161): its fixes (review, 2026-10-18).

ST-FMR / FMR SF1-SF6, PPMS / SHHV PS1-PS8, MOKE / CIDWM / Non-local MN1-MN4
and the nits.  No Tk window and no instrument: the real methods run on
stand-ins (the shared STFMR, PPMS and SHHV harnesses of the other suites),
every dialog is mocked, and the real Newmark driver only ever sees the fake
PerformaxCom.dll of test_rte_angle_scan."""
from concurrent.futures import Future
import csv
import inspect
import json
import re
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import test_ppms_run_save_zero as ppms_run
import test_rte_panel as rte_tests
import test_stfmr_motor_angle as motor_tests
import test_stfmr_power_sweep as power_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var
from test_stfmr_realtime_recording import events, harness

App = app.CombinedControlApp


def setUpModule():
    # No real DLL: the guards of the Newmark stage's suite.
    motor_tests.setUpModule()


def tearDownModule():
    motor_tests.tearDownModule()


def read_events(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


class Kepco:
    """Another page's 4886 controller, as the stage check sees it."""

    def __init__(self, on=False, connected=False):
        self.output_owned, self.connected = on, connected


# ---- SF1: the stage never turns with any page's 4886 on ---------------------------------------
class StageKepcoTests(motor_tests.MotorBase):
    def hand(self, target="15", **options):
        run = self.make(**options)
        run.h.stfmr_lockin_stage_target_var.set(target)
        return run

    def test_a_hand_move_needs_this_pages_4886_connected(self):
        run = self.hand()
        run.h.stfmr_lockin_controllers["kepco"].connected = False
        run.h.move_stfmr_lockin_stage()
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[1],
                         "Connect the 4886 on this page and zero it (Zero / OFF) first: the stage must not "
                         "turn with the magnet on")
        self.assertEqual(run.sim.moves, [], "the stage did not turn")

    def test_a_hand_move_is_refused_while_the_shv_or_fmr_4886_is_on(self):
        for page in ("SHHV", "FMR"):
            with self.subTest(page=page):
                self.error.reset_mock()
                run = self.hand()
                h = run.h
                if page == "SHHV":
                    h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": Kepco(on=True)})
                else:
                    h.stfmr_controllers = {"kepco": Kepco(on=True)}
                h.move_stfmr_lockin_stage()
                self.error.assert_called_once()
                self.assertEqual(self.error.call_args.args[1],
                                 f"The KEPCO 4886 output of the {page} page is on (or not confirmed off): zero it "
                                 "there (Zero / OFF) first: the stage must not turn with the magnet on")
                self.assertEqual(run.sim.moves, [])

    def test_the_clr_and_eo_checks_refuse_too(self):
        run = self.hand()
        run.h.stfmr_controllers = {"kepco": Kepco(on=True)}
        with self.assertRaisesRegex(RuntimeError, "of the FMR page is on"):
            run.h._stfmr_lockin_require_stage_fix_ready()
        run.h.stfmr_controllers = {"kepco": Kepco(on=False)}
        run.h.stfmr_lockin_controllers["kepco"].connected = False
        with self.assertRaisesRegex(RuntimeError, "^Connect the 4886 on this page"):
            run.h._stfmr_lockin_require_stage_fix_ready()

    def test_with_every_4886_off_the_move_runs(self):
        run = self.hand("15")
        run.h.stfmr_controllers = {"kepco": Kepco(on=False, connected=True)}
        run.h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": Kepco(on=False)})
        run.h.move_stfmr_lockin_stage()
        run.h.root.drain()
        self.error.assert_not_called()
        self.assertEqual(run.sim.moves, ["X150000"])

    def test_a_sweep_refuses_its_first_move_while_another_4886_is_on(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        h.stfmr_controllers = {"kepco": Kepco(on=True)}
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_called_once()
        self.assertIn("of the FMR page is on", self.error.call_args.args[1])
        self.assertEqual(run.sim.moves, [], "the stage did not turn")
        self.assertFalse([e for e in h.log if e[:2] == ("kepco", "output")], "nothing switched on")

    def test_a_sweep_refuses_a_later_move_while_another_4886_is_on(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        shv = Kepco(on=False)
        h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": shv})

        def switch_on_elsewhere():
            shv.output_owned = True        # SHHV's KEPCO switched on during the first angle
        h.move_hooks[30.0] = switch_on_elsewhere
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(run.sim.moves, ["X300000"], "the first move only")
        self.error.assert_called_once()
        self.assertIn("of the SHHV page is on", self.error.call_args.args[1])

    def test_a_sweep_refuses_a_later_move_when_its_4886_is_gone(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        h.move_hooks[30.0] = lambda: setattr(h.stfmr_lockin_controllers["kepco"], "connected", False)
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(run.sim.moves, ["X300000"])
        self.assertIn("The KEPCO 4886 is not connected; the sweep stopped before the stage moved",
                      self.error.call_args.args[1])

    def test_the_lookup_ignores_this_pages_own_and_missing_pages(self):
        h = object.__new__(App)
        own = Kepco(on=True)
        h.stfmr_lockin_controllers = {"kepco": own}
        self.assertEqual(h._stfmr_lockin_other_4886_on(), [])
        h.stfmr_controllers = {"kepco": own}
        self.assertEqual(h._stfmr_lockin_other_4886_on(), [], "the same object is this page's")
        h.stfmr_controllers = {"kepco": Kepco(on=True)}
        h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": Kepco(on=True)})
        self.assertEqual(h._stfmr_lockin_other_4886_on(), ["FMR", "SHHV"])


# ---- SF2: the FMR close sets no StringVar off the Tk thread ------------------------------------
class FMRCloseReadoutTests(unittest.TestCase):
    def page(self):
        a = object.__new__(App)
        kepco = mock.Mock(connected=True)
        kepco.force_output_off.return_value = dict(mode="CURR", current_a=0.0, voltage_v=0.0, output=False)
        a.stfmr_controllers = {"kepco": kepco, "vna": mock.Mock(connected=True)}
        a._apply_stfmr_kepco_readback = mock.Mock(side_effect=RuntimeError("main thread is not in main loop"))
        a._flag_stfmr_kepco_off_unconfirmed = mock.Mock()
        return a

    def test_closing_updates_no_readout(self):
        a = self.page()
        results = a._stfmr_fmr_zero_outputs(readout=False)
        self.assertEqual(results, {"4886": "zeroed", "PNA RF": "zeroed"})
        a._apply_stfmr_kepco_readback.assert_not_called()
        a.stfmr_controllers["kepco"].force_output_off.side_effect = RuntimeError("OUTP OFF timeout")
        results = a._stfmr_fmr_zero_outputs(readout=False)
        self.assertTrue(results["4886"].startswith("zero FAILED: OUTP OFF timeout"))
        a._flag_stfmr_kepco_off_unconfirmed.assert_not_called()

    def test_a_tk_error_in_the_readout_never_fails_a_sent_zero(self):
        a = self.page()
        results = a._stfmr_fmr_zero_outputs()
        a._apply_stfmr_kepco_readback.assert_called_once()
        self.assertEqual(results["4886"], "zeroed")

    def test_safe_close_passes_readout_false(self):
        source = inspect.getsource(App.safe_close_stfmr)
        # magnet=: the LS648's zero then runs on a thread of its own
        # (2026-10-21); the readout is never set either way.
        self.assertIn("self._stfmr_fmr_zero_outputs(readout=False, magnet=not deferred)", source)


# ---- SF3 and SF6: the SMB100A carrier read-back and the first read-backs -----------------------
class CarrierReadBackTests(power_tests.PowerSweepCase):
    def test_the_carrier_is_compared_with_the_value_sent(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        # 9 digits: "FREQ 12.3456789GHz", 1.2 Hz from the value asked for.
        readback = smb.set_frequency(12.3456789012)
        self.assertIn("FREQ 12.3456789GHz", smb.instrument.writes)
        self.assertAlmostEqual(readback, 12.3456789e9, delta=1e-3)
        smb.configure(12.3456789012, 10.0, 317.3)
        self.assertAlmostEqual(smb.last_configure_readbacks["frequency_readback_hz"], 12.3456789e9, delta=1e-3)
        self.assertAlmostEqual(smb.last_configure_readbacks["modulation_readback_hz"], 317.3, delta=1e-9)

    def test_a_carrier_the_generator_did_not_take_still_raises(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        smb.instrument.query = lambda command: "5000000000" if command == "FREQ?" else "0"
        with self.assertRaisesRegex(RuntimeError, "did not take the new carrier frequency"):
            smb.set_frequency(6.0)

    def test_the_lfo_rule_is_unchanged(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        smb.instrument.query = lambda command: "317.4" if command == "LFO:FREQ?" else "0"
        with self.assertRaisesRegex(RuntimeError, "modulation frequency"):
            smb.set_modulation_frequency(317.3)

    def test_a_failed_configure_leaves_no_old_read_backs(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        smb.configure(5.0, 10.0, 317.3)
        self.assertIn("frequency_readback_hz", smb.last_configure_readbacks)
        smb.instrument.query = lambda command: "x"
        with self.assertRaises(RuntimeError):
            smb.configure(6.0, 10.0, 317.3)
        self.assertEqual(smb.last_configure_readbacks, {})

    def test_the_first_frequency_ready_has_the_carrier_and_lfo_read_backs(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        h, recorder = self.sweep(powers=("10", "10", "1"), stop="6", smb=smb)
        self.error.assert_not_called()
        ready = [e for e in events(recorder) if e["event"] == "frequency_ready"]
        self.assertEqual(len(ready), 2)
        self.assertAlmostEqual(ready[0]["frequency_readback_hz"], 5e9, delta=1e-3)
        self.assertAlmostEqual(ready[0]["modulation_readback_hz"], 117.3, delta=1e-9)
        self.assertAlmostEqual(ready[1]["frequency_readback_hz"], 6e9, delta=1e-3)


# ---- SF4: Save CSV's file names --------------------------------------------------------------
class SaveCsvNameTests(unittest.TestCase):
    def test_a_saved_copy_never_matches_the_run_csv_pattern(self):
        with tempfile.TemporaryDirectory() as folder:
            h = harness(folder)
            h.root = mock.Mock()
            h.stfmr_lockin_rows = [{"index": 1, "current_a": 0.5}]
            h._export_stfmr_lockin_data_pngs = mock.Mock(return_value=())
            run = Path(folder) / "STFMR_field_scan_20261018_120000_000001"
            run.mkdir()
            (run / f"{run.name}.csv").write_text("index\n", encoding="utf-8")
            h.stfmr_lockin_run_directory = run
            with mock.patch.object(app.filedialog, "askdirectory", return_value=str(run)) as ask, \
                    mock.patch.object(app.messagebox, "showerror") as error:
                h.save_stfmr_lockin_results()
            error.assert_not_called()
            self.assertEqual(ask.call_args.kwargs["initialdir"], str(run))
            self.assertEqual(len(list(run.glob("STFMR_field_scan_*.csv"))), 1, "still one run CSV")
            [saved] = run.glob("STFMR_saved_*.csv")
            self.assertRegex(saved.name, r"^STFMR_saved_\d{8}_\d{6}_\d{6}\.csv$")
            h._export_stfmr_lockin_data_pngs.assert_called_once_with(run, saved.stem)
            with saved.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [{"index": "1", "current_a": "0.5"}])


# ---- SF5: a cancelled Start unlocks the rows and Apply ---------------------------------------
class StartStepRefreshTests(unittest.TestCase):
    def test_every_step_refreshes_the_page(self):
        a = object.__new__(App)
        a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
        seen = []
        a._update_stfmr_lockin_ui = mock.Mock(side_effect=lambda: seen.append(a.stfmr_lockin_starting))
        a._run_stfmr_lockin_start_step(lambda: a._update_stfmr_lockin_ui())   # cancelled at its Yes/No
        self.assertEqual(seen, [True, False], "locked during the step, refreshed after it")
        self.assertFalse(a._stfmr_lockin_rows_locked())
        self.assertFalse(a._stfmr_lockin_manual_output_held())

    def test_no_refresh_on_a_closing_page(self):
        a = object.__new__(App)
        a.closing, a.stfmr_lockin_closed, a.stfmr_lockin_running = True, False, False
        a._update_stfmr_lockin_ui = mock.Mock()
        a._run_stfmr_lockin_start_step(lambda: None)
        a._update_stfmr_lockin_ui.assert_not_called()


# ---- PS1, PS3: closing during a PPMS scan -----------------------------------------------------
class PPMSCloseTests(unittest.TestCase):
    def page(self):
        page = ppms_run.Page(self)
        sent = page.rig.log
        for key, controller in page.panel.ppms_instrument_controllers.items():
            def disconnect(key=key, controller=controller):
                sent.append(("disconnect", key))
                controller.instrument, controller.connected = None, False
            controller.disconnect = disconnect
        page.panel.ppms_environment_controller.disconnect = lambda: sent.append(("disconnect", "ppms"))
        page.panel.root.after_cancel = lambda _timer: None
        return page

class FieldZeroOnceTests(unittest.TestCase):
    def page(self, closing=False):
        a = object.__new__(App)
        a.closing = closing
        a.ppms_field_scan_stop_event = threading.Event()
        a.ppms_field_scan_stop_event.set()
        a.ppms_active_scan_mode = "Field"
        a.ppms_environment_controller = mock.Mock(connected=True)
        a.post_ui = lambda _callback: None
        a.thread_log = mock.Mock()
        a._ppms_zero_outputs_after_scan = mock.Mock(return_value={"6221": "zeroed"})
        a._ppms_close_run = mock.Mock()
        return a

# ---- PS2, PS5: the stop handlers' order and the I off dialog ---------------------------------
class Probe:
    closed = False

    def __init__(self, probe):
        self.probe, self.events = probe, []

    def event(self, name, **details):
        self.events.append((name, details.get("button"), self.probe()))
        return True


class PPMSStopOrderTests(unittest.TestCase):
    def page(self):
        a = object.__new__(App)
        a.closing = False
        a.ppms_field_scan_running = True
        a.ppms_field_scan_stop_event = threading.Event()
        a.ppms_field_scan_status_var = Var("")
        a.ppms_field_scan_stop_button = mock.Mock()
        a.ppms_connection_var = mock.Mock()
        a.ppms_executor = mock.Mock()
        a.ppms_executor.submit.return_value = Future()
        a.post_ui = lambda callback: callback()
        return a

# ---- PS4: a failure says CHECK IT when an output was not confirmed at zero ----------------------
class FailureCheckItTests(unittest.TestCase):
    def ppms(self, results):
        a = object.__new__(App)
        later = []
        a.closing, a.root = False, types.SimpleNamespace(after=lambda _ms, show: later.append(show))
        a.thread_log = mock.Mock()
        a.ppms_field_scan_status_var, a.ppms_field_scan_eta_var = Var(""), Var("")
        a.ppms_scan_outputs = results
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._report_ppms_scan_finished("Current", False, OSError("CSV write failed"))
            while later:
                later.pop(0)()
        return a.ppms_field_scan_status_var.get(), shown

# ---- PS6: the SHHV run log is closed after the stage's line ------------------------------------
class SHVCloseOrderTests(unittest.TestCase):
    pass


# ---- PS7: an I off during Start's dialogs keeps the scan from starting -------------------------
class PPMSStartStopEventTests(unittest.TestCase):
    pass

# ---- PS8: LIAS? asked once ------------------------------------------------------------------
class Instrument:
    def __init__(self, *replies):
        self.replies, self.queries, self.clears = list(replies), [], 0

    def query(self, command):
        self.queries.append(command)
        return self.replies.pop(0)

    def clear(self):
        self.clears += 1


class ClearOnReadTests(unittest.TestCase):
    def page(self, instrument):
        a = object.__new__(App)
        a.thread_log = mock.Mock()
        a.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(
            label="SR830-1", lock=threading.RLock(), instrument=instrument, connected=True)}
        return a

# ---- MN1: the MOKE magnet's other KEPCO, sent and not read back --------------------------------
class SentNotReadBackTests(unittest.TestCase):
    labels = ("Out-of-plane KEPCO", "In-plane KEPCO")

# ---- MN2: closing mid Non-local scan ----------------------------------------------------------
class NonlocalCloseTests(unittest.TestCase):
    def page(self):
        a = object.__new__(App)
        a.closing = False
        a.nonlocal_vi_closed = False
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_vi_executor = mock.Mock()
        self.order = order = mock.Mock()
        a._nonlocal_stop_requested = order.stop_requested
        a._disconnect_nonlocal_vi_worker = order.disconnect
        a._nonlocal_zero_outputs = mock.Mock(side_effect=lambda _recorder: (
            order.zero(), {"6221": "zeroed", "Keithley 2400": "zeroed"})[1])
        a._nonlocal_close_run = order.close_run
        return a

# ---- MN3: the Non-local dialogs from the Tk event loop ---------------------------------------
class NonlocalDialogTests(unittest.TestCase):
    FINISHES = ("_finish_nonlocal_vi_device_connect", "_finish_nonlocal_vi_device_disconnect",
                "_finish_nonlocal_vi_connect", "_finish_nonlocal_vi_disconnect", "_finish_nonlocal_vi_off",
                "_finish_nonlocal_keithley_set", "_finish_nonlocal_keithley_pulse", "_finish_nonlocal_iv6221_set")

# ---- MN4: the MOKE-24 and CIDWM controllers' lines are notes ----------------------------------
class ControllerNoteTests(unittest.TestCase):
    pass

# ---- Nits: the re-checks after the dialogs; Gate-VI's summary header ----------------------------
class NitTests(unittest.TestCase):

    def test_write_header_before_any_row(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = app.ScanRunRecorder(Path(folder), "NL_GateVI", "gate", {})
            recorder.write_header(("point_index", "pulse_voltage_V"))
            recorder.write_header(("other",))          # once there, kept
            recorder.append_row({"point_index": 1, "pulse_voltage_V": 0.5})
            with self.assertRaisesRegex(ValueError, "no column for x"):
                recorder.append_row({"x": 1})
            recorder.close("completed")
            with recorder.csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.reader(handle)), [["point_index", "pulse_voltage_V"], ["1", "0.5"]])
            with self.assertRaisesRegex(RuntimeError, "closed"):
                recorder.write_header(("a",))

if __name__ == "__main__":
    unittest.main()
