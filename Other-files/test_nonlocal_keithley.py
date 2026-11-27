"""Non-local page: |R| vs time (moved from MOKE-24) and the I-V sweep.

Both run on the Gate-VI Keithley (NonLocalVI2410Controller).  No Tk and no
instrument: a stand-in canvas records the drawing, and a fake SourceMeter
answers the SCPI commands.
"""
import csv
import threading
from pathlib import Path
import unittest
from concurrent.futures import Future
from unittest import mock

from test_ppms_motor_debug import app


class Canvas:
    def __init__(self, width=1900, height=260):
        self.items = []
        self.size = (width, height)

    def delete(self, *args):
        self.items.clear()

    def winfo_width(self):
        return self.size[0]

    def winfo_height(self):
        return self.size[1]

    def create_line(self, *args, **kwargs):
        self.items.append(("line", len(args), kwargs.get("fill")))

    def create_text(self, *args, **kwargs):
        self.items.append(("text", 0, kwargs.get("text")))

    def create_rectangle(self, *args, **kwargs):
        self.items.append(("rectangle", 0, kwargs.get("fill")))

    def create_oval(self, *args, **kwargs):
        self.items.append(("oval", 0, None))

    def texts(self):
        return [text for kind, _, text in self.items if kind == "text"]


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def bare_app():
    app_ = app.CombinedControlApp.__new__(app.CombinedControlApp)
    app_._canvas_is_visible = lambda canvas: True
    app_._draw_plot_grid = lambda *args: None
    app_.ui_font_family, app_.ui_font_size, app_.ui_small_font_size = "Segoe UI", 10, 9
    return app_


class FakeSourceMeter:
    """A 2410/2400 on the pulse page: a 1 GΩ sample in voltage mode, so the
    current is V / 1 GΩ (nA), clamped at the current limit."""

    def __init__(self, resistance_ohm=1e9):
        self.commands = []
        self.resistance_ohm = resistance_ohm
        self.voltage = 0.0
        self.current_source = 0.0
        self.limit = 1e-3
        self.ovp = 210.0
        self.range = 21.0
        self.output = 0
        self.mode = "VOLT"

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        value = command.rsplit(" ", 1)[-1]
        if upper == "*RST":
            self.output, self.voltage, self.ovp, self.mode, self.range = 0, 0.0, 210.0, "VOLT", 21.0
        elif upper.startswith(":SOUR:FUNC "):
            self.mode = value.upper()
        elif upper.startswith(":SOUR:VOLT:PROT "):
            self.ovp = float(value)
        elif upper.startswith(":SOUR:VOLT:RANG "):
            # The range's full scale, as a 2400 reads it back (21, 210, 1100 V).
            self.range = float(value) * 1.05 if float(value) < 1000 else 1100.0
        elif upper.startswith(":SOUR:VOLT "):
            self.voltage = float(value)
        elif upper.startswith(":SOUR:CURR "):
            self.current_source = float(value)
        elif upper.startswith(":SENS:CURR:PROT "):
            self.limit = float(value)
        elif upper == ":OUTP ON":
            self.output = 1
        elif upper == ":OUTP OFF":
            self.output = 0

    def query(self, command):
        upper = command.upper()
        if upper == "SYST:ERR?":
            return '0,"No error"'
        if upper == ":OUTP?":
            return str(self.output)
        if upper == ":SOUR:VOLT:PROT?":
            return str(self.ovp)
        if upper == ":SOUR:VOLT:RANG?":
            return str(self.range)
        if upper == ":SENS:CURR:PROT?":
            return str(self.limit)
        if upper == ":READ?":
            if self.output == 0:
                # A 2400/2410 refuses a reading with the output off.
                raise RuntimeError('-803,"Not permitted with OUTPUT off"')
            if self.mode == "CURR":
                if self.resistance_ohm == float("inf"):  # open: at the 5 V limit, no current
                    return "5.000000e+00,0.000000e+00"
                return f"{self.current_source * self.resistance_ohm:.6e},{self.current_source:.6e}"
            current = self.voltage / self.resistance_ohm
            current = max(-self.limit, min(self.limit, current))
            return f"{self.voltage:.6e},{current:.6e}"
        raise AssertionError(f"unexpected query: {command}")

    def close(self):
        pass


def connected_controller(meter):
    controller = app.NonLocalVI2410Controller(lambda _message: None)
    controller.instrument = meter
    controller.resource_name = "GPIB0::24::INSTR"
    controller.identity = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1,C30"
    controller.source_mode = "VOLT"
    return controller


# ------------------------------------------------------------- |R| vs time
class ResistancePlotTests(unittest.TestCase):
    def draw(self, rows):
        app_ = bare_app()
        app_.nonlocal_resistance_canvas = Canvas()
        app_.nonlocal_resistance_rows = rows
        with mock.patch.object(app, "np", None):
            app_._redraw_nonlocal_resistance_plot()
        return app_.nonlocal_resistance_canvas.items

class FinishTests(unittest.TestCase):
    """The finish reports the end's zeroing, done on the worker (user,
    2026-10-18: every scan ends with both outputs zeroed)."""

    def finished(self, kind, error, *, results=None, redraw_error=None):
        app_ = bare_app()
        app_.closing = True  # no dialogs
        app_.nonlocal_keithley_task = kind
        app_.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None)
        app_.nonlocal_vi_output_var, app_.nonlocal_vi_status_var = Var(), Var()
        app_.nonlocal_iv_eta_var, app_.nonlocal_iv6221_source_var = Var(), Var()
        app_.nonlocal_iv_rows, app_.nonlocal_iv_total_points = [(1, 0, 0, 0, 0, False)], 49
        app_.nonlocal_run_points = 1
        app_.nonlocal_scan_outputs = (results if results is not None
                                      else {"6221": "zeroed", "Keithley 2410": "zeroed"})
        redraw = mock.Mock(side_effect=redraw_error)
        app_._redraw_nonlocal_resistance_plot = app_._redraw_nonlocal_iv_plot = redraw
        app_._update_nonlocal_vi_controls = mock.Mock()
        future = Future()
        future.set_exception(error)
        finish = (app_._finish_nonlocal_resistance if kind == "resistance"
                  else app_._finish_nonlocal_iv_sweep)
        if redraw_error is None:
            finish(future)
        else:
            with self.assertRaises(type(redraw_error)):
                finish(future)
        return app_

# ----------------------------------------------------------------- I-V sweep
class SweepValuesTests(unittest.TestCase):
    pass

class SweepPlotTests(unittest.TestCase):
    def draw(self, rows, sample="1µm Pt-Pt", size=(900, 600)):
        app_ = bare_app()
        app_.nonlocal_iv_canvas = Canvas(*size)
        app_.nonlocal_iv_rows = rows
        app_.nonlocal_iv_settings = {"start_v": -6.0, "end_v": 6.0, "sample": sample}
        with mock.patch.object(app, "np", None):
            app_._redraw_nonlocal_iv_plot()
        return app_.nonlocal_iv_canvas

class SweepControllerTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.meter = FakeSourceMeter()
        self.controller = connected_controller(self.meter)

    def test_start_sets_the_limit_and_checks_the_ovp(self):
        self.controller.start_iv_sweep(1e-6)
        commands = self.meter.commands
        self.assertEqual(commands[0], "*RST", "from a known state")
        for command in (":SOUR:VOLT:PROT 20", ":SOUR:VOLT:RANG 20", ":SENS:CURR:PROT 1e-06",
                        ":SENS:CURR:NPLC 1", ":OUTP ON"):
            self.assertIn(command, commands)
        self.assertLess(commands.index(":SOUR:VOLT 0"), commands.index(":OUTP ON"), "on at 0 V")
        self.assertEqual(self.meter.output, 1)
        self.controller.set_iv_voltage(-6)
        self.assertEqual(self.controller.read_iv_point(), (-6.0, -6e-9))
        with self.assertRaisesRegex(ValueError, "within ±20 V"):
            self.controller.set_iv_voltage(20.5)

    def test_a_wrong_ovp_or_limit_stops_before_the_output_is_on(self):
        with self.assertRaisesRegex(ValueError, "at most 0.1 A"):
            self.controller.start_iv_sweep(0.5)
        meter = FakeSourceMeter()
        meter.write = (lambda original: lambda command: original(
            ":SOUR:VOLT:PROT 40" if command.startswith(":SOUR:VOLT:PROT") else command))(meter.write)
        controller = connected_controller(meter)
        with self.assertRaisesRegex(RuntimeError, "reads OVP 40 V"):
            controller.start_iv_sweep(1e-3)
        self.assertEqual(meter.output, 0)

    def start(self, model, limit_a, max_abs_v):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = model
        controller.start_iv_sweep(limit_a, max_abs_v)
        return meter, controller

    def test_above_20_v_the_high_range_and_the_ovp_level_above_the_sweep(self):
        # User, 2026-10-09: ±200 V on a 2400, ±1000 V on a 2410.
        for model, max_abs_v, source_range, ovp in (
            # Above ±20 V the OVP level is at least 5 % above the voltages
            # (the levels are ±5 %): 160 V on a 2400 takes "none" (210 V).
            ("2400", 20, 21.0, 20.0), ("2400", 40, 210.0, 60.0), ("2400", 50, 210.0, 60.0),
            ("2400", 150, 210.0, 160.0), ("2400", 160, 210.0, 210.0), ("2400", 200, 210.0, 210.0),
            ("2410", 150, 1100.0, 200.0), ("2410", 1000, 1100.0, 1100.0),
        ):
            with self.subTest(model=model, max_abs_v=max_abs_v):
                meter, controller = self.start(model, 1e-3, max_abs_v)
                self.assertEqual((meter.range, meter.ovp), (source_range, ovp))
                self.assertLess(meter.commands.index(f":SOUR:VOLT:PROT {ovp:g}" if ovp != 20 else ":SOUR:VOLT:PROT 20"),
                                meter.commands.index(":OUTP ON"), "set before the output goes on")
                controller.set_iv_voltage(-max_abs_v)
                with self.assertRaisesRegex(ValueError, f"within ±{max_abs_v:g} V"):
                    controller.set_iv_voltage(max_abs_v + 0.5)
                controller.restore_voltage_mode()
                self.assertEqual((meter.output, meter.range, meter.ovp), (0, 21.0, 20.0), "back to the pulse setup")
                self.assertEqual(controller.iv_sweep_max_abs_v, 20.0)

    def test_the_model_limits_and_the_high_range_current_limit(self):
        for model, too_high in (("2400", 200.5), ("2410", 1000.5)):
            with self.assertRaisesRegex(ValueError, "must be within"):
                self.start(model, 1e-3, too_high)
        with self.assertRaisesRegex(ValueError, "at most 0.021 A on the Keithley 2410's 1000 V range"):
            self.start("2410", 0.05, 100)
        self.start("2410", 0.05, 20)   # 20 V range: up to 0.1 A
        self.start("2400", 0.1, 200)

    def test_an_ovp_below_the_sweep_stops_before_the_output_is_on(self):
        meter = FakeSourceMeter()
        meter.write = (lambda original: lambda command: original(
            ":SOUR:VOLT:PROT 160" if command.startswith(":SOUR:VOLT:PROT 210") else command))(meter.write)
        controller = connected_controller(meter)
        controller.model = "2400"
        with self.assertRaisesRegex(RuntimeError, "reads 160 V, not the 210 V"):
            controller.start_iv_sweep(1e-3, 200)
        self.assertEqual(meter.output, 0)

    def test_set_leaves_the_output_on_at_the_voltage(self):
        # User, 2026-10-09: a 2400 voltage left on, from 2400-IV settings.
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        self.assertEqual(controller.set_dc_voltage(50, 1e-3), (50.0, 5e-8))
        self.assertEqual((meter.output, meter.voltage, meter.range, meter.ovp), (1, 50.0, 210.0, 60.0))
        self.assertLess(meter.commands.index(":SOUR:VOLT:PROT 60"), meter.commands.index(":OUTP ON"))
        self.assertLess(meter.commands.index(":OUTP ON"), meter.commands.index(":SOUR:VOLT 50"),
                        "on at 0 V, then the voltage")
        with self.assertRaisesRegex(ValueError, "within ±200 V"):
            controller.set_dc_voltage(250, 1e-3)
        controller.model = "2410"
        with self.assertRaisesRegex(ValueError, "at most 0.021 A"):
            controller.set_dc_voltage(500, 0.05)

    def test_after_high_voltage_the_output_drains_at_0_v_before_it_goes_off(self):
        # Review, 2026-10-09: a charged sample is drained, not left at 1000 V.
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2410"
        controller.set_dc_voltage(-1000, 1e-3)
        with mock.patch.object(app.time, "sleep", lambda s: meter.commands.append(f"sleep {s:g}")):
            meter.commands.clear()
            controller.safe_shutdown(reset=False)
            # 1 s, then until two readings are off the current limit.
            self.assertEqual(meter.commands, [":SOUR:VOLT 0", "sleep 1", "sleep 0.2", ":OUTP OFF"])
            # The next setup (*RST switches off at once) drains first too.
            controller.set_dc_voltage(500, 1e-3)
            meter.commands.clear()
            controller.setup_voltage_mode(1e-3)
            self.assertEqual(meter.commands[:5], [":SOUR:VOLT 0", "sleep 1", "sleep 0.2", ":OUTP OFF", "*RST"])
            self.assertFalse(controller.high_voltage_active)
            # Up to ±20 V nothing waits.
            controller.set_dc_voltage(5, 1e-3)
            meter.commands.clear()
            controller.safe_shutdown(reset=False)
            self.assertEqual(meter.commands, [":SOUR:VOLT 0", ":OUTP OFF"])

    def test_the_drain_waits_while_the_current_is_at_the_limit(self):
        # Review, 2026-10-09: a low Current limit A drains slowly; the output
        # stays at 0 V until the current is off the limit (the terminals at
        # 0 V), and a sample still charged after 30 s is reported.
        clock = [0.0]
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        controller.set_dc_voltage(100, 1e-6)
        readings = iter(["0,-1.0e-06"] * 5 + ["0,-2.0e-07"] * 2)
        original = meter.query
        meter.query = lambda command: next(readings) if command.upper() == ":READ?" else original(command)
        fake_time = mock.Mock(monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        with mock.patch.object(app, "time", fake_time):
            controller.safe_shutdown(reset=False)
        # 1 s, five readings at the limit, then two off it (0.2 s apart).
        self.assertAlmostEqual(clock[0], 1.0 + 6 * 0.2, places=6, msg="1 s, then until off the limit")
        self.assertEqual(meter.output, 0)
        meter.query = original
        controller.set_dc_voltage(100, 1e-6)
        meter.query = lambda command: "0,-1.0e-06" if command.upper() == ":READ?" else original(command)
        with mock.patch.object(app, "time", fake_time), \
                self.assertRaisesRegex(RuntimeError, "may still be charged"):
            controller.safe_shutdown(reset=False)
        self.assertEqual(meter.output, 0, "switched off all the same")

    def test_an_output_found_on_is_drained_before_the_reset(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        meter.output, meter.voltage = 1, 150.0   # e.g. left on before a reconnect
        with mock.patch.object(app.time, "sleep", lambda s: meter.commands.append(f"sleep {s:g}")):
            controller.setup_voltage_mode(1e-3)
        self.assertEqual(meter.commands[:3], [":SOUR:VOLT 0", "sleep 1", "sleep 0.2"])
        self.assertLess(meter.commands.index(":OUTP OFF"), meter.commands.index("*RST"))

    def test_every_pulse_ends_drained(self):
        # User, 2026-10-09: drain every pulse, ±20 V and below included.
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        controller.setup_voltage_mode(1e-3)
        with mock.patch.object(app.time, "sleep", lambda s: meter.commands.append(f"sleep {s:g}")):
            meter.commands.clear()
            controller.apply_voltage_pulse(5, 0.0, threading.Event())
        end = meter.commands[meter.commands.index(":SOUR:VOLT 5"):]
        self.assertEqual(end[1:5], [":SOUR:VOLT 0", "sleep 1", "sleep 0.2", ":OUTP OFF"])
        self.assertEqual(meter.output, 0)
        self.assertFalse(controller.high_voltage_active, "drained: no second drain")

    def test_no_drain_with_the_output_already_off(self):
        # Review, 2026-10-09: a Stop before the pulse's output went on, or a
        # drain that timed out, must not leave the drain flag stuck.
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        controller.setup_voltage_mode(1e-3)
        stop = threading.Event()
        stop.set()
        with mock.patch.object(app.time, "sleep", lambda s: meter.commands.append(f"sleep {s:g}")):
            with self.assertRaises(app.NonLocalVIStopped):
                controller.apply_voltage_pulse(100, 0.0, stop)
            self.assertNotIn("sleep 1", meter.commands, "nothing to drain: never on")
            self.assertFalse(controller.high_voltage_active)
            controller.high_voltage_active = True   # e.g. after a drain timeout
            controller.safe_shutdown(reset=False)
            self.assertNotIn("sleep 1", meter.commands)
            self.assertFalse(controller.high_voltage_active, "cleared: the output reads off")
            controller.setup_voltage_mode(1e-3)    # usable again at once

    def test_a_failed_set_leaves_the_output_off(self):
        meter = FakeSourceMeter()
        meter.query = (lambda original: lambda command: (
            "2e-3" if command.upper() == ":SENS:CURR:PROT?" else original(command)))(meter.query)
        controller = connected_controller(meter)
        controller.model = "2400"
        with self.assertRaisesRegex(RuntimeError, "current limit reads"):
            controller.set_dc_voltage(50, 1e-3)
        self.assertEqual(meter.output, 0)

    def test_each_pulse_takes_its_own_range_and_ovp(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        controller.setup_voltage_mode(1e-3)
        controller.apply_voltage_pulse(100, 0.0, threading.Event())
        commands = meter.commands
        self.assertLess(commands.index(":SOUR:VOLT:PROT 120"), commands.index(":OUTP ON"), "105 V → 120 V")
        self.assertLess(commands.index(":SOUR:VOLT:RANG 200"), commands.index(":OUTP ON"))
        self.assertIn(":SOUR:VOLT 100", commands)
        self.assertEqual(meter.output, 0, "off after the pulse")
        controller.apply_voltage_pulse(-5, 0.0, threading.Event())
        self.assertEqual((meter.range, meter.ovp, meter.output), (21.0, 20.0, 0), "back to the 20 V range")
        with self.assertRaisesRegex(ValueError, "±200 V"):
            controller.apply_voltage_pulse(201, 0.0, threading.Event())
        controller.model = "2410"
        controller.apply_voltage_pulse(-1000, 0.0, threading.Event())
        self.assertEqual((meter.range, meter.ovp, meter.output), (1100.0, 1100.0, 0))

    def test_restore_leaves_the_output_off_in_the_pulse_setup(self):
        self.controller.start_iv_sweep(1e-3)
        self.controller.set_iv_voltage(5)
        self.meter.commands.clear()
        self.controller.restore_voltage_mode()
        self.assertEqual(self.meter.commands[:2], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertIn(":SENS:CURR:PROT 0.001", self.meter.commands)
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.controller.source_mode, "VOLT")
        with self.assertRaisesRegex(RuntimeError, "not set up for the 2400-IV sweep"):
            self.controller.set_iv_voltage(1)


class Source:
    """The Gate-VI 6221: its output is switched off before the Keithley sources."""

    def __init__(self, log):
        self.log = log
        self.source_connected = True

    def output_off(self):
        self.log.append("6221 off")

    def confirm_output_off(self, context):
        self.log.append("6221 confirmed off")


class WorkerTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.meter = FakeSourceMeter()
        self.app = bare_app()
        self.log = []
        self.app.nonlocal_vi_2410_controller = connected_controller(self.meter)
        self.app.nonlocal_vi_ac_controller = Source(self.log)
        self.app.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        self.app.nonlocal_keithley_stop_event = threading.Event()
        self.app.nonlocal_keithley_output_off = False
        self.posted = []
        self.app.post_ui = self.posted.append
        self.app.nonlocal_vi_status_var = Var()
        self.app.nonlocal_iv6221_source_var = Var()

    def settings(self, voltages=(-1.0, 0.0, 1.0)):
        return {"voltages": list(voltages), "current_limit_a": 1e-3, "settle_s": 0.0}

    def recorder(self, page="NL_2400IV"):
        # The run folder's recorder, as begin_scan_run makes it (2026-10-18).
        import tempfile
        return app.ScanRunRecorder(Path(tempfile.mkdtemp()), page, "run", {})

class SixTwoTwoOneOffTests(WorkerTests):
    """|R| and 2400-IV switch the 6221 off first (review of 2026-10-08)."""

    pass

class OpenCircuitTests(unittest.TestCase):
    def test_an_open_circuit_point_is_skipped_not_fatal(self):
        with mock.patch.object(app.time, "sleep"):
            meter = FakeSourceMeter(resistance_ohm=float("inf"))
            controller = connected_controller(meter)
            controller.start_resistance(1e-9)
            voltage, current, resistance = controller.read_resistance()
        self.assertEqual((voltage, current), (5.0, 0.0))
        self.assertNotEqual(resistance, resistance, "NaN: the worker skips this point")


class StartQuestionTests(unittest.TestCase):
    def setUp(self):
        self.app = a = bare_app()
        a.root = None
        a.nonlocal_keithley_task = None
        a._nonlocal_keithley_free = lambda what: True
        a.nonlocal_iv_safety_var = Var(True)
        a.active_file_exports = set()
        a.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None)
        a.nonlocal_iv_start_var, a.nonlocal_iv_end_var = Var("-6"), Var("6")
        a.nonlocal_iv_step_var, a.nonlocal_iv_settle_var = Var("0.25"), Var("0.5")
        a.nonlocal_iv_limit_var = Var("1e-6")
        a._begin_nonlocal_keithley_task = mock.Mock()

    def ask(self, answer, folder="", name=""):
        # The Yes/No, then begin_scan_run's folder and optional file name
        # (user, 2026-10-18).
        with mock.patch.object(app.messagebox, "askyesno", return_value=answer) as question, \
                mock.patch.object(app.filedialog, "askdirectory", return_value=folder) as folders, \
                mock.patch.object(app.simpledialog, "askstring", return_value=name):
            self.app.start_nonlocal_iv_sweep()
        self.folders = folders
        return question.call_args[0][1] if question.called else None

if __name__ == "__main__":
    unittest.main()
