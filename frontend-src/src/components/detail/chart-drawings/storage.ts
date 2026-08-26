/** Versioned local persistence. Corrupt payloads are ignored, never thrown. */
import { collectStoredDrawings } from './schema.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';

export const STORAGE_PREFIX = 'option-pro:chart-drawings:v1';
export const OUTBOX_STORAGE_PREFIX = 'option-pro:chart-drawing-outbox:v1';

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

export function outboxStorageKey(
  identity: string,
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment = 'raw',
): string {
  return `${OUTBOX_STORAGE_PREFIX}:${identity}:${ticker}:${range}:${adjustment}`;
}

export type LoadResult =
  | { ok: true; drawings: ChartDrawing[] }
  | { ok: false; error: string; drawings: ChartDrawing[]; recoverable: boolean };

export function quarantineKey(key: string): string {
  return `${key}:quarantine`;
}

/**
 * 改写之前把原文另存一份。一行坏掉就整份判死、再被空列表覆盖写回的话，本来还能
 * 解析的图形会永久消失；先留底再谈重写。
 */
export function quarantineDrawings(key: string, storage?: StorageLike | null): boolean {
  const store = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
  if (!store) return false;
  try {
    const raw = store.getItem(key);
    if (!raw) return false;
    store.setItem(quarantineKey(key), raw);
    return true;
  } catch {
    return false;
  }
}

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
    return { ok: false, error: 'storage_blocked', drawings: [], recoverable: false };
  }
  if (!raw) return { ok: true, drawings: [] };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: 'corrupt', drawings: [], recoverable: false };
  }
  // 逐条校验：坏行丢掉，好行留下，绝不用一行坏掉换来整段清空。
  const collected = collectStoredDrawings(parsed);
  if (collected.fatal) {
    return { ok: false, error: collected.fatal, drawings: [], recoverable: false };
  }
  if (collected.dropped > 0) {
    return {
      ok: false,
      error: collected.error ?? 'invalid_drawing',
      drawings: collected.drawings,
      recoverable: true,
    };
  }
  return { ok: true, drawings: collected.drawings };
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
