import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
import {
  classifyDocument,
  classifyPageReady,
  isTerminalReady,
  pageReadyInstallScript,
  snapshotFromDocument,
} from '../../scripts/perf/lib/page_ready.mjs';

function node(tag, attrs = {}, children = [], text = '') {
  const el = {
    tagName: tag.toUpperCase(),
    attrs,
    children,
    text,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? String(attrs[name]) : null;
    },
    get textContent() {
      return `${text}${children.map((child) => child.textContent).join('')}`;
    },
    get innerText() {
      return this.textContent;
    },
    querySelector(sel) {
      return queryAll(this, sel)[0] ?? null;
    },
    querySelectorAll(sel) {
      return queryAll(this, sel);
    },
  };
  return el;
}

function queryAll(root, selector) {
  const out = [];
  const visit = (el) => {
    if (matches(el, selector)) out.push(el);
    for (const child of el.children || []) visit(child);
  };
  if (root.children) for (const child of root.children) visit(child);
  else visit(root);
  return out;
}

function matches(el, selector) {
  const parts = selector.split(',').map((part) => part.trim());
  return parts.some((part) => matchOne(el, part));
}

function matchOne(el, selector) {
  if (selector.includes(' ')) {
    const bits = selector.split(/\s+/);
    return matchOne(el, bits[bits.length - 1]);
  }
  const attrStar = /^([a-z0-9-]*)\[([a-z0-9-]+)\*=['"]([^'"]+)['"]\]$/i.exec(selector);
  if (attrStar) {
    const [, tag, attr, value] = attrStar;
    if (tag && el.tagName !== tag.toUpperCase()) return false;
    return String(el.getAttribute(attr) || '').includes(value);
  }
  const attrEq = /^([a-z0-9-]*)\[([a-z0-9-]+)=['"]([^'"]+)['"]\]$/i.exec(selector);
  if (attrEq) {
    const [, tag, attr, value] = attrEq;
    if (tag && el.tagName !== tag.toUpperCase()) return false;
    return el.getAttribute(attr) === value;
  }
  const attrOnly = /^([a-z0-9-]*)\[([a-z0-9-]+)\]$/i.exec(selector);
  if (attrOnly) {
    const [, tag, attr] = attrOnly;
    if (tag && el.tagName !== tag.toUpperCase()) return false;
    return el.getAttribute(attr) != null;
  }
  return el.tagName === selector.toUpperCase();
}

function makeDocument({ heading, regions = [], extra = [], bodyText }) {
  const headingEl = node('h1', {}, [], heading);
  const regionEls = regions.map((region) => node(
    'section',
    {
      'data-optix-region': region.name,
      'data-optix-state': region.state,
      'aria-label': region.label || region.name,
    },
    region.children || [],
    region.text || '',
  ));
  const main = node('main', {}, [headingEl, ...regionEls, ...extra]);
  const body = node('body', {}, [main], '');
  const text = bodyText ?? [heading, ...regions.map((region) => region.text || ''), ...extra.map((el) => el.textContent)].join('\n');
  return {
    body: { innerText: text, textContent: text },
    querySelector(sel) {
      if (sel === 'h1') return headingEl;
      if (sel === 'main') return main;
      if (sel === 'body') return body;
      return queryAll(body, sel)[0] ?? null;
    },
    querySelectorAll(sel) {
      return queryAll(body, sel);
    },
  };
}

test('选股壳和个股错误态不能算 content-ready', () => {
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    bodyText: '选股扫描 开始扫描',
    mainText: '选股扫描',
    hasForm: true,
    hasScanHits: false,
    hasScanEmpty: false,
    hasScanIdle: false,
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
    hasScanIdle: false,
    hasScanTableRow: false,
  }), 'shell');
  assert.equal(classifyPageReady({
    path: '/watchlist',
    heading: 'Your watchlist',
    bodyText: 'Your watchlist is empty',
    mainText: 'Your watchlist is empty',
    hasTable: false,
    hasWatchCards: false,
    hasWatchTableRow: false,
  }), 'empty');
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    hasForm: true,
    scanHitCount: 12,
    hasScanTableRow: true,
    bodyText: '命中 12 只',
    mainText: '命中 12 只',
  }), 'content');
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    hasForm: true,
    scanHitCount: 0,
    hasScanEmpty: true,
    bodyText: '命中 0 只 当前条件无命中',
    mainText: '命中 0 只',
  }), 'empty');
  assert.equal(classifyPageReady({
    path: '/screener',
    heading: '选股扫描',
    hasForm: true,
    hasScanIdle: true,
    bodyText: '设定条件，开始一次扫描',
    mainText: '设定条件，开始一次扫描',
  }), 'idle');
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

test('真实 DOM：长说明/长错误/卡片/零命中/非默认语言', () => {
  const longCopy = '跟踪自选股的价格、走势与市场信号。本页说明很长，其中提到暂无统一口径，但不能据此当作清单空结果。';
  const emptyWatch = makeDocument({
    heading: '自选观察',
    regions: [{
      name: 'watchlist',
      state: 'empty',
      label: '自选列表',
      text: `清单还是空的 ${longCopy}`,
    }],
    extra: [node('p', {}, [], longCopy)],
  });
  assert.equal(classifyDocument(emptyWatch, '/watchlist'), 'empty');

  const longError = makeDocument({
    heading: 'NVDA',
    regions: [{
      name: 'stock',
      state: 'error',
      text: '行情服务暂不可用。这是一段很长的错误说明，包含暂无完整快照、请稍后重试等句子。',
    }],
    bodyText: 'STOCK · $NVDA 行情服务暂不可用。这是一段很长的错误说明，包含暂无完整快照。',
  });
  assert.equal(classifyDocument(longError, '/stock/NVDA'), 'error');

  const cards = makeDocument({
    heading: '自选观察',
    regions: [{
      name: 'watchlist',
      state: 'content',
      label: '自选列表',
      text: '4 只（默认关注）',
      children: [
        node('div', { 'data-watch-ticker': 'AAPL' }, [], 'AAPL Apple'),
        node('div', { 'data-watch-ticker': 'MSFT' }, [], 'MSFT 暂无行情'),
      ],
    }],
    extra: [node('p', {}, [], '暂无行情：MSFT（不在当前覆盖范围内，可在个股页手动获取）')],
  });
  assert.equal(classifyDocument(cards, '/watchlist'), 'content');
  assert.equal(snapshotFromDocument(cards, '/watchlist').hasWatchCards, true);

  const zeroHits = makeDocument({
    heading: '选股扫描',
    regions: [{
      name: 'screener',
      state: 'empty',
      label: '扫描结果',
      text: '命中 0 只 当前条件无命中',
    }],
  });
  assert.equal(classifyDocument(zeroHits, '/screener'), 'empty');
  assert.equal(snapshotFromDocument(zeroHits, '/screener').scanHitCount, 0);

  const englishIdle = makeDocument({
    heading: 'Screener',
    regions: [{
      name: 'screener',
      state: 'idle',
      label: 'Scan results',
      text: 'Set your filters and run a scan',
    }],
  });
  assert.equal(classifyDocument(englishIdle, '/screener'), 'idle');

  const englishEmptyWatch = makeDocument({
    heading: 'Your watchlist',
    regions: [{
      name: 'watchlist',
      state: 'empty',
      label: 'Watchlist',
      text: 'Your watchlist is empty',
    }],
  });
  assert.equal(classifyDocument(englishEmptyWatch, '/watchlist'), 'empty');
});

test('业务区标记优先，长 main 文本不能冒充 content；idle 是终态', () => {
  assert.equal(classifyPageReady({
    path: '/market',
    heading: '大盘强弱',
    bodyText: 'x'.repeat(200),
    mainText: 'x'.repeat(200),
    hasForm: true,
  }), 'shell');
  assert.equal(classifyPageReady({
    path: '/watchlist',
    heading: '自选观察',
    regionState: 'content',
    bodyText: '暂无行情：XYZ',
    hasWatchCards: true,
  }), 'content');
  assert.equal(isTerminalReady('idle'), true);
  assert.equal(isTerminalReady('shell'), false);
});

test('注入脚本自包含，不依赖模块闭包', () => {
  const doc = makeDocument({
    heading: 'Your watchlist',
    regions: [{
      name: 'watchlist',
      state: 'content',
      label: 'Watchlist',
      children: [node('div', { 'data-watch-ticker': 'AAPL' }, [], 'AAPL')],
    }],
  });
  const sandbox = { document: doc, window: {}, result: null };
  sandbox.window = sandbox;
  vm.runInNewContext(`${pageReadyInstallScript()}; result = window.__optixPageReadyClass('/watchlist');`, sandbox);
  assert.equal(sandbox.result, 'content');
});

test('measure_pages 按类报告，不再把错误文案写进成功耗时', async () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const pages = await readFile(path.resolve(here, '../../scripts/perf/measure_pages.mjs'), 'utf8');
  assert.match(pages, /ready_class/);
  assert.match(pages, /content_p75/);
  assert.match(pages, /idle_n/);
  assert.match(pages, /error_rate/);
  assert.match(pages, /pageReadyInstallScript/);
  assert.doesNotMatch(pages, /行情服务暂不可用\|请求较频繁\|登录状态已失效/);
  assert.doesNotMatch(pages, /button, form, input/);
});
