"""Reads trust stored results whose schema identity is current.

That identity folds in RESULT_VALIDATION_CONTRACT_VERSION, so any change to
what validate_result accepts or rewrites must bump that version; otherwise
rows stored under the old rules keep being served without the new check.
The fingerprint below makes such a change fail loudly.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from app.services.ai_jobs.models import (
    RESULT_VALIDATION_CONTRACT_VERSION,
    check_stored_result,
    validate_result,
)
from test_ai_jobs import _earnings_result
from test_ai_jobs_zh_contract import (
    _market_focus_payload,
    _market_focus_result,
    _news_payload,
    _news_result,
)
from test_signal_context import _signal_result
from test_verified_focus_prose_compatibility import fixture as _verified_focus_fixture

# Update only together with RESULT_VALIDATION_CONTRACT_VERSION when the
# validator changed, or alone when only the corpus below changed.
CONTRACT_FINGERPRINTS = {
    "simplified-chinese-v4": "1404f593895a4cf553dbe65520b7d07bd4254870bc0e3853bee1b668e2608b02",
}


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _corpus() -> list[tuple[str, str, dict, dict]]:
    rewritten_news = _news_result()
    rewritten_news["title_zh"] = "  英伟达依据Rule 10b5-1计划减持  "
    rewritten_news["affected_stocks"][0]["ticker"] = "nvda"
    rewritten_news["key_factors"] = ["ＮＶＤＡ供货速度"]
    macro_focus = _market_focus_result()
    macro_focus["summary_zh"] = "宏观环境状态为active，科技股波动加大。"
    self_listed = _earnings_result()
    self_listed["impacted"].append(
        {
            "ticker": "aapl",
            "name": "苹果",
            "relation": "competitor",
            "direction": "mixed",
            "reason": "自身不应出现在受影响名单。",
        }
    )
    verified_payload, verified, _evidence = _verified_focus_fixture()
    verified["summary_zh"] = "截至as_of，带宽为8Gbps。主要分类。(6)公司类事件：公司已披露合作。"
    compat_payload, compat, _evidence = _verified_focus_fixture()
    compat["summary_zh"] = "主要分类。⑹公司类事件：公司已披露合作。带宽为８Ｇｂｐｓ。"
    option = {
        "output_language": "zh-CN",
        "confidence": "low",
        "direction": "bullish",
        "direction_status": "available",
        "summary": "看涨期权成交集中。",
        "analysis": "缺少成交主动方，无法判断方向。",
        "key_strikes": ["120行权价"],
        "risk_note": "仅供信息参考。",
    }
    return [
        ("news", "news_impact", _news_result(), _news_payload()),
        ("news rewrites", "news_impact", rewritten_news, _news_payload()),
        ("focus", "market_focus", _market_focus_result(), _market_focus_payload()),
        (
            "focus macro state",
            "market_focus",
            macro_focus,
            _market_focus_payload(macro_conditions={"status": "active"}),
        ),
        ("earnings", "earnings_impact", _earnings_result(), {"ticker": "AAPL", "name": "Apple"}),
        ("earnings self listed", "earnings_impact", self_listed, {"ticker": "AAPL", "name": "Apple"}),
        ("signal", "signal_analysis", _signal_result(), {"ticker": "AMD"}),
        ("option without trade side", "option_alerts", option, {"ticker": "AMD", "alerts": []}),
        ("verified focus translations", "market_focus", verified, verified_payload),
        ("verified focus compatibility characters", "market_focus", compat, compat_payload),
    ]


def test_validator_behaviour_is_pinned_to_the_contract_version() -> None:
    lines = []
    for name, job_type, raw, payload in _corpus():
        try:
            output = _canonical(validate_result(job_type, json.dumps(raw, ensure_ascii=False), payload))
        except ValidationError as exc:
            output = "rejected:" + json.dumps(
                [[list(error["loc"]), error["type"], error["msg"]] for error in exc.errors()],
                ensure_ascii=False,
            )
        except ValueError as exc:
            output = f"rejected:{exc}"
        lines.append(f"{name}\t{output}")
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    assert CONTRACT_FINGERPRINTS.get(RESULT_VALIDATION_CONTRACT_VERSION) == digest, (
        f"corpus fingerprint is {digest}. If validate_result changed what it "
        "accepts or rewrites, bump RESULT_VALIDATION_CONTRACT_VERSION and record "
        "the fingerprint under the new version. If only the fixtures this corpus "
        "borrows from other test modules changed, re-pin the current version."
    )


def test_stored_results_read_back_as_the_full_check_would_return_them() -> None:
    for name, job_type, raw, payload in _corpus():
        try:
            validated = validate_result(job_type, json.dumps(raw, ensure_ascii=False), payload)
        except ValueError:
            continue
        stored = json.loads(_canonical(validated))
        assert validate_result(job_type, _canonical(validated), payload) == validated, name
        assert check_stored_result(job_type, stored, payload) == validated, name
