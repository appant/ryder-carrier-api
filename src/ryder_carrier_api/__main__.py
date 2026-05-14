"""Module entry point so `python -m ryder_carrier_api <job>` works in Docker."""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main())
