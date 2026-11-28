"""The deferred close (scan active) must not disconnect while other
in-flight hardware tasks on the PPMS executor are still running."""

import threading
import unittest
from unittest import mock

from transport.ppms.state import PPMSAppState


class DeferredCloseDrainTests(unittest.TestCase):
    def _state(self):
        state = PPMSAppState(lambda _msg: None)
        state.ppms_environment_controller = mock.Mock(connected=False)
        state.zurich_controller = mock.Mock()
        return state

    def test_disconnect_waits_for_other_inflight_tasks_and_cancels_queued_ones(self):
        state = self._state()
        events = []
        for controller in state.ppms_instrument_controllers.values():
            controller.disconnect = lambda: events.append("disconnect")
        state.ppms_environment_controller.disconnect = lambda: events.append("env_disconnect")

        read_started, release_read, scan_closing = (threading.Event() for _ in range(3))
        scan_can_end = threading.Event()

        def inflight_read():
            read_started.set()
            release_read.wait(5)
            events.append("read_done")

        def scan_worker():
            state.close_handoff.begin()
            scan_closing.set()
            scan_can_end.wait(5)
            state.close_handoff.end()  # runs the deferred cleanup here

        state.scan_running = True
        scan_future = state.ppms_executor.submit(scan_worker)
        read_future = state.ppms_executor.submit(inflight_read)
        self.assertTrue(read_started.wait(5))
        self.assertTrue(scan_closing.wait(5))
        # Saturate the pool so one more task is only queued.
        blockers = [threading.Event() for _ in range(3)]
        for blocker in blockers:
            state.ppms_executor.submit(blocker.wait, 5)
        queued_ran = []
        queued = state.ppms_executor.submit(lambda: queued_ran.append(1))

        state.safe_close()
        self.assertEqual(events, [], "deferred: nothing disconnected yet")
        scan_can_end.set()
        threading.Timer(0.3, release_read.set).start()
        for blocker in blockers:
            threading.Timer(0.3, blocker.set).start()
        scan_future.result(10)
        read_future.result(10)

        self.assertIn("read_done", events)
        self.assertIn("disconnect", events)
        self.assertLess(events.index("read_done"), events.index("disconnect"))
        self.assertTrue(queued.cancelled(), "queued tasks are cancelled, not run after disconnect")
        self.assertEqual(queued_ran, [])


if __name__ == "__main__":
    unittest.main()
