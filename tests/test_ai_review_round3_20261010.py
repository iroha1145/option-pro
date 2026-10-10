"""Counterexamples from the last review of the 2026-10-10 AI fixes (head 0b7b68fa).

The original samples stay exercised under v5. Ordinary market counts and
prose codes publish; explicit Hong Kong code labels still require binding.
"""

from __future__ import annotations

import pytest

from test_ai_analysis_fixes_20261010 import _focus_field, _news_field


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


def _publish_both(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


def _reject_both(text, match=None):
    with pytest.raises(ValueError, match=match):
        _news_field(text)
    with pytest.raises(ValueError, match=match):
        _focus_field(text, field="summary_zh")


# --- M1. Market overview counts after 港股 are not codes ----------------------


@pytest.mark.parametrize(
    "text",
    [
        "港股2600家上市公司中多数收跌。",
        "港股1500只个股下跌。",
        "港股1200余只个股上涨。",
        "港股2000多只股票下跌。",
        "港股5000亿成交创新高。",
        "港股26000点附近震荡。",
        "港股20000点关口失守。",
    ],
)
def test_m1_market_overview_counts_after_hong_kong_stocks_publish(text):
    _publish_both(text)


@pytest.mark.parametrize("text", ["港股1810小米集团盘中走高。", "腾讯港股代码为0700。"])
def test_m1_explicit_hong_kong_code_labels_require_binding(text):
    if "代码" in text:
        with pytest.raises(ValueError, match="unbound_numeric_security_code"):
            _news_field(text)
        with pytest.raises(ValueError, match="unbound_numeric_security_code"):
            _focus_field(text, field="summary_zh")
    else:
        assert _news_field(text) == text
        assert _focus_field(text, field="summary_zh") == text


# --- Suggestion 1. Country suffixes, case-insensitive suffixes, www. hosts ----


@pytest.mark.parametrize(
    "text",
    [
        "据港交所（hkexnews.hk）披露，公司配售新股。",
        "公司公布融资安排（info.gov.hk）。",
        "墨西哥媒体报道（eleconomista.com.mx）。",
        "韩联社报道（yna.co.kr）。",
        "欧洲媒体报道（politico.eu）。",
        "瑞士媒体报道（nzz.ch）。",
        "新西兰媒体报道（rnz.co.nz）。",
        "新加坡媒体报道（straitstimes.com.sg）。",
        "路透社报道（Reuters.COM）。",
        "监管文件见（WWW.SEC.GOV）。",
        "监管文件见（Sec.Gov）。",
    ],
)
def test_r3s1_country_and_upper_case_hosts_are_not_published(text):
    _reject_both(text)


@pytest.mark.parametrize(
    "text",
    [
        "开发者转向（node.js）生态。",
        "前端框架（Vue.js）更新。",
        "框架基于（ASP.NET）。",
        "网络协议（TCP/IP）。",
        "聊天机器人公司（Character.AI）获融资。",
        "深度学习课程（Fast.ai）走红。",
        "项目主页（GitHub.io）上线。",
        "说明文档（README.md）更新。",
        "部署脚本（deploy.sh）调整。",
        "类型声明（index.ts）补齐。",
        "入口文件（main.go）重写。",
        "核心库（lib.rs）发布。",
        "脚本（train.py）开源。",
    ],
)
def test_r3s1_technical_and_product_suffixes_stay_glosses(text):
    _publish_both(text)


# --- Suggestion 2. Runtime settings writes keep the same token floor ---------


def _settings_client(monkeypatch, shared_budget):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.runtime_settings import router
    from app.personal_config import PersonalConfig
    from app.services import runtime_settings

    config = PersonalConfig.model_validate({"model_budget": {"daily_budget_usd": shared_budget}})
    monkeypatch.setattr(runtime_settings, "get_personal_config", lambda: config)
    runtime_settings.get_runtime_settings_store.cache_clear()
    store = runtime_settings.get_runtime_settings_store()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, base_url="http://localhost"), store


def _write_token_limit(client, version, limit):
    return client.put(
        "/api/runtime-settings",
        json={"expected_version": version, "settings": {"ai": {"daily_token_limit": limit}}},
    )


def test_r3s2_token_only_budget_rejects_a_low_daily_token_limit(monkeypatch):
    client, store = _settings_client(monkeypatch, 0)
    response = _write_token_limit(client, 1, 102_400)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "token_limit_below_task_reservation"
    assert detail["minimum"] == 1_050_000
    assert "1,050,000" in detail["message"] and "model_budget" in detail["message"]
    assert store.read().version == 1


def test_r3s2_rollback_to_a_low_limit_is_refused_under_a_token_only_budget(monkeypatch):
    from app.services.runtime_settings import RuntimeAISettingsPatch, RuntimeSettingsPatch, RuntimeSettingsStore

    client, store = _settings_client(monkeypatch, 0)
    # A revision written while a shared budget was configured.
    unchecked = RuntimeSettingsStore(store.path, defaults=store.defaults)
    for version, limit in ((1, 102_400), (2, 9_000_000)):
        unchecked.update(
            RuntimeSettingsPatch(ai=RuntimeAISettingsPatch(daily_token_limit=limit)),
            expected_version=version,
        )
    response = client.post("/api/runtime-settings/rollback", json={"expected_version": 3, "target_version": 2})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "token_limit_below_task_reservation"
    assert store.read().settings.ai.daily_token_limit == 9_000_000


@pytest.mark.parametrize(("shared_budget", "limit"), [(0, 1_050_000), (0, 9_000_000), (10.0, 102_400)])
def test_r3s2_limits_that_admit_one_task_or_a_shared_budget_are_saved(monkeypatch, shared_budget, limit):
    client, store = _settings_client(monkeypatch, shared_budget)
    response = _write_token_limit(client, 1, limit)
    assert response.status_code == 200
    assert store.read().settings.ai.daily_token_limit == limit


@pytest.mark.parametrize(("text", "code"), [
    ("港股代码1810小米集团走强。", "1810"),
    ("腾讯港股代码为0700。", "0700"),
    ("港股编号为09888百度集团受到关注。", "09888"),
])
def test_explicit_hong_kong_code_or_number_label_requires_and_accepts_matching_binding(text, code):
    _reject_both(text, match="unbound_numeric_security_code")
    assert _news_field(text, allowed_tickers=["NVDA", code]) == text
    assert _focus_field(text, field="summary_zh", allowed_tickers=["NVDA", code]) == text
