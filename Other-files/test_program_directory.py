"""Default save folders in a built Transport_all.exe (Windows .exe build,
user, 2026-11-10).  In a one-file build (PyInstaller or Nuitka) __file__ is
inside a temporary folder that is deleted when the program exits, so the
default results folders, and the folder dialogs' fallback start folder, are
beside the .exe there; for the .py they stay beside the .py, as before."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from transport.ppms.ui import theme

ROOT = Path(__file__).resolve().parents[1]


class ProgramDirectoryTests(unittest.TestCase):
    def test_the_py_program_saves_beside_itself(self):
        self.assertEqual(theme.program_directory(), ROOT)

    def test_a_built_exe_saves_beside_the_exe(self):
        exe = Path(tempfile.gettempdir()) / "Lab PC" / "Transport_all.exe"
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "argv", [str(exe)]):
            self.assertEqual(theme.program_directory(), exe.resolve().parent)

if __name__ == "__main__":
    unittest.main()
