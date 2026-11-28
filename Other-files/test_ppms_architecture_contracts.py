"""Deterministic offline regression tests for Batch 2 PPMS architecture contracts."""

from __future__ import annotations

import ast
import json
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import transport.ppms.ui.dialogs as dialogs_mod
import transport.ppms.ui.panel as panel_mod
from transport.ppms import app as app_facade
from transport.ppms import app_shell
from transport.ppms import controllers as controllers_pkg
from transport.ppms.controllers.environment import PPMS_QDINSTRUMENT_BACKEND
from transport.ppms.engine.macro import MacroRunner
from transport.ppms.engine.runner import PPMSScanRunner, ScanRequest
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.dialogs import ScanRunRecorder
from transport.ppms.ui.panel import PPMSPanelView

REPO_ROOT = Path(__file__).resolve().parents[1]


class SingleOwnershipContractTests(unittest.TestCase):
    def test_no_panel_inheritance_or_getattr_or_dual_init(self):
        self.assertNotIn(app_shell.AppShell, PPMSPanelView.__mro__)
        self.assertNotIn("__getattr__", PPMSPanelView.__dict__)
        self.assertNotIn("__getattr__", app_shell.AppShell.__dict__)
        self.assertFalse(hasattr(app_shell.AppShell, "_initialize_ppms_state"))
        self.assertFalse(hasattr(PPMSPanelView, "_initialize_ppms_state"))

    def test_app_state_single_resource_ownership(self):
        state = PPMSAppState(lambda _msg: None)
        try:
            self.assertEqual(state.ppms_executor._max_workers, 5)
            self.assertEqual(state.zurich_executor._max_workers, 1)
            self.assertIsNot(state.scan_stop_event, state.macro_stop_event)
            self.assertEqual(
                set(state.ppms_instrument_controllers),
                {"6221", "830_a", "830_b", "830_c", "2400"},
            )
            self.assertIsNotNone(state.close_handoff)
        finally:
            state.safe_close()

    def test_no_temporary_shims_or_retired_tools_remain(self):
        for symbol in ("STFMRSR830Controller", "NonLocalACController"):
            self.assertFalse(hasattr(controllers_pkg, symbol), symbol)
            self.assertFalse(hasattr(app_facade, symbol), symbol)
        for tool in (
            "tools/extract_app.py",
            "tools/ppms_host.py",
            "tools/ppms_package_glue.py",
        ):
            self.assertFalse((REPO_ROOT / tool).exists(), tool)


class TkIndependentFinalizationTests(unittest.TestCase):
    def test_runner_closes_recorder_and_handoff_when_closed_without_tk(self):
        state = PPMSAppState(lambda _msg: None)
        cleaned = threading.Event()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                recorder = ScanRunRecorder(
                    Path(tmp), "PPMS", "tkless", {"mode": "Current"}, label="PPMS"
                )
                state.run_recorder = recorder
                state.scan_running = True
                state.closed = True  # simulates window destroyed before finalization
                runner = PPMSScanRunner(state, post_ui=lambda _cb: None)
                runner._zero_outputs_after_scan = mock.Mock(
                    return_value={"6221": "zeroed"}
                )
                runner.nested_scan_worker = mock.Mock()
                state.close_handoff.begin()
                state.close_handoff.defer(cleaned.set)

                request = ScanRequest(
                    mode="Current",
                    sequence=((1, 1, 1, 0.0),),
                    rate_oe_s=100.0,
                    stable_s=0.1,
                    approach_code=0,
                    transport_settings={"harmonic_passes": ()},
                    total_points=1,
                    estimated_seconds=1.0,
                    sr830_tc_code=10,
                )
                try:
                    runner.run(request, sink=None)
                finally:
                    state.close_handoff.end()

                self.assertTrue(recorder.closed)
                self.assertTrue(cleaned.is_set())
                events = [
                    json.loads(line)["event"]
                    for line in recorder.log_path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
                self.assertIn("run_finished", events)
        finally:
            state.safe_close()

    def test_completion_vs_close_race_closes_recorder_before_ui_callback(self):
        state = PPMSAppState(lambda _msg: None)
        ui_callbacks = []
        cleaned = threading.Event()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                recorder = ScanRunRecorder(
                    Path(tmp), "PPMS", "race", {"mode": "Current"}, label="PPMS"
                )
                state.run_recorder = recorder
                state.scan_running = True
                runner = PPMSScanRunner(state, post_ui=ui_callbacks.append)
                runner._zero_outputs_after_scan = mock.Mock(
                    return_value={"6221": "zeroed"}
                )
                runner.nested_scan_worker = mock.Mock()
                sink = mock.Mock()
                sink.capture_plot_snapshots = mock.Mock()

                # Simulate close arriving right before deferring cleanup
                state.close_handoff.begin()
                state.close_handoff.defer(cleaned.set)
                request = ScanRequest(
                    mode="Current",
                    sequence=((1, 1, 1, 0.0),),
                    rate_oe_s=100.0,
                    stable_s=0.1,
                    approach_code=0,
                    transport_settings={"harmonic_passes": ()},
                    total_points=1,
                    estimated_seconds=1.0,
                    sr830_tc_code=10,
                )
                runner.run(request, sink=sink)
                state.close_handoff.end()

                # Even before any ui_callback runs, recorder is closed and cleanup ran
                self.assertTrue(recorder.closed)
                self.assertTrue(cleaned.is_set())
                self.assertTrue(ui_callbacks)
        finally:
            state.safe_close()


class MacroVsScanCancellationTests(unittest.TestCase):
    def test_stop_macro_signals_macro_stop_and_stops_scan(self):
        macro_stop = threading.Event()
        scan_stop = threading.Event()
        runner = MacroRunner(
            environment_controller=mock.Mock(),
            executor=mock.Mock(),
            macro_stop_event=macro_stop,
            is_closed=lambda: False,
            field_ready_fn=lambda *_a, **_kw: True,
            stop_scan_fn=scan_stop.set,
            post_ui=lambda cb: cb(),
        )
        runner.running = True
        runner.stop()
        self.assertTrue(macro_stop.is_set())
        self.assertTrue(scan_stop.is_set())

    def test_macro_scan_handoff_waits_for_on_scan_finished_before_advancing(self):
        state = PPMSAppState(lambda _msg: None)
        try:
            panel = PPMSPanelView.__new__(PPMSPanelView)
            panel.state = state
            panel.shell = types.SimpleNamespace(
                closing=False,
                post_ui=lambda cb: cb(),
                thread_log=lambda _msg: None,
            )
            panel.root = None
            panel._macro_scan_pending = False
            panel.ppms_macro_status = mock.Mock()
            panel.ppms_macro_editor = None
            panel.ppms_macro_edit_buttons = []
            panel.ppms_macro_window = None
            panel._update_ppms_control_ui = mock.Mock()
            panel._set_ppms_environment_ui = mock.Mock()
            panel.ppms_field_scan_status_var = mock.Mock()
            panel.ppms_field_scan_eta_var = mock.Mock()
            panel.stop_ppms_field_scan = mock.Mock()

            scheduled = []

            def fake_start_scan():
                state.scan_running = True

            panel.start_ppms_field_scan = fake_start_scan
            class ImmediateExecutor:
                def submit(self, fn, *args, **kwargs):
                    fn(*args, **kwargs)
                    fut = mock.Mock()
                    fut.result.return_value = None
                    fut.add_done_callback = lambda cb: cb(fut)
                    return fut

            panel.macro_runner = MacroRunner(
                environment_controller=mock.Mock(),
                executor=ImmediateExecutor(),
                macro_stop_event=state.macro_stop_event,
                is_closed=lambda: state.closed,
                field_ready_fn=lambda *_a, **_kw: True,
                stop_scan_fn=panel.stop_ppms_field_scan,
                post_ui=lambda cb: cb(),
            )
            commands = [(1, "SCAN", ()), (2, "WAIT", (0.0,))]
            panel.macro_runner.start(
                commands,
                panel,
                start_scan=panel._macro_start_scan,
                is_scan_running=lambda: state.scan_running or panel._macro_scan_pending,
                schedule_poll=lambda _ms, cb: scheduled.append(cb),
            )
            self.assertTrue(panel.macro_runner.running)
            self.assertEqual(len(scheduled), 1)

            # Worker clears state.scan_running before UI drains on_scan_finished
            state.scan_running = False
            poll_cb = scheduled.pop(0)
            poll_cb()
            self.assertTrue(panel.macro_runner.running)
            self.assertEqual(len(scheduled), 1)

            # UI drains on_scan_finished, then next poll advances and completes Macro
            panel.on_scan_finished(
                mode="Current",
                outcome="completed",
                status="Completed; 6221 zeroed",
                results={"6221": "zeroed"},
                outputs_confirmed=True,
                field_note="",
                error=None,
                problems=[],
            )
            scheduled.pop(0)()
            # WAIT 0 submits to executor; drain its scheduled advance
            while scheduled:
                scheduled.pop(0)()
            self.assertFalse(panel.macro_runner.running)
            panel.ppms_macro_status.set.assert_called_with("Macro completed.")
        finally:
            state.safe_close()


class QueuedScanAndInflightShutdownTests(unittest.TestCase):
    def test_queued_scan_submitted_via_start_ppms_field_scan_defers_close_handoff(self):
        state = PPMSAppState(lambda _msg: None)
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = state
        panel.shell = types.SimpleNamespace(
            closing=False,
            root=None,
            post_ui=lambda _cb: None,
            thread_log=lambda _msg: None,
            append_log=lambda _msg: None,
        )
        panel.root = types.SimpleNamespace(after_cancel=lambda _aid: None)
        panel.ppms_poll_after_id = None
        panel.zurich_after_id = None
        panel._macro_scan_pending = False
        panel._require_ppms_control_ready = mock.Mock()
        panel._update_ppms_control_ui = mock.Mock()
        panel._redraw_ppms_transport_plot = mock.Mock()
        panel.macro_runner = mock.Mock()
        panel.ppms_simple_mode = mock.Mock(get=lambda: "Current")
        panel.ppms_simple_range = [mock.Mock(get=lambda v=v: v) for v in ("0.01", "0.01", "0.01")]
        panel.ppms_temperature_scan_range = [mock.Mock(get=lambda v=v: v) for v in ("300", "290", "5")]
        panel.ppms_temperature_delay_var = mock.Mock(get=lambda: "0")
        defaults = dict(
            ppms_acquisition_time_var="1",
            ppms_field_target_var="0",
            ppms_temperature_target_var="300",
            ppms_motor_target_var="0",
            ppms_field_scan_stable_var="0",
            ppms_field_rate_var="100",
            ppms_field_approach_var="No overshoot",
            ppms_transport_current_var="0.01",
            ppms_transport_frequency_var="13.7",
            ppms_transport_compliance_var="50",
            ppms_transport_harmonic_a_var="1",
            ppms_transport_harmonic_b_var="2",
            ppms_motor_confirm_var=True,
            ppms_temperature_rate_var="1",
            ppms_temperature_approach_var="No overshoot",
            ppms_motor_speed_var="14",
            ppms_sr830_time_constant_var="1 s",
        )
        for name, value in defaults.items():
            setattr(panel, name, mock.Mock(get=lambda v=value: v))
        panel.ppms_lockin_selection = {
            key: mock.Mock(get=lambda k=key: k == "830_a")
            for key in ("830_a", "830_b", "zurich")
        }
        panel.ppms_resource_vars = {}
        panel.ppms_field_scan_progress = mock.Mock()
        panel.ppms_field_scan_eta_var = mock.Mock()
        panel.ppms_field_scan_status_var = mock.Mock()
        panel.ppms_transport_status_var = mock.Mock()
        panel.ppms_zero_persist_button = mock.Mock()
        panel.ppms_field_scan_save_button = mock.Mock()
        panel.ppms_field_scan_stop_button = mock.Mock()
        panel.ppms_control_widgets = []
        panel.ppms_control_safety_var = mock.Mock(get=lambda: False)
        panel.ppms_scan_target_widgets = {}
        panel.zurich_harmonic = mock.Mock(get=lambda: "1")
        panel.zurich_reference = mock.Mock(get=lambda: "Trigger In 1")
        panel.zurich_timeconstant = mock.Mock(get=lambda: "1")

        events = []
        disconnected = threading.Event()
        addresses = {"6221": 30, "830_a": 8, "830_b": 9, "830_c": 10, "2400": 24}
        for key in ("6221", "830_a", "830_b", "830_c", "2400"):
            ctrl = state.ppms_instrument_controllers[key]
            ctrl.instrument = mock.Mock() if key in ("6221", "830_a") else None
            ctrl.resource_name = f"GPIB0::{addresses[key]}::INSTR"
            ctrl.disconnect = lambda k=key: events.append(f"disconnect:{k}")
        state.ppms_environment_controller = types.SimpleNamespace(
            backend=PPMS_QDINSTRUMENT_BACKEND,
            connected=False,
            disconnect=lambda: events.append("disconnect:env"),
        )
        state.zurich_controller = types.SimpleNamespace(
            mfli_connected=False,
            mfli_has_multi_demod=lambda: True,
            disconnect_mfli=lambda: (events.append("disconnect:mfli"), disconnected.set()),
        )
        state.last_readback = {"temperature_k": 300.0, "field_oe": 0.0}

        panel.runner = PPMSScanRunner(state, post_ui=lambda _cb: None)
        panel.runner._zero_outputs_after_scan = lambda sink=None: (
            events.append("runner:zeroed") or {"6221": "zeroed"}
        )
        panel.runner.nested_scan_worker = lambda *a, **kw: events.append("worker:ran")

        # Occupy all 5 ppms_executor workers so start_ppms_field_scan queues runner.run
        all_busy = []
        release_workers = threading.Event()
        for _ in range(5):
            entered = threading.Event()
            all_busy.append(entered)
            state.ppms_executor.submit(lambda e=entered: (e.set(), release_workers.wait(5.0)))
        for e in all_busy:
            self.assertTrue(e.wait(2.0))

        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch.object(panel_mod, "ask_scan_start", return_value=True),
                mock.patch.object(panel_mod.filedialog, "askdirectory", return_value=folder),
                mock.patch.object(dialogs_mod.simpledialog, "askstring", return_value="queued_test"),
                mock.patch.object(panel_mod.messagebox, "showerror") as showerror,
            ):
                panel.start_ppms_field_scan()
                self.assertFalse(showerror.called, showerror.call_args)

            recorder = state.run_recorder
            self.assertIsNotNone(recorder)
            self.assertTrue(state.close_handoff.active)

            # Close while scan is still queued in ppms_executor
            close_thread = threading.Thread(target=panel.safe_close_ppms)
            close_thread.start()
            close_thread.join(timeout=2.0)
            self.assertFalse(disconnected.is_set())

            # Release workers so the queued scan runs and completes handoff cleanup
            release_workers.set()
            self.assertTrue(disconnected.wait(5.0))
            self.assertTrue(recorder.closed)
            self.assertIn("runner:zeroed", events)
            self.assertLess(events.index("runner:zeroed"), events.index("disconnect:mfli"))

    def test_safe_close_waits_for_inflight_hardware_task_before_disconnect(self):
        state = PPMSAppState(lambda _msg: None)
        events = []
        task_started = threading.Event()
        allow_finish = threading.Event()
        disconnected = threading.Event()

        for key in ("6221", "830_a", "830_b", "830_c", "2400"):
            state.ppms_instrument_controllers[key].disconnect = lambda k=key: events.append(f"disconnect:{k}")
        state.ppms_environment_controller.disconnect = lambda: events.append("disconnect:env")
        state.zurich_controller.disconnect_mfli = lambda: (events.append("disconnect:mfli"), disconnected.set())

        def inflight_task():
            task_started.set()
            allow_finish.wait(5.0)
            events.append("task:done")

        state.ppms_executor.submit(inflight_task)
        self.assertTrue(task_started.wait(2.0))

        close_thread = threading.Thread(target=state.safe_close)
        close_thread.start()
        self.assertFalse(disconnected.wait(0.1))
        allow_finish.set()
        close_thread.join(timeout=5.0)
        self.assertTrue(disconnected.is_set())
        self.assertLess(events.index("task:done"), events.index("disconnect:env"))
        self.assertLess(events.index("task:done"), events.index("disconnect:mfli"))


class DependencyDirectionASTTests(unittest.TestCase):
    def test_zero_tk_ui_app_imports_in_core_common_protocol_controllers_engine_state(self):
        forbidden_roots = {"tkinter", "tk", "ttk"}
        targets = [
            *sorted((REPO_ROOT / "transport" / "core").glob("*.py")),
            *sorted((REPO_ROOT / "transport" / "common").glob("*.py")),
            *sorted((REPO_ROOT / "transport" / "ppms" / "controllers").glob("*.py")),
            *sorted((REPO_ROOT / "transport" / "ppms" / "engine").glob("*.py")),
            REPO_ROOT / "transport" / "ppms" / "protocol.py",
            REPO_ROOT / "transport" / "ppms" / "state.py",
        ]
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertNotIn(root, forbidden_roots, f"{path}: {alias.name}")
                        self.assertNotIn("ppms.ui", alias.name, f"{path}: {alias.name}")
                        self.assertNotIn("ppms.app", alias.name, f"{path}: {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, forbidden_roots, f"{path}: {node.module}")
                    self.assertNotIn("ppms.ui", node.module, f"{path}: {node.module}")
                    self.assertNotIn("ppms.app", node.module, f"{path}: {node.module}")


if __name__ == "__main__":
    unittest.main()
