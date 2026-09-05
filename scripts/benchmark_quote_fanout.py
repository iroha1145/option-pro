"""Deterministic fanout microbenchmark; no Finnhub, network or production load.

Run: PYTHONPATH=backend python scripts/benchmark_quote_fanout.py
The legacy path reproduces the pre-delta publisher's full-page snapshot loop.
Wall times measure envelope construction only; JSON byte counts are logical
payloads and exclude TCP/TLS overhead. They are not whole-server CPU claims.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from statistics import median
from time import perf_counter
from types import SimpleNamespace

from app.services import realtime_quotes as quotes


async def run(clients: int = 100, symbols: int = 46, changed: int = 1, repeats: int = 10):
    now = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
    hub = quotes.QuoteHub(SimpleNamespace(quotes_enabled=True, finnhub_api_key="synthetic"))
    requested = [f"S{i}" for i in range(symbols)]
    ids = [await hub.subscribe(requested) for _ in range(clients)]
    for symbol in requested:
        hub._store_quote(symbol, 100, now, now, "finnhub_websocket")
    rows = {}
    for mode in ("full_page", "delta"):
        times = []
        calls = 0
        original = hub._quote_view
        def view(symbol):
            nonlocal calls
            calls += 1
            return original(symbol)
        hub._quote_view = view
        frames = []
        for _ in range(repeats):
            for id in ids:
                while not hub._clients[id].queue.empty():
                    hub._clients[id].queue.get_nowait()
            hub._dirty_symbols = set(requested[:changed])
            hub._all_dirty = hub._status_dirty = False
            start = perf_counter()
            if mode == "full_page":
                for client in hub._clients.values():
                    if hub._dirty_symbols.intersection(client.provider_symbols):
                        hub._emit(client, {"event": "quotes", "data": await hub.snapshot(client.symbols)})
            else:
                hub._publish_pending()
            times.append((perf_counter() - start) * 1000)
            frames = [hub._clients[id].queue.get_nowait() for id in ids]
        hub._quote_view = original
        rows[mode] = {
            "median_build_ms": round(median(times), 3),
            "quote_view_calls_per_tick": calls // repeats,
            "quote_rows_per_tick": sum(len(f["data"]["quotes"]) for f in frames),
            "json_bytes_per_tick": sum(len(json.dumps(f, separators=(",", ":")).encode()) for f in frames),
        }
    return {"clients": clients, "symbols_per_client": symbols, "changed_symbols": changed,
            "repeats": repeats, "measurements": rows}


if __name__ == "__main__":
    async def main():
        print(json.dumps([await run(changed=1), await run(changed=46)], indent=2))
    asyncio.run(main())
