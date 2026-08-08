/**
 * 期权链派生指标（展示层纯函数）。
 * 只从 OptionChain 已有字段计算：逐腿求和时跳过缺失腿，整侧全缺则合计为
 * null（显「—」），绝不落 0；权利金流按 vol × 买卖中价 × 100 估算。
 */
import type { OptionChain, OptionChainRow } from '@/api/types';
import { midpoint, volOiState, type VolOiState } from '../optionAnalysis.ts';

export interface RowMeta {
  callVolOi: VolOiState;
  putVolOi: VolOiState;
  callAlert: boolean;
  putAlert: boolean;
  callPremium: number | null; // 美元，估算
  putPremium: number | null;
}

/** 量持比 > 3 或持仓量为 0 而有成交（全部新开仓）都算异动。 */
export function isAlerting(state: VolOiState): boolean {
  return (
    (state.kind === 'ratio' && state.ratio > 3) || state.kind === 'new_opening'
  );
}

function premiumOf(
  volume: number | null,
  bid: number | null,
  ask: number | null,
): number | null {
  const m = midpoint(bid, ask);
  if (volume === null || m === null) return null;
  return volume * m * 100;
}

export function rowMeta(r: OptionChainRow): RowMeta {
  const callVolOi = volOiState(r.callVol, r.callOi);
  const putVolOi = volOiState(r.putVol, r.putOi);
  return {
    callVolOi,
    putVolOi,
    callAlert: isAlerting(callVolOi),
    putAlert: isAlerting(putVolOi),
    callPremium: premiumOf(r.callVol, r.callBid, r.callAsk),
    putPremium: premiumOf(r.putVol, r.putBid, r.putAsk),
  };
}

/** 异动角标文案：有比值显倍数，新开仓显 ∞（量持比不适用）。 */
export function volOiBadge(state: VolOiState): string | null {
  if (state.kind === 'ratio') return state.ratio > 3 ? `${state.ratio.toFixed(1)}×` : null;
  return state.kind === 'new_opening' ? '∞' : null;
}

export interface ChainTotals {
  callVol: number | null;
  putVol: number | null;
  callOi: number | null;
  putOi: number | null;
  /** 估算权利金流（美元）逐腿合计；该侧无可估算腿时为 null。 */
  callPremium: number | null;
  putPremium: number | null;
  /** 横条归一基准：两侧共用一个全链最大值，Call/Put 量级才可比。 */
  maxVol: number | null;
  maxOi: number | null;
}

function sumLegs(values: (number | null)[]): number | null {
  let total = 0;
  let seen = false;
  for (const value of values) {
    if (value === null || !Number.isFinite(value)) continue;
    total += value;
    seen = true;
  }
  return seen ? total : null;
}

function maxLeg(values: (number | null)[]): number | null {
  let max = 0;
  let seen = false;
  for (const value of values) {
    if (value === null || !Number.isFinite(value)) continue;
    if (value > max) max = value;
    seen = true;
  }
  return seen && max > 0 ? max : null;
}

export function summarizeChain(chain: OptionChain): ChainTotals {
  const rows = chain.rows;
  return {
    callVol: sumLegs(rows.map((r) => r.callVol)),
    putVol: sumLegs(rows.map((r) => r.putVol)),
    callOi: sumLegs(rows.map((r) => r.callOi)),
    putOi: sumLegs(rows.map((r) => r.putOi)),
    callPremium: sumLegs(
      rows.map((r) => premiumOf(r.callVol, r.callBid, r.callAsk)),
    ),
    putPremium: sumLegs(
      rows.map((r) => premiumOf(r.putVol, r.putBid, r.putAsk)),
    ),
    // 全链归一：把两侧放进同一个数组取最大值，而不是各侧各归一。
    maxVol: maxLeg(rows.flatMap((r) => [r.callVol, r.putVol])),
    maxOi: maxLeg(rows.flatMap((r) => [r.callOi, r.putOi])),
  };
}

/** 横条宽度占比 0..1；值缺失或无归一基准时为 0（不画条，数字仍显「—」）。 */
export function barShare(value: number | null, max: number | null): number {
  if (value === null || max === null || max <= 0 || !Number.isFinite(value)) {
    return 0;
  }
  return Math.min(1, Math.max(0, value / max));
}

/** 两侧合计：至少一侧有值才返回数字，全缺为 null。 */
export function sumSides(
  call: number | null,
  put: number | null,
): number | null {
  if (call === null && put === null) return null;
  return (call ?? 0) + (put ?? 0);
}

/** Call/Put 比值：分母缺失 → null（显「—」）；分母为 0 而分子有量 → Infinity（显 ∞）。 */
export function cpRatio(
  call: number | null,
  put: number | null,
): number | null {
  if (call === null || put === null) return null;
  if (put === 0) return call > 0 ? Infinity : null;
  return call / put;
}
