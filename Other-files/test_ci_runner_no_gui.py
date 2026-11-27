"""Contract tests for Other-files/ci/run_suite.py --no-gui mode (T1.4 / T-1)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI_DIR = Path(__file__).resolve().parent / "ci"
if str(CI_DIR) not in sys.path:
    sys.path.insert(0, str(CI_DIR))

import run_suite  # noqa: E402


class NoGuiRunnerTests(unittest.TestCase):
    def test_default_discovery_keeps_gui_modules_and_no_gui_excludes_them(self):
        old_cwd = os.getcwd()
        os.chdir(Path(__file__).resolve().parent)
        try:
            full_modules, full_excluded = run_suite.discover_modules(no_gui=False)
            nogui_modules, nogui_excluded = run_suite.discover_modules(no_gui=True)
        finally:
            os.chdir(old_cwd)

        self.assertEqual(full_excluded, [])
        for gui_mod in run_suite.GUI_ONLY_MODULES:
            self.assertIn(gui_mod, full_modules)
            self.assertNotIn(gui_mod, nogui_modules)
        self.assertEqual(len(nogui_modules) + len(nogui_excluded), len(full_modules))

    def test_no_gui_guard_fails_fast_when_tk_is_instantiated(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_path = Path(tmp) / "test_accidental_tk.py"
            mod_path.write_text(
                "import tkinter as tk\n"
                "import unittest\n"
                "class AccidentalTk(unittest.TestCase):\n"
                "    def test_opens_window(self):\n"
                "        tk.Tk()\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(CI_DIR / "run_suite.py"), "--no-gui", "--module", "test_accidental_tk"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Tk window creation is forbidden in --no-gui mode", proc.stderr + proc.stdout)

    def test_no_gui_propagates_test_failure_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_path = Path(tmp) / "test_failing_case.py"
            mod_path.write_text(
                "import unittest\n"
                "class FailingCase(unittest.TestCase):\n"
                "    def test_fail(self):\n"
                "        self.fail('intentional failure')\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(CI_DIR / "run_suite.py"), "--no-gui"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("FAILED: test_failing_case", proc.stderr)


if __name__ == "__main__":
    unittest.main()
