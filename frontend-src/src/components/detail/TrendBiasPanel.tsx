/**
 * 技术信号 · trend_bias 面板（GET /api/signals/stock/{t} 完整形状）
 * 半环仪表（score 直接呈现 + draw-line）+ label（偏多/中性/偏空）+ status 可见
 * 分项条组（趋势/动量/量能/波动 grow-bar 错峰）+ 因子中文解读列表
 */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import { SkeletonText } from '@/components/shared/Skeleton';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { cn } from '@/lib/utils';
import { fmtTimeHHMMSS } from '@/lib/format';
import { getTrendBias, type StockTrendBiasView } from './api';
import type { TrendBiasFactor } from '@/mocks/fixtures';

const LABEL_STYLE: Record<StockTrendBiasView['trend_bias_label'], string> = {
  偏多: 'bg-up-50 text-up-700',
  中性: 'bg-warn-50 text-warn-600',
  偏空: 'bg-down-50 text-down-700',
  数据不足: 'bg-card-warm text-ink-500',
};

const STATUS_META: Record<StockTrendBiasView['trend_bias_status'], { text: string; cls: string } | null> = {
  ok: null,
  degraded: { text: '部分指标缺失', cls: 'border-warn-600/40 bg-warn-50 text-warn-600' },
  insufficient_data: { text: '数据不足 · 结果仅供参考', cls: 'border-line-strong bg-card-warm text-ink-500' },
};

const TONE_DOT: Record<TrendBiasFactor['tone'], string> = {
  bullish: 'bg-up-600',
  neutral: 'bg-ink-300',
  bearish: 'bg-down-600',
};

function gaugeColor(score: number): string {
  if (score >= 60) return 'var(--up-600)';
  if (score <= 40) return 'var(--down-600)';
  return 'var(--brand-500)';
}

/** 半环仪表（180°，r=64，厚 10px） */
function Gauge({ score, label }: { score: number; label: StockTrendBiasView['trend_bias_label'] }) {
  const R = 64;
  const C = Math.PI * R; // 半圆周长
  /* count-up 减量：非 hero 数字直接呈现终值 */
  const shown = score;
  return (
    <div className="relative mx-auto w-[168px]">
      <svg viewBox="0 0 168 92" className="w-full" role="img" aria-label={`趋势偏向分 ${score}，${label}`}>
        <path d={`M 20 84 A ${R} ${R} 0 0 1 148 84`} fill="none" stroke="var(--line)" strokeWidth="10" strokeLinecap="round" />
        <motion.path
          d={`M 20 84 A ${R} ${R} 0 0 1 148 84`}
          fill="none"
          stroke={gaugeColor(score)}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={{ strokeDashoffset: C * (1 - score / 100) }}
          transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }}
        />
      </svg>
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center">
        <span className="font-mono text-data-xl text-ink-900 tnum">{Math.round(shown)}</span>
        <span className="mt-0.5 flex items-center gap-1">
          <span className={cn('rounded-xs px-1.5 py-px text-caption font-medium', LABEL_STYLE[label])}>{label}</span>
          <InfoHint hint={SCORE_HINTS.trendBias} side="bottom" size={11} />
        </span>
      </div>
    </div>
  );
}

function MissingGauge({ label }: { label: StockTrendBiasView['trend_bias_label'] }) {
  return (
    <div className="relative mx-auto w-[168px]" role="img" aria-label="趋势偏向分数据不足">
      <svg viewBox="0 0 168 92" className="w-full" aria-hidden="true">
        <path d="M 20 84 A 64 64 0 0 1 148 84" fill="none" stroke="var(--line)" strokeWidth="10" strokeLinecap="round" />
      </svg>
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center">
        <span className="font-mono text-data-xl text-ink-400 tnum">—</span>
        <span className={cn('mt-0.5 rounded-xs px-1.5 py-px text-caption font-medium', LABEL_STYLE[label])}>{label}</span>
      </div>
    </div>
  );
}

const SUBSCORES: { key: keyof StockTrendBiasView['scores']; label: string }[] = [
  { key: 'trend', label: '趋势' },
  { key: 'momentum', label: '动量' },
  { key: 'volume', label: '量能' },
  { key: 'volatility', label: '波动' },
];

export default function TrendBiasPanel({
  ticker,
  refreshVersion = 0,
}: {
  ticker: string;
  refreshVersion?: number;
}) {
  const { data, loading, error } = usePolling(
    () => getTrendBias(ticker),
    null,
    [ticker, refreshVersion],
  );

  if (loading) return <SkeletonText lines={5} className="py-4" />;
  if (error || !data) {
    return (
      <p className="rounded-md border border-line bg-card-warm px-4 py-6 text-center text-body-s text-ink-400">
        暂无技术信号数据
      </p>
    );
  }

  const status = STATUS_META[data.trend_bias_status];
  return (
    <div>
      {data.trend_bias_score === null
        ? <MissingGauge label={data.trend_bias_label} />
        : <Gauge score={data.trend_bias_score} label={data.trend_bias_label} />}
      {status && (
        <p className={cn('mx-auto mt-3 w-fit rounded-xs border px-2 py-0.5 text-caption', status.cls)}>{status.text}</p>
      )}

      {/* 分项条组（grow-bar 错峰 60ms） */}
      <div className="mt-5 space-y-2.5">
        {SUBSCORES.map(({ key, label }, i) => {
          const v = data.scores[key];
          return (
            <div key={key} className="flex items-center gap-3">
              <span className="w-8 shrink-0 text-caption text-ink-500">{label}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-pill bg-line" role="presentation">
                <span
                  className={cn(
                    'block h-full origin-left animate-grow-bar rounded-pill',
                    v === null ? 'bg-line-strong' : strengthBarClass(v),
                  )}
                  style={{ width: `${v ?? 0}%`, animationDelay: `${i * 60}ms` }}
                />
              </span>
              <span className="w-8 shrink-0 text-right font-mono text-caption text-ink-600 tnum">
                {v === null ? '—' : Math.round(v)}
              </span>
            </div>
          );
        })}
      </div>

      {/* 因子中文解读 */}
      {data.factors.length > 0 ? (
        <ul className="mt-5 space-y-2 border-t border-line pt-4">
          {data.factors.map((f) => (
            <li key={f.key} className="flex items-start gap-2.5">
              <span className={cn('mt-[7px] size-1.5 shrink-0 rounded-full', TONE_DOT[f.tone])} aria-hidden="true" />
              <div className="min-w-0">
                <span className="mr-2 rounded-xs border border-line-strong bg-card-warm px-1.5 py-px text-micro font-medium text-ink-600">
                  {f.label}
                </span>
                <span className="text-body-s text-ink-500">{f.reading}</span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-5 border-t border-line pt-4 text-center text-body-s text-ink-400">
          暂无可展示的真实信号因子
        </p>
      )}

      <p className="mt-4 text-micro text-ink-400">
        分项由该股实测信号换算
        <InfoHint hint={SCORE_HINTS.trendBiasFactors} size={11} className="mx-0.5" />
        {' · 缺项显示 — · 非收益预测 · 更新于 '}
        <span className="font-mono tnum">{fmtTimeHHMMSS(new Date(data.as_of))}</span>
      </p>
    </div>
  );
}
