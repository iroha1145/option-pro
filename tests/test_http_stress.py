"""High-pressure public-read stress for the 16-core / 32 GiB host profile.

These tests pin concurrency safety, not a wall-clock SLA: every public section
must stay 2xx/3xx/4xx (never 5xx) under overlapping reads, and the news
endpoints must remain reachable while other boards are also hammered.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient

from app.main import app

PUBLIC_READS = (
    "/health",
    "/ready",
    "/api/market/status",
    "/api/market/indices",
    "/api/catalysts/status",
    "/api/catalysts/feed?window_hours=24&limit=1",
    "/api/catalysts/hotspots?limit=8",
    "/api/catalysts/hotspots/status",
    "/api/earnings/upcoming",
    "/api/sectors",
    "/api/breakouts/status",
    "/api/breakouts/current",
    "/api/catalysts/calendar",
    "/api/strength/market",
    "/api/signals/market",
    "/api/macro/conditions",
)


def _get(client: TestClient, path: str) -> tuple[str, int, float]:
    started = time.perf_counter()
    response = client.get(path)
    elapsed = time.perf_counter() - started
    return path, response.status_code, elapsed


def test_overlapping_public_reads_never_return_server_error() -> None:
    client = TestClient(app)
    jobs = PUBLIC_READS * 8
    results: list[tuple[str, int, float]] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(_get, client, path) for path in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    failures = [item for item in results if item[1] >= 500]
    assert not failures, failures
    news = [item for item in results if item[0].startswith("/api/catalysts/")]
    assert news
    assert all(item[1] < 500 for item in news)


def test_news_feed_stays_available_under_repeated_hits() -> None:
    client = TestClient(app)
    path = "/api/catalysts/feed?window_hours=72&limit=12"
    statuses = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(client.get, path) for _ in range(48)]
        for future in as_completed(futures):
            statuses.append(future.result().status_code)
    assert statuses
    assert all(code < 500 for code in statuses)
    assert any(code in {200, 503} for code in statuses)
