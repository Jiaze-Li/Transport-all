"""Deterministic offline regression tests for Batch 2 PPMS architecture contracts."""

from __future__ import annotations

import ast
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from transport.ppms import app as app_facade
from transport.ppms import app_shell
from transport.ppms import controllers as controllers_pkg
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
                runner.run(request, sink=None)

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
