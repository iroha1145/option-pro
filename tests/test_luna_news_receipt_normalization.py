from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.repository import AIJobRepository
from test_catalyst_local_intelligence import _news_result
from test_luna_news_web_fallback import payload, response, settings, started


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


ISSUER = "https://investors.issuer.com/news/financing?id=1&series=A"
PUBLICATION = "https://6ix.com/news/issuer-debt-offering"


def provider_call(urls, *, status="completed", identity="search_1"):
    return dict(
        type="web_search_call",
        id=identity,
        status=status,
        action=dict(
            type="search", sources=[dict(url=url, title="核验来源") for url in urls]
        ),
    )


def news(**changes):
    result = _news_result(news_id=1, change_sequence=1, content_hash="hash-1")
    result.update(changes)
    return result


def receipt(data, *, urls=None, status="completed"):
    return runtime.openai_receipt(
        response(
            calls=[provider_call([ISSUER] if urls is None else urls, status=status)],
            text=json.dumps(data, ensure_ascii=False),
        )
    )


def test_actual_background_poll_retains_provider_sources(tmp_path, monkeypatch):
    repository, job = started(tmp_path)
    raw = news(
        summary_zh=f"公司宣布融资安排。([investors.issuer.com]({ISSUER}&utm_source=openai))"
    )
    completed = response(
        calls=[provider_call([ISSUER])], text=json.dumps(raw, ensure_ascii=False)
    )
    retrieved = []

    async def retrieve(response_id, **kwargs):
        retrieved.append((response_id, kwargs))
        assert kwargs["include"] == ["web_search_call.action.sources"]
        return completed

    async def forbidden(*args, **kwargs):
        raise AssertionError("Polling must not create another paid response")

    monkeypatch.setattr(
        runtime,
        "_client",
        lambda _: NS(responses=NS(retrieve=retrieve, create=forbidden)),
    )
    monkeypatch.setattr(runtime, "submit_background", forbidden)
    asyncio.run(worker.process_job(repository, settings(repository.path), job, "owner"))
    stored = repository.get_job(job["job_id"])
    assert stored["status"] == "completed"
    assert retrieved == [
        ("resp_news_web", dict(include=["web_search_call.action.sources"], timeout=30))
    ]
    saved = repository.get_provider_result(job["job_id"])
    assert saved["output_text"] == completed.output_text
    assert saved["evidence_sources"][0]["url"] == ISSUER
    assert json.loads(stored["result_json"])["summary_zh"] == "公司宣布融资安排。"
    assert not json.loads(stored["result_json"])["insufficient_context"]


def test_cited_successful_sources_are_selected_before_ten_source_limit():
    noise = [f"https://www.sec.gov/Archives/unrelated-{index}" for index in range(16)]
    second = [f"https://www.sec.gov/Archives/other-{index}" for index in range(15)]
    second[7] = PUBLICATION
    first = noise[:]
    first[12] = ISSUER
    raw = news(
        summary_zh=f"发行事项见[公司公告]({ISSUER}&utm_source=openai)。([6ix.com]({PUBLICATION}?utm_source=openai))"
    )
    output = response(
        calls=[
            provider_call(first),
            provider_call(second, identity="search_2"),
            {
                "type": "message",
                "content": [
                    {
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://fabricated.com/article",
                            }
                        ]
                    }
                ],
            },
        ],
        text=json.dumps(raw, ensure_ascii=False),
    )
    captured = runtime.openai_receipt(output)
    selected = [source["url"] for source in captured["evidence_sources"]]
    assert set(selected[:2]) == {ISSUER, PUBLICATION}
    assert len(selected) == 10
    assert "https://fabricated.com/article" not in selected
    assert set(selected) <= set(first + second)
    AIJobRepository._provider_receipt_json(captured)
    result = runtime.receipt_result(captured, "news_impact", payload())
    assert result["summary_zh"] == "发行事项见公司公告。"


@pytest.mark.parametrize(
    "unbound",
    [
        "https://investors.issuer.com/news/financing?id=2&series=A&utm_source=openai",
        "https://investors.issuer.com/news/different?id=1&series=A&utm_source=openai",
        "https://different.issuer.com/news/financing?id=1&series=A&utm_source=openai",
        "http://investors.issuer.com/news/financing?id=1&series=A&utm_source=openai",
        ISSUER + "&utm_source=untrusted",
        ISSUER + "&redirect=https%3A%2F%2Ffabricated.com",
        ISSUER + "#different-section",
    ],
)
def test_unbound_links_are_stripped_and_the_receipt_is_untouched(unbound):
    # 2026-10-10 第三轮：链接不论是否对得上回执来源都剥掉，标签文字留下（原先报
    # ai_news_unbound_or_unhandled_url）。回执原样不动。
    captured = receipt(news(summary_zh=f"公司公布新安排。[公司公告]({unbound})"))
    original = deepcopy(captured)
    result = runtime.receipt_result(captured, "news_impact", payload())
    assert result["summary_zh"] == "公司公布新安排。公司公告"
    assert captured == original


def test_unknown_urls_annotations_and_failed_calls_cannot_authenticate_text():
    invented = "https://fabricated.com/article"
    raw = news(summary_zh=f"公司公布新安排。([fabricated.com]({invented}))")
    output = response(
        calls=[
            provider_call([ISSUER]),
            provider_call([invented], status="failed", identity="failed"),
            {
                "type": "message",
                "content": [
                    {"annotations": [{"type": "url_citation", "url": invented}]}
                ],
            },
        ],
        text=json.dumps(raw, ensure_ascii=False),
    )
    captured = runtime.openai_receipt(output)
    assert invented not in [source["url"] for source in captured["evidence_sources"]]
    # 2026-10-10 第三轮：正文里的引用照样剥掉，但它进不了回执来源（原先报
    # ai_news_unbound_or_unhandled_url）。
    assert runtime.receipt_result(captured, "news_impact", payload())["summary_zh"] == "公司公布新安排。"
    captured["evidence_sources"].append(
        dict(url=invented, title="捏造来源", type="web_search")
    )
    with pytest.raises(ValueError, match="ai_job_provider_sources_invalid"):
        runtime.receipt_result(captured, "news_impact", payload())


def test_no_provider_sources_stays_insufficient_instead_of_normalizing_claims():
    captured = receipt(
        news(summary_zh=f"资料声称公司融资。([6ix.com]({PUBLICATION}))"), urls=[]
    )
    result = runtime.receipt_result(captured, "news_impact", payload())
    assert result["insufficient_context"] is True
    assert result["confidence"] == 0
    assert result["affected_stocks"] == []


def test_only_narrative_is_normalized_and_receipt_accounting_is_immutable():
    data = payload()
    data["article_reason"] = "http_403"
    raw = news(
        summary_zh=f"融资规模为8.5亿美元，利率7.625%。([6ix.com]({PUBLICATION}?utm_source=openai))",
        uncertainty_notes=[f"输入链接正文因HTTP 403不可得。([6ix.com]({PUBLICATION}))"],
        affected_sectors=[f"特种化学品（[6ix.com]({PUBLICATION})）"],
    )
    captured = receipt(raw, urls=[PUBLICATION])
    captured["usage"]["web_search_requests"] = None
    captured["usage"]["web_fetch_requests"] = None
    original = deepcopy(captured)
    original_payload = deepcopy(data)
    result = runtime.receipt_result(captured, "news_impact", data)
    assert result["summary_zh"] == "融资规模为8.5亿美元，利率7.625%。"
    assert (
        result["uncertainty_notes"][0]
        == "输入链接正文因访问被拒绝（状态码403）不可得。"
    )
    assert result["affected_sectors"] == ["特种化学品"]
    for field in (
        "news_id",
        "change_sequence",
        "content_hash",
        "overall_sentiment",
        "classification",
        "confidence",
        "market_relevance",
        "affected_stocks",
        "affected_commodities",
        "insufficient_context",
    ):
        assert result[field] == raw[field]
    assert captured == original and data == original_payload
    assert (
        runtime.settled_usage_cost_microusd(
            "news_impact",
            captured["usage"],
            model=runtime.LUNA_MODEL,
            fallback_microusd=93130,
        )
        == 93130
    )


@pytest.mark.parametrize(
    "article_status,article_reason",
    [("unavailable", "http_404"), ("unavailable", None), (None, "http_403")],
)
def test_http_label_requires_matching_input_failure(article_status, article_reason):
    data = payload()
    data.update(article_status=article_status, article_reason=article_reason)
    captured = receipt(
        news(uncertainty_notes=["原始链接因HTTP 403不可用。"]), urls=[PUBLICATION]
    )
    # 2026-10-10 口径变更：状态码对不上输入时照旧不翻译；「HTTP 403」按词条原样发布，
    # 不再拒绝。
    result = runtime.receipt_result(captured, "news_impact", data)
    assert result["uncertainty_notes"][0] == "原始链接因HTTP 403不可用。"


def test_trusted_links_do_not_relax_english_or_unknown_metadata():
    for prose in [
        f"公司表示[The deal will improve margins]({ISSUER})。",
        "公司提供UNKNOWN_METADATA标签。",
    ]:
        captured = receipt(news(summary_zh=prose))
        with pytest.raises(ValueError):
            runtime.receipt_result(captured, "news_impact", payload())
    # 2026-10-10 口径变更：输入没有抓取失败记录时照旧不翻译；「HTTP 404」按词条原样发布
    # （原先在上面的拒绝清单里）。
    captured = receipt(news(summary_zh="公司提供HTTP 404状态。"))
    assert runtime.receipt_result(captured, "news_impact", payload())["summary_zh"] == "公司提供HTTP 404状态。"


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", runtime.OFFICIAL_OPENAI_MODEL),
        ("anthropic", runtime.OFFICIAL_CLAUDE_MODEL),
    ],
)
def test_other_models_do_not_receive_luna_normalization(provider, model):
    captured = receipt(
        news(summary_zh=f"公司公布新安排。([investors.issuer.com]({ISSUER}))")
    )
    captured.update(provider=provider, model=model)
    with pytest.raises(ValueError):
        runtime.receipt_result(captured, "news_impact", payload())


def test_normalization_preserves_luna_runtime_schema_identity():
    assert (
        runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL)
        == runtime.LUNA_WEB_NEWS_IDENTITY
    )
