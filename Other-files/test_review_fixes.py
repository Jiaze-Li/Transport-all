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

    def test_a_scan_axis_does_not_repeat_its_end(self):
        self.assertEqual(app.ppms_scan_axis(0.1, 0.4, 0.1)[-2:], (0.30000000000000004, 0.4))
        self.assertEqual(len(app.ppms_scan_axis(0.1, 0.4, 0.1)), 4)

    def test_the_short_gpib_spelling_shares_the_lease(self):
        self.assertEqual(app.instrument_lease_key("GPIB0::30"), app.instrument_lease_key("GPIB0::30::INSTR"))


class MokeTests(unittest.TestCase):
    pass

class StfmrTests(unittest.TestCase):
    def test_the_overload_latched_by_a_range_change_is_dropped(self):
        controller = app.STFMRSR830Controller.__new__(app.STFMRSR830Controller)
        controller.lock = threading.RLock()
        controller.auto_range_enabled = True
        controller.sensitivity_index = 17
        controller.low_range_count = controller.range_cooldown = 0
        snap = "0.9e-3,0,0.95e-3,0,17.3"   # near full scale of SENS 17 (1 mV)
        controller.instrument = Recorder({
            "SNAP? 1,2,3,4,9": [snap, "1e-4,0,1e-4,0,17.3"],
            "LIAS?": ["0", "4", "0"],   # 4: latched by the range change
            "SENS?": "18",
        })
        controller.read_sample(17.3)                      # steps up to 18
        sample = controller.read_sample(17.3)
        self.assertEqual(controller.sensitivity_index, 18, "no further step")
        self.assertEqual(sample["status"], 0, "the latched bit was read and dropped")

    def test_zero_off_stops_a_running_fmr_scan_first(self):
        a = bare_app()
        kepco = mock.Mock(connected=True)
        a.stfmr_controllers = {"kepco": kepco}
        a.stfmr_fmr_running, a.stfmr_frequency_running = True, False
        order = []
        a.stop_stfmr_fmr_scan = lambda button=None: order.append(("stop", button))
        kepco.force_output_off.side_effect = lambda: order.append("off") or {}
        a._apply_stfmr_kepco_readback = lambda result: None
        a.stfmr_status_var = Var()
        a.zero_stfmr_kepco_output()
        self.assertEqual(order, [("stop", "KEPCO Zero / OFF"), "off"])


class PpmsTests(unittest.TestCase):
    pass

if __name__ == "__main__":
    unittest.main()
