"""Composition root (AppShell) for the standalone PPMS application."""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from transport.ppms.state import PPMSAppState
from transport.ppms.ui.dialogs import ConnectionOverview, use_english_tk_dialogs
from transport.ppms.ui.panel import PPMSPanelView
from transport.ppms.ui.theme import (
    APP_VERSION,
    MAIN_WINDOW_TITLE,
    THEME_COLORS,
    UI_QUEUE_ACTIVE_DELAY_MS,
    UI_QUEUE_IDLE_DELAY_MS,
    UI_QUEUE_MAX_CALLBACKS_PER_TICK,
    UI_QUEUE_TIME_BUDGET_S,
    apply_application_identity,
    apply_look_to_tree,
    calculate_window_layout,
    configure_button_roles,
    configure_tk_font_system,
    create_lamp,
    force_english_input_mode,
    geometry_string,
    lamp_state,
    set_lamp,
)


class AppShell:
    """Tk shell and composition root for Transport_PPMS."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(MAIN_WINDOW_TITLE)
        apply_application_identity(self.root)
        self.window_layout = calculate_window_layout(self.root)
        self.ui_scale = float(self.window_layout["ui_scale"])
        self.canvas_scale = float(self.window_layout["canvas_scale"])
        setattr(self.root, "_moke_ui_scale", self.ui_scale)
        self.root.geometry(geometry_string(self.window_layout["control"]))
        control_geometry = self.window_layout["control"]
        self.root.minsize(
            min(control_geometry[0], max(620, int(760 * self.ui_scale))),
            min(control_geometry[1], max(500, int(650 * self.ui_scale))),
        )
        screen_width, screen_height = self.window_layout["screen"]
        self.root.maxsize(screen_width, screen_height)
        self.root.bind_all("<FocusIn>", self._enforce_english_input_mode, add="+")
        self.root.after_idle(lambda: force_english_input_mode(self.root))

        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="led-io")
        self.file_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="file-export"
        )
        self.closing = False
        self.instrument_connection_indicators: dict[str, tuple[tk.Canvas, int]] = {}
        self.notebook: ttk.Notebook | None = None
        self.connection_overview = None

        self.state = PPMSAppState(self.thread_log)
        self.build_ui()
        self.notebook.bind(
            "<<NotebookTabChanged>>", self._on_notebook_tab_changed, add="+"
        )
        self._apply_commercial_look()
        self.root.after(20, self.drain_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)

    def _enforce_english_input_mode(self, event=None) -> None:
        target = getattr(event, "widget", self.root)
        try:
            self.root.after_idle(
                lambda selected=target: force_english_input_mode(selected)
            )
        except tk.TclError:
            pass

    def _ui_px(self, value: int | float, minimum: int = 1, *, canvas: bool = False) -> int:
        scale = self.canvas_scale if canvas else self.ui_scale
        return max(int(minimum), int(round(float(value) * scale)))

    @staticmethod
    def _pair_inset(container) -> int:
        raw = container.cget("padding") if "padding" in container.keys() else ()
        padding = [int(float(str(v))) for v in container.tk.splitlist(raw)] or [0]
        inset = padding[0] + (padding[2] if len(padding) > 2 else padding[0])
        if container.winfo_class() == "TLabelframe":
            inset += 4
        return inset

    def _create_connection_indicator(self, parent: tk.Misc, key: str) -> tk.Canvas:
        status = "ref" in key.split("_") or "reference" in key
        canvas, item = create_lamp(
            parent, kind="status" if status else "connection"
        )
        self.instrument_connection_indicators[key] = (canvas, item)
        return canvas

    def _set_connection_indicator(
        self, key: str, connected: bool, *, fault: bool = False
    ) -> None:
        set_lamp(
            self.instrument_connection_indicators.get(key),
            lamp_state(connected, fault),
        )

    def _configure_ui_styles(self) -> None:
        self.ui_font_family, self.mono_font_family = configure_tk_font_system(
            self.root
        )
        use_english_tk_dialogs(self.root)
        style = ttk.Style(self.root)
        if sys.platform == "darwin" and "clam" in style.theme_names():
            style.theme_use("clam")
        font_family = self.ui_font_family
        font_size = int(getattr(self.root, "_moke_ui_font_size", 10))
        small_font_size = int(getattr(self.root, "_moke_ui_small_font_size", 9))
        self.ui_font_size = font_size
        self.ui_small_font_size = small_font_size
        large_font_size = max(font_size + 3, int(round(15 * self.ui_scale)))
        px = self._ui_px
        colors = THEME_COLORS
        palette = {
            "background": colors["page"],
            "surface": colors["surface"],
            "text": colors["text"],
            "muted": colors["muted"],
            "border": colors["border"],
            "accent": colors["accent"],
            "accent_soft": "#e8f1f8",
            "success": colors["success"],
            "warning": colors["warning"],
            "status": colors["status"],
        }
        self.ui_palette = palette
        self.root.configure(background=palette["background"])
        style.configure(".", font=(font_family, font_size))
        style.configure("TFrame", background=palette["surface"])
        style.configure(
            "TLabel", background=palette["surface"], foreground=palette["text"]
        )
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                name, background=palette["surface"], foreground=palette["text"]
            )
            style.map(name, background=[("active", palette["surface"])])
        style.configure(
            "TLabelframe",
            background=palette["surface"],
            bordercolor=palette["border"],
            lightcolor=palette["surface"],
            darkcolor=palette["surface"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=palette["surface"],
            foreground=palette["text"],
            font=(font_family, font_size, "bold"),
        )
        style.configure(
            "TNotebook",
            background=palette["background"],
            borderwidth=0,
            tabmargins=(px(3, 2), px(3, 2), px(3, 2), 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(px(14, 7), px(7, 4)),
            font=(font_family, font_size),
            background=colors["tab"],
            foreground=palette["muted"],
            bordercolor=palette["border"],
            lightcolor=colors["tab"],
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", palette["surface"]),
                ("active", colors["button_active"]),
            ],
            foreground=[("selected", palette["accent"])],
            lightcolor=[("selected", palette["surface"])],
            font=[("selected", (font_family, font_size, "bold"))],
        )
        style.configure(
            "TButton",
            background=colors["button"],
            bordercolor=palette["border"],
            lightcolor=colors["button"],
            darkcolor=colors["button"],
        )
        style.map(
            "TButton",
            background=[
                ("disabled", colors["button"]),
                ("pressed", colors["tab"]),
                ("active", colors["button_active"]),
            ],
        )
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(
                name,
                fieldbackground=palette["surface"],
                bordercolor=palette["border"],
                lightcolor=palette["surface"],
                darkcolor=palette["surface"],
            )
            style.map(
                name,
                bordercolor=[("focus", palette["accent"])],
                lightcolor=[("focus", palette["accent"])],
            )
        style.configure(
            "Horizontal.TProgressbar",
            background=palette["accent"],
            troughcolor=colors["tab"],
            bordercolor=palette["border"],
            lightcolor=palette["accent"],
            darkcolor=palette["accent"],
        )
        style.configure("Overview.TFrame", background=palette["background"])
        style.configure(
            "Overview.TLabel",
            background=palette["background"],
            foreground=palette["muted"],
        )
        style.configure(
            "Tooltip.TLabel",
            background=palette["surface"],
            foreground=palette["text"],
            relief="solid",
            borderwidth=1,
            padding=(6, 4),
        )
        style.configure("TButton", padding=(px(9, 5), px(6, 3)))
        style.configure("Compact.TButton", padding=(px(6, 4), px(5, 3)))
        style.configure("Inline.TButton", padding=(px(9, 5), px(3, 2)))
        style.configure("TLabel", padding=(0, px(2, 1)))
        style.configure("TCombobox", padding=(px(5, 3), px(5, 3)))
        style.configure("TSpinbox", padding=(px(5, 3), px(5, 3)))
        style.configure("TEntry", padding=(px(5, 3), px(5, 3)))
        style.configure("TCheckbutton", padding=(px(4, 2), px(4, 2)))
        style.configure("TRadiobutton", padding=(px(4, 2), px(4, 2)))
        style.configure(
            "Strong.TButton",
            foreground=palette["accent"],
            font=(font_family, font_size, "bold"),
        )
        try:
            self.ui_button_roles = configure_button_roles(self.root, style)
        except Exception as error:
            self.ui_button_roles = {}
            print(f"Button styles not made: {error}", file=sys.stderr)
        style.configure(
            "Metric.TLabel",
            foreground=palette["text"],
            font=(font_family, font_size, "bold"),
        )
        style.configure(
            "LargeMetric.TLabel",
            foreground=palette["text"],
            font=(font_family, large_font_size, "bold"),
        )
        style.configure("Muted.TLabel", foreground=palette["muted"])
        style.configure("Warning.TLabel", foreground=palette["warning"])
        style.configure(
            "Hint.TLabel",
            background="#fff1a8",
            foreground="#6f4500",
            padding=(px(8, 4), px(4, 2)),
            font=(font_family, font_size, "bold"),
        )
        style.configure(
            "Success.TLabel",
            foreground=palette["success"],
            font=(font_family, font_size, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=palette["status"],
            foreground=palette["text"],
            padding=(px(8, 4), px(4, 2)),
            relief="flat",
        )

    def _apply_commercial_look(self, widget=None) -> None:
        if not isinstance(self.root, tk.Misc):
            return
        style = ttk.Style(self.root)
        apply_look_to_tree(widget or self.root, style)
        if widget is None and self.connection_overview is None:
            try:
                self.connection_overview = ConnectionOverview(
                    self.root, self.notebook, pad=self._ui_px(6, 4)
                )
            except Exception as error:
                self.connection_overview = None
                print(f"Connection overview not shown: {error}", file=sys.stderr)
            self.notebook.bind(
                "<<NotebookTabChanged>>", self._apply_look_to_shown_page, add="+"
            )

    def _apply_look_to_shown_page(self, _event=None) -> None:
        try:
            shown = self.notebook.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return
        apply_look_to_tree(shown, ttk.Style(self.root))

    def _on_notebook_tab_changed(self, _event=None) -> None:
        return

    def build_ui(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self._configure_ui_styles()
        notebook_pad = self._ui_px(3, 2)
        self.notebook = ttk.Notebook(
            self.root, padding=(notebook_pad, notebook_pad, notebook_pad, 0)
        )
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.ppms_panel_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.ppms_panel_tab, text="PPMS")
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="Log")
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        self.ppms_panel = PPMSPanelView(self, self.ppms_panel_tab, self.state)

        self.led_log_frame = ttk.LabelFrame(
            self.log_tab,
            text="Program communication log (PPMS / Zurich / instruments)",
            padding=5,
        )
        self.led_log_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.led_log_frame.rowconfigure(0, weight=1)
        self.led_log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.led_log_frame,
            height=5,
            wrap="none",
            state="disabled",
            font=(self.mono_font_family, self.ui_small_font_size),
            background="#fbfcfd",
            relief="flat",
            padx=5,
            pady=4,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            self.led_log_frame, command=self.log_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(
            self.led_log_frame, orient="horizontal", command=self.log_text.xview
        )
        xscrollbar.grid(row=1, column=0, sticky="ew")
        self.log_text.configure(
            yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set
        )
        self.log_display_paused = False
        self.log_pending_lines: list[str] = []
        log_tools = ttk.Frame(self.led_log_frame)
        log_tools.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.log_pause_button = ttk.Button(
            log_tools, text="Pause refresh", command=self.toggle_log_refresh
        )
        self.log_pause_button.pack(side="left")
        ttk.Button(
            log_tools, text="Copy shown log", command=self.copy_displayed_log
        ).pack(side="left", padx=5)
        ttk.Button(
            log_tools, text="Save full log", command=self.save_communication_log
        ).pack(side="left")
        ttk.Label(
            log_tools,
            text="Pausing only freezes the display; logging continues and catches up on resume.",
        ).pack(side="left", padx=8)

    def post_ui(self, callback: Callable[[], None]) -> None:
        self.ui_queue.put(callback)

    def thread_log(self, message: str) -> None:
        self.post_ui(lambda text=message: self.append_log(text))

    def drain_ui_queue(self) -> None:
        deadline = time.perf_counter() + UI_QUEUE_TIME_BUDGET_S
        processed = 0
        while processed < UI_QUEUE_MAX_CALLBACKS_PER_TICK:
            if processed and time.perf_counter() >= deadline:
                break
            try:
                if not self.root.winfo_exists():
                    break
            except tk.TclError:
                break
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except BaseException as error:
                if not self.closing:
                    print(f"Transport UI callback error: {error}", file=sys.stderr)
                    traceback.print_exc()
                    try:
                        self.append_log(f"Internal UI error: {error!r}")
                    except BaseException:
                        pass
            processed += 1
        try:
            if self.root.winfo_exists():
                delay_ms = (
                    UI_QUEUE_ACTIVE_DELAY_MS
                    if not self.ui_queue.empty()
                    else UI_QUEUE_IDLE_DELAY_MS
                )
                self.root.after(delay_ms, self.drain_ui_queue)
        except tk.TclError:
            pass

    def toggle_log_refresh(self) -> None:
        self.log_display_paused = not self.log_display_paused
        self.log_pause_button.configure(
            text="Resume refresh" if self.log_display_paused else "Pause refresh"
        )
        if not self.log_display_paused and self.log_pending_lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "".join(self.log_pending_lines))
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
            self.log_pending_lines.clear()

    def copy_displayed_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_text.get("1.0", "end-1c"))

    def save_communication_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save the full program log (including lines buffered while paused)",
            initialfile=time.strftime("Transport_log_%Y%m%d_%H%M%S.txt"),
            defaultextension=".txt",
            filetypes=(("Text", "*.txt"),),
        )
        if not path:
            return
        try:
            content = self.log_text.get("1.0", "end-1c") + "".join(
                self.log_pending_lines
            )
            with Path(path).open("w", encoding="utf-8-sig") as handle:
                handle.write(content)
        except OSError as error:
            messagebox.showerror("Save log failed", str(error), parent=self.root)

    def append_log(self, message: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.post_ui(lambda text=str(message): self.append_log(text))
            return
        try:
            if not self.root.winfo_exists() or not self.log_text.winfo_exists():
                return
        except (AttributeError, tk.TclError):
            return
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        if self.log_display_paused:
            self.log_pending_lines.append(line)
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            if not self.closing:
                raise

    def safe_close_hardware(self) -> None:
        try:
            self.ppms_panel.safe_close_ppms()
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
        self.state.scan_stop_event.set()
        self.state.macro_stop_event.set()

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

        threading.Thread(
            target=close_everything, name="ppms-safe-shutdown", daemon=True
        ).start()


def maximize_main_window(root: tk.Tk) -> None:
    try:
        root.state("zoomed")
    except tk.TclError:
        try:
            root.attributes("-zoomed", True)
        except tk.TclError:
            pass


def main() -> None:
    root = tk.Tk()
    app = AppShell(root)
    root.after_idle(lambda: maximize_main_window(root))
    try:
        root.mainloop()
    finally:
        app.safe_close_hardware()
        app.executor.shutdown(wait=False, cancel_futures=True)


def run() -> None:
    """The installed command (pyproject.toml: transport-ppms)."""
    main()


__all__ = [
    "APP_VERSION",
    "AppShell",
    "MAIN_WINDOW_TITLE",
    "main",
    "maximize_main_window",
    "run",
]
