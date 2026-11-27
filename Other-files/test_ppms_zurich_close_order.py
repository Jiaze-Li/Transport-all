"""Deterministic offline regression tests for PPMS Zurich close ordering (A2-3).

Traces PPMSPanel.safe_close_ppms, CombinedControlApp.safe_close_ppms,
ScanCloseHandoff, _ppms_scan_with_final_field, zurich_executor, and
LABONE::<device> leases using threading.Event barriers and executor shutdown
synchronization (no timing guesses).

Contract verified: PPMSPanel.safe_close_ppms cancels polling and shuts down
zurich_executor without disconnecting MFLI early; CombinedControlApp.safe_close_ppms
coordinates zurich_controller.disconnect_mfli() inside ScanCloseHandoff cleanup so
MFLI state and LABONE::<device> lease remain held until the active or queued scan
worker reaches ScanCloseHandoff.end().
"""

from concurrent.futures import ThreadPoolExecutor
import threading
import types
import unittest

from test_ppms_motor_debug import app
from test_ppms_sr830_tc import build_ppms_panel


class FakeMfliDaq:
    def __init__(self):
        self.disconnected = False
        self.nodes = []

    def setInt(self, node, value):
        self.nodes.append((node, value))

    def sync(self):
        pass

    def disconnect(self):
        self.disconnected = True


def _make_panel_with_zurich(on_mfli_disconnect_done=None):
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

    events = []
    events_lock = threading.Lock()

    def record(label):
        with events_lock:
            events.append(label)

    for key in ("6221", "830_a"):
        ctrl = panel.ppms_instrument_controllers[key]
        ctrl.disconnect = lambda k=key: record(f"disconnect:{k}")
    panel.ppms_environment_controller = types.SimpleNamespace(
        connected=False,
        disconnect=lambda: record("disconnect:ppms_env"),
    )

    daq = FakeMfliDaq()
    panel.zurich_controller.mfli_daq = daq
    panel.zurich_controller.mfli_device = "dev4414"
    panel.zurich_controller.mfli_config = {"extref_demodulator": 3, "extref_channel": 0}
    lease_key = "LABONE::DEV4414"
    app.acquire_instrument_lease(lease_key, panel.zurich_controller, "PPMS Zurich MFLI")
    panel.zurich_controller.mfli_lease_resource = lease_key

    # Signal completion only AFTER disconnect_mfli() finishes clearing MFLI
    # state and releasing LABONE::<device> lease.
    orig_disconnect_mfli = panel.zurich_controller.disconnect_mfli

    def wrapped_disconnect_mfli():
        try:
            return orig_disconnect_mfli()
        finally:
            record("disconnect:mfli")
            if on_mfli_disconnect_done is not None:
                on_mfli_disconnect_done()

    panel.zurich_controller.disconnect_mfli = wrapped_disconnect_mfli
    return panel, daq, events, record, lease_key


class ZurichCloseOrderTests(unittest.TestCase):
    def setUp(self):
        app._INSTRUMENT_LEASES.clear()

    def tearDown(self):
        for key, (owner, _label) in list(app._INSTRUMENT_LEASES.items()):
            app.release_instrument_lease(key, owner)
        app._INSTRUMENT_LEASES.clear()

    def test_idle_close_disconnects_mfli_and_releases_labone_lease(self):
        mfli_done = threading.Event()
        panel, daq, events, _record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_done.set
        )
        cancelled = []
        panel.zurich_after_id = "after#1"
        panel.root = types.SimpleNamespace(after_cancel=cancelled.append)

        panel.safe_close_ppms()
        self.assertTrue(mfli_done.wait(timeout=5.0), "idle close must disconnect MFLI")
        panel.zurich_executor.shutdown(wait=True)
        panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(cancelled, ["after#1"])
        self.assertTrue(daq.disconnected)
        self.assertIsNone(panel.zurich_controller.mfli_daq)
        self.assertEqual(panel.zurich_controller.mfli_lease_resource, "")
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertIn("disconnect:mfli", events)
        self.assertIn("disconnect:6221", events)
        self.assertIn("disconnect:ppms_env", events)

    def test_labone_lease_blocks_duplicate_until_mfli_disconnects(self):
        panel, _daq, _events, _record, lease_key = _make_panel_with_zurich()
        other = object()
        with self.assertRaises(RuntimeError):
            app.acquire_instrument_lease(lease_key, other, "Other program")

        panel.zurich_controller.disconnect_mfli()
        app.acquire_instrument_lease(lease_key, other, "Other program")
        app.release_instrument_lease(lease_key, other)
        panel.ppms_executor.shutdown(wait=True)
        panel.zurich_executor.shutdown(wait=True)

    def test_active_scan_close_defers_mfli_disconnect_and_lease_until_handoff(self):
        """Active scan close must keep MFLI connected and LABONE::<device> lease
        held until ScanCloseHandoff.end() runs after the scan worker finishes.
        """
        mfli_disconnected = threading.Event()
        scan_entered = threading.Event()
        allow_worker_read = threading.Event()
        worker_done = threading.Event()

        panel, daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
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
            record("worker:finished")

        panel._ppms_nested_scan_worker = fake_nested_worker

        def run_scan():
            try:
                panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0)
            except BaseException as exc:
                worker_errors.append(f"worker_exception:{exc!r}")
            finally:
                worker_done.set()

        panel.ppms_executor.submit(run_scan)
        self.assertTrue(scan_entered.wait(timeout=5.0))

        # Trigger close while the scan worker is still inside ScanCloseHandoff.
        panel.closing = True
        panel.safe_close_ppms()
        panel.zurich_executor.shutdown(wait=True)

        # MFLI connection, LABONE lease, and VISA/env must all remain intact
        # while the scan worker still holds ScanCloseHandoff ownership.
        self.assertFalse(mfli_disconnected.is_set())
        self.assertFalse(daq.disconnected)
        self.assertIsNotNone(panel.zurich_controller.mfli_daq)
        self.assertEqual(panel.zurich_controller.mfli_lease_resource, lease_key)
        self.assertIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertNotIn("disconnect:mfli", events)
        self.assertNotIn("disconnect:6221", events)
        self.assertNotIn("disconnect:ppms_env", events)

        allow_worker_read.set()
        self.assertTrue(worker_done.wait(timeout=5.0))
        panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, [])
        self.assertTrue(mfli_disconnected.is_set())
        self.assertTrue(daq.disconnected)
        self.assertIsNone(panel.zurich_controller.mfli_daq)
        self.assertEqual(panel.zurich_controller.mfli_lease_resource, "")
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertLess(events.index("worker:finished"), events.index("disconnect:mfli"))
        self.assertLess(events.index("worker:finished"), events.index("disconnect:6221"))
        self.assertLess(events.index("worker:finished"), events.index("disconnect:ppms_env"))

    def test_mfli_disconnect_waits_for_scan_close_handoff(self):
        """MFLI disconnect must not run on zurich_executor during safe_close_ppms()
        while a scan holds ScanCloseHandoff, and must complete after handoff ends.
        """
        mfli_disconnected = threading.Event()
        scan_entered = threading.Event()
        allow_worker_exit = threading.Event()
        worker_done = threading.Event()
        worker_errors = []

        panel, _daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
        )
        try:
            panel.ppms_field_scan_running = True

            def fake_nested_worker(*_args):
                scan_entered.set()
                if not allow_worker_exit.wait(timeout=5.0):
                    raise TimeoutError("allow_worker_exit not signaled")
                record("worker:finished")

            panel._ppms_nested_scan_worker = fake_nested_worker

            def run_scan():
                try:
                    panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0)
                except BaseException as exc:
                    worker_errors.append(exc)
                finally:
                    worker_done.set()

            panel.ppms_executor.submit(run_scan)
            self.assertTrue(scan_entered.wait(timeout=5.0))

            panel.closing = True
            panel.safe_close_ppms()
            # Deterministic barrier: waits for any disconnect_mfli task submitted
            # to zurich_executor during safe_close_ppms(), and returns immediately
            # once deferred. No timing guess.
            panel.zurich_executor.shutdown(wait=True)
            disconnected_early = mfli_disconnected.is_set()
        finally:
            allow_worker_exit.set()
            self.assertTrue(worker_done.wait(timeout=5.0))
            panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, [])
        self.assertFalse(
            disconnected_early,
            "A2-3: MFLI disconnected before ScanCloseHandoff.end()",
        )
        self.assertTrue(mfli_disconnected.is_set())
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertLess(events.index("worker:finished"), events.index("disconnect:mfli"))

    def test_scan_cancellation_and_exception_still_run_handoff_cleanup(self):
        for mode in ("cancel", "exception"):
            with self.subTest(mode=mode):
                app._INSTRUMENT_LEASES.clear()
                mfli_disconnected = threading.Event()
                panel, daq, events, record, lease_key = _make_panel_with_zurich(
                    on_mfli_disconnect_done=mfli_disconnected.set
                )
                panel.ppms_field_scan_running = True
                scan_entered = threading.Event()
                allow_exit = threading.Event()
                worker_done = threading.Event()
                unexpected_errors = []

                def worker(*_args):
                    scan_entered.set()
                    if not allow_exit.wait(timeout=5.0):
                        raise TimeoutError("allow_exit not signaled")
                    try:
                        if mode == "exception":
                            raise RuntimeError("simulated scan failure")
                    finally:
                        record("worker:finished")

                panel._ppms_nested_scan_worker = worker

                def run_scan():
                    try:
                        try:
                            panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0)
                        except RuntimeError:
                            if mode != "exception":
                                raise
                    except BaseException as exc:
                        unexpected_errors.append(exc)
                    finally:
                        worker_done.set()

                panel.ppms_executor.submit(run_scan)
                self.assertTrue(scan_entered.wait(timeout=5.0))

                panel.closing = True
                if mode == "cancel":
                    panel.ppms_field_scan_stop_event.set()
                panel.safe_close_ppms()
                self.assertFalse(mfli_disconnected.is_set())
                self.assertIn(lease_key, app._INSTRUMENT_LEASES)

                allow_exit.set()
                self.assertTrue(worker_done.wait(timeout=5.0))
                panel.zurich_executor.shutdown(wait=True)
                panel.ppms_executor.shutdown(wait=True)

                self.assertEqual(unexpected_errors, [])
                self.assertTrue(mfli_disconnected.is_set())
                self.assertTrue(daq.disconnected)
                self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
                self.assertIn("disconnect:6221", events)
                self.assertIn("disconnect:ppms_env", events)
                self.assertIn("disconnect:mfli", events)
                self.assertLess(events.index("worker:finished"), events.index("disconnect:mfli"))

    def test_queued_scan_close_with_saturated_five_workers_defers_mfli_until_handoff(self):
        mfli_disconnected = threading.Event()
        blockers_entered = 0
        blockers_lock = threading.Lock()
        all_blockers_entered = threading.Event()
        release_blockers = threading.Event()
        queued_scan_done = threading.Event()
        worker_errors = []

        panel, daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
        )

        def blocker():
            nonlocal blockers_entered
            with blockers_lock:
                blockers_entered += 1
                if blockers_entered == 5:
                    all_blockers_entered.set()
            if not release_blockers.wait(timeout=5.0):
                raise TimeoutError("release_blockers not signaled")

        for _ in range(5):
            panel.ppms_executor.submit(blocker)
        self.assertTrue(all_blockers_entered.wait(timeout=5.0))

        def fake_nested_worker(*_args):
            record("worker:finished")

        panel._ppms_nested_scan_worker = fake_nested_worker
        panel.ppms_field_scan_running = True
        panel._ppms_close_handoff().begin()

        def run_queued_scan():
            try:
                panel._ppms_scan_with_final_field(None, 100.0, 0.5, 0)
            except BaseException as exc:
                worker_errors.append(exc)
            finally:
                queued_scan_done.set()

        panel.ppms_executor.submit(run_queued_scan)

        panel.closing = True
        panel.safe_close_ppms()

        self.assertFalse(mfli_disconnected.is_set())
        self.assertIsNotNone(panel.zurich_controller.mfli_daq)
        self.assertIn(lease_key, app._INSTRUMENT_LEASES)

        release_blockers.set()
        self.assertTrue(queued_scan_done.wait(timeout=5.0))
        panel.zurich_executor.shutdown(wait=True)
        panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, [])
        self.assertTrue(mfli_disconnected.is_set())
        self.assertTrue(daq.disconnected)
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertLess(events.index("worker:finished"), events.index("disconnect:mfli"))

    def test_repeated_safe_close_runs_mfli_disconnect_once(self):
        panel, daq, events, _record, lease_key = _make_panel_with_zurich()
        panel.safe_close_ppms()
        panel.safe_close_ppms()
        panel.zurich_executor.shutdown(wait=True)
        panel.ppms_executor.shutdown(wait=True)

        self.assertTrue(daq.disconnected)
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertEqual(events.count("disconnect:mfli"), 1)

    def test_inflight_zurich_connect_finishes_before_disconnect_and_releases_lease(self):
        mfli_disconnected = threading.Event()
        connect_started = threading.Event()
        allow_connect_finish = threading.Event()
        after_cancelled = threading.Event()
        connect_errors = []

        panel, daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
        )
        # Reset to disconnected state before simulating an in-flight connect task.
        app.release_instrument_lease(lease_key, panel.zurich_controller)
        panel.zurich_controller.mfli_daq = None
        panel.zurich_controller.mfli_device = ""
        panel.zurich_controller.mfli_lease_resource = ""
        panel.zurich_after_id = "after#connect"
        panel.root = types.SimpleNamespace(after_cancel=lambda _aid: after_cancelled.set())

        def connect_work():
            try:
                connect_started.set()
                if not allow_connect_finish.wait(timeout=5.0):
                    raise TimeoutError("allow_connect_finish not signaled")
                with panel.zurich_controller.lock:
                    app.acquire_instrument_lease(
                        lease_key, panel.zurich_controller, "PPMS Zurich MFLI"
                    )
                    panel.zurich_controller.mfli_daq = daq
                    panel.zurich_controller.mfli_device = "dev4414"
                    panel.zurich_controller.mfli_lease_resource = lease_key
                record("connect:finished")
            except BaseException as exc:
                connect_errors.append(exc)

        panel.zurich_executor.submit(connect_work)
        self.assertTrue(connect_started.wait(timeout=5.0))

        close_thread = threading.Thread(target=panel.safe_close_ppms)
        close_thread.start()
        self.assertTrue(after_cancelled.wait(timeout=5.0))
        self.assertFalse(mfli_disconnected.is_set())

        allow_connect_finish.set()
        close_thread.join(timeout=5.0)
        self.assertFalse(close_thread.is_alive())
        panel.ppms_executor.shutdown(wait=True)

        self.assertEqual(connect_errors, [])
        self.assertTrue(mfli_disconnected.is_set())
        self.assertTrue(daq.disconnected)
        self.assertIsNone(panel.zurich_controller.mfli_daq)
        self.assertEqual(panel.zurich_controller.mfli_lease_resource, "")
        self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
        self.assertLess(events.index("connect:finished"), events.index("disconnect:mfli"))

    def test_cleanup_failure_does_not_skip_other_instruments_or_raise(self):
        for failing in ("ppms_env", "mfli"):
            with self.subTest(failing=failing):
                app._INSTRUMENT_LEASES.clear()
                panel, _daq, events, _record, lease_key = _make_panel_with_zurich()
                logged = []
                panel.append_log = logged.append
                if failing == "ppms_env":
                    def fail_env():
                        raise RuntimeError("env disconnect failed")
                    panel.ppms_environment_controller.disconnect = fail_env
                else:
                    def fail_mfli():
                        raise RuntimeError("mfli disconnect failed")
                    panel.zurich_controller.disconnect_mfli = fail_mfli

                panel.safe_close_ppms()
                panel.zurich_executor.shutdown(wait=True)
                panel.ppms_executor.shutdown(wait=True)

                self.assertIn("disconnect:6221", events)
                if failing == "ppms_env":
                    self.assertIn("disconnect:mfli", events)
                    self.assertNotIn(lease_key, app._INSTRUMENT_LEASES)
                    self.assertTrue(any("env disconnect failed" in msg for msg in logged))
                else:
                    self.assertIn("disconnect:ppms_env", events)
                    self.assertTrue(any("mfli disconnect failed" in msg for msg in logged))


if __name__ == "__main__":
    unittest.main()
