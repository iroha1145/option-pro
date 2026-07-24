/**
 * 催化剂契约网关（catalysts 页专用）
 * - mock：走 fixtures2 的契约扩展（确定性种子）
 * - live：对齐个人版后端真实契约（app/api/catalysts.py + services/catalysts）：
 *   · status = {status, enabled, last_sync_at, streams:{news,calendar}, analysis_availability:{enabled,reason}, model…}
 *   · feed/calendar/hotspots 数据在 items 键；news_id 为数字；匿名态 analysis_status 仅 not_requested|completed（设计使然）
 *   · hotspots 为事件组 {event_group_id, hot_score, representative_title, source_count, validated_tickers…}
 *   · 焦点周期 = {cycle:{…, result:{title_zh, summary_zh, headline_summary, focus_ticker_assessments…}}}
 *     触发需 expected_prepared_revision（取自 hotspots/status.prepared_revision）
 *   snake_case → camelCase 的归一在本文件完成；契约缺失字段不编造（null/空由 UI 显「—」或隐藏）。
 */
import { ApiError, get, idFromLocation, mockOr, notifyOwnerSessionInvalid, post, postCreate, toQuery } from '@/api/client';
import { asRec, pickB, pickN, pickS, unwrap, type Rec } from '@/api/live';
import * as fx2 from '@/mocks/fixtures2';
import type {
  AnalysisJobStatus,
  CatalystFeedQuery,
  CatalystNewsItem,
  CatalystsStatusDetail,
  CatalystStreamHealth,
  EconomicEvent,
  FocusCycleJob,
  HotspotGroup,
  HotspotsStatusDetail,
  MarketFocusCycle,
  NewsAnalysisJob,
  NewsClassification,
  NewsImpactResult,
  SourceHealth,
  TickerImpactSummary,
  TrustedStockImpact,
} from '@/mocks/fixtures2';
import {
  buildFocusCycleRequestBody,
  focusCyclePollPath,
} from '@/components/catalysts/focusCycleRequest';

export type {
  CatalystFeedQuery,
  CatalystNewsItem,
  CatalystsStatusDetail,
  CatalystStreamHealth,
  EconomicEvent,
  FocusCycleJob,
  FocusCycleStockAssessment,
  HotspotGroup,
  HotspotsStatusDetail,
  MarketFocusCycle,
  NewsAnalysisJob,
  NewsClassification,
  NewsAnalysisStatus,
  NewsImpactResult,
  SourceHealth,
  TickerImpactSummary,
  TrustedStockImpact,
} from '@/mocks/fixtures2';

/* ================= live 契约归一（snake_case → camelCase） ================= */

const BACKEND_IMPACT_POINTS_PER_UI_UNIT = 20;

function nClassification(v: unknown): NewsClassification {
  return v === 'bullish' || v === 'bearish' ? v : 'neutral';
}

function toBackendImpact(value: number | undefined): number | undefined {
  return value && value > 0 ? Math.round(value * BACKEND_IMPACT_POINTS_PER_UI_UNIT) : undefined;
}

function fromBackendImpact(value: number): number {
  return value / BACKEND_IMPACT_POINTS_PER_UI_UNIT;
}

/** id 兼容数字（个人版 news_id 为整数） */
function pickId(r: Rec, ...keys: string[]): string | null {
  const s = pickS(r, ...keys);
  if (s) return s;
  const n = pickN(r, ...keys);
  return n !== null ? String(n) : null;
}

function nStockImpact(v: unknown): TrustedStockImpact | null {
  const r = asRec(v);
  const ticker = pickS(r, 'ticker');
  const backendImpactScore = pickN(r, 'impactScore', 'impact_score');
  // 个人版 NewsStockImpact 没有 direction/classification；有符号 impact_score 是唯一方向事实。
  // 后端范围 [-100,100]，统一映射为界面使用的 [-5,5]，避免真实数据全部挤在端点。
  if (!ticker || backendImpactScore === null || backendImpactScore === 0) return null;
  const impactScore = fromBackendImpact(backendImpactScore);
  return {
    ticker,
    direction: backendImpactScore > 0 ? 'bullish' : 'bearish',
    impactScore,
    horizon: pickS(r, 'horizon') ?? '',
    mechanism: pickS(r, 'mechanism') ?? '',
    reason: pickS(r, 'reason', 'reason_zh', 'why') ?? '',
  };
}

function nImpact(v: unknown): NewsImpactResult | null {
  const r = asRec(v);
  if (!Object.keys(r).length) return null;
  const conf = pickN(r, 'confidence') ?? 0;
  return {
    classification: nClassification(r.classification),
    // 个人版契约 confidence 为 0–100 整数；UI 口径 0–1
    confidence: conf > 1 ? conf / 100 : conf,
    headlineSummary: pickS(r, 'headlineSummary', 'headline_summary') ?? '',
    causalSummary: pickS(r, 'causalSummary', 'causal_summary', 'summary_zh', 'market_summary') ?? '',
    trustedStockImpacts: unwrap(r, 'trustedStockImpacts', 'trusted_stock_impacts', 'affected_stocks')
      .map(nStockImpact)
      .filter((x): x is TrustedStockImpact => x !== null),
    model: pickS(r, 'model') ?? '',
    generatedAt: pickS(r, 'generatedAt', 'generated_at') ?? '',
  };
}

function nAnalysisStatus(v: unknown): CatalystNewsItem['analysisStatus'] {
  const s = String(v ?? 'pending');
  // 契约活跃态归一（processing/running → in_progress；preparing → pending）
  // 个人版匿名态只回 not_requested|completed：not_requested 视作「未分析」
  if (s === 'not_requested' || s === 'preparing') return 'pending';
  if (s === 'processing' || s === 'running' || s === 'cancel_requested') return 'in_progress';
  if (s === 'canceled') return 'failed';
  if (s === 'queued' || s === 'in_progress' || s === 'completed' || s === 'insufficient_context' || s === 'failed' || s === 'pending') return s;
  return 'pending';
}

const WAITING_ZH = new Set(['中文标题等待生成', '中文摘要等待生成', '热点标题等待中文分析']);
const usefulZh = (value: string | null): string | null => (value && !WAITING_ZH.has(value) ? value : null);

function nNewsItem(r: Rec): CatalystNewsItem {
  const impact = nImpact(r.analysis);
  if (impact && !impact.generatedAt) impact.generatedAt = pickS(r, 'analyzed_at', 'available_at') ?? '';
  const rawTitle = usefulZh(pickS(r, 'title'));
  const rawSummary = usefulZh(pickS(r, 'summary'));
  const titleZh = usefulZh(pickS(r, 'titleZh', 'title_zh'));
  const summaryZh = usefulZh(pickS(r, 'summaryZh', 'summary_zh'));
  return {
    newsId: pickId(r, 'newsId', 'news_id') ?? '',
    source: pickS(r, 'source') ?? '',
    sourceCount: pickN(r, 'sourceCount', 'source_count') ?? 1,
    title: rawTitle ?? titleZh ?? '',
    titleZh: titleZh ?? rawTitle ?? '',
    summary: rawSummary ?? '',
    summaryZh: summaryZh ?? rawSummary ?? '',
    url: pickS(r, 'url') ?? '',
    publishedAt: pickS(r, 'publishedAt', 'published_at') ?? '',
    fetchedAt: pickS(r, 'fetchedAt', 'fetched_at') ?? '',
    sourceTickers: Array.isArray(r.source_tickers) ? (r.source_tickers as string[]) : Array.isArray(r.sourceTickers) ? (r.sourceTickers as string[]) : [],
    isStale: pickB(r, 'isStale', 'is_stale') ?? false,
    themeIds: Array.isArray(r.theme_ids) ? (r.theme_ids as string[]) : Array.isArray(r.themeIds) ? (r.themeIds as string[]) : [],
    analysisStatus: nAnalysisStatus(r.analysis_status ?? r.analysisStatus),
    analysis: impact,
    analysisJobId: pickS(r, 'analysisJobId', 'analysis_job_id') ?? pickS(asRec(r.analysis_job), 'job_id') ?? (typeof r.analysis_job === 'string' ? r.analysis_job : null),
  };
}

/** 契约任务状态 → NewsAnalysisJob.status */
function nJobStatus(v: unknown): AnalysisJobStatus {
  const s = String(v ?? '');
  if (s === 'preparing' || s === 'pending' || s === 'queued' || s === 'not_requested') return 'queued';
  if (s === 'in_progress' || s === 'processing' || s === 'running' || s === 'cancel_requested') return 'in_progress';
  if (s === 'completed') return 'completed';
  if (s === 'insufficient_context') return 'insufficient_context';
  if (s === 'canceled' || s === 'cancelled') return 'cancelled';
  return 'failed'; // failed 及其余失败类终态
}

function nJobProgress(raw: unknown): number | null {
  const progress = pickN(asRec(raw), 'progress');
  return progress !== null && progress >= 0 && progress <= 100
    ? progress
    : null;
}

function nAnalysisJob(raw: unknown, fallbackId?: string | null): NewsAnalysisJob {
  const r = asRec(raw);
  const status = nJobStatus(r.status);
  return {
    jobId: pickS(r, 'jobId', 'job_id') ?? fallbackId ?? '',
    newsId: pickId(r, 'newsId', 'news_id') ?? '',
    status,
    progress: nJobProgress(raw),
    submittedAt: pickS(r, 'submittedAt', 'submitted_at', 'created_at') ?? '',
    updatedAt: pickS(r, 'updatedAt', 'updated_at', 'completed_at') ?? '',
    error: pickS(r, 'error', 'error_code'),
    cancellable: pickB(r, 'cancellable') ?? (status === 'queued' || status === 'in_progress'),
  };
}

function nFocusJob(
  raw: unknown,
  fallbackId?: string | null,
  fallbackCycleId?: string | null,
): FocusCycleJob {
  const r = asRec(raw);
  const s = nJobStatus(r.status);
  const locationCycleId =
    fallbackId && /^mfc_[0-9a-f]{32}$/.test(fallbackId) ? fallbackId : null;
  return {
    jobId:
      pickS(r, 'jobId', 'job_id', 'intent_id', 'request_id') ??
      (locationCycleId ? '' : fallbackId) ??
      '',
    // FocusCycleJob 状态机不含 cancelled/insufficient_context：归一到 failed 停止轮询
    status: s === 'queued' ? 'queued' : s === 'in_progress' ? 'in_progress' : s === 'completed' ? 'completed' : 'failed',
    progress: nJobProgress(raw),
    submittedAt: pickS(r, 'submittedAt', 'submitted_at', 'created_at') ?? '',
    updatedAt: pickS(r, 'updatedAt', 'updated_at', 'completed_at') ?? '',
    cycleId:
      pickS(r, 'cycleId', 'cycle_id') ??
      fallbackCycleId ??
      locationCycleId,
  };
}

function nCycleRecord(r: Rec): MarketFocusCycle {
  const result = asRec(r.result);
  const legacyStage = pickN(r, 'stage');
  const assessments = unwrap(result, 'focus_ticker_assessments').length
    ? unwrap(result, 'focus_ticker_assessments')
    : unwrap(r, 'assessments');
  return {
    cycleId: pickS(r, 'cycleId', 'cycle_id', 'id') ?? '',
    dominantEvent: pickS(result, 'title_zh') ?? pickS(r, 'dominantEvent', 'dominant_event') ?? '—',
    stage: legacyStage !== null ? (Math.min(4, Math.max(1, legacyStage)) as 1 | 2 | 3 | 4) : null,
    startedAt: pickS(r, 'startedAt', 'started_at', 'created_at') ?? '',
    generatedAt: pickS(r, 'generatedAt', 'generated_at', 'completed_at') ?? '',
    trigger: pickS(r, 'trigger') === 'manual' || pickB(r, 'force') === true ? 'manual' : 'scheduled',
    model: pickS(r, 'model') ?? '',
    newsCount: pickN(r, 'newsCount', 'news_count') ?? pickN(r, 'event_group_count') ?? 0,
    sampleLabel: pickN(r, 'newsCount', 'news_count') !== null ? '条' : '组事件',
    status: pickS(r, 'status'),
    summary: pickS(result, 'summary_zh', 'market_summary') ?? pickS(r, 'summary') ?? '',
    headline: pickS(result, 'headline_summary'),
    uncertainties: unwrap(result, 'market_uncertainties').length
      ? (result.market_uncertainties as unknown[]).filter((x): x is string => typeof x === 'string')
      : undefined,
    assessments: assessments.map((a) => ({
      ticker: pickS(a, 'ticker') ?? '',
      name: pickS(a, 'name') ?? '',
      direction: nClassification(a.direction ?? a.stance ?? a.classification),
      impactScore: pickN(a, 'impactScore', 'impact_score') ?? 0,
      note: pickS(a, 'note', 'note_zh', 'assessment_zh', 'reason', 'reason_zh') ?? '',
    })),
  };
}

/** 个人版焦点周期：失败尝试不覆盖最近一次已完成且通过校验的结果。 */
function nCycle(raw: unknown): MarketFocusCycle {
  const env = asRec(raw);
  const current = asRec(env.cycle ?? env);
  const latestSuccessful = asRec(env.latest_successful_cycle);
  const currentStatus = pickS(current, 'status');
  const failedAttempt = ['failed', 'cancelled', 'canceled', 'budget_blocked'].includes(
    currentStatus ?? '',
  );
  const useSuccessfulFallback = failedAttempt && Object.keys(latestSuccessful).length > 0;
  const normalized = nCycleRecord(useSuccessfulFallback ? latestSuccessful : current);
  if (useSuccessfulFallback) {
    normalized.latestAttempt = {
      cycleId: pickS(current, 'cycleId', 'cycle_id', 'id') ?? '',
      status: currentStatus ?? 'failed',
      startedAt: pickS(current, 'startedAt', 'started_at', 'created_at') ?? '',
    };
  }
  return normalized;
}

const STREAM_CN: Record<string, string> = {
  news: '新闻采集流',
  calendar: '经济日历流',
};

function nStream(key: string, raw: unknown): CatalystStreamHealth | null {
  const r = asRec(raw);
  if (!Object.keys(r).length) return null;
  const failures = pickN(r, 'consecutive_failures') ?? 0;
  const remoteStatus = pickS(r, 'remote_status');
  return {
    name: STREAM_CN[key] ?? key,
    // 缺少 remote_status 不能视作正常；必须由后端明确报告成功。
    ok: remoteStatus === 'ok' && failures === 0,
    lastSuccessAt: pickS(r, 'last_success_at', 'data_through') ?? '',
    failures,
    errorCode: pickS(r, 'last_error_code'),
  };
}

function nStatus(d: unknown): CatalystsStatusDetail {
  const r = asRec(d);
  // 个人版真实契约：status/enabled/last_sync_at/streams/analysis_availability
  if (r.streams !== undefined || r.analysis_availability !== undefined || r.last_sync_at !== undefined) {
    const streamsRec = asRec(r.streams);
    const streams = Object.entries(streamsRec)
      .map(([k, v]) => nStream(k, v))
      .filter((s): s is CatalystStreamHealth => s !== null);
    const avail = asRec(r.analysis_availability);
    const analysisReason = pickS(avail, 'reason');
    if (analysisReason === 'owner_login_required') notifyOwnerSessionInvalid();
    return {
      // 缺少 enabled 不能默认打开，避免把不完整快照误报成正在采集。
      collecting: pickS(r, 'status') === 'active' && pickB(r, 'enabled') === true,
      intervalMinutes: null,
      lastCrawlAt: pickS(r, 'last_sync_at', 'data_through') ?? '',
      newsToday: null,
      analyzedToday: null,
      queueDepth: null,
      analysisAvailable: pickB(avail, 'enabled') ?? false,
      analysisReason,
      analysisModel: pickS(r, 'model'),
      analysisReasoning: pickS(r, 'reasoning'),
      analysisTriggerEnabled: pickB(r, 'analysis_trigger_enabled'),
      sourcesActive: streams.filter((s) => s.ok).length,
      sourcesTotal: streams.length,
      streams,
      warnings: Array.isArray(r.warnings) ? (r.warnings as unknown[]).filter((x): x is string => typeof x === 'string') : [],
    };
  }
  // mock 契约字段
  return {
    collecting: pickB(r, 'collecting') ?? false,
    intervalMinutes: pickN(r, 'intervalMinutes', 'interval_minutes'),
    lastCrawlAt: pickS(r, 'lastCrawlAt', 'last_crawl_at') ?? '',
    newsToday: pickN(r, 'newsToday', 'news_today'),
    analyzedToday: pickN(r, 'analyzedToday', 'analyzed_today'),
    analysisAvailable: pickB(r, 'analysisAvailable', 'analysis_available') ?? false,
    queueDepth: pickN(r, 'queueDepth', 'queue_depth'),
    sourcesActive: pickN(r, 'sourcesActive', 'sources_active') ?? 0,
    sourcesTotal: pickN(r, 'sourcesTotal', 'sources_total') ?? 0,
  };
}

const EVENT_TYPE_CN: Record<string, string> = {
  merger: '并购',
  earnings: '财报',
  product: '产品',
  regulatory: '监管',
  macro: '宏观',
  guidance: '指引',
  legal: '法务',
  calendar: '日历',
};

function nHotspot(r: Rec): HotspotGroup {
  // 个人版事件组契约
  if (r.event_group_id !== undefined || r.hot_score !== undefined) {
    const heat = pickN(r, 'hot_score') ?? 0;
    const repId = pickId(r, 'representative_news_id');
    const eventType = pickS(r, 'event_type');
    const repTitle =
      usefulZh(pickS(r, 'representative_title', 'summary_zh')) ??
      (eventType ? EVENT_TYPE_CN[eventType] ?? eventType : '新闻热点');
    return {
      hotspotId: pickS(r, 'event_group_id') ?? '',
      theme: repTitle || '—',
      keywords: Array.isArray(r.reasons) ? (r.reasons as unknown[]).filter((x): x is string => typeof x === 'string') : [],
      heat: Math.round(heat),
      heatLevel: Math.max(0, Math.min(5, Math.ceil(heat / 20))),
      newsCount: pickN(r, 'source_count') ?? 0,
      countKind: 'sources',
      eventType: eventType ? (EVENT_TYPE_CN[eventType] ?? eventType) : null,
      representative: repId
        ? {
            newsId: repId,
            titleZh: repTitle,
            publishedAt: pickS(r, 'last_published_at', 'first_published_at') ?? '',
          }
        : null,
      tickers: Array.isArray(r.validated_tickers) ? (r.validated_tickers as string[]) : [],
      updatedAt: pickS(r, 'prepared_at', 'available_at') ?? '',
    };
  }
  // mock 契约
  const rep = asRec(r.representative);
  return {
    hotspotId: pickS(r, 'hotspotId', 'hotspot_id', 'id') ?? '',
    theme: pickS(r, 'theme') ?? '',
    keywords: Array.isArray(r.keywords) ? (r.keywords as string[]) : [],
    heat: pickN(r, 'heat') ?? 0,
    heatLevel: pickN(r, 'heatLevel', 'heat_level') ?? 0,
    newsCount: pickN(r, 'newsCount', 'news_count') ?? 0,
    countKind: 'news',
    representative: Object.keys(rep).length
      ? {
          newsId: pickId(rep, 'newsId', 'news_id') ?? '',
          titleZh: pickS(rep, 'titleZh', 'title_zh') ?? pickS(rep, 'title') ?? '',
          publishedAt: pickS(rep, 'publishedAt', 'published_at') ?? '',
        }
      : null,
    tickers: Array.isArray(r.tickers) ? (r.tickers as string[]) : [],
    updatedAt: pickS(r, 'updatedAt', 'updated_at') ?? '',
  };
}

function nEconEvent(r: Rec): EconomicEvent {
  const impact = pickS(r, 'impact') ?? 'low';
  const scheduledAt = pickS(r, 'scheduledAt', 'scheduled_at', 'scheduled_at_utc') ?? '';
  const actual = pickS(r, 'actual');
  const rawReleaseStatus = pickS(r, 'releaseStatus', 'release_status');
  const releaseStatus =
    rawReleaseStatus === 'released' || rawReleaseStatus === 'awaiting_source' || rawReleaseStatus === 'scheduled'
      ? rawReleaseStatus
      : actual !== null
        ? 'released'
        : scheduledAt && Date.parse(scheduledAt) <= Date.now()
          ? 'awaiting_source'
          : 'scheduled';
  return {
    eventId: pickId(r, 'eventId', 'event_id') ?? '',
    country: pickS(r, 'country') ?? pickS(r, 'country_code') ?? '',
    title: pickS(r, 'title') ?? '',
    impact: (['low', 'medium', 'high', 'holiday'].includes(impact) ? impact : 'low') as EconomicEvent['impact'],
    impactZh: pickS(r, 'impactZh', 'impact_zh') ?? '',
    scheduledAt,
    forecast: pickS(r, 'forecast') ?? '—',
    previous: pickS(r, 'previous') ?? '—',
    actual,
    releaseStatus,
  };
}

export interface EconomicCalendarQuery {
  dateFrom: string;
  dateTo: string;
  timezoneOffsetMinutes: number;
}

function localDateWithOffset(now: Date, dayOffset: number): string {
  const shifted = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate() + dayOffset,
    12,
  );
  return [
    shifted.getFullYear(),
    String(shifted.getMonth() + 1).padStart(2, '0'),
    String(shifted.getDate()).padStart(2, '0'),
  ].join('-');
}

/** 以浏览器本地自然日请求：完整保留今天之前三天，而不是按 UTC 日期截断。 */
export function browserCalendarQuery(now = new Date()): EconomicCalendarQuery {
  return {
    dateFrom: localDateWithOffset(now, -3),
    dateTo: localDateWithOffset(now, 14),
    // Date#getTimezoneOffset 是“本地到 UTC”的分钟差，后端需要相反方向。
    timezoneOffsetMinutes: -now.getTimezoneOffset(),
  };
}

function nSource(r: Rec): SourceHealth {
  return {
    source: pickS(r, 'source', 'name') ?? '',
    status: pickS(r, 'status') === 'active' ? 'active' : 'degraded',
    latencyMs: pickN(r, 'lagMs', 'lag_ms', 'latencyMs', 'latency_ms'),
    lastFetchedAt: pickS(r, 'lastFetchedAt', 'last_fetched_at', 'last_success_at') ?? '',
    itemsToday: pickN(r, 'itemsLast24h', 'items_last_24h', 'itemsToday', 'items_today'),
    note: pickS(r, 'note') ?? '',
  };
}

/* ================= 查询序列化（个人版 feed 参数） ================= */

function qs(q: CatalystFeedQuery): string {
  // UI minConfidence 为 0–1；契约 min_confidence 为 0–100 整数
  const conf = q.minConfidence != null && q.minConfidence > 0 ? Math.round(q.minConfidence <= 1 ? q.minConfidence * 100 : q.minConfidence) : undefined;
  // UI 'pending'（未分析）↔ 个人版 'not_requested'
  const status = q.analysisStatus === 'pending' ? 'not_requested' : q.analysisStatus || undefined;
  // 默认新闻流只展示已经通过中文校验的分析。用户明确筛选待处理
  // 状态时才请求未分析项。文章整体中性仍可能包含有方向的个股影响，
  // 因此文章流保留中性；股票影响榜另按非零个股影响严格过滤。
  const includeUnanalyzed =
    q.includeUnanalyzed ?? Boolean(status && status !== 'completed');
  const includeNeutral = q.includeNeutral ?? true;
  const s = toQuery({
    ticker: q.ticker,
    window_hours: q.windowHours,
    classification: q.classification || undefined,
    analysis_status: status,
    include_unanalyzed: includeUnanalyzed,
    include_neutral: includeNeutral,
    min_confidence: conf,
    min_abs_impact: toBackendImpact(q.minAbsImpact),
    multi_source_only: q.multiSourceOnly ? true : undefined,
    limit: q.limit,
    cursor: q.cursor ?? undefined,
  });
  return s ? `?${s}` : '';
}

/* ================= 股票影响汇总（batch results map → 客户端聚合） ================= */

function aggTickerSummary(ticker: string, raw: unknown): TickerImpactSummary {
  const r = asRec(raw);
  const summary = asRec(r.summary);
  const directionalCount = pickN(summary, 'directional_count', 'directionalCount');
  const backendNetImpact = pickN(summary, 'net_impact', 'netImpact');

  // 新版个人接口在完整过滤窗口上聚合；items 仍受匿名 limit=5 约束，只用于明细预览。
  if (directionalCount !== null && backendNetImpact !== null) {
    const bullish = pickN(summary, 'bullish') ?? 0;
    const bearish = pickN(summary, 'bearish') ?? 0;
    return {
      ticker,
      name: ticker,
      sector: '',
      count: directionalCount,
      analyzed: pickN(summary, 'analyzed_count', 'analyzedCount') ?? directionalCount,
      netImpact: fromBackendImpact(backendNetImpact),
      latestAt: pickS(summary, 'latest_at', 'latestAt') ?? '',
      sourceDiversity: pickN(summary, 'source_diversity', 'sourceDiversity') ?? 0,
      bullish,
      bearish,
      neutral: pickN(summary, 'neutral') ?? 0,
    };
  }

  // 兼容旧接口：仅能按返回页推导，仍严格排除待分析、提及和 0 分影响。
  const items = unwrap(r, 'items').map(nNewsItem);
  let bullish = 0;
  let bearish = 0;
  let analyzed = 0;
  const impacts: number[] = [];
  let latest = '';
  const sources = new Set<string>();

  for (const item of items) {
    if (item.analysisStatus !== 'completed' || !item.analysis) continue;
    const mine = item.analysis.trustedStockImpacts
      .filter((impact) => impact.ticker === ticker && impact.impactScore !== 0)
      .sort((a, b) => Math.abs(b.impactScore) - Math.abs(a.impactScore))[0];
    if (!mine) continue;
    analyzed += 1;
    impacts.push(mine.impactScore);
    if (mine.impactScore > 0) bullish += 1;
    else bearish += 1;
    if (item.source) sources.add(item.source.split('/')[0]);
    if (item.publishedAt > latest) latest = item.publishedAt;
  }

  const netImpact = impacts.length
    ? impacts.reduce((sum, score) => sum + score, 0) / impacts.length
    : 0;
  return {
    ticker,
    name: ticker,
    sector: '',
    count: analyzed,
    analyzed,
    netImpact,
    latestAt: latest,
    sourceDiversity: sources.size,
    bullish,
    bearish,
    neutral: 0,
  };
}

/** batch 响应：个人版 {results: {TICKER: {...}}}；兼容 {results|items:[...]} 与顶层映射 */
function nBatchSummaries(body: unknown): TickerImpactSummary[] {
  const r = asRec(body);
  const resultsRec = asRec(r.results);
  if (Object.keys(resultsRec).length && !Array.isArray(r.results)) {
    return Object.entries(resultsRec)
      .filter(([, v]) => v && typeof v === 'object')
      .map(([t, v]) => aggTickerSummary(t, v))
      .filter((x) => x.count > 0)
      .sort((a, b) => b.count - a.count);
  }
  const arr = unwrap(body, 'results', 'items', 'summaries');
  if (arr.length) {
    return arr
      .map((x) => aggTickerSummary(pickS(x, 'ticker') ?? '', x))
      .filter((x) => x.count > 0)
      .sort((a, b) => b.count - a.count);
  }
  const per = asRec(r.tickers ?? body);
  return Object.entries(per)
    .filter(([, v]) => v && typeof v === 'object')
    .map(([t, v]) => aggTickerSummary(t, v))
    .filter((x) => x.count > 0)
    .sort((a, b) => b.count - a.count);
}

/* ================= 网关 ================= */

const PUBLIC_BATCH_MAX_TICKERS = 20; // 匿名上限（owner 50，取交集下限保证双态可用）
const PUBLIC_BATCH_MAX_LIMIT = 5;

/* ---------- 客户端读缓存 ----------
 * 短 TTL + 并发去重：同一路径的并发请求共享一个 Promise（StatusHero 与
 * HotspotsStrip 各自轮询 hotspots/status 时只发一次），标签切回时 30s 内
 * 即时显示。TTL 低于最小轮询间隔（45s），不改变轮询的新鲜度语义；
 * 页头「刷新」与所有写操作（触发分析/焦点周期）会清空缓存。
 */
const READ_CACHE_TTL_MS = 30_000;
const readCache = new Map<string, { at: number; promise: Promise<unknown> }>();

export function clearCatalystReadCache(): void {
  readCache.clear();
}

function cachedFetch<T>(key: string, run: () => Promise<T>, ttlMs: number): Promise<T> {
  const hit = readCache.get(key);
  const now = Date.now();
  if (hit && now - hit.at <= ttlMs) return hit.promise as Promise<T>;
  const promise = run();
  readCache.set(key, { at: now, promise });
  promise.catch(() => {
    // 失败不留缓存，下一次调用重新请求
    if (readCache.get(key)?.promise === promise) readCache.delete(key);
  });
  return promise;
}

function cachedGet<T = unknown>(path: string, ttlMs = READ_CACHE_TTL_MS): Promise<T> {
  return cachedFetch(`GET ${path}`, () => get<T>(path), ttlMs);
}

function cachedPost<T = unknown>(path: string, body: unknown, ttlMs = READ_CACHE_TTL_MS): Promise<T> {
  return cachedFetch(`POST ${path} ${JSON.stringify(body)}`, () => post<T>(path, body), ttlMs);
}

export const catalystsContract = {
  status: (): Promise<CatalystsStatusDetail> =>
    mockOr(() => fx2.getCatalystsStatusV2(), () => cachedGet('/catalysts/status').then(nStatus)),
  /** 今日新闻计数优先使用后端完整过滤窗口汇总；旧后端才退回当前页计数。 */
  newsToday: (): Promise<{ count: number; analyzed: number; saturated: boolean }> =>
    mockOr(
      async () => {
        const s = await fx2.getCatalystsStatusV2();
        return { count: s.newsToday ?? 0, analyzed: s.analyzedToday ?? 0, saturated: false };
      },
      () =>
        cachedGet(`/catalysts/feed${qs({
          windowHours: 24,
          limit: 50,
          includeUnanalyzed: true,
          includeNeutral: true,
        })}`).then((d) => {
          const items = unwrap(d, 'items').map(nNewsItem);
          const summary = asRec(asRec(d).summary);
          const completeCount = pickN(summary, 'count');
          const completeAnalyzed = pickN(
            summary,
            'analyzedCount',
            'analyzed_count',
          );
          return {
            count: completeCount ?? items.length,
            analyzed:
              completeAnalyzed
              ?? items.filter((i) => i.analysisStatus === 'completed').length,
            saturated: completeCount === null && items.length >= 50,
          };
        }),
    ),
  feed: (q: CatalystFeedQuery = {}): Promise<{ items: CatalystNewsItem[]; nextCursor: string | null; total: number }> =>
    mockOr(
      () => fx2.getCatalystsFeedV2(q),
      () =>
        cachedGet(`/catalysts/feed${qs(q)}`).then((d) => {
          const items = unwrap(d, 'items')
            .map(nNewsItem)
            .filter((item) => item.titleZh && item.summaryZh);
          return { items, nextCursor: pickS(asRec(d), 'next_cursor', 'nextCursor'), total: items.length };
        }),
    ),
  news: (id: string): Promise<CatalystNewsItem> =>
    mockOr(() => fx2.getNewsDetailV2(id), () =>
      get(`/catalysts/news/${encodeURIComponent(id)}`).then((d) => {
        const env = asRec(d);
        return nNewsItem(asRec(env.item ?? env));
      }),
    ),
  hotspots: (): Promise<HotspotGroup[]> =>
    mockOr(() => fx2.getHotspotsV2(), () => cachedGet('/catalysts/hotspots?limit=8').then((d) => unwrap(d, 'items').map(nHotspot))),
  hotspotsStatus: (): Promise<HotspotsStatusDetail> =>
    mockOr(
      () => fx2.getHotspotsStatusV2(),
      () =>
        cachedGet('/catalysts/hotspots/status').then((d) => {
          const r = asRec(d);
          // 个人版：status/prepared_hot_count/prepared_since/manual_enabled/prepared_revision
          if (r.prepared_revision !== undefined || r.prepared_hot_count !== undefined) {
            const count = pickN(r, 'prepared_hot_count') ?? 0;
            return {
              state: 'ready' as const,
              scanning: pickS(r, 'status') === 'active',
              updatedAt: pickS(r, 'prepared_since', 'as_of', 'last_sync_at') ?? '',
              etaSeconds: null,
              groupCount: count,
              manualEnabled: pickB(r, 'manual_enabled'),
              preparedRevision: pickN(r, 'prepared_revision'),
              lastCycleAt: pickS(r, 'last_cycle_at'),
            };
          }
          const scanning = pickB(r, 'scanning') ?? false;
          return {
            state: (pickS(r, 'state') === 'computing' || scanning ? 'computing' : 'ready') as HotspotsStatusDetail['state'],
            scanning,
            updatedAt: pickS(r, 'updatedAt', 'updated_at', 'as_of') ?? '',
            etaSeconds: pickN(r, 'etaSeconds', 'eta_seconds'),
            groupCount: pickN(r, 'groupCount', 'group_count') ?? 0,
          };
        }),
    ),
  calendar: (query?: EconomicCalendarQuery): Promise<EconomicEvent[]> =>
    mockOr(
      () => fx2.getEconomicCalendar(),
      () => {
        const queryString = query
          ? toQuery({
              date_from: query.dateFrom,
              date_to: query.dateTo,
              timezone_offset_minutes: query.timezoneOffsetMinutes,
            })
          : '';
        const path = `/catalysts/calendar${queryString ? `?${queryString}` : ''}`;
        return cachedGet(path).then((d) => unwrap(d, 'items', 'events').map(nEconEvent));
      },
    ),
  sources: (): Promise<SourceHealth[]> =>
    mockOr(
      () => fx2.getCatalystsSources(),
      async () => {
        // 只展示后端真实健康快照。新闻数量不能证明当前采集链路健康。
        const statusBody = await cachedGet('/catalysts/status');
        const fromStatus = unwrap(statusBody, 'sources', 'source_health').map(nSource).filter((s) => s.source);
        if (fromStatus.length) return fromStatus;
        const streams = asRec(asRec(statusBody).streams);
        const streamNames: Record<string, string> = {
          news: '新闻采集流',
          calendar: '经济日历流',
        };
        const realStreams = Object.entries(streams).map(([key, raw]) => {
          const r = asRec(raw);
          const failures = pickN(r, 'consecutive_failures') ?? 0;
          const remoteStatus = pickS(r, 'remote_status');
          const active = remoteStatus === 'ok' && failures === 0;
          return {
            source: streamNames[key] ?? key,
            status: active ? ('active' as const) : ('degraded' as const),
            latencyMs: pickN(r, 'lag_ms', 'lagMs'),
            lastFetchedAt: pickS(r, 'last_success_at', 'data_through') ?? '',
            itemsToday: pickN(r, 'items_last_24h', 'itemsLast24h'),
            note: active
              ? '后台采集流最近一次执行成功'
              : pickS(r, 'last_error_code') ?? '后台采集流状态异常',
          };
        });
        if (realStreams.length) return realStreams;
        throw new ApiError(503, '后端未提供数据源健康快照');
      },
    ),
  latestFocusCycle: (): Promise<MarketFocusCycle> =>
    mockOr(() => fx2.getLatestFocusCycleV2(), () => cachedGet('/catalysts/market-focus-cycles/latest').then(nCycle)),
  previousFocusCycle: (): Promise<MarketFocusCycle> =>
    mockOr(() => fx2.getPreviousSuccessfulFocusCycle(), () =>
      cachedGet('/catalysts/market-focus-cycles/latest').then((data) => {
        const previous = asRec(asRec(data).previous_successful_cycle);
        if (!Object.keys(previous).length) {
          throw new ApiError(404, '暂无更早的成功焦点周期', {
            bizCode: 'previous_focus_cycle_not_found',
          });
        }
        return nCycle(previous);
      }),
    ),
  triggerFocusCycle: (retryCycleId: string | null = null): Promise<FocusCycleJob> =>
    // 个人版：POST 需带 expected_prepared_revision（来自 hotspots/status），XOR retry_cycle_id
    mockOr(() => {
      const job = fx2.triggerFocusCycle();
      // mock 的任务表仍以 jobId 为键；统一放进轮询标识槽，live 始终使用真实 cycleId。
      return { ...job, cycleId: job.cycleId ?? job.jobId };
    }, async () => {
      let body;
      if (retryCycleId) {
        body = buildFocusCycleRequestBody(null, null, null, retryCycleId);
      } else {
        const hs = await get('/catalysts/hotspots/status');
        const revision = pickN(asRec(hs), 'prepared_revision');
        const hasNewHotspots = pickB(asRec(hs), 'has_new_hotspots');
        const preparedHotCount = pickN(asRec(hs), 'prepared_hot_count');
        body = buildFocusCycleRequestBody(
          revision,
          hasNewHotspots,
          preparedHotCount,
        );
      }
      const { data, location } = await postCreate('/catalysts/market-focus-cycles', body);
      clearCatalystReadCache();
      const rec = asRec(data);
      const locationId = idFromLocation(location);
      const cycle = asRec(rec.cycle);
      const cycleId =
        pickS(rec, 'cycleId', 'cycle_id') ??
        pickS(cycle, 'cycleId', 'cycle_id') ??
        (locationId && /^mfc_[0-9a-f]{32}$/.test(locationId) ? locationId : null);
      const job = nFocusJob(rec.job ?? rec.cycle ?? data, locationId, cycleId);
      // 202 已受理但拿不到周期编号（形状差异）：交给 UI 走「延迟刷新 latest」兜底，不视为失败
      return job;
    }),
  focusCycleJob: (cycleId: string): Promise<FocusCycleJob> =>
    mockOr(() => fx2.getFocusCycleJob(cycleId), () =>
      get(focusCyclePollPath(cycleId)).then((d) => nFocusJob(d, null, cycleId)),
    ),
  tickerSummaries: (q: CatalystFeedQuery = {}): Promise<TickerImpactSummary[]> =>
    mockOr(
      () => fx2.getTickerImpactSummaries(q),
      // 个人版 batch {tickers, window_hours, limit≤5(匿名), include_neutral/unanalyzed}：
      // 候选代码 = 指定 ticker，否则按 feed 中真实个股影响排序并用来源/热点补足（≤20 匿名安全）
      async () => {
        // 股票榜只包含已完成且有方向的分析；选择其他分析状态时结果应为空。
        if (q.analysisStatus && q.analysisStatus !== 'completed') return [];
        let tickers: string[] = q.ticker ? [q.ticker] : [];
        if (!tickers.length) {
          // 候选发现不能套用文章整体多空过滤；真实排名按每只股票自己的影响分完成。
          const [feedBody, hotBody] = await Promise.all([
            cachedGet(
              `/catalysts/feed${qs({
                windowHours: q.windowHours,
                analysisStatus: 'completed',
                limit: 50,
              })}`,
            ).catch(() => null),
            cachedGet('/catalysts/hotspots?limit=20').catch(() => null),
          ]);
          const candidateScores = new Map<string, number>();
          const addCandidate = (value: unknown, score: number) => {
            if (typeof value !== 'string' || !value) return;
            candidateScores.set(value, Math.max(candidateScores.get(value) ?? -1, score));
          };
          for (const item of unwrap(feedBody, 'items')) {
            for (const ticker of Array.isArray(item.source_tickers) ? item.source_tickers : []) {
              addCandidate(ticker, 0);
            }
            const analysis = asRec(item.analysis);
            for (const impact of Array.isArray(analysis.affected_stocks) ? analysis.affected_stocks : []) {
              const row = asRec(impact);
              const score = pickN(row, 'impact_score', 'impactScore') ?? 0;
              if (score !== 0) addCandidate(pickS(row, 'ticker'), Math.abs(score));
            }
          }
          for (const hotspot of unwrap(hotBody, 'items')) {
            const values = Array.isArray(hotspot.validated_tickers)
              ? hotspot.validated_tickers
              : Array.isArray(hotspot.tickers)
                ? hotspot.tickers
                : [];
            for (const ticker of values) addCandidate(ticker, 0);
          }
          tickers = [...candidateScores]
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .slice(0, PUBLIC_BATCH_MAX_TICKERS)
            .map(([ticker]) => ticker);
        }
        if (!tickers.length) return [];
        const body = await cachedPost('/catalysts/tickers/batch', {
          tickers,
          window_hours: q.windowHours ?? 72,
          limit: PUBLIC_BATCH_MAX_LIMIT,
          // 文章整体可为 neutral，但个股仍可能有方向；只取该 ticker 的非零个股影响。
          include_neutral: true,
          include_unanalyzed: false,
          directional_only: true,
          classification: q.classification || undefined,
          min_confidence:
            q.minConfidence && q.minConfidence > 0
              ? Math.round(q.minConfidence <= 1 ? q.minConfidence * 100 : q.minConfidence)
              : 0,
          min_abs_impact: toBackendImpact(q.minAbsImpact),
          multi_source_only: Boolean(q.multiSourceOnly),
        });
        return nBatchSummaries(body);
      },
    ),
  /* 新闻 AI 分析任务（owner）：POST → 202 + Location；GET 轮询；POST cancel {confirm:true} */
  createAnalysisJob: (newsId: string, force = false): Promise<NewsAnalysisJob> =>
    mockOr(
      () => fx2.createNewsAnalysisJob(newsId, force),
      () =>
        postCreate(`/catalysts/news/${encodeURIComponent(newsId)}/analysis`, { force }).then(({ data, location }) => {
          clearCatalystReadCache();
          const job = nAnalysisJob(data, idFromLocation(location));
          if (!job.jobId) throw new ApiError(502, '任务创建响应缺少 job_id', { payload: data });
          if (!job.newsId) job.newsId = newsId;
          return job;
        }),
    ),
  analysisJob: (jobId: string): Promise<NewsAnalysisJob> =>
    mockOr(() => fx2.getNewsAnalysisJob(jobId), () =>
      get(`/catalysts/analysis-jobs/${encodeURIComponent(jobId)}`).then((d) => nAnalysisJob(d, jobId)),
    ),
  cancelAnalysisJob: (jobId: string): Promise<NewsAnalysisJob> =>
    mockOr(
      () => fx2.cancelNewsAnalysisJob(jobId),
      () => post(`/catalysts/analysis-jobs/${encodeURIComponent(jobId)}/cancel`, { confirm: true }).then((d) => { clearCatalystReadCache(); return nAnalysisJob(d, jobId); }),
    ),
  themeName: (themeId: string): string => fx2.getHotspotThemeName(themeId),
};
