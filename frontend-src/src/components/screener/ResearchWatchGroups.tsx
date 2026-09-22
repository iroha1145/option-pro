import type { ResearchWatchRow, ResearchWatchView } from '@/lib/researchWatchGroups';
import { t as __t } from '../../i18n/core.ts';

function WatchList({ rows }: { rows: ResearchWatchRow[] }) {
  if (rows.length === 0) {
    return <p className="mt-2 text-caption text-ink-400">{__t('当前没有名称')}</p>;
  }
  return (
    <ul className="mt-2 space-y-1">
      {rows.map((row) => (
        <li key={`${row.sourceDate}:${row.securityId}`} className="flex flex-wrap items-baseline gap-x-3 text-caption">
          <span className="font-mono">{row.securityId}</span>
          <span className="font-mono tnum">{row.score === null ? '—' : row.score}</span>
          <span className="text-ink-400">{__t('来源日期')} {row.sourceDate}</span>
          {row.rejectionReasons.length > 0 && <span className="text-ink-400">{row.rejectionReasons.join(', ')}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Independent of the main-board table. High volatility stays collapsed unless the payload says otherwise. */
export function ResearchWatchGroups({ view }: { view: ResearchWatchView }) {
  return (
    <div className="mt-4 space-y-3" data-research-watch="on">
      <section className="card-surface p-4" aria-label={__t('偏离待观察')}>
        <h2 className="text-body font-medium">{__t('偏离待观察')}</h2>
        {view.note ? <p className="mt-1 text-caption text-ink-400">{__t(view.note)}</p> : null}
        <WatchList rows={view.extension} />
      </section>
      <details className="card-surface p-4" {...(view.highVolatilityCollapsed ? {} : { open: true })}>
        <summary className="cursor-pointer text-body font-medium">{__t('高波动观察')}</summary>
        <WatchList rows={view.highVolatility} />
      </details>
    </div>
  );
}
