"""The scan-start window (user, 2026-11-23: "all this scan start window, only
tell me the scan expected time is Okay and make the required time bold and
red"): the expected time in bold red and Yes / No, the long details only in
the log; a hazard warning (high voltage, a large first rotation) stays above
the time.  A real Tk window, so it runs on GitHub only."""
import tkinter as tk
import tkinter.font
import unittest

from test_ppms_motor_debug import app


class ScanStartWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        self.addCleanup(self.root.destroy)
        self.root.geometry("800x600+0+0")
        self.root.update()

    def ask(self, press, warning=""):
        seen = {}

        def answer():
            top = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            try:
                look(top)
            except Exception as error:  # never leave the modal window open: that would hang the run
                seen["error"] = error
                top.destroy()

        def look(top):
            widgets, pending = [], list(top.winfo_children())
            while pending:
                widget = pending.pop(0)
                widgets.append(widget)
                pending.extend(widget.winfo_children())
            labels = [w for w in widgets if w.winfo_class() == "TLabel"]
            seen["title"] = top.title()
            seen["texts"] = [str(w.cget("text")) for w in labels]
            shown = next(w for w in labels if str(w.cget("text")) == "at least 48 min 43 s")
            seen["colour"] = str(shown.cget("foreground"))
            seen["weight"] = tkinter.font.Font(root=top, name=str(shown.cget("font")), exists=True).actual("weight")
            seen["buttons"] = [str(w.cget("text")) for w in widgets if w.winfo_class() == "TButton"]
            if press == "Escape":
                top.focus_force()
                top.update()
                top.event_generate("<Escape>")
            else:
                next(w for w in widgets if w.winfo_class() == "TButton" and str(w.cget("text")) == press).invoke()

        def watchdog():
            # A key that never arrives must not hang the run: close the
            # window and fail.
            for top in [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]:
                seen["error"] = AssertionError(f"{press}: the window was still open after 5 s")
                top.destroy()

        self.root.after(300, answer)
        guard = self.root.after(5300, watchdog)
        result = app.ask_scan_start_window(self.root, "SHHV scan", "Switch scan.\n\nField, the OUTER loop: ...",
                                           "at least 48 min 43 s", warning)
        self.root.after_cancel(guard)
        if "error" in seen:
            raise seen["error"]
        return result, seen

    def test_only_the_expected_time_in_bold_red_then_yes(self):
        result, seen = self.ask("Yes")
        self.assertTrue(result)
        self.assertEqual(seen["title"], "SHHV scan")
        self.assertEqual(seen["texts"], ["Expected scan time:", "at least 48 min 43 s", "Is this time OK? Start the scan?"])
        self.assertEqual(seen["colour"], "#d00000")
        self.assertEqual(seen["weight"], "bold")
        self.assertEqual(seen["buttons"], ["Yes", "No"])

    def test_no_and_escape_do_not_start(self):
        self.assertFalse(self.ask("No")[0])
        self.assertFalse(self.ask("Escape")[0])

    def test_a_hazard_warning_stays_above_the_time(self):
        result, seen = self.ask("Yes", warning="HIGH VOLTAGE: up to 60 V on the 200 V range; hardware OVP 70 V.")
        self.assertTrue(result)
        self.assertEqual(seen["texts"][0], "HIGH VOLTAGE: up to 60 V on the 200 V range; hardware OVP 70 V.")
        self.assertEqual(seen["texts"][1:], ["Expected scan time:", "at least 48 min 43 s", "Is this time OK? Start the scan?"])


if __name__ == "__main__":
    unittest.main()
