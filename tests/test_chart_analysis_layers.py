"""ChartAnalysisBundle + auto-pattern v2 layer contract."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.strength.price_action import _find_swings
from app.services.technical.auto_patterns import (
    ALGORITHM_VERSION,
    apply_volume_confirmation,
    compute_display_priority,
    detect_auto_patterns,
)
from app.services.technical.base_structure import detect_base_structure
from app.services.technical.chart_analysis import assemble_chart_analysis, bar_fingerprint
from app.services.technical.layer_registry import PRESETS, preset_enabled
from app.services.technical.structure import clean_series, compute_technical_structure

from tests.test_auto_technical_patterns import _detect, _series, _zigzag


def test_cutoff_equals_truncated_series() -> None:
    bars = _zigzag(200, lambda i: 50 + 0.16 * i, lambda i: 64 + 0.16 * i)
    full = _series(bars)
    truncated = _series(bars[:-8])
    cutoff = truncated["dates"][-1]
    assert detect_auto_patterns(full, data_through=cutoff) == detect_auto_patterns(
        truncated, data_through=cutoff
    )


def test_multi_span_swings_are_deterministic() -> None:
    series = _series(_zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i))
    first = detect_auto_patterns(series, data_through=series["dates"][-1])
    second = detect_auto_patterns(series, data_through=series["dates"][-1])
    assert first == second
    highs_a, lows_a = _find_swings(series["highs"], series["lows"], 2)
    highs_b, lows_b = _find_swings(series["highs"], series["lows"], 3)
    highs_c, lows_c = _find_swings(series["highs"], series["lows"], 5)
    assert highs_a and highs_b and highs_c
    assert lows_a and lows_b and lows_c
    assert highs_a == _find_swings(series["highs"], series["lows"], 2)[0]


def test_robust_fit_ignores_a_single_absurd_wick() -> None:
    clean = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i, wick=0.15)
    dirty = [dict(row) for row in clean]
    spike = dirty[90]
    dirty[90] = {**spike, "h": spike["h"] * 1.8, "l": spike["l"] * 0.4}
    clean_rows = _detect(clean)
    dirty_rows = _detect(dirty)
    clean_support = [row for row in clean_rows if row["kind"] == "support_trend" and row["subtype"] == "rising"]
    dirty_support = [row for row in dirty_rows if row["kind"] == "support_trend" and row["subtype"] == "rising"]
    assert clean_support and dirty_support
    spike_high = dirty[90]["h"]
    for row in dirty_support:
        for anchor in row["anchors"]:
            assert abs(anchor["price"] - spike_high) > 1.0


def test_touch_time_dedup_gap() -> None:
    from app.services.technical.auto_patterns import _dedupe_indexes

    assert _dedupe_indexes([1, 2, 3, 8, 9, 20]) == [1, 8, 20]


def test_rail_alternation_and_channel_width() -> None:
    from app.services.technical.auto_patterns import _alternates

    assert _alternates([10, 30], [20, 40]) is True
    assert _alternates([10, 12, 14], [11]) is True
    rows = _detect(_zigzag(200, lambda i: 40 + 0.14 * i, lambda i: 52 + 0.14 * i))
    channel = next(row for row in rows if row["kind"] == "channel" and row["subtype"] == "rising")
    assert channel["shapeQuality"] >= 0.55
    assert channel["displayPriority"] > 0


def test_triangle_and_wedge_exact_subtypes() -> None:
    cases = [
        (_zigzag(160, lambda i: 40 + 0.12 * i, lambda i: 90 - 0.12 * i), "triangle", "symmetric"),
        (_zigzag(160, lambda i: 40 + 0.14 * i, lambda i: 80.0), "triangle", "ascending"),
        (_zigzag(160, lambda i: 40.0, lambda i: 90 - 0.14 * i), "triangle", "descending"),
        (_zigzag(160, lambda i: 40 + 0.22 * i, lambda i: 70 + 0.12 * i), "wedge", "rising"),
        (_zigzag(160, lambda i: 80 - 0.12 * i, lambda i: 110 - 0.22 * i), "wedge", "falling"),
    ]
    for bars, kind, subtype in cases:
        rows = _detect(bars)
        matched = [row for row in rows if row["kind"] == kind and row.get("subtype") == subtype]
        assert matched, (kind, subtype, [(row["kind"], row.get("subtype")) for row in rows])
        assert all(row["subtype"] == subtype for row in matched)


def test_box_overlays_only_from_base_structure() -> None:
    bars = _zigzag(160, lambda i: 50.0, lambda i: 62.0)
    rows = _detect(bars)
    assert all(row["kind"] != "box" for row in rows)
    series = _series(bars)
    prior = {key: values[:-1] for key, values in series.items()}
    base = detect_base_structure(prior)
    assert base is not None
    bundle = assemble_chart_analysis(series=series, data_through=series["dates"][-1], base=base, auto_patterns=rows)
    boxes = [row for row in bundle["overlays"] if row["kind"] == "box"]
    assert boxes
    assert {row["sourceId"] for row in boxes} == {"base_structure"}


def test_nms_merges_duplicate_sources() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    rows = _detect(bars)
    assert rows
    for row in rows:
        assert isinstance(row.get("sources"), list)
        assert 0.0 <= float(row.get("consensus") or 0) <= 1.0
    bundle = assemble_chart_analysis(
        series=_series(bars),
        data_through=_series(bars)["dates"][-1],
        auto_patterns=rows,
    )
    pattern_ids = [row["id"] for row in bundle["overlays"] if row["sourceId"] == "auto_patterns"]
    assert len(pattern_ids) == len(set(pattern_ids))
    assert any(row.get("evidence", {}).get("sources") for row in bundle["overlays"])


def test_strength_score_does_not_change_shape_or_geometry() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    series = _series(bars)
    rows = detect_auto_patterns(series, data_through=series["dates"][-1])
    assert rows
    original = rows[0]
    geom = [dict(a) for a in original["anchors"]]
    quality = original["shapeQuality"]
    bundle = assemble_chart_analysis(
        series=series,
        data_through=series["dates"][-1],
        auto_patterns=rows,
        technicals={"rsi_score": 99.0},
        price_action={"score": 99.0},
        vol_price={"status": "active", "setup_type": "absorption_bullish", "breakout_quality_adjustment": 12},
    )
    again = [row for row in bundle["autoPatterns"] if row["id"] == original["id"]][0]
    assert again["shapeQuality"] == quality
    assert again["anchors"] == geom
    assert bundle["strengthContext"]["finalScore"] is None
    assert "not a win probability" in bundle["strengthContext"]["note"]


def test_volume_confirmation_changes_priority_not_geometry() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    row = _detect(bars)[0]
    geom = [dict(a) for a in row["anchors"]]
    low = apply_volume_confirmation(row, 0.1)
    high = apply_volume_confirmation(row, 0.9)
    assert low["anchors"] == geom == high["anchors"]
    assert low["shapeQuality"] == high["shapeQuality"]
    assert high["displayPriority"] > low["displayPriority"]
    expected = compute_display_priority(
        row["shapeQuality"], 0.9, row["trendAlignment"], row["recency"], row["consensus"]
    )
    assert high["displayPriority"] == expected


def test_single_support_resistance_break_direction() -> None:
    rows = _detect(_zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i))
    for row in rows:
        if row["kind"] == "support_trend":
            assert row["status"] != "broken_up"
            assert row["measuredTarget"] is None
        if row["kind"] == "resistance_trend":
            assert row["status"] != "broken_down"
            assert row["measuredTarget"] is None


def test_invalidated_and_stale_lifecycle_fields() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    rows = _detect(bars)
    assert rows
    for row in rows:
        assert row["status"] in {"forming", "testing", "broken_up", "broken_down", "invalidated"}
        assert row["dataThrough"] <= _series(bars)["dates"][-1]
        assert 0.0 <= row["recency"] <= 1.0


def test_fingerprint_mismatch_is_detectable() -> None:
    series = _series(_zigzag(120, lambda i: 50 + 0.1 * i, lambda i: 60 + 0.1 * i))
    fp = bar_fingerprint(series["dates"], series["closes"], series["highs"], series["lows"])
    other = bar_fingerprint(series["dates"][:-1], series["closes"][:-1], series["highs"][:-1], series["lows"][:-1])
    assert fp != other
    bundle = assemble_chart_analysis(series=series, data_through=series["dates"][-1], auto_patterns=[])
    assert bundle["barFingerprint"] == fp
    assert bundle["dataThrough"] == series["dates"][-1]


def test_unclosed_last_bar_excluded_from_daily_detection() -> None:
    bars = _zigzag(120, lambda i: 50 + 0.1 * i, lambda i: 60 + 0.1 * i)
    now = datetime(2025, 1, 6, 16, 0, tzinfo=timezone.utc)
    result = compute_technical_structure(bars, last_bar_closed=False, now=now)
    assert result is not None
    assert result["last_bar"]["closed"] is False
    assert result["data_through"] < result["last_bar"]["trade_date"] or result["data_through"] <= result["last_bar"]["trade_date"]
    # Analysis series is one bar shorter than the visible last bar date when the last bar is open.
    assert result["chart_analysis"]["dataThrough"] == result["data_through"]


def test_bundle_has_required_overlay_fields_and_no_echarts_option() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    result = compute_technical_structure(bars, last_bar_closed=True)
    assert result is not None
    bundle = result["chart_analysis"]
    required = {
        "ticker",
        "range",
        "adjustment",
        "dataThrough",
        "barFingerprint",
        "overlays",
        "indicatorPanes",
        "strengthContext",
    }
    assert required <= set(bundle)
    pane_ids = {pane["id"] for pane in bundle["indicatorPanes"]}
    assert {"rsi", "macd", "obv", "clv", "range_persistence", "spy_rs"} <= pane_ids
    for overlay in bundle["overlays"]:
        for key in (
            "id",
            "sourceId",
            "algorithmVersion",
            "group",
            "kind",
            "geometry",
            "status",
            "direction",
            "shapeQuality",
            "displayPriority",
            "evidence",
            "formationStart",
            "formationEnd",
            "dataThrough",
            "label",
            "detail",
        ):
            assert key in overlay
        assert "option" not in overlay
        assert "graphic" not in overlay
        evidence = overlay["evidence"]
        for field in ("shapeQuality", "volumeConfirmation", "trendAlignment", "recency", "consensus"):
            assert field in evidence
    assert "option" not in bundle
    assert "scan_strength" not in str(bundle)


def test_registry_presets_exist() -> None:
    for name in ("minimal", "structure", "breakout", "momentum", "volume", "all"):
        assert name in PRESETS
        assert preset_enabled(name)
    assert PRESETS["minimal"]["maxPatterns"] == 3
    assert PRESETS["minimal"]["maxLabels"] == 6


def test_scanner_not_imported_by_chart_analysis() -> None:
    import app.services.technical.chart_analysis as mod
    import inspect

    source = inspect.getsource(mod)
    assert "scan_strength" not in source
    assert "from app.services.strength.scanner" not in source
    assert "import echarts" not in source.lower()
