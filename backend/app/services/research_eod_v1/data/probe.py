"""Capability probe. Local first, Yahoo next, Massive env last. No secret echo."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.research_eod_v1.data.contract import UNSUPPORTED
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider
from app.services.research_eod_v1.data.massive_env import MassiveEnvProvider
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider

PROBE_SAMPLE = ("SPY", "AAPL", "NVDA", "JNJ", "XOM")


def run_provider_probe(
    *,
    allow_network: bool = True,
    start: date = date(2024, 1, 2),
    end: date = date(2024, 3, 1),
    sample: tuple[str, ...] = PROBE_SAMPLE,
) -> dict[str, Any]:
    local = LocalParquetProvider()
    yahoo = YahooDiagnosticProvider(allow_network=allow_network)
    massive = MassiveEnvProvider(allow_network=allow_network)
    report: dict[str, Any] = {
        "local": local.probe_capabilities().to_dict(),
        "yahoo": yahoo.probe_capabilities().to_dict(),
        "massive_env": massive.probe_capabilities().to_dict(),
        "sample": [],
        "selected_provider": None,
        "secret_present_in_report": False,
    }
    local_hits = 0
    for symbol in sample:
        bars = local.fetch_daily_bars(symbol, start, end)
        if isinstance(bars, list) and bars:
            local_hits += 1
            report["sample"].append({"symbol": symbol, "provider": "local_parquet", "bars": len(bars)})
    if local_hits:
        report["selected_provider"] = "local_parquet"
        return report

    yahoo_hits = 0
    for symbol in sample:
        bars = yahoo.fetch_daily_bars(symbol, start, end)
        row = {"symbol": symbol, "provider": "yahoo_yfinance", "bars": 0, "status": "empty"}
        if bars == UNSUPPORTED:
            row["status"] = UNSUPPORTED
        elif isinstance(bars, list):
            row["bars"] = len(bars)
            row["status"] = "ok" if bars else "empty"
            yahoo_hits += 1 if bars else 0
        report["sample"].append(row)
    report["yahoo_failures"] = list(yahoo.failures)
    if yahoo_hits:
        report["selected_provider"] = "yahoo_yfinance"
        return report

    massive_hits = 0
    for symbol in sample:
        bars = massive.fetch_daily_bars(symbol, start, end)
        row = {"symbol": symbol, "provider": "massive_env", "bars": 0, "status": "empty"}
        if bars == UNSUPPORTED:
            row["status"] = "AUTH_REQUIRED_OR_UNSUPPORTED"
        elif isinstance(bars, list):
            row["bars"] = len(bars)
            row["status"] = "ok" if bars else "empty"
            massive_hits += 1 if bars else 0
        report["sample"].append(row)
    report["massive_failures"] = [
        {k: v for k, v in item.items() if k != "detail" or "key" not in str(v).lower()}
        for item in massive.failures
    ]
    if massive_hits:
        report["selected_provider"] = "massive_env"
    else:
        report["selected_provider"] = None
    return report
