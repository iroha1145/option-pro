from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.services.eod_limited.diagnostic_store import DiagnosticWriter, read_security_diagnostics
from app.services.eod_limited import diagnostics as diagnostics_module
from app.services.eod_limited.diagnostics import VariantDiagnostics, _return_pct, build_theme_statistics, build_universe_funnel
from app.services.eod_limited.context_snapshot import sector_rows_with_scores
from app.services.eod_limited.store import publish_batch, read_batch, variant_from_batch
from app.services.eod_limited import worker
from app.services.market_calendar import prior_trading_sessions


def _batch(manifest: dict, *, version: str = "test-v1") -> dict:
    return {
        "served_session": "2026-09-18", "compute_version": version,
        "feature_version": "features-v1", "coverage": {"source_hash": "bars-1"},
        "diagnostics": manifest,
        "variants": {"balanced|mid": {
            "capability_flags": {"dollar_liquidity_verified": False},
            "volume_scope": "UNVERIFIED",
            "family_results": [{"rows": []}], "watch_list": [], "composite_results": [],
        }},
    }


def test_indexed_diagnostics_keep_rejected_paths_and_data_gaps(tmp_path: Path) -> None:
    writer = DiagnosticWriter(
        root=tmp_path, served_session="2026-09-18", compute_version="test-v1",
        feature_version="features-v1", source_hash="bars-1",
        dollar_volume_basis="raw_close_x_raw_volume_unverified",
    )
    writer.write_coverage([
        {"ticker": "AMD", "status": "ok", "name": "AMD"},
        {"ticker": "MISSING", "status": "missing_session", "reason": "MISSING_TARGET_SESSION", "name": "Missing"},
        {"ticker": "SHORT", "status": "ok", "name": "Short history"},
    ])
    writer.write_block("balanced|mid", "semiconductors", "A_trend_quality", [{
        "security_id": "AMD", "name": "AMD", "price": 200.0, "score": 81.0,
        "status": "rejected", "rejection_reasons": ["HIGH_ATR"],
        "gate_results": {"common": ["HIGH_ATR"]},
        "atr": 10.0, "atr_pct": 5.0, "sector_median_atr_pct": 2.0,
        "atr_threshold_pct": 3.5, "atr_reference_n": 100,
        "adv20": 42_000_000.0, "factors": {"T": 90.0},
        "effective_weights": {"T": 1.0}, "score_components": {"T": 81.0},
        "stock_or_etf_track": "stock", "weight_provenance_id": "weights-1",
    }], {"id": "weights-1", "source": "base_times_prior_normalized_v1"})
    writer.write_block("balanced|mid", "semiconductors", "D_residual_momentum", [
        {"security_id": "SHORT", "score": None, "status": "rejected", "rejection_reasons": ["SHORT_HISTORY"]},
    ])
    writer.write_block("balanced|mid", "semiconductors", "C_trend_pullback", [
        {"security_id": "SHORT", "score": 60.0, "status": "rejected", "rejection_reasons": ["SHORT_HISTORY"]},
    ])
    manifest = writer.finish()
    batch = _batch(manifest)
    rejected = read_security_diagnostics(batch, "amd", "balanced", "mid", root=tmp_path)
    assert rejected is not None and rejected["data_status"] == "scored"
    assert rejected["ticker"] == "AMD" and rejected["requested_ticker"] == "amd"
    assert rejected["identity_resolution"] == "unique_case_insensitive"
    assert rejected["paths"][0]["status"] == "rejected"
    assert rejected["paths"][0]["atr_threshold_pct"] == 3.5
    assert rejected["paths"][0]["adv20_proxy_basis"] == "raw_close_x_raw_volume_unverified"
    assert rejected["weight_provenance_sources"]["weights-1"]["source"] == "base_times_prior_normalized_v1"
    assert rejected["display"]["in_observation"] is False
    missing = read_security_diagnostics(batch, "MISSING", "balanced", "mid", root=tmp_path)
    assert missing is not None and missing["data_status"] == "data_insufficient"
    assert missing["coverage"]["reason"] == "MISSING_TARGET_SESSION"
    assert missing["paths"] == []
    short = read_security_diagnostics(batch, "SHORT", "balanced", "mid", root=tmp_path)
    assert short is not None and short["data_status"] == "data_insufficient"
    assert len(short["paths"]) == 2
    assert all(path["rejection_reasons"] == ["SHORT_HISTORY"] for path in short["paths"])
    assert read_security_diagnostics(batch, "UNKNOWN", "balanced", "mid", root=tmp_path) is None
    assert read_security_diagnostics({**batch, "compute_version": "different"}, "AMD", "balanced", "mid", root=tmp_path)["data_status"] == "diagnostics_unavailable"
    assert read_security_diagnostics(batch, "AMD", "aggressive", "mid", root=tmp_path)["data_status"] == "variant_diagnostics_unavailable"


def test_provider_case_variants_remain_distinct_in_coverage_paths_and_lookup(tmp_path: Path) -> None:
    writer = DiagnosticWriter(
        root=tmp_path, served_session="2026-09-18", compute_version="test-v1",
        feature_version="features-v1", source_hash="bars-1", dollar_volume_basis=None,
    )
    writer.write_coverage([
        {"ticker": "BCPC", "status": "ok", "name": "Balchem common"},
        {"ticker": "BCpC", "status": "excluded:UNSUPPORTED_SECURITY_TYPE:PFD", "name": "Balchem preferred"},
        {"ticker": "XYpA", "status": "ok"},
        {"ticker": "AbC", "status": "ok"}, {"ticker": "aBC", "status": "ok"},
    ])
    writer.write_block("balanced|mid", "all_market_stocks", "A_trend_quality", [
        {"security_id": "BCPC", "score": 70.0, "status": "watch", "stock_or_etf_track": "stock"},
        {"security_id": "BCpC", "score": 30.0, "status": "rejected", "stock_or_etf_track": "stock"},
    ])
    batch = _batch(writer.finish())
    batch["variants"]["balanced|mid"]["watch_list"] = [
        {"security_id": "BCPC", "score": 70.0, "status": "watch", "stock_or_etf_track": "stock"},
    ]
    common = read_security_diagnostics(batch, "BCPC", "balanced", "mid", root=tmp_path)
    assert common is not None and common["provider_ticker"] == "BCPC"
    assert common["ticker"] == "BCPC" and common["requested_ticker"] == "BCPC"
    assert common["identity_resolution"] == "exact"
    assert common["coverage"]["name"] == "Balchem common"
    assert [path["security_id"] for path in common["paths"]] == ["BCPC"]
    assert common["display"]["in_observation"] is True
    preferred = read_security_diagnostics(batch, "BCpC", "balanced", "mid", root=tmp_path)
    assert preferred is not None and preferred["provider_ticker"] == "BCpC"
    assert preferred["ticker"] == "BCpC" and preferred["requested_ticker"] == "BCpC"
    assert preferred["identity_resolution"] == "exact"
    assert preferred["coverage"]["name"] == "Balchem preferred"
    assert [path["security_id"] for path in preferred["paths"]] == ["BCpC"]
    assert preferred["data_status"] == "out_of_scope"
    lower = read_security_diagnostics(batch, "bcpc", "balanced", "mid", root=tmp_path)
    assert lower is not None and lower["data_status"] == "ambiguous_symbol"
    assert lower["ticker"] is None and lower["requested_ticker"] == "bcpc"
    assert lower["provider_tickers"] == ["BCPC", "BCpC"]
    assert lower["paths"] == []
    preferred_only = read_security_diagnostics(batch, "XYPA", "balanced", "mid", root=tmp_path)
    assert preferred_only is not None and preferred_only["provider_ticker"] == "XYpA"
    assert preferred_only["identity_resolution"] == "unique_case_insensitive"
    assert preferred_only["paths"] == []
    ambiguous = read_security_diagnostics(batch, "ABC", "balanced", "mid", root=tmp_path)
    assert ambiguous is not None and ambiguous["data_status"] == "ambiguous_symbol"
    assert ambiguous["provider_tickers"] == ["AbC", "aBC"]
    assert ambiguous["paths"] == []


def test_theme_coverage_uses_exact_provider_ticker_with_case_variant(monkeypatch) -> None:
    original = diagnostics_module.SECTORS["semiconductors"]
    monkeypatch.setitem(diagnostics_module.SECTORS, "semiconductors", {**original, "tickers": ["BCPC"]})
    reference = VariantDiagnostics(profile="balanced", horizon="mid")
    reference.add_block("semiconductors", "A_trend_quality", [
        {"security_id": "BCPC", "score": 55.0, "status": "watch", "rejection_reasons": []},
        {"security_id": "BCpC", "score": 99.0, "status": "rejected", "rejection_reasons": ["OUT_OF_SCOPE"]},
    ])
    end = date(2026, 9, 18)
    sessions = [*prior_trading_sessions(end, 20), end]
    panel = {"BCPC": SimpleNamespace(dates=sessions, close=np.linspace(100.0, 110.0, len(sessions)))}
    statistics = build_theme_statistics(
        reference, panel=panel,
        coverage_records=[
            {"ticker": "BCPC", "status": "ok"},
            {"ticker": "BCpC", "status": "excluded:UNSUPPORTED_SECURITY_TYPE:PFD"},
        ],
        served_session=end.isoformat(), compute_version="test-v1",
        feature_version="features-v1", source_hash="bars-1",
    )
    semi = next(row for row in statistics["sectors"] if row["sector_id"] == "semiconductors")
    assert semi["member_count"] == semi["eligible_universe_count"] == semi["complete_bar_count"] == 1
    assert semi["scored_count"] == 1 and semi["avg_strength"] == 55.0
    assert semi["directory_missing_count"] == 0
    assert semi["return_coverage_1mo"] == 1


def test_absent_theme_member_is_not_counted_in_eligible_directory_pool(monkeypatch) -> None:
    original = diagnostics_module.SECTORS["semiconductors"]
    monkeypatch.setitem(diagnostics_module.SECTORS, "semiconductors", {**original, "tickers": ["ABSENT"]})
    reference = VariantDiagnostics(profile="balanced", horizon="mid")
    reference.add_block("semiconductors", "A_trend_quality", [])
    statistics = build_theme_statistics(
        reference, panel={}, coverage_records=[],
        served_session="2026-09-18", compute_version="test-v1",
        feature_version="features-v1", source_hash="bars-1",
    )
    semi = next(row for row in statistics["sectors"] if row["sector_id"] == "semiconductors")
    assert semi["member_count"] == 1
    assert semi["eligible_universe_count"] == semi["complete_bar_count"] == 0
    assert semi["directory_missing_count"] == 1
    assert semi["missing_reasons"] == {"NOT_IN_DIRECTORY": 1}


def test_fixed_reference_theme_statistics_include_rejected_scores_without_imputation() -> None:
    reference = VariantDiagnostics(profile="balanced", horizon="mid")
    reference.add_block("semiconductors", "A_trend_quality", [
        {"security_id": "AMD", "score": 80.0, "status": "rejected", "rejection_reasons": ["EXTENDED"]},
        {"security_id": "NVDA", "score": 20.0, "status": "watch", "rejection_reasons": ["DOLLAR_LIQUIDITY_UNVERIFIED"]},
    ])
    sessions = [*prior_trading_sessions(date(2026, 9, 18), 139), date(2026, 9, 18)]
    panel = {
        symbol: SimpleNamespace(dates=sessions, close=np.linspace(start, end, len(sessions)))
        for symbol, start, end in (("AMD", 50, 100), ("NVDA", 100, 110), ("SPY", 100, 105))
    }
    statistics = build_theme_statistics(
        reference, panel=panel,
        coverage_records=[{"ticker": "AMD", "status": "ok"}, {"ticker": "NVDA", "status": "ok"}],
        served_session="2026-09-18", compute_version="test-v1",
        feature_version="features-v1", source_hash="bars-1",
    )
    assert statistics["sector_count"] == 24
    semi = next(row for row in statistics["sectors"] if row["sector_id"] == "semiconductors")
    assert semi["scored_count"] == 2 and semi["avg_strength"] == 50.0
    assert semi["member_count"] > 2 and semi["missing_reasons"]["NOT_IN_DIRECTORY"] > 0
    assert semi["eligible_universe_count"] == 2
    assert semi["directory_missing_count"] == semi["member_count"] - 2
    assert semi["excess_vs_spy_3mo"] is not None
    software = next(row for row in statistics["sectors"] if row["sector_id"] == "software")
    assert software["avg_strength"] is None
    assert software["score_source_status"] == "unavailable"


def test_theme_return_uses_fixed_calendar_endpoints() -> None:
    end = date(2026, 9, 18)
    sessions = [*prior_trading_sessions(end, 126), end]
    full = SimpleNamespace(dates=sessions, close=np.linspace(100.0, 200.0, len(sessions)))
    internal = 35
    with_gap = SimpleNamespace(
        dates=sessions[:internal] + sessions[internal + 1:],
        close=np.delete(full.close, internal),
    )
    assert _return_pct(full, 126, end.isoformat()) == _return_pct(with_gap, 126, end.isoformat()) == 100.0
    without_start = SimpleNamespace(dates=sessions[1:], close=full.close[1:])
    assert _return_pct(without_start, 126, end.isoformat()) is None
    stale = SimpleNamespace(dates=sessions[:-1], close=full.close[:-1])
    assert _return_pct(stale, 126, end.isoformat()) is None
    assert _return_pct(SimpleNamespace(close=full.close), 126, end.isoformat()) is None

    reference = VariantDiagnostics(profile="balanced", horizon="mid")
    reference.add_block("semiconductors", "A_trend_quality", [
        {"security_id": "AMD", "score": 80.0, "status": "rejected", "rejection_reasons": ["EXTENDED"]},
        {"security_id": "NVDA", "score": 70.0, "status": "watch", "rejection_reasons": []},
    ])
    statistics = build_theme_statistics(
        reference, panel={"AMD": full, "NVDA": without_start, "SPY": with_gap},
        coverage_records=[{"ticker": "AMD", "status": "ok"}, {"ticker": "NVDA", "status": "ok"}],
        served_session=end.isoformat(), compute_version="test-v1", feature_version="features-v1", source_hash="bars-1",
    )
    semi = next(row for row in statistics["sectors"] if row["sector_id"] == "semiconductors")
    assert semi["return_coverage_6mo"] == 1
    assert semi["avg_return_6mo"] == semi["spy_return_6mo"] == 100.0
    assert semi["excess_vs_spy_6mo"] == 0.0


def test_family_funnel_conserves_exclusive_outcomes() -> None:
    diagnostics = VariantDiagnostics(profile="balanced", horizon="mid")
    diagnostics.add_block("semiconductors", "A_trend_quality", [
        {"security_id": "A", "score": None, "status": "rejected", "rejection_reasons": ["DATA_INSUFFICIENT"]},
        {"security_id": "B", "score": 80, "status": "rejected", "rejection_reasons": ["HIGH_ATR", "EXTENDED"]},
        {"security_id": "C", "score": 80, "status": "watch", "rejection_reasons": ["DOLLAR_LIQUIDITY_UNVERIFIED"]},
        {"security_id": "D", "score": 80, "status": "eligible", "rejection_reasons": []},
    ])
    branch = diagnostics.branches[0]
    assert branch["input_count"] == 4
    assert branch["data_insufficient_count"] == 1
    assert branch["technical_rejected_count"] == 1
    assert branch["qualification_watch_count"] == 1
    assert branch["strict_eligible_count"] == 1
    assert branch["reason_counts_nonexclusive"]["HIGH_ATR"] == 1
    assert branch["reason_counts_nonexclusive"]["EXTENDED"] == 1
    universe = build_universe_funnel([
        {"ticker": "A", "status": "excluded:INACTIVE"},
        {"ticker": "B", "status": "ok", "short_history": True},
        {"ticker": "C", "status": "missing_session"},
    ])
    assert universe["directory_count"] == universe["excluded_count"] + universe["in_scope_count"]
    assert universe["in_scope_count"] == universe["complete_bar_count"] + universe["data_unavailable_count"]
    assert universe["short_history_with_bar_count"] == 1


def test_worker_publishes_diagnostics_theme_statistics_and_public_rows_together(tmp_path: Path) -> None:
    session = date(2026, 9, 18)
    panel = worker.build_synthetic_panel(sessions=370, end=session)
    result = worker.run_eod_limited_job(
        session=session, panel=panel, root=tmp_path,
        profile="balanced", horizon="mid", themes=["semiconductors"],
        algorithms=["A_trend_quality"], refresh_context=False,
    )
    assert result["status"] == "RAN"
    batch = read_batch(tmp_path)
    assert batch is not None
    assert batch["diagnostics"]["served_session"] == batch["served_session"]
    assert batch["theme_statistics"]["served_session"] == batch["served_session"]
    assert variant_from_batch(batch, "balanced", "mid")["theme_statistics"] == batch["theme_statistics"]
    assert variant_from_batch(batch, "balanced", "mid")["family_funnels"]["branches"]
    assert variant_from_batch(batch, "balanced", "mid")["family_funnels"]["universe"]["status"] == "unavailable"
    nvda = read_security_diagnostics(batch, "NVDA", "balanced", "mid", root=tmp_path)
    assert nvda is not None and nvda["paths"]
    assert all(path["sector_context"] == "semiconductors" for path in nvda["paths"])
    assert len(nvda["paths"]) == 1
    assert all(row["status"] == "eligible" for block in batch["variants"]["balanced|mid"]["family_results"] for row in block["rows"])


def test_missing_diagnostic_manifest_is_explicitly_unavailable(tmp_path: Path) -> None:
    publish_batch({"served_session": "2026-09-18", "variants": {"balanced|mid": {}}}, root=tmp_path)
    old = read_batch(tmp_path)
    assert old is not None
    response = read_security_diagnostics(old, "AMD", "balanced", "mid", root=tmp_path)
    assert response is not None and response["data_status"] == "variant_diagnostics_unavailable"


def test_old_batch_does_not_infer_theme_score_from_observation() -> None:
    context = {"sectors": [{"sector_id": "semiconductors", "avg_return_3mo": 2.0}]}
    old_selection = {"observation_rows": [{"ticker": "NVDA", "score": 99.0}], "score_data_through": "2026-09-18"}
    row = next(row for row in sector_rows_with_scores(context, old_selection, period="3mo")
               if row["sector_id"] == "semiconductors")
    assert row["avg_strength"] is None and row["score_coverage"] is None
    assert row["score_source_status"] == "unavailable"
    assert row["avg_return"] == 2.0


def test_theme_score_refuses_a_different_snapshot_session() -> None:
    context = {"sectors": [{"sector_id": "semiconductors", "avg_return_3mo": 2.0}]}
    selection = {
        "score_data_through": "2026-09-18", "score_version": "test-v1",
        "theme_statistics": {
            "served_session": "2026-09-17", "compute_version": "test-v1",
            "reference_profile": "balanced", "reference_horizon": "mid",
            "reference_family": "A_trend_quality",
            "sectors": [{"sector_id": "semiconductors", "member_count": 14,
                         "scored_count": 14, "avg_strength": 99.0,
                         "score_source_status": "active"}],
        },
    }
    row = next(row for row in sector_rows_with_scores(context, selection, period="3mo")
               if row["sector_id"] == "semiconductors")
    assert row["avg_strength"] is None and row["score_source_status"] == "unavailable"


def test_sector_response_uses_one_batch_for_every_return_period_and_count() -> None:
    context = {"sectors": [{
        "sector_id": "semiconductors", "avg_return_1mo": 99.0,
        "avg_return_3mo": 99.0, "avg_return_6mo": 99.0, "count": 99,
    }]}
    selection = {
        "score_data_through": "2026-09-18", "score_version": "test-v1",
        "feature_version": "features-v1", "coverage": {"source_hash": "bars-1"},
        "theme_statistics": {
            "served_session": "2026-09-18", "compute_version": "test-v1",
            "feature_version": "features-v1", "source_hash": "bars-1",
            "reference_profile": "balanced", "reference_horizon": "mid",
            "reference_family": "A_trend_quality",
            "sectors": [{
                "sector_id": "semiconductors", "member_count": 14, "scored_count": 2,
                "avg_strength": 50.0, "score_source_status": "degraded",
                "avg_return_1mo": 1.0, "avg_return_3mo": 2.0, "avg_return_6mo": 3.0,
                "return_coverage_1mo": 4, "return_coverage_3mo": 5,
                "return_coverage_6mo": 6,
            }],
        },
    }
    semi = next(row for row in sector_rows_with_scores(context, selection, period="3mo")
                if row["sector_id"] == "semiconductors")
    assert (semi["avg_return_1mo"], semi["avg_return_3mo"], semi["avg_return_3m"], semi["avg_return_6mo"]) == (1.0, 2.0, 2.0, 3.0)
    assert semi["avg_return"] == 2.0 and semi["count"] == 5
    assert semi["return_coverage_1mo"] == 4 and semi["return_coverage_6mo"] == 6


def test_failed_publication_removes_unreferenced_generation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker, "publish_batch", lambda *args, **kwargs: {"ok": False, "integrity": "stale_previous_retained"})
    session = date(2026, 9, 18)
    result = worker.run_eod_limited_job(
        session=session, panel=worker.build_synthetic_panel(sessions=5, end=session),
        root=tmp_path, profile="balanced", horizon="mid",
        themes=["semiconductors"], algorithms=["A_trend_quality"],
        refresh_context=False,
    )
    assert result["status"] == "PUBLISH_FAILED"
    assert list((tmp_path / "eod-limited-v1").glob("diagnostics-*.sqlite")) == []
