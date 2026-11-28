# D1 — PPMS Modular Architecture Design

Status: **Converged architecture proposal (Draft PR #4 — awaiting final user sign-off on updated D1 document; no production code or behavior changes)**.

Baseline: `Transport-PPMS@56d0584493253468e29e9b1163bf92e89d244e84` (PR #3 merged). Reference backup: `main@56908d88feb69597499b1c270bcf9a0f88b26972`.

Evidence: [A1/A2 audit](PPMS_EXTRACTION_ARCHITECTURE_AUDIT.md), [T1 regression baseline and A2-3 fix](PPMS_REGRESSION_BASELINE.md), and direct static inspection of `transport/ppms/app.py` (10,940 lines), `tools/extract_app.py`, `tools/ppms_host.py`, and `tools/ppms_package_glue.py`.

---

## Executive Summary

`Transport-PPMS` currently runs cleanly on offline CI (51 test modules, 640 tests, plus layout and Windows `.exe` checks) and has resolved the A2-3 Zurich shutdown race in PR #3. However, **90.9% of the `transport/` package (10,940 lines) still lives inside the generated monolith `transport/ppms/app.py`**, while the cleanly decoupled modules (`transport/core/*`, `transport/common/*`, `transport/ppms/protocol.py`, `transport/ppms/controllers/multivu.py`) account for ~1,002 lines.

This D1 proposal establishes **clear module boundaries, single ownership, and dependency direction** without changing PPMS measurement semantics, UI interactions, instrument commands, safety invariants, or output formats:

1. **Source Authority (Q1 — User-agreed direction):** Freeze `tools/extract_app.py` regeneration at the start of D2 and make the `Transport-PPMS` branch (`transport/ppms`, `transport/common`, `transport/core`) the **sole authoritative source** for PPMS. `main@56908d88` remains a read-only backup/reference, and extraction scripts are retired once no dependencies remain (Git history preserves provenance).
2. **Replace Dual Inheritance/Delegation with Explicit Composition:** Eliminate `PPMSPanel(CombinedControlApp)` inheritance, `__getattr__` attribute forwarding, and duplicate `_initialize_ppms_state()` calls. Introduce a focused `PPMSAppState` resource/lifecycle owner (not a new god object) composed by `AppShell` and `PPMSPanelView`.
3. **Separate Instrument Controllers, Measurement Engine, and Tk UI:**
   - Move VISA/SCPI (`SafeVisaIdentityController`, `SR830Controller`), Zurich MFLI (`ZurichMFLIController` + LabOne/legacy adapters), QDInstrument/Remote environment controllers, and motor control out of `app.py` into `transport/ppms/controllers/`.
   - Isolate scan planning, execution, acquisition, Macro, and recording behind explicit typed contracts (`ScanRequest`, `ScanEventSink`, `PPMSScanRunner`, `MacroRunner`) with **zero `tkinter` imports** in measurement or controller modules.
4. **Preserve Concurrency, Cancellation, and Window-Independent Safety Invariants:** Keep `ppms_executor` (5 workers) and `zurich_executor` (1 worker) semantics intact, preserve distinct `ppms_field_scan_stop_event` vs `ppms_macro_stop` cancellation semantics, and ensure data saving (`recorder.close()`) and safety cleanup never depend on the Tk window (`root`) still existing.

### Decisions Status (Q1–Q3) vs. Delegated Engineering Details

| Category | Item | Status / Owner |
| --- | --- | --- |
| **Q1: Source authority** | `Transport-PPMS` (`transport/ppms`, `transport/common`, `transport/core`) becomes sole authoritative source; stop `tools/extract_app.py` at D2 start and retire once unused | **Agreed Option A (User)** |
| **Q2: Legacy cleanup** | Remove Non-local legacy code based on **whether it fulfills an actual PPMS responsibility** (not method/line-count targets); unused features + dedicated historical tests may be removed together after dependency check | **Agreed Refined Option A (User)** |
| **Q3: Test compatibility shims** | Temporary re-exports/aliases allowed only for mechanical extraction; migrate test entrypoints synchronously when responsibilities change without weakening behavioral assertions; every shim requires an explicit removal condition in M4 | **Modified Option A (User)** |
| **Engineering details (D2)** | Exact file names under `transport/ppms/`, dataclass field names, internal helper decomposition, adapter signatures | AI (within D1 contracts) |
| **Established facts (Audit/Source)** | 10 scan modes, PR #3 `ScanCloseHandoff` fix, `PPMSPanel` dual init, `ppms_macro_stop` vs `ppms_field_scan_stop_event`, 51-module CI baseline | Verified from source |

---

## 1. Design Goals, Non-Goals, and Invariants

### Goals
- **Cohesive module boundaries and unidirectional dependencies:** UI depends on application/engine contracts and controllers; measurement engine depends on controller protocols, `transport/core/*`, `transport/common/*`, and `transport/ppms/protocol.py`; controllers and engine have **zero** `tkinter` dependencies.
- **Single authoritative ownership:** Eliminate duplicated state (`_initialize_ppms_state()` called twice), `__getattr__` delegation, and split `closing` / `stop_event` handling.
- **Testability without live hardware or Tk windows:** Measurement logic, scan state transitions, and instrument controllers become directly unit-testable offline under `--no-gui`.
- **Safe maintainability for future PPMS modes and macros:** Adding a scan mode or macro step should not require editing a 10,940-line file.

### Non-Goals
- No production code, test, workflow, or generator changes in D1.
- No changes to PPMS measurement semantics, settling formulas, temperature/field safety limits, SCPI/LabOne/QDInstrument commands, UI layout, or CSV/JSONL/PNG formats.
- No speculative plugin framework, headless CLI product, or cross-branch refactoring of `Transport-FMR`, `Transport-MOKE`, or `Transport-RT`.
- No arbitrary file-count or line-count targets.

### Hard Invariants (Must Hold Across Every D2 Step)
1. **6221 Output-Off Readback:** Every scan end (normal, cancelled, error, close) calls `safe_output_off()` under `controller.lock` and verifies `OUTP? == 0`, raising if non-zero (`_ppms_zero_6221_after_scan`, `L9143–9153`).
2. **Field Zeroing on Interrupted/Closed Field-Commanding Scans:** Modes in `PPMS_FIELD_COMMANDING_MODES` (`Field`, `Field+Temperature`, `Angle+Field`) zero the field on error or program close unless already accepted (`_ppms_zero_field_after_interrupted_scan`, `safe_close_ppms`). Normal user Stop preserves the field.
3. **Temperature & Field Guardrails:** `5–380 K` (`<= 1 K/min` if path enters `5–9 K`, `<= 10 K/min` above `9 K`, staged via `ppms_scan_temperature_segments`), `|B| <= 89,900 Oe`, `0 < rate < 150 Oe/s` (`transport/ppms/protocol.py`).
4. **PR #3 `ScanCloseHandoff` Contract:** `PPMSPanel.safe_close_ppms` sets `zurich_closed = True`, cancels `zurich_after_id`, shuts down `zurich_executor(wait=False, cancel_futures=True)`, and delegates to `ScanCloseHandoff`. `cleanup(after_worker)` waits for `zurich_executor.shutdown(wait=True, cancel_futures=True)` and disconnects `zurich_controller.disconnect_mfli()` **only after** `ScanCloseHandoff.end()` when a scan is active or queued (`L10151–10193`, `L10906–10917`).
5. **Cross-Program Instrument Leases:** `SafeVisaIdentityController` and `ZurichMFLIController` acquire and release OS-level leases (`CreateFileMappingW` on Windows, `fcntl.flock` on POSIX) around device sessions.
6. **Data & Snapshot Compatibility:** Run folder `<parent>/<stem>/` (with `_01` collision suffix), `<stem>.csv` columns/units, `<stem>.log` JSONL events (`run_prepared`, `scan_started`, `harmonic_pass`, `outputs_zeroed`, `plot_snapshots`, `run_finished`), and `run1_plots_snapshot.png` remain unchanged.
7. **Window-Independent Finalization & Safety Cleanup:** Data persistence (`ScanRunRecorder.close()`, live CSV flush/fsync) and hardware safety cleanup (6221 output-off readback, interrupted field zeroing, `ScanCloseHandoff.end()`) **must never depend on the Tk window (`root`) still existing**. Normal scan completion may trigger UI-thread PNG snapshot capture (`save_scan_plot_snapshots`) while `root` is alive, but `recorder.close()` and hardware cleanup must always complete deterministically on the worker/handoff path when `closing=True` or `root` is destroyed, never relying on `post_ui` callbacks during shutdown.
8. **Distinct Scan vs. Macro Cancellation Semantics:** Preserve separate `ppms_field_scan_stop_event` (`L5880`) and `ppms_macro_stop` (`L7288`, `L7387`) events. Stop Macro cancels remaining macro lines while keeping already-sent temperature/field targets; Stop Scan aborts the active scan, zeros 6221, and preserves the field. During `request_close()`, `closing = True` and both stop events are signaled.

---

## 2. Current Architecture and Concrete Coupling

### 2.1 Current Dependency Map (Verified Facts)

```text
Transport_PPMS.py (19 L)
  └── transport/ppms/app.py (10,940 L)  [Generated by tools/extract_app.py]
        ├── Tk Shell + Log + UI Queue (CombinedControlApp, L5724–10324)
        ├── PPMS UI + Zurich UI + Polling (PPMSPanel(CombinedControlApp), L10325–10917)
        ├── Scan Orchestration + Workers (start_ppms_field_scan, _ppms_nested_scan_worker, _ppms_field_scan_worker)
        ├── Motor / Macro (PPMSMotorDebug L2776, _ppms_motor_action, _ppms_macro_operation)
        ├── VISA / SR830 (SafeVisaIdentityController L1997, STFMRSR830Controller L2143)
        ├── PPMS Env (PPMSQDInstrumentReadbackController L2332, PPMSRemoteReadbackController L2922)
        ├── Zurich MFLI (LabOneDotNetAdapter L1266, LabOneCAPIAdapter L1393, LegacyMFLIBridge L3621, NonLocalACController L3748)
        │
        ├──► transport/core/scan_sequences.py (93 L, pure math)
        ├──► transport/core/scan_recording.py (260 L, pure stdlib)
        ├──► transport/core/scan_lifecycle.py (106 L, pure stdlib)
        ├──► transport/common/harmonics.py (110 L, pure stdlib)
        ├──► transport/common/settling.py (65 L, pure stdlib)
        ├──► transport/common/validation.py (98 L, pure stdlib)
        ├──► transport/ppms/protocol.py (101 L, pure stdlib)
        └──► transport/ppms/controllers/multivu.py (169 L, stdlib + protocol + lazy MultiPyVu)
```

### 2.2 Concrete Coupling Pain Points (Source Facts)
1. **Inheritance + `__getattr__` + Dual Initialization (`app.py:L5746`, `L10325–10435`):**
   - `CombinedControlApp.__init__` calls `self._initialize_ppms_state()` (`L5746`), creating `ppms_executor`, `ppms_environment_controller`, `ppms_instrument_controllers`, and ~20 `tk.StringVar`/`BooleanVar`s on the root app instance.
   - `PPMSPanel(CombinedControlApp).__init__` (`L10327`) does **not** call `super().__init__()`; instead it sets `self.parent_app = parent`, `self.root = parent.root`, and calls `self._initialize_ppms_state()` **a second time** on `self`, creating a second unused `ppms_executor` / controller set on `CombinedControlApp` while `PPMSPanel` uses its own.
   - `PPMSPanel.__getattr__` (`L10434`) forwards non-`ppms`/`zurich` attributes to `self.parent_app`.
   - As a result, `request_close()` (`L10303–10309`) must loop over `(self, panel)` to set `ppms_field_scan_stop_event`, and `safe_close_ppms()` (`L10126–10139`) executes twice (once for `ppms_panel`, once for `CombinedControlApp`).
2. **Generated Monolith Friction (`tools/extract_app.py`, `tools/ppms_host.py`):**
   - `transport/ppms/app.py` is produced by AST reachability extraction from `main`'s `Transport_all.py`, which does not even exist on the `Transport-PPMS` branch.
   - Fixing A2-3 in PR #3 required embedding a 24-line literal string replacement inside `tools/ppms_host.py:REPLACE` (`L61–87`) because `PPMSPanel` is copied whole by `extract_app.py`.
3. **Interleaved Tk State and Scan Execution:**
   - `start_ppms_field_scan` (`L8727`) reads `tk.StringVar`s, validates inputs, builds axes, and launches `_ppms_scan_with_final_field` (`L9185`).
   - Workers (`_ppms_nested_scan_worker`, `_ppms_field_scan_worker`) directly read/mutate `self.__dict__` state (`ppms_last_readback`, `ppms_zero_persist_pending`, `ppms_scan_field_zero_accepted`) and post closures mutating Tk `StringVar`s and widgets (`ppms_zero_persist_button`, `ppms_field_scan_status_var`).
4. **Oversized Legacy Controller (`NonLocalACController`, `L3748–5662`, 1,915 lines, 63 methods):**
   - Bundles Keithley 6221, Keithley 2182A nanovoltmeter, SR830, and Zurich MFLI logic from `main`'s Non-local page. PPMS only uses its MFLI subset (`zurich_controller = NonLocalACController(...)`, `L10346`) and 2 class constants.

---

## 3. Target Architecture and Contracts

### 3.1 Target Module Map & Dependency Direction

```text
Transport_PPMS.py (entrypoint)
  └── transport/ppms/app_shell.py (Composition Root: Tk root, Notebook [PPMS, Log], ui_queue, request_close)
        ├── transport/ppms/ui/panel.py (PPMSPanelView: Tk widgets, StringVars, plot canvases, polling timers)
        ├── transport/ppms/ui/dialogs.py (EditableFolderDialog, ConnectionOverview, layout helpers)
        ├── transport/ppms/state.py (PPMSAppState: single owner of executors, stop_event, ScanCloseHandoff, controllers)
        ├── transport/ppms/engine/
        │     ├── planning.py (ScanRequest, ScanPlan, validation/axis/pass planning — pure Python)
        │     ├── runner.py (PPMSScanRunner: nested & field scan execution, settling, acquisition — zero Tk)
        │     ├── macro.py (MacroRunner: macro sequence execution & validation — zero Tk)
        │     └── motor.py (PPMSMotorService: motor debug state & commands — zero Tk)
        └── transport/ppms/controllers/
              ├── visa.py (SafeVisaIdentityController, lease management)
              ├── sr830.py (SR830Controller — renamed from STFMRSR830Controller)
              ├── zurich.py (ZurichMFLIController + LabOneDotNetAdapter, LabOneCAPIAdapter, LegacyMFLIBridge)
              ├── qdinstrument.py (PPMSQDInstrumentReadbackController, QDCommandUncertain)
              ├── environment.py (PPMSRemoteReadbackController)
              └── multivu.py (existing PPMSMultiVuReadbackController — keep as-is)

Shared pure modules (UNCHANGED, zero Tk, zero app imports):
  transport/core/{scan_sequences, scan_recording, scan_lifecycle}.py
  transport/common/{harmonics, settling, validation}.py
  transport/ppms/protocol.py
```

**Prohibited Reverse Dependencies:**
- `transport/core/*`, `transport/common/*`, `transport/ppms/protocol.py` **must never** import `transport/ppms/engine/*`, `controllers/*`, `ui/*`, or `tkinter`.
- `transport/ppms/controllers/*` and `transport/ppms/engine/*` **must never** import `tkinter` or `transport/ppms/ui/*`.
- `transport/ppms/ui/*` **must never** issue raw VISA/LabOne/QDInstrument commands directly; it calls `PPMSScanRunner` / `PPMSMotorService` / `controllers` via `PPMSAppState` and receives updates via `UiDispatcher` (`post_ui`).

### 3.2 State and Resource Ownership Map (Single Owner per Resource)

| Resource / State | Current Owner (Split) | Target Single Owner | Concurrency / Threading Rule |
| --- | --- | --- | --- |
| `ui_queue` & `drain_ui_queue` | `CombinedControlApp` | `AppShell` (`UiDispatcher`) | Drained only on Tk main thread; `after` ID cancelled on close |
| `ppms_executor` (5 workers) | Created twice (`_initialize_ppms_state`) | `PPMSAppState` | Shared pool for scans, env polling, motor, connect; hardware safety via per-controller locks |
| `zurich_executor` (1 worker) | `PPMSPanel` | `PPMSAppState` | Dedicated single worker for MFLI connect/configure/poll; drained in `cleanup()` |
| `file_executor`, `executor` | `CombinedControlApp` | `AppShell` | Shut down in `request_close` after `safe_close_hardware()` |
| `ppms_field_scan_stop_event` | Both `CombinedControlApp` and `PPMSPanel` | `PPMSAppState.scan_stop_event` | Single scan `threading.Event`; set by Stop Scan or `request_close` |
| `ppms_macro_stop` | `CombinedControlApp` (`L7288`) | `PPMSAppState.macro_stop_event` | Separate macro `threading.Event`; stops remaining macro lines only |
| `ppms_close_handoff_state` | `CombinedControlApp.__dict__` | `PPMSAppState.close_handoff` | `ScanCloseHandoff` coordinating worker `begin()`/`end()` with `defer(cleanup)` |
| `ppms_instrument_controllers` (`6221`, `830_a/b/c`, `2400`) | Created twice | `PPMSAppState.instruments` | Each `SafeVisaIdentityController` owns its `threading.RLock`/lock & lease |
| `zurich_controller` | `PPMSPanel` | `PPMSAppState.zurich` | `ZurichMFLIController` owns `lock` and `LABONE::<dev>` lease |
| `ppms_environment_controller` | Created twice | `PPMSAppState.environment` | `PPMSRemoteReadbackController` owns backend lock & connection |
| `ppms_run_recorder` | `CombinedControlApp` / `PPMSPanel` | `PPMSScanRunner` (active run) | Thread-safe `ScanRunRecorder` (internal lock); closed once at run end |
| `closing` flag | `CombinedControlApp` | `AppShell` + `PPMSAppState.closed` | Read-only check passed to workers/UI callbacks |
| Tk `StringVar`s, widgets, `after` IDs | `CombinedControlApp` + `PPMSPanel` | `PPMSPanelView` (`ui/panel.py`) | Mutated **only** on Tk main thread |

> **Explicit notes on ownership and concurrency:**
> 1. **Registry, not a god object:** `PPMSAppState` is a focused resource/lifecycle registry (executors, controllers, `ScanCloseHandoff`, `scan_stop_event`, `macro_stop_event`, `closed`), **not** a container for scan algorithms, UI state, or hardware protocols. Business logic stays in `PPMSScanRunner`, `MacroRunner`, `PPMSMotorService`, and the individual controllers.
> 2. **Concurrency preserved:** Having `PPMSAppState` as the single lifecycle owner does **not** collapse `ppms_executor` (5 workers) and `zurich_executor` (1 worker) into a single worker. Live PPMS polling, motor commands, and Zurich polling continue to run concurrently, synchronized by per-controller locks and `ScanCloseHandoff`.

### 3.3 Instrument-Control Boundary & Legacy Classification

#### Instrument Boundaries
- **VISA / Lease Layer (`controllers/visa.py`):** `SafeVisaIdentityController` + `SharedVisaManager` + OS-level `acquire_instrument_lease` / `release_instrument_lease`. Handles `*IDN?` verification, safe disconnect without closing sibling sessions on shared `ResourceManager`, and safe output-off commands.
- **Keithley 6221 & SR830 (`controllers/sr830.py`, `SafeVisaIdentityController`):**
  - Rename `STFMRSR830Controller` → `SR830Controller` (keep `STFMRSR830Controller = SR830Controller` alias during D2 transition if tests import the old name).
  - **Caller classification for `STFMRSR830Controller` (9 methods in `app.py:L2143–2329`):**
    - Called in `app.py` + tests: `connect` (`L2152`), `configure` (`L2164`).
    - Called internally / in tests (`test_lockin_downrange.py`, `test_review_fixes.py`): `_auto_range_locked` (`L2251`), `read_sample` (`L2302`).
    - Internal helper: `_numbers` (`L2159`).
    - Unreferenced in `app.py` or retained tests: `wait_for_reference` (`L2221`), `new_pass` (`L2291`), `auto_phase` (`L2295`) — retain on `SR830Controller` if part of lock-in contract or verify against `test_lockin_downrange.py` before any pruning.
- **Zurich MFLI (`controllers/zurich.py`):** Extract `ZurichMFLIController` together with `LabOneDotNetAdapter`, `LabOneCAPIAdapter`, and `LegacyMFLIBridge`.
  - **Caller classification for `NonLocalACController` (63 methods in `app.py:L3748–5662`, verified by AST/regex scan):**
    - **16 methods (+ `__init__`) called outside class in `app.py`:** `connected`, `mfli_connected`, `configure_second_harmonic`, `configure_third_harmonic`, `disable_unused_ppms_demods`, `connect_mfli`, `configure_mfli`, `forget_mfli_range_guard` (via `getattr` at `L8566`), `read_mfli`, `read_mfli_four_demods`, `connect`, `configure`, `read_stfmr_mfli`, `mfli_has_multi_demod`, `disconnect`, `disconnect_mfli`. (Note: `connect`/`configure`/`disconnect` are thin wrappers or multi-instrument dispatchers used by PPMS Zurich setup/teardown.)
    - **19 methods called in `Other-files/test_*.py` only (or internally):** `any_connected`, `nanovoltmeter_connected`, `source_connected`, `connected_lockin_keys`, `_write_sr830`, `_query_sr830_status`, `connect_lockin`, `_mfli_reachable_servers`, `_mfli_server_devices`, `_mfli_connect_failure_message`, `wait_references`, `_wait_mfli_reference`, `_run_mfli_autorange`, `_auto_range_mfli`, `_auto_range`, `_read_sample_one`, `set_lockin_harmonic`, `output_off`, `confirm_output_off`.
    - **27 methods unreferenced outside class in `app.py` or `Other-files/test_*.py`:** Primarily Non-local 6221/2182A/SR830 methods (`_write_2182`, `configure_nanovoltmeter`, `read_nanovoltmeter`, `connect_source`, `connect_nanovoltmeter`, `set_current`, `read_samples`, `update_amplitude`, `disconnect_source`, `disconnect_lockin`, `disconnect_nanovoltmeter`, etc.) plus internal helpers (`_query_float`, `_mfli_scalar`, `_mfli_phase_degrees`, etc.).
    - **Rule:** Static non-reference alone is **not** sufficient deletion evidence. In D2, move the MFLI methods + test-verified helpers into `ZurichMFLIController`; for the 27 unreferenced methods, verify internal call graph first and apply Decision Q2 (deprecate/remove vs. legacy shim).
- **PPMS Environment & Motor:** `PPMSQDInstrumentReadbackController`, `PPMSMultiVuReadbackController` (already in `controllers/multivu.py`), `PPMSRemoteReadbackController`, and `PPMSMotorDebug` move into `controllers/` and `engine/motor.py` without changing QDInstrument reflection, `MultiPyVu` calls, or `protocol.py` guardrails.

### 3.4 Measurement Engine and UI Contract

#### Captured-at-Start vs. Live State
- **Captured at Start (immutable `ScanRequest` / `ScanPlan`):** `scan_mode`, axis values (`sequence`, `temperatures`, `outer_fields`, `frequencies`), `rate_oe_s`, `approach_code`, `stable_s`, `acquisition_s`, `temperature_rate`, `temperature_delay_s`, `fixed_field` (including `_ppms_field_read_at_start()`), `fixed_temp`, `fixed_angle`, `transport_settings` (current, frequency, compliance, active lock-ins, harmonic passes), `zurich_scan_enabled` / `harmonic` / `reference` / `tau`, `run_folder`, `file_stem`.
  - *Rationale:* Ensures reproducible metadata in `run_prepared`, deterministic axis execution, and immunity to user edits in Entry boxes during a scan.
- **Remains Live (thread-safe shared state):** `stop_event` (`ppms_field_scan_stop_event`), `closing` flag, `ppms_zero_persist_pending` (user can queue or cancel Zero+Persist while a scan is running), `ppms_last_readback` (updated by live polling and worker readbacks), `ppms_idle_autorange_enabled`, `ppms_scan_field_zero_accepted`.

#### Illustrative Engine & UI Contracts (Pseudocode)

```python
@dataclass(frozen=True)
class ScanRequest:
    mode: str                          # One of PPMS_SCAN_MODES (10 modes)
    axis_values: tuple[float, ...]
    outer_fields: tuple[float, ...] | None
    temperatures: tuple[float, ...] | None
    frequencies: tuple[float, ...] | None
    field_rate_oe_s: float
    field_approach: int
    settle_s: float
    acquisition_s: float
    temperature_rate_k_min: float
    temperature_delay_s: float
    fixed_field_oe: float
    fixed_temp_k: float
    fixed_angle_deg: float
    transport_settings: dict[str, object]
    zurich_settings: dict[str, object]
    output_dir: Path
    file_stem: str

class ScanEventSink(Protocol):
    """Delivered to UI via UiDispatcher.post_ui; zero Tk types in signature."""
    def on_status(self, text: str) -> None: ...
    def on_environment_readback(self, data: dict[str, object]) -> None: ...
    def on_point_recorded(self, row: dict[str, object], progress: tuple[int, int]) -> None: ...
    def on_outputs_zeroed(self, results: dict[str, str]) -> None: ...
    def on_scan_finished(self, outcome: str, summary: str, error: Exception | None) -> None: ...

class PPMSScanRunner:
    def run(self, request: ScanRequest, sink: ScanEventSink) -> None:
        """Executes under ScanCloseHandoff.begin()/end(); preserves distinct
        _ppms_nested_scan_worker and _ppms_field_scan_worker loops rather than
        forcing all 10 modes into one unproven generic loop."""
```

#### Representative End-to-End Flows
1. **Field / Nested Scan + Harmonics:**
   `PPMSPanelView` reads `StringVar`s → `planning.build_scan_request(...)` validates via `protocol.py` & `validation.py` → `AppState.ppms_executor.submit(runner.run, request, sink)` inside `close_handoff.begin()` → `runner` iterates `harmonic_passes` (`common/harmonics.py`), executes nested temperature/angle/field/frequency loops, acquires via `SafeVisaIdentityController` / `SR830Controller` / `ZurichMFLIController` locks, writes durable CSV + JSONL via `ScanRunRecorder`, and pushes `ScanEventSink` callbacks to `UiDispatcher` → `finally`: `_zero_6221_with_readback()`, optional `zero_persist` wrap-up, UI-thread `save_scan_plot_snapshots` (when `root` is alive) + deterministic `recorder.close()` (never relying on `post_ui` during shutdown), `close_handoff.end()`.
2. **Macro:**
   `MacroRunner` executes queued macro steps on `ppms_executor`, invoking `PPMSScanRunner` / `PPMSMotorService` / `environment` sequentially while respecting `macro_stop_event` (stops remaining lines, keeps sent targets), `scan_stop_event` (when inside a scan), and `closing`.
3. **Stop vs. Close:**
   - **Stop Scan button:** Sets `AppState.scan_stop_event` (`preserve_on_stop = True`); worker exits loop cleanly, zeros 6221 (read back), preserves field and saved CSV/log.
   - **Stop Macro button:** Sets `AppState.macro_stop_event`; stops remaining macro commands while keeping already-sent temperature/field targets.
   - **Window Close (`request_close`):** Confirms with user → sets `AppShell.closing = True` and signals both `scan_stop_event` and `macro_stop_event` → spawns `ppms-safe-shutdown` daemon → `safe_close_ppms` cancels `ppms_poll_after_id` & `zurich_after_id`, shuts down `zurich_executor(wait=False, cancel_futures=True)`, and calls `close_handoff.defer(cleanup)`. If a scan is active/queued, `cleanup(after_worker=True)` runs at `close_handoff.end()` after the worker finishes output/field zeroing and `recorder.close()` (independent of `root`), then waits for `zurich_executor.shutdown(wait=True, cancel_futures=True)` and disconnects 6221/SR830/PPMS env/Zurich MFLI.

---

## 4. Decisions Requiring Human Approval

### Decision Q1: Source Authority and Extraction Lifecycle (Agreed Direction — Option A)
- **Context:** `transport/ppms/app.py` is currently generated by `tools/extract_app.py` from `main:Transport_all.py` (which is not on `Transport-PPMS`). PR #3 already required a literal `REPLACE` patch in `tools/ppms_host.py`.
- **Agreed Route (Option A):** Declare the `Transport-PPMS` branch (`transport/ppms`, `transport/common`, `transport/core`) as the **sole authoritative source** for PPMS. Stop using `tools/extract_app.py` at the start of D2, and retire the extraction scripts once no dependencies remain (Git history preserves provenance). `main@56908d88` remains a read-only backup/reference, and other standalone branches (`Transport-FMR`, `Transport-MOKE`, `Transport-RT`) are unaffected.
- **Alternative Rejected (Option B):** Continuing `tools/extract_app.py` regeneration from `main` blocks splitting `app.py` and fixing dual initialization cleanly.
- **Rollback:** `Transport-PPMS@56d0584` and `main@56908d8` remain untouched in git history.

### Decision Q2: Cleaning Up Non-Local Legacy Code (Agreed Direction — Refined Option A)
- **Agreed Route (Refined Option A):** Judge retention/removal by **whether the code fulfills an actual PPMS program responsibility**, rather than targeting a fixed method or line count. During D2, extract `ZurichMFLIController` for PPMS's MFLI responsibilities; where legacy methods (e.g., Non-local 2182A nanovoltmeter or non-PPMS 6221/SR830 helpers) have no PPMS runtime responsibility and are exercised only by historical non-PPMS tests, remove both the unused production code and its dedicated historical tests together after dependency verification.
- **Alternative Rejected (Option B):** Keeping unused Non-local code indefinitely in a legacy module merely to preserve historical tests.

### Decision Q3: Compatibility Shims During D2 (Agreed Direction — Modified Option A)
- **Agreed Route (Modified Option A):** Protect **measurement behavior and safety contracts**, not old internal class structure (`CombinedControlApp` attributes or `__getattr__` forwarding):
  - Temporary re-exports/aliases (e.g., `STFMRSR830Controller = SR830Controller`) are permitted **only** during mechanical extraction stages.
  - When object responsibilities change (e.g., M3 state/engine separation), migrate test entrypoints synchronously without weakening behavioral assertions.
  - Every compatibility shim must declare an explicit removal condition (retired in M4) and must never become permanent architecture.

---

## 5. Migration Feasibility and Verification (`M0–M4` High-Level D2 Constraints)

Exact batch/task breakdown belongs to **D2** after D1 sign-off. Each stage below is a separate PR verified by GitHub Actions CI:

| Stage | Scope | Risk | Compatibility / Removal Rule |
| --- | --- | --- | --- |
| **M0** | Establish independent source authority on `Transport-PPMS` (freeze `extract_app.py` regeneration) | Low | Prerequisite for M1–M4; `main@56908d8` untouched |
| **M1** | Extract instrument controllers (`visa.py`, `sr830.py`, `zurich.py`, `qdinstrument.py`, `environment.py`) into `transport/ppms/controllers/` | Low–Medium | Temporary re-exports/aliases in `app.py` allowed; remove in M4 |
| **M2** | Separate scan planning (`engine/planning.py`), `PPMSMotorService` (`engine/motor.py`), and `MacroRunner` (`engine/macro.py`) | Medium | Zero `tkinter` imports; preserve `ppms_macro_stop` and `validate_ppms_*` semantics |
| **M3** | Separate `PPMSScanRunner` & UI (`AppShell` + `PPMSPanelView`); unify resource ownership in `PPMSAppState` | High | Migrate test entrypoints synchronously as responsibilities move; guarantee window-independent `recorder.close()` & PR #3 `ScanCloseHandoff` |
| **M4** | Clean up temporary compatibility shims, unused Non-local code/tests (Q2), and retire `tools/` extraction scripts (Q1) | Low–Medium | All M1–M3 shims removed; `PPMSPanel(CombinedControlApp)` / `__getattr__` gone |

### Regression Gates (Every D2 PR)
- **GitHub Actions CI (`ppms-tests.yml`):** Full 51-module suite on Linux (`xvfb-run`) and Windows (`python ci/run_suite.py`), `ppms_checks.py` layout & screenshot checks, wheel build/install, and Windows `.exe` `--package-self-test` when packaging/entrypoints change.
- **Local macOS (only when explicitly authorized):** `cd Other-files && python3 ci/run_suite.py --no-gui` (47 non-GUI modules, 596+ tests, zero Tk popups).
- **Hardware Acceptance:** Offline CI does **not** prove physical hardware behavior; live lab-PC verification (6221, SR830, Zurich MFLI, PPMS MultiVu/QDInstrument, rotator) remains required before production lab deployment.

---

## 6. Decision Status

Updated to reflect human feedback on **Q1–Q3**, the three design principles (`PPMSAppState` registry-not-god-object, separate scan vs. macro stop events, window-independent finalization/close handoff), and `M0–M4` staging. Pending final user sign-off on PR #4. No production-code, test, workflow, or generator changes are authorized by this document.
