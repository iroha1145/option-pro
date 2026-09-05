/**
 * 宏观综合卡 — 综合分 / Regime / 7 日变化 / 置信度 / 有效模块数 / 数据截止。
 * 大分数用 font-mono + text-data-xxl + tnum；分数条复用 strengthBarClass 色阶。
 * 明确写出「历史分位，不是预测」，不做任何进度条式的品牌装饰。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { useCountUp } from '@/hooks/useCountUp';
import { strengthBarClass } from '@/lib/strengthColor';
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS_MACRO } from '@/lib/scoreHints';
import type { MacroComposite, MacroConditionsResponse } from '@/api/modules/macro';
import { t } from '../../../i18n/core.ts';

const HISTORY_BASIS_LABEL: Record<string, string> = {
  latest_revised_backfill: t('历史区间按当前修订值回算'),
  local_point_in_time: t('本地点时快照'),
  mixed: t('混合：部分区间按当前修订值回算'),
};

function StatLine({
  label,
  value,
  hintKey,
}: {
  label: string;
  value: string;
  hintKey?: keyof typeof SCORE_HINTS_MACRO;
}) {
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-1 text-micro text-ink-400">
        {label}
        {hintKey && <InfoHint hint={SCORE_HINTS_MACRO[hintKey]} side="bottom" align="start" size={11} />}
      </p>
      <p className="mt-0.5 truncate font-mono text-data-m text-ink-800 tnum">{value}</p>
    </div>
  );
}

export default function CompositeCard({
  composite,
  historyBasis,
  dataThrough,
}: {
  composite: MacroComposite;
  historyBasis: MacroConditionsResponse['historyBasis'];
  dataThrough: string | null;
}) {
  const hasScore = typeof composite.score === 'number' && Number.isFinite(composite.score);
  const animated = useCountUp(hasScore ? (composite.score as number) : Number.NaN);
  const shown = hasScore ? animated : null;

  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      className="card-surface flex h-full flex-col p-5"
      aria-label={t("宏观环境综合分")}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="eyebrow">{t('综合分 · COMPOSITE')}</p>
        <span className="flex items-center gap-1 text-micro text-ink-400">
          {t('历史分位')}
          <InfoHint hint={SCORE_HINTS_MACRO.macroComposite} side="bottom" align="end" size={11} />
        </span>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-2">
        <span className="font-mono text-data-xxl text-ink-900 tnum">
          <span className="sr-only">{hasScore ? (composite.score as number).toFixed(1) : '—'}</span>
          <span aria-hidden="true">{shown === null ? '—' : shown.toFixed(1)}</span>
        </span>
        <div className="flex flex-col gap-1 pb-1.5">
          {composite.regime ? (
            <span className="flex items-center gap-1 self-start rounded-xs border border-line bg-paper-2 px-2 py-0.5 text-caption text-ink-700">
              {t(composite.regime)}
              <InfoHint hint={SCORE_HINTS_MACRO.macroRegime} side="top" align="start" size={11} />
            </span>
          ) : (
            <span className="self-start text-caption text-ink-400">{t('环境标签暂不可用')}</span>
          )}
          <span className="flex items-center gap-1.5 text-micro text-ink-400">
            {t('7 日变化')}
            <ChangeBadge value={composite.scoreChange7d} size="sm" format="points" />
          </span>
        </div>
      </div>

      <div className="mt-4 h-[3px] overflow-hidden rounded-pill bg-line" role="presentation">
        {hasScore && (
          <motion.div
            className={cn(
              'h-full origin-left rounded-pill',
              strengthBarClass(composite.score as number),
            )}
            initial={{ scaleX: 0 }}
            whileInView={{ scaleX: 1 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            style={{ width: `${Math.max(2, Math.min(100, composite.score as number))}%` }}
          />
        )}
      </div>

      <div className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <StatLine
          label={t("置信度")}
          hintKey="macroConfidence"
          value={
            typeof composite.confidence === 'number'
              ? `${(composite.confidence * 100).toFixed(0)}%`
              : '—'
          }
        />
        <StatLine
          label={t("有效模块")}
          value={
            composite.validModuleCount === null
              ? '—'
              : `${composite.validModuleCount} / ${composite.totalModuleCount ?? 7}`
          }
        />
        <StatLine label={t("数据截止")} value={dataThrough ?? '—'} />
      </div>

      <p className="mt-4 border-t border-line pt-3 text-micro leading-relaxed text-ink-400">
        {t('分数是过去 5 年的历史分位，不是预测。高分表示当前金融环境相对历史更支持风险资产， 不代表市场一定上涨，也不构成买入、卖出、仓位或目标价建议。')}
        {historyBasis && (
          <>
            {' '}
            <span className="inline-flex items-center gap-1">
              {HISTORY_BASIS_LABEL[historyBasis] ?? historyBasis}
              <InfoHint hint={SCORE_HINTS_MACRO.macroHistoryBasis} side="top" align="end" size={11} />
            </span>
          </>
        )}
      </p>
    </section>
  );
}
