from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.services import earnings_enrichment as enrich, massive


TODAY = date(2026, 9, 4)
EXPIRATION = "2026-09-11"
FRESH = "2026-09-03T20:00:00+00:00"
STALE = "2026-08-01T20:00:00+00:00"


def _epoch(timestamp: str | None) -> float | None:
    return datetime.fromisoformat(timestamp).timestamp() if timestamp else None


def _move(monkeypatch: pytest.MonkeyPatch, provider: str, quotes: list[tuple]) -> dict:
    if provider == "massive":
        monkeypatch.setattr(massive, "configured", lambda: True)
        monkeypatch.setattr(massive, "options_capability_known_denied", lambda: False)
        monkeypatch.setattr(massive, "option_expirations", lambda *_, **__: [EXPIRATION])
        rows = [
            {
                "details": {"strike_price": strike, "contract_type": side},
                "last_quote": {"bid": 3.0, "ask": 3.2, "last_updated": _epoch(timestamp)},
                "underlying_asset": {"price": 100.0},
            }
            for side, strike, timestamp in quotes
        ]
        monkeypatch.setattr(massive, "_get", lambda *_, **__: {"results": rows})
        return enrich._massive_expected_move("TEST", TODAY, TODAY, "bmo")

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            if "/expirations/" in url:
                payload = {"expirations": [EXPIRATION]}
            else:
                payload = {
                    "side": [side for side, _, _ in quotes],
                    "strike": [strike for _, strike, _ in quotes],
                    "updated": [_epoch(timestamp) for _, _, timestamp in quotes],
                    "bid": [3.0] * len(quotes), "ask": [3.2] * len(quotes),
                    "underlyingPrice": [100.0] * len(quotes),
                }
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(enrich.httpx, "Client", Client)
    monkeypatch.setattr(enrich, "get_settings", lambda: SimpleNamespace(
        marketdata_token="fixture", marketdata_base_url="https://marketdata.test",
    ))
    return enrich._marketdata_expected_move("TEST", TODAY, TODAY, "bmo")


@pytest.mark.parametrize("provider", ["massive", "marketdata"])
@pytest.mark.parametrize("old_quote", [STALE, None])
def test_unrelated_fresh_contract_does_not_validate_stale_or_undated_straddle(
    monkeypatch: pytest.MonkeyPatch, provider: str, old_quote: str | None,
) -> None:
    result = _move(monkeypatch, provider, [
        ("call", 110.0, FRESH),
        ("call", 100.0, old_quote),
        ("put", 100.0, FRESH),
    ])

    assert result["expected_move_status"] != "active"
    assert "expected_move_pct" not in result


@pytest.mark.parametrize("provider", ["massive", "marketdata"])
def test_unrelated_stale_contract_does_not_hide_fresh_straddle(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    result = _move(monkeypatch, provider, [
        ("call", 110.0, STALE),
        ("call", 100.0, FRESH),
        ("put", 100.0, FRESH),
    ])

    assert result["expected_move_status"] == "active"
    assert result["expected_move_pct"] == 6.2


@pytest.mark.parametrize("provider", ["massive", "marketdata"])
def test_expected_move_timestamp_is_the_older_of_the_selected_quotes(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    earlier = "2026-09-02T20:00:00+00:00"
    result = _move(monkeypatch, provider, [
        ("call", 110.0, FRESH),
        ("call", 100.0, earlier),
        ("put", 100.0, FRESH),
    ])

    assert result["expected_move_status"] == "active"
    assert result["expected_move_observed_at"] == earlier
