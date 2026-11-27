"""Deterministic offline characterization of PPMS Zurich close ordering (T1.3 / A2-3).

Traces PPMSPanel.safe_close_ppms, CombinedControlApp.safe_close_ppms,
ScanCloseHandoff, _ppms_scan_with_final_field, zurich_executor, and
LABONE::<device> leases using threading.Event barriers (no timing guesses).

Finding: PPMSPanel.safe_close_ppms (AST-identical to main@56908d88) submits
zurich_controller.disconnect_mfli() to zurich_executor and shuts down
zurich_executor BEFORE calling super().safe_close_ppms(), which only defers
ppms_instrument_controllers / ppms_environment_controller via ScanCloseHandoff.
Therefore MFLI disconnect + LABONE::<device> lease release can execute while
the scan worker on ppms_executor is still active.
"""

from concurrent.futures import ThreadPoolExecutor
import threading
import types
import unittest
from unittest import mock

from test_ppms_motor_debug import app
from test_ppms_sr830_tc import build_ppms_panel


class FakeMfliDaq:
    def __init__(self, on_disconnect=None):
        self.disconnected = False
        self.on_disconnect = on_disconnect
        self.nodes = []

    def setInt(self, node, value):
        self.nodes.append((node, value))

    def sync(self):
        pass

    def disconnect(self):
        self.disconnected = True
        if self.on_disconnect is not None:
            self.on_disconnect()


def _make_panel_with_zurich(on_mfli_disconnect=None):
    panel, _ttk = build_ppms_panel()
    panel.ppms_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="ppms-control")
    panel.zurich_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ppms-zurich")
    panel.zurich_controller = app.NonLocalACController(lambda _msg: None)
    panel.closing = False
    panel.ppms_closed = False
    panel.zurich_closed = False
    panel.ppms_field_scan_running = False
    panel.ppms_active_scan_mode = "Current"
    panel.ppms_field_scan_rows = []
    panel.ppms_scan_field_zero_accepted = False
    panel.ppms_poll_after_id = None
    panel.zurich_after_id = None
    panel.root = types.SimpleNamespace(after_cancel=lambda _aid: None)
    panel.append_log = lambda _msg: None
    panel.thread_log = lambda _msg: None
    panel.post_ui = lambda cb: None
    panel._ppms_zero_outputs_after_scan = lambda: {"6221": "zeroed"}
    panel._ppms_close_run = lambda *args, **kwargs: None

    # Attach fake connected external controllers and environment controller
    # (recording disconnect order without touching hardware).
    events = []
    for key in ("6221", "830_a"):
        ctrl = panel.ppms_instrument_controllers[key]
        ctrl.disconnect = lambda k=key: events.append(f"disconnect:{k}")
    panel.ppms_environment_controller = types.SimpleNamespace(
        connected=False,
        disconnect=lambda: events.append("disconnect:ppms_env"),
    )

    daq = FakeMfliDaq(
        on_disconnect=lambda: (
            events.append("disconnect:mfli"),
            on_mfli_disconnect() if on_mfli_disconnect else None,
        )
    )
    panel.zurich_controller.mfli_daq = daq
    panel.zurich_controller.mfli_device = "dev4414"
    panel.zurich_controller.mfli_config = {"extref_demodulator": 3, "extref_channel": 0}
    lease_key = "LABONE::DEV4414"
    app.acquire_instrument_lease(lease_key, panel.zurich_controller, "PPMS Zurich MFLI")
    panel.zurich_controller.mfli_lease_resource = lease_key
    return panel, daq, events, lease_key


class ZurichCloseOrderTests(unittest.TestCase):
    def setUp(self):
        app._INSTRUMENT_LEASES.clear()

    def tearDown(self):
        for key, (owner, _label) in list(app._INSTRUMENT_LEASES.items()):
            app.release_instrument_lease(key, owner)
        app._INSTRUMENT_LEASES.clear()

    def test_idle_close_disconnects_mfli_and_releases_labone_lease(self):
        mfli_done = threading.Event()
        panel, daq, events, lease_key = _make_panel_with_zurich(on_mfli_disconnect=mfli_done.set)
        cancelled = []
        panel.zurich_after_id = "after#1"
        panel.root = types.SimpleNamespace(after_cancel=cancelled.append)

        panel.safe_close_ppms()
        self.assertTrue(mfli_done.wait(timeout=5.0), "idle close must disconnect MFLI")
        panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(cancelled, ["after#1"])
        self.assertTrue(daq.disconnected)
        self.assertIsNone(panel.zurich_controller.mfli_daq)
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertIn("disconnect:mfli", events)
        self.assertIn("disconnect:6221", events)
        self.assertIn("disconnect:ppms_env", events)

    def test_labone_lease_blocks_duplicate_until_mfli_disconnects(self):
        panel, _daq, _events, lease_key = _make_panel_with_zurich()
        other = object()
        with self.assertRaises(RuntimeError):
            app.acquire_instrument_lease(lease_key, other, "Other program")

        panel.zurich_controller.disconnect_mfli()
        app.acquire_instrument_lease(lease_key, other, "Other program")
        app.release_instrument_lease(lease_key, other)
        panel.ppms_executor.shutdown(wait=True)
        panel.zurich_executor.shutdown(wait=True)

    def test_active_scan_close_defers_visa_env_but_disconnects_mfli_early(self):
        """Characterizes confirmed A2-3 behavior deterministically:
        ScanCloseHandoff defers ppms_instrument_controllers / env until end(),
        whereas PPMSPanel.safe_close_ppms disconnects MFLI and releases
        LABONE::DEV4414 immediately on zurich_executor, causing an in-flight
        worker Zurich read to fail.
        """
        mfli_disconnected = threading.Event()
        scan_entered = threading.Event()
        allow_worker_read = threading.Event()
        worker_done = threading.Event()

        panel, _daq, events, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect=mfli_disconnected.set
        )
        panel.ppms_field_scan_running = True
        worker_errors = []

        def fake_nested_worker(*_args):
            scan_entered.set()
            if not allow_worker_read.wait(timeout=5.0):
                raise TimeoutError("allow_worker_read not signaled")
            with panel.zurich_controller.lock:
                if panel.zurich_controller.mfli_daq is None:
                    worker_errors.append("mfli_disconnected_mid_scan")
            events.append("worker:finished")

        panel._ppms_nested_scan_worker = fake_nested_worker

        def run_scan():
            try:
                panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0)
            finally:
                worker_done.set()

        panel.ppms_executor.submit(run_scan)
        self.assertTrue(scan_entered.wait(timeout=5.0))

        # Trigger close while the scan worker is still inside ScanCloseHandoff.
        panel.closing = True
        panel.safe_close_ppms()

        # MFLI disconnect completes BEFORE the scan worker even finishes!
        self.assertTrue(
            mfli_disconnected.wait(timeout=5.0),
            "zurich_executor ran disconnect_mfli before worker finished",
        )
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        # Meanwhile VISA/env disconnects are still deferred by ScanCloseHandoff.
        self.assertNotIn("disconnect:6221", events)
        self.assertNotIn("disconnect:ppms_env", events)

        allow_worker_read.set()
        self.assertTrue(worker_done.wait(timeout=5.0))
        panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, ["mfli_disconnected_mid_scan"])
        self.assertLess(events.index("disconnect:mfli"), events.index("worker:finished"))
        self.assertLess(events.index("worker:finished"), events.index("disconnect:6221"))
        self.assertLess(events.index("worker:finished"), events.index("disconnect:ppms_env"))

    @unittest.expectedFailure
    def test_mfli_disconnect_waits_for_scan_close_handoff_known_bug_a2_3(self):
        """Removal condition: remove @unittest.expectedFailure once the separate
        A2-3 fix PR coordinates zurich_controller.disconnect_mfli with ScanCloseHandoff.
        """
        mfli_disconnected = threading.Event()
        scan_entered = threading.Event()
        allow_worker_exit = threading.Event()
        worker_done = threading.Event()

        panel, _daq, events, _lease = _make_panel_with_zurich(
            on_mfli_disconnect=mfli_disconnected.set
        )
        panel.ppms_field_scan_running = True

        def fake_nested_worker(*_args):
            scan_entered.set()
            allow_worker_exit.wait(timeout=5.0)
            events.append("worker:finished")

        panel._ppms_nested_scan_worker = fake_nested_worker
        panel.ppms_executor.submit(
            lambda: (panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0), worker_done.set())
        )
        self.assertTrue(scan_entered.wait(timeout=5.0))

        panel.closing = True
        panel.safe_close_ppms()

        # Desired invariant (currently violated by A2-3): MFLI must NOT
        # disconnect until the scan worker finishes and ScanCloseHandoff ends.
        try:
            self.assertFalse(
                mfli_disconnected.is_set() or mfli_disconnected.wait(timeout=0.1),
                "A2-3: MFLI disconnected before ScanCloseHandoff.end()",
            )
        finally:
            allow_worker_exit.set()
            worker_done.wait(timeout=5.0)
            panel.ppms_executor.shutdown(wait=True)

    def test_scan_cancellation_and_exception_still_run_handoff_cleanup(self):
        for mode in ("cancel", "exception"):
            with self.subTest(mode=mode):
                app._INSTRUMENT_LEASES.clear()
                panel, _daq, events, _lease = _make_panel_with_zurich()
                panel.ppms_field_scan_running = True
                scan_entered = threading.Event()
                allow_exit = threading.Event()
                worker_done = threading.Event()

                def worker(*_args):
                    scan_entered.set()
                    allow_exit.wait(timeout=5.0)
                    if mode == "exception":
                        raise RuntimeError("simulated scan failure")

                panel._ppms_nested_scan_worker = worker

                def run_scan():
                    try:
                        try:
                            panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0)
                        except RuntimeError:
                            pass
                    finally:
                        worker_done.set()

                panel.ppms_executor.submit(run_scan)
                self.assertTrue(scan_entered.wait(timeout=5.0))

                panel.closing = True
                if mode == "cancel":
                    panel.ppms_field_scan_stop_event.set()
                panel.safe_close_ppms()
                allow_exit.set()
                self.assertTrue(worker_done.wait(timeout=5.0))
                panel.ppms_executor.shutdown(wait=True)

                self.assertIn("disconnect:6221", events)
                self.assertIn("disconnect:ppms_env", events)


if __name__ == "__main__":
    unittest.main()
