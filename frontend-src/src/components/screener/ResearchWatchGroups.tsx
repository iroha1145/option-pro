import { formatWatchScore, type ResearchWatchRow, type ResearchWatchView } from '@/lib/researchWatchGroups';
import { t as __t } from '../../i18n/core.ts';

function profileLabel(profile: string): string {
  if (profile === 'balanced') return __t('均衡');
  if (profile === 'conservative') return __t('稳健');
  if (profile === 'aggressive') return __t('进取');
  return profile;
}

function horizonLabel(horizon: string): string {
  if (horizon === 'short') return __t('短期');
  if (horizon === 'mid') return __t('中期');
  if (horizon === 'long') return __t('长期');
  return horizon;
}

function WatchList({ rows, total, marker }: { rows: ResearchWatchRow[]; total: number; marker: string }) {
  return (
    <>
      <p className="mt-2 text-caption text-ink-400" data-watch-count={marker}>
        {__t('显示 {shown} / 共 {total}', { shown: rows.length, total })}
      </p>
      {rows.length === 0 ? (
        <p className="mt-2 text-caption text-ink-400">{__t('当前没有名称')}</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {rows.map((row) => (
            <li key={`${row.sourceDate}:${row.securityId}`} className="flex flex-wrap items-baseline gap-x-3 text-caption">
              <span className="font-mono">{row.securityId}</span>
              <span className="font-mono tnum">{formatWatchScore(row.score)}</span>
              <span className="text-ink-400">{__t('来源日期')} {row.sourceDate}</span>
              {row.rejectionReasons.length > 0 && <span className="text-ink-400">{row.rejectionReasons.join(', ')}</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** Independent of the main-board table. High volatility stays collapsed unless the payload says otherwise. */
export function ResearchWatchGroups({ view }: { view: ResearchWatchView }) {
  return (
    <div
      className="mt-4 space-y-3"
      data-research-watch="on"
      data-watch-profile={view.profile}
      data-watch-horizon={view.horizon}
      data-watch-filter-scope={view.filterScope}
    >
      <p className="text-caption text-ink-400">
        {__t('观察来源 {profile} · {horizon} · {date}', {
          profile: profileLabel(view.profile),
          horizon: horizonLabel(view.horizon),
          date: view.sourceDate,
        })}
      </p>
      {view.filterScopeNote ? <p className="text-caption text-ink-400">{__t(view.filterScopeNote)}</p> : null}
      <section className="card-surface p-4" aria-label={__t('偏离待观察')}>
        <h2 className="text-body font-medium">{__t('偏离待观察')}</h2>
        {view.note ? <p className="mt-1 text-caption text-ink-400">{__t(view.note)}</p> : null}
        <WatchList rows={view.extension} total={view.extensionTotal} marker="extension" />
      </section>
      <details className="card-surface p-4" data-high-vol-collapsed={view.highVolatilityCollapsed ? 'true' : 'false'} {...(view.highVolatilityCollapsed ? {} : { open: true })}>
        <summary className="cursor-pointer text-body font-medium">{__t('高波动观察')}</summary>
        <WatchList rows={view.highVolatility} total={view.highVolatilityTotal} marker="high-volatility" />
      </details>
    </div>
  );
}
