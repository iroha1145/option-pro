"""Resolve earnings analysis eligibility without adding private data to prompts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.personal_config import get_personal_config
from app.services.earnings_enrichment import featured_flags
from app.services.numeric import finite_number_or_none


def is_featured_company(
    row: Mapping[str, Any],
    *,
    personal_tickers: Iterable[str] = (),
    personal_config: Any | None = None,
) -> bool:
    """Use the page's public flags, market-cap rule and the caller's watchlist.

    Callers supply server-owned snapshot rows or persisted server-bound facts,
    never a browser's claim that a company is featured. Missing market cap is
    unknown; it grants no eligibility by itself.
    """
    from app.api.earnings import EARNINGS_TICKERS

    ticker = str(row.get("ticker") or "").strip().upper()
    if not ticker:
        return False
    if row.get("public_featured") is True or ticker in EARNINGS_TICKERS:
        return True
    if ticker in {str(value).strip().upper() for value in personal_tickers}:
        return True
    market_cap = finite_number_or_none(row.get("market_cap"))
    if market_cap is None:
        return False
    config = personal_config or get_personal_config()
    public, _reasons = featured_flags(
        ticker,
        market_cap,
        threshold=float(config.earnings.featured_market_cap_usd),
        public_pool=frozenset(EARNINGS_TICKERS),
    )
    return public


def owner_watchlist_tickers() -> list[str]:
    """Background collection follows the owner's selections, never all users'."""
    from app.services.accounts import OWNER_USER_ID, get_account_store

    return get_account_store().watchlist(OWNER_USER_ID)
