"""Offline regression tests for shared VISA manager, LegacyMFLIBridge,
UI queue slicing, and PPMS safety/Zurich setup (T1.1 / A1-1).

Restored from main:Other-files/test/test_instrument_safety_logic.py for the
standalone Transport-PPMS application. No VISA, hardware, or Tk windows.
"""

import math
import types
import unittest
from unittest import mock

from test_ppms_motor_debug import app
import transport.ppms.controllers.visa as visa_ctrl


class FakeSharedVisaInstrument:
    def __init__(self, identity):
        self.identity = identity
        self.closed = False
        self.commands = []
        self.timeout = None
        self.write_termination = "PYVISA_DEFAULT_WRITE"
        self.read_termination = "PYVISA_DEFAULT_READ"
        self.send_end = False
        self.query_delay = 0.0

    def _require_open(self):
        if self.closed:
            raise RuntimeError("VI_ERROR_INV_OBJECT")

    def query(self, command):
        self._require_open()
        if command == "*IDN?":
            return self.identity
        return "0"

    def write(self, command):
        self._require_open()
        self.commands.append(command)

    def close(self):
        self.closed = True


class FakeSharedVisaManager:
    def __init__(self, identities):
        self.identities = dict(identities)
        self._session = 1
        self.resources = []
        self.close_calls = 0
        self.list_resources_calls = 0

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError("invalid ResourceManager session")
        return self._session

    def open_resource(self, resource):
        self.session
        instrument = FakeSharedVisaInstrument(self.identities[resource])
        self.resources.append(instrument)
        return instrument

    def list_resources(self):
        self.session
        self.list_resources_calls += 1
        return tuple(self.identities)

    def close(self):
        self.close_calls += 1
        for instrument in self.resources:
            instrument.close()
        self._session = None


class FakePyVisaModule:
    def __init__(self, manager):
        self.manager = manager

    def ResourceManager(self):
        return self.manager


class Fake6221:
    def __init__(self):
        self.commands = []
        self.current = 0.0
        self.compliance = 10.0
        self.auto_range = 0
        self.output = 0
        self.wave_frequency = 13.7
        self.wave_amplitude = 0.0
        self.wave_offset = 0.0
        self.wave_cycles = 1
        self.marker_enabled = 0
        self.marker_line = 4
        self.marker_phase = 0.0
        self.measurement_condition = 0
        self.auto_complete_waveform = False
        self.reported_wave_cycles = None

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith("CURR:COMP "):
            self.compliance = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:CURR:RANG:AUTO "):
            self.auto_range = int(upper.endswith("ON"))
        elif upper.startswith("SOUR:CURR:RANG "):
            self.current_range = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:CURR "):
            self.current = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:FREQ "):
            self.wave_frequency = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:AMPL "):
            self.wave_amplitude = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:OFFS "):
            self.wave_offset = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:DUR:CYCL "):
            value = command.rsplit(" ", 1)[1]
            self.wave_cycles = value if value.upper() == "INF" else int(value)
        elif upper.startswith("SOUR:WAVE:PMAR:OLIN "):
            self.marker_line = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:PMAR:STAT "):
            self.marker_enabled = int(upper.endswith("ON"))
        elif upper.startswith("SOUR:WAVE:PMAR "):
            self.marker_phase = float(command.rsplit(" ", 1)[1])
        elif upper == "SOUR:WAVE:INIT":
            self.output = 0 if self.auto_complete_waveform else 1
        elif upper == "SOUR:WAVE:ABOR":
            self.output = 0
        elif upper == "OUTP ON":
            self.output = 1
        elif upper == "OUTP OFF":
            self.output = 0

    def query(self, command):
        upper = command.upper()
        if upper == "SYST:ERR?":
            return '0,"No error"'
        if upper == "CURR:COMP?":
            return str(self.compliance)
        if upper == "SOUR:CURR:RANG:AUTO?":
            return str(self.auto_range)
        if upper == "SOUR:CURR:RANG?":
            return str(getattr(self, "current_range", 0.1))
        if upper == "SOUR:CURR?":
            return str(self.current)
        if upper == "SOUR:WAVE:FREQ?":
            return str(self.wave_frequency)
        if upper == "SOUR:WAVE:AMPL?":
            return str(self.wave_amplitude)
        if upper == "SOUR:WAVE:OFFS?":
            return str(self.wave_offset)
        if upper == "SOUR:WAVE:DUR:CYCL?":
            if self.reported_wave_cycles is not None:
                return str(self.reported_wave_cycles)
            return str(self.wave_cycles)
        if upper == "SOUR:WAVE:PMAR:STAT?":
            return str(self.marker_enabled)
        if upper == "SOUR:WAVE:PMAR:OLIN?":
            return str(self.marker_line)
        if upper == "SOUR:WAVE:PMAR?":
            return str(self.marker_phase)
        if upper == "OUTP?":
            return str(self.output)
        if upper == "STAT:MEAS:COND?":
            return str(self.measurement_condition)
        if upper == "OUTP:LTE?":
            return "0"
        raise AssertionError(f"unexpected query: {command}")


class FakeSR830:
    def __init__(self, r_value):
        self.commands = []
        self.harmonic = 1
        self.reference_mode = 0
        self.reference_slope = 1
        self.frequency = 13.7
        self.r_value = float(r_value)
        self.status = 0

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith("HARM "):
            self.harmonic = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("FMOD "):
            self.reference_mode = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("RSLP "):
            self.reference_slope = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("SENS "):
            self.sensitivity = int(command.rsplit(" ", 1)[1])

    def query(self, command):
        upper = command.upper()
        if upper == "*ESR?":
            return "0"
        if upper == "HARM?":
            return str(self.harmonic)
        if upper == "FMOD?":
            return str(self.reference_mode)
        if upper == "RSLP?":
            return str(self.reference_slope)
        if upper == "FREQ?":
            return str(self.frequency)
        if upper == "LIAS?":
            return str(self.status)
        if upper == "SNAP? 1,2,3,4,9":
            return f"1e-6,2e-6,{self.r_value},3.5,{self.frequency}"
        if upper == "SENS?":
            return str(getattr(self, "sensitivity", 24))
        if upper == "OFLT?":
            return str(getattr(self, "time_constant_code", 9))
        raise AssertionError(f"unexpected query: {command}")

    def clear(self):
        pass


class SharedVisaAndSafetyTests(unittest.TestCase):
    def setUp(self):
        visa_ctrl._INSTRUMENT_LEASES.clear()
        visa_ctrl._SHARED_VISA_MANAGER = None
        visa_ctrl._SHARED_VISA_MANAGER_USERS = 0

    def tearDown(self):
        visa_ctrl._INSTRUMENT_LEASES.clear()
        visa_ctrl._SHARED_VISA_MANAGER = None
        visa_ctrl._SHARED_VISA_MANAGER_USERS = 0

    def test_shared_visa_manager_survives_one_controller_disconnect(self):
        original_pyvisa = visa_ctrl.pyvisa
        manager = FakeSharedVisaManager({
            "GPIB0::12::INSTR": "KEITHLEY INSTRUMENTS INC.,MODEL 6221,TEST,1",
            "GPIB0::10::INSTR": "Stanford_Research_Systems,SR830,TEST,1",
        })
        visa_ctrl.pyvisa = FakePyVisaModule(manager)
        k6221 = visa_ctrl.SafeVisaIdentityController(
            lambda _message: None, "Keithley 6221", "6221", owner_scope="PPMS"
        )
        sr830 = visa_ctrl.SafeVisaIdentityController(
            lambda _message: None, "SR830", "SR830", owner_scope="PPMS"
        )
        try:
            k6221.connect("GPIB0::12::INSTR")
            sr830.connect("GPIB0::10::INSTR")
            self.assertIs(k6221.resource_manager, sr830.resource_manager)
            self.assertEqual(visa_ctrl._SHARED_VISA_MANAGER_USERS, 2)

            k6221.disconnect()
            self.assertEqual(manager.close_calls, 0)
            self.assertEqual(visa_ctrl._SHARED_VISA_MANAGER_USERS, 1)
            self.assertIn("SR830", sr830.instrument.query("*IDN?"))

            sr830.disconnect()
            self.assertEqual(manager.close_calls, 1)
            self.assertEqual(visa_ctrl._SHARED_VISA_MANAGER_USERS, 0)
            self.assertIsNone(visa_ctrl._SHARED_VISA_MANAGER)
        finally:
            for ctrl in (k6221, sr830):
                try:
                    ctrl.disconnect()
                except BaseException:
                    pass
            visa_ctrl.pyvisa = original_pyvisa

    def test_failed_visa_identity_check_does_not_close_other_session(self):
        original_pyvisa = visa_ctrl.pyvisa
        manager = FakeSharedVisaManager({
            "GPIB0::12::INSTR": "KEITHLEY INSTRUMENTS INC.,MODEL 6221,TEST,1",
            "GPIB0::16::INSTR": "UNEXPECTED,DEVICE,TEST,1",
        })
        visa_ctrl.pyvisa = FakePyVisaModule(manager)
        k6221 = visa_ctrl.SafeVisaIdentityController(
            lambda _message: None, "Keithley 6221", "6221", owner_scope="PPMS"
        )
        other = visa_ctrl.SafeVisaIdentityController(
            lambda _message: None, "SR830", "SR830", owner_scope="PPMS"
        )
        try:
            k6221.connect("GPIB0::12::INSTR")
            with self.assertRaises(RuntimeError):
                other.connect("GPIB0::16::INSTR")
            self.assertEqual(manager.close_calls, 0)
            self.assertEqual(visa_ctrl._SHARED_VISA_MANAGER_USERS, 1)
            self.assertIn("6221", k6221.instrument.query("*IDN?"))
            k6221.disconnect()
            self.assertEqual(manager.close_calls, 1)
        finally:
            for ctrl in (k6221, other):
                try:
                    ctrl.disconnect()
                except BaseException:
                    pass
            visa_ctrl.pyvisa = original_pyvisa

    def test_legacy_labone_bridge_source_still_compiles(self):
        from transport.ppms.controllers.zurich import _MFLI_BRIDGE_SOURCE
        compile(_MFLI_BRIDGE_SOURCE, "<mfli_bridge>", "exec")
        self.assertIn(
            'requested_imp50 = int(request.get("input_imp50", 0))',
            _MFLI_BRIDGE_SOURCE,
        )
        self.assertIn(
            "daq.setInt(imp50_node, requested_imp50)",
            _MFLI_BRIDGE_SOURCE,
        )
        self.assertIn(
            "daq.setDouble(scaling_node, requested_scaling)",
            _MFLI_BRIDGE_SOURCE,
        )
        self.assertIn('"actual_scaling"', _MFLI_BRIDGE_SOURCE)

    def test_the_zurich_is_set_up_as_on_ppms(self):
        from transport.ppms.controllers.zurich import ZurichMFLIController
        from transport.ppms.engine.runner import PPMSScanRunner
        from transport.ppms.state import PPMSAppState

        for has_md, expected in ((True, 3), (False, None)):
            with self.subTest(mf_md=has_md):
                controller = ZurichMFLIController(lambda _message: None)
                controller.mfli_daq = object()
                controller.mfli_has_multi_demod = lambda: has_md
                controller.configure_mfli = mock.Mock(return_value={"sample_rate_hz": 10.0})
                controller.configure_second_harmonic = mock.Mock()
                controller.configure_third_harmonic = mock.Mock()
                controller.disable_unused_ppms_demods = mock.Mock()

                state = PPMSAppState.__new__(PPMSAppState)
                state.zurich_controller = controller
                state.zurich_scan_enabled = True
                state.zurich_scan_configured = False
                state.zurich_scan_harmonic = "1"
                state.zurich_scan_reference = "Trigger In 1"
                state.zurich_scan_tau = 0.1
                runner = PPMSScanRunner(state, post_ui=lambda cb: cb())
                runner._configure_zurich_for_scan()

                kwargs = controller.configure_mfli.call_args.kwargs
                self.assertEqual(kwargs["external_reference_demodulator"], expected)
                self.assertEqual((kwargs["input_impedance"], kwargs["input_scaling"]), ("10 MΩ", 1.0))
                controller.disable_unused_ppms_demods.assert_called_once_with("1")

    def test_safe_close_hardware_logs_and_does_not_raise_on_failure(self):
        from transport.ppms.app_shell import AppShell

        harness = object.__new__(AppShell)
        warnings = []

        def fail_ppms():
            raise RuntimeError("simulated ppms shutdown failure")

        harness.ppms_panel = mock.Mock()
        harness.ppms_panel.safe_close_ppms = fail_ppms
        harness.append_log = warnings.append
        AppShell.safe_close_hardware(harness)
        self.assertTrue(any("simulated ppms shutdown failure" in text for text in warnings))

    def test_ui_queue_drain_is_time_sliced(self):
        import queue
        from transport.ppms.app_shell import AppShell
        from transport.ppms.ui.theme import UI_QUEUE_ACTIVE_DELAY_MS, UI_QUEUE_MAX_CALLBACKS_PER_TICK

        class FakeRoot:
            def __init__(self):
                self.after_calls = []

            @staticmethod
            def winfo_exists():
                return True

            def after(self, delay_ms, callback):
                self.after_calls.append((delay_ms, callback))

        harness = object.__new__(AppShell)
        harness.root = FakeRoot()
        harness.ui_queue = queue.Queue()
        harness.closing = False
        completed = []
        callback_count = UI_QUEUE_MAX_CALLBACKS_PER_TICK + 5
        for index in range(callback_count):
            harness.ui_queue.put(lambda value=index: completed.append(value))

        AppShell.drain_ui_queue(harness)

        self.assertGreater(len(completed), 0)
        self.assertLessEqual(len(completed), UI_QUEUE_MAX_CALLBACKS_PER_TICK)
        self.assertFalse(harness.ui_queue.empty())
        self.assertEqual(harness.root.after_calls[-1][0], UI_QUEUE_ACTIVE_DELAY_MS)

    def test_ppms1_transport_allows_one_sr830_at_address_10(self):
        import threading
        from transport.ppms.controllers.visa import SafeVisaIdentityController
        from transport.ppms.engine.planning import validate_ppms_transport_scan_settings
        from transport.ppms.engine.runner import PPMSScanRunner, ppms_sr830_address
        from transport.ppms.state import PPMSAppState

        settings = validate_ppms_transport_scan_settings(
            0.001, 13.7, 50.0, 1, 20000, ("830_a",)
        )
        self.assertTrue(math.isclose(settings["peak_a"], math.sqrt(2.0) * 1e-6))
        self.assertEqual(settings["active_lockins"], ("830_a",))
        with self.assertRaises(ValueError):
            validate_ppms_transport_scan_settings(80.0, 13.7, 50.0, 1, 1)
        with self.assertRaises(ValueError):
            validate_ppms_transport_scan_settings(0.001, 13.7, 50.0, 1, 1, ())
        self.assertEqual(ppms_sr830_address("GPIB0::10::INSTR"), 10)
        with self.assertRaises(ValueError):
            ppms_sr830_address("GPIB0::11::INSTR")

        state = PPMSAppState.__new__(PPMSAppState)
        source = SafeVisaIdentityController(
            lambda _message: None,
            "Keithley 6221",
            "6221",
            ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"),
        )
        lockin_a = SafeVisaIdentityController(
            lambda _message: None, "SR830 槽 A", "SR830"
        )
        lockin_b = SafeVisaIdentityController(
            lambda _message: None, "SR830 槽 B", "SR830"
        )
        source.instrument = Fake6221()
        lockin_a.instrument = FakeSR830(10e-6)
        lockin_a.resource_name = "GPIB0::10::INSTR"
        state.ppms_instrument_controllers = {
            "6221": source,
            "830_a": lockin_a,
            "830_b": lockin_b,
        }
        state.zurich_controller = mock.Mock()
        state.zurich_scan_enabled = False
        state.zurich_scan_configured = False
        state.scan_stop_event = threading.Event()
        state.sr830_range_states = {}
        state.sr830_time_constant_codes = {}
        state.last_current_trip = False
        state.logger = lambda _m: None
        runner = PPMSScanRunner(state, post_ui=lambda cb: cb())
        runner.configure_transport_scan(settings)
        self.assertEqual(source.instrument.output, 1)
        self.assertEqual(lockin_a.instrument.harmonic, 1)
        self.assertIsNone(lockin_b.instrument)
        sample = harness._read_ppms_transport_sample(13.7, ("830_a",))
        self.assertTrue(math.isclose(sample["sr830_10_r_v"], 10e-6))
        self.assertTrue(sample["sr830_10_reference_valid"])
        self.assertNotIn("sr830_8_r_v", sample)
        self.assertNotIn("sr830_9_r_v", sample)
        harness._stop_ppms_transport_source()
        self.assertEqual(source.instrument.output, 0)


if __name__ == "__main__":
    unittest.main()
