"""
main.py — Thin launcher for VISOR.

All application logic lives in ``src/visor/runtime.py``. This file stays
at the project root because ``VISOR.vbs`` and ``install.bat`` launch it
by path. Its only job is to put ``src/`` on ``sys.path`` (so the bare
``config`` / ``monitor`` modules and the ``visor`` package resolve) and
hand off to :func:`visor.runtime.main`.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from visor.runtime import main

if __name__ == "__main__":
    main()
