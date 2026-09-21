from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from app.services.eod_limited.inference import precompute_all_horizon_inputs, score_eod_session
from app.services.eod_limited.market_registry import load_market_registry
from app.services.eod_limited.price_only import apply_price_only_track
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited.shadow import (
    _best, _entry_view, _new_branches, _relax_public_extension, etf_classification, momentum_features,
    panel_hash, run_shadow_comparison, write_shadow_report,
)
from app.services.eod_limited.worker import build_synthetic_panel
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
from app.services.research_eod_v1.snapshot import _atr_references, compute_snapshot


@pytest.fixture(scope="module")
def inputs():
    registry = load_market_registry()
    session = date(2023, 7, 10)
    source = build_synthetic_panel(sessions=370, end=session)
    panel = {sid: source[sid] for sid in ("AMD", "NVDA", "SPY", "QQQ")}
    bond = deepcopy(panel["SPY"])
    bond.security_id = bond.ticker_at_signal = "BOND"
    bond.asset_track = "etf"
    bond.theme_ids = ("etfs",)
    bond.close = np.linspace(99, 100, len(bond.close))
    bond.open = bond.close.copy()
    bond.high = bond.close * 1.001
    bond.low = bond.close * .999
    bond.raw_close = bond.close.copy()
    bond.dollar_volume = np.full(len(bond.close), 50_000_000.)
    bond.venue_metadata = {**bond.venue_metadata, "etf_subtype": "fixed_income", "etf_subtype_source": "fixture-provider", "etf_subtype_verified": True}
    panel["BOND"] = bond
    themes = ["semiconductors", "etfs", "all_market_stocks"]
    shared = precompute_all_horizon_inputs(panel, session, registry=registry, horizons=["short", "mid", "long"], themes=themes)
    return registry, panel, session, themes, shared


def snapshot(inputs, policy=None, cache=None):
    registry, _, session, _, shared = inputs
    _, clipped, themed = shared["mid"]
    return compute_snapshot(eod_evaluation_as_of(session), clipped, "fixture", registry,
                            sector_id="semiconductors", algorithm="A_trend_quality", profile="balanced", horizon="mid",
                            source_finalized_through=session, precomputed_raws=themed["semiconductors"],
                            reapply_theme_gates=False, already_session_clipped=True, snapshot_cache=cache,
                            **({} if policy is None else {"atr_reference_policy": policy}))


def test_default_legacy_and_cache_policy_isolation(inputs):
    default = snapshot(inputs)
    cache = {}
    assert snapshot(inputs, "legacy", cache) == default
    assert snapshot(inputs, "track_liquid_v1", cache) == snapshot(inputs, "track_liquid_v1")
    assert snapshot(inputs, "legacy", cache) == default
    rows = [r for r in default["rows"] if r["security_id"] in {"AMD", "NVDA"}]
    _, _, _, _, shared = inputs
    raws = shared["mid"][0]
    values = sorted(r.atr_pct for r in raws.values() if r.atr_pct is not None)
    for row in rows:
        assert row["sector_median_atr_pct"] == values[len(values) // 2]
        assert row["atr_reference_n"] == len(values)
        assert row["atr_threshold_pct"] == min(8.0, 1.75 * row["sector_median_atr_pct"])
        assert row["common_gate_checks"]["extension"] == (row["extension_atr"] <= row["extension_limit_atr"])


def test_reference_fallback_and_proxy_filter_are_track_local(inputs):
    raw = inputs[4]["mid"][0]["AMD"]
    base = replace(raw, raw_close=30, last_close=30, adv20=50_000_000, currently_tradable=True, industry_id=None, parent_industry_id=None, asset_track="stock")
    stocks = {f"S{i}": replace(base, security_id=f"S{i}", atr_pct=float(i + 1)) for i in range(6)}
    mixed = {**stocks, "ILLQ": replace(base, atr_pct=.01, adv20=1),
             "HALT": replace(base, atr_pct=.01, currently_tradable=False),
             "CHEAP": replace(base, atr_pct=.01, raw_close=1),
             "BOND": replace(base, asset_track="etf", atr_pct=.01)}
    refs = _atr_references(mixed, "track_liquid_v1")
    assert refs["S0"] == (4.0, 6, "stock:track")
    assert refs["BOND"] == (.01, 1, "etf:track")
    industry = {sid: replace(item, industry_id="real-industry", parent_industry_id="real-parent") for sid, item in stocks.items()}
    assert _atr_references(industry, "track_liquid_v1")["S0"] == (4.0, 6, "stock:industry")
    industry["S0"] = replace(industry["S0"], industry_id="tiny")
    assert _atr_references(industry, "track_liquid_v1")["S0"] == (4.0, 6, "stock:parent")
    assert _atr_references({"bad": replace(base, adv20=None, atr_pct=2)}, "track_liquid_v1")["bad"] == (None, 0, "unavailable")


@pytest.mark.parametrize("family_reason", [None, "TOO_FAR_FROM_MA", "TOO_FAR_FROM_PLATFORM"])
def test_extension_observation_never_bypasses_strict_or_family_gate(inputs, family_reason):
    registry = inputs[0]
    row = {"security_id": "TEST", "status": "rejected", "factors": {key: 95.0 for key in "TMSBPVRG"},
           "rejection_reasons": ["EXTENDED"] + ([family_reason] if family_reason else []),
           "gate_results": {"common": ["EXTENDED"], "setup": [family_reason] if family_reason else []}}
    payload = {"rows": [row]}
    args = dict(registry=registry, theme_id="semiconductors", family="A_trend_quality", profile="balanced", horizon="mid")
    strict = apply_price_only_track(payload, **args)["rows"][0]
    relaxed = apply_price_only_track(_relax_public_extension(payload), **args)["rows"][0]
    viewed = _entry_view(strict, relaxed)
    assert viewed["status"] == "rejected"
    assert viewed["strict_eligible"] is False and viewed["new_entry_allowed"] is False
    assert viewed["entry_state"] == "extended"
    assert viewed["observation_included"] is (family_reason is None)
    if family_reason is None:
        assert viewed["observation_status"] == "watch"
        assert "DOLLAR_LIQUIDITY_UNVERIFIED" in viewed["observation_rejection_reasons"]
    else:
        assert family_reason in viewed["observation_rejection_reasons"]


def test_horizon_windows_use_different_actual_prices(inputs):
    source = deepcopy(inputs[4]["mid"][1])
    stock = source["AMD"]
    stock.close = np.ones(len(stock.close)) * 100
    stock.close[-126:] = np.linspace(100, 80, 126)
    stock.close[-20:] = np.linspace(80, 100, 20)
    source["SPY"].close = np.ones(len(stock.close)) * 100
    source["NVDA"].close = np.linspace(80, 100, len(stock.close))
    calculated = {h: momentum_features(source, h, session=inputs[2])["AMD"] for h in ("short", "mid", "long")}
    assert calculated["short"]["relative_return"] != calculated["mid"]["relative_return"]
    assert len({item["acceleration"] for item in calculated.values()}) == 3
    assert calculated["short"]["relative_sessions"] == 20
    assert calculated["long"]["acceleration_sessions"] == 63
    # Window changes must reach the branch score, not only explanatory metadata.
    template = {"security_id": "AMD", "gate_results": {"common": ()}, "stock_or_etf_track": "stock"}
    short_rows = _new_branches([template], source, momentum_features(source, "short", session=inputs[2]), inputs[0], "balanced", "short")
    long_rows = _new_branches([template], source, momentum_features(source, "long", session=inputs[2]), inputs[0], "balanced", "long")
    assert short_rows[0]["score"] != long_rows[0]["score"]


def test_full_comparison_shares_inputs_matches_baseline_and_isolates_bonds(inputs, monkeypatch, tmp_path):
    registry, panel, session, themes, shared = inputs
    import app.services.eod_limited.shadow as shadow
    original = shadow.compute_snapshot
    cache_ids = {"legacy": set(), "track_liquid_v1": set()}
    cache_refs = []
    def wrapped(*args, **kwargs):
        cache = kwargs["snapshot_cache"]
        cache_refs.append(cache)
        cache_ids[kwargs["atr_reference_policy"]].add(id(cache))
        return original(*args, **kwargs)
    monkeypatch.setattr(shadow, "compute_snapshot", wrapped)
    before_hash = panel_hash(shared["mid"][1])
    report = run_shadow_comparison(panel, session, registry=registry, profiles=["balanced"], themes=themes,
                                   precomputed_horizon_inputs=shared, stock_isolation_remove_ids=["BOND"])
    assert panel_hash(shared["mid"][1]) == before_hash
    assert not cache_ids["legacy"] & cache_ids["track_liquid_v1"]
    assert report["stock_isolation"]["passed"] is True
    assert report["stock_isolation"]["removed_nonbenchmark_etf_n"] == 1
    assert report["stock_isolation"]["removed_etf_ids"] == ["BOND"]
    assert all(value is False for value in report["qualification_flags"].values())
    for horizon in ("short", "mid", "long"):
        raws, clipped, themed = shared[horizon]
        live = score_eod_session(panel, session, registry=registry, profile="balanced", horizon=horizon, themes=themes,
                                 precomputed_raws=raws, clipped_panel=clipped, precomputed_theme_raws=themed)
        data = report["variants"]["baseline"][f"balanced/{horizon}"]
        assert data["row_status_counts"].get("eligible", 0) == live["eligible_n"]
        assert data["row_status_counts"].get("watch", 0) == live["watch_n"]
        assert data["row_status_counts"].get("rejected", 0) == live["rejected_n"]
        projected = project_strength_payload(live, parameters={})["rows"][:20]
        assert [(r["security_id"], r["score"]) for r in data["combined_baseline_ranking"]] == [(r["ticker"], r["sort_score"]) for r in projected]
        assert data["theme_count"] == 24
        assert report["variants"]["raw_momentum"][f"balanced/{horizon}"]["strict_security_count"] == 0
        assert "E_raw_relative_momentum" in report["variants"]["raw_momentum"][f"balanced/{horizon}"]["families"]
    write_shadow_report(report, tmp_path)
    assert json.loads((tmp_path / "shadow-comparison.json").read_text())["frozen_inputs"]["panel_hash"] == before_hash
    assert "没有历史收益检验" in (tmp_path / "shadow-comparison.md").read_text()


def test_etf_subtype_requires_evidence(inputs):
    bond = deepcopy(inputs[4]["mid"][1]["BOND"])
    assert etf_classification(bond)["subtype"] == "fixed_income"
    bond.venue_metadata = {"name": "High income inverse leveraged bond ETF", "etf_subtype": "fixed_income"}
    assert etf_classification(bond) == {"subtype": "unknown", "source": None}


def test_input_hash_catches_price_and_metadata_changes(inputs):
    panel = deepcopy(inputs[4]["mid"][1])
    first = panel_hash(panel)
    panel["AMD"].close[-2] += .01
    assert panel_hash(panel) != first
    second = panel_hash(panel)
    panel["AMD"].venue_metadata = {**panel["AMD"].venue_metadata, "new_evidence": "provided"}
    assert panel_hash(panel) != second


def test_cli_loads_archive_read_only_after_live_split_ttl(monkeypatch, tmp_path):
    import importlib.util
    from app.services.eod_limited import market_data
    spec = importlib.util.spec_from_file_location("shadow_cli", Path(__file__).parents[1] / "scripts/eod_shadow_compare.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    session = date(2023, 7, 10)
    directory = [{"ticker": "AAA", "name": "Fixture", "market": "stocks", "locale": "us", "type": "CS", "primary_exchange": "XNAS", "active": True}]
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: directory)
    def provider(path, params):
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": []}
        day = date.fromisoformat(path.rsplit("/", 1)[-1])
        timestamp = int(eod_evaluation_as_of(day).timestamp() * 1000)
        return {"status": "OK", "adjusted": False, "resultsCount": 1,
                "results": [{"T": "AAA", "o": 30., "h": 31., "l": 29., "c": 30., "v": 1_000_000., "t": timestamp}]}
    monkeypatch.setattr(market_data, "_provider_get", provider)
    expected, _, _ = market_data.load_all_market_panel(end=session, root=tmp_path)
    db = market_data._db_path(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE split_captures SET fetched_at = '2000-01-01T00:00:00+00:00'")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    directory_path = tmp_path / "directory.json"
    directory_path.write_text(json.dumps(directory))
    before = db.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("offline shadow cannot fetch")
    monkeypatch.setattr(market_data, "_provider_get", forbidden)
    actual, manifest = cli.load_frozen_cache(db, directory_path, session, allow_subset=True)
    assert panel_hash(actual) == panel_hash(expected)
    assert manifest["panel_count"] == 1 and manifest["source_hash"]
    assert manifest["directory_scope"] == "subset"
    assert db.read_bytes() == before
    directory_path.write_text(json.dumps({"results": directory, "next_url": "partial"}))
    with pytest.raises(ValueError, match="partial page"):
        cli.load_frozen_cache(db, directory_path, session)


def _controlled_momentum_panel(inputs):
    panel = deepcopy(inputs[4]["mid"][1])
    n = len(panel["AMD"].dates)
    panel["AMD"].close = np.exp(np.linspace(0, .6, n)) * 50
    panel["NVDA"].close = np.exp(np.linspace(0, .3, n)) * 50
    panel["SPY"].close = np.ones(n) * 100
    return panel


@pytest.mark.parametrize("bad_kind", ["future_source", "short_history", "partial_T", "halted"])
def test_ineligible_reference_cannot_dilute_valid_momentum_score(inputs, bad_kind):
    panel = _controlled_momentum_panel(inputs)
    template = {"security_id": "AMD", "gate_results": {"common": ()}, "stock_or_etf_track": "stock"}
    def score(current):
        return _new_branches([template], current, momentum_features(current, "short", session=inputs[2]), inputs[0], "balanced", "short")[0]
    baseline = score(panel)
    assert baseline["score"] == 100 and baseline["observation_included"] is True
    bad = deepcopy(panel["AMD"])
    bad.security_id = bad.ticker_at_signal = "OUTLIER"
    bad.close = np.exp(np.linspace(0, 100, len(bad.close)))
    if bad_kind == "future_source":
        bad.source_available_at = eod_evaluation_as_of(inputs[2]) + timedelta(days=1)
    elif bad_kind == "short_history":
        bad = bad.last_n(31)
    elif bad_kind == "partial_T":
        bad.bar_partial = np.zeros(len(bad.dates), dtype=bool)
        bad.bar_partial[-1] = True
    else:
        bad.halted = True
        bad.bar_halted = np.ones(len(bad.dates), dtype=bool)
    panel["OUTLIER"] = bad
    actual = score(panel)
    assert actual["score"] == baseline["score"]
    assert actual["observation_included"] == baseline["observation_included"]
    features = momentum_features(panel, "short", session=inputs[2])["OUTLIER"]
    assert features["reference_eligible"] is False
    assert features["relative_return"] is None


def test_missing_internal_bar_never_extends_relative_or_acceleration_window(inputs):
    panel = _controlled_momentum_panel(inputs)
    panel["AMD"].close = panel["SPY"].close.copy()
    full = momentum_features(panel, "short", session=inputs[2])["AMD"]
    assert full["relative_return"] == 0
    day = panel["AMD"].dates[-3]
    series = panel["AMD"]
    remove_index = series.dates.index(day)
    # Delete the whole source bar, as a real missing-data event would do.
    from dataclasses import fields
    for field in fields(series):
        value = getattr(series, field.name)
        if isinstance(value, np.ndarray) and len(value) == len(series.dates):
            setattr(series, field.name, np.delete(value, remove_index))
        elif isinstance(value, (tuple, list)) and field.name != "dates" and len(value) == len(series.dates):
            kept = list(value[:remove_index]) + list(value[remove_index + 1:])
            setattr(series, field.name, tuple(kept) if isinstance(value, tuple) else kept)
    series.dates.pop(remove_index)
    missing = momentum_features(panel, "short", session=inputs[2])["AMD"]
    assert missing["relative_return"] is None and missing["acceleration"] is None
    assert missing["relative_return_reason"] == "MISSING_WINDOW_PRICE"
    assert missing["acceleration_reason"] == "MISSING_WINDOW_PRICE"
    assert missing["window_dates"] == full["window_dates"]
    assert missing["missing_sessions"]["relative"] == [day.isoformat()]


def test_stock_tie_order_matches_production_projection():
    rows = [{"security_id": sid, "score": 80., "algorithm_id": family,
             "sector_context": "all_market_stocks", "stock_or_etf_track": "stock", "status": "watch"}
            for sid, family in (("ZZZ", "A_trend_quality"), ("AAA", "D_residual_momentum"))]
    projected = project_strength_payload({"watch_list": rows}, parameters={})["rows"]
    shadow = _best([_entry_view(row) for row in rows])
    assert [(r["security_id"], r["score"]) for r in shadow] == [(r["ticker"], r["sort_score"]) for r in projected]
    assert shadow[0]["security_id"] == "AAA"


def test_baseline_topk_matches_real_scoring_and_projection_nonempty(inputs):
    registry, panel, session, _, _ = inputs
    panel = deepcopy(panel)
    for sid in ("AAA", "ZZZ"):
        panel[sid] = deepcopy(panel["AMD"])
        panel[sid].security_id = panel[sid].ticker_at_signal = sid
    themes = ["semiconductors", "etfs", "all_market_stocks"]
    shared = precompute_all_horizon_inputs(panel, session, registry=registry, horizons=["mid"], themes=themes)
    raws, clipped, themed = shared["mid"]
    # Deterministic nonempty technical states, scored by the real snapshot,
    # price-only and projection functions on both sides of the comparison.
    for theme, theme_raws in themed.items():
        for i, (sid, raw) in enumerate(sorted(theme_raws.items())):
            theme_raws[sid] = replace(raw, structure_score=100., ma_state=100., er63=1., t_direction_ok=True,
                                     lh_ll_unrepaired=False, above_sma50=True, sma50=100., sma50_prev20=90.,
                                     raw_close=100., adv20=100_000_000., atr_pct=1., extension_atr=0., ma_distance_atr=0.,
                                     unresolved_upthrust=False, structure_invalidated=False, slope50=float(i),
                                     m63=float(i), m126_skip21=float(i), m252_skip21=float(i), sigma20=10. - i,
                                     gap_tail252=10. - i, max_drawdown63=10. - i)
    live = score_eod_session(panel, session, registry=registry, profile="balanced", horizon="mid", themes=themes,
                             precomputed_raws=raws, clipped_panel=clipped, precomputed_theme_raws=themed)
    expected = project_strength_payload(live, parameters={})["rows"][:2]
    assert len(expected) == 2
    report = run_shadow_comparison(panel, session, registry=registry, profiles=["balanced"], horizons=["mid"],
                                   themes=themes, precomputed_horizon_inputs=shared, top_k=2, verify_stock_isolation=False)
    actual = report["variants"]["baseline"]["balanced/mid"]["combined_baseline_ranking"]
    assert [(r["security_id"], r["score"]) for r in actual] == [(r["ticker"], r["sort_score"]) for r in expected]


def test_etf_relative_branch_requires_verified_matching_benchmark(inputs):
    panel = _controlled_momentum_panel(inputs)
    features = momentum_features(panel, "short", session=inputs[2])
    assert features["AMD"]["benchmark_ticker"] == "SPY"
    assert features["BOND"]["benchmark_status"] == "missing_benchmark"
    assert features["BOND"]["relative_return"] is None
    assert features["BOND"]["acceleration"] is not None
    assert features["BOND"]["acceleration_basis"].startswith("absolute_")
    source = {"security_id": "BOND", "stock_or_etf_track": "etf", "gate_results": {"common": ()}}
    branches = _new_branches([source], panel, features, inputs[0], "balanced", "short")
    assert branches[0]["score"] is None and branches[0]["observation_included"] is False
    assert "MISSING_BENCHMARK" in branches[0]["rejection_reasons"]
    panel["BOND"].venue_metadata = {**panel["BOND"].venue_metadata, "etf_relative_benchmark_ticker": "QQQ",
                                    "etf_relative_benchmark_source": "fixture-explicit-matching-evidence", "etf_relative_benchmark_verified": True}
    matched = momentum_features(panel, "short", session=inputs[2])["BOND"]
    assert matched["benchmark_ticker"] == "QQQ" and matched["benchmark_status"] == "ok"
    assert matched["relative_return"] is not None
    panel["BOND"].venue_metadata["etf_relative_benchmark_verified"] = False
    assert momentum_features(panel, "short", session=inputs[2])["BOND"]["relative_return"] is None


def test_directory_manifest_binds_complete_capture_to_file_and_count(tmp_path):
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location("shadow_cli_manifest", Path(__file__).parents[1] / "scripts/eod_shadow_compare.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    rows = [{"ticker": "AAA"}]
    raw = json.dumps(rows).encode()
    path = tmp_path / "directory-manifest.json"
    with pytest.raises(ValueError, match="directory-manifest"):
        cli._directory_capture(raw, rows, None, allow_subset=False)
    assert cli._directory_capture(raw, rows, None, allow_subset=True)["scope"] == "subset"
    capture = {"sha256": hashlib.sha256(raw).hexdigest(), "row_count": 1,
               "fetch_method": cli.DIRECTORY_FETCH_METHOD, "source": "Massive /v3/reference/tickers",
               "source_client": "option-pro", "source_client_version": "fixture-version",
               "captured_at": "2026-09-21T01:00:00+00:00", "pagination_complete": True, "terminal_next_url": None}
    path.write_text(json.dumps(capture))
    assert cli._directory_capture(raw, rows, path, allow_subset=False)["scope"] == "all_market"
    for key, invalid in (("sha256", "bad"), ("row_count", 2), ("fetch_method", "one_page"), ("terminal_next_url", "next"), ("captured_at", "2026-09-21T01:00:00")):
        path.write_text(json.dumps({**capture, key: invalid}))
        with pytest.raises(ValueError):
            cli._directory_capture(raw, rows, path, allow_subset=False)
    path.write_text(json.dumps({**capture, "pagination_complete": False}))
    assert cli._directory_capture(raw, rows, path, allow_subset=True)["scope"] == "subset"
