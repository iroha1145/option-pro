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

/** 契约 market_regime 六维分（/strength/market · /strength/scan 同源；缺失如实为 null） */
export interface MarketRegimeDims {
  indexTrend: number | null;      // index_trend_score
  momentum: number | null;        // market_momentum_score
  breadth: number | null;         // market_breadth_score
  volume: number | null;          // market_volume_score
  riskAppetite: number | null;    // risk_appetite_score
  riskOnSpread: number | null;    // risk_on_spread_score
}

export interface MarketRegimeInfo {
  score: number | null;           // 契约 score / partial_score
  label: string | null;           // 如「温和偏强」
  spreadLabel: string | null;     // risk_on_spread_label
  warnings: string[];             // 契约 warnings（如「IWM/SPY偏弱…」）
  dims: MarketRegimeDims;
  asOf: string | null;
}

export interface MarketStrength {
  avgScore: number;
  ge85Count: number;
  histogram: number[];    // 10 桶（0-9 … 90-100）；live 契约无直方图 → []（UI 隐藏参照）
  /** live 契约 market_regime 快照；mock 不填（UI 回退直方图推导） */
  regime?: MarketRegimeInfo;
}

export type StrengthBand = 'strong' | 'mid' | 'weak';

/** 扫描行分项（live 契约周期/质量分；value 缺失如实为 null，UI 显「—」） */
export interface ScreenerSubscoreDim {
  key: string;            // 契约字段名（score_short / score_mid / score_long / breakout_quality_score）
  label: string;          // 中文短标签（短期 / 中期 / 长期 / 突破质量）
  value: number | null;
}

export interface ScreenerRow {
  ticker: string;
  name: string;
  sector: string;
  /** live 契约 sector_id（板块过滤下发用）；mock 无 id，过滤回退 sector 名 */
  sectorId?: string;
  price: number;
  /** 契约 change_pct；缺失如实为 null（ChangeBadge 显「—」，不显 +0.00%） */
  changePct: number | null;
  strengthScore: number;
  /** 后端 avg_dollar_volume_20d；缺失时为 null，不用当日成交额冒充。 */
  avgDollarVolume20d?: number | null;
  band: StrengthBand;
  subscores: { trend: number; momentum: number; volume: number; volatility: number };
  /** live 契约分项（周期/质量分）：存在时 UI 优先消费；mock 不填，回退 subscores 四维 */
  subscoreDims?: ScreenerSubscoreDim[];
  sparkline: number[];
}

/** 契约 /strength/profiles 的板块字典（{id,name}，name 为中文） */
export interface SectorOption {
  id: string;
  name: string;
}

export interface StrengthProfile {
  id: string;
  name: string;
  description: string;
  /** mock 四因子权重；live 契约仅返回枚举字符串（无权重）→ 缺失时 UI 隐藏权重条 */
  weights?: { trend: number; momentum: number; volume: number; volatility: number };
}

/** /strength/profiles 全量元数据（profiles + 板块字典；mock 无板块字典 → []） */
export interface StrengthProfilesMeta {
  profiles: StrengthProfile[];
  sectors: SectorOption[];
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
  /**
   * live 快照口径：'strength-row' = 概览接口未覆盖（焦点池外），
   * 由 /strength/stocks/{t} 扫描行回退的基础行情（仅价/涨跌/市值等，其余如实留空）
   */
  snapshotScope?: 'full' | 'strength-row';
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
  /** 后端明确提供时为 0–100；未提供时为 null，前端不得自行推算。 */
  progress: number | null;
  createdAt: string;
  updatedAt: string;
  /** 按任务类型保留后端结构化结果；消费组件负责校验所属契约。 */
  result?: string | Record<string, unknown>;
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
