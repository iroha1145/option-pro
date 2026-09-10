"""Bounded snapshots for explicitly requested non-daily, unadjusted charts."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

from app.data_paths import get_data_paths
from app.stock_pull_snapshot import (
    STOCK_CHART_RESOURCE_RANGES,
    read_stock_pull_resource,
    validate_stock_pull_payload,
    write_stock_pull_resources,
)

MAX_SNAPSHOTS = 512
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
_RETENTION_SECONDS = 7 * 24 * 60 * 60
_TICKER = re.compile(r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$")
_FILE = re.compile(r"^.+\.(?:5m|15m|1h|1w)\.json$")


def stock_chart_snapshot_path(ticker: str, period: str, *, root: Path | None = None) -> Path:
    symbol = ticker.upper().strip()
    if not _TICKER.fullmatch(symbol) or f"chart_{period}" not in STOCK_CHART_RESOURCE_RANGES:
        raise ValueError("invalid stock chart snapshot key")
    return (root or get_data_paths().root) / "stock-chart-snapshots-v1" / f"{symbol}.{period}.json"


def read_stock_chart_resource(
    ticker: str, period: str, *, root: Path | None = None, now: float | None = None,
) -> dict[str, Any] | None:
    return read_stock_pull_resource(
        ticker, f"chart_{period}",
        path=stock_chart_snapshot_path(ticker, period, root=root), now=now,
    )


@contextmanager
def _directory_lock(directory: Path):
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("chart snapshot directory cannot be a symlink")
    directory.mkdir(parents=True, mode=0o750, exist_ok=True)
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("invalid chart snapshot lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _prune(directory: Path, keep: Path, now: float) -> None:
    entries = []
    for path in directory.iterdir():
        if not _FILE.fullmatch(path.name):
            continue
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            entries.append((path, info))
    total = sum(info.st_size for _, info in entries)
    count = len(entries)
    for path, info in sorted(entries, key=lambda item: item[1].st_mtime_ns):
        if path == keep:
            continue
        if count <= MAX_SNAPSHOTS and total <= MAX_SNAPSHOT_BYTES and info.st_mtime + _RETENTION_SECONDS > now:
            continue
        path.unlink(missing_ok=True)
        total -= info.st_size
        count -= 1


def write_stock_chart_resource(
    ticker: str, period: str, payload: Any, saved_at: float, *, root: Path | None = None,
) -> None:
    resource = f"chart_{period}"
    target = stock_chart_snapshot_path(ticker, period, root=root)
    if validate_stock_pull_payload(ticker, resource, payload) is None:
        raise ValueError("stock chart payload is unavailable")
    with _directory_lock(target.parent):
        written = write_stock_pull_resources(ticker, {resource: (payload, saved_at)}, path=target)
        if resource not in written:
            raise ValueError("stock chart snapshot was not saved")
        _prune(target.parent, target, time.time())
