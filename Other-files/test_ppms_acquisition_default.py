"""PPMS page, "Acquire s" (user, 2026-10-16): 5 s for a Temperature scan,
1 s for the other modes.  A scan-mode change puts the new mode's default in
the box unless the operator typed another value there.  No Tk window."""
import inspect
import unittest

from transport.ppms.engine.runner import PPMS_ACQUISITION_S, PPMS_TEMPERATURE_ACQUISITION_S
import transport.ppms.ui.panel as panel_mod
from transport.ppms.ui.panel import PPMSPanelView


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def page(value="1"):
    panel = PPMSPanelView.__new__(PPMSPanelView)
    panel.ppms_acquisition_time_var = Var(value)
    panel.ppms_acquisition_default_shown = f"{PPMS_ACQUISITION_S:g}"
    return panel


class AcquisitionDefaultTests(unittest.TestCase):
    def test_the_defaults(self):
        self.assertEqual(PPMS_TEMPERATURE_ACQUISITION_S, 5.0)
        self.assertEqual(PPMS_ACQUISITION_S, 1.0)
        panel = page()
        for mode in ("Field", "Angle", "Current"):
            with self.subTest(mode=mode):
                panel._apply_ppms_mode_acquisition_default(mode)
                self.assertEqual(panel.ppms_acquisition_time_var.get(), "1")

    def test_switching_modes_follows_the_defaults(self):
        panel = page()
        panel._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "5")
        panel._apply_ppms_mode_acquisition_default("Field")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "1")
        panel._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "5")

    def test_a_typed_value_is_kept(self):
        panel = page()
        panel._apply_ppms_mode_acquisition_default("Temperature")
        panel.ppms_acquisition_time_var.set("3")
        panel._apply_ppms_mode_acquisition_default("Field")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "3")
        panel._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "3")
        typed = page("2")
        typed.ppms_acquisition_default_shown = "1"
        typed._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(typed.ppms_acquisition_time_var.get(), "2")

    def test_the_mode_change_calls_it(self):
        text = inspect.getsource(panel_mod)
        self.assertIn("self._apply_ppms_mode_acquisition_default(self.ppms_simple_mode.get())", text)
        self.assertIn("self.ppms_acquisition_time_var = tk.StringVar(value=f\"{PPMS_ACQUISITION_S:g}\")", text)


if __name__ == "__main__":
    unittest.main()
