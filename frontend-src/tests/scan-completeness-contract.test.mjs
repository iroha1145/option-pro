/**
 * 「局部样本不得冒充完整扫描」回归（GPT-5.6-Pro 审计 P1-05 / P1-06 / P2-10 / P2-12）
 *
 * 这一批的共同形状是：结果集其实只覆盖一部分候选，界面却按全量呈现 —— 或者更糟，
 * 排名结果取决于用户翻过哪几页。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  CATALYST_SUMMARY_TTL_MS,
  catalystSummaryUsable,
} from '../src/components/screener/types.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

function codeOf(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart();
      return !trimmed.startsWith('//') && !trimmed.startsWith('*');
    })
    .join('\n');
}

/* ---------------- P1-06：排序不得依赖访问过哪些分页 ---------------- */

test('切到催化排序时为全部候选取摘要，而不是只取当前页', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));

  // 缺失集合按 filtered（全部候选）计算，不是 pageRows
  assert.match(page, /missingCatalystTickers = useMemo\(\(\) => \{[\s\S]*?return filtered/);
  assert.match(page, /catalystSortActive = sortMode !== 'deterministic'/);
  // 未取齐时维持确定性顺序 —— 不能用缺失值参与正式排名
  assert.match(page, /if \(sortMode === 'deterministic' \|\| preparingCatalystSort\)/);
  // 界面必须说明正在准备
  assert.match(page, /正在准备排序数据/);
  // 契约批量上限是 20，超过必须切片
  assert.match(page, /CATALYST_BATCH_SIZE/);
  assert.match(page, /offset \+= CATALYST_BATCH_SIZE/);
});

test('批量抓取按 20 只切片，覆盖 120 只候选需要 6 批', async () => {
  const page = await source('pages/Screener.tsx');
  const size = Number(/const CATALYST_BATCH_SIZE = (\d+)/.exec(page)?.[1]);
  assert.equal(size, 20, '契约 /catalysts/tickers/batch 的匿名上限');
  assert.equal(Math.ceil(120 / size), 6);
});

/* ---------------- P2-12：摘要不能在会话内永不失效 ---------------- */

test('催化摘要按 TTL 过期，失败条目立即可重试', () => {
  const now = 1_000_000;
  const fresh = { loaded: true, count: 3, pos: 2, neg: 1, neu: 0, latestAt: null, latestTitle: null, fetchedAt: now };

  assert.equal(catalystSummaryUsable(fresh, now), true);
  assert.equal(
    catalystSummaryUsable(fresh, now + CATALYST_SUMMARY_TTL_MS + 1),
    false,
    '过期后必须重取，否则当天新出现的新闻永远进不了已看过的股票',
  );
  assert.equal(
    catalystSummaryUsable({ ...fresh, failed: true }, now),
    false,
    '失败的条目不该占住键位阻止重试',
  );
  assert.equal(
    catalystSummaryUsable({ ...fresh, fetchedAt: undefined }, now),
    false,
    '没有时间戳就无法判断新鲜度，按需要重取处理',
  );
  assert.equal(catalystSummaryUsable(undefined, now), false);
  // 真实为 0 与「没加载」是两件事：前者可用
  assert.equal(
    catalystSummaryUsable({ ...fresh, count: 0, pos: 0, neg: 0 }, now),
    true,
  );
});

test('页内抓取也走同一套新鲜度判断，不再只看键是否存在', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));
  assert.doesNotMatch(page, /catalystsRef\.current\[t\] === undefined/);
  assert.match(page, /!catalystSummaryUsable\(catalystsRef\.current\[t\], now\)/);
  assert.match(page, /fetchedAt/);
});

/* ---------------- P1-05：客户端筛选的真实作用范围 ---------------- */

test('已评分候选多于返回行时，界面标明只在前 N 名内筛选', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));
  assert.match(page, /truncatedScope = useMemo/);
  assert.match(page, /if \(scanMeta\.screenedCount <= returned\) return null;/);
  assert.match(page, /\{__t\('仅在强度前'\)\} \{truncatedScope\.returned\} \{__t\('名内筛选'\)\}/);
});

/* ---------------- P2-10：分档计数描述候选池 ---------------- */

test('分档计数取后端整池分布，缺失时如实标注范围', async () => {
  const page = codeOf(await source('pages/Screener.tsx'));
  const workbench = codeOf(await source('components/screener/FilterWorkbench.tsx'));

  assert.match(page, /universeQ\.data\?\.tierDistribution \?\? null/);
  assert.match(page, /tierCountsCoverPool: distribution !== null/);
  assert.match(workbench, /coversPool \? __t\('已评分候选池'\) : __t\('当前快照返回的行'\)/);
});

test('分档分布任一档缺失即整体判为不可用', async () => {
  const api = codeOf(await source('api/modules/strength.ts'));
  // 不做部分拼装：缺一个键就返回 null，由界面回退并标注
  assert.match(api, /if \(values\.some\(\(value\) => value === null\)\) return null;/);
  assert.match(api, /'S', 'A', 'B', 'C', 'D', 'unscored', 'scored', 'total'/);
});
