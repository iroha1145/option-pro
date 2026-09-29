from __future__ import annotations

import pytest

from app.services.catalysts.news_quality import news_quality


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        ("公司宣布派发0.2175美元股息", "公司宣布派发0.2175美元股息"),
        ("ABC declares quarterly cash dividend of $0.25 per share", None),
        ("ABC dividend ex-date is October 10", "The record date is October 11."),
        ("某公司股息登记日为10月10日", None),
    ],
)
def test_routine_dividend_is_hidden(title: str, summary: str | None) -> None:
    assert news_quality(title, summary) == "routine_dividend"


@pytest.mark.parametrize(
    ("title", "summary", "body"),
    [
        ("公司宣布派发0.2175美元股息", "公司将股息从0.10美元提高至0.2175美元", None),
        ("ABC declares quarterly dividend of $0.25", None, "This is its first dividend."),
        ("ABC announces special dividend of $0.25", None, None),
        ("公司宣布取消股息", None, None),
        ("公司宣布恢复派息", None, None),
        ("ABC dividend ex-date is October 10", "Revenue rose after earnings beat", None),
        ("Fed cuts rates", "Fed cuts rates", None),
        ("美联储宣布降息", None, None),
        ("ABC to acquire XYZ", None, None),
        ("监管机构启动调查", None, None),
        ("ABC reports quarterly earnings", None, None),
        ("ABC dividend yield reaches 5%", None, None),
        ("ABC announces a dividend", None, None),
        ("ABC launches new product", None, None),
    ],
)
def test_concrete_news_stays_visible(
    title: str, summary: str | None, body: str | None
) -> None:
    assert news_quality(title, summary, body) is None


def test_generic_repeated_headline_without_detail_is_hidden() -> None:
    assert news_quality("公司发布最新公告", "公司发布最新公告。") == "incomplete_information"
    assert news_quality("Company announces update", "Company announces update.") == "incomplete_information"


def test_generic_headline_with_article_detail_stays_visible() -> None:
    assert news_quality("公司发布最新公告", "公司发布最新公告", "公司将发布新产品") is None


@pytest.mark.parametrize(
    "material_detail",
    [
        "The board also approved a new $5 billion share buyback.",
        "The board approved a two-for-one stock split.",
        "The company announced a secondary offering.",
        "The company secured new financing.",
        "Its bonds entered debt default.",
        "The company filed for bankruptcy.",
        "The CEO resigned effective today.",
        "The board appointed a new CEO.",
        "董事会同时批准股份回购计划。",
        "董事会同时批准拆股。",
        "公司同时宣布增发股票。",
        "公司同时完成新一轮融资。",
        "公司债券发生债务违约。",
        "公司申请破产。",
        "首席执行官今日辞职。",
        "董事会任命新首席执行官。",
    ],
)
def test_other_material_event_with_routine_dividend_stays_visible(material_detail: str) -> None:
    title = "ABC declares quarterly dividend of $0.25"
    assert news_quality(title, material_detail) is None
    assert news_quality(title, article_text=material_detail) is None


def test_dividend_calendar_details_remain_routine() -> None:
    assert news_quality(
        "ABC declares quarterly dividend of $0.25",
        "Record date is October 10 and payment date is October 20.",
    ) == "routine_dividend"
