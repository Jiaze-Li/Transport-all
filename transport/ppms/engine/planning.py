"""Pure Tk-independent PPMS scan planning, macro parsing, settling, and validation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from transport.common.harmonics import (
    DEFAULT_HARMONIC_CHOICES,
    DEFAULT_HARMONIC_LABELS,
    DEFAULT_HARMONIC_MODE_ORDERS,
    DEFAULT_HARMONIC_ORDERS,
    DEFAULT_SR830_HARMONIC_SETTING_KEYS,
)
from transport.common.settling import (
    calculate_lockin_settle_s,
    format_lockin_settle_text,
    format_settle_status_text,
)
from transport.common.validation import validate_transport_scan_settings
from transport.core.scan_sequences import build_round_trip_values, ppms_scan_axis
from transport.ppms.protocol import (
    PPMS_FIELD_LIMIT_OE,
    PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN,
    validate_ppms_field_command,
    validate_ppms_temperature_command,
)

K6221_MAX_ABS_CURRENT_A = 0.105
K6221_MIN_COMPLIANCE_V = 0.1
K6221_MAX_COMPLIANCE_V = 105.0
LOCKIN_SETTLE_TAUS = 1.0
NONLOCAL_FSCAN_MAX_HZ = 100000

PPMS_HARMONIC_ORDERS = DEFAULT_HARMONIC_ORDERS
PPMS_HARMONIC_LABELS = dict(DEFAULT_HARMONIC_LABELS)
PPMS_ZURICH_MODE_ORDERS = dict(DEFAULT_HARMONIC_MODE_ORDERS)
PPMS_HARMONIC_MODE_ORDERS = PPMS_ZURICH_MODE_ORDERS
PPMS_HARMONIC_CHOICES = DEFAULT_HARMONIC_CHOICES

PPMS_SR830_KEYS = ("830_a", "830_b", "830_c")
PPMS_SR830_LABELS = {"830_a": "SR830-1", "830_b": "SR830-2", "830_c": "SR830-3"}
PPMS_SR830_HARMONIC_SETTING_KEYS = dict(DEFAULT_SR830_HARMONIC_SETTING_KEYS)

PPMS_FREQUENCY_SCAN_MODES = ("Frequency", "Frequency+Current")
PPMS_SCAN_MODES = (
    "Temperature",
    "Field",
    "Angle",
    "Current",
    "Field+Temperature",
    "Angle+Temperature",
    "Current+Temperature",
    "Angle+Field",
    *PPMS_FREQUENCY_SCAN_MODES,
)
PPMS_FIELD_COMMANDING_MODES = ("Field", "Field+Temperature", "Angle+Field")
PPMS_SCAN_PLOT_AXES = {
    "Temperature": "temperature",
    "Field": "field",
    "Field+Temperature": "field",
    "Angle": "angle",
    "Angle+Temperature": "angle",
    "Current": "current",
    "Current+Temperature": "current",
    "Angle+Field": "angle",
    "Frequency": "frequency",
    "Frequency+Current": "current",
}
PPMS_PLOT_AXIS_COLUMNS = {
    "field": "measured_field_oe",
    "temperature": "temperature_k",
    "angle": "measured_angle_deg",
    "current": "6221_current_rms_ma",
    "frequency": "6221_frequency_hz",
}


def nonlocal_prime_frequencies(start_hz, stop_hz) -> tuple[float, ...]:
    """Return prime frequencies in [start_hz, stop_hz] Hz for frequency scans."""
    try:
        start, stop = float(start_hz), float(stop_hz)
    except (TypeError, ValueError) as error:
        raise ValueError("f start and f stop must be whole numbers of Hz") from error
    if not all(math.isfinite(value) and value.is_integer() for value in (start, stop)):
        raise ValueError("f start and f stop must be whole numbers of Hz")
    start, stop = int(start), int(stop)
    if not 2 <= start <= stop <= NONLOCAL_FSCAN_MAX_HZ:
        raise ValueError(
            f"The frequency scan needs 2 ≤ f start ≤ f stop ≤ {NONLOCAL_FSCAN_MAX_HZ} Hz"
        )
    sieve = bytearray(b"\x01") * (stop + 1)
    sieve[:2] = b"\x00\x00"
    for number in range(2, math.isqrt(stop) + 1):
        if sieve[number]:
            sieve[number * number :: number] = bytes(
                len(range(number * number, stop + 1, number))
            )
    values = tuple(float(number) for number in range(start, stop + 1) if sieve[number])
    if not values:
        raise ValueError(f"No prime from {start} to {stop} Hz; widen the range")
    return values


def ppms_prime_frequencies(start_hz, end_hz) -> tuple[float, ...]:
    """Return prime frequencies in [start_hz, end_hz] Hz for PPMS frequency scans."""
    try:
        start, end = float(start_hz), float(end_hz)
    except (TypeError, ValueError):
        raise ValueError(
            "Frequency Start and End must be whole numbers of Hz"
        ) from None
    if not all(math.isfinite(value) and value.is_integer() for value in (start, end)):
        raise ValueError("Frequency Start and End must be whole numbers of Hz")
    if not 2 <= start <= end <= NONLOCAL_FSCAN_MAX_HZ:
        raise ValueError(
            f"A frequency scan needs 2 ≤ Start ≤ End ≤ {NONLOCAL_FSCAN_MAX_HZ} Hz (the 6221's sine)"
        )
    return nonlocal_prime_frequencies(start, end)


def ppms_frequency_count_text(frequencies) -> str:
    """Format a human-readable summary of prime scan frequencies."""
    values = tuple(frequencies)
    if len(values) == 1:
        return f"1 prime: {values[0]:g} Hz"
    return f"{len(values)} primes: {values[0]:g}–{values[-1]:g} Hz"


def ppms_frequency_scan_note(start_hz, end_hz) -> str:
    """Return either a prime-frequency count summary or validation error text."""
    try:
        return ppms_frequency_count_text(ppms_prime_frequencies(start_hz, end_hz))
    except ValueError as error:
        return str(error)


def parse_ppms_macro(text):
    """Parse and validate a PPMS macro script into (line, op, values) tuples."""
    commands = []
    targets = {}
    last_temp_k = None
    for line, raw in enumerate(text.splitlines(), 1):
        tokens = raw.strip().split()
        if not tokens or tokens[0].upper() in ("REM", "#") or raw.lstrip().startswith("#"):
            continue
        try:
            op = tokens.pop(0).upper()
            if op in ("TMP", "FLD", "WAI"):
                expected = {"TMP": "TEMP", "FLD": "FIELD", "WAI": "WAITFOR"}[op]
                if not tokens or tokens.pop(0).upper() != expected:
                    raise ValueError("Command format error")
                op = expected
            values = tuple(float(x) for x in tokens)
            if not all(math.isfinite(x) for x in values):
                raise ValueError("Parameters must be finite values")
            if op == "TEMP" and len(values) in (1, 2, 3):
                target = values[0]
                rate = values[1] if len(values) > 1 else None
                approach = values[2] if len(values) > 2 else 0.0
                validate_ppms_temperature_command(
                    target,
                    PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN if rate is None else rate,
                    last_temp_k,
                )
                if approach not in (0, 1):
                    raise ValueError("Temperature approach only supports 0/1")
                values = (target, rate, int(approach))
                targets["TEMP"] = target
                last_temp_k = target
            elif op == "FIELD" and len(values) in (2, 4):
                target, rate = validate_ppms_field_command(*values[:2])
                approach, mode = values[2:] if len(values) == 4 else (0, 1)
                if approach not in (0, 1, 2) or mode not in (0, 1):
                    raise ValueError(
                        "Field approach is 0/1/2, mode is 0(Persistent)/1(Driven)"
                    )
                values = (target, rate, int(approach), int(mode))
                targets["FIELD"] = target
                targets["FIELD_MODE"] = int(mode)
            elif op == "WAIT" and len(values) == 1:
                if not 0 <= values[0] <= 86400:
                    raise ValueError("Wait time range is 0–86400 s")
            elif op == "WAITFOR" and len(values) == 6:
                delay, temp, field, *unsupported = values
                if (
                    not 0 <= delay <= 86400
                    or temp not in (0, 1)
                    or field not in (0, 1)
                    or any(unsupported)
                ):
                    raise ValueError(
                        "WAITFOR supports: seconds temperature(0/1) field(0/1) 0 0 0"
                    )
                if (temp and "TEMP" not in targets) or (
                    field and "FIELD" not in targets
                ):
                    raise ValueError(
                        "Please specify the corresponding TEMP/FIELD target before waiting"
                    )
                values = (
                    delay,
                    targets.get("TEMP") if temp else None,
                    targets.get("FIELD") if field else None,
                    targets.get("FIELD_MODE") if field else None,
                )
            elif op == "SCAN" and not values:
                last_temp_k = None
            else:
                raise ValueError(
                    "Unsupported command or parameter count; VSMDF/VSMMH/VSMMT cannot be used for transport measurements"
                )
            commands.append((line, op, values))
        except ValueError as error:
            raise ValueError(f"Line {line}: {error}") from None
    if not commands:
        raise ValueError("No runnable commands")
    return commands


def build_ppms_field_scan_sequence(
    start_oe: float, end_oe: float, step_oe: float, loops: int
):
    """Build PPMS start→end→start field targets for each loop."""
    start_oe = float(start_oe)
    end_oe = float(end_oe)
    step_oe = abs(float(step_oe))
    loops = int(loops)
    if not all(math.isfinite(value) for value in (start_oe, end_oe, step_oe)):
        raise ValueError("PPMS start field, end field and step must be finite values")
    if step_oe <= 0:
        raise ValueError("PPMS field step must be greater than 0 Oe")
    if not 1 <= loops <= 1000:
        raise ValueError("PPMS scan loop count must be between 1 and 1000")
    if abs(start_oe) > PPMS_FIELD_LIMIT_OE or abs(end_oe) > PPMS_FIELD_LIMIT_OE:
        raise ValueError(
            f"PPMS start/end field must be within ±{PPMS_FIELD_LIMIT_OE:g} Oe"
        )
    cycle = build_round_trip_values(
        start_oe,
        end_oe,
        step_oe,
        decimals=9,
        max_total_points=max(1, 100_000 // loops),
        too_many_message="PPMS total scan points exceed 100000; please increase the step or reduce the loop count",
    )
    if len(cycle) * loops > 100000:
        raise ValueError(
            "PPMS total scan points exceed 100000; please increase the step or reduce the loop count"
        )
    return [
        (loop_index, point_index, len(cycle), float(target))
        for loop_index in range(1, loops + 1)
        for point_index, target in enumerate(cycle, start=1)
    ]


def ppms_lockin_settle_s(settle_s, time_constants_s) -> float:
    """The wait before a scan point is read: Settle s, or LOCKIN_SETTLE_TAUS
    time constants of the slowest lock-in measured if that is longer.
    """
    return calculate_lockin_settle_s(
        settle_s, time_constants_s, settle_taus=LOCKIN_SETTLE_TAUS
    )


def ppms_settle_status_text(seconds) -> str:
    """The wait before each reading, as a status line shows it."""
    return format_settle_status_text(seconds)


def ppms_lockin_settle_text(settle_s, effective_s, slowest_tc_s, meter) -> str:
    """How the wait before each reading was chosen, in words."""
    return format_lockin_settle_text(
        settle_s, effective_s, slowest_tc_s, meter, settle_taus=LOCKIN_SETTLE_TAUS
    )


def validate_ppms_transport_scan_settings(
    current_rms_ma: float,
    frequency_hz: float,
    compliance_v: float,
    harmonic_a: int,
    harmonic_b: int,
    active_lockins: tuple[str, ...] = ("830_a", "830_b"),
    allow_no_lockins: bool = False,
    harmonic_c: int = 1,
) -> dict[str, object]:
    """Validate 6221/SR830 settings used by the PPMS field scan."""
    return validate_transport_scan_settings(
        current_rms_ma,
        frequency_hz,
        compliance_v,
        harmonic_a,
        harmonic_b,
        active_lockins=active_lockins,
        allow_no_lockins=allow_no_lockins,
        harmonic_c=harmonic_c,
        max_abs_current_a=K6221_MAX_ABS_CURRENT_A,
        min_compliance_v=K6221_MIN_COMPLIANCE_V,
        max_compliance_v=K6221_MAX_COMPLIANCE_V,
        valid_sr830_keys=PPMS_SR830_KEYS,
    )


@dataclass(frozen=True)
class PPMSScanAxisPlan:
    """Pure axis and loop plan for any of the 10 PPMS scan modes."""

    mode: str
    values: tuple[float, ...]
    frequencies: tuple[float, ...] | None
    outer_fields: tuple[float, ...] | None
    temperatures: tuple[float, ...] | None
    current_sweep: bool
    scan_field: bool
    field_outer: bool
    field_commanded: bool


def plan_ppms_scan_axes(
    mode: str,
    simple_range: tuple[float, ...],
    *,
    outer_field_range: tuple[float, float, float] | None = None,
    temperature_range: tuple[float, float, float] | None = None,
    frequency_range: tuple[float, float] | None = None,
) -> PPMSScanAxisPlan:
    """Compute scan axes and mode flags without touching Tk variables or hardware."""
    if mode not in PPMS_SCAN_MODES:
        raise ValueError("Please select a valid scan mode")
    frequencies = None
    if mode == "Frequency":
        frequencies = ppms_prime_frequencies(*simple_range[:2])
        values = frequencies
    else:
        values = ppms_scan_axis(*simple_range)
    if mode == "Frequency+Current":
        if frequency_range is None:
            raise ValueError("Frequency+Current requires outer frequency range")
        frequencies = ppms_prime_frequencies(*frequency_range)
    current_sweep = mode in ("Current", "Current+Temperature", "Frequency+Current")
    scan_field = mode in ("Field", "Field+Temperature")
    field_outer = mode == "Angle+Field"
    field_commanded = mode in PPMS_FIELD_COMMANDING_MODES
    outer_fields = (
        ppms_scan_axis(*outer_field_range)
        if field_outer and outer_field_range is not None
        else None
    )
    if mode in ("Field+Temperature", "Angle+Temperature", "Current+Temperature"):
        if temperature_range is None:
            raise ValueError(f"{mode} requires outer temperature range")
        temperatures = ppms_scan_axis(*temperature_range)
    elif mode == "Temperature":
        temperatures = values
    else:
        temperatures = None
    return PPMSScanAxisPlan(
        mode=mode,
        values=values,
        frequencies=frequencies,
        outer_fields=outer_fields,
        temperatures=temperatures,
        current_sweep=current_sweep,
        scan_field=scan_field,
        field_outer=field_outer,
        field_commanded=field_commanded,
    )


__all__ = [
    "K6221_MAX_ABS_CURRENT_A",
    "K6221_MAX_COMPLIANCE_V",
    "K6221_MIN_COMPLIANCE_V",
    "LOCKIN_SETTLE_TAUS",
    "NONLOCAL_FSCAN_MAX_HZ",
    "PPMSScanAxisPlan",
    "PPMS_FIELD_COMMANDING_MODES",
    "PPMS_FREQUENCY_SCAN_MODES",
    "PPMS_HARMONIC_CHOICES",
    "PPMS_HARMONIC_LABELS",
    "PPMS_HARMONIC_MODE_ORDERS",
    "PPMS_HARMONIC_ORDERS",
    "PPMS_PLOT_AXIS_COLUMNS",
    "PPMS_SCAN_MODES",
    "PPMS_SCAN_PLOT_AXES",
    "PPMS_SR830_HARMONIC_SETTING_KEYS",
    "PPMS_SR830_KEYS",
    "PPMS_SR830_LABELS",
    "PPMS_ZURICH_MODE_ORDERS",
    "build_ppms_field_scan_sequence",
    "nonlocal_prime_frequencies",
    "parse_ppms_macro",
    "plan_ppms_scan_axes",
    "ppms_frequency_count_text",
    "ppms_frequency_scan_note",
    "ppms_lockin_settle_s",
    "ppms_lockin_settle_text",
    "ppms_prime_frequencies",
    "ppms_settle_status_text",
    "validate_ppms_transport_scan_settings",
]
