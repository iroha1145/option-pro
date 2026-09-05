/**
 * 期权链派生指标（展示层纯函数）。
 * 一条记录对应一份合约，保留缺失字段与精确行权价。
 * 金额按成交量 × 买卖中价 × 100 估算，不推断实际资金流向。
 */
import type { OptionChain } from '@/api/types';
import { midpoint, volOiState, type VolOiState } from '../optionAnalysis.ts';

/** 成交量达到持仓量三倍，或零持仓有成交；不推断开平仓方向。 */
export function isAlerting(state: VolOiState): boolean {
  return (
    (state.kind === 'ratio' && state.ratio >= 3) || state.kind === 'new_opening'
  );
}

function premiumOf(
  volume: number | null,
  bid: number | null,
  ask: number | null,
): number | null {
  const m = midpoint(bid, ask);
  if (volume === null || m === null) return null;
  const estimate = volume * m * 100;
  return Number.isFinite(estimate) ? estimate : null;
}

export type ContractSide = 'call' | 'put';
export type ContractScope = 'near' | 'alerts' | 'all';
export interface ChainContract {
  id: string;
  side: ContractSide;
  strike: number;
  volume: number | null;
  openInterest: number | null;
  bid: number | null;
  ask: number | null;
  iv: number | null;
  mid: number | null;
  premium: number | null;
  volOi: VolOiState;
  activity: ('ratio' | 'zero_oi' | 'volume' | 'premium')[];
}

const nonnegative = (n: number | null): number | null =>
  n !== null && Number.isFinite(n) && n >= 0 ? n : null;

/** One row is one contract. A missing opposite leg never becomes a zero-valued contract. */
export function contractsForChain(chain: OptionChain): ChainContract[] {
  return chain.rows.flatMap((row) => {
    if (!Number.isFinite(row.strike) || row.strike <= 0) return [];
    return (['call', 'put'] as const).flatMap((side): ChainContract[] => {
      const volume = nonnegative(side === 'call' ? row.callVol : row.putVol);
      const openInterest = nonnegative(side === 'call' ? row.callOi : row.putOi);
      const bid = nonnegative(side === 'call' ? row.callBid : row.putBid);
      const ask = nonnegative(side === 'call' ? row.callAsk : row.putAsk);
      const iv = nonnegative(side === 'call' ? row.callIv : row.putIv);
      if ([volume, openInterest, bid, ask, iv].every((n) => n === null)) return [];
      const mid = midpoint(bid, ask);
      const premium = premiumOf(volume, bid, ask);
      const volOi = volOiState(volume, openInterest);
      const activity: ChainContract['activity'] = [];
      if (volOi.kind === 'ratio' && volOi.ratio >= 3) activity.push('ratio');
      if (volOi.kind === 'new_opening') activity.push('zero_oi');
      if (volume !== null && volume >= 5000) activity.push('volume');
      if (premium !== null && premium >= 500000) activity.push('premium');
      return [{ id: `${side}-${row.strike}`, side, strike: row.strike, volume, openInterest,
        bid, ask, iv, mid, premium, volOi, activity }];
    });
  }).sort((a, b) => a.strike - b.strike || (a.side === 'call' ? -1 : 1));
}

/** Near-money view uses eleven observed strikes; no invented strike spacing or spot. */
export function selectContracts(
  contracts: ChainContract[], scope: ContractScope, side: ContractSide | 'all', spot: number | null,
): ChainContract[] {
  let rows = contracts;
  if (scope === 'alerts') {
    rows = rows.filter((c) => c.activity.length > 0).sort((a, b) =>
      (b.volume ?? -1) - (a.volume ?? -1) || a.strike - b.strike);
  } else if (scope === 'near' && spot !== null && Number.isFinite(spot) && spot > 0) {
    const nearest = new Set([...new Set(contracts.map((c) => c.strike))]
      .sort((a, b) => Math.abs(a - spot) - Math.abs(b - spot) || a - b).slice(0, 11));
    rows = rows.filter((c) => nearest.has(c.strike));
  }
  return side === 'all' ? rows : rows.filter((c) => c.side === side);
}
