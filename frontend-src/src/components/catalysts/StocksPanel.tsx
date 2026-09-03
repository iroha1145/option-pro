/** stocks 面板：按股票的影响汇总（batch：净影响渐变条 / 最新时间 / 来源数 / count），点行进 /stock/:t */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { catalystsContract } from './api';
import type { TickerImpactSummary } from './api';
import type { CatalystFilters } from './filters';
import { toFeedQuery } from './filters';
import EmptyState from '@/components/shared/EmptyState';
import TickerLogo from '@/components/shared/TickerLogo';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonRows } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import { t } from '../../i18n/core.ts';

const RANGE = 5; // 净影响映射区间 ±5

/**
 * 窄屏（<sm）不渲染列头，读数会变成一个没有名字的数字。
 *
 * 原先靠「· 非收益」后缀兼当标签：每行重复一遍，却既没说清这个数是什么，
 * 也解释不了自己。改成真正的标签 + 一个 ⓘ；宽屏交给列头的同一个 ⓘ，
 * 不必每行重复。
 */
function NetImpactLabel() {
  return (
    <span className="flex shrink-0 items-center gap-1 text-micro text-ink-400 sm:hidden">
      {t('净影响')}
      <InfoHint hint={SCORE_HINTS.netImpact} size={11} />
    </span>
  );
}

/* 净影响渐变条：轨道 down→纸→up 渐变 + 零点刻度 + 指针 */
function NetImpactBar({ value, analyzed }: { value: number; analyzed: number }) {
  if (analyzed === 0) {
    return (
      <div className="flex items-center gap-2">
        <NetImpactLabel />
        <div className="h-1.5 w-28 rounded-pill bg-line" aria-hidden="true" />
        <span className="font-mono text-micro text-ink-300">—</span>
      </div>
    );
  }
  const clamped = Math.max(-RANGE, Math.min(RANGE, value));
  const pct = ((clamped + RANGE) / (2 * RANGE)) * 100;
  const tone = value > 0.05 ? 'text-up-700' : value < -0.05 ? 'text-down-700' : 'text-ink-500';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return (
    <div className="flex items-center gap-2.5">
      <NetImpactLabel />
      <div
        className="relative h-1.5 w-28 rounded-pill"
        style={{ background: 'linear-gradient(90deg, color-mix(in srgb, var(--down-600) 35%, transparent), rgba(233,231,224,.6) 50%, color-mix(in srgb, var(--up-600) 35%, transparent))' }}
        role="img"
        aria-label={t('净影响 {sign}{value}', { sign, value: Math.abs(value).toFixed(2) })}
      >
        <span className="absolute left-1/2 top-1/2 h-2.5 w-px -translate-x-1/2 -translate-y-1/2 bg-ink-300" aria-hidden="true" />
        <span
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
          style={{ left: `${pct}%` }}
          aria-hidden="true"
        >
          <motion.span
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 520, damping: 32, delay: 0.15 }}
            className={cn(
              'block size-2.5 rounded-full border-2 border-card',
              value >= 0 ? 'bg-up-600' : 'bg-down-600',
            )}
          />
        </span>
      </div>
      <span className={cn('font-mono text-data-m tnum', tone)} title={t("净影响分")}>
        {sign}
        {Math.abs(value).toFixed(2)}
      </span>
    </div>
  );
}

export default function StocksPanel({ filters, refreshToken }: { filters: CatalystFilters; refreshToken: number }) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<TickerImpactSummary[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const filtersKey = JSON.stringify(filters);
  const filtersRef = useMemo(() => filters, [filtersKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let dead = false;
    setLoading(true);
    setError(null);
    catalystsContract
      .tickerSummaries({ ...toFeedQuery(filtersRef) })
      .then((res) => {
        if (dead) return;
        setRows(res);
        setLoading(false);
      })
      .catch((e) => {
        if (dead) return;
        setError(e instanceof ApiError ? e : new ApiError(500, t('加载失败')));
        setLoading(false);
      });
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey, refreshToken]);

  /* 只显示有实际影响的：已完成分析且存在非中性方向；无分析/纯中性不上榜 */
  const impactful = useMemo(
    () =>
      (rows ?? [])
        .filter((r) => r.analyzed > 0 && r.bullish + r.bearish > 0)
        .sort((a, b) => Math.abs(b.netImpact) - Math.abs(a.netImpact) || b.count - a.count),
    [rows],
  );

  const content = useMemo(() => {
    if (loading && !rows) return <SkeletonRows rows={7} />;
    if (error) {
      return (
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={error.code === 503 ? t('影响汇总暂不可用') : t('加载失败')}
          description={error.code === 503 ? t('稍后刷新再试') : error.message}
        />
      );
    }
    if (impactful.length === 0) {
      return (
        <EmptyState
          image="/empty-news.svg"
          title={t("当前窗口暂无已分析出方向性影响的股票")}
          description={
            rows && rows.length > 0
              ? t('{n} 只候选股票的新闻尚未分析或影响为中性，暂不上榜', { n: rows.length })
              : t('放宽时间窗或清除过滤后重试')
          }
        />
      );
    }
    return (
      <div className="divide-y divide-line">
        {impactful.map((r, i) => (
          <motion.button
            key={r.ticker}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: Math.min(i * 0.035, 0.42) }}
            onClick={() => navigate(`/stock/${r.ticker}`)}
            className="group flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3.5 text-left transition-colors duration-fast hover:bg-paper-2/70 sm:px-5"
            aria-label={t('查看 {ticker} 股票详情', { ticker: r.ticker })}
          >
            <span className="flex min-w-0 flex-1 items-center gap-2.5 sm:w-40 sm:flex-none">
              <TickerLogo ticker={r.ticker} size={28} />
              <span className="min-w-0">
                <span className="block font-mono text-body-s font-semibold text-ink-800">{r.ticker}</span>
                {(r.name !== r.ticker || r.sector) && (
                  <span className="block truncate text-micro text-ink-400">
                    {r.name !== r.ticker ? r.name : ''}
                    {r.name !== r.ticker && r.sector ? ' · ' : ''}
                    {r.sector}
                  </span>
                )}
              </span>
            </span>
            <span className="order-last w-full min-w-0 sm:order-none sm:min-w-[180px] sm:flex-1">
              <NetImpactBar value={r.netImpact} analyzed={r.analyzed} />
            </span>
            <span className="hidden items-center gap-1 font-mono text-micro tnum md:flex" title={t("利多 / 利空 / 中性")}>
              <span className="text-up-700">{r.bullish}</span>
              <span className="text-ink-300">/</span>
              <span className="text-down-700">{r.bearish}</span>
              <span className="text-ink-300">/</span>
              <span className="text-ink-500">{r.neutral}</span>
            </span>
            <span className="hidden w-14 text-right font-mono text-micro text-ink-500 tnum sm:block" title={t("来源数")}>
              {r.sourceDiversity} {t('源')}
            </span>
            <span className="hidden w-16 text-right font-mono text-micro text-ink-400 tnum lg:block" title={t("最新新闻")}>
              {fmtRelative(r.latestAt)}
            </span>
            <span className="w-12 shrink-0 text-right font-mono text-data-m text-ink-800 tnum" title={t("相关新闻数")}>
              {r.count}
              <span className="ml-0.5 text-micro font-normal text-ink-400">{t('条')}</span>
            </span>
            <Icon name="chevron-right" size={14} className="shrink-0 text-ink-300 transition-colors group-hover:text-brand-600" />
          </motion.button>
        ))}
      </div>
    );
  }, [loading, rows, impactful, error, navigate]);

  return (
    <div className="card-surface overflow-hidden">
      <div className="hidden grid-cols-none items-center border-b border-line px-5 py-2.5 sm:flex">
        <p className="w-40 eyebrow">{t('代码')}</p>
        <p className="min-w-[180px] flex-1 eyebrow">
          {t('净影响')}
          <InfoHint hint={SCORE_HINTS.netImpact} side="bottom" size={11} className="ml-1" />
        </p>
        <p className="hidden eyebrow md:block">{t('多/空/中')}</p>
        <p className="hidden w-14 text-right eyebrow sm:block">{t('来源')}</p>
        <p className="hidden w-16 text-right eyebrow lg:block">{t('最新')}</p>
        <p className="w-12 text-right eyebrow">{t('新闻')}</p>
        <span className="w-3.5" />
      </div>
      {content}
    </div>
  );
}
