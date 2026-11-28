"""RT-E and PPMS: every lock-in reading settles at least 1 x TC (user,
2026-10-14).

The user's rule: "every lock-in reading waits at least 7 x TC (99 % settled
at 12 dB/oct) after a change, on every page; Settle s still applies when
longer; the Start confirmation shows the wait".  Later that day, after
seeing 9 s a point, the user chose 1 x TC instead: "change all to 1*TC
please" (LOCKIN_SETTLE_TAUS = 1.0).  On RT-E (Field, Current, Angle) and
PPMS (Temperature, Field, Angle, Current and the combined scans, macro SCAN
lines included) the wait before each point's reading is max(Settle s,
1 x the slowest time constant among the lock-ins measured).  Where a test
checks that the time constant sets the wait, 1 x TC is longer than Settle s
(e.g. 1 s or 3 s against 0.5 s).

Checked here: the rule (SR830 only, Zurich only, both, Settle s longer);
the wait before every reading in each RT-E mode and each PPMS mode, the
first point of each harmonic pass included, while the PPMS temperature and
field stability waits keep their criteria; the Start confirmations, status
lines and duration estimates; the CSV columns, appended last.

The review of 2026-10-14 adds: RT-E reads each SR830's time constant at
the scan start, as PPMS does, and a time constant changed since Connect is
waited for; the status line shows a wait that differs from the announced
one while it runs; the wait is rounded to 1e-12 s (0.7 s, not
0.7000000000000001 s, in the CSV, under the 7 x TC rule); status lines show
at most five digits;
the Angle+Temperature and Current+Temperature modes, PPMS Angle harmonic
passes, and the Zurich TC box read at Start on both pages.

No Tk window and no instrument: the scan workers run on stand-ins with a
clock that moves only when the scan waits.  Every message box is mocked,
and creating a Tk root fails the test.
"""
import tempfile
import threading
import tkinter
import tkinter.messagebox
import types
import unittest
from unittest import mock

import transport.ppms.engine.motor as motor_mod
import transport.ppms.engine.runner as runner_mod
import transport.ppms.ui.panel as panel_mod
from transport.ppms.controllers.environment import PPMS_QDINSTRUMENT_BACKEND
from transport.ppms.controllers.sr830 import SR830_TIME_CONSTANTS_S
from transport.ppms.engine.planning import (
    LOCKIN_SETTLE_TAUS,
    ppms_lockin_settle_s,
    ppms_lockin_settle_text,
    ppms_settle_status_text,
    validate_ppms_transport_scan_settings,
)
from transport.ppms.engine.macro import MacroRunner
from transport.ppms.engine.runner import PPMSScanRunner, ppms_field_is_ready
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMS_SR830_HARMONIC_ATTRS, PPMSPanelView
from test_ppms_two_panels import Var


class NoWindows(unittest.TestCase):
    def setUp(self):
        self.error = mock.Mock()
        unexpected = mock.Mock(side_effect=AssertionError("an unexpected dialog opened"))
        patches = [
            mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window")),
            mock.patch.object(tkinter.messagebox, "showerror", self.error),
        ]
        for name in ("showwarning", "showinfo", "askyesno", "askokcancel", "askyesnocancel",
                     "askquestion", "askretrycancel"):
            patches.append(mock.patch.object(tkinter.messagebox, name, unexpected))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)


class Clock:
    """time.monotonic for a scan: it moves only when the scan waits."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ClockEvent:
    """A scan's stop event that is never set: each wait is logged and moves
    the clock on."""

    def __init__(self, clock, log):
        self.clock, self.log = clock, log

    def is_set(self):
        return False

    def wait(self, seconds):
        self.log.append(("wait", seconds))
        self.clock.now += seconds
        return False


class LogVar:
    """A status Var whose every set goes into the scan's event log, so a
    test sees when, relative to the waits, the status changed."""

    def __init__(self, log, value=""):
        self.log, self.value = log, value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.log.append(("status", value))


def settles_before_reads(log):
    """The wait just before each reading."""
    waits = []
    for index, entry in enumerate(log):
        if entry[0] == "read":
            before = [e for e in log[:index] if e[0] == "wait" and e[1]]
            waits.append(before[-1][1] if before else None)
    return waits


# ---- the rule ---------------------------------------------------------------------
class RuleTests(NoWindows):
    def test_the_constant_is_1_time_constant(self):
        self.assertEqual(LOCKIN_SETTLE_TAUS, 1.0)

    def test_the_pure_rule(self):
        rule = ppms_lockin_settle_s
        self.assertEqual(rule(0.5, [1.0]), 1.0, "SR830 at 1 s")
        self.assertEqual(rule(0.2, [0.3]), 0.3)
        self.assertEqual(rule(0.5, [0.3]), 0.5, "1 x 0.3 s is shorter than Settle s")
        self.assertEqual(rule(0.5, [2.0]), 2.0, "Zurich at 2 s")
        self.assertEqual(rule(0.5, [1.0, 3.0]), 3.0, "both: the slowest counts")
        self.assertEqual(rule(30.0, [1.0, 3.0]), 30.0, "a longer Settle s is kept")
        self.assertEqual(rule(0.5, []), 0.5, "no lock-in: Settle s")
        self.assertAlmostEqual(rule(0.0, [float("nan"), 1e-3]), 1e-3, msg="a non-finite TC is ignored")

    def test_the_wait_has_no_float_noise(self):
        """7 x 0.1 s is 0.7000000000000001 in binary; the CSV writes str()
        of the wait, so it is rounded to 1e-12 s (user, 2026-10-14).  At the
        user's 1 x TC the product is exact; the rounding is kept and checked
        with the earlier 7 x TC."""
        rule = ppms_lockin_settle_s
        for taus, settle, expected in (([0.1], 0.05, "0.1"), ([0.3], 0.2, "0.3"), ([1e-5], 0., "1e-05"),
                                       ([0.03], 0.01, "0.03"), ([3e-5], 0., "3e-05"), ([30e3], 0.5, "30000.0")):
            with self.subTest(taus=taus):
                self.assertEqual(str(rule(settle, taus)), expected)
        self.assertEqual(str(rule(0.05, [SR830_TIME_CONSTANTS_S[8]])), "0.1", "the 100 ms 'SR830 TC'")
        import transport.ppms.engine.planning as planning
        with mock.patch.object(planning, "LOCKIN_SETTLE_TAUS", 7.0):
            for taus, settle, expected in (([0.1], 0.5, "0.7"), ([0.3], 0.5, "2.1"), ([1e-5], 0., "7e-05"),
                                           ([0.03], 0.1, "0.21"), ([3e-5], 0., "0.00021")):
                with self.subTest(taus=taus, taus_rule=7.0):
                    self.assertEqual(str(rule(settle, taus)), expected)

    def test_the_status_lines_show_at_most_five_digits(self):
        """Whole seconds from 100 s, 3 significant digits below; with {:g}
        a Settle s of 12345.7 or a 0.0864199 s wait widened the RT-E status
        line past its label (user, 2026-10-14)."""
        text = ppms_settle_status_text
        for seconds, expected in ((7.0, "7"), (0.7, "0.7"), (2.1, "2.1"), (21.0, "21"), (70.0, "70"),
                                  (12.0, "12"), (12.34, "12.3"), (99.94, "99.9"), (123.4, "123"),
                                  (12345.7, "12346"), (86400.0, "86400"), (0.0864199, "0.0864"),
                                  (7e-05, "7e-05"), (0.0, "0")):
            with self.subTest(seconds=seconds):
                self.assertEqual(text(seconds), expected)

    def test_the_words(self):
        self.assertEqual(ppms_lockin_settle_text(0.5, 1.0, 1.0, "SR830-1"),
                         "1 s (1 x 1 s, the time constant of SR830-1, the slowest lock-in measured; "
                         "Settle s is 0.5 s)")
        self.assertEqual(ppms_lockin_settle_text(12.0, 12.0, 1.0, "the Zurich"),
                         "12 s (Settle s; 1 x the slowest lock-in time constant, 1 s on the Zurich, is 1 s)")


# ---- PPMS: every mode, through the real nested and point workers --------------------
class PPMSWorkerTests(NoWindows):
    def harness(self, mode, codes=None, zurich_tau=None, stable=0.5, harmonics=None):
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        self.clock, self.log = Clock(), []
        obj.state.scan_stop_event = ClockEvent(self.clock, self.log)
        obj.state.scan_started_at = self.clock.now
        obj.state.sr830_time_constant_codes = dict(codes or {"830_a": 10})
        obj.state.zurich_scan_enabled = zurich_tau is not None
        obj.state.zurich_scan_tau = zurich_tau or 1.0
        obj.state.ppms_instrument_controllers = {
            key: types.SimpleNamespace(resource_name=f"GPIB0::{address}::INSTR")
            for key, address in (("830_a", 8), ("830_b", 9), ("830_c", 10))}
        for key, choice in (harmonics or {}).items():
            setattr(obj, PPMS_SR830_HARMONIC_ATTRS[key], Var(choice))
        state = self.state = dict(angle=26., field=0., temp=300.)
        log = self.log

        def read_live():
            return dict(field_oe=state["field"], field_status="StableDriven",
                        temperature_k=state["temp"], temperature_status="Stable")

        def set_field(target, *_args):
            state["field"] = target
            log.append(("field", target))
            return read_live()

        def set_temperature(target, *_args):
            state["temp"] = target
            log.append(("temperature", target))
        obj.state.ppms_environment_controller = types.SimpleNamespace(
            backend=PPMS_QDINSTRUMENT_BACKEND, active=state, set_field=set_field,
            set_temperature=set_temperature)
        obj.shell = types.SimpleNamespace(closing=False, post_ui=lambda cb: None, thread_log=mock.Mock())
        obj.thread_log = obj.shell.thread_log
        obj.runner = PPMSScanRunner(obj.state, post_ui=lambda cb: None, read_live_fn=read_live)
        obj.runner._append_live_csv = lambda row, sink=None: None
        obj.runner.configure_transport_scan = lambda config, sink=None: log.append(("configure",))
        obj.runner.update_transport_current = lambda config: log.append(("amplitude", config["current_rms_ma"]))
        obj.runner._stop_transport_source = lambda **_options: log.append(("off",))

        def read(frequency, lockins, duration, sink=None):
            log.append(("read",))
            sample = {}
            for key in lockins:
                address = {"830_a": 8, "830_b": 9, "830_c": 10}[key]
                sample.update({f"sr830_{address}_x_v": 1e-6, f"sr830_{address}_y_v": 0.,
                               f"sr830_{address}_r_v": 1e-6, f"sr830_{address}_frequency_hz": frequency,
                               f"sr830_{address}_reference_valid": True})
            return sample
        obj.runner._acquire_first_point = lambda *args, **kwargs: (read(*args), 2)
        obj.runner.average_transport_sample = read

        class Motor:
            def __init__(self, _active):
                self.on_read = None

            def read(self):
                return {"angle_deg": state["angle"], "status": "AtTarget"}

            def move_and_verify(self, target, slowdown, cancelled, index_target=None):
                state["angle"] = target
                log.append(("move", target))
        self.motor = Motor
        values = {"Temperature": dict(angle=(26.,), temperature=(300., 301.)),
                  "Field": dict(angle=(26.,), temperature=(300.,)),
                  "Angle": dict(angle=(26., 27.), temperature=(300.,)),
                  "Current": dict(angle=(26.,), temperature=(300.,)),
                  "Field+Temperature": dict(angle=(26.,), temperature=(300., 301.)),
                  "Angle+Temperature": dict(angle=(26., 27.), temperature=(300., 301.)),
                  "Current+Temperature": dict(angle=(26.,), temperature=(300., 301.))}[mode]
        active = tuple(harmonics or ("830_a",))
        settings = validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, active)
        settings.update(scan_mode=mode, nested=values, slowdown=14, temperature_rate=1.,
                        temperature_approach=1, acquisition_s=1.,
                        currents=(1., 2., 3.) if mode.startswith("Current") else (1.,))
        sequence = ([(1, 1, 3, 100.), (1, 2, 3, 200.), (1, 3, 3, 100.)] if "Field" in mode
                    else [(1, 1, 1, 0.)])
        self.stable = stable
        return obj, sequence, settings

    def run_scan(self, obj, sequence, settings):
        from transport.ppms.engine.runner import ScanRequest
        request = ScanRequest(
            sequence=sequence,
            rate_oe_s=100.,
            stable_s=self.stable,
            approach_code=1,
            transport_settings=settings,
        )
        with mock.patch.object(motor_mod, "PPMSMotorDebug", self.motor), \
                mock.patch.object(runner_mod.time, "monotonic", self.clock):
            obj.runner.run(request, sink=obj)

    def test_every_mode_waits_1_time_constant_before_every_reading(self):
        for mode, points in (("Temperature", 2), ("Field", 3), ("Angle", 2), ("Current", 3),
                             ("Field+Temperature", 6), ("Angle+Temperature", 4),
                             ("Current+Temperature", 6)):
            with self.subTest(mode=mode):
                obj, sequence, settings = self.harness(mode)
                self.run_scan(obj, sequence, settings)
                # 1 x the 1 s TC, longer than Settle s 0.5 s.
                self.assertEqual(settles_before_reads(self.log), [1.0] * points, "first point included")
                rows = obj.state.scan_rows
                self.assertEqual([r["effective_settle_s"] for r in rows], [1.0] * points)
                self.assertEqual(list(rows[0])[-2:], ["effective_settle_s", "slowest_lockin_tc_s"])
                self.assertEqual([r["stable_s"] for r in rows], [0.5] * points, "Settle s itself is recorded")

    def test_temperature_and_field_stability_waits_keep_their_criteria(self):
        """The stability waits poll every 0.5 s and accept after max(1 s,
        Settle s) of good readings, as before; only the wait right before
        the reading is max(Settle s, 1 x TC)."""
        obj, sequence, settings = self.harness("Temperature")
        self.run_scan(obj, sequence, settings)
        waits = [e[1] for e in self.log if e[0] == "wait"]
        self.assertEqual(waits, [.5, .5, 1.0, .5, .5, 1.0])
        obj, sequence, settings = self.harness("Field")
        self.run_scan(obj, sequence, settings)
        waits = [e[1] for e in self.log if e[0] == "wait"]
        # The field's first setpoint (0.5 + 0.5 s), the temperature (0.5 +
        # 0.5 s), then 1 s before each of the three readings.
        self.assertEqual(waits, [.5, .5, .5, .5, 1.0, 1.0, 1.0])

    def test_a_longer_settle_s_is_kept_and_the_stability_criteria_follow_it_as_before(self):
        obj, sequence, settings = self.harness("Temperature", stable=8.0)
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [8.0, 8.0])

    def test_with_the_zurich_the_slowest_lock_in_counts(self):
        obj, sequence, settings = self.harness("Current", zurich_tau=2.0)
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [2.0] * 3, "the Zurich (2 s) is slower than SR830-1")
        rows = obj.state.scan_rows
        self.assertEqual({r["slowest_lockin_tc_s"] for r in rows}, {2.0})

    def test_each_harmonic_pass_settles_again(self):
        obj, sequence, settings = self.harness(
            "Field", codes={"830_a": 10, "830_c": 11}, harmonics={"830_a": "1+2", "830_c": "1"})
        self.run_scan(obj, sequence, settings)
        # Pass 1 measures SR830-3 (3 s) too; pass 2 SR830-1 (1 s) alone.
        self.assertEqual(settles_before_reads(self.log), [3.0] * 3 + [1.0] * 3)
        self.assertEqual([r["harmonic_pass"] for r in obj.state.scan_rows], [1] * 3 + [2] * 3)
        logged = [c.args[0] for c in obj.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual([text.split(" (")[0] for text in logged],
                         ["PPMS scan: each reading now waits 3 s", "PPMS scan: each reading now waits 1 s"])

    def test_angle_harmonic_passes_settle_again(self):
        obj, sequence, settings = self.harness(
            "Angle", codes={"830_a": 10, "830_c": 11}, harmonics={"830_a": "1+2", "830_c": "1"})
        self.run_scan(obj, sequence, settings)
        # Pass 1 measures SR830-3 (3 s) too; pass 2 SR830-1 (1 s) alone.
        self.assertEqual(settles_before_reads(self.log), [3.0] * 2 + [1.0] * 2)
        self.assertEqual([r["harmonic_pass"] for r in obj.state.scan_rows], [1] * 2 + [2] * 2)
        moves = [i for i, e in enumerate(self.log) if e[0] == "move"]
        reads = [i for i, e in enumerate(self.log) if e[0] == "read"]
        self.assertLess(moves[-1], reads[-1], "the last reading follows the last move")

    def test_a_100_ms_time_constant_is_written_without_float_noise(self):
        # Settle s 0.05 s, so the 100 ms TC sets the wait (1 x TC).
        obj, sequence, settings = self.harness("Current", codes={"830_a": 8}, stable=0.05)
        self.run_scan(obj, sequence, settings)
        rows = obj.state.scan_rows
        self.assertEqual([str(r["effective_settle_s"]) for r in rows], ["0.1"] * 3)
        self.assertEqual({r["slowest_lockin_tc_s"] for r in rows}, {0.1})

    def test_a_front_panel_time_constant_read_at_the_scan_start_is_used(self):
        """The scan start's OFLT? read (here 3 s instead of the 1 s set) is
        what the wait counts with, and it is logged."""
        obj, sequence, settings = self.harness("Current")
        obj.state.scan_settle_logged = 1.0      # announced at Start with 1 s

        def configure(config, sink=None):
            obj.state.sr830_time_constant_codes["830_a"] = 11
            self.log.append(("configure",))
        obj.runner.configure_transport_scan = configure
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [3.0] * 3)
        logged = [c.args[0] for c in obj.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual(len(logged), 1)
        self.assertIn("3 s (1 x 3 s, the time constant of SR830-1", logged[0])

    def test_a_different_wait_is_shown_on_the_status_line_once(self):
        """The Start confirmation said 1 s; the scan start reads 10 s on the
        front panel.  The status line shows the 10 s for as long as the
        wait lasts (user, 2026-10-14), and only when the wait changes."""
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        obj.state.sr830_time_constant_codes = {"830_a": 12}
        obj.state.scan_settle_logged = 1.0
        obj.shell = types.SimpleNamespace(closing=False, post_ui=lambda cb: cb(), thread_log=mock.Mock())
        obj.thread_log = obj.shell.thread_log
        obj.ppms_field_scan_status_var = Var("")
        obj.runner = PPMSScanRunner(obj.state, post_ui=lambda cb: cb())
        self.assertEqual(obj.runner._point_settle(0.5, ("830_a",), sink=obj), (10.0, 10.0))
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Each reading now waits 10 s: 1 x 10 s TC of SR830-1")
        obj.ppms_field_scan_status_var.set("CSV saved 1/5; remaining 4 min 44 s")
        obj.runner._point_settle(0.5, ("830_a",), sink=obj)
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "CSV saved 1/5; remaining 4 min 44 s",
                         "the same wait again: nothing new")
        self.assertEqual(obj.thread_log.call_count, 1)
        obj.runner._point_settle(100.0, ("830_a",), sink=obj)
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Each reading now waits 100 s (Settle s)")
        obj.shell.closing = True
        obj.runner._point_settle(0.5, ("830_a",), sink=obj)
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Each reading now waits 100 s (Settle s)",
                         "not after the window closed")


# ---- PPMS Start: the confirmation, the estimate, the status line, macro SCAN ---------
class PPMSStartTests(NoWindows):
    def scan_object(self, label="1 s", stable=0.5, zurich_tau=None, harmonic_a=1, second=False):
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        obj.shell = types.SimpleNamespace(closing=False, root=mock.Mock(), post_ui=lambda cb: cb(), thread_log=mock.Mock())
        obj.root = obj.shell.root
        obj.thread_log = obj.shell.thread_log
        obj.runner = PPMSScanRunner(obj.state, post_ui=obj.shell.post_ui)
        obj._require_ppms_control_ready = mock.Mock()
        obj.ppms_simple_mode = Var("Field")
        obj.ppms_simple_range = [Var(v) for v in (100, 300, 100)]
        values = dict(ppms_acquisition_time_var=1, ppms_field_target_var=100, ppms_temperature_target_var=300,
                      ppms_motor_target_var=26, ppms_field_scan_stable_var=stable, ppms_field_rate_var=100,
                      ppms_field_approach_var="No overshoot", ppms_transport_current_var=1,
                      ppms_transport_frequency_var=317.3, ppms_transport_compliance_var=10,
                      ppms_transport_harmonic_a_var=harmonic_a, ppms_temperature_rate_var=1,
                      ppms_temperature_approach_var="No overshoot", ppms_motor_speed_var=14,
                      ppms_motor_confirm_var=True, ppms_sr830_time_constant_var=label)
        for name, value in values.items():
            setattr(obj, name, Var(value))
        obj.state.ppms_environment_controller = types.SimpleNamespace(backend=PPMS_QDINSTRUMENT_BACKEND, connected=True)
        obj.state.ppms_instrument_controllers = {
            "6221": types.SimpleNamespace(connected=True, resource_name="GPIB0::12::INSTR"),
            "830_a": types.SimpleNamespace(connected=True, resource_name="GPIB0::8::INSTR")}
        if second:
            obj.state.ppms_instrument_controllers["830_b"] = types.SimpleNamespace(
                connected=True, resource_name="GPIB0::9::INSTR")
        obj.ppms_lockin_selection = {"830_a": Var(True), "zurich": Var(False)}
        obj.zurich_harmonic = Var("1")
        obj.zurich_reference = Var("Trigger In 1")
        obj.zurich_timeconstant = Var("1")
        if zurich_tau is not None:
            obj.ppms_lockin_selection = {"830_a": Var(True), "zurich": Var(True)}
            obj.state.zurich_controller = types.SimpleNamespace(mfli_connected=True, mfli_has_multi_demod=lambda: True)
            obj.state.zurich_scan_enabled, obj.state.zurich_scan_tau = True, zurich_tau
        obj.state.last_readback = dict(temperature_k=300, field_oe=100)
        for name in ("_redraw_ppms_transport_plot", "_update_ppms_control_ui",
                     "ppms_field_scan_progress", "ppms_transport_status_var"):
            setattr(obj, name, mock.Mock())
        obj.ppms_macro_status = None
        obj.ppms_macro_editor = None
        obj.ppms_macro_edit_buttons = []
        obj.ppms_macro_window = None
        obj.state.ppms_executor = mock.Mock()
        obj.ppms_field_scan_status_var = Var("")
        obj.ppms_field_scan_eta_var = Var("")
        return obj

    def start(self, obj, yes, run=None):
        import transport.ppms.ui.dialogs as dialogs_mod

        ask = mock.Mock(return_value=yes)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(panel_mod.messagebox, "askyesno", ask), \
                mock.patch.object(panel_mod, "ask_scan_start", return_value=True), \
                mock.patch.object(panel_mod.filedialog, "askdirectory", return_value=folder), \
                mock.patch.object(dialogs_mod.simpledialog, "askstring", return_value=""):
            (run or obj.start_ppms_field_scan)()
        self.error.assert_not_called()
        return ask

    def test_the_confirmation_shows_the_wait_and_the_estimate_counts_it(self):
        for kwargs, line, estimate in (
                ({}, "1 s (1 x 1 s, the time constant of SR830-1, the slowest lock-in measured; "
                     "Settle s is 0.5 s)", "56 s"),            # 4 + 5 x (1 + 1) + 42 s
                (dict(zurich_tau=3.0), "3 s (1 x 3 s, the time constant of the Zurich, the slowest "
                                      "lock-in measured; Settle s is 0.5 s)", "1 min 06 s"),
                (dict(stable=30.0), "30 s (Settle s; 1 x the slowest lock-in time constant, 1 s on "
                                   "SR830-1, is 1 s)", "3 min 21 s"),
                (dict(label="100 ms", stable=0.05), "0.1 s (1 x 0.1 s, the time constant of SR830-1, the "
                                                   "slowest lock-in measured; Settle s is 0.05 s)", "52 s")):
            with self.subTest(**kwargs):
                obj = self.scan_object(**kwargs)
                ask = self.start(obj, yes=False)
                message = ask.call_args.args[1]
                self.assertIn(f"Measurement points: 5\nWait before each reading: {line}\n"
                              f"Estimated duration: about {estimate}\n", message)
                self.assertEqual(ask.call_args.kwargs["icon"], "question")
                obj.state.ppms_executor.submit.assert_not_called()

    def test_harmonic_passes_with_fewer_lock_ins_are_named_and_counted(self):
        # SR830-1 reads 1+2 at 1 s; SR830-2 reads 1 at 3 s (read back), so
        # the second pass measures SR830-1 alone.
        obj = self.scan_object(harmonic_a="1+2", second=True)
        obj.state.sr830_time_constant_codes = {"830_a": 10, "830_b": 11}
        message = self.start(obj, yes=False).call_args.args[1]
        self.assertIn("Measurement points: 10\nWait before each reading: 3 s (1 x 3 s, the time constant "
                      "of SR830-2, the slowest lock-in measured; Settle s is 0.5 s); harmonic passes with "
                      "fewer lock-ins wait 1 s\n", message)
        # 4 s of ramp in each of the two passes, 5 x (3 + 1) s, 5 x (1 + 1)
        # s, 6 x 10 s and a 12 s set-up per pass: the ramps and set-ups come
        # again in the second pass (review, 2026-10-18; it said 1 min 46 s).
        self.assertIn("Estimated duration: about 2 min 02 s", message)
        # Both passes measure SR830-1 and the Zurich: one wait, no such note.
        obj = self.scan_object(zurich_tau=0.1, harmonic_a="1+2")
        obj.state.sr830_time_constant_codes = {"830_a": 11}
        message = self.start(obj, yes=False).call_args.args[1]
        self.assertIn("Measurement points: 10\nWait before each reading: 3 s (1 x 3 s", message)
        self.assertNotIn("harmonic passes with fewer lock-ins", message)

    def test_the_status_line_and_the_log_show_the_wait(self):
        obj = self.scan_object()
        self.start(obj, yes=True)
        self.assertEqual(obj.ppms_field_scan_status_var.get(),
                         "Field scan: 5 points, settle 1 s; estimated about 56 s")
        self.assertEqual(obj.ppms_field_scan_eta_var.get(), "Remaining: about 56 s")
        logs = [c.args[0] for c in obj.thread_log.call_args_list]
        self.assertIn("PPMS Field scan: each reading waits 1 s (1 x 1 s, the time constant of SR830-1, "
                      "the slowest lock-in measured; Settle s is 0.5 s)", logs)
        self.assertEqual(obj.state.scan_settle_logged, 1.0)
        obj.state.ppms_executor.submit.assert_called_once()
        request = obj.state.ppms_executor.submit.call_args.args[1]
        self.assertEqual(request.stable_s, 0.5, "the worker gets Settle s itself")

    def test_the_status_line_rounds_the_wait(self):
        obj = self.scan_object(stable=12345.7)
        self.start(obj, yes=True)
        self.assertTrue(obj.ppms_field_scan_status_var.get().startswith("Field scan: 5 points, settle 12346 s; "))

    def test_the_ppms_page_reads_its_zurich_tc_box_at_start(self):
        """PPMSPanelView.start_ppms_field_scan copies the Zurich row's "TC (s)"
        box into zurich_scan_tau before the shared Start works out the
        wait: 1 x 2 s = 2 s, slower than SR830-1 at 1 s."""
        import test_ppms_sr830_tc as tc_tests
        panel, _ttk = tc_tests.build_ppms_panel()
        panel._require_ppms_control_ready = mock.Mock()
        panel.root = None
        panel.shell.root = None
        panel.ppms_simple_mode.set("Field")
        for var, value in zip(panel.ppms_simple_range, ("100", "300", "100")):
            var.set(value)
        panel.ppms_field_scan_stable_var.set("0.5")
        panel.ppms_acquisition_time_var.set("1")
        panel.state.last_readback = dict(temperature_k=300, field_oe=100)
        panel.state.ppms_environment_controller = types.SimpleNamespace(backend=PPMS_QDINSTRUMENT_BACKEND)
        panel.ppms_motor_confirm_var.set(True)     # every scan holds an angle
        panel.state.ppms_instrument_controllers["6221"] = types.SimpleNamespace(
            connected=True, resource_name="GPIB0::12::INSTR")
        panel.state.ppms_instrument_controllers["830_a"].connect("GPIB0::8::INSTR")
        for key in ("830_b", "830_c"):
            panel.ppms_lockin_selection[key].set(False)
        panel.ppms_lockin_selection["zurich"].set(True)
        panel.state.zurich_busy = False
        panel.state.zurich_controller.mfli_connected = True
        panel.zurich_harmonic.set("1")
        panel.zurich_timeconstant.set("2")
        panel.state.zurich_scan_tau = 1.0                  # stale until Start reads the box
        ask = self.start(panel, yes=False)
        self.assertEqual(panel.state.zurich_scan_tau, 2.0)
        self.assertIn("Wait before each reading: 2 s (1 x 2 s, the time constant of the Zurich, the slowest "
                      "lock-in measured; Settle s is 0.5 s)\n", ask.call_args.args[1])

    def test_a_macro_scan_line_goes_through_the_same_start(self):
        obj = self.scan_object(label="3 s")
        obj.ppms_macro_status = Var("")
        obj.ppms_macro_editor = mock.Mock()
        obj.macro_runner = MacroRunner(
            environment_controller=obj.state.ppms_environment_controller,
            executor=obj.state.ppms_executor,
            macro_stop_event=obj.state.macro_stop_event,
            is_closed=lambda: False,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=obj.stop_ppms_field_scan,
            post_ui=obj.shell.post_ui,
        )
        ask = self.start(
            obj,
            yes=True,
            run=lambda: obj.macro_runner.start(
                [(2, "SCAN", ())],
                obj,
                start_scan=lambda: obj.start_ppms_field_scan() is not False,
                is_scan_running=lambda: obj.state.scan_running,
                schedule_poll=mock.Mock(),
            ),
        )
        self.assertIn("Wait before each reading: 3 s (1 x 3 s", ask.call_args.args[1])
        self.assertTrue(obj.state.scan_running)
        obj.state.ppms_executor.submit.assert_called_once()
        self.assertEqual(obj.ppms_field_scan_status_var.get().split(";")[0], "Field scan: 5 points, settle 3 s")


if __name__ == "__main__":
    unittest.main()
