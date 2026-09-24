/** Server drawings client. Guests never call this; anonymous data stays local. */
import { ApiError, del, get, post, put, toQuery } from '@/api/client';
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';
import { parseList, parseMutation, parseSaved } from './contract.ts';

export { DrawingContractError, parseList, parseMutation, parseSaved, type DrawingListResponse } from './contract.ts';

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

  remove: (
    drawingId: string,
    expectedScopeRevision: number,
    ticker: string,
    range: ChartRange,
    adjustment: ChartAdjustment = 'raw',
  ) =>
    del<unknown>(
      `/account/chart-drawings/${encodeURIComponent(drawingId)}?${scopeQuery(ticker, range, adjustment, { expected_scope_revision: expectedScopeRevision })}`,
    ).then(parseMutation),

  clearScope: (
    ticker: string,
    range: ChartRange,
    expectedScopeRevision: number,
    adjustment: ChartAdjustment = 'raw',
  ) =>
    del<unknown>(
      `/account/chart-drawings?${scopeQuery(ticker, range, adjustment, { expected_scope_revision: expectedScopeRevision })}`,
    ).then(parseMutation),

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

/** 后端业务码；没有 body 或不是 ApiError 时为 null。 */
export function drawingErrorCode(error: unknown): string | null {
  return error instanceof ApiError && error.bizCode ? error.bizCode : null;
}

export function drawingErrorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.code : null;
}
