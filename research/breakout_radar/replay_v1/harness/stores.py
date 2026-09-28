"""Frozen-data stores: 5-minute bars, split-adjusted daily bars, FRED, shares, metadata.

Loading and validation call production's own helpers where they exist
(``market_data._valid_ohlc``, ``market_data._series_from_rows``); the rest is file I/O.
"""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
from bisect import bisect_right
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.eod_limited import market_data as md
from app.services.eod_limited.universe import UniverseMember

NY = ZoneInfo("America/New_York")
BAR_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
_SLOT_MINUTES = 5
_FIRST_SLOT_MINUTE = 4 * 60  # 04:00 ET
SLOTS_PER_DAY = (20 * 60 - _FIRST_SLOT_MINUTE) // _SLOT_MINUTES  # 192


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------- minute bars


def parse_aggregate_page(payload: Mapping[str, Any]) -> list[tuple[int, float, float, float, float, float]]:
    """Rows (t, o, h, l, c, v) with the same acceptance rule as ``massive.ticker_range``.

    A bar without a finite close or timestamp is skipped; later pages overwrite
    earlier bars with the same timestamp (``ticker_range`` keeps a dict by ``t``).
    """

    rows = payload.get("results") or []
    out: list[tuple[int, float, float, float, float, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        close = _finite(row.get("c"))
        stamp = _finite(row.get("t"))
        if close is None or stamp is None or not stamp.is_integer():
            continue
        volume = _finite(row.get("v"))
        out.append(
            (
                int(stamp),
                _finite(row.get("o")) if _finite(row.get("o")) is not None else float("nan"),
                _finite(row.get("h")) if _finite(row.get("h")) is not None else float("nan"),
                _finite(row.get("l")) if _finite(row.get("l")) is not None else float("nan"),
                close,
                volume if volume is not None else float("nan"),
            )
        )
    return out


def _slot_index(stamps_ms: np.ndarray) -> np.ndarray:
    """Five-minute slot from 04:00 ET for each bar start, -1 for bars outside or misaligned."""

    local = pd.to_datetime(stamps_ms, unit="ms", utc=True).tz_convert(NY)
    minutes = np.asarray(local.hour * 60 + local.minute)
    slots = (minutes - _FIRST_SLOT_MINUTE) // _SLOT_MINUTES
    valid = (slots >= 0) & (slots < SLOTS_PER_DAY) & ((minutes - _FIRST_SLOT_MINUTE) % _SLOT_MINUTES == 0)
    return np.where(valid, slots, -1)


def build_day_files(out_dir: Path, tickers: Sequence[str], days: Sequence[str], *, chunk_months: int = 3) -> int:
    """Second pass: one parquet per ET day (ticker, slot, close, volume) for the discovery proxy.

    ``build_day_context`` needs every ticker's slots for one day; reading 12,500 per-ticker
    files per day would thrash any cache, so the store also holds the same bars grouped by
    day. Ticker files are read once per ``chunk_months`` so memory stays bounded.
    """

    day_dir = out_dir / "days"
    day_dir.mkdir(parents=True, exist_ok=True)
    months = sorted({day[:7] for day in days})
    written = 0
    for start in range(0, len(months), chunk_months):
        chunk = months[start:start + chunk_months]
        first = date.fromisoformat(chunk[0] + "-01")
        last_month = date.fromisoformat(chunk[-1] + "-01")
        after = (last_month.replace(day=28) + timedelta(days=4)).replace(day=1)  # first day of the next month
        # Bars are keyed by ET day; widen the UTC range by a day on each side and filter exactly below.
        low_ms = int(datetime(first.year, first.month, first.day, tzinfo=timezone.utc).timestamp() * 1000) - 86_400_000
        high_ms = int(datetime(after.year, after.month, after.day, tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000
        rows: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]] = defaultdict(list)
        for ticker in tickers:
            path = out_dir / f"{ticker}.parquet"
            if not path.exists():
                continue
            raw = pd.read_parquet(path, columns=["t", "close", "volume"], filters=[("t", ">=", low_ms), ("t", "<", high_ms)])
            if raw.empty:
                continue
            stamps = raw["t"].to_numpy(dtype=np.int64)
            slots = _slot_index(stamps)
            local_days = pd.to_datetime(stamps, unit="ms", utc=True).tz_convert(NY).strftime("%Y-%m-%d").to_numpy()
            keep = slots >= 0
            for day in np.unique(local_days[keep]):
                if day[:7] not in chunk:
                    continue
                mask = keep & (local_days == day)
                rows[day].append((
                    np.full(int(mask.sum()), ticker, dtype=object), slots[mask].astype(np.int16),
                    raw["close"].to_numpy(dtype=np.float64)[mask], raw["volume"].to_numpy(dtype=np.float64)[mask],
                ))
        for day, parts in rows.items():
            frame = pd.DataFrame(
                {
                    "ticker": np.concatenate([p[0] for p in parts]),
                    "slot": np.concatenate([p[1] for p in parts]),
                    "close": np.concatenate([p[2] for p in parts]),
                    "volume": np.concatenate([p[3] for p in parts]),
                }
            )
            frame.sort_values(["ticker", "slot"], kind="stable").to_parquet(day_dir / f"{day}.parquet", index=False)
            written += 1
    return written


def build_minute_store(
    manifest_path: Path, raw_root: Path, out_dir: Path, *, day_files: bool = True, chunk_months: int = 3
) -> dict[str, Any]:
    """Turn frozen Massive pages into one parquet per ticker plus a day-coverage table.

    With ``day_files`` the same bars are also written per ET day (``days/<day>.parquet``),
    which is what the discovery proxy reads; the per-ticker files serve the intraday stage.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with open(manifest_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if not entry.get("complete", True) or int(entry.get("http") or 200) != 200:
                continue
            by_ticker[str(entry["ticker"]).upper()].append(entry)
    coverage_rows: list[tuple[str, str]] = []
    summary = {"tickers": 0, "bars": 0, "pages": 0, "skipped_tickers": []}
    for ticker in sorted(by_ticker):
        bars: dict[int, tuple[float, float, float, float, float]] = {}
        for entry in sorted(by_ticker[ticker], key=lambda item: (str(item.get("from")), int(item.get("part") or 0))):
            path = raw_root / str(entry["file"])
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            for t, o, h, l, c, v in parse_aggregate_page(payload):
                bars[t] = (o, h, l, c, v)
            summary["pages"] += 1
        if not bars:
            summary["skipped_tickers"].append(ticker)
            continue
        stamps = np.array(sorted(bars), dtype=np.int64)
        values = np.array([bars[t] for t in stamps], dtype=np.float64)
        frame = pd.DataFrame(
            {
                "t": stamps,
                "open": values[:, 0],
                "high": values[:, 1],
                "low": values[:, 2],
                "close": values[:, 3],
                "volume": values[:, 4],
            }
        )
        frame.to_parquet(out_dir / f"{ticker}.parquet", index=False)
        days = pd.to_datetime(stamps, unit="ms", utc=True).tz_convert(NY).date
        for day in sorted(set(days)):
            coverage_rows.append((ticker, day.isoformat()))
        summary["tickers"] += 1
        summary["bars"] += int(len(stamps))
    pd.DataFrame(coverage_rows, columns=["ticker", "day"]).to_parquet(
        out_dir / "coverage.parquet", index=False
    )
    if day_files:
        summary["day_files"] = build_day_files(
            out_dir, sorted({ticker for ticker, _day in coverage_rows}), sorted({day for _ticker, day in coverage_rows}),
            chunk_months=chunk_months,
        )
    (out_dir / "store_manifest.json").write_text(json.dumps(summary, indent=1))
    return summary


class MinuteStore:
    """Read-side of the derived store; frames look like ``scanner._slice_ticker`` output.

    ``window`` (first day, last day) bounds what a per-ticker load keeps in memory: a
    segment only ever asks for bars from 30 calendar days before its warm-up day to its
    last day, so a 5-year file shrinks to that slice. Day slots come from ``days/`` when
    the store has them (one file per day, every ticker), otherwise from the ticker files.
    """

    def __init__(
        self,
        root: Path | str,
        cache_tickers: int = 800,
        window: tuple[date, date] | None = None,
    ) -> None:
        self.root = Path(root)
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._cache_size = cache_tickers
        self.window = window
        self._day_dir = self.root / "days"
        self.has_day_files = self._day_dir.is_dir()
        self._day_cache: OrderedDict[date, tuple[dict[str, int], np.ndarray, np.ndarray]] = OrderedDict()
        coverage = pd.read_parquet(self.root / "coverage.parquet")
        self._days_by_ticker: dict[str, list[str]] = {
            ticker: sorted(group["day"].tolist())
            for ticker, group in coverage.groupby("ticker")
        }
        self._tickers_by_day: dict[str, set[str]] = defaultdict(set)
        for ticker, days in self._days_by_ticker.items():
            for day in days:
                self._tickers_by_day[day].add(ticker)

    @property
    def tickers(self) -> list[str]:
        return sorted(self._days_by_ticker)

    def tickers_with_bars_on(self, day: date) -> set[str]:
        return set(self._tickers_by_day.get(day.isoformat(), ()))

    def _load(self, ticker: str) -> pd.DataFrame:
        cached = self._cache.get(ticker)
        if cached is not None:
            self._cache.move_to_end(ticker)
            return cached
        path = self.root / f"{ticker}.parquet"
        if not path.exists():
            frame = pd.DataFrame(columns=list(BAR_COLUMNS))
            frame.index = pd.DatetimeIndex([], tz="UTC")
        else:
            if self.window is not None:
                first, last = self.window
                low_ms = int(datetime(first.year, first.month, first.day, tzinfo=timezone.utc).timestamp() * 1000) - 86_400_000
                high_ms = int(datetime(last.year, last.month, last.day, tzinfo=timezone.utc).timestamp() * 1000) + 2 * 86_400_000
                raw = pd.read_parquet(path, filters=[("t", ">=", low_ms), ("t", "<", high_ms)])
            else:
                raw = pd.read_parquet(path)
            index = pd.DatetimeIndex(pd.to_datetime(raw["t"].to_numpy(), unit="ms", utc=True))
            frame = pd.DataFrame(
                {
                    "Open": raw["open"].to_numpy(),
                    "High": raw["high"].to_numpy(),
                    "Low": raw["low"].to_numpy(),
                    "Close": raw["close"].to_numpy(),
                    "Volume": raw["volume"].to_numpy(),
                },
                index=index,
            )
            frame["_day"] = index.tz_convert(NY).date
        self._cache[ticker] = frame
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return frame

    def bars(self, ticker: str, start_day: date, end_day: date) -> pd.DataFrame:
        """UTC-indexed OHLCV for ET calendar days ``start_day`` through ``end_day``."""

        frame = self._load(ticker)
        if frame.empty:
            result = frame.drop(columns=["_day"], errors="ignore")
        else:
            mask = (frame["_day"] >= start_day) & (frame["_day"] <= end_day)
            result = frame.loc[mask, list(BAR_COLUMNS)]
        if result.empty and self.has_bars_between(ticker, start_day, end_day):
            # Coverage says bars exist: an empty read is a store or I/O fault, and a
            # silent miss would turn into a wrong "no intraday data" verdict downstream.
            raise RuntimeError(f"minute store returned no bars for {ticker} in {start_day}..{end_day} despite coverage")
        return result

    def has_bars_between(self, ticker: str, start_day: date, end_day: date) -> bool:
        days = self._days_by_ticker.get(ticker)
        if not days:
            return False
        low, high = start_day.isoformat(), end_day.isoformat()
        position = bisect_right(days, low) - 1
        if position >= 0 and days[position] == low:
            return True
        return position + 1 < len(days) and days[position + 1] <= high

    def day_table(self, day: date) -> tuple[dict[str, int], np.ndarray, np.ndarray]:
        """Every ticker's (close, volume) slot rows for one day from ``days/<day>.parquet``."""

        cached = self._day_cache.get(day)
        if cached is not None:
            self._day_cache.move_to_end(day)
            return cached
        path = self._day_dir / f"{day.isoformat()}.parquet"
        if not path.exists():
            table: tuple[dict[str, int], np.ndarray, np.ndarray] = ({}, np.zeros((0, SLOTS_PER_DAY)), np.zeros((0, SLOTS_PER_DAY)))
        else:
            raw = pd.read_parquet(path)
            tickers = sorted(set(raw["ticker"].astype(str)))
            index = {ticker: position for position, ticker in enumerate(tickers)}
            row = raw["ticker"].astype(str).map(index).to_numpy(dtype=np.int64)
            slot = raw["slot"].to_numpy(dtype=np.int64)
            close = np.full((len(tickers), SLOTS_PER_DAY), np.nan)
            volume = np.full((len(tickers), SLOTS_PER_DAY), np.nan)
            close[row, slot] = raw["close"].to_numpy(dtype=np.float64)
            volume[row, slot] = raw["volume"].to_numpy(dtype=np.float64)
            table = (index, close, volume)
        self._day_cache[day] = table
        while len(self._day_cache) > 4:
            self._day_cache.popitem(last=False)
        return table

    def day_slots_from_ticker_file(self, ticker: str, day: date) -> tuple[np.ndarray, np.ndarray] | None:
        """(close, volume) slot arrays computed from the ticker's own file (the reference path)."""

        frame = self._load(ticker)
        if frame.empty:
            return None
        rows = frame.loc[frame["_day"] == day]
        if rows.empty:
            return None
        local = rows.index.tz_convert(NY)
        minutes = local.hour * 60 + local.minute
        slots = (minutes - _FIRST_SLOT_MINUTE) // _SLOT_MINUTES
        valid = (slots >= 0) & (slots < SLOTS_PER_DAY) & ((minutes - _FIRST_SLOT_MINUTE) % _SLOT_MINUTES == 0)
        close = np.full(SLOTS_PER_DAY, np.nan)
        volume = np.full(SLOTS_PER_DAY, np.nan)
        close[slots[valid]] = rows["Close"].to_numpy()[valid]
        volume[slots[valid]] = rows["Volume"].to_numpy()[valid]
        return close, volume

    def day_slots(self, ticker: str, day: date) -> tuple[np.ndarray, np.ndarray] | None:
        """(close, volume) arrays over the 192 five-minute slots from 04:00 ET, NaN when no bar."""

        covered = day.isoformat() in self._tickers_by_day and ticker in self._tickers_by_day[day.isoformat()]
        if not self.has_day_files:
            slots = self.day_slots_from_ticker_file(ticker, day)
        else:
            index, close, volume = self.day_table(day)
            position = index.get(ticker)
            slots = None if position is None else (close[position].copy(), volume[position].copy())
        if slots is None and covered:
            raise RuntimeError(f"minute store has no slots for {ticker} on {day} despite coverage")
        return slots


# --------------------------------------------------------------------------- daily bars


def _member(ticker: str) -> UniverseMember:
    return UniverseMember(
        ticker=ticker, name=ticker, provider_type="", primary_exchange="",
        asset_track="stock", theme_ids=(), venue_metadata={},
    )


class DailyStore:
    """Split-adjusted daily frames as production's Massive path would have seen them on a day.

    ``frame(ticker, through, as_of_day)`` returns bars with session <= ``through``,
    adjusted for the splits executed on or before ``as_of_day`` (what ``adjusted=true``
    bars fetched on that day contain), as a tz-naive normalized DatetimeIndex with
    Open/High/Low/Close/Volume, the shape ``scanner._slice_ticker`` yields.
    """

    def __init__(self, db_path: Path | str, cache_tickers: int = 2000) -> None:
        """``cache_tickers`` bounds the per-ticker row cache (each ticker's rows are about
        0.25 MB for five years); the day-context build touches every ticker once a day and
        re-reads evicted ones from SQLite, which costs seconds, not memory."""

        self.db_path = Path(db_path)
        self._connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row
        self._rows: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._rows_limit = max(1, int(cache_tickers))
        self._splits: dict[str, list[tuple[date, float, float]]] = defaultdict(list)
        for row in self._connection.execute(
            "SELECT ticker, execution_date, split_from, split_to FROM splits ORDER BY execution_date"
        ):
            self._splits[str(row["ticker"])].append(
                (date.fromisoformat(str(row["execution_date"])), float(row["split_from"]), float(row["split_to"]))
            )
        self._frame_cache: dict[tuple[str, date, date], pd.DataFrame] = {}
        self.sessions: list[date] = [
            date.fromisoformat(str(row[0]))
            for row in self._connection.execute("SELECT session_date FROM market_sessions ORDER BY session_date")
        ]

    def _ticker_rows(self, ticker: str) -> list[dict[str, Any]]:
        rows = self._rows.get(ticker)
        if rows is not None:
            self._rows.move_to_end(ticker)
        if rows is None:
            rows = [
                dict(row)
                for row in self._connection.execute(
                    "SELECT session_date, open, high, low, close, volume FROM raw_daily_bars "
                    "WHERE ticker = ? ORDER BY session_date",
                    (ticker,),
                )
            ]
            # Production's Massive validator drops crossed or non-positive bars and
            # negative volume (scanner._validated_massive_history); mirror that.
            rows = [
                row for row in rows
                if md._valid_ohlc(row) and (row["volume"] is None or float(row["volume"]) >= 0)
            ]
            self._rows[ticker] = rows
            while len(self._rows) > self._rows_limit:
                self._rows.popitem(last=False)
        return rows

    def splits_between(self, ticker: str, after: date, through: date) -> list[tuple[date, float, float]]:
        return [item for item in self._splits.get(ticker, ()) if after < item[0] <= through]

    def clear_cache(self) -> None:
        self._frame_cache.clear()

    def frame(self, ticker: str, *, through: date, as_of_day: date, cache: bool = True) -> pd.DataFrame:
        """The adjusted frame; ``cache=False`` for one-off reads (the day-context build
        asks once per ticker per day, and caching those frames costs ~40 MB a day per
        thousand tickers until the runner clears the cache at the end of the day)."""

        key = (ticker, through, as_of_day)
        cached = self._frame_cache.get(key)
        if cached is not None:
            return cached
        rows = [row for row in self._ticker_rows(ticker) if str(row["session_date"]) <= through.isoformat()]
        if not rows:
            frame = pd.DataFrame(columns=list(BAR_COLUMNS), index=pd.DatetimeIndex([]))
        else:
            splits = [item for item in self._splits.get(ticker, ()) if item[0] <= as_of_day]
            dates = [date.fromisoformat(str(row["session_date"])) for row in rows]
            series = md._series_from_rows(_member(ticker), rows, splits=splits, dates=dates)
            frame = pd.DataFrame(
                {
                    "Open": series.open,
                    "High": series.high,
                    "Low": series.low,
                    "Close": series.close,
                    "Volume": series.volume,
                },
                index=pd.DatetimeIndex(pd.to_datetime(dates)),
            )
        if cache:
            self._frame_cache[key] = frame
        return frame

    def previous_close(self, ticker: str, day: date) -> float | None:
        frame = self.frame(ticker, through=day - timedelta(days=1), as_of_day=day)
        if frame.empty:
            return None
        return _finite(frame["Close"].iloc[-1])

    def mean_volume(self, ticker: str, day: date, sessions: int = 10, *, min_sessions: int = 1) -> float | None:
        """Average volume of the last ``sessions`` sessions before ``day`` (fewer when young)."""

        frame = self.frame(ticker, through=day - timedelta(days=1), as_of_day=day)
        values = pd.to_numeric(frame["Volume"], errors="coerce").dropna().tail(sessions)
        if len(values) < min_sessions:
            return None
        mean = _finite(values.mean())
        return mean if mean is not None and mean > 0 else None

    def prior_session_stats(self, ticker: str, day: date, sessions: int = 10) -> "PriorSessionStats | None":
        """Everything the discovery proxy needs from the daily bars before ``day``, one frame read.

        TradingView's fields show the previous session's values until they roll
        (DATA_SPEC 20.3): yesterday's close and change, yesterday's volume and its
        relative volume against the 10 sessions before it. After the roll the day's
        cumulative volume is compared with the 10 sessions before today.
        """

        frame = self.frame(ticker, through=day - timedelta(days=1), as_of_day=day, cache=False)
        if frame.empty:
            return None
        closes = pd.to_numeric(frame["Close"], errors="coerce")
        volumes = pd.to_numeric(frame["Volume"], errors="coerce")
        prev_close = _finite(closes.iloc[-1])
        prev_prev_close = _finite(closes.iloc[-2]) if len(closes) >= 2 else None
        prev_volume = _finite(volumes.iloc[-1])
        history = volumes.dropna()
        mean = _finite(history.tail(sessions).mean()) if len(history) else None
        before_previous = history.iloc[:-1].tail(sessions)
        mean_before = _finite(before_previous.mean()) if len(before_previous) else None
        prev_relvol = None
        if prev_volume is not None and mean_before is not None and mean_before > 0:
            prev_relvol = prev_volume / mean_before
        return PriorSessionStats(
            prev_close=prev_close,
            prev_prev_close=prev_prev_close,
            prev_volume=prev_volume,
            mean_volume=mean if mean is not None and mean > 0 else None,
            prev_relvol=prev_relvol,
        )

    def previous_session_relvol(self, ticker: str, day: date, sessions: int = 10) -> float | None:
        """The prior session's full-day volume over the 10-session average before it.

        TradingView's ``relative_volume_10d_calc`` still shows this value in the first
        minutes of the regular session and throughout pre-market (smoke day 1: the ratio
        to it was exactly 1.00 at 09:37 and 09:43 ET).
        """

        frame = self.frame(ticker, through=day - timedelta(days=1), as_of_day=day)
        values = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
        if len(values) < 2:
            return None
        history = values.iloc[:-1].tail(sessions)
        mean = _finite(history.mean())
        last = _finite(values.iloc[-1])
        if mean is None or mean <= 0 or last is None:
            return None
        return last / mean


# --------------------------------------------------------------------------- FRED


class FredStore:
    """VIXCLS as ^VIX and DGS10 x 10 as ^TNX, Close-only frames (DATA_SPEC 11.4)."""

    def __init__(self, folder: Path | str) -> None:
        folder = Path(folder)
        self.frames: dict[str, pd.DataFrame] = {
            "^VIX": self._load(folder / "VIXCLS.csv", 1.0),
            "^TNX": self._load(folder / "DGS10.csv", 10.0),
        }

    @staticmethod
    def _load(path: Path, scale: float) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=["Close"])
        raw = pd.read_csv(path)
        date_column = raw.columns[0]
        value_column = raw.columns[1]
        values = pd.to_numeric(raw[value_column], errors="coerce") * scale
        frame = pd.DataFrame({"Close": values.to_numpy()}, index=pd.DatetimeIndex(pd.to_datetime(raw[date_column])))
        return frame.dropna()

    def frame(self, symbol: str, *, through: date) -> pd.DataFrame:
        frame = self.frames.get(symbol)
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["Close"])
        return frame.loc[frame.index.date <= through] if len(frame) else frame


# --------------------------------------------------------------------------- shares


class SharesStore:
    """Point-in-time shares from ``/v3/reference/tickers/{T}?date=`` samples (JSONL)."""

    def __init__(self, path: Path | str | None, daily: DailyStore | None = None) -> None:
        self._samples: dict[str, list[tuple[date, float | None, float | None]]] = defaultdict(list)
        self._daily = daily
        if path is None:
            return
        with open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                results = row.get("results") if isinstance(row.get("results"), Mapping) else row
                ticker = str(row.get("ticker") or results.get("ticker") or "").upper()
                sample_date = row.get("date") or row.get("as_of")
                if not ticker or not sample_date:
                    continue
                shares = _finite(results.get("weighted_shares_outstanding"))
                cap = _finite(results.get("market_cap"))
                self._samples[ticker].append((date.fromisoformat(str(sample_date)[:10]), shares, cap))
        for ticker in self._samples:
            self._samples[ticker].sort()

    def shares(self, ticker: str, day: date) -> float | None:
        samples = self._samples.get(ticker)
        if not samples:
            return None
        dates = [item[0] for item in samples]
        position = bisect_right(dates, day) - 1
        if position < 0:
            return None
        sample_date, shares, _cap = samples[position]
        if shares is None:
            return None
        if self._daily is not None:
            for _execution, split_from, split_to in self._daily.splits_between(ticker, sample_date, day):
                shares *= split_to / split_from
        return shares


# --------------------------------------------------------------------------- metadata


@dataclass(frozen=True)
class TickerMeta:
    ticker: str
    name: str
    exchange: str
    tv_type: str
    typespecs: tuple[str, ...]
    sector: str | None
    is_fund: bool


# Massive reference ``type`` -> TradingView ``type``/``typespecs`` (DATA_SPEC appendix B).
_TYPE_TO_TV: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "CS": ("stock", ("common",), False),
    "OS": ("stock", ("common",), False),
    "ADRC": ("dr", ("adr",), False),
    "ADRP": ("dr", ("adr",), False),
    "GDR": ("dr", ("gdr",), False),
    "ETF": ("fund", ("etf",), True),
    "ETV": ("fund", ("etf",), True),
    "ETS": ("fund", ("etf",), True),
    "ETN": ("structured", ("etn",), True),
    "PFD": ("stock", ("preferred",), False),
    "WARRANT": ("warrant", (), False),
    "RIGHT": ("right", (), False),
    "UNIT": ("stock", ("unit",), False),
    "FUND": ("fund", ("closed-end",), True),
}
_EXCHANGE_TO_TV = {"XNAS": "NASDAQ", "XNYS": "NYSE", "XASE": "AMEX", "ARCX": "AMEX", "BATS": "CBOE"}

# SIC major groups -> the TradingView sector strings production's candidates carry.
_SIC_SECTORS: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Process Industries"),
    (1000, 1099, "Non-Energy Minerals"),
    (1300, 1399, "Energy Minerals"),
    (1400, 1499, "Non-Energy Minerals"),
    (1500, 1799, "Industrial Services"),
    (2000, 2199, "Consumer Non-Durables"),
    (2200, 2399, "Consumer Non-Durables"),
    (2400, 2599, "Consumer Durables"),
    (2600, 2699, "Process Industries"),
    (2700, 2799, "Consumer Services"),
    (2800, 2829, "Process Industries"),
    (2830, 2836, "Health Technology"),
    (2840, 2844, "Consumer Non-Durables"),
    (2850, 2899, "Process Industries"),
    (2900, 2999, "Energy Minerals"),
    (3000, 3099, "Process Industries"),
    (3100, 3199, "Consumer Non-Durables"),
    (3200, 3299, "Non-Energy Minerals"),
    (3300, 3399, "Non-Energy Minerals"),
    (3400, 3499, "Producer Manufacturing"),
    (3500, 3569, "Producer Manufacturing"),
    (3570, 3579, "Electronic Technology"),
    (3580, 3599, "Producer Manufacturing"),
    (3600, 3659, "Producer Manufacturing"),
    (3660, 3699, "Electronic Technology"),
    (3700, 3719, "Consumer Durables"),
    (3720, 3799, "Electronic Technology"),
    (3800, 3839, "Electronic Technology"),
    (3840, 3851, "Health Technology"),
    (3860, 3899, "Electronic Technology"),
    (3900, 3999, "Consumer Durables"),
    (4000, 4799, "Transportation"),
    (4800, 4899, "Communications"),
    (4900, 4999, "Utilities"),
    (5000, 5199, "Distribution Services"),
    (5200, 5999, "Retail Trade"),
    (6000, 6499, "Finance"),
    (6500, 6599, "Finance"),
    (6600, 6799, "Finance"),
    (6800, 6999, "Finance"),
    (7000, 7299, "Consumer Services"),
    (7300, 7369, "Commercial Services"),
    (7370, 7379, "Technology Services"),
    (7380, 7399, "Commercial Services"),
    (7500, 7999, "Consumer Services"),
    (8000, 8099, "Health Services"),
    (8100, 8999, "Commercial Services"),
)


def sic_to_sector(sic: Any) -> str | None:
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    for low, high, sector in _SIC_SECTORS:
        if low <= code <= high:
            return sector
    return None


class DirectoryMetadata:
    """Weekly point-in-time directory snapshots plus the frozen SIC table."""

    def __init__(self, folder: Path | str, sic_path: Path | str | None = None) -> None:
        self.folder = Path(folder)
        self.labels = sorted(path.name[:-8] for path in self.folder.glob("20*.json.gz"))
        if not self.labels:
            raise ValueError(f"no directory snapshots under {self.folder}")
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._sic: dict[tuple[str, str], str] = {}
        if sic_path is not None and Path(sic_path).exists():
            with gzip.open(sic_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            items = payload if isinstance(payload, list) else payload.get("results") or payload.get("rows") or []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                ticker = str(item.get("ticker") or "").upper()
                cik = str(item.get("cik") or "")
                sic = item.get("sic_code", item.get("sic"))
                if ticker and sic not in (None, ""):
                    self._sic[(ticker, cik)] = str(sic)
                    self._sic.setdefault((ticker, ""), str(sic))

    def label_for(self, day: date) -> str | None:
        eligible = [label for label in self.labels if label <= day.isoformat()]
        return eligible[-1] if eligible else None

    def rows(self, label: str) -> dict[str, dict[str, Any]]:
        if label not in self._rows:
            with gzip.open(self.folder / f"{label}.json.gz", "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            self._rows[label] = {str(row.get("ticker") or "").upper(): row for row in payload["results"]}
        return self._rows[label]

    def meta(self, ticker: str, day: date) -> TickerMeta | None:
        label = self.label_for(day)
        if label is None:
            return None
        row = self.rows(label).get(ticker)
        if row is None:
            return None
        provider_type = str(row.get("type") or "").upper()
        tv_type, specs, is_fund = _TYPE_TO_TV.get(provider_type, ("structured", (), False))
        sic = self._sic.get((ticker, str(row.get("cik") or ""))) or self._sic.get((ticker, ""))
        sector = "Miscellaneous" if is_fund else sic_to_sector(sic)
        return TickerMeta(
            ticker=ticker,
            name=str(row.get("name") or ticker),
            exchange=_EXCHANGE_TO_TV.get(str(row.get("primary_exchange") or ""), str(row.get("primary_exchange") or "")),
            tv_type=tv_type,
            typespecs=specs,
            sector=sector,
            is_fund=is_fund,
        )


class ProductionCandidateMetadata:
    """Smoke-test metadata taken from production's own candidate rows (TradingView values).

    Isolates the price/volume proxy from the classification proxy: type, exchange,
    sector, name and market cap are TradingView's, per scan where available.
    """

    _ASSET_TO_TV = {
        "common_stock": ("stock", ("common",), False),
        "adr": ("dr", ("adr",), False),
        "etf": ("fund", ("etf",), True),
    }

    def __init__(self, export_path: Path | str) -> None:
        self._latest: dict[str, dict[str, Any]] = {}
        self._cap_by_scan: dict[str, dict[str, float | None]] = defaultdict(dict)
        self._otc_by_scan: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._scan_as_of: dict[str, datetime] = {}
        columns: dict[str, list[str]] = {}
        current = None
        with gzip.open(export_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                obj = json.loads(line)
                if isinstance(obj, dict) and "table" in obj:
                    current = obj["table"]
                    columns[current] = obj["columns"]
                    continue
                if isinstance(obj, dict) and obj.get("end"):
                    break
                if current == "breakout_scan_runs":
                    row = dict(zip(columns[current], obj))
                    if row.get("status") == "completed":
                        self._scan_as_of[row["scan_run_id"]] = datetime.fromisoformat(
                            str(row["scheduled_at"]).replace("Z", "+00:00")
                        )
                elif current == "breakout_candidates":
                    row = dict(zip(columns[current], obj))
                    scan_id = row["scan_run_id"]
                    body = json.loads(row["candidate_json"])
                    ticker = str(row["ticker"]).upper()
                    self._cap_by_scan[scan_id][ticker] = _finite(body.get("provider_market_cap"))
                    if str(body.get("exchange") or "").upper() == "OTC":
                        self._otc_by_scan[scan_id].append(body)
                        continue
                    stamp = str(body.get("provider_timestamp") or "")
                    previous = self._latest.get(ticker)
                    if previous is None or stamp >= str(previous.get("provider_timestamp") or ""):
                        self._latest[ticker] = body
        self._scan_by_as_of = {value.astimezone(timezone.utc): key for key, value in self._scan_as_of.items()}

    def scan_id_for(self, as_of: datetime) -> str | None:
        return self._scan_by_as_of.get(as_of.astimezone(timezone.utc))

    def meta(self, ticker: str, day: date) -> TickerMeta | None:
        body = self._latest.get(ticker)
        if body is None:
            return None
        tv_type, specs, is_fund = self._ASSET_TO_TV.get(str(body.get("asset_type")), ("structured", (), False))
        sector = body.get("sector")
        return TickerMeta(
            ticker=ticker,
            name=str(body.get("name") or ticker),
            exchange=str(body.get("exchange") or ""),
            tv_type=tv_type,
            typespecs=specs,
            sector=str(sector) if sector else None,
            is_fund=is_fund,
        )

    def market_cap(self, ticker: str, as_of: datetime) -> float | None:
        scan_id = self.scan_id_for(as_of)
        if scan_id is not None and ticker in self._cap_by_scan.get(scan_id, {}):
            return self._cap_by_scan[scan_id][ticker]
        body = self._latest.get(ticker)
        return _finite(body.get("provider_market_cap")) if body else None

    def otc_rows(self, as_of: datetime) -> list[dict[str, Any]]:
        scan_id = self.scan_id_for(as_of)
        return list(self._otc_by_scan.get(scan_id, ())) if scan_id else []


@dataclass(frozen=True)
class PriorSessionStats:
    prev_close: float | None
    prev_prev_close: float | None
    prev_volume: float | None
    mean_volume: float | None  # over the 10 sessions before the day (fewer when young)
    prev_relvol: float | None  # yesterday's volume over the 10 sessions before it


def previous_trading_day(day: date) -> date | None:
    """The trading day before ``day`` by the production market calendar."""

    from app.services.market_calendar import is_trading_day

    for back in range(1, 8):
        candidate = day - timedelta(days=back)
        if is_trading_day(candidate):
            return candidate
    return None


def iter_trading_days(start: date, end: date, is_trading_day) -> Iterable[date]:
    day = start
    while day <= end:
        if is_trading_day(day):
            yield day
        day += timedelta(days=1)
