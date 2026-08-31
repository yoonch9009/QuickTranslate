from __future__ import annotations

# ruff: noqa: I001 - the local src path must be active before app import

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quicktranslate.app import main


if __name__ == "__main__":
    raise SystemExit(main())
