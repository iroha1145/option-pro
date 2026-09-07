"""Yahoo/yfinance 期权能力：先统一行情代码，再判断当前适配能否提供期权。

这是供应商能力声明，不是「该指数在金融市场上没有期权」。
例如 CBOE 确有 SPX / RUT 指数期权，但本项目行情代码是 ^GSPC / ^RUT，
Yahoo/yfinance 不能按这些现金指数报价代码返回期权链。禁止自动改用
SPY / QQQ 等基金期权冒充原指数。

能力状态：
- supported：本次发现了可用到期日
- unsupported_by_provider：适配声明明确不支持（零外呼）
- permission_denied：套餐或访问权限不允许探测
- unknown：尚无声明，允许受控发现
- empty_unconfirmed：一次成功空列表，尚不能升格为永久不支持
- temporary_unavailable：超时 / 429 / 5xx / 解析失败

声明变更靠 CAPABILITY_VERSION。缓存与复核周期可配置；时间流逝本身
不会自动外呼。多进程部署时能力表一致，但 IO 预算是每进程一份。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.symbols import quote_symbol


OptionStatus = Literal[
    "supported",
    "unsupported_by_provider",
    "permission_denied",
    "unknown",
    "empty_unconfirmed",
    "temporary_unavailable",
]

PROVIDER_YAHOO = "Yahoo/yfinance"
CAPABILITY_VERSION = "yahoo-index-quotes-v1"


class UnsupportedOptionsError(RuntimeError):
    """Declared provider mapping cannot serve options for this quote symbol."""


# 当前 Yahoo/yfinance 适配无法按这些「行情代码」提供期权链。
# 只列已核对的大盘报价代码，不用 ^ 前缀一刀切。
YAHOO_UNSUPPORTED_QUOTE_SYMBOLS: frozenset[str] = frozenset(
    {
        "^GSPC",
        "^IXIC",
        "^DJI",
        "^N225",
        "000001.SS",
    }
)


@dataclass(frozen=True)
class OptionCapability:
    ticker: str
    provider: str
    options_status: OptionStatus
    retryable: bool
    capability_version: str = CAPABILITY_VERSION

    def as_payload(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "provider": self.provider,
            "options_status": self.options_status,
            "retryable": self.retryable,
            "capability_version": self.capability_version,
        }


def canonicalize_option_symbol(ticker: str) -> str:
    return quote_symbol(str(ticker or "").strip())


def resolve_option_capability(
    ticker: str,
    *,
    provider: str = PROVIDER_YAHOO,
) -> OptionCapability:
    symbol = canonicalize_option_symbol(ticker)
    if provider == PROVIDER_YAHOO and symbol in YAHOO_UNSUPPORTED_QUOTE_SYMBOLS:
        return OptionCapability(
            ticker=symbol,
            provider=provider,
            options_status="unsupported_by_provider",
            retryable=False,
        )
    return OptionCapability(
        ticker=symbol,
        provider=provider,
        options_status="unknown",
        retryable=True,
    )


def is_declared_unsupported(ticker: str, *, provider: str = PROVIDER_YAHOO) -> bool:
    return (
        resolve_option_capability(ticker, provider=provider).options_status
        == "unsupported_by_provider"
    )


def unsupported_payload(ticker: str, *, extra: dict | None = None) -> dict:
    payload = resolve_option_capability(ticker).as_payload()
    payload["expirations"] = []
    if extra:
        payload.update(extra)
    return payload
