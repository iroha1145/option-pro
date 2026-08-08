/**
 * K线结构分析卡（与日股工作台同款三段：市场结构 / 基底检测 / 量价一致）
 * 数据源 /stocks/{t}/technical；后端下发中文标签在此处过 t()（与雷达同纪律）。
 */
import InfoHint from '@/components/shared/InfoHint';
import { STRUCTURE_HINTS } from '@/lib/structureHints';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { TechnicalStructure } from '@/api/types';
import { t } from '../../i18n/core.ts';

function StructFact({ label, value, hint }: { label: string; value: string; hint?: typeof STRUCTURE_HINTS[string] }) {
  return (
    <div className="rounded-md bg-paper-2 px-2 py-1">
      <dt className="flex flex-wrap items-center gap-x-1 text-micro text-ink-400">
        {label}
        {hint && <InfoHint hint={hint} size={10} />}
      </dt>
      <dd className="font-mono text-caption text-ink-800 tnum">{value}</dd>
    </div>
  );
}

const priceOr = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? fmtPrice(v) : '—';

const distPct = (v: number | null): string => (v === null ? '—' : `${v > 0 ? '+' : ''}${v}%`);

function ScoreLine({ label, score, hint }: { label: string; score: number | null; hint?: typeof STRUCTURE_HINTS[string] }) {
  const valid = typeof score === 'number' && Number.isFinite(score);
  return (
    <div className="mt-1.5 flex items-center gap-2">
      <span className="flex items-center gap-1 text-caption text-ink-500">
        {label}
        {hint && <InfoHint hint={hint} size={11} />}
      </span>
      <span className="h-[3px] flex-1 overflow-hidden rounded-pill bg-line" role="presentation">
        {valid && (
          <span
            className={cn('block h-full origin-left rounded-pill animate-grow-bar', strengthBarClass(score))}
            style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
          />
        )}
      </span>
      <span className="font-mono text-caption text-ink-700 tnum">{valid ? Math.round(score) : '—'}</span>
    </div>
  );
}

export default function StructurePanel({ technical }: { technical: TechnicalStructure | null }) {
  if (!technical) return <p className="mt-3 text-body-s text-ink-400">{t('暂无数据')}</p>;
  const pa = technical.price_action;
  const vpm = technical.vol_price;
  const base = technical.base;
  return (
    <div className="mt-3 space-y-3">
      {/* 市场结构 */}
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-caption text-ink-500">
            {t('市场结构（HH/HL）')}
            <InfoHint hint={STRUCTURE_HINTS.market_structure} />
          </span>
          <span className="text-caption font-medium text-ink-800">{pa.structure_label ? t(pa.structure_label) : '—'}</span>
        </div>
        <ScoreLine label={t('价格行为')} score={pa.score} hint={STRUCTURE_HINTS.price_action_score} />
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {pa.pattern_labels.map((label) => (
            <span key={label} className="rounded-pill border border-line bg-card px-2 py-0.5 text-micro text-ink-600">
              {t(label)}
            </span>
          ))}
          {pa.spring && (
            <span className="inline-flex items-center gap-1 rounded-pill bg-up-50 px-2 py-0.5 text-micro text-up-700">
              {t('Spring 假跌破回收')}
              <InfoHint hint={STRUCTURE_HINTS.spring} size={11} />
            </span>
          )}
          {pa.upthrust && (
            <span className="inline-flex items-center gap-1 rounded-pill bg-down-50 px-2 py-0.5 text-micro text-down-700">
              {t('Upthrust 假突破')}
              <InfoHint hint={STRUCTURE_HINTS.upthrust} size={11} />
            </span>
          )}
        </div>
        <dl className="mt-2 grid grid-cols-2 gap-1.5 text-caption">
          <StructFact
            label={t('摆动阻力')}
            value={t('{price}（距 {dist}）', { price: priceOr(pa.resistance), dist: distPct(pa.resistance_dist_pct) })}
          />
          <StructFact
            label={t('摆动支撑')}
            value={t('{price}（距 {dist}）', { price: priceOr(pa.support), dist: distPct(pa.support_dist_pct) })}
          />
        </dl>
      </div>

      {/* 基底 */}
      <div className="border-t border-line pt-2.5">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-caption text-ink-500">
            {t('基底检测（枢轴聚类）')}
            <InfoHint hint={STRUCTURE_HINTS.base_detection} />
          </span>
          <span className="flex items-center gap-1 text-caption font-medium text-ink-800">
            {base
              ? t('{n} 次触碰 · 质量 {q}', {
                  n: base.resistance_touches ?? '—',
                  q: base.quality !== null ? Math.round((base.quality ?? 0) * 100) : '—',
                })
              : t('未检测到完成基底')}
            {base && <InfoHint hint={STRUCTURE_HINTS.base_quality} size={11} align="end" />}
          </span>
        </div>
        {base && (
          <dl className="grid grid-cols-2 gap-1.5 text-caption">
            <StructFact label={t('阻力带')} value={`${priceOr(base.resistance_low)} – ${priceOr(base.resistance_high)}`} />
            <StructFact label={t('失效位')} value={priceOr(base.invalidation_price)} />
            <StructFact label={t('基底区间')} value={`${base.base_start ?? '—'} → ${base.base_end ?? '—'}`} />
            <StructFact label={t('支撑下沿')} value={priceOr(base.support_low)} />
          </dl>
        )}
      </div>

      {/* 量价一致 */}
      <div className="border-t border-line pt-2.5">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-caption text-ink-500">
            {t('量价一致（努力/结果）')}
            <InfoHint hint={STRUCTURE_HINTS.vol_price} />
          </span>
          <span className="rounded-pill bg-ai-50 px-2 py-0.5 text-micro font-medium text-ai-600">
            {vpm.setup_label ? t(vpm.setup_label) : '—'}
          </span>
        </div>
        <dl className="grid grid-cols-3 gap-1.5 text-caption">
          <StructFact
            label={t('努力')}
            value={vpm.effort !== null ? `${vpm.effort.toFixed(2)}x` : '—'}
            hint={STRUCTURE_HINTS.effort}
          />
          <StructFact
            label={t('结果')}
            value={vpm.result !== null ? `${vpm.result.toFixed(2)}x` : '—'}
            hint={STRUCTURE_HINTS.result}
          />
          <StructFact
            label={t('假突破风险')}
            value={`${vpm.false_breakout_risk > 0 ? '+' : ''}${vpm.false_breakout_risk}`}
            hint={STRUCTURE_HINTS.false_breakout_risk}
          />
        </dl>
        {vpm.tags.length > 0 && (
          <p className="mt-1.5 text-micro text-ink-400">{vpm.tags.map((tag) => t(tag)).join(' · ')}</p>
        )}
      </div>
    </div>
  );
}
