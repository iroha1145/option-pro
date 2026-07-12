from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z0-9][a-z0-9-]*:\n|^volumes:\n|\Z)",
        compose,
    )
    assert match is not None, f"missing Compose service: {service}"
    return match.group(1)


def _env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator, f"invalid environment line: {raw_line}"
        assert key not in values, f"duplicate environment key: {key}"
        values[key] = value
    return values


def test_breakout_bootstrap_environment_is_complete_and_off_by_default() -> None:
    values = _env_values()
    expected = {
        "BREAKOUT_RADAR_ENABLED": "false",
        "DEPLOY_REQUIRE_BREAKOUT": "false",
        "BREAKOUT_DISCOVERY_PROVIDER": "tradingview",
        "BREAKOUT_DB_PATH": "/data/optix.db",
        "BREAKOUT_PROVIDER_TIMEOUT_SECONDS": "10",
        "BREAKOUT_PROVIDER_CACHE_TTL_SECONDS": "60",
        "BREAKOUT_PROVIDER_STALE_TTL_SECONDS": "900",
        "BREAKOUT_PROVIDER_FAILURE_THRESHOLD": "3",
        "BREAKOUT_PROVIDER_MAX_RESPONSE_BYTES": "2000000",
        "BREAKOUT_PROVIDER_MAX_CONCURRENCY": "4",
        "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS": "600",
        "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS": "300",
        "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS": "1800",
        "BREAKOUT_WORKER_LEASE_TTL_SECONDS": "90",
        "BREAKOUT_WORKER_HEALTH_STALE_SECONDS": "120",
        "BREAKOUT_RAW_PAYLOAD_RETENTION_HOURS": "24",
        "BREAKOUT_SCAN_RETENTION_DAYS": "30",
        "BREAKOUT_RETENTION_BATCH_SIZE": "500",
        "BREAKOUT_PROVIDER_RESULT_LIMIT": "150",
        "BREAKOUT_DAILY_ENRICH_LIMIT": "60",
        "BREAKOUT_INTRADAY_ENRICH_LIMIT": "30",
        "BREAKOUT_EXPIRED_DUE_LIMIT": "40",
        "BREAKOUT_MIN_PRICE": "2.0",
        "BREAKOUT_MIN_AVG_DOLLAR_VOLUME": "10000000",
        "BREAKOUT_REGULAR_MIN_CHANGE_PCT": "3.0",
        "BREAKOUT_REGULAR_MIN_RELATIVE_VOLUME": "1.5",
        "BREAKOUT_PREMARKET_MIN_CHANGE_PCT": "5.0",
        "BREAKOUT_PREMARKET_MIN_DOLLAR_VOLUME": "500000",
        "BREAKOUT_BASE_MIN_DAYS": "10",
        "BREAKOUT_BASE_MAX_DAYS": "80",
        "BREAKOUT_PIVOT_TOLERANCE_ATR": "0.50",
        "BREAKOUT_BREAK_BUFFER_ATR": "0.10",
        "BREAKOUT_BREAK_BUFFER_PCT": "0.0025",
        "BREAKOUT_OPENING_RANGE_MINUTES": "30",
        "BREAKOUT_CONFIRMATION_BARS": "2",
        "BREAKOUT_MAX_CHASE_DISTANCE_ATR": "2.0",
        "BREAKOUT_EVENT_TTL_SECONDS": "86400",
        "BREAKOUT_SCORING_VERSION": "breakout-score-v1",
        "BREAKOUT_FEATURE_VERSION": "breakout-features-v1",
        "BREAKOUT_DETECTOR_VERSION": "breakout-detector-v1",
        "BREAKOUT_API_SCHEMA_VERSION": "breakout-api-v1",
        "MARKET_SHAPE_ENTER_CONFIRM_DAYS": "2",
        "MARKET_SHAPE_EXIT_CONFIRM_DAYS": "2",
        "MARKET_SHAPE_MIN_DWELL_DAYS": "3",
        "MARKET_SHAPE_HISTORY_DAYS": "20",
        "RANGE_PERSISTENCE_MODE": "shadow",
        "RANGE_PERSISTENCE_VERSION": "range-persistence-v1",
        "RANGE_PERSISTENCE_VALIDATION_VERSION": "",
        "RANGE_PERSISTENCE_LENGTH": "35",
        "RANGE_PERSISTENCE_FAST_LENGTH": "3",
        "RANGE_PERSISTENCE_SLOPE_DAYS": "5",
        "RANGE_PERSISTENCE_RATIO_WINDOW": "10",
        "RANGE_PERSISTENCE_RATIO_THRESHOLD": "60",
        "RANGE_PERSISTENCE_MIN_HISTORY_MULTIPLIER": "5",
        "RANGE_PERSISTENCE_TREND_FAMILY_WEIGHT": "0.15",
        "RANGE_PERSISTENCE_FINAL_WEIGHT_CAP": "0.04",
        "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED": "false",
        "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_CAP": "4.0",
    }
    assert {key: values.get(key) for key in expected} == expected


def test_backend_and_worker_share_one_writable_data_volume_and_image() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    backend = _service_block(compose, "backend")
    worker = _service_block(compose, "breakout-worker")

    image = 'image: "option-pro:${APP_COMMIT:-local}"'
    assert image in backend
    assert image in worker
    assert "APP_COMMIT=${APP_COMMIT:-unknown}" in backend
    assert "APP_COMMIT=${APP_COMMIT:-unknown}" in worker
    assert "optix-data:/data" in backend
    assert "optix-data:/data" in worker
    assert "optix-data:/data:ro" not in compose
    validation_version = (
        "RANGE_PERSISTENCE_VALIDATION_VERSION="
        "${RANGE_PERSISTENCE_VALIDATION_VERSION:-}"
    )
    assert validation_version in backend
    assert validation_version in worker
    for key in (
        "MARKET_SHAPE_ENTER_CONFIRM_DAYS",
        "MARKET_SHAPE_EXIT_CONFIRM_DAYS",
        "MARKET_SHAPE_MIN_DWELL_DAYS",
        "MARKET_SHAPE_HISTORY_DAYS",
        "RANGE_PERSISTENCE_VERSION",
        "RANGE_PERSISTENCE_LENGTH",
        "RANGE_PERSISTENCE_FAST_LENGTH",
        "RANGE_PERSISTENCE_SLOPE_DAYS",
        "RANGE_PERSISTENCE_RATIO_WINDOW",
        "RANGE_PERSISTENCE_RATIO_THRESHOLD",
        "RANGE_PERSISTENCE_MIN_HISTORY_MULTIPLIER",
        "RANGE_PERSISTENCE_TREND_FAMILY_WEIGHT",
        "RANGE_PERSISTENCE_FINAL_WEIGHT_CAP",
        "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED",
        "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_CAP",
    ):
        assert f"{key}=${{{key}:-" in backend
        assert f"{key}=${{{key}:-" in worker
    assert "BREAKOUT_EXPIRED_DUE_LIMIT=${BREAKOUT_EXPIRED_DUE_LIMIT:-40}" in worker
    assert "MARKET_SHAPE_VERSION=${MARKET_SHAPE_VERSION:-" not in compose
    assert re.search(r"(?m)^volumes:\n  optix-data:\s*$", compose)


def test_deployment_respects_optional_radar_gate_and_shadow_defaults() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "DEPLOY_REQUIRE_BREAKOUT=true requires BREAKOUT_RADAR_ENABLED=true" in script
    assert "RANGE_PERSISTENCE_MODE must be disabled, shadow, or enabled" in script
    assert "Range Persistence interactions are disabled" not in script
    assert "payload.get(\"enabled\")" in script
    assert "from app.services.strength.market_shape import MARKET_SHAPE_VERSION" in script
    assert "EXPECTED_MARKET_SHAPE_VERSION" not in script
    assert "market-shape-v2" not in script
    assert "app.services.breakouts.worker --healthcheck" in script
    assert 'WORKER_HEALTH=${worker_health}' in script
    assert "json.loads(os.environ[\"WORKER_HEALTH\"])" in script


def test_worker_is_isolated_and_has_an_independent_healthcheck() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = _service_block(compose, "breakout-worker")

    assert "ports:" not in worker
    assert "env_file:" not in worker
    assert "OPENAI_API_KEY" not in worker
    assert "APP_AUTH_TOKEN" not in worker
    assert "build:" not in worker
    assert "BREAKOUT_RADAR_ENABLED=${BREAKOUT_RADAR_ENABLED:-false}" in worker
    assert "python -m app.services.breakouts.worker" in worker
    assert '"--healthcheck"' in worker
    assert "healthcheck:" in worker


def test_both_runtime_services_keep_the_container_security_baseline() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("backend", "breakout-worker"):
        service = _service_block(compose, name)
        assert "read_only: true" in service
        assert "no-new-privileges:true" in service
        assert re.search(r"(?ms)cap_drop:\n\s+- ALL", service)
        assert "/tmp:rw,noexec,nosuid,size=134217728,mode=1777" in service
        assert "pids_limit: 256" in service
        assert 'max-size: "10m"' in service
        assert 'max-file: "3"' in service


def test_image_runs_as_non_root_and_prepares_private_data_directory() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "install -d -o app -g app -m 0700 /data" in dockerfile
    assert "COPY THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md" in dockerfile
    assert "COPY third_party/BreakoutAnalysis-LICENSE /licenses/BreakoutAnalysis-LICENSE" in dockerfile
    assert 'org.opencontainers.image.revision="${APP_COMMIT}"' in dockerfile
    assert dockerfile.rfind("USER app") > dockerfile.rfind("COPY ")
