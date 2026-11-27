"""Fixes from the whole-program review (2026-10-09), one check each.

No Tk and no instrument: stand-ins record what the code asks of them."""
import threading
import types
import unittest
from unittest import mock

import numpy as np

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Recorder:
    """An instrument that records writes and answers queries from a table."""

    def __init__(self, replies=None):
        self.log, self.replies = [], dict(replies or {})

    def write(self, command):
        self.log.append(command)

    def query(self, command):
        self.log.append(command)
        reply = self.replies[command]
        return reply.pop(0) if isinstance(reply, list) else reply


class SharedHelperTests(unittest.TestCase):
    def test_a_2_5_step_keeps_its_half_steps_in_the_labels(self):
        self.assertEqual(app.format_nice_tick(3002.5, 2.5), "3002.5")
        self.assertEqual(app.format_nice_tick(10005.0, 2.5), "10005.0")
        self.assertEqual(app.format_nice_tick(3000.0, 5.0), "3000")

    def test_the_short_gpib_spelling_shares_the_lease(self):
        self.assertEqual(app.instrument_lease_key("GPIB0::30"), app.instrument_lease_key("GPIB0::30::INSTR"))


class MokeTests(unittest.TestCase):
    def test_magnet_disconnect_writes_0_a_first(self):
        a = bare_app()
        calls = []
        bop = mock.Mock(connected=True)
        bop.set_current.side_effect = lambda value: calls.append(("0 A", value))
        channel = mock.Mock(bop=bop)
        channel.close.side_effect = lambda: calls.append("close")
        a.magnet_channels = {"OUT": channel, "IN": mock.Mock(bop=None)}
        a.magnet_auto_poll_var = Var(True)
        a._submit_magnet_task = lambda label, worker, success: calls.append(("result", worker()))
        a.disconnect_magnets()
        self.assertEqual(calls[:2], [("0 A", 0.0), "close"])
        self.assertEqual(calls[-1], ("result", []), "no warnings")

    def test_a_frame_goes_into_the_scan_samples_once(self):
        window = app.TkCameraWindow.__new__(app.TkCameraWindow)
        window.roi = {"cx": 5.0, "cy": 5.0, "w": 4.0, "h": 4.0, "angle": 0.0}
        window.selecting_roi, window.roi_start, window.roi_end = False, None, None
        window.mapping = object()
        window.app = mock.Mock()
        image = np.ones((10, 10), dtype=float)
        frame = types.SimpleNamespace(raw=image, difference=image, display_difference=None)
        with mock.patch.object(app, "np", np):
            for _redraw in range(3):   # e.g. pan, zoom, a CLim edit
                window._ingest_roi_values(frame)
            self.assertEqual(window.app.ingest_2410_roi_sample.call_count, 1)
            window._ingest_roi_values(types.SimpleNamespace(raw=image, difference=image, display_difference=None))
        self.assertEqual(window.app.ingest_2410_roi_sample.call_count, 2, "a new frame counts")

    def test_moke_24_keithley_goes_off_before_its_range_changes(self):
        controller = app.Keithley2410Controller.__new__(app.Keithley2410Controller)
        controller.lock = threading.RLock()
        controller.instrument = object()
        controller.output_enabled = True       # a manual Write left it on
        controller.model = "2410"
        controller.compliance_a = 1e-3
        calls = []
        controller._write_checked = calls.append
        controller._configure_voltage_source_locked = lambda v: calls.append(f"configure {v:g}")
        controller.enable_output(100.0)
        self.assertEqual(calls, [":SOUR:VOLT 0", ":OUTP OFF", "configure 100", ":OUTP ON"])


class StfmrTests(unittest.TestCase):
    pass

class PpmsTests(unittest.TestCase):
    pass

if __name__ == "__main__":
    unittest.main()
