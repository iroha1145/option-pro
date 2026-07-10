from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.ai import AlertsRequest, _MAX_AI_BODY_BYTES, router
from app.config import Settings, _ROOT_ENV_FILE
from app.services import ai_analysis


def _valid_alert(**overrides):
    alert = {
        "strike": 200.0,
        "type": "call",
        "expiration": "2026-08-21",
        "dte": 42,
        "volume": 1200,
        "open_interest": 400,
        "last_price": 2.5,
        "implied_volatility": 0.42,
        "premium_flow": 300_000,
        "vol_oi_ratio": 3.0,
        "reasons": ["成交量明显高于持仓量"],
        "signal": "unknown",
        "inferred_direction": "unknown",
        "direction_note": "缺少 bid/ask 成交位置",
    }
    alert.update(overrides)
    return alert


def test_root_env_path_is_independent_of_working_directory():
    expected = Path(__file__).resolve().parents[1] / ".env"
    assert _ROOT_ENV_FILE == expected
    assert Path(Settings.model_config["env_file"]) == expected


def test_custom_openai_base_url_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ALLOW_CUSTOM_OPENAI_BASE_URL", raising=False)

    with pytest.raises(ValidationError, match="ALLOW_CUSTOM_OPENAI_BASE_URL"):
        Settings(_env_file=None, openai_base_url="https://proxy.example/v1")

    settings = Settings(
        _env_file=None,
        openai_base_url="https://proxy.example/v1/",
        allow_custom_openai_base_url=True,
    )
    assert settings.openai_base_url == "https://proxy.example/v1"

    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            _env_file=None,
            openai_base_url="http://proxy.example/v1",
            allow_custom_openai_base_url=True,
        )


def test_alert_request_rejects_unbounded_or_unexpected_input():
    with pytest.raises(ValidationError):
        AlertsRequest.model_validate({"ticker": "AAPL;DROP", "alerts": []})

    with pytest.raises(ValidationError):
        AlertsRequest.model_validate({
            "ticker": "AAPL",
            "alerts": [_valid_alert()] * 11,
        })

    with pytest.raises(ValidationError):
        AlertsRequest.model_validate({
            "ticker": "AAPL",
            "alerts": [_valid_alert(unexpected="value")],
        })

    with pytest.raises(ValidationError):
        AlertsRequest.model_validate({
            "ticker": "AAPL",
            "alerts": [_valid_alert(reasons=["x" * 161])],
        })


def test_ai_route_rejects_body_larger_than_64_kib():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/ai/analyze-alerts",
        content=b"x" * (_MAX_AI_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_identical_ai_requests_use_single_flight(monkeypatch):
    settings = SimpleNamespace(
        openai_model="test-model",
        openai_reasoning="low",
        openai_timeout_seconds=2.0,
        openai_max_output_tokens=256,
        openai_max_concurrency=2,
    )
    calls = []
    calls_lock = threading.Lock()

    class FakeResponses:
        def create(self, **kwargs):
            with calls_lock:
                calls.append(kwargs)
            time.sleep(0.1)
            return SimpleNamespace(output_text='{"summary":"ok"}', output=[])

    fake_client = SimpleNamespace(responses=FakeResponses())
    monkeypatch.setattr(ai_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_analysis, "_get_client", lambda: fake_client)
    ai_analysis._inflight.clear()

    start = threading.Barrier(2)

    def run():
        start.wait()
        return ai_analysis._ask("same prompt", use_web_search=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: run(), range(2)))

    assert results == ['{"summary":"ok"}', '{"summary":"ok"}']
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == 256
    assert "tools" not in calls[0]
