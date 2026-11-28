"""Quantum Design QDInstrument remote (.NET) sample-environment controller."""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from transport.ppms.controllers.multivu import PPMSMultiVuReadbackController
from transport.ppms.protocol import (
    validate_ppms_field_command,
    validate_ppms_temperature_command,
)

PPMS_QDINSTRUMENT_DLL_ENV = "MOKE_QDINSTRUMENT_DLL"
PPMS_FIELD_TOLERANCE_OE = 2.0
PPMS_FIELD_RELATIVE_TOLERANCE = 0.0

_PROGRAM_FILE = str(Path(__file__).resolve().parents[3] / "Transport_PPMS.py")
if not getattr(sys, "frozen", False) and {
    "site-packages",
    "dist-packages",
} & set(Path(_PROGRAM_FILE).parts):
    _PROGRAM_FILE = str(Path.cwd() / "Transport_PPMS.py")


class QDCommandUncertain(RuntimeError):
    """Raised when QDInstrument returns code 1 (command may have taken effect)."""


class PPMSQDInstrumentReadbackController:
    """PPMS sample-environment client using QDInstrument.dll via pythonnet."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.instrument = None
        self.host = ""
        self.port = 0
        self.dll_path = ""
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    @staticmethod
    def candidate_dll_paths(explicit_path: str = "") -> tuple[Path, ...]:
        candidates: list[Path] = []
        for raw_path in (explicit_path, os.environ.get(PPMS_QDINSTRUMENT_DLL_ENV, "")):
            if str(raw_path).strip():
                candidates.append(Path(str(raw_path).strip().strip('"')))
        script_directory = Path(_PROGRAM_FILE).resolve().parent
        executable_directory = Path(sys.executable).resolve().parent
        working_directory = Path.cwd()
        candidates.extend(
            (
                script_directory / "QDInstrument.dll",
                script_directory / "QDInstrument" / "QDInstrument.dll",
                executable_directory / "QDInstrument.dll",
                executable_directory / "QDInstrument" / "QDInstrument.dll",
                working_directory / "QDInstrument.dll",
                working_directory / "QDInstrument" / "QDInstrument.dll",
                Path(r"C:\QDLabVIEW\QDInstrument.dll"),
                Path(r"C:\QDInstrument\QDInstrument.dll"),
                Path(r"C:\ProgramData\Quantum Design\QDInstrument\QDInstrument.dll"),
                Path(r"C:\Program Files\Quantum Design\QDInstrument\QDInstrument.dll"),
                Path(
                    r"C:\Program Files (x86)\Quantum Design\QDInstrument\QDInstrument.dll"
                ),
                Path(
                    r"C:\Program Files\National Instruments\LabVIEW 2020\instr.lib\QDInstrument_LabView\QDInstrument.dll"
                ),
                Path(
                    r"C:\Program Files (x86)\National Instruments\LabVIEW 2020\instr.lib\QDInstrument_LabView\QDInstrument.dll"
                ),
                Path(
                    r"C:\Program Files\National Instruments\LabVIEW 2025\QDInstrument.dll"
                ),
                Path(
                    r"C:\Program Files (x86)\National Instruments\LabVIEW 2025\QDInstrument.dll"
                ),
                Path(r"D:\user data\Niu Heng\QDInstrument.dll"),
                Path(r"D:\user data\Niu Heng\QDInstrument\QDInstrument.dll"),
            )
        )
        for raw_directory in (
            *sys.path,
            *os.environ.get("PATH", "").split(os.pathsep),
        ):
            if str(raw_directory).strip():
                candidates.append(
                    Path(str(raw_directory).strip().strip('"')) / "QDInstrument.dll"
                )
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return tuple(unique)

    @staticmethod
    def _windows_discovery_roots() -> tuple[Path, ...]:
        roots: list[Path] = []
        for variable in (
            "USERPROFILE",
            "PUBLIC",
            "PROGRAMDATA",
            "ProgramFiles",
            "ProgramFiles(x86)",
        ):
            raw = os.environ.get(variable, "").strip()
            if not raw:
                continue
            base = Path(raw)
            if variable in ("USERPROFILE", "PUBLIC"):
                roots.extend((base / "Desktop", base / "Documents"))
            elif variable == "PROGRAMDATA":
                roots.append(base / "Quantum Design")
            else:
                roots.extend((base / "Quantum Design", base / "National Instruments"))
        roots.extend(
            (
                Path("C:\\QDInstrument"),
                Path("C:\\QDLabVIEW"),
                Path("D:\\user data\\Niu Heng"),
            )
        )
        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return tuple(unique)

    @classmethod
    def discover_dll_paths(
        cls,
        explicit_path: str = "",
        *,
        recursive: bool = False,
        time_limit_s: float = 8.0,
        max_results: int = 8,
    ) -> tuple[Path, ...]:
        matches: list[Path] = []
        seen: set[str] = set()

        def add(path: Path) -> bool:
            try:
                if not path.is_file():
                    return False
                resolved = path.resolve()
            except (OSError, RuntimeError):
                return False
            key = str(resolved).casefold()
            if key in seen:
                return False
            seen.add(key)
            matches.append(resolved)
            return len(matches) >= max(1, int(max_results))

        for path in cls.candidate_dll_paths(explicit_path):
            if add(path):
                return tuple(matches)
        if not recursive or not sys.platform.startswith("win"):
            return tuple(matches)
        deadline = time.monotonic() + max(0.5, float(time_limit_s))
        skipped_names = {
            "$recycle.bin",
            ".git",
            ".svn",
            "appdata",
            "cache",
            "node_modules",
            "site-packages",
            "temp",
            "windowsapps",
        }
        for root in cls._windows_discovery_roots():
            if time.monotonic() >= deadline:
                break
            try:
                if not root.is_dir():
                    continue
                root_depth = len(root.parts)
                for current, directory_names, file_names in os.walk(root):
                    if time.monotonic() >= deadline:
                        break
                    current_path = Path(current)
                    depth = len(current_path.parts) - root_depth
                    directory_names[:] = [
                        name
                        for name in directory_names
                        if name.casefold() not in skipped_names
                        and not name.startswith(".")
                    ]
                    if depth >= 7:
                        directory_names[:] = []
                    for file_name in file_names:
                        if file_name.casefold() == "qdinstrument.dll":
                            if add(current_path / file_name):
                                return tuple(matches)
            except (OSError, PermissionError):
                continue
        return tuple(matches)

    @classmethod
    def locate_dll(cls, explicit_path: str = "") -> Path:
        candidates = cls.candidate_dll_paths(explicit_path)
        matches = cls.discover_dll_paths(explicit_path)
        if matches:
            return matches[0]
        checked = "\n".join(f"  - {path}" for path in candidates)
        raise RuntimeError(
            "QDInstrument.dll was not found. Please select that DLL from the currently available LabVIEW QDInstrument "
            "directory, or set the environment variable "
            f"{PPMS_QDINSTRUMENT_DLL_ENV}. Checked:\n{checked}"
        )

    @staticmethod
    def _import_qdinstrument_api():
        from QuantumDesign.QDInstrument import QDInstrumentBase, QDInstrumentFactory

        return QDInstrumentBase, QDInstrumentFactory

    @classmethod
    def _load_qdinstrument_api(cls, clr, explicit_path: str = ""):
        attempts: list[str] = []
        for dll in cls.discover_dll_paths(explicit_path):
            try:
                clr.AddReference(str(dll))
                api = cls._import_qdinstrument_api()
                return (*api, str(dll))
            except BaseException as error:
                attempts.append(f"{dll}: {error}")
        try:
            clr.AddReference("QDInstrument")
            api = cls._import_qdinstrument_api()
            return (*api, ".NET auto-resolved: QDInstrument")
        except BaseException as error:
            attempts.append(f".NET assembly name QDInstrument: {error}")
        for dll in cls.discover_dll_paths(
            explicit_path, recursive=True, time_limit_s=8.0
        ):
            try:
                clr.AddReference(str(dll))
                api = cls._import_qdinstrument_api()
                return (*api, str(dll))
            except BaseException as error:
                detail = f"{dll}: {error}"
                if detail not in attempts:
                    attempts.append(detail)
        explicit_note = ""
        if str(explicit_path).strip():
            explicit_note = f"\nManually entered path: {explicit_path}"
        attempt_text = "\n".join(f"  - {item}" for item in attempts[-8:])
        raise RuntimeError(
            "TCP 11000 being reachable only means QDInstrument_Server is listening; the local Python "
            "still needs the same QDInstrument.dll set as LabVIEW. The program tried loading by .NET assembly name, "
            "common program/LabVIEW directories and a time-limited automatic search, but did not succeed.\n"
            "Please locate QDInstrument.dll in an available LabVIEW project directory, or copy the DLL matching "
            "the LabVIEW/Server set into the main program directory."
            f"{explicit_note}\nLoad log:\n{attempt_text}"
        )

    @staticmethod
    def _normalize_readback(raw_result, quantity: str) -> tuple[float, str]:
        if isinstance(raw_result, tuple):
            values = list(raw_result)
        elif isinstance(raw_result, list):
            values = raw_result
        else:
            try:
                values = list(raw_result)
            except TypeError:
                values = [raw_result]
        if len(values) < 2:
            raise RuntimeError(
                f"QDInstrument {quantity} returned an unexpected format: {raw_result!r}"
            )
        physical_value, status = values[-2], values[-1]
        try:
            physical_value = float(physical_value)
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                f"QDInstrument {quantity} reading is not numeric: {physical_value!r}"
            ) from error
        if not math.isfinite(physical_value):
            raise RuntimeError(
                f"QDInstrument {quantity} returned a non-finite reading: {physical_value!r}"
            )
        if len(values) >= 3:
            return_code = values[0]
            try:
                numeric_code = int(return_code)
            except (TypeError, ValueError):
                numeric_code = 0
            if numeric_code != 0:
                raise RuntimeError(
                    f"QDInstrument {quantity} read failed, return code {numeric_code}"
                )
        status_text = getattr(status, "name", None)
        if not status_text:
            try:
                status_text = status.ToString()
            except BaseException:
                status_text = str(status)
        return physical_value, str(status_text)

    @staticmethod
    def _reflection_signature(method) -> str:
        try:
            parameters = method.GetParameters()
            parts = []
            for parameter in parameters:
                parameter_type = parameter.ParameterType
                if bool(getattr(parameter_type, "IsByRef", False)):
                    parameter_type = parameter_type.GetElementType()
                    prefix = "ref "
                else:
                    prefix = ""
                type_name = getattr(parameter_type, "FullName", None)
                if not type_name:
                    type_name = str(parameter_type)
                parts.append(f"{prefix}{type_name}")
            return f"{method.Name}({', '.join(parts)})"
        except BaseException:
            return str(method)

    @classmethod
    def _invoke_ref_readback(cls, instrument, method_name: str):
        try:
            import System
        except ImportError as error:
            raise RuntimeError(
                "pythonnet loaded QDInstrument.dll, but the System reflection interface cannot be imported"
            ) from error
        try:
            methods = [
                method
                for method in instrument.GetType().GetMethods()
                if str(method.Name) == str(method_name)
            ]
        except BaseException as error:
            raise RuntimeError(
                f"Cannot reflect QDInstrument method {method_name}: {error}"
            ) from error
        if not methods:
            raise RuntimeError(
                f"The current QDInstrument.dll has no method {method_name}"
            )

        def rank(method) -> tuple[int, int]:
            try:
                parameters = list(method.GetParameters())
            except BaseException:
                return (99, 99)
            ref_count = sum(
                bool(getattr(item.ParameterType, "IsByRef", False))
                for item in parameters
            )
            return (0 if len(parameters) == 2 else 1, -ref_count)

        errors: list[str] = []
        for method in sorted(methods, key=rank):
            signature = cls._reflection_signature(method)
            try:
                parameters = list(method.GetParameters())
                if len(parameters) != 2:
                    errors.append(f"{signature}: parameter count is not 2")
                    continue
                arguments = System.Array[System.Object](len(parameters))
                for index, parameter in enumerate(parameters):
                    parameter_type = parameter.ParameterType
                    if bool(getattr(parameter_type, "IsByRef", False)):
                        value_type = parameter_type.GetElementType()
                    else:
                        value_type = parameter_type
                    if bool(getattr(value_type, "IsEnum", False)):
                        value = System.Enum.ToObject(value_type, 0)
                    elif bool(getattr(value_type, "IsValueType", False)):
                        value = System.Activator.CreateInstance(value_type)
                    else:
                        value = None
                    arguments[index] = value
                return_value = method.Invoke(instrument, arguments)
                output_values = list(arguments)
                if return_value is not None:
                    output_values.insert(0, return_value)
                return tuple(output_values)
            except BaseException as error:
                errors.append(f"{signature}: {error}")
        details = "\n".join(f"  - {item}" for item in errors)
        raise RuntimeError(f"QDInstrument {method_name} ref/out call failed:\n{details}")

    @classmethod
    def _invoke_typed_command(cls, instrument, method_name: str, values):
        try:
            import System
        except ImportError as error:
            raise RuntimeError(
                "pythonnet loaded QDInstrument.dll, but the System reflection interface cannot be imported"
            ) from error
        methods = [
            method
            for method in instrument.GetType().GetMethods()
            if str(method.Name) == str(method_name)
        ]
        if not methods:
            raise RuntimeError(
                f"The current QDInstrument.dll has no method {method_name}"
            )
        errors: list[str] = []
        for method in methods:
            invoked = False
            signature = cls._reflection_signature(method)
            try:
                parameters = list(method.GetParameters())
                if len(parameters) != len(values):
                    continue
                arguments = System.Array[System.Object](len(parameters))
                for index, (parameter, raw_value) in enumerate(zip(parameters, values)):
                    value_type = parameter.ParameterType
                    if bool(getattr(value_type, "IsByRef", False)):
                        value_type = value_type.GetElementType()
                    if bool(getattr(value_type, "IsEnum", False)):
                        converted = System.Enum.ToObject(value_type, int(raw_value))
                    else:
                        try:
                            converted = System.Convert.ChangeType(
                                raw_value,
                                value_type,
                                System.Globalization.CultureInfo.InvariantCulture,
                            )
                        except BaseException:
                            converted = value_type(raw_value)
                    arguments[index] = converted
                invoked = True
                return_value = method.Invoke(instrument, arguments)
                return_type = getattr(method, "ReturnType", None)
                if str(getattr(return_type, "FullName", "")) == "System.Boolean":
                    if not bool(return_value):
                        raise RuntimeError("returned Boolean False")
                elif return_value is not None and int(return_value) != 0:
                    if int(return_value) == 1 and method_name in (
                        "SetTemperature",
                        "SetField",
                    ):
                        raise QDCommandUncertain(
                            f"{method_name} return code 1; the command may have taken effect, not resending"
                        )
                    raise RuntimeError(
                        f"return code {int(return_value)} (return type {return_type}); "
                        "the command may have taken effect; please verify the MultiVu target and rate and do not resubmit"
                    )
                return return_value
            except BaseException as error:
                if invoked:
                    raise
                errors.append(f"{signature}: {error}")
        details = "\n".join(f"  - {item}" for item in errors)
        raise RuntimeError(f"QDInstrument {method_name} call failed:\n{details}")

    def connect(self, host: str, port: int, dll_path: str = "") -> dict[str, object]:
        host, port = PPMSMultiVuReadbackController.validate_endpoint(host, port)
        with self.lock:
            if self.instrument is not None:
                return self.read_environment()
            if not sys.platform.startswith("win"):
                raise RuntimeError(
                    "The QDInstrument remote interface can only be loaded on Windows"
                )
            try:
                import clr
            except ImportError as error:
                raise RuntimeError(
                    "The current Python lacks pythonnet; please run:\npy -m pip install pythonnet"
                ) from error
            try:
                QDInstrumentBase, QDInstrumentFactory, load_source = (
                    self._load_qdinstrument_api(clr, dll_path)
                )
                instrument = QDInstrumentFactory.GetQDInstrument(
                    QDInstrumentBase.QDInstrumentType.PPMS, True, host, int(port)
                )
            except BaseException as error:
                raise RuntimeError(
                    "Loading QDInstrument.dll or creating the remote PPMS session failed. Please confirm the DLL is not "
                    "blocked by Windows, that .NET Framework 3.5 is enabled on the ThinkPad, and that the same set of "
                    f"QDInstrument files as the lab PC is used. Original error: {error}"
                ) from error
            self.instrument = instrument
            self.host = host
            self.port = port
            self.dll_path = str(load_source)
            try:
                result = self.read_environment()
            except BaseException:
                self.disconnect()
                raise
            self.logger(
                f"PPMS QDInstrument connected: {host}:{port}; assembly={load_source}"
            )
            return result

    def read_environment(self) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("PPMS QDInstrument is not connected")
            temperature_raw = self._invoke_ref_readback(
                self.instrument, "GetTemperature"
            )
            field_raw = self._invoke_ref_readback(self.instrument, "GetField")
        temperature_k, temperature_status = self._normalize_readback(
            temperature_raw, "temperature"
        )
        field_oe, field_status = self._normalize_readback(field_raw, "magnetic field")
        data = {
            "temperature_k": temperature_k,
            "temperature_status": temperature_status,
            "field_oe": field_oe,
            "field_status": field_status,
        }
        self._verify_environment_command(data)
        return data

    def _verify_environment_command(self, data):
        pending = getattr(self, "pending_environment_command", None)
        if not pending:
            return
        kind, target, deadline = pending
        temperature = kind == "SetTemperature"
        label = "Temperature" if temperature else "Magnetic field"
        measured = float(data["temperature_k" if temperature else "field_oe"])
        status = (
            str(data["temperature_status" if temperature else "field_status"])
            .lower()
            .replace(" ", "")
        )
        stable = (
            status in ("stable", "stableattarget")
            if temperature
            else status
            in (
                "stable",
                "holding",
                "persistent",
                "stablepersistent",
                "stabledriven",
                "standby",
            )
        )
        tolerance = (
            0.1
            if temperature
            else max(
                PPMS_FIELD_TOLERANCE_OE, abs(target) * PPMS_FIELD_RELATIVE_TOLERANCE
            )
        )
        if stable and abs(measured - target) <= tolerance:
            text = (
                f"{label} reply uncertain; readback now confirms the target {target:g} "
                "was reached and is stable (rate not confirmed by readback)"
            )
            self.pending_environment_command = None
        elif time.monotonic() >= deadline:
            text = (
                f"{label} readback confirmation timed out; current {measured:g}, "
                f"target {target:g}, please check MultiVu; not resent"
            )
            self.pending_environment_command = None
        else:
            text = (
                f"{label} reply uncertain, confirming by readback; target {target:g}, "
                f"current {measured:g}; not resent"
            )
        self.environment_command_status = text
        data["command_verification"] = text

    def _send_environment_command(self, name, values):
        self.pending_environment_command = None
        try:
            self._invoke_typed_command(self.instrument, name, values)
        except QDCommandUncertain as error:
            self.logger(str(error))
            self.pending_environment_command = (
                name,
                float(values[0]),
                time.monotonic() + (14400 if name == "SetTemperature" else 3600),
            )

    def set_temperature(
        self, target_k: float, rate_k_min: float, approach_code: int
    ) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("PPMS QDInstrument is not connected")
            current = self.read_environment()
            target_k, rate_k_min = validate_ppms_temperature_command(
                target_k, rate_k_min, float(current["temperature_k"])
            )
            if int(approach_code) not in (0, 1):
                raise ValueError("PPMS temperature approach parameter is invalid")
            self._send_environment_command(
                "SetTemperature", (target_k, rate_k_min, int(approach_code))
            )
            self.logger(
                f"PPMS temperature setpoint: {target_k:g} K, {rate_k_min:g} K/min"
            )
            return self.read_environment()

    def set_field(
        self, target_oe: float, rate_oe_s: float, approach_code: int, mode_code: int
    ) -> dict[str, object]:
        target_oe, rate_oe_s = validate_ppms_field_command(target_oe, rate_oe_s)
        if int(approach_code) not in (0, 1, 2):
            raise ValueError("PPMS field approach parameter is invalid")
        if int(mode_code) not in (0, 1):
            raise ValueError("PPMS field end mode parameter is invalid")
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("PPMS QDInstrument is not connected")
            self._send_environment_command(
                "SetField",
                (target_oe, rate_oe_s, int(approach_code), int(mode_code)),
            )
            self.logger(
                f"PPMS field setpoint: {target_oe:+g} Oe, {rate_oe_s:g} Oe/s, mode={int(mode_code)}"
            )
            return self.read_environment()

    def disconnect(self) -> None:
        with self.lock:
            instrument = self.instrument
            self.instrument = None
            self.host = ""
            self.port = 0
            self.dll_path = ""
            if instrument is None:
                return
            for method_name in ("Dispose", "Close"):
                method = getattr(instrument, method_name, None)
                if callable(method):
                    try:
                        method()
                    except BaseException:
                        pass
                    break
            self.logger(
                "PPMS QDInstrument client disconnected (current setpoints unchanged)"
            )


__all__ = [
    "PPMSQDInstrumentReadbackController",
    "PPMS_FIELD_RELATIVE_TOLERANCE",
    "PPMS_FIELD_TOLERANCE_OE",
    "PPMS_QDINSTRUMENT_DLL_ENV",
    "QDCommandUncertain",
]
