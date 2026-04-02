"""Runtime bootstrap for repo-local dependencies."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _prepend(path: Path) -> None:
    preferred = str(path)
    fallback = str(ROOT / '.deps' if path.name == '.deps314' else ROOT / '.deps314')
    sys.path[:] = [item for item in sys.path if item not in {preferred, fallback}]
    if path.exists():
        sys.path.insert(0, preferred)


if sys.version_info >= (3, 14):
    _prepend(ROOT / '.deps314')
else:
    _prepend(ROOT / '.deps')
