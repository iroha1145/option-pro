"""Keep yfinance's per-ticker thread creation inside a small batch boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import threading
from typing import Any

import pandas as pd


YFINANCE_TICKER_BATCH_SIZE = 8
YFINANCE_MAX_CONCURRENT_DOWNLOADS = 4
_download_gate = threading.BoundedSemaphore(YFINANCE_MAX_CONCURRENT_DOWNLOADS)


class YFinanceBatchBusy(RuntimeError):
    """Raised instead of queueing another download behind abandoned work."""


def _ticker_list(tickers: str | Iterable[str]) -> list[str]:
    values = tickers.replace(",", " ").split() if isinstance(tickers, str) else tickers
    symbols: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value).strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _normalize_batch_index(frame: pd.DataFrame) -> pd.DataFrame:
    """Give timezone-aware batches one shared index timezone before merging."""

    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        return frame
    normalized = frame.copy()
    normalized.index = frame.index.tz_convert("UTC")
    return normalized


def download_in_bounded_batches(
    download: Callable[..., pd.DataFrame | None],
    *,
    tickers: str | Iterable[str],
    batch_size: int = YFINANCE_TICKER_BATCH_SIZE,
    **kwargs: Any,
) -> pd.DataFrame:
    """Download ticker-grouped frames without submitting the whole universe at once.

    yfinance 1.5 creates one operating-system thread for every submitted ticker;
    its ``threads`` integer only limits how many of those threads enter the
    downloader concurrently. Sequential batches keep the number of created
    threads bounded while preserving yfinance's ticker-grouped frame shape.
    """

    if isinstance(batch_size, bool) or batch_size < 1 or batch_size > 64:
        raise ValueError("batch_size must be between 1 and 64")
    if "threads" in kwargs:
        raise TypeError("threads is controlled by download_in_bounded_batches")
    if "multi_level_index" in kwargs:
        raise TypeError(
            "multi_level_index is controlled by download_in_bounded_batches"
        )
    if kwargs.get("group_by") != "ticker":
        raise ValueError("bounded yfinance downloads require group_by='ticker'")

    symbols = _ticker_list(tickers)
    if not symbols:
        return pd.DataFrame()

    if not _download_gate.acquire(blocking=False):
        raise YFinanceBatchBusy("yfinance batch capacity is busy")
    try:
        frames: list[pd.DataFrame] = []
        first_error: Exception | None = None
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset : offset + batch_size]
            try:
                frame = download(
                    tickers=" ".join(batch),
                    threads=len(batch),
                    multi_level_index=True,
                    **kwargs,
                )
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                continue
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            if not isinstance(frame.columns, pd.MultiIndex):
                if len(batch) != 1:
                    raise ValueError(
                        "multi-ticker yfinance response must use MultiIndex columns"
                    )
                frame = frame.copy()
                frame.columns = pd.MultiIndex.from_product(
                    [batch, frame.columns],
                    names=["Ticker", frame.columns.name or "Price"],
                )
            frames.append(_normalize_batch_index(frame))
    finally:
        _download_gate.release()

    if not frames:
        if first_error is not None:
            raise first_error
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0]
    merged = pd.concat(frames, axis=1).sort_index()
    return merged.loc[:, ~merged.columns.duplicated()]


__all__ = [
    "YFINANCE_MAX_CONCURRENT_DOWNLOADS",
    "YFINANCE_TICKER_BATCH_SIZE",
    "YFinanceBatchBusy",
    "download_in_bounded_batches",
]
