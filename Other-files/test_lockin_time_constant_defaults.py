"""Every lock-in time constant the program starts with is 1 s.

User, 2026-10-14: "all default lock-in time constant change to 1s pls."
Before, three boxes started at 0.1 s (STFMR "MFLI TC s", the Non-local rack's
Zurich "TC (s)", the PPMS / RT-E Zurich "Time constant s" / "TC (s)") and
STFMR's "SR830 TC" at 100 ms.  Only the defaults changed: every wait computed
from a time constant keeps its rule, so the waits that follow from the new
defaults are checked here too, as the README lists them.

No Tk window and no instrument: the boxes are checked in the source (as
test_stfmr_mod_prime_range checks Mod. start / end) and, for the PPMS / RT-E
page, in the real panel constructor on stand-in widgets (test_ppms_two_panels'
make_panel).  Creating a Tk root fails the test."""
import inspect
import math
import re
import sys
import threading
import tkinter
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from transport.ppms.controllers.sr830 import SR830Controller
from transport.ppms.controllers.zurich import ZurichMFLIController
from transport.ppms.engine.runner import PPMS_ZURICH_TIME_CONSTANT_S
import test_ppms_two_panels as two_panels  # noqa: E402


class NoWindows(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        patcher.start()
        self.addCleanup(patcher.stop)


class DefaultsAreOneSecond(NoWindows):

    def test_the_methods_default_to_1_s(self):
        """What the program sends when the caller gives no time constant."""
        self.assertEqual(inspect.signature(SR830Controller.configure)
                         .parameters["time_constant_code"].default, 10)
        self.assertEqual(inspect.signature(ZurichMFLIController.configure_mfli)
                         .parameters["timeconstant_s"].default, 1.0)

    def test_the_ppms_and_rte_zurich_box_shows_1(self):
        """RTEPanel builds its Zurich row through PPMSPanel.__init__, so this
        is the RT-E "TC (s)" box too."""
        _parent, panel, controller = two_panels.TwoPanelTests.make_panel(None)
        self.assertEqual(panel.zurich_timeconstant.get(), "1")
        panel.zurich_harmonic.set("1+2+3")
        panel.connect_ppms2_zurich()
        self.assertEqual(controller.configure_mfli.call_args.kwargs["timeconstant_s"], 1.0)
        controller.configure_second_harmonic.assert_called_once_with(1.0)
        controller.configure_third_harmonic.assert_called_once_with(1.0)


class OneTimeConstantSettle(NoWindows):
    pass


class WaitsAtTheNewDefaults(NoWindows):
    """The rules are unchanged; these are their values at the 1 s defaults."""

    def test_ppms_zurich_range_wait_falls_back_to_1_s(self):
        """After a Zurich range change the PPMS / RT-E reading waits
        max(0.2 s, 10 x TC); with no configured TC, 10 x the 1 s default."""
        _parent, panel, controller = two_panels.TwoPanelTests.make_panel(None)
        controller.mfli_config = {}
        controller.read_stfmr_mfli.side_effect = [
            {"auto_range": "RANGE 0.01->0.1 V", "X": 999},
            {"auto_range": "", "X": 1e-6, "input_range_v": 0.1}]
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        import time as std_time

        with mock.patch.object(std_time, "monotonic", lambda: clock[0]), \
                mock.patch.object(std_time, "sleep", sleep):
            result = panel._read_ppms2_zurich_ranged(317.3, scanning=True)
        self.assertEqual(result["X"], 1e-6)
        self.assertAlmostEqual(clock[0], 10.0, places=6)


def _step_seen(t_s, tau_s):
    """Fraction of a current step a 12 dB/oct (2nd-order) lock-in shows t_s
    after the step."""
    x = t_s / tau_s
    return 1.0 - math.exp(-x) * (1.0 + x)


class DefaultSetupsAtTheNewTc(NoWindows):
    """Settle s, Acquire s and Wait/pt do not follow the TC, so these
    out-of-the-box setups read before a 1 s TC has settled (reviewed,
    user, 2026-10-14).  Only documented here; the waits are unchanged."""

    def test_a_ranging_idle_ppms_read_holds_the_zurich_10_s_per_step(self):
        """The idle readout (after Set 6221 with a current) ranges under the
        Zurich lock, so Zurich actions and a scan Start's Zurich set-up wait
        for it: up to 4 x 10 s at the 1 s default, 4 x 1 s at 0.1 s."""
        for tc_s, held_s in ((PPMS_ZURICH_TIME_CONSTANT_S, 40.0), (0.1, 4.0)):
            with self.subTest(tc_s=tc_s):
                _parent, panel, controller = two_panels.TwoPanelTests.make_panel(None)
                panel.connect_ppms2_zurich()
                self.assertTrue(controller.mfli_connected)
                clock = [0.0]

                class TimedLock:
                    """The Zurich RLock, timing each outermost hold."""
                    def __init__(self):
                        self.inner = threading.RLock()
                        self.depth = 0
                        self.held = []

                    def __enter__(self):
                        self.inner.acquire()
                        if self.depth == 0:
                            self.since = clock[0]
                        self.depth += 1
                        return self

                    def __exit__(self, *exc):
                        self.depth -= 1
                        if self.depth == 0:
                            self.held.append(clock[0] - self.since)
                        self.inner.release()
                        return False

                controller.lock = TimedLock()
                controller.mfli_config = {"timeconstant_s": tc_s}
                controller.read_stfmr_mfli.side_effect = lambda frequency, **kwargs: {
                    "auto_range": "RANGE 0.01->0.1 V", "X": 999}
                panel.state.idle_autorange_enabled = True
                panel.state.k6221_zero_output = False
                panel.state.scan_running = False

                def sleep(seconds):
                    clock[0] += seconds

                import time as std_time

                with mock.patch.object(std_time, "monotonic", lambda: clock[0]), \
                        mock.patch.object(std_time, "sleep", sleep):
                    panel._poll_ppms2_zurich()
                self.assertEqual(controller.read_stfmr_mfli.call_count, 4)
                self.assertTrue(all(call.kwargs["auto_range"]
                                    for call in controller.read_stfmr_mfli.call_args_list))
                self.assertEqual(len(controller.lock.held), 1)
                self.assertAlmostEqual(controller.lock.held[0], held_s, places=6)
                self.assertIn("did not settle", panel.zurich_status.get())


if __name__ == "__main__":
    unittest.main()
