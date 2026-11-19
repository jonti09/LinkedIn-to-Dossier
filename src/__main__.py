#!/usr/bin/env python3
"""Allow running `python3 src` or `python3 -m src`."""

import sys

try:
    from .cli import main
except ImportError:
    from cli import main

if __name__ == "__main__":
    sys.exit(main())
