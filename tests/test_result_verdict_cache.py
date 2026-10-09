"""The full result check runs once per (validator, job type, result bytes, payload)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import models as ai_job_models
from app.services.ai_jobs.models import validate_result, validate_result_cached
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import personal_service as personal_module
from test_ai_jobs import _create_earnings_job, _earnings_result
from test_catalysts_audit_2026_09_25 import (
    _ai27_published_news,
    _ai27_service,
    _ai_finish,
    _ai_news_result,
)

_PAYLOAD = {"ticker": "AAPL", "name": "Apple"}


def _counting(calls: list[str]):
    def counted(job_type, raw_json, payload):
        calls.append(job_type)
        return validate_result(job_type, raw_json, payload)

    return counted


def test_an_accepted_verdict_is_reused_and_handed_out_as_a_fresh_copy() -> None:
    calls: list[str] = []
    validator = _counting(calls)
    raw = json.dumps(_earnings_result(), ensure_ascii=False)

    first = validate_result_cached("earnings_impact", raw, _PAYLOAD, validator=validator)
    first["summary"] = "调用方改了自己的副本"
    second = validate_result_cached("earnings_impact", raw, _PAYLOAD, validator=validator)

    assert calls == ["earnings_impact"]
    assert second == validate_result("earnings_impact", raw, _PAYLOAD)


def test_a_rejection_is_reused_with_the_same_error() -> None:
    calls: list[str] = []
    validator = _counting(calls)
    rejected = _earnings_result()
    rejected["summary"] = "Markets rally after strong earnings"
    raw = json.dumps(rejected, ensure_ascii=False)

    errors = []
    for _attempt in range(2):
        with pytest.raises(ValueError) as caught:
            validate_result_cached("earnings_impact", raw, _PAYLOAD, validator=validator)
        errors.append((type(caught.value), str(caught.value)))

    assert calls == ["earnings_impact"]
    assert errors[0] == errors[1]


def test_new_bytes_payload_or_validator_run_the_check_again() -> None:
    calls: list[str] = []
    validator = _counting(calls)
    raw = json.dumps(_earnings_result(), ensure_ascii=False)
    edited = _earnings_result()
    edited["summary"] = "供应链与大型科技股可能出现更强联动。"

    validate_result_cached("earnings_impact", raw, _PAYLOAD, validator=validator)
    validate_result_cached(
        "earnings_impact", json.dumps(edited, ensure_ascii=False), _PAYLOAD, validator=validator,
    )
    validate_result_cached(
        "earnings_impact", raw, {**_PAYLOAD, "name": "Apple Inc."}, validator=validator,
    )
    assert len(calls) == 3

    other: list[str] = []
    validate_result_cached("earnings_impact", raw, _PAYLOAD, validator=_counting(other))
    assert other == ["earnings_impact"]


def test_the_cache_keeps_only_the_most_recent_verdicts(monkeypatch) -> None:
    monkeypatch.setattr(ai_job_models, "_RESULT_VERDICT_LIMIT", 2)
    calls: list[str] = []
    validator = _counting(calls)
    raws = []
    for index in range(3):
        result = _earnings_result()
        result["expectation"] = f"关注营收、利润率与第{index + 1}项指引。"
        raws.append(json.dumps(result, ensure_ascii=False))
        validate_result_cached("earnings_impact", raws[-1], _PAYLOAD, validator=validator)

    validate_result_cached("earnings_impact", raws[2], _PAYLOAD, validator=validator)
    validate_result_cached("earnings_impact", raws[0], _PAYLOAD, validator=validator)

    assert len(calls) == 4
    assert len(ai_job_models._result_verdicts) == 2


def test_a_payload_that_cannot_be_keyed_is_checked_every_time() -> None:
    calls: list[str] = []
    validator = _counting(calls)
    raw = json.dumps(_earnings_result(), ensure_ascii=False)
    payload = {**_PAYLOAD, "unkeyable": {1, 2}}

    for _attempt in range(2):
        validate_result_cached("earnings_impact", raw, payload, validator=validator)

    assert calls == ["earnings_impact", "earnings_impact"]


def test_repeated_job_reads_run_the_full_check_once(monkeypatch, tmp_path) -> None:
    from app.services.ai_jobs import repository as repo_module

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    owner = "verdict-cache-owner"
    claimed = repository.claim_due(owner, 60)
    repository.complete(claimed["job_id"], owner, _earnings_result(), {})
    calls: list[str] = []
    monkeypatch.setattr(repo_module, "validate_result", _counting(calls))

    stored = repository.get_job(row["job_id"])
    first = repository.public(stored)
    second = repository.public(stored)

    assert first["result"] == second["result"]
    assert first["result"]["ticker"] == "AAPL"
    assert calls == ["earnings_impact"]


def test_repeated_public_feed_reads_run_the_full_check_once(tmp_path, monkeypatch) -> None:
    with request_owner_access_context(True):
        _etl, ai, engine, now = _ai27_published_news(tmp_path, (55,))
        job = engine.request_analysis(55, force=False)
        _ai_finish(ai, job["job_id"], _ai_news_result(55))
        engine.reconcile()
        engine.reconcile()
    calls: list[str] = []
    monkeypatch.setattr(personal_module, "validate_result", _counting(calls))
    service = _ai27_service(engine)

    feeds = []
    for _attempt in range(2):
        with request_owner_access_context(False):
            feeds.append(
                service.feed(window_hours=72, limit=10, as_of=now + timedelta(minutes=1))
            )

    assert [feed["items"][0]["analysis"]["title_zh"] for feed in feeds] == [
        "英伟达发布新一代芯片平台",
    ] * 2
    assert calls == ["news_impact"]
