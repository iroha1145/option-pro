"""PR #174 algorithm-round-1 regressions. Isolation on 155685da plus new signatures."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.research_eod_v1.ablation import (
    ABLATION_FACTORS,
    BASELINE_ID,
    DATE_BLOCKS,
    NEIGHBOR_SPECS,
    analyze_rows,
    iter_variants,
    preregistered_neighbors,
)
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of, next_session
from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    PRICE_ONLY_DIAGNOSTIC,
    build_coverage_matrix,
    diagnostic_weights,
    family_required,
    g_is_available,
    renormalize,
    rescore_row,
)
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close
from app.services.research_eod_v1.ledger import size_notional
from app.services.research_eod_v1.measurement import (
    _setup_or_episode,
    _signal_episode_id,
    run_snapshot_matrix,
)
from app.services.research_eod_v1.runs import CheckpointSignatureError, committed_row_key
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.stats import register_independent_events
from registry import resolve_weights  # type: ignore


PROBE_DATA_INSUFFICIENT = {
    ("semiconductors", "D_residual_momentum"),
    ("ai_cloud", "D_residual_momentum"),
    ("finance", "D_residual_momentum"),
    ("retail", "D_residual_momentum"),
    ("energy", "A_trend_quality"),
    ("energy", "D_residual_momentum"),
    ("utilities", "D_residual_momentum"),
    ("defense_aero", "D_residual_momentum"),
    ("airlines", "D_residual_momentum"),
    ("real_estate", "D_residual_momentum"),
    ("crypto", "D_residual_momentum"),
    ("china_adr", "D_residual_momentum"),
    ("telecom", "D_residual_momentum"),
    ("industrials", "D_residual_momentum"),
}
TEN_PCT = {
    "max_position_fraction": 0.10,
    "position_risk_budget_fraction": 1.0,
    "max_order_adv_fraction": 1.0,
}


def _tiny_panel():
    days = trading_days(date(2018, 1, 2), 430)
    panel = {}
    for i, name in enumerate(["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "QCOM", "ASML", "AMAT", "LRCX", "KLAC"]):
        series = make_series(
            name,
            days,
            trending_close(len(days), 20 + i, 0.08 + i * 0.002),
            industry_id=None,
            parent_industry_id=None,
            theme_ids=("semiconductors",),
        )
        series.venue_metadata = dict(series.venue_metadata)
        series.venue_metadata["industry_source"] = "unverified_economic_industry_missing"
        panel[name] = series
    panel["SPY"] = make_series(
        "SPY", days, trending_close(len(days), 200, 0.04), asset_track="etf", security_type="ETF", theme_ids=("etfs",)
    )
    panel["QQQ"] = make_series(
        "QQQ", days, trending_close(len(days), 180, 0.05), asset_track="etf", security_type="ETF", theme_ids=("etfs",)
    )
    return days, panel


def test_same_signature_resume_does_not_recompute(tmp_path: Path) -> None:
    days, panel = _tiny_panel()
    sessions = days[-4:-1]
    registry = load_registry()
    common = dict(
        panel=panel,
        sessions=sessions,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=registry,
        profile="balanced",
        horizon="mid",
        registry_version=1,
        checkpoint_path=tmp_path / "ckpt.json",
    )
    first = run_snapshot_matrix(**common)
    second = run_snapshot_matrix(**common)
    assert second["function_calls"] == first["function_calls"]
    assert second["function_calls"] > 0
    assert first["run_signature"] == second["run_signature"]


def test_resume_probe_rejects_stale_profile_horizon_registry(tmp_path: Path) -> None:
    days, panel = _tiny_panel()
    sessions = days[-3:-1]
    registry = load_registry()
    ckpt = tmp_path / "ckpt.json"
    first = run_snapshot_matrix(
        panel,
        sessions,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=registry,
        profile="balanced",
        horizon="mid",
        registry_version=1,
        checkpoint_path=ckpt,
    )
    stale_reuse_observed = False
    for kwargs in (
        {"profile": "aggressive", "horizon": "mid", "registry_version": 1},
        {"profile": "balanced", "horizon": "long", "registry_version": 1},
        {"profile": "balanced", "horizon": "mid", "registry_version": 2},
    ):
        with pytest.raises(CheckpointSignatureError):
            run_snapshot_matrix(
                panel,
                sessions,
                themes=("semiconductors",),
                algorithms=("A_trend_quality",),
                registry=registry,
                checkpoint_path=ckpt,
                **kwargs,
            )
    isolated = run_snapshot_matrix(
        panel,
        sessions,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=registry,
        profile="aggressive",
        horizon="long",
        registry_version=2,
        checkpoint_path=tmp_path / "other.json",
    )
    assert isolated["run_signature"] != first["run_signature"]
    assert isolated["function_calls"] > 0
    assert stale_reuse_observed is False


def test_crash_after_rows_without_checkpoint_does_not_duplicate(tmp_path: Path) -> None:
    days, panel = _tiny_panel()
    sessions = days[-3:-1]
    registry = load_registry()
    store = tmp_path / "rows.jsonl"
    first = run_snapshot_matrix(
        panel,
        sessions,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=registry,
        attach_labels=True,
        last_allowed=days[-1],
        row_store_path=store,
    )
    assert store.exists()
    before = store.read_text(encoding="utf-8").splitlines()
    second = run_snapshot_matrix(
        panel,
        sessions,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=registry,
        attach_labels=True,
        last_allowed=days[-1],
        row_store_path=store,
        checkpoint_path=tmp_path / "late.json",
    )
    after = store.read_text(encoding="utf-8").splitlines()
    keys = []
    for line in after:
        rec = json.loads(line)
        keys.append(
            committed_row_key(
                session=str(rec["signal_session"]),
                theme_id=str(rec["theme_id"]),
                algorithm=str(rec["algorithm"]),
                profile=str(rec["profile"]),
                horizon=str(rec["horizon"]),
                security_id=str(rec["security_id"]),
                label_horizon=rec.get("label_horizon"),
            )
        )
    assert len(after) == len(before)
    assert len(keys) == len(set(keys))
    assert second["unique_committed_rows"] == len(set(keys))
    assert first["unique_committed_rows"] == len(set(keys))


def test_weekend_and_holiday_are_not_new_events() -> None:
    friday = date(2024, 1, 5)
    monday = date(2024, 1, 8)
    holiday_friday = date(2024, 1, 12)
    after_mlk = date(2024, 1, 16)
    tuesday = date(2024, 1, 9)
    assert next_session(friday) == monday
    assert next_session(holiday_friday) == after_mlk
    shared = {
        "security_id": "AAA",
        "algorithm": "A_trend_quality",
        "profile": "balanced",
        "horizon": "mid",
        "label_horizon": 5,
        "final_eligible": True,
        "status": "eligible",
    }
    weekend = register_independent_events(
        [{**shared, "signal_session": friday}, {**shared, "signal_session": monday}],
        continuous_calendar=True,
    )
    holiday = register_independent_events(
        [{**shared, "signal_session": holiday_friday}, {**shared, "signal_session": after_mlk}],
        continuous_calendar=True,
    )
    gap = register_independent_events(
        [{**shared, "signal_session": friday}, {**shared, "signal_session": tuesday}],
        continuous_calendar=True,
    )
    assert weekend["deduped_event_groups"] == 1
    assert holiday["deduped_event_groups"] == 1
    assert gap["deduped_event_groups"] == 2
    assert weekend["independent_events"] is None
    assert weekend["old_count_4514"] == "SUPERSEDED_EVENT_COUNT"
    assert weekend["usable_for_independent_sample"] is False


def test_b_keeps_frozen_setup_id_others_use_episode() -> None:
    row = {
        "security_id": "AAA",
        "session_date": "2024-01-05",
        "status": "eligible",
        "frozen_setup": {"setup_id": "AAA:2024-01-05:w20"},
    }
    family_b = _setup_or_episode(row, "semiconductors", "B_confirmed_base_breakout", "balanced", "mid")
    family_a = _setup_or_episode(row, "semiconductors", "A_trend_quality", "balanced", "mid")
    assert family_b == "AAA:2024-01-05:w20"
    assert family_a == _signal_episode_id(row, "semiconductors", "A_trend_quality", "balanced", "mid")
    assert family_a != "eligible"
    assert family_a != "rejected"
    missing_b = _setup_or_episode(
        {"security_id": "AAA", "session_date": "2024-01-05", "status": "rejected"},
        "semiconductors",
        "B_confirmed_base_breakout",
        "balanced",
        "mid",
    )
    assert missing_b is None


def test_coverage_matrix_matches_g_missing_probe() -> None:
    matrix = build_coverage_matrix()
    assert len(matrix) == 96
    failed = {(row["theme"], row["family"]) for row in matrix if row["score_status"] == "DATA_INSUFFICIENT"}
    scored = {(row["theme"], row["family"]) for row in matrix if row["score_status"] == "SCORED_NOT_SETUP_VALIDATED"}
    assert failed == PROBE_DATA_INSUFFICIENT
    assert len(failed) + len(scored) == 96
    semi_d = next(row for row in matrix if row["theme"] == "semiconductors" and row["family"] == "D_residual_momentum")
    assert semi_d["G_weight"] == pytest.approx(0.11188811188881119)
    assert semi_d["maximum_coverage_without_G"] == pytest.approx(0.8881118881111888)
    assert semi_d["score_with_other_seven_factors_at_100"] is None
    assert "data-capability" in semi_d["note"]


def test_diagnostic_tracks_are_named_and_eight_key() -> None:
    registry = load_registry()
    weights = resolve_weights(registry, "semiconductors", "D_residual_momentum", "balanced", "mid")
    price_only = diagnostic_weights(weights, track=PRICE_ONLY_DIAGNOSTIC, family="D_residual_momentum")
    market = diagnostic_weights(weights, track=D_MARKET_RESIDUAL_DIAGNOSTIC, family="D_residual_momentum")
    assert set(price_only) == set(weights)
    assert price_only["G"] == 0.0
    assert market["G"] == 0.0
    assert sum(price_only.values()) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        diagnostic_weights(weights, track=D_MARKET_RESIDUAL_DIAGNOSTIC, family="A_trend_quality")
    dropped = renormalize(weights, {"G"})
    assert dropped["G"] == 0.0
    assert abs(sum(dropped.values()) - 1.0) < 1e-12


def test_g_ablation_is_absent_when_coverage_fails() -> None:
    registry = load_registry()
    matrix = { (row["theme"], row["family"]): row for row in build_coverage_matrix(registry=registry) }
    semi_d = iter_variants("semiconductors", "D_residual_momentum", registry, matrix[("semiconductors", "D_residual_momentum")])
    software_d = iter_variants("software", "D_residual_momentum", registry, matrix[("software", "D_residual_momentum")])
    assert not g_is_available(matrix[("semiconductors", "D_residual_momentum")])
    assert g_is_available(matrix[("software", "D_residual_momentum")])
    assert "ABLATION_DROP_G" not in {item.variant_id for item in semi_d}
    assert "ABLATION_DROP_G" in {item.variant_id for item in software_d}
    assert PRICE_ONLY_DIAGNOSTIC in {item.variant_id for item in semi_d}
    assert D_MARKET_RESIDUAL_DIAGNOSTIC in {item.variant_id for item in semi_d}
    assert {spec["variant_id"] for spec in NEIGHBOR_SPECS} <= {item.variant_id for item in semi_d}
    assert [block["id"] for block in DATE_BLOCKS][:3] == ["Y2018", "Y2019", "Y2020"]
    assert [spec["variant_id"] for spec in preregistered_neighbors()] == [
        "N_M_PLUS_5PP",
        "N_S_PLUS_5PP",
        "N_SCORE_FLOOR_PLUS_10PCT",
    ]


def test_rescore_keeps_hard_gates_and_uses_scorer_universe() -> None:
    weights = {key: 0.125 for key in ("T", "M", "S", "B", "P", "V", "R", "G")}
    row = {
            "factors": {"T": 80, "M": 80, "S": 80, "B": 80, "P": 80, "V": 80, "R": 80, "G": 80},
        "rejection_reasons": ["SETUP_NOT_MET", "LOW_SCORE", "MISSING_SCORE"],
    }
    scored = rescore_row(row, weights, coverage_min=0.9, required=family_required("A_trend_quality"), score_floor=74)
    assert scored["score"] == pytest.approx(80.0)
    assert scored["final_eligible"] is False
    assert "SETUP_NOT_MET" in scored["rejection_reasons"]
    assert "LOW_SCORE" not in scored["rejection_reasons"]
    assert "MISSING_SCORE" not in scored["rejection_reasons"]


def test_analyze_rows_pairs_baseline_and_ablations() -> None:
    registry = load_registry()
    rows = []
    for i in range(12):
        rows.append(
            {
                "security_id": f"N{i:02d}",
                "signal_session": "2023-06-12",
                "theme_id": "software",
                "algorithm": "A_trend_quality",
                "profile": "balanced",
                "horizon": "mid",
                "label_horizon": 5,
                "snapshot_key": f"2023-06-12|software|A|N{i:02d}",
                "factors": {"T": 60 + i, "M": 70, "S": 65, "B": 50, "P": 40, "V": 55, "R": 80, "G": 50},
                "score": 70.0,
                "label": 0.01 * i,
                "final_eligible": i >= 8,
                "status": "eligible" if i >= 8 else "rejected",
                "rejection_reasons": [] if i >= 8 else ["LOW_SCORE"],
            }
        )
    out = analyze_rows(rows, registry, profile="balanced", horizon="mid")
    assert out["inspected_outcomes"] == 12
    assert out["unique_snapshots"] == 12
    assert out["actual_rescore_calls"] == 12 * len(iter_variants("software", "A_trend_quality", registry, {"score_status": "SCORED_NOT_SETUP_VALIDATED"}))
    ids = {item["variant_id"] for item in out["pairing"] if item["theme"] == "software" and item["family"] == "A_trend_quality"}
    assert BASELINE_ID in ids
    assert {f"ABLATION_DROP_{factor}" for factor in ABLATION_FACTORS} <= ids
    assert out["events"]["independent_events"] is None
    assert out["events"]["old_count_4514"] == "SUPERSEDED_EVENT_COUNT"
    card = next(item for item in out["theme_cards"] if item["theme"] == "software")
    assert card["families"][0]["next_neighbors"] == [spec["variant_id"] for spec in NEIGHBOR_SPECS]


def test_snapshot_rows_carry_geometry_close() -> None:
    days, panel = _tiny_panel()
    session = days[-1]
    payload = compute_snapshot(
        eod_evaluation_as_of(session),
        panel,
        "u_geometry",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
        source_finalized_through=session,
    )
    assert payload["rows"]
    for row in payload["rows"]:
        assert "geometry_close" in row
        assert "risk_distance_fraction" in row
        if row.get("geometry_close"):
            assert row["geometry_close"] > 0
    live = next((row for row in payload["rows"] if row.get("planned_invalidation") and row.get("geometry_close")), None)
    if live is not None:
        notional = size_notional(live, panel[live["security_id"]], session, 10_000.0, TEN_PCT, True)
        assert notional >= 0.0


def test_committed_row_key_includes_profile_horizon_label() -> None:
    left = committed_row_key(
        session="2023-06-12",
        theme_id="software",
        algorithm="A_trend_quality",
        profile="balanced",
        horizon="mid",
        security_id="AAA",
        label_horizon=5,
    )
    right = committed_row_key(
        session="2023-06-12",
        theme_id="software",
        algorithm="A_trend_quality",
        profile="aggressive",
        horizon="long",
        security_id="AAA",
        label_horizon=20,
    )
    assert left != right
