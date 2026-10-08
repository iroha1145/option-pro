"""市场综合研判（market_brief）的输出契约。

三方共用同一份契约：
- 模型侧：``MarketBriefResult.model_json_schema()`` 经 SDK 的 ``transform_schema`` 后作为
  结构化输出（``output_config.format``）的 JSON Schema；
- 后端侧：模型返回的 JSON 用 ``MarketBriefResult.model_validate_json`` 校验，
  自然语言字段全部走简体中文校验（与 ai_jobs 共用 ``validate_simplified_chinese_text``）；
- 前端侧：``tests/fixtures/market_brief_sample.json`` 是 GET /api/market-brief/latest 的样例。

边界（来自产品定义，改字段前先对照）：
- 程序算数、模型解释。结果里没有概率、目标价、仓位、买卖指令；覆盖范围（股票池、
  有效行情、数据截止、缺失模块）由程序写入 coverage，不由模型生成。
- 证据引用：所有 ``evidence_ids`` / ``evidence_id`` 必须是证据包里存在的 id
  （``news:<id>``、``idx:^GSPC``、``theme:<sector_id>`` 等，见 evidence.py 的约定）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.services.ai_jobs.models import validate_simplified_chinese_text

SCHEMA_VERSION = "market-brief-v1"

BriefSlot = Literal["pre_open", "post_close"]
BriefTrigger = Literal["scheduled", "manual"]

Regime = Literal["broad_advance", "narrow_leadership", "rotation", "risk_off", "mixed", "uncertain"]
Sufficiency = Literal["low", "medium", "high"]
Consistency = Literal["confirms", "diverges", "mixed", "unknown"]
MacroVerdict = Literal["supports", "contradicts", "mixed", "unknown"]
SectorChange = Literal["substantive", "noise", "unknown"]
PricedIn = Literal["yes", "partly", "no", "unclear"]


def _zh(max_length: int):
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length),
        AfterValidator(validate_simplified_chinese_text),
    ]


ZhLine = _zh(120)
ZhPoint = _zh(300)
ZhPara = _zh(1200)
EvidenceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=96, pattern=r"^[a-z]+:[A-Za-z0-9_.^:\-]{1,90}$")]
Ticker = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=12, pattern=r"^[A-Z0-9.^\-]{1,12}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class BriefSection(_Strict):
    """一段研判：一句总结、若干要点、引用的证据 id。"""

    summary: ZhPara = Field(description="这一段的结论，两到四句，只写证据撑得住的话")
    points: list[ZhPoint] = Field(default_factory=list, max_length=5, description="支撑结论的要点，每条一句，引用证据包里的数字")
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=12, description="这一段引用的证据 id，只能填证据包里出现过的")


class InternalsSection(BriefSection):
    """大盘与市场内部结构：指数表现与广度、相对强弱、突破状态是否一致。"""

    breadth_vs_index: Consistency = Field(description="广度代理与指数方向是否一致：confirms 一致、diverges 背离、mixed 部分一致、unknown 证据不足")


class MacroSection(BriefSection):
    """宏观与跨资产验证：利率、美元、波动率、信用等是否支持当前股票叙事。"""

    verdict: MacroVerdict = Field(description="宏观与跨资产是否支持当前的股票叙事：supports 支持、contradicts 反驳、mixed 部分支持、unknown 证据不足")


class SectorRead(_Strict):
    name: ZhLine = Field(description="板块或主题的中文名")
    change: SectorChange = Field(description="substantive 实质变化、noise 噪音、unknown 待定")
    note: ZhPoint = Field(description="一句话说明变化是什么、靠什么证据判断")
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=4, description="引用的证据 id")


class NewsRead(_Strict):
    evidence_id: EvidenceId = Field(description="对应的新闻或热点证据 id（news:… 或 hot:…）")
    title_zh: ZhLine = Field(description="新闻的中文标题")
    what_is_new: ZhPoint = Field(description="这次新增的信息是什么，不重复已有叙事")
    priced_in: PricedIn = Field(description="价格是否已反映：yes 已反映、partly 部分反映、no 未反映、unclear 不明")
    tickers: list[Ticker] = Field(default_factory=list, max_length=6, description="相关代码，只能用 allowed_codes 里的")


class WatchItem(_Strict):
    what: ZhPoint = Field(description="接下来看什么：哪个数据、财报、价格水平或关系")
    why: ZhPoint = Field(description="为什么它重要：它影响当前判断的哪一环")
    revise_if: ZhPoint = Field(description="出现什么情况就要修正判断，以及改成什么")


class MarketBriefResult(_Strict):
    """模型输出的完整研判。字段顺序即前端展示顺序。"""

    output_language: Literal["zh-CN"] = Field(description="固定为 zh-CN")
    headline: ZhLine = Field(description="一句话市场结论：说清状态、主导因素和把握程度")
    regime: Regime = Field(description="市场状态标签：broad_advance 普涨、narrow_leadership 少数权重股领涨、rotation 板块轮动、risk_off 避险、mixed 信号混杂、uncertain 证据不足以判断")
    evidence_sufficiency: Sufficiency = Field(description="证据是否充分：low、medium、high；它不是上涨或下跌的概率")
    internals: InternalsSection = Field(description="大盘与市场内部结构")
    macro_check: MacroSection = Field(description="宏观与跨资产验证")
    sectors: list[SectorRead] = Field(default_factory=list, max_length=6, description="发生变化的板块，只写有证据的")
    key_news: list[NewsRead] = Field(default_factory=list, max_length=6, description="关键新闻，按新增信息的重要性排序")
    watch_items: list[WatchItem] = Field(default_factory=list, max_length=5, description="后续观察项")
    invalidators: list[ZhPoint] = Field(default_factory=list, max_length=4, description="出现哪些情况应撤回当前解释")
    prior_review: ZhPara | None = Field(default=None, description="对上一份研判的复盘：哪些兑现、哪些被推翻；证据包没有上一份时为 null")


__all__ = [
    "SCHEMA_VERSION",
    "BriefSlot",
    "BriefTrigger",
    "Regime",
    "Sufficiency",
    "Consistency",
    "MacroVerdict",
    "SectorChange",
    "PricedIn",
    "BriefSection",
    "InternalsSection",
    "MacroSection",
    "SectorRead",
    "NewsRead",
    "WatchItem",
    "MarketBriefResult",
]
