"""Tk-independent PPMS measurement runner, scan request, and event sink."""

from __future__ import annotations

import csv
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from transport.core.scan_lifecycle import scan_end_outputs_text, zero_scan_outputs
from transport.ppms.controllers.zurich import ZurichMFLIController
from transport.core.scan_sequences import ppms_scan_axis
from transport.ppms.controllers.environment import PPMS_QDINSTRUMENT_BACKEND
from transport.ppms.controllers.qdinstrument import (
    PPMS_FIELD_RELATIVE_TOLERANCE,
    PPMS_FIELD_TOLERANCE_OE,
)
from transport.ppms.controllers.sr830 import (
    LOCKIN_DOWNRANGE_FRACTION,
    NONLOCAL_SR830_FULL_SCALE_V,
    SR830Controller,
    SR830_TIME_CONSTANTS_S,
)
from transport.ppms.controllers.zurich import (
    K6221_OUTPUT_LOW_FLOATING,
    K6221_OUTPUT_LOW_QUERY,
    NONLOCAL_MARKER_LINE,
    NONLOCAL_MARKER_PHASE_DEG,
    NONLOCAL_SAMPLE_HZ,
    short_status_error,
)
from transport.ppms.engine.errors import (
    PPMSFieldScanError,
    PPMSLockinRangeError,
    PPMSMotorScanError,
    PPMSScanCancelled,
    PPMS_FIELD_NOT_ZEROED_TEXT,
)
from transport.ppms.engine.motor import PPMSMotorDebug
from transport.ppms.engine.planning import (
    K6221_MAX_ABS_CURRENT_A,
    LOCKIN_SETTLE_TAUS,
    PPMS_FIELD_COMMANDING_MODES,
    PPMS_FREQUENCY_SCAN_MODES,
    PPMS_PLOT_AXIS_COLUMNS,
    PPMS_SCAN_PLOT_AXES,
    PPMS_SR830_HARMONIC_SETTING_KEYS,
    PPMS_SR830_LABELS,
    ppms_lockin_settle_s,
    ppms_lockin_settle_text,
    ppms_settle_status_text,
    validate_ppms_transport_scan_settings,
)
from transport.ppms.protocol import (
    PPMS_TEMPERATURE_LOW_BOUNDARY_K,
    PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN,
    ppms_scan_temperature_segments,
)

PPMS_SR830_DOWNRANGE_FRACTION = LOCKIN_DOWNRANGE_FRACTION
PPMS_SR830_DOWNRANGE_READINGS = 3
PPMS_SR830_RANGE_COOLDOWN_READINGS = 3
PPMS_SR830_RANGE_LIMIT_S = 180.0
PPMS_SR830_RANGE_LIMIT_STEPS = 36
PPMS_FIRST_POINT_MAX_WINDOWS = 10
PPMS_FIRST_POINT_TOLERANCE_FRACTION = 0.02
PPMS_FIRST_POINT_FLOOR_FRACTION = (
    PPMS_FIRST_POINT_TOLERANCE_FRACTION * 2.5 * LOCKIN_DOWNRANGE_FRACTION
)
PPMS_ZURICH_TIME_CONSTANT_S = 1.0
PPMS_ACQUISITION_S = 1.0
PPMS_TEMPERATURE_ACQUISITION_S = 5.0
PPMS_ROTATOR_MOVE_ALLOWANCE_S = 1.0
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
PPMS_SR830_REFERENCE_SLOPE = 1
PPMS_SR830_DEFAULT_TIME_CONSTANT = "1 s"


@dataclass(frozen=True)
class ScanRequest:
    """Immutable scan configuration captured at start."""

    mode: str
    sequence: tuple[tuple[int, int, int, float], ...]
    rate_oe_s: float
    stable_s: float
    approach_code: int
    transport_settings: dict[str, object]
    total_points: int
    estimated_seconds: float
    sr830_tc_code: int
    zurich_enabled: bool = False
    zurich_harmonic: str = "1+2+3"
    zurich_reference: str = "Trigger In 1"
    zurich_tau: float = PPMS_ZURICH_TIME_CONSTANT_S


class ScanEventSink(Protocol):
    """UI/observer callback contract (zero Tk types)."""

    def on_status(self, text: str) -> None: ...
    def on_transport_status(self, text: str) -> None: ...
    def on_eta(self, text: str) -> None: ...
    def on_progress(self, value: int) -> None: ...
    def on_environment_readback(self, data: dict[str, object]) -> None: ...
    def on_persistent_follow_up_ended(self, owner: str) -> None: ...
    def on_sr830_range(
        self, key: str, label: str, status: int, action: str, range_index: int | None
    ) -> None: ...
    def on_zurich_sample(self, sample: dict[str, object]) -> None: ...
    def on_zurich_autorange(self, action_text: str) -> None: ...
    def on_compliance_trip(self) -> None: ...
    def on_outputs_zeroed(self, results: dict[str, str]) -> None: ...
    def on_point_recorded(
        self,
        data: dict[str, object],
        progress: int,
        remaining_s: float,
        sample: dict[str, object],
    ) -> None: ...
    def on_zero_persist_button_reset(self) -> None: ...
    def capture_plot_snapshots(self, recorder, points: int) -> None: ...
    def on_scan_finished(
        self,
        *,
        mode: str,
        outcome: str,
        status: str,
        results: dict[str, str] | None,
        outputs_confirmed: bool,
        field_note: str,
        error: BaseException | None,
        problems: list[str],
    ) -> None: ...
    def log(self, message: str) -> None: ...


def format_voltage_range(value) -> str:
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


def format_sr830_full_scale(index) -> str:
    scales = NONLOCAL_SR830_FULL_SCALE_V
    if not isinstance(index, int) or not 0 <= index < len(scales):
        return "--"
    return format_voltage_range(scales[index])


def format_scan_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d} h {minutes:02d} min {secs:02d} s"
    if minutes:
        return f"{minutes:d} min {secs:02d} s"
    return f"{secs:d} s"


def ppms_field_is_ready(
    result: dict[str, object], target_oe: float, driven: bool = False
) -> bool:
    measured = float(result["field_oe"])
    tolerance = max(
        PPMS_FIELD_TOLERANCE_OE, abs(target_oe) * PPMS_FIELD_RELATIVE_TOLERANCE
    )
    status = str(result.get("field_status", "")).casefold().replace(" ", "")
    if driven and "persistent" in status:
        return False
    stable_status = any(
        token in status for token in ("holding", "persistent", "stable", "standby")
    )
    return stable_status and abs(measured - target_oe) <= tolerance


def ppms_sr830_address(resource_name: str) -> int:
    match = re.fullmatch(
        r"GPIB\d*::(8|9|10)::INSTR", str(resource_name).strip(), flags=re.IGNORECASE
    )
    if match is None:
        raise ValueError("PPMS SR830 address must be GPIB 8, 9 or 10")
    return int(match.group(1))


class PPMSScanRunner:
    """Tk-independent PPMS measurement execution engine.

    Preserves the exact field/nested scan loops, harmonic passes, SR830 and
    Zurich MFLI acquisition/autorange, 6221 output-off readback, interrupted
    field zeroing, and deterministic Tk-independent recorder.close().
    """

    def __init__(
        self,
        state,
        *,
        post_ui: Callable[[Callable[[], None]], None],
        read_live_fn: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self.state = state
        self.post_ui = post_ui
        self._read_live_override = read_live_fn

    def _log(self, sink: ScanEventSink | None, message: str) -> None:
        if sink is not None:
            sink.log(message)
        else:
            self.state.logger(message)

    def _rec_event(self, name: str, **details) -> None:
        recorder = self.state.run_recorder
        if recorder is not None:
            recorder.event(name, **details)

    def _rec_check(self) -> None:
        recorder = self.state.run_recorder
        if recorder is not None:
            recorder.check()

    def _append_live_csv(self, row: dict[str, object]) -> None:
        path = self.state.live_csv_path
        if path is None:
            raise RuntimeError("No live CSV file selected; acquisition stopped")
        first = self.state.live_csv_fields is None
        fields = list(row) if first else self.state.live_csv_fields
        if set(row) != set(fields):
            raise RuntimeError(
                "Live CSV channels changed; appending stopped to avoid misaligned columns"
            )
        with Path(path).open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if first:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        self.state.live_csv_fields = fields

    def _rec_append_row(self, row: dict[str, object]) -> None:
        recorder = self.state.run_recorder
        if recorder is None:
            self._append_live_csv(row)
        else:
            recorder.append_row(row)

    def close_run(
        self, outcome: str, sink: ScanEventSink | None = None, **details
    ) -> list[str]:
        recorder = self.state.run_recorder
        self.state.run_recorder = None
        if recorder is None or recorder.closed:
            return []
        errors = recorder.close(outcome, **details)
        try:
            self._log(
                sink,
                f"PPMS run folder {recorder.directory}: {outcome}; {recorder.csv_path.name} and {recorder.log_path.name} closed",
            )
            if errors:
                self._log(sink, f"PPMS run log: {'; '.join(errors)}")
        except Exception:
            pass
        return errors

    def query_numbers(
        self, key: str, command: str, expected_count: int
    ) -> tuple[float, ...]:
        controller = self.state.ppms_instrument_controllers[key]
        last_response = ""
        last_error: BaseException | None = None
        zurich_cls = type(getattr(self.state, "zurich_controller", None))
        clear_queries = getattr(
            zurich_cls,
            "CLEAR_ON_READ_QUERIES",
            ZurichMFLIController.CLEAR_ON_READ_QUERIES,
        )
        once = command.strip().upper() in clear_queries
        with controller.lock:
            instrument = controller.instrument
            if instrument is None:
                raise RuntimeError(f"PPMS {controller.label} is not connected")
            for attempt in range(1, 2 if once else 4):
                try:
                    last_response = instrument.query(command).strip()
                    values = tuple(
                        float(part.strip()) for part in last_response.split(",")
                    )
                    if len(values) != expected_count:
                        raise ValueError(
                            f"expected {expected_count} values, received {len(values)}"
                        )
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
            + (
                "returned an abnormal reply (not asked again: it clears as it is read): "
                if once
                else "kept returning abnormal replies: "
            )
            + f"{last_response!r}"
        ) from last_error

    def query_lias(self, key: str, sink: ScanEventSink | None = None) -> int:
        try:
            value = self.query_numbers(key, "LIAS?", 1)[0]
            if not (float(value).is_integer() and 0 <= value <= 255):
                raise ValueError(f"not a status byte: {value!r}")
            return int(value)
        except Exception as error:
            if not self.state.ppms_instrument_controllers[key].connected:
                raise
            self._log(
                sink,
                f"PPMS {self.state.ppms_instrument_controllers[key].label} LIAS? reply unreadable ({error}); "
                "not asked again (it clears as it is read): the reading counts as overloaded",
            )
            return type(self.state.zurich_controller).SR830_LIAS_UNREADABLE

    def write_6221_checked(self, command: str) -> None:
        controller = self.state.ppms_instrument_controllers["6221"]
        with controller.lock:
            if self.state.scan_stop_event.is_set():
                raise PPMSScanCancelled("6221 setup cancelled")
            if controller.instrument is None:
                raise RuntimeError("PPMS Keithley 6221 is not connected")
            controller.instrument.write(command)
            time.sleep(0.03)
            response = controller.instrument.query("SYST:ERR?").strip()
            try:
                code = int(response.split(",", 1)[0].strip())
            except ValueError as error:
                raise RuntimeError(
                    f"6221 error status could not be parsed: {response}"
                ) from error
            if code != 0:
                raise RuntimeError(
                    f"6221 reported an error after {command!r}: {response}"
                )

    def write_sr830_checked(self, key: str, command: str) -> None:
        controller = self.state.ppms_instrument_controllers[key]
        with controller.lock:
            if controller.instrument is None:
                raise RuntimeError(f"PPMS {controller.label} is not connected")
            controller.instrument.write(command)
            time.sleep(0.03)
            event_status = int(round(self.query_numbers(key, "*ESR?", 1)[0]))
            if event_status & 0x30:
                raise RuntimeError(
                    f"{controller.label} rejected {command!r}; *ESR?={event_status}"
                )

    def initialize_6221(self) -> None:
        controller = self.state.ppms_instrument_controllers["6221"]
        with controller.lock:
            if self.query_numbers("6221", K6221_OUTPUT_LOW_QUERY, 1)[0] == 0:
                return
            controller.instrument.write("*CLS")
            self.write_sr830_checked("6221", K6221_OUTPUT_LOW_FLOATING)
            state = self.query_numbers("6221", K6221_OUTPUT_LOW_QUERY, 1)[0]
        if state != 0:
            raise RuntimeError(
                f"6221 output low stayed earth-grounded (OUTP:LTE? returned {state:g}); set TRIAX -> OUTPUT LOW -> FLOATING on the front panel"
            )

    def read_live(self) -> dict[str, object]:
        if self._read_live_override is not None:
            return self._read_live_override()
        result = self.state.ppms_environment_controller.read_environment()
        if getattr(self.state.ppms_environment_controller, "backend", "") == PPMS_QDINSTRUMENT_BACKEND:
            try:
                motor_data = PPMSMotorDebug(
                    self.state.ppms_environment_controller.active
                ).read()
                result["motor_display"] = (
                    f"{motor_data['angle_deg']:.4f}° / {motor_data['status']}"
                )
            except Exception as error:
                result["motor_display"] = f"Motor unavailable: {error}"
        return result

    def lockin_settle(
        self,
        stable_s: float,
        active_lockins,
        zurich: bool,
        fallback_code: int | None = None,
    ) -> tuple[float, float, str]:
        codes = self.state.sr830_time_constant_codes
        meters = []
        for key in active_lockins:
            code = codes.get(key)
            if code is None and fallback_code is not None:
                code = fallback_code
            if code is None:
                code = 10
            meters.append(
                (SR830_TIME_CONSTANTS_S[int(code)], PPMS_SR830_LABELS.get(key, key))
            )
        if zurich:
            tau = float(self.state.zurich_scan_tau)
            if not math.isfinite(tau) or tau <= 0:
                tau = PPMS_ZURICH_TIME_CONSTANT_S
            meters.append((tau, "the Zurich"))
        slowest, meter = max(meters, key=lambda item: item[0], default=(0.0, ""))
        return (
            ppms_lockin_settle_s(stable_s, [tau for tau, _name in meters]),
            slowest,
            meter,
        )

    def _point_settle(
        self, stable_s: float, active_lockins, sink: ScanEventSink | None
    ) -> tuple[float, float]:
        zurich = bool(self.state.zurich_scan_enabled)
        settle, slowest, meter = self.lockin_settle(
            stable_s,
            active_lockins,
            zurich,
            fallback_code=self.state.scan_sr830_tc_code,
        )
        if self.state.scan_settle_logged != settle:
            self.state.scan_settle_logged = settle
            self._log(
                sink,
                "PPMS scan: each reading now waits "
                + ppms_lockin_settle_text(stable_s, settle, slowest, meter),
            )
            status = f"Each reading now waits {ppms_settle_status_text(settle)} s" + (
                f": {LOCKIN_SETTLE_TAUS:g} x {ppms_settle_status_text(slowest)} s TC of {meter}"
                if slowest and settle > stable_s
                else " (Settle s)"
            )
            if sink is not None and not self.state.closed:
                self.post_ui(lambda s=status: sink.on_status(s))
        return settle, slowest

    def _read_sr830_time_constant_for_scan(
        self, key: str, address: int, sink: ScanEventSink | None
    ) -> None:
        verified = self.state.sr830_time_constant_codes
        previous = verified.pop(key, None)
        code = int(round(self.query_numbers(key, "OFLT?", 1)[0]))
        if not 0 <= code < len(SR830_TIME_CONSTANTS_S):
            raise RuntimeError(
                f"SR830 #{address} time-constant readback invalid: OFLT={code}"
            )
        verified[key] = code
        if previous is not None and code != previous:
            self._log(
                sink,
                f"PPMS {self.state.ppms_instrument_controllers[key].label}: the time constant reads "
                f"{SR830_TIME_CONSTANTS_S[code]:g} s (OFLT {code}), not the "
                f"{SR830_TIME_CONSTANTS_S[previous]:g} s last set (changed on the front panel?); "
                "the scan records the value read and counts it in the wait before each reading",
            )

    def clear_sr830_range_floor(self, keys=None, zurich=None) -> None:
        for key, state in dict(self.state.sr830_range_states).items():
            if keys is None or key in keys:
                state.overload_floor = None
        if keys is None if zurich is None else zurich:
            forget = getattr(
                getattr(self.state, "zurich_controller", None),
                "forget_mfli_range_guard",
                None,
            )
            if callable(forget):
                forget()

    def _read_sr830_status(
        self, key: str, fresh: bool = False, sink: ScanEventSink | None = None
    ) -> int:
        state = self.state.sr830_range_states.get(key)
        if state is not None and getattr(state, "clear_latch_pending", False):
            self.query_lias(key, sink)
            state.clear_latch_pending = False
        if fresh:
            self.query_lias(key, sink)
        return self.query_lias(key, sink)

    def _sr830_settle_s(self, key: str, time_constants: float = 5.0) -> float:
        code = int(self.query_numbers(key, "OFLT?", 1)[0])
        if not 0 <= code <= 19:
            raise RuntimeError("SR830 time-constant readback invalid")
        tau = (1 if code % 2 == 0 else 3) * 10 ** (code // 2 - 5)
        return max(0.1, time_constants * tau)

    def _autorange_sr830(
        self,
        key: str,
        r_value: float,
        status: int,
        enabled: bool = True,
        allow_down: bool = True,
        sink: ScanEventSink | None = None,
    ) -> float:
        controller = self.state.ppms_instrument_controllers[key]
        with controller.lock:
            state = self.state.sr830_range_states.get(key)
            if state is None:
                state = SR830Controller.__new__(SR830Controller)
                state.auto_range_enabled = True
                state.low_range_count = state.range_cooldown = 0
                state.low_range_peak = 0.0
                state.phase = None
                state.clear_latch_pending = False
                self.state.sr830_range_states[key] = state
            state.instrument = controller.instrument
            state.sensitivity_index = int(self.query_numbers(key, "SENS?", 1)[0])
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
                elif (
                    enabled
                    and old > max(0, floor)
                    and not (status & 8)
                    and magnitude < PPMS_SR830_DOWNRANGE_FRACTION * scales[old]
                ):
                    state.low_range_count += 1
                    if state.low_range_count >= PPMS_SR830_DOWNRANGE_READINGS:
                        requested = old - 1
                        controller.instrument.write(f"SENS {requested}")
                        actual = float(controller.instrument.query("SENS?").strip())
                        if actual != requested:
                            raise RuntimeError(
                                f"SR830 range-down readback mismatch: requested {requested}, read {actual}"
                            )
                        state.sensitivity_index = requested
                        state.low_range_count = 0
                        state.range_cooldown = PPMS_SR830_RANGE_COOLDOWN_READINGS
                        state.clear_latch_pending = True
                        action = f"SENS {old}->{requested} (low signal)"
                else:
                    state.low_range_count = 0
            if overloaded:
                if old >= top:
                    if sink is not None:
                        self.post_ui(
                            lambda: sink.on_sr830_range(
                                key, "OVERLOAD; at maximum range", status, "", old
                            )
                        )
                    raise PPMSLockinRangeError(
                        f"{controller.label} OVERLOAD: already at maximum sensitivity, check the input and gain"
                    )
                requested = old + 1
                controller.instrument.write(f"SENS {requested}")
                actual = float(controller.instrument.query("SENS?").strip())
                if actual != requested:
                    raise RuntimeError(
                        f"SR830 autorange readback mismatch: requested {requested}, read {actual}"
                    )
                state.sensitivity_index = requested
                state.clear_latch_pending = True
                state.low_range_count = 0
                state.range_cooldown = PPMS_SR830_RANGE_COOLDOWN_READINGS
                state.overload_floor = max(floor, requested)
                action = f"SENS {old}->{requested} (overload)"
            label = ("OVERLOAD; " if overloaded else "") + (
                f"autorange {action}"
                if action
                else "reference locked"
                if not status & 8
                else "reference unlocked"
            )
            if sink is not None:
                idx = state.sensitivity_index
                self.post_ui(
                    lambda: sink.on_sr830_range(key, label, status, action, idx)
                )
            if action:
                return self._sr830_settle_s(key, 5.0)
            return 0.0

    def _configure_zurich_for_scan(self) -> None:
        if self.state.zurich_scan_enabled and not self.state.zurich_scan_configured:
            zurich = self.state.zurich_controller
            with zurich.lock:
                harmonic_mode = str(self.state.zurich_scan_harmonic)
                dual = harmonic_mode in ("1+2", "1+2+3")
                zurich.configure_mfli(
                    "AC",
                    1 if dual else int(harmonic_mode),
                    "R",
                    reference_source=self.state.zurich_scan_reference,
                    sample_rate_hz=NONLOCAL_SAMPLE_HZ,
                    input_impedance="10 MΩ",
                    input_scaling=1.0,
                    timeconstant_s=self.state.zurich_scan_tau,
                    external_reference_demodulator=(
                        3 if zurich.mfli_has_multi_demod() else None
                    ),
                )
                if dual:
                    zurich.configure_second_harmonic(self.state.zurich_scan_tau)
                if harmonic_mode == "1+2+3":
                    zurich.configure_third_harmonic(self.state.zurich_scan_tau)
                zurich.disable_unused_ppms_demods(harmonic_mode)
            self.state.zurich_scan_configured = True

    def configure_transport_scan(
        self, settings: dict[str, object], sink: ScanEventSink | None = None
    ) -> None:
        self._configure_zurich_for_scan()
        if self.state.scan_stop_event.is_set():
            raise PPMSScanCancelled("Scan stopped; current source not reconfigured")
        active_lockins = tuple(settings["active_lockins"])
        required = ("6221", *active_lockins)
        missing = [
            self.state.ppms_instrument_controllers[key].label
            for key in required
            if not self.state.ppms_instrument_controllers[key].connected
        ]
        if missing:
            raise RuntimeError("Please connect first: " + ", ".join(missing))
        self.state.last_current_trip = False
        self.clear_sr830_range_floor(active_lockins, zurich=True)
        current_rms_ma = float(settings["current_rms_ma"])
        frequency_hz = float(settings["frequency_hz"])
        compliance_v = float(settings["compliance_v"])
        validated = validate_ppms_transport_scan_settings(
            current_rms_ma,
            frequency_hz,
            compliance_v,
            settings["harmonic_a"],
            settings["harmonic_b"],
            active_lockins,
            allow_no_lockins=bool(settings.get("allow_no_lockins", False)),
        )
        peak_a = validated["peak_a"]
        sweep_peak = float(settings.get("continuous_sweep_peak_a", peak_a))
        if not math.isfinite(sweep_peak) or not peak_a <= sweep_peak <= K6221_MAX_ABS_CURRENT_A:
            raise ValueError("Invalid continuous current sweep range")
        source = self.state.ppms_instrument_controllers["6221"]
        source.safe_output_off()
        with source.lock:
            source.instrument.write("*RST")
            source.instrument.write("*CLS")
        self.initialize_6221()
        if "continuous_sweep_peak_a" in settings:
            self.write_6221_checked(f"SOUR:CURR:RANG {sweep_peak:.12e}")
            actual_range = self.query_numbers("6221", "SOUR:CURR:RANG?", 1)[0]
            if actual_range * 1.05 + 1e-12 < sweep_peak:
                raise RuntimeError("6221 current range is too small for the sweep")
        for command in (
            f"CURR:COMP {compliance_v:.12g}",
            "SOUR:WAVE:FUNC SIN",
            f"SOUR:WAVE:FREQ {frequency_hz:.12g}",
            f"SOUR:WAVE:AMPL {peak_a:.12e}",
            "SOUR:WAVE:OFFS 0",
            "SOUR:WAVE:RANG FIX"
            if "continuous_sweep_peak_a" in settings
            else "SOUR:WAVE:RANG BEST",
            "SOUR:WAVE:DUR:CYCL INF",
            f"SOUR:WAVE:PMAR {NONLOCAL_MARKER_PHASE_DEG:g}",
            f"SOUR:WAVE:PMAR:OLIN {NONLOCAL_MARKER_LINE}",
            "SOUR:WAVE:PMAR:STAT ON",
            "OUTP OFF",
        ):
            self.write_6221_checked(command)
        actual_compliance = self.query_numbers("6221", "CURR:COMP?", 1)[0]
        actual_frequency = self.query_numbers("6221", "SOUR:WAVE:FREQ?", 1)[0]
        actual_peak = self.query_numbers("6221", "SOUR:WAVE:AMPL?", 1)[0]
        if not math.isclose(
            actual_compliance, compliance_v, rel_tol=0.0005, abs_tol=1e-06
        ):
            raise RuntimeError(
                f"6221 compliance readback mismatch: requested {compliance_v:g} V, read {actual_compliance:g} V"
            )
        frequency_tol_hz = settings.get("frequency_readback_tol_hz")
        if not (
            math.isclose(actual_frequency, frequency_hz, rel_tol=0.005, abs_tol=0.1)
            if frequency_tol_hz is None
            else abs(actual_frequency - frequency_hz) <= float(frequency_tol_hz)
        ):
            raise RuntimeError(
                f"6221 frequency readback mismatch: requested {frequency_hz:g} Hz, read {actual_frequency:g} Hz"
            )
        if not math.isclose(actual_peak, peak_a, rel_tol=5e-4, abs_tol=1e-12):
            raise RuntimeError(
                f"6221 amplitude readback mismatch: requested {peak_a:g} Apeak, read {actual_peak:g} Apeak"
            )
        harmonic_keys = PPMS_SR830_HARMONIC_SETTING_KEYS
        for key in active_lockins:
            controller = self.state.ppms_instrument_controllers[key]
            self.write_sr830_checked(key, "ILIN 0")
            self.write_sr830_checked(key, "SYNC 0")
            harmonic = int(settings[harmonic_keys[key]])
            address = ppms_sr830_address(controller.resource_name)
            actual_harmonic = int(round(self.query_numbers(key, "HARM?", 1)[0]))
            if actual_harmonic != harmonic:
                self.write_sr830_checked(key, f"HARM {harmonic}")
                actual_harmonic = int(round(self.query_numbers(key, "HARM?", 1)[0]))
            reference_mode = int(round(self.query_numbers(key, "FMOD?", 1)[0]))
            reference_slope = int(round(self.query_numbers(key, "RSLP?", 1)[0]))
            if reference_mode != 0 or reference_slope != PPMS_SR830_REFERENCE_SLOPE:
                self.write_sr830_checked(key, "FMOD 0")
                self.write_sr830_checked(key, f"RSLP {PPMS_SR830_REFERENCE_SLOPE}")
                reference_mode = int(round(self.query_numbers(key, "FMOD?", 1)[0]))
                reference_slope = int(round(self.query_numbers(key, "RSLP?", 1)[0]))
            if (
                actual_harmonic != harmonic
                or reference_mode != 0
                or reference_slope != PPMS_SR830_REFERENCE_SLOPE
            ):
                raise RuntimeError(
                    f"SR830 #{address} configuration readback mismatch: HARM={actual_harmonic}, FMOD={reference_mode}, RSLP={reference_slope}"
                )
            self._read_sr830_time_constant_for_scan(key, address, sink)
        for command in ("SOUR:WAVE:ABOR", "SOUR:WAVE:ARM", "SOUR:WAVE:INIT"):
            self.write_6221_checked(command)
        output_on = int(round(self.query_numbers("6221", "OUTP?", 1)[0]))
        if output_on != 1:
            raise RuntimeError("6221 sine output did not turn on")
        reference_timeout_s = float(
            settings.get("reference_timeout_s", PPMS_TRANSPORT_REFERENCE_TIMEOUT_S)
        )
        deadline = time.monotonic() + reference_timeout_s
        last_reference = {}
        while time.monotonic() < deadline:
            locked = True
            for key in active_lockins:
                frequency = self.query_numbers(key, "FREQ?", 1)[0]
                status = self.query_lias(key, sink)
                last_reference[key] = (frequency, status)
                if status & 8 or not math.isclose(
                    frequency, frequency_hz, rel_tol=0.005, abs_tol=0.1
                ):
                    locked = False
            if locked:
                if sink is not None:
                    self.post_ui(
                        lambda c=current_rms_ma, f=frequency_hz: sink.on_transport_status(
                            f"Transport acquisition: 6221 {c:g} mA RMS / {f:g} Hz ON; connected SR830 external references locked"
                        )
                    )
                return
            if self.state.scan_stop_event.wait(0.2):
                raise PPMSScanCancelled(
                    "Stopped as requested while waiting for the lock-in reference"
                )
        detail = "; ".join(
            f"SR830 #{ppms_sr830_address(self.state.ppms_instrument_controllers[key].resource_name)}: {value[0]:.6g} Hz, LIAS={value[1]}"
            for key, value in last_reference.items()
        )
        raise RuntimeError(
            f"Connected SR830 failed to lock the 6221 external reference within {reference_timeout_s:g} s; {detail}"
        )

    def update_transport_current(self, settings: dict[str, object]) -> None:
        if self.state.scan_stop_event.is_set() or self.state.closed:
            raise PPMSScanCancelled("Scan stopped; current not updated")
        validated = validate_ppms_transport_scan_settings(
            settings["current_rms_ma"],
            settings["frequency_hz"],
            settings["compliance_v"],
            settings["harmonic_a"],
            settings["harmonic_b"],
            settings["active_lockins"],
            allow_no_lockins=bool(settings.get("allow_no_lockins", False)),
        )
        peak = validated["peak_a"]
        if peak > settings["continuous_sweep_peak_a"] + 1e-12:
            raise ValueError("Current target exceeds reserved sweep range")
        if self.query_numbers("6221", "OUTP?", 1)[0] != 1:
            raise RuntimeError("6221 output is off; continuous sweep cannot continue")
        self.write_6221_checked(f"SOUR:WAVE:AMPL {peak:.12e}")
        actual = self.query_numbers("6221", "SOUR:WAVE:AMPL?", 1)[0]
        if not math.isclose(actual, peak, rel_tol=5e-4, abs_tol=1e-12):
            raise RuntimeError("6221 live amplitude readback mismatch")
        if self.query_numbers("6221", "OUTP?", 1)[0] != 1:
            raise RuntimeError("6221 output stopped during amplitude update")

    def read_transport_sample(
        self,
        expected_frequency_hz: float,
        active_lockins: tuple[str, ...],
        allow_down: bool = True,
        sink: ScanEventSink | None = None,
    ) -> dict[str, object]:
        output_on = int(round(self.query_numbers("6221", "OUTP?", 1)[0]))
        measurement_condition = int(
            round(self.query_numbers("6221", "STAT:MEAS:COND?", 1)[0])
        )
        if output_on != 1:
            raise RuntimeError("6221 output turned off unexpectedly during the scan")
        if measurement_condition & 8:
            self.state.last_current_trip = True
            if sink is not None:
                self.post_ui(sink.on_compliance_trip)
            raise RuntimeError(
                "6221 hit compliance; scan stopped and output turned off"
            )
        sample: dict[str, object] = {
            "6221_output_on": True,
            "6221_measurement_condition": measurement_condition,
        }
        zero_current = self.query_numbers("6221", "SOUR:WAVE:AMPL?", 1)[0] == 0.0
        for key in active_lockins:
            address = ppms_sr830_address(
                self.state.ppms_instrument_controllers[key].resource_name
            )
            prefix = f"sr830_{address}"
            x, y, r_value, theta, frequency = self.query_numbers(
                key, "SNAP? 1,2,3,4,9", 5
            )
            lia_status = self._read_sr830_status(key, sink=sink)
            reference_valid = not (lia_status & 8) and math.isclose(
                frequency, expected_frequency_hz, rel_tol=0.005, abs_tol=0.1
            )
            sample.update(
                {
                    f"{prefix}_x_v": x,
                    f"{prefix}_y_v": y,
                    f"{prefix}_r_v": r_value,
                    f"{prefix}_theta_deg": theta,
                    f"{prefix}_frequency_hz": frequency,
                    f"{prefix}_lias": lia_status,
                    f"{prefix}_reference_valid": reference_valid,
                }
            )
            settling = self._autorange_sr830(
                key,
                r_value,
                lia_status,
                enabled=not zero_current,
                allow_down=allow_down,
                sink=sink,
            )
            sample["range_settle_s"] = max(sample.get("range_settle_s", 0.0), settling)
            sample[f"{prefix}_overload"] = bool(lia_status & 7)
            sample[f"{prefix}_sensitivity_index"] = self.state.sr830_range_states[
                key
            ].sensitivity_index
        return sample

    def read_zurich_ranged(
        self,
        frequency: float,
        *,
        scanning: bool = False,
        sink: ScanEventSink | None = None,
    ) -> dict[str, object]:
        actions: list[str] = []
        for attempt in range(4):
            if (
                self.state.closed
                or (scanning and self.state.scan_stop_event.is_set())
            ):
                raise PPMSScanCancelled("Zurich autorange wait cancelled")
            enabled = (
                scanning or self.state.idle_autorange_enabled
            ) and not self.state.k6221_zero_output
            initial = enabled and self.state.zurich_autorange_pending
            with self.state.zurich_controller.lock:
                sample = self.state.zurich_controller.read_stfmr_mfli(
                    frequency,
                    auto_range=enabled,
                    initial=initial,
                    attempts=attempt,
                )
            if initial:
                self.state.zurich_autorange_pending = False
            action = sample.get("auto_range", "")
            if not action:
                return dict(sample, ppms_autorange_actions="; ".join(actions))
            actions.append(str(action))
            self._log(sink, f"PPMS Zurich autorange: {action}")
            if sink is not None:
                text = str(action)
                self.post_ui(lambda t=text: sink.on_zurich_autorange(t))
            tau = float(
                self.state.zurich_controller.mfli_config.get(
                    "timeconstant_s", PPMS_ZURICH_TIME_CONSTANT_S
                )
            )
            deadline = time.monotonic() + max(0.2, 10.0 * tau)
            while time.monotonic() < deadline:
                if (
                    self.state.closed
                    or (scanning and self.state.scan_stop_event.is_set())
                ):
                    raise PPMSScanCancelled("Zurich autorange wait cancelled")
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        raise RuntimeError(
            "Zurich autorange did not settle; current point not saved; field kept"
        )

    def average_transport_sample(
        self,
        frequency: float,
        lockins: tuple[str, ...],
        duration: float,
        sink: ScanEventSink | None = None,
    ) -> dict[str, object]:
        start = call_start = time.monotonic()
        range_limit_s = PPMS_SR830_RANGE_LIMIT_S
        count, sums, last = 0, {}, None
        overloaded_samples = 0
        first_reading = True
        trace = self.state.sample_trace
        if trace is not None:
            trace.clear()
        while True:
            if self.state.scan_stop_event.is_set() or self.state.closed:
                raise PPMSScanCancelled(
                    "Acquisition aborted; the unfinished point is not saved"
                )
            sample = self.read_transport_sample(
                frequency, lockins, first_reading, sink=sink
            )
            first_reading = False
            if self.state.scan_stop_event.is_set() or self.state.closed:
                raise PPMSScanCancelled(
                    "Acquisition aborted; the unfinished point is not saved"
                )
            settling = float(sample.get("range_settle_s", 0.0))
            if settling:
                range_limit_s = max(
                    range_limit_s, PPMS_SR830_RANGE_LIMIT_STEPS * settling
                )
                if time.monotonic() + settling > call_start + range_limit_s:
                    raise PPMSLockinRangeError(
                        f"SR830 autorange did not settle within {range_limit_s:.0f} s; current point not saved"
                    )
                if self.state.scan_stop_event.wait(settling):
                    raise PPMSScanCancelled(
                        "Autorange wait stopped; current point not saved"
                    )
                start = time.monotonic()
                count, sums, overloaded_samples = 0, {}, 0
                if trace is not None:
                    trace.clear()
                continue
            for name, value in sample.items():
                if name.endswith("_reference_valid") and not value:
                    raise RuntimeError(
                        "SR830 reference unlocked or frequency mismatch during acquisition; acquisition stopped"
                    )
            if any(
                name.endswith("_overload") and value for name, value in sample.items()
            ):
                overloaded_samples += 1
                last = dict(sample)
            else:
                for name, value in sample.items():
                    if name.endswith(("_x_v", "_y_v", "_frequency_hz")):
                        sums[name] = sums.get(name, 0.0) + float(value)
                count += 1
                last = dict(sample)
                if trace is not None:
                    trace.append(
                        (
                            time.monotonic(),
                            {
                                name: float(value)
                                for name, value in sample.items()
                                if name.endswith(("_x_v", "_y_v"))
                            },
                        )
                    )
            remaining = duration - (time.monotonic() - start)
            if remaining <= 0:
                if count == 0:
                    raise PPMSLockinRangeError(
                        "Every SR830 reading in the acquisition window was overloaded; current point not saved"
                    )
                break
            if self.state.scan_stop_event.wait(min(0.05, remaining)):
                raise PPMSScanCancelled(
                    "Acquisition aborted; the unfinished point is not saved"
                )
        for name, value in sums.items():
            last[name] = value / count
        for name in sums:
            if name.endswith("_x_v"):
                prefix = name[:-4]
                x, y = last[name], last[prefix + "_y_v"]
                last[prefix + "_r_v"] = math.hypot(x, y)
                last[prefix + "_theta_deg"] = math.degrees(math.atan2(y, x))
        last.update(
            acquisition_s=duration,
            acquisition_elapsed_s=time.monotonic() - start,
            sample_count=count,
            overloaded_sample_count=overloaded_samples,
        )
        if self.state.zurich_scan_enabled:
            with self.state.zurich_controller.lock:
                all_demods = self.state.zurich_controller.read_mfli_four_demods()
                measured = (
                    self.read_zurich_ranged(frequency, scanning=True, sink=sink)
                    if all_demods["zurich_demod1_enabled"]
                    else None
                )
                if measured is not None and measured.get("ppms_autorange_actions"):
                    all_demods = self.state.zurich_controller.read_mfli_four_demods()
            if measured is not None and (
                not measured["reference_valid"] or measured.get("input_overload")
            ):
                raise RuntimeError(
                    f"Zurich reference unlocked or input overload: {measured['status_text']}; field kept"
                )
            if measured is not None and not all(
                math.isfinite(float(measured[k]))
                for k in ("X", "Y", "R", "phase_deg")
            ):
                raise RuntimeError("Zurich readback is not finite; field kept")
            last.update(all_demods)
            last.update(
                zurich_x_v=all_demods["zurich_demod1_x_v"],
                zurich_y_v=all_demods["zurich_demod1_y_v"],
                zurich_r_v=all_demods["zurich_demod1_r_v"],
                zurich_theta_deg=all_demods["zurich_demod1_theta_deg"],
                zurich_reference_hz=(
                    measured["reference_frequency_hz"]
                    if measured is not None
                    else ""
                ),
                zurich_harmonic=self.state.zurich_scan_harmonic,
                zurich_sample_count=int(measured is not None),
                zurich_input_range_v=(
                    measured.get("input_range_v", "") if measured else ""
                ),
                zurich_autorange=(
                    measured.get("ppms_autorange_actions", "") if measured else ""
                ),
            )
            if measured is not None and sink is not None:
                snapshot = dict(measured)
                self.post_ui(lambda s=snapshot: sink.on_zurich_sample(s))
        return last

    @staticmethod
    def first_point_settled(previous, current) -> bool:
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
            tolerance = max(
                PPMS_FIRST_POINT_TOLERANCE_FRACTION * math.hypot(x1, y1), floor
            )
            checked = True
            if abs(x1 - x0) > tolerance or abs(y1 - y0) > tolerance:
                return False
        return checked

    def _acquire_first_point(
        self,
        frequency: float,
        lockins: tuple[str, ...],
        acquisition_s: float,
        sink: ScanEventSink | None = None,
    ) -> tuple[dict[str, object], int]:
        self.clear_sr830_range_floor()
        previous = None
        sample = None
        for window in range(1, PPMS_FIRST_POINT_MAX_WINDOWS + 1):
            if sink is not None:
                self.post_ui(
                    lambda w=window: sink.on_status(
                        f"First point: settling, window {w}/{PPMS_FIRST_POINT_MAX_WINDOWS} of {acquisition_s:g} s…"
                    )
                )
            sample = self.average_transport_sample(
                frequency, lockins, acquisition_s, sink=sink
            )
            if previous is not None and self.first_point_settled(previous, sample):
                return sample, window
            previous = sample
        self._log(
            sink,
            f"PPMS first point did not settle within {PPMS_FIRST_POINT_MAX_WINDOWS} windows; keeping the last one",
        )
        return sample, PPMS_FIRST_POINT_MAX_WINDOWS

    def wait_zurich_reference(
        self,
        frequency: float,
        timeout_s: float,
        previous_hz: float | None = None,
        sink: ScanEventSink | None = None,
    ) -> None:
        frequency = float(frequency)
        self.state.zurich_expected_frequency = frequency
        if not self.state.zurich_scan_enabled:
            return
        stop = self.state.scan_stop_event
        try:
            previous = float(previous_hz)
        except (TypeError, ValueError):
            previous = math.nan
        unchanged = (
            not math.isfinite(previous)
            or abs(previous - frequency) <= PPMS_FREQUENCY_READBACK_TOL_HZ
        )
        deadline = time.monotonic() + float(timeout_s)
        in_a_row = 0
        while True:
            if stop.is_set() or self.state.closed:
                raise PPMSScanCancelled(
                    "Stopped as requested while waiting for the Zurich reference"
                )
            with self.state.zurich_controller.lock:
                sample = self.state.zurich_controller.read_mfli(frequency)
            try:
                reported = float(sample.get("reference_frequency_hz"))
            except (TypeError, ValueError):
                reported = math.nan
            followed = unchanged or (
                math.isfinite(reported)
                and abs(reported - frequency) < abs(reported - previous)
            )
            if sample.get("reference_valid") and followed:
                in_a_row += 1
                if in_a_row >= PPMS_ZURICH_LOCK_READS:
                    if sink is not None:
                        snapshot = dict(sample)
                        self.post_ui(lambda s=snapshot: sink.on_zurich_sample(s))
                    return
            else:
                in_a_row = 0
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Zurich failed to lock the 6221 external reference at {frequency:g} Hz within {float(timeout_s):g} s (it reads {reported:g} Hz); {sample.get('status_text', '')}"
                )
            if stop.wait(0.2):
                raise PPMSScanCancelled(
                    "Stopped as requested while waiting for the Zurich reference"
                )

    def _stop_transport_source(
        self, *, read_back: bool = False, sink: ScanEventSink | None = None
    ) -> None:
        self.state.idle_autorange_enabled = False
        controller = self.state.ppms_instrument_controllers["6221"]
        controller.safe_output_off()
        text = "Transport acquisition: 6221 output OFF"
        if read_back:
            try:
                state = self.query_numbers("6221", "OUTP?", 1)[0]
            except Exception as error:
                text = f"Transport acquisition: 6221 OFF sent; read-back failed: {short_status_error(error, 60)}"
            else:
                if state == 0:
                    text = "Transport acquisition: 6221 output OFF (read back)"
                else:
                    text = f"Transport acquisition: 6221 still ON (OUTP?={state:g}); switch it off"
                    self._log(
                        sink,
                        f"PPMS: the 6221 still reads ON (OUTP?={state:g}) after the scan's output off",
                    )
        if sink is not None and not self.state.closed:
            self.post_ui(lambda t=text: sink.on_transport_status(t))

    def _zero_6221_after_scan(self) -> str:
        controller = self.state.ppms_instrument_controllers["6221"]
        if not controller.connected:
            return "not connected"
        self.state.idle_autorange_enabled = False
        with controller.lock:
            controller.safe_output_off()
            state = self.query_numbers("6221", "OUTP?", 1)[0]
        if state != 0:
            raise RuntimeError(
                f"the 6221 output did not read back OFF (OUTP? {state:g})"
            )
        return "zeroed"

    def _zero_outputs_after_scan(
        self, sink: ScanEventSink | None = None
    ) -> dict[str, str]:
        results = zero_scan_outputs((("6221", self._zero_6221_after_scan),))
        try:
            self._rec_event("outputs_zeroed", **results)
            self._log(sink, f"PPMS scan end: 6221 {results['6221']}")
            if sink is not None and not self.state.closed:
                self.post_ui(lambda r=dict(results): sink.on_outputs_zeroed(r))
        except Exception:
            pass
        return results

    def _preserve_stopped_scan(self) -> bool:
        return (
            self.state.scan_preserve_on_stop
            and self.state.scan_stop_event.is_set()
            and not self.state.closed
        )

    def _zero_field_after_interrupted_scan(
        self, rate_oe_s: float, approach_code: int, sink: ScanEventSink | None = None
    ) -> bool:
        if self.state.scan_field_zero_accepted:
            return True
        zero_rate = 100.0 if self.state.scan_stop_event.is_set() else rate_oe_s
        if sink is not None and not self.state.closed:
            self.post_ui(
                lambda: sink.on_persistent_follow_up_ended("The scan clean-up")
            )
        try:
            self.state.ppms_environment_controller.set_field(
                0.0, zero_rate, approach_code, 1
            )
        except BaseException as error:
            self._log(
                sink, f"PPMS: zeroing after the interrupted field scan failed: {error}"
            )
            self.state.scan_field_note = PPMS_FIELD_NOT_ZEROED_TEXT
            return False
        self.state.scan_field_zero_accepted = True
        self.state.scan_field_note = "field ramping to 0 Oe"
        return True

    def _field_error_after_zero(
        self, error, rate_oe_s: float, approach_code: int, sink: ScanEventSink | None
    ):
        if getattr(error, "ppms_field_zero_tried", False):
            return error
        if not self._zero_field_after_interrupted_scan(rate_oe_s, approach_code, sink):
            failure = PPMSFieldScanError(f"{error}; {PPMS_FIELD_NOT_ZEROED_TEXT}")
            failure.__cause__ = error
            error = failure
        error.ppms_field_zero_tried = True
        return error

    def _set_up_frequency(
        self,
        settings: dict[str, object],
        index: int,
        count: int,
        sink: ScanEventSink | None,
    ) -> None:
        stop = self.state.scan_stop_event
        frequency = float(settings["frequency_hz"])
        if stop.is_set() or self.state.closed:
            raise PPMSScanCancelled("Scan stopped; the next frequency was not set up")
        if sink is not None:
            self.post_ui(
                lambda: sink.on_status(
                    f"Frequency {index}/{count}: {frequency:g} Hz; 6221 off, setting up again…"
                )
            )
        source = self.state.ppms_instrument_controllers["6221"]
        with source.lock:
            previous_hz = self.query_numbers("6221", "SOUR:WAVE:FREQ?", 1)[0]
            source.safe_output_off()
            state = self.query_numbers("6221", "OUTP?", 1)[0]
        if state != 0:
            raise RuntimeError(
                f"6221 output still ON (OUTP?={state:g}) before setting up {frequency:g} Hz; scan stopped"
            )
        if sink is not None:
            self.post_ui(
                lambda: sink.on_transport_status(
                    f"Transport acquisition: 6221 OFF (read back); setting up {frequency:g} Hz"
                )
            )
        self.configure_transport_scan(settings, sink=sink)
        self.wait_zurich_reference(
            frequency,
            float(
                settings.get("reference_timeout_s", PPMS_TRANSPORT_REFERENCE_TIMEOUT_S)
            ),
            previous_hz,
            sink=sink,
        )

    def _frequency_scan_block(
        self,
        sequence,
        rate: float,
        stable: float,
        approach: int,
        settings: dict[str, object],
        sink: ScanEventSink | None,
    ) -> None:
        stop = self.state.scan_stop_event
        frequencies = tuple(float(value) for value in settings["frequencies"])
        currents = tuple(settings.get("currents", (settings["current_rms_ma"],)))
        sweep = settings.get("scan_mode") == "Frequency+Current"
        sweep_peak = max(currents) * math.sqrt(2.0) * 1e-3 if sweep else None
        for index, frequency in enumerate(frequencies, 1):
            for position, current in enumerate(currents):
                if stop.is_set() or self.state.closed:
                    return
                point_settings = dict(
                    settings,
                    current_rms_ma=current,
                    frequency_hz=frequency,
                    peak_a=math.sqrt(2.0) * current * 1e-3,
                    reference_timeout_s=NONLOCAL_IV6221_REFERENCE_WAIT_S,
                    frequency_readback_tol_hz=PPMS_FREQUENCY_READBACK_TOL_HZ,
                )
                if sweep:
                    point_settings["continuous_sweep_peak_a"] = sweep_peak
                if position == 0:
                    self._set_up_frequency(point_settings, index, len(frequencies), sink)
                    self.state.source_fresh = True
                    point_settings["frequency_settle_s"] = (
                        NONLOCAL_FSCAN_SETTLE_PERIODS / frequency
                    )
                else:
                    self.update_transport_current(point_settings)
                point_settings["keep_source_between_current_points"] = True
                self.field_scan_worker(
                    sequence, rate, stable, approach, point_settings, sink=sink
                )

    def field_scan_worker(
        self,
        sequence,
        rate_oe_s: float,
        stable_s: float,
        approach_code: int,
        transport_settings: dict[str, object],
        *,
        sink: ScanEventSink | None = None,
    ) -> None:
        failed = False
        preserve_range_output = False
        last_field = None
        command_field = transport_settings.get("scan_mode", "Field") in (
            "Field",
            "Field+Temperature",
        )
        check_field = (
            transport_settings.get("scan_mode", "Field") in PPMS_FIELD_COMMANDING_MODES
        )
        if self.state.last_readback is not None:
            try:
                last_field = float(self.state.last_readback["field_oe"])
            except (KeyError, TypeError, ValueError):
                last_field = None
        try:
            if not (
                transport_settings.get("keep_source_between_angles", False)
                or transport_settings.get("keep_source_between_current_points", False)
            ):
                self.configure_transport_scan(transport_settings, sink=sink)
                self.state.source_fresh = True
            for completed, (loop_index, point_index, points_per_loop, target) in enumerate(
                sequence, start=1
            ):
                if self.state.scan_stop_event.is_set():
                    break
                if command_field:
                    try:
                        result = self.state.ppms_environment_controller.set_field(
                            target, rate_oe_s, approach_code, 1
                        )
                    except Exception as error:
                        raise PPMSFieldScanError(f"Field set failed: {error}") from error
                else:
                    result = self.read_live()
                distance = (
                    abs(target - last_field) if last_field is not None else abs(target)
                )
                timeout_s = distance / rate_oe_s + 180.0
                deadline = time.monotonic() + timeout_s
                while check_field and not ppms_field_is_ready(
                    result, target, driven=True
                ):
                    if self.state.scan_stop_event.wait(PPMS_SCAN_POLL_S):
                        break
                    if time.monotonic() >= deadline:
                        raise PPMSFieldScanError(
                            f"PPMS timed out waiting for {target:+g} Oe to settle; current {float(result['field_oe']):+.7g} Oe / {result['field_status']}"
                        )
                    result = self.read_live()
                    if sink is not None:
                        snapshot = dict(result)
                        self.post_ui(
                            lambda d=snapshot: sink.on_environment_readback(d)
                        )
                if self.state.scan_stop_event.is_set():
                    break
                settle_s, slowest_tc_s = self._point_settle(
                    stable_s, tuple(transport_settings["active_lockins"]), sink
                )
                periods_s = float(transport_settings.get("frequency_settle_s", 0.0))
                if periods_s > settle_s:
                    settle_s = periods_s
                    periods_text = (
                        f"this frequency's first reading waits {ppms_settle_status_text(periods_s)} s: "
                        f"{NONLOCAL_FSCAN_SETTLE_PERIODS:g} periods of "
                        f"{float(transport_settings['frequency_hz']):g} Hz"
                    )
                    self._log(sink, f"PPMS scan: {periods_text}")
                    if sink is not None:
                        self.post_ui(
                            lambda t=periods_text: sink.on_status(
                                t[0].upper() + t[1:]
                            )
                        )
                if settle_s > 0 and self.state.scan_stop_event.wait(settle_s):
                    break
                result = self.read_live()
                if check_field and not ppms_field_is_ready(
                    result, target, driven=True
                ):
                    raise PPMSFieldScanError(
                        "Field drifted from the stable target before measurement; acquisition stopped"
                    )
                expected_t = self.state.scan_context.get("target_temperature_k", "")
                if expected_t != "" and abs(float(result["temperature_k"]) - expected_t) > 0.1:
                    raise RuntimeError(
                        "Temperature drifted more than 0.1 K from the target during the field scan; acquisition stopped"
                    )
                elapsed = time.monotonic() - self.state.scan_started_at
                row = {
                    **self.state.scan_context,
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
                source_fresh = self.state.source_fresh
                self.state.source_fresh = False
                if completed == 1 and (command_field or source_fresh):
                    transport_sample, windows = self._acquire_first_point(
                        float(transport_settings["frequency_hz"]),
                        tuple(transport_settings["active_lockins"]),
                        acquisition_s,
                        sink,
                    )
                    row["warmup_s"] = acquisition_s * (windows - 1)
                    row["first_point_windows"] = windows
                else:
                    row["warmup_s"] = 0.0
                    row["first_point_windows"] = 1
                    transport_sample = self.average_transport_sample(
                        float(transport_settings["frequency_hz"]),
                        tuple(transport_settings["active_lockins"]),
                        acquisition_s,
                        sink=sink,
                    )
                result = self.read_live()
                if check_field and not ppms_field_is_ready(
                    result, target, driven=True
                ):
                    raise PPMSFieldScanError(
                        "Field drifted from the target at the end of acquisition; current point not saved"
                    )
                if expected_t != "" and abs(float(result["temperature_k"]) - expected_t) > 0.1:
                    raise RuntimeError(
                        "Temperature drifted from the target at the end of acquisition; current point not saved"
                    )
                if self.state.scan_stop_event.is_set() or self.state.closed:
                    raise PPMSScanCancelled(
                        "Acquisition stopped; current point not saved"
                    )
                elapsed = time.monotonic() - self.state.scan_started_at
                row.update(
                    elapsed_s=elapsed,
                    measured_field_oe=float(result["field_oe"]),
                    field_status=str(result["field_status"]),
                    temperature_k=float(result["temperature_k"]),
                    temperature_status=str(result["temperature_status"]),
                )
                row.update(
                    {
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
                    }
                )
                if (
                    getattr(self.state.ppms_environment_controller, "backend", "")
                    == PPMS_QDINSTRUMENT_BACKEND
                ):
                    try:
                        motor_data = PPMSMotorDebug(
                            self.state.ppms_environment_controller.active
                        ).read()
                    except Exception as error:
                        raise PPMSMotorScanError(
                            f"Angle readback failed: {error}; field kept, not zeroed"
                        ) from error
                    row["measured_angle_deg"] = motor_data["angle_deg"]
                    expected_a = row.get("target_angle_deg", "")
                    if expected_a != "" and (
                        motor_data["status"] not in ("AtTarget", "AtIndexSwitch")
                        or abs(motor_data["angle_deg"] - expected_a) > 0.06
                    ):
                        raise PPMSMotorScanError(
                            "Motor drifted from the target or did not stop during acquisition; acquisition stopped; field kept, not zeroed"
                        )
                    result["motor_display"] = (
                        f"{motor_data['angle_deg']:.4f}° / {motor_data['status']}"
                    )
                else:
                    row["measured_angle_deg"] = ""
                result["lockin_reference"] = {}
                for key in tuple(transport_settings["active_lockins"]):
                    address = ppms_sr830_address(
                        self.state.ppms_instrument_controllers[key].resource_name
                    )
                    harmonic_key = PPMS_SR830_HARMONIC_SETTING_KEYS[key]
                    row[f"sr830_{address}_harmonic"] = int(
                        transport_settings[harmonic_key]
                    )
                    result["lockin_reference"][key] = (
                        f"{transport_sample[f'sr830_{address}_frequency_hz']:.5g} Hz / "
                        + (
                            "LOCK reference locked"
                            if transport_sample[f"sr830_{address}_reference_valid"]
                            else "WARNING reference unlocked or frequency mismatch"
                        )
                    )
                verified_codes = self.state.sr830_time_constant_codes
                for key in tuple(transport_settings["active_lockins"]):
                    address = ppms_sr830_address(
                        self.state.ppms_instrument_controllers[key].resource_name
                    )
                    code = verified_codes.get(key)
                    row[f"sr830_{address}_time_constant_s"] = (
                        SR830_TIME_CONSTANTS_S[code] if code is not None else ""
                    )
                row["effective_settle_s"] = settle_s
                row["slowest_lockin_tc_s"] = slowest_tc_s
                if "outer_field_index" in transport_settings:
                    row["outer_field_index"] = transport_settings["outer_field_index"]
                if self.state.scan_rows:
                    row = dict.fromkeys(self.state.scan_rows[0], "") | row
                self._rec_append_row(row)
                self.state.scan_rows.append(row)
                axis_column = PPMS_PLOT_AXIS_COLUMNS.get(
                    PPMS_SCAN_PLOT_AXES.get(
                        transport_settings.get("scan_mode", "Field"), "field"
                    ),
                    "measured_field_oe",
                )
                self._rec_event(
                    "point_saved",
                    index=len(self.state.scan_rows),
                    harmonic_pass=row.get("harmonic_pass", 1),
                    x_column=axis_column,
                    x=row.get(axis_column, ""),
                    target_field_oe=target,
                    measured_field_oe=row.get("measured_field_oe", ""),
                    temperature_k=row.get("temperature_k", ""),
                    current_rms_ma=row.get("6221_current_rms_ma", ""),
                    frequency_hz=row.get("6221_frequency_hz", ""),
                )
                self._rec_check()
                last_field = float(result["field_oe"])
                mean_point_s = elapsed / max(1, len(self.state.scan_rows))
                remaining_s = mean_point_s * max(
                    0, self.state.scan_total - len(self.state.scan_rows)
                )
                if sink is not None:
                    progress = len(self.state.scan_rows)
                    data_snap = dict(result)
                    sample_snap = dict(transport_sample)
                    self.post_ui(
                        lambda d=data_snap, p=progress, r=remaining_s, s=sample_snap: sink.on_point_recorded(
                            d, p, r, s
                        )
                    )
            if (
                check_field
                and self.state.scan_stop_event.is_set()
                and not self._preserve_stopped_scan()
            ):
                self._zero_field_after_interrupted_scan(rate_oe_s, approach_code, sink)
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
            raise self._field_error_after_zero(error, rate_oe_s, approach_code, sink)
        except BaseException:
            failed = True
            self._log(sink, "Non-field scan error: field setpoint kept, not zeroed")
            raise
        finally:
            try:
                if not preserve_range_output and (
                    failed
                    or self.state.closed
                    or (
                        self.state.scan_stop_event.is_set()
                        and not self._preserve_stopped_scan()
                    )
                ):
                    self._stop_transport_source(read_back=True, sink=sink)
            except BaseException as error:
                self._log(
                    sink,
                    f"PPMS: failed to turn the 6221 off at the end of the transport scan: {error}",
                )
                raise

    def nested_scan_worker(
        self,
        sequence,
        rate: float,
        stable: float,
        approach: int,
        settings: dict[str, object],
        *,
        sink: ScanEventSink | None = None,
    ) -> None:
        stop = self.state.scan_stop_event
        remote = self.state.ppms_environment_controller
        nested = settings.get("nested", {})

        def wait_until(
            predicate,
            timeout: float,
            label: str,
            cancelled: str = "Scan stopped; cancelling the motor wait does not mean the motor physically stopped",
        ):
            deadline = time.monotonic() + timeout
            good_since = None
            while not stop.is_set() and not self.state.closed:
                data = self.read_live()
                if sink is not None:
                    snap = dict(data)
                    self.post_ui(lambda d=snap: sink.on_environment_readback(d))
                if predicate(data):
                    good_since = good_since or time.monotonic()
                    if time.monotonic() - good_since >= max(1.0, stable):
                        return data
                else:
                    good_since = None
                if time.monotonic() >= deadline:
                    error_type = (
                        PPMSFieldScanError if label == "measured field" else TimeoutError
                    )
                    raise error_type(f"{label} settle timeout, last reading {data}")
                stop.wait(0.5)
            raise PPMSScanCancelled(cancelled)

        def prepare_temperature(temperature):
            if temperature is None:
                return
            if stop.is_set() or self.state.closed:
                raise PPMSScanCancelled("Scan stopped")
            accepted = {"near", "stable", "stableat target", "stableattarget"}
            current = float(self.read_live()["temperature_k"])
            typed_rate = float(settings["temperature_rate"])
            segments = ppms_scan_temperature_segments(current, temperature, typed_rate)
            for target, seg_rate in segments:
                restore_high_rate = (
                    seg_rate < typed_rate
                    and current <= PPMS_TEMPERATURE_LOW_BOUNDARY_K
                    and target > PPMS_TEMPERATURE_LOW_BOUNDARY_K
                )
                if stop.is_set() or self.state.closed:
                    raise PPMSScanCancelled("Scan stopped")
                self._log(
                    sink, f"Scan temperature segment: {target:g} K at {seg_rate:g} K/min"
                )
                try:
                    try:
                        remote.set_temperature(
                            target, seg_rate, settings["temperature_approach"]
                        )
                    except ValueError as error:
                        if seg_rate <= 1.0 or "must not exceed 1 K/min" not in str(
                            error
                        ):
                            raise
                        if stop.is_set() or self.state.closed:
                            raise PPMSScanCancelled("Scan stopped")
                        seg_rate = PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                        restore_high_rate = target > PPMS_TEMPERATURE_LOW_BOUNDARY_K
                        self._log(
                            sink,
                            f"9 K boundary rate limit: switched to 1 K/min automatically, continuing to {target:g} K",
                        )
                        remote.set_temperature(
                            target, seg_rate, settings["temperature_approach"]
                        )
                except RuntimeError as error:
                    if "SetTemperature" not in str(error) or "return code 1" not in str(
                        error
                    ):
                        raise
                    self._log(
                        sink,
                        f"Temperature command returned 1; waiting for the actual stable confirmation: {error}",
                    )

                def temperature_ready(data):
                    nonlocal restore_high_rate
                    measured = float(data["temperature_k"])
                    if restore_high_rate and measured > PPMS_TEMPERATURE_LOW_BOUNDARY_K:
                        if stop.is_set() or self.state.closed:
                            raise PPMSScanCancelled("Scan stopped")
                        try:
                            remote.set_temperature(
                                target, typed_rate, settings["temperature_approach"]
                            )
                        except ValueError as error:
                            if "must not exceed 1 K/min" not in str(error):
                                raise
                            return False
                        except RuntimeError as error:
                            if (
                                "SetTemperature" not in str(error)
                                or "return code 1" not in str(error)
                            ):
                                raise
                            self._log(
                                sink,
                                f"Rate-restore command reply uncertain; keeping the target and continuing readback: {error}",
                            )
                        restore_high_rate = False
                        self._log(
                            sink,
                            f"Temperature is above 9 K; target {target:g} K automatically restored to the typed {typed_rate:g} K/min (rate command sent)",
                        )
                        return False
                    return (
                        abs(measured - target) <= 0.1
                        and str(data["temperature_status"]).strip().lower() in accepted
                    )

                wait_until(temperature_ready, 14400, "temperature")

            delay = float(settings.get("temperature_delay_s", 0.0) or 0.0)
            if delay > 0:
                self._log(
                    sink,
                    f"Temperature {temperature:g} K reached; waiting {delay:g} s before the scan at it",
                )
                if sink is not None:
                    self.post_ui(
                        lambda: sink.on_status(
                            f"{temperature:g} K reached; waiting {delay:g} s"
                        )
                    )
                if stop.wait(delay) or self.state.closed:
                    raise PPMSScanCancelled(
                        "Scan stopped during the wait after the temperature"
                    )

        temperature_outer = settings.get("scan_mode") == "Angle+Temperature"
        continuous_current = settings.get("scan_mode") in (
            "Current",
            "Current+Temperature",
        )
        current_source_started = False
        sweep_peak = (
            max(settings.get("currents", (settings["current_rms_ma"],)))
            * math.sqrt(2.0)
            * 0.001
            if continuous_current
            else None
        )
        field_outer = settings.get("scan_mode") == "Angle+Field"
        last_field = [None]
        if field_outer:
            try:
                last_field[0] = float((self.state.last_readback or {})["field_oe"])
            except (KeyError, TypeError, ValueError):
                pass

        def set_outer_field(index: int, count: int, field: float) -> None:
            if stop.is_set() or self.state.closed:
                raise PPMSScanCancelled("Scan stopped; the next field was not set")
            self._rec_event(
                "field_step",
                index=index,
                count=count,
                target_field_oe=field,
                rate_oe_s=rate,
                approach_code=approach,
                mode="Driven",
            )
            self._rec_check()
            if sink is not None:
                self.post_ui(
                    lambda: sink.on_status(
                        f"Field {index}/{count}: {field:g} Oe at {rate:g} Oe/s; waiting for it to settle…"
                    )
                )
                self.post_ui(
                    lambda: sink.on_persistent_follow_up_ended("The field scan")
                )
            try:
                remote.set_field(field, rate, approach, 1)
            except Exception as error:
                raise PPMSFieldScanError(f"Field set failed: {error}") from error
            distance = (
                abs(field - last_field[0]) if last_field[0] is not None else abs(field)
            )
            data = wait_until(
                lambda d: ppms_field_is_ready(d, field, driven=True),
                max(3600.0, distance / rate + 180.0),
                "measured field",
                cancelled="Scan stopped while the field settled",
            )
            last_field[0] = float(data["field_oe"])
            self.state.source_fresh = True

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
            if settings.get("scan_mode") in (
                "Angle",
                "Angle+Temperature",
                "Angle+Field",
            ):
                self.configure_transport_scan(settings, sink=sink)
                self.state.source_fresh = True
                settings = dict(settings, keep_source_between_angles=True)
            for ai, angle, temperatures, outer_field in scan_blocks():
                if stop.is_set() or self.state.closed:
                    break
                self._rec_check()
                if angle is not None:
                    try:
                        motor = PPMSMotorDebug(remote.active)
                        if sink is not None:
                            motor.on_read = lambda d: self.post_ui(
                                lambda data=dict(d): sink.on_environment_readback(
                                    {
                                        "motor_display": f"{data['angle_deg']:.4f}° / {data['status']}"
                                    }
                                )
                            )
                        initial_motor = motor.read()
                        if initial_motor["status"] not in (
                            "AtTarget",
                            "AtIndexSwitch",
                        ):
                            raise RuntimeError(
                                f"Motor status unsafe before rotation: {initial_motor}"
                            )
                        current = initial_motor["angle_deg"]
                        for target in ppms_scan_axis(current, angle, 0.9)[1:]:
                            verified = motor.move_and_verify(
                                target,
                                settings["slowdown"],
                                cancelled=lambda: stop.is_set() or self.state.closed,
                                index_target=angle,
                            )
                            if (
                                verified is not None
                                and abs(verified["angle_deg"] - angle) <= 0.06
                            ):
                                break
                    except PPMSScanCancelled:
                        raise
                    except Exception as error:
                        raise PPMSMotorScanError(
                            f"{error}; field kept, not zeroed"
                        ) from error
                if settings.get("scan_mode") in ("Field", "Field+Temperature"):
                    if stop.is_set() or self.state.closed:
                        break
                    field_target = sequence[0][3]
                    if sink is not None:
                        self.post_ui(
                            lambda: sink.on_persistent_follow_up_ended("The field scan")
                        )
                    try:
                        remote.set_field(field_target, rate, approach, 1)
                    except Exception as error:
                        raise PPMSFieldScanError(f"Field set failed: {error}") from error
                    wait_until(
                        lambda d: ppms_field_is_ready(d, field_target, driven=True),
                        3600,
                        "measured field",
                    )
                for ti, temperature in temperatures:
                    if stop.is_set() or self.state.closed:
                        break
                    currents = tuple(
                        settings.get("currents", (settings["current_rms_ma"],))
                    )
                    stepped_to_first = False
                    if continuous_current and current_source_started:
                        self.update_transport_current(
                            dict(
                                settings,
                                current_rms_ma=currents[0],
                                peak_a=math.sqrt(2.0) * currents[0] * 0.001,
                                continuous_sweep_peak_a=sweep_peak,
                            )
                        )
                        self.state.source_fresh = True
                        stepped_to_first = True
                    if not temperature_outer:
                        prepare_temperature(temperature)
                    self.state.scan_context = {
                        "angle_index": ai,
                        "temperature_index": ti,
                        "target_angle_deg": angle if angle is not None else "",
                        "target_temperature_k": (
                            temperature if temperature is not None else ""
                        ),
                    }
                    if settings.get("scan_mode") in PPMS_FREQUENCY_SCAN_MODES:
                        self._frequency_scan_block(
                            sequence, rate, stable, approach, settings, sink
                        )
                        continue
                    for position, current in enumerate(currents):
                        if stop.is_set() or self.state.closed:
                            break
                        point_settings = dict(
                            settings,
                            current_rms_ma=current,
                            peak_a=math.sqrt(2.0) * current * 0.001,
                        )
                        if continuous_current:
                            point_settings["continuous_sweep_peak_a"] = sweep_peak
                            if not current_source_started:
                                self.configure_transport_scan(point_settings, sink=sink)
                                self.state.source_fresh = True
                                current_source_started = True
                            elif not (position == 0 and stepped_to_first):
                                self.update_transport_current(point_settings)
                            point_settings["keep_source_between_current_points"] = True
                        block_sequence = sequence
                        if outer_field is not None:
                            block_sequence = ((1, 1, 1, outer_field[1]),)
                            point_settings["outer_field_index"] = outer_field[0]
                        self.field_scan_worker(
                            block_sequence,
                            rate,
                            stable,
                            approach,
                            point_settings,
                            sink=sink,
                        )
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
            raise self._field_error_after_zero(error, rate, approach, sink)
        except BaseException:
            failed = True
            self._log(sink, "Non-field scan error: field setpoint kept, not zeroed")
            raise
        finally:
            self.state.scan_context = {}
            if not preserve_range_output and (
                failed
                or self.state.closed
                or (stop.is_set() and not self._preserve_stopped_scan())
            ):
                if (
                    (stop.is_set() or self.state.closed)
                    and not failed
                    and settings.get("scan_mode") in PPMS_FIELD_COMMANDING_MODES
                ):
                    self._zero_field_after_interrupted_scan(rate, approach, sink)
                self._stop_transport_source(read_back=True, sink=sink)

    def run(self, request: ScanRequest, sink: ScanEventSink | None = None) -> None:
        """Execute a full scan under ScanCloseHandoff, with Tk-independent finalization."""
        self.state.zero_persist_completed = False
        self.state.scan_outputs = None
        self.state.close_handoff.begin()
        self.state.scan_field_zero_accepted = False
        error: BaseException | None = None
        try:
            try:
                settings = dict(request.transport_settings)
                passes = settings.get("harmonic_passes", ())
                self._rec_event(
                    "scan_started",
                    mode=request.mode,
                    points=request.total_points,
                    harmonic_passes=len(passes),
                )
                self._rec_check()
                for index, pass_settings in enumerate(passes, 1):
                    if self.state.scan_stop_event.is_set() or self.state.closed:
                        break
                    self._rec_event(
                        "harmonic_pass",
                        harmonic_pass=pass_settings.get("harmonic_pass", index),
                        harmonics={
                            PPMS_SR830_LABELS.get(key, key): pass_settings.get(
                                PPMS_SR830_HARMONIC_SETTING_KEYS.get(key, "")
                            )
                            for key in pass_settings.get("active_lockins", ())
                        },
                    )
                    self._rec_check()
                    self.nested_scan_worker(
                        request.sequence,
                        request.rate_oe_s,
                        request.stable_s,
                        request.approach_code,
                        pass_settings,
                        sink=sink,
                    )
            finally:
                self.state.scan_outputs = self._zero_outputs_after_scan(sink=sink)

            if (
                not self.state.scan_stop_event.is_set()
                and not self.state.closed
                and self.state.zero_persist_pending
            ):
                if sink is not None:
                    self.post_ui(
                        lambda: sink.on_status(
                            "CSV saved; zeroing field and switching to Persistent…"
                        )
                    )
                    self.post_ui(
                        lambda: sink.on_persistent_follow_up_ended("Zero + persist")
                    )
                remote = self.state.ppms_environment_controller
                for mode in (1, 0):
                    if self.state.scan_stop_event.is_set() or self.state.closed:
                        raise PPMSScanCancelled("Zero/Persistent wrap-up cancelled")
                    remote.set_field(0.0, 100.0, 0, mode)
                    deadline = time.monotonic() + 1800
                    while True:
                        if self.state.scan_stop_event.is_set() or self.state.closed:
                            raise PPMSScanCancelled("Zero/Persistent wrap-up cancelled")
                        data = remote.read_environment()
                        if sink is not None:
                            snap = dict(data)
                            self.post_ui(
                                lambda d=snap: sink.on_environment_readback(d)
                            )
                        status = str(data.get("field_status", "")).strip().lower()
                        zero = ppms_field_is_ready(data, 0.0)
                        if zero and (
                            mode == 1 or status in ("persistent", "stablepersistent")
                        ):
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                "Scan data saved; timed out confirming zero field / Persistent"
                            )
                        self.state.scan_stop_event.wait(1.0)
                self.state.zero_persist_completed = True
        except BaseException as caught:
            error = caught
        finally:
            stopped = self.state.scan_stop_event.is_set()
            self.state.zero_persist_pending = False
            if sink is not None and not self.state.closed:
                self.post_ui(sink.on_zero_persist_button_reset)

            if self.state.closed:
                if (
                    request.mode in PPMS_FIELD_COMMANDING_MODES
                    and not self.state.scan_field_zero_accepted
                    and self.state.ppms_environment_controller.connected
                ):
                    try:
                        self._zero_field_after_interrupted_scan(
                            request.rate_oe_s, request.approach_code, sink
                        )
                    except Exception as zero_error:
                        self._log(
                            sink,
                            f"PPMS: zeroing the field before close failed: {zero_error}",
                        )

            results = self.state.scan_outputs
            field_note = str(self.state.scan_field_note or "")
            stop_reason = str(self.state.scan_stop_button or "")
            if isinstance(error, PPMSScanCancelled):
                self._log(sink, f"PPMS {request.mode} scan cancelled normally: {error}")
                stop_reason = f"{stop_reason}: {error}" if stop_reason else str(error)
                error = None
                stopped = True
            outputs_confirmed = bool(results) and all(
                str(state) in ("zeroed", "not connected")
                for state in results.values()
            )
            field_mode = request.mode in PPMS_FIELD_COMMANDING_MODES
            if error is not None:
                outcome = "error"
                zero_tail = (
                    ""
                    if outputs_confirmed
                    else "; 6221 zero FAILED"
                    if results
                    else "; 6221 not confirmed zeroed"
                )
                text = short_status_error(error, 100 - len(zero_tail))
                status = f"Failed: {text.rstrip(';') if zero_tail else text}{zero_tail}"
                if field_note == PPMS_FIELD_NOT_ZEROED_TEXT:
                    status += f"; {PPMS_FIELD_NOT_ZEROED_TEXT}"
            else:
                extras = []
                if self.state.closed:
                    outcome, prefix = "stopped", "Stopped; "
                    if not self.state.scan_stop_button:
                        stop_reason = "the program closing"
                    else:
                        stop_reason = stop_reason or "the program closing"
                elif stopped:
                    outcome, prefix = "stopped", "Stopped; "
                    if field_mode:
                        extras.append(field_note or "field kept at its setpoint")
                else:
                    outcome, prefix = "completed", "Completed; "
                    if self.state.zero_persist_completed:
                        extras.append("field zeroed and confirmed Persistent")
                status = (
                    prefix
                    + scan_end_outputs_text(results, ("6221",), check=not extras)
                    + "".join(f"; {extra}" for extra in extras)
                )

            rows_count = len(self.state.scan_rows)
            if sink is not None and not self.state.closed and self.state.run_recorder is not None:
                try:
                    sink.capture_plot_snapshots(self.state.run_recorder, rows_count)
                except Exception:
                    pass

            # Deterministic Tk-independent recorder.close() on the worker thread
            problems = self.close_run(
                outcome,
                sink=sink,
                points_saved=rows_count,
                status=status,
                outputs=results,
                error=str(error) if error is not None else "",
                **({"stop_reason": stop_reason} if outcome == "stopped" else {}),
            )

            self.state.scan_running = False
            if sink is not None:
                self.post_ui(
                    lambda: sink.on_scan_finished(
                        mode=request.mode,
                        outcome=outcome,
                        status=status,
                        results=results,
                        outputs_confirmed=outputs_confirmed,
                        field_note=field_note,
                        error=error,
                        problems=problems,
                    )
                )
            self.state.close_handoff.end()


__all__ = [
    "NONLOCAL_FSCAN_SETTLE_PERIODS",
    "NONLOCAL_FSCAN_START_HZ",
    "NONLOCAL_FSCAN_STOP_HZ",
    "NONLOCAL_IV6221_REFERENCE_WAIT_S",
    "PPMS_ACQUISITION_S",
    "PPMS_FIELD_RATE_DEFAULT_OE_S",
    "PPMS_FIRST_POINT_FLOOR_FRACTION",
    "PPMS_FIRST_POINT_MAX_WINDOWS",
    "PPMS_FIRST_POINT_TOLERANCE_FRACTION",
    "PPMS_PERSISTENT_CONFIRM_S",
    "PPMS_PERSISTENT_MARGIN_S",
    "PPMS_ROTATOR_MOVE_ALLOWANCE_S",
    "PPMS_SCAN_POLL_S",
    "PPMS_SET_UP_ALLOWANCE_S",
    "PPMS_SR830_DEFAULT_TIME_CONSTANT",
    "PPMS_SR830_DOWNRANGE_FRACTION",
    "PPMS_SR830_DOWNRANGE_READINGS",
    "PPMS_SR830_RANGE_COOLDOWN_READINGS",
    "PPMS_SR830_RANGE_LIMIT_S",
    "PPMS_SR830_RANGE_LIMIT_STEPS",
    "PPMS_SR830_REFERENCE_SLOPE",
    "PPMS_TEMPERATURE_ACQUISITION_S",
    "PPMS_TEMPERATURE_DELAY_S",
    "PPMS_TRANSPORT_COMPLIANCE_V",
    "PPMS_TRANSPORT_CURRENT_RMS_MA",
    "PPMS_TRANSPORT_FREQUENCY_HZ",
    "PPMS_TRANSPORT_HARMONIC",
    "PPMS_TRANSPORT_REFERENCE_TIMEOUT_S",
    "PPMS_ZURICH_LOCK_READS",
    "PPMS_ZURICH_TIME_CONSTANT_S",
    "PPMSScanRunner",
    "ScanEventSink",
    "ScanRequest",
    "format_scan_duration",
    "format_sr830_full_scale",
    "format_voltage_range",
    "ppms_field_is_ready",
    "ppms_sr830_address",
]
