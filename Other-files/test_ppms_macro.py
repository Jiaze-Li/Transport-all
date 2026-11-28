"""Offline Macro parsing/execution and full-session He regressions."""
import threading
import unittest
from unittest.mock import Mock, patch

import transport.ppms.engine.macro as macro_mod
from transport.ppms.engine.errors import PPMSScanCancelled
from transport.ppms.engine.macro import MacroRunner
from transport.ppms.engine.planning import parse_ppms_macro
import transport.ppms.ui.panel as panel_mod
from transport.ppms.ui.panel import PPMSPanelView


class MacroTests(unittest.TestCase):
    def test_sequence_and_line_numbers(self):
        commands = parse_ppms_macro('REM example\nTMP TEMP 10 10 0\nFLD FIELD 0 100 0 0\nWAI WAITFOR 1 1 1 0 0 0\nSCAN')
        self.assertEqual(commands[0], (2, 'TEMP', (10, 10, 0)))
        self.assertEqual(commands[2], (4, 'WAITFOR', (1, 10, 0, 0)))
        self.assertEqual(commands[3], (5, 'SCAN', ()))

    def test_reject_entire_script_before_execution(self):
        for line in ('VSMMH 1 2', 'FIELD nan 100', 'TEMP 5 10', 'WAIT -1', 'WAI WAITFOR 1 0 0 1 0 0', 'SCAN 1', '__import__("os")'):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, 'Line 2'):
                parse_ppms_macro('WAIT 0\n' + line)

    def test_missing_wait_target(self):
        with self.assertRaisesRegex(ValueError, 'target'):
            parse_ppms_macro('WAI WAITFOR 1 1 0 0 0 0')

    def test_stop_prevents_device_command(self):
        stop = threading.Event()
        stop.set()
        env = Mock()
        runner = MacroRunner(
            environment_controller=env,
            executor=Mock(),
            macro_stop_event=stop,
            is_closed=lambda: False,
            field_ready_fn=lambda *_a, **_kw: True,
            stop_scan_fn=lambda: None,
            post_ui=lambda cb: cb(),
        )
        with self.assertRaises(PPMSScanCancelled):
            runner.execute_operation('FIELD', (0, 100, 0, 0))
        env.set_field.assert_not_called()

    def test_persistent_wait_checks_mode(self):
        from transport.ppms.engine.runner import ppms_field_is_ready
        env = Mock()
        env.read_environment.side_effect = [
            dict(field_oe=0, field_status='Holding'),
            dict(field_oe=0, field_status='StablePersistent'),
        ]
        runner = MacroRunner(
            environment_controller=env,
            executor=Mock(),
            macro_stop_event=Mock(is_set=lambda: False),
            is_closed=lambda: False,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=lambda: None,
            post_ui=lambda cb: cb(),
        )
        runner.execute_operation('WAITFOR', (0, None, 0, 0))
        self.assertEqual(env.read_environment.call_count, 2)

    def test_he_ten_second_gate(self):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.ppms_helium_history = []
        panel._append_ppms_helium_csv = Mock()
        panel.ppms_helium_change_var = Mock()
        for second in (0, 1, 9, 10, 11, 20, 4000):
            with patch.object(panel_mod.time, 'monotonic', return_value=second):
                panel._update_ppms_helium_change(dict(helium_percent=80, helium_fresh=True))
        self.assertEqual([t for t, v in panel.ppms_helium_history], [0, 10, 20, 4000])


if __name__ == '__main__':
    unittest.main()
