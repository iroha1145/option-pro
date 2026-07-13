from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from .config import CatalystSettings
from .errors import CatalystError, CatalystRepositoryError
from .repository import CatalystRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CatalystService:
    """Same-origin API facade.

    Every method in this class is local-only.  Remote access is intentionally
    absent so an ordinary API request can never wait for MacroLens.
    """

    def __init__(self, settings: CatalystSettings) -> None:
        self.settings = settings

    @property
    def available_by_config(self) -> bool:
        return self.settings.enabled and self.settings.catalyst_mode != "disabled"

    def _repository(self, *, writer: bool = False) -> CatalystRepository:
        if not self.available_by_config:
            raise CatalystError(
                "capability_disabled",
                "Catalyst integration is disabled",
                retryable=False,
                counts_for_circuit=False,
            )
        if not self.settings.cache_db_path.is_file():
            raise CatalystRepositoryError(
                "cache_unavailable", "Catalyst cache has not completed its first sync"
            )
        return CatalystRepository(self.settings.cache_db_path, read_only=not writer)

    def status(self, *, now: Optional[datetime] = None) -> dict[str, Any]:
        observed = now or _utc_now()
        if not self.available_by_config:
            return {
                "enabled": False,
                "status": "disabled",
                "as_of": observed.isoformat().replace("+00:00", "Z"),
                "data_through": None,
                "last_sync_at": None,
                "remote_status": None,
                "analysis_trigger_enabled": False,
                "model": None,
                "reasoning": None,
                "execution_mode": None,
                "expected_model": self.settings.model,
                "expected_reasoning": self.settings.reasoning,
                "schema_version": self.settings.schema_version,
                "sources": [],
                "warnings": [],
            }
        try:
            return self._repository().status_snapshot(
                stale_ttl_seconds=self.settings.stale_ttl_seconds,
                feed_interval_seconds=self.settings.feed_interval_seconds,
                action_enabled=self.settings.action_enabled,
                model=self.settings.model,
                reasoning=self.settings.reasoning,
                schema_version=self.settings.schema_version,
                now=observed,
            )
        except CatalystRepositoryError as error:
            return {
                "enabled": True,
                "status": "unavailable",
                "as_of": observed.isoformat().replace("+00:00", "Z"),
                "data_through": None,
                "last_sync_at": None,
                "remote_status": None,
                "analysis_trigger_enabled": False,
                "model": None,
                "reasoning": None,
                "execution_mode": None,
                "expected_model": self.settings.model,
                "expected_reasoning": self.settings.reasoning,
                "schema_version": self.settings.schema_version,
                "sources": [],
                "warnings": [error.code],
            }

    def feed(self, **kwargs: Any) -> dict[str, Any]:
        status = self.status(now=kwargs.get("as_of"))
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "status": status["status"],
                "as_of": status["as_of"],
                "data_through": status.get("data_through"),
                "items": [],
                "summary": {
                    "news_6h": None,
                    "analyzed_24h": None,
                    "bullish": None,
                    "bearish": None,
                    "pending": None,
                    "high_impact_macro": None,
                },
                "stock_impacts": [],
                "next_cursor": None,
                "has_more": False,
                "warnings": status.get("warnings", []),
            }
        result = self._repository().list_feed(**kwargs)
        result["status"] = status["status"] if result["items"] else (
            status["status"] if status["status"] in {"stale", "degraded"} else "empty"
        )
        result["warnings"] = status.get("warnings", [])
        return result

    def news(self, news_id: int, *, as_of: datetime) -> Optional[dict[str, Any]]:
        repository = self._repository()
        item = repository.get_news(news_id, as_of=as_of)
        if item is None:
            return None
        status = self.status(now=as_of)
        return {
            "status": status["status"],
            "as_of": as_of.isoformat().replace("+00:00", "Z"),
            "item": item,
            "analysis_job": repository.latest_job_for_news(
                news_id,
                content_hash=item["content_hash"],
                change_sequence=item["change_sequence"],
                contract_schema_version=self.settings.schema_version,
                model=self.settings.model,
                reasoning=self.settings.reasoning,
                as_of=as_of,
            ),
            "analysis_trigger_enabled": status["analysis_trigger_enabled"],
        }

    def ticker(self, ticker: str, **kwargs: Any) -> dict[str, Any]:
        status = self.status(now=kwargs.get("as_of"))
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "ticker": ticker.strip().upper(),
                "status": status["status"],
                "as_of": status["as_of"],
                "data_through": status.get("data_through"),
                "items": [],
                "next_cursor": None,
                "has_more": False,
                "warnings": status.get("warnings", []),
            }
        result = self._repository().ticker_feed(ticker, **kwargs)
        if status["status"] in {"stale", "degraded"}:
            result["status"] = status["status"]
        result["warnings"] = status.get("warnings", [])
        return result

    def batch(self, tickers: Sequence[str], **kwargs: Any) -> dict[str, Any]:
        status = self.status(now=kwargs.get("as_of"))
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "as_of": status["as_of"],
                "status": status["status"],
                "results": {
                    ticker: {
                        "ticker": ticker,
                        "status": status["status"],
                        "items": [],
                        "next_cursor": None,
                        "has_more": False,
                    }
                    for ticker in tickers
                },
                "warnings": status.get("warnings", []),
            }
        result = self._repository().batch_tickers(tickers, **kwargs)
        result["status"] = status["status"]
        if status["status"] in {"stale", "degraded"}:
            for item in result["results"].values():
                item["status"] = status["status"]
        result["warnings"] = status.get("warnings", [])
        return result

    def calendar(
        self,
        *,
        date_from: date,
        date_to: date,
        as_of: datetime,
        currencies: Optional[Sequence[str]],
        min_impact: Optional[str],
    ) -> dict[str, Any]:
        status = self.status(now=as_of)
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "status": status["status"],
                "as_of": status["as_of"],
                "data_through": None,
                "items": [],
                "warnings": status.get("warnings", []),
            }
        result = self._repository().list_calendar(
            date_from=datetime.combine(date_from, time.min, tzinfo=timezone.utc),
            date_to=datetime.combine(date_to, time.max, tzinfo=timezone.utc),
            as_of=as_of,
            currencies=currencies,
            min_impact=min_impact,
        )
        result["status"] = status["status"] if result["items"] else (
            status["status"] if status["status"] in {"stale", "degraded"} else "empty"
        )
        result["warnings"] = status.get("warnings", [])
        return result

    def request_refresh(self) -> dict[str, Any]:
        request_id = self._repository(writer=True).enqueue_refresh()
        return {"request_id": request_id, "status": "queued"}

    def request_analysis(self, news_id: int, *, force: bool) -> dict[str, Any]:
        if not self.settings.action_enabled:
            raise CatalystError(
                "capability_disabled",
                "News analysis requests are not enabled",
                retryable=False,
                counts_for_circuit=False,
            )
        runtime_status = self.status()
        if not runtime_status.get("analysis_trigger_enabled"):
            raise CatalystError(
                "capability_degraded",
                "MacroLens analysis runtime does not match the required capability",
                retryable=False,
                counts_for_circuit=False,
            )
        repository = self._repository(writer=True)
        item = repository.get_news(news_id, as_of=_utc_now())
        if item is None:
            raise CatalystError(
                "news_not_found",
                "The requested news item is not in the local cache",
                retryable=False,
                counts_for_circuit=False,
            )
        return repository.enqueue_analysis(
            news_id,
            content_hash=item["content_hash"],
            change_sequence=item["change_sequence"],
            contract_schema_version=self.settings.schema_version,
            force=force,
            model=self.settings.model,
            reasoning=self.settings.reasoning,
        )

    def analysis_job(self, local_job_id: str) -> Optional[dict[str, Any]]:
        return self._repository().get_analysis_job(local_job_id)

    def cancel_analysis_job(self, local_job_id: str) -> Optional[dict[str, Any]]:
        if not self.settings.action_enabled:
            raise CatalystError(
                "capability_disabled",
                "News analysis actions are not enabled",
                retryable=False,
                counts_for_circuit=False,
            )
        return self._repository(writer=True).request_job_cancel(local_job_id)

    def _market_focus_snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        if not self.available_by_config:
            return {
                "status": "disabled",
                "as_of": (now or _utc_now()).isoformat().replace("+00:00", "Z"),
                "hotspot_status": None,
                "items": [],
                "cycle": None,
                "warnings": ["capability_disabled"],
            }
        try:
            return self._repository().market_focus_snapshot(
                stale_ttl_seconds=self.settings.stale_ttl_seconds,
                now=now,
            )
        except CatalystRepositoryError as error:
            return {
                "status": "unavailable",
                "as_of": (now or _utc_now()).isoformat().replace("+00:00", "Z"),
                "hotspot_status": None,
                "items": [],
                "cycle": None,
                "warnings": [error.code],
            }

    def hotspot_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        snapshot = self._market_focus_snapshot(now=now)
        payload = dict(snapshot.get("hotspot_status") or {})
        payload.update(
            {
                "status": snapshot["status"],
                "as_of": snapshot["as_of"],
                "last_sync_at": snapshot.get("last_sync_at"),
                "action_enabled": self.settings.action_enabled,
                "warnings": snapshot.get("warnings", []),
            }
        )
        return payload

    def hotspots(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        snapshot = self._market_focus_snapshot(now=now)
        return {
            "status": snapshot["status"],
            "as_of": snapshot["as_of"],
            "data_through": snapshot.get("data_through"),
            "items": snapshot.get("items", [])[:limit],
            "warnings": snapshot.get("warnings", []),
        }

    def latest_market_focus_cycle(
        self, *, now: datetime | None = None
    ) -> dict[str, Any]:
        snapshot = self._market_focus_snapshot(now=now)
        return {
            "status": snapshot["status"],
            "as_of": snapshot["as_of"],
            "data_through": snapshot.get("data_through"),
            "cycle": snapshot.get("cycle"),
            "warnings": snapshot.get("warnings", []),
        }

    def request_market_focus_cycle(
        self,
        *,
        expected_prepared_revision: int | None,
        retry_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.action_enabled:
            raise CatalystError(
                "capability_disabled",
                "Market focus analysis actions are not enabled",
                retryable=False,
                counts_for_circuit=False,
            )
        repository = self._repository(writer=True)
        if retry_cycle_id is not None:
            if expected_prepared_revision is not None:
                raise CatalystError(
                    "invalid_market_focus_request",
                    "A retry cannot also request a new prepared revision",
                    retryable=False,
                    counts_for_circuit=False,
                )
            return repository.enqueue_market_focus_retry(retry_cycle_id)
        if expected_prepared_revision is None:
            raise CatalystError(
                "invalid_market_focus_request",
                "A prepared revision is required for a new market focus cycle",
                retryable=False,
                counts_for_circuit=False,
            )
        status = self.hotspot_status()
        last_consumed_revision = int(status.get("last_consumed_revision") or 0)
        existing = repository.market_focus_job_for_batch(
            expected_prepared_revision,
            last_consumed_revision,
        )
        if existing is not None:
            return existing
        if status.get("status") in {"disabled", "unavailable", "stale"}:
            raise CatalystError(
                "capability_degraded",
                "Market focus snapshot is not current",
                retryable=True,
                counts_for_circuit=False,
            )
        if int(status.get("prepared_revision") or -1) != expected_prepared_revision:
            raise CatalystError(
                "prepared_revision_changed",
                "The prepared hotspot revision changed before the request was queued",
                retryable=False,
                counts_for_circuit=False,
            )
        if not status.get("manual_enabled"):
            code = str(status.get("capability") or "no_new_hot_events")
            if code == "enabled":
                code = "no_new_hot_events"
            raise CatalystError(
                code,
                "Market focus analysis is not available for this revision",
                retryable=False,
                counts_for_circuit=False,
            )
        return repository.enqueue_market_focus_cycle(
            expected_prepared_revision,
            last_consumed_revision=last_consumed_revision,
            model=str(status["model"]),
            reasoning=str(status["reasoning"]),
        )

    def market_focus_cycle(self, local_cycle_id: str) -> dict[str, Any] | None:
        return self._repository().get_market_focus_cycle(local_cycle_id)

    def cancel_market_focus_cycle(
        self, local_cycle_id: str
    ) -> dict[str, Any] | None:
        if not self.settings.action_enabled:
            raise CatalystError(
                "capability_disabled",
                "Market focus analysis actions are not enabled",
                retryable=False,
                counts_for_circuit=False,
            )
        return self._repository(writer=True).request_market_focus_cancel(
            local_cycle_id
        )
