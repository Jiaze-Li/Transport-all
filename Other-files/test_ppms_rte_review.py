"""PPMS review fixes (2026-10-13), one check each.

No Tk and no instrument: stand-ins record what the code asks of them."""
import threading
import tkinter as tk
import types
import unittest
from unittest.mock import Mock, patch

import transport.ppms.ui.panel as panel_mod
from transport.ppms.app_shell import AppShell
from transport.ppms.engine.planning import parse_ppms_macro
from transport.ppms.engine.macro import MacroRunner
from transport.ppms.engine.runner import ppms_field_is_ready
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMSPanelView
from test_ppms_two_panels import ImmediateExecutor, Var


class PPMSExitKeepsFieldOutsideFieldScans(unittest.TestCase):
    def stand_in(self, mode):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = PPMSAppState()
        panel.shell = types.SimpleNamespace(
            closing=False, root=None, append_log=Mock(), thread_log=Mock(), post_ui=lambda cb: cb()
        )
        panel.state.closed = False
        panel.state.scan_running = True
        panel.state.scan_stop_event = threading.Event()
        panel.ppms_poll_after_id = None
        panel.state.ppms_environment_controller = Mock(connected=True)
        panel.ppms_field_rate_var = Var('100')
        panel.ppms_field_approach_var = Var('No overshoot')
        panel.state.ppms_instrument_controllers = {}
        panel.state.ppms_executor = Mock()
        panel.state.zurich_closed = False
        panel.zurich_after_id = None
        panel.state.zurich_executor = Mock()
        panel.state.zurich_controller = Mock()
        if mode is not None:
            panel.state.active_scan_mode = mode
        return panel

    def test_only_field_scans_zero_the_field_on_exit(self):
        for mode in ('Temperature', 'Angle', 'Current', 'Angle+Temperature',
                     'Current+Temperature', 'Field', 'Field+Temperature'):
            with self.subTest(mode=mode):
                panel = self.stand_in(mode)
                panel.safe_close_ppms()
                remote = panel.state.ppms_environment_controller
                if mode in ('Field', 'Field+Temperature'):
                    remote.set_field.assert_called_once_with(0.0, 100.0, 1, 1)
                else:
                    remote.set_field.assert_not_called()
                remote.disconnect.assert_called_once()
                self.assertTrue(panel.state.scan_stop_event.is_set())

    def test_ppms_page_close_path(self):
        for mode in ('Temperature', None, 'Field'):
            with self.subTest(mode=mode):
                panel = self.stand_in(mode)
                panel.safe_close_ppms()
                remote = panel.state.ppms_environment_controller
                if mode == 'Field':
                    remote.set_field.assert_called_once_with(0.0, 100.0, 1, 1)
                else:
                    remote.set_field.assert_not_called()
                remote.disconnect.assert_called_once()


class WarmUpFromBaseTemperature(unittest.TestCase):
    def set_t(self, target, rate, readback_k):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = PPMSAppState()
        panel.shell = types.SimpleNamespace(root=None, closing=False)
        panel.root = None
        panel.ppms_temperature_target_var = Var(target)
        panel.ppms_temperature_rate_var = Var(rate)
        panel.state.last_readback = None if readback_k is None else {'temperature_k': readback_k}
        panel.ppms_temperature_approach_var = Var('Fast settle')
        panel.state.ppms_environment_controller = Mock()
        panel._run_ppms_command = Mock()
        with patch.object(panel_mod.messagebox, 'showerror') as showerror:
            panel.set_ppms_temperature()
        showerror.assert_not_called()
        panel._run_ppms_command.assert_called_once()
        panel._run_ppms_command.call_args.args[1]()   # the queued PPMS operation
        return panel

    def test_set_t_from_5_k_lowers_the_filled_in_rate(self):
        panel = self.set_t('300', '10', 5.0)
        panel.state.ppms_environment_controller.set_temperature.assert_called_once_with(300.0, 1.0, 0)
        self.assertEqual(panel.ppms_temperature_rate_var.get(), '1')

    def test_set_t_above_9_k_or_a_slower_rate_is_kept(self):
        for rate, readback, sent in (('10', 20.0, 10.0), ('10', None, 10.0), ('0.5', 5.0, 0.5)):
            with self.subTest(rate=rate, readback=readback):
                panel = self.set_t('300', rate, readback)
                panel.state.ppms_environment_controller.set_temperature.assert_called_once_with(300.0, sent, 0)
                self.assertEqual(panel.ppms_temperature_rate_var.get(), rate)

    def macro_runner(self, live_k):
        env = Mock()
        env.read_environment.return_value = {'temperature_k': live_k}
        sink = Mock()
        runner = MacroRunner(
            environment_controller=env,
            executor=ImmediateExecutor(),
            macro_stop_event=threading.Event(),
            is_closed=lambda: False,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=Mock(),
            post_ui=lambda cb: cb(),
        )
        runner._sink = sink
        return runner, env, sink

    def test_macro_temp_without_rate_picks_it_from_the_live_temperature(self):
        commands = parse_ppms_macro('TEMP 5\nWAI WAITFOR 60 1 0 0 0 0\nSCAN\nTEMP 300')
        self.assertEqual(commands[-1], (4, 'TEMP', (300.0, None, 0)))
        for target, live, rate in ((300., 5., 1.), (300., 9., 1.), (300., 20., 10.),
                                   (5., 300., 1.), (300., float('nan'), 1.)):
            with self.subTest(target=target, live=live):
                runner, env, sink = self.macro_runner(live)
                runner.execute_operation('TEMP', (target, None, 0))
                env.set_temperature.assert_called_once_with(target, rate, 0)
                # The run log names the rate the line used.
                self.assertIn(f'without a rate: {rate:g} K/min', sink.log.call_args.args[0])

    def test_macro_explicit_rates(self):
        # An explicit rate is kept; after a TEMP at or below 9 K with no SCAN
        # in between, a fast warm-up is refused at Validate, before anything
        # is sent.
        self.assertEqual(parse_ppms_macro('TMP TEMP 10 10 0')[0], (1, 'TEMP', (10, 10, 0)))
        for text in ('TEMP 5\nWAI WAITFOR 60 1 0 0 0 0\nTEMP 300 10',
                     'TEMP 5\nFIELD 0 100\nWAIT 10\nTEMP 300 10'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, r'Line \d: .*1 K/min'):
                parse_ppms_macro(text)
        self.assertEqual(parse_ppms_macro('TEMP 5\nWAI WAITFOR 60 1 0 0 0 0\nTEMP 300 1')[-1],
                         (3, 'TEMP', (300., 1., 0)))
        # A SCAN in between may be a Temperature scan that warmed the sample
        # (e.g. 5 -> 300 K), so Validate no longer assumes 5 K: the line
        # parses (as at c3271af) and the run checks the live temperature.
        commands = parse_ppms_macro('TMP TEMP 5 1 0\nWAI WAITFOR 60 1 0 0 0 0\nSCAN\nTMP TEMP 250 10 0')
        self.assertEqual(commands[-1], (4, 'TEMP', (250., 10., 0)))
        # WAITFOR still waits for the last TEMP target.
        self.assertEqual(parse_ppms_macro('TEMP 5\nSCAN\nWAI WAITFOR 60 1 0 0 0 0')[-1],
                         (3, 'WAITFOR', (60., 5., None, None)))
        runner, env, _sink = self.macro_runner(300.)
        runner.execute_operation('TEMP', commands[-1][2])
        env.set_temperature.assert_called_once_with(250., 10., 0)
        # The same line after a SCAN that left the sample at base temperature
        # is refused at run time, with nothing sent.
        runner, env, sink = self.macro_runner(5.)
        with self.assertRaisesRegex(ValueError, '1 K/min'):
            runner.execute_operation('TEMP', commands[-1][2])
        env.set_temperature.assert_not_called()
        # Starting at base temperature with no TEMP before it: still refused
        # at run time, with nothing sent (fails safe, as before).
        runner, env, sink = self.macro_runner(5.)
        with self.assertRaisesRegex(ValueError, '1 K/min'):
            runner.execute_operation('TEMP', parse_ppms_macro('TEMP 300 10')[0][2])
        env.set_temperature.assert_not_called()
        sink.log.assert_not_called()


class RTESR830AddressesCanBeTyped(unittest.TestCase):
    """minor #20: SR830 addresses can be plain entries or comboboxes."""

    def state(self, widget_class, key, connected=False, busy=False):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = PPMSAppState()
        panel.shell = types.SimpleNamespace(_set_connection_indicator=Mock())
        panel.state.scan_running = busy
        panel.ppms_device_connect_buttons = {key: Mock()}
        panel.ppms_device_disconnect_buttons = {key: Mock()}
        widget = Mock(spec=widget_class)
        panel.ppms_device_resource_widgets = {key: widget}
        panel._set_ppms_device_ui(key, connected)
        widget.configure.assert_called_once()
        return widget.configure.call_args.kwargs['state']

    def test_entry_and_combobox_states(self):
        self.assertEqual(self.state(tk.Entry, '830_a', connected=False, busy=False), 'normal')
        self.assertEqual(self.state(tk.Entry, '830_a', connected=True, busy=False), 'disabled')


class TracedVar(Var):
    """A StringVar stand-in whose write trace runs, as Tk's does."""

    def __init__(self, value, on_write):
        super().__init__(value)
        self.on_write = on_write

    def set(self, value):
        super().set(value)
        self.on_write()


class MacroTempKeepsItsRate(unittest.TestCase):
    """minor #21 and the review's status line: the real MacroRunner and
    _ppms_adjust_low_temperature_rate with the real target trace."""

    def run_macro(self, text, box_rate='3', live_k=300.):
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = PPMSAppState()
        panel.shell = types.SimpleNamespace(
            closing=False, root=types.SimpleNamespace(after=lambda _ms, cb: cb()),
            post_ui=lambda cb: cb(), thread_log=Mock(),
        )
        panel.root = panel.shell.root
        panel.ppms_macro_status = Mock()
        panel.ppms_macro_editor = Mock()
        panel.thread_log = panel.shell.thread_log
        panel.ppms_temperature_rate_var = Var(box_rate)
        panel.ppms_temperature_target_var = TracedVar('300', panel._ppms_adjust_low_temperature_rate)
        panel.state.ppms_environment_controller = Mock()
        panel.state.ppms_environment_controller.read_environment.return_value = {'temperature_k': live_k}
        panel.state.ppms_executor = ImmediateExecutor()
        panel._set_ppms_environment_ui = Mock()
        panel.state.scan_running = False   # the SCAN stand-in starts nothing
        seen = []
        panel.start_ppms_field_scan = Mock(side_effect=lambda: (seen.append(
            (panel.ppms_temperature_target_var.get(), panel.ppms_temperature_rate_var.get())), True)[-1])
        panel.macro_runner = MacroRunner(
            environment_controller=panel.state.ppms_environment_controller,
            executor=panel.state.ppms_executor,
            macro_stop_event=panel.state.macro_stop_event,
            is_closed=lambda: panel.shell.closing,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=panel.stop_ppms_field_scan,
            post_ui=panel.shell.post_ui,
        )
        panel.macro_runner.start(
            parse_ppms_macro(text),
            panel,
            start_scan=lambda: panel.start_ppms_field_scan() is not False,
            is_scan_running=lambda: panel.state.scan_running,
            schedule_poll=lambda ms, cb: panel.root.after(ms, cb),
        )
        return panel, seen

    def test_temp_with_a_rate_then_scan(self):
        # The box held 3 K/min; the target trace made it 10, so the SCAN
        # re-sent 250 K at 10 K/min, overriding the Macro's 3.
        panel, seen = self.run_macro('TEMP 250 3\nSCAN')
        panel.state.ppms_environment_controller.set_temperature.assert_called_once_with(250., 3., 0)
        self.assertEqual(seen, [('250.0', '3')])
        statuses = [c.args[0] for c in panel.ppms_macro_status.set.call_args_list]
        self.assertEqual(statuses[0], 'Running line 1: TEMP 250.0 3.0 0')
        # Set T afterwards sends the same rate.
        panel.state.last_readback = {'temperature_k': 250.}
        panel.ppms_temperature_approach_var = Var('Fast settle')
        panel._run_ppms_command = Mock()
        panel.root = None
        with patch.object(panel_mod.messagebox, 'showerror') as showerror:
            panel.set_ppms_temperature()
        showerror.assert_not_called()
        panel._run_ppms_command.call_args.args[1]()
        panel.state.ppms_environment_controller.set_temperature.assert_called_with(250., 3., 0)

    def test_other_explicit_rates_reach_the_box(self):
        for text, live, box in (('TEMP 5 0.5', 6., '0.5'), ('TMP TEMP 20 7 1', 300., '7'),
                                ('TEMP 300 1\nSCAN', 5., '1')):
            with self.subTest(text=text):
                panel, _ = self.run_macro(text, box_rate='3', live_k=live)
                self.assertEqual(panel.ppms_temperature_rate_var.get(), box)

    def test_temp_without_a_rate_shows_auto(self):
        # The run picks 10 (live and target above 9 K); the box shows the
        # target trace's value, as for a typed target, and the status line
        # says "auto" instead of "None".
        panel, seen = self.run_macro('TEMP 300\nSCAN', box_rate='3', live_k=300.)
        panel.state.ppms_environment_controller.set_temperature.assert_called_once_with(300., 10., 0)
        statuses = [c.args[0] for c in panel.ppms_macro_status.set.call_args_list]
        self.assertEqual(statuses[0], 'Running line 1: TEMP 300.0 auto 0')
        self.assertEqual(seen, [('300.0', '10')])
        self.assertNotIn('None', ' '.join(statuses))

    def test_a_waitfor_with_a_wait_switched_off_shows_a_dash(self):
        # Review, 2026-10-13: "auto" read as if the field were waited for
        # automatically; that wait is skipped.
        commands = parse_ppms_macro('TEMP 300 1\nFIELD 100 50\nWAITFOR 10 1 0 0 0 0')
        panel = PPMSPanelView.__new__(PPMSPanelView)
        panel.state = PPMSAppState()
        panel.shell = types.SimpleNamespace(closing=False, root=None, post_ui=lambda cb: cb(), thread_log=Mock())
        panel.ppms_macro_status = Mock()
        panel.ppms_macro_editor = Mock()
        panel.thread_log = panel.shell.thread_log
        panel._set_ppms_environment_ui = Mock()
        panel.state.ppms_executor = Mock()        # the wait itself is not run here
        runner = MacroRunner(
            environment_controller=panel.state.ppms_environment_controller,
            executor=panel.state.ppms_executor,
            macro_stop_event=panel.state.macro_stop_event,
            is_closed=lambda: False,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=Mock(),
            post_ui=panel.shell.post_ui,
        )
        runner.start(
            commands[2:],
            panel,
            start_scan=Mock(return_value=False),
            is_scan_running=lambda: False,
            schedule_poll=Mock(),
        )
        status = panel.ppms_macro_status.set.call_args.args[0]
        self.assertTrue(status.startswith('Running line 3: WAITFOR 10.0 300.0 '), status)
        self.assertNotIn('auto', status)
        self.assertIn(' -', status)
        panel.state.ppms_executor.submit.assert_called_once_with(runner.execute_operation, 'WAITFOR', commands[2][2])


if __name__ == '__main__':
    unittest.main()
