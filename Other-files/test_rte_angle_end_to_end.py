"""RT-E Angle scan, end to end (user, 2026-10-17: "now move the stage
controllers to the RT-E settings and make the angle scan function
workable"; asked what fails: "Not tried yet; make it ready").

The real RTEPanel, built on stand-in widgets, runs a whole Angle scan
through its own Start (requirements, the Yes/No, the folder and file name,
the stage check), its scan workers and its finish, on a real single-worker
executor named rte-control, as on the lab PC.  The instruments are fakes
that log every call with the thread it came from: the KEPCO 4886 and the
LS475 at their controllers' methods, the 6221 and the lock-ins at the
page's acquisition methods (their instrument paths are the PPMS page's,
shared with the Field and Current scans), and the Newmark NSC-A1 is the
program's real controller on test_rte_angle_scan's fake PerformaxCom.dll
(the standalone script's simulated NSC-A1, on a virtual clock).  The Tk
thread is the test's: the post_ui queue and root.after callbacks run when
the test pumps them.  No real DLL is ever loaded, no window opens and
every dialog is answered here."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import csv
import ctypes
import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_rte_panel as rte_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var
from test_rte_angle_scan import Clock, EndsShort, Recorder, RecordingDLL, Sim, script

_guards = []
# The end of every SHHV scan (user, 2026-10-18): the 6221 off and read back,
# then the KEPCO's Zero / OFF.
ZEROED = [("6221", "off"), ("6221", "OUTP?"), ("kepco", "force_off")]


def setUpModule():
    def refuse(what):
        def refused(*_args, **_kwargs):
            raise AssertionError(f"a test reached {what}")
        return refused
    for target, name, replacement in ((ctypes, "WinDLL", refuse("ctypes.WinDLL (a real DLL)")),
                                      (os, "add_dll_directory", refuse("os.add_dll_directory")),
                                      (app, "newmark_load_performax", refuse("the real Newmark DLL loader")),
                                      (app.tk, "Tk", refuse("tkinter.Tk (a window)"))):
        guard = patch.object(target, name, replacement, create=True)
        guard.start()
        _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


class Root:
    """root.after for the page: callbacks wait for the test's Tk loop; the
    idle poll's 1 s timer is kept but only run when a test asks."""

    def __init__(self):
        self.pending = []

    def after(self, delay, callback):
        self.pending.append((delay, callback))
        return f"after#{len(self.pending)}"

    def after_cancel(self, _token):
        pass


class Kepco:
    connected, label = True, "KEPCO 4886"

    def __init__(self, log):
        self.log = log
        self.output_owned, self.cached_current_a = False, 0.0
        # As the real controller's (the field setting takes it, 2026-11-17).
        self.lock, self.active_mode = threading.RLock(), ""

    def set_output(self, mode, target, limit):
        self.log.append(("kepco", "set_output", mode, target, limit, threading.get_ident()))
        self.output_owned, self.cached_current_a, self.active_mode = True, target, mode

    def set_scan_current(self, value):
        self.log.append(("kepco", "set_scan_current", value, threading.get_ident()))
        self.cached_current_a = value       # as the controller's (Angle+Field rows, 2026-10-18)

    def safe_output_off(self):
        self.log.append(("kepco", "off", threading.get_ident()))
        self.output_owned = False

    def force_output_off(self):
        self.log.append(("kepco", "force_off", threading.get_ident()))
        self.output_owned, self.cached_current_a = False, 0.0
        return {}

    def disconnect(self):
        pass


class Gauss:
    connected, label = True, "Lake Shore 475"

    def __init__(self, log, field=123.0):
        self.log, self.field = log, field

    def read_field(self):
        self.log.append(("gauss", "read", threading.get_ident()))
        return {"field": self.field, "unit": "G"}

    @staticmethod
    def value_in_gauss(reading):
        return float(reading["field"])

    def disconnect(self):
        pass


class FollowingGauss(Gauss):
    """An LS475 that reads 100 G per A of the stand-in KEPCO's current
    (Angle+Field's field setting, 2026-11-17)."""

    def __init__(self, log, kepco):
        super().__init__(log)
        self.kepco = kepco

    def field_g(self):
        return 100.0 * (self.kepco.cached_current_a if self.kepco.output_owned else 0.0)

    def read_field(self):
        # The real reading's unit_code (1 = G), which the field setting's
        # STFMRGauss475Controller.value_in_gauss reads.
        self.log.append(("gauss", "read", threading.get_ident()))
        return {"field": self.field_g(), "unit": "G", "unit_code": 1}


class StuckGauss(FollowingGauss):
    """An LS475 whose field does not answer the current."""

    def field_g(self):
        return 0.0


def kepco_chosen(panel):
    """SHHV starts on the Lake Shore 643 since 2026-10-23 (user); these
    tests drive the KEPCO 4886 path, chosen as the combobox would."""
    panel.rte_magnet_choice_var.set(app.MAGNET_SUPPLY_KEPCO)
    panel._rte_magnet_choice_changed()
    return panel


def build_page(kepco=True):
    """The real RTEPanel constructor on stand-in widgets (as test_rte_angle_scan's)."""
    parent = Mock(closing=False)
    parent.root.after.return_value = "timer"
    Recorder.made = []
    with ExitStack() as stack:
        for name in ("Frame", "LabelFrame", "Label", "Entry", "Button", "Combobox", "Checkbutton", "Progressbar"):
            stack.enter_context(patch.object(app.ttk, name, Recorder))
        stack.enter_context(patch.object(app.tk, "Canvas", Recorder))
        for name in ("StringVar", "IntVar", "BooleanVar"):
            stack.enter_context(patch.object(app.tk, name, Var))
        stack.enter_context(patch.object(app, "ThreadPoolExecutor", ImmediateExecutor))
        stack.enter_context(patch.object(app.CombinedControlApp, "_create_connection_indicator",
                                         lambda _self, parent, key: Recorder(parent, indicator=key)))
        stack.enter_context(patch.object(app.CombinedControlApp, "_set_connection_indicator", Mock()))
        stack.enter_context(patch.object(app.CombinedControlApp, "_draw_nonlocal_series", Mock()))
        panel = app.RTEPanel(parent, rte_tests.Widget())
        return kepco_chosen(panel) if kepco else panel


class EndToEnd(unittest.TestCase):
    def make(self, sim=None, span=("0", "20", "10"), loops="1", round_trip=True, meter="zurich",
             tc_zurich="0.001"):
        """The page, ready for an Angle scan: the safety tick, every
        instrument connected (the stage through its real Connect, on the
        worker), Angle chosen and the range set; ``meter``: "zurich" (SHHV's
        default, the Zurich alone at 1+2 since 2026-10-18) or "sr830" (SR830-1
        alone at 1+2+3, three harmonic passes)."""
        panel = build_page()
        h = types.SimpleNamespace(panel=panel, log=[], logged=[], ui=[], in_drain=False,
                                  folder=tempfile.TemporaryDirectory())
        self.addCleanup(h.folder.cleanup)
        h.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(h.executor.shutdown, wait=True)
        h.worker = h.executor.submit(threading.get_ident).result(timeout=30)
        panel.ppms_executor = h.executor
        panel.root = h.root = Root()
        panel.post_ui = h.ui.append
        # The page's log lines, at once; the page's own thread_log still runs,
        # so a running scan's lines are also its run log's notes (2026-10-18).
        guard = patch.object(app.CombinedControlApp, "thread_log", lambda _self, message: h.logged.append(message))
        guard.start()
        self.addCleanup(guard.stop)
        # The plots: what each redraw would draw.
        h.draw = Mock()
        guard = patch.object(app.CombinedControlApp, "_draw_nonlocal_series", h.draw)
        guard.start()
        self.addCleanup(guard.stop)
        # Every dialog the scan can reach.
        h.askyesno = Mock(return_value=True)
        h.showerror = Mock()
        for target, name, value in ((app.messagebox, "askyesno", h.askyesno),
                                    (app.messagebox, "showerror", h.showerror),
                                    (app.filedialog, "askdirectory", Mock(return_value=h.folder.name)),
                                    (app.simpledialog, "askstring", Mock(return_value="angle"))):
            guard = patch.object(target, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        # The instruments.
        panel.rte_controllers["kepco"] = h.kepco = Kepco(h.log)
        panel.rte_controllers["gauss"] = h.gauss = Gauss(h.log)
        h.clock = Clock()
        guard = patch.object(script, "_now", h.clock.now)
        guard.start()
        self.addCleanup(guard.stop)
        h.sim = Sim() if sim is None else sim
        h.dll = RecordingDLL(h.sim)
        stage = app.NewmarkNSCA1Controller(h.logged.append, owner_scope="SHHV",
                                           loader=lambda: (h.dll, "fake PerformaxCom.dll"))
        stage._now, stage._sleep = h.clock.now, h.clock.sleep
        real_move = stage.move_to_deg
        h.move_hooks = {}

        def move(target, stop_event, on_progress=None):
            h.log.append(("stage", "move", float(target), threading.get_ident()))
            hook = h.move_hooks.pop(float(target), None)
            if hook is not None:
                hook()
            state = real_move(target, stop_event, on_progress)
            h.log.append(("stage", "at", state["angle_deg"], threading.get_ident()))
            return state

        stage.move_to_deg = move
        panel.rte_controllers["newmark"] = h.stage = stage
        source = types.SimpleNamespace(connected=True, label="Keithley 6221", resource_name="GPIB0::12::INSTR",
                                       disconnect=lambda: None)
        panel.ppms_instrument_controllers["6221"] = source
        panel.zurich_controller = types.SimpleNamespace(mfli_connected=meter == "zurich", lock=threading.Lock(), mfli_has_multi_demod=lambda: True,
                                                        disconnect_mfli=lambda: None)
        panel.zurich_timeconstant.set(tc_zurich)
        if meter == "zurich":
            # The Zurich alone, chosen here: SHHV starts with SR830-1 and
            # SR830-2 measured since 2026-10-20 (user's figure).
            for key in app.PPMS_SR830_KEYS:
                panel.ppms_lockin_selection[key].set(False)
            panel.ppms_lockin_selection["zurich"].set(True)
        if meter == "sr830":
            panel.ppms_instrument_controllers["830_a"] = types.SimpleNamespace(
                connected=True, label="SR830-1", resource_name="GPIB0::8::INSTR", disconnect=lambda: None)
            for key in app.PPMS_SR830_KEYS:
                panel.ppms_lockin_selection[key].set(key == "830_a")
            panel.ppms_lockin_selection["zurich"].set(False)
            panel.ppms_transport_harmonic_a_var.set("1+2+3")
            panel.ppms_sr830_time_constant_codes = {"830_a": 0}     # 10 us: no real wait
            h.harmonic = {"830_a": 1}

            def write(key, command):
                h.log.append(("sr830", command, threading.get_ident()))
                if command.startswith("HARM "):
                    h.harmonic[key] = int(command.split()[1])

            panel._ppms_write_sr830_checked = write
        h.harmonic = h.__dict__.get("harmonic", {})

        def query(key, command, count):
            # The 6221's OUTP? after the end's off (user, 2026-10-18), and an
            # SR830's HARM? between harmonic passes.
            if command == "OUTP?":
                h.log.append(("6221", "OUTP?", threading.get_ident()))
                return (0,)
            assert command == "HARM?", command
            return (h.harmonic[key],)
        panel._ppms_query_numbers = query
        h.points = 0

        def sample(settings_lockins):
            h.points += 1
            # The Zurich's harmonics as the page has them (1+2 by default,
            # user's figure, 2026-10-18), one demodulator per order.
            orders = str(panel.zurich_harmonic.get())
            values = {"zurich_harmonic": orders}
            for demod in range(1, len(orders.split("+")) + 1):
                values.update({f"zurich_demod{demod}_x_v": demod * 1e-6 + h.points * 1e-9,
                               f"zurich_demod{demod}_y_v": -demod * 1e-6, f"zurich_demod{demod}_r_v": demod * 1.4e-6,
                               f"zurich_demod{demod}_theta_deg": -45.0, f"zurich_demod{demod}_harmonic": demod,
                               f"zurich_demod{demod}_input": 0})
            for key in settings_lockins:
                values.update({"sr830_8_x_v": 2e-6 + h.points * 1e-9, "sr830_8_y_v": 1e-6, "sr830_8_r_v": 2.2e-6,
                               "sr830_8_theta_deg": 26.0, "sr830_8_reference_valid": True})
            return values

        def configure(settings):
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))

        def first(frequency, lockins, duration):
            h.log.append(("lockins", "first", tuple(lockins), threading.get_ident()))
            return sample(lockins), 2

        def average(frequency, lockins, duration):
            h.log.append(("lockins", "read", tuple(lockins), threading.get_ident()))
            return sample(lockins)

        panel._configure_ppms_transport_scan = configure
        panel._acquire_ppms_first_point = first
        panel._average_ppms_transport_sample = average
        panel._stop_ppms_transport_source = lambda: h.log.append(("6221", "off", threading.get_ident()))
        panel._read_ppms_current_display = lambda: "ON"
        # The page as the user leaves it before Start.
        panel.ppms_control_safety_var.set(True)
        panel.rte_drive_target.set("1.5")
        panel.rte_voltage_limit.set("5")
        panel.ppms_field_scan_stable_var.set("0")
        panel.ppms_acquisition_time_var.set("0.01")
        panel.rte_connect = None
        panel._rte_connect("newmark")
        self.pump(h)
        self.assertTrue(stage.connected, h.showerror.call_args)
        # The 0.01 s typed above stays through the mode changes: a mode puts
        # its own Acquire s in the box only while the box still holds the
        # last mode's (2026-11-22; test_rte_angle_scan's mode tests).
        panel.ppms_simple_mode.set("Angle")
        panel._rte_mode_changed()
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel.ppms_field_scan_loop_var.set(loops)
        panel.rte_round_trip.set(round_trip)
        h.log.clear()
        return h

    def pump(self, h, until=None, limit=5000):
        """As the program's Tk thread, until nothing is left: wait for the
        worker, run the post_ui queue (drain_ui_queue), then what
        root.after(0, ...) scheduled (the Tk event loop); the idle poll's
        1 s timer stays queued."""
        for _ in range(limit):
            h.executor.submit(lambda: None).result(timeout=120)
            moved = False
            while h.ui:
                h.in_drain = True
                try:
                    h.ui.pop(0)()
                finally:
                    h.in_drain = False
                moved = True
            ready = [item for item in h.root.pending if item[0] < 1000]
            for item in ready:
                h.root.pending.remove(item)
                item[1]()
                moved = True
            if until is not None and until():
                return
            if not moved and not h.panel.ppms_field_scan_running:
                return
        raise AssertionError("the page did not settle")

    def start(self, h):
        h.panel.start_ppms_field_scan()
        self.pump(h)

    @staticmethod
    def rows(h):
        path = h.panel.ppms_live_csv_path
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def names(h, *kinds):
        return [entry[:2] for entry in h.log if entry[0] in kinds]


class AngleScanTests(EndToEnd):
    pass

class AngleScanStopTests(EndToEnd):
    pass

def fail_next_log_write(recorder, failures=1):
    """The run log's next ``failures`` writes raise, as a full disk would."""
    real = recorder._append
    left = [failures]

    def append(text):
        if left[0]:
            left[0] -= 1
            raise OSError(28, "No space left on device")
        return real(text)
    recorder._append = append


class RunLogBase(EndToEnd):
    """The page of the scans above, with which thread wrote each log event
    and Field and Current scans on it."""

    def make(self, *args, **kwargs):
        h = super().make(*args, **kwargs)
        # Which thread wrote each event, from the recorder's own event().
        h.writes = []
        real = app.SHHVScanRecorder.event

        def event(recorder, name, **details):
            h.writes.append((name, threading.get_ident()))
            return real(recorder, name, **details)
        guard = patch.object(app.SHHVScanRecorder, "event", event)
        guard.start()
        self.addCleanup(guard.stop)
        real_close = app.SHHVScanRecorder.close

        def close(recorder, outcome, **details):
            h.writes.append(("close", threading.get_ident()))
            return real_close(recorder, outcome, **details)
        guard = patch.object(app.SHHVScanRecorder, "close", close)
        guard.start()
        self.addCleanup(guard.stop)
        return h

    def make_mode(self, mode, span, **kwargs):
        """A Field or Current scan on the same page (the KEPCO, the LS475
        and the 6221 fakes); the stage stays connected and still."""
        h = self.make(**kwargs)
        panel = h.panel
        panel.ppms_simple_mode.set(mode)
        panel._rte_mode_changed()
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel._update_ppms_transport_current = lambda settings: h.log.append(
            ("6221", "current", settings["current_rms_ma"], threading.get_ident()))
        h.log.clear()
        return h

    @staticmethod
    def events(h):
        folder = h.panel.rte_run_folder
        with (folder / f"{folder.name}.log").open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    @staticmethod
    def without_notes(events):
        return [event["event"] for event in events if event["event"] != "note"]

    def assert_closed(self, h):
        self.assertIsNone(h.panel.rte_recorder, "the page let the recorder go")
        self.assertEqual(self.events(h)[-1]["event"], "run_finished")


class RunLogTests(RunLogBase):
    """The SHHV run folder and its log (user, 2026-10-18: "like ST-FMR, pls
    also write a log of SHHV and save results in a folder"), on the real
    page, worker and stage controller of the scans above."""

    pass

class ScanEndZeroTests(RunLogBase):
    """User, 2026-10-18: "when finish scan in SHHV, automatically zero the
    Kepco and 6221 pls": each ending zeroes the 6221 (off, read back), then
    the KEPCO (its Zero / OFF), on the worker, each step tried whatever the
    other did; a zero that fails is said on the status line, in a dialog
    and in the log."""

    pass

class OuterScanTests(RunLogBase):
    """Angle+Field and Angle+Current, end to end (user, 2026-10-18: "add the
    angle + field scan mode, field is the outside loop"; "also angle+current
    mode, the current is outside loop"), on the real page, worker and stage
    controller of the scans above."""

    def make_outer(self, mode, outer, span=("0", "10", "10"), **kwargs):
        h = self.make(span=span, round_trip=False, **kwargs)
        panel = h.panel
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set(mode)
            panel._rte_mode_changed()
        self.assertIs(panel.rte_round_trip.get(), False, "round trip off by default")
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        for variable, value in zip(panel.rte_outer_ranges[mode], outer):
            variable.set(value)
        h.configured, h.updated = [], []

        def configure(settings):
            h.configured.append(dict(settings))
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))

        def update(settings):
            h.updated.append(dict(settings))
            h.log.append(("6221", "current", settings["current_rms_ma"], threading.get_ident()))
        panel._configure_ppms_transport_scan = configure
        panel._update_ppms_transport_current = update
        h.log.clear()
        return h

    @staticmethod
    def sequence(h):
        """The calls in order, without their threads: a move's angle, a
        KEPCO step's and a 6221 change's current, the 6221 on's current, the
        mode of set_output."""
        calls = []
        for entry in h.log:
            if entry[0] in ("gauss", "lockins") or entry[1] in ("off", "OUTP?", "force_off"):
                calls.append(entry[:2])
            elif entry[1] in ("on", "set_output"):
                calls.append(entry[:3])
            else:
                calls.append(entry[:-1])
        return calls

    @staticmethod
    def point(angle, first=False):
        return [("stage", "move", angle), ("stage", "at", angle), ("gauss", "read"),
                ("lockins", "first" if first else "read"), ("gauss", "read")]

    def make_field_list(self, typed, gauss_class=None, **kwargs):
        """An Angle+Field scan whose LS475 reads 100 G per A of the KEPCO's
        current (FollowingGauss), with the field setting's waits made tiny
        (not 0: read_settled_magnet_field divides its timeout by them)."""
        h = self.make_outer("Angle+Field", (typed,), **kwargs)
        h.gauss = (gauss_class or FollowingGauss)(h.log, h.kepco)
        h.panel.rte_controllers["gauss"] = h.gauss
        for name, value in (("MAGNET_FIELD_SET_SETTLE_S", 0.0), ("MAGNET_FIELD_SET_READ_INTERVAL_S", 1e-4),
                            ("MAGNET_FIELD_SET_GROUP_GAP_S", 1e-4)):
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return h

    def make_field_angle(self, typed, span=("-1", "1", "1"), trip=False, **kwargs):
        """A Field+Angle scan (user, 2026-11-22): the typed stage angles
        outside, the KEPCO's currents of ``span`` at each, with an LS475
        that reads 100 G per A of the KEPCO's current."""
        h = self.make(span=span, round_trip=False, **kwargs)
        panel = h.panel
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set("Field+Angle")
            panel._rte_mode_changed()
        self.assertIs(panel.rte_round_trip.get(), True, "a field sweep goes and comes back by default")
        panel.rte_round_trip.set(trip)
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel.rte_outer_ranges["Field+Angle"][0].set(typed)
        h.gauss = FollowingGauss(h.log, h.kepco)
        panel.rte_controllers["gauss"] = h.gauss
        h.configured = []

        def configure(settings):
            h.configured.append(dict(settings))
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))
        panel._configure_ppms_transport_scan = configure
        h.log.clear()
        return h

    def make_field_current(self, typed, span=("-1", "1", "1"), trip=False, **kwargs):
        """A Field+Current scan (user, 2026-11-25): the typed 6221 read
        currents outside, the KEPCO's currents of ``span`` at each."""
        h = self.make(span=span, round_trip=False, **kwargs)
        panel = h.panel
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set("Field+Current")
            panel._rte_mode_changed()
        self.assertIs(panel.rte_round_trip.get(), True, "a field sweep goes and comes back by default")
        self.assertEqual(panel.rte_outer_ranges["Field+Current"][0].get(), "0.5, 1, 1.5, 2")
        panel.rte_round_trip.set(trip)
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel.rte_outer_ranges["Field+Current"][0].set(typed)
        h.gauss = FollowingGauss(h.log, h.kepco)
        panel.rte_controllers["gauss"] = h.gauss
        h.configured, h.updated = [], []

        def configure(settings):
            h.configured.append(dict(settings))
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))

        def update(settings):
            h.updated.append(dict(settings))
            h.log.append(("6221", "current", settings["current_rms_ma"], threading.get_ident()))
        panel._configure_ppms_transport_scan = configure
        panel._update_ppms_transport_current = update
        h.log.clear()
        return h

def panel_current(panel):
    """The 6221 current the page's box holds (the Field and Angle scans' excitation)."""
    return float(panel.ppms_transport_current_var.get())


if __name__ == "__main__":
    unittest.main()
