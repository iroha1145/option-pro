import { t } from '../../../i18n/core.ts';
import { LINE_INK } from './linePresentation.ts';

const RAIL_KINDS = new Set(['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge']);

/** Explain the ink that is actually visible, including a level-only selection. */
export default function AnalysisLegend({ overlays, smartEnabled }: {
  overlays: readonly { kind: string }[];
  smartEnabled: boolean;
}) {
  const hasRails = overlays.some(row => RAIL_KINDS.has(row.kind));
  const hasLevels = overlays.some(row => row.kind === 'level');
  if (!hasRails && !hasLevels) return null;
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-500">
      <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="h-0 w-4 border-t-[3px]" style={{ borderColor: LINE_INK.support }} />{t('支撑')}</span>
      <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="h-0 w-4 border-t-[3px]" style={{ borderColor: LINE_INK.resistance }} />{t('阻力')}</span>
      <span>{hasRails
        ? t('实线为结构边界，虚线为延伸；淡色点线为已失效结构')
        : t('实线为水平价位；淡色点线为已失效价位')}</span>
      {smartEnabled && <span>{t('智能标注仅辅助读图，不是买卖信号')}</span>}
    </p>
  );
}
