from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from app.access import current_request_is_owner
from app.config import Settings, get_settings
from app.data_paths import get_data_paths
from app.personal_config import PersonalConfig, get_personal_config
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.models import (
    validate_result,
    validate_simplified_chinese_text,
)
from app.services.ai_jobs.repository import AIJobRepository
from app.services.runtime_settings import (
    RuntimeSettingsStorageError,
    get_effective_runtime_settings,
)
from app.worker.state import WorkerStateRepository
from app.services.sectors import SECTORS

from .config import CatalystSettings
from .errors import CatalystError


_WAITING_TITLE = "中文标题等待生成"
_WAITING_SUMMARY = "中文摘要等待生成"
_WAITING_HOTSPOT_TITLE = "热点标题等待中文分析"
_INTERACTIVE_MODES = frozenset({"manual", "scheduled"})
_NEWS_PROMPT_VERSION = "news-impact-zh-cn-v6"
_LOCAL_STORE_RUNTIME_CODES = frozenset(
    {
        "ai_job_insert_failed",
        "ai_job_created_at_invalid",
        "ai_job_payload_invalid",
        "ai_job_batch_schema_checksum_mismatch",
        "ai_job_schema_checksum_mismatch",
        "local_catalyst_schema_checksum_mismatch",
    }
)
_REQUIRED_LOCAL_TABLES = frozenset(
    {
        "macrolens_etl_state",
        "catalyst_local_schema",
        "catalyst_local_news_revisions",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_tickers() -> frozenset[str]:
    return frozenset(
        str(ticker).strip().upper()
        for sector in SECTORS.values()
        for ticker in sector.get("tickers", [])
        if str(ticker).strip()
    )


def _valid_zh_text(
    value: Any,
    *,
    allowed_codes: Sequence[str] = (),
    source_texts: Sequence[str] = (),
) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return validate_simplified_chinese_text(
            value,
            None,
            allowed_codes=allowed_codes,
            source_texts=source_texts,
        )
    except ValueError:
        return None


class _LocalIntelligence(Protocol):
    def initialize(self) -> None: ...
    def status(
        self,
        *,
        now: datetime | None = None,
        include_manual_refreshes: bool | None = None,
    ) -> dict[str, Any]: ...
    def feed(self, **kwargs: Any) -> dict[str, Any]: ...
    def news(self, news_id: int, *, as_of: datetime) -> dict[str, Any] | None: ...
    def ticker(self, ticker: str, **kwargs: Any) -> dict[str, Any]: ...
    def batch(self, tickers: Sequence[str], **kwargs: Any) -> dict[str, Any]: ...
    def calendar(self, **kwargs: Any) -> dict[str, Any]: ...
    def hotspot_status(self, *, now: datetime | None = None) -> dict[str, Any]: ...
    def hotspots(self, *, limit: int, now: datetime | None = None) -> dict[str, Any]: ...
    def latest_market_focus_cycle(
        self, *, now: datetime | None = None
    ) -> dict[str, Any]: ...
    def request_market_focus_cycle(
        self,
        *,
        expected_prepared_revision: int | None,
        retry_cycle_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]: ...
    def market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None: ...
    def cancel_market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None: ...
    def request_refresh(
        self,
        operation_type: Literal["news", "calendar", "source_health"] = "news",
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...
    def manual_operation(self, request_id: str) -> dict[str, Any] | None: ...
    def request_analysis(self, news_id: int, *, force: bool) -> dict[str, Any]: ...
    def analysis_job(self, job_id: str) -> dict[str, Any] | None: ...
    def cancel_analysis_job(self, job_id: str) -> dict[str, Any] | None: ...
    def news_result_audit_states(
        self,
        jobs: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, str]: ...


class PersonalCatalystService:
    """Local-only API facade for the personal Catalyst runtime.

    The service never contacts MacroLens or OpenAI.  Reads are delegated to the
    local ETL/intelligence database and writes only append persistent local jobs
    after an explicit authenticated POST reaches this facade.
    """

    def __init__(
        self,
        settings: CatalystSettings,
        *,
        intelligence: _LocalIntelligence | None = None,
        ai_repository: AIJobRepository | None = None,
        personal_config: PersonalConfig | None = None,
        ai_settings: Settings | None = None,
    ) -> None:
        self.settings = settings
        self.personal_config = personal_config or get_personal_config()
        self.mode: Literal["off", "read", "manual", "scheduled"] = (
            self.personal_config.features.catalyst_mode
        )
        resolved_ai_settings = ai_settings or get_settings()
        self.ai_settings = resolved_ai_settings
        try:
            effective_runtime = get_effective_runtime_settings()
        except RuntimeSettingsStorageError:
            effective_runtime = None
        self._ai_repository_injected = ai_repository is not None
        self.ai_repository = ai_repository or AIJobRepository(
            resolved_ai_settings.openai_job_db_path
        )
        self._intelligence_injected = intelligence is not None
        if intelligence is None:
            # Keep web-process imports side-effect free until a local read is made.
            from .local_intelligence import LocalCatalystIntelligence

            intelligence = LocalCatalystIntelligence(
                settings.cache_db_path,
                self.ai_repository,
                mode="read" if self.mode == "off" else self.mode,
                canonical_tickers=_canonical_tickers(),
                model=resolved_ai_settings.openai_model,
                reasoning=resolved_ai_settings.openai_reasoning,
                max_queued=resolved_ai_settings.openai_job_max_queued,
                manual_refresh_cooldown_seconds=(
                    effective_runtime.catalyst.manual_refresh_cooldown_seconds
                    if effective_runtime is not None
                    else self.personal_config.catalyst.manual_refresh_cooldown_seconds
                ),
            )
        self.intelligence = intelligence

    def _effective_runtime(self) -> Any | None:
        try:
            return get_effective_runtime_settings()
        except RuntimeSettingsStorageError:
            return None

    def _ai_configured(self) -> bool:
        secret = getattr(self.ai_settings, "openai_api_key", None)
        if secret is None:
            return bool(self._ai_repository_injected or self._intelligence_injected)
        if hasattr(secret, "get_secret_value"):
            secret = secret.get_secret_value()
        return bool(str(secret or "").strip())

    def _worker_healthy(self) -> bool:
        if self._ai_repository_injected or self._intelligence_injected:
            return True
        path = Path(
            getattr(
                self.ai_settings,
                "optix_worker_db_path",
                Path("/data/optix-worker.db"),
            )
        )
        try:
            health = WorkerStateRepository(path).health()
            return bool(health.get("healthy"))
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False

    def _personal_etl_enabled(self) -> bool:
        return bool(
            getattr(
                self.ai_settings,
                "personal_etl_enabled",
                False,
            )
        )

    def analysis_availability(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = now or _utc_now()
        runtime_settings = self._effective_runtime()
        daily_limit = int(
            runtime_settings.ai.daily_max_jobs
            if runtime_settings is not None
            else self.personal_config.ai.daily_max_jobs
        )
        daily_budget = float(
            runtime_settings.ai.daily_budget_usd
            if runtime_settings is not None
            else self.personal_config.ai.daily_budget_usd
        )
        daily_token_limit = int(
            getattr(runtime_settings.ai, "daily_token_limit", 10_000_000)
            if runtime_settings is not None
            else self.personal_config.ai.daily_token_limit
        )
        cooldown_seconds = int(
            runtime_settings.ai.manual_analysis_cooldown_seconds
            if runtime_settings is not None
            else self.personal_config.catalyst.manual_refresh_cooldown_seconds
        )
        capacity = {
            "daily_max_jobs": daily_limit,
            "daily_budget_usd": daily_budget,
            "daily_token_limit": daily_token_limit,
            "submitted_jobs": 0,
            "budget_used_usd": 0.0,
            "budget_remaining_usd": daily_budget,
            "usage_total_tokens": 0,
            "token_budget_used_tokens": 0,
            "token_budget_remaining_tokens": daily_token_limit,
            "token_budget_available": True,
            "budget_available": True,
            "job_limit_available": True,
            "dollar_budget_available": True,
            "concurrency_available": True,
            "active_job": None,
            "cooldown_until": None,
            "cooldown_complete": True,
        }
        repository_has_path = hasattr(self.ai_repository, "path")
        if hasattr(self.ai_repository, "budget_snapshot") and (
            not repository_has_path or self._ai_store_ready()
        ):
            try:
                capacity.update(
                    self.ai_repository.budget_snapshot(
                        daily_limit=daily_limit,
                        daily_budget_usd=daily_budget,
                        daily_token_limit=daily_token_limit,
                        cooldown_seconds=cooldown_seconds,
                        unknown_submission_hold_seconds=int(
                            getattr(
                                self.ai_settings,
                                "openai_job_max_age_seconds",
                                86400,
                            )
                        ),
                        now=observed,
                    )
                )
            except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError):
                capacity["concurrency_available"] = False
        mode_enabled = self.mode in _INTERACTIVE_MODES
        manual_enabled = bool(
            mode_enabled
            and runtime_settings is not None
            and runtime_settings.ai.manual_analysis_enabled
        )
        configured = self._ai_configured()
        worker_healthy = self._worker_healthy()
        reason = "available"
        if runtime_settings is None:
            reason = "settings_unavailable"
        elif not mode_enabled:
            reason = "read_only_mode"
        elif not manual_enabled:
            reason = "manual_analysis_disabled"
        elif not configured:
            reason = "not_configured"
        elif not worker_healthy:
            reason = "worker_unavailable"
        elif not capacity["token_budget_available"]:
            reason = "daily_token_limit"
        elif not capacity["concurrency_available"]:
            reason = "analysis_in_progress"
        elif not capacity["cooldown_complete"]:
            reason = "cooldown_active"
        enabled = reason == "available"
        return {
            "enabled": enabled,
            "configured": configured,
            "worker_healthy": worker_healthy,
            "budget_available": bool(capacity["budget_available"]),
            "concurrency_available": bool(capacity["concurrency_available"]),
            "cooldown_complete": bool(capacity["cooldown_complete"]),
            "reason": reason,
            **capacity,
        }

    @staticmethod
    def public_analysis_availability() -> dict[str, Any]:
        """Expose the visitor gate without owner budget or queue metadata."""

        return {
            "enabled": False,
            "reason": "owner_login_required",
        }

    def _analysis_availability_for_access(
        self,
        *,
        include_owner_state: bool,
        now: datetime,
    ) -> dict[str, Any]:
        if include_owner_state:
            return self.analysis_availability(now=now)
        return self.public_analysis_availability()

    @staticmethod
    def _resolve_owner_state(value: bool | None) -> bool:
        return current_request_is_owner() if value is None else bool(value)

    def _require_analysis_available(self) -> None:
        availability = self.analysis_availability()
        if availability["enabled"] or availability["reason"] in {
            # The durable queue performs the final atomic check. Allowing these
            # active states through preserves duplicate-request idempotency.
            # Daily and cooldown gates can be reported immediately because they
            # do not describe queue capacity.
            "analysis_in_progress",
        }:
            return
        reason = str(availability["reason"])
        retry_after: int | None = None
        if reason == "cooldown_active":
            until = _parse_utc(availability.get("cooldown_until"))
            if until is not None:
                retry_after = max(1, int((until - _utc_now()).total_seconds()) + 1)
        code = {
            "not_configured": "ai_not_configured",
            "worker_unavailable": "worker_unavailable",
            "daily_token_limit": "daily_token_limit_reached",
            "analysis_in_progress": "analysis_in_progress",
            "cooldown_active": "analysis_cooldown_active",
            "settings_unavailable": "runtime_settings_unavailable",
            "read_only_mode": "read_only_mode",
            "manual_analysis_disabled": "manual_analysis_disabled",
        }.get(reason, "analysis_unavailable")
        message = {
            "daily_token_limit_reached": "今日 1000 万 Token 额度已用完",
            "analysis_cooldown_active": "分析正在冷却中",
            "worker_unavailable": "后台工作进程暂不可用",
            "read_only_mode": "当前模式只允许读取新闻",
            "manual_analysis_disabled": "手动分析功能当前未启用",
        }.get(code, "当前无法开始新闻分析")
        raise CatalystError(
            code,
            message,
            retryable=reason in {
                "worker_unavailable",
                "analysis_in_progress",
                "cooldown_active",
            },
            retry_after_seconds=retry_after,
            counts_for_circuit=False,
        )

    def _manual_analysis_enabled(self) -> bool:
        runtime_settings = self._effective_runtime()
        return bool(
            self.mode in _INTERACTIVE_MODES
            and runtime_settings is not None
            and runtime_settings.ai.manual_analysis_enabled
        )

    def _cache_file_ready(self) -> bool:
        if self._intelligence_injected:
            return True
        try:
            path = Path(self.settings.cache_db_path)
            if not path.is_file():
                return False
            connection = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=1.0,
            )
            try:
                rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            finally:
                connection.close()
            return _REQUIRED_LOCAL_TABLES <= {str(row[0]) for row in rows}
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False

    @staticmethod
    def _cache_unavailable() -> CatalystError:
        return CatalystError(
            "cache_unavailable",
            "Catalyst local cache is not ready",
            retryable=True,
            retry_after_seconds=30,
            counts_for_circuit=False,
        )

    def _require_cache_ready(self) -> None:
        if not self._cache_file_ready():
            raise self._cache_unavailable()

    def _ai_store_ready(self) -> bool:
        if self._ai_repository_injected and not hasattr(
            self.ai_repository, "path"
        ):
            return True
        try:
            return Path(self.ai_repository.path).is_file()
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    @staticmethod
    def _is_local_store_error(error: BaseException) -> bool:
        return isinstance(error, (sqlite3.Error, OSError)) or (
            isinstance(error, RuntimeError)
            and str(error) in _LOCAL_STORE_RUNTIME_CODES
        )

    @classmethod
    def _analysis_request_error(
        cls,
        error: BaseException,
    ) -> CatalystError | None:
        if isinstance(error, RuntimeError) and str(error) == "ai_job_queue_full":
            return CatalystError(
                "ai_job_queue_full",
                "分析队列已满，请稍后重试",
                retryable=True,
                retry_after_seconds=60,
                counts_for_circuit=False,
            )
        if cls._is_local_store_error(error):
            return cls._cache_unavailable()
        return None

    def _verified_news_job_row(
        self, job_id: str
    ) -> dict[str, Any] | None:
        if not self._ai_store_ready():
            return None
        if hasattr(self.ai_repository, "path"):
            try:
                path = Path(self.ai_repository.path)
                connection = sqlite3.connect(
                    f"{path.resolve().as_uri()}?mode=ro",
                    uri=True,
                    timeout=1.0,
                )
                connection.row_factory = sqlite3.Row
                try:
                    stored = connection.execute(
                        "SELECT * FROM ai_jobs WHERE job_id=?",
                        (job_id,),
                    ).fetchone()
                finally:
                    connection.close()
                row = dict(stored) if stored is not None else None
            except (OSError, sqlite3.Error, TypeError, ValueError):
                return None
        else:
            row = self.ai_repository.get_job(job_id)
        if row is None or row.get("job_type") != "news_impact":
            return None
        schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
        if (
            row.get("model") != self.settings.model
            or row.get("reasoning") != self.settings.reasoning
            or row.get("execution_mode") != "background"
            or row.get("prompt_version") != _NEWS_PROMPT_VERSION
            or row.get("schema_version") != schema_version
            or row.get("schema_sha256") != schema_hash
        ):
            return None
        return row

    @staticmethod
    def _news_identity(
        item: Mapping[str, Any],
    ) -> tuple[int, int, str] | None:
        try:
            news_id = int(item["news_id"])
            change_sequence = int(item["change_sequence"])
            content_hash = str(item["content_hash"])
        except (KeyError, TypeError, ValueError):
            return None
        if news_id < 1 or change_sequence < 1 or not content_hash:
            return None
        return news_id, change_sequence, content_hash

    def _analysis_links_as_of(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        as_of: datetime,
    ) -> dict[tuple[int, int, str], str] | None:
        identities = {
            identity
            for item in items
            if (identity := self._news_identity(item)) is not None
        }
        if not identities:
            return {}
        try:
            path = Path(self.settings.cache_db_path)
            if not path.is_file():
                return None
            connection = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=1.0,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            try:
                links: dict[tuple[int, int, str], str] = {}
                cutoff = _iso(as_of)
                for identity in identities:
                    row = connection.execute(
                        """SELECT job_id FROM catalyst_local_analysis_links
                           WHERE news_id=? AND change_sequence=? AND content_hash=?
                             AND created_at<=?
                           ORDER BY created_at DESC LIMIT 1""",
                        (*identity, cutoff),
                    ).fetchone()
                    if row is not None:
                        links[identity] = str(row["job_id"])
                return links
            finally:
                connection.close()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None

    def _project_news_job_as_of(
        self,
        job_id: str,
        *,
        as_of: datetime,
    ) -> tuple[dict[str, Any] | None, str | None]:
        row = self._verified_news_job_row(job_id)
        if row is None:
            return None, "pending"
        observed = as_of.astimezone(timezone.utc)
        created_at = _parse_utc(row.get("created_at"))
        if created_at is None or created_at > observed:
            return None, "not_requested"
        updated_at = _parse_utc(row.get("updated_at"))
        if updated_at is None or updated_at > observed:
            # The task existed at the requested time, but its present row
            # contains a later transition or result.  Preserve only the state
            # that can be established without exposing that future update.
            return None, "pending"
        try:
            return self.ai_repository.public(row), None
        except (KeyError, TypeError, ValueError):
            return None, "pending"

    @staticmethod
    def _project_news_analysis(
        analysis: Any,
        *,
        item: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(analysis, Mapping):
            return None
        allowed_tickers = item.get("source_tickers")
        if not isinstance(allowed_tickers, list):
            allowed_tickers = []
        validation_allowed_tickers = item.get(
            "_validation_allowed_tickers",
            allowed_tickers,
        )
        if not isinstance(validation_allowed_tickers, list):
            validation_allowed_tickers = allowed_tickers
        validation_sources = item.get("_validation_sources")
        if not isinstance(validation_sources, list):
            validation_sources = []
        try:
            return validate_result(
                "news_impact",
                json.dumps(
                    dict(analysis),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                {
                    "news_id": item.get("news_id"),
                    "change_sequence": item.get("change_sequence"),
                    "content_hash": item.get("content_hash"),
                    "source": item.get("_validation_source")
                    or item.get("source"),
                    "title": item.get("_validation_title"),
                    "summary": item.get("_validation_summary"),
                    "sources": validation_sources,
                    "allowed_tickers": validation_allowed_tickers,
                },
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def _project_news_item(
        cls,
        raw: Mapping[str, Any],
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        item = dict(raw)
        # Keep a bounded display fallback before removing the duplicate source
        # fields from the public envelope.  This is source text, not generated
        # analysis, and prevents a failed/pending job from leaving a permanent
        # "waiting for Chinese title" placeholder.
        raw_source_title = (
            item.get("_validation_title")
            if "_validation_title" in item
            else item.get("title")
        )
        raw_source_summary = (
            item.get("_validation_summary")
            if "_validation_summary" in item
            else item.get("summary")
        )
        source_title = str(raw_source_title or "").strip()
        source_summary = str(raw_source_summary or "").strip()
        if source_title == _WAITING_TITLE:
            source_title = ""
        if source_summary == _WAITING_SUMMARY:
            source_summary = ""
        allowed_tickers = item.get("source_tickers")
        if not isinstance(allowed_tickers, list):
            allowed_tickers = []
        source_title_zh = _valid_zh_text(
            source_title,
            allowed_codes=allowed_tickers,
        )
        source_summary_zh = _valid_zh_text(
            source_summary,
            allowed_codes=allowed_tickers,
        )
        for field in (
            "title",
            "summary",
            "raw",
            "raw_json",
            "source_observations",
            "source_observations_json",
        ):
            item.pop(field, None)

        analysis = cls._project_news_analysis(item.get("analysis"), item=item)
        for field in (
            "_validation_source",
            "_validation_title",
            "_validation_summary",
            "_validation_sources",
            "_validation_allowed_tickers",
        ):
            item.pop(field, None)
        if analysis is not None and as_of is not None:
            result_available_at = _parse_utc(
                item.get("available_at") or item.get("analyzed_at")
            )
            if (
                result_available_at is None
                or result_available_at > as_of.astimezone(timezone.utc)
            ):
                analysis = None
        item["analysis"] = analysis
        for field in (
            "classification",
            "confidence",
            "market_relevance",
            "overall_sentiment",
            "trusted_stock_impacts",
            "headline_summary",
            "causal_summary",
        ):
            item.pop(field, None)
        if analysis is not None:
            for field in (
                "classification",
                "confidence",
                "market_relevance",
                "overall_sentiment",
                "headline_summary",
                "causal_summary",
            ):
                item[field] = analysis[field]
            item["trusted_stock_impacts"] = analysis["affected_stocks"]
        title = analysis["title_zh"] if analysis is not None else None
        summary = analysis["summary_zh"] if analysis is not None else None
        # The public *_zh fields must never relabel English source prose as
        # translated Chinese. Pending English news stays blank until the paid
        # analysis publishes; the live UI omits those rows while exact batch
        # progress remains visible.
        item["title_zh"] = title or source_title_zh or ""
        item["summary_zh"] = summary or source_summary_zh or ""
        if analysis is None:
            item.pop("analyzed_at", None)
            item.pop("available_at", None)
            if str(item.get("analysis_status")) == "completed":
                item["analysis_status"] = "pending"
                item["analysis_error_code"] = "legacy_output_hidden"
        return item

    def _project_news_envelope(
        self,
        payload: Mapping[str, Any],
        *,
        as_of: datetime | None = None,
        include_job_state: bool = True,
    ) -> dict[str, Any]:
        projected = dict(payload)
        if not include_job_state:
            projected.pop("analysis_job", None)
            projected.pop("analysis_revisions", None)
        observed = as_of
        if observed is None:
            observed = _parse_utc(projected.get("as_of"))
        if observed is not None:
            observed = observed.astimezone(timezone.utc)

        def visible(item: Mapping[str, Any]) -> bool:
            if bool(item.get("deleted")):
                return False
            if observed is None:
                return True
            source_available_at = _parse_utc(
                item.get("source_available_at") or item.get("updated_at")
            )
            return (
                source_available_at is not None
                and source_available_at <= observed
            )

        items = projected.get("items")
        if isinstance(items, list):
            projected["items"] = [
                self._project_news_item(item, as_of=observed)
                for item in items
                if isinstance(item, Mapping) and visible(item)
            ]
        item = projected.get("item")
        if isinstance(item, Mapping):
            projected["item"] = (
                self._project_news_item(item, as_of=observed)
                if visible(item)
                else None
            )

        projected_items = [
            candidate
            for candidate in projected.get("items") or []
            if isinstance(candidate, dict)
        ]
        detail_item = projected.get("item")
        if isinstance(detail_item, dict):
            projected_items.append(detail_item)
        if include_job_state and observed is not None and projected_items:
            links = self._analysis_links_as_of(projected_items, as_of=observed)
            snapshots: dict[
                tuple[int, int, str], dict[str, Any] | None
            ] = {}
            if links is not None:
                for candidate in projected_items:
                    identity = self._news_identity(candidate)
                    if identity is None:
                        continue
                    job_id = links.get(identity)
                    if job_id is None:
                        job = None
                        status_override = "not_requested"
                    else:
                        job, status_override = self._project_news_job_as_of(
                            job_id,
                            as_of=observed,
                        )
                    snapshots[identity] = job
                    if candidate.get("analysis") is None and status_override:
                        candidate["analysis_status"] = status_override
                        candidate.pop("analysis_error_code", None)

            if isinstance(detail_item, dict):
                identity = self._news_identity(detail_item)
                projected["analysis_job"] = (
                    snapshots.get(identity) if identity is not None else None
                )
        return projected

    @staticmethod
    def _project_hotspots(payload: Mapping[str, Any]) -> dict[str, Any]:
        projected = dict(payload)
        items: list[dict[str, Any]] = []
        for raw in projected.get("items") or []:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            item.pop("_analysis_published", None)
            validation_sources = item.pop("_validation_sources", [])
            if not isinstance(validation_sources, list):
                validation_sources = []
            validation_source_texts = [
                source_text
                for key in (
                    "_validation_source",
                    "_validation_title",
                    "_validation_summary",
                )
                if isinstance((source_text := item.pop(key, None)), str)
                and source_text.strip()
            ]
            validation_source_texts.extend(
                source
                for source in validation_sources
                if isinstance(source, str) and source.strip()
            )
            source_title = str(
                item.get("representative_title") or item.get("title") or ""
            ).strip()
            if source_title == _WAITING_HOTSPOT_TITLE:
                source_title = ""
            item.pop("title", None)
            allowed_codes = item.get("validated_tickers")
            if not isinstance(allowed_codes, list):
                allowed_codes = []
            validation_allowed_codes = item.pop(
                "_validation_allowed_tickers",
                allowed_codes,
            )
            if not isinstance(validation_allowed_codes, list):
                validation_allowed_codes = allowed_codes
            candidate_title = item.get("representative_title")
            if candidate_title == _WAITING_HOTSPOT_TITLE:
                candidate_title = None
            representative_title = (
                _valid_zh_text(
                    candidate_title,
                    allowed_codes=validation_allowed_codes,
                    source_texts=validation_source_texts,
                )
                or _valid_zh_text(
                    item.get("title_zh"),
                    allowed_codes=validation_allowed_codes,
                    source_texts=validation_source_texts,
                )
                or _valid_zh_text(
                    source_title,
                    allowed_codes=validation_allowed_codes,
                    source_texts=validation_source_texts,
                )
            )
            # Keep the strip Chinese-only. A group without validated Chinese
            # copy remains in the analysis queue instead of exposing source
            # prose or an internal event code as a finished headline.
            if representative_title is None:
                continue
            item["representative_title"] = representative_title
            summary = item.get("summary_zh")
            if summary == _WAITING_SUMMARY:
                summary = None
            item["summary_zh"] = (
                _valid_zh_text(
                    summary,
                    allowed_codes=validation_allowed_codes,
                    source_texts=validation_source_texts,
                )
                or ""
            )
            items.append(item)
        projected["items"] = items
        if projected.get("status") in {"active", "empty"}:
            projected["status"] = "active" if items else "empty"
        return projected

    @staticmethod
    def _project_focus_cycle(cycle: Any) -> dict[str, Any] | None:
        if not isinstance(cycle, Mapping):
            return None
        projected = dict(cycle)
        allowed_tickers = projected.pop("validation_allowed_tickers", [])
        if not isinstance(allowed_tickers, list):
            allowed_tickers = []
        allowed_event_group_ids = projected.pop(
            "validation_allowed_event_group_ids",
            [],
        )
        if not isinstance(allowed_event_group_ids, list):
            allowed_event_group_ids = []
        result = projected.get("result")
        if result is None:
            return projected
        try:
            result_data = validate_result(
                "market_focus",
                json.dumps(
                    result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                {
                    "cycle_id": projected.get("cycle_id"),
                    "as_of": projected.get("snapshot_as_of"),
                    "input_hash": projected.get("input_hash"),
                    "allowed_event_group_ids": allowed_event_group_ids,
                    "allowed_tickers": allowed_tickers,
                },
            )
        except (TypeError, ValueError):
            projected["result"] = None
            projected["error_code"] = (
                projected.get("error_code") or "legacy_output_hidden"
            )
            return projected
        projected["result"] = result_data
        return projected

    def _project_focus_cycle_for_access(
        self,
        cycle: Any,
        *,
        include_owner_state: bool,
    ) -> dict[str, Any] | None:
        projected = self._project_focus_cycle(cycle)
        if include_owner_state or projected is None:
            return projected
        if projected.get("status") != "completed" or projected.get("result") is None:
            return None
        public_fields = (
            "cycle_id",
            "status",
            "prepared_revision",
            "snapshot_as_of",
            "input_hash",
            "created_at",
            "completed_at",
            "no_new_hot_events",
            "focus_revision",
            "cycle_revision",
            "force",
            "consumes_prepared_revision",
            "event_group_count",
            "focus_symbol_count",
            "model",
            "reasoning_effort",
            "result",
        )
        return {
            field: projected[field]
            for field in public_fields
            if field in projected
        }

    def status(
        self,
        *,
        now: datetime | None = None,
        include_owner_state: bool | None = None,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        observed = now or _utc_now()
        if self.mode == "off":
            payload = {
                "enabled": False,
                "status": "disabled",
                "as_of": _iso(observed),
                "data_through": None,
                "last_sync_at": None,
                "analysis_trigger_enabled": False,
                "model": self.settings.model,
                "reasoning": self.settings.reasoning,
                "execution_mode": "background",
                "manual_refreshes": {},
                "warnings": [],
            }
            payload["analysis_availability"] = self._analysis_availability_for_access(
                include_owner_state=include_owner_state,
                now=observed,
            )
            if not include_owner_state:
                payload.pop("manual_refreshes", None)
            return payload
        if not self._cache_file_ready():
            payload = {
                "enabled": True,
                "status": "unavailable",
                "as_of": _iso(observed),
                "data_through": None,
                "last_sync_at": None,
                "remote_status": None,
                "analysis_trigger_enabled": False,
                "model": self.settings.model,
                "reasoning": self.settings.reasoning,
                "execution_mode": "background",
                "manual_refreshes": {},
                "warnings": ["cache_unavailable"],
            }
            payload["analysis_availability"] = self._analysis_availability_for_access(
                include_owner_state=include_owner_state,
                now=observed,
            )
            if not include_owner_state:
                payload.pop("manual_refreshes", None)
            return payload
        try:
            payload = dict(
                self.intelligence.status(
                    now=observed,
                    include_manual_refreshes=include_owner_state,
                )
            )
        except Exception as error:
            if not self._is_local_store_error(error):
                raise
            payload = {
                "enabled": True,
                "status": "unavailable",
                "as_of": _iso(observed),
                "data_through": None,
                "last_sync_at": None,
                "remote_status": None,
                "analysis_trigger_enabled": False,
                "model": self.settings.model,
                "reasoning": self.settings.reasoning,
                "execution_mode": "background",
                "manual_refreshes": {},
                "warnings": ["cache_unavailable"],
            }
            payload["analysis_availability"] = self._analysis_availability_for_access(
                include_owner_state=include_owner_state,
                now=observed,
            )
            if not include_owner_state:
                payload.pop("manual_refreshes", None)
            return payload
        payload["analysis_trigger_enabled"] = bool(
            include_owner_state
            and self._manual_analysis_enabled()
            and payload.get("analysis_trigger_enabled", True)
        )
        payload["analysis_availability"] = self._analysis_availability_for_access(
            include_owner_state=include_owner_state,
            now=observed,
        )
        if not include_owner_state:
            payload.pop("manual_refreshes", None)
        return payload

    def feed(
        self,
        *,
        include_owner_state: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        observed = kwargs.get("as_of") or _utc_now()
        status = self.status(
            now=observed,
            include_owner_state=include_owner_state,
        )
        if status["status"] in {"disabled", "unavailable"}:
            payload = {
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
            payload["analysis_availability"] = status["analysis_availability"]
            return payload
        try:
            payload = self.intelligence.feed(**kwargs)
        except Exception as error:
            if not self._is_local_store_error(error):
                raise
            return {
                "status": "unavailable",
                "as_of": _iso(observed),
                "data_through": None,
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
                "warnings": ["cache_unavailable"],
            }
        projected = self._project_news_envelope(
            payload,
            as_of=kwargs.get("as_of"),
            include_job_state=include_owner_state,
        )
        projected_items = [
            item
            for item in projected.get("items") or []
            if isinstance(item, dict)
        ]

        def matches_projected_filters(item: Mapping[str, Any]) -> bool:
            analysis = item.get("analysis")
            has_analysis = isinstance(analysis, Mapping)
            if not kwargs.get("include_unanalyzed", True) and not has_analysis:
                return False
            analysis_status = kwargs.get("analysis_status")
            if (
                analysis_status
                and str(item.get("analysis_status") or "") != str(analysis_status)
            ):
                return False
            if not has_analysis:
                return not any(
                    (
                        kwargs.get("classification"),
                        kwargs.get("min_abs_impact") is not None,
                        kwargs.get("horizon"),
                        kwargs.get("mechanism"),
                        int(kwargs.get("min_confidence") or 0) > 0,
                    )
                )
            assert isinstance(analysis, Mapping)
            if (
                kwargs.get("classification")
                and analysis.get("classification") != kwargs.get("classification")
            ):
                return False
            if (
                not kwargs.get("include_neutral", True)
                and analysis.get("classification") == "neutral"
            ):
                return False
            if int(analysis.get("confidence") or 0) < int(
                kwargs.get("min_confidence") or 0
            ):
                return False
            impacts = [
                impact
                for impact in analysis.get("affected_stocks") or []
                if isinstance(impact, Mapping)
            ]
            min_abs_impact = kwargs.get("min_abs_impact")
            if min_abs_impact is not None and not any(
                abs(int(impact.get("impact_score") or 0))
                >= int(min_abs_impact)
                for impact in impacts
            ):
                return False
            horizon = kwargs.get("horizon")
            if horizon and not any(
                impact.get("horizon") == horizon for impact in impacts
            ):
                return False
            mechanism = kwargs.get("mechanism")
            if mechanism and not any(
                impact.get("mechanism") == mechanism for impact in impacts
            ):
                return False
            return True

        projected["items"] = [
            item for item in projected_items if matches_projected_filters(item)
        ]
        if not projected["items"] and not projected.get("has_more"):
            projected["status"] = "empty"
        projected["analysis_availability"] = status["analysis_availability"]
        return projected

    def news(
        self,
        news_id: int,
        *,
        as_of: datetime,
        include_owner_state: bool | None = None,
    ) -> dict[str, Any] | None:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        if self.mode == "off":
            return None
        self._require_cache_ready()
        try:
            payload = self.intelligence.news(news_id, as_of=as_of)
        except Exception as error:
            if self._is_local_store_error(error):
                raise self._cache_unavailable() from error
            raise
        if payload is None:
            return None
        projected = self._project_news_envelope(
            payload,
            as_of=as_of,
            include_job_state=include_owner_state,
        )
        if projected.get("item") is None:
            return None
        projected["analysis_trigger_enabled"] = bool(
            include_owner_state
            and self._manual_analysis_enabled()
            and projected.get("analysis_trigger_enabled", True)
        )
        projected["analysis_availability"] = self._analysis_availability_for_access(
            include_owner_state=include_owner_state,
            now=as_of,
        )
        return projected

    def ticker(
        self,
        ticker: str,
        *,
        include_owner_state: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        payload = self.feed(
            ticker=ticker,
            include_owner_state=include_owner_state,
            **kwargs,
        )
        payload["ticker"] = ticker.strip().upper()
        return payload

    def batch(
        self,
        tickers: Sequence[str],
        *,
        include_owner_state: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        status = self.status(
            now=kwargs.get("as_of"),
            include_owner_state=include_owner_state,
        )
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
        try:
            payload = dict(self.intelligence.batch(tickers, **kwargs))
        except Exception as error:
            if not self._is_local_store_error(error):
                raise
            return {
                "as_of": status["as_of"],
                "status": "unavailable",
                "results": {
                    ticker: {
                        "ticker": ticker,
                        "status": "unavailable",
                        "items": [],
                        "next_cursor": None,
                        "has_more": False,
                    }
                    for ticker in tickers
                },
                "warnings": ["cache_unavailable"],
            }
        results = payload.get("results")
        if isinstance(results, Mapping):
            payload["results"] = {
                str(ticker): self._project_news_envelope(
                    result,
                    as_of=kwargs.get("as_of"),
                    include_job_state=include_owner_state,
                )
                for ticker, result in results.items()
                if isinstance(result, Mapping)
            }
        return payload

    def calendar(
        self,
        *,
        date_from: date,
        date_to: date,
        as_of: datetime,
        currencies: Sequence[str] | None,
        min_impact: str | None,
        include_owner_state: bool | None = None,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        status = self.status(
            now=as_of,
            include_owner_state=include_owner_state,
        )
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "status": status["status"],
                "as_of": status["as_of"],
                "data_through": status.get("data_through"),
                "items": [],
                "warnings": status.get("warnings", []),
            }
        try:
            return self.intelligence.calendar(
                date_from=date_from,
                date_to=date_to,
                as_of=as_of,
                currencies=currencies,
                min_impact=min_impact,
            )
        except Exception as error:
            if not self._is_local_store_error(error):
                raise
            return {
                "status": "unavailable",
                "as_of": _iso(as_of),
                "data_through": None,
                "items": [],
                "warnings": ["cache_unavailable"],
            }

    def request_refresh(
        self,
        operation_type: Literal["news", "calendar", "source_health"] = "news",
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if self.mode == "off":
            raise CatalystError(
                "catalyst_disabled",
                "Catalyst refresh is disabled while the feature is off",
                retryable=False,
                counts_for_circuit=False,
            )
        if not self._personal_etl_enabled():
            raise CatalystError(
                "catalyst_sync_disabled",
                "Catalyst refresh is disabled until MacroLens sync is configured",
                retryable=False,
                counts_for_circuit=False,
            )
        self._require_cache_ready()
        runtime_settings = self._effective_runtime()
        if not self._worker_healthy():
            raise CatalystError(
                "worker_unavailable",
                "Catalyst refresh worker is unavailable",
                retryable=True,
                counts_for_circuit=False,
            )
        if hasattr(self.intelligence, "manual_refresh_cooldown_seconds"):
            self.intelligence.manual_refresh_cooldown_seconds = int(
                runtime_settings.catalyst.manual_refresh_cooldown_seconds
                if runtime_settings is not None
                else self.personal_config.catalyst.manual_refresh_cooldown_seconds
            )
        try:
            if operation_type == "news" and idempotency_key is None:
                return self.intelligence.request_refresh()
            return self.intelligence.request_refresh(
                operation_type,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            if self._is_local_store_error(error):
                raise self._cache_unavailable() from error
            raise

    def manual_operation(self, request_id: str) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        self._require_cache_ready()
        try:
            return self.intelligence.manual_operation(request_id)
        except Exception as error:
            if self._is_local_store_error(error):
                raise self._cache_unavailable() from error
            raise

    def analysis_progress(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Read exact batch progress and accepted-result audits without writes."""

        try:
            progress = self.ai_repository.news_analysis_progress(
                now=now or _utc_now()
            )
            batch_jobs = progress.pop("_batch_jobs", [])
            completed_jobs = [
                row
                for row in batch_jobs
                if isinstance(row, Mapping)
                and str(row.get("status") or "") == "completed"
            ]
            audit_reader = getattr(
                self.intelligence,
                "news_result_audit_states",
                None,
            )
            audit_states: Mapping[str, str] = {}
            if completed_jobs and callable(audit_reader):
                audit_states = audit_reader(completed_jobs)
            succeeded = 0
            awaiting_validation = 0
            rejected = 0
            for row in completed_jobs:
                state = str(
                    audit_states.get(
                        str(row.get("job_id") or ""),
                        "awaiting_validation",
                    )
                )
                if state == "accepted":
                    succeeded += 1
                elif state == "rejected":
                    rejected += 1
                else:
                    awaiting_validation += 1
            progress["succeeded"] = succeeded
            progress["awaiting_validation"] = awaiting_validation
            progress["rejected"] = rejected
            if awaiting_validation and progress.get("status") == "completed":
                progress["status"] = "active"
            return progress
        except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError) as error:
            raise CatalystError(
                "analysis_progress_unavailable",
                "新闻分析进度暂不可用",
                retryable=True,
                counts_for_circuit=False,
            ) from error

    def hotspot_status(
        self,
        *,
        now: datetime | None = None,
        include_owner_state: bool | None = None,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        observed = now or _utc_now()
        status = self.status(
            now=observed,
            include_owner_state=include_owner_state,
        )
        if status["status"] in {"disabled", "unavailable"}:
            payload = {
                "prepared_revision": 0,
                "last_consumed_revision": 0,
                "prepared_hot_count": 0,
                "prepared_since": None,
                "last_cycle_at": None,
                "next_scheduled_at": None,
                "model": self.settings.model,
                "reasoning": self.settings.reasoning,
                "data_through": status.get("data_through"),
                "status": status["status"],
                "as_of": status["as_of"],
                "last_sync_at": status.get("last_sync_at"),
                "manual_enabled": False,
                "warnings": status.get("warnings", []),
            }
            payload["analysis_availability"] = status["analysis_availability"]
            return payload
        try:
            payload = dict(self.intelligence.hotspot_status(now=observed))
        except Exception as error:
            if self._is_local_store_error(error):
                payload = {
                    "prepared_revision": 0,
                    "last_consumed_revision": 0,
                    "prepared_hot_count": 0,
                    "prepared_since": None,
                    "last_cycle_at": None,
                    "next_scheduled_at": None,
                    "model": self.settings.model,
                    "reasoning": self.settings.reasoning,
                    "data_through": None,
                    "status": "unavailable",
                    "as_of": _iso(observed),
                    "last_sync_at": None,
                    "manual_enabled": False,
                    "warnings": ["cache_unavailable"],
                }
                payload["analysis_availability"] = self._analysis_availability_for_access(
                    include_owner_state=include_owner_state,
                    now=observed,
                )
                return payload
            raise
        payload["manual_enabled"] = bool(
            include_owner_state
            and self._manual_analysis_enabled()
            and payload.get("manual_enabled", True)
        )
        payload["analysis_availability"] = self._analysis_availability_for_access(
            include_owner_state=include_owner_state,
            now=observed,
        )
        return payload

    def hotspots(
        self,
        *,
        limit: int,
        now: datetime | None = None,
        include_owner_state: bool | None = None,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        status = self.status(
            now=now,
            include_owner_state=include_owner_state,
        )
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "status": status["status"],
                "as_of": status["as_of"],
                "data_through": status.get("data_through"),
                "items": [],
                "warnings": status.get("warnings", []),
            }
        try:
            payload = self.intelligence.hotspots(limit=limit, now=now)
        except Exception as error:
            if not self._is_local_store_error(error):
                raise
            return {
                "status": "unavailable",
                "as_of": _iso(now or _utc_now()),
                "data_through": None,
                "items": [],
                "warnings": ["cache_unavailable"],
            }
        return self._project_hotspots(payload)

    def latest_market_focus_cycle(
        self,
        *,
        now: datetime | None = None,
        include_owner_state: bool | None = None,
    ) -> dict[str, Any]:
        include_owner_state = self._resolve_owner_state(include_owner_state)
        status = self.status(
            now=now,
            include_owner_state=include_owner_state,
        )
        if status["status"] in {"disabled", "unavailable"}:
            return {
                "status": status["status"],
                "as_of": status["as_of"],
                "data_through": status.get("data_through"),
                "cycle": None,
                "latest_successful_cycle": None,
                "previous_successful_cycle": None,
                "warnings": status.get("warnings", []),
            }
        try:
            payload = dict(self.intelligence.latest_market_focus_cycle(now=now))
        except Exception as error:
            if not self._is_local_store_error(error):
                raise
            return {
                "status": "unavailable",
                "as_of": _iso(now or _utc_now()),
                "data_through": None,
                "cycle": None,
                "latest_successful_cycle": None,
                "previous_successful_cycle": None,
                "warnings": ["cache_unavailable"],
            }
        payload["cycle"] = self._project_focus_cycle_for_access(
            payload.get("cycle"),
            include_owner_state=include_owner_state,
        )
        payload["latest_successful_cycle"] = self._project_focus_cycle_for_access(
            payload.get("latest_successful_cycle"),
            include_owner_state=include_owner_state,
        )
        payload["previous_successful_cycle"] = self._project_focus_cycle_for_access(
            payload.get("previous_successful_cycle"),
            include_owner_state=include_owner_state,
        )
        return payload

    def request_market_focus_cycle(
        self,
        *,
        expected_prepared_revision: int | None,
        retry_cycle_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        self._require_cache_ready()
        self._require_analysis_available()
        try:
            arguments = {
                "expected_prepared_revision": expected_prepared_revision,
                "retry_cycle_id": retry_cycle_id,
            }
            if force:
                arguments["force"] = True
            cycle = self.intelligence.request_market_focus_cycle(**arguments)
            projected = self._project_focus_cycle(cycle)
            if projected is None:
                raise RuntimeError("market_focus_cycle_invalid")
            return projected
        except Exception as error:
            classified = self._analysis_request_error(error)
            if classified is not None:
                raise classified from error
            raise

    def market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        include_owner_state = self._resolve_owner_state(None)
        if self.mode == "off":
            return None
        self._require_cache_ready()
        try:
            cycle = self.intelligence.market_focus_cycle(cycle_id)
        except Exception as error:
            if self._is_local_store_error(error):
                raise self._cache_unavailable() from error
            raise
        return self._project_focus_cycle_for_access(
            cycle,
            include_owner_state=include_owner_state,
        )

    def cancel_market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        self._require_cache_ready()
        try:
            cycle = self.intelligence.cancel_market_focus_cycle(cycle_id)
        except Exception as error:
            if self._is_local_store_error(error):
                raise self._cache_unavailable() from error
            raise
        return self._project_focus_cycle(cycle)

    def request_analysis(self, news_id: int, *, force: bool) -> dict[str, Any]:
        self._require_cache_ready()
        self._require_analysis_available()
        try:
            return self.intelligence.request_analysis(news_id, force=force)
        except Exception as error:
            classified = self._analysis_request_error(error)
            if classified is not None:
                raise classified from error
            raise

    def analysis_job(self, job_id: str) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        row = self._verified_news_job_row(job_id)
        if row is None:
            return None
        return self.ai_repository.public(row)

    def cancel_analysis_job(self, job_id: str) -> dict[str, Any] | None:
        self._require_cache_ready()
        if not self._ai_store_ready():
            raise self._cache_unavailable()
        row = self._verified_news_job_row(job_id)
        if row is None:
            return None
        updated = self.ai_repository.request_cancel(job_id)
        return (
            self.ai_repository.public(cast(dict[str, Any], updated))
            if updated is not None
            else None
        )
