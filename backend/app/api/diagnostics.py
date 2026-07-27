"""Owner-only cache observability.

One place to see hit/miss/stale rates, byte budgets, evictions, singleflight
waits, 304 counts, and snapshot parse timings across every cache layer. No
cache keys, account data, or provider secrets are exposed — counters and
coarse per-layer stats only — and the router sits behind the owner boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services import cache_metrics
from app.services.cache import cache as shared_ttl_cache

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/cache")
async def cache_diagnostics() -> dict[str, Any]:
    from app import public_home_snapshot
    from app.api import sectors as sectors_api
    from app.api import strength as strength_api
    from app.services import http_read_cache

    return {
        "metrics": cache_metrics.snapshot(),
        "layers": {
            "shared_ttl_cache": shared_ttl_cache.stats(),
            "public_home_documents": public_home_snapshot._parsed_documents.stats(),
            "strength_documents": strength_api._strength_documents.stats(),
            "sector_iv_documents": sectors_api._sector_iv_documents.stats(),
            "serialized_responses": {
                "note": "byte-budgeted response bodies; see metrics.gauges",
            },
        },
    }
