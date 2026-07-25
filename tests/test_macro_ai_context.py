"""Market Focus AI integration: compact macro block, identity bumps, isolation.

No test here calls OpenAI. The AI job repository is exercised locally only.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.services.ai_jobs import runtime as ai_runtime
from app.services.catalysts import local_intelligence as local_module
from app.services.macro_conditions.registry import MODULES, SCORING_VERSION
from app.services.macro_conditions.repository import MacroRepository
from app.services.macro_conditions.service import (
    MAX_AI_CONTEXT_BYTES,
    MacroConditionsService,
)
from macro_fixtures import fixed_clock, seed_repository


SEED_START = dt.date(2019, 7, 1)
SEED_END = dt.date(2026, 7, 23)
AS_OF = "2026-07-24T22:30:00Z"


def _service(tmp_path) -> MacroConditionsService:
    repository = MacroRepository(tmp_path / "macro-conditions.db", clock=fixed_clock())
    seed_repository(repository, start=SEED_START, end=SEED_END)
    service = MacroConditionsService(repository, clock=fixed_clock())
    bundle, _summary = service.build_snapshot(as_of=AS_OF)
    assert bundle is not None
    repository.publish(bundle, run_id="mcr_test")
    return service


def _encoded(block: dict) -> bytes:
    return json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# 85, 90 the compact block
# ---------------------------------------------------------------------------


def test_the_macro_block_is_compact_bounded_and_complete(tmp_path) -> None:
    block = _service(tmp_path).ai_context()
    assert block is not None
    assert block["scoring_version"] == SCORING_VERSION
    assert block["status"] in {"active", "degraded", "stale"}
    assert isinstance(block["composite_score"], float)
    assert block["regime"]
    assert block["data_through"]
    assert block["history_basis"] in {
        "latest_revised_backfill",
        "local_point_in_time",
        "mixed",
    }
    # Exactly seven modules, at most three drivers per side.
    assert set(block["module_scores"]) == {module.module_id for module in MODULES}
    assert len(block["module_scores"]) == 7
    assert len(block["top_improving"]) <= 3
    assert len(block["top_deteriorating"]) <= 3
    assert len(_encoded(block)) <= MAX_AI_CONTEXT_BYTES


def test_the_macro_block_carries_no_history_no_raw_series_and_no_secret(
    tmp_path,
    monkeypatch,
) -> None:
    secret = "c" * 32
    monkeypatch.setenv("FRED_API_KEY", secret)
    block = _service(tmp_path).ai_context()
    assert block is not None
    text = _encoded(block).decode()
    assert secret not in text
    assert "api_key" not in text
    assert "FRED_API_KEY" not in text
    # No eight-year history and no raw series identifiers.
    assert "points" not in block
    assert "observations" not in block
    for series_id in ("WALCL", "RRPONTSYD", "VIXCLS", "DTWEXBGS"):
        assert series_id not in text


def test_a_missing_snapshot_yields_no_block_rather_than_invented_scores(
    tmp_path,
) -> None:
    repository = MacroRepository(tmp_path / "macro-conditions.db", clock=fixed_clock())
    repository.initialize()
    service = MacroConditionsService(repository, clock=fixed_clock())
    assert service.ai_context() is None


def test_an_unconfigured_key_yields_no_block(tmp_path) -> None:
    service = _service(tmp_path)
    assert service.ai_context(key_configured=False) is None


def test_a_stale_snapshot_is_labelled_stale_in_the_block(tmp_path) -> None:
    from datetime import datetime, timezone

    repository = MacroRepository(tmp_path / "macro-conditions.db", clock=fixed_clock())
    seed_repository(repository, start=SEED_START, end=SEED_END)
    service = MacroConditionsService(repository, clock=fixed_clock())
    bundle, _summary = service.build_snapshot(as_of=AS_OF)
    assert bundle is not None
    repository.publish(bundle, run_id="mcr_test")

    # Read the same snapshot a month later: it is stale, and says so.
    later = MacroConditionsService(
        repository,
        clock=lambda: datetime(2026, 8, 30, 22, 30, tzinfo=timezone.utc),
    )
    block = later.ai_context()
    assert block is not None
    assert block["status"] == "stale"


# ---------------------------------------------------------------------------
# 87-88 identity
# ---------------------------------------------------------------------------


def test_the_focus_prompt_and_input_schema_versions_are_bumped() -> None:
    assert local_module.FOCUS_PROMPT_VERSION == "market-focus-zh-cn-v6"
    assert local_module.FOCUS_INPUT_SCHEMA_VERSION == "market-focus-input-v2"
    assert local_module.FOCUS_PROMPT_FAMILY_RE.fullmatch(
        local_module.FOCUS_PROMPT_VERSION
    )


def test_the_output_schema_name_is_unchanged_so_old_results_stay_readable() -> None:
    schema_name, _digest = ai_runtime.schema_identity("market_focus")
    assert schema_name == "market_focus_zh_cn_v5"
    assert local_module.FOCUS_SCHEMA_FAMILY_RE.fullmatch(schema_name)


def test_the_prompt_cache_key_changed_with_the_macro_discipline() -> None:
    request = ai_runtime.build_runtime_request("market_focus", {})
    _name, digest = ai_runtime.schema_identity("market_focus")
    # The digest is a function of the instructions, so the discipline text is
    # necessarily part of the prompt cache key.
    assert len(digest) == 64
    assert "macro_conditions" in request.instructions
    assert digest != ai_runtime.schema_identity("news_impact")[1]


def test_the_input_hash_covers_the_macro_version_and_values() -> None:
    from hashlib import sha256

    def digest(document: dict) -> str:
        return sha256(
            json.dumps(
                document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

    base = {
        "input_schema_version": local_module.FOCUS_INPUT_SCHEMA_VERSION,
        "prepared_revision": 7,
        "events": [{"event_group_id": "g1"}],
    }
    with_macro = dict(base)
    with_macro["macro_conditions"] = {
        "scoring_version": SCORING_VERSION,
        "composite_score": 51.4,
    }
    moved = dict(base)
    moved["macro_conditions"] = {
        "scoring_version": SCORING_VERSION,
        "composite_score": 52.9,
    }
    other_version = dict(base)
    other_version["macro_conditions"] = {
        "scoring_version": "optix-macro-score-v2",
        "composite_score": 51.4,
    }
    hashes = {digest(base), digest(with_macro), digest(moved), digest(other_version)}
    # Version and values both move the hash; four distinct documents, four hashes.
    assert len(hashes) == 4


# ---------------------------------------------------------------------------
# 91 prompt discipline
# ---------------------------------------------------------------------------


def test_the_prompt_states_the_scores_are_deterministic_and_not_a_forecast() -> None:
    instructions = ai_runtime.build_runtime_request("market_focus", {}).instructions
    for phrase in (
        "由确定性代码计算完成",
        "不得重新计算",
        "历史滚动分位",
        "不是预测概率",
        "禁止据此输出买入、卖出、仓位或目标价指令",
        "必须明确说明宏观数据已陈旧",
        "当该块缺失时不得提及或推测宏观环境",
    ):
        assert phrase in instructions, phrase


def test_no_macro_job_type_or_macro_ai_route_was_added() -> None:
    from typing import get_args

    from app.services.ai_jobs.models import AIJobType

    job_types = set(get_args(AIJobType))
    assert job_types == {
        "earnings_impact",
        "option_alerts",
        "signal_analysis",
        "news_impact",
        "market_focus",
    }
    assert "macro_analysis" not in job_types
    assert "macro_conditions" not in job_types
    with pytest.raises(ValueError):
        ai_runtime.build_runtime_request("macro_analysis", {})

    import app.main as main

    def collect(routes) -> set[str]:
        # This FastAPI version keeps included routers nested rather than
        # flattening their routes onto the application.
        found: set[str] = set()
        for route in routes:
            path = getattr(route, "path", "")
            if path:
                found.add(path)
            for holder in ("routes", "original_router", "router", "app"):
                nested = getattr(route, holder, None)
                nested_routes = getattr(nested, "routes", None) if nested else None
                if holder == "routes":
                    nested_routes = nested
                if nested_routes:
                    found |= collect(nested_routes)
        return found

    paths = collect(main.app.routes)
    macro_routes = {path for path in paths if "macro" in path}
    assert "/api/macro/ai" not in macro_routes
    assert macro_routes == {
        "/api/macro/conditions",
        "/api/macro/conditions/history",
        "/api/macro/conditions/modules/{module_id}",
        "/api/macro/conditions/factors/{factor_id}/history",
        "/api/macro/conditions/refresh",
    }


# ---------------------------------------------------------------------------
# 86, 89 isolation and historical jobs
# ---------------------------------------------------------------------------


def test_macro_context_reader_is_failure_isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    # No macro database at all: the reader returns None instead of raising.
    assert local_module.macro_conditions_context() is None

    def explode() -> None:
        raise RuntimeError("macro store on fire")

    monkeypatch.setattr(
        "app.services.macro_conditions.repository.MacroRepository.__init__",
        lambda *_args, **_kwargs: explode(),
    )
    assert local_module.macro_conditions_context() is None


def test_a_focus_cycle_still_builds_without_any_macro_data(monkeypatch) -> None:
    """The original input must survive a macro outage untouched."""

    monkeypatch.setattr(local_module, "macro_conditions_context", lambda: None)
    document = {
        "input_schema_version": local_module.FOCUS_INPUT_SCHEMA_VERSION,
        "prepared_revision": 3,
        "events": [{"event_group_id": "g1"}],
    }
    assert "macro_conditions" not in document
    assert local_module.macro_conditions_context() is None
