import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  buildInterleavedSummary,
  interleavedExitCode,
} from '../../scripts/perf/lib/interleaved_summary.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');
const verifyFrontendDist = path.join(repo, 'scripts/perf/lib/verify_frontend_dist.sh');

function samples(values) {
  return values.map((newsContentReadyMs) => ({
    news_content_ready_ms: newsContentReadyMs,
    news_title: newsContentReadyMs == null ? null : '第9600条快讯',
    lcp: newsContentReadyMs == null ? null : { startTime: newsContentReadyMs - 100 },
    rate_limited: 0,
  }));
}

test('交错测量全超时时报告失败，且不计算虚假的性能差值', () => {
  const summary = buildInterleavedSummary({
    optCold: samples([null, null]),
    optWarm: samples([null, null]),
    unoptCold: samples([1000, 1100]),
    unoptWarm: samples([500, 600]),
  });

  assert.equal(summary.opt.cold.timeout_n, 2);
  assert.equal(summary.opt.cold.failure_rate, 1);
  assert.equal(summary.comparison_complete, false);
  assert.equal(summary.delta_cold_p75, null);
  assert.equal(summary.delta_warm_p75, null);
  assert.equal(interleavedExitCode(summary), 1);
});

test('交错测量保留部分成功样本统计，但不把混合结果视作完整比较', () => {
  const summary = buildInterleavedSummary({
    optCold: samples([800, null, 1000]),
    optWarm: samples([400, 500, 600]),
    unoptCold: samples([1200, 1300, 1400]),
    unoptWarm: samples([700, 800, 900]),
  });

  assert.equal(summary.opt.cold.ready_n, 2);
  assert.equal(summary.opt.cold.timeout_n, 1);
  assert.equal(summary.opt.cold.failure_rate, 1 / 3);
  assert.equal(summary.opt.cold.news_content_ready_p50, 1000);
  assert.equal(summary.comparison_status, 'incomplete_samples');
  assert.equal(summary.delta_cold_p75, null);
  assert.equal(summary.delta_warm_p75, 500 - 800);
  assert.equal(interleavedExitCode(summary), 1);
});

test('交错测量全部成功时保留原有分位数与性能差值', () => {
  const summary = buildInterleavedSummary({
    optCold: samples([800, 900]),
    optWarm: samples([400, 500]),
    unoptCold: samples([1200, 1300]),
    unoptWarm: samples([700, 800]),
  });

  assert.equal(summary.opt.cold.timeout_n, 0);
  assert.equal(summary.opt.cold.failure_rate, 0);
  assert.equal(summary.opt.cold.news_content_ready_p75, 900);
  assert.equal(summary.comparison_status, 'complete');
  assert.equal(summary.delta_cold_p75, 900 - 1300);
  assert.equal(summary.delta_warm_p75, 500 - 800);
  assert.equal(interleavedExitCode(summary), 0);
});

test('生产静态产物不一致时验证失败并保留差异日志', async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'optix-dist-check-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const dist = path.join(temp, 'dist');
  const frontend = path.join(temp, 'frontend');
  const log = path.join(temp, 'artifacts', 'frontend-dist.diff');
  await mkdir(dist);
  await mkdir(frontend);
  // The earlier build smoke test only compares index.html. A stale boot script
  // must still stop this gate when the entry documents are identical.
  await writeFile(path.join(dist, 'index.html'), 'same entry\n');
  await writeFile(path.join(frontend, 'index.html'), 'same entry\n');
  await writeFile(path.join(dist, 'theme-boot.js'), 'new boot\n');
  await writeFile(path.join(frontend, 'theme-boot.js'), 'old boot\n');

  const result = spawnSync('bash', [verifyFrontendDist, dist, frontend, log], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 1, result.stderr);
  assert.match(await readFile(log, 'utf8'), /theme-boot\.js differ/);
});

test('生产静态产物一致时验证成功并写出空差异日志', async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'optix-dist-check-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const dist = path.join(temp, 'dist');
  const frontend = path.join(temp, 'frontend');
  const log = path.join(temp, 'artifacts', 'frontend-dist.diff');
  await mkdir(dist);
  await mkdir(frontend);
  await writeFile(path.join(dist, 'index.html'), 'same build\n');
  await writeFile(path.join(frontend, 'index.html'), 'same build\n');

  const result = spawnSync('bash', [verifyFrontendDist, dist, frontend, log], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr);
  assert.equal(await readFile(log, 'utf8'), '');
});

test('静态产物目录无法读取时验证报错并把原因写入日志', async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'optix-dist-check-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const missing = path.join(temp, 'missing-dist');
  const frontend = path.join(temp, 'frontend');
  const log = path.join(temp, 'artifacts', 'frontend-dist.diff');
  await mkdir(frontend);

  const result = spawnSync('bash', [verifyFrontendDist, missing, frontend, log], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 2, result.stderr);
  assert.match(await readFile(log, 'utf8'), /No such file or directory/);
});
