from __future__ import annotations

import json
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.data_paths import get_data_paths
from app.services.market_brief.store import (
    PUBLIC_COVERAGE_KEYS,
    RETAINED_RUNS,
    BriefRunRecord,
    BriefStore,
    new_run_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "market_brief_sample.json"
SAMPLE = json.loads(FIXTURE.read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 9, 3, 41, 12, tzinfo=timezone.utc)
# 与 evidence.py 产出同形的证据包；样例研判引用的主题、指数与宏观分项正好画出样例里的 visuals。
# 其余条目（非美股指数、没被引用的主题、信号与新闻原文）不该出现在公开响应里。
EVIDENCE: dict[str, Any] = {
    "version": "market-brief-evidence-v1",
    "indices": {
        "as_of": "2026-10-08T20:05:12Z",
        "saved_at": "2026-10-08T20:05:30Z",
        "stale": False,
        "unit": "change_percent 为百分数",
        "rows": [
            {"id": "idx:^GSPC", "symbol": "^GSPC", "name_zh": "标普500指数", "price": 6011.54, "change_percent": 0.62},
            {"id": "idx:^IXIC", "symbol": "^IXIC", "name_zh": "纳斯达克综合指数", "price": 19811.2, "change_percent": 0.91},
            {"id": "idx:^DJI", "symbol": "^DJI", "name_zh": "道琼斯工业平均指数", "price": 44001.3, "change_percent": 0.04},
            {"id": "idx:^RUT", "symbol": "^RUT", "name_zh": None, "price": 2301.9, "change_percent": -0.35},
            {"id": "idx:^N225", "symbol": "^N225", "name_zh": "日经225指数", "price": 39811.0, "change_percent": -0.27},
        ],
    },
    "internals": {
        "market_signals": {
            "as_of": "2026-10-08T20:02:40Z",
            "stale": False,
            "sector_etf_breadth": {"available": 11, "expected": 11, "above_50dma": 6},
            "signals": [{"id": "sig:rsp_spy_5d", "field": "rsp_spy_5d", "value": -0.41, "label": "等权弱于市值加权"}],
        },
        "market_regime": {"id": "regime:market", "served_session": "2026-10-08", "score": 59.5},
    },
    "themes": {
        "served_session": "2026-10-08",
        "spy_return_1mo": 1.87,
        "rows": [
            {"id": "theme:software", "name": "软件基础设施", "excess_vs_spy_1mo": 10.68,
             "leaders": [{"ticker": "CRWD", "score": 88.1}]},
            {"id": "theme:semiconductors", "name": "半导体", "excess_vs_spy_1mo": 8.42,
             "leaders": [{"ticker": "MU", "score": 91.0}]},
            {"id": "theme:energy", "name": "能源", "excess_vs_spy_1mo": -3.71, "leaders": []},
        ],
    },
    "macro": {
        "id": "macro:composite",
        "data_through": "2026-10-08",
        "unit": "分位分数 0-100，越高越宽松或越支持风险资产",
        "composite_score": 58.0,
        "modules": [
            {"id": "macro:credit", "name_zh": "信用", "score": 61.2},
            {"id": "macro:external", "name_zh": "外部冲击", "score": 52.4},
            {"id": "macro:funding", "name_zh": "融资", "score": 66.8},
            {"id": "macro:liquidity", "name_zh": "流动性", "score": 63.5},
            {"id": "macro:rates", "name_zh": "利率", "score": 38.9},
            {"id": "macro:risk", "name_zh": "风险", "score": 57.3},
            {"id": "macro:treasury", "name_zh": "国债", "score": 49.6},
        ],
        "warnings": ["宏观数据滞后一周"],
    },
    "news": {"items": [{"id": "news:9f3c2b7e1a", "news": {"title": "Memory prices rise again", "summary": "secret body"}}]},
}


def _record(
    *,
    status: str = "completed",
    trading_date: date = date(2026, 10, 8),
    slot: str = "post_close",
    started_at: datetime = NOW - timedelta(minutes=10),
    trigger: str = "scheduled",
    error_code: str | None = None,
) -> BriefRunRecord:
    completed = status == "completed"
    return BriefRunRecord(
        run_id=new_run_id(trading_date, slot),  # type: ignore[arg-type]
        slot=slot,  # type: ignore[arg-type]
        trading_date=trading_date,
        trigger=trigger,  # type: ignore[arg-type]
        status=status,
        started_at=started_at,
        completed_at=started_at + timedelta(minutes=8),
        model="claude-opus-5-5",
        effort="xhigh",
        error_code=error_code,
        error_detail=None if completed else "status=529 type=overloaded_error",
        evidence={**EVIDENCE, "session": {"slot": slot}},
        evidence_bytes=41234,
        coverage={
            **SAMPLE["brief"]["coverage"],
            "trimmed": [{"block": "news", "from": 20, "to": 14}],
            "allowed_codes_count": 87,
            "block_errors": {"breakouts": "OperationalError"},
        },
        request_meta={"model": "claude-opus-5-5"},
        raw_output_text=json.dumps(SAMPLE["brief"]["result"], ensure_ascii=False) if completed else None,
        result=SAMPLE["brief"]["result"] if completed else None,
        validation_warnings=(),
        external_sources=(
            {"url": "https://www.bls.gov/schedule/news_release/ppi.htm", "title": "PPI release schedule", "via": "web_search"},
            {"url": "https://www.federalreserve.gov/monetarypolicy/fomcminutes20261001.htm", "title": "FOMC Minutes, October 2026", "via": "web_fetch"},
            {"query": "FOMC minutes October 2026", "via": "web_search"},
        ),
        usage={"input_tokens": 30_000, "output_tokens": 20_000, "web_search_requests": 6,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        cost_microusd=1_234_567,
        duration_seconds=480.0,
        continuation_count=1,
    )


def test_default_root_lives_under_data_dir() -> None:
    assert BriefStore().root == get_data_paths().root / "market-brief"


def test_write_read_round_trip_and_files(tmp_path: Path) -> None:
    store = BriefStore(tmp_path / "market-brief")
    record = _record()
    path = store.write_run(record)

    assert path == store.record_path(record)
    assert path.name == f"2026-10-08-post_close-{record.run_id}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = store.latest_record()
    assert loaded == record
    assert store.latest_record(status="failed") is None
    assert not list(path.parent.glob(".*.tmp"))
    index = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert [item["run_id"] for item in index["runs"]] == [record.run_id]


def test_run_ids_follow_the_documented_format() -> None:
    run_id = new_run_id(date(2026, 10, 8), "post_close")
    assert run_id.startswith("mb_20261008_post_close_") and len(run_id.rsplit("_", 1)[1]) == 8
    with pytest.raises(ValueError):
        new_run_id(date(2026, 10, 8), "midday")  # type: ignore[arg-type]


def test_completed_and_runs_on(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    assert store.completed(date(2026, 10, 8), "post_close") is False
    store.write_run(_record(status="failed", error_code="provider_server_error", started_at=NOW - timedelta(hours=1)))
    assert store.completed(date(2026, 10, 8), "post_close") is False
    store.write_run(_record())
    assert store.completed(date(2026, 10, 8), "post_close") is True
    assert store.completed(date(2026, 10, 8), "pre_open") is False
    assert store.completed(date(2026, 10, 9), "post_close") is False
    # 两次都在 UTC 10-09 启动（交易日是 10-08）：按 UTC 日历日计数。
    assert store.runs_on(date(2026, 10, 9)) == 2
    assert store.runs_on(date(2026, 10, 8)) == 0


def test_latest_public_matches_the_frontend_fixture_shape(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    record = _record()
    store.write_run(record)

    public = store.latest_public(now=NOW)
    assert set(public) == set(SAMPLE)
    assert public["status"] == "ok"
    assert public["schema_version"] == SAMPLE["schema_version"]
    assert public["latest_attempt"] is None
    # 10-08 收盘后之后的下一个开窗：10-09 开盘前 08:40 EDT。
    assert public["next_slot"] == {"slot": "pre_open", "at": "2026-10-09T12:40:00Z"}
    brief = public["brief"]
    assert set(brief) == set(SAMPLE["brief"])
    assert set(brief["coverage"]) == set(SAMPLE["brief"]["coverage"]) == set(PUBLIC_COVERAGE_KEYS)
    assert brief["model"] == {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "effort": "xhigh"}
    assert brief["run_id"] == record.run_id
    assert brief["generated_at"] == "2026-10-09T03:39:12Z"
    assert brief["result"] == SAMPLE["brief"]["result"]
    assert brief["web_search_count"] == 6
    # 访客只看到带 URL 的来源；抓取过全文的排在前面；搜索词不出现。
    assert brief["external_sources"] == [
        {"url": "https://www.federalreserve.gov/monetarypolicy/fomcminutes20261001.htm", "title": "FOMC Minutes, October 2026"},
        {"url": "https://www.bls.gov/schedule/news_release/ppi.htm", "title": "PPI release schedule"},
    ]
    assert "usage" not in brief and "cost_usd" not in brief
    # 图表数据由该次运行的证据现算，正好是接口样例里的 visuals（前端演示模式用的同一份）。
    assert brief["visuals"] == SAMPLE["brief"]["visuals"]

    owner = store.latest_public(now=NOW, owner=True)["brief"]
    assert owner["visuals"] == SAMPLE["brief"]["visuals"]
    assert owner["usage"]["output_tokens"] == 20_000
    assert owner["cost_usd"] == 1.2346
    assert owner["duration_seconds"] == 480.0
    assert owner["coverage"]["block_errors"] == {"breakouts": "OperationalError"}
    assert owner["coverage"]["trimmed"] == [{"block": "news", "from": 20, "to": 14}]


def test_visuals_carry_only_labels_numbers_and_dates(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    store.write_run(_record())
    visuals = store.latest_public(now=NOW)["brief"]["visuals"]
    encoded = json.dumps(visuals, ensure_ascii=False)
    for leaked in ("leaders", "signals", "symbol", "price", "name_zh", "spy_return_1mo", "composite",
                   "CRWD", "软件基础设施", "日经", "等权弱于", "secret body", "Memory prices", "滞后一周"):
        assert leaked not in encoded, leaked
    assert set(visuals) == {"market", "breadth", "sectors", "macro"}
    assert set(visuals["market"]) == {"as_of", "stale", "rows"}
    assert set(visuals["sectors"]) == {"served_session", "rows"}
    assert set(visuals["macro"]) == {"data_through", "rows"}
    for chart in ("market", "sectors", "macro"):
        for row in visuals[chart]["rows"]:
            assert set(row) == {"id", "label", "value"}


def test_visuals_are_null_without_chart_evidence_or_once_the_run_file_is_gone(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    # 早于图表的记录、或证据包超预算被整块撤下：没有可画的块，公开响应照常、visuals 为 null。
    record = _record()
    store.write_run(BriefRunRecord(**{**record.__dict__, "evidence": {"version": "market-brief-evidence-v1", "indices": None,
                                                                    "internals": {"market_signals": None, "market_regime": None},
                                                                    "themes": None, "macro": None}}))
    public = store.latest_public(now=NOW)
    assert public["status"] == "ok" and public["brief"]["visuals"] is None

    store.write_run(_record(started_at=NOW))
    latest = store.latest_record()
    assert latest is not None and store.latest_public(now=NOW)["brief"]["visuals"] is not None
    store.record_path(latest).unlink()
    public = store.latest_public(now=NOW)
    assert public["brief"]["run_id"] == latest.run_id
    assert public["brief"]["visuals"] is None


def test_latest_public_missing_still_reports_attempt_and_next_slot(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    empty = store.latest_public(now=NOW)
    assert empty["status"] == "missing" and empty["brief"] is None and empty["latest_attempt"] is None
    assert empty["snapshot_saved_at"] is None
    assert empty["next_slot"] == {"slot": "pre_open", "at": "2026-10-09T12:40:00Z"}

    store.write_run(_record(status="failed", error_code="provider_refusal"))
    public = store.latest_public(now=NOW)
    assert public["status"] == "missing"
    assert public["brief"] is None
    assert public["latest_attempt"] == {
        "error_code": "provider_refusal",
        "at": "2026-10-09T03:39:12Z",
        "slot": "post_close",
        "trading_date": "2026-10-08",
    }
    assert isinstance(public["snapshot_saved_at"], str)


def test_latest_attempt_only_when_newer_than_the_latest_success(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    store.write_run(_record(status="failed", error_code="provider_server_error", started_at=NOW - timedelta(hours=2)))
    store.write_run(_record(started_at=NOW - timedelta(hours=1)))
    assert store.latest_public(now=NOW)["latest_attempt"] is None

    failed = _record(status="failed", error_code="output_not_json", trading_date=date(2026, 10, 9), slot="pre_open", started_at=NOW + timedelta(hours=9))
    store.write_run(failed)
    public = store.latest_public(now=NOW + timedelta(hours=10))
    assert public["status"] == "ok"
    assert public["brief"]["trading_date"] == "2026-10-08"
    assert public["latest_attempt"]["error_code"] == "output_not_json"
    assert public["latest_attempt"]["slot"] == "pre_open"

    # 较早完成的成功记录晚落盘（命令行与 worker 交错）不会覆盖较新的成功。
    newer = _record(trading_date=date(2026, 10, 9), slot="pre_open", started_at=NOW + timedelta(hours=11))
    store.write_run(newer)
    store.write_run(_record(started_at=NOW - timedelta(hours=3)))
    assert store.latest_public(now=NOW + timedelta(hours=12))["brief"]["run_id"] == newer.run_id


def test_history_is_newest_first_and_limited(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    records = [_record(started_at=NOW + timedelta(hours=offset)) for offset in range(3)]
    for record in records:
        store.write_run(record)
    history = store.history(limit=2)
    assert [item["run_id"] for item in history] == [records[2].run_id, records[1].run_id]
    assert set(history[0]) == {"run_id", "trading_date", "slot", "trigger", "status", "generated_at", "error_code"}
    assert store.history(limit=0) == []


def test_retention_keeps_the_latest_sixty_runs(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    store.runs_dir.mkdir(parents=True)
    unrelated = store.runs_dir / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    records = [_record(started_at=NOW + timedelta(minutes=offset)) for offset in range(RETAINED_RUNS + 2)]
    for record in records:
        store.write_run(record)

    kept = {path.name for path in store.runs_dir.glob("*.json")}
    assert len(kept) == RETAINED_RUNS
    assert store.record_path(records[0]).name not in kept
    assert store.record_path(records[1]).name not in kept
    assert store.record_path(records[-1]).name in kept
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert len(store.history(limit=100)) == RETAINED_RUNS


def test_rejects_inconsistent_records(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    record = _record()
    with pytest.raises(ValueError):
        store.write_run(BriefRunRecord(**{**record.__dict__, "run_id": "bad"}))
    with pytest.raises(ValueError):
        store.write_run(BriefRunRecord(**{**record.__dict__, "result": None}))
    with pytest.raises(ValueError):
        store.write_run(BriefRunRecord(**{**record.__dict__, "status": "running"}))
    assert not store.index_path.exists()


def _corrupt(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def test_unreadable_files_read_as_empty(tmp_path: Path) -> None:
    store = BriefStore(tmp_path)
    _corrupt(store.index_path, "{not json")
    _corrupt(store.latest_path, {"version": 99})
    assert store.history() == []
    assert store.latest_record() is None
    assert store.latest_public(now=NOW)["status"] == "missing"
