"""Process-wide cache observability counters.

Every cache layer reports here so the owner diagnostics endpoint can show
hit/miss/stale rates, byte budgets, and avoided upstream calls without any
external dependency. Counters are cheap (dict + lock) and never expose cache
keys or payload contents — only aggregate numbers and coarse labels.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_counters: dict[str, int] = {}
_timings: dict[str, tuple[int, float]] = {}
_gauges: dict[str, float] = {}
_started_at = time.time()


def incr(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def observe_ms(name: str, elapsed_ms: float) -> None:
    """Accumulate a duration; snapshot() reports count, total, and mean."""
    with _lock:
        count, total = _timings.get(name, (0, 0.0))
        _timings[name] = (count + 1, total + float(elapsed_ms))


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = float(value)


def snapshot() -> dict[str, Any]:
    with _lock:
        timings = {
            name: {
                "count": count,
                "total_ms": round(total, 1),
                "mean_ms": round(total / count, 2) if count else 0.0,
            }
            for name, (count, total) in sorted(_timings.items())
        }
        return {
            "started_at": _started_at,
            "uptime_seconds": round(time.time() - _started_at, 1),
            "counters": dict(sorted(_counters.items())),
            "timings": timings,
            "gauges": dict(sorted(_gauges.items())),
        }


def reset_for_tests() -> None:
    with _lock:
        _counters.clear()
        _timings.clear()
        _gauges.clear()
