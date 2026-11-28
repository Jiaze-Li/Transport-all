"""Four-mode launch validation without Tk or instrument writes."""
import threading
import tempfile
import csv
from pathlib import Path
import ast
import inspect
import textwrap
import types
import unittest
from unittest.mock import Mock, patch

import transport.ppms.engine.motor as motor_mod
import transport.ppms.engine.runner as runner_mod
import transport.ppms.ui.panel as panel_mod
from transport.ppms.controllers.environment import PPMS_QDINSTRUMENT_BACKEND
from transport.ppms.engine.errors import PPMSScanCancelled
from transport.ppms.engine.runner import PPMSScanRunner
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMSPanelView


class ModeTests(unittest.TestCase):
    def test_stop_is_non_modal_in_all_four_modes(self):
        # Every end zeroes the 6221 (user, 2026-10-18): the status says so,
        # and a stop with the 6221 read back off opens no dialog.
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        later = []
        obj.shell = types.SimpleNamespace(closing=False, root=types.SimpleNamespace(after=lambda _ms, show: later.append(show)), thread_log=Mock())
        obj.root = obj.shell.root
        obj.thread_log = obj.shell.thread_log
        obj.ppms_field_scan_status_var = Mock()
        obj.ppms_field_scan_eta_var = Mock()
        obj.state.scan_outputs = {"6221": "zeroed"}
        for mode in ("Temperature", "Field", "Angle", "Current"):
            for error in (None, PPMSScanCancelled("stopped as requested while waiting for lock-in reference")):
                with patch.object(panel_mod.messagebox, "showerror") as popup:
                    obj.on_scan_finished(mode, True, error)
                    popup.assert_not_called()
                self.assertEqual(later, [])
                self.assertEqual(obj.ppms_field_scan_status_var.set.call_args.args[0],
                                 "Stopped; 6221 zeroed" + ("; field kept at its setpoint" if mode == "Field" else ""))

    def test_real_failure_is_not_hidden_by_stop(self):
        # The dialog opens from the Tk event loop and says how the 6221 ended
        # (user, 2026-10-18).
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        later_root = types.SimpleNamespace(after=lambda _ms, show: show())
        obj.shell = types.SimpleNamespace(closing=False, root=later_root, thread_log=Mock())
        obj.root = later_root
        obj.thread_log = obj.shell.thread_log
        obj.ppms_field_scan_status_var = Mock()
        obj.ppms_field_scan_eta_var = Mock()
        obj.state.scan_outputs = {"6221": "zeroed"}
        with patch.object(panel_mod.messagebox, "showerror") as popup:
            obj.on_scan_finished("Current", True, OSError("CSV write failed"))
            popup.assert_called_once_with("PPMS Current scan", "CSV write failed\n\nAt the end: 6221 zeroed",
                                          parent=obj.root)
        self.assertEqual(obj.ppms_field_scan_status_var.set.call_args.args[0], "Failed: CSV write failed")

    def test_controls_are_created_inside_static_content(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(PPMSPanelView._build_ui)))
        parents = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.value.args:
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value.args[0], ast.Name):
                        parents[target.id] = node.value.args[0].id
        self.assertEqual(parents["content"], "outer")
        self.assertNotIn("scroller", parents)
        for name in ("environment", "control", "instruments"):
            self.assertEqual(parents[name], "content")
        self.assertEqual(parents["transport_plot"], "outer")
        self.assertEqual(parents["scan"], "content")
        self.assertEqual(parents["metrics_outer"], "content")
        self.assertEqual(parents["metrics_view"], "metrics_outer")
        self.assertEqual(parents["metrics"], "metrics_view")
        self.assertNotIn("in_=", inspect.getsource(PPMSPanelView._build_compact_controls))

    def launch(self, mode, values, confirm=True, cancel_folder=False, filename="", existing=None, selected=None, speed=14,
               expect_error=None):
        obj = PPMSPanelView.__new__(PPMSPanelView)
        obj.state = PPMSAppState()
        obj.runner = PPMSScanRunner(obj.state, post_ui=lambda cb: None)
        obj.shell = types.SimpleNamespace(closing=False, root=None, thread_log=Mock(), post_ui=lambda cb: None)
        obj.root = None
        obj.thread_log = obj.shell.thread_log
        obj.state.scan_running = obj.state.environment_busy = False
        obj._require_ppms_control_ready = Mock()
        obj.ppms_simple_mode = Mock(get=lambda: mode)
        obj.ppms_simple_range = [Mock(get=lambda v=v: v) for v in values]
        obj.ppms_temperature_scan_range = [Mock(get=lambda v=v: v) for v in (300, 290, 5)]
        obj.ppms_temperature_delay_var = Mock(get=lambda: getattr(self, "temperature_delay", "0"))
        defaults = dict(ppms_acquisition_time_var=1, ppms_field_target_var=100, ppms_temperature_target_var=300,
            ppms_motor_target_var=26, ppms_field_scan_stable_var=0,
            ppms_field_rate_var=100, ppms_field_approach_var="No overshoot",
            ppms_transport_current_var=.001, ppms_transport_frequency_var=13.7,
            ppms_transport_compliance_var=50, ppms_transport_harmonic_a_var=1,
            ppms_transport_harmonic_b_var=2, ppms_motor_confirm_var=True,
            ppms_temperature_rate_var=1, ppms_temperature_approach_var="No overshoot",
            ppms_motor_speed_var=speed, ppms_sr830_time_constant_var="1 s")
        for name, value in defaults.items():
            setattr(obj, name, Mock(get=lambda v=value: v))
        obj.state.ppms_environment_controller = types.SimpleNamespace(backend=PPMS_QDINSTRUMENT_BACKEND)
        obj.state.ppms_instrument_controllers = {k: types.SimpleNamespace(connected=True, resource_name=f"GPIB0::{address}::INSTR")
            for k, address in (("6221", 30), ("830_a", 8), ("830_b", 9))}
        obj.ppms_lockin_selection = {key: Mock(get=lambda key=key: False)
                                    for key in ('830_a', '830_b', 'zurich')}
        if selected is not None:
            obj.ppms_lockin_selection = {key: Mock(get=lambda key=key: key in selected)
                                        for key in ('830_a', '830_b', 'zurich')}
            obj.state.zurich_controller = types.SimpleNamespace(mfli_connected='zurich' in selected, mfli_has_multi_demod=lambda: True)
            for key in ('830_a', '830_b'):
                obj.state.ppms_instrument_controllers[key].connected = key in selected
        obj.state.last_readback = dict(temperature_k=300, field_oe=100)
        for name in ("_redraw_ppms_transport_plot", "_update_ppms_control_ui",
                     "ppms_field_scan_progress", "ppms_field_scan_eta_var", "ppms_field_scan_status_var", "ppms_transport_status_var"):
            setattr(obj, name, Mock())
        obj.state.ppms_executor = Mock()
        self.durations = []
        obj._format_scan_duration = lambda seconds: (self.durations.append(seconds), f"{seconds:g} s")[1]
        order = []
        def answer(*args, **kwargs):
            order.append("confirm")
            self.last_question = args[1] if len(args) > 1 else kwargs.get("message", "")
            return confirm
        import transport.ppms.ui.dialogs as dialogs_mod

        with tempfile.TemporaryDirectory() as folder, patch.object(dialogs_mod.simpledialog, "askstring", return_value=filename), patch.object(panel_mod.messagebox, "askyesno", side_effect=answer), patch.object(panel_mod, "ask_scan_start", side_effect=answer), patch.object(panel_mod.filedialog, "askdirectory", side_effect=lambda **kw: (order.append("folder") or ("" if cancel_folder else folder))), patch.object(panel_mod.messagebox, "showerror") as error:
            if existing:
                (Path(folder) / existing).write_text("original", encoding="utf-8")
            obj.start_ppms_field_scan()
            if expect_error is not None:
                self.assertIn(expect_error, error.call_args.args[1])
                obj.state.ppms_executor.submit.assert_not_called()
                return None
            self.assertFalse(error.called, error.call_args)
            self.assertEqual(order, ["confirm", "folder"] if confirm else ["confirm"])
            if not confirm or cancel_folder or filename is None:
                obj.state.ppms_executor.submit.assert_not_called()
                self.assertFalse(obj.state.scan_running)
                self.assertEqual(list(Path(folder).iterdir()), [])
                return
            if existing:
                self.assertEqual((Path(folder) / existing).read_text(encoding="utf-8"), "original")
        request = obj.state.ppms_executor.submit.call_args.args[1]
        return obj, request.sequence, request.transport_settings

    def test_four_modes(self):
        for mode, values, axis in (("Temperature", (300, 290, 5), "temperature"),
                                   ("Field", (100, 300, 100), "field"),
                                   ("Angle", (26, 28, 1), "angle"),
                                   ("Current", (.001, .003, .001), "current")):
            obj, sequence, settings = self.launch(mode, values)
            self.assertEqual(obj.ppms_plot_axis, axis)
            self.assertIn("estimated about", obj.ppms_field_scan_status_var.set.call_args.args[0])
            self.assertNotIn("等待另计", obj.ppms_field_scan_status_var.set.call_args.args[0])
            self.assertEqual(obj.ppms_field_scan_total, 5 if mode == "Field" else 3)
            self.assertEqual(len(sequence), 5 if mode == "Field" else 1)
            self.assertEqual(len(settings["nested"]["temperature"]), 3 if mode == "Temperature" else 1)
            self.assertEqual(len(settings["nested"]["angle"]), 3 if mode == "Angle" else 1)
            self.assertEqual(len(settings["currents"]), 3 if mode == "Current" else 1)

    def test_decel_code_is_parsed_as_move_parses_it(self):
        # Move took '5.0' as code 5, Start scan refused it: int('5.0') raised
        # (audit, 2026-10-13).  Both now accept and refuse the same entries.
        for typed, code in (("5.0", 5), ("14.0", 14), (" 0 ", 0), ("7", 7), (14, 14)):
            with self.subTest(typed=typed):
                _, _, settings = self.launch("Angle", (26, 28, 1), speed=typed)
                self.assertEqual(settings["slowdown"], code)
                self.assertIs(type(settings["slowdown"]), int)
                self.assertEqual(motor_mod.PPMSMotorDebug.validate_move(26, typed, 26)[1], code)
        for typed in ("5.5", "15", "-1", "nan", "1e3"):
            with self.subTest(typed=typed):
                with self.assertRaises(ValueError):
                    motor_mod.PPMSMotorDebug.validate_move(26, typed, 26)
                # launch() asserts that no error dialog opened; here that
                # assertion fails on the refusal, which names the decel code.
                with self.assertRaisesRegex(AssertionError, "Decel code must be an integer within 0–14"):
                    self.launch("Angle", (26, 28, 1), speed=typed)

    def test_optional_filename_and_no_overwrite(self):
        # The run folder <folder>/<name>/ with <name>.csv and <name>.log
        # (user, 2026-10-18); a name in use gets _01, and nothing is
        # overwritten; blank: PPMS_<date>_<time>.
        for entered, expected in (("sampleA", "sampleA"), ("sampleA.csv", "sampleA")):
            obj, _, _ = self.launch("Angle", (0, 10, 5), filename=entered)
            self.assertEqual(obj.ppms_run_folder.name, expected)
            self.assertEqual(obj.ppms_run_recorder.csv_path.name, f"{expected}.csv")
            self.assertEqual(obj.ppms_run_recorder.log_path.name, f"{expected}.log")
        obj, _, _ = self.launch("Angle", (0, 10, 5), filename="sampleA.csv", existing="sampleA")
        self.assertEqual(obj.ppms_run_folder.name, "sampleA_01")
        self.assertEqual(obj.ppms_run_recorder.csv_path.name, "sampleA_01.csv")
        obj, _, _ = self.launch("Angle", (0, 10, 5), filename=" ")
        self.assertRegex(obj.ppms_run_folder.name, r"^PPMS_\d{8}_\d{6}$")
        self.launch("Angle", (0, 10, 5), filename=None)

    def test_temperature_field_launch(self):
        obj, sequence, settings = self.launch("Field+Temperature", (-100, 100, 100))
        self.assertEqual(obj.ppms_plot_axis, "field")
        self.assertIsNone(obj.ppms_plot_angle_range, "only an angle axis gets the scan's range")
        self.assertEqual(obj.ppms_field_scan_total, 15)
        self.assertEqual([point[3] for point in sequence], [-100, 0, 100, 0, -100])
        self.assertEqual(settings["nested"]["temperature"], (300, 295, 290))
        self.assertEqual(settings["nested"]["angle"], (26,))
        self.assertEqual(settings["currents"], (.001,))
        self.assertEqual(settings["temperature_delay_s"], 0.0)

    def test_temperature_field_launch_with_a_wait_after_each_temperature(self):
        """User, 2026-11-23: Wait s after each temperature is reached; the
        Yes/No names it and the estimate counts it, three temperatures x 600 s."""
        self.temperature_delay = "0"
        self.launch("Field+Temperature", (-100, 100, 100))
        without = max(self.durations)
        self.assertNotIn("Wait after each temperature", self.last_question)
        self.temperature_delay = "600"
        _obj, _sequence, settings = self.launch("Field+Temperature", (-100, 100, 100))
        self.assertEqual(settings["temperature_delay_s"], 600.0)
        self.assertIn("Wait after each temperature is stable: 600 s, then the scan at it", self.last_question)
        self.assertAlmostEqual(max(self.durations) - without, 3 * 600)
        for typed, message in (("-1", "must be within 0–86400 s"), ("x", "must be a number")):
            self.temperature_delay = typed
            self.launch("Field+Temperature", (-100, 100, 100), expect_error=message)
        self.temperature_delay = "0"

    def test_temperature_outer_angle_launch(self):
        obj, sequence, settings = self.launch("Angle+Temperature", (-5, 355, 5))
        self.assertEqual(obj.ppms_plot_axis, "angle")
        self.assertEqual(obj.ppms_plot_angle_range, (-5, 355), "the plots' x axis (user, 2026-11-15)")
        self.assertEqual(obj.ppms_field_scan_total, 73 * 3)
        self.assertEqual([p[3] for p in sequence], [100])
        self.assertEqual(settings["nested"]["temperature"], (300, 295, 290))
        self.assertEqual(settings["nested"]["angle"], tuple(range(-5, 356, 5)))

    def test_temperature_outer_current_launch(self):
        obj, sequence, settings = self.launch("Current+Temperature", (1, 3, 1))
        self.assertEqual(obj.ppms_plot_axis, "current")
        self.assertEqual(obj.ppms_field_scan_total, 9)
        self.assertEqual(settings["currents"], (1., 2., 3.))
        self.assertEqual(settings["current_rms_ma"], 1.)
        self.assertEqual(settings["nested"], {"angle": (26.,), "temperature": (300., 295., 290.)})
        self.assertEqual([p[3] for p in sequence], [100.])

    def test_launch_with_only_selected_lockins_connected(self):
        for selected in (('830_a',), ('830_b',), ('zurich',), ('830_a', 'zurich')):
            obj, sequence, settings = self.launch('Field', (0, 10, 5), selected=selected)
            self.assertEqual(settings['active_lockins'], tuple(k for k in selected if k != 'zurich'))
            self.assertEqual(settings['allow_no_lockins'], 'zurich' in selected)
            self.assertTrue(obj.ppms_field_scan_running)

    def test_decline_and_folder_cancel_do_not_start(self):
        self.launch("Field", (0, 100, 10), confirm=False)
        self.launch("Field", (0, 100, 10), cancel_folder=True)

    def test_field_roundtrip_descending_uneven_and_single_point(self):
        for limits, expected in (((100, -100, 75), [100, 25, -50, -100, -50, 25, 100]),
                                 ((0, 0, 100), [0])):
            obj, sequence, settings = self.launch("Field", limits)
            self.assertEqual([p[3] for p in sequence], expected)
            self.assertEqual(obj.state.scan_total, len(expected))
            self.assertEqual([p[1] for p in sequence], list(range(1, len(expected)+1)))

    def test_live_csv_appends_rows_with_one_header(self):
        state = PPMSAppState()
        runner = PPMSScanRunner(state, post_ui=lambda cb: None)
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "scan.csv"
            state.live_csv_path = csv_path
            state.live_csv_fields = None
            runner._append_live_csv(dict(field=1, x=2))
            runner._append_live_csv(dict(field=3, x=4))
            before = csv_path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "channels changed"):
                runner._append_live_csv(dict(field=5, y=6))
            self.assertEqual(csv_path.read_bytes(), before)
            with csv_path.open(encoding="utf-8-sig") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [dict(field="1", x="2"), dict(field="3", x="4")])


if __name__ == "__main__":
    unittest.main()
