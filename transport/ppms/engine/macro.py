"""Tk-independent PPMS Macro validation and sequential orchestration."""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Protocol

from transport.ppms.engine.errors import PPMSScanCancelled
from transport.ppms.engine.planning import parse_ppms_macro
from transport.ppms.protocol import (
    PPMS_TEMPERATURE_LOW_BOUNDARY_K,
    PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN,
    PPMS_TEMPERATURE_MAX_RATE_K_MIN,
    validate_ppms_temperature_command,
)


class MacroEventSink(Protocol):
    """Callback contract for Macro UI updates and scan delegation (zero Tk types)."""

    def on_macro_status(self, text: str) -> None: ...
    def on_macro_line(self, line: int, op: str, args: tuple) -> None: ...
    def on_macro_temp_target(self, target_k: float, rate_k_min: float | None) -> None: ...
    def on_macro_field_target(self, target_oe: float, owner: str) -> None: ...
    def on_macro_readback(self, data: dict[str, object]) -> None: ...
    def on_macro_busy(self, busy: bool) -> None: ...
    def on_macro_finished(self, message: str) -> None: ...
    def log(self, text: str) -> None: ...


class MacroRunner:
    """Executes parsed PPMS Macro commands sequentially on the PPMS executor."""

    def __init__(
        self,
        *,
        environment_controller,
        executor,
        macro_stop_event: threading.Event,
        is_closed: Callable[[], bool],
        field_ready_fn: Callable[..., bool],
        stop_scan_fn: Callable[[], None],
        post_ui: Callable[[Callable[[], None]], None],
    ) -> None:
        self.environment = environment_controller
        self.executor = executor
        self.macro_stop_event = macro_stop_event
        self.is_closed = is_closed
        self.field_ready_fn = field_ready_fn
        self.stop_scan_fn = stop_scan_fn
        self.post_ui = post_ui
        self.running = False
        self.scan_result: bool | None = None
        self._commands = iter(())
        self._sink: MacroEventSink | None = None
        self._start_scan_cb: Callable[[], bool] | None = None
        self._is_scan_running_cb: Callable[[], bool] | None = None
        self._schedule_poll: Callable[[int, Callable[[], None]], None] | None = None

    @staticmethod
    def validate(text: str) -> list[tuple[int, str, tuple]]:
        return parse_ppms_macro(text)

    def start(
        self,
        commands: list[tuple[int, str, tuple]],
        sink: MacroEventSink,
        *,
        start_scan: Callable[[], bool],
        is_scan_running: Callable[[], bool],
        schedule_poll: Callable[[int, Callable[[], None]], None],
    ) -> None:
        self.running = True
        self.macro_stop_event.clear()
        self.scan_result = None
        self._commands = iter(commands)
        self._sink = sink
        self._start_scan_cb = start_scan
        self._is_scan_running_cb = is_scan_running
        self._schedule_poll = schedule_poll
        self.advance()

    def stop(self) -> None:
        if self.running:
            self.macro_stop_event.set()
            self.stop_scan_fn()
            if self._sink is not None:
                self._sink.on_macro_status(
                    "Stopping Macro; waiting for the current command to return, remaining commands will not run."
                )

    def _finish(self, message: str) -> None:
        self.running = False
        if self._sink is not None:
            self._sink.on_macro_finished(message)

    def advance(self) -> None:
        sink = self._sink
        if sink is None:
            return
        if self.is_closed() or self.macro_stop_event.is_set():
            self._finish("Macro stopped; settings already sent are kept.")
            return
        command = next(self._commands, None)
        if command is None:
            self._finish("Macro completed.")
            return
        line, op, args = command
        missing = "auto" if op == "TEMP" else "-"
        shown = " ".join(missing if arg is None else str(arg) for arg in args)
        sink.on_macro_status(f"Running line {line}: {op} {shown}")
        sink.on_macro_line(line, op, args)
        sink.log(f"PPMS Macro line {line}: {op} {args}")
        if op == "SCAN":
            self.scan_result = None
            try:
                started = bool(self._start_scan_cb()) if self._start_scan_cb else False
            except Exception as error:
                self._finish(f"Line {line}: scan failed to start: {error}")
                return
            if not started or not (
                self._is_scan_running_cb and self._is_scan_running_cb()
            ):
                self._finish(
                    f"Line {line}: scan not started or save cancelled; Macro stopped."
                )
                return

            def poll_scan() -> None:
                if self.is_closed() or self.macro_stop_event.is_set():
                    self._finish("Macro stopped; settings already sent are kept.")
                    return
                if self._is_scan_running_cb and self._is_scan_running_cb():
                    if self._schedule_poll is not None:
                        self._schedule_poll(100, poll_scan)
                elif self.scan_result is True:
                    self.advance()
                else:
                    self._finish(
                        f"Line {line}: scan stopped or failed; Macro stopped."
                    )

            if self._schedule_poll is not None:
                self._schedule_poll(100, poll_scan)
            return

        if op == "TEMP":
            sink.on_macro_temp_target(float(args[0]), args[1])
        elif op == "FIELD":
            sink.on_macro_field_target(float(args[0]), "The Macro")

        sink.on_macro_busy(True)
        try:
            future = self.executor.submit(self.execute_operation, op, args)
        except Exception as error:
            sink.on_macro_busy(False)
            self._finish(f"Line {line} not started: {error}")
            return

        def finished() -> None:
            sink.on_macro_busy(False)
            try:
                future.result()
            except Exception as error:
                self._finish(f"Line {line} stopped: {error}")
                return
            if self._schedule_poll is not None:
                self._schedule_poll(0, self.advance)
            else:
                self.advance()

        future.add_done_callback(lambda _future: self.post_ui(finished))

    def execute_operation(self, op: str, args: tuple) -> None:
        stop = self.macro_stop_event
        if stop.is_set() or self.is_closed():
            raise PPMSScanCancelled("Macro stopped")
        remote = self.environment
        if op == "TEMP":
            current = float(remote.read_environment()["temperature_k"])
            target, rate, approach = args
            if rate is None:
                rate = (
                    PPMS_TEMPERATURE_MAX_RATE_K_MIN
                    if math.isfinite(current)
                    and min(current, target) > PPMS_TEMPERATURE_LOW_BOUNDARY_K
                    else PPMS_TEMPERATURE_LOW_MAX_RATE_K_MIN
                )
                if self._sink is not None:
                    self._sink.log(
                        f"Macro TEMP {target:g} K without a rate: {rate:g} K/min (live {current:g} K)"
                    )
            validate_ppms_temperature_command(target, rate, current)
            remote.set_temperature(target, rate, approach)
            return
        if op == "FIELD":
            remote.set_field(*args)
            return

        delay = args[0]
        deadline = time.monotonic() + 14400 + delay
        stable_since = None
        while True:
            if stop.is_set() or self.is_closed():
                raise PPMSScanCancelled("Macro stopped")
            ready = True
            if op == "WAITFOR":
                data = remote.read_environment()
                if self._sink is not None:
                    snapshot = dict(data)
                    self.post_ui(lambda d=snapshot: self._sink.on_macro_readback(d))
                if args[1] is not None:
                    ready = (
                        abs(float(data["temperature_k"]) - args[1]) <= 0.1
                        and str(data["temperature_status"])
                        .lower()
                        .replace(" ", "")
                        in ("stable", "stableattarget")
                    )
                if args[2] is not None:
                    ready = ready and self.field_ready_fn(
                        data, args[2], driven=args[3] == 1
                    )
                    if args[3] == 0:
                        ready = ready and str(data["field_status"]).lower().replace(
                            " ", ""
                        ) in ("persistent", "stablepersistent")
            now = time.monotonic()
            stable_since = (now if stable_since is None else stable_since) if ready else None
            if stable_since is not None and now - stable_since >= delay:
                return
            if now >= deadline:
                raise TimeoutError(
                    "Timed out waiting for temperature/field to stabilise"
                )
            stop.wait(0.2)


__all__ = [
    "MacroEventSink",
    "MacroRunner",
]
