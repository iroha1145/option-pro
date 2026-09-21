from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.api import strength
from app.services.eod_limited import context_snapshot as context
from app.services.eod_limited import worker
from app.services.eod_limited.store import read_batch

NOW = datetime(2026, 9, 18, 23, tzinfo=timezone.utc)


def _context_payload(session: str = "2026-09-18") -> dict:
    return {
        "as_of": NOW.isoformat(), "served_session": session,
        "market_regime": {"score": 61, "status": "active"}, "source_status": "active",
        "sectors": [{
            "sector_id": "semiconductors", "name": "半导体", "count": 14,
            "avg_return_1mo": 1.0, "avg_return_3mo": 2.0, "avg_return_6mo": 3.0,
            "macro_sector_fit": 62, "avg_strength": 99, "leaders": [{"ticker": "OLD", "score": 99}],
        }],
    }


def _selection() -> dict:
    return {
        "as_of": "2026-09-18", "score_data_through": "2026-09-18",
        "source_status": "active", "_stale": False, "score_version": "limited-all-market-v1.3",
        "observation_rows": [{"ticker": "NVDA", "score": 72.0, "strength_score": 72.0}],
        "theme_statistics": {
            "status": "active", "served_session": "2026-09-18",
            "compute_version": "limited-all-market-v1.3",
            "reference_profile": "balanced", "reference_horizon": "mid",
            "reference_family": "A_trend_quality",
            "sectors": [
                {"sector_id": sid, "member_count": 14, "scored_count": 1,
                 "avg_strength": 72.0, "leaders": [{"ticker": "NVDA", "score": 72.0}],
                 "missing_reasons": {"SCORE_UNAVAILABLE": 13},
                 "score_source_status": "degraded", "avg_return_3mo": 2.0,
                 "spy_return_3mo": 1.0, "excess_vs_spy_3mo": 1.0}
                for sid in ("semiconductors", "ai_cloud")
            ],
        },
    }


def test_context_build_uses_descriptive_data_without_old_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.research_eod_v1.fixtures import trading_days_ending
    from app.services.strength import scanner, scoring
    from app.services.strength.market_regime import MARKET_BENCHMARKS
    from app.services.strength.features import _ret

    days = trading_days_ending(NOW.date(), 380)
    close = 100 + np.arange(len(days)) * 0.1 + np.sin(np.arange(len(days)) / 7)
    frames = {
        symbol: pd.DataFrame({
            "Open": close, "High": close + 1, "Low": close - 1, "Close": close,
            "Volume": np.full(len(days), 2_000_000),
        }, index=pd.to_datetime(days))
        for symbol in ["NVDA", *MARKET_BENCHMARKS]
    }
    raw = pd.concat(frames, axis=1)
    requested = []

    def download(symbols, period):
        requested.append((symbols, period))
        return raw

    def forbidden(*args, **kwargs):
        raise AssertionError("old ranking must not run")

    monkeypatch.setattr(scanner, "_theme_universe", lambda: (["NVDA"], {"NVDA": {"theme_ids": ["semiconductors", "ai_cloud"]}}))
    monkeypatch.setattr(scanner, "_download_history", download)
    monkeypatch.setattr(scanner, "scan_strength", forbidden)
    monkeypatch.setattr(scanner, "_score_rows", forbidden)
    monkeypatch.setattr(scanner, "_intrinsic_row", forbidden)
    monkeypatch.setattr(scanner, "score_intrinsic", forbidden)
    monkeypatch.setattr(scoring, "score_intrinsic", forbidden)
    fit = SimpleNamespace(score=62, tailwind="supportive", confidence=.8, supporting=[], opposing=[])
    monkeypatch.setattr(scanner, "_load_macro_reader", lambda: SimpleNamespace(available=True, fit_for=lambda sid: fit))
    payload = context.build_context_snapshot(as_of=NOW)
    assert set(requested[0][0]) == {"NVDA", *MARKET_BENCHMARKS}
    assert requested[0][1] == "2y"
    assert payload["market_regime"]["score"] is not None
    assert payload["market_regime"]["ranking_adjustments_applied"] is False
    assert "rules" not in payload["market_regime"]
    sectors = {row["sector_id"]: row for row in payload["sectors"]}
    assert set(sectors) == {"semiconductors", "ai_cloud"}
    for row in sectors.values():
        assert row["avg_return_3mo"] == round(_ret(frames["NVDA"]["Close"], 63) * 100, 2)
        assert row["macro_sector_fit"] == 62
        assert "avg_strength" not in row and "leaders" not in row


def test_context_refresh_reuses_same_session_and_retains_previous_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def build(*, as_of):
        calls.append(as_of)
        return _context_payload()

    monkeypatch.setattr(context, "build_context_snapshot", build)
    assert context.refresh_context_snapshot(root=tmp_path, now=NOW)["status"] == "RAN"
    original = context.context_path(tmp_path).read_bytes()
    assert context.refresh_context_snapshot(root=tmp_path, now=NOW)["status"] == "CURRENT"
    assert len(calls) == 1

    def fail(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(context, "build_context_snapshot", fail)
    later = datetime(2026, 9, 21, 23, tzinfo=timezone.utc)
    outcome = context.refresh_context_snapshot(root=tmp_path, now=later)
    assert outcome["status"] == "UNAVAILABLE"
    assert context.context_path(tmp_path).read_bytes() == original
    stale = context.read_context_snapshot(root=tmp_path, now=later)
    assert stale["market_regime"]["score"] == 61
    assert stale["_stale"] is True and stale["source_status"] == "stale"
    assert stale["stale_reason"] == "newer_session_available"


def test_context_incomplete_market_does_not_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.strength import scanner

    monkeypatch.setattr(scanner, "_download_history", lambda *args, **kwargs: pd.DataFrame())
    outcome = context.refresh_context_snapshot(root=tmp_path, now=NOW)
    assert outcome["status"] == "UNAVAILABLE"
    assert not context.context_path(tmp_path).exists()


def test_eod_context_hook_defaults_to_real_capture_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.eod_limited.bars import _frame_to_bars
    from app.services.eod_limited import market_data

    calls = []
    monkeypatch.setattr(context, "refresh_context_snapshot", lambda **kwargs: calls.append(kwargs) or {"status": "RAN"})
    panel = worker.build_synthetic_panel(sessions=5, end=NOW.date())
    params = dict(session=NOW.date(), themes=["semiconductors"], algorithms=["A_trend_quality"])
    worker.run_eod_limited_job(**params, panel=panel, root=tmp_path / "injected")
    assert calls == []
    frame = pd.DataFrame({"Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1_000_000]}, index=pd.to_datetime([NOW.date()]))
    loaded_panel, coverage = worker.bars_to_panel({"NVDA": _frame_to_bars("NVDA", frame)}, {"NVDA": ["semiconductors"]}, end=NOW.date())
    monkeypatch.setattr(market_data, "load_all_market_panel", lambda **kwargs: (loaded_panel, coverage, {
        "status": "complete", "eligible_count": 1, "complete_bar_count": 1,
        "volume_session_scope": market_data.VOLUME_SCOPE,
    }))
    outcome = worker.run_eod_limited_job(session=NOW.date(), root=tmp_path / "real")
    assert outcome["status"] == "RAN" and len(calls) == 1
    worker.run_eod_limited_job(session=NOW.date(), root=tmp_path / "isolated", refresh_context=False)
    assert len(calls) == 1


def test_context_failure_does_not_erase_ranking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**kwargs):
        raise RuntimeError("context failed")

    monkeypatch.setattr(context, "refresh_context_snapshot", fail)
    outcome = worker.run_eod_limited_job(
        session=NOW.date(), panel=worker.build_synthetic_panel(sessions=5, end=NOW.date()), root=tmp_path,
        refresh_context=True, themes=["semiconductors"], algorithms=["A_trend_quality"],
    )
    assert outcome["status"] == "RAN"
    assert outcome["context"]["status"] == "UNAVAILABLE"
    assert read_batch(tmp_path)["served_session"] == "2026-09-18"


@pytest.mark.parametrize("mode", ["password", "private_network"])
def test_all_access_modes_read_eod_scores_and_independent_context(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(strength, "current_request_is_owner", lambda: True)
    monkeypatch.setattr(strength, "get_personal_config", lambda: SimpleNamespace(access=SimpleNamespace(mode=mode)))
    monkeypatch.setattr(context, "read_context_snapshot", lambda: {**_context_payload(), "_stale": False})

    async def selection(**kwargs):
        return _selection()

    async def forbidden(*args, **kwargs):
        raise AssertionError("no live old ranking or market download in GET")

    monkeypatch.setattr(strength, "_public_strength_snapshot", selection)
    for name in ("stock_strength", "sector_strength", "market_strength"):
        monkeypatch.setattr(strength, name, forbidden)
    stock = asyncio.run(strength.stock("NVDA", profile="balanced"))
    assert stock["row"]["score"] == 72
    sectors = asyncio.run(strength.sectors(period="3mo"))
    rows = {row["sector_id"]: row for row in sectors["sectors"]}
    assert rows["semiconductors"]["avg_strength"] == 72
    assert rows["semiconductors"]["score_basis"] == "full_theme_balanced_mid_A"
    assert rows["semiconductors"]["excess_return"] == 1.0
    assert rows["semiconductors"]["macro_sector_fit"] == 62
    assert rows["semiconductors"]["avg_return"] == 2
    assert rows["semiconductors"]["scored_count"] == rows["ai_cloud"]["scored_count"] == 1
    assert rows["software"]["avg_strength"] is None
    assert rows["software"]["leaders"] == []
    assert rows["semiconductors"]["member_count"] > 1
    assert asyncio.run(strength.market())["market_regime"]["score"] == 61


def test_market_and_sector_returns_survive_missing_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(**kwargs):
        raise strength.HTTPException(status_code=503, detail="no selection")

    monkeypatch.setattr(strength, "_public_strength_snapshot", missing)
    monkeypatch.setattr(context, "read_context_snapshot", lambda: {**_context_payload(), "_stale": True, "source_status": "stale", "stale_reason": "newer_session_available"})
    market = asyncio.run(strength.market())
    assert market["_stale"] and market["source_status"] == "stale"
    sectors = asyncio.run(strength.sectors(period="3mo"))
    semiconductor = next(row for row in sectors["sectors"] if row["sector_id"] == "semiconductors")
    assert semiconductor["avg_return"] == 2
    assert semiconductor["avg_strength"] is None and semiconductor["scored_count"] is None
    assert semiconductor["score_source_status"] == "unavailable"


def test_public_selection_helper_uses_new_mid_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    async def snapshot(**kwargs):
        captured.append(kwargs)
        return _selection(), 1.0, False

    monkeypatch.setattr(strength, "_scan_snapshot_payload", snapshot)
    asyncio.run(strength._public_strength_snapshot(profile="aggressive"))
    assert captured[0]["ranking_algorithm"] == "eod_limited_v1"
    assert captured[0]["timeframe"] == "mid" and captured[0]["profile"] == "aggressive"


def test_macro_read_overlay_updates_same_price_session_without_mutation_or_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from app.services.macro_conditions import linkage_reader
    from app.services.macro_conditions.linkage import MacroFit
    from app.services.strength import scanner

    document = {"version": 1, "published_at": NOW.timestamp(), "payload": _context_payload()}
    context._atomic_write(context.context_path(tmp_path), document)
    original_bytes = context.context_path(tmp_path).read_bytes()
    cached = context._documents.read(context.context_path(tmp_path), context._parse_document)
    cached_before = json.dumps(cached, sort_keys=True)
    readers = [
        linkage_reader.MacroFitReader(
            available=True, snapshot_date="2026-09-17", scoring_version="same-version",
            available_at=f"2026-09-18T{hour}:00:00+00:00",
            _fits={"semiconductors": MacroFit(score, .75, "exposure-test", "顺风", ("test-support",), (), 1.0)},
        )
        for score, hour in [(65.0, "20"), (73.0, "22")]
    ]
    calls = []

    def load():
        calls.append(True)
        return readers.pop(0)

    def forbidden(*args, **kwargs):
        raise AssertionError("macro read must not write, download, or run ranking")

    monkeypatch.setattr(linkage_reader, "load_macro_fit_reader", load)
    monkeypatch.setattr(context, "_atomic_write", forbidden)
    monkeypatch.setattr(context, "build_context_snapshot", forbidden)
    monkeypatch.setattr(scanner, "_download_history", forbidden)
    monkeypatch.setattr(scanner, "scan_strength", forbidden)
    first = context.read_context_snapshot(root=tmp_path, now=NOW)
    second = context.read_context_snapshot(root=tmp_path, now=NOW)
    assert len(calls) == 2
    assert first["sectors"][0]["macro_sector_fit"] == 65
    assert second["sectors"][0]["macro_sector_fit"] == 73
    assert first["macro_context"]["macro_snapshot_date"] == second["macro_context"]["macro_snapshot_date"] == "2026-09-17"
    assert first["macro_context"]["macro_scoring_version"] == second["macro_context"]["macro_scoring_version"]
    assert first["macro_context"]["macro_available_at"] != second["macro_context"]["macro_available_at"]
    for payload in (first, second):
        assert payload["as_of"] == document["payload"]["as_of"]
        assert payload["served_session"] == document["payload"]["served_session"]
        assert payload["snapshot_saved_at"] == NOW.isoformat()
        assert payload["sectors"][0] is not cached["payload"]["sectors"][0]
        assert "fresh" not in payload["macro_context"]
    assert context.context_path(tmp_path).read_bytes() == original_bytes
    assert json.dumps(cached, sort_keys=True) == cached_before


def test_unavailable_macro_replaces_seed_values_with_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.macro_conditions import linkage_reader

    context._atomic_write(context.context_path(tmp_path), {
        "version": 1, "published_at": NOW.timestamp(), "payload": _context_payload(),
    })
    monkeypatch.setattr(linkage_reader, "load_macro_fit_reader", lambda: linkage_reader.unavailable_reader("macro_snapshot_unavailable"))
    result = context.read_context_snapshot(root=tmp_path, now=NOW)
    assert result["sectors"][0]["macro_sector_fit"] is None
    assert result["sectors"][0]["macro_sector_fit_confidence"] is None
    assert result["sectors"][0]["macro_sector_supporting_factors"] == []
    assert result["macro_context"]["available"] is False
    assert result["macro_context"]["reason"] == "macro_snapshot_unavailable"
    assert result["source_status"] == "active"  # Independent price freshness.


def test_sector_endpoint_exposes_independent_macro_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    provenance = {"available": True, "reason": None, "macro_snapshot_date": "2026-09-17", "macro_available_at": "2026-09-18T22:00:00+00:00"}
    monkeypatch.setattr(context, "read_context_snapshot", lambda: {**_context_payload(), "macro_context": provenance})

    async def selection(**kwargs):
        return _selection()

    monkeypatch.setattr(strength, "_public_strength_snapshot", selection)
    result = asyncio.run(strength.sectors(period="3mo"))
    assert result["macro_context"] == provenance


def test_descriptive_regime_keeps_dimensions_without_old_ranking_claims() -> None:
    from copy import deepcopy

    original = {
        "score": 51.0, "market_breadth_score": 39.0, "risk_appetite_score": 44.0,
        "input_coverage": {"score": .9, "groups": {"trend": True}},
        "market_context": {"status": "degraded", "breadth_score": 39.0},
        "rules": {"breakout_weight_multiplier": .75, "option_heat_weight_multiplier": .8},
        "warnings": [
            "市场宽度偏弱，突破型信号已降权",
            "波动或信用压力偏高，期权热度已降权",
            "风险偏好价差偏弱，突破与期权信号已降权",
            "可选市场数据不完整：credit",
        ],
    }
    before = deepcopy(original)
    descriptive = context._descriptive_market_regime(original)
    assert original == before
    assert "rules" not in descriptive
    assert descriptive["ranking_adjustments_applied"] is False
    assert descriptive["warnings"] == [
        "市场宽度偏弱", "波动或信用压力偏高", "风险偏好价差偏弱", "可选市场数据不完整：credit",
    ]
    for key in set(original) - {"rules", "warnings"}:
        assert descriptive[key] == original[key]


def test_reading_old_seed_removes_ranking_claims_without_rewriting_file(tmp_path: Path) -> None:
    payload = _context_payload()
    payload["market_regime"].update({
        "rules": {"momentum_weight_multiplier": .9},
        "warnings": ["市场宽度偏弱，突破型信号已降权"],
    })
    context._atomic_write(context.context_path(tmp_path), {"version": 1, "published_at": NOW.timestamp(), "payload": payload})
    before = context.context_path(tmp_path).read_bytes()
    result = context.read_context_snapshot(root=tmp_path, now=NOW)
    assert result["market_regime"]["warnings"] == ["市场宽度偏弱"]
    assert result["market_regime"]["ranking_adjustments_applied"] is False
    assert "rules" not in result["market_regime"]
    assert result["market_regime"]["score"] == 61
    assert context.context_path(tmp_path).read_bytes() == before
    cached = context._documents.read(context.context_path(tmp_path), context._parse_document)
    assert cached["payload"]["market_regime"]["rules"] == {"momentum_weight_multiplier": .9}
