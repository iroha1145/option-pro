/**
 * live 响应归一化工具（契约 snake_case → UI camelCase 的容错读取）
 * 原则（api-contract §0.5「不造假」）：契约没有的字段不编造，
 * 映射层返回 null / 空数组，由 UI 渲染「—」；仅做字段名对齐与信封解包。
 */

import { t } from '../i18n/core.ts';

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

/**
 * 后端下发的中文展示标签（公司名、板块名、公司简介）在归一层就地本地化：
 * 这些是 backend/app/services/zh_names.py 与 sectors.py 里的静态中文表（见
 * i18n/dict/companies.ts），不是模型当次生成的正文，所以过 t() 是正确的。
 * 集中在此一处而非几十个渲染点，避免逐个遗漏。表里没有的（后端本就回退成裸
 * 代码的情形）t() 缺译回退原值，原样透传，不会显示空白或裸中文。
 * AI 正文（titleZh / summaryZh / analysis 等）不经过这里，仍裸透传。
 */
export const localizeLabel = (v: string | null): string | null =>
  v === null ? null : t(v);

/** 取候选键并本地化（公司名 / 板块名 / 简介专用）。 */
export function pickLabel(r: Rec, ...keys: string[]): string | null {
  return localizeLabel(pickS(r, ...keys));
}

/** 按候选键取 boolean */
export function pickB(r: Rec, ...keys: string[]): boolean | null {
  for (const k of keys) {
    if (typeof r[k] === 'boolean') return r[k] as boolean;
  }
  return null;
}
