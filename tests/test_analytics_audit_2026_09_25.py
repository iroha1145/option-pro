"""Regression tests for the 2026-09-25 analytics-module audit fixes.

Covers option-pro-full-review-2026-09-25.md section 4 (macro_conditions,
strength, technical, api/breakouts, api/sectors). Each test is named after
the finding it pins down so a failure points straight back to the report.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone

import pytest

from app import failure_diagnostics


@pytest.fixture(autouse=True)
def _clear_fallback_diagnostics_dedup():
    """record_fallback_failure dedupes identical log lines for 300s; without
    resetting its module-level cache, an earlier test's call could silently
    swallow the one this file expects to observe via caplog."""

    failure_diagnostics._seen.clear()
    yield
    failure_diagnostics._seen.clear()


# ---------------------------------------------------------------------------
# M-7: macro_conditions/service.py refresh() series-write isolation
# ---------------------------------------------------------------------------


def test_m7_one_series_write_failure_does_not_lose_the_rest(tmp_path) -> None:
    """A MacroSchemaError raised while writing one fetched series must not
    abort the rest of the write loop, must be counted as a series failure
    (not silently dropped from both totals), and must degrade the run status
    rather than leave it looking like a clean success.
    """

    from app.services.macro_conditions.models import SeriesFetch
    from app.services.macro_conditions.registry import FRED_SERIES
    from app.services.macro_conditions.repository import MacroRepository, MacroSchemaError
    from app.services.macro_conditions.service import MacroConditionsService
    from app.services.macro_conditions.market_proxy import MarketProxyReader
    from tests.macro_fixtures import fixed_clock, seed_repository, synthetic_series_fetch

    class _StaticFredClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def fetch_many(self, specs, *, start, end):
            fetched = {}
            failures: dict[str, str] = {}
            for spec in specs:
                if not spec.enabled:
                    continue
                metadata, observations = synthetic_series_fetch(
                    spec.series_id, start=start, end=end
                )
                fetched[spec.series_id] = SeriesFetch(
                    metadata=metadata, observations=observations
                )
            return fetched, failures

    def _no_etf_history(_symbol, _period):
        # The proxy's own per-symbol try/except turns this into an
        # etf_history_unavailable failure; the point of this test is the FRED
        # series write loop, not ETF coverage.
        raise RuntimeError("etf history disabled in this test")

    repo = MacroRepository(tmp_path / "macro.db", clock=fixed_clock())
    start = date(2020, 1, 1)
    end = date(2026, 7, 24)
    seed_repository(repo, start=start, end=end)

    enabled_ids = [spec.series_id for spec in FRED_SERIES if spec.enabled]
    failing_series_id = enabled_ids[len(enabled_ids) // 2]
    later_series_id = enabled_ids[-1]
    assert later_series_id != failing_series_id

    write_attempts: list[str] = []
    original_record = repo.record_series_revisions

    def flaky_record(metadata, observations, **kwargs):
        write_attempts.append(metadata.series_id)
        if metadata.series_id == failing_series_id:
            raise MacroSchemaError("simulated write failure")
        return original_record(metadata, observations, **kwargs)

    repo.record_series_revisions = flaky_record  # type: ignore[method-assign]

    service = MacroConditionsService(
        repo,
        fred_factory=lambda: _StaticFredClient(),
        proxy=MarketProxyReader(history=_no_etf_history),
        clock=fixed_clock(),
    )
    result = service.refresh(trigger="test")

    # The loop kept going past the failing series instead of aborting (this is
    # the actual bug: before the fix, later_series_id would never appear here
    # at all).
    assert later_series_id in write_attempts
    assert write_attempts.index(later_series_id) > write_attempts.index(failing_series_id)

    # The failure is attributed to the right series and counted, not dropped.
    assert result["series_failed"] >= 1
    assert any(
        warning.startswith(f"series:{failing_series_id}:") for warning in result["warnings"]
    )
    # series_succeeded now counts writes, not fetches: exactly one of the
    # fetched series failed to write.
    assert result["series_succeeded"] == len(enabled_ids) - 1
    # A write failure must never look like an unqualified success.
    assert result["status"] != "succeeded"
    assert result["status"] == "degraded"


# ---------------------------------------------------------------------------
# M-8: macro_conditions/linkage.py structural_macro_score equal weighting
# ---------------------------------------------------------------------------
# (test_macro_linkage.py::test_structural_macro_excludes_credit_and_risk was
# amended in place to use four distinct structural scores, per the audit
# instruction; nothing further is added here.)


# ---------------------------------------------------------------------------
# M-9: macro_conditions/calculations.py _funding_fragmentation minimum sample
# ---------------------------------------------------------------------------


def test_m9_funding_fragmentation_requires_a_full_window() -> None:
    """Fewer than FUNDING_FRAGMENTATION_WINDOW valid dispersion points -- for
    example right after a backfill start -- must not be averaged and reported
    as though it were the full 21-day mean."""

    from app.services.macro_conditions.alignment import AsOfSeries, build_grid
    from app.services.macro_conditions.calculations import compute_factor_points
    from app.services.macro_conditions.registry import (
        FUNDING_FRAGMENTATION_WINDOW,
        SERIES_BY_ID,
    )

    grid = build_grid(date(2026, 1, 2), date(2026, 7, 24))
    assert len(grid) > FUNDING_FRAGMENTATION_WINDOW

    recent_days = grid[-5:]  # far fewer than the required 21-point window
    series_values = {
        "SOFR": 4.30,
        "OBFR": 4.36,
        "IORB": 4.40,
        "RRPONTSYAWARD": 4.25,
        "EFFR": 4.33,
        "DCPF3M": 4.85,
        "DTB3": 4.30,
    }
    series = {}
    for series_id, value in series_values.items():
        spec = SERIES_BY_ID[series_id]
        rows = [
            (day, value, "2026-07-24T00:00:00Z", "latest_revised_backfill")
            for day in recent_days
        ]
        series[series_id] = AsOfSeries(
            series_id, rows, max_stale_calendar_days=spec.max_stale_calendar_days
        )

    points = compute_factor_points(grid, series, {})
    last_point = points["funding_fragmentation_21d"][-1]

    # Before the fix this reported status="ok" with a 5-point mean.
    assert last_point.raw_value is None
    assert last_point.status == "missing"
    assert "funding_fragmentation_21d_window" in last_point.missing_inputs


def test_m9_funding_fragmentation_names_each_underlying_series() -> None:
    """When every underlying series is absent, missing_inputs must name the
    seven actual FRED series ids -- matching this factor's registered
    required_series=7 -- not one generic 'funding_spread_panel' tag that
    cannot distinguish 'all seven missing' from 'one stale leg'."""

    from app.services.macro_conditions.alignment import build_grid
    from app.services.macro_conditions.calculations import compute_factor_points
    from app.services.macro_conditions.registry import FACTORS_BY_ID

    grid = build_grid(date(2026, 1, 2), date(2026, 7, 24))
    points = compute_factor_points(grid, {}, {})
    last_point = points["funding_fragmentation_21d"][-1]

    assert last_point.raw_value is None
    spec = FACTORS_BY_ID["funding_fragmentation_21d"]
    assert len(spec.required_series) == 7
    assert set(spec.required_series) <= set(last_point.missing_inputs)


# ---------------------------------------------------------------------------
# M-低 (macro): score_factor_series now calls factor_confidence
# ---------------------------------------------------------------------------


def test_m_low_score_factor_series_populates_confidence_via_factor_confidence() -> None:
    """ScoredFactor.confidence must come from factor_confidence, matching a
    hand-computed call, rather than being hardcoded to None."""

    from app.services.macro_conditions.models import FactorPoint
    from app.services.macro_conditions.registry import FACTORS_BY_ID
    from app.services.macro_conditions.scoring import factor_confidence, score_factor_series

    factor_id = "fed_net_liquidity"
    spec = FACTORS_BY_ID[factor_id]
    required_inputs = len(spec.required_series) + len(spec.required_etfs)
    assert required_inputs == 3

    point = FactorPoint(
        factor_id=factor_id,
        snapshot_date=date(2026, 7, 24),
        raw_value=100.0,
        score_value=100.0,
        data_through=date(2026, 7, 24),
        available_at="2026-07-24T00:00:00Z",
        history_basis="latest_revised_backfill",
        missing_inputs=("WTREGEN",),
        stale_inputs=(),
    )

    scored = score_factor_series(factor_id, [point], window_years=5)
    assert len(scored) == 1
    assert scored[0].confidence == factor_confidence(point, required_inputs=required_inputs)
    assert scored[0].confidence == pytest.approx(2 / 3)


def test_m_low_alignment_and_service_share_one_as_date() -> None:
    """The two byte-identical _as_date copies were merged into one function in
    alignment.py, imported (not redefined) by service.py."""

    from app.services.macro_conditions import alignment, service

    assert service._as_date is alignment._as_date
    assert alignment._as_date("2026-07-24") == date(2026, 7, 24)
    assert alignment._as_date("not-a-date") is None
    assert alignment._as_date(date(2026, 7, 24)) == date(2026, 7, 24)


# ---------------------------------------------------------------------------
# M-10: strength/scanner.py per-ticker diagnostics
# ---------------------------------------------------------------------------


def test_m10_ticker_exception_is_counted_and_diagnosed_without_losing_others(
    tmp_path, monkeypatch, caplog,
) -> None:
    """A crash inside _scan_sync's per-ticker loop must keep skipping just
    that ticker (business behaviour unchanged) but must now name the ticker
    and the exception type instead of vanishing into a bare counter."""

    from app.api import strength
    from app.services.strength import scanner
    from tests.http_response_support import (
        anonymous_get_request,
        lock_screener_admin_production,
        response_payload,
    )
    from tests.legacy_strength_support import LegacySnapshotTask, read_legacy_snapshot
    from tests.test_screener_freshness_task_chain import _install_provider_boundary

    lock_screener_admin_production(monkeypatch)
    path = tmp_path / "strength-snapshot-v1.json"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    observed = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(strength.time, "time", lambda: observed.timestamp())

    _install_provider_boundary(monkeypatch)
    panel = scanner._download_history(["NVDA", "AAPL", "MSFT"])

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed.astimezone(tz) if tz else observed.replace(tzinfo=None)

    monkeypatch.setattr(scanner, "datetime", FixedDatetime)
    monkeypatch.setattr(scanner, "_download_history", lambda *_a, **_k: panel)

    original_feature_row = scanner._feature_row

    def flaky_feature_row(ticker, hist, spy, meta):
        if ticker == "NVDA":
            raise RuntimeError("simulated feature computation crash")
        return original_feature_row(ticker, hist, spy, meta)

    monkeypatch.setattr(scanner, "_feature_row", flaky_feature_row)

    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        result = asyncio.run(
            LegacySnapshotTask(snapshot_path=path, clock=lambda: observed.timestamp())()
        )

    assert result.status == "idle"
    payload = response_payload(
        asyncio.run(
            read_legacy_snapshot(
                anonymous_get_request(),
                **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
                ranking_algorithm="production",
            )
        )
    )
    assert payload["skipped"]["data_error"] == 1
    assert {row["ticker"] for row in payload["rows"]} == {"AAPL", "MSFT"}
    assert any(
        "stage=strength_scan_ticker" in record.getMessage()
        and "symbol=NVDA" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# M-11: api/breakouts.py per-event isolation
# ---------------------------------------------------------------------------


def _minimal_breakout_event(event_id: str, ticker: str, event_at) -> dict:
    return {
        "event_id": event_id,
        "ticker": ticker,
        "name": f"{ticker} Incorporated",
        "exchange": "NASDAQ",
        "asset_type": "common_stock",
        "sector": "Technology",
        "session": "regular",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "TRIGGERED",
        "event_at": event_at,
        "first_seen_at": event_at,
        "last_seen_at": event_at,
        "event_price": 105.0,
        "structure": {"pivot_price": 100.0},
        "features": {"current_price": 105.2},
        "scores": {"alert_priority_score": 70.0},
        "data_quality": {},
        "versions": {},
        "warnings": [],
    }


def test_m11_one_timezone_less_event_does_not_take_down_the_page(caplog) -> None:
    """A single stored event with a naive (no-tzinfo) timestamp must be
    skipped and diagnosed, not turn the whole page into a 500."""

    from app.api import breakouts as breakout_api
    from app.services.breakouts.config import BreakoutSettings

    settings = BreakoutSettings(_env_file=None)
    good_before = _minimal_breakout_event(
        "evt-1", "AAA", datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)
    )
    bad = _minimal_breakout_event(
        "evt-2", "BBB", datetime(2026, 7, 10, 14, 31)  # no tzinfo: the bug trigger
    )
    good_after = _minimal_breakout_event(
        "evt-3", "CCC", datetime(2026, 7, 10, 14, 32, tzinfo=timezone.utc)
    )

    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        built = breakout_api._public_events(
            settings,
            [good_before, bad, good_after],
            observed_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        )

    assert [event.event_id for event in built] == ["evt-1", "evt-3"]
    assert any(
        "stage=breakouts_public_event" in record.getMessage()
        and "symbol=BBB" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# M-12: api/sectors.py finite-number and sanitize coverage
# ---------------------------------------------------------------------------


def test_m12_rank_iv_rows_scrubs_non_finite_price() -> None:
    from app.api import sectors

    payload = sectors._rank_iv_rows(
        "semiconductors",
        [
            {
                "ticker": "AMD",
                "iv": 0.30,
                "price": float("inf"),
                "as_of": "2026-01-01T00:00:00+00:00",
            },
            {
                "ticker": "NVDA",
                "iv": 0.35,
                "price": float("nan"),
                "as_of": "2026-01-01T00:00:00+00:00",
            },
        ],
    )
    prices = {row["ticker"]: row["price"] for row in payload["rankings"]}
    assert prices == {"AMD": None, "NVDA": None}


def test_m12_iv_ranking_endpoint_sanitizes_non_finite_values(monkeypatch) -> None:
    from app.api import sectors
    from tests.http_response_support import anonymous_get_request

    async def fake_payload(_sector_id):
        return {
            "sector_id": "semiconductors",
            "sector_name": "半导体",
            "rankings": [
                {
                    "ticker": "AMD",
                    "price": float("inf"),
                    "atm_iv_percent": float("nan"),
                }
            ],
            "refresh": {"status": "idle"},
        }

    monkeypatch.setattr(sectors, "_request_iv_payload", fake_payload)
    result = asyncio.run(sectors.iv_ranking("semiconductors", anonymous_get_request()))
    row = result["rankings"][0]
    assert row["price"] is None
    assert row["atm_iv_percent"] is None


def test_m12_heatmap_endpoint_sanitizes_non_finite_values(monkeypatch) -> None:
    from app.api import sectors
    from tests.http_response_support import anonymous_get_request

    async def fake_payload(_sector_id):
        return {
            "sector_id": "semiconductors",
            "sector_name": "半导体",
            "rankings": [
                {
                    "ticker": "AMD",
                    "atm_iv_percent": float("inf"),
                    "sector_iv_rank": float("nan"),
                }
            ],
            "refresh": {"status": "idle"},
        }

    monkeypatch.setattr(sectors, "_request_iv_payload", fake_payload)
    result = asyncio.run(sectors.heatmap("semiconductors", anonymous_get_request()))
    row = result["data"][0]
    assert row["atm_iv_percent"] is None
    assert row["sector_iv_rank"] is None


# ---------------------------------------------------------------------------
# M-低 (technical): chart_analysis.py RVOL isolation
# ---------------------------------------------------------------------------


def test_m_low_technical_rvol_failure_does_not_lose_vwap_or_opening_range(
    monkeypatch, caplog,
) -> None:
    """A crash inside compute_time_of_day_rvol must leave the RVOL field at
    None (with a diagnostic) but must not take VWAP or the opening range
    overlays down with it."""

    from app.services.breakouts import feature_engine
    from app.services.technical import chart_analysis

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated rvol crash")

    monkeypatch.setattr(feature_engine, "compute_time_of_day_rvol", boom)

    base = int(datetime(2026, 7, 10, 13, 30, tzinfo=timezone.utc).timestamp())
    n = 10
    closes = [100.0 + index * 0.1 for index in range(n)]
    series = {
        "times": [base + index * 300 for index in range(n)],
        "dates": ["2026-07-10"] * n,
        "opens": closes,
        "highs": [value + 0.2 for value in closes],
        "lows": [value - 0.2 for value in closes],
        "closes": closes,
        "volumes": [1000] * n,
    }

    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        overlays = chart_analysis._intraday_overlays(
            series, "2026-07-10T14:15:00+00:00", "5m"
        )

    kinds = {overlay["kind"] for overlay in overlays}
    assert "vwap" in kinds
    for overlay in overlays:
        assert overlay["evidence"].get("rvolTimeOfDay") is None
    vwap_overlay = next(overlay for overlay in overlays if overlay["kind"] == "vwap")
    # VWAP itself was computed fine; only the RVOL input is missing.
    assert vwap_overlay["geometry"]["values"][-1] is not None
    assert any(
        "stage=technical_intraday_rvol" in record.getMessage()
        for record in caplog.records
    )
