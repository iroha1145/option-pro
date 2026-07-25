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

export const OWNER_SESSION_INVALID_EVENT = 'optix:owner-session-invalid';

export function notifyOwnerSessionInvalid(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(OWNER_SESSION_INVALID_EVENT));
  }
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

/** live 模式请求（返回原始 Response 供需要读头的场景，如 202 Location） */
export async function requestRaw(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? 'GET').toUpperCase();
  const isWrite = method !== 'GET' && method !== 'HEAD';
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    redirect: 'error',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      // 后端 require_same_origin_json：写操作必须带自定义头
      ...(isWrite ? { 'X-Optix-Action': '1' } : {}),
      ...(init?.headers ?? {}),
    },
  });
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
    if (res.status === 401 && bizCode === 'owner_login_required') {
      notifyOwnerSessionInvalid();
    }
    throw new ApiError(res.status, message, { bizCode, retryable, retryAfter, payload });
  }
  return res;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
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

export function get<T>(path: string) {
  return request<T>(path, { method: 'GET' });
}
export function post<T>(path: string, body?: unknown) {
  return request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
}
export function put<T>(path: string, body?: unknown) {
  return request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) });
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
