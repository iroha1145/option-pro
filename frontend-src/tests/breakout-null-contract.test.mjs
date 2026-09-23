import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import * as live from '../src/api/live.ts';

const here = path.dirname(new URL(import.meta.url).pathname);
const configPath = path.resolve(here, '../tsconfig.app.json');

test('the strict type contract rejects using missing radar data as measured values', () => {
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  assert.equal(config.error, undefined);
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(configPath));
  const fixture = path.resolve(here, 'fixtures/breakout-null-contract.ts');
  const program = ts.createProgram([fixture], { ...parsed.options, noEmit: true, incremental: false });
  const diagnostics = ts.getPreEmitDiagnostics(program);
  assert.deepEqual(diagnostics.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')), []);
});

const module = { exports: {} };
const mockEvent = {
  id: 'mock-history', event_id: 'mock-history', ticker: 'AEHR', name: 'AEHR',
  type: 'breakout', label: '突破', at: '2026-09-22T12:00:00Z',
  event_at: '2026-09-22T12:00:00Z', setup_type: 'DAILY_BASE_BREAKOUT',
  lifecycle_state: 'CONFIRMED', result: 'hit', price: 84.51, event_price: 84.51,
  current_price: 90, versions: { mock: 1 }, warnings: ['mock warning'],
};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/api/modules/breakouts.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, {
  module,
  exports: module.exports,
  require: id => id === '../live' ? live
    : id === '../macroFields' ? { mapMacroFitDrivers: () => [] }
      : id === '@/lib/signalLabels' ? { SIGNAL_LABELS: { breakout: '突破' } }
        : id === '../client' ? { mockOr: mock => Promise.resolve(mock()) }
          : id === '@/mocks/fixtures2' ? { getBreakoutsByTicker: () => [mockEvent] }
        : {},
});
const { normalizeBreakoutEvent, normalizeBreakoutStatus, breakoutsApi } = module.exports;

test('live radar normalization preserves absent prices, scores, zones, and unknown worker health', () => {
  const event = normalizeBreakoutEvent({
    event_id: 'partial-orb', ticker: 'AEHR', setup_type: 'OPENING_RANGE_BREAKOUT',
    event_price: null, current_price: null, intrinsic_strength_score: null,
    support_zone: { low: 84.51, high: null }, resistance_zone: null,
    pivot_price: 84.51, session_change_pct: 0,
  });
  assert.equal(event.price, null);
  assert.equal(event.current_price, null);
  assert.equal(event.triggerPrice, null);
  assert.equal(event.intrinsic_strength_score, null);
  assert.equal(event.support_zone, null);
  assert.equal(event.resistance_zone, null);
  assert.equal(event.pivot_price, 84.51);
  assert.equal(event.session_change_pct, 0);
  assert.equal(event.changePct, 0);
  assert.equal(normalizeBreakoutStatus({ worker: null }).worker.healthy, null);
});

test('mock history keeps its trigger display price and original extension fields', async () => {
  const [event] = await breakoutsApi.byTicker('AEHR');
  assert.equal(event.price, 84.51);
  assert.equal(event.current_price, 90);
  assert.equal(event.event_price, 84.51);
  assert.equal(event.versions, mockEvent.versions);
  assert.equal(event.warnings, mockEvent.warnings);
});
