"""Tkinter theme, font, lamp, window layout, and plot-canvas helpers."""

from __future__ import annotations

import ctypes
import math
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, ttk

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

APP_VERSION = "1.0"
MAIN_WINDOW_TITLE = "Transport_PPMS_NH"
APPLICATION_ICON_BASENAME = "Transport_PPMS"

_PROGRAM_FILE = str(Path(__file__).resolve().parents[3] / "Transport_PPMS.py")
if not getattr(sys, "frozen", False) and {"site-packages", "dist-packages"} & set(
    Path(_PROGRAM_FILE).parts
):
    _PROGRAM_FILE = str(Path.cwd() / "Transport_PPMS.py")


def program_directory() -> Path:
    frozen = getattr(sys, "frozen", False) or "__compiled__" in globals()
    return Path(sys.argv[0] if frozen else _PROGRAM_FILE).resolve().parent


def _application_asset_path(filename: str) -> Path | None:
    candidates: list[Path] = []
    for directory in (
        Path(_PROGRAM_FILE).resolve().parent,
        Path(sys.argv[0]).resolve().parent,
        Path(sys.executable).resolve().parent,
    ):
        candidates.extend((directory / "assets" / filename, directory / filename))
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def apply_application_identity(window: tk.Misc) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "NH.ProfChen.TransportPPMS"
            )
        except BaseException:
            pass
    icon_png = _application_asset_path(f"{APPLICATION_ICON_BASENAME}_icon.png")
    if icon_png is not None:
        try:
            icon_photo = tk.PhotoImage(master=window, file=str(icon_png))
            window.winfo_toplevel().iconphoto(True, icon_photo)
            setattr(
                window.winfo_toplevel(), "_transport_application_icon", icon_photo
            )
        except (OSError, tk.TclError):
            pass
    if sys.platform == "win32":
        icon_ico = _application_asset_path(f"{APPLICATION_ICON_BASENAME}.ico")
        if icon_ico is not None:
            try:
                window.winfo_toplevel().iconbitmap(default=str(icon_ico))
            except (OSError, tk.TclError):
                pass


UI_QUEUE_MAX_CALLBACKS_PER_TICK = 50
UI_QUEUE_TIME_BUDGET_S = 0.008
UI_QUEUE_ACTIVE_DELAY_MS = 1
UI_QUEUE_IDLE_DELAY_MS = 5
CONTROL_WIDTH_FRACTION = 0.43
WINDOW_GAP = 10
PAIR_FLIP_LIMIT = 8
SCAN_PLOT_SNAPSHOT_SETTLE_S = 0.05

THEME_COLORS = {
    "page": "#eef1f5",
    "surface": "#ffffff",
    "border": "#d5dbe3",
    "text": "#1f2933",
    "muted": "#5f6b76",
    "accent": "#0068b5",
    "accent_active": "#00589a",
    "accent_pressed": "#004a82",
    "danger": "#c62828",
    "danger_active": "#b71c1c",
    "danger_pressed": "#8e0000",
    "on_fill": "#ffffff",
    "disabled_fill": "#e0e0e0",
    "disabled_text": "#9e9e9e",
    "button": "#f5f7fa",
    "button_active": "#e8edf3",
    "tab": "#e2e7ee",
    "success": "#087a55",
    "warning": "#a65300",
    "status": "#eaf0f5",
    "plot_background": "#ffffff",
    "plot_border": "#cfd6dd",
    "plot_grid": "#e3e7eb",
    "plot_series": "#0068b5",
}
LAMP_SIZE_PX = 16
LAMP_DOT_PX = 10
LAMP_COLORS = {
    "on": ("#20b15a", "#10783a"),
    "off": ("#9aa0a6", "#6f757b"),
    "fault": ("#e53935", "#9d1d1a"),
    "warning": ("#f9a825", "#a86f00"),
}
BUTTON_BASE_STYLES = ("TButton", "Compact.TButton", "Inline.TButton")
BUTTON_ROLES = ("Accent", "Danger")
NATIVE_BUTTON_THEMES = ("vista", "xpnative", "winnative", "aqua")
BUTTON_ROLE_NEUTRAL_WORDS = ("recording",)
PPMS_HARMONIC_COLORS = {"1": "#0072B2", "2": "#E69F00", "3": "#009E73"}
PPMS_PLOT_ROW_MIN_HEIGHT_PX = 70
PPMS_METER_COLORS = {
    "830_a": "#0072B2",
    "830_b": "#E69F00",
    "830_c": "#009E73",
    "zurich": "#CC79A7",
}


def configure_tk_font_system(root: tk.Misc) -> tuple[str, str]:
    installed = tuple(tkfont.families(root))
    installed_lookup = {name.casefold(): name for name in installed}

    def choose(candidates: tuple[str, ...], fallback: str) -> str:
        for candidate in candidates:
            actual = installed_lookup.get(candidate.casefold())
            if actual is not None:
                return actual
        return fallback

    default_font = tkfont.nametofont("TkDefaultFont", root=root)
    fixed_font = tkfont.nametofont("TkFixedFont", root=root)
    default_family = str(default_font.actual("family"))
    fixed_family = str(fixed_font.actual("family"))
    if sys.platform.startswith("win"):
        ui_candidates = (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "DengXian",
            "Segoe UI",
        )
        mono_candidates = ("Cascadia Mono", "Consolas", "Courier New")
    elif sys.platform == "darwin":
        ui_candidates = ("PingFang SC", "Heiti SC", "Helvetica Neue")
        mono_candidates = ("SF Mono", "Menlo", "Monaco")
    else:
        ui_candidates = (
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "WenQuanYi Micro Hei",
            "DejaVu Sans",
        )
        mono_candidates = ("Noto Sans Mono CJK SC", "DejaVu Sans Mono")
    ui_family = choose(ui_candidates, default_family)
    mono_family = choose(mono_candidates, fixed_family)
    top_level = root.winfo_toplevel()
    ui_scale = float(getattr(top_level, "_moke_ui_scale", 1.0))
    normal_size = max(8, min(12, int(round(10 * ui_scale))))
    small_size = max(8, min(11, int(round(9 * ui_scale))))
    named_specs = {
        "TkDefaultFont": (ui_family, normal_size, "normal"),
        "TkTextFont": (ui_family, normal_size, "normal"),
        "TkMenuFont": (ui_family, normal_size, "normal"),
        "TkHeadingFont": (ui_family, normal_size, "bold"),
        "TkCaptionFont": (ui_family, normal_size, "normal"),
        "TkSmallCaptionFont": (ui_family, small_size, "normal"),
        "TkIconFont": (ui_family, normal_size, "normal"),
        "TkTooltipFont": (ui_family, small_size, "normal"),
        "TkFixedFont": (mono_family, small_size, "normal"),
    }
    available_named_fonts = set(tkfont.names(root))
    for font_name, (family, size, weight) in named_specs.items():
        if font_name not in available_named_fonts:
            continue
        tkfont.nametofont(font_name, root=root).configure(
            family=family, size=size, weight=weight
        )
    root.option_add("*Font", "TkDefaultFont", "widgetDefault")
    root.option_add("*Menu.Font", "TkMenuFont", "widgetDefault")
    setattr(top_level, "_moke_ui_font_family", ui_family)
    setattr(top_level, "_moke_mono_font_family", mono_family)
    setattr(top_level, "_moke_ui_font_size", normal_size)
    setattr(top_level, "_moke_ui_small_font_size", small_size)
    return ui_family, mono_family


def button_role(text) -> str | None:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    if not words or any(word in words for word in BUTTON_ROLE_NEUTRAL_WORDS):
        return None
    if words[0] in ("stop", "zero") or "stop" in words or "off" in words:
        return "Danger"
    if "start" in words:
        return "Accent"
    return None


def button_role_style(role: str, base: str = "TButton") -> str:
    return f"{role}.{base or 'TButton'}"


def lamp_state(connected, fault: bool = False) -> str:
    if fault:
        return "fault"
    return "on" if connected else "off"


def parse_ttk_padding(value) -> tuple[int, int, int, int]:
    if isinstance(value, (tuple, list)):
        parts = [str(part) for part in value]
    else:
        parts = str(value or "").split()
    numbers = []
    for part in parts:
        numbers.extend(int(round(float(piece))) for piece in str(part).split())
    if not numbers:
        return (0, 0, 0, 0)
    left = numbers[0]
    top = numbers[1] if len(numbers) > 1 else left
    right = numbers[2] if len(numbers) > 2 else left
    bottom = numbers[3] if len(numbers) > 3 else top
    return (left, top, right, bottom)


def matched_padding(padding, base_size, role_size):
    left, top, right, bottom = padding
    dw = int(base_size[0]) - int(role_size[0])
    dh = int(base_size[1]) - int(role_size[1])
    result = (
        left + dw // 2,
        top + dh // 2,
        right + dw - dw // 2,
        bottom + dh - dh // 2,
    )
    return result if min(result) >= 0 else None


def theme_surface_of(widget) -> str:
    if not isinstance(widget, tk.Misc):
        return THEME_COLORS["surface"]
    current = widget
    try:
        for _depth in range(100):
            if not isinstance(current, tk.Misc):
                break
            kind = current.winfo_class()
            if kind in ("TLabelframe", "Labelframe"):
                return THEME_COLORS["surface"]
            if kind in ("Toplevel", "Tk"):
                break
            current = current.master
    except (tk.TclError, AttributeError):
        return THEME_COLORS["surface"]
    return THEME_COLORS["page"]


def create_lamp(parent, *, height=None, kind: str = "connection", background=None):
    height = max(LAMP_SIZE_PX, int(height or LAMP_SIZE_PX))
    canvas = tk.Canvas(
        parent,
        width=LAMP_SIZE_PX,
        height=height,
        highlightthickness=0,
        background=background or theme_surface_of(parent),
    )
    middle, centre, radius = height // 2, LAMP_SIZE_PX // 2, LAMP_DOT_PX // 2
    fill, outline = LAMP_COLORS["off"]
    item = canvas.create_oval(
        centre - radius,
        middle - radius,
        centre + radius,
        middle + radius,
        fill=fill,
        outline=outline,
    )
    register_lamp(canvas, kind)
    return canvas, item


def register_lamp(canvas, kind: str = "connection") -> None:
    try:
        canvas._lamp_kind = kind
        canvas._lamp_state = getattr(canvas, "_lamp_state", "off")
        top = canvas.winfo_toplevel()
        top.__dict__.setdefault("_look_lamps", []).append(canvas)
    except (tk.TclError, AttributeError, TypeError):
        pass


def set_lamp(target, state: str) -> None:
    if target is None:
        return
    canvas, item = target
    fill, outline = LAMP_COLORS.get(state, LAMP_COLORS["off"])
    try:
        canvas.itemconfigure(item, fill=fill, outline=outline)
    except tk.TclError:
        return
    try:
        changed = getattr(canvas, "_lamp_state", None) != state
        canvas._lamp_state = state
        if changed and threading.current_thread() is threading.main_thread():
            refresh = getattr(canvas.winfo_toplevel(), "_look_overview_refresh", None)
            if callable(refresh):
                refresh()
    except (tk.TclError, AttributeError, TypeError):
        pass


def _calibrate_role_padding(
    root, style, base: str, name: str, probe_text: str = "Start scan"
) -> bool:
    try:
        def size(style_name):
            probe = ttk.Button(root, text=probe_text, style=style_name)
            try:
                return int(probe.winfo_reqwidth()), int(probe.winfo_reqheight())
            finally:
                probe.destroy()

        padding = parse_ttk_padding(style.lookup(base, "padding"))
        style.configure(name, padding=padding)
        target = size(base)
        new = matched_padding(padding, target, size(name))
        if new is None:
            return False
        style.configure(name, padding=new)
        return size(name) == target
    except (tk.TclError, TypeError, ValueError, AttributeError):
        return False


def configure_button_roles(
    root, style, *, probe_text: str = "Start scan"
) -> dict[str, bool]:
    colors = THEME_COLORS
    fills = {
        "Accent": (
            colors["accent"],
            colors["accent_active"],
            colors["accent_pressed"],
        ),
        "Danger": (
            colors["danger"],
            colors["danger_active"],
            colors["danger_pressed"],
        ),
    }
    native = style.theme_use() in NATIVE_BUTTON_THEMES
    border = None
    if native:
        try:
            if "Look.Button.border" not in style.element_names():
                style.element_create("Look.Button.border", "from", "default", "border")
            border = "Look.Button.border"
        except tk.TclError:
            border = None
    result = {}
    for base in BUTTON_BASE_STYLES:
        try:
            base_layout = style.layout(base)
        except tk.TclError:
            base_layout = style.layout("TButton")
        for role in BUTTON_ROLES:
            name = button_role_style(role, base)
            fill, active, pressed = fills[role]
            filled = not native or border is not None
            if filled:
                if native:
                    outer = list(base_layout[0]) if base_layout else ["", {}]
                    outer[0] = border
                    style.layout(name, [tuple(outer)] + list(base_layout[1:]))
                style.configure(
                    name,
                    background=fill,
                    foreground=colors["on_fill"],
                    bordercolor=fill,
                    lightcolor=fill,
                    darkcolor=fill,
                    focuscolor=colors["on_fill"],
                )
                if native:
                    style.configure(name, relief="flat", borderwidth=1)
                style.map(
                    name,
                    background=[
                        ("disabled", colors["disabled_fill"]),
                        ("pressed", pressed),
                        ("active", active),
                    ],
                    bordercolor=[
                        ("disabled", colors["disabled_fill"]),
                        ("pressed", pressed),
                        ("active", active),
                    ],
                    lightcolor=[
                        ("disabled", colors["disabled_fill"]),
                        ("pressed", pressed),
                        ("active", active),
                    ],
                    darkcolor=[
                        ("disabled", colors["disabled_fill"]),
                        ("pressed", pressed),
                        ("active", active),
                    ],
                    foreground=[("disabled", colors["disabled_text"])],
                )
                if native:
                    filled = _calibrate_role_padding(
                        root, style, base, name, probe_text=probe_text
                    )
            if not filled:
                style.layout(name, base_layout)
                style.configure(
                    name,
                    foreground=fill,
                    padding=parse_ttk_padding(style.lookup(base, "padding")),
                )
                style.map(name, foreground=[("disabled", colors["disabled_text"])])
            result[name] = filled
    return result


def apply_look_to_tree(widget, style) -> None:
    page, surface = THEME_COLORS["page"], THEME_COLORS["surface"]
    made = set()

    def page_style(base: str) -> str:
        name = "Page." + base
        if name not in made:
            style.configure(name, background=page)
            if base.endswith(("TCheckbutton", "TRadiobutton")):
                style.map(name, background=[("active", page)])
            made.add(name)
        return name

    def walk(parent, in_card: bool) -> None:
        for child in parent.winfo_children():
            try:
                kind = child.winfo_class()
                if kind in ("Toplevel", "Tk"):
                    continue
                card = in_card or kind == "TLabelframe"
                if kind == "TButton":
                    current = str(child.cget("style")) or "TButton"
                    role = (
                        button_role(child.cget("text"))
                        if current in BUTTON_BASE_STYLES
                        else None
                    )
                    if role:
                        child.configure(style=button_role_style(role, current))
                elif (
                    kind in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton")
                    and not in_card
                ):
                    current = str(child.cget("style")) or kind
                    own = kind == "TLabel" and str(child.cget("background"))
                    if (
                        not own
                        and not current.startswith("Page.")
                        and str(style.lookup(current, "background")).lower() == surface
                    ):
                        child.configure(style=page_style(current))
                elif kind == "Canvas" and getattr(child, "_lamp_kind", None):
                    child.configure(background=surface if in_card else page)
            except (tk.TclError, AttributeError):
                continue
            walk(child, card)

    try:
        walk(widget, False)
    except (tk.TclError, AttributeError, TypeError):
        pass


def force_english_input_mode(widget=None) -> bool:
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        imm32 = ctypes.WinDLL("imm32", use_last_error=True)
        user32.GetKeyboardLayoutList.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        user32.GetKeyboardLayoutList.restype = ctypes.c_int
        user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE
        user32.ActivateKeyboardLayout.argtypes = [wintypes.HANDLE, wintypes.UINT]
        user32.ActivateKeyboardLayout.restype = wintypes.HANDLE
        imm32.ImmGetContext.argtypes = [wintypes.HWND]
        imm32.ImmGetContext.restype = wintypes.HANDLE
        imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
        imm32.ImmSetOpenStatus.restype = wintypes.BOOL
        imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm32.ImmReleaseContext.restype = wintypes.BOOL
        count = int(user32.GetKeyboardLayoutList(0, None))
        english_layout = None
        if count > 0:
            layouts = (wintypes.HANDLE * count)()
            actual = int(user32.GetKeyboardLayoutList(count, layouts))
            for index in range(actual):
                handle_value = int(layouts[index] or 0)
                language_id = handle_value & 0xFFFF
                if (language_id & 0x03FF) == 0x0009:
                    english_layout = layouts[index]
                    break
        if not english_layout:
            english_layout = user32.LoadKeyboardLayoutW("00000409", 0x00000001)
        if english_layout:
            user32.ActivateKeyboardLayout(english_layout, 0)
        if widget is not None:
            hwnd = wintypes.HWND(int(widget.winfo_id()))
            input_context = imm32.ImmGetContext(hwnd)
            if input_context:
                try:
                    imm32.ImmSetOpenStatus(input_context, False)
                finally:
                    imm32.ImmReleaseContext(hwnd, input_context)
        return True
    except BaseException:
        return False


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calculate_window_layout(root: tk.Tk):
    screen_width = max(640, int(root.winfo_screenwidth()))
    screen_height = max(480, int(root.winfo_screenheight()))
    if sys.platform.startswith("win"):
        taskbar_reserve = 48
    elif sys.platform == "darwin":
        taskbar_reserve = 30
    else:
        taskbar_reserve = 42
    usable_height = max(400, screen_height - taskbar_reserve)
    edge = max(4, int(round(min(screen_width, screen_height) * 0.008)))
    gap = max(6, int(round(WINDOW_GAP)))
    wide_split = screen_width >= 1700
    if wide_split:
        control_width = int(round(screen_width * CONTROL_WIDTH_FRACTION))
        control_width = max(720, min(control_width, screen_width - 620 - gap))
        control_x = max(edge, screen_width - control_width - edge)
        camera_x = edge
        camera_width = max(600, control_x - gap - camera_x)
    else:
        control_x = edge
        camera_x = edge
        control_width = max(480, screen_width - 2 * edge)
        camera_width = control_width
    window_height = max(380, usable_height - edge)
    ui_scale = _clamp(
        min(control_width / 960.0, window_height / 1000.0), 0.72, 1.18
    )
    canvas_scale = _clamp(window_height / 1000.0, 0.62, 1.22)
    return {
        "camera": (camera_width, window_height, camera_x, edge),
        "control": (control_width, window_height, control_x, edge),
        "screen": (screen_width, screen_height),
        "usable_height": usable_height,
        "ui_scale": ui_scale,
        "canvas_scale": canvas_scale,
        "compact": not wide_split or window_height < 850,
    }


def geometry_string(geometry) -> str:
    width, height, x, y = geometry
    return f"{width}x{height}+{x}+{y}"


def nice_linear_ticks(
    data_min: float,
    data_max: float,
    target_intervals: int = 4,
    *,
    include_zero: bool = False,
    expand_limits: bool = True,
):
    low = float(data_min)
    high = float(data_max)
    if not math.isfinite(low) or not math.isfinite(high):
        low, high = 0.0, 1.0
    if low > high:
        low, high = high, low
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if math.isclose(low, high, rel_tol=0.0, abs_tol=1e-15):
        magnitude = abs(low) if abs(low) > 0.0 else 1.0
        half_span = max(magnitude * 0.05, 1e-12)
        low -= half_span
        high += half_span
    raw_step = (high - low) / max(int(target_intervals), 1)
    exponent = math.floor(math.log10(raw_step))
    scale = 10.0 ** exponent
    fraction = raw_step / scale
    if fraction <= 1.5:
        nice_fraction = 1.0
    elif fraction <= 2.25:
        nice_fraction = 2.0
    elif fraction <= 3.75:
        nice_fraction = 2.5
    elif fraction <= 7.5:
        nice_fraction = 5.0
    else:
        nice_fraction = 10.0
    step = nice_fraction * scale
    epsilon = abs(step) * 1e-10
    if expand_limits:
        axis_min = math.floor((low + epsilon) / step) * step
        axis_max = math.ceil((high - epsilon) / step) * step
        if axis_max <= axis_min:
            axis_max = axis_min + step
    else:
        axis_min, axis_max = low, high
    first_index = int(math.ceil((axis_min - epsilon) / step))
    last_index = int(math.floor((axis_max + epsilon) / step))
    ticks = [index * step for index in range(first_index, last_index + 1)]
    if len(ticks) < 2:
        ticks = [axis_min, axis_max]
    ticks = [0.0 if abs(value) < epsilon else value for value in ticks]
    return axis_min, axis_max, ticks, step


def angle_axis_ticks(low: float, high: float):
    low, high = float(low), float(high)
    if low > high:
        low, high = high, low
    span = high - low
    if not math.isfinite(span) or span <= 0.0:
        return nice_linear_ticks(low, high)
    if span >= 4.0:
        step = next(
            (s for s in (1, 2, 5, 10, 15, 30, 45, 90, 180) if span / s <= 6),
            360,
        )
        epsilon = step * 1e-9
        ticks = [
            float(index * step)
            for index in range(
                math.ceil((low - epsilon) / step),
                math.floor((high + epsilon) / step) + 1,
            )
        ]
        if len(ticks) >= 2:
            return low, high, ticks, float(step)
    return nice_linear_ticks(low, high, expand_limits=False)


def format_nice_tick(value: float, step: float) -> str:
    if abs(value) < abs(step) * 1e-9:
        return "0"
    if abs(step) < 1e-6 or abs(value) >= 1e7:
        return f"{value:.4g}"
    if abs(step) >= 1.0:
        if math.isclose(step, round(step), rel_tol=0.0, abs_tol=1e-12):
            return f"{value:.0f}"
        return f"{value:.1f}"
    decimals = min(8, max(0, int(math.ceil(-math.log10(abs(step))))))
    while decimals < 8 and not math.isclose(
        abs(step) * 10.0 ** decimals,
        round(abs(step) * 10.0 ** decimals),
        rel_tol=0.0,
        abs_tol=1e-09,
    ):
        decimals += 1
    return f"{value:.{decimals}f}"


def shade_color(color: str, fraction: float) -> str:
    fraction = min(max(float(fraction), 0.0), 1.0)
    rgb = [int(color[index : index + 2], 16) for index in (1, 3, 5)]
    if fraction < 0.5:
        mix = (0.5 - fraction) / 0.5 * 0.4
        rgb = [channel + (255 - channel) * mix for channel in rgb]
    else:
        mix = (fraction - 0.5) / 0.5 * 0.45
        rgb = [channel * (1 - mix) for channel in rgb]
    return "#" + "".join(f"{round(channel):02x}" for channel in rgb)


def draw_plot_grid(canvas, left, top, plot_width, plot_height) -> None:
    for index in range(1, 4):
        fraction = index / 4.0
        x = left + fraction * plot_width
        y = top + fraction * plot_height
        canvas.create_line(
            x, top, x, top + plot_height, fill=THEME_COLORS["plot_grid"], dash=(2, 3)
        )
        canvas.create_line(
            left, y, left + plot_width, y, fill=THEME_COLORS["plot_grid"], dash=(2, 3)
        )


def draw_nonlocal_series(
    canvas,
    series,
    x_label,
    y_label,
    waiting_text,
    markers=False,
    auto_voltage_unit=False,
    show_legend=True,
    color_override=None,
    series_labels=None,
    series_colors=None,
    marker_radius=3.0,
    series_markers=None,
    symmetric_x_extent=None,
    angle_x_range=None,
    x_range=None,
) -> None:
    canvas.delete("all")
    width = max(canvas.winfo_width(), 330)
    height = max(canvas.winfo_height(), 120)
    left, right, top, bottom = 86, 24, 24, 52
    if getattr(canvas, "ppms_compact_plot", False):
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        if height < 55:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Increase the window height",
                anchor="center",
            )
            return
        top, bottom = 14, 44
        legend_names = {
            (series_labels or {}).get(key, str(key).split("_loop_", 1)[0])
            for key, rows in series.items()
            if rows
        }
        one_named = (
            any(legend_names)
            and getattr(canvas, "compact_legend_strip", False) is True
        )
        if show_legend and (len(legend_names) > 1 or one_named):
            top = 26
    hide_x_labels = getattr(canvas, "ppms_hide_x_labels", False) is True
    if hide_x_labels:
        bottom = 10
    plot_width = max(1, width - left - right)
    plot_height = max(1, height - top - bottom)
    draw_plot_grid(canvas, left, top, plot_width, plot_height)
    canvas.create_rectangle(
        left, top, left + plot_width, top + plot_height, outline="#444444"
    )
    if not hide_x_labels:
        canvas.create_text(left + plot_width / 2, height - 10, text=x_label)
    finite_series = {
        key: [
            (float(x), float(y))
            for x, y in rows
            if math.isfinite(float(x)) and math.isfinite(float(y))
        ]
        for key, rows in series.items()
    }
    all_rows = [row for rows in finite_series.values() for row in rows]
    if auto_voltage_unit:
        reference = max((abs(row[1]) for row in all_rows), default=0.0)
        if reference >= 1.0:
            voltage_scale, voltage_unit = 1.0, "V"
        elif reference >= 1e-3:
            voltage_scale, voltage_unit = 1e-3, "mV"
        elif reference >= 1e-6:
            voltage_scale, voltage_unit = 1e-6, "µV"
        else:
            voltage_scale, voltage_unit = 1e-9, "nV"
        finite_series = {
            key: [(x, y / voltage_scale) for x, y in rows]
            for key, rows in finite_series.items()
        }
        all_rows = [row for row in finite_series.values() for row in rows]
        y_label = f"{y_label} ({voltage_unit})"
    canvas.create_text(16, top + plot_height / 2, text=y_label, angle=90)
    if not all_rows:
        canvas.create_text(
            left + plot_width / 2,
            top + plot_height / 2,
            text=waiting_text,
            fill="#666666",
        )
        return
    xs = [row[0] for row in all_rows]
    ys = [row[1] for row in all_rows]
    if symmetric_x_extent is not None:
        extent = max(abs(float(symmetric_x_extent)), max(abs(x) for x in xs))
        extent = extent if extent > 0 else 1.0
        x_min, x_max, x_ticks, x_tick_step = nice_linear_ticks(-extent, extent)
    elif angle_x_range is not None and float(angle_x_range[0]) != float(
        angle_x_range[1]
    ):
        x_min, x_max, x_ticks, x_tick_step = angle_axis_ticks(
            min(float(min(angle_x_range)), min(xs)),
            max(float(max(angle_x_range)), max(xs)),
        )
    elif x_range is not None:
        x_min, x_max, x_ticks, x_tick_step = nice_linear_ticks(
            min(float(x_range[0]), min(xs)),
            max(float(x_range[1]), max(xs)),
            expand_limits=False,
        )
    else:
        x_min, x_max, x_ticks, x_tick_step = nice_linear_ticks(min(xs), max(xs))
    y_min, y_max, y_ticks, y_tick_step = nice_linear_ticks(min(ys), max(ys))
    for tick_index, x_value in enumerate(x_ticks):
        x = left + (x_value - x_min) / (x_max - x_min) * plot_width
        canvas.create_line(x, top + plot_height, x, top + plot_height + 4)
        if not hide_x_labels:
            canvas.create_text(
                x,
                top + plot_height + 15,
                text=format_nice_tick(x_value, x_tick_step),
                anchor=(
                    "w"
                    if tick_index == 0
                    else "e"
                    if tick_index == len(x_ticks) - 1
                    else "center"
                ),
            )
    spacing = plot_height / max(1, len(y_ticks) - 1)
    label_every = max(1, math.ceil(16.0 / spacing)) if spacing > 0 else 1
    for index, y_value in enumerate(reversed(y_ticks)):
        y = top + (y_max - y_value) / (y_max - y_min) * plot_height
        canvas.create_line(left - 4, y, left, y)
        if index % label_every:
            continue
        canvas.create_text(
            left - 7, y, text=format_nice_tick(y_value, y_tick_step), anchor="e"
        )
    colors = {
        "8": "#1976a3",
        "9": "#c43b75",
        "10": "#7b4ab8",
        "mfli": "#008b6f",
        "2182": "#6a3dad",
    }
    series_labels = {} if series_labels is None else dict(series_labels)
    series_colors = {} if series_colors is None else dict(series_colors)
    legend_x = left + 8
    legend_drawn: set[str] = set()
    order = {"8": 0, "9": 1, "10": 2, "mfli": 3, "2182": 4}

    def base_series_key(series_key):
        return str(series_key).split("_loop_", 1)[0]

    def series_sort_key(series_key):
        frequency_match = re.search(
            r"_freq_([-+0-9.eE]+)_loop_(\d+)$", str(series_key)
        )
        if frequency_match is not None:
            return (
                1,
                float(frequency_match.group(1)),
                int(frequency_match.group(2)),
            )
        return (0, order.get(base_series_key(series_key), 99), str(series_key))

    for key in sorted(finite_series, key=series_sort_key):
        rows = finite_series[key]
        if not rows:
            continue
        base_key = base_series_key(key)
        color = (
            color_override
            or series_colors.get(key)
            or series_colors.get(base_key)
            or colors.get(base_key, "#555555")
        )
        points = []
        for x_value, y_value in rows:
            x = left + (x_value - x_min) / (x_max - x_min) * plot_width
            y = top + plot_height - (y_value - y_min) / (y_max - y_min) * plot_height
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=2)
        if markers:
            shape = (series_markers or {}).get(key)
            hollow = shape == "open_circle"
            radius = max(2.5 if hollow else 0.5, float(marker_radius))
            marker = (
                canvas.create_rectangle if shape == "square" else canvas.create_oval
            )
            fill = "white" if hollow else color
            outline = color if hollow else ""
            marker_width = 0.75 if hollow else 1
            drawn = set()
            offset = (
                radius
                if float(radius).is_integer() or (2 * radius).is_integer()
                else 0.0
            )
            for x, y in zip(points[0::2], points[1::2]):
                if offset:
                    pixel = (round(x - offset), round(y - offset))
                    if pixel in drawn:
                        continue
                    drawn.add(pixel)
                marker(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill=fill,
                    outline=outline,
                    width=marker_width,
                )
        legend_label = series_labels.get(key)
        if legend_label is None:
            legend_label = (
                "Keithley 2182A"
                if base_key == "2182"
                else "Zurich MFLI"
                if base_key == "mfli"
                else f"SR830 #{base_key}"
            )
        if show_legend and legend_label and legend_label not in legend_drawn:
            legend_drawn.add(legend_label)
            canvas.create_line(legend_x, 12, legend_x + 18, 12, fill=color, width=2)
            if series_markers:
                marker = (
                    canvas.create_rectangle
                    if series_markers.get(key) == "square"
                    else canvas.create_oval
                )
                hollow = series_markers.get(key) == "open_circle"
                marker(
                    legend_x + 6,
                    9,
                    legend_x + 12,
                    15,
                    fill="white" if hollow else color,
                    outline=color if hollow else "",
                    width=0.75 if hollow else 1,
                )
            item = canvas.create_text(
                legend_x + 22, 12, text=legend_label, anchor="w", fill=color
            )
            try:
                end = float(canvas.bbox(item)[2])
            except (AttributeError, TypeError, IndexError, ValueError, tk.TclError):
                end = legend_x + 22 + 7 * len(str(legend_label))
            legend_x = max(legend_x + 82, end + 14)


def save_plot_canvas_snapshot(root: tk.Misc, canvas: tk.Canvas, path: Path) -> None:
    if ImageGrab is None:
        raise RuntimeError(
            "Saving a plot snapshot requires Pillow's ImageGrab module"
        )
    root.update_idletasks()
    width = int(canvas.winfo_width())
    height = int(canvas.winfo_height())
    if width <= 1 or height <= 1 or not canvas.winfo_ismapped():
        raise RuntimeError("The plot is not visible; cannot capture a snapshot")
    left = int(canvas.winfo_rootx())
    top = int(canvas.winfo_rooty())
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    bbox = (left, top, left + width, top + height)
    try:
        snapshot = ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        snapshot = ImageGrab.grab(bbox=bbox)
    snapshot.save(destination, format="PNG")


def save_scan_plot_snapshots(
    root: tk.Misc,
    recorder,
    plots,
    *,
    points: int = 1,
    redraw=None,
    closing: bool = False,
    log_fn=None,
) -> str:
    try:
        if recorder is None or recorder.closed or int(points) <= 0 or closing:
            return ""
        directory = Path(recorder.directory)
        stem = directory.name
        plots = [(suffix, widget) for suffix, widget in plots if widget is not None]
        if not plots:
            return ""
    except BaseException:
        return ""
    saved: list[str] = []
    failed: list[str] = []
    try:
        if redraw is not None:
            redraw()
        root.update_idletasks()
        if sys.platform == "win32":
            ctypes.windll.gdi32.GdiFlush()
        time.sleep(SCAN_PLOT_SNAPSHOT_SETTLE_S)
    except BaseException:
        pass
    for suffix, widget in plots:
        try:
            path = directory / f"{stem}_{suffix}_snapshot.png"
            number = 0
            while path.exists():
                number += 1
                path = directory / f"{stem}_{suffix}_snapshot_{number:02d}.png"
            save_plot_canvas_snapshot(root, widget, path)
            saved.append(path.name)
        except BaseException as error:
            failed.append(f"{suffix}: {' '.join(str(error).split())[:160]}")
    problem = "; ".join(failed)
    try:
        recorder.event("plot_snapshots", saved=not failed, files=saved, error=problem)
    except BaseException:
        pass
    if log_fn is not None:
        try:
            log_fn(
                f"{recorder.label} plot snapshot: "
                + (", ".join(saved) + " saved" if saved else "none saved")
                + (f"; save failed: {problem}" if failed else "")
            )
        except BaseException:
            pass
    return f"; plot snapshot save failed: {failed[0]}" if failed else ""
