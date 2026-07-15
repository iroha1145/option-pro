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
});

test('paid analysis is explicit and earnings no longer uses the synchronous GET trigger', () => {
  assert.doesNotMatch(api, /hasAppToken|appToken/);
  assert.doesNotMatch(catalysts, /privateActionsAvailable|管理令牌|管理会话未解锁/);
  assert.doesNotMatch(app, /privateActionsAvailable|管理授权|公开页面不会创建付费任务/);
  assert.match(catalysts, /const access = analysisActionDecision\(triggerEnabled\)[\s\S]*const canTrigger = access\.canTrigger/);
  assert.match(catalysts, /id="cat-refresh">请求后台同步/);
  assert.match(catalysts, /id="cat-focus-run" disabled/);
  assert.match(catalysts, /data-cat-analyze/);
  assert.match(catalysts, /createCatalystAnalysis/);
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
  assert.match(catalysts, /分析预算未配置/);
  assert.match(catalysts, /普通页面刷新不会创建模型任务/);
  assert.match(catalysts, /不进入正式股票排名、突破评分或市场形态/);
  assert.match(catalysts, /signedScore\(item\.weighted_catalyst_context\)/);
  assert.doesNotMatch(catalysts, /weighted_catalyst_context\s*\?\?/);
  assert.doesNotMatch(catalysts, /胜率\s*[:：]?\s*\d|收益概率\s*[:：]?\s*\d/);
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
  const disabledAnalysis = desk.analysisActionDecision(false);
  assert.equal(disabledAnalysis.actionMissing, true);
  assert.equal(disabledAnalysis.canTrigger, false);
  assert.equal(disabledAnalysis.title, '分析功能未启用');

  const enabledAnalysis = desk.analysisActionDecision(true);
  assert.equal(enabledAnalysis.canTrigger, true);
  assert.equal(enabledAnalysis.title, '尚未生成模型分析');

  const oldCycle = {
    cycle_id: 'mfc_old_unknown',
    status: 'failed',
    error_code: 'submission_outcome_unknown',
    prepared_revision: 31669,
    completed_at: '2026-07-14T14:45:33.549482Z',
  };
  const disabled = desk.focusCycleDecision({
    status: 'active',
    capability: 'disabled',
    action_enabled: false,
    manual_enabled: false,
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055, true);

  assert.equal(disabled.actionMissing, true);
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
    capability: 'disabled',
    action_enabled: false,
    manual_enabled: false,
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055);
  assert.equal(disabledUnknown.newPreparationAfterUnknown, true);
  assert.match(
    desk.focusUnknownHistoryHtml(oldCycle, disabledUnknown),
    /需待分析功能可用后另建周期/,
  );

  const enabled = desk.focusCycleDecision({
    status: 'active',
    capability: 'enabled',
    action_enabled: true,
    manual_enabled: true,
    prepared_revision: 54331,
    last_consumed_revision: 0,
  }, oldCycle, 1055, true);
  assert.equal(enabled.newPreparationAfterUnknown, true);
  assert.equal(enabled.canCreate, true);
  assert.equal(enabled.canRetry, false);
  assert.equal(enabled.buttonText, '基于 1055 个新热点创建新周期');
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.focusCycleRequest(enabled, oldCycle))),
    { expected_prepared_revision: 54331 },
  );

  const sameRevision = desk.focusCycleDecision({
    status: 'active',
    capability: 'enabled',
    action_enabled: true,
    manual_enabled: true,
    prepared_revision: 31669,
    last_consumed_revision: 0,
  }, oldCycle, 1, true);
  assert.equal(sameRevision.canCreate, false);
  assert.equal(sameRevision.canRetry, false);
  assert.equal(desk.focusCycleRequest(sameRevision, oldCycle), null);

  const sameRevisionDuringCooldown = desk.focusCycleDecision({
    status: 'active',
    capability: 'enabled',
    action_enabled: true,
    manual_enabled: true,
    prepared_revision: 31669,
    last_consumed_revision: 0,
    cooldown_until: '2999-01-01T00:00:00Z',
  }, oldCycle, 1, true);
  assert.equal(sameRevisionDuringCooldown.buttonText, '提交结果待核对');
  assert.equal(sameRevisionDuringCooldown.canRun, false);

  const missingCycleRevision = desk.focusCycleDecision({
    status: 'active',
    capability: 'enabled',
    action_enabled: true,
    manual_enabled: true,
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
    capability: 'enabled',
    action_enabled: true,
    manual_enabled: true,
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
  assert.match(catalysts, /N\.catalystNews\(id, \{ as_of: asOf \|\| undefined \}, \{ signal: drawerController\.signal \}\)/);
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
  assert.equal(desk.analysisRetryForce({ analysis_status: 'failed' }, { status: 'completed' }), false);
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
  assert.match(catalysts, /status === "failed" \|\| status === "cancelled"/);
  assert.match(catalysts, /显式重试分析/);
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
