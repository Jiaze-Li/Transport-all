"""Instrument exception classes maintain their historical import names."""
import unittest
from test_ppms_motor_debug import app
from transport.instruments import errors


class HardwareErrorsTests(unittest.TestCase):
    def test_legacy_exception_names_are_exact_exports(self):
        for name in (
            "CurrentZurichAPIMissingError",
            "AvtechPulseError",
            "LakeShoreRampStopped",
            "LakeShoreRampError",
            "LakeShoreZeroError",
            "MagnetProtocolError",
            "LEDProtocolError",
            "NewmarkStageError",
            "NewmarkCommandNotAllowed",
            "NewmarkControllerError",
            "NewmarkRefused",
            "NewmarkDriverOff",
            "NewmarkMotionError",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(app, name), getattr(errors, name))

    def test_lakeshore_error_preserves_current_and_hierarchy(self):
        problem = app.LakeShoreZeroError("not zeroed", current_a=1.25)
        self.assertIsInstance(problem, app.LakeShoreRampError)
        self.assertEqual(problem.current_a, 1.25)
        self.assertEqual(str(problem), "not zeroed")

    def test_avtech_pulse_marks_possible_trigger(self):
        err = app.AvtechPulseError("trigger timeout", fired=True, amplitude_v=5, width_s=1e-8)
        self.assertTrue(err.fired)
        self.assertEqual(err.amplitude_v, 5)
        self.assertEqual(err.width_s, 1e-8)

    def test_newmark_error_hierarchy_and_details(self):
        self.assertTrue(issubclass(app.NewmarkDriverOff, app.NewmarkStageError))
        err = app.NewmarkMotionError("simulated stop", stopped=False, stop_ran=True)
        self.assertFalse(err.stopped)
        self.assertTrue(err.stop_ran)
        self.assertEqual(err.notes, [])


if __name__ == "__main__":
    unittest.main()
