"""Regression tests for shared lock-in settling calculations (transport.common.settling)."""

import inspect
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure repository root is on sys.path for direct test execution
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import transport.fmr.app as app
from transport.common import (
    DEFAULT_LOCKIN_SETTLE_TAUS,
    calculate_lockin_settle_s,
    format_lockin_settle_text,
    format_settle_status_text,
)


class TestTransportCommonSettling(unittest.TestCase):
    def test_default_constant(self):
        self.assertEqual(DEFAULT_LOCKIN_SETTLE_TAUS, 1.0)
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 1.0)

    def test_pure_function_signatures(self):
        """Requirement 3: The new pure calculation functions accept an explicit settle_taus argument."""
        sig_settle = inspect.signature(calculate_lockin_settle_s)
        self.assertEqual(
            list(sig_settle.parameters.keys()),
            ["settle_s", "time_constants_s", "settle_taus"],
        )
        self.assertEqual(sig_settle.parameters["settle_taus"].default, DEFAULT_LOCKIN_SETTLE_TAUS)

        sig_text = inspect.signature(format_lockin_settle_text)
        self.assertEqual(
            list(sig_text.parameters.keys()),
            ["settle_s", "effective_s", "slowest_tc_s", "meter", "settle_taus"],
        )
        self.assertEqual(sig_text.parameters["settle_taus"].default, DEFAULT_LOCKIN_SETTLE_TAUS)

    def test_no_misleading_ppms_aliases_in_transport_common(self):
        """Requirement 6: transport.common should not export misleading ppms_* compatibility aliases."""
        import transport.common as common
        import transport.common.settling as settling

        for name in ("ppms_lockin_settle_s", "ppms_settle_status_text", "ppms_lockin_settle_text"):
            self.assertFalse(hasattr(common, name), f"Misleading alias {name} found in transport.common")
            self.assertFalse(hasattr(settling, name), f"Misleading alias {name} found in transport.common.settling")

    def test_calculate_lockin_settle_default_rule(self):
        self.assertEqual(calculate_lockin_settle_s(0.5, [1.0]), 1.0)
        self.assertEqual(calculate_lockin_settle_s(0.2, [0.3]), 0.3)
        self.assertEqual(calculate_lockin_settle_s(0.5, [2.0]), 2.0)
        self.assertEqual(calculate_lockin_settle_s(0.5, [1.0, 3.0]), 3.0)
        self.assertEqual(calculate_lockin_settle_s(0.5, [0.3]), 0.5)
        self.assertEqual(calculate_lockin_settle_s(30.0, [1.0, 3.0]), 30.0)
        self.assertEqual(calculate_lockin_settle_s(0.5, []), 0.5)
        self.assertEqual(calculate_lockin_settle_s(0.5, [0.0]), 0.5)

    def test_calculate_lockin_settle_custom_taus(self):
        self.assertEqual(calculate_lockin_settle_s(0.5, [1.0], 7.0), 7.0)
        self.assertEqual(calculate_lockin_settle_s(0.5, [0.1], 7.0), 0.7)
        self.assertEqual(calculate_lockin_settle_s(10.0, [1.0], 7.0), 10.0)
        self.assertEqual(calculate_lockin_settle_s(0.5, [1.0], settle_taus=7.0), 7.0)
        self.assertEqual(calculate_lockin_settle_s(0.5, [0.1], settle_taus=7.0), 0.7)
        self.assertEqual(calculate_lockin_settle_s(0.5, [1.0], settle_taus=0.0), 0.5)

    def test_calculate_lockin_settle_numerical_rounding(self):
        self.assertEqual(str(calculate_lockin_settle_s(0.5, [0.1], settle_taus=7.0)), "0.7")
        self.assertEqual(str(calculate_lockin_settle_s(0.5, [0.3], settle_taus=7.0)), "2.1")
        self.assertEqual(str(calculate_lockin_settle_s(0.0, [1e-5], settle_taus=7.0)), "7e-05")
        self.assertEqual(str(calculate_lockin_settle_s(0.1, [0.03], settle_taus=7.0)), "0.21")
        self.assertEqual(str(calculate_lockin_settle_s(0.0, [3e-5], settle_taus=7.0)), "0.00021")
        self.assertEqual(str(calculate_lockin_settle_s(0.0, [1e-5])), "1e-05")

    def test_format_settle_status_text(self):
        self.assertEqual(format_settle_status_text(7.0), "7")
        self.assertEqual(format_settle_status_text(0.7), "0.7")
        self.assertEqual(format_settle_status_text(2.1), "2.1")
        self.assertEqual(format_settle_status_text(21.0), "21")
        self.assertEqual(format_settle_status_text(70.0), "70")
        self.assertEqual(format_settle_status_text(12.34), "12.3")
        self.assertEqual(format_settle_status_text(99.94), "99.9")
        self.assertEqual(format_settle_status_text(0.0864199), "0.0864")
        self.assertEqual(format_settle_status_text(7e-05), "7e-05")
        self.assertEqual(format_settle_status_text(0.0), "0")
        self.assertEqual(format_settle_status_text(123.4), "123")
        self.assertEqual(format_settle_status_text(12345.7), "12346")
        self.assertEqual(format_settle_status_text(86400.0), "86400")

if __name__ == "__main__":
    unittest.main()
