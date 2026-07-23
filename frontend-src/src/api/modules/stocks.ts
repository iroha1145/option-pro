/** 股票域：watchlist / detail / signals / chart / search */
import { mockOr } from '../client';
import { marketGet } from '../marketRead';
import { asRec, pickN, pickS, unwrap, type Rec } from '../live';
import * as fx from '@/mocks/fixtures';
import type { Candle, Signal, StockChart, StockDetail, StockSearchResult, WatchlistItem } from '../types';

/** 契约 bar {t,o,h,l,c,v,quote_only} → UI Candle（字段名 1:1，仅做容错读取） */
export function mapBar<T extends Candle = Candle>(b: Rec): T {
  return {
    t: pickS(b, 't') ?? '',
    o: pickN(b, 'o') ?? 0,
    h: pickN(b, 'h') ?? 0,
    l: pickN(b, 'l') ?? 0,
    c: pickN(b, 'c') ?? 0,
    v: pickN(b, 'v') ?? 0,
    ...(b.quote_only === true ? { quote_only: true } : {}),
  } as T;
}

/** 由收盘价计算 MA20（真实推导，非编造；不足 20 根处为 null） */
export function ma20Of(candles: Candle[]): (number | null)[] {
  return candles.map((_, i) => {
    if (i < 19) return null;
    let sum = 0;
    for (let j = i - 19; j <= i; j++) sum += candles[j].c;
    return Math.round((sum / 20) * 100) / 100;
  });
}

/** 契约 {bars:[...], as_of, _stale?} → UI StockChart */
export function mapChart(body: unknown, ticker: string, range: StockChart['range']): StockChart {
  const bars = unwrap(body, 'bars', 'candles');
  const candles = bars.map(mapBar);
  return { ticker, range, candles, ma20: ma20Of(candles) };
}

/**
 * 契约 {groups:[{id,name,stocks:[{ticker,name,price,change,change_percent,
 * spark[≤7],quote_as_of,quote_session}]}]} → 扁平 UI WatchlistItem[]
 * 契约无 sector/strengthScore/signals —— 不编造：strengthScore 置 null（UI 按缺失处理），signals 空数组。
 */
function mapWatchlist(body: unknown): WatchlistItem[] {
  /* 同一 ticker 可属多个分组（生产 231 行 / 唯一 214）：按 ticker 去重，
     保留首次出现的行情行，分组名合并进 sector（UI 仅作展示文案，无按组过滤） */
  const byTicker = new Map<string, { item: WatchlistItem; groups: string[] }>();
  for (const g of unwrap(body, 'groups')) {
    const groupName = pickS(g, 'name') ?? '';
    for (const s of unwrap(g, 'stocks')) {
      const ticker = pickS(s, 'ticker') ?? '';
      if (!ticker) continue;
      const seen = byTicker.get(ticker);
      if (seen) {
        if (groupName && !seen.groups.includes(groupName)) {
          seen.groups.push(groupName);
          if (!pickS(s as Rec, 'sector')) seen.item.sector = seen.groups.join(' / ');
        }
        continue;
      }
      const item: WatchlistItem = {
        ticker,
        name: pickS(s, 'name') ?? '',
        sector: pickS(s, 'sector') ?? groupName, // 契约无板块字段，回退分组名
        price: pickN(s, 'price') ?? 0,
        change: pickN(s, 'change') ?? 0,
        changePct: pickN(s, 'change_percent', 'changePct') ?? 0,
        sparkline: unwrap(s, 'spark', 'sparkline')
          .map((x) => pickN(x as Rec, 'c') ?? (typeof x === 'number' ? x : null))
          .filter((x): x is number => x !== null),
        strengthScore: pickN(s, 'strength_score', 'strengthScore') ?? (null as unknown as number),
        signals: [],
        updatedAt: pickS(s, 'quote_as_of', 'updatedAt') ?? '',
      };
      byTicker.set(ticker, { item, groups: groupName ? [groupName] : [] });
    }
  }
  return [...byTicker.values()].map((v) => v.item);
}

/**
 * 契约 /stocks/{t}（snake_case：change_percent/prev_close/market_cap/pe_ratio/
 * year_low/year_high/sic_description…）→ UI StockDetail。
 * 契约无 sector/strengthScore/signals/sparkline/avgVolume/ivPercentile —— 不编造：
 * sector 回退行业描述 sic_description，数值缺失置 null（UI 渲染「—」）。
 */
function mapStockDetail(body: unknown): StockDetail {
  const r = asRec(body);
  const yearLow = pickN(r, 'year_low');
  const yearHigh = pickN(r, 'year_high');
  return {
    ticker: pickS(r, 'ticker') ?? '',
    name: pickS(r, 'name') ?? '',
    sector: pickS(r, 'sector', 'sic_description') ?? '',
    price: pickN(r, 'price') ?? 0,
    change: pickN(r, 'change') ?? (null as unknown as number),
    changePct: pickN(r, 'changePct', 'change_percent') ?? (null as unknown as number),
    sparkline: Array.isArray(r.sparkline) ? (r.sparkline as number[]) : [],
    strengthScore: pickN(r, 'strengthScore', 'strength_score') ?? (null as unknown as number),
    signals: [],
    updatedAt: pickS(r, 'updatedAt', 'as_of') ?? '',
    open: pickN(r, 'open') ?? (null as unknown as number),
    high: pickN(r, 'high') ?? (null as unknown as number),
    low: pickN(r, 'low') ?? (null as unknown as number),
    prevClose: pickN(r, 'prevClose', 'prev_close') ?? (null as unknown as number),
    volume: pickN(r, 'volume') ?? (null as unknown as number),
    avgVolume: pickN(r, 'avgVolume', 'avg_volume') ?? (null as unknown as number),
    marketCap: pickN(r, 'marketCap', 'market_cap') ?? (null as unknown as number),
    pe: pickN(r, 'pe', 'pe_ratio'),
    ivPercentile: pickN(r, 'ivPercentile', 'iv_percentile') ?? (null as unknown as number),
    range52w: yearLow !== null && yearHigh !== null ? [yearLow, yearHigh] : ((null as unknown) as [number, number]),
    priceProvider: pickS(r, 'priceProvider', 'price_provider'),
    profileProvider: pickS(r, 'profileProvider', 'profile_provider'),
  };
}

/** 契约 search 返回键可能是 results 或 items（容错归一） */
function mapSearch(body: unknown): StockSearchResult[] {
  return unwrap(body, 'results', 'items').map((r) => ({
    ticker: pickS(r, 'ticker', 'symbol') ?? '',
    name: pickS(r, 'name') ?? '',
    sector: pickS(r, 'sector') ?? '',
  }));
}

export const stocksApi = {
  watchlist: (force = false): Promise<WatchlistItem[]> =>
    mockOr(
      () => fx.getWatchlist(force),
      () =>
        marketGet('/stocks/watchlist', {
          ttlMs: 5_000,
          staleMs: 10 * 60_000,
          force,
        }).then(mapWatchlist),
    ),
  detail: (ticker: string): Promise<StockDetail> =>
    mockOr(
      () => fx.getStockDetail(ticker),
      () =>
        marketGet(`/stocks/${encodeURIComponent(ticker)}`, {
          ttlMs: 15_000,
          staleMs: 30 * 60_000,
        }).then(mapStockDetail),
    ),
  signals: (ticker: string): Promise<Signal[]> =>
    mockOr(
      () => fx.getStockSignals(ticker),
      () =>
        marketGet(`/stocks/${encodeURIComponent(ticker)}/signals`, {
          ttlMs: 60_000,
          staleMs: 30 * 60_000,
        }),
    ),
  // adjustment 形参保留以兼容既有调用签名；契约仅支持 adjustment=raw，live 恒发 raw
  chart: (ticker: string, range: StockChart['range'] = '1d', adjustment = 'raw'): Promise<StockChart> =>
    mockOr(
      () => fx.getStockChart(ticker, range),
      // 契约仅支持 adjustment=raw；界面直接使用后端真实 K 线周期。
      () => {
        void adjustment;
        return marketGet(
          `/stocks/${encodeURIComponent(ticker)}/chart?range=${range}&adjustment=raw`,
          { ttlMs: 60_000, staleMs: 60 * 60_000 },
        ).then((d) => mapChart(d, ticker, range));
      },
    ),
  search: (q: string): Promise<StockSearchResult[]> =>
    mockOr(
      () => fx.searchStocks(q),
      () =>
        marketGet(`/stocks/search?q=${encodeURIComponent(q)}`, {
          ttlMs: 10 * 60_000,
          staleMs: 24 * 60 * 60_000,
        }).then(mapSearch),
    ),
};
