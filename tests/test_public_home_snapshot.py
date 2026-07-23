from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    request_owner_access_context,
    require_public_read_or_owner_access,
)
from app.api import earnings, market, options, signals, stocks
from app.data_paths import get_data_paths
from app.public_home_snapshot import (
    PUBLIC_HOME_INDEX_SYMBOLS,
    PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS,
    PUBLIC_HOME_RESOURCE_ORDER,
    PUBLIC_HOME_RESOURCE_SPECS,
    PUBLIC_HOME_SNAPSHOT_MAX_BYTES,
    PUBLIC_HOME_SNAPSHOT_VERSION,
    breakout_lead_chart_parameters,
    create_public_home_entry,
    public_home_resource_parameters,
    read_public_home_entries,
    read_public_home_resource,
    validate_public_home_payload,
    write_public_home_snapshot,
)
from app.services.cache import cache
from app.personal_config import AccessConfig
from app.worker.tasks import PublicHomeTask


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _regular_time() -> float:
    return datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc).timestamp()


def _weekend_time() -> float:
    return datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc).timestamp()


def _watchlist_payload(*, price: float = 100.0) -> dict:
    return {
        "groups": [
            {
                "id": "core",
                "name": "Core",
                "stocks": [
                    {
                        "ticker": "NVDA",
                        "name": "NVIDIA",
                        "price": price,
                        "change_percent": 1.0,
                        "spark": [price - 1, price],
                    }
                ],
            }
        ],
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "failed_tickers": [],
        "data_limited": False,
        "source_status": "active",
    }


def _seed_watchlist(
    bundle_path: Path,
    saved_at: float,
    *,
    price: float = 100.0,
) -> Path:
    path = bundle_path.parent / "watchlist-snapshot-v1.json"
    stocks._write_watchlist_snapshot(
        path,
        payload=_watchlist_payload(price=price),
        saved_at=saved_at,
    )
    return path


def _payload(resource: str, now: float, *, price: float = 100.0) -> dict:
    if resource == "watchlist":
        return _watchlist_payload(price=price)
    if resource == "indices":
        return {
            "indices": [
                {
                    "symbol": symbol,
                    "price": price + index,
                    "change_percent": 0.5,
                }
                for index, symbol in enumerate(PUBLIC_HOME_INDEX_SYMBOLS)
            ],
            "attempted": len(PUBLIC_HOME_INDEX_SYMBOLS),
            "succeeded": len(PUBLIC_HOME_INDEX_SYMBOLS),
            "data_limited": False,
            "source_status": "active",
            "as_of": _iso(now),
        }
    if resource == "focus_overview":
        return {
            "ticker": "NVDA",
            "name": "英伟达",
            "name_en": "NVIDIA Corporation",
            "website": "https://www.nvidia.com",
            "logo_url": "https://example.test/nvda.svg",
            "logo_urls": ["https://example.test/nvda.svg"],
            "price": price,
            "change": 1.0,
            "change_percent": 1.0,
            "volume": 1000,
            "market_cap": 1_000_000.0,
            "prev_close": price - 1,
            "high": price + 2,
            "low": price - 2,
            "open": price - 0.5,
            "as_of": _iso(now),
            "price_provider": "Massive",
            "description": "测试公司",
            "description_en": "Test company",
            "sic_description": "Semiconductors",
            "pe_ratio": 30.0,
            "dividend_yield": None,
            "year_high": price + 20,
            "year_low": price - 20,
        }
    if resource == "focus_chart":
        timestamp = int(now) - 60
        return {
            "ticker": "NVDA",
            "range": "1d",
            "period": "2y",
            "interval": "1d",
            "exchange_timezone": "America/New_York",
            "price_adjustment": "raw",
            "include_extended_hours": False,
            "moving_average_scope": "regular_session_only",
            "as_of": _iso(now),
            "last_bar_at": _iso(timestamp),
            "source_status": "active",
            "visible": 120,
            "bars": [
                {
                    "t": timestamp,
                    "o": price,
                    "h": price + 2,
                    "l": price - 2,
                    "c": price + 1,
                    "v": 1000,
                    "ext": False,
                    "quote_only": False,
                    "session": "regular",
                }
            ],
            "ema20": [],
            "sma50": [],
        }
    if resource == "focus_signals":
        return {
            "ticker": "NVDA",
            "price": price,
            "score": 60,
            "overall": "bullish",
            "signals": {
                "rsi": {"value": 50.0, "signal": "neutral", "label": "RSI(14)"},
                "macd": {"value": 0.1, "signal": "bullish", "label": "MACD"},
                "ema20": {"value": 99.0, "signal": "above", "label": "EMA(20)"},
                "sma50": {"value": 98.0, "signal": "above", "label": "SMA(50)"},
                "volume": {"value": 1.0, "signal": "normal", "label": "Volume"},
            },
            "tags": ["TREND"],
        }
    if resource == "market_signals":
        return {
            "signals": {
                "rsi14": {
                    "value": 50.0,
                    "label": "SPY RSI(14)",
                    "top_score": 0,
                    "bottom_score": 0,
                },
                "_breadth_coverage": {
                    "available": 11,
                    "expected": 11,
                    "ratio": 1.0,
                },
                "_source_status": {
                    "value": "active",
                    "label": "板块广度数据完整",
                },
            },
            "scores": {
                "top_score": 20,
                "bottom_score": 10,
                "top_status": "active",
                "bottom_status": "active",
                "data_quality": 100,
                "signal_data_quality": 100,
                "data_quality_available": 14,
                "data_quality_expected": 14,
                "coverage": {
                    "top_active_weight": 0.9,
                    "bottom_active_weight": 0.95,
                    "top_ratio": 0.9,
                    "bottom_ratio": 0.95,
                    "top_missing_components": ["positioning"],
                    "bottom_missing_components": ["sentiment_pessimism"],
                },
                "top_breakdown": {
                    "price_overheated": 20.0,
                    "breadth_divergence": 10.0,
                    "options_sentiment": 30.0,
                    "volatility_turning": 10.0,
                    "rates_pressure": 10.0,
                    "credit_risk": 10.0,
                    "positioning": None,
                },
                "bottom_breakdown": {
                    "panic_release": 10.0,
                    "technical_reclaim": 20.0,
                    "breadth_repair": 20.0,
                    "volatility_falling": 10.0,
                    "credit_stable": 10.0,
                    "rates_easing": 10.0,
                    "sentiment_pessimism": None,
                },
                "top_label": "顶部风险低",
                "bottom_label": "没有底部迹象",
                "top_reasons": {
                    "raising": ["SPY RSI(14) 50.0"],
                    "suppressing": [],
                },
                "bottom_reasons": {
                    "raising": [],
                    "suppressing": ["SPY RSI(14) 50.0"],
                },
            },
            "as_of": _iso(now),
        }
    if resource == "earnings":
        market_date = datetime.fromtimestamp(now, timezone.utc).date()
        return {
            "earnings": [
                {
                    "ticker": "NVDA",
                    "name": "NVIDIA Corporation",
                    "earnings_date": (market_date + timedelta(days=3)).isoformat(),
                    "days_until": 3,
                    "timing": "amc",
                    "eps_estimate": 1.0,
                    "eps_actual": None,
                    "eps_high": 1.1,
                    "eps_low": 0.9,
                    "revenue_estimate": 1_000_000.0,
                    "revenue_actual": None,
                    "market_cap": 2_000_000.0,
                    "sector": "Technology",
                    "earnings_date_source": "calendar",
                    "estimate_source": "calendar",
                    "actual_source": None,
                    "release_status": "scheduled",
                    "quarter": None,
                    "year": None,
                    "source_status": "active",
                    "observed_at": _iso(now),
                    "expected_move_pct": 7.5,
                    "expected_move_expiration": (
                        market_date + timedelta(days=7)
                    ).isoformat(),
                    "expected_move_source": "Yahoo/yfinance options",
                    "expected_move_observed_at": _iso(now),
                    "expected_move_source_status": "active",
                }
            ],
            "attempted": 1,
            "succeeded": 1,
            "failed_symbols": [],
            "data_limited": False,
            "source_status": "active",
            "providers": ["Yahoo Finance"],
            "as_of": _iso(now),
        }
    if resource == "unusual":
        return {
            "results": [
                {
                    "ticker": "NVDA",
                    "contract_ticker": "NVDA270101C00100000",
                    "contract_type": "call",
                    "type": "call",
                    "strike": 100.0,
                    "expiration": "2027-01-01",
                    "volume": 200,
                    "open_interest": 100,
                    "oi": 100,
                    "vol_oi_ratio": 2.0,
                    "vol_oi": 2.0,
                    "premium": 20_000.0,
                    "last_price": 1.0,
                    "implied_volatility": 0.5,
                    "underlying_price": price,
                    "in_the_money": False,
                    "moneyness": "atm",
                    "direction": None,
                    "direction_confidence": 0,
                    "direction_status": "unavailable_without_trade_side",
                    "signal": "unknown",
                    "inferred_direction": "unknown",
                    "direction_deprecated": True,
                }
            ],
            "data_limited": False,
            "source_status": "active",
            "attempted": 1,
            "succeeded": 1,
            "failed_symbols": [],
            "partial_symbols": [],
            "as_of": _iso(now),
        }
    raise AssertionError(resource)


def _entries(now: float, *, price: float = 100.0) -> dict[str, dict]:
    return {
        resource: create_public_home_entry(
            resource,
            _payload(resource, now, price=price),
            saved_at=now,
            parameters=public_home_resource_parameters(resource, now=now),
        )
        for resource in PUBLIC_HOME_RESOURCE_ORDER
    }


def _task_config() -> SimpleNamespace:
    return SimpleNamespace(
        poll_seconds=30,
        watchlist_seconds=1800,
        indices_seconds=300,
        overview_seconds=300,
        chart_seconds=300,
        signals_seconds=900,
        earnings_seconds=21_600,
        unusual_seconds=1800,
        failure_retry_seconds=300,
    )


def test_snapshot_round_trip_and_atomic_generation_reload(tmp_path: Path) -> None:
    now = time.time()
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, _entries(now), now=now)

    first_inode = path.stat().st_ino
    first = read_public_home_resource(
        "focus_overview",
        parameters=public_home_resource_parameters("focus_overview", now=now),
        path=path,
        now=now + 1,
    )
    assert first is not None
    assert first["price"] == 100.0
    assert first["_stale"] is True
    assert first["source_status"] == "degraded"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    write_public_home_snapshot(path, _entries(now + 2, price=125.0), now=now + 2)
    second = read_public_home_resource(
        "focus_overview",
        parameters=public_home_resource_parameters("focus_overview", now=now + 2),
        path=path,
        now=now + 3,
    )
    assert second is not None
    assert second["price"] == 125.0
    assert path.stat().st_ino != first_inode


def test_snapshot_rejects_corruption_version_expiry_and_parameter_drift(
    tmp_path: Path,
) -> None:
    now = time.time()
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, _entries(now), now=now)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["resources"]["focus_overview"]["payload"]["internal_secret"] = "no"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = read_public_home_entries(path, now=now + 1)
    assert "focus_overview" not in loaded
    assert "indices" in loaded

    document["version"] = PUBLIC_HOME_SNAPSHOT_VERSION + 1
    path.write_text(json.dumps(document), encoding="utf-8")
    assert read_public_home_entries(path, now=now + 1) == {}

    write_public_home_snapshot(path, _entries(now), now=now)
    assert read_public_home_resource(
        "focus_chart",
        parameters={"ticker": "NVDA", "range": "15m", "adjustment": "raw"},
        path=path,
        now=now + 1,
    ) is None


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_snapshot_rejects_duplicate_keys_and_non_finite_json(
    tmp_path: Path,
    constant: str,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"version":1,"version":1,"resources":{}}',
        encoding="utf-8",
    )
    assert read_public_home_entries(duplicate, now=time.time()) == {}

    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text(
        f'{{"version":1,"resources":{{"indices":{constant}}}}}',
        encoding="utf-8",
    )
    assert read_public_home_entries(non_finite, now=time.time()) == {}


def test_snapshot_rejects_future_saved_at(tmp_path: Path) -> None:
    now = time.time()
    path = tmp_path / "future.json"
    document = {
        "version": PUBLIC_HOME_SNAPSHOT_VERSION,
        "resources": _entries(now),
    }
    document["resources"]["indices"]["saved_at"] = now + 1
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = read_public_home_entries(path, now=now)
    assert "indices" not in loaded
    assert "focus_overview" in loaded
    assert read_public_home_resource(
        "focus_overview",
        parameters={"ticker": "NVDA"},
        path=path,
        now=now + PUBLIC_HOME_RESOURCE_SPECS["focus_overview"].max_age + 1,
    ) is None


@pytest.mark.parametrize(
    ("resource", "field"),
    (
        ("indices", "as_of"),
        ("focus_overview", "as_of"),
        ("focus_chart", "as_of"),
        ("focus_chart", "last_bar_at"),
        ("earnings", "as_of"),
        ("earnings", "observed_at"),
        ("unusual", "as_of"),
    ),
)
def test_snapshot_rejects_payload_clock_far_after_saved_at(
    resource: str,
    field: str,
) -> None:
    now = time.time()
    payload = _payload(resource, now)
    future = _iso(now + PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS + 1)
    if field == "observed_at":
        payload["earnings"][0][field] = future
    else:
        payload[field] = future
    with pytest.raises(ValueError, match="timestamp is later than saved_at"):
        create_public_home_entry(
            resource,
            payload,
            saved_at=now,
            parameters=public_home_resource_parameters(resource, now=now),
        )


def test_snapshot_rejects_future_chart_bar_timestamp() -> None:
    now = time.time()
    payload = _payload("focus_chart", now)
    payload["bars"][0]["t"] = int(
        now + PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS + 1
    )
    with pytest.raises(ValueError, match="timestamp is later than saved_at"):
        create_public_home_entry(
            "focus_chart",
            payload,
            saved_at=now,
            parameters=public_home_resource_parameters(
                "focus_chart",
                now=now,
            ),
        )


def test_snapshot_enforces_payload_size_shape_and_field_whitelists() -> None:
    now = time.time()
    overview = _payload("focus_overview", now)
    overview["internal"] = "hidden"
    with pytest.raises(ValueError, match="invalid public home payload"):
        validate_public_home_payload("focus_overview", overview)

    oversized = _payload("focus_overview", now)
    oversized["description"] = "x" * 262_145
    with pytest.raises(ValueError, match="invalid public home payload"):
        validate_public_home_payload("focus_overview", oversized)

    with pytest.raises(ValueError, match="invalid public home payload"):
        validate_public_home_payload("focus_chart", {})

    unusual = _payload("unusual", now)
    unusual["results"][0]["provider_token"] = "hidden"
    with pytest.raises(ValueError, match="invalid public home payload"):
        validate_public_home_payload("unusual", unusual)


def test_earnings_snapshot_accepts_finnhub_actuals_and_real_expected_move() -> None:
    now = _regular_time()
    payload = _payload("earnings", now)
    row = payload["earnings"][0]
    row.update(
        {
            "ticker": "AAOI",
            "name": "Applied Optoelectronics",
            "earnings_date": (
                datetime.fromtimestamp(now, timezone.utc).date()
                - timedelta(days=1)
            ).isoformat(),
            "days_until": -1,
            "earnings_date_source": "finnhub_calendar",
            "estimate_source": "finnhub_calendar",
            "eps_actual": 0.42,
            "revenue_actual": 118_000_000.0,
            "actual_source": "finnhub_calendar",
            "release_status": "released",
            "quarter": 2,
            "year": 2026,
            "expected_move_pct": None,
            "expected_move_expiration": None,
            "expected_move_source": None,
            "expected_move_observed_at": None,
            "expected_move_source_status": None,
        }
    )
    payload["providers"] = ["Finnhub"]

    validated = validate_public_home_payload("earnings", payload)

    assert validated["earnings"][0]["eps_actual"] == 0.42
    assert validated["providers"] == ["Finnhub"]


def test_five_thousand_earnings_rows_fit_public_snapshot_limit(
    tmp_path: Path,
) -> None:
    now = _regular_time()
    payload = _payload("earnings", now)
    base = {
        **payload["earnings"][0],
        "earnings_date_source": "finnhub_calendar",
        "estimate_source": "finnhub_calendar",
    }
    payload["earnings"] = [
        {**base, "ticker": f"T{index:04d}", "name": f"Company {index}"}
        for index in range(5_000)
    ]
    payload["attempted"] = 5_000
    payload["succeeded"] = 5_000
    payload["providers"] = ["Finnhub"]
    entry = create_public_home_entry(
        "earnings",
        payload,
        saved_at=now,
        parameters=public_home_resource_parameters("earnings", now=now),
    )
    target = tmp_path / "public-home-snapshot-v1.json"

    write_public_home_snapshot(target, {"earnings": entry}, now=now)

    assert target.stat().st_size < PUBLIC_HOME_SNAPSHOT_MAX_BYTES
    assert "earnings" in read_public_home_entries(target, now=now)


def test_snapshot_write_failure_preserves_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, _entries(now), now=now)
    original = path.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_public_home_snapshot(path, _entries(now + 1, price=130.0), now=now + 1)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_snapshot_rejects_target_and_parent_symlinks(tmp_path: Path) -> None:
    now = time.time()
    victim = tmp_path / "victim.json"
    victim.write_text("safe", encoding="utf-8")
    target = tmp_path / "snapshot.json"
    target.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        write_public_home_snapshot(target, _entries(now), now=now)
    assert victim.read_text(encoding="utf-8") == "safe"
    assert read_public_home_entries(target, now=now) == {}

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    linked_target = linked_parent / "snapshot.json"
    with pytest.raises(ValueError, match="symlink"):
        write_public_home_snapshot(linked_target, _entries(now), now=now)
    assert read_public_home_entries(linked_target, now=now) == {}


def test_worker_publishes_quick_resources_before_heavy_failure(tmp_path: Path) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    calls: list[str] = []

    def builder(resource: str):
        async def run(_parameters: dict) -> dict:
            calls.append(resource)
            if resource == "earnings":
                raise RuntimeError("provider failed")
            return _payload(resource, now)

        return run

    task = PublicHomeTask(
        _task_config(),
        builders={
            resource: builder(resource)
            for resource in ("watchlist", *PUBLIC_HOME_RESOURCE_ORDER)
        },
        snapshot_path=path,
        clock=lambda: now,
    )
    result = asyncio.run(task())

    assert result.status == "degraded"
    assert result.details["deferred"] == ["unusual"]
    assert result.details["failed"] == ["earnings"]
    assert calls == [
        "watchlist",
        "indices",
            "focus_overview",
            "focus_chart",
            "focus_signals",
            "market_signals",
            "earnings",
        ]
    assert set(read_public_home_entries(path, now=now)) == {
        "indices",
        "focus_overview",
        "focus_chart",
        "focus_signals",
        "market_signals",
    }


def test_worker_keeps_complete_earnings_snapshot_when_refresh_is_limited(
    tmp_path: Path,
) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(now)
    entries["earnings"]["saved_at"] = now - 21_601
    old_saved_at = entries["earnings"]["saved_at"]
    old_payload = json.loads(json.dumps(entries["earnings"]["payload"]))
    write_public_home_snapshot(path, entries, now=now)
    _seed_watchlist(path, now)
    calls = 0

    async def limited(_parameters: dict) -> dict:
        nonlocal calls
        calls += 1
        payload = _payload("earnings", now)
        payload["earnings"][0]["ticker"] = "PARTIAL"
        payload["earnings"][0]["name"] = "Partial fallback"
        payload["data_limited"] = True
        payload["source_status"] = "degraded"
        return payload

    task = PublicHomeTask(
        _task_config(),
        builders={"earnings": limited},
        snapshot_path=path,
        clock=lambda: now,
    )

    result = asyncio.run(task())
    persisted = read_public_home_entries(path, now=now)["earnings"]

    assert result.status == "degraded"
    assert result.details["failed"] == ["earnings"]
    assert result.details["refreshed"] == []
    assert persisted["saved_at"] == old_saved_at
    assert persisted["payload"] == old_payload
    assert persisted["payload"]["data_limited"] is False
    assert persisted["payload"]["source_status"] == "active"
    assert calls == 1


def test_worker_staggers_heavy_resources_and_skips_not_due_providers(
    tmp_path: Path,
) -> None:
    current = [_regular_time()]
    path = tmp_path / "public-home-snapshot-v1.json"
    calls: list[str] = []

    def builder(resource: str):
        async def run(_parameters: dict) -> dict:
            calls.append(resource)
            return _payload(resource, current[0])

        return run

    task = PublicHomeTask(
        _task_config(),
        builders={
            resource: builder(resource)
            for resource in ("watchlist", *PUBLIC_HOME_RESOURCE_ORDER)
        },
        snapshot_path=path,
        clock=lambda: current[0],
    )
    first = asyncio.run(task())
    assert first.details["deferred"] == ["unusual"]
    assert calls[-1] == "earnings"

    current[0] += 30
    calls.clear()
    second = asyncio.run(task())
    assert calls == ["unusual"]
    assert second.details["deferred"] == []

    current[0] += 30
    calls.clear()
    third = asyncio.run(task())
    assert third.status == "idle"
    assert calls == []
    assert set(read_public_home_entries(path, now=current[0])) == set(
        PUBLIC_HOME_RESOURCE_ORDER
    )


def test_worker_retries_failed_heavy_resource_after_cooldown_only(
    tmp_path: Path,
) -> None:
    current = [_regular_time()]
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(current[0])
    entries["unusual"]["saved_at"] = current[0] - 1801
    write_public_home_snapshot(path, entries, now=current[0])
    _seed_watchlist(path, current[0])
    calls = 0

    async def fail(_parameters: dict) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider failed")

    def refresh(resource: str):
        async def run(_parameters: dict) -> dict:
            return _payload(resource, current[0])

        return run

    task = PublicHomeTask(
        _task_config(),
        builders={
            resource: fail if resource == "unusual" else refresh(resource)
            for resource in ("watchlist", *PUBLIC_HOME_RESOURCE_ORDER)
        },
        snapshot_path=path,
        clock=lambda: current[0],
    )
    first = asyncio.run(task())
    assert first.status == "degraded"
    assert first.details["retry_after_seconds"]["unusual"] == 300
    current[0] += 30
    cooling = asyncio.run(task())
    assert cooling.status == "degraded"
    assert cooling.error_code == "public_home_refresh_cooling"
    assert cooling.details["cooling"] == ["unusual"]
    assert calls == 1

    current[0] += 270
    second = asyncio.run(task())
    assert second.status == "degraded"
    assert second.details["retry_after_seconds"]["unusual"] == 600
    assert calls == 2

    current[0] += 600
    third = asyncio.run(task())
    assert third.details["retry_after_seconds"]["unusual"] == 1200
    current[0] += 1200
    fourth = asyncio.run(task())
    assert fourth.details["retry_after_seconds"]["unusual"] == 1800
    current[0] += 1800
    capped = asyncio.run(task())
    assert capped.details["retry_after_seconds"]["unusual"] == 1800
    assert calls == 5


def test_worker_cancelled_round_keeps_single_flight_and_later_harvests(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = _regular_time()
        path = tmp_path / "public-home-snapshot-v1.json"
        entries = _entries(now)
        entries["focus_overview"]["saved_at"] = now - 301
        write_public_home_snapshot(path, entries, now=now)
        _seed_watchlist(path, now)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def delayed(_parameters: dict) -> dict:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return _payload("focus_overview", now, price=135.0)

        task = PublicHomeTask(
            _task_config(),
            builders={"focus_overview": delayed},
            snapshot_path=path,
            clock=lambda: now,
        )
        round_one = asyncio.create_task(task())
        await asyncio.wait_for(started.wait(), timeout=1)
        round_one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await round_one

        pending = await task()
        assert pending.status == "degraded"
        assert pending.error_code == "public_home_refresh_in_flight"
        assert pending.details["in_flight"] == ["focus_overview"]
        assert calls == 1

        release.set()
        await asyncio.sleep(0)
        harvested = await task()
        assert harvested.status == "idle"
        assert harvested.details["refreshed"] == ["focus_overview"]
        assert calls == 1
        stored = read_public_home_entries(path, now=now)
        assert stored["focus_overview"]["payload"]["price"] == 135.0
        await task.aclose()

    asyncio.run(scenario())


def test_worker_rebuilds_missing_watchlist_and_anonymous_reads_it_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, _entries(now), now=now)
    calls = 0

    async def build(_parameters: dict) -> dict:
        nonlocal calls
        calls += 1
        return _watchlist_payload(price=123.0)

    task = PublicHomeTask(
        _task_config(),
        builders={"watchlist": build},
        snapshot_path=path,
        clock=lambda: now,
    )
    result = asyncio.run(task())
    snapshot = path.parent / "watchlist-snapshot-v1.json"

    assert result.status == "idle"
    assert result.details["refreshed"] == ["watchlist"]
    assert calls == 1
    assert stocks._read_watchlist_snapshot(snapshot, now=now) is not None

    async def unexpected() -> dict:
        raise AssertionError("anonymous watchlist called provider")

    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot)
    monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", False)
    monkeypatch.setattr(stocks, "_watchlist_snapshot_observed", None)
    monkeypatch.setattr(stocks, "_build_watchlist", unexpected)
    monkeypatch.setattr(stocks.time, "time", lambda: now)
    stocks._endpoint_cache.clear()

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await stocks.watchlist()

    payload = asyncio.run(scenario())
    assert payload["groups"][0]["stocks"][0]["price"] == 123.0
    assert payload["_stale"] is True


def test_worker_watchlist_failure_preserves_last_good_file(tmp_path: Path) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, _entries(now), now=now)
    watchlist_path = _seed_watchlist(path, now - 1801, price=95.0)
    original = watchlist_path.read_bytes()

    async def fail(_parameters: dict) -> dict:
        raise RuntimeError("provider failed")

    task = PublicHomeTask(
        _task_config(),
        builders={"watchlist": fail},
        snapshot_path=path,
        clock=lambda: now,
    )
    result = asyncio.run(task())

    assert result.status == "degraded"
    assert result.details["failed"] == ["watchlist"]
    assert "watchlist" in result.details["available"]
    assert watchlist_path.read_bytes() == original


def test_weekend_uses_closed_windows_and_keeps_unusual_servable(
    tmp_path: Path,
) -> None:
    now = _weekend_time()
    saved_at = now - 2 * 60 * 60
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = {
        resource: create_public_home_entry(
            resource,
            _payload(resource, saved_at),
            saved_at=saved_at,
            parameters=public_home_resource_parameters(resource, now=now),
        )
        for resource in PUBLIC_HOME_RESOURCE_ORDER
    }
    write_public_home_snapshot(path, entries, now=now)
    _seed_watchlist(path, saved_at)
    calls: list[str] = []

    def builder(resource: str):
        async def run(_parameters: dict) -> dict:
            calls.append(resource)
            return _payload(resource, now)

        return run

    task = PublicHomeTask(
        _task_config(),
        builders={
            resource: builder(resource)
            for resource in ("watchlist", *PUBLIC_HOME_RESOURCE_ORDER)
        },
        snapshot_path=path,
        clock=lambda: now,
    )
    result = asyncio.run(task())

    assert result.status == "idle"
    assert result.details["available"] == [
        "watchlist",
        *PUBLIC_HOME_RESOURCE_ORDER,
    ]
    assert result.details["unavailable"] == []
    assert result.details["deferred"] == ["unusual"]
    assert calls == []


@pytest.mark.parametrize("expired", [False, True], ids=["missing", "hard-expired"])
def test_weekend_allows_one_unusual_first_fill_when_not_servable(
    tmp_path: Path,
    expired: bool,
) -> None:
    now = _weekend_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(now)
    if expired:
        entries["unusual"]["saved_at"] = (
            now - PUBLIC_HOME_RESOURCE_SPECS["unusual"].max_age - 1
        )
    else:
        entries.pop("unusual")
    write_public_home_snapshot(path, entries, now=now)
    _seed_watchlist(path, now)
    calls = 0

    async def build(_parameters: dict) -> dict:
        nonlocal calls
        calls += 1
        return _payload("unusual", now)

    task = PublicHomeTask(
        _task_config(),
        builders={"unusual": build},
        snapshot_path=path,
        clock=lambda: now,
    )
    first = asyncio.run(task())
    second = asyncio.run(task())

    assert first.status == "idle"
    assert first.details["refreshed"] == ["unusual"]
    assert second.details["deferred"] == ["unusual"]
    assert calls == 1


def test_weekend_retries_only_unusual_persistence_after_write_failure(
    tmp_path: Path,
) -> None:
    current = [_weekend_time()]
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(current[0])
    entries.pop("unusual")
    write_public_home_snapshot(path, entries, now=current[0])
    _seed_watchlist(path, current[0])
    builder_calls = 0
    writer_calls = 0

    async def build(_parameters: dict) -> dict:
        nonlocal builder_calls
        builder_calls += 1
        return _payload("unusual", current[0])

    def fail_once_writer(
        destination: Path,
        bundle: dict[str, dict],
        *,
        now: float,
    ) -> None:
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            raise OSError("simulated first persistence failure")
        write_public_home_snapshot(destination, bundle, now=now)

    config = _task_config()
    for field in (
        "watchlist_seconds",
        "indices_seconds",
        "overview_seconds",
        "chart_seconds",
        "signals_seconds",
        "earnings_seconds",
    ):
        setattr(config, field, 7 * 24 * 60 * 60)
    task = PublicHomeTask(
        config,
        builders={"unusual": build},
        writer=fail_once_writer,
        snapshot_path=path,
        clock=lambda: current[0],
    )
    failed = asyncio.run(task())
    current[0] += 6 * 60 * 60
    repaired = asyncio.run(task())

    assert failed.status == "degraded"
    assert failed.details["failed"] == ["unusual"]
    assert repaired.status == "idle"
    assert repaired.details["refreshed"] == ["unusual"]
    assert "unusual" in repaired.details["available"]
    assert builder_calls == 1
    assert writer_calls == 2


@pytest.mark.parametrize(
    ("resource", "minimum_retry"),
    (("indices", 6 * 60 * 60), ("earnings", 12 * 60 * 60)),
)
def test_closed_failure_retry_respects_resource_window(
    tmp_path: Path,
    resource: str,
    minimum_retry: int,
) -> None:
    current = [_weekend_time()]
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(current[0])
    entries[resource]["saved_at"] = current[0] - minimum_retry - 1
    write_public_home_snapshot(path, entries, now=current[0])
    _seed_watchlist(path, current[0])
    calls = 0

    async def fail(_parameters: dict) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider failed")

    task = PublicHomeTask(
        _task_config(),
        builders={resource: fail},
        snapshot_path=path,
        clock=lambda: current[0],
    )
    first = asyncio.run(task())
    current[0] += 30
    cooling = asyncio.run(task())

    assert first.details["retry_after_seconds"][resource] == minimum_retry
    assert cooling.status == "degraded"
    assert resource in cooling.details["cooling"]
    assert calls == 1


def test_hanging_heavy_refresh_does_not_block_quick_or_duplicate_heavy(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        current = [_regular_time()]
        path = tmp_path / "public-home-snapshot-v1.json"
        entries = _entries(current[0])
        entries["earnings"]["saved_at"] = current[0] - 21_601
        write_public_home_snapshot(path, entries, now=current[0])
        _seed_watchlist(path, current[0])
        heavy_started = asyncio.Event()
        heavy_release = asyncio.Event()
        calls: dict[str, int] = {}

        def builder(resource: str):
            async def run(_parameters: dict) -> dict:
                calls[resource] = calls.get(resource, 0) + 1
                if resource == "earnings":
                    heavy_started.set()
                    await heavy_release.wait()
                return _payload(resource, current[0])

            return run

        task = PublicHomeTask(
            _task_config(),
            builders={
                resource: builder(resource)
                for resource in ("watchlist", *PUBLIC_HOME_RESOURCE_ORDER)
            },
            snapshot_path=path,
            clock=lambda: current[0],
        )
        first = asyncio.create_task(task())
        await asyncio.wait_for(heavy_started.wait(), timeout=1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        current[0] += 301
        second = await task()
        assert second.status == "degraded"
        assert second.details["in_flight"] == ["earnings"]
        assert calls["earnings"] == 1
        assert calls["focus_overview"] == 1

        heavy_release.set()
        await asyncio.sleep(0)
        third = await task()
        assert third.status == "idle"
        assert third.details["refreshed"] == ["earnings"]
        assert calls["earnings"] == 1
        await task.aclose()

    asyncio.run(scenario())


def test_external_fresh_generation_clears_local_failure(tmp_path: Path) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(now)
    entries["focus_overview"]["saved_at"] = now - 301
    write_public_home_snapshot(path, entries, now=now)
    _seed_watchlist(path, now)
    calls = 0

    async def fail(_parameters: dict) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider failed")

    task = PublicHomeTask(
        _task_config(),
        builders={"focus_overview": fail},
        snapshot_path=path,
        clock=lambda: now,
    )
    failed = asyncio.run(task())
    repaired = read_public_home_entries(path, now=now)
    repaired["focus_overview"] = create_public_home_entry(
        "focus_overview",
        _payload("focus_overview", now, price=140.0),
        saved_at=now,
        parameters=public_home_resource_parameters("focus_overview", now=now),
    )
    write_public_home_snapshot(path, repaired, now=now)
    recovered = asyncio.run(task())

    assert failed.status == "degraded"
    assert recovered.status == "idle"
    assert "focus_overview" not in recovered.details["retry_after_seconds"]
    assert calls == 1


def test_unusual_uses_options_close_on_early_close_day(tmp_path: Path) -> None:
    before = datetime(2026, 11, 27, 18, 5, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 11, 27, 18, 16, tzinfo=timezone.utc).timestamp()

    def run_at(observed: float, name: str) -> tuple[object, list[str]]:
        path = tmp_path / name
        entries = _entries(observed)
        entries["unusual"]["saved_at"] = observed - 1801
        write_public_home_snapshot(path, entries, now=observed)
        _seed_watchlist(path, observed)
        calls: list[str] = []

        async def build(_parameters: dict) -> dict:
            calls.append("unusual")
            return _payload("unusual", observed)

        task = PublicHomeTask(
            _task_config(),
            builders={"unusual": build},
            snapshot_path=path,
            clock=lambda: observed,
        )
        return asyncio.run(task()), calls

    before_result, before_calls = run_at(before, "before.json")
    after_result, after_calls = run_at(after, "after.json")

    assert before_result.details["refreshed"] == ["unusual"]
    assert before_calls == ["unusual"]
    assert after_result.details["deferred"] == ["unusual"]
    assert after_calls == []


def test_worker_default_signal_builder_bypasses_stale_public_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(now)
    entries["focus_signals"]["saved_at"] = now - 901
    write_public_home_snapshot(path, entries, now=now)
    _seed_watchlist(path, now)
    calls: list[str] = []

    async def live_signals(ticker: str) -> dict:
        calls.append(ticker)
        return _payload("focus_signals", now, price=222.0)

    async def cached_api_must_not_run(_ticker: str) -> dict:
        raise AssertionError("worker reused the cache-aware API endpoint")

    monkeypatch.setattr(stocks, "_build_stock_signals", live_signals)
    monkeypatch.setattr(stocks, "stock_signals", cached_api_must_not_run)
    task = PublicHomeTask(
        _task_config(),
        snapshot_path=path,
        clock=lambda: now,
    )
    async def scenario() -> object:
        with request_owner_access_context(False):
            return await task()

    result = asyncio.run(scenario())

    assert result.status == "idle"
    assert result.details["refreshed"] == ["focus_signals"]
    assert result.details["failed"] == []
    assert calls == ["NVDA"]
    stored_entry = read_public_home_entries(path, now=now)["focus_signals"]
    stored = stored_entry["payload"]
    assert stored_entry["saved_at"] == now
    assert stored["price"] == 222.0
    assert set(stored) == {"ticker", "price", "score", "overall", "signals", "tags"}


def test_owner_endpoint_and_worker_share_live_signal_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    calls: list[str] = []

    async def live_signals(ticker: str) -> dict:
        calls.append(ticker)
        payload = _payload("focus_signals", now, price=180.0)
        payload["ticker"] = ticker
        return payload

    monkeypatch.setattr(stocks, "_build_stock_signals", live_signals)
    stocks._endpoint_cache.pop("technical-signals:AAPL", None)
    try:
        with request_owner_access_context(True):
            api_payload = asyncio.run(stocks.stock_signals("AAPL"))
    finally:
        stocks._endpoint_cache.pop("technical-signals:AAPL", None)
        stocks._endpoint_locks.pop("technical-signals:AAPL", None)
    task = PublicHomeTask(_task_config(), clock=lambda: now)
    worker_payload = asyncio.run(
        task._default_build(
            "focus_signals",
            {"ticker": "NVDA", "period": "100d"},
        )
    )

    assert calls == ["AAPL", "NVDA"]
    assert api_payload["ticker"] == "AAPL"
    assert api_payload["_stale"] is False
    assert api_payload["source_status"] == "active"
    assert worker_payload["ticker"] == "NVDA"
    assert "_stale" not in worker_payload
    assert "source_status" not in worker_payload


def test_public_home_worker_does_not_block_on_massive_symbol_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = _regular_time()
    async def prewarm(*, allow_refresh: bool) -> dict:
        raise AssertionError(
            "the independent stock_directory task owns provider prewarming"
        )

    def builder(resource: str):
        async def run(_parameters: dict) -> dict:
            return _payload(resource, now)

        return run

    monkeypatch.setattr(stocks, "_stock_directory", prewarm)
    task = PublicHomeTask(
        _task_config(),
        builders={
            resource: builder(resource)
            for resource in ("watchlist", *PUBLIC_HOME_RESOURCE_ORDER)
        },
        snapshot_path=tmp_path / "public-home-snapshot-v1.json",
        clock=lambda: now,
    )

    asyncio.run(task())

    assert (
        task._snapshot_path
        == tmp_path / "public-home-snapshot-v1.json"
    )


def test_earnings_refreshes_immediately_on_new_york_date_rollover(
    tmp_path: Path,
) -> None:
    before = datetime(2026, 7, 18, 3, 59, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 7, 18, 4, 1, tzinfo=timezone.utc).timestamp()
    assert public_home_resource_parameters("earnings", now=before) == {
        "market_date": "2026-07-17"
    }
    assert public_home_resource_parameters("earnings", now=after) == {
        "market_date": "2026-07-18"
    }

    path = tmp_path / "public-home-snapshot-v1.json"
    entries = _entries(after)
    entries["earnings"] = create_public_home_entry(
        "earnings",
        _payload("earnings", before),
        saved_at=before,
        parameters=public_home_resource_parameters("earnings", now=before),
    )
    write_public_home_snapshot(path, entries, now=after)
    _seed_watchlist(path, after)
    calls: list[dict] = []

    async def refresh(parameters: dict) -> dict:
        calls.append(parameters)
        return _payload("earnings", after)

    task = PublicHomeTask(
        _task_config(),
        builders={"earnings": refresh},
        snapshot_path=path,
        clock=lambda: after,
    )
    result = asyncio.run(task())
    assert result.status == "idle"
    assert calls == [{"market_date": "2026-07-18"}]
    assert read_public_home_entries(path, now=after)["earnings"]["parameters"] == {
        "market_date": "2026-07-18"
    }


def test_public_cold_process_reads_file_without_provider_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    path = get_data_paths().public_home_snapshot
    write_public_home_snapshot(path, _entries(now), now=now)
    original = path.read_bytes()
    cache.clear()
    stocks._endpoint_cache.clear()

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("anonymous request called provider")

    def unexpected_ticker(*_args, **_kwargs):
        raise AssertionError("anonymous request called provider")

    monkeypatch.setattr(market, "_build_indices", unexpected)
    monkeypatch.setattr(stocks, "_stock_overview_impl", unexpected)
    monkeypatch.setattr(stocks, "_stock_chart_impl", unexpected)
    monkeypatch.setattr(stocks.yf, "Ticker", unexpected_ticker)
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", unexpected)
    monkeypatch.setattr(options, "_unusual_activity_impl", unexpected)
    monkeypatch.setattr(signals, "compute_market_signals", unexpected_ticker)

    async def scenario() -> None:
        with request_owner_access_context(False):
            assert (await market.market_indices())["_stale"] is True
            assert (await stocks.stock_overview("NVDA"))["price"] == 100.0
            assert (await stocks.stock_chart("NVDA", "1d", "raw"))["_stale"] is True
            assert (await stocks.stock_signals("NVDA"))["_stale"] is True
            assert (await earnings.upcoming_earnings())["_stale"] is True
            assert (
                await options.unusual_activity(type="all", min_vol_oi=1.0)
            )["_stale"] is True

    asyncio.run(scenario())
    assert path.read_bytes() == original


def test_public_snapshot_parameter_scope_never_leaks_default_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_public_home_snapshot(
        get_data_paths().public_home_snapshot,
        _entries(now),
        now=now,
    )
    cache.clear()
    stocks._endpoint_cache.clear()

    async def unavailable(awaitable) -> None:
        with pytest.raises(HTTPException) as captured:
            await awaitable
        assert captured.value.status_code == 503
        assert captured.value.detail["code"] == "public_snapshot_unavailable"

    async def scenario() -> None:
        with request_owner_access_context(False):
            await unavailable(stocks.stock_overview("AAPL"))
            await unavailable(stocks.stock_signals("AAPL"))
            await unavailable(stocks.stock_chart("NVDA", "15m", "raw"))
            await unavailable(stocks.stock_chart("NVDA", "1d", "adjusted"))
            await unavailable(options.unusual_activity(type="call", min_vol_oi=1.0))
            await unavailable(options.unusual_activity(type="all", min_vol_oi=2.0))

    asyncio.run(scenario())


def test_owner_stock_path_still_refreshes_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stocks._endpoint_cache.clear()
    calls = 0

    async def owner_builder(_ticker: str) -> dict:
        nonlocal calls
        calls += 1
        return {"ticker": "AAPL", "name": "Apple", "price": 42.0}

    monkeypatch.setattr(stocks, "_stock_overview_impl", owner_builder)

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await stocks.stock_overview("AAPL")

    payload = asyncio.run(scenario())
    assert payload["ticker"] == "AAPL"
    assert payload["price"] == 42.0
    assert payload["_stale"] is False
    assert calls == 1


def test_private_network_owner_ignores_old_public_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_public_home_snapshot(
        get_data_paths().public_home_snapshot,
        _entries(now, price=100.0),
        now=now,
    )
    config = stocks.get_personal_config()
    private_config = config.model_copy(
        update={
            "access": config.access.model_copy(
                update={"mode": "private_network"}
            )
        }
    )
    monkeypatch.setattr(stocks, "get_personal_config", lambda: private_config)
    stocks._endpoint_cache.clear()
    calls = 0

    async def live_builder(_ticker: str) -> dict:
        nonlocal calls
        calls += 1
        return {"ticker": "NVDA", "name": "NVIDIA", "price": 42.0}

    monkeypatch.setattr(stocks, "_stock_overview_impl", live_builder)

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await stocks.stock_overview("NVDA")

    payload = asyncio.run(scenario())
    assert payload["price"] == 42.0
    assert payload["_stale"] is False
    assert calls == 1


def test_owner_closed_cold_process_reuses_two_hour_disk_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    saved_at = now - 2 * 60 * 60
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    private_config = market.get_personal_config()
    password_config = private_config.model_copy(
        update={
            "access": private_config.access.model_copy(
                update={"mode": "password"}
            )
        }
    )
    for module in (market, stocks, earnings, options):
        monkeypatch.setattr(
            module,
            "get_personal_config",
            lambda: password_config,
        )
    entries = {
        resource: create_public_home_entry(
            resource,
            _payload(resource, saved_at),
            saved_at=saved_at,
            parameters=public_home_resource_parameters(resource, now=now),
        )
        for resource in PUBLIC_HOME_RESOURCE_ORDER
    }
    write_public_home_snapshot(
        get_data_paths().public_home_snapshot,
        entries,
        now=now,
    )
    watchlist_path = _seed_watchlist(
        get_data_paths().public_home_snapshot,
        saved_at,
    )
    cache.clear()
    stocks._endpoint_cache.clear()
    options._unusual_failure_deadlines.clear()
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", watchlist_path)
    calls: list[str] = []

    async def unexpected(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("owner default request called provider")

    def unexpected_ticker(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("owner default request called provider")

    monkeypatch.setattr(market, "_build_indices", unexpected)
    monkeypatch.setattr(stocks, "_build_watchlist", unexpected)
    monkeypatch.setattr(stocks, "_stock_overview_impl", unexpected)
    monkeypatch.setattr(stocks, "_stock_chart_impl", unexpected)
    monkeypatch.setattr(stocks.yf, "Ticker", unexpected_ticker)
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", unexpected)
    monkeypatch.setattr(options, "_unusual_activity_impl", unexpected)

    async def scenario() -> list[dict]:
        with request_owner_access_context(True):
            return [
                await market.market_indices(),
                await stocks.watchlist(),
                await stocks.stock_overview("NVDA"),
                await stocks.stock_chart("NVDA", "1d", "raw"),
                await stocks.stock_signals("NVDA"),
                await earnings.upcoming_earnings(),
                await options.unusual_activity(type="all", min_vol_oi=1.0),
            ]

    payloads = asyncio.run(scenario())

    assert calls == []
    for payload in (*payloads[:5], payloads[6]):
        assert payload["_stale"] is True
        assert payload["source_status"] == "degraded"
    assert payloads[5].get("_stale") is not True
    assert payloads[5]["source_status"] == "active"


def test_password_mode_anonymous_dependency_reads_snapshot_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    path = get_data_paths().public_home_snapshot
    write_public_home_snapshot(path, _entries(now), now=now)
    original = path.read_bytes()
    cache.clear()
    stocks._endpoint_cache.clear()
    calls: list[str] = []

    async def unexpected(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("anonymous request called provider")

    def unexpected_ticker(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("anonymous request called provider")

    monkeypatch.setattr(market, "_build_indices", unexpected)
    monkeypatch.setattr(stocks, "_stock_overview_impl", unexpected)
    monkeypatch.setattr(stocks, "_stock_chart_impl", unexpected)
    monkeypatch.setattr(stocks.yf, "Ticker", unexpected_ticker)
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", unexpected)
    monkeypatch.setattr(options, "_unusual_activity_impl", unexpected)
    monkeypatch.setattr(signals, "compute_market_signals", unexpected_ticker)

    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("public-home-test-password"),
    )
    dependency = [Depends(require_public_read_or_owner_access)]
    for router in (
        market.router,
        stocks.router,
        earnings.router,
        options.router,
        signals.router,
    ):
        app.include_router(router, dependencies=dependency)

    with TestClient(app, base_url="https://testserver") as client:
        responses = [
            client.get("/api/market/indices"),
            client.get("/api/stocks/NVDA"),
            client.get("/api/stocks/NVDA/chart?range=1d&adjustment=raw"),
            client.get("/api/stocks/NVDA/signals"),
            client.get("/api/earnings/upcoming"),
            client.get("/api/options/unusual?type=all&min_vol_oi=1.0"),
            client.get("/api/signals/market"),
        ]

    assert [response.status_code for response in responses] == [200] * 7
    assert all(response.json()["_stale"] is True for response in responses)
    assert calls == []
    assert path.read_bytes() == original


def test_breakout_lead_chart_snapshot_binds_payload_to_exact_ticker() -> None:
    now = time.time()
    parameters = breakout_lead_chart_parameters("nvec")
    payload = {**_payload("focus_chart", now), "ticker": "NVEC"}

    entry = create_public_home_entry(
        "breakout_lead_chart",
        payload,
        saved_at=now,
        parameters=parameters,
    )

    assert entry["parameters"] == {
        "ticker": "NVEC",
        "range": "1d",
        "adjustment": "raw",
    }
    with pytest.raises(
        ValueError,
        match="payload does not match its parameters",
    ):
        create_public_home_entry(
            "breakout_lead_chart",
            {**payload, "ticker": "AAPL"},
            saved_at=now,
            parameters=parameters,
        )


def test_password_owner_market_signals_reads_worker_snapshot_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_public_home_snapshot(
        get_data_paths().public_home_snapshot,
        _entries(now),
        now=now,
    )
    private_config = signals.get_personal_config()
    password_config = private_config.model_copy(
        update={
            "access": private_config.access.model_copy(
                update={"mode": "password"}
            )
        }
    )
    monkeypatch.setattr(
        signals,
        "get_personal_config",
        lambda: password_config,
    )
    calls: list[str] = []

    def unexpected() -> dict:
        calls.append("provider")
        raise AssertionError("owner snapshot request called provider")

    monkeypatch.setattr(signals, "compute_market_signals", unexpected)

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await signals.market_signals()

    payload = asyncio.run(scenario())

    assert payload["_cached"] is True
    assert payload["snapshot_source"] == "worker"
    assert payload["signals"]["rsi14"]["value"] == 50.0
    assert calls == []


def test_worker_publishes_exact_breakout_lead_chart_as_optional_resource(
    tmp_path: Path,
) -> None:
    now = _regular_time()
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, _entries(now), now=now)
    _seed_watchlist(path, now)
    calls: list[dict] = []

    async def build(parameters: dict) -> dict:
        calls.append(parameters)
        return {
            **_payload("focus_chart", now),
            "ticker": parameters["ticker"],
        }

    task = PublicHomeTask(
        _task_config(),
        builders={"breakout_lead_chart": build},
        snapshot_path=path,
        breakout_lead_ticker_reader=lambda: "NVEC",
        clock=lambda: now,
    )
    result = asyncio.run(task())
    stored = read_public_home_entries(path, now=now)

    assert result.status == "idle"
    assert result.details["refreshed"] == ["breakout_lead_chart"]
    assert "breakout_lead_chart" in result.details["available"]
    assert calls == [breakout_lead_chart_parameters("NVEC")]
    assert stored["breakout_lead_chart"]["parameters"] == calls[0]
    assert stored["breakout_lead_chart"]["payload"]["ticker"] == "NVEC"


def test_anonymous_breakout_lead_chart_reads_disk_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    path = get_data_paths().public_home_snapshot
    parameters = breakout_lead_chart_parameters("NVEC")
    entries = _entries(now)
    entries["breakout_lead_chart"] = create_public_home_entry(
        "breakout_lead_chart",
        {**_payload("focus_chart", now), "ticker": "NVEC"},
        saved_at=now,
        parameters=parameters,
    )
    write_public_home_snapshot(path, entries, now=now)
    stocks._endpoint_cache.clear()
    calls: list[str] = []

    async def unexpected(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("anonymous chart request called provider")

    monkeypatch.setattr(stocks, "_stock_chart_impl", unexpected)

    async def scenario() -> tuple[dict, int]:
        with request_owner_access_context(False):
            payload = await stocks.stock_chart("NVEC", "1d", "raw")
            with pytest.raises(HTTPException) as exc_info:
                await stocks.stock_chart("AAPL", "1d", "raw")
            return payload, exc_info.value.status_code

    payload, missing_status = asyncio.run(scenario())

    assert payload["ticker"] == "NVEC"
    assert payload["_stale"] is True
    assert missing_status == 503
    assert calls == []


def test_frontend_default_focus_contract_matches_worker_snapshot() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "frontend-src"
        / "src"
        / "api"
        / "modules"
        / "stocks.ts"
    ).read_text(encoding="utf-8")
    # React 前端直接使用后端真实 K 线周期；默认日线与 worker 快照一致。
    assert "range: StockChart['range'] = '1d'" in source
    assert "range=${range}&adjustment=raw" in source
    assert "CHART_RANGE_MAP" not in source
    assert "adjustment = 'raw'" in source
    assert public_home_resource_parameters("focus_overview", now=time.time()) == {
        "ticker": "NVDA"
    }
    assert public_home_resource_parameters("focus_chart", now=time.time()) == {
        "ticker": "NVDA",
        "range": "1d",
        "adjustment": "raw",
    }
