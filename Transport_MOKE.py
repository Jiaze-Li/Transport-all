"""Transport_MOKE: the MOKE light, MOKE motor, MOKE magnet, CIDWM and MOKE-24 pages of Transport_all as a program of its own.

    python Transport_MOKE.py

The application is the ``transport`` package beside this file
(``transport/moke/app.py`` and the shared modules it imports).  It does not
need Transport_all.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from transport.moke.app import run  # noqa: E402

if __name__ == "__main__":
    run()      # --pco-dll-self-test, or the program
