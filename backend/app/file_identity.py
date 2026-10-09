"""File identity and atomic replacement primitives; each caller owns its stronger path safeguards."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile


def regular_file_identity(value: os.stat_result) -> tuple[int, int, int] | None:
    if not stat.S_ISREG(value.st_mode):
        return None
    return (int(value.st_ino), int(value.st_mtime_ns), int(value.st_size))


def path_file_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        return regular_file_identity(os.stat(path, follow_symlinks=False))
    except OSError:
        return None


def replace_file_atomically(path: Path, data: bytes) -> None:
    """Write beside ``path``, fsync, then rename over it so readers see old or new bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
