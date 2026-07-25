"""Verify persisted production data required by the personal deployment."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from app.personal_config import get_personal_config


def release_data_report(*, now: float | None = None) -> dict[str, Any]:
    if get_personal_config().access.mode != "password":
        return {
            "ready": True,
            "required": False,
            "reason": "private_network",
        }

    from app.api.stocks import (
        _STOCK_DIRECTORY_PATH,
        _read_stock_directory_snapshot,
        _read_watchlist_snapshot,
    )
    from app.data_paths import get_data_paths
    from app.public_home_snapshot import (
        PUBLIC_HOME_RESOURCE_ORDER,
        public_home_entry_is_servable,
        public_home_resource_parameters,
        read_public_home_entries,
    )

    from app.public_home_snapshot import PUBLIC_HOME_RESOURCE_SPECS

    observed = float(now if now is not None else time.time())
    paths = get_data_paths()
    entries = read_public_home_entries(paths.public_home_snapshot, now=observed)

    def _classify(resource: str) -> str:
        """Return 'ok', 'stale' or 'missing' for one public-home resource.

        A deploy can only be responsible for two of these. ``missing`` means
        there is no usable entry at all, or the stored schema/parameters no
        longer match what this build expects — that is a broken release and
        must stop the rollout. ``stale`` means a correctly shaped entry exists
        and has simply aged past its window; the worker refreshes on a
        market-phase-aware cadence that is hours long while the market is
        closed, so waiting out a deploy on it blocks forever and fixes
        nothing — the data was already stale before the rollout started.
        """

        parameters = public_home_resource_parameters(resource, now=observed)
        if public_home_entry_is_servable(
            resource,
            entries.get(resource),
            parameters=parameters,
            now=observed,
        ):
            return "ok"
        entry = entries.get(resource)
        spec = PUBLIC_HOME_RESOURCE_SPECS.get(resource)
        if spec is None or not isinstance(entry, Mapping):
            return "missing"
        saved_at = entry.get("saved_at")
        shape_matches = (
            entry.get("schema") == spec.schema
            and entry.get("max_age") == spec.max_age
            and entry.get("parameters") == dict(parameters)
        )
        if not shape_matches:
            return "missing"
        try:
            age_ok = 0 < float(saved_at) <= observed
        except (TypeError, ValueError):
            return "missing"
        return "stale" if age_ok else "missing"

    classified = {
        resource: _classify(resource) for resource in PUBLIC_HOME_RESOURCE_ORDER
    }
    missing = [key for key, value in classified.items() if value == "missing"]
    stale = [key for key, value in classified.items() if value == "stale"]
    unavailable = [*missing, *stale]
    watchlist_ready = (
        _read_watchlist_snapshot(paths.watchlist_snapshot, now=observed)
        is not None
    )

    directory = _read_stock_directory_snapshot(
        _STOCK_DIRECTORY_PATH,
        now=observed,
    )
    directory_value = (
        directory.value
        if directory is not None and isinstance(directory.value, Mapping)
        else {}
    )
    directory_symbols = {
        str(item.get("ticker") or "")
        for item in directory_value.get("items", [])
        if isinstance(item, Mapping)
    }
    directory_count = int(directory_value.get("count") or 0)
    directory_ready = bool(
        directory is not None
        and directory.expires_at > observed
        and directory_value.get("provider") == "Massive"
        and {"AAOI", "NBIS"}.issubset(directory_symbols)
    )

    earnings_entry = entries.get("earnings")
    earnings_payload = (
        earnings_entry.get("payload")
        if isinstance(earnings_entry, Mapping)
        else None
    )
    earnings_complete = bool(
        isinstance(earnings_payload, Mapping)
        and earnings_payload.get("data_limited") is False
        and earnings_payload.get("source_status") == "active"
        and "Finnhub" in (earnings_payload.get("providers") or [])
    )
    ready = bool(
        watchlist_ready
        and directory_ready
        and earnings_complete
        and not missing
    )
    return {
        "ready": ready,
        "required": True,
        "watchlist": watchlist_ready,
        "stock_directory_count": directory_count,
        "stock_directory_ready": directory_ready,
        "earnings_complete": earnings_complete,
        "unavailable": unavailable,
        # Split so the rollout can block on a broken release without waiting
        # out a refresh cadence it does not control.
        "missing": missing,
        "stale": stale,
        **(
            {
                "available": [
                    "watchlist",
                    "stock_directory",
                    *PUBLIC_HOME_RESOURCE_ORDER,
                ]
            }
            if ready
            else {}
        ),
    }


def main() -> int:
    report = release_data_report()
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
