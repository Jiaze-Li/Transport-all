# D1 — PPMS Modular Architecture Design

Status: **Pending design proposal and human decisions** (Draft PR #4; no production code or behavior changes).

Baseline: `Transport-PPMS@56d0584493253468e29e9b1163bf92e89d244e84` (PR #3 merged). Reference backup: `main@56908d88feb69597499b1c270bcf9a0f88b26972`.

Evidence: [A1/A2 audit](PPMS_EXTRACTION_ARCHITECTURE_AUDIT.md), [T1 regression baseline and A2-3 fix](PPMS_REGRESSION_BASELINE.md), and direct static inspection of `transport/ppms/app.py` (10,940 lines), `tools/extract_app.py`, `tools/ppms_host.py`, and `tools/ppms_package_glue.py`.

---

## Executive Summary

`Transport-PPMS` currently runs cleanly on offline CI (51 test modules, 640 tests, plus layout and Windows `.exe` checks) and has resolved the A2-3 Zurich shutdown race in PR #3. However, **90.9% of the `transport/` package (10,940 lines) still lives inside the generated monolith `transport/ppms/app.py`**, while the cleanly decoupled modules (`transport/core/*`, `transport/common/*`, `transport/ppms/protocol.py`, `transport/ppms/controllers/multivu.py`) account for ~1,002 lines.

This D1 proposal establishes **clear module boundaries, single ownership, and dependency direction** without changing PPMS measurement semantics, UI interactions, instrument commands, safety invariants, or output formats:

1. **Recommended Source Authority (Decision Q1 — requires your approval):** Freeze `tools/extract_app.py` regeneration and make `transport/ppms/*` the **authoritative source** on `Transport-PPMS`, leaving `main@56908d88` as a read-only backup/reference and `tools/*` as historical extraction provenance (until retired after D2). Continuing AST regeneration from `main` blocks structural refactoring (as already seen in PR #3 needing a fragile literal `REPLACE` patch in `tools/ppms_host.py`).
2. **Replace Dual Inheritance/Delegation with Explicit Composition:** Eliminate `PPMSPanel(CombinedControlApp)` inheritance, `__getattr__` attribute forwarding, and duplicate `_initialize_ppms_state()` calls. Introduce a single `PPMSAppState` / `PPMSSession` owner composed by the Tk shell (`AppShell`) and `PPMSPanel`.
3. **Separate Instrument Controllers, Measurement Engine, and Tk UI:**
   - Move VISA/SCPI (`SafeVisaIdentityController`, `SR830Controller`), Zurich MFLI (`ZurichMFLIController` + LabOne/legacy adapters), QDInstrument/Remote environment controllers, and motor control out of `app.py` into `transport/ppms/controllers/` (and shared `transport/common/` where appropriate).
   - Isolate scan planning, execution, acquisition, and recording behind explicit typed contracts (`ScanRequest`, `ScanEventSink`, `ScanController`) with **zero `tkinter` imports** in measurement or controller modules.
4. **Preserve Concurrency and Safety Invariants:** Keep `ppms_executor` (5 workers) and `zurich_executor` (1 worker) semantics intact, protect hardware I/O with per-controller locks, preserve `ScanCloseHandoff` (including PR #3's `zurich_executor` drain + `disconnect_mfli` ordering), output-off readback, field zeroing, and CSV/JSONL/PNG snapshot compatibility.

### Decisions Requiring Your Approval vs. Delegated Engineering Details

| Category | Item | Owner |
| --- | --- | --- |
| **Needs human decision (Q1)** | **Source authority:** Make `transport/ppms/*` authoritative on `Transport-PPMS` vs. keep `tools/extract_app.py` regeneration from `main` | **User** |
| **Needs human decision (Q2)** | **Legacy `NonLocalACController` dead-method policy:** Deprecate/remove the 27 unreferenced non-PPMS methods during D2 after test verification vs. quarantine/keep in a legacy shim | **User** |
| **Needs human decision (Q3)** | **D2 test-harness compatibility:** Keep `CombinedControlApp` / `PPMSPanel` attribute compatibility shims during D2 vs. allow updating internal-only test hooks as modules split | **User** |
| **Engineering details (D2)** | Exact file names under `transport/ppms/`, dataclass field names, internal helper decomposition, adapter signatures | AI (within D1 contracts) |
| **Established facts (Audit/Source)** | 10 scan modes, PR #3 `ScanCloseHandoff` fix, `PPMSPanel` dual init, `NonLocalACController` caller counts, 51-module CI baseline | Verified from source |

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
| `ppms_field_scan_stop_event` | Both `CombinedControlApp` and `PPMSPanel` | `PPMSAppState` | Single `threading.Event`; set by Stop button or `request_close` |
| `ppms_close_handoff_state` | `CombinedControlApp.__dict__` | `PPMSAppState.close_handoff` | `ScanCloseHandoff` coordinating worker `begin()`/`end()` with `defer(cleanup)` |
| `ppms_instrument_controllers` (`6221`, `830_a/b/c`, `2400`) | Created twice | `PPMSAppState.instruments` | Each `SafeVisaIdentityController` owns its `threading.RLock`/lock & lease |
| `zurich_controller` | `PPMSPanel` | `PPMSAppState.zurich` | `ZurichMFLIController` owns `lock` and `LABONE::<dev>` lease |
| `ppms_environment_controller` | Created twice | `PPMSAppState.environment` | `PPMSRemoteReadbackController` owns backend lock & connection |
| `ppms_run_recorder` | `CombinedControlApp` / `PPMSPanel` | `PPMSScanRunner` (active run) | Thread-safe `ScanRunRecorder` (internal lock); closed once at run end |
| `closing` flag | `CombinedControlApp` | `AppShell` + `PPMSAppState.closed` | Read-only check passed to workers/UI callbacks |
| Tk `StringVar`s, widgets, `after` IDs | `CombinedControlApp` + `PPMSPanel` | `PPMSPanelView` (`ui/panel.py`) | Mutated **only** on Tk main thread |

> **Explicit note on concurrency:** Having `PPMSAppState` as the single state/lifecycle owner does **not** mean collapsing `ppms_executor` (5 workers) and `zurich_executor` (1 worker) into a single worker. Live PPMS polling, motor commands, and Zurich polling continue to run concurrently with each other, synchronized by per-controller locks and `ScanCloseHandoff`.

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
   `PPMSPanelView` reads `StringVar`s → `planning.build_scan_request(...)` validates via `protocol.py` & `validation.py` → `AppState.ppms_executor.submit(runner.run, request, sink)` inside `close_handoff.begin()` → `runner` iterates `harmonic_passes` (`common/harmonics.py`), executes nested temperature/angle/field/frequency loops, acquires via `SafeVisaIdentityController` / `SR830Controller` / `ZurichMFLIController` locks, writes durable CSV + JSONL via `ScanRunRecorder`, and pushes `ScanEventSink` callbacks to `UiDispatcher` → `finally`: `_zero_6221_with_readback()`, optional `zero_persist` wrap-up, `save_scan_plot_snapshots` (on UI thread before `run_finished`), `recorder.close()`, `close_handoff.end()`.
2. **Macro:**
   `MacroRunner` executes queued macro steps on `ppms_executor`, invoking `PPMSScanRunner` / `PPMSMotorService` / `environment` sequentially while respecting `stop_event` and `closing`.
3. **Stop vs. Close:**
   - **Stop button:** Sets `AppState.stop_event` (`preserve_on_stop = True`); worker exits loop cleanly, zeros 6221 (read back), preserves field and saved CSV/log.
   - **Window Close (`request_close`):** Confirms with user → sets `AppShell.closing = True` and `AppState.stop_event` → spawns `ppms-safe-shutdown` daemon → `safe_close_ppms` cancels `ppms_poll_after_id` & `zurich_after_id`, shuts down `zurich_executor(wait=False, cancel_futures=True)`, and calls `close_handoff.defer(cleanup)`. If a scan is active/queued, `cleanup(after_worker=True)` runs at `close_handoff.end()` after worker finishes output/field zeroing and `recorder.close()`, then waits for `zurich_executor.shutdown(wait=True, cancel_futures=True)` and disconnects 6221/SR830/PPMS env/Zurich MFLI.

---

## 4. Decisions Requiring Human Approval

### Decision Q1: Source Authority and Extraction Lifecycle (Blocking for D2)
- **Context:** `transport/ppms/app.py` is currently generated by `tools/extract_app.py` from `main:Transport_all.py` (which is not on `Transport-PPMS`). PR #3 already required a literal `REPLACE` patch in `tools/ppms_host.py`.
- **Option A (Recommended):** Declare `transport/ppms/*` as the **authoritative source** on `Transport-PPMS`. Freeze `tools/extract_app.py` regeneration immediately; keep `main@56908d88` as historical backup/reference; keep `tools/extract_app.py`, `ppms_host.py`, `ppms_package_glue.py` as provenance during D2 and retire/archive them after D2 completes.
  - *Pros:* Unblocks clean module extraction, composition, and controller separation without fighting AST reachability or `REPLACE` patches.
  - *Cons:* One-way divergence from `main:Transport_all.py` (already intended by `main@56908d8` backup status). Other standalone branches (`Transport-FMR`, `Transport-MOKE`, `Transport-RT`) are unaffected unless they independently choose the same route.
- **Option B:** Keep `tools/extract_app.py` regeneration active against `main`.
  - *Pros:* Preserves mechanical re-extraction from `Transport_all.py`.
  - *Cons:* Prohibits splitting `app.py`, fixing `PPMSPanel(CombinedControlApp)` dual init cleanly, or refactoring `NonLocalACController` without duplicating every change in `main` or `REPLACE` blocks.
- **Rollback:** `Transport-PPMS@56d0584` and `main@56908d8` remain untouched in git history.

### Decision Q2: Treatment of 27 Unreferenced Methods in `NonLocalACController`
- **Option A (Recommended):** During D2, extract `ZurichMFLIController` with the 16 app-called + 19 test/internal-verified methods, and remove the remaining 27 non-PPMS methods (2182A nanovoltmeter, non-local 6221/SR830) **only after** static call-graph + full CI test verification confirms zero dynamic/test callers.
- **Option B:** Retain all 63 methods in a legacy module (`_legacy_nonlocal_ac.py`) indefinitely.
  - *Trade-off:* Option A removes dead non-PPMS code cleanly; Option B has zero deletion risk but keeps ~1,000 lines of unused Non-local code.

### Decision Q3: D2 Compatibility Shims for Existing Tests
- **Option A (Recommended):** Keep thin re-exports/attributes on `CombinedControlApp` and `PPMSPanel` (and `STFMRSR830Controller = SR830Controller`) during intermediate D2 PRs so the 51 existing test modules continue passing unmodified; update internal-only test monkeypatches only in the final cleanup PR if needed.
- **Option B:** Rewrite test imports/monkeypatches alongside every module extraction.
  - *Trade-off:* Option A preserves stronger regression confidence across each intermediate PR.

---

## 5. Migration Feasibility and Verification (High-Level D2 Constraints)

Exact batch/task breakdown belongs to **D2** after D1 approval. High-level dependency order and intermediate safe states:

1. **Gate 0 (Prerequisite):** Human approval of D1 decisions (especially Q1 Source Authority). PR #3 (A2-3) is already merged.
2. **Stage 1 — Mechanical Controller Extraction (Behavior-Preserving):**
   - Extract `SafeVisaIdentityController`, `SR830Controller` (with `STFMRSR830Controller` alias), `PPMSQDInstrumentReadbackController`, `PPMSRemoteReadbackController`, and `ZurichMFLIController` (+ LabOne/legacy adapters) into `transport/ppms/controllers/`. Re-export from `app.py` for compatibility.
   - *Risk:* Low (mechanical move + re-export). *Rollback:* Revert single PR.
3. **Stage 2 — Pure Planning, Motor, and Macro Extraction:**
   - Extract `engine/planning.py`, `engine/motor.py`, and `engine/macro.py` with zero `tkinter` imports; `app.py` delegates to them.
   - *Risk:* Low–Medium (must preserve exact `validate_ppms_*` error messages and `ScanRequest` snapshot semantics).
4. **Stage 3 — State Ownership & Composition (`PPMSAppState` + `PPMSScanRunner`):**
   - Replace `PPMSPanel(CombinedControlApp)` inheritance, `__getattr__`, and dual `_initialize_ppms_state()` with explicit composition (`AppShell` + `PPMSAppState` + `PPMSPanelView`).
   - *Risk:* Medium–High (touches close lifecycle, `ScanCloseHandoff`, `drain_ui_queue`, and monkeypatched test attributes). In particular, normal scan completion runs `save_scan_plot_snapshots` and `recorder.close()` via `post_ui` (`L9277–9281`), whereas `closing=True` closes `recorder` directly on the worker thread (`L9244`) before `ScanCloseHandoff.end()`; Stage 3 must preserve `recorder.close()` guarantees even if `root.destroy()` runs concurrently. Requires behavior-preserving adapters on `CombinedControlApp`/`PPMSPanel` during transition.
5. **Stage 4 — Cleanup & Generator Retirement:**
   - Remove temporary compatibility shims (if approved under Q3) and retire `tools/extract_app.py` / `ppms_host.py` / `ppms_package_glue.py` (if approved under Q1).

### Regression Gates (Every D2 PR)
- **GitHub Actions CI (`ppms-tests.yml`):** Full 51-module suite on Linux (`xvfb-run`) and Windows (`python ci/run_suite.py`), `ppms_checks.py` layout & screenshot checks, wheel build/install, and Windows `.exe` `--package-self-test` when packaging/entrypoints change.
- **Local macOS (only when explicitly authorized):** `cd Other-files && python3 ci/run_suite.py --no-gui` (47 non-GUI modules, 596+ tests, zero Tk popups).
- **Hardware Acceptance:** Offline CI does **not** prove physical hardware behavior; live lab-PC verification (6221, SR830, Zurich MFLI, PPMS MultiVu/QDInstrument, rotator) remains required before production lab deployment.

---

## 6. Decision Status

Pending human review of **Q1–Q3** and architectural boundaries. No production-code, test, workflow, or generator changes are authorized by this document.
