import { t } from '../../../i18n/core.ts';
import { LINE_INK } from './linePresentation.ts';

const RAIL_KINDS = new Set(['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge']);

/** Describe only the layers actually present; a gap-only view has its own legend. */
export default function AnalysisLegend({ overlays, smartEnabled }: {
  overlays: readonly { kind: string }[];
  smartEnabled: boolean;
}) {
  const hasRails = overlays.some(row => RAIL_KINDS.has(row.kind) || row.kind === 'level');
  const hasBox = overlays.some(row => row.kind === 'box');
  const hasGap = overlays.some(row => row.kind === 'gap');
  if (!hasRails && !hasBox && !hasGap) return null;
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-500">
      {hasRails && <>
        <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="h-0 w-4 border-t-[3px]" style={{ borderColor: LINE_INK.support }} />{t('支撑')}</span>
        <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="h-0 w-4 border-t-[3px]" style={{ borderColor: LINE_INK.resistance }} />{t('阻力')}</span>
        <span>{t('深色为主要边界，细线为参考；虚线为延伸，淡色点线为历史结构')}</span>
      </>}
      {hasBox && <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="h-2.5 w-4 rounded-sm border" style={{ backgroundColor: 'rgba(82,97,122,0.075)', borderColor: 'rgba(82,97,122,0.3)' }} />{t('整理区')}</span>}
      {hasGap && <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="h-2.5 w-4 rounded-sm border border-dashed" style={{ backgroundColor: 'rgba(184,120,33,0.10)', borderColor: 'rgba(184,120,33,0.3)' }} />{t('价格缺口（日线）')}</span>}
      {smartEnabled && <span>{t('智能标注仅辅助读图，不是买卖信号')}</span>}
    </p>
  );
}
