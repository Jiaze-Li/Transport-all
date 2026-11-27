"""SHHV page, Newmark driver and shared controllers: the audit of 2026-10-18.

H1: an unconfirmed stop is kept across a Disconnect and a Connect, on either
page, until a STOP sequence sees the stage stopped; a Disconnect with MST
unreadable after one sends STOP again and says CHECK THE STAGE.  H2: the
SHHV stop handlers set their event and queue their OFF before the run log is
written.  H3: an ABORT that stopped the stage is said so, and the KEPCO,
LS475 and Newmark lines are a running scan's run-log notes.  C1-1: the
LabOne C / .NET adapters, without setDouble / getDouble, are skipped.
C1-3: the PNA gets OUTP OFF only once identified.  C1-5: the .NET DLL search
goes past a bad candidate and never leaks its DLL-directory handle.  C2-7:
the camera is "still releasing" while its thread holds it.  The SHHV scans'
save and zero at every ending, and their dialogs out of drain_ui_queue.

No Tk window, no instrument, no real DLL: the stage is the program's
controller on test_rte_angle_scan's fake PerformaxCom.dll (the standalone
script's simulated NSC-A1, on a virtual clock), the pages are the stand-ins
of test_rte_panel, test_rte_angle_end_to_end and test_stfmr_motor_angle."""
import csv
import ctypes
import inspect
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_rte_angle_end_to_end as ete
import test_rte_angle_scan as rte_stage
import test_rte_panel as rte_tests
import test_stfmr_motor_angle as stfmr_motor
from test_ppms_motor_debug import app
from test_ppms_two_panels import Var
from test_rte_angle_scan import AfterRoot, script

_guards = []


def setUpModule():
    def refuse(what):
        def refused(*_args, **_kwargs):
            raise AssertionError(f"a test reached {what}")
        return refused
    for module in (app, stfmr_motor.app):
        for target, name, replacement in ((ctypes, "WinDLL", refuse("ctypes.WinDLL (a real DLL)")),
                                          (os, "add_dll_directory", refuse("os.add_dll_directory")),
                                          (module, "newmark_load_performax", refuse("the real Newmark DLL loader")),
                                          (module.tk, "Tk", refuse("tkinter.Tk (a window)"))):
            guard = patch.object(target, name, replacement, create=True)
            guard.start()
            _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


# ---- simulated stages ----------------------------------------------------------
class StopIgnored(rte_stage.Stuck):
    """A move that does not end by itself; STOP does nothing, ABORT stops it at once."""

    def _stop(self, command, now):
        if command == "ABORT":
            super()._stop(command, now)


class LosesMST(rte_stage.Runaway):
    """STOP and ABORT do nothing (Runaway) while ``stuck``; ``lose`` MST
    transactions from now on get no reply (-1: every one).  Not stuck, it is
    the plain simulated stage again."""

    lose = 0
    stuck = True

    def _finished(self, now):
        return super()._finished(now) if self.stuck else rte_stage.Sim._finished(self, now)

    def _stop(self, command, now):
        if not self.stuck:
            rte_stage.Sim._stop(self, command, now)

    def send_recv(self, handle, data):
        command = bytes(data).split(b"\0", 1)[0].decode("ascii")
        if command == "MST" and self.lose:
            self.lose -= 1 if self.lose > 0 else 0
            self.received.append("MST (lost)")
            return None
        return super().send_recv(handle, data)


def turning(sim):
    sim._start(3000000, script._now())


# ---- H1, H3: the driver ----------------------------------------------------------------
class UnconfirmedStopDriverTests(rte_stage.StageBase):
    def unconfirmed(self, sim, ownership=None):
        h = self.make(sim)
        if ownership is not None:
            h.stage.share_stop_record(ownership)
        h.stage.connect("auto")
        turning(h.sim)
        result = h.stage.stop()
        self.assertFalse(result["stopped"])
        self.assertTrue(h.stage.stop_finished, "the stop sequence ran to its end")
        return h

class AbortDriverTests(rte_stage.StageBase):
    pass

# ---- H3: the SHHV page's stage texts ------------------------------------------------------
class AbortPageTests(rte_stage.StageBase):
    pass

# ---- H1 on the SHHV page, end to end --------------------------------------------------------
class SHVReconnectTests(ete.EndToEnd):
    pass


# ---- H1 across the pages: ST-FMR -----------------------------------------------------------
class CrossPageTests(stfmr_motor.MotorBase):
    def test_an_unconfirmed_stop_on_shv_holds_on_stfmr_until_its_stop_stage(self):
        run = self.make(connect=False)
        h = run.h
        ownership = h.newmark_stage_ownership
        shv_sim = LosesMST()
        shv_dll = rte_stage.RecordingDLL(shv_sim)
        shv_log = []
        shv = stfmr_motor.app.NewmarkNSCA1Controller(shv_log.append, owner_scope="SHHV",
                                                     loader=lambda: (shv_dll, "fake PerformaxCom.dll"))
        shv._now, shv._sleep = run.clock.now, run.clock.sleep
        ownership.claim("SHHV", shv)
        shv.connect("auto")
        turning(shv_sim)
        self.assertFalse(shv.stop()["stopped"])
        with self.assertRaises(stfmr_motor.app.NewmarkStageError):
            shv.disconnect()
        ownership.release("SHHV")
        # The STFMR page's own Connect: the flag is the program's.
        h.connect_stfmr_lockin_motor()
        h.root.drain()
        self.assertTrue(run.motor.connected)
        self.assertTrue(run.motor.motion_may_run)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "CHECK STAGE")
        with self.assertRaisesRegex(RuntimeError, "last stop was not confirmed, so it may still be turning: press "
                                                  "Stop stage first"):
            h._stfmr_lockin_angle_plan()
        h.move_stfmr_lockin_stage()
        self.assertIn("press Stop stage first", self.error.call_args.args[1])
        self.assertEqual(run.sim.moves, [])
        # Its Stop stage sees the stage stopped: cleared for both pages.
        h.stop_stfmr_lockin_stage()
        h.root.drain()
        self.assertFalse(run.motor.motion_may_run)
        self.assertEqual(ownership.unconfirmed_stop, "")
        self.assertFalse(shv.motion_may_run)
        self.assertEqual(h._stfmr_lockin_angle_plan()["used"], True)


# ---- H2: the SHHV stop handlers, event and OFF first ------------------------------------------
class ProbeRecorder:
    """A running scan's recorder that notes, at each event, what the probe says."""

    closed = False

    def __init__(self, probe):
        self.probe, self.events = probe, []

    def event(self, name, **details):
        self.events.append((name, details.get("button"), self.probe()))
        return True

    def check(self):
        pass


class StopOrderTests(unittest.TestCase):
    def panel(self):
        panel = rte_tests.RTE.make_worker(self)
        panel.ppms_executor = Mock()
        return panel

# ---- H3: the controllers' lines are the run log's notes ----------------------------------------
class ControllerLogTests(ete.RunLogBase):
    pass

# ---- the SHHV scans' save and zero, and their dialogs out of drain_ui_queue --------------------------
class SHVSaveZeroTests(ete.RunLogBase):
    def outside_drain(self, h):
        """Every dialog: never inside drain_ui_queue (the README's 2026-10-14 rule)."""
        h.showerror.side_effect = lambda *_args, **_kwargs: self.assertFalse(h.in_drain, "opened inside "
                                                                                             "drain_ui_queue")

# ---- the red Set stage 0° (user, 2026-10-18), offline: its colours by state ---------------------
class RedButtonTests(unittest.TestCase):
    class Button:
        def __init__(self):
            self.options = {"state": "normal"}

        def cget(self, name):
            return self.options[name]

        def configure(self, **options):
            self.options.update(options)

# ---- C1-1: the MFLI adapters --------------------------------------------------------------
class MFLIAdapterTests(unittest.TestCase):
    SERVERS = ([("127.0.0.1", 8004)], ["127.0.0.1:8004 open"])

    def test_the_native_adapters_lack_the_double_calls(self):
        for adapter in (app.LabOneCAPIAdapter, app.LabOneDotNetAdapter):
            with self.subTest(adapter=adapter.__name__):
                self.assertEqual([name for name in app.NONLOCAL_MFLI_DAQ_METHODS
                                  if not callable(getattr(adapter, name, None))], ["setDouble", "getDouble"])

    def test_the_list_holds_every_call_on_the_session(self):
        called = set(re.findall(r"daq\.([A-Za-z]+)\(", inspect.getsource(app.NonLocalACController)))
        # listNodes: the Core path's own daq, before it is the session.
        self.assertEqual(called - {"listNodes"}, set(app.NONLOCAL_MFLI_DAQ_METHODS))

    def connect(self, c_api=None):
        log = []
        controller = app.NonLocalACController(log.append)
        daq = Mock()
        daq.listNodes.return_value = ["/dev4414/demods"]
        daq.getString.return_value = "18.12"
        core = types.SimpleNamespace(ziDAQServer=Mock(return_value=daq))

        def refuse(*_args, **_kwargs):
            raise AssertionError("an adapter without setDouble / getDouble was loaded")
        guards = [patch.object(app.sys, "platform", "win32"),
                  patch.object(app.NonLocalACController, "_mfli_reachable_servers", Mock(return_value=self.SERVERS)),
                  patch.object(app, "acquire_instrument_lease"), patch.object(app, "release_instrument_lease"),
                  patch.object(app.LabOneDotNetAdapter, "__init__", refuse), patch.object(app, "zi_core", core)]
        guards.append(patch.object(app.LabOneCAPIAdapter, "__init__", refuse) if c_api is None
                      else patch.object(app, "LabOneCAPIAdapter", c_api))
        for guard in guards:
            guard.start()
        try:
            identity = controller.connect_mfli("MF-DEV4414")
        finally:
            for guard in reversed(guards):
                guard.stop()
        return controller, identity, log

    def test_on_windows_they_are_skipped_and_core_connects(self):
        controller, identity, log = self.connect()
        self.assertEqual(identity, "Zurich Instruments MFLI · dev4414 · LabOne 18.12")
        self.assertEqual(controller.mfli_props["connection"], "current-python-core")
        for label in ("LabOne C API", "LabOne .NET"):
            self.assertIn(f"Zurich MFLI {label}: skipped, it has no setDouble, getDouble (the MFLI set-up and reads "
                          "need them)", log)

    def test_an_adapter_with_every_call_is_still_used(self):
        class Complete:
            label = ""
            api_dll = "ziAPI-win64.dll"

            def __init__(self, host, port, level):
                pass

            def getSample(self, path):
                return {"x": 0.0, "y": 0.0, "frequency": 317.3, "phase": 0.0}

            def getString(self, path):
                return "18.12"
        for name in app.NONLOCAL_MFLI_DAQ_METHODS:
            if not hasattr(Complete, name):
                setattr(Complete, name, lambda self, *args: None)
        controller, identity, log = self.connect(Complete)
        self.assertEqual(controller.mfli_props["connection"], "current-python-labone-c-api")
        self.assertFalse([line for line in log if "LabOne C API: skipped" in line])


# ---- C1-3: the PNA connect ------------------------------------------------------------------------
class PNAConnectTests(unittest.TestCase):
    class Instrument:
        def __init__(self, identity, fails=None):
            self.identity, self.fails, self.sent, self.closed = identity, fails, [], False

        def query(self, command):
            self.sent.append(command)
            return self.identity

        def write(self, command):
            self.sent.append(command)
            if command == self.fails:
                raise RuntimeError("VI_ERROR_TMO")

        def close(self):
            self.closed = True

    def connect(self, instrument, resource):
        manager = Mock()
        manager.open_resource.return_value = instrument
        controller = app.STFMRPNAFMRController(lambda _text: None)
        with patch.object(app, "pyvisa", Mock()), \
                patch.object(app, "acquire_shared_visa_manager", return_value=manager), \
                patch.object(app, "release_shared_visa_manager"):
            with self.assertRaises(RuntimeError):
                controller.connect(resource)
        self.assertFalse(controller.connected)
        self.assertTrue(instrument.closed)

    def test_an_unidentified_device_gets_nothing_but_idn(self):
        instrument = self.Instrument("KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234,C30")
        self.connect(instrument, "GPIB0::31::INSTR")
        self.assertEqual(instrument.sent, ["*IDN?"])

    def test_an_identified_pna_still_gets_outp_off_on_a_failure(self):
        instrument = self.Instrument("Agilent Technologies,N5242A,MY1234,A.09.90", fails="FORM:DATA ASC,0")
        self.connect(instrument, "GPIB0::32::INSTR")
        self.assertEqual(instrument.sent, ["*IDN?", "OUTP OFF", "FORM:DATA ASC,0", "OUTP OFF"])


# ---- C1-5: the LabOne .NET DLL search and its handle ----------------------------------------------
class DotNetSearchTests(unittest.TestCase):
    def test_a_bad_candidate_does_not_end_the_search(self):
        with tempfile.TemporaryDirectory() as bad, tempfile.TemporaryDirectory() as root:
            (Path(bad) / "ziDotNET-win64.dll").write_bytes(b"")     # its Core DLL is missing
            good = Path(root) / "Zurich Instruments/LabOne/API/DotNet"
            good.mkdir(parents=True)
            for name in ("ziDotNET-win64.dll", "ziDotNETCore-win64.dll"):
                (good / name).write_bytes(b"")
            with patch.dict(os.environ, {"MOKE_LABONE_DOTNET_API": bad, "ProgramW6432": root}), \
                    patch.object(app.sys, "maxsize", 2 ** 63 - 1):
                self.assertEqual(app.LabOneDotNetAdapter._find_dotnet_api(), (good, good / "ziDotNET-win64.dll"))
                with patch.dict(os.environ, {"ProgramW6432": bad}):
                    with self.assertRaisesRegex(RuntimeError, r"ziDotNETCore-win64\.dll is missing from the same "
                                                              r"directory\. Checked: "):
                        app.LabOneDotNetAdapter._find_dotnet_api()

    def test_a_failed_create_instance_closes_the_dll_directory(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: sys.path.remove(folder) if folder in sys.path else None)
        self.addCleanup(os.rmdir, folder)
        handle = Mock()
        assembly = Mock()
        clr = types.ModuleType("clr")
        clr.AddReference = Mock(return_value=assembly)
        system = types.ModuleType("System")
        system.Activator = Mock()
        system.Activator.CreateInstance.side_effect = RuntimeError("no CLR host")
        system.Enum = Mock()
        with patch.object(app.sys, "platform", "win32"), patch.dict(sys.modules, {"clr": clr, "System": system}), \
                patch.object(app.LabOneDotNetAdapter, "_find_dotnet_api",
                             return_value=(Path(folder), Path(folder) / "ziDotNET-win64.dll")), \
                patch.object(app.os, "add_dll_directory", Mock(return_value=handle), create=True):
            with self.assertRaisesRegex(RuntimeError, "Failed to create the LabOne .NET API object.*no CLR host"):
                app.LabOneDotNetAdapter("127.0.0.1", 8004, 5)
        handle.close.assert_called_once()


# ---- C2-7: the camera still releasing ---------------------------------------------------------------
class FakeCamera:
    def __init__(self, wait):
        self.wait_for_new_image = wait
        self.calls = []
        self.exposure_time, self.configuration = None, None

    def default_configuration(self):
        pass

    def record(self, *_args):
        self.calls.append("record")

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")

    def image(self, *_args):
        raise AssertionError("no frame in this test")


class CameraReleaseTests(unittest.TestCase):
    def worker(self, camera, join_s):
        stack = [patch.object(app, "pco", types.SimpleNamespace(Camera=lambda: camera), create=True),
                 patch.object(app, "CAMERA_WAIT_SLICE_S", 0.05), patch.object(app, "CAMERA_STOP_JOIN_S", join_s)]
        for guard in stack:
            guard.start()
            self.addCleanup(guard.stop)
        worker = app.CameraAcquisitionWorker(lambda _text: None)
        worker.start()
        return worker

    def app(self, releasing):
        a = app.CombinedControlApp.__new__(app.CombinedControlApp)
        a.closing = False
        self.afters = []
        a.root = AfterRoot(self.afters)
        a.log = []
        a.append_log = a.log.append
        a.magnet_scan_running = a.keithley_scan_running = a.k6221_scan_running = False
        a.camera_video_stopping = False
        a.camera_connect_button = None
        a.camera_window_status_var, a.difference_status_var = Var(""), Var("")
        for name in ("_set_connection_indicator", "_set_camera_dependent_controls", "clear_camera_roi",
                     "_update_camera_launcher_controls", "attach_camera"):
            setattr(a, name, Mock())
        self.worker_state = types.SimpleNamespace(releasing=releasing)
        a.camera_window = types.SimpleNamespace(video_recorder=None, close=Mock(), worker=self.worker_state)
        return a

if __name__ == "__main__":
    unittest.main()
