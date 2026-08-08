/**
 * K线结构分析卡（与日股工作台同款三段：高低点结构 / 整理区检测 / 量价配合）
 * 数据源 /stocks/{t}/technical；后端下发中文标签在此处过 t()（与雷达同纪律）。
 *
 * 纪律（2026-08-08 深修批）：
 * - 「检出过基底」≠「基底现在仍有效」：base_state 说明最新价相对结构的位置
 *   （区间内/测上沿/已突破/破位），未收盘末根参与判定时标「盘中暂定」。
 * - null 一律显「—」，不折成 0（量价缺数据时假突破风险不是零）。
 * - 形态/Spring/Upthrust 是最近数根内的历史事件，带发生时点。
 */
import InfoHint from '@/components/shared/InfoHint';
import { STRUCTURE_HINTS } from '@/lib/structureHints';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { TechBaseState, TechnicalStructure } from '@/api/types';
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

const signedOr = (v: number | null): string =>
  v === null ? '—' : `${v > 0 ? '+' : ''}${v}`;

/** 事件发生时点：0 根前 = 最新一根 */
const barsAgoText = (barsAgo: number | null): string | null => {
  if (barsAgo === null) return null;
  return barsAgo === 0 ? t('最新一根') : t('{n} 根前', { n: barsAgo });
};

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

/* 与 SignalList RESULT_META 同惯例：标签在定义处 t()，渲染处直接用（不二次翻译） */
const BASE_STATE_META: Record<TechBaseState['status'], { label: string; cls: string }> = {
  in_base: { label: t('价格在结构区间内'), cls: 'bg-paper-2 text-ink-600' },
  at_resistance: { label: t('正测试区间上沿'), cls: 'bg-warn-50 text-warn-600' },
  breakout: { label: t('已突破区间上沿'), cls: 'bg-up-50 text-up-700' },
  below_support: { label: t('跌破支撑下沿'), cls: 'bg-down-50 text-down-700' },
  failed: { label: t('已跌破失效位'), cls: 'bg-down-50 text-down-700' },
};

export default function StructurePanel({ technical }: { technical: TechnicalStructure | null }) {
  if (!technical) return <p className="mt-3 text-body-s text-ink-400">{t('暂无数据')}</p>;
  const pa = technical.price_action;
  const vpm = technical.vol_price;
  const base = technical.base;
  const state = technical.base_state;
  const stateMeta = state ? BASE_STATE_META[state.status] : null;
  const coverage = base?.quality_coverage ?? null;
  const vpmMeasured = vpm.status === 'active';
  const springWhen = barsAgoText(pa.spring_bars_ago);
  const upthrustWhen = barsAgoText(pa.upthrust_bars_ago);
  const provisionalNote = technical.last_bar && technical.last_bar.closed === false;
  return (
    <div className="mt-3 space-y-3">
      {/* 高低点结构 */}
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-caption text-ink-500">
            {t('高低点结构')}
            <InfoHint hint={STRUCTURE_HINTS.market_structure} />
          </span>
          <span className="text-caption font-medium text-ink-800">{pa.structure_label ? t(pa.structure_label) : '—'}</span>
        </div>
        <ScoreLine label={t('价格行为')} score={pa.score} hint={STRUCTURE_HINTS.price_action_score} />
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {(pa.pattern_events.length > 0 ? pa.pattern_events : pa.pattern_labels.map((label) => ({ label, bars_ago: null }))).map((event) => {
            const when = barsAgoText(event.bars_ago);
            return (
              <span key={event.label} className="rounded-pill border border-line bg-card px-2 py-0.5 text-micro text-ink-600">
                {t(event.label)}
                {when && <span className="text-ink-400"> · {when}</span>}
              </span>
            );
          })}
          {pa.spring && (
            <span className="inline-flex items-center gap-1 rounded-pill bg-up-50 px-2 py-0.5 text-micro text-up-700">
              {t('假跌破收回')}
              {springWhen && <span className="opacity-75">· {springWhen}</span>}
              <InfoHint hint={STRUCTURE_HINTS.spring} size={11} />
            </span>
          )}
          {pa.upthrust && (
            <span className="inline-flex items-center gap-1 rounded-pill bg-down-50 px-2 py-0.5 text-micro text-down-700">
              {t('假突破回落')}
              {upthrustWhen && <span className="opacity-75">· {upthrustWhen}</span>}
              <InfoHint hint={STRUCTURE_HINTS.upthrust} size={11} />
            </span>
          )}
        </div>
        <dl className="mt-2 grid grid-cols-2 gap-1.5 text-caption">
          <StructFact
            label={t('最近确认阻力')}
            value={t('{price}（距 {dist}）', { price: priceOr(pa.resistance), dist: distPct(pa.resistance_dist_pct) })}
            hint={STRUCTURE_HINTS.confirmed_levels}
          />
          <StructFact
            label={t('最近确认支撑')}
            value={t('{price}（距 {dist}）', { price: priceOr(pa.support), dist: distPct(pa.support_dist_pct) })}
          />
        </dl>
      </div>

      {/* 整理区检测 */}
      <div className="border-t border-line pt-2.5">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-caption text-ink-500">
            {t('整理区检测')}
            <InfoHint hint={STRUCTURE_HINTS.base_detection} />
          </span>
          <span className="flex items-center gap-1 text-caption font-medium text-ink-800">
            {base
              ? t('{n} 次触碰 · 质量 {q}', {
                  n: base.resistance_touches ?? '—',
                  q: base.quality !== null ? Math.round((base.quality ?? 0) * 100) : '—',
                })
              : t('未检测到完成的整理区')}
            {base && <InfoHint hint={STRUCTURE_HINTS.base_quality} size={11} align="end" />}
          </span>
        </div>
        {base && stateMeta && state && (
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <span className={cn('inline-flex items-center gap-1 rounded-pill px-2 py-0.5 text-micro font-medium', stateMeta.cls)}>
              {stateMeta.label}
              {state.provisional && <span className="opacity-75">{t('（盘中暂定）')}</span>}
              <InfoHint hint={STRUCTURE_HINTS.base_state} size={11} />
            </span>
            {coverage && coverage.observed < coverage.total && (
              <span className="rounded-pill border border-line bg-card px-2 py-0.5 text-micro text-ink-400">
                {t('{a}/{b} 维实测', { a: coverage.observed, b: coverage.total })}
              </span>
            )}
            {typeof base.window_agreement === 'number' && typeof base.windows_scanned === 'number' && (
              <span className="rounded-pill border border-line bg-card px-2 py-0.5 text-micro text-ink-400">
                {t('{k}/{n} 档窗口检出', { k: base.window_agreement, n: base.windows_scanned })}
              </span>
            )}
          </div>
        )}
        {base && (
          <dl className="grid grid-cols-2 gap-1.5 text-caption">
            <StructFact label={t('阻力带')} value={`${priceOr(base.resistance_low)} – ${priceOr(base.resistance_high)}`} />
            <StructFact label={t('失效位')} value={priceOr(base.invalidation_price)} />
            <StructFact label={t('形成区间')} value={`${base.base_start ?? '—'} → ${base.base_end ?? '—'}`} />
            <StructFact label={t('支撑下沿')} value={priceOr(base.support_low)} />
          </dl>
        )}
      </div>

      {/* 量价配合 */}
      <div className="border-t border-line pt-2.5">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-caption text-ink-500">
            {t('量价配合')}
            <InfoHint hint={STRUCTURE_HINTS.vol_price} />
          </span>
          <span className="rounded-pill bg-ai-50 px-2 py-0.5 text-micro font-medium text-ai-600">
            {vpm.setup_label ? t(vpm.setup_label) : '—'}
          </span>
        </div>
        <dl className="grid grid-cols-2 gap-1.5 text-caption">
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
            label={t('突破质量修正')}
            value={vpmMeasured ? signedOr(vpm.breakout_quality_adjustment) : '—'}
            hint={STRUCTURE_HINTS.breakout_quality}
          />
          <StructFact
            label={t('假突破风险')}
            value={vpmMeasured ? signedOr(vpm.false_breakout_risk) : '—'}
            hint={STRUCTURE_HINTS.false_breakout_risk}
          />
        </dl>
        {vpm.tags.length > 0 && (
          <p className="mt-1.5 text-micro text-ink-400">{vpm.tags.map((tag) => t(tag)).join(' · ')}</p>
        )}
      </div>

      <p className="text-micro text-ink-400">
        {technical.data_through && t('计算截至 {date} 收盘', { date: technical.data_through })}
        {provisionalNote && <span> · {t('末根未收盘，不计入指标')}</span>}
        {technical.series_break_at && <span> · {t('检测到 {date} 处序列断裂，仅分析其后数据', { date: technical.series_break_at })}</span>}
      </p>
    </div>
  );
}
