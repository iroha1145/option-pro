/** 突破雷达域 */
import { get, mockOr, toQuery } from '../client';
import { marketGet } from '../marketRead';
import { asRec, num, pickN, pickS, str, unwrap, type Rec } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import { SIGNAL_LABELS } from '@/mocks/fixtures';
import type { BreakoutEvent, BreakoutEventDetail, BreakoutSignal, BreakoutStatus, SignalType } from '../types';

export interface BreakoutEventFilters {
  page?: number;
  pageSize?: number;
  type?: string;
  result?: string;
  /** 契约参数（mock 忽略）：lifecycle_state / cursor */
  lifecycle_state?: string;
  cursor?: string;
}

/* ================= live 归一化（契约原始事件 → mock 形状，mock 是 UI 唯一事实源） =================
 * 原则「留空优于编造」：契约缺失的字段给 null / '' / []，组件端渲染「—」；
 * 仅做字段改名 / 真实推导（setup→type 分类、lifecycle→result 分类），不虚构数值。
 */

/** setup_type → UI SignalType（与 mock fixtures2 同一张推导表） */
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

/** lifecycle_state → mock result 语义（hit/failed/pending，与 fixtures2 生成规则一致） */
function lifecycleToResult(state: string | null): BreakoutEvent['result'] {
  if (state === 'FAILED' || state === 'EXPIRED') return 'failed';
  if (state === 'CONFIRMED' || state === 'RETEST_HELD' || state === 'REACCELERATING' || state === 'EXTENDED') return 'hit';
  return 'pending';
}

const SESSIONS = ['premarket', 'regular', 'postmarket', 'closed'] as const;

/** {low, high} 区带：两端都有限值才保留，否则 null（组件按缺失渲染） */
function normZone(v: unknown): { low: number; high: number } | null {
  const r = asRec(v);
  const low = num(r.low);
  const high = num(r.high);
  return low !== null && high !== null ? { low, high } : null;
}

/**
 * transitions 归一：兼容 mock 形状 {state, at, note} 与契约形状
 * {from_state, to_state, evidence_at, reason}；契约为倒序 → 升序排列，
 * 并补首条 from_state 起点（真实字段改名，非编造）。
 */
export function normalizeBreakoutTransitions(v: unknown): { state: string; at: string; note?: string }[] {
  if (!Array.isArray(v)) return [];
  const mockShaped = v.every((t) => str(asRec(t).state) !== null);
  if (mockShaped) {
    return (v as Rec[]).map((t) => ({
      state: pickS(t, 'state') ?? '',
      at: pickS(t, 'at', 'evidence_at') ?? '',
      note: pickS(t, 'note') ?? undefined,
    }));
  }
  const sorted = (v as Rec[])
    .map((t) => ({
      from: pickS(t, 'from_state'),
      to: pickS(t, 'to_state', 'state') ?? '',
      at: pickS(t, 'evidence_at', 'at') ?? '',
      note: pickS(t, 'reason', 'note') ?? undefined,
    }))
    .filter((t) => t.to)
    .sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  if (!sorted.length) return [];
  const out: { state: string; at: string; note?: string }[] = [];
  if (sorted[0].from) out.push({ state: sorted[0].from, at: sorted[0].at });
  for (const t of sorted) out.push({ state: t.to, at: t.at, note: t.note });
  return out;
}

/** contribution_breakdown：契约键带 _score 后缀 → 补齐组件读取的短键别名（原键保留，无信息损失） */
function normContribution(v: unknown): Record<string, number> | null {
  const r = asRec(v);
  const keys = Object.keys(r);
  if (!keys.length) return null;
  const out: Record<string, number> = {};
  for (const k of keys) {
    const n = num(r[k]);
    if (n === null) continue;
    out[k] = n;
    if (k.endsWith('_score')) out[k.slice(0, -'_score'.length)] = n;
  }
  // 组件短键 breakout_quality 无契约对应时以 base_quality 补位（同一评分族，仅命名差异）
  if (out.breakout_quality === undefined && out.base_quality !== undefined) out.breakout_quality = out.base_quality;
  return Object.keys(out).length ? out : null;
}

/**
 * 契约原始 BreakoutEvent → mock（fixtures2 getBreakoutsCurrent）形状。
 * 字段对照见文件尾注；缺失一律 null/''/[]，组件端渲染「—」。
 */
export function normalizeBreakoutEvent(raw: unknown): BreakoutSignal & BreakoutEventDetail {
  const r = asRec(raw);
  const eventId = pickS(r, 'event_id', 'id') ?? '';
  const ticker = pickS(r, 'ticker') ?? '';
  const setup = pickS(r, 'setup_type') ?? '';
  const lifecycle = pickS(r, 'lifecycle_state');
  const type: SignalType = SETUP_TO_SIGNAL[setup] ?? 'breakout';
  const sessionRaw = pickS(r, 'session');
  const eventAt = pickS(r, 'event_at', 'first_seen_at', 'at') ?? '';
  const eventPrice = pickN(r, 'event_price', 'price');
  const currentPrice = pickN(r, 'current_price') ?? eventPrice;
  const targetPrice = pickN(r, 'target_price', 'targetPrice'); // 契约无 → null（不编造目标价）
  const invalidation = pickN(r, 'invalidation_price', 'invalidPrice');
  const rangePersistence = (() => {
    // 兼容早期 mock 五维，并把生产契约的真实标量/斜率/分位保留为独立口径。
    const rp = asRec(r.range_persistence);
    const dims = ['trend', 'hold', 'volatility', 'volume', 'participation'];
    if (dims.every((d) => num(rp[d]) !== null)) {
      return {
        trend: num(rp.trend)!,
        hold: num(rp.hold)!,
        volatility: num(rp.volatility)!,
        volume: num(rp.volume)!,
        participation: num(rp.participation)!,
      };
    }
    const value = num(r.range_persistence);
    const slope5d = pickN(r, 'range_persistence_slope_5d');
    const ratio10d = pickN(r, 'range_persistence_ratio_10d');
    const selfPercentile = pickN(r, 'range_persistence_self_percentile');
    const globalPercentile = pickN(r, 'range_persistence_global_percentile');
    const sectorPercentile = pickN(r, 'range_persistence_sector_percentile');
    const status = pickS(r, 'range_persistence_status') ?? 'unavailable';
    return [value, slope5d, ratio10d, selfPercentile, globalPercentile, sectorPercentile].some((v) => v !== null)
      ? {
          kind: 'live' as const,
          value,
          slope5d,
          ratio10d,
          selfPercentile,
          globalPercentile,
          sectorPercentile,
          status,
        }
      : null;
  })();
  return {
    /* ---- BreakoutSignal / BreakoutEvent 精简字段（旧组件仍消费：SidebarEvents 等） ---- */
    id: eventId,
    ticker,
    name: pickS(r, 'name') ?? ticker,
    type,
    label: SIGNAL_LABELS[type],
    price: currentPrice,
    changePct: pickN(r, 'session_change_pct', 'changePct'),
    strengthScore: pickN(r, 'intrinsic_strength_score', 'strengthScore'),
    at: eventAt,
    summary: pickS(r, 'summary') ?? '',
    result: (pickS(r, 'result') as BreakoutEvent['result'] | null) ?? lifecycleToResult(lifecycle),
    /* ---- 契约全字段（页面按 BreakoutEventFull 读取） ---- */
    event_id: eventId,
    exchange: pickS(r, 'exchange'),
    sector: pickS(r, 'sector') ?? '',
    session: sessionRaw && (SESSIONS as readonly string[]).includes(sessionRaw) ? sessionRaw : 'closed',
    setup_type: setup,
    lifecycle_state: lifecycle ?? '',
    event_at: eventAt,
    triggered_at: pickS(r, 'triggered_at'), // WATCHING 事件为 null → 组件显「—」
    first_seen_at: pickS(r, 'first_seen_at'),
    last_seen_at: pickS(r, 'last_seen_at'),
    event_age_seconds: pickN(r, 'event_age_seconds'),
    event_price: eventPrice,
    current_price: currentPrice,
    session_change_pct: pickN(r, 'session_change_pct'),
    gap_pct: pickN(r, 'gap_pct'),
    rvol_time_of_day: pickN(r, 'rvol_time_of_day'),
    pivot_price: pickN(r, 'pivot_price'),
    target_price: targetPrice,
    support_zone: normZone(r.support_zone),
    resistance_zone: normZone(r.resistance_zone),
    invalidation_price: invalidation,
    intrinsic_strength_score: pickN(r, 'intrinsic_strength_score'),
    base_quality_score: pickN(r, 'base_quality_score', 'breakout_quality_score'),
    breakout_quality_score: pickN(r, 'breakout_quality_score'),
    breakout_confirmation_score: pickN(r, 'breakout_confirmation_score'),
    liquidity_quality_score: pickN(r, 'liquidity_quality_score'),
    chase_risk_score: pickN(r, 'chase_risk_score'),
    sector_fit_score: pickN(r, 'sector_fit_score'),
    market_fit_score: pickN(r, 'market_fit_score'),
    alert_priority_score: pickN(r, 'alert_priority_score'),
    data_confidence_score: pickN(r, 'data_confidence_score'),
    range_persistence: rangePersistence,
    contribution_breakdown: normContribution(r.contribution_breakdown),
    configured_weights: r.configured_weights ?? null,
    effective_weights: r.effective_weights ?? null,
    transitions: normalizeBreakoutTransitions(r.transitions),
    warnings: Array.isArray(r.warnings) ? (r.warnings as unknown[]).filter((w): w is string => typeof w === 'string') : [],
    score_version: pickS(r, 'score_version'),
    market_shape: r.market_shape ?? null,
    /* ---- 详情别名（与 BreakoutEventDetail 对齐） ---- */
    triggerPrice: eventPrice,
    targetPrice,
    invalidPrice: invalidation,
    evidence: Array.isArray(r.evidence) ? (r.evidence as unknown[]).filter((x): x is string => typeof x === 'string') : [],
  } as unknown as BreakoutSignal & BreakoutEventDetail;
}

/** 契约 /breakouts/status 全量对象 → mock BreakoutStatusFull 形状（改名映射，缺失置空） */
export function normalizeBreakoutStatus(raw: unknown): BreakoutStatus {
  const r = asRec(raw);
  const worker = asRec(r.worker);
  const scan = asRec(r.latest_completed_scan);
  const workerBad = ['error', 'failed', 'down', 'stale', 'unhealthy'];
  const health = pickS(worker, 'health_status', 'status');
  const scheduled = Date.parse(pickS(scan, 'scheduled_at') ?? '');
  const published = Date.parse(pickS(scan, 'published_at') ?? '');
  const nextAt = pickS(r, 'next_session_at') ?? pickS(asRec(worker.details), 'next_session_at');
  const marketSession = pickS(r, 'market_session');
  return {
    state: worker.current_scan_run_id ? 'scanning' : 'idle',
    scanned: pickN(scan, 'candidate_count', 'scanned') ?? 0,
    universe: pickN(r, 'universe') ?? 0,
    cooldownEndsAt: pickS(r, 'cooldownEndsAt'),
    lastScanAt: pickS(worker, 'last_completed_at') ?? pickS(scan, 'published_at') ?? pickS(r, 'lastScanAt') ?? '',
    enabled: r.enabled === true,
    worker: {
      healthy: worker.error_code == null && (health === null || !workerBad.includes(health)),
      heartbeat_at: pickS(worker, 'heartbeat_at') ?? '',
    },
    latest_completed_scan: Object.keys(scan).length
      ? {
          at: pickS(scan, 'published_at', 'scheduled_at', 'at') ?? '',
          duration_ms: Number.isFinite(published - scheduled) && published >= scheduled ? published - scheduled : 0,
          scanned: pickN(scan, 'candidate_count', 'scanned') ?? 0,
          triggered: pickN(scan, 'event_count', 'triggered') ?? 0,
        }
      : null,
    market_session: marketSession && (SESSIONS as readonly string[]).includes(marketSession) ? marketSession : 'closed',
    next_session_at: nextAt,
  } as unknown as BreakoutStatus;
}

export const breakoutsApi = {
  // 契约 {as_of, session, status, events:[BreakoutEvent], ...} → events 数组（逐条归一到 mock 形状）
  current: (): Promise<BreakoutSignal[]> =>
    mockOr(
      () => fx2.getBreakoutsCurrent(),
      () => get('/breakouts/current').then((d) => unwrap(d, 'events').map(normalizeBreakoutEvent) as unknown as BreakoutSignal[]),
    ),
  status: (): Promise<BreakoutStatus> =>
    mockOr(() => fx2.getBreakoutsStatus(), () => get('/breakouts/status').then(normalizeBreakoutStatus)),
  events: (filters: BreakoutEventFilters = {}): Promise<{ items: BreakoutEvent[]; total: number; page: number }> =>
    mockOr(
      () => fx2.getBreakoutEvents(filters.page ?? 1, filters.pageSize ?? 12),
      // 契约：?lifecycle_state&limit&cursor → {events, next_cursor}；page/type/result 为 UI 侧概念，不下发
      () => {
        const page = filters.page ?? 1;
        const pageSize = filters.pageSize ?? 12;
        const qs = toQuery({ lifecycle_state: filters.lifecycle_state, limit: pageSize, cursor: filters.cursor });
        return get(`/breakouts/events${qs ? `?${qs}` : ''}`).then((d) => {
          const events = unwrap(d, 'events', 'items').map(normalizeBreakoutEvent);
          const nextCursor = asRec(d).next_cursor;
          // 契约无 total：有 next_cursor 说明至少还有一页
          const total = nextCursor ? page * pageSize + 1 : (page - 1) * pageSize + events.length;
          return { items: events as unknown as BreakoutEvent[], total, page };
        });
      },
    ),
  // 契约 {event, structure, scores, transitions[]} → 拍平为详情对象（event 归一 + transitions 归一）
  eventDetail: (id: string): Promise<BreakoutEventDetail> =>
    mockOr(
      () => fx2.getBreakoutEventDetail(id),
      () =>
        get(`/breakouts/events/${encodeURIComponent(id)}`).then((d) => {
          const r = asRec(d);
          const base = normalizeBreakoutEvent(r.event && typeof r.event === 'object' ? r.event : r);
          const transitions = normalizeBreakoutTransitions(r.transitions);
          return {
            ...base,
            ...(transitions.length ? { transitions } : {}),
            structure: r.structure ?? null,
            scores: r.scores ?? null,
          } as unknown as BreakoutEventDetail;
        }),
    ),
  byTicker: (ticker: string): Promise<BreakoutEvent[]> =>
    mockOr(
      () => fx2.getBreakoutsByTicker(ticker),
      () =>
        marketGet(`/breakouts/tickers/${encodeURIComponent(ticker)}`, {
          ttlMs: 60_000,
          staleMs: 30 * 60_000,
        }).then(
          (d) => unwrap(d, 'events', 'items').map(normalizeBreakoutEvent) as unknown as BreakoutEvent[],
        ),
    ),
};

/* ---- 归一化字段对照（契约 → mock 形状） ----
 * event_id→id/event_id · name→name(缺省 ticker) · setup_type→type(SETUP_TO_SIGNAL 推导)+label
 * current_price→price/current_price · session_change_pct→changePct/session_change_pct
 * intrinsic_strength_score→strengthScore(可 null) · event_at→at/event_at
 * lifecycle_state→result(hit/failed/pending 分类)+lifecycle_state
 * event_price→event_price/triggerPrice · invalidation_price→invalidation_price/invalidPrice
 * target_price：契约无 → null（targetPrice 同）· summary：契约无 → ''
 * support_zone/resistance_zone→{low,high}|null · range_persistence 标量/斜率/分位→真实口径对象（兼容 mock 五维）
 * transitions：{from_state,to_state,evidence_at,reason}[]→{state,at,note}[](升序+起点)
 * contribution_breakdown：*_score 键补短键别名 · evidence：契约无 → []
 * triggered_at/rvol_time_of_day/pivot_price/各评分：可空透传（null → UI「—」）
 */
