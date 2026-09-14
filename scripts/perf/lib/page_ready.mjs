/**
 * Laboratory ready classes. Do not mix error-state timing into content-ready.
 *
 * pending — 还没有可判定的壳
 * shell   — 标题/表单已出，业务结果未到
 * empty   — 合法空结果（未扫描、无命中、无个股数据）
 * error   — 失败/限流/未授权界面已可交互
 * content — 真实业务数据可看可点
 *
 * 这两个函数必须自包含：pageReadyInstallScript 用 Function#toString 注入页面，
 * 不能闭包模块级常量。
 */
export const READY_CLASSES = ['pending', 'shell', 'empty', 'error', 'content'];

export function snapshotFromDocument(doc, path) {
  const heading = doc.querySelector('h1')?.textContent || '';
  const main = doc.querySelector('main');
  const results = [...(doc.querySelectorAll('[aria-label]') || [])]
    .find((node) => /扫描结果|Scan results/i.test(node.getAttribute('aria-label') || ''));
  const bodyText = doc.body?.innerText || doc.body?.textContent || '';
  const mainText = main?.innerText || main?.textContent || '';
  const resultsText = results?.innerText || results?.textContent || '';
  return {
    path,
    heading,
    bodyText,
    mainText,
    ariaBusy: !!doc.querySelector('[aria-busy="true"]'),
    hasForm: !!doc.querySelector('form, input, button'),
    hasTable: !!doc.querySelector('table'),
    hasScanHits: /命中\s*\d+\s*只|Hit[s]?\s+\d+/i.test(resultsText),
    hasScanEmpty: /设定条件，开始一次扫描|暂无股票符合当前条件|Set your filters and run a scan|No stocks match/i.test(resultsText),
    hasScanTableRow: !!(results && results.querySelector('table tbody tr, [data-ticker]')),
    hasIndexOverview: !!(doc.querySelector('[aria-label="指数概览"]') || doc.querySelector('[aria-label="Index overview"]')),
    hasQuote: !!doc.querySelector('[data-quote-symbol], [aria-label*="K 线"], [aria-label*="candlestick"], [aria-label*="K-line"]'),
    hasNotFound: /页面不存在|Page not found/i.test(bodyText),
  };
}

export function classifyPageReady(snapshot) {
  const path = snapshot.path || '';
  const heading = snapshot.heading || '';
  const body = snapshot.bodyText || '';
  const main = snapshot.mainText || '';
  if (path === '/screener') {
    if (!/选股|Screener/i.test(heading)) return 'pending';
    if (snapshot.hasScanTableRow || snapshot.hasScanHits) return 'content';
    if (snapshot.hasScanEmpty) return 'empty';
    if (snapshot.hasForm) return 'shell';
    return 'pending';
  }
  if (path.startsWith('/stock/')) {
    if (snapshot.ariaBusy) return 'pending';
    if (/行情服务暂不可用|请求较频繁|登录状态已失效|Quote service unavailable|Too many requests|Session expired/i.test(body)) return 'error';
    if (/该标的暂无完整数据|该股票暂无数据|代码不存在|No complete data|No data for this stock|Unknown ticker/i.test(body)) return 'empty';
    const symbol = path.slice('/stock/'.length);
    if (symbol && body.includes(symbol) && snapshot.hasQuote) return 'content';
    if (symbol && body.includes(symbol) && main.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/') {
    if (!/首页|Home/i.test(heading)) return 'pending';
    if (snapshot.hasIndexOverview && main.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/watchlist') {
    if (!/自选|watchlist/i.test(heading)) return 'pending';
    if (snapshot.hasTable) return 'content';
    if (/清单还是空的|Your watchlist is empty|暂无|还没有/.test(body)) return 'empty';
    return 'shell';
  }
  if (path === '/market') {
    if (!/大盘|Market/i.test(heading)) return 'pending';
    if (main.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/breakouts') {
    if (!/突破|雷达|Breakout|Radar/i.test(heading)) return 'pending';
    if (main.length > 40) return 'content';
    return 'shell';
  }
  if (path === '/earnings') {
    if (!/财报|Earnings/i.test(heading)) return 'pending';
    if (snapshot.hasTable || main.length > 40) return 'content';
    return 'shell';
  }
  if (path === '/sectors') {
    if (!/板块|Sectors/i.test(heading)) return 'pending';
    if (main.length > 40) return 'content';
    return 'shell';
  }
  if (path === '/login') {
    if (snapshot.hasForm || /已登录|管理员|Signed in|Admin/i.test(body)) return 'content';
    return 'pending';
  }
  if (path === '/cta') {
    if (!/CTA|趋势资金/i.test(heading)) return 'pending';
    if (/CTA 估算读取失败|failed to read/i.test(body)) return 'error';
    if (/CTA 估算尚未生成|暂无数据|not generated yet/i.test(body)) return 'empty';
    if (main.length > 80) return 'content';
    return 'shell';
  }
  if (path === '/this-page-is-not-a-route') {
    return snapshot.hasNotFound ? 'empty' : 'pending';
  }
  return main.length > 40 ? 'content' : 'pending';
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
