"""Zurich Instruments MFLI controller, LabOne adapters, and legacy Python 3.7 bridge."""

from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

try:
    import numpy as np
except ImportError:
    np = None
try:
    import pyvisa
except ImportError:
    pyvisa = None
try:
    import zhinst.core as zi_core
except ImportError:
    zi_core = None
try:
    import zhinst.utils as zi_utils
except ImportError:
    zi_utils = None

from transport.common.harmonics import DEFAULT_HARMONIC_MODE_ORDERS
from transport.ppms.controllers.sr830 import (
    LOCKIN_DOWNRANGE_FRACTION,
    LOCKIN_DOWNRANGE_READINGS,
    LOCKIN_UPRANGE_FRACTION,
    NONLOCAL_LOCKIN_FILTER_ORDER,
    NONLOCAL_LOCKIN_TIME_CONSTANT_S,
    NONLOCAL_SR830_FULL_SCALE_V,
    SR830_DISPLAY_XY_COMMANDS,
    SR830_TIME_CONSTANTS_S,
    show_sr830_x_and_y,
)
from transport.ppms.controllers.visa import (
    acquire_instrument_lease,
    acquire_shared_visa_manager,
    release_instrument_lease,
    release_shared_visa_manager,
)

_PROGRAM_FILE = str(Path(__file__).resolve().parents[3] / "Transport_PPMS.py")
if not getattr(sys, "frozen", False) and {"site-packages", "dist-packages"} & set(Path(_PROGRAM_FILE).parts):
    _PROGRAM_FILE = str(Path.cwd() / "Transport_PPMS.py")
PROGRAM_DIRECTORY = Path(sys.argv[0] if getattr(sys, "frozen", False) else _PROGRAM_FILE).resolve().parent

K6221_MAX_COMPLIANCE_V = 105.0
K6221_MIN_COMPLIANCE_V = 0.1
K6221_OUTPUT_LOW_FLOATING = "OUTP:LTE 0"
K6221_OUTPUT_LOW_QUERY = "OUTP:LTE?"
MFLI_DOWNRANGE_READINGS_LIMIT = LOCKIN_DOWNRANGE_READINGS * 1024
NONLOCAL_MFLI_DEVICE = "dev4435"
NONLOCAL_MFLI_SERVER_HOST = "192.168.69.78"
NONLOCAL_MFLI_SERVER_PORT = 8004
NONLOCAL_MFLI_SERVER_CANDIDATES = (("127.0.0.1", NONLOCAL_MFLI_SERVER_PORT), (NONLOCAL_MFLI_SERVER_HOST, NONLOCAL_MFLI_SERVER_PORT))
SUPER_ZURICH_MFLI_DEVICE = "dev4414"
SUPER_ZURICH_MFLI_SERVERS = (("192.168.68.250", NONLOCAL_MFLI_SERVER_PORT),)
ZURICH_MFLI_CHOICES = {"DEV4435 (Normal)": (NONLOCAL_MFLI_DEVICE, None), "DEV4414 (Super)": (SUPER_ZURICH_MFLI_DEVICE, SUPER_ZURICH_MFLI_SERVERS)}
NONLOCAL_MFLI_API_LEVEL = 5
NONLOCAL_MFLI_DAQ_METHODS = ("setInt", "getInt", "setDouble", "getDouble", "sync", "getSample", "getString", "connectDevice", "disconnect")
NONLOCAL_MFLI_DEMODULATOR = 0
NONLOCAL_MFLI_EXTREF_DEMODULATOR = 1
NONLOCAL_MFLI_EXTREF_CHANNEL = 0
NONLOCAL_MFLI_INPUT_SCALING = 1.0
NONLOCAL_MFLI_REFERENCE_INPUTS = {"Aux Input 1": 8, "Aux Input 2": 9, "Trigger In 1": 2, "Trigger In 2": 3}
NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT = "Aux Input 1"
NONLOCAL_MFLI_LEGACY_PYTHON_ENV = "MOKE_MFLI_PYTHON"
NONLOCAL_2182_RESOURCE = "GPIB0::7::INSTR"
NONLOCAL_SAMPLE_HZ = 1.0
NONLOCAL_MARKER_LINE = 4
NONLOCAL_MARKER_PHASE_DEG = 0.0
NONLOCAL_MAX_PEAK_A = 0.105
NONLOCAL_VI_SR830_TIME_CONSTANT_CODE = 10
PPMS_ZURICH_MODE_ORDERS = dict(DEFAULT_HARMONIC_MODE_ORDERS)

def short_status_error(error: BaseException, limit: int = 120) -> str:
    text = " ".join(str(error).replace("\r", "\n").split())
    first_sentence = re.split(r"(?<=[。；;])\s+|\n", text, maxsplit=1)[0]
    if len(first_sentence) <= limit:
        return first_sentence
    return first_sentence[: max(1, limit - 1)].rstrip() + "…"

def zurich_mfli_choice(text: str) -> tuple[str, tuple[tuple[str, int], ...] | None]:
    text = str(text).strip()
    if text in ZURICH_MFLI_CHOICES:
        return ZURICH_MFLI_CHOICES[text]
    device = text.lower()
    if device.startswith("mf-"):
        device = device[3:]
    for known_device, servers in ZURICH_MFLI_CHOICES.values():
        if device == known_device:
            return known_device, servers
    return device, None

def zurich_mfli_label(text: str) -> str:
    device, _servers = zurich_mfli_choice(text)
    return next((label for label, (known, _) in ZURICH_MFLI_CHOICES.items() if known == device), str(text).strip())

def zurich_choice_hint(device: str) -> str:
    device = zurich_mfli_choice(device)[0]
    if device == SUPER_ZURICH_MFLI_DEVICE:
        return 'This is the Super Zurich, DEV4414. If MF Device Finder lists DEV4435 instead, choose "DEV4435 (Normal)" in the page\'s Zurich list.'
    if device == NONLOCAL_MFLI_DEVICE:
        return 'If MF Device Finder lists DEV4414 at 192.168.68.250 instead, that is the Super Zurich: choose "DEV4414 (Super)" in the page\'s Zurich list.'
    return 'The page\'s Zurich list offers the lab\'s two MFLIs: "DEV4435 (Normal)" and "DEV4414 (Super)", the Super Zurich at 192.168.68.250.'

class LabOneDotNetAdapter:
    def __init__(self, host: str, port: int, api_level: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The LabOne .NET API can only be loaded on the Windows control PC")
        try:
            import clr
        except ImportError as error:
            raise RuntimeError('The current Python lacks pythonnet; cannot load the .NET API bundled with LabOne') from error
        dll_directory, api_dll = self._find_dotnet_api()
        self.dll_directory = dll_directory
        self.api_dll = api_dll
        self._dll_directory_handle = None
        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(str(dll_directory))
        if str(dll_directory) not in sys.path:
            sys.path.insert(0, str(dll_directory))
        try:
            assembly = clr.AddReference(str(api_dll))
            from System import Activator, Enum
            api_type = assembly.GetType("zhinst.ziDotNET")
            enum_type = assembly.GetType("zhinst.ZIAPIVersion_enum")
            if api_type is None or enum_type is None:
                exported = list(assembly.GetExportedTypes())
                if api_type is None:
                    api_type = next((item for item in exported if item.Name == 'ziDotNET'), None)
                if enum_type is None:
                    enum_type = next((item for item in exported if item.Name == 'ZIAPIVersion_enum'), None)
            if api_type is None or enum_type is None:
                raise RuntimeError("ziDotNET or the API level enum was not found in the DLL")
        except BaseException as error:
            self._close_dll_directory()
            raise RuntimeError(f'Failed to load the LabOne .NET DLL ({api_dll}): {error}') from error
        try:
            self._daq = Activator.CreateInstance(api_type)
            enum_level = Enum.ToObject(enum_type, int(api_level))
        except BaseException as error:
            self._close_dll_directory()
            raise RuntimeError(f"Failed to create the LabOne .NET API object ({api_dll}): {error}") from error
        try:
            try:
                self._daq.init(str(host), int(port), enum_level, True)
            except TypeError:
                self._daq.init(str(host), int(port), enum_level)
            self._daq.connect()
        except BaseException as error:
            try:
                self._daq.terminate()
            except BaseException:
                pass
            self._close_dll_directory()
            raise RuntimeError(f'LabOne .NET API failed to connect to {host}:{port}: {error}') from error
    @staticmethod
    def _find_dotnet_api() -> tuple[Path, Path]:
        is_64_bit = sys.maxsize > 2**32
        suffix = "win64" if is_64_bit else "win32"
        api_filename = f"ziDotNET-{suffix}.dll"
        roots: list[Path] = []
        for environment_name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            value = os.environ.get(environment_name, "").strip()
            if value:
                roots.append(Path(value))
        roots.extend((Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")))
        candidates: list[Path] = []
        explicit = os.environ.get("MOKE_LABONE_DOTNET_API", "").strip().strip('"')
        if explicit:
            configured = Path(explicit)
            candidates.append(configured if configured.suffix.lower() == '.dll' else configured / api_filename)
        runtime_directories = {Path(_PROGRAM_FILE).resolve().parent, Path(sys.executable).resolve().parent}
        candidates.extend(directory / api_filename for directory in runtime_directories)
        for root in roots:
            candidates.extend(
                (
                    root / "Zurich Instruments/LabOne/API/DotNet" / api_filename,
                    root / "Zurich Instruments/LabOne/API/DotNET" / api_filename,
                    root / "Zurich Instruments/LabOne/API/Dotnet" / api_filename,
                )
            )
        checked: list[str] = []
        seen: set[str] = set()
        problems: list[str] = []
        for candidate in candidates:
            key = str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            checked.append(str(candidate))
            try:
                if candidate.is_file():
                    core_dll = candidate.with_name(f'ziDotNETCore-{suffix}.dll')
                    if not core_dll.is_file():
                        problems.append(f'found {candidate}, but {core_dll.name} is missing from the same directory')
                        continue
                    return candidate.parent, candidate
            except OSError:
                continue
        raise RuntimeError(
            "No matching-version .NET API bundled with LabOne was found. Please confirm that the full "
            "LabOne (including API/DotNet) is installed. "
            + ("".join(f"{problem[:1].upper()}{problem[1:]}. " for problem in problems))
            + "Checked: " + "; ".join(checked)
        )
    def _close_dll_directory(self) -> None:
        handle = self._dll_directory_handle
        self._dll_directory_handle = None
        if handle is not None:
            try:
                handle.close()
            except BaseException:
                pass
    def setInt(self, path: str, value: int) -> None:
        self._daq.setInt(str(path), int(value))
    def getInt(self, path: str) -> int:
        return int(self._daq.getInt(str(path)))
    def setDouble(self, path: str, value: float) -> None:
        self._daq.setDouble(str(path), float(value))
    def getDouble(self, path: str) -> float:
        return float(self._daq.getDouble(str(path)))
    def sync(self) -> None:
        self._daq.sync()
    def getSample(self, path: str) -> dict[str, float]:
        sample = self._daq.getDemodSample(str(path))
        return {'x': float(sample.x), 'y': float(sample.y), 'frequency': float(sample.frequency), 'phase': float(sample.phase)}
    def getString(self, path: str) -> str:
        return str(self._daq.getString(str(path)))
    def connectDevice(self, device: str, interface: str) -> None:
        self._daq.connectDevice(str(device), str(interface), "")
    def disconnect(self) -> None:
        try:
            self._daq.disconnect()
        finally:
            self._close_dll_directory()

class LabOneCAPIAdapter:
    class _DemodSample(ctypes.Structure):
        _fields_ = (
            ("timeStamp", ctypes.c_uint64),
            ("x", ctypes.c_double),
            ("y", ctypes.c_double),
            ("frequency", ctypes.c_double),
            ("phase", ctypes.c_double),
            ("dioBits", ctypes.c_uint32),
            ("trigger", ctypes.c_uint32),
            ("auxIn0", ctypes.c_double),
            ("auxIn1", ctypes.c_double),
        )
    def __init__(self, host: str, port: int, api_level: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The LabOne C API can only be loaded on the Windows control PC")
        self.api_dll = self._find_c_api()
        self._dll_directory_handle = None
        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(str(self.api_dll.parent))
        try:
            self._lib = ctypes.WinDLL(str(self.api_dll))
            self._bind_functions()
            self._connection = ctypes.c_void_p()
            self._check(self._lib.ziAPIInit(ctypes.byref(self._connection)), 'ziAPIInit')
            self._check(self._lib.ziAPIConnectEx(self._connection, str(host).encode('ascii'), int(port), int(api_level), b'allowVersionMismatch=1'), f'ziAPIConnectEx({host}:{port})')
        except BaseException:
            self._destroy()
            raise
    def _bind_functions(self) -> None:
        connection = ctypes.c_void_p
        result = ctypes.c_int
        self._lib.ziAPIInit.argtypes = [ctypes.POINTER(connection)]
        self._lib.ziAPIInit.restype = result
        self._lib.ziAPIConnectEx.argtypes = [connection, ctypes.c_char_p, ctypes.c_uint16, ctypes.c_int, ctypes.c_char_p]
        self._lib.ziAPIConnectEx.restype = result
        self._lib.ziAPIDisconnect.argtypes = [connection]
        self._lib.ziAPIDisconnect.restype = result
        self._lib.ziAPIDestroy.argtypes = [connection]
        self._lib.ziAPIDestroy.restype = result
        self._lib.ziAPIConnectDevice.argtypes = [connection, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        self._lib.ziAPIConnectDevice.restype = result
        self._lib.ziAPISetValueI.argtypes = [connection, ctypes.c_char_p, ctypes.c_int64]
        self._lib.ziAPISetValueI.restype = result
        self._lib.ziAPIGetValueI.argtypes = [connection, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
        self._lib.ziAPIGetValueI.restype = result
        self._lib.ziAPISetValueD.argtypes = [connection, ctypes.c_char_p, ctypes.c_double]
        self._lib.ziAPISetValueD.restype = result
        self._lib.ziAPIGetValueD.argtypes = [connection, ctypes.c_char_p, ctypes.POINTER(ctypes.c_double)]
        self._lib.ziAPIGetValueD.restype = result
        self._lib.ziAPISync.argtypes = [connection]
        self._lib.ziAPISync.restype = result
        self._lib.ziAPIGetDemodSample.argtypes = [connection, ctypes.c_char_p, ctypes.POINTER(self._DemodSample)]
        self._lib.ziAPIGetDemodSample.restype = result
    @staticmethod
    def _candidate_labview_roots() -> list[Path]:
        roots = []
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            ni_root = Path(base) / "National Instruments"
            try:
                roots.extend((item / 'instr.lib/Zurich Instruments LabOne' for item in ni_root.glob('LabVIEW*') if item.is_dir()))
            except OSError:
                continue
        return roots
    @classmethod
    def _find_c_api(cls) -> Path:
        suffix = "win64" if sys.maxsize > 2**32 else "win32"
        filename = f"ziAPI-{suffix}.dll"
        explicit = os.environ.get("MOKE_LABONE_C_API", "").strip().strip('"')
        candidates: list[Path] = []
        if explicit:
            configured = Path(explicit)
            candidates.append(configured if configured.suffix.lower() == '.dll' else configured / filename)
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            labone = Path(base) / "Zurich Instruments/LabOne"
            candidates.extend((labone / 'API/C/lib' / filename, labone / 'API/C/bin' / filename, labone / filename))
            try:
                candidates.extend(labone.rglob(filename))
            except OSError:
                pass
        runtime_directories = {Path(_PROGRAM_FILE).resolve().parent, Path(sys.executable).resolve().parent}
        candidates.extend(directory / filename for directory in runtime_directories)
        for root in cls._candidate_labview_roots():
            try:
                candidates.extend(root.rglob(filename))
            except OSError:
                continue
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            ni_root = Path(base) / "National Instruments"
            try:
                candidates.extend(ni_root.rglob(filename))
            except OSError:
                continue
        checked: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            checked.append(str(candidate))
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        raise RuntimeError(
            f"No {filename} with a matching bitness was found. Please install the LabOne C API, or point the environment variable "
            f"MOKE_LABONE_C_API to that DLL. Checked: {'; '.join(checked)}"
        )
    @staticmethod
    def _path(path: str) -> bytes:
        return str(path).encode("ascii")
    @staticmethod
    def _check(code: int, operation: str) -> None:
        if int(code) != 0:
            raise RuntimeError(f"{operation} failed, LabOne C API code={int(code)}")
    def setInt(self, path: str, value: int) -> None:
        self._check(self._lib.ziAPISetValueI(self._connection, self._path(path), int(value)), f'setInt({path})')
    def getInt(self, path: str) -> int:
        value = ctypes.c_int64()
        self._check(self._lib.ziAPIGetValueI(self._connection, self._path(path), ctypes.byref(value)), f'getInt({path})')
        return int(value.value)
    def setDouble(self, path: str, value: float) -> None:
        self._check(self._lib.ziAPISetValueD(self._connection, self._path(path), float(value)), f'setDouble({path})')
    def getDouble(self, path: str) -> float:
        value = ctypes.c_double()
        self._check(self._lib.ziAPIGetValueD(self._connection, self._path(path), ctypes.byref(value)), f'getDouble({path})')
        return float(value.value)
    def sync(self) -> None:
        self._check(self._lib.ziAPISync(self._connection), "ziAPISync")
    def getSample(self, path: str) -> dict[str, float]:
        sample = self._DemodSample()
        self._check(self._lib.ziAPIGetDemodSample(self._connection, self._path(path), ctypes.byref(sample)), f'getDemodSample({path})')
        return {'x': float(sample.x), 'y': float(sample.y), 'frequency': float(sample.frequency), 'phase': float(sample.phase)}
    def getString(self, path: str) -> str:
        return ""
    def connectDevice(self, device: str, interface: str) -> None:
        self._check(self._lib.ziAPIConnectDevice(self._connection, str(device).encode('ascii'), str(interface).encode('ascii'), b''), f'connectDevice({device}, {interface})')
    def _destroy(self) -> None:
        connection = getattr(self, "_connection", None)
        library = getattr(self, "_lib", None)
        if connection is not None and connection.value and library is not None:
            try:
                library.ziAPIDestroy(connection)
            except BaseException:
                pass
            connection.value = None
        handle = getattr(self, "_dll_directory_handle", None)
        self._dll_directory_handle = None
        if handle is not None:
            try:
                handle.close()
            except BaseException:
                pass
    def disconnect(self) -> None:
        try:
            if self._connection.value:
                self._lib.ziAPIDisconnect(self._connection)
        finally:
            self._destroy()

_MFLI_BRIDGE_PREFIX = "MOKE_MFLI_JSON:"

_MFLI_BRIDGE_SOURCE = r'''
from __future__ import print_function

import json
import math
import sys
import time
import traceback

PREFIX = "MOKE_MFLI_JSON:"
DEVICE_REQUEST = sys.argv[1]
API_LEVEL = int(sys.argv[2])
DEMOD = int(sys.argv[3])
EXTREF_DEMOD = 1
EXTREF = 0
# The id of the request being answered, echoed in its reply, so a reply that
# arrives after its request timed out is never taken for the next one's.
REQUEST_ID = None


def emit(payload):
    if REQUEST_ID is not None and "id" not in payload:
        payload["id"] = REQUEST_ID
    sys.stdout.write(PREFIX + json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def scalar(sample, field, required=True):
    try:
        value = sample[field]
    except (KeyError, TypeError):
        if required:
            raise RuntimeError("MFLI sample missing field: " + field)
        return None
    try:
        value = value[-1]
    except (IndexError, KeyError, TypeError):
        pass
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError("MFLI sample has non-finite field: " + field)
    return result


def input_state(reset_extrema=False):
    range_node = "/%s/sigins/0/range" % device
    max_node = "/%s/sigins/0/max" % device
    min_node = "/%s/sigins/0/min" % device
    result = {"input_range_v": float(daq.getDouble(range_node))}
    try:
        result["input_max_normalized"] = float(daq.getDouble(max_node))
        result["input_min_normalized"] = float(daq.getDouble(min_node))
        if reset_extrema:
            daq.setDouble(max_node, 0.0)
            daq.setDouble(min_node, 0.0)
    except Exception:
        result["input_max_normalized"] = None
        result["input_min_normalized"] = None
    return result


def run_autorange(timeout_s=8.0):
    old_range = float(daq.getDouble("/%s/sigins/0/range" % device))
    autorange_node = "/%s/sigins/0/autorange" % device
    daq.setInt(autorange_node, 1)
    # As zhinst.utils.sigin_autorange: the set must take effect before the
    # poll, or a stale 0 ends it at once.
    daq.sync()
    deadline = time.monotonic() + float(timeout_s)
    while int(daq.getInt(autorange_node)) != 0:
        if time.monotonic() >= deadline:
            raise RuntimeError("MFLI Signal Input 1 autorange timed out")
        time.sleep(0.02)
    daq.sync()
    result = input_state(reset_extrema=True)
    result["old_input_range_v"] = old_range
    return result


daq = None
device = ""
configured_extref_demod = DEMOD
try:
    import zhinst.utils as zi_utils

    session = zi_utils.create_api_session(DEVICE_REQUEST, API_LEVEL)
    if not isinstance(session, tuple) or len(session) < 3:
        raise RuntimeError("Unexpected create_api_session result: %r" % (session,))
    daq, discovered, props = session[:3]
    device = str(discovered).strip().lower()
    if not device:
        raise RuntimeError("LabOne Discovery returned an empty device name")
    try:
        zi_utils.api_server_version_check(daq)
    except AttributeError:
        pass
    enable_node = "/%s/demods/%d/enable" % (device, DEMOD)
    daq.setInt(enable_node, 1)
    daq.sync()
    daq.getSample("/%s/demods/%d/sample" % (device, DEMOD))
    version = ""
    options = ""
    if isinstance(props, dict):
        version = str(props.get("serverversion") or props.get("version") or "")
        raw_options = props.get("options") or ""
        if isinstance(raw_options, (tuple, list)):
            options = ",".join(str(item) for item in raw_options)
        else:
            options = str(raw_options)
    if not options:
        try:
            options = str(daq.getString("/%s/features/options" % device))
        except Exception:
            pass
    try:
        daq.getInt("/%s/demods/1/enable" % device)
        has_demod_2 = True
    except Exception:
        has_demod_2 = False
    try:
        daq.getInt("/%s/demods/3/enable" % device)
        daq.getInt("/%s/extrefs/1/demodselect" % device)
        has_demod_4 = True
    except Exception:
        has_demod_4 = False
    identity = "Zurich Instruments MFLI - " + device
    if version:
        identity += " - LabOne " + version
    emit({"ok": True, "event": "ready", "device": device,
          "version": version, "options": options,
          "has_demod_2": has_demod_2, "has_demod_4": has_demod_4, "identity": identity})
except BaseException:
    emit({"ok": False, "event": "ready", "error": traceback.format_exc()})
    try:
        if daq is not None:
            daq.disconnect()
    except BaseException:
        pass
    sys.exit(2)

for request_line in sys.stdin:
    REQUEST_ID = None
    try:
        request = json.loads(request_line)
        REQUEST_ID = request.get("id")
        command = request.get("command")
        if command in ("configure_second_harmonic", "configure_third_harmonic"):
            harmonic = 2 if command == "configure_second_harmonic" else 3
            measurement_demod = harmonic - 1 if configured_extref_demod == 3 else harmonic
            base = "/%s/demods/%d/" % (device, measurement_demod)
            if configured_extref_demod not in (1, 3) or measurement_demod == configured_extref_demod:
                raise RuntimeError("Measurement demodulator conflicts with external reference")
            for name, value in (("adcselect", 0), ("oscselect", 0), ("harmonic", harmonic),
                                ("order", int(request["order"])), ("enable", 1)):
                daq.setInt(base + name, value)
            for name in ("timeconstant", "rate"):
                daq.setDouble(base + name, float(request[name]))
            daq.sync()
            for name, value in (("adcselect", 0), ("oscselect", 0), ("harmonic", harmonic), ("enable", 1)):
                if daq.getInt(base + name) != value:
                    raise RuntimeError("Additional harmonic readback mismatch: " + name)
            emit({"ok": True})
        elif command == "disable_unused_demods":
            for index in request["indices"]:
                if index not in (1, 2, 3) or index == configured_extref_demod:
                    raise RuntimeError("Cannot disable active reference demodulator")
                node = "/%s/demods/%d/enable" % (device, index)
                daq.setInt(node, 0)
                daq.sync()
                if daq.getInt(node) != 0:
                    raise RuntimeError("Unused demodulator disable readback mismatch")
            emit({"ok": True})
        elif command == "configure":
            requested_ac = 1 if str(request["coupling"]).upper() == "AC" else 0
            requested_imp50 = int(request.get("input_imp50", 0))
            requested_scaling = float(request.get("input_scaling", 1.0))
            requested_harmonic = int(request["harmonic"])
            requested_reference_input = int(request["reference_adcselect"])
            requested_timeconstant = float(request["timeconstant_s"])
            requested_order = int(request["filter_order"])
            requested_rate = float(request["sample_rate_hz"])
            extref_demod = int(request.get("extref_demod", EXTREF_DEMOD))
            if extref_demod == 3:
                routes = [i for i in range(2)
                          if int(daq.getInt("/%s/extrefs/%d/demodselect" % (device, i))) == 3]
                if len(routes) != 1:
                    raise RuntimeError("Cannot identify external reference controller for Demod 4")
                EXTREF = routes[0]
            else:
                EXTREF = 0
            for other in range(2):
                # Only the chosen reference PLL: another left on (an earlier
                # session) would drive oscillator 0 as well.
                if other != EXTREF:
                    try:
                        daq.setInt("/%s/extrefs/%d/enable" % (device, other), 0)
                    except Exception:
                        pass
            configured_extref_demod = extref_demod
            ac_node = "/%s/sigins/0/ac" % device
            imp50_node = "/%s/sigins/0/imp50" % device
            scaling_node = "/%s/sigins/0/scaling" % device
            harmonic_node = "/%s/demods/%d/harmonic" % (device, DEMOD)
            enable_node = "/%s/demods/%d/enable" % (device, DEMOD)
            osc_node = "/%s/demods/%d/oscselect" % (device, DEMOD)
            extref_demod_node = "/%s/extrefs/%d/demodselect" % (device, EXTREF)
            extref_auto_node = "/%s/extrefs/%d/automode" % (device, EXTREF)
            extref_enable_node = "/%s/extrefs/%d/enable" % (device, EXTREF)
            extref_adc_node = "/%s/extrefs/%d/adcselect" % (device, EXTREF)
            measurement_adc_node = "/%s/demods/%d/adcselect" % (device, DEMOD)
            timeconstant_node = "/%s/demods/%d/timeconstant" % (device, DEMOD)
            order_node = "/%s/demods/%d/order" % (device, DEMOD)
            rate_node = "/%s/demods/%d/rate" % (device, DEMOD)
            daq.setInt(ac_node, requested_ac)
            daq.setInt(imp50_node, requested_imp50)
            daq.setDouble(scaling_node, requested_scaling)
            ext_demod_node = "/%s/demods/%d/adcselect" % (device, extref_demod)
            ext_osc_node = "/%s/demods/%d/oscselect" % (device, extref_demod)
            ext_harmonic_node = "/%s/demods/%d/harmonic" % (device, extref_demod)
            ext_demod_enable_node = "/%s/demods/%d/enable" % (device, extref_demod)
            daq.setInt(ext_demod_node, requested_reference_input)
            daq.setInt(ext_osc_node, 0)
            if extref_demod != DEMOD:
                daq.setInt(ext_harmonic_node, 1)
            daq.setInt(ext_demod_enable_node, 1)
            selected_extref_input = int(daq.getInt(ext_demod_node))
            try:
                daq.setInt(extref_demod_node, extref_demod)
            except Exception:
                pass
            # automode/demodselect/locked were exposed as dedicated nodes in
            # newer LabOne releases.  LabOne 18.12 can still enable ExtRef,
            # but does not necessarily expose all three nodes to the API.
            try:
                daq.setInt(extref_auto_node, 4)
            except Exception:
                pass
            daq.setInt(extref_enable_node, 1)
            if extref_demod == 3:
                daq.sync()
                if daq.getInt(extref_demod_node) != 3:
                    raise RuntimeError("External reference Demod 4 readback mismatch")
            # A base MFLI temporarily uses Demod 1 to select/latch the chosen
            # external reference, then returns its measurement input to
            # Signal In 1.
            if extref_demod == DEMOD:
                daq.sync()
                if selected_extref_input != requested_reference_input:
                    raise RuntimeError(
                        "Requested external reference was not selected: "
                        "%d != %d" %
                        (selected_extref_input, requested_reference_input)
                    )
                daq.setInt(measurement_adc_node, 0)
            daq.setInt(osc_node, 0)
            daq.setInt(harmonic_node, requested_harmonic)
            daq.setDouble(timeconstant_node, requested_timeconstant)
            daq.setInt(order_node, requested_order)
            daq.setDouble(rate_node, requested_rate)
            daq.setInt(enable_node, 1)
            daq.sync()
            if extref_demod != DEMOD:
                try:
                    selected_extref_input = int(daq.getInt(extref_adc_node))
                except Exception:
                    pass
            configured_input = input_state(reset_extrema=True)
            emit({"ok": True, "extref_channel": EXTREF, "actual_ac": int(daq.getInt(ac_node)),
                  "actual_imp50": int(daq.getInt(imp50_node)),
                  "actual_scaling": float(daq.getDouble(scaling_node)),
                  "actual_harmonic": int(daq.getInt(harmonic_node)),
                  "actual_extref_harmonic": (
                      int(daq.getInt(ext_harmonic_node))
                      if extref_demod != DEMOD else 1),
                  "extref_enabled": int(daq.getInt(extref_enable_node)),
                  "extref_adcselect": selected_extref_input,
                  "timeconstant_s": float(daq.getDouble(timeconstant_node)),
                  "filter_order": int(daq.getInt(order_node)),
                  "sample_rate_hz": float(daq.getDouble(rate_node)),
                  "input_range_v": configured_input["input_range_v"]})
        elif command == "read":
            sample = daq.getSample("/%s/demods/%d/sample" % (device, DEMOD))
            payload = {"x": scalar(sample, "x"), "y": scalar(sample, "y")}
            frequency = scalar(sample, "frequency", required=False)
            flags = scalar(sample, "flags", required=False)
            if frequency is not None:
                payload["frequency"] = frequency
            if flags is not None:
                payload["flags"] = flags
            try:
                payload["extref_locked"] = int(daq.getInt(
                    "/%s/extrefs/%d/locked" % (device, EXTREF)))
            except Exception:
                payload["extref_locked"] = None
            payload.update(input_state(reset_extrema=True))
            try:
                payload["device_status_flags"] = int(daq.getInt(
                    "/%s/status/flags/binary" % device))
            except Exception:
                payload["device_status_flags"] = None
            emit({"ok": True, "sample": payload})
        elif command == "read_demods":
            channels = []
            for index in range(int(request.get("count", 4))):
                node = "/%s/demods/%d" % (device, index)
                enabled = int(daq.getInt(node + "/enable"))
                if not enabled:
                    channels.append({"enabled": 0})
                    continue
                raw = daq.getSample(node + "/sample")
                channels.append({"x": scalar(raw, "x"), "y": scalar(raw, "y"),
                    "frequency": scalar(raw, "frequency"),
                    "timestamp": scalar(raw, "timestamp", required=False),
                    "harmonic": int(daq.getInt(node + "/harmonic")),
                    "adcselect": int(daq.getInt(node + "/adcselect")), "enabled": enabled})
            emit({"ok": True, "demods": channels})
        elif command == "autorange":
            emit({"ok": True, "autorange": run_autorange()})
        elif command == "close":
            try:
                daq.setInt("/%s/extrefs/%d/enable" % (device, EXTREF), 0)
                daq.setInt("/%s/demods/%d/enable" % (device, DEMOD), 0)
                if configured_extref_demod != DEMOD:
                    daq.setInt("/%s/demods/%d/enable" %
                               (device, configured_extref_demod), 0)
                daq.sync()
            finally:
                daq.disconnect()
            emit({"ok": True})
            break
        else:
            raise ValueError("Unknown bridge command: %r" % (command,))
    except BaseException:
        emit({"ok": False, "error": traceback.format_exc()})
'''

def _find_mfli_legacy_python() -> Path:
    configured = os.environ.get(NONLOCAL_MFLI_LEGACY_PYTHON_ENV, "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured.strip('"')))
    system_drive = os.environ.get("SystemDrive", "C:")
    drive_root = Path(system_drive + os.sep if system_drive.endswith(':') else system_drive)
    user_profile = os.environ.get("USERPROFILE", "")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / 'NBB/MOKE_MFLI_PY37/python.exe')
    candidates.append(PROGRAM_DIRECTORY / '.mfli_py37/python.exe')
    candidates.extend((drive_root / 'ProgramData/miniconda3/envs/GMS_VENV_PYTHON/python.exe', drive_root / 'ProgramData/anaconda3/envs/GMS_VENV_PYTHON/python.exe'))
    if user_profile:
        candidates.extend((Path(user_profile) / 'miniconda3/envs/GMS_VENV_PYTHON/python.exe', Path(user_profile) / 'anaconda3/envs/GMS_VENV_PYTHON/python.exe'))
    rejected = []
    validation_code = (
        "import sys,pkg_resources,numpy,zhinst,zhinst.utils;"
        "v=pkg_resources.get_distribution('zhinst').version;"
        "assert sys.version_info[:2]==(3,7),sys.version;"
        "assert v=='18.12.59443',v;"
        "assert numpy.__version__=='1.21.6',numpy.__version__;print(v)"
    )
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            check = subprocess.run(
                [str(candidate), "-c", validation_code],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if check.returncode == 0:
                return candidate
            detail = (check.stderr or check.stdout or "verification failed").strip()
            rejected.append(f"{candidate} ({short_status_error(detail, 160)})")
        except (OSError, subprocess.SubprocessError) as error:
            rejected.append(f"{candidate} ({short_status_error(error, 160)})")
            continue
    searched = "; ".join(str(path) for path in candidates)
    rejected_text = "; ".join(rejected) if rejected else "none"
    raise RuntimeError(
        "No verified Python 3.7 + zhinst 18.12.59443 environment was found. Please run "
        r"INSTALL_MFLI_LABONE_18_12.bat first, or confirm that "
        r"C:\ProgramData\miniconda3\envs\GMS_VENV_PYTHON\python.exe "
        f"exists, or set the environment variable {NONLOCAL_MFLI_LEGACY_PYTHON_ENV}."
        f"Checked: {searched}. Rejected environments: {rejected_text}"
    )

class LegacyMFLIBridge:
    def __init__(self, device_id: str, timeout_s: float = 20.0) -> None:
        self.python_path = _find_mfli_legacy_python()
        self.process = None
        self.responses: queue.Queue = queue.Queue()
        self.diagnostics: list[str] = []
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [str(self.python_path), '-u', '-c', _MFLI_BRIDGE_SOURCE, str(device_id), str(NONLOCAL_MFLI_API_LEVEL), str(NONLOCAL_MFLI_DEMODULATOR)]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            threading.Thread(target=self._read_stdout, name='MFLI-bridge-stdout', daemon=True).start()
            threading.Thread(target=self._read_stderr, name='MFLI-bridge-stderr', daemon=True).start()
            ready = self._receive(timeout_s)
            if ready.get("event") != "ready":
                raise RuntimeError(f"MFLI bridge start reply is abnormal: {ready!r}")
            self.device = str(ready.get("device", device_id)).strip().lower()
            self.version = str(ready.get("version", ""))
            self.options = str(ready.get("options", ""))
            self.has_demod_2 = bool(ready.get("has_demod_2", False))
            self.has_demod_4 = bool(ready.get("has_demod_4", False))
            self.identity = str(ready.get("identity", self.device))
        except BaseException:
            self.close(force=True)
            raise
    def _append_diagnostic(self, line: str) -> None:
        line = str(line).strip()
        if not line:
            return
        self.diagnostics.append(line)
        del self.diagnostics[:-12]
    def _read_stdout(self) -> None:
        stream = self.process.stdout if self.process is not None else None
        if stream is None:
            self.responses.put({"_eof": True})
            return
        try:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                if line.startswith(_MFLI_BRIDGE_PREFIX):
                    try:
                        self.responses.put(json.loads(line[len(_MFLI_BRIDGE_PREFIX):]))
                    except BaseException as error:
                        self._append_diagnostic(f"JSON parse failed: {error}: {line}")
                else:
                    self._append_diagnostic(line)
        finally:
            self.responses.put({"_eof": True})
    def _read_stderr(self) -> None:
        stream = self.process.stderr if self.process is not None else None
        if stream is None:
            return
        for line in stream:
            self._append_diagnostic(line)
    def _diagnostic_suffix(self) -> str:
        if not self.diagnostics:
            return ""
        return "; legacy environment output: " + " | ".join(self.diagnostics[-5:])
    def _receive(self, timeout_s: float, request_id: int | None = None) -> dict[str, object]:
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        while True:
            try:
                reply = self.responses.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty as error:
                raise RuntimeError(f'The legacy LabOne bridge did not reply within {timeout_s:g} s' + self._diagnostic_suffix()) from error
            if request_id is None or reply.get('_eof') or reply.get('id') in (None, request_id):
                break
            self._append_diagnostic(f"dropped a late reply to request {reply.get('id')}")
        if reply.get("_eof"):
            exit_code = self.process.poll() if self.process is not None else None
            raise RuntimeError(f'The legacy LabOne bridge exited unexpectedly (code={exit_code})' + self._diagnostic_suffix())
        if not reply.get("ok"):
            detail = str(reply.get("error", "unknown error")).strip()
            raise RuntimeError('Legacy LabOne bridge failed: ' + detail + self._diagnostic_suffix())
        return reply
    def request(self, command: str, timeout_s: float=10.0, **parameters) -> dict[str, object]:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError('Legacy LabOne bridge is not running' + self._diagnostic_suffix())
        if self.process.stdin is None:
            raise RuntimeError("Legacy LabOne bridge stdin is unavailable")
        self.request_id = getattr(self, "request_id", 0) + 1
        payload = {"command": str(command), "id": self.request_id}
        payload.update(parameters)
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError('Cannot send a command to the legacy LabOne bridge' + self._diagnostic_suffix()) from error
        return self._receive(timeout_s, self.request_id)
    def close(self, force: bool = False) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None and not force:
            try:
                self.request("close", timeout_s=5.0)
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    force = True
            except BaseException:
                force = True
        if process.poll() is None and force:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except BaseException:
                try:
                    process.kill()
                except BaseException:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except BaseException:
                pass
        self.process = None

class ZurichMFLIController:
    CLEAR_ON_READ_QUERIES = ("LIAS?", "*ESR?", "ERRS?")
    MFLI_RANGE_FRESH_REASONS = ("initial", "STFMR frequency start")
    SR830_LIAS_UNREADABLE = 0x0F
    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.resource_manager = None
        self.source = None
        self.lockins: dict[str, object] = {}
        self.mfli_daq = None
        self.mfli_bridge: LegacyMFLIBridge | None = None
        self.mfli_device = ""
        self.mfli_props: dict[str, object] = {}
        self.mfli_config: dict[str, object] = {}
        self.nanovoltmeter = None
        self.source_identity = ""
        self.lockin_identities: dict[str, str] = {}
        self.mfli_identity = ""
        self.nanovoltmeter_identity = ""
        self.source_resource = ""
        self.lockin_resources: dict[str, str] = {}
        self.nanovoltmeter_resource = ""
        self.mfli_lease_resource = ""
        self.output_enabled = False
        self.source_output_mode = "AC"
        self.lock = threading.RLock()
        self.sensitivity_indices: dict[str, int] = {}
        self.low_range_counts: dict[str, int] = {}
        self.range_cooldowns: dict[str, int] = {}
        self.overload_floors: dict[str, int] = {}
        self.overload_floor_memory: dict[tuple[str, int], int] = {}
        self.lockin_harmonics: dict[str, int] = {}
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 0
        self.mfli_low_floor_v = 0.0
        self.mfli_stepped_down = False
        self.mfli_low_readings_needed = LOCKIN_DOWNRANGE_READINGS
        self.mfli_initial_autorange_pending = False
        self.mfli_auto_range_enabled = True
        self.mfli_in_scan = False
        self.lias_clear_pending: dict[str, bool] = {}
    @property
    def connected(self) -> bool:
        return self.mfli_connected

    @property
    def mfli_connected(self) -> bool:
        return self.mfli_daq is not None or self.mfli_bridge is not None

    def _close_manager_if_unused(self) -> None:
        if self.source is not None or self.lockins or self.nanovoltmeter is not None or (self.resource_manager is None):
            return
        manager = self.resource_manager
        self.resource_manager = None
        release_shared_visa_manager(manager)
    @staticmethod
    def _mfli_reachable_servers(timeout_s: float=1.2, candidates=None) -> tuple[list[tuple[str, int]], list[str]]:
        reachable = []
        diagnostics = []
        for host, port in (NONLOCAL_MFLI_SERVER_CANDIDATES if candidates is None else candidates):
            try:
                with socket.create_connection((host, int(port)), timeout_s):
                    reachable.append((host, int(port)))
                    diagnostics.append(f"{host}:{port}=TCP reachable")
            except OSError as error:
                diagnostics.append(f"{host}:{port}=unreachable ({error})")
        return reachable, diagnostics
    def configure_second_harmonic(self, tau, rate=NONLOCAL_SAMPLE_HZ):
        with self.lock:
            if self.mfli_config.get('extref_demodulator') not in (1, 3) or self.mfli_config.get('harmonic') != 1:
                raise RuntimeError('Simultaneous measurement requires MF-MD: Demod 1 measures 1st harmonic, Demod 2 external reference, Demod 3 measures 2nd harmonic')
            if self.mfli_bridge is not None:
                self.mfli_bridge.request('configure_second_harmonic', timeconstant=tau, rate=rate, order=NONLOCAL_LOCKIN_FILTER_ORDER)
                return
            index = 1 if self.mfli_config['extref_demodulator'] == 3 else 2
            base = f'/{self.mfli_device}/demods/{index}/'
            for name, value in (('adcselect', 0), ('oscselect', 0), ('harmonic', 2), ('order', NONLOCAL_LOCKIN_FILTER_ORDER), ('enable', 1)):
                self.mfli_daq.setInt(base + name, value)
            self.mfli_daq.setDouble(base + 'timeconstant', tau)
            self.mfli_daq.setDouble(base + 'rate', rate)
            self.mfli_daq.sync()
            for name, value in (('adcselect', 0), ('oscselect', 0), ('harmonic', 2), ('enable', 1)):
                if self.mfli_daq.getInt(base + name) != value:
                    raise RuntimeError('Zurich 2nd-harmonic configuration readback mismatch: ' + name)
    def configure_third_harmonic(self, tau, rate=NONLOCAL_SAMPLE_HZ):
        with self.lock:
            if self.mfli_config.get('extref_demodulator') not in (1, 3) or self.mfli_config.get('harmonic') != 1:
                raise RuntimeError('Simultaneous measurement requires MF-MD: Demod 1 measures 1st harmonic, Demod 2 external reference, Demod 4 measures 3rd harmonic')
            if self.mfli_bridge is not None:
                self.mfli_bridge.request('configure_third_harmonic', timeconstant=tau, rate=rate, order=NONLOCAL_LOCKIN_FILTER_ORDER)
                return
            index = 2 if self.mfli_config['extref_demodulator'] == 3 else 3
            base = f'/{self.mfli_device}/demods/{index}/'
            for name, value in (('adcselect', 0), ('oscselect', 0), ('harmonic', 3), ('order', NONLOCAL_LOCKIN_FILTER_ORDER), ('enable', 1)):
                self.mfli_daq.setInt(base + name, value)
            self.mfli_daq.setDouble(base + 'timeconstant', tau)
            self.mfli_daq.setDouble(base + 'rate', rate)
            self.mfli_daq.sync()
            for name, value in (('adcselect', 0), ('oscselect', 0), ('harmonic', 3), ('enable', 1)):
                if self.mfli_daq.getInt(base + name) != value:
                    raise RuntimeError('Zurich 3rd-harmonic configuration readback mismatch: ' + name)
    def disable_unused_ppms_demods(self, mode):
        extref = self.mfli_config.get('extref_demodulator')
        measuring = (0, 1, 2) if extref == 3 else (0, 2, 3) if extref == 1 and self.mfli_has_multi_demod() else (0,)
        used = len(PPMS_ZURICH_MODE_ORDERS.get(str(mode), (1,)))
        indices = tuple(measuring[used:])
        with self.lock:
            if self.mfli_bridge is not None:
                if indices:
                    self.mfli_bridge.request('disable_unused_demods', indices=list(indices))
                return
            for index in indices:
                if index == self.mfli_config.get('extref_demodulator'):
                    raise RuntimeError('Cannot disable the external-reference demodulator that is in use')
                node = f'/{self.mfli_device}/demods/{index}/enable'
                self.mfli_daq.setInt(node, 0)
                self.mfli_daq.sync()
                if self.mfli_daq.getInt(node) != 0:
                    raise RuntimeError('Unused demodulator disable readback mismatch')
    def connect_mfli(self, device_id: str) -> str:
        requested = str(device_id).strip().lower()
        if not requested:
            raise ValueError("Please enter the Zurich MFLI API device name, e.g. dev4435")
        if requested.startswith("mf-"):
            requested = requested[3:]
        with self.lock:
            if self.mfli_connected:
                return self.mfli_identity
            direct_errors: list[str] = []
            server_reports: dict[tuple[str, int], str] = {}
            server_override = getattr(self, 'mfli_server_candidates', None)
            reachable_servers, tcp_diagnostics = self._mfli_reachable_servers(candidates=server_override) if server_override is not None else self._mfli_reachable_servers()
            self.logger("MFLI network pre-check: " + "; ".join(tcp_diagnostics))
            if not reachable_servers:
                diagnostic_text = "; ".join(tcp_diagnostics)
                if server_override is not None:
                    checks = '\n'.join((f'Test-NetConnection {host} -Port {port}' for host, port in server_override))
                    raise RuntimeError(
                        f'Zurich {requested}: configured Data Server is unreachable; '
                        'the LabOne API handshake has not started.\n\n'
                        f'Quick check: {diagnostic_text}\n\n'
                        'Verify the device address in MF Device Finder, its power and Ethernet/USB connection. '
                        'Run on the measurement PC:\n' + checks + '\n\n'
                        'TCP reachability is required but does not prove API compatibility. '
                        'Do not change the PC IP based on settings for another instrument.\n\n'
                        + zurich_choice_hint(requested)
                    )
                raise RuntimeError(
                    "The Zurich MFLI Data Server is currently unreachable; the program has not entered the "
                    "LabOne API handshake.\n\n"
                    f"Quick check: {diagnostic_text}\n\n"
                    "Please check in order:\n"
                    "1. The MFLI is powered on and connected to the PC via the rear-panel USB Device port;\n"
                    "2. The Windows USB/RNDIS network adapter is enabled and the PC address should be "
                    "192.168.69.77;\n"
                    "3. Run in PowerShell: ping 192.168.69.78;\n"
                    "4. Run: Test-NetConnection 192.168.69.78 -Port 8004.\n\n"
                    "Only after TcpTestSucceeded=True can the Python API connect.\n\n"
                    + zurich_choice_hint(requested)
                )
            mfli_lease_resource = f"LABONE::{requested}"
            acquire_instrument_lease(mfli_lease_resource, self, 'Non-local Zurich MFLI')
            self.mfli_lease_resource = mfli_lease_resource
            if sys.platform == "win32":
                native_adapters = (('LabOne C API', LabOneCAPIAdapter, 'current-python-labone-c-api'), ('LabOne .NET', LabOneDotNetAdapter, 'current-python-labone-dotnet'))
                for adapter_label, adapter_class, connection_name in native_adapters:
                    missing = [name for name in NONLOCAL_MFLI_DAQ_METHODS if not callable(getattr(adapter_class, name, None))]
                    if missing:
                        note = f"{adapter_label}: skipped, it has no {', '.join(missing)} (the MFLI set-up and reads need them)"
                        direct_errors.append(note)
                        self.logger(f"Zurich MFLI {note}")
                        continue
                    for server_host, server_port in reachable_servers:
                        daq = None
                        try:
                            daq = adapter_class(server_host, server_port, NONLOCAL_MFLI_API_LEVEL)
                            sample_node = f'/{requested}/demods/{NONLOCAL_MFLI_DEMODULATOR}/sample'
                            enable_node = f'/{requested}/demods/{NONLOCAL_MFLI_DEMODULATOR}/enable'
                            try:
                                daq.setInt(enable_node, 1)
                                daq.sync()
                                daq.getSample(sample_node)
                            except BaseException as initial_error:
                                attachment_errors = [f"attached read: {initial_error}"]
                                for interface in ("PCIe", "1GbE", "USB"):
                                    try:
                                        daq.connectDevice(requested, interface)
                                        daq.setInt(enable_node, 1)
                                        daq.sync()
                                        daq.getSample(sample_node)
                                        break
                                    except BaseException as attach_error:
                                        attachment_errors.append(f'{interface}: {attach_error}')
                                else:
                                    report = self._mfli_server_devices(daq)
                                    if report:
                                        server_reports[(server_host, server_port)] = report
                                    raise RuntimeError(f'Data Server connected, but {requested} cannot be read' + (f' (server sees {report})' if report else '') + ': ' + ' | '.join(attachment_errors))
                            version = ""
                            for version_node in ('/zi/about/version', '/zi/about/revision'):
                                try:
                                    version = str(daq.getString(version_node)).strip()
                                except BaseException:
                                    continue
                                if version:
                                    break
                            identity_parts = ["Zurich Instruments MFLI", requested]
                            if version:
                                identity_parts.append(f"LabOne {version}")
                            identity = " · ".join(identity_parts)
                            self.mfli_daq = daq
                            self.mfli_bridge = None
                            self.mfli_device = requested
                            self.mfli_props = {'serveraddress': server_host, 'serverport': server_port, 'serverversion': version, 'connection': connection_name, 'api_dll': str(daq.api_dll)}
                            self.mfli_config = {}
                            self.mfli_identity = identity
                            self.logger(f'Non-local Zurich MFLI loaded by the current Python {adapter_label}: {identity} @ {server_host}:{server_port}')
                            return identity
                        except BaseException as error:
                            direct_errors.append(f'{adapter_label} {server_host}:{server_port}: {error}')
                            if daq is not None:
                                try:
                                    daq.disconnect()
                                except BaseException:
                                    pass
                            self.logger(f'The current Python loaded {adapter_label} but connecting to {server_host}:{server_port} failed: {error}')
            if zi_core is not None:
                for server_host, server_port in reachable_servers:
                    daq = None
                    try:
                        daq = zi_core.ziDAQServer(server_host, server_port, NONLOCAL_MFLI_API_LEVEL, allow_version_mismatch=True)
                        sample_node = f'/{requested}/demods/{NONLOCAL_MFLI_DEMODULATOR}/sample'
                        try:
                            visible_nodes = daq.listNodes(f'/{requested}', recursive=False, absolute=True)
                        except TypeError:
                            visible_nodes = daq.listNodes(f"/{requested}", 0)
                        if not visible_nodes:
                            attachment_errors = []
                            for interface in ("PCIe", "1GbE", "USB"):
                                try:
                                    daq.connectDevice(requested, interface)
                                    break
                                except BaseException as attach_error:
                                    attachment_errors.append(f'{interface}: {attach_error}')
                            else:
                                report = self._mfli_server_devices(daq)
                                if report:
                                    server_reports[(server_host, server_port)] = report
                                raise RuntimeError(f'Data Server connected, but {requested} cannot be attached' + (f' (server sees {report})' if report else '') + ': ' + ' | '.join(attachment_errors))
                        daq.setInt(f'/{requested}/demods/{NONLOCAL_MFLI_DEMODULATOR}/enable', 1)
                        daq.sync()
                        daq.getSample(sample_node)
                        version = ""
                        for version_node in ('/zi/about/version', '/zi/about/revision'):
                            try:
                                version = str(daq.getString(version_node)).strip()
                            except BaseException:
                                continue
                            if version:
                                break
                        identity_parts = ["Zurich Instruments MFLI", requested]
                        if version:
                            identity_parts.append(f"LabOne {version}")
                        identity = " · ".join(identity_parts)
                        self.mfli_daq = daq
                        self.mfli_bridge = None
                        self.mfli_device = requested
                        self.mfli_props = {'serveraddress': server_host, 'serverport': server_port, 'serverversion': version, 'connection': 'current-python-core', 'allow_version_mismatch': True}
                        self.mfli_config = {}
                        self.mfli_identity = identity
                        self.logger(f'Non-local Zurich MFLI connected directly by the current Python Core API: {identity} @ {server_host}:{server_port}')
                        return identity
                    except BaseException as error:
                        direct_errors.append(f'Core {server_host}:{server_port}: {error}')
                        if daq is not None:
                            try:
                                daq.disconnect()
                            except BaseException:
                                pass
                        self.logger(f'The current Python Core API connection to {server_host}:{server_port} failed: {error}')
            bridge = None
            try:
                bridge = LegacyMFLIBridge(requested)
                self.mfli_daq = None
                self.mfli_bridge = bridge
                self.mfli_device = bridge.device
                self.mfli_props = {
                    "serverversion": bridge.version,
                    "connection": "labone-18.12-python37-bridge",
                    "python": str(bridge.python_path),
                    "options": bridge.options,
                    "has_demod_2": bridge.has_demod_2,
                    "has_demod_4": bridge.__dict__.get("has_demod_4", False),
                }
                self.mfli_config = {}
                self.mfli_identity = bridge.identity
                self.logger(f'Non-local Zurich MFLI connected through the legacy LabOne 18.12 environment: {bridge.identity}')
                return bridge.identity
            except BaseException as bridge_error:
                if bridge is not None:
                    bridge.close(force=True)
                direct_errors.append(f"18.12 Python 3.7 bridge: {bridge_error}")
                self.logger(f'The legacy LabOne 18.12 bridge is unavailable, continuing to try Discovery: {bridge_error}')
            if zi_core is None and zi_utils is not None and reachable_servers:
                daq = None
                try:
                    session = zi_utils.create_api_session(requested, NONLOCAL_MFLI_API_LEVEL)
                    if not isinstance(session, tuple) or len(session) < 3:
                        raise RuntimeError(f'create_api_session returned an unexpected format: {session!r}')
                    daq, discovered, props = session[:3]
                    device = str(discovered).strip().lower()
                    if not device:
                        raise RuntimeError("LabOne Discovery did not return an MFLI device name")
                    daq.setInt(f'/{device}/demods/{NONLOCAL_MFLI_DEMODULATOR}/enable', 1)
                    daq.sync()
                    daq.getSample(f'/{device}/demods/{NONLOCAL_MFLI_DEMODULATOR}/sample')
                    identity_parts = ["Zurich Instruments MFLI", device]
                    if isinstance(props, dict):
                        version = props.get("serverversion") or props.get("version")
                        if version:
                            identity_parts.append(f"LabOne {version}")
                    identity = " · ".join(identity_parts)
                    self.mfli_daq = daq
                    self.mfli_bridge = None
                    self.mfli_device = device
                    self.mfli_props = dict(props) if isinstance(props, dict) else {}
                    self.mfli_props["connection"] = "current-python-discovery"
                    self.mfli_config = {}
                    self.mfli_identity = identity
                    self.logger(f'Non-local Zurich MFLI connected by the current Python Discovery: {identity}')
                    return identity
                except BaseException as error:
                    direct_errors.append(f"Discovery: {error}")
                    if daq is not None:
                        try:
                            daq.disconnect()
                        except BaseException:
                            pass
                    self.logger(f'The current Python Discovery failed; trying the legacy environment bridge as a last resort: {error}')
            direct_detail = " | ".join(direct_errors)
            release_instrument_lease(self.mfli_lease_resource, self)
            self.mfli_lease_resource = ""
            raise RuntimeError(self._mfli_connect_failure_message(requested, reachable_servers, direct_errors, server_reports))
    @staticmethod
    def _mfli_server_devices(daq) -> str:
        parts = []
        for label, node in (('visible', '/zi/devices/visible'), ('connected', '/zi/devices/connected')):
            try:
                value = str(daq.getString(node)).strip()
            except BaseException:
                continue
            if value:
                parts.append(f"{label}={value}")
        return "; ".join(parts)
    @staticmethod
    def _mfli_connect_failure_message(requested: str, reachable_servers, direct_errors, server_reports) -> str:
        servers_text = ', '.join((f'{host}:{port}' for host, port in reachable_servers)) or '8004'
        reports_text = '; '.join((f'{host}:{port} → {report}' for (host, port), report in server_reports.items() if report))
        device_missing = bool(reports_text) and requested not in reports_text.lower()
        if device_missing:
            headline = f'Zurich MFLI {requested}: Data Server {servers_text} is reachable, but {requested} is not on that server (devices visible to the server: {reports_text}).'
            advice = (
                "Check the device name in the panel (it must be this MFLI's own DEV number) and make sure it is "
                "connected to this PC by USB or Ethernet and attached to this PC's Data Server in LabOne; "
                "the other MFLI's built-in 192.168.69.78 server can only see itself. "
                + zurich_choice_hint(requested)
            )
        else:
            last_error = short_status_error(RuntimeError(direct_errors[-1]), 160) if direct_errors else 'the current Python has no usable LabOne API'
            headline = f'Zurich MFLI {requested}: Data Server {servers_text} is reachable, but the LabOne API handshake failed ({last_error}).'
            advice = (
                "Make sure the LabOne / zhinst version on this PC matches the instrument firmware; if this instrument runs "
                "LabOne 18.12, close this program, run "
                "INSTALL_MFLI_LABONE_18_12.bat from the same folder, and restart the program after installation."
            )
        direct_detail = " | ".join(direct_errors) or "no API attempt recorded"
        sections = [headline, f"Connection diagnostics: {direct_detail}"]
        if reports_text:
            sections.append(f"Server device list: {reports_text}")
        sections.append(advice)
        return "\n\n".join(sections)
    @staticmethod
    def _mfli_scalar(value, field: str) -> float:
        try:
            candidate = value[field]
        except (KeyError, TypeError) as error:
            raise RuntimeError(f"MFLI sample lacks the {field!r} field") from error
        if np is not None:
            array = np.asarray(candidate).reshape(-1)
            if array.size == 0:
                raise RuntimeError(f"MFLI sample field {field!r} is empty")
            result = float(array[-1])
        elif isinstance(candidate, (tuple, list)):
            if not candidate:
                raise RuntimeError(f"MFLI sample field {field!r} is empty")
            result = float(candidate[-1])
        else:
            result = float(candidate)
        if not math.isfinite(result):
            raise RuntimeError(f"MFLI sample field {field!r} is not a finite value")
        return result
    @staticmethod
    def _mfli_phase_degrees(x: float, y: float) -> float:
        return math.degrees(math.atan2(float(y), float(x)))
    def configure_mfli(
        self,
        coupling: str,
        harmonic: int,
        channel: str,
        reference_source: str = NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT,
        sample_rate_hz: float = NONLOCAL_SAMPLE_HZ,
        input_impedance: str = "10 MΩ",
        input_scaling: float = NONLOCAL_MFLI_INPUT_SCALING,
        timeconstant_s: float = NONLOCAL_LOCKIN_TIME_CONSTANT_S,
        external_reference_demodulator=None,
    ) -> dict[str, object]:
        if not self.mfli_connected:
            raise RuntimeError("Zurich MFLI is not connected")
        coupling = str(coupling).strip().upper()
        channel = str(channel).strip().upper()
        reference_source = str(reference_source).strip()
        harmonic = int(harmonic)
        sample_rate_hz = float(sample_rate_hz)
        input_impedance = str(input_impedance).strip()
        input_scaling = float(input_scaling)
        timeconstant_s = float(timeconstant_s)
        if coupling not in {"AC", "DC"}:
            raise ValueError("MFLI input coupling can only be AC or DC")
        if harmonic < 1:
            raise ValueError("MFLI harmonic must be a positive integer")
        if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
            raise ValueError("MFLI sampling rate must be a finite positive number")
        transfer_rate_hz = max(10.0, min(1000.0, sample_rate_hz * 5.0))
        if channel not in {"X", "Y", "R"}:
            raise ValueError("MFLI read channel can only be X, Y or R")
        if reference_source not in NONLOCAL_MFLI_REFERENCE_INPUTS:
            choices = ", ".join(NONLOCAL_MFLI_REFERENCE_INPUTS)
            raise ValueError(f"MFLI external reference input must be one of: {choices}")
        impedance_choices = {"10 MΩ": 0, "50 Ω": 1}
        if input_impedance not in impedance_choices:
            raise ValueError("MFLI Signal Input 1 input impedance can only be 10 MΩ or 50 Ω")
        requested_imp50 = impedance_choices[input_impedance]
        if not math.isfinite(input_scaling) or input_scaling <= 0:
            raise ValueError("MFLI Signal Input 1 Scaling must be a finite positive number")
        if not math.isfinite(timeconstant_s) or timeconstant_s <= 0:
            raise ValueError("MFLI time constant must be a finite positive number")
        reference_adcselect = NONLOCAL_MFLI_REFERENCE_INPUTS[reference_source]
        options_text = str(self.mfli_props.get("options", "")).upper()
        has_demod_2 = bool(self.mfli_props.get("has_demod_2", False))
        if self.mfli_bridge is None and not options_text:
            try:
                options_text = str(self.mfli_daq.getString(f'/{self.mfli_device}/features/options')).upper()
                self.mfli_props["options"] = options_text
            except BaseException:
                pass
        if self.mfli_bridge is None and not has_demod_2:
            try:
                self.mfli_daq.getInt(f'/{self.mfli_device}/demods/{NONLOCAL_MFLI_EXTREF_DEMODULATOR}/enable')
                has_demod_2 = True
            except BaseException:
                has_demod_2 = False
            self.mfli_props["has_demod_2"] = has_demod_2
        has_multi_demod = 'MD' in options_text if options_text else has_demod_2
        extref_demod = NONLOCAL_MFLI_EXTREF_DEMODULATOR if has_multi_demod else NONLOCAL_MFLI_DEMODULATOR
        if external_reference_demodulator is not None:
            if external_reference_demodulator != 3 or not self.mfli_has_multi_demod():
                raise RuntimeError('Demod 4 external reference requires MF-MD multi-demodulator support')
            extref_demod = 3
        extref = NONLOCAL_MFLI_EXTREF_CHANNEL
        if self.mfli_bridge is None and extref_demod == 3:
            routes = [i for i in range(2) if int(self.mfli_daq.getInt(f'/{self.mfli_device}/extrefs/{i}/demodselect')) == 3]
            if len(routes) != 1:
                raise RuntimeError('Cannot identify the external-reference controller for Demod 4')
            extref = routes[0]
        if self.mfli_bridge is None:
            for other in range(2):
                if other == extref:
                    continue
                try:
                    self.mfli_daq.setInt(f'/{self.mfli_device}/extrefs/{other}/enable', 0)
                except BaseException:
                    pass
        if self.mfli_bridge is not None:
            reply = self.mfli_bridge.request(
                "configure",
                coupling=coupling,
                harmonic=harmonic,
                extref_demod=extref_demod,
                reference_adcselect=reference_adcselect,
                timeconstant_s=timeconstant_s,
                filter_order=NONLOCAL_LOCKIN_FILTER_ORDER,
                sample_rate_hz=transfer_rate_hz,
                input_imp50=requested_imp50,
                input_scaling=input_scaling,
            )
            actual_ac = int(reply["actual_ac"])
            actual_imp50 = int(reply["actual_imp50"])
            actual_scaling = float(reply["actual_scaling"])
            actual_harmonic = int(reply["actual_harmonic"])
            actual_extref_harmonic = int(reply["actual_extref_harmonic"])
            extref_enabled = int(reply["extref_enabled"])
            extref_adcselect = int(reply["extref_adcselect"])
            actual_timeconstant = float(reply["timeconstant_s"])
            actual_filter_order = int(reply["filter_order"])
            actual_sample_rate = float(reply["sample_rate_hz"])
            input_range_v = float(reply["input_range_v"])
        else:
            device = self.mfli_device
            demod = NONLOCAL_MFLI_DEMODULATOR
            ac_node = f"/{device}/sigins/0/ac"
            imp50_node = f"/{device}/sigins/0/imp50"
            scaling_node = f"/{device}/sigins/0/scaling"
            harmonic_node = f"/{device}/demods/{demod}/harmonic"
            enable_node = f"/{device}/demods/{demod}/enable"
            osc_node = f"/{device}/demods/{demod}/oscselect"
            extref_demod_node = f"/{device}/extrefs/{extref}/demodselect"
            extref_auto_node = f"/{device}/extrefs/{extref}/automode"
            extref_enable_node = f"/{device}/extrefs/{extref}/enable"
            extref_adc_node = f"/{device}/extrefs/{extref}/adcselect"
            measurement_adc_node = f"/{device}/demods/{demod}/adcselect"
            timeconstant_node = f"/{device}/demods/{demod}/timeconstant"
            order_node = f"/{device}/demods/{demod}/order"
            rate_node = f"/{device}/demods/{demod}/rate"
            self.mfli_daq.setInt(ac_node, 1 if coupling == "AC" else 0)
            self.mfli_daq.setInt(imp50_node, requested_imp50)
            self.mfli_daq.setDouble(scaling_node, input_scaling)
            ext_demod_node = f'/{device}/demods/{extref_demod}/adcselect'
            ext_osc_node = f'/{device}/demods/{extref_demod}/oscselect'
            ext_harmonic_node = f'/{device}/demods/{extref_demod}/harmonic'
            ext_demod_enable_node = f'/{device}/demods/{extref_demod}/enable'
            self.mfli_daq.setInt(ext_demod_node, reference_adcselect)
            self.mfli_daq.setInt(ext_osc_node, 0)
            if extref_demod != demod:
                self.mfli_daq.setInt(ext_harmonic_node, 1)
            self.mfli_daq.setInt(ext_demod_enable_node, 1)
            selected_extref_input = int(self.mfli_daq.getInt(ext_demod_node))
            try:
                self.mfli_daq.setInt(extref_demod_node, extref_demod)
            except BaseException:
                pass
            try:
                self.mfli_daq.setInt(extref_auto_node, 4)
            except BaseException:
                pass
            self.mfli_daq.setInt(extref_enable_node, 1)
            if external_reference_demodulator is not None:
                self.mfli_daq.sync()
                if self.mfli_daq.getInt(extref_demod_node) != extref_demod:
                    raise RuntimeError('Zurich external reference Demod 4 routing readback mismatch')
            if extref_demod == demod:
                self.mfli_daq.sync()
                if selected_extref_input != reference_adcselect:
                    raise RuntimeError(f'MFLI single-demodulator external reference failed to select {reference_source}: expected {reference_adcselect}, readback {selected_extref_input}')
                self.mfli_daq.setInt(measurement_adc_node, 0)
            self.mfli_daq.setInt(osc_node, 0)
            self.mfli_daq.setInt(harmonic_node, harmonic)
            self.mfli_daq.setDouble(timeconstant_node, timeconstant_s)
            self.mfli_daq.setInt(order_node, NONLOCAL_LOCKIN_FILTER_ORDER)
            self.mfli_daq.setDouble(rate_node, transfer_rate_hz)
            self.mfli_daq.setInt(enable_node, 1)
            self.mfli_daq.sync()
            actual_ac = int(self.mfli_daq.getInt(ac_node))
            actual_imp50 = int(self.mfli_daq.getInt(imp50_node))
            actual_scaling = float(self.mfli_daq.getDouble(scaling_node))
            actual_harmonic = int(self.mfli_daq.getInt(harmonic_node))
            actual_extref_harmonic = int(self.mfli_daq.getInt(ext_harmonic_node)) if extref_demod != demod else 1
            extref_enabled = int(self.mfli_daq.getInt(extref_enable_node))
            input_range_v = float(self.mfli_daq.getDouble(f'/{device}/sigins/0/range'))
            actual_timeconstant = float(self.mfli_daq.getDouble(timeconstant_node))
            actual_filter_order = int(self.mfli_daq.getInt(order_node))
            actual_sample_rate = float(self.mfli_daq.getDouble(rate_node))
            if extref_demod != demod:
                try:
                    selected_extref_input = int(self.mfli_daq.getInt(extref_adc_node))
                except BaseException:
                    pass
            extref_adcselect = selected_extref_input
        if actual_ac != (1 if coupling == "AC" else 0):
            raise RuntimeError(f"MFLI AC/DC readback mismatch: {actual_ac}")
        if actual_imp50 != requested_imp50:
            raise RuntimeError(f'MFLI Signal Input 1 input impedance readback mismatch: expected {input_impedance}, imp50={actual_imp50}')
        if not math.isclose(actual_scaling, input_scaling, rel_tol=1e-09, abs_tol=1e-12):
            raise RuntimeError(f'MFLI Signal Input 1 Scaling readback mismatch: expected {input_scaling:g}, readback {actual_scaling:g}')
        if actual_harmonic != harmonic:
            raise RuntimeError(f'MFLI harmonic readback mismatch: requested {harmonic}, readback {actual_harmonic}')
        if actual_extref_harmonic != 1:
            raise RuntimeError(f'MFLI external-reference PLL harmonic must be 1: readback {actual_extref_harmonic}')
        if extref_enabled != 1:
            raise RuntimeError(f'MFLI {reference_source} external reference was not enabled successfully')
        if extref_adcselect != reference_adcselect:
            raise RuntimeError(f'MFLI external reference input readback mismatch: expected {reference_source}({reference_adcselect}), readback {extref_adcselect}')
        if not math.isclose(actual_timeconstant, timeconstant_s, rel_tol=0.05, abs_tol=1e-06):
            raise RuntimeError(f'MFLI time constant readback mismatch: requested {timeconstant_s:g} s, readback {actual_timeconstant:g} s')
        if actual_filter_order != NONLOCAL_LOCKIN_FILTER_ORDER:
            raise RuntimeError(f'MFLI filter order readback mismatch: {actual_filter_order}')
        if not math.isfinite(actual_sample_rate) or actual_sample_rate < sample_rate_hz:
            raise RuntimeError(f'MFLI data transfer rate is insufficient: {actual_sample_rate:g} Sa/s')
        self.mfli_config = {
            "meter_type": "MFLI",
            "resource": self.mfli_device,
            "coupling": "AC" if actual_ac else "DC",
            "harmonic": actual_harmonic,
            "channel": channel,
            "reference_source": reference_source,
            "reference_adcselect": reference_adcselect,
            "extref_channel": (int(reply.get("extref_channel", 0)) if self.mfli_bridge is not None else extref),
            "extref_demodulator": extref_demod,
            "extref_harmonic": actual_extref_harmonic,
            "input_range_v": input_range_v,
            "input_impedance": input_impedance,
            "input_scaling": actual_scaling,
            "auto_range": True,
            "timeconstant_s": actual_timeconstant,
            "filter_order": actual_filter_order,
            "sample_rate_hz": actual_sample_rate,
        }
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 0
        self.mfli_initial_autorange_pending = True
        self.forget_mfli_range_guard()
        return dict(self.mfli_config)
    def forget_mfli_range_guard(self) -> None:
        self.mfli_low_floor_v = 0.0
        self.mfli_stepped_down = False
        self.mfli_low_readings_needed = LOCKIN_DOWNRANGE_READINGS
    def read_mfli(self, expected_frequency_hz: float) -> dict[str, object]:
        if not self.mfli_connected:
            raise RuntimeError("Zurich MFLI is not connected")
        if self.mfli_bridge is not None:
            sample = self.mfli_bridge.request("read")["sample"]
        else:
            sample = self.mfli_daq.getSample(f'/{self.mfli_device}/demods/{NONLOCAL_MFLI_DEMODULATOR}/sample')
            sample = dict(sample)
            try:
                sample['device_status_flags'] = int(self.mfli_daq.getInt(f'/{self.mfli_device}/status/flags/binary'))
            except Exception:
                sample["device_status_flags"] = None
            try:
                sample['extref_locked'] = int(self.mfli_daq.getInt(f"/{self.mfli_device}/extrefs/{self.mfli_config.get('extref_channel', NONLOCAL_MFLI_EXTREF_CHANNEL)}/locked"))
            except BaseException:
                sample["extref_locked"] = None
            range_node = f"/{self.mfli_device}/sigins/0/range"
            max_node = f"/{self.mfli_device}/sigins/0/max"
            min_node = f"/{self.mfli_device}/sigins/0/min"
            sample['input_range_v'] = float(self.mfli_daq.getDouble(range_node))
            try:
                sample['input_max_normalized'] = float(self.mfli_daq.getDouble(max_node))
                sample['input_min_normalized'] = float(self.mfli_daq.getDouble(min_node))
                self.mfli_daq.setDouble(max_node, 0.0)
                self.mfli_daq.setDouble(min_node, 0.0)
            except BaseException:
                sample["input_max_normalized"] = None
                sample["input_min_normalized"] = None
        x = self._mfli_scalar(sample, "x")
        y = self._mfli_scalar(sample, "y")
        r = math.hypot(x, y)
        phase_deg = self._mfli_phase_degrees(x, y)
        harmonic = int(self.mfli_config.get("harmonic", 1))
        reported_frequency = math.nan
        try:
            reported_frequency = self._mfli_scalar(sample, "frequency")
            candidates = (reported_frequency, reported_frequency / max(harmonic, 1))
            reference_frequency = min(candidates, key=lambda value: abs(value - expected_frequency_hz))
        except RuntimeError:
            reference_frequency = float(expected_frequency_hz)
        frequency_valid = math.isclose(reference_frequency, expected_frequency_hz, rel_tol=0.005, abs_tol=0.1)
        raw_extref_locked = sample.get("extref_locked")
        extref_locked = None if raw_extref_locked is None else bool(round(float(raw_extref_locked)))
        reference_valid = frequency_valid and extref_locked is not False
        try:
            flags = int(round(self._mfli_scalar(sample, "flags")))
        except RuntimeError:
            flags = 0
        status_parts = []
        raw_device_flags = sample.get("device_status_flags")
        try:
            device_flags = int(raw_device_flags) if raw_device_flags is not None else None
            if device_flags is not None and device_flags < 0:
                device_flags = None
        except (TypeError, ValueError, OverflowError):
            device_flags = None
        input_overload = None if device_flags is None else bool(device_flags & 0x3)
        if input_overload:
            status_parts.append("MFLI input OVERLOAD")
        if flags:
            status_parts.append(f"LabOne flags={flags}")
        if extref_locked is False:
            reference_source = str(self.mfli_config.get('reference_source', NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT))
            status_parts.append(f"{reference_source} external reference not locked")
        elif extref_locked is None and frequency_valid:
            status_parts.append("LabOne 18.12 judges LOCK by reference-frequency convergence")
        if not frequency_valid:
            status_parts.append("reference frequency mismatch")
        input_range_v = self._mfli_scalar(sample, "input_range_v")
        input_extrema = []
        for field in ("input_max_normalized", "input_min_normalized"):
            try:
                input_extrema.append(abs(self._mfli_scalar(sample, field)))
            except (RuntimeError, TypeError, ValueError):
                pass
        input_utilization = max(input_extrema) if input_extrema else min(1.0, abs(r) / max(input_range_v, 1e-30))
        return {
            "meter_type": "MFLI",
            "voltage": {"X": x, "Y": y, "R": r}.get(
                str(self.mfli_config.get("channel", "R")), r
            ),
            "X": x,
            "Y": y,
            "R": r,
            "phase_deg": phase_deg,
            "reference_frequency_hz": reference_frequency,
            "demodulation_frequency_hz": reported_frequency,
            "reference_source": str(
                self.mfli_config.get(
                    "reference_source",
                    NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT,
                )
            ),
            "extref_locked": extref_locked,
            "lia_status": flags | (0 if reference_valid else 8),
            "status_text": "; ".join(status_parts) if status_parts else "OK",
            "reference_valid": reference_valid,
            "sensitivity_index": "",
            "input_range_v": input_range_v,
            "input_utilization": input_utilization,
            "input_extrema_valid": len(input_extrema) == 2 and all(
                math.isfinite(value) for value in input_extrema
            ),
            "device_status_flags": device_flags,
            "input_overload": input_overload,
            "auto_range": "",
        }
    def read_mfli_four_demods(self):
        with self.lock:
            if not self.mfli_connected:
                raise RuntimeError("Zurich MFLI is not connected")
            count = 4 if self.mfli_has_multi_demod() else 2 if self.mfli_props.get("has_demod_2") else 1
            if self.mfli_bridge is not None:
                channels = self.mfli_bridge.request("read_demods", count=count)["demods"]
            else:
                channels = []
                for index in range(count):
                    node = f"/{self.mfli_device}/demods/{index}"
                    enabled = int(self.mfli_daq.getInt(node + "/enable"))
                    if not enabled:
                        channels.append(dict(enabled=0))
                        continue
                    raw = self.mfli_daq.getSample(node + "/sample")
                    try:
                        timestamp = self._mfli_scalar(raw, "timestamp")
                    except (RuntimeError, KeyError):
                        timestamp = None
                    channels.append(dict(x=self._mfli_scalar(raw, "x"), y=self._mfli_scalar(raw, "y"),
                        frequency=self._mfli_scalar(raw, "frequency"), timestamp=timestamp,
                        harmonic=int(self.mfli_daq.getInt(node + "/harmonic")),
                        adcselect=int(self.mfli_daq.getInt(node + "/adcselect")), enabled=enabled))
            channels = list(channels) + [dict(enabled=0)] * (4 - len(channels))
            if len(channels) != 4:
                raise RuntimeError("Zurich must read back all four Demod channels")
            fields = {}
            for index, channel in enumerate(channels, 1):
                prefix = f'zurich_demod{index}_'
                if not channel['enabled']:
                    fields.update({prefix + key: '' for key in ('x_v', 'y_v', 'r_v', 'theta_deg', 'frequency_hz', 'timestamp_ticks', 'harmonic', 'input')})
                    fields.update({prefix+'enabled': 0, prefix+'status': 'disabled'})
                    continue
                x, y, frequency = (float(channel[k]) for k in ('x', 'y', 'frequency'))
                if not all(math.isfinite(v) for v in (x, y, frequency)):
                    raise RuntimeError(f"Zurich Demod {index} readback is invalid")
                prefix = f'zurich_demod{index}_'
                fields.update({prefix+'x_v': x, prefix+'y_v': y, prefix+'r_v': math.hypot(x, y),
                    prefix+'theta_deg': math.degrees(math.atan2(y, x)), prefix+'frequency_hz': frequency,
                    prefix+'timestamp_ticks': channel.get('timestamp') if channel.get('timestamp') is not None else '',
                    prefix+'harmonic': channel['harmonic'], prefix+'input': channel['adcselect'],
                    prefix+'enabled': channel['enabled'], prefix+'status': 'enabled'})
            return fields
    @staticmethod
    def _run_mfli_autorange(self, reason: str) -> str:
        if not self.mfli_connected:
            raise RuntimeError("Zurich MFLI is not connected")
        configured_old = float(self.mfli_config.get("input_range_v", math.nan))
        if self.mfli_bridge is not None:
            result = self.mfli_bridge.request("autorange")["autorange"]
            old_range = float(result["old_input_range_v"])
            new_range = float(result["input_range_v"])
        else:
            device = self.mfli_device
            range_node = f"/{device}/sigins/0/range"
            autorange_node = f"/{device}/sigins/0/autorange"
            old_range = float(self.mfli_daq.getDouble(range_node))
            self.mfli_daq.setInt(autorange_node, 1)
            self.mfli_daq.sync()
            deadline = time.monotonic() + 8.0
            while int(self.mfli_daq.getInt(autorange_node)) != 0:
                if time.monotonic() >= deadline:
                    raise RuntimeError("MFLI Signal Input 1 auto-range timed out")
                time.sleep(0.02)
            self.mfli_daq.sync()
            new_range = float(self.mfli_daq.getDouble(range_node))
            for suffix in ("max", "min"):
                try:
                    self.mfli_daq.setDouble(f'/{device}/sigins/0/{suffix}', 0.0)
                except BaseException:
                    pass
        if not math.isfinite(old_range) or old_range <= 0:
            old_range = configured_old
        if not math.isfinite(new_range) or new_range <= 0:
            raise RuntimeError(f"MFLI auto-range readback is invalid: {new_range!r}")
        self.mfli_config["input_range_v"] = new_range
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 3
        self.mfli_initial_autorange_pending = False
        if reason in self.MFLI_RANGE_FRESH_REASONS:
            self.forget_mfli_range_guard()
        elif reason == "low":
            if new_range < old_range * (1.0 - 1e-6):
                self.mfli_stepped_down = True
            else:
                self.mfli_low_floor_v = new_range
        elif new_range > old_range * (1.0 + 1e-6) and self.__dict__.get("mfli_stepped_down", False):
            self.mfli_stepped_down = False
            needed = int(self.__dict__.get("mfli_low_readings_needed") or LOCKIN_DOWNRANGE_READINGS)
            self.mfli_low_readings_needed = min(2 * needed, MFLI_DOWNRANGE_READINGS_LIMIT)
        return f"RANGE {old_range:.6g}->{new_range:.6g} V ({reason})"
    def _auto_range_mfli(self, sample: dict[str, object]) -> str:
        if not self.__dict__.get("mfli_auto_range_enabled", True):
            return ""
        utilization = float(sample.get("input_utilization", 0.0))
        if not math.isfinite(utilization):
            return ""
        if utilization >= LOCKIN_UPRANGE_FRACTION:
            return self._run_mfli_autorange("overload/high")
        if self.mfli_range_cooldown > 0:
            self.mfli_range_cooldown -= 1
            self.mfli_low_range_count = 0
            return ""
        floor = float(self.__dict__.get("mfli_low_floor_v") or 0.0)
        try:
            in_use = float((self.__dict__.get("mfli_config") or {}).get("input_range_v", math.nan))
        except (TypeError, ValueError):
            in_use = math.nan
        kept_here = floor > 0.0 and math.isfinite(in_use) and in_use <= floor * (1.0 + 1e-6)
        if utilization < LOCKIN_DOWNRANGE_FRACTION and not kept_here:
            self.mfli_low_range_count += 1
            needed = int(self.__dict__.get("mfli_low_readings_needed") or LOCKIN_DOWNRANGE_READINGS)
            if self.mfli_low_range_count >= needed:
                return self._run_mfli_autorange("low")
        else:
            self.mfli_low_range_count = 0
        return ""
    def read_stfmr_mfli(self, expected_frequency_hz: float, *, auto_range: bool, initial: bool=False, attempts: int=0) -> dict[str, object]:
        sample = self.read_mfli(expected_frequency_hz)
        if not auto_range:
            return sample
        utilization = float(sample.get("input_utilization", math.nan))
        raw_valid = bool(sample.get("input_extrema_valid", False))
        overloaded = sample.get("input_overload") is True
        high = raw_valid and math.isfinite(utilization) and utilization >= LOCKIN_UPRANGE_FRACTION
        if attempts >= 3:
            if overloaded or high or initial:
                raise RuntimeError('MFLI auto-range still overloaded or near full scale after 3 retries; the current point was not saved; please check the input signal and wiring')
            return sample
        if overloaded:
            action = self._run_mfli_autorange("STFMR input overload")
        elif initial:
            action = self._run_mfli_autorange("STFMR frequency start")
        elif raw_valid:
            action = self._auto_range_mfli(sample)
        else:
            action = ""
        if action:
            sample["auto_range"] = action
            sample["input_range_after_v"] = self.mfli_config["input_range_v"]
        return sample
    def mfli_has_multi_demod(self) -> bool:
        with self.lock:
            options = str(self.mfli_props.get("options", "")).upper()
            if self.mfli_bridge is None and self.mfli_daq is not None and not options:
                try:
                    options = str(self.mfli_daq.getString(f'/{self.mfli_device}/features/options')).upper()
                    self.mfli_props["options"] = options
                except BaseException:
                    pass
            if options:
                return "MD" in options
            has_demod_4 = self.mfli_props.get("has_demod_4")
            if not has_demod_4 and self.mfli_bridge is None and self.mfli_daq is not None:
                try:
                    self.mfli_daq.getInt(f'/{self.mfli_device}/demods/3/enable')
                    self.mfli_daq.getInt(f'/{self.mfli_device}/extrefs/1/demodselect')
                    has_demod_4 = self.mfli_props["has_demod_4"] = True
                except BaseException:
                    has_demod_4 = False
            return bool(has_demod_4)
    def disconnect_mfli(self) -> None:
        with self.lock:
            daq = self.mfli_daq
            bridge = self.mfli_bridge
            mfli_lease_resource = self.mfli_lease_resource
            if daq is None and bridge is None:
                self.mfli_lease_resource = ""
                release_instrument_lease(mfli_lease_resource, self)
                self._close_manager_if_unused()
                return
            if daq is not None:
                extref_demod = int(self.mfli_config.get('extref_demodulator', NONLOCAL_MFLI_DEMODULATOR))
                disable_nodes = [
                    f"/{self.mfli_device}/extrefs/"
                    f"{self.mfli_config.get('extref_channel', NONLOCAL_MFLI_EXTREF_CHANNEL)}/enable",
                    f"/{self.mfli_device}/demods/"
                    f"{NONLOCAL_MFLI_DEMODULATOR}/enable",
                ]
                if extref_demod != NONLOCAL_MFLI_DEMODULATOR:
                    disable_nodes.append(f'/{self.mfli_device}/demods/{extref_demod}/enable')
                for node in disable_nodes:
                    try:
                        daq.setInt(node, 0)
                    except BaseException:
                        pass
                try:
                    daq.sync()
                except BaseException:
                    pass
            try:
                if daq is not None:
                    daq.disconnect()
            except BaseException:
                pass
            if bridge is not None:
                try:
                    bridge.close()
                except BaseException:
                    bridge.close(force=True)
            self.mfli_daq = None
            self.mfli_bridge = None
            self.mfli_device = ""
            self.mfli_props = {}
            self.mfli_config = {}
            self.mfli_identity = ""
            self.mfli_lease_resource = ""
            self.mfli_low_range_count = 0
            self.mfli_range_cooldown = 0
            self.mfli_initial_autorange_pending = False
            release_instrument_lease(mfli_lease_resource, self)
            self._close_manager_if_unused()
            self.logger("Non-local Zurich MFLI disconnected")



__all__ = [
    "LabOneCAPIAdapter",
    "LabOneDotNetAdapter",
    "LegacyMFLIBridge",
    "MFLI_DOWNRANGE_READINGS_LIMIT",
    "NONLOCAL_MFLI_API_LEVEL",
    "NONLOCAL_MFLI_DAQ_METHODS",
    "NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT",
    "NONLOCAL_MFLI_DEMODULATOR",
    "NONLOCAL_MFLI_DEVICE",
    "NONLOCAL_MFLI_EXTREF_CHANNEL",
    "NONLOCAL_MFLI_EXTREF_DEMODULATOR",
    "NONLOCAL_MFLI_INPUT_SCALING",
    "NONLOCAL_MFLI_LEGACY_PYTHON_ENV",
    "NONLOCAL_MFLI_REFERENCE_INPUTS",
    "NONLOCAL_MFLI_SERVER_CANDIDATES",
    "NONLOCAL_MFLI_SERVER_HOST",
    "NONLOCAL_MFLI_SERVER_PORT",
    "SUPER_ZURICH_MFLI_DEVICE",
    "SUPER_ZURICH_MFLI_SERVERS",
    "ZURICH_MFLI_CHOICES",
    "ZurichMFLIController",
    "_MFLI_BRIDGE_PREFIX",
    "_MFLI_BRIDGE_SOURCE",
    "_find_mfli_legacy_python",
    "pyvisa",
    "short_status_error",
    "zi_core",
    "zi_utils",
    "zurich_choice_hint",
    "zurich_mfli_choice",
    "zurich_mfli_label",
]
