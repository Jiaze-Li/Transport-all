"""Every page that uses a Zurich MFLI picks one of the lab's two by name.

DEV4435 is reached over its USB link (192.168.69.78, or a PC-side LabOne
server); DEV4414, the Super Zurich, has its own Data Server on the lab network
at 192.168.68.250.  Each server sees only its own MFLI, so a page that probed
DEV4435's servers for DEV4414 could never connect: the STFMR and PPMS bug.
These tests run the fully built application with the network stubbed out.
"""
import time
import tkinter as tk
import tkinter.font
import unittest
from unittest import mock

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app

SUPER = [("192.168.68.250", 8004)]
USB = [("127.0.0.1", 8004), ("192.168.69.78", 8004)]
CHOICES = ("DEV4435 (Normal)", "DEV4414 (Super)")


class ZurichChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.app = build_lab_app(app, cls.root)  # as the lab PC shows it
        cls.root.geometry(f"{LAB_WIDE}x{lab_window(cls.app)[1]}+0+0")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def setUp(self):
        self.a = self.app
        self.errors, self.probed = [], []

        def refuse(address, timeout=None):
            self.probed.append(tuple(address))
            raise OSError("refused (test)")

        for patcher in (
            mock.patch.object(app.messagebox, "showerror", lambda *a, **k: self.errors.append(a[1])),
            mock.patch.object(app.messagebox, "showwarning", lambda *a, **k: self.errors.append(a[1])),
            mock.patch.object(app.socket, "create_connection", refuse),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def pump(self, done):
        for _ in range(500):
            self.root.update()
            if done():
                return
            time.sleep(0.01)
        self.fail("the connection attempt never finished")

    def show(self, tab_name):
        notebook = next(w for w in self.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == tab_name:
                notebook.select(index)
        for _ in range(3):
            self.root.update()

    def choosers(self):
        """(page, list widget, its variable, default entry) for every Zurich user."""
        vi = next(w for w in self._descendants(self.root)
                  if w.winfo_class() == "TCombobox"
                  and str(w.cget("textvariable")) == str(self.a.nonlocal_vi_mfli_device_var))
        return (
            ("STFMR", self.a.stfmr_lockin_resource_widgets["mfli"],
             self.a.stfmr_lockin_resource_vars["mfli"], "DEV4435 (Normal)"),
            # RT-E's rack Zurich since 2026-10-08 (user: the same cables as RT-E).
            ("Non-local", vi, self.a.nonlocal_vi_mfli_device_var, "DEV4414 (Super)"),
            ("PPMS", self.a.ppms_panel.zurich_entries[0], self.a.ppms_panel.zurich_resource,
             "DEV4414 (Super)"),
            ("SHHV", self.a.rte_panel.zurich_entries[0], self.a.rte_panel.zurich_resource,
             "DEV4414 (Super)"),
        )

    @classmethod
    def _descendants(cls, widget):
        for child in widget.winfo_children():
            yield child
            yield from cls._descendants(child)

    # ------------------------------------------------------------------ lists

    # ---------------------------------------------------------------- servers

    # --------------------------------------------------------------- messages
    def test_failure_points_at_the_other_lockin_without_misleading_addresses(self):
        for device, servers, present, absent in (
                ("dev4414", SUPER, ("Super Zurich", '"DEV4435 (Normal)"', "192.168.68.250"),
                 ("192.168.69.78", "192.168.69.77")),
                ("dev4435", None, ('"DEV4414 (Super)"', "192.168.68.250", "192.168.69.78"), ())):
            with self.subTest(device=device):
                controller = app.NonLocalACController(lambda _message: None)
                controller.mfli_server_candidates = servers
                with self.assertRaises(RuntimeError) as caught:
                    controller.connect_mfli(device)
                message = str(caught.exception)
                for text in present:
                    self.assertIn(text, message)
                for text in absent:
                    self.assertNotIn(text, message)


if __name__ == "__main__":
    unittest.main()
