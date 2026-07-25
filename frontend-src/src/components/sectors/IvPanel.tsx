/** 当前 ATM IV 在所选板块成分中的真实横截面排名。 */
import { useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import { fmtPrice, fmtRelative } from '@/lib/format';
import type { ApiError } from '@/api/client';
import TickerLogo from '@/components/shared/TickerLogo';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonRows } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { useRetryCountdown } from '@/hooks/useRetryCountdown';
import SectorChips from './SectorChips';
import type { IvMetaVm, IvRowVm } from './model';
import { SOURCE_STATUS_CN, ivRankColor } from './model';

/* ---------- source_status 徽标 ---------- */
function SourceStatusBadge({ status }: { status: keyof typeof SOURCE_STATUS_CN }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-xs border px-1.5 py-0.5 text-micro',
        status === 'insufficient_data'
          ? 'border-down-600/25 bg-down-50 text-down-700'
          : 'border-warn-600/25 bg-warn-50 text-warn-600',
      )}
    >
      <Icon name="flag" size={10} />
      {SOURCE_STATUS_CN[status]}
    </span>
  );
}

/* ---------- IV rank 色阶条（低 up-600 → 中 brand-100 → 高 down-600） ---------- */
function IvRankBar({ rank, replayKey }: { rank: number; replayKey: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="w-8 text-right font-mono text-body-s font-semibold text-ink-800 tnum">{rank}</span>
      <span className="h-[3px] w-[100px] overflow-hidden rounded-pill bg-line" role="presentation">
        <span
          key={replayKey}
          className="block h-full origin-left animate-grow-bar rounded-pill"
          style={{ width: `${Math.max(2, rank)}%`, backgroundColor: ivRankColor(rank) }}
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
  error: ApiError | null;
  onRetry: () => void;
  onOpenTicker: (ticker: string) => void;
}

export default function IvPanel({ sectors, sectorId, onSectorChange, data, meta, loading, error, onRetry, onOpenTicker }: IvPanelProps) {
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
  return (
    <div className="card-surface p-4 md:p-6">
      {/* 头：标题 + 徽标 + 排序 */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex items-center gap-2.5">
          <h2 className="text-h3 text-ink-800">板块 IV 横截面排名</h2>
          {meta.status !== 'active' && <SourceStatusBadge status={meta.status} />}
        </div>
        <div className="flex items-center gap-3">
          {meta.asOf && (
            <span className="hidden font-mono text-micro text-ink-400 tnum sm:inline">更新于 {fmtRelative(meta.asOf)}</span>
          )}
          <button
            type="button"
            onClick={() => setDesc((v) => !v)}
            className="flex h-7 items-center gap-1 rounded-md border border-line bg-card px-2 text-caption text-ink-500 transition-colors duration-fast hover:border-line-strong hover:text-ink-800"
            aria-label={`切换排序，当前板块排位${desc ? '降序' : '升序'}`}
          >
            <Icon name={desc ? 'arrow-down' : 'arrow-up'} size={12} />
            排位{desc ? '降序' : '升序'}
          </button>
        </div>
      </div>
      <p className="mt-1 text-caption text-ink-400">
        {desc ? '当前 ATM IV 较高的成分在前' : '当前 ATM IV 较低的成分在前'}
      </p>

      {/* 板块 pills（随 B1 联动，可手动改） */}
      <SectorChips sectors={sectors} value={sectorId} onChange={onSectorChange} className="mt-3" />

      {/* 数据未刷新横幅 */}
      {meta.stale && !loading && !error && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-warn-600/25 bg-warn-50 px-3 py-2 text-caption text-warn-600" role="status">
          <Icon name="clock-ny" size={14} />
          数据暂未刷新，以下为最近一次结果
        </div>
      )}

      {/* 表 / 骨架 / 空态 */}
      <div className="mt-3 overflow-x-auto overscroll-x-contain">
        {loading ? (
          <SkeletonRows rows={6} />
        ) : error ? (
          <EmptyState
            image="/empty-chart.svg"
            title={error.code === 503 ? 'IV 排名暂不可用' : 'IV 排名加载失败'}
            description={
              error.code === 503
                ? `期权数据暂时获取不到${retrySeconds > 0 ? ` · ${retrySeconds} 秒后可重试` : ''}`
                : error.message
            }
            action={
              <button
                type="button"
                onClick={onRetry}
                disabled={retrySeconds > 0}
                className="flex min-h-11 items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter,opacity] hover:brightness-105 disabled:cursor-wait disabled:opacity-60"
              >
                <Icon name="refresh" size={14} />
                {retrySeconds > 0 ? `${retrySeconds} 秒后重试` : '重试'}
              </button>
            }
          />
        ) : rows.length === 0 ? (
          <EmptyState
            image="/empty-chart.svg"
            title="该板块暂无 IV 排名数据"
            description="该板块成分暂无可用的期权样本，可切换板块或重新加载"
            action={
              <button
                type="button"
                onClick={onRetry}
                className="flex min-h-11 items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter,opacity] hover:brightness-105"
              >
                <Icon name="refresh" size={14} />
                重新加载
              </button>
            }
          />
        ) : (
          <table className="min-w-[420px] w-full border-collapse" aria-label="板块 IV 横截面排名表">
            <thead>
              <tr className="border-b border-line text-left text-eyebrow font-sans uppercase tracking-[0.14em] text-ink-400">
                <th className="py-2.5 pr-2 font-sans">代码</th>
                <th className="px-2 py-2.5 text-right font-sans">价</th>
                <th className="px-2 py-2.5 font-sans">
                  板块排位
                  <InfoHint hint={SCORE_HINTS.sectorIvRank} side="bottom" size={11} className="ml-1" />
                </th>
                <th className="px-2 py-2.5 text-right font-sans">
                  IV%
                  <InfoHint hint={SCORE_HINTS.sectorAtmIv} side="bottom" align="end" size={11} className="ml-1" />
                </th>
                <th className="w-10" aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={`${sectorId}:${r.ticker}`}
                  onClick={() => onOpenTicker(r.ticker)}
                  className="group h-11 cursor-pointer border-b border-line transition-colors duration-fast last:border-0 hover:bg-paper-2"
                >
                  <td className="py-2 pr-2">
                    <span className="flex items-center gap-2.5">
                      <TickerLogo ticker={r.ticker} size={28} />
                      <span>
                        <span className="block font-mono text-body-s font-semibold text-ink-800">{r.ticker}</span>
                        <span className="hidden max-w-[120px] truncate text-micro text-ink-400 sm:block">{r.name}</span>
                      </span>
                    </span>
                  </td>
                  <td
                    className="px-2 py-2 text-right font-mono text-data-m text-ink-800 tnum"
                  >
                    {r.price !== null ? fmtPrice(r.price) : <span className="text-ink-300">—</span>}
                  </td>
                  <td className="px-2 py-2">
                    {r.rank !== null ? (
                      <IvRankBar rank={r.rank} replayKey={`${sectorId}:${r.ticker}`} />
                    ) : (
                      <span className="font-mono text-ink-300">—</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-data-m text-ink-600 tnum">
                    {r.atmIv !== null ? `${r.atmIv.toFixed(1)}%` : <span className="text-ink-300">—</span>}
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
      </div>

      <SourceNote
        className="mt-4"
        text="板块排位是同板块成分之间的横向比较，不是该股自己的历史高低位；期权与价格均为延迟数据"
      />
    </div>
  );
}
