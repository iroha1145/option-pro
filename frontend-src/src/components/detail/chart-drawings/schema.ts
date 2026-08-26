/** Versioned JSON schema for drawings. Import never executes expressions or CSS. */
import {
  ANCHOR_COUNTS,
  CHART_RANGES,
  DRAWINGS_PER_RANGE_MAX,
  DRAWING_KINDS,
  DRAWING_TEXT_MAX,
  SCHEMA_VERSION,
  type ChartDrawing,
  type DrawingKind,
  type DrawingStyle,
} from './types.ts';

export const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;
export const PALETTE_COLORS = new Set([
  '#2E46E0',
  '#3B59F2',
  '#6B82FF',
  '#0E9F6E',
  '#E5484D',
  '#E8930C',
  '#0B7285',
  '#3D4A68',
  '#8A94B0',
  'brand',
  'up',
  'down',
  'ink',
  'warn',
  'ai',
]);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const WIDTHS = new Set([1, 2, 3, 4]);
const DASHES = new Set(['solid', 'dashed', 'dotted']);
const PRICE_MAX = 10_000_000;

export type SchemaError = { ok: false; error: string };
export type SchemaOk<T> = { ok: true; value: T };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isAllowedColor(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  if (PALETTE_COLORS.has(value)) return true;
  return HEX_COLOR.test(value);
}

export function normalizeColor(value: string): string {
  return value.startsWith('#') ? value.toUpperCase() : value;
}

function parseIso(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  const text = value.endsWith('Z') ? value.slice(0, -1) + '+00:00' : value;
  const ms = Date.parse(text);
  if (!Number.isFinite(ms)) return null;
  return new Date(ms).toISOString();
}

function parseAnchor(raw: unknown): ChartDrawing['anchors'][number] | null {
  if (!isRecord(raw)) return null;
  if (Object.keys(raw).some((key) => !['time', 'barKey', 'price'].includes(key))) return null;
  const time = parseIso(raw.time);
  const barKey = typeof raw.barKey === 'string' ? raw.barKey.trim() : '';
  const price = typeof raw.price === 'number' ? raw.price : Number(raw.price);
  if (!time || !barKey || barKey.length > 64) return null;
  if (!Number.isFinite(price) || price <= 0 || price > PRICE_MAX) return null;
  return { time, barKey, price };
}

function parseStyle(raw: unknown): DrawingStyle | null {
  if (!isRecord(raw)) return null;
  const extra = Object.keys(raw).filter((key) => !['color', 'width', 'dash', 'fillOpacity'].includes(key));
  if (extra.length) return null;
  if (!isAllowedColor(raw.color)) return null;
  if (!WIDTHS.has(raw.width as number)) return null;
  if (typeof raw.dash !== 'string' || !DASHES.has(raw.dash)) return null;
  const style: DrawingStyle = {
    color: normalizeColor(String(raw.color)),
    width: raw.width as DrawingStyle['width'],
    dash: raw.dash as DrawingStyle['dash'],
  };
  if (raw.fillOpacity !== undefined) {
    const opacity = Number(raw.fillOpacity);
    if (!Number.isFinite(opacity) || opacity < 0 || opacity > 1) return null;
    style.fillOpacity = opacity;
  }
  return style;
}

const DRAWING_KEYS = new Set([
  'schemaVersion',
  'id',
  'ticker',
  'range',
  'adjustment',
  'kind',
  'anchors',
  'style',
  'text',
  'locked',
  'hidden',
  'zOrder',
  'revision',
  'createdAt',
  'updatedAt',
]);

export function parseDrawing(raw: unknown): ChartDrawing | null {
  if (!isRecord(raw)) return null;
  if ('option' in raw || 'graphic' in raw || 'css' in raw) return null;
  for (const key of Object.keys(raw)) {
    if (!DRAWING_KEYS.has(key)) return null;
  }
  if (raw.schemaVersion !== SCHEMA_VERSION && raw.schemaVersion !== undefined) return null;
  if (typeof raw.id !== 'string' || !UUID_RE.test(raw.id)) return null;
  if (typeof raw.ticker !== 'string' || !raw.ticker.trim()) return null;
  if (typeof raw.range !== 'string' || !CHART_RANGES.includes(raw.range as ChartDrawing['range'])) return null;
  if (raw.adjustment !== undefined && raw.adjustment !== 'raw') return null;
  if (typeof raw.kind !== 'string' || !DRAWING_KINDS.includes(raw.kind as DrawingKind)) return null;
  const kind = raw.kind as DrawingKind;
  if (!Array.isArray(raw.anchors) || raw.anchors.length !== ANCHOR_COUNTS[kind]) return null;
  const anchors = raw.anchors.map(parseAnchor);
  if (anchors.some((item) => item === null)) return null;
  const style = parseStyle(raw.style);
  if (!style) return null;
  let text: string | undefined;
  if (raw.text !== undefined && raw.text !== null) {
    if (typeof raw.text !== 'string') return null;
    if (raw.text.length > DRAWING_TEXT_MAX) return null;
    if (raw.text.includes('<') || raw.text.includes('>')) return null;
    if (kind === 'text' && !raw.text.trim()) return null;
    text = raw.text;
  } else if (kind === 'text') {
    return null;
  }
  const zOrder = raw.zOrder === undefined ? 0 : Number(raw.zOrder);
  if (!Number.isFinite(zOrder) || Math.abs(zOrder) > 1_000_000) return null;
  const revision = raw.revision === undefined ? 1 : Number(raw.revision);
  if (!Number.isInteger(revision) || revision < 1) return null;
  return {
    schemaVersion: 1,
    id: raw.id.toLowerCase(),
    ticker: raw.ticker.trim().toUpperCase(),
    range: raw.range as ChartDrawing['range'],
    adjustment: 'raw',
    kind,
    anchors: anchors as ChartDrawing['anchors'],
    style,
    text,
    locked: Boolean(raw.locked),
    hidden: Boolean(raw.hidden),
    zOrder,
    revision,
    createdAt: typeof raw.createdAt === 'string' ? raw.createdAt : new Date(0).toISOString(),
    updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : new Date(0).toISOString(),
  };
}

export function migrateStoredPayload(raw: unknown): SchemaOk<ChartDrawing[]> | SchemaError {
  if (raw == null) return { ok: true, value: [] };
  if (Array.isArray(raw)) {
    const drawings: ChartDrawing[] = [];
    for (const item of raw) {
      const parsed = parseDrawing(item);
      if (!parsed) return { ok: false, error: 'invalid_drawing' };
      drawings.push(parsed);
    }
    return { ok: true, value: drawings };
  }
  if (!isRecord(raw)) return { ok: false, error: 'corrupt' };
  const version = raw.schemaVersion ?? raw.version;
  if (version !== 1 && version !== undefined) {
    if (typeof version === 'number' && version < 1) {
      return { ok: false, error: 'unsupported_version' };
    }
    return { ok: false, error: 'unsupported_version' };
  }
  const list = raw.drawings;
  if (!Array.isArray(list)) return { ok: false, error: 'corrupt' };
  const drawings: ChartDrawing[] = [];
  for (const item of list) {
    const parsed = parseDrawing(item);
    if (!parsed) return { ok: false, error: 'invalid_drawing' };
    drawings.push(parsed);
  }
  return { ok: true, value: drawings };
}

export function validateImport(raw: unknown): SchemaOk<ChartDrawing[]> | SchemaError {
  const migrated = migrateStoredPayload(raw);
  if (!migrated.ok) return migrated;
  if (migrated.value.length > DRAWINGS_PER_RANGE_MAX) {
    return { ok: false, error: 'too_many' };
  }
  const size = JSON.stringify(raw).length;
  if (size > 512_000) return { ok: false, error: 'too_large' };
  return migrated;
}

export function exportDrawings(drawings: ChartDrawing[]): string {
  const payload = {
    schemaVersion: SCHEMA_VERSION,
    drawings: drawings.map((item) => ({
      schemaVersion: 1,
      id: item.id,
      ticker: item.ticker,
      range: item.range,
      adjustment: item.adjustment,
      kind: item.kind,
      anchors: item.anchors,
      style: item.style,
      text: item.text,
      locked: item.locked,
      hidden: item.hidden,
      zOrder: item.zOrder,
    })),
  };
  return JSON.stringify(payload, null, 2);
}

export function whitelistStyle(style: DrawingStyle): DrawingStyle | null {
  return parseStyle(style);
}

export function whitelistText(text: string): string | null {
  if (text.length > DRAWING_TEXT_MAX) return null;
  if (text.includes('<') || text.includes('>')) return null;
  if (!text.trim()) return null;
  return text;
}
