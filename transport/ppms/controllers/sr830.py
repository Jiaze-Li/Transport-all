"""Stanford Research SR830 lock-in controller and display helpers."""

from __future__ import annotations

import math
import time
from typing import Callable

from transport.ppms.controllers.visa import SafeVisaIdentityController

LOCKIN_DOWNRANGE_FRACTION = 0.01
LOCKIN_DOWNRANGE_READINGS = 3
LOCKIN_UPRANGE_FRACTION = 0.85

STFMR_LOCKIN_SR830_SENSITIVITY_CODE = 13
STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE = 10
STFMR_LOCKIN_SR830_TIME_CONSTANTS = {
    "1 ms": 4,
    "3 ms": 5,
    "10 ms": 6,
    "30 ms": 7,
    "100 ms": 8,
    "300 ms": 9,
    "1 s": 10,
    "3 s": 11,
    "10 s": 12,
}
STFMR_LOCKIN_SR830_REFERENCE_INPUTS = {
    "Sine": 0,
    "TTL Rising": 1,
    "TTL Falling": 2,
}
STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT = "Sine"

NONLOCAL_SR830_FULL_SCALE_V = (
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6,
    500e-6, 1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3,
    200e-3, 500e-3, 1.0,
)
NONLOCAL_LOCKIN_TIME_CONSTANT_S = 1.0
NONLOCAL_LOCKIN_FILTER_ORDER = 2
SR830_TIME_CONSTANTS_S = (
    1e-05, 3e-05, 0.0001, 0.0003, 0.001, 0.003, 0.01, 0.03,
    0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0,
)

SR830_DISPLAY_XY_COMMANDS = ("DDEF 1,0,0", "DDEF 2,0,0")


def show_sr830_x_and_y(instrument, logger=None, label: str = "SR830") -> bool:
    """Configure SR830 front-panel displays to show X and Y."""
    try:
        for command in SR830_DISPLAY_XY_COMMANDS:
            instrument.write(command)
        return True
    except Exception as error:
        if logger is not None:
            logger(f"{label}: the display was not set to X and Y: {error}")
        return False


class SR830Controller(SafeVisaIdentityController):
    """Stanford Research SR830 lock-in controller with auto-range and readback."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        super().__init__(logger, "Stanford SR830", "SR830", owner_scope="STFMR")
        self.reference_input = STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT
        self.sensitivity_index = STFMR_LOCKIN_SR830_SENSITIVITY_CODE
        self.auto_range_enabled = True
        self.low_range_count = 0
        self.range_cooldown = 0
        self.overload_floor = 0

    def connect(self, resource_name: str) -> str:
        identity = super().connect(resource_name)
        with self.lock:
            if self.instrument is not None:
                show_sr830_x_and_y(self.instrument, self.logger, "STFMR SR830")
        return identity

    @staticmethod
    def _numbers(response: str, expected: int) -> list[float]:
        values = [float(item.strip()) for item in str(response).split(",")]
        if len(values) != expected or not all(math.isfinite(v) for v in values):
            raise RuntimeError(f"SR830 readback cannot be parsed: {response!r}")
        return values

    def configure(
        self,
        reference_input: str = STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT,
        auto_range: bool = True,
        time_constant_code: int = STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE,
    ) -> dict[str, object]:
        reference_input = str(reference_input).strip()
        if reference_input not in STFMR_LOCKIN_SR830_REFERENCE_INPUTS:
            choices = ", ".join(STFMR_LOCKIN_SR830_REFERENCE_INPUTS)
            raise ValueError(f"SR830 REF IN must be one of: {choices}")
        reference_slope = STFMR_LOCKIN_SR830_REFERENCE_INPUTS[reference_input]
        if time_constant_code not in STFMR_LOCKIN_SR830_TIME_CONSTANTS.values():
            raise ValueError(
                f"SR830 time constant code {time_constant_code!r} is not an 'SR830 TC' choice"
            )
        time_constant_code = int(time_constant_code)
        auto_range = bool(auto_range)
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SR830 is not connected")
            for command in (
                "*CLS",
                "FMOD 0",
                f"RSLP {reference_slope}",
                "HARM 1",
                "ISRC 0",
                "IGND 0",
                "ICPL 0",
                "ILIN 0",
                "SYNC 0",
                *((f"SENS {STFMR_LOCKIN_SR830_SENSITIVITY_CODE}",) if auto_range else ()),
                f"OFLT {time_constant_code}",
                "OFSL 1",
                "RMOD 1",
                *SR830_DISPLAY_XY_COMMANDS,
            ):
                self.instrument.write(command)
            actual_reference_mode = int(
                round(float(self.instrument.query("FMOD?").strip()))
            )
            actual_reference_slope = int(
                round(float(self.instrument.query("RSLP?").strip()))
            )
            actual_harmonic = int(
                round(float(self.instrument.query("HARM?").strip()))
            )
            actual_sensitivity = int(
                round(float(self.instrument.query("SENS?").strip()))
            )
            actual_time_constant = int(
                round(float(self.instrument.query("OFLT?").strip()))
            )
            if (
                actual_reference_mode != 0
                or actual_reference_slope != reference_slope
                or actual_harmonic != 1
            ):
                raise RuntimeError(
                    f"SR830 REF IN configuration readback mismatch: "
                    f"FMOD={actual_reference_mode}, RSLP={actual_reference_slope}, HARM={actual_harmonic}"
                )
            if auto_range and actual_sensitivity != STFMR_LOCKIN_SR830_SENSITIVITY_CODE:
                raise RuntimeError(
                    f"SR830 initial sensitivity readback mismatch: "
                    f"requested SENS {STFMR_LOCKIN_SR830_SENSITIVITY_CODE}, readback {actual_sensitivity}"
                )
            if not 0 <= actual_sensitivity < len(NONLOCAL_SR830_FULL_SCALE_V):
                raise RuntimeError(
                    f"SR830 SENS? read back {actual_sensitivity}, not a sensitivity index (0-26)"
                )
            if actual_time_constant != time_constant_code:
                raise RuntimeError(
                    f"SR830 time constant readback mismatch: "
                    f"requested OFLT {time_constant_code}, readback {actual_time_constant}"
                )
            self.reference_input = reference_input
            self.sensitivity_index = actual_sensitivity
            self.auto_range_enabled = auto_range
            self.low_range_count = 0
            self.range_cooldown = 0
            self.overload_floor = 0
            self.lias_clear_pending = True
            return {
                "reference_input": reference_input,
                "reference_slope": actual_reference_slope,
                "sensitivity_index": actual_sensitivity,
                "time_constant_index": actual_time_constant,
                "auto_range": self.auto_range_enabled,
            }

    def wait_for_reference(
        self,
        expected_frequency_hz: float,
        timeout_s: float = 5.0,
        previous_frequency_hz: float | None = None,
    ) -> dict[str, object]:
        expected = float(expected_frequency_hz)
        if not math.isfinite(expected) or expected <= 0:
            raise ValueError(
                "SR830 expected reference frequency must be a finite positive number"
            )
        previous = None
        if previous_frequency_hz is not None:
            previous = float(previous_frequency_hz)
            if not math.isfinite(previous) or previous == expected:
                previous = None
        deadline = time.monotonic() + max(float(timeout_s), 0.1)
        frequency = math.nan
        status = 8
        while time.monotonic() < deadline:
            with self.lock:
                if self.instrument is None:
                    raise RuntimeError("SR830 is not connected")
                frequency = float(self.instrument.query("FREQ?").strip())
                status = int(round(float(self.instrument.query("LIAS?").strip())))
            left_previous = (
                previous is None
                or abs(frequency - expected) < abs(frequency - previous)
            )
            if (
                not status & 8
                and left_previous
                and math.isclose(frequency, expected, rel_tol=0.005, abs_tol=0.1)
            ):
                return {
                    "frequency_hz": frequency,
                    "status": status,
                    "reference_input": self.reference_input,
                }
            time.sleep(0.1)
        raise RuntimeError(
            f"SR830 REF IN not locked: selected {self.reference_input}, "
            f"expected {expected:g} Hz"
            + (f" (previous {previous:g} Hz)" if previous is not None else "")
            + f", readback {frequency:g} Hz, LIAS={status}; "
            "please confirm that SMB100A LFO OUTPUT is connected to SR830 REF IN, "
            "and select the Sine/TTL edge matching the waveform"
        )

    def _auto_range_locked(
        self, r_value: float, status: int, allow_down: bool = True
    ) -> str:
        if not self.auto_range_enabled:
            return ""
        old = int(self.sensitivity_index)
        if not 0 <= old < len(NONLOCAL_SR830_FULL_SCALE_V):
            raise RuntimeError(f"SR830 current SENS index is invalid: {old}")
        utilization = abs(float(r_value)) / NONLOCAL_SR830_FULL_SCALE_V[old]
        requested = old
        reason = ""
        overloaded = bool(status & 0x07)
        floor = int(self.__dict__.get("overload_floor") or 0)
        if overloaded or utilization >= LOCKIN_UPRANGE_FRACTION:
            requested = min(old + 1, len(NONLOCAL_SR830_FULL_SCALE_V) - 1)
            reason = "overload/high"
            self.low_range_count = 0
        elif not allow_down:
            pass
        elif self.range_cooldown > 0:
            self.range_cooldown -= 1
            self.low_range_count = 0
        elif (
            utilization < LOCKIN_DOWNRANGE_FRACTION
            and old > floor
            and not status & 0x08
        ):
            self.low_range_count += 1
            if self.low_range_count >= 3:
                requested = old - 1
                reason = "low"
                self.low_range_count = 0
        else:
            self.low_range_count = 0
        if requested == old:
            return ""
        self.instrument.write(f"SENS {requested}")
        actual = int(round(float(self.instrument.query("SENS?").strip())))
        if actual != requested:
            raise RuntimeError(
                f"SR830 auto-range readback mismatch: requested {requested}, readback {actual}"
            )
        self.sensitivity_index = actual
        self.range_cooldown = 3
        self.lias_clear_pending = True
        if overloaded and actual > old and utilization < LOCKIN_UPRANGE_FRACTION:
            self.overload_floor = max(floor, actual)
        return f"SENS {old}->{actual} ({reason})"

    def new_pass(self) -> None:
        with self.lock:
            self.overload_floor = 0
            self.lias_clear_pending = True

    def auto_phase(self) -> float:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SR830 is not connected")
            self.instrument.write("APHS")
            time.sleep(0.25)
            return float(self.instrument.query("PHAS?").strip())

    def read_sample(
        self,
        expected_frequency_hz: float,
        auto_range: bool = True,
        allow_down: bool = True,
    ) -> dict[str, object]:
        with self.lock:
            if self.instrument is None:
                raise RuntimeError("SR830 is not connected")
            x, y, r_value, theta, frequency = self._numbers(
                self.instrument.query("SNAP? 1,2,3,4,9"), 5
            )
            if self.__dict__.pop("lias_clear_pending", False):
                self.instrument.query("LIAS?")
            lias = int(round(float(self.instrument.query("LIAS?").strip())))
            sensitivity_used = int(self.sensitivity_index)
            auto_range_action = (
                self._auto_range_locked(r_value, lias, allow_down)
                if auto_range
                else ""
            )
        reference_valid = not lias & 8 and math.isclose(
            frequency, float(expected_frequency_hz), rel_tol=0.005, abs_tol=0.1
        )
        return {
            "x_v": x,
            "y_v": y,
            "r_v": r_value,
            "phase_deg": theta,
            "reference_frequency_hz": frequency,
            "reference_valid": reference_valid,
            "status": lias,
            "sensitivity_index": sensitivity_used,
            "sensitivity_full_scale_v": NONLOCAL_SR830_FULL_SCALE_V[sensitivity_used],
            "auto_range": auto_range_action,
            "measurement_valid": bool(
                reference_valid and not (lias & 0x07) and not auto_range_action
            ),
        }


__all__ = [
    "LOCKIN_DOWNRANGE_FRACTION",
    "LOCKIN_DOWNRANGE_READINGS",
    "LOCKIN_UPRANGE_FRACTION",
    "NONLOCAL_LOCKIN_FILTER_ORDER",
    "NONLOCAL_LOCKIN_TIME_CONSTANT_S",
    "NONLOCAL_SR830_FULL_SCALE_V",
    "SR830Controller",
    "SR830_DISPLAY_XY_COMMANDS",
    "SR830_TIME_CONSTANTS_S",
    "STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT",
    "STFMR_LOCKIN_SR830_REFERENCE_INPUTS",
    "STFMR_LOCKIN_SR830_SENSITIVITY_CODE",
    "STFMR_LOCKIN_SR830_TIME_CONSTANTS",
    "STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE",
    "show_sr830_x_and_y",
]
