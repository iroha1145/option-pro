/**
 * Laboratory ready classes. Do not mix error-state timing into content-ready.
 *
 * pending — 还没有可判定的壳
 * shell   — 标题/表单已出，业务结果未到
 * idle    — 合法未开始（选股未扫描）；从数据加载成绩中剥离
 * empty   — 合法空结果（无自选、命中 0 只、无个股数据）
 * error   — 失败/限流/未授权界面已可交互
 * content — 真实业务数据可看可点
 *
 * 这两个函数必须自包含：pageReadyInstallScript 用 Function#toString 注入页面，
 * 不能闭包模块级常量。
 */
export const READY_CLASSES = ['pending', 'shell', 'idle', 'empty', 'error', 'content'];

export function snapshotFromDocument(doc, path) {
  const heading = doc.querySelector('h1')?.textContent || '';
  const main = doc.querySelector('main');
  const results = [...(doc.querySelectorAll('[aria-label]') || [])]
    .find((node) => /扫描结果|Scan results|スキャン結果/i.test(node.getAttribute('aria-label') || ''));
  const watchList = [...(doc.querySelectorAll('[aria-label]') || [])]
    .find((node) => /自选列表|Watchlist|ウォッチリスト/i.test(node.getAttribute('aria-label') || ''));
  const bodyText = doc.body?.innerText || doc.body?.textContent || '';
  const mainText = main?.innerText || main?.textContent || '';
  const resultsText = results?.innerText || results?.textContent || '';
  let regionName = '';
  if (path === '/') regionName = 'home-indices';
  else if (path === '/watchlist') regionName = 'watchlist';
  else if (path === '/screener') regionName = 'screener';
  else if (path.startsWith('/stock/')) regionName = 'stock';
  else if (path === '/market') regionName = 'market-indices';
  else if (path === '/breakouts') regionName = 'breakouts';
  else if (path === '/earnings') regionName = 'earnings';
  else if (path === '/sectors') regionName = 'sectors';
  else if (path === '/cta') regionName = 'cta';
  else if (path === '/login') regionName = 'login';
  else if (path === '/this-page-is-not-a-route') regionName = 'notfound';
  const regionEl = regionName
    ? doc.querySelector(`[data-optix-region="${regionName}"]`)
    : doc.querySelector('[data-optix-region]');
  const regionState = regionEl?.getAttribute('data-optix-state') || '';
  const watchScope = watchList || regionEl;
  const labeled = /(?:命中|Hit[s]?|Matches)\s+(\d+)/i.exec(resultsText);
  const counted = /(\d+)\s*(?:只|hits?|matches?|銘柄)/i.exec(resultsText);
  const rawHits = labeled?.[1] ?? counted?.[1];
  const parsedHits = rawHits == null ? null : Number(rawHits);
  return {
    path,
    heading,
    bodyText,
    mainText,
    regionState,
    ariaBusy: !!doc.querySelector('[aria-busy="true"]'),
    hasForm: !!doc.querySelector('form, input, button'),
    hasWatchCards: !!doc.querySelector('[data-watch-ticker]'),
    hasWatchTableRow: !!(watchScope && watchScope.querySelector('table tbody tr')),
    hasScanHits: false,
    scanHitCount: Number.isFinite(parsedHits) ? parsedHits : null,
    hasScanIdle: /设定条件，开始一次扫描|Set your filters and run a scan|条件を設定してスキャンを開始/i.test(resultsText),
    hasScanEmpty: /当前条件无命中|暂无股票符合当前条件|No matches for the current filters|No stocks match|現在の条件に一致する銘柄がありません/i.test(resultsText),
    hasScanTableRow: !!(results && results.querySelector('table tbody tr, [data-ticker]')),
    hasScanError: /扫描失败|扫描数据不可用|Scan failed|スキャンに失敗/i.test(resultsText),
    hasIndexOverview: !!(doc.querySelector('[aria-label="指数概览"]') || doc.querySelector('[aria-label="Index overview"]') || doc.querySelector('[aria-label="指数概要"]')),
    hasIndexCards: !!doc.querySelector('[data-optix-region="home-indices"] a, [data-optix-region="market-indices"] button'),
    hasNewsArticle: !!doc.querySelector('article h3'),
    hasQuote: !!doc.querySelector('[data-quote-symbol], [aria-label*="K 线"], [aria-label*="candlestick"], [aria-label*="K-line"]'),
    hasNotFound: /页面不存在|Page not found|ページが存在しません/i.test(bodyText),
  };
}

export function classifyPageReady(snapshot) {
  const path = snapshot.path || '';
  const heading = snapshot.heading || '';
  const body = snapshot.bodyText || '';
  const region = snapshot.regionState || '';
  const headingReady = () => {
    if (path === '/screener') return /选股|Screener|スクリーナー/i.test(heading);
    if (path === '/') return /首页|Home|ホーム/i.test(heading);
    if (path === '/watchlist') return /自选|watchlist|ウォッチリスト/i.test(heading);
    if (path === '/market') return /大盘|Market|地合い/i.test(heading);
    if (path === '/breakouts') return /突破|雷达|Breakout|Radar|ブレイクアウト/i.test(heading);
    if (path === '/earnings') return /财报|Earnings|決算/i.test(heading);
    if (path === '/sectors') return /板块|Sectors|セクター|透視/i.test(heading);
    if (path === '/cta') return /CTA|趋势资金|トレンド資金/i.test(heading);
    if (path === '/catalysts') return /新闻|催化|Catalyst|ニュース/i.test(heading);
    return Boolean(heading);
  };
  if (region === 'content' || region === 'empty' || region === 'error' || region === 'idle') {
    return region;
  }
  if (region === 'loading') {
    return headingReady() ? 'shell' : 'pending';
  }
  if (path === '/screener') {
    if (!headingReady()) return 'pending';
    const hits = snapshot.scanHitCount;
    if (snapshot.hasScanError) return 'error';
    if (hits != null && hits > 0) return 'content';
    if (hits === 0 || snapshot.hasScanEmpty) return 'empty';
    if (snapshot.hasScanIdle) return 'idle';
    if (snapshot.hasForm) return 'shell';
    return 'pending';
  }
  if (path.startsWith('/stock/')) {
    if (snapshot.ariaBusy) return 'pending';
    if (/行情服务暂不可用|请求较频繁|登录状态已失效|Quote service unavailable|Too many requests|Session expired/i.test(body)) return 'error';
    if (/该标的暂无完整数据|该股票暂无数据|代码不存在|No complete data|No data for this stock|Unknown ticker/i.test(body)) return 'empty';
    const symbol = path.slice('/stock/'.length);
    if (symbol && body.includes(symbol) && snapshot.hasQuote) return 'content';
    return 'shell';
  }
  if (path === '/') {
    if (!headingReady()) return 'pending';
    if (/数据暂不可用|加载失败|Failed to load|データを取得できません/i.test(body) && !snapshot.hasIndexCards) return 'error';
    if (snapshot.hasIndexOverview && snapshot.hasIndexCards) return 'content';
    if (/暂无指数数据|No index data/i.test(body)) return 'empty';
    return 'shell';
  }
  if (path === '/watchlist') {
    if (!headingReady()) return 'pending';
    if (/自选读取失败|Watchlist failed|ウォッチリストの読み込みに失敗/i.test(body)) return 'error';
    if (snapshot.hasWatchCards || snapshot.hasWatchTableRow) return 'content';
    if (/清单还是空的|Your watchlist is empty|ウォッチリストは空です/i.test(body)) return 'empty';
    return 'shell';
  }
  if (path === '/market') {
    if (!headingReady()) return 'pending';
    if (/数据暂不可用|加载失败|Failed to load/i.test(body) && !snapshot.hasIndexCards) return 'error';
    if (snapshot.hasIndexOverview && snapshot.hasIndexCards) return 'content';
    if (/暂无指数数据|No index data/i.test(body)) return 'empty';
    return 'shell';
  }
  if (path === '/breakouts') {
    if (!headingReady()) return 'pending';
    if (/信号加载失败|扫描数据暂不可用|Failed to load signals/i.test(body)) return 'error';
    if (/本轮暂无突破信号|没有符合筛选的信号|No breakout signals/i.test(body)) return 'empty';
    if (/个活跃|active signals/i.test(body) && /当日信号|Today/.test(body)) return 'content';
    return 'shell';
  }
  if (path === '/earnings') {
    if (!headingReady()) return 'pending';
    if (/财报列表不可用|日历数据不可用|Earnings (list )?unavailable/i.test(body)) return 'error';
    if (/近一个月暂无财报|No upcoming earnings/i.test(body)) return 'empty';
    if (snapshot.hasScanTableRow || /重点公司|Featured/i.test(body)) return 'content';
    return 'shell';
  }
  if (path === '/sectors') {
    if (!headingReady()) return 'pending';
    if (/板块目录加载失败|Failed to load sectors/i.test(body)) return 'error';
    if (/暂无板块目录|No sector catalog/i.test(body)) return 'empty';
    if (/板块总览|Sector overview|セクター/i.test(body) && /IV|强度|Strength/i.test(body)) return 'content';
    return 'shell';
  }
  if (path === '/login') {
    if (snapshot.hasForm || /已登录|管理员|Signed in|Admin|ログイン済み/i.test(body)) return 'content';
    return 'pending';
  }
  if (path === '/cta') {
    if (!headingReady()) return 'pending';
    if (/CTA 估算读取失败|failed to read/i.test(body)) return 'error';
    if (/CTA 估算尚未生成|暂无数据|not generated yet/i.test(body)) return 'empty';
    if (/指数总览|指数详情|Overview/i.test(body)) return 'content';
    return 'shell';
  }
  if (path === '/catalysts') {
    if (!headingReady()) return 'pending';
    if (/新闻流不可用|加载失败|Failed to load/i.test(body) && !snapshot.hasNewsArticle) return 'error';
    if (snapshot.hasNewsArticle) return 'content';
    if (/暂无新闻|No news|ニュースがありません/i.test(body)) return 'empty';
    return 'shell';
  }
  if (path === '/this-page-is-not-a-route') {
    return snapshot.hasNotFound ? 'empty' : 'pending';
  }
  return 'pending';
}

export function classifyDocument(doc, path) {
  return classifyPageReady(snapshotFromDocument(doc, path));
}

export function isTerminalReady(kind) {
  return kind === 'content' || kind === 'empty' || kind === 'error' || kind === 'idle';
}

export function pageReadyInstallScript() {
  return `window.__optixSnapshotPage = ${snapshotFromDocument.toString()};
window.__optixClassifyPageReady = ${classifyPageReady.toString()};
window.__optixPageReadyClass = function(path) {
  return window.__optixClassifyPageReady(window.__optixSnapshotPage(document, path));
};`;
}
