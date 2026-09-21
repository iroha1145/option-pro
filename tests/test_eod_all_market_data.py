from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from app.services import massive
from app.services.eod_limited import market_data
from app.services.eod_limited.market_data import AllMarketDataError, load_all_market_panel
from app.services.eod_limited.universe import select_all_market_universe


END = date(2026, 9, 16)
ET = ZoneInfo("America/New_York")


def _directory_row(
    ticker: str,
    provider_type: str = "CS",
    exchange: str = "XNAS",
) -> dict:
    return {
        "ticker": ticker,
        "name": f"{ticker} Incorporated",
        "market": "stocks",
        "type": provider_type,
        "primary_exchange": exchange,
        "locale": "us",
        "currency_symbol": "USD",
        "active": True,
    }


def _bar(ticker: str, close: float, volume: float = 100.0) -> dict:
    return {
        "T": ticker,
        "o": close * 0.98,
        "h": close * 1.02,
        "l": close * 0.97,
        "c": close,
        "v": volume,
        "vw": close * 0.995,
        "n": 10,
    }


def _rows_for_day(rows: list[dict], day: str) -> list[dict]:
    session = date.fromisoformat(day)
    timestamp_ms = int(datetime(session.year, session.month, session.day, 16, tzinfo=ET).timestamp() * 1000)
    return [{**row, "t": timestamp_ms} for row in rows]


def _install_provider(
    monkeypatch: pytest.MonkeyPatch,
    directory: list[dict],
    grouped: dict[str, list[dict]],
    *,
    splits: list[dict] | None = None,
    calls: list[tuple[str, dict]] | None = None,
) -> None:
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(market_data, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: [dict(row) for row in directory])

    def fake_get(path: str, params=None):
        params = dict(params or {})
        if calls is not None:
            calls.append((path, params))
        if path.startswith("/v2/aggs/grouped/locale/us/market/stocks/"):
            day = path.rsplit("/", 1)[-1]
            rows = _rows_for_day(grouped.get(day, []), day)
            return {
                "adjusted": False,
                "status": "OK",
                "resultsCount": len(rows),
                "results": [dict(row) for row in rows],
            }
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": list(splits or [])}
        raise AssertionError(path)

    monkeypatch.setattr(massive, "_get", fake_get)


def test_universe_types_exchanges_and_diagnostic_subset_have_complete_ledger() -> None:
    directory = [
        _directory_row("CS1", "CS", "XNYS"),
        _directory_row("ADR1", "ADRC", "XNAS"),
        _directory_row("ETF1", "ETF", "ARCX"),
        _directory_row("ETS1", "ETS", "BATS"),
        _directory_row("ETV1", "ETV", "XASE"),
        _directory_row("ETN1", "ETN", "XNAS"),
        _directory_row("FUND1", "FUND", "XNYS"),
        _directory_row("PFD1", "PFD", "XNYS"),
        _directory_row("OTC1", "CS", "OTCM"),
    ]
    members, coverage = select_all_market_universe(directory)
    assert set(members) == {"CS1", "ADR1", "ETF1", "ETS1", "ETV1", "ETN1", "FUND1"}
    assert len(coverage) == len(directory)
    assert members["ADR1"].venue_metadata["provider_type"] == "ADRC"
    assert members["ADR1"].venue_metadata["security_type"] == "ADR"
    assert members["ETF1"].asset_track == "etf"
    assert members["ETF1"].theme_ids == ("etfs",)
    assert members["CS1"].theme_ids == ("all_market_stocks",)
    reasons = {row["ticker"]: row["status"] for row in coverage}
    assert reasons["PFD1"] == "excluded:UNSUPPORTED_SECURITY_TYPE:PFD"
    assert reasons["OTC1"] == "excluded:NOT_US_MAJOR_EXCHANGE:OTCM"

    subset, subset_coverage = select_all_market_universe(directory, tickers=["ADR1"])
    assert set(subset) == {"ADR1"}
    assert len(subset_coverage) == len(directory)
    assert next(row for row in subset_coverage if row["ticker"] == "CS1")["status"] == "excluded:NOT_REQUESTED"


def test_load_panel_applies_split_only_before_execution_and_preserves_raw_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    grouped = {
        "2026-09-14": [_bar("AAAA", 100.0, 10.0)],
        "2026-09-15": [_bar("AAAA", 25.0, 40.0)],
        "2026-09-16": [_bar("AAAA", 26.0, 50.0)],
    }
    splits = [
        {
            "id": "split-1",
            "ticker": "AAAA",
            "execution_date": "2026-09-15",
            "split_from": 1,
            "split_to": 4,
        }
    ]
    _install_provider(monkeypatch, [_directory_row("AAAA")], grouped, splits=splits)

    panel, coverage, manifest = load_all_market_panel(end=END, root=tmp_path)
    series = panel["AAAA"]
    assert series.close.tolist() == pytest.approx([25.0, 25.0, 26.0])
    assert series.raw_close.tolist() == pytest.approx([100.0, 25.0, 26.0])
    assert series.volume.tolist() == pytest.approx([40.0, 40.0, 50.0])
    assert series.dollar_volume.tolist() == pytest.approx([1000.0, 1000.0, 1300.0])
    assert series.price_adjustment == (
        "split_adjusted_from_raw",
        "raw_no_later_split",
        "raw_no_later_split",
    )
    assert series.venue_metadata["name"] == "AAAA Incorporated"
    assert series.volume_session_scope == market_data.VOLUME_SCOPE
    assert coverage[0]["status"] == "ok"
    assert coverage[0]["short_history"] is True
    assert manifest["source_status"] == "complete"
    assert manifest["status"] == "complete"
    assert manifest["complete_bar_count"] == 1
    assert manifest["split_count"] == 1
    assert manifest["bars_adjusted_by_provider"] is False


def test_daily_cache_fetches_only_new_session_when_window_advances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    grouped = {
        "2026-09-11": [_bar("AAAA", 10.0)],
        "2026-09-14": [_bar("AAAA", 11.0)],
        "2026-09-15": [_bar("AAAA", 12.0)],
        "2026-09-16": [_bar("AAAA", 13.0)],
    }
    calls: list[tuple[str, dict]] = []
    _install_provider(monkeypatch, [_directory_row("AAAA")], grouped, calls=calls)

    load_all_market_panel(end=date(2026, 9, 15), root=tmp_path)
    load_all_market_panel(end=date(2026, 9, 16), root=tmp_path)

    days = [path.rsplit("/", 1)[-1] for path, _ in calls if "/grouped/" in path]
    assert Counter(days) == Counter(
        {"2026-09-11": 1, "2026-09-14": 1, "2026-09-15": 2, "2026-09-16": 1}
    )


def test_failed_day_is_resumable_without_refetching_completed_days(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = [_directory_row("AAAA")]
    grouped = {
        "2026-09-14": [_bar("AAAA", 10.0)],
        "2026-09-15": [_bar("AAAA", 11.0)],
        "2026-09-16": [_bar("AAAA", 12.0)],
    }
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(market_data, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: directory)
    fail = True
    calls: Counter[str] = Counter()

    def fake_get(path: str, params=None):
        nonlocal fail
        if "/grouped/" in path:
            day = path.rsplit("/", 1)[-1]
            calls[day] += 1
            if day == "2026-09-15" and fail:
                raise massive.MassiveError("transport", code="transport")
            rows = _rows_for_day(grouped[day], day)
            return {"adjusted": False, "status": "OK", "resultsCount": 1, "results": rows}
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": []}
        raise AssertionError(path)

    monkeypatch.setattr(massive, "_get", fake_get)
    with pytest.raises(AllMarketDataError, match="2026-09-15"):
        load_all_market_panel(end=END, root=tmp_path)
    assert calls["2026-09-14"] == 1
    assert calls["2026-09-16"] == 1

    fail = False
    panel, _coverage, _manifest = load_all_market_panel(end=END, root=tmp_path)
    assert set(panel) == {"AAAA"}
    assert calls["2026-09-14"] == 1
    assert calls["2026-09-16"] == 2
    assert calls["2026-09-15"] == 3  # two bounded attempts, then one resumed request


def test_failed_split_fetch_keeps_completed_day_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = [_directory_row("AAAA")]
    grouped = {
        "2026-09-14": [_bar("AAAA", 10.0)],
        "2026-09-15": [_bar("AAAA", 11.0)],
        "2026-09-16": [_bar("AAAA", 12.0)],
    }
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(market_data, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: directory)
    split_fail = True
    grouped_calls: Counter[str] = Counter()
    split_calls = 0

    def fake_get(path: str, params=None):
        nonlocal split_calls, split_fail
        if "/grouped/" in path:
            day = path.rsplit("/", 1)[-1]
            grouped_calls[day] += 1
            rows = _rows_for_day(grouped[day], day)
            return {"adjusted": False, "status": "OK", "resultsCount": 1, "results": rows}
        if path == "/stocks/v1/splits":
            split_calls += 1
            if split_fail:
                raise massive.MassiveError("transport", code="transport")
            return {"status": "OK", "results": []}
        raise AssertionError(path)

    monkeypatch.setattr(massive, "_get", fake_get)
    with pytest.raises(AllMarketDataError, match="split fetch failed"):
        load_all_market_panel(end=END, root=tmp_path)
    assert set(grouped_calls.values()) == {1}

    split_fail = False
    load_all_market_panel(end=END, root=tmp_path)
    assert grouped_calls == Counter(
        {"2026-09-14": 1, "2026-09-15": 2, "2026-09-16": 2}
    )
    assert split_calls == 3


def test_coverage_classifies_missing_history_session_invalid_and_excluded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = [
        _directory_row("GOOD"),
        _directory_row("MISS"),
        _directory_row("NONE"),
        _directory_row("BAD"),
        _directory_row("PREF", "PFD"),
    ]
    grouped = {
        "2026-09-14": [_bar("GOOD", 10), _bar("MISS", 20), _bar("BAD", 30)],
        "2026-09-15": [_bar("GOOD", 11), _bar("MISS", 21), _bar("BAD", 31)],
        "2026-09-16": [_bar("GOOD", 12), _bar("BAD", -1)],
    }
    _install_provider(monkeypatch, directory, grouped)

    panel, coverage, manifest = load_all_market_panel(end=END, root=tmp_path)
    statuses = {row["ticker"]: row["status"] for row in coverage}
    assert statuses == {
        "GOOD": "ok",
        "MISS": "missing_session",
        "NONE": "no_history",
        "BAD": "invalid",
        "PREF": "excluded:UNSUPPORTED_SECURITY_TYPE:PFD",
    }
    assert set(panel) == {"GOOD"}
    assert len(coverage) == len(directory)
    assert manifest["directory_count"] == 5
    assert manifest["eligible_count"] == 4
    assert manifest["excluded_count"] == 1
    assert manifest["complete_bar_count"] == 1
    assert manifest["missing_session_count"] == 3
    assert manifest["no_history_count"] == 1
    assert manifest["invalid_count"] == 1
    assert manifest["complete_bar_count"] + manifest["missing_session_count"] == manifest["eligible_count"]


def test_panel_reads_each_exact_member_with_no_global_temp_sort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tickers = ["CASE", "Case", *(f"T{index:03d}" for index in range(32))]
    directory = [_directory_row(ticker) for ticker in tickers]
    days = ("2026-09-14", "2026-09-15", "2026-09-16")
    grouped = {
        day: [
            _bar(ticker, 10.0 + ticker_index + day_index)
            for ticker_index, ticker in enumerate(tickers)
        ]
        for day_index, day in enumerate(days)
    }
    statements: list[str] = []
    _install_provider(monkeypatch, directory, grouped)
    original_connect = market_data._connect

    def traced_connect(path: Path) -> sqlite3.Connection:
        connection = original_connect(path)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(market_data, "_connect", traced_connect)
    panel, coverage, manifest = load_all_market_panel(end=END, root=tmp_path)

    assert set(panel) == set(tickers)
    assert all(row["status"] == "ok" for row in coverage)
    assert manifest["complete_bar_count"] == len(tickers)
    assert panel["CASE"].close.tolist() == pytest.approx([10.0, 11.0, 12.0])
    assert panel["Case"].close.tolist() == pytest.approx([11.0, 12.0, 13.0])
    member_reads = [
        statement
        for statement in statements
        if statement.startswith("SELECT * FROM raw_daily_bars WHERE ticker = ")
    ]
    assert len(member_reads) == len(tickers)
    assert all("ORDER BY session_date" in statement for statement in member_reads)
    assert not any("all_market_selected" in statement for statement in statements)

    database = tmp_path / "eod-limited-v1" / market_data.DB_NAME
    with sqlite3.connect(database) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM raw_daily_bars "
            "WHERE ticker = ? AND session_date BETWEEN ? AND ? "
            "ORDER BY session_date",
            ("CASE", days[0], days[-1]),
        ).fetchall()
    details = [str(row[3]).upper() for row in plan]
    assert any("SEARCH RAW_DAILY_BARS" in detail for detail in details)
    assert not any("TEMP B-TREE" in detail for detail in details)


def test_split_pagination_rebuilds_fixed_path_and_forwards_only_cursor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    grouped = {
        "2026-09-14": [_bar("AAAA", 100)],
        "2026-09-15": [_bar("AAAA", 25)],
        "2026-09-16": [_bar("AAAA", 26)],
    }
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: [_directory_row("AAAA")])
    split_calls: list[tuple[str, dict]] = []

    def fake_get(path: str, params=None):
        params = dict(params or {})
        if "/grouped/" in path:
            day = path.rsplit("/", 1)[-1]
            return {"adjusted": False, "status": "OK", "resultsCount": 1, "results": _rows_for_day(grouped[day], day)}
        assert path == "/stocks/v1/splits"
        split_calls.append((path, params))
        if "cursor" not in params:
            return {
                "status": "OK",
                "results": [],
                "next_url": "https://api.massive.com/stocks/v1/splits?cursor=page-2&apiKey=discard-me",
            }
        assert params == {"cursor": "page-2"}
        return {"status": "OK", "results": []}

    monkeypatch.setattr(massive, "_get", fake_get)
    load_all_market_panel(end=END, root=tmp_path)
    assert len(split_calls) == 2
    assert split_calls[0][1]["sort"] == "execution_date.desc"
    assert "order" not in split_calls[0][1]
    assert split_calls[1] == ("/stocks/v1/splits", {"cursor": "page-2"})


@pytest.mark.parametrize(
    "bad_row",
    [
        {"T": "BAD/TICKER", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        {"T": "AAAA", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
    ],
)
def test_grouped_capture_fails_closed_on_invalid_or_duplicate_ticker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bad_row: dict,
) -> None:
    grouped = {
        "2026-09-14": [_bar("AAAA", 10)],
        "2026-09-15": [_bar("AAAA", 11)],
        "2026-09-16": [_bar("AAAA", 12), bad_row],
    }
    _install_provider(monkeypatch, [_directory_row("AAAA")], grouped)
    with pytest.raises(AllMarketDataError, match="2026-09-16"):
        load_all_market_panel(end=END, root=tmp_path)


def test_split_capture_fails_closed_on_invalid_ratio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    grouped = {
        "2026-09-14": [_bar("AAAA", 10)],
        "2026-09-15": [_bar("AAAA", 11)],
        "2026-09-16": [_bar("AAAA", 12)],
    }
    _install_provider(
        monkeypatch,
        [_directory_row("AAAA")],
        grouped,
        splits=[
            {
                "ticker": "AAAA",
                "execution_date": "2026-09-15",
                "split_from": 0,
                "split_to": 4,
            }
        ],
    )
    with pytest.raises(AllMarketDataError, match="split fetch failed"):
        load_all_market_panel(end=END, root=tmp_path)


@pytest.mark.parametrize("adjusted", [None, True])
def test_grouped_capture_requires_explicit_raw_bars_and_matching_new_york_session(
    monkeypatch: pytest.MonkeyPatch,
    adjusted: bool | None,
) -> None:
    wrong_day = _rows_for_day([_bar("AAAA", 10)], "2026-09-15")
    monkeypatch.setattr(
        market_data,
        "_provider_get",
        lambda *_args, **_kwargs: {
            "status": "OK",
            "adjusted": adjusted,
            "resultsCount": 1,
            "results": wrong_day,
        },
    )
    with pytest.raises(massive.MassiveError) as captured:
        market_data._fetch_grouped_session(END)
    assert captured.value.code == "protocol"


def test_stale_split_capture_is_refreshed_and_subset_changes_source_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = [_directory_row("AAAA"), _directory_row("BBBB")]
    grouped = {
        "2026-09-14": [_bar("AAAA", 10), _bar("BBBB", 20)],
        "2026-09-15": [_bar("AAAA", 11), _bar("BBBB", 21)],
        "2026-09-16": [_bar("AAAA", 12), _bar("BBBB", 22)],
    }
    calls: list[tuple[str, dict]] = []
    _install_provider(monkeypatch, directory, grouped, calls=calls)
    _panel_a, _coverage_a, manifest_a = load_all_market_panel(
        end=END,
        root=tmp_path,
        tickers=["AAAA"],
    )
    database = tmp_path / "eod-limited-v1" / market_data.DB_NAME
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE split_captures SET fetched_at = ?",
            ("2020-01-01T00:00:00+00:00",),
        )
    _panel_b, _coverage_b, manifest_b = load_all_market_panel(
        end=END,
        root=tmp_path,
        tickers=["BBBB"],
    )
    split_calls = [item for item in calls if item[0] == "/stocks/v1/splits"]
    assert len(split_calls) == 2
    assert manifest_a["eligible_member_hash"] != manifest_b["eligible_member_hash"]
    assert manifest_a["source_hash"] != manifest_b["source_hash"]


def test_reference_directory_pagination_preserves_case_distinct_securities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def provider_get(path: str, params: dict) -> dict:
        calls.append((path, dict(params)))
        assert path == "/v3/reference/tickers"
        if "cursor" not in params:
            return {
                "status": "OK",
                "results": [
                    _directory_row("BCPC", "CS"),
                    _directory_row("BCpC", "PFD"),
                ],
                "next_url": "https://api.massive.com/v3/reference/tickers?cursor=page-2&apiKey=discard",
            }
        assert params == {"cursor": "page-2"}
        return {
            "status": "OK",
            "results": [
                _directory_row("TPC", "CS"),
                _directory_row("TpC", "PFD"),
            ],
        }

    monkeypatch.setattr(market_data, "_provider_get", provider_get)
    directory = market_data._fetch_directory()
    assert [row["ticker"] for row in directory] == ["BCPC", "BCpC", "TPC", "TpC"]
    assert calls[1] == ("/v3/reference/tickers", {"cursor": "page-2"})


def test_case_distinct_daily_and_split_rows_never_cross_security_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = [
        _directory_row("BCPC", "CS"),
        _directory_row("BCpC", "PFD"),
        _directory_row("TPC", "CS"),
        _directory_row("TpC", "PFD"),
    ]
    grouped = {
        day: [
            _bar("BCPC", 166.0, 100),
            _bar("BCpC", 23.52, 10),
            _bar("TPC", 23.18, 200),
            _bar("TpC", 19.07, 20),
        ]
        for day in ("2026-09-14", "2026-09-15", "2026-09-16")
    }
    _install_provider(
        monkeypatch,
        directory,
        grouped,
        splits=[
            {
                "ticker": "BCpC",
                "execution_date": "2026-09-15",
                "split_from": 1,
                "split_to": 4,
            }
        ],
    )
    panel, coverage, manifest = load_all_market_panel(end=END, root=tmp_path)
    assert set(panel) == {"BCPC", "TPC"}
    assert panel["BCPC"].close.tolist() == pytest.approx([166.0, 166.0, 166.0])
    assert panel["TPC"].close.tolist() == pytest.approx([23.18, 23.18, 23.18])
    statuses = {row["ticker"]: row["status"] for row in coverage}
    assert statuses["BCPC"] == statuses["TPC"] == "ok"
    assert statuses["BCpC"] == statuses["TpC"] == "excluded:UNSUPPORTED_SECURITY_TYPE:PFD"
    assert len(statuses) == 4
    assert manifest["directory_count"] == 4
    assert manifest["eligible_count"] == 2
    database = tmp_path / "eod-limited-v1" / market_data.DB_NAME
    with sqlite3.connect(database) as connection:
        cached = connection.execute(
            "SELECT ticker, close FROM raw_daily_bars WHERE session_date = ? ORDER BY ticker",
            ("2026-09-14",),
        ).fetchall()
    assert cached == [("BCPC", 166.0), ("BCpC", 23.52), ("TPC", 23.18), ("TpC", 19.07)]
