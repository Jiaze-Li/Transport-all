"""FROZEN LEGACY ARTIFACT (D1/M0 — scheduled for removal in Batch 2).

Formerly the host window and extraction spec for tools/extract_app.py.
Transport-PPMS (transport/ppms, transport/common, transport/core) is now the
sole authoritative source for PPMS; this file is not imported or executed.
"""
# What is extracted, and where to.
PAGE_CLASS = "PPMSPanel"
APP_NAME = "the PPMS page"
# This program's own version (user, 2026-11-27: "name PPMS, RT, MOKE, FMR exe all as v1.0"), not the
# main program's.  It is in the .exe's properties and in every results file, and it names the release
# ("Transport_PPMS v1.0"): raise it here, and in pyproject.toml, and push the tag ppms-v<version>, to start a new release.
APP_VERSION = "1.0"
OUTPUT = "transport/ppms/app.py"
GLUE_FILES = ("ppms_package_glue.py",)
# Everything named after the page is kept, used by kept code or not.
KEEP_PATTERNS = ("(?i)ppms",)

# Pages and parts of the main program the PPMS application does not have.
# A name here is left out even where kept code still names it.
DROP = (
    "RTEPanel", "create_motor_subsystem", "run_pco_dll_self_test",
    "TkCameraWindow", "CameraAcquisitionWorker", "CameraVideoRecorder", "AutoFocusManager",
    # The MOKE light page's LED controller and its methods of the main window
    # (kept by name only: connect, disconnect ... are also instrument methods).
    "LEDSerialController", "ChannelSettings", "LEDProtocolError", "set_connected_ui", "update_action_state",
    "set_busy", "run_task", "connect", "disconnect", "load_state", "query_channel", "query_all", "all_off",
)
# Stand-ins for dropped names that kept code tests with isinstance().
STUBS = {}
# Statements of the main window's __init__ that start other pages.
INIT_DROP = (
    "self.nonlocal_vi_mfli_device_var.trace_add(",
    "self.refresh_ports()",
    "self.set_connected_ui(",
    "self.update_stage_controls()",
    "self._update_magnet_controls()",
    "self.root.after(250, self._magnet_poll_timer)",
    "self.controller = LEDSerialController(",
    "self.port_var = ", "self.channel_vars", "self.action_widgets",
)
# Names kept whatever the reachability says (helpers only tests call).
KEEP = ("recycle_bin_available",)
# Names the transport package already has: imported, not copied.
PACKAGE_IMPORTS = {}
# Lines after the imports.  _PROGRAM_FILE stands for the main program's
# __file__ everywhere in the generated module: the entry script's place (the
# repository root, or the unpacked folder of the .exe), where the assets,
# DLLs kept beside the program and the helium history are looked for.
EXTRA_IMPORTS = (
    '_PROGRAM_FILE = str(Path(__MODULE_FILE__).resolve().parents[2] / "Transport_PPMS.py")',
    '# Installed with pip, the package is in site-packages: the folder the program is started in stands in.',
    'if not getattr(sys, "frozen", False) and {"site-packages", "dist-packages"} & set(Path(_PROGRAM_FILE).parts):',
    '    _PROGRAM_FILE = str(Path.cwd() / "Transport_PPMS.py")',
)
# Literal text of the generated module to replace.  Its own taskbar identity,
# so it does not group under the main program's button when both are open.
REPLACE = {
    "'NH.ProfChen.MagnetoElectroOpticalMicrowaveTransport'": "'NH.ProfChen.TransportPPMS'",
    (
        "    def safe_close_ppms(self):\n"
        "        if self.zurich_closed:\n"
        "            return\n"
        "        self.zurich_closed = True\n"
        "        if self.zurich_after_id is not None:\n"
        "            self.root.after_cancel(self.zurich_after_id)\n"
        "            self.zurich_after_id = None\n"
        "        self.zurich_executor.submit(self.zurich_controller.disconnect_mfli)\n"
        "        self.zurich_executor.shutdown(wait=False)\n"
        "        super().safe_close_ppms()"
    ): (
        "    def safe_close_ppms(self):\n"
        "        if self.zurich_closed:\n"
        "            return\n"
        "        self.zurich_closed = True\n"
        "        if self.zurich_after_id is not None:\n"
        "            try:\n"
        "                self.root.after_cancel(self.zurich_after_id)\n"
        "            except (AttributeError, tk.TclError):\n"
        "                pass\n"
        "            self.zurich_after_id = None\n"
        "        self.zurich_executor.shutdown(wait=False, cancel_futures=True)\n"
        "        super().safe_close_ppms()"
    ),
}

MAIN_WINDOW_TITLE = "Transport_PPMS_NH"
# Its own icon (Other-files/assets): the main program's picture in another colour, with the name on it.
APPLICATION_ICON_BASENAME = "Transport_PPMS"


class CombinedControlApp:
    def build_ui(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self._configure_ui_styles()
        notebook_pad = self._ui_px(3, 2)
        self.notebook = ttk.Notebook(self.root, padding=(notebook_pad, notebook_pad, notebook_pad, 0))
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.ppms_panel_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.ppms_panel_tab, text="PPMS")
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="Log")
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        self.ppms_panel = PPMSPanel(self, self.ppms_panel_tab)
        self.led_log_frame = ttk.LabelFrame(self.log_tab, text="Program communication log (PPMS / Zurich / instruments)", padding=5)
        self.led_log_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.led_log_frame.rowconfigure(0, weight=1)
        self.led_log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(self.led_log_frame, height=5, wrap="none", state="disabled", font=(self.mono_font_family, self.ui_small_font_size), background="#fbfcfd", relief="flat", padx=5, pady=4)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.led_log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(self.led_log_frame, orient="horizontal", command=self.log_text.xview)
        xscrollbar.grid(row=1, column=0, sticky="ew")
        self.log_text.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        self.log_display_paused = False
        self.log_pending_lines = []
        log_tools = ttk.Frame(self.led_log_frame)
        log_tools.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.log_pause_button = ttk.Button(log_tools, text="Pause refresh", command=self.toggle_log_refresh)
        self.log_pause_button.pack(side="left")
        ttk.Button(log_tools, text="Copy shown log", command=self.copy_displayed_log).pack(side="left", padx=5)
        ttk.Button(log_tools, text="Save full log", command=self.save_communication_log).pack(side="left")
        ttk.Label(log_tools, text="Pausing only freezes the display; logging continues and catches up on resume.").pack(side="left", padx=8)

    def _redraw_active_tab(self) -> None:
        # The main program redraws other pages' plots here, never the PPMS page's.
        return

    def safe_close_ppms(self) -> None:
        for name in ("rte_panel", "ppms_panel"):
            panel = self.__dict__.get(name)
            if panel is None:
                continue
            try:
                panel.safe_close_ppms()
            except BaseException as error:
                try:
                    self.append_log(f"{name} close warning: {error}")
                except BaseException:
                    pass
        if self.ppms_closed:
            return
        self.ppms_closed = True
        scan_was_running = self.ppms_field_scan_running
        self.ppms_field_scan_stop_event.set()
        recorder = self.__dict__.get("ppms_run_recorder")
        if recorder is not None:
            recorder.event("stop_requested", button="the program closing")
        if self.ppms_poll_after_id is not None:
            try:
                self.root.after_cancel(self.ppms_poll_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.ppms_poll_after_id = None
        def cleanup(after_worker: bool) -> None:
            errors = []
            if (scan_was_running and not after_worker
                    and getattr(self, "ppms_active_scan_mode", "") in PPMS_FIELD_COMMANDING_MODES
                    and not self.__dict__.get("ppms_scan_field_zero_accepted", False)
                    and self.ppms_environment_controller.connected):
                try:
                    rate = float(self.ppms_field_rate_var.get())
                    _target, rate = validate_ppms_field_command(0.0, rate)
                    approach = self._ppms_field_approach_code(self.ppms_field_approach_var.get())
                    self.ppms_environment_controller.set_field(0.0, rate, approach, 1)
                except BaseException as error:
                    errors.append(f"PPMS: zeroing the field before close failed: {error}")
            for controller in self.ppms_instrument_controllers.values():
                try:
                    controller.disconnect()
                except BaseException as error:
                    errors.append(str(error))
            try:
                self.ppms_environment_controller.disconnect()
            except BaseException as error:
                errors.append(str(error))
            zurich_executor = self.__dict__.get("zurich_executor")
            if zurich_executor is not None:
                try:
                    zurich_executor.shutdown(wait=True, cancel_futures=True)
                except BaseException as error:
                    errors.append(str(error))
            zurich_controller = self.__dict__.get("zurich_controller")
            if zurich_controller is not None:
                try:
                    zurich_controller.disconnect_mfli()
                except BaseException as error:
                    errors.append(str(error))
            for error in errors:
                try:
                    self.append_log(f"PPMS safe-close warning: {error}")
                except BaseException:
                    pass
        deferred = scan_was_running and self._ppms_close_handoff().defer(lambda: cleanup(True))
        self.ppms_executor.shutdown(wait=False, cancel_futures=not deferred)
        if not deferred:
            cleanup(False)

    def safe_close_hardware(self) -> None:
        try:
            self.safe_close_ppms()
        except BaseException as error:
            try:
                self.append_log(f"PPMS safe close incomplete: {error}")
            except BaseException:
                pass

    def request_close(self) -> None:
        if self.closing:
            return
        confirmed = messagebox.askyesno(
            "Close the PPMS program",
            "Exit the PPMS control program?\n\n"
            "The program will switch off the 6221 output and disconnect the external instruments. If a field scan "
            "is running, it requests field zeroing first; otherwise it disconnects the PPMS client and keeps the "
            "temperature and field setpoints.\n\n"
            "After exiting, still confirm every instrument output and power supply is in a safe state.",
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return
        self.closing = True
        panel = self.__dict__.get("ppms_panel")
        for owner in (self, panel):
            if owner is None:
                continue
            event = owner.__dict__.get("ppms_field_scan_stop_event")
            if event is not None:
                event.set()

        def finish_window_close() -> None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

        def close_everything() -> None:
            try:
                self.safe_close_hardware()
            finally:
                self.file_executor.shutdown(wait=False, cancel_futures=False)
                self.executor.shutdown(wait=False, cancel_futures=True)
                self.post_ui(finish_window_close)
        threading.Thread(target=close_everything, name="ppms-safe-shutdown", daemon=True).start()


def run() -> None:
    """The installed command (pyproject.toml: transport-ppms)."""
    main()


def main() -> None:
    root = tk.Tk()
    app = CombinedControlApp(root)
    root.after_idle(lambda: maximize_main_window(root))
    try:
        root.mainloop()
    finally:
        app.safe_close_hardware()
        app.executor.shutdown(wait=False, cancel_futures=True)
