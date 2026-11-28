"""Unified PPMS remote sample-environment controller (QDInstrument / MultiPyVu)."""

from __future__ import annotations

import threading
from typing import Callable

from transport.ppms.controllers.multivu import PPMSMultiVuReadbackController
from transport.ppms.controllers.qdinstrument import PPMSQDInstrumentReadbackController

PPMS_DEFAULT_HOST = "192.168.137.5"
PPMS_HOST_CHOICES = ("192.168.137.5", "192.168.137.163")
PPMS_QDINSTRUMENT_BACKEND = "QDInstrument remote"
PPMS_MULTIPYVU_BACKEND = "MultiPyVu"
PPMS_DEFAULT_BACKEND = PPMS_QDINSTRUMENT_BACKEND
PPMS_QDINSTRUMENT_DEFAULT_PORT = 11000
PPMS_MULTIPYVU_DEFAULT_PORT = 5000
PPMS_DEFAULT_PORT = PPMS_QDINSTRUMENT_DEFAULT_PORT


class PPMSRemoteReadbackController:
    """Backend-agnostic PPMS environment controller delegating to QDInstrument or MultiPyVu."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self.logger = logger
        self.active = None
        self.backend = ""
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.active is not None and self.active.connected

    def connect(
        self, backend: str, host: str, port: int, dll_path: str = ""
    ) -> dict[str, object]:
        with self.lock:
            if self.connected:
                return self.active.read_environment()
            if backend == PPMS_QDINSTRUMENT_BACKEND:
                controller = PPMSQDInstrumentReadbackController(self.logger)
                result = controller.connect(host, port, dll_path)
            elif backend == PPMS_MULTIPYVU_BACKEND:
                controller = PPMSMultiVuReadbackController(self.logger)
                result = controller.connect(host, port)
            else:
                raise ValueError(f"Unknown PPMS interface: {backend}")
            self.active = controller
            self.backend = backend
            return result

    def read_environment(self) -> dict[str, object]:
        with self.lock:
            if not self.connected:
                raise RuntimeError("PPMS remote interface is not connected")
            return self.active.read_environment()

    def set_temperature(
        self, target_k: float, rate_k_min: float, approach_code: int
    ) -> dict[str, object]:
        with self.lock:
            if not self.connected:
                raise RuntimeError("PPMS remote interface is not connected")
            return self.active.set_temperature(target_k, rate_k_min, approach_code)

    def set_field(
        self, target_oe: float, rate_oe_s: float, approach_code: int, mode_code: int
    ) -> dict[str, object]:
        with self.lock:
            if not self.connected:
                raise RuntimeError("PPMS remote interface is not connected")
            return self.active.set_field(
                target_oe, rate_oe_s, approach_code, mode_code
            )

    def disconnect(self) -> None:
        with self.lock:
            controller = self.active
            self.active = None
            self.backend = ""
            if controller is not None:
                controller.disconnect()


__all__ = [
    "PPMSRemoteReadbackController",
    "PPMS_DEFAULT_BACKEND",
    "PPMS_DEFAULT_HOST",
    "PPMS_DEFAULT_PORT",
    "PPMS_HOST_CHOICES",
    "PPMS_MULTIPYVU_BACKEND",
    "PPMS_MULTIPYVU_DEFAULT_PORT",
    "PPMS_QDINSTRUMENT_BACKEND",
    "PPMS_QDINSTRUMENT_DEFAULT_PORT",
]
