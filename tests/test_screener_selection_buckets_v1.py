"""Exclusive display buckets. Synthetic lists only; no historical rescore."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from screener_gate_candidates_v1 import (
    DOLLAR_LIQUIDITY_UNVERIFIED,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    VOLUME_SESSION_UNVERIFIED,
)
from screener_selection_buckets_v1 import (
    EXTENSION,
    HIGH_VOL,
    TECHNICAL,
    select_buckets,
    summarize_frozen_checkpoints,
)


def _row(security_id, score, reasons=None, **extra):
    row = {
        "security_id": security_id,
        "score": score,
        "algorithm_id": "D_residual_momentum",
        "sector_context": "all_market_stocks",
        "rejection_reasons": list(reasons or []),
    }
    row.update(extra)
    return row


def _payload(g1_technical, g2_discovery, g3_discovery, *, qualified=None, session="2022-01-03"):
    qualified = qualified or {}
    layers = {}
    for variant, discovery, technical in (
        ("B0_current", g1_technical, g1_technical),
        (G1_STOCK_REFERENCE, g1_technical, g1_technical),
        (G2_EXTENSION_DISCOVERY, g2_discovery, g1_technical),
        (G3_RISK_DISCOVERY, g3_discovery, g1_technical),
    ):
        layers[variant] = {
            "discovery": discovery,
            "technical_entry": technical,
            "qualified_entry": qualified.get(variant, []),
        }
    return {"identity": {"session_date": session}, "layers": layers}


def test_groups_are_exclusive_and_keep_source_order_before_truncation():
    payload = _payload(
        [_row("aaa", 10), _row("BBB", 50)],
        [_row("BBB", 50), _row("aaa", 10), _row("ccc", 99), _row("ddd", 70)],
        [_row("BBB", 50), _row("ccc", 99), _row("eee", 100), _row("aaa", 10)],
    )
    before = json.dumps(payload, sort_keys=True)
    result = select_buckets(payload, limit=1)
    assert json.dumps(payload, sort_keys=True) == before
    assert [row["security_id"] for row in result[TECHNICAL]] == ["aaa"]
    assert [row["security_id"] for row in result[EXTENSION]] == ["ccc"]
    assert [row["security_id"] for row in result[HIGH_VOL]] == ["eee"]
    unlimited = select_buckets(payload)
    assert [row["security_id"] for row in unlimited[EXTENSION]] == ["ccc", "ddd"]
    assert [row["security_id"] for row in unlimited[HIGH_VOL]] == ["eee"]
    names = [row["security_id"] for key in (TECHNICAL, EXTENSION, HIGH_VOL) for row in unlimited[key]]
    assert len(names) == len(set(names))


def test_truncating_sources_first_would_hide_a_later_extension_name():
    payload = _payload(
        [_row("AAA", 10)],
        [_row("AAA", 10), _row("ZZZ", 99)],
        [_row("AAA", 10), _row("ZZZ", 99)],
    )
    result = select_buckets(payload, limit=1)
    assert [row["security_id"] for row in result[EXTENSION]] == ["ZZZ"]


def test_technical_membership_removes_a_g3_only_overlap():
    payload = _payload(
        [_row("AAA", 10)],
        [_row("BBB", 20)],
        [_row("BBB", 20), _row("AAA", 90)],
    )
    result = select_buckets(payload)
    assert [row["security_id"] for row in result[TECHNICAL]] == ["AAA"]
    assert [row["security_id"] for row in result[EXTENSION]] == ["BBB"]
    assert result[HIGH_VOL] == []


def test_unverified_liquidity_is_not_promoted_and_technical_is_not_tradable():
    payload = _payload(
        [_row("Aaa", 80, [DOLLAR_LIQUIDITY_UNVERIFIED])],
        [_row("Aaa", 80, [DOLLAR_LIQUIDITY_UNVERIFIED]), _row("Qqq", 70, [VOLUME_SESSION_UNVERIFIED])],
        [_row("Aaa", 80), _row("Qqq", 70), _row("HHH", 60, ["HIGH_ATR"])],
        qualified={
            G1_STOCK_REFERENCE: [_row("Aaa", 80, [DOLLAR_LIQUIDITY_UNVERIFIED])],
            G2_EXTENSION_DISCOVERY: [_row("Qqq", 70, [VOLUME_SESSION_UNVERIFIED])],
            G3_RISK_DISCOVERY: [_row("HHH", 60, ["HIGH_ATR"])],
        },
    )
    result = select_buckets(payload)
    technical = result[TECHNICAL][0]
    assert technical["security_id"] == "Aaa"
    assert technical["score"] == 80
    assert technical["rejection_reasons"] == [DOLLAR_LIQUIDITY_UNVERIFIED]
    assert technical["source_date"] == "2022-01-03"
    assert technical["qualified"] is False
    assert technical["tradable"] is False
    extension = result[EXTENSION][0]
    assert extension["security_id"] == "Qqq"
    assert extension["qualified"] is False
    high = result[HIGH_VOL][0]
    assert high["security_id"] == "HHH"
    assert high["qualified"] is True
    assert high["tradable"] is False
    assert high["rejection_reasons"] == ["HIGH_ATR"]


def test_empty_lists_stay_empty():
    result = select_buckets(_payload([], [], [], session="2022-09-23"))
    assert result["session_date"] == "2022-09-23"
    assert result[TECHNICAL] == []
    assert result[EXTENSION] == []
    assert result[HIGH_VOL] == []


def test_etf_rows_are_not_rewritten_into_stock_buckets():
    etf = _row("SPY", 100, ["EXTENDED"], stock_or_etf_track="etf")
    payload = _payload(
        [_row("AAA", 10)],
        [_row("AAA", 10), etf, _row("BBB", 20)],
        [_row("AAA", 10), etf, _row("BBB", 20)],
    )
    original = json.dumps(etf, sort_keys=True)
    result = select_buckets(payload)
    assert json.dumps(etf, sort_keys=True) == original
    assert [row["security_id"] for row in result[EXTENSION]] == ["BBB"]
    assert result["etf_unchanged"] == [etf]
    assert "qualified" not in result["etf_unchanged"][0]
    assert "tradable" not in result["etf_unchanged"][0]


def test_output_reason_list_does_not_alias_the_input():
    row = _row("AAA", 10, ["EXTENDED"])
    result = select_buckets(_payload([row], [row], [row]))
    result[TECHNICAL][0]["rejection_reasons"].append("LOW_SCORE")
    assert row["rejection_reasons"] == ["EXTENDED"]


def test_summarize_counts_empty_days_without_rescoring(tmp_path):
    empty = _payload([], [], [], session="2022-09-23")
    filled = _payload([_row("AAA", 10)], [_row("AAA", 10), _row("BBB", 20)], [_row("AAA", 10), _row("BBB", 20), _row("CCC", 30)], session="2022-01-03")
    (tmp_path / "2022-09-23.json").write_text(json.dumps(empty), encoding="utf-8")
    (tmp_path / "2022-01-03.json").write_text(json.dumps(filled), encoding="utf-8")
    summary = summarize_frozen_checkpoints(tmp_path)
    assert summary["counts"] == {TECHNICAL: 1, EXTENSION: 1, HIGH_VOL: 1}
    assert summary["no_duplicate_or_unqualified_promotion"] is True
    assert summary["input_unchanged"] is True
    assert summary["market_performance_replayed"] is False
    assert summary["empty_primary_days"][0]["date"] == "2022-09-23"


def test_limit_does_not_pull_a_watch_name_into_the_technical_group():
    payload = _payload(
        [_row("AAA", 10)],
        [_row("AAA", 10), _row("ZZZ", 99)],
        [_row("AAA", 10), _row("ZZZ", 99), _row("HHH", 98)],
    )
    result = select_buckets(payload, limit=20)
    assert [row["security_id"] for row in result[TECHNICAL]] == ["AAA"]
    assert "ZZZ" not in {row["security_id"] for row in result[TECHNICAL]}
    assert "HHH" not in {row["security_id"] for row in result[TECHNICAL]}
    assert result[TECHNICAL][0]["score"] == 10
