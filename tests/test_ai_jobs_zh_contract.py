from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import (
    validate_result,
    validate_simplified_chinese_text,
)
from app.services.ai_jobs.repository import AIJobRepository
from app.services.ai_jobs.worker import process_job


def _settings(path):
    return SimpleNamespace(
        openai_api_key=SecretStr("test-key"),
        openai_model="gpt-5.6-terra",
        openai_reasoning="max",
        openai_execution_mode="background",
        openai_timeout_seconds=900,
        openai_control_timeout_seconds=30,
        openai_max_concurrency=1,
        openai_daily_max_jobs=4,
        openai_job_db_path=path,
        openai_job_lease_seconds=60,
        openai_job_max_age_seconds=86400,
        openai_background_initial_poll_seconds=2,
        openai_background_max_poll_seconds=15,
        openai_background_poll_timeout_seconds=1800,
    )


def _create_job(repository: AIJobRepository, ticker: str):
    version, digest = runtime.schema_identity("earnings_impact")
    return repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": ticker, "name": ticker},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-zh-cn-v3",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )[0]


def _news_result() -> dict:
    return {
        "output_language": "zh-CN",
        "news_id": 1,
        "change_sequence": 7,
        "content_hash": "news-content-hash-1",
        "title_zh": "英伟达发布新一代人工智能芯片",
        "summary_zh": "公司介绍了新产品，市场关注后续供货与客户采用情况。",
        "headline_summary": "新品发布可能影响半导体供应链预期，但实际影响仍取决于出货。",
        "overall_sentiment": 20,
        "classification": "bullish",
        "confidence": 65,
        "market_relevance": 80,
        "affected_stocks": [
            {
                "ticker": "NVDA",
                "company": "NVIDIA",
                "impact_score": 25,
                "confidence": 70,
                "horizon": "weeks",
                "mechanism": "direct_company",
                "reason": "新品进展可能改变收入预期，但尚缺少实际出货数据。",
            }
        ],
        "affected_sectors": ["半导体"],
        "affected_commodities": [],
        "causal_summary": "产品发布先影响订单预期，再通过产能与交付情况影响业绩判断。",
        "key_factors": ["客户采用速度", "供应链交付能力"],
        "uncertainty_notes": ["新闻没有提供经审计的订单数据。"],
        "insufficient_context": False,
    }


def _market_focus_result() -> dict:
    return {
        "output_language": "zh-CN",
        "cycle_id": "cycle-20260715-01",
        "as_of": "2026-07-15T12:00:00Z",
        "input_hash": "a" * 64,
        "title_zh": "科技股财报与利率预期成为市场焦点",
        "summary_zh": "市场同时关注大型科技股财报和利率路径，短期分歧有所增加。",
        "headline_summary": "财报线索与利率预期交织，市场方向仍需更多数据确认。",
        "market_summary": "现有事件对科技股和成长板块影响较大，但证据尚不足以支持单一方向判断。",
        "dominant_events": [
            {
                "event_group_id": "event-01",
                "summary": "大型科技公司更新财报指引。",
                "affected_sectors": ["信息技术"],
            }
        ],
        "market_uncertainties": ["利率预期仍可能随经济数据改变。"],
        "affected_sectors": ["信息技术"],
        "focus_ticker_assessments": [
            {
                "ticker": "NVDA",
                "catalyst_bias": 15,
                "confidence": 60,
                "horizon": "days",
                "supporting_event_ids": ["event-01"],
                "conflicting_event_ids": [],
                "summary": "财报指引提供支持，但估值与利率变化仍会带来波动。",
                "risks": ["指引不及市场预期。"],
                "insufficient_evidence": False,
            }
        ],
        "no_new_material_catalyst": False,
        "insufficient_context": False,
    }


def _news_payload() -> dict:
    return {
        "news_id": 1,
        "change_sequence": 7,
        "content_hash": "news-content-hash-1",
        "allowed_tickers": ["NVDA"],
        "title": "NVIDIA announces a product update",
    }


def _market_focus_payload(**overrides) -> dict:
    payload = {
        "cycle_id": "cycle-20260715-01",
        "as_of": "2026-07-15T08:00:00-04:00",
        "input_hash": "a" * 64,
        "allowed_event_group_ids": ["event-01"],
        "allowed_tickers": ["NVDA"],
    }
    payload.update(overrides)
    return payload


def _payload_for(job_type: str) -> dict:
    if job_type == "news_impact":
        return _news_payload()
    if job_type == "market_focus":
        return _market_focus_payload()
    return {}


def _assert_strict_objects(schema: dict) -> None:
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(schema.get("properties", {}))
    for value in schema.values():
        if isinstance(value, dict):
            _assert_strict_objects(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _assert_strict_objects(item)


@pytest.mark.parametrize("job_type", tuple(runtime.AI_TASK_MAX_OUTPUT_TOKENS))
def test_every_model_schema_is_strict_and_declares_simplified_chinese(job_type):
    request = runtime.build_runtime_request(job_type, {"title": "untrusted"})
    _assert_strict_objects(request.schema)
    language = request.schema["properties"]["output_language"]
    assert language["const"] == "zh-CN"
    assert "简体中文" in request.instructions
    assert "不可信" in request.instructions
    assert "交易建议" in request.instructions
    assert "目标价" in request.instructions
    assert "仓位" in request.instructions
    assert "止损" in request.instructions
    assert "untrusted_" in request.input_text


def test_news_and_market_focus_results_accept_simplified_chinese():
    news = validate_result(
        "news_impact",
        json.dumps(_news_result(), ensure_ascii=False),
        _news_payload(),
    )
    focus = validate_result(
        "market_focus",
        json.dumps(_market_focus_result(), ensure_ascii=False),
        _market_focus_payload(),
    )
    assert news["title_zh"].startswith("英伟达")
    assert news["affected_stocks"][0]["company"] == "NVIDIA"
    assert focus["cycle_id"] == "cycle-20260715-01"


def test_market_focus_rejects_a_model_rewritten_snapshot_time():
    with pytest.raises(ValueError, match="market_focus_as_of_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(_market_focus_result(), ensure_ascii=False),
            _market_focus_payload(as_of="2026-07-15T13:00:00Z"),
        )


def test_market_focus_requires_a_timezone_aware_as_of():
    result = _market_focus_result()
    result["as_of"] = "2026-07-15T12:00:00"
    with pytest.raises(ValidationError):
        validate_result(
            "market_focus",
            json.dumps(result, ensure_ascii=False),
            _market_focus_payload(),
        )


@pytest.mark.parametrize(
    "title",
    [
        "US stocks rally after earnings beat expectations",
        "美國科技股上漲",
        "蘋果公司推出新品",
        "市場關注聯準會利率與企業財報",
        "臺灣供應鏈營運與庫存壓力升高",
        "該公司預計擴大產能並調整價格",
    ],
)
def test_news_title_rejects_english_prose_and_traditional_chinese(title):
    result = _news_result()
    result["title_zh"] = title
    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    "traditional_text",
    [
        "市場更新",
        "消息彙整",
        "公司發佈財報後，營收較預期成長。",
        "聯準會維持利率不變，市場關注後續聲明。",
        "供應鏈壓力緩解，晶片庫存回歸正常。",
        "臺灣半導體產業受惠於人工智慧需求。",
        "投資人關注監管機構對併購交易的調查。",
        "主要特徵已经出现。",
        "公司著手改善供货。",
        "投资者需要瞭解风险。",
        "天气持续乾旱。",
    ],
)
def test_common_traditional_and_variant_prose_is_rejected(traditional_text):
    with pytest.raises(ValueError, match="traditional_chinese_not_allowed"):
        validate_simplified_chinese_text(traditional_text)


@pytest.mark.parametrize(
    "simplified_text",
    [
        "市场更新",
        "消息汇总",
        "公司发布财报后，收入高于市场预期。",
        "供应链压力缓解，芯片库存逐步恢复正常。",
        "调查显示后续利率变化仍是主要风险。",
        "这是一家著名企业。",
        "行业瞭望认为需求仍会增长。",
        "乾照光电发布业绩。",
        "英伟达（NVIDIA）发布Blackwell芯片",
        "NVIDIA、AMD与TSM关注Blackwell供货",
    ],
)
def test_simplified_prose_and_necessary_foreign_names_are_allowed(simplified_text):
    assert validate_simplified_chinese_text(simplified_text) == simplified_text


def test_wholly_english_sentence_is_rejected():
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        validate_simplified_chinese_text(
            "Markets rally after companies report stronger earnings"
        )


def _replace_nested_value(document: dict, path: tuple[str | int, ...], value: str):
    target = document
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value


@pytest.mark.parametrize(
    "path",
    [
        ("title_zh",),
        ("summary_zh",),
        ("headline_summary",),
        ("affected_stocks", 0, "reason"),
        ("affected_sectors", 0),
        ("affected_commodities", 0, "name"),
        ("affected_commodities", 0, "reason"),
        ("causal_summary",),
        ("key_factors", 0),
        ("uncertainty_notes", 0),
    ],
)
def test_every_news_natural_language_field_uses_the_simplified_gate(path):
    result = _news_result()
    result["affected_commodities"] = [
        {
            "name": "原油",
            "impact_score": 10,
            "reason": "供应变化可能影响短期价格。",
        }
    ]
    _replace_nested_value(result, path, "市場消息彙整")
    with pytest.raises(ValidationError, match="traditional_chinese_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    "path",
    [
        ("title_zh",),
        ("summary_zh",),
        ("headline_summary",),
        ("market_summary",),
        ("dominant_events", 0, "summary"),
        ("dominant_events", 0, "affected_sectors", 0),
        ("market_uncertainties", 0),
        ("affected_sectors", 0),
        ("focus_ticker_assessments", 0, "summary"),
        ("focus_ticker_assessments", 0, "risks", 0),
    ],
)
def test_every_market_focus_natural_language_field_uses_the_simplified_gate(path):
    result = _market_focus_result()
    _replace_nested_value(result, path, "市場消息彙整")
    with pytest.raises(ValidationError, match="traditional_chinese_not_allowed"):
        validate_result(
            "market_focus",
            json.dumps(result, ensure_ascii=False),
            _market_focus_payload(),
        )


def test_chinese_text_allows_tickers_and_necessary_foreign_proper_names():
    result = _news_result()
    result["title_zh"] = "英伟达（NVIDIA）发布Blackwell芯片"
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )
    assert "NVIDIA" in validated["title_zh"]


@pytest.mark.parametrize(
    "title",
    [
        "著名公司发布新品",
        "乾照光电发布业绩公告",
        "覆盘显示芯片需求回升",
        "研究覆盖主要半导体公司",
    ],
)
def test_unihan_self_mapped_characters_remain_valid_in_simplified_contexts(title):
    result = _news_result()
    result["title_zh"] = title
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )
    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    "mixed_prose",
    [
        "Breaking 苹果公司发布新品",
        "Breaking苹果公司发布新品",
        "Update 苹果公司发布新品",
        "苹果公司 reports 新品",
        "英伟达launches新品",
        "新闻称 shares rose after earnings",
        "英伟达 reports strong growth now",
        "NVIDIA launches new chip 新品",
        "英伟达 launches chip",
    ],
)
def test_chinese_text_rejects_english_fragments(mixed_prose):
    result = _news_result()
    result["title_zh"] = mixed_prose
    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_chinese_text_allows_multiple_tickers_and_a_short_proper_name():
    result = _news_result()
    result["title_zh"] = "NVIDIA、AMD与TSM关注Blackwell供货"
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )
    assert validated["title_zh"].startswith("NVIDIA")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("news_id", 2),
        ("change_sequence", 8),
        ("content_hash", "different-content"),
    ],
)
def test_news_result_is_bound_to_the_exact_local_revision(field, value):
    result = _news_result()
    result[field] = value
    with pytest.raises(ValueError, match="news_identity_mismatch"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_news_result_rejects_a_ticker_not_verified_by_local_code():
    result = _news_result()
    result["affected_stocks"][0]["ticker"] = "AAPL"
    with pytest.raises(ValueError, match="news_ticker_binding_mismatch"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_news_result_allows_an_empty_verified_ticker_set():
    result = _news_result()
    result["affected_stocks"] = []
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload() | {"allowed_tickers": []},
    )
    assert validated["affected_stocks"] == []


def test_market_focus_rejects_unlisted_event_ticker_and_input_hash():
    event_result = _market_focus_result()
    event_result["dominant_events"][0]["event_group_id"] = "invented-event"
    with pytest.raises(ValueError, match="market_focus_event_binding_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(event_result, ensure_ascii=False),
            _market_focus_payload(),
        )

    ticker_result = _market_focus_result()
    ticker_result["focus_ticker_assessments"][0]["ticker"] = "AAPL"
    with pytest.raises(ValueError, match="market_focus_ticker_binding_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(ticker_result, ensure_ascii=False),
            _market_focus_payload(),
        )

    hash_result = _market_focus_result()
    hash_result["input_hash"] = "b" * 64
    with pytest.raises(ValueError, match="market_focus_input_hash_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(hash_result, ensure_ascii=False),
            _market_focus_payload(),
        )


def test_runtime_uses_fixed_model_reasoning_background_and_per_task_limits(tmp_path):
    settings = _settings(tmp_path / "ai-jobs.db")
    for job_type, expected_tokens in runtime.AI_TASK_MAX_OUTPUT_TOKENS.items():
        params = runtime._create_params(settings, job_type, _payload_for(job_type))
        assert params["model"] == "gpt-5.6-terra"
        assert params["reasoning"] == {"effort": "max"}
        assert params["background"] is True
        assert params["store"] is True
        assert params["max_output_tokens"] == expected_tokens
        assert params["text"]["format"]["strict"] is True
    assert runtime.max_output_tokens_for("news_impact") == 32_768
    assert runtime.max_output_tokens_for("market_focus") == 49_152


def test_runtime_policy_changes_schema_identity(monkeypatch):
    _, before = runtime.schema_identity("news_impact")
    monkeypatch.setitem(
        runtime.AI_TASK_MAX_OUTPUT_TOKENS,
        "news_impact",
        runtime.max_output_tokens_for("news_impact") + 1,
    )
    _, after = runtime.schema_identity("news_impact")
    assert after != before


def test_daily_paid_submission_limit_is_atomic_and_capped_at_four(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    jobs = [_create_job(repository, ticker) for ticker in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    for index, job in enumerate(jobs[:4]):
        owner = f"owner-{index}"
        claimed = repository.claim_due(owner, 60)
        assert claimed["job_id"] == job["job_id"]
        assert repository.mark_submission_started(
            job["job_id"], owner, daily_limit=100, daily_budget_usd=100
        ) == "started"
        repository.fail(job["job_id"], owner, "provider_failed")

    owner = "owner-limit"
    claimed = repository.claim_due(owner, 60)
    assert claimed["job_id"] == jobs[4]["job_id"]
    assert repository.mark_submission_started(
        jobs[4]["job_id"], owner, daily_limit=100, daily_budget_usd=100
    ) == "daily_limit"
    blocked = repository.get_job(jobs[4]["job_id"])
    assert blocked["status"] == "budget_blocked"
    assert blocked["error_code"] == "daily_job_limit_reached"
    assert blocked["submission_started_at"] is None


def test_global_concurrency_limit_defers_a_second_paid_submission(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")

    first_claim = repository.claim_due("owner-one", 60)
    assert first_claim["job_id"] == first["job_id"]
    assert repository.mark_submission_started(
        first["job_id"], "owner-one", daily_limit=4
    ) == "started"

    second_claim = repository.claim_due("owner-two", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"], "owner-two", daily_limit=4
    ) == "concurrency_limit"
    deferred = repository.get_job(second["job_id"])
    assert deferred["status"] == "pending"
    assert deferred["error_code"] == "global_concurrency_limit"
    assert deferred["submission_started_at"] is None


def test_unknown_submission_holds_the_global_concurrency_slot(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")

    first_claim = repository.claim_due("owner-one", 60)
    assert repository.mark_submission_started(
        first["job_id"], "owner-one", daily_limit=4
    ) == "started"
    repository.fail(
        first_claim["job_id"],
        "owner-one",
        "submission_outcome_unknown",
    )

    second_claim = repository.claim_due("owner-two", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"], "owner-two", daily_limit=4
    ) == "concurrency_limit"
    health = repository.health()
    assert health["submission_unknown"] == 1
    snapshot = repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=2.0,
    )
    assert snapshot["concurrency_available"] is False
    assert snapshot["active_job"]["error_code"] == "submission_outcome_unknown"
    assert repository.get_job(first["job_id"])["budget_charge_microusd"] == (
        runtime.budget_reservation_microusd("earnings_impact")
    )


def test_local_preflight_failure_does_not_consume_budget_or_become_unknown(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row = _create_job(repository, "AAA")
    settings = _settings(tmp_path / "ai-jobs.db")

    def fail_preflight(*_args, **_kwargs):
        raise RuntimeError("ai_sdk_unavailable")

    monkeypatch.setattr(runtime, "prepare_background", fail_preflight)
    owner = "preflight-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    failed = repository.get_job(row["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "ai_sdk_unavailable"
    assert failed["submission_started_at"] is None


def test_explicit_provider_rejection_does_not_create_an_unknown_submission_lock(
    monkeypatch,
    tmp_path,
):
    class ProviderRejected(RuntimeError):
        status_code = 401

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")
    settings = _settings(tmp_path / "ai-jobs.db")

    async def reject_submission(*_args, **_kwargs):
        raise ProviderRejected("authentication rejected")

    monkeypatch.setattr(runtime, "submit_background", reject_submission)
    owner = "provider-rejected-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    failed = repository.get_job(first["job_id"])
    assert failed["error_code"] == "provider_auth_failed"
    assert repository.health()["submission_unknown"] == 0
    second_claim = repository.claim_due("second-owner", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"], "second-owner", daily_limit=4
    ) == "started"


def test_oversized_runtime_input_fails_before_paid_capacity_is_reserved(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")
    row, _ = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAA", "raw_context": "x" * 61_000},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-zh-cn-v3",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    owner = "oversized-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(
        process_job(
            repository,
            _settings(tmp_path / "ai-jobs.db"),
            claimed,
            owner,
        )
    )

    failed = repository.get_job(row["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "ai_input_too_large"
    assert failed["submission_started_at"] is None


def test_public_job_hides_legacy_non_chinese_result(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row = _create_job(repository, "AAA")
    owner = "legacy-owner"
    claimed = repository.claim_due(owner, 60)
    legacy_result = {
        "output_language": "zh-CN",
        "ticker": "AAA",
        "summary": "Markets rally after strong earnings",
        "expectation": "Revenue and margins may rise",
        "impacted": [
            {
                "ticker": ticker,
                "name": ticker,
                "relation": "competitor",
                "direction": "mixed",
                "reason": "Public business relationship may transmit demand",
            }
            for ticker in ("BBB", "CCC", "DDD", "EEE")
        ],
    }
    repository.complete(
        claimed["job_id"],
        owner,
        legacy_result,
        {},
    )

    stored = repository.get_job(row["job_id"])
    assert "Markets rally" in stored["result_json"]
    public = repository.public(stored)
    assert public["result"] is None
    assert public["error_code"] == "legacy_output_hidden"


def test_public_job_hides_new_shape_result_when_legacy_payload_lacks_identity(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("news_impact")
    row, _ = repository.create_job(
        job_type="news_impact",
        payload={"news_id": 1},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="legacy-news",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    owner = "legacy-news-owner"
    claimed = repository.claim_due(owner, 60)
    repository.complete(claimed["job_id"], owner, _news_result(), {})

    stored = repository.get_job(row["job_id"])
    public = repository.public(stored)
    assert public["result"] is None
    assert public["error_code"] == "legacy_output_hidden"


def test_v1_database_migration_preserves_and_disables_sync_history(tmp_path):
    path = tmp_path / "ai-jobs-v1.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ai_jobs (
            job_id TEXT PRIMARY KEY,job_type TEXT NOT NULL,request_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,status TEXT NOT NULL,priority INTEGER NOT NULL,
            model TEXT NOT NULL,reasoning TEXT NOT NULL,execution_mode TEXT NOT NULL,
            prompt_version TEXT NOT NULL,schema_version TEXT NOT NULL,
            schema_sha256 TEXT NOT NULL,openai_response_id TEXT,
            submission_started_at TEXT,submitted_at TEXT,last_polled_at TEXT,
            completed_at TEXT,attempt_count INTEGER NOT NULL,poll_count INTEGER NOT NULL,
            next_attempt_at TEXT,error_code TEXT,result_json TEXT,
            usage_input_tokens INTEGER,usage_cached_input_tokens INTEGER,
            usage_output_tokens INTEGER,usage_reasoning_tokens INTEGER,
            usage_total_tokens INTEGER,cancel_requested_at TEXT,lease_owner TEXT,
            lease_expires_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO ai_jobs(
            job_id,job_type,request_hash,payload_json,status,priority,
            model,reasoning,execution_mode,prompt_version,schema_version,
            schema_sha256,attempt_count,poll_count,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "aij_legacy_sync_123",
            "earnings_impact",
            "legacy-hash",
            "{}",
            "pending",
            50,
            "gpt-5.6-terra",
            "max",
            "worker_sync",
            "earnings-v2",
            "earnings-v2",
            "legacy-digest",
            0,
            0,
            "2026-07-15T00:00:00Z",
            "2026-07-15T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    repository = AIJobRepository(path)
    repository.initialize()
    migrated = repository.get_job("aij_legacy_sync_123")
    assert migrated["status"] == "failed"
    assert migrated["execution_mode"] == "background"
    assert migrated["legacy_execution_mode"] == "worker_sync"
    assert migrated["error_code"] == "legacy_execution_mode_disabled"
    assert migrated["execution_number"] == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            SimpleNamespace(
                status="completed",
                refusal="不能处理",
                output=[],
            ),
            "provider_refusal",
        ),
        (
            SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
            "provider_incomplete_max_output_tokens",
        ),
        (SimpleNamespace(status="failed"), "provider_failed"),
        (SimpleNamespace(status="cancelled"), "provider_cancelled"),
    ],
)
def test_terminal_provider_states_have_explicit_bounded_errors(response, expected):
    assert runtime.response_terminal_error(response) == expected
