import { useNow } from '@/hooks/useNow';
import { cn } from '@/lib/utils';
import { fmtTimeHHMMSS } from '@/lib/format';
import Icon from '@/components/icons';
import { t } from '../../i18n/core.ts';

type RefreshStatus = 'refreshed' | 'cooldown' | 'failed_stale' | 'queued' | null;

interface EarningsRefreshButtonProps {
  cooldownUntil: number;
  refreshing: boolean;
  refreshStatus: RefreshStatus;
  lastUpdatedAt?: number | null;
  onRefresh: (cooldownRemain: number) => void;
}

/** 秒级冷却只在本按钮内走字，不牵动整页。 */
export default function EarningsRefreshButton({
  cooldownUntil,
  refreshing,
  refreshStatus,
  lastUpdatedAt,
  onRefresh,
}: EarningsRefreshButtonProps) {
  const now = useNow(cooldownUntil > 0 ? 1000 : 0);
  const cooldownRemain = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));

  return (
    <span className="flex items-center gap-2.5">
      {refreshStatus === 'failed_stale' && (
        <span className="font-mono text-micro text-warn-600">{t('刷新失败 · 显示已有数据')}</span>
      )}
      {refreshStatus === 'refreshed' && cooldownRemain <= 0 && lastUpdatedAt && (
        <span className="font-mono text-micro text-ink-400 tnum">{t('已更新')} {fmtTimeHHMMSS(lastUpdatedAt)}</span>
      )}
      <button
        onClick={() => onRefresh(cooldownRemain)}
        disabled={refreshing || cooldownRemain > 0}
        title={cooldownRemain > 0 ? t('冷却中，{n}s 后可刷新', { n: cooldownRemain }) : t('手动刷新财报日历')}
        className={cn(
          'flex h-9 items-center gap-2 rounded-md border px-3 text-caption shadow-btn transition-colors duration-fast',
          refreshing || cooldownRemain > 0
            ? 'cursor-not-allowed border-line bg-card-warm text-ink-300'
            : 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600',
        )}
      >
        <Icon name="refresh" size={15} className={refreshing ? 'animate-spin-once' : ''} />
        {refreshing ? t('刷新中') : cooldownRemain > 0 ? <span className="font-mono tnum">{cooldownRemain}s</span> : t('刷新日历')}
      </button>
    </span>
  );
}
