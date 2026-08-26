/** Server drawings client. Guests never call this; anonymous data stays local. */
import { ApiError, del, get, post, put, toQuery } from '@/api/client';
import { parseDrawing } from './schema.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';

export interface DrawingListResponse {
  drawings: ChartDrawing[];
  maxPerRange: number;
  scopeRevision: number;
}

export class DrawingContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DrawingContractError';
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseScopeRevision(row: Record<string, unknown>): number {
  const raw = row.scope_revision ?? row.scopeRevision;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) {
    throw new DrawingContractError('invalid_scope_revision');
  }
  return value;
}

function parseList(body: unknown): DrawingListResponse {
  if (!isRecord(body)) throw new DrawingContractError('invalid_list');
  if (!Array.isArray(body.drawings)) throw new DrawingContractError('invalid_list');
  const drawings: ChartDrawing[] = [];
  for (const item of body.drawings) {
    const parsed = parseDrawing(item);
    if (!parsed) throw new DrawingContractError('invalid_drawing');
    drawings.push(parsed);
  }
  const maxRaw = Number(body.max_per_range ?? body.maxPerRange);
  return {
    drawings,
    maxPerRange: Number.isFinite(maxRaw) && maxRaw > 0 ? maxRaw : 500,
    scopeRevision: parseScopeRevision(body),
  };
}

function parseSaved(body: unknown): { drawing: ChartDrawing; scopeRevision: number } {
  const drawing = parseDrawing(body);
  if (!drawing) throw new DrawingContractError('invalid_drawing');
  if (!isRecord(body)) throw new DrawingContractError('invalid_drawing');
  return { drawing, scopeRevision: parseScopeRevision(body) };
}

function drawingBody(drawing: ChartDrawing) {
  return {
    schemaVersion: 1 as const,
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
  };
}

function scopeQuery(
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment,
  extra?: Record<string, string | number>,
) {
  return toQuery({ ticker, range, adjustment, ...extra });
}

export const drawingsApi = {
  list: (ticker: string, range: ChartRange, adjustment: ChartAdjustment = 'raw') =>
    get<unknown>(`/account/chart-drawings?${scopeQuery(ticker, range, adjustment)}`).then(parseList),

  create: (drawing: ChartDrawing, expectedScopeRevision: number) =>
    post<unknown>('/account/chart-drawings', {
      ...drawingBody(drawing),
      expected_scope_revision: expectedScopeRevision,
    }).then(parseSaved),

  update: (drawing: ChartDrawing, expectedScopeRevision: number) =>
    put<unknown>(`/account/chart-drawings/${encodeURIComponent(drawing.id)}`, {
      ...drawingBody(drawing),
      revision: drawing.revision,
      expected_scope_revision: expectedScopeRevision,
    }).then(parseSaved),

  remove: (drawingId: string, expectedScopeRevision: number) =>
    del<unknown>(
      `/account/chart-drawings/${encodeURIComponent(drawingId)}?${toQuery({ expected_scope_revision: expectedScopeRevision })}`,
    ),

  clearScope: (
    ticker: string,
    range: ChartRange,
    expectedScopeRevision: number,
    adjustment: ChartAdjustment = 'raw',
  ) =>
    del<unknown>(
      `/account/chart-drawings?${scopeQuery(ticker, range, adjustment, { expected_scope_revision: expectedScopeRevision })}`,
    ),

  replaceScope: (
    ticker: string,
    range: ChartRange,
    drawings: ChartDrawing[],
    expectedScopeRevision: number,
    adjustment: ChartAdjustment = 'raw',
  ) =>
    post<unknown>(`/account/chart-drawings/replace?${scopeQuery(ticker, range, adjustment)}`, {
      schemaVersion: 1,
      expected_scope_revision: expectedScopeRevision,
      drawings: drawings.map(drawingBody),
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
  const code = drawingErrorCode(error);
  return code === 'revision_conflict' || code === 'scope_revision_conflict' || code === 'drawing_id_conflict';
}

export const QUOTA_ERROR_CODES = new Set(['drawings_range_full', 'drawings_full']);

export function isQuotaError(error: unknown): boolean {
  const code = drawingErrorCode(error);
  return code !== null && QUOTA_ERROR_CODES.has(code);
}
