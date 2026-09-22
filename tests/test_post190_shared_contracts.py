from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api import earnings, stocks
from app.services import market_calendar, scoring
from app.services.breakouts import clock, feature_engine
from app.services.catalysts.etl_client import EtlProtocolError, MacroLensEtlClient
from app.services.macro_conditions import linkage, market_proxy
from app.services.strength import scanner, yahoo_options


class BrokenNumber:
    def __float__(self):
        raise RuntimeError("provider conversion failed")


def test_numeric_consumers_keep_distinct_missing_and_error_boundaries():
    assert scoring._finite_number(10 ** 400) is None
    with pytest.raises(RuntimeError, match="provider conversion failed"):
        scoring._finite_number(BrokenNumber())
    with pytest.raises(OverflowError):
        linkage._finite(10 ** 400)
    for value in (10 ** 400, BrokenNumber(), None, "bad", float("inf")):
        assert earnings._to_optional_float(value) is None
    assert earnings._to_optional_float("1.23456789") == 1.23456789
    assert earnings._to_optional_float(True) == 1.0
    assert not stocks._is_finite_number(True)
    assert not stocks._is_finite_number("1.23456789")
    assert not stocks._is_finite_number(0, positive=True)


def test_option_percentiles_keep_six_digit_preprocessing():
    rows = [{"ticker": "A", "value": 1.0000001}, {"ticker": "B", "value": 1.0000002}]
    assert scanner._pct_rank(rows, "value") == {"A": 0.0, "B": 100.0}
    assert yahoo_options._pct_rank(rows, "value") == {"A": 50.0, "B": 50.0}
    assert scanner._pct_rank(rows[:1], "value") == {}
    assert yahoo_options._pct_rank(rows[:1], "value") == {}
    provider_rows = [{"ticker": "A", "value": "1"}, {"ticker": "B", "value": None}, {"ticker": "C", "value": "2"}]
    assert yahoo_options._pct_rank(provider_rows, "value") == {"A": 0.0, "C": 100.0}


@pytest.mark.parametrize(("raw", "cause"), [
    (b'{"private-key":1,"private-key":2}', "duplicate JSON key"),
    (b'{"x":NaN}', "non-finite JSON number"),
    (b'{"x":Infinity}', "non-finite JSON number"),
])
def test_etl_json_errors_keep_their_existing_redacted_cause(raw, cause):
    with pytest.raises(EtlProtocolError) as error:
        MacroLensEtlClient._json_object(raw)
    assert str(error.value) == "invalid_response: MacroLens returned malformed JSON"
    assert type(error.value.__cause__) is ValueError
    assert str(error.value.__cause__) == cause
    assert "private-key" not in str(error.value.__cause__)


@pytest.mark.parametrize("instant", [
    datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc),
    datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc),
    datetime(2026, 7, 4, 20, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc),
])
def test_calendar_consumers_agree_at_early_close_holidays_and_regular_close(instant):
    assert market_proxy.last_completed_trading_day(instant) == market_calendar.last_completed_trading_day(instant)
    assert clock._at_minutes(instant.date(), 780) == market_calendar.market_datetime(instant.date(), 780)


def test_feature_calendar_keeps_its_unbounded_walk(monkeypatch):
    observed = date(2026, 9, 22)
    open_day = observed - timedelta(days=20)
    monkeypatch.setattr(market_calendar, "is_trading_day", lambda day: day <= open_day)
    with pytest.raises(RuntimeError, match="previous US trading day"):
        market_calendar.previous_trading_day(observed)
    cutoff = SimpleNamespace(completed_daily_session=None, event_at=datetime(2026, 9, 22, 18, tzinfo=timezone.utc))
    assert feature_engine.completed_daily_session(cutoff) == open_day
