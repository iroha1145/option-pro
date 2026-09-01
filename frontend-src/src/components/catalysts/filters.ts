/** 过滤器模型与默认值（独立模块，供页面/过滤条/面板共享，URL 为唯一事实来源） */
import type { CatalystFeedQuery, NewsAnalysisStatus, NewsClassification } from './api';

/** 主题 ID：字母数字/下划线/连字符，最长 64。非法值一律丢弃，不进 URL、不发给后端。 */
export const THEME_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export function sanitizeThemeId(raw: string | null | undefined): string | null {
  const value = String(raw ?? '').trim();
  return THEME_ID_RE.test(value) ? value : null;
}

export interface CatalystFilters {
  ticker: string;
  windowHours: number;
  classification: '' | NewsClassification;
  analysisStatus: '' | NewsAnalysisStatus;
  minConfidence: number; // 0–1
  minAbsImpact: number; // 0–5
  multiSourceOnly: boolean;
  themeId: string | null;
}

export const DEFAULT_FILTERS: CatalystFilters = {
  ticker: '',
  windowHours: 72,
  classification: '',
  analysisStatus: '',
  minConfidence: 0,
  minAbsImpact: 0,
  multiSourceOnly: false,
  themeId: null,
};

/** CatalystFilters → 契约查询（null/空串 → undefined/''） */
export function toFeedQuery(f: CatalystFilters): CatalystFeedQuery {
  return {
    ticker: f.ticker || undefined,
    windowHours: f.windowHours,
    classification: f.classification || '',
    analysisStatus: f.analysisStatus || '',
    minConfidence: f.minConfidence,
    minAbsImpact: f.minAbsImpact,
    multiSourceOnly: f.multiSourceOnly,
    themeId: f.themeId ?? undefined,
  };
}
