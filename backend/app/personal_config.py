from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # - visitor_live_pulls: 个股手动拉取、板块 IV 冷启动实扫、日历 actual 外部补全
    #   （消耗 Massive/Yahoo/TradingView 等第三方行情额度）
    # - visitor_ai_actions: 财报影响分析的提交（消耗 OpenAI 模型预算）
    # 打开后仍保留原有的每 IP 限流、冷却与同源校验。
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


class AIConfig(StrictConfigModel):
    model: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    reasoning: Literal["max"] = "max"
    max_concurrency: Literal[1] = 1
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


class CatalystConfig(StrictConfigModel):
    sync_seconds: int = Field(default=120, ge=30, le=86_400)
    focus_seconds: int = Field(default=1800, ge=300, le=86_400)
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


class PublicHomeConfig(StrictConfigModel):
    poll_seconds: int = Field(default=30, ge=10, le=300)
    watchlist_seconds: int = Field(default=1800, ge=300, le=86_400)
    indices_seconds: int = Field(default=300, ge=300, le=86_400)
    overview_seconds: int = Field(default=300, ge=300, le=86_400)
    chart_seconds: int = Field(default=300, ge=300, le=86_400)
    signals_seconds: int = Field(default=900, ge=900, le=86_400)
    earnings_seconds: int = Field(default=21_600, ge=21_600, le=172_800)
    unusual_seconds: int = Field(default=1800, ge=1800, le=86_400)
    failure_retry_seconds: int = Field(default=300, ge=60, le=3600)


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


class StorageConfig(StrictConfigModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    backup_keep: int = Field(default=7, ge=1, le=100)


class PersonalConfig(StrictConfigModel):
    access: AccessConfig = Field(default_factory=AccessConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    catalyst: CatalystConfig = Field(default_factory=CatalystConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    public_home: PublicHomeConfig = Field(default_factory=PublicHomeConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
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
