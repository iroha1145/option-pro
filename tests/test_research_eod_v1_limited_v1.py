"""Engineering contracts for LIMITED_CURRENT_UNIVERSE_V1. Not a market backtest."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC
from app.services.research_eod_v1.composite import m1_consensus
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES
from app.services.research_eod_v1.eod_shadow import publish_snapshot, read_snapshot
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.limited_v1 import (
    ALLOWED_END,
    EVIDENCE_STATUS,
    HOLDOUT_START,
    MODE,
    NETWORK_COUNTER,
    _compact_composite,
    acceptance_sessions,
    apply_price_only_track,
    close_task,
    current_universe_tickers,
    inventory_inputs,
    limited_config_hash,
    preview_html,
    run_limited_v1,
    score_session,
    write_preview,
)
from app.services.research_eod_v1.snapshot import compute_snapshot


def _panel(n: int = 80):
    days = trading_days(date(2023, 10, 2), n)
    nvda = make_series("NVDA", days, trending_close(n, 40, 0.12), theme_ids=("semiconductors", "ai_cloud"))
    amd = make_series("AMD", days, trending_close(n, 30, 0.10), theme_ids=("semiconductors",))
    spy = make_series("SPY", days, trending_close(n, 210, 0.07), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    qqq = make_series("QQQ", days, trending_close(n, 200, 0.06), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    return days, {"NVDA": nvda, "AMD": amd, "SPY": spy, "QQQ": qqq}


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
    assert "LIQUIDITY_HARD_GATE_UNVERIFIED" in scored["rows"][0]["rejection_reasons"]


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
    future = {}
    for sid, series in panel.items():
        grown = make_series(
            sid,
            days + extra,
            trending_close(len(days) + len(extra), 40, 0.12),
            theme_ids=series.theme_ids,
            asset_track=series.asset_track,
            security_type=series.venue_metadata.get("security_type", "CS"),
        )
        future[sid] = grown
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
