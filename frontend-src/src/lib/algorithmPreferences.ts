/** Persist optional production algorithm choices without overriding explicit originals. */

import {
  LEGACY_PREFERENCE_STORAGE_KEY,
  preferenceStorageKey,
} from './choiceGeneration.ts';

export const SCREENER_EOD_LIMITED = 'eod_limited_v1';
export const RADAR_FOLLOW_DEFAULT = 'follow_default';
export const RADAR_PRODUCTION = 'production';
export const RADAR_T1 = 't1_daily_priority';

export type ScreenerRankingChoice = 'follow_default' | 'production' | 'a0_mid_long' | 'eod_limited_v1';
export type RadarSortChoice = 'follow_default' | 'production' | 't1_daily_priority';

export interface AlgorithmPreferences {
  screenerRankingAlgorithm: ScreenerRankingChoice;
  radarSortAlgorithm: RadarSortChoice;
}

export const DEFAULT_ALGORITHM_PREFERENCES: AlgorithmPreferences = {
  screenerRankingAlgorithm: SCREENER_EOD_LIMITED,
  radarSortAlgorithm: RADAR_FOLLOW_DEFAULT,
};

function asScreenerChoice(value: unknown): ScreenerRankingChoice {
  // The screener has one current engine. Legacy values remain readable so old
  // local/account documents cannot restore removed mathematics or UI modes.
  void value;
  return SCREENER_EOD_LIMITED;
}

function asRadarChoice(value: unknown): RadarSortChoice {
  if (value === RADAR_PRODUCTION || value === RADAR_T1 || value === RADAR_FOLLOW_DEFAULT) {
    return value;
  }
  return RADAR_FOLLOW_DEFAULT;
}

function readStorage(key: string): Record<string, unknown> | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: AlgorithmPreferences | Record<string, unknown>): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota / private mode must not crash the page.
  }
}

export function readAlgorithmPreferences(principal?: string | null): AlgorithmPreferences {
  const scoped = readStorage(preferenceStorageKey(principal));
  const legacy = principal ? null : readStorage(LEGACY_PREFERENCE_STORAGE_KEY);
  const parsed = scoped ?? legacy;
  if (!parsed) return { ...DEFAULT_ALGORITHM_PREFERENCES };
  return {
    screenerRankingAlgorithm: asScreenerChoice(parsed.screenerRankingAlgorithm),
    radarSortAlgorithm: asRadarChoice(parsed.radarSortAlgorithm),
  };
}

export function writeAlgorithmPreferences(
  next: Partial<AlgorithmPreferences>,
  principal?: string | null,
): AlgorithmPreferences {
  const merged: AlgorithmPreferences = {
    ...readAlgorithmPreferences(principal),
    ...next,
    screenerRankingAlgorithm: SCREENER_EOD_LIMITED as ScreenerRankingChoice,
  };
  const current = readStorage(preferenceStorageKey(principal)) ?? {};
  writeStorage(preferenceStorageKey(principal), {
    ...current,
    ...merged,
  } as AlgorithmPreferences & Record<string, unknown>);
  return merged;
}

export function markAlgorithmPreferencePendingSync(
  principal: string | null | undefined,
  pending: boolean,
): void {
  const key = preferenceStorageKey(principal);
  const current = readStorage(key) ?? readAlgorithmPreferences(principal);
  writeStorage(key, { ...current, pendingSync: pending } as AlgorithmPreferences & { pendingSync?: boolean });
}

export function algorithmPreferencePendingSync(principal?: string | null): boolean {
  const parsed = readStorage(preferenceStorageKey(principal));
  return parsed?.pendingSync === true;
}
