import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { classifyPageReady } from '../../scripts/perf/lib/page_ready.mjs';

test('选股壳和个股错误态不能算 content-ready', () => {
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    bodyText: '选股扫描 开始扫描',
    mainText: '选股扫描',
    hasForm: true,
    hasScanHits: false,
    hasScanEmpty: false,
    hasScanTableRow: false,
  }), 'shell');
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: 'Screener',
    bodyText: 'Screener Start scan',
    mainText: 'Screener',
    hasForm: true,
    hasScanHits: false,
    hasScanEmpty: false,
    hasScanTableRow: false,
  }), 'shell');
  assert.equal(classifyPageReady({
    path: '/watchlist',
    heading: 'Your watchlist',
    bodyText: 'Your watchlist is empty',
    mainText: 'Your watchlist is empty',
    hasTable: false,
  }), 'empty');
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    hasForm: true,
    hasScanHits: true,
    hasScanTableRow: true,
    bodyText: '命中 12 只',
    mainText: '命中 12 只',
  }), 'content');
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    hasForm: true,
    hasScanEmpty: true,
    hasScanHits: false,
    hasScanTableRow: false,
    bodyText: '暂无股票符合当前条件。',
    mainText: '暂无股票符合当前条件。',
  }), 'empty');
  assert.equal(classifyPageReady({
    path: '/stock/NVDA',
    heading: '',
    ariaBusy: false,
    bodyText: 'STOCK · $NVDA 行情服务暂不可用',
    mainText: '行情服务暂不可用',
    hasQuote: false,
  }), 'error');
  assert.equal(classifyPageReady({
    path: '/stock/NVDA',
    heading: '',
    ariaBusy: false,
    bodyText: 'STOCK · $NVDA 请求较频繁',
    mainText: '请求较频繁',
    hasQuote: false,
  }), 'error');
  assert.equal(classifyPageReady({
    path: '/stock/NVDA',
    heading: '',
    ariaBusy: false,
    bodyText: 'STOCK · $NVDA 登录状态已失效',
    mainText: '登录状态已失效',
    hasQuote: false,
  }), 'error');
  assert.equal(classifyPageReady({
    path: '/stock/NVDA',
    heading: '',
    ariaBusy: false,
    bodyText: 'STOCK · $NVDA 该标的暂无完整数据',
    mainText: '该标的暂无完整数据',
    hasQuote: false,
  }), 'empty');
  assert.equal(classifyPageReady({
    path: '/stock/NVDA',
    heading: 'NVDA',
    ariaBusy: false,
    bodyText: 'NVDA $128.40',
    mainText: 'NVDA $128.40 日线',
    hasQuote: true,
  }), 'content');
});

test('measure_pages 按类报告，不再把错误文案写进成功耗时', async () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const pages = await readFile(path.resolve(here, '../../scripts/perf/measure_pages.mjs'), 'utf8');
  assert.match(pages, /ready_class/);
  assert.match(pages, /content_p75/);
  assert.match(pages, /error_rate/);
  assert.match(pages, /pageReadyInstallScript/);
  assert.doesNotMatch(pages, /行情服务暂不可用\|请求较频繁\|登录状态已失效/);
  assert.doesNotMatch(pages, /button, form, input/);
});
