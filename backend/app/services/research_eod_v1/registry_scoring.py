"""Research-only helpers. No prices are fetched and no market backtest is run.

This module resolves the registered weight matrix, aggregates already-computed
0..100 factors, checks common (not setup-specific) gates and estimates planned
position capacity. It is NOT a raw-feature engine, broker or production patch.
Python 3.10+; standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = PACKAGE_DIR / 'registry.json'
FACTORS = ('T', 'M', 'S', 'B', 'P', 'V', 'R', 'G')


def finite(value: Any, *, name: str, minimum: float | None = None,
           maximum: float | None = None) -> float:
    """Reject missing, booleans and nonfinite values rather than imputing them."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number, not {type(value).__name__}')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'{name} must be finite')
    if minimum is not None and result < minimum:
        raise ValueError(f'{name} must be >= {minimum}')
    if maximum is not None and result > maximum:
        raise ValueError(f'{name} must be <= {maximum}')
    return result


def normalized(weights: Mapping[str, float]) -> dict[str, float]:
    if set(weights) != set(FACTORS):
        raise ValueError('weights must contain exactly the eight registered factors')
    clean = {k: finite(weights[k], name=f'weight.{k}', minimum=0) for k in FACTORS}
    total = sum(clean.values())
    if total <= 0:
        raise ValueError('positive total weight is required')
    return {k: v / total for k, v in clean.items()}


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    with Path(path or DEFAULT_REGISTRY_PATH).open(encoding='utf-8') as handle:
        data = json.load(handle)
    validate_registry(data)
    return data


def validate_registry(data: Mapping[str, Any]) -> None:
    if tuple(data['factors']) != FACTORS:
        raise ValueError('unsupported factor order')
    if len(data['sectors']) != 24 or len(data['base_algorithm_weights']) != 4:
        raise ValueError('expected 24 sectors and four algorithm families')
    if set(data['profiles']) != {'conservative', 'balanced', 'aggressive'}:
        raise ValueError('unexpected risk profiles')
    if set(data['horizons']) != {'short', 'mid', 'long'}:
        raise ValueError('unexpected horizons')
    for sid, sector in data['sectors'].items():
        if set(sector['candidates']) != set(data['base_algorithm_weights']):
            raise ValueError(f'{sid}: incomplete family coverage')
        for candidate in sector['candidates'].values():
            weights = candidate['weights']
            normalized(weights)
            if not math.isclose(sum(weights.values()), 1, abs_tol=1e-8):
                raise ValueError(f'{sid}: weights must sum to one')
    for section in ('profiles', 'horizons'):
        for key, item in data[section].items():
            if len(item['factor_tilt']) != len(FACTORS):
                raise ValueError(f'{section}.{key}: wrong tilt length')
            for tilt in item['factor_tilt']:
                if finite(tilt, name='tilt', minimum=0) == 0:
                    raise ValueError('tilts must be positive')


def resolve_weights(data: Mapping[str, Any], sector: str, algorithm: str,
                    profile: str = 'balanced', horizon: str = 'mid') -> dict[str, float]:
    try:
        base = data['sectors'][sector]['candidates'][algorithm]['weights']
        p = data['profiles'][profile]['factor_tilt']
        h = data['horizons'][horizon]['factor_tilt']
    except KeyError as exc:
        raise ValueError(f'unknown configuration key: {exc}') from exc
    return normalized({f: base[f] * p[i] * h[i] for i, f in enumerate(FACTORS)})


@dataclass(frozen=True)
class ScoreResult:
    score: float | None
    coverage: float
    effective_weights: dict[str, float]
    contributions: dict[str, float]
    missing: tuple[str, ...]
    status: str


def score_features(features: Mapping[str, float | None], weights: Mapping[str, float],
                   *, coverage_min: float = .9,
                   required: Sequence[str] = ('T', 'M', 'S', 'R')) -> ScoreResult:
    """Aggregate only supplied, valid 0..100 factors. Missing required => reject."""
    floor = finite(coverage_min, name='coverage_min', minimum=0, maximum=1)
    w = normalized(weights)
    if not set(required).issubset(FACTORS):
        raise ValueError('unknown required factor')
    if set(features) - set(FACTORS):
        raise ValueError('unknown feature key')
    active: dict[str, float] = {}
    missing: list[str] = []
    for factor in FACTORS:
        value = features.get(factor)
        if value is None:
            missing.append(factor)
        else:
            active[factor] = finite(value, name=factor, minimum=0, maximum=100)
    coverage = sum(w[f] for f in active)
    if set(required).intersection(missing) or coverage + 1e-12 < floor or coverage <= 0:
        return ScoreResult(None, coverage, {}, {}, tuple(missing), 'DATA_INSUFFICIENT')
    effective = {f: w[f] / coverage for f in active}
    contributions = {f: active[f] * effective[f] for f in active}
    return ScoreResult(sum(contributions.values()), coverage, effective,
                       contributions, tuple(missing), 'SCORED_NOT_SETUP_VALIDATED')


@dataclass(frozen=True)
class CommonInputs:
    raw_close: float
    adv20: float
    atr_pct: float
    sector_median_atr_pct: float
    extension_atr: float
    structure_score: float
    history_sessions: int
    us_venue_and_security_eligible: bool
    daily_data_complete: bool
    currently_tradable: bool
    unresolved_upthrust: bool
    structure_invalidated: bool


def common_rejections(data: Mapping[str, Any], sector: str, algorithm: str,
                      profile: str, inputs: CommonInputs, result: ScoreResult) -> tuple[str, ...]:
    """Common gates only. A/B/C/D setup and event gates remain mandatory."""
    spec = data['sectors'][sector]
    p = data['profiles'][profile]
    x = inputs
    reasons: list[str] = []
    for name in ('raw_close', 'adv20', 'atr_pct', 'sector_median_atr_pct',
                 'extension_atr', 'structure_score'):
        finite(getattr(x, name), name=name, minimum=0)
    if x.raw_close <= 0 or x.sector_median_atr_pct <= 0:
        raise ValueError('price and median ATR percentage must be positive')
    for name in ('us_venue_and_security_eligible', 'daily_data_complete',
                 'currently_tradable', 'unresolved_upthrust', 'structure_invalidated'):
        if type(getattr(x, name)) is not bool:
            raise ValueError(f'{name} requires an explicit boolean')
    if type(x.history_sessions) is not int or x.history_sessions < 0:
        raise ValueError('history_sessions requires a nonnegative integer')
    checks = (
        (not x.us_venue_and_security_eligible, 'OUT_OF_SCOPE'),
        (not x.daily_data_complete, 'INCOMPLETE_DAILY_DATA'),
        (not x.currently_tradable, 'NOT_TRADABLE'),
        (x.raw_close < data['global_rules']['minimum_raw_price_usd'], 'LOW_PRICE'),
        (x.adv20 < max(spec['gates']['minimum_adv_usd'], p['minimum_adv_usd']), 'LOW_ADV'),
        (x.atr_pct > min(p['atr_absolute_cap_pct'],
                        p['atr_sector_median_multiplier'] * x.sector_median_atr_pct), 'HIGH_ATR'),
        (x.extension_atr > p['max_extension_atr'], 'EXTENDED'),
        (x.structure_score < p['structure_floor'], 'WEAK_STRUCTURE'),
        (x.history_sessions < spec['candidates'][algorithm]['min_history_sessions'], 'SHORT_HISTORY'),
        (x.unresolved_upthrust, 'UNRESOLVED_UPTHRUST'),
        (x.structure_invalidated, 'INVALIDATED'),
        (result.score is None, 'MISSING_SCORE'),
        (result.coverage + 1e-12 < p['coverage_min'], 'LOW_COVERAGE'),
        (result.score is not None and result.score < p['score_floor'], 'LOW_SCORE'),
    )
    reasons.extend(reason for rejected, reason in checks if rejected)
    return tuple(reasons)


@dataclass(frozen=True)
class Capacity:
    shares: int
    notional: float
    planned_risk_fraction: float
    adv_participation: float
    planned_risk_distance_fraction: float


def position_capacity(capital: float, close: float, known_invalidation: float,
                      atr: float, adv20: float, profile: Mapping[str, Any]) -> Capacity:
    """Plan at T close, not an assured T+1 fill or a guaranteed maximum loss."""
    for name, value in [('capital', capital), ('close', close), ('atr', atr), ('adv20', adv20)]:
        if finite(value, name=name, minimum=0) == 0:
            raise ValueError(f'{name} must be positive')
    finite(known_invalidation, name='known_invalidation', minimum=0)
    if known_invalidation >= close:
        raise ValueError('a long position needs an invalidation below close')
    max_w = finite(profile['max_position_fraction'], name='max_weight', minimum=0, maximum=1)
    risk = finite(profile['position_risk_budget_fraction'], name='risk_budget', minimum=0, maximum=1)
    part = finite(profile['max_order_adv_fraction'], name='participation', minimum=0, maximum=1)
    distance = max((close - known_invalidation) / close, atr / close)
    amount = min(capital * max_w, capital * risk / distance, adv20 * part)
    shares = math.floor(amount / close)
    actual = shares * close
    return Capacity(shares, actual, actual * distance / capital, actual / adv20, distance)


def validate_information_cutoff(*, signal_time: datetime, session_close: datetime,
                                source_available_at: datetime) -> None:
    for value in (signal_time, session_close, source_available_at):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('all timestamps must be timezone-aware')
    if source_available_at < session_close:
        raise ValueError('completed-session data cannot be available before the session closes')
    if signal_time < source_available_at:
        raise ValueError('signal predates source availability')


def capped_selection(rows: Sequence[Mapping[str, Any]], top_k: int,
                     score_floor: float) -> list[Mapping[str, Any]]:
    """Eligible means all upstream setup gates passed, NOT merely scored."""
    if type(top_k) is not int or top_k < 0:
        raise ValueError('top_k must be a nonnegative integer')
    floor = finite(score_floor, name='score_floor', minimum=0, maximum=100)
    admitted: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        security_id = row.get('security_id')
        if not isinstance(security_id, str) or not security_id:
            raise ValueError('stable security_id required')
        if security_id in admitted:
            raise ValueError('deduplicate/calibrate multi-theme rows before final selection')
        if row.get('status') != 'eligible' or row.get('score') is None:
            continue
        score = finite(row['score'], name='row.score', minimum=0, maximum=100)
        if score >= floor:
            admitted[security_id] = row
    return sorted(admitted.values(), key=lambda row: (-float(row['score']), row['security_id']))[:top_k]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY_PATH)
    args = parser.parse_args()
    data = load_registry(args.registry)
    print(json.dumps({'sectors': len(data['sectors']), 'families': 4, 'profiles': 3,
                      'horizons': 3, 'registered_primary_evaluations': 864,
                      'market_backtests_executed_by_this_program': 0,
                      'example_weights': resolve_weights(data, 'semiconductors', 'A_trend_quality')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
