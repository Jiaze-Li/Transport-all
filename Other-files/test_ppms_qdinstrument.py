"""Offline regression tests for PPMSQDInstrumentReadbackController (T1.1 / A1-1).

Restored from main:Other-files/test/test_instrument_safety_logic.py (L2023-2249)
for the standalone Transport-PPMS application. No vendor DLLs, pythonnet, or
hardware required.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

from transport.ppms.controllers import qdinstrument as qd_mod


class PPMSQDInstrumentTests(unittest.TestCase):
    def test_qdinstrument_pythonnet_readback_shapes(self):
        value, status = qd_mod.PPMSQDInstrumentReadbackController._normalize_readback(
            (0, 7.25, "Stable"), "温度"
        )
        self.assertEqual(value, 7.25)
        self.assertEqual(status, "Stable")
        value, status = qd_mod.PPMSQDInstrumentReadbackController._normalize_readback(
            (-1234.5, "Holding"), "磁场"
        )
        self.assertEqual(value, -1234.5)
        self.assertEqual(status, "Holding")
        with self.assertRaises(RuntimeError):
            qd_mod.PPMSQDInstrumentReadbackController._normalize_readback(
                (1, 7.25, "Error"), "温度"
            )

    def test_qdinstrument_explicit_dll_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "QDInstrument.dll"
            dll.write_bytes(b"offline-test-placeholder")
            matches = qd_mod.PPMSQDInstrumentReadbackController.discover_dll_paths(
                str(dll)
            )
            self.assertTrue(matches)
            self.assertEqual(matches[0], dll.resolve())

    def test_qdinstrument_loader_can_use_dotnet_assembly_name(self):
        class FakeClr:
            def __init__(self):
                self.references = []

            def AddReference(self, reference):
                self.references.append(reference)

        controller = qd_mod.PPMSQDInstrumentReadbackController
        original_discover = controller.__dict__["discover_dll_paths"]
        original_import = controller.__dict__["_import_qdinstrument_api"]
        fake_clr = FakeClr()
        try:
            controller.discover_dll_paths = classmethod(
                lambda cls, *args, **kwargs: ()
            )
            controller._import_qdinstrument_api = staticmethod(
                lambda: ("QDInstrumentBase", "QDInstrumentFactory")
            )
            base, factory, source = controller._load_qdinstrument_api(fake_clr)
        finally:
            controller.discover_dll_paths = original_discover
            controller._import_qdinstrument_api = original_import
        self.assertEqual(base, "QDInstrumentBase")
        self.assertEqual(factory, "QDInstrumentFactory")
        self.assertEqual(source, ".NET auto-resolved: QDInstrument")
        self.assertEqual(fake_clr.references, ["QDInstrument"])

    def test_qdinstrument_ref_readback_uses_reflected_argument_types(self):
        class FakeValueType:
            IsByRef = False
            IsEnum = False
            IsValueType = True
            FullName = "System.Double"

        class FakeRefType:
            IsByRef = True
            FullName = "System.Double&"

            @staticmethod
            def GetElementType():
                return FakeValueType

        class FakeParameter:
            ParameterType = FakeRefType

        class FakeMethod:
            Name = "GetTemperature"

            @staticmethod
            def GetParameters():
                return [FakeParameter(), FakeParameter()]

            @staticmethod
            def Invoke(_instrument, arguments):
                arguments[0] = 7.25
                arguments[1] = "Stable"
                return 0

        class FakeRuntimeType:
            @staticmethod
            def GetMethods():
                return [FakeMethod()]

        class FakeInstrument:
            @staticmethod
            def GetType():
                return FakeRuntimeType()

        class FakeArray:
            @classmethod
            def __class_getitem__(cls, _item):
                return lambda count: [None] * count

        fake_system = types.ModuleType("System")
        fake_system.Array = FakeArray
        fake_system.Object = object
        fake_system.Enum = types.SimpleNamespace(
            ToObject=lambda _value_type, value: value
        )
        fake_system.Activator = types.SimpleNamespace(
            CreateInstance=lambda _value_type: 0.0
        )
        original_system = sys.modules.get("System")
        sys.modules["System"] = fake_system
        try:
            raw = qd_mod.PPMSQDInstrumentReadbackController._invoke_ref_readback(
                FakeInstrument(), "GetTemperature"
            )
        finally:
            if original_system is None:
                sys.modules.pop("System", None)
            else:
                sys.modules["System"] = original_system
        self.assertEqual(raw, (0, 7.25, "Stable"))
        self.assertEqual(
            qd_mod.PPMSQDInstrumentReadbackController._normalize_readback(
                raw, "温度"
            ),
            (7.25, "Stable"),
        )

    def test_qdinstrument_setfield_uses_reflected_value_types(self):
        captured = []

        class FakeValueType:
            IsByRef = False
            IsEnum = False
            IsValueType = True
            FullName = "System.Double"

            def __call__(self, value):
                return float(value)

        class FakeEnumType:
            IsByRef = False
            IsEnum = True
            IsValueType = True
            FullName = "QD.FieldMode"

        class FakeParameter:
            def __init__(self, value_type):
                self.ParameterType = value_type

        class FakeMethod:
            Name = "SetField"

            @staticmethod
            def GetParameters():
                return [
                    FakeParameter(FakeValueType()),
                    FakeParameter(FakeValueType()),
                    FakeParameter(FakeEnumType()),
                    FakeParameter(FakeEnumType()),
                ]

            @staticmethod
            def Invoke(_instrument, arguments):
                captured.extend(arguments)
                return 0

        class FakeRuntimeType:
            @staticmethod
            def GetMethods():
                return [FakeMethod()]

        class FakeInstrument:
            @staticmethod
            def GetType():
                return FakeRuntimeType()

        class FakeArray:
            @classmethod
            def __class_getitem__(cls, _item):
                return lambda count: [None] * count

        fake_system = types.ModuleType("System")
        fake_system.Array = FakeArray
        fake_system.Object = object
        fake_system.Enum = types.SimpleNamespace(
            ToObject=lambda _value_type, value: int(value)
        )
        fake_system.Convert = types.SimpleNamespace(
            ChangeType=lambda value, _value_type, _culture: float(value)
        )
        fake_system.Globalization = types.SimpleNamespace(
            CultureInfo=types.SimpleNamespace(InvariantCulture=object())
        )
        original_system = sys.modules.get("System")
        sys.modules["System"] = fake_system
        try:
            result = qd_mod.PPMSQDInstrumentReadbackController._invoke_typed_command(
                FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
            )
            FakeMethod.ReturnType = types.SimpleNamespace(FullName="System.Boolean")
            FakeMethod.Invoke = staticmethod(lambda _instrument, _arguments: True)
            self.assertIs(
                qd_mod.PPMSQDInstrumentReadbackController._invoke_typed_command(
                    FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
                ),
                True,
            )
            FakeMethod.Invoke = staticmethod(lambda _instrument, _arguments: False)
            with self.assertRaisesRegex(RuntimeError, "Boolean False"):
                qd_mod.PPMSQDInstrumentReadbackController._invoke_typed_command(
                    FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
                )
            FakeMethod.ReturnType = types.SimpleNamespace(FullName="System.Int32")
            FakeMethod.Invoke = staticmethod(lambda _instrument, _arguments: 1)
            with self.assertRaisesRegex(RuntimeError, "may have taken effect"):
                qd_mod.PPMSQDInstrumentReadbackController._invoke_typed_command(
                    FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
                )
        finally:
            if original_system is None:
                sys.modules.pop("System", None)
            else:
                sys.modules["System"] = original_system
        self.assertEqual(result, 0)
        self.assertEqual(captured, [1000.0, 100.0, 1, 1])


if __name__ == "__main__":
    unittest.main()
