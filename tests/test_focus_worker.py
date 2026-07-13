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
    _async_main,
    _default_strength_loader,
    _merge_candidate_rows,
    fixed_refresh_times,
    health_payload,
    next_refresh_at,
)
from app.services.catalysts.repository import CatalystRepository
from app.services.market_calendar import ET


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


def _strength_payload(*, as_of: datetime = SUMMER_NOW) -> dict:
    universe_as_of = as_of - timedelta(hours=18)
    daily_data_through = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    return {
        "as_of": as_of.isoformat(),
        "universe_as_of": universe_as_of.isoformat(),
        "universe_version": "themes-test-v1",
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


def _intraday_frame(*, price: float, current_volume: float) -> pd.DataFrame:
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
        for minute in range(9 * 60 + 30, 10 * 60, 5):
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


def _snapshot(ticker: str, frame: pd.DataFrame, *, quality: float = 1.0):
    return SimpleNamespace(
        ticker=ticker,
        frame=frame,
        source="Yahoo/yfinance",
        data_through=SUMMER_NOW,
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
    repository.release_worker_lock(LOCK_NAME, producer.owner_id, token)


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
    payload = health_payload(
        _settings(path),
        repository=repository,
        now=SUMMER_NOW,
    )
    assert payload["healthy"] is True
    assert payload["ready_dependency"] is False

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
    with pytest.raises(CatalystRepositoryError, match="Focus producer lease"):
        repository.publish_focus_context(
            draft,
            now=SUMMER_NOW,
            lock_name="focus-context-producer",
            owner_id=focus_owner,
            fencing_token=focus_token + 1,
        )
    assert repository.current_focus_context() is None


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


def test_compose_has_isolated_focus_producer_without_openai_or_readiness_coupling() -> None:
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
        "FOCUS_PRODUCER_ENABLED=true",
        "FOCUS_PRODUCER_INTERVAL_SECONDS=1800",
        "FOCUS_PRODUCER_CANDIDATE_LIMIT=40",
        "FOCUS_PRODUCER_HEARTBEAT_SECONDS=30",
        "FOCUS_PRODUCER_HEALTH_STALE_SECONDS=120",
        "FOCUS_PRODUCER_LEASE_SECONDS=90",
    ):
        assert line in env
