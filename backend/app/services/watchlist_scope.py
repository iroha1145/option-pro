"""Keep the starter watchlist separate from the market-wide research universe."""

from __future__ import annotations

from typing import Any, Iterable


DEFAULT_WATCHLIST_TICKERS = ("AAPL", "MSFT", "NVDA", "SPY")


def collection_watchlist_tickers() -> list[str]:
    """Warm four starter symbols plus the owner's explicitly saved selections.

    Customer registration must not grant access to provider collection budgets.
    Other research pages keep their existing bounded, displayed-row demand.
    """
    from app.services.accounts import OWNER_USER_ID, get_account_store

    personal = get_account_store().watchlist(OWNER_USER_ID)
    return list(dict.fromkeys([*DEFAULT_WATCHLIST_TICKERS, *personal]))


def scope_watchlist(payload: Any, tickers: Iterable[str]) -> Any:
    """Project cached rows without exposing other selections or fetching data."""
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        return payload
    wanted = list(dict.fromkeys(tickers))
    allowed = set(wanted)
    found: set[str] = set()
    groups = []
    for group in payload["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("stocks"), list):
            continue
        rows = [row for row in group["stocks"] if isinstance(row, dict) and row.get("ticker") in allowed]
        if rows:
            groups.append({**group, "stocks": rows})
            found.update(row["ticker"] for row in rows)
    missing = [ticker for ticker in wanted if ticker not in found]
    delayed = [ticker for ticker in payload.get("delayed_tickers", []) if ticker in allowed]
    return {
        **payload,
        "groups": groups,
        "attempted": len(wanted),
        "succeeded": len(found),
        "failed": len(missing),
        "failed_tickers": missing,
        "delayed": len(delayed),
        "delayed_tickers": delayed,
        "data_limited": bool(missing or delayed),
    }
