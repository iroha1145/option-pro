/**
 * API 适配层（design.md §11）
 * - mode: 'mock' | 'live'，由 VITE_API_MODE 一键切换（默认 mock）
 * - live: 同源 /api + credentials:'include'（HttpOnly Cookie 会话）
 * - mock: 本地 fixtures，接口形状 1:1 对齐契约，随机延迟 250–700ms
 * 错误形状统一 { code, message }；503 → UI 显示「快照不可用」空态。
 */

export type ApiMode = 'mock' | 'live';

export const API_MODE: ApiMode =
  (import.meta.env?.VITE_API_MODE as ApiMode | undefined) === 'live' ? 'live' : 'mock';

export const isMock = API_MODE === 'mock';

export interface ApiErrorShape {
  code: number;
  message: string;
}

/**
 * 统一错误对象（对齐后端契约 {code, message, retryable, retry_after}）
 * - code: HTTP 状态码；bizCode: 后端业务码（如 analysis_required / version_conflict /
 *   ai_job_queue_full / login_cooldown / public_snapshot_unavailable / rate_limited）
 * - retryAfter: 秒（Retry-After 头或 body.retry_after_seconds）
 */
export class ApiError extends Error implements ApiErrorShape {
  code: number;
  bizCode?: string;
  retryable?: boolean;
  retryAfter?: number;
  payload?: unknown;
  constructor(code: number, message: string, extras?: { bizCode?: string; retryable?: boolean; retryAfter?: number; payload?: unknown }) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.bizCode = extras?.bizCode;
    this.retryable = extras?.retryable;
    this.retryAfter = extras?.retryAfter;
    this.payload = extras?.payload;
  }
}

const BASE = '/api';

/**
 * 任一主体（管理员或客户）会话失效（审计 P1-08）。
 *
 * 旧事件名只覆盖管理员，于是客户接口返回的 account_login_required 不触发任何
 * 降级：页面继续显示客户用户名和自选管理按钮，请求持续 401。两种主体现在共用
 * 同一条降级通路，由 AccessProvider 重新拉取 /access/status 得出权威结论。
 */
export const PRINCIPAL_INVALID_EVENT = 'optix:principal-invalid';

/** 会让前端认定「当前主体已失效」的业务码。 */
const PRINCIPAL_INVALID_CODES = new Set([
  'owner_login_required',
  'account_login_required',
]);

export function notifyPrincipalInvalid(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(PRINCIPAL_INVALID_EVENT));
  }
}

/**
 * 单次请求超时（审计 P1-10）。
 *
 * fetch 没有默认超时，而 marketRead 会把同一路径的 Promise 放进 inFlight 共享；
 * 半开连接或代理不终止请求时，该路径的所有页面会永远等同一个 Promise，手动刷新
 * 也不会发起新请求，界面永久停在骨架屏且没有任何错误提示。
 */
export const REQUEST_TIMEOUT_MS = 20_000;

/** 组合调用方 signal 与超时；返回的 dispose 必须在请求结束后调用。 */
function withTimeout(
  external: AbortSignal | null | undefined,
  timeoutMs: number,
): { signal: AbortSignal; dispose: () => void; timedOut: () => boolean } {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener('abort', onExternalAbort);
  }
  return {
    signal: controller.signal,
    dispose: () => {
      clearTimeout(timer);
      if (external) external.removeEventListener('abort', onExternalAbort);
    },
    timedOut: () => timedOut,
  };
}

function pick(obj: unknown, ...keys: string[]): unknown {
  if (obj && typeof obj === 'object') {
    for (const k of keys) {
      const v = (obj as Record<string, unknown>)[k];
      if (v !== undefined && v !== null) return v;
    }
  }
  return undefined;
}

/**
 * 查询参数序列化（契约 §0：undefined/null/空串不发出；数组重复键；布尔 → true/false）
 * 返回不含前导 `?` 的串；无参数时返回空串。
 */
export function toQuery(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) {
      for (const item of v) {
        if (item === undefined || item === null || item === '') continue;
        p.append(k, String(item));
      }
      continue;
    }
    p.set(k, String(v));
  }
  return p.toString();
}

export interface RequestOptions extends RequestInit {
  /** 覆盖默认超时；长任务轮询可单独放宽。传 0 表示不超时。 */
  timeoutMs?: number;
}

/** live 模式请求（返回原始 Response 供需要读头的场景，如 202 Location） */
export async function requestRaw(path: string, init?: RequestOptions): Promise<Response> {
  const method = (init?.method ?? 'GET').toUpperCase();
  const isWrite = method !== 'GET' && method !== 'HEAD';
  const { timeoutMs, signal: callerSignal, ...rest } = init ?? {};
  const budget = timeoutMs ?? REQUEST_TIMEOUT_MS;
  const timeout = budget > 0 ? withTimeout(callerSignal, budget) : null;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: 'include',
      redirect: 'error',
      ...rest,
      ...(timeout ? { signal: timeout.signal } : callerSignal ? { signal: callerSignal } : {}),
      headers: {
        'Content-Type': 'application/json',
        // 后端 require_same_origin_json：写操作必须带自定义头
        ...(isWrite ? { 'X-Optix-Action': '1' } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    if (timeout?.timedOut()) {
      throw new ApiError(408, '请求超时，请重试', {
        bizCode: 'request_timeout',
        retryable: true,
      });
    }
    throw error;
  } finally {
    timeout?.dispose();
  }
  if (!res.ok) {
    let message = res.statusText || '请求失败';
    let bizCode: string | undefined;
    let retryable: boolean | undefined;
    let retryAfter: number | undefined;
    let payload: unknown;
    try {
      const body = (await res.json()) as Record<string, unknown>;
      payload = body;
      const errObj = (body?.error && typeof body.error === 'object' ? body.error : body) as Record<string, unknown>;
      const detail = body?.detail && typeof body.detail === 'object' ? (body.detail as Record<string, unknown>) : undefined;
      bizCode = String(
        pick(errObj, 'code') ?? pick(body, 'code') ?? (typeof body?.error === 'string' ? body.error : undefined) ?? (detail ? pick(detail, 'code') : undefined) ?? '',
      ) || undefined;
      const msg = pick(errObj, 'message') ?? pick(body, 'message') ?? (detail ? pick(detail, 'message') : undefined);
      if (typeof msg === 'string' && msg) message = msg;
      const r = pick(errObj, 'retryable');
      if (typeof r === 'boolean') retryable = r;
      const ra = pick(errObj, 'retry_after_seconds') ?? pick(errObj, 'retry_after') ?? res.headers.get('Retry-After');
      const raNum = typeof ra === 'string' ? Number(ra) : (ra as number | undefined);
      if (typeof raNum === 'number' && Number.isFinite(raNum)) retryAfter = raNum;
    } catch {
      /* ignore */
    }
    if (res.status === 401 && bizCode && PRINCIPAL_INVALID_CODES.has(bizCode)) {
      notifyPrincipalInvalid();
    }
    throw new ApiError(res.status, message, { bizCode, retryable, retryAfter, payload });
  }
  return res;
}

export async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const res = await requestRaw(path, init);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * 创建类 POST（契约 §0.4）：202 + `Location: /api/.../{id}` + `Retry-After: 2`
 * body 可能不含任务 id，需从 Location 头提取；body 也可能为空。
 */
export async function postCreate<T = unknown>(
  path: string,
  body?: unknown,
): Promise<{ data: T | undefined; location: string | null; retryAfter: number | null }> {
  const res = await requestRaw(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const location = res.headers.get('Location');
  const ra = res.headers.get('Retry-After');
  const retryAfter = ra !== null && Number.isFinite(Number(ra)) ? Number(ra) : null;
  let data: T | undefined;
  try {
    data = (await res.json()) as T;
  } catch {
    data = undefined; // 202 允许空 body
  }
  return { data, location, retryAfter };
}

/** 从 Location 头提取末段 id（/api/ai/jobs/{id} → {id}） */
export function idFromLocation(location: string | null): string | null {
  if (!location) return null;
  const seg = location.split('?')[0].split('/').filter(Boolean);
  return seg.length ? decodeURIComponent(seg[seg.length - 1]) : null;
}

/* 便捷封装一律透传 options：调用方需要能传 signal 与 timeoutMs，否则超时与
   取消只对直接使用 request() 的少数路径生效。 */
export function get<T>(path: string, options?: RequestOptions) {
  return request<T>(path, { ...options, method: 'GET' });
}
export function post<T>(path: string, body?: unknown, options?: RequestOptions) {
  return request<T>(path, {
    ...options,
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
export function put<T>(path: string, body?: unknown, options?: RequestOptions) {
  return request<T>(path, {
    ...options,
    method: 'PUT',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
/** 无请求体的删除；同源守卫只看 Origin 与 X-Optix-Action 头。 */
export function del<T>(path: string, options?: RequestOptions) {
  return request<T>(path, { ...options, method: 'DELETE' });
}

/** mock 模式：250–700ms 随机延迟 */
export function mockDelay(): Promise<void> {
  const ms = 250 + Math.random() * 450;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 模块内便捷封装：mock 走 fixture，live 走 HTTP */
export async function mockOr<T>(fixture: () => T | Promise<T>, live: () => Promise<T>): Promise<T> {
  if (isMock) {
    await mockDelay();
    return fixture();
  }
  return live();
}

/** mock 下抛 503（数据不可用） */
export function unavailable(feature: string): never {
  throw new ApiError(503, `${feature}暂不可用`);
}
