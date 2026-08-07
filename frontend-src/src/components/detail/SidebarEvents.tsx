/**
 * 相关突破事件（个股整页右栏，≤3 条；原 StockDrawerBody 侧栏卡）
 * loading / error / empty 三态分开；空态不写时间窗——后端按 last_seen_at
 * 返回全部历史事件的最近 50 条。
 */
import { breakoutsApi } from '@/api/modules/breakouts';
import { usePolling } from '@/hooks/usePolling';
import { fmtPrice, fmtRelative } from '@/lib/format';
import SignalChip from '@/components/shared/SignalChip';
import Icon from '@/components/icons';
import { t as __t } from '../../i18n/core.ts';

export default function SidebarEvents({ ticker }: { ticker: string }) {
  const { data, loading, error, refresh } = usePolling(() => breakoutsApi.byTicker(ticker), null, [ticker]);
  const items = (data ?? []).slice(0, 3);
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">BREAKOUT EVENTS</p>
      <h3 className="mt-1.5 text-h3 text-ink-900">{__t('相关突破事件')}</h3>
      {loading && !data ? (
        <div className="mt-3 space-y-2" aria-hidden="true">
          <span className="skeleton-shimmer block h-4 w-full rounded-xs" />
          <span className="skeleton-shimmer block h-4 w-2/3 rounded-xs" />
        </div>
      ) : error && !data ? (
        <p className="mt-3 flex items-center gap-2 text-body-s text-ink-400">
          <Icon name="doc-quote" size={16} className="text-ink-300" />
          {__t('突破事件读取失败')}
          <button
            onClick={() => refresh()}
            className="ml-auto rounded-md border border-line px-2 py-0.5 text-micro text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
          >
            {__t('重试')}
          </button>
        </p>
      ) : items.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-body-s text-ink-400">
          <Icon name="radar" size={16} className="text-ink-300" />
          {__t('暂无突破事件记录 · 雷达仍在盯')}
        </p>
      ) : (
        <ul className="mt-3 space-y-2.5">
          {items.map((e) => (
            <li key={e.id} className="flex items-center gap-2.5">
              <SignalChip type={e.type} label={e.label} />
              {/* 盘前跳空等事件可无成交价（event_price=null）——显「—」，不崩页 */}
              <span className="font-mono text-caption text-ink-800 tnum">
                {typeof e.price === 'number' && Number.isFinite(e.price) ? fmtPrice(e.price) : '—'}
              </span>
              <span className="ml-auto text-micro text-ink-400">{fmtRelative(e.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
