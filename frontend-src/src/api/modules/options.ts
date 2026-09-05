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

export interface OptionExpirationReadOptions {
  force?: boolean;
}

/**
 * 契约 {results:[{ticker,contract_type,strike,expiration,volume,open_interest,vol_oi_ratio,premium,...}]} → UI
 * 缺少 ticker 或行权价的记录无法定位到具体合约，直接丢弃；数值字段缺失保留 null。
 */
function mapUnusual(body: unknown): UnusualOption[] {
  const rows: UnusualOption[] = [];
  unwrap(body, 'results', 'items').forEach((r, i) => {
    const ticker = pickS(r, 'ticker');
    const strike = pickN(r, 'strike');
    if (!ticker || strike === null) return;
    const expiration = pickS(r, 'expiration') ?? '';
    rows.push({
      id: pickS(r, 'id') ?? `${ticker}-${strike}-${expiration}-${i}`,
      ticker,
      side: (pickS(r, 'side', 'contract_type') as UnusualOption['side']) ?? 'call',
      strike,
      expiration,
      volume: pickN(r, 'volume'),
      openInterest: pickN(r, 'openInterest', 'open_interest'),
      premium: pickN(r, 'premium'),
      // 契约 §3：direction 字段恒 null（direction_deprecated）—— 不复活方向推断，情绪恒 neutral
      sentiment: 'neutral',
      at: pickS(r, 'at', 'as_of') ?? '',
    });
  });
  return rows;
}

/**
 * 契约 calls/puts 单腿字段（snake/camel 容错）；上游未给的字段保持 null。
 * 隐含波动率统一保留小数单位：0.32 表示 32%，只在展示时转成百分比。
 * 后端可能使用 Yahoo 原值或模型回算，目前没有逐腿的计算来源标识。
 */
function legRow(leg: unknown, prefix: 'call' | 'put'): Partial<OptionChainRow> {
  const r = asRec(leg);
  return {
    [`${prefix}Oi`]: pickN(r, 'oi', 'open_interest', `${prefix}Oi`),
    [`${prefix}Vol`]: pickN(r, 'vol', 'volume', `${prefix}Vol`),
    [`${prefix}Iv`]: pickN(r, 'iv', 'implied_volatility', `${prefix}Iv`),
    [`${prefix}Bid`]: pickN(r, 'bid', `${prefix}Bid`),
    [`${prefix}Ask`]: pickN(r, 'ask', `${prefix}Ask`),
  };
}

/**
 * 契约 {underlying_price, calls:[], puts:[], alerts, as_of} → UI OptionChain（按 strike 合腿）
 * 行权价缺失的合约无法归行，丢弃；只有一侧存在时另一侧留 null，不补一条全零腿。
 */
function mapChain(body: unknown, ticker: string, expiration: string): OptionChain {
  const r = asRec(body);
  const byStrike = new Map<number, OptionChainRow>();
  const ensure = (strike: number): OptionChainRow => {
    let row = byStrike.get(strike);
    if (!row) {
      row = {
        strike,
        callOi: null, callVol: null, callIv: null, callBid: null, callAsk: null,
        putOi: null, putVol: null, putIv: null, putBid: null, putAsk: null,
      };
      byStrike.set(strike, row);
    }
    return row;
  };
  for (const c of unwrap(body, 'calls')) {
    const strike = pickN(c, 'strike');
    if (strike === null) continue;
    Object.assign(ensure(strike), legRow(c, 'call'));
  }
  for (const p of unwrap(body, 'puts')) {
    const strike = pickN(p, 'strike');
    if (strike === null) continue;
    Object.assign(ensure(strike), legRow(p, 'put'));
  }
  const rows = [...byStrike.values()].sort((a, b) => a.strike - b.strike);
  return {
    ticker,
    expiration,
    spot: pickN(r, 'underlying_price', 'spot'),
    rows,
    provider: pickS(r, 'provider'),
    asOf: pickS(r, 'as_of', 'asOf'),
    stale: r._stale === true || r.stale === true,
  };
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
  expirations: (
    ticker: string,
    readOptions: OptionExpirationReadOptions = {},
  ): Promise<string[]> =>
    mockOr(
      () => fx2.getOptionExpirations(ticker),
      () =>
        marketGet(`/options/${encodeURIComponent(ticker)}/expirations`, {
          ttlMs: 5 * 60_000,
          staleMs: 30 * 60_000,
          force: readOptions.force,
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
