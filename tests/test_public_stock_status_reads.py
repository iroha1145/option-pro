"""Status metadata is parsed once per file version, without loosening its checks."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from app import public_stock_data
from app.api import stocks
from app.public_stock_data import public_stock_snapshot_path
from tests.test_http_audit_2026_09_25 import _daily_chart, _overview, _signals, _write_pull_document

SYMBOLS = [f"T{index:02d}" for index in range(20)]


def _status_path(symbol: str):
    return public_stock_snapshot_path(symbol).parent / "status" / f"{symbol}.json"


def _write_status(symbol: str, status: str, as_of: float) -> None:
    path = _status_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "ticker": symbol, "status": status, "as_of": as_of, "retry_after": None,
        "priority": 2, "failure_count": 0,
    }), encoding="utf-8")


def _count_metadata_parses(monkeypatch) -> list[int]:
    parses: list[int] = []

    def counting_loads(*args, **kwargs):
        parses.append(1)
        return json.loads(*args, **kwargs)

    monkeypatch.setattr(public_stock_data, "json", SimpleNamespace(loads=counting_loads, dumps=json.dumps))
    return parses


def _poll(symbols: list[str]) -> list[dict]:
    return asyncio.run(stocks.stock_data_status(tickers=",".join(symbols)))["items"]


@pytest.fixture
def published():
    now = time.time()
    for symbol in SYMBOLS:
        _write_pull_document(public_stock_snapshot_path(symbol), {symbol: {
            "overview": (_overview(symbol), now - 60),
            "daily_chart": (_daily_chart(symbol), now - 60),
            "signals": (_signals(), now - 60),
        }})
        _write_status(symbol, "ready", now - 60)
    return now


def test_a_status_poll_parses_each_status_file_once(published, monkeypatch):
    parses = _count_metadata_parses(monkeypatch)

    first = _poll(SYMBOLS)
    assert len(parses) == len(SYMBOLS)
    second = _poll(SYMBOLS)

    assert len(parses) == len(SYMBOLS)
    assert second == first
    assert [item["status"] for item in first] == ["ready"] * len(SYMBOLS)


def test_a_rewritten_status_file_is_read_again(published, monkeypatch):
    _poll(SYMBOLS)
    _write_status(SYMBOLS[0], "running", time.time())
    parses = _count_metadata_parses(monkeypatch)

    items = _poll(SYMBOLS)

    assert len(parses) == 1
    assert items[0]["refresh_status"] == "running"


def test_unusable_metadata_is_never_remembered(tmp_path, monkeypatch):
    path = tmp_path / "status" / "AAOI.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    parses = _count_metadata_parses(monkeypatch)

    assert public_stock_data._read_metadata(path) is None
    assert public_stock_data._read_metadata(path) is None
    assert len(parses) == 2


def test_symlinks_and_oversized_files_are_still_rejected(tmp_path):
    directory = tmp_path / "status"
    directory.mkdir()
    real = directory / "REAL.json"
    real.write_text(json.dumps({"ticker": "REAL"}), encoding="utf-8")
    assert public_stock_data._read_metadata(real) == {"ticker": "REAL"}

    linked = directory / "LINK.json"
    os.symlink(real, linked)
    assert public_stock_data._read_metadata(linked) is None

    real.unlink()
    os.symlink(directory / "LINK.json", real)
    assert public_stock_data._read_metadata(real) is None

    large = directory / "LARGE.json"
    large.write_text(json.dumps({"pad": "x" * public_stock_data._METADATA_MAX_BYTES}), encoding="utf-8")
    assert public_stock_data._read_metadata(large) is None


def test_callers_get_their_own_copy(tmp_path):
    path = tmp_path / "status" / "AAOI.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"ticker": "AAOI", "status": "ready"}), encoding="utf-8")

    first = public_stock_data._read_metadata(path)
    first["status"] = "changed"

    assert public_stock_data._read_metadata(path) == {"ticker": "AAOI", "status": "ready"}
