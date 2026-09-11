from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from app.services import realtime_quotes as quotes
from tests.test_realtime_quotes import settings, trade


@pytest.mark.parametrize("prior_day,trade_day,official", [
    ("2026-09-03", "2026-09-04", False),
    ("2026-09-04", "2026-09-08", False),
    ("2026-09-03", "2026-09-04", True),
])
def test_first_trade_recovers_baseline_after_a_midnight_stale_snapshot(
    tmp_path, monkeypatch, prior_day, trade_day, official,
):
    clock = [datetime.fromisoformat(f"{trade_day}T04:01:00+00:00")]
    monkeypatch.setattr(quotes, "_utcnow", lambda: clock[0])

    async def reserve(*args, **kwargs):
        return True

    monkeypatch.setattr(quotes, "async_reserve_finnhub_request", reserve)

    async def run():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._desired_symbols = ["AAPL"]
        prior_time = "20:00:00" if official else "23:59:00"
        prior_at = datetime.fromisoformat(f"{prior_day}T{prior_time}+00:00")
        # The midnight request succeeds, but its last trade still belongs to
        # the previous session. Only a closing print proves that day's close.
        hub._apply_rest_quote("AAPL", {
            "c": 102 if official else 110, "pc": 100,
            "t": int(prior_at.timestamp()),
        })
        clock[0] = datetime.fromisoformat(f"{trade_day}T14:30:00+00:00")
        await hub._process_trade(trade(price=105, at=clock[0]))

        calls = []

        def transport(request):
            calls.append(request.url.params["symbol"])
            return httpx.Response(200, json={
                "c": 106, "pc": 102, "t": int(clock[0].timestamp()),
            })

        async def finish_iteration(_seconds):
            hub._running = False

        # Exercise the actual warm-loop decision without waiting in real time.
        monkeypatch.setattr(quotes, "asyncio", SimpleNamespace(sleep=finish_iteration))
        hub._running = True
        hub._lock_file = object()
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            hub._http = client
            await hub._warm_loop()

        view = (await hub.snapshot(["AAPL"]))["quotes"][0]
        assert calls == ([] if official else ["AAPL"])
        assert view["price"] == 105, "REST baseline recovery must retain the streamed last price"
        assert view["source"] == "finnhub_websocket"
        assert view["previous_close"] == 102
        assert view["change_pct"] == pytest.approx(100 * (105 - 102) / 102)
        assert not hub._quote_warmup_needed("AAPL")
        assert "AAPL" not in hub._rest_backoff

    asyncio.run(run())
