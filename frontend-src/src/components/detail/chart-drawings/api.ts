/** Server drawings client. Guests never call this; anonymous data stays local. */
import { ApiError, del, get, post, put, toQuery } from '@/api/client';
import { parseDrawing } from './schema.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';

export interface DrawingListResponse {
  drawings: ChartDrawing[];
  maxPerRange: number;
}

function parseList(body: unknown): DrawingListResponse {
  const row = body && typeof body === 'object' ? (body as Record<string, unknown>) : {};
  const raw = Array.isArray(row.drawings) ? row.drawings : [];
  const drawings = raw.map(parseDrawing).filter((item): item is ChartDrawing => item !== null);
  const maxRaw = Number(row.max_per_range ?? row.maxPerRange);
  return {
    drawings,
    maxPerRange: Number.isFinite(maxRaw) && maxRaw > 0 ? maxRaw : 500,
  };
}

function scopeQuery(ticker: string, range: ChartRange, adjustment: ChartAdjustment) {
  return toQuery({ ticker, range, adjustment });
}

export const drawingsApi = {
  list: (ticker: string, range: ChartRange, adjustment: ChartAdjustment = 'raw') =>
    get<unknown>(`/account/chart-drawings?${scopeQuery(ticker, range, adjustment)}`).then(parseList),

  create: (drawing: ChartDrawing) =>
    post<unknown>('/account/chart-drawings', {
      schemaVersion: 1,
      id: drawing.id,
      ticker: drawing.ticker,
      range: drawing.range,
      adjustment: drawing.adjustment,
      kind: drawing.kind,
      anchors: drawing.anchors,
      style: drawing.style,
      text: drawing.text ?? null,
      locked: drawing.locked,
      hidden: drawing.hidden,
      zOrder: drawing.zOrder,
    }).then((body) => parseDrawing(body)),

  update: (drawing: ChartDrawing) =>
    put<unknown>(`/account/chart-drawings/${encodeURIComponent(drawing.id)}`, {
      schemaVersion: 1,
      id: drawing.id,
      revision: drawing.revision,
      ticker: drawing.ticker,
      range: drawing.range,
      adjustment: drawing.adjustment,
      kind: drawing.kind,
      anchors: drawing.anchors,
      style: drawing.style,
      text: drawing.text ?? null,
      locked: drawing.locked,
      hidden: drawing.hidden,
      zOrder: drawing.zOrder,
    }).then((body) => parseDrawing(body)),

  remove: (drawingId: string) =>
    del<unknown>(`/account/chart-drawings/${encodeURIComponent(drawingId)}`),

  clearScope: (ticker: string, range: ChartRange, adjustment: ChartAdjustment = 'raw') =>
    del<unknown>(`/account/chart-drawings?${scopeQuery(ticker, range, adjustment)}`),

  replaceScope: (
    ticker: string,
    range: ChartRange,
    drawings: ChartDrawing[],
    adjustment: ChartAdjustment = 'raw',
  ) =>
    post<unknown>(`/account/chart-drawings/replace?${scopeQuery(ticker, range, adjustment)}`, {
      schemaVersion: 1,
      drawings: drawings.map((drawing) => ({
        schemaVersion: 1,
        id: drawing.id,
        ticker: drawing.ticker,
        range: drawing.range,
        adjustment: drawing.adjustment,
        kind: drawing.kind,
        anchors: drawing.anchors,
        style: drawing.style,
        text: drawing.text ?? null,
        locked: drawing.locked,
        hidden: drawing.hidden,
        zOrder: drawing.zOrder,
      })),
    }).then(parseList),
};

export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.code === 401;
}

/** 后端业务码；没有 body 或不是 ApiError 时为 null。 */
export function drawingErrorCode(error: unknown): string | null {
  return error instanceof ApiError && error.bizCode ? error.bizCode : null;
}

export function drawingErrorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.code : null;
}

/**
 * 409 不等于版本冲突：配额满（drawings_range_full / drawings_full）和重放
 * （drawing_exists）也走 409，按冲突处理会弹「另一台设备改过」并让必然失败的
 * 任务无限重放。只有 revision_conflict 是真冲突。
 */
export function isConflictError(error: unknown): boolean {
  return drawingErrorCode(error) === 'revision_conflict';
}

export const QUOTA_ERROR_CODES = new Set(['drawings_range_full', 'drawings_full']);

export function isQuotaError(error: unknown): boolean {
  const code = drawingErrorCode(error);
  return code !== null && QUOTA_ERROR_CODES.has(code);
}
