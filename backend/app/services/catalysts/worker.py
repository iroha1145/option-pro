from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import signal
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from .client import MacroLensClient
from .config import CatalystSettings, get_catalyst_settings
from .errors import CatalystError, CatalystRepositoryError
from .models import SCHEMA_VERSION
from .repository import CatalystRepository
from .sync_service import CatalystSyncService


logger = logging.getLogger("optix.catalysts.worker")
CONTRACT_PATH = Path(__file__).resolve().parents[4] / "contracts" / "macrolens-option-pro-v2.json"
# Updated only when the reviewed, byte-identical contract changes in both repos.
PINNED_CONTRACT_SHA256 = "29d65fc52d1d9c4a8cb3c665cb0dbaf2cf6ee6d3d91f2e16b16b0a480a65209b"


def _contract_health(
    settings: CatalystSettings,
    *,
    contract_path: Path = CONTRACT_PATH,
    pinned_sha256: str = PINNED_CONTRACT_SHA256,
) -> dict[str, Any]:
    if not contract_path.is_file():
        return {"status": "missing", "valid": False, "schema_version": SCHEMA_VERSION}
    try:
        raw = contract_path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "invalid", "valid": False, "schema_version": SCHEMA_VERSION}
    digest = hashlib.sha256(raw).hexdigest()
    configured = settings.schema_sha256
    valid = (
        document.get("schema_version") == SCHEMA_VERSION
        and digest == pinned_sha256
        and (not configured or configured == digest)
    )
    return {
        "status": "ok" if valid else "mismatch",
        "valid": valid,
        "schema_version": document.get("schema_version"),
        "schema_sha256": digest,
    }


def health_payload(
    settings: CatalystSettings,
    *,
    repository: Optional[CatalystRepository] = None,
    contract_path: Path = CONTRACT_PATH,
    pinned_sha256: str = PINNED_CONTRACT_SHA256,
) -> dict[str, Any]:
    """Return local-only health; remote outages never make the container fail."""

    if not settings.enabled or settings.catalyst_mode == "disabled":
        return {
            "healthy": True,
            "status": "disabled",
            "enabled": False,
            "schema_version": settings.schema_version,
            "contract": {"status": "not_checked", "valid": None},
            "database": {"status": "not_checked"},
            "remote_checked": False,
        }
    contract = _contract_health(
        settings, contract_path=contract_path, pinned_sha256=pinned_sha256
    )
    local_repository = repository or CatalystRepository(settings.cache_db_path, read_only=True)
    try:
        database = local_repository.worker_health(
            heartbeat_ttl_seconds=max(settings.worker_lease_seconds * 2, 30)
        )
    except CatalystError as error:
        database = {
            "healthy": False,
            "status": "unavailable",
            "error_code": error.code,
        }
    except (OSError, ValueError, sqlite3.Error):
        database = {
            "healthy": False,
            "status": "unavailable",
            "error_code": "cache_unavailable",
        }
    healthy = bool(contract["valid"] and database.get("healthy"))
    return {
        "healthy": healthy,
        "status": "ok" if healthy else "unhealthy",
        "enabled": True,
        "schema_version": settings.schema_version,
        "contract": contract,
        "database": database,
        "remote_checked": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Option Pro Catalyst sync worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one local sync cycle")
    mode.add_argument("--healthcheck", action="store_true", help="check local worker health")
    mode.add_argument(
        "--migrate",
        action="store_true",
        help="initialize or migrate the local Catalyst cache without contacting MacroLens",
    )
    mode.add_argument(
        "--request-refresh",
        action="store_true",
        help="request a local health and feed refresh without contacting MacroLens",
    )
    return parser


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            pass


def _migration_database_status(repository: CatalystRepository) -> dict[str, Any]:
    database = repository.check_schema()
    with repository.open_read_connection() as connection:
        quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        integrity_rows = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if quick_rows != ["ok"] or integrity_rows != ["ok"] or foreign_key_rows:
        raise CatalystRepositoryError(
            "cache_integrity_failed",
            "Catalyst cache integrity checks failed after migration",
        )
    database.update(
        {
            "quick_check": "ok",
            "integrity_check": "ok",
            "foreign_key_check": "ok",
        }
    )
    return database


def deployment_status_ready(
    payload: dict[str, Any],
    *,
    required_after_epoch: float,
    actions_required: bool,
) -> bool:
    """Accept only a remote health and feed success from this deployment.

    The local cache survives container replacement.  A status may therefore
    look active for a short time even when the replacement container has not
    authenticated to MacroLens yet.  The deployment gate supplies a cutoff
    captured immediately before it requests a refresh; both remote health and
    feed must publish success at or after that cutoff.
    """

    try:
        cutoff = float(required_after_epoch)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(cutoff) or cutoff <= 0:
        return False

    streams = payload.get("streams")
    if not isinstance(streams, dict):
        return False

    def succeeded_after(stream: str) -> bool:
        state = streams.get(stream)
        if not isinstance(state, dict):
            return False
        value = state.get("last_success_at")
        if not isinstance(value, str) or not value:
            return False
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if observed.tzinfo is None or observed.utcoffset() is None:
            return False
        return observed.timestamp() >= cutoff

    read_ready = (
        payload.get("enabled") is True
        and payload.get("status") == "active"
        and payload.get("remote_status") in {"ok", "active"}
        and payload.get("last_sync_at") is not None
        and payload.get("snapshot_id") is not None
        and payload.get("resync_required") is False
        and succeeded_after("health")
        and succeeded_after("feed")
    )
    return bool(
        read_ready
        and (
            not actions_required
            or payload.get("analysis_trigger_enabled") is True
        )
    )


async def _wait_disabled(stop: asyncio.Event) -> None:
    logger.info("Catalyst sync worker is disabled")
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            continue


async def _async_main(args: argparse.Namespace) -> int:
    try:
        settings = get_catalyst_settings()
    except Exception:
        print(
            json.dumps(
                {
                    "healthy": False,
                    "status": "invalid_configuration",
                    "error_code": "configuration_error",
                },
                separators=(",", ":"),
            )
        )
        return 1

    if args.healthcheck:
        payload = health_payload(settings)
        print(json.dumps(payload, allow_nan=False, separators=(",", ":")))
        return 0 if payload["healthy"] else 1

    if getattr(args, "migrate", False):
        repository = CatalystRepository(settings.cache_db_path)
        try:
            repository.initialize()
            database = _migration_database_status(repository)
        except CatalystError as error:
            print(
                json.dumps(
                    {
                        "status": "migration_failed",
                        "error_code": error.code,
                    },
                    separators=(",", ":"),
                )
            )
            return 1
        except (OSError, ValueError, sqlite3.Error):
            print(
                json.dumps(
                    {
                        "status": "migration_failed",
                        "error_code": "cache_migration_failed",
                    },
                    separators=(",", ":"),
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "status": "migrated",
                    "enabled": settings.enabled,
                    "database": database,
                    "remote_checked": False,
                },
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        return 0

    if getattr(args, "request_refresh", False):
        repository = CatalystRepository(settings.cache_db_path)
        try:
            repository.check_schema()
            repository.enqueue_refresh(("health", "feed"))
        except CatalystError as error:
            print(
                json.dumps(
                    {
                        "status": "refresh_request_failed",
                        "error_code": error.code,
                    },
                    separators=(",", ":"),
                )
            )
            return 1
        except (OSError, ValueError, sqlite3.Error):
            print(
                json.dumps(
                    {
                        "status": "refresh_request_failed",
                        "error_code": "cache_refresh_failed",
                    },
                    separators=(",", ":"),
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "status": "refresh_requested",
                    "streams": ["health", "feed"],
                    "remote_checked": False,
                },
                separators=(",", ":"),
            )
        )
        return 0

    if not settings.enabled or settings.catalyst_mode == "disabled":
        if args.once:
            print(
                json.dumps(
                    {"status": "disabled", "enabled": False, "processed": []},
                    separators=(",", ":"),
                )
            )
            return 0
        stop = asyncio.Event()
        _install_stop_handlers(stop)
        await _wait_disabled(stop)
        return 0

    repository = CatalystRepository(settings.cache_db_path)
    repository.initialize()
    async with MacroLensClient(settings) as client:
        service = CatalystSyncService(settings, repository, client)
        if args.once:
            try:
                result = await service.run_once()
                print(json.dumps(result, allow_nan=False, separators=(",", ":")))
                return 0
            finally:
                service.release()
        stop = asyncio.Event()
        _install_stop_handlers(stop)
        await service.run_forever(stop=stop)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["deployment_status_ready", "health_payload", "main"]
