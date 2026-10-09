import type { EvidenceSource } from '../../api/types.ts';
import { normalizeEvidenceSources } from '../../api/evidenceSources.ts';
import { t } from '../../i18n/core.ts';

/** Sources belong to this saved analysis; intermediate tool content stays private. */
export default function AnalysisSources({ sources }: { sources?: readonly EvidenceSource[] | null }) {
  const links = normalizeEvidenceSources(sources);
  if (!links.length) return null;
  return (
    <div className="mt-3 min-w-0 border-t border-line pt-2.5">
      <p className="text-micro font-medium text-ink-500">{t('信息来源')}</p>
      <ul className="mt-1.5 grid min-w-0 gap-1.5">
        {links.map((source) => (
          <li key={source.url} className="min-w-0">
            <a href={source.url} target="_blank" rel="noopener noreferrer"
              className="block min-w-0 break-words [overflow-wrap:anywhere] text-micro leading-5 text-brand-600 underline decoration-line-strong underline-offset-4 hover:text-brand-500">
              {source.title}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
