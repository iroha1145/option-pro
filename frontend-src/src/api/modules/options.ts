/** 期权域：unusual / expirations / chain */
import { get, mockOr, toQuery } from '../client';
import { marketGet } from '../marketRead';
import { asRec, pickN, pickS, unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { OptionChain, OptionChainRow, UnusualOption } from '../types';

export interface UnusualParams {
  type?: 'all' | 'call' | 'put';
  /** 契约参数名 min_vol_oi（下划线），默认 1.0 */
  minVolOi?: number;
}

/** 契约 {results:[{ticker,contract_type,strike,expiration,volume,open_interest,vol_oi_ratio,premium,...}]} → UI */
function mapUnusual(body: unknown): UnusualOption[] {
  return unwrap(body, 'results', 'items').map((r, i) => {
    const ticker = pickS(r, 'ticker') ?? '';
    const strike = pickN(r, 'strike') ?? 0;
    const expiration = pickS(r, 'expiration') ?? '';
    return {
      id: pickS(r, 'id') ?? `${ticker}-${strike}-${expiration}-${i}`,
      ticker,
      side: (pickS(r, 'side', 'contract_type') as UnusualOption['side']) ?? 'call',
      strike,
      expiration,
      volume: pickN(r, 'volume') ?? 0,
      openInterest: pickN(r, 'openInterest', 'open_interest') ?? 0,
      premium: pickN(r, 'premium') ?? 0,
      // 契约 §3：direction 字段恒 null（direction_deprecated）—— 不复活方向推断，情绪恒 neutral
      sentiment: 'neutral',
      at: pickS(r, 'at', 'as_of') ?? '',
    };
  });
}

/** 契约 calls/puts 单腿字段（snake/camel 容错） */
function legRow(leg: unknown, prefix: 'call' | 'put'): Partial<OptionChainRow> {
  const r = asRec(leg);
  return {
    [`${prefix}Oi`]: pickN(r, 'oi', 'open_interest', `${prefix}Oi`) ?? 0,
    [`${prefix}Vol`]: pickN(r, 'vol', 'volume', `${prefix}Vol`) ?? 0,
    [`${prefix}Iv`]: pickN(r, 'iv', 'implied_volatility', `${prefix}Iv`) ?? 0,
    [`${prefix}Bid`]: pickN(r, 'bid', `${prefix}Bid`) ?? 0,
    [`${prefix}Ask`]: pickN(r, 'ask', `${prefix}Ask`) ?? 0,
  };
}

/** 契约 {underlying_price, calls:[], puts:[], alerts, as_of} → UI OptionChain（按 strike 合腿） */
function mapChain(body: unknown, ticker: string, expiration: string): OptionChain {
  const r = asRec(body);
  const spot = pickN(r, 'underlying_price', 'spot') ?? 0;
  const byStrike = new Map<number, OptionChainRow>();
  const ensure = (strike: number): OptionChainRow => {
    let row = byStrike.get(strike);
    if (!row) {
      row = {
        strike,
        callOi: 0, callVol: 0, callIv: 0, callBid: 0, callAsk: 0,
        putOi: 0, putVol: 0, putIv: 0, putBid: 0, putAsk: 0,
      };
      byStrike.set(strike, row);
    }
    return row;
  };
  for (const c of unwrap(body, 'calls')) {
    const strike = pickN(c, 'strike') ?? 0;
    Object.assign(ensure(strike), legRow(c, 'call'));
  }
  for (const p of unwrap(body, 'puts')) {
    const strike = pickN(p, 'strike') ?? 0;
    Object.assign(ensure(strike), legRow(p, 'put'));
  }
  const rows = [...byStrike.values()].sort((a, b) => a.strike - b.strike);
  return { ticker, expiration, spot, rows };
}

export const optionsApi = {
  unusual: (params: UnusualParams = {}): Promise<UnusualOption[]> =>
    mockOr(
      () => fx2.getUnusualOptions(),
      // 契约：GET /api/options/unusual?type=all|call|put&min_vol_oi=1.0
      () => {
        const qs = toQuery({ type: params.type, min_vol_oi: params.minVolOi });
        return get(`/options/unusual${qs ? `?${qs}` : ''}`).then(mapUnusual);
      },
    ),
  expirations: (ticker: string): Promise<string[]> =>
    mockOr(
      () => fx2.getOptionExpirations(ticker),
      () =>
        marketGet(`/options/${encodeURIComponent(ticker)}/expirations`, {
          ttlMs: 5 * 60_000,
          staleMs: 30 * 60_000,
        }).then((d) =>
          unwrap(d, 'expirations')
            .map((x) => pickS(asRec(x), 'date', 'expiration') ?? (typeof x === 'string' ? x : null))
            .filter((x): x is string => x !== null),
        ),
    ),
  chain: (ticker: string, expiration?: string): Promise<OptionChain> =>
    mockOr(
      () => fx2.getOptionChain(ticker, expiration),
      () =>
        marketGet(
          `/options/${encodeURIComponent(ticker)}/chain${expiration ? `?expiration=${encodeURIComponent(expiration)}` : ''}`,
          { ttlMs: 60_000, staleMs: 30 * 60_000 },
        ).then((d) => mapChain(d, ticker, expiration ?? '')),
    ),
};
