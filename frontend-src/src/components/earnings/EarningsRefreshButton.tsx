import { useNow } from '@/hooks/useNow';
import { fmtTimeHHMMSS } from '@/lib/format';
import { BusyIcon } from '@/components/shared/IconSwap';
import { t } from '../../i18n/core.ts';
import TextSwap from '@/components/shared/TextSwap';

type RefreshStatus = 'refreshed' | 'cooldown' | 'failed_stale' | 'queued' | null;

interface EarningsRefreshButtonProps {
  cooldownUntil: number;
  refreshing: boolean;
  refreshStatus: RefreshStatus;
  lastUpdatedAt?: number | null;
  onRefresh: () => void;
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
        <span className="tnum text-micro text-warn-700">{t('刷新失败 · 显示已有数据')}</span>
      )}
      {refreshStatus === 'refreshed' && cooldownRemain <= 0 && lastUpdatedAt && (
        <span className="text-micro text-ink-400 tnum">{t('已更新')} {fmtTimeHHMMSS(lastUpdatedAt)}</span>
      )}
      <button
        onClick={() => onRefresh()}
        disabled={refreshing || cooldownRemain > 0}
        title={cooldownRemain > 0 ? t('冷却中，{n}s 后可刷新', { n: cooldownRemain }) : t('手动刷新财报日历')}
        className="control-button touch-target"
      >
        <BusyIcon busy={refreshing} size={15} tone="brand" />
        <TextSwap swapKey={refreshing ? 'busy' : cooldownRemain > 0 ? 'cooldown' : 'idle'}>
          {refreshing ? t('刷新中') : cooldownRemain > 0 ? <span className="tnum">{cooldownRemain}s</span> : t('刷新日历')}
        </TextSwap>
      </button>
    </span>
  );
}
