import { useId, useState } from 'react';
import Icon from '@/components/icons';
import CollapsePresence from '@/components/shared/CollapsePresence';
import { cn } from '@/lib/utils';
import type { EvidenceSource } from '../../api/types.ts';
import { normalizeEvidenceSources } from '../../api/evidenceSources.ts';
import { t } from '../../i18n/core.ts';
import { SourceLinkList } from './AnalysisSources';

/* 热点追踪的来源动辄十条，默认收起：标题行写明条数，点开再列出链接。
   其他地方（新闻详情、个股分析）仍用常开的 AnalysisSources。 */
export default function CollapsibleAnalysisSources({ sources }: { sources?: readonly EvidenceSource[] | null }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const links = normalizeEvidenceSources(sources);
  if (!links.length) return null;
  return (
    <div className="mt-3 min-w-0 border-t border-line">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        className="touch-target flex min-h-10 w-full items-center justify-between gap-3 py-2 text-left text-micro text-ink-500 transition-colors duration-fast hover:text-ink-800"
      >
        <span className="min-w-0">{t('信息来源 · {n} 条', { n: links.length })}</span>
        <Icon name="chevron-down" size={14} className={cn('shrink-0 transition-transform duration-ui', open && 'rotate-180')} />
      </button>
      <CollapsePresence open={open} id={panelId}>
        <SourceLinkList links={links} className="grid min-w-0 gap-1.5 pb-2" />
      </CollapsePresence>
    </div>
  );
}
