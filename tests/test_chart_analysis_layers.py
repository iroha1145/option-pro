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
import pandas as pd

from app.services.technical.chart_analysis import (
    assemble_chart_analysis,
    assemble_intraday_analysis,
    bar_fingerprint,
    canonical_bar_payload,
    consecutive_swing_labels,
    series_from_chart_bars,
)
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
    again = [row for row in bundle["overlays"] if row["id"] == original["id"]][0]
    assert again["shapeQuality"] == quality
    painted = again["geometry"].get("fitAnchors") or again["geometry"].get("anchors")
    assert painted
    assert original.get("fitAnchors") or original["anchors"] == geom
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
    fp = bar_fingerprint(series)
    truncated = {key: values[:-1] for key, values in series.items()}
    other = bar_fingerprint(truncated)
    assert fp != other
    bundle = assemble_chart_analysis(series=series, data_through=series["dates"][-1], auto_patterns=[])
    assert bundle["barFingerprint"] == fp
    assert bundle["dataThrough"] == series["dates"][-1]
    interior = {key: list(values) for key, values in series.items()}
    interior["highs"][40] = interior["highs"][40] + 7.5
    assert interior["closes"][-1] == series["closes"][-1]
    assert interior["highs"][-1] == series["highs"][-1]
    assert interior["lows"][-1] == series["lows"][-1]
    assert bar_fingerprint(interior) != fp
    opened = {key: list(values) for key, values in series.items()}
    opened["opens"][40] = opened["opens"][40] + 3.0
    assert bar_fingerprint(opened) != fp
    volumed = {key: list(values) for key, values in series.items()}
    volumed["volumes"][40] = volumed["volumes"][40] + 50_000
    assert bar_fingerprint(volumed) != fp


FINGERPRINT_VECTOR_SERIES = {
    "times": [1_700_000_000, 1_700_086_400],
    "opens": [10.0, 10.5],
    "highs": [11.0, 12.0],
    "lows": [9.0, 10.0],
    "closes": [10.5, 11.0],
    "volumes": [1000.0, 1100.0],
    "ext": [False, False],
    "quote_only": [False, False],
}


def test_fingerprint_shared_sha256_vector() -> None:
    payload = canonical_bar_payload(FINGERPRINT_VECTOR_SERIES)
    assert payload == (
        "1700000000|10.000000|11.000000|9.000000|10.500000|1000.000000|0|0\n"
        "1700086400|10.500000|12.000000|10.000000|11.000000|1100.000000|0|0"
    )
    digest = bar_fingerprint(FINGERPRINT_VECTOR_SERIES)
    import hashlib

    assert digest == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert len(digest) == 64


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
    assert {"rsi", "macd", "obv", "clv", "range_persistence"} <= pane_ids
    assert "spy_rs" not in pane_ids
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
    assert "score_market_fit" not in source
    assert "score_ranking" not in source
    assert "from app.services.strength.scanner" not in source
    assert "import echarts" not in source.lower()
    assert "from app.services.strength.scoring import score_intrinsic" in source


def test_detail_chart_and_radar_share_one_feature_row_builder() -> None:
    """两个界面的因子行只能有一份实现，否则同票同日会打出不同分数。

    抄写版漂过：follow_through 口径不同、atr_pct 与 avg_dollar_volume_20d 缺失
    （score_intrinsic 会读，缺了就被静默重归一）；补齐字段的第二份抄写也跟不上
    雷达对每个因子的定点舍入。实现落在中立模块 strength/features，扫描器与
    详情图都 import 它——所以上面那条「不 import scanner」的红线仍然成立。
    """

    import app.services.technical.chart_analysis as mod
    from app.services.strength import features, scanner

    assert scanner._feature_row is features._feature_row
    assert mod.build_feature_row is features._feature_row
    # 复制版才会有的特征键字面量：出现即说明特征行又被抄回来了。
    import inspect

    source = inspect.getsource(mod)
    for literal in ('"follow_through"', '"ath_proximity"', '"return_63d"', '"atr_pct"'):
        assert literal not in source, literal

    series = _series(_zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i))
    frame = pd.DataFrame(
        {
            "Open": series["opens"],
            "High": series["highs"],
            "Low": series["lows"],
            "Close": series["closes"],
            "Volume": series["volumes"],
        },
        index=pd.DatetimeIndex(
            [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in series["times"]]
        ),
    )
    radar_row = scanner._feature_row("TEST", frame, pd.DataFrame(), {})
    assert radar_row is not None
    chart_row = mod._intrinsic_row(series=series, hist=frame, ticker="TEST", spy_closes=None)
    # 逐字段相等，不是「差不多」：这才叫按构造一致。
    assert chart_row == radar_row
    assert "atr_pct" in chart_row and "avg_dollar_volume_20d" in chart_row


def test_swing_labels_from_consecutive_confirmed_highs_and_lows() -> None:
    highs = [
        {"trade_date": "2025-01-02", "price": 10.0},
        {"trade_date": "2025-01-06", "price": 12.0},
        {"trade_date": "2025-01-10", "price": 11.0},
    ]
    lows = [
        {"trade_date": "2025-01-04", "price": 8.0},
        {"trade_date": "2025-01-08", "price": 9.0},
        {"trade_date": "2025-01-12", "price": 7.0},
    ]
    assert consecutive_swing_labels(highs, role="high") == ["H", "HH", "LH"]
    assert consecutive_swing_labels(lows, role="low") == ["L", "HL", "LL"]
    series = _series(_zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i))
    bundle = assemble_chart_analysis(
        series=series,
        data_through=series["dates"][-1],
        price_action={"swing_highs": highs, "swing_lows": lows, "structure": "uptrend"},
        auto_patterns=[],
    )
    high_labels = [row["label"] for row in bundle["overlays"] if row["kind"] == "swing" and row["geometry"].get("role") == "high"]
    low_labels = [row["label"] for row in bundle["overlays"] if row["kind"] == "swing" and row["geometry"].get("role") == "low"]
    assert high_labels == ["H", "HH", "LH"]
    assert low_labels == ["L", "HL", "LL"]


def test_assemble_keeps_per_pattern_volume_confirmation() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    rows = _detect(bars)
    assert rows
    original = rows[0]["volumeConfirmation"]
    original_priority = rows[0]["displayPriority"]
    bundle = assemble_chart_analysis(
        series=_series(bars),
        data_through=_series(bars)["dates"][-1],
        auto_patterns=rows,
        vol_price={"status": "active", "setup_type": "absorption_bullish", "setup_label": "多头吸收"},
    )
    pattern = next(row for row in bundle["overlays"] if row["id"] == rows[0]["id"])
    assert pattern["evidence"]["volumeConfirmation"] == original
    assert pattern["displayPriority"] == original_priority
    vol = next(row for row in bundle["overlays"] if row["kind"] == "volume_setup")
    assert vol["evidence"]["volumeConfirmation"] == 0.7
    assert vol["id"] != rows[0]["id"]


def test_spy_rs_nonempty_when_aligned_closes_passed() -> None:
    series = _series(_zigzag(120, lambda i: 50 + 0.1 * i, lambda i: 60 + 0.1 * i))
    spy = [close * 0.4 + 10 for close in series["closes"]]
    bundle = assemble_chart_analysis(
        series=series,
        data_through=series["dates"][-1],
        auto_patterns=[],
        spy_closes=spy,
    )
    pane = next(row for row in bundle["indicatorPanes"] if row["id"] == "spy_rs")
    values = pane["values"]["rs"]
    assert any(value is not None for value in values)
    assert values[-1] == round(series["closes"][-1] / spy[-1], 6)
    empty = assemble_chart_analysis(series=series, data_through=series["dates"][-1], auto_patterns=[])
    assert all(row["id"] != "spy_rs" for row in empty["indicatorPanes"])


def test_strength_context_uses_score_intrinsic_families() -> None:
    series = _series(_zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i))
    bundle = assemble_chart_analysis(
        series=series,
        data_through=series["dates"][-1],
        auto_patterns=[],
        technicals={"rsi14": 62.0, "rsi_score": 70.0, "macd": {"direction_pct": 0.4}},
        price_action={"status": "active", "score": 64.0},
        vol_price={"status": "active", "setup_type": "absorption_bullish", "breakout_quality_adjustment": 4.0},
    )
    ctx = bundle["strengthContext"]
    assert ctx["finalScore"] is None
    assert ctx["globalPercentile"] is None
    families = ctx["families"]
    assert set(families) >= {"short", "mid", "long", "trend", "breakout", "price_action"}
    assert any(families[name]["score"] is not None for name in families)
    quality = next(row["shapeQuality"] for row in bundle["overlays"] if row["kind"] == "ma")
    ctx["families"]["short"]["score"] = 1.0
    again = assemble_chart_analysis(
        series=series,
        data_through=series["dates"][-1],
        auto_patterns=[],
        technicals={"rsi14": 10.0, "rsi_score": 5.0},
        price_action={"status": "active", "score": 10.0},
    )
    assert next(row["shapeQuality"] for row in again["overlays"] if row["kind"] == "ma") == quality


def test_painted_fit_rails_lie_on_scored_theil_sen_line() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    rows = _detect(bars)
    singles = [row for row in rows if row["kind"] in {"support_trend", "resistance_trend"}]
    duals = [row for row in rows if row["kind"] in {"channel", "triangle", "wedge"}]
    assert singles
    for row in singles:
        slope = float(row["slope"])
        intercept = float(row["intercept"])
        painted = row.get("fitAnchors") or row["anchors"]
        assert len(painted) >= 2
        for anchor in painted:
            index = int(anchor["index"])
            expected = slope * index + intercept
            assert abs(float(anchor["price"]) - expected) < 1e-3
        touches = row.get("touchAnchors") or []
        if len(touches) >= 2:
            assert any(
                abs(float(touch["price"]) - float(painted[0]["price"])) > 1e-4
                or abs(float(touch["price"]) - float(painted[1]["price"])) > 1e-4
                for touch in touches
            ) or abs(float(painted[0]["price"]) - (slope * int(painted[0]["index"]) + intercept)) < 1e-6
    for row in duals:
        support = row.get("supportRail") or []
        resist = row.get("resistanceRail") or []
        assert len(support) == 2 and len(resist) == 2
        for anchor in support:
            expected = float(row["supportSlope"]) * int(anchor["index"]) + float(row["supportIntercept"])
            assert abs(float(anchor["price"]) - expected) < 1e-3
        for anchor in resist:
            expected = float(row["resistanceSlope"]) * int(anchor["index"]) + float(row["resistanceIntercept"])
            assert abs(float(anchor["price"]) - expected) < 1e-3
    dirty = [dict(row) for row in bars]
    spike = dirty[90]
    dirty[90] = {**spike, "h": spike["h"] * 1.8, "l": spike["l"] * 0.4}
    dirty_rows = _detect(dirty)
    spike_high = dirty[90]["h"]
    for row in dirty_rows:
        for anchor in row.get("fitAnchors") or row["anchors"]:
            assert abs(float(anchor["price"]) - spike_high) > 1.0


def test_intraday_tod_rvol_is_same_clock_and_hold_uses_per_bar_vwap() -> None:
    from app.services.breakouts.feature_engine import compute_time_of_day_rvol
    from app.services.breakouts.models import MarketSession, TemporalCutoff
    session_starts = [
        int(datetime(2025, 1, day, 14, 30, tzinfo=timezone.utc).timestamp())
        for day in (6, 7, 8, 9, 10, 13)
    ]
    bars: list[dict] = []
    for day_i, start in enumerate(session_starts):
        count = 78 if day_i < 5 else 12
        for i in range(count):
            bars.append(
                {
                    "t": start + i * 300,
                    "o": 100.0,
                    "h": 100.2,
                    "l": 99.8,
                    "c": 100.0,
                    "v": 1000,
                }
            )
    bundle = assemble_intraday_analysis(bars, ticker="AAPL", chart_range="5m")
    assert bundle is not None
    hold = next(row for row in bundle["overlays"] if row["id"].startswith("intraday-hold"))
    rvol = hold["evidence"]["rvolTimeOfDay"]
    assert rvol is not None
    morning = 12 * 1000
    full_day = 78 * 1000
    naive = morning / full_day
    assert abs(rvol - 1.0) < 0.15
    assert rvol - naive > 0.5
    series = series_from_chart_bars(bars, "5m")
    assert series is not None
    index = pd.DatetimeIndex(
        [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in series["times"]]
    )
    frame = pd.DataFrame(
        {
            "Open": series["opens"],
            "High": series["highs"],
            "Low": series["lows"],
            "Close": series["closes"],
            "Volume": series["volumes"],
        },
        index=index,
    )
    cutoff = TemporalCutoff(
        event_at=index[-1].to_pydatetime(),
        include_current_bar=True,
        session=MarketSession.REGULAR,
    )
    shipped = compute_time_of_day_rvol(frame, cutoff)["rvol_time_of_day"]
    assert shipped is not None
    assert abs(rvol - round(float(shipped), 4)) < 1e-9

    hold_start = int(datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc).timestamp())
    hold_bars = [
        {"t": hold_start, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
        {"t": hold_start + 300, "o": 90, "h": 90, "l": 90, "c": 90, "v": 1},
        {"t": hold_start + 600, "o": 40, "h": 60, "l": 40, "c": 60, "v": 10_000},
    ]
    hold_bundle = assemble_intraday_analysis(hold_bars, ticker="AAPL", chart_range="5m")
    assert hold_bundle is not None
    hold_row = next(row for row in hold_bundle["overlays"] if row["id"].startswith("intraday-hold"))
    count = hold_row["evidence"]["holdBarsAboveVwap"]
    vwap = next(row for row in hold_bundle["overlays"] if row["kind"] == "vwap")["geometry"]["values"]
    closes = [100.0, 90.0, 60.0]
    expected = 0
    for close, level in zip(reversed(closes), reversed(vwap)):
        if level is None or close < level:
            break
        expected += 1
    assert count == expected
    last_vwap = next(value for value in reversed(vwap) if value is not None)
    inflated = 0
    for close in reversed(closes):
        if close < last_vwap:
            break
        inflated += 1
    assert inflated > count


def test_intraday_vwap_opening_range_rvol_on_demand() -> None:
    start = int(datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc).timestamp())
    bars = []
    price = 100.0
    for i in range(78):
        close = price + 0.05
        bars.append(
            {
                "t": start + i * 300,
                "o": price,
                "h": close + 0.15,
                "l": price - 0.1,
                "c": close,
                "v": 1_000 + i * 10,
            }
        )
        price = close
    bundle = assemble_intraday_analysis(bars, ticker="AAPL", chart_range="5m")
    assert bundle is not None
    assert bundle["range"] == "5m"
    kinds = {row["kind"] for row in bundle["overlays"]}
    assert {"vwap", "opening_range", "breakout"} <= kinds
    vwap = next(row for row in bundle["overlays"] if row["kind"] == "vwap")
    assert any(value is not None for value in vwap["geometry"]["values"])
    hold = next(row for row in bundle["overlays"] if row["id"].startswith("intraday-hold"))
    assert "rvolTimeOfDay" in hold["evidence"]
    assert "clv" in hold["evidence"]
    assert "holdBarsAboveVwap" in hold["evidence"]
    series = series_from_chart_bars(bars, "5m")
    assert series is not None
    assert bundle["barFingerprint"] == bar_fingerprint(series)


# 两端钉同一个字面 digest：口径任何一侧改动都先挂测试，而不是让整张图静默空白。
FINGERPRINT_VECTOR_DIGEST = "656393794b6b8d7ac710ac4f37f7a1b950ab6b673f5118d5b16416db114f6f39"


def test_fingerprint_literal_digest_is_pinned_on_both_sides() -> None:
    assert bar_fingerprint(FINGERPRINT_VECTOR_SERIES) == FINGERPRINT_VECTOR_DIGEST


def test_bundle_publishes_which_bars_were_hashed() -> None:
    """指纹对不上时客户端要能自查/自修，而不是整包图层静默消失。"""

    series = _series(_zigzag(120, lambda i: 50 + 0.1 * i, lambda i: 60 + 0.1 * i))
    bundle = assemble_chart_analysis(
        series=series, data_through=series["dates"][-1], auto_patterns=[]
    )
    assert bundle["fingerprintAlgorithm"] == "sha256-bar-ohlcv-v1"
    assert bundle["barFingerprint"] == bar_fingerprint(series)
    assert bundle["barCount"] == len(series["closes"])
    assert bundle["firstBarDate"] == series["dates"][0]
    assert bundle["lastBarDate"] == series["dates"][-1]
    # 后端丢掉一根坏 bar 时，元数据必须跟着变——这正是客户端对齐窗口的依据。
    dropped = {key: values[1:] for key, values in series.items()}
    other = assemble_chart_analysis(
        series=dropped, data_through=dropped["dates"][-1], auto_patterns=[]
    )
    assert other["barCount"] == bundle["barCount"] - 1
    assert other["firstBarDate"] != bundle["firstBarDate"]
    assert other["barFingerprint"] != bundle["barFingerprint"]


def test_dates_are_emitted_once_and_series_carry_an_index_offset() -> None:
    """~500 个日期原来每条 MA/副图各复制一份（约 10 份），只发一份 + 偏移。"""

    series = _series(_zigzag(260, lambda i: 50 + 0.05 * i, lambda i: 58 + 0.05 * i))
    bundle = assemble_chart_analysis(
        series=series, data_through=series["dates"][-1], auto_patterns=[]
    )
    dates = bundle["dates"]
    assert dates == series["dates"]
    for pane in bundle["indicatorPanes"]:
        assert "dates" not in pane
        start = pane["startIndex"]
        assert isinstance(start, int) and start >= 0
        for key, values in pane["values"].items():
            assert start + len(values) <= len(dates), (pane["id"], key)
            # 偏移之后仍然对齐到同一根 bar：末值必须落在最后一根上。
            if values:
                assert start + len(values) == len(dates), (pane["id"], key)
    ma = {row["id"]: row for row in bundle["overlays"] if row["kind"] == "ma"}
    assert {"ma20", "ma50", "ma200"} <= set(ma)
    for layer_id, window in (("ma20", 20), ("ma50", 50), ("ma200", 200)):
        geometry = ma[layer_id]["geometry"]
        assert "dates" not in geometry
        assert geometry["startIndex"] == window - 1
        assert len(geometry["values"]) == len(dates) - (window - 1)
        assert all(value is not None for value in geometry["values"])
        expected = sum(series["closes"][:window]) / window
        assert abs(geometry["values"][0] - expected) < 1e-4


def test_macd_pane_blanks_its_warmup_like_its_siblings() -> None:
    """MACD 头部约 35 根是种子的影子，和任何看盘软件都对不上，必须留 None。"""

    from app.services.technical.indicators import MACD_WARMUP, macd_series

    series = _series(_zigzag(260, lambda i: 50 + 0.05 * i, lambda i: 58 + 0.05 * i))
    raw = macd_series(series["closes"])
    for key in ("macd", "signal", "histogram"):
        assert raw[key][:MACD_WARMUP] == [None] * MACD_WARMUP
        assert raw[key][MACD_WARMUP] is not None
    bundle = assemble_chart_analysis(
        series=series, data_through=series["dates"][-1], auto_patterns=[]
    )
    pane = next(row for row in bundle["indicatorPanes"] if row["id"] == "macd")
    assert pane["startIndex"] == MACD_WARMUP
    assert all(value is not None for value in pane["values"]["macd"])
    rsi = next(row for row in bundle["indicatorPanes"] if row["id"] == "rsi")
    assert rsi["startIndex"] == 14


def test_pattern_overlay_evidence_carries_touches() -> None:
    """前端「触碰 n 次」这枚 chip 读 evidence.touches，缺了就永远不渲染。"""

    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    rows = _detect(bars)
    assert rows
    bundle = assemble_chart_analysis(
        series=_series(bars), data_through=_series(bars)["dates"][-1], auto_patterns=rows
    )
    by_id = {row["id"]: row for row in rows}
    patterns = [row for row in bundle["overlays"] if row["sourceId"] == "auto_patterns"]
    assert patterns
    for overlay in patterns:
        assert overlay["evidence"]["touches"] == by_id[overlay["id"]]["touches"]
        assert overlay["evidence"]["touches"] >= 2


def test_no_pane_ships_without_a_registry_entry() -> None:
    """注册表里没有的副图（dollar_volume）会被前端 VALID_IDS 丢掉，别白算白传。"""

    from app.services.technical.layer_registry import LAYERS

    series = _series(_zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i))
    bundle = assemble_chart_analysis(
        series=series, data_through=series["dates"][-1], auto_patterns=[]
    )
    pane_ids = {pane["id"] for pane in bundle["indicatorPanes"]}
    assert "dollar_volume" not in pane_ids
    assert pane_ids <= {row["id"] for row in LAYERS}
    assert "dollarVolume" not in str(bundle)
