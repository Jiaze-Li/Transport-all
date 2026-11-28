"""Single authoritative resource and lifecycle owner for the PPMS application."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from typing import Callable

from transport.core.scan_lifecycle import ScanCloseHandoff
from transport.ppms.controllers.environment import PPMSRemoteReadbackController
from transport.ppms.controllers.sr830 import SR830Controller
from transport.ppms.controllers.visa import SafeVisaIdentityController
from transport.ppms.controllers.zurich import ZurichMFLIController
from transport.ppms.engine.planning import (
    PPMS_FIELD_COMMANDING_MODES,
    PPMS_SR830_KEYS,
    PPMS_SR830_LABELS,
)
from transport.ppms.protocol import validate_ppms_field_command

PPMS_DEFAULT_RESOURCES = {
    "6221": "GPIB0::30::INSTR",
    "830_a": "GPIB0::8::INSTR",
    "830_b": "GPIB0::9::INSTR",
    "830_c": "GPIB0::10::INSTR",
    "2400": "GPIB0::24::INSTR",
}


# How long the deferred close waits for other in-flight hardware tasks.
PPMS_CLOSE_DRAIN_TIMEOUT_S = 30.0


class InflightTasks:
    """Tracks the futures submitted through one executor.

    The deferred close runs on a worker of that executor, so it cannot
    ``shutdown(wait=True)`` (that would join itself).  It cancels queued tasks
    and waits for the other running ones instead.  Tracking is installed on
    the executor instance, so a replaced or patched executor class still works.
    """

    def __init__(self, executor) -> None:
        self._lock = threading.Lock()
        self._tracked: dict = {}
        original = executor.submit

        def submit(fn, /, *args, **kwargs):
            holder: dict = {}

            def run():
                holder["thread"] = threading.get_ident()
                return fn(*args, **kwargs)

            future = original(run)
            with self._lock:
                self._tracked[future] = holder
            future.add_done_callback(self._forget)
            return future

        executor.submit = submit

    def _forget(self, future) -> None:
        with self._lock:
            self._tracked.pop(future, None)

    def drain_others(self, timeout: float) -> list[str]:
        """Cancel queued tasks, wait for running ones; return problems."""
        me = threading.get_ident()
        with self._lock:
            items = list(self._tracked.items())
        pending = []
        for future, holder in items:
            if holder.get("thread") == me or future.cancel():
                continue
            pending.append(future)
        if not pending:
            return []
        _done, not_done = wait_futures(pending, timeout=timeout)
        if not_done:
            return [
                f"{len(not_done)} in-flight hardware task(s) still running "
                f"after {timeout:g} s; disconnecting anyway"
            ]
        return []


class PPMSAppState:
    """Focused resource and lifecycle registry (not a god object).

    Owns the single authoritative executors, stop events, ScanCloseHandoff,
    instrument controllers, environment controller, Zurich controller, and
    thread-safe live shared state.
    """

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        if logger is None:
            logger = lambda _msg: None
        self.logger = logger
        self.ppms_executor = ThreadPoolExecutor(
            max_workers=5, thread_name_prefix="ppms"
        )
        self.ppms_inflight = InflightTasks(self.ppms_executor)
        self.zurich_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ppms-zurich"
        )
        self.scan_stop_event = threading.Event()
        self.macro_stop_event = threading.Event()
        self.close_handoff = ScanCloseHandoff()

        self.closed = False

        self.ppms_instrument_controllers: dict[str, SafeVisaIdentityController] = {
            "6221": SafeVisaIdentityController(
                logger,
                "Keithley 6221",
                "6221",
                safe_off_commands=("SOUR:WAVE:ABOR", "OUTP OFF"),
                owner_scope="PPMS",
            ),
            "830_a": SR830Controller(logger),
            "830_b": SR830Controller(logger),
            "830_c": SR830Controller(logger),
            "2400": SafeVisaIdentityController(
                logger,
                "Keithley 2400",
                "2400",
                safe_off_commands=(":OUTP OFF",),
                owner_scope="PPMS",
            ),
        }
        for key in PPMS_SR830_KEYS:
            self.ppms_instrument_controllers[key].label = PPMS_SR830_LABELS[key]
            self.ppms_instrument_controllers[key].owner_scope = "PPMS"

        self.ppms_environment_controller = PPMSRemoteReadbackController(logger)
        self.zurich_controller = ZurichMFLIController(logger)

        # Live shared state
        self.last_readback: dict[str, object] | None = None
        self.environment_busy = False
        self.scan_running = False
        self.active_scan_mode = ""
        self.zurich_busy = False
        self.zurich_reading = False
        self.zurich_has_md = True
        self.zurich_expected_frequency = 317.3
        self.zurich_scan_enabled = False
        self.zurich_scan_configured = False
        self.zurich_scan_harmonic = "1+2+3"
        self.zurich_scan_reference = "Trigger In 1"
        self.zurich_scan_tau = 1.0

        self.zero_persist_pending = False
        self.zero_persist_completed = False
        self.scan_preserve_on_stop = False
        self.scan_field_zero_accepted = False
        self.scan_outputs: dict[str, str] | None = None
        self.scan_field_note = ""
        self.scan_stop_button = ""
        self.scan_context: dict[str, object] = {}
        self.scan_rows: list[dict[str, object]] = []
        self.scan_total = 0
        self.scan_started_at = 0.0
        self.scan_settle_logged: float | None = None
        self.scan_sr830_tc_code = 10
        self.source_fresh = False

        self.run_recorder = None
        self.run_folder = None
        self.live_csv_path = None
        self.live_csv_fields = None

        self.idle_autorange_enabled = False
        self.k6221_zero_output = False
        self.zurich_autorange_pending = False
        self.last_current_trip = False
        self.sr830_range_states: dict[str, object] = {}
        self.sr830_time_constant_codes: dict[str, int] = {}
        self.idle_range_ready_at: dict[str, float] = {}
        self.sr830_connecting: set[str] = set()
        self.persistent_watch: dict[str, object] | None = None
        self.sample_trace = None

    def safe_close(
        self,
        *,
        log_warning: Callable[[str], None] | None = None,
        field_rate_fn: Callable[[], float] | None = None,
        field_approach_fn: Callable[[], int] | None = None,
    ) -> None:
        """Close hardware and executors once, respecting PR #3 ScanCloseHandoff."""
        if self.closed:
            return
        self.closed = True
        scan_was_running = self.scan_running
        self.scan_stop_event.set()
        self.macro_stop_event.set()

        recorder = self.run_recorder
        if recorder is not None:
            try:
                recorder.event("stop_requested", button="the program closing")
            except Exception:
                pass

        self.zurich_executor.shutdown(wait=False, cancel_futures=True)

        def cleanup(after_worker: bool) -> None:
            errors: list[str] = []
            if after_worker:
                # Deferred close: the scan worker is one of several workers;
                # let the others (reads, motor, macro, connects) finish first.
                errors.extend(
                    self.ppms_inflight.drain_others(PPMS_CLOSE_DRAIN_TIMEOUT_S)
                )
            if (
                scan_was_running
                and not after_worker
                and self.active_scan_mode in PPMS_FIELD_COMMANDING_MODES
                and not self.scan_field_zero_accepted
                and self.ppms_environment_controller.connected
            ):
                try:
                    rate = field_rate_fn() if field_rate_fn is not None else 100.0
                    _target, rate = validate_ppms_field_command(0.0, rate)
                    approach = (
                        field_approach_fn() if field_approach_fn is not None else 0
                    )
                    self.ppms_environment_controller.set_field(
                        0.0, rate, approach, 1
                    )
                except BaseException as error:
                    errors.append(
                        f"PPMS: zeroing the field before close failed: {error}"
                    )

            for controller in self.ppms_instrument_controllers.values():
                try:
                    controller.disconnect()
                except BaseException as error:
                    errors.append(str(error))

            try:
                self.ppms_environment_controller.disconnect()
            except BaseException as error:
                errors.append(str(error))

            try:
                self.zurich_executor.shutdown(wait=True, cancel_futures=True)
            except BaseException as error:
                errors.append(str(error))

            try:
                self.zurich_controller.disconnect_mfli()
            except BaseException as error:
                errors.append(str(error))

            if log_warning is not None:
                for error in errors:
                    try:
                        log_warning(f"PPMS safe-close warning: {error}")
                    except BaseException:
                        pass

        deferred = self.close_handoff.defer(lambda: cleanup(True))
        self.ppms_executor.shutdown(wait=not deferred, cancel_futures=not deferred)
        if not deferred:
            cleanup(False)


__all__ = [
    "PPMSAppState",
    "PPMS_DEFAULT_RESOURCES",
]
