"""Run LenkRaster Studio with ``python -m lenkraster.studio``."""

from __future__ import annotations

import sys

from .app import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
