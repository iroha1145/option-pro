"""2026-09-25 审计批的回归测试（新闻、焦点周期、热点与 ETL 上下游）。

按审计条目分节：每个分节自带所需数据，不依赖其它分节留下的状态；模块级
import 与 `_ai_` 前缀的准备函数供各分节共用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app import failure_diagnostics
from app.access import request_owner_access_context
from app.personal_config import FeatureConfig, PersonalConfig
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts import personal_service as personal_module
from app.services.catalysts.errors import CatalystError
from app.services.catalysts.etl_client import NewsChangesPage
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from app.services.catalysts.personal_service import PersonalCatalystService


# --- AI-28：ETL 时间戳归一化与日历实际值标题门槛 ---


def test_require_utc_text_normalizes_offsets_to_z_preserving_fraction_width():
    """_require_utc_text 必须把带偏移的时间戳转成以 Z 结尾的 UTC 文本，且
    小数秒位数跟输入保持一致（无小数秒则不补、3 位/6 位原样保留）。"""

    from app.services.catalysts.etl_client import _require_utc_text

    # +00:00：只换后缀，不改时刻，也不产生原本没有的小数秒。
    assert (
        _require_utc_text("2026-09-25T08:00:00+00:00", field="t")
        == "2026-09-25T08:00:00Z"
    )
    # +08:00，3 位小数秒：换算到 UTC 且小数秒位数不变。
    assert (
        _require_utc_text("2026-09-25T08:00:00.123+08:00", field="t")
        == "2026-09-25T00:00:00.123Z"
    )
    # -05:30（非整小时偏移），6 位小数秒。
    assert (
        _require_utc_text("2026-09-25T08:00:00.654321-05:30", field="t")
        == "2026-09-25T13:30:00.654321Z"
    )
    # -05:00，无小数秒。
    assert (
        _require_utc_text("2026-09-25T08:00:00-05:00", field="t")
        == "2026-09-25T13:00:00Z"
    )


def test_require_utc_text_already_z_is_returned_byte_for_byte():
    """已是 Z 的输入必须逐字节原样返回：既有数据/哈希不能因为归一化而抖动。"""

    from app.services.catalysts.etl_client import _require_utc_text

    for value in (
        "2026-09-25T08:00:00Z",
        "2026-09-25T08:00:00.123Z",
        "2026-09-25T08:00:00.123456Z",
    ):
        assert _require_utc_text(value, field="t") == value


def test_require_utc_text_rejects_invalid_or_naive_input():
    from app.services.catalysts.etl_client import _require_utc_text

    with pytest.raises(ValueError, match="must be a timestamp"):
        _require_utc_text("", field="t")
    with pytest.raises(ValueError, match="must be a timestamp"):
        _require_utc_text(None, field="t")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ISO-8601 timestamp"):
        _require_utc_text("not-a-timestamp", field="t")
    with pytest.raises(ValueError, match="must include a timezone"):
        _require_utc_text("2026-09-25T08:00:00", field="t")


def test_offset_timestamp_normalizes_through_the_real_pydantic_parse_path():
    """不只测 _require_utc_text 本身：证明带偏移的时间戳经过 etl_client 的
    真实解析路径（pydantic field_validator）后，模型字段已经是 Z 串——也就是
    下游 local_intelligence 按字典序比较/存库时看到的值。"""

    from app.services.catalysts.etl_client import NewsChange

    change = NewsChange.model_validate(
        {
            "sequence": 1,
            "operation": "delete",
            "changed_at": "2026-09-25T08:00:00.123+08:00",
            "source_updated_at": "2026-09-25T08:00:00+00:00",
            "available_at": "2026-09-25T08:00:00.123456-05:30",
            "news": None,
            "news_id": 1,
        }
    )
    assert change.changed_at == "2026-09-25T00:00:00.123Z"
    assert change.source_updated_at == "2026-09-25T08:00:00Z"
    assert change.available_at == "2026-09-25T13:30:00.123456Z"


def _cpi_mom_event(**overrides: object) -> dict[str, object]:
    """一个「事件标题已经过 local_intelligence._public_calendar_title 预翻译」
    的合成样本：翻译器在识别到 m/m 这类限定词时会保留上游英文原文（见
    economic_calendar_actuals.py 里 AI-28b 分节的说明），所以标题里同时有中
    文类别名和英文原文——这跟生产环境的真实形状一致。"""

    event = {
        "event_id": "cpi-mom",
        "currency": "USD",
        "title": "消费者价格指数 · 环比（Consumer Price Index MoM）",
        "scheduled_at_utc": "2026-07-23T14:00:00Z",
        "forecast": "0.3%",
        "previous": "0.2%",
        "actual": None,
        "release_status": "awaiting_source",
    }
    event.update(overrides)
    return event


def test_numeric_coincidence_with_unrelated_title_is_not_filled():
    """AI-28b：同一时刻、同币种、forecast/previous 都巧合相等，但候选标题跟
    事件毫不相关——不能只凭数值巧合回填。"""

    from datetime import datetime, timezone

    from app.services.catalysts.economic_calendar_actuals import merge_recent_actuals

    payload = {"items": [_cpi_mom_event()]}
    unrelated_candidate = {
        "title": "Building Permits",
        "indicator": "Building Permits",
        "currency": "USD",
        "date": "2026-07-23T14:00:00.000Z",
        "actual": 5.0,
        "forecast": 0.3,
        "previous": 0.2,
        "unit": "%",
    }

    merged, filled, attempted = merge_recent_actuals(
        payload,
        [unrelated_candidate],
        as_of=datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc),
    )

    assert attempted == 1
    assert filled == 0
    assert merged["items"][0]["actual"] is None


def test_related_title_with_different_wording_is_still_filled():
    """AI-28b：候选标题用词不同（CPI m/m 缩写 vs. 事件里保留的 Consumer Price
    Index MoM 原文）但确实是同一个指标——仍然可以回填。"""

    from datetime import datetime, timezone

    from app.services.catalysts.economic_calendar_actuals import merge_recent_actuals

    payload = {"items": [_cpi_mom_event()]}
    related_candidate = {
        "title": "CPI m/m",
        "indicator": "CPI m/m",
        "currency": "USD",
        "date": "2026-07-23T14:00:00.000Z",
        "actual": 0.4,
        "forecast": 0.3,
        "previous": 0.2,
        "unit": "%",
    }

    merged, filled, attempted = merge_recent_actuals(
        payload,
        [related_candidate],
        as_of=datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc),
    )

    assert attempted == 1
    assert filled == 1
    assert merged["items"][0]["actual"] == "0.4%"
    assert merged["items"][0]["actual_source"] == "TradingView Economic Calendar"


def test_unrelated_and_related_candidates_together_pick_the_related_one():
    """AI-28b：数值巧合的无关候选和用词不同的相关候选同时存在时，必须选中
    相关的那个，而不是被无关候选的数值巧合抢先或造成歧义拒绝。"""

    from datetime import datetime, timezone

    from app.services.catalysts.economic_calendar_actuals import merge_recent_actuals

    payload = {"items": [_cpi_mom_event()]}
    unrelated_candidate = {
        "title": "Building Permits",
        "indicator": "Building Permits",
        "currency": "USD",
        "date": "2026-07-23T14:00:00.000Z",
        "actual": 5.0,
        "forecast": 0.3,
        "previous": 0.2,
        "unit": "%",
    }
    related_candidate = {
        "title": "CPI m/m",
        "indicator": "CPI m/m",
        "currency": "USD",
        "date": "2026-07-23T14:00:00.000Z",
        "actual": 0.4,
        "forecast": 0.3,
        "previous": 0.2,
        "unit": "%",
    }

    merged, filled, attempted = merge_recent_actuals(
        payload,
        [unrelated_candidate, related_candidate],
        as_of=datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc),
    )

    assert attempted == 1
    assert filled == 1
    assert merged["items"][0]["actual"] == "0.4%"


def test_different_cadence_of_the_same_release_is_not_treated_as_related():
    """AI-28b 的护栏：同一指标但不同频率（m/m vs y/y）必须继续被当成不同
    数值，不能因为核心词重叠就放行——否则又会退化回纯数值巧合匹配。"""

    from app.services.catalysts.economic_calendar_actuals import _titles_are_related

    assert (
        _titles_are_related(
            "消费者价格指数 · 环比（Consumer Price Index MoM）",
            "CPI y/y",
        )
        is False
    )
    # 防御式检查一致性：反过来也一样。
    assert (
        _titles_are_related(
            "CPI y/y",
            "消费者价格指数 · 环比（Consumer Price Index MoM）",
        )
        is False
    )


# --- 公共准备：AI 上下游（新闻/焦点周期/热点）分节共用 ---


def _ai_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ai_news_change(
    sequence: int,
    news_id: int,
    *,
    available_at: datetime,
    title: str | None = None,
    summary: str | None = None,
    tickers: tuple[str, ...] = ("NVDA",),
    published_at: datetime | None = None,
) -> dict[str, Any]:
    published = published_at or available_at - timedelta(minutes=5)
    return {
        "sequence": sequence,
        "operation": "upsert",
        "changed_at": _ai_iso(available_at),
        "source_updated_at": _ai_iso(available_at),
        "available_at": _ai_iso(available_at),
        "news_id": news_id,
        "news": {
            "id": news_id,
            "source": "Reuters",
            "title": title or f"NVIDIA launches Blackwell platform {news_id}",
            "summary": summary if summary is not None else f"Raw English summary {news_id}",
            "url": f"https://example.test/news/{news_id}/{sequence}",
            "image_url": None,
            "published_at": _ai_iso(published),
            "fetched_at": _ai_iso(available_at),
            "updated_at": _ai_iso(available_at),
            "source_tickers": list(tickers),
            "sources": ["Reuters"],
            "source_count": 1,
            "source_observations": [],
            "content_hash": f"hash-{news_id}-{sequence}",
        },
    }


def _ai_apply_news(
    etl: CatalystEtlRepository,
    changes: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> None:
    state = etl.state("news")
    sequence = max(int(item["sequence"]) for item in changes)
    page = NewsChangesPage.model_validate(
        {
            "items": changes,
            "has_more": False,
            "next_cursor": None,
            "watermark": {"sequence": sequence, "as_of": _ai_iso(as_of)},
            "next_updated_after": _ai_iso(as_of),
            "next_after_sequence": sequence,
        }
    )
    etl.apply_news_page(
        page,
        expected_cursor=state.cursor,
        expected_generation=state.generation,
    )


def _ai_stack(
    tmp_path,
    *,
    mode: str = "manual",
    tickers: tuple[str, ...] = ("NVDA", "AMD"),
    ai_repository: Any | None = None,
    max_queued: int = 200,
) -> tuple[CatalystEtlRepository, Any, LocalCatalystIntelligence]:
    cache_path = tmp_path / "catalyst-cache.db"
    local_module._reset_revision_cache()
    etl = CatalystEtlRepository(cache_path)
    etl.initialize()
    ai = ai_repository
    if ai is None:
        ai = AIJobRepository(tmp_path / "ai-jobs.db")
        ai.initialize()
    intelligence = LocalCatalystIntelligence(
        cache_path,
        ai,
        mode=mode,
        canonical_tickers=tickers,
        max_queued=max_queued,
    )
    intelligence.initialize()
    return etl, ai, intelligence


def _ai_news_result(news_id: int, *, sequence: int = 1) -> dict[str, Any]:
    return {
        "output_language": "zh-CN",
        "news_id": news_id,
        "change_sequence": sequence,
        "content_hash": f"hash-{news_id}-{sequence}",
        "title_zh": "英伟达发布新一代芯片平台",
        "summary_zh": "公司发布新产品，市场关注后续供货与客户采用情况。",
        "headline_summary": "新品发布可能影响半导体供应链预期，实际影响仍需观察。",
        "overall_sentiment": 20,
        "classification": "bullish",
        "confidence": 65,
        "market_relevance": 80,
        "affected_stocks": [
            {
                "ticker": "NVDA",
                "company": "英伟达",
                "impact_score": 25,
                "confidence": 70,
                "horizon": "weeks",
                "mechanism": "direct_company",
                "reason": "新品进展可能改变收入预期，但尚缺少实际出货数据。",
            }
        ],
        "affected_sectors": ["半导体"],
        "affected_commodities": [],
        "causal_summary": "产品发布先影响订单预期，再由产能与交付情况影响业绩判断。",
        "key_factors": ["客户采用速度", "供应链交付能力"],
        "uncertainty_notes": ["新闻没有提供经审计的订单数据。"],
        "insufficient_context": False,
    }


def _ai_finish(repository: AIJobRepository, job_id: str, result: dict[str, Any]) -> None:
    owner = f"test-owner-{job_id}"
    claimed = repository.claim_due(owner, lease_seconds=60)
    assert claimed is not None and claimed["job_id"] == job_id
    assert repository.mark_submission_started(job_id, owner, daily_limit=4) == "started"
    repository.complete(
        job_id,
        owner,
        result,
        {
            "input_tokens": 1,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_tokens": 0,
            "total_tokens": 2,
        },
    )


def _ai_job_ids(repository: AIJobRepository, job_type: str) -> list[str]:
    with sqlite3.connect(repository.path) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT job_id FROM ai_jobs WHERE job_type=? ORDER BY created_at",
                (job_type,),
            ).fetchall()
        ]


@pytest.fixture
def ai_audit_diagnostics(caplog):
    failure_diagnostics._seen.clear()
    caplog.set_level(logging.WARNING, logger=failure_diagnostics.__name__)
    yield caplog
    failure_diagnostics._seen.clear()


def _ai_diagnostic_stages(caplog) -> list[str]:
    return [
        record.getMessage().split("stage=", 1)[1].split(" ", 1)[0]
        for record in caplog.records
        if record.name == failure_diagnostics.__name__
    ]


# --- AI-1：访客读焦点周期同样用写入时的完整 payload 重校验 ---

_AI1_CYCLE_ID = "mfc_" + "a" * 32
_AI1_JOB_ID = "aij_" + "f" * 32
_AI1_INPUT_HASH = "a" * 64
_AI1_AS_OF = "2026-07-15T04:00:00Z"
_AI1_READ_AT = datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc)
_AI1_BOUND_SUMMARY = "据报道，berobenatide的月度给药数据带来减重管线关注。"


class _AiFocusJobs:
    """Only the job store is faked; the catalyst store and engine are real."""

    def __init__(self, rows: dict[str, dict[str, Any]], path) -> None:
        self.rows = rows
        self.path = path

    def initialize(self) -> None:
        raise AssertionError("reading focus cycles must not initialize the AI store")

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)


def _ai1_focus_service(tmp_path, *, event_summary: str):
    schema_version, schema_hash = ai_runtime.schema_identity("market_focus")
    payload = {
        "cycle_id": _AI1_CYCLE_ID,
        "as_of": _AI1_AS_OF,
        "input_hash": _AI1_INPUT_HASH,
        "input_schema_version": "market-focus-input-v2",
        "prepared_revision": 1,
        "allowed_event_group_ids": [],
        "allowed_tickers": [],
        "events": [
            {
                "event_group_id": "evt_x",
                "title_zh": "Pfizer obesity candidate",
                "summary_zh": event_summary,
            }
        ],
        "force": False,
        "cycle_revision": 1,
    }
    result = {
        "output_language": "zh-CN",
        "cycle_id": _AI1_CYCLE_ID,
        "as_of": _AI1_AS_OF,
        "input_hash": _AI1_INPUT_HASH,
        "title_zh": "市场焦点等待进一步确认",
        "summary_zh": _AI1_BOUND_SUMMARY,
        "headline_summary": "市场缺少方向一致的新催化剂。",
        "market_summary": "不同事件相互抵消，暂未形成清晰主线。",
        "dominant_events": [],
        "market_uncertainties": ["后续数据仍可能改变判断"],
        "affected_sectors": [],
        "focus_ticker_assessments": [],
        "no_new_material_catalyst": True,
        "insufficient_context": False,
    }
    jobs = _AiFocusJobs(
        {
            _AI1_JOB_ID: {
                "job_id": _AI1_JOB_ID,
                "job_type": "market_focus",
                "status": "completed",
                "model": local_module.MODEL,
                "reasoning": local_module.REASONING,
                "execution_mode": "background",
                "prompt_version": local_module.FOCUS_PROMPT_VERSION,
                "schema_version": schema_version,
                "schema_sha256": schema_hash,
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "result_json": json.dumps(result, ensure_ascii=False),
            }
        },
        tmp_path / "missing-ai-jobs.db",
    )
    with request_owner_access_context(True):
        _etl, _jobs, engine = _ai_stack(tmp_path, ai_repository=jobs)
        with sqlite3.connect(engine.db_path) as connection:
            connection.execute(
                """INSERT INTO catalyst_local_focus_cycles(
                       cycle_id,status,prepared_revision,snapshot_as_of,input_hash,
                       job_id,payload_json,result_json,created_at,completed_at,
                       updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _AI1_CYCLE_ID,
                    "completed",
                    1,
                    _AI1_AS_OF,
                    _AI1_INPUT_HASH,
                    _AI1_JOB_ID,
                    local_module._json(payload),
                    local_module._json(result),
                    _AI1_AS_OF,
                    "2026-07-15T04:01:00Z",
                    "2026-07-15T04:01:00Z",
                ),
            )
            connection.commit()
    service = PersonalCatalystService(
        SimpleNamespace(
            cache_db_path=str(engine.db_path),
            model=local_module.MODEL,
            reasoning=local_module.REASONING,
        ),
        intelligence=engine,
        ai_repository=jobs,
        personal_config=PersonalConfig(features=FeatureConfig(catalyst_mode="manual")),
        ai_settings=SimpleNamespace(personal_etl_enabled=True),
    )
    return engine, service


@pytest.mark.parametrize("owner", [True, False])
def test_focus_cycle_keeps_source_bound_entities_for_owner_and_visitor(
    tmp_path,
    owner,
):
    engine, service = _ai1_focus_service(
        tmp_path,
        event_summary=(
            "Pfizer's obesity candidate berobenatide showed monthly dosing "
            "potential in a phase 2b study."
        ),
    )

    with request_owner_access_context(owner):
        raw = engine.latest_market_focus_cycle(now=_AI1_READ_AT)
        projected = service.latest_market_focus_cycle(now=_AI1_READ_AT)

    raw_cycle = raw["latest_successful_cycle"]
    # The visitor projection drops job_id; the write-time payload travels
    # with the cycle instead, so both audiences validate the same context.
    assert ("job_id" in raw_cycle) is owner
    assert raw_cycle["_validation_payload"]["events"][0]["event_group_id"] == "evt_x"
    cycle = projected["latest_successful_cycle"]
    assert cycle is not None
    assert cycle["result"]["summary_zh"] == _AI1_BOUND_SUMMARY
    assert "_validation_payload" not in cycle
    if not owner:
        assert "job_id" not in cycle
        # The private context carries source-language event text.
        assert "Pfizer" not in json.dumps(cycle, ensure_ascii=False)


def test_visitor_focus_cycle_still_hides_entities_the_payload_never_named(tmp_path):
    _engine, service = _ai1_focus_service(
        tmp_path,
        event_summary="An obesity candidate showed monthly dosing potential.",
    )

    with request_owner_access_context(False):
        projected = service.latest_market_focus_cycle(now=_AI1_READ_AT)

    assert projected["latest_successful_cycle"] is None


# --- AI-8：超大新闻按字节预算截断入队，单条入队失败不拖垮整轮 ---


def test_oversized_news_is_truncated_and_does_not_block_the_round(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    oversized = "Nvidia filing digest <b>detail</b>. " * 2_000
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
        _ai_apply_news(
            etl,
            [
                _ai_news_change(1, 1, available_at=now - timedelta(hours=2)),
                _ai_news_change(
                    2,
                    2,
                    available_at=now - timedelta(hours=1),
                    summary=oversized,
                ),
            ],
            as_of=now - timedelta(minutes=30),
        )
        engine.reconcile()
        outcome = engine.run_scheduled(now=now)

        assert outcome == {"queued": 2, "skipped": 0}
        payloads = {
            payload["news_id"]: payload
            for payload in (
                json.loads(ai.get_job(job_id)["payload_json"])
                for job_id in _ai_job_ids(ai, "news_impact")
            )
        }
        assert set(payloads) == {1, 2}
        assert "truncated_fields" not in payloads[1]
        bounded = payloads[2]
        assert bounded["truncated_fields"] == ["summary"]
        assert oversized.startswith(bounded["summary"])
        assert (
            local_module._untrusted_json_bytes(bounded["summary"])
            <= local_module.NEWS_SUMMARY_MAX_BYTES
        )
        # The bounded payload also passes the runtime's submission gate.
        ai_runtime._bounded_untrusted_json(bounded)
        with sqlite3.connect(engine.db_path) as connection:
            connection.row_factory = sqlite3.Row
            revision = connection.execute(
                """SELECT * FROM catalyst_local_news_revisions
                   WHERE news_id=2"""
            ).fetchone()
            linked = {
                int(row["news_id"])
                for row in connection.execute(
                    "SELECT news_id FROM catalyst_local_analysis_links"
                ).fetchall()
            }
        # The bounded payload still binds to its revision: the job is linked.
        assert engine._news_payload_matches_revision(bounded, revision)
        assert linked == {1, 2}
        # A marked field must be a strict prefix of this revision's own text.
        assert not engine._news_payload_matches_revision(
            {**bounded, "summary": "Unrelated text"},
            revision,
        )
        assert not engine._news_payload_matches_revision(
            {**bounded, "summary": oversized},
            revision,
        )


def test_ticker_hint_budget_keeps_whole_leading_items():
    hints = [f"TICKER-{index:04d}-" + "X" * 80 for index in range(500)]
    fields = local_module._news_request_source_fields(
        {"raw_summary": "short summary", "source_tickers_json": json.dumps(hints)}
    )

    kept = fields["source_ticker_hints"]
    assert fields["truncated_fields"] == ["source_ticker_hints"]
    assert fields["summary"] == "short summary"
    assert kept == hints[: len(kept)] and 0 < len(kept) < len(hints)
    assert (
        local_module._untrusted_json_bytes(kept)
        <= local_module.NEWS_TICKER_HINTS_MAX_BYTES
    )
    normal = local_module._news_request_source_fields(
        {"raw_summary": "short summary", "source_tickers_json": '["NVDA"]'}
    )
    assert normal == {"summary": "short summary", "source_ticker_hints": ["NVDA"]}


def test_scheduled_round_skips_a_candidate_the_job_store_rejects(
    tmp_path,
    monkeypatch,
    ai_audit_diagnostics,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
        _ai_apply_news(
            etl,
            [
                _ai_news_change(1, 11, available_at=now - timedelta(hours=2)),
                _ai_news_change(
                    2,
                    12,
                    available_at=now - timedelta(hours=1),
                    title="AMD wins a large cloud contract",
                    tickers=("AMD",),
                ),
            ],
            as_of=now - timedelta(minutes=30),
        )
        engine.reconcile()
        original_create = ai.create_job

        def reject_news_12(**kwargs):
            if kwargs["payload"].get("news_id") == 12:
                raise ValueError("ai_job_payload_too_large")
            return original_create(**kwargs)

        monkeypatch.setattr(ai, "create_job", reject_news_12)
        outcome = engine.run_scheduled(now=now)

        assert outcome == {"queued": 1, "skipped": 1}
        assert [
            json.loads(ai.get_job(job_id)["payload_json"])["news_id"]
            for job_id in _ai_job_ids(ai, "news_impact")
        ] == [11]
    assert "catalyst_scheduled_payload_too_large" in _ai_diagnostic_stages(
        ai_audit_diagnostics
    )


# --- AI-9：热点排序分在每次计划时重算，事件组版本只承载身份与内容 ---


def _ai9_two_plans(tmp_path, monkeypatch):
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=60)
    clock = {"now": t0}
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock["now"])
    etl, ai, engine = _ai_stack(tmp_path)
    _ai_apply_news(
        etl,
        [
            _ai_news_change(
                1,
                1,
                available_at=t0 - timedelta(minutes=5),
                title="Nvidia unveils new accelerator roadmap",
                tickers=("NVDA",),
            )
        ],
        as_of=t0,
    )
    first = engine.reconcile()["prepared_revision"]
    t1 = t0 + timedelta(hours=59)
    clock["now"] = t1
    _ai_apply_news(
        etl,
        [
            _ai_news_change(
                2,
                2,
                available_at=t1 - timedelta(minutes=5),
                title="AMD wins large cloud contract",
                tickers=("AMD",),
            )
        ],
        as_of=t1,
    )
    second = engine.reconcile()["prepared_revision"]
    return ai, engine, first, second, t1


def test_hotspot_rank_recomputes_recency_at_each_plan(tmp_path, monkeypatch):
    with request_owner_access_context(True):
        _ai, engine, first, second, t1 = _ai9_two_plans(tmp_path, monkeypatch)
        items = engine.hotspots(limit=10, now=t1)["items"]
        with sqlite3.connect(engine.db_path) as connection:
            story_a_versions = connection.execute(
                """SELECT COUNT(*) FROM catalyst_local_event_groups
                   WHERE representative_news_id=1"""
            ).fetchone()[0]

    assert second > first
    assert [item["representative_news_id"] for item in items] == [2, 1]
    fresh, stale = items
    assert fresh["component_scores"]["recency"] > 99
    assert "发布时间较近" in fresh["reasons"]
    # 59 hours old at plan 2: recency has fully decayed (it was ~100 when the
    # group first appeared) and no longer explains the rank.
    assert stale["component_scores"]["recency"] == 0.0
    assert stale["hot_score"] == 25.93
    assert "发布时间较近" not in stale["reasons"]
    # The group's identity and content did not change: no new version.
    assert story_a_versions == 1


def test_hotspot_strip_and_focus_input_share_the_plan_ranking(tmp_path, monkeypatch):
    with request_owner_access_context(True):
        ai, engine, _first, second, t1 = _ai9_two_plans(tmp_path, monkeypatch)
        strip = engine.hotspots(limit=20, now=t1)["items"]
        cycle = engine.request_market_focus_cycle(
            expected_prepared_revision=second,
            as_of=t1,
        )
        payload = json.loads(ai.get_job(cycle["job_id"])["payload_json"])

    events = [event for event in payload["events"] if event.get("event_type") != "calendar"]
    assert [event["event_group_id"] for event in events] == [
        item["event_group_id"] for item in strip
    ]
    assert [event["hot_score"] for event in events] == [
        item["hot_score"] for item in strip
    ]


def test_time_alone_does_not_open_a_new_hotspot_revision(tmp_path, monkeypatch):
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=10)
    clock = {"now": start}
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock["now"])
    with request_owner_access_context(True):
        etl, _ai, engine = _ai_stack(tmp_path)
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 21, available_at=start - timedelta(minutes=5))],
            as_of=start,
        )
        first = engine.reconcile()["prepared_revision"]
        clock["now"] = start + timedelta(hours=5)
        second = engine.reconcile()["prepared_revision"]

    # A time-driven revision would look like "new hotspots" and re-open the
    # hourly paid focus cycle although no news changed.
    assert second == first


def test_v5_store_gains_plan_score_columns_and_old_items_fall_back(tmp_path):
    cache_path = tmp_path / "catalyst-cache.db"
    CatalystEtlRepository(cache_path).initialize()
    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            """CREATE TABLE catalyst_local_hotspot_items (
                   prepared_revision INTEGER NOT NULL
                     REFERENCES catalyst_local_hotspot_revisions(prepared_revision),
                   ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
                   event_group_id TEXT NOT NULL,
                   event_group_version INTEGER NOT NULL,
                   PRIMARY KEY(prepared_revision,ordinal),
                   UNIQUE(prepared_revision,event_group_id),
                   FOREIGN KEY(event_group_id,event_group_version)
                     REFERENCES catalyst_local_event_groups(
                       event_group_id,event_group_version)
               )"""
        )
        connection.execute(
            """CREATE TABLE catalyst_local_schema (
                   version TEXT PRIMARY KEY,
                   checksum TEXT NOT NULL,
                   applied_at TEXT NOT NULL
               )"""
        )
        connection.execute(
            """INSERT INTO catalyst_local_schema VALUES(
                   'optix-local-catalyst-v5','v5-checksum','2026-09-01T00:00:00Z')"""
        )
        connection.commit()
    engine = LocalCatalystIntelligence(
        cache_path,
        AIJobRepository(tmp_path / "ai-jobs.db"),
        mode="manual",
        canonical_tickers=("NVDA",),
    )
    with request_owner_access_context(True):
        engine.initialize()
        engine.initialize()
        with sqlite3.connect(cache_path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(catalyst_local_hotspot_items)"
                ).fetchall()
            }
            versions = dict(
                connection.execute(
                    "SELECT version,checksum FROM catalyst_local_schema"
                ).fetchall()
            )
            prepared_at = "2026-09-01T00:00:00Z"
            connection.execute(
                """INSERT INTO catalyst_local_event_groups(
                       event_group_id,event_group_version,input_hash,event_type,
                       representative_news_id,representative_change_sequence,
                       representative_content_hash,representative_title_zh,
                       representative_summary_zh,first_published_at,
                       last_published_at,available_at,source_count,
                       source_names_json,validated_tickers_json,
                       news_identities_json,hot_score,component_scores_json,
                       reasons_json,created_at
                   ) VALUES('evt_old',1,?,'earnings',1,1,'h','英伟达发布新品',
                            '市场关注后续进展',?,?,?,1,'[]','["NVDA"]','[]',
                            61.5,'{"source_breadth":48.0}','["单一来源"]',?)""",
                ("b" * 64, prepared_at, prepared_at, prepared_at, prepared_at),
            )
            connection.execute(
                """INSERT INTO catalyst_local_hotspot_revisions(
                       prepared_revision,input_hash,prepared_at,data_through,
                       item_count
                   ) VALUES(1,?,?,?,1)""",
                ("c" * 64, prepared_at, prepared_at),
            )
            connection.execute(
                """INSERT INTO catalyst_local_hotspot_items(
                       prepared_revision,ordinal,event_group_id,
                       event_group_version
                   ) VALUES(1,1,'evt_old',1)""",
            )
            connection.commit()
        items = engine.hotspots(
            limit=5,
            now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )["items"]

    assert {"hot_score", "component_scores_json", "reasons_json"} <= columns
    assert versions["optix-local-catalyst-v5"] == "v5-checksum"
    assert versions[local_module.SCHEMA_VERSION] == local_module.SCHEMA_CHECKSUM
    # Items written before the upgrade keep the score stored with their group.
    assert items[0]["hot_score"] == 61.5
    assert items[0]["reasons"] == ["单一来源"]
    # Preparing the local store never touches the AI job store (AI-10).
    assert not (tmp_path / "ai-jobs.db").exists()


# --- AI-10（本地层）：AI 任务库不可用只暂停分析读写，不挡新闻摄入与热点 ---


class _UnavailableAIStore:
    """A job store whose schema check fails and whose file is unreadable."""

    def __init__(self, path) -> None:
        self.path = path
        self.initialize_calls = 0

    def initialize(self) -> None:
        self.initialize_calls += 1
        raise RuntimeError("ai_job_schema_checksum_mismatch")


def test_unavailable_ai_store_pauses_analysis_but_not_ingestion(
    tmp_path,
    ai_audit_diagnostics,
):
    store_path = tmp_path / "ai-jobs.db"
    store_path.write_bytes(b"not a sqlite database " * 64)
    store = _UnavailableAIStore(store_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, _store, engine = _ai_stack(tmp_path, ai_repository=store)
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 31, available_at=now - timedelta(minutes=10))],
            as_of=now - timedelta(minutes=9),
        )
        outcome = engine.reconcile()
        with sqlite3.connect(engine.db_path) as connection:
            connection.execute(
                """INSERT INTO catalyst_local_analysis_links(
                       news_id,change_sequence,content_hash,job_id,created_at
                   ) VALUES(31,1,'hash-31-1','aij_unreadable',?)""",
                (_ai_iso(now - timedelta(minutes=5)),),
            )
            connection.commit()
        local_module._reset_revision_cache()
        feed = engine.feed(window_hours=72, limit=10, as_of=now)
        hotspots = engine.hotspots(limit=5, now=now)

    assert store.initialize_calls == 0
    assert outcome["ingested"] == 1
    assert outcome["hotspots"] == 1
    assert outcome["analysis_store_available"] is False
    assert [item["news_id"] for item in feed["items"]] == [31]
    assert feed["items"][0]["analysis_status"] == "not_requested"
    assert [item["representative_news_id"] for item in hotspots["items"]] == [31]
    stages = _ai_diagnostic_stages(ai_audit_diagnostics)
    assert "catalyst_reconcile_ai_jobs" in stages
    assert "catalyst_feed_ai_jobs" in stages


# --- AI-12：reconcile 只读保留期内任务，先查修订再校验，校验不占写锁 ---


def _ai12_insert_completed_news_jobs(
    repository: AIJobRepository,
    rows: list[tuple[str, int, str]],
) -> None:
    schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
    with sqlite3.connect(repository.path) as connection:
        for job_id, news_id, created_at in rows:
            payload = {
                "news_id": news_id,
                "change_sequence": 1,
                "content_hash": f"hash-{news_id}-1",
                "source": "Reuters",
                "title": "Chip maker posts results",
                "summary": "Revenue rose while demand risk remains.",
                "url": f"https://example.test/{news_id}",
                "published_at": created_at,
                "fetched_at": created_at,
                "sources": ["Reuters"],
                "source_count": 1,
                "source_ticker_hints": ["NVDA"],
                "allowed_tickers": ["NVDA"],
                "analysis_revision": 1,
            }
            connection.execute(
                """INSERT INTO ai_jobs(job_id,job_type,request_hash,payload_json,
                       status,priority,model,reasoning,execution_mode,
                       prompt_version,schema_version,schema_sha256,result_json,
                       created_at,updated_at,completed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    "news_impact",
                    hashlib.sha256(job_id.encode()).hexdigest(),
                    json.dumps(payload),
                    "completed",
                    70,
                    local_module.MODEL,
                    local_module.REASONING,
                    "background",
                    local_module.NEWS_PROMPT_VERSION,
                    schema_version,
                    schema_hash,
                    json.dumps(_ai_news_result(news_id), ensure_ascii=False),
                    created_at,
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                """INSERT INTO ai_job_sources(job_id,submission_source,created_at)
                   VALUES(?,?,?)""",
                (job_id, "scheduled", created_at),
            )
        connection.commit()


def _ai12_write_lock_free(db_path) -> bool:
    probe = sqlite3.connect(db_path, timeout=0)
    try:
        probe.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return False
    else:
        probe.rollback()
        return True
    finally:
        probe.close()


def test_reconcile_validates_only_retained_jobs_with_revisions_outside_the_lock(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path)
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 41, available_at=now - timedelta(minutes=10))],
            as_of=now - timedelta(minutes=9),
        )
        engine.reconcile()
        job = engine.request_analysis(41, force=False)
        _ai_finish(ai, job["job_id"], _ai_news_result(41))
        engine.reconcile()
        with sqlite3.connect(engine.db_path) as connection:
            # A link lost after the paid job completed must be recovered.
            connection.execute(
                "DELETE FROM catalyst_local_analysis_links WHERE job_id=?",
                (job["job_id"],),
            )
            connection.commit()
        outside_retention = [
            (f"aij_old_{index:028d}", 5_000 + index, _ai_iso(now - timedelta(days=40)))
            for index in range(25)
        ]
        pruned_revision = [
            (f"aij_prn_{index:028d}", 6_000 + index, _ai_iso(now - timedelta(hours=1)))
            for index in range(25)
        ]
        _ai12_insert_completed_news_jobs(ai, outside_retention + pruned_revision)
        snapshot, available = engine._reconcile_ai_job_snapshot()
        validated: list[tuple[str, bool]] = []
        original_public = AIJobRepository.public

        def counting_public(row, *, cached=False):
            validated.append(
                (str(row.get("job_id")), _ai12_write_lock_free(engine.db_path))
            )
            return original_public(row, cached=cached)

        monkeypatch.setattr(AIJobRepository, "public", staticmethod(counting_public))
        outcome = engine.reconcile()

    assert available is True
    assert not any(job_id.startswith("aij_old_") for job_id in snapshot)
    assert sum(job_id.startswith("aij_prn_") for job_id in snapshot) == 25
    # Neither history outside retention nor jobs whose revision was pruned
    # are re-validated on every reconcile.
    assert not [
        job_id
        for job_id, _lock_free in validated
        if job_id.startswith(("aij_old_", "aij_prn_"))
    ]
    assert outcome["analysis_links_recovered"] == 1
    recovered_checks = [
        lock_free for job_id, lock_free in validated if job_id == job["job_id"]
    ]
    # The recovery validation runs before reconcile takes the write lock.
    assert recovered_checks and recovered_checks[0] is True


def test_reconcile_job_window_follows_the_configured_news_retention(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        _etl, ai, engine = _ai_stack(tmp_path)
        _ai12_insert_completed_news_jobs(
            ai,
            [("aij_" + "6" * 32, 7_001, _ai_iso(now - timedelta(days=60)))],
        )
        default_window, _available = engine._reconcile_ai_job_snapshot()
        engine.prune_journal(retention_days=90, now=now)
        widened_window, _available = engine._reconcile_ai_job_snapshot()

    assert default_window == {}
    assert list(widened_window) == ["aij_" + "6" * 32]


# --- AI-13：定时候选按修订索引任务快照，解析次数随候选线性增长 ---


def test_scheduled_round_parses_each_job_payload_a_bounded_number_of_times(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    candidates_total = 40
    schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
        _ai_apply_news(
            etl,
            [
                _ai_news_change(
                    index,
                    100 + index,
                    available_at=now - timedelta(minutes=index),
                    title=f"Headline {index} about chips and demand",
                )
                for index in range(1, candidates_total + 1)
            ],
            as_of=now,
        )
        engine.reconcile()
        rows = engine._scheduled_news_candidates(now=now, limit=10_000)
        assert len(rows) == candidates_total
        created_at = _ai_iso(now - timedelta(hours=1))
        with sqlite3.connect(ai.path) as connection:
            for row in rows:
                payload = {
                    "news_id": int(row["news_id"]),
                    "change_sequence": int(row["change_sequence"]),
                    "content_hash": str(row["content_hash"]),
                    "source": str(row["source"]),
                    "title": str(row["raw_title"]),
                    "url": str(row["url"]),
                    "published_at": row.get("published_at"),
                    "fetched_at": row.get("fetched_at"),
                    "sources": list(row.get("source_names") or []),
                    "source_count": int(row.get("source_count") or 1),
                    "allowed_tickers": list(row.get("canonical_tickers") or []),
                    "analysis_revision": 1,
                    **local_module._news_request_source_fields(row),
                }
                job_id = "aij_" + hashlib.sha256(
                    str(row["news_id"]).encode()
                ).hexdigest()[:32]
                connection.execute(
                    """INSERT INTO ai_jobs(job_id,job_type,request_hash,payload_json,
                           status,priority,model,reasoning,execution_mode,
                           prompt_version,schema_version,schema_sha256,error_code,
                           created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        "news_impact",
                        hashlib.sha256(job_id.encode()).hexdigest(),
                        json.dumps(payload),
                        "failed",
                        70,
                        local_module.MODEL,
                        local_module.REASONING,
                        "background",
                        local_module.NEWS_PROMPT_VERSION,
                        schema_version,
                        schema_hash,
                        "schema_validation_failed",
                        created_at,
                        created_at,
                    ),
                )
                connection.execute(
                    """INSERT INTO ai_job_sources(job_id,submission_source,created_at)
                       VALUES(?,?,?)""",
                    (job_id, "scheduled", created_at),
                )
            connection.commit()
        parses = {"count": 0}
        original_payload = LocalCatalystIntelligence._job_payload

        def counting_payload(row):
            parses["count"] += 1
            return original_payload(row)

        monkeypatch.setattr(
            LocalCatalystIntelligence,
            "_job_payload",
            staticmethod(counting_payload),
        )
        outcome = engine.run_scheduled(now=now)

    # Every candidate is a permanent failure, so nothing is queued.
    assert outcome["queued"] == 0
    # Indexing parses each payload once and each candidate re-reads only its
    # own jobs; the old per-candidate scan needed candidates x jobs parses.
    assert parses["count"] <= 3 * candidates_total


# --- AI-14：过期的焦点意图可以用新意图重建 ---


def _ai14_expired_focus_intent(tmp_path, monkeypatch):
    """Return a stack whose prepared revision has one expired, unpaid intent."""

    start = datetime.now(timezone.utc).replace(microsecond=0)
    clock = {"now": start}
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock["now"])
    etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
    _ai_apply_news(
        etl,
        [_ai_news_change(1, 76, available_at=start - timedelta(minutes=10))],
        as_of=start - timedelta(minutes=9),
    )
    engine.reconcile()
    assert engine.run_scheduled(now=start) == {"queued": 1, "skipped": 0}
    (news_job_id,) = _ai_job_ids(ai, "news_impact")
    _ai_finish(ai, news_job_id, _ai_news_result(76))
    clock["now"] = start + timedelta(minutes=1)
    prepared = engine.reconcile()["prepared_revision"]

    def transient_store_failure(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    original_create = engine._create_focus_job
    monkeypatch.setattr(engine, "_create_focus_job", transient_store_failure)
    with pytest.raises(sqlite3.OperationalError):
        engine.request_market_focus_cycle(
            expected_prepared_revision=prepared,
            as_of=clock["now"],
            submission_source="scheduled",
        )
    monkeypatch.setattr(engine, "_create_focus_job", original_create)
    with sqlite3.connect(engine.db_path) as connection:
        (expired_id,) = connection.execute(
            "SELECT cycle_id FROM catalyst_local_focus_cycles"
        ).fetchone()
    return ai, engine, clock, prepared, expired_id


def _ai14_cycles(engine) -> dict[str, tuple[str, str | None, str]]:
    with sqlite3.connect(engine.db_path) as connection:
        return {
            str(row[0]): (str(row[1]), row[2], str(row[3]))
            for row in connection.execute(
                """SELECT cycle_id,status,error_code,job_id
                   FROM catalyst_local_focus_cycles"""
            ).fetchall()
        }


def test_scheduled_run_rebuilds_an_expired_focus_intent(tmp_path, monkeypatch):
    with request_owner_access_context(True):
        ai, engine, clock, prepared, expired_id = _ai14_expired_focus_intent(
            tmp_path,
            monkeypatch,
        )
        clock["now"] += timedelta(minutes=12)
        engine.reconcile()
        assert _ai14_cycles(engine)[expired_id][:2] == (
            "failed",
            local_module.FOCUS_PREPARE_EXPIRED_ERROR,
        )
        clock["now"] += timedelta(minutes=1)
        outcome = engine.run_scheduled(now=clock["now"])
        cycles = _ai14_cycles(engine)

    assert outcome == {"queued": 1, "skipped": 0}
    rebuilt = {
        cycle_id: state for cycle_id, state in cycles.items() if cycle_id != expired_id
    }
    assert len(rebuilt) == 1
    (status, error_code, job_id), = rebuilt.values()
    assert status == "pending" and error_code is None
    assert _ai_job_ids(ai, "market_focus") == [job_id]


def test_manual_request_rebuilds_instead_of_returning_the_expired_intent(
    tmp_path,
    monkeypatch,
):
    with request_owner_access_context(True):
        ai, engine, clock, prepared, expired_id = _ai14_expired_focus_intent(
            tmp_path,
            monkeypatch,
        )
        clock["now"] += timedelta(minutes=12)
        engine.reconcile()
        cycle = engine.request_market_focus_cycle(
            expected_prepared_revision=prepared,
            as_of=clock["now"],
        )

    assert cycle["cycle_id"] != expired_id
    assert cycle["status"] == "pending"
    assert _ai_job_ids(ai, "market_focus") == [cycle["job_id"]]


def test_expired_intent_relinks_the_paid_job_it_already_owns(tmp_path, monkeypatch):
    with request_owner_access_context(True):
        ai, engine, clock, prepared, expired_id = _ai14_expired_focus_intent(
            tmp_path,
            monkeypatch,
        )
        with sqlite3.connect(engine.db_path) as connection:
            (payload_json,) = connection.execute(
                "SELECT payload_json FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                (expired_id,),
            ).fetchone()
        # The paid job was created, but the local relink never committed.
        orphan, _created = engine._create_focus_job(
            json.loads(payload_json),
            submission_source="scheduled",
        )
        clock["now"] += timedelta(minutes=12)
        engine.reconcile()
        relinked = _ai14_cycles(engine)[expired_id]
        clock["now"] += timedelta(minutes=1)
        outcome = engine.run_scheduled(now=clock["now"])

    assert relinked == ("pending", None, orphan["job_id"])
    assert outcome["queued"] == 0
    assert _ai_job_ids(ai, "market_focus") == [orphan["job_id"]]


# --- AI-27：本地计数与公开层可见条目同一口径；batch 只返回有中文的条目 ---


def _ai27_service(engine) -> PersonalCatalystService:
    return PersonalCatalystService(
        SimpleNamespace(
            cache_db_path=str(engine.db_path),
            model=local_module.MODEL,
            reasoning=local_module.REASONING,
        ),
        intelligence=engine,
        ai_repository=engine.ai_repository,
        personal_config=PersonalConfig(features=FeatureConfig(catalyst_mode="manual")),
        ai_settings=SimpleNamespace(personal_etl_enabled=True),
    )


def _ai27_published_news(tmp_path, news_ids: tuple[int, ...]):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    etl, ai, engine = _ai_stack(tmp_path)
    _ai_apply_news(
        etl,
        [
            _ai_news_change(
                index,
                news_id,
                available_at=now - timedelta(minutes=30 - index),
                title=f"Unrelated English headline number {news_id} about markets",
            )
            for index, news_id in enumerate(news_ids, start=1)
        ],
        as_of=now - timedelta(minutes=20),
    )
    engine.reconcile()
    return etl, ai, engine, now


def test_counts_follow_the_analyses_the_public_boundary_shows(tmp_path, monkeypatch):
    with request_owner_access_context(True):
        _etl, ai, engine, now = _ai27_published_news(tmp_path, (51,))
        job = engine.request_analysis(51, force=False)
        _ai_finish(ai, job["job_id"], _ai_news_result(51))
        engine.reconcile()
        engine.reconcile()

    # A later contract rejects the stored paid result in both layers.
    def reject_news(job_type, raw_json, payload):
        if job_type == "news_impact":
            raise ValueError("future_language_style_rejected")
        raise AssertionError(job_type)

    monkeypatch.setattr(
        local_module,
        "NEWS_RESULT_CONTRACT_ID",
        "news-impact-result:simplified-chinese-v99:test",
    )
    monkeypatch.setattr(local_module, "validate_result", reject_news)
    monkeypatch.setattr(personal_module, "validate_result", reject_news)
    local_module._reset_revision_cache()
    read_at = now + timedelta(minutes=1)
    with request_owner_access_context(False):
        local_feed = engine.feed(window_hours=72, limit=10, as_of=read_at)
        local_batch = engine.batch(
            ["NVDA"],
            window_hours=72,
            limit=5,
            include_neutral=True,
            as_of=read_at,
        )
        public_feed = _ai27_service(engine).feed(
            window_hours=72,
            limit=10,
            as_of=read_at,
        )

    # The paid bytes stay attached locally (never discarded) ...
    assert local_feed["items"][0]["analysis"]["title_zh"] == "英伟达发布新一代芯片平台"
    assert local_feed["items"][0]["_analysis_current"] is False
    # ... but counts describe what readers can see.
    assert local_feed["summary"]["analyzed_count"] == 0
    assert local_feed["summary"]["bullish"] == 0
    assert local_feed["summary"]["pending"] == 1
    ticker_summary = local_batch["results"]["NVDA"]["summary"]
    assert ticker_summary["analyzed_count"] == 0
    assert ticker_summary["bullish"] == 0
    assert ticker_summary["pending"] == 1
    assert public_feed["summary"]["analyzed_count"] == sum(
        1 for item in public_feed["items"] if item.get("analysis")
    )


def test_batch_returns_only_items_with_chinese_copy(tmp_path):
    with request_owner_access_context(True):
        _etl, ai, engine, now = _ai27_published_news(tmp_path, (61, 62))
        job = engine.request_analysis(62, force=False)
        _ai_finish(ai, job["job_id"], _ai_news_result(62, sequence=2))
        engine.reconcile()
    local_module._reset_revision_cache()
    with request_owner_access_context(False):
        payload = _ai27_service(engine).batch(
            ["NVDA"],
            window_hours=72,
            limit=5,
            include_neutral=True,
            as_of=now + timedelta(minutes=1),
        )

    result = payload["results"]["NVDA"]
    assert [item["news_id"] for item in result["items"]] == [62]
    assert all(item["title_zh"] for item in result["items"])
    assert result["hidden_unanalyzed"] == 1
    assert result["summary"]["pending"] == 1
    assert "_analysis_current" not in result["items"][0]


# --- AI-29（焦点部分）：写事务里再核对修订仍是最新 ---


def test_focus_enqueue_rechecks_the_revision_under_its_write_lock(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path)
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 81, available_at=now - timedelta(minutes=30))],
            as_of=now - timedelta(minutes=29),
        )
        first = engine.reconcile()["prepared_revision"]
        stale_status = engine.hotspot_status(now=datetime.now(timezone.utc))
        assert stale_status["prepared_revision"] == first
        _ai_apply_news(
            etl,
            [
                _ai_news_change(
                    2,
                    82,
                    available_at=now - timedelta(minutes=10),
                    title="AMD wins large cloud contract",
                    tickers=("AMD",),
                )
            ],
            as_of=now - timedelta(minutes=9),
        )
        second = engine.reconcile()["prepared_revision"]
        read_at = datetime.now(timezone.utc)
        # The caller read the revision before reconcile committed a newer one.
        monkeypatch.setattr(engine, "hotspot_status", lambda *, now=None: stale_status)
        with pytest.raises(CatalystError) as rejected:
            engine.request_market_focus_cycle(
                expected_prepared_revision=first,
                as_of=read_at,
            )
        cycles = _ai14_cycles(engine)

    assert second > first
    assert rejected.value.code == "prepared_revision_changed"
    assert cycles == {}
    assert _ai_job_ids(ai, "market_focus") == []


# --- 安全清理：宏观块失败留诊断；公开层复用本地层的常量 ---


def test_macro_context_failure_is_recorded(monkeypatch, ai_audit_diagnostics):
    import app.config

    def settings_unavailable():
        raise OSError("settings store unavailable")

    monkeypatch.setattr(app.config, "get_settings", settings_unavailable)

    assert local_module.macro_conditions_context() is None
    assert _ai_diagnostic_stages(ai_audit_diagnostics) == ["catalyst_macro_context"]


def test_personal_service_shares_the_local_status_vocabulary():
    assert personal_module._WAITING_TITLE is local_module.TITLE_WAITING
    assert personal_module._WAITING_SUMMARY is local_module.SUMMARY_WAITING
    assert personal_module._WAITING_HOTSPOT_TITLE is local_module.HOTSPOT_WAITING
    assert personal_module._UNANALYZED_STATUSES is local_module._UNANALYZED_STATUSES


# --- 协调补充：付费后的 runtime_configuration_changed 不自动重做；手动新闻留额度 ---


def _ai_focus_result_for(repository: AIJobRepository, job_id: str) -> dict[str, Any]:
    payload = json.loads(repository.get_job(job_id)["payload_json"])
    return {
        "output_language": "zh-CN",
        "cycle_id": payload["cycle_id"],
        "as_of": payload["as_of"],
        "input_hash": payload["input_hash"],
        "title_zh": "市场热点综合分析",
        "summary_zh": "当前公开信息不足以形成新的确定方向。",
        "headline_summary": "热点证据已整理，方向仍需后续数据确认。",
        "market_summary": "当前热点信息有限，暂不形成方向判断。",
        "dominant_events": [],
        "market_uncertainties": ["后续数据仍可能改变市场判断。"],
        "affected_sectors": [],
        "focus_ticker_assessments": [],
        "no_new_material_catalyst": True,
        "insufficient_context": True,
    }


def test_scheduled_focus_never_redoes_a_paid_cycle_retired_for_configuration_change(
    tmp_path,
    monkeypatch,
):
    start = datetime.now(timezone.utc).replace(microsecond=0)
    clock = {"now": start}
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock["now"])
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 91, available_at=start - timedelta(minutes=10))],
            as_of=start - timedelta(minutes=9),
        )
        engine.reconcile()
        assert engine.run_scheduled(now=start)["queued"] == 1
        (news_job_id,) = _ai_job_ids(ai, "news_impact")
        _ai_finish(ai, news_job_id, _ai_news_result(91))
        clock["now"] = start + timedelta(minutes=1)
        engine.reconcile()
        clock["now"] = start + timedelta(minutes=2)
        assert engine.run_scheduled(now=clock["now"])["queued"] == 1
        (focus_job_id,) = _ai_job_ids(ai, "market_focus")
        _ai_finish(ai, focus_job_id, _ai_focus_result_for(ai, focus_job_id))
        # The paid result's runtime identity moves before it is published, so
        # the publisher retires the cycle as runtime_configuration_changed.
        with sqlite3.connect(ai.path) as connection:
            connection.execute(
                "UPDATE ai_jobs SET prompt_version=? WHERE job_id=?",
                ("market-focus-legacy-v1", focus_job_id),
            )
            connection.commit()
        clock["now"] = start + timedelta(minutes=3)
        engine.reconcile()
        with sqlite3.connect(engine.db_path) as connection:
            retired = connection.execute(
                """SELECT status,error_code FROM catalyst_local_focus_cycles
                   WHERE job_id=?""",
                (focus_job_id,),
            ).fetchone()
        clock["now"] = start + timedelta(hours=2)
        outcome = engine.run_scheduled(now=clock["now"])

    assert retired == ("failed", "runtime_configuration_changed")
    assert "runtime_configuration_changed" in ai_runtime.SCHEDULED_TRANSIENT_AI_ERRORS
    assert outcome["queued"] == 0
    assert _ai_job_ids(ai, "market_focus") == [focus_job_id]


def test_scheduled_news_never_retries_a_submitted_configuration_change(tmp_path):
    start = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 93, available_at=start - timedelta(minutes=10))],
            as_of=start - timedelta(minutes=9),
        )
        engine.reconcile()
        assert engine.run_scheduled(now=start)["queued"] == 1
        (news_job_id,) = _ai_job_ids(ai, "news_impact")
        with sqlite3.connect(ai.path) as connection:
            connection.execute(
                """UPDATE ai_jobs SET status='failed',
                       error_code='runtime_configuration_changed',
                       openai_response_id='resp_paid_once',updated_at=?
                   WHERE job_id=?""",
                (_ai_iso(start), news_job_id),
            )
            connection.commit()
        outcome = engine.run_scheduled(now=start + timedelta(hours=2))

    assert outcome["queued"] == 0
    assert _ai_job_ids(ai, "news_impact") == [news_job_id]


def test_manual_news_request_keeps_its_reserve_above_a_full_queue(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled", max_queued=1)
        _ai_apply_news(
            etl,
            [
                _ai_news_change(
                    index,
                    94 + index,
                    available_at=now - timedelta(minutes=10 + index),
                    title=f"Separate headline {index} about a distinct event",
                    tickers=(("NVDA", "AMD", "NVDA")[index - 1],),
                )
                for index in range(1, 4)
            ],
            as_of=now - timedelta(minutes=5),
        )
        engine.reconcile()
        first = engine.request_analysis(95, force=False)
        # The base ceiling (1) is reached; an owner's click keeps its reserve.
        second = engine.request_analysis(96, force=False)
        with pytest.raises(RuntimeError, match="ai_job_queue_full"):
            engine.request_analysis(
                97,
                force=False,
                submission_source="scheduled",
            )

    assert first["status"] == "pending" and second["status"] == "pending"
    assert len(_ai_job_ids(ai, "news_impact")) == 2


# --- 独立审查补充：旧载荷绑定、过期意图付费任务、锁内修订核对、状态口径 ---


def test_job_enqueued_before_the_byte_budget_still_binds_and_is_not_rebought(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    summary = ("Nvidia supplier update sentence number filler text. " * 900)[:45_000]
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path, mode="scheduled")
        _ai_apply_news(
            etl,
            [
                _ai_news_change(
                    1,
                    7,
                    available_at=now - timedelta(hours=1),
                    summary=summary,
                )
            ],
            as_of=now - timedelta(minutes=30),
        )
        engine.reconcile()
        row = engine._current_revision(7, now=now)
        # The verbatim payload the pre-budget code built: it fit the
        # submission gate, so it may already have been paid for.
        legacy_payload = {
            "news_id": 7,
            "change_sequence": 1,
            "content_hash": row["content_hash"],
            "source": str(row["source"]),
            "title": str(row["raw_title"]),
            "summary": row["raw_summary"],
            "url": str(row["url"]),
            "published_at": row.get("published_at"),
            "fetched_at": row.get("fetched_at"),
            "sources": list(row.get("source_names") or []),
            "source_count": int(row.get("source_count") or 1),
            "source_ticker_hints": json.loads(row["source_tickers_json"]),
            "allowed_tickers": list(row.get("canonical_tickers") or []),
            "analysis_revision": 1,
        }
        ai_runtime._bounded_untrusted_json(legacy_payload)
        schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
        legacy_job, _created = ai.create_job(
            job_type="news_impact",
            payload=legacy_payload,
            model=engine.model,
            reasoning=engine.reasoning,
            execution_mode="background",
            prompt_version=local_module.NEWS_PROMPT_VERSION,
            schema_version=schema_version,
            schema_sha256=schema_hash,
            max_queued=engine.max_queued,
            submission_source="scheduled",
            priority=70,
        )
        _ai_finish(ai, legacy_job["job_id"], _ai_news_result(7))
        engine.reconcile()
        with sqlite3.connect(engine.db_path) as connection:
            published = connection.execute(
                """SELECT result_json IS NOT NULL
                   FROM catalyst_local_analysis_links WHERE job_id=?""",
                (legacy_job["job_id"],),
            ).fetchone()
        engine.run_scheduled(now=now + timedelta(hours=1))

    assert local_module._untrusted_json_bytes(summary) > local_module.NEWS_SUMMARY_MAX_BYTES
    assert published == (1,)
    # The paid verbatim job counts for its revision: no second news job.
    assert _ai_job_ids(ai, "news_impact") == [legacy_job["job_id"]]


def test_request_resumes_an_expired_intent_that_already_owns_a_paid_job(
    tmp_path,
    monkeypatch,
):
    with request_owner_access_context(True):
        ai, engine, clock, prepared, expired_id = _ai14_expired_focus_intent(
            tmp_path,
            monkeypatch,
        )
        with sqlite3.connect(engine.db_path) as connection:
            (payload_json,) = connection.execute(
                "SELECT payload_json FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                (expired_id,),
            ).fetchone()
        # The paid job exists but its local link never committed, and no
        # reconcile ran before the intent's preparing window ran out.
        orphan, _created = engine._create_focus_job(
            json.loads(payload_json),
            submission_source="manual",
        )
        clock["now"] += timedelta(minutes=12)
        cycle = engine.request_market_focus_cycle(
            expected_prepared_revision=prepared,
            as_of=clock["now"],
        )
        engine.reconcile()
        cycles = _ai14_cycles(engine)

    assert cycle["cycle_id"] == expired_id
    assert cycle["job_id"] == orphan["job_id"]
    assert list(cycles) == [expired_id]
    assert _ai_job_ids(ai, "market_focus") == [orphan["job_id"]]


def test_revision_committed_after_the_request_clock_still_blocks_minting(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with request_owner_access_context(True):
        etl, ai, engine = _ai_stack(tmp_path)
        _ai_apply_news(
            etl,
            [_ai_news_change(1, 81, available_at=now - timedelta(minutes=30))],
            as_of=now - timedelta(minutes=29),
        )
        first = engine.reconcile()["prepared_revision"]
        newer: dict[str, int] = {}

        def reconcile_while_building_input():
            # A reconcile commits a newer revision after this request read its
            # clock and before it takes the write lock.
            _ai_apply_news(
                etl,
                [
                    _ai_news_change(
                        2,
                        82,
                        available_at=now - timedelta(minutes=10),
                        title="AMD wins large cloud contract",
                        tickers=("AMD",),
                    )
                ],
                as_of=now - timedelta(minutes=9),
            )
            newer["revision"] = engine.reconcile()["prepared_revision"]
            return None

        monkeypatch.setattr(
            local_module,
            "macro_conditions_context",
            reconcile_while_building_input,
        )
        with pytest.raises(CatalystError) as rejected:
            engine.request_market_focus_cycle(expected_prepared_revision=first)
        cycles = _ai14_cycles(engine)

    assert newer["revision"] > first
    assert rejected.value.code == "prepared_revision_changed"
    assert cycles == {}
    assert _ai_job_ids(ai, "market_focus") == []


def test_status_filters_follow_the_public_status_of_a_hidden_result(
    tmp_path,
    monkeypatch,
):
    with request_owner_access_context(True):
        _etl, ai, engine, now = _ai27_published_news(tmp_path, (51,))
        job = engine.request_analysis(51, force=False)
        _ai_finish(ai, job["job_id"], _ai_news_result(51))
        engine.reconcile()
        engine.reconcile()

    def reject_news(job_type, raw_json, payload):
        raise ValueError("future_language_style_rejected")

    monkeypatch.setattr(
        local_module,
        "NEWS_RESULT_CONTRACT_ID",
        "news-impact-result:simplified-chinese-v99:test",
    )
    monkeypatch.setattr(local_module, "validate_result", reject_news)
    monkeypatch.setattr(personal_module, "validate_result", reject_news)
    local_module._reset_revision_cache()
    read_at = now + timedelta(minutes=1)
    with request_owner_access_context(False):
        local_item = engine.feed(window_hours=72, limit=10, as_of=read_at)["items"][0]
        unanalyzed = engine.feed(
            window_hours=72,
            limit=10,
            as_of=read_at,
            analysis_status="not_requested",
        )
        completed = engine.feed(
            window_hours=72,
            limit=10,
            as_of=read_at,
            analysis_status="completed",
        )

    assert local_item["analysis_status"] == "pending"
    assert local_item["analysis_error_code"] == "legacy_output_hidden"
    assert unanalyzed["summary"]["count"] == 1
    assert unanalyzed["summary"]["pending"] == 1
    assert completed["summary"]["count"] == 0
    assert completed["items"] == []


def test_hotspot_reads_fall_back_before_the_worker_adds_plan_scores(tmp_path):
    with request_owner_access_context(True):
        _etl, _ai, engine = _ai_stack(tmp_path)
        prepared_at = "2026-09-01T00:00:00Z"
        with sqlite3.connect(engine.db_path) as connection:
            connection.execute("DROP TABLE catalyst_local_hotspot_items")
            connection.execute(
                """CREATE TABLE catalyst_local_hotspot_items (
                       prepared_revision INTEGER NOT NULL,
                       ordinal INTEGER NOT NULL,
                       event_group_id TEXT NOT NULL,
                       event_group_version INTEGER NOT NULL,
                       PRIMARY KEY(prepared_revision,ordinal)
                   )"""
            )
            connection.execute(
                """INSERT INTO catalyst_local_event_groups(
                       event_group_id,event_group_version,input_hash,event_type,
                       representative_news_id,representative_change_sequence,
                       representative_content_hash,representative_title_zh,
                       representative_summary_zh,first_published_at,
                       last_published_at,available_at,source_count,
                       source_names_json,validated_tickers_json,
                       news_identities_json,hot_score,component_scores_json,
                       reasons_json,created_at
                   ) VALUES('evt_web',1,?,'earnings',1,1,'h','英伟达发布新品',
                            '市场关注后续进展',?,?,?,1,'[]','["NVDA"]','[]',
                            57.0,'{}','[]',?)""",
                ("d" * 64, prepared_at, prepared_at, prepared_at, prepared_at),
            )
            connection.execute(
                """INSERT INTO catalyst_local_hotspot_revisions(
                       prepared_revision,input_hash,prepared_at,data_through,
                       item_count
                   ) VALUES(1,?,?,?,1)""",
                ("e" * 64, prepared_at, prepared_at),
            )
            connection.execute(
                """INSERT INTO catalyst_local_hotspot_items
                   VALUES(1,1,'evt_web',1)"""
            )
            connection.commit()
    # The web process never runs initialize(); it may start before the worker
    # upgrades the schema and must still read hotspots.
    with request_owner_access_context(False):
        items = engine.hotspots(
            limit=5,
            now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )["items"]
        revision_row, focus_items = engine._hotspots_for_revision(1, limit=20)

    assert [item["hot_score"] for item in items] == [57.0]
    assert revision_row is not None
    assert [item["hot_score"] for item in focus_items] == [57.0]
