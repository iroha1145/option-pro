/**
 * Owner 管理接口（催化剂运维面板专用）
 * - 数据刷新：POST /api/catalysts/refresh {operation_type} → 202 {request_id}；GET /refresh/{id} 轮询（owner）
 * - 后台任务：POST /api/worker/actions/{type}（focus_refresh/strength_refresh/breakout_refresh，空体走服务端默认参数）
 *   GET /api/worker/status（owner）报 worker 健康与任务清单
 * - 运行设置：GET/PUT /api/runtime-settings（乐观锁 expected_version）+ POST /rollback + GET /history
 * mock 模式提供确定性桩（面板仅 owner 可见，「模拟登录」后可演练交互）。
 */
import { get, mockOr, post, put } from '@/api/client';
import { asRec, pickB, pickN, pickS, unwrap, type Rec } from '@/api/live';

export type RefreshOperation = 'news' | 'calendar' | 'source_health';
export type WorkerActionType = 'focus_refresh' | 'strength_refresh' | 'breakout_refresh';

export interface ManualOpTicket {
  requestId: string | null;
  status: string;
  reason: string | null;
  retryAfter: number | null;
}

export interface WorkerTaskState {
  name: string;
  enabled: boolean;
  healthy: boolean;
  lastSuccessAt: string | null;
  note: string | null;
}

export interface WorkerHealth {
  healthy: boolean;
  status: string;
  tasks: WorkerTaskState[];
}

export interface RuntimeToggles {
  manualAnalysisEnabled: boolean | null;
  scheduledAnalysisEnabled: boolean | null;
}

export interface RuntimeDoc {
  version: number;
  updatedAt: string;
  toggles: RuntimeToggles;
}

function nTicket(d: unknown): ManualOpTicket {
  const r = asRec(d);
  return {
    requestId: pickS(r, 'request_id', 'requestId', 'id'),
    status: pickS(r, 'status') ?? 'queued',
    reason: pickS(r, 'reason'),
    retryAfter: pickN(r, 'retry_after_seconds', 'retry_after'),
  };
}

function nTask(r: Rec): WorkerTaskState {
  const failures = pickN(r, 'consecutive_failures') ?? 0;
  return {
    name: pickS(r, 'task_name', 'name') ?? '',
    enabled: pickB(r, 'enabled') ?? true,
    healthy: (pickB(r, 'healthy') ?? true) && failures === 0,
    lastSuccessAt: pickS(r, 'last_success_at', 'last_success', 'last_run_at', 'updated_at'),
    note: pickS(r, 'last_error_code', 'last_error', 'note'),
  };
}

function nWorker(d: unknown): WorkerHealth {
  const r = asRec(d);
  const worker = asRec(r.worker ?? r);
  return {
    healthy: pickB(worker, 'healthy') ?? false,
    status: pickS(worker, 'status') ?? (pickB(worker, 'healthy') ? 'healthy' : 'unknown'),
    tasks: unwrap(worker, 'tasks').map(nTask).filter((t) => t.name),
  };
}

function nRuntimeDoc(d: unknown): RuntimeDoc {
  const r = asRec(d);
  const settings = asRec(r.settings);
  const ai = asRec(settings.ai);
  const catalyst = asRec(settings.catalyst);
  return {
    version: pickN(r, 'version') ?? 1,
    updatedAt: pickS(r, 'updated_at', 'updatedAt') ?? '',
    toggles: {
      manualAnalysisEnabled: pickB(ai, 'manual_analysis_enabled'),
      scheduledAnalysisEnabled: pickB(catalyst, 'scheduled_analysis_enabled'),
    },
  };
}

/* ---------------- mock 桩（确定性，供 owner 模拟态演练） ---------------- */
let mockRuntimeVersion = 3;
let mockToggles: RuntimeToggles = { manualAnalysisEnabled: true, scheduledAnalysisEnabled: true };
const MOCK_TASKS = ['focus_refresh', 'strength_refresh', 'breakout_refresh'];

export const adminApi = {
  catalystRefresh: (operation: RefreshOperation): Promise<ManualOpTicket> =>
    mockOr(
      () => ({ requestId: `refresh_${operation}`, status: 'queued', reason: null, retryAfter: null }),
      () => post('/catalysts/refresh', { operation_type: operation }).then(nTicket),
    ),
  catalystRefreshStatus: (requestId: string): Promise<ManualOpTicket> =>
    mockOr(
      () => ({ requestId, status: 'completed', reason: null, retryAfter: null }),
      () => get(`/catalysts/refresh/${encodeURIComponent(requestId)}`).then(nTicket),
    ),
  workerAction: (action: WorkerActionType): Promise<ManualOpTicket> =>
    mockOr(
      () => ({ requestId: `act_${action}`, status: 'queued', reason: null, retryAfter: null }),
      () => post(`/worker/actions/${encodeURIComponent(action)}`, {}).then(nTicket),
    ),
  workerStatus: (): Promise<WorkerHealth> =>
    mockOr(
      () => ({
        healthy: true,
        status: 'healthy',
        tasks: MOCK_TASKS.map((t) => ({ name: t, enabled: true, healthy: true, lastSuccessAt: new Date().toISOString(), note: null })),
      }),
      () => get('/worker/status').then(nWorker),
    ),
  runtimeSettings: (): Promise<RuntimeDoc> =>
    mockOr(
      () => ({ version: mockRuntimeVersion, updatedAt: new Date().toISOString(), toggles: { ...mockToggles } }),
      () => get('/runtime-settings').then(nRuntimeDoc),
    ),
  updateRuntimeSettings: (expectedVersion: number, toggles: RuntimeToggles): Promise<RuntimeDoc> =>
    mockOr(
      () => {
        mockRuntimeVersion += 1;
        mockToggles = { ...toggles };
        return { version: mockRuntimeVersion, updatedAt: new Date().toISOString(), toggles: { ...mockToggles } };
      },
      () =>
        put('/runtime-settings', {
          expected_version: expectedVersion,
          settings: {
            ...(toggles.manualAnalysisEnabled !== null ? { ai: { manual_analysis_enabled: toggles.manualAnalysisEnabled } } : {}),
            ...(toggles.scheduledAnalysisEnabled !== null
              ? { catalyst: { scheduled_analysis_enabled: toggles.scheduledAnalysisEnabled } }
              : {}),
          },
        }).then(nRuntimeDoc),
    ),
  rollbackRuntimeSettings: (expectedVersion: number, targetVersion: number): Promise<RuntimeDoc> =>
    mockOr(
      () => {
        mockRuntimeVersion += 1;
        return { version: mockRuntimeVersion, updatedAt: new Date().toISOString(), toggles: { ...mockToggles } };
      },
      () => post('/runtime-settings/rollback', { expected_version: expectedVersion, target_version: targetVersion }).then(nRuntimeDoc),
    ),
  runtimeHistory: (): Promise<{ version: number; updatedAt: string; current: boolean }[]> =>
    mockOr(
      () => [
        { version: mockRuntimeVersion, updatedAt: new Date().toISOString(), current: true },
        { version: mockRuntimeVersion - 1, updatedAt: new Date(Date.now() - 86_400_000).toISOString(), current: false },
      ],
      () =>
        get('/runtime-settings/history').then((d) =>
          unwrap(d, 'revisions').map((r) => ({
            version: pickN(r, 'version') ?? 0,
            updatedAt: pickS(r, 'updated_at', 'updatedAt') ?? '',
            current: pickB(r, 'current') ?? false,
          })),
        ),
    ),
};
