import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import transport.ppms.app_shell as app_shell
from transport.ppms.app_shell import AppShell


class LogPauseTests(unittest.TestCase):
    def test_pause_copy_save_resume(self):
        obj = AppShell.__new__(AppShell)
        obj.root = Mock()
        obj.log_text = Mock()
        obj.log_text.get.return_value = 'earlier\n'
        obj.log_pause_button = Mock()
        obj.log_display_paused = False
        obj.log_pending_lines = []
        obj.toggle_log_refresh()
        obj.append_log('LabOne diagnostic')
        obj.log_text.insert.assert_not_called()
        self.assertIn('LabOne diagnostic', obj.log_pending_lines[0])
        obj.copy_displayed_log()
        obj.root.clipboard_append.assert_called_once_with('earlier\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'log.txt'
            with patch.object(app_shell.filedialog, 'asksaveasfilename', return_value=str(path)):
                obj.save_communication_log()
            self.assertIn('LabOne diagnostic', path.read_text(encoding='utf-8-sig'))
        pending = ''.join(obj.log_pending_lines)
        obj.toggle_log_refresh()
        obj.log_text.insert.assert_called_once_with('end', pending)
        self.assertEqual(obj.log_pending_lines, [])


if __name__ == '__main__':
    unittest.main()
