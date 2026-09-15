/** Persist optional production algorithm choices without overriding explicit originals. */

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

const STORAGE_KEY = 'optix.algorithm-prefs.v1';

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

export function readAlgorithmPreferences(): AlgorithmPreferences {
  if (typeof window === 'undefined') return { ...DEFAULT_ALGORITHM_PREFERENCES };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_ALGORITHM_PREFERENCES };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      screenerRankingAlgorithm: asScreenerChoice(parsed.screenerRankingAlgorithm),
      radarSortAlgorithm: asRadarChoice(parsed.radarSortAlgorithm),
    };
  } catch {
    return { ...DEFAULT_ALGORITHM_PREFERENCES };
  }
}

export function writeAlgorithmPreferences(next: Partial<AlgorithmPreferences>): AlgorithmPreferences {
  const merged = { ...readAlgorithmPreferences(), ...next };
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
  }
  return merged;
}

export function requestedScreenerAlgorithm(
  choice: ScreenerRankingChoice,
): Exclude<ScreenerRankingChoice, 'follow_default'> | undefined {
  return choice === SCREENER_FOLLOW_DEFAULT ? undefined : choice;
}

export function requestedRadarAlgorithm(
  choice: RadarSortChoice,
): Exclude<RadarSortChoice, 'follow_default'> | undefined {
  return choice === RADAR_FOLLOW_DEFAULT ? undefined : choice;
}
