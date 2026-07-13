from __future__ import annotations

import hashlib
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.market_calendar import ET, early_close_minutes, is_trading_day

from .focus_config import FocusContextSettings, get_focus_context_settings
from .focus_models import FOCUS_SCHEMA_SHA256
from .focus_universe import build_focus_context
from .repository import CatalystRepository, _as_utc


FOCUS_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "option-pro-macrolens-focus-v2.json"
)


def verify_focus_contract(path: Path = FOCUS_CONTRACT_PATH) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == FOCUS_SCHEMA_SHA256
    except OSError:
        return False


def _market_session(as_of: datetime) -> str:
    observed = as_of.astimezone(ET)
    if not is_trading_day(observed.date()):
        return "closed"
    minute = observed.hour * 60 + observed.minute
    close = early_close_minutes(observed.date()) or 16 * 60
    if minute < 9 * 60 + 30:
        return "premarket"
    if minute < close:
        return "regular"
    if minute < 20 * 60:
        return "after_hours"
    return "closed"


def _breakout_rows() -> list[Mapping[str, Any]]:
    try:
        from app.services.breakouts.config import get_breakout_settings
        from app.services.breakouts.repository import BreakoutRepository

        path = get_breakout_settings().db_path
        if not path.is_file():
            return []
        latest = BreakoutRepository(path, read_only=True).latest_completed_scan()
        return list((latest or {}).get("events") or [])
    except Exception:
        return []


def publish_focus_from_strength_payload(
    payload: Mapping[str, Any],
    *,
    settings: FocusContextSettings | None = None,
) -> None:
    """Persist a focus revision from an already-computed strength snapshot.

    This extension point is intentionally local-only: it does not invoke the
    scanner, download quotes, or call MacroLens/OpenAI.
    """

    focus_settings = settings or get_focus_context_settings()
    if not focus_settings.cache_db_path.is_file() or not verify_focus_contract():
        return
    # ``_focus_rows`` is produced before request-specific top/sector/price/
    # liquidity filters are applied, then removed from the public strength
    # response. Never fall back to the visible rows: the first user query must
    # not be able to shrink the shared focus universe for everyone else.
    rows = list(payload.get("_focus_rows") or [])
    if not rows:
        return
    try:
        as_of = _as_utc(payload.get("as_of"))
    except (TypeError, ValueError):
        as_of = None
    if as_of is None:
        return
    repository = CatalystRepository(focus_settings.cache_db_path)
    current = repository.current_focus_context()
    data_through = _as_utc(payload.get("universe_as_of")) or as_of
    if current is not None:
        if as_of < current.as_of:
            return
        if (
            current.data_through is not None
            and data_through is not None
            and data_through < current.data_through
        ):
            return
        age = (as_of - current.as_of).total_seconds()
        if 0 <= age < focus_settings.refresh_seconds:
            return
    previous = [symbol.ticker for symbol in current.symbols] if current else []
    canonical = [
        str(row.get("ticker") or "")
        for row in rows
        if isinstance(row, Mapping) and bool(row.get("universe_member"))
    ]
    draft = build_focus_context(
        settings=focus_settings,
        strength_rows=[row for row in rows if isinstance(row, Mapping)],
        breakout_rows=_breakout_rows(),
        canonical_symbols=canonical,
        previous_symbols=previous,
        previous_context=(current.symbols if current else ()),
        as_of=as_of,
        data_through=data_through,
        market_session=_market_session(as_of),
        universe_version=str(payload.get("universe_version") or "unknown")[:200],
    )
    repository.publish_focus_context(draft, now=datetime.now(timezone.utc))
