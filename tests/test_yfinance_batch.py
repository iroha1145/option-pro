from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pandas as pd
import pytest

from app.services.yfinance_batch import download_in_bounded_batches
from app.services.yfinance_batch import YFinanceBatchBusy


def test_download_batches_bound_created_threads_and_preserve_ticker_columns():
    calls: list[tuple[list[str], int]] = []
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def fake_download(*, tickers: str, threads: int, **_kwargs: Any) -> pd.DataFrame:
        nonlocal active, peak
        symbols = tickers.split()
        calls.append((symbols, threads))

        def worker() -> None:
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with state_lock:
                active -= 1

        workers = [threading.Thread(target=worker) for _symbol in symbols]
        for item in workers:
            item.start()
        for item in workers:
            item.join()

        columns = pd.MultiIndex.from_product([symbols, ["Close"]])
        return pd.DataFrame([[1.0] * len(symbols)], columns=columns)

    symbols = [f"T{number:02d}" for number in range(21)]
    result = download_in_bounded_batches(
        fake_download,
        tickers=symbols,
        group_by="ticker",
        progress=False,
    )

    assert [len(batch) for batch, _threads in calls] == [8, 8, 5]
    assert all(threads == len(batch) for batch, threads in calls)
    assert peak <= 8
    assert list(dict.fromkeys(result.columns.get_level_values(0))) == symbols


def test_download_batches_reject_unsafe_or_ambiguous_overrides():
    def fake_download(**_kwargs: Any) -> pd.DataFrame:
        raise AssertionError("invalid configuration reached downloader")

    with pytest.raises(TypeError, match="threads is controlled"):
        download_in_bounded_batches(
            fake_download,
            tickers=["AAPL"],
            group_by="ticker",
            threads=1,
        )
    with pytest.raises(ValueError, match="group_by='ticker'"):
        download_in_bounded_batches(
            fake_download,
            tickers=["AAPL"],
            group_by="column",
        )
    with pytest.raises(TypeError, match="multi_level_index is controlled"):
        download_in_bounded_batches(
            fake_download,
            tickers=["AAPL"],
            group_by="ticker",
            multi_level_index=True,
        )


def test_download_batches_promote_single_ticker_flat_columns():
    def fake_download(**_kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame({"Close": [123.0]})

    result = download_in_bounded_batches(
        fake_download,
        tickers=["AAPL"],
        group_by="ticker",
    )

    assert isinstance(result.columns, pd.MultiIndex)
    assert list(result.columns) == [("AAPL", "Close")]


def test_download_batches_normalize_intraday_timezones_before_merging():
    def fake_download(*, tickers: str, **_kwargs: Any) -> pd.DataFrame:
        symbol = tickers.split()[0]
        market_timezone = (
            "America/New_York" if symbol == "AAPL" else "Asia/Tokyo"
        )
        index = pd.DatetimeIndex(["2026-07-17 09:30"], tz=market_timezone)
        columns = pd.MultiIndex.from_product([[symbol], ["Close"]])
        return pd.DataFrame([[1.0]], index=index, columns=columns)

    result = download_in_bounded_batches(
        fake_download,
        tickers=["AAPL", "7203.T"],
        batch_size=1,
        group_by="ticker",
    )

    assert isinstance(result.index, pd.DatetimeIndex)
    assert str(result.index.tz) == "UTC"
    assert str(result["AAPL"].index.tz) == "UTC"
    assert str(result["7203.T"].index.tz) == "UTC"


def test_download_batches_keep_partial_results_and_stably_deduplicate_tickers():
    calls: list[list[str]] = []

    def fake_download(*, tickers: str, **_kwargs: Any) -> pd.DataFrame:
        symbols = tickers.split()
        calls.append(symbols)
        if symbols[0] == "T08":
            return pd.DataFrame()
        columns = pd.MultiIndex.from_product([symbols, ["Close"]])
        return pd.DataFrame([[1.0] * len(symbols)], columns=columns)

    symbols = [f"T{number:02d}" for number in range(17)] + ["T00", "T16"]
    result = download_in_bounded_batches(
        fake_download,
        tickers=symbols,
        group_by="ticker",
    )

    assert [len(batch) for batch in calls] == [8, 8, 1]
    assert list(result.columns.get_level_values(0)) == [
        *symbols[:8],
        "T16",
    ]


def test_download_gate_fails_fast_and_bounds_overlapping_batches():
    state_lock = threading.Lock()
    all_active = threading.Event()
    release = threading.Event()
    active = 0
    peak = 0
    failures: list[BaseException] = []

    def fake_download(*, tickers: str, **_kwargs: Any) -> pd.DataFrame:
        nonlocal active, peak
        symbols = tickers.split()

        def worker() -> None:
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
                if active == 32:
                    all_active.set()
            release.wait(timeout=5)
            with state_lock:
                active -= 1

        workers = [threading.Thread(target=worker) for _symbol in symbols]
        for item in workers:
            item.start()
        for item in workers:
            item.join(timeout=5)
        columns = pd.MultiIndex.from_product([symbols, ["Close"]])
        return pd.DataFrame([[1.0] * len(symbols)], columns=columns)

    def run_download(prefix: str) -> None:
        try:
            download_in_bounded_batches(
                fake_download,
                tickers=[f"{prefix}{number}" for number in range(8)],
                group_by="ticker",
            )
        except BaseException as exc:  # pragma: no cover - assertion reports it
            failures.append(exc)

    callers = [threading.Thread(target=run_download, args=(f"B{index}-",)) for index in range(4)]
    for caller in callers:
        caller.start()
    assert all_active.wait(timeout=5)

    with pytest.raises(YFinanceBatchBusy, match="capacity is busy"):
        download_in_bounded_batches(
            fake_download,
            tickers=["EXTRA"],
            group_by="ticker",
        )

    release.set()
    for caller in callers:
        caller.join(timeout=5)
    assert not failures
    assert all(not caller.is_alive() for caller in callers)
    assert peak == 32


@pytest.mark.anyio
async def test_cancelled_outer_download_does_not_queue_more_background_work():
    state_lock = threading.Lock()
    all_active = threading.Event()
    all_finished = threading.Event()
    release = threading.Event()
    active = 0

    def blocking_download(*, tickers: str, **_kwargs: Any) -> pd.DataFrame:
        nonlocal active
        symbols = tickers.split()

        def worker() -> None:
            nonlocal active
            with state_lock:
                active += 1
                if active == 32:
                    all_active.set()
            release.wait(timeout=5)
            with state_lock:
                active -= 1
                if active == 0:
                    all_finished.set()

        workers = [threading.Thread(target=worker) for _symbol in symbols]
        for item in workers:
            item.start()
        for item in workers:
            item.join(timeout=5)
        columns = pd.MultiIndex.from_product([symbols, ["Close"]])
        return pd.DataFrame([[1.0] * len(symbols)], columns=columns)

    async def run(prefix: str) -> pd.DataFrame:
        return await asyncio.to_thread(
            download_in_bounded_batches,
            blocking_download,
            tickers=[f"{prefix}{number}" for number in range(8)],
            group_by="ticker",
        )

    tasks = [asyncio.create_task(run(f"C{index}-")) for index in range(4)]
    assert await asyncio.to_thread(all_active.wait, 5)
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)

    with pytest.raises(YFinanceBatchBusy, match="capacity is busy"):
        await asyncio.to_thread(
            download_in_bounded_batches,
            blocking_download,
            tickers=["EXTRA"],
            group_by="ticker",
        )

    release.set()
    assert await asyncio.to_thread(all_finished.wait, 5)

    def quick_download(**_kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame({"Close": [1.0]})

    for _attempt in range(100):
        try:
            recovered = await asyncio.to_thread(
                download_in_bounded_batches,
                quick_download,
                tickers=["RECOVERED"],
                group_by="ticker",
            )
        except YFinanceBatchBusy:
            await asyncio.sleep(0.01)
            continue
        assert list(recovered.columns) == [("RECOVERED", "Close")]
        break
    else:  # pragma: no cover - deterministic failure message
        pytest.fail("yfinance download gate did not recover")
