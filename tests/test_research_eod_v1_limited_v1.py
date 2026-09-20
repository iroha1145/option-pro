"""Engineering contracts for LIMITED_CURRENT_UNIVERSE_V1. Not a market backtest."""

from __future__ import annotations

import copy
import json
import socket
from datetime import date
from pathlib import Path

import numpy as np

from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC
from app.services.research_eod_v1.composite import m1_consensus
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES
from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.eod_shadow import publish_snapshot, read_snapshot
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.limited_v1 import (
    ALLOWED_END,
    COMPUTE_VERSION,
    DOLLAR_LIQUIDITY_UNVERIFIED,
    EVIDENCE_STATUS,
    HOLDOUT_START,
    MODE,
    NETWORK_COUNTER,
    RETURN_BASIS,
    VOLUME_SESSION_UNVERIFIED,
    _compact_composite,
    acceptance_sessions,
    apply_price_only_track,
    bars_to_panel,
    close_task,
    current_universe_tickers,
    dataset_hash,
    inventory_inputs,
    limited_config_hash,
    preview_html,
    row_compare_payload,
    run_limited_v1,
    score_session,
    source_dataset_hash,
    write_preview,
)
from app.services.research_eod_v1.residual import residual_raw_momentum
from app.services.research_eod_v1.snapshot import compute_snapshot


def _panel(n: int = 80):
    days = trading_days(date(2023, 10, 2), n)
    nvda = make_series("NVDA", days, trending_close(n, 40, 0.12), theme_ids=("semiconductors", "ai_cloud"))
    amd = make_series("AMD", days, trending_close(n, 30, 0.10), theme_ids=("semiconductors",))
    spy = make_series("SPY", days, trending_close(n, 210, 0.07), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    qqq = make_series("QQQ", days, trending_close(n, 200, 0.06), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    return days, {"NVDA": nvda, "AMD": amd, "SPY": spy, "QQQ": qqq}


def _extend_future(series, extra):
    """Append later bars without rewriting vintage or the historical close path."""

    if len(series.close) >= 2:
        drift = float(series.close[1] - series.close[0])
    else:
        drift = 0.0
    extra_close = np.asarray(series.close[-1] + drift * np.arange(1, len(extra) + 1), dtype=float)
    grown = make_series(
        series.security_id,
        list(series.dates) + list(extra),
        np.concatenate([np.asarray(series.close, dtype=float), extra_close]),
        theme_ids=series.theme_ids,
        asset_track=series.asset_track,
        security_type=series.venue_metadata.get("security_type", "CS"),
        industry_id=series.industry_id,
        parent_industry_id=series.parent_industry_id,
    )
    grown.source_available_at = series.source_available_at
    return grown


def test_acceptance_window_stays_in_development_zone() -> None:
    window = acceptance_sessions()
    assert len(window) == 20
    assert window[-1] == ALLOWED_END
    assert window[0] < window[-1]
    assert all(session < HOLDOUT_START for session in window)
    assert window == sorted(window)


def test_inventory_does_not_invent_missing_cache() -> None:
    rows = inventory_inputs()
    assert any(row["path"].endswith("bars.pkl") for row in rows)
    assert all("exists" in row and "bytes" in row for row in rows)


def test_g_missing_does_not_block_price_only_track() -> None:
    days, panel = _panel()
    registry = load_registry()
    payload = compute_snapshot(
        as_of_after_close(days[-1]),
        panel,
        "u_test",
        registry,
        sector_id="semiconductors",
        algorithm="A_trend_quality",
        source_finalized_through=days[-1],
    )
    scored = apply_price_only_track(
        payload,
        registry=registry,
        theme_id="semiconductors",
        family="A_trend_quality",
        profile="balanced",
        horizon="mid",
        volume_verified=True,
    )
    assert scored["track"] == PRICE_ONLY_DIAGNOSTIC
    assert scored["rows"]
    assert all(row.get("track") == PRICE_ONLY_DIAGNOSTIC for row in scored["rows"])
    assert all(row.get("momentum_basis") == "price_return_not_total_return" for row in scored["rows"])


def test_unverified_volume_demotes_b_and_c_to_watch() -> None:
    registry = load_registry()
    payload = {
        "rows": [
            {
                "security_id": "NVDA",
                "algorithm_id": "B_confirmed_base_breakout",
                "status": "eligible",
                "score": 80,
                "factors": {"T": 80, "M": 80, "S": 80, "B": 80, "P": 80, "V": 80, "R": 80, "G": None},
                "rejection_reasons": (),
            }
        ]
    }
    scored = apply_price_only_track(
        payload,
        registry=registry,
        theme_id="semiconductors",
        family="B_confirmed_base_breakout",
        profile="balanced",
        horizon="mid",
        volume_verified=False,
    )
    assert scored["rows"][0]["status"] == "watch"
    assert DOLLAR_LIQUIDITY_UNVERIFIED in scored["rows"][0]["rejection_reasons"] or "VOLUME_SESSION_UNVERIFIED" in scored["rows"][0]["rejection_reasons"]


def test_m1_does_not_revive_rejects_or_count_duplicate_themes() -> None:
    rows = [
        {"security_id": "AAA", "algorithm_id": "A_trend_quality", "status": "eligible", "score": 90, "factors": {"R": 70}, "sector_context": "software"},
        {"security_id": "AAA", "algorithm_id": "A_trend_quality", "status": "eligible", "score": 91, "factors": {"R": 70}, "sector_context": "ai_cloud"},
        {"security_id": "AAA", "algorithm_id": "D_residual_momentum", "status": "eligible", "score": 88, "factors": {"R": 70}, "sector_context": "software"},
        {"security_id": "BBB", "algorithm_id": "A_trend_quality", "status": "rejected", "score": 99, "factors": {"R": 70}, "sector_context": "software"},
    ]
    out = m1_consensus(rows, "balanced", 20)
    assert {row["security_id"] for row in out} == {"AAA"}
    assert len(out[0]["family_votes"]) == 2
    assert "BBB" not in {row["security_id"] for row in out}


def test_empty_composite_is_kept() -> None:
    assert m1_consensus([], "balanced", 20) == []


def test_last_n_keeps_newest_bars() -> None:
    days, panel = _panel(80)
    trimmed = panel["NVDA"].last_n(10)
    assert trimmed.dates == days[-10:]
    assert len(trimmed.close) == 10
    assert panel["NVDA"].last_n(800) is panel["NVDA"]


def test_compact_composite_keeps_family_votes() -> None:
    compact = _compact_composite({
        "security_id": "COST",
        "algorithm_id": "A_trend_quality",
        "status": "eligible",
        "score": 81.1,
        "consensus_z": 81.1,
        "family_votes": ("A_trend_quality", "D_residual_momentum"),
        "theme_count": 2,
        "R": 70,
        "G": None,
    })
    assert compact["family_votes"] == ["A_trend_quality", "D_residual_momentum"]
    assert compact["consensus_z"] == 81.1
    html = preview_html({
        "session_date": "2024-06-28",
        "composite_n": 1,
        "watch_n": 0,
        "theme_summaries": [],
        "composite_results": [compact],
        "watch_list": [],
        "limitations": [],
    })
    assert "A_trend_quality" in html
    assert "D_residual_momentum" in html


def test_score_session_and_replay_are_offline(tmp_path: Path) -> None:
    days, panel = _panel()
    registry = load_registry()
    before = NETWORK_COUNTER["yahoo_batch_downloads"]
    scored = score_session(
        panel,
        days[-1],
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert scored["mode"] == MODE
    assert scored["evidence_status"] == EVIDENCE_STATUS
    assert scored["session_date"] == days[-1].isoformat()
    assert scored["theme_summaries"][0]["theme_id"] == "semiconductors"
    assert isinstance(scored["composite_results"], list)
    published = close_task(
        panel=panel,
        session=days[-1],
        out_dir=tmp_path,
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )["published"]
    assert published["mode"] == MODE
    assert (tmp_path / "sessions" / days[-1].isoformat() / "summary.json").is_file()
    html = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert "历史 EOD 预览" in html
    assert "不是今日选股" in html
    assert "IC" not in html or "不展示 IC" in html
    assert NETWORK_COUNTER["yahoo_batch_downloads"] == before
    assert NETWORK_COUNTER["preview_provider_calls"] == 0


def test_future_bars_do_not_change_prior_signal() -> None:
    days, panel = _panel(90)
    extra = trading_days(days[-1], 6)[1:]
    registry = load_registry()
    first = score_session(
        panel,
        days[-1],
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    future = {sid: _extend_future(series, extra) for sid, series in panel.items()}
    assert all(len(future[sid].dates) > len(panel[sid].dates) for sid in panel)
    second = score_session(
        future,
        days[-1],
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert first["family_results"][0]["fingerprint"] == second["family_results"][0]["fingerprint"]
    assert first["config_hash"] == second["config_hash"]
    left = [row_compare_payload(row) for row in first["family_results"][0]["rows"]]
    right = [row_compare_payload(row) for row in second["family_results"][0]["rows"]]
    assert left == right


def test_publish_failure_retains_previous(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "snap.json"
    first = publish_snapshot(path, session_date="2024-06-27", config_hash="a", universe_version="u", rows=[{"security_id": "OLD", "status": "eligible"}])
    assert first["session_date"] == "2024-06-27"

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.research_eod_v1.eod_shadow.atomic_write_json", boom)
    retained = publish_snapshot(path, session_date="2024-06-28", config_hash="b", universe_version="u", rows=[{"security_id": "NEW"}])
    assert retained["integrity"] == "stale_previous_retained"
    assert retained["session_date"] == "2024-06-27"
    assert read_snapshot(path)["session_date"] == "2024-06-27"


def test_repeat_close_task_does_not_duplicate_identity(tmp_path: Path) -> None:
    days, panel = _panel()
    registry = load_registry()
    first = close_task(
        panel=panel,
        session=days[-1],
        out_dir=tmp_path,
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    second = close_task(
        panel=panel,
        session=days[-1],
        out_dir=tmp_path,
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert first["published"]["cache_key"] == second["published"]["cache_key"]
    assert first["scored"]["run_signature"] == second["scored"]["run_signature"]
    assert list((tmp_path / "sessions").iterdir()).__len__() == 1


def test_config_hash_changes_when_track_or_data_changes() -> None:
    registry = load_registry()
    a = limited_config_hash(registry=registry, profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="one")
    b = limited_config_hash(registry=registry, profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="two")
    c = limited_config_hash(registry=registry, profile="aggressive", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="one")
    assert a != b
    assert a != c


def test_run_limited_v1_synthetic_panel_writes_preview(tmp_path: Path) -> None:
    days, panel = _panel()
    report = run_limited_v1(
        out_dir=tmp_path,
        session=days[-1],
        replay_days=2,
        panel=panel,
        volume_verified=True,
        data_hash="synthetic-test",
        synthetic=True,
        smoke=False,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert report["status"] == "RAN"
    assert report["replay"]["ordered"] is True
    assert len(report["acceptance_sessions"]) == 2
    assert (tmp_path / "preview.html").is_file()
    assert (tmp_path / "research-eod-v1-snapshot.json").is_file()
    assert "AUTH_REQUIRED" not in json.dumps(report["replay"])
    assert report["load"]["wanted_n"] == len(current_universe_tickers())
    assert "NVDA" not in report["load"]["missing"]
    assert len(report["load"]["missing"]) == report["load"]["wanted_n"] - 4


def test_preview_only_reads_snapshot_without_yahoo(tmp_path: Path) -> None:
    snapshot = {
        "session_date": "2024-06-28",
        "mode": MODE,
        "evidence_status": EVIDENCE_STATUS,
        "profile": "balanced",
        "horizon": "mid",
        "composite_n": 0,
        "watch_n": 0,
        "theme_summaries": [{"theme_id": "software", "members_listed": 1, "members_in_panel": 1, "families": {}}],
        "composite_results": [],
        "watch_list": [],
        "limitations": ["test"],
    }
    before = NETWORK_COUNTER["yahoo_batch_downloads"]
    write_preview(tmp_path / "preview.html", snapshot)
    text = preview_html(snapshot)
    assert "2024-06-28" in text
    assert NETWORK_COUNTER["yahoo_batch_downloads"] == before


def test_holdout_session_is_rejected() -> None:
    days, panel = _panel()
    registry = load_registry()
    try:
        score_session(panel, HOLDOUT_START, registry=registry)
    except ValueError as exc:
        assert "development_zone" in str(exc)
    else:
        raise AssertionError("holdout must be rejected")


def test_profiles_and_horizons_are_registered() -> None:
    assert PROFILES == ("conservative", "balanced", "aggressive")
    assert HORIZONS == ("short", "mid", "long")
    assert len(ALGORITHMS) == 4
    assert len(current_universe_tickers()) >= 200


def _bar(symbol: str, session: date, close: float, tri: float) -> ResearchBar:
    return ResearchBar(
        security_id=symbol,
        session_date=session,
        open=close,
        high=close,
        low=close,
        close=close,
        raw_open=close,
        raw_close=close,
        volume=1_000_000,
        dollar_volume=close * 1_000_000,
        tri=tri,
        volume_scope="UNKNOWN",
    )


def test_config_hash_covers_weights_and_gates() -> None:
    registry = load_registry()
    base = limited_config_hash(registry=registry, profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="one")
    weights = copy.deepcopy(registry)
    theme = next(iter(weights["sectors"]))
    family = next(iter(weights["base_algorithm_weights"]))
    weights["base_algorithm_weights"][family]["T"] = float(weights["base_algorithm_weights"][family]["T"]) + 0.01
    gates = copy.deepcopy(registry)
    gates["sectors"][theme]["gates"] = {**gates["sectors"][theme]["gates"], "min_structure": 99}
    assert limited_config_hash(registry=weights, profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="one") != base
    assert limited_config_hash(registry=gates, profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="one") != base


def test_compute_hash_uses_declared_return_series_not_unused_tri() -> None:
    day = date(2024, 6, 27)
    original = {"AAA": [_bar("AAA", day, 100.0, 95.0)]}
    revised_tri = {"AAA": [_bar("AAA", day, 100.0, 110.0)]}
    revised_close = {"AAA": [_bar("AAA", day, 101.0, 95.0)]}
    assert dataset_hash(original) == dataset_hash(revised_tri)
    assert dataset_hash(original) != dataset_hash(revised_close)
    assert source_dataset_hash(original) != source_dataset_hash(revised_tri)


def test_theme_order_is_not_economic_industry() -> None:
    day = date(2024, 6, 27)
    bars = {"NVDA": [_bar("NVDA", day, 100.0, 105.0)]}
    first, _ = bars_to_panel(bars, {"NVDA": ["semiconductors", "ai_cloud"]})
    reversed_order, _ = bars_to_panel(bars, {"NVDA": ["ai_cloud", "semiconductors"]})
    assert first["NVDA"].industry_id is None
    assert reversed_order["NVDA"].industry_id is None
    assert first["NVDA"].parent_industry_id is None
    spy = make_series("SPY", [day], trending_close(1, 200, 0.0), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    left = residual_raw_momentum(first["NVDA"], spy, {"NVDA": first["NVDA"], "SPY": spy})
    right = residual_raw_momentum(reversed_order["NVDA"], spy, {"NVDA": reversed_order["NVDA"], "SPY": spy})
    assert left.raw == right.raw
    assert left.status == right.status


def test_declared_price_return_reads_close_and_keeps_vendor_tri() -> None:
    day1, day2 = date(2024, 6, 26), date(2024, 6, 27)
    bars = {"AAA": [_bar("AAA", day1, 95.0, 95.0), _bar("AAA", day2, 95.0, 100.0)]}
    panel, _ = bars_to_panel(bars, {"AAA": ["software"]})
    series = panel["AAA"]
    assert series.return_basis == RETURN_BASIS
    assert float(series.tri[-1]) == float(series.close[-1]) == 95.0
    assert series.vendor_tri is not None
    assert float(series.vendor_tri[-1]) == 100.0
    assert (series.tri[-1] / series.tri[0] - 1.0) == 0.0


def test_rescore_only_missing_g_rejection_becomes_eligible() -> None:
    registry = load_registry()
    payload = {
        "rows": [
            {
                "security_id": "NVDA",
                "algorithm_id": "A_trend_quality",
                "status": "rejected",
                "score": None,
                "factors": {"T": 90, "M": 90, "S": 90, "B": 90, "P": 90, "V": 90, "R": 90, "G": None},
                "rejection_reasons": ["MISSING_SCORE"],
            }
        ]
    }
    scored = apply_price_only_track(
        payload,
        registry=registry,
        theme_id="semiconductors",
        family="A_trend_quality",
        profile="balanced",
        horizon="mid",
        volume_verified=True,
    )
    assert scored["rows"][0]["score"] is not None
    assert scored["rows"][0]["score"] >= 89
    assert scored["rows"][0]["status"] == "eligible"
    assert "MISSING_SCORE" not in scored["rows"][0]["rejection_reasons"]


def test_common_dollar_liquidity_policy_applies_to_all_families() -> None:
    registry = load_registry()
    statuses = {}
    for family in ALGORITHMS:
        scored = apply_price_only_track(
            {
                "rows": [
                    {
                        "security_id": "NVDA",
                        "algorithm_id": family,
                        "status": "eligible",
                        "score": 90,
                        "factors": {"T": 90, "M": 90, "S": 90, "B": 90, "P": 90, "V": 90, "R": 90, "G": None},
                        "rejection_reasons": (),
                    }
                ]
            },
            registry=registry,
            theme_id="semiconductors",
            family=family,
            profile="balanced",
            horizon="mid",
            volume_verified=False,
        )
        statuses[family] = scored["rows"][0]["status"]
        assert DOLLAR_LIQUIDITY_UNVERIFIED in scored["rows"][0]["rejection_reasons"]
    assert set(statuses.values()) == {"watch"}


def test_m1_separates_stock_and_etf_tracks() -> None:
    days, panel = _panel(80)
    registry = load_registry()
    scored = score_session(
        panel,
        days[-1],
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors", "etfs"),
        algorithms=("A_trend_quality", "D_residual_momentum"),
    )
    stock_ids = {row["security_id"] for row in scored["composite_stock"]}
    etf_ids = {row["security_id"] for row in scored["composite_etf"]}
    assert stock_ids.isdisjoint(etf_ids)
    assert all(row.get("stock_or_etf_track") != "etf" for row in scored["composite_stock"])
    assert all(row.get("stock_or_etf_track") == "etf" for row in scored["composite_etf"])


def test_future_bars_on_warm_series_keep_prior_outputs() -> None:
    days = trading_days(date(2023, 1, 3), 370)
    assert days[-1] < HOLDOUT_START
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(370, 40, 0.12), theme_ids=("semiconductors",)),
        "AMD": make_series("AMD", days, trending_close(370, 30, 0.10), theme_ids=("semiconductors",)),
        "SPY": make_series("SPY", days, trending_close(370, 210, 0.07), theme_ids=("etfs",), asset_track="etf", security_type="ETF"),
        "QQQ": make_series("QQQ", days, trending_close(370, 200, 0.06), theme_ids=("etfs",), asset_track="etf", security_type="ETF"),
    }
    extra = trading_days(days[-1], 8)[1:]
    registry = load_registry()
    first = score_session(
        panel,
        days[-1],
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    future = {sid: _extend_future(series, extra) for sid, series in panel.items()}
    assert all(len(future[sid].dates) == 370 + len(extra) for sid in panel)
    second = score_session(
        future,
        days[-1],
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    left = [row_compare_payload(row) for row in first["family_results"][0]["rows"]]
    right = [row_compare_payload(row) for row in second["family_results"][0]["rows"]]
    assert left == right
    assert left
    assert any(row["factors"] for row in left)


def test_score_and_preview_make_no_socket_calls(monkeypatch) -> None:
    days, panel = _panel()
    seen: list[str] = []

    def blocked(self, address, *args, **kwargs):
        seen.append(str(address))
        raise AssertionError(f"unexpected socket connect: {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    scored = score_session(
        panel,
        days[-1],
        registry=load_registry(),
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    html = preview_html(scored)
    assert seen == []
    assert "主题家族明细" in html
    assert "NVDA" in html
    assert COMPUTE_VERSION in html


def test_hard_reject_is_not_revived() -> None:
    registry = load_registry()
    scored = apply_price_only_track(
        {
            "rows": [
                {
                    "security_id": "NVDA",
                    "algorithm_id": "A_trend_quality",
                    "status": "rejected",
                    "score": 99,
                    "factors": {"T": 90, "M": 90, "S": 90, "B": 90, "P": 90, "V": 90, "R": 90, "G": None},
                    "rejection_reasons": ["SETUP_NOT_MET", "MISSING_SCORE"],
                }
            ]
        },
        registry=registry,
        theme_id="semiconductors",
        family="A_trend_quality",
        profile="balanced",
        horizon="mid",
        volume_verified=True,
    )
    assert scored["rows"][0]["status"] == "rejected"
    assert "SETUP_NOT_MET" in scored["rows"][0]["rejection_reasons"]
    assert "MISSING_SCORE" not in scored["rows"][0]["rejection_reasons"]


def test_empty_symbol_preserved_in_coverage() -> None:
    day = date(2024, 6, 27)
    bars = {"AAA": [_bar("AAA", day, 100.0, 105.0)]}
    appearances = {"AAA": ["software"], "MISSING": ["software"]}
    panel, coverage = bars_to_panel(bars, appearances)
    assert "AAA" in panel
    assert "MISSING" not in panel
    empty = next(row for row in coverage if row["ticker"] == "MISSING")
    assert empty["status"] == "empty"
    assert empty["bars"] == 0


def test_config_hash_covers_theme_members() -> None:
    from app.services.sectors import SECTORS

    registry = load_registry()
    base = limited_config_hash(registry=registry, profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC, data_hash="one")
    members = {theme_id: list(sector["tickers"]) for theme_id, sector in SECTORS.items()}
    members["semiconductors"] = list(members["semiconductors"]) + ["ZZZZTEST"]
    assert limited_config_hash(
        registry=registry,
        profile="balanced",
        horizon="mid",
        track=PRICE_ONLY_DIAGNOSTIC,
        data_hash="one",
        theme_members=members,
    ) != base


def test_session_volume_unknown_is_separate_from_dollar_basis() -> None:
    registry = load_registry()
    session_only = {}
    dollar_only = {}
    for family in ALGORITHMS:
        session_scored = apply_price_only_track(
            {
                "rows": [
                    {
                        "security_id": "NVDA",
                        "algorithm_id": family,
                        "status": "eligible",
                        "score": 90,
                        "factors": {"T": 90, "M": 90, "S": 90, "B": 90, "P": 90, "V": 90, "R": 90, "G": None},
                        "rejection_reasons": (),
                    }
                ]
            },
            registry=registry,
            theme_id="semiconductors",
            family=family,
            profile="balanced",
            horizon="mid",
            volume_verified=True,
            dollar_liquidity_verified=True,
            volume_session_verified=False,
        )
        dollar_scored = apply_price_only_track(
            {
                "rows": [
                    {
                        "security_id": "NVDA",
                        "algorithm_id": family,
                        "status": "eligible",
                        "score": 90,
                        "factors": {"T": 90, "M": 90, "S": 90, "B": 90, "P": 90, "V": 90, "R": 90, "G": None},
                        "rejection_reasons": (),
                    }
                ]
            },
            registry=registry,
            theme_id="semiconductors",
            family=family,
            profile="balanced",
            horizon="mid",
            volume_verified=False,
            dollar_liquidity_verified=False,
            volume_session_verified=True,
        )
        session_only[family] = session_scored["rows"][0]
        dollar_only[family] = dollar_scored["rows"][0]
    assert session_only["A_trend_quality"]["status"] == "eligible"
    assert session_only["D_residual_momentum"]["status"] == "eligible"
    assert session_only["B_confirmed_base_breakout"]["status"] == "watch"
    assert session_only["C_trend_pullback"]["status"] == "watch"
    assert VOLUME_SESSION_UNVERIFIED in session_only["B_confirmed_base_breakout"]["rejection_reasons"]
    assert DOLLAR_LIQUIDITY_UNVERIFIED not in session_only["B_confirmed_base_breakout"]["rejection_reasons"]
    assert {row["status"] for row in dollar_only.values()} == {"watch"}
    assert all(DOLLAR_LIQUIDITY_UNVERIFIED in row["rejection_reasons"] for row in dollar_only.values())
    assert VOLUME_SESSION_UNVERIFIED not in dollar_only["A_trend_quality"]["rejection_reasons"]
    assert VOLUME_SESSION_UNVERIFIED not in dollar_only["B_confirmed_base_breakout"]["rejection_reasons"]


def test_close_task_publish_failure_keeps_old_and_marks_stale(tmp_path: Path, monkeypatch) -> None:
    days, panel = _panel()
    registry = load_registry()
    first = close_task(
        panel=panel,
        session=days[-2],
        out_dir=tmp_path,
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    previous = dict(first["published"])

    def fail_publish(path, scored):
        return {**previous, "integrity": "stale_previous_retained", "publish_failed": True}

    monkeypatch.setattr("app.services.research_eod_v1.limited_v1.publish_limited_snapshot", fail_publish)
    second = close_task(
        panel=panel,
        session=days[-1],
        out_dir=tmp_path,
        registry=registry,
        volume_verified=True,
        data_hash="synthetic-test",
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert second["publish_failed"] is True
    assert second["published"]["session_date"] == first["published"]["session_date"]
    assert read_snapshot(tmp_path / "research-eod-v1-snapshot.json")["session_date"] == first["published"]["session_date"]
    html = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert "stale" in html.lower() or "过期" in html
    assert days[-1].isoformat() in html
    assert (tmp_path / "sessions" / days[-1].isoformat() / "publish_failed.json").is_file()
    assert not (tmp_path / "sessions" / days[-1].isoformat() / "summary.json").is_file()


def test_synthetic_flag_does_not_relabel_yahoo_cache(tmp_path: Path) -> None:
    report = run_limited_v1(
        out_dir=tmp_path,
        session=date(2024, 6, 28),
        replay_days=1,
        synthetic=True,
        smoke=False,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert report["synthetic"] is True
    assert report["load"]["sources"] == ["synthetic_fixture_panel"]
    html = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert "SYNTHETIC" in html
