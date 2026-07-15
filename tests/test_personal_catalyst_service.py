from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import catalysts as catalyst_api
from app.personal_config import FeatureConfig, PersonalConfig
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts.errors import CatalystError
from app.services.catalysts.etl_client import NewsChangesPage
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.services.catalysts.personal_service import PersonalCatalystService
from app.services.catalysts.config import CatalystSettings
from app.worker.tasks import CatalystSyncTask, FocusTask


NOW = datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)


def _news_result(*, news_id: int = 101, change_sequence: int = 7) -> dict[str, Any]:
    return {
        "output_language": "zh-CN",
        "news_id": news_id,
        "change_sequence": change_sequence,
        "content_hash": "hash-101",
        "title_zh": "芯片企业发布最新业绩",
        "summary_zh": "公司收入增长，但管理层仍提示需求波动风险。",
        "headline_summary": "收入增长与需求风险同时存在。",
        "overall_sentiment": 20,
        "classification": "bullish",
        "confidence": 70,
        "market_relevance": 80,
        "affected_stocks": [],
        "affected_sectors": ["半导体"],
        "affected_commodities": [],
        "causal_summary": "收入改善可能提振行业预期，但持续性仍待观察。",
        "key_factors": ["收入增长"],
        "uncertainty_notes": ["后续需求仍有波动"],
        "insufficient_context": False,
    }


def _focus_result(*, input_hash: str) -> dict[str, Any]:
    return {
        "output_language": "zh-CN",
        "cycle_id": "mfc_" + "a" * 32,
        "as_of": "2026-07-15T04:00:00Z",
        "input_hash": input_hash,
        "title_zh": "市场焦点等待进一步确认",
        "summary_zh": "现有事件影响方向不一，仍需观察后续数据。",
        "headline_summary": "市场缺少方向一致的新催化剂。",
        "market_summary": "不同事件相互抵消，暂未形成清晰主线。",
        "dominant_events": [],
        "market_uncertainties": ["后续数据仍可能改变判断"],
        "affected_sectors": [],
        "focus_ticker_assessments": [],
        "no_new_material_catalyst": True,
        "insufficient_context": False,
    }


class FakeIntelligence:
    def __init__(self) -> None:
        self.actions: list[tuple[str, Any]] = []
        self.item = {
            "news_id": 101,
            "change_sequence": 7,
            "content_hash": "hash-101",
            "deleted": False,
            "updated_at": "2026-07-15T03:58:00Z",
            "available_at": "2026-07-15T03:59:00Z",
            "title": "Chip company reports latest results",
            "summary": "Revenue rose, but demand remains uncertain.",
            "raw": {"secret_source_text": "English raw body"},
            "source_observations": [{"title": "English source title"}],
            "title_zh": "This English title must not be shown",
            "summary_zh": "This English summary must not be shown",
            "analysis_status": "completed",
            "analysis": _news_result(),
        }

    def status(self, *, now=None):
        return {
            "enabled": True,
            "status": "ok",
            "as_of": (now or NOW).isoformat().replace("+00:00", "Z"),
            "analysis_trigger_enabled": True,
        }

    def feed(self, **kwargs):
        return {"status": "ok", "as_of": NOW.isoformat(), "items": [self.item]}

    def news(self, news_id, *, as_of):
        if news_id != 101:
            return None
        return {
            "status": "ok",
            "as_of": as_of.isoformat(),
            "item": self.item,
            "analysis_trigger_enabled": True,
        }

    def ticker(self, ticker, **kwargs):
        return {"ticker": ticker, "status": "ok", "items": [self.item]}

    def batch(self, tickers, **kwargs):
        return {
            "status": "ok",
            "results": {
                ticker: {"ticker": ticker, "status": "ok", "items": [self.item]}
                for ticker in tickers
            },
        }

    def calendar(self, **kwargs):
        return {"status": "ok", "items": []}

    def hotspot_status(self, *, now=None):
        return {
            "status": "ok",
            "prepared_revision": 3,
            "manual_enabled": True,
        }

    def hotspots(self, *, limit, now=None):
        return {
            "status": "ok",
            "items": [
                {
                    "event_group_id": "event-1",
                    "representative_title": "English hotspot title",
                    "title": "Another English title",
                }
            ][:limit],
        }

    def latest_market_focus_cycle(self, *, now=None):
        return {"status": "ok", "cycle": None}

    def request_market_focus_cycle(
        self, *, expected_prepared_revision, retry_cycle_id=None, force=False
    ):
        action = ("focus", expected_prepared_revision, retry_cycle_id)
        self.actions.append((*action, True) if force else action)
        return {"cycle_id": "mfc_" + "a" * 32, "status": "pending"}

    def market_focus_cycle(self, cycle_id):
        return None

    def cancel_market_focus_cycle(self, cycle_id):
        self.actions.append(("cancel_focus", cycle_id))
        return {"cycle_id": cycle_id, "status": "cancelled", "result": None}

    def request_refresh(self):
        self.actions.append(("refresh",))
        return {"request_id": "refresh-1", "status": "queued"}

    def request_analysis(self, news_id, *, force):
        self.actions.append(("analysis", news_id, force))
        return {"job_id": "aij_" + "b" * 32, "status": "pending"}


class FakeAIRepository:
    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows = rows or {}

    def get_job(self, job_id):
        return self.rows.get(job_id)

    def public(self, row, *, cached=False):
        return {
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "result": row.get("result"),
        }

    def request_cancel(self, job_id):
        row = self.rows.get(job_id)
        if row is not None:
            row = {**row, "status": "cancelled"}
            self.rows[job_id] = row
        return row


def _service(
    mode: str,
    engine=None,
    repository=None,
    *,
    cache_path=None,
) -> PersonalCatalystService:
    settings = type(
        "SettingsStub",
        (),
        {
            "cache_db_path": cache_path or "/tmp/not-used.db",
            "model": "gpt-5.6-terra",
            "reasoning": "max",
        },
    )()
    personal = PersonalConfig(features=FeatureConfig(catalyst_mode=mode))
    return PersonalCatalystService(
        settings,
        intelligence=engine or FakeIntelligence(),
        ai_repository=repository or FakeAIRepository(),
        personal_config=personal,
        ai_settings=type("AISettingsStub", (), {})(),
    )


def test_personal_feed_never_projects_source_language_title_or_summary() -> None:
    service = _service("read")

    item = service.feed(as_of=NOW)["items"][0]

    assert item["title_zh"] == "芯片企业发布最新业绩"
    assert item["summary_zh"] == "公司收入增长，但管理层仍提示需求波动风险。"
    assert item["analysis"]["output_language"] == "zh-CN"
    assert "title" not in item
    assert "summary" not in item
    assert "raw" not in item
    assert "source_observations" not in item
    assert "English" not in str(item)


def test_invalid_or_revision_mismatched_analysis_fails_closed() -> None:
    engine = FakeIntelligence()
    engine.item = {
        **engine.item,
        "analysis": _news_result(change_sequence=6),
        "title_zh": "English only",
        "summary_zh": "English only",
    }

    item = _service("read", engine=engine).feed(as_of=NOW)["items"][0]

    assert item["analysis"] is None
    assert item["analysis_status"] == "pending"
    assert item["title_zh"] == "中文标题等待生成"
    assert item["summary_zh"] == "中文摘要等待生成"


def test_deleted_news_and_english_hotspot_titles_fail_closed() -> None:
    engine = FakeIntelligence()
    engine.item = {**engine.item, "deleted": True}
    service = _service("read", engine=engine)

    assert service.feed(as_of=NOW)["items"] == []
    assert service.news(101, as_of=NOW) is None
    hotspot = service.hotspots(limit=10)["items"][0]
    assert hotspot["representative_title"] == "热点标题等待中文分析"
    assert "title" not in hotspot


def test_news_available_after_as_of_is_not_projected() -> None:
    engine = FakeIntelligence()
    service = _service("read", engine=engine)
    historical = datetime(2026, 7, 15, 3, 57, tzinfo=timezone.utc)

    assert service.feed(as_of=historical)["items"] == []
    assert service.news(101, as_of=historical) is None


def test_analysis_available_after_as_of_is_hidden_but_news_remains_visible() -> None:
    engine = FakeIntelligence()
    service = _service("read", engine=engine)
    historical = datetime(2026, 7, 15, 3, 58, 30, tzinfo=timezone.utc)

    item = service.feed(as_of=historical)["items"][0]

    assert item["analysis"] is None
    assert item["title_zh"] == "中文标题等待生成"
    assert item["summary_zh"] == "中文摘要等待生成"


@pytest.mark.parametrize(
    ("link_created_at", "job_updated_at", "job_status", "expected_status"),
    (
        (
            datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc),
            "pending",
            "not_requested",
        ),
        (
            datetime(2026, 7, 15, 3, 59, tzinfo=timezone.utc),
            datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc),
            "completed",
            "pending",
        ),
    ),
)
def test_historical_news_never_exposes_future_job_state_or_result(
    tmp_path,
    link_created_at,
    job_updated_at,
    job_status,
    expected_status,
) -> None:
    job_id = "aij_" + "f" * 32
    cache_path = tmp_path / "catalyst.db"
    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            """CREATE TABLE catalyst_local_analysis_links (
                   news_id INTEGER NOT NULL,
                   change_sequence INTEGER NOT NULL,
                   content_hash TEXT NOT NULL,
                   job_id TEXT NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        connection.execute(
            """INSERT INTO catalyst_local_analysis_links(
                   news_id,change_sequence,content_hash,job_id,created_at
               ) VALUES(?,?,?,?,?)""",
            (
                101,
                7,
                "hash-101",
                job_id,
                link_created_at.isoformat().replace("+00:00", "Z"),
            ),
        )

    schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
    repository = FakeAIRepository(
        {
            job_id: {
                "job_id": job_id,
                "job_type": "news_impact",
                "status": job_status,
                "model": "gpt-5.6-terra",
                "reasoning": "max",
                "execution_mode": "background",
                "prompt_version": "news-impact-zh-cn-v2",
                "schema_version": schema_version,
                "schema_sha256": schema_hash,
                "created_at": link_created_at.isoformat().replace("+00:00", "Z"),
                "updated_at": job_updated_at.isoformat().replace("+00:00", "Z"),
                "result": _news_result() if job_status == "completed" else None,
            }
        }
    )
    engine = FakeIntelligence()
    engine.item = {
        **engine.item,
        "analysis": None,
        "analysis_status": job_status,
    }
    original_news = engine.news

    def news_with_job(news_id, *, as_of):
        payload = original_news(news_id, as_of=as_of)
        if payload is not None:
            payload["analysis_job"] = {"job_id": job_id, "status": job_status}
        return payload

    engine.news = news_with_job
    service = _service(
        "manual",
        engine=engine,
        repository=repository,
        cache_path=cache_path,
    )

    feed_item = service.feed(as_of=NOW)["items"][0]
    detail = service.news(101, as_of=NOW)

    assert detail is not None
    assert feed_item["analysis_status"] == expected_status
    assert detail["item"]["analysis_status"] == expected_status
    assert detail["analysis_job"] is None
    assert detail["item"]["analysis"] is None
    assert "analysis_error_code" not in detail["item"]


def test_read_mode_keeps_refresh_and_cancel_available_but_blocks_new_analysis() -> None:
    engine = FakeIntelligence()
    service = _service("read", engine=engine)

    assert service.status(now=NOW)["analysis_trigger_enabled"] is False
    assert service.news(101, as_of=NOW)["analysis_trigger_enabled"] is False
    assert service.hotspot_status(now=NOW)["manual_enabled"] is False
    service.feed(as_of=NOW)
    service.latest_market_focus_cycle(now=NOW)
    assert engine.actions == []

    service.request_refresh()
    service.cancel_market_focus_cycle("mfc_" + "a" * 32)
    assert service.cancel_analysis_job("aij_" + "b" * 32) is None

    create_actions = (
        lambda: service.request_analysis(101, force=False),
        lambda: service.request_market_focus_cycle(expected_prepared_revision=3),
    )
    for action in create_actions:
        with pytest.raises(CatalystError, match="read_only_mode"):
            action()
    assert engine.actions == [
        ("refresh",),
        ("cancel_focus", "mfc_" + "a" * 32),
    ]


def test_refresh_uses_personal_cooldown_when_runtime_settings_are_unavailable() -> None:
    engine = FakeIntelligence()
    engine.manual_refresh_cooldown_seconds = 999
    service = _service("read", engine=engine)
    service._effective_runtime = lambda: None

    result = service.request_refresh()

    assert result["status"] == "queued"
    assert engine.manual_refresh_cooldown_seconds == 30
    assert engine.actions == [("refresh",)]


def test_removed_legacy_false_switches_do_not_gate_owner_actions() -> None:
    engine = FakeIntelligence()
    engine.manual_refresh_cooldown_seconds = 999
    service = _service("manual", engine=engine)
    runtime = service._effective_runtime()
    assert runtime is not None
    service._effective_runtime = lambda: SimpleNamespace(
        ai=runtime.ai,
        catalyst=SimpleNamespace(
            manual_force_reanalysis=False,
            manual_refresh_enabled=False,
            manual_refresh_cooldown_seconds=17,
        ),
    )

    service.request_refresh()
    service.request_market_focus_cycle(
        expected_prepared_revision=3,
        force=True,
    )

    assert engine.manual_refresh_cooldown_seconds == 17
    assert engine.actions == [
        ("refresh",),
        ("focus", 3, None, True),
    ]


def test_off_mode_is_the_only_feature_mode_that_rejects_refresh() -> None:
    service = _service("off")

    with pytest.raises(CatalystError) as captured:
        service.request_refresh()

    assert captured.value.code == "catalyst_disabled"


def test_manual_analysis_switch_reports_a_runtime_reason_not_a_permission() -> None:
    service = _service("manual")
    runtime = service._effective_runtime()
    assert runtime is not None
    disabled = runtime.model_copy(
        update={
            "ai": runtime.ai.model_copy(
                update={"manual_analysis_enabled": False}
            )
        }
    )
    service._effective_runtime = lambda: disabled

    availability = service.analysis_availability(now=NOW)

    assert availability["enabled"] is False
    assert availability["reason"] == "manual_analysis_disabled"
    with pytest.raises(CatalystError) as captured:
        service.request_analysis(101, force=False)
    assert captured.value.code == "manual_analysis_disabled"


@pytest.mark.parametrize("mode", ["manual", "scheduled"])
def test_interactive_modes_only_delegate_explicit_calls(mode: str) -> None:
    engine = FakeIntelligence()
    service = _service(mode, engine=engine)

    service.feed(as_of=NOW)
    assert engine.actions == []
    service.request_refresh()
    service.request_analysis(101, force=True)
    service.request_market_focus_cycle(expected_prepared_revision=3)

    assert engine.actions == [
        ("refresh",),
        ("analysis", 101, True),
        ("focus", 3, None),
    ]


def test_manual_refresh_fails_closed_when_worker_is_unavailable(monkeypatch) -> None:
    engine = FakeIntelligence()
    service = _service("manual", engine=engine)
    monkeypatch.setattr(service, "_worker_healthy", lambda: False)

    with pytest.raises(CatalystError, match="worker_unavailable"):
        service.request_refresh()

    assert engine.actions == []


def test_analysis_job_endpoint_is_limited_to_news_jobs() -> None:
    news_id = "aij_" + "n" * 32
    other_id = "aij_" + "o" * 32
    schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
    repository = FakeAIRepository(
        {
            news_id: {
                "job_id": news_id,
                "job_type": "news_impact",
                "status": "completed",
                "model": "gpt-5.6-terra",
                "reasoning": "max",
                "execution_mode": "background",
                "prompt_version": "news-impact-zh-cn-v2",
                "schema_version": schema_version,
                "schema_sha256": schema_hash,
                "result": _news_result(),
            },
            other_id: {
                "job_id": other_id,
                "job_type": "earnings_impact",
                "status": "completed",
                "result": {"private": "not a Catalyst news result"},
            },
        }
    )
    service = _service("manual", repository=repository)

    assert service.analysis_job(news_id)["job_type"] == "news_impact"
    assert service.analysis_job(other_id) is None
    assert service.cancel_analysis_job(other_id) is None

    repository.rows[news_id]["prompt_version"] = "news-impact-legacy-v1"
    assert service.analysis_job(news_id) is None


def test_read_mode_can_cancel_a_news_job_created_before_the_mode_changed() -> None:
    job_id = "aij_" + "c" * 32
    schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
    repository = FakeAIRepository(
        {
            job_id: {
                "job_id": job_id,
                "job_type": "news_impact",
                "status": "pending",
                "model": "gpt-5.6-terra",
                "reasoning": "max",
                "execution_mode": "background",
                "prompt_version": "news-impact-zh-cn-v2",
                "schema_version": schema_version,
                "schema_sha256": schema_hash,
                "result": None,
            }
        }
    )
    service = _service("read", repository=repository)

    cancelled = service.cancel_analysis_job(job_id)

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"


def test_focus_result_must_match_cycle_time_and_input_hash() -> None:
    expected_hash = "a" * 64
    cycle = {
        "cycle_id": "mfc_" + "a" * 32,
        "snapshot_as_of": "2026-07-15T04:00:00Z",
        "input_hash": expected_hash,
        "status": "completed",
        "result": _focus_result(input_hash=expected_hash),
    }

    projected = PersonalCatalystService._project_focus_cycle(cycle)
    assert projected["result"]["input_hash"] == expected_hash

    mismatched = {
        **cycle,
        "result": _focus_result(input_hash="b" * 64),
    }
    hidden = PersonalCatalystService._project_focus_cycle(mismatched)
    assert hidden["result"] is None
    assert hidden["error_code"] == "legacy_output_hidden"


def test_api_factory_always_uses_personal_service(monkeypatch) -> None:
    settings = type(
        "SettingsStub",
        (),
        {
            "catalyst_mode": "display",
        },
    )()
    personal = object()
    monkeypatch.setattr(catalyst_api, "PersonalCatalystService", lambda value: personal)

    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    assert catalyst_api._service(settings) is personal
    monkeypatch.setenv("INTERNAL_API_TOKEN", "configured-token")
    assert catalyst_api._service(settings) is personal
    settings.catalyst_mode = "disabled"
    assert catalyst_api._service(settings) is personal


def test_local_api_settings_keep_fixed_model_and_drop_remote_hmac_credentials(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    monkeypatch.setenv("OPENAI_REASONING", "high")
    settings = CatalystSettings(
        _env_file=None,
        cache_db_path=tmp_path / "missing.db",
        MACROLENS_READ_KEY_ID="ignored-old-key",
        MACROLENS_READ_SECRET="ignored-old-secret",
    )

    assert settings.cache_db_path == tmp_path / "missing.db"
    assert settings.model == "gpt-5.6-terra"
    assert settings.reasoning == "max"
    assert not hasattr(settings, "read_key_id")
    assert not hasattr(settings, "read_secret")
    with pytest.raises(ValueError):
        CatalystSettings(_env_file=None, model="other-model")


def test_unified_worker_degrades_safely_when_owner_token_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    personal = PersonalConfig(features=FeatureConfig(catalyst_mode="read"))

    sync_result = asyncio.run(
        CatalystSyncTask("test", personal_config=personal)()
    )
    focus_result = asyncio.run(
        FocusTask("test", enabled=True, personal_config=personal)()
    )

    assert sync_result.status == "degraded"
    assert sync_result.error_code == "personal_etl_token_missing"
    assert sync_result.details == {
        "processed": [],
        "reason": "internal_token_missing",
    }
    assert focus_result.status == "degraded"
    assert focus_result.error_code == "personal_etl_token_missing"
    assert focus_result.details == {"result": "internal_token_missing"}


def test_unconfigured_default_read_view_is_safe_and_does_not_create_cache(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MACROLENS_ENABLED", raising=False)
    monkeypatch.delenv("CATALYST_MODE", raising=False)
    cache_path = tmp_path / "missing.db"
    settings = CatalystSettings(
        _env_file=None,
        cache_db_path=cache_path,
    )

    service = catalyst_api._service(settings)

    assert isinstance(service, PersonalCatalystService)
    assert service.status()["status"] == "unavailable"
    assert not cache_path.exists()


def test_api_read_mode_rejects_explicit_analysis_without_creating_a_job() -> None:
    engine = FakeIntelligence()
    service = _service("read", engine=engine)
    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: service
    client = TestClient(app)

    read = client.get("/api/catalysts/feed")
    create = client.post("/api/catalysts/news/101/analysis", json={})

    assert read.status_code == 200
    assert read.json()["items"][0]["title_zh"] == "芯片企业发布最新业绩"
    assert create.status_code == 409
    assert create.json()["detail"]["code"] == "read_only_mode"
    assert engine.actions == []


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        ("/api/catalysts/news/101/analysis", {}),
        (
            "/api/catalysts/market-focus-cycles",
            {"expected_prepared_revision": 3},
        ),
    ),
)
def test_analysis_queue_full_returns_429_with_retry_after(
    path: str,
    payload: dict[str, Any],
) -> None:
    class QueueFullIntelligence(FakeIntelligence):
        @staticmethod
        def _raise_queue_full() -> None:
            raise RuntimeError("ai_job_queue_full")

        def request_analysis(self, news_id, *, force):
            self._raise_queue_full()

        def request_market_focus_cycle(
            self,
            *,
            expected_prepared_revision,
            retry_cycle_id=None,
        ):
            self._raise_queue_full()

    service = _service("manual", engine=QueueFullIntelligence())
    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: service
    app.dependency_overrides[catalyst_api.require_expensive_action] = lambda: None
    client = TestClient(app)

    response = client.post(path, json=payload)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json()["detail"] == {
        "code": "ai_job_queue_full",
        "message": "分析队列已满，请稍后重试",
        "retryable": True,
        "retry_after": 60,
    }


def test_active_market_focus_cycle_returns_409_with_safe_chinese_message() -> None:
    class ActiveFocusIntelligence(FakeIntelligence):
        def request_market_focus_cycle(
            self,
            *,
            expected_prepared_revision,
            retry_cycle_id=None,
        ):
            raise CatalystError(
                "analysis_in_progress",
                "已有市场焦点分析正在运行",
                retryable=True,
                counts_for_circuit=False,
            )

    service = _service("manual", engine=ActiveFocusIntelligence())
    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: service
    app.dependency_overrides[catalyst_api.require_expensive_action] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/api/catalysts/market-focus-cycles",
        json={"expected_prepared_revision": 3},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "analysis_in_progress",
        "message": "已有市场焦点分析正在运行",
        "retryable": True,
        "retry_after": None,
    }


@pytest.mark.parametrize(
    "path",
    (
        "/api/catalysts/news/101/analysis",
        "/api/catalysts/market-focus-cycles",
    ),
)
@pytest.mark.parametrize(
    ("capacity", "code", "message", "retry_after"),
    (
        (
            {
                "budget_available": False,
                "job_limit_available": False,
                "dollar_budget_available": True,
            },
            "daily_job_limit_reached",
            "今日任务次数已用完",
            None,
        ),
        (
            {
                "budget_available": False,
                "job_limit_available": True,
                "dollar_budget_available": False,
            },
            "daily_budget_usd_reached",
            "今日分析预算已用完",
            None,
        ),
        (
            {
                "budget_available": True,
                "job_limit_available": True,
                "dollar_budget_available": True,
                "cooldown_complete": False,
                "cooldown_until": (NOW + timedelta(seconds=44)).isoformat(),
            },
            "analysis_cooldown_active",
            "分析正在冷却中",
            45,
        ),
    ),
)
def test_analysis_capacity_errors_keep_their_http_and_retry_semantics(
    path: str,
    capacity: dict[str, Any],
    code: str,
    message: str,
    retry_after: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CapacityRepository(FakeAIRepository):
        def budget_snapshot(self, **_kwargs):
            return {
                "concurrency_available": True,
                "cooldown_complete": True,
                **capacity,
            }

    runtime_settings = SimpleNamespace(
        ai=SimpleNamespace(
            manual_analysis_enabled=True,
            daily_max_jobs=4,
            daily_budget_usd=2.0,
            manual_analysis_cooldown_seconds=30,
        ),
        catalyst=SimpleNamespace(manual_force_reanalysis=True),
    )
    monkeypatch.setattr(
        "app.services.catalysts.personal_service.get_effective_runtime_settings",
        lambda: runtime_settings,
    )
    monkeypatch.setattr(
        "app.services.catalysts.personal_service._utc_now",
        lambda: NOW,
    )
    service = _service("manual", repository=CapacityRepository())
    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: service
    app.dependency_overrides[catalyst_api.require_expensive_action] = lambda: None
    client = TestClient(app)
    payload = {} if "/news/" in path else {"expected_prepared_revision": 3}

    response = client.post(path, json=payload)

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "code": code,
        "message": message,
        "retryable": retry_after is not None,
        "retry_after": retry_after,
    }
    if retry_after is None:
        assert "Retry-After" not in response.headers
    else:
        assert response.headers["Retry-After"] == str(retry_after)


def test_real_local_intelligence_keeps_unanalyzed_news_visible_without_english(
    tmp_path,
) -> None:
    cache_path = tmp_path / "catalyst.db"
    ai_path = tmp_path / "ai-jobs.db"
    etl = CatalystEtlRepository(cache_path)
    etl.initialize()
    state = etl.state("news")
    page = NewsChangesPage.model_validate(
        {
            "items": [
                {
                    "sequence": 1,
                    "operation": "upsert",
                    "changed_at": "2026-07-15T03:58:00Z",
                    "source_updated_at": "2026-07-15T03:58:00Z",
                    "available_at": "2026-07-15T03:58:00Z",
                    "news_id": 101,
                    "news": {
                        "id": 101,
                        "source": "wire",
                        "title": "English source headline",
                        "summary": "English source summary",
                        "url": "https://example.com/news/101",
                        "image_url": None,
                        "published_at": "2026-07-15T03:50:00Z",
                        "fetched_at": "2026-07-15T03:57:00Z",
                        "updated_at": "2026-07-15T03:58:00Z",
                        "source_tickers": ["NVDA"],
                        "sources": ["wire"],
                        "source_count": 1,
                        "content_hash": "hash-101",
                    },
                }
            ],
            "has_more": False,
            "next_cursor": None,
            "watermark": {
                "sequence": 1,
                "as_of": "2026-07-15T03:58:00Z",
            },
            "next_updated_after": "2026-07-15T03:58:00Z",
            "next_after_sequence": 1,
        }
    )
    etl.apply_news_page(
        page,
        expected_cursor=state.cursor,
        expected_generation=state.generation,
    )
    settings = type(
        "SettingsStub",
        (),
        {
            "cache_db_path": cache_path,
            "model": "gpt-5.6-terra",
            "reasoning": "max",
        },
    )()
    ai_settings = type(
        "AISettingsStub",
        (),
        {
            "openai_job_db_path": ai_path,
            "openai_model": "gpt-5.6-terra",
            "openai_reasoning": "max",
            "openai_job_max_queued": 200,
        },
    )()
    service = PersonalCatalystService(
        settings,
        ai_repository=AIJobRepository(ai_path),
        personal_config=PersonalConfig(
            features=FeatureConfig(catalyst_mode="read")
        ),
        ai_settings=ai_settings,
    )
    service.intelligence.reconcile()

    payload = service.feed(as_of=NOW)

    assert len(payload["items"]) == 1
    assert payload["items"][0]["analysis"] is None
    assert payload["items"][0]["title_zh"] == "中文标题等待生成"
    assert payload["items"][0]["summary_zh"] == "中文摘要等待生成"
    assert "English" not in str(payload["items"][0])


def test_web_reads_do_not_initialize_missing_worker_databases(tmp_path) -> None:
    cache_path = tmp_path / "missing-catalyst.db"
    ai_path = tmp_path / "missing-ai.db"
    settings = type(
        "SettingsStub",
        (),
        {
            "cache_db_path": cache_path,
            "model": "gpt-5.6-terra",
            "reasoning": "max",
        },
    )()
    ai_settings = type(
        "AISettingsStub",
        (),
        {
            "openai_job_db_path": ai_path,
            "openai_model": "gpt-5.6-terra",
            "openai_reasoning": "max",
            "openai_job_max_queued": 200,
        },
    )()
    service = PersonalCatalystService(
        settings,
        ai_repository=AIJobRepository(ai_path),
        personal_config=PersonalConfig(
            features=FeatureConfig(catalyst_mode="manual")
        ),
        ai_settings=ai_settings,
    )

    status = service.status(now=NOW)
    feed = service.feed(as_of=NOW)

    assert status["status"] == "unavailable"
    assert feed["status"] == "unavailable"
    assert feed["items"] == []
    assert not cache_path.exists()
    assert not ai_path.exists()
    with pytest.raises(CatalystError, match="cache_unavailable"):
        service.request_analysis(101, force=False)
    assert not cache_path.exists()
    assert not ai_path.exists()
