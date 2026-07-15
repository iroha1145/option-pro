from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
            "action_enabled": True,
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
            "action_enabled": True,
            "capability": "enabled",
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
        self, *, expected_prepared_revision, retry_cycle_id=None
    ):
        self.actions.append(
            ("focus", expected_prepared_revision, retry_cycle_id)
        )
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


def test_read_mode_gets_do_not_create_jobs_and_all_actions_are_disabled() -> None:
    engine = FakeIntelligence()
    service = _service("read", engine=engine)

    assert service.status(now=NOW)["analysis_trigger_enabled"] is False
    assert service.news(101, as_of=NOW)["analysis_trigger_enabled"] is False
    assert service.hotspot_status(now=NOW)["manual_enabled"] is False
    service.feed(as_of=NOW)
    service.latest_market_focus_cycle(now=NOW)
    assert engine.actions == []

    actions = (
        lambda: service.request_refresh(),
        lambda: service.request_analysis(101, force=False),
        lambda: service.request_market_focus_cycle(expected_prepared_revision=3),
        lambda: service.cancel_market_focus_cycle("mfc_" + "a" * 32),
        lambda: service.cancel_analysis_job("aij_" + "b" * 32),
    )
    for action in actions:
        with pytest.raises(CatalystError, match="capability_disabled"):
            action()
    assert engine.actions == []


@pytest.mark.parametrize("mode", ["manual", "scheduled"])
def test_action_modes_only_delegate_explicit_calls(mode: str) -> None:
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


def test_api_factory_uses_personal_read_view_and_keeps_legacy_fallback(
    monkeypatch,
) -> None:
    settings = type(
        "SettingsStub",
        (),
        {
            "catalyst_mode": "display",
            "read_key_id": "",
            "read_secret": "",
        },
    )()
    personal = object()
    legacy = object()
    monkeypatch.setattr(catalyst_api, "PersonalCatalystService", lambda value: personal)
    monkeypatch.setattr(catalyst_api, "CatalystService", lambda value: legacy)

    monkeypatch.delenv("MACROLENS_INTERNAL_TOKEN", raising=False)
    assert catalyst_api._service(settings) is personal
    monkeypatch.setenv("MACROLENS_INTERNAL_TOKEN", "configured-token")
    assert catalyst_api._service(settings) is personal

    settings.read_key_id = "legacy-read-key"
    settings.read_secret = "legacy-read-secret"
    monkeypatch.delenv("MACROLENS_INTERNAL_TOKEN", raising=False)
    assert catalyst_api._service(settings) is legacy

    settings.catalyst_mode = "disabled"
    assert catalyst_api._service(settings) is legacy


def test_new_bearer_settings_do_not_require_legacy_hmac(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MACROLENS_ENABLED", raising=False)
    monkeypatch.delenv("CATALYST_MODE", raising=False)
    settings = CatalystSettings(
        _env_file=None,
        MACROLENS_BASE_URL="https://macrolens.example",
        MACROLENS_INTERNAL_TOKEN="owner-token",
        MACROLENS_CACHE_DB_PATH=tmp_path / "missing.db",
    )

    assert settings.internal_token.get_secret_value() == "owner-token"
    assert settings.read_key_id == ""
    assert catalyst_api._internal_token_configured(settings) is True

    with pytest.raises(ValueError, match="must use HTTPS"):
        CatalystSettings(
            _env_file=None,
            MACROLENS_BASE_URL="http://localhost:9876",
            MACROLENS_ALLOW_LOCAL_HTTP=True,
            MACROLENS_INTERNAL_TOKEN="owner-token",
            MACROLENS_CACHE_DB_PATH=tmp_path / "missing.db",
        )


def test_unconfigured_default_read_view_is_safe_and_does_not_create_cache(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MACROLENS_ENABLED", raising=False)
    monkeypatch.delenv("CATALYST_MODE", raising=False)
    cache_path = tmp_path / "missing.db"
    settings = CatalystSettings(
        _env_file=None,
        MACROLENS_CACHE_DB_PATH=cache_path,
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
    app.dependency_overrides[catalyst_api.require_expensive_action] = lambda: None
    client = TestClient(app)

    read = client.get("/api/catalysts/feed")
    create = client.post("/api/catalysts/news/101/analysis", json={})

    assert read.status_code == 200
    assert read.json()["items"][0]["title_zh"] == "芯片企业发布最新业绩"
    assert create.status_code == 503
    assert create.json()["detail"]["code"] == "capability_disabled"
    assert engine.actions == []


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
