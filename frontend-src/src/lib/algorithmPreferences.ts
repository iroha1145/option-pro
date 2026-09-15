/** Persist optional production algorithm choices without overriding explicit originals. */

import {
  LEGACY_PREFERENCE_STORAGE_KEY,
  preferenceStorageKey,
} from './choiceGeneration.ts';

export const SCREENER_FOLLOW_DEFAULT = 'follow_default';
export const SCREENER_PRODUCTION = 'production';
export const SCREENER_A0 = 'a0_mid_long';
export const RADAR_FOLLOW_DEFAULT = 'follow_default';
export const RADAR_PRODUCTION = 'production';
export const RADAR_T1 = 't1_daily_priority';

export type ScreenerRankingChoice = 'follow_default' | 'production' | 'a0_mid_long';
export type RadarSortChoice = 'follow_default' | 'production' | 't1_daily_priority';

export interface AlgorithmPreferences {
  screenerRankingAlgorithm: ScreenerRankingChoice;
  radarSortAlgorithm: RadarSortChoice;
}

export const DEFAULT_ALGORITHM_PREFERENCES: AlgorithmPreferences = {
  screenerRankingAlgorithm: SCREENER_FOLLOW_DEFAULT,
  radarSortAlgorithm: RADAR_FOLLOW_DEFAULT,
};

function asScreenerChoice(value: unknown): ScreenerRankingChoice {
  if (value === SCREENER_PRODUCTION || value === SCREENER_A0 || value === SCREENER_FOLLOW_DEFAULT) {
    return value;
  }
  return SCREENER_FOLLOW_DEFAULT;
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

function writeStorage(key: string, value: AlgorithmPreferences): void {
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
  const merged = { ...readAlgorithmPreferences(principal), ...next };
  writeStorage(preferenceStorageKey(principal), merged);
  return merged;
}

export function requestedScreenerAlgorithm(
  choice: ScreenerRankingChoice,
): ScreenerRankingChoice {
  return choice;
}

export function requestedRadarAlgorithm(
  choice: RadarSortChoice,
): RadarSortChoice {
  return choice;
}
