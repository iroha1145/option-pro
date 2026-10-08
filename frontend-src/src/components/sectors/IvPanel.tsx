import { useQuoteSymbols } from '@/hooks/useLiveQuote';
import { LivePrice } from '@/components/shared/LiveQuote';
import HorizontalScroller from '@/components/shared/HorizontalScroller';
/** 当前 ATM IV 在所选板块成分中的真实横截面排名。 */
import { useMemo, useState } from 'react';
import { fmtRelative } from '@/lib/format';
import type { SectorIvRefreshState } from '@/api/modules/sectors';
import TickerLogo from '@/components/shared/TickerLogo';
import EmptyState from '@/components/shared/EmptyState';
import InfoHint from '@/components/shared/InfoHint';
import SoftBadge from '@/components/shared/SoftBadge';
import StatusNotice from '@/components/shared/StatusNotice';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonRows } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { useRetryCountdown } from '@/hooks/useRetryCountdown';
import { t } from '../../i18n/core.ts';
import SectorChips from './SectorChips';
import IvRefreshControl from './IvRefreshControl';
import type { SectorIvFlowError } from './ivRefreshFlow';
import type { IvMetaVm, IvRowVm } from './model';
import { SOURCE_STATUS_CN } from './model';

/* ---------- source_status 徽标 ---------- */
function SourceStatusBadge({ status }: { status: keyof typeof SOURCE_STATUS_CN }) {
  return (
    <SoftBadge tone={status === 'insufficient_data' ? 'danger' : 'warn'}>
      <Icon name="flag" size={10} />
      {SOURCE_STATUS_CN[status]}
    </SoftBadge>
  );
}

/* ---------- IV rank 条：与右栏高 IV 列表同用中性墨色。IV 高低是类别信息，
   不借涨跌或状态的红绿。 ---------- */
function IvRankBar({ rank, replayKey }: { rank: number; replayKey: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="w-8 text-right text-body-s font-medium text-ink-800 tnum">{rank}</span>
      <span className="h-1 w-[100px] strength-track overflow-hidden rounded-pill bg-line" role="presentation">
        <span
          key={replayKey}
          className="block h-full origin-left animate-grow-bar rounded-pill bg-ink-500"
          style={{ width: `${Math.max(2, rank)}%` }}
        />
      </span>
    </span>
  );
}

/* ---------- 面板 ---------- */
interface IvPanelProps {
  sectors: { id: string; name: string }[];
  sectorId: string;
  onSectorChange: (id: string) => void;
  data: IvRowVm[];
  meta: IvMetaVm;
  loading: boolean;
  refreshing: boolean;
  error: SectorIvFlowError | null;
  refresh: SectorIvRefreshState;
  submitting: boolean;
  actionError: SectorIvFlowError | null;
  onRetry: () => void;
  onRefresh: () => void;
  onOpenTicker: (ticker: string) => void;
}

export default function IvPanel({
  sectors,
  sectorId,
  onSectorChange,
  data,
  meta,
  loading,
  refreshing,
  error,
  refresh,
  submitting,
  actionError,
  onRetry,
  onRefresh,
  onOpenTicker,
}: IvPanelProps) {
  const [desc, setDesc] = useState(false);
  const retrySeconds = useRetryCountdown(error, error?.retryAfter);

  const rows = useMemo(() => {
    const sorted = [...data].sort((a, b) => {
      const ra = a.rank ?? Infinity;
      const rb = b.rank ?? Infinity;
      return desc ? rb - ra : ra - rb;
    });
    /* null 恒排最后（降序时 Infinity 变最小会跑到最前，需修正） */
    if (desc) {
      const withRank = sorted.filter((r) => r.rank !== null);
      const noRank = sorted.filter((r) => r.rank === null);
      return [...withRank, ...noRank];
    }
    return sorted;
  }, [data, desc]);
  useQuoteSymbols(rows.map(row => row.ticker));
  return (
    <div className="card-surface p-4 md:p-6">
      {/* 头：标题 + 徽标 + 排序 */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2.5">
          <h2 className="text-h3 text-ink-800">{t('行业隐含波动率（IV）排名')}</h2>
          {meta.status !== 'active' && <SourceStatusBadge status={meta.status} />}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setDesc((v) => !v)}
            className="flex h-7 items-center gap-1 rounded-md border border-line bg-card px-2 text-caption text-ink-500 shadow-btn transition-colors duration-fast hover:border-line-strong hover:text-ink-800"
            aria-label={t('切换排序，当前行业排位{order}', { order: desc ? t('降序') : t('升序') })}
          >
            <Icon name={desc ? 'arrow-down' : 'arrow-up'} size={12} />
            {t('排位')}{desc ? t('降序') : t('升序')}
          </button>
        </div>
      </div>

      {/* 板块 pills（随 B1 联动，可手动改） */}
      <SectorChips sectors={sectors} value={sectorId} onChange={onSectorChange} className="mt-3" />

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-y border-line py-2.5">
        <span className="text-micro text-ink-400">
          {refreshing && refresh.status === 'idle'
            ? t('正在确认最新结果')
            : meta.asOf
              ? t('数据时间 {time}', { time: fmtRelative(meta.asOf) })
              : t('数据时间暂缺')}
        </span>
        <IvRefreshControl
          refresh={refresh}
          submitting={submitting}
          actionError={actionError}
          hasData={data.length > 0}
          onRefresh={onRefresh}
        />
      </div>

      {/* 数据未刷新横幅 */}
      {meta.stale && !loading && !error && (
        <StatusNotice className="mt-3">
          {t('数据暂未更新，以下为最近一次结果')}
        </StatusNotice>
      )}
      {error && data.length > 0 && !loading && (
        <StatusNotice
          className="mt-3"
          action={
            <button
              type="button"
              onClick={onRetry}
              className="min-h-9 rounded-md px-2 text-caption font-medium text-brand-600 hover:bg-brand-50"
            >
              {t('重试')}
            </button>
          }
        >
          {t('读取最新结果失败，仍显示上次数据')}
        </StatusNotice>
      )}

      {/* 表 / 骨架 / 空态。表最窄 420px：手机上横向滚动，HorizontalScroller 给出右侧渐隐，看得出还有列。 */}
      <HorizontalScroller className="mt-3" scrollerClassName="overscroll-x-contain" surface="card">
        {loading && rows.length === 0 ? (
          <SkeletonRows rows={6} />
        ) : error && rows.length === 0 ? (
          <EmptyState
            image="/empty-chart.svg"
            title={error.code === 503 ? t('IV 排名暂不可用') : t('IV 排名加载失败')}
            description={
              error.code === 503
                ? `${t('期权数据暂时获取不到')}${retrySeconds > 0 ? t(' · {n} 秒后可重试', { n: retrySeconds }) : ''}`
                : error.message
            }
            action={
              <button
                type="button"
                onClick={onRetry}
                disabled={retrySeconds > 0}
                className="btn-primary"
              >
                <Icon name="refresh" size={14} />
                {retrySeconds > 0 ? t('{n} 秒后重试', { n: retrySeconds }) : t('重试')}
              </button>
            }
          />
        ) : rows.length === 0 ? (
          <EmptyState
            image="/empty-chart.svg"
            title={t("该行业暂无隐含波动率排名数据")}
            description={
              refresh.status === 'queued' || refresh.status === 'running'
                ? t('正在准备该行业的 IV 数据，完成后会自动显示')
                : t('该行业成分暂无可用的期权样本，可切换行业或更新数据')
            }
          />
        ) : (
          <table className="min-w-[420px] w-full border-collapse" aria-label={t("行业隐含波动率排名表")}>
            <thead>
              <tr className="border-b border-line text-left text-eyebrow font-sans text-ink-400">
                <th className="py-2.5 pr-2 font-sans">{t('股票代码')}</th>
                <th className="px-2 py-2.5 text-right font-sans">{t('股价')}</th>
                <th className="px-2 py-2.5 font-sans">
                  {t('行业排位')}
                  <InfoHint hint={SCORE_HINTS.sectorIvRank} side="bottom" size={11} className="ml-1" />
                </th>
                <th className="px-2 py-2.5 text-right font-sans">
                  IV%
                  <InfoHint hint={SCORE_HINTS.sectorAtmIv} side="bottom" align="end" size={11} className="ml-1" />
                </th>
                <th className="w-10" aria-label={t("操作")} />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={`${sectorId}:${r.ticker}`}
                  onClick={() => onOpenTicker(r.ticker)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.target !== e.currentTarget) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onOpenTicker(r.ticker);
                    }
                  }}
                  className="group h-11 cursor-pointer border-b border-line transition-colors duration-fast last:border-0 hover:bg-paper-2 focus-visible:bg-paper-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600"
                >
                  <td className="py-2 pr-2">
                    <span className="flex items-center gap-2.5">
                      <TickerLogo ticker={r.ticker} size={28} />
                      <span>
                        <span className="block tnum text-body-s font-medium text-ink-800">{r.ticker}</span>
                        <span className="hidden max-w-[120px] truncate text-micro text-ink-400 sm:block">{r.name}</span>
                      </span>
                    </span>
                  </td>
                  <td
                    className="px-2 py-2 text-right text-data-m text-ink-800 tnum"
                  >
                    {/* 报价说明折到第二行时也靠右，与右对齐的「价」表头对齐 */}
                    <LivePrice symbol={r.ticker} fallback={r.price} className="justify-end" />
                  </td>
                  <td className="px-2 py-2">
                    {r.rank !== null ? (
                      <IvRankBar rank={r.rank} replayKey={`${sectorId}:${r.ticker}`} />
                    ) : (
                      <span className="tnum text-ink-400">—</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right text-data-m text-ink-600 tnum">
                    {r.atmIv !== null ? `${r.atmIv.toFixed(1)}%` : <span className="text-ink-400">—</span>}
                  </td>
                  <td className="py-2 pl-2">
                    <span className="inline-flex size-7 items-center justify-center rounded-xs border border-line bg-card text-ink-400 opacity-0 transition-opacity duration-fast group-hover:opacity-100">
                      <Icon name="arrow-up-right" size={13} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </HorizontalScroller>
    </div>
  );
}
