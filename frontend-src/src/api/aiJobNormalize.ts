import type { AiJob, AiJobStatus } from './types.ts';
import { ApiError } from './client.ts';
import { asRec } from './live.ts';
import { normalizeEvidenceSources } from './evidenceSources.ts';
import { getLocale, t } from '../i18n/core.ts';

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
    evidenceSources: normalizeEvidenceSources(record.evidence_sources),
    model: firstString(record, 'model'),
    reasoning: firstString(record, 'reasoning', 'reasoning_effort'),
    usage: Object.fromEntries(
      Object.entries(asRec(record.usage)).filter(([, value]) =>
        value === null || (typeof value === 'number' && Number.isInteger(value) && value >= 0),
      ),
    ) as Record<string, number | null>,
    result,
    error:
      firstString(record, 'error', 'error_code', 'message') ?? undefined,
    errorDetail: firstString(record, 'error_detail') ?? undefined,
    // 旧契约用 status='cancel_requested' 表达同一件事。
    cancelRequested: record.cancel_requested === true || record.status === 'cancel_requested',
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
 * 后端 error_code 到界面文案的共用表：各 AI 任务卡片按同一口径说明失败原因。
 * 原始码是排障标识，不直接显示；认不出的码回落到通用失败说明。
 * 调用方只在任务失败（含 budget_blocked，归一后同为 failed）时翻译：排队、运行、
 * 取消状态的 error_code 可能是上一次推迟留下的旧码，不代表这次的结果。
 * legacy_output_hidden 例外：它出现在已完成但结果被隐藏的任务上。
 */
const AI_JOB_ERROR_TEXT = new Map<string, () => string>([
  ['manual_analysis_disabled', () => t('手动分析已关闭')],
  ['scheduled_analysis_disabled', () => t('自动分析未开启，这次没有执行')],
  ['runtime_configuration_changed', () => t('分析设置已更改，请重新发起分析')],
  ['submission_outcome_unknown', () => t('无法确认模型服务是否收到分析请求，已停止重复提交，请先核对原任务状态')],
  ['provider_incomplete_max_output_tokens', () => t('分析内容过长，没有生成完整，请重试')],
  ['provider_credit_exhausted', () => t('模型服务余额不足，需充值')],
  ['provider_credit_exhausted_hold', () => t('模型服务余额不足，需充值')],
  ['provider_incomplete', () => t('模型服务没有返回完整结果，请重试')],
  ['provider_failed', () => t('模型服务暂时不可用，请稍后重试')],
  ['provider_refusal', () => t('模型拒绝了这次分析')],
  ['provider_poll_timeout', () => t('等待 AI 结果超时，未能确认模型服务已停止处理')],
  ['provider_poll_timeout_cancelled', () => t('等待 AI 结果超时，已取消，可以重试')],
  ['provider_response_expired', () => t('AI 结果已过期，请重新分析')],
  ['provider_auth_failed', () => t('模型服务密钥无效，请检查设置')],
  ['provider_rate_limited', () => t('模型服务繁忙，请稍后重试')],
  ['provider_server_error', () => t('模型服务暂时不可用，请稍后重试')],
  ['provider_request_rejected', () => t('AI 服务拒绝了这次请求')],
  ['provider_unavailable', () => t('模型服务暂时不可用，请稍后重试')],
  ['local_storage_error', () => t('服务器保存数据时出错，请稍后重试')],
  ['schema_validation_failed', () => t('分析结果未通过格式检查，请重试')],
  ['invalid_job_payload', () => t('任务数据无效，无法分析')],
  ...['provider_tool_result_invalid', 'provider_invalid_final_tool', 'provider_unknown_client_tool', 'provider_invalid_tool_response', 'provider_incomplete_tool_result', 'provider_unknown_server_tool', 'provider_tool_limit_exceeded'].map((code): [string, () => string] => [code, () => t('模型返回的内容未通过检查，分析已停止')]),
  ['provider_model_mismatch', () => t('模型服务返回的模型与分析设置不符，请检查服务配置')],
  ['provider_empty_response', () => t('模型没有返回内容，请重试')],
  ['ai_empty_response', () => t('模型没有返回内容，请重试')],
  ['ai_input_too_large', () => t('输入数据过多，超出了分析上限')],
  ['daily_budget_usd_reached', () => t('共享模型日预算不足，东京 09:00 重置后再试')],
  ['daily_token_limit_reached', () => t('今日分析额度已用完，额度重置后再试')],
  ['budget_blocked', () => t('今日分析预算已用完，重置后再试')],
  ['global_concurrency_limit', () => t('同时进行的分析太多，请稍后重试')],
  ['analysis_cooldown_active', () => t('分析冷却中，请稍后重试')],
  ['legacy_output_hidden', () => t('旧版分析结果已不再展示，请重新分析')],
  ['duplicate_request_migrated', () => t('重复的分析请求已合并到另一个任务')],
  ['provider_cancelled', () => t('模型服务取消了这次分析，可以重试')],
  ['provider_response_id_missing', () => t('模型服务的响应不完整，请重试')],
  ['provider_status_unsupported', () => t('模型服务返回了无法识别的状态，请重试')],
  ['ai_job_result_too_large', () => t('分析结果过大，无法保存')],
  ['ai_job_lease_lost', () => t('后台处理意外中断，请重试')],
  ['signal_ticker_mismatch', () => t('分析结果与股票代码不符，已作废，请重试')],
  ['ai_not_configured', () => t('AI 分析服务尚未就绪，暂时不能发起分析')],
  ['ai_sdk_unavailable', () => t('AI 分析服务尚未就绪，暂时不能发起分析')],
  ['runtime_configuration_invalid', () => t('AI 分析服务尚未就绪，暂时不能发起分析')],
]);

/** 共用表认识的错误码才返回文案；认不出返回 null，由调用方决定兜底句。 */
export function aiJobKnownErrorMessage(code: string | null | undefined): string | null {
  const text = code ? AI_JOB_ERROR_TEXT.get(code.trim()) : undefined;
  return text ? text() : null;
}

/** 失败任务的原因说明；code 为空或认不出时给通用句。 */
export function aiJobErrorMessage(code: string | null): string {
  return aiJobKnownErrorMessage(code) ?? t('分析任务失败');
}

/** 立刻重试只会再次被挡下的失败（开关、额度、余额、认证、服务未配置）：按钮写「关闭」而不是「重试」。 */
const RETRY_BLOCKED_CODES: ReadonlySet<string> = new Set([
  'submission_outcome_unknown',
  'manual_analysis_disabled',
  'scheduled_analysis_disabled',
  'daily_budget_usd_reached',
  'daily_token_limit_reached',
  'budget_blocked',
  'provider_credit_exhausted',
  'provider_credit_exhausted_hold',
  'provider_auth_failed',
  'ai_not_configured',
  'ai_sdk_unavailable',
  'runtime_configuration_invalid',
]);

/**
 * 任务被挡下、重试也无济于事时的原因；其余情况返回 null。
 * 这类失败立刻重试只会再建一个同样被挡的任务，界面应说明原因而不是只写「失败」。
 */
export function aiJobBlockedMessage(job: Pick<AiJob, 'status' | 'error'> | null | undefined): string | null {
  if (job?.status !== 'failed' || !job.error || !RETRY_BLOCKED_CODES.has(job.error)) return null;
  return aiJobErrorMessage(job.error);
}

/** 排队期的推迟码：任务还会自动继续，已结束的任务残留这些码时不是失败原因。 */
export const AI_JOB_DEFERRAL_CODES: ReadonlySet<string> = new Set([
  'global_concurrency_limit',
  'analysis_cooldown_active',
  'provider_poll_deferred',
  'provider_cancel_deferred',
]);

/**
 * 排队任务被推迟的原因。只在任务仍排队时读：开始处理后后端不清 error_code，
 * 那时留下的是上一次推迟的旧码，不能当成现状显示。
 */
export function aiJobDeferralMessage(job: Pick<AiJob, 'status' | 'error'> | null | undefined): string | null {
  if (job?.status !== 'queued') return null;
  switch (job.error) {
    case 'global_concurrency_limit':
      return t('同时进行的分析较多，稍后自动开始');
    case 'analysis_cooldown_active':
      return t('上一次分析刚结束，冷却后自动开始');
    case 'provider_credit_exhausted_hold':
      return t('模型服务余额不足，充值后自动继续');
    default:
      return null;
  }
}

const HAN = /[\u4e00-\u9fff]/;
const KANA = /[\u3040-\u30ff]/;

/**
 * 创建任务失败的说明。后端 message 是中文，英日界面不直接显示；
 * 队列满的响应不带 message，只有 bizCode 与 Retry-After。
 * 日文译文也有汉字，靠有没有假名区分：没有假名的汉字句子才当成后端中文。
 */
export function aiJobCreateErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.bizCode) {
      case 'ai_job_queue_full':
        return error.retryAfter !== undefined
          ? t('分析队列已满，请约 {seconds} 秒后再试', { seconds: Math.ceil(error.retryAfter) })
          : t('分析队列已满，请稍后再试');
      case 'rate_limited':
        return error.retryAfter !== undefined
          ? t('请求过于频繁，请约 {seconds} 秒后再试', { seconds: Math.ceil(error.retryAfter) })
          : t('请求过于频繁，请稍后再试');
      case 'manual_analysis_disabled':
        return aiJobErrorMessage('manual_analysis_disabled');
      case 'read_only_mode':
        return t('当前为只读模式，不能发起分析');
      case 'stale_signal_evidence':
        return t('技术信号已过期，请先手动获取最新行情再分析');
      case 'runtime_settings_unavailable':
        return t('暂时读不到分析设置，请稍后再试');
      case 'not_configured':
      case 'unsupported_provider_capability':
      case 'runtime_configuration_invalid':
      case 'ai_runtime_unavailable':
        return t('AI 分析服务尚未就绪，暂时不能发起分析');
    }
    if (error.code === 413) return aiJobErrorMessage('ai_input_too_large');
  }
  if (error instanceof Error && error.message) {
    const backendChinese = HAN.test(error.message) && !KANA.test(error.message);
    if (getLocale() === 'zh' || !backendChinese) return error.message;
  }
  return t('任务创建失败');
}
