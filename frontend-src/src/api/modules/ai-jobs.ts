/** AI 任务域：POST /api/ai/jobs/* · GET /api/ai/jobs/{id} · POST /cancel */
import { ApiError, get, idFromLocation, mockOr, postCreate, post } from '../client';
import { asRec, pickN, pickS } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { AiJob, AiJobStatus } from '../types';

/** 契约 AIJobPublic.job_type → UI kind */
const KIND_MAP: Record<string, AiJob['kind']> = {
  earnings_impact: 'earnings-impact',
  option_alerts: 'option-alerts',
  news_impact: 'news-analysis',
  signal_analysis: 'signal-analysis',
  market_focus: 'market-focus',
};

/**
 * 状态归一（api-contract §0.4）：
 * - 活跃 {preparing,pending,queued} → queued；{in_progress,processing,running,cancel_requested} → in_progress
 * - 终态 {completed,succeeded} → succeeded；failed → failed；{canceled,cancelled} → cancelled；
 *   其余失败类终态（insufficient_context/budget_blocked/incomplete_output/
 *   submission_outcome_unknown/worker_interrupted）→ failed（细节由 error 透出）
 */
export function normalizeJobStatus(raw: unknown): AiJobStatus {
  const s = String(raw ?? '');
  switch (s) {
    case 'preparing':
    case 'pending':
    case 'queued':
      return 'queued';
    case 'in_progress':
    case 'processing':
    case 'running':
    case 'cancel_requested':
      return 'in_progress';
    case 'completed':
    case 'succeeded':
      return 'succeeded';
    case 'canceled':
    case 'cancelled':
      return 'cancelled';
    default:
      return 'failed'; // failed 及未知/失败类终态
  }
}

/** 契约 AIJobPublic → UI AiJob（字段名对齐，缺失不编造） */
export function normalizeAiJob(raw: unknown, fallbackId?: string | null): AiJob {
  const r = asRec(raw);
  const id = pickS(r, 'id', 'job_id') ?? fallbackId ?? '';
  const kindRaw = pickS(r, 'kind', 'job_type') ?? '';
  const status = normalizeJobStatus(r.status);
  const resultRaw = r.result;
  let result: string | undefined;
  if (typeof resultRaw === 'string') result = resultRaw;
  else if (resultRaw !== null && typeof resultRaw === 'object') {
    // AI result 内层字段后端按 job_type 不同（残余风险，见 AUDIT-live.md）
    const rr = asRec(resultRaw);
    result = pickS(rr, 'text', 'summary', 'headline_summary') ?? JSON.stringify(resultRaw);
  }
  return {
    id,
    kind: KIND_MAP[kindRaw] ?? ((kindRaw || 'news-analysis') as AiJob['kind']),
    status,
    progress: pickN(r, 'progress') ?? (status === 'queued' ? 5 : status === 'in_progress' ? 50 : 100),
    createdAt: pickS(r, 'createdAt', 'submitted_at', 'created_at') ?? '',
    updatedAt: pickS(r, 'updatedAt', 'updated_at', 'completed_at') ?? '',
    result,
    error: pickS(r, 'error', 'error_code', 'message') ?? undefined,
  };
}

/**
 * 创建类任务 POST（契约 §0.4）：202 + Location:/api/ai/jobs/{id} + Retry-After:2
 * body 不含 job_id 时从 Location 头提取。
 */
export async function postAiJob(path: string, body?: unknown): Promise<AiJob> {
  const { data, location } = await postCreate(path, body);
  const locId = idFromLocation(location);
  const job = normalizeAiJob(data, locId);
  if (!job.id) {
    throw new ApiError(502, '任务创建响应缺少 job_id（body 与 Location 头均未提供）', { payload: data });
  }
  return job;
}

export const aiJobsApi = {
  createEarningsImpact: (ticker: string): Promise<AiJob> =>
    mockOr(() => fx2.createAiJob('earnings-impact', ticker), () => postAiJob('/ai/jobs/earnings-impact', { ticker })),
  createOptionAlerts: (params: {
    tickers: string[];
    force?: boolean;
    alerts?: unknown[];
    underlyingPrice?: number;
    expiration?: string;
  }): Promise<AiJob> =>
    mockOr(
      () => fx2.createAiJob('option-alerts', params.tickers.join(',')),
      // 契约：{ticker, force, alerts, underlying_price, expiration}
      () =>
        postAiJob('/ai/jobs/option-alerts', {
          ticker: params.tickers[0],
          force: params.force ?? false,
          alerts: params.alerts ?? [],
          ...(params.underlyingPrice !== undefined ? { underlying_price: params.underlyingPrice } : {}),
          ...(params.expiration !== undefined ? { expiration: params.expiration } : {}),
        }),
    ),
  get: (id: string): Promise<AiJob> =>
    mockOr(() => fx2.getAiJob(id), () => get(`/ai/jobs/${encodeURIComponent(id)}`).then((d) => normalizeAiJob(d, id))),
  cancel: (id: string): Promise<AiJob> =>
    mockOr(
      () => fx2.cancelAiJob(id),
      // 契约：body {confirm:true}
      () => post(`/ai/jobs/${encodeURIComponent(id)}/cancel`, { confirm: true }).then((d) => normalizeAiJob(d, id)),
    ),
};
