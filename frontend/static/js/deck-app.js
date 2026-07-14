/* Optix Pro 夜间观测台 — 渲染层(生产数据版)
   数据全部来自 OPTIX_NET(同源 /api/*);接口没有的能力一律留空并如实标注。 */
(function () {
  "use strict";
  const N = window.OPTIX_NET;
  const C = window.OPTIX_CATALYSTS;
  const Jobs = window.OPTIX_AI_JOBS;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const view = $("#view");
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const privateActionsAvailable = () => !!(N && N.hasAppToken && N.hasAppToken());

  /* ---------- 格式化(全部空值安全) ---------- */
  const isNum = v => typeof v === "number" && isFinite(v);
  const fmt = n => !isNum(n) ? "—" : (Math.abs(n) >= 1000 ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : n.toFixed(2));
  const sign = v => !isNum(v) ? "—" : (v > 0 ? "+" : "") + v.toFixed(2);
  const pct = v => !isNum(v) ? "—" : sign(v) + "%";
  const tone = v => !isNum(v) ? "dim" : v > 0 ? "u" : v < 0 ? "d" : "dim";
  const arrow = v => !isNum(v) ? "―" : v > 0 ? "▲" : v < 0 ? "▼" : "―";
  const rnd0 = v => isNum(v) ? Math.round(v) : null;
  const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const SIGNAL_CN = { bullish: "偏多", bearish: "偏空", neutral: "中性", above: "价格在上方", below: "价格在下方", normal: "常态", elevated: "放量", low: "缩量" };
  const sigCN = s => SIGNAL_CN[s] || s || "—";

  /* ---------- SVG ---------- */
  function spark(values, w, h, chg) {
    if (!values || values.length < 2) return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"></svg>`;
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const pts = values.map((v, i) => [(i / (values.length - 1)) * (w - 4) + 2, h - 3 - ((v - min) / span) * (h - 7)]);
    const dSeg = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const col = chg >= 0 ? "var(--up)" : "var(--down)";
    const last = pts[pts.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <path d="${dSeg}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linecap="round" opacity="0.9"/>
      <circle cx="${last[0]}" cy="${last[1]}" r="2" fill="${col}"/>
    </svg>`;
  }

  function candleChart(candles, w = 660, h = 230, opts = {}) {
    if (!candles || !candles.length) return `<div class="empty-note" style="padding:34px"><p>暂无K线数据</p><small>数据源未返回该周期行情</small></div>`;
    const levels = (opts.levels || []).filter(L => isNum(L.v));
    const padR = 56, padT = 14, padB = 26;
    let lo = Math.min(...candles.map(c => c.l)), hi = Math.max(...candles.map(c => c.h));
    levels.forEach(L => { lo = Math.min(lo, L.v); hi = Math.max(hi, L.v); });
    const pad = ((hi - lo) || 1) * 0.05;
    lo -= pad; hi += pad;
    const span = (hi - lo) || 1;
    const iw = w - padR - 8, ih = h - padT - padB;
    const step = iw / candles.length, bw = Math.max(2.5, step * 0.52);
    const y = v => padT + ih - ((v - lo) / span) * ih;
    let bars = "", vols = "";
    const maxV = Math.max(...candles.map(c => c.v || 0), 1);
    candles.forEach((c, i) => {
      const x = 8 + i * step + step / 2;
      const up = c.c >= c.o;
      const col = up ? "var(--up)" : "var(--down)";
      bars += `<g class="candle" style="--i:${i}">
        <line x1="${x}" x2="${x}" y1="${y(c.h)}" y2="${y(c.l)}" stroke="${col}" stroke-width="1" opacity="0.85"/>
        <rect x="${x - bw / 2}" y="${Math.min(y(c.o), y(c.c))}" width="${bw}" height="${Math.max(1.5, Math.abs(y(c.o) - y(c.c)))}" rx="1" fill="${col}"/>
      </g>`;
      vols += `<rect x="${x - bw / 2}" y="${h - padB + 4 + (1 - (c.v || 0) / maxV) * 15}" width="${bw}" height="${((c.v || 0) / maxV) * 15}" rx="1" fill="${col}" opacity="0.28"/>`;
    });
    let grid = "";
    for (let g = 0; g <= 3; g++) {
      const gv = lo + (span * g) / 3, gy = y(gv);
      grid += `<line x1="8" x2="${w - padR + 6}" y1="${gy}" y2="${gy}" stroke="var(--line-soft)" stroke-width="1"/>
        <text x="${w - padR + 12}" y="${gy + 3}" font-size="9.5" fill="var(--faint)" font-family="var(--font-mono)">${fmt(gv)}</text>`;
    }
    let lvl = "";
    levels.forEach(L => {
      const ly = y(L.v);
      lvl += `<line x1="8" x2="${w - padR + 6}" y1="${ly}" y2="${ly}" stroke="${L.color}" stroke-width="1" stroke-dasharray="5 5" opacity="0.6"/>
        <rect x="14" y="${ly - 16}" width="${9.4 * (String(L.label).length / 1.62) + 18}" height="15" rx="4" fill="color-mix(in oklab, var(--surface) 82%, transparent)"/>
        <text x="21" y="${ly - 5}" font-size="9.5" fill="${L.color}" font-family="var(--font-mono)">${L.label}</text>`;
    });
    const lastC = candles[candles.length - 1].c, ly2 = y(lastC);
    const flag = `<line x1="8" x2="${w - padR + 6}" y1="${ly2}" y2="${ly2}" stroke="var(--amber-line)" stroke-width="1" stroke-dasharray="3 4"/>
      <rect x="${w - padR + 7}" y="${ly2 - 9}" width="${padR - 10}" height="18" rx="5" fill="var(--amber)"/>
      <text x="${w - padR + 7 + (padR - 10) / 2}" y="${ly2 + 3.5}" font-size="9.5" font-weight="600" fill="var(--on-amber)" text-anchor="middle" font-family="var(--font-mono)">${fmt(lastC)}</text>`;
    return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="价格走势K线图">${grid}${lvl}${vols}${bars}${flag}</svg>`;
  }

  function lineArea(values, w = 540, h = 150, id = "g1") {
    if (!values || values.length < 2) return `<div class="empty-note" style="padding:28px"><p>暂无走势数据</p></div>`;
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const pts = values.map((v, i) => [4 + (i / (values.length - 1)) * (w - 8), h - 8 - ((v - min) / span) * (h - 22)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--amber)" stop-opacity="0.20"/><stop offset="1" stop-color="var(--amber)" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${d} L ${w - 4} ${h} L 4 ${h} Z" fill="url(#${id})" class="fade-fill"/>
      <path d="${d}" fill="none" stroke="var(--amber)" stroke-width="1.7" stroke-linecap="round" class="draw-path"/>
    </svg>`;
  }

  function ring(score, size = 52, risk) {
    const val = rnd0(score);
    const r = (size - 8) / 2, c = 2 * Math.PI * r;
    const col = val == null ? "var(--faint)" : risk && val >= 60 ? "var(--down)" : "var(--amber)";
    return `<svg viewBox="0 0 ${size} ${size}" class="ring" aria-hidden="true" style="width:${size}px;height:${size}px">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--track)" stroke-width="3.5"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${col}" stroke-width="3.5" stroke-linecap="round"
        stroke-dasharray="${(c * (val == null ? 0 : val) / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 ${size / 2} ${size / 2})"/>
      <text x="50%" y="50%" dy="4" text-anchor="middle" font-size="${size * 0.28}" font-weight="600" fill="var(--ink)" font-family="var(--font-mono)">${val == null ? "—" : val}</text>
    </svg>`;
  }

  const isIndexSym = t => !!N.INDEX_NAMES[t] || /^\^/.test(String(t)) || t === "000001.SS";
  const logo = t => {
    if (isIndexSym(t)) return `<span class="tik__logo" aria-hidden="true" style="font-size:9px;letter-spacing:0">${esc(N.indexInfo(t).badge.slice(0, 3))}</span>`;
    return `<span class="tik__logo" aria-hidden="true">${esc(String(t).slice(0, 2))}</span>`;
  };
  const tik = (s, name, sub) => `<span class="tik">${logo(s)}<span><span class="tik__sym">${esc(s)}</span><br><span class="tik__name">${esc(name)}${sub ? " · " + esc(sub) : ""}</span></span></span>`;

  /* ---------- 页面级状态 ---------- */
  const St = {
    market: null,          // /api/market/status
    indices: null,
    watch: null,           // watchlist 分组
    earnWeek: null,        // 财报周结构
    unusual: null,
    strength: null,        // strength/market
    sigMarket: null,       // signals/market
    profiles: null,
    scanKey: "", scan: null,
    brkCurrent: null, brkStatus: null, brkEvents: null, brkCursor: null,
    sectors: null, sectorIV: {},
    impacts: {},           // ticker → result | {error} | "loading"
  };
  let gen = 0; // 路由代际,防陈旧渲染

  /* ---------- 通用状态块 ---------- */
  const loadingView = label => `<div class="view-loading" role="status"><span class="spinner" aria-hidden="true"></span><p>${esc(label)}</p><small>实时读取生产数据 · 冷启动扫描约需 1—2 分钟</small></div>`;
  const errorView = (label, detail) => `<div class="view-loading is-err" role="alert"><span class="spinner spinner--err" aria-hidden="true"></span><p>${esc(label)}</p><small>${esc(detail || "")}</small><button class="btn btn--sm" data-retry>重试</button></div>`;
  const missingBlock = (title, note) => `
    <div class="missing-block">
      <span class="mono">${esc(title)}</span>
      <p>${esc(note)}</p>
      <small>原版后端无此接口 · 留空不伪造</small>
    </div>`;
  const inlineErr = (label, detail) => `<div class="empty-note" style="padding:20px"><p>${esc(label)}</p><small>${esc(detail || "接口暂不可用")}</small></div>`;
  const bindRetry = fn => { const b = $("[data-retry]", view); if (b) b.addEventListener("click", fn); };
  const settle = p => p.then(v => ({ ok: true, v }), e => ({ ok: false, e }));

  const dataState = (label, warn, tip) => tip
    ? `<span class="data-state ${warn ? "is-warn" : ""} has-tip" tabindex="0"><i></i>${label}<span class="data-tip" role="tooltip">${tip}</span></span>`
    : `<span class="data-state ${warn ? "is-warn" : ""}"><i></i>${label}</span>`;

  // 数据源明细弹层:失败名单如实列出;正常时只报总数,不铺全量代码
  function srcTip({ attempted, succeeded, failedTickers, failed, stale, asOf, label }) {
    const bad = Array.isArray(failedTickers) ? failedTickers : [];
    const okCount = succeeded != null ? succeeded : Math.max((attempted || 0) - bad.length, 0);
    const rows = [];
    if (bad.length) {
      rows.push(`<b class="data-tip__bad">本轮拉取失败 ${bad.length} 只</b>`);
      rows.push(`<span class="data-tip__codes">${bad.map(t => `<i>${esc(t)}</i>`).join("")}</span>`);
      rows.push(`<small>其余 ${okCount} 只正常 · 失败标的暂缺行情,下轮刷新自动重试</small>`);
    } else if (failed > 0) {
      rows.push(`<b class="data-tip__bad">本轮拉取失败 ${failed} 只</b>`);
      rows.push(`<small>本次快照未附失败名单,下轮刷新后可见</small>`);
    } else {
      rows.push(`<b class="data-tip__ok">${attempted != null ? okCount + "/" + attempted + " " : ""}全部拉取成功</b>`);
      rows.push(`<small>${label || "数据源"}正常${asOf ? " · 更新 " + N.fmtTime(asOf) : ""}</small>`);
    }
    if (stale) rows.push(`<small class="data-tip__warn">当前为过期快照:上游暂时不可用,展示最近一次成功数据</small>`);
    return rows.join("");
  }

  /* ---------- 视图:自选 ---------- */
  let focusTicker = null, focusRange = "1d", focusAdj = "raw", watchGroup = null, watchFilter = "all";
  let watchFetchedAt = 0;
  // quiet=true 为后台静默拉取:跳过加载页、失败不打断当前内容、不重播入场动画
  async function renderWatchlist(quiet) {
    const g0 = ++gen;
    if (!quiet) view.innerHTML = loadingView("正在读取自选行情…");
    const [watch, earn, flow] = await Promise.all([
      settle(N.watchlist(!!quiet)),
      settle(N.earnings().then(d => N.buildWeek(d.earnings || []))),
      settle(N.unusual()),
    ]);
    if (g0 !== gen) return;
    if (!watch.ok) {
      if (quiet) return; // 静默轮询失败:保留现有内容,等下一轮
      view.innerHTML = errorView("自选行情读取失败", watch.e.message); bindRetry(renderWatchlist); return;
    }
    St.watch = watch.v;
    watchFetchedAt = Date.now();
    if (earn.ok) St.earnWeek = earn.v;
    if (flow.ok) St.unusual = flow.v;

    const groups = St.watch.groups;
    if (!groups.length) { view.innerHTML = errorView("自选列表为空", "接口未返回任何分组"); bindRetry(renderWatchlist); return; }
    if (!watchGroup || !groups.some(g => g.id === watchGroup)) watchGroup = groups[0].id;
    const grp = groups.find(g => g.id === watchGroup);
    const flat = St.watch.flat;
    if (!focusTicker || !flat.some(s => s.ticker === focusTicker)) {
      focusTicker = flat.some(s => s.ticker === "NVDA") ? "NVDA" : (grp.stocks[0] || flat[0]).ticker;
    }
    const f = flat.find(s => s.ticker === focusTicker) || flat[0];

    /* 焦点扩展数据(单标的,独立失败) */
    const [fq, fchart, fsig] = await Promise.all([
      settle(N.stock(f.ticker)),
      settle(N.chart(f.ticker, focusRange, focusAdj)),
      settle(N.stockSignals(f.ticker)),
    ]);
    if (g0 !== gen) return;

    const up = flat.filter(s => s.chg > 0).length;
    const avg = flat.reduce((a, s) => a + (s.chg || 0), 0) / (flat.length || 1);
    const lead = [...flat].sort((a, b) => (b.chg || 0) - (a.chg || 0))[0];
    const lag = [...flat].sort((a, b) => (a.chg || 0) - (b.chg || 0))[0];
    const breadth = Math.round((up / (flat.length || 1)) * 100);
    const shown = grp.stocks.filter(s => watchFilter === "up" ? s.chg > 0 : watchFilter === "down" ? s.chg < 0 : true);

    /* 下一事件:未来七日最先出现的 3 场财报 */
    const nextEvents = [];
    if (St.earnWeek) {
      for (const d of St.earnWeek.week) {
        for (const e of d.events) {
          nextEvents.push({ ticker: e.ticker, delta: "T+" + (e.days_until != null ? e.days_until : "?"), date: d.d.replace(".", " 月 ") + " 日", tone: nextEvents.length === 0 ? "amber" : "" });
          if (nextEvents.length >= 3) break;
        }
        if (nextEvents.length >= 3) break;
      }
    }

    const flowRows = (St.unusual && St.unusual.results || []).slice(0, 6);
    const q = fq.ok ? fq.v : null;
    const sig = fsig.ok ? fsig.v : null;

    view.innerHTML = `
    <div class="view-head" data-reveal style="--reveal-i:0">
      <div>
        <p class="view-head__kicker">01 · Watchlist</p>
        <h1>自选观察<small>${flat.length} 只标的 · ${up} 涨 ${flat.length - up} 跌 · 平均 <span class="num ${tone(avg)}">${pct(avg)}</span> · 延迟行情,仅供研究</small></h1>
      </div>
      <div class="view-head__aside">
        ${dataState(`YAHOO ${N.srcCN(St.watch.sourceStatus)} · ${St.watch.succeeded}/${St.watch.attempted} · ${N.fmtTime(St.watch.asOf)}`, St.watch.sourceStatus !== "active" || St.watch.stale, srcTip({ attempted: St.watch.attempted, succeeded: St.watch.succeeded, failedTickers: St.watch.failedTickers, failed: St.watch.failed, stale: St.watch.stale, asOf: St.watch.asOf, label: "Yahoo 行情源" }))}
      </div>
    </div>

    <div class="pulse-grid">
      <section class="panel panel--hero panel--flush" data-reveal style="--reveal-i:1" aria-label="焦点标的">
        <div class="focus-panel__top">
          <div class="focus-panel__id">
            <span class="mono">当前焦点 · ${esc(f.group)}</span>
            <h3>${esc(f.name)} <span class="dim" style="font-weight:400">${esc(f.ticker)}</span></h3>
            <button class="btn btn--sm btn--ghost" data-open="${esc(f.ticker)}">查看研究页 →</button>
          </div>
          <div class="focus-price">
            <div class="focus-price__now" data-count="${f.price}">0.00</div>
            <div class="focus-price__chg"><span class="${tone(f.chg)}">${arrow(f.chg)} ${pct(f.chg)}</span><span class="dim">${q ? sign(q.change) : ""}</span></div>
          </div>
        </div>
        <div class="focus-ranges" role="tablist" aria-label="图表周期">
          ${N.CHART_RANGES.map(r => `<button class="rangebtn ${r.key === focusRange ? "active" : ""}" data-range="${r.key}" role="tab" aria-selected="${r.key === focusRange}">${r.label}</button>`).join("")}
        </div>
        <div class="focus-chart" id="focus-chart">${fchart.ok ? candleChart(fchart.v.bars) : inlineErr("K线读取失败", fchart.e && fchart.e.message)}</div>
        <dl class="focus-stats">
          <div><dt>成交量</dt><dd>${q ? N.cnAmount(q.volume) : "—"}</dd></div>
          <div><dt>市值</dt><dd>${q && isNum(q.market_cap) ? N.cnAmount(q.market_cap) + " 美元" : "—"}</dd></div>
          <div><dt>技术评分</dt><dd>${sig && isNum(sig.score) ? sig.score + " / 100" : "—"}</dd></div>
          <div><dt>信号摘要</dt><dd style="font-family:var(--font-ui);font-size:12.5px;color:var(--ink-soft)">${sig ? "整体" + sigCN(sig.overall) + (sig.signals && sig.signals.rsi ? " · RSI " + fmt(sig.signals.rsi.value) : "") : "—"}</dd></div>
        </dl>
      </section>

      <div style="display:flex;flex-direction:column;gap:18px">
        <section class="panel panel--pad pulsebox" data-reveal style="--reveal-i:2" aria-label="今日摘要">
          <div class="sect-head" style="margin-bottom:2px"><span class="sect-head__no">PULSE</span><h2 style="font-size:14.5px">今日摘要</h2><span class="sect-head__rule"></span><span class="sect-head__meta">全部 ${flat.length} 只</span></div>
          <div>
            <div class="pulse-row"><span class="pulse-row__k">上涨宽度</span><span class="pulse-row__v">${breadth}%</span></div>
            <div class="pulse-meter" style="margin-top:8px"><i data-w="${breadth}"></i></div>
          </div>
          <button class="pulse-jump" data-focus-jump="${esc(lead.ticker)}">
            <span style="display:flex;align-items:center;gap:9px"><span class="chip chip--up"><i></i>领涨</span><b class="mono" style="font-size:13px">${esc(lead.ticker)}</b><span style="font-size:11.5px;color:var(--muted)">${esc(lead.name)}</span></span>
            <b class="num u" style="font-size:13px">${pct(lead.chg)}</b>
          </button>
          <button class="pulse-jump" data-focus-jump="${esc(lag.ticker)}">
            <span style="display:flex;align-items:center;gap:9px"><span class="chip chip--down"><i></i>承压</span><b class="mono" style="font-size:13px">${esc(lag.ticker)}</b><span style="font-size:11.5px;color:var(--muted)">${esc(lag.name)}</span></span>
            <b class="num d" style="font-size:13px">${pct(lag.chg)}</b>
          </button>
          <div class="pulse-live"><i></i>快照 ${N.fmtTime(St.watch.asOf)} · 每 75 秒自动拉取 · 延迟行情</div>
        </section>

        <section class="panel panel--pad" data-reveal style="--reveal-i:3" aria-label="下一事件">
          <div class="sect-head" style="margin-bottom:8px"><span class="sect-head__no">NEXT</span><h2 style="font-size:14.5px">下一事件 · 财报</h2><span class="sect-head__rule"></span><a class="mono" href="#earnings" style="font-size:10px;color:var(--amber);letter-spacing:.08em">日历 →</a></div>
          ${nextEvents.length ? nextEvents.map(e => `
          <div class="evt-mini">
            <span class="chip ${e.tone === "amber" ? "chip--amber" : "chip--mute"}">${esc(e.delta)}</span>
            <b class="mono" style="font-size:12.5px">${esc(e.ticker)}</b>
            <span class="evt-mini__d">${esc(e.date)}</span>
          </div>`).join("") : `<div class="empty-note" style="padding:14px"><p>未来七日暂无财报</p><small>${earn.ok ? "预设美股列表内没有安排" : "财报接口读取失败"}</small></div>`}
        </section>
      </div>
    </div>

    <section class="sect" aria-label="观察清单">
      <div class="sect-head" data-reveal style="--reveal-i:0">
        <span class="sect-head__no">LIST</span><h2>观察清单</h2>
        <span class="sect-head__rule"></span>
        <span style="display:flex;gap:6px">
          ${[["all", "全部"], ["up", "上涨"], ["down", "下跌"]].map(x => `<button class="fchip ${watchFilter === x[0] ? "active" : ""}" data-wf="${x[0]}">${x[1]}</button>`).join("")}
        </span>
        <span class="sect-head__meta">${shown.length} / ${grp.stocks.length} 只 · 点击设为焦点</span>
      </div>
      <div class="group-chips" data-reveal style="--reveal-i:0">
        ${groups.map(g => `<button class="fchip ${g.id === watchGroup ? "active" : ""}" data-wg="${esc(g.id)}">${esc(g.name)} <span style="color:var(--faint);font-size:10px">${g.stocks.length}</span></button>`).join("")}
      </div>
      <div class="stock-grid" data-reveal style="--reveal-i:1">
        ${shown.map(s => `
        <div class="stock-card ${s.ticker === focusTicker ? "selected" : ""}" data-card="${esc(s.ticker)}" role="button" tabindex="0" aria-pressed="${s.ticker === focusTicker}">
          <span class="stock-card__top"><span class="stock-card__sym">${esc(s.ticker)}</span><span class="stock-card__tag">${esc(s.group)}</span></span>
          <span class="stock-card__name">${esc(s.name)}</span>
          <span class="stock-card__quote"><b>${fmt(s.price)}</b><span class="num ${tone(s.chg)}">${pct(s.chg)}</span></span>
          <span class="stock-card__spark">${spark(s.spark, 182, 30, s.chg || 0)}</span>
          <span class="stock-card__foot"><span class="stock-card__note">近 7 个交易日走势</span><button class="stock-card__go" data-go="${esc(s.ticker)}">研究 ↗</button></span>
        </div>`).join("")}
        ${shown.length === 0 ? `<div class="empty-note" style="grid-column:1/-1;padding:24px"><p>该分组下没有匹配的标的</p><small>试试切换涨跌筛选</small></div>` : ""}
      </div>
    </section>

    <section class="sect" aria-label="期权异动">
      <div class="sect-head" data-reveal style="--reveal-i:0">
        <span class="sect-head__no">FLOW</span><h2>期权异动脉冲</h2>
        <span class="sect-head__rule"></span><span class="sect-head__meta">${flow.ok ? `${St.unusual.attempted} 个热门标的 · 快照 ${N.fmtTime(St.unusual.as_of)}` : "接口读取失败"}</span>
      </div>
      <div class="pulse-grid pulse-grid--flow">
        <div class="panel panel--flush" data-reveal style="--reveal-i:1">
          ${flow.ok && flowRows.length ? `
          <div class="flow-list">
            ${flowRows.map(x => `
            <div class="flow-item" data-open="${esc(x.ticker)}">
              <span class="flow-item__side chip ${x.contract_type === "call" ? "chip--up" : "chip--down"}">${x.contract_type === "call" ? "看涨" : "看跌"}</span>
              <span class="flow-item__desc"><b>${esc(x.ticker)}</b> ${fmt(x.strike)} ${x.contract_type === "call" ? "Call" : "Put"} · ${esc(x.expiration || "")} 到期</span>
              <span class="flow-item__ratio">成交 ${isNum(x.vol_oi_ratio) ? x.vol_oi_ratio >= 100 ? Math.round(x.vol_oi_ratio) : x.vol_oi_ratio.toFixed(1) : "—"}× 持仓</span>
              <span class="flow-item__prem">${isNum(x.premium) ? "$" + N.cnAmount(x.premium) : "—"}</span>
            </div>`).join("")}
          </div>
          <div class="panel__foot"><span class="mono" style="font-size:10px;color:var(--faint)">接口不含成交主动方向,无法判断买卖方(官方口径),故不作方向标注</span><span class="mono" style="font-size:10px;color:var(--faint)">共 ${St.unusual.results.length} 条</span></div>`
          : `<div style="padding:8px 14px 14px">${flow.ok ? inlineErr("暂无期权异动", "当前快照没有满足条件的合约") : inlineErr("期权异动读取失败", flow.e && flow.e.message)}</div>`}
        </div>
        <div class="panel ai-note" data-reveal style="--reveal-i:2">
          <div class="ai-note__rule" aria-hidden="true"></div>
          <div>
            <div class="ai-note__head"><span class="mono">AI DESK NOTE · 智能观察</span></div>
            ${missingBlock("市场级 AI 观点", "原版后端只提供「期权告警 AI 分析」「财报关联分析」两类按需接口,没有整页市场观点的生成端点;个股 AI 解读请到研究页按需生成。")}
          </div>
        </div>
      </div>
    </section>`;
    postRender();
    if (quiet) $$("[data-reveal]", view).forEach(el => el.classList.add("in")); // 静默更新:直接落定终态,不重播动画
    $$(".rangebtn[data-range]", view).forEach(b => b.addEventListener("click", async () => {
      focusRange = b.dataset.range;
      $$(".rangebtn[data-range]", view).forEach(x => { x.classList.toggle("active", x === b); x.setAttribute("aria-selected", x === b); });
      const box = $("#focus-chart");
      box.innerHTML = loadingView("读取 " + b.textContent + " K线…");
      const r = await settle(N.chart(f.ticker, focusRange, focusAdj));
      if (g0 !== gen) return;
      box.innerHTML = r.ok ? candleChart(r.v.bars) : inlineErr("K线读取失败", r.e.message);
      animateCandles();
    }));
    $$("[data-card]", view).forEach(c => {
      const pick = () => { focusTicker = c.dataset.card; renderWatchlist(); };
      c.addEventListener("click", pick);
      c.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    });
    $$("[data-go]", view).forEach(g => g.addEventListener("click", e => { e.stopPropagation(); openDrawer(g.dataset.go); }));
    $$("[data-focus-jump]", view).forEach(b => b.addEventListener("click", () => { focusTicker = b.dataset.focusJump; renderWatchlist(); }));
    $$("[data-wf]", view).forEach(b => b.addEventListener("click", () => { watchFilter = b.dataset.wf; renderWatchlist(); }));
    $$("[data-wg]", view).forEach(b => b.addEventListener("click", () => { watchGroup = b.dataset.wg; renderWatchlist(); }));
  }

  /* ---------- 视图:选股 ---------- */
  /* 区间持续性:接口返回嵌套对象(range_persistence / range_persistence_shadow),统一拍平 */
  function persistInfo(c) {
    const rp = c.range_persistence && typeof c.range_persistence === "object" ? c.range_persistence : null;
    const sh = c.range_persistence_shadow && typeof c.range_persistence_shadow === "object" ? c.range_persistence_shadow : null;
    const mode = c.range_persistence_mode || (sh && sh.mode) || null;
    const prodRaw = rp ? rp.range_persistence : (typeof c.range_persistence === "number" ? c.range_persistence : null);
    const insufficient = rp && rp.status && rp.status !== "active" && rp.status !== "ok";
    return {
      prod: isNum(prodRaw) ? prodRaw : (sh && isNum(sh.production_score) ? sh.production_score : null),
      hypo: sh && isNum(sh.hypothetical_score) ? sh.hypothetical_score : null,
      delta: isNum(c.range_persistence_score_delta) ? c.range_persistence_score_delta : (sh && isNum(sh.score_delta) ? sh.score_delta : null),
      slope: rp && isNum(rp.range_persistence_slope_5d) ? rp.range_persistence_slope_5d : null,
      ratio: rp && isNum(rp.range_persistence_ratio_10d) ? rp.range_persistence_ratio_10d : null,
      effW: sh && isNum(sh.effective_weight) ? sh.effective_weight : null,
      cap: sh && isNum(sh.contribution_cap) ? sh.contribution_cap : null,
      version: (rp && rp.version) || (sh && sh.version) || null,
      statusTxt: insufficient ? "数据不足" : mode === "shadow" ? "影子观察 · 不参与排序" : mode ? "已参与评分" : "—",
    };
  }
  const scr = { tf: "all", profile: "balanced", top: "20", minPrice: "5", minTurnover: "10", sector: "" };
  const scanParams = () => ({
    timeframe: scr.tf, profile: scr.profile, top: scr.top,
    sector_id: scr.sector || undefined,
    min_price: scr.minPrice === "" ? undefined : scr.minPrice,
    min_avg_dollar_volume: scr.minTurnover === "" ? undefined : String(parseFloat(scr.minTurnover) * 1e6),
  });
  const planText = (secName) => `当前方案:${N.PROFILE_CN[scr.profile]} · ${N.TIMEFRAME_CN[scr.tf]} · 前 ${scr.top} 只 · 股价 ≥ $${scr.minPrice || 0} · 日均额 ≥ ${(parseFloat(scr.minTurnover || 0) * 100).toLocaleString("zh-CN")} 万美元 · ${secName || "全部板块"}`;

  async function renderScreener(forceScan) {
    const g0 = ++gen;
    view.innerHTML = loadingView(forceScan ? "正在重新扫描全市场…" : "正在读取扫描结果…");
    const [prof, scanR] = await Promise.all([
      settle(N.profiles()),
      settle(N.scan(scanParams(), forceScan)),
    ]);
    if (g0 !== gen) return;
    if (!scanR.ok) { view.innerHTML = errorView("扫描接口读取失败", scanR.e.message); bindRetry(() => renderScreener()); return; }
    St.profiles = prof.ok ? prof.v : { profiles: ["balanced"], timeframes: ["all"], sectors: [] };
    St.scan = scanR.v;

    const P = St.profiles, D2 = St.scan;
    const rows = Array.isArray(D2.rows) ? D2.rows : [];
    const reg = D2.market_regime || {};
    const dsrc = D2.data_sources || {};
    const secName = scr.sector ? ((P.sectors || []).find(s => s.id === scr.sector) || {}).name : "";
    const skipped = D2.skipped || {};
    const skippedTotal = Object.values(skipped).reduce((a, b) => a + (b || 0), 0);
    const degraded = (dsrc.prices && dsrc.prices.status && dsrc.prices.status !== "active");

    const inputChips = [
      dsrc.prices && { k: "价格与成交量", p: dsrc.prices.provider, s: N.srcCN(dsrc.prices.status), warn: dsrc.prices.status !== "active", msg: dsrc.prices.message },
      dsrc.fundamentals && { k: "基本面", p: dsrc.fundamentals.provider, s: N.srcCN(dsrc.fundamentals.status), warn: dsrc.fundamentals.status === "degraded" },
      dsrc.options && { k: "期权热度", p: dsrc.options.provider, s: N.srcCN(dsrc.options.status), warn: dsrc.options.status === "degraded" },
      dsrc.range_persistence && { k: "区间持续性", p: dsrc.range_persistence.version, s: dsrc.range_persistence.status === "shadow" ? "影子观察" : N.srcCN(dsrc.range_persistence.status), warn: false },
    ].filter(Boolean);

    const dims = N.STRENGTH_DIMS.map(([key, label]) => ({ k: label, v: rnd0(reg[key]) }));
    const snapshot = [];
    if (reg.momentum && isNum(reg.momentum.spy_20d)) snapshot.push({ k: "SPY 20日", v: pct(reg.momentum.spy_20d), tone: tone(reg.momentum.spy_20d) });
    if (reg.momentum && isNum(reg.momentum.qqq_20d)) snapshot.push({ k: "QQQ 20日", v: pct(reg.momentum.qqq_20d), tone: tone(reg.momentum.qqq_20d) });
    if (reg.risk && isNum(reg.risk.vix)) snapshot.push({ k: "波动率指数", v: fmt(reg.risk.vix), tone: "" });

    const verdictOf = r => r.label || r.classification || (isNum(r.final_score) ? (r.final_score >= 72 ? "强观察" : r.final_score >= 58 ? "观察" : "暂缓") : "—");

    view.innerHTML = `
    <div class="view-head" data-reveal style="--reveal-i:0">
      <div>
        <p class="view-head__kicker">02 · Screener</p>
        <h1>从市场里,筛出值得研究的股票<small>先设条件,再看环境和候选;详细依据只在需要时展开。</small></h1>
      </div>
      <div class="view-head__aside">${dataState(`扫描 ${N.fmtTime(D2.as_of)} · 服务端缓存至 ${N.fmtTime(D2.cache_expires_at)}`, degraded)}</div>
    </div>

    <section class="panel panel--pad" data-reveal style="--reveal-i:1" aria-label="扫描设置">
      <div class="sect-head" style="margin-bottom:16px"><span class="sect-head__no">STEP 01</span><h2 style="font-size:14.5px">扫描设置 · 设定候选范围</h2><span class="sect-head__rule"></span><span class="sect-head__meta">改完条件点「重新扫描」生效</span></div>
      <div class="setup-row" style="margin-bottom:18px">
        <div class="setup-group">
          <span class="filter-row__label">观察周期</span>
          <span class="chips">${(P.timeframes || []).map(t => `<button class="fchip ${t === scr.tf ? "active" : ""}" data-k="tf" data-v="${esc(t)}">${N.TIMEFRAME_CN[t] || t}</button>`).join("")}</span>
        </div>
        <div class="setup-group">
          <span class="filter-row__label">评分偏好</span>
          <span class="chips">${(P.profiles || []).map(t => `<button class="fchip ${t === scr.profile ? "active" : ""}" data-k="profile" data-v="${esc(t)}">${N.PROFILE_CN[t] || t}</button>`).join("")}</span>
        </div>
        <div class="setup-group">
          <span class="filter-row__label">候选数量</span>
          <span class="chips">${["10", "20", "30", "50"].map(t => `<button class="fchip ${t === scr.top ? "active" : ""}" data-k="top" data-v="${t}">${t} 只</button>`).join("")}</span>
        </div>
        <div class="nfield">
          <label for="scr-minprice">最低股价(美元)</label>
          <input id="scr-minprice" type="number" min="0" step="1" value="${esc(scr.minPrice)}" />
          <small>0 表示不限制股价</small>
        </div>
        <div class="nfield">
          <label for="scr-minturn">最低20日均成交额(百万美元)</label>
          <input id="scr-minturn" type="number" min="0" step="5" value="${esc(scr.minTurnover)}" />
          <small>输入 10 代表每日 1,000 万美元</small>
        </div>
      </div>
      <div class="setup-group" style="margin-bottom:16px">
        <span class="filter-row__label">板块范围</span>
        <span class="chips">
          <button class="fchip ${scr.sector === "" ? "active" : ""}" data-k="sector" data-v="">全部板块</button>
          ${(P.sectors || []).map(x => `<button class="fchip ${x.id === scr.sector ? "active" : ""}" data-k="sector" data-v="${esc(x.id)}">${esc(x.name)}</button>`).join("")}
        </span>
      </div>
      <div class="setup-foot">
        <span class="mono" id="plan-line" style="font-size:11px;color:var(--faint)">${esc(planText(secName))}</span>
        <span style="flex:1"></span>
        <button class="btn btn--amber btn--sm" id="rescan">重新扫描</button>
      </div>
    </section>

    <section class="panel panel--pad sect" data-reveal style="--reveal-i:2;margin-top:18px" aria-label="扫描状态">
      <div class="sect-head" style="margin-bottom:14px"><span class="sect-head__no">STEP 02</span><h2 style="font-size:14.5px">扫描状态 · 理解市场背景</h2><span class="sect-head__rule"></span><span class="sect-head__meta">评分 ${esc(D2.score_version || "—")} · 全域 ${D2.universe_count != null ? D2.universe_count : "—"} 只</span></div>
      <div class="scan-status ${degraded || !rows.length ? "is-warn" : ""}" style="margin-bottom:16px">
        <i></i><b>${rows.length ? `已找到 ${rows.length} 只候选` : "本轮没有候选"}</b>
        <span>${rows.length
          ? `覆盖 ${secName || "全部板块"} · ${N.TIMEFRAME_CN[scr.tf]}周期 · 评分偏好${N.PROFILE_CN[scr.profile]}`
          : degraded
            ? esc((dsrc.prices && dsrc.prices.message) || "行情数据源降级,扫描未能完成")
            : `通过初筛 ${D2.screened_count != null ? D2.screened_count : 0} 只 · 跳过 ${skippedTotal} 只`}</span>
      </div>
      <div style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap">
        ${ring(reg.score, 74)}
        <div style="flex:1;min-width:230px">
          <div style="font-size:15.5px;font-weight:650;margin-bottom:4px">市场判断 · ${esc(reg.label || "数据不足")}</div>
          <p style="margin:0 0 10px;font-size:12.5px;color:var(--muted);line-height:1.7">
            ${reg.status === "active"
              ? `输入覆盖 ${reg.input_coverage ? Math.round((reg.input_coverage.ratio || 0) * 100) : "—"}%,${(reg.warnings || []).length ? esc(reg.warnings[0]) : "八组市场输入齐备,判分正常。"}`
              : `市场环境判分当前不可用:${esc(((reg.degraded_reasons || [])[0]) || ((reg.warnings || [])[0]) || "输入数据不足")}`}
          </p>
          <div style="display:flex;gap:8px;flex-wrap:wrap">${snapshot.map(x => `<span class="chip chip--mute">${x.k} <b class="num ${x.tone}" style="margin-left:4px">${x.v}</b></span>`).join("") || `<span class="chip chip--amber"><i></i>市场快照数据不足</span>`}</div>
        </div>
      </div>
      <details class="fold">
        <summary>查看市场分项与数据说明</summary>
        <div class="fold__body">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 26px;margin-bottom:14px">
            ${dims.map(d2 => `
              <div class="ivbar" style="grid-template-columns:86px 1fr 34px;padding:8px 0">
                <span style="font-size:12px;color:var(--muted)">${d2.k}</span>
                <span class="ivbar__track"><span class="ivbar__fill" data-w="${d2.v == null ? 0 : d2.v}"></span></span>
                <b class="num" style="font-size:12px;text-align:right">${d2.v == null ? "—" : d2.v}</b>
              </div>`).join("")}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${inputChips.map(x => `<span class="chip ${x.warn ? "chip--amber" : "chip--mute"}" title="${esc(x.msg || "")}"><i style="background:${x.warn ? "var(--amber)" : "var(--up)"}"></i>${esc(x.k)} · ${esc(x.s)} · ${esc(x.p || "")}</span>`).join("")}
          </div>
          ${skippedTotal ? `<p class="mono" style="font-size:10px;color:var(--faint);margin:12px 0 0">初筛跳过:历史不足 ${skipped.insufficient_history || 0} · 低价 ${skipped.low_price || 0} · 流动性不足 ${skipped.low_liquidity || 0} · 数据错误 ${skipped.data_error || 0}</p>` : ""}
        </div>
      </details>
    </section>

    <section class="sect" aria-label="候选列表">
      <div class="sect-head" data-reveal style="--reveal-i:0"><span class="sect-head__no">STEP 03</span><h2>候选列表 · 查看优先研究标的</h2><span class="sect-head__rule"></span><span class="sect-head__meta">按综合评分排序 · 点击行展开理由</span></div>
      <label class="cat-screener-sort" data-cat-screener-sort hidden>当前页面展示排序
        <select aria-label="候选展示排序"><option value="deterministic">确定性强势（默认）</option><option value="latest">最近催化剂</option><option value="impact">催化剂绝对影响</option></select>
        <span>只调整当前列表顺序；正式 ranking_score 与分类不变。</span>
      </label>
      <div class="panel panel--flush" data-candidate-list data-reveal style="--reveal-i:1">
        ${rows.length ? rows.map((c, i) => {
          const score = rnd0(c.final_score);
          const verdict = verdictOf(c);
          const pi = persistInfo(c);
          const persistTxt = pi.statusTxt + (pi.prod != null ? " · " + rnd0(pi.prod) : "");
          return `
        <details class="cand" ${i === 0 ? "open" : ""} data-cand-idx="${i}" data-cand-ticker="${esc(c.ticker)}" data-cand-rank="${i}">
          <summary>
            <span class="cand__no">${String(i + 1).padStart(2, "0")}</span>
            ${tik(c.ticker, c.name || c.ticker, c.sector_name || "")}
            <span class="cand__reason">${esc((c.reasons || [])[0] || "综合评分入选")}<small>区间持续性 · ${esc(persistTxt)}</small><span class="cat-screener-summary" data-catalyst-summary="${esc(c.ticker)}"><span class="chip chip--mute">新闻读取中</span></span></span>
            <span class="cand__quote"><b>${fmt(c.price)}</b><span class="${tone(c.change_pct)}">${isNum(c.change_pct) ? (c.change_pct > 0 ? "↑ " : c.change_pct < 0 ? "↓ " : "") + pct(c.change_pct) : "—"}</span></span>
            <span class="scorebar cand__scorecell"><span class="scorebar__track"><span class="scorebar__fill" data-w="${score == null ? 0 : score}"></span></span><b>${score == null ? "—" : score}</b><span class="mono" style="font-size:9px;color:var(--faint)">/100</span></span>
            <span class="chip ${score >= 72 ? "chip--up" : score >= 58 ? "chip--amber" : "chip--mute"}">${esc(verdict)}</span>
            <span class="cand__chev" aria-hidden="true"></span>
          </summary>
          <div class="cand__body">
            <div>
              <h4 style="color:var(--up)">入选依据</h4>
              <ul>${(c.reasons && c.reasons.length ? c.reasons : ["本轮扫描未给出入选说明"]).map(r => `<li><span style="color:var(--up)">＋</span>${esc(r)}</li>`).join("")}</ul>
            </div>
            <div>
              <h4 style="color:var(--down)">需要留意</h4>
              <ul>${(c.warnings && c.warnings.length ? c.warnings : ["本轮扫描未触发风险规则"]).map(r => `<li><span style="color:var(--down)">－</span><span style="color:var(--muted)">${esc(r)}</span></li>`).join("")}</ul>
            </div>
            <div>
              <h4 style="color:var(--faint)">周期维度</h4>
              <div class="cand__dims">
                ${[["短期", c.score_short], ["中期", c.score_mid], ["长期", c.score_long]].map(x => `
                <div class="ivbar" style="grid-template-columns:38px 1fr 30px;padding:3px 0;border:0">
                  <span class="mono" style="font-size:10px;color:var(--muted)">${x[0]}</span>
                  <span class="ivbar__track"><span class="ivbar__fill" data-w="${rnd0(x[1]) == null ? 0 : rnd0(x[1])}" style="background:linear-gradient(90deg,var(--amber-deep),var(--amber))"></span></span>
                  <b class="num" style="font-size:11.5px;text-align:right">${rnd0(x[1]) == null ? "—" : rnd0(x[1])}</b>
                </div>`).join("")}
              </div>
            </div>
            <div class="cand__actions">
              <button class="btn btn--sm" data-cevt="${i}">查看完整证据</button>
              <button class="btn btn--amber btn--sm" data-open="${esc(c.ticker)}">打开研究页 →</button>
              <span class="mono" style="font-size:9.5px;color:var(--faint)">证据字段来自 /api/strength/scan 实时输出</span>
            </div>
          </div>
        </details>`;
        }).join("") : `
        <div class="empty-note" style="padding:34px 20px">
          <p>${degraded ? "数据源降级,本轮扫描没有产出候选" : "当前条件下没有候选"}</p>
          <small>${degraded ? esc((dsrc.prices && dsrc.prices.message) || "") + " · 服务端会在缓存过期后自动重试" : "试试放宽最低股价 / 成交额,或切换板块范围"}</small>
          <button class="btn btn--sm" id="rescan-empty">重新扫描</button>
        </div>`}
      </div>
    </section>`;
    postRender();
    if (C) C.mountScreenerBatch(view, rows);
    $$(".fchip[data-k]", view).forEach(b => b.addEventListener("click", () => {
      scr[b.dataset.k === "sector" ? "sector" : b.dataset.k] = b.dataset.v;
      $$(`.fchip[data-k="${b.dataset.k}"]`, view).forEach(x => x.classList.toggle("active", x === b));
      const sn = scr.sector ? ((P.sectors || []).find(s => s.id === scr.sector) || {}).name : "";
      $("#plan-line").textContent = planText(sn);
    }));
    const mp = $("#scr-minprice"), mt = $("#scr-minturn");
    if (mp) mp.addEventListener("change", e => { scr.minPrice = e.target.value; $("#plan-line").textContent = planText(secName); });
    if (mt) mt.addEventListener("change", e => { scr.minTurnover = e.target.value; $("#plan-line").textContent = planText(secName); });
    const rescan = () => renderScreener(true);
    const rs = $("#rescan"); if (rs) rs.addEventListener("click", rescan);
    const rse = $("#rescan-empty"); if (rse) rse.addEventListener("click", rescan);
  }

  /* ---------- 视图:突破雷达 ---------- */
  const brk = { state: "", min: 0, ticker: "" };
  const LIFE_TRACK = ["DISCOVERED", "TRIGGERED", "CONFIRMED", "HOLDING", "RETESTING"];
  const lifeIdx = st => ({ DISCOVERED: 0, WATCHING: 0, TRIGGERED: 1, CONFIRMED: 2, HOLDING: 3, EXTENDED: 3, REACCELERATING: 3, RETESTING: 4, RETEST_HELD: 4 })[st];
  const lifeChip = st => st === "FAILED" ? "chip--down" : st === "EXPIRED" ? "chip--mute" : st === "DISCOVERED" || st === "WATCHING" ? "chip--amber" : "chip--up";

  async function renderBreakouts(force) {
    const g0 = ++gen;
    view.innerHTML = loadingView("正在读取突破雷达…");
    const evFilters = { limit: 12 };
    if (brk.state) evFilters.lifecycle_state = brk.state;
    if (brk.min) evFilters.min_priority = brk.min;
    if (brk.ticker) evFilters.ticker = brk.ticker;
    const [cur, stat, evs, strength] = await Promise.all([
      settle(N.breakoutsCurrent(force)),
      settle(N.breakoutsStatus(force)),
      settle(N.breakoutsEvents(evFilters, force)),
      settle(N.strengthMarket()),
    ]);
    if (g0 !== gen) return;
    if (!cur.ok) { view.innerHTML = errorView("突破雷达接口读取失败", cur.e.message); bindRetry(() => renderBreakouts()); return; }
    St.brkCurrent = cur.v; St.brkStatus = stat.ok ? stat.v : null;
    St.brkEvents = evs.ok ? evs.v : null; St.brkCursor = evs.ok ? evs.v.next_cursor : null;
    St.strength = strength.ok ? strength.v : St.strength;

    const C = St.brkCurrent, SS = St.brkStatus;
    const liveEvents = [...(C.events || [])].sort((a, b) => (b.alert_priority_score || 0) - (a.alert_priority_score || 0));
    const filteredEvents = St.brkEvents && St.brkEvents.events || [];
    const lead = brk.ticker ? (filteredEvents[0] || null) : (liveEvents[0] || null);
    const queue = filteredEvents.filter(e => !lead || e.event_id !== lead.event_id);
    const paused = C.runtime_status === "paused";
    const reg = St.strength && St.strength.market_regime || null;
    const env = N.STRENGTH_DIMS.map(([key, label]) => ({ k: label, v: reg ? rnd0(reg[key]) : null }));
    const shape = lead && lead.market_shape && lead.market_shape.state ? lead.market_shape : null;

    const sysChips = [];
    if (SS) {
      sysChips.push({ k: "工作进程", v: SS.worker ? (SS.worker.status === "paused" ? "已暂停" : SS.worker.status === "running" ? "扫描中" : SS.worker.status) : "—", warn: SS.worker && SS.worker.status !== "running" && SS.worker.status !== "paused" });
      const prov = (SS.provider_health || [])[0];
      if (prov) sysChips.push({ k: "发现源 " + prov.provider, v: N.srcCN(prov.status), warn: prov.status !== "active" });
      sysChips.push({ k: "数据库", v: SS.database ? N.srcCN(SS.database.status) : "—", warn: SS.database && SS.database.status !== "active" });
      sysChips.push({ k: "区间持续度", v: SS.range_persistence_mode === "shadow" ? "影子观察" : SS.range_persistence_mode || "—", warn: SS.range_persistence_mode === "shadow" });
    }

    let leadChart = null;
    if (lead) {
      const r = await settle(N.chart(lead.ticker, "15m"));
      if (g0 !== gen) return;
      leadChart = r.ok ? r.v : null;
    }

    view.innerHTML = `
    <div class="view-head" data-reveal style="--reveal-i:0">
      <div>
        <p class="view-head__kicker">03 · Breakout Radar</p>
        <h1>突破雷达<small>${brk.ticker ? `聚焦 ${esc(brk.ticker)} · ` : "全市场粗筛 → "}点时复核 → 生命周期跟踪 · 快照 ${N.fmtTime(C.as_of)} · ${brk.ticker ? filteredEvents.length + " 条匹配事件" : liveEvents.length + " 条活跃事件"}</small></h1>
      </div>
      <div class="view-head__aside" style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
        ${sysChips.map(s => dataState(`${s.k} · ${s.v}`, s.warn)).join("")}
      </div>
    </div>

    <div class="filter-row" data-reveal style="--reveal-i:1;margin-bottom:18px">
      <span class="filter-row__label">生命周期</span>
      ${[["", "全部状态"], ["WATCHING", "观察中"], ["TRIGGERED", "已触发"], ["CONFIRMED", "已确认"], ["HOLDING", "保持中"], ["RETESTING", "回踩中"], ["FAILED", "突破失败"]].map(x => `<button class="fchip ${brk.state === x[0] ? "active" : ""}" data-bf-state="${x[0]}">${x[1]}</button>`).join("")}
      <span class="filter-row__label" style="margin-left:10px">优先级</span>
      ${[["不限", 0], ["65 分以上", 65], ["80 分以上", 80]].map(x => `<button class="fchip ${brk.min === x[1] ? "active" : ""}" data-bf-min="${x[1]}">${x[0]}</button>`).join("")}
      ${brk.ticker ? `<button class="fchip active" data-bf-ticker-clear title="清除股票聚焦">${esc(brk.ticker)} ×</button>` : ""}
      <span style="flex:1"></span>
      <button class="btn btn--sm" id="brk-refresh">刷新快照</button>
    </div>

    <div class="radar-grid">
      ${lead ? renderLeadPanel(lead, leadChart) : `
      <section class="panel panel--hero panel--pad lead" data-reveal style="--reveal-i:2" aria-label="扫描状态">
        <div class="empty-note" style="padding:46px 20px">
          <p style="font-size:16px">${paused ? "扫描已暂停 · " + (C.runtime_reason === "market_closed" ? "市场休市中" : esc(C.runtime_reason || "")) : "当前没有活跃突破事件"}</p>
          <small>
            ${paused
              ? `突破雷达只在交易时段运行;下一时段 ${N.fmtET(C.next_session_at)} 自动恢复。`
              : "粗筛与复核照常运行,只是此刻没有满足条件的突破。"}
            ${SS && SS.latest_completed_scan ? ` 最近完整扫描 ${N.fmtDateTime(SS.latest_completed_scan.completed_at || SS.latest_completed_scan.as_of)}。` : " 尚无已完成的扫描快照。"}
          </small>
          <span class="mono" style="font-size:10px;color:var(--faint)">运行状态 ${esc(C.runtime_status || "—")} · 交易时段 ${esc(N.SESSION_CN[C.market_session] || C.market_session || "—")} · 接口 ${esc((C.versions || {}).api_schema || "—")}</span>
        </div>
      </section>`}

      <div style="display:flex;flex-direction:column;gap:18px">
        <section class="panel panel--flush" data-reveal style="--reveal-i:3" aria-label="事件队列">
          <div class="sect-head" style="padding:15px 20px 0;margin-bottom:4px"><span class="sect-head__no">QUEUE</span><h2 style="font-size:14.5px">事件队列</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${evs.ok ? "游标分页" : "读取失败"}</span></div>
          ${queue.map(q => `
          <div class="evt-row" data-evt="${esc(q.event_id)}" title="查看 ${esc(q.ticker)} 事件证据">
            ${logo(q.ticker)}
            <span class="evt-row__meta">
              <span class="tik__sym">${esc(q.ticker)}</span> <span class="chip ${lifeChip(q.lifecycle_state)}" style="margin-left:6px">${N.LIFECYCLE_CN[q.lifecycle_state] || esc(q.lifecycle_state)}</span>
              <small>${N.SETUP_CN[q.setup_type] || esc(q.setup_type)} · ${N.ago(q.event_at)} · 量能 ${isNum(q.rvol_time_of_day) ? q.rvol_time_of_day.toFixed(1) + "×" : "—"}</small>
            </span>
            <span class="watch-row__price"><b>${fmt(q.current_price != null ? q.current_price : q.event_price)}</b><span class="${tone(q.session_change_pct)}">${pct(q.session_change_pct)}</span></span>
            <span class="evt-row__score"><span class="mono" style="font-size:10px;color:var(--faint)">优先级</span><br><b class="num" style="font-size:15px;color:${(q.alert_priority_score || 0) >= 80 ? "var(--up)" : (q.alert_priority_score || 0) >= 60 ? "var(--amber)" : (q.alert_priority_score || 0) >= 40 ? "var(--muted)" : "var(--down)"}">${rnd0(q.alert_priority_score) != null ? rnd0(q.alert_priority_score) : "—"}</b></span>
          </div>`).join("")}
          ${queue.length === 0 ? `<div class="empty-note" style="padding:24px"><p>${evs.ok ? "没有匹配的事件" : "事件列表读取失败"}</p><small>${evs.ok ? (paused ? "休市期间不产生新事件;历史事件可切换生命周期筛选查看" : "试试放宽生命周期或优先级筛选") : esc(evs.e && evs.e.message || "")}</small></div>` : ""}
          <div class="panel__foot"><span class="mono" style="font-size:10px;color:var(--faint)">已显示 ${queue.length} 条${St.brkCursor ? " · 还有更多" : " · 已到底"}</span><button class="btn btn--sm" id="brk-more" ${St.brkCursor ? "" : "disabled"}>显示更多事件</button></div>
        </section>

        <section class="panel panel--flush" data-reveal style="--reveal-i:4" aria-label="市场环境">
          <div class="sect-head" style="padding:15px 20px 0;margin-bottom:4px"><span class="sect-head__no">ENV</span><h2 style="font-size:14.5px">六维市场环境</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${shape ? esc(N.shapeCN(shape.state)) + " · 置信 " + rnd0((shape.confidence || 0) * (shape.confidence <= 1 ? 100 : 1)) + "%" : "形态随扫描快照产出 · 当前无快照"}</span></div>
          ${reg ? `
          <dl class="env-grid env-grid--3">
            ${env.map(e => `<div class="env-cell ${e.v != null && e.v >= 65 ? "warm" : ""}"><dt>${e.k}</dt><dd>${e.v == null ? "—" : e.v}<span class="env-cell__meter"><i data-w="${e.v == null ? 0 : e.v}"></i></span></dd></div>`).join("")}
          </dl>
          <p class="mono" style="font-size:9.5px;color:var(--faint);padding:0 20px 14px;margin:6px 0 0">${esc(reg.label || "")} · 综合 ${rnd0(reg.score) != null ? rnd0(reg.score) : "—"} · 数据 ${N.fmtTime(reg.as_of)} · 来源 /api/strength/market</p>`
          : `<div style="padding:6px 14px 14px">${inlineErr("市场环境读取失败", strength.ok ? "接口返回为空" : strength.e && strength.e.message)}</div>`}
        </section>
      </div>
    </div>`;
    postRender();
    $$("[data-bf-state]", view).forEach(b => b.addEventListener("click", () => { brk.state = b.dataset.bfState; renderBreakouts(); }));
    $$("[data-bf-min]", view).forEach(b => b.addEventListener("click", () => { brk.min = parseInt(b.dataset.bfMin, 10); renderBreakouts(); }));
    const clearTicker = $("[data-bf-ticker-clear]", view); if (clearTicker) clearTicker.addEventListener("click", () => { brk.ticker = ""; history.replaceState(null, "", "#breakouts"); renderBreakouts(); });
    $("#brk-refresh").addEventListener("click", () => renderBreakouts(true));
    const more = $("#brk-more");
    if (more && St.brkCursor) more.addEventListener("click", async () => {
      more.textContent = "读取中…"; more.disabled = true;
      const r = await settle(N.breakoutsEvents(Object.assign({}, evFilters, { cursor: St.brkCursor })));
      if (g0 !== gen) return;
      if (r.ok) {
        St.brkEvents.events = St.brkEvents.events.concat(r.v.events || []);
        St.brkEvents.next_cursor = r.v.next_cursor; St.brkCursor = r.v.next_cursor;
        renderBreakouts();
      } else { more.textContent = "读取失败,点击重试"; more.disabled = false; }
    });
  }

  function renderLeadPanel(L, chartData) {
    const idx = lifeIdx(L.lifecycle_state);
    const terminal = L.lifecycle_state === "FAILED" || L.lifecycle_state === "EXPIRED";
    const price = L.current_price != null ? L.current_price : L.event_price;
    const contribs = Object.entries(L.contribution_breakdown || {}).sort((a, b) => b[1] - a[1]).slice(0, 5);
    const palette = ["var(--amber)", "var(--amber-deep)", "var(--teal)", "var(--teal-deep)", "var(--faint)"];
    const warnTxt = (L.warnings || [])[0];
    return `
      <section class="panel panel--hero panel--flush lead" data-reveal style="--reveal-i:2" aria-label="首要信号">
        <div class="lead__head">
          <div class="radar-brief__state">
            <span class="chip ${lifeChip(L.lifecycle_state)}"><i></i>${N.LIFECYCLE_CN[L.lifecycle_state] || esc(L.lifecycle_state)}</span>
            <span class="chip chip--amber">${N.SETUP_CN[L.setup_type] || esc(L.setup_type)}</span>
            <span class="chip chip--mute">${N.SESSION_CN[L.session] || esc(L.session)} · ${N.ago(L.event_at)}</span>
            <span style="flex:1"></span>
            <span class="chip chip--mute">首要信号 · 优先级最高</span>
          </div>
          <h3>${esc(L.name || L.ticker)} <span class="mono">${esc(L.ticker)}</span></h3>
          <div class="lead__sub">${esc(L.exchange || "—")} · ${esc(L.sector || "—")} · 跳空 ${pct(L.gap_pct)} · 同时段量能 ${isNum(L.rvol_time_of_day) ? L.rvol_time_of_day.toFixed(1) + "×" : "—"} · 事件时间 ${N.fmtET(L.event_at)}</div>
        </div>
        <div class="lead__chart">${chartData ? candleChart(chartData.bars, 880, 310, { levels: [
          isNum(L.pivot_price) && { v: L.pivot_price, label: "突破枢轴 " + fmt(L.pivot_price), color: "var(--amber)" },
          isNum(L.invalidation_price) && { v: L.invalidation_price, label: "失效位置 " + fmt(L.invalidation_price), color: "var(--down)" },
        ].filter(Boolean) }) : inlineErr("K线读取失败", "15 分钟行情暂不可用")}</div>
        <dl class="lead__nums">
          <div><dt>当前价</dt><dd class="${tone(L.session_change_pct)}">${fmt(price)}<small>${pct(L.session_change_pct)}</small></dd></div>
          <div><dt>突破枢轴</dt><dd>${fmt(L.pivot_price)}<small>${isNum(L.pivot_price) && isNum(price) ? pct((L.pivot_price / price - 1) * 100) + " 至现价" : ""}</small></dd></div>
          <div><dt>失效位置</dt><dd class="d">${fmt(L.invalidation_price)}<small>${isNum(L.invalidation_price) && isNum(price) ? pct((L.invalidation_price / price - 1) * 100) + " 至现价" : ""}</small></dd></div>
          <div style="margin-left:auto"><dt>告警优先级</dt><dd>${ring(L.alert_priority_score, 50)}</dd></div>
        </dl>
        <div class="lifecycle" aria-label="生命周期">
          ${terminal ? `<span class="chip ${lifeChip(L.lifecycle_state)}" style="margin:2px 0"><i></i>${N.LIFECYCLE_CN[L.lifecycle_state]} · ${N.fmtDateTime(L.state_changed_at)}</span>` :
          LIFE_TRACK.map((x, i) => `
            <span class="lifecycle__step ${idx != null && i < idx ? "done" : idx === i ? "now" : ""}"><span class="lifecycle__node"></span>${N.LIFECYCLE_CN[x]}</span>
            ${i < LIFE_TRACK.length - 1 ? `<span class="lifecycle__link ${idx != null && i < idx ? "done" : ""}"></span>` : ""}`).join("")}
        </div>
        <div class="anatomy">
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px 22px">
            ${[["突破质量", L.breakout_quality_score, false], ["确认强度", L.breakout_confirmation_score, false], ["数据可信度", L.data_confidence_score, false], ["追高风险", L.chase_risk_score, true]].map(b => `
              <div style="padding:6px 0">
                <div style="display:flex;justify-content:space-between;margin-bottom:6px"><span class="mono" style="font-size:9.5px;letter-spacing:.1em;color:var(--faint)">${b[0]}</span><b class="num" style="font-size:11.5px;color:${b[2] && (b[1] || 0) >= 60 ? "var(--down)" : "var(--ink)"}">${rnd0(b[1]) != null ? rnd0(b[1]) : "—"}</b></div>
                <span class="ivbar__track" style="display:block"><span class="ivbar__fill" data-w="${rnd0(b[1]) || 0}" style="background:${b[2] ? "linear-gradient(90deg,var(--down-deep),var(--down))" : "linear-gradient(90deg,var(--amber-deep),var(--amber))"};display:block"></span></span>
              </div>`).join("")}
          </div>
          ${contribs.length ? `
          <div class="anatomy__bar">
            ${contribs.map((c, i) => `<span class="anatomy__seg" style="flex:${Math.max(c[1], 0.5)};background:${palette[i % palette.length]}" title="${esc(N.scanComponentCN(c[0]))} ${c[1].toFixed(1)}"></span>`).join("")}
          </div>
          <div class="anatomy__legend">
            ${contribs.map((c, i) => `<span><i style="background:${palette[i % palette.length]}"></i>${esc(N.scanComponentCN(c[0]))} · ${c[1].toFixed(1)}</span>`).join("")}
          </div>` : ""}
          ${warnTxt ? `<div class="anatomy__warn"><b>⚠ 风险提醒</b><span>${esc(warnTxt)}</span></div>` : ""}
        </div>
        <div class="panel__foot">
          <span class="mono" style="font-size:10px;color:var(--faint)">评分 ${esc(L.score_version || "—")} · 形态 ${esc((L.market_shape || {}).version || "—")} · 观测 ${N.ago(L.last_seen_at)}</span>
          <span style="display:flex;gap:8px"><button class="btn btn--sm" data-evt="${esc(L.event_id)}">查看完整证据</button><button class="btn btn--amber btn--sm" data-open="${esc(L.ticker)}">打开研究页</button></span>
        </div>
      </section>`;
  }

  /* ---------- 视图:板块 ---------- */
  const sec = { selected: null, mode: "perf", tab: "cons" };
  async function renderSectors() {
    const g0 = ++gen;
    view.innerHTML = loadingView("正在读取板块行情…");
    const [secR, watch] = await Promise.all([settle(N.sectors()), settle(N.watchlist())]);
    if (g0 !== gen) return;
    if (!secR.ok) { view.innerHTML = errorView("板块接口读取失败", secR.e.message); bindRetry(renderSectors); return; }
    if (!watch.ok) { view.innerHTML = errorView("行情接口读取失败", watch.e.message); bindRetry(renderSectors); return; }
    St.sectors = secR.v.sectors || []; St.watch = watch.v;

    const byId = {}; St.watch.groups.forEach(g => { byId[g.id] = g; });
    const list = St.sectors.map(s => {
      const g = byId[s.id];
      const stocks = g ? g.stocks : [];
      const perfs = stocks.filter(x => isNum(x.chg));
      const perf = perfs.length ? perfs.reduce((a, x) => a + x.chg, 0) / perfs.length : null;
      const leaders = [...stocks].sort((a, b) => (b.chg || 0) - (a.chg || 0)).slice(0, 3).map(x => x.ticker);
      return { id: s.id, name: s.name, count: (s.tickers || []).length || stocks.length, perf, leaders, stocks };
    }).sort((a, b) => (b.perf == null ? -999 : b.perf) - (a.perf == null ? -999 : a.perf));

    if (!sec.selected || !list.some(x => x.id === sec.selected)) sec.selected = (list[0] || {}).id;
    const cur = list.find(x => x.id === sec.selected) || list[0];
    const ivData = St.sectorIV[cur.id]; // undefined | "loading" | {rankings} | {error}
    const needIV = (sec.mode === "iv" || sec.tab === "iv") && (ivData === undefined);
    if (needIV) {
      St.sectorIV[cur.id] = "loading";
      N.sectorIV(cur.id).then(d => { St.sectorIV[cur.id] = d; if (gen === g0 && location.hash.includes("sectors")) renderSectors(); },
        e => { St.sectorIV[cur.id] = { error: e.message }; if (gen === g0 && location.hash.includes("sectors")) renderSectors(); });
    }
    const ivMap = {};
    if (ivData && ivData.rankings) ivData.rankings.forEach(r => { ivMap[r.ticker] = r; });

    const upCount = list.filter(x => x.perf > 0).length;
    const maxAbs = Math.max(...list.map(x => Math.abs(x.perf || 0)), 0.01);
    const cons = cur.stocks;
    const consPerfs = cons.filter(c => isNum(c.chg));
    const avgPerf = consPerfs.length ? consPerfs.reduce((a, c) => a + c.chg, 0) / consPerfs.length : null;
    const consUp = cons.filter(c => c.chg > 0).length;
    const ivVals = cons.map(c => ivMap[c.ticker]).filter(r => r && isNum(r.atm_iv_percent));
    const avgIv = ivVals.length ? ivVals.reduce((a, r) => a + r.atm_iv_percent, 0) / ivVals.length : null;

    view.innerHTML = `
    <div class="view-head" data-reveal style="--reveal-i:0">
      <div>
        <p class="view-head__kicker">04 · Sectors</p>
        <h1>板块<small>${upCount} 个板块上涨${list[0] && list[0].perf != null ? "," + esc(list[0].name) + "暂时领先" : ""} · 先看资金方向,再查内部波动</small></h1>
      </div>
      <div class="view-head__aside">${dataState(`行情快照 ${N.fmtTime(St.watch.asOf)} · ${St.watch.flat.length} 只成分`, St.watch.stale, srcTip({ attempted: St.watch.attempted, succeeded: St.watch.succeeded, failedTickers: St.watch.failedTickers, failed: St.watch.failed, stale: St.watch.stale, asOf: St.watch.asOf, label: "Yahoo 行情源" }))}</div>
    </div>

    <div class="sector-grid">
      <section class="panel panel--pad" data-reveal style="--reveal-i:1" aria-label="板块排行">
        <div class="sect-head" style="margin-bottom:10px"><span class="sect-head__no">RANK</span><h2 style="font-size:14.5px">板块地图与排行</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${list.length} 个主题板块</span></div>
        <div class="sector-rank">
          ${list.map((x, i) => `
          <button class="sector-rank__row ${x.id === sec.selected ? "selected" : ""}" data-sector="${esc(x.id)}">
            <span class="sector-rank__no">${String(i + 1).padStart(2, "0")}</span>
            <span class="sector-rank__name">${esc(x.name)}<small>${x.leaders.map(esc).join(" · ") || "—"}</small></span>
            <span class="ivbar__track"><span class="ivbar__fill" data-w="${x.perf == null ? 0 : (Math.abs(x.perf) / maxAbs * 100).toFixed(0)}" style="background:${(x.perf || 0) >= 0 ? "linear-gradient(90deg,var(--up-deep),var(--up))" : "linear-gradient(90deg,var(--down-deep),var(--down))"}"></span></span>
            <b class="num ${tone(x.perf)}" style="font-size:12.5px;text-align:right">${pct(x.perf)}</b>
          </button>`).join("")}
        </div>
        <p class="mono" style="font-size:10px;color:var(--faint);margin:12px 2px 0;letter-spacing:.05em">板块涨跌 = 成分股当日涨跌均值(等权)· 点击切换右侧</p>
      </section>

      <div style="display:flex;flex-direction:column;gap:18px">
        <section class="panel panel--pad" data-reveal style="--reveal-i:2" aria-label="成分股地图">
          <div class="sect-head" style="margin-bottom:14px">
            <span class="sect-head__no">MAP</span><h2 style="font-size:14.5px">${esc(cur.name)} · 成分股地图</h2>
            <span class="sect-head__rule"></span>
            <span style="display:flex;gap:6px">
              <button class="fchip ${sec.mode === "perf" ? "active" : ""}" data-mode="perf">今日表现</button>
              <button class="fchip ${sec.mode === "iv" ? "active" : ""}" data-mode="iv">隐含波动率</button>
            </span>
          </div>
          ${sec.mode === "iv" && ivData === "loading" ? loadingView("读取板块隐波排名…")
          : sec.mode === "iv" && ivData && ivData.error ? inlineErr("隐波数据读取失败", ivData.error)
          : `
          <div class="heat-grid">
            ${cons.map(c => {
              let bg, val;
              if (sec.mode === "perf") {
                const mag = Math.min(Math.abs(c.chg || 0) / 5, 1);
                bg = (c.chg || 0) >= 0
                  ? `color-mix(in oklab, var(--up) ${8 + mag * 34}%, var(--surface))`
                  : `color-mix(in oklab, var(--down) ${8 + mag * 34}%, var(--surface))`;
                val = `<span class="num ${tone(c.chg)}" style="font-size:13px;font-weight:600">${pct(c.chg)}</span>`;
              } else {
                const iv = ivMap[c.ticker] && ivMap[c.ticker].atm_iv_percent;
                const mag = isNum(iv) ? Math.min(Math.max((iv - 20) / 60, 0), 1) : 0;
                bg = `color-mix(in oklab, var(--amber) ${isNum(iv) ? 6 + mag * 36 : 4}%, var(--surface))`;
                val = `<span class="num" style="font-size:13px;font-weight:600;color:var(--amber-hi)">${isNum(iv) ? iv.toFixed(1) + "%" : "—"}</span>`;
              }
              return `<div class="heat-cell" data-open="${esc(c.ticker)}" style="background:${bg}">
                <span><b>${esc(c.ticker)}</b><small>${esc(c.name)}</small></span>${val}
              </div>`;
            }).join("")}
          </div>
          <p class="mono" style="font-size:9.5px;color:var(--faint);margin:12px 2px 0;letter-spacing:.06em">${sec.mode === "perf" ? "颜色 = 今日涨跌 · 等面积(批量接口无市值权重,如实等权)" : "颜色 = 平值隐含波动率(20—80% 归一)· 等面积"}</p>`}
        </section>

        <section class="panel panel--pad" data-reveal style="--reveal-i:3" aria-label="选中板块">
          <div class="sect-head" style="margin-bottom:14px">
            <span class="sect-head__no">FOCUS</span><h2 style="font-size:14.5px">选中板块 · ${esc(cur.name)}</h2>
            <span class="sect-head__rule"></span>
            <span class="itabs" role="tablist">
              <button class="itab ${sec.tab === "cons" ? "active" : ""}" data-itab="cons" role="tab" aria-selected="${sec.tab === "cons"}">成分股</button>
              <button class="itab ${sec.tab === "iv" ? "active" : ""}" data-itab="iv" role="tab" aria-selected="${sec.tab === "iv"}">波动率</button>
            </span>
          </div>
          <dl class="kv-grid kv-grid--4" style="margin-bottom:16px">
            <div><dt>平均表现</dt><dd class="${tone(avgPerf)}">${pct(avgPerf)}</dd></div>
            <div><dt>上涨 / 下跌</dt><dd>${consUp} / ${cons.length - consUp}</dd></div>
            <div><dt>平均平值隐波</dt><dd>${avgIv != null ? avgIv.toFixed(1) + "%" : (ivData === "loading" ? "读取中…" : "点「波动率」页签载入")}</dd></div>
            <div><dt>数据覆盖</dt><dd>${cons.length} 只标的</dd></div>
          </dl>
          ${sec.tab === "cons" ? `
          <div role="tabpanel">
            ${[...cons].sort((a, b) => (b.chg || 0) - (a.chg || 0)).map((c, i) => `
            <div class="crow" data-open="${esc(c.ticker)}">
              <span class="crow__no">${String(i + 1).padStart(2, "0")}</span>
              <span><span class="crow__sym">${esc(c.ticker)}</span><span class="crow__name">${esc(c.name)}</span></span>
              <span class="num ${tone(c.chg)}" style="font-size:12.5px">${pct(c.chg)}</span>
            </div>`).join("")}
          </div>` : ivData === "loading" ? loadingView("读取板块隐波排名…")
            : ivData && ivData.error ? inlineErr("隐波数据读取失败", ivData.error)
            : `
          <div role="tabpanel">
            ${(ivData && ivData.rankings || []).map(r => `
            <div class="ivbar ${(r.sector_iv_rank || 0) >= 70 ? "ivbar--hot" : ""}" style="grid-template-columns:150px 1fr 150px" data-open="${esc(r.ticker)}">
              <span>${tik(r.ticker, r.name || r.ticker)}</span>
              <span class="ivbar__track"><span class="ivbar__fill" data-w="${rnd0(r.sector_iv_rank) || 0}"></span></span>
              <span style="text-align:right"><b class="num" style="font-size:13px">${rnd0(r.sector_iv_rank) != null ? rnd0(r.sector_iv_rank) : "—"}</b><span class="mono" style="font-size:10px;color:var(--faint)"> 板块分位</span><br><span class="mono" style="font-size:10.5px;color:var(--muted)">平值隐波 ${isNum(r.atm_iv_percent) ? r.atm_iv_percent.toFixed(1) + "%" : "—"}</span></span>
            </div>`).join("") || inlineErr("暂无隐波数据", "该板块本轮未返回期权数据")}
            ${ivData && ivData.rankings ? `<p class="mono" style="font-size:9.5px;color:var(--faint);margin:10px 2px 0">覆盖 ${ivData.success_count}/${ivData.requested_count} 只 · ${N.fmtTime(ivData.as_of)} · 全局 IV 分位接口未提供,此处为板块内相对分位</p>` : ""}
          </div>`}
        </section>
      </div>
    </div>`;
    postRender();
    $$("[data-sector]", view).forEach(b => b.addEventListener("click", () => { sec.selected = b.dataset.sector; renderSectors(); }));
    $$("[data-mode]", view).forEach(b => b.addEventListener("click", () => { sec.mode = b.dataset.mode; renderSectors(); }));
    $$("[data-itab]", view).forEach(b => b.addEventListener("click", () => { sec.tab = b.dataset.itab; renderSectors(); }));
  }

  /* ---------- 视图:财报 ---------- */
  let earnDay = null, earnSel = null;
  const dayCN = d => d.replace(".", " 月 ") + " 日";
  async function renderEarnings(forceReload) {
    const g0 = ++gen;
    if (!St.earnWeek || !St.earnMeta || forceReload) {
      view.innerHTML = loadingView("正在读取财报日历…");
      const r = await settle(N.earnings(forceReload).then(d => ({ week: N.buildWeek(d.earnings || []), meta: d })));
      if (g0 !== gen) return;
      if (!r.ok) { view.innerHTML = errorView("财报日历读取失败", r.e.message); bindRetry(() => renderEarnings(true)); return; }
      St.earnWeek = r.v.week; St.earnMeta = r.v.meta;
    }
    const E = St.earnWeek;
    if (!earnDay || !E.week.some(w => w.d === earnDay)) earnDay = E.week[0].d;
    const dayInfo = E.week.find(w => w.d === earnDay) || E.week[0];
    const events = dayInfo.events;
    if (earnSel && !events.some(e => e.ticker === earnSel)) earnSel = null;
    const total = E.week.reduce((a, w) => a + w.count, 0);
    const dayIdx = E.week.findIndex(w => w.d === earnDay);
    let nextDay = E.week.slice(dayIdx + 1).find(w => w.count > 0);
    const isForward = Boolean(nextDay);
    if (!nextDay) nextDay = E.week.find(w => w.count > 0);
    const nextEvt = nextDay ? nextDay.events[0] : null;
    const impact = earnSel ? St.impacts[earnSel] : null;
    const selEvt = events.find(e => e.ticker === earnSel);
    const sectorsCovered = new Set();
    E.week.forEach(w => w.events.forEach(e => e.sector && sectorsCovered.add(e.sector)));

    view.innerHTML = `
    <div class="view-head" data-reveal style="--reveal-i:0">
      <div>
        <p class="view-head__kicker">05 · Earnings</p>
        <h1>财报日历<small>未来七日 ${total} 场,覆盖 ${sectorsCovered.size} 个行业 · 预设美股列表 · 日期以美东为准</small></h1>
      </div>
      <div class="view-head__aside">${dataState(`日历 ${St.earnMeta ? N.fmtTime(St.earnMeta.as_of) : "—"} · ${St.earnMeta ? St.earnMeta.succeeded + "/" + St.earnMeta.attempted : ""} 源正常`, St.earnMeta && St.earnMeta.source_status !== "active", St.earnMeta ? srcTip({ attempted: St.earnMeta.attempted, succeeded: St.earnMeta.succeeded, failedTickers: St.earnMeta.failed_symbols, stale: false, asOf: St.earnMeta.as_of, label: "财报日历源" }) : "")}</div>
    </div>

    <section class="panel panel--pad" data-reveal style="--reveal-i:1" aria-label="选择日期">
      <div class="sect-head" style="margin-bottom:14px">
        <span class="sect-head__no">CALENDAR</span><h2 style="font-size:14.5px">选择日期 · 未来七日</h2>
        <span class="sect-head__rule"></span><span class="sect-head__meta">${esc(E.range)}</span>
      </div>
      <div class="timeline-wrap">
        <div class="timeline" role="tablist" aria-label="未来七日">
          ${E.week.map(w => `
          <button class="tl-day ${w.d === earnDay ? "active" : ""} ${w.count ? "has" : ""} ${w.today ? "today" : ""}" data-day="${esc(w.d)}" role="tab" aria-selected="${w.d === earnDay}">
            <span class="tl-day__wd">${esc(w.wd)}</span>
            <span class="tl-day__date">${esc(w.d)}</span>
            <span class="tl-day__node"><i></i></span>
            <span class="tl-day__count ${w.count ? "" : "none"}">${w.count ? w.count + " 家" : "无事件"}</span>
            <span class="tl-day__preview">${esc(w.tickers)}</span>
          </button>`).join("")}
        </div>
      </div>
    </section>

    <div class="pulse-grid pulse-grid--earn">
      <section class="panel panel--flush" data-reveal style="--reveal-i:2" aria-label="所选日期事件">
        <div class="sect-head" style="padding:16px 20px 0;margin-bottom:6px">
          <span class="sect-head__no">${dayInfo.today ? "TODAY" : esc(dayInfo.wd.toUpperCase())}</span>
          <h2 style="font-size:15px">${dayCN(earnDay)}${dayInfo.today ? " · 今天" : " · " + esc(dayInfo.wd)} · ${events.length} 场</h2>
          <span class="sect-head__rule"></span>
          <span class="sect-head__meta">选择行后按需生成，不会自动调用模型</span>
        </div>
        ${events.map(e => `
        <div class="earn-row ${e.ticker === earnSel ? "selected" : ""}" data-sel="${esc(e.ticker)}" role="button" tabindex="0">
          ${logo(e.ticker)}
          <span><span class="tik__sym">${esc(e.ticker)}</span> <span class="tik__name">${esc(e.name || e.ticker)}${e.sector ? " · " + esc(e.sector) : ""}</span>${e.earnings_date_source === "estimated" ? ` <span class="chip chip--mute" style="margin-left:6px">日期为估计</span>` : ""}</span>
          <span class="earn-row__est mono" style="font-size:11px;color:var(--muted);text-align:right">EPS 预期 ${isNum(e.eps_estimate) ? "$" + e.eps_estimate.toFixed(2) : "—"}<br>营收预期 ${isNum(e.revenue_estimate) ? "$" + N.cnAmount(e.revenue_estimate) : "—"}</span>
          <span class="earn-row__when">T+${e.days_until != null ? e.days_until : "?"}<br><small style="color:var(--faint)">市值 ${isNum(e.market_cap) ? "$" + N.cnAmount(e.market_cap) : "—"}</small></span>
          <span style="display:flex;flex-direction:column;gap:3px;align-items:flex-end">
            <span class="earn-row__tag mono" style="font-size:11px;color:${e.ticker === earnSel ? "var(--amber-hi)" : "var(--faint)"}">关联 →</span>
            <button class="earn-row__link" data-go="${esc(e.ticker)}">研究 ↗</button>
          </span>
        </div>`).join("")}
        ${events.length === 0 ? `
        <div class="empty-note">
          <p>这一天没有已确认的财报</p>
          <small>预设美股列表 · 缓存 30 分钟 · 接口只提供日期,不含盘前盘后场次</small>
          ${nextEvt ? `<button class="btn btn--amber btn--sm" data-jump="${esc(nextDay.d)}">${isForward ? "查看下一场" : "回看最近一场"}:${esc(nextEvt.ticker)} · ${dayCN(nextDay.d)}</button>` : ""}
        </div>` : ""}
      </section>

      <aside class="panel panel--pad" id="earn-impact-panel" data-reveal style="--reveal-i:3" aria-label="关联影响">
        ${renderImpactPanel(impact, earnSel, selEvt, nextDay, nextEvt)}
      </aside>
    </div>`;
    postRender();
    $$("[data-day]", view).forEach(b => b.addEventListener("click", () => { earnDay = b.dataset.day; earnSel = null; renderEarnings(); }));
    $$("[data-jump]", view).forEach(b => b.addEventListener("click", () => { earnDay = b.dataset.jump; earnSel = null; renderEarnings(); }));
    $$("[data-sel]", view).forEach(r => {
      const pick = () => { selectImpact(r.dataset.sel); };
      r.addEventListener("click", pick);
      r.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    });
    $$("[data-go]", view).forEach(g => g.addEventListener("click", e => { e.stopPropagation(); openDrawer(g.dataset.go); }));
    bindImpactActions();
  }
  function selectImpact(ticker) {
    earnSel = ticker;
    if (!St.impacts[ticker]) St.impacts[ticker] = { status: "idle" };
    renderEarnings();
  }
  function renderImpactPanel(impact, sel, selEvt, nextDay, nextEvt) {
    if (!sel) return `
      <div class="sect-head" style="margin-bottom:12px"><span class="sect-head__no">IMPACT</span><h2 style="font-size:14.5px">关联影响</h2></div>
      <div class="empty-note" style="padding:22px 8px">
        <p>${privateActionsAvailable() ? "选择一场财报后，可按需生成关联影响分析。" : "公开页面提供财报与行情查看。"}</p>
        <small>${privateActionsAvailable() ? "选择行只会切换研究对象，不会调用模型；生成任务必须再次点击确认。" : "模型分析需要管理授权，浏览页面不会产生模型调用。"}</small>
        ${nextEvt ? `<button class="btn btn--sm" data-jump="${esc(nextDay.d)}">跳到 ${dayCN(nextDay.d)}</button>` : ""}
      </div>
      <p class="mono" style="font-size:9.5px;color:var(--faint);margin:8px 0 0;line-height:1.7">模型推断不代表收益概率，结果不构成投资建议。</p>`;
    const status = Jobs ? Jobs.normalizeStatus(impact && impact.status || "idle") : (impact && impact.status || "idle");
    const result = impact && (impact.result || impact.output || impact.analysis) || (impact && (impact.summary || impact.impacted) ? impact : null);
    const active = status === "pending" || status === "queued" || status === "in_progress" || status === "cancel_requested";
    const statusCN = { idle: "未生成", pending: "准备排队", queued: "排队中", in_progress: "分析中", cancel_requested: "正在取消", completed: "已完成", failed: "失败", cancelled: "已取消", insufficient_context: "信息不足", budget_blocked: "预算受限" };
    const elapsed = Jobs && active ? Jobs.elapsed(impact) : null;
    const statusTone = status === "completed" ? "chip--up" : status === "failed" ? "chip--down" : active ? "chip--amber" : "chip--mute";
    const retryBlocked = status === "failed" && impact && impact.error_code === "submission_outcome_unknown";
    const head = `<div class="sect-head" style="margin-bottom:12px"><span class="sect-head__no">IMPACT</span><h2 style="font-size:14.5px">关联影响 · ${esc(sel)}</h2><span class="sect-head__rule"></span><span class="chip ${statusTone}">${esc(statusCN[status] || status)}</span></div>`;
    if (status === "idle" || status === "cancelled" || status === "failed" || status === "budget_blocked") return `${head}
      <div class="empty-note" style="padding:22px 8px">
        <p>${status === "failed" ? "关联分析任务失败" : status === "cancelled" ? "关联分析任务已取消" : status === "budget_blocked" ? "当前预算门禁阻止创建任务" : "尚未生成关联分析"}</p>
        <small>${esc(impact && (impact.error_message || impact.message || impact.error_code || impact.error) || "生成后将通过后台任务处理，页面不会长时间等待。")}</small>
        ${privateActionsAvailable() && status !== "budget_blocked" && !retryBlocked ? `<button class="btn btn--amber btn--sm" id="impact-run" type="button" data-impact-run>${status === "failed" || status === "cancelled" ? "显式重试" : "生成 AI 关联分析"}</button>` : ""}
        ${!privateActionsAvailable() ? `<small data-private-action-note>公开页面仅供查看；模型分析需要管理授权。</small>` : ""}
        ${retryBlocked ? `<small>上游是否已接受请求无法确认，为避免重复计费，本次不能自动重提。</small>` : ""}
      </div>`;
    if (active) return `${head}<div class="empty-note" style="padding:22px 8px"><p>${status === "in_progress" ? "模型正在分析" : "任务正在排队"}</p><small>${impact && (impact.submitted_at || impact.created_at) ? "提交 " + N.fmtDateTime(impact.submitted_at || impact.created_at) : "已交给后台任务"}${elapsed != null && status === "in_progress" ? " · 已运行 " + elapsed + " 秒" : ""} · 不显示估算进度</small>${privateActionsAvailable() ? `<button class="btn btn--sm" id="impact-cancel" type="button" data-impact-cancel>取消任务</button>` : ""}</div>`;
    if (status === "insufficient_context") return `${head}<div class="empty-note" style="padding:22px 8px"><p>信息不足，未生成方向性分析</p><small>服务端没有为缺失信息补造结论，也不会显示假结果。</small></div>`;
    const impacted = Array.isArray(result && result.impacted) ? result.impacted : Array.isArray(result && result.affected_stocks) ? result.affected_stocks : [];
    const groups = new Map();
    impacted.forEach(en => {
      const rel = N.RELATION_ORDER.includes(en && en.relation) ? en.relation : "other";
      if (!groups.has(rel)) groups.set(rel, []);
      groups.get(rel).push(en);
    });
    const dirMeta = d => d === "positive" || d === "bullish" || d === "up" || d === "u" ? ["u", "↑ 正向"] : d === "negative" || d === "bearish" || d === "down" || d === "d" ? ["d", "↓ 负向"] : d === "mixed" ? ["dim", "↕ 多空交织"] : ["dim", "— 中性"];
    return `${head}
      <p style="margin:0 0 4px;font-size:13px;font-weight:600">${esc(selEvt ? selEvt.name : sel)} · 这场财报可能牵动什么</p>
      ${result && result.summary ? `<p style="margin:0 0 8px;font-family:var(--font-serif);font-size:13.5px;line-height:1.8;color:var(--ink-soft)">${esc(result.summary)}</p>` : ""}
      ${result && result.expectation ? `<p style="margin:0 0 14px;font-size:12px;line-height:1.7;color:var(--muted)">${esc(result.expectation)}</p>` : ""}
      ${N.RELATION_ORDER.filter(rel => groups.has(rel)).map(rel => `
        <p class="mono" style="font-size:9.5px;letter-spacing:.16em;color:var(--faint);margin:14px 0 7px">${N.RELATION_CN[rel]}</p>
        ${groups.get(rel).map(it => {
          const dm = dirMeta(it && it.direction);
          return `
        <div style="display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line-soft);font-size:12.5px">
          <b class="mono" style="min-width:44px;cursor:pointer" data-go="${esc(it.ticker || "")}">${esc(it.ticker || "—")}</b>
          <span class="${dm[0]}" style="min-width:52px">${dm[1]}</span>
          <span style="color:var(--muted)">${esc((it && it.reason) || "暂无具体说明")}</span>
        </div>`;
        }).join("")}`).join("") || `<p class="empty-note" style="padding:14px">暂未识别到显著关联公司。</p>`}
      <p class="mono" style="font-size:9.5px;color:var(--faint);margin:14px 0 0;line-height:1.7">${esc(impact && impact.model || "gpt-5.6-terra")} · ${esc(impact && impact.reasoning || "max")} · ${impact && (impact.cache_hit || impact.cached) ? "复用缓存" : "已保存结果"} · ${N.fmtDateTime(impact && (impact.completed_at || impact.generated_at))} · 模型推断，不代表收益概率。</p>`;
  }

  function selectedEarningsEvent() {
    if (!earnSel || !St.earnWeek) return null;
    for (const day of St.earnWeek.week) {
      const found = day.events.find(event => event.ticker === earnSel);
      if (found) return found;
    }
    return null;
  }

  function updateImpactPanel() {
    if (!location.hash.includes("earnings")) return;
    const panel = $("#earn-impact-panel", view);
    if (!panel) return;
    const y = window.scrollY;
    const hadFocus = panel.contains(document.activeElement);
    const focusId = hadFocus && document.activeElement.id;
    panel.innerHTML = renderImpactPanel(St.impacts[earnSel], earnSel, selectedEarningsEvent(), null, null);
    bindImpactActions();
    const nextFocus = focusId ? document.getElementById(focusId) : null;
    if (nextFocus && panel.contains(nextFocus)) nextFocus.focus({ preventScroll: true });
    else if (hadFocus) { panel.tabIndex = -1; panel.focus({ preventScroll: true }); }
    window.scrollTo({ top: y, behavior: "instant" });
  }

  function runEarningsImpact() {
    if (!earnSel || !Jobs || !privateActionsAvailable()) return;
    const ticker = earnSel;
    const event = selectedEarningsEvent();
    const payload = {
      ticker,
      force: ["failed", "cancelled"].includes(Jobs.normalizeStatus(St.impacts[ticker] && St.impacts[ticker].status)),
      name: event && event.name || "",
      sector: event && event.sector || "",
      earnings_date: event && event.earnings_date || "",
      eps_estimate: event && isNum(event.eps_estimate) ? event.eps_estimate : null,
      revenue_estimate: event && isNum(event.revenue_estimate) ? event.revenue_estimate : null,
      market_cap: event && isNum(event.market_cap) ? event.market_cap : null,
    };
    Jobs.start({
      scope: "earnings:" + ticker,
      create: signal => N.createEarningsImpactJob(payload, { signal }),
      poll: (jobId, signal) => N.aiJob(jobId, { signal }),
      cancel: jobId => N.cancelAiJob(jobId),
      onUpdate: job => { St.impacts[ticker] = job; if (earnSel === ticker) updateImpactPanel(); },
      onComplete: job => { St.impacts[ticker] = job; if (earnSel === ticker) updateImpactPanel(); },
      onError: error => { St.impacts[ticker] = { status: "failed", error_code: error.code || "job_request_failed", error_message: error.message }; if (earnSel === ticker) updateImpactPanel(); },
    });
  }

  function bindImpactActions() {
    const panel = $("#earn-impact-panel", view);
    if (!panel) return;
    const run = $("[data-impact-run]", panel); if (run) run.addEventListener("click", runEarningsImpact);
    const cancel = $("[data-impact-cancel]", panel); if (cancel) cancel.addEventListener("click", async () => {
      if (!window.confirm("确认取消这项财报关联分析？已完成的缓存结果不会被删除。")) return;
      cancel.disabled = true;
      await Jobs.cancel("earnings:" + earnSel);
    });
    $$("[data-go]", panel).forEach(element => element.addEventListener("click", () => openDrawer(element.dataset.go)));
  }

  /* ---------- 抽屉 ---------- */
  const drawer = $("#drawer"), backdrop = $("#drawer-backdrop");
  let lastFocusEl = null, drawerTimer = null, paletteTimer = null, drawerGen = 0;
  const inertTargets = [$(".deck-header"), view, $(".deck-foot"), $(".dock")].filter(Boolean);

  function setDrawerBackgroundInert(value) {
    inertTargets.forEach(element => { element.inert = value; });
  }

  function drawerShell(inner, options) {
    const opts = options || {};
    clearTimeout(drawerTimer);
    const wasHidden = drawer.hidden;
    const oldScroll = opts.preserveScroll ? drawer.scrollTop : 0;
    const focusId = !wasHidden && drawer.contains(document.activeElement) ? document.activeElement.id : null;
    if (wasHidden) lastFocusEl = document.activeElement;
    const labelled = String(inner).includes('id="drawer-title"')
      ? inner
      : `<h2 class="sr-only" id="drawer-title">${esc(opts.title || "详情")}</h2>${inner}`;
    drawer.innerHTML = `<div class="drawer__inner"><button class="drawer__close" id="drawer-close" aria-label="关闭详情">✕</button>${labelled}</div>`;
    drawer.hidden = false; backdrop.hidden = false;
    requestAnimationFrame(() => { drawer.classList.add("open"); backdrop.classList.add("open"); });
    document.body.style.overflow = "hidden";
    setDrawerBackgroundInert(true);
    $("#drawer-close").addEventListener("click", closeDrawer);
    drawer.scrollTop = oldScroll;
    const focusTarget = focusId ? document.getElementById(focusId) : null;
    if (focusTarget && drawer.contains(focusTarget)) focusTarget.focus({ preventScroll: true });
    else if (wasHidden) $("#drawer-close").focus({ preventScroll: true });
  }

  /* 候选证据抽屉(选股「查看完整证据」— 数据即扫描行本身) */
  function openCandEvidence(idx) {
    const rows = St.scan && St.scan.rows || [];
    const c = rows[idx];
    if (!c) return;
    const dg = ++drawerGen;
    const score = rnd0(c.final_score);
    const contributions = Object.entries(c.contributions || c.contribution_breakdown || (c.breakdown && c.breakdown.contributions) || {}).sort((a, b) => (b[1] || 0) - (a[1] || 0));
    const cw = c.configured_weights || {}, ew = c.effective_weights || {};
    const weightKeys = Array.from(new Set([...Object.keys(cw), ...Object.keys(ew)]));
    const pa = c.price_action || c.price_action_detail || (c.breakdown && c.breakdown.price_action_detail) || null;
    const vt = c.volume_truth || null;
    const pi = persistInfo(c);
    const cov = c.coverage || {};
    const covRows = [["ranking", "排序评分"], ["intrinsic", "内在强度"], ["profile_fit", "偏好适配"], ["market_fit", "市场适配"]]
      .map(([k, label]) => { const v = cov[k]; return v == null ? null : { label, ratio: isNum(v && v.ratio) ? v.ratio : (isNum(v) ? v : null), status: (v && v.status) || null }; })
      .filter(Boolean);
    const missing = c.missing_components || [];
    const palette = ["var(--amber)", "var(--amber-deep)", "var(--teal)", "var(--teal-deep)", "var(--faint)"];

    drawerShell(`
      <header class="drawer__head">
        <span class="mono">候选证据 · ${esc(c.sector_name || "全部板块")} · 强势股扫描</span>
        <h2 id="drawer-title">${esc(c.name || c.ticker)} <span class="dim" style="font-weight:400;font-size:17px">${esc(c.ticker)}</span></h2>
        <div class="drawer__price">
          <b>${fmt(c.price)}</b>
          <span class="${tone(c.change_pct)}">${arrow(c.change_pct)} ${pct(c.change_pct)}</span>
          <span class="chip ${score >= 72 ? "chip--up" : score >= 58 ? "chip--amber" : "chip--mute"}" style="align-self:center"><i></i>${esc(c.label || c.classification || "候选")} · 综合 ${score == null ? "—" : score}/100</span>
        </div>
        <span class="mono" style="font-size:10px;color:var(--faint)">延迟行情 · 本轮扫描 ${N.fmtTime(St.scan.as_of)} · ${esc((c.reasons || [])[0] || "")}</span>
      </header>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">SCORE</span><h2>评分构成</h2><span class="sect-head__rule"></span><span class="sect-head__meta">周期 · 0—100</span></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 24px">
          ${[["短期", c.score_short], ["中期", c.score_mid], ["长期", c.score_long], ["价格结构", c.price_action_score], ["突破质量", c.breakout_quality_score], ["期权热度", c.option_heat_score]].map(x => `
          <div class="ivbar" style="grid-template-columns:64px 1fr 34px;padding:7px 0;border:0">
            <span style="font-size:12px;color:var(--muted)">${x[0]}</span>
            <span class="ivbar__track"><span class="ivbar__fill" data-w="${rnd0(x[1]) || 0}" style="background:linear-gradient(90deg,var(--amber-deep),var(--amber))"></span></span>
            <b class="num" style="font-size:12px;text-align:right">${rnd0(x[1]) != null ? rnd0(x[1]) : "—"}</b>
          </div>`).join("")}
        </div>
        ${contributions.length ? `
        <div class="anatomy__bar" style="margin-top:14px">
          ${contributions.slice(0, 5).map((x, i) => `<span class="anatomy__seg" style="flex:${Math.max(x[1] || 0, 0.5)};background:${palette[i % palette.length]}" title="${esc(N.scanComponentCN(x[0]))} ${(x[1] || 0).toFixed(1)}"></span>`).join("")}
        </div>
        <div class="anatomy__legend">
          ${contributions.slice(0, 5).map((x, i) => `<span><i style="background:${palette[i % palette.length]}"></i>${esc(N.scanComponentCN(x[0]))} · ${(x[1] || 0).toFixed(1)}</span>`).join("")}
        </div>` : `<p class="mono" style="font-size:10px;color:var(--faint);margin:12px 0 0">本行未返回贡献构成字段</p>`}
        ${weightKeys.length ? `
        <details class="fold">
          <summary>查看配置权重 → 有效权重</summary>
          <div class="fold__body">
            <dl class="kv-grid" style="grid-template-columns:repeat(2,1fr)">
              ${weightKeys.map(k => `<div><dt>${esc(N.scanComponentCN(k))}</dt><dd style="font-size:12px">${isNum(cw[k]) ? Math.round(cw[k] * 100) + "%" : "—"} → ${isNum(ew[k]) ? Math.round(ew[k] * 100) + "%" : "0%(降级/再分配)"}</dd></div>`).join("")}
            </dl>
          </div>
        </details>` : ""}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">STRUCT</span><h2>价格结构与量价</h2><span class="sect-head__rule"></span></div>
        ${pa || vt ? `
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
          ${pa && (pa.structure_label || pa.structure) ? `<span class="chip chip--up"><i></i>${esc(pa.structure_label || pa.structure)}</span>` : ""}
          ${(pa && (pa.pattern_labels && pa.pattern_labels.length ? pa.pattern_labels : pa.patterns) || []).map(x => `<span class="chip chip--amber"><i></i>${esc(x)}</span>`).join("")}
          ${pa && pa.spring ? `<span class="chip chip--amber"><i></i>Spring 假跌破回收</span>` : ""}
          ${pa && pa.upthrust ? `<span class="chip chip--down"><i></i>Upthrust 假突破</span>` : ""}
          ${vt ? `<span class="chip chip--mute">${esc(vt.setup_label || "量价")}${isNum(vt.false_breakout_risk) && vt.false_breakout_risk > 0 ? " · 假突破风险 " + Math.round(vt.false_breakout_risk) + "%" : ""}</span>` : ""}
        </div>
        <dl class="kv-grid">
          ${pa && isNum(pa.support) ? `<div><dt>支撑</dt><dd>${fmt(pa.support)}${isNum(pa.support_dist_pct) ? `<small style="color:var(--muted);font-size:10px"> 距 ${pct(pa.support_dist_pct)}</small>` : ""}</dd></div>` : ""}
          ${pa && isNum(pa.resistance) ? `<div><dt>阻力</dt><dd>${fmt(pa.resistance)}${isNum(pa.resistance_dist_pct) ? `<small style="color:var(--muted);font-size:10px"> 距 ${pct(pa.resistance_dist_pct)}</small>` : ""}</dd></div>` : ""}
          ${pa && isNum(pa.score) ? `<div><dt>结构评分</dt><dd>${rnd0(pa.score)}</dd></div>` : ""}
          ${vt && isNum(vt.breakout_quality_adjustment) ? `<div><dt>量价判定</dt><dd style="font-size:12px">突破质量修正 ${sign(vt.breakout_quality_adjustment)}</dd></div>` : ""}
        </dl>` : `<p class="mono" style="font-size:10.5px;color:var(--faint);margin:0">本行未返回价格结构明细(price_action / volume_truth)。</p>`}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">PERSIST</span><h2>区间持续性</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${esc(pi.statusTxt)}</span></div>
        <dl class="kv-grid">
          <div><dt>生产评分</dt><dd>${pi.prod != null ? rnd0(pi.prod) : "—"}</dd></div>
          <div><dt>影子假设分</dt><dd>${pi.hypo != null ? rnd0(pi.hypo) : "—"}</dd></div>
          <div><dt>已应用分差</dt><dd>${pi.delta != null ? sign(pi.delta) : "—"}</dd></div>
          <div><dt>5日斜率</dt><dd>${pi.slope != null ? pct(pi.slope) : "—"}</dd></div>
          <div><dt>10日高位比例</dt><dd>${pi.ratio != null ? Math.round(pi.ratio) + "%" : "—"}</dd></div>
          <div><dt>有效权重 / 上限</dt><dd>${pi.effW != null ? Math.round(pi.effW * 100) + "%" : "—"} / ${pi.cap != null ? Math.round(pi.cap * 100) + "%" : "—"}</dd></div>
        </dl>
        <p class="mono" style="font-size:9.5px;color:var(--faint);margin:10px 0 0">${esc(pi.version || St.scan.range_persistence_version || "")} · 影子指标不参与正式排序,仅供研究对照。</p>
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">WHY</span><h2>入选依据与风险</h2><span class="sect-head__rule"></span></div>
        <ul style="margin:0 0 12px;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px">
          ${(c.reasons && c.reasons.length ? c.reasons : ["本轮扫描未给出入选说明"]).map(r2 => `<li style="font-size:12.5px;color:var(--ink-soft);display:flex;gap:8px"><span style="color:var(--up)">＋</span>${esc(r2)}</li>`).join("")}
        </ul>
        <ul style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px">
          ${(c.warnings && c.warnings.length ? c.warnings : ["本轮扫描未触发风险规则"]).map(r2 => `<li style="font-size:12.5px;color:var(--muted);display:flex;gap:8px"><span style="color:var(--down)">－</span>${esc(r2)}</li>`).join("")}
        </ul>
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">NEWS</span><h2>新闻催化剂</h2><span class="sect-head__rule"></span><span class="sect-head__meta">展示信息 · 不参与排序</span></div>
        <div class="cat-inline-panel" id="cand-catalysts"></div>
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">AUDIT</span><h2>数据覆盖与降级</h2><span class="sect-head__rule"></span></div>
        ${covRows.length ? `
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
          ${covRows.map(x => `<span class="chip ${x.status === "degraded" || (x.ratio != null && x.ratio < 0.85) ? "chip--amber" : "chip--mute"}"><i style="background:${x.status === "degraded" || (x.ratio != null && x.ratio < 0.85) ? "var(--amber)" : "var(--up)"}"></i>${x.label} · ${x.ratio != null ? Math.round(x.ratio * 100) + "%" : esc(x.status || "—")}</span>`).join("")}
        </div>` : `<p class="mono" style="font-size:10.5px;color:var(--faint);margin:0 0 10px">本行未返回覆盖率字段。</p>`}
        ${missing.length ? `<p style="font-size:12px;color:var(--muted);margin:0;display:flex;gap:8px"><span style="color:var(--amber)">⚠</span>缺失分量:${missing.map(m => esc(N.scanComponentCN(m))).join("、")} —— 权重已按规则再分配,不以默认分填充。</p>` : ""}
        <p class="mono" style="font-size:9.5px;color:var(--faint);margin:14px 0 0;line-height:1.8">评分 ${esc(St.scan.score_version || "—")} · 特征 ${esc(St.scan.feature_version || "—")} · 归一化 ${esc(St.scan.normalization_version || "—")} · 数据 ${N.fmtTime(St.scan.as_of)} · 仅作研究,不构成投资建议。</p>
      </section>

      <div style="display:flex;gap:10px;margin-top:22px">
        <button class="btn btn--amber" id="cevd-open-research">打开研究页 →</button>
        <span class="mono" style="font-size:9.5px;color:var(--faint);align-self:center">证据字段来自 /api/strength/scan 实时输出</span>
      </div>`);
    animateBars(drawer);
    $("#cevd-open-research").addEventListener("click", () => openDrawer(c.ticker));
    if (C) C.mountTickerPanel($("#cand-catalysts", drawer), c.ticker, { limit: 3, windowHours: 72, context: "screener" });
  }

  /* 事件证据抽屉(突破雷达 — 事件详情 + 该股历史) */
  async function openEvidenceDrawer(eventId) {
    const dg = ++drawerGen;
    drawerShell(loadingView("正在读取事件证据…"));
    const detail = await settle(N.breakoutEventDetail(eventId));
    if (dg !== drawerGen) return;
    if (!detail.ok) {
      drawerShell(`<div style="padding:30px 6px">${inlineErr("事件证据读取失败", detail.e.message)}</div>`);
      return;
    }
    const Dv = detail.v, L = Dv.event;
    const hist = await settle(N.breakoutTicker(L.ticker));
    if (dg !== drawerGen) return;
    const price = L.current_price != null ? L.current_price : L.event_price;
    const scores8 = [
      ["内在强度", L.intrinsic_strength_score], ["基底质量", L.base_quality_score],
      ["突破确认", L.breakout_confirmation_score], ["流动性", L.liquidity_quality_score],
      ["突破质量", L.breakout_quality_score], ["数据可信度", L.data_confidence_score],
      ["追高风险", L.chase_risk_score, true], ["告警优先级", L.alert_priority_score],
    ];
    const ew = L.effective_weights || {};
    const trans = Dv.transitions || [];
    const recent = ((hist.ok && hist.v.events) || []).filter(e => e.event_id !== L.event_id).slice(0, 4);
    const srcs = Object.entries(L.source_status || {}).filter(([k, v]) => typeof v === "string");
    const shape = L.market_shape || {};
    const zone = z => z && isNum(z.low) && isNum(z.high) ? fmt(z.low) + " — " + fmt(z.high) : "—";

    drawerShell(`
      <header class="drawer__head">
        <span class="mono">事件证据 · ${N.SETUP_CN[L.setup_type] || esc(L.setup_type)} · ${esc(L.exchange || "")}</span>
        <h2 id="drawer-title">${esc(L.name || L.ticker)} <span class="dim" style="font-weight:400;font-size:17px">${esc(L.ticker)}</span></h2>
        <div class="drawer__price">
          <b>${fmt(price)}</b>
          <span class="${tone(L.session_change_pct)}">${arrow(L.session_change_pct)} ${pct(L.session_change_pct)}</span>
          <span class="chip ${lifeChip(L.lifecycle_state)}" style="align-self:center"><i></i>${N.LIFECYCLE_CN[L.lifecycle_state] || esc(L.lifecycle_state)}</span>
        </div>
        <span class="mono" style="font-size:10px;color:var(--faint)">延迟行情 · 事件 ${N.fmtET(L.event_at)} · 最近观测 ${N.ago(L.last_seen_at)}</span>
      </header>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">STRUCT</span><h2>价格结构</h2><span class="sect-head__rule"></span></div>
        <dl class="kv-grid">
          <div><dt>突破枢轴</dt><dd>${fmt(L.pivot_price)}</dd></div>
          <div><dt>失效位置</dt><dd class="d">${fmt(L.invalidation_price)}</dd></div>
          <div><dt>同时段量能</dt><dd>${isNum(L.rvol_time_of_day) ? L.rvol_time_of_day.toFixed(1) + "×" : "—"}</dd></div>
          <div><dt>支撑区域</dt><dd style="font-size:12px">${zone(L.support_zone)}</dd></div>
          <div><dt>阻力区域</dt><dd style="font-size:12px">${zone(L.resistance_zone)}</dd></div>
          <div><dt>跳空幅度</dt><dd>${pct(L.gap_pct)}</dd></div>
        </dl>
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">SCORE</span><h2>正式评分</h2><span class="sect-head__rule"></span><span class="sect-head__meta">8 项 · 0—100</span></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 24px">
          ${scores8.map(x => `
          <div class="ivbar" style="grid-template-columns:86px 1fr 34px;padding:7px 0;border:0">
            <span style="font-size:12px;color:var(--muted)">${x[0]}</span>
            <span class="ivbar__track"><span class="ivbar__fill" data-w="${rnd0(x[1]) || 0}" style="background:${x[2] ? "linear-gradient(90deg,var(--down-deep),var(--down))" : "linear-gradient(90deg,var(--amber-deep),var(--amber))"}"></span></span>
            <b class="num" style="font-size:12px;text-align:right;color:${x[2] && (x[1] || 0) >= 60 ? "var(--down)" : "var(--ink)"}">${rnd0(x[1]) != null ? rnd0(x[1]) : "—"}</b>
          </div>`).join("")}
        </div>
        ${Object.keys(ew).length ? `
        <details class="fold">
          <summary>查看有效权重与罚项</summary>
          <div class="fold__body">
            <dl class="kv-grid" style="grid-template-columns:repeat(3,1fr)">
              ${Object.entries(ew).map(([k, v]) => `<div><dt>${esc(N.scanComponentCN(k))}</dt><dd>${isNum(v) ? Math.round(v * 100) + "%" : "—"}</dd></div>`).join("")}
              ${Object.entries(L.penalties || {}).filter(([k, v]) => isNum(v) && v !== 0).map(([k, v]) => `<div><dt style="color:var(--down)">罚 · ${esc(N.scanComponentCN(k))}</dt><dd class="d">${sign(v)}</dd></div>`).join("")}
            </dl>
          </div>
        </details>` : ""}
        <div class="ms-shape" style="margin-top:12px">
          <span><small class="mono">区间强势持续度</small><strong>${rnd0(L.range_persistence) != null ? rnd0(L.range_persistence) : "—"}</strong></span>
          <dl>
            <div><dt>5日斜率</dt><dd>${isNum(L.range_persistence_slope_5d) ? pct(L.range_persistence_slope_5d) : "—"}</dd></div>
            <div><dt>10日高位比例</dt><dd>${isNum(L.range_persistence_ratio_10d) ? Math.round(L.range_persistence_ratio_10d) + "%" : "—"}</dd></div>
          </dl>
          <span class="chip chip--amber" style="align-self:center"><i></i>${L.range_persistence_status === "scored" ? "已参与评分" : "影子观察 · 不参与排序"}</span>
        </div>
        ${shape.state ? `
        <div class="ms-shape" style="margin-top:10px">
          <span><small class="mono">扫描时市场形态</small><strong>${esc(N.shapeCN(shape.state))}</strong></span>
          <dl>
            <div><dt>置信度</dt><dd>${isNum(shape.confidence) ? Math.round(shape.confidence * (shape.confidence <= 1 ? 100 : 1)) + "%" : "—"}</dd></div>
            <div><dt>转向风险</dt><dd>${isNum(shape.transition_risk) ? Math.round(shape.transition_risk * (shape.transition_risk <= 1 ? 100 : 1)) + "%" : "—"}</dd></div>
          </dl>
          <span class="chip chip--mute" style="align-self:center">${esc(shape.version || "")}</span>
        </div>` : ""}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">CHANGES</span><h2>状态变化</h2><span class="sect-head__rule"></span><span class="sect-head__meta">共 ${trans.length} 次</span></div>
        ${trans.length ? trans.slice(0, 8).map(t2 => `
        <div class="evd-row">
          <b class="mono" style="font-size:12px;white-space:nowrap">${esc(N.LIFECYCLE_CN[t2.from_state] || t2.from_state || "…")} → ${esc(N.LIFECYCLE_CN[t2.to_state] || t2.to_state || "…")}</b>
          <span style="color:var(--muted);flex:1">${esc(t2.reason || t2.note || "")}</span>
          <span class="mono" style="font-size:10.5px;color:var(--faint);white-space:nowrap">${N.fmtET(t2.at || t2.transitioned_at || t2.created_at)}</span>
        </div>`).join("") : `<p class="mono" style="font-size:10.5px;color:var(--faint)">接口未返回状态变化记录。</p>`}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">CATALYST</span><h2>新闻催化剂</h2><span class="sect-head__rule"></span><span class="sect-head__meta">最近 3 条 · 不改变突破判断</span></div>
        <div class="cat-inline-panel" id="evd-catalysts"></div>
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">RECENT</span><h2>该股票近期事件</h2><span class="sect-head__rule"></span></div>
        ${recent.length ? recent.map(r2 => `
        <div class="evd-row" data-evt2="${esc(r2.event_id)}" style="cursor:pointer">
          <b style="font-size:12.5px">${N.SETUP_CN[r2.setup_type] || esc(r2.setup_type)}</b>
          <span class="chip ${lifeChip(r2.lifecycle_state)}">${N.LIFECYCLE_CN[r2.lifecycle_state] || esc(r2.lifecycle_state)}</span>
          <span class="mono" style="font-size:10.5px;color:var(--faint);margin-left:auto">${N.fmtDateTime(r2.event_at)}</span>
        </div>`).join("") : `<p class="mono" style="font-size:10.5px;color:var(--faint)">${hist.ok ? "该股票近期没有其他突破事件。" : "历史事件读取失败:" + esc(hist.e && hist.e.message || "")}</p>`}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">AUDIT</span><h2>数据来源与限制</h2><span class="sect-head__rule"></span></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
          ${srcs.map(([k, v]) => `<span class="chip ${v === "active" ? "chip--mute" : "chip--amber"}"><i style="background:${v === "active" ? "var(--up)" : "var(--amber)"}"></i>${esc(k)} · ${N.srcCN(v)}</span>`).join("")}
        </div>
        ${(L.warnings || []).length ? `
        <ul style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px">
          ${L.warnings.map(w2 => `<li style="font-size:12px;color:var(--muted);display:flex;gap:8px"><span style="color:var(--amber)">⚠</span>${esc(w2)}</li>`).join("")}
        </ul>` : ""}
        ${(L.missing_components || []).length ? `<p style="font-size:12px;color:var(--muted);margin:10px 0 0;display:flex;gap:8px"><span style="color:var(--amber)">⚠</span>缺失分量:${L.missing_components.map(m => esc(N.scanComponentCN(m))).join("、")}</p>` : ""}
        <p class="mono" style="font-size:9.5px;color:var(--faint);margin:14px 0 0;line-height:1.8">评分 ${esc(L.score_version || "—")} · 接口 ${esc((Dv.versions || {}).api_schema || "—")} · 事件ID ${esc(L.event_id)}</p>
      </section>

      <div style="display:flex;gap:10px;margin-top:22px">
        <button class="btn btn--amber" id="evd-open-research">打开研究页 →</button>
        <span class="mono" style="font-size:9.5px;color:var(--faint);align-self:center">全部字段来自 /api/breakouts/events/${esc(eventId).slice(0, 10)}…</span>
      </div>`);
    animateBars(drawer);
    $("#evd-open-research").addEventListener("click", () => openDrawer(L.ticker));
    $$("[data-evt2]", drawer).forEach(el => el.addEventListener("click", () => openEvidenceDrawer(el.dataset.evt2)));
    if (C) C.mountTickerPanel($("#evd-catalysts", drawer), L.ticker, { limit: 3, windowHours: 72, asOf: L.event_at, context: "breakout" });
  }

  /* 指数抽屉:行情 + 大盘强弱观察 */
  async function openIndexDrawer(ticker) {
    const dg = ++drawerGen;
    const info = N.indexInfo(ticker);
    drawerShell(loadingView("正在读取 " + info.cn + " …"));
    const [q, ch, sig, strength, sigMkt, brkCur] = await Promise.all([
      settle(N.stock(ticker)), settle(N.chart(ticker, "1d")), settle(N.stockSignals(ticker)),
      info.us ? settle(N.strengthMarket()) : Promise.resolve({ ok: false, skip: true }),
      info.us ? settle(N.signalsMarket()) : Promise.resolve({ ok: false, skip: true }),
      info.us ? settle(N.breakoutsCurrent()) : Promise.resolve({ ok: false, skip: true }),
    ]);
    if (dg !== drawerGen) return;
    if (!q.ok) { drawerShell(`<div style="padding:30px 6px">${inlineErr("指数行情读取失败", q.e.message)}</div>`); return; }
    const Q = q.v;
    const reg = strength.ok && strength.v.market_regime || null;
    const SM = sigMkt.ok ? sigMkt.v : null;
    const scores = SM && SM.scores || null;
    const shapeEvt = brkCur.ok && (brkCur.v.events || []).find(e => e.market_shape && e.market_shape.state);
    const shape = shapeEvt ? shapeEvt.market_shape : null;
    const tech = sig.ok ? sig.v : null;

    const dims2 = reg ? N.STRENGTH_DIMS.map(([key, label]) => `
      <div class="ivbar" style="grid-template-columns:86px 1fr 34px;padding:7px 0;border:0">
        <span style="font-size:12px;color:var(--muted)">${label}</span>
        <span class="ivbar__track"><span class="ivbar__fill" data-w="${rnd0(reg[key]) || 0}" style="background:linear-gradient(90deg,var(--amber-deep),var(--amber))"></span></span>
        <b class="num" style="font-size:12px;text-align:right">${rnd0(reg[key]) != null ? rnd0(reg[key]) : "—"}</b>
      </div>`).join("") : "";

    const msCard = (t2, score, label, breakdown, cnMap, reasons, toneColor) => `
      <article class="ms-card">
        <header style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px">
          <span class="mono" style="font-size:10px;letter-spacing:.12em;color:var(--faint)">${t2}</span>
          <span style="display:flex;align-items:baseline;gap:8px"><b class="num" style="font-size:19px;color:${toneColor}">${rnd0(score) != null ? rnd0(score) : "—"}</b><small style="font-size:11px;color:var(--muted)">${esc(label || "")}</small></span>
        </header>
        <dl style="display:flex;gap:14px;margin:0 0 9px;padding:0;flex-wrap:wrap">
          ${Object.entries(breakdown || {}).filter(([k, v]) => isNum(v)).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `<div style="flex:1;min-width:64px"><dt class="mono" style="font-size:9px;color:var(--faint);letter-spacing:.08em;margin-bottom:2px">${esc(cnMap[k] || k)}</dt><dd class="num" style="margin:0;font-size:12.5px;font-weight:600">${Math.round(v)}</dd></div>`).join("")}
        </dl>
        <ul style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:5px">
          ${[...(reasons && reasons.raising || []).map(r => "抬升:" + r), ...(reasons && reasons.suppressing || []).map(r => "抑制:" + r)].slice(0, 4).map(r2 => `<li style="font-size:11.5px;color:var(--muted);line-height:1.6">${esc(r2)}</li>`).join("")}
        </ul>
      </article>`;

    drawerShell(`
      <header class="drawer__head">
        <span class="mono">指数研究 · ${esc(info.badge)} · 延迟行情</span>
        <h2 id="drawer-title">${esc(info.cn)} <span class="dim" style="font-weight:400;font-size:16px">${esc(ticker)}</span></h2>
        <div class="drawer__price">
          <b>${fmt(Q.price)}</b>
          <span class="${tone(Q.change_percent)}">${arrow(Q.change_percent)} ${pct(Q.change_percent)}</span>
        </div>
        <span class="mono" style="font-size:10px;color:var(--faint)">最新点位 · 更新 ${N.fmtTime(Q.as_of)}</span>
      </header>

      <div style="margin:18px 0 6px" id="dr-chart">${ch.ok ? lineArea(ch.v.bars.map(b => b.c), 540, 150, "idx-g") : inlineErr("走势读取失败", ch.e && ch.e.message)}</div>
      <div class="focus-ranges" style="padding:0">
        ${N.CHART_RANGES.map((r2, i2) => `<button class="rangebtn ${r2.key === "1d" ? "active" : ""}" data-dr="${r2.key}">${r2.label}</button>`).join("")}
      </div>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">KEY</span><h2>今日点位与区间</h2><span class="sect-head__rule"></span></div>
        <dl class="kv-grid">
          <div><dt>开盘点位</dt><dd>${fmt(Q.open)}</dd></div>
          <div><dt>日内高点</dt><dd>${fmt(Q.high)}</dd></div>
          <div><dt>日内低点</dt><dd>${fmt(Q.low)}</dd></div>
          <div><dt>成交量</dt><dd>${Q.volume ? N.cnAmount(Q.volume) : "—"}</dd></div>
          <div><dt>52周高点</dt><dd>${fmt(Q.year_high)}</dd></div>
          <div><dt>52周低点</dt><dd>${fmt(Q.year_low)}</dd></div>
        </dl>
      </section>

      ${info.us ? `
      <section class="sect" aria-label="大盘强弱观察">
        <div class="sect-head"><span class="sect-head__no">STRENGTH</span><h2>大盘强势技术判断</h2><span class="sect-head__rule"></span><span class="sect-head__meta">市场环境研究 · ${reg ? (reg.status === "active" ? "数据正常" : "数据受限") : "读取失败"}</span></div>
        ${reg ? `
        <div style="display:flex;gap:16px;align-items:center;margin-bottom:6px">
          ${ring(reg.score, 56)}
          <div>
            <div style="font-size:15px;font-weight:650">${esc(reg.label || "数据不足")}</div>
            <div class="mono" style="font-size:10px;color:var(--faint);margin-top:3px">输入覆盖 ${reg.input_coverage ? Math.round((reg.input_coverage.ratio || 0) * 100) : "—"}% · 数据 ${N.fmtTime(reg.as_of)} · /api/strength/market</div>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 22px;margin-bottom:14px">${dims2}</div>` : inlineErr("大盘强弱读取失败", strength.e && strength.e.message)}

        ${shape ? `
        <div class="ms-shape">
          <span><small class="mono">市场形态</small><strong>${esc(N.shapeCN(shape.state))}</strong></span>
          <dl>
            <div><dt>置信度</dt><dd>${isNum(shape.confidence) ? Math.round(shape.confidence * (shape.confidence <= 1 ? 100 : 1)) + "%" : "—"}</dd></div>
            <div><dt>转向风险</dt><dd>${isNum(shape.transition_risk) ? Math.round(shape.transition_risk * (shape.transition_risk <= 1 ? 100 : 1)) + "%" : "—"}</dd></div>
          </dl>
          <span class="chip chip--mute" style="align-self:center">${esc(shape.version || "")}</span>
        </div>
        ${Object.keys(shape.rules || {}).length ? `
        <details class="fold" style="margin-top:0;border-top:0">
          <summary>形态依据与版本</summary>
          <div class="fold__body">
            <dl class="kv-grid" style="grid-template-columns:repeat(2,1fr)">
              ${Object.entries(shape.rules).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd style="font-size:12px">${esc(String(v))}</dd></div>`).join("")}
            </dl>
          </div>
        </details>` : ""}` : `
        <div class="ms-shape">
          <span><small class="mono">市场形态</small><strong>暂无快照</strong></span>
          <dl><div><dt>说明</dt><dd style="font-size:11.5px">形态判定随突破扫描快照产出;${brkCur.ok && brkCur.v.runtime_status === "paused" ? "当前休市暂停扫描" : "当前无带形态的活跃事件"}</dd></div></dl>
          <span class="chip chip--mute" style="align-self:center">market-shape-v3</span>
        </div>`}

        <div class="sect-head" style="margin-top:20px"><span class="sect-head__no">BREADTH</span><h2 style="font-size:14px">广度动量 · 全市场见顶风险与见底修复</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${scores ? "数据质量 " + (scores.data_quality != null ? scores.data_quality : "—") + "%" : "读取失败"}</span></div>
        ${scores ? `
        <div class="ms-cards">
          ${msCard("技术见顶风险", scores.top_score, scores.top_label, scores.top_breakdown, N.TOP_BREAKDOWN_CN, scores.top_reasons, (scores.top_score || 0) >= 60 ? "var(--down)" : "var(--ink)")}
          ${msCard("技术见底修复", scores.bottom_score, scores.bottom_label, scores.bottom_breakdown, N.BOTTOM_BREAKDOWN_CN, scores.bottom_reasons, (scores.bottom_score || 0) >= 60 ? "var(--up)" : "var(--ink)")}
        </div>
        <p class="mono" style="font-size:9px;color:var(--faint);margin:10px 0 0;line-height:1.7">技术判断 ${N.fmtTime(SM.as_of)} · 分数表示条件聚合强度,不代表反转已经发生。</p>` : inlineErr("见顶/见底信号读取失败", sigMkt.e && sigMkt.e.message)}

        ${tech ? `
        <div class="sect-head" style="margin-top:20px"><span class="sect-head__no">TREND</span><h2 style="font-size:14px">趋势结构 · ${esc(info.cn)}自身技术面</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${N.srcCN(tech.source_status)}</span></div>
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:6px">
          ${ring(tech.score, 46)}
          <div><span class="mono" style="font-size:9.5px;letter-spacing:.12em;color:var(--faint)">技术倾向</span><div style="font-size:14px;font-weight:650">${sigCN(tech.overall)}</div></div>
        </div>
        ${Object.values(tech.signals || {}).map(s2 => `
        <div class="sig-row">
          <span class="sig-row__lamp ${s2.signal === "bullish" || s2.signal === "above" || s2.signal === "elevated" ? "on-u" : s2.signal === "bearish" || s2.signal === "below" ? "on-d" : "on-n"}"></span>
          <b>${esc(s2.label)}</b>
          <span class="num" style="color:var(--ink-soft)">${fmt(s2.value)}</span>
          <span class="mono">${sigCN(s2.signal)}</span>
        </div>`).join("")}
        <p class="mono" style="font-size:9px;color:var(--faint);margin:10px 0 0;line-height:1.7">指数自身技术分只使用该指数价格历史,不代替全市场广度与风险判断。</p>` : ""}

        <p class="mono" style="font-size:9.5px;color:var(--amber);margin:16px 0 0;letter-spacing:.04em">仅作市场环境研究,不构成买卖信号。</p>
      </section>` : `
      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">NOTE</span><h2>大盘强弱观察</h2><span class="sect-head__rule"></span></div>
        <div class="empty-note" style="padding:20px 10px;text-align:left">
          <p style="font-size:13.5px">该指数暂不参与大盘强弱观察</p>
          <small style="margin-bottom:0">市场环境研究目前只覆盖美股市场;此页仅提供行情与走势,缺失能力如实标注,不以空数据填充。</small>
        </div>
        ${tech ? `
        <div style="margin-top:14px">
        ${Object.values(tech.signals || {}).map(s2 => `
        <div class="sig-row">
          <span class="sig-row__lamp ${s2.signal === "bullish" || s2.signal === "above" ? "on-u" : s2.signal === "bearish" || s2.signal === "below" ? "on-d" : "on-n"}"></span>
          <b>${esc(s2.label)}</b>
          <span class="num" style="color:var(--ink-soft)">${fmt(s2.value)}</span>
          <span class="mono">${sigCN(s2.signal)}</span>
        </div>`).join("")}
        </div>` : ""}
      </section>`}`);
    measurePaths(drawer); animateBars(drawer);
    bindDrawerRanges(ticker, "idx-g", dg);
    countUp(drawer);
  }

  /* 个股抽屉(研究页) */
  async function openDrawer(ticker) {
    if (!ticker) return;
    if (isIndexSym(ticker)) { openIndexDrawer(ticker); return; }
    const dg = ++drawerGen;
    drawerShell(loadingView("正在读取 " + ticker + " 研究数据…"));
    const [q, ch, sig, deep, exps] = await Promise.all([
      settle(N.stock(ticker)), settle(N.chart(ticker, "1d")), settle(N.stockSignals(ticker)),
      settle(N.signalDeep(ticker)), settle(N.expirations(ticker)),
    ]);
    if (dg !== drawerGen) return;
    if (!q.ok) { drawerShell(`<div style="padding:30px 6px">${inlineErr("标的行情读取失败", q.e.message + (q.e.status === 404 ? " · 该代码可能不在数据源覆盖内" : ""))}</div>`); return; }
    const Q = q.v;
    let chainData = null, chainExp = null;
    if (exps.ok && (exps.v.expirations || []).length) {
      chainExp = exps.v.expirations[0];
      const cr = await settle(N.chain(ticker, chainExp));
      if (dg !== drawerGen) return;
      chainData = cr.ok ? cr.v : null;
    }
    const S2 = deep.ok ? deep.v : null;
    const sc = S2 && S2.scores || {};
    const tech = sig.ok ? sig.v : null;

    /* 期权链:平值 ±4 档 */
    let chainRows = [], chainSummary = [], alerts = [];
    if (chainData) {
      const up2 = chainData.underlying_price;
      const strikes = (chainData.strikes || []).slice().sort((a, b) => a - b);
      let atmIdx = 0, best = Infinity;
      strikes.forEach((s3, i) => { const d3 = Math.abs(s3 - up2); if (d3 < best) { best = d3; atmIdx = i; } });
      const sel = strikes.slice(Math.max(0, atmIdx - 4), atmIdx + 5);
      const byStrike = {};
      (chainData.calls || []).forEach(c2 => { (byStrike[c2.strike] = byStrike[c2.strike] || {}).c = c2; });
      (chainData.puts || []).forEach(p2 => { (byStrike[p2.strike] = byStrike[p2.strike] || {}).p = p2; });
      chainRows = sel.map(s3 => {
        const pair = byStrike[s3] || {};
        return { strike: s3, atm: s3 === strikes[atmIdx], c: pair.c, p: pair.p };
      });
      const cv = (chainData.calls || []).reduce((a, x) => a + (x.volume || 0), 0);
      const pv = (chainData.puts || []).reduce((a, x) => a + (x.volume || 0), 0);
      const coi = (chainData.calls || []).reduce((a, x) => a + (x.open_interest || 0), 0);
      const poi = (chainData.puts || []).reduce((a, x) => a + (x.open_interest || 0), 0);
      alerts = chainData.alerts || [];
      chainSummary = [
        { k: "看跌/看涨成交", v: cv ? (pv / cv).toFixed(2) : "—" },
        { k: "看跌/看涨持仓", v: coi ? (poi / coi).toFixed(2) : "—" },
        { k: "异动提示", v: alerts.length + " 条" },
      ];
    }
    const g4 = [
      { k: "趋势偏向", v: S2 && S2.trend_bias_score, risk: false },
      { k: "顶部风险", v: sc.top_score, risk: true },
      { k: "底部机会", v: sc.bottom_score, risk: false },
      { k: "回调质量", v: sc.dip_buy_quality, risk: false },
    ];
    const sigRows = tech ? Object.values(tech.signals || {}) : [];

    drawerShell(`
      <header class="drawer__head">
        <span class="mono">标的研究 · ${esc(Q.sic_description || "")}${Q._stale ? " · 过期快照" : ""}</span>
        <h2 id="drawer-title">${esc(Q.name || ticker)} <span class="dim" style="font-weight:400;font-size:17px">${esc(ticker)}</span></h2>
        <div class="drawer__price">
          <b>${fmt(Q.price)}</b>
          <span class="${tone(Q.change_percent)}">${arrow(Q.change_percent)} ${pct(Q.change_percent)} · ${sign(Q.change)}</span>
        </div>
        <span class="mono" style="font-size:10px;color:var(--faint)">延迟行情 · 更新 ${N.fmtTime(Q.as_of)}${Q.name_en ? " · " + esc(Q.name_en) : ""}</span>
      </header>

      <div style="margin:18px 0 6px" id="dr-chart">${ch.ok ? lineArea(ch.v.bars.map(b => b.c), 540, 150, "drawer-g") : inlineErr("走势读取失败", ch.e && ch.e.message)}</div>
      <div class="focus-ranges" style="padding:0">
        ${N.CHART_RANGES.map(r2 => `<button class="rangebtn ${r2.key === "1d" ? "active" : ""}" data-dr="${r2.key}">${r2.label}</button>`).join("")}
        <span style="flex:1"></span>
        <button class="rangebtn active" data-adj="raw">原始</button><button class="rangebtn" data-adj="adjusted">复权</button>
      </div>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">KEY</span><h2>今日价格与估值</h2><span class="sect-head__rule"></span></div>
        <dl class="kv-grid">
          <div><dt>开盘</dt><dd>${fmt(Q.open)}</dd></div>
          <div><dt>最高</dt><dd>${fmt(Q.high)}</dd></div>
          <div><dt>最低</dt><dd>${fmt(Q.low)}</dd></div>
          <div><dt>成交量</dt><dd>${Q.volume ? N.cnAmount(Q.volume) : "—"}</dd></div>
          <div><dt>市值</dt><dd>${isNum(Q.market_cap) ? "$" + N.cnAmount(Q.market_cap) : "—"}</dd></div>
          <div><dt>市盈率</dt><dd>${isNum(Q.pe_ratio) ? Q.pe_ratio.toFixed(1) : "—"}</dd></div>
          <div><dt>52周高点</dt><dd>${fmt(Q.year_high)}</dd></div>
          <div><dt>52周低点</dt><dd>${fmt(Q.year_low)}</dd></div>
          <div><dt>股息率</dt><dd>${isNum(Q.dividend_yield) ? Q.dividend_yield.toFixed(2) + "%" : "—"}</dd></div>
        </dl>
        ${Q.description ? `<p style="margin:12px 0 0;font-size:12px;line-height:1.7;color:var(--muted)">${esc(Q.description)}</p>` : ""}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">NEWS</span><h2>新闻催化</h2><span class="sect-head__rule"></span><span class="sect-head__meta">最近 72 小时 · 展示信息</span></div>
        <div class="cat-inline-panel" id="stock-catalysts"></div>
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">SIG</span><h2>技术信号 · 顶底分析</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${S2 ? "覆盖 " + Math.round(((sc.coverage || {}).top_ratio || 0) * 100) + "%" : deep.ok ? "" : "深度信号读取失败"}</span></div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px">
          ${g4.map(g5 => `
          <div style="text-align:center;padding:12px 6px;border:1px solid var(--line-soft);border-radius:12px">
            ${ring(g5.v, 48, g5.risk)}
            <div class="mono" style="font-size:9.5px;color:var(--muted);margin-top:7px;letter-spacing:.08em">${g5.k}</div>
          </div>`).join("")}
        </div>
        ${S2 && sc.dip_buy_label ? `<p class="mono" style="font-size:10px;color:var(--faint);margin:0 0 12px">回调质量 · ${esc(sc.dip_buy_label)}${S2.trend_bias_label ? " · 趋势偏向 " + esc(S2.trend_bias_label) : ""}${(sc.coverage && (sc.coverage.top_missing_components || []).length) ? " · 部分分量缺数据,按有效权重折算" : ""}</p>` : ""}
        ${sigRows.length ? sigRows.map(s3 => `
        <div class="sig-row">
          <span class="sig-row__lamp ${s3.signal === "bullish" || s3.signal === "above" || s3.signal === "elevated" ? "on-u" : s3.signal === "bearish" || s3.signal === "below" ? "on-d" : "on-n"}"></span>
          <b>${esc(s3.label)}</b>
          <span class="num" style="color:var(--ink-soft)">${fmt(s3.value)}</span>
          <span class="mono">${sigCN(s3.signal)}</span>
        </div>`).join("") : `<p class="mono" style="font-size:10.5px;color:var(--faint)">技术信号读取失败${sig.ok ? "" : ":" + esc(sig.e && sig.e.message || "")}</p>`}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">OPT</span><h2>期权链${chainExp ? " · " + esc(chainExp) : ""}</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${chainData ? "平值附近 ±4 档" : ""}</span></div>
        ${chainData ? `
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
          ${chainSummary.map(s3 => `<span class="chip chip--mute">${s3.k} <b class="num" style="margin-left:4px">${s3.v}</b></span>`).join("")}
        </div>
        <div class="tbl-wrap">
          <table class="chain">
            <thead><tr>
              <th class="chain__side-c" colspan="4">看涨期权</th><th>行权价</th><th class="chain__side-p" colspan="4">看跌期权</th>
            </tr><tr>
              <th>成交量</th><th>持仓量</th><th>Δ</th><th>隐波</th><th></th><th>隐波</th><th>Δ</th><th>持仓量</th><th>成交量</th>
            </tr></thead>
            <tbody>
              ${chainRows.map(r3 => `
              <tr class="${r3.atm ? "atm" : ""}">
                <td class="chain__side-c">${r3.c ? N.cnAmount(r3.c.volume) : "—"}</td><td>${r3.c ? N.cnAmount(r3.c.open_interest) : "—"}</td><td>${r3.c && isNum(r3.c.delta) ? r3.c.delta.toFixed(2) : "—"}</td><td>${r3.c && isNum(r3.c.implied_volatility) ? Math.round(r3.c.implied_volatility * 100) + "%" : "—"}</td>
                <td class="strike">${fmt(r3.strike)}${r3.atm ? `<br><span style="font-size:8.5px;color:var(--amber)">平值</span>` : ""}</td>
                <td>${r3.p && isNum(r3.p.implied_volatility) ? Math.round(r3.p.implied_volatility * 100) + "%" : "—"}</td><td>${r3.p && isNum(r3.p.delta) ? r3.p.delta.toFixed(2) : "—"}</td><td>${r3.p ? N.cnAmount(r3.p.open_interest) : "—"}</td><td class="chain__side-p">${r3.p ? N.cnAmount(r3.p.volume) : "—"}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
        <p class="mono" style="font-size:9px;color:var(--faint);margin:10px 0 0;line-height:1.7">到期 ${esc(chainExp)} · 剩余 ${isNum(chainData.dte) ? chainData.dte.toFixed(1) : "—"} 天 · 希腊值来自接口 · 仅供研究。</p>`
        : exps.ok ? inlineErr("暂无期权数据", "该标的没有可用的期权到期日") : inlineErr("期权链读取失败", exps.e && exps.e.message)}
      </section>

      <section class="sect">
        <div class="sect-head"><span class="sect-head__no">AI</span><h2>智能异动解读</h2><span class="sect-head__rule"></span><span class="sect-head__meta">${privateActionsAvailable() ? "按需生成 · 调用模型" : "公开页面 · 仅供查看"}</span></div>
        <div id="ai-box">
          ${alerts.length ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:11px">${alerts.slice(0, 3).map(a2 => `<span class="chip chip--amber"><i></i>${fmt(a2.strike)} ${a2.type === "call" ? "Call" : "Put"} · 量比 ${isNum(a2.vol_oi_ratio) ? a2.vol_oi_ratio.toFixed(1) : "—"}×</span>`).join("")}</div>` : ""}
          <p style="margin:0 0 12px;font-size:12px;color:var(--muted);line-height:1.7">${privateActionsAvailable() ? "模型解读通过后台持久任务生成；选择股票和打开抽屉都不会产生调用。" : "模型分析需要管理授权；行情、技术信号与期权数据仍可正常查看。"}</p>
          ${privateActionsAvailable() ? `<button class="btn btn--amber btn--sm" id="ai-run">生成 AI 解读</button>` : `<span class="mono" data-private-action-note>公开页面不会创建付费任务</span>`}
        </div>
      </section>`);
    measurePaths(drawer); animateBars(drawer); countUp(drawer);
    bindDrawerRanges(ticker, "drawer-g", dg);
    if (C) C.mountTickerPanel($("#stock-catalysts", drawer), ticker, { limit: 6, windowHours: 72, context: "stock" });
    const aiBtn = $("#ai-run", drawer);
    if (aiBtn && Jobs) {
      const scope = "signal-drawer:" + ticker;
      let startSignalJob = null;
      const biasCN = value => ({ bullish_continuation: "多头延续", healthy_rotation: "健康轮动", trend_pullback: "趋势回调", range_consolidation: "区间整理", tactical_top_risk: "战术顶部风险", dip_buy_setup: "回调观察", capitulation_bottom_setup: "恐慌底部观察", bearish_breakdown: "空头破位", insufficient_data: "数据不足" })[value] || value || "—";
      const renderOptionJob = job => {
        if (dg !== drawerGen) return;
        const box = $("#ai-box", drawer); if (!box) return;
        const hadFocus = box.contains(document.activeElement);
        const focusId = hadFocus && document.activeElement.id;
        const restoreFocus = () => {
          const next = focusId ? document.getElementById(focusId) : null;
          if (next && box.contains(next)) next.focus({ preventScroll: true });
          else if (hadFocus) { box.tabIndex = -1; box.focus({ preventScroll: true }); }
        };
        const status = Jobs.normalizeStatus(job.status);
        const active = Jobs.isActive(status);
        if (active) {
          const elapsed = Jobs.elapsed(job);
          box.innerHTML = `<span class="chip chip--amber"><i></i>${status === "in_progress" ? "分析中" : "排队中"}</span><p style="margin:10px 0 12px;font-size:12px;color:var(--muted)">${job.submitted_at ? "提交 " + N.fmtDateTime(job.submitted_at) : "后台任务已创建"}${elapsed != null && status === "in_progress" ? " · 已运行 " + elapsed + " 秒" : ""} · 不显示估算进度</p>${job.cancellable !== false ? `<button class="btn btn--sm" id="option-ai-cancel">取消任务</button>` : ""}`;
          const cancel = $("#option-ai-cancel", box); if (cancel) cancel.addEventListener("click", async () => { if (!window.confirm("确认取消这项异动解读任务？")) return; cancel.disabled = true; await Jobs.cancel(scope); });
          restoreFocus();
          return;
        }
        if (status === "completed") {
          const result = job.result || {};
          const evidence = Array.isArray(result.top_evidence) ? result.top_evidence : [];
          const confirmations = Array.isArray(result.confirmation_signals) ? result.confirmation_signals : [];
          const invalidations = Array.isArray(result.invalidation_signals) ? result.invalidation_signals : [];
          const eventRisks = Array.isArray(result.event_risks) ? result.event_risks : [];
          box.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:11px"><span class="chip chip--amber"><i></i>模型推断</span><span class="chip chip--mute">${esc(biasCN(result.final_bias))}</span><span class="chip chip--mute">周期 ${esc(result.horizon || "—")}</span><span class="chip chip--mute">趋势把握 ${isNum(result.trend_bias_confidence) ? Math.round(result.trend_bias_confidence) + "/100" : "—"} · 非胜率</span></div><p style="margin:0 0 8px;font-family:var(--font-serif);font-size:14.5px;line-height:1.9;color:var(--ink-soft)">${esc(result.summary || "模型未返回摘要")}</p>${result.dominant_regime ? `<p style="margin:0 0 10px;font-size:12px;line-height:1.8;color:var(--muted)">主导结构：${esc(result.dominant_regime)} · 数据质量 ${isNum(result.data_quality) ? Math.round(result.data_quality) + "/100" : "—"}</p>` : ""}${evidence.length ? `<details class="fold"><summary>查看模型证据与确认条件</summary><div class="fold__body"><p class="mono" style="font-size:9.5px;color:var(--faint)">顶部证据</p><ul style="font-size:12px;color:var(--muted)">${evidence.slice(0, 5).map(item => `<li>${esc(item)}</li>`).join("")}</ul>${confirmations.length ? `<p class="mono" style="font-size:9.5px;color:var(--faint)">确认信号</p><ul style="font-size:12px;color:var(--muted)">${confirmations.slice(0, 5).map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}${invalidations.length ? `<p class="mono" style="font-size:9.5px;color:var(--faint)">失效信号</p><ul style="font-size:12px;color:var(--muted)">${invalidations.slice(0, 5).map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}</div></details>` : ""}${eventRisks.length ? `<p style="margin:10px 0 0;font-size:12px;color:var(--down)">事件风险：${eventRisks.slice(0, 3).map(esc).join("；")}</p>` : ""}<p class="mono" style="font-size:9.5px;color:var(--faint);margin:12px 0 0">${esc(job.model || "gpt-5.6-terra")} · ${esc(job.reasoning || "max")} · ${job.cached ? "复用缓存" : "已保存结果"} · ${N.fmtDateTime(job.completed_at)} · 模型分数不是收益概率，不构成投资建议</p>`;
          restoreFocus();
          return;
        }
        if (status === "analysis_required") {
          box.innerHTML = `<div class="empty-note" style="padding:18px 8px"><p>尚未创建异动解读任务</p><small>服务端要求重新明确提交；这不是模型分析失败。</small><button class="btn btn--sm" id="option-ai-retry">重新提交任务</button></div>`;
          const retry = $("#option-ai-retry", box); if (retry) retry.addEventListener("click", () => startSignalJob(false));
          restoreFocus();
          return;
        }
        const retryBlocked = status === "failed" && job.error_code === "submission_outcome_unknown";
        box.innerHTML = `${inlineErr(status === "insufficient_context" ? "信息不足，未生成方向性解读" : status === "cancelled" ? "异动解读任务已取消" : "异动解读任务失败", job.error_code || "服务端没有发布分析结果")}${retryBlocked ? `<p class="mono" style="font-size:9.5px;color:var(--down);margin:10px 0 0">远端是否已接受请求尚不确定；为避免重复计费，此处禁止重提，请先核对任务记录。</p>` : `<button class="btn btn--sm" id="option-ai-retry" style="margin-top:10px">显式重试</button>`}`;
        const retry = $("#option-ai-retry", box); if (retry) retry.addEventListener("click", () => startSignalJob(true));
        restoreFocus();
      };
      startSignalJob = force => {
        if (!privateActionsAvailable()) return;
        Jobs.start({
          scope,
          create: signal => N.aiStock(ticker, force, { signal }),
          poll: (jobId, signal) => N.aiJob(jobId, { signal }),
          cancel: jobId => N.cancelAiJob(jobId),
          onUpdate: renderOptionJob,
          onComplete: renderOptionJob,
          onError: error => renderOptionJob({ status: error.status === 409 ? "analysis_required" : "failed", error_code: error.code || error.message }),
        });
      };
      aiBtn.addEventListener("click", () => startSignalJob(false));
    }
  }

  function bindDrawerRanges(ticker, gid, dg) {
    let adj = "raw";
    const redraw = async (range) => {
      const box = $("#dr-chart", drawer);
      if (!box) return;
      box.innerHTML = loadingView("读取行情…");
      const r = await settle(N.chart(ticker, range, adj));
      if (dg !== drawerGen) return;
      box.innerHTML = r.ok ? lineArea(r.v.bars.map(b => b.c), 540, 150, gid) : inlineErr("走势读取失败", r.e.message);
      measurePaths(drawer);
    };
    $$("[data-dr]", drawer).forEach(b => b.addEventListener("click", () => {
      $$("[data-dr]", drawer).forEach(x => x.classList.toggle("active", x === b));
      redraw(b.dataset.dr);
    }));
    $$("[data-adj]", drawer).forEach(b => b.addEventListener("click", () => {
      $$("[data-adj]", drawer).forEach(x => x.classList.toggle("active", x === b));
      adj = b.dataset.adj;
      const active = $("[data-dr].active", drawer);
      redraw(active ? active.dataset.dr : "1d");
    }));
  }
  function closeDrawer() {
    if (drawer.hidden) return;
    drawerGen++;
    drawer.classList.remove("open"); backdrop.classList.remove("open");
    document.body.style.overflow = "";
    setDrawerBackgroundInert(false);
    if (C) C.onDrawerClosed();
    if (Jobs) Jobs.stopPrefix("signal-drawer:");
    drawerTimer = setTimeout(() => { drawer.hidden = true; backdrop.hidden = true; }, REDUCED ? 0 : 650);
    if (lastFocusEl && lastFocusEl.isConnected) lastFocusEl.focus({ preventScroll: true });
  }
  backdrop.addEventListener("click", closeDrawer);
  window.OPTIX_DECK = {
    openStockDrawer: openDrawer,
    drawer: {
      open: drawerShell,
      close: closeDrawer,
      scrollTop: () => drawer.scrollTop,
      restoreScroll: value => { if (!drawer.hidden && Number.isFinite(value)) drawer.scrollTop = value; },
      isOpen: () => !drawer.hidden,
    },
  };
  document.addEventListener("keydown", e => {
    if (e.key === "Tab" && !drawer.hidden) {
      const focusable = $$("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])", drawer).filter(element => !element.hidden && element.getClientRects().length);
      if (!focusable.length) { e.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    if (e.key === "Escape") {
      if (!pback.hidden) closePalette();
      else if (!drawer.hidden) closeDrawer();
      else if ($(".deck-nav").classList.contains("open")) {
        $(".deck-nav").classList.remove("open");
        $("#menu-toggle").setAttribute("aria-expanded", "false");
        $("#menu-toggle").focus({ preventScroll: true });
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  });

  /* ---------- 搜索面板(真实搜索接口) ---------- */
  const pback = $("#palette-backdrop"), pinput = $("#palette-input"), presults = $("#palette-results");
  let searchTimer = null, searchGen = 0;
  function openPalette() {
    clearTimeout(paletteTimer);
    pback.hidden = false; requestAnimationFrame(() => pback.classList.add("open"));
    pinput.value = "";
    presults.innerHTML = `<a class="palette-item" href="#catalysts"><span class="tik__logo" aria-hidden="true">06</span><span><span class="tik__sym">催化剂中心</span><br><span class="tik__name">新闻、股票影响、经济日历与来源健康</span></span><span class="chip chip--amber">打开 →</span></a><p class="palette__hint">输入代码或公司名(中文/英文均可),回车打开第一个结果。</p>`;
    pinput.focus();
  }
  function closePalette() {
    if (pback.hidden) return;
    pback.classList.remove("open");
    paletteTimer = setTimeout(() => { pback.hidden = true; }, REDUCED ? 0 : 180);
  }
  async function runSearch(q) {
    const sg = ++searchGen;
    if (!q) { presults.innerHTML = `<a class="palette-item" href="#catalysts"><span class="tik__logo" aria-hidden="true">06</span><span><span class="tik__sym">催化剂中心</span><br><span class="tik__name">新闻与宏观事件</span></span><span class="chip chip--amber">打开 →</span></a><p class="palette__hint">输入代码或公司名(中文/英文均可)。</p>`; return; }
    presults.innerHTML = `<p class="palette__hint">正在搜索"${esc(q)}"…</p>`;
    const r = await settle(N.search(q));
    if (sg !== searchGen) return;
    if (!r.ok) { presults.innerHTML = `<p class="palette__hint">搜索失败:${esc(r.e.message)}</p>`; return; }
    const hits = (r.v || []).slice(0, 8);
    presults.innerHTML = hits.length
      ? hits.map((s, i) => `
        <button class="palette-item ${i === 0 ? "hot" : ""}" data-open-palette="${esc(s.ticker)}">
          ${logo(s.ticker)}<span><span class="tik__sym">${esc(s.ticker)}</span><br><span class="tik__name">${esc(s.name || s.name_en || "")}${s.name_en && s.name !== s.name_en ? " · " + esc(s.name_en) : ""}</span></span>
          <span class="chip chip--mute">${s.market === "indices" || isIndexSym(s.ticker) ? "指数" : "股票"}</span>
        </button>`).join("")
      : `<p class="palette__hint">没有匹配"${esc(q)}"的标的。</p>`;
    $$("[data-open-palette]", presults).forEach(b => b.addEventListener("click", () => { closePalette(); openDrawer(b.dataset.openPalette); }));
  }
  $("#search-toggle").addEventListener("click", openPalette);
  pinput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(pinput.value.trim()), 280);
  });
  pinput.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      const hot = presults.querySelector(".palette-item");
      if (hot) hot.click();
    }
  });
  pback.addEventListener("click", e => { if (e.target === pback) closePalette(); });

  /* ---------- 通用后处理 ---------- */
  function measurePaths(root) {
    $$(".draw-path", root).forEach(p => {
      const len = Math.ceil(p.getTotalLength ? p.getTotalLength() : 1200);
      p.style.setProperty("--path-len", len);
    });
  }
  function animateBars(root) {
    $$("[data-w]", root).forEach(el => {
      const raw = Math.max(0, Math.min(100, parseFloat(el.dataset.w) || 0));
      const w = raw > 0 ? Math.max(raw, 3.5) : 0;
      el.style.width = "0%";
      requestAnimationFrame(() => requestAnimationFrame(() => {
        el.style.transition = REDUCED ? "none" : "width 900ms var(--ease-swift) 120ms";
        el.style.width = w + "%";
      }));
    });
  }
  function animateCandles() {
    if (REDUCED) return;
    $$(".candle", view).forEach(c => {
      c.style.opacity = "0";
      c.style.transition = "opacity 380ms ease " + (parseInt(c.style.getPropertyValue("--i") || 0) * 9) + "ms";
      requestAnimationFrame(() => requestAnimationFrame(() => { c.style.opacity = "1"; }));
    });
  }
  function countUp(root) {
    $$("[data-count]", root).forEach(el => {
      const target = parseFloat(el.dataset.count);
      if (!isFinite(target)) { el.textContent = "—"; return; }
      if (REDUCED) { el.textContent = fmt(target); return; }
      const t0 = performance.now(), dur = 850;
      (function tick(t) {
        const k = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - k, 4);
        el.textContent = fmt(target * e);
        if (k < 1) requestAnimationFrame(tick);
      })(t0);
    });
  }
  let io;
  function reveal() {
    if (io) io.disconnect();
    const els = $$("[data-reveal]", view);
    if (REDUCED) { els.forEach(el => el.classList.add("in")); return; }
    io = new IntersectionObserver(entries => {
      entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { threshold: 0.08 });
    els.forEach(el => io.observe(el));
  }
  function a11yRow(el) {
    if (el.tagName === "BUTTON" || el.tagName === "A" || el.tagName === "SUMMARY") return;
    if (!el.hasAttribute("tabindex")) { el.setAttribute("tabindex", "0"); el.setAttribute("role", "button"); }
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } });
  }
  function bindOpen() {
    $$("[data-open]", view).forEach(el => { a11yRow(el); el.addEventListener("click", e => { e.stopPropagation(); openDrawer(el.dataset.open); }); });
    $$("[data-evt]", view).forEach(el => { a11yRow(el); el.addEventListener("click", e => { e.stopPropagation(); openEvidenceDrawer(el.dataset.evt); }); });
    $$("[data-cevt]", view).forEach(el => { a11yRow(el); el.addEventListener("click", e => { e.stopPropagation(); openCandEvidence(parseInt(el.dataset.cevt, 10)); }); });
  }
  function postRender() {
    reveal(); bindOpen(); measurePaths(view); animateBars(view); animateCandles(); countUp(view);
  }

  /* ---------- 路由 ---------- */
  function catalystRouteParams() {
    const hash = (location.hash || "#catalysts").slice(1);
    const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
    return new URLSearchParams(query);
  }
  function renderCatalysts() {
    if (!C) { view.innerHTML = errorView("催化剂页面未能载入", "生产模块 deck-catalysts.js 不可用"); return; }
    C.renderPage({ view, params: catalystRouteParams(), postRender, openStock: openDrawer });
  }
  const routes = { watchlist: renderWatchlist, screener: () => renderScreener(), breakouts: () => renderBreakouts(), sectors: renderSectors, earnings: () => renderEarnings(), catalysts: renderCatalysts };
  const routeTitles = { watchlist: "自选观察", screener: "选股", breakouts: "突破雷达", sectors: "板块", earnings: "财报日历", catalysts: "催化剂中心" };
  let activeRoute = null;
  function route() {
    const key = (location.hash || "#watchlist").slice(1).split("?")[0];
    if (C) C.abortPageEnhancements();
    if (key.startsWith("detail/")) { /* 旧版深链:#detail/TICKER → 自选 + 研究抽屉 */
      const tkr = decodeURIComponent(key.slice(7)).trim();
      const renderBackground = activeRoute !== "watchlist" || !St.watch;
      activeRoute = "watchlist";
      if (C) C.leaveRoute();
      if (Jobs) Jobs.stopPrefix("earnings:");
      closePalette();
      $$(".deck-nav a, .dock a").forEach(a => {
        const current = a.dataset.route === "watchlist";
        a.classList.toggle("active", current);
        if (current) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
      document.title = `${tkr ? tkr.toUpperCase() + " · 标的研究" : routeTitles.watchlist} · Optix Pro`;
      $(".deck-nav").classList.remove("open");
      $("#menu-toggle").setAttribute("aria-expanded", "false");
      if (renderBackground) renderWatchlist();
      if (tkr) openDrawer(tkr.toUpperCase());
      return;
    }
    if (key && !routes[key]) { view.focus({ preventScroll: true }); return; }
    if (activeRoute === "catalysts" && key !== "catalysts" && C) C.leaveRoute();
    if (activeRoute === "earnings" && key !== "earnings" && Jobs) Jobs.stopPrefix("earnings:");
    if (!drawer.hidden) closeDrawer();
    closePalette();
    const fn = routes[key] || renderWatchlist;
    const currentRoute = routes[key] ? key : "watchlist";
    activeRoute = currentRoute;
    if (currentRoute === "breakouts") {
      const ticker = catalystRouteParams().get("ticker") || "";
      brk.ticker = ticker.trim().toUpperCase().replace(/[^A-Z0-9.^-]/g, "").slice(0, 15);
    }
    $$(".deck-nav a, .dock a").forEach(a => {
      const current = a.dataset.route === currentRoute;
      a.classList.toggle("active", current);
      if (current) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = `${routeTitles[currentRoute]} · Optix Pro`;
    const menu = $("#menu-toggle");
    menu.classList.toggle("has-current-route", currentRoute === "catalysts");
    menu.setAttribute("aria-label", currentRoute === "catalysts" ? "打开导航，当前页面：催化剂" : "打开导航");
    $(".deck-nav").classList.remove("open");
    $("#menu-toggle").setAttribute("aria-expanded", "false");
    window.scrollTo({ top: 0, behavior: "instant" });
    fn();
    view.focus({ preventScroll: true });
  }
  window.addEventListener("hashchange", route);

  /* ---------- 指数纸带 ---------- */
  async function tape() {
    const wrap = $("#index-tape");
    const [idx, ms] = await Promise.all([settle(N.indices()), settle(N.marketStatus())]);
    if (idx.ok) St.indices = idx.v;
    if (ms.ok) St.market = ms.v;
    if (!idx.ok) {
      wrap.innerHTML = `<span class="tape-item" style="cursor:default"><b>指数行情读取失败</b><span class="num d">${esc(idx.e.message)}</span></span>`;
      $("#tape-updated").textContent = "行情不可用";
      return;
    }
    const html = St.indices.list.map(x => `
      <button class="tape-item" data-index="${esc(x.ticker)}" title="查看 ${esc(x.sym)} 指数研究"><b>${esc(x.sym)}</b><span class="num">${fmt(x.price)}</span><span class="num ${(x.chg || 0) >= 0 ? "u" : "d"}">${pct(x.chg)}</span></button>`).join("");
    const clone = html.split('<button class="tape-item"').join('<button tabindex="-1" class="tape-item"');
    wrap.innerHTML = html + `<span aria-hidden="true" style="display:contents">${clone}</span>`;
    const phase = St.market ? ({ open: "盘中", premarket: "盘前", postmarket: "盘后", closed: "休市" })[St.market.market] || St.market.market : "";
    $("#tape-updated").textContent = `延迟行情 · ${phase}${St.market && St.market.phase === "weekend" ? "(周末)" : St.market && St.market.holiday ? "(假日)" : ""} · ${N.fmtTime(St.indices.asOf)} 快照`;
    if (!wrap.dataset.bound) {
      wrap.dataset.bound = "1";
      wrap.addEventListener("click", e => {
        const item = e.target.closest("[data-index]");
        if (item) openDrawer(item.dataset.index);
      });
    }
  }

  /* ---------- 时钟 ---------- */
  function clock() {
    const now = new Date();
    const bj = now.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
    const ny = now.toLocaleTimeString("en-US", { hour12: false, timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" });
    let phase;
    if (St.market) {
      phase = ({ open: "盘中", premarket: "盘前", postmarket: "盘后", closed: "休市" })[St.market.market] || "—";
      if (St.market.holiday) phase = "休市(假日)";
    } else {
      const nyH = parseInt(now.toLocaleString("en-US", { hour: "2-digit", hour12: false, timeZone: "America/New_York" }));
      const nyM = parseInt(now.toLocaleString("en-US", { minute: "2-digit", timeZone: "America/New_York" }));
      const mins = nyH * 60 + nyM;
      const nyWd = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" })).getDay();
      const weekend = nyWd === 0 || nyWd === 6;
      phase = weekend ? "休市" : mins >= 240 && mins < 570 ? "盘前" : mins >= 570 && mins < 960 ? "盘中" : mins >= 960 && mins < 1200 ? "盘后" : "休市";
    }
    $("#clock-bj").textContent = bj;
    $("#market-phase").textContent = `${phase} · 纽约 ${ny}`;
    const dot = $(".deck-clock__dot");
    dot.style.background = phase === "盘中" ? "var(--up)" : phase.startsWith("休市") ? "var(--faint)" : "var(--amber)";
  }

  /* ---------- 手机菜单 ---------- */
  $("#menu-toggle").addEventListener("click", () => {
    const nav = $(".deck-nav"), open = nav.classList.toggle("open");
    $("#menu-toggle").setAttribute("aria-expanded", open);
  });

  /* ---------- 主题 ---------- */
  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("optix.theme", t); } catch (e) { /* 忽略 */ }
    syncThemeColor();
  }
  function syncThemeColor() {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = document.documentElement.dataset.theme === "light" ? "#f5f7fa" : "#0b0e14";
  }
  $("#theme-toggle").addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  });

  /* ---------- 启动 ---------- */
  syncThemeColor(); tape(); clock();
  setInterval(clock, 1000);
  setInterval(tape, 60e3);

  /* 自选页静默自动拉取:每 75 秒;标签页隐藏或不在自选路由时跳过 */
  const onWatchRoute = () => (location.hash.slice(1) || "watchlist").split("/")[0] === "watchlist";
  setInterval(() => {
    if (document.visibilityState !== "visible" || !onWatchRoute()) return;
    renderWatchlist(true);
  }, 75e3);
  document.addEventListener("visibilitychange", () => {
    // 切回标签页时,若数据已旧于一分钟则立即补一轮,避免看到过期行情
    if (document.visibilityState === "visible" && onWatchRoute() && St.watch && Date.now() - watchFetchedAt > 60e3) renderWatchlist(true);
  });
  route();
})();
