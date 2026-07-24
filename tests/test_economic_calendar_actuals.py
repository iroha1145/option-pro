from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import catalysts as catalyst_api
from app.services.catalysts import economic_calendar_actuals as actuals
from app.services.catalysts.economic_calendar_actuals import (
    enrich_recent_actuals,
    merge_recent_actuals,
)


AS_OF = datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc)


def test_recent_actuals_are_matched_by_release_identity_without_overwriting_source_data():
    payload = {
        "status": "active",
        "items": [
            {
                "event_id": "claims",
                "currency": "USD",
                "title": "初请失业金人数",
                "scheduled_at_utc": "2026-07-23T12:30:00Z",
                "forecast": "211K",
                "previous": "208K",
                "actual": None,
                "release_status": "awaiting_source",
            },
            {
                "event_id": "retail",
                "currency": "GBP",
                "title": "零售销售",
                "scheduled_at_utc": "2026-07-24T06:00:00Z",
                "forecast": "-0.3%",
                "previous": "1.2%",
                "actual": None,
                "release_status": "awaiting_source",
            },
        ],
    }
    source_rows = [
        {
            "title": "Initial Jobless Claims",
            "indicator": "Initial Jobless Claims",
            "currency": "USD",
            "date": "2026-07-23T12:30:00.000Z",
            "actual": 205,
            "forecast": 211,
            "previous": 208,
            "scale": "K",
        },
        {
            "title": "Retail Sales MoM",
            "indicator": "Retail Sales MoM",
            "currency": "GBP",
            "date": "2026-07-24T06:00:00.000Z",
            "actual": 0.4,
            "forecast": -0.3,
            "previous": 1.2,
            "unit": "%",
        },
    ]

    merged, filled, attempted = merge_recent_actuals(
        payload,
        source_rows,
        as_of=AS_OF,
    )

    assert attempted == 2
    assert filled == 2
    assert merged["items"][0]["actual"] == "205K"
    assert merged["items"][1]["actual"] == "0.4%"
    assert merged["items"][0]["release_status"] == "released"
    assert merged["items"][0]["actual_source"] == "TradingView Economic Calendar"
    assert payload["items"][0]["actual"] is None


def test_ambiguous_or_future_candidates_are_not_used():
    payload = {
        "items": [
            {
                "event_id": "ambiguous",
                "currency": "EUR",
                "title": "制造业PMI初值",
                "scheduled_at_utc": "2026-07-24T07:30:00Z",
                "forecast": "50.4",
                "previous": "50.0",
                "actual": None,
                "release_status": "scheduled",
            }
        ]
    }
    duplicate = {
        "title": "German Flash Manufacturing PMI",
        "indicator": "Manufacturing PMI",
        "currency": "EUR",
        "date": "2026-07-24T07:30:00.000Z",
        "actual": 51.2,
        "forecast": 50.4,
        "previous": 50.0,
    }

    future, filled, attempted = merge_recent_actuals(
        payload,
        [duplicate],
        as_of=datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc),
    )
    assert attempted == 0
    assert filled == 0
    assert future["items"][0]["actual"] is None

    ambiguous, filled, attempted = merge_recent_actuals(
        payload,
        [duplicate, {**duplicate, "id": "duplicate"}],
        as_of=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
    )
    assert attempted == 1
    assert filled == 0
    assert ambiguous["items"][0]["actual"] is None


def test_existing_zero_actual_is_preserved_and_old_events_are_not_enriched():
    payload = {
        "items": [
            {
                "event_id": "zero",
                "currency": "USD",
                "title": "零售销售",
                "scheduled_at_utc": "2026-07-23T12:30:00Z",
                "actual": 0,
            },
            {
                "event_id": "old",
                "currency": "USD",
                "title": "零售销售",
                "scheduled_at_utc": "2026-07-20T12:30:00Z",
                "actual": None,
            },
        ]
    }
    source_rows = [
        {
            "title": "Retail Sales",
            "currency": "USD",
            "date": "2026-07-23T12:30:00Z",
            "actual": 1.2,
        },
        {
            "title": "Retail Sales",
            "currency": "USD",
            "date": "2026-07-20T12:30:00Z",
            "actual": 2.5,
        },
    ]

    merged, filled, attempted = merge_recent_actuals(
        payload,
        source_rows,
        as_of=AS_OF,
    )

    assert filled == 0
    assert attempted == 0
    assert merged["items"][0]["actual"] == 0
    assert merged["items"][1]["actual"] is None


def test_forecast_only_match_and_non_numeric_actual_are_rejected():
    payload = {
        "items": [
            {
                "event_id": "weak-match",
                "currency": "USD",
                "title": "消费者信心指数",
                "scheduled_at_utc": "2026-07-23T14:00:00Z",
                "forecast": "100",
                "previous": None,
                "actual": None,
            }
        ]
    }

    weak_match = {
        "title": "Unrelated Release",
        "currency": "USD",
        "date": "2026-07-23T14:00:00Z",
        "forecast": 100,
        "actual": 12,
    }
    merged, filled, attempted = merge_recent_actuals(
        payload,
        [weak_match],
        as_of=AS_OF,
    )
    assert attempted == 1
    assert filled == 0
    assert merged["items"][0]["actual"] is None

    invalid_actual = {
        "title": "Consumer Confidence",
        "currency": "USD",
        "date": "2026-07-23T14:00:00Z",
        "forecast": 100,
        "actual": "N/A",
    }
    merged, filled, attempted = merge_recent_actuals(
        payload,
        [invalid_actual],
        as_of=AS_OF,
    )
    assert attempted == 1
    assert filled == 0
    assert merged["items"][0]["actual"] is None


@pytest.mark.anyio
async def test_historical_window_does_not_fetch_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_fetch(*args, **kwargs):
        raise AssertionError("historical calendar must not use the live fallback")

    monkeypatch.setattr(actuals, "_fetch_source_rows", fail_fetch)
    payload = {
        "items": [
            {
                "event_id": "historical",
                "currency": "USD",
                "title": "零售销售",
                "scheduled_at_utc": "2026-06-01T12:30:00Z",
                "actual": None,
            }
        ]
    }

    enriched = await enrich_recent_actuals(
        payload,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 2),
        as_of=AS_OF,
    )

    assert enriched == payload


@pytest.mark.anyio
async def test_network_failure_returns_original_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_fetch(*args, **kwargs):
        request = httpx.Request("GET", "https://example.invalid/calendar")
        raise httpx.ReadTimeout("timeout", request=request)

    monkeypatch.setattr(actuals, "_fetch_source_rows", fail_fetch)
    payload = {
        "status": "active",
        "items": [
            {
                "event_id": "recent",
                "currency": "USD",
                "title": "零售销售",
                "scheduled_at_utc": "2026-07-23T12:30:00Z",
                "actual": None,
            }
        ],
    }

    enriched = await enrich_recent_actuals(
        payload,
        date_from=date(2026, 7, 21),
        date_to=date(2026, 7, 30),
        as_of=AS_OF,
    )

    assert enriched["items"] == payload["items"]
    assert enriched["actual_fallback"] == {
        "provider": "TradingView Economic Calendar",
        "status": "unavailable",
        "attempted": 1,
        "filled": 0,
    }


def test_explicit_as_of_route_never_calls_live_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CalendarService:
        def calendar(self, **kwargs):
            return {
                "status": "active",
                "as_of": kwargs["as_of"].isoformat(),
                "items": [],
            }

    async def fail_enrichment(*args, **kwargs):
        raise AssertionError("explicit as_of must not call the live fallback")

    monkeypatch.setattr(catalyst_api, "enrich_recent_actuals", fail_enrichment)
    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = CalendarService

    response = TestClient(app).get(
        "/api/catalysts/calendar",
        params={"as_of": "2026-07-22T04:00:00Z"},
    )

    assert response.status_code == 200
    assert response.json()["as_of"] == "2026-07-22T04:00:00+00:00"
