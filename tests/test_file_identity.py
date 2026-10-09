from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app import file_identity
from app.file_identity import replace_file_atomically


def test_replace_file_atomically_writes_private_bytes_beside_the_target(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "snapshot.json"

    replace_file_atomically(target, b'{"a":1}')
    replace_file_atomically(target, b'{"a":2}')

    assert target.read_bytes() == b'{"a":2}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(target.parent.glob(".snapshot.json.*.tmp")) == []


def test_failed_replace_keeps_the_old_file_and_leaves_no_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "snapshot.json"
    replace_file_atomically(target, b"old")

    def refuse(_source: str, _destination: os.PathLike[str] | str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(file_identity.os, "replace", refuse)
    with pytest.raises(OSError, match="disk unavailable"):
        replace_file_atomically(target, b"new")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(".snapshot.json.*.tmp")) == []
