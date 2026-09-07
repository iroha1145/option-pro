"""Regressions for current-session quotes being compared with midnight."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import earnings_enrichment as enrich
from app.services import massive


NOW = datetime(2026, 9, 4, 20, 5, tzinfo=timezone.utc)
QUOTE_TIME = "2026-09-04T20:00:00+00:00"


@pytest.mark.parametrize(
    ("observed_at", "expected"),
    [
        ("2026-09-04T13:30:00+00:00", True),
        (QUOTE_TIME, True),
        (NOW.isoformat(), True),
        ((NOW - timedelta(days=5)).isoformat(), True),
        ((NOW - timedelta(days=5, seconds=1)).isoformat(), False),
        ((NOW + timedelta(hours=12, seconds=1)).isoformat(), False),
        ("2099-01-01T00:00:00+00:00", False),
        (None, False),
        ("not-a-time", False),
    ],
)
def test_quote_freshness_uses_actual_instant(observed_at, expected) -> None:
    assert enrich._quote_is_fresh(observed_at, NOW) is expected


def test_quote_freshness_accepts_an_equivalent_zoned_clock() -> None:
    now_ny = NOW.astimezone(ZoneInfo("America/New_York"))
    assert enrich._quote_is_fresh(QUOTE_TIME, now_ny)


def _snapshot() -> dict:
    return {
        "as_of": "2026-09-04T20:02:00+00:00",
        "underlying_price": 100.0,
        "calls": [{"strike": 100, "bid": 3.8, "ask": 4.2, "quote_as_of": QUOTE_TIME}],
        "puts": [{"strike": 100, "bid": 3.4, "ask": 3.6, "quote_as_of": QUOTE_TIME}],
    }


@pytest.mark.parametrize("provider", ["massive", "marketdata"])
def test_providers_keep_valid_same_day_straddle(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(enrich, "datetime", FixedDateTime)
    if provider == "massive":
        monkeypatch.setattr(massive, "configured", lambda: True)
        monkeypatch.setattr(massive, "options_capability_known_denied", lambda: False)
        monkeypatch.setattr(massive, "option_expirations", lambda *_args, **_kwargs: ["2026-09-11"])
        monkeypatch.setattr(massive, "option_chain_snapshot", lambda *_args: _snapshot())
        result = enrich._massive_expected_move("TEST", date(2026, 9, 10), NOW.date(), "amc")
    else:
        monkeypatch.setattr(enrich, "get_settings", lambda: SimpleNamespace(
            marketdata_token="isolated-test-token", marketdata_base_url="https://marketdata.example",
        ))
        calls = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        class Client:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def get(self, url, **_kwargs):
                calls.append(url)
                if "/expirations/" in url:
                    return Response({"expirations": ["2026-09-11"]})
                quote_epoch = datetime.fromisoformat(QUOTE_TIME).timestamp()
                return Response({
                    "side": ["call", "put"], "strike": [100, 100],
                    "bid": [3.8, 3.4], "ask": [4.2, 3.6],
                    "underlyingPrice": [100, 100], "updated": [quote_epoch, quote_epoch],
                })

        monkeypatch.setattr(enrich.httpx, "Client", Client)
        result = enrich._marketdata_expected_move("TEST", date(2026, 9, 10), NOW.date(), "amc")
        assert len(calls) == 2

    assert result["expected_move_status"] == "active"
    assert result["expected_move_pct"] == 7.5
    assert datetime.fromisoformat(result["expected_move_observed_at"]) == datetime.fromisoformat(QUOTE_TIME)
