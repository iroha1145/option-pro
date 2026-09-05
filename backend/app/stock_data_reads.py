"""Shared reads of the newest valid manual or worker stock resource."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.public_stock_data import read_public_stock_resource
from app.stock_pull_snapshot import read_stock_pull_resource


def read_latest_stock_resource(
    ticker: str,
    resource: str,
    *,
    path: Path | None = None,
    root: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Compare resource timestamps, never unrelated snapshot file mtimes.

    Explicit paths retain the manual reader's isolated-file semantics. Invalid
    or expired worker data cannot hide a usable manual pull, and vice versa.
    """
    manual = read_stock_pull_resource(ticker, resource, path=path, now=now)
    public = None if path is not None else read_public_stock_resource(
        ticker, resource, root=root, now=now,
    )
    if public is not None and (
        manual is None or float(public["saved_at"]) > float(manual["saved_at"])
    ):
        return {**public, "source": "public_stock_data"}
    return {**manual, "source": "manual_pull"} if manual is not None else None
