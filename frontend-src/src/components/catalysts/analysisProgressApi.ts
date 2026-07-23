import { ApiError, get, isMock } from '@/api/client';
import {
  normalizeNewsAnalysisProgress,
  type NewsAnalysisProgress,
} from './analysisProgressContract';

export async function fetchNewsAnalysisProgress(): Promise<NewsAnalysisProgress> {
  if (isMock) {
    throw new ApiError(503, '演示模式不提供真实新闻分析进度');
  }
  const response = await get('/catalysts/analysis-progress');
  return normalizeNewsAnalysisProgress(response);
}
