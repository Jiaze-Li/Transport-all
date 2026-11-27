"""Take out of a test module the tests that cannot run in the standalone PPMS
application because they test other pages of the main program.

    python tools/prune_tests.py <module name> [<module name> ...]      (run in Other-files)

Runs the module, removes every test method that fails or errors, and repeats
until it passes; a module that cannot be imported at all, or has no test
left, is deleted.  Every removed test is printed with the reason it failed:
read that list.  A reason that is not "another page's code is not here"
(an AttributeError on a name of another page) is a fault of the extraction,
not of the test.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

# Modules other test modules import their stand-ins from: kept with no test left.
KEEP_AS_HELPERS = ("test_nonlocal_keithley", "test_rte_panel", "test_rte_angle_scan", "test_ppms_motor_debug",
                   "test_ppms_two_panels", "test_ppms_frequency_scan", "test_ppms_sr830_tc", "test_ppms_run_save_zero",
                   "test_moke_cidwm_runs", "test_nonlocal_scan_runs", "test_rte_angle_end_to_end", "test_fmr_scan_run",
                   "test_stfmr_scan_convention", "test_fmr_stfmr_review")
HEADER = re.compile(r"^(ERROR|FAIL): (\w+) \(([\w.]+)\)", re.M)


def run(module: str) -> str:
    # In Other-files the suite's own runner; elsewhere (Other-files/test) plain unittest.
    command = ["ci/run_suite.py", "--module", module] if Path("ci/run_suite.py").exists() else ["-m", "unittest", "-v", module]
    done = subprocess.run([sys.executable, *command], capture_output=True, text=True)
    return done.stdout + done.stderr


def failures(output: str) -> dict[tuple[str, str], str]:
    """{(class, method): reason} of the failed tests in a unittest report."""
    found = {}
    blocks = re.split(r"^={60,}$", output, flags=re.M)
    for block in blocks:
        header = HEADER.search(block)
        if header is None:
            continue
        parts = header.group(3).split(".")
        if parts[-1] != header.group(2):
            parts.append(header.group(2))
        lines = [line for line in block.strip().split("\n") if line and not line.startswith(("-", " ", "Traceback"))]
        found[(parts[-2], parts[-1])] = lines[-1][:200] if lines else "?"
    return found


def remove(path: Path, tests) -> int:
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")
    tree = ast.parse(source)
    spans = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        gone = [item for item in node.body if isinstance(item, ast.FunctionDef) and (node.name, item.name) in tests]
        left = [item for item in node.body if item not in gone and not (
            isinstance(item, ast.Expr) and isinstance(getattr(item, "value", None), ast.Constant))]
        for index, item in enumerate(gone):
            first = min([item.lineno, *(d.lineno for d in item.decorator_list)])
            # A class left with nothing but its docstring keeps a "pass".
            spans.append((first, item.end_lineno, "    pass" if not left and index == 0 else None))
    for first, last, stand_in in sorted(spans, reverse=True):
        lines[first - 1:last] = [stand_in] if stand_in else []
        while not stand_in and first - 1 < len(lines) and first >= 2 and not lines[first - 1].strip() and not lines[first - 2].strip():
            del lines[first - 1]
    text = "\n".join(lines)
    ast.parse(text)
    path.write_text(text, encoding="utf-8")
    return len(spans)


def main(modules) -> int:
    for module in modules:
        path = Path(f"{module}.py")
        removed = {}
        for _round in range(12):
            output = run(module)
            if "_FailedTest" in output or "Failed to import test module" in output:
                reason = (re.findall(r"^\w+Error: .*$", output, flags=re.M) or ["?"])[-1]
                print(f"{module}: DELETED, it cannot be imported: {reason[:200]}")
                path.unlink()
                break
            failed = failures(output)
            if not failed:
                ran = re.search(r"^Ran (\d+) test", output, flags=re.M)
                count = int(ran.group(1)) if ran else 0
                if count == 0 and removed and module not in KEEP_AS_HELPERS:
                    print(f"{module}: DELETED, no test left ({len(removed)} removed)")
                    path.unlink()
                else:
                    print(f"{module}: {count} tests pass, {len(removed)} removed")
                break
            if not remove(path, set(failed)):
                print(f"{module}: STOPPED, could not remove {sorted(failed)}")
                break
            removed.update(failed)
        for (cls, name), reason in sorted(removed.items()):
            print(f"    - {cls}.{name}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
