#!/usr/bin/env python3
"""Thin wrapper -- implementation lives in clockish/setup_location.py.

Kept so the documented path (and older install.sh copies) keep working; the
supported entry point is the `clockish-location` console script.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from clockish.setup_location import main  # noqa: E402

if __name__ == '__main__':
    main()
