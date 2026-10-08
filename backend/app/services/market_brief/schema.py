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

    summary: ZhPara
    points: list[ZhPoint] = Field(default_factory=list, max_length=5)
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=12)


class InternalsSection(BriefSection):
    """大盘与市场内部结构：指数表现与广度、相对强弱、突破状态是否一致。"""

    breadth_vs_index: Consistency


class MacroSection(BriefSection):
    """宏观与跨资产验证：利率、美元、波动率、信用等是否支持当前股票叙事。"""

    verdict: MacroVerdict


class SectorRead(_Strict):
    name: ZhLine
    change: SectorChange
    note: ZhPoint
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=4)


class NewsRead(_Strict):
    evidence_id: EvidenceId
    title_zh: ZhLine
    what_is_new: ZhPoint
    priced_in: PricedIn
    tickers: list[Ticker] = Field(default_factory=list, max_length=6)


class WatchItem(_Strict):
    what: ZhPoint
    why: ZhPoint
    revise_if: ZhPoint


class MarketBriefResult(_Strict):
    """模型输出的完整研判。字段顺序即前端展示顺序。"""

    output_language: Literal["zh-CN"]
    headline: ZhLine
    regime: Regime
    evidence_sufficiency: Sufficiency
    internals: InternalsSection
    macro_check: MacroSection
    sectors: list[SectorRead] = Field(default_factory=list, max_length=6)
    key_news: list[NewsRead] = Field(default_factory=list, max_length=6)
    watch_items: list[WatchItem] = Field(default_factory=list, max_length=5)
    invalidators: list[ZhPoint] = Field(default_factory=list, max_length=4)
    prior_review: ZhPara | None = None


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
