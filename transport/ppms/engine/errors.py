"""Shared exceptions and status constants for PPMS measurement engines."""

from __future__ import annotations

PPMS_FIELD_NOT_ZEROED_TEXT = "field NOT zeroed: check MultiVu"


class PPMSFieldScanError(RuntimeError):
    """Raised when a PPMS field setpoint or settle operation fails."""


class PPMSMotorScanError(RuntimeError):
    """Raised when a PPMS rotator readback or move fails during a scan."""


class PPMSLockinRangeError(RuntimeError):
    """Raised when a lock-in amplifier hits unrecoverable overload or autorange timeout."""


class PPMSScanCancelled(RuntimeError):
    """Raised when a scan, motor move, or macro step is cancelled by user/close."""


__all__ = [
    "PPMSFieldScanError",
    "PPMSLockinRangeError",
    "PPMSMotorScanError",
    "PPMSScanCancelled",
    "PPMS_FIELD_NOT_ZEROED_TEXT",
]
