"""Each v1.7 candidate switch, on its own, against the v1.6 default on one synthetic panel."""
from __future__ import annotations

import gzip
import json
from copy import deepcopy
from datetime import date

import numpy as np
import pytest

from app.services.eod_limited import PURPOSE_SYNTHETIC
from app.services.eod_limited import industry as industry_module
from app.services.eod_limited.full_market_tuning import (
    DEFAULT_POLICY, M_ALPHA, R_NEUTRAL_VALUE, TuningPolicy, conservative_v17_policy,
)
from app.services.eod_limited.industry import IndustryTag, SicTable, sic_group, tag_for
from app.services.eod_limited.inference import precompute_all_horizon_inputs, score_eod_session
from app.services.eod_limited.live_config import CONSERVATIVE_V14, CONSERVATIVE_V17, V16_CONFIG, LiveConfig
from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS, load_market_registry
from app.services.eod_limited.options import (
    INDUSTRY_FULL, INDUSTRY_G_ONLY, INDUSTRY_OFF, DEFAULT_OPTIONS, G_NEUTRAL_VALUE, ScoringOptions,
)
from app.services.eod_limited.panel import prepare_limited_panel
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited.universe import (
    FUND_SCOPE_ALL, FUND_SCOPE_BENCHMARKS, benchmark_fund_tickers, select_all_market_universe,
)
from app.services.eod_limited.worker import _compact_variant
from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC, PRICE_SIC_INDUSTRY_DIAGNOSTIC
from app.services.research_eod_v1.constants import HORIZONS, PROFILES
from app.services.research_eod_v1.fixtures import make_series, structured_close, trading_days_ending
from app.services.research_eod_v1.residual import residual_raw_momentum

from eod_v17_fixtures import SESSION, build_panel, directory_rows, rounded, sic_table, stock_ids

THEMES = [ALL_MARKET_STOCKS, "semiconductors", "etfs"]
GOLDEN = json.loads(gzip.open(__file__.replace("test_eod_v17_switches.py", "fixtures/eod_v16_golden.json.gz"), "rt").read())


def tags(level: int = 3) -> dict[str, IndustryTag]:
    return SicTable(sic_table()).classify(directory_rows(), level=level)


def score_views(panel, options=None, registry=None, profiles=PROFILES, horizons=HORIZONS, themes=THEMES):
    registry = registry or load_market_registry()
    extra = {} if options is None else {"options": options}
    inputs = precompute_all_horizon_inputs(panel, SESSION, registry=registry, horizons=horizons, themes=themes, **extra)
    cache: dict = {}
    out = {}
    for horizon in horizons:
        raws, clipped, themed = inputs[horizon]
        for profile in profiles:
            scored = score_eod_session(
                panel, SESSION, registry=registry, profile=profile, horizon=horizon, themes=themes,
                purpose=PURPOSE_SYNTHETIC, precomputed_raws=raws, clipped_panel=clipped,
                precomputed_theme_raws=themed, compact=True, snapshot_cache=cache, **extra,
            )
            out[f"{profile}/{horizon}"] = scored
    return inputs, out


def public_rows(scored):
    return project_strength_payload(_compact_variant(scored), parameters={})["rows"]


def without_provenance(rows):
    return [{key: value for key, value in row.items() if key != "weight_provenance_id"} for row in rows]


def factor_rows(scored):
    """security -> family -> row for the scored (eligible/watch) rows of one view."""
    out: dict = {}
    for block in scored["family_results"]:
        for row in block["rows"]:
            out.setdefault(row["security_id"], {})[block["algorithm_id"]] = row
    return out


# ---------------------------------------------------------------- residual basket cache


def _industry_panel(count: int = 8, sessions: int = 400):
    rng = np.random.default_rng(3)
    days = trading_days_ending(date(2026, 9, 25), sessions)
    market_returns = rng.normal(0.0004, 0.01, sessions)
    market = make_series("SPY", days, 100 * np.cumprod(1 + market_returns), industry_id=None, asset_track="etf")
    panel = {"SPY": market}
    for index in range(count):
        industry = "sic3:283" if index < 5 else "sic3:367"
        returns = (0.9 + 0.1 * index) * market_returns + rng.normal(0.0002 * index, 0.012, sessions)
        panel[f"S{index}"] = make_series(f"S{index}", days, 50 * np.cumprod(1 + returns), industry_id=industry,
                                         parent_industry_id=None)
    # One peer too short for the residual window must be skipped by both paths.
    short = panel["S1"]
    panel["S1"] = make_series("S1", days[100:], short.close[100:], industry_id="sic3:283", parent_industry_id=None)
    return panel


def test_industry_basket_cache_matches_the_quadratic_path_and_is_leave_one_out():
    panel = _industry_panel()
    cache: dict = {}
    for sid, series in panel.items():
        if sid == "SPY":
            continue
        slow = residual_raw_momentum(series, panel["SPY"], panel)
        fast = residual_raw_momentum(series, panel["SPY"], panel, basket_cache=cache)
        assert fast.status == slow.status, sid
        if slow.raw is not None:
            assert fast.raw == pytest.approx(slow.raw, abs=1e-12), sid
    keys = list(cache)
    assert {key[0] for key in keys} == {"sic3:283", "sic3:367"} and len(keys) == 2
    members = {key[0]: set(cache[key][0]) for key in keys}
    assert members["sic3:283"] == {"S0", "S2", "S3", "S4"}  # S1 is too short for the window
    assert members["sic3:367"] == {"S5", "S6", "S7"}
    assert residual_raw_momentum(panel["S1"], panel["SPY"], panel, basket_cache=cache).status == "SHORT_HISTORY"
    # The industry basket changes the residual against the one-factor market fit.
    untagged = {sid: make_series(sid, s.dates, s.close, industry_id=None, asset_track=s.asset_track)
                for sid, s in panel.items()}
    one_factor = residual_raw_momentum(untagged["S0"], untagged["SPY"], untagged)
    two_factor = residual_raw_momentum(panel["S0"], panel["SPY"], panel, basket_cache=cache)
    assert one_factor.status == two_factor.status == "OK"
    assert one_factor.raw != pytest.approx(two_factor.raw)


def test_industry_basket_needs_two_peers_in_both_paths():
    panel = _industry_panel(count=7)  # sic3:367 has S5, S6 only
    assert {sid for sid, s in panel.items() if s.industry_id == "sic3:367"} == {"S5", "S6"}
    for target in ("S5", "S6"):
        slow = residual_raw_momentum(panel[target], panel["SPY"], panel)
        fast = residual_raw_momentum(panel[target], panel["SPY"], panel, basket_cache={})
        assert slow.status == fast.status == "OK" and fast.raw == pytest.approx(slow.raw, abs=1e-12)
    # Only one peer remains after leaving the target out: the basket is None and the fit is one-factor.
    untagged = {sid: make_series(sid, s.dates, s.close, industry_id=None, asset_track=s.asset_track)
                for sid, s in panel.items()}
    assert residual_raw_momentum(panel["S5"], panel["SPY"], panel, basket_cache={}).raw == pytest.approx(
        residual_raw_momentum(untagged["S5"], untagged["SPY"], untagged).raw)


# ---------------------------------------------------------------- SIC classification


def test_sic_groups_and_tags_leave_the_parent_empty_by_default():
    assert sic_group("2834", 3) == "sic3:283"
    assert sic_group("2834", 2) == "sic2:28"
    assert sic_group(2834, 4) == "sic4:2834"
    assert sic_group("283", 3) is None and sic_group(None, 3) is None and sic_group("28A4", 3) is None
    with pytest.raises(ValueError):
        sic_group("2834", 5)
    assert tag_for("2834") == IndustryTag("sic3:283", None, "2834")
    assert tag_for("2834", level=4, parent_level=2) == IndustryTag("sic4:2834", "sic2:28", "2834")
    assert tag_for(None).industry_id is None


def test_sic_table_resolves_reused_tickers_by_cik_and_falls_back_to_the_ticker(tmp_path):
    table = SicTable([
        {"ticker": "AAC", "cik": "0001829432", "as_of": "2023-11-03", "sic_code": "3443"},
        {"ticker": "AAC", "cik": "0002128115", "as_of": "2026-09-25", "sic_code": "6770"},
        {"ticker": "ABAT", "cik": None, "as_of": "2023-09-22", "sic_code": None},
        {"ticker": "ABAT", "cik": "0001576873", "as_of": "2026-09-25", "sic_code": "1400"},
        {"ticker": "ZED", "cik": None, "as_of": "2024-01-05", "sic_code": "7372"},
        {"ticker": "ZED", "cik": None, "as_of": "2025-01-05", "sic_code": "7373"},  # newer row wins
        {"ticker": "", "cik": None, "as_of": None, "sic_code": "9999"},  # ignored
    ])
    assert len(table) == 5
    assert table.sic_for("AAC", "0001829432") == "3443"
    assert table.sic_for("AAC", "0002128115") == "6770"
    assert table.sic_for("AAC", "0009999999") is None  # an unknown CIK never borrows another company's code
    assert table.sic_for("AAC") == "6770"  # no CIK: the latest record
    assert table.sic_for("ABAT", "0009999999") is None
    assert table.sic_for("ZED", "0001234567") == "7373"  # CIK-less rows serve any CIK
    assert table.sic_for("NOPE") is None
    directory = [
        {"ticker": "AAC", "type": "CS", "cik": "0001829432"},
        {"ticker": "ABAT", "type": "CS", "cik": "0001576873"},
        {"ticker": "SPY", "type": "ETF", "cik": None},
        {"ticker": "ZED", "type": "ADRC", "cik": None},
        {"ticker": "UNK", "type": "CS", "cik": "0000000001"},
    ]
    classified = table.classify(directory, level=3)
    assert classified == {"AAC": IndustryTag("sic3:344", None, "3443"), "ABAT": IndustryTag("sic3:140", None, "1400"),
                          "ZED": IndustryTag("sic3:737", None, "7373")}
    assert table.classify(directory, level=2, tickers=["AAC"]) == {"AAC": IndustryTag("sic2:34", None, "3443")}
    path = tmp_path / "industry-sic-v1.json.gz"
    table.save(path)
    reloaded = SicTable.load(path)
    assert reloaded.records == sorted(table.records, key=lambda r: (r["ticker"], r["cik"] or "", r["as_of"] or ""))
    assert reloaded.sic_for("AAC", "0002128115") == "6770"


@pytest.mark.parametrize("name", ["table.json.gz", "table.json"])
def test_sic_table_save_writes_sorted_json_and_replaces_the_previous_file(tmp_path, name):
    path = tmp_path / "nested" / name
    SicTable([{"ticker": "OLD", "cik": None, "as_of": "2026-01-01", "sic_code": "1000"}]).save(path)
    table = SicTable([
        {"ticker": "ZED", "cik": None, "as_of": "2026-09-01", "sic_code": "7373", "sic_description": "Café software"},
        {"ticker": "AAC", "cik": "2", "as_of": "2026-09-02", "sic_code": "6770"},
        {"ticker": "AAC", "cik": "1", "as_of": "2026-09-03", "sic_code": "3443"},
    ])
    table.save(path)
    expected = json.dumps({"version": 1, "records": sorted(
        table.records, key=lambda item: (item["ticker"], item["cik"] or "", item["as_of"] or ""),
    )}, sort_keys=True, ensure_ascii=False)
    raw = path.read_bytes()
    assert (gzip.decompress(raw) if name.endswith(".gz") else raw).decode("utf-8") == expected
    assert [item.name for item in path.parent.iterdir()] == [name]


def test_sic_table_save_is_fsynced_and_a_failed_rename_keeps_the_old_table(tmp_path, monkeypatch):
    from app.services.eod_limited import store

    path = tmp_path / "industry-sic-v1.json.gz"
    synced = []
    real_fsync = store.os.fsync
    monkeypatch.setattr(store.os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
    SicTable([{"ticker": "OLD", "cik": None, "as_of": "2026-01-01", "sic_code": "1000"}]).save(path)
    assert len(synced) == 2  # the temp file, then the directory entry of the rename

    def refuse(*_args):
        raise OSError("rename refused")

    monkeypatch.setattr(store.os, "replace", refuse)
    with pytest.raises(OSError, match="rename refused"):
        SicTable([{"ticker": "NEW", "cik": None, "as_of": "2026-09-01", "sic_code": "2000"}]).save(path)
    assert [item.name for item in tmp_path.iterdir()] == [path.name]
    assert SicTable.load(path).sic_for("OLD") == "1000"


def test_refresh_looks_up_only_unseen_tickers_within_the_budget_and_survives_failures():
    table = SicTable([{"ticker": "AAA", "cik": "1", "as_of": "2026-09-01", "sic_code": "2834"}])
    seen = []

    def fetch(ticker):
        seen.append(ticker)
        if ticker == "BAD":
            raise RuntimeError("provider down")
        return {"cik": f"cik-{ticker}", "sic_code": "3674" if ticker != "BZZ" else None}

    counts = industry_module.refresh_missing(
        table, [("AAA", "1"), ("BAD", None), ("BBB", "cik-BBB"), ("BZZ", None), ("CCC", None), ("DDD", None)],
        budget=4, as_of=date(2026, 9, 28), fetch=fetch,
    )
    assert seen == ["BAD", "BBB", "BZZ", "CCC"]  # sorted, AAA already known, DDD deferred
    assert counts == {"looked_up": 4, "failed": 1, "classified": 2, "no_sic": 1, "deferred": 1}
    assert table.sic_for("BBB") == "3674" and table.has_pair("BZZ") and not table.has_pair("BAD")
    assert table.sic_for("BBB", "cik-BBB") == "3674"
    # A reused ticker: the old issuer is on file, the directory now shows a new CIK -> looked up again,
    # stored under the new CIK, and the old record keeps answering for the old CIK.
    counts = industry_module.refresh_missing(table, [("AAA", "2")], budget=4, as_of=date(2026, 9, 28), fetch=fetch)
    assert seen[-1] == "AAA" and counts == {"looked_up": 1, "classified": 1}
    assert table.sic_for("AAA", "1") == "2834" and table.sic_for("AAA", "2") == "3674"
    assert table.has_pair("AAA", "2") and not table.has_pair("AAA", "3") and table.has_pair("AAA")
    # CCC was looked up without a CIK; the provider's CIK is stored, so another issuer is still unseen.
    assert table.has_pair("CCC") and table.has_pair("CCC", "cik-CCC") and not table.has_pair("CCC", "other")
    table.extend([{"ticker": "ZZZ", "cik": None, "as_of": "2026-09-01", "sic_code": "1000"}])
    assert table.has_pair("ZZZ", "any-cik")  # a CIK-less record answers for any CIK
    assert industry_module.refresh_missing(table, [("AAA", "1"), ("AAA", "2"), ("ZZZ", "x")], fetch=fetch) == {}


def test_ensure_industry_tags_persists_the_table_and_classifies_stock_members(tmp_path):
    root = tmp_path / "data"
    seed = SicTable(sic_table())
    seed.save(industry_module.table_path(root))
    calls = []

    def fetch(ticker):
        calls.append(ticker)
        return {"cik": "new", "sic_code": "6022"}

    directory = directory_rows() + [{"ticker": "FRESH", "type": "CS", "primary_exchange": "XNAS", "cik": "new",
                                     "active": True, "market": "stocks", "locale": "us", "name": "Fresh"}]
    tags_out, summary = industry_module.ensure_industry_tags(directory, root=root, level=3, budget=10, fetch=fetch)
    assert calls == ["FRESH"] and tags_out["FRESH"].industry_id == "sic3:602"
    assert summary["classified"] == len(tags_out) == 50 + 1  # 60 stocks, every sixth unclassified, plus FRESH
    assert summary["refresh"] == {"looked_up": 1, "classified": 1}
    assert summary["groups"] == 4  # 2834 and 2836 share sic3:283; FRESH joins 602
    assert SicTable.load(industry_module.table_path(root)).has_pair("FRESH")
    again, summary2 = industry_module.ensure_industry_tags(directory, root=root, level=3, budget=10, fetch=fetch)
    assert calls == ["FRESH"] and again == tags_out and summary2["refresh"] == {}


@pytest.mark.parametrize("damage", ["truncated", "zeroed", "empty"])
def test_a_damaged_table_is_read_as_empty_and_rewritten_by_the_refresh(tmp_path, monkeypatch, damage):
    root = tmp_path / "data"
    path = industry_module.table_path(root)
    SicTable(sic_table()).save(path)
    saved = path.read_bytes()
    path.write_bytes({"truncated": saved[: len(saved) // 2], "zeroed": b"\0" * 64, "empty": b""}[damage])
    logged = []
    monkeypatch.setattr(industry_module, "record_fallback_failure", lambda stage, exc, **_kw: logged.append(stage))

    tags, summary = industry_module.ensure_industry_tags(
        directory_rows(), root=root, level=3, budget=5, fetch=lambda _ticker: {"sic_code": "6022"},
    )

    assert logged == ["eod_industry_table_read"]
    assert summary["refresh"] == {"looked_up": 5, "classified": 5, "deferred": summary["stock_tickers"] - 5}
    assert len(tags) == 5
    assert len(SicTable.load(path)) == 5  # the damaged file was replaced by a readable one


def test_a_table_with_the_wrong_shape_is_still_an_error(tmp_path):
    root = tmp_path / "data"
    path = industry_module.table_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(b'{"version": 1, "records": 5}'))
    with pytest.raises(TypeError):
        industry_module.ensure_industry_tags(directory_rows(), root=root, budget=5, fetch=lambda _ticker: {})


# ---------------------------------------------------------------- options


def test_scoring_options_default_is_inactive_and_modes_are_validated():
    assert not DEFAULT_OPTIONS.active
    assert DEFAULT_OPTIONS.panel_industry() is None and DEFAULT_OPTIONS.g_overlay() is None
    assert all(DEFAULT_OPTIONS.track_for(profile) == PRICE_ONLY_DIAGNOSTIC for profile in PROFILES)
    assert DEFAULT_OPTIONS.factor_capabilities()["G"] == "disabled_unverified_industry"
    with pytest.raises(ValueError):
        ScoringOptions(industry_mode="sector")
    with pytest.raises(ValueError):
        ScoringOptions(industry_mode=INDUSTRY_G_ONLY)
    g_only = ScoringOptions(industry_mode=INDUSTRY_G_ONLY, industry=tags(), label="g3")
    assert g_only.active and g_only.panel_industry() is None
    assert g_only.g_overlay()["NEW000"] == ("sic3:283", None)
    assert g_only.track_for("balanced") == g_only.track_for("aggressive") == PRICE_SIC_INDUSTRY_DIAGNOSTIC
    assert g_only.track_for("conservative") == PRICE_ONLY_DIAGNOSTIC
    assert g_only.factor_capabilities()["G"] == "sic_industry_g_only_missing_neutral"
    full = ScoringOptions(industry_mode=INDUSTRY_FULL, industry=tags())
    assert full.panel_industry() is not None and full.g_overlay() is None
    cons = ScoringOptions(tuning=conservative_v17_policy(2.0))
    assert cons.active and cons.industry_mode == INDUSTRY_OFF
    assert cons.describe()["tuning_version"] == "full-market-v1.7-cons-atr2"


def test_prepare_limited_panel_tags_copies_and_leaves_the_untagged_panel_alone():
    panel = build_panel()
    base = prepare_limited_panel(panel)  # the loaded all-market panel is already a close-return view
    assert all(series.industry_id is None for series in base.values())
    tagged = prepare_limited_panel(base, industry=tags())
    assert tagged["NEW000"].industry_id == "sic3:283" and tagged["NEW005"].industry_id is None
    assert tagged["SPY"].industry_id is None
    assert tagged["NEW000"] is not base["NEW000"] and tagged["NEW000"].close is base["NEW000"].close
    assert base["NEW000"].industry_id is None
    assert tagged["NEW005"] is base["NEW005"]  # nothing to change: shared
    assert prepare_limited_panel(tagged)["NEW000"].industry_id is None  # the default clears in place


# ---------------------------------------------------------------- G only


@pytest.fixture(scope="module")
def g_only_views():
    return score_views(build_panel(), ScoringOptions(industry_mode=INDUSTRY_G_ONLY, industry=tags(), label="g3"))


def test_g_only_adds_g_to_balanced_and_aggressive_weights_and_nothing_else(g_only_views):
    inputs, views = g_only_views
    classified, unclassified = set(tags()), set(stock_ids()) - set(tags())
    raws = inputs["mid"][0]
    assert all(raw.industry_id is None for raw in raws.values())  # the panel itself is untouched
    for key, scored in views.items():
        profile = key.split("/")[0]
        golden_rows = {row["ticker"]: row for row in GOLDEN[key]["rows"]}
        assert scored["capability_track"] == (PRICE_ONLY_DIAGNOSTIC if profile == "conservative"
                                              else PRICE_SIC_INDUSTRY_DIAGNOSTIC)
        assert scored["factor_capabilities"]["G"] == "sic_industry_g_only_missing_neutral"
        assert scored["v17_options"]["label"] == "g3"
        rows = factor_rows(scored)
        for sid, families in rows.items():
            for family, row in families.items():
                g = row["factors"].get("G")
                if profile == "conservative":
                    assert g is None, (key, sid, family)
                    assert "G" not in row["effective_weights"]
                elif sid in unclassified or row["stock_or_etf_track"] == "etf":
                    assert g == G_NEUTRAL_VALUE, (key, sid, family)  # no classification: neutral G
                    assert row["effective_weights"]["G"] > 0
                else:
                    assert sid in classified and 0 <= g <= 100, (key, sid, family)
                    assert row["effective_weights"]["G"] > 0
                    assert sum(row["score_components"].values()) == pytest.approx(row["score"])
                # Every non-G factor is the v1.6 value: only G entered the score.
                golden = golden_rows.get(sid)
                if golden is not None and golden["algorithm_id"] == family:
                    for factor in "TMSBPVR":
                        assert rounded(row["factors"][factor]) == golden["factors"][factor], (key, sid, factor)
        if profile == "conservative":
            assert rounded(public_rows(scored)) == GOLDEN[key]["rows"]
    # G moved at least one balanced ranking against the golden order.
    changed = [key for key in views if key.startswith("balanced") and
               [row["ticker"] for row in public_rows(views[key])] != [row["ticker"] for row in GOLDEN[key]["rows"]]]
    assert changed


def test_g_only_g_is_the_engine_formula_on_the_classified_peers(g_only_views):
    inputs, views = g_only_views
    raws = inputs["mid"][0]
    from app.services.research_eod_v1.snapshot import industry_g_inputs

    stocks = {sid: raw for sid, raw in raws.items() if raw.asset_track == "stock"}
    stocks.update({sid: raws[sid] for sid in ("SPY", "QQQ")})
    industries = {sid: (tags().get(sid).industry_id if sid in tags() else None) for sid in stocks}
    q_g, breadth = industry_g_inputs(stocks, industries=industries, parents={sid: None for sid in stocks},
                                     tracks={sid: raw.asset_track for sid, raw in stocks.items()},
                                     spy_m63=raws["SPY"].m63)
    rows = factor_rows(views["balanced/mid"])
    checked = 0
    for sid, families in rows.items():
        for row in families.values():
            if q_g.get(sid) is None or breadth.get(sid) is None:
                assert row["factors"]["G"] == G_NEUTRAL_VALUE
                continue
            assert row["factors"]["G"] == pytest.approx(0.6 * q_g[sid] + 0.4 * 100 * breadth[sid])
            checked += 1
    assert checked


def test_g_only_with_sic4_uses_finer_groups(g_only_views):
    _, views3 = g_only_views
    _, views4 = score_views(build_panel(), ScoringOptions(industry_mode=INDUSTRY_G_ONLY, industry=tags(4), label="g4"),
                            profiles=("balanced",), horizons=("mid",))
    g3 = {(sid, f): row["factors"]["G"] for sid, fam in factor_rows(views3["balanced/mid"]).items() for f, row in fam.items()}
    g4 = {(sid, f): row["factors"]["G"] for sid, fam in factor_rows(views4["balanced/mid"]).items() for f, row in fam.items()}
    assert set(g3) & set(g4)
    assert any(g3[key] != g4[key] for key in set(g3) & set(g4) if g3[key] != G_NEUTRAL_VALUE)


# ---------------------------------------------------------------- full industry


def test_full_mode_tags_the_series_and_changes_ranks_and_residuals_for_stocks_only():
    options = ScoringOptions(industry_mode=INDUSTRY_FULL, industry=tags(), label="full3")
    base_inputs, base_views = score_views(build_panel(), profiles=("balanced", "conservative"), horizons=("mid",))
    full_inputs, full_views = score_views(build_panel(), options, profiles=("balanced", "conservative"), horizons=("mid",))
    base_raws, base_clipped, _ = base_inputs["mid"]
    full_raws, full_clipped, _ = full_inputs["mid"]
    assert full_clipped["NEW000"].industry_id == "sic3:283" and base_clipped["NEW000"].industry_id is None
    assert full_raws["NEW000"].industry_id == "sic3:283" and full_raws["NEW005"].industry_id is None
    changed_residuals = [sid for sid in tags() if base_raws[sid].residual != full_raws[sid].residual]
    assert changed_residuals, "the industry basket must enter the D residual"
    for sid in set(stock_ids()) - set(tags()):
        assert base_raws[sid].residual == full_raws[sid].residual  # no industry, one-factor as before
    for sid in ("SPY", "QQQ", "XLE", "TLT", "IWM", "GLD"):
        assert base_raws[sid].residual == full_raws[sid].residual
    # Geometry and the other raw fields are untouched by the tagging.
    for sid in stock_ids():
        for field in ("slope50", "m63", "m126_skip21", "m252_skip21", "structure_score", "atr_pct", "sigma20"):
            assert getattr(base_raws[sid], field) == getattr(full_raws[sid], field), (sid, field)
    base_rows, full_rows = factor_rows(base_views["balanced/mid"]), factor_rows(full_views["balanced/mid"])
    shrunk = [sid for sid in tags() if sid in base_rows and sid in full_rows
              and any(base_rows[sid][f]["factors"]["T"] != full_rows[sid][f]["factors"]["T"]
                      for f in base_rows[sid] if f in full_rows[sid])]
    assert shrunk, "industry-relative quantiles must move T for classified stocks"
    for sid, families in full_rows.items():
        for row in families.values():
            if row["stock_or_etf_track"] == "etf":
                assert row["factors"]["G"] == G_NEUTRAL_VALUE  # funds have no industry
                base_factors = base_rows[sid][row["algorithm_id"]]["factors"]
                assert {k: v for k, v in row["factors"].items() if k != "G"} == \
                    {k: v for k, v in base_factors.items() if k != "G"}  # fund ranks are per track
    assert full_views["balanced/mid"]["capability_track"] == PRICE_SIC_INDUSTRY_DIAGNOSTIC
    assert full_views["conservative/mid"]["capability_track"] == PRICE_ONLY_DIAGNOSTIC
    assert all(row["factors"]["G"] is None for fam in factor_rows(full_views["conservative/mid"]).values()
               for row in fam.values())
    assert full_views["balanced/mid"]["factor_capabilities"]["G"] == "sic_industry_full_missing_neutral"


# ---------------------------------------------------------------- conservative v1.7


def test_conservative_v17_policy_applies_the_hooks_to_conservative_only():
    policy = conservative_v17_policy(2.0)
    assert policy.version == "full-market-v1.7-cons-atr2"
    assert policy.m_alpha["conservative"] == M_ALPHA["balanced"] and policy.m_alpha["balanced"] == M_ALPHA["balanced"]
    assert policy.atr_multiplier["conservative"] == 2.0 and policy.atr_multiplier["balanced"] == 2.5
    assert "conservative" in policy.r_neutral_profiles and "conservative" in policy.extended_state_profiles
    assert DEFAULT_POLICY == TuningPolicy()
    with pytest.raises(ValueError):
        conservative_v17_policy(0)
    config = LiveConfig(conservative_policy=CONSERVATIVE_V17)
    registry = load_market_registry(extra_tilt_multipliers=config.tilt_multipliers())
    sealed = load_market_registry()
    tilt, before = registry["profiles"]["conservative"]["factor_tilt"], sealed["profiles"]["conservative"]["factor_tilt"]
    assert tilt[0] == pytest.approx(before[0] * 0.5) and tilt[1] == pytest.approx(before[1] * 2.0)
    assert tilt[6] == pytest.approx(1.0) and tilt[2:6] == before[2:6] and tilt[7] == before[7]
    assert registry["profiles"]["balanced"] == sealed["profiles"]["balanced"]
    options = config.scoring_options()
    _, views = score_views(build_panel(), options, registry=registry, horizons=("mid",))
    for profile in ("balanced", "aggressive"):
        # The provenance id hashes the whole registry, so only it may differ.
        assert without_provenance(rounded(public_rows(views[f"{profile}/mid"]))) == \
            without_provenance(GOLDEN[f"{profile}/mid"]["rows"])
    conservative = views["conservative/mid"]
    assert conservative["full_market_tuning"]["version"] == policy.version
    assert conservative["full_market_tuning"]["r_neutral_profiles"] == sorted(PROFILES)
    median = conservative["full_market_tuning"]["atr_reference_median"]
    tuned = 0
    for families in factor_rows(conservative).values():
        for row in families.values():
            if row["stock_or_etf_track"] != "stock":
                continue
            tuning = row["full_market_tuning"]
            assert row["factors"]["R"] == R_NEUTRAL_VALUE and tuning["R_neutralized"]
            assert row["atr_threshold_pct"] == pytest.approx(min(5.0, 2.0 * median))
            assert row["atr_multiplier_source"] == f"{policy.version}_override"
            if row["algorithm_id"] in {"A_trend_quality", "D_residual_momentum"}:
                assert tuning["alpha"] == M_ALPHA["balanced"]
            tuned += 1
    assert tuned
    golden_conservative = {row["ticker"] for row in GOLDEN["conservative/mid"]["rows"]}
    assert {row["ticker"] for row in public_rows(conservative)} != golden_conservative


# ---------------------------------------------------------------- fund scope


def test_benchmark_fund_scope_keeps_theme_funds_and_leaves_stock_rows_identical():
    assert {"SPY", "QQQ", "XLE", "TLT", "IWM", "GLD"} <= benchmark_fund_tickers()
    directory = directory_rows() + [{"ticker": "ZZZF", "name": "Loan fund", "market": "stocks", "locale": "us",
                                     "type": "ETF", "primary_exchange": "ARCX", "active": True, "cik": None}]
    members_all, coverage_all = select_all_market_universe(directory)
    members_bench, coverage_bench = select_all_market_universe(directory, fund_scope=FUND_SCOPE_BENCHMARKS)
    assert set(members_all) - set(members_bench) == {"ZZZF"}
    assert {row["ticker"]: row["status"] for row in coverage_bench}["ZZZF"] == "excluded:FUND_OUT_OF_SCOPE"
    assert {row["ticker"]: row["status"] for row in coverage_all}["ZZZF"] == "pending"
    with pytest.raises(ValueError):
        select_all_market_universe(directory, fund_scope="none")
    panel = build_panel()
    days = panel["SPY"].dates
    panel["ZZZF"] = make_series("ZZZF", days, structured_close(370, 25.0, 0.01, 12), theme_ids=("etfs",),
                                asset_track="etf", security_type="ETF", industry_id=None,
                                parent_industry_id=None).with_close_price_return()
    reduced = {sid: series for sid, series in panel.items() if sid in members_bench or sid in stock_ids()}
    assert "ZZZF" not in reduced and len(reduced) == len(panel) - 1
    _, views_all = score_views(panel, horizons=("mid",))
    _, views_reduced = score_views(reduced, horizons=("mid",))
    for key in views_all:
        rows_all = [row for row in public_rows(views_all[key]) if row["stock_or_etf_track"] == "stock"]
        rows_reduced = [row for row in public_rows(views_reduced[key]) if row["stock_or_etf_track"] == "stock"]
        assert rounded(rows_all) == rounded(rows_reduced), key
        assert rounded(rows_all) == [row for row in GOLDEN[key]["rows"] if row["stock_or_etf_track"] == "stock"]


# ---------------------------------------------------------------- registry tilts and live config


def test_extra_tilt_multipliers_apply_on_top_of_the_v16_lean():
    base = load_market_registry()
    tilted = load_market_registry(extra_tilt_multipliers={"balanced": {"G": 2.0}, "aggressive": {"G": 2.0}})
    for profile in ("balanced", "aggressive"):
        before, after = base["profiles"][profile]["factor_tilt"], tilted["profiles"][profile]["factor_tilt"]
        assert after[7] == pytest.approx(before[7] * 2.0) and after[:7] == before[:7]
    assert tilted["profiles"]["conservative"] == base["profiles"]["conservative"]
    assert tilted["v17_tilt_multipliers"] == {"balanced": {"G": 2.0}, "aggressive": {"G": 2.0}}
    assert "v17_tilt_multipliers" not in base
    assert base == load_market_registry()
    with pytest.raises(ValueError):
        load_market_registry(extra_tilt_multipliers={"balanced": {"X": 2.0}})
    with pytest.raises(ValueError):
        load_market_registry(extra_tilt_multipliers={"balanced": {"G": 0.0}})


def test_live_config_dataclass_default_is_v16_and_describes_every_switch():
    default = LiveConfig()
    assert default == V16_CONFIG
    assert default.label() == "v1.6" and default.tilt_multipliers() is None
    assert default.tuning_policy() is DEFAULT_POLICY and default.scoring_options() is None
    assert default.scoring_options(tags()) is None
    g = LiveConfig(industry_mode=INDUSTRY_G_ONLY, g_tilt=2.0)
    assert g.tilt_multipliers() == {"aggressive": {"G": 2.0}, "balanced": {"G": 2.0}}
    assert g.label() == "v1.7 industry=g_only:sic3 g_tilt=2"
    options = g.scoring_options(tags())
    assert options.industry_mode == INDUSTRY_G_ONLY and options.label == g.label()
    fallback = g.scoring_options({})
    assert fallback is None  # no tags and the default tuning: v1.6 scoring
    cons = LiveConfig(industry_mode=INDUSTRY_FULL, conservative_policy=CONSERVATIVE_V17, fund_scope=FUND_SCOPE_BENCHMARKS)
    assert cons.label() == "v1.7 industry=full:sic3 conservative=v1.7:atr2 funds=benchmarks"
    fallback = cons.scoring_options({})
    assert fallback.industry_mode == INDUSTRY_OFF and fallback.tuning.version.startswith("full-market-v1.7-cons")
    assert fallback.label.endswith("industry=unavailable")
    for bad in ({"industry_mode": "x"}, {"conservative_policy": "v2"}, {"fund_scope": "stocks"}, {"g_tilt": -1.0}):
        with pytest.raises(ValueError):
            LiveConfig(**bad)


def test_worker_wires_the_live_config_through_panel_registry_and_scoring(tmp_path, monkeypatch):
    from app.services.eod_limited import market_data, worker
    from app.services.eod_limited.store import read_batch

    panel = build_panel()
    directory = directory_rows()
    manifest = {"status": "complete", "eligible_count": len(panel), "complete_bar_count": len(panel),
                "source_hash": "test", "volume_session_scope": market_data.VOLUME_SCOPE}
    seen = {}

    def fake_load(**kw):
        seen.update(kw)
        kw["on_directory"](directory)
        coverage = [{"ticker": sid, "status": "ok", "bars": 370} for sid in panel]
        return panel, coverage, manifest

    monkeypatch.setattr(market_data, "load_all_market_panel", fake_load)
    SicTable(sic_table()).save(industry_module.table_path(tmp_path))
    config = LiveConfig(industry_mode=INDUSTRY_G_ONLY, industry_lookup_budget=0, fund_scope=FUND_SCOPE_BENCHMARKS)
    result = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False, live_config=config)
    assert result["status"] == "RAN"
    assert seen["fund_scope"] == FUND_SCOPE_BENCHMARKS
    batch = read_batch(tmp_path)
    assert batch["live_config"]["label"] == "v1.7 industry=g_only:sic3 funds=benchmarks"
    assert batch["coverage"]["industry"]["classified"] == 50
    variant = batch["variants"]["balanced|mid"]
    assert variant["factor_capabilities"]["G"] == "sic_industry_g_only_missing_neutral"
    assert variant["v17_options"]["industry_mode"] == INDUSTRY_G_ONLY
    payload = project_strength_payload(variant, parameters={})
    assert payload["factor_capabilities"]["G"] == "sic_industry_g_only_missing_neutral"
    assert any(row["factors"].get("G") is not None for row in payload["rows"])
    conservative = batch["variants"]["conservative|mid"]
    assert conservative["factor_capabilities"]["G"] == "sic_industry_g_only_missing_neutral"
    assert all(row["factors"].get("G") is None for row in project_strength_payload(conservative, parameters={})["rows"])


def test_worker_without_a_classification_publishes_the_v16_scores_and_records_the_fallback(tmp_path, monkeypatch):
    from app.services.eod_limited import market_data, worker
    from app.services.eod_limited.store import read_batch

    panel = build_panel()
    manifest = {"status": "complete", "eligible_count": len(panel), "complete_bar_count": len(panel),
                "source_hash": "test", "volume_session_scope": market_data.VOLUME_SCOPE}

    def fake_load(**kw):
        kw["on_directory"](directory_rows())
        return panel, [{"ticker": sid, "status": "ok", "bars": 370} for sid in panel], manifest

    monkeypatch.setattr(market_data, "load_all_market_panel", fake_load)
    # No table and no lookups: an industry mode on top of the v1.6 switches falls back to v1.6 scoring.
    config = LiveConfig(industry_mode=INDUSTRY_FULL, industry_lookup_budget=0,
                        conservative_policy=CONSERVATIVE_V14, fund_scope=FUND_SCOPE_ALL)
    result = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False, live_config=config)
    assert result["status"] == "RAN"
    batch = read_batch(tmp_path)
    assert batch["coverage"]["industry"]["status"] == "unavailable_scored_without_industry"
    variant = batch["variants"]["balanced|mid"]
    assert "factor_capabilities" not in variant  # nothing switched on in the end: the v1.6 payload
    rows = project_strength_payload(variant, parameters={})["rows"]
    assert rounded(rows) == GOLDEN["balanced/mid"]["rows"]


def test_v16_config_worker_batch_has_no_v17_keys(tmp_path, monkeypatch):
    from app.services.eod_limited import market_data, worker
    from app.services.eod_limited.store import read_batch

    panel = build_panel()
    manifest = {"status": "complete", "eligible_count": len(panel), "complete_bar_count": len(panel),
                "source_hash": "test", "volume_session_scope": market_data.VOLUME_SCOPE}
    monkeypatch.setattr(market_data, "load_all_market_panel", lambda **kw: (
        panel, [{"ticker": sid, "status": "ok", "bars": 370} for sid in panel], manifest))
    result = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False, live_config=V16_CONFIG)
    assert result["status"] == "RAN"
    batch = read_batch(tmp_path)
    assert "live_config" not in batch and "industry" not in batch["coverage"]
    for key, variant in batch["variants"].items():
        assert "factor_capabilities" not in variant and "v17_options" not in variant
        profile, horizon = key.split("|")
        assert rounded(project_strength_payload(variant, parameters={})["rows"]) == GOLDEN[f"{profile}/{horizon}"]["rows"]
