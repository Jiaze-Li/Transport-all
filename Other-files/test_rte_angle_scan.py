"""RT-E Angle scan with the Newmark NSC-A1 rotation stage (user, 2026-10-13).

The program's NewmarkNSCA1Controller runs on a fake PerformaxCom.dll: the
reviewed simulated controller of Newmark_motor_test.py (its
SimulatedPerformax answers the DLL's calls with the same ctypes buffers),
wrapped so that every call records the thread it came from.  No real DLL is
ever loaded (also on the Windows CI, where ctypes.WinDLL exists: it,
os.add_dll_directory and the program's loader are replaced for the whole
module), no serial or COM port is opened, and no window: tkinter.Tk and every
dialog the page can reach fail the test unless a test answers them itself.
Time is a virtual clock, so a 36 s turn takes no real time; past its limit a
sleep fails the test, so a lost time-out fails instead of hanging.
"""
import ast
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
import ctypes
import importlib.util
import inspect
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

SCRIPT = Path(__file__).resolve().parents[1] / "Newmark_motor_test.py"
_spec = importlib.util.spec_from_file_location("newmark_motor_test_for_rte", SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

REAL_LOADER = app.newmark_load_performax     # before the module guard replaces the name
_guards = []


def _refuse(what):
    def refuse(*_args, **_kwargs):
        raise AssertionError(f"a test reached {what}")
    return refuse


def setUpModule():
    targets = [(ctypes, "WinDLL", _refuse("ctypes.WinDLL (a real DLL)")),
               (os, "add_dll_directory", _refuse("os.add_dll_directory")),
               (app, "newmark_load_performax", _refuse("the program's real Newmark DLL loader")),
               (app.tk, "Tk", _refuse("tkinter.Tk (a window)"))]
    for name in ("showerror", "showinfo", "showwarning", "askyesno", "askokcancel", "askyesnocancel"):
        targets.append((app.messagebox, name, _refuse(f"messagebox.{name}")))
    for name in ("askdirectory", "asksaveasfilename", "askopenfilename"):
        targets.append((app.filedialog, name, _refuse(f"filedialog.{name}")))
    targets.append((app.simpledialog, "askstring", _refuse("simpledialog.askstring")))
    for target, name, replacement in targets:
        guard = patch.object(target, name, replacement, create=True)
        guard.start()
        _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


# ---- stand-ins -------------------------------------------------------------------
class Clock:
    """Virtual time for the stage and its simulator.  ``hooks`` are (time, fn)
    run once by the sleep that reaches that time (on whichever thread sleeps)."""

    LIMIT = 5000.0

    def __init__(self):
        self.t = 1000.0
        self.hooks = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        if self.t - 1000.0 > self.LIMIT:
            raise AssertionError("more than 5000 virtual seconds: a time-out that never fires?")
        self.t += seconds
        for hook in list(self.hooks):
            if self.t >= hook[0]:
                self.hooks.remove(hook)
                hook[1]()


# The driver read and its reads: never sent since the microstep is fixed (user, 2026-10-17).
DRIVER_READS = ("RR", "R2", "DRVMS", "DRVRC", "DRVIC", "DRVIT")
SCALE = ("10000 steps/° (72:1 x 200 full steps x microstep 250 / 360; microstep 250 from QuickMotion, fixed in "
         "the program)")


class Sim(script.SimulatedNSC):
    """The script's simulated NSC-A1 (DRVMS 250, the lab PC's QuickMotion
    microstep: 10000 steps/deg; the program never asks it for the driver)."""

    @property
    def moves(self):
        return [command for command in self.received if command.startswith(("X", "HL", "J"))]


class Stuck(Sim):
    """A move whose MST never says it ended, until STOP or ABORT."""

    def _finished(self, now):
        return self.motion["stopping"] and now >= self.motion["t1"]


class EndsShort(Sim):
    """Every move ends 7 steps short of its target (steps lost)."""

    def _end_position(self, motion):
        return motion["to"] if motion["stopping"] else motion["to"] - 7


class LateMovingBits(Sim):
    """After X it answers OK but reads "not moving" for 0.08 s before it starts."""

    def _start(self, target, now, homing=False, queue=()):
        self.motion = {"from": self.px, "to": self.px, "t0": now, "t1": now + 0.08, "homing": False,
                       "stopping": False, "idle": True,
                       "queue": [{"to": target, "duration": abs(target - self.px) / self.hspd + self.acc / 1000}]}


class LosesOne(Sim):
    """The USB transaction of ``command`` fails once (no reply)."""

    def __init__(self, command, **options):
        super().__init__(**options)
        self.lose, self.lost = command, False

    def send_recv(self, handle, data):
        command = bytes(data).split(b"\0", 1)[0].decode("ascii")
        if command == self.lose and not self.lost:
            self.lost = True
            self.received.append(command + " (lost)")
            return None
        return super().send_recv(handle, data)


class ReplyLost(Sim):
    """The controller runs ``command`` once, but its reply is lost (the USB
    transaction fails after it), as for an X whose move has started."""

    def __init__(self, command, **options):
        super().__init__(**options)
        self.lose, self.lost = command, False

    def send_recv(self, handle, data):
        reply = super().send_recv(handle, data)
        command = bytes(data).split(b"\0", 1)[0].decode("ascii")
        if command == self.lose and not self.lost and reply is not None:
            self.lost = True
            return None
        return reply


class Runaway(Stuck):
    """A move that never ends, and STOP and ABORT do nothing: the stage cannot
    be confirmed stopped."""

    def _stop(self, command, now):
        pass


class RunawayReplyLost(Runaway, ReplyLost):
    """X runs, its reply is lost, and STOP and ABORT do nothing."""


class StopLostInTheGap(LateMovingBits):
    """main's 15b88a1 simulator: a STOP in the gap after X, while MST still
    says "not moving", does nothing, and the move starts anyway.  ``on_x``
    runs right after each X is answered (a Stop pressed in that gap)."""

    def __init__(self, on_x=None, **options):
        super().__init__(**options)
        self.on_x = on_x

    def reply(self, command):
        text = super().reply(command)
        if command.startswith("X") and self.on_x is not None:
            self.on_x()
        return text

    def _stop(self, command, now):
        if not (command == "STOP" and self.motion is not None and self.motion.get("idle")):
            super()._stop(command, now)


class RecordingDLL:
    """PerformaxCom.dll as the script's simulator answers it; every call is
    recorded with the thread it came from."""

    def __init__(self, controller, missing=()):
        self.inner = script.SimulatedPerformax(controller)
        self.missing = set(missing)
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("fnPerformaxCom") or name in self.missing:
            raise AttributeError(name)
        function = getattr(self.inner, name)

        def call(*args):
            self.calls.append((name, threading.get_ident()))
            return function(*args)
        return call

    def names(self):
        return [name for name, _thread in self.calls]


class Recorder(rte_tests.Widget):
    """A stand-in widget that remembers every instance and where it was
    gridded (for the page build)."""

    made = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.grid_options = {}
        self.grid_removed = False
        Recorder.made.append(self)

    def grid(self, **kwargs):
        # As Tk: grid() with no options after grid_remove() puts the widget
        # back where it was.
        if kwargs or not self.grid_removed:
            self.grid_options = dict(kwargs)
        self.grid_removed = False

    def grid_remove(self):
        # Tk keeps the grid options of a removed widget (2026-10-18: the
        # SHHV outer row).
        self.grid_removed = True


class AfterRoot:
    """The page's root for the stage harness: ``after(0, fn)`` queues ``fn``
    for StageBase.drain to run from the "Tk event loop", outside the post_ui
    queue (review, 2026-10-17); no window."""

    def __init__(self, afters):
        self.afters = afters
        self.delays = []

    def after(self, delay, callback):
        self.delays.append(delay)
        self.afters.append(callback)
        return f"after#{len(self.delays)}"

    def after_cancel(self, _timer):
        pass


class DeferredExecutor:
    """The RT-E worker, run by the test: jobs wait in order until ``run()``,
    as behind an idle-poll read on the program's one worker (review,
    2026-10-17).  The scan's own job is only recorded, as StartExecutor's."""

    SCAN = rte_tests.StartExecutor.SCAN

    def __init__(self):
        self.jobs = []
        self.submit = Mock(side_effect=self._submit)

    def _submit(self, fn, *args):
        future = Future()
        if getattr(fn, "__name__", "") != self.SCAN:
            self.jobs.append((future, fn, args))
        return future

    def run(self):
        """Run every job queued so far, and those they queue, in order."""
        while self.jobs:
            future, fn, args = self.jobs.pop(0)
            try:
                future.set_result(fn(*args))
            except BaseException as error:
                future.set_exception(error)

    def scans(self):
        return [call.args for call in self.submit.call_args_list
                if getattr(call.args[0], "__name__", "") == self.SCAN]

    def shutdown(self, **_kwargs):
        pass


class StageBase(unittest.TestCase):
    def make(self, sim=None, executor=None, missing=()):
        """An RT-E page stand-in (test_rte_panel's) with the real stage controller
        on the fake DLL.  ``ui`` holds what the page posts to the Tk thread."""
        clock = Clock()
        guard = patch.object(script, "_now", clock.now)
        guard.start()
        self.addCleanup(guard.stop)
        sim = Sim() if sim is None else sim
        dll = RecordingDLL(sim, missing)
        log = []
        stage = app.NewmarkNSCA1Controller(log.append, loader=lambda: (dll, "fake PerformaxCom.dll"))
        stage._now, stage._sleep = clock.now, clock.sleep
        panel = rte_tests.RTE.make_worker(self)
        panel.rte_controllers["newmark"] = stage
        kepco = panel.rte_controllers["kepco"]
        kepco.set_output.side_effect = lambda mode, target, limit: setattr(kepco, "cached_current_a", target)
        panel.rte_stage_stop_event = threading.Event()
        panel.rte_stage_where = ""
        panel.rte_stage_live = Var("Stage: not connected")
        panel.rte_stage_target = Var("0")
        panel.rte_resources = {"newmark": Var("auto")}
        # The rack's Motor row says "Connected" / "Not connected" again since
        # its connection moved back to the rack (user, 2026-10-17); the
        # stage line in the scan box shows the reading.
        panel.rte_connections = {"newmark": Var("Not connected")}
        panel.rte_drive_live = Var("")
        panel.rte_field_live = Var("")
        panel.ppms_environment_busy = False
        panel.ppms_field_scan_running = False
        panel.ppms_control_safety_var = Var(True)
        panel._update_ppms_control_ui = Mock()
        panel.thread_log = Mock()
        afters = []
        panel.root = AfterRoot(afters)
        ui = []
        panel.post_ui = ui.append
        panel.ppms_executor = ImmediateExecutor() if executor is None else executor
        return types.SimpleNamespace(panel=panel, stage=stage, sim=sim, dll=dll, clock=clock, ui=ui, log=log,
                                     afters=afters, in_drain=False)

    @staticmethod
    def drain(h):
        """As the program's Tk thread: drain_ui_queue runs the post_ui
        callbacks (``in_drain`` meanwhile), then the Tk event loop runs what
        root.after(0, ...) scheduled, one at a time (review, 2026-10-17)."""
        while h.ui or h.afters:
            h.in_drain = True
            try:
                while h.ui:
                    h.ui.pop(0)()
            finally:
                h.in_drain = False
            if h.afters:
                h.afters.pop(0)()

    def settle(self, h, executor):
        """Wait until the one worker has run everything queued, then run the
        Tk-thread callbacks it posted, as the page's queue would."""
        executor.submit(lambda: None).result(timeout=30)
        self.drain(h)

    @staticmethod
    def settings():
        return app.validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, ("830_a",))


# ---- the driver ----------------------------------------------------------------
class NewmarkDriverTests(StageBase):

    def test_the_microstep_is_250_with_its_source(self):
        self.assertEqual(app.NEWMARK_MICROSTEP, 250)
        self.assertEqual(app.NEWMARK_MICROSTEP, script.MICROSTEP, "the program and the script agree")
        self.assertEqual(app.NEWMARK_STEPS_PER_DEGREE, 40 * app.NEWMARK_MICROSTEP)
        self.assertEqual(app.NEWMARK_STEPS_PER_DEGREE, 10000)
        self.assertEqual(app.NewmarkNSCA1Controller(lambda _text: None).steps_per_degree, 10000)
        program = Path(app.__file__).read_text(encoding="utf-8").splitlines()
        # The program's comments and docstrings were removed (user, 2026-11-22: "Minimize the total length of the Python source code"); their reasons live in git history and Other-files/README.md.
        self.assertTrue(any(line.startswith("NEWMARK_MICROSTEP = 250") for line in program))

class NewmarkLoaderTests(unittest.TestCase):
    def test_load_errors_are_explained(self):
        explain = app.newmark_explain_load_error
        self.assertIn("not a 64-bit DLL", explain("C:/lab/PerformaxCom.dll",
                                                  OSError("[WinError 193] %1 is not a valid Win32 application")))
        for error in (OSError("[WinError 126] The specified module could not be found"),
                      # What Python 3.8+ raises for it on Windows: no winerror, no
                      # "[WinError 126]" (user, 2026-10-13).
                      FileNotFoundError("Could not find module 'C:\\lab\\run1930\\PerformaxCom.dll' (or one of its "
                                        "dependencies). Try using the full path with constructor syntax.")):
            with self.subTest(error=error):
                text = explain("C:\\lab\\run1930\\PerformaxCom.dll", error)
                self.assertIn("it, or a DLL it needs, was not found (Windows error 126)", text)
                self.assertIn("PerformaxCom.dll needs SiUSBXp.dll, and Python does not search PATH", text)
                self.assertIn("both 64-bit DLLs together in the program's folder", text)
        self.assertNotIn("error 126", explain("PerformaxCom.dll", PermissionError("[WinError 5] Access is denied")))
        # A path holding "193" is not WinError 193.
        self.assertNotIn("64-bit", explain("C:/193/PerformaxCom.dll", OSError("[WinError 5] Access is denied")))

    def test_the_real_loader_only_on_windows_and_64_bit(self):
        with patch.object(app.ctypes, "WinDLL", None, create=True):
            with self.assertRaisesRegex(app.NewmarkStageError, "Windows DLL"):
                REAL_LOADER()
        loaded = []

        class FakeWinDLL:
            def __init__(self, path):
                loaded.append(path)
                if path.endswith("PerformaxCom.dll"):
                    raise OSError("[WinError 193] %1 is not a valid Win32 application")

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(app.ctypes, "WinDLL", FakeWinDLL, create=True), \
                patch.object(app.os, "add_dll_directory", Mock(return_value="cookie"), create=True) as add, \
                patch.object(app, "newmark_dll_folder", return_value=Path(folder)):
            for name in ("PerformaxCom.dll", "SiUSBXp.dll"):
                (Path(folder) / name).write_bytes(b"")
            with self.assertRaisesRegex(app.NewmarkStageError, "WinError 193.*not a 64-bit DLL"):
                REAL_LOADER()
            add.assert_called_once_with(folder)
            self.assertEqual([Path(p).name for p in loaded], ["SiUSBXp.dll", "PerformaxCom.dll"],
                             "SiUSBXp.dll first, from the same folder")
            with patch.object(app.ctypes, "sizeof", lambda _type: 4):
                with self.assertRaisesRegex(app.NewmarkStageError, "32-bit Python"):
                    REAL_LOADER()

        def not_found(path):
            raise FileNotFoundError(f"Could not find module '{path}' (or one of its dependencies). Try using the "
                                    "full path with constructor syntax.")

        # Not in the program's folder: the normal search, and Python 3.8+'s
        # error 126, now named as such with where the DLLs go (user, 2026-10-17).
        with patch.object(app.ctypes, "WinDLL", not_found, create=True), \
                patch.object(app, "newmark_dll_folder", return_value=None):
            with self.assertRaisesRegex(app.NewmarkStageError, r"PerformaxCom\.dll was not found: it is not in "
                                        r"the program's folder"):
                REAL_LOADER()


# ---- every DLL call on the RT-E worker -------------------------------------------
class StageThreadAffinityTests(StageBase):
    pass


# ---- the Motor row and the device owner (user, 2026-10-17) -----------------------
class MotorRowOwnershipTests(StageBase):
    """User, 2026-10-17: "add the connection button for Motor in RT-E and
    ST-FMR modules".  The USB device opens on one page at a time: the RT-E
    page claims it before its Connect job is queued and releases it once the
    handle is closed; a Connect while the STFMR page has it is refused."""

    pass

# ---- the scan plan ---------------------------------------------------------------
class AnglePlanTests(unittest.TestCase):
    pass

# ---- the Angle scan worker, end to end --------------------------------------------
class AngleScanWorkerTests(StageBase):
    def run_scan(self, sim=None, plan=None, passes=None, h=None):
        h = h or self.make(sim)
        if not h.stage.connected:
            h.stage.connect("auto")
        plan = plan or app.rte_scan_plan("Angle", 0, 10, 5, 1, True)
        h.panel._rte_harmonic_scan_worker("Angle", plan, passes or [self.settings()], 5., 0., .001, 1.5)
        return h

    def assert_zeroed_at_the_end(self, panel, failure_offs=0):
        """Every ending, completed, stopped or failed, zeroes the 6221 (off,
        OUTP? read back) and then the KEPCO (its Zero / OFF button's
        force_output_off) on the worker (user, 2026-10-18), after the
        failure's own clean-up (``failure_offs`` 6221 offs); the stage is
        not moved for it."""
        self.assertEqual(panel._stop_ppms_transport_source.call_count, failure_offs + 1)
        self.assertEqual(panel._ppms_query_numbers.call_args.args, ("6221", "OUTP?", 1))
        panel.rte_controllers["kepco"].force_output_off.assert_called_once_with()
        self.assertEqual(panel.rte_scan_outputs, {"6221": "zeroed", "kepco": "zeroed"})

# ---- Move / Stop / Set 0 by hand ---------------------------------------------------
class ManualStageControlTests(StageBase):
    pass

# ---- Start (Angle), status lines, defaults, plots ---------------------------------
class AngleStartTests(unittest.TestCase):
    def start(self, mode="Angle", yes=True, stage=True, kepco=True, gauss=True, scale=10000,
              span=("0", "10", "5"), drive="1.5", limit="5", executor=None, may_run=False, owner=None,
              outer=None, trip=True):
        """The real RTEPanel.start_ppms_field_scan up to its submit (as in
        test_ppms_rte_review), with every dialog answered here.  ``owner``:
        the page that has the motor's USB device (user, 2026-10-17).
        ``outer``: the outer row of Angle+Field / Angle+Current (user,
        2026-10-18)."""
        panel = rte_tests.RTE.make_worker(self)
        if owner is not None:
            panel._newmark_stage_ownership().claim(owner)
        panel._require_ppms_control_ready = Mock()
        panel._rte_capture_zurich = Mock()
        panel.zurich_scan_enabled = False
        panel.ppms_simple_mode = Var(mode)
        panel.ppms_simple_range = [Var(v) for v in span]
        panel.ppms_field_scan_loop_var = Var("1")
        panel.rte_round_trip = Var(trip)
        if outer is not None:
            panel.rte_outer_ranges = {mode: [Var(v) for v in outer]}
        panel.rte_controllers["gauss"].connected = gauss
        panel.rte_controllers["kepco"].connected = kepco
        panel.rte_controllers["newmark"] = Mock(connected=stage, steps_per_degree=scale, motion_may_run=may_run,
                                                label="Newmark NSC-A1")
        panel.rte_stage_stop_event = threading.Event()
        panel.rte_stage_stop_event.set()
        panel.rte_drive_target, panel.rte_voltage_limit = Var(drive), Var(limit)
        settings = app.validate_ppms_transport_scan_settings(.1, 17.3, 50., 1, 2, ("830_a",))
        panel._ppms_direct_transport_settings = Mock(return_value=settings)
        panel._rte_harmonic_passes = Mock(return_value=[settings])
        panel.ppms_field_scan_stable_var = Var("0")
        panel.ppms_acquisition_time_var = Var("1")
        panel.ppms_field_scan_progress = Mock()
        panel.ppms_field_scan_status_var = Var("")
        panel._update_ppms_control_ui = Mock()
        panel._redraw_ppms_transport_plot = Mock()
        panel.thread_log = Mock()
        # The stage check at an Angle Start runs at once; the scan is only
        # recorded (user, 2026-10-17).
        panel.ppms_executor = rte_tests.StartExecutor() if executor is None else executor
        panel.ppms_environment_busy = panel.ppms_field_scan_running = False
        panel.post_ui = lambda callback: callback()
        panel.root = None
        # The run folder lives until the test ends: a scan the test finishes
        # later writes its run log's last entry there (2026-10-18).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(app.filedialog, "askdirectory", return_value=tmp.name) as askdirectory, \
                patch.object(app.simpledialog, "askstring", return_value=""), \
                patch.object(app.messagebox, "showerror") as showerror, \
                patch.object(app.messagebox, "askyesno", return_value=yes) as askyesno:
            panel.start_ppms_field_scan()
        return types.SimpleNamespace(panel=panel, showerror=showerror, askyesno=askyesno,
                                     askdirectory=askdirectory, folder=tmp.name)

    # ---- Angle+Field and Angle+Current (user, 2026-10-18) ----------------------

class AnglePageTests(unittest.TestCase):

    def build_page(self):
        """The real RTEPanel constructor on stand-in widgets (as test_rte_panel's)."""
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
            # The KEPCO 4886 path (SHHV starts on the LS643 since 2026-10-23).
            panel.rte_magnet_choice_var.set(app.MAGNET_SUPPLY_KEPCO)
            panel._rte_magnet_choice_changed()
            return panel

# ---- a latched limit error or the driver off, fixed on the page (user, 2026-10-17) ----
# The user asked that the program not depend on Newmark_motor_test.py, whose
# "clear" and "enable" the old refusals named.  Move stage and an Angle scan's
# Start now ask Yes/No themselves; Yes sends CLR or EO=1 on the RT-E worker,
# reads it back, and then goes on; No sends nothing.
LATCHED = "SHHV stage limit error"
DRIVER = "SHHV stage motor driver"
SCAN = "SHHV Angle scan"
LATCHED_QUESTION = ("A limit error is latched on the stage (MST 64: not moving, -limit ERROR (latched)). Clear it now "
                    "(CLR)?")
# Yes goes on at once, so the warning about the angle comes in the question,
# before it (review, 2026-10-17).
DRIVER_QUESTION = ("The stage's motor driver is off (EO=0). Switch it on now (EO=1) and go on? The motor then holds "
                   "the stage with its current.\n\nIf the driver went off during a move, the angle shown may not be "
                   "the stage's: answer No and check it first (Set stage 0° if needed).")
TICK = "Tick the SHHV rack safety confirmation first"
ACTIONS = ("CLR", "EO=0", "EO=1", "STOP", "ABORT", "PX=0")


def actions(received):
    """The commands that change something (fixes, stops, the zero, moves), in order."""
    return [command for command in received if command in ACTIONS or command.startswith(("X", "HL", "J"))]


def latch(sim):
    sim.latched = 1 << 6


def driver_off(sim):
    sim.eo = 0


class Watched(Sim):
    """``watch(command)`` is called as each command arrives (from the thread that sent it)."""

    watch = None

    def reply(self, command):
        if self.watch is not None:
            self.watch(command)
        return super().reply(command)


class ClrDoesNotClear(Watched):
    """CLR is answered OK, but the limit error stays latched."""

    def reply(self, command):
        return "OK" if command == "CLR" else super().reply(command)


class EoStaysOff(Watched):
    """EO=1 is answered OK, but EO still reads 0."""

    def reply(self, command):
        return "OK" if command == "EO=1" else super().reply(command)


class Relatches(Watched):
    """CLR clears the limit error, but it is latched again at the next ABS,
    as if its cause were still there."""

    cleared = False

    def reply(self, command):
        if command == "ABS" and self.cleared:
            self.latched = 1 << 6
        text = super().reply(command)
        self.cleared = self.cleared or command == "CLR"
        return text


class OpenFails(Sim):
    """fnPerformaxComOpen fails (another program has the device)."""

    def open(self, index):
        return None


class Questions:
    """messagebox.askyesno, answered by title.  Each question is recorded with
    how many commands had been sent by then, whether the rack was busy, and
    ``snapshot()``.  ``before(title, h)`` runs first (e.g. to untick the safety)."""

    def __init__(self, h, answers, before=None, snapshot=None):
        self.h, self.answers, self.before, self.snapshot = h, dict(answers), before, snapshot
        self.asked = []

    def __call__(self, title, message, parent=None, **_options):
        # in_drain: asked from inside drain_ui_queue (it must not be);
        # prompt_open: the buttons locked while it is open (review, 2026-10-17).
        self.asked.append(types.SimpleNamespace(
            title=title, message=message, sent=len(self.h.sim.received), busy=self.h.panel.ppms_environment_busy,
            snapshot=self.snapshot() if self.snapshot else None, in_drain=self.h.in_drain,
            prompt_open=self.h.panel.__dict__.get("rte_stage_prompt_open")))
        if self.before is not None:
            self.before(title, self.h)
        return self.answers[title]

    def titles(self):
        return [question.title for question in self.asked]

    def after(self, title):
        """What was sent after the (first) question with this title."""
        return self.h.sim.received[next(q.sent for q in self.asked if q.title == title):]


NOT_CONFIRMED = ("The Newmark NSC-A1's last stop was not confirmed, so it may still be turning: press Stop stage first "
                 "(check the stage)")
BUSY = "SHHV is busy; wait or stop the scan first"
# What the checks before the question and after its Yes refuse (review,
# 2026-10-17): (name, change(h), the error dialog's text or None for none).
RECHECKS = (("an unconfirmed stop", lambda h: setattr(h.stage, "motion_may_run", True), NOT_CONFIRMED),
            ("a move running", lambda h: setattr(h.stage, "in_move", True), NOT_CONFIRMED),
            # The page's view: the handle gone, as after a Disconnect.
            ("the stage disconnected", lambda h: setattr(h.stage, "handle", None), "Connect the Newmark NSC-A1 first"),
            ("the rack busy", lambda h: setattr(h.panel, "ppms_environment_busy", True), BUSY),
            ("a scan running", lambda h: setattr(h.panel, "ppms_field_scan_running", True), BUSY),
            # A closing page shows no dialog.
            ("the program closing", lambda h: setattr(h.panel, "closing", True), None),
            ("the page closed", lambda h: setattr(h.panel, "rte_closed", True), None))
FIXES = ((latch, LATCHED, "CLR"), (driver_off, DRIVER, "EO=1"))
PRESSES = ("Stop stage", "KEPCO Zero / OFF", "6221 / 2400 OFF", "close")


def press(h, name):
    """A button that stays usable while a fix job waits on the worker (review, 2026-10-17)."""
    panel = h.panel
    if name == "Stop stage":
        panel._rte_stage_stop()
    elif name == "KEPCO Zero / OFF":
        panel._rte_zero()
    elif name == "6221 / 2400 OFF":
        # ppms_sources_off sets the scan's stop event first, then queues its
        # OFF (with the 6221 and 2400 controllers this harness has not).
        panel.ppms_field_scan_stop_event.set()
    else:
        panel.zurich_executor, panel.zurich_controller = Mock(), Mock()
        panel.safe_close_ppms()


def drain_ui_only(h):
    """The post_ui callbacks alone (one drain_ui_queue), not what they
    scheduled with root.after."""
    while h.ui:
        h.ui.pop(0)()


class StageFixDriverTests(StageBase):
    pass

class StageFixMoveTests(StageBase):
    """Move stage with a latched limit error or the driver off (user, 2026-10-17)."""

    def move(self, sim=None, answers=(), arrange=(), before=None, target="10", safety=True, prepare=None):
        h = self.make(Watched() if sim is None else sim)
        h.stage.connect("auto")
        for step in arrange:
            step(h.sim)
        if prepare is not None:
            prepare(h)
        h.panel.rte_stage_target.set(target)
        h.panel.ppms_control_safety_var = Var(safety)
        questions = Questions(h, answers, before)
        h.start = len(h.sim.received)
        with patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_move()
            self.drain(h)
        h.questions, h.showerror = questions, showerror
        return h

    # ---- review, 2026-10-17 ------------------------------------------------------
    def start_move(self, arrange, answers=(), executor=None):
        """Move stage to 10° up to its first job; returns (h, questions, showerror patch)."""
        h = self.make(Watched(), executor=executor)
        h.stage.connect("auto")
        arrange(h.sim)
        h.panel.rte_stage_target.set("10")
        return h, Questions(h, answers)

class StageFixAngleStartTests(StageBase):
    """An Angle scan's Start with a latched limit error or the driver off: the
    stage check runs before any instrument is set up (user, 2026-10-17)."""

    def equip(self, h):
        """The real start_ppms_field_scan on the stage harness, its scan only recorded."""
        panel = h.panel
        panel._rte_capture_zurich = Mock()
        panel.zurich_scan_enabled = False
        panel.ppms_simple_mode = Var("Angle")
        panel.ppms_simple_range = [Var(v) for v in ("0", "10", "5")]
        panel.ppms_field_scan_loop_var = Var("1")
        panel.rte_round_trip = Var(True)
        panel.rte_controllers["gauss"].connected = True
        panel.rte_controllers["kepco"].connected = True
        panel.rte_drive_target, panel.rte_voltage_limit = Var("1.5"), Var("5")
        settings = app.validate_ppms_transport_scan_settings(.1, 17.3, 50., 1, 2, ("830_a",))
        panel._ppms_direct_transport_settings = Mock(return_value=settings)
        panel._rte_harmonic_passes = Mock(return_value=[settings])
        panel.ppms_field_scan_stable_var = Var("0")
        panel.ppms_acquisition_time_var = Var("1")
        panel.ppms_field_scan_progress = Mock()
        panel.ppms_field_scan_status_var = Var("")
        panel._redraw_ppms_transport_plot = Mock()
        panel._rte_show_point = Mock()

    def start(self, sim=None, answers=(), arrange=(), before=None, safety=True, executor=None, settle=None,
              prepare=None):
        executor = rte_tests.StartExecutor() if executor is None else executor
        h = self.make(Watched() if sim is None else sim, executor=executor)
        if isinstance(executor, ThreadPoolExecutor):
            executor.submit(h.stage.connect, "auto").result(timeout=30)
        else:
            h.stage.connect("auto")
        self.equip(h)
        for step in arrange:
            step(h.sim)
        if prepare is not None:
            prepare(h)
        h.panel.ppms_control_safety_var = Var(safety)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        h.folder = folder.name
        kepco = h.panel.rte_controllers["kepco"]
        snapshot = lambda: dict(csv=os.listdir(folder.name), kepco=kepco.set_output.call_count,
                                source=h.panel._configure_ppms_transport_scan.call_count,
                                scans=len(executor.scans()) if hasattr(executor, "scans") else None)
        questions = Questions(h, {SCAN: True, **dict(answers)}, before, snapshot)
        h.start = len(h.sim.received)
        with patch.object(app.filedialog, "askdirectory", return_value=folder.name), \
                patch.object(app.simpledialog, "askstring", return_value=""), \
                patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel.start_ppms_field_scan()
            if settle is None:
                self.drain(h)
            else:
                settle(h)
        h.questions, h.showerror, h.executor = questions, showerror, executor
        return h

    def assert_not_started(self, h):
        self.assertEqual(h.executor.scans(), [])
        self.assertFalse(h.panel.ppms_field_scan_running)
        self.assertEqual(os.listdir(h.folder), [], "no CSV made")
        h.panel.rte_controllers["kepco"].set_output.assert_not_called()

    def assert_started(self, h):
        scans = h.executor.scans()
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0][1], "Angle")
        self.assertTrue(h.panel.ppms_field_scan_running)
        self.assertEqual(len(os.listdir(h.folder)), 1)

    # ---- review, 2026-10-17 ------------------------------------------------------
    @staticmethod
    def during_the_check(event_name):
        """prepare: KEPCO Zero / OFF (the scan's stop event) or Stop stage
        pressed while the check reads MST."""
        def prepare(h):
            event = getattr(h.panel, event_name)
            h.sim.watch = lambda command: event.set() if command == "MST" else None
        return prepare

class StageFixThreadTests(StageBase):
    """With a real single-worker executor, as the program's: every DLL call of
    the fixes and the move after them runs on the worker, and Stop stage
    still stops a move that follows a fix (user, 2026-10-17)."""

    pass


class ProgramIndependenceTests(unittest.TestCase):
    """The program needs no Newmark test script (user, 2026-10-17): it never
    imports it, and no text it shows or raises names it.  A comment may name
    it once, as the separate, optional test tool the driver came from."""

    def test_no_import_and_no_string_names_the_test_script(self):
        path = Path(app.__file__)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                self.assertNotIn("motor_test", name.lower(), f"line {node.lineno}")
        # Every string constant: messages, f-string parts, docstrings.
        strings = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.assertGreater(len(strings), 1000)
        named = [(node.lineno, node.value) for node in strings if "motor_test" in node.value.lower()]
        self.assertEqual(named, [])
        # The comment that named the test script went with every comment
        # (user, 2026-11-22: "Minimize the total length of the Python source
        # code"); nothing else may name it.
        self.assertNotIn("motor_test", source.lower())


class CloseTests(StageBase):
    pass


# ---- Angle+Field and Angle+Current: the plan, the outer row, the plot (user, 2026-10-18) ----
class OuterPlanTests(unittest.TestCase):
    pass

class OuterListTests(unittest.TestCase):
    """Angle+Field (user, 2026-11-17: "add a Angle+Field mode, which scan
    the field order manually input"; "I type the field directly and it auto
    reach the current"): the outer fields in G typed as a list, in any
    order, each reached as Set field reaches it."""

    pass

class OuterRowPageTests(unittest.TestCase):
    """The outer row on the real page built from stand-ins (user, 2026-10-18):
    removed in Field, Current and Angle, shown in Angle+Field and
    Angle+Current with each mode's own label and variables."""

    def mode(self, panel, mode):
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set(mode)
            panel._rte_mode_changed()

# ---- Go to 0° (user, 2026-10-18: "add a zero button here to move to stage 0-degree position") ----
class GoToZeroTests(StageBase):
    pass

if __name__ == "__main__":
    unittest.main()
