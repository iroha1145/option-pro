import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import { SkeletonBlock, SkeletonCard } from '@/components/shared/Skeleton';
import type { IvMetaVm, IvRowVm, SectorVm } from './model';
import {
  SOURCE_STATUS_CN,
  ivRankColor,
  toneOnColor,
} from './model';
import { t } from '../../i18n/core.ts';

function IvHeatCard({
  rows,
  loading,
  onOpenTicker,
  onOpenPalette,
}: {
  rows: IvRowVm[];
  loading: boolean;
  onOpenTicker: (ticker: string) => void;
  onOpenPalette: () => void;
}) {
  const top = useMemo(
    () =>
      rows
        .filter((row) => row.rank !== null)
        .sort((left, right) => (right.rank ?? 0) - (left.rank ?? 0))
        .slice(0, 8),
    [rows],
  );
  return (
    <div className="card-surface p-5">
      <div className="flex items-center justify-between">
        <p className="eyebrow">{t('当前 IV 横截面 · 高位')}</p>
        <Icon name="flame-line" size={16} className="text-ink-400" />
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {loading
          ? Array.from({ length: 8 }, (_, index) => (
              <SkeletonBlock key={index} className="h-14 rounded-md" />
            ))
          : top.map((row, index) => {
              const background = ivRankColor(row.rank ?? 0);
              const light = toneOnColor(background) === 'light';
              return (
                <motion.button
                  key={row.ticker}
                  type="button"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: 0.4,
                    ease: [0.16, 1, 0.3, 1],
                    delay: index * 0.04,
                  }}
                  whileHover={{
                    y: -2,
                    transition: { duration: 0.16, ease: [0.22, 1, 0.36, 1] },
                  }}
                  onClick={() => onOpenTicker(row.ticker)}
                  aria-label={`${row.ticker} 板块 IV 排位 ${row.rank}，打开详情`}
                  className="flex h-14 flex-col items-center justify-center gap-0.5 rounded-md shadow-sh-1 transition-shadow duration-fast hover:shadow-sh-2"
                  style={{ backgroundColor: background }}
                >
                  <span
                    className={cn(
                      'font-mono text-caption font-semibold leading-none',
                      light ? 'text-white' : 'text-ink-800',
                    )}
                  >
                    {row.ticker}
                  </span>
                  <span
                    className={cn(
                      'font-mono text-micro leading-none tnum',
                      light ? 'text-white/75' : 'text-ink-500',
                    )}
                  >
                    {row.rank}
                  </span>
                </motion.button>
              );
            })}
        <button
          type="button"
          onClick={onOpenPalette}
          aria-label={t("搜索更多代码并加入自选")}
          className="flex h-14 items-center justify-center rounded-md border border-dashed border-line-strong text-ink-400 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
        >
          <Icon name="plus" size={16} />
        </button>
      </div>
      {!loading && top.length === 0 && (
        <p className="mt-3 text-micro text-ink-400">{t('当前没有可用的 IV 样本。')}</p>
      )}
    </div>
  );
}

function CoverageCard({
  sector,
  rows,
  meta,
}: {
  sector: SectorVm | null;
  rows: IvRowVm[];
  meta: IvMetaVm;
}) {
  const ranked = rows
    .filter((row): row is IvRowVm & { rank: number } => row.rank !== null)
    .sort((left, right) => right.rank - left.rank);
  const highest = ranked[0] ?? null;
  const lowest = ranked[ranked.length - 1] ?? null;
  const statusLabel =
    meta.status === 'active' ? t('数据正常') : SOURCE_STATUS_CN[meta.status];

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
              ? 'border-up-600/20 bg-up-50 text-up-700'
              : 'border-warn-600/25 bg-warn-50 text-warn-600',
          )}
        >
          {statusLabel}
        </span>
      </div>

      <dl className="mt-4 divide-y divide-line">
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-caption text-ink-500">{t('成功样本')}</dt>
          <dd className="font-mono text-data-m text-ink-800 tnum">
            {meta.successCount ?? rows.length} / {meta.requestedCount ?? '—'}
          </dd>
        </div>
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-caption text-ink-500">{t('横截面最高')}</dt>
          <dd className="font-mono text-data-m text-ink-800 tnum">
            {highest ? `${highest.ticker} · ${highest.rank.toFixed(1)}` : '—'}
          </dd>
        </div>
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-caption text-ink-500">{t('横截面最低')}</dt>
          <dd className="font-mono text-data-m text-ink-800 tnum">
            {lowest ? `${lowest.ticker} · ${lowest.rank.toFixed(1)}` : '—'}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-micro leading-5 text-ink-400">
        {meta.asOf ? `数据时间 ${fmtRelative(meta.asOf)}。` : t('数据时间暂缺。')}
        {t('排位只比较当前板块成分的 ATM IV，不代表一年历史百分位。')}
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
  onOpenTicker: (ticker: string) => void;
  onOpenPalette: () => void;
}

export default function SideRail({
  sector,
  rows,
  meta,
  loading,
  ivLoading,
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
        onOpenTicker={onOpenTicker}
        onOpenPalette={onOpenPalette}
      />
      <CoverageCard sector={sector} rows={rows} meta={meta} />
    </div>
  );
}
