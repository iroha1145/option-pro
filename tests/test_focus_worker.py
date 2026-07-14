from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.breakouts.adapters import price_data
from app.services.breakouts.adapters.price_data import YahooPriceDataAdapter
from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import MarketSession, TemporalCutoff
from app.services.catalysts.errors import CatalystRepositoryError
from app.services.catalysts.focus_config import FocusContextSettings
from app.services.catalysts.focus_publisher import _market_session
from app.services.catalysts.focus_universe import build_focus_context
from app.services.catalysts.focus_worker import (
    FOCUS_PRODUCER_WORKER_PREFIX,
    LOCK_NAME,
    FocusContextProducer,
    _admit_cross_session_intraday_symbol,
    _async_main,
    _daily_strength_cache_identity,
    _default_discovery_loader,
    _default_strength_loader,
    _intraday_session_change_pct,
    _latest_completed_trading_day,
    _merge_candidate_rows,
    fixed_refresh_times,
    health_payload,
    next_refresh_at,
)
from app.services.catalysts.repository import CatalystRepository
from app.services.market_calendar import ET
from app.services.strength.scanner import _actual_daily_data_through


ROOT = Path(__file__).resolve().parents[1]
SUMMER_NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


def _settings(path: Path, **overrides) -> FocusContextSettings:
    values = {
        "MACROLENS_CACHE_DB_PATH": path,
        # Keep unit tests isolated from CI/deployment environment overrides.
        "FOCUS_PRODUCER_ENABLED": True,
        "FOCUS_PRODUCER_CANDIDATE_LIMIT": 40,
    }
    values.update(overrides)
    return FocusContextSettings(_env_file=None, **values)


def _strength_payload(
    *,
    as_of: datetime = SUMMER_NOW,
    daily_data_through: datetime | None = None,
    universe_count: int = 2,
) -> dict:
    universe_as_of = as_of - timedelta(hours=18)
    daily_data_through = daily_data_through or datetime(
        2026, 7, 10, 20, 0, tzinfo=timezone.utc
    )
    return {
        "as_of": as_of.isoformat(),
        "universe_as_of": universe_as_of.isoformat(),
        "universe_version": "themes-test-v1",
        "universe_count": universe_count,
        "screened_count": 2,
        "_focus_rows": [
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 50_000_000,
                "data_quality": 0.9,
                "universe_member": True,
                "universe_as_of": universe_as_of.isoformat(),
                "daily_data_through": daily_data_through.isoformat(),
            },
            {
                "ticker": "MSFT",
                "avg_dollar_volume_20d": 40_000_000,
                "data_quality": 0.8,
                "universe_member": True,
                "universe_as_of": universe_as_of.isoformat(),
                "daily_data_through": daily_data_through.isoformat(),
            },
        ],
    }


def _intraday_frame(
    *,
    price: float,
    current_volume: float,
    end_minute: int = 10 * 60,
) -> pd.DataFrame:
    historical_days = [
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
        date(2026, 7, 9),
        date(2026, 7, 10),
    ]
    index: list[datetime] = []
    volumes: list[float] = []
    for day in [*historical_days, date(2026, 7, 13)]:
        for minute in range(9 * 60 + 30, end_minute, 5):
            index.append(
                datetime.combine(
                    day,
                    time(hour=minute // 60, minute=minute % 60),
                    tzinfo=ET,
                )
            )
            volumes.append(current_volume if day == date(2026, 7, 13) else 100.0)
    return pd.DataFrame(
        {
            "Open": price,
            "High": price + 1,
            "Low": price - 1,
            "Close": price,
            "Volume": volumes,
        },
        index=pd.DatetimeIndex(index),
    )


def _snapshot(
    ticker: str,
    frame: pd.DataFrame,
    *,
    quality: float = 1.0,
    data_through: datetime = SUMMER_NOW,
):
    return SimpleNamespace(
        ticker=ticker,
        frame=frame,
        source="Yahoo/yfinance",
        data_through=data_through,
        warnings=(),
        quality=quality,
    )


def _discovery(_snapshot) -> dict:
    return {
        "provider": "tradingview",
        "status": "active",
        "as_of": SUMMER_NOW,
        "warnings": [],
        "candidates": [
            {
                "ticker": "AAPL",
                "price": 100,
                "provider_volume": 1_000,
                "provider_change_pct": 5.0,
                "source": "tradingview",
            },
            {
                "ticker": "MSFT",
                "price": 200,
                "provider_volume": 500,
                "provider_change_pct": 2.0,
                "source": "tradingview",
            },
        ],
    }


def test_schedule_adds_fixed_refreshes_and_actual_early_close() -> None:
    summer = datetime(2026, 7, 13, 7, 49, tzinfo=ET)
    assert next_refresh_at(summer).astimezone(ET).strftime("%H:%M") == "07:50"
    assert next_refresh_at(summer + timedelta(minutes=1)).astimezone(ET).strftime(
        "%H:%M"
    ) == "08:00"
    assert next_refresh_at(summer.replace(hour=11, minute=49)).astimezone(ET).strftime(
        "%H:%M"
    ) == "11:50"
    assert next_refresh_at(summer.replace(hour=15, minute=49)).astimezone(ET).strftime(
        "%H:%M"
    ) == "15:50"

    early_day = date(2026, 11, 27)
    assert [value.strftime("%H:%M") for value in fixed_refresh_times(early_day)] == [
        "07:50",
        "11:50",
        "12:50",
    ]
    early = datetime(2026, 11, 27, 12, 49, tzinfo=ET)
    assert next_refresh_at(early).astimezone(ET).strftime("%H:%M") == "12:50"


def test_completed_daily_cache_key_waits_for_regular_and_early_close_settlement() -> None:
    regular_close = datetime(2026, 7, 13, 16, 0, tzinfo=ET)
    assert _latest_completed_trading_day(regular_close) == date(2026, 7, 10)
    assert _latest_completed_trading_day(
        regular_close + timedelta(minutes=29, seconds=59)
    ) == date(2026, 7, 10)
    assert _latest_completed_trading_day(
        regular_close + timedelta(minutes=30)
    ) == date(2026, 7, 13)

    early_close = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    assert _latest_completed_trading_day(early_close) == date(2026, 11, 25)
    assert _latest_completed_trading_day(
        early_close + timedelta(minutes=30)
    ) == date(2026, 11, 27)


def test_daily_data_through_uses_the_actual_last_bar_not_theoretical_cutoff() -> None:
    frame = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.DatetimeIndex(["2026-07-09", "2026-07-10"]),
    )

    assert _actual_daily_data_through(frame) == datetime(
        2026, 7, 10, 20, 0, tzinfo=timezone.utc
    )


def test_intraday_adapter_accepts_60_candidates_but_rejects_61(monkeypatch) -> None:
    monkeypatch.setattr(price_data.yf, "download", lambda **_kwargs: pd.DataFrame())
    cutoff = TemporalCutoff(
        event_at=SUMMER_NOW,
        session=MarketSession.REGULAR,
        include_current_bar=False,
    )
    adapter = YahooPriceDataAdapter()
    tickers = [f"T{index}" for index in range(60)]
    assert asyncio.run(adapter.intraday(tickers, cutoff=cutoff)) == {}
    with pytest.raises(ValueError, match="exceeds 60 symbols"):
        asyncio.run(adapter.intraday([*tickers, "T60"], cutoff=cutoff))


def test_regular_discovery_unions_volume_leaders_and_movers(monkeypatch) -> None:
    calls = []
    closed = False

    class FakeResult:
        def __init__(self, ticker: str, profile: str) -> None:
            self.status = "active"
            self.cache_key = f"cache-{profile}"
            self.warnings = []
            self._ticker = ticker

        def model_dump(self, *, mode: str) -> dict:
            assert mode == "python"
            return {
                "provider": "tradingview",
                "status": "active",
                "as_of": SUMMER_NOW,
                "warnings": [],
                "candidates": [
                    {
                        "ticker": self._ticker,
                        "price": 100.0,
                        "provider_volume": 1_000.0,
                        "provider_change_pct": 2.0,
                        "source": "tradingview",
                    }
                ],
            }

    class FakeProvider:
        async def scan(self, *, session, as_of, profile):
            calls.append(profile)
            ticker = (
                "LEAD"
                if profile.value == "regular_dollar_volume_leaders"
                else "MOVE"
            )
            return FakeResult(ticker, profile.value)

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        "app.services.catalysts.focus_worker.TradingViewDiscoveryProvider",
        FakeProvider,
    )
    snapshot = MarketClock(now=lambda: SUMMER_NOW).snapshot()

    payload = asyncio.run(_default_discovery_loader(snapshot))

    assert {profile.value for profile in calls} == {
        "regular_dollar_volume_leaders",
        "regular_movers",
    }
    assert {item["ticker"] for item in payload["candidates"]} == {
        "LEAD",
        "MOVE",
    }
    by_ticker = {item["ticker"]: item for item in payload["candidates"]}
    assert by_ticker["LEAD"]["_focus_volume_leader"] is True
    assert by_ticker["MOVE"]["_focus_regular_mover"] is True
    assert payload["_focus_volume_leader_tickers"] == ["LEAD"]
    assert payload["_focus_regular_mover_tickers"] == ["MOVE"]
    assert closed is True


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        (datetime(2026, 7, 13, 13, 29, tzinfo=timezone.utc), "premarket"),
        (datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc), "regular"),
        (datetime(2026, 7, 13, 19, 59, tzinfo=timezone.utc), "regular"),
        (datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc), "after_hours"),
        (datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc), "closed"),
        (datetime(2026, 1, 13, 20, 59, tzinfo=timezone.utc), "regular"),
        (datetime(2026, 1, 13, 14, 30, tzinfo=timezone.utc), "regular"),
        (datetime(2026, 1, 13, 21, 0, tzinfo=timezone.utc), "after_hours"),
        (datetime(2026, 1, 14, 1, 0, tzinfo=timezone.utc), "closed"),
        (datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc), "regular"),
        (datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc), "after_hours"),
    ],
)
def test_focus_market_session_uses_half_open_boundaries(
    observed: datetime,
    expected: str,
) -> None:
    assert _market_session(observed) == expected


def test_producer_publishes_completed_bar_dollar_volume_and_rvol(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "focus.db")
    repository.initialize(now=SUMMER_NOW)
    requested: list[tuple[list[str], bool]] = []

    async def strength_loader() -> dict:
        return _strength_payload()

    async def discovery_loader(snapshot) -> dict:
        assert snapshot.as_of == SUMMER_NOW
        return _discovery(snapshot)

    async def intraday_loader(tickers, cutoff) -> dict:
        requested.append((list(tickers), cutoff.include_current_bar))
        assert len(tickers) <= 40
        return {
            "AAPL": _snapshot(
                "AAPL", _intraday_frame(price=100, current_volume=200)
            ),
            "MSFT": _snapshot(
                "MSFT", _intraday_frame(price=200, current_volume=50)
            ),
        }

    producer = FocusContextProducer(
        settings=_settings(tmp_path / "focus.db"),
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=strength_loader,
        discovery_loader=discovery_loader,
        intraday_loader=intraday_loader,
        breakout_loader=lambda: [
            {"ticker": "AAPL", "lifecycle_state": "CONFIRMED"}
        ],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}test",
    )
    result = asyncio.run(producer.run_once())

    assert result["status"] == "completed"
    assert result["dollar_volume_basis"] == {"intraday_completed_bars": 2}
    assert requested == [(["AAPL", "MSFT"], False)]
    current = repository.current_focus_context()
    assert current is not None
    symbols = {symbol.ticker: symbol for symbol in current.symbols}
    assert symbols["AAPL"].dollar_volume_rank == 1
    assert symbols["AAPL"].dollar_volume == pytest.approx(120_000.0)
    assert symbols["AAPL"].dollar_volume_basis == "intraday_completed_bars"
    assert symbols["AAPL"].data_through == SUMMER_NOW
    assert symbols["AAPL"].source_status == "active"
    assert symbols["AAPL"].data_source == "Yahoo/yfinance"
    assert symbols["AAPL"].rvol_time_of_day == pytest.approx(2.0)
    assert symbols["AAPL"].session_change_pct == pytest.approx(0.0)
    assert symbols["AAPL"].breakout_state == "CONFIRMED"
    assert symbols["MSFT"].rvol_time_of_day == pytest.approx(0.5)
    assert current.data_through == SUMMER_NOW
    serialized = json.dumps(current.model_dump(mode="json"))
    for forbidden in (
        "intrinsic_strength_score",
        "ranking_score",
        "breakout_quality_score",
        "option_score",
    ):
        assert forbidden not in serialized

    focus_health = repository.focus_producer_health(
        heartbeat_ttl_seconds=120,
        now=SUMMER_NOW,
    )
    assert focus_health["details"]["symbol_sources"][0] == {
        "ticker": "AAPL",
        "dollar_volume_basis": "intraday_completed_bars",
        "dollar_volume": 120_000.0,
        "data_through": SUMMER_NOW.isoformat(),
        "source_status": "active",
        "data_source": "Yahoo/yfinance",
    }
    assert focus_health["details"]["candidate_semantics"] == (
        "candidate_dollar_volume_top20"
    )
    assert focus_health["details"]["data_through_coverage"] == 1.0
    assert focus_health["details"]["selected_data_through_min"] == (
        SUMMER_NOW.isoformat()
    )
    assert focus_health["details"]["selected_data_through_max"] == (
        SUMMER_NOW.isoformat()
    )
    assert focus_health["details"]["active_symbol_count"] == 2
    assert focus_health["details"]["stale_symbol_count"] == 0
    assert focus_health["details"]["fallback_symbol_count"] == 0
    assert focus_health["details"]["unavailable_symbol_count"] == 0
    assert focus_health["details"]["intraday_exact_count"] == 2
    assert focus_health["details"]["intraday_exact_ratio"] == 1.0
    assert focus_health["details"]["rvol_available_count"] == 2
    assert focus_health["details"]["rvol_available_ratio"] == 1.0
    assert focus_health["details"]["market_volume_rank_scope"] == "candidate"
    assert focus_health["snapshot_fresh"] is True


def test_daily_strength_scan_is_reused_for_the_same_trading_day_and_version(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    calls = 0

    async def strength_loader() -> dict:
        nonlocal calls
        calls += 1
        return _strength_payload()

    async def no_intraday(_tickers, _cutoff) -> dict:
        return {}

    first = FocusContextProducer(
        settings=_settings(path),
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=strength_loader,
        discovery_loader=lambda snapshot: asyncio.sleep(
            0,
            result={
                "provider": "fixture",
                "status": "unavailable",
                "as_of": snapshot.as_of,
                "warnings": [],
                "candidates": [],
            },
        ),
        intraday_loader=no_intraday,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}cache-first",
    )
    first_result = asyncio.run(first.run_once())

    async def unexpected_strength_loader() -> dict:
        raise AssertionError("same-day persistent cache was not reused")

    later = SUMMER_NOW + timedelta(minutes=30)
    second = FocusContextProducer(
        settings=_settings(path),
        repository=repository,
        clock=MarketClock(now=lambda: later),
        strength_loader=unexpected_strength_loader,
        discovery_loader=lambda snapshot: asyncio.sleep(
            0,
            result={
                "provider": "fixture",
                "status": "unavailable",
                "as_of": snapshot.as_of,
                "warnings": [],
                "candidates": [],
            },
        ),
        intraday_loader=no_intraday,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}cache-second",
    )
    second_result = asyncio.run(second.run_once())

    assert calls == 1
    assert first_result["daily_strength_cache"]["source"] == "fresh_scan"
    assert second_result["daily_strength_cache"]["source"] == "persistent_cache"


def test_close_settlement_lag_never_becomes_an_all_day_active_cache(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    regular_close = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
    monday_close = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)

    def load_at(observed: datetime, loader, suffix: str):
        owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}{suffix}"
        producer = FocusContextProducer(
            settings=_settings(path),
            repository=repository,
            clock=MarketClock(now=lambda: observed),
            strength_loader=loader,
            breakout_loader=lambda: [],
            owner_id=owner,
        )
        token = repository.acquire_worker_lock(
            LOCK_NAME,
            owner,
            lease_seconds=producer.settings.producer_lease_seconds,
            now=observed,
        )
        assert token is not None
        try:
            return asyncio.run(producer._load_strength_payload(observed, token))
        finally:
            repository.release_worker_lock(LOCK_NAME, owner, token)

    _, at_close = load_at(
        regular_close,
        lambda: asyncio.sleep(
            0,
            result=_strength_payload(
                as_of=regular_close,
                daily_data_through=monday_close,
            ),
        ),
        "settlement-close",
    )
    assert at_close["trading_day"] == "2026-07-10"
    assert at_close["status"] == "degraded"
    assert at_close["coverage"] == 0.0
    assert at_close["expires_at"] is not None

    settled_at = regular_close + timedelta(minutes=30)
    _, settled = load_at(
        settled_at,
        lambda: asyncio.sleep(
            0,
            result=_strength_payload(
                as_of=settled_at,
                daily_data_through=monday_close,
            ),
        ),
        "settlement-ready",
    )
    assert settled["source"] == "fresh_scan"
    assert settled["trading_day"] == "2026-07-13"
    assert settled["status"] == "active"
    assert settled["coverage"] == 1.0
    assert settled["expires_at"] is None

    next_morning = datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc)

    async def unexpected_loader() -> dict:
        raise AssertionError("settled Monday cache should be reused Tuesday morning")

    _, reused = load_at(
        next_morning,
        unexpected_loader,
        "settlement-next-morning",
    )
    assert reused["source"] == "persistent_cache"
    assert reused["trading_day"] == "2026-07-13"


def test_low_expected_universe_coverage_is_only_short_lived(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    observed = datetime(2026, 7, 13, 20, 30, tzinfo=timezone.utc)
    repository.initialize(now=observed)
    owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}low-coverage"
    producer = FocusContextProducer(
        settings=_settings(path, FOCUS_DAILY_STRENGTH_MIN_COVERAGE=0.9),
        repository=repository,
        clock=MarketClock(now=lambda: observed),
        strength_loader=lambda: asyncio.sleep(
            0,
            result=_strength_payload(
                as_of=observed,
                daily_data_through=datetime(
                    2026, 7, 13, 20, 0, tzinfo=timezone.utc
                ),
                universe_count=100,
            ),
        ),
        breakout_loader=lambda: [],
        owner_id=owner,
    )
    token = repository.acquire_worker_lock(
        LOCK_NAME,
        owner,
        lease_seconds=producer.settings.producer_lease_seconds,
        now=observed,
    )
    assert token is not None
    try:
        payload, details = asyncio.run(
            producer._load_strength_payload(observed, token)
        )
    finally:
        repository.release_worker_lock(LOCK_NAME, owner, token)

    assert payload["expected_symbol_count"] == 100
    assert payload["available_symbol_count"] == 2
    assert payload["completed_session_symbol_count"] == 2
    assert payload["coverage"] == 0.02
    assert details["status"] == "degraded"
    assert details["expires_at"] is not None


def test_daily_strength_cache_rebuilds_for_algorithm_and_universe_identity_changes(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    observed = SUMMER_NOW
    repository.initialize(now=observed)
    calls = 0
    identity = {
        "cache_version": "focus-cache-algorithm-v1",
        "strength_feature_version": "feature-v1",
        "strength_score_version": "score-v1",
        "normalization_version": "normalization-v1",
        "range_persistence_version": "range-v1",
    }
    monkeypatch.setattr(
        "app.services.catalysts.focus_worker._daily_strength_cache_identity",
        lambda: dict(identity),
    )

    async def strength_loader() -> dict:
        nonlocal calls
        calls += 1
        return _strength_payload()

    def load(suffix: str, loader=strength_loader):
        owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}{suffix}"
        producer = FocusContextProducer(
            settings=_settings(path),
            repository=repository,
            clock=MarketClock(now=lambda: observed),
            strength_loader=loader,
            breakout_loader=lambda: [],
            owner_id=owner,
        )
        token = repository.acquire_worker_lock(
            LOCK_NAME,
            owner,
            lease_seconds=producer.settings.producer_lease_seconds,
            now=observed,
        )
        assert token is not None
        try:
            return asyncio.run(producer._load_strength_payload(observed, token))[1]
        finally:
            repository.release_worker_lock(LOCK_NAME, owner, token)

    assert load("identity-v1")["source"] == "fresh_scan"
    identity.update(
        cache_version="focus-cache-algorithm-v2",
        strength_score_version="score-v2",
    )
    assert load("identity-v2")["source"] == "fresh_scan"
    identity["cache_version"] = "focus-cache-universe-v3"
    assert load("identity-v3")["source"] == "fresh_scan"

    async def unexpected_loader() -> dict:
        raise AssertionError("unchanged identity should use persistent cache")

    assert load("identity-v3-reuse", unexpected_loader)["source"] == (
        "persistent_cache"
    )
    assert calls == 3


def test_daily_strength_cache_identity_covers_range_scoring_configuration(
    monkeypatch,
) -> None:
    base = {
        "range_persistence_mode": "enabled",
        "range_persistence_version": "range-v1",
        "range_persistence_length": 35,
        "range_persistence_fast_length": 3,
        "range_persistence_slope_days": 5,
        "range_persistence_ratio_window": 10,
        "range_persistence_ratio_threshold": 60.0,
        "range_persistence_min_history_multiplier": 5,
        "range_persistence_trend_family_weight": 0.15,
        "range_persistence_final_weight_cap": 0.04,
    }
    current = SimpleNamespace(**base)
    monkeypatch.setattr(
        "app.services.breakouts.config.get_breakout_settings",
        lambda: current,
    )
    monkeypatch.setattr(
        "app.services.strength.scanner._theme_universe",
        lambda: (["AAPL"], {"AAPL": {"sector_id": "technology"}}),
    )
    monkeypatch.setattr(
        "app.services.strength.scanner._canonical_universe_version",
        lambda _tickers, _metadata: "themes-test-v1",
    )
    baseline = _daily_strength_cache_identity()["cache_version"]

    changed_values = {
        "range_persistence_min_history_multiplier": 6,
        "range_persistence_trend_family_weight": 0.1,
        "range_persistence_final_weight_cap": 0.03,
    }
    for field, changed_value in changed_values.items():
        current = SimpleNamespace(**{**base, field: changed_value})
        assert _daily_strength_cache_identity()["cache_version"] != baseline


def test_daily_strength_cache_rechecks_current_coverage_threshold(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    observed = SUMMER_NOW
    repository.initialize(now=observed)
    calls = 0
    completed_day = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    rows = [
        {
            "ticker": f"S{index:03d}",
            "avg_dollar_volume_20d": 100_000_000 - index,
            "data_quality": 1.0,
            "universe_member": True,
            "universe_as_of": (observed - timedelta(hours=18)).isoformat(),
            "daily_data_through": completed_day.isoformat(),
        }
        for index in range(91)
    ]
    source_payload = {
        **_strength_payload(
            as_of=observed,
            daily_data_through=completed_day,
            universe_count=100,
        ),
        "_focus_rows": rows,
    }

    async def strength_loader() -> dict:
        nonlocal calls
        calls += 1
        return source_payload

    def load(minimum_coverage: float, suffix: str):
        owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}{suffix}"
        producer = FocusContextProducer(
            settings=_settings(
                path,
                FOCUS_DAILY_STRENGTH_MIN_COVERAGE=minimum_coverage,
            ),
            repository=repository,
            clock=MarketClock(now=lambda: observed),
            strength_loader=strength_loader,
            breakout_loader=lambda: [],
            owner_id=owner,
        )
        token = repository.acquire_worker_lock(
            LOCK_NAME,
            owner,
            lease_seconds=producer.settings.producer_lease_seconds,
            now=observed,
        )
        assert token is not None
        try:
            return asyncio.run(
                producer._load_strength_payload(observed, token)
            )
        finally:
            repository.release_worker_lock(LOCK_NAME, owner, token)

    first_payload, first = load(0.9, "coverage-threshold-90")
    second_payload, second = load(0.95, "coverage-threshold-95")

    assert calls == 2
    assert first_payload["coverage"] == 0.91
    assert first["source"] == "fresh_scan"
    assert first["status"] == "active"
    assert first["expires_at"] is None
    assert second_payload["coverage"] == 0.91
    assert second["source"] == "fresh_scan"
    assert second["status"] == "degraded"
    assert second["minimum_coverage"] == 0.95
    assert second["expires_at"] is not None
    cache_identity = _daily_strength_cache_identity()
    stored = repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version=cache_identity["cache_version"],
        strength_feature_version=cache_identity["strength_feature_version"],
        strength_score_version=cache_identity["strength_score_version"],
        normalization_version=cache_identity["normalization_version"],
        range_persistence_version=cache_identity["range_persistence_version"],
        now=observed,
    )
    assert stored is not None
    assert stored["status"] == "degraded"
    assert stored["coverage"] == 0.91
    assert stored["expires_at"] is not None


def test_top_level_data_through_uses_only_final_symbols_and_never_as_of(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    selected_data_through = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    omitted_data_through = datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)
    payload = {
        "as_of": SUMMER_NOW.isoformat(),
        "universe_as_of": SUMMER_NOW.isoformat(),
        "universe_version": "final-symbols-v1",
        "_focus_rows": [
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 200_000_000,
                "universe_member": True,
                "daily_data_through": selected_data_through.isoformat(),
            },
            {
                "ticker": "MSFT",
                "avg_dollar_volume_20d": 100_000_000,
                "universe_member": True,
                "daily_data_through": omitted_data_through.isoformat(),
            },
        ],
    }
    settings = _settings(
        path,
        FOCUS_MAX_SYMBOLS=1,
        FOCUS_STRENGTH_COUNT=0,
        FOCUS_DOLLAR_VOLUME_COUNT=1,
        FOCUS_ENTER_DOLLAR_VOLUME_RANK=1,
        FOCUS_RETAIN_DOLLAR_VOLUME_RANK=1,
    )
    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=payload),
        discovery_loader=lambda snapshot: asyncio.sleep(
            0,
            result={
                "provider": "fixture",
                "status": "unavailable",
                "as_of": snapshot.as_of,
                "warnings": [],
                "candidates": [],
            },
        ),
        intraday_loader=lambda _tickers, _cutoff: asyncio.sleep(0, result={}),
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}data-through",
    )

    result = asyncio.run(producer.run_once())
    current = repository.current_focus_context()

    assert result["status"] == "completed"
    assert current is not None
    assert [symbol.ticker for symbol in current.symbols] == ["AAPL"]
    assert current.data_through == selected_data_through
    assert current.data_through != omitted_data_through
    assert result["data_through_symbol_count"] == 1
    assert result["data_through_missing_count"] == 0
    assert result["selected_data_through_min"] == selected_data_through.isoformat()
    assert result["selected_data_through_max"] == selected_data_through.isoformat()
    assert result["active_symbol_count"] == 0
    assert result["fallback_symbol_count"] == 1
    assert result["stale_symbol_count"] == 0
    assert result["unavailable_symbol_count"] == 0
    assert result["intraday_exact_count"] == 0
    assert result["intraday_exact_ratio"] == 0.0
    assert result["rvol_available_count"] == 0
    assert result["rvol_available_ratio"] == 0.0


def test_first_snapshot_includes_dollar_volume_top_twenty(tmp_path) -> None:
    rows = [
        {
            "ticker": f"T{index:02d}",
            "cumulative_dollar_volume": 100_000_000 - index,
            "_dollar_volume_basis": "intraday_completed_bars",
            "_source_status": "active",
            "_data_through": SUMMER_NOW,
            "universe_member": True,
        }
        for index in range(1, 26)
    ]
    draft = build_focus_context(
        settings=_settings(tmp_path / "focus.db", FOCUS_STRENGTH_COUNT=0),
        strength_rows=rows,
        canonical_symbols=[row["ticker"] for row in rows],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="top20-v1",
    )
    assert [item.ticker for item in draft.symbols] == [
        f"T{index:02d}" for index in range(1, 21)
    ]
    assert all(
        "market_dollar_volume_top20" in item.universe_reasons
        for item in draft.symbols
    )


def test_candidate_limited_volume_reason_never_claims_market_wide_scope(tmp_path) -> None:
    draft = build_focus_context(
        settings=_settings(tmp_path / "focus.db", FOCUS_STRENGTH_COUNT=0),
        strength_rows=[
            {
                "ticker": "AAPL",
                "cumulative_dollar_volume": 100_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="candidate-v1",
        dollar_volume_scope="candidate",
    )

    assert draft.symbols[0].universe_reasons == [
        "candidate_dollar_volume_top20"
    ]
    assert "dollar_volume_top20" not in draft.symbols[0].universe_reasons


def test_extended_hours_change_uses_regular_close_baseline() -> None:
    index = pd.DatetimeIndex(
        [
            datetime(2026, 7, 10, 15, 55, tzinfo=ET),
            datetime(2026, 7, 10, 16, 5, tzinfo=ET),
            datetime(2026, 7, 13, 8, 0, tzinfo=ET),
            datetime(2026, 7, 13, 15, 55, tzinfo=ET),
            datetime(2026, 7, 13, 16, 5, tzinfo=ET),
        ]
    )
    frame = pd.DataFrame(
        {"Close": [100.0, 120.0, 110.0, 110.0, 121.0]},
        index=index,
    )

    premarket = _intraday_session_change_pct(
        frame.iloc[:3],
        as_of=datetime(2026, 7, 13, 8, 5, tzinfo=ET),
        session=MarketSession.PREMARKET,
    )
    after_hours = _intraday_session_change_pct(
        frame,
        as_of=datetime(2026, 7, 13, 16, 10, tzinfo=ET),
        session=MarketSession.POSTMARKET,
    )

    assert premarket == pytest.approx(10.0)
    assert after_hours == pytest.approx(10.0)


def test_regular_ranking_never_lets_adv20_outrank_live_intraday_dollars(tmp_path) -> None:
    draft = build_focus_context(
        settings=_settings(tmp_path / "focus.db", FOCUS_STRENGTH_COUNT=0),
        strength_rows=[
            {
                "ticker": "LIVE",
                "cumulative_dollar_volume": 100_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "universe_member": True,
            },
            {
                "ticker": "FALL",
                "avg_dollar_volume_20d": 10_000_000_000,
                "_dollar_volume_basis": "adv20_completed_sessions",
                "universe_member": True,
            },
        ],
        canonical_symbols=["LIVE", "FALL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="mixed-basis-v1",
    )
    by_ticker = {item.ticker: item for item in draft.symbols}
    assert by_ticker["LIVE"].dollar_volume_rank == 1
    assert by_ticker["FALL"].dollar_volume_rank == 2
    assert by_ticker["LIVE"].dollar_volume == 100_000_000
    assert by_ticker["FALL"].dollar_volume == 10_000_000_000


def test_coarse_intraday_candidates_are_not_displaced_by_forced_symbols(tmp_path) -> None:
    discovery = [
        {"ticker": f"D{index:02d}", "_coarse_dollar_volume": 1_000 - index}
        for index in range(40)
    ]
    forced = [f"P{index:02d}" for index in range(40)]
    rows, warnings, enrichment = _merge_candidate_rows(
        strength_rows=[],
        discovery_rows=discovery,
        breakout_rows=[],
        previous=[],
        settings=_settings(
            tmp_path / "focus.db",
            FOCUS_PRIORITY_WATCHLIST=",".join(forced),
        ),
    )
    assert enrichment == [f"D{index:02d}" for index in range(40)]
    assert {row["ticker"] for row in rows}.issuperset(forced)
    assert "focus_forced_symbols_using_fallback" in warnings


def test_forced_cross_session_candidate_keeps_market_rank_scope(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    old_as_of = SUMMER_NOW - timedelta(days=1)
    old_data_through = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    settings = _settings(
        path,
        FOCUS_MAX_SYMBOLS=1,
        FOCUS_STRENGTH_COUNT=0,
        FOCUS_PRIORITY_WATCHLIST="OLD",
    )
    repository.initialize(now=old_as_of)
    initial = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "OLD",
                "cumulative_dollar_volume": 1_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "_data_through": old_data_through,
                "_source_status": "active",
                "_data_source": "Yahoo/yfinance",
            }
        ],
        as_of=old_as_of,
        data_through=old_data_through,
        market_session="closed",
        universe_version="market-scope-old-v1",
    )
    repository.publish_focus_context(initial, now=old_as_of)
    leaders = [f"L{index:02d}" for index in range(1, 41)]

    async def discovery_loader(_snapshot) -> dict:
        return {
            "provider": "tradingview",
            "status": "active",
            "as_of": SUMMER_NOW,
            "warnings": [],
            "candidates": [
                {
                    "ticker": ticker,
                    "price": 100.0,
                    "provider_volume": float(100_000 - index),
                    "provider_change_pct": None,
                    "source": "tradingview",
                    "_focus_volume_leader": True,
                }
                for index, ticker in enumerate(leaders, start=1)
            ],
            "_focus_discovery_profile": (
                "regular_dollar_volume_leaders+regular_movers"
            ),
            "_focus_dollar_volume_leaders_supported": True,
            "_focus_volume_leader_status": "active",
            "_focus_regular_mover_status": "active",
            "_focus_volume_leader_tickers": leaders,
            "_focus_regular_mover_tickers": [],
        }

    async def intraday_loader(tickers, _cutoff) -> dict:
        return {
            ticker: _snapshot(
                ticker,
                _intraday_frame(
                    price=100.0,
                    current_volume=float(1_000 - index),
                ),
            )
            for index, ticker in enumerate(tickers, start=1)
        }

    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=discovery_loader,
        intraday_loader=intraday_loader,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}market-scope-recovery",
    )

    result = asyncio.run(producer.run_once())

    assert result["status"] == "completed"
    assert result["market_volume_rank_scope"] == "market"
    assert result["cross_session_fresh_admitted_count"] == 1
    current = repository.current_focus_context()
    assert current is not None
    assert len(current.symbols) == 1
    forced = current.symbols[0]
    assert forced.ticker in leaders
    assert "market_dollar_volume_top20" in forced.universe_reasons
    assert all(
        not reason.startswith("candidate_dollar_volume_")
        for reason in forced.universe_reasons
    )


def test_twenty_first_volume_leader_failure_downgrades_market_scope_and_keeps_mover(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    leaders = [f"L{index:02d}" for index in range(1, 41)]
    candidates = [
        {
            "ticker": ticker,
            "price": 100.0,
            "provider_volume": float(100_000 - index),
            "provider_change_pct": None,
            "source": "tradingview",
            "_focus_volume_leader": True,
        }
        for index, ticker in enumerate(leaders, start=1)
    ]
    candidates.append(
        {
            "ticker": "MOVE",
            "price": 50.0,
            "provider_volume": 1_000.0,
            "provider_change_pct": 8.0,
            "source": "tradingview",
            "_focus_regular_mover": True,
        }
    )

    async def discovery_loader(_snapshot) -> dict:
        return {
            "provider": "tradingview",
            "status": "active",
            "as_of": SUMMER_NOW,
            "warnings": [],
            "candidates": candidates,
            "_focus_discovery_profile": (
                "regular_dollar_volume_leaders+regular_movers"
            ),
            "_focus_dollar_volume_leaders_supported": True,
            "_focus_volume_leader_status": "active",
            "_focus_regular_mover_status": "active",
            "_focus_volume_leader_tickers": leaders,
            "_focus_regular_mover_tickers": ["MOVE"],
        }

    async def intraday_loader(tickers, _cutoff) -> dict:
        assert tickers == leaders
        return {
            ticker: _snapshot(
                ticker,
                _intraday_frame(
                    price=100.0,
                    current_volume=float(1_000 - index),
                ),
            )
            for index, ticker in enumerate(leaders, start=1)
            if ticker != "L21"
        }

    producer = FocusContextProducer(
        settings=_settings(path, FOCUS_STRENGTH_COUNT=0),
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=discovery_loader,
        intraday_loader=intraday_loader,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}leader-coverage",
    )

    result = asyncio.run(producer.run_once())
    current = repository.current_focus_context()

    assert result["status"] == "completed"
    assert result["market_volume_rank_scope"] == "candidate"
    assert result["required_market_leader_count"] == 40
    assert result["expected_market_leader_window_count"] == 40
    assert result["required_market_leader_exact_count"] == 39
    assert "focus_market_leader_exact_coverage_incomplete" in result["warnings"]
    assert current is not None
    by_ticker = {item.ticker: item for item in current.symbols}
    assert "MOVE" in by_ticker
    assert "regular_mover" in by_ticker["MOVE"].universe_reasons
    assert all(
        "market_dollar_volume_top20" not in item.universe_reasons
        for item in current.symbols
    )


def test_missing_intraday_uses_named_adv20_fallback_with_lower_quality(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "focus.db")
    repository.initialize(now=SUMMER_NOW)

    async def no_intraday(_tickers, _cutoff) -> dict:
        return {}

    producer = FocusContextProducer(
        settings=_settings(tmp_path / "focus.db"),
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=lambda snapshot: asyncio.sleep(0, result=_discovery(snapshot)),
        intraday_loader=no_intraday,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}fallback",
    )
    result = asyncio.run(producer.run_once())

    assert result["status"] == "completed"
    assert result["cross_session_transition"] is False
    assert result["cross_session_retained_count"] == 0
    assert result["cross_session_excluded_count"] == 0
    assert result["dollar_volume_basis"] == {"adv20_completed_sessions": 2}
    assert "focus_intraday_unavailable_adv20_fallback" in result["warnings"]
    current = repository.current_focus_context()
    assert current is not None
    symbols = {symbol.ticker: symbol for symbol in current.symbols}
    assert symbols["AAPL"].rvol_time_of_day is None
    assert symbols["AAPL"].data_quality == pytest.approx(0.6)
    assert symbols["AAPL"].dollar_volume == pytest.approx(50_000_000.0)
    assert symbols["AAPL"].dollar_volume_basis == "adv20_completed_sessions"
    assert symbols["AAPL"].source_status == "fallback"
    assert symbols["AAPL"].data_source == "canonical_strength_daily"
    assert symbols["AAPL"].data_through == datetime(
        2026, 7, 10, 20, 0, tzinfo=timezone.utc
    )
    details = repository.focus_producer_health(
        heartbeat_ttl_seconds=120,
        now=SUMMER_NOW,
    )["details"]
    assert details["symbol_sources"][0]["dollar_volume_basis"] == (
        "adv20_completed_sessions"
    )
    assert details["symbol_sources"][0]["source_status"] == "fallback"
    assert details["symbol_sources"][0]["data_source"] == "canonical_strength_daily"


def test_cross_session_without_fresh_intraday_does_not_refresh_snapshot_age(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    settings = _settings(path)
    old_as_of = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
    old_data_through = datetime(2026, 7, 13, 22, 20, tzinfo=timezone.utc)
    premarket_now = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    repository.initialize(now=old_as_of)
    initial = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "AAPL",
                "cumulative_dollar_volume": 1_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "_data_through": old_data_through,
                "_source_status": "active",
                "_data_source": "Yahoo/yfinance",
                "data_quality": 1.0,
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=old_as_of,
        data_through=old_data_through,
        market_session="closed",
        universe_version="themes-old-v1",
    )
    repository.publish_focus_context(initial, now=old_as_of)
    with repository.open_read_connection() as connection:
        created_before = connection.execute(
            "SELECT created_at FROM focus_context_snapshots WHERE revision=1"
        ).fetchone()[0]

    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: premarket_now),
        strength_loader=lambda: asyncio.sleep(
            0,
            result=_strength_payload(
                as_of=premarket_now,
                daily_data_through=datetime(
                    2026, 7, 13, 20, 0, tzinfo=timezone.utc
                ),
            ),
        ),
        discovery_loader=lambda _snapshot: asyncio.sleep(
            0,
            result={
                "provider": "tradingview",
                "status": "active",
                "as_of": premarket_now,
                "warnings": [],
                "candidates": [],
            },
        ),
        intraday_loader=lambda _tickers, _cutoff: asyncio.sleep(0, result={}),
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}cross-session-empty",
    )

    result = asyncio.run(producer.run_once())

    assert result["status"] == "degraded"
    assert result["error_code"] == "focus_recovery_requires_intraday"
    assert result["stale_revision"] is None
    current = repository.current_focus_context()
    assert current is not None
    assert current.revision == 1
    assert current.as_of == old_as_of
    assert current.data_through == old_data_through
    with repository.open_read_connection() as connection:
        rows = connection.execute(
            "SELECT revision,created_at FROM focus_context_snapshots ORDER BY revision"
        ).fetchall()
    assert [(row["revision"], row["created_at"]) for row in rows] == [
        (1, created_before)
    ]
    health = health_payload(settings, repository=repository, now=premarket_now)
    assert health["healthy"] is False
    assert health["database"]["snapshot_fresh"] is False


def test_forced_cross_session_candidate_keeps_prior_trusted_validation(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    settings = _settings(path, FOCUS_MAX_SYMBOLS=1)
    old_as_of = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
    old_data_through = datetime(2026, 7, 13, 22, 20, tzinfo=timezone.utc)
    later = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    repository.initialize(now=old_as_of)
    previous = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "EXT",
                "cumulative_dollar_volume": 1_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "_data_through": old_data_through,
                "_source_status": "active",
                "_data_source": "Yahoo/yfinance",
                "validation_status": "valid_external",
            }
        ],
        as_of=old_as_of,
        data_through=old_data_through,
        market_session="closed",
        universe_version="trusted-external-v1",
    )
    current = repository.publish_focus_context(previous, now=old_as_of)
    assert current.symbols[0].validation_status == "valid_external"
    candidate = current.symbols[0].model_copy(
        update={
            "validation_status": "unverified",
            "data_through": later,
            "data_status": "active",
            "source_status": "active",
        }
    )
    bounded = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 50_000_000,
                "_dollar_volume_basis": "adv20_completed_sessions",
                "_data_through": old_data_through,
                "_source_status": "fallback",
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=later,
        data_through=old_data_through,
        market_session="premarket",
        universe_version="bounded-v1",
        dollar_volume_scope="candidate",
    )

    admitted, admitted_count = _admit_cross_session_intraday_symbol(
        bounded,
        candidate=candidate,
        current=current,
        max_symbols=1,
    )

    assert admitted_count == 1
    assert [symbol.ticker for symbol in admitted.symbols] == ["EXT"]
    assert admitted.symbols[0].validation_status == "valid_external"


def test_cross_session_recovery_retains_old_truth_and_excludes_new_fallbacks(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    old_as_of = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
    old_data_through = datetime(2026, 7, 13, 22, 20, tzinfo=timezone.utc)
    premarket_now = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    daily_fallback = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
    settings = _settings(
        path,
        FOCUS_PRIORITY_WATCHLIST="NEWFALLBACK",
        FOCUS_MAX_SYMBOLS=2,
    )
    repository.initialize(now=old_as_of)
    initial = build_focus_context(
        settings=_settings(path),
        strength_rows=[
            {
                "ticker": "AAPL",
                "cumulative_dollar_volume": 1_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "_data_through": old_data_through,
                "_source_status": "active",
                "_data_source": "Yahoo/yfinance",
                "session_change_pct": 3.5,
                "rvol_time_of_day": 2.0,
                "data_quality": 1.0,
                "universe_member": True,
            },
            {
                "ticker": "MSFT",
                "cumulative_dollar_volume": 900_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "_data_through": old_data_through,
                "_source_status": "active",
                "_data_source": "Yahoo/yfinance",
                "session_change_pct": 2.5,
                "rvol_time_of_day": 1.8,
                "data_quality": 1.0,
                "universe_member": True,
            },
        ],
        canonical_symbols=["AAPL", "MSFT"],
        as_of=old_as_of,
        data_through=old_data_through,
        market_session="closed",
        universe_version="themes-old-v1",
    )
    repository.publish_focus_context(initial, now=old_as_of)

    universe_as_of = old_as_of
    strength_payload = {
        "as_of": premarket_now.isoformat(),
        "universe_as_of": universe_as_of.isoformat(),
        "universe_version": "themes-new-v1",
        "universe_count": 4,
        "_focus_rows": [
            {
                "ticker": ticker,
                "avg_dollar_volume_20d": dollar_volume,
                "data_quality": 0.9,
                "universe_member": True,
                "universe_as_of": universe_as_of.isoformat(),
                "daily_data_through": daily_fallback.isoformat(),
            }
            for ticker, dollar_volume in (
                ("AAPL", 50_000_000),
                ("MSFT", 49_000_000),
                ("NEWFALLBACK", 45_000_000),
                ("NEWEXACT", 40_000_000),
            )
        ],
    }

    def premarket_frame() -> pd.DataFrame:
        index: list[datetime] = []
        volumes: list[float] = []
        for day in (
            date(2026, 7, 7),
            date(2026, 7, 8),
            date(2026, 7, 9),
            date(2026, 7, 10),
            date(2026, 7, 13),
            date(2026, 7, 14),
        ):
            for minute in range(8 * 60, 8 * 60 + 30, 5):
                index.append(
                    datetime.combine(
                        day,
                        time(hour=minute // 60, minute=minute % 60),
                        tzinfo=ET,
                    )
                )
                volumes.append(200.0 if day == date(2026, 7, 14) else 100.0)
        return pd.DataFrame(
            {
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": volumes,
            },
            index=pd.DatetimeIndex(index),
        )

    async def intraday_loader(_tickers, _cutoff) -> dict:
        return {
            "NEWEXACT": _snapshot(
                "NEWEXACT",
                premarket_frame(),
                data_through="not-a-timestamp",
            )
        }

    clock_now = [premarket_now]
    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: clock_now[0]),
        strength_loader=lambda: asyncio.sleep(0, result=strength_payload),
        discovery_loader=lambda _snapshot: asyncio.sleep(
            0,
            result={
                "provider": "tradingview",
                "status": "active",
                "as_of": premarket_now,
                "warnings": [],
                "candidates": [],
            },
        ),
        intraday_loader=intraday_loader,
        breakout_loader=lambda: [
            {"ticker": "NEWFALLBACK", "lifecycle_state": "CONFIRMED"}
        ],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}cross-session",
    )
    token = repository.acquire_worker_lock(
        LOCK_NAME,
        producer.owner_id,
        lease_seconds=settings.producer_lease_seconds,
        now=premarket_now,
    )
    assert token is not None

    result = asyncio.run(producer.run_once(fencing_token=token))

    assert result["status"] == "completed"
    assert result["cross_session_transition"] is True
    assert result["failed_transition_recovery"] is False
    assert result["cross_session_retained_count"] == 1
    assert result["cross_session_excluded_count"] == 1
    assert result["cross_session_fresh_admitted_count"] == 1
    assert result["stale_symbol_count"] == 1
    assert result["fallback_symbol_count"] == 0
    assert "focus_snapshot_time_regression" not in result["warnings"]
    assert "focus_cross_session_stale_retained" in result["warnings"]
    assert "focus_cross_session_fallback_excluded" in result["warnings"]
    assert "focus_cross_session_fresh_admitted" in result["warnings"]

    current = repository.current_focus_context()
    assert current is not None
    assert current.revision == 2
    assert current.market_session == "premarket"
    assert current.data_through == old_data_through
    symbols = {symbol.ticker: symbol for symbol in current.symbols}
    assert set(symbols) == {"AAPL", "NEWEXACT"}
    assert "NEWFALLBACK" not in symbols
    retained = symbols["AAPL"]
    assert retained.data_through == old_data_through
    assert retained.data_status == "stale"
    assert retained.source_status == "stale"
    assert retained.dollar_volume == pytest.approx(1_000_000.0)
    assert retained.dollar_volume_rank is None
    assert retained.session_change_pct is None
    assert retained.rvol_time_of_day is None
    assert retained.data_quality is None
    assert "stale_retained" in retained.universe_reasons
    exact = symbols["NEWEXACT"]
    assert exact.data_status == "active"
    assert exact.source_status == "active"
    assert exact.dollar_volume_basis == "intraday_completed_bars"
    assert exact.data_through == premarket_now
    assert exact.dollar_volume_rank == 3
    assert "candidate_dollar_volume_top20" in exact.universe_reasons

    async def advance_clock(seconds: float) -> None:
        clock_now[0] += timedelta(seconds=seconds)

    async def wait_until_next_heartbeat() -> bool:
        return await producer._wait_until(
            premarket_now + timedelta(seconds=60),
            stop=asyncio.Event(),
            fencing_token=token,
        )

    producer.sleeper = advance_clock
    assert asyncio.run(wait_until_next_heartbeat()) is True
    health = health_payload(settings, repository=repository, now=clock_now[0])
    assert health["healthy"] is True
    assert health["status"] == "degraded"
    assert health["production_status"] == "degraded"
    assert health["database"]["details"]["cross_session_retained_count"] == 1
    assert health["database"]["details"]["cross_session_excluded_count"] == 1
    repository.release_worker_lock(LOCK_NAME, producer.owner_id, token)


def test_failed_same_session_transition_requires_fresh_intraday_before_recovery(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    settings = _settings(path)
    active = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "AAPL",
                "cumulative_dollar_volume": 1_000_000,
                "_dollar_volume_basis": "intraday_completed_bars",
                "_data_through": SUMMER_NOW,
                "_source_status": "active",
                "_data_source": "Yahoo/yfinance",
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="themes-test-v1",
    )
    stale = active.model_copy(
        update={
            "symbols": [
                symbol.model_copy(
                    update={
                        "data_status": "stale",
                        "source_status": "stale",
                        "dollar_volume_rank": None,
                        "session_change_pct": None,
                        "rvol_time_of_day": None,
                        "data_quality": None,
                    }
                )
                for symbol in active.symbols
            ],
            "warnings": ["focus_snapshot_stale", "focus_snapshot_time_regression"],
        }
    )
    repository.publish_focus_context(stale, now=SUMMER_NOW)
    later = SUMMER_NOW + timedelta(minutes=30)

    blocked = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: later),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=lambda snapshot: asyncio.sleep(
            0, result={**_discovery(snapshot), "as_of": later}
        ),
        intraday_loader=lambda _tickers, _cutoff: asyncio.sleep(0, result={}),
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}same-session-blocked",
    )
    blocked_token = repository.acquire_worker_lock(
        LOCK_NAME,
        blocked.owner_id,
        lease_seconds=settings.producer_lease_seconds,
        now=later,
    )
    assert blocked_token is not None

    blocked_result = asyncio.run(blocked.run_once(fencing_token=blocked_token))

    assert blocked_result["status"] == "degraded"
    assert blocked_result["error_code"] == "focus_recovery_requires_intraday"
    assert blocked_result["stale_revision"] is None
    assert repository.current_focus_context().revision == 1
    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM focus_context_snapshots"
        ).fetchone()[0] == 1
    repository.release_worker_lock(
        LOCK_NAME,
        blocked.owner_id,
        blocked_token,
    )

    async def exact_intraday(tickers, _cutoff) -> dict:
        return {
            ticker: _snapshot(
                ticker,
                _intraday_frame(
                    price=100.0,
                    current_volume=200.0,
                    end_minute=10 * 60 + 30,
                ),
                data_through=later,
            )
            for ticker in tickers
        }

    recovered = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: later),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=lambda snapshot: asyncio.sleep(
            0, result={**_discovery(snapshot), "as_of": later}
        ),
        intraday_loader=exact_intraday,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}same-session-recovered",
    )
    recovered_token = repository.acquire_worker_lock(
        LOCK_NAME,
        recovered.owner_id,
        lease_seconds=settings.producer_lease_seconds,
        now=later,
    )
    assert recovered_token is not None

    recovered_result = asyncio.run(
        recovered.run_once(fencing_token=recovered_token)
    )

    assert recovered_result["status"] == "completed"
    assert recovered_result["cross_session_transition"] is False
    assert recovered_result["failed_transition_recovery"] is True
    assert recovered_result["cross_session_retained_count"] == 0
    assert recovered_result["intraday_exact_count"] == 2
    current = repository.current_focus_context()
    assert current is not None
    assert current.revision == 2
    assert current.data_through == later
    assert all(symbol.data_status == "active" for symbol in current.symbols)
    repository.release_worker_lock(
        LOCK_NAME,
        recovered.owner_id,
        recovered_token,
    )


def test_non_typical_intraday_dollar_volume_is_rejected_to_named_fallback(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "focus.db")
    repository.initialize(now=SUMMER_NOW)

    async def intraday_without_high_low(_tickers, _cutoff) -> dict:
        frame = _intraday_frame(price=100, current_volume=200).drop(
            columns=["High", "Low"]
        )
        return {"AAPL": _snapshot("AAPL", frame)}

    producer = FocusContextProducer(
        settings=_settings(tmp_path / "focus.db"),
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=lambda snapshot: asyncio.sleep(
            0, result={**_discovery(snapshot), "candidates": _discovery(snapshot)["candidates"][:1]}
        ),
        intraday_loader=intraday_without_high_low,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}non-typical",
    )
    result = asyncio.run(producer.run_once())
    assert result["status"] == "completed"
    assert result["non_typical_dollar_volume_count"] == 1
    assert "focus_intraday_non_typical_price_rejected" in result["warnings"]
    symbol = {item.ticker: item for item in repository.current_focus_context().symbols}[
        "AAPL"
    ]
    assert symbol.dollar_volume_basis == "adv20_completed_sessions"
    assert symbol.source_status == "fallback"


def test_failure_retains_previous_snapshot_as_stale(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    settings = _settings(path)
    initial = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 1_000_000,
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="test-v1",
    )
    repository.publish_focus_context(initial, now=SUMMER_NOW)
    later = SUMMER_NOW + timedelta(minutes=30)

    async def fail_strength() -> dict:
        raise RuntimeError("fixture_failure")

    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: later),
        strength_loader=fail_strength,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}failure",
    )
    token = repository.acquire_worker_lock(
        LOCK_NAME,
        producer.owner_id,
        lease_seconds=settings.producer_lease_seconds,
        now=later,
    )
    assert token is not None
    result = asyncio.run(producer.run_once(fencing_token=token))

    assert result["status"] == "degraded"
    assert result["stale_revision"] == 2
    assert result["active_symbol_count"] == 0
    assert result["stale_symbol_count"] == 1
    assert result["fallback_symbol_count"] == 0
    assert result["unavailable_symbol_count"] == 0
    assert result["rvol_available_count"] == 0
    current = repository.current_focus_context()
    assert current is not None
    assert current.revision == 2
    assert current.data_through == SUMMER_NOW
    assert current.symbols[0].data_status == "stale"
    assert current.symbols[0].rvol_time_of_day is None
    assert current.symbols[0].source_status == "stale"
    assert "focus_snapshot_stale" in current.warnings
    health = health_payload(settings, repository=repository, now=later)
    assert health["healthy"] is True
    assert health["status"] == "degraded"
    assert health["production_status"] == "degraded"
    assert health["database"]["details"]["stale_symbol_count"] == 1
    assert health["database"]["details"]["active_symbol_count"] == 0
    repository.release_worker_lock(LOCK_NAME, producer.owner_id, token)


def test_regressing_fallback_is_rejected_and_published_as_stale(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    settings = _settings(path)

    async def initial_intraday(tickers, _cutoff) -> dict:
        return {
            ticker: _snapshot(
                ticker,
                _intraday_frame(price=100.0, current_volume=200.0),
            )
            for ticker in tickers
        }

    initial = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=_strength_payload()),
        discovery_loader=lambda snapshot: asyncio.sleep(
            0, result=_discovery(snapshot)
        ),
        intraday_loader=initial_intraday,
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}regression-initial",
    )
    first = asyncio.run(initial.run_once())
    active = repository.current_focus_context()
    assert first["status"] == "completed"
    assert active is not None
    assert active.revision == 1
    assert active.data_through == SUMMER_NOW
    assert all(item.data_status == "active" for item in active.symbols)

    later = SUMMER_NOW + timedelta(minutes=30)

    async def unexpected_strength_loader() -> dict:
        raise AssertionError("same completed-day strength cache should be reused")

    fallback = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: later),
        strength_loader=unexpected_strength_loader,
        discovery_loader=lambda snapshot: asyncio.sleep(
            0, result={**_discovery(snapshot), "as_of": later}
        ),
        intraday_loader=lambda _tickers, _cutoff: asyncio.sleep(0, result={}),
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}regression-fallback",
    )
    token = repository.acquire_worker_lock(
        LOCK_NAME,
        fallback.owner_id,
        lease_seconds=settings.producer_lease_seconds,
        now=later,
    )
    assert token is not None
    result = asyncio.run(fallback.run_once(fencing_token=token))

    current = repository.current_focus_context()
    assert result["status"] == "degraded"
    assert result["error_code"] == "focus_snapshot_time_regression"
    assert result["stale_revision"] == 2
    assert result["active_symbol_count"] == 0
    assert result["stale_symbol_count"] == len(active.symbols)
    assert current is not None
    assert current.revision == 2
    assert current.data_through == active.data_through
    assert all(item.data_status == "stale" for item in current.symbols)
    assert all(item.source_status == "stale" for item in current.symbols)
    assert "focus_snapshot_time_regression" in current.warnings

    health = health_payload(settings, repository=repository, now=later)
    assert health["healthy"] is True
    assert health["status"] == "degraded"
    assert health["production_status"] == "degraded"
    assert health["database"]["details"]["active_symbol_count"] == 0
    assert health["database"]["details"]["stale_symbol_count"] == len(
        active.symbols
    )
    repository.release_worker_lock(LOCK_NAME, fallback.owner_id, token)


def test_unavailable_daily_strength_result_is_not_cached(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    unavailable = {
        **_strength_payload(),
        "status": "unavailable",
    }
    producer = FocusContextProducer(
        settings=_settings(path),
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        strength_loader=lambda: asyncio.sleep(0, result=unavailable),
        breakout_loader=lambda: [],
        owner_id=f"{FOCUS_PRODUCER_WORKER_PREFIX}unavailable-cache",
    )

    result = asyncio.run(producer.run_once())

    assert result["status"] == "unavailable"
    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM focus_daily_strength_snapshots"
        ).fetchone()[0] == 0


def test_focus_health_and_fencing_are_independent_from_sync_worker(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    sync_token = repository.acquire_worker_lock(
        "catalyst-sync-worker",
        "sync-worker",
        lease_seconds=90,
        now=SUMMER_NOW,
    )
    focus_owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}health"
    focus_token = repository.acquire_worker_lock(
        "focus-context-producer",
        focus_owner,
        lease_seconds=90,
        now=SUMMER_NOW,
    )
    assert sync_token is not None and focus_token is not None
    repository.heartbeat("sync-worker", "idle", {"kind": "sync"}, now=SUMMER_NOW)
    repository.heartbeat(focus_owner, "running", {"kind": "focus"}, now=SUMMER_NOW)
    draft = build_focus_context(
        settings=_settings(path),
        strength_rows=[
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 1_000_000,
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="test-v1",
    )
    repository.publish_focus_context(draft, now=SUMMER_NOW)

    sync_health = repository.worker_health(
        heartbeat_ttl_seconds=120,
        now=SUMMER_NOW,
    )
    focus_health = repository.focus_producer_health(
        heartbeat_ttl_seconds=120,
        now=SUMMER_NOW,
    )
    assert sync_health["status"] == "idle"
    assert focus_health["status"] == "running"
    assert focus_health["details"] == {"kind": "focus"}
    assert focus_health["snapshot_fresh"] is True
    payload = health_payload(
        _settings(path),
        repository=repository,
        now=SUMMER_NOW,
    )
    assert payload["healthy"] is True
    assert payload["ready_dependency"] is False

    with pytest.raises(CatalystRepositoryError, match="Focus producer lease"):
        repository.publish_focus_context(
            draft,
            now=SUMMER_NOW,
            lock_name="focus-context-producer",
            owner_id=focus_owner,
            fencing_token=focus_token + 1,
        )
    assert repository.current_focus_context().revision == 1


def test_focus_health_requires_a_present_and_fresh_snapshot(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}snapshot-health"
    token = repository.acquire_worker_lock(
        LOCK_NAME,
        owner,
        lease_seconds=4000,
        now=SUMMER_NOW,
    )
    assert token is not None
    repository.heartbeat(owner, "idle", {"kind": "focus"}, now=SUMMER_NOW)

    missing = repository.focus_producer_health(
        heartbeat_ttl_seconds=120,
        snapshot_ttl_seconds=1800,
        now=SUMMER_NOW,
    )
    assert missing["healthy"] is False
    assert missing["snapshot_fresh"] is False
    assert missing["latest_snapshot"] is None

    draft = build_focus_context(
        settings=_settings(path),
        strength_rows=[
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 1_000_000,
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="health-v1",
    )
    repository.publish_focus_context(draft, now=SUMMER_NOW)
    fresh = repository.focus_producer_health(
        heartbeat_ttl_seconds=120,
        snapshot_ttl_seconds=1800,
        now=SUMMER_NOW,
    )
    assert fresh["healthy"] is True
    assert fresh["snapshot_fresh"] is True

    later = SUMMER_NOW + timedelta(seconds=1801)
    assert repository.renew_worker_lock(
        LOCK_NAME,
        owner,
        token,
        lease_seconds=4000,
        now=later,
    )
    repository.heartbeat(owner, "idle", {"kind": "focus"}, now=later)
    stale = repository.focus_producer_health(
        heartbeat_ttl_seconds=120,
        snapshot_ttl_seconds=1800,
        now=later,
    )
    assert stale["healthy"] is False
    assert stale["heartbeat_fresh"] is True
    assert stale["lock_live"] is True
    assert stale["snapshot_fresh"] is False
    assert stale["snapshot_age_seconds"] == 1801


def test_focus_health_allows_running_refresh_inside_snapshot_grace(tmp_path) -> None:
    path = tmp_path / "focus.db"
    settings = _settings(
        path,
        FOCUS_CONTEXT_REFRESH_SECONDS=1800,
        FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120,
    )
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}grace"
    token = repository.acquire_worker_lock(
        LOCK_NAME, owner, lease_seconds=4000, now=SUMMER_NOW
    )
    assert token is not None
    draft = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 1_000_000,
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="grace-v1",
    )
    repository.publish_focus_context(draft, now=SUMMER_NOW)

    inside = SUMMER_NOW + timedelta(seconds=1810)
    repository.heartbeat(
        owner,
        "running",
        {"stage": "preparing", "refresh_started_at": inside.isoformat()},
        now=inside,
    )
    grace = health_payload(settings, repository=repository, now=inside)
    assert grace["healthy"] is True
    assert grace["status"] == "degraded"
    assert grace["production_status"] == "degraded"
    assert grace["database"]["warnings"] == ["focus_refresh_in_progress"]

    expired = SUMMER_NOW + timedelta(seconds=1921)
    repository.heartbeat(
        owner,
        "running",
        {"stage": "preparing", "refresh_started_at": inside.isoformat()},
        now=expired,
    )
    unhealthy = health_payload(settings, repository=repository, now=expired)
    assert unhealthy["healthy"] is False
    assert unhealthy["status"] == "unhealthy"
    assert unhealthy["database"]["snapshot_fresh"] is False


def test_focus_health_rejects_idle_worker_inside_snapshot_grace(tmp_path) -> None:
    path = tmp_path / "focus.db"
    settings = _settings(
        path,
        FOCUS_CONTEXT_REFRESH_SECONDS=1800,
        FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120,
    )
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}idle-grace"
    token = repository.acquire_worker_lock(
        LOCK_NAME, owner, lease_seconds=4000, now=SUMMER_NOW
    )
    assert token is not None
    draft = build_focus_context(
        settings=settings,
        strength_rows=[
            {
                "ticker": "AAPL",
                "avg_dollar_volume_20d": 1_000_000,
                "universe_member": True,
            }
        ],
        canonical_symbols=["AAPL"],
        as_of=SUMMER_NOW,
        data_through=SUMMER_NOW,
        market_session="regular",
        universe_version="idle-grace-v1",
    )
    repository.publish_focus_context(draft, now=SUMMER_NOW)

    inside = SUMMER_NOW + timedelta(seconds=1810)
    repository.heartbeat(owner, "idle", {"stage": "waiting"}, now=inside)
    result = health_payload(settings, repository=repository, now=inside)

    assert result["healthy"] is False
    assert result["status"] == "unhealthy"
    assert result["database"]["snapshot_fresh"] is True
    assert result["database"]["snapshot_within_refresh"] is False
    assert result["database"]["refresh_in_progress"] is False


def test_focus_health_allows_only_bounded_first_start_grace(tmp_path) -> None:
    path = tmp_path / "focus.db"
    settings = _settings(
        path,
        FOCUS_CONTEXT_REFRESH_SECONDS=1800,
        FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120,
    )
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}startup-grace"
    token = repository.acquire_worker_lock(
        LOCK_NAME, owner, lease_seconds=4000, now=SUMMER_NOW
    )
    assert token is not None
    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: SUMMER_NOW),
        owner_id=owner,
    )
    producer._heartbeat(
        "running",
        {"stage": "starting", "refresh_started_at": SUMMER_NOW.isoformat()},
    )
    producer._heartbeat("running", {"stage": "preparing"})

    inside = health_payload(
        settings,
        repository=repository,
        now=SUMMER_NOW + timedelta(seconds=119),
    )
    assert inside["healthy"] is True
    assert inside["status"] == "degraded"
    assert inside["database"]["startup_in_progress"] is True
    assert inside["database"]["details"]["refresh_started_at"] == (
        SUMMER_NOW.isoformat()
    )

    expired_at = SUMMER_NOW + timedelta(seconds=121)
    assert repository.renew_worker_lock(
        LOCK_NAME,
        owner,
        token,
        lease_seconds=4000,
        now=expired_at,
    )
    repository.heartbeat(
        owner,
        "running",
        {"stage": "preparing", "refresh_started_at": SUMMER_NOW.isoformat()},
        now=expired_at,
    )
    expired = health_payload(settings, repository=repository, now=expired_at)
    assert expired["healthy"] is False
    assert expired["status"] == "unhealthy"
    assert expired["database"]["startup_in_progress"] is False


def test_focus_health_never_combines_old_heartbeat_with_new_owner_lock(
    tmp_path,
) -> None:
    path = tmp_path / "focus.db"
    settings = _settings(
        path,
        FOCUS_CONTEXT_REFRESH_SECONDS=1800,
        FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120,
    )
    repository = CatalystRepository(path)
    repository.initialize(now=SUMMER_NOW)
    old_owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}old-owner"
    old_token = repository.acquire_worker_lock(
        LOCK_NAME, old_owner, lease_seconds=90, now=SUMMER_NOW
    )
    assert old_token is not None
    repository.heartbeat(
        old_owner,
        "running",
        {"stage": "starting", "refresh_started_at": SUMMER_NOW.isoformat()},
        now=SUMMER_NOW,
    )

    takeover_at = SUMMER_NOW + timedelta(seconds=91)
    new_owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}new-owner"
    new_token = repository.acquire_worker_lock(
        LOCK_NAME, new_owner, lease_seconds=90, now=takeover_at
    )
    assert new_token is not None
    result = health_payload(settings, repository=repository, now=takeover_at)

    assert result["healthy"] is False
    assert result["status"] == "unhealthy"
    assert result["database"]["heartbeat_fresh"] is False
    assert result["database"]["startup_in_progress"] is False


def test_enabled_focus_producer_rejects_refresh_schedule_drift(tmp_path) -> None:
    with pytest.raises(ValueError, match="must match"):
        _settings(
            tmp_path / "focus.db",
            FOCUS_CONTEXT_REFRESH_SECONDS=60,
            FOCUS_PRODUCER_INTERVAL_SECONDS=1800,
        )


def test_default_strength_loader_never_requests_options_or_implicit_publication(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[dict] = []

    async def fake_scan_strength(**kwargs) -> dict:
        calls.append(kwargs)
        return _strength_payload()

    monkeypatch.setattr(
        "app.services.strength.scanner.scan_strength",
        fake_scan_strength,
    )
    asyncio.run(_default_strength_loader(_settings(tmp_path / "focus.db")))

    assert calls == [
        {
            "timeframe": "all",
            "profile": "balanced",
            "top": 40,
            "include_options": False,
            "_include_focus_rows": True,
            "_publish_focus": False,
        }
    ]


def test_invalid_configuration_returns_structured_health_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "app.services.catalysts.focus_worker.get_focus_context_settings",
        lambda: (_ for _ in ()).throw(ValueError("bad config")),
    )
    code = asyncio.run(
        _async_main(SimpleNamespace(once=False, healthcheck=True))
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out) == {
        "healthy": False,
        "status": "invalid_configuration",
        "error_code": "configuration_error",
        "ready_dependency": False,
    }


def _service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z0-9][a-z0-9-]*:\n|^volumes:\n|\Z)",
        compose,
    )
    assert match is not None
    return match.group(1)


def test_compose_has_isolated_focus_producer_without_openai_or_readiness_coupling(
    monkeypatch,
) -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = _service_block(compose, "focus-context-producer")
    backend = _service_block(compose, "backend")

    assert "app.services.catalysts.focus_worker" in worker
    assert '"--healthcheck"' in worker
    assert "optix-data:/data" in worker
    assert "read_only: true" in worker
    assert "no-new-privileges:true" in worker
    assert "cap_drop:\n      - ALL" in worker
    assert "OPENAI" not in worker
    assert "APP_AUTH_TOKEN" not in worker
    assert "YAHOO_OPTIONS_ENABLED=false" in worker
    assert "MARKETDATA_OPTIONS_ENRICH_LIMIT=0" in worker
    assert "ports:" not in worker
    assert "depends_on:" not in worker
    assert "focus-context-producer" not in backend

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in (
        "FOCUS_PRODUCER_ENABLED=false",
        "FOCUS_PRODUCER_INTERVAL_SECONDS=1800",
        "FOCUS_PRODUCER_CANDIDATE_LIMIT=40",
        "FOCUS_PRODUCER_HEARTBEAT_SECONDS=30",
        "FOCUS_PRODUCER_HEALTH_STALE_SECONDS=120",
        "FOCUS_PRODUCER_LEASE_SECONDS=90",
        "FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120",
        "FOCUS_DAILY_STRENGTH_SETTLEMENT_DELAY_SECONDS=1800",
        "FOCUS_DAILY_STRENGTH_MIN_COVERAGE=0.9",
        "FOCUS_SNAPSHOT_FULL_RESOLUTION_DAYS=30",
        "FOCUS_SNAPSHOT_DAILY_ROLLUP_ENABLED=true",
    ):
        assert line in env

    assert "FOCUS_PRODUCER_ENABLED=${FOCUS_PRODUCER_ENABLED:-false}" in worker
    monkeypatch.delenv("FOCUS_PRODUCER_ENABLED", raising=False)
    assert FocusContextSettings(_env_file=None).producer_enabled is False
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "set_env_value FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS 120" in setup
    assert (
        'focus_producer_enabled="$(configuration_boolean '
        'FOCUS_PRODUCER_ENABLED false)"'
    ) in deploy
    assert (
        'focus_producer_snapshot_grace_seconds="${focus_producer_snapshot_grace_seconds:-120}"'
        in deploy
    )
    assert '"$focus_producer_snapshot_grace_seconds" -lt 30' in deploy
    assert '"$focus_producer_snapshot_grace_seconds" -gt 900' in deploy
    assert 'p["status"] in {"ok", "degraded"}' in deploy
    assert 'p["database"]["latest_snapshot"] is not None' in deploy
    assert 'p["database"]["snapshot_fresh"] is True' in deploy
    assert "FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120" in readme
    assert "默认 120 秒，可设置为 30 至 900 秒" in readme
