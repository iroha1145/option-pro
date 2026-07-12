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
  const dock = index.match(/<nav class="dock"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.equal((dock.match(/<a /g) || []).length, 5);
  assert.doesNotMatch(dock, /catalysts/);
  assert.match(deckCss, /\.deck-menu-toggle\.has-current-route::after/);
});

test('Catalyst data remains same-origin, abortable, cached by endpoint family, and isolated from job polling', () => {
  for (const endpoint of [
    '/api/catalysts/status', '/api/catalysts/feed', '/api/catalysts/news/',
    '/api/catalysts/tickers/', '/api/catalysts/calendar', '/api/catalysts/analysis-jobs/',
    '/api/ai/jobs/earnings-impact', '/api/ai/jobs/',
  ]) assert.match(api, new RegExp(endpoint.replaceAll('/', '\\/')));
  assert.match(api, /const normalGate = makeGate\(3\)/);
  assert.match(api, /const jobGate = makeGate\(2\)/);
  assert.match(api, /signal: opts\.signal/);
  assert.match(api, /credentials: "same-origin"/);
  assert.match(api, /redirect: "error"/);
  assert.match(api, /noCache: true, lowPriority: true/);
  assert.match(api, /sessionStorage\.getItem\("optix\.app\.token"\)/);
  assert.match(api, /headers\.Authorization = "Bearer " \+ token/);
  assert.doesNotMatch(api, /https?:\/\//);
});

test('paid analysis is explicit and earnings no longer uses the synchronous GET trigger', () => {
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
  const own = [{ ticker: 'NVDA', impact_score: 40 }];
  const all = [...own, { ticker: 'AMD', impact_score: -30 }];
  assert.deepEqual(
    JSON.parse(JSON.stringify(desk.impactsOf({ ticker_impacts: own, analysis: { affected_stocks: all } }))),
    own,
  );
  assert.equal(desk.impactDirection({ impact_score: -30 }), 'bearish');
  assert.equal(desk.impactDirection({ impact_score: 0 }), 'neutral');
  assert.equal(desk.sentimentOf({ analysis: { overall_sentiment: 0 } }), 0);
  assert.equal(desk.isRuleOnlyAnalysis({ analysis_status: 'insufficient_context', analysis: { insufficient_context: true, model: 'low-context-neutral-v2' } }), true);
  assert.equal(desk.isRuleOnlyAnalysis({ analysis_status: 'completed', analysis: { model: 'gpt-5.6-terra' } }), false);
  assert.equal(desk.analysisRetryForce({ analysis_status: 'completed' }, { status: 'failed' }), true);
  assert.equal(desk.analysisRetryForce({ analysis_status: 'failed' }, { status: 'cancelled' }), true);
  assert.equal(desk.analysisRetryForce({ analysis_status: 'failed' }, { status: 'completed' }), false);
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
  assert.match(catalysts, /finite\(analysis\.overall_sentiment\)/);
  assert.match(catalysts, /新闻整体/);
  assert.match(catalysts, /规则中性 · 信息不足 · 未调用模型/);
  assert.match(catalysts, /目标配置/);
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

test('frontend sources never contain service secrets or a project API key', () => {
  const bundle = [index, api, jobs, catalysts, app, deckCss, catalystCss].join('\n');
  assert.doesNotMatch(bundle, /sk-proj-[A-Za-z0-9_-]+/);
  assert.doesNotMatch(bundle, /MACROLENS_(?:READ|ACTION)_SECRET|OPENAI_API_KEY/);
});

test('unknown signal submission cannot be retried into duplicate billing', () => {
  assert.match(app, /job\.error_code === "submission_outcome_unknown"/);
  assert.match(app, /为避免重复计费，此处禁止重提/);
});
