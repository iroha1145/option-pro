"""PR #174 data-readiness contracts. Inventory and layers, not a new grid."""

from __future__ import annotations

import json
import pickle
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.data_readiness import (
    ALLOWED_END,
    AUTOMOTIVE_MEMBERS,
    HOLDOUT_START,
    LAYER_A,
    LAYER_C,
    LAYER_D,
    TRUSTED_RELATIVE_PICKLES,
    automotive_descriptive,
    capability_matrix,
    decade_budget,
    inspect_symbol_bars,
    inventory_sources,
    is_trusted_pickle,
    load_trusted_research_bars,
    local_verification_arithmetic,
    next_unique_gap,
    stage_status,
    stop_rule,
    theme_capability_table,
    verify_contract_bars,
)
from app.services.sectors import SECTORS

EVIDENCE = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/evidence/local_verification.json"
FREEZE_VALIDATION = (
    Path(__file__).resolve().parents[1]
    / "research/option_pro_us_eod_v1/return_pack/algorithm_round2_freeze_validation.json"
)
ET = ZoneInfo("America/New_York")


def _bar(session: date, close: float, *, raw: float | None = None, tri: float | None = None, volume: float = 100.0) -> ResearchBar:
    return ResearchBar(
        security_id="AAA",
        session_date=session,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        raw_open=close if raw is None else raw,
        raw_close=close if raw is None else raw,
        volume=volume,
        dollar_volume=close * volume,
        tri=close if tri is None else tri,
        price_adjustment="yahoo_unverified_raw",
        economic_known_at=datetime(session.year, session.month, session.day, 16, 0, tzinfo=ET),
        vintage_status="download_time_not_pit",
    )


def test_untrusted_pickle_is_refused(tmp_path: Path) -> None:
    mystery = tmp_path / "random.pkl"
    mystery.write_bytes(pickle.dumps({"AAA": [_bar(date(2020, 1, 2), 10.0)]}))
    assert is_trusted_pickle(mystery, root=tmp_path) is False
    with pytest.raises(ValueError, match="untrusted pickle"):
        load_trusted_research_bars(mystery, root=tmp_path)


def test_trusted_project_pickle_round_trips(tmp_path: Path) -> None:
    target = tmp_path / TRUSTED_RELATIVE_PICKLES[0]
    target.parent.mkdir(parents=True)
    payload = {"SPY": [_bar(date(2020, 1, 2), 10.0), _bar(date(2020, 1, 3), 10.5)]}
    target.write_bytes(pickle.dumps(payload))
    loaded = load_trusted_research_bars(target, root=tmp_path)
    assert list(loaded) == ["SPY"]
    assert loaded["SPY"][0].close == 10.0


def test_offline_replay_is_not_the_whole_inventory() -> None:
    rows = inventory_sources()
    roles = {row["role"] for row in rows}
    assert "continuous_runner_yahoo_cache" in roles
    assert "offline_replay_spy_nvda_fixture" in roles
    assert "b0_feature_label_tape" in roles
    fixture = next(row for row in rows if row["role"] == "offline_replay_spy_nvda_fixture")
    yahoo = next(row for row in rows if row["role"] == "continuous_runner_yahoo_cache")
    assert fixture["authorization"] != yahoo["authorization"]
    assert "not_a_substitute" in fixture["authorization"]


def test_b0_tape_is_not_raw_eod() -> None:
    rows = inventory_sources()
    matrix = capability_matrix(rows, None)
    assert matrix["sources"]["b0_tape"]["is_raw_eod"] is False
    assert matrix["b0_feature_version"] != matrix["recovery_dataset_version"]
    assert matrix["versions_not_spliced"] is True
    assert matrix["invent_neutral_g"] is False


def test_layer_c_blocks_when_raw_copies_close() -> None:
    summary = inspect_symbol_bars([_bar(date(2020, 1, 2), 10.0), _bar(date(2020, 1, 3), 10.2)])
    assert summary["raw_ne_close"] == 0
    assert summary["ohlc_violations"] == 0
    matrix = capability_matrix(inventory_sources(), {"spy": summary})
    assert matrix["layers"][LAYER_A]["status"] == "available"
    assert matrix["layers"][LAYER_C]["status"] == "blocked"
    assert matrix["layers"][LAYER_C]["silent_raw_equals_close"] is True
    assert matrix["layers"][LAYER_D]["status"] == "current_list_only"


def test_ohlc_and_duplicate_fail_contract() -> None:
    from app.services.research_eod_v1.data.contract import validate_research_bars

    bad = _bar(date(2020, 1, 2), 10.0)
    broken = ResearchBar(**{**bad.__dict__, "high": 9.0})
    assert verify_contract_bars([broken])["ohlc_violations"] == 1
    assert "duplicate" in verify_contract_bars(
        [_bar(date(2020, 1, 2), 10.0), _bar(date(2020, 1, 2), 11.0)]
    )["contract"]
    with pytest.raises(ValueError, match="duplicate"):
        validate_research_bars([_bar(date(2020, 1, 2), 10.0), _bar(date(2020, 1, 2), 11.0)])


def test_decade_budget_does_not_use_holdout() -> None:
    start = date(2018, 1, 2)
    sessions = [date.fromordinal(start.toordinal() + day) for day in range(400)]
    sessions = [session for session in sessions if session < HOLDOUT_START]
    budget = decade_budget(sessions)
    assert budget["holdout_used_for_labels"] is False
    assert budget["ten_year_evaluable"] is False
    assert budget["label_63_is_not_horizon_long"] is True
    assert budget["allowed_end"] <= ALLOWED_END.isoformat()
    assert budget["families"]["D_residual_momentum"]["warmup_sessions"] == 330


def test_theme_table_covers_twenty_four_without_pit() -> None:
    fake = {ticker: [_bar(date(2018, 1, 2), 10.0)] for ticker in ("SPY", *AUTOMOTIVE_MEMBERS)}
    for ticker in fake:
        fake[ticker][0] = ResearchBar(**{**fake[ticker][0].__dict__, "security_id": ticker})
    rows = theme_capability_table(fake)
    assert {row["theme"] for row in rows} == set(SECTORS)
    assert len(rows) == 24
    assert all(row["membership"] == "CURRENT_LIST_ONLY" for row in rows)
    assert all(row["historical_theme_rebuild"] == "NOT_AVAILABLE" for row in rows)
    assert all(row["new_version_minted"] is False for row in rows)
    auto = next(row for row in rows if row["theme"] == "automotive")
    assert auto["missing_from_cache"] == []


def test_stage_status_corrects_composite_label() -> None:
    status = stage_status()
    assert "已有候选实现" in status["composite_layer"]["status"]
    assert status["composite_layer"]["not"] == "没有综合层"
    assert status["freeze_small_pool"]["do_not_rerun_1152"] is True
    assert status["family_e_weekly_volume_macro_radar_composite"]["cancelled_by_freeze"] is False
    assert status["holdout"]["unsealed"] is False


def test_automotive_descriptive_skips_holdout_and_does_not_invent_nav() -> None:
    sessions = []
    start = date(2024, 5, 1)
    day = start
    while len(sessions) < 30:
        if day.weekday() < 5:
            sessions.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    bars: dict[str, list[ResearchBar]] = {}
    for symbol in (*AUTOMOTIVE_MEMBERS, "SPY"):
        series = []
        price = 100.0
        for session in sessions:
            series.append(
                ResearchBar(
                    **{
                        **_bar(session, price).__dict__,
                        "security_id": symbol,
                    }
                )
            )
            price += 0.2
        bars[symbol] = series
    result = automotive_descriptive(bars)
    assert result["nav_winrate_capacity"] == "NOT_INVENTED"
    assert result["holdout_opens_used"] is False
    assert result["weights_not_retuned"] is True
    if result["next_day_confirm_n"]:
        last = result["public_rows"][-1]["session"]
        assert last < HOLDOUT_START.isoformat()


def test_local_verification_quarterly_arithmetic() -> None:
    published = json.loads(FREEZE_VALIDATION.read_text())
    check = local_verification_arithmetic(published)
    local = json.loads(EVIDENCE.read_text())
    agg = local["quarterly_weighted_aggregate"]
    assert check["weighted_baseline"] == pytest.approx(agg["baseline"])
    assert check["weighted_candidate"] == pytest.approx(agg["candidate"])
    assert check["weighted_delta"] == pytest.approx(agg["mean_delta"])
    rest = local["quarterly_decomposition_NOT_an_additional_significance_test"]["without_2024Q1"]
    assert check["without_2024Q1"]["baseline"] == pytest.approx(rest["baseline"])
    assert check["without_2024Q1"]["candidate"] == pytest.approx(rest["candidate"])
    assert check["not_an_additional_significance_test"] is True
    assert local["full_private_tape_replayed"] is False


def test_stop_and_gap_do_not_invent_bars() -> None:
    inventory = [
        {"role": "continuous_runner_yahoo_cache", "status": "present"},
        {"role": "public_yahoo_current_universe_bars", "status": "missing"},
    ]
    layers = {LAYER_A: {"status": "available"}, LAYER_C: {"status": "blocked"}}
    stopped = stop_rule(inventory, layers)
    assert stopped["outcome"] == "RECOVERED_CACHE_ARCHIVED"
    assert stopped["executed_backtests"] == 0
    gap = next_unique_gap(inventory)
    assert gap["field"] == "historical_constituent_membership_and_delist_tape"
    assert gap["user_must_provide"] is True
