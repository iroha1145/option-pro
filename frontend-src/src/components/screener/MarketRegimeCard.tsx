/**
 * 市场形态 6 维条（契约 market_regime 字段对照）
 * index_trend / momentum / breadth / volume / risk_appetite / risk_on_spread
 * live：直读 /strength/market 的 market_regime 真实六维分 + 综合分/label/warnings（不再由分布推导）
 * mock：使用与真实接口相同的六维数据结构
 * 数值条首帧显示完整比例，不以自身零面积观测触发；说明按需查看
 */
import SoftBadge from '@/components/shared/SoftBadge';
import type { MarketRegimeDims, MarketRegimeInfo, MarketStrength } from '@/api/types';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS, type ScoreHintKey } from '@/lib/scoreHints';
import { t } from '../../i18n/core.ts';

interface RegimeDim {
  key: string;
  label: string;
  /** null = 契约缺失（空轨道 + 「—」，不编造） */
  value: number | null;
  /** 评分算法解释（lib/scoreHints） */
  hintKey: ScoreHintKey;
}

/** live：契约 market_regime 六维 → 展示维度（字段名如实标注） */
function liveDims(r: MarketRegimeInfo): RegimeDim[] {
  const d: MarketRegimeDims = r.dims;
  return [
    { key: 'index_trend', label: t('指数走势'), value: d.indexTrend, hintKey: 'regimeTrend' },
    { key: 'momentum', label: t('市场动量'), value: d.momentum, hintKey: 'regimeMomentum' },
    { key: 'breadth', label: t('市场广度'), value: d.breadth, hintKey: 'regimeBreadth' },
    { key: 'volume', label: t('量能配合'), value: d.volume, hintKey: 'regimeVolume' },
    { key: 'risk_appetite', label: t('风险偏好'), value: d.riskAppetite, hintKey: 'regimeRiskAppetite' },
    {
      key: 'risk_on_spread',
      label: t('攻防价差'),
      value: d.riskOnSpread,
      hintKey: 'regimeRiskOn',
    },
  ];
}

function RegimeBar({ dim }: { dim: RegimeDim }) {
  /* count-up 减量：六维条数值直接呈现终值 */
  const v = dim.value ?? 0;
  return (
    <div className="col-span-3 grid grid-cols-subgrid items-center gap-x-3">
        <span className="whitespace-nowrap text-caption text-ink-500">
          {dim.label}
          <InfoHint hint={SCORE_HINTS[dim.hintKey]} side="bottom" size={11} className="ml-0.5" />
        </span>
        <span className="strength-track relative h-1.5 flex-1 overflow-hidden rounded-pill bg-paper" role="presentation">
          {dim.value !== null && (
            <span
              className="block h-full origin-left rounded-pill bg-brand-500"
              style={{ width: `${Math.max(0, Math.min(100, dim.value))}%` }}
            />
          )}
        </span>
        <span className="metric-value text-right text-caption text-ink-800 tnum">
          {dim.value !== null ? Math.round(v) : '—'}
        </span>
    </div>
  );
}

export default function MarketRegimeCard({ market }: { market: MarketStrength }) {
  const regime = market.regime ?? null;
  const dims = liveDims(regime ?? {
    score: null, label: null, spreadLabel: null, warnings: [], asOf: null,
    dims: { indexTrend: null, momentum: null, breadth: null, volume: null, riskAppetite: null, riskOnSpread: null },
  });
  return (
    <div className="card-surface p-5">
      <div className="flex items-baseline justify-between">
        <p className="eyebrow">
          {t('走势评分')}
          <InfoHint hint={SCORE_HINTS.marketRegime} side="bottom" size={12} className="ml-1" />
        </p>
        {regime && regime.score !== null ? (
          <span className="metric-value text-data-m text-ink-900 tnum">{regime.score}</span>
        ) : (
          <span className="text-micro text-ink-400 tnum">{t('六项')}</span>
        )}
      </div>
      {regime && (regime.label || regime.spreadLabel) && (
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {regime.label && (
            <SoftBadge tone="brand">{regime.label}</SoftBadge>
          )}
          {regime.spreadLabel && (
            <SoftBadge>{regime.spreadLabel}</SoftBadge>
          )}
        </p>
      )}
      <div className="mt-4 grid grid-cols-[max-content_minmax(0,1fr)_max-content] gap-y-3">
        {dims.map((d) => (
          <RegimeBar key={d.key} dim={d} />
        ))}
      </div>
      {regime && regime.warnings.length > 0 && (
        <ul className="mt-3.5 space-y-1 border-t border-line pt-3">
          {regime.warnings.map((w, i) => (
            <li key={i}>
              <SoftBadge tone="warn" className="items-start whitespace-normal">
                <span className="mt-px shrink-0" aria-hidden="true">⚠</span>
                {w}
              </SoftBadge>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
