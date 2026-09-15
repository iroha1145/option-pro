/** Last-intent guards for algorithm choice, remote prefs, and paged history. */

export function nextChoiceGeneration(current: number): number {
  return current + 1;
}

export function shouldCommitChoiceGeneration(started: number, current: number): boolean {
  return started === current;
}

export function shouldApplyRemoteAlgorithmPreference(input: {
  startedGeneration: number;
  currentGeneration: number;
  cancelled: boolean;
  pendingLocalSync?: boolean;
}): boolean {
  if (input.pendingLocalSync) return false;
  return !input.cancelled && input.startedGeneration === input.currentGeneration;
}

export function shouldCommitHistoryPage(input: {
  startedGeneration: number;
  currentGeneration: number;
  startedCursor: string | null;
  currentCursor: string | null;
  startedSort: string | null | undefined;
  currentSort: string | null | undefined;
}): boolean {
  return (
    input.startedGeneration === input.currentGeneration
    && input.startedCursor === input.currentCursor
    && input.startedSort === input.currentSort
  );
}

export function historyPageDecision(input: {
  startedGeneration: number;
  currentGeneration: number;
  startedCursor: string | null;
  currentCursor: string | null;
  startedSort: string | null | undefined;
  currentSort: string | null | undefined;
  cursorStale?: boolean;
  restartRequired?: boolean;
}): 'ignore' | 'restart' | 'append' {
  if (!shouldCommitHistoryPage(input)) return 'ignore';
  if (input.cursorStale || input.restartRequired) return 'restart';
  return 'append';
}

export function preferenceStorageKey(principal?: string | null): string {
  const id = String(principal || '').trim();
  return id ? `optix.algorithm-prefs.v1:${id}` : 'optix.algorithm-prefs.v1:guest';
}

export const LEGACY_PREFERENCE_STORAGE_KEY = 'optix.algorithm-prefs.v1';
