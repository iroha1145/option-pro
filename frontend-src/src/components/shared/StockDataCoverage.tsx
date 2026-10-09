import type { useStockDataStatus } from '@/hooks/useStockDataStatus';
import { t } from '@/i18n/core';
import { cn } from '@/lib/utils';

/**
 * reserve：股票清单还在读取时先按同样的行高占位，清单到达后这一条不会从无到有地插进页面。
 * 计数格按「99/99」留宽、状态说明在手机上单独一行，数字变化和说明长短都不改变换行。
 */
export default function StockDataCoverage({ state, className, reserve = false }: { state: ReturnType<typeof useStockDataStatus>; className?: string; reserve?: boolean }) {
  const { summary: s, error, loading, data } = state;
  if (!s.total && !reserve) return null;
  const pending = !s.total;
  return (
    <div className={cn('data-coverage-strip flex flex-wrap items-center gap-x-4 gap-y-2 text-micro text-ink-500', className)} role="status" aria-live="polite" data-testid="stock-data-coverage">
      <span className="text-ink-600">{t('数据覆盖')}</span>
      {([
        [t('行情'), s.overview], [t('日线'), s.dailyChart], [t('技术信号'), s.signals],
      ] as const).map(([label, count]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          {label}<span className="min-w-[calc(5ch+0.75rem)] rounded bg-paper px-1.5 py-0.5 text-center text-ink-700 tnum">{pending ? '—' : `${data === null ? '—' : count}/${s.total}`}</span>
        </span>
      ))}
      <span className={cn('flex basis-full flex-wrap gap-x-3 gap-y-1 sm:basis-auto', error || s.failed ? 'text-warn-700' : 'text-ink-400')}>
        {error ? <span>{t('状态读取失败，稍后自动重试')}</span> : pending || (loading && data === null) ? <span>{t('正在检查数据')}</span> : <>
          {s.ready === s.total && <span>{t('数据已就绪')}</span>}
          {s.preparing > 0 && <span>{t('正在获取 {n}', { n: s.preparing })}</span>}
          {s.partial > 0 && <span>{t('部分缺失 {n}', { n: s.partial })}</span>}
          {s.stale > 0 && <span>{t('待更新 {n}', { n: s.stale })}</span>}
          {s.failed > 0 && <span>{t('获取失败 {n}', { n: s.failed })}</span>}
          {s.unknown > 0 && <span>{t('状态未知 {n}', { n: s.unknown })}</span>}
        </>}
      </span>
    </div>
  );
}
