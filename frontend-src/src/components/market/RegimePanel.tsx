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

const DIMS: { key: keyof MarketRegime; label: string; tip: string; hint: ScoreHint }[] = [
  { key: 'index_trend_score', label: '指数趋势', tip: '主要指数相对关键均线（20/50 日）的位置与斜率，衡量大盘方向性。', hint: SCORE_HINTS.regimeTrend },
  { key: 'market_momentum_score', label: '市场动量', tip: '全市场上涨动能与价格速率，动能衰竭常领先于指数见顶。', hint: SCORE_HINTS.regimeMomentum },
  { key: 'market_breadth_score', label: '市场广度', tip: '涨跌家数比与创新高/新低家数，判断指数上涨是否有广度支撑。', hint: SCORE_HINTS.regimeBreadth },
  { key: 'market_volume_score', label: '量能配合', tip: '成交量相对均量的放大程度，放量上行比缩量上行更可信。', hint: SCORE_HINTS.regimeVolume },
  { key: 'risk_appetite_score', label: '风险偏好', tip: '成长/小盘相对防御板块的表现，反映资金的风险承担意愿。', hint: SCORE_HINTS.regimeRiskAppetite },
  { key: 'risk_on_spread_score', label: '风险利差', tip: '风险资产与避险资产的相对强弱，利差走阔偏向 risk-on。', hint: SCORE_HINTS.regimeRiskOn },
];

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
          title="数据暂不可用"
          description={error ? error.message : '暂无市场环境六维数据'}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
            >
              {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
              重试
            </button>
          }
        />
      </div>
    );
  }

  const mean = regimeMean(data);

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
      className="card-surface flex h-full flex-col p-5"
      aria-label="市场形态六维"
    >
      <div className="flex items-start justify-between">
        <p className="eyebrow">市场形态六维 · MARKET REGIME</p>
        <p className="text-right">
          <span className="font-mono text-data-l text-ink-900 tnum">{mean.toFixed(1)}</span>
          <span className="block text-micro text-ink-400">
            综合均值
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
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-pill bg-line" role="presentation">
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
      <p className="mt-4 border-t border-line pt-3 text-micro text-ink-400">
        色阶：&lt;50 弱 · 50–69 中性 · 70–84 强 · ≥85 极强
      </p>
    </motion.section>
  );
}
