from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path


class ProcessFileLock:
    """Non-blocking process lock backed by ``fcntl.flock``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._descriptor: int | None = None

    @property
    def held(self) -> bool:
        return self._descriptor is not None

    def acquire(self, owner_id: str) -> bool:
        if self._descriptor is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    os.close(descriptor)
                    return False
                raise
            marker = (owner_id[:200] + "\n").encode("utf-8", errors="replace")
            os.ftruncate(descriptor, 0)
            os.write(descriptor, marker)
            os.fsync(descriptor)
        except Exception:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
            raise
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "ProcessFileLock":
        if not self.acquire(str(os.getpid())):
            raise BlockingIOError("worker process lock is already held")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


__all__ = ["ProcessFileLock"]
