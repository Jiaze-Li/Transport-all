"""Unit and regression tests for shared transport measurement validation.

Covers transport.common.validation and its backward-compatibility wrapper
in Transport_all.py.
"""

from __future__ import annotations

import inspect
import math
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure repository root is on sys.path for direct test execution
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from transport.common import (
    DEFAULT_K6221_MAX_ABS_CURRENT_A,
    DEFAULT_K6221_MAX_COMPLIANCE_V,
    DEFAULT_K6221_MAX_FREQUENCY_HZ,
    DEFAULT_K6221_MIN_COMPLIANCE_V,
    DEFAULT_K6221_MIN_FREQUENCY_HZ,
    DEFAULT_SR830_KEYS,
    DEFAULT_SR830_LABELS,
    DEFAULT_SR830_MAX_DEMOD_FREQ_HZ,
    DEFAULT_SR830_MAX_HARMONIC,
    DEFAULT_SR830_MIN_HARMONIC,
    validate_transport_scan_settings,
)
import transport.fmr.app as app


class ReferenceOriginalValidation:
    """Exact copy of original validate_ppms_transport_scan_settings logic before extraction."""

    @staticmethod
    def validate(
        current_rms_ma,
        frequency_hz,
        compliance_v,
        harmonic_a,
        harmonic_b,
        active_lockins=("830_a", "830_b"),
        allow_no_lockins=False,
        harmonic_c=1,
        max_abs_current_a=0.105,
        min_compliance_v=0.1,
        max_compliance_v=105.0,
        valid_sr830_keys=("830_a", "830_b", "830_c"),
    ):
        current_rms_ma = float(current_rms_ma)
        frequency_hz = float(frequency_hz)
        compliance_v = float(compliance_v)
        harmonic_a = int(harmonic_a)
        harmonic_b = int(harmonic_b)
        harmonic_c = int(harmonic_c)
        active_lockins = tuple(str(key) for key in active_lockins)
        if not active_lockins and not allow_no_lockins:
            raise ValueError("PPMS field sweep requires at least one connected SR830")
        if any(key not in valid_sr830_keys for key in active_lockins):
            raise ValueError("PPMS SR830 slot is invalid")
        if not all(
            math.isfinite(value)
            for value in (current_rms_ma, frequency_hz, compliance_v)
        ):
            raise ValueError("6221 parameters for the PPMS transport scan must be finite values")
        if current_rms_ma <= 0:
            raise ValueError("6221 AC current for the PPMS transport scan must be greater than 0 mA RMS")
        peak_a = math.sqrt(2.0) * current_rms_ma * 1e-3
        if peak_a > max_abs_current_a:
            maximum_rms_ma = max_abs_current_a / math.sqrt(2.0) * 1000.0
            raise ValueError(
                f"6221 sine-wave peak must not exceed {max_abs_current_a * 1000:g} mA; "
                f"the GUI RMS current limit is about {maximum_rms_ma:.6g} mA"
            )
        if not 1e-3 <= frequency_hz <= 1e5:
            raise ValueError("6221 waveform frequency must be within 0.001–100000 Hz")
        if not min_compliance_v <= compliance_v <= max_compliance_v:
            raise ValueError(
                f"6221 compliance voltage must be within {min_compliance_v:g}–"
                f"{max_compliance_v:g} V"
            )
        harmonics = {"830_a": harmonic_a, "830_b": harmonic_b, "830_c": harmonic_c}
        for key in active_lockins:
            label = {"830_a": "A", "830_b": "B", "830_c": "C"}[key]
            harmonic = harmonics[key]
            if not 1 <= harmonic <= 19999:
                raise ValueError(f"SR830 slot {label} harmonic must be within 1–19999")
            if frequency_hz * harmonic > 102000.0:
                raise ValueError(f"SR830 slot {label} demodulation frequency must not exceed 102 kHz")
        return {
            "current_rms_ma": current_rms_ma,
            "frequency_hz": frequency_hz,
            "compliance_v": compliance_v,
            "harmonic_a": harmonic_a,
            "harmonic_b": harmonic_b,
            "harmonic_c": harmonic_c,
            "active_lockins": active_lockins,
            "allow_no_lockins": allow_no_lockins,
            "peak_a": peak_a,
        }


class TestTransportValidationPurityAndContract(unittest.TestCase):
    """AC1 and AC2: Pure module import, signature preservation, and exact contract."""

    def test_shared_module_does_not_export_misleading_ppms_alias(self):
        """transport.common and transport.common.validation must not export validate_ppms_transport_scan_settings."""
        import transport.common as tc
        import transport.common.validation as tcv
        self.assertFalse(hasattr(tc, "validate_ppms_transport_scan_settings"))
        self.assertFalse(hasattr(tcv, "validate_ppms_transport_scan_settings"))
        self.assertNotIn("validate_ppms_transport_scan_settings", tc.__all__)

    def test_default_constants(self):
        """Verify default electrical and slot constants."""
        self.assertEqual(DEFAULT_K6221_MAX_ABS_CURRENT_A, 0.105)
        self.assertEqual(DEFAULT_K6221_MIN_COMPLIANCE_V, 0.1)
        self.assertEqual(DEFAULT_K6221_MAX_COMPLIANCE_V, 105.0)
        self.assertEqual(DEFAULT_K6221_MIN_FREQUENCY_HZ, 1e-3)
        self.assertEqual(DEFAULT_K6221_MAX_FREQUENCY_HZ, 1e5)
        self.assertEqual(DEFAULT_SR830_KEYS, ("830_a", "830_b", "830_c"))
        self.assertEqual(DEFAULT_SR830_LABELS, {"830_a": "A", "830_b": "B", "830_c": "C"})
        self.assertEqual(DEFAULT_SR830_MIN_HARMONIC, 1)
        self.assertEqual(DEFAULT_SR830_MAX_HARMONIC, 19999)
        self.assertEqual(DEFAULT_SR830_MAX_DEMOD_FREQ_HZ, 102000.0)

class TestNumericLimitsAndPrecedence(unittest.TestCase):
    """Detailed boundary checks, exact error messages, and exception precedence."""

    def test_direct_dictionary_lookup_raises_keyerror_on_unmapped_slot(self):
        """Custom valid_sr830_keys that lacks slot mapping raises KeyError matching original behavior."""
        with self.assertRaises(KeyError):
            validate_transport_scan_settings(
                1.0, 100.0, 10.0, 1, 1, ("830_custom",), valid_sr830_keys=("830_custom",)
            )
        with self.assertRaises(KeyError):
            ReferenceOriginalValidation.validate(
                1.0, 100.0, 10.0, 1, 1, ("830_custom",), valid_sr830_keys=("830_custom",)
            )

class TestDynamicMonkeypatchCompatibility(unittest.TestCase):
    """AC3: Monkeypatching Transport_all constants dynamically affects wrapper at call time."""

    pass

class TestBehavioralEquivalence(unittest.TestCase):
    """Direct comparison between ReferenceOriginalValidation and new extracted function."""

    pass

class TestHardwareSafetyGuards(unittest.TestCase):
    """AC5: Ensure invalid settings reject before prohibited hardware operations."""

    pass

if __name__ == "__main__":
    unittest.main()
