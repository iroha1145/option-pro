import type { AiJob, AiJobStatus } from './types.ts';
import { asRec } from './live.ts';
import { t } from '../i18n/core.ts';

const KIND_MAP: Record<string, AiJob['kind']> = {
  earnings_impact: 'earnings-impact',
  option_alerts: 'option-alerts',
  news_impact: 'news-analysis',
  signal_analysis: 'signal-analysis',
  market_focus: 'market-focus',
};

function firstString(
  value: Record<string, unknown>,
  ...keys: string[]
): string | undefined {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === 'string' && candidate.trim()) return candidate;
  }
  return undefined;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * 后端任务状态归一。未知状态按失败处理，避免把失联任务继续显示成运行中。
 */
export function normalizeJobStatus(raw: unknown): AiJobStatus {
  const status = String(raw ?? '');
  switch (status) {
    case 'preparing':
    case 'pending':
    case 'queued':
      return 'queued';
    case 'in_progress':
    case 'processing':
    case 'running':
    case 'cancel_requested':
      return 'in_progress';
    case 'completed':
    case 'succeeded':
      return 'succeeded';
    case 'canceled':
    case 'cancelled':
      return 'cancelled';
    default:
      return 'failed';
  }
}

/**
 * 保留后端结构化结果；接口没有 progress 时返回 null，不推算演示百分比。
 */
export function normalizeAiJob(raw: unknown, fallbackId?: string | null): AiJob {
  const record = asRec(raw);
  const id = firstString(record, 'id', 'job_id') ?? fallbackId ?? '';
  const rawKind = firstString(record, 'kind', 'job_type') ?? '';
  const status = normalizeJobStatus(record.status);
  const rawProgress = finiteNumber(record.progress);
  const rawResult = record.result;
  const result =
    typeof rawResult === 'string'
      ? rawResult
      : rawResult !== null &&
          typeof rawResult === 'object' &&
          !Array.isArray(rawResult)
        ? (rawResult as Record<string, unknown>)
        : undefined;

  return {
    id,
    kind: KIND_MAP[rawKind] ?? ((rawKind || 'news-analysis') as AiJob['kind']),
    status,
    progress:
      rawProgress === null ? null : Math.min(100, Math.max(0, rawProgress)),
    createdAt:
      firstString(record, 'createdAt', 'submitted_at', 'created_at') ?? '',
    updatedAt:
      firstString(record, 'updatedAt', 'updated_at', 'completed_at') ?? '',
    result,
    error:
      firstString(record, 'error', 'error_code', 'message') ?? undefined,
    errorDetail: firstString(record, 'error_detail') ?? undefined,
  };
}

/** 从结构化任务结果读取正文摘要；没有正文时保持 null。 */
export function aiJobResultSummary(result: unknown): string | null {
  if (typeof result === 'string') {
    const text = result.trim();
    return text || null;
  }
  const record = asRec(result);
  return (
    firstString(record, 'summary', 'headline_summary', 'text', 'analysis') ?? null
  );
}

/**
 * 任务没到模型就被挡下（budget_blocked 或分析开关关闭）时的原因。
 * 这类失败立刻重试只会再建一个同样被挡的任务，界面应说明原因而不是只写「失败」。
 */
export function aiJobBlockedMessage(job: Pick<AiJob, 'status' | 'error'> | null | undefined): string | null {
  if (job?.status !== 'failed') return null;
  if (job.error === 'daily_token_limit_reached') return t('今日 Token 额度已用完，额度重置后再试');
  if (job.error === 'manual_analysis_disabled') return t('手动分析功能当前未启用');
  return null;
}
