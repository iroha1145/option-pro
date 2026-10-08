from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.market_brief import errors
from app.services.market_brief.evidence import BENCHMARK_CODES, EvidencePack
from app.services.market_brief.runner import BriefRunConfig, run_brief, validate_result
from app.services.market_brief.store import BriefStore

NOW = datetime(2026, 10, 9, 3, 30, tzinfo=timezone.utc)
TRADING_DATE = date(2026, 10, 8)
SAMPLE_RESULT = json.loads(
    (Path(__file__).parent / "fixtures" / "market_brief_sample.json").read_text(encoding="utf-8")
)["brief"]["result"]
EVIDENCE_IDS = frozenset({
    "idx:^GSPC", "idx:^IXIC", "sig:rsp_spy_5d", "sig:sectors_above_50dma", "sig:yield_10y_20d_change",
    "sig:credit_risk", "regime:market", "macro:composite", "theme:semiconductors", "theme:energy",
    "news:9f3c2b7e1a", "hot:evt_2c91",
})


def make_pack(*, missing: tuple[str, ...] = ()) -> EvidencePack:
    coverage = {
        "universe_size": 5894,
        "scored_count": 4410,
        "quotes_valid": 5,
        "breadth_basis": "sector_etf_proxy_11",
        "data_through": {"indices": "2026-10-08T20:05:12Z"},
        "missing_blocks": [{"block": block, "reason": errors.SNAPSHOT_MISSING} for block in missing],
        "trimmed": [],
        "evidence_bytes": 1234,
    }
    return EvidencePack(
        payload={"version": "market-brief-evidence-v1", "coverage": coverage, "indices": {"rows": []}},
        bytes=1234,
        coverage=coverage,
        allowed_codes=frozenset({"MU", "WDC"}) | BENCHMARK_CODES,
        evidence_ids=EVIDENCE_IDS,
        source_texts=("Micron said contract prices rose",),
        data_through={"indices": "2026-10-08T20:05:12Z"},
    )


def reply(result: Any, *, stop_reason: str = "end_turn", output_tokens: int = 4000, stop_details: Any = None) -> SimpleNamespace:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return SimpleNamespace(
        id="msg_1",
        model="claude-opus-5-5",
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=20_000,
            output_tokens=output_tokens,
            cache_creation_input_tokens=3_000,
            cache_read_input_tokens=0,
            cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=3_000, ephemeral_5m_input_tokens=0),
            server_tool_use=SimpleNamespace(web_search_requests=2, web_fetch_requests=0),
        ),
    )


class Client:
    def __init__(self, *messages: Any) -> None:
        self.queue = list(messages)
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.queue.pop(0)

        class _Stream:
            def __enter__(self_inner) -> Any:
                return self_inner

            def __exit__(self_inner, *exc: Any) -> None:
                return None

            def get_final_message(self_inner) -> Any:
                return outcome

        return _Stream()


def run(store: BriefStore, client: Client | None, pack: EvidencePack | None = None, **overrides: Any) -> Any:
    factory_calls: list[tuple[str, float]] = []

    def factory(api_key: str, timeout: float) -> Client:
        factory_calls.append((api_key, timeout))
        assert client is not None
        return client

    record = run_brief(
        slot=overrides.pop("slot", "post_close"),
        trading_date=TRADING_DATE,
        trigger=overrides.pop("trigger", "scheduled"),
        store=store,
        config=overrides.pop("config", BriefRunConfig()),
        api_key="sk-ant-test",
        now=NOW,
        client_factory=factory,
        evidence_builder=overrides.pop("evidence_builder", lambda **kwargs: pack or make_pack()),
        **overrides,
    )
    return record, factory_calls


def test_successful_run_is_validated_and_stored(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    client = Client(reply(SAMPLE_RESULT))
    seen_requests: list[dict[str, Any]] = []
    record, factory_calls = run(store, client, on_request=seen_requests.append)

    assert record.status == "completed" and record.error_code is None
    assert record.result == SAMPLE_RESULT
    assert record.validation_warnings == ()
    assert record.run_id.startswith("mb_20261008_post_close_")
    assert record.started_at == NOW and record.completed_at >= NOW
    assert record.duration_seconds is not None and record.duration_seconds >= 0
    assert record.usage["output_tokens"] == 4000 and record.usage["web_search_requests"] == 2
    # 20K 输入×$4 + 3K 一小时缓存写×$8 + 4K 输出×$20 + 2 次搜索×$0.01
    assert record.cost_microusd == 80_000 + 24_000 + 80_000 + 20_000
    assert record.continuation_count == 0
    assert factory_calls == [("sk-ant-test", 1500.0)]
    # 单次请求超时取「整次运行剩余预算」与 request_timeout_seconds 的较小值。
    assert 0 < client.calls[0]["timeout"] <= 1500.0
    assert seen_requests and seen_requests[0]["model"] == "claude-opus-5-5"
    assert seen_requests[0]["evidence_bytes"] == 1234
    assert seen_requests[0]["prompt_version"] == "market-brief-prompt-v1"
    assert record.request_meta == seen_requests[0]
    user_text = client.calls[0]["messages"][0]["content"][0]["text"]
    assert "<untrusted_market_evidence>" in user_text and "收盘后" in user_text
    assert "MU、" in user_text or "、MU" in user_text  # allowed_codes 列在用户消息里
    system_text = client.calls[0]["system"][0]["text"]
    assert "2026" not in system_text  # 系统提示词不含日期，缓存前缀稳定
    assert store.latest_record() == record
    public = store.latest_public(now=NOW)
    assert public["status"] == "ok" and public["brief"]["result"] == SAMPLE_RESULT


def test_english_list_item_is_dropped_with_a_warning(tmp_path: Path) -> None:
    result = copy.deepcopy(SAMPLE_RESULT)
    result["key_news"].insert(1, {
        "evidence_id": "news:9f3c2b7e1a",
        "title_zh": "Fed holds rates steady as expected",
        "what_is_new": "利率决议与预期一致。",
        "priced_in": "yes",
        "tickers": [],
    })
    record, _ = run(BriefStore(tmp_path), Client(reply(result)))
    assert record.status == "completed"
    assert record.result["key_news"] == SAMPLE_RESULT["key_news"]
    assert len(record.validation_warnings) == 1
    assert record.validation_warnings[0].startswith("removed key_news[1]:")
    assert record.raw_output_text == json.dumps(result, ensure_ascii=False)


def test_scalar_failure_fails_the_run_and_keeps_the_raw_output(tmp_path: Path) -> None:
    result = copy.deepcopy(SAMPLE_RESULT)
    result["headline"] = "Megacaps carry the index while breadth lags"
    raw = json.dumps(result, ensure_ascii=False)
    store = BriefStore(tmp_path)
    record, _ = run(store, Client(reply(raw)))
    assert record.status == "failed"
    assert record.error_code == errors.SCHEMA_VALIDATION_FAILED
    assert "headline" in (record.error_detail or "")
    assert record.raw_output_text == raw
    assert record.cost_microusd is not None and record.cost_microusd > 0
    assert store.latest_public(now=NOW)["latest_attempt"]["error_code"] == errors.SCHEMA_VALIDATION_FAILED


def test_reference_check_drops_unknown_ids_and_codes(tmp_path: Path) -> None:
    result = copy.deepcopy(SAMPLE_RESULT)
    result["internals"]["evidence_ids"].append("idx:^RUT")
    result["sectors"][0]["evidence_ids"].append("theme:made_up")
    result["key_news"][0]["tickers"].append("TSLA")
    result["key_news"].append({
        "evidence_id": "news:unknown",
        "title_zh": "某条证据包里没有的新闻",
        "what_is_new": "没有出处。",
        "priced_in": "unclear",
        "tickers": [],
    })
    record, _ = run(BriefStore(tmp_path), Client(reply(result)))
    assert record.status == "completed"
    assert record.result["internals"]["evidence_ids"] == SAMPLE_RESULT["internals"]["evidence_ids"]
    assert record.result["sectors"][0]["evidence_ids"] == ["theme:semiconductors"]
    assert record.result["key_news"][0]["tickers"] == ["MU", "WDC"]
    assert len(record.result["key_news"]) == 2
    warnings = "\n".join(record.validation_warnings)
    assert "idx:^RUT" in warnings and "theme:made_up" in warnings and "TSLA" in warnings
    assert "removed key_news[2]: unknown evidence id news:unknown" in warnings


def test_list_overflow_is_truncated() -> None:
    result = copy.deepcopy(SAMPLE_RESULT)
    result["sectors"] = [copy.deepcopy(SAMPLE_RESULT["sectors"][0]) for _ in range(8)]
    validation = validate_result(json.dumps(result, ensure_ascii=False), make_pack())
    assert validation.result is not None and len(validation.result["sectors"]) == 6
    assert validation.warnings == ("truncated sectors to 6 items",)


def test_nested_repairs_apply_inner_lists_before_outer_removals() -> None:
    result = copy.deepcopy(SAMPLE_RESULT)
    result["key_news"][0]["title_zh"] = "English only headline"  # 整条删
    result["key_news"][1]["tickers"] = ["MU", "WDC", "SPY", "QQQ", "IWM", "RSP", "DIA"]  # 7 个，截到 6 个
    validation = validate_result(json.dumps(result, ensure_ascii=False), make_pack())
    assert validation.result is not None
    assert [item["evidence_id"] for item in validation.result["key_news"]] == ["hot:evt_2c91"]
    assert validation.result["key_news"][0]["tickers"] == ["MU", "WDC", "SPY", "QQQ", "IWM", "RSP"]


def test_all_removable_failures_are_repaired_in_one_round() -> None:
    result = copy.deepcopy(SAMPLE_RESULT)
    result["internals"]["points"] = ["English point"] * 5
    result["invalidators"] = ["English invalidator"]
    validation = validate_result(json.dumps(result, ensure_ascii=False), make_pack())
    assert validation.result is not None
    assert validation.result["internals"]["points"] == [] and validation.result["invalidators"] == []
    assert len(validation.warnings) == 6


def test_soft_removal_stops_after_five_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # 校验器每轮都报列表项失败：修到第五轮仍不过就判失败，不会无限循环。
    from pydantic import ValidationError

    from app.services.market_brief import runner

    attempts: list[str] = []

    class AlwaysFailing:
        @staticmethod
        def model_validate_json(text: str, *, context: Any) -> Any:
            attempts.append(text)
            raise ValidationError.from_exception_data(
                "MarketBriefResult",
                [{"type": "value_error", "loc": ("invalidators", 0), "input": "x", "ctx": {"error": ValueError("english_prose_not_allowed")}}],
            )

    monkeypatch.setattr(runner, "MarketBriefResult", AlwaysFailing)
    result = copy.deepcopy(SAMPLE_RESULT)
    result["invalidators"] = [f"第{index}条反证" for index in range(10)]
    validation = validate_result(json.dumps(result, ensure_ascii=False), make_pack())
    assert validation.result is None
    assert len(attempts) == 6  # 首次校验 + 5 轮修复后的重验
    assert len(validation.warnings) == 5
    assert "invalidators[0]" in (validation.error_detail or "")


def test_evidence_unavailable_skips_the_request(tmp_path: Path) -> None:
    pack = make_pack(missing=("indices", "market_signals", "market_regime"))
    record, factory_calls = run(BriefStore(tmp_path), None, pack=pack)
    assert record.status == "failed" and record.error_code == errors.EVIDENCE_UNAVAILABLE
    assert factory_calls == []
    assert record.coverage["missing_blocks"][0]["block"] == "indices"
    # 只缺指数时照常请求。
    partial, calls = run(BriefStore(tmp_path / "b"), Client(reply(SAMPLE_RESULT)), pack=make_pack(missing=("indices", "market_signals")))
    assert partial.status == "completed" and len(calls) == 1


def test_provider_refusal_is_recorded_with_usage(tmp_path: Path) -> None:
    details = SimpleNamespace(type="refusal", category="bio", explanation=None)
    record, _ = run(BriefStore(tmp_path), Client(reply("", stop_reason="refusal", stop_details=details)))
    assert record.status == "failed" and record.error_code == errors.PROVIDER_REFUSAL
    assert "category=bio" in (record.error_detail or "")
    assert record.usage["input_tokens"] == 20_000 and record.cost_microusd and record.cost_microusd > 0


def test_program_errors_are_recorded_then_raised(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)

    def broken_builder(**kwargs: Any) -> EvidencePack:
        raise RuntimeError("bug in evidence")

    with pytest.raises(RuntimeError, match="bug in evidence"):
        run(store, None, evidence_builder=broken_builder)
    failed = store.latest_record(status="failed")
    assert failed is not None and failed.error_code == errors.RUNTIME_ERROR
    assert failed.error_detail == "RuntimeError"


def test_argument_checks(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    with pytest.raises(ValueError, match="anthropic_api_key_missing"):
        run_brief(slot="pre_open", trading_date=TRADING_DATE, trigger="manual", store=store, config=BriefRunConfig(), api_key="", now=NOW)
    with pytest.raises(ValueError):
        run_brief(slot="midday", trading_date=TRADING_DATE, trigger="manual", store=store, config=BriefRunConfig(), api_key="k", now=NOW)  # type: ignore[arg-type]
    assert store.history() == []


def test_manual_trigger_and_config_pass_through(tmp_path: Path) -> None:
    config = replace(BriefRunConfig(), effort="high", request_timeout_seconds=600.0)
    client = Client(reply(SAMPLE_RESULT))
    record, factory_calls = run(BriefStore(tmp_path), client, config=config, trigger="manual", slot="pre_open")
    assert record.trigger == "manual" and record.slot == "pre_open" and record.effort == "high"
    assert factory_calls == [("sk-ant-test", 600.0)]
    assert client.calls[0]["output_config"]["effort"] == "high"
    assert "开盘前" in client.calls[0]["messages"][0]["content"][0]["text"]
