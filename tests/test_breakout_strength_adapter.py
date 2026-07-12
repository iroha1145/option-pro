from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import app.services.breakouts.adapters.strength as strength_adapter_module
from app.services.breakouts.adapters.strength import ExistingStrengthAdapter
from app.services.breakouts.config import BreakoutSettings
from app.services.strength import scanner


def _history(offset: float = 0.0, size: int = 420) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=size, freq="B")
    close = 50.0 + offset + np.arange(size) * 0.12 + np.sin(np.arange(size) / 6)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 2_000_000 + np.arange(size) * 1000,
        },
        index=index,
    )


def _panel() -> pd.DataFrame:
    panel = pd.concat(
        {
            "AAA": _history(),
            "BBB": _history(10),
            "SPY": _history(-5),
        },
        axis=1,
    )
    panel.attrs["price_source"] = {"provider": "fixture", "status": "active"}
    return panel


def test_explicit_ticker_set_score_is_invariant_to_candidate_set(monkeypatch) -> None:
    panel = _panel()
    monkeypatch.setattr(scanner, "_download_history", lambda _tickers, period="2y": panel)
    as_of = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

    single = asyncio.run(
        scanner.score_ticker_set(["AAA"], as_of=as_of, range_mode="shadow")
    )
    combined = asyncio.run(
        scanner.score_ticker_set(["BBB", "AAA"], as_of=as_of, range_mode="shadow")
    )
    first = single["rows"][0]
    second = next(row for row in combined["rows"] if row["ticker"] == "AAA")
    assert first["score"] == second["score"]
    assert first["score_scope"] == "intrinsic"
    assert first["market_regime_score"] is None
    assert first["sector_score"] is None
    assert first["option_context"]["status"] == "skipped"
    assert first["range_persistence_shadow"]["production_unchanged"] is True


def test_explicit_scoring_rejects_option_enrichment(monkeypatch) -> None:
    monkeypatch.setattr(scanner, "_download_history", lambda _tickers, period="2y": _panel())
    with pytest.raises(ValueError, match="option"):
        asyncio.run(scanner.score_ticker_set(["AAA"], include_options=True))


def test_strength_adapter_preserves_intrinsic_provenance(monkeypatch) -> None:
    async def fake_score(tickers, **kwargs):
        assert kwargs["include_options"] is False
        return {
            "rows": [
                {
                    "ticker": tickers[0],
                    "score": 77.0,
                    "score_scope": "intrinsic",
                    "confidence": 0.75,
                    "score_version": "strength-intrinsic-v1",
                    "included_features": ["momentum_63d"],
                    "factor_breakdown": {},
                    "coverage": {"ratio": 0.75},
                }
            ]
        }

    monkeypatch.setattr(scanner, "score_ticker_set", fake_score)
    monkeypatch.setattr(
        strength_adapter_module,
        "get_breakout_settings",
        lambda: BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="shadow",
            RANGE_PERSISTENCE_VALIDATION_VERSION="",
        ),
    )
    as_of = datetime(2026, 7, 10, tzinfo=timezone.utc)
    result = asyncio.run(
        ExistingStrengthAdapter().score_ticker_set(["aaa"], as_of=as_of)
    )
    assert result["AAA"].score_scope == "intrinsic"
    assert result["AAA"].score == 77.0
    assert result["AAA"].included_features == ["momentum_63d"]


def test_strength_adapter_forwards_complete_range_configuration(monkeypatch) -> None:
    settings = BreakoutSettings(
        _env_file=None,
        RANGE_PERSISTENCE_MODE="shadow",
        RANGE_PERSISTENCE_VERSION="range-persistence-v2",
        RANGE_PERSISTENCE_LENGTH=21,
        RANGE_PERSISTENCE_FAST_LENGTH=4,
        RANGE_PERSISTENCE_SLOPE_DAYS=7,
        RANGE_PERSISTENCE_RATIO_WINDOW=12,
        RANGE_PERSISTENCE_RATIO_THRESHOLD=64,
        RANGE_PERSISTENCE_MIN_HISTORY_MULTIPLIER=3,
    )
    captured: dict = {}

    async def fake_score(_tickers, **kwargs):
        captured.update(kwargs)
        return {"rows": []}

    monkeypatch.setattr(strength_adapter_module, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(scanner, "score_ticker_frames", fake_score)
    as_of = datetime(2026, 7, 10, tzinfo=timezone.utc)
    asyncio.run(
        ExistingStrengthAdapter().score_from_daily_snapshots(
            ["AAA"],
            snapshots={
                "AAA": SimpleNamespace(frame=_history(), source="fixture"),
                "SPY": SimpleNamespace(frame=_history(-5), source="fixture"),
            },
            as_of=as_of,
            range_mode="shadow",
            range_trend_weight=0.08,
            range_final_cap=0.02,
        )
    )

    assert captured["range_mode"] == "shadow"
    assert captured["range_version"] == "range-persistence-v2"
    assert captured["range_length"] == 21
    assert captured["range_fast_length"] == 4
    assert captured["range_slope_days"] == 7
    assert captured["range_ratio_window"] == 12
    assert captured["range_ratio_threshold"] == 64
    assert captured["range_min_history_multiplier"] == 3
    assert captured["range_trend_weight"] == 0.08
    assert captured["range_final_cap"] == 0.02


def test_frame_scoring_uses_every_range_configuration_value(monkeypatch) -> None:
    captured: dict = {}
    original = scanner.compute_range_persistence

    def recording_compute(frame, **kwargs):
        captured.update(kwargs)
        return original(frame, **kwargs)

    monkeypatch.setattr(scanner, "compute_range_persistence", recording_compute)
    as_of = datetime(2026, 7, 10, tzinfo=timezone.utc)
    payload = asyncio.run(
        scanner.score_ticker_frames(
            ["AAA"],
            frames={"AAA": _history(), "SPY": _history(-5)},
            as_of=as_of,
            range_mode="shadow",
            range_version="range-persistence-v2",
            range_length=21,
            range_fast_length=4,
            range_slope_days=7,
            range_ratio_window=12,
            range_ratio_threshold=64,
            range_min_history_multiplier=3,
        )
    )

    assert payload["rows"][0]["range_persistence"]["version"] == "range-persistence-v2"
    assert (
        payload["rows"][0]["range_persistence_shadow"]["version"]
        == "range-persistence-v2"
    )
    assert captured.pop("cutoff") is not None
    assert captured == {
        "length": 21,
        "fast_length": 4,
        "slope_lookback": 7,
        "ratio_window": 12,
        "ratio_threshold": 64,
        "min_history_multiplier": 3,
        "version": "range-persistence-v2",
    }


def test_disabled_frame_scoring_does_not_compute_range_feature(monkeypatch) -> None:
    def forbidden_compute(*_args, **_kwargs):
        raise AssertionError("disabled range mode must not compute the feature")

    monkeypatch.setattr(scanner, "compute_range_persistence", forbidden_compute)
    as_of = datetime(2026, 7, 10, tzinfo=timezone.utc)
    payload = asyncio.run(
        scanner.score_ticker_frames(
            ["AAA"],
            frames={"AAA": _history(), "SPY": _history(-5)},
            as_of=as_of,
            range_mode="disabled",
            range_version="range-persistence-disabled-v2",
        )
    )

    assert payload["rows"][0]["range_persistence"] == {
        "status": "disabled",
        "version": "range-persistence-disabled-v2",
    }


def test_shadow_range_failure_preserves_intrinsic_score_and_rank() -> None:
    incomplete = _history()[["Close", "Volume"]]
    incomplete_spy = _history(-5)[["Close", "Volume"]]
    frames = {"AAA": incomplete, "SPY": incomplete_spy}
    as_of = datetime(2026, 7, 10, tzinfo=timezone.utc)

    baseline = asyncio.run(
        scanner.score_ticker_frames(
            ["AAA"],
            frames=frames,
            as_of=as_of,
            range_mode="disabled",
        )
    )
    shadow = asyncio.run(
        scanner.score_ticker_frames(
            ["AAA"],
            frames=frames,
            as_of=as_of,
            range_mode="shadow",
        )
    )

    assert [row["ticker"] for row in shadow["rows"]] == ["AAA"]
    assert shadow["rows"][0]["score"] == baseline["rows"][0]["score"]
    assert shadow["rows"][0]["final_score"] == baseline["rows"][0]["final_score"]
    assert shadow["rows"][0]["range_persistence"]["status"] == "unavailable"
    assert shadow["rows"][0]["range_persistence_shadow"]["production_unchanged"] is True


def test_range_component_uses_three_inputs_and_actual_final_weight_is_capped() -> None:
    row = {
        "ticker": "AAA",
        "return_20d": 0.08,
        "return_63d": 0.15,
        "rs_spy_63d": 0.05,
        "price_action": {"status": "unavailable"},
    }
    feature = {
        "status": "active",
        "range_persistence": 80,
        "range_persistence_normalized_score": 85,
        "range_persistence_slope_5d": 3,
        "range_persistence_ratio_10d": 90,
    }
    shadow = scanner._intrinsic_row(
        row,
        _history(),
        range_feature=feature,
        range_mode="shadow",
    )
    enabled = scanner._intrinsic_row(
        row,
        _history(),
        range_feature=feature,
        range_mode="enabled",
        range_final_cap=0.02,
    )
    audit = enabled["factor_breakdown"]
    subcomponents = audit["trend_family"]["range_persistence_subcomponents"]
    assert set(subcomponents["components"]) == {
        "persistence_level",
        "slope_5d",
        "ratio_10d",
    }
    assert enabled["range_persistence_shadow"]["effective_weight"] <= 0.02
    assert shadow["score"] == round(
        shadow["range_persistence_shadow"]["production_score"], 1
    )
    assert enabled["score"] == round(
        enabled["range_persistence_shadow"]["hypothetical_score"], 1
    )
    assert abs(sum(audit["contributions"].values()) - enabled["score"]) <= 0.1
    assert "momentum_20d" in enabled["included_features"]
    assert "medium_term_momentum_63d" in enabled["included_features"]


def test_explicit_ticker_set_daily_cache_avoids_duplicate_downloads(monkeypatch) -> None:
    scanner.cache.clear()
    calls = 0

    def download(_tickers, period="2y"):
        nonlocal calls
        calls += 1
        return _panel()

    monkeypatch.setattr(scanner, "_download_history", download)
    as_of = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    first = asyncio.run(
        scanner.score_ticker_set(["AAA"], as_of=as_of, range_mode="shadow")
    )
    second = asyncio.run(
        scanner.score_ticker_set(["AAA"], as_of=as_of, range_mode="shadow")
    )
    changed = asyncio.run(
        scanner.score_ticker_set(
            ["AAA"],
            as_of=as_of,
            range_mode="shadow",
            range_length=36,
        )
    )
    assert calls == 2
    assert first["_cached"] is False
    assert second["_cached"] is True
    assert changed["_cached"] is False
    scanner.cache.clear()


def test_public_single_stock_lookup_preserves_profile_and_market_semantics(monkeypatch) -> None:
    async def fake_scan_strength(**kwargs):
        assert kwargs["profile"] == "aggressive"
        assert kwargs["top"] == 250
        assert kwargs["include_options"] is False
        return {
            "as_of": "2026-07-10T12:00:00+00:00",
            "rows": [{"ticker": "AAA", "final_score": 88}],
            "market_regime": {"status": "active", "score": 72},
        }

    monkeypatch.setattr(scanner, "scan_strength", fake_scan_strength)
    payload = asyncio.run(scanner.stock_strength("aaa", profile="aggressive"))
    assert payload["row"]["final_score"] == 88
    assert payload["market_regime"] == {"status": "active", "score": 72}
    assert "score_scope" not in payload
