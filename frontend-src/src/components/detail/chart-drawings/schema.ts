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
export const NAMED_PAINT: Record<string, string> = {
  brand: '#2E46E0',
  up: '#0E9F6E',
  down: '#E5484D',
  ink: '#3D4A68',
  warn: '#E8930C',
  ai: '#0B7285',
};
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
  ...Object.keys(NAMED_PAINT),
]);

export function resolvePaintColor(value: string): string {
  if (NAMED_PAINT[value]) return NAMED_PAINT[value];
  if (HEX_COLOR.test(value)) return value.toUpperCase();
  return '#2E46E0';
}
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const WIDTHS = new Set([1, 2, 3, 4]);
const DASHES = new Set(['solid', 'dashed', 'dotted']);
/** 价格上下界：前后端同一套（price<=0 后端也判 invalid_price → 400）。 */
export const PRICE_MAX = 10_000_000;
export const PRICE_MIN = 1e-6;

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
  // RFC 3339：必须带 Z 或显式偏移。naive 本地时间不能按浏览器时区解析。
  if (!/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const text = value.endsWith('Z') || value.endsWith('z') ? value.slice(0, -1) + '+00:00' : value;
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
  if ([...barKey].some((ch) => ch.charCodeAt(0) < 32)) return null;
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


export function parseDrawingDetailed(raw: unknown): SchemaOk<ChartDrawing> | SchemaError {
  if (!isRecord(raw)) return { ok: false, error: 'invalid_drawing' };
  if ('option' in raw || 'graphic' in raw || 'css' in raw) return { ok: false, error: 'invalid_drawing' };
  if (raw.schemaVersion !== SCHEMA_VERSION && raw.schemaVersion !== undefined) {
    return { ok: false, error: 'unsupported_version' };
  }
  if (typeof raw.id !== 'string' || !UUID_RE.test(raw.id)) return { ok: false, error: 'invalid_drawing' };
  if (typeof raw.ticker !== 'string' || !raw.ticker.trim()) return { ok: false, error: 'invalid_drawing' };
  if (typeof raw.range !== 'string' || !CHART_RANGES.includes(raw.range as ChartDrawing['range'])) {
    return { ok: false, error: 'invalid_drawing' };
  }
  if (raw.adjustment !== undefined && raw.adjustment !== 'raw') return { ok: false, error: 'invalid_drawing' };
  if (typeof raw.kind !== 'string' || !DRAWING_KINDS.includes(raw.kind as DrawingKind)) {
    return { ok: false, error: 'invalid_drawing' };
  }
  const kind = raw.kind as DrawingKind;
  if (!Array.isArray(raw.anchors) || raw.anchors.length !== ANCHOR_COUNTS[kind]) {
    return { ok: false, error: 'invalid_drawing' };
  }
  const anchors = raw.anchors.map(parseAnchor);
  if (anchors.some((item) => item === null)) return { ok: false, error: 'invalid_drawing' };
  const style = parseStyle(raw.style);
  if (!style) return { ok: false, error: 'invalid_drawing' };
  let text: string | undefined;
  if (raw.text !== undefined && raw.text !== null) {
    if (typeof raw.text !== 'string') return { ok: false, error: 'illegal_text' };
    if (raw.text.length > DRAWING_TEXT_MAX) return { ok: false, error: 'illegal_text' };
    if (raw.text.includes('<') || raw.text.includes('>') || raw.text.includes('\0')) {
      return { ok: false, error: 'illegal_text' };
    }
    text = raw.text;
  } else if (kind === 'text') {
    text = '';
  }
  if ('locked' in raw && typeof raw.locked !== 'boolean') return { ok: false, error: 'invalid_boolean' };
  if ('hidden' in raw && typeof raw.hidden !== 'boolean') return { ok: false, error: 'invalid_boolean' };
  const zOrderRaw = raw.zOrder === undefined ? 0 : Number(raw.zOrder);
  if (!Number.isFinite(zOrderRaw) || Math.abs(zOrderRaw) > 1_000_000) return { ok: false, error: 'invalid_drawing' };
  const zOrder = Math.trunc(zOrderRaw);
  const revision = raw.revision === undefined ? 1 : Number(raw.revision);
  if (!Number.isInteger(revision) || revision < 1) return { ok: false, error: 'invalid_drawing' };
  return {
    ok: true,
    value: {
      schemaVersion: 1,
      id: raw.id.toLowerCase(),
      ticker: raw.ticker.trim().toUpperCase(),
      range: raw.range as ChartDrawing['range'],
      adjustment: 'raw',
      kind,
      anchors: anchors as ChartDrawing['anchors'],
      style,
      text,
      locked: raw.locked === true,
      hidden: raw.hidden === true,
      zOrder,
      revision,
      createdAt: typeof raw.createdAt === 'string' ? raw.createdAt : new Date(0).toISOString(),
      updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : new Date(0).toISOString(),
    },
  };
}

export function parseDrawing(raw: unknown): ChartDrawing | null {
  const parsed = parseDrawingDetailed(raw);
  return parsed.ok ? parsed.value : null;
}

export function migrateStoredPayload(raw: unknown): SchemaOk<ChartDrawing[]> | SchemaError {
  if (raw == null) return { ok: true, value: [] };
  if (Array.isArray(raw)) {
    const drawings: ChartDrawing[] = [];
    const seen = new Set<string>();
    for (const item of raw) {
      const parsed = parseDrawingDetailed(item);
      if (!parsed.ok) return parsed;
      if (seen.has(parsed.value.id)) return { ok: false, error: 'id_conflict' };
      seen.add(parsed.value.id);
      drawings.push(parsed.value);
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
  const seen = new Set<string>();
  for (const item of list) {
    const parsed = parseDrawingDetailed(item);
    if (!parsed.ok) return parsed;
    if (seen.has(parsed.value.id)) return { ok: false, error: 'id_conflict' };
    seen.add(parsed.value.id);
    drawings.push(parsed.value);
  }
  return { ok: true, value: drawings };
}

export interface CollectResult {
  drawings: ChartDrawing[];
  dropped: number;
  /** 整份 payload 都读不出来（不是记录/版本不认/drawings 不是数组）。 */
  fatal: string | null;
  error: string | null;
}

/**
 * 逐条校验的本地读取口径：一行坏掉只丢那一行，其余照常还给调用方。
 * migrateStoredPayload 的全量拒绝口径留给导入（导入必须整份对或整份错），
 * 本地存档用它会把仍能解析的图形一起判死，然后被空列表覆盖写回。
 */
export function collectStoredDrawings(raw: unknown): CollectResult {
  if (raw == null) return { drawings: [], dropped: 0, fatal: null, error: null };
  let list: unknown[];
  if (Array.isArray(raw)) {
    list = raw;
  } else if (!isRecord(raw)) {
    return { drawings: [], dropped: 0, fatal: 'corrupt', error: 'corrupt' };
  } else {
    const version = raw.schemaVersion ?? raw.version;
    if (version !== 1 && version !== undefined) {
      return { drawings: [], dropped: 0, fatal: 'unsupported_version', error: 'unsupported_version' };
    }
    if (!Array.isArray(raw.drawings)) {
      return { drawings: [], dropped: 0, fatal: 'corrupt', error: 'corrupt' };
    }
    list = raw.drawings;
  }
  const drawings: ChartDrawing[] = [];
  const seen = new Set<string>();
  let dropped = 0;
  let error: string | null = null;
  for (const item of list) {
    const parsed = parseDrawingDetailed(item);
    if (!parsed.ok) {
      dropped += 1;
      error = error ?? parsed.error;
      continue;
    }
    if (seen.has(parsed.value.id)) {
      dropped += 1;
      error = error ?? 'id_conflict';
      continue;
    }
    seen.add(parsed.value.id);
    drawings.push(parsed.value);
  }
  return { drawings, dropped, fatal: null, error };
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
  if (text.includes('<') || text.includes('>') || text.includes('\0')) return null;
  return text;
}
