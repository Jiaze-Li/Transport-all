"""Tkinter dialogs, folder management, connection overview, and run-recording UI helpers."""

from __future__ import annotations

import ctypes
import datetime as datetime_module
import os
import re
import sys
import tkinter as tk
import weakref
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from transport.core.scan_recording import (
    WINDOWS_RESERVED_FILE_NAMES,
    ScanRunRecorder as _CoreScanRunRecorder,
    make_scan_run_folder as _make_scan_run_folder,
    scan_file_stem as _scan_file_stem,
    scan_log_line as _scan_log_line,
)
from transport.ppms.controllers.zurich import ZURICH_MFLI_CHOICES, zurich_mfli_label
from transport.ppms.ui.theme import (
    APP_VERSION,
    LAMP_SIZE_PX,
    THEME_COLORS,
    _PROGRAM_FILE,
    create_lamp,
    set_lamp,
)

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
_ENGLISH_MESSAGE_ICONS = {
    "error": "error",
    "warning": "warning",
    "info": "information",
    "question": "question",
}
_NATIVE_MESSAGE_SHOW = None


def english_message_box(
    title=None, message=None, _icon=None, _type=None, **options
) -> str:
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


def _english_message_box(
    parent, title, message, _icon, _type, **options
) -> str:
    kind = str(options.get("type") or _type or "ok")
    buttons = _ENGLISH_MESSAGE_BUTTONS.get(kind, _ENGLISH_MESSAGE_BUTTONS["ok"])
    names = [name for name, _text in buttons]
    default = str(options.get("default") or names[0])
    cancel = next(
        (name for name in ("cancel", "no", "abort", "ok") if name in names), names[-1]
    )
    top = tk.Toplevel(parent, class_="EnglishMessageBox")
    try:
        return _show_english_message_box(
            top, parent, title, message, _icon, kind, buttons, names, default, cancel, options
        )
    except BaseException:
        try:
            top.destroy()
        except tk.TclError:
            pass
        raise


def _show_english_message_box(
    top, parent, title, message, _icon, kind, buttons, names, default, cancel, options
) -> str:
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
        button = ttk.Button(
            row, text=text, name=name, command=lambda value=name: answer.set(value)
        )
        button.pack(side="left", padx=(6, 0))
        widgets[name] = button
    body = ttk.Frame(top, padding=(12, 12, 12, 8))
    body.pack(side="top", fill="both", expand=True)
    icon = _ENGLISH_MESSAGE_ICONS.get(
        str(options.get("icon") or _icon or "info"), "information"
    )
    image = f"::tk::icons::{icon}"
    try:
        has_image = image in top.tk.splitlist(top.tk.call("image", "names"))
    except tk.TclError:
        has_image = False
    if has_image:
        ttk.Label(body, image=image).pack(side="left", anchor="n", padx=(0, 12))
    else:
        tk.Label(
            body, bitmap={"information": "info"}.get(icon, icon)
        ).pack(side="left", anchor="n", padx=(0, 12))
    text = ttk.Frame(body)
    text.pack(side="left", fill="both", expand=True)
    try:
        wrap = max(360, min(640, int(top.winfo_screenwidth() * 0.4)))
    except tk.TclError:
        wrap = 520
    ttk.Label(text, text=str(message or ""), wraplength=wrap, justify="left").pack(
        anchor="w"
    )
    if options.get("detail"):
        ttk.Label(
            text, text=str(options["detail"]), wraplength=wrap, justify="left"
        ).pack(anchor="w", pady=(6, 0))
    top.protocol("WM_DELETE_WINDOW", lambda: answer.set(cancel))
    top.bind("<Escape>", lambda _event: answer.set(cancel))

    def press(_event=None) -> None:
        focused = top.focus_get()
        target = (
            focused
            if focused in widgets.values()
            else widgets.get(default, widgets[names[0]])
        )
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
                if int(parent.tk.call("winfo", "exists", previous_grab)) and int(
                    parent.tk.call("winfo", "viewable", previous_grab)
                ):
                    parent.tk.call("grab", "set", previous_grab)
            except tk.TclError:
                pass
    return value


FOLDER_DIALOG_NAME_RULE = "Use a valid Windows folder name without a path."
FOLDER_DIALOG_COUNT_LIMIT = 20000
_OPEN_SCAN_RUNS = weakref.WeakSet()
_FOLDER_DIALOG_LAST_DIRECTORY = ""
_NATIVE_ASK_DIRECTORY = None
_FOLDER_DELETE_WHILE_SCANNING = (
    "A scan is running. A folder with data can be deleted here once it has ended."
)
SCAN_FILE_NAME_PROMPT = "Optional file name (leave blank for a timestamped default):"


def folder_name_problem(name: str) -> str:
    text = str(name)
    if not text.strip():
        return "Type a name."
    if (
        text != text.strip()
        or text.startswith(".")
        or text.endswith(".")
        or len(text) > 255
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', text)
        or text.split(".")[0].upper() in WINDOWS_RESERVED_FILE_NAMES
    ):
        return FOLDER_DIALOG_NAME_RULE
    return ""


def folder_name_sort_key(name: str) -> list:
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part.casefold())
        for part in re.split(r"(\d+)", str(name))
        if part
    ]


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
    return [
        recorder
        for recorder in list(_OPEN_SCAN_RUNS)
        if not getattr(recorder, "closed", True)
    ]


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
        return getattr(os.lstat(folder), "st_reparse_tag", 0) in (
            0xA000000C,
            0xA0000003,
        )
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
        return f"A running scan is saving into {run.as_posix()}; {folder.name} cannot be {action} until that scan has ended."
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
        same = name.casefold() == folder.name.casefold() and os.path.samefile(
            folder, target
        )
        if not same:
            raise ValueError(f"{name} is already there.")
    os.rename(folder, target)
    return target


class _RecycleBinInfo(ctypes.Structure):
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


class _ShellFileOperation(ctypes.Structure):
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_void_p),
    ]


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
            with winreg.OpenKey(
                hive, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
            ) as key:
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
    guid = volume.value[volume.value.index("{") : volume.value.rindex("}") + 1]
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\BitBucket\\Volume"
            + "\\"
            + guid,
        ) as key:
            def value(entry):
                try:
                    return int(winreg.QueryValueEx(key, entry)[0])
                except OSError:
                    return None

            megabytes = value("MaxCapacity")
            return bool(value("NukeOnDelete")), (
                None if megabytes is None else megabytes * 1024 * 1024
            )
    except OSError:
        return False, None


def _windows_drive_size(root: str) -> int:
    total = ctypes.c_uint64(0)
    size = ctypes.windll.kernel32.GetDiskFreeSpaceExW
    size.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint64),
        ctypes.c_void_p,
    ]
    size.restype = ctypes.c_int
    if not size(root, None, ctypes.byref(total), None):
        raise OSError("the drive's size could not be read")
    return int(total.value)


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
            return f"It is larger than the Recycle Bin of its drive ({_folder_size_text(largest)})"
        return ""
    except Exception as error:
        return f"The Recycle Bin of its drive could not be checked ({error})"


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
        raise OSError(
            f"Windows could not move {name} to the Recycle Bin (code {code})."
        )
    if operation.fAnyOperationsAborted:
        raise OSError(f"Moving {name} to the Recycle Bin was cancelled.")
    if os.path.lexists(folder):
        raise OSError(
            f"{name} is still there: Windows did not move it to the Recycle Bin."
        )


def _folder_delete_refusal(folder: Path) -> str:
    refusal = _folder_edit_refusal(folder, "deleted")
    if not refusal and folder_is_link(folder):
        refusal = f"{folder.name} is a link to another folder; it is not deleted here."
    return refusal


def _folder_is_empty(folder) -> bool:
    with os.scandir(folder) as entries:
        return next(entries, None) is None


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
            "in the Windows Explorer."
        )
    holds = f"{files} file{('s' if files != 1 else '')} and {folders} folder{('s' if folders != 1 else '')} ({_folder_size_text(size)})"
    no_bin = recycle_bin_refusal(folder, size)
    if no_bin:
        raise ValueError(
            f"{folder.name} holds {holds}. {no_bin}, so only an empty folder can be deleted in this window."
        )
    return (
        "recycle",
        f"Delete the folder {folder.name}?\n\nIt holds {holds}. Windows moves it to the Recycle Bin, where it can be restored.",
    )


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
        raise ValueError(
            f"{no_bin}, so only an empty folder can be deleted in this window."
        )
    before = recycle_bin_item_count(folder)
    move_folder_to_recycle_bin(folder, window_handle)
    after = recycle_bin_item_count(folder)
    if before is not None and after is not None and after <= before:
        return f"{folder.name} is gone. The Recycle Bin did not gain an item, so check that it is in there: Windows may have deleted it for good."
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
        ttk.Label(frame, text="Directory:").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.directory_box = ttk.Combobox(
            frame, textvariable=self.directory_var, name="directory"
        )
        self.directory_box.grid(row=0, column=1, sticky="ew")
        self.up_button = ttk.Button(
            frame, text="Up", width=6, name="up", command=self.go_up
        )
        self.up_button.grid(row=0, column=2, sticky="e", padx=(6, 0))
        box = ttk.Frame(frame)
        box.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=8)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(
            box,
            name="folders",
            width=64,
            height=14,
            activestyle="none",
            exportselection=False,
            selectmode="browse",
        )
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.status_label = ttk.Label(
            frame,
            textvariable=self.status_var,
            name="status",
            wraplength=520,
            justify="left",
        )
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.status_label.grid_remove()
        ttk.Label(frame, text="Selection:").grid(
            row=3, column=0, sticky="w", padx=(0, 6)
        )
        self.selection_entry = ttk.Entry(
            frame, textvariable=self.selection_var, name="selection"
        )
        self.selection_entry.grid(row=3, column=1, columnspan=2, sticky="ew")
        bar = ttk.Frame(frame, name="buttons")
        bar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.new_button = ttk.Button(
            bar, text="New folder", name="new", command=self.new_folder
        )
        self.rename_button = ttk.Button(
            bar, text="Rename", name="rename", command=self.rename_selected
        )
        self.delete_button = ttk.Button(
            bar, text="Delete", name="delete", command=self.delete_selected
        )
        for button in (self.new_button, self.rename_button, self.delete_button):
            button.pack(side="left", padx=(0, 6))
        self.cancel_button = ttk.Button(
            bar, text="Cancel", name="cancel", command=self.cancel
        )
        self.cancel_button.pack(side="right")
        self.ok_button = ttk.Button(bar, text="OK", name="ok", command=self.ok)
        self.ok_button.pack(side="right", padx=(0, 6))
        self.listbox.bind(
            "<<ListboxSelect>>", lambda _event: self._selection_changed()
        )
        self.listbox.bind("<Double-Button-1>", lambda _event: self.open_selected())
        self.listbox.bind("<Return>", lambda _event: self.open_selected())
        self.listbox.bind("<BackSpace>", lambda _event: self.go_up())
        self.listbox.bind("<F2>", lambda _event: self.rename_selected())
        self.listbox.bind("<Delete>", lambda _event: self.delete_selected())
        self.directory_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.go_to(self.directory_var.get()),
        )
        self.directory_box.bind(
            "<Return>", lambda _event: self.go_to(self.directory_var.get())
        )
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
            return [
                str(volume)
                for volume in self.top.tk.splitlist(self.top.tk.call("file", "volumes"))
            ]
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
        self.directory_box.configure(
            values=values + [v for v in self._volumes() if v not in values]
        )
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
        self.up_button.state(
            ["disabled"] if self.directory.parent == self.directory else ["!disabled"]
        )

    def go_to(self, text) -> bool:
        path = self._typed_path(text)
        if path is None or not path.is_dir():
            self._error(
                "Choose a folder",
                f"{str(text).strip() or 'That'} is not a folder.",
            )
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
            return simpledialog.askstring(
                title, prompt, initialvalue=initial, parent=self.top
            )
        finally:
            self._regrab()

    def new_folder(self) -> None:
        name = self._ask_name(
            "New folder",
            f"Name of the new folder in {self.directory.as_posix()}:",
        )
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
        name = self._ask_name(
            "Rename folder", f"New name for {folder.name}:", folder.name
        )
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
            agreed = messagebox.askyesno(
                "Delete folder",
                question,
                default="no",
                icon="warning",
                parent=self.top,
            )
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
                self._error(
                    "Choose a folder", f"{path.as_posix()} is not a folder."
                )
                return
            if self.mustexist:
                self._error(
                    "Choose a folder", f"{path.as_posix()} does not exist."
                )
                return
            try:
                agreed = messagebox.askyesno(
                    "Choose a folder",
                    f"{path.as_posix()} does not exist. Make it?",
                    parent=self.top,
                )
            finally:
                self._regrab()
            if not agreed:
                return
            try:
                path.mkdir(parents=True)
            except OSError as error:
                self._error(
                    "Choose a folder", f"The folder was not made: {error}"
                )
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
                    if int(parent.tk.call("winfo", "exists", previous_grab)) and int(
                        parent.tk.call("winfo", "viewable", previous_grab)
                    ):
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
        dialog = EditableFolderDialog(
            parent,
            options.get("title", ""),
            options.get("initialdir", ""),
            options.get("mustexist", False),
        )
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
        native_folder = tk_filedialog._english_native_askdirectory = (
            tk_filedialog.askdirectory
        )
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
        print(
            f"Tk's own dialogs are not available; the system's are used: {error}",
            file=sys.stderr,
        )
        return False


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
        self.frame = ttk.Frame(
            root, style="Overview.TFrame", padding=(pad, 0, pad, 0)
        )
        self.lamp = create_lamp(
            self.frame, kind="overview", background=THEME_COLORS["page"]
        )
        self.lamp[0].pack(side="left", padx=(0, 4))
        self.text = tk.StringVar(value="")
        self.label = ttk.Label(
            self.frame, textvariable=self.text, style="Overview.TLabel"
        )
        self.label.pack(side="left")
        self.tooltip = None
        self.pending = None
        self.texts = ("", "", "off")
        root._look_overview_refresh = self.schedule
        for widget in (self.frame, self.label, self.lamp[0]):
            widget.bind("<Enter>", self._show_tooltip, add="+")
            widget.bind("<Leave>", self._hide_tooltip, add="+")
        notebook.bind("<Configure>", lambda _event: self.schedule(), add="+")
        notebook.bind(
            "<<NotebookTabChanged>>", lambda _event: self.schedule(), add="+"
        )
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
        return (
            str(self.notebook.tk.call(str(self.notebook), "index", f"@{x},{y}"))
            != ""
        )

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
            if left > 0 and not any(
                self._tab_at(x, y)
                for x in range(left - 6, width, 4)
                for y in (2, height // 2, height - 3)
            ):
                self.frame.place(
                    in_=self.notebook,
                    x=width - self.pad,
                    y=0,
                    anchor="ne",
                    height=height,
                )
                return

    def _show_tooltip(self, _event=None) -> None:
        self._hide_tooltip()
        lines = connection_overview_lines(self.pages()) or ["No instruments"]
        try:
            tip = tk.Toplevel(self.root)
            tip.wm_overrideredirect(True)
            ttk.Label(
                tip, text="\n".join(lines), style="Tooltip.TLabel", justify="left"
            ).pack()
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


def create_zurich_chooser(
    parent, variable, *, match_chars: int | None = None, width: int = 16
):
    variable.set(zurich_mfli_label(variable.get()))
    chooser = ttk.Combobox(
        parent,
        textvariable=variable,
        values=tuple(ZURICH_MFLI_CHOICES),
        state="readonly",
        width=match_chars or width,
    )
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
            raw = [
                int(round(float(str(value))))
                for value in parent.tk.splitlist(style.lookup("TCombobox", "padding"))
            ] or [0]
            pad_left = raw[0]
            pad_top = raw[1] if len(raw) > 1 else pad_left
            pad_right = raw[2] if len(raw) > 2 else pad_left
            pad_bottom = raw[3] if len(raw) > 3 else pad_top
            name = f"Zurich{spare}.TCombobox"
            style.configure(
                name, padding=(pad_left, pad_top, pad_right + spare, pad_bottom)
            )
            chooser.configure(style=name)
        chooser.zurich_shortfall = max(0, target - int(chooser.winfo_reqwidth()))
    except (tk.TclError, TypeError, ValueError, AttributeError):
        chooser.zurich_shortfall = 0
    return chooser


def ask_scan_start(parent, title, question, expected, warning="", **_options) -> bool:
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)
    top.resizable(False, False)
    body = ttk.Frame(top, padding=18)
    body.grid(row=0, column=0, sticky="nsew")
    base = tkfont.nametofont("TkDefaultFont")
    size = int(base.cget("size"))
    big = base.copy()
    big.configure(weight="bold", size=round(size * 1.4))
    row = 0
    if warning:
        ttk.Label(
            body, text=warning, foreground="#b00020", wraplength=460, justify="left"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        row = 1
    ttk.Label(body, text="Expected scan time:").grid(
        row=row, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(body, text=expected, foreground="#d00000", font=big).grid(
        row=row + 1, column=0, columnspan=2, sticky="w", pady=(4, 12)
    )
    ttk.Label(body, text="Is this time OK? Start the scan?").grid(
        row=row + 2, column=0, columnspan=2, sticky="w", pady=(0, 14)
    )
    answer = [False]

    def close(value: bool) -> None:
        answer[0] = value
        top.grab_release()
        top.destroy()

    yes = ttk.Button(body, text="Yes", width=10, command=lambda: close(True))
    yes.grid(row=row + 3, column=0, sticky="e", padx=(0, 8))
    ttk.Button(body, text="No", width=10, command=lambda: close(False)).grid(
        row=row + 3, column=1, sticky="w"
    )
    body.columnconfigure(0, weight=1)
    top.protocol("WM_DELETE_WINDOW", lambda: close(False))
    top.bind("<Escape>", lambda _event: close(False))
    top.bind("<Return>", lambda _event: close(True))
    top.ask_scan_start_font = big
    top.update_idletasks()
    try:
        x = parent.winfo_rootx() + max(
            0, (parent.winfo_width() - top.winfo_reqwidth()) // 2
        )
        y = parent.winfo_rooty() + max(
            0, (parent.winfo_height() - top.winfo_reqheight()) // 3
        )
        top.geometry(f"+{x}+{y}")
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
    spec = (
        str(label.cget("font"))
        or ttk.Style(label).lookup(label.winfo_class(), "font")
        or "TkDefaultFont"
    )

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


def scan_file_stem(name: str) -> str:
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
    return _make_scan_run_folder(parent, stem)


def scan_log_line(name: str, details: dict) -> str:
    return _scan_log_line(name, details, datetime_class=datetime_module.datetime)


class ScanRunRecorder(_CoreScanRunRecorder):
    """Application-bound ScanRunRecorder registered with open-run tracking."""

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


def begin_scan_run(
    parent,
    page_label: str,
    folder_title: str,
    initial_dir,
    settings: dict,
    *,
    prefix: str | None = None,
    make=None,
) -> ScanRunRecorder | None:
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
        return ScanRunRecorder(
            Path(folder), prefix or page_label, typed, settings, label=page_label
        )
    except Exception as error:
        messagebox.showerror(
            f"{page_label} run folder",
            f"Could not create the run folder and its files; the scan was not started: {error}",
            parent=parent,
        )
        return None
