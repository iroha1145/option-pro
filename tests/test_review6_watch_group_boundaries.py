"""REVIEW(6) watch-group boundaries.

The uploaded task package did not include tests/test_review6_watch_group_boundaries.py.
These 18 cases reconstruct the review head's 5 passing boundaries and 13 failing ones:
same-view display, date mismatch, explicit ETF exclusion, missing sidecar session,
and the default-off switch; then non-mapping rows, unconfirmed assets, other views,
duplicate ids, missing ids, solo G2 qualification, an incompatible protocol, and a
missing served date. They are not a byte copy of an unseen file.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.eod_limited.watch_groups import (
    EXTENSION,
    HIGH_VOL,
    TECHNICAL,
    classify_watch_groups,
    maybe_attach_watch_groups,
    publish_watch_sidecar,
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


def _stock(security_id, score, reasons=None, **extra):
    return _row(security_id, score, reasons, stock_or_etf_track="stock", **extra)


def _document(g1, g2, g3, *, session="2024-03-28", profile="balanced", horizon="mid", protocol="research_watch_layers_v1"):
    layers = {}
    for variant, discovery in (
        ("B0_current", g1),
        ("G1_stock_reference", g1),
        ("G2_extension_discovery", g2),
        ("G3_risk_discovery", g3),
    ):
        layers[variant] = {
            "discovery": discovery,
            "technical_entry": g1,
            "qualified_entry": [],
        }
    return {
        "identity": {
            "session_date": session,
            "profile": profile,
            "horizon": horizon,
            "protocol": protocol,
            "source": "synthetic_watch_layers_v1",
        },
        "layers": layers,
    }


def _valid():
    return _document(
        [_stock("AAA", 10)],
        [_stock("AAA", 10), _stock("ZZZ", 99, ["EXTENDED"])],
        [_stock("AAA", 10), _stock("ZZZ", 99, ["EXTENDED"]), _stock("HHH", 98, ["HIGH_ATR"])],
    )


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


def _write_raw(tmp_path, document):
    path = tmp_path / "layers.json"
    raw = (json.dumps(document) + "\n").encode("utf-8")
    path.write_bytes(raw)
    identity = document.get("identity") if isinstance(document.get("identity"), dict) else {}
    manifest = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "protocol": identity.get("protocol"),
        "source": identity.get("source"),
        "profile": identity.get("profile"),
        "horizon": identity.get("horizon"),
        "session_date": identity.get("session_date"),
    }
    path.with_name(path.name + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _arm(monkeypatch, path):
    monkeypatch.setenv("EOD_RESEARCH_WATCH_GROUPS", "1")
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(path))


def test_same_view_keeps_extension_and_high_vol_off_the_main_board(monkeypatch, tmp_path):
    path = tmp_path / "layers.json"
    publish_watch_sidecar(_valid(), path)
    _arm(monkeypatch, path)
    rows = [{"ticker": "AAA", "sort_score": 10}]
    payload = _snapshot(rows=rows, results=list(rows))
    attached = maybe_attach_watch_groups(payload)
    assert attached["rows"] is rows
    assert attached["count"] == 1
    assert [row["ticker"] for row in attached["rows"]] == ["AAA"]
    groups = attached["research_watch_groups"]
    assert [row["security_id"] for row in groups["extension_watch"]] == ["ZZZ"]
    assert [row["security_id"] for row in groups["high_volatility_watch"]] == ["HHH"]
    assert all(row["qualified"] is False and row["tradable"] is False for row in groups["extension_watch"])
    assert groups["displaces_main_board"] is False


def test_default_switch_returns_the_same_payload(monkeypatch, tmp_path):
    path = tmp_path / "layers.json"
    publish_watch_sidecar(_valid(), path)
    monkeypatch.delenv("EOD_RESEARCH_WATCH_GROUPS", raising=False)
    monkeypatch.setenv("EOD_RESEARCH_WATCH_LAYERS", str(path))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_session_mismatch_returns_the_same_payload(monkeypatch, tmp_path):
    path = tmp_path / "layers.json"
    publish_watch_sidecar(_valid(), path)
    _arm(monkeypatch, path)
    payload = _snapshot(served_session="2024-06-28")
    assert maybe_attach_watch_groups(payload) is payload


def test_explicit_etf_is_excluded_from_stock_groups():
    etf = _row("SPY", 100, ["EXTENDED"], stock_or_etf_track="etf")
    result = classify_watch_groups(_document([_stock("AAA", 10)], [_stock("AAA", 10), etf], [_stock("AAA", 10), etf]))
    assert result["etf_unchanged"] == [etf]
    assert "qualified" not in result["etf_unchanged"][0]
    names = [row["security_id"] for key in (TECHNICAL, EXTENSION, HIGH_VOL) for row in result[key]]
    assert "SPY" not in names


def test_missing_sidecar_session_date_returns_the_same_payload(monkeypatch, tmp_path):
    document = _valid()
    del document["identity"]["session_date"]
    _arm(monkeypatch, _write_raw(tmp_path, document))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


@pytest.mark.parametrize("bad", [7, None, "ZZZ"])
def test_non_mapping_row_does_not_break_the_snapshot(monkeypatch, tmp_path, bad):
    document = _valid()
    document["layers"]["G2_extension_discovery"]["discovery"] = [_stock("AAA", 10), bad]
    _arm(monkeypatch, _write_raw(tmp_path, document))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


@pytest.mark.parametrize("track", ["missing", "unknown"])
def test_unconfirmed_asset_is_not_a_stock(track):
    spy = _row("SPY", 100, ["EXTENDED"])
    if track == "unknown":
        spy["stock_or_etf_track"] = "unknown"
    result = classify_watch_groups(
        _document([_stock("AAA", 10)], [_stock("AAA", 10), spy], [_stock("AAA", 10), spy])
    )
    names = [row["security_id"] for key in (TECHNICAL, EXTENSION, HIGH_VOL) for row in result[key]]
    assert "SPY" not in names
    assert all(row.get("security_id") != "SPY" for row in result["etf_unchanged"])
    assert [row["security_id"] for row in result[TECHNICAL]] == ["AAA"]


@pytest.mark.parametrize(
    ("profile", "timeframe"),
    [("aggressive", "mid"), ("balanced", "long"), ("conservative", "short")],
)
def test_other_normalized_views_do_not_reuse_one_sidecar(monkeypatch, tmp_path, profile, timeframe):
    path = tmp_path / "layers.json"
    publish_watch_sidecar(_valid(), path)
    _arm(monkeypatch, path)
    payload = _snapshot(params={"profile": profile, "timeframe": timeframe, "sector_id": None, "min_price": 0})
    assert maybe_attach_watch_groups(payload) is payload


def test_duplicate_id_is_rejected(monkeypatch, tmp_path):
    document = _valid()
    document["layers"]["G2_extension_discovery"]["discovery"] = [
        _stock("AAA", 10),
        _stock("ZZZ", 99),
        _stock("ZZZ", 98),
    ]
    _arm(monkeypatch, _write_raw(tmp_path, document))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_missing_security_id_is_rejected(monkeypatch, tmp_path):
    document = _valid()
    document["layers"]["G2_extension_discovery"]["discovery"] = [
        _stock("AAA", 10),
        {"score": 99, "rejection_reasons": ["EXTENDED"], "stock_or_etf_track": "stock"},
    ]
    _arm(monkeypatch, _write_raw(tmp_path, document))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_g2_alone_cannot_mark_qualified(monkeypatch, tmp_path):
    document = _valid()
    document["layers"]["G2_extension_discovery"]["qualified_entry"] = [_stock("ZZZ", 99, ["EXTENDED"])]
    _arm(monkeypatch, _write_raw(tmp_path, document))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_incompatible_protocol_is_rejected(monkeypatch, tmp_path):
    document = _valid()
    document["identity"]["protocol"] = "not-a-real-protocol"
    _arm(monkeypatch, _write_raw(tmp_path, document))
    payload = _snapshot()
    assert maybe_attach_watch_groups(payload) is payload


def test_missing_served_date_is_not_a_match(monkeypatch, tmp_path):
    path = tmp_path / "layers.json"
    publish_watch_sidecar(_valid(), path)
    _arm(monkeypatch, path)
    payload = _snapshot()
    payload.pop("served_session")
    assert maybe_attach_watch_groups(payload) is payload
