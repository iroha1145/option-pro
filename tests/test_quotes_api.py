from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.middleware.gzip import GZipMiddleware
import pytest

from app.access import OWNER_COOKIE_NAME, OwnerAccessRuntime, hash_owner_password, require_public_read_or_owner_access
from app.api import quotes
from app.main import _GatewayMiddleware
from app.personal_config import AccessConfig, QuotesConfig


class FakeHub:
    def __init__(self):
        self.subscriptions = []
        self.unsubscribed = []
        self.expire = None

    async def snapshot(self, symbols):
        return {
            "quotes": [{"symbol": symbol, "price": 123.45} for symbol in symbols],
            "status": {"enabled": True, "configured": True, "connected": True,
                       "connection_status": "connected", "allocated_symbols": ["AAPL", "SECRET_RESEARCH"],
                       "client_count": 3, "max_symbols": 50},
        }

    async def subscribe(self, symbols, *, focus):
        self.subscriptions.append((symbols, focus))
        return "client-1"

    def unsubscribe(self, client_id):
        self.unsubscribed.append(client_id)

    async def events(self, _client_id):
        yield {"event": "quotes", "data": await self.snapshot(["AAPL"])}
        if self.expire:
            self.expire()
        yield {"event": "radar", "data": {"events": [{"ticker": "AAPL", "lifecycle_state": "TRIGGERED"}]}}


def make_app(*, public=False, enabled=True, key="test-finnhub-key"):
    runtime = OwnerAccessRuntime(AccessConfig(mode="password"), password_hash=hash_owner_password("test-password"))
    app = FastAPI()
    app.state.access_runtime = runtime
    app.state.quote_settings = SimpleNamespace(
        quotes_enabled=enabled, quotes_public_enabled=public, quotes_signals_enabled=True,
        finnhub_api_key=key, quotes_max_symbols=50,
    )
    hub = FakeHub()
    app.state.quote_hub = hub
    app.include_router(quotes.router, dependencies=[Depends(require_public_read_or_owner_access)])
    app.add_middleware(_GatewayMiddleware, access_runtime=runtime)
    app.add_middleware(GZipMiddleware, minimum_size=1)
    return app, runtime, hub


def test_private_quotes_are_not_disclosed_to_visitors_and_probe_does_not_subscribe():
    app, _runtime, hub = make_app()
    with TestClient(app) as client:
        response = client.get("/api/quotes?symbols=AAPL")
        assert response.status_code == 200
        assert response.json()["quotes"] == []
        assert response.json()["status"]["allowed"] is False
        assert client.get("/api/quotes/stream?symbols=AAPL").status_code == 403
    assert hub.subscriptions == []


def test_owner_can_read_snapshot_without_reserving_or_exposing_others_symbols():
    app, runtime, hub = make_app()
    token = runtime.login("test-password", client_key="test").session_token
    with TestClient(app) as client:
        client.cookies.set(OWNER_COOKIE_NAME, token)
        response = client.get("/api/quotes?symbols=aapl,AAPL,BRK.B")
    assert response.status_code == 200
    assert response.json()["status"]["allowed"] is True
    assert response.json()["status"]["allocated_symbols"] == 2
    assert [row["symbol"] for row in response.json()["quotes"]] == ["AAPL", "BRK.B"]
    assert "SECRET_RESEARCH" not in response.text
    assert "test-finnhub-key" not in response.text
    assert not hub.subscriptions


@pytest.mark.parametrize("symbols", ["AAPL,,MSFT", "BINANCE:BTCUSDT", "^GSPC", "../token", ",".join(["AAPL"] * 201)])
def test_quote_input_is_bounded_and_accepts_only_us_symbol_shape(symbols):
    app, _, hub = make_app(public=True)
    with TestClient(app) as client:
        response = client.get("/api/quotes", params={"symbols": symbols})
    assert response.status_code == 422
    assert not hub.subscriptions


def test_public_stream_is_same_origin_uncompressed_and_releases_subscription():
    app, _, hub = make_app(public=True)
    with TestClient(app) as client:
        response = client.get("/api/quotes/stream?symbols=AAPL&focus=AAPL", headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in response.headers
    assert response.headers["x-accel-buffering"] == "no"
    assert "no-store" in response.headers["cache-control"]
    assert "event: quotes" in response.text and "event: radar" in response.text
    assert "SECRET_RESEARCH" not in response.text
    assert hub.subscriptions == [(["AAPL"], ["AAPL"])]
    assert hub.unsubscribed == ["client-1"]
    assert not app.state.quote_stream_clients


@pytest.mark.parametrize("headers", [{"Origin": "https://unrelated.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}])
def test_foreign_origin_cannot_allocate_stream_subscriptions(headers):
    app, _, hub = make_app(public=True)
    with TestClient(app) as client:
        assert client.get("/api/quotes/stream?symbols=AAPL", headers=headers).status_code == 403
    assert not hub.subscriptions


def test_focus_cannot_add_symbols_outside_current_page():
    app, _, hub = make_app(public=True)
    with TestClient(app) as client:
        assert client.get("/api/quotes/stream?symbols=AAPL&focus=MSFT").status_code == 422
    assert not hub.subscriptions


def test_expiring_owner_stream_stops_delivering_live_data():
    app, runtime, hub = make_app()
    token = runtime.login("test-password", client_key="test").session_token
    hub.expire = lambda: runtime.logout(token)
    with TestClient(app) as client:
        client.cookies.set(OWNER_COOKIE_NAME, token)
        response = client.get("/api/quotes/stream?symbols=AAPL")
    assert '"allowed":false' in response.text
    assert "event: radar" not in response.text
    assert hub.unsubscribed == ["client-1"]


def test_disabled_or_unconfigured_features_do_not_allocate():
    for kwargs in ({"enabled": False}, {"key": ""}):
        app, _, hub = make_app(public=True, **kwargs)
        with TestClient(app) as client:
            assert client.get("/api/quotes").json()["status"]["allowed"] is False
            assert client.get("/api/quotes/stream").status_code == 403
        assert not hub.subscriptions


def test_quotes_config_keeps_free_capacity_and_independent_opt_in_flags():
    default = QuotesConfig()
    assert not default.enabled and not default.public_enabled and not default.signals_enabled
    assert default.max_symbols == 50 and default.publish_interval_ms == 250
    assert QuotesConfig(signals_enabled=True).enabled is False
    with pytest.raises(ValueError):
        QuotesConfig(max_symbols=51)
    with pytest.raises(ValueError):
        QuotesConfig(release_seconds=31)


def test_connection_closing_before_first_body_chunk_releases_ip_and_hub():
    app, _, hub = make_app(public=True)

    async def run():
        scope = {"type": "http", "method": "GET", "path": "/api/quotes/stream",
                 "scheme": "http", "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
                 "headers": [(b"host", b"testserver")], "query_string": b"", "app": app,
                 "asgi": {"version": "3.0", "spec_version": "2.4"}}

        async def receive():
            return {"type": "http.disconnect"}

        async def send(_message):
            raise RuntimeError("socket closed before headers")

        response = await quotes.quotes_stream(Request(scope, receive), symbols="AAPL", focus="")
        with pytest.raises(RuntimeError, match="socket closed"):
            await response(scope, receive, send)

    asyncio.run(run())
    assert hub.unsubscribed == ["client-1"]
    assert not app.state.quote_stream_clients
