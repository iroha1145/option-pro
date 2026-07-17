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
    /function manualAnalysisDecision\(documentState, ownerAccess = true\) \{[\s\S]*?\n  \}/,
  );
  assert.ok(source, 'manual analysis decision helper must remain present');
  return vm.runInNewContext(`(${source[0].replace(/^function /, 'function ')})`);
}

function runtimeSettingsOrdering() {
  const versionSource = app.match(
    /function runtimeSettingsVersion\(documentState\) \{[\s\S]*?\n  \}/,
  );
  const decisionSource = app.match(
    /function runtimeSettingsDocumentDecision\(current, candidate\) \{[\s\S]*?\n  \}/,
  );
  assert.ok(versionSource, 'runtime settings version helper must remain present');
  assert.ok(decisionSource, 'runtime settings ordering helper must remain present');
  return vm.runInNewContext(`(() => {
    ${versionSource[0]}
    ${decisionSource[0]}
    return runtimeSettingsDocumentDecision;
  })()`);
}

function runtimeSettingsRecorder() {
  const versionSource = app.match(
    /function runtimeSettingsVersion\(documentState\) \{[\s\S]*?\n  \}/,
  );
  const decisionSource = app.match(
    /function runtimeSettingsDocumentDecision\(current, candidate\) \{[\s\S]*?\n  \}/,
  );
  const recordSource = app.match(
    /function recordRuntimeSettingsResponse\(requestOwnerSessionEpoch, documentState\) \{[\s\S]*?\n  \}/,
  );
  assert.ok(recordSource, 'runtime settings response recorder must remain present');
  return vm.runInNewContext(`(() => {
    const St = {
      ownerStatus: { logged_in: true },
      runtimeSettings: { version: 2, settings: { ai: { manual_analysis_enabled: false } } },
    };
    const ownerAccessEnabled = status => !!(status && status.logged_in === true);
    let ownerSessionEpoch = 4;
    let runtimeSettingsEpoch = 9;
    let runtimeSettingsPromise = {};
    ${versionSource[0]}
    ${decisionSource[0]}
    ${recordSource[0]}
    return {
      recordRuntimeSettingsResponse,
      state: () => ({
        ownerSessionEpoch,
        runtimeSettingsEpoch,
        runtimeSettingsPromise,
        runtimeSettings: St.runtimeSettings,
      }),
    };
  })()`);
}

test('the shared runtime switch fails closed and never enables a visitor', () => {
  const decide = decisionFunction();
  const runtimeEnabled = { settings: { ai: { manual_analysis_enabled: true } } };
  const enabledOwner = decide(runtimeEnabled, true);
  const disabledOwner = decide(
    { settings: { ai: { manual_analysis_enabled: false } } },
    true,
  );
  const unavailableOwner = decide(null, true);
  const visitor = decide(runtimeEnabled, false);

  assert.equal(enabledOwner.enabled, true);
  assert.equal(disabledOwner.enabled, false);
  assert.equal(disabledOwner.title, '手动分析已关闭');
  assert.match(disabledOwner.detail, /已有任务仍可查询和取消/);
  assert.equal(unavailableOwner.enabled, false);
  assert.equal(unavailableOwner.title, '手动分析暂不可用');
  assert.equal(visitor.enabled, false);
  assert.equal(visitor.title, '登录后可使用模型分析');
  assert.match(visitor.detail, /公开浏览不会创建、重试或取消模型任务/);
});

test('late runtime settings responses cannot overwrite a newer saved revision', () => {
  const decide = runtimeSettingsOrdering();
  const current = { version: 8, settings: { ai: { manual_analysis_enabled: false } } };
  assert.equal(decide(current, { version: 7, settings: {} }), 'reject');
  assert.equal(decide(current, { version: 8, settings: {} }), 'current');
  assert.equal(decide(current, { version: 9, settings: {} }), 'newer');
  assert.equal(decide(null, { version: 1, settings: {} }), 'newer');
  assert.equal(decide(current, null), 'reject');
});

test('a confirmed runtime settings response invalidates concurrent failures', () => {
  const recorder = runtimeSettingsRecorder();
  const failedRequestEpoch = recorder.state().runtimeSettingsEpoch;
  const changed = recorder.recordRuntimeSettingsResponse(
    4,
    { version: 3, settings: { ai: { manual_analysis_enabled: true } } },
  );
  const state = recorder.state();
  assert.equal(changed, true);
  assert.equal(state.runtimeSettings.version, 3);
  assert.equal(state.runtimeSettingsEpoch, failedRequestEpoch + 1);
  assert.equal(state.runtimeSettingsPromise, null);
  assert.notEqual(failedRequestEpoch, state.runtimeSettingsEpoch);

  assert.equal(recorder.recordRuntimeSettingsResponse(4, { version: 3, settings: {} }), false);
  assert.equal(recorder.state().runtimeSettingsEpoch, failedRequestEpoch + 2);
  assert.equal(recorder.recordRuntimeSettingsResponse(3, { version: 4, settings: {} }), false);
  assert.equal(recorder.state().runtimeSettings.version, 3);
});

test('earnings and stock research require both owner access and the runtime switch', () => {
  assert.match(app, /const ownerAccessEnabled = status => !!\(status && status\.logged_in === true\)/);
  assert.match(app, /const manualControlRequest = loadManualAnalysisControl\(\)/);
  assert.match(app, /const ownerStatus = await loadOwnerStatus\(false\)/);
  assert.match(app, /if \(!ownerAccessEnabled\(ownerStatus\)\) \{[\s\S]*return manualAnalysisDecision\(null, false\)/);
  assert.match(app, /const manualControl = manualAnalysisDecision\([\s\S]*St\.runtimeSettings,[\s\S]*ownerAccessEnabled\(St\.ownerStatus\)/);
  assert.match(app, /!retryBlocked && manualControl\.enabled \? `<button[^`]+data-impact-run/);
  assert.match(app, /!retryBlocked && !manualControl\.enabled \? `<small class="mono">/);
  assert.match(app, /manualControl\.enabled \? '<button class="btn btn--amber btn--sm" id="ai-run"/);
  assert.match(app, /manualControl\.title[\s\S]*manualControl\.detail/);
  assert.doesNotMatch(app, /id="ai-run"[^>]*disabled/);
  assert.match(app, /const manualControl = await loadManualAnalysisControl\(\)[\s\S]*if \(!manualControl\.enabled\) \{[\s\S]*return;/);
  assert.match(app, /const currentControl = await loadManualAnalysisControl\(\)[\s\S]*if \(!currentControl\.enabled\) \{[\s\S]*return;/);
  assert.match(app, /ownerAccessEnabled\(St\.ownerStatus\) \? '<button[^']+data-impact-cancel/);
  assert.match(app, /error\.code === "manual_analysis_disabled"/);
  assert.ok((app.match(/手动分析已关闭/g) || []).length >= 2);
});

test('only the owner sees and can operate the global analysis switch', () => {
  assert.match(app, /aiToggle\.hidden = !owner/);
  assert.match(app, /if \(!aiToggle \|\| !ownerAccessEnabled\(St\.ownerStatus\)\) return/);
  assert.match(app, /aiToggle\.addEventListener\("click", toggleOwnerAI\)/);
  assert.match(app, /ai: \{ manual_analysis_enabled: false \}[\s\S]*catalyst: \{ scheduled_analysis_enabled: false \}/);
  assert.match(app, /: \{ ai: \{ manual_analysis_enabled: true \} \}/);
  assert.match(app, /expected_version: Number\(current\.version\)/);
  assert.match(app, /N\.updateRuntimeSettings\(\{/);
  assert.match(app, /window\.addEventListener\("optix:runtime-settings-changed"[\s\S]*syncOwnerControls\(St\.runtimeSettings\)/);
  assert.match(app, /const requestEpoch = runtimeSettingsEpoch[\s\S]*requestOwnerSessionEpoch === ownerSessionEpoch[\s\S]*requestEpoch === runtimeSettingsEpoch/);
  assert.match(app, /function recordRuntimeSettingsResponse\(requestOwnerSessionEpoch, documentState\)[\s\S]*runtimeSettingsEpoch \+= 1/);
  assert.match(app, /const requestOwnerSessionEpoch = ownerSessionEpoch[\s\S]*requestOwnerSessionEpoch === ownerSessionEpoch/);
  assert.match(app, /const previousOwner = ownerAccessEnabled\(St\.ownerStatus\)[\s\S]*if \(previousOwner !== owner\) route\(\)/);
  assert.match(app, /statusRequest === ownerStatusRequest[\s\S]*requestOwnerSessionEpoch === ownerSessionEpoch/);
  assert.match(app, /await loadOwnerStatus\(true\);\s*if \(requestOwnerSessionEpoch !== ownerSessionEpoch\) return St\.ownerStatus/);
  assert.match(app, /const OWNER_SESSION_REFRESH_MS = 60e3/);
  assert.match(app, /setInterval\(\(\) => \{[\s\S]*refreshOwnerSessionState\(\)[\s\S]*OWNER_SESSION_REFRESH_MS/);
  assert.match(app, /document\.addEventListener\("visibilitychange"[\s\S]*refreshOwnerSessionState\(\)/);
  assert.match(app, /分析：开启/);
  assert.match(app, /分析：关闭/);
});
