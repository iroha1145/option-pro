/**
 * 信号卡列表（stock-detail.md T1）：stocks/{t}/signals + breakouts/tickers/{t}
 * 类型 chip + 触发价 Mono + 状态章 + 相对时间；空态「近期无信号 · 雷达仍在盯」
 */
import { usePolling } from '@/hooks/usePolling';
import { signalsApi } from '@/api/modules/signals';
import { breakoutsApi } from '@/api/modules/breakouts';
import SignalChip from '@/components/shared/SignalChip';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtPrice, fmtRelative } from '@/lib/format';

const RESULT_META = {
  hit: { text: '已达成', cls: 'bg-up-50 text-up-700' },
  failed: { text: '已失效', cls: 'bg-down-50 text-down-700' },
  pending: { text: '进行中', cls: 'bg-brand-50 text-brand-700' },
} as const;

export default function SignalList({ ticker }: { ticker: string }) {
  const { data: signals } = usePolling(() => signalsApi.stock(ticker), null, [ticker]);
  const { data: events } = usePolling(() => breakoutsApi.byTicker(ticker), null, [ticker]);

  const hasSignals = (signals?.length ?? 0) > 0;
  const hasEvents = (events?.length ?? 0) > 0;

  if (!hasSignals && !hasEvents) {
    return (
      <div className="flex flex-col items-center rounded-md border border-line bg-card-warm px-4 py-8 text-center">
        <Icon name="radar" size={28} className="text-ink-300" />
        <p className="mt-3 text-body-s font-medium text-ink-600">近期无信号 · 雷达仍在盯</p>
        <p className="mt-1 text-caption text-ink-400">突破 / 放量 / 回踩等触发后将在此出现</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {hasSignals && (
        <div className="flex flex-wrap items-center gap-1.5">
          {signals!.map((s, i) => (
            <span key={`${s.type}-${i}`} className="inline-flex items-center gap-1.5">
              <SignalChip type={s.type} label={s.label} />
              <span className="text-micro text-ink-400">{fmtRelative(s.at)}</span>
            </span>
          ))}
        </div>
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
              <p className="font-mono text-body-s text-ink-800 tnum">触发 {fmtPrice(e.price)}</p>
              <p className="text-micro text-ink-400">{fmtRelative(e.at)}</p>
            </div>
            <span className={cn('rounded-xs px-1.5 py-0.5 text-micro font-medium', meta.cls)}>{meta.text}</span>
          </div>
        );
      })}
    </div>
  );
}
