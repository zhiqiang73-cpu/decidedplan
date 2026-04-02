"""Small file I/O helpers for the live runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_file(path: str | Path, default: Any) -> Any:
    """Read JSON from disk and return ``default`` on any error."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_text_atomic(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Atomically replace a text file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(target)


def write_json_atomic(
    path: str | Path,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
) -> None:
    """Atomically write JSON to disk."""
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent),
        encoding="utf-8",
    )
