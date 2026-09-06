/** 股票域：watchlist / detail / signals / chart / search */
import { get, mockOr, post } from '../client';
import { quoteSymbol } from '@/lib/quoteSymbol';
import { DEFAULT_WATCHLIST_TICKERS } from '@/lib/personalWatchlist';
import { mapStockDataStatus, normalizeStatusTickers, type StockDataStatus } from '@/lib/stockDataStatus';
import { marketGet, resetMarketReadPaths } from '../marketRead';
import { asRec, pickN, pickS, pickLabel, unwrap, type Rec } from '../live';
import { mapMacroFitDrivers } from '../macroFields';
import * as fx from '@/mocks/fixtures';
import type {
  Candle,
  Signal,
  StockChart,
  StockDetail,
  StockPullResource,
  StockPullResult,
  StockSearchResult,
  WatchlistItem,
} from '../types';

/** 契约 bar {t,o,h,l,c,v,quote_only} → UI Candle（字段名 1:1，仅做容错读取） */
export function mapBar<T extends Candle = Candle>(b: Rec): T {
  const rawTime = b.t;
  const epoch = typeof rawTime === 'number' && Number.isFinite(rawTime)
    ? (rawTime >= 100_000_000_000 ? rawTime : rawTime * 1_000)
    : null;
  const parsedTime = epoch !== null ? new Date(epoch) : null;
  const isoTime = parsedTime !== null && Number.isFinite(parsedTime.getTime())
    ? parsedTime.toISOString()
    : null;
  return {
    t: pickS(b, 't') ?? isoTime ?? '',
    o: pickN(b, 'o') ?? 0,
    h: pickN(b, 'h') ?? 0,
    l: pickN(b, 'l') ?? 0,
    c: pickN(b, 'c') ?? 0,
    v: pickN(b, 'v') ?? 0,
    // 盘前盘后与仅报价 bar 要一路带到图表层：均线等常规时段指标按它剔除
    ...(b.ext === true ? { ext: true } : {}),
    ...(b.quote_only === true ? { quote_only: true } : {}),
    ...(typeof b.closed === 'boolean' ? { closed: b.closed } : {}),
  } as T;
}

/** 由收盘价计算 MA20（真实推导，非编造；不足 20 根处为 null） */
export function ma20Of(candles: Candle[]): (number | null)[] {
  // 与后端契约 moving_average_scope=regular_session_only 同口径：盘前盘后
  // （ext）与零成交的仅报价 bar 不进窗口也不画点——报价路径混进均线，
  // 会把 MA20 朝着盘后噪声拽弯。窗口是「最近 20 根合格收盘」。
  const eligible: number[] = [];
  return candles.map((bar) => {
    if (bar.ext === true || bar.quote_only === true) return null;
    eligible.push(bar.c);
    if (eligible.length < 20) return null;
    let sum = 0;
    for (let j = eligible.length - 20; j < eligible.length; j++) sum += eligible[j];
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
export function mapWatchlist(body: unknown): WatchlistItem[] {
  /* 同一 ticker 可属多个分组（生产 231 行 / 唯一 214）：按 ticker 去重，
     保留首次出现的行情行，分组名合并进 sector（UI 仅作展示文案，无按组过滤） */
  const byTicker = new Map<string, { item: WatchlistItem; groups: string[] }>();
  for (const g of unwrap(body, 'groups')) {
    const groupName = pickLabel(g, 'name') ?? '';
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
        name: pickLabel(s, 'name') ?? '',
        sector: pickLabel(s, 'sector') ?? groupName, // 契约无板块字段，回退分组名
        price: pickN(s, 'price') ?? NaN,
        change: pickN(s, 'change') ?? NaN,
        changePct: pickN(s, 'change_percent', 'changePct') ?? NaN,
        sparkline: unwrap(s, 'spark', 'sparkline')
          .map((x) => pickN(x as Rec, 'c') ?? (typeof x === 'number' ? x : null))
          .filter((x): x is number => x !== null),
        dailyTrend: mapDailyTrend(s.daily_trend),
        strengthScore: pickN(s, 'strength_score', 'strengthScore') ?? (null as unknown as number),
        signals: [],
        updatedAt: pickS(s, 'quote_as_of', 'updatedAt') ?? '',
      };
      byTicker.set(ticker, { item, groups: groupName ? [groupName] : [] });
    }
  }
  return [...byTicker.values()].map((v) => v.item);
}

/** 不把旧 7 点 spark 或无日期数组伪装成长期走势。 */
export function mapDailyTrend(body: unknown): WatchlistItem['dailyTrend'] {
  const data = asRec(body);
  if (data.interval !== '1d' || data.adjustment !== 'raw') return undefined;
  const points = unwrap(data, 'points');
  if (points.length < 2 || points.length > 30) return undefined;
  const out: { date: string; close: number }[] = [];
  for (const rawPoint of points) {
    const point = asRec(rawPoint);
    const date = pickS(point, 'date');
    const close = pickN(point, 'close');
    if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date) || close === null || close <= 0) return undefined;
    const parsed = new Date(`${date}T00:00:00Z`);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) return undefined;
    if (out.length && date <= out[out.length - 1].date) return undefined;
    out.push({ date, close });
  }
  return out;
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
    name: pickLabel(r, 'name') ?? '',
    sector: pickLabel(r, 'sector', 'sic_description') ?? '',
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
    /* 宏观适配（影子字段）来自概览端点本身。
       曾经取自 /strength/stocks/{t}，但那个端点只回答公开快照 top 切片里的代码，
       其余一律 404 —— 于是 AMD、SLB 等约 190 只票明明算得出分，界面上却写着
       「暂无宏观读数」。概览是抽屉必然会调的那次请求。 */
    macroFit: pickN(r, 'macro_fit_shadow'),
    macroTailwind: pickS(r, 'macro_tailwind'),
    macroFitConfidence: pickN(r, 'macro_fit_confidence'),
    macroSupporting: mapMacroFitDrivers(r.macro_supporting_factors),
    macroOpposing: mapMacroFitDrivers(r.macro_opposing_factors),
    macroShadowStatus: pickS(r, 'macro_shadow_status'),
    macroSnapshotDate: pickS(r, 'macro_snapshot_date'),
  };
}

/** 契约 search 返回键可能是 results 或 items（容错归一） */
function mapSearch(body: unknown): StockSearchResult[] {
  return unwrap(body, 'results', 'items').map((r) => ({
    ticker: pickS(r, 'ticker', 'symbol') ?? '',
    name: pickLabel(r, 'name') ?? '',
    sector: pickLabel(r, 'sector') ?? '',
  }));
}

function mapPullResource(body: unknown): StockPullResource {
  const r = asRec(body);
  const rawStatus = pickS(r, 'status');
  return {
    status: rawStatus === 'available' || rawStatus === 'unavailable' ? rawStatus : 'failed',
    provider: pickS(r, 'provider'),
    asOf: pickS(r, 'as_of', 'asOf'),
    persisted: r.persisted === true,
    errorCode: pickS(r, 'error_code', 'errorCode'),
    barCount: pickN(r, 'bar_count', 'barCount') ?? undefined,
    metricCount: pickN(r, 'metric_count', 'metricCount') ?? undefined,
    lastBarAt: pickS(r, 'last_bar_at', 'lastBarAt'),
  };
}

function mapPull(body: unknown): StockPullResult {
  const r = asRec(body);
  const resources = asRec(r.resources);
  return {
    ticker: pickS(r, 'ticker') ?? '',
    status: pickS(r, 'status') === 'completed' ? 'completed' : 'partial',
    fetchedAt: pickS(r, 'fetched_at', 'fetchedAt') ?? '',
    persistenceStatus: pickS(r, 'persistence_status', 'persistenceStatus') === 'completed' ? 'completed' : 'failed',
    resources: {
      overview: mapPullResource(resources.overview),
      dailyChart: mapPullResource(resources.daily_chart ?? resources.dailyChart),
      signals: mapPullResource(resources.signals),
    },
  };
}

export const stocksApi = {
  /** Shared preparation status is read directly so an old cache cannot hide failures. */
  dataStatus: async (input: readonly string[]): Promise<StockDataStatus[]> => {
    const tickers = normalizeStatusTickers(input);
    const batches: string[][] = [];
    for (let start = 0; start < tickers.length; start += 200) batches.push(tickers.slice(start, start + 200));
    const results = await Promise.all(batches.map((batch) => mockOr(
      async () => {
        const at = '2026-09-04T20:00:00Z';
        const ready = { available: true, fresh: true, as_of: at };
        return mapStockDataStatus({ items: batch.map((ticker) => ({ ticker, status: 'ready', refresh_status: 'ready', as_of: at,
          resources: { overview: ready, daily_chart: ready, signals: ready } })) }, batch);
      },
      () => get(`/stocks/data/status?tickers=${encodeURIComponent(batch.join(','))}`)
        .then((body) => mapStockDataStatus(body, batch)),
    )));
    return results.flat();
  },
  /** Detach reads started before a newly prepared daily snapshot arrived. */
  invalidatePreparedDaily: (ticker?: string): void => resetMarketReadPaths([
    ticker ? `/stocks/${encodeURIComponent(quoteSymbol(ticker))}/chart?range=1d&adjustment=raw` : '/stocks/watchlist',
  ]),
  /** Shared default view stays small; personal combinations reuse cached stock data. */
  watchlist: (force = false): Promise<WatchlistItem[]> =>
    mockOr(
      () => fx.getWatchlist(force).filter((row) => DEFAULT_WATCHLIST_TICKERS.includes(row.ticker)),
      () => marketGet('/stocks/watchlist', { ttlMs: 5_000, staleMs: 10 * 60_000, force }).then(mapWatchlist),
    ),
  watchlistFor: (tickers: string[], force = false): Promise<WatchlistItem[]> => {
    if (!tickers.length) return Promise.resolve([]);
    const symbols = [...new Set(tickers)].sort();
    return mockOr(
      () => fx.getWatchlist(force).filter((row) => symbols.includes(row.ticker)),
      () => marketGet(`/stocks/watchlist?tickers=${encodeURIComponent(symbols.join(','))}`, {
        ttlMs: 5_000, staleMs: 10 * 60_000, force,
      }).then(mapWatchlist),
    );
  },
  detail: (ticker: string, force = false): Promise<StockDetail> =>
    mockOr(
      () => fx.getStockDetail(ticker),
      () =>
        marketGet(`/stocks/${encodeURIComponent(quoteSymbol(ticker))}`, {
          ttlMs: 15_000,
          staleMs: 30 * 60_000,
          force,
        }).then(mapStockDetail),
    ),
  signals: (ticker: string): Promise<Signal[]> =>
    mockOr(
      () => fx.getStockSignals(ticker),
      () =>
        marketGet(`/stocks/${encodeURIComponent(quoteSymbol(ticker))}/signals`, {
          ttlMs: 60_000,
          staleMs: 30 * 60_000,
        }),
    ),
  // adjustment 形参保留以兼容既有调用签名；契约仅支持 adjustment=raw，live 恒发 raw
  chart: (
    ticker: string,
    range: StockChart['range'] = '1d',
    adjustment = 'raw',
    force = false,
  ): Promise<StockChart> =>
    mockOr(
      () => fx.getStockChart(ticker, range),
      // 契约仅支持 adjustment=raw；界面直接使用后端真实 K 线周期。
      () => {
        void adjustment;
        return marketGet(
          `/stocks/${encodeURIComponent(quoteSymbol(ticker))}/chart?range=${range}&adjustment=raw`,
          { ttlMs: 60_000, staleMs: 60 * 60_000, force },
        ).then((d) => mapChart(d, quoteSymbol(ticker), range));
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
  pull: (ticker: string): Promise<StockPullResult> =>
    mockOr<StockPullResult>(
      async () => {
        const [detail, chart] = await Promise.all([
          fx.getStockDetail(ticker),
          fx.getStockChart(ticker, '1d'),
        ]);
        const asOf = detail.updatedAt || new Date().toISOString();
        return {
          ticker: ticker.toUpperCase(),
          status: 'completed',
          fetchedAt: asOf,
          persistenceStatus: 'completed',
          resources: {
            overview: { status: 'available', provider: 'Mock', asOf, persisted: true },
            dailyChart: {
              status: chart.candles.length ? 'available' : 'unavailable',
              provider: 'Mock',
              asOf,
              persisted: true,
              barCount: chart.candles.length,
            },
            signals: {
              status: 'available',
              provider: 'Mock',
              asOf,
              persisted: true,
              metricCount: fx.getStockSignals(ticker).length,
            },
          },
        };
      },
      async () => {
        const t = quoteSymbol(ticker);
        const encoded = encodeURIComponent(t);
        const result = mapPull(await post(`/stocks/${encoded}/pull`, {}));
        resetMarketReadPaths([
          `/stocks/${encoded}`,
          `/stocks/${encoded}/chart?range=1d&adjustment=raw`,
          `/signals/stock/${encoded}`,
          `/stocks/${encoded}/signals`,
          `/stocks/${encoded}/technical`,
        ]);
        return result;
      },
    ),
};
