/**
 * 市场形态 6 维条（契约 market_regime 字段对照）
 * index_trend / momentum / breadth / volume / risk_appetite / risk_on_spread
 * live：直读 /strength/market 的 market_regime 真实六维分 + 综合分/label/warnings（不再由分布推导）
 * mock：无 regime 字段时回退直方图确定性推导（不编造随机量）
 * grow-bar 700ms 错峰 45ms + 毛玻璃 tooltip
 */
import { motion } from 'framer-motion';
import type { MarketRegimeDims, MarketRegimeInfo, MarketStrength } from '@/api/types';
import { useCountUp } from '@/hooks/useCountUp';
import SourceNote from '@/components/shared/SourceNote';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

interface RegimeDim {
  key: string;
  label: string;
  en: string;
  /** null = 契约缺失（空轨道 + 「—」，不编造） */
  value: number | null;
  hint: string;
}

/** live：契约 market_regime 六维 → 展示维度（字段名如实标注） */
function liveDims(r: MarketRegimeInfo): RegimeDim[] {
  const d: MarketRegimeDims = r.dims;
  return [
    { key: 'index_trend', label: '指数趋势', en: 'INDEX TREND', value: d.indexTrend, hint: '契约 index_trend_score：指数层面趋势健康度。' },
    { key: 'momentum', label: '市场动量', en: 'MOMENTUM', value: d.momentum, hint: '契约 market_momentum_score：动量资金活跃度。' },
    { key: 'breadth', label: '市场广度', en: 'BREADTH', value: d.breadth, hint: '契约 market_breadth_score：上涨扩散广度。' },
    { key: 'volume', label: '量能配合', en: 'VOLUME', value: d.volume, hint: '契约 market_volume_score：量能对趋势的确认度。' },
    { key: 'risk_appetite', label: '风险偏好', en: 'RISK APPETITE', value: d.riskAppetite, hint: '契约 risk_appetite_score：资金追高风险意愿。' },
    {
      key: 'risk_on_spread',
      label: '强弱价差',
      en: 'RISK-ON SPREAD',
      value: d.riskOnSpread,
      hint: `契约 risk_on_spread_score：风险偏好强弱价差${r.spreadLabel ? `（${r.spreadLabel}）` : ''}。`,
    },
  ];
}

/** mock：由 10 桶直方图推导六维（0–100）——live 无直方图时不会走到这里 */
function deriveRegime(m: MarketStrength): RegimeDim[] {
  const h = m.histogram;
  const total = Math.max(1, h.reduce((s, n) => s + n, 0));
  const share = (from: number, to: number) => h.slice(from, to + 1).reduce((s, n) => s + n, 0) / total;
  const ge50 = share(5, 9);
  const ge60 = share(6, 9);
  const ge70 = share(7, 9);
  const ge85 = m.ge85Count / total;
  const wAvg = (from: number, to: number) => {
    let sw = 0;
    let sn = 0;
    for (let i = from; i <= to; i++) {
      sw += h[i] * (i * 10 + 5);
      sn += h[i];
    }
    return sn === 0 ? 0 : sw / sn;
  };
  const spread = Math.max(0, Math.min(100, wAvg(7, 9) - wAvg(0, 3)));
  const clamp = (v: number) => Math.max(0, Math.min(100, Math.round(v)));
  return [
    { key: 'index_trend', label: '指数趋势', en: 'INDEX TREND', value: clamp(m.avgScore), hint: '全市场强度分均值，衡量指数层面趋势健康度。' },
    { key: 'momentum', label: '市场动量', en: 'MOMENTUM', value: clamp(ge70 * 160), hint: '强度 ≥70 标的占比放大映射，刻画动量资金活跃度。' },
    { key: 'breadth', label: '市场广度', en: 'BREADTH', value: clamp(ge50 * 100), hint: '强度 ≥50 标的占全市场比例，越高说明上涨扩散越广。' },
    { key: 'volume', label: '量能配合', en: 'VOLUME', value: clamp(ge60 * 130), hint: '强度 ≥60 占比映射的资金参与度，配合趋势确认有效性。' },
    { key: 'risk_appetite', label: '风险偏好', en: 'RISK APPETITE', value: clamp(m.avgScore * 0.8 + ge85 * 80), hint: '均值与 ≥85 高强度占比加权，反映资金追高风险意愿。' },
    { key: 'risk_on_spread', label: '强弱价差', en: 'RISK-ON SPREAD', value: clamp(spread), hint: '高分组（≥70）与低分组（<40）均分之差，价差越大风格越极化。' },
  ];
}

function RegimeBar({ dim, index }: { dim: RegimeDim; index: number }) {
  const v = useCountUp(dim.value ?? 0, 900);
  return (
    <div className="group relative">
      <div className="flex items-center gap-3">
        <span className="w-16 shrink-0 text-caption text-ink-500">{dim.label}</span>
        <span className="relative h-1.5 flex-1 overflow-hidden rounded-pill bg-line" role="presentation">
          {dim.value !== null && (
            <motion.span
              className="block h-full origin-left rounded-pill bg-brand-500"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true, amount: 0.4 }}
              transition={{ duration: 0.7, ease: EASE_PAPER, delay: index * 0.045 }}
              style={{ width: `${Math.max(0, Math.min(100, dim.value))}%` }}
            />
          )}
        </span>
        <span className="w-8 shrink-0 text-right font-mono text-caption text-ink-800 tnum">
          {dim.value !== null ? Math.round(v) : '—'}
        </span>
      </div>
      {/* 毛玻璃 tooltip */}
      <div className="glass pointer-events-none absolute -top-2 left-16 z-20 hidden w-56 -translate-y-full rounded-md border border-line p-3 shadow-sh-2 group-hover:block">
        <p className="flex items-baseline justify-between">
          <span className="text-caption font-semibold text-ink-800">{dim.label}</span>
          <span className="font-mono text-micro text-ink-400">{dim.en}</span>
        </p>
        <p className="mt-1.5 text-micro leading-[16px] text-ink-500">{dim.hint}</p>
        <p className="mt-1.5 font-mono text-caption text-brand-600 tnum">
          {dim.value !== null ? `${Math.round(dim.value * 10) / 10} / 100` : '契约缺失 · 留空优于编造'}
        </p>
      </div>
    </div>
  );
}

export default function MarketRegimeCard({ market }: { market: MarketStrength }) {
  const regime = market.regime ?? null;
  const dims = regime ? liveDims(regime) : deriveRegime(market);
  return (
    <div className="card-surface p-5">
      <div className="flex items-baseline justify-between">
        <p className="eyebrow">市场形态 · MARKET REGIME</p>
        {regime && regime.score !== null ? (
          <span className="font-mono text-data-m text-ink-900 tnum">{regime.score}</span>
        ) : (
          <span className="font-mono text-micro text-ink-300 tnum">6 维</span>
        )}
      </div>
      {regime && (regime.label || regime.spreadLabel) && (
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {regime.label && (
            <span className="rounded-xs bg-brand-50 px-1.5 py-px text-micro font-medium text-brand-700">{regime.label}</span>
          )}
          {regime.spreadLabel && (
            <span className="rounded-xs border border-line bg-card-warm px-1.5 py-px text-micro text-ink-500">{regime.spreadLabel}</span>
          )}
        </p>
      )}
      <div className="mt-4 space-y-3">
        {dims.map((d, i) => (
          <RegimeBar key={d.key} dim={d} index={i} />
        ))}
      </div>
      {regime && regime.warnings.length > 0 && (
        <ul className="mt-3.5 space-y-1 border-t border-line pt-3">
          {regime.warnings.map((w, i) => (
            <li key={i} className="flex items-start gap-1.5 text-micro leading-[16px] text-warn-600">
              <span className="mt-px shrink-0" aria-hidden="true">
                ⚠
              </span>
              {w}
            </li>
          ))}
        </ul>
      )}
      <SourceNote
        className="mt-4"
        text={regime ? '来源：/strength/market · market_regime 快照 · 300s 轮询' : '推导自全市场强度分布 · 300s 轮询'}
      />
    </div>
  );
}
