/**
 * 新闻分析与市场焦点周期的任务原因码 → 读者看得懂的说法。
 *
 * 后端 error_code 是排查用的机器标识，界面与提示只展示这里的句子；认不出的码返回 null，
 * 由调用方给通用句，绝不把原始码打到页面上。排队期的推迟码（并发上限、冷却、轮询推迟）
 * 表示任务还会自动继续，不是失败原因，同样返回 null。
 */
import { aiJobKnownErrorMessage } from '../../api/aiJobNormalize.ts';
import { t } from '../../i18n/core.ts';

const DEFERRAL_CODES = new Set([
  'global_concurrency_limit',
  'analysis_cooldown_active',
  'provider_poll_deferred',
  'provider_cancel_deferred',
]);

const QUOTA_USED = t('今天的 AI 用量已用完，这次没有执行分析');
const PROVIDER_DOWN = t('模型服务暂时不可用，请稍后重试');
const PROVIDER_TIMEOUT = t('模型服务长时间没有返回结果，请重试');
const INCOMPLETE = t('分析内容未生成完整，请重试');
const CREDIT = t('模型服务余额不足，充值后可重试');
const NEWS_CHANGED = t('新闻内容已更新，请重新分析');
const HOTSPOTS_CHANGED = t('热点数据已更新，请重新计算');

const REASONS: Record<string, string> = {
  schema_validation_failed: t('模型返回的结果没有通过格式或语言检查，请重试'),
  legacy_output_hidden: t('旧版分析结果已不再展示，请重新分析'),
  provider_credit_exhausted: CREDIT,
  credit_balance_exhausted: CREDIT,
  daily_token_limit_reached: QUOTA_USED,
  daily_output_token_limit_reached: QUOTA_USED,
  budget_blocked: QUOTA_USED,
  daily_budget_usd_reached: t('今天的 AI 预算已用完，这次没有执行分析'),
  daily_job_limit_reached: t('今天的 AI 分析次数已用完，这次没有执行分析'),
  submission_outcome_unknown: t('未能确认上次分析结果，请重试'),
  provider_incomplete_max_output_tokens: INCOMPLETE,
  provider_incomplete: INCOMPLETE,
  manual_analysis_disabled: t('AI 分析已关闭'),
  runtime_configuration_changed: t('分析设置已更新，请重新生成'),
  runtime_settings_unavailable: t('暂时无法读取分析设置，请稍后重试'),
  provider_poll_timeout: PROVIDER_TIMEOUT,
  provider_poll_timeout_cancelled: PROVIDER_TIMEOUT,
  provider_response_expired: t('模型结果已过期，请重试'),
  provider_refusal: t('模型拒绝处理这条内容'),
  provider_rate_limited: t('模型服务繁忙，请稍后重试'),
  provider_unavailable: PROVIDER_DOWN,
  provider_server_error: PROVIDER_DOWN,
  provider_failed: PROVIDER_DOWN,
  provider_auth_failed: t('模型服务密钥无效，请检查设置'),
  ai_job_payload_too_large: t('内容过长，无法分析'),
  ai_job_result_too_large: t('分析结果过大，无法保存'),
  news_identity_mismatch: NEWS_CHANGED,
  news_ticker_binding_mismatch: NEWS_CHANGED,
  focus_prepare_expired: t('热点准备已过期，请重新计算'),
  market_focus_payload_mismatch: HOTSPOTS_CHANGED,
  market_focus_input_hash_mismatch: HOTSPOTS_CHANGED,
  market_focus_event_binding_mismatch: HOTSPOTS_CHANGED,
  market_focus_ticker_binding_mismatch: HOTSPOTS_CHANGED,
  market_focus_cycle_mismatch: HOTSPOTS_CHANGED,
  market_focus_as_of_mismatch: HOTSPOTS_CHANGED,
};

/** 已知的失败原因；未知码与排队期推迟码返回 null。 */
export function analysisFailureReason(code: string | null | undefined): string | null {
  const key = String(code ?? '').trim();
  if (!key || DEFERRAL_CODES.has(key)) return null;
  // 新闻与焦点特有的说法优先；其余错误码沿用个股分析面板的共用表，避免两边各缺一半。
  return REASONS[key] ?? aiJobKnownErrorMessage(key);
}

/** 新闻分析失败面板与提示用：总有一句可读的原因。 */
export function newsAnalysisFailureText(code: string | null | undefined): string {
  return analysisFailureReason(code) ?? t('这次分析没有完成，可以重试');
}

/** 焦点周期终态（失败 / 取消 / 预算受限）的标题与原因。 */
export function focusCycleOutcome(
  status: string | null | undefined,
  code: string | null | undefined,
): { title: string; reason: string } {
  if (status === 'cancelled' || status === 'canceled') {
    return { title: t('焦点周期已取消'), reason: t('可以重新触发计算') };
  }
  if (status === 'budget_blocked') {
    return { title: t('焦点周期没有执行'), reason: analysisFailureReason(code) ?? QUOTA_USED };
  }
  return {
    title: t('焦点周期计算失败'),
    reason: analysisFailureReason(code) ?? t('这次计算没有完成，可以重试'),
  };
}
