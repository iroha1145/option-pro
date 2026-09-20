import { get, mockOr, put, type RequestOptions } from '@/api/client';
import { asRec, pickB, pickS } from '@/api/live';
import {
  DEFAULT_ALGORITHM_PREFERENCES,
  markAlgorithmPreferencePendingSync,
  readAlgorithmPreferences,
  writeAlgorithmPreferences,
  type RadarSortChoice,
  type ScreenerRankingChoice,
} from '@/lib/algorithmPreferences';
import {
  currentPreferenceWriteGeneration,
  persistRemoteOrKeepLocal,
} from '@/lib/viewPreferenceWrites';

export interface ViewPreferencesDoc {
  principal: string | null;
  persisted: boolean;
  screenerRankingAlgorithm: ScreenerRankingChoice;
  radarSortAlgorithm: RadarSortChoice;
  syncError?: unknown;
}

function asScreener(value: string | null): ScreenerRankingChoice {
  if (value === 'production' || value === 'a0_mid_long' || value === 'eod_limited_v1' || value === 'follow_default') return value;
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
  write: (
    patch: {
      screenerRankingAlgorithm?: ScreenerRankingChoice;
      radarSortAlgorithm?: RadarSortChoice;
    },
    options?: RequestOptions,
  ): Promise<ViewPreferencesDoc> =>
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
        }, options).then(nDoc),
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

/** Persist is best-effort. The visible request already carries the explicit choice. */
export async function persistAlgorithmChoice(
  patch: {
    screenerRankingAlgorithm?: ScreenerRankingChoice;
    radarSortAlgorithm?: RadarSortChoice;
  },
  persistRemote: boolean,
  principal?: string | null,
): Promise<ViewPreferencesDoc> {
  const local = asLocalDoc(writeAlgorithmPreferences(patch, principal));
  if (!persistRemote) {
    markAlgorithmPreferencePendingSync(principal, false);
    return local;
  }
  const generation = currentPreferenceWriteGeneration();
  const result = await persistRemoteOrKeepLocal(
    local,
    (signal) => viewPreferencesApi.write(patch, signal ? { signal } : undefined),
    { principal: principal ?? undefined, generation },
  );
  markAlgorithmPreferencePendingSync(principal, Boolean(result.syncError) || result.persisted === false);
  return result;
}
