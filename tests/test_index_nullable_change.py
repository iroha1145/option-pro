"""The producer, published snapshot and anonymous response share nullable change."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app.api import market
from app import public_home_snapshot
from tests.http_response_support import anonymous_get_request, response_payload
from app.public_home_snapshot import (
    create_public_home_entry,
    public_home_resource_parameters,
    read_public_home_resource,
    write_public_home_snapshot,
)


def _build(monkeypatch, previous_close=None):
    monkeypatch.setattr(market.yf, "Ticker", lambda symbol: SimpleNamespace(
        fast_info=SimpleNamespace(last_price=6123.45, previous_close=previous_close)
    ))
    return asyncio.run(market._build_indices())


@pytest.mark.parametrize("previous_close", [None, 0, -1, float("nan"), float("inf")])
def test_index_price_without_previous_close_survives_publication(monkeypatch, tmp_path, previous_close):
    payload = _build(monkeypatch, previous_close)
    now = time.time()
    parameters = public_home_resource_parameters("indices", now=now)
    assert all(row["price"] == 6123.45 and row["change_percent"] is None for row in payload["indices"])
    entry = create_public_home_entry("indices", payload, saved_at=now, parameters=parameters)
    path = tmp_path / "public-home-snapshot-v1.json"
    write_public_home_snapshot(path, {"indices": entry}, now=now)
    published = read_public_home_resource("indices", parameters=parameters, path=path, now=now)
    assert published is not None
    assert published["indices"] == payload["indices"]
    assert published["succeeded"] == len(payload["indices"])
    monkeypatch.setattr(public_home_snapshot, "get_data_paths", lambda: SimpleNamespace(public_home_snapshot=path))
    monkeypatch.setattr(market, "current_request_is_owner", lambda: False)
    response = asyncio.run(market.market_indices(anonymous_get_request("/api/market/indices")))
    assert response.status_code == 200
    assert response_payload(response)["indices"] == payload["indices"]


@pytest.mark.parametrize("price,change", [(0, None), (-1, None), (float("nan"), None), (float("inf"), None), (True, None), (None, 1), (100, float("nan")), (100, float("inf")), (100, True), (100, "0")])
def test_index_publication_rejects_invalid_price_or_present_change(monkeypatch, price, change):
    payload = _build(monkeypatch, 6100)
    payload["indices"][0].update(price=price, change_percent=change)
    now = time.time()
    with pytest.raises(ValueError):
        create_public_home_entry("indices", payload, saved_at=now,
                                 parameters=public_home_resource_parameters("indices", now=now))
