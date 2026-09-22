"""File identity primitives; each reader owns its stronger path safeguards."""
from __future__ import annotations

import os
from pathlib import Path
import stat


def regular_file_identity(value: os.stat_result) -> tuple[int, int, int] | None:
    if not stat.S_ISREG(value.st_mode):
        return None
    return (int(value.st_ino), int(value.st_mtime_ns), int(value.st_size))


def path_file_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        return regular_file_identity(os.stat(path, follow_symlinks=False))
    except OSError:
        return None
