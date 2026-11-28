"""FROZEN LEGACY ARTIFACT (D1/M0 — scheduled for removal in Batch 2).

Formerly read by tools/extract_app.py to wire package imports during extraction.
Transport-PPMS (transport/ppms, transport/common, transport/core) is now the
sole authoritative source for PPMS; this file is not imported or executed.
"""
# Names the transport package already has: imported, not copied.
PACKAGE_IMPORTS = {
    "transport.core.scan_sequences": ("build_round_trip_values", "lakeshore_ramp_seconds", "ppms_scan_axis"),
    "transport.core.scan_recording": (
        "WINDOWS_RESERVED_FILE_NAMES", "ScanRunRecorder as _CoreScanRunRecorder",
        "make_scan_run_folder as _make_scan_run_folder", "scan_file_stem as _scan_file_stem",
        "scan_log_line as _scan_log_line"),
    "transport.core.scan_lifecycle": (
        "ScanCloseHandoff", "ZERO_SENT_NOT_READ_BACK", "SCAN_ZERO_OK_STATES", "zero_scan_outputs",
        "scan_end_outputs_text"),
    "transport.ppms.protocol": (
        "PPMS_FIELD_LIMIT_OE", "PPMS_FIELD_RATE_LIMIT_OE_S", "PPMS_TEMPERATURE_LOW_BOUNDARY_K",
        "PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN", "PPMS_TEMPERATURE_MAX_K", "PPMS_TEMPERATURE_MAX_RATE_K_MIN",
        "PPMS_TEMPERATURE_MIN_K", "ppms_scan_temperature_segments", "validate_ppms_field_command",
        "validate_ppms_temperature_command"),
    "transport.ppms.controllers.multivu": ("PPMSMultiVuReadbackController",),
    "transport.common.harmonics": (
        "DEFAULT_HARMONIC_CHOICES", "DEFAULT_HARMONIC_LABELS", "DEFAULT_HARMONIC_MODE_ORDERS",
        "DEFAULT_HARMONIC_ORDERS", "DEFAULT_SR830_HARMONIC_SETTING_KEYS", "parse_harmonic_mode",
        "schedule_harmonic_passes"),
    "transport.common.settling": (
        "calculate_lockin_settle_s", "format_lockin_settle_text", "format_settle_status_text"),
    "transport.common.validation": ("validate_transport_scan_settings",),
}

PPMS_HARMONIC_ORDERS = DEFAULT_HARMONIC_ORDERS
PPMS_HARMONIC_LABELS = dict(DEFAULT_HARMONIC_LABELS)
PPMS_ZURICH_MODE_ORDERS = dict(DEFAULT_HARMONIC_MODE_ORDERS)
PPMS_HARMONIC_CHOICES = DEFAULT_HARMONIC_CHOICES
PPMS_SR830_HARMONIC_SETTING_KEYS = dict(DEFAULT_SR830_HARMONIC_SETTING_KEYS)


def build_ppms_field_scan_sequence(start_oe: float, end_oe: float, step_oe: float, loops: int):
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
        raise ValueError(f"PPMS start/end field must be within ±{PPMS_FIELD_LIMIT_OE:g} Oe")
    cycle = build_round_trip_values(
        start_oe, end_oe, step_oe, decimals=9, max_total_points=max(1, 100_000 // loops),
        too_many_message="PPMS total scan points exceed 100000; please increase the step or reduce the loop count")
    if len(cycle) * loops > 100000:
        raise ValueError("PPMS total scan points exceed 100000; please increase the step or reduce the loop count")
    return [(loop_index, point_index, len(cycle), float(target))
            for loop_index in range(1, loops + 1) for point_index, target in enumerate(cycle, start=1)]


def ppms_lockin_settle_s(settle_s, time_constants_s) -> float:
    """The wait before a scan point is read: Settle s, or LOCKIN_SETTLE_TAUS
    time constants of the slowest lock-in measured if that is longer."""
    return calculate_lockin_settle_s(settle_s, time_constants_s, settle_taus=LOCKIN_SETTLE_TAUS)


def ppms_settle_status_text(seconds) -> str:
    """The wait before each reading, as a status line shows it."""
    return format_settle_status_text(seconds)


def ppms_lockin_settle_text(settle_s, effective_s, slowest_tc_s, meter) -> str:
    """How the wait before each reading was chosen, in words."""
    return format_lockin_settle_text(settle_s, effective_s, slowest_tc_s, meter, settle_taus=LOCKIN_SETTLE_TAUS)


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
        current_rms_ma, frequency_hz, compliance_v, harmonic_a, harmonic_b,
        active_lockins=active_lockins, allow_no_lockins=allow_no_lockins, harmonic_c=harmonic_c,
        max_abs_current_a=K6221_MAX_ABS_CURRENT_A, min_compliance_v=K6221_MIN_COMPLIANCE_V,
        max_compliance_v=K6221_MAX_COMPLIANCE_V, valid_sr830_keys=PPMS_SR830_KEYS)


def scan_file_stem(name: str) -> str:
    """The shared file-name rule, under the application's own name."""
    return _scan_file_stem(name)


def make_scan_run_folder(parent: Path, stem: str) -> Path:
    """The shared unique-folder allocator, under the application's own name."""
    return _make_scan_run_folder(parent, stem)


def scan_log_line(name: str, details: dict) -> str:
    """The shared log line with the application's clock (tests replace it)."""
    return _scan_log_line(name, details, datetime_class=datetime_module.datetime)


class ScanRunRecorder(_CoreScanRunRecorder):
    """The package's recorder bound to this application: its file name and
    version in the log, its own helpers (so replacing app.scan_file_stem,
    make_scan_run_folder or scan_log_line still takes effect), and the list of
    open runs the folder dialog reads."""

    @property
    def PROGRAM_NAME(self):
        return Path(__file__).name

    @property
    def APPLICATION_VERSION(self):
        return APP_VERSION

    def _validated_stem(self, stem: str) -> str:
        return scan_file_stem(stem)

    def _make_run_folder(self, parent: Path, stem: str) -> Path:
        return make_scan_run_folder(parent, stem)

    def _format_log(self, name: str, details: dict) -> str:
        return scan_log_line(name, details)

    def _opened(self) -> None:
        _OPEN_SCAN_RUNS.add(self)


class CombinedControlApp:
    def _ppms_harmonic_orders(self, key: str) -> tuple[int, ...]:
        """Harmonic orders this SR830 measures, one per scan pass."""
        variable = self._ppms_harmonic_var(key)
        value = str(variable.get()).strip() if variable is not None else "1"
        return parse_harmonic_mode(value, mode_orders=PPMS_HARMONIC_MODE_ORDERS)

    def _ppms_harmonic_passes(self, settings):
        """One settings dict per pass; a meter drops out once its orders end."""
        schedules = {key: self._ppms_harmonic_orders(key) for key in settings["active_lockins"]}

        def validate_pass(config):
            validate_ppms_transport_scan_settings(
                config["current_rms_ma"], config["frequency_hz"], config["compliance_v"],
                config["harmonic_a"], config["harmonic_b"], config["active_lockins"],
                allow_no_lockins=bool(config.get("allow_no_lockins", False)), harmonic_c=config["harmonic_c"])

        return schedule_harmonic_passes(settings, schedules, validator=validate_pass,
                                        harmonic_setting_keys=PPMS_SR830_HARMONIC_SETTING_KEYS)
