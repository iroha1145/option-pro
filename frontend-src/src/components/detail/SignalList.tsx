/**
 * 信号卡列表（stock-detail.md T1）：stocks/{t}/signals + breakouts/tickers/{t}
 * 类型 chip + 触发价 Mono + 状态章 + 相对时间；空态「近期无信号 · 雷达仍在盯」。
 * loading / error / empty 三态分开（审计 2.2.5/2.2.6）：首帧不闪空态文案，
 * 请求失败明说「读不到」并给重试，不冒充「确实没有信号」。
 */
import { usePolling } from '@/hooks/usePolling';
import { signalsApi } from '@/api/modules/signals';
import { breakoutsApi } from '@/api/modules/breakouts';
import SignalChip from '@/components/shared/SignalChip';
import SignalLines from '@/components/shared/SignalLines';
import { SkeletonText } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtPrice, fmtRelative } from '@/lib/format';
import ManualStockPull from './ManualStockPull';
import { t } from '../../i18n/core.ts';

const RESULT_META = {
  hit: { text: t('已达成'), cls: 'bg-up-50 text-up-700' },
  failed: { text: t('已失效'), cls: 'bg-down-50 text-down-700' },
  pending: { text: t('进行中'), cls: 'bg-brand-50 text-brand-700' },
} as const;

export default function SignalList({
  ticker,
  refreshVersion = 0,
  onPulled,
}: {
  ticker: string;
  refreshVersion?: number;
  /** 快照缺失 CTA 拉取成功后的整页刷新回调（StockDetail.handlePulled） */
  onPulled?: () => void;
}) {
  const signalsQ = usePolling(
    () => signalsApi.stock(ticker),
    null,
    [ticker, refreshVersion],
  );
  const eventsQ = usePolling(() => breakoutsApi.byTicker(ticker), null, [ticker]);
  const signals = signalsQ.data;
  const events = eventsQ.data;

  const hasSignals = (signals?.length ?? 0) > 0;
  const hasEvents = (events?.length ?? 0) > 0;

  if (!hasSignals && !hasEvents) {
    if (signalsQ.loading || eventsQ.loading) {
      return <SkeletonText lines={4} className="py-2" />;
    }
    /* 快照缺失 ≠ 读取失败：没拉过数据的股票给「拉取并分析」，不误导重试 */
    if (signalsQ.error?.bizCode === 'public_snapshot_unavailable' && !signals) {
      return (
        <div className="flex flex-col items-center rounded-md border border-line bg-card-warm px-4 py-8 text-center">
          <Icon name="radar" size={26} className="text-ink-300" />
          <p className="mt-3 text-body-s font-medium text-ink-600">{t('该股尚未拉取数据，拉取后自动分析')}</p>
          <ManualStockPull ticker={ticker} minimal className="mt-3" onPulled={() => onPulled?.()} />
        </div>
      );
    }
    const failed = (signalsQ.error && !signals) || (eventsQ.error && !events);
    if (failed) {
      return (
        <div className="flex flex-col items-center rounded-md border border-line bg-card-warm px-4 py-8 text-center">
          <Icon name="doc-quote" size={26} className="text-ink-300" />
          <p className="mt-3 text-body-s font-medium text-ink-600">{t('信号数据读取失败')}</p>
          <p className="mt-1 text-caption text-ink-400">{t('是读取失败，不代表该股没有信号')}</p>
          <button
            onClick={() => {
              signalsQ.refresh();
              eventsQ.refresh();
            }}
            className="mt-3 flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-caption text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="refresh" size={13} />
            {t('重试')}
          </button>
        </div>
      );
    }
    return (
      <div className="flex flex-col items-center rounded-md border border-line bg-card-warm px-4 py-8 text-center">
        <Icon name="radar" size={28} className="text-ink-300" />
        <p className="mt-3 text-body-s font-medium text-ink-600">{t('近期无信号 · 雷达仍在盯')}</p>
        <p className="mt-1 text-caption text-ink-400">{t('突破 / 放量 / 回踩等触发后将在此出现')}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {hasSignals && (
        /* 文字行排版（对齐日股展开区）：原卡片 chip 一行一句读起来太密 */
        <SignalLines signals={signals!} />
      )}
      {events?.slice(0, 4).map((e) => {
        const meta = RESULT_META[e.result];
        return (
          <div
            key={e.id}
            className="flex items-center gap-3 rounded-md border border-line bg-card px-3 py-2.5 transition-colors duration-fast hover:bg-paper-2"
          >
            <SignalChip type={e.type} label={e.label} />
            <div className="min-w-0 flex-1">
              <p className="font-mono text-body-s text-ink-800 tnum">{t('触发')} {fmtPrice(e.price)}</p>
              <p className="text-micro text-ink-400">{fmtRelative(e.at)}</p>
            </div>
            <span className={cn('rounded-xs px-1.5 py-0.5 text-micro font-medium', meta.cls)}>{meta.text}</span>
          </div>
        );
      })}
    </div>
  );
}
