from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.services.breakouts.config import BreakoutSettings
from app.services.strength import scanner


def _history(*, slope: float, offset: float = 0.0, size: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-10", periods=size)
    step = np.arange(size, dtype=float)
    close = 40.0 + offset + step * slope + np.sin(step / 9.0)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": 1_500_000.0 + step * 1_000.0,
        },
        index=index,
    )


def _panel() -> pd.DataFrame:
    panel = pd.concat(
        {
            "AAA": _history(slope=0.18),
            "BBB": _history(slope=0.11, offset=5.0),
            "CCC": _history(slope=0.04, offset=10.0),
            "SPY": _history(slope=0.08, offset=20.0),
        },
        axis=1,
    )
    panel.attrs["price_source"] = {"provider": "fixture", "status": "active"}
    return panel


def _install_scan_fixtures(monkeypatch) -> list[tuple[list[str], str]]:
    requested: list[tuple[list[str], str]] = []
    metadata = {
        "AAA": {
            "sector_id": "software",
            "sector_name": "软件",
            "primary_sector_id": "software",
            "primary_sector_name": "软件",
            "theme_ids": ["software", "ai_cloud"],
            "theme_names": ["软件", "AI 与云"],
        },
        "BBB": {
            "sector_id": "software",
            "sector_name": "软件",
            "primary_sector_id": "software",
            "primary_sector_name": "软件",
            "theme_ids": ["software"],
            "theme_names": ["软件"],
        },
        "CCC": {
            "sector_id": "energy",
            "sector_name": "能源",
            "primary_sector_id": "energy",
            "primary_sector_name": "能源",
            "theme_ids": ["energy"],
            "theme_names": ["能源"],
        },
    }
    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (["AAA", "BBB", "CCC"], deepcopy(metadata)),
    )

    def download(symbols, period="1y"):
        requested.append((list(symbols), period))
        return _panel()

    monkeypatch.setattr(scanner, "_download_history", download)
    monkeypatch.setattr(
        scanner,
        "compute_market_regime",
        lambda _frames, **_kwargs: {
            "status": "active",
            "score": 70.0,
            "confidence": 1.0,
            "risk_on_spread_score": 65.0,
            "market_context": {},
            "spread_matrix": {},
        },
    )
    monkeypatch.setattr(
        "app.services.breakouts.config.get_breakout_settings",
        lambda: BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow"),
    )
    def enrich_options(rows, display_top):
        del display_top
        for row in rows:
            row["option_heat_score"] = 99.0
        return {"status": "active", "enriched": len(rows)}

    monkeypatch.setattr(scanner, "enrich_rows_with_yahoo_options", enrich_options)
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_finnhub",
        lambda rows: {"status": "skipped", "enriched": 0},
    )
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_marketdata_options",
        lambda rows: {"status": "skipped", "enriched": 0},
    )
    return requested


def _scan(**overrides):
    params = {
        "universe": "themes",
        "timeframe": "all",
        "profile": "balanced",
        "top": 3,
        "sector_id": None,
        "min_price": 0,
        "min_avg_dollar_volume": 0,
        "include_options": False,
    }
    params.update(overrides)
    return scanner._scan_sync(**params)


def test_sector_top_and_option_views_do_not_change_intrinsic_or_canonical_ranks(monkeypatch) -> None:
    requested = _install_scan_fixtures(monkeypatch)
    full = _scan()
    sector = _scan(sector_id="software")
    option_enriched = _scan(include_options=True)
    top_one = _scan(top=1)

    full_by_ticker = {row["ticker"]: row for row in full["rows"]}
    sector_by_ticker = {row["ticker"]: row for row in sector["rows"]}
    option_by_ticker = {row["ticker"]: row for row in option_enriched["rows"]}
    for ticker in ("AAA", "BBB"):
        assert sector_by_ticker[ticker]["intrinsic_score"] == full_by_ticker[ticker]["intrinsic_score"]
        assert sector_by_ticker[ticker]["global_rank_percentile"] == full_by_ticker[ticker]["global_rank_percentile"]
        assert sector_by_ticker[ticker]["sector_rank_percentile"] == full_by_ticker[ticker]["sector_rank_percentile"]
        assert option_by_ticker[ticker]["intrinsic_score"] == full_by_ticker[ticker]["intrinsic_score"]

    leader = top_one["rows"][0]["ticker"]
    assert top_one["rows"][0]["intrinsic_score"] == full_by_ticker[leader]["intrinsic_score"]
    assert all(
        {"AAA", "BBB", "CCC"}.issubset(set(symbols))
        for symbols, _period in requested
    )
    assert {period for _symbols, period in requested} == {"2y"}
    assert all(row["score_scope"] == "ranking" for row in full["rows"])
    assert all(row["selected_view_rank"] >= 1 for row in full["rows"])


def test_market_and_profile_layers_are_separate_from_intrinsic(monkeypatch) -> None:
    _install_scan_fixtures(monkeypatch)
    balanced = _scan(profile="balanced")
    aggressive = _scan(profile="aggressive")
    for row in balanced["rows"]:
        other = next(item for item in aggressive["rows"] if item["ticker"] == row["ticker"])
        assert other["intrinsic_score"] == row["intrinsic_score"]
        assert other["score_version"] == row["score_version"] == "strength-v2"
        assert row["final_score"] == row["ranking_score"]
        assert row["strength_score"] == row["ranking_score"]


def test_shadow_range_weight_configuration_cannot_change_production_intrinsic() -> None:
    hist = _history(slope=0.15)
    raw = scanner._feature_row(
        "AAA",
        hist,
        _history(slope=0.08),
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert raw is not None
    feature = {
        "status": "active",
        "version": "range-fixture",
        "range_persistence_normalized_score": 90.0,
        "range_persistence_slope_5d": 4.0,
        "range_persistence_ratio_10d": 95.0,
    }
    low_weight = scanner._intrinsic_row(
        raw,
        hist,
        range_feature=feature,
        range_mode="shadow",
        range_trend_weight=0.01,
    )
    high_weight = scanner._intrinsic_row(
        raw,
        hist,
        range_feature=feature,
        range_mode="shadow",
        range_trend_weight=0.15,
    )
    assert low_weight["intrinsic_score"] == high_weight["intrinsic_score"]
    assert low_weight["contributions"] == high_weight["contributions"]
    assert low_weight["range_persistence_shadow"]["production_score"] == high_weight["range_persistence_shadow"]["production_score"]
    for row in (low_weight, high_weight):
        trend = row["factor_breakdown"]["trend_family"]
        assert trend["applied_effective_weights"] == trend["production_effective_weights"]


def test_scan_and_explicit_ticker_set_share_the_same_intrinsic_engine(monkeypatch) -> None:
    requested = _install_scan_fixtures(monkeypatch)
    scan_payload = _scan()
    explicit = scanner._score_ticker_set_sync(
        ["AAA"],
        as_of=datetime.now(timezone.utc),
        range_mode="shadow",
    )
    scanned = next(row for row in scan_payload["rows"] if row["ticker"] == "AAA")
    assert explicit["rows"][0]["intrinsic_score"] == scanned["intrinsic_score"]
    assert explicit["rows"][0]["factor_breakdown"] == scanned["factor_breakdown"]
    assert len(requested) == 2
    assert requested[0][1] == requested[1][1] == "2y"


def test_view_variants_share_the_same_two_year_history_download(monkeypatch) -> None:
    requested: list[tuple[list[str], str]] = []
    scan_calls: list[tuple[int, pd.DataFrame | None]] = []
    panel = _panel()
    metadata = {
        "AAA": {
            "sector_id": "software",
            "sector_name": "软件",
            "primary_sector_id": "software",
            "primary_sector_name": "软件",
            "theme_ids": ["software"],
            "theme_names": ["软件"],
        }
    }

    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (["AAA"], deepcopy(metadata)),
    )

    def download(symbols, period="1y"):
        requested.append((list(symbols), period))
        return panel

    def fake_scan_sync(*, top, raw_history=None, **_kwargs):
        scan_calls.append((top, raw_history))
        return {
            "as_of": "2026-07-10T20:00:00+00:00",
            "sectors": [],
            "market_regime": {},
            "rows": [],
            "results": [],
        }

    monkeypatch.setattr(scanner, "_download_history", download)
    monkeypatch.setattr(scanner, "_scan_sync", fake_scan_sync)
    scanner.cache.clear()
    try:
        asyncio.run(scanner.scan_strength(top=1, include_options=False))
        asyncio.run(scanner.scan_strength(top=2, include_options=False))
    finally:
        scanner.cache.clear()

    assert [top for top, _history in scan_calls] == [1, 2]
    assert all(history is panel for _top, history in scan_calls)
    assert len(requested) == 1
    assert requested[0][1] == "2y"


# ---------------- tier distribution covers the pool, not the slice ----------------


def test_tier_distribution_counts_every_screened_row_not_just_top_n() -> None:
    """The screener's S/A/B/C/D counts have to describe the candidate pool.

    Counting the returned rows describes only the slice that was asked for,
    which is why a pool of 300 could show five tiers adding up to 20 while the
    real pool size was displayed right beside them (audit P2-10).
    """

    rows = [
        {"ticker": "AAA", "final_score": 95.0},
        {"ticker": "BBB", "final_score": 90.0},
        {"ticker": "CCC", "final_score": 84.0},
        {"ticker": "DDD", "final_score": 80.0},
        {"ticker": "EEE", "final_score": 70.0},
        {"ticker": "FFF", "final_score": 61.0},
        {"ticker": "GGG", "final_score": 12.0},
        {"ticker": "HHH", "final_score": None},
    ]

    distribution = scanner._tier_distribution(rows, "all")

    assert distribution["S"] == 2, "90 is the S floor, not the top of A"
    assert distribution["A"] == 2
    assert distribution["B"] == 1
    assert distribution["C"] == 1
    assert distribution["D"] == 1
    # A missing score is not a D-tier stock; folding it in would understate the
    # weak tier and overstate it at the same time.
    assert distribution["unscored"] == 1
    assert distribution["scored"] == 7
    assert distribution["total"] == len(rows)
    assert (
        distribution["S"]
        + distribution["A"]
        + distribution["B"]
        + distribution["C"]
        + distribution["D"]
        + distribution["unscored"]
        == len(rows)
    )


def test_tier_distribution_follows_the_requested_timeframe() -> None:
    rows = [
        {"ticker": "AAA", "score_short": 92.0, "final_score": 20.0},
        {"ticker": "BBB", "score_short": None, "final_score": 88.0},
    ]

    short = scanner._tier_distribution(rows, "short")
    assert short["S"] == 1, "the short-timeframe score decides the tier"
    # A row without the timeframe score still has an overall score to fall back
    # on; that is a real value, not a guess.
    assert short["A"] == 1
    assert short["unscored"] == 0

    overall = scanner._tier_distribution(rows, "all")
    assert overall["D"] == 1 and overall["A"] == 1


def test_tier_floors_match_the_screener_ui() -> None:
    """Two copies of the same thresholds is exactly how these drift apart."""

    ui = (
        Path(__file__).resolve().parents[1]
        / "frontend-src"
        / "src"
        / "components"
        / "screener"
        / "types.ts"
    ).read_text(encoding="utf-8")
    body = ui.split("export function tierOf", 1)[1].split("}", 1)[0]
    for name, floor in scanner._TIER_FLOORS:
        assert f"score >= {int(floor)}) return '{name}'" in body, (
            f"{name} floor {int(floor)} is not what tierOf uses; "
            "the backend distribution and the UI would classify differently"
        )
