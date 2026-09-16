"""Round-3 economic invariant regressions for PR174.

Run from the repository root:
    PYTHONPATH=backend pytest -q tests/test_pr174_review_regressions.py
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services.research_eod_v1 import factors
from app.services.research_eod_v1.algorithms import setup_b
from app.services.research_eod_v1.calendar_asof import last_complete_eod_session
from app.services.research_eod_v1.composite import m2_utility, m3_diversified
from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.data.to_series import bars_to_series
from app.services.research_eod_v1.fixtures import make_series, trading_days
from app.services.research_eod_v1.ledger import _mark_price, simulate_ledger
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.research_eod_v1.residual import residual_raw_momentum


def _signal(sid: str, session: date, notional: float = 2000.0):
    return dict(security_id=sid, session_date=session.isoformat(), status="eligible",
                score=90, adv20=80_000_000, notional=notional)


def _flat(sid: str, days: list[date], price: float = 100.0):
    x = np.full(len(days), price)
    s = make_series(sid, days, x)
    s.open = x.copy()
    s.raw_open = x.copy()
    s.raw_close = x.copy()
    return s


def test_committed_eod_capture_is_not_before_its_market_close():
    path = Path('research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/manifest.json')
    if not path.exists():
        pytest.skip('Market tape deliberately removed from repository; validate its private manifest instead.')
    manifest = json.loads(path.read_text())
    session = date.fromisoformat(manifest['session_date'])
    if session != date(2026, 9, 16):
        pytest.skip('This regression targets the originally reviewed capture date.')
    close = datetime(2026, 9, 16, 16, tzinfo=ZoneInfo('America/New_York'))
    retrieved = datetime.fromisoformat(manifest['retrieved_at'])
    if manifest.get('eod_status') == 'INVALID_EOD_CAPTURE':
        assert retrieved < close
        return
    assert retrieved >= close


def test_breakout_gate_checks_current_streak_not_historical_maximum():
    raw = SimpleNamespace(
        frozen_setup={'setup_id': 'x'}, b_status='observed', sma50=99,
        sma50_prev20=98, above_sma50=True, rvol=2.0, clv=0.9, extension_atr=1.0,
        platform_distance_atr=1.0, ma_distance_atr=1.0,
        breakout_track={'through': True, 'still_through': True, 'tracking_expired': False,
                        'max_consecutive': 2, 'max_consecutive_closes': 2,
                        'consecutive_closes': 1, 'current_consecutive_closes': 1,
                        'first_day_rvol': 2.0, 'first_day_clv': 0.9},
    )
    decision = setup_b(raw, {'confirm_closes': 2}, {'breakout_rvol_min': 1.2})
    assert not decision.passed


def test_failed_early_base_is_not_permanently_active(monkeypatch):
    days = trading_days(date(2015, 1, 2), 500)
    closes = np.full(500, 100.0)
    closes[40:300] = 70.0
    closes[300:] = 195.0
    s = _flat('PLAT', days)
    s.close = closes
    s.low = closes - 1
    s.high = closes + 1
    s.open = closes.copy()
    def geometry(_series, t, **kwargs):
        if t == 25:
            return 80.0, 'observed', {'support': 90., 'resistance_high': 100.}
        if t >= 450:
            return 80.0, 'observed', {'support': 190., 'resistance_high': 200.}
        return 0.0, 'no_base_observed', None
    monkeypatch.setattr(factors, '_base_geometry', geometry)
    _, status, setup = factors.resolve_frozen_setup(
        s, 499, min_sessions=20, max_sessions=80, min_touches=2)
    assert not (status == 'observed' and setup and setup['resistance_high'] == 100.)


def test_residual_is_invariant_to_unused_benchmark_prefix():
    rng = np.random.default_rng(174)
    days = trading_days(date(2018, 1, 2), 700)
    rm = rng.normal(.0003, .01, 700)
    pm = 100 * np.cumprod(1 + rm)
    ri = 1.3 * rm[-400:] + rng.normal(.0004, .003, 400)
    pi = 30 * np.cumprod(1 + ri)
    stock = make_series('NEWER', days[-400:], pi, industry_id=None)
    full = make_series('SPY', days, pm, asset_track='etf', security_type='ETF')
    trimmed = make_series('SPY', days[-400:], pm[-400:], asset_track='etf', security_type='ETF')
    a = residual_raw_momentum(stock, full, {'NEWER': stock, 'SPY': full})
    b = residual_raw_momentum(stock, trimmed, {'NEWER': stock, 'SPY': trimmed})
    assert a.status == b.status == 'OK'
    assert a.raw == pytest.approx(b.raw, abs=1e-10)


def test_raw_shares_are_marked_at_raw_close():
    days = trading_days(date(2021, 1, 4), 3)
    s = _flat('SPL', days)
    s.close[:] = 50.0
    s.raw_close[:] = 100.0
    mark, _ = _mark_price(s, days[0])
    assert mark == 100.0


def test_split_trade_return_reconciles_to_share_cash_flows():
    days = trading_days(date(2021, 1, 4), 12)
    s = _flat('SPL', days, 50.0)
    s.raw_open[:6] = 100.0
    s.raw_close[:6] = 100.0
    s.splits = ((days[6], 2.0),)
    result = simulate_ledger(start=days[0], end=days[-1], panel={'SPL': s},
                             capital=10_000., holding_sessions=5, cost_multiple=0,
                             signals=[_signal('SPL', days[1], 5000.)])
    assert len(result['trades']) == 1
    assert result['ending_equity'] == pytest.approx(10_000.)
    assert result['trades'][0]['net_return'] == pytest.approx(0.0)


def test_one_unknown_mark_does_not_erase_other_known_positions():
    days = trading_days(date(2021, 1, 4), 12)
    a, b = _flat('AAA', days), _flat('BBB', days)
    a.close[4] = np.nan
    a.raw_close[4] = np.nan
    result = simulate_ledger(start=days[0], end=days[-1], panel={'AAA': a, 'BBB': b},
                             capital=10_000., holding_sessions=20, cost_multiple=0,
                             signals=[_signal('AAA', days[1]), _signal('BBB', days[1])])
    row = next(r for r in result['daily_equity'] if r['session'] == days[4].isoformat())
    assert row['equity'] is None or row['equity'] >= row['cash'] + 2000


def test_missing_high_low_are_not_fabricated_into_complete_bars():
    day = date(2021, 1, 4)
    bar = ResearchBar(security_id='BAD', session_date=day, open=100., high=None,
                      low=None, close=101., raw_open=100., raw_close=101.,
                      volume=1000., dollar_volume=101000., tri=101.)
    try:
        s = bars_to_series([bar], security_id='BAD', asset_track='stock')
    except ValueError:
        return
    assert s is None or not has_complete_session_bar(s, day)


def test_m2_cannot_accept_future_labels_with_no_evaluation_date():
    row = dict(security_id='AAA', algorithm_id='A_trend_quality', score=95.,
               status='eligible', primary_industry_id='tech', factors={'R': 90., 'G': 90.})
    try:
        selected = m2_utility([row], 'balanced', 5, matured_returns={
            'AAA': {'returns': [.02]*100, 'label_matured_at': date(2099, 1, 1)}})
    except (ValueError, TypeError):
        return
    assert not selected


def test_m3_reorders_by_marginal_score_after_first_selection():
    def row(sid, score):
        return dict(security_id=sid, algorithm_id='A_trend_quality', score=score,
                    status='eligible', primary_industry_id=sid,
                    factors={'R': score, 'G': score})
    chosen = m3_diversified([row('AAA',100.),row('BBB',95.),row('CCC',94.)],
                            'balanced', 2,
                            corr={('AAA','BBB'):.8,('AAA','CCC'):0.,('BBB','CCC'):0.})
    assert [r['security_id'] for r in chosen] == ['AAA','CCC']


def test_ny_1324_capture_is_not_same_day_eod():
    now = datetime(2026, 9, 16, 13, 24, 41, tzinfo=ZoneInfo('America/New_York'))
    assert last_complete_eod_session(now) == date(2026, 9, 15)


def test_regular_close_boundary_and_vendor_late():
    et = ZoneInfo('America/New_York')
    at_close = datetime(2026, 9, 16, 16, 0, tzinfo=et)
    before = datetime(2026, 9, 16, 15, 59, tzinfo=et)
    after = datetime(2026, 9, 16, 17, 0, tzinfo=et)
    assert last_complete_eod_session(at_close) == date(2026, 9, 16)
    assert last_complete_eod_session(before) == date(2026, 9, 15)
    assert last_complete_eod_session(after, source_finalized_through=date(2026, 9, 15)) == date(2026, 9, 15)
