import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import { SkeletonBlock, SkeletonCard } from '@/components/shared/Skeleton';
import type { IvMetaVm, IvRowVm, SectorVm } from './model';
import {
  SOURCE_STATUS_CN,
  ivRankInk,
  ivRankTint,
} from './model';
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
              const rank = row.rank ?? 0;
              /* v8.3 去糖果色：白底 card + 极淡 tint 底（边缘热色竖条按用户
                 要求移除，热度只靠 tint 底 + 排位数字的 ink 色阶表达）。 */
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
                  aria-label={t('{ticker} 板块 IV 排位 {rank}，打开详情', { ticker: row.ticker, rank: row.rank })}
                  className="relative flex h-14 flex-col items-center justify-center gap-0.5 rounded-md border border-line bg-card shadow-sh-1 transition-shadow duration-fast hover:shadow-sh-2"
                  style={{ backgroundColor: ivRankTint(rank) }}
                >
                  <span className="font-mono text-caption font-semibold leading-none text-ink-800">
                    {row.ticker}
                  </span>
                  <span
                    className="font-mono text-micro leading-none tnum"
                    style={{ color: ivRankInk(rank) }}
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
        error ? (
          <p className="mt-3 flex items-center gap-2 text-micro text-ink-400">
            {t('IV 数据读取失败——是读不到，不是该板块没有样本。')}
            {onRetry && (
              <button
                onClick={onRetry}
                className="rounded-md border border-line px-2 py-0.5 text-micro text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
              >
                {t('重试')}
              </button>
            )}
          </p>
        ) : (
          <p className="mt-3 text-micro text-ink-400">{t('当前没有可用的 IV 样本。')}</p>
        )
      )}
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
            {error && meta.successCount === null
              ? '— / —'
              : `${meta.successCount ?? rows.length} / ${meta.requestedCount ?? '—'}`}
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
        {meta.asOf ? t('数据时间 {time}。', { time: fmtRelative(meta.asOf) }) : t('数据时间暂缺。')}
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
