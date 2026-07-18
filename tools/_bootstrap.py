from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ensure_repository_on_path() -> Path:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return ROOT
