/** Versioned local persistence. Corrupt payloads are ignored, never thrown. */
import { migrateStoredPayload, type SchemaError } from './schema.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';

export const STORAGE_PREFIX = 'option-pro:chart-drawings:v1';

export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export function drawingsStorageKey(
  identity: string,
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment = 'raw',
): string {
  return `${STORAGE_PREFIX}:${identity}:${ticker}:${range}:${adjustment}`;
}

export function anonymousStorageKey(
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment = 'raw',
): string {
  return drawingsStorageKey('anonymous', ticker, range, adjustment);
}

export type LoadResult =
  | { ok: true; drawings: ChartDrawing[] }
  | { ok: false; error: string; drawings: [] };

export function loadDrawings(
  key: string,
  storage?: StorageLike | null,
): LoadResult {
  const store = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
  if (!store) return { ok: true, drawings: [] };
  let raw: string | null;
  try {
    raw = store.getItem(key);
  } catch {
    return { ok: false, error: 'storage_blocked', drawings: [] };
  }
  if (!raw) return { ok: true, drawings: [] };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: 'corrupt', drawings: [] };
  }
  const migrated = migrateStoredPayload(parsed);
  if (!migrated.ok) {
    return { ok: false, error: (migrated as SchemaError).error, drawings: [] };
  }
  return { ok: true, drawings: migrated.value };
}

export function saveDrawings(
  key: string,
  drawings: ChartDrawing[],
  storage?: StorageLike | null,
): boolean {
  const store = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
  if (!store) return false;
  try {
    store.setItem(key, JSON.stringify({ schemaVersion: 1, drawings }));
    return true;
  } catch {
    return false;
  }
}

export function clearDrawings(key: string, storage?: StorageLike | null): void {
  const store = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
  if (!store) return;
  try {
    store.removeItem(key);
  } catch {
    /* ignore */
  }
}
