import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { isMock } from '@/api/client';
import { mapLatest, type MarketBriefLatest } from '@/api/modules/marketBrief';
import { SERVER_BRIEF_ARCHIVE } from '@/mocks/marketBriefServerArchive';

/** Local archives only; never changes the live API or a production report. */
export function useMarketBriefArchiveSelection() {
  const [params] = useSearchParams();
  const enabled = import.meta.env.DEV && isMock && params.get('report') === 'openui'
    && params.get('sample') !== 'demo';
  return enabled
    ? SERVER_BRIEF_ARCHIVE.find(item => item.runId === params.get('run')) ?? SERVER_BRIEF_ARCHIVE[0] ?? null
    : null;
}

export function useMarketBriefPreview(latest: MarketBriefLatest | null): MarketBriefLatest | null {
  const archive = useMarketBriefArchiveSelection();
  return useMemo(() => archive ? mapLatest(archive.response) : latest, [archive, latest]);
}
