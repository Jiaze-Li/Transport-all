"""Layout checks of the standalone SHHV & Switch application for the cloud test
machines, with screenshots of its pages.

Run on a GitHub runner, where Tk draws on a virtual display (Xvfb on Linux) or
the runner's own desktop (Windows), so nothing appears on anyone's screen.  It
never talks to instruments.

    python Other-files/ci/rt_checks.py --shots DIR

For each emulated lab screen it builds the whole application at 96 DPI,
checks that the window is this program (its title and its pages), and reports any widget that sticks out past
the page and any label, button or Zurich list entry whose text does not fit.
It saves a screenshot of each page at the lab PC's windows.

Exit status is 1 if the application does not build, a page is missing, or the
log shows an error from building the page.  Clipped widgets and cut text are
reported only, as for this page in the main program's checks.
"""
import argparse
import time
import sys
import tkinter as tk
import tkinter.font
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCREENS = (  # (screen width, height, window width or 0 for the program's own)
    (2560, 1440, 2000),  # the lab PC, window widened by hand
    (2560, 1440, 0),     # the lab PC, the program's default window
    (1920, 1080, 0),
    (1600, 1586, 0),
)
PAGES = ('SHHV & Switch', 'Log')


def build(module, screen_w, screen_h, window_w):
    original = module.calculate_window_layout

    class FakeScreen:
        def __init__(self, _root):
            pass

        def winfo_screenwidth(self):
            return screen_w

        def winfo_screenheight(self):
            return screen_h

    module.calculate_window_layout = lambda root: original(FakeScreen(root))
    try:
        root = tk.Tk()
        root.tk.call("tk", "scaling", 1.333)  # 96 DPI, as on the lab PC
        root.maxsize(4000, 3000)
        app = module.CombinedControlApp(root)
    finally:
        module.calculate_window_layout = original
    width = window_w or app.window_layout["control"][0]
    root.geometry(f"{width}x1300+0+0")
    root.update()
    return root, app, app.notebook


def show(root, notebook, name):
    for index in range(notebook.index("end")):
        if notebook.tab(index, "text") == name:
            notebook.select(index)
            for _ in range(4):
                root.update()
            return notebook.nametowidget(notebook.tabs()[index])
    raise KeyError(name)


def text_fits(widget):
    try:
        text = widget.cget("text")
    except tk.TclError:
        return True
    if not text or ("textvariable" in widget.keys() and str(widget.cget("textvariable"))):
        return True
    try:
        font = tkinter.font.nametofont(str(widget.cget("font")) or "TkDefaultFont")
    except (tk.TclError, KeyError):
        font = tkinter.font.nametofont("TkDefaultFont")
    slack = {"TButton": 10, "TCheckbutton": 22, "TLabel": 0}.get(widget.winfo_class(), 0)
    # Text with a wrap width shows in full on several lines, as long as the
    # wrap width fits the widget.
    try:
        wrap = widget.cget("wraplength") if "wraplength" in widget.keys() else ""
        if not str(wrap) and "style" in widget.keys():
            wrap = tk.ttk.Style(widget).lookup(str(widget.cget("style")) or widget.winfo_class(), "wraplength")
        wrap = int(float(str(wrap or 0)))
    except (tk.TclError, ValueError):
        wrap = 0
    if wrap > 0:
        longest = max(font.measure(line) for line in str(text).splitlines() or [""])
        return min(wrap, longest) <= widget.winfo_width() - slack + 2
    # Tk measures a string as one line; a wrapped label is as wide as its
    # longest line.
    longest = max(font.measure(line) for line in str(text).splitlines() or [""])
    return longest <= widget.winfo_width() - slack


def inspect(tab):
    right = tab.winfo_rootx() + tab.winfo_width()
    clipped, cut, pairs = [], [], []

    def walk(widget):
        for child in widget.winfo_children():
            if child.winfo_ismapped():
                over = child.winfo_rootx() + child.winfo_width() - right
                if over > 1:
                    label = child.cget("text") if "text" in child.keys() else ""
                    clipped.append(f"{child.winfo_class()}[{str(label)[:24]}] over by {over}px")
                if child.winfo_class() in ("TButton", "TCheckbutton", "TLabel") and not text_fits(child):
                    cut.append(str(child.cget("text"))[:28])
                if getattr(child, "zurich_chooser", False) is True:
                    font = tkinter.font.nametofont("TkTextFont")
                    # The text area: ttk sizes it as width x the width of "0";
                    # the rest of the requested width is arrow, border, padding.
                    room = child.winfo_width() - (
                        child.winfo_reqwidth() - int(child.cget("width")) * font.measure("0"))
                    for value in child.cget("values"):
                        if font.measure(value) > room:
                            cut.append(f"Zurich list: {value}")
            if hasattr(child, "_pair_side_by_side"):
                pairs.append("side" if child._pair_side_by_side else "stack")
            walk(child)

    walk(tab)
    return sorted(set(clipped)), sorted(set(cut)), pairs


def screenshot(root, tab, path):
    """Save the page as shown, even when it is larger than the machine's screen.

    GitHub's Windows machines have a 1024x768 desktop, so the page is shot in
    screen-sized tiles, moving the window so each tile is on screen, and the
    tiles are stitched together.
    """
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return "Pillow missing"
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(4):
        root.update()
    width, height = tab.winfo_width(), tab.winfo_height()
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    if sys.platform == "win32":
        screen_h -= 80  # stay clear of the taskbar, which stays on top
    page = Image.new("RGB", (width, height), "black")
    try:
        for top in range(0, height, screen_h):
            for left in range(0, width, screen_w):
                # Move the window until this tile's corner is at the screen's.
                for _ in range(3):
                    dx = tab.winfo_rootx() + left
                    dy = tab.winfo_rooty() + top
                    if (dx, dy) == (0, 0):
                        break
                    root.geometry(f"+{root.winfo_x() - dx}+{root.winfo_y() - dy}")
                    for _ in range(4):
                        root.update()
                time.sleep(0.2)
                root.update()
                tile_w = min(screen_w, width - left)
                tile_h = min(screen_h, height - top)
                page.paste(ImageGrab.grab(bbox=(0, 0, tile_w, tile_h)), (left, top))
    except Exception as error:  # no grabbable display
        return f"screenshot failed: {error}"
    finally:
        root.geometry("+0+0")
        root.update()
    page.save(path)
    return str(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, help="folder for page screenshots")
    args = parser.parse_args()
    import transport.rt.app as module
    failed = []
    for screen_w, screen_h, window_w in SCREENS:
        root, app, notebook = build(module, screen_w, screen_h, window_w)
        try:
            print(f"screen {screen_w}x{screen_h}, window {root.winfo_width()} px, ui_scale {app.ui_scale:.2f}")
            titles = tuple(notebook.tab(index, "text") for index in range(notebook.index("end")))
            if root.title() != module.MAIN_WINDOW_TITLE or titles != PAGES:
                failed.append(f"window {root.title()!r} with pages {titles}")
            if type(app.rte_panel) is not module.RTEPanel or not app.rte_panel.rte_controllers:
                failed.append("no SHHV & Switch panel with its instrument controllers")
            app.append_log("SHHV & Switch checks: the log page shows this line")
            for name in PAGES:
                tab = show(root, notebook, name)
                clipped, cut, pairs = inspect(tab)
                marker = "note" if clipped or cut else "ok  "
                print(f"  {marker} {name:5s} layouts {pairs} | clipped {clipped or 'none'} | text cut {cut or 'none'}")
                if args.shots and screen_w == 2560:
                    args.shots.mkdir(parents=True, exist_ok=True)
                    shot = args.shots / f"{name.replace(' & ', '-').replace(' ', '_')}_{root.winfo_width()}px.png"
                    print(f"       screenshot: {screenshot(root, tab, shot)}")
                    if name == "SHHV & Switch":
                        # Every scan mode builds its rows; each is shot.
                        panel, was = app.rte_panel, app.rte_panel.ppms_simple_mode.get()
                        for mode in (*module.RTE_SCAN_MODES, was):
                            panel.ppms_simple_mode.set(mode)
                            panel._rte_mode_changed()
                            root.update()
                            if mode != was:
                                shot = args.shots / f"SHHV-Switch_{mode.replace('+', '-')}-mode_{root.winfo_width()}px.png"
                                print(f"       screenshot: {screenshot(root, tab, shot)}")
            shown = app.log_text.get("1.0", "end")
            if "SHHV & Switch checks: the log page shows this line" not in shown:
                failed.append("the log page did not take a line")
            for line in shown.splitlines():
                if "Traceback" in line or "Error" in line:
                    failed.append(f"log: {line[:160]}")
        finally:
            app.closing = True
            app.safe_close_hardware()
            root.destroy()
    for problem in failed:
        print("FAIL " + problem)
    print("SHHV & SWITCH CHECKS FAILED" if failed else "SHHV & SWITCH CHECKS OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
