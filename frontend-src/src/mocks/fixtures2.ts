/** Mock fixtures · 突破雷达 / 板块 / 财报 / 催化剂 / 期权 / AI 任务 / Worker */
import { ApiError } from '@/api/client';
import { Rng, round2, round4 } from './rng';
import { HOTSPOTS, NEWS_SOURCES, NEWS_TEMPLATES, SECTORS, TICKER_POOL } from './data';
import { SIGNAL_LABELS, WATCHLIST_TICKERS, getMarketStatus, getStockDetail } from './fixtures';
import type {
  AiJob,
  BreakoutEvent,
  BreakoutEventDetail,
  BreakoutSignal,
  BreakoutStatus,
  CatalystsStatus,
  EarningsImpact,
  EarningsItem,
  FocusCycle,
  Hotspot,
  IvRankRow,
  NewsItem,
  OptionChain,
  Sector,
  SignalType,
  UnusualOption,
  WorkerTask,
} from '@/api/types';
import { t as __t } from '../i18n/core.ts';

const pickTicker = (r: Rng) => TICKER_POOL[r.int(0, TICKER_POOL.length - 1)];

/* ---------------- 突破雷达 ---------------- */
/*
 * 契约全字段（api-contract.md BreakoutEvent）：在 BreakoutSignal / BreakoutEventDetail
 * 之上宽松挂载 lifecycle_state / setup_type / 评分套组 / 支撑阻力区带 / 生命周期轨迹等，
 * 保持既有字段不变（不破坏现有消费者），页面侧按同名结构读取。
 */
type BSession = 'premarket' | 'regular' | 'postmarket' | 'closed';

interface ContractFields {
  event_id: string;
  name: string;
  sector: string;
  session: BSession;
  setup_type: string;
  lifecycle_state: string;
  event_at: string;
  triggered_at: string;
  event_price: number;
  current_price: number;
  session_change_pct: number;
  gap_pct: number;
  rvol_time_of_day: number;
  pivot_price: number;
  target_price: number;
  support_zone: { low: number; high: number };
  resistance_zone: { low: number; high: number };
  invalidation_price: number;
  intrinsic_strength_score: number;
  base_quality_score: number;
  breakout_confirmation_score: number;
  liquidity_quality_score: number;
  chase_risk_score: number;
  sector_fit_score: number;
  market_fit_score: number;
  alert_priority_score: number;
  data_confidence_score: number;
  range_persistence: { trend: number; hold: number; volatility: number; volume: number; participation: number };
  transitions: { state: string; at: string; note?: string }[];
  /* 详情别名（与 BreakoutEventDetail 对齐，便于当日信号直接打开详情面板） */
  triggerPrice?: number;
  targetPrice?: number;
  invalidPrice?: number;
  evidence?: string[];
}

const SETUP_TYPES = [
  'DAILY_BASE_BREAKOUT', 'OPENING_RANGE_BREAKOUT', 'PREMARKET_GAP', 'GAP_AND_GO', 'GAP_HOLD',
  'GAP_FADE', 'RETEST_BREAKOUT', 'MOMENTUM_SPIKE', 'RECOVERY_BREAKOUT',
] as const;

const SETUP_CN_MOCK: Record<string, string> = {
  DAILY_BASE_BREAKOUT: '日线基底突破',
  OPENING_RANGE_BREAKOUT: '开盘区间突破',
  PREMARKET_GAP: '盘前跳空',
  GAP_AND_GO: '跳空续攻',
  GAP_HOLD: '跳空企稳',
  GAP_FADE: '跳空回落',
  RETEST_BREAKOUT: '回踩突破',
  MOMENTUM_SPIKE: '动量急拉',
  RECOVERY_BREAKOUT: '修复突破',
};

const SETUP_TO_SIGNAL: Record<string, SignalType> = {
  PREMARKET_GAP: 'gap',
  GAP_AND_GO: 'gap',
  GAP_HOLD: 'gap',
  GAP_FADE: 'gap',
  MOMENTUM_SPIKE: 'volume',
  RETEST_BREAKOUT: 'pullback',
  RECOVERY_BREAKOUT: 'pullback',
  DAILY_BASE_BREAKOUT: 'breakout',
  OPENING_RANGE_BREAKOUT: 'breakout',
};

const LIFECYCLE_FLOW = [
  'DISCOVERED', 'WATCHING', 'TRIGGERED', 'CONFIRMED', 'HOLDING', 'RETESTING', 'RETEST_HELD', 'REACCELERATING', 'EXTENDED',
] as const;

function buildScores(r: Rng, strength: number) {
  return {
    intrinsic_strength_score: strength,
    base_quality_score: Math.round(r.normal(68, 14, 35, 96)),
    breakout_confirmation_score: Math.round(r.normal(strength, 10, 30, 99)),
    liquidity_quality_score: Math.round(r.normal(70, 12, 40, 98)),
    chase_risk_score: Math.round(r.normal(45, 18, 12, 88)),
    sector_fit_score: Math.round(r.normal(66, 14, 30, 96)),
    market_fit_score: Math.round(r.normal(62, 13, 28, 94)),
    alert_priority_score: Math.round(r.normal(strength, 9, 35, 99)),
    data_confidence_score: Math.round(r.normal(84, 8, 55, 99)),
  };
}

function buildRangePersistence(r: Rng, strength: number) {
  return {
    trend: Math.round(r.normal(strength, 12, 25, 98)),
    hold: Math.round(r.normal(64, 15, 22, 96)),
    volatility: Math.round(r.normal(58, 16, 18, 94)),
    volume: Math.round(r.normal(66, 15, 24, 99)),
    participation: Math.round(r.normal(60, 14, 20, 95)),
  };
}

/** 证据列表：分钟级时间戳前缀（HH:MM 文本） */
function evidenceLines(r: Rng, triggerISO: string, pivot: number, sector: string): string[] {
  const t0 = new Date(triggerISO).getTime();
  const pool: [number, string][] = [
    [0, `放量 ${r.float(1.8, 4.2).toFixed(1)}× 突破枢轴位 ${pivot.toFixed(2)}`],
    [3, `5 分钟量能达 20 日均量 ${r.float(1.6, 3.4).toFixed(1)} 倍`],
    [7, `板块同步走强，${sector}当日相对强度 ${r.int(62, 94)}`],
    [12, `期权看涨成交占比 ${r.int(55, 82)}%，IV 百分位 ${r.int(30, 78)}`],
    [18, `回踩不破 ${(pivot * 0.995).toFixed(2)}，承接有效`],
  ];
  const n = r.int(3, 5);
  return pool.slice(0, n).map(([offset, text]) => {
    const d = new Date(t0 + offset * 60_000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm} ${text}`;
  });
}

/** 生命周期轨迹：DISCOVERED → … → 当前态，分钟级递增时间 */
function buildTransitions(r: Rng, finalState: string, triggerISO: string): { state: string; at: string; note?: string }[] {
  const t0 = new Date(triggerISO).getTime();
  let chain: string[];
  if (finalState === 'FAILED' || finalState === 'EXPIRED') {
    chain = ['DISCOVERED', 'WATCHING', 'TRIGGERED', finalState];
  } else {
    const idx = Math.max(0, LIFECYCLE_FLOW.indexOf(finalState as (typeof LIFECYCLE_FLOW)[number]));
    chain = [...LIFECYCLE_FLOW.slice(0, idx + 1)];
  }
  const uniq = chain.filter((s, i) => chain.indexOf(s) === i);
  return uniq.map((state, i) => {
    const at = i === 0 ? t0 - r.int(70, 130) * 60_000 : i === 1 ? t0 - r.int(25, 55) * 60_000 : t0 + (i - 2) * r.int(8, 22) * 60_000;
    const note = state === 'TRIGGERED' ? __t('价格越过触发位') : state === 'FAILED' ? __t('跌得失效价，信号作废') : state === 'EXPIRED' ? __t('超时未延续，归档') : undefined;
    return { state, at: new Date(at).toISOString(), note };
  });
}

/* 当日 6 条信号：覆盖 6 种形态 / 5 种生命周期态（breakouts.md Mock 要点） */
const CURRENT_SPECS: { ticker: string; setup: string; lifecycle: string; minAgo: number }[] = [
  { ticker: 'NVDA', setup: 'DAILY_BASE_BREAKOUT', lifecycle: 'CONFIRMED', minAgo: 38 },
  { ticker: 'AMD', setup: 'OPENING_RANGE_BREAKOUT', lifecycle: 'TRIGGERED', minAgo: 21 },
  { ticker: 'TSLA', setup: 'MOMENTUM_SPIKE', lifecycle: 'TRIGGERED', minAgo: 12 },
  { ticker: 'META', setup: 'RETEST_BREAKOUT', lifecycle: 'RETESTING', minAgo: 64 },
  { ticker: 'COIN', setup: 'PREMARKET_GAP', lifecycle: 'HOLDING', minAgo: 132 },
  { ticker: 'SMCI', setup: 'GAP_AND_GO', lifecycle: 'WATCHING', minAgo: 187 },
];

export function getBreakoutsCurrent(): BreakoutSignal[] {
  const now = Date.now();
  const items: (BreakoutSignal & ContractFields)[] = CURRENT_SPECS.map((spec, i) => {
    const r = new Rng(77120 + i * 331);
    const d = getStockDetail(spec.ticker);
    const strength = Math.round(r.normal(78, 9, 58, 96));
    const pivot = round2(d.prevClose * (1 + r.float(0.008, 0.03)));
    const trigger = pivot;
    const current = round2(Math.max(d.price, trigger * (1 + r.float(0.002, 0.028))));
    const sessionChange = round2((current / d.prevClose - 1) * 100);
    const target = round2(trigger * (1 + r.float(0.05, 0.11)));
    const invalid = round2(trigger * (1 - r.float(0.025, 0.05)));
    const triggeredAt = new Date(now - spec.minAgo * 60_000).toISOString();
    const type = SETUP_TO_SIGNAL[spec.setup] ?? 'breakout';
    const rvol = round2(r.float(1.6, 4.2));
    return {
      id: `bc-${i + 1}`,
      ticker: d.ticker,
      name: d.name,
      type,
      label: SIGNAL_LABELS[type],
      price: current,
      changePct: sessionChange,
      strengthScore: strength,
      at: triggeredAt,
      summary: `${d.ticker} ${SETUP_CN_MOCK[spec.setup]}：放量 ${rvol.toFixed(1)}× 越过枢轴 ${pivot.toFixed(2)}，强度 ${strength}，失效价 ${invalid.toFixed(2)} 上方承接有效。`,
      event_id: `bc-${i + 1}`,
      sector: d.sector,
      session: 'regular',
      setup_type: spec.setup,
      lifecycle_state: spec.lifecycle,
      event_at: triggeredAt,
      triggered_at: triggeredAt,
      event_price: trigger,
      current_price: current,
      session_change_pct: sessionChange,
      gap_pct: spec.setup.includes('GAP') ? round2(r.float(1.2, 4.6)) : round2(r.float(-0.4, 0.8)),
      rvol_time_of_day: rvol,
      pivot_price: pivot,
      target_price: target,
      support_zone: { low: round2(invalid * 0.985), high: round2(invalid * 1.015) },
      resistance_zone: { low: round2(pivot * 0.994), high: round2(pivot * 1.006) },
      invalidation_price: invalid,
      triggerPrice: trigger,
      targetPrice: target,
      invalidPrice: invalid,
      evidence: evidenceLines(r, triggeredAt, pivot, d.sector),
      ...buildScores(r, strength),
      range_persistence: buildRangePersistence(r, strength),
      transitions: buildTransitions(r, spec.lifecycle, triggeredAt),
    };
  });
  return items.sort((a, b) => b.strengthScore - a.strengthScore);
}

/* 扫描状态机：默认「冷却中 · 07:32」展示倒计时环（breakouts.md Mock 要点）；
   owner 触发 breakout_refresh 后进入 9s 扫描窗口，随后回到 8 分钟冷却。 */
let scanWindowStart = 0;
let scanWindowEnd = 0;
let cooldownEndAt = Date.now() + (7 * 60 + 32) * 1000;
let lastScanAt = Date.now() - 8 * 60_000;

interface StatusContract extends BreakoutStatus {
  enabled: boolean;
  worker: { healthy: boolean; heartbeat_at: string };
  latest_completed_scan: { at: string; duration_ms: number; scanned: number; triggered: number } | null;
  market_session: BSession;
  next_session_at: string | null;
}

export function getBreakoutsStatus(): BreakoutStatus {
  const now = Date.now();
  /* 冷却结束自动进入下一轮扫描（mock 活体状态机） */
  if (scanWindowEnd === 0 && now > cooldownEndAt) {
    scanWindowStart = now;
    scanWindowEnd = now + 9000;
  }
  if (scanWindowEnd > 0 && now >= scanWindowEnd) {
    lastScanAt = scanWindowEnd;
    cooldownEndAt = scanWindowEnd + 8 * 60_000;
    scanWindowStart = 0;
    scanWindowEnd = 0;
  }
  const market = getMarketStatus();
  const sessionMap: Record<string, BSession> = { premarket: 'premarket', regular: 'regular', afterhours: 'postmarket', closed: 'closed' };
  const universe = 612;
  if (scanWindowEnd > 0) {
    const progress = Math.min(0.98, (now - scanWindowStart) / (scanWindowEnd - scanWindowStart));
    const status: StatusContract = {
      state: 'scanning',
      scanned: Math.round(universe * progress),
      universe,
      cooldownEndsAt: null,
      lastScanAt: new Date(lastScanAt).toISOString(),
      enabled: true,
      worker: { healthy: true, heartbeat_at: new Date(now - 9_000).toISOString() },
      latest_completed_scan: { at: new Date(lastScanAt).toISOString(), duration_ms: 41_800, scanned: universe, triggered: 6 },
      market_session: sessionMap[market.session] ?? 'closed',
      next_session_at: new Date(scanWindowEnd + 8 * 60_000).toISOString(),
    };
    return status;
  }
  const status: StatusContract = {
    state: 'cooldown',
    scanned: 0,
    universe,
    cooldownEndsAt: new Date(cooldownEndAt).toISOString(),
    lastScanAt: new Date(lastScanAt).toISOString(),
    enabled: true,
    worker: { healthy: true, heartbeat_at: new Date(now - 12_000).toISOString() },
    latest_completed_scan: { at: new Date(lastScanAt).toISOString(), duration_ms: 46_200, scanned: universe, triggered: 6 },
    market_session: sessionMap[market.session] ?? 'closed',
    next_session_at: new Date(cooldownEndAt).toISOString(),
  };
  return status;
}

const breakoutEvents: BreakoutEventDetail[] = (() => {
  const r = new Rng(5150);
  const list: (BreakoutEventDetail & ContractFields)[] = Array.from({ length: 40 }, (_, i) => {
    const info = pickTicker(r);
    const setup = SETUP_TYPES[r.int(0, SETUP_TYPES.length - 1)];
    const type = SETUP_TO_SIGNAL[setup] ?? 'breakout';
    const result: BreakoutEvent['result'] = r.chance(0.35) ? 'hit' : r.chance(0.4) ? 'failed' : 'pending';
    const lifecycle =
      result === 'hit'
        ? r.pick(['EXTENDED', 'REACCELERATING', 'CONFIRMED'] as const)
        : result === 'failed'
          ? r.pick(['FAILED', 'EXPIRED'] as const)
          : r.pick(['TRIGGERED', 'HOLDING', 'RETESTING', 'RETEST_HELD', 'WATCHING'] as const);
    const at = new Date(Date.now() - r.int(20, 60 * 72) * 60_000);
    const pivot = round2(info.base * (1 + r.float(0.005, 0.035)));
    const price = pivot;
    const current = round2(
      price * (1 + (result === 'hit' ? r.float(0.03, 0.12) : result === 'failed' ? r.float(-0.07, -0.015) : r.float(-0.01, 0.035))),
    );
    const target = round2(price * (1 + r.float(0.05, 0.12)));
    const invalid = round2(price * (1 - r.float(0.02, 0.06)));
    const strength = Math.round(r.normal(64, 16, 32, 97));
    const iso = at.toISOString();
    const hourNy = Number(at.toLocaleString('en-US', { timeZone: 'America/New_York', hour: '2-digit', hour12: false }));
    const session: BSession = hourNy < 9 || (hourNy === 9 && at.getMinutes() < 30) ? 'premarket' : hourNy >= 16 ? 'postmarket' : 'regular';
    return {
      id: `be-${1000 + i}`,
      ticker: info.ticker,
      type,
      label: SIGNAL_LABELS[type],
      at: iso,
      price,
      result,
      triggerPrice: price,
      targetPrice: target,
      invalidPrice: invalid,
      evidence: evidenceLines(r, iso, pivot, info.sector),
      event_id: `be-${1000 + i}`,
      name: info.name,
      sector: info.sector,
      session,
      setup_type: setup,
      lifecycle_state: lifecycle,
      event_at: iso,
      triggered_at: iso,
      event_price: price,
      current_price: current,
      session_change_pct: round2((current / info.base - 1) * 100),
      gap_pct: setup.includes('GAP') ? round2(r.float(1, 5)) : round2(r.float(-0.5, 0.9)),
      rvol_time_of_day: round2(r.float(1.3, 4.4)),
      pivot_price: pivot,
      target_price: target,
      support_zone: { low: round2(invalid * 0.985), high: round2(invalid * 1.015) },
      resistance_zone: { low: round2(pivot * 0.994), high: round2(pivot * 1.006) },
      invalidation_price: invalid,
      ...buildScores(r, strength),
      range_persistence: buildRangePersistence(r, strength),
      transitions: buildTransitions(r, lifecycle, iso),
    };
  });
  return list.sort((a, b) => (a.at < b.at ? 1 : -1));
})();

export function getBreakoutEvents(page = 1, pageSize = 12): { items: BreakoutEvent[]; total: number; page: number } {
  const start = (page - 1) * pageSize;
  return { items: breakoutEvents.slice(start, start + pageSize), total: breakoutEvents.length, page };
}

export function getBreakoutEventDetail(id: string): BreakoutEventDetail {
  // 先查当日事件（bc-*），再查历史事件（be-*）；未命中按契约语义 404，不得错配其它事件
  const idOf = (x: unknown) => (x as { id?: string; event_id?: string });
  const cur = getBreakoutsCurrent().find((e) => idOf(e).event_id === id || idOf(e).id === id);
  if (cur) return cur as unknown as BreakoutEventDetail;
  const found = breakoutEvents.find((e) => idOf(e).id === id || idOf(e).event_id === id);
  if (found) return found;
  throw new ApiError(404, __t('突破事件不存在'));
}

export function getBreakoutsByTicker(ticker: string): BreakoutEvent[] {
  const t = ticker.toUpperCase();
  return breakoutEvents.filter((e) => e.ticker === t);
}

/* ---------------- 板块 ---------------- */
/** 板块数据源状态（对齐契约 source_status 枚举） */
export type SectorSourceStatus = 'active' | 'degraded' | 'stale' | 'insufficient_data';

/** 扩展成分行（契约字段之外的 mock 预览补充：价 / 强度分） */
export interface SectorConstituentQuote {
  ticker: string;
  name: string;
  changePct: number;
  price: number;
  strengthScore: number;
}

/** 扩展板块快照：Sector 契约字段 + 页面预览派生字段（趋势 / IV 均值 / 相关性等） */
export interface SectorSnapshot extends Omit<Sector, 'constituents'> {
  constituents: SectorConstituentQuote[];
  trend30d: number[];   // 30 日强度趋势（点阵面积图）
  ivAvg: number;        // 板块 IV 均值 %
  rsVsSpy: number;      // 30 日相对 SPY 强度（pct）
  flowRating: number;   // 5 日资金流评级 1–5
  corr: { spy: number; ndx: number; ust20y: number };
  asOf: string;
}

/** 扩展 IV 排名行：IvRankRow + 契约 price/_stale/as_of + source_status */
export interface SectorIvRow extends IvRankRow {
  price: number;
  stale: boolean;
  asOf: string;
  sourceStatus: SectorSourceStatus;
}

/** 板块快照（确定性种子；构建一次缓存，保证 IV 排名与成分口径一致） */
const sectorSnapshots: SectorSnapshot[] = (() => {
  return SECTORS.map((s, i) => {
    const r = new Rng(3300 + i * 57);
    const members = TICKER_POOL.filter((t) => t.sector === s.name);
    const base = members.length ? members : TICKER_POOL.slice(i * 3, i * 3 + 3);
    const constituents: SectorConstituentQuote[] = base.map((m) => {
      const changePct = round2(r.float(-3.4, 4.4));
      return {
        ticker: m.ticker,
        name: m.name,
        changePct,
        price: round2(m.base * (1 + changePct / 100)),
        strengthScore: Math.round(r.normal(58, 17, 22, 94)),
      };
    });
    const changePct = round2(constituents.reduce((x, c) => x + c.changePct, 0) / constituents.length);
    const strengthScore = Math.round(r.normal(60, 16, 25, 92));
    /* 30 日强度趋势：随机游走，终点锚定当日强度分 */
    let v = Math.min(96, Math.max(12, strengthScore - r.float(-9, 9)));
    const trend30d = Array.from({ length: 30 }, () => {
      v += r.float(-3, 3.4);
      return round2(Math.min(96, Math.max(12, v)));
    });
    trend30d[29] = strengthScore;
    return {
      id: s.id,
      name: s.name,
      changePct,
      strengthScore,
      constituents,
      trend30d,
      ivAvg: round2(r.float(24, 58)),
      rsVsSpy: round2(r.float(-3.2, 4.8)),
      flowRating: r.int(1, 5),
      corr: {
        spy: round2(r.float(0.35, 0.95)),
        ndx: round2(r.float(0.22, 0.92)),
        ust20y: round2(r.float(-0.55, 0.25)),
      },
      asOf: new Date(Date.now() - r.int(2, 9) * 60_000).toISOString(),
    };
  });
})();

export function getSectors(): Sector[] {
  return sectorSnapshots;
}

/** IV 排名缓存：sectorId → 行（确定性 + 跨调用一致） */
const ivRankingCache = new Map<string, SectorIvRow[]>();

export function getSectorIvRanking(sectorId: string): IvRankRow[] {
  const cached = ivRankingCache.get(sectorId);
  if (cached) return cached;
  const snap = sectorSnapshots.find((s) => s.id === sectorId) ?? sectorSnapshots[0];
  const seed = 9100 + [...sectorId].reduce((a, c) => a + c.charCodeAt(0), 0);
  const r = new Rng(seed);
  /* 以真实成分为基底；成分不足 8 只时从全池按序补足（preview 口径） */
  const pool: SectorConstituentQuote[] = [...snap.constituents];
  for (const t of TICKER_POOL) {
    if (pool.length >= 8) break;
    if (!pool.some((p) => p.ticker === t.ticker)) {
      pool.push({
        ticker: t.ticker,
        name: t.name,
        changePct: round2(r.float(-3, 4)),
        price: round2(t.base * (1 + r.float(-0.03, 0.04))),
        strengthScore: Math.round(r.normal(55, 16, 22, 92)),
      });
    }
  }
  /* 演示数据诚信状态：energy 降级徽标 / util 过期快照（其余 active） */
  const stale = sectorId === 'util';
  const degraded = sectorId === 'energy';
  const rows: SectorIvRow[] = pool.map((c) => ({
    ticker: c.ticker,
    name: c.name,
    ivPercentile: Math.round(r.float(8, 92)), // 对齐契约 sector_iv_rank(0-100)
    iv: round2(r.float(18, 66)),              // 对齐契约 atm_iv_percent（%）
    changePct: c.changePct,
    price: c.price,
    stale,
    asOf: new Date(Date.now() - (stale ? 26 * 3600_000 : r.int(2, 9) * 60_000)).toISOString(),
    sourceStatus: stale ? 'stale' : degraded ? 'degraded' : 'active',
  }));
  ivRankingCache.set(sectorId, rows);
  return rows;
}

/* ---------------- 财报 ---------------- */
/**
 * 财报扩展字段（对齐契约 eps_high/eps_low/market_cap/sector/days_until 与预期波动）。
 * api/types 的 EarningsItem 为精简子集；扩展字段随 fixture 下发，live 缺失时 UI 如实留空「—」。
 */
export interface EarningsItemEx extends EarningsItem {
  epsHigh: number | null;
  epsLow: number | null;
  marketCap: number | null;        // 美元市值
  sector: string;
  expectedMovePct: number | null;  // 预期波动 %（2.8–11.5）
  impactReady: boolean;            // 是否已有 AI 影响分析缓存
}

/** AI 影响分析扩展：生成时间 / 历史均值对照 / 模型置信度（UI 必须标注「· 非胜率」） */
export interface EarningsImpactEx extends EarningsImpact {
  generatedAt: string;
  histAvgMovePct: number | null;
  confidence: number | null; // 0–1
}

/** Mock 要点：NVDA/MSFT/TSLA 三条已含 AI 分析结果，其余 409 analysis_required */
const analyzedTickers = new Set<string>(['NVDA', 'MSFT', 'TSLA']);
const analyzedAt = new Map<string, string>();

/*
 * 生成范围：上月 1 日 ~ 下月月末（覆盖月历「上月/本月/下月」三月视图）。
 * 工作日每天 0–4 条（按日期确定性种子，当日 ticker 不重复）；周末留白。
 * 过去日期视为已公布（eps/rev 实际值大概率回填，days_until 由页面侧算得为负）；
 * 今日/未来多数未公布。字段与既有 EarningsItem 完全一致，不破坏现有消费者。
 */
const earningsList: EarningsItem[] = (() => {
  const names = [...WATCHLIST_TICKERS, 'TSM', 'NFLX', 'CRM', 'ORCL', 'JPM', 'UNH', 'COST'].slice(0, 18);
  const now = new Date();
  const todayIso = now.toISOString().slice(0, 10);
  const startMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1);
  const endMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 2, 0); // 下月月末
  const byDate = new Map<string, EarningsItem[]>();
  const push = (date: string, item: EarningsItem) => {
    const arr = byDate.get(date) ?? [];
    arr.push(item);
    byDate.set(date, arr);
  };
  const mkItem = (r: Rng, ticker: string, date: string): EarningsItem => {
    const info = TICKER_POOL.find((x) => x.ticker === ticker) ?? TICKER_POOL[0];
    const epsEst = round2(r.float(0.2, 6.4));
    const reported = date < todayIso ? r.chance(0.9) : r.chance(0.15);
    return {
      ticker: info.ticker,
      name: info.name,
      date,
      timing: r.chance(0.5) ? 'bmo' : 'amc',
      epsEstimate: epsEst,
      epsActual: reported ? round2(epsEst * r.float(0.86, 1.18)) : null,
      revEstimate: Math.round(r.float(40, 980) * 1_000_000),
      revActual: reported ? Math.round(r.float(40, 990) * 1_000_000) : null,
    } as EarningsItem;
  };
  for (let ms = startMs; ms <= endMs; ms += 86_400_000) {
    const d = new Date(ms);
    const dow = d.getUTCDay();
    if (dow === 0 || dow === 6) continue; // 仅工作日
    const date = d.toISOString().slice(0, 10);
    const r = new Rng(20240 + Number(date.replaceAll('-', '')));
    const roll = r.float();
    const count = roll < 0.12 ? 0 : roll < 0.45 ? 1 : roll < 0.75 ? 2 : roll < 0.92 ? 3 : 4;
    /* 确定性洗牌后取前 N 个，保证当日 ticker 唯一 */
    const order = [...names];
    for (let i = order.length - 1; i > 0; i--) {
      const j = r.int(0, i);
      [order[i], order[j]] = [order[j], order[i]];
    }
    order.slice(0, count).forEach((t) => push(date, mkItem(r, t, date)));
  }
  /* 锚定 NVDA/MSFT/TSLA 于近期工作日：AI 影响演示三标的在未来两周内必有排期 */
  ['NVDA', 'MSFT', 'TSLA'].forEach((t, i) => {
    let ms = Date.parse(`${todayIso}T12:00:00Z`) + (2 + i * 3) * 86_400_000;
    let d = new Date(ms);
    while (d.getUTCDay() === 0 || d.getUTCDay() === 6) {
      ms += 86_400_000;
      d = new Date(ms);
    }
    const date = d.toISOString().slice(0, 10);
    const arr = byDate.get(date) ?? [];
    if (!arr.some((e) => e.ticker === t) && arr.length < 4) {
      push(date, mkItem(new Rng(60606 + i * 97), t, date));
    }
  });
  return [...byDate.values()].flat().sort((a, b) => (a.date < b.date ? -1 : 1));
})();

/** 每标的确定性预期波动（2.8%–11.5%），与 AI 影响结果共用同一来源 */
function expectedMoveOf(ticker: string): number {
  const r = new Rng(31337 + ticker.length * 11 + ticker.charCodeAt(0));
  return round2(r.float(2.8, 11.5));
}

export function getEarningsUpcoming(): EarningsItemEx[] {
  return earningsList.map((e) => {
    const r = new Rng(4477 + e.ticker.length * 31 + e.ticker.charCodeAt(0));
    const info = TICKER_POOL.find((x) => x.ticker === e.ticker);
    const est = e.epsEstimate;
    return {
      ...e,
      epsHigh: est != null ? round2(est * r.float(1.04, 1.22)) : null,
      epsLow: est != null ? round2(est * r.float(0.78, 0.96)) : null,
      // 演示模式没有真实市值来源，必须留空，不能按基础价随机推算。
      marketCap: null,
      sector: info?.sector ?? '—',
      expectedMovePct: expectedMoveOf(e.ticker),
      impactReady: analyzedTickers.has(e.ticker),
    };
  });
}

let earningsRefreshCount = 0;
export function refreshEarningsUpcoming(): EarningsItem[] {
  earningsRefreshCount += 1;
  return getEarningsUpcoming();
}
export const getEarningsRefreshCount = () => earningsRefreshCount;

/** 连锁反应关系图谱（真实风格供应链叙事） */
const IMPACT_RELATIONS: Record<string, { ticker: string; relation: string }[]> = {
  NVDA: [
    { ticker: 'SMCI', relation: __t('服务器整机') },
    { ticker: 'TSM', relation: __t('晶圆代工') },
    { ticker: 'MU', relation: __t('HBM 供应') },
    { ticker: 'ARM', relation: __t('IP 授权') },
  ],
  MSFT: [
    { ticker: 'NVDA', relation: __t('算力供应链') },
    { ticker: 'ORCL', relation: __t('云基建') },
    { ticker: 'CRM', relation: __t('企业软件') },
    { ticker: 'PLTR', relation: __t('数据平台') },
  ],
  TSLA: [
    { ticker: 'UBER', relation: __t('出行生态') },
    { ticker: 'DASH', relation: __t('消费景气') },
    { ticker: 'ABNB', relation: '可选消费' },
    { ticker: 'AMZN', relation: __t('大盘成长联动') },
  ],
};

const IMPACT_SUMMARIES: Record<string, string> = {
  NVDA: 'AI 综合历史财报反应与当前期权定价，预计英伟达（$NVDA）财报后单日波动 ±{m}%。$TSM 与 $SMCI 对数据中心指引口径最敏感，$MU 的 HBM 供给节奏是最大变量；若资本开支指引上修，$ARM 与先进封装链条将同步共振。',
  MSFT: '模型预计微软（$MSFT）财报后单日波动 ±{m}%。Azure 增速与 Copilot 变现是指引核心，$NVDA 的算力供给与 $ORCL 的云基建开支将被同步重估；$CRM、$PLTR 对企业 IT 预算口径高度敏感。',
  TSLA: '模型预计特斯拉（$TSLA）财报后单日波动 ±{m}%。毛利率与 FSD 进展主导定价，$UBER、$DASH 反映的出行与消费景气会放大波动；若指引不及预期，$ABNB 等高 β 可选消费标的将承压。',
};

const IMPACT_SENTIMENT: Record<string, EarningsImpact['sentiment']> = {
  NVDA: 'bullish',
  MSFT: 'neutral',
  TSLA: 'bearish',
};

export function getEarningsImpact(ticker: string): EarningsImpactEx {
  const t = ticker.toUpperCase();
  if (!analyzedTickers.has(t)) {
    throw new ApiError(409, `${t} 尚未生成 AI 影响分析`, { bizCode: 'analysis_required' });
  }
  const r = new Rng(8080 + t.length * 7 + t.charCodeAt(0));
  const info = TICKER_POOL.find((x) => x.ticker === t) ?? TICKER_POOL[0];
  const move = expectedMoveOf(t);
  const relations = IMPACT_RELATIONS[t] ?? TICKER_POOL.filter((x) => x.sector === info.sector && x.ticker !== t)
    .slice(0, 4)
    .map((x) => ({ ticker: x.ticker, relation: __t('同板块联动') }));
  const rr = new Rng(9090 + t.length * 13 + t.charCodeAt(0));
  const related = relations.map((rel) => {
    const relInfo = TICKER_POOL.find((x) => x.ticker === rel.ticker);
    return {
      ticker: rel.ticker,
      name: relInfo?.name ?? rel.ticker,
      relation: rel.relation,
      changePct: round2(rr.float(-2.4, 4.6)),
    };
  });
  const tpl = IMPACT_SUMMARIES[t];
  const summary = tpl
    ? tpl.replace('{m}', move.toFixed(1))
    : `AI 综合历史财报反应与当前期权定价，预计${info.name}（$${info.ticker}）财报后单日波动区间 ±${move.toFixed(1)}%。供应链与指引口径是本次最大变量，建议关注管理层对资本开支的表述。`;
  return {
    ticker: info.ticker,
    expectedMovePct: move,
    sentiment: IMPACT_SENTIMENT[t] ?? rr.pick(['bullish', 'neutral', 'bearish'] as const),
    ivRank: Math.round(r.float(28, 86)),
    related,
    summary,
    generatedAt: analyzedAt.get(t) ?? new Date(Date.now() - r.int(2, 30) * 3_600_000).toISOString(),
    histAvgMovePct: round2(move * r.float(0.72, 1.08)),
    confidence: round2(r.float(0.58, 0.86)),
  };
}

/** AI 任务成功 → 标记该标的已有影响分析缓存（getEarningsImpact 随即可用） */
export function markImpactAnalyzed(ticker: string): void {
  const t = ticker.toUpperCase();
  analyzedTickers.add(t);
  analyzedAt.set(t, new Date().toISOString());
}

/* ---------------- 催化剂 ---------------- */
const newsPool: NewsItem[] = (() => {
  const r = new Rng(133713);
  return Array.from({ length: 60 }, (_, i) => {
    const tpl = NEWS_TEMPLATES[i % NEWS_TEMPLATES.length];
    const info = pickTicker(r);
    const sentiment: NewsItem['sentiment'] = r.chance(0.45) ? 'positive' : r.chance(0.6) ? 'neutral' : 'negative';
    return {
      id: `news-${2000 + i}`,
      title: tpl.title.replaceAll('{t}', info.ticker),
      summary: tpl.summary.replaceAll('{t}', info.ticker),
      source: r.pick(NEWS_SOURCES),
      publishedAt: new Date(Date.now() - r.int(3, 60 * 30) * 60_000).toISOString(),
      sentiment,
      tickers: [info.ticker, ...(r.chance(0.4) ? [pickTicker(r).ticker] : [])],
      heat: Math.round(r.normal(55, 18, 10, 98)),
    };
  }).sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1));
})();

export function getCatalystsFeed(page = 1, pageSize = 20): { items: NewsItem[]; total: number } {
  const start = (page - 1) * pageSize;
  return { items: newsPool.slice(start, start + pageSize), total: newsPool.length };
}
export function getNewsById(id: string): NewsItem {
  return newsPool.find((n) => n.id === id) ?? newsPool[0];
}
export function getNewsByTicker(ticker: string): NewsItem[] {
  const t = ticker.toUpperCase();
  return newsPool.filter((n) => n.tickers.includes(t));
}

export function getCatalystsStatus(): CatalystsStatus {
  return {
    newsToday: newsPool.filter((n) => Date.now() - new Date(n.publishedAt).getTime() < 24 * 3600_000).length,
    hotspotsActive: HOTSPOTS.length,
    lastCrawlAt: new Date(Date.now() - 96_000).toISOString(),
  };
}

export function getHotspots(): Hotspot[] {
  const r = new Rng(61616);
  return HOTSPOTS.map((h, i) => ({
    id: `hot-${i + 1}`,
    theme: h.theme,
    heat: Math.round(r.float(52, 97)),
    newsCount: r.int(6, 28),
    representative: newsPool[i * 3]?.title ?? `${h.theme} 持续发酵`,
    tickers: [...h.tickers],
  })).sort((a, b) => b.heat - a.heat);
}

export function getHotspotsStatus(): { scanning: boolean; updatedAt: string } {
  return { scanning: true, updatedAt: new Date(Date.now() - 58_000).toISOString() };
}

export function getCatalystsCalendar(): { date: string; items: { kind: string; label: string }[] }[] {
  const r = new Rng(7788);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(Date.now() + i * 86_400_000);
    const items = [
      { kind: 'macro', label: r.pick([__t('CPI 数据公布'), __t('FOMC 会议纪要'), __t('初请失业金人数'), __t('PPI 数据公布'), __t('零售销售月率')]) },
      ...(r.chance(0.6) ? [{ kind: 'earnings', label: `${pickTicker(r).ticker} 财报` }] : []),
    ];
    return { date: d.toISOString().slice(0, 10), items };
  });
}

export function getLatestFocusCycle(): FocusCycle {
  return {
    id: 'fc-1',
    theme: __t('AI 算力资本开支'),
    startedAt: new Date(Date.now() - 19 * 86_400_000).toISOString(),
    days: 19,
    stage: '主升',
    summary: '本轮焦点周期由 hyperscaler 资本开支指引上修点燃，半导体设备与先进封装轮动走强，资金沿「算力—散热—电力」链条扩散。',
  };
}

export function submitTickersBatch(tickers: string[]): { accepted: number } {
  return { accepted: tickers.length };
}

/* ---------------- 期权 ---------------- */
const OPTION_TICKERS = ['NVDA', 'TSLA', 'AAPL', 'AMD', 'AMZN', 'META', 'MSFT', 'SPY', 'QQQ', 'GOOGL'];

/** 期权链快照覆盖判定（未覆盖标的显示空态，不编造） */
export function optionsSupported(ticker: string): boolean {
  return OPTION_TICKERS.includes(ticker.toUpperCase());
}
export const OPTION_SUPPORTED_LIST = OPTION_TICKERS.join(' ');

export function getUnusualOptions(): UnusualOption[] {
  const r = new Rng(90909);
  return Array.from({ length: 12 }, (_, i) => {
    const t = OPTION_TICKERS[r.int(0, OPTION_TICKERS.length - 1)];
    const info = TICKER_POOL.find((x) => x.ticker === t)!;
    const side: UnusualOption['side'] = r.chance(0.62) ? 'call' : 'put';
    const dte = r.int(2, 45);
    return {
      id: `uo-${i + 1}`,
      ticker: t,
      side,
      strike: round2(info.base * (1 + r.float(-0.08, 0.12))),
      expiration: new Date(Date.now() + dte * 86_400_000).toISOString().slice(0, 10),
      volume: Math.round(r.float(2, 48) * 1000),
      openInterest: Math.round(r.float(4, 90) * 1000),
      premium: round2(r.float(12, 480)),
      sentiment: side === 'call' ? ('positive' as const) : ('negative' as const),
      at: new Date(Date.now() - r.int(5, 240) * 60_000).toISOString(),
    };
  }).sort((a, b) => (a.at < b.at ? 1 : -1));
}

export function getOptionExpirations(ticker: string): string[] {
  const r = new Rng(4141 + ticker.length);
  return Array.from({ length: 6 }, (_, i) =>
    new Date(Date.now() + (i + 1) * r.int(6, 12) * 86_400_000).toISOString().slice(0, 10),
  ).sort();
}

export function getOptionChain(ticker: string, expiration?: string): OptionChain {
  const info = TICKER_POOL.find((x) => x.ticker === ticker.toUpperCase()) ?? TICKER_POOL[0];
  const spot = info.base;
  const exp = expiration ?? getOptionExpirations(info.ticker)[0];
  const r = new Rng(70007 + exp.length + info.ticker.length * 13);
  const step = Math.max(1, Math.round(spot * 0.025));
  const start = Math.round(spot / step) * step - 8 * step;
  const rows = Array.from({ length: 16 }, (_, i) => {
    const strike = start + (i + 1) * step;
    const moneyness = (strike - spot) / spot;
    const baseIv = 0.32 + Math.abs(moneyness) * 0.9;
    return {
      strike,
      callOi: Math.round(r.float(1, 60) * 1000),
      callVol: Math.round(r.float(0.2, 22) * 1000),
      callIv: round2(baseIv * 100 + r.float(-3, 3)),
      callBid: round2(Math.max(0.05, spot - strike) + r.float(0.2, 4)),
      callAsk: 0,
      putOi: Math.round(r.float(1, 60) * 1000),
      putVol: Math.round(r.float(0.2, 22) * 1000),
      putIv: round2(baseIv * 100 + r.float(-3, 3)),
      putBid: round2(Math.max(0.05, strike - spot) + r.float(0.2, 4)),
      putAsk: 0,
    };
  }).map((row) => ({
    ...row,
    callAsk: round2(row.callBid + Math.max(0.05, row.callBid * 0.04)),
    putAsk: round2(row.putBid + Math.max(0.05, row.putBid * 0.04)),
  }));
  return { ticker: info.ticker, expiration: exp, spot: round2(spot), rows };
}

/* ---------------- AI 任务 ---------------- */
const jobs = new Map<string, AiJob & { _born: number; _ticker: string }>();

export function createAiJob(kind: AiJob['kind'], payloadLabel = ''): AiJob {
  const id = `job-${Date.now().toString(36)}-${jobs.size + 1}`;
  const now = new Date().toISOString();
  const job: AiJob & { _born: number; _ticker: string } = {
    id,
    kind,
    status: 'queued',
    progress: 0,
    createdAt: now,
    updatedAt: now,
    result: '',
    _born: Date.now(),
    _ticker: kind === 'earnings-impact' ? payloadLabel.toUpperCase() : '',
  };
  if (payloadLabel) job.result = payloadLabel;
  jobs.set(id, job);
  return { ...job };
}

export function getAiJob(id: string): AiJob {
  const job = jobs.get(id);
  if (!job) throw new ApiError(404, __t('任务不存在'), { bizCode: 'job_not_found' });
  const age = Date.now() - job._born;
  if (job.status === 'queued' && age > 1200) job.status = 'running';
  if (job.status === 'running') {
    job.progress = Math.min(96, Math.round(age / 60));
    if (age > 5200) {
      job.status = 'succeeded';
      job.progress = 100;
      job.result = job.result || __t('AI 分析完成：综合基本面、量价与期权定价，详见结果正文。');
      // 财报影响任务成功 → 该标的分析缓存就绪（409 analysis_required 解除）
      if (job.kind === 'earnings-impact' && job._ticker) markImpactAnalyzed(job._ticker);
    }
  }
  job.updatedAt = new Date().toISOString();
  const { _born, _ticker, ...pub } = job;
  void _born;
  void _ticker;
  return pub;
}

export function cancelAiJob(id: string): AiJob {
  const job = jobs.get(id);
  if (job && (job.status === 'queued' || job.status === 'running')) {
    job.status = 'cancelled';
    job.updatedAt = new Date().toISOString();
  }
  return getAiJob(id);
}

/* ---------------- Worker 心跳 ---------------- */
export function getWorkerStatus(): WorkerTask[] {
  const names = [
    __t('指数行情采集'), __t('自选股快照'), __t('强度分计算'), '突破扫描', __t('板块聚合'),
    __t('财报日历同步'), __t('新闻抓取'), __t('热点聚类'), __t('期权异动'), __t('AI 任务调度'),
  ];
  const r = new Rng(24680);
  return names.map((name, i) => ({
    id: `w${i + 1}`,
    name,
    status: r.chance(0.85) ? 'ok' : r.chance(0.7) ? 'degraded' : 'down',
    lastBeatAt: new Date(Date.now() - r.int(4, 90) * 1000).toISOString(),
    note: r.pick([__t('运行正常'), __t('延迟略高于均值'), __t('队列积压清理中'), __t('等待下一周期')]),
  }));
}

export function postWorkerAction(action: string): { ok: boolean; action: string } {
  /* owner 触发突破扫描：进入 9s 扫描窗口（getBreakoutsStatus 状态机联动） */
  if (action === 'breakout_refresh') {
    const now = Date.now();
    scanWindowStart = now;
    scanWindowEnd = now + 9000;
  }
  return { ok: true, action };
}


/* ============================================================
   催化剂契约扩展（api-contract.md catalysts 全节 1:1 对齐）
   NewsItem / NewsImpactResult / 热点组 / 市场焦点周期 /
   经济日历 / 数据源健康 / 股票影响汇总 / 分析任务状态机
   —— 纯新增导出，不改动上方既有 fixture 与其消费者
   ============================================================ */

export type NewsClassification = 'bullish' | 'bearish' | 'neutral';
export type NewsAnalysisStatus =
  | 'pending'
  | 'queued'
  | 'in_progress'
  | 'completed'
  | 'insufficient_context'
  | 'failed';

export interface TrustedStockImpact {
  ticker: string;
  direction: NewsClassification;
  impactScore: number; // 影响分（· 非收益）
  horizon: string;
  mechanism: string;
  reason: string;
}

export interface NewsImpactResult {
  classification: NewsClassification;
  confidence: number; // 0–1（· 非胜率）
  headlineSummary: string;
  causalSummary: string;
  trustedStockImpacts: TrustedStockImpact[];
  model: string;
  generatedAt: string;
}

export interface CatalystNewsItem {
  newsId: string;
  source: string;
  sourceCount: number; // 多源确认数（multi_source_only 依据）
  title: string;
  titleZh: string;
  summary: string;
  summaryZh: string;
  url: string;
  publishedAt: string;
  fetchedAt: string;
  sourceTickers: string[];
  isStale: boolean;
  themeIds: string[];
  analysisStatus: NewsAnalysisStatus;
  analysis: NewsImpactResult | null;
  analysisJobId: string | null;
}

export interface HotspotGroup {
  hotspotId: string;
  theme: string;
  keywords: string[];
  heat: number; // 0–100
  heatLevel: number; // 0–5 段
  newsCount: number;
  /** newsCount 的口径：news=新闻条数（mock 契约）；sources=来源数（live 事件组契约） */
  countKind?: 'news' | 'sources';
  eventType?: string | null;
  representative: { newsId: string; titleZh: string; publishedAt: string } | null;
  tickers: string[];
  updatedAt: string;
}

export interface HotspotsStatusDetail {
  state: 'ready' | 'computing';
  scanning: boolean;
  updatedAt: string;
  etaSeconds: number | null;
  groupCount: number;
  /** live 扩展：手动焦点周期开关 / 热点准备区修订号（触发周期需带 expected_prepared_revision） */
  manualEnabled?: boolean | null;
  preparedRevision?: number | null;
  lastCycleAt?: string | null;
}

/** live 扩展：个人版按「流」报告采集健康（news / calendar 两条流） */
export interface CatalystStreamHealth {
  name: string;
  ok: boolean;
  lastSuccessAt: string;
  failures: number;
  errorCode: string | null;
}

export interface CatalystsStatusDetail {
  collecting: boolean;
  intervalMinutes: number | null;
  lastCrawlAt: string;
  newsToday: number | null;
  analyzedToday: number | null;
  analysisAvailable: boolean;
  queueDepth: number | null;
  sourcesActive: number;
  sourcesTotal: number;
  /** live 扩展：不可用原因（owner_login_required / ai_not_configured…）与模型信息 */
  analysisReason?: string | null;
  analysisModel?: string | null;
  analysisReasoning?: string | null;
  analysisTriggerEnabled?: boolean | null;
  streams?: CatalystStreamHealth[];
  warnings?: string[];
}

export interface EconomicEvent {
  eventId: string;
  country: string;
  title: string;
  impact: 'low' | 'medium' | 'high' | 'holiday';
  impactZh: string;
  scheduledAt: string;
  forecast: string;
  previous: string;
  actual: string | null;
  releaseStatus?: 'scheduled' | 'released' | 'awaiting_source';
}

export interface SourceHealth {
  source: string;
  status: 'active' | 'degraded';
  latencyMs: number | null;
  lastFetchedAt: string;
  itemsToday: number | null;
  note: string;
}

export interface FocusCycleStockAssessment {
  ticker: string;
  name: string;
  /** 由 catalystBias 的正负号推出；无偏向值时为 neutral。 */
  direction: NewsClassification;
  /**
   * 催化剂偏向，UI 刻度 ±5（后端 catalyst_bias 为 ±100，沿用催化剂域 ÷20 的换算，
   * 与新闻分析里的影响分同一把尺子）。证据不足时后端强制为 null —— 此处保持 null
   * 并显「证据不足」，绝不落成 0，那会把「没结论」显示成「结论是中性」。
   */
  catalystBias: number | null;
  /** 0–1（后端 0–100）。标注「· 非胜率」。 */
  confidence: number | null;
  horizon: 'intraday' | 'days' | 'weeks' | 'uncertain' | null;
  insufficientEvidence: boolean;
  note: string;
}

export interface MarketFocusCycle {
  cycleId: string;
  dominantEvent: string;
  /** 1 萌芽 → 4 退潮；live 契约无阶段概念时为 null（UI 隐藏步进条） */
  stage: 1 | 2 | 3 | 4 | null;
  startedAt: string;
  generatedAt: string;
  trigger: 'scheduled' | 'manual';
  model: string;
  newsCount: number;
  summary: string;
  assessments: FocusCycleStockAssessment[];
  /** live 扩展：一句话结论 / 不确定性列表 / 样本单位（条 news vs 组事件）/ 周期状态 */
  headline?: string | null;
  uncertainties?: string[];
  sampleLabel?: string;
  status?: string | null;
  /** 当前尝试失败时，主卡继续展示上次成功结果，并保留失败尝试供重试。 */
  latestAttempt?: {
    cycleId: string;
    status: string;
    startedAt: string;
  } | null;
}

export type AnalysisJobStatus =
  | 'queued'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'insufficient_context';

export interface NewsAnalysisJob {
  jobId: string;
  newsId: string;
  status: AnalysisJobStatus;
  /** 后端明确提供时才有百分比；null 表示只能展示真实状态。 */
  progress: number | null;
  submittedAt: string;
  updatedAt: string;
  error: string | null;
  cancellable: boolean;
}

export interface FocusCycleJob {
  jobId: string;
  status: 'queued' | 'in_progress' | 'completed' | 'failed';
  progress: number | null;
  submittedAt: string;
  updatedAt: string;
  cycleId: string | null;
}

export interface TickerImpactSummary {
  ticker: string;
  name: string;
  sector: string;
  count: number; // 相关新闻数
  analyzed: number; // 其中已完成分析数
  netImpact: number; // 方向性新闻的平均净影响分，范围 ±5（· 非收益）
  latestAt: string;
  sourceDiversity: number;
  bullish: number;
  bearish: number;
  neutral: number;
}

export interface CatalystFeedQuery {
  ticker?: string;
  windowHours?: number;
  classification?: NewsClassification | '';
  analysisStatus?: NewsAnalysisStatus | '';
  includeUnanalyzed?: boolean;
  includeNeutral?: boolean;
  minConfidence?: number; // 0–1
  minAbsImpact?: number;
  multiSourceOnly?: boolean;
  themeId?: string;
  limit?: number;
  cursor?: string | null;
}

/* ---------------- 主题与标题模板 ---------------- */
interface ThemeDef {
  id: string;
  theme: string;
  keywords: string[];
  tickers: string[];
}

const THEME_DEFS: ThemeDef[] = [
  { id: 'theme-ai-capex', theme: __t('AI 算力资本开支'), keywords: [__t('算力'), __t('资本开支'), __t('数据中心')], tickers: ['NVDA', 'AMD', 'AVGO', 'TSM', 'MU'] },
  { id: 'theme-megacap-earnings', theme: __t('大型科技股财报季'), keywords: ['财报', '指引', __t('利润率')], tickers: ['AAPL', 'MSFT', 'META', 'GOOGL', 'AMZN'] },
  { id: 'theme-rate-path', theme: __t('降息预期与利率路径'), keywords: [__t('降息'), 'CPI', __t('美债收益率')], tickers: ['SPY', 'QQQ', 'JPM', 'V'] },
  { id: 'theme-ev-price-war', theme: __t('电动车价格战升级'), keywords: [__t('降价'), __t('毛利率'), __t('交付量')], tickers: ['TSLA', 'UBER'] },
  { id: 'theme-crypto-rebound', theme: __t('加密资产回暖'), keywords: [__t('比特币'), __t('ETF 流入'), __t('合规')], tickers: ['COIN', 'HOOD'] },
  { id: 'theme-glp1', theme: __t('减肥药产业链'), keywords: ['GLP-1', __t('产能'), __t('渠道')], tickers: ['LLY', 'UNH'] },
];

interface NewsTpl {
  t: string;
  s: string;
  dir: NewsClassification;
}

const THEME_TEMPLATES: Record<string, NewsTpl[]> = {
  'theme-ai-capex': [
    { t: '{t} 上调全年资本开支指引，AI 数据中心投资再加码', s: '管理层在电话会上确认，{t} 全年资本开支指引上调，增量几乎全部投向 AI 数据中心与加速计算集群，供应链已收到加急订单。', dir: 'bullish' },
    { t: '供应链消息称 {t} 新一代加速卡订单排至明年下半年', s: '产业链调研显示，{t} 新一代加速计算平台需求远超预期，主要云厂商已锁定未来四个季度产能，现货交期继续拉长。', dir: 'bullish' },
    { t: '{t} 与头部云厂商签署多年期算力供货框架协议', s: '公告称协议覆盖未来三年数据中心产品线供货，锁定了核心客户的基础采购量，为收入能见度提供支撑。', dir: 'bullish' },
    { t: '先进封装产能吃紧，{t} 部分型号交付周期延长', s: '渠道消息称先进封装产能持续紧张，{t} 部分型号交付周期延长，买方开始重估产能爬坡对季度出货的影响。', dir: 'neutral' },
    { t: '{t} 数据中心业务增速疑虑升温，盘前股价承压', s: '有分析师提示，{t} 数据中心业务基数抬高后增速可能放缓，叠加估值处于高位，短线资金获利了结意愿上升。', dir: 'bearish' },
    { t: '电力基础设施成算力扩张瓶颈，{t} 联手能源企业破局', s: '{t} 宣布与公用事业公司合作建设专用供电设施，以保障新数据中心园区的电力需求，长期资本开支确定性增强。', dir: 'bullish' },
    { t: '投行：{t} 算力收入能见度延至 2027 年，上调目标价', s: '多家投行本周上调 {t} 目标价，研报普遍认为 hyperscaler 资本开支周期仍未见顶，算力链条订单能见度延长。', dir: 'bullish' },
    { t: '出口管制新规或影响 {t} 部分高端型号出货节奏', s: '新规细则尚待明确，市场担忧 {t} 部分高端加速卡对特定地区出货受限，短期情绪偏谨慎。', dir: 'bearish' },
  ],
  'theme-megacap-earnings': [
    { t: '{t} 财报前瞻：市场聚焦云业务增速与 AI 变现进展', s: '分析师一致预期 {t} 本季营收稳健增长，焦点集中在云业务增速、AI 相关收入贡献与下季资本开支指引。', dir: 'neutral' },
    { t: '{t} 盘后公布财报：营收超预期，但指引措辞偏谨慎', s: '{t} 季度营收与利润均超市场预期，但管理层对下一季度指引偏保守，盘后股价冲高回落，分析师分歧加大。', dir: 'neutral' },
    { t: '{t} 广告业务回暖，经营利润率连续三个季度改善', s: '财报显示 {t} 广告收入增速回升，叠加降本措施见效，经营利润率连续改善，现金流质量获市场认可。', dir: 'bullish' },
    { t: '{t} 宣布新一轮组织优化，资源向 AI 产品线集中', s: '{t} 内部备忘录显示将裁撤部分传统业务团队，资源向 AI 助手与云基础设施倾斜，市场解读为效率优先。', dir: 'neutral' },
    { t: '分析师：{t} 资本开支或再创新高，自由现金流承压', s: '有研报提示 {t} 数据中心资本开支仍将攀升，短期自由现金流利润率可能收窄，部分机构下调目标价。', dir: 'bearish' },
    { t: '{t} 回购加码百亿美元，管理层称当前股价被低估', s: '{t} 公告称董事会批准新增回购额度，管理层表示长期现金流价值未获充分定价，盘后股价走高。', dir: 'bullish' },
    { t: '监管压力再起，{t} 面临新一轮反垄断听证', s: '立法者要求 {t} 就平台竞争行为出席听证，听证焦点是潜在业务拆分风险与合规成本上升。', dir: 'bearish' },
    { t: '{t} 云服务签约额创纪录，企业 AI 需求持续兑现', s: '{t} 披露企业级云与 AI 服务签约额创单季新高，大型客户续约率维持高位，收入递延规模扩大。', dir: 'bullish' },
  ],
  'theme-rate-path': [
    { t: 'CPI 数据低于预期，降息概率升温，{t} 领涨大盘', s: '最新通胀数据全面低于预期，利率期货市场押注下次会议降息概率上升，{t} 与成长股板块集体走强。', dir: 'bullish' },
    { t: '美联储官员鹰派表态，{t} 回吐日内涨幅', s: '多位官员暗示不急于降息，强调通胀回落仍需更多证据，{t} 午后转跌，利率敏感板块普遍承压。', dir: 'bearish' },
    { t: '十年期美债收益率回落至阶段低位，{t} 获估值支撑', s: '债券交易员表示通胀预期降温带动收益率下行，长久期资产重获资金青睐，{t} 与纳指成分股率先受益。', dir: 'bullish' },
    { t: '非农就业超预期，市场对降息时点分歧加大', s: '就业市场韧性超预期，交易员下调近期降息押注，{t} 震荡整理，板块间轮动明显加快。', dir: 'neutral' },
    { t: '鲍威尔讲话前瞻：利率路径指引成市场焦点', s: '投资者等待美联储主席最新表态，期权市场隐含波动率抬升，{t} 与大盘指数期货窄幅波动。', dir: 'neutral' },
    { t: '利率敏感板块走强，{t} 获避险资金回流', s: '美元指数回落叠加收益率下行，资金回流高股息与利率敏感板块，{t} 连续两日获主力资金净买入。', dir: 'bullish' },
    { t: '通胀黏性担忧重燃，{t} 承压回落', s: '能源价格反弹引发通胀黏性担忧，交易员重新评估降息路径，{t} 随大盘回落，防御板块相对抗跌。', dir: 'bearish' },
    { t: 'FOMC 会议纪要公布在即，市场观望情绪浓厚', s: '会议纪要有望披露更多利率讨论细节，投资者普遍降低仓位等待落地，{t} 维持区间窄幅震荡。', dir: 'neutral' },
  ],
  'theme-ev-price-war': [
    { t: '{t} 再度下调主力车型售价，价格战蔓延至新市场', s: '{t} 宣布下调多款主力车型售价，幅度超出市场预期，竞争对手跟进压力陡增，板块毛利率预期承压。', dir: 'bearish' },
    { t: '{t} 季度交付量超预期，规模效应对冲降价压力', s: '{t} 公布季度交付数据高于市场预期，产能利用率维持高位，规模效应部分对冲了降价对毛利率的影响。', dir: 'bullish' },
    { t: '毛利率保卫战：{t} 称将依靠降本而非提价应对竞争', s: '管理层在财报会上表示，{t} 将通过电池成本下降与制造效率提升守住毛利率，暂无提价计划。', dir: 'neutral' },
    { t: '竞争对手跟进降价，{t} 市场份额争夺白热化', s: '多家车企宣布跟进降价或增加终端优惠，{t} 主力细分市场竞争加剧，分析师下调行业盈利预期。', dir: 'bearish' },
    { t: '{t} 新车型预订量破纪录，需求韧性超预期', s: '{t} 新车型开启预订后订单量迅速突破纪录，交付排期已延至明年，显示终端需求韧性仍存。', dir: 'bullish' },
    { t: '电池原材料成本下行，{t} 单车利润有望企稳', s: '锂价持续回落缓解成本压力，分析师预计 {t} 单车利润将在下半年企稳，毛利率拐点渐近。', dir: 'bullish' },
    { t: '分析师下调 {t} 全年交付预期，目标价遭削减', s: '多家机构以需求不确定性与降价压力为由，下调 {t} 全年交付与盈利预期，评级分歧明显加大。', dir: 'bearish' },
    { t: '{t} 充电网络开放计划扩围，服务收入成新看点', s: '{t} 宣布充电网络向更多第三方品牌开放，服务与能源业务收入占比提升，估值逻辑渐趋多元。', dir: 'neutral' },
  ],
  'theme-crypto-rebound': [
    { t: '比特币 ETF 单周净流入创阶段新高，{t} 放量上行', s: '现货比特币 ETF 连续多日净流入，机构配置需求回暖，{t} 作为合规交易入口直接受益，成交量显著放大。', dir: 'bullish' },
    { t: '{t} 上线新合规产品线，机构客户覆盖面扩容', s: '{t} 宣布推出面向机构的新合规产品线，首批签约多家资管机构，管理层称合规化是长期增长主线。', dir: 'bullish' },
    { t: '加密市场监管框架明朗化，{t} 受益预期升温', s: '立法进程推进使监管不确定性下降，市场预计合规平台将承接更多机构资金，{t} 获多家投行上调评级。', dir: 'bullish' },
    { t: '比特币冲高回落，{t} 日内波动显著放大', s: '比特币盘中触及阶段高点后回落，杠杆资金平仓加剧波动，{t} 跟随宽幅震荡，多空分歧加大。', dir: 'neutral' },
    { t: '分析师：{t} 交易量收入对币价的弹性或被高估', s: '有研报提示 {t} 零售交易收入与币价相关性正在弱化，估值中包含的牛市假设偏乐观，维持中性评级。', dir: 'bearish' },
    { t: '{t} 获大型资管机构数字资产托管合作', s: '{t} 与头部资管机构达成托管与结算合作，机构业务收入占比有望提升，市场视为合规里程碑。', dir: 'bullish' },
    { t: '稳定币立法推进，{t} 或成合规化最大受益者之一', s: '稳定币监管法案取得进展，分析师认为合规发行与交易基础设施价值将重估，{t} 卡位优势明显。', dir: 'bullish' },
    { t: '避险情绪升温，加密相关股普跌，{t} 领跌板块', s: '宏观不确定性推升避险情绪，高风险资产普遍回调，{t} 单日跌幅居前，资金流向防御板块。', dir: 'bearish' },
  ],
  'theme-glp1': [
    { t: '{t} GLP-1 药物产能扩建获批，供应瓶颈逐步缓解', s: '{t} 宣布新生产基地获批投产，GLP-1 类药物产能将显著提升，长期困扰的供应瓶颈有望缓解。', dir: 'bullish' },
    { t: '临床数据亮眼，{t} 减重适应症再添新证据', s: '最新临床数据显示 {t} 在研管线减重效果优于预期，心血管获益证据同步增强，投行上调峰值销售预测。', dir: 'bullish' },
    { t: '渠道库存水平偏高，{t} 短期出货节奏承压', s: '渠道调研显示部分区域库存偏高，{t} 短期出货能否匹配真实终端需求成为关键变量。', dir: 'neutral' },
    { t: '竞品临床数据超预期，{t} 市场份额面临挑战', s: '竞争对手披露的头对头数据表现强劲，市场担忧 {t} 在减重领域的定价能力与份额前景，股价承压。', dir: 'bearish' },
    { t: '{t} 与支付方达成新报销协议，药物可及性提升', s: '{t} 与主要支付方达成新的报销安排，患者自付比例下降，渗透率提升路径进一步清晰。', dir: 'bullish' },
    { t: '口服剂型研发进展披露，{t} 管线价值获重估', s: '{t} 披露口服剂型最新研发进展，若顺利上市将显著扩大适用人群，多家机构上调管线估值。', dir: 'bullish' },
    { t: '投行提示 {t} 估值已计入过多增长预期', s: '有机构下调 {t} 评级，认为当前估值已充分反映 GLP-1 业务的乐观预期，短期缺乏新的催化剂。', dir: 'bearish' },
    { t: '减肥药渗透率仍处低位，{t} 长期空间获市场共识', s: '行业研究显示目标人群渗透率仍低，支付覆盖持续扩大，{t} 所处赛道的长期增长空间获机构共识。', dir: 'neutral' },
  ],
};

const GENERIC_TEMPLATES: NewsTpl[] = [
  { t: '{t} 盘前异动，成交量放大至日均两倍', s: '市场人士指出，{t} 盘前成交明显放大，机构买单集中在开盘前半小时，短线资金快速涌入。', dir: 'neutral' },
  { t: '期权市场大额买单现身 {t}，押注财报前波动放大', s: '期权异动监测显示，{t} 本周到期看涨合约成交激增，单笔权利金超过千万美元，多空博弈升温。', dir: 'neutral' },
  { t: '散户交易热度榜：{t} 单日净买入量跃居前三', s: '零售券商数据显示，{t} 连续两日位居散户净买入前列，社交平台讨论热度同步攀升。', dir: 'bullish' },
  { t: '{t} 遭空头机构点名，公司回应「严重失实」', s: '做空报告质疑 {t} 收入确认方式，公司盘后发布逐条反驳声明，并表示将保留法律追责权利。', dir: 'bearish' },
  { t: '监管文件显示：多家主权基金增持 {t}', s: '最新 13F 披露显示，两家主权财富基金在上一季度合计增持 {t} 逾千万股，长线资金态度积极。', dir: 'bullish' },
  { t: '{t} 管理层变动：CFO 宣布退休，继任遴选已启动', s: '{t} 宣布首席财务官将于本季度末退休，公司已启动外部遴选，分析师称过渡风险总体可控。', dir: 'neutral' },
];

/* ---------------- 新闻池（60 条，近 7 天，确定性种子） ---------------- */
interface NewsMeta {
  dir: NewsClassification;
  insufficient: boolean;
  flaky: boolean; // 首次分析必失败，force 重试成功
}

const newsMeta = new Map<string, NewsMeta>();

function buildAnalysisResult(
  newsId: string,
  titleZh: string,
  tickers: string[],
  dir: NewsClassification,
  themeLabel: string,
  generatedAt: string,
): NewsImpactResult {
  const seed = newsId.split('').reduce((a, c) => a + c.charCodeAt(0), 0) * 131;
  const r = new Rng(seed);
  const mechanisms = [__t('资本开支传导'), __t('供应链定价权'), __t('利润率重估'), __t('估值倍数变化'), __t('订单能见度改善'), __t('利率敏感性'), __t('成本转嫁能力'), __t('需求弹性')];
  const horizons = ['盘中', __t('1–3 天'), __t('1–2 周')];
  const confidence = round4(r.float(0.56, 0.93));
  const impacts: TrustedStockImpact[] = tickers.map((t, i) => {
    const base = dir === 'bullish' ? r.float(0.6, 4.6) : dir === 'bearish' ? -r.float(0.6, 4.6) : r.float(-0.9, 0.9);
    const score = Math.round((i === 0 ? base : base * r.float(0.3, 0.7)) * 100) / 100;
    const direction: NewsClassification = score > 0.3 ? 'bullish' : score < -0.3 ? 'bearish' : 'neutral';
    return {
      ticker: t,
      direction,
      impactScore: score,
      horizon: r.pick(horizons),
      mechanism: r.pick(mechanisms),
      reason:
        direction === 'bullish'
          ? `事件直接强化${t} 的短期基本面预期，资金大概率沿「${themeLabel}」主线继续定价。`
          : direction === 'bearish'
            ? `事件削弱${t} 的短期盈利可见度，需等待管理层或后续数据证伪/证实。`
            : `现有信息对${t} 的方向性影响有限，更多体现为波动率抬升而非趋势变化。`,
    };
  });
  const clsZh = dir === 'bullish' ? '偏多' : dir === 'bearish' ? '偏空' : '中性';
  return {
    classification: dir,
    confidence,
    headlineSummary: `「${titleZh}」整体判定为${clsZh}催化，主要影响 ${tickers.join('、')}。`,
    causalSummary: `该新闻处于「${themeLabel}」叙事主线上：表层是单一事件，底层是资金对该主线持续性的再确认。传导路径上，${tickers[0]}最先被定价，其后沿产业链与同类标的扩散；若未来 48 小时内出现证伪信息，影响将快速衰减，反之则可能演化为趋势级重估。`,
    trustedStockImpacts: impacts,
    model: 'optix-news-v2',
    generatedAt,
  };
}

const NEWS_TOTAL = 60;

const catalystsNews: CatalystNewsItem[] = (() => {
  const r = new Rng(906130);
  const items: CatalystNewsItem[] = [];
  for (let i = 0; i < NEWS_TOTAL; i++) {
    const theme = THEME_DEFS[i % THEME_DEFS.length];
    const isGeneric = i >= THEME_DEFS.length * 8; // 48–59 用通用模板
    const tplPool = isGeneric ? GENERIC_TEMPLATES : THEME_TEMPLATES[theme.id];
    const tpl = tplPool[Math.floor(i / THEME_DEFS.length) % tplPool.length];
    const ticker1 = theme.tickers[r.int(0, theme.tickers.length - 1)];
    const ticker2 = r.chance(0.38) ? theme.tickers[r.int(0, theme.tickers.length - 1)] : null;
    const tickers = ticker2 && ticker2 !== ticker1 ? [ticker1, ticker2] : [ticker1];
    const newsId = `n-${3000 + i}`;
    /* 时间分布：55% 24h 内 · 30% 24–72h · 15% 72–160h（过期） */
    const u = r.float();
    const ageMin = u < 0.55 ? r.int(4, 24 * 60) : u < 0.85 ? r.int(24 * 60 + 12, 72 * 60) : r.int(72 * 60 + 30, 160 * 60);
    const publishedAt = new Date(Date.now() - ageMin * 60_000);
    const fetchedAt = new Date(publishedAt.getTime() + r.int(2, 9) * 60_000);
    const insufficient = i === 20 || i === 33 || i === 47;
    const flaky = i === 18;
    /* 分析状态：前 16 条预置完成；1 条演示任务流（in_progress）；其余 pending */
    let analysisStatus: NewsAnalysisStatus = 'pending';
    if (i < 16) analysisStatus = 'completed';
    else if (insufficient) analysisStatus = 'insufficient_context';
    else if (flaky) analysisStatus = 'failed';
    else if (i === 24) analysisStatus = 'in_progress';
    const dir = tpl.dir;
    newsMeta.set(newsId, { dir, insufficient, flaky });
    const item: CatalystNewsItem = {
      newsId,
      source: r.pick(NEWS_SOURCES),
      sourceCount: r.chance(0.42) ? r.int(2, 4) : 1,
      title: tpl.t.replaceAll('{t}', ticker1),
      titleZh: tpl.t.replaceAll('{t}', ticker1),
      summary: tpl.s.replaceAll('{t}', ticker1),
      summaryZh: tpl.s.replaceAll('{t}', ticker1),
      url: `https://news.optix.example/${newsId}`,
      publishedAt: publishedAt.toISOString(),
      fetchedAt: fetchedAt.toISOString(),
      sourceTickers: tickers,
      isStale: ageMin > 72 * 60,
      themeIds: isGeneric ? [theme.id] : [theme.id],
      analysisStatus,
      analysis: null,
      analysisJobId: null,
    };
    if (analysisStatus === 'completed') {
      item.analysis = buildAnalysisResult(
        newsId,
        item.titleZh,
        tickers,
        dir,
        theme.theme,
        new Date(fetchedAt.getTime() + r.int(6, 40) * 60_000).toISOString(),
      );
    }
    items.push(item);
  }
  return items.sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1));
})();

const newsById = new Map(catalystsNews.map((n) => [n.newsId, n]));

/* ---------------- 分析任务（状态机：queued → in_progress → 终态） ---------------- */
interface AnalysisJobRec extends NewsAnalysisJob {
  force: boolean;
  _born: number;
  _applied: boolean;
}
const analysisJobs = new Map<string, AnalysisJobRec>();
let demoJobEnsured = false;

function applyJobTerminal(rec: AnalysisJobRec) {
  if (rec._applied) return;
  rec._applied = true;
  const item = newsById.get(rec.newsId);
  if (!item) return;
  const meta = newsMeta.get(rec.newsId);
  if (rec.status === 'completed') {
    const themeLabel = THEME_DEFS.find((t) => t.id === item.themeIds[0])?.theme ?? __t('市场焦点');
    item.analysis = buildAnalysisResult(item.newsId, item.titleZh, item.sourceTickers, meta?.dir ?? 'neutral', themeLabel, new Date().toISOString());
    item.analysisStatus = 'completed';
  } else if (rec.status === 'insufficient_context') {
    item.analysis = null;
    item.analysisStatus = 'insufficient_context';
  } else if (rec.status === 'failed') {
    item.analysisStatus = 'failed';
  } else if (rec.status === 'cancelled') {
    item.analysisStatus = 'pending';
    item.analysisJobId = null;
  }
}

function advanceAnalysisJob(rec: AnalysisJobRec): NewsAnalysisJob {
  const age = Date.now() - rec._born;
  if (rec.status === 'queued' || rec.status === 'in_progress') {
    const meta = newsMeta.get(rec.newsId);
    const duration = meta?.insufficient ? 3600 : 4800;
    if (age < 1100) {
      rec.status = 'queued';
      rec.progress = Math.min(12, Math.round(age / 100));
    } else if (age < duration) {
      rec.status = 'in_progress';
      rec.progress = Math.min(94, Math.round(15 + (age - 1100) / (duration - 1100) * 79));
    } else {
      if (meta?.insufficient) {
        rec.status = 'insufficient_context';
        rec.error = null;
      } else if (meta?.flaky && !rec.force) {
        rec.status = 'failed';
        rec.error = __t('模型输出校验失败，可带 force 重试');
      } else {
        rec.status = 'completed';
        rec.error = null;
      }
      rec.progress = 100;
      rec.cancellable = false;
      applyJobTerminal(rec);
    }
    rec.updatedAt = new Date().toISOString();
  }
  const { force: _f, _born, _applied, ...pub } = rec;
  void _f;
  void _born;
  void _applied;
  return { ...pub };
}

/** 首次访问时植入一条「演示任务流」：新闻 i=24 预置 in_progress 任务 */
function ensureDemoJob() {
  if (demoJobEnsured) return;
  demoJobEnsured = true;
  const item = catalystsNews.find((n) => n.analysisStatus === 'in_progress' && !n.analysisJobId);
  if (!item) return;
  const now = new Date().toISOString();
  const rec: AnalysisJobRec = {
    jobId: `ajob-demo-${item.newsId}`,
    newsId: item.newsId,
    status: 'queued',
    progress: 0,
    submittedAt: now,
    updatedAt: now,
    error: null,
    cancellable: true,
    force: false,
    _born: Date.now(),
    _applied: false,
  };
  analysisJobs.set(rec.jobId, rec);
  item.analysisJobId = rec.jobId;
}

export function createNewsAnalysisJob(newsId: string, force = false): NewsAnalysisJob {
  const item = newsById.get(newsId);
  if (!item) throw new Error(__t('新闻不存在'));
  const now = new Date().toISOString();
  const rec: AnalysisJobRec = {
    jobId: `ajob-${Date.now().toString(36)}-${analysisJobs.size + 1}`,
    newsId,
    status: 'queued',
    progress: 0,
    submittedAt: now,
    updatedAt: now,
    error: null,
    cancellable: true,
    force,
    _born: Date.now(),
    _applied: false,
  };
  analysisJobs.set(rec.jobId, rec);
  item.analysisStatus = 'queued';
  item.analysisJobId = rec.jobId;
  return advanceAnalysisJob(rec);
}

export function getNewsAnalysisJob(jobId: string): NewsAnalysisJob {
  ensureDemoJob();
  const rec = analysisJobs.get(jobId);
  if (!rec) throw new Error(__t('任务不存在'));
  return advanceAnalysisJob(rec);
}

export function cancelNewsAnalysisJob(jobId: string): NewsAnalysisJob {
  const rec = analysisJobs.get(jobId);
  if (!rec) throw new Error(__t('任务不存在'));
  if (rec.status === 'queued' || rec.status === 'in_progress') {
    rec.status = 'cancelled';
    rec.cancellable = false;
    rec.progress = 100;
    rec.updatedAt = new Date().toISOString();
    applyJobTerminal(rec);
  }
  return advanceAnalysisJob(rec);
}

/* ---------------- 过滤与查询 ---------------- */
function syncNewsJob(item: CatalystNewsItem) {
  if (item.analysisJobId) {
    const rec = analysisJobs.get(item.analysisJobId);
    if (rec) advanceAnalysisJob(rec);
  }
}

function maxAbsImpact(item: CatalystNewsItem): number {
  if (!item.analysis) return 0;
  return item.analysis.trustedStockImpacts.reduce((m, x) => Math.max(m, Math.abs(x.impactScore)), 0);
}

function matchNews(item: CatalystNewsItem, q: CatalystFeedQuery, ignoreTicker = false): boolean {
  if (!ignoreTicker && q.ticker) {
    const t = q.ticker.trim().toUpperCase();
    if (t && !item.sourceTickers.includes(t)) return false;
  }
  if (q.windowHours) {
    const age = Date.now() - new Date(item.publishedAt).getTime();
    if (age > q.windowHours * 3600_000) return false;
  }
  if (q.themeId && !item.themeIds.includes(q.themeId)) return false;
  if (q.multiSourceOnly && item.sourceCount < 2) return false;
  if (q.analysisStatus && item.analysisStatus !== q.analysisStatus) return false;
  if (q.classification && item.analysis?.classification !== q.classification) return false;
  if (q.minConfidence && (item.analysis?.confidence ?? 0) < q.minConfidence) return false;
  if (q.minAbsImpact && maxAbsImpact(item) < q.minAbsImpact) return false;
  return true;
}

export function getCatalystsFeedV2(q: CatalystFeedQuery = {}): {
  items: CatalystNewsItem[];
  nextCursor: string | null;
  total: number;
} {
  ensureDemoJob();
  catalystsNews.forEach(syncNewsJob);
  const filtered = catalystsNews.filter((n) => matchNews(n, q));
  const limit = Math.max(1, Math.min(q.limit ?? 12, 100));
  const offset = Math.max(0, parseInt(q.cursor ?? '0', 10) || 0);
  const items = filtered.slice(offset, offset + limit);
  const nextCursor = offset + limit < filtered.length ? String(offset + limit) : null;
  return { items, nextCursor, total: filtered.length };
}

export function getNewsDetailV2(newsId: string): CatalystNewsItem {
  ensureDemoJob();
  const item = newsById.get(newsId);
  if (!item) throw new Error(__t('新闻不存在'));
  syncNewsJob(item);
  return item;
}

/* ---------------- 热点组 ---------------- */
export function getHotspotsV2(): HotspotGroup[] {
  ensureDemoJob();
  const r = new Rng(424242);
  return THEME_DEFS.map((t, i) => {
    const inTheme = catalystsNews.filter((n) => n.themeIds.includes(t.id));
    const heat = Math.round(r.float(56, 97));
    return {
      hotspotId: t.id,
      theme: t.theme,
      keywords: [...t.keywords],
      heat,
      heatLevel: Math.max(1, Math.min(5, Math.round(heat / 20))),
      newsCount: inTheme.length + r.int(4, 18),
      representative: inTheme[0]
        ? { newsId: inTheme[0].newsId, titleZh: inTheme[0].titleZh, publishedAt: inTheme[0].publishedAt }
        : null,
      tickers: [...t.tickers].slice(0, 4),
      updatedAt: new Date(Date.now() - (58 + i * 37) * 1000).toISOString(),
    };
  }).sort((a, b) => b.heat - a.heat);
}

export function getHotspotsStatusV2(): HotspotsStatusDetail {
  return {
    state: 'ready',
    scanning: true,
    updatedAt: new Date(Date.now() - 58_000).toISOString(),
    etaSeconds: null,
    groupCount: THEME_DEFS.length,
  };
}

export function getCatalystsStatusV2(): CatalystsStatusDetail {
  ensureDemoJob();
  const newsToday = catalystsNews.filter((n) => Date.now() - new Date(n.publishedAt).getTime() < 24 * 3600_000).length;
  return {
    collecting: true,
    intervalMinutes: 5,
    lastCrawlAt: new Date(Date.now() - 96_000).toISOString(),
    newsToday,
    analyzedToday: catalystsNews.filter((n) => n.analysisStatus === 'completed').length,
    analysisAvailable: true,
    queueDepth: catalystsNews.filter((n) => n.analysisStatus === 'queued' || n.analysisStatus === 'in_progress').length,
    sourcesActive: 5,
    sourcesTotal: 6,
  };
}

/* ---------------- 经济日历 ---------------- */
export function getEconomicCalendar(): EconomicEvent[] {
  const defs: { d: number; h: number; m: number; title: string; impact: EconomicEvent['impact']; f: string; p: string; a: string | null }[] = [
    { d: -1, h: 8, m: 30, title: __t('纽约联储制造业指数'), impact: 'low', f: '8.0', p: '6.6', a: '12.9' },
    { d: 0, h: 8, m: 30, title: __t('CPI 月率（10 月）'), impact: 'high', f: '0.2%', p: '0.3%', a: null },
    { d: 0, h: 21, m: 0, title: __t('美联储理事沃勒讲话'), impact: 'medium', f: '—', p: '—', a: null },
    { d: 1, h: 8, m: 30, title: __t('初请失业金人数'), impact: 'medium', f: __t('22.5 万'), p: __t('22.1 万'), a: null },
    { d: 1, h: 10, m: 0, title: __t('密歇根大学消费者信心指数'), impact: 'medium', f: '71.0', p: '70.5', a: null },
    { d: 2, h: 8, m: 30, title: __t('PPI 月率（10 月）'), impact: 'medium', f: '0.2%', p: '0.1%', a: null },
    { d: 2, h: 14, m: 0, title: __t('FOMC 会议纪要'), impact: 'high', f: '—', p: '—', a: null },
    { d: 3, h: 8, m: 30, title: __t('零售销售月率（10 月）'), impact: 'high', f: '0.4%', p: '0.6%', a: null },
    { d: 3, h: 21, m: 0, title: __t('美联储主席鲍威尔讲话'), impact: 'high', f: '—', p: '—', a: null },
    { d: 4, h: 0, m: 0, title: __t('感恩节 · 美股休市'), impact: 'holiday', f: '—', p: '—', a: null },
    { d: 5, h: 8, m: 30, title: __t('非农就业人口变动（11 月）'), impact: 'high', f: __t('18.5 万'), p: __t('17.2 万'), a: null },
    { d: 5, h: 8, m: 30, title: __t('失业率（11 月）'), impact: 'medium', f: '4.1%', p: '4.1%', a: null },
    { d: 6, h: 10, m: 0, title: __t('ISM 制造业 PMI（11 月）'), impact: 'medium', f: '47.8', p: '47.2', a: null },
  ];
  const impactZhMap: Record<EconomicEvent['impact'], string> = { high: '高', medium: __t('中'), low: '低', holiday: '休市' };
  return defs
    .map((e, i) => {
      const base = new Date();
      base.setHours(0, 0, 0, 0);
      const at = new Date(base.getTime() + e.d * 86_400_000 + e.h * 3600_000 + e.m * 60_000);
      const releaseStatus: EconomicEvent['releaseStatus'] =
        e.a !== null ? 'released' : at.getTime() <= Date.now() ? 'awaiting_source' : 'scheduled';
      return {
        eventId: `ev-${100 + i}`,
        country: __t('美国'),
        title: e.title,
        impact: e.impact,
        impactZh: impactZhMap[e.impact],
        scheduledAt: at.toISOString(),
        forecast: e.f,
        previous: e.p,
        actual: e.a,
        releaseStatus,
      };
    })
    .sort((a, b) => (a.scheduledAt < b.scheduledAt ? -1 : 1));
}

/* ---------------- 数据源健康 ---------------- */
export function getCatalystsSources(): SourceHealth[] {
  const r = new Rng(31337);
  const defs: { name: string; degraded?: boolean }[] = [
    { name: __t('路透社') },
    { name: __t('彭博社') },
    { name: 'CNBC' },
    { name: __t('华尔街日报') },
    { name: __t('巴伦周刊') },
    { name: 'MarketWatch', degraded: true },
  ];
  return defs.map((d, i) => ({
    source: d.name,
    status: d.degraded ? 'degraded' : 'active',
    latencyMs: d.degraded ? Math.round(r.float(2400, 3800)) : Math.round(r.float(220, 980)),
    lastFetchedAt: new Date(Date.now() - (40 + i * 26) * 1000).toISOString(),
    itemsToday: Math.round(r.float(6, 26)),
    note: d.degraded ? __t('数据覆盖时间落后，采集流处于降级状态') : __t('运行正常'),
  }));
}

/* ---------------- 市场焦点周期 ---------------- */
function buildCycle(seedNum: number, trigger: MarketFocusCycle['trigger'], generatedAt: Date, idSuffix: string): MarketFocusCycle {
  const r = new Rng(seedNum);
  /* 最后一只故意是「证据不足」：那是后端真会出现的一种状态（catalyst_bias 为 null），
     UI 必须显「证据不足」而不是 0.00，mock 里留一个样本守住这条路径。 */
  const assessments: FocusCycleStockAssessment[] = [
    { ticker: 'NVDA', name: __t('英伟达'), direction: 'bullish', catalystBias: round2(r.float(2.4, 4.2)), confidence: 0.82, horizon: 'days', insufficientEvidence: false, note: '算力订单能见度延长，焦点周期核心受益标的' },
    { ticker: 'TSM', name: __t('台积电'), direction: 'bullish', catalystBias: round2(r.float(1.2, 2.4)), confidence: 0.74, horizon: 'weeks', insufficientEvidence: false, note: '先进制程产能利用率维持满载，定价权稳固' },
    { ticker: 'AMD', name: __t('超威半导体'), direction: 'bullish', catalystBias: round2(r.float(0.8, 1.8)), confidence: 0.61, horizon: 'days', insufficientEvidence: false, note: '加速卡份额提升预期，跟随主线但弹性次之' },
    { ticker: 'INTC', name: __t('英特尔'), direction: 'bearish', catalystBias: -round2(r.float(0.6, 1.4)), confidence: 0.55, horizon: 'weeks', insufficientEvidence: false, note: '资本开支主线之外，相对资金吸引力下降' },
    { ticker: 'SMCI', name: __t('超微电脑'), direction: 'neutral', catalystBias: null, confidence: 0.3, horizon: 'uncertain', insufficientEvidence: true, note: '出货节奏与毛利率信号混杂，证据不足以给出偏向' },
  ];
  return {
    cycleId: `fc-${idSuffix}`,
    dominantEvent: '财报季 · 科技资本开支验证',
    stage: 3,
    startedAt: new Date(Date.now() - 19 * 86_400_000).toISOString(),
    generatedAt: generatedAt.toISOString(),
    trigger,
    model: 'optix-focus-v1',
    newsCount: 42,
    summary:
      '本轮焦点周期由 hyperscaler 资本开支指引上修点燃，半导体设备与先进封装轮动走强，资金沿「算力—散热—电力」链条扩散。当前处于主升阶段：龙头财报兑现与订单能见度相互强化，但拥挤度同步抬升。周期后续的关键观察点在于下批云厂商指引是否继续上修——若证伪，主升将快速切换为退潮；若证实，主线有望向二线供应链外延。',
    assessments,
  };
}

let latestCycle: MarketFocusCycle = buildCycle(808081, 'scheduled', new Date(Date.now() - 2 * 3600_000), 'latest');
const previousCycle: MarketFocusCycle = {
  ...buildCycle(707071, 'scheduled', new Date(Date.now() - 13 * 86_400_000), 'prev'),
  dominantEvent: '降息交易回摆',
  stage: 4,
  startedAt: new Date(Date.now() - 41 * 86_400_000).toISOString(),
  newsCount: 35,
  summary:
    '上一轮焦点周期围绕降息路径展开：CPI 回落确认后利率敏感资产持续占优，直至 FOMC 鹰派表述令交易拥挤度出清。周期完整走过「萌芽—发酵—主升—退潮」四阶段，最终在非农数据超预期后退潮。本轮经验显示，宏观周期的持续性高度依赖数据验证节奏。',
};

export function getLatestFocusCycleV2(): MarketFocusCycle {
  return latestCycle;
}
export function getPreviousSuccessfulFocusCycle(): MarketFocusCycle {
  return previousCycle;
}

/* 焦点周期手动触发任务（owner） */
interface FocusJobRec extends FocusCycleJob {
  _born: number;
  _applied: boolean;
}
const focusJobs = new Map<string, FocusJobRec>();

export function triggerFocusCycle(): FocusCycleJob {
  const now = new Date().toISOString();
  const rec: FocusJobRec = {
    jobId: `fcjob-${Date.now().toString(36)}`,
    status: 'queued',
    progress: 0,
    submittedAt: now,
    updatedAt: now,
    cycleId: null,
    _born: Date.now(),
    _applied: false,
  };
  focusJobs.set(rec.jobId, rec);
  return { ...rec };
}

export function getFocusCycleJob(jobId: string): FocusCycleJob {
  const rec = focusJobs.get(jobId);
  if (!rec) throw new Error(__t('任务不存在'));
  const age = Date.now() - rec._born;
  if (rec.status === 'queued' || rec.status === 'in_progress') {
    if (age < 1300) {
      rec.status = 'queued';
      rec.progress = Math.min(12, Math.round(age / 120));
    } else if (age < 6400) {
      rec.status = 'in_progress';
      rec.progress = Math.min(94, Math.round(14 + (age - 1300) / 5100 * 80));
    } else {
      rec.status = 'completed';
      rec.progress = 100;
      if (!rec._applied) {
        rec._applied = true;
        latestCycle = buildCycle(808081 + Math.floor(Date.now() / 1000) % 997, 'manual', new Date(), `m-${Date.now().toString(36)}`);
        rec.cycleId = latestCycle.cycleId;
      }
    }
    rec.updatedAt = new Date().toISOString();
  }
  const { _born, _applied, ...pub } = rec;
  void _born;
  void _applied;
  return { ...pub };
}

/* ---------------- 股票影响汇总（batch） ---------------- */
export function getTickerImpactSummaries(q: CatalystFeedQuery = {}): TickerImpactSummary[] {
  ensureDemoJob();
  catalystsNews.forEach(syncNewsJob);
  const filtered = catalystsNews.filter((n) => matchNews(n, q, true));
  const acc = new Map<string, {
    count: number; analyzed: number; impactCount: number; net: number; latestAt: string;
    sources: Set<string>; bullish: number; bearish: number; neutral: number;
  }>();
  for (const item of filtered) {
    for (const t of item.sourceTickers) {
      let a = acc.get(t);
      if (!a) {
        a = { count: 0, analyzed: 0, impactCount: 0, net: 0, latestAt: item.publishedAt, sources: new Set(), bullish: 0, bearish: 0, neutral: 0 };
        acc.set(t, a);
      }
      a.count += 1;
      a.sources.add(item.source);
      if (item.publishedAt > a.latestAt) a.latestAt = item.publishedAt;
      if (item.analysis) {
        a.analyzed += 1;
        const imp = item.analysis.trustedStockImpacts.find((x) => x.ticker === t);
        if (imp) {
          a.impactCount += 1;
          a.net += imp.impactScore;
          if (imp.direction === 'bullish') a.bullish += 1;
          else if (imp.direction === 'bearish') a.bearish += 1;
          else a.neutral += 1;
        } else {
          a.neutral += 1;
        }
      }
    }
  }
  return [...acc.entries()]
    .map(([ticker, a]) => {
      const info = TICKER_POOL.find((x) => x.ticker === ticker);
      return {
        ticker,
        name: info?.name ?? ticker,
        sector: info?.sector ?? '—',
        count: a.count,
        analyzed: a.analyzed,
        netImpact: a.impactCount ? Math.round((a.net / a.impactCount) * 100) / 100 : 0,
        latestAt: a.latestAt,
        sourceDiversity: a.sources.size,
        bullish: a.bullish,
        bearish: a.bearish,
        neutral: a.neutral,
      };
    })
    .sort((x, y) => (y.count - x.count) || (Math.abs(y.netImpact) - Math.abs(x.netImpact)));
}

/** 热点主题列表（供筛选 chip 反显主题名） */
export function getHotspotThemeName(themeId: string): string {
  return THEME_DEFS.find((t) => t.id === themeId)?.theme ?? themeId;
}
