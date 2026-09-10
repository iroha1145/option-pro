"""Aggregate pagination retains recent bars without trusting upstream URLs."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import massive

PREFIX = "/v2/aggs/ticker/NVDA/range/1/hour/"
END = "2026-09-10"
OLD = 1_787_043_600_000
NEXT = OLD + 3_600_000
LATEST = 1_788_998_100_000


@pytest.fixture(autouse=True)
def provider_origin(monkeypatch):
    monkeypatch.setattr(massive, "get_settings", lambda: SimpleNamespace(massive_base_url="https://api.massive.com"))


def bar(stamp, close=10):
    return {"t": stamp, "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 100}


def fetch():
    return massive.ticker_range("NVDA", 1, "hour", "2026-06-02", END, adjusted=False)


def test_reads_recent_page_and_deduplicates_boundary_without_forwarding_query(monkeypatch):
    calls = []

    def get(path, params):
        calls.append((path, dict(params)))
        if len(calls) == 1:
            return {"queryCount": 50_000, "resultsCount": 850, "results": [bar(OLD)],
                    "next_url": f"https://api.massive.com{PREFIX}{NEXT}/{END}?apiKey=do-not-forward&adjusted=true&sort=desc"}
        return {"results": [bar(LATEST, 12), bar(OLD, 11)]}

    monkeypatch.setattr(massive, "_get", get)
    result = fetch()
    assert [(row["t"], row["c"]) for row in result] == [(OLD, 11), (LATEST, 12)]
    assert [path for path, _ in calls] == [f"{PREFIX}2026-06-02/{END}", f"{PREFIX}{NEXT}/{END}"]
    assert all(params == {"adjusted": "false", "sort": "asc", "limit": 50_000} for _, params in calls)


@pytest.mark.parametrize("next_url", [
    f"https://elsewhere.example{PREFIX}{NEXT}/{END}",
    f"http://api.massive.com{PREFIX}{NEXT}/{END}",
    f"https://user:password@api.massive.com{PREFIX}{NEXT}/{END}",
    f"https://api.massive.com:444{PREFIX}{NEXT}/{END}",
    f"https://api.massive.com/v2/aggs/ticker/AAPL/range/1/hour/{NEXT}/{END}",
    f"https://api.massive.com/v2/aggs/ticker/NVDA/range/5/minute/{NEXT}/{END}",
    f"https://api.massive.com{PREFIX}{NEXT}/2099-01-01",
    f"https://api.massive.com{PREFIX}{OLD}/{END}",
    f"https://api.massive.com{PREFIX}{NEXT}/{END}#fragment",
    {"url": "invalid"},
])
def test_rejects_unsafe_or_nonadvancing_next_page(monkeypatch, next_url):
    calls = []

    def get(path, params):
        calls.append(path)
        return {"results": [bar(OLD)], "next_url": next_url}

    monkeypatch.setattr(massive, "_get", get)
    with pytest.raises(massive.MassiveError) as caught:
        fetch()
    assert caught.value.code == "protocol"
    assert len(calls) == 1


def test_later_page_failure_does_not_return_the_old_prefix(monkeypatch):
    calls = []

    def get(path, params):
        calls.append(path)
        if len(calls) == 1:
            return {"results": [bar(OLD)], "next_url": f"{PREFIX}{NEXT}/{END}"}
        raise massive.MassiveError("rate limited", code="rate_limited", status=429)

    monkeypatch.setattr(massive, "_get", get)
    with pytest.raises(massive.MassiveError) as caught:
        fetch()
    assert caught.value.code == "rate_limited"
    assert len(calls) == 2


def test_repeated_empty_page_cannot_loop(monkeypatch):
    calls = []

    def get(path, params):
        calls.append(path)
        return {"results": [bar(OLD)] if len(calls) == 1 else [], "next_url": f"{PREFIX}{NEXT}/{END}"}

    monkeypatch.setattr(massive, "_get", get)
    with pytest.raises(massive.MassiveError, match="did not advance"):
        fetch()
    assert len(calls) == 2


def test_page_cap_fails_without_publishing_partial_history(monkeypatch):
    monkeypatch.setattr(massive, "_AGGREGATE_MAX_PAGES", 2)
    calls = []

    def get(path, params):
        stamp = OLD + len(calls) * 3_600_000
        calls.append(path)
        return {"results": [bar(stamp)], "next_url": f"{PREFIX}{stamp + 3_600_000}/{END}"}

    monkeypatch.setattr(massive, "_get", get)
    with pytest.raises(massive.MassiveError, match="pagination limit"):
        fetch()
    assert len(calls) == 2


def test_result_cap_is_bounded(monkeypatch):
    monkeypatch.setattr(massive, "_AGGREGATE_MAX_BARS", 1)
    monkeypatch.setattr(massive, "_get", lambda path, params: {"results": [bar(OLD), bar(NEXT)]})
    with pytest.raises(massive.MassiveError, match="result limit"):
        fetch()


def test_real_chart_history_adapter_keeps_the_latest_page(monkeypatch):
    from app.api import stocks

    calls = []

    def get(path, params):
        calls.append(path)
        end = path.rsplit("/", 1)[1]
        if len(calls) == 1:
            return {"results": [bar(OLD)], "next_url": f"{PREFIX}{NEXT}/{end}"}
        return {"results": [bar(LATEST)]}

    monkeypatch.setattr(massive, "_get", get)
    frame = stocks._massive_chart_history(massive, "NVDA", "1h", False)
    assert frame is not None and len(frame) == 2
    assert round(frame.index[-1].timestamp() * 1_000) == LATEST
    assert len(calls) == 2
