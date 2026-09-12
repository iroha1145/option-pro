"""Long-lived provider clients close in both serving processes, including failure paths."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("hub_close_fails", [False, True])
def test_api_shutdown_closes_all_clients_even_if_quote_hub_fails(monkeypatch, hub_close_fails):
    from app import config, main
    from app.services import massive, realtime_quotes

    closed = []

    class Hub:
        def __init__(self, *_args, **_kwargs):
            pass

        async def start(self):
            pass

        async def close(self):
            closed.append("quotes")
            if hub_close_fails:
                raise RuntimeError("hub close failed")

    async def start_logos():
        pass

    async def close_logos():
        closed.append("logos")

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(quotes_enabled=False, quotes_signals_enabled=False))
    monkeypatch.setattr(realtime_quotes, "QuoteHub", Hub)
    monkeypatch.setattr(main.stocks, "start_company_logo_client", start_logos)
    monkeypatch.setattr(main.stocks, "close_company_logo_client", close_logos)
    monkeypatch.setattr(massive, "close", lambda: closed.append("massive"))
    application = SimpleNamespace(state=SimpleNamespace())

    async def run():
        async with main._lifespan(application):
            assert application.state.quote_hub is not None

    if hub_close_fails:
        with pytest.raises(RuntimeError, match="hub close failed"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert closed == ["quotes", "logos", "massive"]
    assert application.state.quote_hub is None


@pytest.mark.parametrize("run_fails", [False, True])
def test_worker_closes_massive_after_async_runtime_exits(monkeypatch, tmp_path, run_fails):
    from app.worker import __main__ as cli
    from app.services import massive

    order = []

    async def run(_once, _settings):
        order.append("run")
        if run_fails:
            raise RuntimeError("fixture worker failed")
        return 0

    monkeypatch.setattr(cli, "_load_worker_settings", lambda: SimpleNamespace(optix_worker_db_path=tmp_path / "worker.db"))
    monkeypatch.setattr(cli, "_run", run)
    monkeypatch.setattr(massive, "close", lambda: order.append("close"))
    assert cli.main(["--once"]) == (1 if run_fails else 0)
    assert order == ["run", "close"]
