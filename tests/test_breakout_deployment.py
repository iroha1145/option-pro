from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_breakout_behavior_is_owned_by_personal_toml() -> None:
    config = tomllib.loads(
        (ROOT / "config" / "personal.toml").read_text(encoding="utf-8")
    )
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert config["features"]["breakout_enabled"] is True
    assert config["breakout"] == {
        "regular_seconds": 300,
        "premarket_seconds": 600,
        "closed_seconds": 1800,
        "range_persistence_mode": "shadow",
    }
    assert "BREAKOUT_RADAR_ENABLED=" not in environment
    assert "BREAKOUT_SCAN_INTERVAL" not in environment
    assert "RANGE_PERSISTENCE_MODE=" not in environment


def test_breakout_runs_inside_the_unified_worker() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    worker = compose["services"]["worker"]

    assert set(compose["services"]) == {"backend", "worker"}
    assert worker["command"] == ["python", "-m", "app.worker"]
    assert "ports" not in worker
    assert worker["environment"]["DATA_DIR"] == "${DATA_DIR:-/data}"
    assert "BREAKOUT_DB_PATH" not in worker["environment"]
    assert "optix-data:/data" in worker["volumes"]


def test_deployment_checks_only_the_unified_worker_inventory() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert 'compose exec -T worker python -m app.worker --healthcheck' in script
    for task_name in (
        "breakout",
        "catalyst_sync",
        "focus",
        "ai_jobs",
        "maintenance",
        "stock_directory",
        "public_home",
        "earnings_analysis",
        "focus_refresh",
        "strength_refresh",
        "breakout_refresh",
        "retention",
    ):
        assert f'"{task_name}"' in script
    assert "all twelve task types" in script
    assert "verify_public_snapshots" in script
    release_gate = (
        ROOT / "backend" / "app" / "tools" / "verify_release_data.py"
    ).read_text(encoding="utf-8")
    assert '"watchlist": watchlist_ready' in release_gate
    assert "PUBLIC_HOME_RESOURCE_ORDER" in release_gate
    assert "app.services.breakouts.worker --healthcheck" not in script
    assert "app.services.ai_jobs.worker --healthcheck" not in script
    assert "app.services.catalysts.worker --healthcheck" not in script
    assert "--once" not in script
    assert "/api/ai/jobs/" not in script
    assert "/api/catalysts/refresh" not in script


def test_image_runs_as_non_root_and_keeps_license_notices() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "install -d -o app -g app -m 0700 /data" in dockerfile
    assert "COPY THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md" in dockerfile
    assert (
        "COPY third_party/BreakoutAnalysis-LICENSE /licenses/BreakoutAnalysis-LICENSE"
        in dockerfile
    )
    assert dockerfile.rfind("USER app") > dockerfile.rfind("COPY ")
