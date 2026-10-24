#!/usr/bin/env python3
"""fsutil.py — Atomic filesystem helpers for caelestia-material-cursor.

Every state/config write goes through these helpers so an interrupted sync
(or two concurrent hooks) can never leave a truncated JSON/Lua file behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write(path: Path | str, data: str | bytes, mode: int | None = None) -> None:
    """Write *data* to *path* atomically (same-directory temp file + os.replace)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_json_dump(path: Path | str, obj: Any, indent: int = 2) -> None:
    """Serialize *obj* as JSON and write it atomically."""
    atomic_write(path, json.dumps(obj, indent=indent, ensure_ascii=False) + "\n")


def atomic_json_load(path: Path | str, default: Any = None) -> Any:
    """Load JSON, returning *default* when the file is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default
