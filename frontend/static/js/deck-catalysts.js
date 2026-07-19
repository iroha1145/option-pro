/* Optix Pro Night Desk — Catalyst Desk
   浏览器只读取 Option Pro 同源缓存；任何模型任务都必须由用户明确点击。 */
(function () {
  "use strict";

  const N = window.OPTIX_NET;
  const Jobs = window.OPTIX_AI_JOBS;
  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const list = (payload, keys) => {
    if (Array.isArray(payload)) return payload;
    for (const key of keys) if (payload && Array.isArray(payload[key])) return payload[key];
    return [];
  };
  const object = (payload, keys) => {
    for (const key of keys) if (payload && payload[key] && typeof payload[key] === "object") return payload[key];
    return null;
  };
  const safeUrl = value => {
    if (!value) return "";
    try {
      const url = new URL(String(value), window.location.origin);
      return (url.protocol === "http:" || url.protocol === "https:") ? url.href : "";
    } catch (error) { return ""; }
  };
  const pct = value => {
    if (!finite(value)) return "—";
    return Math.round(value) + "%";
  };
  const score = value => finite(value) ? (Math.round(value * 10) / 10).toFixed(Math.abs(value % 1) > 0 ? 1 : 0) : "—";
  const signedScore = value => finite(value) ? (value > 0 ? "+" : "") + score(value) : "—";
  const fmtCount = value => finite(value) ? Math.round(value).toLocaleString("zh-CN") : "—";
  const upperTicker = value => String(value || "").trim().toUpperCase().replace(/[^A-Z0-9.^-]/g, "").slice(0, 16);
  const NAMED_ENTITIES = Object.freeze({ nbsp: " ", amp: "&", quot: '"', apos: "'", lt: "<", gt: ">" });
  const numericEntity = (match, raw) => {
    const codePoint = raw[0].toLowerCase() === "x"
      ? Number.parseInt(raw.slice(1), 16)
      : Number.parseInt(raw, 10);
    if (!Number.isInteger(codePoint) || codePoint <= 0 || codePoint > 0x10ffff || (codePoint >= 0xd800 && codePoint <= 0xdfff)) return match;
    if ((codePoint < 0x20 && ![0x09, 0x0a, 0x0d].includes(codePoint)) || (codePoint >= 0x7f && codePoint <= 0x9f)) return " ";
    return String.fromCodePoint(codePoint);
  };
  const plainText = value => String(value == null ? "" : value)
    .replace(/&(nbsp|amp|quot|apos|lt|gt);/gi, (_match, name) => NAMED_ENTITIES[name.toLowerCase()])
    .replace(/&#(x[0-9a-f]+|[0-9]+);/gi, numericEntity)
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<\s*\/?\s*(?:p|div|br|li|ul|ol|h[1-6]|section|article|blockquote|table|tr|td|th)\b[^>]*>/gi, " ")
    .replace(/<\s*\/?\s*[a-z][^>]*>/gi, " ")
    .replace(/\s+/g, " ")
    .trim();

  const CLASS_CN = {
    bullish: "正向", positive: "正向", bearish: "负向", negative: "负向",
    neutral: "中性", mixed: "多空交织", macro: "宏观", other: "其他",
  };
  const HORIZON_CN = { intraday: "日内", days: "数日", weeks: "数周", uncertain: "不确定" };
  const MECHANISM_CN = { direct_company: "公司直接影响", supplier_customer: "供应链传导", sector_readthrough: "行业映射", macro_rate: "宏观利率", commodity_input: "大宗商品成本", regulatory: "监管", competitive: "竞争格局", other: "其他" };
  const STATUS_CN = {
    ok: "正常", active: "正常", degraded: "部分降级", fallback: "兜底源", stale: "过期快照", unavailable: "暂不可用",
    disabled: "未启用", empty: "没有匹配", not_requested: "未请求", pending: "待处理", queued: "排队中",
    in_progress: "分析中", cancel_requested: "正在取消", completed: "已完成", failed: "失败", cancelled: "已取消",
    insufficient_context: "信息不足", budget_blocked: "预算受限", not_configured: "未配置",
    prepared: "准备完成", leased: "分析占用中", consumed: "已消费", resync_required: "需要重新同步",
    incomplete_output: "输出不完整", submission_outcome_unknown: "提交结果待确认", worker_interrupted: "任务中断",
  };
  const ANALYSIS_ERROR_CN = Object.freeze({
    ai_job_queue_full: "分析队列已满，请稍后重试",
    daily_job_limit_reached: "今日任务次数已用完",
    daily_budget_usd_reached: "今日分析预算已用完",
    daily_token_limit_reached: "今日 1000 万 Token 额度已用完",
    analysis_cooldown_active: "分析正在冷却中",
    cache_unavailable: "本地缓存暂不可用",
  });
  const LOW_CONTEXT_RULE_MODEL = "low-context-neutral-v2";
  const className = value => String(value || "").toLowerCase();
  const analysisErrorMessage = value => ANALYSIS_ERROR_CN[className(value)] || "分析任务暂未完成，请稍后重试";
  const analysisErrorDetail = payload => {
    const message = analysisErrorMessage(payload && (payload.error_code || payload.code));
    const retryAfter = Number(payload && (payload.retry_after_seconds || payload.retry_after));
    return Number.isFinite(retryAfter) && retryAfter > 0
      ? `${message} · ${Math.ceil(retryAfter)} 秒后可重试`
      : message;
  };
  const classLabel = value => CLASS_CN[className(value)] || String(value || "—");
  const horizonLabel = value => HORIZON_CN[className(value)] || String(value || "—");
  const mechanismLabel = value => MECHANISM_CN[className(value)] || String(value || "—");
  const statusLabel = value => STATUS_CN[className(value)] || String(value || "未知");
  const chipTone = value => {
    const key = className(value);
    if (["bullish", "positive", "ok", "active", "completed"].includes(key)) return "chip--up";
    if (["bearish", "negative", "failed", "unavailable"].includes(key)) return "chip--down";
    if (["degraded", "stale", "pending", "queued", "in_progress", "budget_blocked"].includes(key)) return "chip--amber";
    return "chip--mute";
  };

  function analysisOf(item) {
    return item && item.analysis && typeof item.analysis === "object" ? item.analysis : null;
  }
  function analysisStatus(item) {
    return className(item && (item.analysis_status || item.status) || (analysisOf(item) ? "completed" : "pending"));
  }
  function isRuleOnlyAnalysis(item) {
    const analysis = analysisOf(item);
    return className(analysis && analysis.model) === LOW_CONTEXT_RULE_MODEL;
  }
  function analysisOriginLabel(item) {
    const analysis = analysisOf(item);
    if (isRuleOnlyAnalysis(item)) return "规则结果 · 未调用模型";
    return `模型推断${analysis && analysis.insufficient_context ? " · 信息不足" : ""}`;
  }
  function analysisRetryForce(item, job) {
    const status = Jobs.normalizeStatus(job && job.status || analysisStatus(item));
    return !!analysisOf(item) || ["completed", "insufficient_context", "failed", "cancelled"].includes(status);
  }
  function analysisAvailabilityOf(item) {
    return item && item.analysis_availability
      || page.feed && page.feed.analysis_availability
      || (metaStatus(page.status) || {}).analysis_availability
      || {};
  }
  function analysisTriggerEnabledOf(item) {
    if (item && item.analysis_trigger_enabled != null) return !!item.analysis_trigger_enabled;
    const status = metaStatus(page.status) || {};
    if (status.analysis_trigger_enabled != null) return !!status.analysis_trigger_enabled;
    const availability = analysisAvailabilityOf(item);
    return typeof availability.enabled === "boolean" ? !!availability.enabled : false;
  }
  function analysisActionDecision(triggerEnabled, availability, ownerAccess = true) {
    if (!ownerAccess) return {
      modeUnavailable: true,
      showAction: false,
      reason: "owner_login_required",
      canTrigger: false,
      title: "登录后可使用模型分析",
      detail: "公开浏览只显示已有结果，不会创建、重试或取消模型任务。",
    };
    const state = availability && typeof availability === "object" ? availability : {};
    const hasRuntimeState = typeof state.enabled === "boolean" || !!state.reason;
    const enabled = hasRuntimeState ? !!state.enabled : !!triggerEnabled;
    const statedReason = className(state.reason || "");
    const reason = className(
      !triggerEnabled && (!statedReason || statedReason === "available")
        ? "read_only_mode"
        : (statedReason || "available"),
    );
    const modeUnavailable = !triggerEnabled || [
      "read_only_mode", "manual_analysis_disabled", "settings_unavailable",
      "not_configured", "catalyst_disabled",
    ].includes(reason);
    const showAction = ![
      "read_only_mode", "manual_analysis_disabled", "settings_unavailable",
      "catalyst_disabled",
    ].includes(reason);
    const titles = {
      settings_unavailable: "运行设置暂不可用",
      read_only_mode: "当前为只读模式",
      manual_analysis_disabled: "手动分析已关闭",
      catalyst_disabled: "催化剂功能已关闭",
      not_configured: "模型服务尚未配置",
      worker_unavailable: "服务暂不可用",
      budget_exhausted: "今日预算已用完",
      analysis_in_progress: "分析任务正在运行",
      cooldown_active: "分析冷却中",
    };
    const details = {
      settings_unavailable: "为避免意外产生费用，运行设置恢复前不会创建模型任务。",
      read_only_mode: "当前运行模式关闭了手动分析，已有中文结果仍可查看。",
      manual_analysis_disabled: "运行设置关闭了手动分析，已有中文结果仍可查看。",
      catalyst_disabled: "催化剂功能恢复后才能创建新的模型任务。",
      not_configured: "模型服务配置完成后才能生成新的分析。",
      worker_unavailable: "后台工作进程恢复后才能接收新的分析任务。",
      budget_exhausted: `${budgetPolicyText()}；今日额度已达到上限。`,
      analysis_in_progress: "同一时间只运行一项模型分析，请等待当前任务结束。",
      cooldown_active: "请等待冷却结束，按钮会在状态刷新后恢复。",
    };
    return {
      modeUnavailable,
      showAction,
      reason,
      canTrigger: !!triggerEnabled && enabled,
      title: titles[reason] || "尚未生成模型分析",
      detail: details[reason] || "来源信息仍可查看；未分析状态不会补成中性方向。",
    };
  }

  function budgetPolicyText() {
    const status = page.focusStatus || metaStatus(page.status) || {};
    const availability = status.analysis_availability && typeof status.analysis_availability === "object"
      ? status.analysis_availability
      : {};
    const tokens = Number(availability.daily_token_limit);
    const tokenText = Number.isFinite(tokens)
      ? `每日 ${Math.max(0, Math.floor(tokens)).toLocaleString("zh-CN")} Token`
      : "每日 Token 额度受限";
    return `任务次数不限制，${tokenText}`;
  }
  function classificationOf(item) {
    const analysis = analysisOf(item);
    return item && (item.classification || (analysis && analysis.classification)) || null;
  }
  function confidenceOf(item) {
    const analysis = analysisOf(item);
    const value = item && (item.confidence ?? (analysis && analysis.confidence));
    return finite(value) ? value : null;
  }
  function marketRelevanceOf(item) {
    const analysis = analysisOf(item);
    const value = item && (item.market_relevance ?? (analysis && analysis.market_relevance));
    return finite(value) ? value : null;
  }
  function sentimentOf(item) {
    const analysis = analysisOf(item);
    const value = item && (item.overall_sentiment ?? (analysis && analysis.overall_sentiment));
    return finite(value) ? value : null;
  }
  function impactsOf(item) {
    if (item && Array.isArray(item.ticker_impacts)) return item.ticker_impacts;
    return list(item || {}, ["trusted_stock_impacts"]);
  }
  function rawImpactsOf(item) {
    const analysis = analysisOf(item);
    return list(analysis || {}, ["affected_stocks"]);
  }
  function validationMapOf(item) {
    const analysis = analysisOf(item);
    const map = new Map();
    const duplicates = new Set();
    for (const row of list(analysis || {}, ["stock_validations"])) {
      const ticker = upperTicker(row && row.ticker);
      if (!ticker) continue;
      if (map.has(ticker)) duplicates.add(ticker);
      else map.set(ticker, row);
    }
    // Old cached payloads may predate backend duplicate validation.  A
    // contradictory pair must fail closed instead of inheriting whichever
    // record happened to appear first.
    for (const ticker of duplicates) map.set(ticker, { validation_status: "unverified" });
    return map;
  }
  function validationLabel(status) {
    return ({ canonical: "正式代码", valid_external: "外部代码有效", ambiguous: "代码有歧义", invalid: "无效代码", unverified: "尚未验证" })[className(status)] || "尚未验证";
  }
  function impactValue(item) {
    if (!item || typeof item !== "object") return null;
    for (const key of ["impact_score", "net_impact", "impact", "score"]) if (finite(item[key])) return item[key];
    return null;
  }
  function impactDirection(item) {
    const value = impactValue(item);
    if (!finite(value)) return null;
    return value > 0 ? "bullish" : value < 0 ? "bearish" : "neutral";
  }
  function itemId(item) { return String(item && (item.news_id || item.id) || ""); }
  function itemTitle(item) {
    const analysis = analysisOf(item);
    return plainText(
      (analysis && analysis.title_zh)
      || (item && item.title_zh)
      || "中文标题等待生成",
    );
  }
  function itemSummary(item) {
    const analysis = analysisOf(item);
    return plainText(
      (item && item.summary_zh)
      || (analysis && (analysis.summary_zh || analysis.headline_summary))
      || (item && item.headline_summary)
      || "中文摘要等待生成",
    );
  }
  function feedItems(payload) { return list(payload, ["items", "news", "results", "feed"]); }
  function newsItemFromPayload(payload) {
    const item = payload && (payload.news || payload.item) || payload || {};
    return Object.assign({}, item, {
      analysis_job: payload && payload.analysis_job || item.analysis_job || null,
      analysis_trigger_enabled: payload && payload.analysis_trigger_enabled != null ? payload.analysis_trigger_enabled : item.analysis_trigger_enabled,
      analysis_availability: payload && payload.analysis_availability || item.analysis_availability || null,
    });
  }
  function visibleFeedItems(payload) { return feedItems(payload); }
  function calendarItems(payload) { return list(payload, ["events", "items", "calendar"]); }
  function sourceItems(status, feed) {
    return list(status, ["sources", "source_health", "source_statuses"]).concat(list(feed, ["source_health"]));
  }
  function stateOf(payload, fallback) {
    const status = payload && (payload.status || payload.data_status || payload.state);
    if (typeof status === "string") return className(status);
    return fallback || (feedItems(payload).length ? "active" : "empty");
  }
  function metaStatus(payload) {
    return payload && payload.status && typeof payload.status === "object" ? payload.status : payload;
  }

  function timeHtml(value, label) {
    if (!value) return "—";
    return `<time datetime="${esc(value)}" title="${esc(value)}">${esc(label ? label(value) : N.fmtDateTime(value))}</time>`;
  }
  function externalLink(url, label) {
    const href = safeUrl(url);
    return href ? `<a class="cat-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label || "查看原文")} ↗</a>` : "";
  }
  function impactReasonHtml(value) {
    const reason = String(value || "").trim();
    if (!reason) return "";
    if (reason.length <= 72) return `<p>${esc(reason)}</p>`;
    return `<details class="cat-impact-reason"><summary>查看完整影响理由</summary><p>${esc(reason)}</p></details>`;
  }
  function stateBlock(status, title, detail) {
    return `<div class="cat-state cat-state--${esc(className(status) || "empty")}" role="${status === "failed" || status === "unavailable" ? "alert" : "status"}>
      <span class="cat-state__mark" aria-hidden="true"></span>
      <div><b>${esc(title)}</b><p>${esc(detail || "")}</p></div>
    </div>`;
  }

  const DEFAULT_FILTERS = Object.freeze({
    ticker: "", window_hours: "24", classification: "", analysis_status: "", min_confidence: "",
    min_abs_impact: "", source: "", horizon: "", mechanism: "", multi_source_only: false,
  });
  const page = {
    active: false,
    view: null,
    params: {},
    draft: Object.assign({}, DEFAULT_FILTERS),
    applied: Object.assign({}, DEFAULT_FILTERS),
    tab: "feed",
    controller: null,
    timer: null,
    refreshStateTimer: null,
    generation: 0,
    statusRequest: 0,
    feedRequest: 0,
    calendarRequest: 0,
    focusRequest: 0,
    runtimeSettingsRequest: 0,
    workerStatusRequest: 0,
    status: null,
    feed: null,
    calendar: null,
    focusStatus: null,
    hotspots: null,
    marketCycle: null,
    successfulMarketCycle: null,
    runtimeSettings: null,
    runtimeHistory: [],
    runtimeDirty: false,
    workerStatus: null,
    workerStateTimer: null,
    ownerAccess: false,
    openStock: null,
    postRender: null,
  };
  let drawerController = null;
  const inlineControllers = new WeakMap();
  const drawerPanelControllers = new Set();
  const pageEnhancementControllers = new Set();

  function queryParams() {
    const params = {};
    const serverKeys = new Set(["ticker", "window_hours", "classification", "analysis_status", "min_confidence", "min_abs_impact", "source", "horizon", "mechanism", "multi_source_only"]);
    for (const [key, value] of Object.entries(page.applied)) {
      if (!serverKeys.has(key)) continue;
      if (value === true) params[key] = "true";
      else if (value !== false && value !== "" && value != null) params[key] = value;
    }
    return params;
  }

  function routeFilters(params) {
    const next = Object.assign({}, DEFAULT_FILTERS);
    for (const key of Object.keys(DEFAULT_FILTERS)) {
      if (params.has(key)) next[key] = key === "multi_source_only" ? params.get(key) === "true" : params.get(key);
    }
    return next;
  }

  function replaceRouteQuery(extra) {
    const out = new URLSearchParams();
    if (page.tab !== "feed") out.set("tab", page.tab);
    for (const [key, value] of Object.entries(page.applied)) {
      if (value === true) out.set(key, "true");
      else if (value !== false && value !== "" && value != null && !(key === "window_hours" && value === "24")) out.set(key, value);
    }
    Object.entries(extra || {}).forEach(([key, value]) => {
      if (value == null || value === "") out.delete(key); else out.set(key, value);
    });
    history.replaceState(null, "", "#catalysts" + (out.toString() ? "?" + out.toString() : ""));
  }

  function pageShell() {
    page.view.innerHTML = `
      <div class="cat-desk">
        <div class="view-head" data-reveal style="--reveal-i:0">
          <div>
            <p class="view-head__kicker">06 · Catalyst Desk</p>
            <h1>催化剂中心<small>把新闻、股票影响与宏观日历放回时间轴；催化剂仅用于展示，不改变正式评分。</small></h1>
          </div>
          <div class="view-head__aside" id="cat-head-state">${stateBlock("loading", "正在读取本地快照", "不会触发模型分析")}</div>
        </div>

        <section class="cat-command panel panel--pad" data-reveal style="--reveal-i:1" aria-label="催化剂状态">
          <div class="cat-command__identity">
            <span class="mono">MACROLENS · LOCAL CACHE</span>
            <strong id="cat-connection">读取中</strong>
            <small id="cat-times">数据截止 — · 最近同步 —</small>
          </div>
          <div class="cat-command__facts" id="cat-facts"></div>
          <div class="cat-command__model">
            <span class="chip chip--amber"><i></i><span id="cat-model">GPT-5.6 Terra · max</span></span>
            <p id="cat-model-note">模型推断，不代表收益概率；影响分不是预期收益，置信度不是胜率。</p>
          </div>
        </section>

        <details class="panel panel--pad cat-runtime-settings" id="cat-runtime-settings" data-owner-only data-reveal style="--reveal-i:2" ${page.ownerAccess ? "" : "hidden"}>
          <summary><strong>运行设置</strong><span class="mono" id="cat-runtime-version">正在读取</span></summary>
          <p>这里只调整每日 Token 上限、分析冷却和运行时段，不会读取或显示任何密钥。</p>
          <form id="cat-runtime-form" autocomplete="off">
            <div class="cat-filter-grid">
              <label><span>每日 Token 上限</span><input name="daily_token_limit" type="number" min="102400" max="100000000" step="1" disabled /></label>
              <label><span>分析冷却（秒）</span><input name="manual_analysis_cooldown_seconds" type="number" min="0" max="86400" step="1" disabled /></label>
              <label><span>每小时分析时刻（美东）</span><input name="scheduled_times_et" placeholder="00:00, 01:00, …, 23:00" disabled /></label>
              <label class="cat-check"><input name="manual_analysis_enabled" type="checkbox" disabled /><span>允许手动分析</span></label>
              <label class="cat-check"><input name="scheduled_analysis_enabled" type="checkbox" disabled /><span>启用每小时分析</span></label>
            </div>
            <div class="cat-filter-actions">
              <span class="mono" id="cat-runtime-state">运行设置尚未载入</span>
              <button class="btn btn--ghost btn--sm" type="button" id="cat-runtime-rollback" disabled>回滚上一版</button>
              <button class="btn btn--amber btn--sm" type="submit" id="cat-runtime-save" disabled>保存设置</button>
            </div>
          </form>
        </details>

        <section class="panel panel--pad cat-owner-operations" id="cat-owner-operations" data-owner-only data-reveal style="--reveal-i:3" aria-labelledby="cat-operations-title" ${page.ownerAccess ? "" : "hidden"}>
          <header class="cat-focus-cycle__head">
            <div>
              <span class="mono">OWNER OPERATIONS · UNIFIED WORKER</span>
              <h2 id="cat-operations-title">日常维护</h2>
              <p>操作进入同一个后台队列；重复点击会复用现有任务，普通刷新不占用模型预算。</p>
            </div>
            <span class="chip chip--mute" id="cat-worker-health">工作进程状态读取中</span>
          </header>
          <div class="cat-operation-facts" id="cat-operation-facts" aria-live="polite"></div>
          <div class="cat-operation-actions">
            <button class="btn btn--sm" type="button" data-worker-action="focus_refresh" disabled>刷新焦点股票池</button>
            <button class="btn btn--sm" type="button" data-worker-action="strength_refresh" disabled>刷新强势雷达</button>
            <button class="btn btn--sm" type="button" data-worker-action="breakout_refresh" disabled>刷新突破雷达</button>
            <button class="btn btn--ghost btn--sm" type="button" data-worker-action="retention" disabled>执行数据清理</button>
          </div>
          <div id="cat-operation-state">${stateBlock("loading", "正在读取后台状态", "不会触发刷新或模型任务。")}</div>
        </section>

        <section class="cat-focus-cycle panel panel--pad" id="cat-focus-cycle" data-reveal style="--reveal-i:4" aria-labelledby="cat-focus-title">
          <header class="cat-focus-cycle__head">
            <div>
              <span class="mono">MARKET FOCUS · DISPLAY ONLY</span>
              <h2 id="cat-focus-title">市场与焦点股综合分析</h2>
              <p>事件组先通过确定性热点门控，再按需进入一次有界综合分析；结果不改变正式评分。</p>
            </div>
            <span id="cat-focus-action"></span>
          </header>
          <div class="cat-focus-cycle__meta" id="cat-focus-meta" aria-live="polite"></div>
          <div class="cat-focus-cycle__body" id="cat-focus-body">${stateBlock("loading", "正在读取热点准备区", "普通页面刷新不会创建模型任务。")}</div>
        </section>

        <section class="cat-summary" id="cat-summary" data-reveal style="--reveal-i:4" aria-label="催化剂摘要"></section>

        <section class="cat-workspace sect">
          <div class="cat-tabs" role="tablist" aria-label="催化剂数据视图">
            ${[["feed", "新闻流"], ["stocks", "股票影响"], ["calendar", "经济日历"], ["sources", "数据源"]].map(([key, label]) => `
              <button type="button" role="tab" id="cat-tab-${key}" aria-controls="cat-panel" aria-selected="${page.tab === key}" tabindex="${page.tab === key ? "0" : "-1"}" data-cat-tab="${key}">${label}</button>`).join("")}
          </div>

          <form class="cat-filters panel panel--pad" id="cat-filters" autocomplete="off">
            <div class="cat-filter-grid">
              <label><span>股票代码</span><input name="ticker" inputmode="latin" maxlength="16" placeholder="如 NVDA" /></label>
              <label><span>时间窗口</span><select name="window_hours"><option value="6">最近 6 小时</option><option value="24">最近 24 小时</option><option value="72">最近 72 小时</option><option value="168">最近 7 天</option></select></label>
              <label><span>分类</span><select name="classification"><option value="">全部</option><option value="bullish">正向</option><option value="bearish">负向</option><option value="neutral">中性</option></select></label>
              <label><span>分析状态</span><select name="analysis_status"><option value="">全部</option><option value="pending">待分析</option><option value="queued">排队中</option><option value="in_progress">分析中</option><option value="completed">已完成</option><option value="insufficient_context">信息不足</option><option value="failed">失败</option></select></label>
              <label><span>最小模型置信度</span><input name="min_confidence" type="number" min="0" max="100" step="5" placeholder="—" /><small>置信度不是胜率</small></label>
              <label><span>最小绝对影响分</span><input name="min_abs_impact" type="number" min="0" step="1" placeholder="—" /><small>影响分不是预期收益</small></label>
              <label><span>来源</span><input name="source" maxlength="80" placeholder="全部来源" /></label>
              <label><span>影响期限</span><select name="horizon"><option value="">全部</option><option value="intraday">日内</option><option value="days">数日</option><option value="weeks">数周</option><option value="uncertain">不确定</option></select></label>
              <label><span>影响机制</span><select name="mechanism"><option value="">全部</option><option value="direct_company">公司直接影响</option><option value="supplier_customer">供应链传导</option><option value="sector_readthrough">行业映射</option><option value="macro_rate">宏观利率</option><option value="commodity_input">大宗商品成本</option><option value="regulatory">监管</option><option value="competitive">竞争格局</option><option value="other">其他</option></select></label>
              <label class="cat-check"><input name="multi_source_only" type="checkbox" /><span>仅多来源确认</span></label>
            </div>
            <div class="cat-filter-actions">
              <span class="mono" id="cat-filter-note">编辑中的条件不会被自动刷新覆盖</span>
              <button class="btn btn--ghost btn--sm" type="button" id="cat-clear">清除</button>
              <button class="btn btn--sm" type="button" data-cat-refresh="news" data-owner-only ${page.ownerAccess ? "disabled" : "hidden"}>正在读取</button>
              <button class="btn btn--sm" type="button" data-cat-refresh="calendar" data-owner-only ${page.ownerAccess ? "disabled" : "hidden"}>正在读取</button>
              <button class="btn btn--sm" type="button" data-cat-refresh="source_health" data-owner-only ${page.ownerAccess ? "disabled" : "hidden"}>正在读取</button>
              <button class="btn btn--amber btn--sm" type="submit">应用筛选</button>
            </div>
          </form>

          <div id="cat-read-state" aria-live="polite"></div>
          <div id="cat-panel" role="tabpanel" aria-labelledby="cat-tab-${esc(page.tab)}" tabindex="0"></div>
        </section>
      </div>`;
    writeDraftToForm();
    bindPageEvents();
    if (page.postRender) page.postRender();
  }

  function writeDraftToForm() {
    const form = $("#cat-filters", page.view);
    if (!form) return;
    for (const [key, value] of Object.entries(page.draft)) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      if (input.type === "checkbox") input.checked = !!value; else input.value = value == null ? "" : String(value);
    }
  }

  function readDraftFromForm() {
    const form = $("#cat-filters", page.view);
    if (!form) return Object.assign({}, page.draft);
    const next = {};
    for (const key of Object.keys(DEFAULT_FILTERS)) {
      const input = form.elements.namedItem(key);
      next[key] = input && input.type === "checkbox" ? !!input.checked : input ? input.value.trim() : page.draft[key];
    }
    next.ticker = upperTicker(next.ticker);
    return next;
  }

  function bindTabs() {
    const tabs = $$('[role="tab"][data-cat-tab]', page.view);
    const select = tab => {
      page.tab = tab.dataset.catTab;
      tabs.forEach(item => {
        const active = item === tab;
        item.setAttribute("aria-selected", String(active));
        item.tabIndex = active ? 0 : -1;
      });
      replaceRouteQuery();
      renderPanel();
      if (page.tab === "calendar" && !page.calendar) loadCalendar();
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", event => {
        let next = null;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (next == null) return;
        event.preventDefault();
        tabs[next].focus(); select(tabs[next]);
      });
    });
  }

  function bindPageEvents() {
    bindTabs();
    const form = $("#cat-filters", page.view);
    form.addEventListener("input", () => {
      page.draft = readDraftFromForm();
      $("#cat-filter-note", page.view).textContent = "有未应用的筛选条件；自动刷新会保留这些输入";
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      page.draft = readDraftFromForm();
      writeDraftToForm();
      page.applied = Object.assign({}, page.draft);
      replaceRouteQuery();
      loadFeed(true);
    });
    $("#cat-clear", page.view).addEventListener("click", () => {
      page.draft = Object.assign({}, DEFAULT_FILTERS);
      writeDraftToForm();
      $("#cat-filter-note", page.view).textContent = "筛选草稿已清除；点“应用筛选”后生效";
    });
    const runtimeForm = $("#cat-runtime-form", page.view);
    runtimeForm.addEventListener("input", () => {
      page.runtimeDirty = true;
      $("#cat-runtime-state", page.view).textContent = "有尚未保存的运行设置";
      $("#cat-runtime-save", page.view).disabled = false;
    });
    runtimeForm.addEventListener("submit", event => {
      event.preventDefault();
      saveRuntimeSettings();
    });
    $("#cat-runtime-rollback", page.view).addEventListener("click", rollbackRuntimeSettings);
    $$('[data-worker-action]', page.view).forEach(button => button.addEventListener("click", () => {
      requestWorkerOperation(button.dataset.workerAction);
    }));
    $$('[data-cat-refresh]', page.view).forEach(refreshButton => refreshButton.addEventListener("click", async buttonEvent => {
      const button = buttonEvent.currentTarget;
      const operationType = button.dataset.catRefresh;
      button.disabled = true; button.textContent = "正在提交";
      try {
        const operation = await N.catalystRefresh(operationType, { signal: page.controller && page.controller.signal });
        rememberManualRefresh(operationType, operation);
        renderRefreshButtons();
        const status = className(operation && operation.status);
        if (status === "cooldown") {
          $("#cat-read-state", page.view).innerHTML = stateBlock("queued", "刷新仍在冷却中", `${Math.max(1, Number(operation.retry_after_seconds) || 1)} 秒后可再次请求。`);
        } else {
          $("#cat-read-state", page.view).innerHTML = stateBlock(status || "queued", "刷新请求已进入后台队列", "当前快照继续显示；后台只执行所选的数据刷新，不占用模型预算。 ");
          watchManualRefresh(operation, operationType);
        }
      } catch (error) {
        if (error.name !== "AbortError") $("#cat-read-state", page.view).innerHTML = stateBlock("degraded", "刷新请求未完成", error.message);
      } finally {
        if (page.active) renderRefreshButtons();
      }
    }));
  }

  function runtimePreviousVersion() {
    const current = Number(page.runtimeSettings && page.runtimeSettings.version);
    return page.runtimeHistory
      .map(item => Number(item && item.version))
      .filter(version => Number.isFinite(version) && version < current)
      .sort((left, right) => right - left)[0] || null;
  }

  function renderRuntimeSettings() {
    if (!page.active || !page.view) return;
    const form = $("#cat-runtime-form", page.view);
    const documentState = page.runtimeSettings;
    const settings = documentState && documentState.settings;
    const available = !!(settings && settings.ai && settings.catalyst);
    for (const input of Array.from(form.elements)) input.disabled = !available;
    if (!available) {
      $("#cat-runtime-version", page.view).textContent = "暂不可用";
      $("#cat-runtime-state", page.view).textContent = "运行设置暂时无法读取";
      $("#cat-runtime-save", page.view).disabled = true;
      $("#cat-runtime-rollback", page.view).disabled = true;
      return;
    }
    if (!page.runtimeDirty) {
      form.elements.daily_token_limit.value = String(settings.ai.daily_token_limit);
      form.elements.manual_analysis_cooldown_seconds.value = String(settings.ai.manual_analysis_cooldown_seconds);
      form.elements.manual_analysis_enabled.checked = !!settings.ai.manual_analysis_enabled;
      form.elements.scheduled_analysis_enabled.checked = !!settings.catalyst.scheduled_analysis_enabled;
      form.elements.scheduled_times_et.value = Array.isArray(settings.catalyst.scheduled_times_et)
        ? settings.catalyst.scheduled_times_et.join(", ")
        : "";
      $("#cat-runtime-state", page.view).textContent = `已载入版本 ${documentState.version}`;
    }
    $("#cat-runtime-version", page.view).textContent = `版本 ${documentState.version}`;
    $("#cat-runtime-save", page.view).disabled = !page.runtimeDirty;
    const previous = runtimePreviousVersion();
    const rollback = $("#cat-runtime-rollback", page.view);
    rollback.disabled = !previous;
    rollback.textContent = previous ? `回滚到版本 ${previous}` : "没有可回滚版本";
  }

  function runtimeSettingsPatch() {
    const form = $("#cat-runtime-form", page.view);
    const times = String(form.elements.scheduled_times_et.value || "")
      .split(",")
      .map(value => value.trim())
      .filter(Boolean);
    if (!times.length || times.some(value => !/^([01]\d|2[0-3]):[0-5]\d$/.test(value))) {
      throw new Error("分析时刻须使用 24 小时制，例如 00:00, 01:00, 02:00");
    }
    const dailyTokenLimit = Number(form.elements.daily_token_limit.value);
    const cooldown = Number(form.elements.manual_analysis_cooldown_seconds.value);
    if (!Number.isInteger(dailyTokenLimit) || dailyTokenLimit < 102400 || dailyTokenLimit > 100000000) throw new Error("每日 Token 上限须在 102400 至 1 亿之间");
    if (!Number.isInteger(cooldown) || cooldown < 0 || cooldown > 86400) throw new Error("分析冷却须为 0 至 86400 秒");
    return {
      ai: {
        daily_max_jobs: 0,
        daily_budget_usd: 0,
        daily_token_limit: dailyTokenLimit,
        manual_analysis_enabled: !!form.elements.manual_analysis_enabled.checked,
        manual_analysis_cooldown_seconds: cooldown,
      },
      catalyst: {
        scheduled_analysis_enabled: !!form.elements.scheduled_analysis_enabled.checked,
        scheduled_times_et: times,
      },
    };
  }

  async function loadRuntimeSettings() {
    const request = ++page.runtimeSettingsRequest;
    const generation = page.generation;
    const controller = page.controller;
    try {
      const [documentState, history] = await Promise.all([
        N.runtimeSettings({ signal: controller.signal }),
        N.runtimeSettingsHistory({ signal: controller.signal }).catch(() => ({ revisions: [] })),
      ]);
      if (
        !page.active
        || request !== page.runtimeSettingsRequest
        || generation !== page.generation
        || controller !== page.controller
      ) return;
      page.runtimeSettings = documentState;
      page.runtimeHistory = Array.isArray(history && history.revisions) ? history.revisions : [];
      window.dispatchEvent(new CustomEvent(
        "optix:runtime-settings-changed",
        { detail: documentState },
      ));
    } catch (error) {
      if (
        error.name === "AbortError"
        || !page.active
        || request !== page.runtimeSettingsRequest
        || generation !== page.generation
        || controller !== page.controller
      ) return;
      page.runtimeSettings = null;
      page.runtimeHistory = [];
    }
    renderRuntimeSettings();
  }

  async function saveRuntimeSettings() {
    if (!page.runtimeSettings || !page.runtimeDirty) return;
    let settings;
    try {
      settings = runtimeSettingsPatch();
    } catch (error) {
      $("#cat-runtime-state", page.view).textContent = error.message;
      return;
    }
    if (!window.confirm("保存后会立即影响新的分析任务和固定分析时刻；已提交任务不会被删除。确定保存吗？")) return;
    const save = $("#cat-runtime-save", page.view);
    save.disabled = true;
    save.textContent = "正在保存";
    try {
      page.runtimeSettings = await N.updateRuntimeSettings({
        expected_version: Number(page.runtimeSettings.version),
        settings,
      }, { signal: page.controller.signal });
      page.runtimeDirty = false;
      $("#cat-runtime-state", page.view).textContent = "运行设置已保存并立即生效";
      await Promise.all([loadRuntimeSettings(), loadStatus(true), loadMarketFocus(true, false)]);
      window.dispatchEvent(new CustomEvent(
        "optix:runtime-settings-changed",
        { detail: page.runtimeSettings },
      ));
    } catch (error) {
      if (error.name !== "AbortError") $("#cat-runtime-state", page.view).textContent = error.message;
    } finally {
      if (page.active) {
        save.textContent = "保存设置";
        renderRuntimeSettings();
      }
    }
  }

  async function rollbackRuntimeSettings() {
    const targetVersion = runtimePreviousVersion();
    if (!page.runtimeSettings || !targetVersion) return;
    if (!window.confirm(`将运行设置回滚到版本 ${targetVersion}，并生成一个新的当前版本。确定继续吗？`)) return;
    const button = $("#cat-runtime-rollback", page.view);
    button.disabled = true;
    button.textContent = "正在回滚";
    try {
      page.runtimeSettings = await N.rollbackRuntimeSettings({
        expected_version: Number(page.runtimeSettings.version),
        target_version: targetVersion,
      }, { signal: page.controller.signal });
      page.runtimeDirty = false;
      await Promise.all([loadRuntimeSettings(), loadStatus(true), loadMarketFocus(true, false)]);
      window.dispatchEvent(new CustomEvent(
        "optix:runtime-settings-changed",
        { detail: page.runtimeSettings },
      ));
    } catch (error) {
      if (error.name !== "AbortError") $("#cat-runtime-state", page.view).textContent = error.message;
    } finally {
      if (page.active) renderRuntimeSettings();
    }
  }

  const WORKER_ACTION_LABELS = Object.freeze({
    focus_refresh: "刷新焦点股票池",
    strength_refresh: "刷新强势雷达",
    breakout_refresh: "刷新突破雷达",
    retention: "执行数据清理",
  });

  function workerTaskFor(actionType) {
    return actionType;
  }

  function workerTaskAvailable(worker, actionType) {
    if (!worker || worker.healthy !== true) return false;
    const tasks = Array.isArray(worker.tasks) ? worker.tasks : [];
    const taskName = workerTaskFor(actionType);
    const task = tasks.find(item => item && item.task_name === taskName);
    return !!task && task.enabled !== false;
  }

  function latestWorkerAction(actionType) {
    const actions = page.workerStatus && Array.isArray(page.workerStatus.actions)
      ? page.workerStatus.actions
      : [];
    return actions.find(item => item && item.action_type === actionType) || null;
  }

  function renderWorkerOperations() {
    if (!page.active || !page.view) return;
    clearTimeout(page.workerStateTimer);
    page.workerStateTimer = null;
    const worker = page.workerStatus || {};
    const healthy = worker.healthy === true;
    const workerStatus = className(worker.status || "unavailable");
    const health = $("#cat-worker-health", page.view);
    health.className = `chip ${healthy ? "chip--up" : "chip--down"}`;
    health.textContent = healthy ? "工作进程正常" : "工作进程暂不可用";

    const availability = (metaStatus(page.status) || {}).analysis_availability || {};
    const used = Number(availability.budget_used_usd);
    const submitted = Number(availability.submitted_jobs);
    const actualTokens = Number(availability.usage_total_tokens);
    const reservedTokens = Number(availability.token_budget_used_tokens);
    const tokenLimit = Number(availability.daily_token_limit);
    $("#cat-operation-facts", page.view).innerHTML = [
      ["后台心跳", worker.heartbeat_at ? N.ago(worker.heartbeat_at) : "尚无记录"],
      ["今日分析任务", Number.isFinite(submitted) ? `${Math.max(0, Math.round(submitted)).toLocaleString("zh-CN")} 次 · 不限次数` : "—"],
      ["今日实际用量", Number.isFinite(actualTokens) ? `${Math.max(0, Math.round(actualTokens)).toLocaleString("zh-CN")} Token` : "—"],
      ["今日额度占用", Number.isFinite(reservedTokens) && Number.isFinite(tokenLimit) ? `${Math.max(0, Math.round(reservedTokens)).toLocaleString("zh-CN")} / ${Math.max(0, Math.round(tokenLimit)).toLocaleString("zh-CN")} Token · 含运行中预留` : "—"],
      ["今日估算费用", Number.isFinite(used) ? `${used.toFixed(2)} 美元 · 仅记录` : "—"],
    ].map(([label, value]) => `<span><small>${esc(label)}</small><b>${esc(value)}</b></span>`).join("");

    let needsPoll = false;
    let needsTick = false;
    $$('[data-worker-action]', page.view).forEach(button => {
      const actionType = button.dataset.workerAction;
      const label = WORKER_ACTION_LABELS[actionType] || "执行操作";
      const operation = latestWorkerAction(actionType) || {};
      const status = className(operation.status);
      const remaining = remainingSeconds(operation);
      const taskEnabled = workerTaskAvailable(worker, actionType);
      if (["queued", "running"].includes(status)) {
        button.disabled = true;
        button.textContent = status === "running" ? `执行中 · ${elapsedSeconds(operation)}秒` : "已进入队列";
        needsPoll = true;
        needsTick = true;
      } else if (remaining > 0 || operation.reason === "cooldown") {
        button.disabled = true;
        button.textContent = `稍后可执行 · ${Math.max(1, remaining)}秒`;
        needsTick = true;
      } else if (!healthy || !taskEnabled) {
        button.disabled = true;
        button.textContent = `${label} · 暂不可用`;
      } else {
        button.disabled = false;
        button.textContent = label;
      }
    });

    const state = $("#cat-operation-state", page.view);
    if (!page.workerStatus) {
      state.innerHTML = stateBlock("unavailable", "后台状态暂不可用", "旧数据仍可读取，恢复后按钮会重新启用。");
    } else if (!healthy) {
      state.innerHTML = stateBlock("unavailable", "后台工作进程暂不可用", `状态：${workerStatus || "unavailable"}；不会创建重复任务。`);
    } else {
      const active = (worker.actions || []).find(item => ["queued", "running"].includes(className(item && item.status)));
      state.innerHTML = active
        ? stateBlock(className(active.status), WORKER_ACTION_LABELS[active.action_type] || "后台操作", active.status === "running" ? "正在执行，现有页面数据继续保留。" : "任务已排队，重复点击会复用此任务。")
        : stateBlock("active", "后台工作进程正常", "四项日常操作均由运行状态、冷却与幂等控制。 ");
    }
    if (needsPoll) page.workerStateTimer = window.setTimeout(() => loadWorkerStatus(true), 2000);
    else if (needsTick) page.workerStateTimer = window.setTimeout(renderWorkerOperations, 1000);
  }

  async function loadWorkerStatus(force) {
    const request = ++page.workerStatusRequest;
    try {
      const worker = await N.workerStatus({ force: !!force, signal: page.controller.signal });
      if (!page.active || request !== page.workerStatusRequest) return;
      page.workerStatus = worker;
    } catch (error) {
      if (error.name === "AbortError" || !page.active || request !== page.workerStatusRequest) return;
      page.workerStatus = null;
    }
    renderWorkerOperations();
  }

  async function requestWorkerOperation(actionType) {
    const label = WORKER_ACTION_LABELS[actionType];
    if (!label || !page.active) return;
    if (actionType === "retention" && !window.confirm("数据清理只处理已到保留期限的数据，并先遵守数据库备份策略。确定继续吗？")) return;
    const button = $(`[data-worker-action="${actionType}"]`, page.view);
    button.disabled = true;
    button.textContent = "正在提交";
    try {
      const operation = await N.requestWorkerAction(actionType, {}, { signal: page.controller.signal });
      const worker = page.workerStatus || { healthy: true, status: "ok", tasks: [], actions: [] };
      worker.actions = [operation].concat((worker.actions || []).filter(item => item.request_id !== operation.request_id));
      page.workerStatus = worker;
      renderWorkerOperations();
    } catch (error) {
      if (error.name !== "AbortError") {
        $("#cat-operation-state", page.view).innerHTML = stateBlock("failed", `${label}未提交`, error.message);
      }
      renderWorkerOperations();
    }
  }

  const REFRESH_LABELS = Object.freeze({
    news: "刷新新闻",
    calendar: "刷新日历",
    source_health: "检查来源",
  });

  function rememberManualRefresh(operationType, operation) {
    if (!operation || typeof operation !== "object") return;
    const raw = metaStatus(page.status) || {};
    if (!raw.manual_refreshes || typeof raw.manual_refreshes !== "object") raw.manual_refreshes = {};
    raw.manual_refreshes[operationType] = operation;
  }

  function remainingSeconds(operation) {
    const until = Date.parse(operation && operation.cooldown_until || "");
    if (Number.isFinite(until)) return Math.max(0, Math.ceil((until - Date.now()) / 1000));
    const explicit = Number(operation && operation.retry_after_seconds);
    return Number.isFinite(explicit) && explicit > 0 ? Math.ceil(explicit) : 0;
  }

  function elapsedSeconds(operation) {
    const since = Date.parse(operation && (operation.started_at || operation.requested_at) || "");
    return Number.isFinite(since) ? Math.max(0, Math.floor((Date.now() - since) / 1000)) : 0;
  }

  function renderRefreshButtons() {
    if (!page.active || !page.view) return;
    clearTimeout(page.refreshStateTimer);
    page.refreshStateTimer = null;
    const raw = metaStatus(page.status) || {};
    const availability = raw.analysis_availability && typeof raw.analysis_availability === "object"
      ? raw.analysis_availability
      : {};
    const workerUnavailable = !page.status
      || availability.worker_healthy === false
      || ["unavailable", "disabled"].includes(className(raw.status));
    const operations = raw.manual_refreshes && typeof raw.manual_refreshes === "object"
      ? raw.manual_refreshes
      : {};
    let needsTick = false;
    $$('[data-cat-refresh]', page.view).forEach(button => {
      const type = button.dataset.catRefresh;
      const operation = operations[type] || {};
      const status = className(operation.status);
      const remaining = remainingSeconds(operation);
      if (["queued", "running"].includes(status)) {
        const elapsed = elapsedSeconds(operation);
        button.disabled = true;
        button.textContent = status === "running" ? `刷新中 · ${elapsed}秒` : "刷新排队中";
        needsTick = true;
      } else if (remaining > 0 || status === "cooldown") {
        button.disabled = true;
        button.textContent = `稍后可刷新 · ${Math.max(1, remaining)}秒`;
        needsTick = true;
      } else if (workerUnavailable) {
        button.disabled = true;
        button.textContent = "刷新服务暂不可用";
      } else {
        button.disabled = false;
        button.textContent = REFRESH_LABELS[type] || "立即刷新";
      }
    });
    if (needsTick) page.refreshStateTimer = window.setTimeout(renderRefreshButtons, 1000);
  }

  function watchManualRefresh(operation, operationType) {
    const requestId = operation && operation.request_id;
    if (!requestId || !page.active) return;
    window.setTimeout(async () => {
      if (!page.active) return;
      try {
        const next = await N.catalystRefreshStatus(requestId, { signal: page.controller && page.controller.signal });
        rememberManualRefresh(operationType, next);
        renderRefreshButtons();
        const status = className(next && next.status);
        if (["queued", "running"].includes(status)) {
          $("#cat-read-state", page.view).innerHTML = stateBlock(status, status === "running" ? "正在刷新数据" : "刷新任务排队中", "这项普通刷新不会占用模型预算。 ");
          watchManualRefresh(next, operationType);
          return;
        }
        if (status === "failed") {
          $("#cat-read-state", page.view).innerHTML = stateBlock("failed", "刷新失败", next.error_code ? `错误码：${next.error_code}` : "后台没有完成这项刷新。 ");
          return;
        }
        $("#cat-read-state", page.view).innerHTML = stateBlock("completed", "刷新完成", "正在读取最新的本地快照。 ");
        if (operationType === "calendar") await loadCalendar(true);
        else await Promise.all([loadStatus(true), loadFeed(true)]);
      } catch (error) {
        if (error.name !== "AbortError" && page.active) $("#cat-read-state", page.view).innerHTML = stateBlock("degraded", "刷新状态暂时无法读取", error.message);
      }
    }, 2000);
  }

  function renderHeader() {
    const raw = metaStatus(page.status) || {};
    const status = stateOf(raw, "unavailable");
    const feedStream = raw.streams && (raw.streams.feed || raw.streams.latest) || {};
    const resyncRequired = !!(raw.resync_required || feedStream.resync_required);
    const sources = sourceItems(raw, page.feed);
    const active = sources.filter(source => ["active", "ok", "healthy"].includes(className(source.status || source.state))).length;
    const fallback = sources.filter(source => className(source.status || source.state) === "fallback").length;
    const degraded = sources.filter(source => ["degraded", "fallback", "failed", "stale", "unavailable"].includes(className(source.status || source.state))).length;
    const displayStatus = ["active", "empty"].includes(status) && degraded ? "degraded" : status;
    const warnings = list(raw, ["warnings"]).filter(value => typeof value === "string" && value.trim());
    const actualModel = raw.model || null;
    const model = actualModel || raw.expected_model || "gpt-5.6-terra";
    const reasoning = actualModel ? (raw.reasoning || "max") : (raw.expected_reasoning || "max");
    $("#cat-head-state", page.view).innerHTML = `<span class="data-state ${["active", "empty"].includes(displayStatus) && !resyncRequired ? "" : displayStatus === "unavailable" ? "is-down" : "is-warn"}"><i></i>${esc(resyncRequired ? "有界重新同步中" : statusLabel(displayStatus))} · 本地缓存</span>`;
    $("#cat-connection", page.view).textContent = resyncRequired ? "旧快照 · 重新同步中" : statusLabel(displayStatus);
    $("#cat-times", page.view).innerHTML = `数据截止 ${timeHtml(raw.data_through || raw.as_of)} · 最近同步 ${timeHtml(raw.last_sync_at)}`;
    $("#cat-model", page.view).textContent = actualModel
      ? `${model} · ${reasoning}`
      : `目标配置 ${model} · ${reasoning} · 尚无运行记录`;
    $("#cat-model-note", page.view).textContent = warnings[0]
      || "模型推断，不代表收益概率；影响分不是预期收益，置信度不是胜率。";
    $("#cat-facts", page.view).innerHTML = `
      <span><small>活跃来源</small><b>${sources.length ? active : "—"}</b></span>
      <span><small>降级来源</small><b>${sources.length ? (fallback ? `<span>兜底源</span> · ${fallback}` : degraded) : "—"}</b></span>
      <span><small>远程状态</small><b>${esc(resyncRequired ? `Resync · 第 ${feedStream.resync_generation ?? raw.resync_generation ?? "—"} 代` : statusLabel(raw.remote_status || raw.remote_state || status))}</b></span>
      <span><small>Schema</small><b>${esc(raw.schema_version || "—")}</b></span>`;
    renderRefreshButtons();
  }

  function hotspotItems(payload) {
    return list(payload, ["items", "hotspots", "event_groups", "results"]);
  }

  function cyclePayload(payload) {
    const raw = payload && (payload.cycle || payload.item) || payload || {};
    const job = raw.job && typeof raw.job === "object" ? raw.job : null;
    const cycle = Object.assign({}, raw);
    const cycleId = raw.cycle_id || raw.id || null;
    const storedJobId = (job && (job.job_id || job.id)) || raw.job_id || null;
    const awaitingSubmission = raw.awaiting_submission === true
      || String(storedJobId || "").startsWith("intent:");
    const analysisJobId = awaitingSubmission ? null : storedJobId;
    const localTransitionPending = raw.local_link_pending === true
      || raw.local_publish_pending === true;
    const rawStatus = raw.status || raw.state;
    const preferredStatus = localTransitionPending && rawStatus
      ? rawStatus
      : (job && (job.status || job.state)) || rawStatus || "pending";
    delete cycle.job;
    return Object.assign(cycle, {
      cycle_id: cycleId,
      job_id: cycleId,
      analysis_job_id: analysisJobId,
      awaiting_submission: awaitingSubmission,
      status: Jobs.normalizeStatus(preferredStatus),
    });
  }

  function focusCycleDecision(rawStatus, cycleState, preparedCountValue, ownerAccess = true) {
    const raw = rawStatus || {};
    const cycle = cycleState || {};
    const preparedRevision = Number(raw.prepared_revision || 0);
    const consumedRevision = Number(raw.last_consumed_revision || 0);
    const rawCyclePreparedRevision = Number(cycle.prepared_revision);
    const cycleHasPreparedRevision = Number.isFinite(rawCyclePreparedRevision) && rawCyclePreparedRevision >= 0;
    const cyclePreparedRevision = cycleHasPreparedRevision ? rawCyclePreparedRevision : 0;
    const preparedCount = finite(preparedCountValue) ? preparedCountValue : 0;
    const awaitingSubmission = cycle.awaiting_submission === true;
    const active = !!(
      !awaitingSubmission
      && (cycle.cycle_id || raw.active_cycle_id)
      && Jobs.isActive(cycle.status)
    );
    const availability = raw.analysis_availability && typeof raw.analysis_availability === "object"
      ? raw.analysis_availability
      : {};
    const availabilityReason = className(availability.reason || "available");
    const readOnly = availabilityReason === "read_only_mode" || !ownerAccess;
    const budgetMissing = availability.budget_available === false || availabilityReason === "budget_exhausted";
    const workerMissing = availability.worker_healthy === false || availabilityReason === "worker_unavailable";
    const concurrencyMissing = availability.concurrency_available === false || availabilityReason === "analysis_in_progress";
    const notConfigured = availability.configured === false || availabilityReason === "not_configured";
    const analysisUnavailable = !ownerAccess || raw.manual_enabled !== true || [
      "read_only_mode", "manual_analysis_disabled", "settings_unavailable", "catalyst_disabled",
    ].includes(availabilityReason);
    const snapshotUnavailable = ["stale", "unavailable", "disabled"].includes(className(raw.status));
    const hasNew = preparedRevision > consumedRevision && preparedCount > 0;
    const cooldownUntil = availability.cooldown_until || raw.cooldown_until;
    const cooldown = availability.cooldown_complete === false || !!(cooldownUntil && new Date(cooldownUntil).getTime() > Date.now());
    const unknownSubmission = className(cycle.error_code) === "submission_outcome_unknown";
    const newPreparationAfterUnknown = unknownSubmission
      && cycleHasPreparedRevision
      && preparedRevision > cyclePreparedRevision;
    const retryable = !!(
      cycle.cycle_id
      && !unknownSubmission
      && (
        awaitingSubmission
        || ["failed", "cancelled", "incomplete_output"].includes(className(cycle.status))
      )
    );
    const commonAllowed = !active && !budgetMissing && !workerMissing && !concurrencyMissing && !notConfigured && !analysisUnavailable && !snapshotUnavailable && !cooldown;
    const canRetry = commonAllowed && retryable;
    const canCreate = commonAllowed
      && !retryable
      && !!raw.manual_enabled
      && hasNew
      && (!unknownSubmission || newPreparationAfterUnknown);
    const canForce = commonAllowed
      && !retryable
      && !!raw.manual_enabled
      && !hasNew
      && preparedRevision > 0
      && !unknownSubmission;
    const canRun = canRetry || canCreate || canForce;
    const showAction = !analysisUnavailable;
    const buttonText = active ? "分析任务正在运行"
      : budgetMissing ? "今日预算已用完"
        : workerMissing ? "服务暂不可用"
        : concurrencyMissing ? "分析任务正在运行"
        : notConfigured ? "尚未配置OpenAI"
        : analysisUnavailable ? "分析功能未启用"
          : snapshotUnavailable ? "热点快照暂不可用"
            : unknownSubmission && !newPreparationAfterUnknown ? "提交结果待核对"
              : cooldown ? "分析冷却中"
                : canCreate ? (unknownSubmission
                ? `基于 ${Math.round(preparedCount)} 个新热点创建新周期`
                : `基于 ${Math.round(preparedCount)} 个新热点重新分析`)
                  : retryable ? "重试同一不可变快照"
                    : hasNew ? `基于 ${Math.round(preparedCount)} 个新热点重新分析`
                      : canForce ? "重新分析当前上下文" : "暂无可分析的热点上下文";
    return {
      preparedRevision, consumedRevision, cyclePreparedRevision, cycleHasPreparedRevision, preparedCount,
      awaitingSubmission,
      active, budgetMissing, workerMissing, concurrencyMissing, notConfigured, readOnly, analysisUnavailable, snapshotUnavailable,
      hasNew, cooldown, unknownSubmission, newPreparationAfterUnknown,
      retryable, canRetry, canCreate, canForce, canRun, buttonText,
      showAction,
      showHistoricalUnknown: unknownSubmission && (
        newPreparationAfterUnknown || notConfigured || analysisUnavailable || budgetMissing || snapshotUnavailable
      ),
    };
  }

  function focusCycleRequest(decision, cycleState) {
    const cycle = cycleState || {};
    if (!decision || !decision.canRun) return null;
    if (decision.canRetry && cycle.cycle_id) return { retry_cycle_id: cycle.cycle_id };
    if (decision.canCreate) return { expected_prepared_revision: decision.preparedRevision };
    if (decision.canForce) return { expected_prepared_revision: decision.preparedRevision, force: true };
    return null;
  }

  function focusUnknownHistoryHtml(cycleState, decision) {
    const cycle = cycleState || {};
    if (!decision || !decision.showHistoricalUnknown) return "";
    const occurredAt = cycle.completed_at || cycle.updated_at || cycle.created_at;
    const occurredLabel = occurredAt ? N.fmtDateTime(occurredAt) : "时间未知";
    const revisionLabel = decision.cyclePreparedRevision > 0
      ? `准备版本 ${decision.cyclePreparedRevision}`
      : "旧准备版本";
    const nextStep = decision.canCreate
      ? "当前的新准备版本可另行创建全新周期。"
      : decision.newPreparationAfterUnknown
        ? "新的准备版本不属于该历史周期，需待分析功能可用后另建周期。"
        : "需等待服务端完成核对。";
    return stateBlock(
      "degraded",
      `历史记录 · 提交结果待核对 · ${occurredLabel}`,
      `${revisionLabel} 无法确认远端是否已经受理，仍禁止重试同一周期。${nextStep}`,
    );
  }

  function compactValues(values, limit) {
    return list({ values: Array.isArray(values) ? values : [] }, ["values"])
      .slice(0, limit || 6)
      .map(value => typeof value === "object" ? (value.name || value.title || value.ticker || value.id) : value)
      .filter(Boolean);
  }

  function hotspotCard(item) {
    const sources = compactValues(item.source_names || item.sources, 4);
    const tickers = compactValues(item.validated_tickers || item.tickers, 6).map(upperTicker).filter(Boolean);
    const reasons = compactValues(item.hot_reasons || item.reasons || item.gate_reasons, 4);
    const hotScore = finite(item.hot_score) ? item.hot_score : null;
    const confirmation = item.component_scores && finite(item.component_scores.market_confirmation)
      ? item.component_scores.market_confirmation
      : null;
    return `<article class="cat-hotspot">
      <header><span class="chip ${hotScore != null && hotScore >= 75 ? "chip--down" : "chip--amber"}">热点 ${score(hotScore)}</span><span class="chip chip--mute">${esc(statusLabel(item.status || "prepared"))}</span></header>
      <h3>${esc(item.representative_title || "热点标题等待中文分析")}</h3>
      <p class="cat-hotspot__meta">${esc(item.event_type || "other")} · 独立来源 ${finite(item.source_count) ? Math.round(item.source_count) : sources.length || "—"} · 市场确认 ${confirmation == null ? "缺失并重算权重" : score(confirmation)}</p>
      <p class="cat-hotspot__meta">首次 ${timeHtml(item.first_published_at || item.available_at)} · 最近 ${timeHtml(item.last_published_at || item.available_at)}</p>
      ${reasons.length ? `<p>${esc(reasons.join(" · "))}</p>` : ""}
      <footer>${sources.map(value => `<span>${esc(value)}</span>`).join("")}${tickers.map(value => `<b>${esc(value)}</b>`).join("")}</footer>
    </article>`;
  }

  function cycleResultHtml(cycle) {
    const result = cycle && (cycle.result || cycle.analysis || cycle.structured_output);
    if (!result || typeof result !== "object") return "";
    const events = list(result, ["dominant_events", "events"]);
    const sectors = compactValues(result.affected_sectors, 8);
    const assessments = list(result, ["focus_ticker_assessments", "ticker_assessments"]);
    const assessmentHtml = item => {
      const supporting = compactValues(item.supporting_event_ids, 8);
      const conflicting = compactValues(item.conflicting_event_ids, 8);
      const risks = compactValues(item.risks, 6);
      return `<article><b>${esc(upperTicker(item.ticker) || "—")}</b><span class="mono">加权催化剂语境 ${signedScore(item.weighted_catalyst_context)} · 仅展示</span><p>${esc(item.summary || "证据不足，未形成方向摘要。")}</p>${supporting.length ? `<small>支持证据 ${esc(supporting.join(" · "))}</small>` : ""}${conflicting.length ? `<small>冲突证据 ${esc(conflicting.join(" · "))}</small>` : ""}${risks.length ? `<small>风险 ${esc(risks.join(" · "))}</small>` : ""}${item.confidence != null ? `<small>模型置信度 ${pct(item.confidence)} · 非胜率</small>` : ""}</article>`;
    };
    return `<div class="cat-cycle-result">
      <div class="cat-cycle-result__lead">
        <span class="chip chip--amber">模型推断 · ${esc(cycle.model || "gpt-5.6-terra")} · ${esc(cycle.reasoning || cycle.reasoning_effort || "max")}</span>
        <p>${esc(result.market_summary || (result.no_new_material_catalyst ? "本周期没有新的重要催化剂。" : "综合分析已完成。"))}</p>
        <small>模型时间 ${timeHtml(result.as_of || cycle.completed_at || cycle.updated_at)} · 快照时间 ${timeHtml(cycle.snapshot_as_of)}</small>
      </div>
      ${events.length ? `<section><h3>主要热点</h3><ul>${events.slice(0, 8).map(event => `<li>${esc(typeof event === "object" ? (event.summary || event.title || event.event_group_id || "事件") : event)}</li>`).join("")}</ul></section>` : ""}
      ${sectors.length ? `<section><h3>受影响行业</h3><p>${sectors.map(value => `<span class="chip chip--mute">${esc(value)}</span>`).join(" ")}</p></section>` : ""}
      ${assessments.length ? `<section><h3>焦点股票摘要</h3><div class="cat-cycle-tickers">${assessments.slice(0, 20).map(assessmentHtml).join("")}</div></section>` : ""}
      ${list(result, ["market_uncertainties"]).length ? `<section><h3>不确定性</h3><ul>${list(result, ["market_uncertainties"]).map(value => `<li>${esc(value)}</li>`).join("")}</ul></section>` : ""}
      <p class="cat-disclaimer">综合分析只使用有界事件组摘要，不包含完整文章，也不进入正式股票排名、突破评分或市场形态。</p>
    </div>`;
  }

  function renderFocusPanel() {
    const raw = page.focusStatus || {};
    const cycle = cyclePayload(page.marketCycle || {});
    const successfulCycle = cyclePayload(page.successfulMarketCycle || {});
    const displayedCycle = cycleResultHtml(cycle) ? cycle : successfulCycle;
    const events = hotspotItems(page.hotspots);
    const preparedCount = finite(raw.prepared_hot_count) && raw.prepared_hot_count > 0
      ? raw.prepared_hot_count
      : events.length;
    const decision = focusCycleDecision(raw, cycle, preparedCount, page.ownerAccess);
    const actionHost = $("#cat-focus-action", page.view);
    let button = actionHost && $("#cat-focus-run", actionHost);
    if (actionHost && !decision.showAction) {
      actionHost.replaceChildren();
      button = null;
    } else if (actionHost && !button) {
      actionHost.innerHTML = '<button class="btn btn--amber" type="button" id="cat-focus-run" disabled>读取准备状态</button>';
      button = $("#cat-focus-run", actionHost);
      button.addEventListener("click", () => startMarketFocusCycle());
    }
    if (button) {
      button.disabled = !decision.canRun;
      button.textContent = decision.buttonText;
    }
    $("#cat-focus-meta", page.view).innerHTML = [
      ["准备热点", finite(preparedCount) ? Math.round(preparedCount) : "—"],
      ["准备版本", raw.prepared_revision ?? "—"],
      ["已消费版本", raw.last_consumed_revision ?? "—"],
      ["上次固定分析", raw.last_cycle_at ? N.fmtDateTime(raw.last_cycle_at) : "—"],
      ["下次固定分析", raw.next_scheduled_at ? N.fmtDateTime(raw.next_scheduled_at) : "—"],
      ["数据截止", raw.data_through ? N.fmtDateTime(raw.data_through) : "—"],
    ].map(([label, value]) => `<span><small>${esc(label)}</small><b>${esc(value)}</b></span>`).join("");

    const terminalFailure = !decision.unknownSubmission
      && ["failed", "incomplete_output", "budget_blocked"].includes(cycle.status);
    const unknownNeedsReview = decision.unknownSubmission && !decision.newPreparationAfterUnknown;
    let state = "";
    if (decision.active) state = stateBlock(cycle.status, "分析任务正在运行", "新热点仍会进入下一准备版本，不会混入当前不可变快照。 ");
    else if (unknownNeedsReview) state = stateBlock("degraded", "提交结果待核对", "无法确认远端是否已经受理。为避免重复计费，本周期禁止重试，需等待服务端核对结果。 ");
    else if (terminalFailure) state = stateBlock("failed", statusLabel(cycle.status), cycle.error_code ? `${analysisErrorDetail(cycle)}；准备版本尚未消费。` : "准备版本尚未消费，可在冷却结束后显式重试。 ");
    else if (decision.budgetMissing) state = stateBlock("disabled", "今日分析预算已用完", `${budgetPolicyText()}；额度恢复前不会提交新的模型任务。`);
    else if (decision.workerMissing) state = stateBlock("disabled", "后台工作进程暂不可用", "工作进程恢复后才能创建新的综合分析周期。 ");
    else if (decision.concurrencyMissing) state = stateBlock("disabled", "已有分析正在运行", "同一时间只运行一项模型分析。 ");
    else if (decision.notConfigured) state = stateBlock("disabled", "模型服务尚未配置", "配置完成前只显示历史结果和热点准备状态，不会创建新的模型任务。 ");
    else if (decision.readOnly) state = stateBlock("disabled", "当前为只读模式", "当前只显示历史结果和热点准备状态，不会创建新的模型任务。 ");
    else if (decision.analysisUnavailable) state = stateBlock("disabled", "分析功能未启用", "当前只显示历史结果和热点准备状态，不会创建新的模型任务。 ");
    else if (decision.snapshotUnavailable) state = stateBlock("unavailable", "热点快照暂不可用", "当前快照恢复后才能创建新的综合分析周期。 ");
    else if (!decision.hasNew && !cycleResultHtml(displayedCycle)) state = stateBlock("empty", "当前没有新热点", "可重新分析现有新闻、焦点股票和市场数据；会创建新周期并可能产生模型费用。 ");
    const history = focusUnknownHistoryHtml(cycle, decision);
    const cards = events.length ? `<div class="cat-hotspot-list">${events.slice(0, 8).map(hotspotCard).join("")}</div>` : "";
    const preservedResult = displayedCycle.cycle_id && displayedCycle.cycle_id !== cycle.cycle_id
      ? stateBlock("active", "保留最近成功结果", "最新重跑未完成，旧周期结果继续显示且未被覆盖。")
      : "";
    $("#cat-focus-body", page.view).innerHTML = `${state}${history}${preservedResult}${cycleResultHtml(displayedCycle)}${cards}` || stateBlock("empty", "暂无热点准备记录", "确定性门控不会用中性分填充缺失证据。 ");
  }

  function summaryValue(summary, keys) {
    for (const key of keys) if (finite(summary && summary[key])) return summary[key];
    return null;
  }

  function renderSummary() {
    const summary = object(page.feed, ["summary", "stats", "counts"]) || object(metaStatus(page.status), ["summary", "stats", "counts"]) || {};
    const values = [
      ["近 6 小时新闻", summaryValue(summary, ["news_6h", "last_6h", "recent_6h_count"])],
      ["24 小时已分析", summaryValue(summary, ["analyzed_24h", "analysis_24h", "completed_24h_count"])],
      ["正向催化剂", summaryValue(summary, ["bullish", "positive", "bullish_count"])],
      ["负向催化剂", summaryValue(summary, ["bearish", "negative", "bearish_count"])],
      ["待分析", summaryValue(summary, ["pending", "pending_count"])],
      ["高影响宏观事件", summaryValue(summary, ["high_impact_calendar", "high_impact_macro", "macro_high_count"])],
    ];
    $("#cat-summary", page.view).innerHTML = values.map(([label, value]) => `<div class="cat-summary__item"><span>${esc(label)}</span><strong class="mono">${fmtCount(value)}</strong></div>`).join("");
  }

  function renderReadState() {
    const status = stateOf(page.feed, page.status ? stateOf(metaStatus(page.status), "unavailable") : "unavailable");
    const raw = metaStatus(page.status) || {};
    const feedStream = raw.streams && (raw.streams.feed || raw.streams.latest) || {};
    const resyncRequired = !!(raw.resync_required || feedStream.resync_required);
    const warnings = list(raw, ["warnings"]);
    if (resyncRequired) {
      $("#cat-read-state", page.view).innerHTML = stateBlock("stale", "正在有界重新同步", "旧快照继续可读；完整分页校验成功后才会原子切换水位。 ");
      return;
    }
    if (status === "active" || status === "empty") {
      $("#cat-read-state", page.view).innerHTML = warnings.length ? stateBlock("degraded", "部分能力受限", warnings[0]) : "";
      return;
    }
    const texts = {
      degraded: ["正在显示可用数据", "部分来源或远程同步异常，核心行情与评分不受影响。"],
      stale: ["正在显示最近一次有效快照", "请留意页眉中的数据截止时间。"],
      unavailable: ["催化剂数据暂不可用", "新闻服务异常不影响行情、期权、突破与财报。"],
      disabled: ["催化剂功能尚未启用", "当前没有可读取的本地新闻快照。"],
    };
    const copy = texts[status] || [statusLabel(status), "当前状态已如实保留。"];
    $("#cat-read-state", page.view).innerHTML = stateBlock(status, copy[0], copy[1]);
  }

  function newsCard(item) {
    const analysis = analysisOf(item);
    const status = analysisStatus(item);
    const classification = classificationOf(item);
    const confidence = confidenceOf(item);
    const relevance = marketRelevanceOf(item);
    const sentiment = sentimentOf(item);
    const impacts = impactsOf(item);
    const ruleOnly = isRuleOnlyAnalysis(item);
    const job = item.analysis_job || item.job || null;
    const triggerEnabled = analysisTriggerEnabledOf(item);
    const access = analysisActionDecision(
      triggerEnabled,
      analysisAvailabilityOf(item),
      page.ownerAccess,
    );
    const activeJob = !!(job && job.job_id && Jobs.isActive(job.status));
    const force = analysisRetryForce(item, job);
    const url = item.url || item.source_url;
    return `<article class="cat-news ${item.is_stale ? "is-stale" : ""}">
      <div class="cat-news__rail" aria-hidden="true"><i></i></div>
      <div class="cat-news__meta mono">
        <span>${esc(item.source || "未知来源")}</span>
        <span>发布 ${timeHtml(item.published_at)}</span>
        <span>抓取 ${timeHtml(item.fetched_at)}</span>
        ${item.is_stale ? `<span class="chip chip--amber">过期快照</span>` : ""}
      </div>
      <h3>${esc(itemTitle(item))}</h3>
      ${itemSummary(item) ? `<p class="cat-news__summary">${esc(itemSummary(item))}</p>` : ""}
      <div class="cat-news__signals">
        <span class="chip ${chipTone(status)}">${esc(statusLabel(status))}</span>
        ${ruleOnly ? `<span class="chip chip--mute">规则中性 · 信息不足 · 未调用模型</span>` : analysis && classification ? `<span class="chip ${chipTone(classification)}">新闻整体 · 模型推断 · ${esc(classLabel(classification))}</span>` : ""}
        ${analysis && !ruleOnly && sentiment != null ? `<span class="chip chip--mute">总体情绪 ${signedScore(sentiment)}</span>` : ""}
        ${analysis && !ruleOnly && confidence != null ? `<span class="chip chip--mute">模型置信度 ${pct(confidence)} · 非胜率</span>` : ""}
        ${analysis && !ruleOnly && relevance != null ? `<span class="chip chip--mute">市场相关度 ${pct(relevance)}</span>` : ""}
      </div>
        ${analysis && !ruleOnly && impacts.length ? `<div class="cat-news__tickers">${impacts.slice(0, 5).map(impact => `<span><b>${esc(upperTicker(impact.ticker || impact.symbol) || "—")}</b><em class="${(impactValue(impact) || 0) > 0 ? "u" : (impactValue(impact) || 0) < 0 ? "d" : "dim"}">${signedScore(impactValue(impact))}</em><small>${esc(mechanismLabel(impact.mechanism || impact.impact_mechanism || ""))}</small></span>`).join("")}</div>` : ""}
      <footer class="cat-news__foot">
        <span class="mono">${ruleOnly ? "上下文不足，确定性规则未生成方向，也未调用模型" : analysis ? "模型影响分仅作新闻展示，不进入正式评分" : "尚无模型方向、影响分或置信度"}</span>
        <span>${externalLink(url, "原文")}<button type="button" class="cat-link" data-catalyst-news="${esc(itemId(item))}">查看分析 →</button>${access.showAction ? `<button type="button" class="btn btn--amber btn--sm" data-catalyst-analyze="${esc(itemId(item))}" ${access.canTrigger && !activeJob ? "" : `disabled title="${esc(activeJob ? "分析任务正在运行" : access.detail)}"`}>${activeJob ? "分析处理中" : force ? "重新分析" : "生成分析"}</button>` : ""}</span>
      </footer>
    </article>`;
  }

  function renderFeed() {
    const items = visibleFeedItems(page.feed);
    if (!items.length) return stateBlock("empty", "没有匹配的新闻", "当前筛选条件下没有可显示的本地记录；这不代表新闻服务不可用。 ");
    return `<div class="cat-timeline">${items.map(newsCard).join("")}</div>${page.feed && page.feed.next_cursor ? `<button class="btn btn--sm cat-more" type="button" data-cat-more>加载更多</button>` : ""}`;
  }

  function aggregateImpacts() {
    const explicit = list(page.feed, ["stock_impacts", "ticker_impacts", "impact_rows"]);
    if (explicit.length) return explicit;
    const map = new Map();
    for (const item of visibleFeedItems(page.feed)) {
      for (const impact of impactsOf(item)) {
        const ticker = upperTicker(impact.ticker || impact.symbol);
        if (!ticker) continue;
        const row = map.get(ticker) || { ticker, net_impact: 0, positive_count: 0, negative_count: 0, sources: new Set(), latest_at: null, max_confidence: null, catalyst_count: 0 };
        const value = impactValue(impact);
        if (finite(value)) row.net_impact += value;
        if (finite(value) && value > 0) row.positive_count += 1;
        if (finite(value) && value < 0) row.negative_count += 1;
        row.catalyst_count += 1;
        if (item.source) row.sources.add(item.source);
        const at = item.published_at || item.fetched_at;
        if (at && (!row.latest_at || new Date(at) > new Date(row.latest_at))) row.latest_at = at;
        const confidence = finite(impact.confidence) ? impact.confidence : confidenceOf(item);
        if (confidence != null) row.max_confidence = row.max_confidence == null ? confidence : Math.max(row.max_confidence, confidence);
        map.set(ticker, row);
      }
    }
    return Array.from(map.values()).map(row => Object.assign({}, row, { source_diversity: row.sources.size })).sort((a, b) => Math.abs(b.net_impact || 0) - Math.abs(a.net_impact || 0));
  }

  function renderStocks() {
    const rows = aggregateImpacts();
    if (!rows.length) return stateBlock("empty", "没有可汇总的股票影响", "未分析新闻不会被补成中性方向或零分。 ");
    return `<p class="cat-table-note">展示排序：按当前新闻结果的绝对净影响排列；不会改写 intrinsic_score、ranking_score、突破质量或告警优先级。</p>
      <div class="cat-table-wrap"><table class="cat-table"><thead><tr><th>股票</th><th>净影响</th><th>正向 / 负向</th><th>来源多样性</th><th>最近催化剂</th><th>最高模型置信度</th><th>研究入口</th></tr></thead><tbody>
      ${rows.map(row => {
        const ticker = upperTicker(row.ticker || row.symbol);
        const net = row.net_impact ?? row.impact_score ?? row.net_score;
        const sourceDiversity = row.source_diversity ?? (Array.isArray(row.sources) ? row.sources.length : null);
        return `<tr><td><b class="mono">${esc(ticker || "—")}</b></td><td class="mono ${finite(net) && net > 0 ? "u" : finite(net) && net < 0 ? "d" : "dim"}">${signedScore(net)}<small>模型影响分 · 非收益</small></td><td>${fmtCount(row.positive_count ?? row.bullish_count)} / ${fmtCount(row.negative_count ?? row.bearish_count)}</td><td>${fmtCount(sourceDiversity)}</td><td>${timeHtml(row.latest_at || row.latest_catalyst_at || row.published_at)}</td><td>${pct(row.max_confidence ?? row.confidence)}<small>非胜率</small></td><td><button type="button" class="cat-link" data-cat-stock="${esc(ticker)}">研究</button><a class="cat-link" href="#screener">选股</a><a class="cat-link" href="#breakouts?ticker=${encodeURIComponent(ticker)}">雷达</a></td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  function eventDistance(at) {
    if (!at) return "—";
    const diff = new Date(at).getTime() - Date.now();
    if (!Number.isFinite(diff)) return "—";
    const hours = Math.round(Math.abs(diff) / 36e5);
    return diff >= 0 ? (hours < 24 ? `${hours} 小时后` : `${Math.round(hours / 24)} 天后`) : (hours < 24 ? `${hours} 小时前` : `${Math.round(hours / 24)} 天前`);
  }

  function renderCalendar() {
    if (!page.calendar) return stateBlock("loading", "正在读取经济日历", "该请求只读本地缓存。 ");
    const events = calendarItems(page.calendar);
    if (!events.length) return stateBlock(stateOf(page.calendar, "empty"), "当前日期范围没有宏观事件", "空结果与服务不可用是不同状态。 ");
    return `<p class="cat-table-note">宏观日历只展示来源事实，不自动生成多空判断。</p><div class="cat-table-wrap"><table class="cat-table cat-table--calendar"><thead><tr><th>日期时间</th><th>国家 / 货币</th><th>事件</th><th>影响级别</th><th>预期</th><th>前值</th><th>实际</th><th>距离事件</th></tr></thead><tbody>
      ${events.map(event => `<tr class="${event.is_stale ? "is-stale" : ""}"><td>${timeHtml(event.event_at || event.scheduled_at || event.datetime)}</td><td>${esc(event.country || event.currency || "—")}</td><td><b>${esc(event.name || event.event || event.title || "—")}</b>${event.is_stale ? `<small class="d">过期快照</small>` : ""}</td><td><span class="chip ${className(event.impact) === "high" || Number(event.impact) >= 3 ? "chip--amber" : "chip--mute"}">${esc(event.impact || event.importance || "—")}</span></td><td>${esc(event.forecast ?? "—")}</td><td>${esc(event.previous ?? "—")}</td><td>${esc(event.actual ?? "—")}</td><td>${esc(eventDistance(event.event_at || event.scheduled_at || event.datetime))}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function renderSources() {
    const sources = sourceItems(metaStatus(page.status), page.feed);
    if (!sources.length) return stateBlock("empty", "没有来源健康记录", "接口未返回来源级状态，不会用假数据补齐。 ");
    return `<div class="cat-source-grid">${sources.map(source => {
      const status = className(source.status || source.state || "not_configured");
      return `<article class="cat-source"><header><b>${esc(source.source || source.name || "未知来源")}</b><span class="chip ${chipTone(status)}">${esc(statusLabel(status))}</span></header><dl><div><dt>最近成功</dt><dd>${timeHtml(source.last_success || source.last_success_at)}</dd></div><div><dt>连续失败</dt><dd>${fmtCount(source.failures ?? source.consecutive_failures)}</dd></div><div><dt>下次尝试</dt><dd>${timeHtml(source.next_attempt || source.next_attempt_at)}</dd></div><div><dt>原始 / 入库 / 重复</dt><dd>${fmtCount(source.raw_count ?? source.raw)} / ${fmtCount(source.inserted_count ?? source.inserted)} / ${fmtCount(source.duplicates_count ?? source.duplicate_count ?? source.duplicates)}</dd></div></dl></article>`;
    }).join("")}</div>`;
  }

  function bindPanelActions() {
    $$('[data-catalyst-news]', page.view).forEach(button => button.addEventListener("click", () => openNews(button.dataset.catalystNews)));
    $$('[data-catalyst-analyze]', page.view).forEach(button => button.addEventListener("click", async () => {
      const id = button.dataset.catalystAnalyze;
      const item = visibleFeedItems(page.feed).find(candidate => itemId(candidate) === id);
      if (!item) return;
      await openNews(id);
      startAnalysis(item, analysisRetryForce(item, item.analysis_job || item.job || null));
    }));
    $$('[data-cat-stock]', page.view).forEach(button => button.addEventListener("click", () => page.openStock && page.openStock(button.dataset.catStock)));
    const more = $("[data-cat-more]", page.view);
    if (more) more.addEventListener("click", () => loadMore(more));
  }

  function renderPanel() {
    if (!page.active) return;
    const panel = $("#cat-panel", page.view);
    panel.setAttribute("aria-labelledby", "cat-tab-" + page.tab);
    panel.innerHTML = page.tab === "feed" ? renderFeed() : page.tab === "stocks" ? renderStocks() : page.tab === "calendar" ? renderCalendar() : renderSources();
    bindPanelActions();
  }

  async function loadMore(button) {
    if (!page.feed || !page.feed.next_cursor) return;
    const request = page.feedRequest;
    const cursor = page.feed.next_cursor;
    const filterSnapshot = JSON.stringify(queryParams());
    button.disabled = true; button.textContent = "读取中…";
    try {
      const next = await N.catalystFeed(Object.assign(queryParams(), { cursor }), { signal: page.controller.signal });
      if (
        !page.active
        || request !== page.feedRequest
        || cursor !== (page.feed && page.feed.next_cursor)
        || filterSnapshot !== JSON.stringify(queryParams())
      ) return;
      const merged = feedItems(page.feed).concat(feedItems(next));
      page.feed = Object.assign({}, page.feed, next, { items: merged });
      renderPanel();
    } catch (error) {
      if (error.name !== "AbortError") { button.disabled = false; button.textContent = "加载失败，重试"; }
    }
  }

  async function loadStatus(force) {
    const request = ++page.statusRequest;
    let payload;
    try {
      payload = await N.catalystStatus(force, page.controller.signal);
    } catch (error) {
      if (error.name === "AbortError") return;
      payload = { status: "unavailable", warnings: [error.message] };
    }
    if (!page.active || request !== page.statusRequest) return;
    page.status = payload;
    renderHeader(); renderSummary(); renderReadState();
    if (page.feed) renderPanel();
  }

  async function loadFeed(force) {
    const request = ++page.feedRequest;
    const snapshot = Object.assign({}, page.applied);
    $("#cat-read-state", page.view).innerHTML = stateBlock("loading", "正在读取筛选结果", "读取的是 Option Pro 本地缓存，不会触发模型分析。 ");
    try {
      const payload = await N.catalystFeed(Object.assign(queryParams(), { limit: 50 }), { force, signal: page.controller.signal });
      if (!page.active || request !== page.feedRequest) return;
      page.feed = payload;
      page.applied = snapshot;
      renderSummary(); renderReadState(); renderPanel();
      $("#cat-filter-note", page.view).textContent = "已应用筛选；编辑中的条件仍会在自动刷新时保留";
    } catch (error) {
      if (error.name === "AbortError" || !page.active || request !== page.feedRequest) return;
      if (!page.feed) page.feed = { status: "unavailable", items: [] };
      $("#cat-read-state", page.view).innerHTML = stateBlock("degraded", "新闻读取失败", error.message + "；已有内容不会被清空。 ");
      renderPanel();
    }
  }

  async function loadCalendar(force) {
    const request = ++page.calendarRequest;
    const start = new Date();
    const end = new Date(start.getTime() + 7 * 86400e3);
    let payload;
    try {
      payload = await N.catalystCalendar({ date_from: start.toISOString().slice(0, 10), date_to: end.toISOString().slice(0, 10) }, { force, signal: page.controller.signal });
    } catch (error) {
      if (error.name === "AbortError") return;
      payload = { status: "unavailable", events: [], error: error.message };
    }
    if (!page.active || request !== page.calendarRequest) return;
    page.calendar = payload;
    if (page.tab === "calendar") renderPanel();
  }

  function watchMarketFocusCycle(initial) {
    const cycle = cyclePayload(initial);
    if (
      !cycle.cycle_id
      || cycle.awaiting_submission
      || !Jobs.isActive(cycle.status)
    ) return;
    Jobs.watch(cycle, {
      scope: "catalyst-page:market-focus",
      poll: (id, signal) => N.catalystMarketCycle(id, { signal }).then(cyclePayload),
      onUpdate: next => { if (page.active) { page.marketCycle = next; renderFocusPanel(); } },
      onComplete: next => {
        if (!page.active) return;
        page.marketCycle = next;
        renderFocusPanel();
        loadMarketFocus(true, false);
      },
      onError: error => {
        if (!page.active) return;
        page.marketCycle = Object.assign({}, cycle, { status: "failed", error_code: error.code || "market_focus_poll_failed", retry_after_seconds: error.retryAfter });
        renderFocusPanel();
      },
    });
  }

  function startMarketFocusCycle() {
    if (!page.ownerAccess) return;
    const raw = page.focusStatus || {};
    const cycle = cyclePayload(page.marketCycle || {});
    const hotspotCount = hotspotItems(page.hotspots).length;
    const preparedCount = finite(raw.prepared_hot_count) && raw.prepared_hot_count > 0
      ? raw.prepared_hot_count
      : hotspotCount;
    const decision = focusCycleDecision(raw, cycle, preparedCount, page.ownerAccess);
    const request = focusCycleRequest(decision, cycle);
    if (!request) return;
    const budget = budgetPolicyText();
    const confirmation = request.force
      ? `当前没有新热点，将重新分析同一准备版本并创建新周期，旧结果会保留。该操作可能产生模型费用；${budget}。确定继续吗？`
      : request.retry_cycle_id
        ? `重试会再次提交同一份不可变快照，可能产生模型费用；${budget}。确定继续吗？`
        : `将根据当前新热点创建综合分析周期。该操作可能产生模型费用；${budget}。确定继续吗？`;
    if (!window.confirm(confirmation)) return;
    Jobs.start({
      scope: "catalyst-page:market-focus",
      create: signal => N.createCatalystMarketCycle(
        request,
        { signal },
      ).then(cyclePayload),
      poll: (id, signal) => N.catalystMarketCycle(id, { signal }).then(cyclePayload),
      onUpdate: cycle => { if (page.active) { page.marketCycle = cycle; renderFocusPanel(); } },
      onComplete: cycle => {
        if (!page.active) return;
        page.marketCycle = cycle;
        renderFocusPanel();
        loadMarketFocus(true, false);
      },
      onError: error => {
        if (!page.active) return;
        page.marketCycle = { status: "failed", error_code: error.code || "market_focus_create_failed", retry_after_seconds: error.retryAfter };
        renderFocusPanel();
      },
    });
  }

  async function loadMarketFocus(force, shouldWatch = true) {
    const request = ++page.focusRequest;
    const options = { force, signal: page.controller.signal };
    const safe = (promise, fallback) => promise.catch(error => {
      if (error.name === "AbortError") throw error;
      return Object.assign({}, fallback, { error_code: error.code || "market_focus_unavailable" });
    });
    try {
      const [status, hotspots, latest] = await Promise.all([
        safe(N.catalystHotspotStatus(force, page.controller.signal), { manual_enabled: false, analysis_availability: { enabled: false, reason: "unavailable" } }),
        safe(N.catalystHotspots({ limit: 8, include_consumed: true }, options), { items: [] }),
        safe(N.catalystMarketCycleLatest(options), null),
      ]);
      if (!page.active || request !== page.focusRequest) return;
      page.focusStatus = status;
      page.hotspots = hotspots;
      page.marketCycle = latest ? cyclePayload(latest) : null;
      page.successfulMarketCycle = latest && latest.latest_successful_cycle
        ? cyclePayload(latest.latest_successful_cycle)
        : null;
      renderFocusPanel();
      if (shouldWatch && page.marketCycle) watchMarketFocusCycle(page.marketCycle);
    } catch (error) {
      if (error.name === "AbortError" || !page.active || request !== page.focusRequest) return;
      if (!page.focusStatus) page.focusStatus = { manual_enabled: false, analysis_availability: { enabled: false, reason: "unavailable" } };
      renderFocusPanel();
    }
  }

  async function loadAll(force) {
    await Promise.all([
      loadStatus(force),
      loadFeed(force),
      loadMarketFocus(force),
      page.ownerAccess ? loadRuntimeSettings() : Promise.resolve(),
      page.ownerAccess ? loadWorkerStatus(force) : Promise.resolve(),
      page.tab === "calendar" ? loadCalendar(force) : Promise.resolve(),
    ]);
  }

  function scheduleRefresh() {
    clearTimeout(page.timer);
    page.timer = setTimeout(async () => {
      if (!page.active) return;
      page.draft = readDraftFromForm();
      await loadAll(true);
      writeDraftToForm();
      scheduleRefresh();
    }, 120e3);
  }

  async function resolveOwnerAccess() {
    let accessStatus = N.currentAccessStatus ? N.currentAccessStatus() : null;
    if (!accessStatus) {
      try {
        accessStatus = await N.accessStatus();
      } catch (error) {
        accessStatus = null;
      }
    }
    return !!(accessStatus && accessStatus.logged_in === true);
  }

  async function renderPage(options) {
    leaveRoute();
    page.active = true;
    page.view = options.view;
    page.params = options.params || new URLSearchParams();
    page.generation += 1;
    const generation = page.generation;
    page.controller = new AbortController();
    page.status = null;
    page.feed = null;
    page.calendar = null;
    page.focusStatus = null;
    page.hotspots = null;
    page.marketCycle = null;
    page.successfulMarketCycle = null;
    page.runtimeSettings = null;
    page.runtimeHistory = [];
    page.runtimeDirty = false;
    page.workerStatus = null;
    page.ownerAccess = false;
    page.openStock = options.openStock;
    page.postRender = options.postRender;
    page.tab = ["feed", "stocks", "calendar", "sources"].includes(page.params.get("tab")) ? page.params.get("tab") : "feed";
    page.draft = routeFilters(page.params);
    page.applied = Object.assign({}, page.draft);
    page.view.innerHTML = stateBlock("loading", "正在打开公开研究页面", "只读取行情、新闻和已有分析。 ");
    const ownerAccess = await resolveOwnerAccess();
    if (!page.active || generation !== page.generation) return;
    page.ownerAccess = ownerAccess;
    pageShell();
    loadAll(false).then(() => {
      if (page.active && page.params.get("news")) openNews(page.params.get("news"));
    });
    scheduleRefresh();
  }

  function leaveRoute() {
    page.active = false;
    page.statusRequest += 1;
    page.feedRequest += 1;
    page.calendarRequest += 1;
    page.focusRequest += 1;
    page.runtimeSettingsRequest += 1;
    page.workerStatusRequest += 1;
    clearTimeout(page.timer);
    clearTimeout(page.refreshStateTimer);
    clearTimeout(page.workerStateTimer);
    page.timer = null;
    page.refreshStateTimer = null;
    page.workerStateTimer = null;
    if (page.controller) page.controller.abort();
    page.controller = null;
    Jobs.stopPrefix("catalyst-page:");
  }

  function analysisBody(item, statusPayload) {
    const persistedAnalysis = analysisOf(item);
    const jobResult = statusPayload && statusPayload.result && typeof statusPayload.result === "object" ? statusPayload.result : null;
    const analysis = persistedAnalysis || jobResult;
    const displayItem = persistedAnalysis || !analysis ? item : Object.assign({}, item, { analysis });
    const status = statusPayload ? Jobs.normalizeStatus(statusPayload.status) : analysisStatus(item);
    const ruleOnly = isRuleOnlyAnalysis(displayItem, statusPayload);
    const triggerEnabled = analysisTriggerEnabledOf(item);
    const access = analysisActionDecision(
      triggerEnabled,
      analysisAvailabilityOf(item),
      page.ownerAccess,
    );
    const canTrigger = access.canTrigger;
    const isActive = ["pending", "queued", "in_progress", "cancel_requested"].includes(status) && statusPayload && statusPayload.job_id;
    const model = statusPayload && statusPayload.model || (analysis && analysis.model) || item.model || "gpt-5.6-terra";
    const reasoning = statusPayload && statusPayload.reasoning || (analysis && analysis.reasoning) || item.reasoning || "max";
    if (!analysis) {
      const elapsed = statusPayload && Jobs.elapsed(statusPayload);
      const accessNotice = (isActive || status === "failed") && access.modeUnavailable
        ? stateBlock("disabled", access.title, access.detail)
        : "";
      const runAction = !isActive && access.showAction
        ? `<button class="btn btn--amber btn--sm" id="cat-analysis-run" type="button" data-cat-analyze="${esc(itemId(item))}" ${canTrigger ? "" : `disabled title="${esc(access.detail)}"`}>${status === "failed" || status === "cancelled" ? "重试分析" : "生成分析"}</button>`
        : "";
      const cancelAction = isActive && page.ownerAccess
        ? `<button class="btn btn--sm" id="cat-analysis-cancel" type="button" data-cat-cancel-job ${status === "cancel_requested" ? "disabled" : ""}>${status === "cancel_requested" ? "正在取消" : "取消任务"}</button>`
        : "";
      return `<div class="cat-analysis-state">
        <span class="chip ${chipTone(status)}">${esc(statusLabel(status))}</span>
        <h3>${isActive ? "分析任务正在运行" : status === "failed" ? "分析任务失败" : esc(access.title)}</h3>
        <p>${isActive ? `${statusPayload.submitted_at || statusPayload.created_at ? "提交 " + N.fmtDateTime(statusPayload.submitted_at || statusPayload.created_at) : ""}${elapsed != null && status === "in_progress" ? " · 已运行 " + elapsed + " 秒" : ""} · ${esc(model)} · ${esc(reasoning)}；不显示估算进度。` : status === "failed" ? "来源信息仍可查看；失败状态不会补成中性方向。" : esc(access.detail)}</p>
        ${statusPayload && statusPayload.error_code ? `<p class="d">${esc(analysisErrorDetail(statusPayload))}</p>` : ""}
        ${accessNotice}
        ${runAction || cancelAction ? `<div class="cat-analysis-actions">${runAction}${cancelAction}</div>` : ""}
      </div>`;
    }
    const impacts = rawImpactsOf(displayItem);
    const validations = validationMapOf(displayItem);
    const retryableTerminal = !!(statusPayload && statusPayload.job_id && ["failed", "cancelled", "budget_blocked"].includes(status));
    const accessNotice = access.modeUnavailable
      ? stateBlock("disabled", access.title, access.detail)
      : "";
    const jobNotice = isActive
      ? `<div class="cat-analysis-state" style="margin-bottom:14px"><span class="chip ${chipTone(status)}">${esc(statusLabel(status))}</span><p>分析任务正在运行 · ${statusPayload && (statusPayload.submitted_at || statusPayload.created_at) ? "提交 " + N.fmtDateTime(statusPayload.submitted_at || statusPayload.created_at) : "新的分析版本正在后台处理"} · ${esc(model)} · ${esc(reasoning)} · 现有已完成版本继续显示 · 不显示估算进度</p>${page.ownerAccess ? `<div class="cat-analysis-actions"><button class="btn btn--sm" id="cat-analysis-cancel" type="button" data-cat-cancel-job ${status === "cancel_requested" ? "disabled" : ""}>${status === "cancel_requested" ? "正在取消" : "取消新任务"}</button></div>` : ""}</div>`
      : retryableTerminal
        ? `<div class="cat-analysis-state" style="margin-bottom:14px"><span class="chip ${chipTone(status)}">${esc(statusLabel(status))}</span><p>新版本任务未完成；现有已完成版本继续显示。</p>${statusPayload.error_code ? `<p class="d">${esc(analysisErrorDetail(statusPayload))}</p>` : ""}</div>`
        : "";
    const reanalysisAction = !isActive && access.showAction
      ? `<div class="cat-analysis-actions"><button class="btn btn--amber btn--sm" id="cat-analysis-run" type="button" data-cat-analyze="${esc(itemId(item))}" ${canTrigger ? "" : `disabled title="${esc(access.detail)}"`}>重新分析</button></div>`
      : "";
    return `${accessNotice}${jobNotice}${reanalysisAction}<div class="cat-analysis-result">
      <div class="cat-news__signals">
        <span class="chip ${ruleOnly ? "chip--mute" : "chip--amber"}"><i></i>${esc(analysisOriginLabel(displayItem))}</span>
        ${!ruleOnly && classificationOf(displayItem) ? `<span class="chip ${chipTone(classificationOf(displayItem))}">新闻整体 · ${esc(classLabel(classificationOf(displayItem)))}</span>` : ""}
        ${!ruleOnly && confidenceOf(displayItem) != null ? `<span class="chip chip--mute">模型置信度 ${pct(confidenceOf(displayItem))} · 非胜率</span>` : ""}
        ${!ruleOnly && marketRelevanceOf(displayItem) != null ? `<span class="chip chip--mute">市场相关度 ${pct(marketRelevanceOf(displayItem))}</span>` : ""}
      </div>
      ${analysis.title_zh ? `<h3>${esc(analysis.title_zh)}</h3>` : ""}
      ${analysis.headline_summary ? `<p class="cat-analysis-lede">${esc(analysis.headline_summary)}</p>` : ""}
      ${!ruleOnly && finite(analysis.overall_sentiment) ? `<dl class="cat-analysis-kv"><div><dt>总体情绪</dt><dd class="mono ${analysis.overall_sentiment > 0 ? "u" : analysis.overall_sentiment < 0 ? "d" : "dim"}">${signedScore(analysis.overall_sentiment)}</dd></div><div><dt>分析时间</dt><dd>${timeHtml(item.analyzed_at || analysis.analyzed_at)}</dd></div></dl>` : ""}
      ${analysis.causal_summary ? `<section><h4>因果摘要</h4><p>${esc(analysis.causal_summary)}</p></section>` : ""}
      ${list(analysis, ["key_factors"]).length ? `<section><h4>关键因素</h4><ul>${list(analysis, ["key_factors"]).map(factor => `<li>${esc(factor)}</li>`).join("")}</ul></section>` : ""}
      ${list(analysis, ["uncertainty_notes"]).length ? `<section><h4>不确定性</h4><ul>${list(analysis, ["uncertainty_notes"]).map(note => `<li>${esc(note)}</li>`).join("")}</ul></section>` : ""}
      ${!ruleOnly && impacts.length ? `<section><h4>模型识别股票 · 模型影响分不是预期收益 · 验证后才计入影响榜</h4><div class="cat-impact-list">${impacts.map(impact => { const ticker = upperTicker(impact.ticker || impact.symbol); const validation = validations.get(ticker) || {}; const trusted = ["canonical", "valid_external"].includes(className(validation.validation_status)); return `<div><b class="mono">${esc(ticker || "—")}</b><span class="mono ${(impactValue(impact) || 0) > 0 ? "u" : (impactValue(impact) || 0) < 0 ? "d" : "dim"}">${signedScore(impactValue(impact))}</span><span>${esc(horizonLabel(impact.horizon || impact.impact_horizon || ""))}</span><span>${esc(mechanismLabel(impact.mechanism || impact.impact_mechanism || ""))}</span><span class="chip ${trusted ? "chip--up" : "chip--mute"}">${esc(validationLabel(validation.validation_status))}</span>${impactReasonHtml(impact.reason || impact.rationale)}</div>`; }).join("")}</div></section>` : ""}
      <p class="mono cat-disclaimer">${esc(model)} · ${esc(reasoning)} · ${ruleOnly ? "确定性规则结果 · 未调用外部模型" : "结构化模型结果 · 不展示隐藏推理"} · 不构成投资建议。</p>
    </div>`;
  }

  function bindAnalysisActions(item, job, watchCurrent, asOf) {
    const box = $("#cat-analysis-body", document);
    if (!box) return;
    const run = $("[data-cat-analyze]", box);
    if (run) run.addEventListener("click", () => startAnalysis(item, analysisRetryForce(item, job), asOf));
    const cancel = $("[data-cat-cancel-job]", box);
    if (cancel && page.ownerAccess) cancel.addEventListener("click", async () => {
      if (!window.confirm("确认取消这项分析任务？已完成的结果不会被删除。")) return;
      cancel.disabled = true;
      await Jobs.cancel("catalyst-drawer:" + itemId(item));
    });
    if (watchCurrent !== false && job && job.job_id && Jobs.isActive(job.status)) watchAnalysis(item, job, asOf);
  }

  function updateAnalysis(item, job, asOf) {
    const box = $("#cat-analysis-body", document);
    if (!box) return;
    const hadFocus = box.contains(document.activeElement);
    const focusId = hadFocus && document.activeElement.id;
    const scroll = window.OPTIX_DECK && window.OPTIX_DECK.drawer ? window.OPTIX_DECK.drawer.scrollTop() : 0;
    box.innerHTML = analysisBody(item, job);
    bindAnalysisActions(item, job, false, asOf);
    const nextFocus = focusId ? document.getElementById(focusId) : null;
    if (nextFocus && box.contains(nextFocus)) nextFocus.focus({ preventScroll: true });
    else if (hadFocus) { box.tabIndex = -1; box.focus({ preventScroll: true }); }
    if (window.OPTIX_DECK && window.OPTIX_DECK.drawer) window.OPTIX_DECK.drawer.restoreScroll(scroll);
  }

  function watchAnalysis(item, initial, asOf) {
    const id = itemId(item);
    Jobs.watch(initial, {
      scope: "catalyst-drawer:" + id,
      poll: (jobId, signal) => N.catalystAnalysisJob(jobId, { signal }),
      cancel: jobId => N.cancelCatalystAnalysisJob(jobId),
      onUpdate: job => updateAnalysis(item, job, asOf),
      onComplete: async job => {
        if (job.status !== "completed") { updateAnalysis(item, job, asOf); return; }
        N.invalidateCache("/api/catalysts/news/" + id);
        N.invalidateCache("/api/catalysts/feed");
        try {
          const fresh = await N.catalystNews(id, { as_of: asOf || undefined }, { force: true, signal: drawerController && drawerController.signal });
          const next = newsItemFromPayload(fresh);
          updateAnalysis(next, job, asOf);
        } catch (error) { if (error.name !== "AbortError") updateAnalysis(item, job, asOf); }
      },
      onError: error => updateAnalysis(item, { status: "failed", error_code: error.code || "job_poll_failed" }, asOf),
    });
  }

  function startAnalysis(item, force, asOf) {
    if (!page.ownerAccess) return;
    const id = itemId(item);
    const budget = budgetPolicyText();
    const confirmation = force
      ? `重新分析会创建新的分析版本，旧结果会保留。该操作可能产生模型费用；${budget}。确定继续吗？`
      : `生成分析可能产生模型费用；${budget}。确定继续吗？`;
    if (!window.confirm(confirmation)) return;
    Jobs.start({
      scope: "catalyst-drawer:" + id,
      create: signal => N.createCatalystAnalysis(id, force, { signal }),
      poll: (jobId, signal) => N.catalystAnalysisJob(jobId, { signal }),
      cancel: jobId => N.cancelCatalystAnalysisJob(jobId),
      onUpdate: job => updateAnalysis(item, job, asOf),
      onComplete: async job => {
        if (job.status !== "completed") { updateAnalysis(item, job, asOf); return; }
        N.invalidateCache("/api/catalysts/news/" + id);
        N.invalidateCache("/api/catalysts/feed");
        try {
          const fresh = await N.catalystNews(id, { as_of: asOf || undefined }, { force: true, signal: drawerController && drawerController.signal });
          updateAnalysis(newsItemFromPayload(fresh), job, asOf);
        } catch (error) { if (error.name !== "AbortError") updateAnalysis(item, job, asOf); }
      },
      onError: error => updateAnalysis(item, { status: "failed", error_code: error.code || "analysis_create_failed", retry_after_seconds: error.retryAfter }, asOf),
    });
  }

  function newsDrawerHtml(item) {
    const job = item.analysis_job || item.job || null;
    return `<header class="drawer__head cat-drawer-head">
      <span class="mono">新闻分析 · ${esc(item.source || "未知来源")}</span>
      <h2 id="drawer-title">${esc(itemTitle(item))}</h2>
      <div class="cat-news__signals"><span class="chip ${chipTone(analysisStatus(item))}">${esc(statusLabel(analysisStatus(item)))}</span>${item.is_stale ? `<span class="chip chip--amber">过期快照</span>` : ""}</div>
      <p class="mono">发布 ${timeHtml(item.published_at)} · 抓取 ${timeHtml(item.fetched_at)} · 分析 ${timeHtml(item.analyzed_at)}</p>
    </header>
    ${itemSummary(item) ? `<p class="cat-drawer-summary">${esc(itemSummary(item))}</p>` : ""}
    <div class="cat-drawer-links">${externalLink(item.url || item.source_url, "查看新闻原文")}</div>
    <section class="sect"><div class="sect-head"><span class="sect-head__no">ANALYSIS</span><h2>模型分析</h2><span class="sect-head__rule"></span><span class="sect-head__meta">按需生成</span></div><div id="cat-analysis-body">${analysisBody(item, job)}</div></section>`;
  }

  async function openNews(newsId, options) {
    const id = String(newsId || "").trim();
    if (!id || !window.OPTIX_DECK || !window.OPTIX_DECK.drawer) return;
    const asOf = options && options.asOf || null;
    onDrawerClosed();
    const controller = new AbortController();
    drawerController = controller;
    window.OPTIX_DECK.drawer.open(`<div class="cat-drawer-loading">${stateBlock("loading", "正在读取新闻详情", "只读 Option Pro 本地缓存。 ")}</div>`, { title: "新闻详情" });
    try {
      page.ownerAccess = await resolveOwnerAccess();
      if (controller.signal.aborted || drawerController !== controller) return;
      const payload = await N.catalystNews(id, { as_of: asOf || undefined }, { signal: controller.signal });
      const item = newsItemFromPayload(payload);
      if (asOf) item.analysis_trigger_enabled = false;
      window.OPTIX_DECK.drawer.open(newsDrawerHtml(item), { preserveScroll: true, title: itemTitle(item) });
      bindAnalysisActions(item, item.analysis_job || item.job || null, !asOf, asOf);
    } catch (error) {
      if (error.name === "AbortError") return;
      window.OPTIX_DECK.drawer.open(stateBlock("unavailable", "新闻详情暂不可用", error.message), { preserveScroll: true, title: "新闻详情读取失败" });
    }
  }

  function tickerItems(payload) { return list(payload, ["items", "news", "catalysts", "results"]); }
  function tickerStateHtml(payload, context) {
    const status = stateOf(payload, tickerItems(payload).length ? "active" : "empty");
    if (status === "empty") return stateBlock("empty", context === "breakout" ? "最近时间窗口没有匹配到催化剂" : "最近 72 小时没有匹配到催化剂", "空结果不改变技术判断。 ");
    if (status === "stale") return stateBlock("stale", "正在显示最近一次有效新闻快照", "请留意新闻时间；正式评分没有变化。 ");
    if (status === "unavailable" || status === "disabled") return stateBlock(status, "新闻服务暂不可用", context === "breakout" ? "不影响突破判断。" : "不影响行情、K线、期权、估值和评分。 ");
    if (status === "degraded") return stateBlock("degraded", "新闻数据部分降级", "保留现有快照；核心研究数据不受影响。 ");
    return "";
  }

  function compactNews(item) {
    const analysis = analysisOf(item);
    const ruleOnly = isRuleOnlyAnalysis(item);
    const impact = ruleOnly ? null : impactsOf(item)[0];
    const value = impactValue(impact);
    const direction = impactDirection(impact);
    return `<article class="cat-compact-news"><div><span class="mono">${esc(item.source || "未知来源")} · ${timeHtml(item.published_at)}</span><b>${esc(itemTitle(item))}</b></div><div class="cat-news__signals"><span class="chip ${chipTone(analysisStatus(item))}">${esc(statusLabel(analysisStatus(item)))}</span>${ruleOnly ? `<span class="chip chip--mute">信息不足 · 未调用模型</span>` : ""}${analysis && !ruleOnly && direction ? `<span class="chip ${chipTone(direction)}">股票影响 · ${esc(classLabel(direction))}</span>` : ""}${analysis && !ruleOnly && classificationOf(item) ? `<span class="chip chip--mute">新闻整体 · ${esc(classLabel(classificationOf(item)))}</span>` : ""}${analysis && !ruleOnly && finite(value) ? `<span class="chip chip--mute">影响 ${signedScore(value)} · 非收益</span>` : ""}${analysis && !ruleOnly && confidenceOf(item) != null ? `<span class="chip chip--mute">置信度 ${pct(confidenceOf(item))} · 非胜率</span>` : ""}</div>${analysis && (ruleOnly ? analysis.causal_summary : (impact && (impact.reason || impact.rationale) || analysis.causal_summary)) ? `<p>${esc(ruleOnly ? analysis.causal_summary : ((impact && (impact.reason || impact.rationale)) || analysis.causal_summary))}</p>` : ""}<button type="button" class="cat-link" data-catalyst-news="${esc(itemId(item))}">查看详情 →</button></article>`;
  }

  function tickerSummary(payload, items) {
    const directions = items.map(item => impactDirection(impactsOf(item)[0])).filter(Boolean);
    const positive = directions.filter(value => value === "bullish").length;
    const negative = directions.filter(value => value === "bearish").length;
    const neutral = directions.filter(value => value === "neutral").length;
    const sources = new Set(items.map(item => item.source).filter(Boolean));
    const latest = items.map(item => item.published_at || item.fetched_at).filter(Boolean).sort().at(-1) || null;
    const impacts = items.flatMap(impactsOf);
    const horizons = Array.from(new Set(impacts.map(impact => horizonLabel(impact.horizon || impact.impact_horizon)).filter(value => value !== "—"))).slice(0, 3);
    const mechanisms = Array.from(new Set(impacts.map(impact => mechanismLabel(impact.mechanism || impact.impact_mechanism)).filter(value => value !== "—"))).slice(0, 3);
    const macroRisk = payload && (payload.macro_event_risk ?? payload.macro_risk);
    return `<dl class="cat-inline-summary"><div><dt>股票影响 正向 / 负向 / 中性</dt><dd>${directions.length ? `${positive} / ${negative} / ${neutral}` : "—"}</dd></div><div><dt>来源多样性</dt><dd>${items.length ? sources.size : "—"}</dd></div><div><dt>最近催化剂</dt><dd>${latest ? N.ago(latest) : "—"}</dd></div><div><dt>影响期限</dt><dd>${horizons.length ? esc(horizons.join(" · ")) : "—"}</dd></div><div><dt>影响机制</dt><dd>${mechanisms.length ? esc(mechanisms.join(" · ")) : "—"}</dd></div><div><dt>宏观事件风险</dt><dd>${macroRisk == null ? "—" : esc(macroRisk)}</dd></div></dl>`;
  }

  async function mountTickerPanel(container, ticker, options) {
    if (!container || !ticker) return;
    const previous = inlineControllers.get(container); if (previous) previous.abort();
    const controller = new AbortController(); inlineControllers.set(container, controller);
    drawerPanelControllers.add(controller);
    const opts = options || {};
    container.innerHTML = stateBlock("loading", "正在读取新闻催化剂", "只读本地缓存，不会请求模型。 ");
    try {
      const payload = await N.tickerCatalysts(upperTicker(ticker), { window_hours: opts.windowHours || 72, limit: opts.limit || 3, as_of: opts.asOf || undefined, include_neutral: true }, { signal: controller.signal });
      if (!container.isConnected || controller.signal.aborted) return;
      const items = tickerItems(payload).slice(0, opts.limit || 3);
      const state = tickerStateHtml(payload, opts.context);
      container.innerHTML = `${state}${opts.context === "stock" && items.length ? tickerSummary(payload, items) : ""}${items.length ? `<div class="cat-compact-list">${items.map(compactNews).join("")}</div>` : ""}<a class="cat-link cat-all-link" href="#catalysts?ticker=${encodeURIComponent(upperTicker(ticker))}">查看全部催化剂 →</a>`;
      $$('[data-catalyst-news]', container).forEach(button => button.addEventListener("click", () => openNews(button.dataset.catalystNews, { asOf: opts.asOf || null })));
    } catch (error) {
      if (error.name === "AbortError" || !container.isConnected) return;
      container.innerHTML = stateBlock("unavailable", "新闻服务暂不可用", opts.context === "breakout" ? "不影响突破判断。" : "不影响其他研究数据。 ");
    } finally {
      drawerPanelControllers.delete(controller);
    }
  }

  function batchResults(payload) {
    const raw = payload && (payload.results || payload.tickers || payload.items || payload.data);
    if (Array.isArray(raw)) return new Map(raw.map(row => [upperTicker(row.ticker || row.symbol), row]));
    if (raw && typeof raw === "object") return new Map(Object.entries(raw).map(([ticker, row]) => [upperTicker(ticker), row]));
    return new Map();
  }

  function batchMetrics(row) {
    const items = tickerItems(row);
    let net = finite(row && row.net_impact) ? row.net_impact : null;
    if (net == null) {
      const vals = items.flatMap(impactsOf).map(impactValue).filter(finite);
      net = vals.length ? vals.reduce((sum, value) => sum + value, 0) : null;
    }
    const latest = row && (row.latest_at || row.latest_catalyst_at) || items.map(item => item.published_at || item.fetched_at).filter(Boolean).sort().at(-1) || null;
    const sources = row && row.source_diversity != null ? row.source_diversity : new Set(items.map(item => item.source).filter(Boolean)).size;
    return { count: row && finite(row.count) ? row.count : items.length, net, latest, sources, status: stateOf(row, items.length ? "active" : "empty") };
  }

  async function mountScreenerBatch(root, rows) {
    if (!root || !Array.isArray(rows) || !rows.length) return;
    abortPageEnhancements();
    const controller = new AbortController();
    pageEnhancementControllers.add(controller);
    const tickers = rows.map(row => upperTicker(row.ticker)).filter(Boolean).slice(0, 50);
    try {
      const batchSize = page.ownerAccess ? 50 : 20;
      const batches = [];
      for (let offset = 0; offset < tickers.length; offset += batchSize) {
        batches.push(tickers.slice(offset, offset + batchSize));
      }
      const payloads = await Promise.all(batches.map(batch => N.catalystBatch(
        batch,
        { window_hours: 72, limit: 3, include_neutral: true },
        { signal: controller.signal },
      )));
      if (!root.isConnected) return;
      const results = new Map();
      payloads.forEach(payload => {
        batchResults(payload).forEach((value, key) => results.set(key, value));
      });
      const metrics = new Map();
      $$('[data-catalyst-summary]', root).forEach(box => {
        const ticker = upperTicker(box.dataset.catalystSummary);
        const row = results.get(ticker) || { status: "empty", items: [] };
        const metric = batchMetrics(row); metrics.set(ticker, metric);
        box.innerHTML = `<span class="chip ${chipTone(metric.status)}">催化剂 ${metric.status === "unavailable" ? "不可用" : fmtCount(metric.count)}</span><span class="mono">最近 ${metric.latest ? N.ago(metric.latest) : "—"}</span><span class="mono ${finite(metric.net) && metric.net > 0 ? "u" : finite(metric.net) && metric.net < 0 ? "d" : "dim"}">净影响 ${signedScore(metric.net)} · 非收益</span><span class="mono">来源 ${fmtCount(metric.sources)}</span>`;
      });
      const controls = $("[data-cat-screener-sort]", root);
      if (controls) {
        controls.hidden = false;
        controls.addEventListener("change", event => {
          const mode = event.target.value;
          const listRoot = $("[data-candidate-list]", root);
          if (!listRoot) return;
          const nodes = $$('[data-cand-ticker]', listRoot);
          nodes.sort((a, b) => {
            if (mode === "deterministic") return Number(a.dataset.candRank) - Number(b.dataset.candRank);
            const ma = metrics.get(upperTicker(a.dataset.candTicker)) || {};
            const mb = metrics.get(upperTicker(b.dataset.candTicker)) || {};
            if (mode === "latest") return (new Date(mb.latest || 0) - new Date(ma.latest || 0)) || Number(a.dataset.candRank) - Number(b.dataset.candRank);
            const absA = finite(ma.net) ? Math.abs(ma.net) : -Infinity;
            const absB = finite(mb.net) ? Math.abs(mb.net) : -Infinity;
            return (absB - absA) || Number(a.dataset.candRank) - Number(b.dataset.candRank);
          }).forEach(node => listRoot.appendChild(node));
        });
      }
    } catch (error) {
      if (error.name === "AbortError" || !root.isConnected) return;
      $$('[data-catalyst-summary]', root).forEach(box => { box.innerHTML = `<span class="chip chip--mute">新闻暂不可用 · 正式 ranking_score 未变</span>`; });
    } finally {
      pageEnhancementControllers.delete(controller);
    }
  }

  function abortPageEnhancements() {
    for (const controller of pageEnhancementControllers) controller.abort();
    pageEnhancementControllers.clear();
  }

  function onDrawerClosed() {
    if (drawerController) drawerController.abort();
    drawerController = null;
    for (const controller of drawerPanelControllers) controller.abort();
    drawerPanelControllers.clear();
    Jobs.stopPrefix("catalyst-drawer:");
  }

  window.OPTIX_CATALYSTS = {
    renderPage, leaveRoute, openNews, mountTickerPanel, mountScreenerBatch, abortPageEnhancements, onDrawerClosed,
    labels: { statusLabel, classLabel },
    formatConfidence: pct,
    analysisErrorMessage, analysisErrorDetail,
    impactsOf, impactDirection, sentimentOf, isRuleOnlyAnalysis, analysisOriginLabel, analysisRetryForce, compactNews, plainText,
    itemTitle, itemSummary,
    analysisActionDecision, cyclePayload, focusCycleDecision, focusCycleRequest, focusUnknownHistoryHtml,
    workerTaskAvailable,
  };
})();
