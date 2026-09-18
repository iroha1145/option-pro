"""Bind a run to the real B0 tape hash. A literal name is not a content hash."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

EXPECTED_B0_SHA256 = "b490ba6d83965a0c3fe60c76afab8205fc2a0d76cc6577cdf23cb838e447f75a"
FORBIDDEN_LITERAL_HASH = "b0_measurement_factor_rows"


class SourceHashError(ValueError):
    """The tape hash is missing, literal, or does not match the frozen B0 file."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(payload: str) -> str:
    return sha256_bytes(payload.encode("utf-8"))


def source_file_hash(path: Path, *, algo: str = "sha256") -> str:
    data = path.read_bytes()
    if algo == "sha1":
        return hashlib.sha1(data).hexdigest()
    return hashlib.sha256(data).hexdigest()


def assert_content_hash(data_hash: str) -> str:
    value = str(data_hash or "")
    if value == FORBIDDEN_LITERAL_HASH:
        raise SourceHashError("literal b0_measurement_factor_rows is not a content hash")
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise SourceHashError(f"data_hash must be a sha256 hex digest, not {value!r}")
    return value


def bind_b0_tape(path: Path, *, expected: str = EXPECTED_B0_SHA256) -> str:
    if not path.exists():
        raise SourceHashError(f"missing frozen B0 tape: {path}")
    found = sha256_file(path)
    assert_content_hash(found)
    if found != expected:
        raise SourceHashError(
            f"B0 tape hash mismatch: have {found}, expected {expected}. "
            "Stop this run. Do not silently update the expected hash."
        )
    return found


def bind_limited_prefix(path: Path, *, limit: int) -> str:
    """Prefix runs get their own hash and directory. They are not the B0 tape."""

    if limit <= 0:
        raise SourceHashError("limit must be a positive row count")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as handle:
        for raw in handle:
            digest.update(raw)
            count += 1
            if count >= limit:
                break
    if count == 0:
        raise SourceHashError("limited prefix is empty")
    return digest.hexdigest()


def research_code_hashes(root: Path | None = None) -> dict[str, str]:
    base = root or Path(__file__).resolve().parent
    names = (
        "capability.py",
        "ablation.py",
        "runs.py",
        "stats.py",
        "pairing.py",
        "bootstrap.py",
        "event_groups.py",
        "round1b.py",
        "round2.py",
        "freeze.py",
        "source_bind.py",
    )
    out = {}
    for name in names:
        path = base / name
        if path.exists():
            out[name] = source_file_hash(path)
    return out


def member_set_hash(ids: Mapping[str, object] | list[str] | tuple[str, ...]) -> str:
    values = sorted({str(item) for item in ids})
    return sha256_text("\n".join(values))
