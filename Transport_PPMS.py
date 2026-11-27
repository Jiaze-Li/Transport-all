"""Transport_PPMS: the PPMS page of Transport_all as a program of its own.

    python Transport_PPMS.py

The application is the ``transport`` package beside this file
(``transport/ppms/app.py`` and the shared modules it imports).  It does not
need Transport_all.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from transport.ppms.app import main  # noqa: E402

if __name__ == "__main__":
    main()
