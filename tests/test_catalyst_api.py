from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.catalysts import router
from app.services.catalysts.config import CatalystSettings, get_catalyst_settings
from app.services.catalysts.repository import CatalystRepository
from app.services.ai_jobs.security import require_expensive_action
from catalyst_support import catalyst_item, utc


READ_SECRET = "read-secret-0123456789abcdef-0001"
ACTION_SECRET = "action-secret-0123456789abcdef-01"


def configured(path, *, enabled: bool = True, action: bool = True) -> CatalystSettings:
    values = {
        "MACROLENS_ENABLED": enabled,
        "MACROLENS_CACHE_DB_PATH": path,
    }
    if enabled:
        values.update(
            {
                "MACROLENS_BASE_URL": "http://localhost:9876",
                "MACROLENS_ALLOW_LOCAL_HTTP": True,
                "MACROLENS_READ_KEY_ID": "read-key",
                "MACROLENS_READ_SECRET": READ_SECRET,
                "MACROLENS_STALE_TTL_SECONDS": 2_592_000,
            }
        )
    if action:
        values.update(
            {
                "MACROLENS_ACTION_KEY_ID": "action-key",
                "MACROLENS_ACTION_SECRET": ACTION_SECRET,
            }
        )
    return CatalystSettings(_env_file=None, **values)


def client_for(settings: CatalystSettings, *, authorize_actions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_catalyst_settings] = lambda: settings
    if authorize_actions:
        app.dependency_overrides[require_expensive_action] = lambda: None
    return TestClient(app, base_url="http://localhost")


def seed(path) -> CatalystRepository:
    repository = CatalystRepository(path)
    repository.initialize(now=utc(9))
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-api", now=utc(10, 6)
    )
    repository.stage_latest_page(
        run_id, [catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)]
    )
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-api",
        data_through=utc(10, 6),
        next_updated_after=utc(10, 6),
        watermark_sequence=2,
        now=utc(10, 6),
    )
    repository.publish_health(
        status="ok",
        data_through=utc(10, 6),
        sources={},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        analysis_trigger_enabled=True,
        observed_at=utc(10, 6),
    )
    return repository


def test_disabled_status_and_feed_do_not_create_database(tmp_path) -> None:
    path = tmp_path / "must-not-exist.db"
    client = client_for(configured(path, enabled=False, action=False))
    status_response = client.get("/api/catalysts/status")
    feed_response = client.get("/api/catalysts/feed")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "disabled"
    assert feed_response.status_code == 200
    assert feed_response.json()["status"] == "disabled"
    assert feed_response.json()["summary"]["news_6h"] is None
    assert not path.exists()


def test_all_get_routes_read_local_cache_without_remote_side_effect(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    seed(path)
    client = client_for(configured(path))
    as_of = "2026-07-11T10:07:00Z"

    status_response = client.get("/api/catalysts/status")
    feed = client.get(f"/api/catalysts/feed?as_of={as_of}")
    news = client.get(f"/api/catalysts/news/101?as_of={as_of}")
    ticker = client.get(f"/api/catalysts/tickers/NVDA?as_of={as_of}")
    batch = client.post(
        "/api/catalysts/tickers/batch",
        json={"tickers": ["NVDA", "AMD"], "as_of": as_of},
    )

    assert status_response.status_code == 200
    assert feed.status_code == 200
    assert feed.json()["items"][0]["analysis"]["classification"] == "bullish"
    assert feed.json()["summary"]["bullish"] == 1
    assert feed.json()["stock_impacts"][0]["ticker"] == "NVDA"
    assert feed.json()["stock_impacts"][0]["display_sort_only"] is True
    assert news.status_code == 200
    assert news.json()["analysis_job"] is None
    assert news.json()["analysis_trigger_enabled"] is True
    assert ticker.status_code == 200
    assert ticker.json()["ticker"] == "NVDA"
    assert batch.status_code == 200
    assert batch.json()["results"]["NVDA"]["items"]
    assert batch.json()["results"]["AMD"]["status"] == "empty"


def test_post_only_enqueues_refresh_and_opaque_analysis_job(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = seed(path)
    client = client_for(configured(path))

    refresh = client.post("/api/catalysts/refresh")
    analysis = client.post("/api/catalysts/news/101/analysis", json={"force": False})
    assert refresh.status_code == 202
    assert refresh.json()["status"] == "queued"
    assert analysis.status_code == 202
    local_job_id = analysis.json()["job_id"]
    assert len(local_job_id) == 32
    assert "remote" not in analysis.text.lower()
    assert "openai" not in analysis.text.lower()

    job = client.get(f"/api/catalysts/analysis-jobs/{local_job_id}")
    historical_news = client.get(
        "/api/catalysts/news/101?as_of=2026-07-11T10:07:00Z"
    )
    current_news = client.get("/api/catalysts/news/101")
    assert job.status_code == 200
    assert job.json()["status"] == "pending"
    assert historical_news.json()["analysis_job"] is None
    assert current_news.json()["analysis_job"]["job_id"] == local_job_id
    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM catalyst_refresh_outbox WHERE status='pending'"
        ).fetchone()[0] == 1


def test_news_detail_does_not_attach_a_job_from_an_older_revision(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = seed(path)
    repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=2,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )
    revised = catalyst_item(
        sequence=3, updated_at=utc(10, 8), analysis=False
    ).model_copy(update={"content_hash": "content-hash-101-corrected"})
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-corrected", now=utc(10, 8)
    )
    repository.stage_latest_page(run_id, [revised])
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-corrected",
        data_through=utc(10, 8),
        next_updated_after=utc(10, 8),
        watermark_sequence=3,
        now=utc(10, 8),
    )

    client = client_for(configured(path))
    historical = client.get("/api/catalysts/news/101?as_of=2026-07-11T10:07:00Z")
    current = client.get("/api/catalysts/news/101?as_of=2026-07-11T10:09:00Z")
    assert historical.status_code == 200
    assert historical.json()["analysis_job"] is not None
    assert current.status_code == 200
    assert current.json()["item"]["content_hash"] == "content-hash-101-corrected"
    assert current.json()["analysis_job"] is None


def test_feed_cursor_freezes_as_of_when_next_page_omits_it(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = CatalystRepository(path)
    repository.initialize(now=utc(9))
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-pagination", now=utc(10, 6)
    )
    repository.stage_latest_page(
        run_id,
        [
            catalyst_item(
                sequence=1,
                updated_at=utc(10, 5),
                analysis=False,
                news_id=101,
            ),
            catalyst_item(
                sequence=2,
                updated_at=utc(10, 6),
                analysis=False,
                news_id=102,
                ticker="AMD",
            ),
        ],
    )
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-pagination",
        data_through=utc(10, 6),
        next_updated_after=utc(10, 6),
        watermark_sequence=2,
        now=utc(10, 6),
    )
    repository.publish_health(
        status="ok",
        data_through=utc(10, 6),
        sources={},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        analysis_trigger_enabled=True,
        observed_at=utc(10, 6),
    )
    client = client_for(configured(path))
    first = client.get(
        "/api/catalysts/feed?as_of=2026-07-11T10:07:00Z&limit=1"
    )
    assert first.status_code == 200
    assert first.json()["has_more"] is True

    second = client.get(
        "/api/catalysts/feed",
        params={"limit": 1, "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["as_of"] == first.json()["as_of"]
    assert second.json()["items"][0]["news_id"] != first.json()["items"][0]["news_id"]


def test_feed_rejects_malformed_cursor_as_a_safe_client_error(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    seed(path)
    response = client_for(configured(path)).get(
        "/api/catalysts/feed?cursor=not-valid-base64!"
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_cursor"


def test_action_capability_missing_keeps_reads_but_rejects_analysis(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    seed(path)
    client = client_for(configured(path, action=False))
    feed = client.get("/api/catalysts/feed?as_of=2026-07-11T10:07:00Z")
    analysis = client.post("/api/catalysts/news/101/analysis", json={})
    assert feed.status_code == 200
    assert analysis.status_code == 503
    assert analysis.json()["detail"]["code"] == "capability_disabled"


def test_expensive_catalyst_posts_fail_closed_without_app_auth_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    path = tmp_path / "catalysts.db"
    repository = seed(path)
    queued = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=2,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
    )
    client = client_for(configured(path), authorize_actions=False)

    refresh = client.post("/api/catalysts/refresh")
    analysis = client.post("/api/catalysts/news/101/analysis", json={})
    cancel = client.post(f"/api/catalysts/analysis-jobs/{queued['job_id']}/cancel")
    assert refresh.status_code == 503
    assert analysis.status_code == 503
    assert cancel.status_code == 503
    assert refresh.json()["detail"]["code"] == "capability_disabled"
    with repository.open_read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM catalyst_refresh_outbox").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM catalyst_analysis_jobs").fetchone()[0] == "pending"


def test_expensive_catalyst_posts_require_authenticated_gateway_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_AUTH_TOKEN", "configured-test-token")
    path = tmp_path / "catalysts.db"
    repository = seed(path)
    queued = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=2,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
    )
    client = client_for(configured(path), authorize_actions=False)

    refresh = client.post("/api/catalysts/refresh")
    analysis = client.post("/api/catalysts/news/101/analysis", json={})
    cancel = client.post(f"/api/catalysts/analysis-jobs/{queued['job_id']}/cancel")
    assert refresh.status_code == 401
    assert analysis.status_code == 401
    assert cancel.status_code == 401
