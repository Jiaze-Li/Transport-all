"""The Lake Shore 643 / 648 on the SHHV, ST-FMR and FMR pages (user,
2026-10-21: "Add LS643 connection & control for SHHV and STFMR. Add LS648
connection & control for FMR"; each page chooses its magnet supply, the KEPCO
4886 by default).

The real controller on test_lakeshore_magnet_supply's FakeLS64x (its ramp on
a virtual clock) inside each page's own window-free harness: SHHV's
test_rte_angle_end_to_end (the real RTEPanel on stand-in widgets, a real
rte-control worker), the ST-FMR sweep harness and the FMR scans' bare page,
both with a real one-thread worker here.  Every supply command records its
thread.  No VISA session, no window."""
from concurrent.futures import ThreadPoolExecutor, wait
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import test_rte_angle_end_to_end as ete
from test_lakeshore_magnet_supply import connected_supply
from test_ppms_motor_debug import app
import test_fmr_scan_run as fmr_run
import test_fmr_stfmr_review as review
from test_stfmr_realtime_recording import Variable, events as stfmr_events, harness, read_csv
from test_stfmr_realtime_recording import app as stfmr_app      # the ST-FMR harness's own module
from test_stfmr_sr830_tc_fixed_current import NoWindows

setUpModule, tearDownModule = ete.setUpModule, ete.tearDownModule


class WorkerExecutor:
    """A real one-thread worker that finishes each job before submit()
    returns, so a harness's Tk loop stays in order and every job runs off
    the test's (the Tk) thread."""

    def __init__(self, case, name):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        case.addCleanup(self.pool.shutdown, wait=True)
        self.ident = self.pool.submit(threading.get_ident).result(timeout=30)
        self.fail = False

    def submit(self, fn, *args):
        if self.fail:
            raise RuntimeError("cannot schedule new futures after shutdown")
        future = self.pool.submit(fn, *args)
        wait([future], timeout=120)
        return future

    def shutdown(self, **_kwargs):
        pass


def at_least_seconds(text):
    """The seconds of "at least 12.3 s" (or minutes) in a status or log line."""
    match = re.search(r"at least ([0-9.]+) (s|min)", text)
    return float(match.group(1)) * (60.0 if match.group(2) == "min" else 1.0)


# ---- SHHV: the LS643 ---------------------------------------------------------------------------
class ShvLakeShoreTests(ete.EndToEnd):
    events = staticmethod(ete.RunLogBase.events)

    def lakeshore(self, h, rate="2", choose=True, **fake_options):
        controller, fake, _clock = connected_supply("643", owner="SHHV", **fake_options)
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        fake.journal = h.log
        h.panel.rte_controllers["lakeshore"] = h.ls = controller
        h.fake = fake
        h.panel.rte_magnet_rate.set(rate)
        if choose:
            h.panel.rte_magnet_choice_var.set("Lake Shore 643")
            h.panel._rte_magnet_choice_changed()
            self.assertTrue(h.panel._rte_lakeshore_chosen(), h.showerror.call_args)
        # The LS475 reads, with where the supply's current was then.
        h.fields_at = []
        read = h.gauss.read_field

        def read_field():
            h.fields_at.append(fake.current)
            return read()
        h.gauss.read_field = read_field
        return controller, fake

    def field_scan(self, h, span=("-1", "1", "1")):
        with mock.patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            h.panel.ppms_simple_mode.set("Field")
            h.panel._rte_mode_changed()
        for variable, value in zip(h.panel.ppms_simple_range, span):
            variable.set(value)
        h.panel.rte_round_trip.set(False)

    def field2_scan(self, h, typed, fake):
        """Angle+Field (user, 2026-11-17) with an LS475 that reads 100 G per
        A of the LS643's output, and the field setting's waits made tiny."""
        for name, value in (("MAGNET_FIELD_SET_SETTLE_S", 0.0), ("MAGNET_FIELD_SET_READ_INTERVAL_S", 1e-4),
                            ("MAGNET_FIELD_SET_GROUP_GAP_S", 1e-4)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        def read_field():
            h.log.append(("gauss", "read", threading.get_ident()))
            return {"field": 100.0 * fake.current, "unit": "G", "unit_code": 1}
        h.gauss.read_field = read_field
        with mock.patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            h.panel.ppms_simple_mode.set("Angle+Field")
            h.panel._rte_mode_changed()
        for variable, value in zip(h.panel.ppms_simple_range, ("0", "10", "10")):
            variable.set(value)
        h.panel.rte_outer_ranges["Angle+Field"][0].set(typed)

# ---- ST-FMR: the LS643 ------------------------------------------------------------------------
class StfmrLakeShoreTests(NoWindows):
    def sweep(self, rate="2", **fake_options):
        h = harness(self.tmp.name)
        h.stfmr_lockin_executor = WorkerExecutor(self, "stfmr-lockin")
        controller, fake = connected_supply("643", owner="STFMR", module=stfmr_app, **fake_options)[:2]
        self.addCleanup(stfmr_app._INSTRUMENT_LEASES.clear)
        h.stfmr_lockin_controllers["lakeshore"] = controller
        h.stfmr_lockin_magnet_choice = "LS643"
        h.stfmr_lockin_magnet_rate_var = Variable(rate)
        h.ls, h.fake = controller, fake
        return h

    def test_a_sweep_ramps_every_point_on_the_worker_and_zeroes_at_the_end(self):
        h = self.sweep()
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        question = self.confirm.call_args.args[1]
        self.assertIn("s of LS643 ramps at 2 A/s", question)
        self.assertIn("the LS643 is zeroed and the SMB100A RF switched off", question)
        h.root.drain()
        self.error.assert_not_called()
        kepco = h.stfmr_lockin_controllers["kepco"]
        self.assertFalse([call for call in kepco.calls if call[0] in ("set_output", "set_scan_current", "zero",
                                                                      "force_off")])
        points = ["SETI -1.000", "SETI -0.500", "SETI 0.000", "SETI 0.500", "SETI 1.000"]
        self.assertEqual(h.fake.sent(), ["RATE 2.0000", *points, "SETI 0.000", *points, "SETI 0.000"])
        self.assertEqual(h.fake.page_threads(), {h.stfmr_lockin_executor.ident})
        rows = read_csv(recorder.csv_path)
        self.assertEqual({r["magnet_supply"] for r in rows}, {"LS643"})
        for row in rows:
            self.assertAlmostEqual(float(row["magnet_measured_current_a"]), float(row["current_a"]), places=3)
        history = stfmr_events(recorder)
        self.assertEqual(history[0]["settings"]["magnet_supply"]["name"], "LS643")
        self.assertEqual(history[0]["settings"]["magnet_supply"]["rate_a_s"], 2.0)
        zeroed = next(e for e in history if e["event"] == "outputs_zeroed")
        self.assertEqual((zeroed["LS643"], zeroed["SMB100A RF"]), ("zeroed", "zeroed"))
        self.assertTrue(history[-1]["status"].startswith("Completed; LS643 and RF zeroed; 6 points saved"),
                        history[-1]["status"])
        self.assertLessEqual(abs(h.fake.current), 0.01)

    def test_a_stop_during_a_ramp_ends_the_sweep_stopped_and_zeroed(self):
        h = self.sweep()
        h.fake.on_advance = lambda f: h.stfmr_lockin_stop_event.set() if f.current >= 0.2 else None
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_not_called()
        sent = h.fake.sent()
        self.assertIn("STOP", sent)
        self.assertEqual(sent[sent.index("STOP") + 1:], ["SETI 0.000"], "only the end's zero after the STOP")
        self.assertTrue(h.stfmr_lockin_status_var.get().startswith("Stopped; LS643 and RF zeroed"),
                        h.stfmr_lockin_status_var.get())
        self.assertLessEqual(abs(h.fake.current), 0.01)

    def test_the_kepco_path_is_unchanged_by_default(self):
        h = self.sweep()
        h.stfmr_lockin_magnet_choice = stfmr_app.MAGNET_SUPPLY_KEPCO
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        h.root.drain()
        self.assertEqual(h.fake.sent(), [])
        kepco = h.stfmr_lockin_controllers["kepco"]
        self.assertEqual({call[0] for call in kepco.calls}, {"output", "current", "zero"})
        rows = read_csv(recorder.csv_path)
        self.assertEqual({(r["magnet_supply"], r["magnet_measured_current_a"]) for r in rows}, {("KEPCO 4886", "")})


class StfmrNoWorkerTests(StfmrLakeShoreTests):
    def test_without_a_worker_the_end_never_zeroes_the_lakeshore_on_the_tk_thread(self):
        h = self.sweep()
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        while not h.stfmr_lockin_rows:
            h.root.step()
        sent = list(h.fake.sent())
        h.stfmr_lockin_executor.fail = True
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(h.fake.sent(), sent, "no SETI 0 from the Tk thread")
        self.assertEqual(h.fake.page_threads(), {h.stfmr_lockin_executor.ident})
        zeroed = next(e for e in stfmr_events(recorder) if e["event"] == "outputs_zeroed")
        self.assertEqual(zeroed["LS643"], stfmr_app.LAKESHORE_ZERO_NOT_RUN)
        self.assertIn("CHECK IT: LS643", self.error.call_args.args[1])


class StfmrPanelTests(StfmrLakeShoreTests):
    def test_apply_and_zero_off_ramp_on_the_worker(self):
        h = self.sweep()
        h.stfmr_lockin_manual_target_var = Variable("3")
        h.apply_stfmr_lockin_kepco_output()
        self.assertTrue(h._stfmr_lockin_magnet_jobs_pending(), "Start, a hand move and the choice wait for it")
        with self.assertRaisesRegex(RuntimeError, "in progress"):
            h._stfmr_lockin_require_4886_off()
        h.root.drain()
        self.error.assert_not_called()
        self.assertAlmostEqual(h.fake.current, 3.0, places=6)
        with self.assertRaisesRegex(RuntimeError, "The LS643 output is not at zero"):
            h._stfmr_lockin_require_4886_off()
        h.zero_stfmr_lockin_kepco_output()
        h.root.drain()
        self.assertLessEqual(abs(h.fake.current), 0.01)
        self.assertEqual(h.stfmr_lockin_status_var.get(), "LS643 at 0 A (zeroed)")
        h._stfmr_lockin_require_4886_off()
        self.assertEqual(h.fake.page_threads(), {h.stfmr_lockin_executor.ident})
        # Beyond the program's 40 A: refused before anything is sent.
        sent = list(h.fake.sent())
        h.stfmr_lockin_manual_target_var = Variable("41")
        h.apply_stfmr_lockin_kepco_output()
        self.assertIn("within ±40 A", self.error.call_args.args[1])
        self.assertEqual(h.fake.sent(), sent)


class StfmrStageInterlockTests(unittest.TestCase):
    """S1 / SF1 with the Lake Shores (user, 2026-10-21): the stage never
    turns while the chosen supply is not confirmed at zero, nor while any
    other page's supply (4886 or Lake Shore) is on."""

    def page(self, choice="LS643"):
        a = object.__new__(app.CombinedControlApp)
        kepco = mock.Mock(connected=True, output_owned=False)
        lakeshore = mock.Mock(connected=True, output_on=False, short_label="LS643")
        a.stfmr_lockin_controllers = {"kepco": kepco, "lakeshore": lakeshore}
        a.stfmr_lockin_magnet_choice = choice
        return a, kepco, lakeshore

    def test_the_chosen_lakeshore_must_be_connected_and_at_zero(self):
        a, _kepco, lakeshore = self.page()
        a._stfmr_lockin_require_4886_off()
        lakeshore.output_on = True
        with self.assertRaisesRegex(RuntimeError, "The LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()
        lakeshore.output_on, lakeshore.connected = False, False
        with self.assertRaisesRegex(RuntimeError, "Connect the LS643 on this page"):
            a._stfmr_lockin_require_4886_off()
        lakeshore.connected = True
        a.stfmr_lockin_magnet_job_pending = 1
        with self.assertRaisesRegex(RuntimeError, "in progress"):
            a._stfmr_lockin_require_4886_off()
        a._stfmr_lockin_require_4886_off(pending=False)      # the worker's re-check reads flags only

    def test_another_pages_supply_on_refuses_the_move(self):
        a, kepco, _lakeshore = self.page()
        a.rte_panel = mock.Mock()
        a.rte_panel.__dict__["rte_controllers"] = {"lakeshore": mock.Mock(output_on=True), "kepco": None}
        with self.assertRaisesRegex(RuntimeError, "SHHV page's LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()
        # The FMR page's LS648 drives a different magnet (user, 2026-10-21,
        # after review): it never blocks this stage.
        a.rte_panel.__dict__["rte_controllers"] = {}
        a.stfmr_controllers = {"lakeshore": mock.Mock(output_on=True)}
        a._stfmr_lockin_require_4886_off()
        self.assertEqual(a._stfmr_lockin_other_lakeshore_on(), [])
        # With the LS643 chosen, this page's own 4886 counts as another supply.
        a.stfmr_controllers = {}
        kepco.output_owned = True
        with self.assertRaisesRegex(RuntimeError, "KEPCO 4886 output of the STFMR page is on"):
            a._stfmr_lockin_require_4886_off()

    def test_with_the_4886_chosen_its_own_lakeshore_on_refuses_the_move(self):
        a, _kepco, lakeshore = self.page(app.MAGNET_SUPPLY_KEPCO)
        a._stfmr_lockin_require_4886_off()
        lakeshore.output_on = True
        with self.assertRaisesRegex(RuntimeError, "this page's LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()


class StfmrLs648ChoiceTests(unittest.TestCase):
    """User, 2026-11-18: "add LS648 connection selection in STFMR module".
    The ST-FMR page's supply box offers KEPCO4886, LS643 and LS648; the one
    Lake Shore row connects the chosen model, whose controller (limits,
    *IDN? check) and address follow the choice."""

    def page(self):
        a = object.__new__(app.CombinedControlApp)
        a.closing = False
        a.stfmr_lockin_closed = False
        a.root = None
        a.thread_log = lambda _message: None
        a.stfmr_lockin_controllers = {
            "kepco": mock.Mock(connected=False, output_owned=False),
            "lakeshore": app.LakeShoreMagnetSupplyController(lambda _m: None, "643", owner_scope="STFMR")}
        a.stfmr_lockin_magnet_choice = "LS643"
        a.stfmr_lockin_magnet_choice_var = Variable("LS643")
        a.stfmr_lockin_resource_vars = {"lakeshore": Variable("GPIB0::12::INSTR")}
        a.stfmr_lockin_connection_vars = {"lakeshore": Variable("Not connected")}
        a.stfmr_lockin_lakeshore_resources = {m: i["resource"] for m, i in app.LAKESHORE_MAGNET_MODELS.items()}
        for name in ("_stfmr_lockin_show_magnet_choice", "_update_stfmr_lockin_ui"):
            setattr(a, name, lambda: None)
        a._stfmr_lockin_rows_locked = lambda: False
        error = mock.patch.object(app.messagebox, "showerror")
        self.error = error.start()
        self.addCleanup(error.stop)
        return a

    def choose(self, a, shown):
        a.stfmr_lockin_magnet_choice_var.set(shown)
        a._stfmr_lockin_magnet_choice_changed()

    def test_the_choices(self):
        self.assertEqual(app.STFMR_LOCKIN_MAGNET_CHOICES, ("KEPCO4886", "LS643", "LS648"))
        self.assertEqual(app.STFMR_FMR_MAGNET_CHOICES, ("KEPCO4886", "LS648"), "the FMR page unchanged")

    def test_choosing_the_ls648_swaps_the_row_to_it_and_back(self):
        a = self.page()
        old = a.stfmr_lockin_controllers["lakeshore"]
        a.stfmr_lockin_resource_vars["lakeshore"].set("GPIB0::7::INSTR")      # a typed 643 address
        self.choose(a, "LS648")
        self.error.assert_not_called()
        ls = a.stfmr_lockin_controllers["lakeshore"]
        self.assertIsNot(ls, old)
        self.assertEqual((ls.model, ls.short_label, ls.program_max_a, ls.owner_scope),
                         ("648", "LS648", 100.0, "STFMR"))
        self.assertFalse(ls.connected)
        self.assertEqual(a.stfmr_lockin_magnet_choice, "LS648")
        self.assertTrue(a._stfmr_lockin_lakeshore_chosen())
        self.assertEqual(a._stfmr_lockin_supply_label(), "LS648")
        self.assertEqual(a._stfmr_lockin_supply().name, "LS648")
        self.assertEqual(a.stfmr_lockin_resource_vars["lakeshore"].get(), "GPIB0::10::INSTR")
        # To the KEPCO and back to the LS648: the same controller, no swap.
        self.choose(a, "KEPCO4886")
        self.assertEqual(a.stfmr_lockin_magnet_choice, app.MAGNET_SUPPLY_KEPCO)
        self.choose(a, "LS648")
        self.assertIs(a.stfmr_lockin_controllers["lakeshore"], ls)
        # Back to the LS643: its typed address again.
        self.choose(a, "LS643")
        self.assertEqual(a.stfmr_lockin_controllers["lakeshore"].short_label, "LS643")
        self.assertEqual(a.stfmr_lockin_resource_vars["lakeshore"].get(), "GPIB0::7::INSTR")
        self.error.assert_not_called()

    def test_a_connected_or_unconfirmed_lakeshore_is_not_swapped(self):
        a = self.page()
        old = a.stfmr_lockin_controllers["lakeshore"]
        old.instrument = mock.Mock()                                           # connected
        self.choose(a, "LS648")
        self.assertIn("The LS643 is still connected: choose LS643, Disconnect it, then choose LS648",
                      self.error.call_args.args[1])
        self.assertIs(a.stfmr_lockin_controllers["lakeshore"], old)
        self.assertEqual((a.stfmr_lockin_magnet_choice, a.stfmr_lockin_magnet_choice_var.get()), ("LS643", "LS643"))
        # From the KEPCO with the LS643 disconnected but not confirmed at zero.
        old.instrument = None
        self.choose(a, "KEPCO4886")
        old.output_on = True
        self.choose(a, "LS648")
        self.assertIn("The LS643 was left with its output not confirmed at zero", self.error.call_args.args[1])
        self.assertIs(a.stfmr_lockin_controllers["lakeshore"], old)
        self.assertEqual(a.stfmr_lockin_magnet_choice_var.get(), "KEPCO4886", "the choice put back")
        # The LS643 itself may still be chosen (to connect and zero it).
        self.choose(a, "LS643")
        self.assertEqual(a.stfmr_lockin_magnet_choice, "LS643")

    def test_the_stage_check_names_the_chosen_model(self):
        a = self.page()
        self.choose(a, "LS648")
        with self.assertRaisesRegex(RuntimeError, "Connect the LS648 on this page"):
            a._stfmr_lockin_require_4886_off()


# ---- FMR: the LS648 -----------------------------------------------------------------------------
class FmrBusyTests(fmr_run.FmrRun):
    """The review's B1 (2026-10-21): _set_stfmr_scan_busy had lost its def,
    and every FMR test stubbed it.  Here it is the page's own, on stand-in
    buttons: a KEPCO scan from Start to its end, and the LS648's Zero / OFF
    then Disconnect."""

    def page(self):
        a = self.make()
        del a._set_stfmr_scan_busy                      # the real method, not the harness's stub
        a.stfmr_mfli_controller = mock.Mock(mfli_connected=False)
        a.instrument_connection_indicators = {}
        a.stfmr_device_connect_buttons = {key: mock.Mock() for key in app.STFMR_DEFAULT_RESOURCES}
        a.stfmr_device_disconnect_buttons = {key: mock.Mock() for key in app.STFMR_DEFAULT_RESOURCES}
        a.stfmr_device_resource_widgets = {key: mock.Mock() for key in app.STFMR_DEFAULT_RESOURCES}
        for name in ("stfmr_apply_output_button", "stfmr_read_output_button", "stfmr_zero_output_button",
                     "stfmr_read_gauss_button", "stfmr_fmr_start_button", "stfmr_frequency_start_button"):
            setattr(a, name, mock.Mock())
        return a

    @staticmethod
    def last_state(button):
        return button.configure.call_args.kwargs["state"]

    def test_a_kepco_scan_runs_from_start_to_its_end_with_the_real_busy_state(self):
        a = self.page()
        self.drive(a, a.start_stfmr_fmr_scan)
        self.error.assert_not_called()
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Completed; 4886 and RF zeroed; 6 points saved")
        self.assertEqual(self.last_state(a.stfmr_fmr_start_button), "normal", "idle again")
        self.assertEqual(self.last_state(a.stfmr_fmr_stop_button), "disabled")
        self.assertEqual(self.last_state(a.stfmr_apply_output_button), "normal")

    def test_the_lakeshore_zero_off_and_disconnect(self):
        a = self.page()
        errors = []

        def post_ui(callback):
            try:
                callback()
            except BaseException as error:      # a done callback's error is otherwise only logged
                errors.append(error)
                raise
        a.post_ui = post_ui
        self.addCleanup(lambda: self.assertEqual(errors, []))
        a.stfmr_executor = WorkerExecutor(self, "stfmr-control")
        controller, fake = connected_supply("648", owner="FMR", current=3.0)[:2]
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        a.stfmr_controllers["lakeshore"] = controller
        a.stfmr_magnet_choice, a.stfmr_closed = "LS648", False
        ls_row = (mock.Mock(), mock.Mock(), mock.Mock())
        a.stfmr_supply_rows = {"lakeshore": ls_row}
        a.stfmr_device_connection_vars = {"lakeshore": fmr_run.Var("Connected")}
        a.zero_stfmr_kepco_output()
        self.assertEqual(a.__dict__.get("stfmr_magnet_job_pending"), 0, "the job counted down")
        self.assertLessEqual(abs(fake.current), 0.05)
        self.assertEqual(self.last_state(a.stfmr_zero_output_button), "normal")
        self.assertEqual(self.last_state(ls_row[2]), "normal", "Disconnect usable again")
        with mock.patch.object(app, "release_shared_visa_manager"):
            a.disconnect_stfmr_device("lakeshore")
        self.assertFalse(controller.connected)
        self.assertEqual(a.stfmr_device_connection_vars["lakeshore"].get(), "Not connected")
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})



class FmrLakeShoreTests(fmr_run.FmrRun):
    def make_ls(self, rate=5.0, **fake_options):
        a = self.make()
        a.stfmr_executor = WorkerExecutor(self, "stfmr-control")
        controller, fake = connected_supply("648", owner="FMR", **fake_options)[:2]
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        a.stfmr_controllers["lakeshore"] = controller
        a.stfmr_magnet_choice = "LS648"
        a.stfmr_magnet_rate_a_s = rate
        a.stfmr_magnet_rate_var = fmr_run.Var(f"{rate:g}")
        a.stfmr_closed = False

        class Gauss:
            connected = True

            def read_field(self):
                return {"field": 300.0 * fake.current, "unit_code": 1, "unit": "G"}

        a.stfmr_controllers["gauss"] = Gauss()
        return a, controller, fake

    def test_a_field_scan_ramps_each_point_on_the_worker_and_zeroes_after_each_frequency(self):
        a, controller, fake = self.make_ls()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: not a.stfmr_fmr_running
                   and not a.__dict__.get("stfmr_fmr_ending"))
        self.error.assert_not_called()
        self.assertIn("of LS648 ramps at 5 A/s", self.confirm.call_args.args[1])
        self.assertIn("the LS648 is ramped to 0 A and the PNA RF switched off", self.confirm.call_args.args[1])
        self.assertFalse([entry for entry in a.log if entry[0] == "4886"], "the 4886 is sent nothing")
        sweep = ["SETI -0.500", "SETI 0.000", "SETI 0.500"]
        self.assertEqual(fake.sent(), ["RATE 5.0000", *sweep, "SETI 0.000", *sweep, "SETI 0.000"])
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})
        rows = self.rows(self.folder)
        header = rows[0]
        # The supply's two columns, then Acquire s's two (user, 2026-11-17).
        self.assertEqual(header[-4:], ["magnet_supply", "magnet_measured_current_a", "acquire_s", "pna_reading_count"])
        self.assertEqual({row[-4] for row in rows[1:]}, {"LS648"})
        events = self.events(self.folder)
        self.assertEqual(events[0]["settings"]["outputs_zeroed_at_end"], ["LS648", "PNA RF"])
        self.assertEqual(events[0]["settings"]["magnet_supply"]["program_max_a"], 100.0)
        self.assertEqual({k: events[-2][k] for k in ("LS648", "PNA RF")}, {"LS648": "zeroed", "PNA RF": "zeroed"})
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Completed; LS648 and RF zeroed; 6 points saved")
        self.assertLessEqual(abs(fake.current), 0.01)

    def test_zero_off_during_a_scan_stops_it_and_the_end_ramps_to_zero(self):
        a, controller, fake = self.make_ls()
        a.stfmr_controllers["vna"].on_read[2] = lambda: a.zero_stfmr_kepco_output()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: not a.stfmr_fmr_running
                   and not a.__dict__.get("stfmr_fmr_ending"))
        self.assertTrue(a.stfmr_fmr_status_var.get().startswith("Stopped; LS648 and RF zeroed"),
                        a.stfmr_fmr_status_var.get())
        self.assertEqual(fake.sent()[-1], "SETI 0.000")
        self.assertLessEqual(abs(fake.current), 0.01)

    def test_apply_and_zero_off_ramp_on_the_worker(self):
        a, controller, fake = self.make_ls()
        a.stfmr_output_target_var = fmr_run.Var("-60")
        a.apply_stfmr_kepco_output()
        self.error.assert_not_called()
        self.assertAlmostEqual(fake.current, -60.0, places=6)
        self.assertEqual(a.stfmr_status_var.get(), "LS648 at -60 A; check the 475")
        self.assertEqual(a.stfmr_kepco_current_var.get(), "I: -60 A")
        a.zero_stfmr_kepco_output()
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertEqual(a.stfmr_kepco_output_var.get(), "At 0 A")
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})
        a.stfmr_output_target_var = fmr_run.Var("100.5")
        a.apply_stfmr_kepco_output()
        self.assertIn("within ±100 A", self.error.call_args.args[1])

    def test_without_a_worker_the_end_never_zeroes_the_lakeshore_on_the_tk_thread(self):
        a, controller, fake = self.make_ls()
        controller.ramp_to(2.0, 5.0)
        sent = list(fake.sent())
        a.stfmr_executor.fail = True
        results = []
        a._stfmr_zero_then(results.append)
        self.assertEqual(fake.sent(), sent)
        self.assertEqual(results[0]["LS648"], app.LAKESHORE_ZERO_NOT_RUN)
        self.assertEqual(results[0]["PNA RF"], "zeroed")

    def test_the_frequency_sweep_feeds_the_lakeshore_back_to_the_field(self):
        a, controller, fake = self.make_ls(rate=10.0)
        self.drive(a, a.start_stfmr_frequency_scan, done=lambda: not a.stfmr_frequency_running
                   and not a.__dict__.get("stfmr_fmr_ending"))
        self.error.assert_not_called()
        rows = self.rows(self.folder)
        currents = {round(float(row[rows[0].index("current_setpoint_a")]), 3) for row in rows[1:]}
        self.assertEqual(len(rows) - 1, 3)
        self.assertEqual(len(currents), 1)
        self.assertAlmostEqual(currents.pop(), 1.0, delta=0.02)          # 300 G at 300 G/A
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})
        self.assertEqual(fake.sent()[-1], "SETI 0.000")
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertEqual(a.stfmr_frequency_status_var.get(), "Completed; LS648 and RF zeroed; 3 points saved")


if __name__ == "__main__":
    unittest.main()
