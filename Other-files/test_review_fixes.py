"""Fixes from the whole-program review (2026-10-09), one check each.

No Tk and no instrument: stand-ins record what the code asks of them."""
import threading
import unittest
from unittest import mock

from transport.core.scan_sequences import ppms_scan_axis
from transport.ppms.controllers.sr830 import SR830Controller
from transport.ppms.controllers.visa import instrument_lease_key
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMSPanelView
from transport.ppms.ui.theme import format_nice_tick


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


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
        self.assertEqual(format_nice_tick(3002.5, 2.5), "3002.5")
        self.assertEqual(format_nice_tick(10005.0, 2.5), "10005.0")
        self.assertEqual(format_nice_tick(3000.0, 5.0), "3000")

    def test_a_scan_axis_does_not_repeat_its_end(self):
        self.assertEqual(ppms_scan_axis(0.1, 0.4, 0.1)[-2:], (0.30000000000000004, 0.4))
        self.assertEqual(len(ppms_scan_axis(0.1, 0.4, 0.1)), 4)

    def test_the_short_gpib_spelling_shares_the_lease(self):
        self.assertEqual(instrument_lease_key("GPIB0::30"), instrument_lease_key("GPIB0::30::INSTR"))


class StfmrTests(unittest.TestCase):
    def test_the_overload_latched_by_a_range_change_is_dropped(self):
        controller = SR830Controller.__new__(SR830Controller)
        controller.lock = threading.RLock()
        controller.auto_range_enabled = True
        controller.sensitivity_index = 17
        controller.low_range_count = controller.range_cooldown = 0
        snap = "0.9e-3,0,0.95e-3,0,17.3"
        controller.instrument = Recorder({
            "SNAP? 1,2,3,4,9": [snap, "1e-4,0,1e-4,0,17.3"],
            "LIAS?": ["0", "4", "0"],
            "SENS?": "18",
        })
        controller.read_sample(17.3)
        sample = controller.read_sample(17.3)
        self.assertEqual(controller.sensitivity_index, 18, "no further step")
        self.assertEqual(sample["status"], 0, "the latched bit was read and dropped")


class PpmsTests(unittest.TestCase):
    def test_a_typed_temperature_rate_is_kept(self):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.root = None
        panel.state = PPMSAppState.__new__(PPMSAppState)
        panel.state.last_readback = None
        panel.ppms_temperature_target_var, panel.ppms_temperature_rate_var = Var("300"), Var("2")
        try:
            panel.set_ppms_temperature()
        except Exception:
            pass
        self.assertEqual(panel.ppms_temperature_rate_var.get(), "2")
        panel.ppms_temperature_target_var.set("5")
        try:
            panel.set_ppms_temperature()
        except Exception:
            pass
        self.assertEqual(panel.ppms_temperature_rate_var.get(), "1")


if __name__ == "__main__":
    unittest.main()
