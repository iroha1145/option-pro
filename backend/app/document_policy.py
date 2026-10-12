"""Document routing and static cache policies shared by the gateway."""
from __future__ import annotations

import re


# A dot may separate a share class or exchange suffix rather than a file type.
_STOCK_DOCUMENT = re.compile(
    r"/stock/(?:\^[A-Za-z0-9][A-Za-z0-9.^_=-]{0,30}|[A-Za-z0-9][A-Za-z0-9.^_=-]{0,31})/?"
)


def is_stock_document_path(path: str) -> bool:
    return _STOCK_DOCUMENT.fullmatch(path) is not None


def static_cache_control(path: str, status: int) -> str:
    if not (200 <= status < 300 or status == 304):
        return "no-store"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    if path.startswith("/static/company-logos/"):
        # Company logos change rarely (the bundled set ships with releases);
        # cache for a day and allow a week of stale serving.
        return "public, max-age=86400, stale-while-revalidate=604800"
    return "public, max-age=300, stale-while-revalidate=60"
