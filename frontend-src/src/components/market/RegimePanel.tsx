/**
 * B3 市场形态六维（strength/market 的 market_regime）
 * index_trend / market_momentum / market_breadth / market_volume / risk_appetite / risk_on_spread
 * 六条 grow-bar + 数值 + 毛玻璃 tooltip 解释；live 未覆盖 → 503「快照暂不可用」
 */
import { motion } from 'framer-motion';
import type { ApiError } from '@/api/client';
import type { MarketRegime } from './api';
import { cn } from '@/lib/utils';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import EmptyState from '@/components/shared/EmptyState';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS, type ScoreHint } from '@/lib/scoreHints';
import { SkeletonCard } from '@/components/shared/Skeleton';
import { t } from '../../i18n/core.ts';

/*
 * tip 是悬停时的一句话摘要，必须与 SCORE_HINTS 里的算法说明同源——三条原先与代码
 * 不符，且和紧挨着的 InfoHint 长说明自相矛盾：
 *   · 指数趋势原写「20/50 日线」，实际是 50/200 日线；
 *   · 市场广度原写「涨跌家数比与创新高/新低家数」——本系统没有任何个股家数数据，
 *     真实算法是 11 个行业 ETF 站上 50/200 日线的比例 + RSP÷SPY、IWM÷SPY 相对强弱；
 *   · 风险偏好原写「成长/小盘相对防御板块」，那是风险利差那一维干的事，
 *     它自己看的是 VIX、信用价差、久期与回撤。
 */
const DIMS: { key: keyof MarketRegime; label: string; tip: string; hint: ScoreHint }[] = [
  { key: 'index_trend_score', label: t('指数趋势'), tip: t('SPY / QQQ / IWM / RSP 相对 50 与 200 日均线的位置，加 SPY 200 日线斜率。'), hint: SCORE_HINTS.regimeTrend },
  { key: 'market_momentum_score', label: t('市场动量'), tip: t('SPY / QQQ / IWM 的 20 日涨跌，加 QQQ−SPY、RSP−SPY 的 20 日相对差。'), hint: SCORE_HINTS.regimeMomentum },
  { key: 'market_breadth_score', label: t('市场广度'), tip: t('11 个行业 ETF 中站上 50 / 200 日均线的比例，加等权对市值加权（RSP÷SPY）与小盘对大盘（IWM÷SPY）的 20 日相对强弱。'), hint: SCORE_HINTS.regimeBreadth },
  { key: 'market_volume_score', label: t('量能配合'), tip: t('SPY / QQQ 近 5 日方向与相对量能是否同向：放量上行加分，放量下行扣分。'), hint: SCORE_HINTS.regimeVolume },
  { key: 'risk_appetite_score', label: t('风险偏好'), tip: t('VIX 水平与一年分位、HYG−TLT 与 HYG−IEF 信用价差、10 年期利率变化、久期、SPY / QQQ 回撤。'), hint: SCORE_HINTS.regimeRiskAppetite },
  { key: 'risk_on_spread_score', label: t('风险利差'), tip: t('十组进攻／防守资产对的 20 日相对价差（QQQ/SPY、SOXX/XLK、HYG/IEF、XLY/XLP 等）。'), hint: SCORE_HINTS.regimeRiskOn },
];

/** 面板级口径：说清这六个数算自什么、以及为什么它不随选中的指数变。 */
const DIMS_SOURCE_NOTE =
  t('六维算自同一篮固定基准：SPY / QQQ / IWM / RSP、11 个行业 ETF、VIX、HYG / IEF / TLT / 10 年期、GLD、SOXX / SMH —— 是全市场读数，不区分指数。');

function regimeMean(r: MarketRegime): number {
  return DIMS.reduce((s, d) => s + r[d.key], 0) / DIMS.length;
}

export { regimeMean };

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
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105 disabled:opacity-60"
            >
              {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
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
        <p className="eyebrow">{t('市场形态六维 · MARKET REGIME')}</p>
        <p className="text-right">
          <span className="font-mono text-data-l text-ink-900 tnum">{mean.toFixed(1)}</span>
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
            <div key={d.key} className="group relative">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-caption text-ink-600">
                  {d.label}
                  <InfoHint hint={d.hint} side="bottom" size={12} />
                </span>
                <span className="font-mono text-data-m text-ink-800 tnum">{score}</span>
              </div>
              <div className="mt-1.5 h-[3px] overflow-hidden rounded-pill bg-line" role="presentation">
                <motion.div
                  className={cn('h-full origin-left rounded-pill', strengthBarClass(score))}
                  initial={{ scaleX: 0 }}
                  whileInView={{ scaleX: 1 }}
                  viewport={{ once: true, amount: 0.4 }}
                  transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.045 }}
                  style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
                />
              </div>
              {/* 毛玻璃 tooltip */}
              <div className="glass pointer-events-none absolute -top-2 left-0 z-20 hidden w-56 -translate-y-full rounded-md border border-line p-3 text-micro leading-relaxed text-ink-600 shadow-sh-2 group-hover:block">
                <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-ink-400">{d.key}</p>
                {d.tip}
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-4 border-t border-line pt-3 text-micro leading-relaxed text-ink-400">
        {t('色阶：<50 弱 · 50–69 中性 · 70–84 强 · ≥85 极强')}
        <span className="mt-1 block">{DIMS_SOURCE_NOTE}</span>
      </p>
    </section>
  );
}
