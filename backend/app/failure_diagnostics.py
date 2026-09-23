"""Bounded, redacted diagnostics for failures with an intentional fallback."""

from __future__ import annotations

import logging
import re
import threading
import time


_logger = logging.getLogger(__name__)
_stage_pattern = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_symbol_pattern = re.compile(r"(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})\Z")
_type_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_interval_seconds = 300.0
_max_keys = 512
_seen: dict[tuple[str, str, str], float] = {}
_lock = threading.Lock()
_clock = time.monotonic


def record_fallback_failure(stage: str, error: Exception, *, symbol: str | None = None) -> None:
    """Log one safe summary per stage, ticker, and exception type per interval."""

    try:
        safe_stage = stage if _stage_pattern.fullmatch(stage) else "unknown"
        candidate = symbol.strip().upper() if isinstance(symbol, str) else ""
        safe_symbol = candidate if _symbol_pattern.fullmatch(candidate) else "-"
        candidate_type = type(error).__name__
        safe_type = candidate_type if _type_pattern.fullmatch(candidate_type) else "UnknownError"
        key = (safe_stage, safe_symbol, safe_type)
        now = _clock()
        with _lock:
            last = _seen.get(key)
            if last is not None and now - last < _interval_seconds:
                return
            if last is None and len(_seen) >= _max_keys:
                expired = [known for known, logged_at in _seen.items() if now - logged_at >= _interval_seconds]
                for known in expired:
                    del _seen[known]
                if len(_seen) >= _max_keys:
                    return
            _seen[key] = now
        _logger.warning(
            "fallback_failure stage=%s symbol=%s error_type=%s",
            safe_stage,
            safe_symbol,
            safe_type,
        )
    except Exception:
        # Diagnostics must never replace the failure or fallback being recorded.
        return
