"""Standalone PPMS application (authoritative source in transport/ppms/)."""
from __future__ import annotations
import csv
import ctypes
import datetime as datetime_module
import json
import math
import os
import queue
import re
import socket
import subprocess
import sys
import traceback
import threading
import time
import tkinter as tk
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk
from typing import Callable
from transport.core.scan_sequences import build_round_trip_values, ppms_scan_axis
from transport.core.scan_recording import WINDOWS_RESERVED_FILE_NAMES, ScanRunRecorder as _CoreScanRunRecorder, make_scan_run_folder as _make_scan_run_folder, scan_file_stem as _scan_file_stem, scan_log_line as _scan_log_line
from transport.core.scan_lifecycle import ScanCloseHandoff, zero_scan_outputs, scan_end_outputs_text
from transport.ppms.protocol import PPMS_FIELD_LIMIT_OE, PPMS_FIELD_RATE_LIMIT_OE_S, PPMS_TEMPERATURE_LOW_BOUNDARY_K, PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN, PPMS_TEMPERATURE_MAX_K, PPMS_TEMPERATURE_MAX_RATE_K_MIN, PPMS_TEMPERATURE_MIN_K, ppms_scan_temperature_segments, validate_ppms_field_command, validate_ppms_temperature_command
from transport.ppms.controllers.multivu import PPMSMultiVuReadbackController
from transport.ppms.controllers.environment import (
    PPMSRemoteReadbackController,
    PPMS_DEFAULT_BACKEND,
    PPMS_DEFAULT_HOST,
    PPMS_DEFAULT_PORT,
    PPMS_HOST_CHOICES,
    PPMS_MULTIPYVU_BACKEND,
    PPMS_MULTIPYVU_DEFAULT_PORT,
    PPMS_QDINSTRUMENT_BACKEND,
    PPMS_QDINSTRUMENT_DEFAULT_PORT,
)
from transport.ppms.controllers.qdinstrument import (
    PPMSQDInstrumentReadbackController,
    PPMS_FIELD_RELATIVE_TOLERANCE,
    PPMS_FIELD_TOLERANCE_OE,
    PPMS_QDINSTRUMENT_DLL_ENV,
    QDCommandUncertain,
)
from transport.ppms.controllers.sr830 import (
    LOCKIN_DOWNRANGE_FRACTION,
    LOCKIN_DOWNRANGE_READINGS,
    LOCKIN_UPRANGE_FRACTION,
    NONLOCAL_LOCKIN_FILTER_ORDER,
    NONLOCAL_LOCKIN_TIME_CONSTANT_S,
    NONLOCAL_SR830_FULL_SCALE_V,
    SR830Controller,
    SR830_DISPLAY_XY_COMMANDS,
    SR830_TIME_CONSTANTS_S,
    STFMRSR830Controller,
    STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT,
    STFMR_LOCKIN_SR830_REFERENCE_INPUTS,
    STFMR_LOCKIN_SR830_SENSITIVITY_CODE,
    STFMR_LOCKIN_SR830_TIME_CONSTANTS,
    STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE,
    show_sr830_x_and_y,
)
from transport.ppms.controllers.visa import (
    INSTRUMENT_LEASE_PROGRAM,
    INSTRUMENT_LEASE_TEXT_BYTES,
    SafeVisaIdentityController,
    acquire_instrument_lease,
    acquire_shared_visa_manager,
    hold_instrument_for_this_process,
    instrument_lease_key,
    let_go_of_instrument_for_this_process,
    release_instrument_lease,
    release_shared_visa_manager,
)
from transport.ppms.controllers.zurich import (
    K6221_OUTPUT_LOW_FLOATING,
    K6221_OUTPUT_LOW_QUERY,
    LabOneCAPIAdapter,
    LabOneDotNetAdapter,
    LegacyMFLIBridge,
    MFLI_DOWNRANGE_READINGS_LIMIT,
    NONLOCAL_2182_RESOURCE,
    NONLOCAL_MARKER_LINE,
    NONLOCAL_MARKER_PHASE_DEG,
    NONLOCAL_MAX_PEAK_A,
    NONLOCAL_MFLI_API_LEVEL,
    NONLOCAL_MFLI_DAQ_METHODS,
    NONLOCAL_MFLI_DEFAULT_REFERENCE_INPUT,
    NONLOCAL_MFLI_DEMODULATOR,
    NONLOCAL_MFLI_DEVICE,
    NONLOCAL_MFLI_EXTREF_CHANNEL,
    NONLOCAL_MFLI_EXTREF_DEMODULATOR,
    NONLOCAL_MFLI_INPUT_SCALING,
    NONLOCAL_MFLI_LEGACY_PYTHON_ENV,
    NONLOCAL_MFLI_REFERENCE_INPUTS,
    NONLOCAL_MFLI_SERVER_CANDIDATES,
    NONLOCAL_MFLI_SERVER_HOST,
    NONLOCAL_MFLI_SERVER_PORT,
    NONLOCAL_SAMPLE_HZ,
    NONLOCAL_VI_SR830_TIME_CONSTANT_CODE,
    NonLocalACController,
    SUPER_ZURICH_MFLI_DEVICE,
    SUPER_ZURICH_MFLI_SERVERS,
    ZURICH_MFLI_CHOICES,
    ZurichMFLIController,
    _MFLI_BRIDGE_PREFIX,
    _MFLI_BRIDGE_SOURCE,
    _find_mfli_legacy_python,
    short_status_error,
    zurich_choice_hint,
    zurich_mfli_choice,
    zurich_mfli_label,
)
from transport.ppms.engine.planning import (
    K6221_MAX_ABS_CURRENT_A,
    K6221_MAX_COMPLIANCE_V,
    K6221_MIN_COMPLIANCE_V,
    LOCKIN_SETTLE_TAUS,
    NONLOCAL_FSCAN_MAX_HZ,
    PPMS_FIELD_COMMANDING_MODES,
    PPMS_FREQUENCY_SCAN_MODES,
    PPMS_HARMONIC_CHOICES,
    PPMS_HARMONIC_LABELS,
    PPMS_HARMONIC_MODE_ORDERS,
    PPMS_HARMONIC_ORDERS,
    PPMS_PLOT_AXIS_COLUMNS,
    PPMS_SCAN_MODES,
    PPMS_SCAN_PLOT_AXES,
    PPMS_SR830_HARMONIC_SETTING_KEYS,
    PPMS_SR830_KEYS,
    PPMS_SR830_LABELS,
    PPMS_ZURICH_MODE_ORDERS,
    build_ppms_field_scan_sequence,
    nonlocal_prime_frequencies,
    parse_ppms_macro,
    plan_ppms_scan_axes,
    ppms_frequency_count_text,
    ppms_frequency_scan_note,
    ppms_lockin_settle_s,
    ppms_lockin_settle_text,
    ppms_prime_frequencies,
    ppms_settle_status_text,
    validate_ppms_transport_scan_settings,
)
from transport.common.harmonics import DEFAULT_HARMONIC_CHOICES, DEFAULT_HARMONIC_LABELS, DEFAULT_HARMONIC_MODE_ORDERS, DEFAULT_HARMONIC_ORDERS, DEFAULT_SR830_HARMONIC_SETTING_KEYS, parse_harmonic_mode, schedule_harmonic_passes
from transport.common.settling import calculate_lockin_settle_s, format_lockin_settle_text, format_settle_status_text
from transport.common.validation import validate_transport_scan_settings
_PROGRAM_FILE = str(Path(__file__).resolve().parents[2] / "Transport_PPMS.py")
# Installed with pip, the package is in site-packages: the folder the program is started in stands in.
if not getattr(sys, "frozen", False) and {"site-packages", "dist-packages"} & set(Path(_PROGRAM_FILE).parts):
    _PROGRAM_FILE = str(Path.cwd() / "Transport_PPMS.py")
_ENGLISH_TK_DIALOGS = r"""
package require msgcat
::msgcat::mclocale en
foreach {name file} {
    ::tk::MessageBox msgbox.tcl
    ::tk::dialog::file:: tkfbox.tcl
    ::tk::dialog::file::chooseDir:: choosedir.tcl
} {
    if {[info commands $name] eq ""} { catch {auto_load $name} }
    if {[info commands $name] eq ""} {
        uplevel #0 [list source [file join $::tk_library $file]]
    }
}
foreach {command target} {
    ::tk_messageBox {::tk::MessageBox}
    ::tk_getOpenFile {::tk::dialog::file:: open}
    ::tk_getSaveFile {::tk::dialog::file:: save}
    ::tk_chooseDirectory {::tk::dialog::file::chooseDir::}
} {
    if {[interp alias {} $command] ne ""} { continue }
    if {[info commands ${command}_native] eq ""} { rename $command ${command}_native }
    interp alias {} $command {} {*}$target
}
"""
_ENGLISH_MESSAGE_BUTTONS = {
    "ok": (("ok", "OK"),),
    "okcancel": (("ok", "OK"), ("cancel", "Cancel")),
    "yesno": (("yes", "Yes"), ("no", "No")),
    "yesnocancel": (("yes", "Yes"), ("no", "No"), ("cancel", "Cancel")),
    "retrycancel": (("retry", "Retry"), ("cancel", "Cancel")),
    "abortretryignore": (("abort", "Abort"), ("retry", "Retry"), ("ignore", "Ignore")),
}
_ENGLISH_MESSAGE_ICONS = {'error': 'error', 'warning': 'warning', 'info': 'information', 'question': 'question'}
_NATIVE_MESSAGE_SHOW = None
def english_message_box(title=None, message=None, _icon=None, _type=None, **options) -> str:
    parent = options.get("parent") or getattr(tk, "_default_root", None)
    if not isinstance(parent, tk.Misc) or _NATIVE_MESSAGE_SHOW is None:
        if _NATIVE_MESSAGE_SHOW is None:
            raise RuntimeError("A message box needs a Tk window")
        return _NATIVE_MESSAGE_SHOW(title, message, _icon, _type, **options)
    rest = {key: value for key, value in options.items() if key != "parent"}
    try:
        return _english_message_box(parent, title, message, _icon, _type, **rest)
    except Exception:
        return _NATIVE_MESSAGE_SHOW(title, message, _icon, _type, **options)
def _english_message_box(parent, title, message, _icon, _type, **options) -> str:
    kind = str(options.get("type") or _type or "ok")
    buttons = _ENGLISH_MESSAGE_BUTTONS.get(kind, _ENGLISH_MESSAGE_BUTTONS["ok"])
    names = [name for name, _text in buttons]
    default = str(options.get("default") or names[0])
    cancel = next((name for name in ("cancel", "no", "abort", "ok") if name in names), names[-1])
    top = tk.Toplevel(parent, class_="EnglishMessageBox")
    try:
        return _show_english_message_box(top, parent, title, message, _icon, kind, buttons, names, default, cancel, options)
    except BaseException:
        try:
            top.destroy()
        except tk.TclError:
            pass
        raise
def _show_english_message_box(top, parent, title, message, _icon, kind, buttons, names, default, cancel, options) -> str:
    top.withdraw()
    top.title(str(title or ""))
    try:
        owner = parent.winfo_toplevel()
        if owner.winfo_viewable():
            top.transient(owner)
    except tk.TclError:
        owner = None
    answer = tk.StringVar(top, value=cancel)
    bar = ttk.Frame(top, padding=(12, 0, 12, 12), name="buttons")
    bar.pack(side="bottom", fill="x")
    row = ttk.Frame(bar)
    row.pack(side="right")
    widgets = {}
    for name, text in buttons:
        button = ttk.Button(row, text=text, name=name, command=lambda value=name: answer.set(value))
        button.pack(side="left", padx=(6, 0))
        widgets[name] = button
    body = ttk.Frame(top, padding=(12, 12, 12, 8))
    body.pack(side="top", fill="both", expand=True)
    icon = _ENGLISH_MESSAGE_ICONS.get(str(options.get("icon") or _icon or "info"), "information")
    image = f"::tk::icons::{icon}"
    try:
        has_image = image in top.tk.splitlist(top.tk.call("image", "names"))
    except tk.TclError:
        has_image = False
    if has_image:
        ttk.Label(body, image=image).pack(side="left", anchor="n", padx=(0, 12))
    else:
        tk.Label(body, bitmap={'information': 'info'}.get(icon, icon)).pack(side='left', anchor='n', padx=(0, 12))
    text = ttk.Frame(body)
    text.pack(side="left", fill="both", expand=True)
    try:
        wrap = max(360, min(640, int(top.winfo_screenwidth() * 0.4)))
    except tk.TclError:
        wrap = 520
    ttk.Label(text, text=str(message or ""), wraplength=wrap, justify="left").pack(anchor="w")
    if options.get("detail"):
        ttk.Label(text, text=str(options['detail']), wraplength=wrap, justify='left').pack(anchor='w', pady=(6, 0))
    top.protocol("WM_DELETE_WINDOW", lambda: answer.set(cancel))
    top.bind("<Escape>", lambda _event: answer.set(cancel))
    def press(_event=None) -> None:
        focused = top.focus_get()
        target = focused if focused in widgets.values() else widgets.get(default, widgets[names[0]])
        target.invoke()
    top.bind("<Return>", press)
    top.update_idletasks()
    width, height = top.winfo_reqwidth(), top.winfo_reqheight()
    try:
        if owner is not None and owner.winfo_viewable():
            x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
            y = owner.winfo_rooty() + (owner.winfo_height() - height) // 3
        else:
            x = (top.winfo_screenwidth() - width) // 2
            y = (top.winfo_screenheight() - height) // 3
        x = max(0, min(x, top.winfo_screenwidth() - width))
        y = max(0, min(y, top.winfo_screenheight() - height - 40))
        top.geometry(f"+{x}+{y}")
    except tk.TclError:
        pass
    top.deiconify()
    try:
        top.lift()
        top.attributes("-topmost", True)
        top.focus_force()
    except tk.TclError:
        pass
    try:
        previous_grab = str(top.tk.call("grab", "current", top._w)) or None
    except tk.TclError:
        previous_grab = None
    try:
        top.grab_set()
    except tk.TclError:
        pass
    widgets.get(default, widgets[names[0]]).focus_set()
    try:
        top.wait_variable(answer)
        value = answer.get()
    finally:
        try:
            top.grab_release()
            top.destroy()
        except tk.TclError:
            pass
        if previous_grab is not None:
            try:
                if int(parent.tk.call('winfo', 'exists', previous_grab)) and int(parent.tk.call('winfo', 'viewable', previous_grab)):
                    parent.tk.call("grab", "set", previous_grab)
            except tk.TclError:
                pass
    return value
FOLDER_DIALOG_NAME_RULE = "Use a valid Windows folder name without a path."
FOLDER_DIALOG_COUNT_LIMIT = 20000
_OPEN_SCAN_RUNS = weakref.WeakSet()
_FOLDER_DIALOG_LAST_DIRECTORY = ""
_NATIVE_ASK_DIRECTORY = None
def folder_name_problem(name: str) -> str:
    text = str(name)
    if not text.strip():
        return "Type a name."
    if (text != text.strip() or text.startswith(".") or text.endswith(".") or len(text) > 255
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', text)
            or text.split(".")[0].upper() in WINDOWS_RESERVED_FILE_NAMES):
        return FOLDER_DIALOG_NAME_RULE
    return ""
def folder_name_sort_key(name: str) -> list:
    return [(0, int(part), '') if part.isdigit() else (1, 0, part.casefold()) for part in re.split('(\\d+)', str(name)) if part]
def list_subfolders(directory) -> list[str]:
    names = []
    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                if entry.name.startswith(".") or not entry.is_dir():
                    continue
                if getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x2:
                    continue
            except OSError:
                continue
            names.append(entry.name)
    return sorted(names, key=folder_name_sort_key)
def _folder_key(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))
def open_scan_runs() -> list:
    return [recorder for recorder in list(_OPEN_SCAN_RUNS) if not getattr(recorder, "closed", True)]
def scan_run_using_folder(folder) -> Path | None:
    target = _folder_key(folder)
    for recorder in open_scan_runs():
        for used in (recorder.directory, *getattr(recorder, "other_folders", ())):
            key = _folder_key(used)
            if key == target or key.startswith(target.rstrip("\\/") + os.sep):
                return Path(used)
    return None
def folder_is_link(folder) -> bool:
    try:
        if os.path.islink(folder):
            return True
        return getattr(os.lstat(folder), "st_reparse_tag", 0) in (0xA000000C, 0xA0000003)
    except OSError:
        return False
def folder_contents(folder, limit: int | None = None) -> tuple[int, int, int, bool]:
    if limit is None:
        limit = FOLDER_DIALOG_COUNT_LIMIT
    files = folders = size = 0
    unread = []
    for root, directory_names, file_names in os.walk(folder, onerror=unread.append):
        folders += len(directory_names)
        for name in list(directory_names):
            if folder_is_link(os.path.join(root, name)):
                directory_names.remove(name)
                unread.append(name)
        for name in file_names:
            files += 1
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except OSError as error:
                unread.append(error)
        if files + folders > limit:
            return files, folders, size, False
    return files, folders, size, not unread
def _folder_size_text(size: int) -> str:
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} bytes"
def _folder_edit_refusal(folder: Path, action: str) -> str:
    if not folder.is_dir():
        return f"{folder.name} is not a folder any more."
    if sys.platform == "win32" and folder.name != folder.name.rstrip(". "):
        return f"Windows cannot address the name {folder.name!r}; it is left alone here."
    run = scan_run_using_folder(folder)
    if run is not None:
        return f'A running scan is saving into {run.as_posix()}; {folder.name} cannot be {action} until that scan has ended.'
    return ""
def make_subfolder(directory, name: str) -> Path:
    problem = folder_name_problem(name)
    if problem:
        raise ValueError(problem)
    folder = Path(directory) / name
    if os.path.lexists(folder):
        raise ValueError(f"{name} is already there.")
    folder.mkdir()
    return folder
def rename_subfolder(folder, name: str) -> Path:
    folder = Path(folder)
    refusal = _folder_edit_refusal(folder, "renamed")
    if refusal:
        raise ValueError(refusal)
    problem = folder_name_problem(name)
    if problem:
        raise ValueError(problem)
    if name == folder.name:
        return folder
    target = folder.with_name(name)
    if os.path.lexists(target):
        same = name.casefold() == folder.name.casefold() and os.path.samefile(folder, target)
        if not same:
            raise ValueError(f"{name} is already there.")
    os.rename(folder, target)
    return target
class _RecycleBinInfo(ctypes.Structure):
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1
    _fields_ = [('cbSize', ctypes.c_uint32), ('i64Size', ctypes.c_int64), ('i64NumItems', ctypes.c_int64)]
class _ShellFileOperation(ctypes.Structure):
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", ctypes.c_int), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_void_p)]
def _windows_drive_root(folder) -> str:
    drive = os.path.splitdrive(os.path.realpath(str(folder)))[0]
    if len(drive) != 2 or not drive.endswith(":"):
        return ""
    return drive + "\\"
def recycle_bin_item_count(folder) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        root = _windows_drive_root(folder)
        if not root:
            return None
        info = _RecycleBinInfo()
        info.cbSize = ctypes.sizeof(_RecycleBinInfo)
        query = ctypes.windll.shell32.SHQueryRecycleBinW
        query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_RecycleBinInfo)]
        query.restype = ctypes.c_long
        if query(root, ctypes.byref(info)) != 0:
            return None
        return int(info.i64NumItems)
    except Exception:
        return None
def _recycle_bin_settings(root: str) -> tuple[bool, int | None]:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer") as key:
                if int(winreg.QueryValueEx(key, "NoRecycleFiles")[0]):
                    return True, None
        except OSError:
            pass
    volume = ctypes.create_unicode_buffer(64)
    name = ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW
    name.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    name.restype = ctypes.c_int
    if not name(root, volume, 64):
        raise OSError("the drive's volume name could not be read")
    guid = volume.value[volume.value.index("{"):volume.value.rindex("}") + 1]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\BitBucket\\Volume' + '\\' + guid) as key:
            def value(entry):
                try:
                    return int(winreg.QueryValueEx(key, entry)[0])
                except OSError:
                    return None
            megabytes = value("MaxCapacity")
            return bool(value("NukeOnDelete")), None if megabytes is None else megabytes * 1024 * 1024
    except OSError:
        return False, None
def recycle_bin_refusal(folder, size: int = 0) -> str:
    if sys.platform != "win32":
        return "There is no Recycle Bin for it here"
    try:
        root = _windows_drive_root(folder)
        drive_type = ctypes.windll.kernel32.GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        drive_type.restype = ctypes.c_uint
        if not root or drive_type(root) != 3 or recycle_bin_item_count(folder) is None:
            return "There is no Recycle Bin for it here"
        switched_off, largest = _recycle_bin_settings(root)
        if switched_off:
            return "The Recycle Bin is switched off for its drive"
        if largest is None:
            largest = int(0.05 * _windows_drive_size(root))
        if size > 0.9 * largest:
            return f'It is larger than the Recycle Bin of its drive ({_folder_size_text(largest)})'
        return ""
    except Exception as error:
        return f"The Recycle Bin of its drive could not be checked ({error})"
def _windows_drive_size(root: str) -> int:
    total = ctypes.c_uint64(0)
    size = ctypes.windll.kernel32.GetDiskFreeSpaceExW
    size.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
    size.restype = ctypes.c_int
    if not size(root, None, ctypes.byref(total), None):
        raise OSError("the drive's size could not be read")
    return int(total.value)
def recycle_bin_available(folder) -> bool:
    return not recycle_bin_refusal(folder)
def move_folder_to_recycle_bin(folder, window_handle: int = 0) -> None:
    if sys.platform != "win32":
        raise OSError("There is no Recycle Bin on this computer.")
    name = Path(folder).name
    source = ctypes.create_unicode_buffer(os.path.abspath(str(folder)) + "\0")
    operation = _ShellFileOperation()
    operation.hwnd = window_handle or None
    operation.wFunc = 3
    operation.pFrom = ctypes.cast(source, ctypes.c_void_p)
    operation.fFlags = 0x0040 | 0x0004 | 0x0010 | 0x0400 | 0x4000
    function = ctypes.windll.shell32.SHFileOperationW
    function.argtypes = [ctypes.POINTER(_ShellFileOperation)]
    function.restype = ctypes.c_int
    code = function(ctypes.byref(operation))
    if code != 0:
        raise OSError(f"Windows could not move {name} to the Recycle Bin (code {code}).")
    if operation.fAnyOperationsAborted:
        raise OSError(f"Moving {name} to the Recycle Bin was cancelled.")
    if os.path.lexists(folder):
        raise OSError(f"{name} is still there: Windows did not move it to the Recycle Bin.")
def _folder_delete_refusal(folder: Path) -> str:
    refusal = _folder_edit_refusal(folder, "deleted")
    if not refusal and folder_is_link(folder):
        refusal = f"{folder.name} is a link to another folder; it is not deleted here."
    return refusal
def _folder_is_empty(folder) -> bool:
    with os.scandir(folder) as entries:
        return next(entries, None) is None
_FOLDER_DELETE_WHILE_SCANNING = 'A scan is running. A folder with data can be deleted here once it has ended.'
def folder_delete_question(folder) -> tuple[str, str]:
    folder = Path(folder)
    refusal = _folder_delete_refusal(folder)
    if refusal:
        raise ValueError(refusal)
    if _folder_is_empty(folder):
        return "empty", f"Delete the empty folder {folder.name}?"
    if open_scan_runs():
        raise ValueError(_FOLDER_DELETE_WHILE_SCANNING)
    files, folders, size, complete = folder_contents(folder)
    if not complete:
        raise ValueError(
            f"{folder.name} holds more than is counted here ({FOLDER_DIALOG_COUNT_LIMIT} files and "
            "folders), or a part of it cannot be read or is a link to another folder. Delete it "
            "in the Windows Explorer.")
    holds = f"{files} file{('s' if files != 1 else '')} and {folders} folder{('s' if folders != 1 else '')} ({_folder_size_text(size)})"
    no_bin = recycle_bin_refusal(folder, size)
    if no_bin:
        raise ValueError(f'{folder.name} holds {holds}. {no_bin}, so only an empty folder can be deleted in this window.')
    return ('recycle', f'Delete the folder {folder.name}?\n\nIt holds {holds}. Windows moves it to the Recycle Bin, where it can be restored.')
def delete_subfolder(folder, how: str, window_handle: int = 0) -> str:
    folder = Path(folder)
    refusal = _folder_delete_refusal(folder)
    if refusal:
        raise ValueError(refusal)
    if how == "empty":
        os.rmdir(folder)
        return ""
    if how != "recycle":
        raise ValueError(f"Unknown way to delete a folder: {how!r}")
    if open_scan_runs():
        raise ValueError(_FOLDER_DELETE_WHILE_SCANNING)
    no_bin = recycle_bin_refusal(folder)
    if no_bin:
        raise ValueError(f"{no_bin}, so only an empty folder can be deleted in this window.")
    before = recycle_bin_item_count(folder)
    move_folder_to_recycle_bin(folder, window_handle)
    after = recycle_bin_item_count(folder)
    if before is not None and after is not None and after <= before:
        return f'{folder.name} is gone. The Recycle Bin did not gain an item, so check that it is in there: Windows may have deleted it for good.'
    return ""
def folder_dialog_start_directory(initialdir="") -> Path:
    for text in (initialdir, _FOLDER_DIALOG_LAST_DIRECTORY):
        if not str(text or "").strip():
            continue
        path = Path(os.path.abspath(str(text)))
        for candidate in (path, *path.parents):
            if candidate.is_dir():
                return candidate
    return Path(os.path.abspath(os.getcwd()))
class EditableFolderDialog:
    def __init__(self, parent, title="", initialdir="", mustexist=False) -> None:
        self.parent = parent
        self.mustexist = bool(mustexist)
        self.result = ""
        self.directory = folder_dialog_start_directory(initialdir)
        self.names: list[str] = []
        self.top = top = tk.Toplevel(parent, class_="EditableFolderDialog")
        try:
            self._build(str(title or "Choose a folder"))
        except BaseException:
            try:
                top.destroy()
            except tk.TclError:
                pass
            raise
    def _build(self, title: str) -> None:
        top = self.top
        top.withdraw()
        top.title(title)
        top.folder_dialog = self
        self.done = tk.BooleanVar(top, value=False)
        self.directory_var = tk.StringVar(top)
        self.selection_var = tk.StringVar(top)
        self.status_var = tk.StringVar(top)
        frame = ttk.Frame(top, padding=10, name="body")
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="Directory:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.directory_box = ttk.Combobox(frame, textvariable=self.directory_var, name="directory")
        self.directory_box.grid(row=0, column=1, sticky="ew")
        self.up_button = ttk.Button(frame, text="Up", width=6, name="up", command=self.go_up)
        self.up_button.grid(row=0, column=2, sticky="e", padx=(6, 0))
        box = ttk.Frame(frame)
        box.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=8)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(box, name='folders', width=64, height=14, activestyle='none', exportselection=False, selectmode='browse')
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.status_label = ttk.Label(frame, textvariable=self.status_var, name='status', wraplength=520, justify='left')
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.status_label.grid_remove()
        ttk.Label(frame, text="Selection:").grid(row=3, column=0, sticky="w", padx=(0, 6))
        self.selection_entry = ttk.Entry(frame, textvariable=self.selection_var, name="selection")
        self.selection_entry.grid(row=3, column=1, columnspan=2, sticky="ew")
        bar = ttk.Frame(frame, name="buttons")
        bar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.new_button = ttk.Button(bar, text="New folder", name="new", command=self.new_folder)
        self.rename_button = ttk.Button(bar, text="Rename", name="rename", command=self.rename_selected)
        self.delete_button = ttk.Button(bar, text="Delete", name="delete", command=self.delete_selected)
        for button in (self.new_button, self.rename_button, self.delete_button):
            button.pack(side="left", padx=(0, 6))
        self.cancel_button = ttk.Button(bar, text="Cancel", name="cancel", command=self.cancel)
        self.cancel_button.pack(side="right")
        self.ok_button = ttk.Button(bar, text="OK", name="ok", command=self.ok)
        self.ok_button.pack(side="right", padx=(0, 6))
        self.listbox.bind("<<ListboxSelect>>", lambda _event: self._selection_changed())
        self.listbox.bind("<Double-Button-1>", lambda _event: self.open_selected())
        self.listbox.bind("<Return>", lambda _event: self.open_selected())
        self.listbox.bind("<BackSpace>", lambda _event: self.go_up())
        self.listbox.bind("<F2>", lambda _event: self.rename_selected())
        self.listbox.bind("<Delete>", lambda _event: self.delete_selected())
        self.directory_box.bind("<<ComboboxSelected>>", lambda _event: self.go_to(self.directory_var.get()))
        self.directory_box.bind("<Return>", lambda _event: self.go_to(self.directory_var.get()))
        self.selection_entry.bind("<Return>", lambda _event: self.ok())
        top.bind("<Escape>", lambda _event: self.cancel())
        top.protocol("WM_DELETE_WINDOW", self.cancel)
        top.bind("<Destroy>", self._destroyed)
        self.show_directory(self.directory)
    def _destroyed(self, event) -> None:
        if event.widget is self.top:
            try:
                self.done.set(True)
            except tk.TclError:
                pass
    def selected_folder(self) -> Path | None:
        picked = self.listbox.curselection()
        if not picked or picked[0] >= len(self.names):
            return None
        return self.directory / self.names[picked[0]]
    def _volumes(self) -> list[str]:
        try:
            return [str(volume) for volume in self.top.tk.splitlist(self.top.tk.call("file", "volumes"))]
        except tk.TclError:
            return []
    def show_directory(self, directory, select: str = "") -> None:
        self.directory = Path(directory)
        try:
            self.names = list_subfolders(self.directory)
            problem = ""
        except OSError as error:
            self.names = []
            problem = f"This folder cannot be listed: {error}"
        self.listbox.delete(0, "end")
        for name in self.names:
            self.listbox.insert("end", name)
        self.status_var.set(problem)
        if problem:
            self.status_label.grid()
        else:
            self.status_label.grid_remove()
        shown = self.directory.as_posix()
        above = [path.as_posix() for path in self.directory.parents]
        values = [shown, *above]
        self.directory_box.configure(values=values + [v for v in self._volumes() if v not in values])
        self.directory_var.set(shown)
        if select in self.names:
            index = self.names.index(select)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        self._selection_changed()
    def _selection_changed(self) -> None:
        folder = self.selected_folder()
        self.selection_var.set((folder or self.directory).as_posix())
        state = ["!disabled"] if folder is not None else ["disabled"]
        self.rename_button.state(state)
        self.delete_button.state(state)
        self.up_button.state(["disabled"] if self.directory.parent == self.directory else ["!disabled"])
    def go_to(self, text) -> bool:
        path = self._typed_path(text)
        if path is None or not path.is_dir():
            self._error("Choose a folder", f"{str(text).strip() or 'That'} is not a folder.")
            self.directory_var.set(self.directory.as_posix())
            return False
        self.show_directory(path)
        return True
    def go_up(self) -> None:
        if self.directory.parent != self.directory:
            self.show_directory(self.directory.parent, select=self.directory.name)
    def open_selected(self) -> None:
        folder = self.selected_folder()
        if folder is not None:
            self.show_directory(folder)
    def _typed_path(self, text) -> Path | None:
        text = str(text).strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute():
            path = self.directory / path
        return Path(os.path.abspath(path))
    def _regrab(self) -> None:
        try:
            if int(self.top.winfo_exists()):
                self.top.grab_set()
                self.listbox.focus_set()
        except tk.TclError:
            pass
    def _error(self, title: str, text: str) -> None:
        try:
            messagebox.showerror(title, text, parent=self.top)
        finally:
            self._regrab()
    def _ask_name(self, title: str, prompt: str, initial: str = "") -> str | None:
        try:
            return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.top)
        finally:
            self._regrab()
    def new_folder(self) -> None:
        name = self._ask_name("New folder", f"Name of the new folder in {self.directory.as_posix()}:")
        if name is None:
            return
        try:
            folder = make_subfolder(self.directory, name.strip())
        except (ValueError, OSError) as error:
            self._error("New folder", f"The folder was not made: {error}")
            return
        self.show_directory(self.directory, select=folder.name)
    def rename_selected(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            return
        name = self._ask_name("Rename folder", f"New name for {folder.name}:", folder.name)
        if name is None or name.strip() == folder.name:
            return
        try:
            renamed = rename_subfolder(folder, name.strip())
        except (ValueError, OSError) as error:
            self._error("Rename folder", f"{folder.name} was not renamed: {error}")
            self.show_directory(self.directory, select=folder.name)
            return
        self.show_directory(self.directory, select=renamed.name)
    def delete_selected(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            return
        try:
            how, question = folder_delete_question(folder)
        except (ValueError, OSError) as error:
            self._error("Delete folder", f"{folder.name} was not deleted: {error}")
            return
        try:
            agreed = messagebox.askyesno('Delete folder', question, default='no', icon='warning', parent=self.top)
        finally:
            self._regrab()
        if not agreed:
            return
        try:
            warning = delete_subfolder(folder, how, self._window_handle())
        except (ValueError, OSError) as error:
            self._error("Delete folder", f"{folder.name} was not deleted: {error}")
            warning = ""
        self.show_directory(self.directory)
        if warning:
            try:
                messagebox.showwarning("Delete folder", warning, parent=self.top)
            finally:
                self._regrab()
    def _window_handle(self) -> int:
        if sys.platform != "win32":
            return 0
        try:
            parent = ctypes.windll.user32.GetParent
            parent.argtypes, parent.restype = [ctypes.c_void_p], ctypes.c_void_p
            return int(parent(self.top.winfo_id()) or 0)
        except Exception:
            return 0
    def ok(self) -> None:
        path = self._typed_path(self.selection_var.get()) or self.directory
        if not path.is_dir():
            if os.path.lexists(path):
                self._error("Choose a folder", f"{path.as_posix()} is not a folder.")
                return
            if self.mustexist:
                self._error("Choose a folder", f"{path.as_posix()} does not exist.")
                return
            try:
                agreed = messagebox.askyesno('Choose a folder', f'{path.as_posix()} does not exist. Make it?', parent=self.top)
            finally:
                self._regrab()
            if not agreed:
                return
            try:
                path.mkdir(parents=True)
            except OSError as error:
                self._error("Choose a folder", f"The folder was not made: {error}")
                return
        result = path.as_posix()
        self.result = result.rstrip("/") if result.startswith("//") else result
        self.done.set(True)
    def cancel(self) -> None:
        self.result = ""
        self.done.set(True)
    def show(self) -> str:
        global _FOLDER_DIALOG_LAST_DIRECTORY
        top, parent = self.top, self.parent
        try:
            owner = parent.winfo_toplevel()
            if owner.winfo_viewable():
                top.transient(owner)
        except tk.TclError:
            owner = None
        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        try:
            top.minsize(min(width, 420), min(height, 300))
            if owner is not None and owner.winfo_viewable():
                x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
                y = owner.winfo_rooty() + (owner.winfo_height() - height) // 3
            else:
                x = (top.winfo_screenwidth() - width) // 2
                y = (top.winfo_screenheight() - height) // 3
            x = max(0, min(x, top.winfo_screenwidth() - width))
            y = max(0, min(y, top.winfo_screenheight() - height - 40))
            top.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
        top.deiconify()
        try:
            top.lift()
            top.focus_force()
        except tk.TclError:
            pass
        try:
            previous_grab = str(top.tk.call("grab", "current", top._w)) or None
        except tk.TclError:
            previous_grab = None
        try:
            top.grab_set()
        except tk.TclError:
            top.after(100, self._regrab)
        self.listbox.focus_set()
        try:
            top.wait_variable(self.done)
            return self.result
        finally:
            _FOLDER_DIALOG_LAST_DIRECTORY = str(self.directory)
            try:
                top.grab_release()
                top.destroy()
            except tk.TclError:
                pass
            if previous_grab is not None:
                try:
                    if int(parent.tk.call('winfo', 'exists', previous_grab)) and int(parent.tk.call('winfo', 'viewable', previous_grab)):
                        parent.tk.call("grab", "set", previous_grab)
                except tk.TclError:
                    pass
def editable_folder_dialog(**options) -> str:
    parent = options.get("parent") or getattr(tk, "_default_root", None)
    if not isinstance(parent, tk.Misc) or _NATIVE_ASK_DIRECTORY is None:
        if _NATIVE_ASK_DIRECTORY is None:
            raise RuntimeError("A folder dialog needs a Tk window")
        return _NATIVE_ASK_DIRECTORY(**options)
    try:
        dialog = EditableFolderDialog(parent, options.get('title', ''), options.get('initialdir', ''), options.get('mustexist', False))
    except Exception:
        return _NATIVE_ASK_DIRECTORY(**options)
    return dialog.show()
def use_english_tk_dialogs(root: tk.Misc) -> bool:
    import tkinter.messagebox as tk_messagebox
    global _NATIVE_MESSAGE_SHOW
    if hasattr(tk_messagebox, "_show"):
        native = getattr(tk_messagebox, "_english_native_show", None)
        if native is None:
            native = tk_messagebox._english_native_show = tk_messagebox._show
        _NATIVE_MESSAGE_SHOW = native
        tk_messagebox._show = english_message_box
    import tkinter.filedialog as tk_filedialog
    global _NATIVE_ASK_DIRECTORY
    native_folder = getattr(tk_filedialog, "_english_native_askdirectory", None)
    if native_folder is None:
        native_folder = tk_filedialog._english_native_askdirectory = tk_filedialog.askdirectory
    _NATIVE_ASK_DIRECTORY = native_folder
    tk_filedialog.askdirectory = editable_folder_dialog
    try:
        system = str(root.tk.call("tk", "windowingsystem"))
        if system == "x11":
            root.tk.call("package", "require", "msgcat")
            root.tk.call("::msgcat::mclocale", "en")
            return True
        root.tk.eval(_ENGLISH_TK_DIALOGS)
        return True
    except tk.TclError as error:
        print(f"Tk's own dialogs are not available; the system's are used: {error}", file=sys.stderr)
        return False
def configure_tk_font_system(root: tk.Misc) -> tuple[str, str]:
    installed = tuple(tkfont.families(root))
    installed_lookup = {name.casefold(): name for name in installed}
    def choose(candidates: tuple[str, ...], fallback: str) -> str:
        for candidate in candidates:
            actual = installed_lookup.get(candidate.casefold())
            if actual is not None:
                return actual
        return fallback
    default_font = tkfont.nametofont("TkDefaultFont", root=root)
    fixed_font = tkfont.nametofont("TkFixedFont", root=root)
    default_family = str(default_font.actual("family"))
    fixed_family = str(fixed_font.actual("family"))
    if sys.platform.startswith("win"):
        ui_candidates = ('Microsoft YaHei UI', 'Microsoft YaHei', 'DengXian', 'Segoe UI')
        mono_candidates = ("Cascadia Mono", "Consolas", "Courier New")
    elif sys.platform == "darwin":
        ui_candidates = ("PingFang SC", "Heiti SC", "Helvetica Neue")
        mono_candidates = ("SF Mono", "Menlo", "Monaco")
    else:
        ui_candidates = ('Noto Sans CJK SC', 'Noto Sans SC', 'WenQuanYi Micro Hei', 'DejaVu Sans')
        mono_candidates = ("Noto Sans Mono CJK SC", "DejaVu Sans Mono")
    ui_family = choose(ui_candidates, default_family)
    mono_family = choose(mono_candidates, fixed_family)
    top_level = root.winfo_toplevel()
    ui_scale = float(getattr(top_level, "_moke_ui_scale", 1.0))
    normal_size = max(8, min(12, int(round(10 * ui_scale))))
    small_size = max(8, min(11, int(round(9 * ui_scale))))
    named_specs = {
        "TkDefaultFont": (ui_family, normal_size, "normal"),
        "TkTextFont": (ui_family, normal_size, "normal"),
        "TkMenuFont": (ui_family, normal_size, "normal"),
        "TkHeadingFont": (ui_family, normal_size, "bold"),
        "TkCaptionFont": (ui_family, normal_size, "normal"),
        "TkSmallCaptionFont": (ui_family, small_size, "normal"),
        "TkIconFont": (ui_family, normal_size, "normal"),
        "TkTooltipFont": (ui_family, small_size, "normal"),
        "TkFixedFont": (mono_family, small_size, "normal"),
    }
    available_named_fonts = set(tkfont.names(root))
    for font_name, (family, size, weight) in named_specs.items():
        if font_name not in available_named_fonts:
            continue
        tkfont.nametofont(font_name, root=root).configure(family=family, size=size, weight=weight)
    root.option_add("*Font", "TkDefaultFont", "widgetDefault")
    root.option_add("*Menu.Font", "TkMenuFont", "widgetDefault")
    setattr(top_level, "_moke_ui_font_family", ui_family)
    setattr(top_level, "_moke_mono_font_family", mono_family)
    setattr(top_level, "_moke_ui_font_size", normal_size)
    setattr(top_level, "_moke_ui_small_font_size", small_size)
    return ui_family, mono_family
THEME_COLORS = {
    "page": "#eef1f5",
    "surface": "#ffffff",
    "border": "#d5dbe3",
    "text": "#1f2933",
    "muted": "#5f6b76",
    "accent": "#0068b5",
    "accent_active": "#00589a",
    "accent_pressed": "#004a82",
    "danger": "#c62828",
    "danger_active": "#b71c1c",
    "danger_pressed": "#8e0000",
    "on_fill": "#ffffff",
    "disabled_fill": "#e0e0e0",
    "disabled_text": "#9e9e9e",
    "button": "#f5f7fa",
    "button_active": "#e8edf3",
    "tab": "#e2e7ee",
    "success": "#087a55",
    "warning": "#a65300",
    "status": "#eaf0f5",
    "plot_background": "#ffffff",
    "plot_border": "#cfd6dd",
    "plot_grid": "#e3e7eb",
    "plot_series": "#0068b5",
}
LAMP_SIZE_PX = 16
LAMP_DOT_PX = 10
LAMP_COLORS = {'on': ('#20b15a', '#10783a'), 'off': ('#9aa0a6', '#6f757b'), 'fault': ('#e53935', '#9d1d1a'), 'warning': ('#f9a825', '#a86f00')}
BUTTON_BASE_STYLES = ("TButton", "Compact.TButton", "Inline.TButton")
BUTTON_ROLES = ("Accent", "Danger")
NATIVE_BUTTON_THEMES = ("vista", "xpnative", "winnative", "aqua")
BUTTON_ROLE_NEUTRAL_WORDS = ("recording",)
def button_role(text) -> str | None:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    if not words or any(word in words for word in BUTTON_ROLE_NEUTRAL_WORDS):
        return None
    if words[0] in ("stop", "zero") or "stop" in words or "off" in words:
        return "Danger"
    if "start" in words:
        return "Accent"
    return None
def button_role_style(role: str, base: str = "TButton") -> str:
    return f"{role}.{base or 'TButton'}"
def lamp_state(connected, fault: bool = False) -> str:
    if fault:
        return "fault"
    return "on" if connected else "off"
def parse_ttk_padding(value) -> tuple[int, int, int, int]:
    if isinstance(value, (tuple, list)):
        parts = [str(part) for part in value]
    else:
        parts = str(value or "").split()
    numbers = []
    for part in parts:
        numbers.extend(int(round(float(piece))) for piece in str(part).split())
    if not numbers:
        return (0, 0, 0, 0)
    left = numbers[0]
    top = numbers[1] if len(numbers) > 1 else left
    right = numbers[2] if len(numbers) > 2 else left
    bottom = numbers[3] if len(numbers) > 3 else top
    return (left, top, right, bottom)
def matched_padding(padding, base_size, role_size):
    left, top, right, bottom = padding
    dw = int(base_size[0]) - int(role_size[0])
    dh = int(base_size[1]) - int(role_size[1])
    result = (left + dw // 2, top + dh // 2, right + dw - dw // 2, bottom + dh - dh // 2)
    return result if min(result) >= 0 else None
def theme_surface_of(widget) -> str:
    if not isinstance(widget, tk.Misc):
        return THEME_COLORS["surface"]
    current = widget
    try:
        for _depth in range(100):
            if not isinstance(current, tk.Misc):
                break
            kind = current.winfo_class()
            if kind in ("TLabelframe", "Labelframe"):
                return THEME_COLORS["surface"]
            if kind in ("Toplevel", "Tk"):
                break
            current = current.master
    except (tk.TclError, AttributeError):
        return THEME_COLORS["surface"]
    return THEME_COLORS["page"]
def create_lamp(parent, *, height=None, kind: str = "connection", background=None):
    height = max(LAMP_SIZE_PX, int(height or LAMP_SIZE_PX))
    canvas = tk.Canvas(parent, width=LAMP_SIZE_PX, height=height, highlightthickness=0, background=background or theme_surface_of(parent))
    middle, centre, radius = height // 2, LAMP_SIZE_PX // 2, LAMP_DOT_PX // 2
    fill, outline = LAMP_COLORS["off"]
    item = canvas.create_oval(centre - radius, middle - radius, centre + radius, middle + radius, fill=fill, outline=outline)
    register_lamp(canvas, kind)
    return canvas, item
def register_lamp(canvas, kind: str = "connection") -> None:
    try:
        canvas._lamp_kind = kind
        canvas._lamp_state = getattr(canvas, "_lamp_state", "off")
        top = canvas.winfo_toplevel()
        top.__dict__.setdefault("_look_lamps", []).append(canvas)
    except (tk.TclError, AttributeError, TypeError):
        pass
def set_lamp(target, state: str) -> None:
    if target is None:
        return
    canvas, item = target
    fill, outline = LAMP_COLORS.get(state, LAMP_COLORS["off"])
    try:
        canvas.itemconfigure(item, fill=fill, outline=outline)
    except tk.TclError:
        return
    try:
        changed = getattr(canvas, "_lamp_state", None) != state
        canvas._lamp_state = state
        if changed and threading.current_thread() is threading.main_thread():
            refresh = getattr(canvas.winfo_toplevel(), "_look_overview_refresh", None)
            if callable(refresh):
                refresh()
    except (tk.TclError, AttributeError, TypeError):
        pass
def configure_button_roles(root, style, *, probe_text: str = "Start scan") -> dict[str, bool]:
    colors = THEME_COLORS
    fills = {'Accent': (colors['accent'], colors['accent_active'], colors['accent_pressed']), 'Danger': (colors['danger'], colors['danger_active'], colors['danger_pressed'])}
    native = style.theme_use() in NATIVE_BUTTON_THEMES
    border = None
    if native:
        try:
            if "Look.Button.border" not in style.element_names():
                style.element_create("Look.Button.border", "from", "default", "border")
            border = "Look.Button.border"
        except tk.TclError:
            border = None
    result = {}
    for base in BUTTON_BASE_STYLES:
        try:
            base_layout = style.layout(base)
        except tk.TclError:
            base_layout = style.layout("TButton")
        for role in BUTTON_ROLES:
            name = button_role_style(role, base)
            fill, active, pressed = fills[role]
            filled = not native or border is not None
            if filled:
                if native:
                    outer = list(base_layout[0]) if base_layout else ["", {}]
                    outer[0] = border
                    style.layout(name, [tuple(outer)] + list(base_layout[1:]))
                style.configure(name, background=fill, foreground=colors['on_fill'], bordercolor=fill, lightcolor=fill, darkcolor=fill, focuscolor=colors['on_fill'])
                if native:
                    style.configure(name, relief="flat", borderwidth=1)
                style.map(name,
                          background=[("disabled", colors["disabled_fill"]), ("pressed", pressed), ("active", active)],
                          bordercolor=[("disabled", colors["disabled_fill"]), ("pressed", pressed), ("active", active)],
                          lightcolor=[("disabled", colors["disabled_fill"]), ("pressed", pressed), ("active", active)],
                          darkcolor=[("disabled", colors["disabled_fill"]), ("pressed", pressed), ("active", active)],
                          foreground=[("disabled", colors["disabled_text"])])
                if native:
                    filled = _calibrate_role_padding(root, style, base, name)
            if not filled:
                style.layout(name, base_layout)
                style.configure(name, foreground=fill, padding=parse_ttk_padding(style.lookup(base, 'padding')))
                style.map(name, foreground=[("disabled", colors["disabled_text"])])
            result[name] = filled
    return result
def _calibrate_role_padding(root, style, base: str, name: str, probe_text: str = "Start scan") -> bool:
    try:
        def size(style_name):
            probe = ttk.Button(root, text=probe_text, style=style_name)
            try:
                return int(probe.winfo_reqwidth()), int(probe.winfo_reqheight())
            finally:
                probe.destroy()
        padding = parse_ttk_padding(style.lookup(base, "padding"))
        style.configure(name, padding=padding)
        target = size(base)
        new = matched_padding(padding, target, size(name))
        if new is None:
            return False
        style.configure(name, padding=new)
        return size(name) == target
    except (tk.TclError, TypeError, ValueError, AttributeError):
        return False
def apply_look_to_tree(widget, style) -> None:
    page, surface = THEME_COLORS["page"], THEME_COLORS["surface"]
    made = set()
    def page_style(base: str) -> str:
        name = "Page." + base
        if name not in made:
            style.configure(name, background=page)
            if base.endswith(("TCheckbutton", "TRadiobutton")):
                style.map(name, background=[("active", page)])
            made.add(name)
        return name
    def walk(parent, in_card: bool) -> None:
        for child in parent.winfo_children():
            try:
                kind = child.winfo_class()
                if kind in ("Toplevel", "Tk"):
                    continue
                card = in_card or kind == "TLabelframe"
                if kind == "TButton":
                    current = str(child.cget("style")) or "TButton"
                    role = button_role(child.cget("text")) if current in BUTTON_BASE_STYLES else None
                    if role:
                        child.configure(style=button_role_style(role, current))
                elif kind in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton") and not in_card:
                    current = str(child.cget("style")) or kind
                    own = kind == "TLabel" and str(child.cget("background"))
                    if not own and (not current.startswith('Page.')) and (str(style.lookup(current, 'background')).lower() == surface):
                        child.configure(style=page_style(current))
                elif kind == "Canvas" and getattr(child, "_lamp_kind", None):
                    child.configure(background=surface if in_card else page)
            except (tk.TclError, AttributeError):
                continue
            walk(child, card)
    try:
        walk(widget, False)
    except (tk.TclError, AttributeError, TypeError):
        pass
def connection_overview_text(pages) -> tuple[str, str, str]:
    states = [state for page_states in pages.values() for state in page_states]
    connected = sum(state in ("on", "warning", "fault") for state in states)
    total = len(states)
    lamp = "fault" if "fault" in states else "on" if connected else "off"
    return (f"{connected}/{total}", f"{connected} of {total} instruments connected", lamp)
def connection_overview_lines(pages) -> list[str]:
    lines = []
    for page, states in pages.items():
        if not states:
            continue
        connected = sum(state in ("on", "warning", "fault") for state in states)
        fault = " (fault)" if "fault" in states else ""
        lines.append(f"{page}: {connected} of {len(states)} connected{fault}")
    return lines
class ConnectionOverview:
    def __init__(self, root, notebook, *, pad: int = 4) -> None:
        self.root, self.notebook, self.pad = root, notebook, pad
        self.frame = ttk.Frame(root, style="Overview.TFrame", padding=(pad, 0, pad, 0))
        self.lamp = create_lamp(self.frame, kind="overview", background=THEME_COLORS["page"])
        self.lamp[0].pack(side="left", padx=(0, 4))
        self.text = tk.StringVar(value="")
        self.label = ttk.Label(self.frame, textvariable=self.text, style="Overview.TLabel")
        self.label.pack(side="left")
        self.tooltip = None
        self.pending = None
        self.texts = ("", "", "off")
        root._look_overview_refresh = self.schedule
        for widget in (self.frame, self.label, self.lamp[0]):
            widget.bind("<Enter>", self._show_tooltip, add="+")
            widget.bind("<Leave>", self._hide_tooltip, add="+")
        notebook.bind("<Configure>", lambda _event: self.schedule(), add="+")
        notebook.bind("<<NotebookTabChanged>>", lambda _event: self.schedule(), add="+")
        self.schedule()
    def pages(self) -> dict[str, list[str]]:
        tabs = {}
        for tab in self.notebook.tabs():
            tabs[str(tab)] = str(self.notebook.tab(tab, "text"))
        pages = {name: [] for name in tabs.values()}
        alive = []
        for canvas in self.root.__dict__.get("_look_lamps", []):
            try:
                if not canvas.winfo_exists():
                    continue
            except tk.TclError:
                continue
            alive.append(canvas)
            if getattr(canvas, "_lamp_kind", "") != "connection":
                continue
            path = str(canvas)
            for tab, name in tabs.items():
                if path.startswith(tab + "."):
                    pages[name].append(getattr(canvas, "_lamp_state", "off"))
                    break
        self.root._look_lamps = alive
        return pages
    def schedule(self) -> None:
        if self.pending is None:
            try:
                self.pending = self.root.after_idle(self.refresh)
            except tk.TclError:
                self.pending = None
    def refresh(self) -> None:
        self.pending = None
        try:
            self.texts = connection_overview_text(self.pages())
            set_lamp(self.lamp, self.texts[2])
            self._place()
        except tk.TclError:
            pass
    def _tab_row_height(self) -> int:
        x = 12
        found = 0
        for y in range(0, 80, 2):
            if self._tab_at(x, y):
                found = y
            elif found:
                break
        return found + 2 if found else 0
    def _tab_at(self, x: int, y: int) -> bool:
        return str(self.notebook.tk.call(str(self.notebook), "index", f"@{x},{y}")) != ""
    def _place(self) -> None:
        width = self.notebook.winfo_width()
        height = self._tab_row_height()
        self.frame.place_forget()
        if width <= 1 or height < LAMP_SIZE_PX:
            return
        for text in self.texts[1], self.texts[0]:
            self.text.set(text)
            need = 2 * self.pad + LAMP_SIZE_PX + 4 + self.label.winfo_reqwidth()
            left = width - self.pad - need
            if left > 0 and (not any((self._tab_at(x, y) for x in range(left - 6, width, 4) for y in (2, height // 2, height - 3)))):
                self.frame.place(in_=self.notebook, x=width - self.pad, y=0, anchor="ne", height=height)
                return
    def _show_tooltip(self, _event=None) -> None:
        self._hide_tooltip()
        lines = connection_overview_lines(self.pages()) or ["No instruments"]
        try:
            tip = tk.Toplevel(self.root)
            tip.wm_overrideredirect(True)
            ttk.Label(tip, text="\n".join(lines), style="Tooltip.TLabel", justify="left").pack()
            x = self.frame.winfo_rootx() + self.frame.winfo_width() - 4
            y = self.frame.winfo_rooty() + self.frame.winfo_height() + 2
            tip.update_idletasks()
            tip.wm_geometry(f"+{max(0, x - tip.winfo_reqwidth())}+{y}")
            self.tooltip = tip
        except tk.TclError:
            self.tooltip = None
    def _hide_tooltip(self, _event=None) -> None:
        if self.tooltip is not None:
            try:
                self.tooltip.destroy()
            except tk.TclError:
                pass
            self.tooltip = None
def force_english_input_mode(widget=None) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        imm32 = ctypes.WinDLL("imm32", use_last_error=True)
        user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)]
        user32.GetKeyboardLayoutList.restype = ctypes.c_int
        user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE
        user32.ActivateKeyboardLayout.argtypes = [wintypes.HANDLE, wintypes.UINT]
        user32.ActivateKeyboardLayout.restype = wintypes.HANDLE
        imm32.ImmGetContext.argtypes = [wintypes.HWND]
        imm32.ImmGetContext.restype = wintypes.HANDLE
        imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
        imm32.ImmSetOpenStatus.restype = wintypes.BOOL
        imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm32.ImmReleaseContext.restype = wintypes.BOOL
        count = int(user32.GetKeyboardLayoutList(0, None))
        english_layout = None
        if count > 0:
            layouts = (wintypes.HANDLE * count)()
            actual = int(user32.GetKeyboardLayoutList(count, layouts))
            for index in range(actual):
                handle_value = int(layouts[index] or 0)
                language_id = handle_value & 0xFFFF
                if (language_id & 0x03FF) == 0x0009:
                    english_layout = layouts[index]
                    break
        if not english_layout:
            english_layout = user32.LoadKeyboardLayoutW("00000409", 0x00000001)
        if english_layout:
            user32.ActivateKeyboardLayout(english_layout, 0)
        if widget is not None:
            hwnd = wintypes.HWND(int(widget.winfo_id()))
            input_context = imm32.ImmGetContext(hwnd)
            if input_context:
                try:
                    imm32.ImmSetOpenStatus(input_context, False)
                finally:
                    imm32.ImmReleaseContext(hwnd, input_context)
        return True
    except BaseException:
        return False
try:
    import numpy as np
except ImportError:
    np = None
try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None
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
APP_VERSION = "1.0"
MAIN_WINDOW_TITLE = "Transport_PPMS_NH"
APPLICATION_ICON_BASENAME = "Transport_PPMS"
def program_directory() -> Path:
    frozen = getattr(sys, "frozen", False) or "__compiled__" in globals()
    return Path(sys.argv[0] if frozen else _PROGRAM_FILE).resolve().parent
PROGRAM_DIRECTORY = program_directory()
def _application_asset_path(filename: str) -> Path | None:
    candidates: list[Path] = []
    for directory in (Path(_PROGRAM_FILE).resolve().parent, Path(sys.argv[0]).resolve().parent, Path(sys.executable).resolve().parent):
        candidates.extend((directory / "assets" / filename, directory / filename))
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None
def apply_application_identity(window: tk.Misc) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('NH.ProfChen.TransportPPMS')
        except BaseException:
            pass
    icon_png = _application_asset_path(f"{APPLICATION_ICON_BASENAME}_icon.png")
    if icon_png is not None:
        try:
            icon_photo = tk.PhotoImage(master=window, file=str(icon_png))
            window.winfo_toplevel().iconphoto(True, icon_photo)
            setattr(window.winfo_toplevel(), "_transport_application_icon", icon_photo)
        except (OSError, tk.TclError):
            pass
    if sys.platform == "win32":
        icon_ico = _application_asset_path(f"{APPLICATION_ICON_BASENAME}.ico")
        if icon_ico is not None:
            try:
                window.winfo_toplevel().iconbitmap(default=str(icon_ico))
            except (OSError, tk.TclError):
                pass
UI_QUEUE_MAX_CALLBACKS_PER_TICK = 50
UI_QUEUE_TIME_BUDGET_S = 0.008
UI_QUEUE_ACTIVE_DELAY_MS = 1
UI_QUEUE_IDLE_DELAY_MS = 5
CONTROL_WIDTH_FRACTION = 0.43
WINDOW_GAP = 10
def create_zurich_chooser(parent, variable, *, match_chars: int | None = None, width: int = 16):
    variable.set(zurich_mfli_label(variable.get()))
    chooser = ttk.Combobox(parent, textvariable=variable, values=tuple(ZURICH_MFLI_CHOICES), state='readonly', width=match_chars or width)
    chooser.zurich_chooser = True
    chooser.zurich_shortfall = 0
    if not match_chars:
        return chooser
    try:
        probe = ttk.Entry(parent, width=match_chars)
        target = int(probe.winfo_reqwidth())
        probe.destroy()
        while int(chooser.cget("width")) > 8 and int(chooser.winfo_reqwidth()) > target:
            chooser.configure(width=int(chooser.cget("width")) - 1)
        spare = target - int(chooser.winfo_reqwidth())
        if spare > 0:
            style = ttk.Style(parent)
            raw = [int(round(float(str(value)))) for value in parent.tk.splitlist(style.lookup('TCombobox', 'padding'))] or [0]
            pad_left = raw[0]
            pad_top = raw[1] if len(raw) > 1 else pad_left
            pad_right = raw[2] if len(raw) > 2 else pad_left
            pad_bottom = raw[3] if len(raw) > 3 else pad_top
            name = f"Zurich{spare}.TCombobox"
            style.configure(name, padding=(pad_left, pad_top, pad_right + spare, pad_bottom))
            chooser.configure(style=name)
        chooser.zurich_shortfall = max(0, target - int(chooser.winfo_reqwidth()))
    except (tk.TclError, TypeError, ValueError, AttributeError):
        chooser.zurich_shortfall = 0
    return chooser
def ask_scan_start(parent, title, question, expected, warning='', **_options) -> bool:
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)
    top.resizable(False, False)
    body = ttk.Frame(top, padding=18)
    body.grid(row=0, column=0, sticky='nsew')
    base = tkfont.nametofont('TkDefaultFont')
    size = int(base.cget('size'))
    big = base.copy()
    big.configure(weight='bold', size=round(size * 1.4))
    row = 0
    if warning:
        ttk.Label(body, text=warning, foreground='#b00020', wraplength=460, justify='left').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        row = 1
    ttk.Label(body, text='Expected scan time:').grid(row=row, column=0, columnspan=2, sticky='w')
    ttk.Label(body, text=expected, foreground='#d00000', font=big).grid(row=row + 1, column=0, columnspan=2, sticky='w', pady=(4, 12))
    ttk.Label(body, text='Is this time OK? Start the scan?').grid(row=row + 2, column=0, columnspan=2, sticky='w', pady=(0, 14))
    answer = [False]
    def close(value: bool) -> None:
        answer[0] = value
        top.grab_release()
        top.destroy()
    yes = ttk.Button(body, text='Yes', width=10, command=lambda: close(True))
    yes.grid(row=row + 3, column=0, sticky='e', padx=(0, 8))
    ttk.Button(body, text='No', width=10, command=lambda: close(False)).grid(row=row + 3, column=1, sticky='w')
    body.columnconfigure(0, weight=1)
    top.protocol('WM_DELETE_WINDOW', lambda: close(False))
    top.bind('<Escape>', lambda _event: close(False))
    top.bind('<Return>', lambda _event: close(True))
    top.ask_scan_start_font = big
    top.update_idletasks()
    try:
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - top.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - top.winfo_reqheight()) // 3)
        top.geometry(f'+{x}+{y}')
    except tk.TclError:
        pass
    yes.focus_set()
    top.grab_set()
    top.wait_window()
    return answer[0]
def show_trimmed(label, variable) -> None:
    if not isinstance(label, tk.Widget):
        label.configure(textvariable=variable)
        return
    spec = str(label.cget('font')) or ttk.Style(label).lookup(label.winfo_class(), 'font') or 'TkDefaultFont'
    def measure(text: str) -> int:
        return int(label.tk.call("font", "measure", spec, "-displayof", label, text))
    def refresh(*_args) -> None:
        try:
            text = str(variable.get())
            room = label.winfo_width()
            if room <= 1 or measure(text) <= room:
                shown = text
            else:
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    if measure(text[:middle].rstrip() + "…") <= room:
                        low = middle
                    else:
                        high = middle - 1
                shown = text[:low].rstrip() + "…"
            if str(label.cget("text")) != shown:
                label.configure(text=shown)
        except tk.TclError:
            pass
    variable.trace_add("write", refresh)
    label.bind("<Configure>", refresh, add="+")
    refresh()
def widget_idle_state(widget) -> str:
    return "readonly" if getattr(widget, "zurich_chooser", False) is True else "normal"
PPMS_HARMONIC_COLORS = {"1": "#0072B2", "2": "#E69F00", "3": "#009E73"}
PPMS_PLOT_ROW_MIN_HEIGHT_PX = 70
PPMS_HELIUM_HISTORY_FILENAME = "PPMS_He_history.csv"
PPMS_SR830_DOWNRANGE_FRACTION = LOCKIN_DOWNRANGE_FRACTION
PPMS_SR830_DOWNRANGE_READINGS = 3
PPMS_SR830_RANGE_COOLDOWN_READINGS = 3
PPMS_SR830_RANGE_LIMIT_S = 180.0
PPMS_SR830_RANGE_LIMIT_STEPS = 36
PPMS_FIRST_POINT_MAX_WINDOWS = 10
PPMS_FIRST_POINT_TOLERANCE_FRACTION = 0.02
PPMS_FIRST_POINT_FLOOR_FRACTION = PPMS_FIRST_POINT_TOLERANCE_FRACTION * 2.5 * LOCKIN_DOWNRANGE_FRACTION
PPMS_METER_COLORS = {"830_a": "#0072B2", "830_b": "#E69F00", "830_c": "#009E73", "zurich": "#CC79A7"}
PPMS_ZURICH_TIME_CONSTANT_S = 1.0
PPMS_ACQUISITION_S = 1.0
PPMS_TEMPERATURE_ACQUISITION_S = 5.0
PPMS_ROTATOR_MOVE_ALLOWANCE_S = 1.0
PPMS_FIELD_NOT_ZEROED_TEXT = "field NOT zeroed: check MultiVu"
PPMS_SET_UP_ALLOWANCE_S = 12.0
PPMS_FREQUENCY_READBACK_TOL_HZ = 0.01
PPMS_ZURICH_LOCK_READS = 3
NONLOCAL_FSCAN_START_HZ = 7
NONLOCAL_FSCAN_STOP_HZ = 97
NONLOCAL_FSCAN_SETTLE_PERIODS = 5.0
NONLOCAL_IV6221_REFERENCE_WAIT_S = 20.0
PPMS_TEMPERATURE_DELAY_S = 0.0
PPMS_FIELD_RATE_DEFAULT_OE_S = 100.0
PPMS_SCAN_POLL_S = 0.5
PPMS_PERSISTENT_CONFIRM_S = 10.0
PPMS_PERSISTENT_MARGIN_S = 1800.0
PPMS_TRANSPORT_CURRENT_RMS_MA = 0.001
PPMS_TRANSPORT_FREQUENCY_HZ = 317.3
PPMS_TRANSPORT_COMPLIANCE_V = 50.0
PPMS_TRANSPORT_HARMONIC = 1
PPMS_TRANSPORT_REFERENCE_TIMEOUT_S = 8.0
PPMS_SR830_RESOURCE_OPTIONS = tuple((f'GPIB0::{address}::INSTR' for address in (8, 9, 10)))
PPMS_DEFAULT_RESOURCES = {'6221': 'GPIB0::30::INSTR', '830_a': 'GPIB0::8::INSTR', '830_b': 'GPIB0::9::INSTR', '830_c': 'GPIB0::10::INSTR', '2400': 'GPIB0::24::INSTR'}
PPMS_SR830_HARMONIC_ATTRS = {'830_a': 'ppms_transport_harmonic_a_var', '830_b': 'ppms_transport_harmonic_b_var', '830_c': 'ppms_transport_harmonic_c_var'}
PPMS_SR830_REFERENCE_SLOPE = 1
PPMS_SR830_DEFAULT_TIME_CONSTANT = "1 s"
class PPMSFieldScanError(RuntimeError):
    pass
class PPMSMotorScanError(RuntimeError):
    pass
class PPMSLockinRangeError(RuntimeError):
    pass
class PPMSScanCancelled(RuntimeError):
    pass
class PPMSMotorDebug:
    AXIS = "Horizontal Rotator"
    def __init__(self, controller):
        self.controller = controller
    def _method(self, name, count):
        instrument = self.controller.instrument
        if instrument is None:
            raise RuntimeError("Please connect QDInstrument first")
        methods = [m for m in instrument.GetType().GetMethods() if str(m.Name) == name and len(list(m.GetParameters())) == count]
        if len(methods) != 1:
            raise RuntimeError(f"{name}: no unique compatible interface found; please use 'Interface info' and send the log")
        return methods[0]
    def describe(self):
        with self.controller.lock:
            if self.controller.instrument is None:
                raise RuntimeError("Please connect QDInstrument first")
            lines = []
            for method in self.controller.instrument.GetType().GetMethods():
                if any(word in str(method.Name).lower() for word in ("position", "ppmsitem", "ppmscommand")):
                    lines.append(str(method))
                    for parameter in method.GetParameters():
                        typ = parameter.ParameterType
                        if getattr(typ, "IsByRef", False):
                            typ = typ.GetElementType()
                        if getattr(typ, "IsEnum", False):
                            import System
                            lines.append(f"  {typ}: " + ", ".join(str(n) for n in System.Enum.GetNames(typ)))
            return "\n".join(lines) or "No motor-related interface found"
    @staticmethod
    def _check_return(method, value):
        typ = str(getattr(getattr(method, "ReturnType", None), "FullName", ""))
        if typ == "System.Boolean":
            good = bool(value)
        else:
            good = value is None or int(value) == 0
        if not good:
            raise RuntimeError(f"{method}: returned {value!r} ({typ}); not resending, please verify in MultiVu whether it was executed")
    def _read_item(self, channel):
        import System
        with self.controller.lock:
            method = self._method("GetPPMSItem", 3)
            params = list(method.GetParameters())
            if (str(params[0].ParameterType.FullName) != "System.Int32"
                    or not getattr(params[1].ParameterType, "IsByRef", False)
                    or str(params[1].ParameterType.GetElementType().FullName) != "System.Double"
                    or str(params[2].ParameterType.FullName) != "System.Boolean"):
                raise RuntimeError(f"GetPPMSItem signature incompatible: {method}")
            arguments = System.Array[System.Object](3)
            arguments[0] = System.Int32(channel)
            arguments[1] = System.Double(0)
            arguments[2] = System.Boolean(True)
            result = method.Invoke(self.controller.instrument, arguments)
            self.controller.logger(f"PPMS motor GetPPMSItem({channel}): return={result!r}; value={arguments[1]!r}")
            self._check_return(method, result)
            value = float(arguments[1])
            if not math.isfinite(value):
                raise RuntimeError("PPMS data item is not a finite value")
            return value
    def read(self):
        with self.controller.lock:
            angle = self._read_item(3)
            status = "PositionUnknown"
            try:
                word = self._read_item(0)
                if not word.is_integer() or not 0 <= word <= 65535:
                    raise ValueError(f"Invalid status word: {word}")
                code = (int(word) >> 12) & 15
                status = {1: "AtTarget", 5: "Moving", 8: "AtLimitSwitch", 9: "AtIndexSwitch", 15: "PositionFailure"}.get(code, "PositionUnknown")
            except Exception as error:
                self.controller.logger(f"PPMS motor status unavailable: {error}")
            if not math.isfinite(angle):
                raise RuntimeError("Motor angle readback is not finite")
            self.controller.logger(f"PPMS motor: angle={angle:.6f} deg; status={status}")
            data = {"angle_deg": angle, "status": status}
            on_read = getattr(self, "on_read", None)
            if on_read is not None:
                on_read(dict(data))
            return data
    @staticmethod
    def validate_move(target, speed, current, allow_large=False):
        target, speed = float(target), float(speed)
        if not all(math.isfinite(v) for v in (target, speed, float(current))):
            raise ValueError("Motor parameters must be finite values")
        if not -370 <= target <= 370:
            raise ValueError("PPMS target angle must be within −370 to 370°")
        if abs(target - current) > 370.0 and not allow_large:
            raise ValueError("Each move must not exceed 370°; please enter the target based on the latest angle")
        if not speed.is_integer() or not 0 <= speed <= 14:
            raise ValueError("Deceleration code must be an integer 0–14; 0 fastest, 14 slowest")
        return target, int(speed)
    def move(self, target, speed, allow_large=False, _index_retry=False):
        import System
        self.movement_attempted = False
        with self.controller.lock:
            before = self.read()
            target, slowdown = self.validate_move(target, speed, before["angle_deg"], allow_large)
            if before["status"] not in ("AtTarget", "AtIndexSwitch"):
                raise RuntimeError(f"Motion status is not AtTarget: {before['status']}; MOVE not sent")
            method = self._method("SendPPMSCommand", 5)
            params = list(method.GetParameters())
            types_ = [p.ParameterType for p in params]
            expected = ["System.String", "System.String", "System.String", "System.Int32", "System.Double"]
            actual = [str((t.GetElementType() if getattr(t, "IsByRef", False) else t).FullName) for t in types_]
            if actual != expected or [bool(getattr(t, "IsByRef", False)) for t in types_] != [False, True, True, False, False]:
                raise RuntimeError(f"SendPPMSCommand signature incompatible: {method}")
            command = f"MOVE {target:.6f} 0 {slowdown}"
            args = System.Array[System.Object](5)
            args[0], args[1], args[2] = command, "", ""
            args[3], args[4] = System.Int32(0), System.Double(0)
            self.controller.logger(f"PPMS motor SEND ONCE: {command}")
            self.movement_attempted = True
            result = method.Invoke(self.controller.instrument, args)
            self.controller.logger(f"PPMS motor reply: return={result!r}; reply={args[1]!r}; error={args[2]!r}")
            return_type = str(getattr(method.ReturnType, "FullName", ""))
            ambiguous = return_type == "System.Int32" and int(result) == 1
            if not ambiguous:
                self._check_return(method, result)
            if str(args[1] or "").strip() or str(args[2] or "").strip():
                ambiguous = True
                self.controller.logger("MOVE reply is non-empty, execution result pending readback confirmation; MOVE not resent")
            return {'target_deg': target, 'return_code': result, 'ambiguous': ambiguous, 'raw_reply': str(args[1] or ''), 'raw_error': str(args[2] or '')}
    def move_and_verify(self, target, speed, cancelled=lambda: False, timeout=90.0, interval=0.5, allow_large=False, index_target=None):
        if cancelled():
            raise PPMSScanCancelled("Cancelled before moving, MOVE not sent; this does not mean the motor is stopped")
        submission = self.move(target, speed, allow_large=True) if allow_large else self.move(target, speed)
        deadline = time.monotonic() + timeout
        consecutive = 0
        last = None
        while time.monotonic() < deadline:
            if cancelled():
                raise PPMSScanCancelled("Arrival readback cancelled; this does not mean the motor is stopped, please check MultiVu")
            try:
                last = self.read()
            except Exception as error:
                raise RuntimeError(f"MOVE was sent, but the arrival readback failed and the motion status is unconfirmed; MOVE not resent, please check MultiVu: {error}") from error
            if last["status"] in ("AtLimitSwitch", "PositionFailure", "PositionUnknown"):
                raise RuntimeError(f"Abnormal motor status: {last}; MOVE not resent, please check MultiVu")
            if last["status"] in ("AtTarget", "AtIndexSwitch") and abs(last["angle_deg"] - submission["target_deg"]) <= 0.06:
                consecutive += 1
                if consecutive >= 2:
                    self.controller.logger(f"PPMS motor VERIFIED: {last}; submission={submission}")
                    return dict(last, **submission)
            else:
                consecutive = 0
            time.sleep(interval)
        raise RuntimeError(f"Motor arrival confirmation timed out ({timeout:g}s); last readback {last}; MOVE not resent, please check MultiVu")
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
def calculate_window_layout(root: tk.Tk):
    screen_width = max(640, int(root.winfo_screenwidth()))
    screen_height = max(480, int(root.winfo_screenheight()))
    if sys.platform.startswith("win"):
        taskbar_reserve = 48
    elif sys.platform == "darwin":
        taskbar_reserve = 30
    else:
        taskbar_reserve = 42
    usable_height = max(400, screen_height - taskbar_reserve)
    edge = max(4, int(round(min(screen_width, screen_height) * 0.008)))
    gap = max(6, int(round(WINDOW_GAP)))
    wide_split = screen_width >= 1700
    if wide_split:
        control_width = int(round(screen_width * CONTROL_WIDTH_FRACTION))
        control_width = max(720, min(control_width, screen_width - 620 - gap))
        control_x = max(edge, screen_width - control_width - edge)
        camera_x = edge
        camera_width = max(600, control_x - gap - camera_x)
    else:
        control_x = edge
        camera_x = edge
        control_width = max(480, screen_width - 2 * edge)
        camera_width = control_width
    window_height = max(380, usable_height - edge)
    ui_scale = _clamp(min(control_width / 960.0, window_height / 1000.0), 0.72, 1.18)
    canvas_scale = _clamp(window_height / 1000.0, 0.62, 1.22)
    return {
        "camera": (camera_width, window_height, camera_x, edge),
        "control": (control_width, window_height, control_x, edge),
        "screen": (screen_width, screen_height),
        "usable_height": usable_height,
        "ui_scale": ui_scale,
        "canvas_scale": canvas_scale,
        "compact": not wide_split or window_height < 850,
    }
def geometry_string(geometry) -> str:
    width, height, x, y = geometry
    return f"{width}x{height}+{x}+{y}"
def nice_linear_ticks(data_min: float, data_max: float, target_intervals: int=4, *, include_zero: bool=False, expand_limits: bool=True):
    low = float(data_min)
    high = float(data_max)
    if not math.isfinite(low) or not math.isfinite(high):
        low, high = 0.0, 1.0
    if low > high:
        low, high = high, low
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if math.isclose(low, high, rel_tol=0.0, abs_tol=1e-15):
        magnitude = abs(low) if abs(low) > 0.0 else 1.0
        half_span = max(magnitude * 0.05, 1e-12)
        low -= half_span
        high += half_span
    raw_step = (high - low) / max(int(target_intervals), 1)
    exponent = math.floor(math.log10(raw_step))
    scale = 10.0 ** exponent
    fraction = raw_step / scale
    if fraction <= 1.5:
        nice_fraction = 1.0
    elif fraction <= 2.25:
        nice_fraction = 2.0
    elif fraction <= 3.75:
        nice_fraction = 2.5
    elif fraction <= 7.5:
        nice_fraction = 5.0
    else:
        nice_fraction = 10.0
    step = nice_fraction * scale
    epsilon = abs(step) * 1e-10
    if expand_limits:
        axis_min = math.floor((low + epsilon) / step) * step
        axis_max = math.ceil((high - epsilon) / step) * step
        if axis_max <= axis_min:
            axis_max = axis_min + step
    else:
        axis_min, axis_max = low, high
    first_index = int(math.ceil((axis_min - epsilon) / step))
    last_index = int(math.floor((axis_max + epsilon) / step))
    ticks = [index * step for index in range(first_index, last_index + 1)]
    if len(ticks) < 2:
        ticks = [axis_min, axis_max]
    ticks = [0.0 if abs(value) < epsilon else value for value in ticks]
    return axis_min, axis_max, ticks, step
def angle_axis_ticks(low: float, high: float):
    low, high = float(low), float(high)
    if low > high:
        low, high = high, low
    span = high - low
    if not math.isfinite(span) or span <= 0.0:
        return nice_linear_ticks(low, high)
    if span >= 4.0:
        step = next((step for step in (1, 2, 5, 10, 15, 30, 45, 90, 180) if span / step <= 6), 360)
        epsilon = step * 1e-9
        ticks = [float(index * step) for index in range(math.ceil((low - epsilon) / step), math.floor((high + epsilon) / step) + 1)]
        if len(ticks) >= 2:
            return low, high, ticks, float(step)
    return nice_linear_ticks(low, high, expand_limits=False)
def format_nice_tick(value: float, step: float) -> str:
    if abs(value) < abs(step) * 1e-9:
        return "0"
    if abs(step) < 1e-6 or abs(value) >= 1e7:
        return f"{value:.4g}"
    if abs(step) >= 1.0:
        if math.isclose(step, round(step), rel_tol=0.0, abs_tol=1e-12):
            return f"{value:.0f}"
        return f"{value:.1f}"
    decimals = min(8, max(0, int(math.ceil(-math.log10(abs(step))))))
    while decimals < 8 and (not math.isclose(abs(step) * 10.0 ** decimals, round(abs(step) * 10.0 ** decimals), rel_tol=0.0, abs_tol=1e-09)):
        decimals += 1
    return f"{value:.{decimals}f}"
def shade_color(color: str, fraction: float) -> str:
    fraction = min(max(float(fraction), 0.0), 1.0)
    rgb = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    if fraction < 0.5:
        mix = (0.5 - fraction) / 0.5 * 0.4
        rgb = [channel + (255 - channel) * mix for channel in rgb]
    else:
        mix = (fraction - 0.5) / 0.5 * 0.45
        rgb = [channel * (1 - mix) for channel in rgb]
    return "#" + "".join(f"{round(channel):02x}" for channel in rgb)
SCAN_FILE_NAME_PROMPT = "Optional file name (leave blank for a timestamped default):"
def scan_file_stem(name: str) -> str:
    """The shared file-name rule, under the application's own name."""
    return _scan_file_stem(name)
def ask_scan_file_stem(parent, title: str) -> str | None:
    name = simpledialog.askstring(title, SCAN_FILE_NAME_PROMPT, parent=parent)
    if name is None:
        return None
    try:
        return scan_file_stem(name)
    except ValueError as error:
        messagebox.showerror(title, str(error), parent=parent)
        return None
def make_scan_run_folder(parent: Path, stem: str) -> Path:
    """The shared unique-folder allocator, under the application's own name."""
    return _make_scan_run_folder(parent, stem)
def scan_log_line(name: str, details: dict) -> str:
    """The shared log line with the application's clock (tests replace it)."""
    return _scan_log_line(name, details, datetime_class=datetime_module.datetime)
SCAN_PLOT_SNAPSHOT_SETTLE_S = 0.05
class ScanRunRecorder(_CoreScanRunRecorder):
    """The package's recorder bound to this application: its file name and
    version in the log, its own helpers (so replacing app.scan_file_stem,
    make_scan_run_folder or scan_log_line still takes effect), and the list of
    open runs the folder dialog reads."""

    @property
    def PROGRAM_NAME(self):
        return Path(_PROGRAM_FILE).name

    @property
    def APPLICATION_VERSION(self):
        return APP_VERSION

    def _validated_stem(self, stem: str) -> str:
        return scan_file_stem(stem)

    def _make_run_folder(self, parent: Path, stem: str) -> Path:
        return make_scan_run_folder(parent, stem)

    def _format_log(self, name: str, details: dict) -> str:
        return scan_log_line(name, details)

    def _opened(self) -> None:
        _OPEN_SCAN_RUNS.add(self)
def begin_scan_run(parent, page_label: str, folder_title: str, initial_dir, settings: dict, *, prefix: str | None=None, make=None) -> ScanRunRecorder | None:
    where = {"initialdir": str(initial_dir)} if initial_dir else {}
    folder = filedialog.askdirectory(parent=parent, title=folder_title, **where)
    if not folder:
        return None
    typed = ask_scan_file_stem(parent, f"{page_label} file name")
    if typed is None:
        return None
    try:
        if make is not None:
            return make(Path(folder), typed)
        return ScanRunRecorder(Path(folder), prefix or page_label, typed, settings, label=page_label)
    except Exception as error:
        messagebox.showerror(f'{page_label} run folder', f'Could not create the run folder and its files; the scan was not started: {error}', parent=parent)
        return None
PAIR_FLIP_LIMIT = 8
class CombinedControlApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(MAIN_WINDOW_TITLE)
        apply_application_identity(self.root)
        self.window_layout = calculate_window_layout(self.root)
        self.ui_scale = float(self.window_layout["ui_scale"])
        self.canvas_scale = float(self.window_layout["canvas_scale"])
        setattr(self.root, "_moke_ui_scale", self.ui_scale)
        self.root.geometry(geometry_string(self.window_layout["control"]))
        control_geometry = self.window_layout["control"]
        self.root.minsize(min(control_geometry[0], max(620, int(760 * self.ui_scale))), min(control_geometry[1], max(500, int(650 * self.ui_scale))))
        screen_width, screen_height = self.window_layout["screen"]
        self.root.maxsize(screen_width, screen_height)
        self.root.bind_all('<FocusIn>', self._enforce_english_input_mode, add='+')
        self.root.after_idle(lambda: force_english_input_mode(self.root))
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="led-io")
        self.file_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='file-export')
        self.closing = False
        self.instrument_connection_indicators: dict[str, tuple[tk.Canvas, int]] = {}
        self.notebook = None
        self._initialize_ppms_state()
        self.build_ui()
        self.notebook.bind('<<NotebookTabChanged>>', self._on_notebook_tab_changed, add='+')
        self._apply_commercial_look()
        self.root.after(20, self.drain_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
    def _enforce_english_input_mode(self, event=None) -> None:
        target = getattr(event, "widget", self.root)
        try:
            self.root.after_idle(lambda selected=target: force_english_input_mode(selected))
        except tk.TclError:
            pass
    def _ui_px(self, value: int | float, minimum: int=1, *, canvas: bool=False) -> int:
        scale = self.canvas_scale if canvas else self.ui_scale
        return max(int(minimum), int(round(float(value) * scale)))
    @staticmethod
    def _pair_inset(container) -> int:
        raw = container.cget("padding") if "padding" in container.keys() else ()
        padding = [int(float(str(v))) for v in container.tk.splitlist(raw)] or [0]
        inset = padding[0] + (padding[2] if len(padding) > 2 else padding[0])
        if container.winfo_class() == "TLabelframe":
            inset += 4
        return inset
    def build_ui(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self._configure_ui_styles()
        notebook_pad = self._ui_px(3, 2)
        self.notebook = ttk.Notebook(self.root, padding=(notebook_pad, notebook_pad, notebook_pad, 0))
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.ppms_panel_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.ppms_panel_tab, text="PPMS")
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="Log")
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        self.ppms_panel = PPMSPanel(self, self.ppms_panel_tab)
        self.led_log_frame = ttk.LabelFrame(self.log_tab, text="Program communication log (PPMS / Zurich / instruments)", padding=5)
        self.led_log_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.led_log_frame.rowconfigure(0, weight=1)
        self.led_log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(self.led_log_frame, height=5, wrap="none", state="disabled", font=(self.mono_font_family, self.ui_small_font_size), background="#fbfcfd", relief="flat", padx=5, pady=4)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.led_log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(self.led_log_frame, orient="horizontal", command=self.log_text.xview)
        xscrollbar.grid(row=1, column=0, sticky="ew")
        self.log_text.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        self.log_display_paused = False
        self.log_pending_lines = []
        log_tools = ttk.Frame(self.led_log_frame)
        log_tools.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.log_pause_button = ttk.Button(log_tools, text="Pause refresh", command=self.toggle_log_refresh)
        self.log_pause_button.pack(side="left")
        ttk.Button(log_tools, text="Copy shown log", command=self.copy_displayed_log).pack(side="left", padx=5)
        ttk.Button(log_tools, text="Save full log", command=self.save_communication_log).pack(side="left")
        ttk.Label(log_tools, text="Pausing only freezes the display; logging continues and catches up on resume.").pack(side="left", padx=8)
    def _initialize_ppms_state(self) -> None:
        self.ppms_sr830_range_vars = {}
        self.ppms_idle_autorange_enabled = False
        self.ppms_helium_warning_active = False
        self.ppms_tab = None
        self.ppms_environment_controller = PPMSRemoteReadbackController(self.thread_log)
        self.ppms_instrument_controllers = {
            "6221": SafeVisaIdentityController(
                self.thread_log,
                "Keithley 6221",
                "6221",
                ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"),
            ),
            "830_a": SafeVisaIdentityController(
                self.thread_log, "SR830-1", "SR830"
            ),
            "830_b": SafeVisaIdentityController(
                self.thread_log, "SR830-2", "SR830"
            ),
            "830_c": SafeVisaIdentityController(
                self.thread_log, "SR830-3", "SR830"
            ),
            "2400": SafeVisaIdentityController(
                self.thread_log,
                "Keithley 2400",
                "2400",
                ("OUTP OFF",),
            ),
        }
        self.ppms_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix='ppms-control')
        self.ppms_closed = False
        self.ppms_environment_busy = False
        self.ppms_poll_inflight = False
        self.ppms_poll_after_id = None
        self.ppms_backend_var = tk.StringVar(value=PPMS_DEFAULT_BACKEND)
        self.ppms_host_var = tk.StringVar(value=PPMS_DEFAULT_HOST)
        self.ppms_port_var = tk.IntVar(value=PPMS_DEFAULT_PORT)
        default_qdinstrument_dll = next((str(path) for path in PPMSQDInstrumentReadbackController.candidate_dll_paths() if path.is_file()), '')
        self.ppms_qdinstrument_dll_var = tk.StringVar(value=default_qdinstrument_dll)
        self.ppms_connection_var = tk.StringVar(value="Not connected")
        self.ppms_temperature_var = tk.StringVar(value="-- K")
        self.ppms_temperature_status_var = tk.StringVar(value="Status: --")
        self.ppms_field_var = tk.StringVar(value="-- Oe")
        self.ppms_field_status_var = tk.StringVar(value="Status: --")
        self.ppms_last_readback: dict[str, object] | None = None
        self.ppms_control_safety_var = tk.BooleanVar(value=False)
        self.ppms_motor_target_var = tk.StringVar(value="0")
        self.ppms_motor_speed_var = tk.StringVar(value="14")
        self.ppms_motor_status_var = tk.StringVar(value="Not read yet; QDInstrument debug interface only")
        self.ppms_angle_live_var = tk.StringVar(value="-- °")
        self.ppms_xy_vars = {k: tk.StringVar(value=f"SR830-{i}: reference status not read") for i, k in enumerate(PPMS_SR830_KEYS, 1)}
        self.ppms_nested_vars = {
            "angle": [tk.BooleanVar(value=False), tk.StringVar(value="26"), tk.StringVar(value="26"), tk.StringVar(value="1")],
            "temperature": [tk.BooleanVar(value=False), tk.StringVar(value="300"), tk.StringVar(value="300"), tk.StringVar(value="10")],
        }
        self.ppms_scan_context = {}
        self.ppms_motor_confirm_var = tk.BooleanVar(value=False)
        self.ppms_motor_pending = False
        self.ppms_temperature_target_var = tk.StringVar(value="300")
        self.ppms_temperature_rate_var = tk.StringVar(value="10")
        if hasattr(self.ppms_temperature_target_var, 'trace_add'):
            self.ppms_temperature_target_var.trace_add('write', self._ppms_adjust_low_temperature_rate)
        self.ppms_temperature_approach_var = tk.StringVar(value="No overshoot")
        self.ppms_field_target_var = tk.StringVar(value="0")
        self.ppms_field_rate_var = tk.StringVar(value=f'{PPMS_FIELD_RATE_DEFAULT_OE_S:g}')
        self.ppms_field_approach_var = tk.StringVar(value="No overshoot")
        self.ppms_field_mode_var = tk.StringVar(value="Persistent")
        self.ppms_field_step_var = tk.StringVar(value="1000")
        self.ppms_field_scan_start_var = tk.StringVar(value="0")
        self.ppms_field_scan_end_var = tk.StringVar(value="0")
        self.ppms_field_scan_step_var = tk.StringVar(value="1000")
        self.ppms_field_scan_loop_var = tk.IntVar(value=1)
        self.ppms_field_scan_stable_var = tk.StringVar(value="0.5")
        self.ppms_acquisition_time_var = tk.StringVar(value=f"{PPMS_ACQUISITION_S:g}")
        self.ppms_field_scan_eta_var = tk.StringVar(value="Remaining: --")
        self.ppms_field_scan_status_var = tk.StringVar(value="Scan: not started")
        self.ppms_field_scan_running = False
        self.ppms_field_scan_stop_event = threading.Event()
        self.ppms_field_scan_rows: list[dict[str, object]] = []
        self.ppms_field_scan_started_at = 0.0
        self.ppms_field_scan_total = 0
        self.ppms_transport_current_var = tk.StringVar(value=f'{PPMS_TRANSPORT_CURRENT_RMS_MA:g}')
        self.ppms_transport_frequency_var = tk.StringVar(value=f'{PPMS_TRANSPORT_FREQUENCY_HZ:g}')
        self.ppms_transport_compliance_var = tk.StringVar(value=f'{PPMS_TRANSPORT_COMPLIANCE_V:g}')
        self.ppms_transport_harmonic_a_var = tk.StringVar(value=str(PPMS_TRANSPORT_HARMONIC))
        self.ppms_transport_harmonic_b_var = tk.StringVar(value='2')
        self.ppms_transport_harmonic_c_var = tk.StringVar(value='3')
        self.ppms_transport_status_var = tk.StringVar(value='Transport acquisition: waiting for scan; 6221 output OFF')
        self.ppms_resource_vars = {key: tk.StringVar(value=value) for key, value in PPMS_DEFAULT_RESOURCES.items()}
        self.ppms_device_connection_vars = {key: tk.StringVar(value='Not connected') for key in PPMS_DEFAULT_RESOURCES}
        self.ppms_device_connect_buttons = {}
        self.ppms_device_disconnect_buttons = {}
        self.ppms_device_resource_widgets = {}
        self.ppms_lockin_selection = {key: tk.BooleanVar(value=True) for key in PPMS_SR830_KEYS}
    def build_ppms_ui(self) -> None:
        outer = ttk.Frame(self.ppms_tab, padding=self._ui_px(8, 5))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1, minsize=220)
        content = ttk.Frame(outer)
        content.grid(row=0, column=0, sticky="ew")
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=1)
        environment = ttk.LabelFrame(content, text='PPMS MultiVu / EverCool II', padding=2)
        environment.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        environment.columnconfigure(10, weight=1)
        ttk.Label(environment, text="Interface:").grid(row=0, column=0, sticky="w")
        self.ppms_backend_combo = ttk.Combobox(environment, textvariable=self.ppms_backend_var, values=(PPMS_QDINSTRUMENT_BACKEND, PPMS_MULTIPYVU_BACKEND), state='readonly', width=20)
        self.ppms_backend_combo.grid(row=0, column=1, padx=(0, 8), sticky="w")
        self.ppms_backend_combo.bind('<<ComboboxSelected>>', self._on_ppms_backend_changed, add='+')
        self.ppms_host_label = ttk.Label(environment, text="PPMS IP:")
        self.ppms_host_label.grid(row=0, column=2, sticky="w")
        self.ppms_host_entry = ttk.Combobox(environment, textvariable=self.ppms_host_var, values=PPMS_HOST_CHOICES, width=16)
        self.ppms_host_entry.grid(row=0, column=3, padx=(0, 8), sticky="w")
        ttk.Label(environment, text="Port:").grid(row=0, column=4, sticky="w")
        self.ppms_port_entry = ttk.Spinbox(environment, from_=1, to=65535, textvariable=self.ppms_port_var, width=7)
        self.ppms_port_entry.grid(row=0, column=5, padx=(0, 8), sticky="w")
        self.ppms_connect_button = ttk.Button(environment, text='Connect PPMS', style='Strong.TButton', command=self.connect_ppms_environment)
        self.ppms_connect_button.grid(row=0, column=6, padx=2)
        self.ppms_disconnect_button = ttk.Button(environment, text='Disconnect', command=self.disconnect_ppms_environment, state='disabled')
        self.ppms_disconnect_button.grid(row=0, column=7, padx=2)
        self.ppms_read_button = ttk.Button(environment, text='Read now', command=self.read_ppms_environment_once, state='disabled')
        self.ppms_read_button.grid(row=0, column=8, padx=(8, 2))
        self.ppms_environment_lamp = self._create_connection_indicator(environment, 'ppms_environment')
        self.ppms_environment_lamp.grid(row=0, column=9, padx=(8, 1))
        self.ppms_connection_shown_var = tk.StringVar(value=self.ppms_connection_var.get())
        def shown(*_args) -> None:
            text = self.ppms_connection_var.get()
            self.ppms_connection_shown_var.set({'Not connected': 'Offline', 'Connected': 'Online'}.get(text, text) if self.__dict__.get('ppms_rows_tight') else text)
        if hasattr(self.ppms_connection_var, 'trace_add'):
            self.ppms_connection_var.trace_add('write', shown)
        self.ppms_connection_shown = shown
        status = ttk.Label(environment, width=1)
        status.grid(row=0, column=10, sticky='ew')
        show_trimmed(status, self.ppms_connection_shown_var)
        metrics_outer = ttk.LabelFrame(content, text="Live readings", padding=3)
        self.ppms_metrics_frame = metrics_outer
        metrics_outer.grid_propagate(False)
        metrics_outer.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 2))
        metrics_outer.rowconfigure(0, weight=1)
        metrics_outer.columnconfigure(0, weight=1)
        metrics_view = tk.Canvas(metrics_outer, highlightthickness=0, height=100, background=THEME_COLORS["surface"])
        metrics_view.grid(row=0, column=0, sticky='nsew')
        metrics_scroll = ttk.Scrollbar(metrics_outer, orient='vertical', command=metrics_view.yview)
        metrics_scroll.grid(row=0, column=1, sticky='ns')
        metrics_view.configure(yscrollcommand=metrics_scroll.set)
        metrics = ttk.Frame(metrics_view)
        metrics_window = metrics_view.create_window(0, 0, window=metrics, anchor='nw')
        metrics.bind('<Configure>', lambda event: metrics_view.configure(scrollregion=metrics_view.bbox('all')))
        metrics_view.bind('<Configure>', lambda event: metrics_view.itemconfigure(metrics_window, width=event.width))
        def scroll_metrics(event):
            if metrics.winfo_reqheight() <= metrics_view.winfo_height():
                return 'break'
            delta = getattr(event, 'delta', 0)
            number = getattr(event, 'num', None)
            units = (-1 if number == 4 else 1) if number in (4, 5) else (-max(1, int(abs(delta)/120)) if delta > 0 else max(1, int(abs(delta)/120)))
            if delta or number in (4, 5):
                metrics_view.yview_scroll(units, 'units')
            return 'break'
        metrics.columnconfigure(0, weight=1, uniform="ppms_metric")
        metrics.columnconfigure(1, weight=1, uniform="ppms_metric")
        def fit_metric_value(metric_frame, metric_title, metric_value):
            metric_frame.bind('<Configure>', lambda event: metric_value.configure(wraplength=max(100, event.width - metric_title.winfo_reqwidth() - 8)), add='+')
        self.ppms_current_live_var = tk.StringVar(value="Unknown / not read")
        current_metric = ttk.Frame(metrics, padding=2)
        current_metric.grid(row=0, column=1, sticky="ew")
        current_title = ttk.Label(current_metric, text="6221 current:")
        current_title.pack(side="left")
        current_value = ttk.Label(current_metric, textvariable=self.ppms_current_live_var, wraplength=230)
        current_value.pack(side="left")
        fit_metric_value(current_metric, current_title, current_value)
        angle_metric = ttk.Frame(metrics, padding=2)
        ttk.Label(angle_metric, text="Angle:").pack(side="left")
        angle_metric.grid(row=2, column=0, sticky="ew")
        self.ppms_pressure_live_var = tk.StringVar(value="Unknown / not read")
        self.ppms_helium_live_var = tk.StringVar(value="Unknown / not read")
        self.ppms_helium_change_var = tk.StringVar(value="Waiting for a valid reading")
        for row, title, var in ((1, 'Pressure:', self.ppms_pressure_live_var), (2, 'He level:', self.ppms_helium_live_var)):
            frame = ttk.Frame(metrics, padding=2)
            frame.grid(row=row, column=1, sticky="ew")
            metric_title = ttk.Label(frame, text=title)
            metric_title.pack(side="left")
            metric_value = ttk.Label(frame, textvariable=var, wraplength=230)
            metric_value.pack(side="left")
            fit_metric_value(frame, metric_title, metric_value)
        self.ppms_helium_canvas = tk.Canvas(metrics, height=120, width=320, background='white', highlightthickness=0)
        self.ppms_helium_canvas.grid(row=3, column=1, sticky='nsew', padx=2, pady=(2, 0))
        self.ppms_helium_canvas.bind('<Configure>', lambda event: self._draw_ppms_helium_history())
        ttk.Label(angle_metric, textvariable=self.ppms_angle_live_var, style="LargeMetric.TLabel").pack(side="left")
        temperature = ttk.Frame(metrics, padding=2)
        ttk.Label(temperature, text="Temperature:").pack(side="left")
        temperature.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Label(temperature, textvariable=self.ppms_temperature_var, style='LargeMetric.TLabel').pack(side='left')
        ttk.Label(temperature, textvariable=self.ppms_temperature_status_var, style='Muted.TLabel').pack(side='bottom', anchor='w')
        field = ttk.Frame(metrics, padding=2)
        ttk.Label(field, text="Field:").pack(side="left")
        field.grid(row=1, column=0, sticky="ew")
        ttk.Label(field, textvariable=self.ppms_field_var, style='LargeMetric.TLabel').pack(side='left')
        ttk.Label(field, textvariable=self.ppms_field_status_var, style='Muted.TLabel').pack(side='bottom', anchor='w')
        def bind_metrics_wheel(widget):
            for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                widget.bind(sequence, scroll_metrics, add='+')
            for child in widget.winfo_children():
                bind_metrics_wheel(child)
        bind_metrics_wheel(metrics_view)
        control = ttk.LabelFrame(content, text='PPMS temperature and field control', padding=8)
        control.grid(row=3, column=0, sticky="nsew", pady=(0, 2))
        control.columnconfigure(0, weight=1, uniform="ppms_control")
        control.columnconfigure(1, weight=1, uniform="ppms_control")
        control.columnconfigure(2, weight=1, uniform="ppms_control")
        self.ppms_control_safety_check = ttk.Checkbutton(
            control,
            text=(
                "I have confirmed the sample, temperature range, field direction, field-sweep path and the current PPMS state are safe, "
                "and that no other program is controlling the PPMS; with transport acquisition enabled I have also confirmed the 6221 "
                "current, compliance and wiring are safe"
            ),
            variable=self.ppms_control_safety_var,
            command=self._update_ppms_control_ui,
        )
        self.ppms_control_safety_check.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 5))
        temperature_control = ttk.LabelFrame(control, text='Temperature setting', padding=7)
        temperature_control.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        ttk.Label(temperature_control, text="Target K:").grid(row=0, column=0)
        self.ppms_temperature_target_entry = ttk.Entry(temperature_control, textvariable=self.ppms_temperature_target_var, width=9)
        self.ppms_temperature_target_entry.grid(row=0, column=1, padx=(0, 7))
        ttk.Label(temperature_control, text="Rate K/min:").grid(row=0, column=2)
        self.ppms_temperature_rate_entry = ttk.Entry(temperature_control, textvariable=self.ppms_temperature_rate_var, width=8)
        self.ppms_temperature_rate_entry.grid(row=0, column=3, padx=(0, 7))
        self.ppms_temperature_approach_combo = ttk.Combobox(temperature_control, textvariable=self.ppms_temperature_approach_var, values=('Fast settle', 'No overshoot'), state='readonly', width=13)
        self.ppms_temperature_approach_combo.grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        self.ppms_temperature_set_button = ttk.Button(temperature_control, text='Set temperature', command=self.set_ppms_temperature)
        self.ppms_temperature_set_button.grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Label(temperature_control, text='Start or target ≤9 K: ≤1 K/min; else ≤10 K/min', style='Warning.TLabel').grid(row=2, column=0, columnspan=4, sticky='w', pady=(5, 0))
        field_control = ttk.LabelFrame(control, text="Field setting", padding=7)
        field_control.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        ttk.Label(field_control, text="Target Oe:").grid(row=0, column=0)
        self.ppms_field_target_entry = ttk.Entry(field_control, textvariable=self.ppms_field_target_var, width=10)
        self.ppms_field_target_entry.grid(row=0, column=1, padx=(0, 6))
        ttk.Label(field_control, text="Rate Oe/s:").grid(row=0, column=2)
        self.ppms_field_rate_entry = ttk.Entry(field_control, textvariable=self.ppms_field_rate_var, width=8)
        self.ppms_field_rate_entry.grid(row=0, column=3, padx=(0, 6))
        self.ppms_field_approach_combo = ttk.Combobox(field_control, textvariable=self.ppms_field_approach_var, values=('Linear', 'No overshoot', 'Oscillate'), state='readonly', width=12)
        self.ppms_field_approach_combo.grid(row=1, column=0, padx=(0, 6), pady=5)
        self.ppms_field_mode_combo = ttk.Combobox(field_control, textvariable=self.ppms_field_mode_var, values=('Driven', 'Persistent'), state='readonly', width=10)
        self.ppms_field_mode_combo.grid(row=1, column=1, padx=(0, 6))
        self.ppms_field_set_button = ttk.Button(field_control, text='Set field', command=self.set_ppms_field)
        self.ppms_field_set_button.grid(row=1, column=2, padx=2)
        self.ppms_field_zero_button = ttk.Button(field_control, text='Zero', command=self.zero_ppms_field)
        self.ppms_field_zero_button.grid(row=1, column=3, padx=2)
        ttk.Label(field_control, text="Step Oe:").grid(row=2, column=0, pady=(5, 0))
        self.ppms_field_step_entry = ttk.Entry(field_control, textvariable=self.ppms_field_step_var, width=10)
        self.ppms_field_step_entry.grid(row=2, column=1, pady=(5, 0))
        self.ppms_field_down_button = ttk.Button(field_control, text='↓ Down', command=lambda: self.step_ppms_field(-1))
        self.ppms_field_down_button.grid(row=2, column=2, padx=2, pady=(5, 0))
        self.ppms_field_up_button = ttk.Button(field_control, text='↑ Up', command=lambda: self.step_ppms_field(1))
        self.ppms_field_up_button.grid(row=2, column=3, padx=2, pady=(5, 0))
        ttk.Label(field_control, text='Range ±89900 Oe; rate must stay below 150 Oe/s', style='Warning.TLabel').grid(row=3, column=0, columnspan=4, sticky='w', pady=(5, 0))
        scan = ttk.LabelFrame(content, text="PPMS scan", padding=7)
        scan.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        fields = (
            ("Start Oe:", self.ppms_field_scan_start_var, 0),
            ("End Oe:", self.ppms_field_scan_end_var, 2),
            ("Step Oe:", self.ppms_field_scan_step_var, 4),
            ("Rate Oe/s:", self.ppms_field_rate_var, 6),
            ("Loop:", self.ppms_field_scan_loop_var, 8),
            ("Settle s:", self.ppms_field_scan_stable_var, 10),
        )
        self.ppms_field_scan_entries = []
        for label, variable, column in fields:
            ttk.Label(scan, text=label).grid(row=0, column=column, sticky="e")
            if variable is self.ppms_field_scan_loop_var:
                widget = ttk.Spinbox(scan, from_=1, to=1000, textvariable=variable, width=6)
            else:
                widget = ttk.Entry(scan, textvariable=variable, width=9)
            widget.grid(row=0, column=column + 1, padx=(0, 7))
            self.ppms_field_scan_entries.append(widget)
        scan_actions = ttk.Frame(scan)
        scan_actions.grid(row=0, column=12, columnspan=3)
        self.ppms_field_scan_start_button = ttk.Button(scan_actions, text='Start scan', command=self.start_ppms_field_scan)
        self.ppms_field_scan_start_button.grid(row=0, column=0, padx=2)
        self.ppms_field_scan_stop_button = ttk.Button(scan_actions, text='Stop', command=self.stop_ppms_field_scan, state='disabled')
        self.ppms_field_scan_stop_button.grid(row=0, column=1, padx=2)
        self.ppms_field_scan_save_button = ttk.Button(scan_actions, text='Save CSV', command=self.save_ppms_field_scan_csv, state='disabled')
        self.ppms_field_scan_save_button.grid(row=0, column=2, padx=2)
        self.ppms_field_scan_progress = ttk.Progressbar(scan, mode='determinate', maximum=1)
        self.ppms_field_scan_progress.grid(row=1, column=0, columnspan=12, sticky='ew', pady=(6, 0))
        ttk.Label(scan, textvariable=self.ppms_field_scan_eta_var, style='Status.TLabel').grid(row=1, column=12, columnspan=3, sticky='w', padx=(8, 0), pady=(6, 0))
        ttk.Label(scan, textvariable=self.ppms_field_scan_status_var, style='Muted.TLabel').grid(row=2, column=0, columnspan=15, sticky='w', pady=(4, 0))
        self.ppms_transport_required_label = ttk.Label(scan, text='Force sync: 6221 AC + at least one SR830', style='Warning.TLabel')
        self.ppms_transport_required_label.grid(row=3, column=0, columnspan=3, sticky='w', pady=(7, 0))
        ttk.Label(scan, text='Current RMS mA:').grid(row=3, column=3, sticky='e', pady=(7, 0))
        self.ppms_transport_current_entry = ttk.Entry(scan, textvariable=self.ppms_transport_current_var, width=9)
        self.ppms_transport_current_entry.grid(row=3, column=4, sticky='w', padx=(0, 7), pady=(7, 0))
        ttk.Label(scan, text='Frequency Hz:').grid(row=3, column=5, sticky='e', pady=(7, 0))
        self.ppms_transport_frequency_entry = ttk.Entry(scan, textvariable=self.ppms_transport_frequency_var, width=9)
        self.ppms_transport_frequency_entry.grid(row=3, column=6, sticky='w', padx=(0, 7), pady=(7, 0))
        ttk.Label(scan, text='Compliance V:').grid(row=3, column=7, sticky='e', pady=(7, 0))
        self.ppms_transport_compliance_entry = ttk.Entry(scan, textvariable=self.ppms_transport_compliance_var, width=8)
        self.ppms_transport_compliance_entry.grid(row=3, column=8, sticky='w', padx=(0, 7), pady=(7, 0))
        ttk.Label(scan, text='SR830-1 harmonic:').grid(row=3, column=9, sticky='e', pady=(7, 0))
        self.ppms_transport_harmonic_a_spin = ttk.Spinbox(scan, from_=1, to=3, textvariable=self.ppms_transport_harmonic_a_var, width=6)
        self.ppms_transport_harmonic_a_spin.grid(row=3, column=10, sticky='w', padx=(0, 7), pady=(7, 0))
        ttk.Label(scan, text='SR830-2 harmonic:').grid(row=3, column=11, sticky='e', pady=(7, 0))
        self.ppms_transport_harmonic_b_spin = ttk.Spinbox(scan, from_=1, to=3, textvariable=self.ppms_transport_harmonic_b_var, width=6)
        self.ppms_transport_harmonic_b_spin.grid(row=3, column=12, sticky='w', padx=(0, 7), pady=(7, 0))
        ttk.Label(scan, textvariable=self.ppms_transport_status_var, style='Status.TLabel').grid(row=4, column=0, columnspan=15, sticky='w', pady=(4, 0))
        motor = ttk.LabelFrame(control, text="Motor setting", padding=7)
        motor.grid(row=1, column=2, sticky="nsew", padx=(4, 0))
        ttk.Label(motor, text="Target angle °:").grid(row=0, column=0)
        ttk.Entry(motor, textvariable=self.ppms_motor_target_var, width=10).grid(row=0, column=1)
        ttk.Label(motor, text="Decel code 0–14:").grid(row=0, column=2)
        ttk.Entry(motor, textvariable=self.ppms_motor_speed_var, width=8).grid(row=0, column=3)
        self.ppms_motor_read_button = ttk.Button(motor, text="Read angle", command=lambda: self._ppms_motor_action("read"))
        self.ppms_motor_read_button.grid(row=1, column=0, padx=3, pady=5)
        self.ppms_motor_move_button = ttk.Button(motor, text="Move", command=lambda: self._ppms_motor_action("move"))
        self.ppms_motor_move_button.grid(row=1, column=1, padx=3)
        self.ppms_motor_info_button = ttk.Button(motor, text="Interface info", command=lambda: self._ppms_motor_action("info"))
        self.ppms_motor_info_button.grid(row=1, column=2, columnspan=2, padx=3, sticky="w")
        ttk.Checkbutton(motor, text='Motor angle, decel code and wiring clearance confirmed', variable=self.ppms_motor_confirm_var).grid(row=2, column=0, columnspan=4, sticky='w')
        ttk.Label(motor, text="−370 to 370°; rotations over 30° need confirmation; 0 fastest, 14 slowest.\nNo software emergency stop; keep MultiVu Motion Control open.",
                  style="Warning.TLabel").grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(motor, textvariable=self.ppms_motor_status_var, wraplength=360).grid(row=4, column=0, columnspan=4, sticky="w")
        nested_widgets = []
        for row_number, (key, label) in enumerate((("angle", "Angle scan °"), ("temperature", "Temperature scan K")), 5):
            variables = self.ppms_nested_vars[key]
            check = ttk.Checkbutton(scan, text=label, variable=variables[0])
            check.grid(row=row_number, column=0, columnspan=2, sticky="w")
            nested_widgets.append(check)
            for col, (caption, variable) in enumerate(zip(("Start", "End", "Step"), variables[1:])):
                ttk.Label(scan, text=caption).grid(row=row_number, column=2+col*2)
                entry = ttk.Entry(scan, textvariable=variable, width=10)
                entry.grid(row=row_number, column=3+col*2)
                nested_widgets.append(entry)
        ttk.Label(scan, text="Unchecked outer axes keep their current value; for temperature/angle-only scans set field start = end. Field is zeroed and the 6221 turned off before each rotation.",
                  style="Warning.TLabel").grid(row=7, column=0, columnspan=15, sticky="w")
        self.ppms_control_widgets = [
            *nested_widgets,
            self.ppms_temperature_target_entry,
            self.ppms_temperature_rate_entry,
            self.ppms_temperature_approach_combo,
            self.ppms_temperature_set_button,
            self.ppms_field_target_entry,
            self.ppms_field_rate_entry,
            self.ppms_field_approach_combo,
            self.ppms_field_mode_combo,
            self.ppms_field_set_button,
            self.ppms_field_zero_button,
            self.ppms_field_step_entry,
            self.ppms_field_down_button,
            self.ppms_field_up_button,
            *self.ppms_field_scan_entries,
            self.ppms_field_scan_start_button,
            self.ppms_transport_current_entry,
            self.ppms_transport_frequency_entry,
            self.ppms_transport_compliance_entry,
            self.ppms_transport_harmonic_a_spin,
            self.ppms_transport_harmonic_b_spin,
        ]
        self._on_ppms_backend_changed()
        instruments = ttk.LabelFrame(content, text='PPMS external instruments (independent connections)', padding=8)
        instruments.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        def sync_metrics_height(_event=None):
            self.ppms_metrics_frame.configure(height=instruments.winfo_reqheight())
        instruments.bind('<Configure>', sync_metrics_height, add='+')
        instruments.after_idle(sync_metrics_height)
        instruments.columnconfigure(0, weight=1)
        instruments.columnconfigure(1, weight=1)
        labels = {"6221": "Keithley 6221", "2400": "Keithley 2400", **PPMS_SR830_LABELS}
        for key, cell_row, cell_column in (('6221', 0, 0), ('830_a', 0, 1), ('2400', 1, 0), ('830_b', 1, 1), ('830_c', 2, 1)):
            cell = ttk.Frame(instruments)
            cell.grid(row=cell_row, column=cell_column, sticky="ew", padx=2)
            row = 0
            name = ttk.Label(cell, text=f'{labels[key]}:')
            name.grid(row=row, column=0, sticky='e', pady=2)
            if key in PPMS_SR830_KEYS:
                entry = ttk.Combobox(cell, textvariable=self.ppms_resource_vars[key], values=PPMS_SR830_RESOURCE_OPTIONS, state='readonly', width=16)
            else:
                entry = ttk.Entry(cell, textvariable=self.ppms_resource_vars[key], width=18)
            entry.grid(row=row, column=1, sticky="w", padx=(0, 3), pady=2)
            self.ppms_device_resource_widgets[key] = entry
            connect_button = ttk.Button(cell, text='Connect', command=lambda selected=key: self.connect_ppms_device(selected), width=8)
            connect_button.grid(row=row, column=3, padx=2, pady=2)
            disconnect_button = ttk.Button(cell, text='Disconnect', state='disabled', command=lambda selected=key: self.disconnect_ppms_device(selected), width=10)
            disconnect_button.grid(row=row, column=4, padx=2, pady=2)
            self.ppms_device_connect_buttons[key] = connect_button
            self.ppms_device_disconnect_buttons[key] = disconnect_button
            lamp = self._create_connection_indicator(cell, f'ppms_{key}')
            lamp.grid(row=row, column=5, padx=(8, 1), pady=2)
            shown = self._ppms_compact_status(self.ppms_xy_vars[key]) if key in PPMS_SR830_KEYS else self.ppms_device_connection_vars[key]
            status = ttk.Label(cell, textvariable=shown, width=13)
            status.grid(row=row, column=6, sticky="w", padx=(1, 8), pady=2)
            self.__dict__.setdefault('ppms_instrument_cells', {})[key] = (name, f'{labels[key]}:', entry, lamp, status, shown)
        macro_row = ttk.Frame(instruments)
        macro_row.grid(row=2, column=0, columnspan=2, sticky="e")
        self.ppms_macro_row = macro_row
        ttk.Button(macro_row, text='Macro', command=self.open_ppms_macro).grid(row=0, column=0)
        self._build_ppms_sr830_tc_choice(macro_row)
        for i, key in enumerate(PPMS_SR830_KEYS):
            ttk.Label(instruments, textvariable=self.ppms_xy_vars[key], style="LargeMetric.TLabel").grid(row=4 + i, column=0, columnspan=2, sticky="w", padx=5)
        transport_plot = ttk.LabelFrame(outer, text='PPMS: SR830-1 / SR830-2 / SR830-3 X, Y vs field (per angle/temperature)', padding=6)
        transport_plot.grid(row=3, column=0, sticky="nsew")
        transport_plot.rowconfigure(0, weight=1)
        transport_plot.columnconfigure(0, weight=1)
        self.ppms_transport_canvas = tk.Canvas(transport_plot, height=self._ui_px(250, 200), background='#ffffff', highlightthickness=1, highlightbackground=THEME_COLORS['plot_border'])
        self.ppms_transport_canvas.grid(row=0, column=0, sticky="nsew")
        self.ppms_transport_canvas.bind('<Configure>', lambda _event: self._redraw_ppms_transport_plot(), add='+')
        self._compact_ppms_controls(control, scan, instruments, transport_plot)
        self.ppms_transport_canvas.destroy()
        self.ppms_transport_canvases = {}
        self.ppms_transport_plot_frames = {}
        for row, key in enumerate(PPMS_SR830_KEYS):
            transport_plot.rowconfigure(row, weight=1, uniform="ppms_plot_rows")
            for col, channel in enumerate(("x", "y", "theta")):
                transport_plot.columnconfigure(col, weight=1, uniform="ppms_plots")
                label = "θ" if channel == "theta" else channel.upper()
                harmonic = self._ppms_harmonic_var(key).get()
                order_label = {1: '1st harmonic', 2: '2nd harmonic', 3: '3rd harmonic'}.get(int(harmonic), f'harmonic {harmonic}')
                frame = ttk.LabelFrame(transport_plot, text=f"{PPMS_SR830_LABELS[key]} {order_label} {label}", padding=2)
                frame.grid(row=row, column=col, sticky="nsew")
                self.ppms_transport_plot_frames[(key, channel)] = frame
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)
                canvas = tk.Canvas(frame, height=PPMS_PLOT_ROW_MIN_HEIGHT_PX, width=300, background="white", highlightthickness=0)
                canvas.ppms_compact_plot = True
                canvas.grid(row=0, column=0, sticky="nsew")
                canvas.bind("<Configure>", lambda event: self._request_plot_redraw())
                self.ppms_transport_canvases[(key, channel)] = canvas
        self.ppms_transport_canvas = self.ppms_transport_canvases[("830_a", "x")]
        self._update_ppms_control_ui()
        self._redraw_ppms_transport_plot()
    def _compact_ppms_controls(self, control, scan, instruments, plot):
        self.ppms_instruments_frame = instruments
        self.ppms_plot_frame = plot
        for child in control.winfo_children():
            child.grid_remove()
        control.configure(text="PPMS settings", padding=3)
        panel = ttk.Frame(control)
        panel.grid(row=0, column=0, sticky="nw")
        left = ttk.Frame(panel)
        left.grid(row=0, column=0, sticky="nw")
        harm = self.ppms_settings_panel = ttk.Frame(panel)
        harm.grid(row=0, column=1, sticky="ns")
        for row in range(4):
            harm.rowconfigure(row, weight=1)
        control.columnconfigure(0, weight=2, uniform="")
        control.columnconfigure(1, weight=1, uniform="")
        control.columnconfigure(2, weight=0, uniform="")
        self.ppms_simple_mode = tk.StringVar(value="Field")
        self.ppms_simple_range = [tk.StringVar(value=v) for v in getattr(self, "ppms_default_field_range", ("0", "1000", "100"))]
        self.ppms_motor_confirm_var = self.ppms_control_safety_var
        self.ppms_scan_target_widgets = {}
        safety_text = "Sample, ranges, wiring and motor clearance confirmed safe; no other program in control, MultiVu stays operable"
        safety_font = tkfont.nametofont("TkDefaultFont", root=self.root)
        ttk.Style(self.root).configure('PPMSSafety.TCheckbutton', wraplength=safety_font.measure(safety_text) // 2 + safety_font.measure('0') * 6)
        ttk.Checkbutton(left, text=safety_text, style="PPMSSafety.TCheckbutton", variable=self.ppms_control_safety_var, command=self._update_ppms_control_ui).grid(row=0, column=0, columnspan=10, sticky="w")
        def entry(row, col, label, variable):
            ttk.Label(left, text=label).grid(row=row, column=col, sticky="e", padx=(0 if col == 0 else 8, 0))
            widget = ttk.Entry(left, textvariable=variable, width=6)
            widget.grid(row=row, column=col+1, sticky="ew", padx=2, pady=1)
            self.ppms_control_widgets.append(widget)
            target_mode = {'T K': 'Temperature', 'H Oe': 'Field', 'Angle °': 'Angle', 'I mA RMS': 'Current', 'f Hz': 'Frequency'}.get(label)
            if target_mode:
                self.ppms_scan_target_widgets.setdefault(target_mode, []).append(widget)
            return widget
        def button(row, col, label, command):
            widget = ttk.Button(left, text=label, command=command, width=8)
            widget.grid(row=row, column=col, padx=2)
            self.ppms_control_widgets.append(widget)
            target_mode = {"Set T": "Temperature", "Set H": "Field", "Move": "Angle", "Set I": "Current"}.get(label)
            if target_mode:
                self.ppms_scan_target_widgets.setdefault(target_mode, []).append(widget)
            return widget
        entry(1, 0, "T K", self.ppms_temperature_target_var)
        entry(1, 2, "K/min", self.ppms_temperature_rate_var)
        button(1, 4, "Set T", self.set_ppms_temperature)
        entry(1, 5, "Angle °", self.ppms_motor_target_var)
        entry(1, 7, "Decel 0–14", self.ppms_motor_speed_var)
        self.ppms_motor_move_button = button(1, 9, "Move", lambda: self._ppms_motor_action("move"))
        self.ppms_control_widgets.remove(self.ppms_motor_move_button)
        entry(2, 0, "H Oe", self.ppms_field_target_var)
        entry(2, 2, "Oe/s", self.ppms_field_rate_var)
        button(2, 4, "Set H", self.set_ppms_field)
        button(2, 5, "Zero H", self.zero_ppms_field).grid_configure(columnspan=2, sticky="e")
        self.ppms_persistent_button = button(2, 7, "Persist", self.switch_ppms_persistent)
        self.ppms_persistent_button.grid_configure(columnspan=2, sticky="e")
        entry(3, 0, "I mA RMS", self.ppms_transport_current_var)
        entry(3, 2, "f Hz", self.ppms_transport_frequency_var)
        button(3, 4, "Set I", self._ppms_set_current_direct)
        ttk.Button(left, text="I off", command=self.ppms_sources_off, width=8).grid(row=3, column=5, columnspan=2, padx=2, sticky="e")
        entry(3, 7, "Comp. V", self.ppms_transport_compliance_var)
        self.ppms_lockin_choice_widgets = []
        for row, key in enumerate(PPMS_SR830_KEYS, 0):
            col, label, var = 13, f"{PPMS_SR830_LABELS[key]} harm.", self._ppms_harmonic_var(key)
            ttk.Label(harm, text=label).grid(row=row, column=col, sticky="e", padx=(12, 3))
            widget = ttk.Combobox(harm, values=PPMS_HARMONIC_CHOICES, textvariable=var, state="readonly", width=7)
            widget.bind('<<ComboboxSelected>>', lambda event, k=key: self._ppms_apply_selected_harmonic(k))
            self.ppms_lockin_choice_widgets.append((widget, 'readonly'))
            widget.grid(row=row, column=col+1, sticky="ew")
            self.ppms_control_widgets.append(widget)
            select = ttk.Checkbutton(harm, text='Measure', variable=self.ppms_lockin_selection[key], command=lambda: self._refresh_ppms2_plot_layout() if isinstance(self, PPMS2Panel) else None)
            select.grid(row=row, column=17, padx=3)
            self.ppms_control_widgets.append(select)
            self.ppms_lockin_choice_widgets.append((select, 'normal'))
            self._create_connection_indicator(harm, f"ppms_ref_{key}")
            if not hasattr(self, "ppms_sr830_status_vars"):
                self.ppms_sr830_status_vars = {}
            self.ppms_sr830_status_vars[key] = tk.StringVar(value="Not read")
            self.ppms_sr830_range_vars[key] = tk.StringVar(value="Autorange ON")
            ttk.Label(harm, textvariable=self.ppms_sr830_range_vars[key], width=16).grid(row=row, column=18, sticky='w', padx=(6, 0))
        scan.grid(row=3, column=1, columnspan=1, sticky="nsew", padx=(6, 0), pady=(0, 2))
        scan.configure(text="PPMS scan", padding=3)
        for child in scan.winfo_children():
            child.grid_remove()
        ttk.Label(scan, text="Mode").grid(row=0, column=0, sticky="e", padx=(0, 2))
        mode = ttk.Combobox(scan, textvariable=self.ppms_simple_mode, values=PPMS_SCAN_MODES, state="readonly", width=19)
        mode.grid(row=0, column=1, columnspan=3, sticky="w", padx=2, pady=1)
        self.ppms_control_widgets.append(mode)
        ttk.Label(scan, text="Settle s").grid(row=0, column=4, sticky="e", padx=(6, 2))
        widget = ttk.Entry(scan, textvariable=self.ppms_field_scan_stable_var, width=6)
        widget.grid(row=0, column=5, sticky="ew", padx=2, pady=1)
        self.ppms_control_widgets.append(widget)
        ttk.Label(scan, text="Acquire s").grid(row=0, column=6, sticky="e", padx=(6, 2))
        acquisition_entry = ttk.Entry(scan, textvariable=self.ppms_acquisition_time_var, width=6)
        acquisition_entry.grid(row=0, column=7, sticky="ew", padx=2, pady=1)
        self.ppms_control_widgets.append(acquisition_entry)
        for i, (label, var) in enumerate(zip(("Start", "End", "Step"), self.ppms_simple_range)):
            ttk.Label(scan, text=label).grid(row=1, column=2*i, sticky="e", padx=(0 if i == 0 else 6, 2))
            widget = ttk.Entry(scan, textvariable=var, width=6)
            widget.grid(row=1, column=2*i+1, sticky="ew", padx=2, pady=1)
            self.ppms_control_widgets.append(widget)
        self.ppms_scan_step_entry = widget
        unit = tk.StringVar(value="Oe")
        self.ppms_simple_unit = unit
        self.ppms_temperature_scan_range = [tk.StringVar(value=v) for v in ("300", "280", "5")]
        temperature_range_frame = ttk.Frame(scan)
        ttk.Label(temperature_range_frame, text="Outer temperature K:").grid(row=0, column=0)
        for i, (label, var) in enumerate(zip(("Start", "End", "Step"), self.ppms_temperature_scan_range)):
            ttk.Label(temperature_range_frame, text=label).grid(row=0, column=1+2*i)
            entry = ttk.Entry(temperature_range_frame, textvariable=var, width=6)
            entry.grid(row=0, column=2+2*i)
            self.ppms_control_widgets.append(entry)
        self.ppms_temperature_delay_var = tk.StringVar(value=f"{PPMS_TEMPERATURE_DELAY_S:g}")
        ttk.Label(temperature_range_frame, text="Wait s").grid(row=0, column=7, padx=(6, 0))
        entry = ttk.Entry(temperature_range_frame, textvariable=self.ppms_temperature_delay_var, width=6)
        entry.grid(row=0, column=8)
        self.ppms_control_widgets.append(entry)
        self.ppms_frequency_scan_range = [tk.StringVar(value=f'{value:g}') for value in (NONLOCAL_FSCAN_START_HZ, NONLOCAL_FSCAN_STOP_HZ)]
        self.ppms_frequency_scan_note_var = tk.StringVar()
        frequency_range_frame = ttk.Frame(scan)
        self.ppms_frequency_range_frame = frequency_range_frame
        ttk.Label(frequency_range_frame, text="Outer frequency Hz:").grid(row=0, column=0)
        for i, (label, var) in enumerate(zip(("Start", "End"), self.ppms_frequency_scan_range)):
            ttk.Label(frequency_range_frame, text=label).grid(row=0, column=1+2*i)
            entry = ttk.Entry(frequency_range_frame, textvariable=var, width=6)
            entry.grid(row=0, column=2+2*i)
            self.ppms_control_widgets.append(entry)
        frequency_note = ttk.Label(frequency_range_frame, width=1)
        frequency_note.grid(row=0, column=5, sticky="ew", padx=(6, 0))
        frequency_range_frame.columnconfigure(5, weight=1)
        show_trimmed(frequency_note, self.ppms_frequency_scan_note_var)
        def refresh_frequency_note(*_args):
            self.ppms_frequency_scan_note_var.set(ppms_frequency_scan_note(*(var.get() for var in self.ppms_frequency_scan_range)))
        for var in self.ppms_frequency_scan_range:
            if hasattr(var, "trace_add"):
                var.trace_add("write", refresh_frequency_note)
        refresh_frequency_note()
        self.ppms_outer_field_scan_range = [tk.StringVar(value=v) for v in getattr(self, 'ppms_default_field_range', ('0', '1000', '100'))]
        field_range_frame = ttk.Frame(scan)
        self.ppms_outer_field_range_frame = field_range_frame
        ttk.Label(field_range_frame, text="Outer field Oe:").grid(row=0, column=0)
        for i, (label, var) in enumerate(zip(("Start", "End", "Step"), self.ppms_outer_field_scan_range)):
            ttk.Label(field_range_frame, text=label).grid(row=0, column=1+2*i)
            entry = ttk.Entry(field_range_frame, textvariable=var, width=6)
            entry.grid(row=0, column=2+2*i)
            self.ppms_control_widgets.append(entry)
        def change_mode(_event=None):
            defaults = self._ppms_scan_mode_defaults()
            combined = self.ppms_simple_mode.get() in ("Field+Temperature", "Angle+Temperature", "Current+Temperature")
            if combined:
                temperature_range_frame.grid(row=4, column=0, columnspan=9, sticky="w", pady=(2, 0))
            else:
                temperature_range_frame.grid_remove()
            if self.ppms_simple_mode.get() == "Frequency+Current":
                frequency_range_frame.grid(row=4, column=0, columnspan=9, sticky="ew", pady=(2, 0))
            else:
                frequency_range_frame.grid_remove()
            if self.ppms_simple_mode.get() == "Angle+Field":
                field_range_frame.grid(row=4, column=0, columnspan=9, sticky="w", pady=(2, 0))
            else:
                field_range_frame.grid_remove()
            scan.configure(text="PPMS scan")
            values = defaults[self.ppms_simple_mode.get()]
            for var, value in zip(self.ppms_simple_range, values): var.set(value)
            unit.set(values[3])
            self._apply_ppms_mode_acquisition_default(self.ppms_simple_mode.get())
            self._update_ppms_control_ui()
            if not self.ppms_field_scan_rows:
                self.ppms_plot_axis = PPMS_SCAN_PLOT_AXES[self.ppms_simple_mode.get()]
                self._redraw_ppms_transport_plot()
        mode.bind("<<ComboboxSelected>>", change_mode)
        self.ppms_scan_mode_combo = mode
        ttk.Label(scan, textvariable=unit).grid(row=1, column=6, columnspan=3, sticky="w", padx=(2, 0))
        actions = self.ppms_field_scan_start_button.master
        actions.grid(row=2, column=0, columnspan=9, sticky="w", pady=(3, 0))
        actions.lift([w for w in scan.winfo_children() if w.winfo_class() == "TEntry"][-1])
        for col, (widget, width) in enumerate(((self.ppms_field_scan_start_button, 10), (self.ppms_field_scan_stop_button, 6), (self.ppms_field_scan_save_button, 10))):
            widget.configure(width=width)
            widget.grid(row=0, column=col, padx=(0, 4))
        self.ppms_field_scan_progress.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(0, 6), pady=(3, 0))
        status = ttk.Label(scan, textvariable=self.ppms_field_scan_status_var, wraplength=0)
        status.grid(row=3, column=2, columnspan=7, sticky="ew", pady=(3, 0))
        status.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(100, event.width-8)))
        ttk.Label(scan, text="One-way start→end; 6221 AC + two SR830; current is turned off at the end, environment holds the last point")
        self.ppms_zero_persist_button = ttk.Button(actions, text='Zero field + persist after scan', command=self._queue_ppms_zero_persist, width=26, state='disabled')
        self.ppms_zero_persist_button.grid(row=0, column=3)
        scan.columnconfigure(8, weight=1)
        instruments.configure(padding=3)
        for child in instruments.winfo_children():
            info = child.grid_info()
            if info and int(info.get("row", 0)) >= 4:
                child.grid_remove()
            elif child is self.__dict__.get("ppms_macro_row"):
                child.grid_configure(row=2, column=0, rowspan=1, columnspan=1, sticky="w", padx=2)
        plot.configure(text="SR830-1 / SR830-2 / SR830-3: X, Y, θ vs scan variable")
        plot.master.rowconfigure(3, weight=1, minsize=220)
        if isinstance(control, tk.Widget):
            self._ppms_arrange_by_width(control.master, instruments, self.ppms_metrics_frame, control, scan, left, harm)
    def _ppms_arrange_by_width(self, content, instruments, metrics, control, scan, left, harm):
        cells, state = {}, {}
        def harm_width() -> int:
            if not cells:
                for widget in harm.grid_slaves():
                    info = widget.grid_info()
                    cells[widget] = (int(info['row']), int(info['column']), sum(int(float(str(v))) for v in (lambda pad: pad if isinstance(pad, (tuple, list)) else str(pad).split())(info.get('padx', 0))))
            widths = {}
            for widget, (_row, column, padx) in cells.items():
                widths[column] = max(widths.get(column, 0), widget.winfo_reqwidth() + padx)
            return sum(widths.values())
        def place(_event=None) -> None:
            try:
                available = content.winfo_width()
                if available <= 1:
                    return
                one, inset = harm_width(), self._pair_inset(control)
                instr = state['instr'] if state.get('tight') else instruments.winfo_reqwidth()
                state['instr'] = instr
                wide = available >= max(instr, inset + left.winfo_reqwidth() + one) + 6 + scan.winfo_reqwidth()
                columns = 0 if wide else 2 if available >= inset + 2 * one else 1
                tight = not wide and available < instr
                window = content.winfo_toplevel().winfo_width()
            except (tk.TclError, ValueError):
                return
            if (state.get('columns'), state.get('tight')) == (columns, tight):
                return
            now, guard = time.monotonic(), state.setdefault('guard', [None, 0.0, 0])
            if guard[0] != window or now - guard[1] > 1.0:
                guard[:] = [window, now, 0]
            guard[2] += 1
            if guard[2] > PAIR_FLIP_LIMIT and 'columns' in state:
                return
            if bool(state.get('tight')) != tight:
                self._ppms_tighten_rows(tight)
            state['columns'], state['tight'] = columns, tight
            content.columnconfigure(0, weight=2 if wide else 0)
            instruments.grid_configure(columnspan=1 if wide else 2)
            control.grid_configure(columnspan=1 if wide else 2)
            metrics.grid_configure(row=1 if wide else 4)
            scan.grid_configure(row=3 if wide else 4, column=1 if wide else 0, padx=(6, 0) if wide else 0)
            harm.grid_configure(row=0 if wide else 1, column=1 if wide else 0, sticky='ns' if wide else 'w', pady=0 if wide else (4, 0))
            for widget, (row, column, _padx) in cells.items():
                widget.grid_configure(row=row // 2 if columns == 2 else row, column=column + 6 * (row % 2) if columns == 2 else column)
        for widget in (content, scan):
            widget.bind('<Configure>', place, add='+')
        place()
    def _ppms_tighten_rows(self, tight: bool) -> None:
        self.ppms_rows_tight = tight
        self.ppms_host_label.configure(text='IP:' if tight else 'PPMS IP:')
        for widget, wide, narrow in ((self.ppms_backend_combo, 20, 18), (self.ppms_host_entry, 16, 14), (self.ppms_port_entry, 7, 6)):
            widget.configure(width=narrow if tight else wide)
            widget.grid_configure(padx=(0, 3) if tight else (0, 8))
        self.ppms_read_button.grid_configure(padx=(3, 2) if tight else (8, 2))
        self.ppms_environment_lamp.grid_configure(padx=(3, 1) if tight else (8, 1))
        self.ppms_connection_shown()
        short, words = self.__dict__.setdefault('ppms_short_status_vars', {}), {'Not connected': 'Offline', 'Connected': 'Online', 'Pass complete': 'Done'}
        for key, (name, text, entry, lamp, status, shown) in self.__dict__.get('ppms_instrument_cells', {}).items():
            sr830 = key in PPMS_SR830_KEYS
            if tight and key not in short:
                compact, short[key] = shown if sr830 else self._ppms_compact_status(shown), tk.StringVar()
                refresh = lambda *_args, c=compact, s=short[key]: s.set(words.get(c.get(), c.get()))
                compact.trace_add('write', refresh)
                refresh()
            name.configure(text=f'{key}:' if tight and not sr830 else text)
            entry.configure(width=(12 if sr830 else 13) if tight else (16 if sr830 else 18))
            lamp.grid_configure(padx=(3, 1) if tight else (8, 1))
            status.configure(width=8 if tight else 13, textvariable=short[key] if tight else shown)
            status.grid_configure(padx=(1, 3) if tight else (1, 8))
        label, button = self.__dict__.get('ppms_zurich_reference_label'), self.__dict__.get('zurich_connect_button')
        if label is not None and button is not None:
            label.configure(text='Ref.' if tight else 'Reference')
            button.configure(text='Connect' if tight else 'Connect / apply settings', width=8 if tight else '')
    def _ppms_scan_mode_defaults(self) -> dict:
        defaults = {"Temperature": ("300", "280", "5", "K"), "Field": ("0", "1000", "100", "Oe"), "Field+Temperature": ("0", "1000", "100", "Oe"), "Angle": ("0", "360", "5", "°"), "Current": ("0.01", "1", "0.01", "mA RMS")}
        defaults["Field"] = defaults["Field+Temperature"] = (*getattr(self, "ppms_default_field_range", ("0", "1000", "100")), "Oe")
        defaults["Angle+Temperature"] = ("0", "360", "5", "°")
        defaults["Current+Temperature"] = defaults["Current"]
        defaults["Frequency"] = (f"{NONLOCAL_FSCAN_START_HZ:g}", f"{NONLOCAL_FSCAN_STOP_HZ:g}", "", "Hz (primes)")
        defaults["Frequency+Current"] = defaults["Current"]
        defaults["Angle+Field"] = defaults["Angle"]
        return defaults
    def _ppms_direct_transport_settings(self):
        if not self.ppms_instrument_controllers['6221'].connected:
            raise ValueError("Please connect the 6221")
        active, zurich = self._ppms_selected_lockins()
        addresses = [self.ppms_instrument_controllers[k].resource_name for k in active]
        if len(set(addresses)) != len(addresses):
            raise ValueError("The SR830 addresses must differ")
        settings = validate_ppms_transport_scan_settings(self.ppms_transport_current_var.get(), self.ppms_transport_frequency_var.get(),
            self.ppms_transport_compliance_var.get(), self._ppms_harmonic_value('830_a'), self._ppms_harmonic_value('830_b'), active, allow_no_lockins=zurich,
            harmonic_c=self._ppms_harmonic_value("830_c"))
        if any(settings[k] not in (1, 2, 3) for k in ("harmonic_a", "harmonic_b", "harmonic_c")):
            raise ValueError("Harmonic must be 1, 2 or 3")
        return settings
    def _ppms_sr830_keys(self) -> tuple[str, ...]:
        controllers = getattr(self, "ppms_instrument_controllers", {}) or {}
        return tuple(key for key in PPMS_SR830_KEYS if key in controllers)
    def _ppms_harmonic_var(self, key: str):
        return getattr(self, PPMS_SR830_HARMONIC_ATTRS[key], None)
    def _ppms_harmonic_orders(self, key: str) -> tuple[int, ...]:
        """Harmonic orders this SR830 measures, one per scan pass."""
        variable = self._ppms_harmonic_var(key)
        value = str(variable.get()).strip() if variable is not None else "1"
        return parse_harmonic_mode(value, mode_orders=PPMS_HARMONIC_MODE_ORDERS)
    def _ppms_harmonic_value(self, key: str, default: int = 1) -> int:
        try:
            return self._ppms_harmonic_orders(key)[0]
        except (ValueError, AttributeError, tk.TclError):
            return int(default)
    def _ppms_harmonic_passes(self, settings):
        """One settings dict per pass; a meter drops out once its orders end."""
        schedules = {key: self._ppms_harmonic_orders(key) for key in settings["active_lockins"]}

        def validate_pass(config):
            validate_ppms_transport_scan_settings(
                config["current_rms_ma"], config["frequency_hz"], config["compliance_v"],
                config["harmonic_a"], config["harmonic_b"], config["active_lockins"],
                allow_no_lockins=bool(config.get("allow_no_lockins", False)), harmonic_c=config["harmonic_c"])

        return schedule_harmonic_passes(settings, schedules, validator=validate_pass,
                                        harmonic_setting_keys=PPMS_SR830_HARMONIC_SETTING_KEYS)
    def _ppms_selected_lockins(self):
        selections = getattr(self, 'ppms_lockin_selection', {})
        active = tuple((k for k in self._ppms_sr830_keys() if (selections[k].get() if k in selections else self.ppms_instrument_controllers[k].connected)))
        zurich = bool(selections.get('zurich') and selections['zurich'].get())
        missing = [k for k in active if not self.ppms_instrument_controllers[k].connected]
        if zurich and not self.zurich_controller.mfli_connected:
            missing.append('Zurich')
        if missing:
            raise ValueError('Connect the selected lock-ins or untick them: ' + ', '.join(missing))
        if not active and not zurich:
            raise ValueError('Select at least one lock-in for measurement')
        return active, zurich
    def _ppms_set_current_direct(self):
        try:
            self._require_ppms_control_ready()
            settings = self._ppms_direct_transport_settings()
        except Exception as error:
            messagebox.showerror("Set current", str(error), parent=self.root)
            return
        if self.ppms_environment_busy:
            return
        self.ppms_field_scan_stop_event.clear()
        def operation():
            self.ppms_idle_autorange_enabled = False
            try:
                self._configure_ppms_transport_scan(settings)
                self.ppms_6221_zero_output = settings["current_rms_ma"] == 0
                self.ppms_zurich_autorange_pending = settings["current_rms_ma"] != 0
                self.ppms_idle_range_ready_at = {}
                for state in getattr(self, 'ppms_sr830_range_states', {}).values():
                    state.low_range_count = state.range_cooldown = 0
                    state.low_range_peak = 0.0
                self.ppms_idle_autorange_enabled = (settings["current_rms_ma"] != 0 and not self.ppms_field_scan_stop_event.is_set())
            except BaseException:
                self._stop_ppms_transport_source()
                raise
        self._run_ppms_command(
            "Setting 6221 AC current…", operation,
            success_text=(f"6221 output ON confirmed by readback; {settings['current_rms_ma']:g} mA RMS / "
                          f"{settings['frequency_hz']:g} Hz; external reference confirmed"),
        )
    STFMR_LOCKIN_WIDE_TABLE = {
        "f start GHz:": (0, 0), "f stop GHz:": (0, 1), "f step GHz:": (0, 2),
        "Mod. start Hz:": (0, 3), "Settle s:": (0, 4),
        "P start dBm:": (1, 0), "P stop dBm:": (1, 1), "P step dB:": (1, 2),
        "Mod. end Hz:": (1, 3), "Acquire s:": (1, 4),
        "Start A:": (2, 0), "Stop A:": (2, 1), "Step A:": (2, 2), "Loop:": (2, 3), "Comp. V:": (2, 4),
        "Meter:": (3, 0), "SR830 REF IN:": (3, 1), "SR830 TC:": (3, 2), "SR830 autorange": (3, 3),
        "Angle start °:": (3, 4),
        "MFLI reference:": (4, 0), "MFLI TC s:": (4, 1), "MFLI autorange": (4, 2),
        "Angle stop °:": (4, 3), "Step °:": (4, 4),
    }
    STFMR_LOCKIN_WIDE_GAPS = (16, 12)
    def _create_connection_indicator(self, parent: tk.Misc, key: str) -> tk.Canvas:
        status = "ref" in key.split("_") or "reference" in key
        canvas, item = create_lamp(parent, kind="status" if status else "connection")
        self.instrument_connection_indicators[key] = (canvas, item)
        return canvas
    def _set_connection_indicator(self, key: str, connected: bool, *, fault: bool = False) -> None:
        set_lamp(self.instrument_connection_indicators.get(key), lamp_state(connected, fault))
    def _configure_ui_styles(self) -> None:
        self.ui_font_family, self.mono_font_family = configure_tk_font_system(self.root)
        use_english_tk_dialogs(self.root)
        style = ttk.Style(self.root)
        if sys.platform == "darwin" and "clam" in style.theme_names():
            style.theme_use("clam")
        font_family = self.ui_font_family
        font_size = int(getattr(self.root, "_moke_ui_font_size", 10))
        small_font_size = int(getattr(self.root, "_moke_ui_small_font_size", 9))
        self.ui_font_size = font_size
        self.ui_small_font_size = small_font_size
        large_font_size = max(font_size + 3, int(round(15 * self.ui_scale)))
        px = self._ui_px
        colors = THEME_COLORS
        palette = {
            "background": colors["page"],
            "surface": colors["surface"],
            "text": colors["text"],
            "muted": colors["muted"],
            "border": colors["border"],
            "accent": colors["accent"],
            "accent_soft": "#e8f1f8",
            "success": colors["success"],
            "warning": colors["warning"],
            "status": colors["status"],
        }
        self.ui_palette = palette
        self.root.configure(background=palette["background"])
        style.configure(".", font=(font_family, font_size))
        style.configure("TFrame", background=palette["surface"])
        style.configure("TLabel", background=palette["surface"], foreground=palette["text"])
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(name, background=palette["surface"], foreground=palette["text"])
            style.map(name, background=[("active", palette["surface"])])
        style.configure('TLabelframe', background=palette['surface'], bordercolor=palette['border'], lightcolor=palette['surface'], darkcolor=palette['surface'], relief='solid', borderwidth=1)
        style.configure('TLabelframe.Label', background=palette['surface'], foreground=palette['text'], font=(font_family, font_size, 'bold'))
        style.configure('TNotebook', background=palette['background'], borderwidth=0, tabmargins=(px(3, 2), px(3, 2), px(3, 2), 0))
        style.configure(
            "TNotebook.Tab",
            padding=(px(14, 7), px(7, 4)),
            font=(font_family, font_size),
            background=colors["tab"],
            foreground=palette["muted"],
            bordercolor=palette["border"],
            lightcolor=colors["tab"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", palette["surface"]), ("active", colors["button_active"])],
            foreground=[("selected", palette["accent"])],
            lightcolor=[("selected", palette["surface"])],
            font=[("selected", (font_family, font_size, "bold"))],
        )
        style.configure('TButton', background=colors['button'], bordercolor=palette['border'], lightcolor=colors['button'], darkcolor=colors['button'])
        style.map('TButton', background=[('disabled', colors['button']), ('pressed', colors['tab']), ('active', colors['button_active'])])
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground=palette['surface'], bordercolor=palette['border'], lightcolor=palette['surface'], darkcolor=palette['surface'])
            style.map(name, bordercolor=[("focus", palette["accent"])], lightcolor=[("focus", palette["accent"])])
        style.configure('Horizontal.TProgressbar', background=palette['accent'], troughcolor=colors['tab'], bordercolor=palette['border'], lightcolor=palette['accent'], darkcolor=palette['accent'])
        style.configure("Overview.TFrame", background=palette["background"])
        style.configure("Overview.TLabel", background=palette["background"], foreground=palette["muted"])
        style.configure('Tooltip.TLabel', background=palette['surface'], foreground=palette['text'], relief='solid', borderwidth=1, padding=(6, 4))
        style.configure("TButton", padding=(px(9, 5), px(6, 3)))
        style.configure("Compact.TButton", padding=(px(6, 4), px(5, 3)))
        style.configure("Inline.TButton", padding=(px(9, 5), px(3, 2)))
        style.configure("TLabel", padding=(0, px(2, 1)))
        style.configure("TCombobox", padding=(px(5, 3), px(5, 3)))
        style.configure("TSpinbox", padding=(px(5, 3), px(5, 3)))
        style.configure("TEntry", padding=(px(5, 3), px(5, 3)))
        style.configure("TCheckbutton", padding=(px(4, 2), px(4, 2)))
        style.configure("TRadiobutton", padding=(px(4, 2), px(4, 2)))
        style.configure('Strong.TButton', foreground=palette['accent'], font=(font_family, font_size, 'bold'))
        try:
            self.ui_button_roles = configure_button_roles(self.root, style)
        except Exception as error:
            self.ui_button_roles = {}
            print(f"Button styles not made: {error}", file=sys.stderr)
        style.configure('Metric.TLabel', foreground=palette['text'], font=(font_family, font_size, 'bold'))
        style.configure('LargeMetric.TLabel', foreground=palette['text'], font=(font_family, large_font_size, 'bold'))
        style.configure("Muted.TLabel", foreground=palette["muted"])
        style.configure("Warning.TLabel", foreground=palette["warning"])
        style.configure('Hint.TLabel', background='#fff1a8', foreground='#6f4500', padding=(px(8, 4), px(4, 2)), font=(font_family, font_size, 'bold'))
        style.configure('Success.TLabel', foreground=palette['success'], font=(font_family, font_size, 'bold'))
        style.configure('Status.TLabel', background=palette['status'], foreground=palette['text'], padding=(px(8, 4), px(4, 2)), relief='flat')
    def _apply_commercial_look(self, widget=None) -> None:
        if not isinstance(self.root, tk.Misc):
            return
        style = ttk.Style(self.root)
        apply_look_to_tree(widget or self.root, style)
        if widget is None and self.__dict__.get("connection_overview") is None:
            try:
                self.connection_overview = ConnectionOverview(self.root, self.notebook, pad=self._ui_px(6, 4))
            except Exception as error:
                self.connection_overview = None
                print(f"Connection overview not shown: {error}", file=sys.stderr)
            self.notebook.bind("<<NotebookTabChanged>>", self._apply_look_to_shown_page, add="+")
    def _apply_look_to_shown_page(self, _event=None) -> None:
        try:
            shown = self.notebook.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return
        apply_look_to_tree(shown, ttk.Style(self.root))
    def _on_notebook_tab_changed(self, _event=None) -> None:
        if self.closing:
            return
        try:
            self.root.after_idle(self._redraw_active_tab)
        except tk.TclError:
            pass
    def _redraw_active_tab(self) -> None:
        # The main program redraws other pages' plots here, never the PPMS page's.
        return
    def save_plot_canvas_snapshot(self, canvas: tk.Canvas, path: Path) -> None:
        if ImageGrab is None:
            raise RuntimeError("Saving a plot snapshot requires Pillow's ImageGrab module")
        self.root.update_idletasks()
        width = int(canvas.winfo_width())
        height = int(canvas.winfo_height())
        if width <= 1 or height <= 1 or not canvas.winfo_ismapped():
            raise RuntimeError("The plot is not visible; cannot capture a snapshot")
        left = int(canvas.winfo_rootx())
        top = int(canvas.winfo_rooty())
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        bbox = (left, top, left + width, top + height)
        try:
            snapshot = ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            snapshot = ImageGrab.grab(bbox=bbox)
        snapshot.save(destination, format="PNG")
    def save_scan_plot_snapshots(self, recorder, plots, *, points: int = 1, redraw=None) -> str:
        try:
            if recorder is None or recorder.closed or int(points) <= 0 or self.closing:
                return ""
            directory = Path(recorder.directory)
            stem = directory.name
            plots = [(suffix, widget) for suffix, widget in plots if widget is not None]
            if not plots:
                return ""
        except BaseException:
            return ""
        saved: list[str] = []
        failed: list[str] = []
        try:
            if redraw is not None:
                redraw()
            self.root.update_idletasks()
            if sys.platform == "win32":
                ctypes.windll.gdi32.GdiFlush()
            time.sleep(SCAN_PLOT_SNAPSHOT_SETTLE_S)
        except BaseException:
            pass
        for suffix, widget in plots:
            try:
                path = directory / f"{stem}_{suffix}_snapshot.png"
                number = 0
                while path.exists():
                    number += 1
                    path = directory / f"{stem}_{suffix}_snapshot_{number:02d}.png"
                self.save_plot_canvas_snapshot(widget, path)
                saved.append(path.name)
            except BaseException as error:
                failed.append(f"{suffix}: {' '.join(str(error).split())[:160]}")
        problem = "; ".join(failed)
        try:
            recorder.event("plot_snapshots", saved=not failed, files=saved, error=problem)
        except BaseException:
            pass
        try:
            self.thread_log(f'{recorder.label} plot snapshot: ' + (', '.join(saved) + ' saved' if saved else 'none saved') + (f'; save failed: {problem}' if failed else ''))
        except BaseException:
            pass
        return f"; plot snapshot save failed: {failed[0]}" if failed else ""
    @staticmethod
    def _draw_plot_grid(canvas, left, top, plot_width, plot_height) -> None:
        for index in range(1, 4):
            fraction = index / 4.0
            x = left + fraction * plot_width
            y = top + fraction * plot_height
            canvas.create_line(x, top, x, top + plot_height, fill=THEME_COLORS['plot_grid'], dash=(2, 3))
            canvas.create_line(left, y, left + plot_width, y, fill=THEME_COLORS['plot_grid'], dash=(2, 3))
    def _ask_scan_start(self, title, question, expected, warning='', **options) -> bool:
        return ask_scan_start(options.pop('parent', self.root), title, question, expected, warning, **options)
    @staticmethod
    def _format_scan_duration(seconds: float) -> str:
        total_seconds = max(0, int(math.ceil(float(seconds))))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:d} h {minutes:02d} min {secs:02d} s"
        if minutes:
            return f"{minutes:d} min {secs:02d} s"
        return f"{secs:d} s"
    def _draw_nonlocal_series(
        self,
        canvas,
        series,
        x_label,
        y_label,
        waiting_text,
        markers=False,
        auto_voltage_unit=False,
        show_legend=True,
        color_override=None,
        series_labels=None,
        series_colors=None,
        marker_radius=3.0,
        series_markers=None,
        symmetric_x_extent=None,
        angle_x_range=None,
        x_range=None,
    ) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 330)
        height = max(canvas.winfo_height(), 120)
        left, right, top, bottom = 86, 24, 24, 52
        if getattr(canvas, "ppms_compact_plot", False):
            width, height = canvas.winfo_width(), canvas.winfo_height()
            if width <= 1 or height <= 1:
                return
            if height < 55:
                canvas.create_text(width / 2, height / 2, text="Increase the window height", anchor="center")
                return
            top, bottom = 14, 44
            legend_names = {(series_labels or {}).get(key, str(key).split('_loop_', 1)[0]) for key, rows in series.items() if rows}
            one_named = any(legend_names) and getattr(canvas, 'compact_legend_strip', False) is True
            if show_legend and (len(legend_names) > 1 or one_named):
                top = 26
        hide_x_labels = getattr(canvas, "ppms_hide_x_labels", False) is True
        if hide_x_labels:
            bottom = 10
        plot_width = max(1, width - left - right)
        plot_height = max(1, height - top - bottom)
        self._draw_plot_grid(canvas, left, top, plot_width, plot_height)
        canvas.create_rectangle(left, top, left + plot_width, top + plot_height, outline='#444444')
        if not hide_x_labels:
            canvas.create_text(left + plot_width / 2, height - 10, text=x_label)
        finite_series = {key: [(float(x), float(y)) for x, y in rows if math.isfinite(float(x)) and math.isfinite(float(y))] for key, rows in series.items()}
        all_rows = [row for rows in finite_series.values() for row in rows]
        if auto_voltage_unit:
            reference = max((abs(row[1]) for row in all_rows), default=0.0)
            if reference >= 1.0:
                voltage_scale, voltage_unit = 1.0, "V"
            elif reference >= 1e-3:
                voltage_scale, voltage_unit = 1e-3, "mV"
            elif reference >= 1e-6:
                voltage_scale, voltage_unit = 1e-6, "µV"
            else:
                voltage_scale, voltage_unit = 1e-9, "nV"
            finite_series = {key: [(x, y / voltage_scale) for x, y in rows] for key, rows in finite_series.items()}
            all_rows = [row for rows in finite_series.values() for row in rows]
            y_label = f"{y_label} ({voltage_unit})"
        canvas.create_text(16, top + plot_height / 2, text=y_label, angle=90)
        if not all_rows:
            canvas.create_text(left + plot_width / 2, top + plot_height / 2, text=waiting_text, fill='#666666')
            return
        xs = [row[0] for row in all_rows]
        ys = [row[1] for row in all_rows]
        if symmetric_x_extent is not None:
            extent = max(abs(float(symmetric_x_extent)), max(abs(x) for x in xs))
            extent = extent if extent > 0 else 1.0
            x_min, x_max, x_ticks, x_tick_step = nice_linear_ticks(-extent, extent)
        elif angle_x_range is not None and float(angle_x_range[0]) != float(angle_x_range[1]):
            x_min, x_max, x_ticks, x_tick_step = angle_axis_ticks(min(float(min(angle_x_range)), min(xs)), max(float(max(angle_x_range)), max(xs)))
        elif x_range is not None:
            x_min, x_max, x_ticks, x_tick_step = nice_linear_ticks(min(float(x_range[0]), min(xs)), max(float(x_range[1]), max(xs)), expand_limits=False)
        else:
            x_min, x_max, x_ticks, x_tick_step = nice_linear_ticks(min(xs), max(xs))
        y_min, y_max, y_ticks, y_tick_step = nice_linear_ticks(min(ys), max(ys))
        for tick_index, x_value in enumerate(x_ticks):
            x = left + (x_value - x_min) / (x_max - x_min) * plot_width
            canvas.create_line(x, top + plot_height, x, top + plot_height + 4)
            if not hide_x_labels:
                canvas.create_text(x, top + plot_height + 15, text=format_nice_tick(x_value, x_tick_step), anchor='w' if tick_index == 0 else 'e' if tick_index == len(x_ticks) - 1 else 'center')
        spacing = plot_height / max(1, len(y_ticks) - 1)
        label_every = max(1, math.ceil(16.0 / spacing)) if spacing > 0 else 1
        for index, y_value in enumerate(reversed(y_ticks)):
            y = top + (y_max - y_value) / (y_max - y_min) * plot_height
            canvas.create_line(left - 4, y, left, y)
            if index % label_every:
                continue
            canvas.create_text(left - 7, y, text=format_nice_tick(y_value, y_tick_step), anchor='e')
        colors = {'8': '#1976a3', '9': '#c43b75', '10': '#7b4ab8', 'mfli': '#008b6f', '2182': '#6a3dad'}
        series_labels = {} if series_labels is None else dict(series_labels)
        series_colors = {} if series_colors is None else dict(series_colors)
        legend_x = left + 8
        legend_drawn: set[str] = set()
        order = {"8": 0, "9": 1, "10": 2, "mfli": 3, "2182": 4}
        def base_series_key(series_key):
            return str(series_key).split("_loop_", 1)[0]
        def series_sort_key(series_key):
            frequency_match = re.search('_freq_([-+0-9.eE]+)_loop_(\\d+)$', str(series_key))
            if frequency_match is not None:
                return (1, float(frequency_match.group(1)), int(frequency_match.group(2)))
            return (0, order.get(base_series_key(series_key), 99), str(series_key))
        for key in sorted(finite_series, key=series_sort_key):
            rows = finite_series[key]
            if not rows:
                continue
            base_key = base_series_key(key)
            color = color_override or series_colors.get(key) or series_colors.get(base_key) or colors.get(base_key, '#555555')
            points = []
            for x_value, y_value in rows:
                x = left + (x_value - x_min) / (x_max - x_min) * plot_width
                y = top + plot_height - (y_value - y_min) / (y_max - y_min) * plot_height
                points.extend((x, y))
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2)
            if markers:
                shape = (series_markers or {}).get(key)
                hollow = shape == "open_circle"
                radius = max(2.5 if hollow else 0.5, float(marker_radius))
                marker = canvas.create_rectangle if shape == "square" else canvas.create_oval
                fill = "white" if hollow else color
                outline = color if hollow else ""
                marker_width = 0.75 if hollow else 1
                drawn = set()
                offset = radius if float(radius).is_integer() or (2 * radius).is_integer() else 0.0
                for x, y in zip(points[0::2], points[1::2]):
                    if offset:
                        pixel = (round(x - offset), round(y - offset))
                        if pixel in drawn:
                            continue
                        drawn.add(pixel)
                    marker(x - radius, y - radius, x + radius, y + radius, fill=fill, outline=outline, width=marker_width)
            legend_label = series_labels.get(key)
            if legend_label is None:
                legend_label = 'Keithley 2182A' if base_key == '2182' else 'Zurich MFLI' if base_key == 'mfli' else f'SR830 #{base_key}'
            if show_legend and legend_label and legend_label not in legend_drawn:
                legend_drawn.add(legend_label)
                canvas.create_line(legend_x, 12, legend_x + 18, 12, fill=color, width=2)
                if series_markers:
                    marker = canvas.create_rectangle if series_markers.get(key) == "square" else canvas.create_oval
                    hollow = series_markers.get(key) == "open_circle"
                    marker(legend_x + 6, 9, legend_x + 12, 15, fill='white' if hollow else color, outline=color if hollow else '', width=0.75 if hollow else 1)
                item = canvas.create_text(legend_x + 22, 12, text=legend_label, anchor='w', fill=color)
                try:
                    end = float(canvas.bbox(item)[2])
                except (AttributeError, TypeError, IndexError, ValueError, tk.TclError):
                    end = legend_x + 22 + 7 * len(str(legend_label))
                legend_x = max(legend_x + 82, end + 14)
    NONLOCAL_VI_RESISTANCE_HEADER = ('timestamp', 'stage', 'point_index', 'pulse_voltage_V', 'source_current_A', 'measured_voltage_V', 'measured_current_A', 'absolute_resistance_Ohm', 'loop')
    NONLOCAL_SCAN_NAMES = {'resistance': ('|R| vs time', 'NL_RvsT'), 'iv': ('2400-IV', 'NL_2400IV'), 'iv6221': ('6221-IV', 'NL_6221IV'), 'gate': ('Gate-VI', 'NL_GateVI')}
    NONLOCAL_RESISTANCE_COLUMNS = ('point', 'elapsed_s', 'source_current_A', 'voltage_V', 'current_A', 'resistance_Ohm', 'timestamp')
    NONLOCAL_IV_COLUMNS = ('point', 'set_voltage_V', 'source_voltage_V', 'current_A', 'elapsed_s', 'at_current_limit')
    def select_ppms_qdinstrument_dll(self) -> None:
        current = self.ppms_qdinstrument_dll_var.get().strip()
        initial_directory = None
        if current:
            current_path = Path(current)
            if current_path.parent.is_dir():
                initial_directory = str(current_path.parent)
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Select the QDInstrument.dll currently used by LabVIEW",
            initialdir=initial_directory,
            filetypes=(("QDInstrument DLL", "QDInstrument.dll"), ("DLL", "*.dll")),
        )
        if selected:
            self.ppms_qdinstrument_dll_var.set(selected)
    def auto_find_ppms_qdinstrument_dll(self) -> None:
        if self.ppms_environment_busy:
            return
        self.ppms_environment_busy = True
        self.ppms_connection_var.set("Searching for QDInstrument.dll (time-limited)…")
        self._set_ppms_environment_ui(False)
        explicit = self.ppms_qdinstrument_dll_var.get().strip()
        future = self.ppms_executor.submit(PPMSQDInstrumentReadbackController.discover_dll_paths, explicit, recursive=True, time_limit_s=12.0, max_results=8)
        def finished() -> None:
            self.ppms_environment_busy = False
            try:
                matches = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(f'DLL search failed: {short_status_error(error, 100)}')
                self._set_ppms_environment_ui(False)
                if not self.closing:
                    messagebox.showerror('QDInstrument auto-search', str(error), parent=self.root)
                return
            self._set_ppms_environment_ui(False)
            if matches:
                selected = str(matches[0])
                self.ppms_qdinstrument_dll_var.set(selected)
                self.ppms_connection_var.set(f"DLL found: {selected}")
                if len(matches) > 1 and not self.closing:
                    options = "\n".join(f"  - {path}" for path in matches)
                    messagebox.showinfo(
                        "QDInstrument auto-search",
                        "Several QDInstrument.dll copies were found; the first one was selected. If it does not match the LabVIEW "
                        f"version, please pick the correct one manually:\n{options}",
                        parent=self.root,
                    )
                return
            self.ppms_connection_var.set("DLL not found; you may leave the field empty and try .NET auto-loading")
            if not self.closing:
                messagebox.showwarning(
                    "QDInstrument auto-search",
                    "The time-limited search did not find QDInstrument.dll. A reachable TCP 11000 does not mean this PC "
                    "has the client assembly. Search the folder of the original LabVIEW control VI for "
                    "QDInstrument.dll, select it or copy it next to this program. If the assembly is already "
                    "installed on the .NET search path, you may also leave the field empty and connect directly.",
                    parent=self.root,
                )
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def _on_ppms_backend_changed(self, _event=None) -> None:
        backend = self.ppms_backend_var.get()
        if backend == PPMS_QDINSTRUMENT_BACKEND:
            default_port = PPMS_QDINSTRUMENT_DEFAULT_PORT
        else:
            default_port = PPMS_MULTIPYVU_DEFAULT_PORT
        if not self.ppms_environment_controller.connected and not self.ppms_environment_busy:
            self.ppms_port_var.set(default_port)
        self._update_ppms_control_ui()
    def _set_ppms_environment_ui(self, connected: bool) -> None:
        self._set_connection_indicator("ppms_environment", connected)
        self.ppms_connect_button.configure(state='disabled' if connected or self.ppms_environment_busy else 'normal')
        self.ppms_disconnect_button.configure(state='normal' if connected and (not self.ppms_environment_busy) else 'disabled')
        self.ppms_read_button.configure(state='normal' if connected and (not self.ppms_environment_busy) else 'disabled')
        entry_state = "disabled" if connected or self.ppms_environment_busy else "normal"
        self.ppms_host_entry.configure(state=entry_state)
        self.ppms_port_entry.configure(state=entry_state)
        self.ppms_backend_combo.configure(state='disabled' if connected or self.ppms_environment_busy else 'readonly')
        if not connected and not self.ppms_environment_busy:
            self.ppms_connection_var.set("Not connected")
        self._update_ppms_control_ui()
    def _update_ppms_control_ui(self) -> None:
        widgets = getattr(self, "ppms_control_widgets", ())
        connected = self.ppms_environment_controller.connected
        enabled = (
            connected
            and bool(self.ppms_control_safety_var.get())
            and not self.ppms_environment_busy
            and not self.ppms_field_scan_running
            and not getattr(self, "ppms_motor_pending", False)
        )
        motor_ready = connected and (not self.ppms_environment_busy) and (not self.ppms_field_scan_running) and (getattr(self.ppms_environment_controller, 'backend', '') == PPMS_QDINSTRUMENT_BACKEND)
        for name in ("ppms_motor_read_button", "ppms_motor_info_button"):
            if hasattr(self, name):
                getattr(self, name).configure(state="normal" if motor_ready else "disabled")
        if hasattr(self, "ppms_motor_move_button"):
            self.ppms_motor_move_button.configure(state="normal" if motor_ready and enabled else "disabled", text="Move")
        for widget in widgets:
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="readonly" if enabled else "disabled")
                else:
                    widget.configure(state="normal" if enabled else "disabled")
            except (AttributeError, tk.TclError):
                pass
        choices_ready = not self.ppms_environment_busy and not self.ppms_field_scan_running
        for widget, state in self.__dict__.get('ppms_lockin_choice_widgets', ()):
            widget.configure(state=state if choices_ready else 'disabled')
        self._update_ppms_sr830_tc_state()
        self._update_ppms_scan_target_widgets(enabled)
        if hasattr(self, "ppms_field_scan_stop_button"):
            self.ppms_field_scan_stop_button.configure(state='normal' if self.ppms_field_scan_running else 'disabled')
        if "ppms_zero_persist_button" in self.__dict__:
            self.ppms_zero_persist_button.configure(state='normal' if self.ppms_field_scan_running else 'disabled')
        if hasattr(self, "ppms_field_scan_save_button"):
            self.ppms_field_scan_save_button.configure(state='normal' if self.ppms_field_scan_rows and (not self.ppms_field_scan_running) else 'disabled')
        for key, controller in self.ppms_instrument_controllers.items():
            if key in getattr(self, "ppms_device_connect_buttons", {}):
                self._set_ppms_device_ui(key, controller.connected)
    @staticmethod
    def _ppms_temperature_approach_code(label: str) -> int:
        mapping = {"Fast settle": 0, "No overshoot": 1}
        try:
            return mapping[str(label)]
        except KeyError as error:
            raise ValueError("Please select a valid PPMS temperature approach") from error
    def _update_ppms_scan_target_widgets(self, enabled):
        mode_var = getattr(self, "ppms_simple_mode", None)
        mode = mode_var.get() if mode_var is not None else ""
        scanned = {"Temperature", "Field"} if mode == "Field+Temperature" else {"Angle", "Temperature"} if mode == "Angle+Temperature" else {mode}
        if mode == "Current+Temperature":
            scanned = {"Current", "Temperature"}
        if mode == "Frequency+Current":
            scanned = {"Current", "Frequency"}
        if mode == "Angle+Field":
            scanned = {"Angle", "Field"}
        for parameter, widgets in getattr(self, "ppms_scan_target_widgets", {}).items():
            for widget in widgets:
                widget.configure(state="normal" if enabled and parameter not in scanned else "disabled")
        step = self.__dict__.get("ppms_scan_step_entry")
        if step is not None and mode == "Frequency":
            step.configure(state="disabled")
    @staticmethod
    def _ppms_field_approach_code(label: str) -> int:
        mapping = {"Linear": 0, "No overshoot": 1, "Oscillate": 2}
        try:
            return mapping[str(label)]
        except KeyError as error:
            raise ValueError("Please select a valid PPMS field approach") from error
    @staticmethod
    def _ppms_field_mode_code(label: str) -> int:
        mapping = {"Persistent": 0, "Driven": 1}
        try:
            return mapping[str(label)]
        except KeyError as error:
            raise ValueError("Please select a valid PPMS field end mode") from error
    def _require_ppms_control_ready(self) -> None:
        if not self.ppms_environment_controller.connected:
            raise RuntimeError("Please connect the PPMS first")
        if not self.ppms_control_safety_var.get():
            raise RuntimeError("Please tick the PPMS safety confirmation first")
        if self.ppms_field_scan_running:
            raise RuntimeError("The PPMS is scanning field; stop the scan first")
        if getattr(self, "ppms_motor_pending", False):
            raise RuntimeError("Motor status after the last command is unconfirmed; check in MultiVu that it stopped, then click Read angle")
    def _ppms_publish_motor_readback(self, data):
        text = f"{data['angle_deg']:.4f}° / {data['status']}"
        def update():
            if not self.closing and hasattr(self, "ppms_angle_live_var"):
                self.ppms_angle_live_var.set(text)
        self.post_ui(update)
    def _ppms_confirmed_manual_move(self, probe, target, speed):
        probe.on_read = self._ppms_publish_motor_readback
        before = probe.read()
        probe.validate_move(target, speed, before["angle_deg"], allow_large=True)
        if before["status"] not in ("AtTarget", "AtIndexSwitch"):
            raise RuntimeError(f"Motion status is not AtTarget: {before['status']}; MOVE not sent")
        large = abs(target - before["angle_deg"]) > 30.
        if large:
            answered = threading.Event()
            accepted = []
            def ask():
                try:
                    accepted.append(not self.closing and messagebox.askyesno(
                        "PPMS large rotation warning",
                        f"Current {before['angle_deg']:.4f}° → target {target:g}°, rotation exceeds 30°.\n"
                        "Confirm the mechanical travel and wiring clearance are safe; there is no software motor emergency stop. Continue?",
                        parent=self.root, default="no"))
                finally:
                    answered.set()
            self.post_ui(ask)
            while not answered.wait(.1):
                if self.closing:
                    raise PPMSScanCancelled("Move cancelled; MOVE not sent")
            if not accepted or not accepted[0]:
                raise PPMSScanCancelled("Move cancelled; MOVE not sent")
        return probe.move_and_verify(target, speed, cancelled=lambda: self.closing, allow_large=large, timeout=900.0 if large else 90.0)
    def _ppms_motor_action(self, action):
        if self.ppms_environment_busy or self.ppms_field_scan_running or self.closing:
            return
        try:
            remote = self.ppms_environment_controller
            if not remote.connected or remote.backend != PPMS_QDINSTRUMENT_BACKEND:
                raise RuntimeError("Motor debugging only supports a connected QDInstrument remote interface")
            probe = PPMSMotorDebug(remote.active)
            if action == "move":
                self._require_ppms_control_ready()
                if not self.ppms_motor_confirm_var.get():
                    raise RuntimeError("Please tick the motor-specific confirmation first and verify the decel code and mechanical safety")
                target, speed = float(self.ppms_motor_target_var.get()), float(self.ppms_motor_speed_var.get())
                self.ppms_motor_pending = True
                self.ppms_motor_expected = target
                operation = lambda: self._ppms_confirmed_manual_move(probe, target, speed)
            elif action == "read":
                operation = probe.read
            elif action == "info":
                operation = probe.describe
            else:
                raise ValueError("Unknown motor debug action")
        except Exception as error:
            messagebox.showwarning("PPMS motor", str(error), parent=self.root)
            return
        self.ppms_environment_busy = True
        if action == "move":
            self.ppms_motor_status_var.set("Verifying rotation and arrival; do not repeat the command")
        self._set_ppms_environment_ui(True)
        future = self.ppms_executor.submit(operation)
        def finished():
            self.ppms_environment_busy = False
            try:
                result = future.result()
                if self.closing:
                    return
                if action == "info":
                    self.append_log("PPMS motor interface:\n" + result)
                    window = tk.Toplevel(self.root)
                    window.title("PPMS motor interface info (copyable)")
                    box = tk.Text(window, width=100, height=22, wrap="word")
                    box.pack(fill="both", expand=True)
                    box.insert("1.0", result)
                elif action == "read":
                    self.ppms_motor_status_var.set(f"Angle {result['angle_deg']:.4f}°; DLL status: {result['status']}")
                    if result["status"] in ("AtTarget", "AtIndexSwitch") and (not self.ppms_motor_pending or abs(result["angle_deg"] - getattr(self, "ppms_motor_expected", result["angle_deg"])) <= 0.06):
                        self.ppms_motor_pending = False
                    if not self.ppms_motor_target_var.get():
                        self.ppms_motor_target_var.set(f"{result['angle_deg']:.4f}")
                else:
                    self.ppms_motor_pending = False
                    note = "; command reply uncertain, confirmed by angle and status readback" if result["ambiguous"] else ""
                    self.ppms_motor_status_var.set(f"Angle confirmed: {result['angle_deg']:.4f}°; {result['status']} (two readbacks){note}")
                    self.ppms_angle_live_var.set(f"{result['angle_deg']:.4f}° / {result['status']}")
            except PPMSScanCancelled as error:
                self.ppms_motor_pending = False
                self.ppms_motor_status_var.set(str(error))
            except Exception as error:
                if action == "move" and not getattr(probe, "movement_attempted", False):
                    self.ppms_motor_pending = False
                self.ppms_motor_status_var.set(f"Debug failed: {short_status_error(error, 180)}")
                if not self.closing:
                    messagebox.showerror("PPMS motor debug", str(error), parent=self.root)
            finally:
                if not self.closing:
                    self._set_ppms_environment_ui(remote.connected)
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def open_ppms_macro(self):
        existing = getattr(self, "ppms_macro_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        window = self.ppms_macro_window = tk.Toplevel(self.root)
        window.title("PPMS Macro")
        window.geometry("860x580")
        window.transient(self.root)
        window.rowconfigure(1, weight=1)
        window.columnconfigure(0, weight=1)
        ttk.Label(window, text="Executed line by line; TEMP K [K/min] [approach]; FIELD Oe Oe/s [approach mode]; WAIT seconds; SCAN\n"
                  "Supports TMP TEMP / FLD FIELD / WAI WAITFOR seconds temperature field 0 0 0; REM comment. SCAN uses the main-window settings and asks for its run folder and file name (one run folder per SCAN).",
                  wraplength=820).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        editor = self.ppms_macro_editor = tk.Text(window, wrap="none", undo=True)
        editor.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        scroll = ttk.Scrollbar(window, command=editor.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        editor.configure(yscrollcommand=scroll.set)
        editor.tag_configure("active_line", background="#fff0b0", foreground="black")
        editor.insert("1.0", getattr(self, "ppms_macro_text", "REM Example: run one transport scan with the main-window settings\nSCAN\nREM Zero the field and enter Persistent after the scan\nFIELD 0 100 0 0\nWAI WAITFOR 1 0 1 0 0 0\n"))
        self.ppms_macro_status = tk.StringVar(value="Not running; Stop Macro stops the remaining commands, temperature/field targets already sent are kept.")
        ttk.Label(window, textvariable=self.ppms_macro_status, wraplength=820).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        bar = ttk.Frame(window)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=8)
        def validate():
            try:
                commands = parse_ppms_macro(editor.get("1.0", "end-1c"))
                self.ppms_macro_status.set(f"Validation passed: {len(commands)} commands; nothing sent to hardware.")
                return commands
            except ValueError as error:
                self.ppms_macro_status.set(str(error))
                return None
        def load():
            path = filedialog.askopenfilename(parent=window, filetypes=(("Sequence", "*.seq"), ("Text", "*.txt"), ("All", "*")))
            if path:
                try:
                    data = Path(path).read_bytes()
                    try:
                        text = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        text = data.decode("gb18030")
                    editor.delete("1.0", "end")
                    editor.insert("1.0", text)
                    validate()
                except Exception as error:
                    self.ppms_macro_status.set(f"Load failed: {error}")
        def save():
            path = filedialog.asksaveasfilename(parent=window, defaultextension=".seq", filetypes=(("Sequence", "*.seq"),))
            if path:
                try:
                    Path(path).write_text(editor.get("1.0", "end-1c"), encoding="utf-8")
                except Exception as error:
                    self.ppms_macro_status.set(f"Save failed: {error}")
        def run():
            if getattr(self, "ppms_macro_running", False):
                return
            commands = validate()
            if commands is None:
                return
            try:
                self._require_ppms_control_ready()
                if self.ppms_environment_busy:
                    raise RuntimeError("Please wait for the current PPMS command to finish")
            except Exception as error:
                self.ppms_macro_status.set(str(error))
                return
            self.ppms_macro_running = True
            self.ppms_macro_stop = threading.Event()
            self.ppms_macro_commands = iter(commands)
            self.ppms_macro_text = editor.get("1.0", "end-1c")
            editor.configure(state="disabled")
            for button in self.ppms_macro_edit_buttons:
                button.configure(state="disabled")
            window.grab_set()
            self._ppms_macro_next()
        self.ppms_macro_edit_buttons = []
        for label, callback in (("Load", load), ("Save", save), ("Validate", validate), ("Run", run)):
            button = ttk.Button(bar, text=label, command=callback)
            button.pack(side="left", padx=2)
            self.ppms_macro_edit_buttons.append(button)
        ttk.Button(bar, text="Stop Macro", command=self._stop_ppms_macro).pack(side="left", padx=2)
        def emergency():
            self._stop_ppms_macro()
            self.ppms_sources_off()
        ttk.Button(bar, text="6221 / 2400 output off now", command=emergency).pack(side="right")
        def close():
            if getattr(self, "ppms_macro_running", False):
                self._stop_ppms_macro()
                return
            self.ppms_macro_text = editor.get("1.0", "end-1c")
            window.destroy()
        window.protocol("WM_DELETE_WINDOW", close)
    def _stop_ppms_macro(self):
        if getattr(self, "ppms_macro_running", False):
            self.ppms_macro_stop.set()
            self.stop_ppms_field_scan()
            self.ppms_macro_status.set("Stopping Macro; waiting for the current command to return, remaining commands will not run.")
    def _ppms_macro_finish(self, message):
        self.ppms_macro_running = False
        self.ppms_macro_status.set(message)
        self.ppms_macro_editor.configure(state="normal")
        for button in self.ppms_macro_edit_buttons:
            button.configure(state="normal")
        self.ppms_macro_window.grab_release()
    def _ppms_macro_next(self):
        if self.closing or self.ppms_macro_stop.is_set():
            self._ppms_macro_finish("Macro stopped; settings already sent are kept.")
            return
        command = next(self.ppms_macro_commands, None)
        if command is None:
            self._ppms_macro_finish("Macro completed.")
            return
        line, op, args = command
        missing = 'auto' if op == 'TEMP' else '-'
        shown = ' '.join(missing if arg is None else str(arg) for arg in args)
        self.ppms_macro_status.set(f"Running line {line}: {op} {shown}")
        self.ppms_macro_editor.tag_remove("active_line", "1.0", "end")
        self.ppms_macro_editor.tag_add("active_line", f"{line}.0", f"{line}.end")
        self.ppms_macro_editor.see(f"{line}.0")
        self.thread_log(f"PPMS Macro line {line}: {op} {args}")
        if op == "SCAN":
            self.ppms_macro_scan_result = None
            try:
                self.start_ppms_field_scan()
            except Exception as error:
                self._ppms_macro_finish(f"Line {line}: scan failed to start: {error}")
                return
            if not self.ppms_field_scan_running:
                self._ppms_macro_finish(f"Line {line}: scan not started or save cancelled; Macro stopped.")
                return
            def poll_scan():
                if self.ppms_field_scan_running:
                    self.root.after(100, poll_scan)
                elif self.ppms_macro_scan_result is True:
                    self._ppms_macro_next()
                else:
                    self._ppms_macro_finish(f"Line {line}: scan stopped or failed; Macro stopped.")
            self.root.after(100, poll_scan)
            return
        if op == "TEMP":
            self.ppms_temperature_target_var.set(str(args[0]))
            if args[1] is not None:
                self.ppms_temperature_rate_var.set(f"{args[1]:g}")
        elif op == "FIELD":
            self.ppms_field_target_var.set(str(args[0]))
            self._end_ppms_persistent_follow_up("The Macro")
        self.ppms_environment_busy = True
        self._set_ppms_environment_ui(True)
        try:
            future = self.ppms_executor.submit(self._ppms_macro_operation, op, args)
        except Exception as error:
            self.ppms_environment_busy = False
            self._set_ppms_environment_ui(True)
            self._ppms_macro_finish(f"Line {line} not started: {error}")
            return
        def finished():
            self.ppms_environment_busy = False
            self._set_ppms_environment_ui(True)
            try:
                future.result()
            except Exception as error:
                self._ppms_macro_finish(f"Line {line} stopped: {error}")
                return
            self.root.after(0, self._ppms_macro_next)
        future.add_done_callback(lambda _: self.post_ui(finished))
    def _ppms_macro_operation(self, op, args):
        stop = self.ppms_macro_stop
        if stop.is_set() or self.closing:
            raise PPMSScanCancelled("Macro stopped")
        remote = self.ppms_environment_controller
        if op == "TEMP":
            current = float(remote.read_environment()["temperature_k"])
            target, rate, approach = args
            if rate is None:
                rate = PPMS_TEMPERATURE_MAX_RATE_K_MIN if math.isfinite(current) and min(current, target) > PPMS_TEMPERATURE_LOW_BOUNDARY_K else PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                self.thread_log(f'Macro TEMP {target:g} K without a rate: {rate:g} K/min (live {current:g} K)')
            validate_ppms_temperature_command(target, rate, current)
            remote.set_temperature(target, rate, approach)
        elif op == "FIELD":
            remote.set_field(*args)
        else:
            delay = args[0]
            deadline = time.monotonic() + 14400 + delay
            stable_since = None
            while True:
                if stop.is_set() or self.closing:
                    raise PPMSScanCancelled("Macro stopped")
                ready = True
                if op == "WAITFOR":
                    data = remote.read_environment()
                    self.post_ui(lambda d=dict(data): self._apply_ppms_environment_readback(d))
                    if args[1] is not None:
                        ready = abs(float(data['temperature_k']) - args[1]) <= 0.1 and str(data['temperature_status']).lower().replace(' ', '') in ('stable', 'stableattarget')
                    if args[2] is not None:
                        ready = ready and self._ppms_field_is_ready(data, args[2], driven=args[3] == 1)
                        if args[3] == 0:
                            ready = ready and str(data["field_status"]).lower().replace(" ", "") in ("persistent", "stablepersistent")
                now = time.monotonic()
                stable_since = (now if stable_since is None else stable_since) if ready else None
                if stable_since is not None and now - stable_since >= delay:
                    return
                if now >= deadline:
                    raise TimeoutError("Timed out waiting for temperature/field to stabilise")
                stop.wait(.2)
    def _run_ppms_command(self, label: str, operation: Callable[[], dict[str, object] | None], *, success_text: str='Command completed; live readback in progress') -> None:
        if self.ppms_environment_busy:
            return
        try:
            self._require_ppms_control_ready()
        except BaseException as error:
            messagebox.showwarning("PPMS control", str(error), parent=self.root)
            return
        self.ppms_environment_busy = True
        self.ppms_connection_var.set(label)
        self._set_ppms_environment_ui(True)
        try:
            future = self.ppms_executor.submit(operation)
        except Exception as error:
            self.ppms_environment_busy = False
            self.ppms_connection_var.set(f"Command not started: {short_status_error(error, 120)}")
            self._set_ppms_environment_ui(True)
            if not self.closing:
                messagebox.showerror("PPMS control failed", str(error), parent=self.root)
            return
        def finished() -> None:
            self.ppms_environment_busy = False
            try:
                result = future.result()
                if result is not None:
                    self._apply_ppms_environment_readback(result)
                if result is not None or not self.ppms_field_scan_stop_event.is_set():
                    self.ppms_connection_var.set(result.get('command_verification', success_text) if result is not None else success_text)
            except PPMSScanCancelled:
                pass
            except BaseException as error:
                self.ppms_connection_var.set(f'Command failed: {short_status_error(error, 120)}')
                if not self.closing:
                    messagebox.showerror("PPMS control failed", str(error), parent=self.root)
            finally:
                self._set_ppms_environment_ui(True)
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def _ppms_adjust_low_temperature_rate(self, *_args):
        try:
            target = float(self.ppms_temperature_target_var.get())
            if math.isfinite(target) and target > 0:
                rate = '1' if target < 10 else '10'
                if self.ppms_temperature_rate_var.get() != rate:
                    self.ppms_temperature_rate_var.set(rate)
        except (ValueError, TypeError, tk.TclError):
            pass
    def set_ppms_temperature(self) -> None:
        try:
            if float(self.ppms_temperature_target_var.get()) < 10 and float(self.ppms_temperature_rate_var.get()) > 1:
                self.ppms_temperature_rate_var.set('1')
        except (ValueError, TypeError, tk.TclError):
            pass
        try:
            target = float(self.ppms_temperature_target_var.get())
            rate = float(self.ppms_temperature_rate_var.get())
            current = None
            if self.ppms_last_readback is not None:
                current = float(self.ppms_last_readback["temperature_k"])
                if current <= PPMS_TEMPERATURE_LOW_BOUNDARY_K and rate > PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN:
                    rate = PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                    self.ppms_temperature_rate_var.set('1')
            validate_ppms_temperature_command(target, rate, current)
            approach = self._ppms_temperature_approach_code(self.ppms_temperature_approach_var.get())
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror("PPMS temperature parameters", str(error), parent=self.root)
            return
        self._run_ppms_command(f'Setting temperature {target:g} K…', lambda: self.ppms_environment_controller.set_temperature(target, rate, approach))
    def _parse_ppms_field_parameters(self, target: float | None=None) -> tuple[float, float, int, int]:
        if target is None:
            target = float(self.ppms_field_target_var.get())
        rate = float(self.ppms_field_rate_var.get())
        target, rate = validate_ppms_field_command(target, rate)
        approach = self._ppms_field_approach_code(self.ppms_field_approach_var.get())
        mode = self._ppms_field_mode_code(self.ppms_field_mode_var.get())
        return target, rate, approach, mode
    def set_ppms_field(self) -> None:
        try:
            target, rate, approach, mode = self._parse_ppms_field_parameters()
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror("PPMS field parameters", str(error), parent=self.root)
            return
        self._send_ppms_field_setpoint(f'Setting field {target:+g} Oe…', target, rate, approach, mode)
    def _send_ppms_field_setpoint(self, label, target, rate, approach, mode):
        remote = self.ppms_environment_controller
        follow = mode == 0 and getattr(remote, "backend", "") == PPMS_QDINSTRUMENT_BACKEND
        def operation():
            self.ppms_persistent_watch = None
            result = remote.set_field(target, rate, approach, mode)
            if not follow:
                return result
            sent = time.monotonic()
            try:
                distance = abs(target - float(result["field_oe"]))
            except (KeyError, TypeError, ValueError):
                distance = abs(target)
            return dict(result, persistent_watch={'target': target, 'sent': sent, 'left_persistent': False, 'deadline': sent + distance / rate + PPMS_PERSISTENT_MARGIN_S})
        if follow:
            success_text = f"Going to {target:+g} Oe, then Persistent…"
        elif mode == 0:
            success_text = f"Sent {target:+g} Oe, ending Persistent; see the field status"
        else:
            success_text = "Command completed; live readback in progress"
        self._run_ppms_command(label, operation, success_text=success_text)
    def _end_ppms_persistent_follow_up(self, owner: str) -> None:
        if self.__dict__.get("ppms_persistent_watch"):
            self.ppms_persistent_watch = None
            self.ppms_connection_var.set(f"{owner} now sets the field; Persistent no longer followed")
    def _follow_ppms_persistent_setpoint(self, result) -> None:
        watch = self.__dict__.get("ppms_persistent_watch")
        if not watch:
            return
        read_at = result.get("read_at")
        if not isinstance(read_at, (int, float)) or read_at <= watch["sent"]:
            return
        try:
            measured = float(result["field_oe"])
        except (KeyError, TypeError, ValueError):
            return
        target = watch["target"]
        status = str(result.get("field_status", ""))
        tolerance = max(PPMS_FIELD_TOLERANCE_OE, abs(target) * PPMS_FIELD_RELATIVE_TOLERANCE)
        at_target = abs(measured - target) <= tolerance
        if status.strip().lower().replace(" ", "") not in ("persistent", "stablepersistent"):
            watch["left_persistent"] = True
        elif at_target and (watch['left_persistent'] or read_at - watch['sent'] >= PPMS_PERSISTENT_CONFIRM_S):
            self.ppms_persistent_watch = None
            self.ppms_connection_var.set(f"Field {target:+g} Oe reached; Persistent confirmed")
            return
        elif watch["left_persistent"] and not at_target:
            self.ppms_persistent_watch = None
            self.ppms_connection_var.set(f'Persistent at {measured:+.7g} Oe, not {target:+g} Oe; check MultiVu, not resent')
            return
        if read_at >= watch["deadline"]:
            self.ppms_persistent_watch = None
            self.ppms_connection_var.set(
                f"{status} at {measured:+.7g} Oe, not Persistent at {target:+g} Oe in time; "
                "check MultiVu, not resent"
                if watch["left_persistent"] else
                f"Still Persistent at {measured:+.7g} Oe, {target:+g} Oe not reached; "
                "check MultiVu, not resent"
            )
    def switch_ppms_persistent(self) -> None:
        if self.ppms_field_scan_running:
            messagebox.showwarning("PPMS Persistent", "Stop the scan before switching the field mode.", parent=self.root)
            return
        try:
            rate = float(self.ppms_field_rate_var.get())
            validate_ppms_field_command(0.0, rate)
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror("PPMS Persistent", str(error), parent=self.root)
            return
        def operation():
            remote = self.ppms_environment_controller
            data = remote.read_environment()
            status = str(data.get("field_status", "")).strip().lower()
            if status in ("persistent", "stablepersistent"):
                return dict(data, command_verification="Field confirmed Persistent; no set command sent")
            if status not in ("stabledriven", "driven", "holding", "stable"):
                raise RuntimeError(f"Field not yet stable, not switching to Persistent: {data.get('field_status')}")
            target, _ = validate_ppms_field_command(float(data["field_oe"]), rate)
            result = remote.set_field(target, rate, 0, 0)
            return dict(result, command_verification=(
                "Field confirmed Persistent"
                if str(result.get("field_status", "")).strip().lower() in ("persistent", "stablepersistent")
                else "Persistent requested; the switch may take time, watch the live field status"))
        self._run_ppms_command("Switching to Persistent at the current measured field…", operation)
    def zero_ppms_field(self) -> None:
        try:
            _target, rate, approach, mode = self._parse_ppms_field_parameters(0.0)
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror("PPMS field parameters", str(error), parent=self.root)
            return
        self.ppms_field_target_var.set("0")
        self._send_ppms_field_setpoint("Zeroing field…", 0.0, rate, approach, mode)
    def step_ppms_field(self, direction: int) -> None:
        try:
            if self.ppms_last_readback is None:
                raise RuntimeError("No PPMS field readback yet; click Read now first")
            step = abs(float(self.ppms_field_step_var.get()))
            if not math.isfinite(step) or step <= 0:
                raise ValueError("PPMS field step must be greater than 0 Oe")
            current = float(self.ppms_last_readback["field_oe"])
            target = current + (1 if int(direction) > 0 else -1) * step
            target, rate, approach, mode = self._parse_ppms_field_parameters(target)
        except (ValueError, TypeError, RuntimeError, tk.TclError) as error:
            messagebox.showerror("PPMS field step", str(error), parent=self.root)
            return
        self.ppms_field_target_var.set(f"{target:.9g}")
        self._send_ppms_field_setpoint(f'Stepping to {target:+g} Oe…', target, rate, approach, mode)
    def _apply_ppms_environment_readback(self, result: dict[str, object]) -> None:
        result = dict(result)
        watch = result.pop("persistent_watch", None)
        if watch is not None:
            self.ppms_persistent_watch = dict(watch)
        self.ppms_last_readback = dict(result)
        if result.get("command_verification"):
            self.ppms_connection_var.set(result["command_verification"])
        self.ppms_temperature_var.set(f"{float(result['temperature_k']):.6g} K")
        self.ppms_temperature_status_var.set(f"Status: {result['temperature_status']}")
        self.ppms_field_var.set(f"{float(result['field_oe']):+.7g} Oe")
        self.ppms_field_status_var.set(f"Status: {result['field_status']}")
        self._follow_ppms_persistent_setpoint(result)
        if hasattr(self, "ppms_angle_live_var"):
            self.ppms_angle_live_var.set(result.get("motor_display", "-- °"))
        if hasattr(self, "ppms_current_live_var"):
            self.ppms_current_live_var.set(result.get("current_display", "Unknown / not read"))
        for name, key in (("ppms_pressure_live_var", "pressure_display"), ("ppms_helium_live_var", "helium_display")):
            if hasattr(self, name):
                getattr(self, name).set(result.get(key, "Unknown / not read"))
        self._draw_ppms_helium_history()
        self._check_ppms_helium_warning(result)
        for key, values in result.get("lockin_reference", {}).items():
            self.ppms_xy_vars[key].set(f"{PPMS_SR830_LABELS.get(key, key)}: {values}")
            self._set_connection_indicator(f'ppms_ref_{key}', str(values).endswith('LOCK reference locked'), fault='OVERLOAD' in str(values))
            if key in getattr(self, "ppms_sr830_status_vars", {}):
                text = str(values)
                self.ppms_sr830_status_vars[key].set(text if 'OVERLOAD' in text else text.split(' / ')[-1])
    @staticmethod
    def _ppms_voltage_text(value):
        magnitude = abs(value)
        scale, unit = (1, "V") if magnitude >= 1 else ((1e3, "mV") if magnitude >= 1e-3 else (1e6, "µV"))
        return f"{value*scale:+.6g} {unit}"
    def _read_ppms_current_display(self):
        source = self.ppms_instrument_controllers["6221"]
        self.ppms_6221_zero_output = False
        try:
            with source.lock:
                if source.instrument is None:
                    return "Not connected"
                state = self._ppms_query_numbers("6221", "OUTP?", 1)[0]
                if state == 0:
                    self.ppms_6221_zero_output = True
                    self.ppms_idle_autorange_enabled = False
                    return 'OFF / 0 mA; compliance hit in the last scan' if getattr(self, 'ppms_last_current_trip', False) else 'OFF / 0 mA'
                if state != 1:
                    raise ValueError(f"OUTP?={state:g}")
                peak = self._ppms_query_numbers("6221", "SOUR:WAVE:AMPL?", 1)[0]
                self.ppms_6221_zero_output = peak == 0.0
                if self.ppms_6221_zero_output:
                    self.ppms_idle_autorange_enabled = False
                frequency = self._ppms_query_numbers("6221", "SOUR:WAVE:FREQ?", 1)[0]
                condition = int(self._ppms_query_numbers("6221", "STAT:MEAS:COND?", 1)[0])
                return ('⚠ Compliance hit! Actual current may be below the setpoint; ' if condition & 8 else '') + f'ON / sine setpoint {peak * 1000 / math.sqrt(2):.6g} mA RMS / {frequency:g} Hz'
        except Exception as error:
            return f"Unknown / {short_status_error(error, 45)}"
    def _read_ppms_live(self):
        started = time.monotonic()
        data = self.ppms_environment_controller.read_environment()
        data["read_at"] = started
        data["current_display"] = self._read_ppms_current_display()
        data.update(self._read_ppms_cryogen_display())
        try:
            if self.ppms_environment_controller.backend == PPMS_QDINSTRUMENT_BACKEND:
                motor = PPMSMotorDebug(self.ppms_environment_controller.active).read()
                data["motor_display"] = f"{motor['angle_deg']:.4f}° / {motor['status']}"
                data["angle_deg"] = motor["angle_deg"]
        except Exception as error:
            data["motor_display"] = f"Read failed: {short_status_error(error, 50)}"
        data["lockin_reference"] = {}
        for key in self._ppms_sr830_keys():
            try:
                if self.ppms_instrument_controllers[key].connected:
                    frequency = self._ppms_query_numbers(key, "FREQ?", 1)[0]
                    status = self._ppms_read_sr830_status(key)
                    data["lockin_reference"][key] = f"{frequency:.5g} Hz / " + ("WARNING unlocked" if status & 8 or frequency <= 0 else "LOCK reference locked")
                    if status & 7:
                        data["lockin_reference"][key] = f"OVERLOAD / LIAS={status}"
                    if ((status & 7 or (getattr(self, 'ppms_idle_autorange_enabled', False)
                                        and not getattr(self, 'ppms_6221_zero_output', False)))
                            and not getattr(self, 'ppms_field_scan_running', False)
                            and not getattr(self, 'closing', False)):
                        if not hasattr(self, 'ppms_idle_range_ready_at'):
                            self.ppms_idle_range_ready_at = {}
                        with self.ppms_instrument_controllers[key].lock:
                            if time.monotonic() >= self.ppms_idle_range_ready_at.get(key, 0.):
                                magnitude = 0. if status & 7 else self._ppms_query_numbers(key, 'OUTP? 3', 1)[0]
                                settling = self._ppms_autorange_sr830(key, magnitude, status)
                                self.ppms_idle_range_ready_at[key] = max(self.ppms_idle_range_ready_at.get(key, 0.0), time.monotonic() + settling)
                                if settling:
                                    data['lockin_reference'][key] += '; autorange, waiting to settle'
                else:
                    data["lockin_reference"][key] = "Not connected"
            except Exception as error:
                data["lockin_reference"][key] = f"Status unknown: {short_status_error(error, 50)}"
        return data
    def _update_ppms_helium_change(self, result):
        variable = self.__dict__.get('ppms_helium_change_var')
        if variable is None:
            return
        value = result.get('helium_percent')
        if not isinstance(value, (int, float)) or not math.isfinite(value) or (not 0 <= value <= 100) or (result.get('helium_fresh') is not True):
            variable.set('Waiting for update (no valid new reading)')
            return
        now = time.monotonic()
        history = self.__dict__.setdefault('ppms_helium_history', [])
        if history and now - history[-1][0] < 10.:
            return
        history.append((now, float(value)))
        self._append_ppms_helium_csv(now, float(value))
        start, baseline = history[0]
        duration = now - start
        variable.set(f'{float(value)-baseline:+.2f} points ({duration/60:.1f} min recorded)')
    def _append_ppms_helium_csv(self, recorded_at, value):
        try:
            path = self.__dict__.get('ppms_helium_csv_path')
            panel = 'PPMS'
            if path is None:
                frozen = getattr(sys, 'frozen', False) or '__compiled__' in globals()
                directory = Path(sys.argv[0] if frozen else _PROGRAM_FILE).resolve().parent
                path = directory / PPMS_HELIUM_HISTORY_FILENAME
                if not path.exists() or path.stat().st_size == 0:
                    with path.open('w', newline='', encoding='utf-8-sig') as handle:
                        csv.writer(handle).writerow(('timestamp_local', 'elapsed_s', 'panel', 'helium_percent', 'fresh'))
                self.ppms_helium_csv_path = path
                self.ppms_helium_csv_started = recorded_at
                self.thread_log(f'{panel} He history appended to: {path}')
            elapsed = recorded_at - self.__dict__.get('ppms_helium_csv_started', recorded_at)
            with path.open('a', newline='', encoding='utf-8-sig') as handle:
                csv.writer(handle).writerow((time.strftime('%Y-%m-%d %H:%M:%S'), f'{elapsed:.3f}', panel, f'{value:.6g}', 1))
                handle.flush()
                os.fsync(handle.fileno())
            self.ppms_helium_csv_error = False
        except OSError as error:
            if not self.__dict__.get('ppms_helium_csv_error', False):
                self.ppms_helium_csv_error = True
                self.thread_log(f'He history CSV save failed: {error}')
                messagebox.showwarning('He history save failed', f'Cannot write to the program folder: {error}\nMeasurement continues; the in-memory He history is kept.', parent=self.root)
    def _draw_ppms_helium_history(self):
        canvas = self.__dict__.get('ppms_helium_canvas')
        if canvas is None:
            return
        canvas.delete('all')
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 40:
            return
        now = time.monotonic()
        rows = list(self.__dict__.get('ppms_helium_history', []))
        start = rows[0][0] if rows else now
        elapsed_minutes = max(1., (rows[-1][0]-start)/60) if rows else 1.
        _, x_max, x_ticks, _ = nice_linear_ticks(0., elapsed_minutes, target_intervals=4)
        left, right, top, bottom = 48, width-12, 5, height-27
        low, high, ticks, step = nice_linear_ticks(min(v for t,v in rows), max(v for t,v in rows), target_intervals=2) if rows else (0.,100.,[0,50,100],50.)
        low, high = max(0., low), min(100., high)
        if high <= low:
            low, high = max(0., low-1), min(100., high+1)
        canvas.create_rectangle(left,top,right,bottom,outline='#555555')
        canvas.create_text(12,(top+bottom)/2,text='He (%)',angle=90)
        canvas.create_text((left+right)/2,height-8,text='Recording time (min)',font=('Arial',8))
        for minutes in x_ticks:
            if not 0 <= minutes <= x_max:
                continue
            x=left+minutes/x_max*(right-left)
            canvas.create_line(x,top,x,bottom,fill=THEME_COLORS['plot_grid'])
            canvas.create_text(x,bottom+11,text=f'{minutes:g}',font=('Arial',8))
        for tick in ticks:
            if low <= tick <= high:
                y=bottom-(tick-low)/(high-low)*(bottom-top)
                canvas.create_line(left,y,right,y,fill=THEME_COLORS['plot_grid'])
                canvas.create_text(left-5,y,text=f'{tick:g}',anchor='e',font=('Arial',8))
        previous=None
        for t,v in rows:
            x=left+(t-start)/60/x_max*(right-left)
            y=bottom-(v-low)/(high-low)*(bottom-top)
            if previous is not None and t-previous[0] <= 300:
                canvas.create_line(previous[1],previous[2],x,y,fill='#0072B2',width=1.5)
            else:
                canvas.create_oval(x-1.5,y-1.5,x+1.5,y+1.5,fill='#0072B2',outline='')
            previous=(t,x,y)
        if not rows:
            canvas.create_text((left+right)/2,(top+bottom)/2,text='Waiting for He level data',font=('Arial',9))
    def _check_ppms_helium_warning(self, result) -> None:
        value = result.get("helium_percent")
        if value is None:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value) or not 0 <= value <= 100:
            return
        fresh = result.get("helium_fresh") is True
        if value >= 70.0:
            if fresh:
                self.ppms_helium_warning_active = False
            return
        if hasattr(self, "ppms_helium_live_var"):
            self.ppms_helium_live_var.set(f'{value:.2f}%' + (' (stale)' if not fresh else '') + ' ⚠ below 70%')
        if self.__dict__.get("ppms_helium_warning_active", False) or getattr(self, "closing", False):
            return
        self.ppms_helium_warning_active = True
        panel = "PPMS"
        stale_note = "\nNote: this is a stale reading; verify the actual helium level." if not fresh else ""
        messagebox.showwarning(
            f"{panel} helium level warning",
            f"{panel} He level is {value:.2f}%, below 70%. Please check the liquid helium level."
            f"{stale_note}\nThis warning does not stop the scan or change the field or current settings.",
            parent=self.root,
        )
    def _read_ppms_cryogen_display(self):
        result = {"pressure_display": "Unknown / not supported by interface", "helium_display": "Unknown / not supported by interface"}
        if self.ppms_environment_controller.backend != PPMS_QDINSTRUMENT_BACKEND:
            return result
        reader = PPMSMotorDebug(self.ppms_environment_controller.active)
        try:
            pressure = reader._read_item(19)
            if pressure < 0:
                raise ValueError("Negative pressure")
            result["pressure_display"] = f"{pressure:.5g} Torr"
        except Exception as error:
            result["pressure_display"] = f"Unknown / {short_status_error(error, 35)}"
        try:
            import System
            with reader.controller.lock:
                method = reader._method("SendPPMSCommand", 5)
                args = System.Array[System.Object](5)
                args[0], args[1], args[2] = "LEVEL?", "", ""
                args[3], args[4] = System.Int32(64), System.Double(2.0)
                code = method.Invoke(reader.controller.instrument, args)
                reader._check_return(method, code)
                if str(args[2] or "").strip():
                    raise RuntimeError(str(args[2]))
                values = [float(v.strip()) for v in str(args[1]).strip().split(",")]
                if len(values) != 2 or not all(math.isfinite(v) for v in values) or not 0 <= values[0] <= 100 or values[1] not in (0, 1):
                    raise ValueError("LEVEL? reply format or range invalid")
                result["helium_display"] = f"{values[0]:.2f}%" + (" (stale)" if values[1] == 0 else "")
                result["helium_percent"] = values[0]
                result["helium_fresh"] = values[1] == 1
        except Exception as error:
            result["helium_display"] = f"Unknown / {short_status_error(error, 35)}"
        return result
    def connect_ppms_environment(self) -> None:
        if self.ppms_environment_busy:
            return
        try:
            backend = self.ppms_backend_var.get()
            host = self.ppms_host_var.get().strip()
            port = int(self.ppms_port_var.get())
            PPMSMultiVuReadbackController.validate_endpoint(host, port)
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("PPMS connection", str(error), parent=self.root)
            return
        self.ppms_environment_busy = True
        dll_path = self.ppms_qdinstrument_dll_var.get().strip()
        self.ppms_connection_var.set(f"Connecting to {host}:{port} via {backend}…")
        self._set_ppms_environment_ui(False)
        future = self.ppms_executor.submit(self.ppms_environment_controller.connect, backend, host, port, dll_path)
        def finished() -> None:
            self.ppms_environment_busy = False
            try:
                result = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(f'Connection failed: {short_status_error(error, 120)}')
                self._set_ppms_environment_ui(False)
                if not self.closing:
                    messagebox.showerror("PPMS connection failed", str(error), parent=self.root)
                return
            self.ppms_connection_var.set("Connected")
            self._apply_ppms_environment_readback(result)
            self._set_ppms_environment_ui(True)
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def disconnect_ppms_environment(self) -> None:
        if self.ppms_environment_busy or self.ppms_field_scan_running:
            if self.ppms_field_scan_running and not self.closing:
                messagebox.showwarning('PPMS disconnect', 'Click Stop/Zero first and wait for the field-scan thread to finish before disconnecting', parent=self.root)
            return
        self.ppms_environment_busy = True
        self.ppms_connection_var.set("Disconnecting PPMS client…")
        self._set_ppms_environment_ui(True)
        future = self.ppms_executor.submit(self.ppms_environment_controller.disconnect)
        def finished() -> None:
            self.ppms_environment_busy = False
            try:
                future.result()
            except BaseException as error:
                if not self.closing:
                    messagebox.showerror("PPMS disconnect failed", str(error), parent=self.root)
            self.ppms_temperature_var.set("-- K")
            self.ppms_temperature_status_var.set("Status: --")
            self.ppms_field_var.set("-- Oe")
            self.ppms_field_status_var.set("Status: --")
            self.ppms_last_readback = None
            self.ppms_persistent_watch = None
            self.ppms_control_safety_var.set(False)
            self._set_ppms_environment_ui(False)
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def read_ppms_environment_once(self) -> None:
        if not self.ppms_environment_controller.connected or self.ppms_poll_inflight:
            return
        self.ppms_poll_inflight = True
        future = self.ppms_executor.submit(self._read_ppms_live)
        def finished() -> None:
            self.ppms_poll_inflight = False
            try:
                result = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(f'Read failed: {short_status_error(error, 120)}')
                return
            self._apply_ppms_environment_readback(result)
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def _poll_ppms_helium_history(self):
        if not self.ppms_environment_controller.connected or getattr(self, 'ppms_helium_poll_inflight', False) or time.monotonic() < getattr(self, 'ppms_helium_poll_due', 0.0):
            return
        self.ppms_helium_poll_inflight = True
        try:
            future = self.ppms_executor.submit(self._read_ppms_cryogen_display)
        except RuntimeError:
            self.ppms_helium_poll_inflight = False
            return
        def finished():
            self.ppms_helium_poll_inflight = False
            self.ppms_helium_poll_due = time.monotonic() + 10.
            if self.closing or self.ppms_closed:
                return
            try:
                self._update_ppms_helium_change(future.result())
                self._draw_ppms_helium_history()
            except Exception as error:
                self.thread_log(f'He trend read failed: {error}')
        future.add_done_callback(lambda _: self.post_ui(finished))
    def _ppms_poll_timer(self) -> None:
        if self.closing or self.ppms_closed:
            return
        self._poll_ppms_helium_history()
        if self.ppms_environment_controller.connected and (not self.ppms_environment_busy) and (not self.ppms_poll_inflight) and (not self.ppms_field_scan_running):
            self.read_ppms_environment_once()
        try:
            self.ppms_poll_after_id = self.root.after(1000, self._ppms_poll_timer)
        except tk.TclError:
            self.ppms_poll_after_id = None
    @staticmethod
    def _ppms_frequency_series_group(row) -> str:
        frequency = row.get("6221_frequency_hz")
        return f"{frequency:g} Hz" if isinstance(frequency, (int, float)) else "? Hz"
    @staticmethod
    def _ppms_frequency_plot_colors(base, frequencies):
        valid = {key: float(value) for key, value in frequencies.items() if isinstance(value, (int, float)) and math.isfinite(value)}
        ordered = sorted(set(valid.values()))
        rank = {value: index for index, value in enumerate(ordered)}
        span = len(ordered) - 1
        return {key: shade_color(base, rank[value] / span if span else 0.5) for key, value in valid.items()}
    @staticmethod
    def _ppms_field_series_group(row) -> str:
        field = row.get("target_field_oe")
        shown = f"{field:g} Oe" if isinstance(field, (int, float)) else "? Oe"
        return f"H{row.get('outer_field_index', 1)} ({shown})"
    def _ppms_temperature_plot_colors(self, base, temperatures):
        if getattr(self, 'ppms_active_scan_mode', '') in ('Frequency+Current', 'Angle+Field'):
            return self._ppms_frequency_plot_colors(base, temperatures)
        if getattr(self, 'ppms_active_scan_mode', '') not in ('Field+Temperature', 'Angle+Temperature', 'Current+Temperature'):
            return {}
        valid = {k: float(v) for k, v in temperatures.items() if isinstance(v, (int, float)) and math.isfinite(v)}
        if not valid:
            return {}
        limits = list(valid.values())
        try:
            limits.extend(float(v.get()) for v in self.ppms_temperature_scan_range[:2])
        except (AttributeError, TypeError, ValueError):
            pass
        limits = [v for v in limits if math.isfinite(v)]
        low, high = min(limits), max(limits)
        rgb = [int(base[i:i+2], 16) for i in (1, 3, 5)]
        colors = {}
        for key, temperature in valid.items():
            fraction = (temperature - low) / (high - low) if high > low else .5
            light = .55 * (1 - fraction)
            dark = .25 * fraction
            values = [round((c + (255-c)*light)*(1-dark)) for c in rgb]
            colors[key] = '#' + ''.join(f'{v:02x}' for v in values)
        return colors
    def _request_plot_redraw(self) -> None:
        if self.__dict__.get('ppms_plot_redraw_queued'):
            return
        self.ppms_plot_redraw_queued = True
        def run() -> None:
            self.ppms_plot_redraw_queued = False
            if any(self.__dict__.get(name) for name in ('closing', 'ppms_closed', 'rte_closed')):
                return
            self._redraw_ppms_transport_plot()
        try:
            self.root.after_idle(run)
        except (tk.TclError, AttributeError, RuntimeError):
            self.ppms_plot_redraw_queued = False
            self._redraw_ppms_transport_plot()
    def _redraw_ppms_transport_plot(self) -> None:
        canvas = getattr(self, "ppms_transport_canvas", None)
        if canvas is None:
            return
        rows = getattr(self, "ppms_field_scan_rows", ())
        address_keys = tuple((address, f"sr830_{address}_r_v") for address in ("8", "9", "10"))
        addresses = set()
        for row in rows:
            addresses.update(address for address, name in address_keys if name in row)
            if len(addresses) == len(address_keys):
                break
        if not addresses:
            for key in self._ppms_sr830_keys():
                controller = self.ppms_instrument_controllers[key]
                if controller.connected:
                    try:
                        addresses.add(str(self._ppms_sr830_address(controller.resource_name)))
                    except ValueError:
                        pass
        axis = getattr(self, "ppms_plot_axis", "field")
        x_key, x_label = {"field": ("measured_field_oe", "Measured magnetic field (Oe)"),
                          "temperature": ("temperature_k", "Measured temperature (K)"),
                          "angle": ("measured_angle_deg", "Measured angle (°)"),
                          "current": ("6221_current_rms_ma", "Applied AC current (mA RMS)"),
                          "frequency": ("6221_frequency_hz", "Frequency (Hz)")}[axis]
        x_scale = 1.0
        x_extent = None
        if axis == "field":
            max_field = max((abs(row[x_key]) for row in rows if isinstance(row.get(x_key), (int, float)) and math.isfinite(row[x_key])), default=0.0)
            if max_field >= 1000:
                x_scale = 1e-4
                x_label = "Measured magnetic field (T)"
            x_extent = max_field * x_scale
        series = {}
        series_temperatures = {}
        channels = ("x", "y", "theta", "r") if hasattr(self, "ppms_transport_canvases") else ("x", "y")
        plot_addresses = sorted(addresses, key=int)
        columns = tuple(((f'sr830_{address}_' + ('theta_deg' if channel == 'theta' else f'{channel}_v'), f'GPIB{address} {channel.upper()} | ') for address in plot_addresses for channel in channels))
        scan_mode = getattr(self, "ppms_active_scan_mode", "")
        for row in rows:
            x_value = row.get(x_key)
            if not isinstance(x_value, (int, float)):
                continue
            group = f"A{row.get('angle_index', 1)} T{row.get('temperature_index', 1)}" if axis == 'field' else f"A{row.get('angle_index', 1)}" if axis == 'temperature' else ''
            if (axis, scan_mode) in (("angle", "Angle+Temperature"), ("current", "Current+Temperature")):
                group = f"T{row.get('temperature_index', 1)} ({row.get('target_temperature_k', '')} K)"
            if axis == "field" and scan_mode == "Field+Temperature":
                group += f" ({row.get('target_temperature_k', '')} K)"
            temperature = row.get("target_temperature_k")
            if scan_mode == "Frequency+Current":
                group = self._ppms_frequency_series_group(row)
                temperature = row.get("6221_frequency_hz")
            elif scan_mode == "Angle+Field":
                group = self._ppms_field_series_group(row)
                temperature = row.get("target_field_oe")
            tail = f"{group} L{row.get('loop', 1)}"
            point_x = x_value * x_scale
            for column, prefix in columns:
                value = row.get(column)
                if value is None:
                    continue
                name = prefix + tail
                series_temperatures[name] = temperature
                series.setdefault(name, []).append((point_x, value))
        if hasattr(self, "ppms_transport_canvases"):
            for (key, channel), target_canvas in self.ppms_transport_canvases.items():
                address = getattr(self, "ppms_plot_addresses", {}).get(key)
                if address is None:
                    try:
                        address = self._ppms_sr830_address(self.ppms_instrument_controllers[key].resource_name)
                    except (ValueError, AttributeError):
                        address = None
                frame = self.__dict__.get('ppms_transport_plot_frames', {}).get((key, channel))
                harmonic_key = f'sr830_{address}_harmonic'
                orders = {str(row[harmonic_key]) for row in rows if harmonic_key in row}
                if not orders and not rows:
                    variable = self._ppms_harmonic_var(key)
                    if variable is not None:
                        orders = {str(variable.get())}
                if frame is not None:
                    labels = {'1': '1st harmonic', '2': '2nd harmonic', '3': '3rd harmonic'}
                    order_label = '/'.join(labels.get(v, 'harmonic ' + v) for v in sorted(orders)) if orders else 'harmonic unknown'
                    variable = self._ppms_harmonic_var(key)
                    if variable is not None:
                        chosen = str(variable.get())
                        previous = order_label
                        order_label = labels.get(chosen, 'harmonic ' + chosen)
                        if rows and orders != {chosen}:
                            order_label += f' (existing data: {previous})'
                    frame.configure(text=f'{PPMS_SR830_LABELS.get(key, key)} {order_label} {"θ" if channel == "theta" else channel.upper()}')
                selected = {name: points for name, points in series.items() if name.startswith(f"GPIB{address} {channel.upper()} |")}
                base_color = PPMS_HARMONIC_COLORS.get(next(iter(orders)) if len(orders) == 1 else '', '#666666')
                temperature_colors = self._ppms_temperature_plot_colors(base_color, series_temperatures)
                self._draw_nonlocal_series(target_canvas, selected, x_label,
                    "Phase θ (°)" if channel == "theta" else f"{channel.upper()} voltage",
                    "Waiting for scan data", markers=True, auto_voltage_unit=channel != "theta",
                    symmetric_x_extent=x_extent,
                    angle_x_range=self.__dict__.get("ppms_plot_angle_range") if axis == "angle" else None,
                    show_legend=False, color_override=None if temperature_colors else base_color,
                    series_colors=temperature_colors, series_labels={name: name.split(" | ")[-1] for name in selected})
            return
        self._draw_nonlocal_series(
            canvas,
            series,
            x_label,
            "Lock-in X / Y voltage",
            "Connect the PPMS, the 6221 and at least one SR830 at address 8/9/10",
            markers=True,
            auto_voltage_unit=True,
            angle_x_range=self.__dict__.get("ppms_plot_angle_range") if axis == "angle" else None,
        )
    @staticmethod
    def _ppms_sr830_address(resource_name: str) -> int:
        match = re.fullmatch('GPIB\\d*::(8|9|10)::INSTR', str(resource_name).strip(), flags=re.IGNORECASE)
        if match is None:
            raise ValueError("PPMS SR830 address must be GPIB 8, 9 or 10")
        return int(match.group(1))
    def _ppms_query_numbers(self, key: str, command: str, expected_count: int) -> tuple[float, ...]:
        controller = self.ppms_instrument_controllers[key]
        last_response = ""
        last_error: BaseException | None = None
        once = command.strip().upper() in NonLocalACController.CLEAR_ON_READ_QUERIES
        with controller.lock:
            instrument = controller.instrument
            if instrument is None:
                raise RuntimeError(f"PPMS {controller.label} is not connected")
            for attempt in range(1, 2 if once else 4):
                try:
                    last_response = instrument.query(command).strip()
                    values = tuple((float(part.strip()) for part in last_response.split(',')))
                    if len(values) != expected_count:
                        raise ValueError(f'expected {expected_count} values, received {len(values)}')
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("Reply contains a non-finite value")
                    return values
                except Exception as error:
                    last_error = error
                    if attempt >= 3:
                        break
                    try:
                        instrument.clear()
                    except Exception:
                        pass
                    time.sleep(0.08)
                    if once:
                        break
        raise RuntimeError(
            f"PPMS {controller.label} query {command!r} "
            + ("returned an abnormal reply (not asked again: it clears as it is read): " if once
               else "kept returning abnormal replies: ")
            + f"{last_response!r}"
        ) from last_error
    def _ppms_query_lias(self, key: str) -> int:
        try:
            value = self._ppms_query_numbers(key, "LIAS?", 1)[0]
            if not (float(value).is_integer() and 0 <= value <= 255):
                raise ValueError(f"not a status byte: {value!r}")
            return int(value)
        except Exception as error:
            if not self.ppms_instrument_controllers[key].connected:
                raise
            self.thread_log(f"{getattr(type(self), 'ppms_page_name', 'PPMS')} "
                            f"{self.ppms_instrument_controllers[key].label} LIAS? reply unreadable ({error}); "
                            "not asked again (it clears as it is read): the reading counts as overloaded")
            return NonLocalACController.SR830_LIAS_UNREADABLE
    def _ppms_write_6221_checked(self, command: str) -> None:
        controller = self.ppms_instrument_controllers["6221"]
        with controller.lock:
            if self.ppms_field_scan_stop_event.is_set():
                raise PPMSScanCancelled("6221 setup cancelled")
            if controller.instrument is None:
                raise RuntimeError("PPMS Keithley 6221 is not connected")
            controller.instrument.write(command)
            time.sleep(0.03)
            response = controller.instrument.query("SYST:ERR?").strip()
            try:
                code = int(response.split(",", 1)[0].strip())
            except ValueError as error:
                raise RuntimeError(f"6221 error status could not be parsed: {response}") from error
            if code != 0:
                raise RuntimeError(f"6221 reported an error after {command!r}: {response}")
    def _ppms_apply_selected_harmonic(self, key):
        if self.ppms_environment_busy or self.ppms_field_scan_running:
            return
        self._redraw_ppms_transport_plot()
        controller = self.ppms_instrument_controllers[key]
        if not controller.connected:
            return
        harmonic = self._ppms_harmonic_value(key, 0)
        if harmonic not in (1, 2, 3):
            return
        self.ppms_environment_busy = True
        self._update_ppms_control_ui()
        def work():
            with controller.lock:
                self._ppms_write_sr830_checked(key, f'HARM {harmonic}')
                actual = int(self._ppms_query_numbers(key, 'HARM?', 1)[0])
                if actual != harmonic:
                    raise RuntimeError(f'Harmonic readback mismatch: requested {harmonic}, actual {actual}')
                return actual
        def finished(future):
            self.ppms_environment_busy = False
            try:
                actual = future.result()
                self.thread_log(f'{key} harmonic updated and confirmed by readback: {actual}')
            except Exception as error:
                self.thread_log(f'{key} harmonic update failed: {error}')
                if not self.closing:
                    messagebox.showerror('SR830 harmonic update failed', str(error), parent=self.root)
            finally:
                self._update_ppms_control_ui()
        try:
            future = self.ppms_executor.submit(work)
        except Exception as error:
            self.ppms_environment_busy = False
            self._update_ppms_control_ui()
            messagebox.showerror('SR830 harmonic update failed', str(error), parent=self.root)
            return
        future.add_done_callback(lambda f: self.post_ui(lambda: finished(f)))
    def _build_ppms_sr830_tc_choice(self, master) -> None:
        self.ppms_sr830_time_constant_var = tk.StringVar(value=PPMS_SR830_DEFAULT_TIME_CONSTANT)
        self.ppms_sr830_time_constant_applied = PPMS_SR830_DEFAULT_TIME_CONSTANT
        ttk.Label(master, text="SR830 TC").grid(row=0, column=1, padx=(12, 3))
        combo = ttk.Combobox(master, textvariable=self.ppms_sr830_time_constant_var, values=tuple(STFMR_LOCKIN_SR830_TIME_CONSTANTS), state='readonly', width=6)
        combo.grid(row=0, column=2)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._ppms_apply_sr830_time_constant())
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            combo.bind(sequence, lambda _event: "break")
        self.ppms_sr830_time_constant_combo = combo
    def _ppms_sr830_time_constant_code(self) -> int:
        variable = self.__dict__.get("ppms_sr830_time_constant_var")
        if variable is None:
            return 10
        label = str(variable.get()).strip()
        if label not in STFMR_LOCKIN_SR830_TIME_CONSTANTS:
            choices = ", ".join(STFMR_LOCKIN_SR830_TIME_CONSTANTS)
            raise ValueError(f"SR830 TC must be one of: {choices}")
        return STFMR_LOCKIN_SR830_TIME_CONSTANTS[label]
    @staticmethod
    def _ppms_acquisition_default(mode) -> str:
        seconds = PPMS_TEMPERATURE_ACQUISITION_S if mode == "Temperature" else PPMS_ACQUISITION_S
        return f"{seconds:g}"
    def _apply_ppms_mode_acquisition_default(self, mode) -> None:
        shown = self.__dict__.get("ppms_acquisition_default_shown", f"{PPMS_ACQUISITION_S:g}")
        new = self._ppms_acquisition_default(mode)
        if str(self.ppms_acquisition_time_var.get()).strip() == shown:
            self.ppms_acquisition_time_var.set(new)
            self.ppms_acquisition_default_shown = new
        else:
            self.ppms_acquisition_default_shown = None
    def _ppms_lockin_settle(self, stable_s: float, active_lockins, zurich: bool, fallback_code=None):
        codes = self.__dict__.get("ppms_sr830_time_constant_codes") or {}
        meters = []
        for key in active_lockins:
            code = codes.get(key)
            if code is None and fallback_code is not None:
                code = fallback_code
            if code is None:
                try:
                    code = self._ppms_sr830_time_constant_code()
                except ValueError:
                    code = 10
            meters.append((SR830_TIME_CONSTANTS_S[int(code)], PPMS_SR830_LABELS.get(key, key)))
        if zurich:
            try:
                tau = float(self.__dict__.get("zurich_scan_tau", PPMS_ZURICH_TIME_CONSTANT_S))
            except (TypeError, ValueError):
                tau = PPMS_ZURICH_TIME_CONSTANT_S
            if not math.isfinite(tau) or tau <= 0:
                tau = PPMS_ZURICH_TIME_CONSTANT_S
            meters.append((tau, "the Zurich"))
        slowest, meter = max(meters, key=lambda item: item[0], default=(0.0, ""))
        return ppms_lockin_settle_s(stable_s, [tau for tau, _name in meters]), slowest, meter
    def _ppms_point_settle(self, stable_s: float, active_lockins):
        zurich = bool(self.__dict__.get("zurich_scan_enabled", False))
        settle, slowest, meter = self._ppms_lockin_settle(stable_s, active_lockins, zurich, fallback_code=self.__dict__.get('ppms_scan_sr830_tc_code', 10))
        if self.__dict__.get("ppms_scan_settle_logged") != settle:
            self.ppms_scan_settle_logged = settle
            self.thread_log(f"{getattr(type(self), 'ppms_page_name', 'PPMS')} scan: each reading now waits " + ppms_lockin_settle_text(stable_s, settle, slowest, meter))
            status = f"Each reading now waits {ppms_settle_status_text(settle)} s" + (
                f": {LOCKIN_SETTLE_TAUS:g} x {ppms_settle_status_text(slowest)} s TC of {meter}"
                if slowest and settle > stable_s else " (Settle s)")
            self.post_ui(lambda: None if self.closing or self.__dict__.get('rte_closed', False) else self.ppms_field_scan_status_var.set(status))
        return settle, slowest
    def _ppms_settle_per_pass(self, stable_s: float, passes, zurich: bool):
        return [self._ppms_lockin_settle(stable_s, settings['active_lockins'], zurich) for settings in passes]
    @staticmethod
    def _ppms_frequency_set_up_s(frequencies, pass_settles, acquisition_s) -> float:
        total = 0.0
        for index, (settle_s, _tc, _meter) in enumerate(pass_settles):
            for frequency in frequencies:
                total += float(acquisition_s) + max(0.0, NONLOCAL_FSCAN_SETTLE_PERIODS / float(frequency) - float(settle_s))
                if index:
                    total += PPMS_SET_UP_ALLOWANCE_S
        return total
    def _ppms_sr830_time_constant_idle(self) -> bool:
        return not (self.ppms_environment_busy or self.ppms_field_scan_running or self.__dict__.get('ppms_sr830_connecting'))
    def _update_ppms_sr830_tc_state(self) -> None:
        combo = self.__dict__.get("ppms_sr830_time_constant_combo")
        if combo is not None:
            combo.configure(state="readonly" if self._ppms_sr830_time_constant_idle() else "disabled")
    def _ppms_apply_sr830_time_constant(self) -> None:
        variable = self.__dict__.get("ppms_sr830_time_constant_var")
        if variable is None:
            return
        previous = self.__dict__.get("ppms_sr830_time_constant_applied", PPMS_SR830_DEFAULT_TIME_CONSTANT)
        label = str(variable.get()).strip()
        if label not in STFMR_LOCKIN_SR830_TIME_CONSTANTS or not self._ppms_sr830_time_constant_idle():
            variable.set(previous)
            self._update_ppms_sr830_tc_state()
            return
        code = STFMR_LOCKIN_SR830_TIME_CONSTANTS[label]
        controllers = self.ppms_instrument_controllers
        if not any(controllers[key].connected for key in self._ppms_sr830_keys()):
            self.ppms_sr830_time_constant_applied = label
            return
        self.ppms_environment_busy = True
        self._update_ppms_control_ui()
        def work():
            done, failures = [], []
            for key in self._ppms_sr830_keys():
                controller = controllers[key]
                try:
                    with controller.lock:
                        if not controller.connected:
                            continue
                        self._ppms_set_sr830_time_constant(key, code)
                    done.append(PPMS_SR830_LABELS[key])
                except Exception as error:
                    failures.append(f"{PPMS_SR830_LABELS[key]}: {error}")
            if failures:
                raise RuntimeError(" | ".join(failures))
            return done
        def finished(future):
            self.ppms_environment_busy = False
            self.ppms_sr830_time_constant_applied = label
            try:
                done = future.result()
                self.thread_log(f'PPMS SR830 TC {label} (OFLT {code}) set and confirmed by readback: ' + (', '.join(done) or 'no SR830 connected any more'))
            except Exception as error:
                self.thread_log(f"PPMS SR830 TC {label} update failed: {error}")
                if not self.closing:
                    messagebox.showerror('SR830 TC update failed', f'{error}\n\nChoose SR830 TC again to retry, or reconnect that SR830.', parent=self.root)
            finally:
                self._update_ppms_control_ui()
        try:
            future = self.ppms_executor.submit(work)
        except Exception as error:
            self.ppms_environment_busy = False
            variable.set(previous)
            self._update_ppms_control_ui()
            messagebox.showerror("SR830 TC update failed", str(error), parent=self.root)
            return
        future.add_done_callback(lambda f: self.post_ui(lambda: finished(f)))
    def _ppms_set_sr830_time_constant(self, key: str, code: int) -> None:
        controller = self.ppms_instrument_controllers[key]
        verified = self.__dict__.setdefault("ppms_sr830_time_constant_codes", {})
        with controller.lock:
            verified.pop(key, None)
            self._ppms_write_sr830_checked(key, f"OFLT {code}")
            actual = int(round(self._ppms_query_numbers(key, "OFLT?", 1)[0]))
            if actual != code:
                raise RuntimeError(f'time constant readback mismatch: requested OFLT {code}, read {actual}')
            verified[key] = actual
            state = getattr(self, "ppms_sr830_range_states", {}).get(key)
            if state is not None:
                state.low_range_count = state.range_cooldown = 0
                state.low_range_peak = 0.0
                state.overload_floor = None
            if not hasattr(self, "ppms_idle_range_ready_at"):
                self.ppms_idle_range_ready_at = {}
            self.ppms_idle_range_ready_at[key] = time.monotonic() + max(0.1, 5.0 * SR830_TIME_CONSTANTS_S[actual])
    def _ppms_read_sr830_time_constant_for_scan(self, key: str, address: int) -> None:
        verified = self.__dict__.setdefault("ppms_sr830_time_constant_codes", {})
        previous = verified.pop(key, None)
        code = int(round(self._ppms_query_numbers(key, "OFLT?", 1)[0]))
        if not 0 <= code < len(SR830_TIME_CONSTANTS_S):
            raise RuntimeError(f"SR830 #{address} time-constant readback invalid: OFLT={code}")
        verified[key] = code
        if previous is not None and code != previous:
            self.thread_log(
                f"{getattr(type(self), 'ppms_page_name', 'PPMS')} "
                f"{self.ppms_instrument_controllers[key].label}: the time constant reads "
                f"{SR830_TIME_CONSTANTS_S[code]:g} s (OFLT {code}), not the "
                f"{SR830_TIME_CONSTANTS_S[previous]:g} s last set (changed on the front panel?); "
                "the scan records the value read and counts it in the wait before each reading")
    def _ppms_write_sr830_checked(self, key: str, command: str) -> None:
        controller = self.ppms_instrument_controllers[key]
        with controller.lock:
            if controller.instrument is None:
                raise RuntimeError(f"PPMS {controller.label} is not connected")
            controller.instrument.write(command)
            time.sleep(0.03)
            event_status = int(round(self._ppms_query_numbers(key, "*ESR?", 1)[0]))
            if event_status & 0x30:
                raise RuntimeError(f'{controller.label} rejected {command!r}; *ESR?={event_status}')
    def _update_ppms_transport_current(self, settings):
        if self.ppms_field_scan_stop_event.is_set() or self.closing:
            raise PPMSScanCancelled('Scan stopped; current not updated')
        validated = validate_ppms_transport_scan_settings(
            settings['current_rms_ma'], settings['frequency_hz'], settings['compliance_v'],
            settings['harmonic_a'], settings['harmonic_b'], settings['active_lockins'],
            allow_no_lockins=bool(settings.get('allow_no_lockins', False)))
        peak = validated['peak_a']
        if peak > settings['continuous_sweep_peak_a'] + 1e-12:
            raise ValueError('Current target exceeds reserved sweep range')
        if self._ppms_query_numbers('6221', 'OUTP?', 1)[0] != 1:
            raise RuntimeError('6221 output is off; continuous sweep cannot continue')
        self._ppms_write_6221_checked(f'SOUR:WAVE:AMPL {peak:.12e}')
        actual = self._ppms_query_numbers('6221', 'SOUR:WAVE:AMPL?', 1)[0]
        if not math.isclose(actual, peak, rel_tol=5e-4, abs_tol=1e-12):
            raise RuntimeError('6221 live amplitude readback mismatch')
        if self._ppms_query_numbers('6221', 'OUTP?', 1)[0] != 1:
            raise RuntimeError('6221 output stopped during amplitude update')
    def _configure_ppms_transport_scan(self, settings: dict[str, object]) -> None:
        if self.ppms_field_scan_stop_event.is_set():
            raise PPMSScanCancelled("Scan stopped; current source not reconfigured")
        active_lockins = tuple(settings["active_lockins"])
        required = ("6221", *active_lockins)
        missing = [self.ppms_instrument_controllers[key].label for key in required if not self.ppms_instrument_controllers[key].connected]
        if missing:
            raise RuntimeError("Please connect first: " + ", ".join(missing))
        self.ppms_last_current_trip = False
        self._ppms_clear_sr830_range_floor(active_lockins, zurich=True)
        current_rms_ma = float(settings["current_rms_ma"])
        frequency_hz = float(settings["frequency_hz"])
        compliance_v = float(settings["compliance_v"])
        validated = validate_ppms_transport_scan_settings(
            current_rms_ma, frequency_hz, compliance_v,
            settings["harmonic_a"], settings["harmonic_b"], active_lockins,
            allow_no_lockins=bool(settings.get('allow_no_lockins', False)),
        )
        peak_a = validated["peak_a"]
        sweep_peak = float(settings.get('continuous_sweep_peak_a', peak_a))
        if not math.isfinite(sweep_peak) or not peak_a <= sweep_peak <= K6221_MAX_ABS_CURRENT_A:
            raise ValueError('Invalid continuous current sweep range')
        source = self.ppms_instrument_controllers["6221"]
        source.safe_output_off()
        with source.lock:
            source.instrument.write("*RST")
            source.instrument.write("*CLS")
        self._initialize_ppms_6221()
        if 'continuous_sweep_peak_a' in settings:
            self._ppms_write_6221_checked(f"SOUR:CURR:RANG {sweep_peak:.12e}")
            actual_range = self._ppms_query_numbers('6221', 'SOUR:CURR:RANG?', 1)[0]
            if actual_range * 1.05 + 1e-12 < sweep_peak:
                raise RuntimeError('6221 current range is too small for the sweep')
        for command in (
            f"CURR:COMP {compliance_v:.12g}",
            "SOUR:WAVE:FUNC SIN",
            f"SOUR:WAVE:FREQ {frequency_hz:.12g}",
            f"SOUR:WAVE:AMPL {peak_a:.12e}",
            "SOUR:WAVE:OFFS 0",
            "SOUR:WAVE:RANG FIX" if 'continuous_sweep_peak_a' in settings else "SOUR:WAVE:RANG BEST",
            "SOUR:WAVE:DUR:CYCL INF",
            f"SOUR:WAVE:PMAR {NONLOCAL_MARKER_PHASE_DEG:g}",
            f"SOUR:WAVE:PMAR:OLIN {NONLOCAL_MARKER_LINE}",
            "SOUR:WAVE:PMAR:STAT ON",
            "OUTP OFF",
        ):
            self._ppms_write_6221_checked(command)
        actual_compliance = self._ppms_query_numbers('6221', 'CURR:COMP?', 1)[0]
        actual_frequency = self._ppms_query_numbers('6221', 'SOUR:WAVE:FREQ?', 1)[0]
        actual_peak = self._ppms_query_numbers('6221', 'SOUR:WAVE:AMPL?', 1)[0]
        if not math.isclose(actual_compliance, compliance_v, rel_tol=0.0005, abs_tol=1e-06):
            raise RuntimeError(f'6221 compliance readback mismatch: requested {compliance_v:g} V, read {actual_compliance:g} V')
        frequency_tol_hz = settings.get("frequency_readback_tol_hz")
        if not (math.isclose(actual_frequency, frequency_hz, rel_tol=0.005, abs_tol=0.1) if frequency_tol_hz is None else abs(actual_frequency - frequency_hz) <= float(frequency_tol_hz)):
            raise RuntimeError(f'6221 frequency readback mismatch: requested {frequency_hz:g} Hz, read {actual_frequency:g} Hz')
        if not math.isclose(actual_peak, peak_a, rel_tol=5e-4, abs_tol=1e-12):
            raise RuntimeError(f'6221 amplitude readback mismatch: requested {peak_a:g} Apeak, read {actual_peak:g} Apeak')
        harmonic_keys = PPMS_SR830_HARMONIC_SETTING_KEYS
        for key in active_lockins:
            controller = self.ppms_instrument_controllers[key]
            self._ppms_write_sr830_checked(key, "ILIN 0")
            self._ppms_write_sr830_checked(key, "SYNC 0")
            harmonic = int(settings[harmonic_keys[key]])
            address = self._ppms_sr830_address(controller.resource_name)
            actual_harmonic = int(round(self._ppms_query_numbers(key, 'HARM?', 1)[0]))
            if actual_harmonic != harmonic:
                self._ppms_write_sr830_checked(key, f"HARM {harmonic}")
                actual_harmonic = int(round(self._ppms_query_numbers(key, "HARM?", 1)[0]))
            reference_mode = int(round(self._ppms_query_numbers(key, 'FMOD?', 1)[0]))
            reference_slope = int(round(self._ppms_query_numbers(key, 'RSLP?', 1)[0]))
            if reference_mode != 0 or reference_slope != PPMS_SR830_REFERENCE_SLOPE:
                self._ppms_write_sr830_checked(key, "FMOD 0")
                self._ppms_write_sr830_checked(key, f"RSLP {PPMS_SR830_REFERENCE_SLOPE}")
                reference_mode = int(round(self._ppms_query_numbers(key, "FMOD?", 1)[0]))
                reference_slope = int(round(self._ppms_query_numbers(key, "RSLP?", 1)[0]))
            if actual_harmonic != harmonic or reference_mode != 0 or reference_slope != PPMS_SR830_REFERENCE_SLOPE:
                raise RuntimeError(f'SR830 #{address} configuration readback mismatch: HARM={actual_harmonic}, FMOD={reference_mode}, RSLP={reference_slope}')
            self._ppms_read_sr830_time_constant_for_scan(key, address)
        for command in ('SOUR:WAVE:ABOR', 'SOUR:WAVE:ARM', 'SOUR:WAVE:INIT'):
            self._ppms_write_6221_checked(command)
        output_on = int(round(self._ppms_query_numbers("6221", "OUTP?", 1)[0]))
        if output_on != 1:
            raise RuntimeError("6221 sine output did not turn on")
        reference_timeout_s = float(settings.get("reference_timeout_s", PPMS_TRANSPORT_REFERENCE_TIMEOUT_S))
        deadline = time.monotonic() + reference_timeout_s
        last_reference = {}
        while time.monotonic() < deadline:
            locked = True
            for key in active_lockins:
                frequency = self._ppms_query_numbers(key, "FREQ?", 1)[0]
                status = self._ppms_query_lias(key)
                last_reference[key] = (frequency, status)
                if status & 8 or not math.isclose(frequency, frequency_hz, rel_tol=0.005, abs_tol=0.1):
                    locked = False
            if locked:
                self.post_ui(
                    lambda current=current_rms_ma, frequency=frequency_hz: (
                        self.ppms_transport_status_var.set(
                            f"Transport acquisition: 6221 {current:g} mA RMS / "
                            f"{frequency:g} Hz ON; connected SR830 external references locked"
                        )
                    )
                )
                return
            if self.ppms_field_scan_stop_event.wait(0.2):
                raise PPMSScanCancelled("Stopped as requested while waiting for the lock-in reference")
        detail = '; '.join((f'SR830 #{self._ppms_sr830_address(self.ppms_instrument_controllers[key].resource_name)}: {value[0]:.6g} Hz, LIAS={value[1]}' for key, value in last_reference.items()))
        raise RuntimeError(f'Connected SR830 failed to lock the 6221 external reference within {reference_timeout_s:g} s; {detail}')
    def _read_ppms_transport_sample(self, expected_frequency_hz: float, active_lockins: tuple[str, ...], allow_down: bool=True) -> dict[str, object]:
        output_on = int(round(self._ppms_query_numbers("6221", "OUTP?", 1)[0]))
        measurement_condition = int(round(self._ppms_query_numbers('6221', 'STAT:MEAS:COND?', 1)[0]))
        if output_on != 1:
            raise RuntimeError("6221 output turned off unexpectedly during the scan")
        if measurement_condition & 8:
            self.ppms_last_current_trip = True
            self.post_ui(lambda: self.ppms_current_live_var.set('⚠ Compliance hit! Scan stopped, turning output off'))
            raise RuntimeError('6221 hit compliance; scan stopped and output turned off')
        sample: dict[str, object] = {'6221_output_on': True, '6221_measurement_condition': measurement_condition}
        zero_current = self._ppms_query_numbers("6221", "SOUR:WAVE:AMPL?", 1)[0] == 0.0
        for key in active_lockins:
            address = self._ppms_sr830_address(self.ppms_instrument_controllers[key].resource_name)
            prefix = f"sr830_{address}"
            x, y, r_value, theta, frequency = self._ppms_query_numbers(key, 'SNAP? 1,2,3,4,9', 5)
            lia_status = self._ppms_read_sr830_status(key)
            reference_valid = not lia_status & 8 and math.isclose(frequency, expected_frequency_hz, rel_tol=0.005, abs_tol=0.1)
            sample.update({
                f"{prefix}_x_v": x,
                f"{prefix}_y_v": y,
                f"{prefix}_r_v": r_value,
                f"{prefix}_theta_deg": theta,
                f"{prefix}_frequency_hz": frequency,
                f"{prefix}_lias": lia_status,
                f"{prefix}_reference_valid": reference_valid,
            })
            settling = self._ppms_autorange_sr830(key, r_value, lia_status, enabled=not zero_current, allow_down=allow_down)
            sample["range_settle_s"] = max(sample.get("range_settle_s", 0.), settling)
            sample[f"{prefix}_overload"] = bool(lia_status & 7)
            sample[f"{prefix}_sensitivity_index"] = self.ppms_sr830_range_states[key].sensitivity_index
        return sample
    def _ppms_clear_sr830_range_floor(self, keys=None, zurich=None) -> None:
        for key, state in dict(getattr(self, "ppms_sr830_range_states", None) or {}).items():
            if keys is None or key in keys:
                state.overload_floor = None
        if keys is None if zurich is None else zurich:
            forget = getattr(self.__dict__.get("zurich_controller"), "forget_mfli_range_guard", None)
            if callable(forget):
                forget()
    def _ppms_read_sr830_status(self, key, fresh=False) -> int:
        state = getattr(self, "ppms_sr830_range_states", {}).get(key)
        if state is not None and getattr(state, "clear_latch_pending", False):
            self._ppms_query_lias(key)
            state.clear_latch_pending = False
        if fresh:
            self._ppms_query_lias(key)
        return self._ppms_query_lias(key)
    def _ppms_sr830_settle_s(self, key, time_constants=5.0) -> float:
        code = int(self._ppms_query_numbers(key, "OFLT?", 1)[0])
        if not 0 <= code <= 19:
            raise RuntimeError("SR830 time-constant readback invalid")
        tau = (1 if code % 2 == 0 else 3) * 10 ** (code // 2 - 5)
        return max(.1, time_constants * tau)
    def _ppms_autorange_sr830(self, key, r_value, status, enabled=True, allow_down=True):
        controller = self.ppms_instrument_controllers[key]
        with controller.lock:
            if not hasattr(self, "ppms_sr830_range_states"):
                self.ppms_sr830_range_states = {}
            state = self.ppms_sr830_range_states.get(key)
            if state is None:
                state = STFMRSR830Controller.__new__(STFMRSR830Controller)
                state.auto_range_enabled = True
                state.low_range_count = state.range_cooldown = 0
                state.low_range_peak = 0.0
                state.phase = None
                state.clear_latch_pending = False
                self.ppms_sr830_range_states[key] = state
            state.instrument = controller.instrument
            state.sensitivity_index = int(self._ppms_query_numbers(key, "SENS?", 1)[0])
            old = state.sensitivity_index
            scales = NONLOCAL_SR830_FULL_SCALE_V
            top = len(scales) - 1
            if not 0 <= old <= top:
                raise RuntimeError("SR830 sensitivity readback invalid")
            magnitude = abs(float(r_value))
            if not math.isfinite(magnitude):
                raise RuntimeError("SR830 amplitude readback invalid")
            action = ""
            overloaded = bool(status & 7)
            floor = getattr(state, "overload_floor", None) or 0
            if not overloaded and not allow_down:
                pass
            elif not overloaded:
                if state.range_cooldown > 0:
                    state.range_cooldown -= 1
                    state.low_range_count = 0
                elif enabled and old > max(0, floor) and (not status & 8) and (magnitude < PPMS_SR830_DOWNRANGE_FRACTION * scales[old]):
                    state.low_range_count += 1
                    if state.low_range_count >= PPMS_SR830_DOWNRANGE_READINGS:
                        requested = old - 1
                        controller.instrument.write(f"SENS {requested}")
                        actual = float(controller.instrument.query("SENS?").strip())
                        if actual != requested:
                            raise RuntimeError(f"SR830 range-down readback mismatch: requested {requested}, read {actual}")
                        state.sensitivity_index = requested
                        state.low_range_count = 0
                        state.range_cooldown = PPMS_SR830_RANGE_COOLDOWN_READINGS
                        state.clear_latch_pending = True
                        action = f"SENS {old}->{requested} (low signal)"
                else:
                    state.low_range_count = 0
            if overloaded:
                if old >= top:
                    self._post_sr830_range_label(key, "OVERLOAD; at maximum range", status, "", old)
                    raise PPMSLockinRangeError(f"{controller.label} OVERLOAD: already at maximum sensitivity, check the input and gain")
                requested = old + 1
                controller.instrument.write(f"SENS {requested}")
                actual = float(controller.instrument.query("SENS?").strip())
                if actual != requested:
                    raise RuntimeError(f"SR830 autorange readback mismatch: requested {requested}, read {actual}")
                state.sensitivity_index = requested
                state.clear_latch_pending = True
                state.low_range_count = 0
                state.range_cooldown = PPMS_SR830_RANGE_COOLDOWN_READINGS
                state.overload_floor = max(floor, requested)
                action = f"SENS {old}->{requested} (overload)"
            label = ('OVERLOAD; ' if overloaded else '') + (f'autorange {action}' if action else 'reference locked' if not status & 8 else 'reference unlocked')
            self._post_sr830_range_label(key, label, status, action, state.sensitivity_index)
            if action:
                return self._ppms_sr830_settle_s(key, 5.0)
            return 0.
    @staticmethod
    def _format_voltage_range(value) -> str:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "--"
        if not math.isfinite(value) or value <= 0:
            return "--"
        for factor, unit in ((1.0, "V"), (1e-3, "mV"), (1e-6, "uV"), (1e-9, "nV")):
            if value >= factor * (1 - 1e-9):
                return f"{value / factor:g} {unit}"
        return f"{value:g} V"
    @classmethod
    def _format_sr830_full_scale(cls, index) -> str:
        scales = NONLOCAL_SR830_FULL_SCALE_V
        if not isinstance(index, int) or not 0 <= index < len(scales):
            return "--"
        return cls._format_voltage_range(scales[index])
    def _ppms_compact_status(self, source):
        display = tk.StringVar(value="Not connected")
        def refresh(*_):
            text = str(source.get()).lower()
            if any(word in text for word in ("warning", "failed", "error", "overload", "unlocked", "mismatch")):
                value = "Warning"
            elif "pass complete" in text:
                value = "Pass complete"
            elif "not connected" in text:
                value = "Not connected"
            elif "not read" in text:
                value = "Not read"
            elif "lock" in text:
                value = "Locked"
            elif any(word in text for word in ("connecting", "updating", "waiting")):
                value = "Updating"
            else:
                value = "Connected"
            display.set(value)
        if hasattr(source, "trace_add"):
            source.trace_add("write", refresh)
        refresh()
        return display
    def _post_sr830_range_label(self, key, label, status, action, range_index=None):
        range_text = "Autorange " + self._format_sr830_full_scale(range_index)
        def update():
            if key in getattr(self, "ppms_sr830_range_vars", {}):
                self.ppms_sr830_range_vars[key].set(range_text)
            if key in getattr(self, "ppms_sr830_status_vars", {}):
                self.ppms_sr830_status_vars[key].set(label)
            self._set_connection_indicator(f'ppms_ref_{key}', not bool(status & 15) and (not action), fault=bool(status & 7))
        self.post_ui(update)
    def _stop_ppms_transport_source(self, *, read_back: bool = False) -> None:
        self.ppms_idle_autorange_enabled = False
        controller = self.ppms_instrument_controllers["6221"]
        controller.safe_output_off()
        text = "Transport acquisition: 6221 output OFF"
        if read_back:
            try:
                state = self._ppms_query_numbers("6221", "OUTP?", 1)[0]
            except Exception as error:
                text = f"Transport acquisition: 6221 OFF sent; read-back failed: {short_status_error(error, 60)}"
            else:
                if state == 0:
                    text = "Transport acquisition: 6221 output OFF (read back)"
                else:
                    text = f"Transport acquisition: 6221 still ON (OUTP?={state:g}); switch it off"
                    self.thread_log(f"PPMS: the 6221 still reads ON (OUTP?={state:g}) after the scan's output off")
        self.post_ui(lambda: self.ppms_transport_status_var.set(text))
    @staticmethod
    def _ppms_field_is_ready(result: dict[str, object], target_oe: float, driven: bool = False) -> bool:
        measured = float(result["field_oe"])
        tolerance = max(PPMS_FIELD_TOLERANCE_OE, abs(target_oe) * PPMS_FIELD_RELATIVE_TOLERANCE)
        status = str(result.get("field_status", "")).casefold().replace(" ", "")
        if driven and "persistent" in status:
            return False
        stable_status = any((token in status for token in ('holding', 'persistent', 'stable', 'standby')))
        return stable_status and abs(measured - target_oe) <= tolerance
    def start_ppms_field_scan(self) -> None:
        if self.ppms_field_scan_running or self.ppms_environment_busy:
            return
        self.ppms_field_scan_stop_event.clear()
        try:
            self._require_ppms_control_ready()
            mode = self.ppms_simple_mode.get()
            if mode not in PPMS_SCAN_MODES:
                raise ValueError("Please select a valid scan mode")
            def number(variable, label):
                raw = str(variable.get()).strip()
                if not raw:
                    raise ValueError(f"Please fill in {label}; the live reading on the right does not replace the setpoint on the left")
                try:
                    value = float(raw)
                except ValueError:
                    raise ValueError(f"{label} must be a number, current input: {raw!r}") from None
                if not math.isfinite(value):
                    raise ValueError(f"{label} must be a finite value")
                return value
            plan = plan_ppms_scan_axes(
                mode,
                tuple(
                    number(v, f"frequency {label} (Hz)" if mode == "Frequency" else f"scan {label}")
                    for v, label in zip(
                        self.ppms_simple_range[:2] if mode == "Frequency" else self.ppms_simple_range,
                        ("start", "end") if mode == "Frequency" else ("start", "end", "step"),
                    )
                ),
                outer_field_range=tuple(number(v, f"outer field {label} (Oe)") for v, label in zip(self.ppms_outer_field_scan_range, ("start", "end", "step"))) if mode == "Angle+Field" else None,
                temperature_range=tuple(number(v, f"outer temperature {label}") for v, label in zip(self.ppms_temperature_scan_range, ("start", "end", "step"))) if mode in ("Field+Temperature", "Angle+Temperature", "Current+Temperature") else None,
                frequency_range=tuple(number(v, f"outer frequency {label} (Hz)") for v, label in zip(self.ppms_frequency_scan_range, ("start", "end"))) if mode == "Frequency+Current" else None,
            )
            values = plan.values
            frequencies = plan.frequencies
            current_sweep = plan.current_sweep
            scan_field = plan.scan_field
            field_outer = plan.field_outer
            field_commanded = plan.field_commanded
            outer_fields = plan.outer_fields
            temperatures = plan.temperatures
            fixed_field = values[0] if scan_field else outer_fields[0] if field_outer else self._ppms_field_read_at_start()
            fixed_temp = temperatures[0] if temperatures is not None else number(self.ppms_temperature_target_var, "the fixed temperature on the left (K)")
            temperature_delay_s = 0.0
            if mode in ("Field+Temperature", "Angle+Temperature", "Current+Temperature"):
                temperature_delay_s = number(self.ppms_temperature_delay_var, 'wait after each temperature is reached (Wait s)')
                if not 0 <= temperature_delay_s <= 86400:
                    raise ValueError("The wait after each temperature (Wait s) must be within 0–86400 s")
            fixed_angle = values[0] if mode in ("Angle", "Angle+Temperature", "Angle+Field") else number(self.ppms_motor_target_var, "the fixed angle on the left (°)")
            start_oe, end_oe = (values[0], values[-1]) if scan_field else (outer_fields[0], outer_fields[-1]) if field_outer else (fixed_field, fixed_field)
            loops = 1
            stable_s = number(self.ppms_field_scan_stable_var, "settle time per point (s)")
            acquisition_s = number(self.ppms_acquisition_time_var, "acquisition time per point (s)")
            if not 0 < acquisition_s <= 86400:
                raise ValueError("Acquisition time must be greater than 0 and at most 86400 s")
            rate_oe_s = number(self.ppms_field_rate_var, "field rate (Oe/s)")
            if scan_field:
                validate_ppms_field_command(start_oe, rate_oe_s)
                validate_ppms_field_command(end_oe, rate_oe_s)
            elif field_outer:
                for field in outer_fields:
                    try:
                        validate_ppms_field_command(field, rate_oe_s)
                    except ValueError as error:
                        raise ValueError(f"Outer field {field:g} Oe: {error}") from None
            else:
                validate_ppms_field_command(0.0, rate_oe_s)
            if not math.isfinite(stable_s) or not 0 <= stable_s <= 86400:
                raise ValueError("PPMS settle time per point must be within 0–86400 s")
            fields = values if scan_field else outer_fields if field_outer else (fixed_field,)
            if scan_field:
                fields = fields + fields[-2::-1]
            sequence = tuple((1, i, len(fields), value) for i, value in enumerate(fields, 1))
            approach = self._ppms_field_approach_code(self.ppms_field_approach_var.get())
            if not self.ppms_instrument_controllers["6221"].connected:
                raise RuntimeError("A PPMS field scan must measure synchronously; connect the Keithley 6221 first")
            active_lockins, use_zurich = self._ppms_selected_lockins()
            addresses = [self._ppms_sr830_address(self.ppms_instrument_controllers[key].resource_name) for key in active_lockins]
            if len(addresses) != len(set(addresses)):
                raise RuntimeError("SR830 slots cannot share a GPIB address")
            transport_settings = validate_ppms_transport_scan_settings(
                values[0] if current_sweep else self.ppms_transport_current_var.get(),
                frequencies[0] if frequencies else self.ppms_transport_frequency_var.get(),
                self.ppms_transport_compliance_var.get(),
                self._ppms_harmonic_value("830_a"),
                self._ppms_harmonic_value("830_b"),
                active_lockins,
                allow_no_lockins=use_zurich,
                harmonic_c=self._ppms_harmonic_value("830_c"),
            )
            nested = {'angle': values if mode in ('Angle', 'Angle+Temperature', 'Angle+Field') else (fixed_angle,), 'temperature': temperatures if temperatures is not None else (fixed_temp,)}
            currents = values if current_sweep else (transport_settings["current_rms_ma"],)
            for current in currents:
                validate_ppms_transport_scan_settings(current, transport_settings["frequency_hz"], transport_settings["compliance_v"],
                    transport_settings["harmonic_a"], transport_settings["harmonic_b"], active_lockins, allow_no_lockins=use_zurich,
                    harmonic_c=transport_settings["harmonic_c"])
            if "angle" in nested:
                if self.ppms_environment_controller.backend != PPMS_QDINSTRUMENT_BACKEND or not self.ppms_motor_confirm_var.get():
                    raise ValueError("Angle scans need QDInstrument and the motor-specific safety confirmation")
                if any(not -370 <= a <= 370 for a in nested["angle"]):
                    raise ValueError("Angle range is limited to −370 to 370°")
            temp_rate = float(self.ppms_temperature_rate_var.get())
            for t in nested.get("temperature", ()):
                ppms_scan_temperature_segments(float((self.ppms_last_readback or {}).get("temperature_k", 300)), t, temp_rate)
            total = len(sequence) * len(nested["angle"]) * len(nested["temperature"]) * len(currents)
            harmonic_passes = self._ppms_harmonic_passes(transport_settings)
            total *= len(harmonic_passes)
            if frequencies:
                total *= len(frequencies)
            if total > 100000:
                raise ValueError("Nested scans are limited to 100000 measurement points")
            if frequencies:
                for frequency in frequencies:
                    for config in harmonic_passes:
                        try:
                            validate_ppms_transport_scan_settings(
                                config["current_rms_ma"], frequency, config["compliance_v"],
                                config["harmonic_a"], config["harmonic_b"], config["active_lockins"],
                                allow_no_lockins=bool(config.get("allow_no_lockins", False)),
                                harmonic_c=config["harmonic_c"])
                        except ValueError as error:
                            raise ValueError(f"At {frequency:g} Hz: {error}") from None
                transport_settings["frequencies"] = tuple(frequencies)
            if any(transport_settings[k] not in (1, 2, 3) for k in ("harmonic_a", "harmonic_b")):
                raise ValueError("SR830 harmonic must be 1, 2 or 3")
            transport_settings["nested"] = nested
            if field_outer:
                transport_settings["outer_fields"] = tuple(outer_fields)
            transport_settings["currents"] = currents
            transport_settings["scan_mode"] = mode
            transport_settings["acquisition_s"] = acquisition_s
            transport_settings["temperature_rate"] = temp_rate
            transport_settings["temperature_delay_s"] = temperature_delay_s
            transport_settings["temperature_approach"] = self._ppms_temperature_approach_code(self.ppms_temperature_approach_var.get())
            slowdown = float(self.ppms_motor_speed_var.get())
            if not slowdown.is_integer() or not 0 <= slowdown <= 14:
                raise ValueError("Decel code must be an integer within 0–14")
            transport_settings["slowdown"] = int(slowdown)
            transport_settings["harmonic_passes"] = tuple(self._ppms_harmonic_passes(transport_settings))
            sr830_tc_code = self._ppms_sr830_time_constant_code()
        except (ValueError, TypeError, RuntimeError, tk.TclError) as error:
            messagebox.showerror("PPMS scan parameters", str(error), parent=self.root)
            return
        initial_field = start_oe
        if self.ppms_last_readback is not None:
            try:
                initial_field = float(self.ppms_last_readback["field_oe"])
            except (KeyError, TypeError, ValueError):
                pass
        pass_settles = self._ppms_settle_per_pass(stable_s, harmonic_passes, use_zurich)
        points_per_pass = total // max(1, len(harmonic_passes))
        blocks = len(nested["angle"]) * len(nested["temperature"]) * len(currents)
        field_ramp_s = 0.0
        if scan_field:
            previous = initial_field
            for _pass in harmonic_passes:
                for _block in range(blocks):
                    for _loop, _point, _count, target in sequence:
                        field_ramp_s += abs(target - previous) / rate_oe_s
                        previous = target
        rotator_moves = 0
        if field_outer:
            previous = initial_field
            for _pass in harmonic_passes:
                for _loop, _point, _count, target in sequence:
                    field_ramp_s += abs(target - previous) / rate_oe_s
                    previous = target
            angles = nested["angle"]
            def moves(start, end):
                return len(ppms_scan_axis(start, end, 0.9)) - 1
            try:
                present_angle = float((self.ppms_last_readback or {})["angle_deg"])
            except (KeyError, TypeError, ValueError):
                present_angle = float(angles[0])
            sweeps = len(sequence) * len(harmonic_passes)
            sweep_moves = sum(moves(a, b) for a, b in zip(angles, angles[1:]))
            return_moves = moves(angles[-1], angles[0])
            rotator_moves = moves(present_angle, angles[0]) + sweeps * sweep_moves + (sweeps - 1) * return_moves
            blocks *= len(sequence)
        estimated_seconds = field_ramp_s + sum(((settle + acquisition_s) * points_per_pass for settle, _tc, _meter in pass_settles))
        estimated_seconds += rotator_moves * PPMS_ROTATOR_MOVE_ALLOWANCE_S
        set_ups = blocks * len(harmonic_passes)
        if frequencies:
            blocks = len(nested["angle"]) * len(nested["temperature"]) * len(frequencies)
            set_ups = blocks
            estimated_seconds += len(nested['angle']) * len(nested['temperature']) * self._ppms_frequency_set_up_s(frequencies, pass_settles, acquisition_s)
        estimated_seconds += 6.0 * total + PPMS_SET_UP_ALLOWANCE_S * set_ups
        previous_temperature = float((self.ppms_last_readback or {}).get("temperature_k", nested["temperature"][0]))
        for _pass in harmonic_passes:
            for _angle in ((None,) if mode == "Angle+Temperature" else nested["angle"]):
                for temperature in nested["temperature"]:
                    estimated_seconds += abs(temperature - previous_temperature) / temp_rate * 60.0
                    estimated_seconds += temperature_delay_s
                    previous_temperature = temperature
        estimate_text = self._format_scan_duration(estimated_seconds)
        rotation_note = ""
        try:
            present_angle = float((self.ppms_last_readback or {})["angle_deg"])
            turn = abs(float(nested["angle"][0]) - present_angle)
            if turn > 30.0:
                rotation_note = f"\n\nThe sample rotator turns {turn:.1f}° first ({present_angle:.1f}° → {float(nested['angle'][0]):.1f}°)."
        except (KeyError, TypeError, ValueError):
            pass
        settle_s, slowest_tc_s, slowest_meter = max(pass_settles, key=lambda item: item[0])
        settle_text = ppms_lockin_settle_text(stable_s, settle_s, slowest_tc_s, slowest_meter)
        settle_line = f"Wait before each reading: {settle_text}"
        if temperature_delay_s:
            settle_line = f'Wait after each temperature is stable: {self._format_scan_duration(temperature_delay_s)}, then the scan at it\n' + settle_line
        later = sorted({settle for settle, _tc, _meter in pass_settles} - {settle_s})
        if later:
            settle_line += '; harmonic passes with fewer lock-ins wait ' + ', '.join((f'{settle:g}' for settle in later)) + ' s'
        frequency_text = ppms_frequency_count_text(frequencies) if frequencies else ""
        frequency_line = ""
        if frequencies:
            frequency_line = (
                f"Frequencies: {frequency_text}"
                + (" (Step not used)" if mode == "Frequency" else ", the whole current sweep at each")
                + "\nAt each frequency the 6221 is switched off and set up again at it and the lock-ins' "
                "reference lock is awaited; the first point then waits as below, at least "
                f"{NONLOCAL_FSCAN_SETTLE_PERIODS:g} periods of the frequency, and takes agreeing windows"
                + ("; the other currents change only the 6221 amplitude" if mode == "Frequency+Current" else "")
                + f"\nHeld as in a Current scan: {fixed_temp:g} K (T K) and {fixed_angle:g}° (Angle °); "
                "the field is not commanded\n")
        if field_outer:
            def step_text(points, unit):
                return f" (Step {abs(points[1] - points[0]):g}{unit})" if len(points) > 1 else ""
            angles = nested["angle"]
            ramp_step = f'about {self._format_scan_duration(abs(sequence[1][3] - sequence[0][3]) / rate_oe_s)} per step; ' if len(sequence) > 1 else ''
            frequency_line = (
                f"Outer fields: {len(sequence)} steps, {sequence[0][3]:g} to {sequence[-1][3]:g} Oe"
                f"{step_text([point[3] for point in sequence], ' Oe')}, the whole angle sweep at each\n"
                f"At each field: set Driven at {rate_oe_s:g} Oe/s ({ramp_step}about "
                f"{self._format_scan_duration(abs(sequence[0][3] - initial_field) / rate_oe_s)} to the first field "
                f"from {initial_field:g} Oe; {self._format_scan_duration(field_ramp_s)} of ramps in all), its "
                "stable reading awaited, then the angles; the first angle after each field change takes "
                "agreeing windows\n"
                f"Angles per field: {len(angles)}, {angles[0]:g} to {angles[-1]:g}°{step_text(angles, '°')}; "
                f"rotator: {sweep_moves} moves of at most 0.9° per sweep and {return_moves} back to "
                f"{angles[0]:g}° before each later field, {rotator_moves} moves in all (planned at "
                f"{PPMS_ROTATOR_MOVE_ALLOWANCE_S:g} s each)\n"
                f"Held as in an Angle scan: {fixed_temp:g} K (T K)\n")
        if field_commanded:
            field_end = 'the field stays at its setpoint after Stop, and is set to 0 Oe after I off, a field failure or closing the program'
        else:
            field_end = f"the field is not commanded (the CSV's target_field_oe is the field read now, {fixed_field:g} Oe)"
        end_line = f'\n\nAt every end (completed, stopped or failed) the 6221 is switched off and read back as off. The temperature and the angle stay where the scan left them; {field_end}.'
        if not self._ask_scan_start("PPMS scan time estimate", f"Scan mode: {mode}\n{frequency_line}Measurement points: {total}\n{settle_line}\n"
            f"Estimated duration: about {estimate_text}\n\n"
            "This is an estimate; the actual duration may grow with instrument settling and rotation waits."
            + rotation_note
            + end_line
            + "\n\nContinue to choose the run folder and start the scan?", f'about {estimate_text}', warning=rotation_note.strip(), icon="warning" if rotation_note else "question", parent=self.root):
            return
        try:
            run_settings = self._ppms_run_settings(mode, sequence, rate_oe_s, stable_s, approach, transport_settings, pass_settles, settle_s, estimated_seconds, fixed_field, field_commanded)
        except Exception as error:
            messagebox.showerror('PPMS run log', f'Could not gather the settings for the run log; the scan was not started: {error}', parent=self.root)
            return
        last_folder = self.__dict__.get("ppms_run_folder")
        initial_dir = None
        if last_folder is not None and Path(last_folder).parent.is_dir():
            initial_dir = Path(last_folder).parent
        if self.ppms_field_scan_stop_event.is_set():
            self.ppms_field_scan_status_var.set(f"{mode} scan not started: I off was pressed")
            return
        recorder = begin_scan_run(self.root, "PPMS", "Select the folder for the PPMS run", initial_dir, run_settings)
        if recorder is None:
            return
        if self.ppms_field_scan_stop_event.is_set():
            recorder.close("not_started", stop_reason="I off was pressed before the scan started", points_saved=0)
            self.ppms_run_folder = recorder.directory
            self.ppms_field_scan_status_var.set(f"{mode} scan not started: I off was pressed")
            return
        self.ppms_run_recorder, self.ppms_run_folder = recorder, recorder.directory
        self.thread_log(f'PPMS {mode} scan; run folder: {recorder.directory} ({recorder.csv_path.name} and {recorder.log_path.name})')
        self.thread_log(f"PPMS {mode} scan: each reading waits {settle_text}")
        if frequencies:
            self.thread_log(f'PPMS {mode} scan: {frequency_text}' + (', the whole current sweep at each' if mode == 'Frequency+Current' else ''))
        if field_outer:
            self.thread_log(f'PPMS {mode} scan: {len(sequence)} outer fields, {sequence[0][3]:g} to {sequence[-1][3]:g} Oe at {rate_oe_s:g} Oe/s, the whole angle sweep at each')
        self.ppms_scan_settle_logged = settle_s
        self.ppms_scan_sr830_tc_code = sr830_tc_code
        self.ppms_field_scan_running = True
        self.ppms_scan_preserve_on_stop = False
        self.ppms_scan_outputs = None
        self.ppms_scan_field_note = ""
        self.ppms_scan_stop_button = ""
        self.ppms_zero_persist_pending = False
        if "ppms_zero_persist_button" in self.__dict__:
            self.ppms_zero_persist_button.configure(text="Zero field + persist after scan")
        self.ppms_active_scan_mode = mode
        self.ppms_plot_addresses = dict(zip(active_lockins, addresses))
        self.ppms_plot_axis = PPMS_SCAN_PLOT_AXES[mode]
        self.ppms_plot_angle_range = (min(nested['angle']), max(nested['angle'])) if self.ppms_plot_axis == 'angle' else None
        self.ppms_field_scan_rows = []
        self._redraw_ppms_transport_plot()
        self.ppms_field_scan_started_at = time.monotonic()
        self.ppms_field_scan_total = total
        self.ppms_field_scan_progress.configure(maximum=max(1, total), value=0)
        self.ppms_field_scan_eta_var.set(f'Remaining: about {estimate_text}')
        self.ppms_field_scan_status_var.set(
            f"{mode} scan: " + (f"{frequency_text}; " if frequencies else "")
            + (f"{len(sequence)} fields x {len(nested['angle'])} angles; " if field_outer else "")
            + f"{total} points, settle {ppms_settle_status_text(settle_s)} s; "
            f"estimated about {estimate_text}"
        )
        self.ppms_transport_status_var.set('Transport acquisition: configuring the 6221 and the connected SR830…')
        self._update_ppms_control_ui()
        self._ppms_close_handoff().begin()
        try:
            future = self.ppms_executor.submit(self._ppms_scan_with_final_field, sequence, rate_oe_s, stable_s, approach, transport_settings)
        except Exception as error:
            self._ppms_close_handoff().end()
            self.ppms_field_scan_running = False
            self._ppms_close_run("not_started", error=str(error), points_saved=0)
            self._update_ppms_control_ui()
            messagebox.showerror("PPMS scan not started", str(error), parent=self.root)
            return
        def finished() -> None:
            stopped = self.ppms_field_scan_stop_event.is_set()
            error = None
            try:
                future.result()
            except BaseException as caught:
                error = caught
            self.ppms_field_scan_running = False
            self._update_ppms_control_ui()
            self._report_ppms_scan_finished(mode, stopped, error)
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def _ppms_field_read_at_start(self) -> float:
        try:
            field = float((self.ppms_last_readback or {})["field_oe"])
        except (KeyError, TypeError, ValueError):
            field = math.nan
        if not math.isfinite(field):
            raise ValueError('No PPMS field reading yet: wait for the live reading, then start the scan again (this mode records the field read at Start as its target_field_oe)')
        return field
    def _ppms_run_settings(self, mode, sequence, rate_oe_s, stable_s, approach, transport_settings, pass_settles, settle_s, estimated_s, fixed_field, scan_field):
        state = self.__dict__
        def box(variable):
            try:
                return variable.get()
            except Exception:
                return None
        resources = {}
        boxes = state.get("ppms_resource_vars", {})
        for key, controller in state.get("ppms_instrument_controllers", {}).items():
            typed = box(boxes[key]) if key in boxes else None
            resources[key] = dict(label=str(getattr(controller, "label", key)),
                                  resource=str(getattr(controller, "resource_name", "") or typed or ""),
                                  connected=bool(getattr(controller, "connected", False)),
                                  identity=str(getattr(controller, "identity", "") or ""))
        zurich = state.get("zurich_controller")
        if zurich is not None:
            resources["zurich"] = dict(label="Zurich MFLI",
                                       resource=str(box(state["zurich_resource"]) or "") if "zurich_resource" in state else "",
                                       connected=bool(getattr(zurich, "mfli_connected", False)),
                                       identity=str(getattr(zurich, "mfli_identity", "") or ""))
        passes = transport_settings.get("harmonic_passes", ())
        scan = {key: value for key, value in transport_settings.items() if key != "harmonic_passes"}
        safety = state.get("ppms_control_safety_var")
        return dict(
            mode=mode, scan=scan,
            field=dict(commanded=bool(scan_field), sequence_oe=[point[3] for point in sequence],
                       rate_oe_s=rate_oe_s, approach_code=approach,
                       target_field_oe=(None if scan_field else fixed_field),
                       note=("the scan sets each field, Driven" if scan_field
                             else "not commanded; target_field_oe is the field read at Start")),
            timing=dict(settle_s=stable_s, acquire_s=transport_settings.get("acquisition_s"),
                        effective_settle_s=settle_s, settle_time_constants=LOCKIN_SETTLE_TAUS,
                        per_pass=[dict(harmonic_pass=index, effective_settle_s=wait, slowest_tc_s=tc,
                                       slowest_meter=meter)
                                  for index, (wait, tc, meter) in enumerate(pass_settles, 1)],
                        estimated_s=estimated_s),
            harmonic_passes=[dict(harmonic_pass=config.get("harmonic_pass", index),
                                  harmonics={PPMS_SR830_LABELS.get(key, key):
                                             config.get(PPMS_SR830_HARMONIC_SETTING_KEYS.get(key, ""))
                                             for key in config.get("active_lockins", ())})
                             for index, config in enumerate(passes, 1)],
            zurich=(dict(harmonics=state.get("zurich_scan_harmonic"), reference=state.get("zurich_scan_reference"),
                         time_constant_s=state.get("zurich_scan_tau"))
                    if state.get("zurich_scan_enabled") else dict(used=False)),
            sr830_time_constant_codes=dict(state.get("ppms_sr830_time_constant_codes") or {}),
            ending=("every end zeroes the 6221 (read back); the temperature and the angle are kept; "
                    + ("Stop keeps the field, I off / a field failure / closing set it to 0 Oe" if scan_field
                       else "the field is not commanded")),
            environment=dict(backend=str(getattr(state.get("ppms_environment_controller"), "backend", "")),
                             last_reading=dict(state.get("ppms_last_readback") or {})),
            resources=resources,
            safety_confirmed=bool(box(safety)) if safety is not None else None)
    def _ppms_log(self, name, **details) -> None:
        recorder = self.__dict__.get("ppms_run_recorder")
        if recorder is not None:
            recorder.event(name, **details)
    def _ppms_log_check(self) -> None:
        recorder = self.__dict__.get("ppms_run_recorder")
        if recorder is not None:
            recorder.check()
    def _ppms_append_row(self, row) -> None:
        recorder = self.__dict__.get("ppms_run_recorder")
        if recorder is None:
            self._append_ppms_live_csv(row)
        else:
            recorder.append_row(row)
    def _ppms_close_run(self, outcome, **details) -> list[str]:
        recorder = self.__dict__.get("ppms_run_recorder")
        self.ppms_run_recorder = None
        if recorder is None or recorder.closed:
            return []
        errors = recorder.close(outcome, **details)
        try:
            self.thread_log(f'PPMS run folder {recorder.directory}: {outcome}; {recorder.csv_path.name} and {recorder.log_path.name} closed')
            if errors:
                self.thread_log(f"PPMS run log: {'; '.join(errors)}")
        except Exception:
            pass
        return errors
    def _ppms_show_later(self, show) -> None:
        try:
            self.root.after(0, show)
        except tk.TclError:
            pass
    def _ppms_zero_6221_after_scan(self):
        controller = self.ppms_instrument_controllers["6221"]
        if not controller.connected:
            return "not connected"
        self.ppms_idle_autorange_enabled = False
        with controller.lock:
            controller.safe_output_off()
            state = self._ppms_query_numbers("6221", "OUTP?", 1)[0]
        if state != 0:
            raise RuntimeError(f"the 6221 output did not read back OFF (OUTP? {state:g})")
        return "zeroed"
    def _ppms_zero_outputs_after_scan(self) -> dict[str, str]:
        results = zero_scan_outputs((("6221", self._ppms_zero_6221_after_scan),))
        try:
            self._ppms_log("outputs_zeroed", **results)
            self.thread_log(f"PPMS scan end: 6221 {results['6221']}")
            outcome = results["6221"]
            def show():
                if self.closing:
                    return
                if outcome == "zeroed":
                    self.ppms_transport_status_var.set("Transport acquisition: 6221 output OFF (read back)")
                    live = self.__dict__.get("ppms_current_live_var")
                    if live is not None:
                        live.set("OFF / 0 mA")
                elif outcome != "not connected":
                    self.ppms_transport_status_var.set(f'Transport acquisition: 6221 {outcome[:120]}; switch it off')
            self.post_ui(show)
        except Exception:
            pass
        return results
    def _queue_ppms_zero_persist(self):
        if not self.ppms_field_scan_running:
            return
        if getattr(self, "ppms_zero_persist_pending", False):
            self.ppms_zero_persist_pending = False
            self.ppms_zero_persist_button.configure(text='Zero field + persist after scan')
            self.ppms_field_scan_status_var.set('Zero field + persist after this scan: cancelled')
            return
        self.ppms_zero_persist_pending = True
        self.ppms_zero_persist_button.configure(text='Queued: zero + persist')
        self.ppms_field_scan_status_var.set('Queued: after this scan completes normally, the field is zeroed and switched to Persistent; click again to cancel')
    def _ppms_scan_with_final_field(self, *args):
        self.ppms_zero_persist_completed = False
        self.ppms_scan_outputs = None
        self._ppms_close_handoff().begin()
        self.ppms_scan_field_zero_accepted = False
        try:
            try:
                if len(args) == 5:
                    sequence, rate, stable, approach, settings = args
                    passes = settings.get("harmonic_passes") or self._ppms_harmonic_passes(settings)
                    self._ppms_log('scan_started', mode=settings.get('scan_mode', ''), points=self.__dict__.get('ppms_field_scan_total'), harmonic_passes=len(passes))
                    self._ppms_log_check()
                    for index, pass_settings in enumerate(passes, 1):
                        if self.ppms_field_scan_stop_event.is_set() or self.closing:
                            break
                        self._ppms_log("harmonic_pass", harmonic_pass=pass_settings.get("harmonic_pass", index),
                                       harmonics={PPMS_SR830_LABELS.get(key, key):
                                                  pass_settings.get(PPMS_SR830_HARMONIC_SETTING_KEYS.get(key, ""))
                                                  for key in pass_settings.get("active_lockins", ())})
                        self._ppms_log_check()
                        self._ppms_nested_scan_worker(sequence, rate, stable, approach, pass_settings)
                else:
                    self._ppms_nested_scan_worker(*args)
            finally:
                self.ppms_scan_outputs = self._ppms_zero_outputs_after_scan()
            if self.ppms_field_scan_stop_event.is_set() or self.closing or (not getattr(self, 'ppms_zero_persist_pending', False)):
                return
            self.post_ui(lambda: self.ppms_field_scan_status_var.set('CSV saved; zeroing field and switching to Persistent…'))
            self.post_ui(lambda: self._end_ppms_persistent_follow_up('Zero + persist'))
            remote = self.ppms_environment_controller
            for mode in (1, 0):
                if self.ppms_field_scan_stop_event.is_set() or self.closing:
                    raise PPMSScanCancelled('Zero/Persistent wrap-up cancelled')
                remote.set_field(0., 100., 0, mode)
                deadline = time.monotonic() + 1800
                while True:
                    if self.ppms_field_scan_stop_event.is_set() or self.closing:
                        raise PPMSScanCancelled('Zero/Persistent wrap-up cancelled')
                    data = remote.read_environment()
                    self.post_ui(lambda d=dict(data): self._apply_ppms_environment_readback(d))
                    status = str(data.get('field_status', '')).strip().lower()
                    zero = self._ppms_field_is_ready(data, 0.)
                    if zero and (mode == 1 or status in ('persistent', 'stablepersistent')):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Scan data saved; timed out confirming zero field / Persistent')
                    self.ppms_field_scan_stop_event.wait(1.)
            self.ppms_zero_persist_completed = True
        finally:
            self.ppms_zero_persist_pending = False
            if 'ppms_zero_persist_button' in self.__dict__:
                self.post_ui(lambda: self.ppms_zero_persist_button.configure(text='Zero field + persist after scan'))
            if getattr(self, "closing", False):
                if self.__dict__.get('ppms_active_scan_mode', '') in PPMS_FIELD_COMMANDING_MODES and len(args) == 5 and (not self.__dict__.get('ppms_scan_field_zero_accepted', False)):
                    try:
                        if self.ppms_environment_controller.connected:
                            self._ppms_zero_field_after_interrupted_scan(args[1], args[3])
                    except Exception as error:
                        self.thread_log(f"PPMS: zeroing the field before close failed: {error}")
                self._ppms_close_run('stopped', stop_reason='the program closing', points_saved=len(self.__dict__.get('ppms_field_scan_rows') or ()), outputs=self.__dict__.get('ppms_scan_outputs'))
            self._ppms_close_handoff().end()
    def _report_ppms_scan_finished(self, mode, stopped, error):
        label = f"PPMS {mode} scan"
        results = self.__dict__.get("ppms_scan_outputs")
        field_note = str(self.__dict__.get("ppms_scan_field_note", "") or "")
        stop_reason = str(self.__dict__.get("ppms_scan_stop_button", "") or "")
        if isinstance(error, PPMSScanCancelled):
            self.thread_log(f"{label} cancelled normally: {error}")
            stop_reason = f"{stop_reason}: {error}" if stop_reason else str(error)
            error, stopped = None, True
        outputs_confirmed = bool(results) and all((str(state) in ('zeroed', 'not connected') for state in results.values()))
        field_mode = mode in PPMS_FIELD_COMMANDING_MODES
        if error is not None:
            outcome = "error"
            zero_tail = '' if outputs_confirmed else '; 6221 zero FAILED' if results else '; 6221 not confirmed zeroed'
            text = short_status_error(error, 100 - len(zero_tail))
            status = f"Failed: {text.rstrip(';') if zero_tail else text}{zero_tail}"
            if field_note == PPMS_FIELD_NOT_ZEROED_TEXT:
                status += f"; {PPMS_FIELD_NOT_ZEROED_TEXT}"
        else:
            extras = []
            if stopped:
                outcome, prefix = "stopped", "Stopped; "
                if field_mode:
                    extras.append(field_note or "field kept at its setpoint")
            else:
                outcome, prefix = "completed", "Completed; "
                if getattr(self, "ppms_zero_persist_completed", False):
                    extras.append("field zeroed and confirmed Persistent")
            status = prefix + scan_end_outputs_text(results, ('6221',), check=not extras) + ''.join((f'; {extra}' for extra in extras))
        self.ppms_macro_scan_result = outcome == "completed" and outputs_confirmed
        rows = self.__dict__.get("ppms_field_scan_rows") or ()
        self.save_scan_plot_snapshots(self.__dict__.get('ppms_run_recorder'), (('plots', self.__dict__.get('ppms_plot_frame')),), points=len(rows), redraw=self._redraw_ppms_transport_plot)
        problems = self._ppms_close_run(
            outcome, points_saved=len(rows), status=status, outputs=results,
            error=str(error) if error is not None else "",
            **({"stop_reason": stop_reason} if outcome == "stopped" else {}))
        self.ppms_field_scan_status_var.set(status)
        self.ppms_field_scan_eta_var.set("Remaining: 00:00" if outcome == "completed" else "Remaining: --")
        if self.closing:
            return
        zeroing = f"At the end: 6221 {results['6221']}" if results and '6221' in results else 'At the end: the 6221 was not confirmed zeroed'
        if field_note and field_note not in str(error or ""):
            zeroing += f"; {field_note}"
        if outcome == "error":
            text = f"{error}\n\n{zeroing}"
            if not outputs_confirmed:
                text += "\n\nCHECK IT: the 6221 was not confirmed at zero."
            self._ppms_show_later(lambda: messagebox.showerror(label, text, parent=self.root))
        elif not outputs_confirmed:
            text = f"The scan {outcome}, but the 6221 was not confirmed at zero: CHECK IT.\n\n{zeroing}"
            self._ppms_show_later(lambda: messagebox.showerror("PPMS outputs", text, parent=self.root))
        if problems:
            text = f"The run log was not closed cleanly: {'; '.join(problems)}\n\nThe live CSV keeps every saved point."
            self._ppms_show_later(lambda: messagebox.showerror("PPMS run log", text, parent=self.root))
    def _ppms_nested_scan_worker(self, sequence, rate, stable, approach, settings):
        stop = self.ppms_field_scan_stop_event
        remote = self.ppms_environment_controller
        nested = settings.get("nested", {})
        def wait_until(predicate, timeout, label, cancelled='Scan stopped; cancelling the motor wait does not mean the motor physically stopped'):
            deadline = time.monotonic() + timeout
            good_since = None
            while not stop.is_set() and not self.closing:
                data = self._read_ppms_live()
                self.post_ui(lambda d=dict(data): self._apply_ppms_environment_readback(d))
                if predicate(data):
                    good_since = good_since or time.monotonic()
                    if time.monotonic()-good_since >= max(1., stable):
                        return data
                else:
                    good_since = None
                if time.monotonic() >= deadline:
                    error_type = PPMSFieldScanError if label == "measured field" else TimeoutError
                    raise error_type(f"{label} settle timeout, last reading {data}")
                stop.wait(.5)
            raise PPMSScanCancelled(cancelled)
        def prepare_temperature(temperature):
            if temperature is None:
                return
            if stop.is_set() or self.closing:
                raise PPMSScanCancelled("Scan stopped")
            accepted = {"near", "stable", "stableat target", "stableattarget"}
            current = float(self._read_ppms_live()["temperature_k"])
            typed_rate = float(settings["temperature_rate"])
            segments = ppms_scan_temperature_segments(current, temperature, typed_rate)
            for target, rate in segments:
                restore_high_rate = rate < typed_rate and current <= PPMS_TEMPERATURE_LOW_BOUNDARY_K and (target > PPMS_TEMPERATURE_LOW_BOUNDARY_K)
                if stop.is_set() or self.closing:
                    raise PPMSScanCancelled("Scan stopped")
                self.thread_log(f"Scan temperature segment: {target:g} K at {rate:g} K/min")
                try:
                    try:
                        remote.set_temperature(target, rate, settings["temperature_approach"])
                    except ValueError as error:
                        if rate <= 1.0 or 'must not exceed 1 K/min' not in str(error):
                            raise
                        if stop.is_set() or self.closing:
                            raise PPMSScanCancelled("Scan stopped")
                        rate = PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                        restore_high_rate = target > PPMS_TEMPERATURE_LOW_BOUNDARY_K
                        self.thread_log(f'9 K boundary rate limit: switched to 1 K/min automatically, continuing to {target:g} K')
                        remote.set_temperature(target, rate, settings["temperature_approach"])
                except RuntimeError as error:
                    if "SetTemperature" not in str(error) or "return code 1" not in str(error):
                        raise
                    self.thread_log(f"Temperature command returned 1; waiting for the actual stable confirmation: {error}")
                def temperature_ready(data):
                    nonlocal restore_high_rate
                    measured = float(data['temperature_k'])
                    if restore_high_rate and measured > PPMS_TEMPERATURE_LOW_BOUNDARY_K:
                        if stop.is_set() or self.closing:
                            raise PPMSScanCancelled('Scan stopped')
                        try:
                            remote.set_temperature(target, typed_rate, settings['temperature_approach'])
                        except ValueError as error:
                            if 'must not exceed 1 K/min' not in str(error):
                                raise
                            return False
                        except RuntimeError as error:
                            if 'SetTemperature' not in str(error) or 'return code 1' not in str(error):
                                raise
                            self.thread_log(f'Rate-restore command reply uncertain; keeping the target and continuing readback: {error}')
                        restore_high_rate = False
                        self.thread_log(f'Temperature is above 9 K; target {target:g} K automatically restored to the typed {typed_rate:g} K/min (rate command sent)')
                        return False
                    return abs(measured - target) <= 0.1 and str(data['temperature_status']).strip().lower() in accepted
                wait_until(temperature_ready, 14400, "temperature")
            delay = float(settings.get("temperature_delay_s", 0.0) or 0.0)
            if delay > 0:
                self.thread_log(f"Temperature {temperature:g} K reached; waiting {delay:g} s before the scan at it")
                status = self.__dict__.get("ppms_field_scan_status_var")
                if status is not None:
                    self.post_ui(lambda: status.set(f"{temperature:g} K reached; waiting {delay:g} s"))
                if stop.wait(delay) or self.closing:
                    raise PPMSScanCancelled("Scan stopped during the wait after the temperature")
        temperature_outer = settings.get("scan_mode") == "Angle+Temperature"
        continuous_current = settings.get('scan_mode') in ('Current', 'Current+Temperature')
        current_source_started = False
        sweep_peak = max(settings.get('currents', (settings['current_rms_ma'],))) * math.sqrt(2.0) * 0.001 if continuous_current else None
        field_outer = settings.get("scan_mode") == "Angle+Field"
        last_field = [None]
        if field_outer:
            try:
                last_field[0] = float((getattr(self, "ppms_last_readback", None) or {})["field_oe"])
            except (KeyError, TypeError, ValueError):
                pass
        def set_outer_field(index, count, field):
            if stop.is_set() or self.closing:
                raise PPMSScanCancelled("Scan stopped; the next field was not set")
            self._ppms_log('field_step', index=index, count=count, target_field_oe=field, rate_oe_s=rate, approach_code=approach, mode='Driven')
            self._ppms_log_check()
            self.post_ui(lambda: self.ppms_field_scan_status_var.set(f'Field {index}/{count}: {field:g} Oe at {rate:g} Oe/s; waiting for it to settle…'))
            self.post_ui(lambda: self._end_ppms_persistent_follow_up("The field scan"))
            try:
                remote.set_field(field, rate, approach, 1)
            except Exception as error:
                raise PPMSFieldScanError(f"Field set failed: {error}") from error
            distance = abs(field - last_field[0]) if last_field[0] is not None else abs(field)
            data = wait_until(lambda d: self._ppms_field_is_ready(d, field, driven=True), max(3600.0, distance / rate + 180.0), 'measured field', cancelled='Scan stopped while the field settled')
            last_field[0] = float(data["field_oe"])
            self.ppms_source_fresh = True
        def scan_blocks():
            angles = tuple(enumerate(nested.get("angle", (None,)), 1))
            temperatures = tuple(enumerate(nested.get("temperature", (None,)), 1))
            if temperature_outer:
                for ti, temperature in temperatures:
                    prepare_temperature(temperature)
                    for ai, angle in angles:
                        yield ai, angle, ((ti, temperature),), None
            elif field_outer:
                fields = tuple(enumerate(settings.get("outer_fields", ()), 1))
                for fi, field in fields:
                    set_outer_field(fi, len(fields), field)
                    for ai, angle in angles:
                        yield ai, angle, temperatures, (fi, field)
            else:
                for ai, angle in angles:
                    yield ai, angle, temperatures, None
        failed = False
        preserve_range_output = False
        try:
            if settings.get("scan_mode") in ("Angle", "Angle+Temperature", "Angle+Field"):
                self._configure_ppms_transport_scan(settings)
                self.ppms_source_fresh = True
                settings = dict(settings, keep_source_between_angles=True)
            for ai, angle, temperatures, outer_field in scan_blocks():
                if stop.is_set() or self.closing:
                    break
                self._ppms_log_check()
                if angle is not None:
                    try:
                        motor = PPMSMotorDebug(remote.active)
                        motor.on_read = self._ppms_publish_motor_readback
                        initial_motor = motor.read()
                        if initial_motor["status"] not in ("AtTarget", "AtIndexSwitch"):
                            raise RuntimeError(f"Motor status unsafe before rotation: {initial_motor}")
                        current = initial_motor["angle_deg"]
                        for target in ppms_scan_axis(current, angle, 0.9)[1:]:
                            verified = motor.move_and_verify(target, settings['slowdown'], cancelled=lambda: stop.is_set() or self.closing, index_target=angle)
                            if verified is not None and abs(verified["angle_deg"] - angle) <= .06:
                                break
                    except PPMSScanCancelled:
                        raise
                    except Exception as error:
                        raise PPMSMotorScanError(f"{error}; field kept, not zeroed") from error
                if settings.get("scan_mode") in ("Field", "Field+Temperature"):
                    if stop.is_set() or self.closing:
                        break
                    field_target = sequence[0][3]
                    self.post_ui(lambda: self._end_ppms_persistent_follow_up("The field scan"))
                    try:
                        remote.set_field(field_target, rate, approach, 1)
                    except Exception as error:
                        raise PPMSFieldScanError(f"Field set failed: {error}") from error
                    if settings.get("scan_mode") in ("Field", "Field+Temperature"):
                        wait_until(lambda d: self._ppms_field_is_ready(d, field_target, driven=True), 3600, "measured field")
                for ti, temperature in temperatures:
                    if stop.is_set() or self.closing:
                        break
                    currents = tuple(settings.get("currents", (settings["current_rms_ma"],)))
                    stepped_to_first = False
                    if continuous_current and current_source_started:
                        self._update_ppms_transport_current(dict(settings, current_rms_ma=currents[0], peak_a=math.sqrt(2.0) * currents[0] * 0.001, continuous_sweep_peak_a=sweep_peak))
                        self.ppms_source_fresh = True
                        stepped_to_first = True
                    if not temperature_outer:
                        prepare_temperature(temperature)
                    self.ppms_scan_context = {"angle_index": ai, "temperature_index": ti,
                        "target_angle_deg": angle if angle is not None else "",
                        "target_temperature_k": temperature if temperature is not None else ""}
                    if settings.get("scan_mode") in PPMS_FREQUENCY_SCAN_MODES:
                        self._ppms_frequency_scan_block(sequence, rate, stable, approach, settings)
                        continue
                    for position, current in enumerate(currents):
                        if stop.is_set() or self.closing:
                            break
                        point_settings = dict(settings, current_rms_ma=current, peak_a=math.sqrt(2.0) * current * 0.001)
                        if continuous_current:
                            point_settings['continuous_sweep_peak_a'] = sweep_peak
                            if not current_source_started:
                                self._configure_ppms_transport_scan(point_settings)
                                self.ppms_source_fresh = True
                                current_source_started = True
                            elif not (position == 0 and stepped_to_first):
                                self._update_ppms_transport_current(point_settings)
                            point_settings['keep_source_between_current_points'] = True
                        block_sequence = sequence
                        if outer_field is not None:
                            block_sequence = ((1, 1, 1, outer_field[1]),)
                            point_settings["outer_field_index"] = outer_field[0]
                        self._ppms_field_scan_worker(block_sequence, rate, stable, approach, point_settings)
        except PPMSLockinRangeError:
            preserve_range_output = True
            raise
        except PPMSScanCancelled:
            raise
        except PPMSMotorScanError:
            failed = True
            raise
        except PPMSFieldScanError as error:
            failed = True
            raise self._ppms_field_error_after_zero(error, rate, approach)
        except BaseException:
            failed = True
            self.thread_log("Non-field scan error: field setpoint kept, not zeroed")
            raise
        finally:
            self.ppms_scan_context = {}
            if not preserve_range_output and (failed or self.closing or (stop.is_set() and (not self._ppms_preserve_stopped_scan()))):
                if (stop.is_set() or self.closing) and (not failed) and (settings.get('scan_mode') in PPMS_FIELD_COMMANDING_MODES):
                    self._ppms_zero_field_after_interrupted_scan(rate, approach)
                self._stop_ppms_transport_source(read_back=True)
    def _ppms_frequency_scan_block(self, sequence, rate, stable, approach, settings):
        stop = self.ppms_field_scan_stop_event
        frequencies = tuple(float(value) for value in settings["frequencies"])
        currents = tuple(settings.get("currents", (settings["current_rms_ma"],)))
        sweep = settings.get("scan_mode") == "Frequency+Current"
        sweep_peak = max(currents) * math.sqrt(2.) * 1e-3 if sweep else None
        for index, frequency in enumerate(frequencies, 1):
            for position, current in enumerate(currents):
                if stop.is_set() or self.closing:
                    return
                point_settings = dict(settings, current_rms_ma=current, frequency_hz=frequency,
                                      peak_a=math.sqrt(2.) * current * 1e-3,
                                      reference_timeout_s=NONLOCAL_IV6221_REFERENCE_WAIT_S,
                                      frequency_readback_tol_hz=PPMS_FREQUENCY_READBACK_TOL_HZ)
                if sweep:
                    point_settings["continuous_sweep_peak_a"] = sweep_peak
                if position == 0:
                    self._ppms_set_up_frequency(point_settings, index, len(frequencies))
                    self.ppms_source_fresh = True
                    point_settings["frequency_settle_s"] = NONLOCAL_FSCAN_SETTLE_PERIODS / frequency
                else:
                    self._update_ppms_transport_current(point_settings)
                point_settings["keep_source_between_current_points"] = True
                self._ppms_field_scan_worker(sequence, rate, stable, approach, point_settings)
    def _ppms_set_up_frequency(self, settings, index, count):
        stop = self.ppms_field_scan_stop_event
        frequency = float(settings["frequency_hz"])
        if stop.is_set() or self.closing:
            raise PPMSScanCancelled("Scan stopped; the next frequency was not set up")
        self.post_ui(lambda: self.ppms_field_scan_status_var.set(f'Frequency {index}/{count}: {frequency:g} Hz; 6221 off, setting up again…'))
        source = self.ppms_instrument_controllers["6221"]
        with source.lock:
            previous_hz = self._ppms_query_numbers("6221", "SOUR:WAVE:FREQ?", 1)[0]
            source.safe_output_off()
            state = self._ppms_query_numbers("6221", "OUTP?", 1)[0]
        if state != 0:
            raise RuntimeError(f'6221 output still ON (OUTP?={state:g}) before setting up {frequency:g} Hz; scan stopped')
        self.post_ui(lambda: self.ppms_transport_status_var.set(f'Transport acquisition: 6221 OFF (read back); setting up {frequency:g} Hz'))
        self._configure_ppms_transport_scan(settings)
        self._ppms_wait_zurich_reference(frequency, float(settings.get('reference_timeout_s', PPMS_TRANSPORT_REFERENCE_TIMEOUT_S)), previous_hz)
    def _ppms_wait_zurich_reference(self, frequency, timeout_s, previous_hz=None):
        return None
    def _average_ppms_transport_sample(self, frequency, lockins, duration):
        start = call_start = time.monotonic()
        range_limit_s = PPMS_SR830_RANGE_LIMIT_S
        count, sums, last = 0, {}, None
        overloaded_samples = 0
        first_reading = True
        trace = self.__dict__.get('ppms_sample_trace')
        if trace is not None:
            trace.clear()
        while True:
            if self.ppms_field_scan_stop_event.is_set() or self.closing:
                raise PPMSScanCancelled("Acquisition aborted; the unfinished point is not saved")
            sample = self._read_ppms_transport_sample(frequency, lockins, first_reading)
            first_reading = False
            if self.ppms_field_scan_stop_event.is_set() or self.closing:
                raise PPMSScanCancelled("Acquisition aborted; the unfinished point is not saved")
            settling = float(sample.get("range_settle_s", 0.))
            if settling:
                range_limit_s = max(range_limit_s, PPMS_SR830_RANGE_LIMIT_STEPS * settling)
                if time.monotonic() + settling > call_start + range_limit_s:
                    raise PPMSLockinRangeError(f'SR830 autorange did not settle within {range_limit_s:.0f} s; current point not saved')
                if self.ppms_field_scan_stop_event.wait(settling):
                    raise PPMSScanCancelled("Autorange wait stopped; current point not saved")
                start = time.monotonic()
                count, sums, overloaded_samples = 0, {}, 0
                if trace is not None:
                    trace.clear()
                continue
            for name, value in sample.items():
                if name.endswith("_reference_valid") and not value:
                    raise RuntimeError("SR830 reference unlocked or frequency mismatch during acquisition; acquisition stopped")
            if any(name.endswith("_overload") and value for name, value in sample.items()):
                overloaded_samples += 1
                last = dict(sample)
            else:
                for name, value in sample.items():
                    if name.endswith(("_x_v", "_y_v", "_frequency_hz")):
                        sums[name] = sums.get(name, 0.) + float(value)
                count += 1
                last = dict(sample)
                if trace is not None:
                    trace.append((time.monotonic(), {name: float(value) for name, value in sample.items() if name.endswith(('_x_v', '_y_v'))}))
            remaining = duration - (time.monotonic() - start)
            if remaining <= 0:
                if count == 0:
                    raise PPMSLockinRangeError("Every SR830 reading in the acquisition window was overloaded; current point not saved")
                break
            if self.ppms_field_scan_stop_event.wait(min(.05, remaining)):
                raise PPMSScanCancelled("Acquisition aborted; the unfinished point is not saved")
        for name, value in sums.items():
            last[name] = value / count
        for name in sums:
            if name.endswith("_x_v"):
                prefix = name[:-4]
                x, y = last[name], last[prefix + "_y_v"]
                last[prefix + "_r_v"] = math.hypot(x, y)
                last[prefix + "_theta_deg"] = math.degrees(math.atan2(y, x))
        last.update(acquisition_s=duration, acquisition_elapsed_s=time.monotonic() - start, sample_count=count, overloaded_sample_count=overloaded_samples)
        return last
    @staticmethod
    def _ppms_first_point_settled(previous, current) -> bool:
        scales = NONLOCAL_SR830_FULL_SCALE_V
        checked = False
        for name, value in current.items():
            if not name.endswith("_x_v") or name not in previous:
                continue
            prefix = name[:-4]
            try:
                x0, y0 = float(previous[name]), float(previous[prefix + "_y_v"])
                x1, y1 = float(value), float(current[prefix + "_y_v"])
            except (KeyError, TypeError, ValueError):
                continue
            if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                return False
            floor = 0.0
            index = current.get(prefix + "_sensitivity_index")
            if isinstance(index, int) and 0 <= index < len(scales):
                floor = PPMS_FIRST_POINT_FLOOR_FRACTION * scales[index]
            elif prefix.startswith("zurich"):
                try:
                    input_range = float(current.get("zurich_input_range_v", ""))
                except (TypeError, ValueError):
                    input_range = math.nan
                if math.isfinite(input_range) and input_range > 0:
                    floor = PPMS_FIRST_POINT_FLOOR_FRACTION * input_range
            tolerance = max(PPMS_FIRST_POINT_TOLERANCE_FRACTION * math.hypot(x1, y1), floor)
            checked = True
            if abs(x1 - x0) > tolerance or abs(y1 - y0) > tolerance:
                return False
        return checked
    def _acquire_ppms_first_point(self, frequency, lockins, acquisition_s):
        self._ppms_clear_sr830_range_floor()
        previous = None
        sample = None
        for window in range(1, PPMS_FIRST_POINT_MAX_WINDOWS + 1):
            self.post_ui(lambda w=window: self.ppms_field_scan_status_var.set(f'First point: settling, window {w}/{PPMS_FIRST_POINT_MAX_WINDOWS} of {acquisition_s:g} s…'))
            sample = self._average_ppms_transport_sample(frequency, lockins, acquisition_s)
            if previous is not None and self._ppms_first_point_settled(previous, sample):
                return sample, window
            previous = sample
        self.thread_log(f'PPMS first point did not settle within {PPMS_FIRST_POINT_MAX_WINDOWS} windows; keeping the last one')
        return sample, PPMS_FIRST_POINT_MAX_WINDOWS
    def _ppms_field_scan_worker(self, sequence, rate_oe_s: float, stable_s: float, approach_code: int, transport_settings: dict[str, object]) -> None:
        failed = False
        preserve_range_output = False
        last_field = None
        command_field = transport_settings.get("scan_mode", "Field") in ("Field", "Field+Temperature")
        check_field = transport_settings.get("scan_mode", "Field") in PPMS_FIELD_COMMANDING_MODES
        if self.ppms_last_readback is not None:
            try:
                last_field = float(self.ppms_last_readback["field_oe"])
            except (KeyError, TypeError, ValueError):
                last_field = None
        try:
            if not (transport_settings.get('keep_source_between_angles', False) or transport_settings.get('keep_source_between_current_points', False)):
                self._configure_ppms_transport_scan(transport_settings)
                self.ppms_source_fresh = True
            for completed, (loop_index, point_index, points_per_loop, target) in enumerate(sequence, start=1):
                if self.ppms_field_scan_stop_event.is_set():
                    break
                if command_field:
                    try:
                        result = self.ppms_environment_controller.set_field(target, rate_oe_s, approach_code, 1)
                    except Exception as error:
                        raise PPMSFieldScanError(f"Field set failed: {error}") from error
                else:
                    result = self._read_ppms_live()
                distance = abs(target - last_field) if last_field is not None else abs(target)
                timeout_s = distance / rate_oe_s + 180.0
                deadline = time.monotonic() + timeout_s
                while check_field and not self._ppms_field_is_ready(result, target, driven=True):
                    if self.ppms_field_scan_stop_event.wait(PPMS_SCAN_POLL_S):
                        break
                    if time.monotonic() >= deadline:
                        raise PPMSFieldScanError(f"PPMS timed out waiting for {target:+g} Oe to settle; current {float(result['field_oe']):+.7g} Oe / {result['field_status']}")
                    result = self._read_ppms_live()
                    self.post_ui(lambda data=dict(result): self._apply_ppms_environment_readback(data))
                if self.ppms_field_scan_stop_event.is_set():
                    break
                settle_s, slowest_tc_s = self._ppms_point_settle(stable_s, tuple(transport_settings['active_lockins']))
                periods_s = float(transport_settings.get("frequency_settle_s", 0.0))
                if periods_s > settle_s:
                    settle_s = periods_s
                    periods_text = (
                        f"this frequency's first reading waits {ppms_settle_status_text(periods_s)} s: "
                        f"{NONLOCAL_FSCAN_SETTLE_PERIODS:g} periods of "
                        f"{float(transport_settings['frequency_hz']):g} Hz")
                    self.thread_log(f"{getattr(type(self), 'ppms_page_name', 'PPMS')} scan: {periods_text}")
                    self.post_ui(lambda text=periods_text: self.ppms_field_scan_status_var.set(text[0].upper() + text[1:]))
                if settle_s > 0 and self.ppms_field_scan_stop_event.wait(settle_s):
                    break
                result = self._read_ppms_live()
                if check_field and not self._ppms_field_is_ready(result, target, driven=True):
                    raise PPMSFieldScanError("Field drifted from the stable target before measurement; acquisition stopped")
                expected_t = getattr(self, "ppms_scan_context", {}).get("target_temperature_k", "")
                if expected_t != "" and abs(float(result["temperature_k"])-expected_t) > .1:
                    raise RuntimeError("Temperature drifted more than 0.1 K from the target during the field scan; acquisition stopped")
                elapsed = time.monotonic() - self.ppms_field_scan_started_at
                row = {
                    **getattr(self, "ppms_scan_context", {}),
                    "elapsed_s": elapsed,
                    "loop": loop_index,
                    "point": point_index,
                    "points_per_loop": points_per_loop,
                    "harmonic_pass": transport_settings.get("harmonic_pass", 1),
                    "target_field_oe": target,
                    "measured_field_oe": float(result["field_oe"]),
                    "field_status": str(result["field_status"]),
                    "temperature_k": float(result["temperature_k"]),
                    "temperature_status": str(result["temperature_status"]),
                    "rate_oe_s": rate_oe_s,
                    "stable_s": stable_s,
                }
                acquisition_s = float(transport_settings["acquisition_s"])
                source_fresh = bool(self.__dict__.pop("ppms_source_fresh", False))
                if completed == 1 and (command_field or source_fresh):
                    transport_sample, windows = self._acquire_ppms_first_point(float(transport_settings['frequency_hz']), tuple(transport_settings['active_lockins']), acquisition_s)
                    row["warmup_s"] = acquisition_s * (windows - 1)
                    row["first_point_windows"] = windows
                else:
                    row["warmup_s"] = 0.0
                    row["first_point_windows"] = 1
                    transport_sample = self._average_ppms_transport_sample(float(transport_settings['frequency_hz']), tuple(transport_settings['active_lockins']), acquisition_s)
                result = self._read_ppms_live()
                if check_field and not self._ppms_field_is_ready(result, target, driven=True):
                    raise PPMSFieldScanError("Field drifted from the target at the end of acquisition; current point not saved")
                if expected_t != "" and abs(float(result["temperature_k"])-expected_t) > .1:
                    raise RuntimeError("Temperature drifted from the target at the end of acquisition; current point not saved")
                if self.ppms_field_scan_stop_event.is_set() or self.closing:
                    raise PPMSScanCancelled("Acquisition stopped; current point not saved")
                elapsed = time.monotonic() - self.ppms_field_scan_started_at
                row.update(elapsed_s=elapsed, measured_field_oe=float(result["field_oe"]),
                           field_status=str(result["field_status"]), temperature_k=float(result["temperature_k"]),
                           temperature_status=str(result["temperature_status"]))
                row.update({
                    "6221_current_rms_ma": float(
                        transport_settings["current_rms_ma"]
                    ),
                    "6221_frequency_hz": float(
                        transport_settings["frequency_hz"]
                    ),
                    "6221_compliance_v": float(
                        transport_settings["compliance_v"]
                    ),
                    **transport_sample,
                })
                if getattr(self.ppms_environment_controller, "backend", "") == PPMS_QDINSTRUMENT_BACKEND:
                    try:
                        motor_data = PPMSMotorDebug(self.ppms_environment_controller.active).read()
                    except Exception as error:
                        raise PPMSMotorScanError(f"Angle readback failed: {error}; field kept, not zeroed") from error
                    row["measured_angle_deg"] = motor_data["angle_deg"]
                    expected_a = row.get("target_angle_deg", "")
                    if expected_a != "" and (motor_data["status"] not in ("AtTarget", "AtIndexSwitch") or abs(motor_data["angle_deg"]-expected_a) > .06):
                        raise PPMSMotorScanError("Motor drifted from the target or did not stop during acquisition; acquisition stopped; field kept, not zeroed")
                    result["motor_display"] = f"{motor_data['angle_deg']:.4f}° / {motor_data['status']}"
                else:
                    row["measured_angle_deg"] = ""
                result["lockin_reference"] = {}
                for key in tuple(transport_settings["active_lockins"]):
                    address = self._ppms_sr830_address(self.ppms_instrument_controllers[key].resource_name)
                    harmonic_key = PPMS_SR830_HARMONIC_SETTING_KEYS[key]
                    row[f'sr830_{address}_harmonic'] = int(transport_settings[harmonic_key])
                    result["lockin_reference"][key] = (f"{transport_sample[f'sr830_{address}_frequency_hz']:.5g} Hz / "
                        + ("LOCK reference locked" if transport_sample[f"sr830_{address}_reference_valid"] else "WARNING reference unlocked or frequency mismatch"))
                verified_codes = getattr(self, "ppms_sr830_time_constant_codes", {})
                for key in tuple(transport_settings["active_lockins"]):
                    address = self._ppms_sr830_address(self.ppms_instrument_controllers[key].resource_name)
                    code = verified_codes.get(key)
                    row[f'sr830_{address}_time_constant_s'] = SR830_TIME_CONSTANTS_S[code] if code is not None else ''
                row["effective_settle_s"] = settle_s
                row["slowest_lockin_tc_s"] = slowest_tc_s
                if "outer_field_index" in transport_settings:
                    row["outer_field_index"] = transport_settings["outer_field_index"]
                if self.ppms_field_scan_rows:
                    row = dict.fromkeys(self.ppms_field_scan_rows[0], "") | row
                self._ppms_append_row(row)
                self.ppms_field_scan_rows.append(row)
                axis_column = PPMS_PLOT_AXIS_COLUMNS.get(PPMS_SCAN_PLOT_AXES.get(transport_settings.get('scan_mode', 'Field'), 'field'), 'measured_field_oe')
                self._ppms_log("point_saved", index=len(self.ppms_field_scan_rows),
                               harmonic_pass=row.get("harmonic_pass", 1), x_column=axis_column,
                               x=row.get(axis_column, ""), target_field_oe=target,
                               measured_field_oe=row.get("measured_field_oe", ""),
                               temperature_k=row.get("temperature_k", ""),
                               current_rms_ma=row.get("6221_current_rms_ma", ""),
                               frequency_hz=row.get("6221_frequency_hz", ""))
                self._ppms_log_check()
                last_field = float(result["field_oe"])
                mean_point_s = elapsed / max(1, len(self.ppms_field_scan_rows))
                remaining_s = mean_point_s * max(0, self.ppms_field_scan_total - len(self.ppms_field_scan_rows))
                def update(
                    data=dict(result),
                    progress=len(self.ppms_field_scan_rows),
                    remaining=remaining_s,
                    loop_value=loop_index,
                    point_value=point_index,
                    points_value=points_per_loop,
                    sample=dict(transport_sample),
                ) -> None:
                    self._apply_ppms_environment_readback(data)
                    self.ppms_field_scan_progress.configure(value=progress)
                    self.ppms_field_scan_eta_var.set(f'Remaining: {self._format_scan_duration(remaining)}')
                    self.ppms_field_scan_status_var.set(f'CSV saved {progress}/{self.ppms_field_scan_total}; remaining {self._format_scan_duration(remaining)}')
                    addresses = sorted({match.group(1) for name in sample if (match := re.fullmatch('sr830_(8|9|10)_r_v', str(name)))}, key=int)
                    warning = ""
                    if not all((bool(sample[f'sr830_{address}_reference_valid']) for address in addresses)):
                        warning = "; reference WARNING"
                    self.ppms_transport_status_var.set('; '.join((f'#{address} ' + ('LOCK' if sample[f'sr830_{address}_reference_valid'] else 'WARNING') for address in addresses)) + warning)
                    self._redraw_ppms_transport_plot()
                self.post_ui(update)
            if check_field and self.ppms_field_scan_stop_event.is_set() and (not self._ppms_preserve_stopped_scan()):
                self._ppms_zero_field_after_interrupted_scan(rate_oe_s, approach_code)
        except PPMSLockinRangeError:
            preserve_range_output = True
            raise
        except PPMSScanCancelled:
            raise
        except PPMSMotorScanError:
            failed = True
            raise
        except PPMSFieldScanError as error:
            failed = True
            raise self._ppms_field_error_after_zero(error, rate_oe_s, approach_code)
        except BaseException:
            failed = True
            self.thread_log("Non-field scan error: field setpoint kept, not zeroed")
            raise
        finally:
            try:
                if not preserve_range_output and (failed or self.closing or (self.ppms_field_scan_stop_event.is_set() and (not self._ppms_preserve_stopped_scan()))):
                    self._stop_ppms_transport_source(read_back=True)
            except BaseException as error:
                self.thread_log(f'PPMS: failed to turn the 6221 off at the end of the transport scan: {error}')
                raise
    def _ppms_zero_field_after_interrupted_scan(self, rate_oe_s: float, approach_code: int) -> bool:
        if self.__dict__.get("ppms_scan_field_zero_accepted", False):
            return True
        zero_rate = 100.0 if self.ppms_field_scan_stop_event.is_set() else rate_oe_s
        self.post_ui(lambda: self._end_ppms_persistent_follow_up("The scan clean-up"))
        try:
            self.ppms_environment_controller.set_field(0.0, zero_rate, approach_code, 1)
        except BaseException as error:
            self.thread_log(f"PPMS: zeroing after the interrupted field scan failed: {error}")
            self.ppms_scan_field_note = PPMS_FIELD_NOT_ZEROED_TEXT
            return False
        self.ppms_scan_field_zero_accepted = True
        self.ppms_scan_field_note = "field ramping to 0 Oe"
        return True
    def _ppms_field_error_after_zero(self, error, rate_oe_s, approach_code):
        if getattr(error, "ppms_field_zero_tried", False):
            return error
        if not self._ppms_zero_field_after_interrupted_scan(rate_oe_s, approach_code):
            failure = PPMSFieldScanError(f"{error}; {PPMS_FIELD_NOT_ZEROED_TEXT}")
            failure.__cause__ = error
            error = failure
        error.ppms_field_zero_tried = True
        return error
    def _ppms_preserve_stopped_scan(self):
        return getattr(self, 'ppms_scan_preserve_on_stop', False) and self.ppms_field_scan_stop_event.is_set() and (not self.closing)
    def stop_ppms_field_scan(self) -> None:
        if not self.ppms_field_scan_running:
            return
        self.ppms_scan_preserve_on_stop = True
        if not self.__dict__.get("ppms_scan_stop_button"):
            self.ppms_scan_stop_button = "Stop"
        self.ppms_field_scan_stop_event.set()
        self._ppms_log("stop_requested", button="Stop")
        mode = getattr(self, "ppms_active_scan_mode", "")
        self.ppms_field_scan_status_var.set(f'Stopping the {mode} scan; completed CSV points kept; the 6221 is zeroed next; temperature, field and angle kept…')
        self.ppms_field_scan_stop_button.configure(state="disabled")
    def _append_ppms_live_csv(self, row):
        path = getattr(self, "ppms_live_csv_path", None)
        if path is None:
            raise RuntimeError("No live CSV file selected; acquisition stopped")
        first = self.ppms_live_csv_fields is None
        fields = list(row) if first else self.ppms_live_csv_fields
        if set(row) != set(fields):
            raise RuntimeError("Live CSV channels changed; appending stopped to avoid misaligned columns")
        with path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if first:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        self.ppms_live_csv_fields = fields
    def save_ppms_field_scan_csv(self) -> None:
        if not self.ppms_field_scan_rows:
            return
        folder = self.__dict__.get("ppms_run_folder")
        where = {"initialdir": str(folder)} if folder is not None and Path(folder).is_dir() else {}
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save PPMS scan CSV as",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
            initialfile=time.strftime("PPMS_field_scan_%Y%m%d_%H%M%S.csv"),
            **where,
        )
        if not selected:
            return
        fieldnames = list(self.ppms_field_scan_rows[0].keys())
        try:
            with open(selected, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.ppms_field_scan_rows)
        except OSError as error:
            messagebox.showerror("Save PPMS CSV", str(error), parent=self.root)
            return
        self.ppms_field_scan_status_var.set(f"Saved: {selected}")
    def _set_ppms_device_ui(self, key: str, connected: bool) -> None:
        self._set_connection_indicator(f"ppms_{key}", connected)
        if not connected and key in PPMS_SR830_KEYS:
            self._set_connection_indicator(f"ppms_ref_{key}", False)
        scan_busy = bool(self.ppms_field_scan_running)
        self.ppms_device_connect_buttons[key].configure(state='disabled' if connected or scan_busy else 'normal')
        self.ppms_device_disconnect_buttons[key].configure(state='normal' if connected and (not scan_busy) else 'disabled')
        resource_widget = self.ppms_device_resource_widgets.get(key)
        if resource_widget is not None:
            if connected or scan_busy:
                resource_widget.configure(state="disabled")
            elif key in PPMS_SR830_KEYS and isinstance(resource_widget, ttk.Combobox):
                resource_widget.configure(state="readonly")
            else:
                resource_widget.configure(state="normal")
    def _initialize_ppms_6221(self) -> None:
        controller = self.ppms_instrument_controllers["6221"]
        with controller.lock:
            if self._ppms_query_numbers("6221", K6221_OUTPUT_LOW_QUERY, 1)[0] == 0:
                return
            controller.instrument.write("*CLS")
            self._ppms_write_sr830_checked("6221", K6221_OUTPUT_LOW_FLOATING)
            state = self._ppms_query_numbers("6221", K6221_OUTPUT_LOW_QUERY, 1)[0]
        if state != 0:
            raise RuntimeError(f'6221 output low stayed earth-grounded (OUTP:LTE? returned {state:g}); set TRIAX -> OUTPUT LOW -> FLOATING on the front panel')
    def _initialize_ppms_sr830(self, key: str, harmonic: int, time_constant_code: int = 10) -> None:
        if harmonic not in (1, 2, 3):
            raise ValueError("SR830 harmonic must be 1, 2 or 3")
        if time_constant_code not in STFMR_LOCKIN_SR830_TIME_CONSTANTS.values():
            raise ValueError(f"SR830 time constant code {time_constant_code!r} is not an 'SR830 TC' choice")
        time_constant_code = int(time_constant_code)
        controller = self.ppms_instrument_controllers[key]
        configuration = (
            ("FMOD", 0), ("RSLP", PPMS_SR830_REFERENCE_SLOPE), ("HARM", harmonic),
            ("ISRC", 1), ("IGND", 0), ("ICPL", 0), ("ILIN", 0), ("SYNC", 0),
            ("SENS", 9), ("OFLT", time_constant_code), ("OFSL", 1), ("RMOD", 1), ("PHAS", 0),
        )
        verified = self.__dict__.setdefault("ppms_sr830_time_constant_codes", {})
        verified.pop(key, None)
        with controller.lock:
            controller.instrument.write("*CLS")
            for command, value in configuration:
                self._ppms_write_sr830_checked(key, f"{command} {value}")
            for command in SR830_DISPLAY_XY_COMMANDS:
                self._ppms_write_sr830_checked(key, command)
            for command, expected in configuration:
                actual = self._ppms_query_numbers(key, f"{command}?", 1)[0]
                if actual != expected:
                    raise RuntimeError(f"SR830 initialisation readback mismatch: {command}={actual}, expected {expected}")
        verified[key] = time_constant_code
        getattr(self, "ppms_sr830_range_states", {}).pop(key, None)
        getattr(self, "ppms_idle_range_ready_at", {}).pop(key, None)
    def _connect_and_initialize_ppms_device(self, key, resource, harmonic=None, time_constant_code=10):
        controller = self.ppms_instrument_controllers[key]
        identity = controller.connect(resource)
        try:
            if key in PPMS_SR830_KEYS:
                self._initialize_ppms_sr830(key, harmonic, time_constant_code)
            elif key == "6221":
                self._initialize_ppms_6221()
        except BaseException:
            controller.disconnect()
            raise
        return identity
    def connect_ppms_device(self, key: str) -> None:
        if self.ppms_field_scan_running:
            return
        controller = self.ppms_instrument_controllers[key]
        resource = self.ppms_resource_vars[key].get().strip()
        harmonic = None
        time_constant_code = 10
        if key in PPMS_SR830_KEYS:
            try:
                address = self._ppms_sr830_address(resource)
                harmonic = self._ppms_harmonic_value(key, 0)
                if harmonic not in (1, 2, 3):
                    raise ValueError("SR830 harmonic must be 1, 2 or 3")
            except ValueError as error:
                messagebox.showerror('PPMS SR830 address', str(error), parent=self.root)
                return
            try:
                time_constant_code = self._ppms_sr830_time_constant_code()
            except ValueError as error:
                messagebox.showerror("PPMS SR830 TC", str(error), parent=self.root)
                return
            for other_key in self._ppms_sr830_keys():
                if other_key == key:
                    continue
                other = self.ppms_instrument_controllers[other_key]
                if not other.connected:
                    continue
                try:
                    other_address = self._ppms_sr830_address(other.resource_name)
                except ValueError:
                    other_address = None
                if other_address == address:
                    messagebox.showerror(
                        "PPMS SR830 address",
                        f"GPIB address {address} is already connected by {PPMS_SR830_LABELS[other_key]}; "
                        "please choose another address among 8, 9 and 10.",
                        parent=self.root,
                    )
                    return
        self.ppms_device_connect_buttons[key].configure(state="disabled")
        resource_widget = self.ppms_device_resource_widgets.get(key)
        if resource_widget is not None:
            resource_widget.configure(state="disabled")
        self.ppms_device_connection_vars[key].set("Connecting and initialising…" if harmonic is not None else "Connecting…")
        future = self.ppms_executor.submit(self._connect_and_initialize_ppms_device, key, resource, harmonic, time_constant_code)
        if harmonic is not None:
            self.__dict__.setdefault("ppms_sr830_connecting", set()).add(key)
            self._update_ppms_sr830_tc_state()
        def finished() -> None:
            connecting = self.__dict__.get("ppms_sr830_connecting")
            if connecting is not None:
                connecting.discard(key)
                self._update_ppms_sr830_tc_state()
            try:
                identity = future.result()
            except BaseException as error:
                self.ppms_device_connection_vars[key].set(f'Failed: {short_status_error(error, 80)}')
                self._set_ppms_device_ui(key, False)
                if not self.closing:
                    messagebox.showerror("PPMS external instruments", str(error), parent=self.root)
                return
            self.thread_log(f"PPMS {key} connected: {identity}")
            self.ppms_device_connection_vars[key].set("Connected")
            self._set_ppms_device_ui(key, True)
            self._redraw_ppms_transport_plot()
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def disconnect_ppms_device(self, key: str) -> None:
        if self.ppms_field_scan_running:
            return
        controller = self.ppms_instrument_controllers[key]
        self.ppms_device_disconnect_buttons[key].configure(state="disabled")
        self.ppms_device_connection_vars[key].set("Disconnecting…")
        future = self.ppms_executor.submit(controller.disconnect)
        def finished() -> None:
            try:
                future.result()
            except BaseException as error:
                if not self.closing:
                    messagebox.showerror("PPMS external instruments", str(error), parent=self.root)
            self.ppms_device_connection_vars[key].set("Not connected")
            self._set_ppms_device_ui(key, False)
            self._redraw_ppms_transport_plot()
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def ppms_sources_off(self) -> None:
        running_scan = self.__dict__.get("ppms_run_recorder") is not None
        if running_scan:
            self.ppms_scan_stop_button = "I off"
        self.ppms_idle_autorange_enabled = False
        self.ppms_scan_preserve_on_stop = False
        self.ppms_field_scan_stop_event.set()
        self.ppms_connection_var.set("Turning outputs off and confirming by readback…")
        def work() -> str:
            errors = []
            confirmed = []
            for key in ("6221", "2400"):
                try:
                    controller = self.ppms_instrument_controllers[key]
                    with controller.lock:
                        if controller.instrument is None:
                            continue
                        controller.safe_output_off()
                        state = self._ppms_query_numbers(key, "OUTP?", 1)[0]
                        if state != 0:
                            raise RuntimeError(f"{key} output is still ON (OUTP?={state:g})")
                        confirmed.append(key)
                except BaseException as error:
                    errors.append(f"{key}: {error}")
            if errors:
                raise RuntimeError(" | ".join(errors))
            return ' / '.join(confirmed) + ' output OFF confirmed by readback' if confirmed else 'No source meter connected; output state cannot be confirmed'
        future = self.ppms_executor.submit(work)
        if running_scan:
            self._ppms_log("stop_requested", button="I off")
        def finished() -> None:
            try:
                status = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(f"Output off not confirmed: {short_status_error(error, 120)}")
                if hasattr(self, "ppms_current_live_var"):
                    self.ppms_current_live_var.set("Unknown / output off not confirmed")
                if not self.closing:
                    text = str(error)
                    def show() -> None:
                        if not self.closing:
                            messagebox.showerror("PPMS source off", text, parent=self.root)
                    self._ppms_show_later(show)
                return
            self.ppms_connection_var.set(status)
            if hasattr(self, "ppms_current_live_var"):
                self.ppms_current_live_var.set('OFF / 0 mA' if status.startswith('6221') else 'Not connected')
        future.add_done_callback(lambda _future: self.post_ui(finished))
    def safe_close_ppms(self) -> None:
        for name in ("rte_panel", "ppms_panel"):
            panel = self.__dict__.get(name)
            if panel is None:
                continue
            try:
                panel.safe_close_ppms()
            except BaseException as error:
                try:
                    self.append_log(f"{name} close warning: {error}")
                except BaseException:
                    pass
        if self.ppms_closed:
            return
        self.ppms_closed = True
        scan_was_running = self.ppms_field_scan_running
        self.ppms_field_scan_stop_event.set()
        recorder = self.__dict__.get("ppms_run_recorder")
        if recorder is not None:
            recorder.event("stop_requested", button="the program closing")
        if self.ppms_poll_after_id is not None:
            try:
                self.root.after_cancel(self.ppms_poll_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.ppms_poll_after_id = None
        def cleanup(after_worker: bool) -> None:
            errors = []
            if (scan_was_running and not after_worker
                    and getattr(self, "ppms_active_scan_mode", "") in PPMS_FIELD_COMMANDING_MODES
                    and not self.__dict__.get("ppms_scan_field_zero_accepted", False)
                    and self.ppms_environment_controller.connected):
                try:
                    rate = float(self.ppms_field_rate_var.get())
                    _target, rate = validate_ppms_field_command(0.0, rate)
                    approach = self._ppms_field_approach_code(self.ppms_field_approach_var.get())
                    self.ppms_environment_controller.set_field(0.0, rate, approach, 1)
                except BaseException as error:
                    errors.append(f"PPMS: zeroing the field before close failed: {error}")
            for controller in self.ppms_instrument_controllers.values():
                try:
                    controller.disconnect()
                except BaseException as error:
                    errors.append(str(error))
            try:
                self.ppms_environment_controller.disconnect()
            except BaseException as error:
                errors.append(str(error))
            zurich_executor = self.__dict__.get("zurich_executor")
            if zurich_executor is not None:
                try:
                    zurich_executor.shutdown(wait=True, cancel_futures=True)
                except BaseException as error:
                    errors.append(str(error))
            zurich_controller = self.__dict__.get("zurich_controller")
            if zurich_controller is not None:
                try:
                    zurich_controller.disconnect_mfli()
                except BaseException as error:
                    errors.append(str(error))
            for error in errors:
                try:
                    self.append_log(f"PPMS safe-close warning: {error}")
                except BaseException:
                    pass
        deferred = scan_was_running and self._ppms_close_handoff().defer(lambda: cleanup(True))
        self.ppms_executor.shutdown(wait=False, cancel_futures=not deferred)
        if not deferred:
            cleanup(False)
    def _ppms_close_handoff(self) -> ScanCloseHandoff:
        return self.__dict__.setdefault("ppms_close_handoff_state", ScanCloseHandoff())
    def safe_close_hardware(self) -> None:
        try:
            self.safe_close_ppms()
        except BaseException as error:
            try:
                self.append_log(f"PPMS safe close incomplete: {error}")
            except BaseException:
                pass
    def post_ui(self, callback: Callable[[], None]) -> None:
        self.ui_queue.put(callback)
    def thread_log(self, message: str) -> None:
        self.post_ui(lambda text=message: self.append_log(text))
    def drain_ui_queue(self) -> None:
        deadline = time.perf_counter() + UI_QUEUE_TIME_BUDGET_S
        processed = 0
        while processed < UI_QUEUE_MAX_CALLBACKS_PER_TICK:
            if processed and time.perf_counter() >= deadline:
                break
            try:
                if not self.root.winfo_exists():
                    break
            except tk.TclError:
                break
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except BaseException as error:
                if not self.closing:
                    print(f"Transport UI callback error: {error}", file=sys.stderr)
                    traceback.print_exc()
                    try:
                        self.append_log(f"Internal UI error: {error!r}")
                    except BaseException:
                        pass
            processed += 1
        try:
            if self.root.winfo_exists():
                delay_ms = UI_QUEUE_ACTIVE_DELAY_MS if not self.ui_queue.empty() else UI_QUEUE_IDLE_DELAY_MS
                self.root.after(delay_ms, self.drain_ui_queue)
        except tk.TclError:
            pass
    def toggle_log_refresh(self) -> None:
        self.log_display_paused = not self.log_display_paused
        self.log_pause_button.configure(text="Resume refresh" if self.log_display_paused else "Pause refresh")
        if not self.log_display_paused and self.log_pending_lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "".join(self.log_pending_lines))
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
            self.log_pending_lines.clear()
    def copy_displayed_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_text.get("1.0", "end-1c"))
    def save_communication_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save the full program log (including lines buffered while paused)",
            initialfile=time.strftime("Transport_log_%Y%m%d_%H%M%S.txt"),
            defaultextension=".txt", filetypes=(("Text", "*.txt"),))
        if not path:
            return
        try:
            content = self.log_text.get("1.0", "end-1c") + "".join(self.log_pending_lines)
            with Path(path).open("w", encoding="utf-8-sig") as handle:
                handle.write(content)
        except OSError as error:
            messagebox.showerror("Save log failed", str(error), parent=self.root)
    def append_log(self, message: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.post_ui(lambda text=str(message): self.append_log(text))
            return
        try:
            if not self.root.winfo_exists() or not self.log_text.winfo_exists():
                return
        except (AttributeError, tk.TclError):
            return
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        if getattr(self, "log_display_paused", False):
            self.log_pending_lines.append(line)
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            if not self.closing:
                raise
    def request_close(self) -> None:
        if self.closing:
            return
        confirmed = messagebox.askyesno(
            "Close the PPMS program",
            "Exit the PPMS control program?\n\n"
            "The program will switch off the 6221 output and disconnect the external instruments. If a field scan "
            "is running, it requests field zeroing first; otherwise it disconnects the PPMS client and keeps the "
            "temperature and field setpoints.\n\n"
            "After exiting, still confirm every instrument output and power supply is in a safe state.",
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return
        self.closing = True
        panel = self.__dict__.get("ppms_panel")
        for owner in (self, panel):
            if owner is None:
                continue
            event = owner.__dict__.get("ppms_field_scan_stop_event")
            if event is not None:
                event.set()

        def finish_window_close() -> None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

        def close_everything() -> None:
            try:
                self.safe_close_hardware()
            finally:
                self.file_executor.shutdown(wait=False, cancel_futures=False)
                self.executor.shutdown(wait=False, cancel_futures=True)
                self.post_ui(finish_window_close)
        threading.Thread(target=close_everything, name="ppms-safe-shutdown", daemon=True).start()
class PPMSPanel(CombinedControlApp):
    ppms_page_name = "PPMS"
    def __init__(self, parent, tab):
        self.parent_app = parent
        self.root = parent.root
        self.instrument_connection_indicators = {}
        self._initialize_ppms_state()
        self.ppms_tab = tab
        self.ppms_host_var.set(PPMS_DEFAULT_HOST)
        for key in PPMS_SR830_KEYS:
            self.ppms_lockin_selection[key].set(True)
        self.ppms_transport_harmonic_a_var.set(1)
        self.ppms_transport_harmonic_b_var.set(2)
        self.ppms_transport_harmonic_c_var.set(3)
        self.ppms_transport_current_var.set("1")
        for controller in self.ppms_instrument_controllers.values():
            controller.owner_scope = "PPMS"
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        self.ppms_default_field_range = ("-15000", "15000", "1500")
        self.build_ppms_ui()
        self.zurich_controller = NonLocalACController(self.thread_log)
        self.zurich_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ppms-zurich")
        self.zurich_busy = False
        self.zurich_reading = False
        self.zurich_closed = False
        self.zurich_after_id = None
        self.zurich_resource = tk.StringVar(value=zurich_mfli_label("MF-DEV4414"))
        self.zurich_reference = tk.StringVar(value="Trigger In 1")
        self.zurich_harmonic = tk.StringVar(value="1+2+3")
        self.zurich_timeconstant = tk.StringVar(value=f"{PPMS_ZURICH_TIME_CONSTANT_S:g}")
        self.zurich_expected_frequency = PPMS_TRANSPORT_FREQUENCY_HZ
        self.zurich_status = tk.StringVar(value="Not connected")
        self.zurich_values = tk.StringVar(value="X: -- V    Y: -- V    R: -- V    θ: -- °    Ref: -- Hz")
        row = ttk.Frame(self.ppms_instruments_frame)
        compact = self.__dict__.get('compact_zurich_connection', False)
        row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(3, 0), padx=0 if compact else 2)
        row.columnconfigure(10 if compact else 11, weight=1)
        ttk.Label(row, text="Zurich MFLI", **({'width': 13} if compact else {})).grid(row=0, column=0)
        self.zurich_entries = []
        for column, variable, width in ((1, self.zurich_resource, 12), (5, self.zurich_timeconstant, 6 if compact else 7)):
            if column == 1:
                entry = create_zurich_chooser(row, variable, match_chars=18 if compact else None)
                extra = entry.zurich_shortfall
            else:
                entry = ttk.Entry(row, textvariable=variable, width=width)
                extra = 0
            entry.grid(row=0, column=(9 if column == 5 else 1) if compact else column, padx=(0, extra) if compact else (3, 3 + extra))
            self.zurich_entries.append(entry)
        self.ppms_zurich_reference_label = ttk.Label(row, text="Ref." if compact else "Reference")
        self.ppms_zurich_reference_label.grid(row=0, column=6 if compact else 2, padx=(4, 2))
        self.zurich_reference_combo = ttk.Combobox(row, textvariable=self.zurich_reference, values=tuple(NONLOCAL_MFLI_REFERENCE_INPUTS), state='readonly', width=12)
        self.zurich_reference_combo.grid(row=0, column=7 if compact else 3)
        ttk.Label(row, text="TC (s)").grid(row=0, column=8 if compact else 4, padx=(4, 2))
        zurich_row = 3
        ttk.Label(self.ppms_settings_panel, text="Zurich harmonic").grid(row=zurich_row, column=13, sticky="e", padx=(12, 3))
        self.zurich_range_var = tk.StringVar(value="Autorange ON")
        ttk.Label(self.ppms_settings_panel, textvariable=self.zurich_range_var, width=16).grid(row=zurich_row, column=18, sticky='w', padx=(6, 0))
        self.zurich_harmonic_combo = ttk.Combobox(self.ppms_settings_panel, textvariable=self.zurich_harmonic, values=(1, 2, 3, '1+2', '1+2+3'), state='readonly', width=7)
        self.zurich_harmonic_combo.grid(row=zurich_row, column=14, sticky="ew")
        self.zurich_harmonic_combo.bind('<<ComboboxSelected>>', lambda event: self._apply_ppms2_zurich_harmonic())
        self.ppms_lockin_selection['zurich'] = tk.BooleanVar(value=True)
        self.zurich_select = ttk.Checkbutton(self.ppms_settings_panel, text='Measure', variable=self.ppms_lockin_selection['zurich'], command=self._refresh_ppms2_plot_layout)
        self.zurich_select.grid(row=zurich_row, column=17, padx=3)
        self._create_connection_indicator(self.ppms_settings_panel, "zurich_reference")
        self.zurich_connect_button = ttk.Button(row, text='Connect' if compact else 'Connect / apply settings', command=self.connect_ppms2_zurich, **{'width': 8} if compact else {})
        self.zurich_connect_button.grid(row=0, column=2 if compact else 8, padx=0 if compact else 4)
        self.zurich_disconnect_button = ttk.Button(row, text="Disconnect", command=self.disconnect_ppms2_zurich, state="disabled", width=10)
        self.zurich_disconnect_button.grid(row=0, column=3 if compact else 9)
        self._create_connection_indicator(row, "zurich_connection").grid(row=0, column=4 if compact else 10, padx=3, sticky="w")
        if compact:
            ttk.Label(row, textvariable=self._ppms_compact_status(self.zurich_status), width=13, wraplength=0).grid(row=0, column=5, sticky='w')
        else:
            status_label = ttk.Label(row, width=1, wraplength=0)
            status_label.grid(row=0, column=11, sticky="ew")
            show_trimmed(status_label, self.zurich_status)
        self.zurich_canvases = {}
        self.zurich_plot_frames = {}
        self.ppms_plot_frame.configure(text="SR830-1 / SR830-2 / SR830-3 / Zurich: X, Y, θ vs scan variable")
        for index, channel in enumerate(("x", "y", "theta", "x_2", "y_2", "theta_2", "x_3", "y_3", "theta_3")):
            column = index % 3
            label = "θ" if channel == "theta" else channel.upper()
            frame = ttk.LabelFrame(self.ppms_plot_frame, text=f"Zurich {label} (Demod 1)", padding=2)
            frame.grid(row=3 + index // 3, column=column, sticky="nsew")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            canvas = tk.Canvas(frame, height=PPMS_PLOT_ROW_MIN_HEIGHT_PX, width=300, background="white", highlightthickness=0)
            canvas.ppms_compact_plot = True
            canvas.grid(row=0, column=0, sticky="nsew")
            canvas.bind("<Configure>", lambda event: self._request_plot_redraw())
            self.zurich_canvases[channel] = canvas
            self.zurich_plot_frames[channel] = frame
        self.ppms_harmonic_canvases = {}
        self.ppms_harmonic_plot_frames = {}
        for order in PPMS_HARMONIC_ORDERS:
            for column, channel in enumerate(("x", "y", "theta")):
                frame = ttk.LabelFrame(self.ppms_plot_frame, text=PPMS_HARMONIC_LABELS[order], padding=2)
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)
                canvas = tk.Canvas(frame, height=PPMS_PLOT_ROW_MIN_HEIGHT_PX, width=300, background="white", highlightthickness=0)
                canvas.ppms_compact_plot = True
                canvas.grid(row=0, column=0, sticky="nsew")
                canvas.bind("<Configure>", lambda event: self._request_plot_redraw())
                self.ppms_harmonic_canvases[(order, channel)] = canvas
                self.ppms_harmonic_plot_frames[(order, channel)] = frame
        self._refresh_ppms2_plot_layout()
        self._redraw_ppms_transport_plot()
        self.ppms_poll_after_id = self.root.after(1000, self._ppms_poll_timer)
        self.zurich_after_id = self.root.after(1000, self._poll_ppms2_zurich)
    def __getattr__(self, name):
        if name.startswith(("ppms", "zurich")):
            raise AttributeError(name)
        return getattr(self.parent_app, name)
    def _set_zurich_busy(self, busy):
        self.zurich_busy = busy
        self._set_connection_indicator("zurich_connection", self.zurich_controller.mfli_connected)
        if not self.zurich_controller.mfli_connected:
            self._set_connection_indicator("zurich_reference", False)
        locked = busy or self.ppms_field_scan_running
        self.zurich_select.configure(state='disabled' if locked else 'normal')
        self.zurich_connect_button.configure(state="disabled" if locked else "normal")
        self.zurich_disconnect_button.configure(state="normal" if not locked and self.zurich_controller.mfli_connected else "disabled")
        for entry in self.zurich_entries:
            entry.configure(state="disabled" if locked else widget_idle_state(entry))
        self.zurich_entries[0].configure(state='disabled' if locked or self.zurich_controller.mfli_connected else widget_idle_state(self.zurich_entries[0]))
        for combo in (self.zurich_reference_combo, self.zurich_harmonic_combo):
            combo.configure(state="disabled" if locked else "readonly")
    def _update_ppms_control_ui(self):
        super()._update_ppms_control_ui()
        if hasattr(self, 'zurich_connect_button'):
            self._set_zurich_busy(self.zurich_busy)
    def thread_log(self, message):
        super().thread_log(message)
        self._ppms_log("note", text=str(message))
    def _ppms_set_current_direct(self):
        if not self.ppms_field_scan_running:
            self.zurich_scan_enabled = False
        super()._ppms_set_current_direct()
    def start_ppms_field_scan(self):
        if self.ppms_field_scan_running:
            return
        if self.ppms_lockin_selection['zurich'].get() and self.zurich_busy:
            self.ppms_field_scan_status_var.set("Zurich is connecting; start the scan later")
            return
        self.zurich_scan_enabled = bool(self.ppms_lockin_selection['zurich'].get())
        self.zurich_scan_configured = False
        if self.zurich_scan_enabled:
            try:
                self.zurich_scan_harmonic = str(self.zurich_harmonic.get())
                self.zurich_scan_reference = self.zurich_reference.get()
                self.zurich_scan_tau = float(self.zurich_timeconstant.get())
                if self.zurich_scan_harmonic not in ('1', '2', '3', '1+2', '1+2+3') or not math.isfinite(self.zurich_scan_tau) or self.zurich_scan_tau <= 0:
                    raise ValueError('Please check the Zurich harmonic and time constant')
                problem = self._zurich_needs_md_text(self.zurich_scan_harmonic)
                if problem:
                    raise ValueError(problem)
            except (ValueError, tk.TclError) as error:
                messagebox.showerror('PPMS Zurich parameters', str(error), parent=self.root)
                return
        super().start_ppms_field_scan()
    def _configure_ppms_transport_scan(self, settings):
        if getattr(self, 'zurich_scan_enabled', False) and not getattr(self, 'zurich_scan_configured', False):
            with self.zurich_controller.lock:
                dual = str(self.zurich_scan_harmonic) in ('1+2', '1+2+3')
                problem = self._zurich_needs_md_text(self.zurich_scan_harmonic)
                if problem:
                    raise RuntimeError(problem)
                self.zurich_controller.configure_mfli('AC', 1 if dual else int(self.zurich_scan_harmonic), 'R',
                    reference_source=self.zurich_scan_reference, sample_rate_hz=NONLOCAL_SAMPLE_HZ,
                    input_impedance='10 MΩ', input_scaling=1.0, timeconstant_s=self.zurich_scan_tau,
                    external_reference_demodulator=3 if self.zurich_controller.mfli_has_multi_demod() else None)
                if dual:
                    self.zurich_controller.configure_second_harmonic(self.zurich_scan_tau)
                if self.zurich_scan_harmonic == '1+2+3':
                    self.zurich_controller.configure_third_harmonic(self.zurich_scan_tau)
                self.zurich_controller.disable_unused_ppms_demods(self.zurich_scan_harmonic)
            self.zurich_scan_configured = True
        super()._configure_ppms_transport_scan(settings)
    def _average_ppms_transport_sample(self, frequency, lockins, duration):
        sample = super()._average_ppms_transport_sample(frequency, lockins, duration)
        if getattr(self, 'zurich_scan_enabled', False):
            with self.zurich_controller.lock:
                all_demods = self.zurich_controller.read_mfli_four_demods()
                measured = self._read_ppms2_zurich_ranged(frequency, scanning=True) if all_demods['zurich_demod1_enabled'] else None
                if measured is not None and measured.get('ppms_autorange_actions'):
                    all_demods = self.zurich_controller.read_mfli_four_demods()
            if measured is not None and (not measured['reference_valid'] or measured.get('input_overload')):
                raise RuntimeError(f"Zurich reference unlocked or input overload: {measured['status_text']}; field kept")
            if measured is not None and not all(math.isfinite(float(measured[k])) for k in ('X', 'Y', 'R', 'phase_deg')):
                raise RuntimeError("Zurich readback is not finite; field kept")
            sample.update(all_demods)
            sample.update(zurich_x_v=all_demods['zurich_demod1_x_v'], zurich_y_v=all_demods['zurich_demod1_y_v'],
                zurich_r_v=all_demods['zurich_demod1_r_v'], zurich_theta_deg=all_demods['zurich_demod1_theta_deg'],
                zurich_reference_hz=measured['reference_frequency_hz'] if measured is not None else '',
                zurich_harmonic=self.zurich_scan_harmonic, zurich_sample_count=int(measured is not None))
            sample.update(zurich_input_range_v=measured.get('input_range_v', '') if measured else '', zurich_autorange=measured.get('ppms_autorange_actions', '') if measured else '')
            if measured is not None:
                self.post_ui(lambda s=dict(measured): self._display_zurich_sample(s))
        return sample
    def _ppms_wait_zurich_reference(self, frequency, timeout_s, previous_hz=None):
        frequency = float(frequency)
        self.zurich_expected_frequency = frequency
        if not getattr(self, 'zurich_scan_enabled', False):
            return
        stop = self.ppms_field_scan_stop_event
        try:
            previous = float(previous_hz)
        except (TypeError, ValueError):
            previous = math.nan
        unchanged = not math.isfinite(previous) or abs(previous - frequency) <= PPMS_FREQUENCY_READBACK_TOL_HZ
        deadline = time.monotonic() + float(timeout_s)
        in_a_row = 0
        while True:
            if stop.is_set() or self.closing or getattr(self, 'zurich_closed', False):
                raise PPMSScanCancelled("Stopped as requested while waiting for the Zurich reference")
            with self.zurich_controller.lock:
                sample = self.zurich_controller.read_mfli(frequency)
            try:
                reported = float(sample.get('reference_frequency_hz'))
            except (TypeError, ValueError):
                reported = math.nan
            followed = unchanged or (math.isfinite(reported) and abs(reported - frequency) < abs(reported - previous))
            if sample.get('reference_valid') and followed:
                in_a_row += 1
                if in_a_row >= PPMS_ZURICH_LOCK_READS:
                    self.post_ui(lambda s=dict(sample): self._display_zurich_sample(s))
                    return
            else:
                in_a_row = 0
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Zurich failed to lock the 6221 external reference at {frequency:g} Hz within {float(timeout_s):g} s (it reads {reported:g} Hz); {sample.get('status_text', '')}")
            if stop.wait(0.2):
                raise PPMSScanCancelled("Stopped as requested while waiting for the Zurich reference")
    def _read_ppms2_zurich_ranged(self, frequency, scanning=False):
        actions = []
        for attempt in range(4):
            if self.closing or self.zurich_closed or (scanning and self.ppms_field_scan_stop_event.is_set()):
                raise PPMSScanCancelled("Zurich autorange wait cancelled")
            enabled = (scanning or getattr(self, 'ppms_idle_autorange_enabled', False)) and not getattr(self, 'ppms_6221_zero_output', False)
            initial = enabled and getattr(self, 'ppms_zurich_autorange_pending', False)
            with self.zurich_controller.lock:
                sample = self.zurich_controller.read_stfmr_mfli(frequency, auto_range=enabled, initial=initial, attempts=attempt)
            if initial:
                self.ppms_zurich_autorange_pending = False
            action = sample.get('auto_range', '')
            if not action:
                return dict(sample, ppms_autorange_actions='; '.join(actions))
            actions.append(str(action))
            self.thread_log(f"PPMS Zurich autorange: {action}")
            def show_range(text=str(action)):
                if not self.closing and not self.zurich_closed:
                    self.zurich_status.set(f"Autorange; waiting to settle: {text}")
                    self._set_connection_indicator('zurich_reference', False)
            self.post_ui(show_range)
            tau = float(self.zurich_controller.mfli_config.get('timeconstant_s', PPMS_ZURICH_TIME_CONSTANT_S))
            deadline = time.monotonic() + max(.2, 10. * tau)
            while time.monotonic() < deadline:
                if self.closing or self.zurich_closed or (scanning and self.ppms_field_scan_stop_event.is_set()):
                    raise PPMSScanCancelled("Zurich autorange wait cancelled")
                time.sleep(min(.05, max(0., deadline-time.monotonic())))
        raise RuntimeError("Zurich autorange did not settle; current point not saved; field kept")
    def _display_zurich_sample(self, sample):
        self.zurich_values.set(f"X: {sample['X']:.7g} V    Y: {sample['Y']:.7g} V    R: {sample['R']:.7g} V    θ: {sample['phase_deg']:.4f}°    Ref: {sample['reference_frequency_hz']:.4f} Hz")
        self.zurich_status.set(("LOCK" if sample['reference_valid'] else "WARNING") + "; " + str(sample['status_text']))
        self._set_connection_indicator('zurich_reference', bool(sample['reference_valid']), fault=bool(sample.get('input_overload')))
        if 'zurich_range_var' in self.__dict__:
            self.zurich_range_var.set('Autorange ' + self._format_voltage_range(sample.get('input_range_v')))
    def _ppms2_ordered_plot_rows(self):
        selected = [key for key in (*PPMS_SR830_KEYS, 'zurich') if key in self.ppms_lockin_selection and self.ppms_lockin_selection[key].get()]
        order = [key for key in self.__dict__.get('ppms_selection_order', []) if key in selected]
        order.extend(key for key in selected if key not in order)
        self.ppms_selection_order = order
        mode = str(self.zurich_harmonic.get()) if 'zurich_harmonic' in self.__dict__ else '1'
        rows = []
        for key in order:
            suffixes = ('', '_2', '_3') if key == 'zurich' and mode == '1+2+3' else ('', '_2') if key == 'zurich' and mode == '1+2' else ('',)
            rows.extend((key, suffix) for suffix in suffixes)
        return rows
    def _ppms2_measured_orders(self) -> dict:
        selections = self.__dict__.get('ppms_lockin_selection', {})
        orders: dict = {}
        for key in self._ppms_sr830_keys():
            variable = selections.get(key)
            if variable is None or not variable.get():
                continue
            for order in self._ppms_harmonic_orders(key):
                if order in PPMS_HARMONIC_ORDERS and key not in orders.setdefault(order, []):
                    orders[order].append(key)
        zurich = selections.get('zurich')
        if zurich is not None and zurich.get():
            mode = str(self.zurich_harmonic.get()) if 'zurich_harmonic' in self.__dict__ else '1'
            for order in PPMS_ZURICH_MODE_ORDERS.get(mode, (1,)):
                orders.setdefault(order, []).append('zurich')
        return orders
    def _refresh_ppms2_plot_layout(self):
        frames = self.__dict__.get('ppms_harmonic_plot_frames')
        if not frames:
            return
        for frame in list(self.__dict__.get('ppms_transport_plot_frames', {}).values()) + list(self.__dict__.get('zurich_plot_frames', {}).values()):
            frame.grid_remove()
        orders = self._ppms2_measured_orders()
        last_visible = max((order for order in PPMS_HARMONIC_ORDERS if orders.get(order)), default=None)
        for (order, channel), canvas in self.__dict__.get('ppms_harmonic_canvases', {}).items():
            canvas.ppms_hide_x_labels = order != last_visible
        try:
            row_count = int(self.ppms_plot_frame.grid_size()[1])
        except (TypeError, ValueError, AttributeError, tk.TclError):
            row_count = 0
        for row in range(max(row_count, len(PPMS_HARMONIC_ORDERS) + 3)):
            self.ppms_plot_frame.rowconfigure(row, weight=0, minsize=0, uniform='')
        row = 0
        for order in PPMS_HARMONIC_ORDERS:
            meters = orders.get(order, [])
            names = ", ".join(PPMS_SR830_LABELS.get(key, "Zurich") for key in meters)
            for column, channel in enumerate(("x", "y", "theta")):
                frame = frames[(order, channel)]
                if not meters:
                    frame.grid_remove()
                    continue
                frame.configure(text=f"{PPMS_HARMONIC_LABELS[order]} {'θ' if channel == 'theta' else channel.upper()} — {names}")
                frame.grid(row=row, column=column, sticky='nsew')
            if meters:
                self.ppms_plot_frame.rowconfigure(row, weight=1, uniform='ppms_plot_rows')
                row += 1
        for column in range(3):
            self.ppms_plot_frame.columnconfigure(column, weight=1, uniform='ppms_plots')
        self._redraw_ppms_transport_plot()
    def _ppms_apply_selected_harmonic(self, key):
        self._refresh_ppms2_plot_layout()
        super()._ppms_apply_selected_harmonic(key)
    @staticmethod
    def _ppms2_finite(value):
        return value if isinstance(value, (int, float)) and math.isfinite(value) else None
    def _ppms2_prefix_resolver(self, meter, order):
        order_text = str(order)
        if meter != 'zurich':
            address = self._ppms2_meter_address(meter)
            if address is None:
                return lambda row: None
            harmonic_key, prefix = f'sr830_{address}_harmonic', f'sr830_{address}_'
            return lambda row: prefix if str(row.get(harmonic_key)) == order_text else None
        demods = tuple(((f'zurich_demod{index}_harmonic', f'zurich_demod{index}_input', f'zurich_demod{index}_') for index in range(1, 5)))
        default_mode = str(self.zurich_harmonic.get()) if 'zurich_harmonic' in self.__dict__ else '1'
        order_prefix = f'zurich_demod{order}_'
        def resolve(row):
            for harmonic_key, input_key, prefix in demods:
                if str(row.get(harmonic_key)) == order_text and str(row.get(input_key)) == '0':
                    return prefix
            mode = row.get('zurich_harmonic')
            mode = default_mode if mode is None else str(mode)
            if '+' in mode and order in PPMS_ZURICH_MODE_ORDERS.get(mode, ()):
                return order_prefix
            return 'zurich_' if mode == order_text else None
        return resolve
    def _ppms2_meter_prefix(self, meter, order, row):
        return self._ppms2_prefix_resolver(meter, order)(row)
    def _ppms2_meter_address(self, meter):
        address = self.__dict__.get('ppms_plot_addresses', {}).get(meter)
        if address is None:
            try:
                address = self._ppms_sr830_address(self.ppms_instrument_controllers[meter].resource_name)
            except (ValueError, AttributeError, KeyError):
                return None
        return address
    def _ppms2_meter_value(self, meter, order, channel, row):
        prefix = self._ppms2_meter_prefix(meter, order, row)
        if prefix is None:
            return None
        return self._ppms2_finite(row.get(prefix + ('theta_deg' if channel == 'theta' else f'{channel}_v')))
    def _redraw_ppms_transport_plot(self):
        canvases = self.__dict__.get('ppms_harmonic_canvases')
        if not canvases:
            super()._redraw_ppms_transport_plot()
            return
        axis = getattr(self, 'ppms_plot_axis', 'field')
        x_key, x_label = {'field': ('measured_field_oe', 'Measured magnetic field (Oe)'),
            'angle': ('measured_angle_deg', 'Measured angle (°)'),
            'temperature': ('temperature_k', 'Measured temperature (K)'),
            'current': ('6221_current_rms_ma', 'Applied AC current (mA RMS)'),
            'frequency': ('6221_frequency_hz', 'Frequency (Hz)')}[axis]
        rows = self.ppms_field_scan_rows
        max_x = max((abs(float(r[x_key])) for r in rows if isinstance(r.get(x_key), (int, float)) and math.isfinite(r[x_key])), default=0.0)
        scale = 1.
        if axis == 'field' and max_x >= 1000:
            scale, x_label = 1e-4, 'Measured magnetic field (T)'
        x_extent = max_x * scale if axis == 'field' else None
        orders = self._ppms2_measured_orders()
        by_frequency = getattr(self, 'ppms_active_scan_mode', '') == 'Frequency+Current'
        by_field = getattr(self, 'ppms_active_scan_mode', '') == 'Angle+Field'
        by_order = {}
        for (order, channel), canvas in canvases.items():
            by_order.setdefault(order, []).append((channel, canvas))
        prepared = []
        for row in rows:
            x_value = row.get(x_key)
            if not isinstance(x_value, (int, float)) or not math.isfinite(x_value):
                continue
            group = f"A{row.get('angle_index', 1)}" if axis == 'temperature' else f"T{row.get('temperature_index', 1)} ({row.get('target_temperature_k', '')} K)"
            temperature = row.get('target_temperature_k')
            if by_frequency:
                group = self._ppms_frequency_series_group(row)
                temperature = row.get('6221_frequency_hz')
            elif by_field:
                group = self._ppms_field_series_group(row)
                temperature = row.get('target_field_oe')
            prepared.append((row, f"{group} L{row.get('loop', 1)}", temperature, float(x_value) * scale))
        for order, targets in by_order.items():
            columns = tuple(((channel, 'theta_deg' if channel == 'theta' else f'{channel}_v') for channel, _ in targets))
            series = {channel: {} for channel, _ in targets}
            colors = {channel: {} for channel, _ in targets}
            labels = {channel: {} for channel, _ in targets}
            markers = {channel: {} for channel, _ in targets}
            for meter in orders.get(order, []):
                meter_label = PPMS_SR830_LABELS.get(meter, 'Zurich')
                base_color = PPMS_METER_COLORS.get(meter, '#666666')
                temperatures = {channel: {} for channel, _ in targets}
                resolve_prefix = self._ppms2_prefix_resolver(meter, order)
                for row, tail, temperature, point_x in prepared:
                    prefix = resolve_prefix(row)
                    if prefix is None:
                        continue
                    name = f'{meter_label} {tail}'
                    for channel, suffix in columns:
                        value = row.get(prefix + suffix)
                        if not isinstance(value, (int, float)) or not math.isfinite(value):
                            continue
                        temperatures[channel][name] = temperature
                        series[channel].setdefault(name, []).append((point_x, value))
                for channel, _ in targets:
                    shaded = self._ppms_temperature_plot_colors(base_color, temperatures[channel])
                    for name in temperatures[channel]:
                        colors[channel][name] = shaded.get(name, base_color)
                        labels[channel][name] = name
                        if meter == 'zurich':
                            markers[channel][name] = 'open_circle'
            for channel, canvas in targets:
                self._draw_nonlocal_series(canvas, series[channel], x_label,
                    'Phase θ (°)' if channel == 'theta' else f'{channel.upper()} voltage',
                    'Waiting for scan data', markers=True, auto_voltage_unit=channel != 'theta',
                    symmetric_x_extent=x_extent, show_legend=False,
                    angle_x_range=self.__dict__.get('ppms_plot_angle_range') if axis == 'angle' else None,
                    series_colors=colors[channel], series_labels=labels[channel],
                    series_markers=markers[channel])
    def _zurich_needs_md_text(self, harmonic):
        controller = self.__dict__.get('zurich_controller')
        if controller is None or '+' not in str(harmonic) or self.__dict__.get('zurich_has_md', True) or not controller.mfli_connected:
            return None
        device = str(controller.mfli_device or 'This Zurich').upper()
        return f'{device} has no MF-MD option: it reads one harmonic at a time. Choose Zurich harmonic 1, 2 or 3 (1+2 and 1+2+3 need MF-MD, as on DEV4414)'
    def _apply_ppms2_zurich_harmonic(self):
        if self.zurich_closed or self.closing or self.ppms_field_scan_running:
            return
        self._refresh_ppms2_plot_layout()
        if self.zurich_busy:
            self.root.after(100, self._apply_ppms2_zurich_harmonic)
            return
        if self.zurich_controller.mfli_connected:
            self.connect_ppms2_zurich(apply_only=True)
    def connect_ppms2_zurich(self, *, apply_only=False):
        if self.zurich_busy or self.zurich_closed or self.ppms_field_scan_running:
            return
        try:
            device, servers = zurich_mfli_choice(self.zurich_resource.get())
            reference = self.zurich_reference.get()
            harmonic = str(self.zurich_harmonic.get())
            tau = float(self.zurich_timeconstant.get())
            frequency = float(self.ppms_transport_frequency_var.get())
            if not device or harmonic not in ('1', '2', '3', '1+2', '1+2+3') or not math.isfinite(tau) or tau <= 0 or not math.isfinite(frequency) or frequency <= 0:
                raise ValueError("Please check the device name, harmonic, time constant and the PPMS reference frequency")
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("PPMS Zurich settings", str(error), parent=self.root)
            return
        if not apply_only:
            self.zurich_controller.mfli_server_candidates = servers
        self._set_zurich_busy(True)
        self.zurich_status.set("Updating harmonic and verifying configuration…" if apply_only else "Connecting and configuring…")
        self._set_connection_indicator("zurich_reference", False)
        one_order = {}
        def work():
            nonlocal harmonic
            controller = self.zurich_controller
            try:
                if not apply_only:
                    controller.connect_mfli(device)
                elif not controller.mfli_connected:
                    raise RuntimeError('Zurich is disconnected; harmonic not updated')
                with controller.lock:
                    multi = controller.mfli_has_multi_demod()
                    self.zurich_has_md = multi
                    if not multi and harmonic in ('1+2', '1+2+3'):
                        one_order['was'], harmonic = harmonic, '1'
                    controller.configure_mfli("AC", 1 if harmonic in ('1+2', '1+2+3') else int(harmonic), "R", reference_source=reference,
                        sample_rate_hz=NONLOCAL_SAMPLE_HZ, input_impedance="10 MΩ",
                        input_scaling=1.0, timeconstant_s=tau,
                        external_reference_demodulator=3 if multi else None)
                    if harmonic in ('1+2', '1+2+3'):
                        controller.configure_second_harmonic(tau)
                    if harmonic == '1+2+3':
                        controller.configure_third_harmonic(tau)
                    controller.disable_unused_ppms_demods(harmonic)
            except Exception:
                if not apply_only:
                    controller.disconnect_mfli()
                raise
        try:
            future = self.zurich_executor.submit(work)
        except Exception as error:
            self._set_zurich_busy(False)
            self.zurich_status.set(f'Configuration not started: {short_status_error(error, 90)}')
            return
        def finished():
            if self.zurich_closed or self.closing:
                return
            try:
                future.result()
                self.zurich_expected_frequency = frequency
                if one_order:
                    self.zurich_harmonic.set(harmonic)
                    self.thread_log(f'{self.ppms_page_name} Zurich {device.upper()} has no MF-MD option: it reads one harmonic at a time, so harmonic {one_order["was"]} was changed to {harmonic} (its reference on Demod 2, as on the Non-local pages)')
                self.zurich_status.set(f"Connected; harmonic {harmonic} applied; AC / 10 MΩ" + ('; no MF-MD: one harmonic' if one_order else ''))
                self.thread_log(f'PPMS Zurich harmonic {harmonic} applied and configuration verified')
            except Exception as error:
                self.zurich_status.set(f"Connect/configure failed: {short_status_error(error, 90)}")
                self.zurich_values.set("Reading unavailable")
                self.thread_log(f'PPMS Zurich configuration failed: {error}')
                if not self.closing:
                    try:
                        messagebox.showerror('Zurich harmonic update failed' if apply_only else 'PPMS Zurich connect / configure failed', str(error), parent=self.root)
                    except Exception:
                        pass
            if one_order:
                try:
                    self._refresh_ppms2_plot_layout()
                except Exception as error:
                    self.thread_log(f'{self.ppms_page_name} plot layout not refreshed after the harmonic change: {error}')
            self._set_zurich_busy(False)
        future.add_done_callback(lambda _: self.post_ui(finished))
    def disconnect_ppms2_zurich(self):
        if self.zurich_busy or self.zurich_closed or self.ppms_field_scan_running:
            return
        self._set_zurich_busy(True)
        future = self.zurich_executor.submit(self.zurich_controller.disconnect_mfli)
        def finished():
            if self.zurich_closed or self.closing:
                return
            try:
                future.result()
                self.zurich_status.set("Not connected")
            except Exception as error:
                self.zurich_status.set(f"Disconnect failed: {short_status_error(error, 90)}")
            self.zurich_values.set("X: -- V    Y: -- V    R: -- V    θ: -- °    Ref: -- Hz")
            self._set_zurich_busy(False)
        future.add_done_callback(lambda _: self.post_ui(finished))
    def _poll_ppms2_zurich(self):
        if self.zurich_closed or self.closing:
            return
        if not self.zurich_busy and (not self.__dict__.get('zurich_reading', False)) and (not self.ppms_field_scan_running) and self.zurich_controller.mfli_connected:
            try:
                expected = float(self.ppms_transport_frequency_var.get())
                if math.isfinite(expected) and expected > 0:
                    self.zurich_expected_frequency = expected
            except (ValueError, tk.TclError):
                pass
            self.zurich_reading = True
            def read():
                with self.zurich_controller.lock:
                    return self._read_ppms2_zurich_ranged(self.zurich_expected_frequency)
            future = self.zurich_executor.submit(read)
            def finished():
                if self.zurich_closed or self.closing:
                    return
                try:
                    sample = future.result()
                    self._display_zurich_sample(sample)
                except Exception as error:
                    self.zurich_status.set(f"Read failed: {short_status_error(error, 80)}")
                    self._set_connection_indicator("zurich_reference", False)
                    self.zurich_values.set("Reading unavailable (stale values hidden)")
                self.zurich_reading = False
            future.add_done_callback(lambda _: self.post_ui(finished))
        self.zurich_after_id = self.root.after(1000, self._poll_ppms2_zurich)
    def safe_close_ppms(self):
        if self.zurich_closed:
            return
        self.zurich_closed = True
        if self.zurich_after_id is not None:
            try:
                self.root.after_cancel(self.zurich_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.zurich_after_id = None
        self.zurich_executor.shutdown(wait=False, cancel_futures=True)
        super().safe_close_ppms()
PPMS2Panel = PPMSPanel
def maximize_main_window(root: tk.Tk) -> None:
    try:
        root.state('zoomed')
    except tk.TclError:
        try:
            root.attributes('-zoomed', True)
        except tk.TclError:
            pass
def main() -> None:
    root = tk.Tk()
    app = CombinedControlApp(root)
    root.after_idle(lambda: maximize_main_window(root))
    try:
        root.mainloop()
    finally:
        app.safe_close_hardware()
        app.executor.shutdown(wait=False, cancel_futures=True)
def run() -> None:
    """The installed command (pyproject.toml: transport-ppms)."""
    main()
if __name__ == "__main__":
    main()
