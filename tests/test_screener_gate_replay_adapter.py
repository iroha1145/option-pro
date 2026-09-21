from __future__ import annotations

import sys
from copy import deepcopy
from datetime import date
from pathlib import Path

from app.services.eod_limited.inference import UNIVERSE_VERSION, precompute_session_raws, precompute_theme_raws
from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS, load_market_registry
from app.services.eod_limited.panel import prepare_limited_panel
from app.services.eod_limited.price_only import apply_price_only_track
from app.services.eod_limited.worker import build_synthetic_panel
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
from app.services.research_eod_v1.constants import ALGORITHMS
from app.services.research_eod_v1.fixtures import make_series, structured_close
from app.services.research_eod_v1.snapshot import compute_snapshot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from screener_gate_adapter_v1 import (
    B0_CURRENT,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    facts_from_raw_and_series,
    production_old_median,
    ranked_stocks,
    score_contexts,
)
from screener_gate_candidates_v1 import HIGH_ATR, build_reference, evaluate, reference_eligible


def _panel_with_extra_etfs():
    panel = build_synthetic_panel(sessions=370, end=date(2023, 7, 10))
    days = panel["NVDA"].dates
    for i, ticker in enumerate(("SLV", "BIL", "SHY", "IEI", "IEF")):
        close = structured_close(len(days), 80 + i, 0.01, 30)
        panel[ticker] = make_series(
            ticker,
            days,
            close,
            theme_ids=("etfs",),
            asset_track="etf",
            security_type="ETF",
            industry_id=None,
            parent_industry_id=None,
            volume=50_000,
        ).with_close_price_return()
    for series in panel.values():
        if series.asset_track == "stock" and ALL_MARKET_STOCKS not in series.theme_ids:
            series.theme_ids = tuple(series.theme_ids) + (ALL_MARKET_STOCKS,)
    return prepare_limited_panel(panel)


def test_adapter_facts_come_from_raw_not_watchlist():
    registry = load_market_registry()
    panel = prepare_limited_panel(build_synthetic_panel(sessions=370, end=date(2023, 7, 10)))
    session = date(2023, 7, 10)
    raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon="mid")
    facts = facts_from_raw_and_series(raws["NVDA"], clipped["NVDA"], session)
    assert facts.security_id == "NVDA"
    assert facts.session_date == "2023-07-10"
    assert facts.asset_track == "stock"
    assert facts.complete_bar is True
    assert facts.atr_pct is None or facts.atr_pct > 0.2  # percent units, not 0.04-style


def test_b0_high_atr_matches_production_row_and_does_not_mutate():
    registry = load_market_registry()
    panel = _panel_with_extra_etfs()
    session = date(2023, 7, 10)
    themes = ["semiconductors", "etfs", ALL_MARKET_STOCKS]
    raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon="mid")
    themed = precompute_theme_raws(raws, clipped, session, registry=registry, themes=themes)
    scored = score_contexts(
        registry=registry,
        session=session,
        as_of=eod_evaluation_as_of(session),
        clipped=clipped,
        raws=raws,
        themed=themed,
        themes=themes,
        families=list(ALGORITHMS),
        profile="balanced",
        horizon="mid",
        universe_version=UNIVERSE_VERSION,
    )
    mismatches = 0
    for item in scored["records"]:
        original = item["row"]
        snapshot = deepcopy(original)
        b0 = item["decisions"][B0_CURRENT]
        production = HIGH_ATR in (original.get("rejection_reasons") or ())
        if item["facts"].asset_track == "stock" and bool(production) != bool(b0.high_atr):
            mismatches += 1
        assert original == snapshot
        assert item["attached"][B0_CURRENT]["status"] == original["status"]
        assert item["attached"][B0_CURRENT]["score"] == original["score"]
        assert item["attached"][B0_CURRENT]["rejection_reasons"] == original["rejection_reasons"]
    assert mismatches == 0
    g1 = {row["security_id"] for row in ranked_stocks(scored, G1_STOCK_REFERENCE, layer="technical_entry")}
    g2 = {row["security_id"] for row in ranked_stocks(scored, G2_EXTENSION_DISCOVERY, layer="technical_entry")}
    g3 = {row["security_id"] for row in ranked_stocks(scored, G3_RISK_DISCOVERY, layer="technical_entry")}
    assert g1 == g2 == g3


def test_future_bars_do_not_change_historical_signal():
    registry = load_market_registry()
    panel = prepare_limited_panel(build_synthetic_panel(sessions=370, end=date(2023, 7, 10)))
    session = date(2023, 6, 26)
    themes = ["semiconductors"]
    raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon="mid")
    themed = precompute_theme_raws(raws, clipped, session, registry=registry, themes=themes)
    first = score_contexts(
        registry=registry,
        session=session,
        as_of=eod_evaluation_as_of(session),
        clipped=clipped,
        raws=raws,
        themed=themed,
        themes=themes,
        families=["A_trend_quality"],
        profile="balanced",
        horizon="mid",
        universe_version=UNIVERSE_VERSION,
    )
    later = prepare_limited_panel(build_synthetic_panel(sessions=390, end=date(2023, 8, 7)))
    raws2, clipped2 = precompute_session_raws(later, session, registry=registry, horizon="mid")
    themed2 = precompute_theme_raws(raws2, clipped2, session, registry=registry, themes=themes)
    second = score_contexts(
        registry=registry,
        session=session,
        as_of=eod_evaluation_as_of(session),
        clipped=clipped2,
        raws=raws2,
        themed=themed2,
        themes=themes,
        families=["A_trend_quality"],
        profile="balanced",
        horizon="mid",
        universe_version=UNIVERSE_VERSION,
    )
    def key(result):
        return sorted(
            (
                item["row"]["security_id"],
                item["row"]["algorithm_id"],
                item["row"]["score"],
                item["decisions"][G1_STOCK_REFERENCE].technical_entry_passed,
            )
            for item in result["records"]
        )
    assert key(first) == key(second)


def test_low_vol_etfs_do_not_enter_stock_reference():
    registry = load_market_registry()
    panel = _panel_with_extra_etfs()
    session = date(2023, 7, 10)
    raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon="mid")
    from screener_gate_adapter_v1 import reference_input_from_raw_and_series

    inputs = [reference_input_from_raw_and_series(raws[sid], clipped[sid], session) for sid in raws if sid in clipped]
    etf_ids = {item.security_id for item in inputs if item.asset_track == "etf"}
    assert etf_ids
    built = None
    try:
        built = build_reference(inputs)
    except Exception:
        built = None
    if built is not None:
        assert not set(built.member_ids) & etf_ids
    assert all(not reference_eligible(item) for item in inputs if item.asset_track == "etf")


def test_finalize_helpers_keep_nulls_and_entry_identity():
    sys.path.insert(0, str(ROOT / "scripts" / "research"))
    from screener_gate_replay_v1 import (
        B0_CURRENT,
        G1_STOCK_REFERENCE,
        G2_EXTENSION_DISCOVERY,
        G3_RISK_DISCOVERY,
        build_validation_md,
        engineering_checks,
        fee_adjust,
        max_drawdown,
        percentile,
    )

    assert fee_adjust(None, 0.001) is None
    assert fee_adjust(0.01, 0.001) == 0.009
    assert percentile([], 0.5) is None
    assert max_drawdown([]) is None
    assert abs(max_drawdown([0.1, -0.2, 0.05]) - ((0.88 / 1.1) - 1.0)) < 1e-12
    daily = [
        {
            B0_CURRENT: {"top20": ["A"], "technical_n": 1, "qualified_n": 0, "discovery_n": 1, "etf_in_stock_top20_n": 0, "overlap_vs_b0": {"added": [], "removed": []}},
            G1_STOCK_REFERENCE: {"top20": ["A", "B"], "technical_n": 2, "qualified_n": 0, "discovery_n": 2, "etf_in_stock_top20_n": 0, "overlap_vs_b0": {"added": ["B"], "removed": []}},
            G2_EXTENSION_DISCOVERY: {"top20": ["A", "B"], "technical_n": 2, "qualified_n": 0, "discovery_n": 4, "etf_in_stock_top20_n": 0, "overlap_vs_b0": {"added": ["B"], "removed": []}},
            G3_RISK_DISCOVERY: {"top20": ["A", "B"], "technical_n": 2, "qualified_n": 0, "discovery_n": 5, "etf_in_stock_top20_n": 0, "overlap_vs_b0": {"added": ["B"], "removed": []}},
        }
    ]
    checks = engineering_checks(daily, {"alignment": {"aligned": True}})
    assert checks["g1_g2_g3_entry_mismatch_days"] == 0
    assert checks["g1_can_add_vs_b0_top20_days"] == 1
    assert checks["unverified_not_promoted"] is True
    validation = build_validation_md(
        manifest={"production_anchor": "d16b25e", "layer": "restricted_current_membership_exploratory", "dates": {}, "run": {}, "data": {}},
        summary={"broad_market_median": None, "broad_market_median_reason": "restricted pool"},
        diagnostics={"engineering_checks": checks},
        caches={"any_reusable_bars": False},
        commands=["python -m pytest -q tests/test_screener_gate_candidates_v1.py"],
    )
    assert "## 执行命令" in validation
    assert "## 旧资源复用" in validation
    assert "not claimed reused" in validation


def test_old_median_uses_production_industry_grouping():
    registry = load_market_registry()
    panel = prepare_limited_panel(build_synthetic_panel(sessions=370, end=date(2023, 7, 10)))
    session = date(2023, 7, 10)
    raws, _clipped = precompute_session_raws(panel, session, registry=registry, horizon="mid")
    median = production_old_median(raws)
    values = [float(raw.atr_pct) for raw in raws.values() if raw.atr_pct is not None]
    assert median == sorted(values)[len(values) // 2]
