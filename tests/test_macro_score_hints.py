"""The reader-facing macro hints must mirror the backend registry verbatim.

``frontend-src/src/lib/scoreHints.ts`` is the single source of the InfoHint copy,
and its macro entries are generated from ``registry.py``. This test is the mirror
that stops the two from drifting: a formula change that forgets the copy fails
here rather than shipping a description that no longer matches the maths.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.macro_conditions.registry import (
    FACTORS,
    FACTOR_IDS_BY_MODULE,
    MODULES,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HINTS_PATH = REPOSITORY_ROOT / "frontend-src" / "src" / "lib" / "scoreHints.ts"

# i18n 之后条目形如 `title: t('…')`：t() 的实参就是简体中文 msgid（gettext 风格，
# 见 src/i18n/core.ts），所以「前端文案 == registry.description_zh 逐字一致」这条
# 契约照旧成立——这里提取的是 msgid，译文层由前端的 i18n-coverage 测试另行把守。
# title/body 捕获括号内文本（下面有精确相等断言）；note 保留整段（断言只用 in）。
_ENTRY = re.compile(
    r"^  (?P<key>[A-Za-z0-9_]+): \{\n"
    r"    title: t\('(?P<title>(?:[^'\\]|\\.)*)'\),\n"
    r"    body:\n"
    r"      t\('(?P<body>(?:[^'\\]|\\.)*)'\),\n"
    r"    note: (?P<note>MACRO_MISSING_AWARE|t\('(?:[^'\\]|\\.)*'\)),\n"
    r"  \},$",
    re.MULTILINE,
)


def _unescape(value: str) -> str:
    return value.replace("\\'", "'").replace("\\\\", "\\")


def _block(name: str) -> dict[str, dict[str, str]]:
    text = HINTS_PATH.read_text(encoding="utf-8")
    start = text.index(f"export const {name}: Record<string, ScoreHint> = {{")
    end = text.index("\n};", start)
    body = text[start:end]
    return {
        match.group("key"): {
            "title": _unescape(match.group("title")),
            "body": _unescape(match.group("body")),
            "note": _unescape(match.group("note")),
        }
        for match in _ENTRY.finditer(body)
    }


@pytest.fixture(scope="module")
def factor_hints() -> dict[str, dict[str, str]]:
    return _block("MACRO_FACTOR_HINTS")


@pytest.fixture(scope="module")
def module_hints() -> dict[str, dict[str, str]]:
    return _block("MACRO_MODULE_HINTS")


def test_the_hints_file_exists_and_parses() -> None:
    assert HINTS_PATH.is_file()
    assert "SCORE_HINTS_MACRO" in HINTS_PATH.read_text(encoding="utf-8")


def test_every_factor_has_a_hint_and_no_extra_hints_exist(factor_hints) -> None:
    assert set(factor_hints) == {factor.factor_id for factor in FACTORS}
    assert len(factor_hints) == 30


def test_each_factor_hint_body_is_the_registry_description_verbatim(
    factor_hints,
) -> None:
    for factor in FACTORS:
        entry = factor_hints[factor.factor_id]
        assert entry["body"] == factor.description_zh, factor.factor_id
        assert factor.display_name_zh in entry["title"], factor.factor_id


def test_each_factor_hint_states_the_real_direction_and_scoring_method(
    factor_hints,
) -> None:
    expected_direction = {
        "high": "原值越高分数越高",
        "low": "原值越低分数越高",
        "target": "越接近目标值分数越高",
    }
    expected_method = {
        "supportive_high_percentile": "5 年滚动历史分位。",
        "supportive_low_percentile": "5 年滚动历史分位（取反）。",
        "target_distance": "先算与目标的距离，再取 5 年滚动历史分位（取反）。",
        "direct_score": "注册公式直接给出 0–100 分，不做历史分位。",
    }
    for factor in FACTORS:
        note = factor_hints[factor.factor_id]["note"]
        assert expected_direction[factor.direction] in note, factor.factor_id
        assert expected_method[factor.score_method] in note, factor.factor_id


def test_every_module_has_a_hint_stating_its_real_factor_count_and_floor(
    module_hints,
) -> None:
    assert set(module_hints) == {module.module_id for module in MODULES}
    for module in MODULES:
        entry = module_hints[module.module_id]
        members = FACTOR_IDS_BY_MODULE[module.module_id]
        assert module.display_name_zh in entry["title"]
        assert module.display_name_en in entry["title"]
        assert f"共 {len(members)} 个因子" in entry["body"], module.module_id
        assert (
            f"至少 {module.minimum_valid_factors} 个有效才出分" in entry["body"]
        ), module.module_id
        if module.ema_days:
            assert f"EMA({module.ema_days})" in entry["body"]
        else:
            assert "EMA(" not in entry["body"], module.module_id


def test_the_composite_hint_states_the_real_module_floor_and_the_disclaimer() -> None:
    from app.services.macro_conditions.registry import (
        COMPOSITE_MINIMUM_VALID_MODULES,
    )

    text = HINTS_PATH.read_text(encoding="utf-8")
    assert f"至少 {COMPOSITE_MINIMUM_VALID_MODULES} 个模块有效才出正式分" in text
    assert "不是预测概率" in text
    assert "不构成买入、卖出、仓位或目标价建议" in text
    assert "不代表市场一定上涨" in text


def test_the_regime_hint_lists_the_real_band_boundaries() -> None:
    text = HINTS_PATH.read_text(encoding="utf-8")
    assert "<30 明显收紧 · 30–45 偏紧 · 45–55 中性 · 55–70 偏松 · ≥70 明显宽松" in text


def test_no_third_party_product_name_appears_in_the_macro_copy() -> None:
    text = HINTS_PATH.read_text(encoding="utf-8")
    for forbidden in ("MacroDial", "macrodial", "bhadial"):
        assert forbidden not in text
