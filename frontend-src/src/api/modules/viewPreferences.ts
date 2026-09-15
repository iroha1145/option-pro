import { get, mockOr, put } from '@/api/client';
import { asRec, pickB, pickS } from '@/api/live';
import {
  DEFAULT_ALGORITHM_PREFERENCES,
  readAlgorithmPreferences,
  writeAlgorithmPreferences,
  type RadarSortChoice,
  type ScreenerRankingChoice,
} from '@/lib/algorithmPreferences';

export interface ViewPreferencesDoc {
  principal: string | null;
  persisted: boolean;
  screenerRankingAlgorithm: ScreenerRankingChoice;
  radarSortAlgorithm: RadarSortChoice;
}

function asScreener(value: string | null): ScreenerRankingChoice {
  if (value === 'production' || value === 'a0_mid_long' || value === 'follow_default') return value;
  return DEFAULT_ALGORITHM_PREFERENCES.screenerRankingAlgorithm;
}

function asRadar(value: string | null): RadarSortChoice {
  if (value === 'production' || value === 't1_daily_priority' || value === 'follow_default') return value;
  return DEFAULT_ALGORITHM_PREFERENCES.radarSortAlgorithm;
}

function nDoc(d: unknown): ViewPreferencesDoc {
  const r = asRec(d);
  return {
    principal: pickS(r, 'principal'),
    persisted: pickB(r, 'persisted') ?? false,
    screenerRankingAlgorithm: asScreener(pickS(r, 'screener_ranking_algorithm', 'screenerRankingAlgorithm')),
    radarSortAlgorithm: asRadar(pickS(r, 'radar_sort_algorithm', 'radarSortAlgorithm')),
  };
}

export const viewPreferencesApi = {
  read: (): Promise<ViewPreferencesDoc> =>
    mockOr(
      () => {
        const local = readAlgorithmPreferences();
        return {
          principal: null,
          persisted: false,
          screenerRankingAlgorithm: local.screenerRankingAlgorithm,
          radarSortAlgorithm: local.radarSortAlgorithm,
        };
      },
      () => get('/view-preferences').then(nDoc),
    ),
  write: (patch: {
    screenerRankingAlgorithm?: ScreenerRankingChoice;
    radarSortAlgorithm?: RadarSortChoice;
  }): Promise<ViewPreferencesDoc> =>
    mockOr(
      () => {
        const next = writeAlgorithmPreferences(patch);
        return {
          principal: null,
          persisted: false,
          screenerRankingAlgorithm: next.screenerRankingAlgorithm,
          radarSortAlgorithm: next.radarSortAlgorithm,
        };
      },
      () =>
        put('/view-preferences', {
          ...(patch.screenerRankingAlgorithm
            ? { screener_ranking_algorithm: patch.screenerRankingAlgorithm }
            : {}),
          ...(patch.radarSortAlgorithm ? { radar_sort_algorithm: patch.radarSortAlgorithm } : {}),
        }).then(nDoc),
    ),
};

function asLocalDoc(local: ReturnType<typeof writeAlgorithmPreferences>): ViewPreferencesDoc {
  return {
    principal: null,
    persisted: false,
    screenerRankingAlgorithm: local.screenerRankingAlgorithm,
    radarSortAlgorithm: local.radarSortAlgorithm,
  };
}

/** Persist the visible choice before a follow_default request can read the old one. */
export async function persistAlgorithmChoice(
  patch: {
    screenerRankingAlgorithm?: ScreenerRankingChoice;
    radarSortAlgorithm?: RadarSortChoice;
  },
  persistRemote: boolean,
): Promise<ViewPreferencesDoc> {
  const local = asLocalDoc(writeAlgorithmPreferences(patch));
  if (!persistRemote) return local;
  try {
    return await viewPreferencesApi.write(patch);
  } catch {
    // Local choice is already saved. A 503/401 here must not block scanning or radar reload.
    return local;
  }
}
