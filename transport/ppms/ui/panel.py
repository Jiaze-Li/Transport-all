"""PPMS Tk panel view, plot rendering, polling timers, and UI event sinks."""

from __future__ import annotations

import csv
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from transport.core.scan_sequences import ppms_scan_axis
from transport.ppms.controllers.environment import (
    PPMS_DEFAULT_BACKEND,
    PPMS_DEFAULT_HOST,
    PPMS_DEFAULT_PORT,
    PPMS_HOST_CHOICES,
    PPMS_MULTIPYVU_BACKEND,
    PPMS_MULTIPYVU_DEFAULT_PORT,
    PPMS_QDINSTRUMENT_BACKEND,
    PPMS_QDINSTRUMENT_DEFAULT_PORT,
)
from transport.ppms.controllers.multivu import PPMSMultiVuReadbackController
from transport.ppms.controllers.qdinstrument import (
    PPMSQDInstrumentReadbackController,
    PPMS_FIELD_RELATIVE_TOLERANCE,
    PPMS_FIELD_TOLERANCE_OE,
)
from transport.ppms.controllers.sr830 import (
    SR830_DISPLAY_XY_COMMANDS,
    SR830_TIME_CONSTANTS_S,
    STFMR_LOCKIN_SR830_TIME_CONSTANTS,
)
from transport.ppms.controllers.zurich import (
    NONLOCAL_MFLI_REFERENCE_INPUTS,
    NONLOCAL_SAMPLE_HZ,
    short_status_error,
    zurich_mfli_choice,
    zurich_mfli_label,
)
from transport.ppms.engine.errors import PPMSScanCancelled
from transport.ppms.engine.macro import MacroRunner
from transport.ppms.engine.motor import PPMSMotorDebug, PPMSMotorService
from transport.ppms.engine.planning import (
    LOCKIN_SETTLE_TAUS,
    PPMS_HARMONIC_CHOICES,
    PPMS_HARMONIC_LABELS,
    PPMS_HARMONIC_MODE_ORDERS,
    PPMS_HARMONIC_ORDERS,
    PPMS_SCAN_MODES,
    PPMS_SCAN_PLOT_AXES,
    PPMS_SR830_HARMONIC_SETTING_KEYS,
    PPMS_SR830_KEYS,
    PPMS_SR830_LABELS,
    PPMS_ZURICH_MODE_ORDERS,
    plan_ppms_scan_axes,
    ppms_frequency_count_text,
    ppms_frequency_scan_note,
    ppms_lockin_settle_text,
    ppms_settle_status_text,
    validate_ppms_transport_scan_settings,
)
from transport.ppms.engine.runner import (
    NONLOCAL_FSCAN_SETTLE_PERIODS,
    NONLOCAL_FSCAN_START_HZ,
    NONLOCAL_FSCAN_STOP_HZ,
    PPMS_ACQUISITION_S,
    PPMS_FIELD_RATE_DEFAULT_OE_S,
    PPMS_PERSISTENT_CONFIRM_S,
    PPMS_PERSISTENT_MARGIN_S,
    PPMS_ROTATOR_MOVE_ALLOWANCE_S,
    PPMS_SET_UP_ALLOWANCE_S,
    PPMS_SR830_DEFAULT_TIME_CONSTANT,
    PPMS_TEMPERATURE_ACQUISITION_S,
    PPMS_TEMPERATURE_DELAY_S,
    PPMS_TRANSPORT_COMPLIANCE_V,
    PPMS_TRANSPORT_FREQUENCY_HZ,
    PPMS_ZURICH_TIME_CONSTANT_S,
    PPMSScanRunner,
    ScanRequest,
    format_scan_duration,
    format_sr830_full_scale,
    format_voltage_range,
    ppms_field_is_ready,
    ppms_sr830_address,
)
from transport.ppms.protocol import (
    PPMS_TEMPERATURE_LOW_BOUNDARY_K,
    PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN,
    ppms_scan_temperature_segments,
    validate_ppms_field_command,
    validate_ppms_temperature_command,
)
from transport.ppms.state import PPMS_DEFAULT_RESOURCES, PPMSAppState
from transport.ppms.ui.dialogs import (
    ask_scan_start,
    begin_scan_run,
    create_zurich_chooser,
    show_trimmed,
    widget_idle_state,
)
from transport.ppms.ui.theme import (
    PAIR_FLIP_LIMIT,
    PPMS_METER_COLORS,
    PPMS_PLOT_ROW_MIN_HEIGHT_PX,
    THEME_COLORS,
    _PROGRAM_FILE,
    draw_nonlocal_series,
    save_scan_plot_snapshots,
    shade_color,
)
from transport.common.harmonics import parse_harmonic_mode, schedule_harmonic_passes

PPMS_HELIUM_HISTORY_FILENAME = "PPMS_He_history.csv"
PPMS_SR830_RESOURCE_OPTIONS = tuple(
    f"GPIB0::{address}::INSTR" for address in (8, 9, 10)
)
PPMS_SR830_HARMONIC_ATTRS = {
    "830_a": "ppms_transport_harmonic_a_var",
    "830_b": "ppms_transport_harmonic_b_var",
    "830_c": "ppms_transport_harmonic_c_var",
}


class PPMSPanelView:
    """Tkinter view for the PPMS tab, composed with AppShell and PPMSAppState."""

    ppms_page_name = "PPMS"

    def __init__(self, shell, tab: ttk.Frame, state: PPMSAppState) -> None:
        self.shell = shell
        self.root = shell.root
        self.ppms_tab = tab
        self.state = state

        self.runner = PPMSScanRunner(
            state, post_ui=shell.post_ui, read_live_fn=self._read_ppms_live
        )
        self.motor_service = PPMSMotorService(state.ppms_environment_controller)
        self.macro_runner = MacroRunner(
            environment_controller=state.ppms_environment_controller,
            executor=state.ppms_executor,
            macro_stop_event=state.macro_stop_event,
            is_closed=lambda: self.shell.closing or self.state.closed,
            field_ready_fn=ppms_field_is_ready,
            stop_scan_fn=self.stop_ppms_field_scan,
            post_ui=shell.post_ui,
        )

        self.ppms_poll_inflight = False
        self.ppms_poll_after_id = None
        self.zurich_after_id = None
        self.ppms_helium_poll_inflight = False
        self.ppms_helium_poll_due = 0.0
        self.ppms_helium_warning_active = False
        self.ppms_helium_history: list[tuple[float, float]] = []
        self.ppms_helium_csv_path: Path | None = None
        self.ppms_helium_csv_started = 0.0
        self.ppms_helium_csv_error = False
        self.ppms_rows_tight = False
        self.ppms_plot_redraw_queued = False
        self.ppms_plot_axis = "field"
        self.ppms_plot_addresses: dict[str, int] = {}
        self.ppms_plot_angle_range: tuple[float, float] | None = None
        self.ppms_selection_order: list[str] = []
        self.ppms_acquisition_default_shown: str | None = f"{PPMS_ACQUISITION_S:g}"
        self.ppms_default_field_range = ("-15000", "15000", "1500")

        self.ppms_macro_window = None
        self.ppms_macro_editor = None
        self.ppms_macro_status = None
        self.ppms_macro_edit_buttons: list[ttk.Button] = []
        self._macro_scan_pending = False
        self.ppms_macro_text = (
            "REM Example: run one transport scan with the main-window settings\n"
            "SCAN\n"
            "REM Zero the field and enter Persistent after the scan\n"
            "FIELD 0 100 0 0\n"
            "WAI WAITFOR 1 0 1 0 0 0\n"
        )

        self._init_tk_vars()
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        self._build_ui()
        self._build_zurich_ui()
        self._refresh_ppms2_plot_layout()
        self._redraw_ppms_transport_plot()
        self.ppms_poll_after_id = self.root.after(1000, self._ppms_poll_timer)
        self.zurich_after_id = self.root.after(1000, self._poll_ppms2_zurich)

    def _init_tk_vars(self) -> None:
        self.ppms_sr830_range_vars: dict[str, tk.StringVar] = {}
        self.ppms_sr830_status_vars: dict[str, tk.StringVar] = {}
        self.ppms_backend_var = tk.StringVar(value=PPMS_DEFAULT_BACKEND)
        self.ppms_host_var = tk.StringVar(value=PPMS_DEFAULT_HOST)
        self.ppms_port_var = tk.IntVar(value=PPMS_DEFAULT_PORT)
        default_dll = next(
            (
                str(p)
                for p in PPMSQDInstrumentReadbackController.candidate_dll_paths()
                if p.is_file()
            ),
            "",
        )
        self.ppms_qdinstrument_dll_var = tk.StringVar(value=default_dll)
        self.ppms_connection_var = tk.StringVar(value="Not connected")
        self.ppms_temperature_var = tk.StringVar(value="-- K")
        self.ppms_temperature_status_var = tk.StringVar(value="Status: --")
        self.ppms_field_var = tk.StringVar(value="-- Oe")
        self.ppms_field_status_var = tk.StringVar(value="Status: --")
        self.ppms_control_safety_var = tk.BooleanVar(value=False)
        self.ppms_motor_confirm_var = self.ppms_control_safety_var
        self.ppms_motor_target_var = tk.StringVar(value="0")
        self.ppms_motor_speed_var = tk.StringVar(value="14")
        self.ppms_motor_status_var = tk.StringVar(
            value="Not read yet; QDInstrument debug interface only"
        )
        self.ppms_angle_live_var = tk.StringVar(value="-- °")
        self.ppms_xy_vars = {
            k: tk.StringVar(value=f"SR830-{i}: reference status not read")
            for i, k in enumerate(PPMS_SR830_KEYS, 1)
        }
        self.ppms_nested_vars = {
            "angle": [
                tk.BooleanVar(value=False),
                tk.StringVar(value="26"),
                tk.StringVar(value="26"),
                tk.StringVar(value="1"),
            ],
            "temperature": [
                tk.BooleanVar(value=False),
                tk.StringVar(value="300"),
                tk.StringVar(value="300"),
                tk.StringVar(value="10"),
            ],
        }
        self.ppms_temperature_target_var = tk.StringVar(value="300")
        self.ppms_temperature_rate_var = tk.StringVar(value="10")
        self.ppms_temperature_target_var.trace_add(
            "write", self._ppms_adjust_low_temperature_rate
        )
        self.ppms_temperature_approach_var = tk.StringVar(value="No overshoot")
        self.ppms_field_target_var = tk.StringVar(value="0")
        self.ppms_field_rate_var = tk.StringVar(
            value=f"{PPMS_FIELD_RATE_DEFAULT_OE_S:g}"
        )
        self.ppms_field_approach_var = tk.StringVar(value="No overshoot")
        self.ppms_field_mode_var = tk.StringVar(value="Persistent")
        self.ppms_field_step_var = tk.StringVar(value="1000")
        self.ppms_field_scan_start_var = tk.StringVar(value="0")
        self.ppms_field_scan_end_var = tk.StringVar(value="0")
        self.ppms_field_scan_step_var = tk.StringVar(value="1000")
        self.ppms_field_scan_loop_var = tk.IntVar(value=1)
        self.ppms_field_scan_stable_var = tk.StringVar(value="0.5")
        self.ppms_acquisition_time_var = tk.StringVar(value=f"{PPMS_ACQUISITION_S:g}")
        self.ppms_field_scan_eta_var = tk.StringVar(value="Remaining: --")
        self.ppms_field_scan_status_var = tk.StringVar(value="Scan: not started")
        self.ppms_transport_current_var = tk.StringVar(value="1")
        self.ppms_transport_frequency_var = tk.StringVar(
            value=f"{PPMS_TRANSPORT_FREQUENCY_HZ:g}"
        )
        self.ppms_transport_compliance_var = tk.StringVar(
            value=f"{PPMS_TRANSPORT_COMPLIANCE_V:g}"
        )
        self.ppms_transport_harmonic_a_var = tk.StringVar(value="1")
        self.ppms_transport_harmonic_b_var = tk.StringVar(value="2")
        self.ppms_transport_harmonic_c_var = tk.StringVar(value="3")
        self.ppms_transport_status_var = tk.StringVar(
            value="Transport acquisition: waiting for scan; 6221 output OFF"
        )
        self.ppms_resource_vars = {
            key: tk.StringVar(value=value)
            for key, value in PPMS_DEFAULT_RESOURCES.items()
        }
        self.ppms_device_connection_vars = {
            key: tk.StringVar(value="Not connected") for key in PPMS_DEFAULT_RESOURCES
        }
        self.ppms_device_connect_buttons: dict[str, ttk.Button] = {}
        self.ppms_device_disconnect_buttons: dict[str, ttk.Button] = {}
        self.ppms_device_resource_widgets: dict[str, tk.Widget] = {}
        self.ppms_lockin_selection = {
            key: tk.BooleanVar(value=True) for key in PPMS_SR830_KEYS
        }
        self.ppms_lockin_selection["zurich"] = tk.BooleanVar(value=True)
        self.zurich_resource = tk.StringVar(value=zurich_mfli_label("MF-DEV4414"))
        self.zurich_reference = tk.StringVar(value="Trigger In 1")
        self.zurich_harmonic = tk.StringVar(value="1+2+3")
        self.zurich_timeconstant = tk.StringVar(
            value=f"{PPMS_ZURICH_TIME_CONSTANT_S:g}"
        )
        self.zurich_status = tk.StringVar(value="Not connected")
        self.zurich_values = tk.StringVar(
            value="X: -- V    Y: -- V    R: -- V    θ: -- °    Ref: -- Hz"
        )
        self.zurich_range_var = tk.StringVar(value="Autorange ON")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.ppms_tab, padding=self.shell._ui_px(8, 5))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1, minsize=220)
        content = ttk.Frame(outer)
        content.grid(row=0, column=0, sticky="ew")
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=1)

        environment = ttk.LabelFrame(
            content, text="PPMS MultiVu / EverCool II", padding=2
        )
        environment.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        environment.columnconfigure(10, weight=1)
        ttk.Label(environment, text="Interface:").grid(row=0, column=0, sticky="w")
        self.ppms_backend_combo = ttk.Combobox(
            environment,
            textvariable=self.ppms_backend_var,
            values=(PPMS_QDINSTRUMENT_BACKEND, PPMS_MULTIPYVU_BACKEND),
            state="readonly",
            width=20,
        )
        self.ppms_backend_combo.grid(row=0, column=1, padx=(0, 8), sticky="w")
        self.ppms_backend_combo.bind(
            "<<ComboboxSelected>>", self._on_ppms_backend_changed, add="+"
        )
        self.ppms_host_label = ttk.Label(environment, text="PPMS IP:")
        self.ppms_host_label.grid(row=0, column=2, sticky="w")
        self.ppms_host_entry = ttk.Combobox(
            environment,
            textvariable=self.ppms_host_var,
            values=PPMS_HOST_CHOICES,
            width=16,
        )
        self.ppms_host_entry.grid(row=0, column=3, padx=(0, 8), sticky="w")
        ttk.Label(environment, text="Port:").grid(row=0, column=4, sticky="w")
        self.ppms_port_entry = ttk.Spinbox(
            environment, from_=1, to=65535, textvariable=self.ppms_port_var, width=7
        )
        self.ppms_port_entry.grid(row=0, column=5, padx=(0, 8), sticky="w")
        self.ppms_connect_button = ttk.Button(
            environment,
            text="Connect PPMS",
            style="Strong.TButton",
            command=self.connect_ppms_environment,
        )
        self.ppms_connect_button.grid(row=0, column=6, padx=2)
        self.ppms_disconnect_button = ttk.Button(
            environment,
            text="Disconnect",
            command=self.disconnect_ppms_environment,
            state="disabled",
        )
        self.ppms_disconnect_button.grid(row=0, column=7, padx=2)
        self.ppms_read_button = ttk.Button(
            environment,
            text="Read now",
            command=self.read_ppms_environment_once,
            state="disabled",
        )
        self.ppms_read_button.grid(row=0, column=8, padx=(8, 2))
        self.ppms_environment_lamp = self.shell._create_connection_indicator(
            environment, "ppms_environment"
        )
        self.ppms_environment_lamp.grid(row=0, column=9, padx=(8, 1))
        self.ppms_connection_shown_var = tk.StringVar(
            value=self.ppms_connection_var.get()
        )

        def shown(*_args) -> None:
            text = self.ppms_connection_var.get()
            self.ppms_connection_shown_var.set(
                {"Not connected": "Offline", "Connected": "Online"}.get(text, text)
                if self.ppms_rows_tight
                else text
            )

        self.ppms_connection_var.trace_add("write", shown)
        self.ppms_connection_shown = shown
        status = ttk.Label(environment, width=1)
        status.grid(row=0, column=10, sticky="ew")
        show_trimmed(status, self.ppms_connection_shown_var)

        metrics_outer = ttk.LabelFrame(content, text="Live readings", padding=3)
        self.ppms_metrics_frame = metrics_outer
        metrics_outer.grid_propagate(False)
        metrics_outer.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 2))
        metrics_outer.rowconfigure(0, weight=1)
        metrics_outer.columnconfigure(0, weight=1)
        metrics_view = tk.Canvas(
            metrics_outer,
            highlightthickness=0,
            height=100,
            background=THEME_COLORS["surface"],
        )
        metrics_view.grid(row=0, column=0, sticky="nsew")
        metrics_scroll = ttk.Scrollbar(
            metrics_outer, orient="vertical", command=metrics_view.yview
        )
        metrics_scroll.grid(row=0, column=1, sticky="ns")
        metrics_view.configure(yscrollcommand=metrics_scroll.set)
        metrics = ttk.Frame(metrics_view)
        metrics_window = metrics_view.create_window(0, 0, window=metrics, anchor="nw")
        metrics.bind(
            "<Configure>",
            lambda event: metrics_view.configure(
                scrollregion=metrics_view.bbox("all")
            ),
        )
        metrics_view.bind(
            "<Configure>",
            lambda event: metrics_view.itemconfigure(metrics_window, width=event.width),
        )

        def scroll_metrics(event):
            if metrics.winfo_reqheight() <= metrics_view.winfo_height():
                return "break"
            delta = getattr(event, "delta", 0)
            number = getattr(event, "num", None)
            units = (
                (-1 if number == 4 else 1)
                if number in (4, 5)
                else (
                    -max(1, int(abs(delta) / 120))
                    if delta > 0
                    else max(1, int(abs(delta) / 120))
                )
            )
            if delta or number in (4, 5):
                metrics_view.yview_scroll(units, "units")
            return "break"

        metrics.columnconfigure(0, weight=1, uniform="ppms_metric")
        metrics.columnconfigure(1, weight=1, uniform="ppms_metric")

        def fit_metric_value(metric_frame, metric_title, metric_value):
            metric_frame.bind(
                "<Configure>",
                lambda event: metric_value.configure(
                    wraplength=max(
                        100, event.width - metric_title.winfo_reqwidth() - 8
                    )
                ),
                add="+",
            )

        self.ppms_current_live_var = tk.StringVar(value="Unknown / not read")
        current_metric = ttk.Frame(metrics, padding=2)
        current_metric.grid(row=0, column=1, sticky="ew")
        current_title = ttk.Label(current_metric, text="6221 current:")
        current_title.pack(side="left")
        current_value = ttk.Label(
            current_metric, textvariable=self.ppms_current_live_var, wraplength=230
        )
        current_value.pack(side="left")
        fit_metric_value(current_metric, current_title, current_value)

        angle_metric = ttk.Frame(metrics, padding=2)
        ttk.Label(angle_metric, text="Angle:").pack(side="left")
        angle_metric.grid(row=2, column=0, sticky="ew")
        self.ppms_pressure_live_var = tk.StringVar(value="Unknown / not read")
        self.ppms_helium_live_var = tk.StringVar(value="Unknown / not read")
        self.ppms_helium_change_var = tk.StringVar(value="Waiting for a valid reading")
        for row, title, var in (
            (1, "Pressure:", self.ppms_pressure_live_var),
            (2, "He level:", self.ppms_helium_live_var),
        ):
            frame = ttk.Frame(metrics, padding=2)
            frame.grid(row=row, column=1, sticky="ew")
            metric_title = ttk.Label(frame, text=title)
            metric_title.pack(side="left")
            metric_value = ttk.Label(frame, textvariable=var, wraplength=230)
            metric_value.pack(side="left")
            fit_metric_value(frame, metric_title, metric_value)
        self.ppms_helium_canvas = tk.Canvas(
            metrics,
            height=120,
            width=320,
            background="white",
            highlightthickness=0,
        )
        self.ppms_helium_canvas.grid(
            row=3, column=1, sticky="nsew", padx=2, pady=(2, 0)
        )
        self.ppms_helium_canvas.bind(
            "<Configure>", lambda event: self._draw_ppms_helium_history()
        )
        ttk.Label(
            angle_metric,
            textvariable=self.ppms_angle_live_var,
            style="LargeMetric.TLabel",
        ).pack(side="left")

        temperature = ttk.Frame(metrics, padding=2)
        ttk.Label(temperature, text="Temperature:").pack(side="left")
        temperature.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Label(
            temperature,
            textvariable=self.ppms_temperature_var,
            style="LargeMetric.TLabel",
        ).pack(side="left")
        ttk.Label(
            temperature,
            textvariable=self.ppms_temperature_status_var,
            style="Muted.TLabel",
        ).pack(side="bottom", anchor="w")

        field = ttk.Frame(metrics, padding=2)
        ttk.Label(field, text="Field:").pack(side="left")
        field.grid(row=1, column=0, sticky="ew")
        ttk.Label(
            field, textvariable=self.ppms_field_var, style="LargeMetric.TLabel"
        ).pack(side="left")
        ttk.Label(
            field, textvariable=self.ppms_field_status_var, style="Muted.TLabel"
        ).pack(side="bottom", anchor="w")

        def bind_metrics_wheel(widget):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, scroll_metrics, add="+")
            for child in widget.winfo_children():
                bind_metrics_wheel(child)

        bind_metrics_wheel(metrics_view)

        control = ttk.LabelFrame(content, text="PPMS settings", padding=3)
        control.grid(row=3, column=0, sticky="nsew", pady=(0, 2))
        self.ppms_control_widgets: list[tk.Widget] = []

        scan = ttk.LabelFrame(content, text="PPMS scan", padding=7)
        scan.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        self.ppms_field_scan_start_button = ttk.Button(
            scan, text="Start scan", command=self.start_ppms_field_scan
        )
        self.ppms_field_scan_stop_button = ttk.Button(
            scan, text="Stop", command=self.stop_ppms_field_scan, state="disabled"
        )
        self.ppms_field_scan_save_button = ttk.Button(
            scan,
            text="Save CSV",
            command=self.save_ppms_field_scan_csv,
            state="disabled",
        )
        self.ppms_field_scan_progress = ttk.Progressbar(
            scan, mode="determinate", maximum=1
        )
        self.ppms_control_widgets.append(self.ppms_field_scan_start_button)
        self._on_ppms_backend_changed()

        instruments = ttk.LabelFrame(
            content, text="PPMS external instruments (independent connections)", padding=8
        )
        instruments.grid(row=1, column=0, sticky="ew", pady=(0, 2))

        def sync_metrics_height(_event=None):
            self.ppms_metrics_frame.configure(height=instruments.winfo_reqheight())

        instruments.bind("<Configure>", sync_metrics_height, add="+")
        instruments.after_idle(sync_metrics_height)
        instruments.columnconfigure(0, weight=1)
        instruments.columnconfigure(1, weight=1)

        labels = {
            "6221": "Keithley 6221",
            "2400": "Keithley 2400",
            **PPMS_SR830_LABELS,
        }
        self.ppms_instrument_cells = {}
        for key, cell_row, cell_column in (
            ("6221", 0, 0),
            ("830_a", 0, 1),
            ("2400", 1, 0),
            ("830_b", 1, 1),
            ("830_c", 2, 1),
        ):
            cell = ttk.Frame(instruments)
            cell.grid(row=cell_row, column=cell_column, sticky="ew", padx=2)
            name = ttk.Label(cell, text=f"{labels[key]}:")
            name.grid(row=0, column=0, sticky="e", pady=2)
            if key in PPMS_SR830_KEYS:
                entry = ttk.Combobox(
                    cell,
                    textvariable=self.ppms_resource_vars[key],
                    values=PPMS_SR830_RESOURCE_OPTIONS,
                    state="readonly",
                    width=16,
                )
            else:
                entry = ttk.Entry(
                    cell, textvariable=self.ppms_resource_vars[key], width=18
                )
            entry.grid(row=0, column=1, sticky="w", padx=(0, 3), pady=2)
            self.ppms_device_resource_widgets[key] = entry
            connect_button = ttk.Button(
                cell,
                text="Connect",
                command=lambda selected=key: self.connect_ppms_device(selected),
                width=8,
            )
            connect_button.grid(row=0, column=3, padx=2, pady=2)
            disconnect_button = ttk.Button(
                cell,
                text="Disconnect",
                state="disabled",
                command=lambda selected=key: self.disconnect_ppms_device(selected),
                width=10,
            )
            disconnect_button.grid(row=0, column=4, padx=2, pady=2)
            self.ppms_device_connect_buttons[key] = connect_button
            self.ppms_device_disconnect_buttons[key] = disconnect_button
            lamp = self.shell._create_connection_indicator(cell, f"ppms_{key}")
            lamp.grid(row=0, column=5, padx=(8, 1), pady=2)
            shown_var = (
                self._ppms_compact_status(self.ppms_xy_vars[key])
                if key in PPMS_SR830_KEYS
                else self.ppms_device_connection_vars[key]
            )
            status_lbl = ttk.Label(cell, textvariable=shown_var, width=13)
            status_lbl.grid(row=0, column=6, sticky="w", padx=(1, 8), pady=2)
            self.ppms_instrument_cells[key] = (
                name,
                f"{labels[key]}:",
                entry,
                lamp,
                status_lbl,
                shown_var,
            )

        macro_row = ttk.Frame(instruments)
        macro_row.grid(row=2, column=0, columnspan=2, sticky="e")
        self.ppms_macro_row = macro_row
        ttk.Button(macro_row, text="Macro", command=self.open_ppms_macro).grid(
            row=0, column=0
        )
        self._build_ppms_sr830_tc_choice(macro_row)

        transport_plot = ttk.LabelFrame(
            outer,
            text="SR830-1 / SR830-2 / SR830-3 / Zurich: X, Y, θ vs scan variable",
            padding=6,
        )
        transport_plot.grid(row=3, column=0, sticky="nsew")
        transport_plot.rowconfigure(0, weight=1)
        transport_plot.columnconfigure(0, weight=1)

        self._build_compact_controls(control, scan, instruments, transport_plot)

        self.ppms_transport_canvases = {}
        self.ppms_transport_plot_frames = {}
        for row, key in enumerate(PPMS_SR830_KEYS):
            transport_plot.rowconfigure(row, weight=1, uniform="ppms_plot_rows")
            for col, channel in enumerate(("x", "y", "theta")):
                transport_plot.columnconfigure(col, weight=1, uniform="ppms_plots")
                label = "θ" if channel == "theta" else channel.upper()
                harmonic = self._ppms_harmonic_var(key).get()
                order_label = {
                    1: "1st harmonic",
                    2: "2nd harmonic",
                    3: "3rd harmonic",
                }.get(int(harmonic), f"harmonic {harmonic}")
                frame = ttk.LabelFrame(
                    transport_plot,
                    text=f"{PPMS_SR830_LABELS[key]} {order_label} {label}",
                    padding=2,
                )
                frame.grid(row=row, column=col, sticky="nsew")
                self.ppms_transport_plot_frames[(key, channel)] = frame
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)
                canvas = tk.Canvas(
                    frame,
                    height=PPMS_PLOT_ROW_MIN_HEIGHT_PX,
                    width=300,
                    background="white",
                    highlightthickness=0,
                )
                canvas.ppms_compact_plot = True
                canvas.grid(row=0, column=0, sticky="nsew")
                canvas.bind("<Configure>", lambda event: self._request_plot_redraw())
                self.ppms_transport_canvases[(key, channel)] = canvas
        self.ppms_transport_canvas = self.ppms_transport_canvases[("830_a", "x")]

    def _build_compact_controls(self, control, scan, instruments, plot) -> None:
        self.ppms_instruments_frame = instruments
        self.ppms_plot_frame = plot
        panel = ttk.Frame(control)
        panel.grid(row=0, column=0, sticky="nw")
        left = ttk.Frame(panel)
        left.grid(row=0, column=0, sticky="nw")
        harm = self.ppms_settings_panel = ttk.Frame(panel)
        harm.grid(row=0, column=1, sticky="ns")
        for row in range(4):
            harm.rowconfigure(row, weight=1)
        control.columnconfigure(0, weight=2, uniform="")
        control.columnconfigure(1, weight=1, uniform="")
        control.columnconfigure(2, weight=0, uniform="")

        self.ppms_simple_mode = tk.StringVar(value="Field")
        self.ppms_simple_range = [
            tk.StringVar(value=v) for v in self.ppms_default_field_range
        ]
        self.ppms_scan_target_widgets: dict[str, list[tk.Widget]] = {}
        safety_text = "Sample, ranges, wiring and motor clearance confirmed safe; no other program in control, MultiVu stays operable"
        safety_font = tkfont.nametofont("TkDefaultFont", root=self.root)
        ttk.Style(self.root).configure(
            "PPMSSafety.TCheckbutton",
            wraplength=safety_font.measure(safety_text) // 2
            + safety_font.measure("0") * 6,
        )
        ttk.Checkbutton(
            left,
            text=safety_text,
            style="PPMSSafety.TCheckbutton",
            variable=self.ppms_control_safety_var,
            command=self._update_ppms_control_ui,
        ).grid(row=0, column=0, columnspan=10, sticky="w")

        def entry(row, col, label, variable):
            ttk.Label(left, text=label).grid(
                row=row, column=col, sticky="e", padx=(0 if col == 0 else 8, 0)
            )
            widget = ttk.Entry(left, textvariable=variable, width=6)
            widget.grid(row=row, column=col + 1, sticky="ew", padx=2, pady=1)
            self.ppms_control_widgets.append(widget)
            target_mode = {
                "T K": "Temperature",
                "H Oe": "Field",
                "Angle °": "Angle",
                "I mA RMS": "Current",
                "f Hz": "Frequency",
            }.get(label)
            if target_mode:
                self.ppms_scan_target_widgets.setdefault(target_mode, []).append(
                    widget
                )
            return widget

        def button(row, col, label, command):
            widget = ttk.Button(left, text=label, command=command, width=8)
            widget.grid(row=row, column=col, padx=2)
            self.ppms_control_widgets.append(widget)
            target_mode = {
                "Set T": "Temperature",
                "Set H": "Field",
                "Move": "Angle",
                "Set I": "Current",
            }.get(label)
            if target_mode:
                self.ppms_scan_target_widgets.setdefault(target_mode, []).append(
                    widget
                )
            return widget

        entry(1, 0, "T K", self.ppms_temperature_target_var)
        entry(1, 2, "K/min", self.ppms_temperature_rate_var)
        button(1, 4, "Set T", self.set_ppms_temperature)
        entry(1, 5, "Angle °", self.ppms_motor_target_var)
        entry(1, 7, "Decel 0–14", self.ppms_motor_speed_var)
        self.ppms_motor_move_button = button(
            1, 9, "Move", lambda: self._ppms_motor_action("move")
        )
        self.ppms_control_widgets.remove(self.ppms_motor_move_button)
        entry(2, 0, "H Oe", self.ppms_field_target_var)
        entry(2, 2, "Oe/s", self.ppms_field_rate_var)
        button(2, 4, "Set H", self.set_ppms_field)
        button(2, 5, "Zero H", self.zero_ppms_field).grid_configure(
            columnspan=2, sticky="e"
        )
        self.ppms_persistent_button = button(
            2, 7, "Persist", self.switch_ppms_persistent
        )
        self.ppms_persistent_button.grid_configure(columnspan=2, sticky="e")
        entry(3, 0, "I mA RMS", self.ppms_transport_current_var)
        entry(3, 2, "f Hz", self.ppms_transport_frequency_var)
        button(3, 4, "Set I", self._ppms_set_current_direct)
        ttk.Button(
            left, text="I off", command=self.ppms_sources_off, width=8
        ).grid(row=3, column=5, columnspan=2, padx=2, sticky="e")
        entry(3, 7, "Comp. V", self.ppms_transport_compliance_var)

        self.ppms_lockin_choice_widgets = []
        for row, key in enumerate(PPMS_SR830_KEYS, 0):
            col, label, var = (
                13,
                f"{PPMS_SR830_LABELS[key]} harm.",
                self._ppms_harmonic_var(key),
            )
            ttk.Label(harm, text=label).grid(
                row=row, column=col, sticky="e", padx=(12, 3)
            )
            widget = ttk.Combobox(
                harm,
                values=PPMS_HARMONIC_CHOICES,
                textvariable=var,
                state="readonly",
                width=7,
            )
            widget.bind(
                "<<ComboboxSelected>>",
                lambda event, k=key: self._ppms_apply_selected_harmonic(k),
            )
            self.ppms_lockin_choice_widgets.append((widget, "readonly"))
            widget.grid(row=row, column=col + 1, sticky="ew")
            self.ppms_control_widgets.append(widget)
            select = ttk.Checkbutton(
                harm,
                text="Measure",
                variable=self.ppms_lockin_selection[key],
                command=self._refresh_ppms2_plot_layout,
            )
            select.grid(row=row, column=17, padx=3)
            self.ppms_control_widgets.append(select)
            self.ppms_lockin_choice_widgets.append((select, "normal"))
            self.shell._create_connection_indicator(harm, f"ppms_ref_{key}")
            range_var = tk.StringVar(value="Autorange ON")
            self.ppms_sr830_range_vars[key] = range_var
            ttk.Label(harm, textvariable=range_var, width=16).grid(
                row=row, column=18, sticky="w", padx=(6, 0)
            )

        ttk.Label(scan, text="Mode").grid(row=0, column=0, sticky="e", padx=(0, 2))
        mode = ttk.Combobox(
            scan,
            values=PPMS_SCAN_MODES,
            textvariable=self.ppms_simple_mode,
            state="readonly",
            width=18,
        )
        mode.grid(row=0, column=1, columnspan=3, sticky="ew", padx=2, pady=1)
        self.ppms_control_widgets.append(mode)
        ttk.Label(scan, text="Settle s").grid(row=0, column=4, sticky="e", padx=(6, 2))
        settle_entry = ttk.Entry(
            scan, textvariable=self.ppms_field_scan_stable_var, width=6
        )
        settle_entry.grid(row=0, column=5, sticky="ew", padx=2, pady=1)
        self.ppms_control_widgets.append(settle_entry)
        ttk.Label(scan, text="Acquire s").grid(
            row=0, column=6, sticky="e", padx=(6, 2)
        )
        acquisition_entry = ttk.Entry(
            scan, textvariable=self.ppms_acquisition_time_var, width=6
        )
        acquisition_entry.grid(row=0, column=7, sticky="ew", padx=2, pady=1)
        self.ppms_control_widgets.append(acquisition_entry)

        for i, (label, var) in enumerate(
            zip(("Start", "End", "Step"), self.ppms_simple_range)
        ):
            ttk.Label(scan, text=label).grid(
                row=1, column=2 * i, sticky="e", padx=(0 if i == 0 else 6, 2)
            )
            widget = ttk.Entry(scan, textvariable=var, width=6)
            widget.grid(row=1, column=2 * i + 1, sticky="ew", padx=2, pady=1)
            self.ppms_control_widgets.append(widget)
        self.ppms_scan_step_entry = widget

        unit = tk.StringVar(value="Oe")
        self.ppms_simple_unit = unit
        self.ppms_temperature_scan_range = [
            tk.StringVar(value=v) for v in ("300", "280", "5")
        ]
        temperature_range_frame = ttk.Frame(scan)
        ttk.Label(temperature_range_frame, text="Outer temperature K:").grid(
            row=0, column=0
        )
        for i, (label, var) in enumerate(
            zip(("Start", "End", "Step"), self.ppms_temperature_scan_range)
        ):
            ttk.Label(temperature_range_frame, text=label).grid(
                row=0, column=1 + 2 * i
            )
            entry_w = ttk.Entry(temperature_range_frame, textvariable=var, width=6)
            entry_w.grid(row=0, column=2 + 2 * i)
            self.ppms_control_widgets.append(entry_w)
        self.ppms_temperature_delay_var = tk.StringVar(
            value=f"{PPMS_TEMPERATURE_DELAY_S:g}"
        )
        ttk.Label(temperature_range_frame, text="Wait s").grid(
            row=0, column=7, padx=(6, 0)
        )
        delay_entry = ttk.Entry(
            temperature_range_frame,
            textvariable=self.ppms_temperature_delay_var,
            width=6,
        )
        delay_entry.grid(row=0, column=8)
        self.ppms_control_widgets.append(delay_entry)

        self.ppms_frequency_scan_range = [
            tk.StringVar(value=f"{value:g}")
            for value in (NONLOCAL_FSCAN_START_HZ, NONLOCAL_FSCAN_STOP_HZ)
        ]
        self.ppms_frequency_scan_note_var = tk.StringVar()
        frequency_range_frame = ttk.Frame(scan)
        self.ppms_frequency_range_frame = frequency_range_frame
        ttk.Label(frequency_range_frame, text="Outer frequency Hz:").grid(
            row=0, column=0
        )
        for i, (label, var) in enumerate(
            zip(("Start", "End"), self.ppms_frequency_scan_range)
        ):
            ttk.Label(frequency_range_frame, text=label).grid(
                row=0, column=1 + 2 * i
            )
            entry_w = ttk.Entry(frequency_range_frame, textvariable=var, width=6)
            entry_w.grid(row=0, column=2 + 2 * i)
            self.ppms_control_widgets.append(entry_w)
        frequency_note = ttk.Label(frequency_range_frame, width=1)
        frequency_note.grid(row=0, column=5, sticky="ew", padx=(6, 0))
        frequency_range_frame.columnconfigure(5, weight=1)
        show_trimmed(frequency_note, self.ppms_frequency_scan_note_var)

        def refresh_frequency_note(*_args):
            self.ppms_frequency_scan_note_var.set(
                ppms_frequency_scan_note(
                    *(var.get() for var in self.ppms_frequency_scan_range)
                )
            )

        for var in self.ppms_frequency_scan_range:
            var.trace_add("write", refresh_frequency_note)
        refresh_frequency_note()

        self.ppms_outer_field_scan_range = [
            tk.StringVar(value=v) for v in self.ppms_default_field_range
        ]
        field_range_frame = ttk.Frame(scan)
        self.ppms_outer_field_range_frame = field_range_frame
        ttk.Label(field_range_frame, text="Outer field Oe:").grid(row=0, column=0)
        for i, (label, var) in enumerate(
            zip(("Start", "End", "Step"), self.ppms_outer_field_scan_range)
        ):
            ttk.Label(field_range_frame, text=label).grid(row=0, column=1 + 2 * i)
            entry_w = ttk.Entry(field_range_frame, textvariable=var, width=6)
            entry_w.grid(row=0, column=2 + 2 * i)
            self.ppms_control_widgets.append(entry_w)

        def change_mode(_event=None):
            defaults = self._ppms_scan_mode_defaults()
            combined = self.ppms_simple_mode.get() in (
                "Field+Temperature",
                "Angle+Temperature",
                "Current+Temperature",
            )
            if combined:
                temperature_range_frame.grid(
                    row=4, column=0, columnspan=9, sticky="w", pady=(2, 0)
                )
            else:
                temperature_range_frame.grid_remove()
            if self.ppms_simple_mode.get() == "Frequency+Current":
                frequency_range_frame.grid(
                    row=4, column=0, columnspan=9, sticky="ew", pady=(2, 0)
                )
            else:
                frequency_range_frame.grid_remove()
            if self.ppms_simple_mode.get() == "Angle+Field":
                field_range_frame.grid(
                    row=4, column=0, columnspan=9, sticky="w", pady=(2, 0)
                )
            else:
                field_range_frame.grid_remove()
            scan.configure(text="PPMS scan")
            values = defaults[self.ppms_simple_mode.get()]
            for var, value in zip(self.ppms_simple_range, values):
                var.set(value)
            unit.set(values[3])
            self._apply_ppms_mode_acquisition_default(self.ppms_simple_mode.get())
            self._update_ppms_control_ui()
            if not self.state.scan_rows:
                self.ppms_plot_axis = PPMS_SCAN_PLOT_AXES[
                    self.ppms_simple_mode.get()
                ]
                self._redraw_ppms_transport_plot()

        mode.bind("<<ComboboxSelected>>", change_mode)
        self.ppms_scan_mode_combo = mode
        ttk.Label(scan, textvariable=unit).grid(
            row=1, column=6, columnspan=3, sticky="w", padx=(2, 0)
        )
        actions = self.ppms_field_scan_start_button.master
        actions.grid(row=2, column=0, columnspan=9, sticky="w", pady=(3, 0))
        for col, (widget, width) in enumerate(
            (
                (self.ppms_field_scan_start_button, 10),
                (self.ppms_field_scan_stop_button, 6),
                (self.ppms_field_scan_save_button, 10),
            )
        ):
            widget.configure(width=width)
            widget.grid(row=0, column=col, padx=(0, 4))
        self.ppms_field_scan_progress.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=(0, 6), pady=(3, 0)
        )
        status = ttk.Label(
            scan, textvariable=self.ppms_field_scan_status_var, wraplength=0
        )
        status.grid(row=3, column=2, columnspan=7, sticky="ew", pady=(3, 0))
        status.bind(
            "<Configure>",
            lambda event: event.widget.configure(
                wraplength=max(100, event.width - 8)
            ),
        )
        self.ppms_zero_persist_button = ttk.Button(
            actions,
            text="Zero field + persist after scan",
            command=self._queue_ppms_zero_persist,
            width=26,
            state="disabled",
        )
        self.ppms_zero_persist_button.grid(row=0, column=3)
        scan.columnconfigure(8, weight=1)

        instruments.configure(padding=3)
        self.ppms_macro_row.grid_configure(
            row=2, column=0, rowspan=1, columnspan=1, sticky="w", padx=2
        )
        self._ppms_arrange_by_width(
            control.master, instruments, self.ppms_metrics_frame, control, scan, left, harm
        )

    def _build_zurich_ui(self) -> None:
        row = ttk.Frame(self.ppms_instruments_frame)
        compact = False
        row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(3, 0), padx=2)
        row.columnconfigure(11, weight=1)
        ttk.Label(row, text="Zurich MFLI").grid(row=0, column=0)
        self.zurich_entries = []
        for column, variable, width in (
            (1, self.zurich_resource, 12),
            (5, self.zurich_timeconstant, 7),
        ):
            if column == 1:
                entry = create_zurich_chooser(row, variable, match_chars=None)
                extra = entry.zurich_shortfall
            else:
                entry = ttk.Entry(row, textvariable=variable, width=width)
                extra = 0
            entry.grid(row=0, column=column, padx=(3, 3 + extra))
            self.zurich_entries.append(entry)
        self.ppms_zurich_reference_label = ttk.Label(row, text="Reference")
        self.ppms_zurich_reference_label.grid(row=0, column=2, padx=(4, 2))
        self.zurich_reference_combo = ttk.Combobox(
            row,
            textvariable=self.zurich_reference,
            values=tuple(NONLOCAL_MFLI_REFERENCE_INPUTS),
            state="readonly",
            width=12,
        )
        self.zurich_reference_combo.grid(row=0, column=3)
        ttk.Label(row, text="TC (s)").grid(row=0, column=4, padx=(4, 2))
        zurich_row = 3
        ttk.Label(self.ppms_settings_panel, text="Zurich harmonic").grid(
            row=zurich_row, column=13, sticky="e", padx=(12, 3)
        )
        ttk.Label(
            self.ppms_settings_panel, textvariable=self.zurich_range_var, width=16
        ).grid(row=zurich_row, column=18, sticky="w", padx=(6, 0))
        self.zurich_harmonic_combo = ttk.Combobox(
            self.ppms_settings_panel,
            textvariable=self.zurich_harmonic,
            values=(1, 2, 3, "1+2", "1+2+3"),
            state="readonly",
            width=7,
        )
        self.zurich_harmonic_combo.grid(row=zurich_row, column=14, sticky="ew")
        self.zurich_harmonic_combo.bind(
            "<<ComboboxSelected>>",
            lambda event: self._apply_ppms2_zurich_harmonic(),
        )
        self.zurich_select = ttk.Checkbutton(
            self.ppms_settings_panel,
            text="Measure",
            variable=self.ppms_lockin_selection["zurich"],
            command=self._refresh_ppms2_plot_layout,
        )
        self.zurich_select.grid(row=zurich_row, column=17, padx=3)
        self.shell._create_connection_indicator(
            self.ppms_settings_panel, "zurich_reference"
        )
        self.zurich_connect_button = ttk.Button(
            row,
            text="Connect / apply settings",
            command=self.connect_ppms2_zurich,
        )
        self.zurich_connect_button.grid(row=0, column=8, padx=4)
        self.zurich_disconnect_button = ttk.Button(
            row,
            text="Disconnect",
            command=self.disconnect_ppms2_zurich,
            state="disabled",
            width=10,
        )
        self.zurich_disconnect_button.grid(row=0, column=9)
        self.shell._create_connection_indicator(
            row, "zurich_connection"
        ).grid(row=0, column=10, padx=3, sticky="w")
        status_label = ttk.Label(row, width=1, wraplength=0)
        status_label.grid(row=0, column=11, sticky="ew")
        show_trimmed(status_label, self.zurich_status)

        self.zurich_canvases = {}
        self.zurich_plot_frames = {}
        for index, channel in enumerate(
            ("x", "y", "theta", "x_2", "y_2", "theta_2", "x_3", "y_3", "theta_3")
        ):
            column = index % 3
            label = "θ" if channel == "theta" else channel.upper()
            frame = ttk.LabelFrame(
                self.ppms_plot_frame, text=f"Zurich {label} (Demod 1)", padding=2
            )
            frame.grid(row=3 + index // 3, column=column, sticky="nsew")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            canvas = tk.Canvas(
                frame,
                height=PPMS_PLOT_ROW_MIN_HEIGHT_PX,
                width=300,
                background="white",
                highlightthickness=0,
            )
            canvas.ppms_compact_plot = True
            canvas.grid(row=0, column=0, sticky="nsew")
            canvas.bind("<Configure>", lambda event: self._request_plot_redraw())
            self.zurich_canvases[channel] = canvas
            self.zurich_plot_frames[channel] = frame

        self.ppms_harmonic_canvases = {}
        self.ppms_harmonic_plot_frames = {}
        for order in PPMS_HARMONIC_ORDERS:
            for column, channel in enumerate(("x", "y", "theta")):
                frame = ttk.LabelFrame(
                    self.ppms_plot_frame,
                    text=PPMS_HARMONIC_LABELS[order],
                    padding=2,
                )
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)
                canvas = tk.Canvas(
                    frame,
                    height=PPMS_PLOT_ROW_MIN_HEIGHT_PX,
                    width=300,
                    background="white",
                    highlightthickness=0,
                )
                canvas.ppms_compact_plot = True
                canvas.grid(row=0, column=0, sticky="nsew")
                canvas.bind("<Configure>", lambda event: self._request_plot_redraw())
                self.ppms_harmonic_canvases[(order, channel)] = canvas
                self.ppms_harmonic_plot_frames[(order, channel)] = frame

    def _ppms_arrange_by_width(
        self, content, instruments, metrics, control, scan, left, harm
    ) -> None:
        cells, state = {}, {}

        def harm_width() -> int:
            if not cells:
                for widget in harm.grid_slaves():
                    info = widget.grid_info()
                    cells[widget] = (
                        int(info["row"]),
                        int(info["column"]),
                        sum(
                            int(float(str(v)))
                            for v in (
                                lambda pad: pad
                                if isinstance(pad, (tuple, list))
                                else str(pad).split()
                            )(info.get("padx", 0))
                        ),
                    )
            widths = {}
            for widget, (_row, column, padx) in cells.items():
                widths[column] = max(
                    widths.get(column, 0), widget.winfo_reqwidth() + padx
                )
            return sum(widths.values())

        def place(_event=None) -> None:
            try:
                available = content.winfo_width() or 0
                if available <= 1:
                    return
                one, inset = harm_width(), self.shell._pair_inset(control)
                instr = (
                    state["instr"]
                    if state.get("tight")
                    else instruments.winfo_reqwidth()
                )
                state["instr"] = instr
                wide = (
                    available
                    >= max(instr, inset + left.winfo_reqwidth() + one)
                    + 6
                    + scan.winfo_reqwidth()
                )
                columns = 0 if wide else 2 if available >= inset + 2 * one else 1
                tight = not wide and available < instr
                window = content.winfo_toplevel().winfo_width()
            except (tk.TclError, ValueError):
                return
            if (state.get("columns"), state.get("tight")) == (columns, tight):
                return
            now, guard = time.monotonic(), state.setdefault("guard", [None, 0.0, 0])
            if guard[0] != window or now - guard[1] > 1.0:
                guard[:] = [window, now, 0]
            guard[2] += 1
            if guard[2] > PAIR_FLIP_LIMIT and "columns" in state:
                return
            if bool(state.get("tight")) != tight:
                self._ppms_tighten_rows(tight)
            state["columns"], state["tight"] = columns, tight
            content.columnconfigure(0, weight=2 if wide else 0)
            instruments.grid_configure(columnspan=1 if wide else 2)
            control.grid_configure(columnspan=1 if wide else 2)
            metrics.grid_configure(row=1 if wide else 4)
            scan.grid_configure(
                row=3 if wide else 4,
                column=1 if wide else 0,
                padx=(6, 0) if wide else 0,
            )
            harm.grid_configure(
                row=0 if wide else 1,
                column=1 if wide else 0,
                sticky="ns" if wide else "w",
                pady=0 if wide else (4, 0),
            )
            for widget, (row, column, _padx) in cells.items():
                widget.grid_configure(
                    row=row // 2 if columns == 2 else row,
                    column=column + 6 * (row % 2) if columns == 2 else column,
                )

        for widget in (content, scan):
            widget.bind("<Configure>", place, add="+")
        place()

    def _ppms_tighten_rows(self, tight: bool) -> None:
        self.ppms_rows_tight = tight
        self.ppms_host_label.configure(text="IP:" if tight else "PPMS IP:")
        for widget, wide, narrow in (
            (self.ppms_backend_combo, 20, 18),
            (self.ppms_host_entry, 16, 14),
            (self.ppms_port_entry, 7, 6),
        ):
            widget.configure(width=narrow if tight else wide)
            widget.grid_configure(padx=(0, 3) if tight else (0, 8))
        self.ppms_read_button.grid_configure(padx=(3, 2) if tight else (8, 2))
        self.ppms_environment_lamp.grid_configure(padx=(3, 1) if tight else (8, 1))
        self.ppms_connection_shown()
        short = getattr(self, "ppms_short_status_vars", {})
        self.ppms_short_status_vars = short
        words = {
            "Not connected": "Offline",
            "Connected": "Online",
            "Pass complete": "Done",
        }
        for key, (
            name,
            text,
            entry,
            lamp,
            status,
            shown,
        ) in self.ppms_instrument_cells.items():
            sr830 = key in PPMS_SR830_KEYS
            if tight and key not in short:
                compact = shown if sr830 else self._ppms_compact_status(shown)
                short[key] = tk.StringVar()
                refresh = (
                    lambda *_args, c=compact, s=short[key]: s.set(
                        words.get(c.get(), c.get())
                    )
                )
                compact.trace_add("write", refresh)
                refresh()
            name.configure(text=f"{key}:" if tight and not sr830 else text)
            entry.configure(
                width=(12 if sr830 else 13)
                if tight
                else (16 if sr830 else 18)
            )
            lamp.grid_configure(padx=(3, 1) if tight else (8, 1))
            status.configure(
                width=8 if tight else 13, textvariable=short[key] if tight else shown
            )
            status.grid_configure(padx=(1, 3) if tight else (1, 8))
        label = getattr(self, "ppms_zurich_reference_label", None)
        button = getattr(self, "zurich_connect_button", None)
        if label is not None and button is not None:
            label.configure(text="Ref." if tight else "Reference")
            button.configure(
                text="Connect" if tight else "Connect / apply settings",
                width=8 if tight else "",
            )

    def _ppms_compact_status(self, source: tk.StringVar) -> tk.StringVar:
        display = tk.StringVar(value="Not connected")

        def refresh(*_):
            text = str(source.get()).lower()
            if any(
                word in text
                for word in (
                    "warning",
                    "failed",
                    "error",
                    "overload",
                    "unlocked",
                    "mismatch",
                )
            ):
                value = "Warning"
            elif "pass complete" in text:
                value = "Pass complete"
            elif "not connected" in text:
                value = "Not connected"
            elif "not read" in text:
                value = "Not read"
            elif "lock" in text:
                value = "Locked"
            elif any(word in text for word in ("connecting", "updating", "waiting")):
                value = "Updating"
            else:
                value = "Connected"
            display.set(value)

        source.trace_add("write", refresh)
        refresh()
        return display

    def _ppms_scan_mode_defaults(self) -> dict:
        defaults = {
            "Temperature": ("300", "280", "5", "K"),
            "Field": (*self.ppms_default_field_range, "Oe"),
            "Field+Temperature": (*self.ppms_default_field_range, "Oe"),
            "Angle": ("0", "360", "5", "°"),
            "Current": ("0.01", "1", "0.01", "mA RMS"),
            "Angle+Temperature": ("0", "360", "5", "°"),
            "Current+Temperature": ("0.01", "1", "0.01", "mA RMS"),
            "Frequency": (
                f"{NONLOCAL_FSCAN_START_HZ:g}",
                f"{NONLOCAL_FSCAN_STOP_HZ:g}",
                "",
                "Hz (primes)",
            ),
            "Frequency+Current": ("0.01", "1", "0.01", "mA RMS"),
            "Angle+Field": ("0", "360", "5", "°"),
        }
        return defaults

    def _ppms_sr830_keys(self) -> tuple[str, ...]:
        return tuple(
            key
            for key in PPMS_SR830_KEYS
            if key in self.state.ppms_instrument_controllers
        )

    def _ppms_harmonic_var(self, key: str):
        return getattr(self, PPMS_SR830_HARMONIC_ATTRS[key], None)

    def _ppms_harmonic_orders(self, key: str) -> tuple[int, ...]:
        variable = self._ppms_harmonic_var(key)
        value = str(variable.get()).strip() if variable is not None else "1"
        return parse_harmonic_mode(value, mode_orders=PPMS_HARMONIC_MODE_ORDERS)

    def _ppms_harmonic_value(self, key: str, default: int = 1) -> int:
        try:
            return self._ppms_harmonic_orders(key)[0]
        except (ValueError, AttributeError, tk.TclError):
            return int(default)

    def _ppms_harmonic_passes(self, settings: dict[str, object]):
        schedules = {
            key: self._ppms_harmonic_orders(key) for key in settings["active_lockins"]
        }

        def validate_pass(config):
            validate_ppms_transport_scan_settings(
                config["current_rms_ma"],
                config["frequency_hz"],
                config["compliance_v"],
                config["harmonic_a"],
                config["harmonic_b"],
                config["active_lockins"],
                allow_no_lockins=bool(config.get("allow_no_lockins", False)),
                harmonic_c=config["harmonic_c"],
            )

        return schedule_harmonic_passes(
            settings,
            schedules,
            validator=validate_pass,
            harmonic_setting_keys=PPMS_SR830_HARMONIC_SETTING_KEYS,
        )

    def _ppms_selected_lockins(self) -> tuple[tuple[str, ...], bool]:
        active = tuple(
            k
            for k in self._ppms_sr830_keys()
            if (
                self.ppms_lockin_selection[k].get()
                if k in self.ppms_lockin_selection
                else self.state.ppms_instrument_controllers[k].connected
            )
        )
        zurich = bool(
            self.ppms_lockin_selection.get("zurich")
            and self.ppms_lockin_selection["zurich"].get()
        )
        missing = [
            k
            for k in active
            if not self.state.ppms_instrument_controllers[k].connected
        ]
        if zurich and not self.state.zurich_controller.mfli_connected:
            missing.append("Zurich")
        if missing:
            raise ValueError(
                "Connect the selected lock-ins or untick them: " + ", ".join(missing)
            )
        if not active and not zurich:
            raise ValueError("Select at least one lock-in for measurement")
        return active, zurich

    def _on_ppms_backend_changed(self, _event=None) -> None:
        backend = self.ppms_backend_var.get()
        default_port = (
            PPMS_MULTIPYVU_DEFAULT_PORT
            if backend == PPMS_MULTIPYVU_BACKEND
            else PPMS_QDINSTRUMENT_DEFAULT_PORT
        )
        try:
            current_port = int(self.ppms_port_var.get())
        except (ValueError, tk.TclError):
            current_port = None
        if current_port in (
            None,
            PPMS_QDINSTRUMENT_DEFAULT_PORT,
            PPMS_MULTIPYVU_DEFAULT_PORT,
        ):
            self.ppms_port_var.set(default_port)

    def _set_ppms_environment_ui(self, connected: bool) -> None:
        self.shell._set_connection_indicator("ppms_environment", connected)
        busy = self.state.environment_busy or self.state.scan_running
        self.ppms_backend_combo.configure(
            state="disabled" if connected or busy else "readonly"
        )
        entry_state = "disabled" if connected or busy else "normal"
        self.ppms_host_entry.configure(state=entry_state)
        self.ppms_port_entry.configure(state=entry_state)
        self.ppms_connect_button.configure(
            state="disabled" if connected or busy else "normal"
        )
        self.ppms_disconnect_button.configure(
            state="normal" if connected and not busy else "disabled"
        )
        self.ppms_read_button.configure(
            state="normal" if connected and not busy else "disabled"
        )
        self._update_ppms_control_ui()

    def _update_ppms_control_ui(self) -> None:
        connected = self.state.ppms_environment_controller.connected
        confirmed = bool(self.ppms_control_safety_var.get())
        scanning = self.state.scan_running
        busy = self.state.environment_busy
        macro_running = self.macro_runner.running
        enabled = (
            connected and confirmed and not scanning and not busy and not macro_running
        )
        state = "normal" if enabled else "disabled"
        for widget in self.ppms_control_widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                widget.configure(state=state)
        self.ppms_field_scan_stop_button.configure(
            state="normal" if scanning else "disabled"
        )
        self.ppms_field_scan_save_button.configure(
            state="normal"
            if self.state.scan_rows and not scanning
            else "disabled"
        )
        if hasattr(self, "ppms_zero_persist_button"):
            self.ppms_zero_persist_button.configure(
                state="normal" if scanning else "disabled"
            )
        if hasattr(self, "ppms_scan_target_widgets"):
            self._update_ppms_scan_target_widgets(enabled)
        elif hasattr(self, "ppms_scan_step_entry"):
            if enabled and self.ppms_simple_mode.get() == "Frequency":
                self.ppms_scan_step_entry.configure(state="disabled")
        if hasattr(self, "ppms_motor_move_button"):
            qd = (
                self.state.ppms_environment_controller.backend
                == PPMS_QDINSTRUMENT_BACKEND
            )
            self.ppms_motor_move_button.configure(
                state="normal"
                if enabled and qd and self.ppms_motor_confirm_var.get()
                else "disabled"
            )
        for widget, idle_state in getattr(self, "ppms_lockin_choice_widgets", ()):
            widget.configure(
                state="disabled" if scanning or busy else idle_state
            )
        self._update_ppms_sr830_tc_state()
        if hasattr(self, "zurich_connect_button"):
            self._set_zurich_busy(self.state.zurich_busy)

    def _update_ppms_scan_target_widgets(self, enabled: bool) -> None:
        mode = self.ppms_simple_mode.get()
        scanned = {
            "Field+Temperature": {"Field", "Temperature"},
            "Angle+Temperature": {"Angle", "Temperature"},
            "Current+Temperature": {"Current", "Temperature"},
            "Angle+Field": {"Angle", "Field"},
            "Frequency+Current": {"Frequency", "Current"},
        }.get(mode, {mode})
        for parameter, widgets in self.ppms_scan_target_widgets.items():
            widget_state = "disabled" if not enabled or parameter in scanned else "normal"
            for widget in widgets:
                widget.configure(state=widget_state)
        if enabled and mode == "Frequency" and hasattr(self, "ppms_scan_step_entry"):
            self.ppms_scan_step_entry.configure(state="disabled")

    def _require_ppms_control_ready(self) -> None:
        if not self.state.ppms_environment_controller.connected:
            raise RuntimeError("Please connect the PPMS first")
        if not self.ppms_control_safety_var.get():
            raise RuntimeError("Please tick the PPMS safety confirmation first")
        if self.state.scan_running:
            raise RuntimeError("The PPMS is scanning field; stop the scan first")
        if self.motor_service.pending:
            raise RuntimeError(
                "Motor status after the last command is unconfirmed; check in MultiVu that it stopped, then click Read angle"
            )

    def _ppms_temperature_approach_code(self, label: str) -> int:
        return {"Fast settle": 0, "No overshoot": 1}.get(label, 1)

    def _ppms_field_approach_code(self, label: str) -> int:
        return {"Linear": 0, "No overshoot": 1, "Oscillate": 2}.get(label, 1)

    def _ppms_field_mode_code(self, label: str) -> int:
        return {"Persistent": 0, "Driven": 1}.get(label, 0)

    def _ppms_adjust_low_temperature_rate(self, *_args) -> None:
        try:
            target = float(self.ppms_temperature_target_var.get())
            if math.isfinite(target) and target > 0:
                rate = "1" if target < 10 else "10"
                if self.ppms_temperature_rate_var.get() != rate:
                    self.ppms_temperature_rate_var.set(rate)
        except (ValueError, TypeError, tk.TclError):
            pass

    def _run_ppms_command(
        self,
        label: str,
        operation,
        *,
        success_text: str = "Command completed; live readback in progress",
    ) -> None:
        if self.state.environment_busy:
            return
        try:
            self._require_ppms_control_ready()
        except BaseException as error:
            messagebox.showwarning("PPMS control", str(error), parent=self.root)
            return
        self.state.environment_busy = True
        self.ppms_connection_var.set(label)
        self._set_ppms_environment_ui(True)
        try:
            future = self.state.ppms_executor.submit(operation)
        except Exception as error:
            self.state.environment_busy = False
            self.ppms_connection_var.set(
                f"Command not started: {short_status_error(error, 120)}"
            )
            self._set_ppms_environment_ui(True)
            if not self.shell.closing:
                messagebox.showerror(
                    "PPMS control failed", str(error), parent=self.root
                )
            return

        def finished() -> None:
            self.state.environment_busy = False
            try:
                result = future.result()
                if result is not None:
                    self._apply_ppms_environment_readback(result)
                if result is not None or not self.state.scan_stop_event.is_set():
                    self.ppms_connection_var.set(
                        result.get("command_verification", success_text)
                        if result is not None
                        else success_text
                    )
            except PPMSScanCancelled:
                pass
            except BaseException as error:
                self.ppms_connection_var.set(
                    f"Command failed: {short_status_error(error, 120)}"
                )
                if not self.shell.closing:
                    messagebox.showerror(
                        "PPMS control failed", str(error), parent=self.root
                    )
            finally:
                self._set_ppms_environment_ui(True)

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def set_ppms_temperature(self) -> None:
        try:
            if (
                float(self.ppms_temperature_target_var.get()) < 10
                and float(self.ppms_temperature_rate_var.get()) > 1
            ):
                self.ppms_temperature_rate_var.set("1")
        except (ValueError, TypeError, tk.TclError):
            pass
        try:
            target = float(self.ppms_temperature_target_var.get())
            rate = float(self.ppms_temperature_rate_var.get())
            current = None
            if self.state.last_readback is not None:
                current = float(self.state.last_readback["temperature_k"])
                if (
                    current <= PPMS_TEMPERATURE_LOW_BOUNDARY_K
                    and rate > PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                ):
                    rate = PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                    self.ppms_temperature_rate_var.set("1")
            validate_ppms_temperature_command(target, rate, current)
            approach = self._ppms_temperature_approach_code(
                self.ppms_temperature_approach_var.get()
            )
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror(
                "PPMS temperature parameters", str(error), parent=self.root
            )
            return
        self._run_ppms_command(
            f"Setting temperature {target:g} K…",
            lambda: self.state.ppms_environment_controller.set_temperature(
                target, rate, approach
            ),
        )

    def _parse_ppms_field_parameters(
        self, target: float | None = None
    ) -> tuple[float, float, int, int]:
        if target is None:
            target = float(self.ppms_field_target_var.get())
        rate = float(self.ppms_field_rate_var.get())
        target, rate = validate_ppms_field_command(target, rate)
        approach = self._ppms_field_approach_code(self.ppms_field_approach_var.get())
        mode = self._ppms_field_mode_code(self.ppms_field_mode_var.get())
        return target, rate, approach, mode

    def set_ppms_field(self) -> None:
        try:
            target, rate, approach, mode = self._parse_ppms_field_parameters()
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror(
                "PPMS field parameters", str(error), parent=self.root
            )
            return
        self._send_ppms_field_setpoint(
            f"Setting field {target:+g} Oe…", target, rate, approach, mode
        )

    def _send_ppms_field_setpoint(
        self, label: str, target: float, rate: float, approach: int, mode: int
    ) -> None:
        remote = self.state.ppms_environment_controller
        follow = (
            mode == 0 and getattr(remote, "backend", "") == PPMS_QDINSTRUMENT_BACKEND
        )

        def operation():
            self.state.persistent_watch = None
            result = remote.set_field(target, rate, approach, mode)
            if not follow:
                return result
            sent = time.monotonic()
            try:
                distance = abs(target - float(result["field_oe"]))
            except (KeyError, TypeError, ValueError):
                distance = abs(target)
            return dict(
                result,
                persistent_watch={
                    "target": target,
                    "sent": sent,
                    "left_persistent": False,
                    "deadline": sent + distance / rate + PPMS_PERSISTENT_MARGIN_S,
                },
            )

        if follow:
            success_text = f"Going to {target:+g} Oe, then Persistent…"
        elif mode == 0:
            success_text = (
                f"Sent {target:+g} Oe, ending Persistent; see the field status"
            )
        else:
            success_text = "Command completed; live readback in progress"
        self._run_ppms_command(label, operation, success_text=success_text)

    def _end_ppms_persistent_follow_up(self, owner: str) -> None:
        if self.state.persistent_watch:
            self.state.persistent_watch = None
            self.ppms_connection_var.set(
                f"{owner} now sets the field; Persistent no longer followed"
            )

    def _follow_ppms_persistent_setpoint(self, result: dict[str, object]) -> None:
        watch = self.state.persistent_watch
        if not watch:
            return
        read_at = result.get("read_at")
        if not isinstance(read_at, (int, float)) or read_at <= watch["sent"]:
            return
        try:
            measured = float(result["field_oe"])
        except (KeyError, TypeError, ValueError):
            return
        target = watch["target"]
        status = str(result.get("field_status", ""))
        tolerance = max(
            PPMS_FIELD_TOLERANCE_OE, abs(target) * PPMS_FIELD_RELATIVE_TOLERANCE
        )
        at_target = abs(measured - target) <= tolerance
        if status.strip().lower().replace(" ", "") not in (
            "persistent",
            "stablepersistent",
        ):
            watch["left_persistent"] = True
        elif at_target and (
            watch["left_persistent"]
            or read_at - watch["sent"] >= PPMS_PERSISTENT_CONFIRM_S
        ):
            self.state.persistent_watch = None
            self.ppms_connection_var.set(
                f"Field {target:+g} Oe reached; Persistent confirmed"
            )
            return
        elif watch["left_persistent"] and not at_target:
            self.state.persistent_watch = None
            self.ppms_connection_var.set(
                f"Persistent at {measured:+.7g} Oe, not {target:+g} Oe; check MultiVu, not resent"
            )
            return
        if read_at >= watch["deadline"]:
            self.state.persistent_watch = None
            self.ppms_connection_var.set(
                f"{status} at {measured:+.7g} Oe, not Persistent at {target:+g} Oe in time; check MultiVu, not resent"
                if watch["left_persistent"]
                else f"Still Persistent at {measured:+.7g} Oe, {target:+g} Oe not reached; check MultiVu, not resent"
            )

    def switch_ppms_persistent(self) -> None:
        if self.state.scan_running:
            messagebox.showwarning(
                "PPMS Persistent",
                "Stop the scan before switching the field mode.",
                parent=self.root,
            )
            return
        try:
            rate = float(self.ppms_field_rate_var.get())
            validate_ppms_field_command(0.0, rate)
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror("PPMS Persistent", str(error), parent=self.root)
            return

        def operation():
            remote = self.state.ppms_environment_controller
            data = remote.read_environment()
            status = str(data.get("field_status", "")).strip().lower()
            if status in ("persistent", "stablepersistent"):
                return dict(
                    data,
                    command_verification="Field confirmed Persistent; no set command sent",
                )
            if status not in ("stabledriven", "driven", "holding", "stable"):
                raise RuntimeError(
                    f"Field not yet stable, not switching to Persistent: {data.get('field_status')}"
                )
            target, _ = validate_ppms_field_command(float(data["field_oe"]), rate)
            result = remote.set_field(target, rate, 0, 0)
            return dict(
                result,
                command_verification=(
                    "Field confirmed Persistent"
                    if str(result.get("field_status", "")).strip().lower()
                    in ("persistent", "stablepersistent")
                    else "Persistent requested; the switch may take time, watch the live field status"
                ),
            )

        self._run_ppms_command(
            "Switching to Persistent at the current measured field…", operation
        )

    def zero_ppms_field(self) -> None:
        try:
            _target, rate, approach, mode = self._parse_ppms_field_parameters(0.0)
        except (ValueError, TypeError, tk.TclError) as error:
            messagebox.showerror(
                "PPMS field parameters", str(error), parent=self.root
            )
            return
        self.ppms_field_target_var.set("0")
        self._send_ppms_field_setpoint("Zeroing field…", 0.0, rate, approach, mode)

    def step_ppms_field(self, direction: int) -> None:
        try:
            if self.state.last_readback is None:
                raise RuntimeError("No PPMS field readback yet; click Read now first")
            step = abs(float(self.ppms_field_step_var.get()))
            if not math.isfinite(step) or step <= 0:
                raise ValueError("PPMS field step must be greater than 0 Oe")
            current = float(self.state.last_readback["field_oe"])
            target = current + (1 if int(direction) > 0 else -1) * step
            target, rate, approach, mode = self._parse_ppms_field_parameters(target)
        except (ValueError, TypeError, RuntimeError, tk.TclError) as error:
            messagebox.showerror("PPMS field step", str(error), parent=self.root)
            return
        self.ppms_field_target_var.set(f"{target:.9g}")
        self._send_ppms_field_setpoint(
            f"Stepping to {target:+g} Oe…", target, rate, approach, mode
        )

    def _ppms_publish_motor_readback(self, data: dict[str, object]) -> None:
        text = f"{data['angle_deg']:.4f}° / {data['status']}"

        def update():
            if not self.shell.closing:
                self.ppms_angle_live_var.set(text)

        self.shell.post_ui(update)

    def _confirm_large_motor_move(self, current_deg: float, target_deg: float) -> bool:
        answered = threading.Event()
        accepted: list[bool] = []

        def ask():
            try:
                accepted.append(
                    not self.shell.closing
                    and messagebox.askyesno(
                        "PPMS large rotation warning",
                        f"Current {current_deg:.4f}° → target {target_deg:g}°, rotation exceeds 30°.\n"
                        "Confirm the mechanical travel and wiring clearance are safe; there is no software motor emergency stop. Continue?",
                        parent=self.root,
                        default="no",
                    )
                )
            finally:
                answered.set()

        self.shell.post_ui(ask)
        while not answered.wait(0.1):
            if self.shell.closing:
                return False
        return bool(accepted and accepted[0])

    def _ppms_motor_action(self, action: str) -> None:
        if (
            self.state.environment_busy
            or self.state.scan_running
            or self.shell.closing
        ):
            return
        try:
            probe = self.motor_service.create_probe()
            if action == "move":
                self._require_ppms_control_ready()
                if not self.ppms_motor_confirm_var.get():
                    raise RuntimeError(
                        "Please tick the motor-specific confirmation first and verify the decel code and mechanical safety"
                    )
                target = float(self.ppms_motor_target_var.get())
                speed = float(self.ppms_motor_speed_var.get())
                self.motor_service.pending = True
                self.motor_service.expected_angle_deg = target
                operation = lambda: self.motor_service.confirmed_manual_move(
                    probe,
                    target,
                    speed,
                    on_read=self._ppms_publish_motor_readback,
                    confirm_large_move=self._confirm_large_motor_move,
                    is_closed=lambda: self.shell.closing or self.state.closed,
                )
            elif action == "read":
                operation = probe.read
            elif action == "info":
                operation = probe.describe
            else:
                raise ValueError("Unknown motor debug action")
        except Exception as error:
            messagebox.showwarning("PPMS motor", str(error), parent=self.root)
            return

        self.state.environment_busy = True
        if action == "move":
            self.ppms_motor_status_var.set(
                "Verifying rotation and arrival; do not repeat the command"
            )
        self._set_ppms_environment_ui(True)
        future = self.state.ppms_executor.submit(operation)

        def finished() -> None:
            self.state.environment_busy = False
            try:
                result = future.result()
                if self.shell.closing:
                    return
                if action == "info":
                    self.shell.append_log("PPMS motor interface:\n" + result)
                    window = tk.Toplevel(self.root)
                    window.title("PPMS motor interface info (copyable)")
                    box = tk.Text(window, width=100, height=22, wrap="word")
                    box.pack(fill="both", expand=True)
                    box.insert("1.0", result)
                elif action == "read":
                    self.ppms_motor_status_var.set(
                        f"Angle {result['angle_deg']:.4f}°; DLL status: {result['status']}"
                    )
                    expected = (
                        self.motor_service.expected_angle_deg
                        if self.motor_service.expected_angle_deg is not None
                        else result["angle_deg"]
                    )
                    if result["status"] in ("AtTarget", "AtIndexSwitch") and (
                        not self.motor_service.pending
                        or abs(result["angle_deg"] - expected) <= 0.06
                    ):
                        self.motor_service.pending = False
                    if not self.ppms_motor_target_var.get():
                        self.ppms_motor_target_var.set(f"{result['angle_deg']:.4f}")
                else:
                    self.motor_service.pending = False
                    note = (
                        "; command reply uncertain, confirmed by angle and status readback"
                        if result["ambiguous"]
                        else ""
                    )
                    self.ppms_motor_status_var.set(
                        f"Angle confirmed: {result['angle_deg']:.4f}°; {result['status']} (two readbacks){note}"
                    )
                    self.ppms_angle_live_var.set(
                        f"{result['angle_deg']:.4f}° / {result['status']}"
                    )
            except PPMSScanCancelled as error:
                self.motor_service.pending = False
                self.ppms_motor_status_var.set(str(error))
            except Exception as error:
                if action == "move" and not getattr(
                    probe, "movement_attempted", False
                ):
                    self.motor_service.pending = False
                self.ppms_motor_status_var.set(
                    f"Debug failed: {short_status_error(error, 180)}"
                )
                if not self.shell.closing:
                    messagebox.showerror(
                        "PPMS motor debug", str(error), parent=self.root
                    )
            finally:
                if not self.shell.closing:
                    self._set_ppms_environment_ui(
                        self.state.ppms_environment_controller.connected
                    )

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    # --- Macro UI & MacroEventSink ---

    def open_ppms_macro(self) -> None:
        existing = self.ppms_macro_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        window = self.ppms_macro_window = tk.Toplevel(self.root)
        window.title("PPMS Macro")
        window.geometry("860x580")
        window.transient(self.root)
        window.rowconfigure(1, weight=1)
        window.columnconfigure(0, weight=1)
        ttk.Label(
            window,
            text="Executed line by line; TEMP K [K/min] [approach]; FIELD Oe Oe/s [approach mode]; WAIT seconds; SCAN\n"
            "Supports TMP TEMP / FLD FIELD / WAI WAITFOR seconds temperature field 0 0 0; REM comment. SCAN uses the main-window settings and asks for its run folder and file name (one run folder per SCAN).",
            wraplength=820,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        editor = self.ppms_macro_editor = tk.Text(window, wrap="none", undo=True)
        editor.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        scroll = ttk.Scrollbar(window, command=editor.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        editor.configure(yscrollcommand=scroll.set)
        editor.tag_configure("active_line", background="#fff0b0", foreground="black")
        editor.insert("1.0", self.ppms_macro_text)
        self.ppms_macro_status = tk.StringVar(
            value="Not running; Stop Macro stops the remaining commands, temperature/field targets already sent are kept."
        )
        ttk.Label(
            window, textvariable=self.ppms_macro_status, wraplength=820
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        bar = ttk.Frame(window)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=8)

        def validate():
            try:
                commands = self.macro_runner.validate(editor.get("1.0", "end-1c"))
                self.ppms_macro_status.set(
                    f"Validation passed: {len(commands)} commands; nothing sent to hardware."
                )
                return commands
            except ValueError as error:
                self.ppms_macro_status.set(str(error))
                return None

        def load():
            path = filedialog.askopenfilename(
                parent=window,
                filetypes=(("Sequence", "*.seq"), ("Text", "*.txt"), ("All", "*")),
            )
            if path:
                try:
                    data = Path(path).read_bytes()
                    try:
                        text = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        text = data.decode("gb18030")
                    editor.delete("1.0", "end")
                    editor.insert("1.0", text)
                    validate()
                except Exception as error:
                    self.ppms_macro_status.set(f"Load failed: {error}")

        def save():
            path = filedialog.asksaveasfilename(
                parent=window,
                defaultextension=".seq",
                filetypes=(("Sequence", "*.seq"),),
            )
            if path:
                try:
                    Path(path).write_text(
                        editor.get("1.0", "end-1c"), encoding="utf-8"
                    )
                except Exception as error:
                    self.ppms_macro_status.set(f"Save failed: {error}")

        def run():
            if self.macro_runner.running:
                return
            commands = validate()
            if commands is None:
                return
            try:
                self._require_ppms_control_ready()
                if self.state.environment_busy:
                    raise RuntimeError(
                        "Please wait for the current PPMS command to finish"
                    )
            except Exception as error:
                self.ppms_macro_status.set(str(error))
                return
            self.ppms_macro_text = editor.get("1.0", "end-1c")
            editor.configure(state="disabled")
            for button in self.ppms_macro_edit_buttons:
                button.configure(state="disabled")
            window.grab_set()
            self.macro_runner.start(
                commands,
                self,
                start_scan=self._macro_start_scan,
                is_scan_running=lambda: self.state.scan_running or self._macro_scan_pending,
                schedule_poll=self.root.after,
            )

        self.ppms_macro_edit_buttons = []
        for label, callback in (
            ("Load", load),
            ("Save", save),
            ("Validate", validate),
            ("Run", run),
        ):
            button = ttk.Button(bar, text=label, command=callback)
            button.pack(side="left", padx=2)
            self.ppms_macro_edit_buttons.append(button)
        ttk.Button(bar, text="Stop Macro", command=self.macro_runner.stop).pack(
            side="left", padx=2
        )

        def emergency():
            self.macro_runner.stop()
            self.ppms_sources_off()

        ttk.Button(
            bar, text="6221 / 2400 output off now", command=emergency
        ).pack(side="right")

        def close():
            if self.macro_runner.running:
                self.macro_runner.stop()
                return
            self.ppms_macro_text = editor.get("1.0", "end-1c")
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close)

    def _macro_start_scan(self) -> bool:
        self.start_ppms_field_scan()
        self._macro_scan_pending = self.state.scan_running
        return self.state.scan_running

    def on_macro_status(self, text: str) -> None:
        if self.ppms_macro_status is not None:
            self.ppms_macro_status.set(text)

    def on_macro_line(self, line: int, op: str, args: tuple) -> None:
        if self.ppms_macro_editor is not None:
            self.ppms_macro_editor.tag_remove("active_line", "1.0", "end")
            self.ppms_macro_editor.tag_add("active_line", f"{line}.0", f"{line}.end")
            self.ppms_macro_editor.see(f"{line}.0")

    def on_macro_temp_target(self, target_k: float, rate_k_min: float | None) -> None:
        self.ppms_temperature_target_var.set(str(target_k))
        if rate_k_min is not None:
            self.ppms_temperature_rate_var.set(f"{rate_k_min:g}")

    def on_macro_field_target(self, target_oe: float, owner: str) -> None:
        self.ppms_field_target_var.set(str(target_oe))
        self._end_ppms_persistent_follow_up(owner)

    def on_macro_readback(self, data: dict[str, object]) -> None:
        self._apply_ppms_environment_readback(data)

    def on_macro_busy(self, busy: bool) -> None:
        self.state.environment_busy = busy
        self._set_ppms_environment_ui(True)

    def on_macro_finished(self, message: str) -> None:
        if self.ppms_macro_status is not None:
            self.ppms_macro_status.set(message)
        if self.ppms_macro_editor is not None:
            self.ppms_macro_editor.configure(state="normal")
        for button in self.ppms_macro_edit_buttons:
            button.configure(state="normal")
        if self.ppms_macro_window is not None:
            try:
                self.ppms_macro_window.grab_release()
            except tk.TclError:
                pass

    def log(self, message: str) -> None:
        self.shell.thread_log(message)
        recorder = self.state.run_recorder
        if recorder is not None:
            try:
                recorder.event("note", text=str(message))
            except Exception:
                pass

    # --- Live Environment & Helium ---

    def _apply_ppms_environment_readback(self, result: dict[str, object]) -> None:
        result = dict(result)
        watch = result.pop("persistent_watch", None)
        if watch is not None:
            self.state.persistent_watch = dict(watch)
        self.state.last_readback = dict(result)
        if result.get("command_verification"):
            self.ppms_connection_var.set(result["command_verification"])
        self.ppms_temperature_var.set(f"{float(result['temperature_k']):.6g} K")
        self.ppms_temperature_status_var.set(f"Status: {result['temperature_status']}")
        self.ppms_field_var.set(f"{float(result['field_oe']):+.7g} Oe")
        self.ppms_field_status_var.set(f"Status: {result['field_status']}")
        self._follow_ppms_persistent_setpoint(result)
        self.ppms_angle_live_var.set(result.get("motor_display", "-- °"))
        self.ppms_current_live_var.set(
            result.get("current_display", "Unknown / not read")
        )
        self.ppms_pressure_live_var.set(
            result.get("pressure_display", "Unknown / not read")
        )
        self.ppms_helium_live_var.set(
            result.get("helium_display", "Unknown / not read")
        )
        self._draw_ppms_helium_history()
        self._check_ppms_helium_warning(result)
        for key, values in result.get("lockin_reference", {}).items():
            self.ppms_xy_vars[key].set(
                f"{PPMS_SR830_LABELS.get(key, key)}: {values}"
            )
            self.shell._set_connection_indicator(
                f"ppms_ref_{key}",
                str(values).endswith("LOCK reference locked"),
                fault="OVERLOAD" in str(values),
            )
            if key in self.ppms_sr830_status_vars:
                text = str(values)
                self.ppms_sr830_status_vars[key].set(
                    text if "OVERLOAD" in text else text.split(" / ")[-1]
                )

    def _read_ppms_current_display(self) -> str:
        source = self.state.ppms_instrument_controllers["6221"]
        self.state.k6221_zero_output = False
        try:
            with source.lock:
                if source.instrument is None:
                    return "Not connected"
                state = self.runner.query_numbers("6221", "OUTP?", 1)[0]
                if state == 0:
                    self.state.k6221_zero_output = True
                    self.state.idle_autorange_enabled = False
                    return (
                        "OFF / 0 mA; compliance hit in the last scan"
                        if self.state.last_current_trip
                        else "OFF / 0 mA"
                    )
                if state != 1:
                    raise ValueError(f"OUTP?={state:g}")
                peak = self.runner.query_numbers("6221", "SOUR:WAVE:AMPL?", 1)[0]
                self.state.k6221_zero_output = peak == 0.0
                if self.state.k6221_zero_output:
                    self.state.idle_autorange_enabled = False
                frequency = self.runner.query_numbers("6221", "SOUR:WAVE:FREQ?", 1)[0]
                condition = int(
                    self.runner.query_numbers("6221", "STAT:MEAS:COND?", 1)[0]
                )
                return (
                    "⚠ Compliance hit! Actual current may be below the setpoint; "
                    if condition & 8
                    else ""
                ) + f"ON / sine setpoint {peak * 1000 / math.sqrt(2):.6g} mA RMS / {frequency:g} Hz"
        except Exception as error:
            return f"Unknown / {short_status_error(error, 45)}"

    def _read_ppms_cryogen_display(self) -> dict[str, object]:
        result: dict[str, object] = {
            "pressure_display": "Unknown / not supported by interface",
            "helium_display": "Unknown / not supported by interface",
        }
        if self.state.ppms_environment_controller.backend != PPMS_QDINSTRUMENT_BACKEND:
            return result
        reader = PPMSMotorDebug(self.state.ppms_environment_controller.active)
        try:
            pressure = reader._read_item(19)
            if pressure < 0:
                raise ValueError("Negative pressure")
            result["pressure_display"] = f"{pressure:.5g} Torr"
        except Exception as error:
            result["pressure_display"] = f"Unknown / {short_status_error(error, 35)}"
        try:
            import System

            with reader.controller.lock:
                method = reader._method("SendPPMSCommand", 5)
                args = System.Array[System.Object](5)
                args[0], args[1], args[2] = "LEVEL?", "", ""
                args[3], args[4] = System.Int32(64), System.Double(2.0)
                code = method.Invoke(reader.controller.instrument, args)
                reader._check_return(method, code)
                if str(args[2] or "").strip():
                    raise RuntimeError(str(args[2]))
                values = [
                    float(v.strip()) for v in str(args[1]).strip().split(",")
                ]
                if (
                    len(values) != 2
                    or not all(math.isfinite(v) for v in values)
                    or not 0 <= values[0] <= 100
                    or values[1] not in (0, 1)
                ):
                    raise ValueError("LEVEL? reply format or range invalid")
                result["helium_display"] = f"{values[0]:.2f}%" + (
                    " (stale)" if values[1] == 0 else ""
                )
                result["helium_percent"] = values[0]
                result["helium_fresh"] = values[1] == 1
        except Exception as error:
            result["helium_display"] = f"Unknown / {short_status_error(error, 35)}"
        return result

    def _read_ppms_live(self) -> dict[str, object]:
        started = time.monotonic()
        data = self.state.ppms_environment_controller.read_environment()
        data["read_at"] = started
        data["current_display"] = self._read_ppms_current_display()
        data.update(self._read_ppms_cryogen_display())
        try:
            if (
                self.state.ppms_environment_controller.backend
                == PPMS_QDINSTRUMENT_BACKEND
            ):
                motor = PPMSMotorDebug(
                    self.state.ppms_environment_controller.active
                ).read()
                data["motor_display"] = (
                    f"{motor['angle_deg']:.4f}° / {motor['status']}"
                )
                data["angle_deg"] = motor["angle_deg"]
        except Exception as error:
            data["motor_display"] = f"Read failed: {short_status_error(error, 50)}"
        data["lockin_reference"] = {}
        for key in self._ppms_sr830_keys():
            try:
                if self.state.ppms_instrument_controllers[key].connected:
                    frequency = self.runner.query_numbers(key, "FREQ?", 1)[0]
                    status = self.runner._read_sr830_status(key, sink=self)
                    data["lockin_reference"][key] = f"{frequency:.5g} Hz / " + (
                        "WARNING unlocked"
                        if status & 8 or frequency <= 0
                        else "LOCK reference locked"
                    )
                    if status & 7:
                        data["lockin_reference"][key] = f"OVERLOAD / LIAS={status}"
                    if (
                        (
                            status & 7
                            or (
                                self.state.idle_autorange_enabled
                                and not self.state.k6221_zero_output
                            )
                        )
                        and not self.state.scan_running
                        and not self.shell.closing
                    ):
                        with self.state.ppms_instrument_controllers[key].lock:
                            if time.monotonic() >= self.state.idle_range_ready_at.get(
                                key, 0.0
                            ):
                                magnitude = (
                                    0.0
                                    if status & 7
                                    else self.runner.query_numbers(
                                        key, "OUTP? 3", 1
                                    )[0]
                                )
                                settling = self.runner._autorange_sr830(
                                    key, magnitude, status, sink=self
                                )
                                self.state.idle_range_ready_at[key] = max(
                                    self.state.idle_range_ready_at.get(key, 0.0),
                                    time.monotonic() + settling,
                                )
                                if settling:
                                    data["lockin_reference"][
                                        key
                                    ] += "; autorange, waiting to settle"
                else:
                    data["lockin_reference"][key] = "Not connected"
            except Exception as error:
                data["lockin_reference"][
                    key
                ] = f"Status unknown: {short_status_error(error, 50)}"
        return data

    def _update_ppms_helium_change(self, result: dict[str, object]) -> None:
        value = result.get("helium_percent")
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 100
            or result.get("helium_fresh") is not True
        ):
            self.ppms_helium_change_var.set(
                "Waiting for update (no valid new reading)"
            )
            return
        now = time.monotonic()
        if self.ppms_helium_history and now - self.ppms_helium_history[-1][0] < 10.0:
            return
        self.ppms_helium_history.append((now, float(value)))
        self._append_ppms_helium_csv(now, float(value))
        start, baseline = self.ppms_helium_history[0]
        duration = now - start
        self.ppms_helium_change_var.set(
            f"{float(value) - baseline:+.2f} points ({duration / 60:.1f} min recorded)"
        )

    def _append_ppms_helium_csv(self, recorded_at: float, value: float) -> None:
        try:
            path = self.ppms_helium_csv_path
            if path is None:
                frozen = getattr(sys, "frozen", False) or "__compiled__" in globals()
                directory = Path(
                    sys.argv[0] if frozen else _PROGRAM_FILE
                ).resolve().parent
                path = directory / PPMS_HELIUM_HISTORY_FILENAME
                if not path.exists() or path.stat().st_size == 0:
                    with path.open("w", newline="", encoding="utf-8-sig") as handle:
                        csv.writer(handle).writerow(
                            (
                                "timestamp_local",
                                "elapsed_s",
                                "panel",
                                "helium_percent",
                                "fresh",
                            )
                        )
                self.ppms_helium_csv_path = path
                self.ppms_helium_csv_started = recorded_at
                self.shell.thread_log(f"PPMS He history appended to: {path}")
            elapsed = recorded_at - self.ppms_helium_csv_started
            with path.open("a", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle).writerow(
                    (
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{elapsed:.3f}",
                        "PPMS",
                        f"{value:.6g}",
                        1,
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            self.ppms_helium_csv_error = False
        except OSError as error:
            if not self.ppms_helium_csv_error:
                self.ppms_helium_csv_error = True
                self.shell.thread_log(f"He history CSV save failed: {error}")
                messagebox.showwarning(
                    "He history save failed",
                    f"Cannot write to the program folder: {error}\nMeasurement continues; the in-memory He history is kept.",
                    parent=self.root,
                )

    def _draw_ppms_helium_history(self) -> None:
        from transport.ppms.ui.theme import nice_linear_ticks

        canvas = getattr(self, "ppms_helium_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 40:
            return
        now = time.monotonic()
        rows = list(self.ppms_helium_history)
        start = rows[0][0] if rows else now
        elapsed_minutes = max(1.0, (rows[-1][0] - start) / 60) if rows else 1.0
        _, x_max, x_ticks, _ = nice_linear_ticks(
            0.0, elapsed_minutes, target_intervals=4
        )
        left, right, top, bottom = 48, width - 12, 5, height - 27
        low, high, ticks, _ = (
            nice_linear_ticks(
                min(v for _t, v in rows),
                max(v for _t, v in rows),
                target_intervals=2,
            )
            if rows
            else (0.0, 100.0, [0, 50, 100], 50.0)
        )
        low, high = max(0.0, low), min(100.0, high)
        if high <= low:
            low, high = max(0.0, low - 1), min(100.0, high + 1)
        canvas.create_rectangle(left, top, right, bottom, outline="#555555")
        canvas.create_text(12, (top + bottom) / 2, text="He (%)", angle=90)
        canvas.create_text(
            (left + right) / 2,
            height - 8,
            text="Recording time (min)",
            font=("Arial", 8),
        )
        for minutes in x_ticks:
            if not 0 <= minutes <= x_max:
                continue
            x = left + minutes / x_max * (right - left)
            canvas.create_line(x, top, x, bottom, fill=THEME_COLORS["plot_grid"])
            canvas.create_text(
                x, bottom + 11, text=f"{minutes:g}", font=("Arial", 8)
            )
        for tick in ticks:
            if low <= tick <= high:
                y = bottom - (tick - low) / (high - low) * (bottom - top)
                canvas.create_line(left, y, right, y, fill=THEME_COLORS["plot_grid"])
                canvas.create_text(
                    left - 5, y, text=f"{tick:g}", anchor="e", font=("Arial", 8)
                )
        previous = None
        for t, v in rows:
            x = left + (t - start) / 60 / x_max * (right - left)
            y = bottom - (v - low) / (high - low) * (bottom - top)
            if previous is not None and t - previous[0] <= 300:
                canvas.create_line(
                    previous[1], previous[2], x, y, fill="#0072B2", width=1.5
                )
            else:
                canvas.create_oval(
                    x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill="#0072B2", outline=""
                )
            previous = (t, x, y)
        if not rows:
            canvas.create_text(
                (left + right) / 2,
                (top + bottom) / 2,
                text="Waiting for He level data",
                font=("Arial", 9),
            )

    def _check_ppms_helium_warning(self, result: dict[str, object]) -> None:
        value = result.get("helium_percent")
        if value is None:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value) or not 0 <= value <= 100:
            return
        fresh = result.get("helium_fresh") is True
        if value >= 70.0:
            if fresh:
                self.ppms_helium_warning_active = False
            return
        self.ppms_helium_live_var.set(
            f"{value:.2f}%" + (" (stale)" if not fresh else "") + " ⚠ below 70%"
        )
        if self.ppms_helium_warning_active or self.shell.closing:
            return
        self.ppms_helium_warning_active = True
        stale_note = (
            "\nNote: this is a stale reading; verify the actual helium level."
            if not fresh
            else ""
        )
        messagebox.showwarning(
            "PPMS helium level warning",
            f"PPMS He level is {value:.2f}%, below 70%. Please check the liquid helium level."
            f"{stale_note}\nThis warning does not stop the scan or change the field or current settings.",
            parent=self.root,
        )

    def connect_ppms_environment(self) -> None:
        if self.state.environment_busy:
            return
        try:
            backend = self.ppms_backend_var.get()
            host = self.ppms_host_var.get().strip()
            port = int(self.ppms_port_var.get())
            PPMSMultiVuReadbackController.validate_endpoint(host, port)
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("PPMS connection", str(error), parent=self.root)
            return
        self.state.environment_busy = True
        dll_path = self.ppms_qdinstrument_dll_var.get().strip()
        self.ppms_connection_var.set(f"Connecting to {host}:{port} via {backend}…")
        self._set_ppms_environment_ui(False)
        future = self.state.ppms_executor.submit(
            self.state.ppms_environment_controller.connect,
            backend,
            host,
            port,
            dll_path,
        )

        def finished() -> None:
            self.state.environment_busy = False
            try:
                result = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(
                    f"Connection failed: {short_status_error(error, 120)}"
                )
                self._set_ppms_environment_ui(False)
                if not self.shell.closing:
                    messagebox.showerror(
                        "PPMS connection failed", str(error), parent=self.root
                    )
                return
            self.ppms_connection_var.set("Connected")
            self._apply_ppms_environment_readback(result)
            self._set_ppms_environment_ui(True)

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def disconnect_ppms_environment(self) -> None:
        if self.state.environment_busy or self.state.scan_running:
            if self.state.scan_running and not self.shell.closing:
                messagebox.showwarning(
                    "PPMS disconnect",
                    "Click Stop/Zero first and wait for the field-scan thread to finish before disconnecting",
                    parent=self.root,
                )
            return
        self.state.environment_busy = True
        self.ppms_connection_var.set("Disconnecting PPMS client…")
        self._set_ppms_environment_ui(True)
        future = self.state.ppms_executor.submit(
            self.state.ppms_environment_controller.disconnect
        )

        def finished() -> None:
            self.state.environment_busy = False
            try:
                future.result()
            except BaseException as error:
                if not self.shell.closing:
                    messagebox.showerror(
                        "PPMS disconnect failed", str(error), parent=self.root
                    )
            self.ppms_temperature_var.set("-- K")
            self.ppms_temperature_status_var.set("Status: --")
            self.ppms_field_var.set("-- Oe")
            self.ppms_field_status_var.set("Status: --")
            self.state.last_readback = None
            self.state.persistent_watch = None
            self.ppms_control_safety_var.set(False)
            self._set_ppms_environment_ui(False)

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def read_ppms_environment_once(self) -> None:
        if (
            not self.state.ppms_environment_controller.connected
            or self.ppms_poll_inflight
        ):
            return
        self.ppms_poll_inflight = True
        future = self.state.ppms_executor.submit(self._read_ppms_live)

        def finished() -> None:
            self.ppms_poll_inflight = False
            try:
                result = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(
                    f"Read failed: {short_status_error(error, 120)}"
                )
                return
            self._apply_ppms_environment_readback(result)

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def _poll_ppms_helium_history(self) -> None:
        if (
            not self.state.ppms_environment_controller.connected
            or self.ppms_helium_poll_inflight
            or time.monotonic() < self.ppms_helium_poll_due
        ):
            return
        self.ppms_helium_poll_inflight = True
        try:
            future = self.state.ppms_executor.submit(self._read_ppms_cryogen_display)
        except RuntimeError:
            self.ppms_helium_poll_inflight = False
            return

        def finished():
            self.ppms_helium_poll_inflight = False
            self.ppms_helium_poll_due = time.monotonic() + 10.0
            if self.shell.closing or self.state.closed:
                return
            try:
                self._update_ppms_helium_change(future.result())
                self._draw_ppms_helium_history()
            except Exception as error:
                self.shell.thread_log(f"He trend read failed: {error}")

        future.add_done_callback(lambda _: self.shell.post_ui(finished))

    def _ppms_poll_timer(self) -> None:
        if self.shell.closing or self.state.closed:
            return
        self._poll_ppms_helium_history()
        if (
            self.state.ppms_environment_controller.connected
            and not self.state.environment_busy
            and not self.ppms_poll_inflight
            and not self.state.scan_running
        ):
            self.read_ppms_environment_once()
        try:
            self.ppms_poll_after_id = self.root.after(1000, self._ppms_poll_timer)
        except tk.TclError:
            self.ppms_poll_after_id = None

    # --- External Instruments (6221 / SR830 / 2400) ---

    def _set_ppms_device_ui(self, key: str, connected: bool) -> None:
        self.shell._set_connection_indicator(f"ppms_{key}", connected)
        if not connected and key in PPMS_SR830_KEYS:
            self.shell._set_connection_indicator(f"ppms_ref_{key}", False)
        scan_busy = bool(self.state.scan_running)
        self.ppms_device_connect_buttons[key].configure(
            state="disabled" if connected or scan_busy else "normal"
        )
        self.ppms_device_disconnect_buttons[key].configure(
            state="normal" if connected and not scan_busy else "disabled"
        )
        resource_widget = self.ppms_device_resource_widgets.get(key)
        if resource_widget is not None:
            if connected or scan_busy:
                resource_widget.configure(state="disabled")
            elif key in PPMS_SR830_KEYS and isinstance(resource_widget, ttk.Combobox):
                resource_widget.configure(state="readonly")
            else:
                resource_widget.configure(state="normal")

    def _initialize_ppms_sr830(
        self, key: str, harmonic: int, time_constant_code: int = 10
    ) -> None:
        if harmonic not in (1, 2, 3):
            raise ValueError("SR830 harmonic must be 1, 2 or 3")
        if time_constant_code not in STFMR_LOCKIN_SR830_TIME_CONSTANTS.values():
            raise ValueError(
                f"SR830 time constant code {time_constant_code!r} is not an 'SR830 TC' choice"
            )
        time_constant_code = int(time_constant_code)
        controller = self.state.ppms_instrument_controllers[key]
        configuration = (
            ("FMOD", 0),
            ("RSLP", 1),
            ("HARM", harmonic),
            ("ISRC", 1),
            ("IGND", 0),
            ("ICPL", 0),
            ("ILIN", 0),
            ("SYNC", 0),
            ("SENS", 9),
            ("OFLT", time_constant_code),
            ("OFSL", 1),
            ("RMOD", 1),
            ("PHAS", 0),
        )
        self.state.sr830_time_constant_codes.pop(key, None)
        with controller.lock:
            controller.instrument.write("*CLS")
            for command, value in configuration:
                self.runner.write_sr830_checked(key, f"{command} {value}")
            for command in SR830_DISPLAY_XY_COMMANDS:
                self.runner.write_sr830_checked(key, command)
            for command, expected in configuration:
                actual = self.runner.query_numbers(key, f"{command}?", 1)[0]
                if actual != expected:
                    raise RuntimeError(
                        f"SR830 initialisation readback mismatch: {command}={actual}, expected {expected}"
                    )
        self.state.sr830_time_constant_codes[key] = time_constant_code
        self.state.sr830_range_states.pop(key, None)
        self.state.idle_range_ready_at.pop(key, None)

    def _connect_and_initialize_ppms_device(
        self, key: str, resource: str, harmonic=None, time_constant_code: int = 10
    ):
        controller = self.state.ppms_instrument_controllers[key]
        identity = controller.connect(resource)
        try:
            if key in PPMS_SR830_KEYS:
                self._initialize_ppms_sr830(key, harmonic, time_constant_code)
            elif key == "6221":
                self.runner.initialize_6221()
        except BaseException:
            controller.disconnect()
            raise
        return identity

    def connect_ppms_device(self, key: str) -> None:
        if self.state.scan_running:
            return
        resource = self.ppms_resource_vars[key].get().strip()
        harmonic = None
        time_constant_code = 10
        if key in PPMS_SR830_KEYS:
            try:
                address = ppms_sr830_address(resource)
                harmonic = self._ppms_harmonic_value(key, 0)
                if harmonic not in (1, 2, 3):
                    raise ValueError("SR830 harmonic must be 1, 2 or 3")
            except ValueError as error:
                messagebox.showerror(
                    "PPMS SR830 address", str(error), parent=self.root
                )
                return
            try:
                time_constant_code = self._ppms_sr830_time_constant_code()
            except ValueError as error:
                messagebox.showerror("PPMS SR830 TC", str(error), parent=self.root)
                return
            for other_key in self._ppms_sr830_keys():
                if other_key == key:
                    continue
                other = self.state.ppms_instrument_controllers[other_key]
                if not other.connected:
                    continue
                try:
                    other_address = ppms_sr830_address(other.resource_name)
                except ValueError:
                    other_address = None
                if other_address == address:
                    messagebox.showerror(
                        "PPMS SR830 address",
                        f"GPIB address {address} is already connected by {PPMS_SR830_LABELS[other_key]}; "
                        "please choose another address among 8, 9 and 10.",
                        parent=self.root,
                    )
                    return

        self.ppms_device_connect_buttons[key].configure(state="disabled")
        resource_widget = self.ppms_device_resource_widgets.get(key)
        if resource_widget is not None:
            resource_widget.configure(state="disabled")
        self.ppms_device_connection_vars[key].set(
            "Connecting and initialising…"
            if harmonic is not None
            else "Connecting…"
        )
        future = self.state.ppms_executor.submit(
            self._connect_and_initialize_ppms_device,
            key,
            resource,
            harmonic,
            time_constant_code,
        )
        if harmonic is not None:
            self.state.sr830_connecting.add(key)
            self._update_ppms_sr830_tc_state()

        def finished() -> None:
            self.state.sr830_connecting.discard(key)
            self._update_ppms_sr830_tc_state()
            try:
                identity = future.result()
            except BaseException as error:
                self.ppms_device_connection_vars[key].set(
                    f"Failed: {short_status_error(error, 80)}"
                )
                self._set_ppms_device_ui(key, False)
                if not self.shell.closing:
                    messagebox.showerror(
                        "PPMS external instruments", str(error), parent=self.root
                    )
                return
            self.shell.thread_log(f"PPMS {key} connected: {identity}")
            self.ppms_device_connection_vars[key].set("Connected")
            self._set_ppms_device_ui(key, True)
            self._redraw_ppms_transport_plot()

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def disconnect_ppms_device(self, key: str) -> None:
        if self.state.scan_running:
            return
        controller = self.state.ppms_instrument_controllers[key]
        self.ppms_device_disconnect_buttons[key].configure(state="disabled")
        self.ppms_device_connection_vars[key].set("Disconnecting…")
        future = self.state.ppms_executor.submit(controller.disconnect)

        def finished() -> None:
            try:
                future.result()
            except BaseException as error:
                if not self.shell.closing:
                    messagebox.showerror(
                        "PPMS external instruments", str(error), parent=self.root
                    )
            self.ppms_device_connection_vars[key].set("Not connected")
            self._set_ppms_device_ui(key, False)
            self._redraw_ppms_transport_plot()

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def ppms_sources_off(self) -> None:
        running_scan = self.state.run_recorder is not None
        if running_scan:
            self.state.scan_stop_button = "I off"
        self.state.idle_autorange_enabled = False
        self.state.scan_preserve_on_stop = False
        self.state.scan_stop_event.set()
        self.ppms_connection_var.set(
            "Turning outputs off and confirming by readback…"
        )

        def work() -> str:
            errors = []
            confirmed = []
            for key in ("6221", "2400"):
                try:
                    controller = self.state.ppms_instrument_controllers[key]
                    with controller.lock:
                        if controller.instrument is None:
                            continue
                        controller.safe_output_off()
                        state = self.runner.query_numbers(key, "OUTP?", 1)[0]
                        if state != 0:
                            raise RuntimeError(
                                f"{key} output is still ON (OUTP?={state:g})"
                            )
                        confirmed.append(key)
                except BaseException as error:
                    errors.append(f"{key}: {error}")
            if errors:
                raise RuntimeError(" | ".join(errors))
            return (
                " / ".join(confirmed) + " output OFF confirmed by readback"
                if confirmed
                else "No source meter connected; output state cannot be confirmed"
            )

        future = self.state.ppms_executor.submit(work)
        if running_scan and self.state.run_recorder is not None:
            self.state.run_recorder.event("stop_requested", button="I off")

        def finished() -> None:
            try:
                status = future.result()
            except BaseException as error:
                self.ppms_connection_var.set(
                    f"Output off not confirmed: {short_status_error(error, 120)}"
                )
                self.ppms_current_live_var.set("Unknown / output off not confirmed")
                if not self.shell.closing:
                    text = str(error)

                    def show() -> None:
                        if not self.shell.closing:
                            messagebox.showerror(
                                "PPMS source off", text, parent=self.root
                            )

                    self.shell.post_ui(show)
                return
            self.ppms_connection_var.set(status)
            self.ppms_current_live_var.set(
                "OFF / 0 mA" if status.startswith("6221") else "Not connected"
            )

        future.add_done_callback(lambda _future: self.shell.post_ui(finished))

    def _build_ppms_sr830_tc_choice(self, master) -> None:
        self.ppms_sr830_time_constant_var = tk.StringVar(
            value=PPMS_SR830_DEFAULT_TIME_CONSTANT
        )
        self.ppms_sr830_time_constant_applied = PPMS_SR830_DEFAULT_TIME_CONSTANT
        ttk.Label(master, text="SR830 TC").grid(row=0, column=1, padx=(12, 3))
        combo = ttk.Combobox(
            master,
            textvariable=self.ppms_sr830_time_constant_var,
            values=tuple(STFMR_LOCKIN_SR830_TIME_CONSTANTS),
            state="readonly",
            width=6,
        )
        combo.grid(row=0, column=2)
        combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._ppms_apply_sr830_time_constant(),
        )
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            combo.bind(sequence, lambda _event: "break")
        self.ppms_sr830_time_constant_combo = combo

    def _ppms_sr830_time_constant_code(self) -> int:
        label = str(self.ppms_sr830_time_constant_var.get()).strip()
        if label not in STFMR_LOCKIN_SR830_TIME_CONSTANTS:
            choices = ", ".join(STFMR_LOCKIN_SR830_TIME_CONSTANTS)
            raise ValueError(f"SR830 TC must be one of: {choices}")
        return STFMR_LOCKIN_SR830_TIME_CONSTANTS[label]

    def _ppms_sr830_time_constant_idle(self) -> bool:
        return not (
            self.state.environment_busy
            or self.state.scan_running
            or self.state.sr830_connecting
        )

    def _update_ppms_sr830_tc_state(self) -> None:
        combo = getattr(self, "ppms_sr830_time_constant_combo", None)
        if combo is not None:
            combo.configure(
                state="readonly" if self._ppms_sr830_time_constant_idle() else "disabled"
            )

    def _ppms_set_sr830_time_constant(self, key: str, code: int) -> None:
        controller = self.state.ppms_instrument_controllers[key]
        with controller.lock:
            self.state.sr830_time_constant_codes.pop(key, None)
            self.runner.write_sr830_checked(key, f"OFLT {code}")
            actual = int(round(self.runner.query_numbers(key, "OFLT?", 1)[0]))
            if actual != code:
                raise RuntimeError(
                    f"time constant readback mismatch: requested OFLT {code}, read {actual}"
                )
            self.state.sr830_time_constant_codes[key] = actual
            state = self.state.sr830_range_states.get(key)
            if state is not None:
                state.low_range_count = state.range_cooldown = 0
                state.low_range_peak = 0.0
                state.overload_floor = None
            self.state.idle_range_ready_at[key] = time.monotonic() + max(
                0.1, 5.0 * SR830_TIME_CONSTANTS_S[actual]
            )

    def _ppms_apply_sr830_time_constant(self) -> None:
        previous = self.ppms_sr830_time_constant_applied
        label = str(self.ppms_sr830_time_constant_var.get()).strip()
        if (
            label not in STFMR_LOCKIN_SR830_TIME_CONSTANTS
            or not self._ppms_sr830_time_constant_idle()
        ):
            self.ppms_sr830_time_constant_var.set(previous)
            self._update_ppms_sr830_tc_state()
            return
        code = STFMR_LOCKIN_SR830_TIME_CONSTANTS[label]
        controllers = self.state.ppms_instrument_controllers
        if not any(controllers[key].connected for key in self._ppms_sr830_keys()):
            self.ppms_sr830_time_constant_applied = label
            return
        self.state.environment_busy = True
        self._update_ppms_control_ui()

        def work():
            done, failures = [], []
            for key in self._ppms_sr830_keys():
                controller = controllers[key]
                try:
                    with controller.lock:
                        if not controller.connected:
                            continue
                        self._ppms_set_sr830_time_constant(key, code)
                    done.append(PPMS_SR830_LABELS[key])
                except Exception as error:
                    failures.append(f"{PPMS_SR830_LABELS[key]}: {error}")
            if failures:
                raise RuntimeError(" | ".join(failures))
            return done

        def finished(future):
            self.state.environment_busy = False
            self.ppms_sr830_time_constant_applied = label
            try:
                done = future.result()
                self.shell.thread_log(
                    f"PPMS SR830 TC {label} (OFLT {code}) set and confirmed by readback: "
                    + (", ".join(done) or "no SR830 connected any more")
                )
            except Exception as error:
                self.shell.thread_log(f"PPMS SR830 TC {label} update failed: {error}")
                if not self.shell.closing:
                    messagebox.showerror(
                        "SR830 TC update failed",
                        f"{error}\n\nChoose SR830 TC again to retry, or reconnect that SR830.",
                        parent=self.root,
                    )
            finally:
                self._update_ppms_control_ui()

        try:
            future = self.state.ppms_executor.submit(work)
        except Exception as error:
            self.state.environment_busy = False
            self.ppms_sr830_time_constant_var.set(previous)
            self._update_ppms_control_ui()
            messagebox.showerror(
                "SR830 TC update failed", str(error), parent=self.root
            )
            return
        future.add_done_callback(lambda f: self.shell.post_ui(lambda: finished(f)))

    def _ppms_apply_selected_harmonic(self, key: str) -> None:
        self._refresh_ppms2_plot_layout()
        if self.state.environment_busy or self.state.scan_running:
            return
        controller = self.state.ppms_instrument_controllers[key]
        if not controller.connected:
            return
        harmonic = self._ppms_harmonic_value(key, 0)
        if harmonic not in (1, 2, 3):
            return
        self.state.environment_busy = True
        self._update_ppms_control_ui()

        def work():
            with controller.lock:
                self.runner.write_sr830_checked(key, f"HARM {harmonic}")
                actual = int(self.runner.query_numbers(key, "HARM?", 1)[0])
                if actual != harmonic:
                    raise RuntimeError(
                        f"Harmonic readback mismatch: requested {harmonic}, actual {actual}"
                    )
                return actual

        def finished(future):
            self.state.environment_busy = False
            try:
                actual = future.result()
                self.shell.thread_log(
                    f"{key} harmonic updated and confirmed by readback: {actual}"
                )
            except Exception as error:
                self.shell.thread_log(f"{key} harmonic update failed: {error}")
                if not self.shell.closing:
                    messagebox.showerror(
                        "SR830 harmonic update failed", str(error), parent=self.root
                    )
            finally:
                self._update_ppms_control_ui()

        try:
            future = self.state.ppms_executor.submit(work)
        except Exception as error:
            self.state.environment_busy = False
            self._update_ppms_control_ui()
            messagebox.showerror(
                "SR830 harmonic update failed", str(error), parent=self.root
            )
            return
        future.add_done_callback(lambda f: self.shell.post_ui(lambda: finished(f)))

    def _ppms_direct_transport_settings(self) -> dict[str, object]:
        if not self.state.ppms_instrument_controllers["6221"].connected:
            raise ValueError("Please connect the 6221")
        active, zurich = self._ppms_selected_lockins()
        addresses = [
            self.state.ppms_instrument_controllers[k].resource_name for k in active
        ]
        if len(set(addresses)) != len(addresses):
            raise ValueError("The SR830 addresses must differ")
        settings = validate_ppms_transport_scan_settings(
            self.ppms_transport_current_var.get(),
            self.ppms_transport_frequency_var.get(),
            self.ppms_transport_compliance_var.get(),
            self._ppms_harmonic_value("830_a"),
            self._ppms_harmonic_value("830_b"),
            active,
            allow_no_lockins=zurich,
            harmonic_c=self._ppms_harmonic_value("830_c"),
        )
        if any(
            settings[k] not in (1, 2, 3)
            for k in ("harmonic_a", "harmonic_b", "harmonic_c")
        ):
            raise ValueError("Harmonic must be 1, 2 or 3")
        return settings

    def _ppms_set_current_direct(self) -> None:
        if not self.state.scan_running:
            self.state.zurich_scan_enabled = False
        try:
            self._require_ppms_control_ready()
            settings = self._ppms_direct_transport_settings()
        except Exception as error:
            messagebox.showerror("Set current", str(error), parent=self.root)
            return
        if self.state.environment_busy:
            return
        self.state.scan_stop_event.clear()

        def operation():
            self.state.idle_autorange_enabled = False
            try:
                self.runner.configure_transport_scan(settings, sink=self)
                self.state.k6221_zero_output = settings["current_rms_ma"] == 0
                self.state.zurich_autorange_pending = settings["current_rms_ma"] != 0
                self.state.idle_range_ready_at = {}
                for state in self.state.sr830_range_states.values():
                    state.low_range_count = state.range_cooldown = 0
                    state.low_range_peak = 0.0
                self.state.idle_autorange_enabled = (
                    settings["current_rms_ma"] != 0
                    and not self.state.scan_stop_event.is_set()
                )
            except BaseException:
                self.runner._stop_transport_source(sink=self)
                raise

        self._run_ppms_command(
            "Setting 6221 AC current…",
            operation,
            success_text=(
                f"6221 output ON confirmed by readback; {settings['current_rms_ma']:g} mA RMS / "
                f"{settings['frequency_hz']:g} Hz; external reference confirmed"
            ),
        )

    # --- Zurich MFLI UI ---

    def _set_zurich_busy(self, busy: bool) -> None:
        self.state.zurich_busy = busy
        self.shell._set_connection_indicator(
            "zurich_connection", self.state.zurich_controller.mfli_connected
        )
        if not self.state.zurich_controller.mfli_connected:
            self.shell._set_connection_indicator("zurich_reference", False)
        locked = busy or self.state.scan_running
        self.zurich_select.configure(state="disabled" if locked else "normal")
        self.zurich_connect_button.configure(state="disabled" if locked else "normal")
        self.zurich_disconnect_button.configure(
            state="normal"
            if not locked and self.state.zurich_controller.mfli_connected
            else "disabled"
        )
        for entry in self.zurich_entries:
            entry.configure(
                state="disabled" if locked else widget_idle_state(entry)
            )
        self.zurich_entries[0].configure(
            state="disabled"
            if locked or self.state.zurich_controller.mfli_connected
            else widget_idle_state(self.zurich_entries[0])
        )
        for combo in (self.zurich_reference_combo, self.zurich_harmonic_combo):
            combo.configure(state="disabled" if locked else "readonly")

    def _zurich_needs_md_text(self, harmonic: str) -> str | None:
        controller = self.state.zurich_controller
        if (
            "+" not in str(harmonic)
            or self.state.zurich_has_md
            or not controller.mfli_connected
        ):
            return None
        device = str(controller.mfli_device or "This Zurich").upper()
        return f"{device} has no MF-MD option: it reads one harmonic at a time. Choose Zurich harmonic 1, 2 or 3 (1+2 and 1+2+3 need MF-MD, as on DEV4414)"

    def _apply_ppms2_zurich_harmonic(self) -> None:
        if (
            self.state.closed
            or self.shell.closing
            or self.state.scan_running
        ):
            return
        self._refresh_ppms2_plot_layout()
        if self.state.zurich_busy:
            self.root.after(100, self._apply_ppms2_zurich_harmonic)
            return
        if self.state.zurich_controller.mfli_connected:
            self.connect_ppms2_zurich(apply_only=True)

    def connect_ppms2_zurich(self, *, apply_only: bool = False) -> None:
        if (
            self.state.zurich_busy
            or self.state.closed
            or self.state.scan_running
        ):
            return
        try:
            device, servers = zurich_mfli_choice(self.zurich_resource.get())
            reference = self.zurich_reference.get()
            harmonic = str(self.zurich_harmonic.get())
            tau = float(self.zurich_timeconstant.get())
            frequency = float(self.ppms_transport_frequency_var.get())
            if (
                not device
                or harmonic not in ("1", "2", "3", "1+2", "1+2+3")
                or not math.isfinite(tau)
                or tau <= 0
                or not math.isfinite(frequency)
                or frequency <= 0
            ):
                raise ValueError(
                    "Please check the device name, harmonic, time constant and the PPMS reference frequency"
                )
        except (ValueError, tk.TclError) as error:
            messagebox.showerror(
                "PPMS Zurich settings", str(error), parent=self.root
            )
            return
        if not apply_only:
            self.state.zurich_controller.mfli_server_candidates = servers
        self._set_zurich_busy(True)
        self.zurich_status.set(
            "Updating harmonic and verifying configuration…"
            if apply_only
            else "Connecting and configuring…"
        )
        self.shell._set_connection_indicator("zurich_reference", False)
        one_order: dict[str, str] = {}

        def work():
            nonlocal harmonic
            controller = self.state.zurich_controller
            try:
                if not apply_only:
                    controller.connect_mfli(device)
                elif not controller.mfli_connected:
                    raise RuntimeError("Zurich is disconnected; harmonic not updated")
                with controller.lock:
                    multi = controller.mfli_has_multi_demod()
                    self.state.zurich_has_md = multi
                    if not multi and harmonic in ("1+2", "1+2+3"):
                        one_order["was"], harmonic = harmonic, "1"
                    controller.configure_mfli(
                        "AC",
                        1 if harmonic in ("1+2", "1+2+3") else int(harmonic),
                        "R",
                        reference_source=reference,
                        sample_rate_hz=NONLOCAL_SAMPLE_HZ,
                        input_impedance="10 MΩ",
                        input_scaling=1.0,
                        timeconstant_s=tau,
                        external_reference_demodulator=3 if multi else None,
                    )
                    if harmonic in ("1+2", "1+2+3"):
                        controller.configure_second_harmonic(tau)
                    if harmonic == "1+2+3":
                        controller.configure_third_harmonic(tau)
                    controller.disable_unused_ppms_demods(harmonic)
            except Exception:
                if not apply_only:
                    controller.disconnect_mfli()
                raise

        try:
            future = self.state.zurich_executor.submit(work)
        except Exception as error:
            self._set_zurich_busy(False)
            self.zurich_status.set(
                f"Configuration not started: {short_status_error(error, 90)}"
            )
            return

        def finished():
            if self.state.closed or self.shell.closing:
                return
            try:
                future.result()
                self.state.zurich_expected_frequency = frequency
                if one_order:
                    self.zurich_harmonic.set(harmonic)
                    self.shell.thread_log(
                        f'PPMS Zurich {device.upper()} has no MF-MD option: it reads one harmonic at a time, so harmonic {one_order["was"]} was changed to {harmonic} (its reference on Demod 2, as on the Non-local pages)'
                    )
                self.zurich_status.set(
                    f"Connected; harmonic {harmonic} applied; AC / 10 MΩ"
                    + ("; no MF-MD: one harmonic" if one_order else "")
                )
                self.shell.thread_log(
                    f"PPMS Zurich harmonic {harmonic} applied and configuration verified"
                )
            except Exception as error:
                self.zurich_status.set(
                    f"Connect/configure failed: {short_status_error(error, 90)}"
                )
                self.zurich_values.set("Reading unavailable")
                self.shell.thread_log(
                    f"PPMS Zurich configuration failed: {error}"
                )
                if not self.shell.closing:
                    try:
                        messagebox.showerror(
                            "Zurich harmonic update failed"
                            if apply_only
                            else "PPMS Zurich connect / configure failed",
                            str(error),
                            parent=self.root,
                        )
                    except Exception:
                        pass
            if one_order:
                try:
                    self._refresh_ppms2_plot_layout()
                except Exception as error:
                    self.shell.thread_log(
                        f"PPMS plot layout not refreshed after the harmonic change: {error}"
                    )
            self._set_zurich_busy(False)

        future.add_done_callback(lambda _: self.shell.post_ui(finished))

    def disconnect_ppms2_zurich(self) -> None:
        if (
            self.state.zurich_busy
            or self.state.closed
            or self.state.scan_running
        ):
            return
        self._set_zurich_busy(True)
        future = self.state.zurich_executor.submit(
            self.state.zurich_controller.disconnect_mfli
        )

        def finished():
            if self.state.closed or self.shell.closing:
                return
            try:
                future.result()
                self.zurich_status.set("Not connected")
            except Exception as error:
                self.zurich_status.set(
                    f"Disconnect failed: {short_status_error(error, 90)}"
                )
            self.zurich_values.set(
                "X: -- V    Y: -- V    R: -- V    θ: -- °    Ref: -- Hz"
            )
            self._set_zurich_busy(False)

        future.add_done_callback(lambda _: self.shell.post_ui(finished))

    def _display_zurich_sample(self, sample: dict[str, object]) -> None:
        self.zurich_values.set(
            f"X: {sample['X']:.7g} V    Y: {sample['Y']:.7g} V    R: {sample['R']:.7g} V    θ: {sample['phase_deg']:.4f}°    Ref: {sample['reference_frequency_hz']:.4f} Hz"
        )
        self.zurich_status.set(
            ("LOCK" if sample["reference_valid"] else "WARNING")
            + "; "
            + str(sample["status_text"])
        )
        self.shell._set_connection_indicator(
            "zurich_reference",
            bool(sample["reference_valid"]),
            fault=bool(sample.get("input_overload")),
        )
        self.zurich_range_var.set(
            "Autorange " + format_voltage_range(sample.get("input_range_v"))
        )

    def _poll_ppms2_zurich(self) -> None:
        if self.state.closed or self.shell.closing:
            return
        if (
            not self.state.zurich_busy
            and not self.state.zurich_reading
            and not self.state.scan_running
            and self.state.zurich_controller.mfli_connected
        ):
            try:
                expected = float(self.ppms_transport_frequency_var.get())
                if math.isfinite(expected) and expected > 0:
                    self.state.zurich_expected_frequency = expected
            except (ValueError, tk.TclError):
                pass
            self.state.zurich_reading = True

            def read():
                with self.state.zurich_controller.lock:
                    return self.runner.read_zurich_ranged(
                        self.state.zurich_expected_frequency, sink=self
                    )

            future = self.state.zurich_executor.submit(read)

            def finished():
                if self.state.closed or self.shell.closing:
                    return
                try:
                    sample = future.result()
                    self._display_zurich_sample(sample)
                except Exception as error:
                    self.zurich_status.set(
                        f"Read failed: {short_status_error(error, 80)}"
                    )
                    self.shell._set_connection_indicator("zurich_reference", False)
                    self.zurich_values.set(
                        "Reading unavailable (stale values hidden)"
                    )
                self.state.zurich_reading = False

            future.add_done_callback(lambda _: self.shell.post_ui(finished))
        self.zurich_after_id = self.root.after(1000, self._poll_ppms2_zurich)

    # --- Scan Start / Stop / Save / Plot ---

    @staticmethod
    def _ppms_frequency_set_up_s(frequencies, pass_settles, acquisition_s) -> float:
        total = 0.0
        for index, (settle_s, _tc, _meter) in enumerate(pass_settles):
            for frequency in frequencies:
                total += float(acquisition_s) + max(
                    0.0,
                    NONLOCAL_FSCAN_SETTLE_PERIODS / float(frequency) - float(settle_s),
                )
                if index:
                    total += PPMS_SET_UP_ALLOWANCE_S
        return total

    def _apply_ppms_mode_acquisition_default(self, mode: str) -> None:
        shown = getattr(
            self, "ppms_acquisition_default_shown", f"{PPMS_ACQUISITION_S:g}"
        )
        seconds = (
            PPMS_TEMPERATURE_ACQUISITION_S
            if mode == "Temperature"
            else PPMS_ACQUISITION_S
        )
        new = f"{seconds:g}"
        if str(self.ppms_acquisition_time_var.get()).strip() == shown:
            self.ppms_acquisition_time_var.set(new)
            self.ppms_acquisition_default_shown = new
        else:
            self.ppms_acquisition_default_shown = None

    def _ppms_field_read_at_start(self) -> float:
        try:
            field = float((self.state.last_readback or {})["field_oe"])
        except (KeyError, TypeError, ValueError):
            field = math.nan
        if not math.isfinite(field):
            raise ValueError(
                "No PPMS field reading yet: wait for the live reading, then start the scan again (this mode records the field read at Start as its target_field_oe)"
            )
        return field

    def _ppms_run_settings(
        self,
        mode: str,
        sequence,
        rate_oe_s: float,
        stable_s: float,
        approach: int,
        transport_settings: dict[str, object],
        pass_settles,
        settle_s: float,
        estimated_s: float,
        fixed_field: float,
        scan_field: bool,
    ) -> dict[str, object]:
        def box(variable):
            try:
                return variable.get()
            except Exception:
                return None

        resources = {}
        resource_vars = getattr(self, "ppms_resource_vars", {})
        for key, controller in self.state.ppms_instrument_controllers.items():
            typed = (
                box(resource_vars[key])
                if key in resource_vars
                else None
            )
            resources[key] = dict(
                label=str(getattr(controller, "label", key)),
                resource=str(getattr(controller, "resource_name", "") or typed or ""),
                connected=bool(getattr(controller, "connected", False)),
                identity=str(getattr(controller, "identity", "") or ""),
            )
        zurich = self.state.zurich_controller
        resources["zurich"] = dict(
            label="Zurich MFLI",
            resource=str(box(getattr(self, "zurich_resource", None)) or ""),
            connected=bool(getattr(zurich, "mfli_connected", False)),
            identity=str(getattr(zurich, "mfli_identity", "") or ""),
        )
        passes = transport_settings.get("harmonic_passes", ())
        scan = {
            key: value
            for key, value in transport_settings.items()
            if key != "harmonic_passes"
        }
        return dict(
            mode=mode,
            scan=scan,
            field=dict(
                commanded=bool(scan_field),
                sequence_oe=[point[3] for point in sequence],
                rate_oe_s=rate_oe_s,
                approach_code=approach,
                target_field_oe=(None if scan_field else fixed_field),
                note=(
                    "the scan sets each field, Driven"
                    if scan_field
                    else "not commanded; target_field_oe is the field read at Start"
                ),
            ),
            timing=dict(
                settle_s=stable_s,
                acquire_s=transport_settings.get("acquisition_s"),
                effective_settle_s=settle_s,
                settle_time_constants=LOCKIN_SETTLE_TAUS,
                per_pass=[
                    dict(
                        harmonic_pass=index,
                        effective_settle_s=wait,
                        slowest_tc_s=tc,
                        slowest_meter=meter,
                    )
                    for index, (wait, tc, meter) in enumerate(pass_settles, 1)
                ],
                estimated_s=estimated_s,
            ),
            harmonic_passes=[
                dict(
                    harmonic_pass=config.get("harmonic_pass", index),
                    harmonics={
                        PPMS_SR830_LABELS.get(key, key): config.get(
                            PPMS_SR830_HARMONIC_SETTING_KEYS.get(key, "")
                        )
                        for key in config.get("active_lockins", ())
                    },
                )
                for index, config in enumerate(passes, 1)
            ],
            zurich=(
                dict(
                    harmonics=self.state.zurich_scan_harmonic,
                    reference=self.state.zurich_scan_reference,
                    time_constant_s=self.state.zurich_scan_tau,
                )
                if self.state.zurich_scan_enabled
                else dict(used=False)
            ),
            sr830_time_constant_codes=dict(self.state.sr830_time_constant_codes),
            ending=(
                "every end zeroes the 6221 (read back); the temperature and the angle are kept; "
                + (
                    "Stop keeps the field, I off / a field failure / closing set it to 0 Oe"
                    if scan_field
                    else "the field is not commanded"
                )
            ),
            environment=dict(
                backend=str(
                    getattr(self.state.ppms_environment_controller, "backend", "")
                ),
                last_reading=dict(self.state.last_readback or {}),
            ),
            resources=resources,
            safety_confirmed=bool(box(getattr(self, "ppms_control_safety_var", None))),
        )

    def start_ppms_field_scan(self) -> None:
        if self.state.scan_running or self.state.environment_busy:
            return
        if self.ppms_lockin_selection["zurich"].get() and self.state.zurich_busy:
            self.ppms_field_scan_status_var.set(
                "Zurich is connecting; start the scan later"
            )
            return
        self.state.zurich_scan_enabled = bool(
            self.ppms_lockin_selection["zurich"].get()
        )
        self.state.zurich_scan_configured = False
        if self.state.zurich_scan_enabled:
            try:
                self.state.zurich_scan_harmonic = str(self.zurich_harmonic.get())
                self.state.zurich_scan_reference = self.zurich_reference.get()
                self.state.zurich_scan_tau = float(self.zurich_timeconstant.get())
                if (
                    self.state.zurich_scan_harmonic
                    not in ("1", "2", "3", "1+2", "1+2+3")
                    or not math.isfinite(self.state.zurich_scan_tau)
                    or self.state.zurich_scan_tau <= 0
                ):
                    raise ValueError(
                        "Please check the Zurich harmonic and time constant"
                    )
                problem = self._zurich_needs_md_text(self.state.zurich_scan_harmonic)
                if problem:
                    raise ValueError(problem)
            except (ValueError, tk.TclError) as error:
                messagebox.showerror(
                    "PPMS Zurich parameters", str(error), parent=self.root
                )
                return

        self.state.scan_stop_event.clear()
        try:
            self._require_ppms_control_ready()
            mode = self.ppms_simple_mode.get()
            if mode not in PPMS_SCAN_MODES:
                raise ValueError("Please select a valid scan mode")

            def number(variable, label):
                raw = str(variable.get()).strip()
                if not raw:
                    raise ValueError(
                        f"Please fill in {label}; the live reading on the right does not replace the setpoint on the left"
                    )
                try:
                    value = float(raw)
                except ValueError:
                    raise ValueError(
                        f"{label} must be a number, current input: {raw!r}"
                    ) from None
                if not math.isfinite(value):
                    raise ValueError(f"{label} must be a finite value")
                return value

            plan = plan_ppms_scan_axes(
                mode,
                tuple(
                    number(
                        v,
                        f"frequency {label} (Hz)"
                        if mode == "Frequency"
                        else f"scan {label}",
                    )
                    for v, label in zip(
                        self.ppms_simple_range[:2]
                        if mode == "Frequency"
                        else self.ppms_simple_range,
                        ("start", "end")
                        if mode == "Frequency"
                        else ("start", "end", "step"),
                    )
                ),
                outer_field_range=tuple(
                    number(v, f"outer field {label} (Oe)")
                    for v, label in zip(
                        self.ppms_outer_field_scan_range, ("start", "end", "step")
                    )
                )
                if mode == "Angle+Field"
                else None,
                temperature_range=tuple(
                    number(v, f"outer temperature {label}")
                    for v, label in zip(
                        self.ppms_temperature_scan_range, ("start", "end", "step")
                    )
                )
                if mode
                in ("Field+Temperature", "Angle+Temperature", "Current+Temperature")
                else None,
                frequency_range=tuple(
                    number(v, f"outer frequency {label} (Hz)")
                    for v, label in zip(
                        self.ppms_frequency_scan_range, ("start", "end")
                    )
                )
                if mode == "Frequency+Current"
                else None,
            )
            values = plan.values
            frequencies = plan.frequencies
            current_sweep = plan.current_sweep
            scan_field = plan.scan_field
            field_outer = plan.field_outer
            field_commanded = plan.field_commanded
            outer_fields = plan.outer_fields
            temperatures = plan.temperatures
            fixed_field = (
                values[0]
                if scan_field
                else outer_fields[0]
                if field_outer
                else self._ppms_field_read_at_start()
            )
            fixed_temp = (
                temperatures[0]
                if temperatures is not None
                else number(
                    self.ppms_temperature_target_var,
                    "the fixed temperature on the left (K)",
                )
            )
            temperature_delay_s = 0.0
            if mode in (
                "Field+Temperature",
                "Angle+Temperature",
                "Current+Temperature",
            ):
                temperature_delay_s = number(
                    self.ppms_temperature_delay_var,
                    "wait after each temperature is reached (Wait s)",
                )
                if not 0 <= temperature_delay_s <= 86400:
                    raise ValueError(
                        "The wait after each temperature (Wait s) must be within 0–86400 s"
                    )
            fixed_angle = (
                values[0]
                if mode in ("Angle", "Angle+Temperature", "Angle+Field")
                else number(
                    self.ppms_motor_target_var, "the fixed angle on the left (°)"
                )
            )
            start_oe, end_oe = (
                (values[0], values[-1])
                if scan_field
                else (outer_fields[0], outer_fields[-1])
                if field_outer
                else (fixed_field, fixed_field)
            )
            stable_s = number(
                self.ppms_field_scan_stable_var, "settle time per point (s)"
            )
            acquisition_s = number(
                self.ppms_acquisition_time_var, "acquisition time per point (s)"
            )
            if not 0 < acquisition_s <= 86400:
                raise ValueError(
                    "Acquisition time must be greater than 0 and at most 86400 s"
                )
            rate_oe_s = number(self.ppms_field_rate_var, "field rate (Oe/s)")
            if scan_field:
                validate_ppms_field_command(start_oe, rate_oe_s)
                validate_ppms_field_command(end_oe, rate_oe_s)
            elif field_outer:
                for field in outer_fields:
                    try:
                        validate_ppms_field_command(field, rate_oe_s)
                    except ValueError as error:
                        raise ValueError(
                            f"Outer field {field:g} Oe: {error}"
                        ) from None
            else:
                validate_ppms_field_command(0.0, rate_oe_s)
            if not math.isfinite(stable_s) or not 0 <= stable_s <= 86400:
                raise ValueError(
                    "PPMS settle time per point must be within 0–86400 s"
                )
            fields = (
                values
                if scan_field
                else outer_fields
                if field_outer
                else (fixed_field,)
            )
            if scan_field:
                fields = fields + fields[-2::-1]
            sequence = tuple(
                (1, i, len(fields), value) for i, value in enumerate(fields, 1)
            )
            approach = self._ppms_field_approach_code(
                self.ppms_field_approach_var.get()
            )
            if not self.state.ppms_instrument_controllers["6221"].connected:
                raise RuntimeError(
                    "A PPMS field scan must measure synchronously; connect the Keithley 6221 first"
                )
            active_lockins, use_zurich = self._ppms_selected_lockins()
            addresses = [
                ppms_sr830_address(
                    self.state.ppms_instrument_controllers[key].resource_name
                )
                for key in active_lockins
            ]
            if len(addresses) != len(set(addresses)):
                raise RuntimeError("SR830 slots cannot share a GPIB address")
            transport_settings = validate_ppms_transport_scan_settings(
                values[0] if current_sweep else self.ppms_transport_current_var.get(),
                frequencies[0]
                if frequencies
                else self.ppms_transport_frequency_var.get(),
                self.ppms_transport_compliance_var.get(),
                self._ppms_harmonic_value("830_a"),
                self._ppms_harmonic_value("830_b"),
                active_lockins,
                allow_no_lockins=use_zurich,
                harmonic_c=self._ppms_harmonic_value("830_c"),
            )
            nested = {
                "angle": values
                if mode in ("Angle", "Angle+Temperature", "Angle+Field")
                else (fixed_angle,),
                "temperature": temperatures
                if temperatures is not None
                else (fixed_temp,),
            }
            currents = (
                values if current_sweep else (transport_settings["current_rms_ma"],)
            )
            for current in currents:
                validate_ppms_transport_scan_settings(
                    current,
                    transport_settings["frequency_hz"],
                    transport_settings["compliance_v"],
                    transport_settings["harmonic_a"],
                    transport_settings["harmonic_b"],
                    active_lockins,
                    allow_no_lockins=use_zurich,
                    harmonic_c=transport_settings["harmonic_c"],
                )
            if (
                self.state.ppms_environment_controller.backend
                != PPMS_QDINSTRUMENT_BACKEND
                or not self.ppms_motor_confirm_var.get()
            ):
                raise ValueError(
                    "Angle scans need QDInstrument and the motor-specific safety confirmation"
                )
            if any(not -370 <= a <= 370 for a in nested["angle"]):
                raise ValueError("Angle range is limited to −370 to 370°")
            temp_rate = float(self.ppms_temperature_rate_var.get())
            for t in nested.get("temperature", ()):
                ppms_scan_temperature_segments(
                    float((self.state.last_readback or {}).get("temperature_k", 300)),
                    t,
                    temp_rate,
                )
            total = (
                len(sequence)
                * len(nested["angle"])
                * len(nested["temperature"])
                * len(currents)
            )
            harmonic_passes = self._ppms_harmonic_passes(transport_settings)
            total *= len(harmonic_passes)
            if frequencies:
                total *= len(frequencies)
            if total > 100000:
                raise ValueError(
                    "Nested scans are limited to 100000 measurement points"
                )
            if frequencies:
                for frequency in frequencies:
                    for config in harmonic_passes:
                        try:
                            validate_ppms_transport_scan_settings(
                                config["current_rms_ma"],
                                frequency,
                                config["compliance_v"],
                                config["harmonic_a"],
                                config["harmonic_b"],
                                config["active_lockins"],
                                allow_no_lockins=bool(
                                    config.get("allow_no_lockins", False)
                                ),
                                harmonic_c=config["harmonic_c"],
                            )
                        except ValueError as error:
                            raise ValueError(
                                f"At {frequency:g} Hz: {error}"
                            ) from None
                transport_settings["frequencies"] = tuple(frequencies)
            if any(
                transport_settings[k] not in (1, 2, 3)
                for k in ("harmonic_a", "harmonic_b")
            ):
                raise ValueError("SR830 harmonic must be 1, 2 or 3")
            transport_settings["nested"] = nested
            if field_outer:
                transport_settings["outer_fields"] = tuple(outer_fields)
            transport_settings["currents"] = currents
            transport_settings["scan_mode"] = mode
            transport_settings["acquisition_s"] = acquisition_s
            transport_settings["temperature_rate"] = temp_rate
            transport_settings["temperature_delay_s"] = temperature_delay_s
            transport_settings[
                "temperature_approach"
            ] = self._ppms_temperature_approach_code(
                self.ppms_temperature_approach_var.get()
            )
            slowdown = float(self.ppms_motor_speed_var.get())
            if not slowdown.is_integer() or not 0 <= slowdown <= 14:
                raise ValueError("Decel code must be an integer within 0–14")
            transport_settings["slowdown"] = int(slowdown)
            transport_settings["harmonic_passes"] = tuple(
                self._ppms_harmonic_passes(transport_settings)
            )
            sr830_tc_code = self._ppms_sr830_time_constant_code()
        except (ValueError, TypeError, RuntimeError, tk.TclError) as error:
            messagebox.showerror(
                "PPMS scan parameters", str(error), parent=self.root
            )
            return

        initial_field = start_oe
        if self.state.last_readback is not None:
            try:
                initial_field = float(self.state.last_readback["field_oe"])
            except (KeyError, TypeError, ValueError):
                pass
        pass_settles = [
            self.runner.lockin_settle(
                stable_s,
                settings["active_lockins"],
                use_zurich,
                fallback_code=sr830_tc_code,
            )
            for settings in harmonic_passes
        ]
        points_per_pass = total // max(1, len(harmonic_passes))
        blocks = len(nested["angle"]) * len(nested["temperature"]) * len(currents)
        field_ramp_s = 0.0
        if scan_field:
            previous = initial_field
            for _pass in harmonic_passes:
                for _block in range(blocks):
                    for _loop, _point, _count, target in sequence:
                        field_ramp_s += abs(target - previous) / rate_oe_s
                        previous = target
        rotator_moves = 0
        sweep_moves = 0
        return_moves = 0
        if field_outer:
            previous = initial_field
            for _pass in harmonic_passes:
                for _loop, _point, _count, target in sequence:
                    field_ramp_s += abs(target - previous) / rate_oe_s
                    previous = target
            angles = nested["angle"]

            def moves(start, end):
                return len(ppms_scan_axis(start, end, 0.9)) - 1

            try:
                present_angle = float(
                    (self.state.last_readback or {})["angle_deg"]
                )
            except (KeyError, TypeError, ValueError):
                present_angle = float(angles[0])
            sweeps = len(sequence) * len(harmonic_passes)
            sweep_moves = sum(moves(a, b) for a, b in zip(angles, angles[1:]))
            return_moves = moves(angles[-1], angles[0])
            rotator_moves = (
                moves(present_angle, angles[0])
                + sweeps * sweep_moves
                + (sweeps - 1) * return_moves
            )
            blocks *= len(sequence)
        estimated_seconds = field_ramp_s + sum(
            (settle + acquisition_s) * points_per_pass
            for settle, _tc, _meter in pass_settles
        )
        estimated_seconds += rotator_moves * PPMS_ROTATOR_MOVE_ALLOWANCE_S
        set_ups = blocks * len(harmonic_passes)
        if frequencies:
            blocks = (
                len(nested["angle"])
                * len(nested["temperature"])
                * len(frequencies)
            )
            set_ups = blocks
            freq_setup_s = sum(
                float(acquisition_s)
                + max(
                    0.0,
                    NONLOCAL_FSCAN_SETTLE_PERIODS / float(freq)
                    - float(settle_s_i),
                )
                + (PPMS_SET_UP_ALLOWANCE_S if idx else 0.0)
                for idx, (settle_s_i, _tc, _m) in enumerate(pass_settles)
                for freq in frequencies
            )
            estimated_seconds += (
                len(nested["angle"])
                * len(nested["temperature"])
                * freq_setup_s
            )
        estimated_seconds += 6.0 * total + PPMS_SET_UP_ALLOWANCE_S * set_ups
        previous_temperature = float(
            (self.state.last_readback or {}).get(
                "temperature_k", nested["temperature"][0]
            )
        )
        for _pass in harmonic_passes:
            for _angle in (
                (None,) if mode == "Angle+Temperature" else nested["angle"]
            ):
                for temperature in nested["temperature"]:
                    estimated_seconds += (
                        abs(temperature - previous_temperature) / temp_rate * 60.0
                    )
                    estimated_seconds += temperature_delay_s
                    previous_temperature = temperature
        estimate_text = format_scan_duration(estimated_seconds)
        rotation_note = ""
        try:
            present_angle = float((self.state.last_readback or {})["angle_deg"])
            turn = abs(float(nested["angle"][0]) - present_angle)
            if turn > 30.0:
                rotation_note = f"\n\nThe sample rotator turns {turn:.1f}° first ({present_angle:.1f}° → {float(nested['angle'][0]):.1f}°)."
        except (KeyError, TypeError, ValueError):
            pass
        settle_s, slowest_tc_s, slowest_meter = max(
            pass_settles, key=lambda item: item[0]
        )
        settle_text = ppms_lockin_settle_text(
            stable_s, settle_s, slowest_tc_s, slowest_meter
        )
        settle_line = f"Wait before each reading: {settle_text}"
        if temperature_delay_s:
            settle_line = (
                f"Wait after each temperature is stable: {format_scan_duration(temperature_delay_s)}, then the scan at it\n"
                + settle_line
            )
        later = sorted(
            {settle for settle, _tc, _meter in pass_settles} - {settle_s}
        )
        if later:
            settle_line += (
                "; harmonic passes with fewer lock-ins wait "
                + ", ".join(f"{settle:g}" for settle in later)
                + " s"
            )
        frequency_text = (
            ppms_frequency_count_text(frequencies) if frequencies else ""
        )
        frequency_line = ""
        if frequencies:
            frequency_line = (
                f"Frequencies: {frequency_text}"
                + (
                    " (Step not used)"
                    if mode == "Frequency"
                    else ", the whole current sweep at each"
                )
                + "\nAt each frequency the 6221 is switched off and set up again at it and the lock-ins' "
                "reference lock is awaited; the first point then waits as below, at least "
                f"{NONLOCAL_FSCAN_SETTLE_PERIODS:g} periods of the frequency, and takes agreeing windows"
                + (
                    "; the other currents change only the 6221 amplitude"
                    if mode == "Frequency+Current"
                    else ""
                )
                + f"\nHeld as in a Current scan: {fixed_temp:g} K (T K) and {fixed_angle:g}° (Angle °); "
                "the field is not commanded\n"
            )
        if field_outer:
            def step_text(points, unit):
                return (
                    f" (Step {abs(points[1] - points[0]):g}{unit})"
                    if len(points) > 1
                    else ""
                )

            angles = nested["angle"]
            ramp_step = (
                f"about {format_scan_duration(abs(sequence[1][3] - sequence[0][3]) / rate_oe_s)} per step; "
                if len(sequence) > 1
                else ""
            )
            frequency_line = (
                f"Outer fields: {len(sequence)} steps, {sequence[0][3]:g} to {sequence[-1][3]:g} Oe"
                f"{step_text([point[3] for point in sequence], ' Oe')}, the whole angle sweep at each\n"
                f"At each field: set Driven at {rate_oe_s:g} Oe/s ({ramp_step}about "
                f"{format_scan_duration(abs(sequence[0][3] - initial_field) / rate_oe_s)} to the first field "
                f"from {initial_field:g} Oe; {format_scan_duration(field_ramp_s)} of ramps in all), its "
                "stable reading awaited, then the angles; the first angle after each field change takes "
                "agreeing windows\n"
                f"Angles per field: {len(angles)}, {angles[0]:g} to {angles[-1]:g}°{step_text(angles, '°')}; "
                f"rotator: {sweep_moves} moves of at most 0.9° per sweep and {return_moves} back to "
                f"{angles[0]:g}° before each later field, {rotator_moves} moves in all (planned at "
                f"{PPMS_ROTATOR_MOVE_ALLOWANCE_S:g} s each)\n"
                f"Held as in an Angle scan: {fixed_temp:g} K (T K)\n"
            )
        if field_commanded:
            field_end = "the field stays at its setpoint after Stop, and is set to 0 Oe after I off, a field failure or closing the program"
        else:
            field_end = f"the field is not commanded (the CSV's target_field_oe is the field read now, {fixed_field:g} Oe)"
        end_line = f"\n\nAt every end (completed, stopped or failed) the 6221 is switched off and read back as off. The temperature and the angle stay where the scan left them; {field_end}."
        if not ask_scan_start(
            self.root,
            "PPMS scan time estimate",
            f"Scan mode: {mode}\n{frequency_line}Measurement points: {total}\n{settle_line}\n"
            f"Estimated duration: about {estimate_text}\n\n"
            "This is an estimate; the actual duration may grow with instrument settling and rotation waits."
            + rotation_note
            + end_line
            + "\n\nContinue to choose the run folder and start the scan?",
            f"about {estimate_text}",
            warning=rotation_note.strip(),
            icon="warning" if rotation_note else "question",
        ):
            return

        try:
            run_settings = self._ppms_run_settings(
                mode,
                sequence,
                rate_oe_s,
                stable_s,
                approach,
                transport_settings,
                pass_settles,
                settle_s,
                estimated_seconds,
                fixed_field,
                field_commanded,
            )
        except Exception as error:
            messagebox.showerror(
                "PPMS run log",
                f"Could not gather the settings for the run log; the scan was not started: {error}",
                parent=self.root,
            )
            return

        last_folder = self.state.run_folder
        initial_dir = None
        if last_folder is not None and Path(last_folder).parent.is_dir():
            initial_dir = Path(last_folder).parent
        if self.state.scan_stop_event.is_set():
            self.ppms_field_scan_status_var.set(
                f"{mode} scan not started: I off was pressed"
            )
            return
        recorder = begin_scan_run(
            self.root,
            "PPMS",
            "Select the folder for the PPMS run",
            initial_dir,
            run_settings,
        )
        if recorder is None:
            return
        if self.state.scan_stop_event.is_set():
            recorder.close(
                "not_started",
                stop_reason="I off was pressed before the scan started",
                points_saved=0,
            )
            self.state.run_folder = recorder.directory
            self.ppms_field_scan_status_var.set(
                f"{mode} scan not started: I off was pressed"
            )
            return
        self.state.run_recorder = recorder
        self.state.run_folder = recorder.directory
        self.shell.thread_log(
            f"PPMS {mode} scan; run folder: {recorder.directory} ({recorder.csv_path.name} and {recorder.log_path.name})"
        )
        self.shell.thread_log(f"PPMS {mode} scan: each reading waits {settle_text}")
        if frequencies:
            self.shell.thread_log(
                f"PPMS {mode} scan: {frequency_text}"
                + (
                    ", the whole current sweep at each"
                    if mode == "Frequency+Current"
                    else ""
                )
            )
        if field_outer:
            self.shell.thread_log(
                f"PPMS {mode} scan: {len(sequence)} outer fields, {sequence[0][3]:g} to {sequence[-1][3]:g} Oe at {rate_oe_s:g} Oe/s, the whole angle sweep at each"
            )

        self.state.scan_settle_logged = settle_s
        self.state.scan_sr830_tc_code = sr830_tc_code
        self.state.scan_running = True
        self.state.scan_preserve_on_stop = False
        self.state.scan_outputs = None
        self.state.scan_field_note = ""
        self.state.scan_stop_button = ""
        self.state.zero_persist_pending = False
        self.ppms_zero_persist_button.configure(
            text="Zero field + persist after scan"
        )
        self.state.active_scan_mode = mode
        self.ppms_plot_addresses = dict(zip(active_lockins, addresses))
        self.ppms_plot_axis = PPMS_SCAN_PLOT_AXES[mode]
        self.ppms_plot_angle_range = (
            (min(nested["angle"]), max(nested["angle"]))
            if self.ppms_plot_axis == "angle"
            else None
        )
        self.state.scan_rows = []
        self._redraw_ppms_transport_plot()
        self.state.scan_started_at = time.monotonic()
        self.state.scan_total = total
        self.ppms_field_scan_progress.configure(maximum=max(1, total), value=0)
        self.ppms_field_scan_eta_var.set(f"Remaining: about {estimate_text}")
        self.ppms_field_scan_status_var.set(
            f"{mode} scan: "
            + (f"{frequency_text}; " if frequencies else "")
            + (
                f"{len(sequence)} fields x {len(nested['angle'])} angles; "
                if field_outer
                else ""
            )
            + f"{total} points, settle {ppms_settle_status_text(settle_s)} s; "
            f"estimated about {estimate_text}"
        )
        self.ppms_transport_status_var.set(
            "Transport acquisition: configuring the 6221 and the connected SR830…"
        )
        self._update_ppms_control_ui()

        request = ScanRequest(
            mode=mode,
            sequence=sequence,
            rate_oe_s=rate_oe_s,
            stable_s=stable_s,
            approach_code=approach,
            transport_settings=transport_settings,
            total_points=total,
            estimated_seconds=estimated_seconds,
            sr830_tc_code=sr830_tc_code,
            zurich_enabled=self.state.zurich_scan_enabled,
            zurich_harmonic=self.state.zurich_scan_harmonic,
            zurich_reference=self.state.zurich_scan_reference,
            zurich_tau=self.state.zurich_scan_tau,
        )
        self.state.close_handoff.begin()
        try:
            self.state.ppms_executor.submit(self.runner.run, request, self)
        except Exception as error:
            self.state.close_handoff.end()
            self.state.scan_running = False
            self.runner.close_run(
                "not_started", sink=self, error=str(error), points_saved=0
            )
            self._update_ppms_control_ui()
            messagebox.showerror(
                "PPMS scan not started", str(error), parent=self.root
            )
            return

    def stop_ppms_field_scan(self) -> None:
        if not self.state.scan_running:
            return
        self.state.scan_preserve_on_stop = True
        if not self.state.scan_stop_button:
            self.state.scan_stop_button = "Stop"
        self.state.scan_stop_event.set()
        if self.state.run_recorder is not None:
            self.state.run_recorder.event("stop_requested", button="Stop")
        mode = self.state.active_scan_mode
        self.ppms_field_scan_status_var.set(
            f"Stopping the {mode} scan; completed CSV points kept; the 6221 is zeroed next; temperature, field and angle kept…"
        )
        self.ppms_field_scan_stop_button.configure(state="disabled")

    def _queue_ppms_zero_persist(self) -> None:
        if not self.state.scan_running:
            return
        if self.state.zero_persist_pending:
            self.state.zero_persist_pending = False
            self.ppms_zero_persist_button.configure(
                text="Zero field + persist after scan"
            )
            self.ppms_field_scan_status_var.set(
                "Zero field + persist after this scan: cancelled"
            )
            return
        self.state.zero_persist_pending = True
        self.ppms_zero_persist_button.configure(text="Queued: zero + persist")
        self.ppms_field_scan_status_var.set(
            "Queued: after this scan completes normally, the field is zeroed and switched to Persistent; click again to cancel"
        )

    def save_ppms_field_scan_csv(self) -> None:
        if not self.state.scan_rows:
            return
        folder = self.state.run_folder
        where = (
            {"initialdir": str(folder)}
            if folder is not None and Path(folder).is_dir()
            else {}
        )
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save PPMS scan CSV as",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
            initialfile=time.strftime("PPMS_field_scan_%Y%m%d_%H%M%S.csv"),
            **where,
        )
        if not selected:
            return
        fieldnames = list(self.state.scan_rows[0].keys())
        try:
            with open(selected, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.state.scan_rows)
        except OSError as error:
            messagebox.showerror("Save PPMS CSV", str(error), parent=self.root)
            return
        self.ppms_field_scan_status_var.set(f"Saved: {selected}")

    # --- ScanEventSink implementation ---

    def on_status(self, text: str) -> None:
        if not self.shell.closing:
            self.ppms_field_scan_status_var.set(text)

    def on_transport_status(self, text: str) -> None:
        if not self.shell.closing:
            self.ppms_transport_status_var.set(text)

    def on_eta(self, text: str) -> None:
        if not self.shell.closing:
            self.ppms_field_scan_eta_var.set(text)

    def on_progress(self, value: int) -> None:
        if not self.shell.closing:
            self.ppms_field_scan_progress.configure(value=value)

    def on_environment_readback(self, data: dict[str, object]) -> None:
        if not self.shell.closing:
            self._apply_ppms_environment_readback(data)

    def on_persistent_follow_up_ended(self, owner: str) -> None:
        if not self.shell.closing:
            self._end_ppms_persistent_follow_up(owner)

    def on_sr830_range(
        self,
        key: str,
        label: str,
        status: int,
        action: str,
        range_index: int | None,
    ) -> None:
        if self.shell.closing:
            return
        range_text = "Autorange " + format_sr830_full_scale(range_index)
        if key in self.ppms_sr830_range_vars:
            self.ppms_sr830_range_vars[key].set(range_text)
        if key in self.ppms_sr830_status_vars:
            self.ppms_sr830_status_vars[key].set(label)
        self.shell._set_connection_indicator(
            f"ppms_ref_{key}",
            not bool(status & 15) and not action,
            fault=bool(status & 7),
        )

    def on_zurich_sample(self, sample: dict[str, object]) -> None:
        if not self.shell.closing and not self.state.closed:
            self._display_zurich_sample(sample)

    def on_zurich_autorange(self, action_text: str) -> None:
        if not self.shell.closing and not self.state.closed:
            self.zurich_status.set(
                f"Autorange; waiting to settle: {action_text}"
            )
            self.shell._set_connection_indicator("zurich_reference", False)

    def on_compliance_trip(self) -> None:
        if not self.shell.closing:
            self.ppms_current_live_var.set(
                "⚠ Compliance hit! Scan stopped, turning output off"
            )

    def on_outputs_zeroed(self, results: dict[str, str]) -> None:
        if self.shell.closing:
            return
        outcome = results.get("6221", "")
        if outcome == "zeroed":
            self.ppms_transport_status_var.set(
                "Transport acquisition: 6221 output OFF (read back)"
            )
            self.ppms_current_live_var.set("OFF / 0 mA")
        elif outcome != "not connected":
            self.ppms_transport_status_var.set(
                f"Transport acquisition: 6221 {outcome[:120]}; switch it off"
            )

    def on_point_recorded(
        self,
        data: dict[str, object],
        progress: int,
        remaining_s: float,
        sample: dict[str, object],
    ) -> None:
        if self.shell.closing:
            return
        self._apply_ppms_environment_readback(data)
        self.ppms_field_scan_progress.configure(value=progress)
        self.ppms_field_scan_eta_var.set(
            f"Remaining: {format_scan_duration(remaining_s)}"
        )
        self.ppms_field_scan_status_var.set(
            f"CSV saved {progress}/{self.state.scan_total}; remaining {format_scan_duration(remaining_s)}"
        )
        addresses = sorted(
            {
                match.group(1)
                for name in sample
                if (match := re.fullmatch(r"sr830_(8|9|10)_r_v", str(name)))
            },
            key=int,
        )
        warning = ""
        if not all(
            bool(sample[f"sr830_{address}_reference_valid"])
            for address in addresses
        ):
            warning = "; reference WARNING"
        self.ppms_transport_status_var.set(
            "; ".join(
                f"#{address} "
                + (
                    "LOCK"
                    if sample[f"sr830_{address}_reference_valid"]
                    else "WARNING"
                )
                for address in addresses
            )
            + warning
        )
        self._redraw_ppms_transport_plot()

    def on_zero_persist_button_reset(self) -> None:
        if not self.shell.closing:
            self.ppms_zero_persist_button.configure(
                text="Zero field + persist after scan"
            )

    def capture_plot_snapshots(self, recorder, points: int) -> None:
        if (
            self.shell.closing
            or self.state.closed
            or recorder is None
            or recorder.closed
            or points <= 0
        ):
            return
        done = threading.Event()

        def capture():
            try:
                if not self.shell.closing and not self.state.closed:
                    save_scan_plot_snapshots(
                        self.root,
                        recorder,
                        (("plots", self.ppms_plot_frame),),
                        points=points,
                        redraw=self._redraw_ppms_transport_plot,
                        closing=self.shell.closing,
                        log_fn=self.shell.thread_log,
                    )
            finally:
                done.set()

        self.shell.post_ui(capture)
        done.wait(timeout=2.0)

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
    ) -> None:
        self.macro_runner.scan_result = outcome == "completed" and outputs_confirmed
        self._macro_scan_pending = False
        self._update_ppms_control_ui()
        self.ppms_field_scan_status_var.set(status)
        self.ppms_field_scan_eta_var.set(
            "Remaining: 00:00" if outcome == "completed" else "Remaining: --"
        )
        if self.shell.closing:
            return
        label = f"PPMS {mode} scan"
        zeroing = (
            f"At the end: 6221 {results['6221']}"
            if results and "6221" in results
            else "At the end: the 6221 was not confirmed zeroed"
        )
        if field_note and field_note not in str(error or ""):
            zeroing += f"; {field_note}"
        if outcome == "error":
            text = f"{error}\n\n{zeroing}"
            if not outputs_confirmed:
                text += "\n\nCHECK IT: the 6221 was not confirmed at zero."
            messagebox.showerror(label, text, parent=self.root)
        elif not outputs_confirmed:
            text = f"The scan {outcome}, but the 6221 was not confirmed at zero: CHECK IT.\n\n{zeroing}"
            messagebox.showerror("PPMS outputs", text, parent=self.root)
        if problems:
            text = f"The run log was not closed cleanly: {'; '.join(problems)}\n\nThe live CSV keeps every saved point."
            messagebox.showerror("PPMS run log", text, parent=self.root)

    # --- Plot layout & rendering ---

    def _ppms2_measured_orders(self) -> dict[int, list[str]]:
        orders: dict[int, list[str]] = {}
        for key in self._ppms_sr830_keys():
            variable = self.ppms_lockin_selection.get(key)
            if variable is None or not variable.get():
                continue
            for order in self._ppms_harmonic_orders(key):
                if order in PPMS_HARMONIC_ORDERS and key not in orders.setdefault(
                    order, []
                ):
                    orders[order].append(key)
        zurich = self.ppms_lockin_selection.get("zurich")
        if zurich is not None and zurich.get():
            mode = str(self.zurich_harmonic.get())
            for order in PPMS_ZURICH_MODE_ORDERS.get(mode, (1,)):
                orders.setdefault(order, []).append("zurich")
        return orders

    def _refresh_ppms2_plot_layout(self) -> None:
        frames = getattr(self, "ppms_harmonic_plot_frames", None)
        if not frames:
            return
        for frame in list(self.ppms_transport_plot_frames.values()) + list(
            self.zurich_plot_frames.values()
        ):
            frame.grid_remove()
        orders = self._ppms2_measured_orders()
        last_visible = max(
            (order for order in PPMS_HARMONIC_ORDERS if orders.get(order)),
            default=None,
        )
        for (order, _channel), canvas in self.ppms_harmonic_canvases.items():
            canvas.ppms_hide_x_labels = order != last_visible
        try:
            row_count = int(self.ppms_plot_frame.grid_size()[1])
        except (TypeError, ValueError, AttributeError, tk.TclError):
            row_count = 0
        for row in range(max(row_count, len(PPMS_HARMONIC_ORDERS) + 3)):
            self.ppms_plot_frame.rowconfigure(
                row, weight=0, minsize=0, uniform=""
            )
        row = 0
        for order in PPMS_HARMONIC_ORDERS:
            meters = orders.get(order, [])
            names = ", ".join(
                PPMS_SR830_LABELS.get(key, "Zurich") for key in meters
            )
            for column, channel in enumerate(("x", "y", "theta")):
                frame = frames[(order, channel)]
                if not meters:
                    frame.grid_remove()
                    continue
                frame.configure(
                    text=f"{PPMS_HARMONIC_LABELS[order]} {'θ' if channel == 'theta' else channel.upper()} — {names}"
                )
                frame.grid(row=row, column=column, sticky="nsew")
            if meters:
                self.ppms_plot_frame.rowconfigure(
                    row, weight=1, uniform="ppms_plot_rows"
                )
                row += 1
        for column in range(3):
            self.ppms_plot_frame.columnconfigure(
                column, weight=1, uniform="ppms_plots"
            )
        self._redraw_ppms_transport_plot()

    def _request_plot_redraw(self) -> None:
        if self.ppms_plot_redraw_queued:
            return
        self.ppms_plot_redraw_queued = True

        def run() -> None:
            self.ppms_plot_redraw_queued = False
            if self.shell.closing or self.state.closed:
                return
            self._redraw_ppms_transport_plot()

        try:
            self.root.after_idle(run)
        except (tk.TclError, AttributeError, RuntimeError):
            self.ppms_plot_redraw_queued = False
            self._redraw_ppms_transport_plot()

    def _ppms2_meter_address(self, meter: str) -> int | None:
        address = self.ppms_plot_addresses.get(meter)
        if address is None:
            try:
                address = ppms_sr830_address(
                    self.state.ppms_instrument_controllers[meter].resource_name
                )
            except (ValueError, AttributeError, KeyError):
                return None
        return address

    def _ppms2_prefix_resolver(self, meter: str, order: int):
        order_text = str(order)
        if meter != "zurich":
            address = self._ppms2_meter_address(meter)
            if address is None:
                return lambda row: None
            harmonic_key, prefix = f"sr830_{address}_harmonic", f"sr830_{address}_"
            return (
                lambda row: prefix
                if str(row.get(harmonic_key)) == order_text
                else None
            )
        demods = tuple(
            (
                f"zurich_demod{index}_harmonic",
                f"zurich_demod{index}_input",
                f"zurich_demod{index}_",
            )
            for index in range(1, 5)
        )
        default_mode = str(self.zurich_harmonic.get())
        order_prefix = f"zurich_demod{order}_"

        def resolve(row):
            for harmonic_key, input_key, prefix in demods:
                if (
                    str(row.get(harmonic_key)) == order_text
                    and str(row.get(input_key)) == "0"
                ):
                    return prefix
            mode = row.get("zurich_harmonic")
            mode = default_mode if mode is None else str(mode)
            if "+" in mode and order in PPMS_ZURICH_MODE_ORDERS.get(mode, ()):
                return order_prefix
            return "zurich_" if mode == order_text else None

        return resolve

    def _ppms_temperature_plot_colors(self, base: str, temperatures: dict):
        if self.state.active_scan_mode in ("Frequency+Current", "Angle+Field"):
            valid = {
                key: float(value)
                for key, value in temperatures.items()
                if isinstance(value, (int, float)) and math.isfinite(value)
            }
            ordered = sorted(set(valid.values()))
            rank = {value: index for index, value in enumerate(ordered)}
            span = len(ordered) - 1
            return {
                key: shade_color(base, rank[value] / span if span else 0.5)
                for key, value in valid.items()
            }
        if self.state.active_scan_mode not in (
            "Field+Temperature",
            "Angle+Temperature",
            "Current+Temperature",
        ):
            return {}
        valid = {
            k: float(v)
            for k, v in temperatures.items()
            if isinstance(v, (int, float)) and math.isfinite(v)
        }
        if not valid:
            return {}
        limits = list(valid.values())
        try:
            limits.extend(
                float(v.get()) for v in self.ppms_temperature_scan_range[:2]
            )
        except (AttributeError, TypeError, ValueError):
            pass
        limits = [v for v in limits if math.isfinite(v)]
        low, high = min(limits), max(limits)
        rgb = [int(base[i : i + 2], 16) for i in (1, 3, 5)]
        colors = {}
        for key, temperature in valid.items():
            fraction = (temperature - low) / (high - low) if high > low else 0.5
            light = 0.55 * (1 - fraction)
            dark = 0.25 * fraction
            values = [round((c + (255 - c) * light) * (1 - dark)) for c in rgb]
            colors[key] = "#" + "".join(f"{v:02x}" for v in values)
        return colors

    def _redraw_ppms_transport_plot(self) -> None:
        canvases = getattr(self, "ppms_harmonic_canvases", None)
        if not canvases:
            return
        axis = self.ppms_plot_axis
        x_key, x_label = {
            "field": ("measured_field_oe", "Measured magnetic field (Oe)"),
            "angle": ("measured_angle_deg", "Measured angle (°)"),
            "temperature": ("temperature_k", "Measured temperature (K)"),
            "current": ("6221_current_rms_ma", "Applied AC current (mA RMS)"),
            "frequency": ("6221_frequency_hz", "Frequency (Hz)"),
        }[axis]
        rows = self.state.scan_rows
        max_x = max(
            (
                abs(float(r[x_key]))
                for r in rows
                if isinstance(r.get(x_key), (int, float)) and math.isfinite(r[x_key])
            ),
            default=0.0,
        )
        scale = 1.0
        if axis == "field" and max_x >= 1000:
            scale, x_label = 1e-4, "Measured magnetic field (T)"
        x_extent = max_x * scale if axis == "field" else None
        orders = self._ppms2_measured_orders()
        by_frequency = self.state.active_scan_mode == "Frequency+Current"
        by_field = self.state.active_scan_mode == "Angle+Field"
        by_order: dict[int, list[tuple[str, tk.Canvas]]] = {}
        for (order, channel), canvas in canvases.items():
            by_order.setdefault(order, []).append((channel, canvas))
        prepared = []
        for row in rows:
            x_value = row.get(x_key)
            if not isinstance(x_value, (int, float)) or not math.isfinite(x_value):
                continue
            group = (
                f"A{row.get('angle_index', 1)}"
                if axis == "temperature"
                else f"T{row.get('temperature_index', 1)} ({row.get('target_temperature_k', '')} K)"
            )
            temperature = row.get("target_temperature_k")
            if by_frequency:
                freq = row.get("6221_frequency_hz")
                group = f"{freq:g} Hz" if isinstance(freq, (int, float)) else "? Hz"
                temperature = freq
            elif by_field:
                fld = row.get("target_field_oe")
                shown = f"{fld:g} Oe" if isinstance(fld, (int, float)) else "? Oe"
                group = f"H{row.get('outer_field_index', 1)} ({shown})"
                temperature = fld
            prepared.append(
                (row, f"{group} L{row.get('loop', 1)}", temperature, float(x_value) * scale)
            )
        for order, targets in by_order.items():
            columns = tuple(
                (channel, "theta_deg" if channel == "theta" else f"{channel}_v")
                for channel, _ in targets
            )
            series = {channel: {} for channel, _ in targets}
            colors = {channel: {} for channel, _ in targets}
            labels = {channel: {} for channel, _ in targets}
            markers = {channel: {} for channel, _ in targets}
            for meter in orders.get(order, []):
                meter_label = PPMS_SR830_LABELS.get(meter, "Zurich")
                base_color = PPMS_METER_COLORS.get(meter, "#666666")
                temperatures = {channel: {} for channel, _ in targets}
                resolve_prefix = self._ppms2_prefix_resolver(meter, order)
                for row, tail, temperature, point_x in prepared:
                    prefix = resolve_prefix(row)
                    if prefix is None:
                        continue
                    name = f"{meter_label} {tail}"
                    for channel, suffix in columns:
                        value = row.get(prefix + suffix)
                        if not isinstance(value, (int, float)) or not math.isfinite(
                            value
                        ):
                            continue
                        temperatures[channel][name] = temperature
                        series[channel].setdefault(name, []).append((point_x, value))
                for channel, _ in targets:
                    shaded = self._ppms_temperature_plot_colors(
                        base_color, temperatures[channel]
                    )
                    for name in temperatures[channel]:
                        colors[channel][name] = shaded.get(name, base_color)
                        labels[channel][name] = name
                        if meter == "zurich":
                            markers[channel][name] = "open_circle"
            for channel, canvas in targets:
                draw_nonlocal_series(
                    canvas,
                    series[channel],
                    x_label,
                    "Phase θ (°)" if channel == "theta" else f"{channel.upper()} voltage",
                    "Waiting for scan data",
                    markers=True,
                    auto_voltage_unit=channel != "theta",
                    symmetric_x_extent=x_extent,
                    show_legend=False,
                    angle_x_range=self.ppms_plot_angle_range
                    if axis == "angle"
                    else None,
                    series_colors=colors[channel],
                    series_labels=labels[channel],
                    series_markers=markers[channel],
                )

    def safe_close_ppms(self) -> None:
        if self.ppms_poll_after_id is not None:
            try:
                self.root.after_cancel(self.ppms_poll_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.ppms_poll_after_id = None
        if self.zurich_after_id is not None:
            try:
                self.root.after_cancel(self.zurich_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.zurich_after_id = None

        def field_rate() -> float:
            rate = float(self.ppms_field_rate_var.get())
            return rate

        def field_approach() -> int:
            return self._ppms_field_approach_code(self.ppms_field_approach_var.get())

        self.state.safe_close(
            log_warning=self.shell.append_log,
            field_rate_fn=field_rate,
            field_approach_fn=field_approach,
        )


__all__ = [
    "PPMSPanelView",
    "PPMS_HELIUM_HISTORY_FILENAME",
    "PPMS_SR830_HARMONIC_ATTRS",
    "PPMS_SR830_RESOURCE_OPTIONS",
]
