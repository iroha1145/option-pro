"""Default-off watch groups. Synthetic lists only; no market cache and no rescore."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from app.services.eod_limited.watch_groups import (
    EXTENSION,
    HIGH_VOL,
    SHADOW_VARIANTS,
    TECHNICAL,
    WatchInputError,
    classify_watch_groups,
    maybe_attach_watch_groups,
    publish_watch_sidecar,
    stock_roster_digest,
)


def _row(security_id, score, reasons=None, **extra):
    row = {
        "security_id": security_id,
        "score": score,
        "algorithm_id": "D_residual_momentum",
        "sector_context": "all_market_stocks",
        "rejection_reasons": list(reasons or []),
        "stock_or_etf_track": "stock",
    }
    row.update(extra)
    return row


def _layers(g1, g2, g3, qualified=None, session="2024-03-28", profile="balanced", horizon="mid"):
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
    return {
        "identity": {
            "session_date": session,
            "profile": profile,
            "horizon": horizon,
            "protocol": "research_watch_layers_v1",
            "source": "synthetic_watch_layers_v1",
        },
        "layers": layers,
    }


def _snapshot(**extra):
    payload = {
        "rows": [{"ticker": "AAA", "sort_score": 10}],
        "results": [{"ticker": "AAA", "sort_score": 10}],
        "count": 1,
        "served_session": "2024-03-28",
        "params": {"profile": "balanced", "timeframe": "mid", "sector_id": None, "min_price": 0},
    }
    payload.update(extra)
    return payload


def _enable(monkeypatch, tmp_path, document):
    path = tmp_path / "layers.json"
    publish_watch_sidecar(document, path)
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(path))
    return path


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
    _enable(
        monkeypatch,
        tmp_path,
        _layers(
            [_row("AAA", 10)],
            [_row("AAA", 10), _row("ZZZ", 99)],
            [_row("AAA", 10), _row("ZZZ", 99), _row("HHH", 98)],
        ),
    )
    rows = [{"ticker": "AAA", "sort_score": 10}]
    payload = _snapshot(rows=rows, results=rows)
    attached = maybe_attach_watch_groups(payload)
    assert attached["rows"] is rows
    assert attached["results"] is rows
    assert [row["ticker"] for row in attached["rows"]] == ["AAA"]
    assert payload.get("research_watch_groups") is None
    groups = attached["research_watch_groups"]
    assert groups["displaces_main_board"] is False
    assert groups["high_volatility_collapsed"] is True
    assert groups["profile"] == "balanced"
    assert groups["horizon"] == "mid"
    assert [row["security_id"] for row in groups["extension_watch"]] == ["ZZZ"]
    assert [row["security_id"] for row in groups["high_volatility_watch"]] == ["HHH"]
    assert all(row["qualified"] is False and row["tradable"] is False for row in groups["extension_watch"])
    assert all(row["qualified"] is False and row["tradable"] is False for row in groups["high_volatility_watch"])


def test_full_source_grouping_precedes_the_limit_and_preserves_identity():
    shared = [_row("BBB", 40, ["DOLLAR_LIQUIDITY_UNVERIFIED"])]
    payload = _layers(
        [_row("aaa", 10, ["EXTENDED"])],
        [_row("aaa", 10), _row("BBB", 40, ["DOLLAR_LIQUIDITY_UNVERIFIED"]), _row("ccc", 30)],
        [_row("aaa", 10), _row("BBB", 40), _row("ccc", 30), _row("DdD", 20, ["HIGH_ATR"])],
        qualified={
            "G1_stock_reference": shared,
            "G2_extension_discovery": shared,
            "G3_risk_discovery": shared,
        },
    )
    before = json.dumps(payload, sort_keys=True)
    limited = classify_watch_groups(payload, limit=1)
    assert json.dumps(payload, sort_keys=True) == before
    assert [row["security_id"] for row in limited[EXTENSION]] == ["BBB"]
    assert limited["totals"][EXTENSION] == 2
    full = classify_watch_groups(payload)
    assert [row["security_id"] for row in full[TECHNICAL]] == ["aaa"]
    assert [row["security_id"] for row in full[EXTENSION]] == ["BBB", "ccc"]
    assert [row["security_id"] for row in full[HIGH_VOL]] == ["DdD"]
    assert full[EXTENSION][0]["qualified"] is False
    assert full[EXTENSION][0]["tradable"] is False
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
    _enable(monkeypatch, tmp_path, _layers([_row("AAA", 1)], [_row("AAA", 1)], [_row("AAA", 1)]))
    payload = _snapshot(served_session="2024-06-28")
    assert maybe_attach_watch_groups(payload) is payload


def test_timeframe_all_matches_only_the_mid_sidecar(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, _layers([_row("AAA", 1)], [_row("AAA", 1), _row("ZZZ", 9)], [_row("AAA", 1), _row("ZZZ", 9)]))
    matched = maybe_attach_watch_groups(_snapshot(params={"profile": "balanced", "timeframe": "all", "min_price": 0}))
    assert [row["security_id"] for row in matched["research_watch_groups"]["extension_watch"]] == ["ZZZ"]
    omitted = {"profile": "balanced", "min_price": 0}
    assert [row["security_id"] for row in maybe_attach_watch_groups(_snapshot(params=omitted))["research_watch_groups"]["extension_watch"]] == ["ZZZ"]
    long_view = _snapshot(params={"profile": "balanced", "timeframe": "long", "min_price": 0})
    assert maybe_attach_watch_groups(long_view) is long_view


def test_shared_qualification_is_protocol_correct_and_not_tradable():
    shared = [_row("CCC", 12)]
    payload = _layers(
        [_row("AAA", 10)],
        [_row("AAA", 10), _row("CCC", 12)],
        [_row("AAA", 10), _row("CCC", 12)],
        qualified={
            "G1_stock_reference": shared,
            "G2_extension_discovery": shared,
            "G3_risk_discovery": shared,
        },
    )
    result = classify_watch_groups(payload)
    assert result[EXTENSION][0]["security_id"] == "CCC"
    assert result[EXTENSION][0]["qualified"] is True
    assert result[EXTENSION][0]["tradable"] is False


def test_divergent_technical_entry_is_rejected():
    document = _layers([_row("AAA", 1)], [_row("AAA", 1)], [_row("AAA", 1)])
    document["layers"]["G2_extension_discovery"]["technical_entry"] = [_row("ZZZ", 2)]
    with pytest.raises(WatchInputError) as caught:
        classify_watch_groups(document)
    assert caught.value.reason == "technical_entry_diverges"


def test_verified_roster_confirms_a_stock_without_guessing_tickers():
    bare = _row("ZZZ", 9)
    bare.pop("stock_or_etf_track")
    document = _layers([_row("AAA", 1)], [_row("AAA", 1), bare], [_row("AAA", 1), bare])
    document["stock_roster"] = {"ids": ["ZZZ"], "sha256": stock_roster_digest(["ZZZ"])}
    result = classify_watch_groups(document)
    assert [row["security_id"] for row in result[EXTENSION]] == ["ZZZ"]
    guessed = _layers([_row("AAA", 1)], [_row("AAA", 1), _row("SPY", 9, stock_or_etf_track="unknown")], [_row("AAA", 1)])
    hidden = classify_watch_groups(guessed)
    assert "SPY" not in [row["security_id"] for row in hidden[EXTENSION]]
    assert hidden["etf_unchanged"] == []


def test_bad_roster_hash_rejects_the_sidecar(monkeypatch, tmp_path):
    document = _layers([_row("AAA", 1)], [_row("AAA", 1)], [_row("AAA", 1)])
    document["stock_roster"] = {"ids": ["AAA"], "sha256": "0" * 64}
    with pytest.raises(WatchInputError) as caught:
        classify_watch_groups(document)
    assert caught.value.reason == "roster_hash"
    raw_path = tmp_path / "layers.json"
    raw = (json.dumps(document) + "\n").encode()
    raw_path.write_bytes(raw)
    manifest = {"sha256": hashlib.sha256(raw).hexdigest(), "protocol": "research_watch_layers_v1", "source": "synthetic_watch_layers_v1", "profile": "balanced", "horizon": "mid", "session_date": "2024-03-28"}
    raw_path.with_name(raw_path.name + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(raw_path))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_display_limit_is_applied_after_grouping(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.eod_limited.watch_groups.WATCH_DISPLAY_LIMIT", 1)
    _enable(
        monkeypatch,
        tmp_path,
        _layers(
            [_row("AAA", 10)],
            [_row("AAA", 10), _row("ZZZ", 40), _row("YYY", 30)],
            [_row("AAA", 10), _row("ZZZ", 40), _row("YYY", 30), _row("HHH", 20)],
        ),
    )
    groups = maybe_attach_watch_groups(_snapshot())["research_watch_groups"]
    assert [row["security_id"] for row in groups["extension_watch"]] == ["ZZZ"]
    assert groups["extension_total"] == 2
    assert groups["display_limit"] == 1
    assert [row["security_id"] for row in groups["high_volatility_watch"]] == ["HHH"]
    assert groups["high_volatility_total"] == 1


def test_page_filters_apply_when_the_rows_carry_them_and_are_labeled_otherwise(monkeypatch, tmp_path):
    priced = _layers(
        [_row("AAA", 10, sector_id="semiconductors", price=50)],
        [
            _row("AAA", 10, sector_id="semiconductors", price=50),
            _row("ZZZ", 40, sector_id="semiconductors", price=20),
            _row("YYY", 30, sector_id="energy", price=80),
        ],
        [_row("AAA", 10, sector_id="semiconductors", price=50)],
    )
    _enable(monkeypatch, tmp_path, priced)
    params = {"profile": "balanced", "timeframe": "mid", "sector_id": "semiconductors", "min_price": 10}
    groups = maybe_attach_watch_groups(_snapshot(params=params))["research_watch_groups"]
    assert [row["security_id"] for row in groups["extension_watch"]] == ["ZZZ"]
    assert groups["filter_scope"] == "page_filters"
    assert groups["filter_scope_note"] == "已按当前行业与最低价筛选。"

    plain = _layers([_row("AAA", 1)], [_row("AAA", 1), _row("ZZZ", 9)], [_row("AAA", 1), _row("ZZZ", 9)])
    _enable(monkeypatch, tmp_path, plain)
    global_groups = maybe_attach_watch_groups(_snapshot(params=params))["research_watch_groups"]
    assert [row["security_id"] for row in global_groups["extension_watch"]] == ["ZZZ"]
    assert global_groups["filter_scope"] == "global_reference"
    assert global_groups["filter_scope_note"] == "全局参考，未按当前行业或最低价筛选。"


def test_publish_replaces_atomically_and_leaves_old_checkpoints_hashed(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen-checkpoint.json"
    frozen.write_bytes(b'{"frozen":true}\n')
    frozen_hash = hashlib.sha256(frozen.read_bytes()).hexdigest()
    document = _layers([_row("AAA", 1)], [_row("AAA", 1), _row("ZZZ", 2)], [_row("AAA", 1), _row("ZZZ", 2)])
    path = tmp_path / "layers.json"
    manifest = publish_watch_sidecar(document, path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == frozen_hash
    assert list(tmp_path.glob("*.tmp")) == []
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(path))
    assert "ZZZ" in [
        row["security_id"]
        for row in maybe_attach_watch_groups(_snapshot())["research_watch_groups"]["extension_watch"]
    ]
    path.write_bytes(b"{")
    partial = _snapshot()
    assert maybe_attach_watch_groups(partial) is partial
    path.write_bytes(b'{"identity":{}}\n')
    replaced = _snapshot()
    assert maybe_attach_watch_groups(replaced) is replaced


def test_integrity_hashes_the_same_bytes_it_parses(monkeypatch, tmp_path):
    document = _layers([_row("AAA", 1)], [_row("AAA", 1), _row("ZZZ", 2)], [_row("AAA", 1), _row("ZZZ", 2)])
    path = _enable(monkeypatch, tmp_path, document)
    reads = {"sidecar": 0}
    original = type(path).read_bytes

    def counted(self: Path) -> bytes:
        if self == path:
            reads["sidecar"] += 1
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    attached = maybe_attach_watch_groups(_snapshot())
    assert reads["sidecar"] == 1
    assert attached["research_watch_groups"]["extension_watch"][0]["security_id"] == "ZZZ"
    raw = original(path)
    raw = raw.replace(b"ZZZ", b"QQQ")
    path.write_bytes(raw)
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_invalid_sidecar_log_names_the_reason_without_a_path(caplog, monkeypatch, tmp_path):
    path = tmp_path / "secret-layers.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(path))
    payload = _snapshot()
    with caplog.at_level(logging.WARNING, logger="optix.eod.watch_groups"):
        assert maybe_attach_watch_groups(payload) is payload
    assert "invalid_json" in caplog.text
    assert "secret-layers" not in caplog.text
    assert str(tmp_path) not in caplog.text
