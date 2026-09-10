"""Local browser fixture: real customer sessions, stock routes and persistence."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from password_server import _certificate

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3076)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="optix-customer-chart-browser-") as temporary:
        root = Path(temporary)
        os.environ["DATA_DIR"] = str(root / "data")
        for key in ("OPENAI_API_KEY", "FINNHUB_API_KEY", "MASSIVE_API_KEY", "INTERNAL_API_TOKEN", "MACROLENS_URL"):
            os.environ[key] = ""
        from fastapi import Depends, FastAPI
        from fastapi.responses import FileResponse, JSONResponse, Response
        from fastapi.staticfiles import StaticFiles
        import uvicorn
        from app.access import OwnerAccessRuntime, hash_owner_password, require_public_read_or_owner_access
        from app.api import access, accounts, stocks
        from app.personal_config import AccessConfig
        from app.services.accounts import AccountStore, set_account_store
        from app.stock_pull_snapshot import write_stock_pull_resources, _snapshot_document_cache

        app = FastAPI()
        app.state.access_runtime = OwnerAccessRuntime(
            AccessConfig(mode="password"), password_hash=hash_owner_password("fixture-owner-password"),
        )
        set_account_store(AccountStore(root / "accounts.db"))
        calls = []
        counts = {"1d": 120, "5m": 160, "15m": 128, "1h": 96, "1w": 80}

        def payload(symbol, period):
            count = counts[period]
            seconds = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1w": 604800}[period]
            # Fixed completed sessions keep this fixture independent of the
            # current clock and avoid classifying synthetic overnight bars as
            # regular-hours data in one analysis path but not another.
            ny = ZoneInfo("America/New_York")
            intraday = period in ("5m", "15m", "1h")
            if intraday:
                cursor = datetime(2026, 8, 28, 15, {"5m": 55, "15m": 45, "1h": 30}[period], tzinfo=ny)
            else:
                cursor = datetime(2026, 8, 24 if period == "1w" else 28, tzinfo=ny)
            timestamps = []
            while len(timestamps) < count:
                regular = cursor.weekday() < 5 and 570 <= cursor.hour * 60 + cursor.minute < 960
                eligible = regular if intraday else cursor.weekday() < 5
                if eligible:
                    timestamps.append(int(cursor.timestamp()))
                cursor -= timedelta(seconds=seconds)
            timestamps.reverse()
            return {
                "ticker": symbol, "name": symbol, "range": period, "price_adjustment": "raw",
                "price_provider": "Fixture", "source_status": "active", "visible": 80,
                "exchange_timezone": "America/New_York", "as_of": datetime.fromtimestamp(timestamps[-1], timezone.utc).isoformat(),
                "bars": [{"t": timestamps[n], "o": 100 + n / 10, "h": 102 + n / 10,
                          "l": 99 + n / 10, "c": 101 + n / 10, "v": 1000 + n, "ext": False} for n in range(count)],
                "ema20": [], "sma50": [],
            }

        async def provider(symbol, period, adjustment):
            calls.append({"ticker": symbol, "range": period, "adjustment": adjustment})
            await asyncio.sleep(0.1)
            return payload(symbol, period)

        stocks._stock_chart_impl = provider

        def seed():
            for symbol in ("NVDA", "AAPL", "SPY"):
                write_stock_pull_resources(symbol, {
                    "overview": ({"ticker": symbol, "name": symbol, "price": 112.9, "change": 1,
                                  "change_percent": 0.9, "market_cap": 1000000000, "price_provider": "Fixture"}, time.time()),
                    "daily_chart": (payload(symbol, "1d"), time.time()),
                    "signals": ({"rsi14": {"value": 50}, "return_20d": {"value": 1}, "macd_hist": {"value": 0.1}}, time.time()),
                })

        @app.post("/test/reset")
        async def reset():
            calls.clear()
            accounts.reset_rate_limits()
            stocks._endpoint_cache.clear()
            stocks._public_stock_pull_recent.clear()
            stocks._public_stock_pull_ticker_deadlines.clear()
            _snapshot_document_cache.clear()
            chart_dir = root / "data" / "stock-chart-snapshots-v1"
            if chart_dir.exists():
                shutil.rmtree(chart_dir)
            seed()
            return {"ok": True}

        @app.post("/test/restart-cache")
        async def restart_cache():
            stocks._endpoint_cache.clear()
            _snapshot_document_cache.clear()
            return {"ok": True}

        @app.get("/test/state")
        async def state():
            return {"provider_calls": calls}

        @app.get("/api/quotes")
        async def quotes():
            return {"quotes": [], "status": {"enabled": False, "configured": False, "allowed": False}}

        @app.get("/api/stocks/{ticker}/logo")
        async def logo(ticker: str):
            return Response(status_code=404)

        app.include_router(access.router)
        app.include_router(accounts.router)
        app.include_router(stocks.router, dependencies=[Depends(require_public_read_or_owner_access)])

        @app.get("/api/{path:path}")
        async def other_api(path: str):
            return JSONResponse(status_code=503, content={"detail": {"code": "public_snapshot_unavailable"}})

        dist = REPO / "frontend-src" / "dist"
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        async def document(path: str):
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

        seed()
        certificate, key = _certificate(root)
        uvicorn.run(app, host="127.0.0.1", port=args.port, ssl_certfile=str(certificate),
                    ssl_keyfile=str(key), log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
