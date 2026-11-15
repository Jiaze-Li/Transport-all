"""An angle scan's plots span the scan's own angles (user, 2026-11-15: "when
scan angle, can the xlim auto set to 0-360?").  The x limits were rounded
out to the tick step, so an SHV scan from 0 to -360 deg was drawn on
-400..0, and they grew with the points.  Now the axis is the scan's range
from the first point on, with ticks at round angles.

The pages' side (the range kept at Start and passed to the plots) is in
test_rte_angle_scan, test_ppms_angle_field, test_ppms_simple_modes,
test_ppms_two_panels and test_ppms_nested.
"""
import unittest
from unittest.mock import Mock

from test_ppms_motor_debug import app

WIDTH, HEIGHT = 600, 300
LEFT, RIGHT, TOP, BOTTOM = 86, 24, 24, 52
PLOT_WIDTH, AXIS_Y = WIDTH - LEFT - RIGHT, HEIGHT - BOTTOM


class AngleTickTests(unittest.TestCase):
    def test_a_full_turn_has_a_tick_every_90_degrees(self):
        self.assertEqual(app.angle_axis_ticks(0, 360), (0.0, 360.0, [0.0, 90.0, 180.0, 270.0, 360.0], 90.0))
        # The SHV page's own scan, 0 to -360 deg, either way round.
        for low, high in ((-360, 0), (0, -360)):
            self.assertEqual(app.angle_axis_ticks(low, high),
                             (-360.0, 0.0, [-360.0, -270.0, -180.0, -90.0, 0.0], 90.0))

    def test_shorter_and_longer_scans_get_round_angles_too(self):
        for low, high, step, ticks in (
                (0, 180, 30, [0, 30, 60, 90, 120, 150, 180]),
                (0, 90, 15, [0, 15, 30, 45, 60, 75, 90]),
                (-45, 45, 15, [-45, -30, -15, 0, 15, 30, 45]),
                (0, 270, 45, [0, 45, 90, 135, 180, 225, 270]),
                (0, 10, 2, [0, 2, 4, 6, 8, 10]),
                (0, 5, 1, [0, 1, 2, 3, 4, 5]),
                # PPMS's -370..370: the limits are the scan's, the ticks round.
                (-370, 370, 180, [-360, -180, 0, 180, 360]),
                (-5, 355, 90, [0, 90, 180, 270])):
            with self.subTest(low=low, high=high):
                self.assertEqual(app.angle_axis_ticks(low, high),
                                 (float(low), float(high), [float(t) for t in ticks], float(step)))

    def test_a_few_degrees_keep_the_scans_limits(self):
        low, high, ticks, step = app.angle_axis_ticks(0, 2)
        self.assertEqual((low, high, step), (0.0, 2.0, 0.5))
        self.assertEqual(ticks, [0.0, 0.5, 1.0, 1.5, 2.0])
        # One angle only: the usual axis around it.
        low, high, _ticks, _step = app.angle_axis_ticks(30, 30)
        self.assertLess(low, 30)
        self.assertGreater(high, 30)


class AnglePlotTests(unittest.TestCase):
    def draw(self, points, **options):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel._draw_plot_grid = Mock()
        canvas = Mock(ppms_compact_plot=False, ppms_hide_x_labels=False, compact_legend_strip=False)
        canvas.winfo_width.return_value = WIDTH
        canvas.winfo_height.return_value = HEIGHT
        panel._draw_nonlocal_series(canvas, {"SR830-1 P1 L1": points}, "Sample angle (°)", "X voltage",
                                    "waiting", markers=True, show_legend=False, **options)
        ticks = [(call.args[0], None) for call in canvas.create_line.call_args_list
                 if len(call.args) == 4 and call.args[1] == AXIS_Y and call.args[3] == AXIS_Y + 4]
        labels = [call.kwargs["text"] for call in canvas.create_text.call_args_list
                  if len(call.args) == 2 and call.args[1] == AXIS_Y + 15]
        curve = next(call.args for call in canvas.create_line.call_args_list if call.kwargs.get("width") == 2)
        return [x for x, _ in ticks], labels, curve[0::2]

    def test_the_axis_is_the_scans_range_from_the_first_points_on(self):
        # Two points of a 0 to -360 deg scan: the whole turn is on the axis.
        ticks, labels, xs = self.draw([(0.0, 1e-3), (-5.0, 2e-3)], angle_x_range=(-360.0, 0.0))
        self.assertEqual(labels, ["-360", "-270", "-180", "-90", "0"])
        self.assertEqual(ticks, [LEFT + PLOT_WIDTH * f for f in (0, 0.25, 0.5, 0.75, 1)])
        self.assertAlmostEqual(xs[0], LEFT + PLOT_WIDTH)
        self.assertAlmostEqual(xs[1], LEFT + PLOT_WIDTH * 355 / 360)

    def test_a_finished_turn_is_not_rounded_out_to_400(self):
        points = [(-float(angle), angle * 1e-6) for angle in range(0, 361, 5)]
        _ticks, labels, xs = self.draw(points, angle_x_range=(-360.0, 0.0))
        self.assertEqual(labels, ["-360", "-270", "-180", "-90", "0"])
        self.assertAlmostEqual(min(xs), LEFT)
        self.assertAlmostEqual(max(xs), LEFT + PLOT_WIDTH)
        # As it was, and as every other axis still is.
        _ticks, labels, xs = self.draw(points)
        self.assertEqual(labels, ["-400", "-300", "-200", "-100", "0"])
        self.assertAlmostEqual(min(xs), LEFT + PLOT_WIDTH * 40 / 400)

    def test_a_point_outside_the_range_is_still_on_the_plot(self):
        # A read-back a little off its target (PPMS's measured angle).
        _ticks, labels, xs = self.draw([(-0.4, 1.0), (180.0, 2.0), (360.3, 3.0)], angle_x_range=(0.0, 360.0),
                                       auto_voltage_unit=False)
        self.assertEqual(labels, ["0", "90", "180", "270", "360"])
        self.assertAlmostEqual(min(xs), LEFT)
        self.assertAlmostEqual(max(xs), LEFT + PLOT_WIDTH)

    def test_a_single_angle_is_drawn_as_before(self):
        with_range = self.draw([(30.0, 1.0), (30.0, 2.0)], angle_x_range=(30.0, 30.0))
        self.assertEqual(with_range, self.draw([(30.0, 1.0), (30.0, 2.0)]))


if __name__ == "__main__":
    unittest.main()
