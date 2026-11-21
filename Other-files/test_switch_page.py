"""The Switch page (user, 2026-11-21: "add a Switch module containing 6221,
830, magnet like SHHV has LS643, Kepco4886, LS475, the motor. 6221 pulse
write function. to get a switching results like this fig", R_H against the
pulse current at several fields).

The real SwitchPanel (SHHV's rack again) on test_rte_angle_end_to_end's
harness: stand-in widgets, a real one-thread worker, a KEPCO and an LS475
whose field follows its current, and here a 6221 that records every command
of the write pulse.  No VISA session, no window."""
from contextlib import ExitStack
import threading
import types
import unittest
from unittest.mock import Mock, patch

import test_rte_angle_end_to_end as ete
import test_rte_panel as rte_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var
from test_rte_angle_scan import Recorder

setUpModule, tearDownModule = ete.setUpModule, ete.tearDownModule


def build_switch_page(kepco=True):
    """The real SwitchPanel constructor on stand-in widgets, as
    test_rte_angle_end_to_end.build_page builds SHHV's."""
    parent = Mock(closing=False)
    parent.root.after.return_value = "timer"
    Recorder.made = []
    with ExitStack() as stack:
        for name in ("Frame", "LabelFrame", "Label", "Entry", "Button", "Combobox", "Checkbutton", "Progressbar"):
            stack.enter_context(patch.object(app.ttk, name, Recorder))
        stack.enter_context(patch.object(app.tk, "Canvas", Recorder))
        for name in ("StringVar", "IntVar", "BooleanVar"):
            stack.enter_context(patch.object(app.tk, name, Var))
        stack.enter_context(patch.object(app, "ThreadPoolExecutor", ImmediateExecutor))
        stack.enter_context(patch.object(app.CombinedControlApp, "_create_connection_indicator",
                                         lambda _self, parent, key: Recorder(parent, indicator=key)))
        stack.enter_context(patch.object(app.CombinedControlApp, "_set_connection_indicator", Mock()))
        stack.enter_context(patch.object(app.CombinedControlApp, "_draw_nonlocal_series", Mock()))
        panel = app.SwitchPanel(parent, rte_tests.Widget())
        made = list(Recorder.made)
    panel.test_widgets = made
    return ete.kepco_chosen(panel) if kepco else panel


class Fake6221:
    """The 6221 as the write pulse uses it: every write kept, the waveform
    settings read back as written, a finite train ending by itself."""

    SETTINGS = ("SOUR:WAVE:AMPL", "SOUR:WAVE:OFFS", "SOUR:WAVE:FREQ", "CURR:COMP", "SOUR:WAVE:DUR:CYCL")

    def __init__(self):
        self.sent, self.values, self.output, self.event, self.trains = [], {}, 0, 0, []
        self.sine, self.sines = False, []
        self.readback = {}          # a setting's wrong read-back, for a test
        self.compliance_after_train = False

    def write(self, command):
        self.sent.append(command)
        name, _, value = command.partition(" ")
        if name in self.SETTINGS:
            self.values[name] = float(value)
        elif command in ("OUTP ON", "OUTP OFF"):
            self.output = int(command == "OUTP ON")
        elif command == "SOUR:WAVE:FUNC SQU":
            self.sine = False
        elif command == "SOUR:WAVE:FUNC SIN":
            self.sine = True
        elif command == "SOUR:WAVE:INIT":
            if self.sine:
                self.output = 1         # the continuous read current
                self.sines.append(dict(self.values))
                return
            self.trains.append(dict(self.values))
            self.output = 0
            if self.compliance_after_train:
                self.event = 8

    def query(self, command):
        if command == "SYST:ERR?":
            return '0,"No error"'
        if command == "STAT:MEAS:EVEN?":
            value, self.event = self.event, 0
            return str(value)
        if command == "OUTP?":
            return str(self.output)
        name = command.rstrip("?")
        return repr(self.readback.get(name, self.values[name]))


class SwitchScan(ete.EndToEnd):
    def make_switch(self, fields="100, -50", span=("-2", "2", "2"), width="30", pulses="1"):
        """The Switch page ready for its scan: SR830-1 alone at the first
        harmonic, the KEPCO and an LS475 that reads 100 G per A of it, the
        fake 6221, and the field setting's waits made tiny."""
        with patch.object(ete, "build_page", build_switch_page):
            h = self.make(meter="sr830")
        panel = h.panel
        self.assertIsInstance(panel, app.SwitchPanel)
        panel.ppms_transport_harmonic_a_var.set("1")
        h.harmonic["830_a"] = 1
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set("Switch")
            panel._rte_mode_changed()
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel.rte_outer_ranges["Switch"][0].set(fields)
        panel.rte_pulse_width_us.set(width)
        panel.rte_pulse_count.set(pulses)
        h.gauss = ete.FollowingGauss(h.log, h.kepco)
        panel.rte_controllers["gauss"] = h.gauss
        for name, value in (("MAGNET_FIELD_SET_SETTLE_S", 0.0), ("MAGNET_FIELD_SET_READ_INTERVAL_S", 1e-4),
                            ("MAGNET_FIELD_SET_GROUP_GAP_S", 1e-4), ("SWITCH_PULSE_DONE_MARGIN_S", 0.0)):
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(app.time, "sleep", lambda _seconds: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        h.fake = Fake6221()
        source = panel.ppms_instrument_controllers["6221"]
        source.lock, source.instrument = threading.RLock(), h.fake

        def query(key, command, count):
            if key == "6221":
                if command == "OUTP?":
                    h.log.append(("6221", "OUTP?", threading.get_ident()))
                return (float(h.fake.query(command)),)
            if command == "FREQ?":      # the SR830's reference, locked to the read current
                return (float(panel.ppms_transport_frequency_var.get()),)
            if command == "LIAS?":
                return (0.0,)
            assert command == "HARM?", command
            return (h.harmonic[key],)
        panel._ppms_query_numbers = query
        h.configured = []

        def configure(settings):
            h.configured.append(dict(settings))
            h.fake.sent.append("FULL SET-UP")
            h.fake.output = 1
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))
        panel._configure_ppms_transport_scan = configure

        def off():
            # The page's output off (SOUR:WAVE:ABOR, CURR 0, OUTP OFF), on the fake.
            h.log.append(("6221", "off", threading.get_ident()))
            for command in ("SOUR:WAVE:ABOR", "OUTP OFF"):
                h.fake.write(command)
        panel._stop_ppms_transport_source = off
        h.log.clear()
        return h


class SwitchPageTests(unittest.TestCase):
    def test_the_page_is_shhvs_rack_with_the_switch_scan(self):
        panel = build_switch_page(kepco=False)
        self.assertEqual((panel.ppms_page_name, panel.rte_scan_modes, panel.rte_default_scan_mode),
                         ("Switch", ("Switch",), "Switch"))
        # The start-up scan: the pulse loop -20 to +20 mA in 1 mA and back,
        # the figure's fields, 30 us x 1, read with 0.5 mA RMS on SR830-1
        # at the first harmonic.
        self.assertEqual(panel.ppms_simple_mode.get(), "Switch")
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["-20", "20", "1"])
        self.assertIs(panel.rte_round_trip.get(), True)
        self.assertEqual(panel.rte_outer_ranges["Switch"][0].get(), "40, 20, 0, -20")
        self.assertEqual((panel.rte_pulse_width_us.get(), panel.rte_pulse_count.get()), ("30", "1"))
        self.assertEqual((panel.ppms_transport_current_var.get(), panel.ppms_transport_frequency_var.get()),
                         ("0.5", "317.3"))
        self.assertEqual(panel.ppms_transport_harmonic_a_var.get(), "1")
        self.assertEqual({key: panel.ppms_lockin_selection[key].get() for key in ("830_a", "830_b", "830_c", "zurich")},
                         {"830_a": True, "830_b": False, "830_c": False, "zurich": False})
        self.assertEqual((panel.rte_scan_units.get(), panel.rte_scan_note.get(), panel.rte_outer_label.get()),
                         ("Pulse mA", "Switch: 6221 pulse mA; outer: field G", "Outer field G:"))
        self.assertEqual(panel.ppms_plot_axis, "pulse")
        # The rack is SHHV's: the same instruments, its own controllers.
        self.assertEqual(set(panel.rte_controllers), {"kepco", "gauss", "newmark", "lakeshore"})
        self.assertEqual({c.owner_scope for c in panel.rte_controllers.values()}, {"Switch"})
        # The page's texts name it.
        texts = {str(w.options.get("text", "")) for w in panel.test_widgets}
        self.assertTrue({"Switch settings", "Switch scan", "6221 write", "Pulse µs", "Pulses"} <= texts, texts)
        self.assertFalse([text for text in texts if "SHHV" in text])
        # The list box shows this mode's fields.
        self.assertIs(panel.rte_outer_list_entry.options["textvariable"], panel.rte_outer_ranges["Switch"][0])

    def rows_shown(self, panel):
        """{SR830 label: (its connection row shown, its harmonic row shown)}."""
        shown = {}
        for key in app.PPMS_SR830_KEYS:
            label = app.PPMS_SR830_LABELS[key]
            group = panel.ppms_device_resource_widgets[key].parent
            harmonic = [w for w in panel.test_widgets if w.options.get("text") == f"{label} harmonic"]
            self.assertEqual(len(harmonic), 1, label)
            shown[label] = (not group.grid_removed, not harmonic[0].grid_removed)
        return shown

    def test_only_sr830_1_has_rows_on_the_switch_page(self):
        """User, 2026-11-21: "hide the SR830-2, SR830-3 rows in Switch": their
        connection rows and harmonic rows are off screen; SR830-1's and the
        Zurich's stay, and neither hidden lock-in is measured."""
        panel = build_switch_page(kepco=False)
        self.assertEqual(panel.rte_shown_sr830_keys, ("830_a",))
        self.assertEqual(self.rows_shown(panel), {"SR830-1": (True, True), "SR830-2": (False, False),
                                                  "SR830-3": (False, False)})
        self.assertEqual({key: panel.ppms_lockin_selection[key].get() for key in ("830_b", "830_c")},
                         {"830_b": False, "830_c": False})
        # Every widget of a hidden harmonic row, not only its label.
        hidden = [w for w in panel.test_widgets if w.grid_removed and w.grid_options.get("column") in (13, 14, 17, 18)
                  and w.grid_options.get("row") in (1, 2)]
        self.assertEqual(len(hidden), 8, "label, harmonic, Measure and range of two rows")
        # The Zurich's harmonic row follows SR830-1's with no empty line
        # between (user, 2026-11-21: "close the gap"): its four widgets on
        # the second line of the block.
        def zurich_rows(page):
            return sorted((w.grid_options["column"], w.grid_options["row"]) for w in page.test_widgets
                          if w.parent is page.ppms_settings_panel and not w.grid_removed
                          and w.grid_options.get("column") in (13, 14, 17, 18) and w.grid_options.get("row") != 0)
        self.assertEqual(zurich_rows(panel), [(13, 1), (14, 1), (17, 1), (18, 1)])
        self.assertEqual(panel.zurich_harmonic_combo.grid_options["row"], 1)
        # Two columns of connections (user, 2026-11-21): the supply's line,
        # the LS475's reading and the field setting are the box's children,
        # shown in the rack until the window has the width (GitHub's window
        # tests check the two layouts).
        self.assertIs(panel.rte_two_column_connections, True)
        rack = panel.rte_connection_widgets["newmark"][0].parent
        field_set = panel.rte_set_field_button.parent
        self.assertIs(field_set.parent, rack.parent)
        self.assertIs(field_set.grid_options["in_"], rack)
        self.assertEqual((field_set.grid_options["row"], field_set.grid_options["column"]), (2, 6))
        # SHHV keeps all three, the Zurich's on the fourth line.
        shhv = ete.build_page(kepco=False)
        shhv.test_widgets = list(Recorder.made)
        self.assertEqual(shhv.zurich_harmonic_combo.grid_options["row"], 3)
        self.assertEqual(shhv.zurich_select.grid_options["row"], 3)
        self.assertIs(shhv.rte_two_column_connections, False)
        shhv_rack = shhv.rte_connection_widgets["newmark"][0].parent
        self.assertIs(shhv.rte_set_field_button.parent.parent, shhv_rack, "SHHV's field setting is the rack's own")
        self.assertNotIn("in_", shhv.rte_set_field_button.parent.grid_options)
        self.assertEqual(shhv.rte_shown_sr830_keys, app.PPMS_SR830_KEYS)
        self.assertEqual(set(self.rows_shown(shhv).values()), {(True, True)})

    def test_the_shhv_page_is_as_before(self):
        panel = ete.build_page(kepco=False)
        self.assertEqual((panel.ppms_page_name, panel.rte_scan_modes), ("SHHV", app.RTE_SCAN_MODES))
        self.assertNotIn("Switch", app.RTE_SCAN_MODES)
        self.assertFalse(hasattr(panel, "rte_pulse_width_us"), "no pulse row on SHHV")
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle+Field2")
        self.assertIs(panel.rte_outer_list_entry.options["textvariable"], panel.rte_outer_ranges["Angle+Field2"][0])

    def test_the_plan_is_the_pulse_loop(self):
        plan = app.rte_scan_plan("Switch", "-2", "2", "2", 1, True)
        self.assertEqual([value for _loop, _point, value in plan], [-2.0, 0.0, 2.0, 0.0, -2.0])
        with self.assertRaisesRegex(ValueError, r"6221 pulse currents must stay within \+/-105 mA; 106 mA is outside"):
            app.rte_scan_plan("Switch", "0", "106", "106", 1, False)

    def test_the_stfmr_stage_counts_the_switch_pages_supplies(self):
        a = object.__new__(app.CombinedControlApp)
        a.stfmr_lockin_controllers = {"kepco": Mock(connected=True, output_owned=False),
                                      "lakeshore": Mock(connected=True, output_on=False, short_label="LS643")}
        a.stfmr_lockin_magnet_choice = "LS643"
        a.switch_panel = types.SimpleNamespace(rte_controllers={"lakeshore": Mock(output_on=True), "kepco": None})
        with self.assertRaisesRegex(RuntimeError, "Switch page's LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()
        a.switch_panel = types.SimpleNamespace(rte_controllers={"kepco": Mock(output_owned=True)})
        with self.assertRaisesRegex(RuntimeError, "KEPCO 4886 output of the Switch page is on"):
            a._stfmr_lockin_require_4886_off()
        a.switch_panel = types.SimpleNamespace(rte_controllers={"kepco": Mock(output_owned=False),
                                                                "lakeshore": Mock(output_on=False)})
        a._stfmr_lockin_require_4886_off()


class SwitchScanTests(SwitchScan):
    def test_each_point_is_written_with_a_pulse_then_read(self):
        h = self.make_switch()
        h.draw.reset_mock()         # the harness drew SHHV's Angle axes while it set the page up
        self.start(h)
        panel = h.panel
        h.showerror.assert_not_called()
        title, question = h.askyesno.call_args.args[:2]
        self.assertEqual(title, "Switch scan")
        self.assertIn("Field, the OUTER loop: 2 fields in the order typed: +100, -50 G, each within ±2 G", question)
        self.assertIn("6221 pulse current, the INNER loop: from -2 mA to +2 mA and back (step 2 mA), 5 pulses a "
                      "loop, 1 loop(s). Largest pulse 2 mA.", question)
        self.assertIn("one write of 1 pulse(s) of 30 µs at the point's current", question)
        self.assertIn("then the read current on again, 0.5 mA RMS at 317.3 Hz", question)
        self.assertIn("2 fields x 5 pulses x 1 harmonic pass(es) = 10 points.", question)
        rows = self.rows(h)
        self.assertEqual([(r["outer_target_field_g"], r["outer_field_index"], float(r["pulse_current_ma"]))
                          for r in rows],
                         [("100.0", "1", v) for v in (-2.0, 0.0, 2.0, 0.0, -2.0)]
                         + [("-50.0", "2", v) for v in (-2.0, 0.0, 2.0, 0.0, -2.0)])
        self.assertEqual({(r["module"], r["scan_mode"]) for r in rows}, {("Switch", "Switch")})
        self.assertEqual([r["pulse_in_compliance"] for r in rows[:5]], ["False", "no pulse", "False", "no pulse", "False"])
        self.assertEqual({(float(r["pulse_width_s"]), r["pulse_count"]) for r in rows}, {(3e-5, "1")})
        for r in rows:
            self.assertAlmostEqual(float(r["measured_field_g"]), float(r["outer_target_field_g"]), delta=2.0)
            self.assertAlmostEqual(float(r["sr830_8_x_over_i_ohm"]), float(r["sr830_8_x_v"]) / 0.5e-3)
            self.assertEqual(r["6221_current_rms_ma"], "0.5")
            # Each reading follows the read current's restart: 5 x the
            # slowest lock-in's time constant, then Settle s (0 here).
            self.assertGreater(float(r["slowest_lockin_tc_s"]), 0)
            self.assertAlmostEqual(float(r["effective_settle_s"]), 5.0 * float(r["slowest_lockin_tc_s"]))
        self.assertEqual(app.SWITCH_READ_SETTLE_TAUS, 5.0)
        self.assertIn("(5 x ", question)
        self.assertIn("then Settle s 0 s) of settling", question)
        # Six trains (0 mA sends none), each the CIDWM pulse: a 50 % square
        # of period 2 x 30 us, amplitude |I|/2, offset I/2, one cycle.
        self.assertEqual(len(h.fake.trains), 6)
        frequency = 1.0 / (2.0 * 30e-6)
        for train, current_ma in zip(h.fake.trains, (-2.0, 2.0, -2.0) * 2):
            self.assertAlmostEqual(train["SOUR:WAVE:AMPL"], abs(current_ma) * 1e-3 / 2.0)
            self.assertAlmostEqual(train["SOUR:WAVE:OFFS"], current_ma * 1e-3 / 2.0)
            self.assertAlmostEqual(train["SOUR:WAVE:FREQ"], frequency, delta=frequency * 1e-9)
            self.assertEqual((train["SOUR:WAVE:DUR:CYCL"], train["CURR:COMP"]),
                             (1.0, float(h.configured[0]["compliance_v"])))
        # The scan's one full set-up (the 6221's reset) comes before any
        # pulse; after each pulse the read current comes back without it.
        sent = h.fake.sent
        self.assertEqual(sent.count("FULL SET-UP"), 1)
        self.assertEqual(sent[0], "FULL SET-UP")
        self.assertNotIn("*RST", sent)
        # One pulse's commands, in order, and nothing armed before every
        # setting is written.
        first = sent.index("SOUR:WAVE:ARM")
        self.assertEqual(sent[first - 1], "SOUR:WAVE:DUR:CYCL 1")
        self.assertEqual(sent[first:first + 3], ["SOUR:WAVE:ARM", "OUTP ON", "SOUR:WAVE:INIT"])
        self.assertEqual(sent[sent.index("SOUR:WAVE:FUNC SQU") - 2:sent.index("SOUR:WAVE:FUNC SQU") + 2],
                         ["SOUR:WAVE:ABOR", f"CURR:COMP {float(h.configured[0]['compliance_v']):.12g}",
                          "SOUR:WAVE:FUNC SQU", "SOUR:WAVE:DCYC 50"])
        # The read current after it: MS-fourState's sine, armed with the
        # output off, on at INIT; ten times, once before every reading.
        sine = sent.index("SOUR:WAVE:FUNC SIN")
        self.assertEqual(sent[sine - 2:sine + 14], [
            "SOUR:WAVE:ABOR", f"CURR:COMP {float(h.configured[0]['compliance_v']):.12g}", "SOUR:WAVE:FUNC SIN",
            "SOUR:WAVE:FREQ 317.3", f"SOUR:WAVE:AMPL {2 ** 0.5 * 0.5e-3:.12e}", "SOUR:WAVE:OFFS 0",
            "SOUR:WAVE:RANG BEST", "SOUR:WAVE:EXTR OFF", "SOUR:WAVE:DUR:CYCL INF",
            f"SOUR:WAVE:PMAR {app.NONLOCAL_MARKER_PHASE_DEG:g}", f"SOUR:WAVE:PMAR:OLIN {app.NONLOCAL_MARKER_LINE}",
            "SOUR:WAVE:PMAR:STAT ON", "OUTP OFF", "SOUR:WAVE:ABOR", "SOUR:WAVE:ARM", "SOUR:WAVE:INIT"])
        self.assertEqual(len(h.fake.sines), 10, "the read current on again before every reading")
        self.assertEqual(h.fake.output, 0, "off at the end")
        # A pulse is never sent with the read current on: each train's
        # commands start after an output read back off.
        for index, command in enumerate(sent):
            if command == "SOUR:WAVE:FUNC SQU":
                before = sent[:index]
                last_sine = max((i for i, c in enumerate(before) if c in ("SOUR:WAVE:INIT", "FULL SET-UP")), default=-1)
                self.assertIn("SOUR:WAVE:ABOR", before[last_sine + 1:])
        kinds = [entry[:2] for entry in h.log if entry[0] in ("6221", "lockins")]
        self.assertEqual(kinds[:3], [("6221", "on"), ("6221", "off"), ("6221", "OUTP?")])
        # Everything on the page's worker.
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; KEPCO and 6221 zeroed")
        events = ete.RunLogBase.events(h)
        prepared = events[0]
        self.assertEqual((prepared["mode"], prepared["plan"]["unit"], prepared["plan"]["points"]),
                         ("Switch", "mA (6221 pulse)", 5))
        self.assertEqual({k: prepared["pulse"][k] for k in ("width_s", "count")}, {"width_s": 3e-5, "count": 1})
        self.assertEqual(prepared["outer"]["values"], [100.0, -50.0])
        self.assertEqual(prepared["outer"]["order"], "outer first, in the order typed; the pulse loop inner")
        pulses = [e for e in events if e["event"] == "pulse"]
        self.assertEqual([(e["step_index"], e["pulse_current_ma"]) for e in pulses],
                         [(1, v) for v in (-2.0, 0.0, 2.0, 0.0, -2.0)] + [(2, v) for v in (-2.0, 0.0, 2.0, 0.0, -2.0)])
        self.assertEqual([e["target_field_g"] for e in events if e["event"] == "field_programmed"], [100.0, -50.0])
        self.assertEqual(events[-1]["event"], "run_finished")
        # The plot: X / I in ohm against the pulse current, a curve per field.
        x_label = {call.args[2] for call in h.draw.call_args_list}
        self.assertEqual(x_label, {"6221 pulse current (mA)"})
        self.assertIn("X / I (Ω)", {call.args[3] for call in h.draw.call_args_list})

    def test_a_setting_that_does_not_read_back_arms_nothing(self):
        h = self.make_switch(fields="100", span=("2", "2", "1"))
        h.fake.readback["SOUR:WAVE:AMPL"] = 5e-3
        self.start(h)
        self.assertIn("6221 pulse amplitude readback mismatch: requested 0.001, read 0.005",
                      h.showerror.call_args.args[1])
        self.assertNotIn("SOUR:WAVE:ARM", h.fake.sent)
        self.assertNotIn("OUTP ON", h.fake.sent)
        self.assertEqual(h.fake.trains, [])
        self.assertTrue(h.panel.ppms_field_scan_status_var.get().startswith("Failed: 6221 pulse amplitude"),
                        h.panel.ppms_field_scan_status_var.get())
        # The end still zeroes the 6221 and the KEPCO.
        self.assertEqual(self.names(h, "6221", "kepco")[-3:], ete.ZEROED)

    def test_compliance_during_a_pulse_is_saved_and_logged(self):
        h = self.make_switch(fields="100", span=("2", "2", "1"))
        h.fake.compliance_after_train = True
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual([r["pulse_in_compliance"] for r in self.rows(h)], ["True", "True", "True"][:1])
        self.assertTrue([line for line in h.logged if "Switch: the 6221 reached compliance" in line
                         and "+2 mA pulse" in line], h.logged)

    def test_start_refuses_a_pulse_beyond_the_limits_before_anything_is_sent(self):
        for span, width, pulses, message in (
                (("0", "200", "100"), "30", "1", "6221 pulse currents must stay within +/-105 mA"),
                (("-2", "2", "2"), "x", "1", "Pulse µs must be a number"),
                (("-2", "2", "2"), "30", "0", "6221 pulse count must be between 1 and"),
                (("-2", "2", "2"), "30", "1.5", "6221 pulse count must be an integer")):
            with self.subTest(message=message):
                h = self.make_switch(span=span, width=width, pulses=pulses)
                self.start(h)
                self.assertEqual(h.showerror.call_args.args[0], "Switch scan parameters")
                self.assertIn(message, h.showerror.call_args.args[1])
                h.askyesno.assert_not_called()
                self.assertEqual(h.fake.sent, [])
                self.assertEqual(h.log, [])

    def test_a_stop_during_the_loop_ends_it_zeroed(self):
        h = self.make_switch(fields="100", span=("-2", "2", "1"))
        panel = h.panel
        real = panel._rte_switch_pulse

        def pulse(pulse_ma, settings):
            if pulse_ma == 0:
                panel.ppms_field_scan_stop_event.set()
            return real(pulse_ma, settings)
        panel._rte_switch_pulse = pulse
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual([float(r["pulse_current_ma"]) for r in self.rows(h)], [-2.0, -1.0])
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Stopped; KEPCO and 6221 zeroed")
        self.assertEqual(self.names(h, "6221", "kepco")[-3:], ete.ZEROED)


if __name__ == "__main__":
    unittest.main()
