# Transport-PPMS

The PPMS page of [Transport-all](https://github.com/nbb2026/Transport-all) as
a program of its own: harmonic Hall and resistance measurements in a Quantum
Design PPMS with a Keithley 6221 source, up to three SR830 lock-ins and a
Zurich MFLI.

It runs without `Transport_all.py`. Everything it needs is in this branch:
the entry script [`Transport_PPMS.py`](Transport_PPMS.py) and the
[`transport`](transport) package beside it.

This is the `Transport-PPMS` branch (it was `modular`). The whole program,
with its MOKE, CIDWM, Non-local, SHHV & Switch, FMR and ST-FMR pages, stays on
`main`.

## Running it

**Windows .exe (no Python needed).** Download from the release
[Transport_PPMS v1.0](https://github.com/nbb2026/Transport-all/releases/tag/ppms-v1.0):

- `Transport_PPMS.exe`: one file. Put it in a folder you keep and
  double-click it.
- `Transport_PPMS-folder.zip`: the same program as one folder, which starts
  faster. Unzip it and double-click `Transport_PPMS\Transport_PPMS.exe`; keep
  the `_internal` folder beside it.

Both are rebuilt from this branch after every push that passes its checks; the
release notes give the commit and the SHA-256 of each file. The version is this
program's own (`APP_VERSION` in [`transport/ppms/app.py`](transport/ppms/app.py)), not the main
program's. Raising it starts a new release, once the owner has pushed the tag
`ppms-v<version>` (only the owner can make tags in this repository).

**From the source (64-bit Python 3.11):**

```bash
python -m pip install -r requirements.txt
```

```bash
python Transport_PPMS.py
```

**As a package:** `pip install` the wheel built from `pyproject.toml`, then
start it with the `transport-ppms` command.

Still needed on the measuring PC, outside the program: NI-VISA and the GPIB
driver, LabOne for the Zurich MFLI, and MultiVu / QDInstrument for the PPMS.
`QDInstrument.dll` kept beside the main program goes beside this one. An MFLI
on LabOne 18.12 needs the Python 3.7 bridge from
[`Other-files/INSTALL_MFLI_LABONE_18_12.bat`](Other-files/INSTALL_MFLI_LABONE_18_12.bat).

## What is in the window

Two pages:

- **PPMS**: the page as in Transport-all, with the same scans (field, angle,
  temperature, current, frequency and their combinations), the PPMS macro,
  the motor controls, the helium history and warning, the SR830 time-constant
  control, and the Zurich with one or several harmonics. Files, columns, run
  logs and start questions are the main program's.
- **Log**: the communication log, with pause, copy and save.

Closing the window switches the 6221 output off and disconnects the
instruments. If a field scan is running, it first asks the PPMS to zero the
field.

**Beside Transport_all or another Transport program:** both can be open at
once. An instrument one program has connected is refused to the others, with
the holder's name, until it is disconnected there (a named object of Windows
per instrument address; it goes when the holder disconnects, closes or dies).

## How it is put together

| Part | What it holds |
|---|---|
| [`Transport_PPMS.py`](Transport_PPMS.py) | The entry script: starts `transport.ppms.app.main`. |
| [`transport/ppms/app.py`](transport/ppms/app.py) | The application shell, UI panel, and scan orchestration. |
| [`transport/ppms/protocol.py`](transport/ppms/protocol.py) | PPMS limits and the checks of field and temperature commands. |
| [`transport/ppms/controllers`](transport/ppms/controllers) | Instrument controllers (VISA/lease, SR830, Zurich MFLI, QDInstrument, MultiPyVu, remote environment). |
| [`transport/ppms/engine`](transport/ppms/engine) | Hardware- and Tk-independent scan planning, macro parsing, and validation helpers. |
| [`transport/core`](transport/core) | Scan sequences, the run recorder (run folder, live CSV, JSON-lines log), and the end-of-scan zeroing and close hand-off. |
| [`transport/common`](transport/common) | Harmonic planning, lock-in settling, and the check of the 6221 / SR830 scan settings. |

### Standalone package authority

The `Transport-PPMS` branch (`transport/ppms`, `transport/common`, `transport/core`)
is the sole authoritative source for this application. Edit the package modules
directly; `tools/extract_app.py` is frozen and must not be used to overwrite
`transport/ppms/app.py`.

## Tests

Offline only; no instrument, port or VISA session is opened.

```bash
cd Other-files
```

```bash
python ci/run_suite.py
```

- The main program's PPMS tests, run against this application. Tests of other
  pages in the same files were taken out with
  [`tools/prune_tests.py`](tools/prune_tests.py), which prints the reason for
  each one it removes.
- Jiaze Li's tests of the package modules.
- `Other-files/ci/ppms_checks.py` builds the real window on GitHub's machines
  at the lab PC's screen sizes and saves screenshots.

GitHub runs all of it on Linux and Windows on every push
([`.github/workflows/ppms-tests.yml`](.github/workflows/ppms-tests.yml)), and
builds and checks the .exe
([`.github/workflows/ppms-windows-exe.yml`](.github/workflows/ppms-windows-exe.yml)).
The tests open windows, so run them on GitHub rather than on a desk computer.

Passing tests are not hardware acceptance: nothing here has been run on the
PPMS.

## More

- [`Other-files/README_PPMS_SIMPLE_MODES.md`](Other-files/README_PPMS_SIMPLE_MODES.md),
  [`Other-files/README_PPMS_NESTED.md`](Other-files/README_PPMS_NESTED.md):
  the scan modes.
- [`Other-files/PPMS_Macro_README.md`](Other-files/PPMS_Macro_README.md): the
  macro.
- [`Other-files/README_PPMS_MOTOR_DEBUG.md`](Other-files/README_PPMS_MOTOR_DEBUG.md):
  the motor.

License: GNU GPL v3.0 ([LICENSE](LICENSE)).
