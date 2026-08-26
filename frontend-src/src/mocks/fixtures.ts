/**
 * Mock fixtures · 市场 / 股票 / 信号 / 强度（design.md §11）
 * 确定性种子；时间戳相对「当前」动态生成；写操作更新本地 fixture。
 */
import { Rng, round2, round4 } from './rng';
import { TICKER_POOL, type TickerInfo } from './data';
import type {
  Candle,
  IndexQuote,
  MarketSignalsSummary,
  MarketStatus,
  MarketStrength,
  ScreenerRow,
  Signal,
  StockChart,
  StockDetail,
  StockSearchResult,
  StrengthProfile,
  WatchlistItem,
} from '@/api/types';
import { t as __t } from '../i18n/core.ts';
// 指纹/日期口径与运行时同一份实现：mock 自己再写一遍，闸门就会在本地静默关掉
import {
  FINGERPRINT_ALGORITHM,
  barFingerprint,
  barStampForRange,
  closedBarsForFingerprint,
} from '@/components/detail/chart-drawings/analysis/mapBundle';

const rng = new Rng(20240521);

/* live 也要读的标签搬到了 @/lib/signalLabels：留在这里会把整个 fixture 模块钉在
   live 依赖图里。这里重新导出，mock 侧的既有引用不必改。 */
export { SIGNAL_LABELS, SIGNAL_TYPES } from '@/lib/signalLabels';
import { SIGNAL_LABELS, SIGNAL_TYPES } from '@/lib/signalLabels';

const infoOf = (t: string): TickerInfo => TICKER_POOL.find((x) => x.ticker === t) ?? TICKER_POOL[0];

/* ---------------- 指数 tape ---------------- */
const INDEX_BASE: { code: string; symbol: string; name: string; base: number }[] = [
  { code: 'SPX', symbol: '^GSPC', name: __t('标普 500'), base: 5972.4 },
  { code: 'NDX', symbol: '^NDX', name: __t('纳指 100'), base: 21468.2 },
  { code: 'DJI', symbol: '^DJI', name: '道琼斯', base: 43828.1 },
  { code: 'RUT', symbol: '^RUT', name: __t('罗素 2000'), base: 2382.6 },
  { code: 'SOX', symbol: '^SOX', name: '费城半导体', base: 5124.7 },
  { code: 'VIX', symbol: '^VIX', name: __t('波动率指数'), base: 14.86 },
];

interface QuoteState { price: number; prevClose: number }
const indexState = new Map<string, QuoteState>(
  INDEX_BASE.map((ix): [string, QuoteState] => {
    const prevClose = ix.base;
    const pct = ix.code === 'VIX' ? rng.float(-4, 5) : rng.float(-1.4, 1.8);
    return [ix.code, { price: round2(prevClose * (1 + pct / 100)), prevClose }];
  }),
);

function jitter(state: QuoteState, vol = 0.0011): void {
  const drift = (Math.random() - 0.5) * 2 * vol;
  state.price = round2(state.price * (1 + drift));
}

export function getIndices(): IndexQuote[] {
  return INDEX_BASE.map(({ code, symbol, name }) => {
    const s = indexState.get(code)!;
    jitter(s, code === 'VIX' ? 0.004 : 0.0009);
    const change = round2(s.price - s.prevClose);
    return { code, symbol, name, price: s.price, change, changePct: round4(change / s.prevClose) * 100 };
  });
}

/* ---------------- 市场状态（按真实纽约时间推导） ---------------- */
export function nyNow(): Date {
  return new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
}

export function getMarketStatus(): MarketStatus {
  const ny = nyNow();
  const day = ny.getDay();
  const mins = ny.getHours() * 60 + ny.getMinutes();
  const isWeekday = day >= 1 && day <= 5;
  let session: MarketStatus['session'] = 'closed';
  let label = __t('休市');
  if (isWeekday) {
    if (mins >= 240 && mins < 570) { session = 'premarket'; label = __t('盘前'); }
    else if (mins >= 570 && mins < 960) { session = 'regular'; label = __t('盘中'); }
    else if (mins >= 960 && mins < 1200) { session = 'afterhours'; label = __t('盘后'); }
  }
  let nextEvent: MarketStatus['nextEvent'] = null;
  if (isWeekday) {
    // ny 是「墙钟平移」的 Date（本地时区字段=纽约时间），真实时刻偏移 = nowReal - ny；
    // 事件时间需把墙钟目标时刻加回该偏移，否则倒计时差一个时区。
    const tzShift = Date.now() - ny.getTime();
    if (mins < 570) {
      const at = new Date(ny); at.setHours(9, 30, 0, 0);
      nextEvent = { kind: 'open', at: new Date(at.getTime() + tzShift).toISOString() };
    } else if (mins < 960) {
      const at = new Date(ny); at.setHours(16, 0, 0, 0);
      nextEvent = { kind: 'close', at: new Date(at.getTime() + tzShift).toISOString() };
    }
  }
  return { session, label, nyTime: ny.toISOString(), nextEvent };
}

/* ---------------- 自选股（12 只） ---------------- */
export const WATCHLIST_TICKERS = ['NVDA', 'TSLA', 'AAPL', 'AMD', 'MSFT', 'META', 'AMZN', 'AVGO', 'SMCI', 'PLTR', 'COIN', 'QQQ'];

interface WatchState extends QuoteState { item: WatchlistItem }
const watchState = new Map<string, WatchState>();

function makeSparkline(r: Rng, changePct: number, points = 24): number[] {
  // 末段方向与涨跌符号一致（§11 真实性约束）
  const out: number[] = [];
  let v = 100;
  for (let i = 0; i < points; i++) {
    const t = i / (points - 1);
    const trend = changePct * 14 * t; // 整体朝涨跌方向
    v += r.float(-1.6, 1.6) + (changePct >= 0 ? 0.22 : -0.22) + trend * 0.06;
    out.push(round2(v));
  }
  return out;
}

function makeSignals(r: Rng): Signal[] {
  const n = r.int(0, 3);
  const types = [...SIGNAL_TYPES].sort(() => r.float() - 0.5).slice(0, n);
  return types.map((type) => ({
    type,
    label: SIGNAL_LABELS[type],
    at: new Date(Date.now() - r.int(12, 560) * 60_000).toISOString(),
  }));
}

WATCHLIST_TICKERS.forEach((t, i) => {
  const r = new Rng(7000 + i * 131);
  const info = infoOf(t);
  const prevClose = info.base;
  const changePct = round2(r.float(-3.2, 4.1));
  const price = round2(prevClose * (1 + changePct / 100));
  const strengthScore = Math.round(r.normal(64, 15, 35, 95));
  const item: WatchlistItem = {
    ticker: info.ticker,
    name: info.name,
    sector: info.sector,
    price,
    change: round2(price - prevClose),
    changePct,
    sparkline: makeSparkline(r, changePct),
    strengthScore,
    signals: makeSignals(r),
    updatedAt: new Date().toISOString(),
  };
  watchState.set(t, { price, prevClose, item });
});

export function getWatchlist(force = false): WatchlistItem[] {
  watchState.forEach((s, t) => {
    jitter(s, 0.0014);
    const it = s.item;
    it.price = s.price;
    it.change = round2(s.price - s.prevClose);
    it.changePct = round2((s.price / s.prevClose - 1) * 100);
    it.updatedAt = new Date().toISOString();
    if (force) {
      // 强制刷新：轻微重采样信号时间戳
      it.signals = it.signals.map((sg, k) => ({ ...sg, at: new Date(Date.now() - (k + 2) * 47 * 60_000).toISOString() }));
    }
    void t;
  });
  return [...watchState.values()].map((s) => ({ ...s.item }));
}

export function getWatchlistAggregates(): { advancers: number; decliners: number } {
  let advancers = 0, decliners = 0;
  watchState.forEach((s) => (s.item.changePct >= 0 ? advancers++ : decliners++));
  return { advancers, decliners };
}

/* ---------------- 市场信号 / 强度 ---------------- */
export function getMarketSignals(): MarketSignalsSummary {
  const r = new Rng(88123);
  const byType = SIGNAL_TYPES.map((type) => {
    const today = r.int(1, 7);
    return { type, label: SIGNAL_LABELS[type], today, avg7d: round2(Math.max(0.6, today + r.float(-2.4, 2.4))) };
  });
  const totalToday = byType.reduce((s, x) => s + x.today, 0);
  return { totalToday, deltaVsYesterday: r.int(-4, 5), byType };
}

export function getMarketStrength(): MarketStrength {
  const r = new Rng(4451);
  // 10 桶近似正态（峰值 60–70）
  const histogram = Array.from({ length: 10 }, (_, b) => {
    const center = 6.2;
    const d = (b - center) / 2.1;
    return Math.max(1, Math.round(46 * Math.exp(-d * d) + r.float(-3, 3)));
  });
  const total = histogram.reduce((s, n) => s + n, 0);
  const ge85Count = histogram[8] + histogram[9];
  const avgScore = round2(histogram.reduce((s, n, b) => s + n * (b * 10 + 5), 0) / total);
  // aggregateAvailable：Market.tsx B6 用严格 === true 判定；mock 不产出该字段
  // 会让强度分布整块在演示模式永不渲染（真数据反而有）。
  return { avgScore, ge85Count, histogram, aggregateAvailable: true };
}

export function getStrengthProfiles(): StrengthProfile[] {
  return [
    { id: 'balanced', name: __t('均衡动量'), description: __t('趋势/动量/量能/波动均衡加权，适合大多数市况。'), weights: { trend: 30, momentum: 30, volume: 20, volatility: 20 } },
    { id: 'breakout', name: __t('突破猎手'), description: __t('加重动量与量能，捕捉放量突破早期的标的。'), weights: { trend: 20, momentum: 40, volume: 30, volatility: 10 } },
    { id: 'lowvol', name: __t('低波稳健'), description: __t('偏好低波动与趋势延续，回撤优先。'), weights: { trend: 40, momentum: 15, volume: 10, volatility: 35 } },
  ];
}

/* ---------------- 选股扫描（48 只） ---------------- */
export function runStrengthScan(): ScreenerRow[] {
  const rows = TICKER_POOL.map((info, i) => {
    const r = new Rng(90210 + i * 37);
    const changePct = round2(r.float(-4.2, 5.2));
    const strengthScore = Math.round(r.normal(62, 17, 18, 97));
    const band: ScreenerRow['band'] = strengthScore >= 70 ? 'strong' : strengthScore >= 45 ? 'mid' : 'weak';
    const row: ScreenerRow = {
      ticker: info.ticker,
      name: info.name,
      sector: info.sector,
      price: round2(info.base * (1 + changePct / 100)),
      changePct,
      strengthScore,
      band,
      subscores: {
        trend: Math.round(r.normal(62, 16, 15, 98)),
        momentum: Math.round(r.normal(60, 18, 12, 98)),
        volume: Math.round(r.normal(58, 20, 10, 99)),
        volatility: Math.round(r.normal(55, 16, 10, 95)),
      },
      sparkline: makeSparkline(r, changePct, 24),
      /* 宏观适配：合成值，与任何第三方报告的示例分数无关。刻意让每 7 只里有 1 只
         没有读数，这样「没读到」这条路径在 mock 模式下也看得见 —— 它和「中性」
         显示得完全不同，这正是需要被看到的差别。 */
      ...(i % 7 === 3
        ? { macroFit: null, macroTailwind: null, macroFitConfidence: null }
        : (() => {
            const fit = round2(r.normal(52, 19, 8, 94));
            return {
              macroFit: fit,
              macroTailwind: fit >= 65 ? '顺风' : fit <= 35 ? '逆风' : '中性',
              macroFitConfidence: round2(r.float(0.62, 1)),
              macroSupporting: [
                { factor_id: 'fed_net_liquidity', label: '联储净流动性' },
                { factor_id: 'risk_vs_safe', label: __t('风险资产相对避险') },
              ],
              macroOpposing: [{ factor_id: 'real_rate_level', label: '实际利率水平' }],
              macroTechnicalGap: round2(r.float(-28, 28)),
            };
          })()),
    };
    return row;
  });
  return rows.sort((a, b) => b.strengthScore - a.strengthScore);
}

/* ---------------- 股票搜索 / 详情 / 图表 ---------------- */
export function searchStocks(q: string): StockSearchResult[] {
  const s = q.trim().toLowerCase();
  if (!s) return [];
  return TICKER_POOL.filter(
    (x) => x.ticker.toLowerCase().includes(s) || x.name.toLowerCase().includes(s),
  )
    .slice(0, 8)
    .map((x) => ({ ticker: x.ticker, name: x.name, sector: x.sector }));
}

export function getStockDetail(ticker: string): StockDetail {
  const i = TICKER_POOL.findIndex((x) => x.ticker === ticker.toUpperCase());
  const info = infoOf(ticker.toUpperCase());
  const r = new Rng(31000 + Math.max(i, 0) * 977);
  const ws = watchState.get(info.ticker);
  const base = ws?.item ?? (() => {
    const changePct = round2(r.float(-3, 3.6));
    return {
      ticker: info.ticker, name: info.name, sector: info.sector,
      price: round2(info.base * (1 + changePct / 100)),
      change: round2(info.base * changePct / 100), changePct,
      sparkline: makeSparkline(r, changePct), strengthScore: Math.round(r.normal(62, 15, 30, 96)),
      signals: makeSignals(r), updatedAt: new Date().toISOString(),
    } satisfies WatchlistItem;
  })();
  const dayRange = r.float(0.008, 0.03);
  return {
    ...base,
    open: round2(info.base * (1 + r.float(-0.006, 0.006))),
    high: round2(Math.max(base.price, info.base) * (1 + dayRange * r.float(0.3, 1))),
    low: round2(Math.min(base.price, info.base) * (1 - dayRange * r.float(0.3, 1))),
    prevClose: info.base,
    volume: Math.round(r.float(8, 96) * 1e6),
    avgVolume: Math.round(r.float(10, 80) * 1e6),
    marketCap: Math.round(info.base * r.float(0.4, 32) * 1e8),
    pe: r.chance(0.85) ? round2(r.float(12, 68)) : null,
    ivPercentile: Math.round(r.float(20, 80)),
    range52w: [round2(info.base * r.float(0.45, 0.8)), round2(info.base * r.float(1.15, 2.1))],
  };
}

function buildCandles(ticker: string, range: StockChart['range']): { candles: Candle[]; ma20: (number | null)[] } {
  const i = Math.max(0, TICKER_POOL.findIndex((x) => x.ticker === ticker.toUpperCase()));
  const r = new Rng(55000 + i * 613 + range.length * 17);
  const info = infoOf(ticker.toUpperCase());
  let points: number;
  let stepMs: number;
  switch (range) {
    case '5m': points = 5 * 78; stepMs = 5 * 60_000; break;
    case '15m': points = 22 * 26; stepMs = 15 * 60_000; break;
    case '1h': points = 90 * 7; stepMs = 60 * 60_000; break;
    case '1d': points = 500; stepMs = 86_400_000; break;
    case '1w': points = 260; stepMs = 7 * 86_400_000; break;
  }
  const daily = stepMs >= 86_400_000;
  const vol = daily ? 0.022 : 0.0022;
  let price = info.base * (1 + r.float(-0.06, 0.02));
  const start = Date.now() - points * stepMs;
  const candles: Candle[] = [];
  for (let k = 0; k < points; k++) {
    const drift = r.float(-vol, vol) + Math.sin(k / 17) * vol * 0.18;
    const o = price;
    const c = o * (1 + drift);
    const h = Math.max(o, c) * (1 + r.float(0, vol * 0.7));
    const l = Math.min(o, c) * (1 - r.float(0, vol * 0.7));
    const v = Math.round(r.float(0.4, 2.2) * 1e6 * (daily ? 18 : 1));
    candles.push({ t: new Date(start + k * stepMs).toISOString(), o: round2(o), h: round2(h), l: round2(l), c: round2(c), v });
    price = c;
  }
  const ma20: (number | null)[] = candles.map((_, k) => {
    if (k < 19) return null;
    let s = 0;
    for (let j = k - 19; j <= k; j++) s += candles[j].c;
    return round2(s / 20);
  });
  return { candles, ma20 };
}

export function getStockChart(ticker: string, range: StockChart['range']): StockChart {
  const { candles, ma20 } = buildCandles(ticker.toUpperCase(), range);
  return { ticker: ticker.toUpperCase(), range, candles, ma20 };
}

export function getStockSignals(ticker: string): Signal[] {
  const i = Math.max(0, TICKER_POOL.findIndex((x) => x.ticker === ticker.toUpperCase()));
  return makeSignals(new Rng(61200 + i * 41));
}

/* ---------------- 详情抽屉扩展（stock-detail 代理；契约对齐 §stocks/signals） ---------------- */

/** 代码池内是否存在该标的（mock 下用于 404 判定） */
export function hasTicker(ticker: string): boolean {
  return TICKER_POOL.some((x) => x.ticker === ticker.toUpperCase());
}

/** ChartBar{t,o,h,l,c,v,quote_only} + as_of/_stale（契约 §stocks chart） */
export interface ChartBarEx extends Candle {
  quote_only?: boolean;
}
export interface StockChartEx {
  ticker: string;
  range: StockChart['range'];
  bars: ChartBarEx[];
  ma20: (number | null)[];
  as_of: string;
  /** 契约 last_bar_at：末根 K 线自身的时间（as_of 只是接口读取时刻） */
  last_bar_at?: string | null;
  _stale?: boolean;
  chart_analysis?: Record<string, unknown> | null;
}

export function getStockChartEx(ticker: string, range: StockChart['range']): StockChartEx {
  const t = ticker.toUpperCase();
  const { candles, ma20 } = buildCandles(t, range);
  const bars: ChartBarEx[] = candles.map((c) => ({ ...c }));
  // 盘中末根为「仅报价」实时 bar（quote_only）
  const intraday = range === '5m' || range === '15m' || range === '1h';
  if (intraday && bars.length > 0) {
    bars[bars.length - 1].quote_only = true;
  }
  return {
    ticker: t,
    range,
    bars,
    ma20,
    as_of: new Date(Date.now() - 15 * 60_000).toISOString(), // 延迟 15 分钟
    ...(t === 'SMCI' ? { _stale: true } : {}), // 演示 _stale 横幅
    // 与线上一致：分钟图的分析包挂在 /chart 上，日线的挂在 /technical 上
    ...(intraday ? { chart_analysis: buildChartAnalysis(t, bars, range) } : {}),
  };
}

/* ---------------- 分析图层包（chart_analysis）---------------- */

function smaSeries(values: number[], window: number): (number | null)[] {
  return values.map((_, index) => {
    if (index + 1 < window) return null;
    let sum = 0;
    for (let k = index + 1 - window; k <= index; k += 1) sum += values[k];
    return round2(sum / window);
  });
}

function rsiSeries(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = closes.map(() => null);
  if (closes.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let k = 1; k <= period; k += 1) {
    const delta = closes[k] - closes[k - 1];
    if (delta > 0) gain += delta; else loss -= delta;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  out[period] = round2(avgLoss <= 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  for (let k = period + 1; k < closes.length; k += 1) {
    const delta = closes[k] - closes[k - 1];
    avgGain = (avgGain * (period - 1) + Math.max(delta, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-delta, 0)) / period;
    out[k] = round2(avgLoss <= 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  }
  return out;
}

function emaSeries(values: number[], span: number): number[] {
  const k = 2 / (span + 1);
  let prev = values[0] ?? 0;
  return values.map((value, index) => {
    if (index === 0) return prev;
    prev = value * k + prev * (1 - k);
    return prev;
  });
}

/** 只有真正被图层菜单勾得到的副图才发；id 必须和 analysis/registry.ts 对得上。 */
function analysisPanes(bars: ChartBarEx[]): Record<string, unknown>[] {
  const closes = bars.map((bar) => bar.c);
  const fast = emaSeries(closes, 12);
  const slow = emaSeries(closes, 26);
  const macd = fast.map((value, index) => round4(value - slow[index]));
  const signal = emaSeries(macd, 9).map(round4);
  let running = 0;
  const obv: number[] = [];
  const clv: (number | null)[] = [];
  const position: (number | null)[] = [];
  bars.forEach((bar, index) => {
    if (index > 0) running += bar.c > closes[index - 1] ? bar.v : (bar.c < closes[index - 1] ? -bar.v : 0);
    obv.push(running);
    const span = bar.h - bar.l;
    clv.push(span > 0 ? round4((2 * bar.c - bar.h - bar.l) / span) : null);
    const window = bars.slice(Math.max(0, index - 59), index + 1);
    const hi = Math.max(...window.map((item) => item.h));
    const lo = Math.min(...window.map((item) => item.l));
    position.push(hi > lo ? round4((bar.c - lo) / (hi - lo)) : null);
  });
  return [
    { id: 'rsi', label: 'RSI', kind: 'rsi', startIndex: 0, values: { rsi: rsiSeries(closes) } },
    { id: 'macd', label: 'MACD', kind: 'macd', startIndex: 0, values: { macd, signal, histogram: macd.map((value, index) => round4(value - signal[index])) } },
    { id: 'obv', label: 'OBV', kind: 'obv', startIndex: 0, values: { obv } },
    { id: 'clv', label: 'CLV', kind: 'clv', startIndex: 0, values: { clv } },
    { id: 'range_persistence', label: 'Range Persistence', kind: 'range', startIndex: 0, values: { position } },
    { id: 'spy_rs', label: 'SPY Relative Strength', kind: 'rs', startIndex: 0, values: { rs: closes.map((c, index) => round4(c / (closes[0] || 1) / (1 + index * 0.0004))) } },
  ];
}

/**
 * mock 的 chart_analysis：形状与 /technical 的真包一致，本地静态预览才画得出
 * overlay/副图/形态标签，fixture 与接口的形状漂移也才有人发现。
 *
 * 分析窗口故意只覆盖尾部 120 根（线上 series_break_at 就是这个形态）：
 * 副图与 MA 必须按日期对齐，否则最近的动量会画到几年前的蜡烛底下。
 */
export function buildChartAnalysis(
  ticker: string,
  bars: ChartBarEx[],
  range: StockChart['range'],
): Record<string, unknown> | null {
  const closed = closedBarsForFingerprint(bars, range);
  const intraday = range === '5m' || range === '15m' || range === '1h';
  const window = intraday ? closed : closed.slice(-120);
  if (window.length < 30) return null;
  const dates = window.map((bar) => barStampForRange(bar.t, range));
  const closes = window.map((bar) => bar.c);
  const keyOf = (index: number) => ({
    time: window[index].t,
    barKey: range === '1d' || range === '1w' ? dates[index] : window[index].t,
    price: 0,
  });
  const anchor = (index: number, price: number) => ({ ...keyOf(index), price: round2(price) });
  const dataThrough = dates[dates.length - 1];
  const last = closes[closes.length - 1];
  const hi = Math.max(...window.map((bar) => bar.h));
  const lo = Math.min(...window.map((bar) => bar.l));
  const evidence = (quality: number, touches?: number) => ({
    shapeQuality: quality,
    volumeConfirmation: 0.6,
    trendAlignment: 0.55,
    recency: 0.8,
    consensus: 1,
    sources: ['fixtures'],
    ...(touches === undefined ? {} : { touches }),
  });
  const overlay = (row: Record<string, unknown>) => ({
    sourceId: 'fixtures',
    algorithmVersion: 'fixtures-v1',
    status: 'forming',
    direction: 'neutral',
    displayPriority: 0.4,
    formationStart: dates[0],
    formationEnd: dataThrough,
    dataThrough,
    detail: 'shapeQuality is geometry, not a probability',
    ...row,
  });
  const overlays: Record<string, unknown>[] = [];
  if (intraday) {
    let dollar = 0;
    let volume = 0;
    const vwap = window.map((bar) => {
      dollar += ((bar.h + bar.l + bar.c) / 3) * bar.v;
      volume += bar.v;
      return volume > 0 ? round2(dollar / volume) : null;
    });
    overlays.push(overlay({
      id: 'vwap',
      group: 'price',
      kind: 'vwap',
      label: 'VWAP',
      shapeQuality: 1,
      evidence: evidence(1),
      geometry: { type: 'series', values: vwap, startIndex: 0, styleHint: 'auto-pale' },
    }));
  } else {
    for (const [span, id] of [[20, 'ma20'], [50, 'ma50'], [200, 'ma200']] as const) {
      overlays.push(overlay({
        id,
        group: 'price',
        kind: 'ma',
        label: `MA${span}`,
        shapeQuality: 1,
        displayPriority: 0.2,
        evidence: evidence(1),
        // 只发 values + offset：dates 整包共用一份，overlay 不再各带一份
        geometry: { type: 'series', window: span, values: smaSeries(closes, span), startIndex: 0, styleHint: 'auto-pale' },
      }));
    }
    const swingHigh = closes.indexOf(Math.max(...closes.slice(0, -5)));
    const swingLow = closes.indexOf(Math.min(...closes.slice(0, -5)));
    overlays.push(overlay({
      id: `swing-h:${dates[swingHigh]}`,
      group: 'price',
      kind: 'swing',
      label: 'HH',
      shapeQuality: 0.6,
      direction: 'bearish',
      evidence: evidence(0.6),
      geometry: { type: 'point', role: 'high', anchors: [anchor(swingHigh, window[swingHigh].h)], styleHint: 'auto-pale' },
    }));
    overlays.push(overlay({
      id: `swing-l:${dates[swingLow]}`,
      group: 'price',
      kind: 'swing',
      label: 'HL',
      shapeQuality: 0.6,
      direction: 'bullish',
      evidence: evidence(0.6),
      geometry: { type: 'point', role: 'low', anchors: [anchor(swingLow, window[swingLow].l)], styleHint: 'auto-pale' },
    }));
    overlays.push(overlay({
      id: 'sr:resistance',
      group: 'price',
      kind: 'level',
      label: '最近阻力',
      shapeQuality: 0.55,
      direction: 'bearish',
      evidence: evidence(0.55),
      geometry: { type: 'level', price: round2(hi * 0.995), role: 'resistance', styleHint: 'auto-pale' },
    }));
    overlays.push(overlay({
      id: 'sr:support',
      group: 'price',
      kind: 'level',
      label: '最近支撑',
      shapeQuality: 0.55,
      direction: 'bullish',
      evidence: evidence(0.55),
      geometry: { type: 'level', price: round2(lo * 1.005), role: 'support', styleHint: 'auto-pale' },
    }));
    const baseStart = Math.max(0, window.length - 60);
    const baseEnd = window.length - 6;
    overlays.push(overlay({
      id: 'base:fixture',
      group: 'price',
      kind: 'box',
      label: '整理区',
      shapeQuality: 0.68,
      formationStart: dates[baseStart],
      formationEnd: dates[baseEnd],
      evidence: evidence(0.68),
      geometry: {
        type: 'band',
        resistanceHigh: round2(hi * 0.99),
        resistanceLow: round2(hi * 0.965),
        supportLow: round2(lo * 1.01),
        supportHigh: round2(lo * 1.035),
        pivot: round2(hi * 0.9775),
        invalidation: round2(lo * 0.99),
        styleHint: 'auto-pale',
      },
    }));
    overlays.push(overlay({
      id: 'pivot:fixture',
      group: 'price',
      kind: 'pivot',
      label: 'pivot/invalidation',
      shapeQuality: 0.68,
      evidence: evidence(0.68),
      geometry: { type: 'levels', pivot: round2(hi * 0.9775), invalidation: round2(lo * 0.99), styleHint: 'auto-pale' },
    }));
    const railStart = Math.max(0, window.length - 70);
    const railMid = Math.max(0, window.length - 40);
    const railEnd = window.length - 3;
    overlays.push(overlay({
      id: 'auto:support_trend:fixture',
      group: 'price',
      kind: 'support_trend',
      label: 'support_trend',
      status: 'testing',
      direction: 'bullish',
      shapeQuality: 0.74,
      displayPriority: 0.82,
      formationStart: dates[railStart],
      evidence: evidence(0.74, 3),
      geometry: {
        type: 'rails',
        subtype: 'rising',
        fitAnchors: [
          anchor(railStart, window[railStart].l),
          anchor(railMid, window[railMid].l * 1.01),
          anchor(railEnd, window[railEnd].l * 1.02),
        ],
        touchAnchors: [anchor(railStart, window[railStart].l), anchor(railMid, window[railMid].l * 1.01)],
        styleHint: 'auto-pale',
      },
    }));
    overlays.push(overlay({
      id: 'breakout:fixture',
      group: 'event',
      kind: 'breakout',
      label: 'breakout:testing',
      status: 'testing',
      shapeQuality: 0.6,
      displayPriority: 0.5,
      evidence: evidence(0.6),
      geometry: { type: 'levels', pivot: round2(hi * 0.9775), invalidation: round2(lo * 0.99), styleHint: 'auto-pale' },
    }));
  }
  const score = (seed: number) => round2(40 + ((seed * 37) % 55));
  return {
    version: 'optix-chart-analysis-v1',
    ticker,
    range,
    adjustment: 'raw',
    dataThrough,
    fingerprintAlgorithm: FINGERPRINT_ALGORITHM,
    barFingerprint: barFingerprint(window),
    barCount: window.length,
    lastClose: round2(last),
    // 整包只发一份 dates；overlay/pane 用 offset 索引进来
    dates,
    firstBarDate: dates[0],
    lastBarDate: dataThrough,
    overlays,
    indicatorPanes: intraday ? [] : analysisPanes(window),
    strengthContext: intraday ? null : {
      snapshotDate: dataThrough,
      note: 'Family scores are context, not a win probability, and never enter shapeQuality.',
      finalScore: null,
      globalPercentile: null,
      sectorPercentile: null,
      families: {
        short: { id: 'short', score: score(ticker.length + 1), activeWeights: {}, contributions: {} },
        mid: { id: 'mid', score: score(ticker.length + 2), activeWeights: {}, contributions: {} },
        long: { id: 'long', score: score(ticker.length + 3), activeWeights: {}, contributions: {} },
        trend: { id: 'trend', score: score(ticker.length + 4), activeWeights: {}, contributions: {} },
        breakout: { id: 'breakout', score: score(ticker.length + 5), activeWeights: {}, contributions: {} },
        price_action: { id: 'price_action', score: score(ticker.length + 6), activeWeights: {}, contributions: {} },
      },
    },
  };
}

/** GET /api/signals/stock/{t} 完整形状：trend_bias_* + 分项 + 因子解读 */
export type TrendBiasStatus = 'ok' | 'degraded' | 'insufficient_data';
export interface TrendBiasFactor {
  key: 'trend' | 'momentum' | 'volume' | 'volatility';
  label: string;
  reading: string;
  tone: 'bullish' | 'neutral' | 'bearish';
}
export interface StockTrendBias {
  ticker: string;
  trend_bias_score: number;
  trend_bias_label: '偏多' | '中性' | '偏空';
  trend_bias_status: TrendBiasStatus;
  scores: { trend: number; momentum: number; volume: number; volatility: number };
  factors: TrendBiasFactor[];
  as_of: string;
}

export function getStockTrendBias(ticker: string): StockTrendBias {
  const t = ticker.toUpperCase();
  const i = Math.max(0, TICKER_POOL.findIndex((x) => x.ticker === t));
  const r = new Rng(93100 + i * 733);
  const ws = watchState.get(t);
  const base = ws?.item.strengthScore ?? Math.round(r.normal(58, 18, 12, 96));
  const score = Math.max(2, Math.min(98, Math.round(base + r.float(-6, 6))));
  const label: StockTrendBias['trend_bias_label'] = score >= 60 ? '偏多' : score <= 40 ? '偏空' : '中性';
  const status: TrendBiasStatus = !hasTicker(t)
    ? 'insufficient_data'
    : i % 7 === 3
      ? 'degraded'
      : 'ok';
  const scores = {
    trend: Math.max(4, Math.min(98, Math.round(score + r.float(-14, 12)))),
    momentum: Math.max(4, Math.min(98, Math.round(score + r.float(-16, 14)))),
    volume: Math.max(4, Math.min(98, Math.round(score + r.float(-18, 16)))),
    volatility: Math.max(4, Math.min(98, Math.round(r.normal(56, 17, 10, 96)))),
  };
  const toneOf = (v: number): TrendBiasFactor['tone'] => (v >= 60 ? 'bullish' : v <= 40 ? 'bearish' : 'neutral');
  const factors: TrendBiasFactor[] = [
    {
      key: 'trend',
      label: __t('趋势结构'),
      tone: toneOf(scores.trend),
      reading:
        scores.trend >= 60
          ? __t('价格站上 MA20，短均线呈多头排列，趋势分偏强')
          : scores.trend <= 40
            ? __t('价格跌破 MA20，均线拐头向下，趋势分偏弱')
            : __t('价格围绕 MA20 反复，趋势方向尚未确认'),
    },
    {
      key: 'momentum',
      label: '动量',
      tone: toneOf(scores.momentum),
      reading:
        scores.momentum >= 60
          ? __t('动量读数上行，近端斜率加速，追涨情绪占优')
          : scores.momentum <= 40
            ? __t('动量读数回落，近端斜率走弱，注意回撤风险')
            : __t('动量读数走平，多空力量暂时均衡'),
    },
    {
      key: 'volume',
      label: '量能',
      tone: toneOf(scores.volume),
      reading:
        scores.volume >= 60
          ? __t('成交较 20 日均量明显放大，量能配合价格方向')
          : scores.volume <= 40
            ? __t('成交较 20 日均量萎缩，方向缺乏量能确认')
            : __t('量能维持在常态区间，未见异常放量'),
    },
    {
      key: 'volatility',
      label: __t('波动定价'),
      tone: toneOf(scores.volatility),
      reading:
        scores.volatility >= 60
          ? __t('IV 百分位偏高，期权定价隐含较大波动预期')
          : scores.volatility <= 40
            ? __t('IV 百分位偏低，期权定价相对便宜')
            : __t('IV 百分位处于中位，波动定价中性'),
    },
  ];
  return {
    ticker: t,
    trend_bias_score: score,
    trend_bias_label: label,
    trend_bias_status: status,
    scores,
    factors,
    as_of: new Date(Date.now() - 15 * 60_000).toISOString(),
  };
}
