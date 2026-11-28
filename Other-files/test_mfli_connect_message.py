"""Offline checks for the MFLI connect failure diagnostics; no LabOne API."""
import unittest
from unittest.mock import Mock

from test_ppms_motor_debug import app

Controller = app.NonLocalACController
SERVERS = [("127.0.0.1", 8004)]


class ConnectFailureMessageTests(unittest.TestCase):
    def test_device_missing_on_server_leads_the_headline(self):
        text = Controller._mfli_connect_failure_message(
            "dev4414", SERVERS, ["Core 127.0.0.1:8004：cannot attach"],
            {("127.0.0.1", 8004): "visible=dev4435; connected=dev4435"})
        first = app.short_status_error(RuntimeError(text), 200)
        self.assertIn("dev4414 is not on that server", first)
        self.assertIn("dev4435", first)
        self.assertNotIn("handshake failed", first)
        self.assertIn("Server device list", text)
        self.assertNotIn("18.12", text.split("\n\n")[-1])

    def test_handshake_failure_names_last_step_and_suggests_18_12(self):
        text = Controller._mfli_connect_failure_message(
            "dev4414", SERVERS,
            ["LabOne C API 127.0.0.1:8004：ziAPIConnectEx failed", "Core 127.0.0.1:8004：version mismatch"],
            {})
        first = app.short_status_error(RuntimeError(text), 200)
        self.assertIn("handshake failed", first)
        self.assertIn("version mismatch", first)
        self.assertIn("INSTALL_MFLI_LABONE_18_12.bat", text)
        self.assertNotIn("DEV4435", text)

    def test_device_present_in_report_is_not_treated_as_missing(self):
        text = Controller._mfli_connect_failure_message(
            "dev4414", SERVERS, ["Core：read failed"],
            {("127.0.0.1", 8004): "visible=dev4414,dev4435"})
        self.assertIn("handshake failed", text.split("\n\n")[0])

    def test_server_device_report_is_best_effort(self):
        daq = Mock()
        daq.getString.side_effect = lambda node: "dev4435" if node.endswith("visible") else (_ for _ in ()).throw(RuntimeError("no node"))
        self.assertEqual(Controller._mfli_server_devices(daq), "visible=dev4435")
        daq.getString.side_effect = RuntimeError("no string ABI")
        self.assertEqual(Controller._mfli_server_devices(daq), "")

    def test_native_labone_adapters_implement_all_daq_methods(self):
        import ctypes
        import transport.ppms.controllers.zurich as zurich_ctrl

        for adapter_cls in (zurich_ctrl.LabOneDotNetAdapter, zurich_ctrl.LabOneCAPIAdapter):
            missing = [
                name
                for name in zurich_ctrl.NONLOCAL_MFLI_DAQ_METHODS
                if not callable(getattr(adapter_cls, name, None))
            ]
            self.assertEqual(missing, [], f"{adapter_cls.__name__} missing {missing}")

        dotnet = zurich_ctrl.LabOneDotNetAdapter.__new__(zurich_ctrl.LabOneDotNetAdapter)
        dotnet._daq = Mock()
        dotnet._daq.getDouble.return_value = 0.25
        dotnet.setDouble("/dev4435/demods/0/rate", 10.5)
        dotnet._daq.setDouble.assert_called_once_with("/dev4435/demods/0/rate", 10.5)
        self.assertEqual(dotnet.getDouble("/dev4435/demods/0/rate"), 0.25)

        capi = zurich_ctrl.LabOneCAPIAdapter.__new__(zurich_ctrl.LabOneCAPIAdapter)
        capi._connection = ctypes.c_void_p(1)
        capi._lib = Mock()
        capi._lib.ziAPISetValueD.return_value = 0

        def fake_get_d(_conn, _path, out_ptr):
            out_ptr._obj.value = 1.25
            return 0

        capi._lib.ziAPIGetValueD.side_effect = fake_get_d
        capi.setDouble("/dev4435/demods/0/timeconstant", 1.0)
        capi._lib.ziAPISetValueD.assert_called_once()
        self.assertEqual(capi.getDouble("/dev4435/demods/0/timeconstant"), 1.25)


if __name__ == "__main__":
    unittest.main()
