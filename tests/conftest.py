from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _reset_read_caches():
    """Isolate fingerprint/byte read caches between tests.

    These module-level caches are keyed by file identity and version, which is
    correct in production but lets one test's parsed document leak into the
    next when tmp paths or frozen clocks repeat.
    """

    from app import public_home_snapshot
    from app.api import sectors as sectors_api
    from app.api import stocks as stocks_api
    from app.api import strength as strength_api
    from app.services import http_read_cache

    def _clear() -> None:
        public_home_snapshot._parsed_documents.invalidate()
        strength_api._strength_documents.invalidate()
        sectors_api._sector_iv_documents.invalidate()
        http_read_cache.reset_serialized_response_cache()
        stocks_api._watchlist_owner_snapshot_observed = None

    _clear()
    yield
    _clear()
