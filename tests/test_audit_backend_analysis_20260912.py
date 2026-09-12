"""Regression contracts for report identity, observed measurements and ORB evidence."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.api import earnings
from app.public_home_snapshot import validate_public_home_payload
from app.services.breakouts import base_detector
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.scoring import score_breakout
from app.services.technical import base_structure
from app.services.technical.structure import clean_series, _frame, compute_technical_structure
from test_breakout_base_detector import _base_frame
from test_breakout_event_anchor import orb_service, _scan, TailPrices
from test_breakout_service import AS_OF
from test_earnings_featured_and_enrichment import isolated_build, _calendar_row, _fmp_success, TODAY


def test_first_scan_confirms_completed_opening_holds_independently_of_daily_base(orb_service):
    event, _ = _scan(orb_service)
    assert event.features["hold_bars_above_opening_range"] == 4
    assert event.features["hold_bars_above_pivot"] == 0
    assert event.lifecycle_state.value == "CONFIRMED"


def test_first_scan_one_complete_hold_cannot_use_unfinished_bar(orb_service):
    orb_service.price_data = TailPrices({AS_OF - timedelta(minutes=10): 101.0})
    event, _ = _scan(orb_service)
    assert event.features["hold_bars_above_opening_range"] == 1
    assert event.lifecycle_state.value == "TRIGGERED"


@pytest.mark.parametrize("difference", [{"days_until": 7, "earnings_date": "2026-07-30"}, {"quarter": 3}, {"year": 2025}])
def test_conflicting_report_never_supplies_missing_estimates(isolated_build, difference):
    primary = {**_calendar_row("CLASH", eps_estimate=None), "quarter": 2, "year": 2026}
    secondary = {**primary, "eps_estimate": 9.9, "revenue_estimate": 999_000, **difference}
    payload = isolated_build(finnhub_rows=[primary], fmp_result=_fmp_success([secondary]))
    row = payload["earnings"][0]
    assert row["earnings_date"] == primary["earnings_date"]
    assert row["quarter"] == 2 and row["year"] == 2026
    assert row["eps_estimate"] is None and row["revenue_estimate"] is None
    assert row["estimate_source"] is None and row["estimate_sources"] == {}
    assert validate_public_home_payload("earnings", payload) == payload


@pytest.mark.parametrize("primary_eps", [None, 1.2])
def test_matching_report_tracks_each_supplemented_estimate_source(isolated_build, primary_eps):
    primary = {**_calendar_row("MATCH", eps_estimate=primary_eps), "quarter": 2, "year": 2026}
    secondary = {**_calendar_row("MATCH", eps_estimate=9.9), "revenue_estimate": 999_000}
    payload = isolated_build(finnhub_rows=[primary], fmp_result=_fmp_success([secondary]))
    row = payload["earnings"][0]
    assert row["eps_estimate"] == (primary_eps if primary_eps is not None else 9.9)
    assert row["revenue_estimate"] == 999_000
    assert row["estimate_sources"] == {
        "eps_estimate": "finnhub_calendar" if primary_eps is not None else "fmp_calendar",
        "revenue_estimate": "fmp_calendar",
    }
    assert row["estimate_source"] == ("mixed" if primary_eps is not None else "fmp_calendar")
    assert "FMP" in payload["providers"]
    assert validate_public_home_payload("earnings", payload) == payload
    row["estimate_sources"]["revenue_estimate"] = "invented"
    with pytest.raises(ValueError, match="invalid public home payload"):
        validate_public_home_payload("earnings", payload)


@pytest.mark.parametrize("same_date", [True, False])
def test_finnhub_replacement_does_not_inherit_another_reports_yahoo_bounds(isolated_build, monkeypatch, same_date):
    report_date = TODAY + timedelta(days=5 if same_date else 7)
    class YahooTicker:
        calendar = {"Earnings Date": [report_date], "Earnings Average": [1.25],
                    "Earnings High": [1.4], "Earnings Low": [1.1]}
        info = {"shortName": "Match Corp"}
        def get_earnings_dates(self, limit=12):
            raise AssertionError("a valid calendar is already available")
    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["MATCH"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _: YahooTicker())
    payload = isolated_build(finnhub_rows=[_calendar_row("MATCH")])
    row = payload["earnings"][0]
    assert row["eps_high"] == (1.4 if same_date else None)
    assert row["eps_low"] == (1.1 if same_date else None)
    assert row["estimate_source"] == ("mixed" if same_date else "finnhub_calendar")
    assert validate_public_home_payload("earnings", payload) == payload


@pytest.mark.parametrize("missing_atr", [False, True])
def test_missing_contraction_does_not_get_quality_or_scoring_weight(monkeypatch, missing_atr):
    frame = _base_frame().drop(columns=["Volume"])
    if missing_atr:
        original = base_detector.compute_atr
        monkeypatch.setattr(base_detector, "compute_atr", lambda data, window: None if len(data) < 60 else original(data, window))
    structure = base_detector._candidate("TEST", frame, settings=BreakoutSettings(_env_file=None))
    assert structure is not None
    metrics = structure.metrics
    assert structure.volume_contraction is None and metrics["volume_contraction_quality"] is None
    if missing_atr:
        assert structure.atr_contraction is None and metrics["atr_contraction_quality"] is None
    weights = {"tightness_quality": .25, "duration_quality": .15, "resistance_touch_quality": .15,
               "volume_contraction_quality": .15, "atr_contraction_quality": .1,
               "support_integrity": .1, "higher_low_quality": .1}
    active = {name: weight for name, weight in weights.items() if metrics[name] is not None}
    expected = sum(metrics[name] * weight for name, weight in active.items()) / sum(active.values()) / 100
    assert structure.quality == pytest.approx(expected, abs=1e-6)
    score = score_breakout(metrics).details["base_quality"]
    assert "volume_contraction_quality" in score.missing_components
    assert "volume_contraction_quality" not in score.effective_weights
    assert sum(score.effective_weights.values()) == pytest.approx(1, abs=3e-6)
    assert score.confidence == pytest.approx(.65 if missing_atr else .75)


@pytest.mark.parametrize("missing_atr", [False, True])
def test_chart_base_missing_turnover_uses_only_observed_quality(monkeypatch, missing_atr):
    frame = _base_frame()
    window = {name: list(frame[column]) for name, column in [("closes", "Close"), ("highs", "High"), ("lows", "Low")]}
    window["turnover"] = [None] * len(frame)
    if missing_atr:
        original = base_structure._atr
        monkeypatch.setattr(base_structure, "_atr", lambda highs, lows, closes, period: None if len(closes) < 60 else original(highs, lows, closes, period))
    result = base_structure._candidate(window, [stamp.date().isoformat() for stamp in frame.index])
    assert result is not None
    assert result["turnover_contraction"] is None
    assert result["metrics"]["turnover_contraction_quality"] is None
    assert "turnover_contraction" in result["quality_coverage"]["missing"]
    weights = {"tightness_quality": .25, "duration_quality": .15, "resistance_touch_quality": .15,
               "atr_contraction_quality": .1, "support_integrity": .1, "higher_low_quality": .1}
    if missing_atr:
        assert result["metrics"]["atr_contraction_quality"] is None
        assert "atr_contraction" in result["quality_coverage"]["missing"]
        weights.pop("atr_contraction_quality")
    expected = sum(result["metrics"][name] * weight for name, weight in weights.items()) / sum(weights.values()) / 100
    assert result["quality"] == pytest.approx(expected, abs=.0001)


def _volume_bars(tail_volume):
    start = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
    return [{"t": int((start + timedelta(days=i)).timestamp()), "o": 100., "c": 100.,
             "h": 100.1 if i >= 80 else 101., "l": 99.9 if i >= 80 else 99.,
             "v": tail_volume if i >= 80 else 1000.} for i in range(90)]


@pytest.mark.parametrize("unknown", [None, "absent", "bad", -1, float("nan"), float("inf")])
def test_chart_missing_volume_preserves_prices_and_cannot_fabricate_vacuum(unknown):
    bars = _volume_bars(unknown)
    if unknown == "absent":
        for bar in bars[-10:]:
            bar.pop("v")
    series = clean_series(bars)
    assert series is not None and len(series["closes"]) == 90
    assert series["volumes"][-10:] == [None] * 10
    assert series["turnover"][-10:] == [None] * 10
    assert _frame(series)["Volume"].tail(10).isna().all()
    result = compute_technical_structure(bars, now=datetime(2028, 1, 1, tzinfo=timezone.utc))
    match = result["vol_price"]
    assert match["setup_type"] != "vacuum"
    assert match["breakout_quality_adjustment"] == 0
    assert match["risk_penalty_adjustment"] == 0


def test_chart_observed_zero_volume_remains_zero():
    series = clean_series(_volume_bars(0))
    assert series["volumes"][-10:] == [0.] * 10
    assert series["turnover"][-10:] == [0.] * 10
    assert not _frame(series)["Volume"].tail(10).isna().any()


def test_realtime_orb_still_requires_whole_bars_after_observed_tick(orb_service):
    first, _ = _scan(orb_service)
    live = first.model_dump(mode="python")
    live.update(lifecycle_state="TRIGGERED", state_version=1,
                triggered_at=AS_OF + timedelta(seconds=10), trigger_source="fixture_trade")
    awaiting, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), live)
    assert awaiting.lifecycle_state.value == "TRIGGERED"
    assert "awaiting_complete_post_trigger_bar" in awaiting.warnings
    one, _ = _scan(orb_service, AS_OF + timedelta(minutes=10), live)
    assert one.lifecycle_state.value == "TRIGGERED"
    assert one.features["hold_bars_above_opening_range"] == 1
    confirmed, _ = _scan(orb_service, AS_OF + timedelta(minutes=15), live)
    assert confirmed.features["hold_bars_above_opening_range"] == 2
    assert confirmed.lifecycle_state.value == "CONFIRMED"


def test_finnhub_duplicate_date_cannot_merge_different_quarter_records(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"earningsCalendar": [
                {"symbol": "CLASH", "date": "2026-07-28", "quarter": 2, "year": 2026, "revenueEstimate": 1000},
                {"symbol": "CLASH", "date": "2026-07-28", "quarter": 3, "year": 2026, "epsEstimate": 9.9},
            ]}
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def get(self, *_args, **_kwargs):
            return Response()
    async def reserve(*_args, **_kwargs):
        return True
    monkeypatch.setattr(earnings, "get_settings", lambda: SimpleNamespace(finnhub_api_key="fixture", finnhub_base_url="https://fixture.invalid"))
    monkeypatch.setattr(earnings.httpx, "AsyncClient", lambda **_: Client())
    monkeypatch.setattr(earnings, "async_reserve_finnhub_request", reserve)
    result = asyncio.run(earnings._fetch_finnhub_earnings(TODAY))
    assert result["succeeded"] is True and len(result["rows"]) == 1
    row = result["rows"][0]
    assert (row["quarter"], row["eps_estimate"], row["revenue_estimate"]) in {(2, None, 1000), (3, 9.9, None)}


def test_existing_single_source_snapshot_without_field_map_remains_readable(isolated_build):
    payload = isolated_build(finnhub_rows=[_calendar_row("LEGACY")])
    payload["earnings"][0].pop("estimate_sources")
    assert validate_public_home_payload("earnings", payload) == payload


@pytest.mark.parametrize("missing", ["all", "partial"])
def test_missing_volume_keeps_complete_chart_bundle_and_price_layers(missing):
    from test_auto_technical_patterns import _zigzag
    from app.services.technical.auto_patterns import detect_auto_patterns
    bars = _zigzag(180, lambda i: 50 + .16 * i, lambda i: 64 + .16 * i)
    now = datetime(2028, 1, 1, tzinfo=timezone.utc)
    reference = compute_technical_structure(bars, now=now)["chart_analysis"]
    for i, bar in enumerate(bars):
        if missing == "all" or i == 90:
            bar["v"] = None
    result = compute_technical_structure(bars, now=now)
    bundle = result["chart_analysis"]
    assert bundle is not None and bundle["barCount"] == 180
    expected_prices = {row["id"]: row["geometry"] for row in reference["overlays"] if row["kind"] in {"ma", "swing", "level"}}
    actual_prices = {row["id"]: row["geometry"] for row in bundle["overlays"] if row["kind"] in {"ma", "swing", "level"}}
    assert actual_prices == expected_prices
    assert any(row["sourceId"] == "auto_patterns" for row in bundle["overlays"])
    panes = {pane["id"]: pane for pane in bundle["indicatorPanes"]}
    ref_panes = {pane["id"]: pane for pane in reference["indicatorPanes"]}
    assert panes["clv"] == ref_panes["clv"]
    assert panes["rsi"] == ref_panes["rsi"] and panes["macd"] == ref_panes["macd"]
    obv = panes["obv"]["values"]["obv"]
    if missing == "all":
        assert all(value is None for value in obv)
    else:
        assert obv[90] is None and obv[91] == 0.0
        assert obv[:90] == ref_panes["obv"]["values"]["obv"][:90]
    series = clean_series(bars)
    patterns = detect_auto_patterns(series, data_through=series["dates"][-1])
    assert patterns
    assert all(row["volumeConfirmation"] is None for row in patterns)
    assert all(row["evidence"]["volumeConfirmation"] is None for row in patterns)


def test_obv_restarts_after_gap_without_affecting_price_only_clv():
    from app.services.technical.chart_analysis import _volume_series
    series = {"closes": [1., 2., 3., 4., 3.], "highs": [2., 3., 4., 5., 4.],
              "lows": [0., 1., 2., 3., 2.], "volumes": [1000., 1000., None, 2000., 500.]}
    result = _volume_series(series)
    assert result["obv"] == [0., 1000., None, 0., -500.]
    assert result["clv"] == [0.] * 5


@pytest.mark.parametrize("missing", ["all", "previous_session", "current_session"])
def test_missing_intraday_volume_preserves_opening_range_and_resets_vwap_only_next_session(missing):
    from app.services.technical.chart_analysis import assemble_intraday_analysis
    bars = []
    for session in range(2):
        start = datetime(2026, 7, 9 + session, 13, 30, tzinfo=timezone.utc)
        for i in range(12):
            volume = None if missing == "all" or (i == 6 and session == (0 if missing == "previous_session" else 1)) else 1000.
            price = 100. + session * 10 + i
            bars.append({"t": int((start + timedelta(minutes=5 * i)).timestamp()),
                         "o": price, "c": price, "h": price + 1, "l": price - 1, "v": volume})
    bundle = assemble_intraday_analysis(bars, ticker="TEST", chart_range="5m", now=datetime(2028, 1, 1, tzinfo=timezone.utc))
    assert bundle is not None and bundle["barCount"] == 24
    overlays = {row["id"]: row for row in bundle["overlays"]}
    assert overlays["opening-range"]["geometry"]["high"] == 116.
    assert overlays["opening-range"]["geometry"]["low"] == 109.
    values = overlays["vwap"]["geometry"]["values"]
    hold = next(row for row in bundle["overlays"] if row["id"].startswith("intraday-hold"))
    if missing == "all":
        assert values == [None] * 24
    elif missing == "previous_session":
        assert values[5] == 102.5 and values[6:12] == [None] * 6
        assert values[12] == 110. and values[-1] == 115.5
        assert hold["geometry"]["vwap"] == 115.5
    else:
        assert values[17] == 112.5 and values[18:] == [None] * 6
    if missing != "previous_session":
        assert hold["geometry"]["vwap"] is None
        assert hold["evidence"]["holdBarsAboveVwap"] is None
        assert hold["evidence"]["rvolTimeOfDay"] is None


def test_missing_volume_fingerprint_has_shared_null_token_and_distinguishes_zero():
    from app.services.technical.chart_analysis import canonical_bar_payload, bar_fingerprint
    series = {"times": [1700000000, 1700086400], "opens": [10., 10.5], "highs": [11., 12.],
              "lows": [9., 10.], "closes": [10.5, 11.], "volumes": [None, 0.]}
    expected = "1700000000|10.000000|11.000000|9.000000|10.500000|null|0|0\n1700086400|10.500000|12.000000|10.000000|11.000000|0.000000|0|0"
    assert canonical_bar_payload(series) == expected
    assert bar_fingerprint(series) == "e210acdc4ad8160ffd7fbf49d60c4305307fdb132f07c956f1a9ccef56310329"
    unknown_digest = bar_fingerprint(series)
    series["volumes"] = [float("nan"), 0.]
    assert bar_fingerprint(series) == unknown_digest
    series["volumes"] = [0., 0.]
    assert bar_fingerprint(series) != unknown_digest
