from __future__ import annotations

import socket

from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.snapshot import compute_snapshot


def test_compute_snapshot_makes_no_external_socket_calls(monkeypatch) -> None:
    days = trading_days(__import__("datetime").date(2018, 1, 2), 260)
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(260, 40, 0.1)),
        "SPY": make_series("SPY", days, trending_close(260, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    seen: list[str] = []

    def blocked(self, address, *args, **kwargs):
        seen.append(str(address))
        raise AssertionError(f"unexpected socket connect: {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    payload = compute_snapshot(
        as_of_after_close(days[-1]),
        panel,
        "u",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
    )
    assert seen == []
    assert payload["session_date"] == days[-1].isoformat()
    assert "NVDA" in payload["candidate_ids"]
