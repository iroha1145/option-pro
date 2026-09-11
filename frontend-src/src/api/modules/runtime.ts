/** 运行时域：worker 动作与等待。设置读写走 adminApi。 */
import { ApiError, get, post, mockOr } from '../client';
import { asRec } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import { t } from '../../i18n/core.ts';
import { workerWaitDecision, workerWaitHasTimedOut } from '../../lib/screenerScanFlow.ts';

export interface StrengthRefreshParameters {
  universe: 'themes';
  timeframe: 'short' | 'mid' | 'long' | 'all';
  profile: 'conservative' | 'balanced' | 'aggressive';
  top: number;
  sector_id: string | null;
  min_price: number;
  min_avg_dollar_volume: number;
  include_options: boolean;
}

export interface WorkerAction {
  requestId: string;
  action: string;
  status: string;
  errorCode: string | null;
  details: Record<string, unknown>;
  reused: boolean | null;
  reason: string | null;
  completedAt: string | null;
  requestedAt: string | null;
}

function mapWorkerAction(d: unknown): WorkerAction {
  const r = asRec(d);
  return {
    requestId: String(r.request_id ?? ''),
    action: String(r.action_type ?? ''),
    status: String(r.status ?? ''),
    errorCode: typeof r.error_code === 'string' ? r.error_code : null,
    details: asRec(r.details),
    reused: typeof r.reused === 'boolean' ? r.reused : null,
    reason: typeof r.reason === 'string' ? r.reason : null,
    completedAt: typeof r.completed_at === 'string' ? r.completed_at : null,
    requestedAt: typeof r.requested_at === 'string' ? r.requested_at : null,
  };
}

export const runtimeApi = {
  workerAction: (action: string, parameters?: StrengthRefreshParameters): Promise<WorkerAction> =>
    mockOr(
      async () => {
        await fx2.postWorkerAction(action);
        return { requestId: `mock-${Date.now()}`, action, status: 'completed', errorCode: null, details: parameters ? { parameters } : {}, reused: false, reason: 'queued', completedAt: null, requestedAt: null };
      },
      () =>
        post(`/worker/actions/${encodeURIComponent(action)}`, parameters ? { parameters } : {}).then(mapWorkerAction),
    ),
  workerActionStatus: (requestId: string): Promise<WorkerAction> =>
    mockOr(
      () => Promise.resolve({ requestId, action: '', status: 'completed', errorCode: null, details: {}, reused: null, reason: null, completedAt: null, requestedAt: null }),
      () => get(`/worker/actions/${encodeURIComponent(requestId)}`).then(mapWorkerAction),
    ),
  waitForWorkerAction: async (
    requestId: string,
    timeoutMs = 1_200_000,
    options: { onProgress?: (action: WorkerAction) => void; shouldContinue?: () => boolean } = {},
  ): Promise<WorkerAction> => {
    const deadline = Date.now() + timeoutMs;
    while (!workerWaitHasTimedOut(Date.now(), deadline)) {
      if (options.shouldContinue && !options.shouldContinue()) throw new DOMException('Scan superseded', 'AbortError');
      const action = await runtimeApi.workerActionStatus(requestId);
      if (options.shouldContinue && !options.shouldContinue()) throw new DOMException('Scan superseded', 'AbortError');
      options.onProgress?.(action);
      const decision = workerWaitDecision(action.status);
      if (decision === 'done') return action;
      if (decision === 'failed') {
        throw new ApiError(503, action.errorCode ?? t('后台扫描失败'), { bizCode: action.errorCode ?? undefined, payload: action });
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1_500));
    }
    throw new ApiError(504, t('后台扫描等待超时'), { bizCode: 'worker_action_timeout' });
  },
};
