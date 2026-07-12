/* Optix Pro 预览 — 真实数据层
   所有数据来自同源生产接口(/api/*)。
   本层职责:取数、TTL 缓存、并发去重、形态规整;不做任何伪造 —— 接口没有的字段一律留空。 */
(function () {
  "use strict";

  const cache = new Map();     // key → { at, ttl, data }
  const inflight = new Map();  // key → Promise

  function appToken() {
    let token = "";
    try {
      token = sessionStorage.getItem("optix.app.token") || "";
    } catch (error) { /* 某些隐私模式禁用会话存储 */ }
    if (token) return token;
    try {
      const legacy = localStorage.getItem("optix.app.token") || "";
      if (legacy) {
        try { sessionStorage.setItem("optix.app.token", legacy); } catch (error) { /* 仅本标签页使用 */ }
        localStorage.removeItem("optix.app.token");
      }
      return legacy;
    } catch (error) { return ""; }
  }

  function requestHeaders(hasBody) {
    const headers = { Accept: "application/json" };
    if (hasBody) headers["Content-Type"] = "application/json";
    const token = appToken();
    if (token) headers.Authorization = "Bearer " + token;
    return headers;
  }

  /*
   * 普通数据和任务轮询使用不同的并发闸。长任务状态查询只能占用低优先级
   * 的 2 路通道，不会挤占行情、突破雷达或财报的 3 路读取通道。
   */
  function makeGate(limit) {
    let running = 0;
    const waiters = [];
    return {
      acquire(signal) {
        if (signal && signal.aborted) return Promise.reject(new DOMException("请求已取消", "AbortError"));
        if (running < limit) { running += 1; return Promise.resolve(); }
        return new Promise((resolve, reject) => {
          const waiter = { resolve, reject, aborted: false };
          if (signal) {
            waiter.onAbort = () => {
              waiter.aborted = true;
              reject(new DOMException("请求已取消", "AbortError"));
            };
            signal.addEventListener("abort", waiter.onAbort, { once: true });
          }
          waiters.push(waiter);
        });
      },
      release() {
        let next = waiters.shift();
        while (next && next.aborted) next = waiters.shift();
        if (!next) { running = Math.max(0, running - 1); return; }
        if (next.onAbort) {
          /* signal 会在 acquire 的闭包内被垃圾回收；这里只需结束排队状态。 */
          next.aborted = false;
        }
        next.resolve();
      },
      stats() { return { running, queued: waiters.filter(w => !w.aborted).length, limit }; },
    };
  }
  const normalGate = makeGate(3);
  const jobGate = makeGate(2);

  /* 各端点缓存时长(毫秒);与服务端缓存节奏对齐,避免无谓请求 */
  const TTL = [
    [/^\/api\/market\/indices/, 60e3],
    [/^\/api\/market\/status/, 60e3],
    [/^\/api\/stocks\/watchlist/, 60e3],
    [/^\/api\/stocks\/search/, 30e3],
    [/^\/api\/stocks\/[^/]+\/chart/, 300e3],
    [/^\/api\/stocks\/[^/]+\/signals/, 300e3],
    [/^\/api\/stocks\/[^/]+$/, 120e3],
    [/^\/api\/signals\//, 300e3],
    [/^\/api\/strength\/scan/, 300e3],
    [/^\/api\/strength\//, 300e3],
    [/^\/api\/breakouts\//, 30e3],
    [/^\/api\/sectors/, 600e3],
    [/^\/api\/earnings\/upcoming/, 1800e3],
    [/^\/api\/ai\/earnings-impact/, 1800e3],
    [/^\/api\/catalysts\/status/, 60e3],
    [/^\/api\/catalysts\/feed/, 120e3],
    [/^\/api\/catalysts\/tickers\//, 120e3],
    [/^\/api\/catalysts\/calendar/, 300e3],
    [/^\/api\/catalysts\/news\//, 120e3],
    [/^\/api\/options\//, 120e3],
  ];
  const ttlFor = p => { for (const [re, t] of TTL) if (re.test(p)) return t; return 60e3; };

  const abortableDelay = (ms, signal) => new Promise((resolve, reject) => {
    if (signal && signal.aborted) { reject(new DOMException("请求已取消", "AbortError")); return; }
    const timer = setTimeout(resolve, ms);
    if (signal) signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("请求已取消", "AbortError"));
    }, { once: true });
  });

  function responseError(resp, body) {
    const detail = body && (body.detail || body.message || body.error);
    const message = typeof detail === "string"
      ? detail
      : detail && typeof detail.message === "string"
        ? detail.message
        : "HTTP " + resp.status;
    const err = new Error(message);
    err.status = resp.status;
    err.code = body && (body.code || (body.error && body.error.code) || (body.detail && body.detail.code)) || null;
    err.retryable = !!(body && (body.retryable || (body.error && body.error.retryable) || (body.detail && body.detail.retryable)));
    err.retryAfter = Number(body && (
      body.retry_after_seconds || body.retry_after ||
      (body.error && (body.error.retry_after_seconds || body.error.retry_after)) ||
      (body.detail && (body.detail.retry_after_seconds || body.detail.retry_after))
    )) || Number(resp.headers.get("Retry-After")) || null;
    return err;
  }

  async function fetchJSON(path, options) {
    const opts = options || {};
    const gate = opts.lowPriority ? jobGate : normalGate;
    await gate.acquire(opts.signal);
    try {
      let resp = null, body = null;
      const attempts = opts.retry5xx === false ? 1 : 2;
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        resp = await fetch(path, {
          method: opts.method || "GET",
          headers: requestHeaders(opts.body !== undefined),
          body: opts.body,
          signal: opts.signal,
          credentials: "same-origin",
          redirect: "error",
        });
        body = null;
        try { body = await resp.json(); } catch (e) { /* 非 JSON 响应由统一错误处理 */ }
        if (resp.status >= 500 && attempt + 1 < attempts) {
          await abortableDelay(1400, opts.signal);
          continue;
        }
        break;
      }
      if (!resp.ok) throw responseError(resp, body);
      return body;
    } finally {
      gate.release();
    }
  }

  async function jget(path, opts) {
    opts = opts || {};
    const force = opts.force;
    const hit = cache.get(path);
    if (!force && !opts.noCache && hit && Date.now() - hit.at < hit.ttl) return hit.data;
    const canCoalesce = !opts.signal && !opts.noCache && !opts.lowPriority;
    if (canCoalesce && inflight.has(path)) return inflight.get(path);
    const p = fetchJSON(path, opts).then(body => {
      if (!opts.noCache) cache.set(path, { at: Date.now(), ttl: ttlFor(path), data: body });
      return body;
    }).finally(() => { if (canCoalesce) inflight.delete(path); });
    if (canCoalesce) inflight.set(path, p);
    return p;
  }

  async function jpost(path, payload, opts) {
    opts = opts || {};
    return fetchJSON(path, Object.assign({}, opts, {
      method: "POST",
      body: JSON.stringify(payload || {}),
      retry5xx: opts.retry5xx === true,
    }));
  }

  function invalidateCache(prefix) {
    for (const key of cache.keys()) if (!prefix || key.startsWith(prefix)) cache.delete(key);
  }

  const enc = encodeURIComponent;
  const qs = obj => {
    const parts = [];
    Object.keys(obj || {}).forEach(k => {
      const v = obj[k];
      if (v !== undefined && v !== null && v !== "") parts.push(enc(k) + "=" + enc(v));
    });
    return parts.length ? "?" + parts.join("&") : "";
  };

  /* ---------- 中文数字格式 ---------- */
  function cnAmount(n) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(2) + " 万亿";
    if (abs >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
    if (abs >= 1e4) return (n / 1e4).toFixed(0) + " 万";
    return Math.round(n).toLocaleString("en-US");
  }

  /* ---------- 指数名称(接口只给代码,名称为本地映射) ---------- */
  const INDEX_NAMES = {
    "^GSPC": { sym: "S&P 500", cn: "标普500指数", badge: "SPX", us: true },
    "^IXIC": { sym: "NASDAQ", cn: "纳斯达克综合指数", badge: "IXIC", us: true },
    "^DJI": { sym: "DOW", cn: "道琼斯工业平均指数", badge: "DJI", us: true },
    "^RUT": { sym: "RUSSELL 2000", cn: "罗素2000指数", badge: "RUT", us: true },
    "^VIX": { sym: "VIX", cn: "标普500波动率指数", badge: "VIX", us: true },
    "^N225": { sym: "日经 225", cn: "日经225指数", badge: "N225", us: false },
    "000001.SS": { sym: "上证综指", cn: "上证综合指数", badge: "SSE", us: false },
    "^TNX": { sym: "US 10Y", cn: "美国10年期国债收益率", badge: "US10Y", us: false },
  };
  const indexInfo = t => INDEX_NAMES[t] || { sym: t, cn: t, badge: t.replace(/^\^/, "").slice(0, 5), us: false };

  /* ---------- 图表周期(接口词汇 = K线粒度) ---------- */
  const CHART_RANGES = [
    { key: "5m", label: "5分", bars: 78 },
    { key: "15m", label: "15分", bars: 52 },
    { key: "1h", label: "1时", bars: 56 },
    { key: "1d", label: "日K", bars: 60 },
    { key: "1w", label: "周K", bars: 60 },
  ];

  /* ---------- 领域加载器 ---------- */

  async function indices() {
    const d = await jget("/api/market/indices");
    return {
      list: (d.indices || []).map(x => {
        const info = indexInfo(x.symbol);
        return { ticker: x.symbol, sym: info.sym, price: x.price, chg: x.change_percent };
      }),
      asOf: d.as_of, sourceStatus: d.source_status, dataLimited: d.data_limited,
    };
  }

  const marketStatus = () => jget("/api/market/status");

  async function watchlist(force) {
    const d = await jget("/api/stocks/watchlist", { force });
    const groups = (d.groups || []).map(g => ({
      id: g.id, name: g.name,
      stocks: (g.stocks || []).map(s => ({
        ticker: s.ticker, name: s.name || s.ticker, price: s.price,
        chg: s.change_percent == null ? 0 : s.change_percent,
        spark: Array.isArray(s.spark) ? s.spark : [],
        group: g.name, groupId: g.id,
      })),
    }));
    const seen = new Map();
    groups.forEach(g => g.stocks.forEach(s => { if (!seen.has(s.ticker)) seen.set(s.ticker, s); }));
    const flat = Array.from(seen.values()); // 跨主题去重:同一标的可属多个分组
    return { groups, flat, asOf: d.as_of, stale: !!d._stale, sourceStatus: d.source_status, attempted: d.attempted, succeeded: d.succeeded };
  }

  const stock = t => jget("/api/stocks/" + enc(t));
  const stockSignals = t => jget("/api/stocks/" + enc(t) + "/signals");
  const signalDeep = t => jget("/api/signals/stock/" + enc(t));
  const signalsMarket = () => jget("/api/signals/market");
  const strengthMarket = () => jget("/api/strength/market");
  const profiles = () => jget("/api/strength/profiles");

  async function chart(t, range, adjustment) {
    const conf = CHART_RANGES.find(r => r.key === range) || CHART_RANGES[3];
    const d = await jget("/api/stocks/" + enc(t) + "/chart" + qs({ range: conf.key, adjustment: adjustment || "raw" }));
    const bars = (d.bars || []).filter(b => b.o != null && b.c != null && b.h != null && b.l != null);
    const shown = bars.slice(-conf.bars).map(b => ({ o: b.o, h: b.h, l: b.l, c: b.c, v: b.v == null ? 0 : b.v, t: b.t, quoteOnly: !!b.quote_only }));
    return { bars: shown, all: bars.length, asOf: d.as_of, lastBarAt: d.last_bar_at, stale: !!d._stale, tz: d.exchange_timezone };
  }

  const scan = (params, force) => jget("/api/strength/scan" + qs(params), { force });
  const breakoutsCurrent = force => jget("/api/breakouts/current", { force });
  const breakoutsStatus = force => jget("/api/breakouts/status", { force });
  const breakoutsEvents = (filters, force) => jget("/api/breakouts/events" + qs(filters), { force });
  const breakoutEventDetail = id => jget("/api/breakouts/events/" + enc(id));
  const breakoutTicker = t => jget("/api/breakouts/tickers/" + enc(t));
  const sectors = () => jget("/api/sectors");
  const sectorIV = id => jget("/api/sectors/" + enc(id) + "/iv-ranking");
  const earnings = force => jget("/api/earnings/upcoming", { force });
  const earningsImpact = t => jget("/api/ai/earnings-impact/" + enc(t));
  const catalystStatus = (force, signal) => jget("/api/catalysts/status", { force, signal });
  const catalystFeed = (params, options) => jget("/api/catalysts/feed" + qs(params), options || {});
  const catalystNews = (id, params, options) => jget("/api/catalysts/news/" + enc(id) + qs(params), options || {});
  const tickerCatalysts = (t, params, options) => jget("/api/catalysts/tickers/" + enc(t) + qs(params), options || {});
  const catalystBatch = (tickers, params, options) => jpost("/api/catalysts/tickers/batch", Object.assign({ tickers }, params || {}), options || {});
  const catalystCalendar = (params, options) => jget("/api/catalysts/calendar" + qs(params), options || {});
  const catalystRefresh = options => jpost("/api/catalysts/refresh", {}, options || {}).then(body => {
    invalidateCache("/api/catalysts/");
    return body;
  });
  const createCatalystAnalysis = (id, force, options) => jpost("/api/catalysts/news/" + enc(id) + "/analysis", { force: !!force }, options || {});
  const catalystAnalysisJob = (id, options) => jget("/api/catalysts/analysis-jobs/" + enc(id), Object.assign({ noCache: true, lowPriority: true, retry5xx: false }, options || {}));
  const cancelCatalystAnalysisJob = (id, options) => jpost("/api/catalysts/analysis-jobs/" + enc(id) + "/cancel", {}, Object.assign({ lowPriority: true }, options || {}));
  const createEarningsImpactJob = (payload, options) => jpost("/api/ai/jobs/earnings-impact", payload || {}, options || {});
  const createOptionAlertsJob = (payload, options) => jpost("/api/ai/jobs/option-alerts", payload || {}, options || {});
  const aiJob = (id, options) => jget("/api/ai/jobs/" + enc(id), Object.assign({ noCache: true, lowPriority: true, retry5xx: false }, options || {}));
  const cancelAiJob = (id, options) => jpost("/api/ai/jobs/" + enc(id) + "/cancel", { confirm: true }, Object.assign({ lowPriority: true }, options || {}));
  const unusual = () => jget("/api/options/unusual");
  const expirations = t => jget("/api/options/" + enc(t) + "/expirations");
  const chain = (t, exp) => jget("/api/options/" + enc(t) + "/chain" + qs({ expiration: exp }));
  const search = q => jget("/api/stocks/search" + qs({ q }));
  const aiStock = (t, force, options) => {
    if (force && typeof force === "object") {
      options = force;
      force = false;
    }
    return jpost("/api/signals/stock/" + enc(t) + "/ai-analysis", { force: !!force }, options || {});
  };

  /* ---------- 财报周结构(美东日历日) ---------- */
  function etToday() {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    return parts; // YYYY-MM-DD
  }
  function buildWeek(list) {
    const start = new Date(etToday() + "T12:00:00Z");
    const wdNames = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
    const byDate = {};
    (list || []).forEach(e => {
      const d = e.earnings_date;
      if (!d) return;
      (byDate[d] = byDate[d] || []).push(e);
    });
    Object.values(byDate).forEach(arr => arr.sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)));
    const week = [];
    for (let i = 0; i < 7; i++) {
      const dt = new Date(start.getTime() + i * 86400e3);
      const iso = dt.toISOString().slice(0, 10);
      const key = (dt.getUTCMonth() + 1) + "." + dt.getUTCDate();
      const events = byDate[iso] || [];
      week.push({
        d: key, iso, wd: i === 0 ? "今天" : wdNames[dt.getUTCDay()], today: i === 0,
        count: events.length,
        tickers: events.length ? events.slice(0, 3).map(e => e.ticker).join(" · ") + (events.length > 3 ? " …" : "") : "暂无安排",
        events,
      });
    }
    const first = week[0], last = week[6];
    const range = (first.d.replace(".", " 月 ") + " 日 — " + last.d.replace(".", " 月 ") + " 日");
    return { week, range };
  }

  /* ---------- 文案映射(接口枚举 → 中文) ---------- */
  const LIFECYCLE_CN = {
    DISCOVERED: "已发现", WATCHING: "观察中", TRIGGERED: "已触发", CONFIRMED: "已确认",
    HOLDING: "保持中", RETESTING: "回踩中", RETEST_HELD: "回踩守住", REACCELERATING: "再加速",
    EXTENDED: "已延伸", FAILED: "突破失败", EXPIRED: "已过期",
  };
  const SETUP_CN = {
    DAILY_BASE_BREAKOUT: "日线基底突破", OPENING_RANGE_BREAKOUT: "开盘区间突破", PREMARKET_GAP: "盘前跳空",
    GAP_AND_GO: "跳空延续", GAP_HOLD: "跳空守稳", GAP_FADE: "跳空回落",
    RETEST_BREAKOUT: "回踩再突破", MOMENTUM_SPIKE: "动量异动", RECOVERY_BREAKOUT: "修复性突破",
  };
  const SESSION_CN = { premarket: "盘前", regular: "正常交易", postmarket: "盘后", closed: "已收盘" };
  const MARKET_SHAPE_CN = {
    TRENDING_UP: "趋势上行", UPTREND: "趋势上行", RANGE_UP: "震荡上行", CHOPPY: "震荡整理", RANGE: "区间震荡",
    RANGE_DOWN: "震荡下行", TRENDING_DOWN: "趋势下行", DOWNTREND: "趋势下行", VOLATILE: "高波动", UNKNOWN: "未判定",
  };
  const shapeCN = s => (s && (MARKET_SHAPE_CN[String(s).toUpperCase()] || s)) || "未判定";

  const STRENGTH_DIMS = [
    ["index_trend_score", "指数趋势"], ["market_momentum_score", "市场动量"], ["market_breadth_score", "市场广度"],
    ["market_volume_score", "量价参与"], ["risk_appetite_score", "风险偏好"], ["risk_on_spread_score", "风险开关"],
  ];

  const TOP_BREAKDOWN_CN = {
    price_overheated: "价格过热", breadth_divergence: "广度背离", options_sentiment: "期权情绪",
    volatility_turning: "波动率拐点", rates_pressure: "利率压力", credit_risk: "信用风险", positioning: "仓位拥挤",
    distribution: "派发迹象", options_crowding: "期权拥挤", earnings_reaction: "财报反应",
    relative_strength_turning: "相对强弱转弱", valuation_expectations: "估值预期", event_risk: "事件风险",
  };
  const BOTTOM_BREAKDOWN_CN = {
    panic_release: "恐慌释放", technical_reclaim: "技术收复", breadth_repair: "广度修复",
    volatility_falling: "波动回落", credit_stable: "信用企稳", rates_easing: "利率缓和", sentiment_pessimism: "情绪悲观",
    false_break_reclaim: "假跌破收复", short_covering: "空头回补", fundamental_stability: "基本面稳定",
    industry_stabilizing: "行业企稳", options_panic_falling: "期权恐慌回落", market_environment: "市场环境",
  };
  const RELATION_CN = {
    competitor: "同行竞争", supplier: "上游供应", customer: "下游客户",
    etf: "指数与基金", opposing: "反向关联", other: "其他关联",
  };
  const RELATION_ORDER = ["competitor", "supplier", "customer", "etf", "opposing", "other"];

  const PROFILE_CN = { conservative: "保守", balanced: "均衡", aggressive: "进取" };
  const TIMEFRAME_CN = { short: "短期", mid: "中期", long: "长期", all: "综合" };

  const SCAN_COMPONENT_CN = {
    technical: "技术面", breakout_quality: "突破质量", price_action: "价格结构", sector_relative: "板块相对",
    market_fit: "市场适配", option_heat: "期权热度", momentum: "动量", relative_strength: "相对强弱",
    long_trend: "长期趋势", sector_strength: "板块强度", liquidity: "流动性", volume: "量能",
    base_quality: "基底质量", breakout: "突破", trend: "趋势", value: "估值", quality: "质量",
    range_persistence: "区间持续性", profile_fit: "偏好适配", intrinsic: "内在强度", ranking: "排序评分",
  };
  const scanComponentCN = k => SCAN_COMPONENT_CN[k] || k;

  const SOURCE_STATUS_CN = { active: "正常", degraded: "降级", disabled: "未启用", skipped: "本次跳过", unavailable: "不可用", stale: "过期快照", fallback: "兜底源" };
  const srcCN = s => SOURCE_STATUS_CN[s] || s || "未知";

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
    } catch (e) { return "—"; }
  }
  function fmtDateTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
    } catch (e) { return "—"; }
  }
  function fmtET(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return new Intl.DateTimeFormat("zh-CN", { timeZone: "America/New_York", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(d) + " 美东";
    } catch (e) { return "—"; }
  }
  function ago(iso) {
    if (!iso) return "—";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!isFinite(s)) return "—";
    if (s < 90) return Math.max(1, Math.round(s)) + " 秒前";
    if (s < 5400) return Math.round(s / 60) + " 分钟前";
    if (s < 129600) return Math.round(s / 3600) + " 小时前";
    return Math.round(s / 86400) + " 天前";
  }

  window.OPTIX_NET = {
    jget, jpost, invalidateCache, cnAmount, indexInfo, INDEX_NAMES, CHART_RANGES,
    indices, marketStatus, watchlist, stock, stockSignals, signalDeep, signalsMarket, strengthMarket,
    profiles, chart, scan, breakoutsCurrent, breakoutsStatus, breakoutsEvents, breakoutEventDetail, breakoutTicker,
    sectors, sectorIV, earnings, earningsImpact, unusual, expirations, chain, search, aiStock,
    catalystStatus, catalystFeed, catalystNews, tickerCatalysts, catalystBatch, catalystCalendar, catalystRefresh,
    createCatalystAnalysis, catalystAnalysisJob, cancelCatalystAnalysisJob,
    createEarningsImpactJob, createOptionAlertsJob, aiJob, cancelAiJob,
    buildWeek, etToday,
    LIFECYCLE_CN, SETUP_CN, SESSION_CN, shapeCN, STRENGTH_DIMS, TOP_BREAKDOWN_CN, BOTTOM_BREAKDOWN_CN,
    RELATION_CN, RELATION_ORDER, PROFILE_CN, TIMEFRAME_CN, scanComponentCN, srcCN,
    fmtTime, fmtDateTime, fmtET, ago,
    _queueStats: () => ({ normal: normalGate.stats(), jobs: jobGate.stats() }),
  };
})();
