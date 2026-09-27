from __future__ import annotations

import pytest

from app.api import strength
from app.services.eod_limited import store as eod_store
from app.services.eod_limited.project import project_row
from tests.test_eod_limited_product import _scored


@pytest.fixture
def eod_snapshot(monkeypatch):
    scored = _scored()
    template = scored["watch_list"][0]
    scored["watch_list"] = [
        {**template, "security_id": f"ITEM{i}", "score": float(i * 10), "price": float(7 - i)}
        for i in range(1, 7)
    ]
    scored["composite_results"] = [
        {**row, "consensus_z": row["score"]} for row in scored["watch_list"]
    ]
    monkeypatch.setattr(eod_store, "read_batch", lambda: {
        "published_at": 1_800_000_000,
        "variants": {"balanced|mid": scored},
    })
    return scored


def read_snapshot(**overrides):
    list_kind = overrides.pop("list_kind", "observation")
    track = overrides.pop("track", "all")
    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "ranking_algorithm": "eod_limited_v1",
        "timeframe": "mid",
        "min_price": 0,
        "top": 5,
        **overrides,
    }
    payload, _, _ = strength._read_eod_limited_snapshot(
        parameters=parameters, list_kind=list_kind, resolution=None, track=track,
    )
    return payload


@pytest.mark.parametrize("list_kind", ["observation", "composite"])
def test_sector_filter_keeps_highest_scores_before_top(eod_snapshot, list_kind):
    payload = read_snapshot(sector_id="semiconductors", list_kind=list_kind)
    assert [row["sort_score"] for row in payload["rows"]] == [60, 50, 40, 30, 20]
    assert payload["count"] == 5


@pytest.mark.parametrize("sector_id", [None, "semiconductors"])
def test_price_filter_precedes_top_and_preserves_available_rows(eod_snapshot, sector_id):
    payload = read_snapshot(sector_id=sector_id, min_price=5)
    assert [row["ticker"] for row in payload["rows"]] == ["ITEM2", "ITEM1"]
    assert payload["count"] == 2
    assert payload["rows"] == payload["results"]
    assert all(row["price"] >= 5 for row in payload["observation_rows"])
    assert payload["filter_support"] == {"min_price": True, "min_avg_dollar_volume": False, "track": True}


def test_support_is_not_used_as_close_price():
    row = project_row({"security_id": "NVDA", "known_support": 150}, list_kind="observation")
    assert row["price_unknown"] is True
    assert row["price"] == 0


def test_unknown_price_does_not_pass_positive_price_filter(eod_snapshot):
    eod_snapshot["watch_list"][0].pop("price")
    eod_snapshot["watch_list"][0]["known_support"] = 150
    payload = read_snapshot(min_price=5)
    assert [row["ticker"] for row in payload["rows"]] == ["ITEM2"]


def test_concurrent_publication_cannot_pair_old_rows_with_new_clock(monkeypatch):
    reads = []
    old = _scored()
    newer = _scored()
    newer["watch_list"][0]["price"] = 200

    def read_batch():
        reads.append(len(reads))
        return {
            "published_at": 1_800_000_000 + reads[-1],
            "variants": {"balanced|mid": old if len(reads) == 1 else newer},
        }

    monkeypatch.setattr(eod_store, "read_batch", read_batch)
    payload = read_snapshot()
    assert payload["rows"][0]["price"] == 120.5
    assert payload["snapshot_saved_at"] == "2027-01-15T08:00:00+00:00"
    assert len(reads) == 1


@pytest.mark.parametrize(
    ("track", "expected"),
    [("stock", ["ITEM5", "ITEM3", "ITEM1"]), ("etf", ["ITEM6", "ITEM4", "ITEM2"]),
     ("all", ["ITEM6", "ITEM5", "ITEM4"])],
)
def test_track_filter_ranks_stocks_and_funds_separately_before_top(eod_snapshot, track, expected):
    for index, row in enumerate(eod_snapshot["watch_list"], 1):
        row["stock_or_etf_track"] = "etf" if index % 2 == 0 else "stock"
    payload = read_snapshot(track=track, top=3)
    assert [row["ticker"] for row in payload["rows"]] == expected
    assert all(track == "all" or row["stock_or_etf_track"] == track for row in payload["observation_rows"])
    assert payload["track"] == track
    assert payload["track_counts"] == {"stock": 3, "etf": 3}


def test_scan_endpoint_serves_each_track_from_its_own_cache_entry(eod_snapshot):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.services.http_read_cache import reset_serialized_response_cache

    for index, row in enumerate(eod_snapshot["watch_list"], 1):
        row["stock_or_etf_track"] = "etf" if index % 2 == 0 else "stock"
    reset_serialized_response_cache()
    app = FastAPI()
    app.include_router(strength.router)
    client = TestClient(app)
    query = {"timeframe": "mid", "profile": "balanced", "min_price": 0, "top": 5}
    served = {}
    for track in ("stock", "etf", "all", None):
        params = dict(query, **({"track": track} if track else {}))
        response = client.get("/api/strength/scan", params=params)
        assert response.status_code == 200
        body = response.json()
        served[track] = [row["ticker"] for row in body["rows"]]
        assert body["track"] == (track or "stock")
    assert served["stock"] == ["ITEM5", "ITEM3", "ITEM1"]
    assert served["etf"] == ["ITEM6", "ITEM4", "ITEM2"]
    assert served["all"] == ["ITEM6", "ITEM5", "ITEM4", "ITEM3", "ITEM2"]
    assert served[None] == served["stock"]
