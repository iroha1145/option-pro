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

const rng = new Rng(20240521);

/* live 也要读的标签搬到了 @/lib/signalLabels：留在这里会把整个 fixture 模块钉在
   live 依赖图里。这里重新导出，mock 侧的既有引用不必改。 */
export { SIGNAL_LABELS, SIGNAL_TYPES } from '@/lib/signalLabels';
import { SIGNAL_LABELS, SIGNAL_TYPES } from '@/lib/signalLabels';

const infoOf = (t: string): TickerInfo => TICKER_POOL.find((x) => x.ticker === t) ?? TICKER_POOL[0];

/* ---------------- 指数 tape ---------------- */
const INDEX_BASE: { code: string; name: string; base: number }[] = [
  { code: 'SPX', name: __t('标普 500'), base: 5972.4 },
  { code: 'NDX', name: __t('纳指 100'), base: 21468.2 },
  { code: 'DJI', name: '道琼斯', base: 43828.1 },
  { code: 'RUT', name: __t('罗素 2000'), base: 2382.6 },
  { code: 'SOX', name: '费城半导体', base: 5124.7 },
  { code: 'VIX', name: __t('波动率指数'), base: 14.86 },
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
  return INDEX_BASE.map(({ code, name }) => {
    const s = indexState.get(code)!;
    jitter(s, code === 'VIX' ? 0.004 : 0.0009);
    const change = round2(s.price - s.prevClose);
    return { code, name, price: s.price, change, changePct: round4(change / s.prevClose) * 100 };
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
  _stale?: boolean;
}

export function getStockChartEx(ticker: string, range: StockChart['range']): StockChartEx {
  const t = ticker.toUpperCase();
  const { candles, ma20 } = buildCandles(t, range);
  const bars: ChartBarEx[] = candles.map((c) => ({ ...c }));
  // 盘中末根为「仅报价」实时 bar（quote_only）
  if ((range === '5m' || range === '15m' || range === '1h') && bars.length > 0) {
    bars[bars.length - 1].quote_only = true;
  }
  return {
    ticker: t,
    range,
    bars,
    ma20,
    as_of: new Date(Date.now() - 15 * 60_000).toISOString(), // 延迟 15 分钟
    ...(t === 'SMCI' ? { _stale: true } : {}), // 演示 _stale 横幅
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
