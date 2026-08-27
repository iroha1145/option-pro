/** Session undo/redo. Ordinary server echoes must use replacePresent, not push. */

export interface HistoryState<T> {
  past: T[];
  present: T;
  future: T[];
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function createHistory<T>(present: T): HistoryState<T> {
  return { past: [], present: clone(present), future: [] };
}

export function historyPush<T>(state: HistoryState<T>, next: T, limit = 80): HistoryState<T> {
  const past = [...state.past, clone(state.present)];
  if (past.length > limit) past.shift();
  return { past, present: clone(next), future: [] };
}

export function historyReplace<T>(state: HistoryState<T>, next: T): HistoryState<T> {
  return { ...state, present: clone(next) };
}

export function historyUndo<T>(state: HistoryState<T>): HistoryState<T> {
  if (!state.past.length) return state;
  const past = state.past.slice(0, -1);
  const present = state.past[state.past.length - 1];
  return { past, present, future: [clone(state.present), ...state.future] };
}

export function historyRedo<T>(state: HistoryState<T>): HistoryState<T> {
  if (!state.future.length) return state;
  const [present, ...future] = state.future;
  return {
    past: [...state.past, clone(state.present)],
    present,
    future,
  };
}

export function canUndo<T>(state: HistoryState<T>): boolean {
  return state.past.length > 0;
}

export function canRedo<T>(state: HistoryState<T>): boolean {
  return state.future.length > 0;
}
