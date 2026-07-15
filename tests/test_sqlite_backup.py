from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.tools.sqlite_backup import BackupError, backup_database, main


ROOT = Path(__file__).resolve().parents[1]


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(ROOT / "backend") + (
        os.pathsep + existing if existing else ""
    )
    return environment


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute(
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id), value TEXT)"
        )
        connection.execute("INSERT INTO parent (value) VALUES ('source row')")
        connection.execute("INSERT INTO child VALUES (1, 'related row')")


def test_backup_uses_consistent_copy_and_writes_verified_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    _create_database(source)

    result = backup_database(source, destination, label="optix", keep=7)

    backup_path = Path(result.backup)
    assert backup_path.is_file()
    with sqlite3.connect(f"{backup_path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT value FROM parent").fetchone() == ("source row",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    expected_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    assert result.sha256 == expected_digest
    assert result.quick_check == "ok"
    assert result.integrity_check == "ok"
    assert result.foreign_key_violations == 0
    assert backup_path.stat().st_mode & 0o777 == 0o600

    manifest = json.loads(Path(result.manifest).read_text(encoding="utf-8"))
    assert manifest["sha256"] == expected_digest
    assert manifest["backup"] == backup_path.name
    assert manifest["integrity_check"] == "ok"
    assert Path(result.checksum_file).read_text(encoding="utf-8") == (
        f"{expected_digest}  {backup_path.name}\n"
    )


def test_backup_retention_is_scoped_by_database_label(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    _create_database(source)
    start = datetime(2026, 7, 15, tzinfo=UTC)

    first = backup_database(
        source, destination, label="optix", keep=2, created_at=start
    )
    second = backup_database(
        source,
        destination,
        label="optix",
        keep=2,
        created_at=start + timedelta(minutes=1),
    )
    other = backup_database(
        source,
        destination,
        label="worker",
        keep=2,
        created_at=start + timedelta(minutes=1),
    )
    third = backup_database(
        source,
        destination,
        label="optix",
        keep=2,
        created_at=start + timedelta(minutes=2),
    )

    assert not Path(first.backup).exists()
    assert not Path(first.manifest).exists()
    assert not Path(first.checksum_file).exists()
    assert Path(second.backup).exists()
    assert Path(third.backup).exists()
    assert Path(other.backup).exists()
    assert third.removed_backups == (Path(first.backup).name,)


def test_retention_deletes_incomplete_groups_without_counting_them(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    _create_database(source)
    start = datetime(2026, 7, 15, tzinfo=UTC)
    complete = backup_database(
        source, destination, label="optix", keep=1, created_at=start
    )

    missing_manifest = destination / "optix-99999999T999999.999999Z-a.sqlite3"
    missing_manifest.write_bytes(b"incomplete")
    missing_manifest.with_suffix(".sqlite3.sha256").write_text(
        "invalid  incomplete\n", encoding="utf-8"
    )
    orphan_manifest = destination / "optix-99999999T999999.999999Z-b.sqlite3.json"
    orphan_manifest.write_text("{}\n", encoding="utf-8")
    abandoned_temporary = destination / ".optix-crashed.sqlite3.tmp"
    abandoned_temporary.write_bytes(b"partial backup")

    latest = backup_database(
        source,
        destination,
        label="optix",
        keep=1,
        created_at=start + timedelta(minutes=1),
    )

    assert not missing_manifest.exists()
    assert not missing_manifest.with_suffix(".sqlite3.sha256").exists()
    assert not orphan_manifest.exists()
    assert not abandoned_temporary.exists()
    assert not Path(complete.backup).exists()
    assert Path(latest.backup).exists()
    assert set(destination.glob("optix-*.sqlite3")) == {Path(latest.backup)}


def test_backup_rejects_missing_or_invalid_database(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="does not exist"):
        backup_database(tmp_path / "missing.db", tmp_path / "backups")

    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(b"not a sqlite database")
    with pytest.raises(BackupError, match="Backup API failed|cannot validate"):
        backup_database(invalid, tmp_path / "backups")
    assert list((tmp_path / "backups").glob("*.sqlite3")) == []


def test_cli_backs_up_multiple_databases_and_returns_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    optix = tmp_path / "optix.db"
    worker = tmp_path / "worker.db"
    destination = tmp_path / "backups"
    _create_database(optix)
    _create_database(worker)

    exit_code = main(
        [
            "--database",
            f"optix={optix}",
            "--database",
            f"worker={worker}",
            "--destination",
            str(destination),
            "--keep",
            "1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert {item["label"] for item in payload["backups"]} == {"optix", "worker"}
    assert payload["errors"] == []


def test_backup_fails_cleanly_when_label_lock_is_already_held(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    destination.mkdir()
    _create_database(source)
    lock_path = destination / ".optix.backup.lock"

    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.tools.sqlite_backup",
                "--database",
                f"optix={source}",
                "--destination",
                str(destination),
                "--lock-timeout-seconds",
                "0.05",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=_subprocess_environment(),
            timeout=10,
        )
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    payload = json.loads(result.stderr)
    assert result.returncode == 1
    assert payload["ok"] is False
    assert "timed out waiting for backup lock" in payload["errors"][0]["error"]
    assert list(destination.glob("optix-*.sqlite3")) == []


def test_concurrent_backups_of_same_label_are_serialized(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    first_marker = tmp_path / "first-entered"
    second_marker = tmp_path / "second-entered"
    _create_database(source)

    script = """
import sys
import time
from pathlib import Path

import app.tools.sqlite_backup as backup_module

source, destination, marker = sys.argv[1:]
original = backup_module._backup_database_locked

def delayed_backup(*args, **kwargs):
    Path(marker).write_text("entered", encoding="utf-8")
    time.sleep(0.4)
    return original(*args, **kwargs)

backup_module._backup_database_locked = delayed_backup
raise SystemExit(
    backup_module.main(
        [
            "--database", f"optix={source}",
            "--destination", destination,
            "--keep", "2",
            "--lock-timeout-seconds", "5",
        ]
    )
)
"""
    processes: list[subprocess.Popen[str]] = []
    try:
        first = subprocess.Popen(
            [sys.executable, "-c", script, str(source), str(destination), str(first_marker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_environment(),
        )
        processes.append(first)
        deadline = time.monotonic() + 5
        while not first_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first_marker.exists(), "first backup did not enter the locked section"

        second = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(source),
                str(destination),
                str(second_marker),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_environment(),
        )
        processes.append(second)
        time.sleep(0.15)
        assert not second_marker.exists()

        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        assert first.returncode == 0, first_stderr
        assert second.returncode == 0, second_stderr
        assert json.loads(first_stdout)["ok"] is True
        assert json.loads(second_stdout)["ok"] is True
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    backup_paths = sorted(destination.glob("optix-*.sqlite3"))
    assert len(backup_paths) == 2
    for backup_path in backup_paths:
        assert backup_path.with_suffix(".sqlite3.json").is_file()
        assert backup_path.with_suffix(".sqlite3.sha256").is_file()
