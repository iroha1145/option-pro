/** API 契约类型（与后端 REST 形状 1:1 对齐，info.md §3） */

/* ---------- 市场 ---------- */
export interface IndexQuote {
  code: string;        // SPX / NDX / DJI / RUT / VIX
  name: string;
  price: number;
  change: number;
  changePct: number;
}

export type MarketSession = 'premarket' | 'regular' | 'afterhours' | 'closed';

export interface MarketStatus {
  session: MarketSession;
  label: string;          // 盘前 / 盘中 / 盘后 / 休市
  nyTime: string;         // ISO
  nextEvent: { kind: 'open' | 'close'; at: string } | null;
}

/* ---------- 访问 ---------- */
export type AccessRole = 'visitor' | 'owner';
export interface AccessStatus {
  role: AccessRole;
  aiEnabled: boolean;
}

/* ---------- 信号 / 强度 ---------- */
export type SignalType = 'breakout' | 'volume' | 'pullback' | 'ma-touch' | 'gap' | 'iv-spike';
export interface Signal {
  type: SignalType;
  label: string;          // 突破 / 放量 / 回踩 …
  at: string;             // ISO
}

export interface WatchlistItem {
  ticker: string;
  name: string;
  sector: string;
  price: number;
  change: number;
  changePct: number;
  sparkline: number[];    // 24 点分时
  strengthScore: number;  // 0–100
  signals: Signal[];
  updatedAt: string;
}

export interface MarketSignalsSummary {
  totalToday: number;
  deltaVsYesterday: number;
  byType: { type: SignalType; label: string; today: number; avg7d: number }[];
}

export interface MarketStrength {
  avgScore: number;
  ge85Count: number;
  histogram: number[];    // 10 桶（0-9 … 90-100）
}

export type StrengthBand = 'strong' | 'mid' | 'weak';
export interface ScreenerRow {
  ticker: string;
  name: string;
  sector: string;
  price: number;
  changePct: number;
  strengthScore: number;
  band: StrengthBand;
  subscores: { trend: number; momentum: number; volume: number; volatility: number };
  sparkline: number[];
}

export interface StrengthProfile {
  id: string;
  name: string;
  description: string;
  weights: { trend: number; momentum: number; volume: number; volatility: number };
}

/* ---------- 股票 ---------- */
export interface StockSearchResult {
  ticker: string;
  name: string;
  sector: string;
}

export interface StockDetail extends WatchlistItem {
  open: number;
  high: number;
  low: number;
  prevClose: number;
  volume: number;
  avgVolume: number;
  marketCap: number;
  pe: number | null;
  ivPercentile: number;   // 0–100
  range52w: [number, number];
}

export interface Candle {
  t: string; // ISO
  o: number; h: number; l: number; c: number; v: number;
}
export interface StockChart {
  ticker: string;
  range: '1D' | '5D' | '1M' | '6M' | '1Y' | 'ALL';
  candles: Candle[];
  ma20: (number | null)[];
}

/* ---------- 突破雷达 ---------- */
export interface BreakoutSignal {
  id: string;
  ticker: string;
  name: string;
  type: SignalType;
  label: string;
  price: number;
  changePct: number;
  strengthScore: number;
  at: string;
  summary: string;
}

export interface BreakoutStatus {
  state: 'scanning' | 'cooldown' | 'idle';
  scanned: number;
  universe: number;
  cooldownEndsAt: string | null;
  lastScanAt: string;
}

export interface BreakoutEvent {
  id: string;
  ticker: string;
  type: SignalType;
  label: string;
  at: string;
  price: number;
  result: 'hit' | 'failed' | 'pending';
}
export interface BreakoutEventDetail extends BreakoutEvent {
  triggerPrice: number;
  targetPrice: number;
  invalidPrice: number;
  evidence: string[];
}

/* ---------- 板块 ---------- */
export interface Sector {
  id: string;
  name: string;
  changePct: number;
  strengthScore: number;
  constituents: { ticker: string; name: string; changePct: number }[];
}
export interface IvRankRow {
  ticker: string;
  name: string;
  ivPercentile: number;
  iv: number;
  changePct: number;
}

/* ---------- 财报 ---------- */
export interface EarningsItem {
  ticker: string;
  name: string;
  date: string;           // ISO date
  timing: 'bmo' | 'amc';  // 盘前 / 盘后
  epsEstimate: number | null;
  epsActual: number | null;
  revEstimate: number | null;  // 百万美元
  revActual: number | null;
}

export interface EarningsImpact {
  ticker: string;
  expectedMovePct: number;
  sentiment: 'bullish' | 'neutral' | 'bearish';
  ivRank: number;
  related: { ticker: string; name: string; relation: string; changePct: number }[];
  summary: string;
}

/* ---------- 催化剂 ---------- */
export type NewsSentiment = 'positive' | 'neutral' | 'negative';
export interface NewsItem {
  id: string;
  title: string;
  summary: string;
  source: string;
  publishedAt: string;
  sentiment: NewsSentiment;
  tickers: string[];
  heat: number; // 0–100
}
export interface Hotspot {
  id: string;
  theme: string;
  heat: number;
  newsCount: number;
  representative: string; // 代表新闻标题
  tickers: string[];
}
export interface FocusCycle {
  id: string;
  theme: string;
  startedAt: string;
  days: number;
  stage: '发酵' | '主升' | '退潮';
  summary: string;
}
export interface CatalystsStatus {
  newsToday: number;
  hotspotsActive: number;
  lastCrawlAt: string;
}

/* ---------- 期权 ---------- */
export interface UnusualOption {
  id: string;
  ticker: string;
  side: 'call' | 'put';
  strike: number;
  expiration: string;
  volume: number;
  openInterest: number;
  premium: number;      // 万美元
  sentiment: NewsSentiment;
  at: string;
}
export interface OptionChainRow {
  strike: number;
  callOi: number; callVol: number; callIv: number; callBid: number; callAsk: number;
  putOi: number; putVol: number; putIv: number; putBid: number; putAsk: number;
}
export interface OptionChain {
  ticker: string;
  expiration: string;
  spot: number;
  rows: OptionChainRow[];
}

/* ---------- AI 任务 ---------- */
/**
 * 归一化后的任务状态（live 由 ai-jobs 模块从契约 AIJobPublic 归一）：
 * - 活跃：queued（preparing/pending/queued）· in_progress（processing/running/cancel_requested）
 * - 终态：succeeded（completed）· failed（failed 及其他失败类终态）· cancelled（canceled→cancelled）
 * 活跃/终态集合见 api-contract §0.4。
 */
export type AiJobStatus = 'queued' | 'in_progress' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface AiJob {
  id: string;
  /** live 由契约 job_type 归一：earnings_impact/option_alerts/news_impact/signal_analysis/market_focus */
  kind: 'earnings-impact' | 'option-alerts' | 'news-analysis' | 'signal-analysis' | 'market-focus';
  status: AiJobStatus;
  progress: number; // 0–100
  createdAt: string;
  updatedAt: string;
  result?: string;
  error?: string;
}

/* ---------- 运行时 ---------- */
export interface RuntimeSettings {
  aiEnabled: boolean;
  scanIntervalMin: number;
  watchlistRefreshSec: number;
  optionsUnusualEnabled: boolean;
}
export interface RuntimeHistoryEntry {
  id: string;
  at: string;
  actor: string;
  change: string;
}
export interface WorkerTask {
  id: string;
  name: string;
  status: 'ok' | 'degraded' | 'down';
  lastBeatAt: string;
  note: string;
}
