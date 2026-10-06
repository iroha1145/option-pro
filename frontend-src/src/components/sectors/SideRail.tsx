import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { EASE_PAPER, GROW_X } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import { SkeletonBlock, SkeletonCard } from '@/components/shared/Skeleton';
import type { IvMetaVm, IvRowVm, SectorVm } from './model';
import { SOURCE_STATUS_CN } from './model';
import { t } from '../../i18n/core.ts';

function IvHeatCard({
  rows,
  loading,
  error = false,
  onRetry,
  onOpenTicker,
  onOpenPalette,
}: {
  rows: IvRowVm[];
  loading: boolean;
  /** /sectors/{id}/iv-ranking 请求失败且无任何数据（审计 2.2.8） */
  error?: boolean;
  onRetry?: () => void;
  onOpenTicker: (ticker: string) => void;
  onOpenPalette: () => void;
}) {
  const top = useMemo(
    () =>
      rows
        .filter((row): row is IvRowVm & { rank: number } => row.rank !== null)
        .sort((left, right) => right.rank - left.rank)
        .slice(0, 8),
    [rows],
  );
  /* 排名列表而不是 3×3 同款砖：名次、代码、板块内排位一行读完，排位用中性墨色
     条表达高低（IV 高低是类别信息，不借涨跌红绿）。 */
  return (
    <div className="card-surface p-5">
      <div className="flex items-center justify-between">
        <p className="eyebrow">{t('当前隐含波动率较高的股票')}</p>
        <Icon name="flame-line" size={16} className="text-ink-400" />
      </div>
      {loading ? (
        <div className="mt-3 space-y-1.5" aria-hidden="true">
          {Array.from({ length: 8 }, (_, index) => (
            <SkeletonBlock key={index} className="h-8 rounded-md" />
          ))}
        </div>
      ) : top.length > 0 ? (
        <motion.ol
          className="mt-3"
          initial="hidden"
          whileInView="shown"
          viewport={{ once: true, amount: 0.3 }}
        >
          {top.map((row, index) => (
            <li key={row.ticker}>
              {/* 320 宽时强度条按上限 4.5rem 占满，名称只剩一个字：360 以下条宽收到下限 2.5rem，名称多出约 32px。 */}
              <button
                type="button"
                onClick={() => onOpenTicker(row.ticker)}
                aria-label={t('{ticker} 板块 IV 排位 {rank}，打开详情', { ticker: row.ticker, rank: row.rank })}
                className="grid min-h-9 w-full grid-cols-[1.25rem_3.5rem_minmax(0,1fr)_minmax(2.5rem,4.5rem)_2rem] items-center max-[359px]:grid-cols-[1.25rem_3.5rem_minmax(0,1fr)_2.5rem_2rem] sm:grid-cols-[1.25rem_3.75rem_minmax(0,1fr)_minmax(3rem,7rem)_2rem] gap-x-2.5 rounded-md px-1.5 text-left transition-colors duration-fast hover:bg-paper-2 [@media(pointer:coarse)]:min-h-11"
              >
                <span className="text-right text-caption text-ink-400 tnum">{index + 1}</span>
                <span className="tnum text-caption font-medium text-ink-800">{row.ticker}</span>
                <span className="truncate text-micro text-ink-500" title={row.name}>
                  {row.name !== row.ticker ? row.name : ''}
                </span>
                <span className="block h-1 overflow-hidden rounded-pill bg-line" aria-hidden="true">
                  <motion.span
                    className="block h-full origin-left rounded-pill bg-ink-500"
                    style={{ width: `${Math.max(2, Math.min(100, row.rank))}%` }}
                    variants={GROW_X}
                    transition={{ duration: 0.7, ease: EASE_PAPER, delay: index * 0.04 }}
                  />
                </span>
                <span className="text-right text-caption font-medium text-ink-800 tnum">
                  {row.rank}
                </span>
              </button>
            </li>
          ))}
        </motion.ol>
      ) : error ? (
        <p className="mt-3 flex items-center gap-2 text-micro text-ink-500">
          {t('隐含波动率数据加载失败，请重试。')}
          {onRetry && (
            <button
              onClick={onRetry}
              className="control-button"
            >
              {t('重试')}
            </button>
          )}
        </p>
      ) : (
        <p className="mt-3 text-micro text-ink-500">{t('当前没有可用的 IV 样本。')}</p>
      )}
      <button
        type="button"
        onClick={onOpenPalette}
        className="control-button mt-3"
      >
        <Icon name="plus" size={14} />
        {t('搜索更多代码并加入自选')}
      </button>
    </div>
  );
}

function CoverageCard({
  sector,
  rows,
  meta,
  loading = false,
  error = false,
}: {
  sector: SectorVm | null;
  rows: IvRowVm[];
  meta: IvMetaVm;
  /** 请求在飞：出骨架。原实现无 loading 通道，切板块的数秒内显示
   *  黄色「数据不足 · 成功样本 0」，随后跳成真值。 */
  loading?: boolean;
  /** 请求失败且无数据：覆盖数显示「—」而不是伪造的「0 / —」（审计 2.2.8） */
  error?: boolean;
}) {
  const ranked = rows
    .filter((row): row is IvRowVm & { rank: number } => row.rank !== null)
    .sort((left, right) => right.rank - left.rank);
  const highest = ranked[0] ?? null;
  const lowest = ranked[ranked.length - 1] ?? null;
  const statusLabel = error
    ? t('读取失败')
    : meta.status === 'active'
      ? t('数据正常')
      : SOURCE_STATUS_CN[meta.status];

  if (loading && !error) {
    return (
      <div className="card-surface p-5">
        <p className="eyebrow">{t('IV 数据覆盖')}</p>
        <h3 className="mt-1 text-h3 text-ink-800">{sector?.name ?? t('所选板块')}</h3>
        <div className="mt-4 space-y-2.5" aria-hidden="true">
          <SkeletonBlock className="h-4 w-full" />
          <SkeletonBlock className="h-4 w-3/4" />
          <SkeletonBlock className="h-4 w-5/6" />
        </div>
      </div>
    );
  }

  return (
    <div className="card-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">{t('IV 数据覆盖')}</p>
          <h3 className="mt-1 text-h3 text-ink-800">{sector?.name ?? t('所选板块')}</h3>
        </div>
        <span
          className={cn(
            'rounded-xs border px-1.5 py-0.5 text-micro',
            meta.status === 'active'
              ? 'border-ok-600/20 bg-ok-50 text-ok-700'
              : 'border-warn-600/25 bg-warn-50 text-warn-700',
          )}
        >
          {statusLabel}
        </span>
      </div>

      <dl className="mt-4 divide-y divide-line">
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-caption text-ink-500">{t('成功样本')}</dt>
          <dd className="text-data-m text-ink-800 tnum">
            {error && meta.successCount === null
              ? '— / —'
              : `${meta.successCount ?? rows.length} / ${meta.requestedCount ?? '—'}`}
          </dd>
        </div>
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-caption text-ink-500">{t('板块内最高')}</dt>
          <dd className="text-data-m text-ink-800 tnum">
            {highest ? <><span className="tnum">{highest.ticker}</span> · {highest.rank.toFixed(1)}</> : '—'}
          </dd>
        </div>
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-caption text-ink-500">{t('板块内最低')}</dt>
          <dd className="text-data-m text-ink-800 tnum">
            {lowest ? <><span className="tnum">{lowest.ticker}</span> · {lowest.rank.toFixed(1)}</> : '—'}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-micro leading-5 text-ink-400">
        {meta.asOf ? t('数据时间 {time}。', { time: fmtRelative(meta.asOf) }) : t('数据时间暂缺。')}
      </p>
    </div>
  );
}

interface SideRailProps {
  sector: SectorVm | null;
  rows: IvRowVm[];
  meta: IvMetaVm;
  loading: boolean;
  ivLoading: boolean;
  /** IV 请求失败且无数据（审计 2.2.8：错误不冒充空态） */
  ivError?: boolean;
  onIvRetry?: () => void;
  onOpenTicker: (ticker: string) => void;
  onOpenPalette: () => void;
}

export default function SideRail({
  sector,
  rows,
  meta,
  loading,
  ivLoading,
  ivError = false,
  onIvRetry,
  onOpenTicker,
  onOpenPalette,
}: SideRailProps) {
  if (loading) {
    return (
      <div
        className="grid grid-cols-1 gap-4 self-start md:grid-cols-2 lg:grid-cols-1"
        aria-hidden="true"
      >
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 self-start md:grid-cols-2 lg:grid-cols-1">
      <IvHeatCard
        rows={rows}
        loading={ivLoading}
        error={ivError}
        onRetry={onIvRetry}
        onOpenTicker={onOpenTicker}
        onOpenPalette={onOpenPalette}
      />
      <CoverageCard sector={sector} rows={rows} meta={meta} loading={ivLoading} error={ivError} />
    </div>
  );
}
