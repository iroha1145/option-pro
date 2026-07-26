/**
 * 单个因子行 —— 桌面渲染为表格行，移动端渲染为纵向卡片（同一组件两种模式）。
 * 320px 宽下不出现横向滚动：移动端不使用表格布局。
 */
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import { MACRO_FACTOR_HINTS } from '@/lib/scoreHints';
import type { MacroFactor } from '@/api/modules/macro';
import { t } from '../../../i18n/core.ts';

const STATUS_LABEL: Record<MacroFactor['status'], string> = {
  ok: '',
  stale: t('数据陈旧'),
  missing: t('数据缺失'),
  insufficient_history: t('历史不足'),
};

function FactorName({ factor }: { factor: MacroFactor }) {
  const hint = MACRO_FACTOR_HINTS[factor.factorId];
  return (
    <span className="flex min-w-0 items-center gap-1">
      <span className="truncate text-ink-700">{t(factor.nameZh)}</span>
      {hint && <InfoHint hint={hint} side="bottom" align="start" size={12} />}
    </span>
  );
}

function StatusNote({ factor }: { factor: MacroFactor }) {
  const label = STATUS_LABEL[factor.status];
  if (!label) return null;
  const detail = [...factor.missingInputs, ...factor.staleInputs].slice(0, 4).join('、');
  return (
    <span className="text-micro text-warn-600">
      {label}
      {detail ? `（${detail}）` : ''}
    </span>
  );
}

function ScoreText({ value }: { value: number | null }) {
  return (
    <span className="font-mono text-data-m text-ink-800 tnum">
      {typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) : '—'}
    </span>
  );
}

/** 桌面表格行。 */
export function FactorTableRow({ factor }: { factor: MacroFactor }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-2.5 pr-3 text-left text-body-s font-normal">
        <FactorName factor={factor} />
        <span className="mt-0.5 block">
          <StatusNote factor={factor} />
        </span>
      </th>
      <td className="py-2.5 pr-3 text-right">
        <span className="font-mono text-data-m text-ink-800 tnum">
          {factor.formattedValue ?? '—'}
        </span>
        {factor.formattedSignedValue && factor.formattedSignedValue !== factor.formattedValue && (
          <span className="mt-0.5 block font-mono text-micro text-ink-400 tnum">
            {t('带符号')} {factor.formattedSignedValue}
          </span>
        )}
      </td>
      <td className="py-2.5 pr-3 text-right">
        <ScoreText value={factor.score} />
      </td>
      <td className="py-2.5 pr-3 text-right">
        <span className="font-mono text-micro text-ink-600 tnum">
          {factor.formattedRawChange7d ?? '—'}
        </span>
      </td>
      <td className="py-2.5 pr-3 text-right">
        <ChangeBadge value={factor.scoreChange7d} size="sm" format="points" />
      </td>
      <td className="py-2.5 text-right font-mono text-micro text-ink-400 tnum">
        {factor.dataThrough ?? '—'}
      </td>
    </tr>
  );
}

/** 移动端纵向卡片。 */
export function FactorCard({ factor }: { factor: MacroFactor }) {
  return (
    <li className="border-t border-line py-3 first:border-t-0">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-body-s">
          <FactorName factor={factor} />
        </span>
        <ScoreText value={factor.score} />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5">
        <div className="min-w-0">
          <dt className="text-micro text-ink-400">{t('当前值')}</dt>
          <dd className="truncate font-mono text-micro text-ink-700 tnum">
            {factor.formattedValue ?? '—'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-micro text-ink-400">{t('7 日原值变化')}</dt>
          <dd className="truncate font-mono text-micro text-ink-700 tnum">
            {factor.formattedRawChange7d ?? '—'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-micro text-ink-400">{t('7 日分数变化')}</dt>
          <dd>
            <ChangeBadge value={factor.scoreChange7d} size="sm" format="points" />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-micro text-ink-400">{t('数据截止')}</dt>
          <dd className="truncate font-mono text-micro text-ink-400 tnum">
            {factor.dataThrough ?? '—'}
          </dd>
        </div>
      </dl>
      <StatusNote factor={factor} />
    </li>
  );
}
