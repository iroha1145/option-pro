from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import pytest

from app.services import signal_context
from app.services import stock_research_context as context


def test_saved_price_history_preserves_zero_volume_and_bar_time(monkeypatch):
    bars = [{"t": 1700000000 + i * 86400, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 0 if i == 299 else None} for i in range(300)]
    monkeypatch.setattr(context, "read_latest_stock_resource", lambda *args: {"payload": {"bars": bars, "price_adjustment": "raw"}, "saved_at": 1900000000, "fresh": False, "source": "manual_pull"})
    value = context.price_history("AMD")
    assert len(value["bars"]) == 250
    assert value["bars"][-1]["v"] == 0
    assert value["bars"][0]["v"] is None
    assert value["data_through"] == datetime.fromtimestamp(bars[-1]["t"], timezone.utc).isoformat()
    assert value["data_through"] != value["as_of"]
    assert value["truncated"] and value["stale"]
    assert value["currency"] is None


def test_missing_saved_history_does_not_fetch(monkeypatch):
    monkeypatch.setattr(context, "read_latest_stock_resource", lambda *args: None)
    monkeypatch.setattr(context, "_provider", lambda *args: pytest.fail("must not pull prices"))
    assert context.price_history("AMD") is None


def test_saved_profile_is_whitelisted_and_description_bounded(monkeypatch):
    monkeypatch.setattr(context, "read_latest_stock_resource", lambda *args: {"payload": {"market_cap": 0, "pe_ratio": None, "description_en": "a" * 7000, "private": "never", "name_en": "AMD"}, "saved_at": 10, "fresh": True})
    value = context.company_profile("AMD")
    assert value["profile"]["market_cap"] == 0
    assert value["profile"]["pe_ratio"] is None
    assert len(value["profile"]["business_description"]) == 6000
    assert value["description_truncated"]
    assert "private" not in str(value)


def test_indices_skip_company_facts_before_io(monkeypatch):
    monkeypatch.setattr(context, "_provider", lambda *args: pytest.fail("index must not fetch company facts"))
    monkeypatch.setattr(context, "read_latest_stock_resource", lambda *args: pytest.fail("index must not load company overview"))
    for load in (context.company_profile, context.financials, context.analyst_consensus, context.earnings_history):
        assert load("^GSPC")["status"] == "not_applicable"


def test_financial_periods_and_partial_statement_failure(monkeypatch):
    period = pd.Timestamp("2026-06-30")
    def provider(symbol, resource, load):
        if resource.endswith("balance"):
            raise TimeoutError()
        rows = {"TotalRevenue": [0], "NetIncome": [float("nan")]} if resource.endswith("income") else {"FreeCashFlow": [123]}
        return pd.DataFrame(rows, index=[period]).T, context._base("test", as_of="2026-08-10T00:00:00Z")
    monkeypatch.setattr(context, "_provider", provider)
    monkeypatch.setattr(context, "_info", lambda symbol: ({"financialCurrency": "EUR"}, {}))
    value = context.financials("AMD")
    assert value["status"] == "partial"
    assert value["currency"] == "EUR"
    assert value["quarterly"][0]["period_end"] == "2026-06-30"
    assert value["quarterly"][0]["revenue"] == 0
    assert value["quarterly"][0]["net_income"] is None
    assert value["quarterly"][0]["free_cash_flow"] == 123
    assert value["data_through"] == "2026-06-30"
    assert value["component_status"]["quarterly_balance"] == "timeout"


def test_earnings_period_uses_quarter_and_keeps_actual_zero(monkeypatch):
    frame = pd.DataFrame({"quarter": [pd.Timestamp("2026-06-30")], "epsActual": [0], "epsEstimate": [None], "surprisePercent": [0.15]})
    monkeypatch.setattr(context, "_provider", lambda *args: (frame, context._base("test", as_of="2026-08-01")))
    value = context.earnings_history("AMD")
    assert value["items"][0]["period_end"] == "2026-06-30"
    assert value["items"][0]["eps_actual"] == 0
    assert value["items"][0]["eps_estimate"] is None
    assert value["items"][0]["surprise_pct"] == 0.15


def test_provider_reuses_existing_cache_metadata_contract(monkeypatch):
    calls = []
    def cached(key, seconds, loader, **kwargs):
        calls.append((key, seconds, kwargs))
        return {"marketCap": 0}, {"as_of": "2026-07-01", "_stale": True}
    monkeypatch.setattr(context.yahoo, "_cached", cached)
    info, meta = context._info("AMD")
    assert info["marketCap"] == 0
    assert meta["stale"] is True and meta["as_of"] == "2026-07-01"
    assert calls[0][1] == 6 * 3600
    assert calls[0][2]["with_metadata"] is True


def test_new_block_failure_and_timeout_do_not_drop_other_blocks(monkeypatch):
    async def empty(*args):
        return None
    async def market():
        return {"source": "test"}
    monkeypatch.setattr(signal_context, "_market_block", market)
    monkeypatch.setattr(signal_context, "_earnings_block", empty)
    monkeypatch.setattr(signal_context, "_options_block", lambda *args: None)
    monkeypatch.setattr(signal_context, "_news_block", lambda *args: None)
    monkeypatch.setattr(signal_context, "macro_conditions_context", lambda: None)
    def failure(symbol):
        raise ValueError("no data")
    monkeypatch.setattr(context, "BLOCK_LOADERS", {"company_profile": lambda symbol: {"status": "partial", "profile": {"market_cap": 0}}, "financials": failure, "earnings_history": lambda symbol: {"status": "not_applicable"}})
    value = asyncio.run(signal_context.build_signal_context("AMD"))
    assert value["status"]["company_profile"] == "partial"
    assert value["status"]["financials"] == "unavailable"
    assert value["status"]["earnings_history"] == "not_applicable"
    assert value["blocks"]["company_profile"]["profile"]["market_cap"] == 0
    assert value["status"]["market_context"] == "ok"


def test_research_timeout_is_explicit_and_other_facts_survive(monkeypatch):
    async def empty(*args):
        return None
    async def research(name, loader, symbol):
        if name == "financials":
            await asyncio.sleep(1)
        return {"source": "test", "status": "ok", "profile": {"market_cap": 0}}
    monkeypatch.setattr(signal_context, "_market_block", empty)
    monkeypatch.setattr(signal_context, "_earnings_block", empty)
    monkeypatch.setattr(signal_context, "_options_block", lambda *args: None)
    monkeypatch.setattr(signal_context, "_news_block", lambda *args: None)
    monkeypatch.setattr(signal_context, "macro_conditions_context", lambda: None)
    monkeypatch.setattr(signal_context, "_research_block", research)
    monkeypatch.setattr(signal_context, "CONTEXT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(context, "BLOCK_LOADERS", {"financials": lambda symbol: None, "company_profile": lambda symbol: None})
    value = asyncio.run(signal_context.build_signal_context("AMD"))
    assert value["status"]["financials"] == "timeout"
    assert value["blocks"]["financials"]["data_through"] is None
    assert value["blocks"]["company_profile"]["profile"]["market_cap"] == 0
