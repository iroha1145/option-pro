/**
 * 催化剂契约网关（catalysts 页专用）
 * - mock：走 fixtures2 的契约扩展（确定性种子）
 * - live：走 /api/catalysts/* 与 /api/ai/jobs/*（形状 1:1 对齐 api-contract.md，
 *   snake_case 契约 → camelCase UI 的归一在本文件完成；契约缺失字段不编造）
 * 注：脚手架 src/api/modules/catalysts.ts 为精简形状且按约束不可改动，
 *     本网关复用 client 的 mockOr/get/post 模式，不改动 api 层。
 */
import { ApiError, get, idFromLocation, mockOr, post, postCreate, toQuery } from '@/api/client';
import { asRec, pickB, pickN, pickS, unwrap, type Rec } from '@/api/live';
import * as fx2 from '@/mocks/fixtures2';
import type {
  AnalysisJobStatus,
  CatalystFeedQuery,
  CatalystNewsItem,
  CatalystsStatusDetail,
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
} from '@/mocks/fixtures2';

export type {
  CatalystFeedQuery,
  CatalystNewsItem,
  CatalystsStatusDetail,
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

function nClassification(v: unknown): NewsClassification {
  return v === 'bullish' || v === 'bearish' ? v : 'neutral';
}

function nImpact(v: unknown): NewsImpactResult | null {
  const r = asRec(v);
  if (!Object.keys(r).length) return null;
  return {
    classification: nClassification(r.classification),
    confidence: pickN(r, 'confidence') ?? 0,
    headlineSummary: pickS(r, 'headlineSummary', 'headline_summary') ?? '',
    causalSummary: pickS(r, 'causalSummary', 'causal_summary') ?? '',
    trustedStockImpacts: unwrap(r, 'trustedStockImpacts', 'trusted_stock_impacts').map((t) => ({
      ticker: pickS(t, 'ticker') ?? '',
      direction: nClassification(t.direction),
      impactScore: pickN(t, 'impactScore', 'impact_score') ?? 0,
      horizon: pickS(t, 'horizon') ?? '',
      mechanism: pickS(t, 'mechanism') ?? '',
      reason: pickS(t, 'reason') ?? '',
    })),
    model: pickS(r, 'model') ?? '',
    generatedAt: pickS(r, 'generatedAt', 'generated_at') ?? '',
  };
}

function nAnalysisStatus(v: unknown): CatalystNewsItem['analysisStatus'] {
  const s = String(v ?? 'pending');
  // 契约 §0.4 活跃态归一（processing/running → in_progress；preparing/pending → pending/queued）
  if (s === 'processing' || s === 'running' || s === 'cancel_requested') return 'in_progress';
  if (s === 'preparing') return 'pending';
  if (s === 'canceled') return 'failed';
  if (s === 'queued' || s === 'in_progress' || s === 'completed' || s === 'insufficient_context' || s === 'failed') return s;
  return 'pending';
}

function nNewsItem(r: Rec): CatalystNewsItem {
  return {
    newsId: pickS(r, 'newsId', 'news_id') ?? '',
    source: pickS(r, 'source') ?? '',
    sourceCount: pickN(r, 'sourceCount', 'source_count') ?? 1,
    title: pickS(r, 'title') ?? '',
    titleZh: pickS(r, 'titleZh', 'title_zh') ?? '',
    summary: pickS(r, 'summary') ?? '',
    summaryZh: pickS(r, 'summaryZh', 'summary_zh') ?? '',
    url: pickS(r, 'url') ?? '',
    publishedAt: pickS(r, 'publishedAt', 'published_at') ?? '',
    fetchedAt: pickS(r, 'fetchedAt', 'fetched_at') ?? '',
    sourceTickers: Array.isArray(r.source_tickers) ? (r.source_tickers as string[]) : Array.isArray(r.sourceTickers) ? (r.sourceTickers as string[]) : [],
    isStale: pickB(r, 'isStale', 'is_stale') ?? false,
    themeIds: Array.isArray(r.theme_ids) ? (r.theme_ids as string[]) : Array.isArray(r.themeIds) ? (r.themeIds as string[]) : [],
    analysisStatus: nAnalysisStatus(r.analysis_status ?? r.analysisStatus),
    analysis: nImpact(r.analysis),
    analysisJobId: pickS(r, 'analysisJobId', 'analysis_job_id') ?? pickS(asRec(r.analysis_job), 'job_id') ?? (typeof r.analysis_job === 'string' ? r.analysis_job : null),
  };
}

/** 契约任务状态（§0.4）→ NewsAnalysisJob.status */
function nJobStatus(v: unknown): AnalysisJobStatus {
  const s = String(v ?? '');
  if (s === 'preparing' || s === 'pending' || s === 'queued') return 'queued';
  if (s === 'in_progress' || s === 'processing' || s === 'running' || s === 'cancel_requested') return 'in_progress';
  if (s === 'completed') return 'completed';
  if (s === 'insufficient_context') return 'insufficient_context';
  if (s === 'canceled' || s === 'cancelled') return 'cancelled';
  return 'failed'; // failed 及其余失败类终态
}

function nJobProgress(status: AnalysisJobStatus, raw: unknown): number {
  const p = pickN(asRec(raw), 'progress');
  if (p !== null) return p;
  if (status === 'queued') return 5;
  if (status === 'in_progress') return 50;
  return 100;
}

function nAnalysisJob(raw: unknown, fallbackId?: string | null): NewsAnalysisJob {
  const r = asRec(raw);
  const status = nJobStatus(r.status);
  return {
    jobId: pickS(r, 'jobId', 'job_id') ?? fallbackId ?? '',
    newsId: pickS(r, 'newsId', 'news_id') ?? '',
    status,
    progress: nJobProgress(status, raw),
    submittedAt: pickS(r, 'submittedAt', 'submitted_at') ?? '',
    updatedAt: pickS(r, 'updatedAt', 'updated_at', 'completed_at') ?? '',
    error: pickS(r, 'error', 'error_code'),
    cancellable: pickB(r, 'cancellable') ?? (status === 'queued' || status === 'in_progress'),
  };
}

function nFocusJob(raw: unknown, fallbackId?: string | null): FocusCycleJob {
  const r = asRec(raw);
  const s = nJobStatus(r.status);
  return {
    jobId: pickS(r, 'jobId', 'job_id') ?? fallbackId ?? '',
    // FocusCycleJob 状态机不含 cancelled/insufficient_context：归一到 failed 停止轮询
    status: s === 'queued' ? 'queued' : s === 'in_progress' ? 'in_progress' : s === 'completed' ? 'completed' : 'failed',
    progress: nJobProgress(s, raw),
    submittedAt: pickS(r, 'submittedAt', 'submitted_at') ?? '',
    updatedAt: pickS(r, 'updatedAt', 'updated_at', 'completed_at') ?? '',
    cycleId: pickS(r, 'cycleId', 'cycle_id'),
  };
}

function nCycle(raw: unknown): MarketFocusCycle {
  const r = asRec(raw);
  const stage = pickN(r, 'stage') ?? 1;
  return {
    cycleId: pickS(r, 'cycleId', 'cycle_id', 'id') ?? '',
    dominantEvent: pickS(r, 'dominantEvent', 'dominant_event') ?? '',
    stage: (Math.min(4, Math.max(1, stage)) as MarketFocusCycle['stage']),
    startedAt: pickS(r, 'startedAt', 'started_at') ?? '',
    generatedAt: pickS(r, 'generatedAt', 'generated_at') ?? '',
    trigger: pickS(r, 'trigger') === 'manual' ? 'manual' : 'scheduled',
    model: pickS(r, 'model') ?? '',
    newsCount: pickN(r, 'newsCount', 'news_count') ?? 0,
    summary: pickS(r, 'summary') ?? '',
    assessments: unwrap(r, 'assessments').map((a) => ({
      ticker: pickS(a, 'ticker') ?? '',
      name: pickS(a, 'name') ?? '',
      direction: nClassification(a.direction),
      impactScore: pickN(a, 'impactScore', 'impact_score') ?? 0,
      note: pickS(a, 'note') ?? '',
    })),
  };
}

function nStatus(d: unknown): CatalystsStatusDetail {
  const r = asRec(d);
  return {
    collecting: pickB(r, 'collecting') ?? false,
    intervalMinutes: pickN(r, 'intervalMinutes', 'interval_minutes') ?? 0,
    lastCrawlAt: pickS(r, 'lastCrawlAt', 'last_crawl_at') ?? '',
    newsToday: pickN(r, 'newsToday', 'news_today') ?? 0,
    analyzedToday: pickN(r, 'analyzedToday', 'analyzed_today') ?? 0,
    analysisAvailable: pickB(r, 'analysisAvailable', 'analysis_available') ?? false,
    queueDepth: pickN(r, 'queueDepth', 'queue_depth') ?? 0,
    sourcesActive: pickN(r, 'sourcesActive', 'sources_active') ?? 0,
    sourcesTotal: pickN(r, 'sourcesTotal', 'sources_total') ?? 0,
  };
}

function nHotspot(r: Rec): HotspotGroup {
  const rep = asRec(r.representative);
  return {
    hotspotId: pickS(r, 'hotspotId', 'hotspot_id', 'id') ?? '',
    theme: pickS(r, 'theme') ?? '',
    keywords: Array.isArray(r.keywords) ? (r.keywords as string[]) : [],
    heat: pickN(r, 'heat') ?? 0,
    heatLevel: pickN(r, 'heatLevel', 'heat_level') ?? 0,
    newsCount: pickN(r, 'newsCount', 'news_count') ?? 0,
    representative: Object.keys(rep).length
      ? {
          newsId: pickS(rep, 'newsId', 'news_id') ?? '',
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
  return {
    eventId: pickS(r, 'eventId', 'event_id') ?? '',
    country: pickS(r, 'country') ?? '',
    title: pickS(r, 'title') ?? '',
    impact: (['low', 'medium', 'high', 'holiday'].includes(impact) ? impact : 'low') as EconomicEvent['impact'],
    impactZh: pickS(r, 'impactZh', 'impact_zh') ?? '',
    scheduledAt: pickS(r, 'scheduledAt', 'scheduled_at') ?? '',
    forecast: pickS(r, 'forecast') ?? '',
    previous: pickS(r, 'previous') ?? '',
    actual: pickS(r, 'actual'),
  };
}

function nSource(r: Rec): SourceHealth {
  return {
    source: pickS(r, 'source') ?? '',
    status: pickS(r, 'status') === 'degraded' ? 'degraded' : 'active',
    latencyMs: pickN(r, 'latencyMs', 'latency_ms') ?? 0,
    lastFetchedAt: pickS(r, 'lastFetchedAt', 'last_fetched_at') ?? '',
    itemsToday: pickN(r, 'itemsToday', 'items_today') ?? 0,
    note: pickS(r, 'note') ?? '',
  };
}

/** 契约 batch 每股 {items, net_impact, latest_at, source_diversity, count} → TickerImpactSummary */
function nTickerSummary(ticker: string, raw: unknown): TickerImpactSummary {
  const r = asRec(raw);
  const items = unwrap(r, 'items');
  let bullish = 0;
  let bearish = 0;
  let neutral = 0;
  let analyzed = 0;
  for (const it of items) {
    const st = nAnalysisStatus(it.analysis_status ?? it.analysisStatus);
    if (st !== 'completed') continue;
    analyzed += 1;
    const cls = nClassification(asRec(it.analysis).classification);
    if (cls === 'bullish') bullish += 1;
    else if (cls === 'bearish') bearish += 1;
    else neutral += 1;
  }
  return {
    ticker,
    name: pickS(r, 'name') ?? ticker,
    sector: pickS(r, 'sector') ?? '—', // 契约每股汇总无板块字段
    count: pickN(r, 'count') ?? items.length,
    analyzed,
    netImpact: pickN(r, 'netImpact', 'net_impact') ?? 0,
    latestAt: pickS(r, 'latestAt', 'latest_at') ?? '',
    sourceDiversity: pickN(r, 'sourceDiversity', 'source_diversity') ?? 0,
    bullish,
    bearish,
    neutral,
  };
}

/* ================= 查询序列化（契约 §catalysts feed 参数） ================= */

function qs(q: CatalystFeedQuery): string {
  // 契约参数：ticker/window_hours/classification/analysis_status/min_confidence/
  // min_abs_impact/source/limit/cursor；multiSourceOnly/themeId 为 UI 侧过滤，契约无此参数不下发
  const s = toQuery({
    ticker: q.ticker,
    window_hours: q.windowHours,
    classification: q.classification || undefined,
    analysis_status: q.analysisStatus || undefined,
    min_confidence: q.minConfidence,
    min_abs_impact: q.minAbsImpact,
    limit: q.limit,
    cursor: q.cursor ?? undefined,
  });
  return s ? `?${s}` : '';
}

/** batch 响应：可能是 {ticker: {...}} 映射或 {results|items:[...]} */
function nBatchSummaries(body: unknown): TickerImpactSummary[] {
  const r = asRec(body);
  const arr = unwrap(body, 'results', 'items', 'summaries');
  if (arr.length) {
    return arr.map((x) => nTickerSummary(pickS(x, 'ticker') ?? '', x)).sort((a, b) => b.count - a.count);
  }
  const per = asRec(r.tickers ?? body);
  return Object.entries(per)
    .filter(([, v]) => v && typeof v === 'object')
    .map(([t, v]) => nTickerSummary(t, v))
    .sort((a, b) => b.count - a.count);
}

/* ================= 网关 ================= */

export const catalystsContract = {
  status: (): Promise<CatalystsStatusDetail> =>
    mockOr(() => fx2.getCatalystsStatusV2(), () => get('/catalysts/status').then(nStatus)),
  feed: (q: CatalystFeedQuery = {}): Promise<{ items: CatalystNewsItem[]; nextCursor: string | null; total: number }> =>
    mockOr(
      () => fx2.getCatalystsFeedV2(q),
      () =>
        get(`/catalysts/feed${qs(q)}`).then((d) => {
          const items = unwrap(d, 'items').map(nNewsItem);
          return { items, nextCursor: pickS(asRec(d), 'next_cursor', 'nextCursor'), total: items.length };
        }),
    ),
  news: (id: string): Promise<CatalystNewsItem> =>
    mockOr(() => fx2.getNewsDetailV2(id), () => get(`/catalysts/news/${encodeURIComponent(id)}`).then((d) => nNewsItem(asRec(d)))),
  hotspots: (): Promise<HotspotGroup[]> =>
    mockOr(() => fx2.getHotspotsV2(), () => get('/catalysts/hotspots?limit=8').then((d) => unwrap(d, 'items').map(nHotspot))),
  hotspotsStatus: (): Promise<HotspotsStatusDetail> =>
    mockOr(
      () => fx2.getHotspotsStatusV2(),
      () =>
        get('/catalysts/hotspots/status').then((d) => {
          const r = asRec(d);
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
  calendar: (): Promise<EconomicEvent[]> =>
    mockOr(() => fx2.getEconomicCalendar(), () => get('/catalysts/calendar').then((d) => unwrap(d, 'events').map(nEconEvent))),
  sources: (): Promise<SourceHealth[]> =>
    mockOr(
      () => fx2.getCatalystsSources(),
      () => get('/catalysts/status').then((d) => unwrap(d, 'sources', 'source_health').map(nSource)),
    ),
  latestFocusCycle: (): Promise<MarketFocusCycle> =>
    mockOr(() => fx2.getLatestFocusCycleV2(), () => get('/catalysts/market-focus-cycles/latest').then(nCycle)),
  previousFocusCycle: (): Promise<MarketFocusCycle> =>
    // 契约无「上一成功周期」端点（仅 latest 与 {id}）：live 下如实报快照不可用，UI 隐藏对照区
    mockOr(() => fx2.getPreviousSuccessfulFocusCycle(), () =>
      Promise.reject(new ApiError(503, '上一焦点周期快照暂不可用（契约未提供该端点）', { bizCode: 'public_snapshot_unavailable' })),
    ),
  triggerFocusCycle: (): Promise<FocusCycleJob> =>
    // 契约：owner+SO {trigger:"manual", force?} → 202（job_id 可能在 Location 头）
    mockOr(() => fx2.triggerFocusCycle(), () =>
      postCreate('/catalysts/market-focus-cycles', { trigger: 'manual' }).then(({ data, location }) => {
        const job = nFocusJob(data, idFromLocation(location));
        if (!job.jobId) throw new ApiError(502, '任务创建响应缺少 job_id', { payload: data });
        return job;
      }),
    ),
  focusCycleJob: (jobId: string): Promise<FocusCycleJob> =>
    mockOr(() => fx2.getFocusCycleJob(jobId), () =>
      get(`/catalysts/analysis-jobs/${encodeURIComponent(jobId)}`).then((d) => nFocusJob(d, jobId)),
    ),
  tickerSummaries: (q: CatalystFeedQuery = {}): Promise<TickerImpactSummary[]> =>
    mockOr(
      () => fx2.getTickerImpactSummaries(q),
      // 契约 batch body {tickers≤50, window_hours, limit, include_neutral}：
      // 查询只有单 ticker 时直接送；否则先按同条件拉 feed 收集 source_tickers（≤50）再汇总
      async () => {
        const tickers: string[] = q.ticker ? [q.ticker] : [];
        if (!tickers.length) {
          const feedBody = await get(`/catalysts/feed${qs({ ...q, limit: q.limit ?? 50 })}`);
          const seen = new Set<string>();
          for (const it of unwrap(feedBody, 'items')) {
            const sts = Array.isArray(it.source_tickers) ? it.source_tickers : [];
            for (const t of sts) {
              if (typeof t === 'string' && t && !seen.has(t)) {
                seen.add(t);
                tickers.push(t);
              }
            }
            if (tickers.length >= 50) break;
          }
        }
        if (!tickers.length) return [];
        const body = await post('/catalysts/tickers/batch', {
          tickers: tickers.slice(0, 50),
          window_hours: q.windowHours,
          limit: q.limit,
          include_neutral: true,
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
      () => post(`/catalysts/analysis-jobs/${encodeURIComponent(jobId)}/cancel`, { confirm: true }).then((d) => nAnalysisJob(d, jobId)),
    ),
  themeName: (themeId: string): string => fx2.getHotspotThemeName(themeId),
};
