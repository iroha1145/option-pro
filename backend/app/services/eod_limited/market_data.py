"""Persistent, raw Massive daily bars for current all-market EOD inference."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import time
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from app.data_paths import get_data_paths
from app.services import massive
from app.services.market_calendar import is_trading_day, prior_trading_sessions
from app.services.research_eod_v1.series import SecuritySeries

from . import VOLUME_SCOPE
from .universe import UniverseMember, select_all_market_universe


HISTORY_SESSIONS = 370
SHORT_HISTORY_SESSIONS = 252
RESIDUAL_HISTORY_SESSIONS = 330
# Leave one of the client's process-wide request slots to the other tasks;
# taking every slot turns their requests and our retries into provider_busy.
MAX_PROVIDER_CONCURRENCY = max(1, massive.MAX_CONCURRENT_REQUESTS - 1)
PROVIDER_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.5
RETRY_DELAY_CAP_SECONDS = 30.0
RECENT_REFRESH_SESSIONS = 2
SPLIT_CAPTURE_TTL_SECONDS = 15 * 60
DB_NAME = "all-market-bars-v2.sqlite"
_TICKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,31}$")
_REFERENCE_PATH = "/v3/reference/tickers"
_SPLIT_PATH = "/stocks/v1/splits"
_MAX_SPLIT_PAGES = 50
_MAX_DIRECTORY_PAGES = 50
_NEW_YORK = ZoneInfo("America/New_York")
_RETRYABLE_CODES = frozenset({"rate_limited", "provider_busy", "transport"})
# Failures confined to one day's content. Anything else (key, plan, rate
# limit, network, redirect, a missing endpoint) would repeat for every day.
_DAY_SCOPED_CODES = frozenset({"empty_session", "protocol"})
_REASON_BY_CODE = {"empty_session": "EMPTY_TRADING_SESSION"}
_retry_jitter = random.Random()


class AllMarketDataError(RuntimeError):
    """A complete all-market input could not be assembled.

    ``reason_code`` names the earliest failure; ``failed_sessions`` holds every
    ``(session, reason_code)`` pair of a daily fetch, oldest first.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str | None = None,
        failed_sessions: Sequence[tuple[str, str]] = (),
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.failed_sessions = tuple(failed_sessions)


def _data_root(root: Path | str | None) -> Path:
    return get_data_paths(root).root if root is not None else get_data_paths().root


def _db_path(root: Path | str | None) -> Path:
    return _data_root(root) / "eod-limited-v1" / DB_NAME


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_sessions (
            session_date TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            provider_status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raw_daily_bars (
            ticker TEXT NOT NULL,
            session_date TEXT NOT NULL,
            timestamp_ms INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            vwap REAL,
            transactions INTEGER,
            PRIMARY KEY (ticker, session_date),
            FOREIGN KEY (session_date) REFERENCES market_sessions(session_date)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS raw_daily_bars_session
            ON raw_daily_bars(session_date, ticker);
        CREATE TABLE IF NOT EXISTS splits (
            ticker TEXT NOT NULL,
            execution_date TEXT NOT NULL,
            split_from REAL NOT NULL,
            split_to REAL NOT NULL,
            provider_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (ticker, execution_date, split_from, split_to)
        );
        CREATE TABLE IF NOT EXISTS split_captures (
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            provider_status TEXT NOT NULL,
            PRIMARY KEY (window_start, window_end)
        );
        """
    )
    return connection


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _retry_delay(attempt: int, error: massive.MassiveError) -> float:
    """Exponential backoff with jitter; a Retry-After hint wins, up to the cap."""

    delay = RETRY_DELAY_SECONDS * (2 ** attempt)
    if error.retry_after is not None:
        delay = max(delay, error.retry_after)
    # Jitter keeps the fetch threads from retrying in lockstep.
    return min(RETRY_DELAY_CAP_SECONDS, delay * _retry_jitter.uniform(1.0, 1.5))


def _provider_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    if PROVIDER_ATTEMPTS < 1:
        raise ValueError("PROVIDER_ATTEMPTS must be positive")
    for attempt in range(PROVIDER_ATTEMPTS):
        try:
            return massive._get(path, params)  # noqa: SLF001 - one authenticated transport boundary
        except massive.MassiveError as exc:
            retryable = exc.code in _RETRYABLE_CODES or (
                exc.code == "http" and isinstance(exc.status, int) and exc.status >= 500
            )
            if not retryable or attempt + 1 >= PROVIDER_ATTEMPTS:
                raise
            time.sleep(_retry_delay(attempt, exc))
    raise AssertionError("unreachable: the last attempt returns or raises")


def _fetch_directory() -> list[dict[str, Any]]:
    """Read the provider directory without case-folding distinct securities."""

    records: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(_MAX_DIRECTORY_PAGES):
        params: dict[str, Any]
        if cursor is None:
            params = {
                "active": "true",
                "market": "stocks",
                "locale": "us",
                "limit": 1000,
                "sort": "ticker",
                "order": "asc",
            }
        else:
            params = {"cursor": cursor}
        payload = _provider_get(_REFERENCE_PATH, params)
        if payload.get("status") not in {None, "OK"}:
            raise massive.MassiveError("reference directory provider status was not OK", code="protocol")
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise massive.MassiveError("unexpected reference directory shape", code="protocol")
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise massive.MassiveError("unexpected reference directory row", code="protocol")
            ticker = str(raw.get("ticker") or "").strip()
            if not _TICKER.fullmatch(ticker):
                raise massive.MassiveError("reference directory row has an invalid ticker", code="protocol")
            if ticker in records:
                raise massive.MassiveError("reference directory has an exact duplicate ticker", code="protocol")
            records[ticker] = {
                "ticker": ticker,
                "name": str(raw.get("name") or "").strip() or ticker,
                "market": str(raw.get("market") or "").strip(),
                "type": str(raw.get("type") or "").strip(),
                "primary_exchange": str(raw.get("primary_exchange") or "").strip(),
                "locale": str(raw.get("locale") or "").strip(),
                "currency_symbol": str(raw.get("currency_symbol") or "").strip(),
                "active": raw.get("active"),
            }
        next_url = payload.get("next_url")
        if next_url is None or next_url == "":
            return [records[ticker] for ticker in sorted(records)]
        next_cursor = massive._page_cursor(next_url, path=_REFERENCE_PATH)  # noqa: SLF001
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise massive.MassiveError("reference directory pagination did not advance", code="protocol")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise massive.MassiveError("reference directory pagination exceeded safety limit", code="protocol")


def _raw_bar(session: date, raw: Mapping[str, Any]) -> tuple[Any, ...]:
    ticker = str(raw.get("T") or "").strip()
    if not _TICKER.fullmatch(ticker):
        raise massive.MassiveError("grouped daily row has an invalid ticker", code="protocol")
    timestamp_ms = _integer(raw.get("t"))
    if timestamp_ms is None or timestamp_ms <= 0:
        raise massive.MassiveError("grouped daily row has no valid timestamp", code="protocol")
    try:
        timestamp_session = datetime.fromtimestamp(
            timestamp_ms / 1000.0,
            timezone.utc,
        ).astimezone(_NEW_YORK).date()
    except (OverflowError, OSError, ValueError) as exc:
        raise massive.MassiveError("grouped daily row timestamp is invalid", code="protocol") from exc
    if timestamp_session != session:
        raise massive.MassiveError("grouped daily row belongs to another session", code="protocol")
    return (
        ticker,
        session.isoformat(),
        timestamp_ms,
        _number(raw.get("o")),
        _number(raw.get("h")),
        _number(raw.get("l")),
        _number(raw.get("c")),
        _number(raw.get("v")),
        _number(raw.get("vw")),
        _integer(raw.get("n")),
    )


def _fetch_grouped_session(session: date) -> dict[str, Any]:
    day = session.isoformat()
    payload = _provider_get(
        f"/v2/aggs/grouped/locale/us/market/stocks/{day}",
        {"adjusted": "false", "include_otc": "false"},
    )
    rows = payload.get("results")
    if payload.get("status") not in {None, "OK"}:
        raise massive.MassiveError("grouped daily provider status was not OK", code="protocol")
    declared = payload.get("resultsCount")
    if (rows is None or rows == []) and _integer(declared) in {None, 0}:
        # The calendar expects a session on this day, so no rows means an
        # unscheduled closure missing from market_calendar.SPECIAL_CLOSURES
        # or a provider gap. Neither may be stored as an empty market.
        raise massive.MassiveError(f"empty grouped daily response for {day}", code="empty_session")
    if not isinstance(rows, list):
        raise massive.MassiveError("unexpected grouped daily results shape", code="protocol")
    if declared is not None and _integer(declared) != len(rows):
        raise massive.MassiveError("incomplete grouped daily response", code="protocol")
    if payload.get("adjusted") is not False:
        raise massive.MassiveError("grouped daily response did not confirm raw bars", code="protocol")
    parsed: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise massive.MassiveError("grouped daily row shape is invalid", code="protocol")
        item = _raw_bar(session, raw)
        if item[0] in seen:
            raise massive.MassiveError("grouped daily response has duplicate tickers", code="protocol")
        seen.add(str(item[0]))
        parsed.append(item)
    if not parsed:
        raise massive.MassiveError("grouped daily response had no usable symbols", code="protocol")
    hash_rows = sorted(parsed, key=lambda item: item[0])
    return {
        "session": session,
        "rows": parsed,
        "result_count": len(rows),
        "content_sha256": _hash_json(hash_rows),
        "provider_status": str(payload.get("status") or "OK"),
    }


def _stored_sessions(connection: sqlite3.Connection, sessions: Sequence[date]) -> set[str]:
    wanted = {value.isoformat() for value in sessions}
    if not wanted:
        return set()
    start, end = min(wanted), max(wanted)
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT session_date FROM market_sessions WHERE session_date BETWEEN ? AND ?",
            (start, end),
        )
        if str(row[0]) in wanted
    }


def _store_grouped_capture(connection: sqlite3.Connection, capture: Mapping[str, Any]) -> None:
    session = capture["session"].isoformat()
    with connection:
        connection.execute("DELETE FROM market_sessions WHERE session_date = ?", (session,))
        connection.execute(
            "INSERT INTO market_sessions "
            "(session_date, fetched_at, result_count, content_sha256, provider_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session,
                datetime.now(timezone.utc).isoformat(),
                int(capture["result_count"]),
                str(capture["content_sha256"]),
                str(capture["provider_status"]),
            ),
        )
        connection.executemany(
            "INSERT INTO raw_daily_bars "
            "(ticker, session_date, timestamp_ms, open, high, low, close, volume, vwap, transactions) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            capture["rows"],
        )


def _failure_reason(error: Exception) -> str:
    if isinstance(error, massive.MassiveError):
        return _REASON_BY_CODE.get(error.code, f"MASSIVE_{str(error.code).upper()}")
    return "FETCH_FAILED"


def _day_scoped(error: Exception) -> bool:
    return isinstance(error, massive.MassiveError) and error.code in _DAY_SCOPED_CODES


def _fill_missing_sessions(connection: sqlite3.Connection, sessions: Sequence[date]) -> None:
    stored = _stored_sessions(connection, sessions)
    refresh = {
        session.isoformat()
        for session in sessions[-min(RECENT_REFRESH_SESSIONS, len(sessions)) :]
    }
    missing = [
        session
        for session in sessions
        if session.isoformat() not in stored or session.isoformat() in refresh
    ]
    failures: list[tuple[date, Exception]] = []
    for offset in range(0, len(missing), MAX_PROVIDER_CONCURRENCY):
        batch = missing[offset : offset + MAX_PROVIDER_CONCURRENCY]
        batch_failures: list[tuple[date, Exception]] = []
        with ThreadPoolExecutor(
            max_workers=min(MAX_PROVIDER_CONCURRENCY, len(batch)),
            thread_name_prefix="all-market-daily",
        ) as pool:
            futures = {pool.submit(_fetch_grouped_session, session): session for session in batch}
            for future in as_completed(futures):
                session = futures[future]
                try:
                    capture = future.result()
                except Exception as exc:
                    batch_failures.append((session, exc))
                    continue
                # Each completed day commits on its own; a failed sibling in
                # the same batch never discards it.
                _store_grouped_capture(connection, capture)
        failures.extend(batch_failures)
        # One bad day must not leave the later days unrequested; a failure
        # that would repeat for every day stops after this batch instead. A
        # batch failing as a whole is treated the same way even when every
        # error looks day-scoped: a bad response shape hits every day alike,
        # and an empty cache would otherwise request the whole window.
        if any(not _day_scoped(exc) for _session, exc in batch_failures) or (
            batch_failures and len(batch_failures) == len(batch)
        ):
            break
    if not failures:
        return
    failures.sort(key=lambda item: item[0])
    failed = [(session.isoformat(), _failure_reason(exc)) for session, exc in failures]
    # The reason names what stopped the loader, not merely the earliest day.
    stopper = next(((s, exc) for s, exc in failures if not _day_scoped(exc)), failures[0])
    reason_session, reason_exc = stopper[0].isoformat(), stopper[1]
    reason = _failure_reason(reason_exc)
    more = f" and {len(failed) - 1} more session(s)" if len(failed) > 1 else ""
    raise AllMarketDataError(
        f"Massive grouped daily fetch failed for {reason_session} ({reason}){more}",
        reason_code=reason,
        failed_sessions=failed,
    ) from reason_exc


def _split_row(raw: Mapping[str, Any], start: date, end: date) -> tuple[Any, ...]:
    ticker = str(raw.get("ticker") or "").strip()
    try:
        execution = date.fromisoformat(str(raw.get("execution_date") or ""))
    except ValueError as exc:
        raise massive.MassiveError("split row has an invalid execution date", code="protocol") from exc
    split_from = _number(raw.get("split_from"), positive=True)
    split_to = _number(raw.get("split_to"), positive=True)
    if not _TICKER.fullmatch(ticker):
        raise massive.MassiveError("split row has an invalid ticker", code="protocol")
    if execution < start or execution > end:
        raise massive.MassiveError("split row is outside the requested window", code="protocol")
    if split_from is None or split_to is None:
        raise massive.MassiveError("split row has an invalid ratio", code="protocol")
    return (
        ticker,
        execution.isoformat(),
        split_from,
        split_to,
        str(raw.get("id") or "")[:200],
    )


def _fetch_splits(start: date, end: date) -> dict[str, Any]:
    rows: dict[tuple[str, str, float, float], tuple[Any, ...]] = {}
    cursor: str | None = None
    seen: set[str] = set()
    status = "OK"
    for _ in range(_MAX_SPLIT_PAGES):
        params: dict[str, Any]
        if cursor is None:
            params = {
                "execution_date.gte": start.isoformat(),
                "execution_date.lte": end.isoformat(),
                "limit": 1000,
                "sort": "execution_date.desc",
            }
        else:
            params = {"cursor": cursor}
        payload = _provider_get(_SPLIT_PATH, params)
        if payload.get("status") not in {None, "OK"}:
            raise massive.MassiveError("splits provider status was not OK", code="protocol")
        raw_rows = payload.get("results")
        if not isinstance(raw_rows, list):
            raise massive.MassiveError("unexpected splits payload shape", code="protocol")
        status = str(payload.get("status") or status)
        for raw in raw_rows:
            if not isinstance(raw, Mapping):
                raise massive.MassiveError("unexpected split row shape", code="protocol")
            item = _split_row(raw, start, end)
            rows[(item[0], item[1], item[2], item[3])] = item
        next_url = payload.get("next_url")
        if next_url is None or next_url == "":
            ordered = [rows[key] for key in sorted(rows)]
            return {
                "rows": ordered,
                "result_count": len(ordered),
                "content_sha256": _hash_json(ordered),
                "provider_status": status,
            }
        next_cursor = massive._page_cursor(next_url, path=_SPLIT_PATH)  # noqa: SLF001
        if next_cursor == cursor or next_cursor in seen:
            raise massive.MassiveError("splits pagination did not advance", code="protocol")
        seen.add(next_cursor)
        cursor = next_cursor
    raise massive.MassiveError("splits pagination exceeded safety limit", code="protocol")


def _split_capture(connection: sqlite3.Connection, start: date, end: date) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT * FROM split_captures WHERE window_start = ? AND window_end = ?",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    if row is None:
        return None
    try:
        fetched_at = datetime.fromisoformat(str(row["fetched_at"]))
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age = (datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)).total_seconds()
    return row if 0 <= age < SPLIT_CAPTURE_TTL_SECONDS else None


def _ensure_splits(connection: sqlite3.Connection, start: date, end: date) -> sqlite3.Row:
    existing = _split_capture(connection, start, end)
    if existing is not None:
        return existing
    try:
        capture = _fetch_splits(start, end)
    except Exception as exc:
        raise AllMarketDataError(
            f"Massive split fetch failed for {start.isoformat()} through {end.isoformat()}"
        ) from exc
    with connection:
        connection.execute(
            "DELETE FROM split_captures WHERE window_start = ? AND window_end = ?",
            (start.isoformat(), end.isoformat()),
        )
        connection.execute(
            "DELETE FROM splits WHERE execution_date BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
        connection.executemany(
            "INSERT INTO splits "
            "(ticker, execution_date, split_from, split_to, provider_id) VALUES (?, ?, ?, ?, ?)",
            capture["rows"],
        )
        connection.execute(
            "INSERT INTO split_captures "
            "(window_start, window_end, fetched_at, result_count, content_sha256, provider_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                start.isoformat(),
                end.isoformat(),
                datetime.now(timezone.utc).isoformat(),
                int(capture["result_count"]),
                str(capture["content_sha256"]),
                str(capture["provider_status"]),
            ),
        )
    stored = _split_capture(connection, start, end)
    assert stored is not None
    return stored


def _required_sessions(end: date) -> list[date]:
    if not is_trading_day(end):
        raise ValueError("all-market end must be a US trading session")
    return [*prior_trading_sessions(end, HISTORY_SESSIONS - 1), end]


def _valid_ohlc(row: Mapping[str, Any]) -> bool:
    values = [_number(row[key], positive=True) for key in ("open", "high", "low", "close")]
    if any(value is None for value in values):
        return False
    open_, high, low, close = (float(value) for value in values)
    return low <= min(open_, close) and high >= max(open_, close) and low <= high


def _split_map(
    connection: sqlite3.Connection,
    members: Mapping[str, UniverseMember],
    start: date,
    end: date,
) -> dict[str, list[tuple[date, float, float]]]:
    out: dict[str, list[tuple[date, float, float]]] = {}
    for row in connection.execute(
        "SELECT ticker, execution_date, split_from, split_to FROM splits "
        "WHERE execution_date BETWEEN ? AND ? ORDER BY ticker, execution_date",
        (start.isoformat(), end.isoformat()),
    ):
        ticker = str(row["ticker"])
        if ticker not in members:
            continue
        out.setdefault(ticker, []).append(
            (
                date.fromisoformat(str(row["execution_date"])),
                float(row["split_from"]),
                float(row["split_to"]),
            )
        )
    return out


def _series_from_rows(
    member: UniverseMember,
    rows: Sequence[Mapping[str, Any]],
    *,
    splits: Sequence[tuple[date, float, float]],
    dates: list[date] | None = None,
) -> SecuritySeries:
    if dates is None:
        dates = [date.fromisoformat(str(row["session_date"])) for row in rows]
    raw_open = np.asarray([float(row["open"]) for row in rows], dtype=float)
    raw_high = np.asarray([float(row["high"]) for row in rows], dtype=float)
    raw_low = np.asarray([float(row["low"]) for row in rows], dtype=float)
    raw_close = np.asarray([float(row["close"]) for row in rows], dtype=float)
    raw_volume = np.asarray(
        [float(row["volume"]) if _number(row["volume"], nonnegative=True) is not None else np.nan for row in rows],
        dtype=float,
    )
    price_factor = np.ones(len(rows), dtype=float)
    volume_factor = np.ones(len(rows), dtype=float)
    for execution, split_from, split_to in splits:
        before = np.asarray([session < execution for session in dates], dtype=bool)
        price_factor[before] *= split_from / split_to
        volume_factor[before] *= split_to / split_from
    adjusted_open = raw_open * price_factor
    adjusted_high = raw_high * price_factor
    adjusted_low = raw_low * price_factor
    adjusted_close = raw_close * price_factor
    adjusted_volume = raw_volume * volume_factor
    dollar_volume = raw_close * raw_volume
    adjusted_flags = price_factor != 1.0
    price_labels = tuple(
        "split_adjusted_from_raw" if adjusted else "raw_no_later_split"
        for adjusted in adjusted_flags
    )
    volume_labels = tuple(
        "split_adjusted_from_raw_unverified" if adjusted else "raw_unverified"
        for adjusted in adjusted_flags
    )
    return SecuritySeries(
        security_id=member.ticker,
        ticker_at_signal=member.ticker,
        dates=dates,
        open=adjusted_open,
        high=adjusted_high,
        low=adjusted_low,
        close=adjusted_close,
        raw_close=raw_close,
        volume=adjusted_volume,
        dollar_volume=dollar_volume,
        tri=adjusted_close.copy(),
        turnover_is_proxy=True,
        volume_session_scope=VOLUME_SCOPE,
        asset_track=member.asset_track,
        industry_id=None,
        parent_industry_id=None,
        theme_ids=member.theme_ids,
        venue_metadata=member.venue_metadata,
        source_available_at=None,
        halted=False,
        raw_open=raw_open,
        splits=tuple((execution, split_to / split_from) for execution, split_from, split_to in splits),
        bar_partial=np.zeros(len(rows), dtype=bool),
        bar_halted=np.zeros(len(rows), dtype=bool),
        vintage_status=tuple("download_time_not_pit" for _ in rows),
        price_adjustment=price_labels,
        volume_adjustment=volume_labels,
        tri_verified=False,
        reconstruction_mode="live_current_membership_reconstruction",
        vendor_tri=adjusted_close.copy(),
        return_basis="close_price_return",
        return_transform_version="all-market-split-adjusted-close-v1",
    )


def _load_panel(
    connection: sqlite3.Connection,
    members: Mapping[str, UniverseMember],
    coverage: list[dict[str, Any]],
    sessions: Sequence[date],
) -> tuple[dict[str, SecuritySeries], list[dict[str, Any]], int, int, int]:
    start, end = sessions[0], sessions[-1]
    split_by_ticker = _split_map(connection, members, start, end)
    by_ticker = {str(row["ticker"]): row for row in coverage}
    # One date object per session, and one shared list for every security with
    # a bar on every session, instead of fresh dates for each of ~11k series.
    # Series never mutate their date list; slicing always builds a new one.
    window = list(sessions)
    session_by_key = {session.isoformat(): session for session in window}
    panel: dict[str, SecuritySeries] = {}
    current_ticker: str | None = None
    current_rows: list[sqlite3.Row] = []
    short_history_count = 0
    residual_short_history_count = 0
    missing_session_count = 0

    def flush() -> None:
        nonlocal current_ticker, current_rows, short_history_count
        nonlocal residual_short_history_count, missing_session_count
        if current_ticker is None:
            return
        coverage_row = by_ticker[current_ticker]
        coverage_row["bars"] = len(current_rows)
        if not current_rows:
            coverage_row.update(status="no_history", reason="NO_HISTORY")
            return
        if any(not _valid_ohlc(row) for row in current_rows):
            coverage_row.update(status="invalid", reason="INVALID_OHLC")
            return
        if str(current_rows[-1]["session_date"]) != end.isoformat():
            coverage_row.update(status="missing_session", reason="MISSING_TARGET_SESSION")
            missing_session_count += 1
            return
        short = len(current_rows) < SHORT_HISTORY_SESSIONS
        residual_short = len(current_rows) < RESIDUAL_HISTORY_SESSIONS
        coverage_row.update(
            status="ok",
            short_history=short,
            residual_short_history=residual_short,
        )
        if short:
            short_history_count += 1
        if residual_short:
            residual_short_history_count += 1
        days = [
            session_by_key.get(key) or date.fromisoformat(key)
            for key in (str(row["session_date"]) for row in current_rows)
        ]
        panel[current_ticker] = _series_from_rows(
            members[current_ticker],
            current_rows,
            splits=split_by_ticker.get(current_ticker, ()),
            dates=window if days == window else days,
        )

    for ticker in members:
        rows = list(
            connection.execute(
                "SELECT * FROM raw_daily_bars "
                "WHERE ticker = ? AND session_date BETWEEN ? AND ? "
                "ORDER BY session_date",
                (ticker, start.isoformat(), end.isoformat()),
            )
        )
        if not rows:
            continue
        current_ticker = ticker
        current_rows = rows
        flush()
    for ticker in members:
        row = by_ticker[ticker]
        if row["status"] == "pending":
            row.update(
                status="no_history",
                reason="NO_HISTORY",
                bars=0,
                short_history=True,
                residual_short_history=True,
            )
    return (
        panel,
        coverage,
        missing_session_count,
        short_history_count,
        residual_short_history_count,
    )


def _session_manifest(connection: sqlite3.Connection, sessions: Sequence[date]) -> list[dict[str, Any]]:
    wanted = {session.isoformat() for session in sessions}
    rows = connection.execute(
        "SELECT session_date, result_count, content_sha256, provider_status "
        "FROM market_sessions WHERE session_date BETWEEN ? AND ? ORDER BY session_date",
        (sessions[0].isoformat(), sessions[-1].isoformat()),
    )
    out = [dict(row) for row in rows if str(row["session_date"]) in wanted]
    if len(out) != len(sessions):
        raise AllMarketDataError("all-market cache is missing required sessions")
    return out


def _prune_rolling_cache(connection: sqlite3.Connection, start: date) -> None:
    with connection:
        connection.execute("DELETE FROM market_sessions WHERE session_date < ?", (start.isoformat(),))
        connection.execute("DELETE FROM split_captures WHERE window_end < ?", (start.isoformat(),))
        connection.execute("DELETE FROM splits WHERE execution_date < ?", (start.isoformat(),))


def load_all_market_panel(
    *,
    end: date,
    root: Path | str | None = None,
    tickers: Iterable[str] | None = None,
) -> tuple[dict[str, SecuritySeries], list[dict[str, Any]], dict[str, Any]]:
    """Load a complete current all-market panel from a resumable raw-bar cache.

    Provider failures propagate after any independently completed day has been
    committed. A retry requests only missing days; no partial capture receives a
    complete manifest.
    """

    sessions = _required_sessions(end)
    requested_tickers = (
        None
        if tickers is None
        else sorted({str(value).strip().upper() for value in tickers if str(value).strip()})
    )
    directory = _fetch_directory()
    if not directory:
        raise AllMarketDataError("Massive reference directory is empty")
    members, coverage = select_all_market_universe(directory, tickers=requested_tickers)
    if not members:
        raise AllMarketDataError("all-market universe has no eligible securities")
    directory_projection = [
        {
            "ticker": str(row.get("ticker") or ""),
            "name": str(row.get("name") or ""),
            "type": str(row.get("type") or ""),
            "primary_exchange": str(row.get("primary_exchange") or ""),
            "active": row.get("active"),
        }
        for row in directory
    ]
    directory_hash = _hash_json(sorted(directory_projection, key=lambda item: item["ticker"]))
    member_projection = [
        {
            "ticker": member.ticker,
            "provider_type": member.provider_type,
            "primary_exchange": member.primary_exchange,
            "asset_track": member.asset_track,
            "theme_ids": list(member.theme_ids),
        }
        for member in sorted(members.values(), key=lambda item: item.ticker)
    ]
    member_hash = _hash_json(member_projection)
    path = _db_path(root)
    with closing(_connect(path)) as connection:
        _fill_missing_sessions(connection, sessions)
        split_capture = _ensure_splits(connection, sessions[0], sessions[-1])
        session_meta = _session_manifest(connection, sessions)
        panel, coverage, missing_count, short_count, residual_short_count = _load_panel(
            connection,
            members,
            coverage,
            sessions,
        )
        _prune_rolling_cache(connection, sessions[0])

    eligible_count = len(members)
    excluded_count = len(directory) - eligible_count
    no_history_count = sum(row.get("status") == "no_history" for row in coverage)
    invalid_count = sum(row.get("status") == "invalid" for row in coverage)
    # Every eligible member lands in exactly one class. missing_session_count
    # is only "no bar on the target session", never no history or bad bars.
    if len(panel) + missing_count + no_history_count + invalid_count != eligible_count:
        raise AllMarketDataError("all-market coverage accounting is inconsistent")
    session_hash = _hash_json(session_meta)
    source_hash = _hash_json(
        {
            "directory": directory_hash,
            "members": member_hash,
            "requested_subset": requested_tickers,
            "sessions": session_hash,
            "splits": str(split_capture["content_sha256"]),
        }
    )
    manifest = {
        "universe": "all_market",
        "cache_schema_version": 2,
        "status": "complete",
        "directory_count": len(directory),
        "eligible_count": eligible_count,
        "excluded_count": excluded_count,
        "complete_bar_count": len(panel),
        "missing_session_count": missing_count,
        "short_history_count": short_count,
        "residual_short_history_count": residual_short_count,
        "provider": "Massive",
        "source_status": "complete",
        "source_dates": {
            "start": sessions[0].isoformat(),
            "end": sessions[-1].isoformat(),
            "count": len(sessions),
            "hash": _hash_json([session.isoformat() for session in sessions]),
        },
        "source_hash": source_hash,
        "directory_hash": directory_hash,
        "eligible_member_hash": member_hash,
        "requested_subset": requested_tickers,
        "bar_content_hash": session_hash,
        "split_content_hash": str(split_capture["content_sha256"]),
        "split_count": int(split_capture["result_count"]),
        "bars_adjusted_by_provider": False,
        "price_adjustment": "local_split_events_from_to",
        "volume_adjustment": "local_inverse_split_unverified",
        "volume_session_scope": VOLUME_SCOPE,
        "dollar_volume_basis": "raw_close_x_raw_volume_unverified",
        "no_history_count": no_history_count,
        "invalid_count": invalid_count,
        "membership_basis": "current_active_reference_not_historical_point_in_time",
        "security_identity": "provider_ticker_without_stable_figi",
        "notes": [
            "Current-membership live inference only; this manifest is not historical PIT membership.",
            "Missing aggregate rows are not imputed as zero-volume or halted sessions.",
        ],
        "cache_path": str(path),
    }
    return panel, coverage, manifest


__all__ = [
    "AllMarketDataError",
    "DB_NAME",
    "MAX_PROVIDER_CONCURRENCY",
    "HISTORY_SESSIONS",
    "RESIDUAL_HISTORY_SESSIONS",
    "RECENT_REFRESH_SESSIONS",
    "SHORT_HISTORY_SESSIONS",
    "VOLUME_SCOPE",
    "load_all_market_panel",
]
