"""Hardware-specific exceptions; no physical I/O or GUI dependencies.

Importable without constructing a source, magnet or stage controller.
"""

import math


class CurrentZurichAPIMissingError(RuntimeError):
    """The interpreter running the GUI does not contain zhinst-core."""


class AvtechPulseError(RuntimeError):
    """A single pulse that failed; ``fired`` says whether the trigger had
    been sent (the sample may have had the pulse), with what was read back."""

    def __init__(self, message: str, *, fired: bool = False, amplitude_v: float = math.nan,
                 width_s: float = math.nan, overload: bool | None = False) -> None:
        super().__init__(message)
        self.fired = fired
        self.amplitude_v = amplitude_v
        self.width_s = width_s
        self.overload = overload


class LakeShoreRampStopped(RuntimeError):
    """A Lake Shore ramp ended by a Stop: STOP was sent (unless nothing had
    been) and the output left where it stopped, ``current_a`` (RDGI? read
    after it, or nan)."""

    def __init__(self, message: str, current_a: float = math.nan) -> None:
        super().__init__(message)
        self.current_a = current_a


class LakeShoreRampError(RuntimeError):
    """A Lake Shore ramp that failed: an error bit, compliance, a time-out,
    a read-back that does not match.  STOP was sent once a setpoint had
    been, and the output is left at its last reading, ``current_a``; no
    further setpoint is sent except by the zero rules of the pages."""

    def __init__(self, message: str, current_a: float = math.nan) -> None:
        super().__init__(message)
        self.current_a = current_a


class LakeShoreZeroError(LakeShoreRampError):
    """A Lake Shore zero (SETI 0) that was not confirmed: |RDGI?| did not
    come under the tolerance, or SETI? did not read back 0."""


class MagnetProtocolError(RuntimeError):
    """A magnet device replied without the expected value."""


class LEDProtocolError(RuntimeError):
    pass


@dataclass


class NewmarkStageError(RuntimeError):
    """Anything that went wrong with the Newmark NSC-A1 stage."""


class NewmarkCommandNotAllowed(NewmarkStageError):
    """A command outside the allowlist, or a number out of range; it was not sent."""


class NewmarkControllerError(NewmarkStageError):
    """The controller answered "?..." (it did not do it)."""


class NewmarkRefused(NewmarkStageError):
    """A precondition was not met; nothing moved."""


class NewmarkDriverOff(NewmarkRefused):
    """The motor driver is off (EO != 1): the move was not sent.  The RT-E page
    offers to switch it on, EO=1 after an explicit Yes (user, 2026-10-17), and
    so does the STFMR page at the Start of a sweep with angles (user,
    2026-10-17)."""

    def __init__(self, eo):
        self.eo = eo
        super().__init__(f"the motor driver is off (EO={eo}): the position counter would advance without motion, so "
                         "the move was not sent. To switch it on, press Move stage or Start an Angle scan on the SHV "
                         "page, or Start a sweep with the motor on the STFMR page, and answer Yes (EO=1)")


# The two refusals the RT-E and STFMR pages can fix themselves, after a Yes
# (user, 2026-10-17).
NEWMARK_FIXABLE_REFUSALS = (NewmarkLimitErrorLatched, NewmarkDriverOff)


class NewmarkMotionError(NewmarkStageError):
    """A move that failed after it was sent.  ``stop_ran``: the STOP sequence
    already ran for it (time-out, lost communication, a failed reply to X);
    otherwise the move had ended (PX off the target, a limit error)."""

    def __init__(self, message, stopped, px=None, notes=(), stop_ran=False):
        super().__init__(message)
        self.stopped, self.px, self.notes, self.stop_ran = stopped, px, list(notes), stop_ran
