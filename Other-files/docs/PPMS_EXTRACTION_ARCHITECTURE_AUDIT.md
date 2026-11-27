# PPMS extraction and architecture audit

Status: **Completed independent audit** (A1 and A2). This is the single evidence report for Draft PR #1 (`Jiaze-Li/Transport-all#1`), not a runtime specification. No production code, tests, workflows, or release artifacts were modified.

## Baselines
- Reference full application: `main@56908d88feb69597499b1c270bcf9a0f88b26972`
- Standalone application: `Transport-PPMS@8369a29e117e643bb9a3e38a5fc795b473f5dfe8`
- Common ancestor: `f4c6d81c1dc086499f0b3ae6e60a771327bf8cab`
- Audit branch scaffold: `audit/ppms-extraction-and-architecture@5e5430b070fab0645cef6c26dda8f3cfa55c2085`

---

## A1 — Extraction integrity

### 1. Feature and behavior parity matrix
AST comparison of `PPMSPanel` and `CombinedControlApp` between `main@56908d88` (`Transport_all.py`) and `Transport-PPMS@8369a29e` (`transport/ppms/app.py`) shows **exact parity** on all PPMS measurement, UI, plotting, recording, and safety logic:

- `PPMSPanel`: **28 / 28 methods** identical at AST level (0 added, 0 removed, 0 modified).
- `CombinedControlApp`: **158 methods** kept (of 796 in `main`; 638 non-PPMS methods dropped). The only **8** modified methods are intentional host/glue/pruning changes:
  - Constructor pruning (`tools/extract_app.py` `INIT_DROP`): `__init__` (drops non-PPMS state, tabs, and timers).
  - Host window & lifecycle (`tools/ppms_host.py`): `build_ui`, `request_close`, `safe_close_hardware`, `_redraw_active_tab`.
  - Package glue (`tools/ppms_package_glue.py`): `_ppms_harmonic_orders`, `_ppms_harmonic_passes` (delegating to `transport.common.harmonics`).
  - Path resolution: `_append_ppms_helium_csv` (`__file__` → `_PROGRAM_FILE`).
- Top-level functions: **93 total** (**92 kept** from `main`, of which 12 differ due to `_PROGRAM_FILE`/icon/AppID (`_application_asset_path`, `apply_application_identity`, `program_directory`), `tools/ppms_host.py` (`main`), or `tools/ppms_package_glue.py` (`build_ppms_field_scan_sequence`, `make_scan_run_folder`, `ppms_lockin_settle_s`, `ppms_lockin_settle_text`, `ppms_settle_status_text`, `scan_file_stem`, `scan_log_line`, `validate_ppms_transport_scan_settings`), plus **1 newly added** entrypoint (`run`, for `pyproject.toml`).

| Feature area | Evidence in `transport/ppms/app.py` | Parity vs `main@56908d88` | Notes |
| --- | --- | --- | --- |
| All 10 scan modes (`Temperature`, `Field`, `Angle`, `Current`, `Field+Temperature`, `Angle+Temperature`, `Current+Temperature`, `Angle+Field`, `Frequency`, `Frequency+Current`) | `PPMS_SCAN_MODES` (`L1747`), `start_ppms_field_scan` (`L8727`), `_ppms_nested_scan_worker` (`L9300`), `_ppms_field_scan_worker` (`L9661`) | Identical | Unchanged AST |
| Harmonic acquisition & lock-in choices (SR830-1/2/3, Zurich MFLI `1`/`2`/`3`/`1+2`/`1+2+3`) | `_ppms_harmonic_orders` (`L6531`), `_ppms_harmonic_passes` (`L6541`), `PPMSPanel.__init__` (`L10315–10421`, lock-in setup `L10322–10418`), `transport/common/harmonics.py` | Equivalent | Delegated via `ppms_package_glue.py` |
| Motor / rotator & Macro | `PPMSMotorDebug` (`L2776`), `_ppms_motor_action` (`L7153`), `_ppms_macro_operation` (`L7386`) | Identical | Unchanged AST |
| Helium monitoring & CSV | `_append_ppms_helium_csv`, `_ppms_poll_timer` (`L7944`) | Equivalent | Uses `_PROGRAM_FILE` for base path |
| Defaults, units, limits | `PPMS_DEFAULT_RESOURCES` (`L1815`), `transport/ppms/protocol.py`, `transport/common/validation.py` | Equivalent | `APP_VERSION = "1.0"` (intentional standalone version) |
| Live plots, CSV columns, run folders, JSON log, PNG snapshots | `ScanRunRecorder` (`L5683`), `transport/core/scan_recording.py`, `_append_ppms_live_csv` (`L9889`), `_redraw_ppms_transport_plot` (`L10683`) | Equivalent | Snapshot runtime test lost during pruning (see §3) |
| Cancellation, error paths, close handoff | `stop_ppms_field_scan` (`L9878`), `_ppms_zero_field_after_interrupted_scan` (`L9853`), `safe_close_ppms` (`L10125`, `L10894`), `request_close` (`L10275`) | Equivalent | Host `request_close` targets PPMS only |

### 2. Dependency closure and packaging
- **Entrypoint & package**: `Transport_PPMS.py` → `transport.ppms.app:main`; `pyproject.toml` declares `transport-ppms = "transport.ppms.app:run"`, `dependencies = ["numpy", "pillow", "pyvisa"]`, and all 5 `transport.*` packages. Verified by `test_package_metadata.py` (4 tests pass) and Linux CI wheel build/install (`45996570966`).
- **Lazy / optional vendor imports**: `numpy`, `PIL.ImageGrab`, `pyvisa` (`L1240–1251`), `zhinst.core`, `zhinst.utils` (`L1252–1259`), plus dynamic `MultiPyVu` (`transport/ppms/controllers/multivu.py`) and `pythonnet` (`clr`) / `pywin32` (`win32com.client`, `pythoncom`, `pywintypes`) for `PPMSQDInstrumentReadbackController`. All are guarded with graceful fallbacks or explicit error messages when missing.
- **Asset & path resolution**: `tools/extract_app.py` rewrites `__file__` to `_PROGRAM_FILE` (`Path(__MODULE_FILE__).resolve().parents[2] / "Transport_PPMS.py"`, or `Path.cwd() / "Transport_PPMS.py"` when installed in `site-packages`). `Other-files/assets/Transport_PPMS_icon.png` and `Transport_PPMS.ico` exist and are bundled in `Other-files/packaging/Transport_PPMS.spec` (`L54–57`).
- **PyInstaller spec (`Other-files/packaging/Transport_PPMS.spec`)**: Bundles `package_self_test.py` first (`--package-self-test`), `Transport_PPMS.py`, `pyvisa` metadata, `transport` submodules, `zhinst.core`/`zhinst.utils`, `MultiPyVu` (`collect_all`), and `pywin32.version.txt`. Windows CI `build` job (`45996570863`) passed `--package-self-test` and window startup.
- **Dropped symbols check**: Static scan of `DROP` names in `transport/ppms/app.py` confirms no missing top-level symbol references (`connect`/`disconnect` hits are instrument/method attributes on controllers). Minor residual: `safe_close_ppms` (`L10126`) still checks `("rte_panel", "ppms_panel")` — harmless no-op when `rte_panel` is absent.

### 3. Test retention, removal, and coverage gaps
Comparing `main@56908d88` vs `Transport-PPMS@8369a29e` across `Other-files/`:
- **Retained / added**: 46 test modules (`610` tests run locally, `0` failed, `3` skipped). New unit suites cover `transport.core.*`, `transport.common.*`, `transport.ppms.protocol`, `transport.ppms.controllers.multivu`, `test_package_metadata.py`, and `test_instrument_lease_across_programs.py` (commit `0ab9d0a`).
- **Helper-only file**: `Other-files/test_nonlocal_keithley.py` has **0 tests** (`KEEP_AS_HELPERS` in `tools/prune_tests.py` so other tests can import its stand-ins).
- **Coverage regressions from wholesale deletion / pruning (Finding A1-1, A1-2)**:
  1. `Other-files/test_scan_plot_snapshots.py` was **deleted in full** because it imported non-PPMS helper modules (`shv_runs`, `moke_runs`, `fmr_runs`). This removed `PpmsTests.test_a_scan_saves_one_picture_of_its_plot_box` (`main` lines `375–391`), leaving PPMS PNG snapshot saving (`plot_snapshots`, `run1_plots_snapshot.png`) untested in `Transport-PPMS`.
  2. `Other-files/test/test_instrument_safety_logic.py` (2,482 lines in `main`) was **deleted in full**. It contained PPMS-specific tests (`main` lines `1668–2269`, `2371–2404`) covering:
     - `PPMSQDInstrumentReadbackController` (5 tests: pythonnet readback shapes, explicit DLL discovery, .NET assembly loader, ref readback reflection, typed `SetField` reflection) — currently **0 unit tests** in `Transport-PPMS`!
     - `LegacyMFLIBridge` compilation check (`test_legacy_labone_bridge_source_still_compiles`)
     - `SharedVisaIdentityController` / `acquire_shared_visa_manager` lifecycle (`test_shared_visa_manager_survives_one_controller_disconnect`, `test_failed_visa_identity_check_does_not_close_other_session`)
     - `test_safe_close_hardware_continues_after_module_failure`, `test_ui_queue_drain_is_time_sliced`, `test_ppms1_transport_allows_one_sr830_at_address_10`, `test_the_zurich_is_set_up_as_on_ppms`.
  3. Partial pruning in `test_lockin_settle_rte_ppms.py`, `test_ppms_rte_review.py`, `test_ppms_sr830_tc.py`, `test_zurich_choice.py` mostly removed RTE/SHHV/ST-FMR cross-page tests, though any test referencing both PPMS and RTE in one method was also dropped.

### 4. Safety contracts (static and offline evidence)
- **Current output OFF + readback**: `ppms_sources_off` (`L10077–10124`) and `_ppms_zero_6221_after_scan` (`L9143–9153`) call `controller.safe_output_off()` under `controller.lock` and verify `OUTP?` == `0`, raising `RuntimeError` if non-zero.
- **Field zeroing**: Interrupted/errored field-commanding scans (`PPMS_FIELD_COMMANDING_MODES`, `L1748`) call `_ppms_zero_field_after_interrupted_scan` (`L9853–9866`) before surfacing errors; `safe_close_ppms` (`L10153–10163`) zeros the field if closing during an active field-commanding scan that has not already accepted field zeroing.
- **Temperature & field limits**: Enforced in `transport/ppms/protocol.py` (`validate_ppms_field_command`, `validate_ppms_temperature_command`, `ppms_scan_temperature_segments`) and covered by `test_ppms_protocol_module.py`.
- **Stop & shutdown ordering**: `request_close` (`L10275`) prompts for confirmation, sets `self.closing = True` and `ppms_field_scan_stop_event` on both `CombinedControlApp` and `ppms_panel`, then spawns daemon thread `ppms-safe-shutdown` to call `safe_close_hardware()` → `safe_close_ppms()` (using `ScanCloseHandoff` at `L10178` to defer hardware disconnect if a scan worker is active), then shuts down `file_executor` and `executor`.
  - *Caveat (Finding A2-3)*: `PPMSPanel.safe_close_ppms` (`L10901–10902`) submits `zurich_controller.disconnect_mfli` and shuts down `zurich_executor` immediately, before `super().safe_close_ppms()` checks `ScanCloseHandoff`.
- **Cross-program instrument lock (`0ab9d0a`, `L1877–1996`)**:
  - **Windows (`win32`)**: Uses `kernel32.CreateFileMappingW` (`Local\TransportInstrumentLease_<key>`, 512 bytes) — kernel-backed, auto-released on process crash.
  - **macOS / Linux (`else`)**: Uses `fcntl.flock(..., LOCK_EX | LOCK_NB)` on `tempfile.gettempdir() / "Transport_instrument_leases" / "<name>.lock"`.
  - **Limitation**: `NonLocalACController` (Zurich MFLI) uses its own connection path (`connect_mfli`), not `SafeVisaIdentityController.acquire_instrument_lease`.

### 5. A1 Verdict
- **A1 Verdict: PASS with minor test-restoration follow-ups (no extraction blockers for D1).**
- Runtime extraction parity is complete (`PPMSPanel` 28/28 identical; all 46 retained test modules / 610 tests pass locally and on Linux/Windows CI; Windows EXE self-test passes).
- **Required follow-up before release hardening (non-blocking for D1 design)**: Restore the deleted PPMS PNG snapshot test from `test_scan_plot_snapshots.py` and the PPMS/QDInstrument/SharedVisa unit tests from `test_instrument_safety_logic.py` as standalone PPMS test modules.

---

## A2 — Internal architecture

### 1. Module and dependency map
Total `transport/` package size: **12,021 lines**, of which **10,926 lines (90.9%)** live in the generated monolith `transport/ppms/app.py`.

```
Transport_PPMS.py (19 L)
  └── transport.ppms.app (10,926 L) [GUI + controllers + legacy classes]
        ├── transport.core.scan_sequences (93 L)      [pure: math only]
        ├── transport.core.scan_recording (260 L)     [pure stdlib: csv, json, pathlib, threading]
        ├── transport.core.scan_lifecycle (106 L)     [pure stdlib: threading]
        ├── transport.common.harmonics (110 L)        [pure stdlib: typing]
        ├── transport.common.settling (65 L)          [pure stdlib: math, typing]
        ├── transport.common.validation (98 L)        [pure stdlib: math, typing]
        ├── transport.ppms.protocol (101 L)           [pure stdlib: math]
        └── transport.ppms.controllers.multivu (169 L) [stdlib + transport.ppms.protocol; lazy MultiPyVu]
```

- **Cleanly decoupled modules**: `transport/core/*`, `transport/common/*`, `transport/ppms/protocol.py`, and `transport/ppms/controllers/multivu.py` have **zero** Tkinter or `app.py` dependencies.
- **Still coupled to `app.py`**: All UI (`build_ppms_ui`, `PPMSPanel`), scan orchestration (`start_ppms_field_scan`, `_ppms_nested_scan_worker`, `_ppms_field_scan_worker`), VISA/SCPI (`SafeVisaIdentityController`, `STFMRSR830Controller`), QDInstrument (`PPMSQDInstrumentReadbackController`), Zurich MFLI (`NonLocalACController`, `LabOneDotNetAdapter`, `LabOneCAPIAdapter`, `LegacyMFLIBridge`), and motor/macro (`PPMSMotorDebug`).

### 2. End-to-end execution flows
- **Representative Field scan & Nested scan**:
  1. **UI → Settings**: `start_ppms_field_scan` (`L8727`) validates inputs from Tk `StringVar`s, builds axes via `ppms_scan_axis` / `ppms_prime_frequencies`, resolves `PPMS_FIELD_COMMANDING_MODES`, and calls `_ppms_run_settings` (`L9058`).
  2. **Scheduling & Passes**: Submits `_ppms_scan_with_final_field` (`L9185`) to `self.ppms_executor` (single-worker `ThreadPoolExecutor`). Calls `ScanCloseHandoff.begin()`, iterates `_ppms_harmonic_passes(settings)` (via `transport.common.harmonics`), and invokes `_ppms_nested_scan_worker` (`L9300`) → `_ppms_field_scan_worker` (`L9661`).
  3. **Hardware I/O**: Worker acquires `controller.lock` on `SafeVisaIdentityController` (`6221`, `830_a/b/c`) / `zurich_controller.lock` (`NonLocalACController`) and commands `ppms_environment_controller` (`PPMSRemoteReadbackController` wrapping `PPMSQDInstrumentReadbackController` or `PPMSMultiVuReadbackController`).
  4. **Persistence & Display**: Each point flushes + `os.fsync`s to `ppms_live_csv_path` (`_append_ppms_live_csv`, `L9889`), appends to `ScanRunRecorder` JSONL, and calls `self.post_ui(...)` → `ui_queue` → `drain_ui_queue` (`L10196`) to update `_redraw_ppms_transport_plot`.
- **Cancellation / Error / Close**:
  - `stop_ppms_field_scan` (`L9878`) sets `ppms_field_scan_stop_event` and `ppms_scan_preserve_on_stop = True`; worker exits loop, zeros 6221 (`_ppms_zero_outputs_after_scan`, `L9154`), and preserves CSV/field.
  - Field scan error triggers `_ppms_field_error_after_zero` (`L9867`) → `_ppms_zero_field_after_interrupted_scan` (`L9853`) before raising.
  - `request_close` (`L10275`) sets `self.closing = True`, signals stop events on both `self` and `ppms_panel`, and runs `safe_close_hardware()` on `ppms-safe-shutdown` daemon thread.

### 3. Resource and state ownership
- **Dual-ownership inheritance + delegation**: `PPMSPanel(CombinedControlApp)` (`L10313`) inherits from `CombinedControlApp` **and** delegates unknown non-`ppms`/`zurich` attributes to `self.parent_app` via `__getattr__` (`L10422–10425`).
- **Duplicate initialization**: `CombinedControlApp.__init__` (`L5746`) calls `self._initialize_ppms_state()`, creating an unused set of `ppms_*` `StringVar`s, `ppms_executor`, and `instrument_connection_indicators`; `PPMSPanel.__init__` (`L10319`) does **not** call `super().__init__()`, instead calling `self._initialize_ppms_state()` a second time on `self`. Consequently `request_close` (`L10290–10294`) must iterate `(self, panel)` to set stop events, and `safe_close_ppms` runs twice (once on `ppms_panel`, once on `CombinedControlApp`).
- **Executors & timers**:
  - `CombinedControlApp`: `executor` (`led-io`), `file_executor` (`file-export`), `ui_queue` + `drain_ui_queue` (`root.after(20, ...)`).
  - `PPMSPanel`: `ppms_executor` (from `_initialize_ppms_state`), `zurich_executor` (`ppms-zurich`), `ppms_poll_after_id`, `zurich_after_id`.
  - Note: `ppms_checks.py` emits `invalid command name "...drain_ui_queue"` on stderr during rapid window destruction because `drain_ui_queue` / `after` callbacks fire as `root` is destroyed.

### 4. Classification of inherited / legacy code in `app.py`
| Symbol / Class | Lines in `app.py` | Classification | Evidence & Rationale |
| --- | --- | --- | --- |
| `STFMRSR830Controller` | `L2143–2329` | **Required shared logic** (misleading name) | Instantiated by PPMS at `L8590` for SR830-1/2/3 (`owner_scope` overridden to `"PPMS"` at `L10329`). Do not remove; rename in D1/later PR. |
| `PPMSQDInstrumentReadbackController`, `PPMSRemoteReadbackController`, `PPMSMotorDebug` | `L2332–2967` | **PPMS-specific logic** | Core PPMS QDInstrument/DLL backend, unified remote wrapper, and rotator debug state. |
| `SafeVisaIdentityController`, `ConnectionOverview`, `EditableFolderDialog` | `L469–770`, `L1094–1193`, `L1997–2132` | **Required shared logic** | Active VISA identity/lease controller, connection lamp overview, and folder picker. |
| `LabOneDotNetAdapter`, `LabOneCAPIAdapter`, `LegacyMFLIBridge` | `L1266–1543`, `L3621–3747` | **Required shared logic** (Zurich MFLI) | Used by `NonLocalACController` (`L3758`, `L4215`, `L4333`) for PPMS Zurich MFLI connection/readback. |
| `NonLocalACController` | `L3748–5662` (1,915 L, 63 methods) | **Mixed: 10 external methods (+ `__init__`) called from `app.py`, reaching 17 methods transitively; 46 methods unreachable** | `PPMSPanel` uses `zurich_controller = NonLocalACController(...)` (`L10334`) for MFLI only (`L10428–10901`) and 2 class constants (`L8129`, `L8172`). The 46 Keithley 6221 AC/DC/Pulse/Gate-VI methods are unused in PPMS. |
| `rte_panel` check in `safe_close_ppms` | `L10126` | **Harmless residual** | `self.__dict__.get("rte_panel")` is always `None` in standalone PPMS. |

### 5. Source authority and generator lifecycle
- Currently `transport/ppms/app.py` is **generated** by `tools/extract_app.py` using `tools/ppms_host.py` and `tools/ppms_package_glue.py` from `main`'s `Transport_all.py`.
- Notice that `Transport-PPMS` is now maintained independently (`main` retains `Transport_all.py` only as backup per `main@56908d8`), yet `tools/extract_app.py` cannot regenerate `app.py` from within `Transport-PPMS` alone because `Transport_all.py` is not in this branch.
- **Trade-offs (for D1 decision, not decided here)**:
  - *Option A (Recommended for D1)*: Declare `transport/ppms/app.py` (and extracted `transport/ppms/*` modules) as the **authoritative source** on `Transport-PPMS`, retiring `tools/extract_app.py` regeneration from `main` once extraction is frozen. Pros: enables clean modularization (`app.py` → controllers/services/UI) without fighting AST extraction. Cons: one-way divergence from `main`'s `Transport_all.py`.
  - *Option B*: Keep `extract_app.py` + `ppms_host.py` + `ppms_package_glue.py` synchronized with `main`. Pros: preserves single-file `Transport_all.py` sync. Cons: blocks structural refactoring inside `transport/ppms/app.py` (like splitting `NonLocalACController` or fixing `PPMSPanel`/`CombinedControlApp` dual init).

---

## Findings
| ID | Priority | Evidence (baseline SHA, file:line / test / log) | Impact | Proposed follow-up | Status |
| --- | --- | --- | --- | --- | --- |
| A1-1 | **P2** | `main@56908d8:Other-files/test/test_instrument_safety_logic.py` L1668–2404 deleted in `8369a29e` | `PPMSQDInstrumentReadbackController` (436 L) and `SharedVisaManager` disconnect/IDN-fail paths have 0 unit tests in `Transport-PPMS`. | Restore PPMS/QDInstrument/SharedVisa unit tests into standalone `Other-files/test_ppms_qdinstrument.py` & `test_visa_shared_manager.py`. | Open (non-blocking for D1) |
| A1-2 | **P2** | `main@56908d8:Other-files/test_scan_plot_snapshots.py` L375–391 (`PpmsTests`) deleted in `8369a29e` | PPMS PNG plot snapshot saving (`run1_plots_snapshot.png`, `plot_snapshots` event) is no longer verified by tests. | Restore `PpmsTests` into `Other-files/test_ppms_scan_plot_snapshots.py`. | Open (non-blocking for D1) |
| A2-1 | **P1** (Arch) | `8369a29e:transport/ppms/app.py` L5746 vs L10319 (`_initialize_ppms_state` called twice), L10313 (`PPMSPanel(CombinedControlApp)`), L10422 (`__getattr__`) | Dual ownership of `ppms_*` Tk vars, `ppms_executor`, and `instrument_connection_indicators` complicates close/stop logic and blocks clean testing. | In D1, replace inheritance + `__getattr__` delegation with explicit composition (single state owner, single executor owner). | Proposed for D1 |
| A2-2 | **P2** (Arch) | `8369a29e:transport/ppms/app.py` L3748–5662 (`NonLocalACController`, 1,915 L) vs L10334–10901 | 46 unused NonLocal 6221 AC/DC/Pulse methods remain bundled in `app.py`; `STFMRSR830Controller` (`L2143`) carries misleading `STFMR` name. | In D1, extract a focused `ZurichMFLIController` (wrapping `LabOne*Adapter`/`LegacyMFLIBridge`) and rename/extract `SR830Controller`. | Proposed for D1 |
| A2-3 | **P2** | `8369a29e:transport/ppms/app.py` L10894–10903 (`PPMSPanel.safe_close_ppms`) vs L10178 (`ScanCloseHandoff`) | `zurich_executor.submit(disconnect_mfli)` and `shutdown(wait=False)` run before `super().safe_close_ppms()` checks `ScanCloseHandoff`, risking concurrent MFLI disconnect while an active scan finishes. | Coordinate `zurich_controller.disconnect_mfli` with `ScanCloseHandoff` in D1 close lifecycle. | Proposed for D1 |
| A2-4 | **P3** | `8369a29e:transport/ppms/app.py` L10196–10227 (`drain_ui_queue`), observed in `ppms_checks.py` stderr (`invalid command name "...drain_ui_queue"`) and `text cut ['Sample, ranges, wiring and m']` at `1920x1080` (`ui_scale 0.86`) / `1600x1586` (`ui_scale 1.18`) | Harmless Tk `after` teardown noise and minor label clipping on narrower/scaled layouts. | Cancel `drain_ui_queue` `after` ID on close and adjust label width/text if needed in UI cleanup. | Deferred / P3 |
| A1-3 | **P3** | `8369a29e:transport/ppms/app.py` L1877–1996 vs L10786 (`connect_mfli`) | Cross-program lease protects VISA resources (`SafeVisaIdentityController`) on Windows/Mac, but Zurich MFLI (`NonLocalACController`) does not acquire a cross-program lease. | Evaluate adding MFLI device ID to cross-program lease in a follow-up if two programs share one MFLI. | Proposed for D1/follow-up |

---

## Verification
- **Local macOS (Python 3.11.3, `pytest 9.0.3` available; no real hardware)**:
  - `cd Other-files && python3 ci/run_suite.py`: **46 modules, 610 tests ran, 0 failed, 3 skipped** (exit 0).
  - `python3 Other-files/ci/ppms_checks.py --shots <scratch>/shots`: **PPMS CHECKS OK** (exit 0; 2 `note` items on label clipping at `1920x1080` / `1600x1586`; `drain_ui_queue` teardown Tcl message on stderr).
  - `python3 -c "import Transport_PPMS, transport.ppms.app as a; print('APP_VERSION:', a.APP_VERSION)"`: `APP_VERSION: 1.0` (exit 0).
- **GitHub Actions CI (`Jiaze-Li/Transport-all`)**:
  - `Transport-PPMS@8369a29e117e643bb9a3e38a5fc795b473f5dfe8`:
    - `PPMS tests` ([run 45996570966](https://github.com/Jiaze-Li/Transport-all/actions/runs/45996570966)): **success** (`linux` suite + layout + wheel build/install, `windows` suite + layout).
    - `PPMS Windows exe` ([run 45996570863](https://github.com/Jiaze-Li/Transport-all/actions/runs/45996570863)): `build` job **success** (PyInstaller `.exe` + one-folder, `--package-self-test`, window startup check); `release` job **failed** only because tag `ppms-v1.0` was not pushed in the `Jiaze-Li/Transport-all` fork.
  - `audit/ppms-extraction-and-architecture@5e5430b070fab0645cef6c26dda8f3cfa55c2085`:
    - `PPMS tests` ([run 45999165068](https://github.com/Jiaze-Li/Transport-all/actions/runs/45999165068)): **success**.
- **Unverified / out of scope**: Live hardware communication (PPMS MultiVu/QDInstrument, Keithley 6221/2400, SR830, Zurich MFLI) and physical lab PC execution were not performed.

---

## Exit decision
- **A1 (Extraction integrity)**: **PASS** (with 2 non-blocking P2 test-restoration follow-ups: `test_scan_plot_snapshots.py` PPMS case and `test_instrument_safety_logic.py` QDInstrument/SharedVisa cases).
- **A2 (Internal architecture)**: **Complete** — core pure-logic/recording/protocol/MultiVu modules (`~1,002 L`) are cleanly decoupled; remaining `10,926 L` in `app.py` has clear refactoring targets (`PPMSPanel`/`CombinedControlApp` dual init, `NonLocalACController` MFLI split, `ScanCloseHandoff` + Zurich close ordering, `STFMRSR830Controller` naming).
- **Design readiness (D1)**: **Safe to proceed to a separate D1 modularization design PR** (recommended: freeze `tools/extract_app.py` regeneration and make `transport/ppms/*` the authoritative source, while restoring the missing A1-1/A1-2 unit tests alongside or ahead of D1).
- **Hardware acceptance**: Not performed by this audit.
