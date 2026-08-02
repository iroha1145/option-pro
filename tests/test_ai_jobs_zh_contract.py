from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import (
    validate_result,
    validate_simplified_chinese_text,
)
from app.services.ai_jobs.repository import AIJobRepository
from app.services.ai_jobs.worker import process_job


def _settings(path):
    return SimpleNamespace(
        openai_api_key=SecretStr("test-key"),
        openai_model="gpt-5.6-terra",
        openai_reasoning="max",
        openai_execution_mode="background",
        openai_timeout_seconds=900,
        openai_control_timeout_seconds=30,
        openai_max_concurrency=1,
        openai_daily_max_jobs=4,
        openai_job_db_path=path,
        openai_job_lease_seconds=60,
        openai_job_max_age_seconds=86400,
        openai_background_initial_poll_seconds=2,
        openai_background_max_poll_seconds=15,
        openai_background_poll_timeout_seconds=1800,
    )


def _create_job(repository: AIJobRepository, ticker: str):
    version, digest = runtime.schema_identity("earnings_impact")
    return repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": ticker, "name": ticker},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-zh-cn-v4",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )[0]


def _news_result() -> dict:
    return {
        "output_language": "zh-CN",
        "news_id": 1,
        "change_sequence": 7,
        "content_hash": "news-content-hash-1",
        "title_zh": "英伟达发布新一代人工智能芯片",
        "summary_zh": "公司介绍了新产品，市场关注后续供货与客户采用情况。",
        "headline_summary": "新品发布可能影响半导体供应链预期，但实际影响仍取决于出货。",
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
        "causal_summary": "产品发布先影响订单预期，再通过产能与交付情况影响业绩判断。",
        "key_factors": ["客户采用速度", "供应链交付能力"],
        "uncertainty_notes": ["新闻没有提供经审计的订单数据。"],
        "insufficient_context": False,
    }


def _market_focus_result() -> dict:
    return {
        "output_language": "zh-CN",
        "cycle_id": "cycle-20260715-01",
        "as_of": "2026-07-15T12:00:00Z",
        "input_hash": "a" * 64,
        "title_zh": "科技股财报与利率预期成为市场焦点",
        "summary_zh": "市场同时关注大型科技股财报和利率路径，短期分歧有所增加。",
        "headline_summary": "财报线索与利率预期交织，市场方向仍需更多数据确认。",
        "market_summary": "现有事件对科技股和成长板块影响较大，但证据尚不足以支持单一方向判断。",
        "dominant_events": [
            {
                "event_group_id": "event-01",
                "summary": "大型科技公司更新财报指引。",
                "affected_sectors": ["信息技术"],
            }
        ],
        "market_uncertainties": ["利率预期仍可能随经济数据改变。"],
        "affected_sectors": ["信息技术"],
        "focus_ticker_assessments": [
            {
                "ticker": "NVDA",
                "catalyst_bias": 15,
                "confidence": 60,
                "horizon": "days",
                "supporting_event_ids": ["event-01"],
                "conflicting_event_ids": [],
                "summary": "财报指引提供支持，但估值与利率变化仍会带来波动。",
                "risks": ["指引不及市场预期。"],
                "insufficient_evidence": False,
            }
        ],
        "no_new_material_catalyst": False,
        "insufficient_context": False,
    }


def _news_payload() -> dict:
    return {
        "news_id": 1,
        "change_sequence": 7,
        "content_hash": "news-content-hash-1",
        "allowed_tickers": ["NVDA"],
        "title": "NVIDIA announces a product update",
    }


def _market_focus_payload(**overrides) -> dict:
    payload = {
        "cycle_id": "cycle-20260715-01",
        "as_of": "2026-07-15T08:00:00-04:00",
        "input_hash": "a" * 64,
        "allowed_event_group_ids": ["event-01"],
        "allowed_tickers": ["NVDA"],
    }
    payload.update(overrides)
    return payload


def _payload_for(job_type: str) -> dict:
    if job_type == "news_impact":
        return _news_payload()
    if job_type == "market_focus":
        return _market_focus_payload()
    return {}


def _assert_strict_objects(schema: dict) -> None:
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(schema.get("properties", {}))
    for value in schema.values():
        if isinstance(value, dict):
            _assert_strict_objects(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _assert_strict_objects(item)


@pytest.mark.parametrize("job_type", tuple(runtime.AI_TASK_MAX_OUTPUT_TOKENS))
def test_every_model_schema_is_strict_and_declares_simplified_chinese(job_type):
    request = runtime.build_runtime_request(job_type, {"title": "untrusted"})
    _assert_strict_objects(request.schema)
    language = request.schema["properties"]["output_language"]
    assert language["const"] == "zh-CN"
    assert "简体中文" in request.instructions
    assert "不可信" in request.instructions
    assert "交易建议" in request.instructions
    assert "目标价" in request.instructions
    assert "仓位" in request.instructions
    assert "止损" in request.instructions
    assert "外文公司名、品牌名" in request.instructions
    assert "不得原样输出外文公司或品牌名称" in request.instructions
    assert "中文译名或中文音译" in request.instructions
    assert "untrusted_" in request.input_text


def test_news_and_market_focus_results_accept_simplified_chinese():
    news = validate_result(
        "news_impact",
        json.dumps(_news_result(), ensure_ascii=False),
        _news_payload(),
    )
    focus = validate_result(
        "market_focus",
        json.dumps(_market_focus_result(), ensure_ascii=False),
        _market_focus_payload(),
    )
    assert news["title_zh"].startswith("英伟达")
    assert news["affected_stocks"][0]["company"] == "英伟达"
    assert focus["cycle_id"] == "cycle-20260715-01"


def test_market_focus_prompt_explains_cross_field_evidence_semantics():
    request = runtime.build_runtime_request(
        "market_focus",
        _market_focus_payload(),
    )

    assert request.schema_name == "market_focus_zh_cn_v5"
    assert "insufficient_evidence为true时，catalyst_bias必须为null" in (
        request.instructions
    )
    assert "insufficient_evidence为false时" in request.instructions
    assert "supporting_event_ids与conflicting_event_ids不得重叠" in (
        request.instructions
    )


def test_news_result_accepts_rule_10b5_1_identifier_in_real_fields():
    result = _news_result()
    result["summary_zh"] = (
        "首席财务官依据预先安排的10b5-1交易计划出售股份，"
        "交易规模和计划设立时间仍需结合监管文件核对。"
    )
    result["affected_stocks"][0]["reason"] = (
        "减持可能带来短期关注，但10b5-1交易计划削弱了自主看空信号。"
    )
    result["key_factors"] = ["交易按10b5-1计划执行。"]
    result["uncertainty_notes"] = ["未说明10b5-1计划的设立日期。"]

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )

    assert "10b5-1" in validated["summary_zh"]


@pytest.mark.parametrize("identifier", ["10b5-1", "10B5-1"])
@pytest.mark.parametrize("plan_name", ["交易计划", "股票交易计划", "证券交易计划"])
def test_rule_10b5_1_identifier_is_allowed_in_chinese_plan_context(
    identifier,
    plan_name,
):
    text = f"该交易依据预先安排的{identifier}{plan_name}执行。"

    assert validate_simplified_chinese_text(text, None) == text


def test_rule_10b5_1_english_prefix_is_normalized_without_a_model_retry():
    text = "该交易依据预先安排的Rule 10b5-1交易计划执行。"

    assert validate_simplified_chinese_text(text, None) == (
        "该交易依据预先安排的规则10b5-1交易计划执行。"
    )


@pytest.mark.parametrize(
    "text",
    [
        "该药物用于治疗活化磷脂酰肌醇3激酶δ综合征。",
        "研究显示β细胞功能有所改善。",
        "实验测得γ射线强度下降。",
        "模型中的λ系数保持稳定。",
    ],
)
def test_embedded_greek_scientific_symbol_is_not_treated_as_foreign_prose(text):
    assert validate_simplified_chinese_text(text, None) == text


def test_greek_prose_is_still_rejected():
    with pytest.raises(ValueError, match="non_chinese_script_not_allowed"):
        validate_simplified_chinese_text("ΑΓΟΡΑ上涨", None)


@pytest.mark.parametrize(
    "text",
    [
        "Α股价上涨",
        "股票Α受到关注",
        "证券代码为Β",
        "Α公司发布业绩",
        "Β产品完成升级",
        "ο行业需求增长",
    ],
)
def test_greek_homoglyphs_are_rejected_in_security_contexts(text):
    with pytest.raises(ValueError, match="non_chinese_script_not_allowed"):
        validate_simplified_chinese_text(text, None)


@pytest.mark.parametrize(
    "text",
    [
        "该交易依据10b5-2计划执行。",
        "该交易依据11b5-1计划执行。",
        "该交易依据Rule 10b5-1 trading plan执行。",
        "10b5-1",
        "10b5-1股价上涨。",
        "10b5-1股票上涨。",
        "10b5-1证券代码受到关注。",
    ],
)
def test_rule_10b5_1_exception_remains_narrow(text):
    with pytest.raises(ValueError):
        validate_simplified_chinese_text(text, None)


def test_news_prompt_allows_compact_registered_names_but_not_english_prose():
    request = runtime.build_runtime_request("news_impact", _news_payload())

    assert request.schema_name == "news_impact_zh_cn_v6"
    assert "可以保留简短注册名，例如3M、AT&T或SAP" in request.instructions
    assert "禁止保留完整英文句子或英文普通叙述" in request.instructions
    assert "输出前逐字段检查" in request.instructions


def test_market_focus_rejects_a_model_rewritten_snapshot_time():
    with pytest.raises(ValueError, match="market_focus_as_of_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(_market_focus_result(), ensure_ascii=False),
            _market_focus_payload(as_of="2026-07-15T13:00:00Z"),
        )


def test_market_focus_requires_a_timezone_aware_as_of():
    result = _market_focus_result()
    result["as_of"] = "2026-07-15T12:00:00"
    with pytest.raises(ValidationError):
        validate_result(
            "market_focus",
            json.dumps(result, ensure_ascii=False),
            _market_focus_payload(),
        )


@pytest.mark.parametrize(
    "title",
    [
        "US stocks rally after earnings beat expectations",
        "美國科技股上漲",
        "蘋果公司推出新品",
        "市場關注聯準會利率與企業財報",
        "臺灣供應鏈營運與庫存壓力升高",
        "該公司預計擴大產能並調整價格",
    ],
)
def test_news_title_rejects_english_prose_and_traditional_chinese(title):
    result = _news_result()
    result["title_zh"] = title
    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    "traditional_text",
    [
        "市場更新",
        "消息彙整",
        "公司發佈財報後，營收較預期成長。",
        "聯準會維持利率不變，市場關注後續聲明。",
        "供應鏈壓力緩解，晶片庫存回歸正常。",
        "臺灣半導體產業受惠於人工智慧需求。",
        "投資人關注監管機構對併購交易的調查。",
        "主要特徵已经出现。",
        "公司著手改善供货。",
        "投资者需要瞭解风险。",
        "天气持续乾旱。",
    ],
)
def test_common_traditional_and_variant_prose_is_rejected(traditional_text):
    with pytest.raises(ValueError, match="traditional_chinese_not_allowed"):
        validate_simplified_chinese_text(traditional_text, None)


@pytest.mark.parametrize(
    "simplified_text",
    [
        "市场更新",
        "消息汇总",
        "公司发布财报后，收入高于市场预期。",
        "供应链压力缓解，芯片库存逐步恢复正常。",
        "调查显示后续利率变化仍是主要风险。",
        "这是一家著名企业。",
        "行业瞭望认为需求仍会增长。",
        "乾照光电发布业绩。",
        "英伟达（NVIDIA）发布Blackwell芯片",
        "NVIDIA、AMD与TSM关注Blackwell供货",
        "IonQ量子计算订单增长",
        "RSA大会期间Varonis发布人工智能研究",
        "Varonis公司首席执行官亚基·法伊特尔松介绍AI战略",
        "Atlas人工智能项目获得全球信息安全奖",
        "NASCAR与Goodyear继续推进DEI合作",
        "Kalshi市场预测显示交易活跃度上升",
        "Axios报道称Kalshi正在扩大事件合约覆盖范围",
        "公司发布Python SDK更新",
        "企业IT安全风险上升",
        "美国EIA原油库存变化低于预期",
        "ADP就业数据高于市场预期",
        "Pharming集团的Joenja（leniolisib）用于APDS治疗并改善患者症状",
        "A股市场回暖",
        "公司完成A轮融资",
        "A股科技板块走强",
        "AN与ON公司受到市场关注",
        "NOW上调全年收入指引",
        "ON半导体上调全年收入指引",
        "通用汽车公司发布最新业绩",
        "苹果公司（Apple）发布最新产品",
        "iPhone销量继续增长",
        "iOS系统完成升级",
        "eBay平台发布最新业绩",
        "macOS系统发布更新",
        "iShares基金公布持仓",
        "McDonald's公司发布最新业绩",
        "强生公司发布最新业绩",
        "宝洁公司发布最新业绩",
        "美国银行发布最新研究",
        "渣打银行发布最新研究",
        "贸易台公司发布最新业绩",
        "全球支付公司发布最新业绩",
        "台积电发布最新业绩",
        "remdesivir用于治疗相关疾病",
        "semaglutide治疗相关疾病的需求增长",
        "iPhone 17销量继续增长",
        "Claude 4.5模型完成升级",
        "Windows 11系统发布更新",
        "BRK.B股价上涨",
        "US.AAPL股价上涨",
        "BRK-B股价上涨",
        "HK.00700股价上涨",
        "RMS.PA股价上涨",
        "MSFT、GOOG与META股价上涨",
        "BTC/USD价格出现波动",
        "S&P 500指数上涨",
        "COVID-19病例数量下降",
        "F-35订单增加",
        "Amazon.com发布最新业绩",
        "Joenja（leniolisib）用于APDS治疗",
        "Apple 公司发布最新产品",
        "Axios 报道称Kalshi交易活跃",
        "英伟达发布 Blackwell 芯片",
        "Kalshi，市场交易活跃度上升",
        "Apple：公司发布最新产品",
        "“Apple”公司发布最新产品",
        "【Apple】公司发布最新产品",
        "《Apple》品牌发布新品",
        "Apple—公司发布最新产品",
        "Apple、Google与Amazon合作",
        "Apple，Google与Amazon合作",
        "Apple；发布最新产品",
        "scikit-learn完成版本升级",
        "PyTorch-Lightning完成版本升级",
        "gpt-oss完成版本升级",
        "美国CPI同比上涨百分之三",
        "核心PCE通胀放缓",
        "GDP增长低于市场预期",
        "FOMC会议维持利率不变",
        "ETF资金流入增加",
        "IPO市场活跃度回升",
        "SEC发布最新监管文件",
        "FDA批准新药上市",
        "OPEC维持产量政策",
        "PMI数据出现改善",
        "HBM需求继续增长",
        "DRAM与NAND价格上涨",
        "GPU与CPU供应趋紧",
        "API与SDK完成升级",
        "H100与B200出货增加",
        "H100 GPU需求增长",
        "CPI PCE数据受到关注",
        "AWS Azure需求增长",
        "MI300X与GB200受到市场关注",
        "RTX 5090发布后需求增长",
        "Gemini 2.5完成升级",
        "Llama 4模型发布",
        "F-35A订单增加",
        "BTC/USDT价格出现波动",
        "XAU/USD价格出现波动",
        "每股收益EPS高于市场预期",
        "ROE与ROIC继续改善",
        "FCF增长支持公司估值",
        "收入CAGR保持稳定",
        "GAAP利润率有所改善",
        "VIX指数明显回落",
        "WTI原油与LNG价格出现波动",
        "EV估值仍处于较高水平",
        "P/E与PEG估值倍数下降",
        "DCF估值显示价格偏高",
        "NAV与AUM继续增长",
        "ISM、ADP与JOLTS数据受到关注",
        "ECB、BOJ与PBOC政策出现分化",
        "CFTC、FTC与DOJ发布最新文件",
        "客户关系管理与CDN需求保持增长",
        "CRM系统需求保持增长",
        "HIV治疗需求保持稳定",
        "GLP-1药物需求继续增长",
        "5G网络投资出现回升",
        "AWS、Azure与Copilot需求增长",
        "YoY、QoQ与MoM增速均有改善",
        "SaaS订阅收入继续增长",
        "mRNA疫苗研发取得进展",
        "2026年收入继续增长",
        "500指数成分股表现分化",
        "08月数据好于预期",
        "01号文件正式发布",
        "09时30分公布数据",
        "2026年07月19日发布财报",
        "编号007的提案获得通过",
        "S&P 500股票指数出现波动",
        "SEC证券监管文件已经发布",
        "iShares股票基金公布持仓",
        "Apple股票应用完成升级",
        "软件代码：Python完成更新",
        "软件代码：2026完成更新",
        "源代码（Python）完成升级",
        "项目代码：Atlas完成更新",
        "产品代码：Apple已经更新",
        "S&P 500（股票指数）出现波动",
        "SEC（证券监管机构）发布文件",
        "iShares（股票基金）公布持仓",
        "iShares（股／票基金）公布持仓",
        "Apple（股票应用）完成升级",
        "Apple股份公司发布公告",
        "Apple股份有限公司发布公告",
        "公司采用Python。股票市场随后上涨",
        "新系统使用Windows。证券市场随后上涨",
        "开发团队使用GitHub。普通股随后上涨",
        "公司使用CUDA。股票市场随后上涨",
        "Python。股票市场随后上涨",
        "Windows。证券市场随后上涨",
        "GitHub。普通股随后上涨",
        "CUDA。股票市场随后上涨",
    ],
)
def test_simplified_prose_and_necessary_foreign_names_are_allowed(simplified_text):
    allowed_codes = {
        "NVIDIA、AMD与TSM关注Blackwell供货": {"AMD", "NVIDIA", "TSM"},
        "AN与ON公司受到市场关注": {"AN", "ON"},
        "NOW上调全年收入指引": {"NOW"},
        "ON半导体上调全年收入指引": {"ON"},
        "BRK.B股价上涨": {"BRK.B"},
        "US.AAPL股价上涨": {"US.AAPL"},
        "BRK-B股价上涨": {"BRK-B"},
        "HK.00700股价上涨": {"HK.00700"},
        "RMS.PA股价上涨": {"RMS.PA"},
        "MSFT、GOOG与META股价上涨": {"MSFT", "GOOG", "META"},
    }.get(simplified_text, set())
    assert (
        validate_simplified_chinese_text(
            simplified_text,
            None,
            allowed_codes=allowed_codes,
        )
        == simplified_text
    )


@pytest.mark.parametrize("annotation_length", [64, 100, 300])
def test_long_balanced_annotation_after_approved_name_is_allowed(
    annotation_length,
):
    annotation = ("新产品说明" * 100)[:annotation_length]
    text = f"Apple（{annotation}）发布更新"
    assert validate_simplified_chinese_text(text, None) == text


def test_wholly_english_sentence_is_rejected():
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        validate_simplified_chinese_text(
            "Markets rally after companies report stronger earnings",
            None,
        )


def test_source_bound_company_name_can_be_revalidated_outside_pydantic_context():
    text = "Hormel Foods公布最新季度业绩"

    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        validate_simplified_chinese_text(text, None, allowed_codes={"HRL"})

    assert (
        validate_simplified_chinese_text(
            text,
            None,
            allowed_codes={"HRL"},
            source_texts={"Hormel Foods reports fiscal third-quarter results"},
        )
        == text
    )


@pytest.mark.parametrize(
    "english_text",
    [
        "IonQ reports stronger revenue",
        "RSA Conference Varonis AI",
        "Varonis Systems Yaki Faitelson AI Atlas Global InfoSec Awards",
        "NASCAR and Goodyear expand DEI partnership",
    ],
)
def test_wholly_english_names_and_prose_are_rejected(english_text):
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        validate_simplified_chinese_text(english_text, None)


def _replace_nested_value(document: dict, path: tuple[str | int, ...], value: str):
    target = document
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value


@pytest.mark.parametrize(
    "path",
    [
        ("title_zh",),
        ("summary_zh",),
        ("headline_summary",),
        ("affected_stocks", 0, "company"),
        ("affected_stocks", 0, "reason"),
        ("affected_sectors", 0),
        ("affected_commodities", 0, "name"),
        ("affected_commodities", 0, "reason"),
        ("causal_summary",),
        ("key_factors", 0),
        ("uncertainty_notes", 0),
    ],
)
def test_every_news_natural_language_field_uses_the_simplified_gate(path):
    result = _news_result()
    result["affected_commodities"] = [
        {
            "name": "原油",
            "impact_score": 10,
            "reason": "供应变化可能影响短期价格。",
        }
    ]
    _replace_nested_value(result, path, "市場消息彙整")
    with pytest.raises(ValidationError, match="traditional_chinese_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    "company",
    [
        "US stocks rally after earnings beat expectations",
        "市場關注聯準會利率",
        "<script>alert(1)</script>",
        "株式会社任天堂",
        "任天堂株式会社",
        "㍿任天堂",
        "㈱任天堂",
        "（株）任天堂",
    ],
)
def test_news_company_name_is_also_bound_to_the_chinese_contract(company):
    result = _news_result()
    result["affected_stocks"][0]["company"] = company
    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    ("ticker", "company"),
    [
        ("MMM", "3M"),
        ("MMM", "3M公司"),
        ("T", "AT&T"),
        ("SAP", "SAP"),
        ("SPGI", "S&P Global"),
        ("TTD", "The Trade Desk"),
        ("LMT", "洛克希德·马丁"),
        ("NVDA", "NVIDIA公司"),
        ("MSFT", "微软（Microsoft）"),
    ],
)
def test_news_company_name_accepts_ticker_bound_registered_names(ticker, company):
    result = _news_result()
    result["affected_stocks"][0]["ticker"] = ticker
    result["affected_stocks"][0]["company"] = company
    payload = _news_payload()
    payload["allowed_tickers"] = [ticker]

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["affected_stocks"][0]["company"] == company


def test_company_name_rejects_chinese_prefix_followed_by_english_prose():
    result = _news_result()
    result["affected_stocks"][0]["company"] = (
        "微软 Microsoft Reports Strong Growth"
    )

    with pytest.raises(ValidationError, match="company_registered_name"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    "path",
    [
        ("title_zh",),
        ("summary_zh",),
        ("headline_summary",),
        ("market_summary",),
        ("dominant_events", 0, "summary"),
        ("dominant_events", 0, "affected_sectors", 0),
        ("market_uncertainties", 0),
        ("affected_sectors", 0),
        ("focus_ticker_assessments", 0, "summary"),
        ("focus_ticker_assessments", 0, "risks", 0),
    ],
)
def test_every_market_focus_natural_language_field_uses_the_simplified_gate(path):
    result = _market_focus_result()
    _replace_nested_value(result, path, "市場消息彙整")
    with pytest.raises(ValidationError, match="traditional_chinese_not_allowed"):
        validate_result(
            "market_focus",
            json.dumps(result, ensure_ascii=False),
            _market_focus_payload(),
        )


def test_chinese_text_allows_tickers_and_necessary_foreign_proper_names():
    result = _news_result()
    result["title_zh"] = "英伟达（NVIDIA）发布Blackwell芯片"
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )
    assert "NVIDIA" in validated["title_zh"]


@pytest.mark.parametrize(
    "title",
    [
        "著名公司发布新品",
        "乾照光电发布业绩公告",
        "覆盘显示芯片需求回升",
        "研究覆盖主要半导体公司",
    ],
)
def test_unihan_self_mapped_characters_remain_valid_in_simplified_contexts(title):
    result = _news_result()
    result["title_zh"] = title
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )
    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    "mixed_prose",
    [
        "Breaking 苹果公司发布新品",
        "Breaking苹果公司发布新品",
        "Update 苹果公司发布新品",
        "苹果公司 reports 新品",
        "英伟达launches新品",
        "新闻称 shares rose after earnings",
        "英伟达 reports strong growth now",
        "NVIDIA launches new chip 新品",
        "英伟达 launches chip",
        "IonQ reports stronger revenue，市场关注",
        "Varonis Systems shares rose after earnings，市场关注",
        "NASCAR and Goodyear expand DEI partnership，市场关注",
        "Markets Rally After Earnings 苹果",
        "Company Reports Strong Growth 苹果",
        "Report 苹果发布新品",
        "Breaking NVIDIA新品",
        "NVIDIA Reports Stronger Revenue，苹果公司表示需求改善",
        "苹果Launches New Chip",
        "REPORTS 苹果发布新品",
        "LAUNCHES NVIDIA新品",
        "AI Business Expands Rapidly 苹果",
        "RALLY 苹果",
        "RESULTS 苹果",
        "IonQ Announces Quantum Partnership 苹果",
        "Apple Raises Guidance，市场持续关注",
        "Stocks Fall Hard 苹果",
        "President Orders Military Attack 全球市场显著震荡",
        "market-rally 苹果",
        "strong-growth 苹果",
        "Apple Beats Estimates，市场持续关注",
        "Apple Cuts Outlook，市场持续关注",
        "General Motors Reports Results，市场持续关注",
        "ON Reports Results，市场持续关注",
        "Crypto Crash市场恐慌",
        "Trade War风险升温",
        "Bank Crisis持续蔓延",
        "Rate Shock冲击市场",
        "Dollar Soars市场承压",
        "Oil Spikes市场震荡",
        "Bonds Sink市场承压",
        "Tech Slumps科技股承压",
        "Jobs Miss降息预期升温",
        "Tariffs Loom市场担忧",
        "Fed Pauses市场上涨",
        "Trump Strikes伊朗局势升级",
        "China Retaliates市场震荡",
        "Bitcoin Crashes市场恐慌",
        "Equities Tumble市场承压",
        "CRASH ALERT市场恐慌",
        "WAR FEAR市场震荡",
        "BONDS SINK市场承压",
        "RATE SHOCK市场震荡",
        "BANK CRISIS风险升温",
        "Credit Stress继续加剧",
        "市场（investors flee quickly）持续下跌",
        "Market-Crash公司发布预警",
        "RSA Conference期间发布新研究",
        "Varonis Systems发布新研究",
        "Yaki Faitelson介绍公司战略",
        "Johnson & Johnson公司发布最新业绩",
        "Procter & Gamble公司发布最新业绩",
        "Bank of America发布最新研究",
        "Standard Chartered Bank发布最新研究",
        "The Trade Desk发布最新业绩",
        "Global Payments公司发布最新业绩",
        "Taiwan Semiconductor Manufacturing公司发布最新业绩",
        "Market's发布最新消息",
        "Company's发布公告",
        "Stock's推动股价上涨",
        "Report's发布最新消息",
        "Apple Inc.发布最新业绩",
        "Foo Corp发布最新业绩",
        "Market Inc.发布最新消息",
        "Report LLC发布最新消息",
        "Company Corp发布最新消息",
        "MARKET/RALLY市场关注度上升",
        "WAR/FEAR市场震荡",
        "BANK.CRISIS风险升温",
        "foobar用于治疗相关疾病",
        "investors治疗相关疾病",
        "Yaki-Faitelson介绍公司战略",
        "Johnson-Johnson公司发布最新业绩",
        "Procter-Gamble公司发布最新业绩",
        "Credit-Stress继续加剧",
        "Investors-Flee市场下跌",
        "PANIC/SELL市场恐慌",
        "BANK.RUN引发担忧",
        "RATE.CUT推动股市上涨",
        "JOB.LOSS拖累消费",
        "RISK.SHIFT改变资金流向",
        "CASH/CRUNCH冲击企业",
        "BULL/BEAR分歧扩大",
        "risk-off交易升温",
        "credit-crunch持续加剧",
        "investor-panic继续蔓延",
        "dollar-strength压制黄金",
        "rate-cut推动股市上涨",
        "bond-yields继续攀升",
        "economic-slowdown正在恶化",
        "Apple-Inc公司发布最新业绩",
        "Foo-Corp发布最新业绩",
        "Acme-LLC发布最新业绩",
        "PANIC-2026市场恐慌",
        "RISK 2026市场关注",
        "BOOM-2026推动股价上涨",
        "Investor 2026继续影响市场",
        "SELL-2026信号出现",
        "Market.rally市场上涨",
        "Investors.flee市场下跌",
        "Company.reports公司发布业绩",
        "GPT-5-market-rally市场上涨",
        "F-35-crash市场恐慌",
        "COVID-19-investors-flee市场下跌",
        "Python-3-market-rally市场上涨",
        "iPhone17crash2026推动市场上涨",
        "Aapl股价上涨",
        "Panic股价上涨",
        "Investors市场恐慌",
        "Bonds市场承压",
        "Inflation推动利率上升",
        "Recession风险升温",
        "Investors、Flee与Quickly推动市场下跌",
        "Officials、Analysts与Investors表示市场下跌",
        "ＰＡＮＩＣ股价上涨",
        "ᴾᴬᴺᴵᶜ股价上涨",
        "𝐏𝐀𝐍𝐈𝐂股价上涨",
        "Ｍａｒｋｅｔｓ ｒａｌｌｙ市场上涨",
        "𝐌𝐚𝐫𝐤𝐞𝐭𝐬 𝐫𝐚𝐥𝐥𝐲市场上涨",
        "ＭＡＲＫＥＴ／ＲＡＬＬＹ市场上涨",
        "NvDa股价上涨",
        "PaNic股价上涨",
        "PANic股价上涨",
        "RateCut推动股市上涨",
        "InVestOrs市场恐慌",
        "InFlation推动利率上升",
        "ⓅⒶⓃⒾⒸ股价上涨",
        "РЫНОК РАСТЕТ市场上涨",
        "マーケット上昇，市场上涨",
        "株式会社任天堂发布财报",
        "任天堂株式会社发布财报",
        "㈱任天堂发布财报",
        "㍿任天堂发布财报",
        "任天堂売上高增长",
        "株価上昇，市场关注",
        "Tesla发布最新业绩",
        "苹果与Tesla合作扩大供应",
        "Broadcom发布最新业绩",
        "Intel发布最新业绩",
        "H100 Markets需求增长",
    ],
)
def test_chinese_text_rejects_english_fragments(mixed_prose):
    result = _news_result()
    result["title_zh"] = mixed_prose
    with pytest.raises(
        ValidationError,
        match="english_prose_not_allowed|non_chinese_script_not_allowed",
    ):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_chinese_text_allows_multiple_tickers_and_a_short_proper_name():
    result = _news_result()
    result["title_zh"] = "NVDA、AMD与TSM关注Blackwell供货"
    payload = _news_payload()
    payload["allowed_tickers"] = ["NVDA", "AMD", "TSM"]
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )
    assert validated["title_zh"].startswith("NVDA")


@pytest.mark.parametrize(
    "title",
    [
        "英特尔18A制程的采用仍待量产数据验证",
        "特斯拉业绩前瞻：自动驾驶出租车与Optimus能否成为亮点",
        "财务总监出售5万股A类普通股",
        "企业完成B轮融资",
        "企业完成B 轮融资",
        "公司发行C类普通股",
        "H股市场回暖",
        "临床试验显示初始B细胞比例改善",
        "房地产REITs估值仍受利率影响",
        "成交量Z分数为0.62",
        "未提供VWAP及其对应周期",
        "缺少原始OHLC序列",
    ],
)
def test_chinese_text_allows_structural_product_and_share_class_names(title):
    result = _news_result()
    result["title_zh"] = title

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )

    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    ("title", "source_title", "source_summary", "source_ticker_hints"),
    [
        (
            "UFC任命Meridian Holdings为赛事官方赞助商",
            "UFC Names Meridian Holdings Official Sponsor",
            "Subsidiary Meridianbet will sponsor the event.",
            ["MRDN"],
        ),
        (
            "报道称GameStop将所持eBay股份提高至10%",
            "GameStop raises eBay stake to 10%",
            None,
            ["GME", "EBAY"],
        ),
        (
            "评论文章审视SpaceX股票的估值风险",
            "Three quotes to read before buying SpaceX Stock",
            None,
            ["SPCX"],
        ),
        (
            "Hims & Hers宣布首席会计官离职",
            "Hims & Hers announces departure of chief accounting officer",
            None,
            ["HIMS"],
        ),
        (
            "黄仁勋在CES 2026表示内存已成为主要瓶颈",
            "Jensen Huang Told CES 2026 That Memory Is the Biggest Bottleneck",
            None,
            ["NVDA"],
        ),
        (
            "Vanguard Mega Cap Growth ETF与标普500成长基金比较",
            "Vanguard Mega Cap Growth ETF or S&P 500 Growth ETF",
            None,
            ["MGK", "VOOG"],
        ),
        (
            "报道比较Vanguard S&P 500 Growth ETF与Vanguard Mega Cap Growth ETF的分散程度",
            "Vanguard S&P 500 Growth ETF or Vanguard Mega Cap Growth ETF",
            None,
            ["MGK", "VOOG"],
        ),
        (
            "VOOG持有148只证券，MGK持有56只证券",
            "Vanguard Growth ETF comparison",
            None,
            ["VOOG", "MGK"],
        ),
        (
            "IonQ 2025年营收较管理层指引高出20%",
            "IonQ's Revenue Just Tripled",
            "IonQ's 2025 revenue came in above guidance.",
            [],
        ),
        (
            "nookplot-runtime 0.5.75发布智能体运行时更新",
            "nookplot-runtime 0.5.75",
            "Python Agent Runtime SDK for Nookplot",
            [],
        ),
        (
            "公司称其获得五项Global InfoSec Awards",
            "RSA Conference 2026",
            "The company won five Global InfoSec Awards at the event.",
            [],
        ),
        (
            "Robotaxi项目仍缺乏可验证的运营数据",
            "How important are Robotaxis to the company's future?",
            None,
            [],
        ),
        (
            "公司正在开发LPU等人工智能处理器",
            "AI infrastructure update",
            "Product development includes GPUs, CPUs, and LPUs.",
            [],
        ),
        (
            "美国股息股票ETF在2026年回报约20%",
            "This Dividend ETF Yields 3.2%",
            None,
            ["SCHD"],
        ),
        (
            "IBM的Z大型机业务表现疲弱",
            "IBM Z Mainframe Shortfall",
            "The IBM Z mainframe business underperformed.",
            ["IBM"],
        ),
    ],
)
def test_news_text_allows_source_bound_registered_entities(
    title,
    source_title,
    source_summary,
    source_ticker_hints,
):
    result = _news_result()
    result["title_zh"] = title
    payload = _news_payload()
    payload.update(
        {
            "title": source_title,
            "summary": source_summary,
            "source_ticker_hints": source_ticker_hints,
            "allowed_tickers": list(
                dict.fromkeys(["NVDA", *source_ticker_hints])
            ),
        }
    )

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    "source_title",
    [
        "UFC Names Meridian Holdings Official Sponsor",
        "Apple Beats Estimates",
        "NVIDIA Launches Blackwell Platform",
        "Rocket Lab Wins NASA Contract",
    ],
)
def test_source_binding_does_not_allow_copied_english_prose(source_title):
    result = _news_result()
    result["title_zh"] = f"市场消息：{source_title}"
    payload = _news_payload()
    payload["title"] = source_title

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


@pytest.mark.parametrize(
    ("copied_fragment", "source_title"),
    [
        (
            "Tesla Tops Forecasts",
            "Tesla Tops Forecasts as Margins Improve",
        ),
        (
            "NVIDIA Launches Blackwell Platform",
            "NVIDIA Launches Blackwell Platform Today",
        ),
        (
            "Rocket Lab Wins",
            "Rocket Lab Wins NASA Contract",
        ),
        (
            "Tesla CEO Tops Forecasts",
            "Tesla CEO Tops Forecasts as Margins Improve",
        ),
        (
            "Apple Q3 Earnings",
            "Apple Q3 Earnings Preview",
        ),
        (
            "Apple Q3 Earnings",
            "Apple Q3 Earnings (AAPL)",
        ),
    ],
)
def test_source_binding_rejects_copied_headline_fragments(
    copied_fragment,
    source_title,
):
    result = _news_result()
    result["title_zh"] = f"{copied_fragment}消息影响市场"
    payload = _news_payload()
    payload["title"] = source_title

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


def test_source_binding_keeps_a_structural_multiword_entity_fragment():
    result = _news_result()
    result["title_zh"] = "The Trade Desk平台发布业务更新"
    payload = _news_payload()
    payload["title"] = "The Trade Desk platform reports stronger demand"

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"].startswith("The Trade Desk平台")


@pytest.mark.parametrize(
    ("title", "source_title", "source_summary"),
    [
        (
            "Varonis CEO Yaki Faitelson将发表主题演讲",
            "Varonis CEO Yaki Faitelson to Deliver Keynote",
            None,
        ),
        (
            "Pershing Square USA是一只封闭式基金",
            "Bill Ackman's New Closed-End Fund Trades Below Its IPO Price",
            "Pershing Square USA, Bill Ackman's closed-end fund, trades at a discount.",
        ),
        (
            "Pershing Square USA出现资产净值折价",
            "Bill Ackman's New Closed-End Fund Trades Below Its IPO Price",
            "Pershing Square USA is a closed-end fund that trades at a discount.",
        ),
        (
            "4D Molecular Therapeutics公布两年临床试验结果",
            "4DMT Announces Positive 2-Year Data from PRISM Phase 2b Clinical Trial",
            "4D Molecular Therapeutics (4DMT) announced positive 2-year results.",
        ),
    ],
)
def test_source_binding_keeps_a_role_subject_or_defined_entity(
    title,
    source_title,
    source_summary,
):
    result = _news_result()
    result["title_zh"] = title
    payload = _news_payload()
    payload["title"] = source_title
    payload["summary"] = source_summary

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"] == title


def test_source_binding_does_not_bind_a_company_name_to_an_unrelated_ticker():
    result = _news_result()
    result["headline_summary"] = "Apple股票上涨受到市场关注"
    payload = _news_payload()
    payload["title"] = "Apple outlook improves"
    payload["allowed_tickers"] = ["NVDA"]

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


@pytest.mark.parametrize(
    "source_title",
    [
        "Apple supplier shares rise",
        "Apple supplier's shares rise",
        "Apple says supplier shares rise",
    ],
)
def test_source_binding_does_not_cross_into_another_noun_phrase(source_title):
    result = _news_result()
    result["headline_summary"] = "Apple股票上涨受到市场关注"
    payload = _news_payload()
    payload["title"] = source_title
    payload["allowed_tickers"] = ["NVDA"]

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


@pytest.mark.parametrize(
    ("entity", "source_title"),
    [
        ("Apple", "Apple shares rise"),
        ("Apple", "Apple's stock rises"),
        ("PayPal", "PayPal among gainers"),
        ("MGK", "MGK's 56 holdings"),
        ("Sandisk", "Sandisk Have Outperformed Nvidia's Stock"),
    ],
)
def test_source_binding_accepts_immediate_security_context(entity, source_title):
    result = _news_result()
    result["headline_summary"] = f"{entity}股票上涨受到市场关注"
    payload = _news_payload()
    payload["title"] = source_title
    payload["allowed_tickers"] = ["NVDA"]

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["headline_summary"].startswith(f"{entity}股票")


@pytest.mark.parametrize(
    ("title", "source_title"),
    [
        (
            "凯茜·伍德卖出一只年内大涨的合成DNA股票",
            "Cathie Wood sells a synthetic DNA manufacturer's stock",
        ),
        (
            "基因RNA股票受到市场关注",
            "The genetic RNA technology market is expanding",
        ),
        (
            "方舟旗下ETF卖出一家合成DNA企业的股份",
            "Ark sold the synthetic DNA manufacturer's stock",
        ),
    ],
)
def test_source_bound_technical_initialism_can_describe_a_stock_category(
    title,
    source_title,
):
    result = _news_result()
    result["title_zh"] = title
    payload = _news_payload()
    payload["title"] = source_title
    payload["allowed_tickers"] = ["NVDA"]

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    ("title", "source_title"),
    [
        ("DNA股价上涨", "A synthetic DNA manufacturer's stock rises"),
        ("DNA股票上涨", "A synthetic DNA manufacturer's stock rises"),
        ("合成DNA企业股价上涨", "A synthetic DNA manufacturer's stock rises"),
        ("关注CAT股票上涨", "CAT manufacturer shares rise"),
    ],
)
def test_source_bound_technical_term_cannot_impersonate_a_ticker(
    title,
    source_title,
):
    result = _news_result()
    result["title_zh"] = title
    payload = _news_payload()
    payload["title"] = source_title
    payload["allowed_tickers"] = ["NVDA"]

    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


def test_source_binding_requires_exact_token_boundaries():
    result = _news_result()
    result["headline_summary"] = "SAP公司产品进展受到市场关注"
    payload = _news_payload()
    payload["title"] = "WhatsApp product update"
    payload["summary"] = "WhatsApp expanded its messaging features."
    payload["allowed_tickers"] = []
    payload["source_ticker_hints"] = []

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


def test_source_binding_does_not_guess_a_nearby_ticker_for_a_company_name():
    result = _news_result()
    result["headline_summary"] = "SpaceX股票估值受到市场关注"
    payload = _news_payload()
    payload["title"] = "SpaceX expands launch operations"
    payload["allowed_tickers"] = ["NVDA"]
    payload["source_ticker_hints"] = ["SPCX"]

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


@pytest.mark.parametrize("source_ticker_hint", ["CAT", "ON", "00700"])
def test_source_ticker_hints_do_not_authorize_uncanonical_codes(
    source_ticker_hint,
):
    result = _news_result()
    result["title_zh"] = f"{source_ticker_hint}股价上涨"
    payload = _news_payload()
    payload["allowed_tickers"] = ["NVDA"]
    payload["source_ticker_hints"] = [source_ticker_hint]

    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


def test_source_bound_ticker_still_requires_an_exact_code_match():
    result = _news_result()
    result["headline_summary"] = "SAP股票上涨受到市场关注"
    payload = _news_payload()
    payload["title"] = "SAP outlook improves"
    payload["allowed_tickers"] = ["NVDA", "SAP"]

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["headline_summary"].startswith("SAP")


@pytest.mark.parametrize(
    ("text", "source_title"),
    [
        (
            "针对AeroVironment, Inc.的证券集体诉讼已被提起",
            "AeroVironment, Inc. Investors Securities Class Action - AVAV",
        ),
        (
            "针对Zoetis Inc.的证券欺诈集体诉讼已被提起",
            "Zoetis Inc. Investors Securities Fraud Class Action - ZTS",
        ),
    ],
)
def test_source_bound_legal_names_are_not_mistaken_for_stock_references(
    text,
    source_title,
):
    result = _news_result()
    result["headline_summary"] = text
    payload = _news_payload()
    payload["title"] = source_title

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["headline_summary"] == text


def test_news_text_allows_a_source_bound_publisher_name():
    result = _news_result()
    result["title_zh"] = "Seeking Alpha提问贸易协议变化的行业影响"
    payload = _news_payload()
    payload["source"] = "seekingalpha/Seeking Alpha"

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"].startswith("Seeking Alpha")


def test_news_text_allows_a_compact_source_identifier_as_publisher_name():
    result = _news_result()
    result["title_zh"] = "Seeking Alpha提问行业前景"
    payload = _news_payload()
    payload["source"] = "seekingalpha/breaking"
    payload["sources"] = ["seekingalpha/breaking"]
    payload["title"] = "SA Asks: What is the industry outlook?"

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"].startswith("Seeking Alpha")


@pytest.mark.parametrize(
    "title",
    [
        "盈透证券2026年客户账户与客户权益继续增长",
        "首席财务官出售3,982股，价值约45.7万美元",
        "公司出售股票3,982股，价值约45.7万美元",
        "公司出售股票3，982股，价值约45.7万美元",
        "股票45.7美元",
        "股票45．7美元",
        "该股过去一年下跌66%，公司曾出现收入下滑",
    ],
)
def test_years_and_formatted_share_counts_are_not_security_codes(title):
    result = _news_result()
    result["title_zh"] = title

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload(),
    )

    assert validated["title_zh"] == title


def test_chinese_text_rejects_a_ticker_not_bound_to_the_job_payload():
    result = _news_result()
    result["title_zh"] = "PANIC股价出现明显波动"
    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize(
    "text",
    [
        "ZZZZ今日上涨",
        "ZZZZ公司发布财报",
        "HELLO WORLD正在发生",
    ],
)
def test_contextual_initialisms_do_not_bypass_ticker_or_language_binding(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        validate_simplified_chinese_text(
            text,
            None,
            allowed_codes=["MSFT"],
        )


@pytest.mark.parametrize(
    "unbound_reference",
    [
        "NVIDIA股价出现波动",
        "Apple股票受到市场关注",
        "Apple的股票受到市场关注",
        "NVIDIA（英伟达）的今日股价出现波动",
        "Apple （苹果）股票受到市场关注",
        "NVIDIA公司股价出现波动",
        "Apple（苹果）这只股票受到市场关注",
        "股票代码：Apple受到关注",
        "证券代码：NVIDIA受到关注",
        "股票代码（Apple）受到关注",
        "证券编号为Apple受到关注",
        "Apple/股票受到市场关注",
        "NVIDIA·股价出现波动",
        "Apple【苹果】股票受到市场关注",
        "Apple​股票受到市场关注",
        "Apple／股票受到市场关注",
        "股票代码／Apple受到关注",
        "Apple〔苹果〕股票受到市场关注",
        "Apple（ 苹果）股票受到市场关注",
        "Apple⁣股票受到市场关注",
        "NVIDIA股票市场表现强劲",
        "Apple股票策略获得关注",
        "NVIDIA证券披露文件显示风险",
        "Apple股票基金出现上涨",
        "Apple公司（苹果）股价出现波动",
        "Apple（股票上涨）受到市场关注",
        "Apple（股价上涨）受到市场关注",
        "Apple（苹果股票上涨）受到市场关注",
        "Apple股／票受到市场关注",
        "股／票代码：Apple受到关注",
        "Apple公／司股／价出现波动",
        "Apple（股／票上涨）受到市场关注",
        "Apple\u034f股票受到市场关注",
        "Apple\ufe0f股票受到市场关注",
        "Apple\x00股票受到市场关注",
        "Apple\ue000股票受到市场关注",
        "Apple（苹果）\ufe0f股票受到市场关注",
        "Tesla（股价上涨）受到关注",
        "公司（Tesla）股价上涨",
        'Apple"苹果"股票受到市场关注',
        "Apple＂苹果＂股票受到市场关注",
        "Apple'苹果'股票受到市场关注",
        "Apple`苹果`股票受到市场关注",
        "Apple的股份上涨",
        "Apple公司股份上涨",
        "Apple普通股上涨",
        "Apple\n股票受到市场关注",
        "Apple。股票受到市场关注",
        "Apple\u0660股票受到市场关注",
        "Microsoft。股票受到市场关注",
    ],
)
def test_news_result_rejects_stock_context_for_an_unbound_approved_brand(
    unbound_reference,
):
    result = _news_result()
    result["affected_stocks"] = []
    result["headline_summary"] = unbound_reference
    payload = _news_payload()
    payload["allowed_tickers"] = []

    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


@pytest.mark.parametrize(
    "unbound_reference",
    [
        "NVIDIA股价出现波动",
        "Apple股票受到市场关注",
        "Apple的股票受到市场关注",
        "NVIDIA（英伟达）的今日股价出现波动",
        "Apple （苹果）股票受到市场关注",
        "NVIDIA公司股价出现波动",
        "Apple（苹果）这只股票受到市场关注",
        "股票代码：Apple受到关注",
        "证券代码：NVIDIA受到关注",
        "股票代码（Apple）受到关注",
        "证券编号为Apple受到关注",
        "Apple/股票受到市场关注",
        "NVIDIA·股价出现波动",
        "Apple【苹果】股票受到市场关注",
        "Apple​股票受到市场关注",
        "Apple／股票受到市场关注",
        "股票代码／Apple受到关注",
        "Apple〔苹果〕股票受到市场关注",
        "Apple（ 苹果）股票受到市场关注",
        "Apple⁣股票受到市场关注",
        "NVIDIA股票市场表现强劲",
        "Apple股票策略获得关注",
        "NVIDIA证券披露文件显示风险",
        "Apple股票基金出现上涨",
        "Apple公司（苹果）股价出现波动",
        "Apple（股票上涨）受到市场关注",
        "Apple（股价上涨）受到市场关注",
        "Apple（苹果股票上涨）受到市场关注",
        "Apple股／票受到市场关注",
        "股／票代码：Apple受到关注",
        "Apple公／司股／价出现波动",
        "Apple（股／票上涨）受到市场关注",
        "Apple\u034f股票受到市场关注",
        "Apple\ufe0f股票受到市场关注",
        "Apple\x00股票受到市场关注",
        "Apple\ue000股票受到市场关注",
        "Apple（苹果）\ufe0f股票受到市场关注",
        "Tesla（股价上涨）受到关注",
        "公司（Tesla）股价上涨",
        'Apple"苹果"股票受到市场关注',
        "Apple＂苹果＂股票受到市场关注",
        "Apple'苹果'股票受到市场关注",
        "Apple`苹果`股票受到市场关注",
        "Apple的股份上涨",
        "Apple公司股份上涨",
        "Apple普通股上涨",
        "Apple\n股票受到市场关注",
        "Apple。股票受到市场关注",
        "Apple\u0660股票受到市场关注",
        "Microsoft。股票受到市场关注",
    ],
)
def test_market_focus_rejects_stock_context_for_an_unbound_approved_brand(
    unbound_reference,
):
    result = _market_focus_result()
    result["focus_ticker_assessments"] = []
    result["headline_summary"] = unbound_reference
    payload = _market_focus_payload(allowed_tickers=[])

    with pytest.raises(ValidationError):
        validate_result(
            "market_focus",
            json.dumps(result, ensure_ascii=False),
            payload,
        )


@pytest.mark.parametrize(
    "title",
    [
        "CRM股价上涨",
        "AI股价上涨",
        "API股价上涨",
        "IPO股价上涨",
        "VIX股价上涨",
        "LNG股价上涨",
        "ADP股价上涨",
        "股票CRM上涨",
        "赛富时（CRM）股价上涨",
        "CRM（赛富时）股价上涨",
        "市场关注CRM这只股票",
        "CRM的今日股价上涨",
        "CRM当前股价上涨",
        "CRM最新股价上涨",
        "CRM盘前股价上涨",
        "CRM这只个股上涨",
        "赛富时（CRM）的今日股价上涨",
    ],
)
def test_common_abbreviation_in_security_context_still_requires_job_binding(
    title,
):
    result = _news_result()
    result["title_zh"] = title
    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_project_security_code_can_be_used_when_bound_to_the_job():
    result = _news_result()
    result["title_zh"] = "CRM股价上涨"
    payload = _news_payload()
    payload["allowed_tickers"] = ["CRM", "NVDA"]
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )
    assert validated["title_zh"] == "CRM股价上涨"


@pytest.mark.parametrize(
    "title",
    [
        "00700股价上涨",
        "1234股票上涨",
        "2026公司发布业绩",
        "9999个股走强",
        "123证券受到关注",
        "００７００股价上涨",
        "𝟘𝟘𝟟𝟘𝟘股价上涨",
        "腾讯（00700）股价上涨",
        "股票代码为00700",
        "股票代码为00700，受到关注",
        "股票代码为1234,受到关注",
        "股票代码为00700.受到关注",
        "股票代码为00700．受到关注",
        "股票1234受到关注",
        "证券1234受到关注",
        "腾讯的股票代码是00700",
        "市场关注00700这只股票",
        "腾讯证券编号为00700",
        "00700上涨",
        "００７００上涨",
        "𝟘𝟘𝟟𝟘𝟘受到关注",
        "600519上涨",
        "６００５１９走强",
        "600519，股价上涨",
        "600519,股价上涨",
        "A股，600519股价上涨",
        "A股,600519股价上涨",
        "A股价上涨",
        "A股票上涨",
        "F股上涨",
        "C股下跌",
        "股票代码／1234受到关注",
        "股／票代码：1234受到关注",
    ],
)
def test_numeric_and_single_letter_security_codes_require_job_binding(title):
    result = _news_result()
    result["title_zh"] = title
    with pytest.raises(ValidationError):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


@pytest.mark.parametrize("ticker", ["00700", "A"])
def test_numeric_and_single_letter_security_codes_are_allowed_when_bound(ticker):
    result = _news_result()
    result["title_zh"] = f"{ticker}股价上涨"
    payload = _news_payload()
    payload["allowed_tickers"] = ["NVDA", ticker]
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )
    assert validated["title_zh"] == f"{ticker}股价上涨"


@pytest.mark.parametrize(
    "title",
    [
        "股票代码为00700，受到关注",
        "股票代码为00700,受到关注",
        "股票代码为00700.受到关注",
        "股票代码为00700．受到关注",
        "00700，股价上涨",
        "00700,股价上涨",
    ],
)
def test_numeric_security_code_before_comma_is_allowed_when_bound(title):
    result = _news_result()
    result["title_zh"] = title
    payload = _news_payload()
    payload["allowed_tickers"] = ["NVDA", "00700"]

    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    "title",
    [
        "600519上涨",
        "600519，股价上涨",
        "600519,股价上涨",
        "股票600519受到关注",
        "A股，600519股价上涨",
        "A股,600519股价上涨",
    ],
)
def test_six_digit_security_code_is_allowed_when_bound(title):
    result = _news_result()
    result["title_zh"] = title
    payload = _news_payload()
    payload["allowed_tickers"] = ["NVDA", "600519"]
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )
    assert validated["title_zh"] == title


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("news_id", 2),
        ("change_sequence", 8),
        ("content_hash", "different-content"),
    ],
)
def test_news_result_is_bound_to_the_exact_local_revision(field, value):
    result = _news_result()
    result[field] = value
    with pytest.raises(ValueError, match="news_identity_mismatch"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_news_result_rejects_a_ticker_not_verified_by_local_code():
    result = _news_result()
    result["affected_stocks"][0]["ticker"] = "AAPL"
    with pytest.raises(ValueError, match="news_ticker_binding_mismatch"):
        validate_result(
            "news_impact",
            json.dumps(result, ensure_ascii=False),
            _news_payload(),
        )


def test_news_result_allows_an_empty_verified_ticker_set():
    result = _news_result()
    result["affected_stocks"] = []
    validated = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _news_payload() | {"allowed_tickers": []},
    )
    assert validated["affected_stocks"] == []


def test_market_focus_rejects_unlisted_event_ticker_and_input_hash():
    event_result = _market_focus_result()
    event_result["dominant_events"][0]["event_group_id"] = "invented-event"
    with pytest.raises(ValueError, match="market_focus_event_binding_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(event_result, ensure_ascii=False),
            _market_focus_payload(),
        )

    ticker_result = _market_focus_result()
    ticker_result["focus_ticker_assessments"][0]["ticker"] = "AAPL"
    with pytest.raises(ValueError, match="market_focus_ticker_binding_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(ticker_result, ensure_ascii=False),
            _market_focus_payload(),
        )

    hash_result = _market_focus_result()
    hash_result["input_hash"] = "b" * 64
    with pytest.raises(ValueError, match="market_focus_input_hash_mismatch"):
        validate_result(
            "market_focus",
            json.dumps(hash_result, ensure_ascii=False),
            _market_focus_payload(),
        )


def test_runtime_uses_fixed_model_reasoning_background_and_per_task_limits(tmp_path):
    settings = _settings(tmp_path / "ai-jobs.db")
    for job_type, expected_tokens in runtime.AI_TASK_MAX_OUTPUT_TOKENS.items():
        params = runtime._create_params(settings, job_type, _payload_for(job_type))
        assert params["model"] == "gpt-5.6-terra"
        assert params["reasoning"] == {"effort": "max"}
        assert params["background"] is True
        assert params["store"] is True
        assert params["max_output_tokens"] == expected_tokens
        assert params["text"]["format"]["strict"] is True
    assert runtime.max_output_tokens_for("news_impact") == 32_768
    assert runtime.max_output_tokens_for("market_focus") == 49_152


def test_runtime_policy_changes_schema_identity(monkeypatch):
    _, before = runtime.schema_identity("news_impact")
    monkeypatch.setitem(
        runtime.AI_TASK_MAX_OUTPUT_TOKENS,
        "news_impact",
        runtime.max_output_tokens_for("news_impact") + 1,
    )
    _, after = runtime.schema_identity("news_impact")
    assert after != before


def test_daily_submission_count_is_not_capped_at_four(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    jobs = [_create_job(repository, ticker) for ticker in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    for index, job in enumerate(jobs[:4]):
        owner = f"owner-{index}"
        claimed = repository.claim_due(owner, 60)
        assert claimed["job_id"] == job["job_id"]
        assert repository.mark_submission_started(
            job["job_id"], owner, daily_limit=100, daily_budget_usd=100
        ) == "started"
        repository.fail(job["job_id"], owner, "provider_failed")

    owner = "owner-limit"
    claimed = repository.claim_due(owner, 60)
    assert claimed["job_id"] == jobs[4]["job_id"]
    assert repository.mark_submission_started(
        jobs[4]["job_id"], owner, daily_limit=100, daily_budget_usd=100
    ) == "started"


def test_daily_token_limit_is_atomic(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")

    first_owner = "token-owner-one"
    assert repository.claim_due(first_owner, 60)["job_id"] == first["job_id"]
    assert repository.mark_submission_started(
        first["job_id"],
        first_owner,
        daily_token_limit=200_000,
    ) == "started"
    repository.link_background_response(
        first["job_id"],
        first_owner,
        "resp_atomic_token_limit",
    )
    repository.fail(first["job_id"], first_owner, "provider_failed")

    second_owner = "token-owner-two"
    assert repository.claim_due(second_owner, 60)["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"],
        second_owner,
        daily_token_limit=200_000,
    ) == "daily_token_limit"
    blocked = repository.get_job(second["job_id"])
    assert blocked["status"] == "budget_blocked"
    assert blocked["error_code"] == "daily_token_limit_reached"
    assert blocked["submission_started_at"] is None


def test_global_concurrency_limit_defers_a_second_paid_submission(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")

    pending_snapshot = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
    )
    assert pending_snapshot["concurrency_available"] is True
    assert pending_snapshot["active_job"] is None

    first_claim = repository.claim_due("owner-one", 60)
    assert first_claim["job_id"] == first["job_id"]
    assert repository.mark_submission_started(
        first["job_id"], "owner-one", daily_limit=4
    ) == "started"
    active_snapshot = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
    )
    assert active_snapshot["concurrency_available"] is False
    assert active_snapshot["active_job"]["job_id"] == first["job_id"]

    second_claim = repository.claim_due("owner-two", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"], "owner-two", daily_limit=4
    ) == "concurrency_limit"
    deferred = repository.get_job(second["job_id"])
    assert deferred["status"] == "pending"
    assert deferred["error_code"] == "global_concurrency_limit"
    assert deferred["submission_started_at"] is None


def test_claim_due_polls_submitted_response_before_older_pending_backlog(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    pending = [_create_job(repository, f"P{index:02d}") for index in range(12)]
    submitted = _create_job(repository, "LIVE")

    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='queued',
                submission_started_at='2026-07-23T12:00:00Z',
                submitted_at='2026-07-23T12:00:00Z',
                openai_response_id='resp_live_poll',
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (submitted["job_id"],),
        )
        connection.execute(
            """
            UPDATE ai_jobs
            SET next_attempt_at='1970-01-01T00:00:00Z',
                error_code='global_concurrency_limit'
            WHERE submission_started_at IS NULL
            """
        )
        connection.commit()

    claimed = repository.claim_due("poll-owner", 60)

    assert claimed["job_id"] == submitted["job_id"]
    assert claimed["openai_response_id"] == "resp_live_poll"
    assert all(item["job_id"] != claimed["job_id"] for item in pending)


def test_recent_unknown_submission_holds_the_global_concurrency_slot(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")

    first_claim = repository.claim_due("owner-one", 60)
    assert repository.mark_submission_started(
        first["job_id"], "owner-one", daily_limit=4
    ) == "started"
    repository.fail(
        first_claim["job_id"],
        "owner-one",
        "submission_outcome_unknown",
    )

    second_claim = repository.claim_due("owner-two", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"], "owner-two", daily_limit=4
    ) == "concurrency_limit"
    health = repository.health()
    assert health["submission_unknown"] == 1
    snapshot = repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=2.0,
    )
    assert snapshot["concurrency_available"] is False
    assert snapshot["active_job"]["error_code"] == "submission_outcome_unknown"
    assert snapshot["token_budget_used_tokens"] == runtime.token_reservation(
        "earnings_impact"
    )
    assert repository.get_job(first["job_id"])["budget_charge_microusd"] == (
        runtime.budget_reservation_microusd("earnings_impact")
    )


def test_expired_unknown_submission_releases_other_jobs_without_retrying_it(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")

    first_claim = repository.claim_due("owner-one", 60)
    assert repository.mark_submission_started(
        first["job_id"], "owner-one", daily_limit=4
    ) == "started"
    repository.fail(
        first_claim["job_id"],
        "owner-one",
        "submission_outcome_unknown",
    )
    recorded_after_restart = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """UPDATE ai_jobs
               SET submission_started_at='2026-01-01T00:00:00Z',
                   completed_at=?,
                   updated_at=?
               WHERE job_id=?""",
            (recorded_after_restart, recorded_after_restart, first["job_id"]),
        )

    snapshot = repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=2.0,
        unknown_submission_hold_seconds=86400,
    )
    assert snapshot["concurrency_available"] is True
    assert snapshot["active_job"] is None

    second = _create_job(repository, "BBB")
    second_claim = repository.claim_due("owner-two", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"],
        "owner-two",
        daily_limit=4,
        unknown_submission_hold_seconds=86400,
    ) == "started"
    preserved = repository.get_job(first["job_id"])
    assert preserved["status"] == "failed"
    assert preserved["error_code"] == "submission_outcome_unknown"
    assert preserved["openai_response_id"] is None
    assert preserved["attempt_count"] == 1


def test_local_preflight_failure_does_not_consume_budget_or_become_unknown(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row = _create_job(repository, "AAA")
    settings = _settings(tmp_path / "ai-jobs.db")

    def fail_preflight(*_args, **_kwargs):
        raise RuntimeError("ai_sdk_unavailable")

    monkeypatch.setattr(runtime, "prepare_background", fail_preflight)
    owner = "preflight-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    failed = repository.get_job(row["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "ai_sdk_unavailable"
    assert failed["submission_started_at"] is None


def test_explicit_provider_rejection_does_not_create_an_unknown_submission_lock(
    monkeypatch,
    tmp_path,
):
    class ProviderRejected(RuntimeError):
        status_code = 401

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_job(repository, "AAA")
    second = _create_job(repository, "BBB")
    settings = _settings(tmp_path / "ai-jobs.db")

    async def reject_submission(*_args, **_kwargs):
        raise ProviderRejected("authentication rejected")

    monkeypatch.setattr(runtime, "submit_background", reject_submission)
    owner = "provider-rejected-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    failed = repository.get_job(first["job_id"])
    assert failed["error_code"] == "provider_auth_failed"
    assert failed["usage_total_tokens"] == 0
    assert failed["budget_charge_microusd"] == 0
    assert repository.health()["submission_unknown"] == 0
    snapshot = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
        daily_token_limit=runtime.token_reservation("earnings_impact"),
    )
    assert snapshot["token_budget_used_tokens"] == 0
    assert snapshot["token_budget_available"] is True
    second_claim = repository.claim_due("second-owner", 60)
    assert second_claim["job_id"] == second["job_id"]
    assert repository.mark_submission_started(
        second["job_id"],
        "second-owner",
        daily_limit=4,
        daily_token_limit=runtime.token_reservation("earnings_impact"),
    ) == "started"


def test_oversized_runtime_input_fails_before_paid_capacity_is_reserved(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")
    row, _ = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAA", "raw_context": "x" * 61_000},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-zh-cn-v4",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    owner = "oversized-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(
        process_job(
            repository,
            _settings(tmp_path / "ai-jobs.db"),
            claimed,
            owner,
        )
    )

    failed = repository.get_job(row["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "ai_input_too_large"
    assert failed["submission_started_at"] is None


def test_public_job_hides_legacy_non_chinese_result(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row = _create_job(repository, "AAA")
    owner = "legacy-owner"
    claimed = repository.claim_due(owner, 60)
    legacy_result = {
        "output_language": "zh-CN",
        "ticker": "AAA",
        "summary": "Markets rally after strong earnings",
        "expectation": "Revenue and margins may rise",
        "impacted": [
            {
                "ticker": ticker,
                "name": ticker,
                "relation": "competitor",
                "direction": "mixed",
                "reason": "Public business relationship may transmit demand",
            }
            for ticker in ("BBB", "CCC", "DDD", "EEE")
        ],
    }
    repository.complete(
        claimed["job_id"],
        owner,
        legacy_result,
        {},
    )

    stored = repository.get_job(row["job_id"])
    assert "Markets rally" in stored["result_json"]
    public = repository.public(stored)
    assert public["result"] is None
    assert public["error_code"] == "legacy_output_hidden"


def test_public_job_hides_new_shape_result_when_legacy_payload_lacks_identity(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("news_impact")
    row, _ = repository.create_job(
        job_type="news_impact",
        payload={"news_id": 1},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="legacy-news",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    owner = "legacy-news-owner"
    claimed = repository.claim_due(owner, 60)
    repository.complete(claimed["job_id"], owner, _news_result(), {})

    stored = repository.get_job(row["job_id"])
    public = repository.public(stored)
    assert public["result"] is None
    assert public["error_code"] == "legacy_output_hidden"


def test_v1_database_migration_preserves_and_disables_sync_history(tmp_path):
    path = tmp_path / "ai-jobs-v1.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ai_jobs (
            job_id TEXT PRIMARY KEY,job_type TEXT NOT NULL,request_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,status TEXT NOT NULL,priority INTEGER NOT NULL,
            model TEXT NOT NULL,reasoning TEXT NOT NULL,execution_mode TEXT NOT NULL,
            prompt_version TEXT NOT NULL,schema_version TEXT NOT NULL,
            schema_sha256 TEXT NOT NULL,openai_response_id TEXT,
            submission_started_at TEXT,submitted_at TEXT,last_polled_at TEXT,
            completed_at TEXT,attempt_count INTEGER NOT NULL,poll_count INTEGER NOT NULL,
            next_attempt_at TEXT,error_code TEXT,result_json TEXT,
            usage_input_tokens INTEGER,usage_cached_input_tokens INTEGER,
            usage_output_tokens INTEGER,usage_reasoning_tokens INTEGER,
            usage_total_tokens INTEGER,cancel_requested_at TEXT,lease_owner TEXT,
            lease_expires_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO ai_jobs(
            job_id,job_type,request_hash,payload_json,status,priority,
            model,reasoning,execution_mode,prompt_version,schema_version,
            schema_sha256,attempt_count,poll_count,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "aij_legacy_sync_123",
            "earnings_impact",
            "legacy-hash",
            "{}",
            "pending",
            50,
            "gpt-5.6-terra",
            "max",
            "worker_sync",
            "earnings-v2",
            "earnings-v2",
            "legacy-digest",
            0,
            0,
            "2026-07-15T00:00:00Z",
            "2026-07-15T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    repository = AIJobRepository(path)
    repository.initialize()
    migrated = repository.get_job("aij_legacy_sync_123")
    assert migrated["status"] == "failed"
    assert migrated["execution_mode"] == "background"
    assert migrated["legacy_execution_mode"] == "worker_sync"
    assert migrated["error_code"] == "legacy_execution_mode_disabled"
    assert migrated["execution_number"] == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            SimpleNamespace(
                status="completed",
                refusal="不能处理",
                output=[],
            ),
            "provider_refusal",
        ),
        (
            SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
            "provider_incomplete_max_output_tokens",
        ),
        (SimpleNamespace(status="failed"), "provider_failed"),
        (SimpleNamespace(status="cancelled"), "provider_cancelled"),
    ],
)
def test_terminal_provider_states_have_explicit_bounded_errors(response, expected):
    assert runtime.response_terminal_error(response) == expected


def test_signal_evidence_may_reference_engine_benchmarks_and_vwap():
    """2026-08-02 生产三连 schema_validation_failed 的镜子（原句逐字）。

    信号引擎输入自带 vs SPY 对比（services/signals.py 基准池），契约又要求
    key_levels.vwap_levels——模型谈及 SPY/VWAP 不是幻觉实体，中文校验不得
    误伤：SPY 后随中文谓语（非「表现」类技术后缀）曾被 ticker 绑定规则拒，
    VWAP 既不在白名单、输入又恰好没有 VWAP 数据可绑。
    """
    spy_text = (
        "价格低于20日、50日均线约7%，以及相对SPY走弱，"
        "构成输入模型识别回撤过度的候选条件。"
    )
    assert (
        validate_simplified_chinese_text(
            spy_text,
            None,
            allowed_codes=("AMD", "SPY"),
        )
        == spy_text
    )
    vwap_text = "未提供VWAP数据，无法评估价格相对VWAP支撑或阻力。"
    assert validate_simplified_chinese_text(vwap_text, None) == vwap_text


def test_signal_evidence_still_rejects_unbound_tickers():
    """基准豁免不放松幻觉实体闸：不在 allowed_codes 的代码照旧拒。"""
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        validate_simplified_chinese_text(
            "以及相对TSLA走弱，构成候选条件。",
            None,
            allowed_codes=("AMD",),
        )
