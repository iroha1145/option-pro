from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from app import stock_pull_snapshot as snapshot


def _overview(ticker: str, price: float) -> dict:
    return {
        "ticker": ticker,
        "price": price,
        "price_provider": "Massive",
    }


def _document(ticker: str, price: float, saved_at: float) -> dict:
    return {
        "version": snapshot.STOCK_PULL_SNAPSHOT_VERSION,
        "entries": {
            ticker: {
                "overview": {
                    "saved_at": saved_at,
                    "payload": _overview(ticker, price),
                }
            }
        },
    }


@pytest.fixture(autouse=True)
def _clear_document_cache():
    snapshot._snapshot_document_cache.clear()
    yield
    snapshot._snapshot_document_cache.clear()


def test_unchanged_document_parses_once_and_atomic_write_refreshes_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "stock-pull.json"
    now = 1_700_000_000.0
    path.write_text(
        json.dumps(_document("AAOI", 112.02, now)),
        encoding="utf-8",
    )
    calls = 0
    original_loads = snapshot.json.loads

    def counted_loads(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(snapshot.json, "loads", counted_loads)

    first = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 1,
    )
    second = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 2,
    )

    assert first is not None and first["payload"]["price"] == 112.02
    assert second is not None and second["payload"]["price"] == 112.02
    assert calls == 1

    persisted = snapshot.write_stock_pull_resources(
        "AAOI",
        {"overview": (_overview("AAOI", 118.75), now + 3)},
        path=path,
        now=now + 4,
    )
    refreshed = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 5,
    )

    assert persisted == {"overview"}
    assert refreshed is not None and refreshed["payload"]["price"] == 118.75
    # The writer publishes its already-validated replacement into the cache.
    assert calls == 1


def test_external_atomic_replacement_invalidates_cached_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "stock-pull.json"
    replacement = tmp_path / "replacement.json"
    now = 1_700_000_000.0
    path.write_text(
        json.dumps(_document("AAOI", 112.02, now)),
        encoding="utf-8",
    )
    calls = 0
    original_loads = snapshot.json.loads

    def counted_loads(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(snapshot.json, "loads", counted_loads)
    assert snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 1,
    ) is not None

    replacement.write_text(
        json.dumps(_document("AAOI", 119.25, now + 2)),
        encoding="utf-8",
    )
    os.replace(replacement, path)
    refreshed = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 3,
    )

    assert refreshed is not None and refreshed["payload"]["price"] == 119.25
    assert calls == 2


def test_replacement_between_open_and_cache_lookup_never_returns_old_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "stock-pull.json"
    replacement = tmp_path / "replacement.json"
    now = 1_700_000_000.0
    path.write_text(
        json.dumps(_document("AAOI", 112.02, now)),
        encoding="utf-8",
    )
    assert snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 1,
    ) is not None
    replacement.write_text(
        json.dumps(_document("AAOI", 119.25, now + 2)),
        encoding="utf-8",
    )

    original_open = snapshot.os.open
    replaced = False

    def replace_after_open(target, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = original_open(target, flags, *args, **kwargs)
        if not replaced and os.path.abspath(target) == os.path.abspath(path):
            replaced = True
            os.replace(replacement, path)
        return descriptor

    monkeypatch.setattr(snapshot.os, "open", replace_after_open)
    raced = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 3,
    )
    refreshed = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 4,
    )

    assert raced is None
    assert refreshed is not None and refreshed["payload"]["price"] == 119.25


def test_cached_document_still_applies_expiry_and_returns_defensive_payloads(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stock-pull.json"
    now = 1_700_000_000.0
    path.write_text(
        json.dumps(_document("AAOI", 112.02, now)),
        encoding="utf-8",
    )

    first = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 1,
    )
    assert first is not None
    first["payload"]["price"] = 1.0
    second = snapshot.read_stock_pull_resource(
        "AAOI",
        "overview",
        path=path,
        now=now + 2,
    )

    assert second is not None and second["payload"]["price"] == 112.02
    assert (
        snapshot.read_stock_pull_resource(
            "AAOI",
            "overview",
            path=path,
            now=now
            + snapshot.STOCK_PULL_RESOURCE_MAX_AGE_SECONDS["overview"]
            + 1,
        )
        is None
    )


def test_document_cache_is_bounded_by_path_count(tmp_path: Path) -> None:
    now = 1_700_000_000.0
    paths = [
        tmp_path / f"stock-pull-{index}.json"
        for index in range(snapshot._SNAPSHOT_DOCUMENT_CACHE_MAX_PATHS + 2)
    ]
    for path in paths:
        path.write_text(
            json.dumps(_document("AAOI", 112.02, now)),
            encoding="utf-8",
        )
        assert snapshot.read_stock_pull_resource(
            "AAOI",
            "overview",
            path=path,
            now=now + 1,
        ) is not None

    assert (
        len(snapshot._snapshot_document_cache)
        == snapshot._SNAPSHOT_DOCUMENT_CACHE_MAX_PATHS
    )
    assert (
        snapshot._snapshot_cache_key(paths[0])
        not in snapshot._snapshot_document_cache
    )


def test_snapshot_reads_reject_target_and_parent_symlink_boundaries(
    tmp_path: Path,
) -> None:
    now = 1_700_000_000.0
    victim = tmp_path / "victim.json"
    victim.write_text(
        json.dumps(_document("AAOI", 112.02, now)),
        encoding="utf-8",
    )
    linked_target = tmp_path / "linked.json"
    linked_target.symlink_to(victim)
    assert (
        snapshot.read_stock_pull_resource(
            "AAOI",
            "overview",
            path=linked_target,
            now=now + 1,
        )
        is None
    )

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    nested = real_parent / "stock-pull.json"
    nested.write_text(
        json.dumps(_document("AAOI", 112.02, now)),
        encoding="utf-8",
    )
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    assert (
        snapshot.read_stock_pull_resource(
            "AAOI",
            "overview",
            path=linked_parent / nested.name,
            now=now + 1,
        )
        is None
    )


def test_snapshot_write_rejects_target_and_parent_symlink_boundaries(
    tmp_path: Path,
) -> None:
    now = 1_700_000_000.0
    victim = tmp_path / "victim.json"
    victim.write_text("untouched", encoding="utf-8")
    linked_target = tmp_path / "linked.json"
    linked_target.symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        snapshot.write_stock_pull_resources(
            "AAOI",
            {"overview": (_overview("AAOI", 112.02), now)},
            path=linked_target,
            now=now + 1,
        )
    assert victim.read_text(encoding="utf-8") == "untouched"

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        snapshot.write_stock_pull_resources(
            "AAOI",
            {"overview": (_overview("AAOI", 112.02), now)},
            path=linked_parent / "stock-pull.json",
            now=now + 1,
        )
    assert not (real_parent / "stock-pull.json").exists()


def test_atomic_write_fsyncs_file_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "stock-pull.json"
    now = 1_700_000_000.0
    original_fsync = snapshot.os.fsync
    fsynced_modes: list[int] = []

    def observed_fsync(descriptor: int) -> None:
        fsynced_modes.append(os.fstat(descriptor).st_mode)
        original_fsync(descriptor)

    monkeypatch.setattr(snapshot.os, "fsync", observed_fsync)
    snapshot.write_stock_pull_resources(
        "AAOI",
        {"overview": (_overview("AAOI", 112.02), now)},
        path=path,
        now=now + 1,
    )

    assert any(stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)
