from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for legacy deploy checks.
    import tomli as tomllib  # type: ignore[no-redef]


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONAL_CONFIG_PATH = REPOSITORY_ROOT / "config" / "personal.toml"
HOURLY_ANALYSIS_TIMES_ET = tuple(f"{hour:02d}:00" for hour in range(24))
#: Mirror of ``app.services.macro_conditions.registry.SCORING_VERSION``. Kept as
#: a literal so the config layer never imports the services layer; the two are
#: asserted equal in tests.
MACRO_SCORING_VERSION = "optix-macro-score-v1"
_PRIVATE_NETWORK_ENVELOPES = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccessConfig(StrictConfigModel):
    mode: Literal["private_network", "password"] = "private_network"
    # 密码模式下的非 Owner（匿名访客与朋友账号）默认只读已保存的快照。
    # 下面两个开关各自打开一个有限的「访客可发起」面，默认关闭：
    # - visitor_live_pulls: 个股手动拉取、日历 actual 外部补全
    #   （消耗 Massive/Yahoo/TradingView 等第三方行情额度）
    # - visitor_ai_actions: 财报影响分析的提交（消耗 Claude 模型预算）
    # 打开后仍保留原有的每 IP 限流、冷却与同源校验。
    # 板块 IV 使用公开的有界后台刷新队列，不依赖这两个开关。
    visitor_live_pulls: bool = False
    visitor_ai_actions: bool = False
    allowed_private_cidrs: list[str] = Field(
        default_factory=lambda: [
            "127.0.0.0/8",
            "::1/128",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "100.64.0.0/10",
        ],
        min_length=1,
        max_length=32,
    )

    @field_validator("allowed_private_cidrs")
    @classmethod
    def validate_private_cidrs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            try:
                network = ipaddress.ip_network(value.strip(), strict=False)
            except ValueError as exc:
                raise ValueError("private access networks must use CIDR notation") from exc
            allowed = any(
                network.version == envelope.version
                and network.subnet_of(envelope)
                for envelope in _PRIVATE_NETWORK_ENVELOPES
            )
            if not allowed:
                raise ValueError(
                    "private access networks must use loopback, RFC1918, "
                    "Tailscale, or IPv6 unique-local ranges"
                )
            item = str(network)
            if item not in normalized:
                normalized.append(item)
        return normalized


class FeatureConfig(StrictConfigModel):
    breakout_enabled: bool = True
    catalyst_mode: Literal["off", "read", "manual", "scheduled"] = "read"


class ModelBudgetConfig(StrictConfigModel):
    # Positive values enable one application budget for Haiku and Opus.
    # Zero retains the legacy token policy for installations not opting in.
    daily_budget_usd: float = Field(
        default=0.0, ge=0.0, le=10_000.0, multiple_of=0.01,
        allow_inf_nan=False,
    )
    accounting_start_at: datetime | None = None
    enforce_limit: bool = True

    @field_validator("daily_budget_usd", mode="before")
    @classmethod
    def reject_boolean_amount(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("model budget must be a dollar amount")
        return value

    @field_validator("accounting_start_at", mode="before")
    @classmethod
    def require_timestamp_input(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("model budget start must be an aware timestamp")
        return value

    @field_validator("accounting_start_at")
    @classmethod
    def normalize_budget_start(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("model budget start must include a timezone")
        return value.astimezone(timezone.utc)


class AIConfig(StrictConfigModel):
    model: Literal["claude-haiku-5-5", "gpt-5.6-terra"] = "claude-haiku-5-5"
    reasoning: Literal["xhigh", "max"] = "xhigh"
    max_concurrency: int = Field(default=4, ge=1, le=4)
    # Retained for one migration cycle so old personal.toml files remain
    # readable. Zero means unlimited; the active safety boundary is Token use.
    daily_max_jobs: int = Field(default=0, ge=0, le=100_000)
    daily_budget_usd: float = Field(default=0.0, ge=0.0, le=10_000.0)
    daily_token_limit: int = Field(
        default=10_000_000,
        ge=102_400,
        le=100_000_000,
    )
    execution_mode: Literal["background"] = "background"

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_concurrency_default(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and value.get("model") == "gpt-5.6-terra"
            and "max_concurrency" not in value
        ):
            return {**value, "max_concurrency": 1}
        return value

    @model_validator(mode="after")
    def validate_model_reasoning(self) -> "AIConfig":
        expected = "xhigh" if self.model == "claude-haiku-5-5" else "max"
        if self.reasoning != expected:
            raise ValueError("AI model and reasoning must use a supported pair")
        if self.model == "gpt-5.6-terra" and self.max_concurrency != 1:
            raise ValueError("legacy OpenAI configuration supports concurrency 1 only")
        return self


class CatalystConfig(StrictConfigModel):
    sync_seconds: int = Field(default=120, ge=30, le=86_400)
    focus_seconds: int = Field(default=1800, ge=300, le=86_400)
    # 变更日志整条目保留期。下限 8 天必须大于公共 feed 窗口上限 7 天：
    # 保留期内的条目全量保留（含旧变更），窗口读与 cursor 分页语义不受修剪影响。
    journal_retention_days: int = Field(default=30, ge=8, le=3650)
    manual_force_reanalysis: Literal[True] = True
    manual_refresh_cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    scheduled_times_et: list[str] = Field(
        default_factory=lambda: list(HOURLY_ANALYSIS_TIMES_ET),
        min_length=1,
        max_length=24,
    )

    @field_validator("scheduled_times_et")
    @classmethod
    def validate_times(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            parts = value.split(":")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError("scheduled times must use HH:MM")
            hour, minute = (int(part) for part in parts)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("scheduled times must be valid clock times")
            item = f"{hour:02d}:{minute:02d}"
            if item not in normalized:
                normalized.append(item)
        return normalized


class BreakoutConfig(StrictConfigModel):
    regular_seconds: int = Field(default=300, ge=30, le=86_400)
    premarket_seconds: int = Field(default=600, ge=30, le=86_400)
    closed_seconds: int = Field(default=1800, ge=60, le=86_400)
    range_persistence_mode: Literal["off", "shadow", "active"] = "shadow"


class QuotesConfig(StrictConfigModel):
    """Bounded real-time prices; public redistribution is separately enabled."""

    enabled: bool = False
    public_enabled: bool = False
    signals_enabled: bool = False
    max_symbols: int = Field(default=50, ge=4, le=50)
    publish_interval_ms: int = Field(default=250, ge=100, le=1000)
    release_seconds: int = Field(default=30, ge=0, le=30)


class PublicHomeConfig(StrictConfigModel):
    poll_seconds: int = Field(default=30, ge=10, le=300)
    watchlist_seconds: int = Field(default=1800, ge=300, le=86_400)
    indices_seconds: int = Field(default=300, ge=300, le=86_400)
    overview_seconds: int = Field(default=300, ge=300, le=86_400)
    chart_seconds: int = Field(default=300, ge=300, le=86_400)
    signals_seconds: int = Field(default=900, ge=900, le=86_400)
    earnings_seconds: int = Field(default=21_600, ge=21_600, le=172_800)
    unusual_seconds: int = Field(default=1800, ge=1800, le=86_400)
    # CTA 趋势资金估算：日频模型，盘中 30 分钟一算足够（末根未收盘只做
    # 暂定标记，正式仓位要等收盘后的下一轮刷新）。
    cta_seconds: int = Field(default=1800, ge=900, le=86_400)
    failure_retry_seconds: int = Field(default=300, ge=60, le=3600)


class EarningsConfig(StrictConfigModel):
    """财报页「重点公司」与增强预算的部署期配置。

    - featured_market_cap_usd：重点公司的市值门槛（美元）。market_cap 缺失表示
      unknown，不参与门槛判断（unknown ≠ small）；公共关注池与账号自选不受
      门槛影响。
    - expected_move_enrich_limit：单次刷新最多为多少家重点公司计算预期波动
      （期权链请求逐家计价，必须有硬上限）。0 表示关闭预期波动增强。
    - market_cap_cache_days：批量市值的持久缓存天数（市值是慢变量，低频刷新）。
    """

    featured_market_cap_usd: float = Field(
        default=20_000_000_000.0,
        ge=100_000_000.0,
        le=10_000_000_000_000.0,
    )
    expected_move_enrich_limit: int = Field(default=120, ge=0, le=500)
    market_cap_cache_days: int = Field(default=3, ge=1, le=30)


class MacroConfig(StrictConfigModel):
    """Operator-tunable macro settings only.

    Series identifiers, formulas, stale thresholds, minimum history, module
    factor floors, regime cut-offs, the ON RRP risk curve, the 2% breakeven
    target and every rolling window stay versioned constants in
    ``app.services.macro_conditions.registry``. Making them configurable would
    let a config edit silently change what a published score means.
    """

    enabled: bool = True
    history_years: int = Field(default=8, ge=5, le=15)
    #: Pinned, not configurable. The published score means "percentile within a
    #: five-year window"; the UI, the API comments and the docs all say five
    #: years, and ``scoring_version`` is a fixed literal. Letting config move the
    #: window changed what a score meant while the version name stayed the same,
    #: so history curves would silently mix algorithms and the AI input hash
    #: would treat two different scores as the same one. A real 3y or 10y
    #: variant has to arrive as optix-macro-score-v2-w3 / -w10, not as a config
    #: edit (incremental review P1).
    score_window_years: Literal[5] = 5
    #: Likewise pinned. Module aggregation always read the registry's
    #: ``ema_days=5`` for the funding module and never consulted this value, so
    #: it was a knob that appeared to work and did nothing (incremental review
    #: P2). An algorithm parameter change belongs to a new scoring version.
    funding_ema_days: Literal[5] = 5
    refresh_times_et: list[str] = Field(
        default_factory=lambda: ["08:30", "18:30"],
        min_length=1,
        max_length=12,
    )
    manual_refresh_cooldown_seconds: int = Field(default=300, ge=0, le=3600)
    scoring_version: str = Field(default="optix-macro-score-v1", max_length=64)

    @field_validator("refresh_times_et")
    @classmethod
    def validate_refresh_times(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            parts = str(value).split(":")
            if len(parts) != 2 or not all(
                len(part) == 2 and part.isdigit() for part in parts
            ):
                raise ValueError("macro refresh times must use HH:MM")
            hour, minute = (int(part) for part in parts)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("macro refresh times must be valid clock times")
            item = f"{hour:02d}:{minute:02d}"
            if item in normalized:
                raise ValueError("macro refresh times must not repeat")
            normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def validate_windows(self) -> "MacroConfig":
        if self.history_years < self.score_window_years:
            raise ValueError(
                "macro history_years must be at least score_window_years"
            )
        # The scoring version names an algorithm, not a preference. Config may
        # only restate the version the code implements, never invent one.
        # MACRO_SCORING_VERSION mirrors macro_conditions.registry.SCORING_VERSION;
        # tests assert the two literals agree. The mirror keeps this module free
        # of any app.services import, so a minimal deployment tree that carries
        # only the config layer still validates.
        if self.scoring_version != MACRO_SCORING_VERSION:
            raise ValueError(
                "macro scoring_version must equal the code constant "
                f"{MACRO_SCORING_VERSION}"
            )
        return self


def _clock_minutes(value: str, *, field: str) -> int:
    parts = str(value).split(":")
    if len(parts) != 2 or not all(len(part) == 2 and part.isdigit() for part in parts):
        raise ValueError(f"{field} must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{field} must be a valid clock time")
    return hour * 60 + minute


class MarketBriefConfig(StrictConfigModel):
    """首页「市场综合研判」：每个交易日开盘前 / 收盘后各一份，由 Claude 生成。

    模型与 effort 钉死成字面量：换模型会改变费用口径与输出风格，必须改代码并过测试，
    不能靠改配置悄悄完成。时刻一律是美东墙钟（America/New_York）。
    """

    enabled: bool = True
    model: Literal["claude-opus-5-5"] = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "xhigh"
    #: 默认排在宏观模块 08:30 刷新之后，让开盘前研判读到当天的宏观快照。
    pre_open_time_et: str = "08:40"
    post_close_offset_minutes: int = Field(default=30, ge=0, le=240)
    #: 当日全市场批次（美东 22:00 起发布）迟迟不到时，过了这个时刻就不再等它。
    post_close_fallback_time_et: str = "23:30"
    grace_minutes: int = Field(default=150, ge=30, le=600)
    web_search_max_uses: int = Field(default=10, ge=0, le=20)
    web_fetch_max_uses: int = Field(default=8, ge=0, le=20)
    web_fetch_max_content_tokens: int = Field(default=12_000, ge=1_000, le=100_000)
    code_execution_tool: bool = False
    refusal_fallback: bool = False
    #: 结构化输出与网页工具并用没有文档背书（文档写明结构化输出与引用不兼容，而网页
    #: 搜索结果自带引用），所以部署默认关：JSON Schema 附在系统提示词里，解析与校验流程
    #: 不变。跑通过一次后可以改成 true 试结构化输出；若被 400 拒绝就改回。
    structured_output: bool = False
    #: 系统提示词显式缓存断点的 TTL；顶层自动缓存固定 5 分钟。一天两份研判相隔数小时，
    #: 1 小时的条目跨不过去，只有同一次运行的续跑与一小时内的手动重跑能命中，5 分钟就够。
    prompt_cache_ttl: Literal["5m", "1h"] = "5m"
    max_output_tokens: int = Field(default=48_000, ge=8_000, le=128_000)
    max_continuations: int = Field(default=4, ge=0, le=8)
    output_token_ceiling: int = Field(default=160_000, ge=8_000, le=1_000_000)
    #: 整次运行（含续跑）的绝对超时；不得超过 Worker 的 1800 秒任务预算。
    request_timeout_seconds: float = Field(default=1500.0, ge=30.0, le=1800.0)
    evidence_max_bytes: int = Field(default=56_000, ge=16_000, le=120_000)
    #: 每个 UTC 日定时、手动和命令行合计的持久准入次数。
    daily_max_runs: int = Field(default=6, ge=1, le=24)
    #: 关掉后，最新研判与历史只对 Owner 可见。
    public_read: bool = True

    @field_validator("pre_open_time_et", "post_close_fallback_time_et")
    @classmethod
    def validate_clock_time(cls, value: str, info: ValidationInfo) -> str:
        minutes = _clock_minutes(value, field=f"market_brief {info.field_name}")
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    @model_validator(mode="after")
    def validate_slots(self) -> "MarketBriefConfig":
        # 开盘前一份必须真的在开盘前生成，否则它和收盘后那份读到的是同一类数据。
        if _clock_minutes(self.pre_open_time_et, field="pre_open_time_et") >= 9 * 60 + 30:
            raise ValueError("market_brief pre_open_time_et must be before 09:30")
        # 兜底时刻落在收盘后窗口开启之前时，收盘后那份永远不会等全市场批次。
        fallback = _clock_minutes(
            self.post_close_fallback_time_et,
            field="post_close_fallback_time_et",
        )
        if fallback < 16 * 60 + self.post_close_offset_minutes:
            raise ValueError(
                "market_brief post_close_fallback_time_et must not precede "
                "the regular close plus post_close_offset_minutes"
            )
        if self.output_token_ceiling < self.max_output_tokens:
            raise ValueError(
                "market_brief output_token_ceiling must be at least max_output_tokens"
            )
        return self

    def to_run_config(
        self, *, shared_daily_budget_usd: float = 0.0,
        shared_budget_start_at: datetime | None = None,
        shared_budget_enforce_limit: bool = True,
        budget_path: str | Path | None = None,
    ) -> Any:
        """映射成 ``BriefRunConfig``（同名字段）。

        延迟导入：配置层不在模块加载时引用 app.services，只带配置层的
        精简部署树也能校验 personal.toml。
        """

        from app.services.market_brief.runner import BriefRunConfig

        return BriefRunConfig(
            shared_daily_budget_usd=shared_daily_budget_usd,
            shared_budget_start_at=shared_budget_start_at,
            shared_budget_enforce_limit=shared_budget_enforce_limit,
            budget_path=budget_path,
            daily_max_runs=self.daily_max_runs,
            model=self.model,
            effort=self.effort,
            max_output_tokens=self.max_output_tokens,
            max_continuations=self.max_continuations,
            output_token_ceiling=self.output_token_ceiling,
            web_search_max_uses=self.web_search_max_uses,
            web_fetch_max_uses=self.web_fetch_max_uses,
            web_fetch_max_content_tokens=self.web_fetch_max_content_tokens,
            code_execution_tool=self.code_execution_tool,
            refusal_fallback=self.refusal_fallback,
            structured_output=self.structured_output,
            prompt_cache_ttl=self.prompt_cache_ttl,
            request_timeout_seconds=self.request_timeout_seconds,
            evidence_max_bytes=self.evidence_max_bytes,
        )

    def to_schedule(self) -> Any:
        """映射成 ``BriefSchedule``（同名字段）；延迟导入的原因同上。"""

        from app.services.market_brief.scheduler import BriefSchedule

        return BriefSchedule(
            pre_open_time_et=self.pre_open_time_et,
            post_close_offset_minutes=self.post_close_offset_minutes,
            post_close_fallback_time_et=self.post_close_fallback_time_et,
            grace_minutes=self.grace_minutes,
        )


class StorageConfig(StrictConfigModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    backup_keep: int = Field(default=7, ge=1, le=100)


class PersonalConfig(StrictConfigModel):
    access: AccessConfig = Field(default_factory=AccessConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    model_budget: ModelBudgetConfig = Field(default_factory=ModelBudgetConfig)
    catalyst: CatalystConfig = Field(default_factory=CatalystConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    quotes: QuotesConfig = Field(default_factory=QuotesConfig)
    public_home: PublicHomeConfig = Field(default_factory=PublicHomeConfig)
    earnings: EarningsConfig = Field(default_factory=EarningsConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    market_brief: MarketBriefConfig = Field(default_factory=MarketBriefConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @property
    def catalyst_sync_enabled(self) -> bool:
        return self.features.catalyst_mode != "off"

    @property
    def catalyst_manual_enabled(self) -> bool:
        return self.features.catalyst_mode in {"manual", "scheduled"}

    @property
    def catalyst_scheduled_enabled(self) -> bool:
        return self.features.catalyst_mode == "scheduled"


def load_personal_config(path: Path = DEFAULT_PERSONAL_CONFIG_PATH) -> PersonalConfig:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(f"personal configuration is missing: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"personal configuration cannot be read: {path}") from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"personal configuration is invalid: {path}") from exc
    return PersonalConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_personal_config() -> PersonalConfig:
    return load_personal_config()


def personal_analysis_permissions(config: Any) -> tuple[bool, bool]:
    features = getattr(config, "features", None)
    mode = getattr(features, "catalyst_mode", None)
    if mode is not None:
        return mode in {"manual", "scheduled"}, mode == "scheduled"
    return (
        bool(getattr(config, "catalyst_manual_enabled", False)),
        bool(getattr(config, "catalyst_scheduled_enabled", False)),
    )
