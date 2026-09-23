import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as flow from '../src/lib/screenerScanFlow.ts';
import { ApiError } from '../src/api/client.ts';

const parameters = {
  universe: 'all_market', timeframe: 'mid', profile: 'balanced', top: 20,
  sector_id: null, min_price: 5, min_avg_dollar_volume: 10_000_000,
  include_options: true, ranking_algorithm: 'eod_limited_v1',
};
const completedAt = '2026-09-04T21:00:00Z';
const completed = { requestId: 'new', status: 'completed', details: {
  parameters, result: { completed_at: completedAt, score_version: 'v2', published: true },
} };

test('runtime forwards real queued/running progress and stops polling a superseded scan', async () => {
  const runtimeSource = fs.readFileSync(new URL('../src/api/modules/runtime.ts', import.meta.url), 'utf8');
  const exports = {};
  const context = {
    exports, Date, Promise, DOMException,
    window: { setTimeout: (cb) => { queueMicrotask(cb); return 1; } },
    require: (name) => {
      if (name.includes('client')) return { ApiError };
      if (name.includes('screenerScanFlow')) return flow;
      return {};
    },
  };
  vm.runInNewContext(ts.transpileModule(runtimeSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const api = exports.runtimeApi;
  const statuses = ['queued', 'running', 'completed'];
  let polls = 0;
  const phases = [];
  api.workerActionStatus = async () => ({ ...completed, status: statuses[polls++] });
  await api.waitForWorkerAction('task', 5000, { onProgress: (action) => phases.push(action.status) });
  assert.deepEqual(phases, statuses);
  let current = true;
  api.workerActionStatus = async () => { polls++; return { ...completed, status: 'running' }; };
  await assert.rejects(api.waitForWorkerAction('task', 5000, { shouldContinue: () => current, onProgress: () => { current = false; } }), (error) => error.name === 'AbortError');
  assert.equal(polls, 4);
});
