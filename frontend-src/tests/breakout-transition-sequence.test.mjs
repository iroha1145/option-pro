import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as live from '../src/api/live.ts';

const module = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/api/modules/breakouts.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
  module, exports: module.exports,
  require: id => id === '../live' ? live : id === '../macroFields' ? { mapMacroFitDrivers: () => [] } : id === '@/lib/signalLabels' ? { SIGNAL_LABELS: {} } : {},
});
const { normalizeBreakoutTransitions, normalizeBreakoutEvent } = module.exports;

test('same-instant transitions retain causal sequence including a real lifecycle retreat', () => {
  const steps = [
    ['DISCOVERED', 'WATCHING'], ['WATCHING', 'TRIGGERED'], ['TRIGGERED', 'CONFIRMED'], ['CONFIRMED', 'RETESTING'],
  ].map(([from_state, to_state], i) => ({ from_state, to_state, evidence_at: i % 2 ? '2026-09-04T19:54:50.000+00:00' : '2026-09-04T19:54:50.000Z', transition_sequence: i + 1 }));
  const result = normalizeBreakoutTransitions([steps[2], steps[1], steps[3], steps[0]]);
  assert.deepEqual(Array.from(result, x => x.state), ['DISCOVERED', 'WATCHING', 'TRIGGERED', 'CONFIRMED', 'RETESTING']);
});

test('legacy transitions without sequence keep the authoritative backend order when times tie', () => {
  const result = normalizeBreakoutTransitions([
    { from_state: 'CONFIRMED', to_state: 'RETESTING', evidence_at: '2026-09-04T19:54:50Z' },
    { from_state: 'RETESTING', to_state: 'HOLDING', evidence_at: '2026-09-04T19:54:50Z' },
  ]);
  assert.deepEqual(Array.from(result, x => x.state), ['CONFIRMED', 'RETESTING', 'HOLDING']);
});

test('a partial opening-range anchor remains partial and cannot acquire an invented invalidation level', () => {
  const result = normalizeBreakoutEvent({ ticker: 'AEHR', setup_type: 'OPENING_RANGE_BREAKOUT', pivot_price: 84.51, invalidation_price: null, event_anchor: { kind: 'opening_range', status: 'partial' } });
  assert.equal(result.pivot_price, 84.51); assert.equal(result.invalidation_price, null);
  assert.equal(result.event_anchor.kind, 'opening_range'); assert.equal(result.event_anchor.status, 'partial');
});
