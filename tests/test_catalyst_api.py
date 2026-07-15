from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import catalysts as catalyst_api
from app.services.catalysts.errors import InvalidCursorError


NOW = "2026-07-15T04:00:00Z"
CYCLE_ID = "mfc_" + "a" * 32
JOB_ID = "aij_" + "b" * 32
REFRESH_ID = "refresh_" + "d" * 32


class StubPersonalService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.invalid_cursor = False

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "status": "ok",
            "as_of": NOW,
            "data_through": NOW,
            "last_sync_at": NOW,
            "model": "gpt-5.6-terra",
            "reasoning": "max",
            "analysis_trigger_enabled": True,
            "snapshot_id": "private-snapshot",
            "sources": [
                {
                    "source": "wire",
                    "status": "ok",
                    "last_success_at": NOW,
                    "data_through": NOW,
                    "consecutive_failures": 0,
                    "raw_count": 1,
                    "inserted_count": 1,
                    "duplicates_count": 0,
                    "detail": "private-detail",
                }
            ],
            "streams": {},
            "warnings": [],
        }

    def feed(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("feed", kwargs))
        if self.invalid_cursor:
            raise InvalidCursorError()
        return {
            "status": "ok",
            "as_of": NOW,
            "items": [
                {
                    "news_id": 101,
                    "title_zh": "芯片企业发布最新业绩",
                    "summary_zh": "收入增长，但需求仍有波动。",
                }
            ],
        }

    def news(self, news_id: int, *, as_of: Any) -> dict[str, Any] | None:
        self.calls.append(("news", news_id, as_of))
        if news_id != 101:
            return None
        return {
            "status": "ok",
            "as_of": NOW,
            "item": {"news_id": news_id, "title_zh": "中文标题"},
            "analysis_job": {"job_id": JOB_ID, "status": "pending"},
            "analysis_trigger_enabled": True,
        }

    def ticker(self, ticker: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("ticker", ticker, kwargs))
        return {"status": "ok", "ticker": ticker, "items": []}

    def batch(self, tickers: list[str], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("batch", tuple(tickers), kwargs))
        return {
            "status": "ok",
            "results": {
                ticker: {"status": "empty", "ticker": ticker, "items": []}
                for ticker in tickers
            },
        }

    def calendar(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("calendar", kwargs))
        return {"status": "ok", "items": []}

    def hotspot_status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "prepared_revision": 3,
            "manual_enabled": True,
            "private_state": "hidden",
        }

    def hotspots(self, *, limit: int) -> dict[str, Any]:
        return {
            "status": "ok",
            "as_of": NOW,
            "items": [
                {
                    "event_group_id": "event-1",
                    "representative_title": "中文热点标题",
                    "private_state": "hidden",
                }
            ][:limit],
        }

    def latest_market_focus_cycle(self) -> dict[str, Any]:
        return {"status": "ok", "as_of": NOW, "cycle": self._cycle()}

    def request_market_focus_cycle(
        self,
        *,
        expected_prepared_revision: int | None,
        retry_cycle_id: str | None,
        force: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            ("request_focus", expected_prepared_revision, retry_cycle_id, force)
        )
        return self._cycle(status="pending")

    def market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        return self._cycle() if cycle_id == CYCLE_ID else None

    def cancel_market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        self.calls.append(("cancel_focus", cycle_id))
        return self._cycle(status="cancelled") if cycle_id == CYCLE_ID else None

    def request_refresh(
        self,
        operation_type: str,
        *,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self.calls.append(("refresh", operation_type, idempotency_key))
        return {"request_id": REFRESH_ID, "status": "queued"}

    def manual_operation(self, request_id: str) -> dict[str, Any] | None:
        return (
            {"request_id": REFRESH_ID, "operation_type": "news", "status": "queued"}
            if request_id == REFRESH_ID
            else None
        )

    def request_analysis(self, news_id: int, *, force: bool) -> dict[str, Any]:
        self.calls.append(("analysis", news_id, force))
        return {"job_id": JOB_ID, "status": "pending"}

    def analysis_job(self, job_id: str) -> dict[str, Any] | None:
        return {"job_id": JOB_ID, "status": "pending"} if job_id == JOB_ID else None

    def cancel_analysis_job(self, job_id: str) -> dict[str, Any] | None:
        self.calls.append(("cancel_analysis", job_id))
        return {"job_id": JOB_ID, "status": "cancelled"} if job_id == JOB_ID else None

    @staticmethod
    def _cycle(*, status: str = "completed") -> dict[str, Any]:
        return {
            "cycle_id": CYCLE_ID,
            "status": status,
            "created_at": NOW,
            "updated_at": NOW,
            "private_state": "hidden",
        }


def client_for(service: StubPersonalService) -> TestClient:
    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: service
    return TestClient(app, base_url="http://localhost")


def test_read_routes_use_only_the_personal_service() -> None:
    service = StubPersonalService()
    client = client_for(service)

    responses = (
        client.get("/api/catalysts/status"),
        client.get("/api/catalysts/feed"),
        client.get("/api/catalysts/news/101"),
        client.get("/api/catalysts/tickers/nvda"),
        client.post("/api/catalysts/tickers/batch", json={"tickers": ["nvda", "NVDA", "AMD"]}),
        client.get("/api/catalysts/calendar"),
        client.get("/api/catalysts/hotspots/status"),
        client.get("/api/catalysts/hotspots"),
        client.get("/api/catalysts/market-focus-cycles/latest"),
        client.get(f"/api/catalysts/market-focus-cycles/{CYCLE_ID}"),
        client.get(f"/api/catalysts/analysis-jobs/{JOB_ID}"),
        client.get(f"/api/catalysts/refresh/{REFRESH_ID}"),
    )

    assert all(response.status_code == 200 for response in responses)
    assert responses[1].json()["items"][0]["title_zh"] == "芯片企业发布最新业绩"
    assert responses[3].json()["ticker"] == "NVDA"
    assert list(responses[4].json()["results"]) == ["NVDA", "AMD"]
    assert any(call[0] == "feed" for call in service.calls)


@pytest.mark.parametrize("chunked", [False, True])
def test_catalyst_routes_reject_oversized_body_before_json_parsing(
    chunked: bool,
) -> None:
    client = client_for(StubPersonalService())
    if chunked:
        half = catalyst_api._MAX_CATALYST_BODY_BYTES // 2

        def content():
            yield b"x" * half
            yield b"x" * (half + 1)
        body = content()
    else:
        body = b"x" * (catalyst_api._MAX_CATALYST_BODY_BYTES + 1)

    response = client.post(
        "/api/catalysts/tickers/batch",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Catalyst request body exceeds 32 KiB"


def test_invalid_cursor_is_a_safe_client_error() -> None:
    service = StubPersonalService()
    service.invalid_cursor = True

    response = client_for(service).get("/api/catalysts/feed?cursor=bad")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_cursor"


def test_actions_delegate_to_personal_service_after_authentication() -> None:
    service = StubPersonalService()
    client = client_for(service)

    responses = (
        client.post(
            "/api/catalysts/refresh",
            json={"operation_type": "news", "idempotency_key": "refresh-news"},
        ),
        client.post("/api/catalysts/news/101/analysis", json={"force": True}),
        client.post(f"/api/catalysts/analysis-jobs/{JOB_ID}/cancel"),
        client.post(
            "/api/catalysts/market-focus-cycles",
            json={"expected_prepared_revision": 3, "force": True},
        ),
        client.post(f"/api/catalysts/market-focus-cycles/{CYCLE_ID}/cancel"),
    )

    assert all(response.status_code == 202 for response in responses)
    assert ("refresh", "news", "refresh-news") in service.calls
    assert ("analysis", 101, True) in service.calls
    assert ("request_focus", 3, None, True) in service.calls


def test_route_validation_and_missing_local_rows_fail_safely() -> None:
    client = client_for(StubPersonalService())

    invalid_ticker = client.get("/api/catalysts/tickers/AAPL%2FBAD")
    invalid_calendar = client.get(
        "/api/catalysts/calendar",
        params={"date_from": date(2026, 7, 15), "date_to": date(2027, 1, 1)},
    )
    missing_news = client.get("/api/catalysts/news/999")
    missing_job = client.get("/api/catalysts/analysis-jobs/aij_" + "c" * 32)

    assert invalid_ticker.status_code == 404
    assert invalid_calendar.status_code == 422
    assert missing_news.status_code == 404
    assert missing_job.status_code == 404
