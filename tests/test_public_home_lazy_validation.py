"""public_home reads validate only the requested resource, once per file version."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from app import public_home_snapshot as phs
from app.public_home_snapshot import (
    public_home_resource_parameters,
    read_public_home_entries,
    read_public_home_resource,
    write_public_home_snapshot,
)
from tests.test_public_home_snapshot import _entries


def _published(tmp_path, now: float):
    path = tmp_path / "public-home.json"
    write_public_home_snapshot(path, _entries(now), now=now)
    return path


def _read(resource: str, path, now: float, parameters=None):
    return read_public_home_resource(
        resource,
        parameters=(
            parameters
            if parameters is not None
            else public_home_resource_parameters(resource, now=now)
        ),
        path=path,
        now=now,
    )


def _counting(monkeypatch, name: str) -> list[str]:
    seen: list[str] = []
    real = getattr(phs, name)

    def counting(resource, *args, **kwargs):
        seen.append(resource)
        return real(resource, *args, **kwargs)

    monkeypatch.setattr(phs, name, counting)
    return seen


def test_a_read_validates_only_its_resource_once_per_file_version(tmp_path, monkeypatch):
    now = time.time()
    path = _published(tmp_path, now)
    validated = _counting(monkeypatch, "_validate_entry")

    assert _read("indices", path, now) is not None
    assert _read("indices", path, now) is not None
    assert validated == ["indices"]

    assert _read("market_signals", path, now) is not None
    assert validated == ["indices", "market_signals"]

    write_public_home_snapshot(path, _entries(now), now=now)
    validated.clear()
    assert _read("indices", path, now) is not None
    assert validated == ["indices"]


def test_iterating_entries_matches_validating_every_resource(tmp_path):
    now = time.time()
    path = _published(tmp_path, now)
    stored = json.loads(path.read_bytes())["resources"]
    expected = {
        resource: entry
        for resource, value in stored.items()
        if (entry := phs._validate_entry(resource, value)) is not None
        and phs._entry_timestamps_fit(resource, entry, now=now)
    }

    assert set(expected) == set(stored)
    assert read_public_home_entries(path, now=now) == expected


def test_max_age_must_still_match_exactly(tmp_path):
    now = time.time()
    path = _published(tmp_path, now)
    document = json.loads(path.read_bytes())
    document["resources"]["indices"]["max_age"] -= 1
    path.write_text(json.dumps(document), encoding="utf-8")

    assert _read("indices", path, now) is None
    assert "indices" not in read_public_home_entries(path, now=now)
    assert _read("market_signals", path, now) is not None


def test_a_parameter_mismatch_validates_nothing(tmp_path, monkeypatch):
    now = time.time()
    path = _published(tmp_path, now)
    validated = _counting(monkeypatch, "_validate_entry")
    payloads = _counting(monkeypatch, "validate_public_home_payload")

    other_ticker = {"ticker": "AMD", "range": "1d", "adjustment": "raw"}
    assert _read("focus_chart", path, now, other_ticker) is None
    assert validated == []
    assert payloads == []


def test_payload_clocks_are_parsed_once_but_checked_on_every_read(tmp_path, monkeypatch):
    now = time.time()
    path = _published(tmp_path, now)
    document = json.loads(path.read_bytes())
    future = now + 1_000
    document["resources"]["earnings"]["payload"]["earnings"][0]["observed_at"] = (
        datetime.fromtimestamp(future, timezone.utc).isoformat()
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    parsed = _counting(monkeypatch, "_iso_timestamp_seconds")
    parameters = public_home_resource_parameters("earnings", now=now)

    assert _read("earnings", path, now, parameters) is None
    clocks_read = len(parsed)
    assert clocks_read > 0
    assert _read("earnings", path, future, parameters) is not None
    assert _read("earnings", path, now, parameters) is None
    assert len(parsed) == clocks_read


def test_a_failing_validator_only_drops_its_own_resource(tmp_path, monkeypatch):
    now = time.time()
    path = _published(tmp_path, now)

    def broken(_payload):
        raise TypeError("validator bug")

    monkeypatch.setitem(phs._PAYLOAD_VALIDATORS, "market_signals", broken)

    assert _read("market_signals", path, now) is None
    assert _read("indices", path, now) is not None
    assert "market_signals" not in read_public_home_entries(path, now=now)


def test_errors_outside_the_unusable_set_still_propagate(tmp_path, monkeypatch):
    now = time.time()
    path = _published(tmp_path, now)

    def broken(_payload):
        raise KeyError("validator bug")

    monkeypatch.setitem(phs._PAYLOAD_VALIDATORS, "market_signals", broken)

    with pytest.raises(KeyError):
        _read("market_signals", path, now)
