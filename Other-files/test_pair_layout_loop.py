"""The window finishes drawing: no side-by-side pair flips forever.

2026-10-12: on the CIDWM page the difference display's mode row (Off / Plain
difference / Drift tracking beside Recapture BG / 100x marker) flipped
between side by side and stacked at every <Configure>, because the spare
width went to the Images box and the display's width followed its own row.
root.update() never returned and the program hung at start-up on the lab
PCs.  Each class builds the whole application as the lab PC shows it.
"""
import tkinter as tk
import tkinter.font
import unittest

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app

WIDTHS = (LAB_WIDE, 1101, 826, 1574, 2560)  # gui_checks.py's, and maximized at the lab
CI_SHOT_HEIGHT = 1300  # gui_checks.py's window height, the CI screenshots'


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def cidwm_mode_row(built):
    box = next(w for w in descendants(built.k6221_tab)
               if isinstance(w, tk.ttk.LabelFrame) and str(w.cget("text")) == "MOKE difference display")
    return next(w for w in box.winfo_children() if hasattr(w, "_pair_side_by_side"))


def select_page(built, text):
    notebook = next(w for w in built.__dict__.values() if isinstance(w, tk.ttk.Notebook))
    for index in range(notebook.index("end")):
        if notebook.tab(index, "text") == text:
            notebook.select(index)


def rte_stage_parts(built):
    """RT-E's settings box since 2026-10-17 (user: "now move the stage
    controllers to the RT-E settings"): (the params/lock-ins pair's frame,
    the params, the lock-ins, the stage block)."""
    panel = built.rte_panel
    block = panel.rte_stage_stop_button.master
    lockins = panel.ppms_settings_panel
    body = block.master
    params = next(w for w in body.winfo_children() if w is not block and w is not lockins.master)
    return body, params, lockins, block


class Built:
    @classmethod
    def build(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.a = build_lab_app(app, cls.root)
        cls.height = lab_window(cls.a)[1]

    @classmethod
    def tearDownClass(cls):
        cls.a.closing = True
        cls.a.safe_close_hardware()
        cls.root.destroy()

    def show(self, width):
        self.root.geometry(f"{width}x{self.height}+0+0")
        self.root.update()


class EveryPageSettlesTests(Built, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build()

    def test_cidwm_difference_display_takes_the_spare_width(self):
        # Since 2026-10-20 (user: "use two columns for these two part") the
        # difference display is the right-hand box of a pair with the pulse
        # sign scan, which keeps its natural width (column 0, weight 0); the
        # display takes the spare width (column 1, weight 1), so its width
        # never follows its own mode row.  It was the left-hand box beside
        # the Images box (weights 1, 0) until then.
        select_page(self.a, "CIDWM")
        self.show(LAB_WIDE)
        row = cidwm_mode_row(self.a)
        display = row.master
        pair = display.master
        self.assertEqual((int(pair.columnconfigure(0)["weight"]), int(pair.columnconfigure(1)["weight"])),
                         (0, 1))
        self.assertTrue(pair._pair_side_by_side, "the scan and the display side by side at 2000 px")
        self.assertEqual(int(display.grid_info()["column"]), 1)
        self.assertTrue(row._pair_side_by_side, "the mode row fits beside its buttons at 2000 px")




class GuardTests(Built, unittest.TestCase):
    """A pair whose width follows its own layout settles: the looping row is
    held stacked.

    Until 2026-10-20 this rebuilt the 2026-10-12 shape on the CIDWM page (the
    spare width given to the Images box beside the difference display).
    That pair is gone: the difference display is beside the pulse sign scan
    and takes the spare width itself (user, 2026-10-20), and the Images box
    is alone on its row.  So the guard is held here by a pair made with the
    program's own _arrange_pair_by_width whose box is one pixel too narrow
    for side by side whenever it is side by side, and wide enough whenever
    it is stacked: the shape of the 2026-10-12 hang, which never settles
    without the guard."""

    @classmethod
    def setUpClass(cls):
        cls.build()

    def test_the_looping_row_is_held_stacked(self):
        self.show(LAB_WIDE)
        box = tk.Frame(self.root)
        self.addCleanup(box.destroy)
        left = tk.ttk.Frame(box, width=200, height=20)
        right = tk.ttk.Frame(box, width=200, height=20)
        left.grid(row=0, column=0)
        right.grid(row=0, column=1)
        widths = []

        def follow_own_layout(_event=None):
            # After the pair's own <Configure> handler: a width that makes
            # the other layout right, as a box sized by its own rows.
            wide = getattr(box, "_pair_side_by_side", None)
            if wide is None:
                return
            need = int(box._pair_wide_need)
            width = need - 1 if wide else need + 20
            widths.append(width)
            box.place_configure(width=width)

        self.a._arrange_pair_by_width(box, left, right, gap=0)
        box.bind("<Configure>", follow_own_layout, add="+")
        box.place(x=0, y=0, width=10, height=60)
        self.root.update()  # returns: without the guard it never would
        row = box
        self.assertTrue(row._pair_flip_guard["held"])
        self.assertIs(row._pair_side_by_side, False)
        self.assertEqual(row._pair_flip_guard["flips"], app.PAIR_FLIP_LIMIT + 1, "no flip after the hold")
        self.assertGreater(len(widths), app.PAIR_FLIP_LIMIT, "it did flip until it was held")
        # Held stacked although it is now wide enough, until the window is resized.
        self.assertGreaterEqual(box.winfo_width(), box._pair_wide_need)


if __name__ == "__main__":
    unittest.main()
