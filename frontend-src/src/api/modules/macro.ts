/**
 * 宏观环境域（Optix 宏观环境 · Optix Macro Conditions）
 * GET  /api/macro/conditions
 * GET  /api/macro/conditions/history?days=
 * GET  /api/macro/conditions/modules/{module_id}
 * GET  /api/macro/conditions/factors/{factor_id}/history?days=
 * POST /api/macro/conditions/refresh（Owner）
 *
 * 归一原则（同 api/live.ts §0.5「不造假」）：
 * - 后端 null 一律保持 null，绝不冒充 0；
 * - 未知枚举安全回落，不臆造状态；
 * - 组件只消费本文件导出的类型，不接触后端原始 JSON。
 *
 * 分数含义：过去 5 年滚动历史分位，不是预测概率。
 */
import { get, mockOr, post, toQuery } from '../client';
import { asRec, pickB, pickN, pickS, unwrap } from '../live';
import { MACRO_MODULE_ORDER, type MacroModuleId } from '@/lib/macroModules';
import * as macroMock from '@/mocks/macro';

/* 模块标识与顺序住在 lib/macroModules.ts（叶子模块），避免与 mocks/macro.ts
   形成 ESM 循环；此处按既有 import 站点原样再导出。 */
export { MACRO_MODULE_ORDER };
export type { MacroModuleId };

/* ---------------------------------- 类型 ---------------------------------- */

export type MacroConditionsStatus =
  | 'active'
  | 'degraded'
  | 'stale'
  | 'unavailable'
  | 'disabled'
  | 'insufficient_history';

const MACRO_STATUSES: readonly MacroConditionsStatus[] = [
  'active',
  'degraded',
  'stale',
  'unavailable',
  'disabled',
  'insufficient_history',
];

export type MacroHistoryBasis = 'latest_revised_backfill' | 'local_point_in_time' | 'mixed';

export type MacroFactorStatus = 'ok' | 'stale' | 'missing' | 'insufficient_history';

export interface MacroUnit {
  unit: string;
  symbolZh: string;
  decimals: number;
}

export interface MacroComposite {
  score: number | null;
  scoreChange7d: number | null;
  confidence: number | null;
  regime: string | null;
  validModuleCount: number | null;
  totalModuleCount: number | null;
  snapshotDate: string | null;
  formattedScore: string | null;
}

export interface MacroModule {
  moduleId: MacroModuleId;
  nameZh: string;
  nameEn: string;
  score: number | null;
  scoreChange7d: number | null;
  confidence: number | null;
  validFactorCount: number | null;
  totalFactorCount: number | null;
  minimumValidFactors: number | null;
  dataThrough: string | null;
  status: string | null;
}

export interface MacroFactor {
  factorId: string;
  moduleId: string;
  nameZh: string;
  descriptionZh: string;
  formulaVersion: string;
  rawValue: number | null;
  formattedValue: string | null;
  signedValue: number | null;
  formattedSignedValue: string | null;
  unit: MacroUnit;
  score: number | null;
  scoreMethod: string | null;
  direction: string | null;
  rawChange7d: number | null;
  formattedRawChange7d: string | null;
  scoreChange7d: number | null;
  confidence: number | null;
  validObservations: number | null;
  minimumHistory: number | null;
  status: MacroFactorStatus;
  dataThrough: string | null;
  historyBasis: MacroHistoryBasis | null;
  missingInputs: string[];
  staleInputs: string[];
  sources: string[];
}

export interface MacroDriver {
  factorId: string;
  moduleId: string;
  nameZh: string;
  score: number | null;
  scoreChange7d: number | null;
}

export interface MacroHistoryPoint {
  date: string;
  score: number | null;
  confidence: number | null;
  regime: string | null;
  historyBasis: MacroHistoryBasis | null;
  moduleScores: Partial<Record<MacroModuleId, number | null>>;
}

export interface MacroConditionsResponse {
  status: MacroConditionsStatus;
  reason: string | null;
  asOf: string | null;
  dataThrough: string | null;
  scoringVersion: string | null;
  historyBasis: MacroHistoryBasis | null;
  composite: MacroComposite | null;
  modules: MacroModule[];
  drivers: { improving: MacroDriver[]; deteriorating: MacroDriver[] };
  warnings: string[];
  sources: string[];
}

export interface MacroHistoryResponse {
  status: string;
  days: number | null;
  points: MacroHistoryPoint[];
}

export interface MacroModuleDetail {
  status: string;
  moduleId: string;
  nameZh: string;
  nameEn: string;
  snapshotDate: string | null;
  module: MacroModule | null;
  factors: MacroFactor[];
}

export interface MacroFactorHistoryPoint {
  date: string;
  rawValue: number | null;
  signedValue: number | null;
  score: number | null;
  status: string | null;
  dataThrough: string | null;
  historyBasis: MacroHistoryBasis | null;
}

export interface MacroRefreshResult {
  requestId: string | null;
  status: string | null;
  reason: string | null;
  reused: boolean;
  cooldownUntil: string | null;
  cooldownSeconds: number | null;
  errorCode: string | null;
}

/* --------------------------------- 归一化 --------------------------------- */

function mapStatus(value: string | null): MacroConditionsStatus {
  /* 未知状态不臆造为 active：按「无法读取」处理。 */
  return MACRO_STATUSES.includes(value as MacroConditionsStatus)
    ? (value as MacroConditionsStatus)
    : 'unavailable';
}

const HISTORY_BASES: readonly MacroHistoryBasis[] = [
  'latest_revised_backfill',
  'local_point_in_time',
  'mixed',
];

function mapHistoryBasis(value: string | null): MacroHistoryBasis | null {
  return HISTORY_BASES.includes(value as MacroHistoryBasis)
    ? (value as MacroHistoryBasis)
    : null;
}

const FACTOR_STATUSES: readonly MacroFactorStatus[] = [
  'ok',
  'stale',
  'missing',
  'insufficient_history',
];

function mapFactorStatus(value: string | null): MacroFactorStatus {
  return FACTOR_STATUSES.includes(value as MacroFactorStatus)
    ? (value as MacroFactorStatus)
    : 'missing';
}

function mapUnit(value: unknown): MacroUnit {
  const r = asRec(value);
  return {
    unit: pickS(r, 'unit') ?? 'ratio',
    symbolZh: pickS(r, 'symbol_zh') ?? '',
    decimals: pickN(r, 'decimals') ?? 2,
  };
}

function mapStrings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.length > 0)
    : [];
}

function isModuleId(value: string | null): value is MacroModuleId {
  return MACRO_MODULE_ORDER.includes(value as MacroModuleId);
}

function mapComposite(value: unknown): MacroComposite | null {
  if (value === null || value === undefined) return null;
  const r = asRec(value);
  return {
    score: pickN(r, 'score'),
    scoreChange7d: pickN(r, 'score_change_7d'),
    confidence: pickN(r, 'confidence'),
    regime: pickS(r, 'regime'),
    validModuleCount: pickN(r, 'valid_module_count'),
    totalModuleCount: pickN(r, 'total_module_count'),
    snapshotDate: pickS(r, 'snapshot_date'),
    formattedScore: pickS(r, 'formatted_score'),
  };
}

function mapModule(value: unknown): MacroModule | null {
  const r = asRec(value);
  const moduleId = pickS(r, 'module_id');
  if (!isModuleId(moduleId)) return null;
  return {
    moduleId,
    nameZh: pickS(r, 'display_name_zh') ?? moduleId,
    nameEn: pickS(r, 'display_name_en') ?? '',
    score: pickN(r, 'score'),
    scoreChange7d: pickN(r, 'score_change_7d'),
    confidence: pickN(r, 'confidence'),
    validFactorCount: pickN(r, 'valid_factor_count'),
    totalFactorCount: pickN(r, 'total_factor_count'),
    minimumValidFactors: pickN(r, 'minimum_valid_factors'),
    dataThrough: pickS(r, 'data_through'),
    status: pickS(r, 'status'),
  };
}

function mapFactor(value: unknown): MacroFactor | null {
  const r = asRec(value);
  const factorId = pickS(r, 'factor_id');
  if (!factorId) return null;
  return {
    factorId,
    moduleId: pickS(r, 'module_id') ?? '',
    nameZh: pickS(r, 'display_name_zh') ?? factorId,
    descriptionZh: pickS(r, 'description_zh') ?? '',
    formulaVersion: pickS(r, 'formula_version') ?? '',
    rawValue: pickN(r, 'raw_value'),
    formattedValue: pickS(r, 'formatted_value'),
    signedValue: pickN(r, 'signed_value'),
    formattedSignedValue: pickS(r, 'formatted_signed_value'),
    unit: mapUnit(r.unit),
    score: pickN(r, 'score'),
    scoreMethod: pickS(r, 'score_method'),
    direction: pickS(r, 'direction'),
    rawChange7d: pickN(r, 'raw_change_7d'),
    formattedRawChange7d: pickS(r, 'formatted_raw_change_7d'),
    scoreChange7d: pickN(r, 'score_change_7d'),
    confidence: pickN(r, 'confidence'),
    validObservations: pickN(r, 'valid_observations'),
    minimumHistory: pickN(r, 'minimum_history'),
    status: mapFactorStatus(pickS(r, 'status')),
    dataThrough: pickS(r, 'data_through'),
    historyBasis: mapHistoryBasis(pickS(r, 'history_basis')),
    missingInputs: mapStrings(r.missing_inputs),
    staleInputs: mapStrings(r.stale_inputs),
    sources: mapStrings(r.source),
  };
}

function mapDriver(value: unknown): MacroDriver | null {
  const r = asRec(value);
  const factorId = pickS(r, 'factor_id');
  if (!factorId) return null;
  return {
    factorId,
    moduleId: pickS(r, 'module_id') ?? '',
    nameZh: pickS(r, 'display_name_zh') ?? factorId,
    score: pickN(r, 'score'),
    scoreChange7d: pickN(r, 'score_change_7d'),
  };
}

export function mapConditions(body: unknown): MacroConditionsResponse {
  const r = asRec(body);
  const drivers = asRec(r.drivers);
  return {
    status: mapStatus(pickS(r, 'status')),
    reason: pickS(r, 'reason'),
    asOf: pickS(r, 'as_of'),
    dataThrough: pickS(r, 'data_through'),
    scoringVersion: pickS(r, 'scoring_version'),
    historyBasis: mapHistoryBasis(pickS(r, 'history_basis')),
    composite: mapComposite(r.composite),
    modules: unwrap(r.modules, 'modules')
      .map(mapModule)
      .filter((item): item is MacroModule => item !== null)
      .sort(
        (a, b) =>
          MACRO_MODULE_ORDER.indexOf(a.moduleId) - MACRO_MODULE_ORDER.indexOf(b.moduleId),
      ),
    drivers: {
      improving: unwrap(drivers.improving, 'improving')
        .map(mapDriver)
        .filter((item): item is MacroDriver => item !== null),
      deteriorating: unwrap(drivers.deteriorating, 'deteriorating')
        .map(mapDriver)
        .filter((item): item is MacroDriver => item !== null),
    },
    warnings: mapStrings(r.warnings),
    sources: mapStrings(r.sources),
  };
}

export function mapHistory(body: unknown): MacroHistoryResponse {
  const r = asRec(body);
  return {
    status: pickS(r, 'status') ?? 'unavailable',
    days: pickN(r, 'days'),
    points: unwrap(r.points, 'points').flatMap((row) => {
      const date = pickS(row, 'date');
      if (!date) return [];
      const scores = asRec(row.module_scores);
      const moduleScores: Partial<Record<MacroModuleId, number | null>> = {};
      for (const key of MACRO_MODULE_ORDER) {
        if (key in scores) moduleScores[key] = pickN(scores, key);
      }
      return [
        {
          date,
          score: pickN(row, 'score'),
          confidence: pickN(row, 'confidence'),
          regime: pickS(row, 'regime'),
          historyBasis: mapHistoryBasis(pickS(row, 'history_basis')),
          moduleScores,
        },
      ];
    }),
  };
}

export function mapModuleDetail(body: unknown): MacroModuleDetail {
  const r = asRec(body);
  return {
    status: pickS(r, 'status') ?? 'unavailable',
    moduleId: pickS(r, 'module_id') ?? '',
    nameZh: pickS(r, 'display_name_zh') ?? '',
    nameEn: pickS(r, 'display_name_en') ?? '',
    snapshotDate: pickS(r, 'snapshot_date'),
    module: mapModule(r.module),
    factors: unwrap(r.factors, 'factors')
      .map(mapFactor)
      .filter((item): item is MacroFactor => item !== null),
  };
}

export function mapRefresh(body: unknown): MacroRefreshResult {
  const r = asRec(body);
  return {
    requestId: pickS(r, 'request_id'),
    status: pickS(r, 'status'),
    reason: pickS(r, 'reason'),
    reused: pickB(r, 'reused') ?? false,
    cooldownUntil: pickS(r, 'cooldown_until'),
    cooldownSeconds: pickN(r, 'cooldown_seconds'),
    errorCode: pickS(r, 'error_code'),
  };
}

/* ---------------------------------- API ---------------------------------- */

export const macroApi = {
  conditions: (): Promise<MacroConditionsResponse> =>
    mockOr(
      () => macroMock.getMacroConditions(),
      () => get('/macro/conditions').then(mapConditions),
    ),
  history: (days = 365): Promise<MacroHistoryResponse> =>
    mockOr(
      () => macroMock.getMacroHistory(days),
      () => get(`/macro/conditions/history?${toQuery({ days })}`).then(mapHistory),
    ),
  module: (moduleId: MacroModuleId): Promise<MacroModuleDetail> =>
    mockOr(
      () => macroMock.getMacroModuleDetail(moduleId),
      () => get(`/macro/conditions/modules/${moduleId}`).then(mapModuleDetail),
    ),
  refresh: (idempotencyKey?: string): Promise<MacroRefreshResult> =>
    mockOr(
      () => macroMock.refreshMacroConditions(),
      () =>
        post('/macro/conditions/refresh', idempotencyKey ? { idempotency_key: idempotencyKey } : {}).then(
          mapRefresh,
        ),
    ),
};
