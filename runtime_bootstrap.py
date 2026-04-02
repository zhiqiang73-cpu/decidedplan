"""Runtime dependency bootstrap for repo-local packages."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def bootstrap_runtime() -> None:
    if sys.version_info >= (3, 14):
        preferred = ROOT / '.deps314'
        fallback = ROOT / '.deps'
    else:
        preferred = ROOT / '.deps'
        fallback = ROOT / '.deps314'

    known_paths = {str(preferred), str(fallback)}
    sys.path[:] = [path for path in sys.path if path not in known_paths]

    if preferred.exists():
        sys.path.insert(0, str(preferred))
    elif fallback.exists():
        sys.path.insert(0, str(fallback))
