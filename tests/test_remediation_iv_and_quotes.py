from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import earnings_enrichment as enrich
from app.services import quote_quality, yahoo
from app.services.yahoo import NEW_YORK_TZ, STOCK_IV_METHOD_VERSION, _bs_price, compute_iv


FIXED_NOW = datetime(2026, 9, 7, 16, 0, tzinfo=NEW_YORK_TZ)


@pytest.fixture(autouse=True)
def _isolate_yahoo_cache() -> None:
    yahoo._cache.clear()
    yield
    yahoo._cache.clear()


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        current = FIXED_NOW
        return current if tz is None else current.astimezone(tz)


def _freeze_yahoo_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yahoo, "datetime", FrozenDateTime)


def _calls_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _iv_ticker(
    *,
    expirations: list[str],
    price: float,
    calls_by_exp: dict[str, pd.DataFrame],
) -> SimpleNamespace:
    return SimpleNamespace(
        options=list(expirations),
        fast_info=SimpleNamespace(last_price=price),
        option_chain=lambda expiration: SimpleNamespace(
            calls=calls_by_exp[expiration],
            puts=pd.DataFrame(),
        ),
    )


def _atm_rows(ivs: list[float | None], *, spot: float = 100.0) -> list[dict]:
    rows = []
    for offset, iv in enumerate(ivs):
        strike = spot + offset
        rows.append(
            {
                "strike": strike,
                "impliedVolatility": iv,
                "lastPrice": 2.0,
                "bid": 1.9,
                "ask": 2.1,
            }
        )
    return rows


def test_iv01_nearest_low_vendor_iv_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_yahoo_now(monkeypatch)
    expiration = "2026-10-07"
    ticker = _iv_ticker(
        expirations=[expiration],
        price=100.0,
        calls_by_exp={
            expiration: _calls_frame(_atm_rows([0.08, 0.09, 0.18, 0.22, 0.25])),
        },
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)

    snapshot = yahoo.get_stock_iv_snapshot("LOWIV")
    assert snapshot["atm_iv"] == 0.08
    assert snapshot["selected_strike"] == 100.0
    assert snapshot["iv_method"] == "vendor_call"
    assert snapshot["method_version"] == STOCK_IV_METHOD_VERSION
    assert yahoo.get_stock_iv("LOWIV") == 0.08


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), -0.12, None, 0.0],
)
def test_iv02_invalid_nearest_falls_back_or_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    bad,
) -> None:
    _freeze_yahoo_now(monkeypatch)
    expiration = "2026-10-07"
    ticker = _iv_ticker(
        expirations=[expiration],
        price=100.0,
        calls_by_exp={
            expiration: _calls_frame(_atm_rows([bad, 0.11, 0.18, 0.22, 0.25])),
        },
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)

    snapshot = yahoo.get_stock_iv_snapshot("BADNEAR")
    assert snapshot["atm_iv"] == 0.11
    assert snapshot["selected_strike"] == 101.0
    assert snapshot["strike_fallback_reason"] == "nearest_invalid"


def test_iv03_target_30_day_is_stable_when_order_shuffled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_yahoo_now(monkeypatch)
    frames = {
        "2026-09-28": _calls_frame(_atm_rows([0.21])),
        "2026-10-07": _calls_frame(_atm_rows([0.30])),
        "2026-10-22": _calls_frame(_atm_rows([0.45])),
    }
    chosen = []
    for order in (
        ["2026-09-28", "2026-10-07", "2026-10-22"],
        ["2026-10-22", "2026-09-28", "2026-10-07"],
        ["2026-10-07", "2026-10-22", "2026-09-28"],
    ):
        yahoo._cache.clear()
        ticker = _iv_ticker(expirations=order, price=100.0, calls_by_exp=frames)
        monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol, current=ticker: current)
        snapshot = yahoo.get_stock_iv_snapshot("ORDER")
        chosen.append((snapshot["selected_expiration"], snapshot["atm_iv"]))
    assert chosen == [("2026-10-07", 0.30)] * 3


def test_iv04_ties_prefer_earlier_expiry_and_lower_strike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_yahoo_now(monkeypatch)
    frames = {
        "2026-09-27": _calls_frame(_atm_rows([0.20])),
        "2026-10-17": _calls_frame(_atm_rows([0.40])),
    }
    ticker = _iv_ticker(
        expirations=["2026-10-17", "2026-09-27"],
        price=100.0,
        calls_by_exp=frames,
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    first = yahoo.get_stock_iv_snapshot("TIE")
    yahoo._cache.clear()
    second = yahoo.get_stock_iv_snapshot("TIE")
    assert first["selected_expiration"] == second["selected_expiration"] == "2026-09-27"
    assert first["atm_iv"] == second["atm_iv"] == 0.20


def test_iv05_fallback_window_is_disclosed_and_expired_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_yahoo_now(monkeypatch)
    ticker = _iv_ticker(
        expirations=["2026-09-01", "2026-09-17"],
        price=100.0,
        calls_by_exp={
            "2026-09-01": _calls_frame(_atm_rows([0.99])),
            "2026-09-17": _calls_frame(_atm_rows([0.17])),
        },
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    snapshot = yahoo.get_stock_iv_snapshot("FALLBACK")
    assert snapshot["atm_iv"] == 0.17
    assert snapshot["expiration_selection"] == "fallback_gt_7"
    assert snapshot["selected_expiration"] == "2026-09-17"

    yahoo._cache.clear()
    empty = _iv_ticker(
        expirations=["2026-09-01"],
        price=100.0,
        calls_by_exp={"2026-09-01": _calls_frame(_atm_rows([0.99]))},
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: empty)
    missing = yahoo.get_stock_iv_snapshot("EXPIRED")
    assert missing["atm_iv"] is None
    assert missing["iv_method"] == "missing"
    assert missing["expiration_selection"] == "missing"


def test_iv06_call_only_method_is_disclosed(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_yahoo_now(monkeypatch)
    ticker = _iv_ticker(
        expirations=["2026-10-07"],
        price=100.0,
        calls_by_exp={"2026-10-07": _calls_frame(_atm_rows([0.19]))},
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    snapshot = yahoo.get_stock_iv_snapshot("SIDE")
    assert snapshot["iv_side"] == "call"
    assert snapshot["iv_method"] == "vendor_call"
    assert snapshot["pricing_assumptions"]["dividend_yield_kind"] == "unmodeled"
    assert snapshot["pricing_assumptions"]["risk_free_rate_kind"] == "model_assumption"


def test_iv07_old_cache_key_is_not_treated_as_new_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_yahoo_now(monkeypatch)
    now = datetime.now(timezone.utc)
    yahoo._cache["stock_iv:OLD"] = (
        now.replace(year=now.year + 1),
        now,
        0.99,
    )
    ticker = _iv_ticker(
        expirations=["2026-10-07"],
        price=100.0,
        calls_by_exp={"2026-10-07": _calls_frame(_atm_rows([0.08]))},
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    snapshot = yahoo.get_stock_iv_snapshot("OLD")
    assert snapshot["atm_iv"] == 0.08
    assert snapshot["method_version"] == STOCK_IV_METHOD_VERSION
    assert snapshot["iv_method"] == "vendor_call"


def test_qt01_quality_mid_inverts_and_reprices() -> None:
    price = _bs_price(100, 100, 0.25, 0.05, 0.20, True)
    iv = compute_iv(price, 100, 100, 0.25, 0.05, True)
    assert iv is not None
    assert abs(iv - 0.20) < 0.01
    assert abs(_bs_price(100, 100, 0.25, 0.05, iv, True) - price) < 0.01


def test_qt02_last_price_is_not_silent_inversion(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_yahoo_now(monkeypatch)
    expiration = "2026-10-07"
    rows = [
        {
            "strike": 100.0,
            "impliedVolatility": float("nan"),
            "lastPrice": 4.0,
            "bid": 0.0,
            "ask": 0.0,
        }
    ]
    ticker = _iv_ticker(
        expirations=[expiration],
        price=100.0,
        calls_by_exp={expiration: _calls_frame(rows)},
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    snapshot = yahoo.get_stock_iv_snapshot("LASTONLY")
    assert snapshot["atm_iv"] is None
    assert snapshot["iv_method"] == "missing"

    chain_ticker = SimpleNamespace(
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: SimpleNamespace(
            calls=_calls_frame(rows),
            puts=pd.DataFrame(),
        ),
    )
    yahoo._cache.clear()
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: chain_ticker)
    chain = yahoo.get_option_chain("LASTONLY", expiration)
    assert chain["calls"][0]["last_price"] == 4.0
    assert chain["calls"][0]["iv_source"] in {"missing", "vendor_raw"}
    assert chain["calls"][0]["iv_source"] != "model_inversion"


def test_qt03_bad_quotes_are_rejected() -> None:
    assert quote_quality.quality_mid(3.0, 1.0)[0] is None
    assert quote_quality.quality_mid(float("nan"), 2.0)[0] is None
    assert quote_quality.quality_mid(1.0, float("inf"))[0] is None
    assert quote_quality.quality_mid(1.0, 8.0)[1] == "wide_spread"
    assert quote_quality.option_mark(bid=None, ask=None, last=2.0)["usable_for_inversion"] is False


def test_qt04_zero_bid_and_unproven_mid_are_not_quality_marks() -> None:
    mid, reason = quote_quality.quality_mid(0.0, 2.0)
    assert mid is None
    assert reason == "non_positive_quote"
    assert enrich._contract_mark({"mid": 2.5, "midpoint": 2.5}) is None
    assert enrich._contract_mark({"bid": 1.9, "ask": 2.1}) == pytest.approx(2.0)


def test_qt05_unsolvable_inputs_return_missing() -> None:
    assert compute_iv(2.0, 100, 100, 0.0, 0.05, True) is None
    assert compute_iv(2.0, 100, 100, -1.0, 0.05, True) is None
    # Deep ITM call priced far below intrinsic has no European root.
    assert compute_iv(0.10, 100, 50, 0.25, 0.05, True) is None


def test_qt06_call_and_put_residuals_stay_within_tolerance() -> None:
    for is_call, tenor, sigma in (
        (True, 0.25, 0.18),
        (False, 0.25, 0.18),
        (True, 0.50, 0.40),
        (False, 1.00, 0.22),
    ):
        price = _bs_price(70, 70, tenor, 0.05, sigma, is_call)
        iv = compute_iv(price, 70, 70, tenor, 0.05, is_call)
        assert iv is not None
        residual = abs(_bs_price(70, 70, tenor, 0.05, iv, is_call) - price)
        assert residual < 0.01 or residual / price < 0.01


def test_qt07_assumptions_are_disclosed_on_chain_and_iv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_yahoo_now(monkeypatch)
    expiration = "2026-10-07"
    ticker = SimpleNamespace(
        options=[expiration],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: SimpleNamespace(
            calls=_calls_frame(_atm_rows([0.22])),
            puts=pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    chain = yahoo.get_option_chain("ASSUME", expiration)
    assert chain["pricing_assumptions"]["risk_free_rate"] == 0.05
    assert chain["pricing_assumptions"]["risk_free_rate_kind"] == "model_assumption"
    assert chain["pricing_assumptions"]["dividend_yield_kind"] == "unmodeled"
    assert chain["quote_time_kind"] == "chain_fetch"
    assert chain["per_contract_quote_time"] is False
    snapshot = yahoo.get_stock_iv_snapshot("ASSUME")
    assert snapshot["iv_source"] == "vendor"
    assert snapshot["pricing_assumptions"]["dividend_yield"] is None


def test_tm01_unrelated_fresh_strike_does_not_validate_stale_atm() -> None:
    move = enrich.compute_straddle_move(
        {
            "underlying_price": 100,
            "as_of": "2026-09-03T20:00:00+00:00",
            "calls": [
                {
                    "strike": 110,
                    "bid": 1.0,
                    "ask": 1.2,
                    "quote_as_of": "2026-09-03T20:00:00+00:00",
                },
                {
                    "strike": 100,
                    "bid": 3.8,
                    "ask": 4.2,
                    "quote_as_of": "2026-08-01T20:00:00+00:00",
                },
            ],
            "puts": [
                {
                    "strike": 100,
                    "bid": 3.4,
                    "ask": 3.6,
                    "quote_as_of": "2026-09-03T20:00:00+00:00",
                },
            ],
        },
        today=datetime(2026, 9, 4).date(),
    )
    assert move is None


def test_tm02_missing_none_and_invalid_quote_times_do_not_gain_trust() -> None:
    today = datetime(2026, 9, 4).date()
    base = {
        "underlying_price": 100,
        "as_of": "2026-09-03T20:00:00+00:00",
        "puts": [{"strike": 100, "bid": 3.4, "ask": 3.6, "quote_as_of": "2026-09-03T20:00:00+00:00"}],
    }
    assert (
        enrich.compute_straddle_move(
            {**base, "calls": [{"strike": 100, "bid": 3.8, "ask": 4.2}]},
            today=today,
        )
        is None
    )
    assert (
        enrich.compute_straddle_move(
            {
                **base,
                "calls": [{"strike": 100, "bid": 3.8, "ask": 4.2, "quote_as_of": None}],
            },
            today=today,
        )
        is None
    )
    assert (
        enrich.compute_straddle_move(
            {
                **base,
                "calls": [{"strike": 100, "bid": 3.8, "ask": 4.2, "quote_as_of": "not-a-time"}],
            },
            today=today,
        )
        is None
    )


def test_tm03_yahoo_fetch_time_is_not_verified_quote_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        yahoo,
        "get_expirations_snapshot",
        lambda _ticker: {"expirations": ["2026-09-18"]},
    )
    monkeypatch.setattr(
        yahoo,
        "get_option_chain",
        lambda _ticker, _expiration: {
            "source_status": "active",
            "as_of": "2026-09-04T15:00:00+00:00",
            "underlying_price": 100,
            "calls": [{"strike": 100, "bid": 3.8, "ask": 4.2}],
            "puts": [{"strike": 100, "bid": 3.4, "ask": 3.6}],
        },
    )
    result = enrich._yahoo_expected_move("TEST", datetime(2026, 9, 10).date(), datetime(2026, 9, 4).date(), "amc")
    assert result["expected_move_pct"] == 7.5
    assert result["expected_move_status"] == "degraded:chain_fetch_time_only"
    assert result["expected_move_observed_at"] == "2026-09-04T15:00:00+00:00"


def test_tm07_far_future_timestamp_is_not_fresh() -> None:
    assert enrich._quote_is_fresh("2099-01-01T00:00:00+00:00", datetime(2026, 9, 4).date()) is False
    assert enrich._quote_is_fresh("2026-09-03T20:00:00+00:00", datetime(2026, 9, 4).date()) is True
