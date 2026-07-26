import { t } from '../../i18n/core.ts';
export interface FocusCycleRequestBody {
  trigger: 'manual';
  expected_prepared_revision?: number;
  retry_cycle_id?: string;
  force?: true;
}

/**
 * 手动触发必须绑定热点准备区版本。
 * 版本已被消费且仍有可用热点时，显式重算一次；空准备区不得误发付费任务。
 */
export function buildFocusCycleRequestBody(
  preparedRevision: number | null,
  hasNewHotspots: boolean | null,
  preparedHotCount: number | null,
  retryCycleId: string | null = null,
): FocusCycleRequestBody {
  const body: FocusCycleRequestBody = { trigger: 'manual' };
  if (retryCycleId) {
    const normalized = retryCycleId.trim();
    if (!/^mfc_[0-9a-f]{32}$/.test(normalized)) {
      throw new Error(t('市场焦点周期编号无效'));
    }
    body.retry_cycle_id = normalized;
    return body;
  }
  if (preparedRevision !== null) body.expected_prepared_revision = preparedRevision;
  if (
    preparedRevision !== null
    && preparedRevision > 0
    && hasNewHotspots === false
    && preparedHotCount !== null
    && preparedHotCount > 0
  ) {
    body.force = true;
  }
  return body;
}

export function focusCyclePollPath(cycleId: string): string {
  const normalized = cycleId.trim();
  if (!/^mfc_[0-9a-f]{32}$/.test(normalized)) {
    throw new Error(t('市场焦点周期编号无效'));
  }
  return `/catalysts/market-focus-cycles/${encodeURIComponent(normalized)}`;
}
