# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for Transport_FMR.exe: the standalone FMR application
# (Transport_FMR.py and the transport package) as one Windows file that runs
# without Python.  It is the main program's recipe (Transport_all.spec, branch
# main) with only the libraries this application uses.
#
# GitHub Actions builds it on every push to the branch Transport-FMR
# (.github/workflows/fmr-windows-exe.yml).  To build it by hand on a Windows PC,
# from the repository root:
#     py -3.11 -m pip install -r requirements.txt pyinstaller==6.22.3
#     py -3.11 -m PyInstaller --noconfirm --clean Other-files/packaging/Transport_FMR.spec
# The program is then dist\Transport_FMR.exe.  With the environment variable
# TRANSPORT_ONE_FOLDER=1 it makes the one-folder build instead,
# dist\Transport_FMR\Transport_FMR.exe with its libraries in _internal,
# which starts faster (nothing is unpacked on each start).
#
# Still needed on the PC, outside the .exe: the instruments' vendor drivers and
# runtimes, and DLLs kept beside the main program, as for the .py program.

import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PACKAGING = Path(SPECPATH).resolve()
ROOT = PACKAGING.parents[1]
ICON = "Magneto_Electro_Optical_Microwave_Transport_NH"
ASSETS = ROOT / "Other-files" / "assets"
# The publisher, stored as the company name in the .exe's version information
# (user, 2026-11-12).  Windows shows it as "Company" (Explorer tooltip and
# column) and "Publisher" (Task Manager).  It is not a code signature:
# SmartScreen still says "Unknown publisher" for an unsigned .exe.
PUBLISHER = "HengNiu & ClaudeCode"
# File > Properties > Details does not show the company name but does show
# the copyright, so the publisher is named there too (user, 2026-11-12).
COPYRIGHT = f"© 2026 {PUBLISHER}"
VERSION = re.search(
    r'^APP_VERSION = "(.+)"', (ROOT / "transport" / "fmr" / "app.py").read_text(encoding="utf-8"), re.M
).group(1)
VERSION_NUMBERS = tuple((list(map(int, re.findall(r"\d+", VERSION))) + [0, 0, 0, 0])[:4])

# The window and taskbar icons, found by the program in "assets" beside it.
datas = [
    (str(ASSETS / f"{ICON}_icon.png"), "assets"),
    (str(ASSETS / f"{ICON}.ico"), "assets"),
]
binaries = []
# PyVISA reports its version from the package metadata.
datas += copy_metadata("pyvisa")
hiddenimports = collect_submodules("pyvisa") + collect_submodules("transport")
# The program imports these by name at run time (importlib).
hiddenimports += ["zhinst.core", "zhinst.utils"]

a = Analysis(
    # package_self_test.py runs first and does nothing unless the .exe is
    # started with --package-self-test; then Transport_FMR.py runs as usual.
    [str(PACKAGING / "package_self_test.py"), str(ROOT / "Transport_FMR.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)
# File > Properties > Details shows the description, versions, product name,
# copyright and original file name; the company name (PUBLISHER) shows
# elsewhere (above).
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=VERSION_NUMBERS,
        prodvers=VERSION_NUMBERS,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,  # Windows NT, 32/64-bit
        fileType=0x1,  # an application
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",  # U.S. English, Unicode
                    [
                        StringStruct("CompanyName", PUBLISHER),
                        StringStruct("FileDescription", "Transport_FMR"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "Transport_FMR"),
                        StringStruct("LegalCopyright", COPYRIGHT),
                        StringStruct("OriginalFilename", "Transport_FMR.exe"),
                        StringStruct("ProductName", "Transport_FMR"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 0x04B0])]),
    ],
)
common = dict(
    name="Transport_FMR",
    icon=str(ASSETS / f"{ICON}.ico"),
    version=version_info,
    console=False,  # a GUI program: no console window
    upx=False,  # compressed executables are flagged more often by antivirus
    debug=False,
    strip=False,
)
if os.environ.get("TRANSPORT_ONE_FOLDER") == "1":
    # One folder: the .exe and an _internal folder with the libraries and data.
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Transport_FMR")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
