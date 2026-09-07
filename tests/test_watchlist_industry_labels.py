from copy import deepcopy
import time

import pytest

from app.api import stocks


@pytest.mark.parametrize('profile_age', [10, 3600])
def test_cached_company_industry_is_independent_of_quote_recency(tmp_path, monkeypatch, profile_age):
    now = time.time()
    snapshot = {'groups': [
        {'name': '自定义', 'stocks': [{'ticker': 'BE', 'price': 266.14, 'quote_as_of': 'latest-quote'}, {'ticker': 'NBIS', 'price': 225.3}]},
        {'name': '半导体', 'stocks': [{'ticker': 'NVDA', 'price': 229.5}]},
        {'name': '宽基 ETF', 'stocks': [{'ticker': 'GLD', 'price': 100}]},
    ]}
    original = deepcopy(snapshot)
    monkeypatch.setattr(stocks, '_WATCHLIST_SNAPSHOT_PATH', tmp_path / 'absent.json')
    monkeypatch.setattr(stocks, '_endpoint_cache', {
        'watchlist': stocks._EndpointCacheEntry(now + 60, now + 300, now - 30, snapshot),
    })
    profiles = {
        'BE': {'sic_description': 'Electrical Equipment & Parts', 'price': None},
        'NBIS': {'sic_description': 'Internet Content & Information', 'price': None},
        'NVDA': {'sic_description': 'Other classification', 'price': None},
        'GLD': {},
    }
    monkeypatch.setattr(stocks, 'read_stock_pull_resource', lambda symbol, resource, **kwargs: {
        'saved_at': now - profile_age, 'payload': profiles[symbol],
    })
    result = stocks._cached_selected_watchlist(['BE', 'NBIS', 'NVDA', 'GLD'])
    rows = {row['ticker']: row for group in result['groups'] for row in group['stocks']}
    assert rows['BE']['sector'] == 'Electrical Equipment & Parts'
    assert rows['NBIS']['sector'] == 'Internet Content & Information'
    assert rows['NVDA']['sector'] == '半导体'
    assert rows['GLD']['sector'] == '宽基 ETF'
    assert rows['BE']['price'] == 266.14 and rows['BE']['quote_as_of'] == 'latest-quote'
    assert rows['NBIS']['price'] == 225.3
    assert snapshot == original


def test_custom_group_without_industry_does_not_become_a_sector(tmp_path, monkeypatch):
    now = time.time()
    snapshot = {'groups': [{'name': '自定义', 'stocks': [{'ticker': 'NEW', 'price': 10, 'sector': '自定义'}]}]}
    monkeypatch.setattr(stocks, '_WATCHLIST_SNAPSHOT_PATH', tmp_path / 'absent.json')
    monkeypatch.setattr(stocks, '_endpoint_cache', {
        'watchlist': stocks._EndpointCacheEntry(now + 60, now + 300, now, snapshot),
    })
    monkeypatch.setattr(stocks, 'read_stock_pull_resource', lambda *args, **kwargs: None)
    row = stocks._cached_selected_watchlist(['NEW'])['groups'][0]['stocks'][0]
    assert row['sector'] == ''
    assert row['price'] == 10
