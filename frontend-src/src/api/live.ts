/**
 * live 响应归一化工具（契约 snake_case → UI camelCase 的容错读取）
 * 原则（api-contract §0.5「不造假」）：契约没有的字段不编造，
 * 映射层返回 null / 空数组，由 UI 渲染「—」；仅做字段名对齐与信封解包。
 */

export type Rec = Record<string, unknown>;

export const asRec = (v: unknown): Rec =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Rec) : {};

/** 信封解包：body 本身是数组原样返回；否则取 keys 中第一个数组字段；都没有 → [] */
export function unwrap(body: unknown, ...keys: string[]): Rec[] {
  if (Array.isArray(body)) return body as Rec[];
  const r = asRec(body);
  for (const k of keys) {
    if (Array.isArray(r[k])) return r[k] as Rec[];
  }
  return [];
}

export const num = (v: unknown): number | null => {
  const n = typeof v === 'string' ? Number(v) : v;
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
};

/** 按候选键取 number（camel/snake 兼容） */
export function pickN(r: Rec, ...keys: string[]): number | null {
  for (const k of keys) {
    const n = num(r[k]);
    if (n !== null) return n;
  }
  return null;
}

export const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

/** 按候选键取 string */
export function pickS(r: Rec, ...keys: string[]): string | null {
  for (const k of keys) {
    const s = str(r[k]);
    if (s) return s;
  }
  return null;
}

/** 按候选键取 boolean */
export function pickB(r: Rec, ...keys: string[]): boolean | null {
  for (const k of keys) {
    if (typeof r[k] === 'boolean') return r[k] as boolean;
  }
  return null;
}
