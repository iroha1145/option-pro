import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const read = (relativePath) => readFile(path.join(frontend, relativePath), 'utf8');

const [index, api, jobs, catalysts, app, deckCss, catalystCss] = await Promise.all([
  read('index.html'),
  read('static/js/deck-api.js'),
  read('static/js/deck-ai-jobs.js'),
  read('static/js/deck-catalysts.js'),
  read('static/js/deck-app.js'),
  read('static/css/optix-deck.css'),
  read('static/css/optix-catalysts.css'),
]);

test('production shell exposes Catalyst Desk without crowding the five-item mobile dock', () => {
  assert.match(index, /href="#catalysts"\s+data-route="catalysts"[\s\S]*06[\s\S]*催化剂/);
  assert.match(index, /optix-catalysts\.css/);
  assert.match(index, /deck-api\.js[\s\S]*deck-ai-jobs\.js[\s\S]*deck-catalysts\.js[\s\S]*deck-app\.js/);
  const deckScriptVersions = [...index.matchAll(/static\/js\/deck-(?:api|ai-jobs|catalysts|app)\.js\?v=([^"&]+)/g)]
    .map((match) => match[1]);
  assert.equal(deckScriptVersions.length, 4);
  assert.equal(new Set(deckScriptVersions).size, 1);
  const dock = index.match(/<nav class="dock"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.equal((dock.match(/<a /g) || []).length, 5);
  assert.doesNotMatch(dock, /catalysts/);
  assert.match(deckCss, /\.deck-menu-toggle\.has-current-route::after/);
});

test('Catalyst data remains same-origin, abortable, cached by endpoint family, and isolated from job polling', () => {
  for (const endpoint of [
    '/api/catalysts/status', '/api/catalysts/feed', '/api/catalysts/news/',
    '/api/catalysts/tickers/', '/api/catalysts/calendar', '/api/catalysts/analysis-jobs/',
    '/api/catalysts/hotspots/status', '/api/catalysts/hotspots',
    '/api/catalysts/market-focus-cycles',
    '/api/ai/jobs/earnings-impact', '/api/ai/jobs/',
  ]) assert.match(api, new RegExp(endpoint.replaceAll('/', '\\/')));
  assert.match(api, /const normalGate = makeGate\(3\)/);
  assert.match(api, /const jobGate = makeGate\(2\)/);
  assert.match(api, /signal: opts\.signal/);
  assert.match(api, /credentials: "same-origin"/);
  assert.match(api, /redirect: "error"/);
  assert.match(api, /noCache: true, lowPriority: true/);
  assert.doesNotMatch(api, /(?:sessionStorage|localStorage).*optix\.app\.token/);
  assert.doesNotMatch(api, /Authorization|Bearer/);
  assert.match(api, /headers\["X-Optix-Action"\] = "1"/);
  assert.doesNotMatch(api, /https?:\/\//);
  assert.match(api, /const alreadyLoggedOut = !!\([\s\S]*if \(!alreadyLoggedOut\)/);
  assert.match(api, /const accessStatus = \(\) => \{[\s\S]*if \(accessStatusPromise\) return accessStatusPromise[\s\S]*requestEpoch === accessStateEpoch[\s\S]*accessStateEpoch \+= 1;\s*accessState = null/);
});

test('concurrent expired-session responses emit one logout event and access failures clear shared state', async () => {
  const events = [];
  let mode = 'logged-in';
  let releaseDelayedStatus;
  let markDelayedStatusStarted;
  const delayedStatusStarted = new Promise(resolve => { markDelayedStatusStarted = resolve; });
  const response = loggedIn => ({
    ok: loggedIn,
    status: loggedIn ? 200 : 401,
    headers: { get: () => null },
    json: async () => loggedIn
      ? { access_mode: 'password', logged_in: true }
      : { code: 'owner_login_required' },
  });
  class TestCustomEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options && options.detail;
    }
  }
  const window = { dispatchEvent: event => events.push(event) };
  const context = vm.createContext({
    window,
    fetch: async requestPath => {
      if (mode === 'network-error') throw new Error('offline');
      if (mode === 'delayed-status' && requestPath === '/api/access/status') {
        markDelayedStatusStarted();
        return new Promise(resolve => { releaseDelayedStatus = () => resolve(response(true)); });
      }
      const loggedIn = mode === 'logged-in';
      return response(loggedIn);
    },
    CustomEvent: TestCustomEvent,
    setTimeout,
    clearTimeout,
    DOMException,
    Date,
    Intl,
    Map,
    Math,
    Promise,
    encodeURIComponent,
    isFinite,
  });
  vm.runInContext(api, context, { filename: 'deck-api.js' });
  const net = window.OPTIX_NET;

  await net.accessStatus();
  assert.equal(net.currentAccessStatus().logged_in, true);
  mode = 'network-error';
  await assert.rejects(net.accessStatus(), /offline/);
  assert.equal(net.currentAccessStatus(), null);

  mode = 'logged-in';
  await net.accessStatus();
  mode = 'unauthorized';
  const results = await Promise.allSettled([
    net.runtimeSettings(),
    net.workerStatus(),
    net.runtimeSettingsHistory(),
  ]);
  assert.ok(results.every(result => result.status === 'rejected'));
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'optix:owner-session-changed');
  assert.equal(net.currentAccessStatus().logged_in, false);

  mode = 'logged-in';
  await net.accessStatus();
  mode = 'delayed-status';
  const staleStatus = net.accessStatus();
  await delayedStatusStarted;
  mode = 'unauthorized';
  await assert.rejects(net.runtimeSettings(), /HTTP 401/);
  releaseDelayedStatus();
  const staleResult = await staleStatus;
  assert.equal(staleResult.logged_in, false);
  assert.equal(net.currentAccessStatus().logged_in, false);
  assert.equal(events.length, 2);
});

test('paid analysis is explicit and earnings no longer uses the synchronous GET trigger', () => {
  assert.doesNotMatch(api, /hasAppToken|appToken/);
  assert.doesNotMatch(catalysts, /privateActionsAvailable|管理令牌|管理会话未解锁/);
  assert.doesNotMatch(app, /privateActionsAvailable|管理授权|公开页面不会创建付费任务/);
  assert.match(api, /jget, jpost, jput, invalidateCache, accessStatus, currentAccessStatus, logoutOwner/);
  assert.match(catalysts, /data-cat-refresh="news"/);
  assert.match(catalysts, /data-cat-refresh="calendar"/);
  assert.match(catalysts, /data-cat-refresh="source_health"/);
  assert.match(catalysts, /analysisAvailabilityOf\(item\)/);
  assert.match(catalysts, /analysisActionDecision\([\s\S]*triggerEnabled,[\s\S]*analysisAvailabilityOf\(item\),[\s\S]*page\.ownerAccess/);
  assert.doesNotMatch(catalysts, /analysisActionDecision\(triggerEnabled, true,/);
  assert.match(catalysts, /access\.showAction \? `<button type="button" class="btn btn--amber btn--sm" data-catalyst-analyze/);
  assert.equal((catalysts.match(/!isActive && access\.showAction/g) || []).length, 2);
  assert.match(catalysts, /运行设置恢复前不会创建模型任务/);
  assert.match(catalysts, /id="cat-focus-action"/);
  assert.match(catalysts, /actionHost\.innerHTML = '<button class="btn btn--amber" type="button" id="cat-focus-run" disabled/);
  assert.match(catalysts, /data-cat-analyze/);
  assert.match(catalysts, /createCatalystAnalysis/);
  assert.match(catalysts, /重新分析会创建新的分析版本/);
  assert.match(catalysts, /budgetPolicyText\(\)/);
  assert.match(catalysts, /manual_refreshes/);
  assert.match(catalysts, /刷新中 · \$\{elapsed\}秒/);
  assert.match(catalysts, /稍后可刷新 · \$\{Math\.max\(1, remaining\)\}秒/);
  assert.match(catalysts, /刷新服务暂不可用/);
  assert.match(api, /runtimeSettingsHistory/);
  assert.match(api, /updateRuntimeSettings/);
  assert.match(api, /rollbackRuntimeSettings/);
  assert.match(api, /workerStatus/);
  assert.match(api, /workerActions/);
  assert.match(api, /requestWorkerAction/);
  assert.match(catalysts, /每日系统预算（美元）/);
  assert.match(catalysts, /固定分析时刻（美东）/);
  assert.match(catalysts, /运行设置已保存并立即生效/);
  assert.ok((catalysts.match(/"optix:runtime-settings-changed"/g) || []).length >= 3);
  assert.match(catalysts, /if \(!page\.ownerAccess\) return;/);
  assert.match(catalysts, /async function resolveOwnerAccess\(\)[\s\S]*accessStatus\.logged_in === true/);
  assert.match(catalysts, /page\.ownerAccess = ownerAccess/);
  assert.match(catalysts, /page\.ownerAccess = await resolveOwnerAccess\(\)/);
  assert.match(catalysts, /focusCycleDecision\(raw, cycle, preparedCount, page\.ownerAccess\)/);
  assert.match(catalysts, /const generation = page\.generation;\s*const controller = page\.controller;/);
  assert.match(catalysts, /generation !== page\.generation[\s\S]*controller !== page\.controller/);
  assert.match(catalysts, /function leaveRoute\(\) \{[\s\S]*page\.runtimeSettingsRequest \+= 1/);
  for (const actionType of ['focus_refresh', 'strength_refresh', 'breakout_refresh', 'retention']) {
    assert.match(catalysts, new RegExp(`data-worker-action="${actionType}"`));
  }
  assert.match(catalysts, /function workerTaskFor\(actionType\) \{\s*return actionType;/);
  assert.match(catalysts, /return !!task && task\.enabled !== false;/);
  assert.match(catalysts, /今日模型用量/);
  assert.match(catalysts, /今日费用额度/);
  assert.match(catalysts, /后台工作进程暂不可用/);
  assert.doesNotMatch(catalysts, /\b(?:capability|action_enabled)\b/);
  assert.match(app, /data-impact-run/);
  assert.match(app, /createEarningsImpactJob/);
  assert.match(api, /createOptionAlertsJob/);
  assert.match(app, /N\.aiStock\(ticker, force, \{ signal \}\)/);
  assert.match(app, /error\.status === 409 \? "analysis_required" : "failed"/);
  assert.match(jobs, /error\.name === "AbortError"/);
  assert.doesNotMatch(app, /N\.earningsImpact\(/);
  assert.match(app, /选择行只会切换研究对象，不会调用模型/);
  assert.doesNotMatch(catalysts, /progress_percent|data-progress|aria-valuenow|进度\s*\d+%/i);
  assert.match(catalysts, /不显示估算进度/);
});

test('Strength Radar refresh runs through the unified worker and keeps old results on failure', () => {
  assert.match(app, /async function refreshStrengthSnapshot\(\) \{\s*if \(!ownerAccessEnabled\(St\.ownerStatus\)\) return;/);
  assert.match(app, /button\.addEventListener\("click", refreshStrengthSnapshot\)/);
  assert.match(api, /const workerAction = \(requestId, options\) => jget\(/);
  assert.match(api, /"\/api\/worker\/actions\/" \+ enc\(requestId\)/);
  assert.match(api, /workerStatus, workerActions, workerAction, requestWorkerAction/);
  assert.match(app, /N\.requestWorkerAction\(\s*"strength_refresh",\s*\{ parameters: requestedParameters \}/);
  assert.match(app, /N\.workerAction\(current\.request_id\)/);
  assert.match(app, /N\.invalidateCache\("\/api\/strength\/scan"\)/);
  assert.match(app, /const snapshot = await N\.scan\(strengthQueryParams\(actualParameters\), true\)/);
  assert.match(app, /operation\.details && operation\.details\.parameters/);
  assert.match(app, /另一组条件的任务已被复用/);
  assert.match(app, /仍在冷却期，本次未重复扫描/);
  for (const field of ['universe', 'timeframe', 'profile', 'top', 'sector_id', 'min_price', 'min_avg_dollar_volume', 'include_options']) {
    assert.match(app, new RegExp(`${field}:`));
  }
  assert.match(app, /button\.disabled = busy/);
  assert.match(app, /data-strength-refresh-state aria-live="polite"/);
  assert.match(app, /scanR\.e\.code === "strength_snapshot_unavailable"/);
  assert.match(app, /页面不会自行发起全市场扫描/);
  assert.match(app, /原有结果继续保留/);
  assert.doesNotMatch(app, /renderScreener\(true\)/);
  assert.doesNotMatch(app, /N\.scan\(scanParams\(\), forceScan\)/);

  const submitAt = app.indexOf('N.requestWorkerAction(\n        "strength_refresh"');
  const pollAt = app.indexOf('operation = await pollStrengthRefresh(operation, generation)');
  const readAt = app.indexOf('const snapshot = await N.scan(strengthQueryParams(actualParameters), true)');
  assert.ok(submitAt >= 0 && pollAt > submitAt && readAt > pollAt);
});

test('worker action transport sends the complete Strength parameters as same-origin JSON', async () => {
  const requests = [];
  const window = {};
  const context = vm.createContext({
    window,
    fetch: async (path, options) => {
      requests.push({ path, options });
      return {
        ok: true,
        status: 202,
        headers: { get: () => null },
        json: async () => ({ request_id: 'act_test', status: 'queued' }),
      };
    },
    setTimeout,
    clearTimeout,
    DOMException,
    Date,
    Intl,
    Map,
    Math,
    Promise,
    encodeURIComponent,
    isFinite,
  });
  vm.runInContext(api, context, { filename: 'deck-api.js' });
  const parameters = {
    universe: 'themes', timeframe: 'mid', profile: 'aggressive', top: 30,
    sector_id: 'semiconductors', min_price: 10, min_avg_dollar_volume: 25000000,
    include_options: true,
  };

  await window.OPTIX_NET.requestWorkerAction('strength_refresh', { parameters });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/worker/actions/strength_refresh');
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(requests[0].options.credentials, 'same-origin');
  assert.equal(requests[0].options.headers['Content-Type'], 'application/json');
  assert.equal(requests[0].options.headers['X-Optix-Action'], '1');
  assert.deepEqual(JSON.parse(requests[0].options.body), { parameters });
});

test('market-focus cycles are explicit, revision-bound, and never triggered by page refresh', () => {
  assert.match(api, /createCatalystMarketCycle/);
  assert.match(api, /expected_prepared_revision/);
  assert.match(api, /retry_cycle_id/);
  assert.match(catalysts, /cat-focus-run/);
  assert.match(catalysts, /addEventListener\("click", \(\) => startMarketFocusCycle\(\)\)/);
  assert.match(catalysts, /重试同一不可变快照/);
  assert.match(catalysts, /submission_outcome_unknown/);
  assert.match(catalysts, /提交结果待核对/);
  assert.match(catalysts, /为避免重复计费/);
  assert.equal((catalysts.match(/N\.createCatalystMarketCycle\(/g) || []).length, 1);
  assert.match(catalysts, /preparedRevision > consumedRevision/);
  assert.match(catalysts, /新热点仍会进入下一准备版本，不会混入当前不可变快照/);
  assert.match(catalysts, /准备版本尚未消费/);
  assert.match(catalysts, /今日预算已用完/);
  assert.match(catalysts, /重新分析当前上下文/);
  assert.match(catalysts, /force: true/);
  assert.match(catalysts, /普通页面刷新不会创建模型任务/);
  assert.match(catalysts, /不进入正式股票排名、突破评分或市场形态/);
  assert.match(catalysts, /signedScore\(item\.weighted_catalyst_context\)/);
  assert.doesNotMatch(catalysts, /weighted_catalyst_context\s*\?\?/);
  assert.doesNotMatch(catalysts, /胜率\s*[:：]?\s*\d|收益概率\s*[:：]?\s*\d/);
});

test('Catalyst analysis errors use the required Simplified Chinese copy', () => {
  const context = vm.createContext({
    window: {
      location: { origin: 'https://option.example' },
      OPTIX_NET: {},
      OPTIX_AI_JOBS: { normalizeStatus: value => String(value || 'pending').toLowerCase() },
    },
    document: { querySelector: () => null, querySelectorAll: () => [] },
    URL,
    URLSearchParams,
    Date,
    history: { replaceState: () => {} },
  });
  vm.runInContext(catalysts, context, { filename: 'deck-catalysts.js' });
  const desk = context.window.OPTIX_CATALYSTS;
  assert.deepEqual(
    [
      'ai_job_queue_full',
      'daily_job_limit_reached',
      'daily_budget_usd_reached',
      'analysis_cooldown_active',
      'cache_unavailable',
    ].map(code => desk.analysisErrorMessage(code)),
    [
      '分析队列已满，请稍后重试',
      '今日任务次数已用完',
      '今日分析预算已用完',
      '分析正在冷却中',
      '本地缓存暂不可用',
    ],
  );
  assert.equal(
    desk.analysisErrorDetail({ error_code: 'ai_job_queue_full', retry_after_seconds: 60 }),
    '分析队列已满，请稍后重试 · 60 秒后可重试',
  );
  assert.match(catalysts, /analysisErrorDetail\(cycle\)/);
  assert.match(catalysts, /analysisErrorDetail\(statusPayload\)/);
});

test('an old unknown market-focus cycle stays immutable while a newer prepared revision can start separately', () => {
  const context = vm.createContext({
    window: {
      location: { origin: 'https://option.example' },
      OPTIX_NET: {
        fmtDateTime: value => `DATE:${value}`,
      },
      OPTIX_AI_JOBS: {
        normalizeStatus: value => String(value || 'pending').toLowerCase(),
        isActive: value => ['pending', 'queued', 'in_progress'].includes(String(value || '').toLowerCase()),
      },
    },
    document: { querySelector: () => null, querySelectorAll: () => [] },
    URL,
    URLSearchParams,
    Date,
    history: { replaceState: () => {} },
  });
  vm.runInContext(catalysts, context, { filename: 'deck-catalysts.js' });
  const desk = context.window.OPTIX_CATALYSTS;
  const workerActions = ['focus_refresh', 'strength_refresh', 'breakout_refresh', 'retention'];
  for (const actionType of workerActions) {
    assert.equal(
      desk.workerTaskAvailable({ healthy: true, tasks: [] }, actionType),
      false,
    );
    assert.equal(
      desk.workerTaskAvailable({
        healthy: true,
        tasks: [{ task_name: actionType, enabled: false }],
      }, actionType),
      false,
    );
    assert.equal(
      desk.workerTaskAvailable({
        healthy: true,
        tasks: [{ task_name: actionType, enabled: true }],
      }, actionType),
      true,
    );
  }
  const disabledAnalysis = desk.analysisActionDecision(false, { enabled: false, reason: 'read_only_mode' });
  assert.equal(disabledAnalysis.modeUnavailable, true);
  assert.equal(disabledAnalysis.showAction, false);
  assert.equal(disabledAnalysis.canTrigger, false);
  assert.equal(disabledAnalysis.title, '当前为只读模式');
  assert.match(disabledAnalysis.detail, /手动分析/);

  const visitorAnalysis = desk.analysisActionDecision(
    true,
    { enabled: true, reason: 'available' },
    false,
  );
  assert.equal(visitorAnalysis.modeUnavailable, true);
  assert.equal(visitorAnalysis.showAction, false);
  assert.equal(visitorAnalysis.canTrigger, false);
  assert.equal(visitorAnalysis.reason, 'owner_login_required');
  assert.equal(visitorAnalysis.title, '登录后可使用模型分析');
  assert.match(visitorAnalysis.detail, /公开浏览只显示已有结果/);

  const enabledAnalysis = desk.analysisActionDecision(true, { enabled: true, reason: 'available' });
  assert.equal(enabledAnalysis.showAction, true);
  assert.equal(enabledAnalysis.canTrigger, true);
  assert.equal(enabledAnalysis.title, '尚未生成模型分析');

  const switchedOffAnalysis = desk.analysisActionDecision(false, { enabled: false, reason: 'manual_analysis_disabled' });
  assert.equal(switchedOffAnalysis.modeUnavailable, true);
  assert.equal(switchedOffAnalysis.showAction, false);
  assert.equal(switchedOffAnalysis.title, '手动分析已关闭');

  const visitorFocus = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: true, reason: 'available' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, null, 1055, false);
  assert.equal(visitorFocus.readOnly, true);
  assert.equal(visitorFocus.analysisUnavailable, true);
  assert.equal(visitorFocus.showAction, false);
  assert.equal(visitorFocus.canRun, false);
  assert.equal(visitorFocus.canCreate, false);
  assert.equal(visitorFocus.buttonText, '分析功能未启用');
  assert.equal(desk.focusCycleRequest(visitorFocus, null), null);

  const oldCycle = {
    cycle_id: 'mfc_old_unknown',
    status: 'failed',
    error_code: 'submission_outcome_unknown',
    prepared_revision: 31669,
    completed_at: '2026-07-14T14:45:33.549482Z',
  };
  const disabled = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: false,
    analysis_availability: { enabled: false, reason: 'read_only_mode' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055, true);

  assert.equal(disabled.analysisUnavailable, true);
  assert.equal(disabled.showAction, false);
  assert.equal(disabled.buttonText, '分析功能未启用');
  assert.equal(disabled.canRun, false);
  assert.equal(disabled.canRetry, false);
  assert.equal(disabled.showHistoricalUnknown, true);
  const historyHtml = desk.focusUnknownHistoryHtml(oldCycle, disabled);
  assert.match(historyHtml, /历史记录 · 提交结果待核对 · DATE:2026-07-14T14:45:33\.549482Z/);
  assert.match(historyHtml, /准备版本 31669/);
  assert.match(historyHtml, /仍禁止重试同一周期/);

  const disabledUnknown = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: false,
    analysis_availability: { enabled: false, reason: 'read_only_mode' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055);
  assert.equal(disabledUnknown.newPreparationAfterUnknown, true);
  assert.match(
    desk.focusUnknownHistoryHtml(oldCycle, disabledUnknown),
    /需待分析功能可用后另建周期/,
  );

  const notConfigured = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: false, configured: false, reason: 'not_configured' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055, true);
  assert.equal(notConfigured.notConfigured, true);
  assert.equal(notConfigured.showAction, true);
  assert.equal(notConfigured.canRun, false);
  assert.equal(notConfigured.buttonText, '尚未配置OpenAI');

  const enabled = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: true, reason: 'available' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055, true);
  assert.equal(enabled.newPreparationAfterUnknown, true);
  assert.equal(enabled.showAction, true);
  assert.equal(enabled.canCreate, true);
  assert.equal(enabled.canRetry, false);
  assert.equal(enabled.buttonText, '基于 1055 个新热点创建新周期');
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.focusCycleRequest(enabled, oldCycle))),
    { expected_prepared_revision: 54331 },
  );

  const sameRevision = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: true, reason: 'available' },
    prepared_revision: 31669,
    last_consumed_revision: 0,
  }, oldCycle, 1, true);
  assert.equal(sameRevision.canCreate, false);
  assert.equal(sameRevision.canRetry, false);
  assert.equal(desk.focusCycleRequest(sameRevision, oldCycle), null);

  const sameRevisionDuringCooldown = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: true, reason: 'available' },
    prepared_revision: 31669,
    last_consumed_revision: 0,
    cooldown_until: '2999-01-01T00:00:00Z',
  }, oldCycle, 1, true);
  assert.equal(sameRevisionDuringCooldown.buttonText, '提交结果待核对');
  assert.equal(sameRevisionDuringCooldown.canRun, false);

  const missingCycleRevision = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: true, reason: 'available' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, { ...oldCycle, prepared_revision: undefined }, 1055, true);
  assert.equal(missingCycleRevision.cycleHasPreparedRevision, false);
  assert.equal(missingCycleRevision.canCreate, false);

  const ordinaryFailedCycle = {
    cycle_id: 'mfc_failed_retryable',
    status: 'failed',
    error_code: 'provider_timeout',
    prepared_revision: 31669,
  };
  const retryBeforeNewBatch = desk.focusCycleDecision({
    status: 'active',
    manual_enabled: true,
    analysis_availability: { enabled: true, reason: 'available' },
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, ordinaryFailedCycle, 1055, true);
  assert.equal(retryBeforeNewBatch.canRetry, true);
  assert.equal(retryBeforeNewBatch.canCreate, false);
  assert.equal(retryBeforeNewBatch.buttonText, '重试同一不可变快照');
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.focusCycleRequest(retryBeforeNewBatch, ordinaryFailedCycle))),
    { retry_cycle_id: 'mfc_failed_retryable' },
  );
});

test('expired-watermark recovery keeps the stale snapshot visible', () => {
  assert.match(catalysts, /raw\.resync_required \|\| feedStream\.resync_required/);
  assert.match(catalysts, /旧快照 · 重新同步中/);
  assert.match(catalysts, /旧快照继续可读/);
  assert.match(catalysts, /完整分页校验成功后才会原子切换水位/);
});

test('focus fallback is visible as a degraded source instead of a healthy snapshot', () => {
  assert.match(catalysts, /fallback: "兜底源"/);
  assert.match(catalysts, /\["degraded", "fallback", "failed", "stale", "unavailable"\]/);
  assert.match(catalysts, /displayStatus = \["active", "empty"\]\.includes\(status\) && degraded \? "degraded" : status/);
  assert.match(catalysts, /<span>兜底源<\/span>/);
  assert.match(catalysts, /warnings\[0\][\s\S]*模型推断，不代表收益概率/);
});

test('filters, time semantics, uncertainty labels, and context isolation stay visible', () => {
  assert.match(catalysts, /page\.draft = readDraftFromForm\(\)/);
  assert.match(catalysts, /page\.applied = Object\.assign\(\{\}, page\.draft\)/);
  assert.match(catalysts, /编辑中的条件不会被自动刷新覆盖/);
  assert.match(catalysts, /as_of: opts\.asOf/);
  assert.match(catalysts, /模型置信度[\s\S]*非胜率/);
  assert.match(catalysts, /模型影响分[\s\S]*不是预期收益/);
  assert.match(catalysts, /不影响突破判断/);
  assert.match(catalysts, /正式 ranking_score/);
  assert.match(catalysts, /不影响行情、K线、期权、估值和评分/);
  assert.match(app, /id="evd-catalysts"/);
  assert.match(app, /id="cand-catalysts"/);
  assert.match(app, /id="stock-catalysts"/);
});

test('historical ticker panels keep their point-in-time cutoff in news details and completion refreshes', () => {
  assert.match(api, /catalystNews = \(id, params, options\)[\s\S]*qs\(params\)/);
  assert.match(catalysts, /openNews\(button\.dataset\.catalystNews, \{ asOf: opts\.asOf \|\| null \}\)/);
  assert.match(catalysts, /N\.catalystNews\(id, \{ as_of: asOf \|\| undefined \}, \{ signal: controller\.signal \}\)/);
  assert.equal((catalysts.match(/N\.catalystNews\(id, \{ as_of: asOf \|\| undefined \}, \{ force: true/g) || []).length, 2);
  assert.match(catalysts, /if \(asOf\) item\.analysis_trigger_enabled = false/);
  assert.match(catalysts, /bindAnalysisActions\(item, item\.analysis_job \|\| item\.job \|\| null, !asOf, asOf\)/);
  assert.match(catalysts, /page\.params\.get\("news"\)\) openNews\(page\.params\.get\("news"\)\)/);
});

test('drawer, tabs, themes, motion preferences, and narrow layouts have explicit safeguards', () => {
  assert.match(app, /element\.inert = value/);
  assert.match(app, /e\.key === "Tab" && !drawer\.hidden/);
  assert.match(app, /lastFocusEl\.focus\(\{ preventScroll: true \}\)/);
  assert.match(app, /\.deck-nav"\)\.classList\.contains\("open"\)/);
  assert.match(app, /menu-toggle"\)\.focus\(\{ preventScroll: true \}\)/);
  assert.match(app, /restoreScroll/);
  assert.match(catalysts, /ArrowRight[\s\S]*ArrowLeft[\s\S]*Home[\s\S]*End/);
  assert.match(catalystCss, /@media \(max-width: 430px\)/);
  assert.match(catalystCss, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(catalystCss, /overflow-wrap: anywhere/);
  assert.match(deckCss, /@media \(max-width: 480px\)[\s\S]*\.brand__word small \{ display: none; \}/);
  assert.match(app, /renderBackground = activeRoute !== "watchlist" \|\| !St\.watch/);
  assert.match(app, /tkr\.toUpperCase\(\) \+ " · 标的研究"/);
  assert.doesNotMatch(catalystCss, /#[0-9a-f]{3,8}\b/i);
});

test('job coordinator uses the documented cadence and completes through its own poller', async () => {
  const callbacks = [];
  const context = vm.createContext({
    window: {},
    document: { hidden: false },
    AbortController,
    DOMException,
    Date,
    setTimeout: (fn) => { queueMicrotask(fn); return 1; },
    clearTimeout: () => {},
  });
  vm.runInContext(jobs, context, { filename: 'deck-ai-jobs.js' });
  const coordinator = context.window.OPTIX_AI_JOBS;
  assert.deepEqual(Array.from(coordinator.pollSchedule), [2, 3, 5, 8, 10]);
  coordinator.start({
    scope: 'test:job',
    create: async () => ({ job_id: 'local-job-1', status: 'queued' }),
    poll: async () => ({ job_id: 'local-job-1', status: 'completed', result: { ok: true } }),
    onUpdate: (job) => callbacks.push(job.status),
    onComplete: (job) => callbacks.push(`done:${job.status}`),
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(callbacks, ['pending', 'queued', 'completed', 'done:completed']);
});

test('cancel keeps polling until the server reports a terminal state', async () => {
  const callbacks = [];
  let polls = 0;
  const context = vm.createContext({
    window: {},
    document: { hidden: false },
    AbortController,
    DOMException,
    Date,
    setTimeout: (fn) => { queueMicrotask(fn); return 1; },
    clearTimeout: () => {},
  });
  vm.runInContext(jobs, context, { filename: 'deck-ai-jobs.js' });
  const coordinator = context.window.OPTIX_AI_JOBS;
  coordinator.watch(
    { job_id: 'local-job-cancel', status: 'in_progress' },
    {
      scope: 'test:cancel',
      poll: async () => ({
        job_id: 'local-job-cancel',
        status: ++polls >= 2 ? 'cancelled' : 'in_progress',
        cancel_requested: true,
      }),
      cancel: async () => ({
        job_id: 'local-job-cancel',
        status: 'in_progress',
        cancel_requested: true,
      }),
      onUpdate: job => callbacks.push(job.status),
      onComplete: job => callbacks.push(`done:${job.status}`),
    },
  );
  await coordinator.cancel('test:cancel');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(callbacks.at(-1), 'done:cancelled');
  assert.equal(callbacks.filter(value => value.startsWith('done:')).length, 1);
});

test('Catalyst confidence uses the contract 0..100 scale and ticker projections stay isolated', () => {
  const context = vm.createContext({
    window: {
      location: { origin: 'https://option.example' },
      OPTIX_NET: { fmtDateTime: value => String(value || '—') },
      OPTIX_AI_JOBS: { normalizeStatus: value => String(value || 'pending').toLowerCase() },
    },
    document: { querySelector: () => null, querySelectorAll: () => [] },
    URL,
    URLSearchParams,
    history: { replaceState: () => {} },
  });
  vm.runInContext(catalysts, context, { filename: 'deck-catalysts.js' });
  const desk = context.window.OPTIX_CATALYSTS;
  assert.equal(desk.formatConfidence(1), '1%');
  assert.equal(
    desk.itemTitle({
      title: 'English source title',
      title_zh: '顶层中文标题',
      analysis: { title_zh: '分析生成的中文标题' },
    }),
    '分析生成的中文标题',
  );
  assert.equal(
    desk.itemTitle({ title: 'English source title', headline: 'English fallback' }),
    '中文标题等待生成',
  );
  assert.equal(
    desk.itemSummary({
      summary: 'English source summary',
      summary_zh: '<p>顶层中文摘要</p>',
      analysis: { headline_summary: '分析生成的中文摘要' },
    }),
    '顶层中文摘要',
  );
  assert.equal(
    desk.itemSummary({
      summary: 'English source summary',
      analysis: { headline_summary: '<p>分析生成的中文摘要</p>' },
    }),
    '分析生成的中文摘要',
  );
  assert.equal(
    desk.itemSummary({ summary: 'English source summary', description: 'English fallback' }),
    '中文摘要等待生成',
  );
  const own = [{ ticker: 'NVDA', impact_score: 40 }];
  const all = [...own, { ticker: 'AMD', impact_score: -30 }];
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.impactsOf({ ticker_impacts: own, analysis: { affected_stocks: all } }))),
    own,
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.impactsOf({ trusted_stock_impacts: own, analysis: { affected_stocks: all } }))),
    own,
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.impactsOf({ analysis: { affected_stocks: all } }))),
    [],
  );
  assert.equal(desk.impactDirection({ impact_score: -30 }), 'bearish');
  assert.equal(desk.impactDirection({ impact_score: 0 }), 'neutral');
  assert.equal(desk.sentimentOf({ analysis: { overall_sentiment: 0 } }), 0);
  assert.equal(desk.isRuleOnlyAnalysis({ analysis_status: 'insufficient_context', analysis: { insufficient_context: true, model: 'low-context-neutral-v2' } }), true);
  assert.equal(desk.isRuleOnlyAnalysis({ analysis_status: 'insufficient_context', analysis: { insufficient_context: true, model: 'gpt-5.6-terra' } }), false);
  assert.equal(desk.isRuleOnlyAnalysis(
    { analysis_status: 'insufficient_context', analysis: { insufficient_context: true, model: 'low-context-neutral-v2' } },
    { status: 'insufficient_context', model: 'gpt-5.6-terra' },
  ), true);
  assert.equal(desk.isRuleOnlyAnalysis({ analysis_status: 'completed', analysis: { model: 'gpt-5.6-terra' } }), false);
  assert.equal(desk.analysisOriginLabel({ analysis: { insufficient_context: true, model: 'low-context-neutral-v2' } }), '规则结果 · 未调用模型');
  assert.equal(desk.analysisOriginLabel({ analysis: { insufficient_context: true, model: 'gpt-5.6-terra' } }), '模型推断 · 信息不足');
  assert.equal(desk.analysisOriginLabel({ analysis: { insufficient_context: false, model: 'gpt-5.6-terra' } }), '模型推断');
  assert.equal(desk.analysisRetryForce({ analysis_status: 'completed' }, { status: 'failed' }), true);
  assert.equal(desk.analysisRetryForce({ analysis_status: 'failed' }, { status: 'cancelled' }), true);
  assert.equal(desk.analysisRetryForce({ analysis_status: 'failed' }, { status: 'completed' }), true);
  assert.equal(
    desk.plainText('<p class="lead">US CPI &amp; rates</p><p>Second paragraph.</p>'),
    'US CPI & rates Second paragraph.',
  );
  assert.equal(
    desk.plainText('<p>Fed&#8217;s move &#x2014; stocks &lt;script&gt;ignored&lt;/script&gt;</p>'),
    'Fed’s move — stocks ignored',
  );
  const lowContextHtml = desk.compactNews({
    news_id: 101,
    source: 'Fixture Wire',
    title: 'Short item',
    published_at: '2026-07-11T10:00:00Z',
    analysis_status: 'insufficient_context',
    analysis: {
      model: 'low-context-neutral-v2',
      insufficient_context: true,
      classification: 'neutral',
      confidence: 0,
      overall_sentiment: 0,
      causal_summary: '上下文不足。',
      affected_stocks: [{ ticker: 'NVDA', impact_score: 0 }],
    },
  });
  assert.match(lowContextHtml, /信息不足 · 未调用模型/);
  assert.doesNotMatch(lowContextHtml, /新闻整体|股票影响|置信度/);
  const paidInsufficientHtml = desk.compactNews({
    news_id: 102,
    source: 'Fixture Wire',
    title: 'Paid model needs more context',
    published_at: '2026-07-11T10:00:00Z',
    analysis_status: 'insufficient_context',
    ticker_impacts: [{ ticker: 'AMD', impact_score: 15, direction: 'bullish' }],
    analysis: {
      model: 'gpt-5.6-terra',
      insufficient_context: true,
      classification: 'bullish',
      confidence: 25,
      overall_sentiment: 15,
      causal_summary: '模型完成分析，但认为上下文不足。',
    },
  });
  assert.doesNotMatch(paidInsufficientHtml, /未调用模型/);
  assert.match(paidInsufficientHtml, /股票影响 · 正向/);
  assert.match(paidInsufficientHtml, /新闻整体 · 正向/);
  assert.match(paidInsufficientHtml, /置信度 25% · 非胜率/);
  assert.match(catalysts, /finite\(analysis\.overall_sentiment\)/);
  assert.match(catalysts, /新闻整体/);
  assert.match(catalysts, /规则中性 · 信息不足 · 未调用模型/);
  assert.match(catalysts, /目标配置/);
  assert.match(catalysts, /模型识别股票[\s\S]*验证后才计入影响榜/);
  assert.match(catalysts, /代码有歧义/);
  assert.match(catalysts, /尚未验证/);
  assert.match(catalysts, /const duplicates = new Set\(\)/);
  assert.match(catalysts, /duplicates\.add\(ticker\)/);
  assert.match(catalysts, /validation_status: "unverified"/);
});

test('pagination discards an old-filter page after a new applied request starts', () => {
  assert.match(catalysts, /const request = page\.feedRequest/);
  assert.match(catalysts, /filterSnapshot !== JSON\.stringify\(queryParams\(\)\)/);
  assert.match(catalysts, /cursor !== \(page\.feed && page\.feed\.next_cursor\)/);
});

test('a completed local job can render its validated result before feed publication catches up', () => {
  assert.match(catalysts, /statusPayload\.result/);
  assert.match(catalysts, /persistedAnalysis \|\| jobResult/);
  assert.match(catalysts, /statusPayload && statusPayload\.model/);
  assert.match(catalysts, /statusPayload && statusPayload\.reasoning/);
});

test('drawer retry follows the current job state and keeps that job bound after rerender', () => {
  assert.match(catalysts, /analysisRetryForce\(item, job\)/);
  assert.match(catalysts, /bindAnalysisActions\(item, job, false, asOf\)/);
  assert.match(catalysts, /\["completed", "insufficient_context", "failed", "cancelled"\]/);
  assert.match(catalysts, />重新分析<\/button>/);
});

test('source health uses the v1 counters and treats ok as a healthy state', () => {
  assert.match(catalysts, /ok: "正常"/);
  assert.match(catalysts, /"ok", "active", "completed"/);
  assert.match(catalysts, /source\.duplicates_count/);
});

test('long stock-impact reasons are accessible without crowding the drawer', () => {
  assert.match(catalysts, /reason\.length <= 72/);
  assert.match(catalysts, /<details class="cat-impact-reason">/);
  assert.match(catalysts, /查看完整影响理由/);
  assert.match(catalystCss, /\.cat-impact-reason summary:focus-visible/);
});

test('feed summaries remove source markup and stay visually bounded', () => {
  assert.match(catalysts, /const plainText = value/);
  assert.match(catalysts, /item && item\.summary_zh/);
  assert.match(catalysts, /analysis\.summary_zh \|\| analysis\.headline_summary/);
  assert.doesNotMatch(catalysts, /item\.summary \|\| item\.description/);
  assert.match(catalystCss, /\.cat-news__summary[^}]*-webkit-line-clamp:\s*3/);
});

test('frontend sources never contain service secrets or a project API key', () => {
  const bundle = [index, api, jobs, catalysts, app, deckCss, catalystCss].join('\n');
  assert.doesNotMatch(bundle, /sk-proj-[A-Za-z0-9_-]+/);
  assert.doesNotMatch(bundle, /MACROLENS_(?:READ|ACTION)_SECRET|OPENAI_API_KEY/);
});

test('unknown signal submission cannot be retried into duplicate billing', () => {
  assert.match(app, /job\.error_code === "submission_outcome_unknown"/);
  assert.match(app, /为避免重复计费，此处禁止重提/);
});
