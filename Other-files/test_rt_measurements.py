"""The five measurements of Transport_RT (user, 2026-11-27: "split SHHV and
Switch to two modes. also add modes named NLH, AHE, PHE. In the order: PHE,
AHE, NLH, SHHV, Switch pls. PHE-scan: angle+field, angle+current; AHE-scan:
field, field+angle, field+current; NLH-scan: current; SHHV-scan: angle+field,
angle+current; Switch-scan: switch, switch-loop, switch2. the others remain
the same. put scan on the left side and settings in the right side pls.").

They are tabs of one page, so the instruments stay connected when the
measurement changes: the page's tab is the chosen measurement's, and choosing
another tab moves the page there and offers that measurement's scans.  Nothing
else follows the measurement: the settings, harmonics and scans are as before.
The saved rows and the run's settings name the measurement.

These tests use test_rte_angle_end_to_end's harness (stand-in widgets, no
window); test_rt_measurement_tabs.py builds the real window for the tabs."""
import json
import unittest
from unittest.mock import patch

import test_rte_angle_end_to_end as ete
from test_ppms_motor_debug import app

setUpModule, tearDownModule = ete.setUpModule, ete.tearDownModule

MEASUREMENTS = {
    "PHE": ("Angle+Field", "Angle+Current"),
    "AHE": ("Field", "Field+Angle", "Field+Current"),
    "NLH": ("Current",),
    "SHHV": ("Angle+Field", "Angle+Current"),
    "Switch": ("Switch", "Switch-loop", "Switch2"),
}


class TheTable(unittest.TestCase):
    def test_the_measurements_their_order_and_their_scans_are_the_users(self):
        self.assertEqual(dict(app.RTE_MEASUREMENTS), MEASUREMENTS)
        self.assertEqual(tuple(app.RTE_MEASUREMENTS), ("PHE", "AHE", "NLH", "SHHV", "Switch"))

    def test_every_scan_is_one_the_page_has(self):
        for name, scans in app.RTE_MEASUREMENTS.items():
            for scan in scans:
                self.assertIn(scan, app.RTE_SCAN_MODES, name)

    def test_the_program_starts_on_shhv_where_the_start_up_settings_belong(self):
        self.assertEqual(app.RTE_DEFAULT_MEASUREMENT, "SHHV")
        self.assertIn(app.RTE_DEFAULT_SCAN_MODE, app.RTE_MEASUREMENTS["SHHV"])


class ThePage(unittest.TestCase):
    def page(self):
        # The stand-in plots cannot be drawn on: what a scan change would redraw is left out.
        guard = patch.object(app.CombinedControlApp, "_draw_nonlocal_series")
        guard.start()
        self.addCleanup(guard.stop)
        return ete.build_page(kepco=False)

    def test_it_starts_on_shhv_with_shhvs_scans_and_the_start_up_scan(self):
        panel = self.page()
        self.assertEqual(panel.rte_measurement, "SHHV")
        self.assertEqual(tuple(panel.rte_scan_mode_combo.options["values"]), MEASUREMENTS["SHHV"])
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle+Field")
        self.assertTrue(str(panel.ppms_plot_frame.options["text"]).startswith("SHHV: X, Y, phase vs"))

    def test_each_measurement_offers_its_scans_and_starts_on_its_first(self):
        panel = self.page()
        for name in ("Switch", "NLH", "AHE", "PHE", "SHHV"):
            self.assertTrue(panel.rte_set_measurement(name))
            self.assertEqual(panel.rte_measurement, name)
            self.assertEqual(tuple(panel.rte_scan_mode_combo.options["values"]), MEASUREMENTS[name])
            self.assertEqual(panel.ppms_simple_mode.get(), MEASUREMENTS[name][0])
            self.assertTrue(str(panel.ppms_plot_frame.options["text"]).startswith(f"{name}: X, Y, phase vs"))

    def test_a_new_scan_is_set_up_as_choosing_it_in_the_list_does(self):
        panel = self.page()
        panel.rte_set_measurement("AHE")
        self.assertEqual([v.get() for v in panel.ppms_simple_range], list(app.RTE_FIELD_DEFAULT_RANGE))
        self.assertTrue(panel.rte_round_trip.get())
        self.assertEqual(panel.rte_scan_units.get(), "LS643 A")
        panel.rte_set_measurement("NLH")
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["0.01", "1", "0.01"])
        self.assertEqual(panel.rte_scan_units.get(), "6221 mA RMS")
        panel.rte_set_measurement("Switch")
        self.assertEqual({w.grid_removed for w in panel.rte_pulse_widgets}, {False})

    def test_a_measurement_keeps_the_scan_it_was_left_on(self):
        panel = self.page()
        panel.rte_set_measurement("Switch")
        panel.ppms_simple_mode.set("Switch-loop")
        panel._rte_mode_changed()
        panel.rte_set_measurement("AHE")
        panel.ppms_simple_mode.set("Field+Current")
        panel._rte_mode_changed()
        panel.rte_set_measurement("Switch")
        self.assertEqual(panel.ppms_simple_mode.get(), "Switch-loop")
        panel.rte_set_measurement("AHE")
        self.assertEqual(panel.ppms_simple_mode.get(), "Field+Current")

    def test_the_same_scan_in_another_measurement_keeps_what_was_typed(self):
        """SHHV and PHE both offer Angle+Field: going from one to the other
        does not put the range back to its start-up values."""
        panel = self.page()
        for variable, value in zip(panel.ppms_simple_range, ("10", "-80", "2")):
            variable.set(value)
        self.assertTrue(panel.rte_set_measurement("PHE"))
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle+Field")
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["10", "-80", "2"])

    def test_a_running_scan_or_a_busy_page_keeps_the_measurement(self):
        for flag in ("ppms_field_scan_running", "ppms_environment_busy", "rte_stage_prompt_open", "rte_closed"):
            panel = self.page()
            setattr(panel, flag, True)
            self.assertFalse(panel.rte_set_measurement("Switch"), flag)
            self.assertEqual(panel.rte_measurement, "SHHV", flag)
            self.assertEqual(tuple(panel.rte_scan_mode_combo.options["values"]), MEASUREMENTS["SHHV"], flag)
            self.assertEqual(panel.ppms_simple_mode.get(), "Angle+Field", flag)
            # The measurement it is on is not a change.
            self.assertTrue(panel.rte_set_measurement("SHHV"), flag)

    def test_a_name_that_is_no_measurement_changes_nothing(self):
        panel = self.page()
        self.assertFalse(panel.rte_set_measurement("Log"))
        self.assertEqual(panel.rte_measurement, "SHHV")

    def test_nothing_else_follows_the_measurement(self):
        """ "the others remain the same": the read current, the harmonics and
        which lock-ins are measured are not the measurement's to change."""
        panel = self.page()

        def settings():
            return (panel.ppms_transport_current_var.get(), panel.ppms_transport_frequency_var.get(),
                    panel.ppms_transport_compliance_var.get(), panel.ppms_transport_harmonic_a_var.get(),
                    panel.ppms_transport_harmonic_b_var.get(), panel.ppms_transport_harmonic_c_var.get(),
                    panel.zurich_harmonic.get(), {key: v.get() for key, v in panel.ppms_lockin_selection.items()},
                    panel.ppms_page_name, {c.owner_scope for c in panel.rte_controllers.values()})
        before = settings()
        for name in ("PHE", "NLH", "SHHV"):
            panel.rte_set_measurement(name)
            self.assertEqual(settings(), before, name)

    def test_the_scan_box_is_left_of_the_settings_box(self):
        panel = self.page()
        boxes = {str(w.options.get("text")): w for w in ete.Recorder.made if w.options.get("text") in ("Scan", "Settings")}
        self.assertEqual(boxes["Scan"].grid_options["column"], 0)
        self.assertEqual(boxes["Settings"].grid_options["column"], 1)
        self.assertIs(boxes["Scan"].parent, boxes["Settings"].parent)
        self.assertIs(panel.rte_start_button.parent.parent, boxes["Scan"])
        self.assertTrue(app.RTE_SCAN_BOX_FIRST)


class SavedWithTheScan(ete.EndToEnd):
    def test_the_rows_and_the_run_settings_name_the_measurement(self):
        h = self.make()
        self.assertEqual(h.panel.rte_measurement, "SHHV")
        self.start(h)
        h.showerror.assert_not_called()
        rows = self.rows(h)
        self.assertTrue(rows)
        self.assertEqual({row["measurement"] for row in rows}, {"SHHV"})
        self.assertEqual(list(rows[0])[:3], ["module", "measurement", "scan_mode"])
        self.assertEqual({row["module"] for row in rows}, {"SHHV"})
        log = next(h.panel.rte_run_folder.glob("*.log"), None) or next(h.panel.rte_run_folder.glob("*.jsonl"))
        prepared = next(json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                        if '"run_prepared"' in line)
        self.assertIn('"measurement": "SHHV"', json.dumps(prepared))


if __name__ == "__main__":
    unittest.main()
