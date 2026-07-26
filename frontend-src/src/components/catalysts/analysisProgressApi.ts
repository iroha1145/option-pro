import { ApiError, get, isMock } from '@/api/client';
import {
  normalizeNewsAnalysisProgress,
  type NewsAnalysisProgress,
} from './analysisProgressContract';
import { t } from '../../i18n/core.ts';

export async function fetchNewsAnalysisProgress(): Promise<NewsAnalysisProgress> {
  if (isMock) {
    throw new ApiError(503, t('演示模式不提供真实新闻分析进度'));
  }
  const response = await get('/catalysts/analysis-progress');
  return normalizeNewsAnalysisProgress(response);
}
