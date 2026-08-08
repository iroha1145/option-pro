/** API 契约类型（与后端 REST 形状 1:1 对齐，info.md §3） */
import type { MacroFitDriver } from '@/lib/macroFit';

/* ---------- 市场 ---------- */
export interface IndexQuote {
  code: string;        // SPX / NDX / DJI / RUT / VIX（显示用短码）
  symbol: string;      // ^GSPC / ^IXIC …（真实行情符号——详情页/接口只认这个）
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
  /** 模型分析是否由后台配置开启；不得由登录状态推导。 */
  aiEnabled: boolean;
  /** 当前是否可立即创建新分析任务（忙碌/额度/配置均由后台判定）。 */
  aiAvailable: boolean;
  aiReason: string | null;
  /**
   * 已登录客户的用户名；未登录或以 admin 身份登录时为 null。
   * 客户身份与管理员身份彼此独立，不能互相推导。
   */
  accountUsername: string | null;
}

/* ---------- 信号 / 强度 ---------- */
export type SignalType = 'breakout' | 'volume' | 'pullback' | 'ma-touch' | 'gap' | 'iv-spike';
export interface Signal {
  type: SignalType;
  label: string;          // 突破 / 放量 / 回踩 …（完整句，兼容旧 chip 用法）
  at: string;             // ISO
  /** live 契约的结构化投影（文字行排版用；mock 简签无这些字段） */
  name?: string;          // 指标名（已本地化）
  value?: number;         // 指标读数
  reading?: string;       // 顶部风险 / 底部修复（已本地化）
  score?: number;         // 方向分 0-100
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

/** 生产 /signals/market 的真实指标对象；不含接口未提供的“今日/昨日/7日”统计。 */
export interface MarketSignalsSnapshot {
  metrics: {
    key: string;
    label: string;
    value: number;
    topScore: number | null;
    bottomScore: number | null;
  }[];
  topScore: number | null;
  bottomScore: number | null;
  topLabel: string | null;
  bottomLabel: string | null;
  dataQuality: number | null;
  sourceStatus: string | null;
  asOf: string | null;
  cached: boolean;
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
  /** 仅当接口明确返回全市场聚合时为 true；market_regime 综合分不等于全市场均分。 */
  aggregateAvailable?: boolean;
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
  /**
   * 兼容槽位；分项缺失时为 null。补 0 会把「没有这一项数据」画成「该项 0 分」
   * ——在评分界面里这两件事读起来完全相反（审计 P2-15）。
   */
  subscores: {
    trend: number | null;
    momentum: number | null;
    volume: number | null;
    volatility: number | null;
  };
  /** live 契约分项（周期/质量分）：存在时 UI 优先消费；mock 不填，回退 subscores 四维 */
  subscoreDims?: ScreenerSubscoreDim[];
  sparkline: number[];
  /**
   * 宏观适配（影子字段，macro-linkage-v1）。
   *
   * 不参与排序、不改 strengthScore —— 后端 macro_linkage.affects_production_ranking
   * 为 false，这里的字段只是把同一个事实带到界面上。null 表示**没读到**，不是中性：
   * 覆盖度不足时后端返回 null 而不是 50，前端也不许兜成 50。
   */
  macroFit?: number | null;
  macroTailwind?: string | null;
  macroFitConfidence?: number | null;
  macroSupporting?: MacroFitDriver[];
  macroOpposing?: MacroFitDriver[];
  /** 技术市场适配 − 结构性宏观。正数＝价格跑在环境前面。 */
  macroTechnicalGap?: number | null;
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
  /** 实时报价与公司资料的真实供应方；接口未标注时保持 null。 */
  priceProvider?: string | null;
  profileProvider?: string | null;
  /**
   * live 快照口径：'strength-row' = 当前仅命中扫描行保存快照，
   * 由 /strength/stocks/{t} 回退基础行情（仅价/涨跌/市值等，其余如实留空）。
   */
  snapshotScope?: 'full' | 'strength-row';
  /**
   * 宏观适配（影子字段）。来自 /strength/stocks/{t} 的扫描行，和选股表同一份读数，
   * 因此是**板块级**的暴露画像，不是对这一只票单独算的。缺失保持 undefined/null，
   * 不兜 50。
   */
  macroFit?: number | null;
  macroTailwind?: string | null;
  macroFitConfidence?: number | null;
  macroSupporting?: MacroFitDriver[];
  macroOpposing?: MacroFitDriver[];
  macroTechnicalGap?: number | null;
  /** 后端 macro_shadow_status：区分「没快照」「未归板块」「暴露观测不足」。 */
  macroShadowStatus?: string | null;
  /**
   * 这份宏观读数出自哪一期快照（后端 macro_snapshot_date）。
   *
   * 抽屉里 macroFit 取自实时概览，而 macroTechnicalGap 只能取自落库的扫描行 ——
   * 两者可能横跨一次宏观发布。有了它才能判断这两个数字说的是不是同一时刻，
   * 而不是把两期快照拼成一个看起来很精确的组合。
   */
  macroSnapshotDate?: string | null;
}

export interface StockPullResource {
  status: 'available' | 'unavailable' | 'failed';
  provider: string | null;
  asOf: string | null;
  persisted: boolean;
  errorCode?: string | null;
  barCount?: number;
  metricCount?: number;
  lastBarAt?: string | null;
}

/** 管理员按需拉取：后端以 Massive 为价格主源，结果回填详情、日线与信号快照。 */
export interface StockPullResult {
  ticker: string;
  status: 'completed' | 'partial';
  fetchedAt: string;
  persistenceStatus: 'completed' | 'failed';
  resources: {
    overview: StockPullResource;
    dailyChart: StockPullResource;
    signals: StockPullResource;
  };
}

export interface Candle {
  t: string; // ISO
  o: number; h: number; l: number; c: number; v: number;
  /** 盘前/盘后 bar（分钟周期 prepost）；均线等常规时段指标不吃它 */
  ext?: boolean;
  /** 零成交的报价路径 bar：只有报价包络，不是真实成交 OHLC */
  quote_only?: boolean;
}
export interface StockChart {
  ticker: string;
  /** 后端真实 K 线周期；不是回看区间。 */
  range: '5m' | '15m' | '1h' | '1d' | '1w';
  candles: Candle[];
  ma20: (number | null)[];
}

/* ---------- 个股技术结构（/stocks/{t}/technical） ---------- */

/** 已确认分形摆动点；t 与日线 bar 的 t 逐根同源，前端按同一转换对齐。 */
export interface TechSwingPoint {
  /** 归一后与 Candle.t 同格式（ISO）；用于在图上按根寻址 */
  t: string;
  /** 纽约时区的交易日 YYYY-MM-DD（展示用） */
  trade_date: string;
  price: number | null;
}

/** 枢轴聚类检出的完成基底（服务端 technical/base_structure）。 */
export interface TechBaseStructure {
  pivot_id?: string | null;
  pivot_price: number | null;
  resistance_low: number | null;
  resistance_high: number | null;
  support_low: number | null;
  support_high?: number | null;
  invalidation_price: number | null;
  base_start: string | null;
  base_end: string | null;
  resistance_touches: number | null;
  quality: number | null;
  base_duration_days?: number | null;
  /** 七维质量分里有几维是实测（其余走保守默认值） */
  quality_coverage?: { observed: number; total: number; missing: string[] } | null;
  /** 七档窗口里有几档独立检出基底（1/7 孤检 vs 5/7 共识） */
  window_agreement?: number | null;
  windows_scanned?: number | null;
}

/** 最新价相对已检出基底的位置（基底本身永远是「截至前一收盘」的历史检测） */
export type TechBaseStatus = 'in_base' | 'at_resistance' | 'breakout' | 'below_support' | 'failed';

export interface TechBaseState {
  status: TechBaseStatus;
  reference_close: number | null;
  reference_date: string | null;
  /** 参考收盘来自未收盘 bar：状态是暂定的 */
  provisional: boolean;
}

export interface TechPatternEvent {
  pattern: string;
  /** 后端下发中文标签，渲染处过 t() */
  label: string;
  bars_ago: number | null;
  trade_date: string | null;
}

export interface TechnicalStructure {
  base: TechBaseStructure | null;
  price_action: {
    status: string;
    score: number | null;
    structure: string;
    /** 后端下发中文标签，渲染处过 t()（与雷达 structure_label 同纪律） */
    structure_label: string;
    swing_highs: TechSwingPoint[];
    swing_lows: TechSwingPoint[];
    resistance: number | null;
    support: number | null;
    resistance_dist_pct: number | null;
    support_dist_pct: number | null;
    patterns: string[];
    pattern_labels: string[];
    /** 形态是「最近 3 根里的历史事件」——带发生时点，不冒充当前状态 */
    pattern_events: TechPatternEvent[];
    spring: boolean;
    upthrust: boolean;
    spring_bars_ago: number | null;
    spring_trade_date: string | null;
    upthrust_bars_ago: number | null;
    upthrust_trade_date: string | null;
    tags: string[];
  };
  base_state: TechBaseState | null;
  vol_price: {
    status: string;
    setup_type: string;
    setup_label: string;
    effort: number | null;
    result: number | null;
    /** null = 量价数据不足未测——不是 0 */
    breakout_quality_adjustment: number | null;
    false_breakout_risk: number | null;
    tags: string[];
  };
  technicals: {
    rsi14: number | null;
    rsi_score: number | null;
    macd: { histogram: number | null; histogram_pct: number | null; direction_pct: number | null };
    trend_efficiency_63d: number | null;
    ma50_slope_pct_21d: number | null;
    return_stability_20d: number | null;
    range_position_60d: number | null;
    range_persistence_fast: number | null;
    range_persistence_slow: number | null;
  };
  chart_overlays: {
    swing_highs: TechSwingPoint[];
    swing_lows: TechSwingPoint[];
    resistance_high?: number | null;
    resistance_low?: number | null;
    support_low?: number | null;
    invalidation_price?: number | null;
    pivot_price?: number | null;
    base_start?: string | null;
    base_end?: string | null;
    /** 最新价相对基底的状态（叠加层按它改变视觉语义） */
    base_status?: TechBaseStatus | null;
  };
  /** raw_daily：与日 K 线同一序列（拆股已复权、分红不复权） */
  basis?: string;
  /** 指标/结构实际吃到的最后一根收盘 K 的日期 */
  data_through?: string | null;
  /** 图上可见的最后一根（可能未收盘）；closed=false 时状态判定为暂定 */
  last_bar?: { t: string; trade_date: string; closed: boolean } | null;
  /** 序列断裂点（坏拼接/异常断崖）；分析只用断裂之后的一致段 */
  series_break_at?: string | null;
  as_of?: string | null;
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
  /** 事件价：盘前跳空等 setup 的契约 event_price 可为 null（无成交锚点） */
  price: number | null;
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
  /** GET /api/sectors 的真实目录字段。 */
  tickers?: string[];
  /** 仅供本地预览旧夹具兼容；在线页面不把这些字段当作接口依据。 */
  changePct?: number;
  strengthScore?: number;
  constituents?: { ticker: string; name: string; changePct: number }[];
}
export interface IvRankRow {
  ticker: string;
  name: string;
  price?: number | null;
  priceProvider?: string | null;
  sectorIvRank?: number | null;
  atmIvPercent?: number | null;
  stale?: boolean;
  asOf?: string | null;
  /** 本地预览旧夹具兼容。 */
  ivPercentile?: number;
  iv?: number;
  changePct?: number;
}

/* ---------- 财报 ---------- */
export interface EarningsItem {
  ticker: string;
  name: string;
  date: string;           // ISO date
  timing: 'bmo' | 'amc' | null;  // 盘前 / 盘后 / 时间待定
  epsEstimate: number | null;
  epsActual: number | null;
  revEstimate: number | null;  // 美元；与后端 revenue_estimate 契约一致
  revActual: number | null;    // 美元
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
/**
 * 期权数值字段一律可空（GPT-5.6-Pro 审计 P1-02）。
 * 在行情界面里 `0` 与「上游没有给」是两个不同的事实：0 表示确有成交量／买价为零，
 * null 表示该字段不可用。把缺失补成 0 会让 $0.00 报价、0 中间价和不存在的合约腿
 * 看起来像真实数据，并继续流进异动判定与权利金估算。
 * 标识字段（ticker / strike / expiration）缺失的记录直接丢弃，不构造替身。
 */
export interface UnusualOption {
  id: string;
  ticker: string;
  side: 'call' | 'put';
  strike: number;
  expiration: string;
  volume: number | null;
  openInterest: number | null;
  premium: number | null;      // 万美元
  sentiment: NewsSentiment;
  at: string;
}
export interface OptionChainRow {
  strike: number;
  callOi: number | null; callVol: number | null; callIv: number | null;
  callBid: number | null; callAsk: number | null;
  putOi: number | null; putVol: number | null; putIv: number | null;
  putBid: number | null; putAsk: number | null;
}
export interface OptionChain {
  ticker: string;
  expiration: string;
  /** 标的现价；缺失时为 null —— 平值判定与价内着色随之停用，而不是错落在 0 上。 */
  spot: number | null;
  rows: OptionChainRow[];
  provider?: string | null;
  asOf?: string | null;
  stale?: boolean;
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
