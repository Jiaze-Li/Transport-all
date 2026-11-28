import tkinter as tk
import unittest
from unittest.mock import Mock

from transport.ppms.app_shell import maximize_main_window


class StartupWindowTests(unittest.TestCase):
    def test_native_maximize(self):
        root = Mock()
        maximize_main_window(root)
        root.state.assert_called_once_with('zoomed')
        root.attributes.assert_not_called()

    def test_x11_fallback(self):
        root = Mock()
        root.state.side_effect = tk.TclError('unsupported')
        maximize_main_window(root)
        root.attributes.assert_called_once_with('-zoomed', True)

    def test_unsupported_manager_does_not_prevent_startup(self):
        root = Mock()
        root.state.side_effect = tk.TclError('unsupported')
        root.attributes.side_effect = tk.TclError('unsupported')
        maximize_main_window(root)
        root.geometry.assert_not_called()
