"""Append-only EOD capture versions. Recapture never rewrites old retrieved_at."""

from __future__ import annotations

from typing import Mapping, Sequence


def normalize_late_securities(late_securities: Sequence[str] | None) -> tuple[str, ...]:
    if not late_securities:
        return ()
    return tuple(dict.fromkeys(item.upper() for item in late_securities if item))


def eod_pool_exclusions(
    late_securities: Sequence[str] | None = None,
) -> frozenset[str]:
    return frozenset(normalize_late_securities(late_securities))


def series_is_late(series: Mapping[str, object] | object, late: frozenset[str]) -> bool:
    if not late:
        return False
    security_id = str(getattr(series, "security_id", "") or "").upper()
    ticker = str(getattr(series, "ticker_at_signal", "") or "").upper()
    return security_id in late or ticker in late
