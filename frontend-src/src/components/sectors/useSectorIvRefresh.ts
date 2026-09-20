import { useCallback, useEffect, useState } from 'react';
import { sectorsApi } from '../../api/modules/sectors.ts';
import {
  SectorIvRefreshFlow,
  type SectorIvFlowSnapshot,
} from './ivRefreshFlow.ts';
import { SectorIvReadScheduler } from './sectorIvReadScheduler.ts';

export function useSectorIvRefresh(sectorId: string | null): SectorIvFlowSnapshot & {
  retryRead: () => void;
  requestRefresh: () => void;
} {
  const [flow] = useState(() =>
    new SectorIvRefreshFlow({
      read: sectorsApi.ivRanking,
      refresh: sectorsApi.ivRefresh,
    }),
  );
  const [snapshot, setSnapshot] = useState(() => flow.getSnapshot());
  const [scheduler] = useState(() =>
    new SectorIvReadScheduler(
      () => flow.read(),
      () => document.visibilityState === 'visible',
      {
        now: () => Date.now(),
        setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
        clearTimeout: (handle) => window.clearTimeout(handle),
        setInterval: (callback, intervalMs) => window.setInterval(callback, intervalMs),
        clearInterval: (handle) => window.clearInterval(handle),
      },
    ),
  );

  useEffect(() => flow.subscribe(setSnapshot), [flow]);

  useEffect(() => {
    void flow.selectSector(sectorId);
  }, [flow, sectorId]);

  useEffect(() => {
    scheduler.update(
      snapshot,
      snapshot.sectorId === sectorId ? sectorId : null,
    );
  }, [scheduler, sectorId, snapshot]);

  useEffect(() => {
    if (!sectorId) return undefined;
    const onVisible = () => scheduler.onVisible();
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [scheduler, sectorId]);

  useEffect(() => () => scheduler.dispose(), [scheduler]);

  const retryRead = useCallback(() => {
    void flow.read();
  }, [flow]);
  const requestRefresh = useCallback(() => {
    void flow.submit();
  }, [flow]);

  return { ...snapshot, retryRead, requestRefresh };
}
