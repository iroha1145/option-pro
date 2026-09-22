"""Default-off watch groups. Synthetic lists only; no market cache and no rescore."""

from __future__ import annotations

import json

from app.services.eod_limited.watch_groups import (
    EXTENSION,
    HIGH_VOL,
    SHADOW_VARIANTS,
    TECHNICAL,
    classify_watch_groups,
    maybe_attach_watch_groups,
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


def _layers(g1, g2, g3, qualified=None, session="2024-03-28"):
    qualified = qualified or {}
    layers = {}
    for variant, discovery, technical in (
        ("B0_current", g1, g1),
        ("G1_stock_reference", g1, g1),
        ("G2_extension_discovery", g2, g1),
        ("G3_risk_discovery", g3, g1),
    ):
        layers[variant] = {
            "discovery": discovery,
            "technical_entry": technical,
            "qualified_entry": qualified.get(variant, []),
        }
    return {"identity": {"session_date": session}, "layers": layers}


def test_shadow_names_are_not_the_research_gates():
    assert set(SHADOW_VARIANTS) == {"baseline", "track_atr", "entry_state", "raw_momentum"}
    assert all(item["research_gate"] is None for item in SHADOW_VARIANTS.values())
    assert SHADOW_VARIANTS["track_atr"]["atr_policy"] == "track_liquid_v1"
    assert SHADOW_VARIANTS["baseline"]["atr_policy"] == "legacy"


def test_disabled_attach_keeps_the_same_snapshot(monkeypatch):
    monkeypatch.delenv("EOD_RESEARCH_WATCH_GROUPS", raising=False)
    payload = {"rows": [{"ticker": "AAA", "sort_score": 10}], "results": [{"ticker": "AAA"}]}
    assert maybe_attach_watch_groups(payload) is payload


def test_enabled_without_sidecar_does_not_score_or_change_rows(monkeypatch):
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.delenv("EOD_RESEARCH_WATCH_LAYERS", raising=False)
    payload = {"rows": [{"ticker": "AAA", "sort_score": 10}]}
    assert maybe_attach_watch_groups(payload) is payload


def test_watch_names_do_not_displace_a_lower_scored_main_board(monkeypatch, tmp_path):
    sidecar = tmp_path / "layers.json"
    sidecar.write_text(
        json.dumps(
            _layers(
                [_row("AAA", 10)],
                [_row("AAA", 10), _row("ZZZ", 99)],
                [_row("AAA", 10), _row("ZZZ", 99), _row("HHH", 98)],
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(sidecar))
    rows = [{"ticker": "AAA", "sort_score": 10}]
    payload = {"rows": rows, "results": rows, "served_session": "2024-03-28"}
    attached = maybe_attach_watch_groups(payload)
    assert attached["rows"] == rows
    assert attached["results"] == rows
    assert [row["ticker"] for row in attached["rows"]] == ["AAA"]
    groups = attached["research_watch_groups"]
    assert groups["displaces_main_board"] is False
    assert groups["high_volatility_collapsed"] is True
    assert [row["security_id"] for row in groups["extension_watch"]] == ["ZZZ"]
    assert [row["security_id"] for row in groups["high_volatility_watch"]] == ["HHH"]
    assert all(row["qualified"] is False and row["tradable"] is False for row in groups["extension_watch"])
    assert all(row["qualified"] is False and row["tradable"] is False for row in groups["high_volatility_watch"])


def test_full_source_grouping_precedes_the_limit_and_preserves_identity():
    payload = _layers(
        [_row("aaa", 10, ["EXTENDED"])],
        [_row("aaa", 10), _row("BBB", 40, ["DOLLAR_LIQUIDITY_UNVERIFIED"]), _row("ccc", 30)],
        [_row("aaa", 10), _row("BBB", 40), _row("ccc", 30), _row("DdD", 20, ["HIGH_ATR"])],
        qualified={"G2_extension_discovery": [_row("BBB", 40, ["DOLLAR_LIQUIDITY_UNVERIFIED"])]},
    )
    before = json.dumps(payload, sort_keys=True)
    limited = classify_watch_groups(payload, limit=1)
    assert json.dumps(payload, sort_keys=True) == before
    assert [row["security_id"] for row in limited[EXTENSION]] == ["BBB"]
    full = classify_watch_groups(payload)
    assert [row["security_id"] for row in full[TECHNICAL]] == ["aaa"]
    assert [row["security_id"] for row in full[EXTENSION]] == ["BBB", "ccc"]
    assert [row["security_id"] for row in full[HIGH_VOL]] == ["DdD"]
    assert full[EXTENSION][0]["qualified"] is False
    assert full[EXTENSION][0]["score"] == 40
    assert full[EXTENSION][0]["rejection_reasons"] == ["DOLLAR_LIQUIDITY_UNVERIFIED"]
    assert full[EXTENSION][0]["source_date"] == "2024-03-28"
    assert full[HIGH_VOL][0]["security_id"] == "DdD"
    names = [row["security_id"] for key in (TECHNICAL, EXTENSION, HIGH_VOL) for row in full[key]]
    assert len(names) == len(set(names))


def test_etf_branch_is_not_rewritten():
    etf = _row("SPY", 100, ["EXTENDED"], stock_or_etf_track="etf")
    payload = _layers([_row("AAA", 10)], [_row("AAA", 10), etf], [_row("AAA", 10), etf])
    result = classify_watch_groups(payload)
    assert result["etf_unchanged"] == [etf]
    assert "qualified" not in result["etf_unchanged"][0]
    assert [row["security_id"] for row in result[EXTENSION]] == []


def test_session_mismatch_leaves_the_snapshot_unchanged(monkeypatch, tmp_path):
    sidecar = tmp_path / "layers.json"
    sidecar.write_text(json.dumps(_layers([_row("AAA", 1)], [_row("AAA", 1)], [_row("AAA", 1)])), encoding="utf-8")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(sidecar))
    payload = {"rows": [{"ticker": "AAA"}], "served_session": "2024-06-28"}
    assert maybe_attach_watch_groups(payload) is payload
