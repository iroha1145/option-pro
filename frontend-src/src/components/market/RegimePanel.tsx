/**
 * B3 市场形态六维（strength/market 的 market_regime）
 * index_trend / market_momentum / market_breadth / market_volume / risk_appetite / risk_on_spread
 * 六条 grow-bar + 数值 + 毛玻璃 tooltip 解释；live 未覆盖 → 503「快照暂不可用」
 */
import { motion } from 'framer-motion';
import { EASE_PAPER, GROW_X } from '@/lib/motion';
import type { ApiError } from '@/api/client';
import type { MarketRegime } from './api';
import { cn } from '@/lib/utils';
import { strengthBarClass } from '@/lib/strengthColor';
import EmptyState from '@/components/shared/EmptyState';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS, type ScoreHint } from '@/lib/scoreHints';
import { SkeletonCard } from '@/components/shared/Skeleton';
import { BusyIcon } from '@/components/shared/IconSwap';
import { t } from '../../i18n/core.ts';

const DIMS: { key: keyof MarketRegime; label: string; hint: ScoreHint }[] = [
  { key: 'index_trend_score', label: t('指数趋势'), hint: SCORE_HINTS.regimeTrend },
  { key: 'market_momentum_score', label: t('市场动量'), hint: SCORE_HINTS.regimeMomentum },
  { key: 'market_breadth_score', label: t('市场广度'), hint: SCORE_HINTS.regimeBreadth },
  { key: 'market_volume_score', label: t('量能配合'), hint: SCORE_HINTS.regimeVolume },
  { key: 'risk_appetite_score', label: t('风险偏好'), hint: SCORE_HINTS.regimeRiskAppetite },
  { key: 'risk_on_spread_score', label: t('风险利差'), hint: SCORE_HINTS.regimeRiskOn },
];

import { regimeMean } from '@/lib/regime';

export default function RegimePanel({
  data,
  loading,
  error,
  onRetry,
  refreshing,
}: {
  data: MarketRegime | null;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  refreshing: boolean;
}) {
  if (loading) return <SkeletonCard className="h-full" />;
  if (error || !data) {
    return (
      <div className="card-surface h-full">
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={t("数据暂不可用")}
          description={error ? error.message : t('暂无市场环境六维数据')}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              aria-busy={refreshing}
              className="btn-primary"
            >
              <BusyIcon busy={refreshing} size={14} tone="on-accent" />
              {t('重试')}
            </button>
          }
        />
      </div>
    );
  }

  const mean = regimeMean(data);

  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      className="card-surface flex h-full flex-col p-5"
      aria-label={t("市场形态六维")}
    >
      <div className="flex items-start justify-between">
        <h3 className="text-h3 text-ink-900">{t('市场形态六维')}</h3>
        <p className="text-right">
          <span className="metric-value text-data-l text-ink-900">{mean.toFixed(1)}</span>
          <span className="block text-micro text-ink-400">
            {t('综合均值')}
            <InfoHint hint={SCORE_HINTS.marketRegime} side="bottom" align="end" size={11} className="ml-1" />
          </span>
        </p>
      </div>
      <div className="mt-5 grid flex-1 grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
        {DIMS.map((d, i) => {
          const score = data[d.key];
          return (
            <div key={d.key}>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-caption text-ink-600">
                  {d.label}
                  <InfoHint hint={d.hint} side="bottom" size={12} />
                </span>
                <span className="text-data-m text-ink-800 tnum">{score}</span>
              </div>
              <motion.div
                className="mt-1.5 h-1 strength-track overflow-hidden rounded-pill bg-line"
                role="presentation"
                initial="hidden"
                whileInView="shown"
                viewport={{ once: true, amount: 0.4 }}
              >
                <motion.div
                  className={cn('h-full origin-left rounded-pill', strengthBarClass(score))}
                  variants={GROW_X}
                  transition={{ duration: 0.7, ease: EASE_PAPER, delay: i * 0.045 }}
                  style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
                />
              </motion.div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
