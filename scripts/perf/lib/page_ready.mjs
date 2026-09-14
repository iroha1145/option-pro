/**
 * Laboratory ready classes. Do not mix error-state timing into content-ready.
 *
 * pending — 还没有可判定的壳
 * shell   — 标题/表单已出，业务结果未到
 * empty   — 合法空结果（未扫描、无命中、无个股数据）
 * error   — 失败/限流/未授权界面已可交互
 * content — 真实业务数据可看可点
 */
export const READY_CLASSES = ['pending', 'shell', 'empty', 'error', 'content'];

const STOCK_ERROR = /行情服务暂不可用|请求较频繁|登录状态已失效/;
const STOCK_EMPTY = /该标的暂无完整数据|该股票暂无数据|代码不存在/;
const SCREENER_EMPTY = /设定条件，开始一次扫描|暂无股票符合当前条件/;
const SCREENER_HITS = /命中\s*\d+\s*只/;
const WATCHLIST_EMPTY = /暂无|空|还没有/;

export function snapshotFromDocument(doc, path) {
  const heading = doc.querySelector?.('h1')?.textContent || '';
  const main = doc.querySelector?.('main');
  const results = doc.querySelector?.('[aria-label="扫描结果"]');
  const bodyText = doc.body?.innerText || doc.body?.textContent || '';
  const mainText = main?.innerText || main?.textContent || '';
  return {
    path,
    heading,
    bodyText,
    mainText,
    ariaBusy: !!doc.querySelector?.('[aria-busy="true"]'),
    hasForm: !!doc.querySelector?.('form, input, button'),
    hasTable: !!doc.querySelector?.('table'),
    hasScanHits: !!(results && SCREENER_HITS.test(results.innerText || results.textContent || '')),
    hasScanEmpty: !!(results && SCREENER_EMPTY.test(results.innerText || results.textContent || '')),
    hasScanTableRow: !!results?.querySelector?.('table tbody tr, [data-ticker]'),
    hasIndexOverview: !!doc.querySelector?.('[aria-label="指数概览"]'),
    hasQuote: !!doc.querySelector?.('[data-quote-symbol], [aria-label*="K 线"]'),
    hasNotFound: /页面不存在/.test(bodyText),
  };
}

export function classifyPageReady(snapshot) {
  const path = snapshot.path || '';
  if (path === '/screener') {
    if (!snapshot.heading.includes('选股')) return 'pending';
    if (snapshot.hasScanTableRow || snapshot.hasScanHits) return 'content';
    if (snapshot.hasScanEmpty) return 'empty';
    if (snapshot.hasForm) return 'shell';
    return 'pending';
  }
  if (path.startsWith('/stock/')) {
    if (snapshot.ariaBusy) return 'pending';
    if (STOCK_ERROR.test(snapshot.bodyText)) return 'error';
    if (STOCK_EMPTY.test(snapshot.bodyText)) return 'empty';
    const symbol = path.slice('/stock/'.length);
    if (symbol && snapshot.bodyText.includes(symbol) && snapshot.hasQuote) return 'content';
    if (symbol && snapshot.bodyText.includes(symbol) && snapshot.mainText.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/') {
    if (!snapshot.heading.includes('首页')) return 'pending';
    if (snapshot.hasIndexOverview && snapshot.mainText.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/watchlist') {
    if (!snapshot.heading.includes('自选')) return 'pending';
    if (snapshot.hasTable) return 'content';
    if (WATCHLIST_EMPTY.test(snapshot.bodyText)) return 'empty';
    return 'shell';
  }
  if (path === '/market') {
    if (!snapshot.heading.includes('大盘')) return 'pending';
    if (snapshot.mainText.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/breakouts') {
    if (!/突破|雷达/.test(snapshot.heading)) return 'pending';
    if (snapshot.mainText.length > 40) return 'content';
    return 'shell';
  }
  if (path === '/earnings') {
    if (!snapshot.heading.includes('财报')) return 'pending';
    if (snapshot.hasTable || snapshot.mainText.length > 40) return 'content';
    return 'shell';
  }
  if (path === '/sectors') {
    if (!snapshot.heading.includes('板块')) return 'pending';
    if (snapshot.mainText.length > 40) return 'content';
    return 'shell';
  }
  if (path === '/login') {
    if (snapshot.hasForm || /已登录|管理员/.test(snapshot.bodyText)) return 'content';
    return 'pending';
  }
  if (path === '/cta') {
    if (!/CTA|趋势资金/.test(snapshot.heading)) return 'pending';
    if (/CTA 估算读取失败/.test(snapshot.bodyText)) return 'error';
    if (/CTA 估算尚未生成|暂无数据/.test(snapshot.bodyText)) return 'empty';
    if (snapshot.mainText.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/this-page-is-not-a-route') {
    return snapshot.hasNotFound ? 'empty' : 'pending';
  }
  return snapshot.mainText.length > 40 ? 'content' : 'pending';
}

export function classifyDocument(doc, path) {
  return classifyPageReady(snapshotFromDocument(doc, path));
}

export function isTerminalReady(kind) {
  return kind === 'content' || kind === 'empty' || kind === 'error';
}

export function pageReadyInstallScript() {
  return `window.__optixSnapshotPage = ${snapshotFromDocument.toString()};
window.__optixClassifyPageReady = ${classifyPageReady.toString()};
window.__optixPageReadyClass = function(path) {
  return window.__optixClassifyPageReady(window.__optixSnapshotPage(document, path));
};`;
}
