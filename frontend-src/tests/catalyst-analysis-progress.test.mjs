import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {
  normalizeNewsAnalysisProgress,
} from '../src/components/catalysts/analysisProgressContract.ts';

const here = path.dirname(fileURLToPath(import.meta.url));

const payload = {
  status: 'active',
  scope: 'latest_submission_batch',
  batch_id: 'aib_0123456789abcdef',
  batch_source: 'scheduled',
  total: 4,
  finished: 2,
  succeeded: 1,
  awaiting_validation: 0,
  rejected: 1,
  failed: 0,
  waiting: 1,
  in_progress: 1,
  cancelled: 0,
  insufficient_context: 0,
  budget_blocked: 0,
  progress_percent: 50,
  current_index: 3,
  current_news_id: 103,
  current_phase: 'provider_processing',
  queue_total: 2,
  queue_waiting: 1,
  queue_in_progress: 1,
  started_at: '2026-07-23T10:00:00Z',
  last_updated_at: '2026-07-23T10:02:00Z',
  as_of: '2026-07-23T10:05:00Z',
};

test('normalizes count-based news analysis progress', () => {
  assert.deepEqual(normalizeNewsAnalysisProgress(payload), {
    status: 'active',
    scope: 'latest_submission_batch',
    batchId: 'aib_0123456789abcdef',
    batchSource: 'scheduled',
    total: 4,
    finished: 2,
    succeeded: 1,
    awaitingValidation: 0,
    rejected: 1,
    failed: 0,
    waiting: 1,
    inProgress: 1,
    cancelled: 0,
    insufficientContext: 0,
    budgetBlocked: 0,
    progressPercent: 50,
    currentIndex: 3,
    currentNewsId: 103,
    currentPhase: 'provider_processing',
    queueTotal: 2,
    queueWaiting: 1,
    queueInProgress: 1,
    startedAt: '2026-07-23T10:00:00Z',
    lastUpdatedAt: '2026-07-23T10:02:00Z',
    asOf: '2026-07-23T10:05:00Z',
  });
});

test('rejects invented or internally inconsistent progress', () => {
  assert.throws(
    () => normalizeNewsAnalysisProgress({ ...payload, progress_percent: 73 }),
    /百分比不一致/,
  );
  assert.throws(
    () => normalizeNewsAnalysisProgress({ ...payload, in_progress: 2 }),
    /总数不一致/,
  );
  assert.throws(
    () =>
      normalizeNewsAnalysisProgress({
        ...payload,
        total: 5,
        in_progress: 2,
        progress_percent: 40,
      }),
    /当前条目无法唯一确定/,
  );
});

test('the UI reads the owner-only endpoint and does not synthesize mock progress', async () => {
  const apiSource = await readFile(
    path.resolve(here, '..', 'src', 'components', 'catalysts', 'analysisProgressApi.ts'),
    'utf8',
  );
  const pageSource = await readFile(
    path.resolve(here, '..', 'src', 'pages', 'Catalysts.tsx'),
    'utf8',
  );
  const catalystApiSource = await readFile(
    path.resolve(here, '..', 'src', 'components', 'catalysts', 'api.ts'),
    'utf8',
  );
  assert.ok(apiSource.includes("get('/catalysts/analysis-progress')"));
  assert.match(apiSource, /演示模式不提供真实新闻分析进度/);
  assert.doesNotMatch(apiSource, /Math\\.random|setInterval|setTimeout/);
  assert.ok(pageSource.includes('<AnalysisProgressCard />'));
  assert.ok(!catalystApiSource.includes("if (status === 'queued') return 5;"));
  assert.ok(!catalystApiSource.includes("if (status === 'in_progress') return 50;"));
});
