from __future__ import annotations

import json

import pytest

from app.services.stock_report_visuals import stock_report_visuals


def saved(payload):
    return {"payload_json": json.dumps({"as_of": "2026-10-10T21:00:00Z", **payload}), "account_id": "secret-account", "result_json": '{"text":"revenue 999999 stock price 1234"}'}


def chart(value, id):
    return next(item for item in value["charts"] if item["id"] == id)


def test_reads_saved_prices_dates_and_independent_financial_periods():
    row = saved({"price_history": {"status": "ok", "currency": "USD", "source": "manual_pull", "as_of": "2026-10-10T20:00:00Z", "bars": [{"t": 1767225600, "c": 42.75, "v": 0}]}, "financials": {"status": "partial", "currency": "USD", "source": "Yahoo/yfinance", "as_of": "2026-09-01", "component_as_of": {"quarterly_income": "2026-08-15"}, "quarterly": [{"period_end": "2026-06-30", "revenue": 0}, {"period_end": "2026-03-31", "revenue": 100}]}})
    value = stock_report_visuals(row)
    price = chart(value, "price_history")
    assert price["rows"] == [{"label": "2026-01-01", "value": 42.75, "evidence_id": "price_history.bars.0.c"}]
    assert price["unit"] == "usd"
    assert "2026-01-01" in price["caption"] and "2026-10-10T20:00:00Z" in price["caption"]
    revenue = chart(value, "quarterly_revenue")
    assert [row["value"] for row in revenue["rows"]] == [100, 0]
    assert "2026-06-30" in revenue["caption"] and "2026-08-15" in revenue["caption"]
    assert "2026-09-01" not in revenue["caption"]
    assert chart(value, "daily_volume")["rows"][0]["value"] == 0
    assert "secret-account" not in json.dumps(value)
    assert "999999" not in json.dumps(value)


@pytest.mark.parametrize("currency", [None, "EUR", "JPY", "currency units"])
def test_unknown_or_non_dollar_currency_never_becomes_usd(currency):
    value = stock_report_visuals(saved({"price_history": {"currency": currency, "bars": [{"t": 1767225600, "c": 10}]}, "financials": {"currency": currency, "quarterly": [{"period_end": "2026-06-30", "revenue": 100}]}}))
    assert value["charts"] == []
    assert len(value["notes"]) == 3


def test_quote_currency_fallback_does_not_override_explicit_foreign_currency():
    payload = {"company_profile": {"currency": "USD"}, "price_history": {"bars": [{"t": 1767225600, "c": 10}]}}
    assert chart(stock_report_visuals(saved(payload)), "price_history")["unit"] == "usd"
    payload["price_history"]["currency"] = "JPY"
    assert not stock_report_visuals(saved(payload))["charts"]


def test_real_structure_scores_only_and_finite_values():
    value = stock_report_visuals(saved({"scores": {"top_score": 0, "bottom_score": 88, "dip_buy_quality": None, "data_quality": 10 ** 400, "signal_data_quality": True, "trend": 5000, "secret_balance": 2}, "signals": {"raw": 123}, "price_history": {"currency": "USD", "bars": [{"t": 1767225600, "c": float("inf"), "v": float("nan")}, {"t": 1767312000, "c": True, "v": None}]}}))
    assert [item["value"] for item in chart(value, "structure_scores")["rows"]] == [0, 88]
    assert len(value["charts"]) == 1
    assert "5000" not in json.dumps(value)
    assert "secret_balance" not in json.dumps(value)


def test_fraction_surprise_is_ratio_without_percent_conversion():
    value = stock_report_visuals(saved({"earnings_history": {"source": "Yahoo/yfinance", "units": {"surprise_pct": "provider fraction"}, "items": [{"period_end": "2026-06-30", "surprise_pct": 0.1}, {"period_end": "2026-03-31", "surprise_pct": 0}]}}))
    earnings = chart(value, "earnings_surprise")
    assert earnings["unit"] == "ratio"
    assert [item["value"] for item in earnings["rows"]] == [0, 0.1]
    assert "2026-06-30" in earnings["caption"]
    assert not stock_report_visuals(saved({"earnings_history": {"items": [{"period_end": "2026-06-30", "surprise_pct": 10}]}}))["charts"]


def test_complete_context_status_coverage_and_missing_block_exclusion():
    value = stock_report_visuals(saved({"context_status": {"company_profile": "not_applicable", "price_history": "omitted_size", "financials": "timeout", "older_optional_block": "partial", "malformed": {"status": "ok"}}, "price_history": {"currency": "USD", "bars": [{"t": 1767225600, "c": 10}]}}))
    coverage = {item["key"]: item for item in value["coverage"]}
    assert coverage["price_history"]["status"] == "omitted_size"
    assert coverage["financials"]["status"] == "timeout"
    assert coverage["older_optional_block"]["status"] == "partial"
    assert coverage["malformed"]["status"] == "unavailable"
    assert coverage["company_profile"]["status"] == "not_applicable"
    assert value["charts"] == []


def test_saved_nested_context_also_projects_with_dates():
    value = stock_report_visuals(saved({"signal_context": {"status": {"analyst_consensus": "partial"}, "blocks": {"analyst_consensus": {"currency": "USD", "as_of": "2026-10-08", "targets": {"low": 10, "mean": 11, "median": None, "high": 12}}}}}))
    assert [item["value"] for item in chart(value, "analyst_targets")["rows"]] == [10, 11, 12]
    assert "2026-10-08" in chart(value, "analyst_targets")["caption"]
    assert next(item for item in value["coverage"] if item["key"] == "analyst_consensus")["as_of"] == "2026-10-08"


def test_rows_have_hard_caps_and_original_evidence_indices():
    payload = {"price_history": {"currency": "USD", "bars": [{"t": 1767225600 + i * 86400, "c": 10 + i, "v": i} for i in range(300)]}, "financials": {"currency": "USD", "quarterly": [{"period_end": f"2026-01-{i + 1:02}", "revenue": i} for i in range(20)]}}
    value = stock_report_visuals(saved(payload))
    assert len(chart(value, "price_history")["rows"]) == 250
    assert chart(value, "price_history")["rows"][0]["evidence_id"] == "price_history.bars.50.c"
    assert len(chart(value, "quarterly_revenue")["rows"]) == 16
    assert len(value["charts"]) <= 6


@pytest.mark.parametrize("row", [{}, {"payload_json": "not json"}, {"payload_json": "[]"}, {"payload_json": '{"as_of":"bad date"}'}, {"payload_json": "x" * (1024 * 1024 + 1)}])
def test_invalid_or_undated_saved_input_returns_none(row):
    assert stock_report_visuals(row) is None


def test_no_facts_returns_only_coverage_and_notes_without_new_timestamp():
    value = stock_report_visuals(saved({"context_status": {"recent_news": "unavailable"}}))
    assert value["version"] == "stock-report-visuals-v1"
    assert value["as_of"] == "2026-10-10T21:00:00Z"
    assert value["charts"] == [] and value["notes"]
    assert all(item["status"] == "unavailable" for item in value["coverage"])
