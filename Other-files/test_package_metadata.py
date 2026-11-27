"""The package: pyproject.toml installs what the standalone PPMS application
imports, under the application's own version, with a command that starts it
as `python Transport_PPMS.py` does."""
import tomllib
import unittest
from pathlib import Path

from test_ppms_motor_debug import app

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class PackageTests(unittest.TestCase):
    def test_the_version_is_the_programs(self):
        self.assertEqual(PROJECT["project"]["version"], app.APP_VERSION)

    def test_every_package_module_is_packaged(self):
        setup = PROJECT["tool"]["setuptools"]
        self.assertEqual(setup["py-modules"], ["Transport_PPMS"])
        on_disk = sorted(".".join(path.parent.relative_to(ROOT).parts)
                         for path in (ROOT / "transport").rglob("__init__.py"))
        self.assertEqual(sorted(setup["packages"]), on_disk)

    def test_the_command_runs_the_program(self):
        module, _, function = PROJECT["project"]["scripts"]["transport-ppms"].partition(":")
        self.assertEqual(module, "transport.ppms.app")
        self.assertTrue(callable(getattr(app, function)))
        self.assertEqual(function, "run")
    def test_the_application_does_not_need_the_main_program(self):
        self.assertFalse((ROOT / "Transport_all.py").exists())
        source = (ROOT / "transport" / "ppms" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("import Transport_all", source)
        entry = (ROOT / "Transport_PPMS.py").read_text(encoding="utf-8")
        self.assertIn("from transport.ppms.app import main", entry)


if __name__ == "__main__":
    unittest.main()
