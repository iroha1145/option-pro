import { useMemo } from 'react';
import type { OptionChain } from '@/api/types';
import { fmtCompact } from '@/lib/format';
import { t } from '../../../i18n/core.ts';
import { contractsForChain } from './chainMetrics.ts';

/** Known-data subtotals: even one missing contract makes a side incomplete. */
export default function SummaryTiles({ chain }: { chain: OptionChain }) {
  const contracts = useMemo(() => contractsForChain(chain), [chain]);
  const sides = (['call', 'put'] as const).map((side) => {
    const legs = contracts.filter((c) => c.side === side);
    const known = legs.filter((c) => c.volume !== null);
    return { side, total: known.length ? known.reduce((sum, c) => sum + c.volume!, 0) : null,
      known: known.length, count: legs.length, partial: known.length !== legs.length };
  });
  const alerts = contracts.filter((c) => c.activity.length > 0).length;
  return <dl className="mt-4 grid grid-cols-2 overflow-hidden rounded-lg border border-line md:grid-cols-3">
    {sides.map(({ side, total, known, count, partial }) => <div key={side} className="px-4 py-4 first:border-r first:border-line">
      <dt className="text-caption text-ink-500">{side === 'call' ? t('看涨期权成交') : t('看跌期权成交')}</dt>
      <dd className="mt-1.5 font-mono text-data-l font-medium text-ink-900 tnum">{total === null ? '—' : fmtCompact(total)}<span className="ml-1.5 font-sans text-caption font-normal text-ink-500">{t('张')}</span></dd>
      <p className="mt-1 text-micro text-ink-500">{partial ? t('已知 {known}/{total} 份合约 · 数据不完整', { known, total: count }) : t('来自 {n} 份已知合约', { n: count })}</p>
    </div>)}
    <div className="col-span-2 border-t border-line bg-paper-2/50 px-4 py-4 md:col-span-1 md:border-l md:border-t-0">
      <dt className="text-caption text-ink-500">{t('需关注合约')}</dt>
      <dd className="mt-1.5 font-mono text-data-l font-medium text-ink-900 tnum">{alerts}<span className="ml-1.5 font-sans text-caption font-normal text-ink-500">{t('份')}</span></dd>
      <p className="mt-1 text-micro text-ink-500">{t('仅当前到期日 · 非买卖建议')}</p>
    </div>
  </dl>;
}
