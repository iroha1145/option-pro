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
    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "ranking_algorithm": "eod_limited_v1",
        "timeframe": "mid",
        "min_price": 0,
        "top": 5,
        **overrides,
    }
    payload, _, _ = strength._read_eod_limited_snapshot(
        parameters=parameters, list_kind=list_kind, resolution=None,
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
    assert payload["filter_support"] == {"min_price": True, "min_avg_dollar_volume": False}


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
