"""Run the Other-files suite with each test's name and a hang watchdog,
each test module in a fresh Python process.

If one test (its class set-up included) takes longer than HANG_S, every
thread's stack is written to stderr and that module stops, so a hang shows
where it is instead of running until the job is cancelled (2026-10-12).

One process per module (CI, 2026-10-14): Tk keeps some state per display
for the whole process.  After one module had built and destroyed its own
copy of the program, a later module's message box crashed Tk on the Linux
CI (segmentation fault in focus_force).  The program itself has one window
for its whole life, so it never meets a window of a destroyed copy; the
tests no longer do either.  A crash also prints every thread's Python stack.

No-GUI mode (`python3 ci/run_suite.py --no-gui`):
  Runs all offline non-GUI test modules without creating native Tk windows
  or requiring a display (useful on macOS so local runs do not pop up Aqua
  windows).  Only modules whose tests genuinely instantiate real `tk.Tk()` /
  `CombinedControlApp(root)` windows are excluded; new/unknown `test_*.py`
  modules are included by default and guarded by a fail-fast Tk/Toplevel
  hook so any accidental window creation fails immediately.
"""
import argparse
import faulthandler
import os
import subprocess
import sys
import unittest
from pathlib import Path

HANG_S = 300
# A backstop for a whole module's process; the per-test watchdog normally
# stops a hang long before this.
MODULE_S = 1800

# Modules excluded in --no-gui mode, keyed by module stem with the concrete
# behavior/fixture reason.  Full GUI/layout coverage runs in default mode
# (e.g. Linux Xvfb and Windows CI).
GUI_ONLY_MODULES = {
    "test_commercial_look": "Instantiates tk.Tk() and CombinedControlApp(root) to inspect live widget styling and layout",
    "test_folder_dialog": "Instantiates tk.Tk() and EditableFolderDialog(Toplevel) to test live modal folder dialog geometry",
    "test_plot_redraw_performance": "Instantiates tk.Tk() and CombinedControlApp(root) to benchmark live Tk plot canvas redraws",
    "test_scan_start_window": "Instantiates tk.Tk() and CombinedControlApp(root) to test live window startup and close dialogs",
    "test_zurich_choice": "Instantiates tk.Tk() and CombinedControlApp(root) to inspect live Zurich combobox width and connection",
}


def install_no_gui_guard() -> None:
    """Fail fast if any code in a --no-gui process tries to create a Tk window."""
    import tkinter as tk

    def _forbid_tk(*_args, **_kwargs):
        raise RuntimeError("Tk window creation is forbidden in --no-gui mode")

    tk.Tk.__init__ = _forbid_tk
    tk.Toplevel.__init__ = _forbid_tk


class WatchedResult(unittest.TextTestResult):
    def startTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(HANG_S, exit=True)
        super().startTest(test)


def run_module(name: str, *, no_gui: bool = False) -> int:
    """Run one test module in this process (the child)."""
    # Test modules import each other ("from test_ppms_motor_debug import
    # app"), as they could when the suite was discovered from this folder.
    sys.path.insert(0, os.getcwd())
    if no_gui:
        install_no_gui_guard()
    faulthandler.enable()
    faulthandler.dump_traceback_later(HANG_S, exit=True)
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    result = unittest.TextTestRunner(verbosity=2, resultclass=WatchedResult).run(suite)
    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


def discover_modules(no_gui: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """Return (included_modules, excluded_with_reasons)."""
    all_modules = sorted(path.stem for path in Path(".").glob("test_*.py"))
    if not no_gui:
        return all_modules, []
    included = [name for name in all_modules if name not in GUI_ONLY_MODULES]
    excluded = [(name, GUI_ONLY_MODULES[name]) for name in all_modules if name in GUI_ONLY_MODULES]
    return included, excluded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Other-files test suite.")
    parser.add_argument("--module", help="Run a single test module (internal child mode)")
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Run offline non-GUI modules only with fail-fast Tk window guards",
    )
    parser.add_argument(
        "--list-no-gui",
        action="store_true",
        help="Print included and excluded modules for --no-gui mode and exit",
    )
    args = parser.parse_args(argv)

    if args.module:
        return run_module(args.module, no_gui=args.no_gui)

    modules, excluded = discover_modules(no_gui=args.no_gui or args.list_no_gui)
    if args.list_no_gui:
        print(f"Included ({len(modules)}):")
        for name in modules:
            print(f"  + {name}")
        print(f"Excluded ({len(excluded)}):")
        for name, reason in excluded:
            print(f"  - {name}: {reason}")
        return 0

    if args.no_gui:
        print(
            f"--no-gui: running {len(modules)} offline modules, excluding {len(excluded)} GUI-only modules:",
            file=sys.stderr,
            flush=True,
        )
        for name, reason in excluded:
            print(f"  EXCLUDED {name}: {reason}", file=sys.stderr, flush=True)

    failed = []
    for name in modules:
        print(f"===== {name}", file=sys.stderr, flush=True)
        cmd = [sys.executable, os.path.abspath(__file__), "--module", name]
        if args.no_gui:
            cmd.append("--no-gui")
        try:
            code = subprocess.run(cmd, timeout=MODULE_S).returncode
        except subprocess.TimeoutExpired:
            code = f"no end after {MODULE_S} s"
        if code != 0:
            failed.append(f"{name} (exit {code})")
    print(
        f"\n{len(modules)} modules, {len(failed)} failed"
        + (f", {len(excluded)} GUI-only excluded" if args.no_gui else ""),
        file=sys.stderr,
    )
    for entry in failed:
        print(f"FAILED: {entry}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
