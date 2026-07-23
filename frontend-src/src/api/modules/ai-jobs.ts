/** AI 任务域：POST /api/ai/jobs/* · GET /api/ai/jobs/{id} · POST /cancel */
import { ApiError, get, idFromLocation, mockOr, postCreate, post } from '../client';
import {
  aiJobResultSummary,
  normalizeAiJob,
  normalizeJobStatus,
} from '../aiJobNormalize';
import * as fx2 from '@/mocks/fixtures2';
import type { AiJob } from '../types';

export { aiJobResultSummary, normalizeAiJob, normalizeJobStatus };

export interface OptionAlertInput {
  strike: number;
  type: 'call' | 'put';
  expiration: string;
  dte: number;
  volume: number;
  open_interest: number;
  implied_volatility?: number;
  premium_flow?: number;
  vol_oi_ratio?: number;
  reasons: string[];
  signal: 'unknown';
  inferred_direction: 'unknown';
  moneyness: 'itm' | 'otm' | 'atm' | 'unavailable';
  direction: 'unknown';
  direction_confidence: 0;
  direction_status: 'unavailable_without_trade_side';
  direction_deprecated: true;
  direction_note: string;
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
    alerts?: OptionAlertInput[];
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
