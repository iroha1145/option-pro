/** 运行时域：runtime-settings / history / rollback / worker */
import { ApiError, get, post, put, mockOr } from '../client';
import { asRec, pickN } from '../live';
import * as session from '@/mocks/session';
import * as fx2 from '@/mocks/fixtures2';
import type { RuntimeHistoryEntry, RuntimeSettings, WorkerTask } from '../types';

/** 契约 GET /api/runtime-settings → {version, settings:{ai,catalyst}, updated_at} */
interface LiveSettingsEnvelope {
  version: number;
  settings: unknown;
  updated_at?: string;
}

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
}

function mapWorkerAction(d: unknown): WorkerAction {
  const r = asRec(d);
  return {
    requestId: String(r.request_id ?? ''),
    action: String(r.action_type ?? ''),
    status: String(r.status ?? ''),
    errorCode: typeof r.error_code === 'string' ? r.error_code : null,
    details: asRec(r.details),
  };
}

function liveVersion(d: unknown): number {
  return pickN(asRec(d), 'version') ?? 0;
}

/** 409 version_conflict：payload 含 current_version（兼容 error 嵌套），缺失则重新 GET */
async function currentVersionFrom(e: ApiError): Promise<number> {
  const p = asRec(e.payload);
  const errObj = asRec(p.error);
  const cv = pickN(p, 'current_version') ?? pickN(errObj, 'current_version');
  if (cv !== null) return cv;
  return liveVersion(await get('/runtime-settings'));
}

const isVersionConflict = (e: unknown): e is ApiError =>
  e instanceof ApiError && e.code === 409 && (e.bizCode === 'version_conflict' || e.bizCode === undefined);

/**
 * 乐观锁写操作（契约 §runtime-settings）：
 * 先 GET 取真实 version → 提交 {expected_version, ...}；
 * 409 version_conflict 时用 current_version 自动重试一次，再失败则抛错。
 */
async function withOptimisticLock(submit: (expectedVersion: number) => Promise<RuntimeSettings>): Promise<RuntimeSettings> {
  const cur = await get<LiveSettingsEnvelope>('/runtime-settings');
  try {
    return await submit(liveVersion(cur));
  } catch (e) {
    if (!isVersionConflict(e)) throw e;
    const retryVersion = await currentVersionFrom(e);
    return submit(retryVersion); // 第二次冲突直接抛出
  }
}

export const runtimeApi = {
  settings: (): Promise<RuntimeSettings> => mockOr(() => session.getSettings(), () => get('/runtime-settings')),
  updateSettings: (patch: Partial<RuntimeSettings>): Promise<RuntimeSettings> =>
    mockOr(
      () => session.updateSettings(patch),
      () => withOptimisticLock((v) => put('/runtime-settings', { expected_version: v, settings: patch })),
    ),
  history: (): Promise<RuntimeHistoryEntry[]> =>
    mockOr(() => session.getSettingsHistory(), () => get('/runtime-settings/history')),
  rollback: (id: string): Promise<RuntimeSettings> =>
    mockOr(
      () => session.rollbackSettings(id),
      () => withOptimisticLock((v) => post('/runtime-settings/rollback', { expected_version: v, target_version: id })),
    ),
  workerStatus: (): Promise<WorkerTask[]> => mockOr(() => fx2.getWorkerStatus(), () => get('/worker/status')),
  workerAction: (action: string, parameters?: StrengthRefreshParameters): Promise<WorkerAction> =>
    mockOr(
      async () => {
        await fx2.postWorkerAction(action);
        return { requestId: `mock-${Date.now()}`, action, status: 'completed', errorCode: null, details: {} };
      },
      () =>
        post(`/worker/actions/${encodeURIComponent(action)}`, parameters ? { parameters } : {}).then(mapWorkerAction),
    ),
  workerActionStatus: (requestId: string): Promise<WorkerAction> =>
    mockOr(
      () => Promise.resolve({ requestId, action: '', status: 'completed', errorCode: null, details: {} }),
      () => get(`/worker/actions/${encodeURIComponent(requestId)}`).then(mapWorkerAction),
    ),
  waitForWorkerAction: async (requestId: string, timeoutMs = 1_200_000): Promise<WorkerAction> => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const action = await runtimeApi.workerActionStatus(requestId);
      if (action.status === 'completed') return action;
      if (action.status === 'failed' || action.status === 'cancelled' || action.status === 'canceled') {
        throw new ApiError(503, action.errorCode ?? '后台扫描失败', { bizCode: action.errorCode ?? undefined, payload: action });
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1_500));
    }
    throw new ApiError(504, '后台扫描等待超时', { bizCode: 'worker_action_timeout' });
  },
};
