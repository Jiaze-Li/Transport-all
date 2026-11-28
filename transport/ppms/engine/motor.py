"""Tk-independent PPMS horizontal rotator motor probe and service."""

from __future__ import annotations

import math
import time
from typing import Callable

from transport.ppms.controllers.environment import PPMS_QDINSTRUMENT_BACKEND
from transport.ppms.engine.errors import PPMSScanCancelled


class PPMSMotorDebug:
    """Low-level QDInstrument reflection wrapper for the PPMS Horizontal Rotator."""

    AXIS = "Horizontal Rotator"

    def __init__(self, controller) -> None:
        self.controller = controller
        self.movement_attempted = False
        self.on_read: Callable[[dict[str, object]], None] | None = None

    def _method(self, name: str, count: int):
        instrument = self.controller.instrument
        if instrument is None:
            raise RuntimeError("Please connect QDInstrument first")
        methods = [
            m
            for m in instrument.GetType().GetMethods()
            if str(m.Name) == name and len(list(m.GetParameters())) == count
        ]
        if len(methods) != 1:
            raise RuntimeError(
                f"{name}: no unique compatible interface found; please use 'Interface info' and send the log"
            )
        return methods[0]

    def describe(self) -> str:
        with self.controller.lock:
            if self.controller.instrument is None:
                raise RuntimeError("Please connect QDInstrument first")
            lines = []
            for method in self.controller.instrument.GetType().GetMethods():
                if any(
                    word in str(method.Name).lower()
                    for word in ("position", "ppmsitem", "ppmscommand")
                ):
                    lines.append(str(method))
                    for parameter in method.GetParameters():
                        typ = parameter.ParameterType
                        if getattr(typ, "IsByRef", False):
                            typ = typ.GetElementType()
                        if getattr(typ, "IsEnum", False):
                            import System

                            lines.append(
                                f"  {typ}: "
                                + ", ".join(str(n) for n in System.Enum.GetNames(typ))
                            )
            return "\n".join(lines) or "No motor-related interface found"

    @staticmethod
    def _check_return(method, value) -> None:
        typ = str(getattr(getattr(method, "ReturnType", None), "FullName", ""))
        if typ == "System.Boolean":
            good = bool(value)
        else:
            good = value is None or int(value) == 0
        if not good:
            raise RuntimeError(
                f"{method}: returned {value!r} ({typ}); not resending, please verify in MultiVu whether it was executed"
            )

    def _read_item(self, channel: int) -> float:
        import System

        with self.controller.lock:
            method = self._method("GetPPMSItem", 3)
            params = list(method.GetParameters())
            if (
                str(params[0].ParameterType.FullName) != "System.Int32"
                or not getattr(params[1].ParameterType, "IsByRef", False)
                or str(params[1].ParameterType.GetElementType().FullName)
                != "System.Double"
                or str(params[2].ParameterType.FullName) != "System.Boolean"
            ):
                raise RuntimeError(f"GetPPMSItem signature incompatible: {method}")
            arguments = System.Array[System.Object](3)
            arguments[0] = System.Int32(channel)
            arguments[1] = System.Double(0)
            arguments[2] = System.Boolean(True)
            result = method.Invoke(self.controller.instrument, arguments)
            self.controller.logger(
                f"PPMS motor GetPPMSItem({channel}): return={result!r}; value={arguments[1]!r}"
            )
            self._check_return(method, result)
            value = float(arguments[1])
            if not math.isfinite(value):
                raise RuntimeError("PPMS data item is not a finite value")
            return value

    def read(self) -> dict[str, object]:
        with self.controller.lock:
            angle = self._read_item(3)
            status = "PositionUnknown"
            try:
                word = self._read_item(0)
                if not word.is_integer() or not 0 <= word <= 65535:
                    raise ValueError(f"Invalid status word: {word}")
                code = (int(word) >> 12) & 15
                status = {
                    1: "AtTarget",
                    5: "Moving",
                    8: "AtLimitSwitch",
                    9: "AtIndexSwitch",
                    15: "PositionFailure",
                }.get(code, "PositionUnknown")
            except Exception as error:
                self.controller.logger(f"PPMS motor status unavailable: {error}")
            if not math.isfinite(angle):
                raise RuntimeError("Motor angle readback is not finite")
            self.controller.logger(
                f"PPMS motor: angle={angle:.6f} deg; status={status}"
            )
            data = {"angle_deg": angle, "status": status}
            on_read = getattr(self, "on_read", None)
            if on_read is not None:
                on_read(dict(data))
            return data

    @staticmethod
    def validate_move(
        target: float, speed: float, current: float, allow_large: bool = False
    ) -> tuple[float, int]:
        target, speed = float(target), float(speed)
        if not all(math.isfinite(v) for v in (target, speed, float(current))):
            raise ValueError("Motor parameters must be finite values")
        if not -370 <= target <= 370:
            raise ValueError("PPMS target angle must be within −370 to 370°")
        if abs(target - current) > 370.0 and not allow_large:
            raise ValueError(
                "Each move must not exceed 370°; please enter the target based on the latest angle"
            )
        if not speed.is_integer() or not 0 <= speed <= 14:
            raise ValueError(
                "Deceleration code must be an integer 0–14; 0 fastest, 14 slowest"
            )
        return target, int(speed)

    def move(
        self,
        target: float,
        speed: float,
        allow_large: bool = False,
        _index_retry: bool = False,
    ) -> dict[str, object]:
        import System

        self.movement_attempted = False
        with self.controller.lock:
            before = self.read()
            target, slowdown = self.validate_move(
                target, speed, before["angle_deg"], allow_large
            )
            if before["status"] not in ("AtTarget", "AtIndexSwitch"):
                raise RuntimeError(
                    f"Motion status is not AtTarget: {before['status']}; MOVE not sent"
                )
            method = self._method("SendPPMSCommand", 5)
            params = list(method.GetParameters())
            types_ = [p.ParameterType for p in params]
            expected = [
                "System.String",
                "System.String",
                "System.String",
                "System.Int32",
                "System.Double",
            ]
            actual = [
                str(
                    (
                        t.GetElementType() if getattr(t, "IsByRef", False) else t
                    ).FullName
                )
                for t in types_
            ]
            if actual != expected or [
                bool(getattr(t, "IsByRef", False)) for t in types_
            ] != [False, True, True, False, False]:
                raise RuntimeError(f"SendPPMSCommand signature incompatible: {method}")
            command = f"MOVE {target:.6f} 0 {slowdown}"
            args = System.Array[System.Object](5)
            args[0], args[1], args[2] = command, "", ""
            args[3], args[4] = System.Int32(0), System.Double(0)
            self.controller.logger(f"PPMS motor SEND ONCE: {command}")
            self.movement_attempted = True
            result = method.Invoke(self.controller.instrument, args)
            self.controller.logger(
                f"PPMS motor reply: return={result!r}; reply={args[1]!r}; error={args[2]!r}"
            )
            return_type = str(getattr(method.ReturnType, "FullName", ""))
            ambiguous = return_type == "System.Int32" and int(result) == 1
            if not ambiguous:
                self._check_return(method, result)
            if str(args[1] or "").strip() or str(args[2] or "").strip():
                ambiguous = True
                self.controller.logger(
                    "MOVE reply is non-empty, execution result pending readback confirmation; MOVE not resent"
                )
            return {
                "target_deg": target,
                "return_code": result,
                "ambiguous": ambiguous,
                "raw_reply": str(args[1] or ""),
                "raw_error": str(args[2] or ""),
            }

    def move_and_verify(
        self,
        target: float,
        speed: float,
        cancelled: Callable[[], bool] = lambda: False,
        timeout: float = 90.0,
        interval: float = 0.5,
        allow_large: bool = False,
        index_target: float | None = None,
    ) -> dict[str, object]:
        if cancelled():
            raise PPMSScanCancelled(
                "Cancelled before moving, MOVE not sent; this does not mean the motor is stopped"
            )
        submission = (
            self.move(target, speed, allow_large=True)
            if allow_large
            else self.move(target, speed)
        )
        deadline = time.monotonic() + timeout
        consecutive = 0
        last = None
        while time.monotonic() < deadline:
            if cancelled():
                raise PPMSScanCancelled(
                    "Arrival readback cancelled; this does not mean the motor is stopped, please check MultiVu"
                )
            try:
                last = self.read()
            except Exception as error:
                raise RuntimeError(
                    f"MOVE was sent, but the arrival readback failed and the motion status is unconfirmed; MOVE not resent, please check MultiVu: {error}"
                ) from error
            if last["status"] in (
                "AtLimitSwitch",
                "PositionFailure",
                "PositionUnknown",
            ):
                raise RuntimeError(
                    f"Abnormal motor status: {last}; MOVE not resent, please check MultiVu"
                )
            if (
                last["status"] in ("AtTarget", "AtIndexSwitch")
                and abs(last["angle_deg"] - submission["target_deg"]) <= 0.06
            ):
                consecutive += 1
                if consecutive >= 2:
                    self.controller.logger(
                        f"PPMS motor VERIFIED: {last}; submission={submission}"
                    )
                    return dict(last, **submission)
            else:
                consecutive = 0
            time.sleep(interval)
        raise RuntimeError(
            f"Motor arrival confirmation timed out ({timeout:g}s); last readback {last}; MOVE not resent, please check MultiVu"
        )


class PPMSMotorService:
    """Tk-independent motor service for manual debug operations and scan moves."""

    def __init__(self, environment_controller, probe_factory=PPMSMotorDebug) -> None:
        self.environment = environment_controller
        self.probe_factory = probe_factory
        self.pending = False
        self.expected_angle_deg: float | None = None

    def create_probe(self) -> PPMSMotorDebug:
        if (
            not self.environment.connected
            or self.environment.backend != PPMS_QDINSTRUMENT_BACKEND
        ):
            raise RuntimeError(
                "Motor debugging only supports a connected QDInstrument remote interface"
            )
        return self.probe_factory(self.environment.active)

    def confirmed_manual_move(
        self,
        probe: PPMSMotorDebug,
        target: float,
        speed: float,
        *,
        on_read: Callable[[dict[str, object]], None] | None = None,
        confirm_large_move: Callable[[float, float], bool],
        is_closed: Callable[[], bool],
    ) -> dict[str, object]:
        if on_read is not None:
            probe.on_read = on_read
        before = probe.read()
        probe.validate_move(target, speed, before["angle_deg"], allow_large=True)
        if before["status"] not in ("AtTarget", "AtIndexSwitch"):
            raise RuntimeError(
                f"Motion status is not AtTarget: {before['status']}; MOVE not sent"
            )
        large = abs(target - before["angle_deg"]) > 30.0
        if large:
            if is_closed() or not confirm_large_move(float(before["angle_deg"]), target):
                raise PPMSScanCancelled("Move cancelled; MOVE not sent")
        return probe.move_and_verify(
            target,
            speed,
            cancelled=is_closed,
            allow_large=large,
            timeout=900.0 if large else 90.0,
        )


__all__ = [
    "PPMSMotorDebug",
    "PPMSMotorService",
]
