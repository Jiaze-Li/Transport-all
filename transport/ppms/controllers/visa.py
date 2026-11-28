"""VISA resource manager sharing, OS-level instrument leases, and safe identity controller."""

from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

try:
    import pyvisa
except ImportError:
    pyvisa = None

_INSTRUMENT_LEASE_LOCK = threading.RLock()
_INSTRUMENT_LEASES: dict[str, tuple[object, str]] = {}
_SHARED_VISA_MANAGER_LOCK = threading.RLock()
_SHARED_VISA_MANAGER = None
_SHARED_VISA_MANAGER_USERS = 0

INSTRUMENT_LEASE_TEXT_BYTES = 512
INSTRUMENT_LEASE_PROGRAM = Path(sys.argv[0]).stem or "Transport"


def acquire_shared_visa_manager():
    """Acquire or reuse the process-wide shared PyVISA ResourceManager."""
    global _SHARED_VISA_MANAGER, _SHARED_VISA_MANAGER_USERS
    if pyvisa is None:
        raise RuntimeError(
            "pyvisa is missing; please run: py -m pip install pyvisa pyvisa-py"
        )
    with _SHARED_VISA_MANAGER_LOCK:
        manager = _SHARED_VISA_MANAGER
        if manager is not None:
            try:
                manager.session
            except BaseException as error:
                if _SHARED_VISA_MANAGER_USERS:
                    raise RuntimeError(
                        "The shared VISA resource manager session became invalid unexpectedly; "
                        "please close the instruments that still show as connected and reconnect"
                    ) from error
                _SHARED_VISA_MANAGER = None
                manager = None
        if manager is None:
            manager = pyvisa.ResourceManager()
            manager.session
            _SHARED_VISA_MANAGER = manager
        _SHARED_VISA_MANAGER_USERS += 1
        return manager


def release_shared_visa_manager(manager) -> None:
    """Release a reference to the shared PyVISA ResourceManager and close when unused."""
    global _SHARED_VISA_MANAGER, _SHARED_VISA_MANAGER_USERS
    if manager is None:
        return
    with _SHARED_VISA_MANAGER_LOCK:
        if manager is not _SHARED_VISA_MANAGER:
            return
        _SHARED_VISA_MANAGER_USERS = max(0, _SHARED_VISA_MANAGER_USERS - 1)
        if _SHARED_VISA_MANAGER_USERS == 0:
            try:
                manager.close()
            except BaseException:
                pass
            finally:
                _SHARED_VISA_MANAGER = None


def instrument_lease_key(resource: str) -> str:
    """Normalize a VISA or device resource string into a canonical lease key."""
    key = str(resource).strip().upper()
    match = re.fullmatch(r"GPIB(\d*)::(\d+)(?:::(\d+))?(?:::INSTR)?", key)
    if match:
        board, primary, secondary = match.groups()
        key = f"GPIB{int(board or '0')}::{int(primary)}"
        if secondary is not None:
            key += f"::{int(secondary)}"
        key += "::INSTR"
    return key


def _process_instrument_leases() -> dict:
    return sys.__dict__.setdefault("_transport_instrument_process_leases", {})


def _process_instrument_lease_lock():
    return sys.__dict__.setdefault(
        "_transport_instrument_process_lease_lock", threading.RLock()
    )


def _instrument_lease_object_name(key: str) -> str:
    return "TransportInstrumentLease_" + re.sub("[^A-Za-z0-9]+", "_", key)


def hold_instrument_for_this_process(key: str, owner_label: str):
    """Acquire an OS-level named lease (Windows CreateFileMappingW / POSIX flock)."""
    text = (
        f"{INSTRUMENT_LEASE_PROGRAM} / {owner_label} (process {os.getpid()})"
    ).encode("utf-8")[: INSTRUMENT_LEASE_TEXT_BYTES - 1]
    name = _instrument_lease_object_name(key)
    holder = None
    if sys.platform == "win32":
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileMappingW.restype = ctypes.c_void_p
            kernel.CreateFileMappingW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_wchar_p,
            )
            kernel.MapViewOfFile.restype = ctypes.c_void_p
            kernel.MapViewOfFile.argtypes = (
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_size_t,
            )
            kernel.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            ctypes.set_last_error(0)
            handle = kernel.CreateFileMappingW(
                ctypes.c_void_p(-1),
                None,
                4,
                0,
                INSTRUMENT_LEASE_TEXT_BYTES,
                "Local\\" + name,
            )
            error = ctypes.get_last_error()
        except Exception:
            return None
        if not handle:
            if error == 5:
                raise RuntimeError(
                    "a program run with other Windows rights, e.g. as administrator"
                )
            return None
        if error != 183:
            view = kernel.MapViewOfFile(handle, 6, 0, 0, INSTRUMENT_LEASE_TEXT_BYTES)
            if view:
                ctypes.memmove(view, text + b"\0", len(text) + 1)
                kernel.UnmapViewOfFile(view)
            return ("win32", handle)
        holder = ""
        try:
            view = kernel.MapViewOfFile(handle, 4, 0, 0, INSTRUMENT_LEASE_TEXT_BYTES)
            if view:
                try:
                    holder = (
                        ctypes.string_at(view, INSTRUMENT_LEASE_TEXT_BYTES)
                        .split(b"\0", 1)[0]
                        .decode("utf-8", "replace")
                    )
                finally:
                    kernel.UnmapViewOfFile(view)
        finally:
            kernel.CloseHandle(handle)
    else:
        try:
            import fcntl
            import tempfile

            folder = Path(tempfile.gettempdir()) / "Transport_instrument_leases"
            folder.mkdir(parents=True, exist_ok=True)
            handle = open(folder / (name + ".lock"), "a+", encoding="utf-8")
        except (OSError, ImportError):
            return None
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                handle.seek(0)
                holder = handle.read(INSTRUMENT_LEASE_TEXT_BYTES).strip()
            except OSError:
                holder = ""
            handle.close()
        else:
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(text.decode("utf-8", "replace"))
                handle.flush()
            except OSError:
                pass
            return ("file", handle)
    raise RuntimeError(holder or "another program")


def let_go_of_instrument_for_this_process(token) -> None:
    """Release an OS-level lease token acquired by hold_instrument_for_this_process."""
    if token is None:
        return
    kind, handle = token
    try:
        if kind == "win32":
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))
        else:
            handle.close()
    except (OSError, AttributeError):
        pass


def acquire_instrument_lease(resource: str, owner: object, owner_label: str) -> str:
    """Acquire both in-process and OS-level leases for an instrument resource."""
    key = instrument_lease_key(resource)
    if not key:
        raise ValueError("The instrument resource address cannot be empty")
    with _INSTRUMENT_LEASE_LOCK:
        current = _INSTRUMENT_LEASES.get(key)
        if current is not None:
            raise RuntimeError(
                f'{resource} is already connected by "{current[1]}"; '
                f'please disconnect it in that module first, then connect from "{owner_label}"'
            )
        with _process_instrument_lease_lock():
            held = _process_instrument_leases()
            entry = held.get(key)
            if entry is None:
                try:
                    entry = held[key] = [
                        hold_instrument_for_this_process(key, str(owner_label)),
                        0,
                    ]
                except RuntimeError as other:
                    raise RuntimeError(
                        f"{resource} is in use by another Transport program on this PC ({other}); "
                        "disconnect it there or close that program first (if no such window is open, "
                        f'end that process in Task Manager), then connect from "{owner_label}"'
                    ) from None
            entry[1] += 1
        _INSTRUMENT_LEASES[key] = (owner, str(owner_label))
    return key


def release_instrument_lease(resource: str, owner: object) -> None:
    """Release in-process and OS-level leases held by owner for resource."""
    key = instrument_lease_key(resource)
    if not key:
        return
    with _INSTRUMENT_LEASE_LOCK:
        current = _INSTRUMENT_LEASES.get(key)
        if current is not None and current[0] is owner:
            _INSTRUMENT_LEASES.pop(key, None)
            with _process_instrument_lease_lock():
                held = _process_instrument_leases()
                entry = held.get(key)
                if entry is not None:
                    entry[1] -= 1
                    if entry[1] <= 0:
                        held.pop(key, None)
                        let_go_of_instrument_for_this_process(entry[0])


class SafeVisaIdentityController:
    """VISA instrument controller with *IDN? verification, lease, and safe-off cleanup."""

    def __init__(
        self,
        logger: Callable[[str], None],
        label: str,
        identity_token: str,
        safe_off_commands: tuple[str, ...] = (),
        owner_scope: str = "PPMS",
        preserve_visa_io_defaults: bool = False,
        safe_off_on_connect: bool = True,
        enumerate_before_open: bool = False,
        connect_settle_seconds: float = 0.0,
    ) -> None:
        self.logger = logger
        self.label = str(label)
        self.identity_token = str(identity_token).upper()
        self.safe_off_commands = tuple(safe_off_commands)
        self.owner_scope = str(owner_scope)
        self.preserve_visa_io_defaults = bool(preserve_visa_io_defaults)
        self.safe_off_on_connect = bool(safe_off_on_connect)
        self.enumerate_before_open = bool(enumerate_before_open)
        self.connect_settle_seconds = max(0.0, float(connect_settle_seconds))
        self.resource_manager = None
        self.instrument = None
        self.resource_name = ""
        self.identity = ""
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    def connect(self, resource_name: str) -> str:
        if pyvisa is None:
            raise RuntimeError(
                "pyvisa is missing; please run: py -m pip install pyvisa pyvisa-py"
            )
        resource_name = str(resource_name).strip()
        with self.lock:
            if self.instrument is not None:
                return self.identity
            acquire_instrument_lease(
                resource_name, self, f"{self.owner_scope} / {self.label}"
            )
            manager = None
            instrument = None
            identity_verified = False
            try:
                manager = acquire_shared_visa_manager()
                if self.enumerate_before_open:
                    manager.list_resources()
                instrument = manager.open_resource(resource_name)
                instrument.timeout = 3000 if self.preserve_visa_io_defaults else 5000
                if not self.preserve_visa_io_defaults:
                    instrument.write_termination = "\n"
                    instrument.read_termination = "\n"
                    try:
                        instrument.query_delay = 0.05
                    except (AttributeError, ValueError):
                        pass
                else:
                    instrument.write_termination = "\r\n"
                    instrument.read_termination = None
                    instrument.send_end = True
                if self.connect_settle_seconds > 0:
                    time.sleep(self.connect_settle_seconds)
                try:
                    identity = instrument.query("*IDN?").strip()
                except BaseException as error:
                    raise RuntimeError(
                        f"{self.label} identity query *IDN? failed: {error}; "
                        "no output/setting command was sent to the device this time; "
                        "the VISA session has been closed"
                    ) from error
                if self.identity_token not in identity.upper():
                    raise RuntimeError(
                        f"The device returned by {resource_name} is not {self.label}: {identity}"
                    )
                identity_verified = True
                if self.safe_off_on_connect:
                    for command in self.safe_off_commands:
                        try:
                            instrument.write(command)
                        except BaseException as error:
                            raise RuntimeError(
                                f"{self.label} connection safety command {command!r} failed: {error}"
                            ) from error
                self.resource_manager = manager
                self.instrument = instrument
                self.resource_name = resource_name
                self.identity = identity
                self.logger(f"{self.owner_scope} {self.label} connected: {identity}")
                return identity
            except BaseException:
                if instrument is not None:
                    if identity_verified and self.safe_off_on_connect:
                        for command in self.safe_off_commands:
                            try:
                                instrument.write(command)
                            except BaseException:
                                pass
                    try:
                        instrument.close()
                    except BaseException:
                        pass
                release_shared_visa_manager(manager)
                release_instrument_lease(resource_name, self)
                raise

    def safe_output_off(self) -> None:
        with self.lock:
            if self.instrument is None:
                return
            errors = []
            for command in self.safe_off_commands:
                try:
                    self.instrument.write(command)
                except BaseException as error:
                    errors.append(f"{command}: {error}")
            if errors:
                raise RuntimeError(
                    f"{self.label} failed to turn output off: " + "; ".join(errors)
                )

    def disconnect(self) -> None:
        with self.lock:
            instrument = self.instrument
            manager = self.resource_manager
            resource_name = self.resource_name
            if instrument is None and manager is None:
                return
            shutdown_error = None
            try:
                self.safe_output_off()
            except BaseException as error:
                shutdown_error = error
            if instrument is not None:
                try:
                    instrument.write("SYST:LOC")
                except BaseException:
                    pass
                try:
                    instrument.close()
                except BaseException:
                    pass
            release_shared_visa_manager(manager)
            self.instrument = None
            self.resource_manager = None
            self.resource_name = ""
            self.identity = ""
            release_instrument_lease(resource_name, self)
            self.logger(f"{self.owner_scope} {self.label} disconnected")
            if shutdown_error is not None:
                raise shutdown_error


__all__ = [
    "INSTRUMENT_LEASE_PROGRAM",
    "INSTRUMENT_LEASE_TEXT_BYTES",
    "SafeVisaIdentityController",
    "acquire_instrument_lease",
    "acquire_shared_visa_manager",
    "hold_instrument_for_this_process",
    "instrument_lease_key",
    "let_go_of_instrument_for_this_process",
    "pyvisa",
    "release_instrument_lease",
    "release_shared_visa_manager",
]
