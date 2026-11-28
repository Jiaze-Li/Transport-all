"""Standalone PPMS application entry facade (authoritative source in transport/ppms/)."""

from __future__ import annotations

from transport.ppms.app_shell import (
    APP_VERSION,
    AppShell,
    MAIN_WINDOW_TITLE,
    main,
    maximize_main_window,
    run,
)
from transport.ppms.state import PPMSAppState
from transport.ppms.ui.panel import PPMSPanelView
from transport.ppms.ui.theme import calculate_window_layout

__all__ = [
    "APP_VERSION",
    "AppShell",
    "MAIN_WINDOW_TITLE",
    "PPMSAppState",
    "PPMSPanelView",
    "calculate_window_layout",
    "main",
    "maximize_main_window",
    "run",
]

if __name__ == "__main__":
    main()
