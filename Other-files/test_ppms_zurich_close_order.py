"""Deterministic offline regression tests for PPMS Zurich close ordering (A2-3).

Traces PPMSPanelView.safe_close_ppms, PPMSAppState.safe_close,
ScanCloseHandoff, PPMSScanRunner.run, zurich_executor, and
LABONE::<device> leases using threading.Event barriers and executor shutdown
synchronization (no timing guesses).
"""

from concurrent.futures import ThreadPoolExecutor
import threading
import types
import unittest

from transport.ppms.controllers import visa as visa_ctrl
from transport.ppms.controllers.zurich import ZurichMFLIController
from transport.ppms.engine.runner import PPMSScanRunner, ScanRequest
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMSPanelView


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


def _make_request(mode="Current"):
    return ScanRequest(
        mode=mode,
        sequence=((1, 1, 1, 0.0),),
        rate_oe_s=100.0,
        stable_s=0.5,
        approach_code=0,
        transport_settings={"harmonic_passes": ({"active_lockins": ()},)},
        total_points=1,
        estimated_seconds=1.0,
        sr830_tc_code=10,
    )


def _make_panel_with_zurich(on_mfli_disconnect_done=None):
    state = PPMSAppState(lambda _msg: None)
    state.active_scan_mode = "Current"

    panel = PPMSPanelView.__new__(PPMSPanelView)
    panel.state = state
    panel.shell = types.SimpleNamespace(
        closing=False,
        post_ui=lambda cb: None,
        thread_log=lambda _msg: None,
        append_log=lambda _msg: None,
    )
    panel.root = types.SimpleNamespace(after_cancel=lambda _aid: None)
    panel.ppms_poll_after_id = None
    panel.zurich_after_id = None
    panel.ppms_field_rate_var = types.SimpleNamespace(get=lambda: "100")
    panel.ppms_field_approach_var = types.SimpleNamespace(get=lambda: "No overshoot")
    panel.runner = PPMSScanRunner(state, post_ui=lambda cb: None)
    panel.runner._zero_outputs_after_scan = lambda sink=None: {"6221": "zeroed"}

    events = []
    events_lock = threading.Lock()

    def record(label):
        with events_lock:
            events.append(label)

    for key in ("6221", "830_a", "830_b", "830_c", "2400"):
        ctrl = state.ppms_instrument_controllers[key]
        ctrl.disconnect = lambda k=key: record(f"disconnect:{k}")
    state.ppms_environment_controller = types.SimpleNamespace(
        connected=False,
        disconnect=lambda: record("disconnect:ppms_env"),
    )

    daq = FakeMfliDaq()
    state.zurich_controller = ZurichMFLIController(lambda _msg: None)
    state.zurich_controller.mfli_daq = daq
    state.zurich_controller.mfli_device = "dev4414"
    state.zurich_controller.mfli_config = {"extref_demodulator": 3, "extref_channel": 0}
    lease_key = "LABONE::DEV4414"
    visa_ctrl.acquire_instrument_lease(lease_key, state.zurich_controller, "PPMS Zurich MFLI")
    state.zurich_controller.mfli_lease_resource = lease_key

    orig_disconnect_mfli = state.zurich_controller.disconnect_mfli

    def wrapped_disconnect_mfli():
        try:
            return orig_disconnect_mfli()
        finally:
            record("disconnect:mfli")
            if on_mfli_disconnect_done is not None:
                on_mfli_disconnect_done()

    state.zurich_controller.disconnect_mfli = wrapped_disconnect_mfli
    return panel, state, daq, events, record, lease_key


class ZurichCloseOrderTests(unittest.TestCase):
    def setUp(self):
        visa_ctrl._INSTRUMENT_LEASES.clear()

    def tearDown(self):
        for key, (owner, _label) in list(visa_ctrl._INSTRUMENT_LEASES.items()):
            visa_ctrl.release_instrument_lease(key, owner)
        visa_ctrl._INSTRUMENT_LEASES.clear()

    def test_idle_close_disconnects_mfli_and_releases_labone_lease(self):
        mfli_done = threading.Event()
        panel, state, daq, events, _record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_done.set
        )
        cancelled = []
        panel.zurich_after_id = "after#1"
        panel.root = types.SimpleNamespace(after_cancel=cancelled.append)

        panel.safe_close_ppms()
        self.assertTrue(mfli_done.wait(timeout=5.0), "idle close must disconnect MFLI")
        state.zurich_executor.shutdown(wait=True)
        state.ppms_executor.shutdown(wait=True)

        self.assertEqual(cancelled, ["after#1"])
        self.assertTrue(daq.disconnected)
        self.assertIsNone(state.zurich_controller.mfli_daq)
        self.assertEqual(state.zurich_controller.mfli_lease_resource, "")
        self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertIn("disconnect:mfli", events)
        self.assertIn("disconnect:6221", events)
        self.assertIn("disconnect:ppms_env", events)

    def test_labone_lease_blocks_duplicate_until_mfli_disconnects(self):
        _panel, state, _daq, _events, _record, lease_key = _make_panel_with_zurich()
        other = object()
        with self.assertRaises(RuntimeError):
            visa_ctrl.acquire_instrument_lease(lease_key, other, "Other program")

        state.zurich_controller.disconnect_mfli()
        visa_ctrl.acquire_instrument_lease(lease_key, other, "Other program")
        visa_ctrl.release_instrument_lease(lease_key, other)
        state.ppms_executor.shutdown(wait=True)
        state.zurich_executor.shutdown(wait=True)

    def test_active_scan_close_defers_mfli_disconnect_and_lease_until_handoff(self):
        mfli_disconnected = threading.Event()
        scan_entered = threading.Event()
        allow_worker_read = threading.Event()
        worker_done = threading.Event()

        panel, state, daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
        )
        state.scan_running = True
        worker_errors = []

        def fake_nested_worker(*_args, **_kwargs):
            scan_entered.set()
            if not allow_worker_read.wait(timeout=5.0):
                raise TimeoutError("allow_worker_read not signaled")
            with state.zurich_controller.lock:
                if state.zurich_controller.mfli_daq is None:
                    worker_errors.append("mfli_disconnected_mid_scan")
            record("worker:finished")

        panel.runner.nested_scan_worker = fake_nested_worker

        def run_scan():
            try:
                panel.runner.run(_make_request(), sink=None)
            except BaseException as exc:
                worker_errors.append(f"worker_exception:{exc!r}")
            finally:
                worker_done.set()

        state.ppms_executor.submit(run_scan)
        self.assertTrue(scan_entered.wait(timeout=5.0))

        panel.shell.closing = True
        panel.safe_close_ppms()
        state.zurich_executor.shutdown(wait=True)

        self.assertFalse(mfli_disconnected.is_set())
        self.assertFalse(daq.disconnected)
        self.assertIsNotNone(state.zurich_controller.mfli_daq)
        self.assertEqual(state.zurich_controller.mfli_lease_resource, lease_key)
        self.assertIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertNotIn("disconnect:mfli", events)
        self.assertNotIn("disconnect:6221", events)
        self.assertNotIn("disconnect:ppms_env", events)

        allow_worker_read.set()
        self.assertTrue(worker_done.wait(timeout=5.0))
        state.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, [])
        self.assertTrue(mfli_disconnected.is_set())
        self.assertTrue(daq.disconnected)
        self.assertIsNone(state.zurich_controller.mfli_daq)
        self.assertEqual(state.zurich_controller.mfli_lease_resource, "")
        self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertLess(events.index("worker:finished"), events.index("disconnect:mfli"))
        self.assertLess(events.index("worker:finished"), events.index("disconnect:6221"))
        self.assertLess(events.index("worker:finished"), events.index("disconnect:ppms_env"))

    def test_mfli_disconnect_waits_for_scan_close_handoff(self):
        mfli_disconnected = threading.Event()
        scan_entered = threading.Event()
        allow_worker_exit = threading.Event()
        worker_done = threading.Event()
        worker_errors = []

        panel, state, _daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
        )
        try:
            state.scan_running = True

            def fake_nested_worker(*_args, **_kwargs):
                scan_entered.set()
                if not allow_worker_exit.wait(timeout=5.0):
                    raise TimeoutError("allow_worker_exit not signaled")
                record("worker:finished")

            panel.runner.nested_scan_worker = fake_nested_worker

            def run_scan():
                try:
                    panel.runner.run(_make_request(), sink=None)
                except BaseException as exc:
                    worker_errors.append(exc)
                finally:
                    worker_done.set()

            state.ppms_executor.submit(run_scan)
            self.assertTrue(scan_entered.wait(timeout=5.0))

            panel.shell.closing = True
            panel.safe_close_ppms()
            state.zurich_executor.shutdown(wait=True)
            disconnected_early = mfli_disconnected.is_set()
        finally:
            allow_worker_exit.set()
            self.assertTrue(worker_done.wait(timeout=5.0))
            state.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, [])
        self.assertFalse(disconnected_early, "A2-3: MFLI disconnected before ScanCloseHandoff.end()")
        self.assertTrue(mfli_disconnected.is_set())
        self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertLess(events.index("worker:finished"), events.index("disconnect:mfli"))

    def test_scan_cancellation_and_exception_still_run_handoff_cleanup(self):
        for mode in ("cancel", "exception"):
            with self.subTest(mode=mode):
                visa_ctrl._INSTRUMENT_LEASES.clear()
                mfli_disconnected = threading.Event()
                panel, state, daq, events, record, lease_key = _make_panel_with_zurich(
                    on_mfli_disconnect_done=mfli_disconnected.set
                )
                state.scan_running = True
                scan_entered = threading.Event()
                allow_exit = threading.Event()
                worker_done = threading.Event()
                unexpected_errors = []

                def worker(*_args, **_kwargs):
                    scan_entered.set()
                    if not allow_exit.wait(timeout=5.0):
                        raise TimeoutError("allow_exit not signaled")
                    try:
                        if mode == "exception":
                            raise RuntimeError("simulated scan failure")
                    finally:
                        record("worker:finished")

                panel.runner.nested_scan_worker = worker
                finished_outcomes = []
                sink = types.SimpleNamespace(
                    capture_plot_snapshots=lambda *_a, **_kw: None,
                    on_zero_persist_button_reset=lambda: None,
                    on_scan_finished=lambda **kw: finished_outcomes.append(kw),
                    log=lambda _m: None,
                )

                def run_scan():
                    try:
                        panel.runner.run(_make_request(), sink=sink)
                    except BaseException as exc:
                        unexpected_errors.append(exc)
                    finally:
                        worker_done.set()

                state.ppms_executor.submit(run_scan)
                self.assertTrue(scan_entered.wait(timeout=5.0))

                panel.shell.closing = True
                if mode == "cancel":
                    state.scan_stop_event.set()
                panel.safe_close_ppms()
                self.assertFalse(mfli_disconnected.is_set())
                self.assertIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)

                allow_exit.set()
                self.assertTrue(worker_done.wait(timeout=5.0))
                state.zurich_executor.shutdown(wait=True)
                state.ppms_executor.shutdown(wait=True)

                self.assertEqual(unexpected_errors, [])
                self.assertTrue(mfli_disconnected.is_set())
                self.assertTrue(daq.disconnected)
                self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
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

        panel, state, daq, events, record, lease_key = _make_panel_with_zurich(
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
            state.ppms_executor.submit(blocker)
        self.assertTrue(all_blockers_entered.wait(timeout=5.0))

        def fake_nested_worker(*_args, **_kwargs):
            record("worker:finished")

        panel.runner.nested_scan_worker = fake_nested_worker
        state.scan_running = True
        state.close_handoff.begin()

        def run_queued_scan():
            try:
                panel.runner.run(_make_request(), sink=None)
            except BaseException as exc:
                worker_errors.append(exc)
            finally:
                state.close_handoff.end()
                queued_scan_done.set()

        state.ppms_executor.submit(run_queued_scan)

        panel.shell.closing = True
        panel.safe_close_ppms()

        self.assertFalse(mfli_disconnected.is_set())
        self.assertIsNotNone(state.zurich_controller.mfli_daq)
        self.assertIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)

        release_blockers.set()
        self.assertTrue(queued_scan_done.wait(timeout=5.0))
        state.zurich_executor.shutdown(wait=True)
        state.ppms_executor.shutdown(wait=True)

        self.assertEqual(worker_errors, [])
        self.assertTrue(mfli_disconnected.is_set())
        self.assertTrue(daq.disconnected)
        self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertIn("disconnect:mfli", events)
        self.assertNotIn("worker:finished", events)

    def test_repeated_safe_close_runs_mfli_disconnect_once(self):
        panel, state, daq, events, _record, lease_key = _make_panel_with_zurich()
        panel.safe_close_ppms()
        panel.safe_close_ppms()
        state.zurich_executor.shutdown(wait=True)
        state.ppms_executor.shutdown(wait=True)

        self.assertTrue(daq.disconnected)
        self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertEqual(events.count("disconnect:mfli"), 1)

    def test_inflight_zurich_connect_finishes_before_disconnect_and_releases_lease(self):
        mfli_disconnected = threading.Event()
        connect_started = threading.Event()
        allow_connect_finish = threading.Event()
        after_cancelled = threading.Event()
        connect_errors = []

        panel, state, daq, events, record, lease_key = _make_panel_with_zurich(
            on_mfli_disconnect_done=mfli_disconnected.set
        )
        visa_ctrl.release_instrument_lease(lease_key, state.zurich_controller)
        state.zurich_controller.mfli_daq = None
        state.zurich_controller.mfli_device = ""
        state.zurich_controller.mfli_lease_resource = ""
        panel.zurich_after_id = "after#connect"
        panel.root = types.SimpleNamespace(after_cancel=lambda _aid: after_cancelled.set())

        def connect_work():
            try:
                connect_started.set()
                if not allow_connect_finish.wait(timeout=5.0):
                    raise TimeoutError("allow_connect_finish not signaled")
                with state.zurich_controller.lock:
                    visa_ctrl.acquire_instrument_lease(
                        lease_key, state.zurich_controller, "PPMS Zurich MFLI"
                    )
                    state.zurich_controller.mfli_daq = daq
                    state.zurich_controller.mfli_device = "dev4414"
                    state.zurich_controller.mfli_lease_resource = lease_key
                record("connect:finished")
            except BaseException as exc:
                connect_errors.append(exc)

        state.zurich_executor.submit(connect_work)
        self.assertTrue(connect_started.wait(timeout=5.0))

        close_thread = threading.Thread(target=panel.safe_close_ppms)
        close_thread.start()
        self.assertTrue(after_cancelled.wait(timeout=5.0))
        self.assertFalse(mfli_disconnected.is_set())

        allow_connect_finish.set()
        close_thread.join(timeout=5.0)
        self.assertFalse(close_thread.is_alive())
        state.ppms_executor.shutdown(wait=True)

        self.assertEqual(connect_errors, [])
        self.assertTrue(mfli_disconnected.is_set())
        self.assertTrue(daq.disconnected)
        self.assertIsNone(state.zurich_controller.mfli_daq)
        self.assertEqual(state.zurich_controller.mfli_lease_resource, "")
        self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
        self.assertLess(events.index("connect:finished"), events.index("disconnect:mfli"))

    def test_cleanup_failure_does_not_skip_other_instruments_or_raise(self):
        for failing in ("ppms_env", "mfli"):
            with self.subTest(failing=failing):
                visa_ctrl._INSTRUMENT_LEASES.clear()
                panel, state, _daq, events, _record, lease_key = _make_panel_with_zurich()
                logged = []
                panel.shell.append_log = logged.append
                if failing == "ppms_env":
                    def fail_env():
                        raise RuntimeError("env disconnect failed")
                    state.ppms_environment_controller.disconnect = fail_env
                else:
                    def fail_mfli():
                        raise RuntimeError("mfli disconnect failed")
                    state.zurich_controller.disconnect_mfli = fail_mfli

                panel.safe_close_ppms()
                state.zurich_executor.shutdown(wait=True)
                state.ppms_executor.shutdown(wait=True)

                self.assertIn("disconnect:6221", events)
                if failing == "ppms_env":
                    self.assertIn("disconnect:mfli", events)
                    self.assertNotIn(lease_key, visa_ctrl._INSTRUMENT_LEASES)
                    self.assertTrue(any("env disconnect failed" in msg for msg in logged))
                else:
                    self.assertIn("disconnect:ppms_env", events)
                    self.assertTrue(any("mfli disconnect failed" in msg for msg in logged))


if __name__ == "__main__":
    unittest.main()
