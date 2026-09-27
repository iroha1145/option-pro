"""v1.7 production adoption of S1: conservative v1.7 plus the benchmark-only fund scope.

The v1.6 scoring functions (no options) stay pinned by the golden fixture; this file
checks what production now publishes through ``live_config.LIVE_CONFIG``.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.services.eod_limited import ALGORITHM_VERSION, COMPUTE_VERSION
from app.services.eod_limited import market_data, worker
from app.services.eod_limited.diagnostic_store import read_security_diagnostics
from app.services.eod_limited.full_market_tuning import DEFAULT_POLICY, R_NEUTRAL_VALUE
from app.services.eod_limited.live_config import (
    CONSERVATIVE_V17, CONSERVATIVE_V17_TILT_MULTIPLIERS, LIVE_CONFIG, V16_CONFIG, LiveConfig,
)
from app.services.eod_limited.options import INDUSTRY_OFF
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited import store as eod_store
from app.services.eod_limited.store import read_batch
from app.services.eod_limited.universe import FUND_SCOPE_BENCHMARKS, benchmark_fund_tickers
from app.services.research_eod_v1.constants import HORIZONS, PROFILES

from eod_v17_fixtures import SESSION, build_panel, rounded

GOLDEN = json.loads(gzip.open(Path(__file__).resolve().parent / "fixtures" / "eod_v16_golden.json.gz", "rt").read())


def without_provenance(rows):
    return [{key: value for key, value in row.items() if key != "weight_provenance_id"} for row in rows]


def test_versions_and_live_config_name_the_adopted_set():
    assert COMPUTE_VERSION == "limited-all-market-v1.7" and ALGORITHM_VERSION == "eod-limited-v1.7"
    assert LIVE_CONFIG == LiveConfig(conservative_policy=CONSERVATIVE_V17, fund_scope=FUND_SCOPE_BENCHMARKS)
    assert LIVE_CONFIG.industry_mode == INDUSTRY_OFF and LIVE_CONFIG.g_tilt is None
    assert LIVE_CONFIG.label() == "v1.7 conservative=v1.7:atr2 funds=benchmarks"
    assert LIVE_CONFIG.tilt_multipliers() == {"conservative": CONSERVATIVE_V17_TILT_MULTIPLIERS}
    assert LIVE_CONFIG.tuning_policy().version == "full-market-v1.7-cons-atr2"
    options = LIVE_CONFIG.scoring_options()
    assert options is not None and options.industry_mode == INDUSTRY_OFF and options.tuning is not DEFAULT_POLICY
    assert options.factor_capabilities()["G"] == "disabled_unverified_industry"
    assert V16_CONFIG.label() == "v1.6" and V16_CONFIG.scoring_options() is None
    assert len(benchmark_fund_tickers()) == 12


def _publish(tmp_path, monkeypatch, config=None, extra_coverage=()):
    panel = build_panel()
    seen = {}
    manifest = {"status": "complete", "eligible_count": len(panel), "complete_bar_count": len(panel),
                "source_hash": "test", "volume_session_scope": market_data.VOLUME_SCOPE}

    def fake_load(**kw):
        seen.update(kw)
        if kw.get("fund_scope") == FUND_SCOPE_BENCHMARKS:
            manifest["fund_scope"] = FUND_SCOPE_BENCHMARKS
        coverage = [{"ticker": sid, "status": "ok", "bars": 370} for sid in panel] + list(extra_coverage)
        return panel, coverage, manifest

    monkeypatch.setattr(market_data, "load_all_market_panel", fake_load)
    kwargs = {} if config is None else {"live_config": config}
    result = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False, **kwargs)
    assert result["status"] == "RAN"
    return read_batch(tmp_path), seen


def test_default_worker_publishes_s1(tmp_path, monkeypatch):
    batch, seen = _publish(tmp_path, monkeypatch)
    assert seen["fund_scope"] == FUND_SCOPE_BENCHMARKS
    assert batch["compute_version"] == COMPUTE_VERSION
    assert batch["live_config"]["label"] == LIVE_CONFIG.label()
    assert batch["coverage"]["fund_scope"] == FUND_SCOPE_BENCHMARKS
    assert "industry" not in batch["coverage"]
    checked = {profile: 0 for profile in PROFILES}
    for horizon in HORIZONS:
        for profile in PROFILES:
            variant = batch["variants"][f"{profile}|{horizon}"]
            assert variant["factor_capabilities"]["G"] == "disabled_unverified_industry"
            assert variant["v17_options"]["tuning_version"] == "full-market-v1.7-cons-atr2"
            served = eod_store.variant_from_batch(batch, profile, horizon)  # what the API reader projects
            payload = project_strength_payload(served, parameters={"profile": profile, "timeframe": horizon})
            assert payload["algorithm_version"] == ALGORITHM_VERSION
            assert payload["coverage"]["fund_scope"] == FUND_SCOPE_BENCHMARKS
            golden = GOLDEN[f"{profile}/{horizon}"]["rows"]
            rows = rounded(payload["rows"])
            # Unverified volume leaves no eligible rows: the scored rows the batch keeps are the watch rows.
            stock_rows = [row for row in variant["watch_list"] if row["stock_or_etf_track"] == "stock"]
            checked[profile] += len(stock_rows)
            if profile == "conservative":
                assert {row["ticker"] for row in rows} != {row["ticker"] for row in golden} or \
                    [row["sort_score"] for row in rows] != [row["sort_score"] for row in golden]
                for row in stock_rows:
                    assert row["factors"]["R"] == R_NEUTRAL_VALUE and row["full_market_tuning"]["R_neutralized"]
                    assert row["full_market_tuning"]["version"] == "full-market-v1.7-cons-atr2"
                    assert row["full_market_tuning"]["atr_multiplier"] == 2.0
                    assert row["atr_multiplier_source"] == "full-market-v1.7-cons-atr2_override"
                    assert row["entry_state"] in {"ok", "extended"}
            else:
                # Balanced and aggressive are v1.6; only the provenance id (a hash of the
                # registry, which now carries the conservative tilt) differs.
                assert without_provenance(rows) == without_provenance(golden), (profile, horizon)
                for row in stock_rows:  # the compact variant keeps row-level tuning records only
                    assert row["full_market_tuning"]["version"] == "full-market-v1.5"
                    assert row["atr_multiplier_source"] == "v1.5_override"
    assert all(checked[profile] > 0 for profile in PROFILES), checked  # every profile's checks ran on real rows
    # The registry the worker scored with carries the conservative tilt (R back to 1.0): read it
    # from the published weight provenance rather than from a registry built here.
    ticker = next(row["security_id"] for row in batch["variants"]["conservative|mid"]["watch_list"]
                  if row["stock_or_etf_track"] == "stock")
    diagnostics = read_security_diagnostics(batch, ticker, "conservative", "mid", root=tmp_path)
    sources = list(diagnostics["weight_provenance_sources"].values())
    assert sources and all(source["profile_factor_tilt"][6] == pytest.approx(1.0) for source in sources)
    assert all(source["profile_factor_tilt"][1] == pytest.approx(0.9 * 2.0) for source in sources)
    balanced = read_security_diagnostics(batch, ticker, "balanced", "mid", root=tmp_path)
    assert all(source["profile_factor_tilt"][6] == 1 for source in balanced["weight_provenance_sources"].values())


def test_v16_config_is_still_available_as_the_control(tmp_path, monkeypatch):
    batch, seen = _publish(tmp_path, monkeypatch, config=V16_CONFIG)
    assert seen["fund_scope"] == "all" and "live_config" not in batch
    for horizon in HORIZONS:
        for profile in PROFILES:
            variant = batch["variants"][f"{profile}|{horizon}"]
            assert "v17_options" not in variant
            payload = project_strength_payload(eod_store.variant_from_batch(batch, profile, horizon), parameters={})
            assert rounded(payload["rows"]) == GOLDEN[f"{profile}/{horizon}"]["rows"]
            assert "fund_scope" not in payload["coverage"]


def test_scan_reader_names_the_fund_scope_and_diagnostics_explain_an_excluded_fund(tmp_path, monkeypatch):
    from app.api import strength

    excluded = {"ticker": "ZZZF", "name": "Loan fund", "provider_type": "ETF", "primary_exchange": "ARCX",
                "status": "excluded:FUND_OUT_OF_SCOPE", "bars": 0}
    batch, _ = _publish(tmp_path, monkeypatch, extra_coverage=[excluded])
    diagnostics = read_security_diagnostics(batch, "ZZZF", "balanced", "mid", root=tmp_path)
    assert diagnostics["data_status"] == "out_of_scope"
    assert diagnostics["coverage"]["status"] == "excluded:FUND_OUT_OF_SCOPE"
    kept = read_security_diagnostics(batch, "XLE", "balanced", "mid", root=tmp_path)
    assert kept["data_status"] in {"scored", "data_insufficient"} and kept["coverage"]["status"] == "ok"

    # The scan reader takes the batch from the store; serve the one the worker just published.
    monkeypatch.setattr(eod_store, "read_batch", lambda: batch)
    served = {}
    for track in ("stock", "etf", "all"):
        parameters = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "ranking_algorithm": "eod_limited_v1",
                      "profile": "balanced", "timeframe": "mid", "min_price": 0, "top": 50}
        out, _, _ = strength._read_eod_limited_snapshot(parameters=parameters, list_kind="observation",
                                                        resolution=None, track=track)
        served[track] = out
    assert all(out["fund_scope"] == FUND_SCOPE_BENCHMARKS for out in served.values())
    assert all(row["stock_or_etf_track"] == "etf" for row in served["etf"]["rows"])
    assert {row["ticker"] for row in served["etf"]["rows"]} <= benchmark_fund_tickers()
    assert [row["ticker"] for row in served["stock"]["rows"]] == \
        [row["ticker"] for row in served["all"]["rows"] if row["stock_or_etf_track"] == "stock"]


def test_fund_theme_strength_is_withheld_under_the_benchmark_scope(tmp_path, monkeypatch):
    from app.services.eod_limited.context_snapshot import sector_rows_with_scores
    from app.services.eod_limited.diagnostics import FUND_SCOPE_REASON, FUND_THEME_ID

    batch, _ = _publish(tmp_path, monkeypatch)
    control, _ = _publish(tmp_path / "control", monkeypatch, config=V16_CONFIG)
    statistics, reference = batch["theme_statistics"], control["theme_statistics"]
    assert statistics["fund_scope"] == FUND_SCOPE_BENCHMARKS and reference["fund_scope"] == "all"
    funds = next(row for row in statistics["sectors"] if row["sector_id"] == FUND_THEME_ID)
    funds_before = next(row for row in reference["sectors"] if row["sector_id"] == FUND_THEME_ID)
    assert funds_before["avg_strength"] is not None  # the full pool ranks funds among ~all funds
    assert funds["avg_strength"] is None and funds["leaders"] == [] and funds["scored_count"] == 0
    assert funds["score_source_status"] == "unavailable" and funds["fund_scope"] == FUND_SCOPE_BENCHMARKS
    assert funds["missing_reasons"] == {FUND_SCOPE_REASON: funds["member_count"]}
    for suffix in ("1mo", "3mo", "6mo"):  # returns do not depend on the pool and stay
        assert funds[f"avg_return_{suffix}"] == funds_before[f"avg_return_{suffix}"]
    for row, before in zip(statistics["sectors"], reference["sectors"]):
        if row["sector_id"] != FUND_THEME_ID:
            assert {k: v for k, v in row.items() if k != "fund_scope"} == before, row["sector_id"]

    context = {"sectors": [], "_stale": False, "source_status": "active"}

    def sector_rows(published):
        selection = project_strength_payload(eod_store.variant_from_batch(published, "balanced", "mid"), parameters={})
        return sector_rows_with_scores(context, selection, period="3mo")

    rows, control_rows = sector_rows(batch), sector_rows(control)
    fund_row = next(row for row in rows if row["sector_id"] == FUND_THEME_ID)
    fund_row_before = next(row for row in control_rows if row["sector_id"] == FUND_THEME_ID)
    assert fund_row_before["avg_strength"] is not None and fund_row_before["leaders"]
    assert fund_row["avg_strength"] is None and fund_row["leaders"] == []
    assert fund_row["score_missing_reasons"] == {FUND_SCOPE_REASON: 12} and fund_row["fund_scope"] == FUND_SCOPE_BENCHMARKS
    assert fund_row["avg_return"] == fund_row_before["avg_return"] == funds["avg_return_3mo"]
    stock_rows = [row for row in rows if row["sector_id"] != FUND_THEME_ID]
    assert len(stock_rows) == 23 and all("fund_scope" not in row for row in stock_rows)
    assert stock_rows == [row for row in control_rows if row["sector_id"] != FUND_THEME_ID]

