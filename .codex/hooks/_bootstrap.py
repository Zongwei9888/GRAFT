from __future__ import annotations

import sys
from pathlib import Path


def add_project_source() -> None:
    project_root = Path(__file__).resolve().parents[2]
    source = project_root / "src"
    sys.path.insert(0, str(source))
