# PPMS regression baseline (T1)

Status: **Completed offline baseline & characterization (Draft PR #2, awaiting human acceptance)**.

Reference: accepted A1/A2 audit in [PPMS_EXTRACTION_ARCHITECTURE_AUDIT.md](PPMS_EXTRACTION_ARCHITECTURE_AUDIT.md).

Base: `Transport-PPMS@c0c26345c9a55fa8ef576f1a3952b8190d3c3c8f`; original full-app reference: `main@56908d88feb69597499b1c270bcf9a0f88b26972`.

---

## T1.1 — Restored instrument safety & controller tests (A1-1)

### Source-to-target mapping (`main:Other-files/test/test_instrument_safety_logic.py`)

| Original test / group (`main@56908d88`) | Target in `Transport-PPMS` | Status / Notes |
| --- | --- | --- |
| `test_qdinstrument_pythonnet_readback_shapes` (`L2023–2037`) | `Other-files/test_ppms_qdinstrument.py` | **Restored** (5/5 QDInstrument tests) |
| `test_qdinstrument_explicit_dll_discovery` (`L2039–2049`) | `Other-files/test_ppms_qdinstrument.py` | **Restored** |
| `test_qdinstrument_loader_can_use_dotnet_assembly_name` (`L2051–2077`) | `Other-files/test_ppms_qdinstrument.py` | **Restored** |
| `test_qdinstrument_ref_readback_uses_reflected_argument_types` (`L2079–2151`) | `Other-files/test_ppms_qdinstrument.py` | **Restored** |
| `test_qdinstrument_setfield_uses_reflected_value_types` (`L2153–2249`) | `Other-files/test_ppms_qdinstrument.py` | **Restored** |
| `test_shared_visa_manager_survives_one_controller_disconnect` (`L408–445`) | `Other-files/test_visa_shared_manager.py` | **Restored** (adapted to `owner_scope="PPMS"`, 6221 + SR830) |
| `test_failed_visa_identity_check_does_not_close_other_session` (`L447–478`) | `Other-files/test_visa_shared_manager.py` | **Restored** |
| `test_legacy_labone_bridge_source_still_compiles` (`L2251–2267`) | `Other-files/test_visa_shared_manager.py` | **Restored** (compiles `_MFLI_BRIDGE_SOURCE` + key node assertions) |
| `test_the_zurich_is_set_up_as_on_ppms` (`L2371–2403`) | `Other-files/test_visa_shared_manager.py` | **Restored** (`NonLocalACController.configure` MF-MD Demod 4 + `disable_unused_ppms_demods`) |
| `test_safe_close_hardware_continues_after_module_failure` (`L1896–1924`) | `Other-files/test_visa_shared_manager.py` | **Restored** (adapted to standalone `CombinedControlApp.safe_close_hardware` calling `safe_close_ppms`) |
| `test_ui_queue_drain_is_time_sliced` (`L1926–1956`) | `Other-files/test_visa_shared_manager.py` | **Restored** (`UI_QUEUE_MAX_CALLBACKS_PER_TICK` time-slicing) |
| `test_ppms1_transport_allows_one_sr830_at_address_10` (`L1958–2021`) | `Other-files/test_visa_shared_manager.py` | **Restored** (validation, `_ppms_sr830_address`, `_configure_ppms_transport_scan`, `_read_ppms_transport_sample`, `_stop_ppms_transport_source`) |
| `test_duplicate_resource_is_rejected_until_release` (`L1617–1628`) | Already covered | Covered by `test_common_robustness.py` & `test_instrument_lease_across_programs.py` |
| `test_ppms_default_endpoint_and_readback` (`L1630–1644`) | Already covered | Covered by `test_ppms_multivu_controller.py` |
| `test_ppms1_temperature_and_field_guardrails` (`L1646–1666`) | Already covered | Covered by `test_ppms_protocol_module.py` |
| `test_ppms1_field_scan_returns_to_start_each_loop` (`L1668–1675`) | Already covered | Covered by `test_scan_sequences_module.py` & `test_ppms_protocol_module.py` |
| `test_round_trip_sequence_*` (`L1677–1685`, `L1887–1894`) | Already covered | Covered by `test_scan_sequences_module.py` |
| `test_nice_linear_ticks_*` (`L2269–2288`) | Already covered | Covered by `test_angle_plot_xlim.py` |
| Non-PPMS hardware tests (`2400`, `2410`, `KEPCO 4886`, `LakeShore 475`, `SMB100A`, `PNA N5227A`, `2182A`, `MOKE` camera/magnet, `ST-FMR` sweeps, `6221` pulse/standalone) | Excluded (not in standalone PPMS) | Non-PPMS pages/instruments removed during extraction; `STFMR` / `Non-local` / `MOKE` / `FMR` pages do not exist in `Transport-PPMS`. |

### Mixed-file reconciliations
- `Other-files/test_zurich_choice.py`: Restored PPMS-applicable assertions into `test_ppms_zurich_page_offers_both_lockins_with_the_super_one_marked` and `test_ppms_tries_the_server_that_can_reach_the_chosen_lockin` (ST-FMR, Non-local, and SHHV/RTE assertions excluded because those pages are absent).
- `Other-files/test_ppms_sr830_tc.py`: Restored `PageTests.test_the_zurich_row_says_tc_s_on_ppms` and `StartSettleTests.test_every_choice_waits_1_time_constant_or_settle_s` (PPMS assertions; RTE-only methods remain excluded).
- `Other-files/test_lockin_settle_rte_ppms.py` & `Other-files/test_ppms_rte_review.py`: Remaining removed methods target `RTEPanel` / `build_rte_panel` / `2400` / `Kepco` on the removed SHHV/RT-E page; pure PPMS settling rules remain covered by `test_transport_common_settling.py` and `test_lockin_settle_rte_ppms.py`.

---

## T1.2 — Restored snapshot and recording contract (A1-2)

- **File**: `Other-files/test_ppms_scan_plot_snapshots.py` (restored from `main:Other-files/test_scan_plot_snapshots.py`).
- **Coverage (6 tests, offline, zero Tk windows)**:
  - `RoutineTests` (5 tests): `save_scan_plot_snapshots` redraw-before-grab ordering, `<run>_plot_snapshot.png` / `<run>_plots_snapshot.png` naming, no overwrite of existing files (`_01`), partial failure reporting (`NOT_SHOWN` logged and recorded in `plot_snapshots` event while other plots still save), no-op on missing recorder / closed recorder / 0 points / unbuilt plots / `closing=True`, and exception safety (`test_it_never_raises`).
  - `PpmsTests.test_a_scan_saves_one_picture_of_its_plot_box`: runs a full offline PPMS scan via `test_ppms_run_save_zero.Page`, verifying `ppms_plot_frame` is captured to `run1_plots_snapshot.png`, event order is `["outputs_zeroed", "plot_snapshots", "run_finished"]`, `plot_snapshots` records `saved=True`, status is `"Completed; 6221 zeroed"`, and Macro continuation remains intact.

---

## T1.3 — Zurich close-order risk characterization (A2-3)

- **Trace**:
  - `PPMSPanel.safe_close_ppms` (`transport/ppms/app.py:L10894–10903`) sets `self.zurich_closed = True`, cancels `self.zurich_after_id`, **immediately** submits `self.zurich_controller.disconnect_mfli` to `self.zurich_executor` (`max_workers=1`, `ppms-zurich`) and calls `self.zurich_executor.shutdown(wait=False)` (`L10901–10902`), **before** calling `super().safe_close_ppms()` (`L10903`).
  - `CombinedControlApp.safe_close_ppms` (`L10125–10181`) only defers `ppms_instrument_controllers` (`6221`, `830_*`) and `ppms_environment_controller` via `ScanCloseHandoff.defer(...)` (`L10178`).
  - `_ppms_scan_with_final_field` (`L9185–9245`) runs on `ppms_executor` (`max_workers=5`, `ppms-control`) and calls `ScanCloseHandoff.begin()` / `end()`.
- **Classification**: **Confirmed pre-existing defect** (not an extraction regression; `PPMSPanel` AST is 28/28 identical to `main@56908d88`).
- **Observed consequences**:
  1. When closing during an active Zurich scan, `zurich_executor` runs `disconnect_mfli()` **before** the scan worker reaches `ScanCloseHandoff.end()`, clearing `mfli_daq` / `mfli_bridge` / `mfli_device` and releasing `LABONE::<device>` while the scan worker is still active.
  2. Any subsequent in-flight worker Zurich read fails with not-connected state instead of completing/exiting cleanly via `ppms_field_scan_stop_event`.
  3. Meanwhile, `ScanCloseHandoff` still properly defers `ppms_instrument_controllers` and `ppms_environment_controller` until `end()` (including on scan cancellation and worker exceptions).
- **Runnable reproduction**: `Other-files/test_ppms_zurich_close_order.py` (5 deterministic `threading.Event` / `zurich_executor.shutdown(wait=True)` synchronized tests; 4 pass + 1 `@unittest.expectedFailure` `test_mfli_disconnect_waits_for_scan_close_handoff_known_bug_a2_3` with `addCleanup` guard so unrelated exceptions are never masked).
  - **A2-3 fix PR sync checklist**: When the separate A2-3 fix PR coordinates `zurich_controller.disconnect_mfli` with `ScanCloseHandoff`, both tests must be updated together:
    1. Remove `@unittest.expectedFailure` from `test_mfli_disconnect_waits_for_scan_close_handoff_known_bug_a2_3`.
    2. Update `test_active_scan_close_defers_visa_env_but_disconnects_mfli_early` (which characterizes the pre-fix early disconnect) to assert `worker_errors == []` and `worker:finished` before `disconnect:mfli`.
- **D1 gating**: **A separate targeted fix PR for A2-3 is a blocking prerequisite before D1 implementation** (no production fix is made in T1).

---

## T1.4 — Mac no-GUI test mode (T-1)

- **Command**:
  - Run offline non-GUI suite: `cd Other-files && python3 ci/run_suite.py --no-gui`
  - Inspect included/excluded modules: `cd Other-files && python3 ci/run_suite.py --list-no-gui`
- **Selection policy & guard (`Other-files/ci/run_suite.py`)**:
  - Default `python3 ci/run_suite.py` is **unchanged** and runs all 51 modules (used by Linux `xvfb-run` and Windows CI).
  - `--no-gui` excludes only the 4 modules in `GUI_ONLY_MODULES` that genuinely instantiate live `tk.Tk()` / `CombinedControlApp(root)` / `Toplevel` windows (`test_folder_dialog`, `test_plot_redraw_performance`, `test_scan_start_window`, `test_zurich_choice`), printing each excluded module and reason. (`test_commercial_look` uses stand-in widget trees and `FakeStyle` without a Tk window, so it remains included and passes under `--no-gui`.)
  - Unknown/new `test_*.py` modules are **included by default** in `--no-gui` and guarded by `install_no_gui_guard()`, which patches `tkinter.Tk.__init__` and `tkinter.Toplevel.__init__` to raise `RuntimeError("Tk window creation is forbidden in --no-gui mode")`.
  - `Other-files/test_ci_runner_no_gui.py` (3 tests) verifies default discovery, fail-fast Tk guard (negative control), and nonzero failure propagation.

---

## Verification

### Before vs After counts (per platform / mode)

| Platform / Mode | Before (`Transport-PPMS@c0c2634` / `8369a29e`) | After (PR #2) | Skips / Expected failures / Notes |
| --- | --- | --- | --- |
| **macOS local (`ci/run_suite.py --no-gui`)** | Not available (`T-1` missing) | **47 modules, 596 tests ran, 0 failed, 0 skipped, 1 expected failure** | Excludes 4 GUI-only modules (`test_folder_dialog`, `test_plot_redraw_performance`, `test_scan_start_window`, `test_zurich_choice`); 1 `expectedFailure` (`test_mfli_disconnect_waits_for_scan_close_handoff_known_bug_a2_3`). |
| **Linux CI (`xvfb-run python ci/run_suite.py`)** | 46 modules, 610 tests ran, 0 failed, 3 skipped | **51 modules, 640 tests ran, 0 failed, 3 skipped, 1 expected failure** | 3 skipped: Windows-only `win32` lease tests; 1 `expectedFailure` (A2-3). Layout (`ppms_checks.py`): `PPMS CHECKS OK`. Wheel build/install + `transport-ppms` CLI: OK. |
| **Windows CI (`python ci/run_suite.py`)** | 46 modules, 610 tests ran, 0 failed, 4 skipped | **51 modules, 640 tests ran, 0 failed, 4 skipped, 1 expected failure** | 4 skipped: POSIX/macOS-only `fcntl` lease tests + `test_a_program_that_dies_leaves_nothing_held`; 1 `expectedFailure` (A2-3). Layout (`ppms_checks.py`): `PPMS CHECKS OK`. |

### Exact commands & CI links
- **Local macOS (`--no-gui`, no real hardware)**:
  - `cd Other-files && python3 ci/run_suite.py --no-gui` → exit `0` (**47 modules, 596 tests ran, 0 failed, 0 skipped, 1 expected failure**, 4 GUI-only modules excluded).
  - `cd Other-files && python3 ci/run_suite.py --module test_ppms_qdinstrument` → **5 tests ran, 0 failed**, exit `0`.
  - `cd Other-files && python3 ci/run_suite.py --module test_visa_shared_manager` → **7 tests ran, 0 failed**, exit `0`.
  - `cd Other-files && python3 ci/run_suite.py --module test_ppms_scan_plot_snapshots` → **6 tests ran, 0 failed**, exit `0`.
  - `cd Other-files && python3 ci/run_suite.py --module test_ppms_zurich_close_order` → **5 tests ran, 0 failed, 1 expected failure**, exit `0`.
  - `cd Other-files && python3 ci/run_suite.py --module test_commercial_look --no-gui` → **20 tests ran, 0 failed**, exit `0`.
  - `cd Other-files && python3 ci/run_suite.py --module test_ci_runner_no_gui --no-gui` → **3 tests ran, 0 failed**, exit `0`.
- **Production source invariance**:
  - `git diff Transport-PPMS -- transport/ Transport_PPMS.py` is empty (0 production source changes).
- **GitHub Actions CI (`Jiaze-Li/Transport-all`, PR #2)**:
  - `829f29f2294245308ee7c26d0f88fd4b3adeba80`: `PPMS tests` ([run 46011810730](https://github.com/Jiaze-Li/Transport-all/actions/runs/46011810730)) — **success** (`linux` + `windows` full 51-module suite, `ppms_checks.py`, wheel build/install).
  - Final PR #2 checks: [PR #2 checks](https://github.com/Jiaze-Li/Transport-all/pull/2/checks) (triggered on push of this commit).

## A2-3 targeted fix (follow-up to T1)

Status: Pending implementation and GitHub Actions verification. Preserve the T1 evidence above as the pre-fix baseline; record the narrowly scoped fix, updated close-order assertions, CI evidence and remaining limitations here. No physical instrument acceptance is implied.
