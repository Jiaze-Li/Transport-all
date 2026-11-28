"""An instrument one Transport program holds is refused to another program
(user, 2026-11-27: "add the cross-program instrument lock").

Inside one program a page cannot connect an instrument another page holds
(the lease table).  Since the standalone page programs (Transport_PPMS,
Transport_RT, Transport_MOKE, Transport_FMR) two programs can be open at
once, each with its own table: both could connect the same 6221, and a
Connect or a close in one switched the output off under the other's scan.
The lease now also holds a named object of the operating system for the
address; a second program finds it and is refused, with the holder's name.
The system drops the object when the holder disconnects, closes or dies.

The other program here is a second Python process that loads the same
program file; no instrument, port or VISA session is opened.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import transport.ppms.controllers.visa as visa_ctrl

SOURCE = Path(visa_ctrl.__file__).resolve()
CHILD = r"""
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("lease_child", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
owner = object()
try:
    module.acquire_instrument_lease(sys.argv[2], owner, "Other program / 6221")
except RuntimeError as error:
    print("REFUSED " + str(error), flush=True)
    raise SystemExit(0)
print("HELD %d" % os.getpid(), flush=True)
line = sys.stdin.readline()
if line.strip() == "release":
    module.release_instrument_lease(sys.argv[2], owner)
    print("RELEASED", flush=True)
    sys.stdin.readline()
"""


def other_program(resource):
    """A second process that takes the lease of ``resource`` and waits."""
    env = dict(os.environ)
    roots = [str(SOURCE.parent), str(SOURCE.parents[2])]      # the one-file program, or a page program in the package
    env["PYTHONPATH"] = os.pathsep.join([*roots, env.get("PYTHONPATH", "")])
    # Its errors go to a file: a pipe nobody reads would stop a noisy import.
    errors = tempfile.TemporaryFile(mode="w+")
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(SOURCE), resource], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=errors, text=True, env=env)
    child.errors = errors
    return child


class LeaseAcrossProgramsTests(unittest.TestCase):
    def setUp(self):
        # An address no other test uses, a new one per test.
        self.resource = f"TEST{os.getpid()}_{abs(hash(self._testMethodName)) % 100000}::INSTR"
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate()
            child.errors.close()

    def start(self, resource=None):
        child = other_program(resource or self.resource)
        self.children.append(child)
        line = child.stdout.readline().strip()
        if not line:
            child.errors.seek(0)
            self.fail("the other program did not start: " + child.errors.read()[-2000:])
        return child, line

    def acquire_soon(self, owner, seconds=10.0):
        """The system frees the object a moment after the holder's end."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                return visa_ctrl.acquire_instrument_lease(self.resource, owner, "This program / 6221")
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def test_an_instrument_held_by_another_program_is_refused_with_its_name(self):
        child, line = self.start()
        self.assertTrue(line.startswith("HELD "), line)
        pid = int(line.split()[1])      # the program's own process (a venv's python.exe is only its launcher)
        owner = object()
        with self.assertRaises(RuntimeError) as caught:
            visa_ctrl.acquire_instrument_lease(self.resource, owner, "This program / 6221")
        message = str(caught.exception)
        self.assertIn(f"{self.resource} is in use by another Transport program on this PC", message)
        self.assertIn("Other program / 6221", message)
        self.assertIn(f"process {pid}", message)
        self.assertIn("if no such window is open, end that process in Task Manager", message)
        self.assertIn('then connect from "This program / 6221"', message)
        self.assertNotIn(visa_ctrl.instrument_lease_key(self.resource), visa_ctrl._INSTRUMENT_LEASES, "nothing taken here")
        # The other program disconnects: the instrument is free.
        child.stdin.write("release\n")
        child.stdin.flush()
        self.assertEqual(child.stdout.readline().strip(), "RELEASED")
        self.acquire_soon(owner)
        visa_ctrl.release_instrument_lease(self.resource, owner)

    def test_the_other_program_is_refused_what_this_one_holds(self):
        owner = object()
        visa_ctrl.acquire_instrument_lease(self.resource, owner, "This program / SR830")
        try:
            _child, line = self.start()
            self.assertTrue(line.startswith("REFUSED "), line)
            self.assertIn("in use by another Transport program on this PC", line)
            self.assertIn("This program / SR830", line)
            self.assertIn(f"process {os.getpid()}", line)
        finally:
            visa_ctrl.release_instrument_lease(self.resource, owner)
        # Released here: the other program can take it.
        _child, line = self.start()
        self.assertTrue(line.startswith("HELD "), line)

    def test_a_program_that_dies_leaves_nothing_held(self):
        child, line = self.start()
        self.assertTrue(line.startswith("HELD "), line)
        child.kill()
        child.communicate()
        owner = object()
        self.acquire_soon(owner)
        visa_ctrl.release_instrument_lease(self.resource, owner)

    def test_the_same_address_written_another_way_is_the_same_instrument(self):
        # A board number no real program uses, so nothing else on the PC holds it.
        self.resource = f"GPIB{os.getpid()}::5::INSTR"
        _child, line = self.start(f"gpib{os.getpid()}::05")
        self.assertTrue(line.startswith("HELD "), line)
        with self.assertRaisesRegex(RuntimeError, "in use by another Transport program"):
            visa_ctrl.acquire_instrument_lease(self.resource, object(), "This program / 6221")

    def test_inside_one_program_the_page_message_is_unchanged(self):
        first, second = object(), object()
        visa_ctrl.acquire_instrument_lease(self.resource, first, "PPMS / 6221")
        try:
            with self.assertRaises(RuntimeError) as caught:
                visa_ctrl.acquire_instrument_lease(self.resource, second, "CIDWM / 6221")
            self.assertEqual(str(caught.exception),
                             f'{self.resource} is already connected by "PPMS / 6221"; please disconnect it in that '
                             'module first, then connect from "CIDWM / 6221"')
            # Another owner's release does nothing; the holder's frees it for the next page.
            visa_ctrl.release_instrument_lease(self.resource, second)
            self.assertIn(visa_ctrl.instrument_lease_key(self.resource), visa_ctrl._INSTRUMENT_LEASES)
        finally:
            visa_ctrl.release_instrument_lease(self.resource, first)
        self.assertNotIn(visa_ctrl.instrument_lease_key(self.resource), visa_ctrl._process_instrument_leases())
        visa_ctrl.acquire_instrument_lease(self.resource, second, "CIDWM / 6221")
        visa_ctrl.release_instrument_lease(self.resource, second)

    def test_a_cleared_table_does_not_lock_the_program_out_of_its_own_instrument(self):
        # Tests clear the table directly; the object this process holds is its own.
        owner = object()
        visa_ctrl.acquire_instrument_lease(self.resource, owner, "PPMS / 6221")
        key = visa_ctrl.instrument_lease_key(self.resource)
        del visa_ctrl._INSTRUMENT_LEASES[key]
        visa_ctrl.acquire_instrument_lease(self.resource, owner, "PPMS / 6221")
        visa_ctrl.release_instrument_lease(self.resource, owner)
        visa_ctrl._process_instrument_leases().pop(key, None)

    def test_a_system_that_cannot_make_the_object_does_not_stop_the_connect(self):
        owner = object()
        with unittest.mock.patch.object(visa_ctrl, "hold_instrument_for_this_process", return_value=None):
            visa_ctrl.acquire_instrument_lease(self.resource, owner, "PPMS / 6221")
        self.assertIn(visa_ctrl.instrument_lease_key(self.resource), visa_ctrl._INSTRUMENT_LEASES)
        visa_ctrl.release_instrument_lease(self.resource, owner)
        self.assertNotIn(visa_ctrl.instrument_lease_key(self.resource), visa_ctrl._INSTRUMENT_LEASES)


if __name__ == "__main__":
    unittest.main()
