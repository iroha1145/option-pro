/**
 * 市场形态 6 维条（契约 market_regime 字段对照）
 * index_trend / momentum / breadth / volume / risk_appetite / risk_on_spread
 * grow-bar 700ms 错峰 45ms + 毛玻璃 tooltip；值由 strengthApi.market() 全市场强度分布推导（确定性变换，不编造随机量）
 */
import { motion } from 'framer-motion';
import type { MarketStrength } from '@/api/types';
import { useCountUp } from '@/hooks/useCountUp';
import SourceNote from '@/components/shared/SourceNote';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

interface RegimeDim {
  key: string;
  label: string;
  en: string;
  value: number;
  hint: string;
}

/** 由 10 桶直方图推导六维（0–100） */
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
  const v = useCountUp(dim.value, 900);
  return (
    <div className="group relative">
      <div className="flex items-center gap-3">
        <span className="w-16 shrink-0 text-caption text-ink-500">{dim.label}</span>
        <span className="relative h-1.5 flex-1 overflow-hidden rounded-pill bg-line" role="presentation">
          <motion.span
            className="block h-full origin-left rounded-pill bg-brand-500"
            initial={{ scaleX: 0 }}
            whileInView={{ scaleX: 1 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.7, ease: EASE_PAPER, delay: index * 0.045 }}
            style={{ width: `${dim.value}%` }}
          />
        </span>
        <span className="w-8 shrink-0 text-right font-mono text-caption text-ink-800 tnum">{Math.round(v)}</span>
      </div>
      {/* 毛玻璃 tooltip */}
      <div className="glass pointer-events-none absolute -top-2 left-16 z-20 hidden w-56 -translate-y-full rounded-md border border-line p-3 shadow-sh-2 group-hover:block">
        <p className="flex items-baseline justify-between">
          <span className="text-caption font-semibold text-ink-800">{dim.label}</span>
          <span className="font-mono text-micro text-ink-400">{dim.en}</span>
        </p>
        <p className="mt-1.5 text-micro leading-[16px] text-ink-500">{dim.hint}</p>
        <p className="mt-1.5 font-mono text-caption text-brand-600 tnum">{dim.value} / 100</p>
      </div>
    </div>
  );
}

export default function MarketRegimeCard({ market }: { market: MarketStrength }) {
  const dims = deriveRegime(market);
  return (
    <div className="card-surface p-5">
      <div className="flex items-baseline justify-between">
        <p className="eyebrow">市场形态 · MARKET REGIME</p>
        <span className="font-mono text-micro text-ink-300 tnum">6 维</span>
      </div>
      <div className="mt-4 space-y-3">
        {dims.map((d, i) => (
          <RegimeBar key={d.key} dim={d} index={i} />
        ))}
      </div>
      <SourceNote className="mt-4" text="推导自全市场强度分布 · 300s 轮询" />
    </div>
  );
}
