from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.breakouts.repository import BreakoutRepository

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows
    resource = None


NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
FIRST_SEEN_AT = NOW - timedelta(minutes=30)
SNAPSHOT_PADDING = "x" * 100_000


def _event(event_number: int, version: int, observed_at: datetime) -> dict:
    ticker = f"T{event_number:02d}"
    return {
        "event_id": f"event-{event_number:02d}",
        "trading_date": FIRST_SEEN_AT.date(),
        "ticker": ticker,
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "TRIGGERED",
        "previous_state": "WATCHING",
        "transition_reason": "pivot_crossed",
        "event_at": FIRST_SEEN_AT,
        "first_seen_at": FIRST_SEEN_AT,
        "last_seen_at": observed_at,
        "pivot_id": f"pivot-{ticker}",
        "source_snapshot_id": "source-snapshot",
        "scores": {
            "alert_priority_score": 80.0,
            "data_confidence_score": 90.0,
        },
        "fixture_version": version,
        "padding": SNAPSHOT_PADDING,
    }


def _publish_version(
    repo: BreakoutRepository,
    version: int,
    observed_at: datetime,
) -> None:
    events = [_event(number, version, observed_at) for number in range(10)]
    scan_id = repo.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=observed_at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        versions={"database": "breakout-db-v1"},
        now=observed_at,
    )
    repo.publish_scan(
        scan_id,
        {
            "provider_snapshot": {
                "provider": "fixture",
                "status": "active",
                "as_of": observed_at,
                "session": "regular",
                "schema_version": "fixture-v1",
                "warnings": [],
                "candidates": [],
            },
            "events": events,
        },
        now=observed_at,
    )


@pytest.mark.skipif(
    os.name != "posix"
    or resource is None
    or not hasattr(resource, "RLIMIT_FSIZE"),
    reason="requires POSIX RLIMIT_FSIZE",
)
def test_carryover_history_fits_a_bounded_sqlite_temp_filesystem(tmp_path) -> None:
    database_path = tmp_path / "carryover-history.db"
    repo = BreakoutRepository(database_path)
    repo.initialize()

    for version in range(5):
        _publish_version(
            repo,
            version,
            FIRST_SEEN_AT + timedelta(minutes=version),
        )

    sqlite_temp_dir = tmp_path / "sqlite-temp"
    sqlite_temp_dir.mkdir()
    backend_path = Path(__file__).resolve().parents[1] / "backend"
    environment = os.environ.copy()
    environment["SQLITE_TMPDIR"] = str(sqlite_temp_dir)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(backend_path), existing_pythonpath)
        if value
    )

    child_code = textwrap.dedent(
        """
        import json
        import resource
        import signal
        import sys
        from datetime import datetime
        from pathlib import Path

        if hasattr(signal, "SIGXFSZ"):
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        file_limit = 2 * 1024 * 1024
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (file_limit, file_limit),
        )

        from app.services.breakouts.repository import BreakoutRepository

        class FileBackedTempRepository(BreakoutRepository):
            def _read_connection(self):
                connection = super()._read_connection()
                connection.execute("PRAGMA temp_store=FILE")
                return connection

        repo = FileBackedTempRepository(Path(sys.argv[1]), read_only=True)
        batch = repo.load_carryover_events(
            as_of=datetime.fromisoformat(sys.argv[2]),
            event_ttl_seconds=3_600,
            limit=6,
            expired_due_limit=3,
        )
        print(
            json.dumps(
                {
                    "event_ids": [event["event_id"] for event in batch.events],
                    "versions": [
                        event["fixture_version"] for event in batch.events
                    ],
                    "expired_due_event_ids": sorted(
                        batch.expired_due_event_ids
                    ),
                    "has_more": batch.has_more,
                }
            )
        )
        """
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(database_path),
            NOW.isoformat(),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0, (
        "carryover query exceeded its 2 MiB temporary-file allowance\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    result = json.loads(completed.stdout)
    assert result == {
        "event_ids": [f"event-{number:02d}" for number in range(6)],
        "versions": [4] * 6,
        "expired_due_event_ids": [],
        "has_more": True,
    }
