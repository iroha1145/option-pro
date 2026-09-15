"""Process-local counters for optional production algorithms.

These are operational counts only: usage, fallbacks, T1 evaluability, and
timing. They are never a win-rate, return, or drawdown claim.
"""

from __future__ import annotations

from typing import Any

from app.services import cache_metrics


def record_screener_resolution(resolution: Any, *, fallback_reason: str | None = None) -> None:
    effective = getattr(resolution, "effective", None) or "unknown"
    source = getattr(resolution, "source", None)
    cache_metrics.incr(f"algorithm.screener.effective.{effective}")
    if source:
        cache_metrics.incr(f"algorithm.screener.source.{source}")
    reason = fallback_reason or getattr(resolution, "fallback_reason", None)
    if reason:
        cache_metrics.incr(f"algorithm.screener.fallback.{reason}")


def record_radar_resolution(resolution: Any) -> None:
    effective = getattr(resolution, "effective", None) or "unknown"
    cache_metrics.incr(f"algorithm.radar.effective.{effective}")


def record_t1_status(status: str | None) -> None:
    label = str(status or "unavailable")
    cache_metrics.incr(f"algorithm.t1.status.{label}")


def record_t1_attach_ms(elapsed_ms: float, count: int) -> None:
    cache_metrics.observe_ms("algorithm.t1.attach", elapsed_ms)
    cache_metrics.incr("algorithm.t1.attach_events", count)
