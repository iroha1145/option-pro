from __future__ import annotations

import copy
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router
from app.services.catalysts.focus_config import (
    FocusContextSettings,
    get_focus_context_settings,
)
from app.services.catalysts.focus_models import (
    FOCUS_SCHEMA_SHA256,
    FocusContextResponse,
    FocusSymbol,
)
from app.services.catalysts.focus_publisher import publish_focus_from_strength_payload
from app.services.catalysts.focus_universe import build_focus_context
from app.services.catalysts.repository import CatalystRepository
from app.services.catalysts.signing import sign_request


SECRET = "focus-secret-0123456789abcdef-0001"
PREVIOUS = "focus-secret-0123456789abcdef-0000"
NOW = datetime(2026, 7, 13, 15, tzinfo=timezone.utc)


def focus_settings(path, **overrides) -> FocusContextSettings:
    values = {
        "MACROLENS_CACHE_DB_PATH": path,
        "MACROLENS_FOCUS_KEY_ID": "focus-read",
        "MACROLENS_FOCUS_SECRET": SECRET,
        "MACROLENS_FOCUS_ALLOWED_CIDRS": "127.0.0.0/8",
    }
    values.update(overrides)
    return FocusContextSettings(_env_file=None, **values)


def row(index: int, *, ticker: str | None = None, dollar: float | None = None) -> dict:
    return {
        "ticker": ticker or f"T{index:02d}",
        "cumulative_dollar_volume": dollar if dollar is not None else 1_000_000-index,
        "avg_dollar_volume_20d": dollar if dollar is not None else 1_000_000-index,
        "session_change_pct": index / 10,
        "rvol_time_of_day": 1 + index / 100,
        "data_quality": 90,
        "universe_member": True,
        "intrinsic_strength_score": 99-index,
        "ranking_score": 98-index,
        "market_fit_score": 97-index,
    }


def test_focus_pool_uses_dollar_volume_hysteresis_and_bounded_replacements(tmp_path) -> None:
    settings = focus_settings(tmp_path / "focus.db")
    rows = [row(index) for index in range(1, 36)]
    previous = [f"T{index:02d}" for index in range(16, 31)]
    draft = build_focus_context(
        settings=settings,
        strength_rows=rows,
        canonical_symbols=[item["ticker"] for item in rows],
        previous_symbols=previous,
        as_of=NOW,
        data_through=NOW,
        market_session="regular",
        universe_version="themes-v1",
    )
    tickers = {item.ticker for item in draft.symbols}
    assert len(tickers - set(previous)) <= settings.max_replacements_per_cycle
    assert "T30" in tickers
    assert next(item for item in draft.symbols if item.ticker == "T30").dollar_volume_rank == 30


def test_active_breakout_bypasses_normal_replacement_limit_and_missing_prior_stays_stale(tmp_path) -> None:
    settings = focus_settings(
        tmp_path / "focus.db",
        FOCUS_MAX_REPLACEMENTS_PER_CYCLE=0,
        FOCUS_PRIORITY_WATCHLIST="CUSTOM",
    )
    prior = FocusSymbol(
        ticker="NVDA",
        validation_status="canonical",
        universe_reasons=["strength_top10"],
        session_change_pct=5,
        rvol_time_of_day=2,
        as_of=NOW,
        data_quality=.9,
    )
    draft = build_focus_context(
        settings=settings,
        strength_rows=[row(1, ticker="AMD")],
        breakout_rows=[{"ticker": "BREAK", "lifecycle_state": "CONFIRMED"}],
        canonical_symbols=["AMD", "BREAK"],
        previous_context=[prior],
        as_of=NOW,
        data_through=NOW,
        market_session="regular",
        universe_version="themes-v2",
    )
    by_ticker = {item.ticker: item for item in draft.symbols}
    assert by_ticker["BREAK"].breakout_state == "CONFIRMED"
    assert by_ticker["NVDA"].data_status == "stale"
    assert by_ticker["NVDA"].session_change_pct is None
    assert by_ticker["NVDA"].rvol_time_of_day is None
    assert by_ticker["CUSTOM"].validation_status == "unverified"


def test_focus_build_does_not_mutate_or_export_formal_scores(tmp_path) -> None:
    settings = focus_settings(tmp_path / "focus.db")
    source = [row(1, ticker="NVDA")]
    before = copy.deepcopy(source)
    draft = build_focus_context(
        settings=settings,
        strength_rows=source,
        canonical_symbols=["NVDA"],
        as_of=NOW,
        data_through=NOW,
        market_session="regular",
        universe_version="themes-v1",
    )
    assert source == before
    encoded = json.dumps(draft.model_dump(mode="json"))
    for forbidden in (
        "intrinsic_strength_score", "ranking_score", "market_fit_score", "option_score"
    ):
        assert forbidden not in encoded


def test_strength_view_filters_cannot_truncate_published_focus_pool(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=NOW)
    settings = focus_settings(path)
    canonical_rows = [row(index) for index in range(1, 36)]
    publish_focus_from_strength_payload(
        {
            "as_of": NOW.isoformat(),
            "universe_as_of": NOW.isoformat(),
            "universe_version": "themes-v1",
            "universe_count": 35,
            # Deliberately tiny user-facing view; it must be ignored.
            "rows": canonical_rows[:1],
            "results": canonical_rows[:1],
            "_focus_rows": canonical_rows,
        },
        settings=settings,
    )
    current = repository.current_focus_context()
    assert current is not None
    assert len(current.symbols) > 1
    assert {item.ticker for item in current.symbols}.issuperset(
        {f"T{index:02d}" for index in range(1, 11)}
    )
    assert next(item for item in current.symbols if item.ticker == "T01").dollar_volume_rank == 1


def _seed_focus(path) -> CatalystRepository:
    repository = CatalystRepository(path)
    repository.initialize(now=NOW)
    settings = focus_settings(path)
    draft = build_focus_context(
        settings=settings,
        strength_rows=[row(1, ticker="NVDA")],
        canonical_symbols=["NVDA"],
        as_of=NOW,
        data_through=NOW,
        market_session="regular",
        universe_version="themes-v1",
    )
    first = repository.publish_focus_context(draft, now=NOW)
    same = repository.publish_focus_context(draft, now=NOW)
    assert first.revision == same.revision == 1
    return repository


def test_focus_publish_rejects_out_of_order_and_data_regressing_candidates(tmp_path) -> None:
    path = tmp_path / "focus.db"
    repository = CatalystRepository(path)
    repository.initialize(now=NOW)
    settings = focus_settings(path)

    def draft(as_of: datetime, data_through: datetime):
        return build_focus_context(
            settings=settings,
            strength_rows=[row(1, ticker="NVDA")],
            canonical_symbols=["NVDA"],
            as_of=as_of,
            data_through=data_through,
            market_session="regular",
            universe_version="themes-v1",
        )

    # Model two scans completing in reverse order: the newer result wins the
    # transaction first and the late, older result must not get a new revision.
    newer = repository.publish_focus_context(
        draft(NOW + timedelta(minutes=20), NOW + timedelta(minutes=20)),
        now=NOW + timedelta(minutes=21),
    )
    late_older = repository.publish_focus_context(
        draft(NOW + timedelta(minutes=10), NOW + timedelta(minutes=10)),
        now=NOW + timedelta(minutes=22),
    )
    assert late_older == newer

    newer_with_old_data = repository.publish_focus_context(
        draft(NOW + timedelta(minutes=30), NOW + timedelta(minutes=15)),
        now=NOW + timedelta(minutes=31),
    )
    assert newer_with_old_data == newer
    assert repository.current_focus_context() == newer


class PeerAddress:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["client"] = ("127.0.0.1", 4242)
        await self.app(scope, receive, send)


def _client(settings: FocusContextSettings) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_focus_context_settings] = lambda: settings
    return TestClient(PeerAddress(app), base_url="https://option.example")


def _headers(secret: str, *, nonce: str = "nonce-0123456789abcdef") -> dict[str, str]:
    return sign_request(
        method="GET",
        path="/api/integrations/macrolens/v1/focus-context",
        params=None,
        body=b"",
        key_id="focus-read",
        secret=secret,
        timestamp=int(NOW.timestamp()),
        nonce=nonce,
    )


def test_focus_endpoint_hmac_rotation_replay_https_and_cidr(tmp_path, monkeypatch) -> None:
    path = tmp_path / "focus.db"
    _seed_focus(path)
    settings = focus_settings(
        path, MACROLENS_FOCUS_PREVIOUS_SECRET=PREVIOUS
    )
    client = _client(settings)
    monkeypatch.setattr("app.services.catalysts.focus_auth.datetime", _FrozenDateTime)
    response = client.get(
        "/api/integrations/macrolens/v1/focus-context", headers=_headers(PREVIOUS)
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_sha256"] == FOCUS_SCHEMA_SHA256
    assert "ranking_score" not in json.dumps(payload)
    replay = client.get(
        "/api/integrations/macrolens/v1/focus-context", headers=_headers(PREVIOUS)
    )
    assert replay.status_code == 409

    http_client = TestClient(PeerAddress(client.app), base_url="http://option.example")
    rejected = http_client.get(
        "/api/integrations/macrolens/v1/focus-context",
        headers=_headers(SECRET, nonce="nonce-http-1234567890"),
    )
    assert rejected.status_code == 403

    forbidden_settings = focus_settings(
        path, MACROLENS_FOCUS_ALLOWED_CIDRS="10.0.0.0/8"
    )
    forbidden = _client(forbidden_settings).get(
        "/api/integrations/macrolens/v1/focus-context",
        headers=_headers(SECRET, nonce="nonce-cidr-1234567890"),
    )
    assert forbidden.status_code == 403


def test_focus_get_does_not_create_a_missing_cache(tmp_path) -> None:
    path = tmp_path / "missing.db"
    response = _client(focus_settings(path)).get(
        "/api/integrations/macrolens/v1/focus-context",
        headers=_headers(SECRET, nonce="nonce-missing-123456789"),
    )
    assert response.status_code == 503
    assert not path.exists()


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def test_focus_contract_is_exact_model_schema_and_pinned_digest() -> None:
    path = Path(__file__).resolve().parents[1] / "contracts" / "option-pro-macrolens-focus-v1.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FOCUS_SCHEMA_SHA256
    assert json.loads(raw) == FocusContextResponse.model_json_schema()


def test_focus_settings_fail_closed_for_rotation_and_nonce_window(tmp_path) -> None:
    with pytest.raises(ValueError, match="must differ"):
        focus_settings(tmp_path / "a.db", MACROLENS_FOCUS_PREVIOUS_SECRET=SECRET)
    with pytest.raises(ValueError, match="cover the clock skew"):
        focus_settings(
            tmp_path / "b.db",
            MACROLENS_FOCUS_CLOCK_SKEW_SECONDS=600,
            MACROLENS_FOCUS_NONCE_TTL_SECONDS=300,
        )


def test_gateway_exempts_only_exact_focus_get_from_browser_token(tmp_path, monkeypatch) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    from app import main

    path = tmp_path / "focus.db"
    _seed_focus(path)
    settings = focus_settings(path)
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "browser-only-token")
    monkeypatch.setattr("app.services.catalysts.focus_auth.datetime", _FrozenDateTime)
    main.app.dependency_overrides[get_focus_context_settings] = lambda: settings
    try:
        client = TestClient(PeerAddress(main.app), base_url="https://localhost")
        get_response = client.get(
            "/api/integrations/macrolens/v1/focus-context",
            headers=_headers(SECRET, nonce="nonce-gateway-123456789"),
        )
        assert get_response.status_code == 200
        post_response = client.post(
            "/api/integrations/macrolens/v1/focus-context"
        )
        assert post_response.status_code == 401
    finally:
        main.app.dependency_overrides.pop(get_focus_context_settings, None)
