import { BusyIcon } from '@/components/shared/IconSwap';
import { useRetryCountdown } from '@/hooks/useRetryCountdown';
import { cn } from '@/lib/utils';
import type { SectorIvRefreshState } from '@/api/modules/sectors';
import type { SectorIvFlowError } from './ivRefreshFlow';
import { t } from '../../i18n/core.ts';

interface IvRefreshControlProps {
  refresh: SectorIvRefreshState;
  submitting: boolean;
  actionError: SectorIvFlowError | null;
  hasData: boolean;
  onRefresh: () => void;
}

export default function IvRefreshControl({
  refresh,
  submitting,
  actionError,
  hasData,
  onRefresh,
}: IvRefreshControlProps) {
  const retrySeconds = useRetryCountdown(refresh, refresh.retryAfterSeconds);
  const queued = refresh.status === 'queued';
  const running = refresh.status === 'running';
  const cooling = retrySeconds > 0;
  const busy = submitting || queued || running;
  const disabled = busy || cooling;

  let label = t('更新 IV');
  if (submitting) label = t('正在提交');
  else if (queued) label = t('等待更新');
  else if (running) label = t('正在更新');
  else if (cooling) label = t('{n} 秒后可更新', { n: retrySeconds });
  else if (refresh.status === 'failed' || refresh.status === 'cooldown') label = t('重新更新');

  let status: string | null = null;
  if (queued) status = t('更新已排队，完成后会自动显示');
  else if (running) status = t('正在获取新的 IV 数据');
  else if (refresh.status === 'failed') {
    status = hasData ? t('更新失败，仍显示上次结果') : t('更新失败，请重试');
  } else if (cooling) {
    status = t('更新较频繁，请等待 {n} 秒', { n: retrySeconds });
  } else if (actionError) {
    status = hasData ? t('未能开始更新，仍显示上次结果') : t('未能开始更新，请重试');
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-2 gap-y-1">
      {status && (
        <span
          className={cn(
            'max-w-full text-right text-micro leading-5',
            refresh.status === 'failed' || actionError ? 'text-down-600' : 'text-ink-400',
          )}
          role={refresh.status === 'failed' || actionError ? 'alert' : 'status'}
          aria-live="polite"
        >
          {status}
        </span>
      )}
      <button
        type="button"
        onClick={onRefresh}
        disabled={disabled}
        className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-md border border-line bg-card px-3 text-caption font-medium text-ink-600 shadow-btn transition-[border-color,color,opacity] hover:border-brand-400 hover:text-brand-600 disabled:cursor-wait disabled:opacity-60"
      >
        <BusyIcon busy={submitting || running} size={13} tone="brand" />
        {label}
      </button>
    </div>
  );
}
