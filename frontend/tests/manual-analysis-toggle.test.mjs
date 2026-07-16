import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const here = path.dirname(fileURLToPath(import.meta.url));
const app = await readFile(
  path.resolve(here, '../static/js/deck-app.js'),
  'utf8',
);

function decisionFunction() {
  const source = app.match(
    /function manualAnalysisDecision\(documentState\) \{[\s\S]*?\n  \}/,
  );
  assert.ok(source, 'manual analysis decision helper must remain present');
  return vm.runInNewContext(`(${source[0].replace(/^function /, 'function ')})`);
}

test('the shared runtime switch fails closed with a visible Chinese reason', () => {
  const decide = decisionFunction();
  const enabled = decide({ settings: { ai: { manual_analysis_enabled: true } } });
  const disabled = decide({ settings: { ai: { manual_analysis_enabled: false } } });
  const unavailable = decide(null);

  assert.equal(enabled.enabled, true);
  assert.equal(disabled.enabled, false);
  assert.equal(disabled.title, '手动分析已关闭');
  assert.match(disabled.detail, /已有任务仍可查询和取消/);
  assert.equal(unavailable.enabled, false);
  assert.equal(unavailable.title, '手动分析暂不可用');
});

test('earnings and stock research buttons use the same runtime decision', () => {
  assert.match(app, /const manualControlRequest = loadManualAnalysisControl\(\)/);
  assert.match(app, /const manualControl = manualAnalysisDecision\(St\.runtimeSettings\)/);
  assert.match(app, /!retryBlocked && manualControl\.enabled \? `<button[^`]+data-impact-run/);
  assert.match(app, /!retryBlocked && !manualControl\.enabled \? `<small class="mono">/);
  assert.match(app, /manualControl\.enabled \? '<button class="btn btn--amber btn--sm" id="ai-run"/);
  assert.match(app, /manualControl\.title[\s\S]*manualControl\.detail/);
  assert.doesNotMatch(app, /id="ai-run"[^>]*disabled/);
  assert.match(app, /const currentControl = await loadManualAnalysisControl\(\)/);
  assert.match(app, /error\.code === "manual_analysis_disabled"/);
  assert.ok((app.match(/手动分析已关闭/g) || []).length >= 2);
});
