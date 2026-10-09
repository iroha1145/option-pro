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

test('2026-10-08 第二版文案：新标题与新空态、错误态仍能判出终态', () => {
  const base = { hasForm: true, hasWatchCards: false, hasWatchTableRow: false, hasScanTableRow: false, hasIndexCards: false };
  assert.equal(classifyPageReady({ ...base, path: '/watchlist', heading: '我的关注', bodyText: '我的关注 暂无关注' }), 'empty');
  assert.equal(classifyPageReady({ ...base, path: '/watchlist', heading: '我的关注', bodyText: '我的关注 关注读取失败' }), 'error');
  assert.equal(classifyPageReady({ ...base, path: '/market', heading: '美股概况', bodyText: '美股概况 暂无指数行情' }), 'empty');
  assert.equal(classifyPageReady({ ...base, path: '/sectors', heading: '行业表现', bodyText: '行业表现 暂无行业目录' }), 'empty');
  assert.equal(classifyPageReady({ ...base, path: '/stock/ZZZZ', heading: 'ZZZZ', bodyText: '未找到该股票' }), 'empty');
  assert.equal(classifyPageReady({ ...base, path: '/stock/AAPL', heading: 'AAPL', bodyText: '请求过于频繁' }), 'error');
  assert.equal(classifyPageReady({ ...base, path: '/earnings', heading: '财报日历', bodyText: '财报日历 未来 30 天暂无财报' }), 'empty');
  assert.equal(classifyPageReady({ ...base, path: '/cta', heading: 'CTA 趋势资金', bodyText: 'CTA 趋势资金 首次估算完成后自动显示' }), 'empty');
  /* hasNotFound 必须由文档文本算出来：直接传 hasNotFound: true 会绕过 snapshotFromDocument 里的「无此页面」匹配。 */
  const notFoundDoc = makeDocument({
    heading: '页面不存在',
    extra: [node('p', {}, [], '没有找到 /nowhere 对应的页面。链接可能已失效或地址输入有误。')],
  });
  const notFound = snapshotFromDocument(notFoundDoc, '/this-page-is-not-a-route');
  assert.equal(notFound.hasNotFound, true, '正文含「无此页面」');
  assert.equal(classifyPageReady(notFound), 'empty');
  assert.equal(
    classifyDocument(makeDocument({ heading: '加载中' }), '/this-page-is-not-a-route'),
    'pending',
    '正文没有这句话就不能判成空态',
  );
  const screener = node('main', {}, [node('section', { 'aria-label': '筛选结果' }, [], '没有符合条件的股票')]);
  const snap = snapshotFromDocument({ querySelector: (sel) => queryAll(screener, sel)[0] ?? null, querySelectorAll: (sel) => queryAll(screener, sel), body: screener }, '/screener');
  assert.equal(snap.hasScanEmpty, true);
});

test('第二版新增的识别分支：快照字段与终态都从假文档的文本推导，不手填', () => {
  const paragraph = (text) => node('p', {}, [], text);
  const section = (label, text = '', children = []) => node('section', { 'aria-label': label }, children, text);
  const docOf = (heading, ...extra) => makeDocument({ heading, extra });

  /* 条件选股：结果区的无障碍名是「筛选结果」，还没开始扫描时写「设置条件，开始扫描」 */
  const idle = docOf('条件选股', section('筛选结果', '设置条件，开始扫描'));
  assert.equal(snapshotFromDocument(idle, '/screener').hasScanIdle, true);
  assert.equal(classifyDocument(idle, '/screener'), 'idle');
  const scanning = docOf('条件选股', section('筛选结果', '正在扫描'));
  assert.equal(snapshotFromDocument(scanning, '/screener').hasScanIdle, false, '换一句话就不是 idle');

  /* 美股概况：指数区的无障碍名是「市场指数」，指数卡是区内的按钮 */
  const indices = (label) => docOf('美股概况', section(label, '', [node('button', {}, [], '标普 500')]));
  assert.equal(snapshotFromDocument(indices('市场指数'), '/market').hasIndexOverview, true);
  assert.equal(classifyDocument(indices('市场指数'), '/market'), 'content');
  assert.equal(snapshotFromDocument(indices('别的区块'), '/market').hasIndexOverview, false, '换个无障碍名就找不到指数区');
  assert.equal(classifyDocument(indices('别的区块'), '/market'), 'shell');

  /* 我的关注：清单区的无障碍名是「关注列表」，只认这个区里的表格行 */
  const tableRow = () => node('table', {}, [node('tbody', {}, [node('tr', {}, [node('td', {}, [], 'AAPL')])])]);
  const watchlist = docOf('我的关注', section('关注列表', '', [tableRow()]));
  assert.equal(snapshotFromDocument(watchlist, '/watchlist').hasWatchTableRow, true, '关注列表区被找到，区内的行算数');
  assert.equal(classifyDocument(watchlist, '/watchlist'), 'content');
  const strayRow = docOf('我的关注', section('关注列表', '暂无关注'), section('行情提示', '', [tableRow()]));
  assert.equal(snapshotFromDocument(strayRow, '/watchlist').hasWatchTableRow, false, '关注列表之外的表格行不算');
  assert.equal(classifyDocument(strayRow, '/watchlist'), 'empty');

  /* 三个页面的文本终态；同一页面换一句话只算壳，说明结果确实来自这句文案 */
  for (const [path, heading, text, expected] of [
    ['/breakouts', '突破雷达', '信号读取失败', 'error'],
    ['/sectors', '行业表现', '行业目录加载失败', 'error'],
    ['/cta', 'CTA 趋势资金', '指数概况', 'content'],
  ]) {
    assert.equal(classifyDocument(docOf(heading, paragraph(text)), path), expected, `${path} 含「${text}」`);
    assert.equal(classifyDocument(docOf(heading, paragraph('正在读取')), path), 'shell', `${path} 没有这句话时只算壳`);
  }
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
    heading: '美股大盘强弱',
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
