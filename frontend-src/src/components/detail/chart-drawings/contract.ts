/** Fail-closed server drawing JSON. Local cache may migrate; this path must not. */
import { parseDrawing } from './schema.ts';
import type { ChartDrawing } from './types.ts';

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

export function parseList(body: unknown): DrawingListResponse {
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

export function parseSaved(body: unknown): { drawing: ChartDrawing; scopeRevision: number } {
  const drawing = parseDrawing(body);
  if (!drawing) throw new DrawingContractError('invalid_drawing');
  if (!isRecord(body)) throw new DrawingContractError('invalid_drawing');
  return { drawing, scopeRevision: parseScopeRevision(body) };
}

export function parseMutation(body: unknown): { scopeRevision: number } {
  if (!isRecord(body)) throw new DrawingContractError('invalid_mutation');
  return { scopeRevision: parseScopeRevision(body) };
}
