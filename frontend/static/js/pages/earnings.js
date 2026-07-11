import { api } from '../api.js';
import { renderIcon } from '../icons.js';

const EARNINGS_STYLE_ID = 'optix-earnings-v3-styles';
const DAYS_IN_TIMELINE = 7;
const WEEKDAY_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const RELATION_ORDER = ['competitor', 'supplier', 'customer', 'etf', 'opposing', 'other'];

let activeEarningsGeneration = 0;
let impactLoadToken = 0;
let stylesheetPromise = null;

function ensureEarningsStylesheet() {
  const existing = document.getElementById(EARNINGS_STYLE_ID);
  if (existing?.sheet) return Promise.resolve();
  if (stylesheetPromise) return stylesheetPromise;

  stylesheetPromise = new Promise((resolve, reject) => {
    const link = existing || document.createElement('link');
    link.id = EARNINGS_STYLE_ID;
    link.rel = 'stylesheet';
    link.href = new URL('../../css/optix-earnings-v3.css', import.meta.url).href;
    link.addEventListener('load', resolve, { once: true });
    link.addEventListener('error', () => {
      link.remove();
      reject(new Error('财报页样式未能载入'));
    }, { once: true });
    if (!existing) document.head.appendChild(link);
  }).catch((error) => {
    stylesheetPromise = null;
    throw error;
  });
  return stylesheetPromise;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function navigateToDetail(ticker) {
  const symbol = String(ticker || '').trim().toUpperCase();
  if (symbol) window.location.hash = `#detail/${encodeURIComponent(symbol)}`;
}

function fmtLargeMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return '—';
  const absolute = Math.abs(number);
  if (absolute >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
  if (absolute >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
  if (absolute >= 1e6) return `$${(number / 1e6).toFixed(1)}M`;
  return `$${number.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function fmtEps(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toFixed(2)}` : '—';
}

function parseISO(value) {
  const [year, month, day] = String(value || '').slice(0, 10).split('-').map(Number);
  if (!year || !month || !day) return null;
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  date.setHours(0, 0, 0, 0);
  return date;
}

function localToday() {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date;
}

function isoFromDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function addDays(date, amount) {
  const next = new Date(date);
  next.setDate(next.getDate() + amount);
  next.setHours(0, 0, 0, 0);
  return next;
}

function formatRange(start, end) {
  const sameYear = start.getFullYear() === end.getFullYear();
  const sameMonth = sameYear && start.getMonth() === end.getMonth();
  if (sameMonth) return `${start.getMonth() + 1} 月 ${start.getDate()}—${end.getDate()} 日`;
  if (sameYear) return `${start.getMonth() + 1} 月 ${start.getDate()} 日—${end.getMonth() + 1} 月 ${end.getDate()} 日`;
  return `${start.getFullYear()} 年 ${start.getMonth() + 1} 月 ${start.getDate()} 日—${end.getFullYear()} 年 ${end.getMonth() + 1} 月 ${end.getDate()} 日`;
}

function formatFullDate(date) {
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日 · ${WEEKDAY_LABELS[date.getDay()]}`;
}

function normalizeEarnings(payload) {
  const source = Array.isArray(payload) ? payload : (payload?.earnings ?? []);
  const deduplicated = new Map();
  source.forEach((item) => {
    const ticker = String(item?.ticker || '').trim().toUpperCase();
    const date = parseISO(item?.earnings_date || item?.date || '');
    if (!ticker || !date) return;
    const normalized = {
      ticker,
      name: String(item?.name || item?.company || ticker),
      date: isoFromDate(date),
      dateObj: date,
      sector: String(item?.sector || ''),
      epsEstimate: item?.eps_estimate ?? null,
      revenueEstimate: item?.revenue_estimate ?? null,
      marketCap: item?.market_cap ?? null,
    };
    deduplicated.set(`${normalized.date}:${ticker}`, normalized);
  });
  return Array.from(deduplicated.values()).sort((a, b) => (
    a.date.localeCompare(b.date)
    || (Number(b.marketCap) || 0) - (Number(a.marketCap) || 0)
    || a.ticker.localeCompare(b.ticker)
  ));
}

function relationLabel(relation) {
  return {
    competitor: '同行竞争',
    supplier: '上游供应',
    customer: '下游客户',
    etf: '指数与基金',
    opposing: '反向关联',
    other: '其他关联',
  }[relation] || '其他关联';
}

function directionMeta(direction) {
  if (direction === 'bullish') return { className: 'positive', symbol: '↑', label: '利好' };
  if (direction === 'bearish') return { className: 'negative', symbol: '↓', label: '利空' };
  return { className: 'neutral', symbol: '—', label: '中性' };
}

function isEarningsMounted(generation) {
  return generation === activeEarningsGeneration
    && window.location.hash.replace(/^#/, '').split('/')[0] === 'earnings'
    && Boolean(document.querySelector(`[data-earnings-mount="${generation}"]`));
}

function renderShell(generation) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = `
    <section class="earnings-page" data-earnings-mount="${generation}" aria-labelledby="earnings-title" data-motion-page="earnings">
      <header class="earnings-page__header" data-motion-reveal data-motion-key="earnings-header">
        <div>
          <span class="earnings-kicker">事件日历</span>
          <h1 id="earnings-title">财报日历</h1>
          <p id="earnings-summary">正在整理未来财报事件…</p>
        </div>
        <div class="earnings-week-nav" aria-label="切换七日时间范围">
          <button type="button" data-week-action="previous" aria-label="查看前七日">
            ${renderIcon('chevron_left', { size: 18 })}
          </button>
          <button type="button" class="earnings-week-nav__today" data-week-action="today">回到今天</button>
          <button type="button" data-week-action="next" aria-label="查看后七日">
            ${renderIcon('chevron_right', { size: 18 })}
          </button>
        </div>
      </header>

      <section class="earnings-calendar" aria-labelledby="earnings-timeline-title" data-motion-reveal data-motion-key="earnings-calendar">
        <header class="earnings-section-heading">
          <div>
            <span class="earnings-kicker">选择日期</span>
            <h2 id="earnings-timeline-title">未来七日</h2>
          </div>
          <time id="earnings-range-label">—</time>
        </header>
        <div id="earnings-timeline-track" class="earnings-timeline" role="group" aria-label="选择财报日期" aria-busy="true">
          ${Array.from({ length: DAYS_IN_TIMELINE }, () => '<div class="earnings-timeline__skeleton" aria-hidden="true"></div>').join('')}
        </div>
      </section>

      <section class="earnings-events" aria-labelledby="earnings-events-title" data-motion-reveal data-motion-key="earnings-events">
        <header class="earnings-section-heading earnings-events__heading">
          <div>
            <span id="earnings-events-eyebrow" class="earnings-kicker">所选日期</span>
            <h2 id="earnings-events-title">今日事件</h2>
          </div>
          <strong id="earnings-events-count" aria-live="polite">—</strong>
        </header>
        <div id="earnings-events-list" class="earnings-events__list" aria-busy="true">
          ${Array.from({ length: 3 }, () => '<div class="earnings-event-skeleton" aria-hidden="true"><i></i><i></i></div>').join('')}
        </div>
      </section>

      <section id="earnings-impact-panel" class="earnings-impact" aria-labelledby="earnings-inspector-title" data-motion-reveal data-motion-key="earnings-impact" data-motion-lens hidden>
        <header class="earnings-impact__header">
          <div>
            <span class="earnings-kicker">按需展开</span>
            <h2 id="earnings-inspector-title">关联影响</h2>
          </div>
          <button type="button" data-impact-close>关闭关联分析</button>
        </header>
        <p id="earnings-impact-status" class="earnings-visually-hidden" role="status" aria-live="polite"></p>
        <div id="earnings-impact-body" class="earnings-impact__body" aria-busy="false"></div>
      </section>

      <div id="earnings-tooltip" class="earnings-tooltip" role="tooltip" hidden></div>
    </section>
  `;
}

function rootForGeneration(generation) {
  return document.querySelector(`[data-earnings-mount="${generation}"]`);
}

function renderHeaderSummary(root, earnings, today) {
  const summary = root?.querySelector('#earnings-summary');
  if (!summary) return;
  const end = addDays(today, DAYS_IN_TIMELINE - 1);
  const near = earnings.filter((item) => item.dateObj >= today && item.dateObj <= end);
  const sectors = new Set(near.map((item) => item.sector).filter(Boolean));
  const next = earnings.find((item) => item.dateObj >= today);
  const pieces = [`未来七日 ${near.length} 场`];
  if (sectors.size) pieces.push(`覆盖 ${sectors.size} 个行业`);
  if (next) pieces.push(`最近一场在 ${next.dateObj.getMonth() + 1} 月 ${next.dateObj.getDate()} 日`);
  summary.textContent = pieces.join('，');
}

function renderTimeline(root, start, selectedISO, byDate, today) {
  const track = root?.querySelector('#earnings-timeline-track');
  const range = root?.querySelector('#earnings-range-label');
  if (!track || !range) return;
  const dates = Array.from({ length: DAYS_IN_TIMELINE }, (_, index) => addDays(start, index));
  range.textContent = formatRange(dates[0], dates[dates.length - 1]);
  range.setAttribute('datetime', `${isoFromDate(dates[0])}/${isoFromDate(dates[dates.length - 1])}`);
  track.setAttribute('aria-busy', 'false');
  track.innerHTML = dates.map((date) => {
    const iso = isoFromDate(date);
    const items = byDate.get(iso) || [];
    const selected = iso === selectedISO;
    const isToday = iso === isoFromDate(today);
    const preview = items.slice(0, 2).map((item) => item.ticker).join(' · ');
    const countLabel = items.length ? `${items.length} 家` : '无事件';
    return `
      <button type="button" class="earnings-day${selected ? ' is-selected' : ''}${isToday ? ' is-today' : ''}"
        data-earnings-date="${iso}" aria-pressed="${selected ? 'true' : 'false'}" tabindex="${selected ? '0' : '-1'}"
        data-motion-card data-motion-key="earnings-day-${iso}"
        aria-label="${escapeHtml(formatFullDate(date))}，${countLabel}">
        <span class="earnings-day__weekday">${isToday ? '今天' : WEEKDAY_LABELS[date.getDay()]}</span>
        <strong class="earnings-day__date">${date.getMonth() + 1}.${String(date.getDate()).padStart(2, '0')}</strong>
        <i class="earnings-day__node" aria-hidden="true"></i>
        <span class="earnings-day__count">${countLabel}</span>
        <small>${escapeHtml(preview || '暂无安排')}</small>
      </button>
    `;
  }).join('');
}

function eventTitle(date, today) {
  return isoFromDate(date) === isoFromDate(today)
    ? `今天 · ${formatFullDate(date)}`
    : formatFullDate(date);
}

function renderEvents(root, dateISO, items, selectedTicker, nextEvent, today) {
  const title = root?.querySelector('#earnings-events-title');
  const eyebrow = root?.querySelector('#earnings-events-eyebrow');
  const count = root?.querySelector('#earnings-events-count');
  const list = root?.querySelector('#earnings-events-list');
  const date = parseISO(dateISO);
  if (!title || !eyebrow || !count || !list || !date) return;

  eyebrow.textContent = isoFromDate(date) === isoFromDate(today) ? '今日事件' : '所选日期';
  title.textContent = eventTitle(date, today);
  count.textContent = `${items.length} 场`;
  list.setAttribute('aria-busy', 'false');

  if (!items.length) {
    const nextAction = nextEvent
      ? `<button type="button" data-jump-date="${escapeHtml(nextEvent.date)}">查看下一场：${escapeHtml(nextEvent.ticker)}，${nextEvent.dateObj.getMonth() + 1} 月 ${nextEvent.dateObj.getDate()} 日</button>`
      : '';
    list.innerHTML = `
      <div class="earnings-empty-state">
        <strong>这一天没有已确认的财报</strong>
        <span>时间轴仍可继续浏览，新的安排会出现在对应日期。</span>
        ${nextAction}
      </div>
    `;
    return;
  }

  const sorted = [...items].sort((a, b) => (
    (Number(b.marketCap) || 0) - (Number(a.marketCap) || 0)
    || a.ticker.localeCompare(b.ticker)
  ));
  list.innerHTML = sorted.map((item, index) => `
    <article class="earnings-event${selectedTicker === item.ticker ? ' is-selected' : ''}"
      role="button" tabindex="0" data-event-row="${escapeHtml(item.ticker)}"
      data-earnings-impact="${escapeHtml(item.ticker)}" data-ticker="${escapeHtml(item.ticker)}"
      data-motion-card data-motion-key="earnings-${escapeHtml(item.date)}-${escapeHtml(item.ticker)}"
      aria-controls="earnings-impact-panel" aria-expanded="${selectedTicker === item.ticker ? 'true' : 'false'}"
      aria-label="打开 ${escapeHtml(item.ticker)} ${escapeHtml(item.name)} 的财报关联影响。行业 ${escapeHtml(item.sector || '待确认')}，每股收益预估 ${escapeHtml(fmtEps(item.epsEstimate))}，营收预估 ${escapeHtml(fmtLargeMoney(item.revenueEstimate))}"
      >
      <span class="earnings-event__index" aria-hidden="true">${String(index + 1).padStart(2, '0')}</span>
      <div class="earnings-event__identity">
        <strong>${escapeHtml(item.ticker)}</strong>
        <h3>${escapeHtml(item.name)}</h3>
        <span>${escapeHtml(item.sector || '行业待确认')}</span>
      </div>
      <dl class="earnings-event__metrics">
        <div><dt>每股收益预估</dt><dd data-numeric>${fmtEps(item.epsEstimate)}</dd></div>
        <div><dt>营收预估</dt><dd data-numeric>${fmtLargeMoney(item.revenueEstimate)}</dd></div>
      </dl>
      <span class="earnings-event__hint" aria-hidden="true">关联研究 <span>↗</span></span>
    </article>
  `).join('');
}

function renderInspectorCompany(item, content) {
  return `
    <div class="earnings-impact__company">
      <div>
        <span>${escapeHtml(item?.sector || '行业待确认')}</span>
        <strong>${escapeHtml(item?.ticker || '')}</strong>
        <p>${escapeHtml(item?.name || '')}</p>
      </div>
      <button type="button" data-detail-ticker="${escapeHtml(item?.ticker || '')}">打开个股详情 ↗</button>
    </div>
    <dl class="earnings-impact__facts">
      <div><dt>财报日期</dt><dd>${escapeHtml(item?.date || '—')}</dd></div>
      <div><dt>每股收益预估</dt><dd data-numeric>${fmtEps(item?.epsEstimate)}</dd></div>
      <div><dt>营收预估</dt><dd data-numeric>${fmtLargeMoney(item?.revenueEstimate)}</dd></div>
    </dl>
    ${content}
  `;
}

function renderImpactResult(item, result) {
  const impacted = Array.isArray(result?.impacted) ? result.impacted : [];
  const groups = new Map();
  impacted.forEach((entry) => {
    const relation = RELATION_ORDER.includes(entry?.relation) ? entry.relation : 'other';
    if (!groups.has(relation)) groups.set(relation, []);
    groups.get(relation).push(entry);
  });

  const groupMarkup = RELATION_ORDER.filter((relation) => groups.has(relation)).map((relation) => {
    const entries = groups.get(relation);
    return `
      <section class="earnings-impact-group" aria-labelledby="impact-${relation}">
        <header><h3 id="impact-${relation}">${relationLabel(relation)}</h3><span>${entries.length} 项</span></header>
        <ul>
          ${entries.map((entry) => {
            const direction = directionMeta(entry?.direction);
            return `
              <li>
                <button type="button" data-detail-ticker="${escapeHtml(entry?.ticker || '')}">
                  <strong>${escapeHtml(entry?.ticker || '—')}</strong>
                  <span>${escapeHtml(entry?.name || entry?.ticker || '')}</span>
                </button>
                <span class="earnings-impact-direction is-${direction.className}">${direction.symbol} ${direction.label}</span>
                <p>${escapeHtml(entry?.reason || '暂无具体说明')}</p>
              </li>
            `;
          }).join('')}
        </ul>
      </section>
    `;
  }).join('');

  const summary = escapeHtml(result?.summary || '暂未返回关联概要。');
  const expectation = result?.expectation
    ? `<p class="earnings-impact-summary__expectation">${escapeHtml(result.expectation)}</p>`
    : '';
  return renderInspectorCompany(item, `
    <section class="earnings-impact-summary" aria-labelledby="earnings-impact-summary-title">
      <span class="earnings-kicker">关联判断</span>
      <h3 id="earnings-impact-summary-title">这场财报可能牵动什么</h3>
      <p>${summary}</p>
      ${expectation}
    </section>
    <div class="earnings-impact-groups">
      ${groupMarkup || '<p class="earnings-impact-empty">暂未识别到显著关联公司。</p>'}
    </div>
    <p class="earnings-impact-note">关联结果只作研究线索，请结合正式财报与行情判断。</p>
  `);
}

function syncSelectedEvent(root, ticker) {
  root?.querySelectorAll('[data-event-row]').forEach((row) => {
    const selected = row.dataset.eventRow === ticker;
    row.classList.toggle('is-selected', selected);
    row.setAttribute('aria-expanded', String(selected));
  });
}

function revealImpact(root) {
  const panel = root?.querySelector('#earnings-impact-panel');
  if (panel) panel.hidden = false;
}

function closeImpact(root) {
  impactLoadToken += 1;
  root?.querySelectorAll('[data-event-row]').forEach((row) => {
    row.classList.remove('is-selected');
    row.setAttribute('aria-expanded', 'false');
  });
  const panel = root?.querySelector('#earnings-impact-panel');
  const body = root?.querySelector('#earnings-impact-body');
  const status = root?.querySelector('#earnings-impact-status');
  if (panel) panel.hidden = true;
  if (status) status.textContent = '';
  if (body) {
    body.setAttribute('aria-busy', 'false');
    body.replaceChildren();
  }
}

function scrollImpactOnSmallScreen(root) {
  if (!window.matchMedia('(max-width: 900px)').matches) return;
  const panel = root?.querySelector('#earnings-impact-panel');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  panel?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
}

async function loadImpact(root, ticker, byTicker, mountGeneration) {
  const body = root?.querySelector('#earnings-impact-body');
  const status = root?.querySelector('#earnings-impact-status');
  const item = byTicker.get(ticker);
  if (!body || !item) return;
  const requestToken = ++impactLoadToken;
  revealImpact(root);
  syncSelectedEvent(root, ticker);
  body.setAttribute('aria-busy', 'true');
  if (status) status.textContent = `正在检查 ${ticker} 的关联影响`;
  body.innerHTML = renderInspectorCompany(item, `
    <div class="earnings-impact-loading">
      <span aria-hidden="true"></span>
      <strong>正在检查关联影响</strong>
      <p>正在整理同行、供应链、客户与基金关系。</p>
    </div>
  `);
  scrollImpactOnSmallScreen(root);

  try {
    const result = await api.earningsImpact(ticker);
    if (requestToken !== impactLoadToken || !isEarningsMounted(mountGeneration) || !body.isConnected) return;
    body.setAttribute('aria-busy', 'false');
    body.innerHTML = renderImpactResult(item, result);
    if (status) status.textContent = `${ticker} 的关联影响已经载入`;
  } catch (error) {
    if (requestToken !== impactLoadToken || !isEarningsMounted(mountGeneration) || !body.isConnected) return;
    body.setAttribute('aria-busy', 'false');
    if (status) status.textContent = `${ticker} 的关联检查没有完成`;
    body.innerHTML = renderInspectorCompany(item, `
      <div class="earnings-impact-error">
        <strong>关联检查没有完成</strong>
        <p>${escapeHtml(error?.message || '服务暂不可用，请稍后重试。')}</p>
        <button type="button" data-impact-retry="${escapeHtml(ticker)}">重新检查关联</button>
      </div>
    `);
  }
}

function tooltipMarkup(item) {
  return `
    <div class="earnings-tooltip__head">
      <strong>${escapeHtml(item.ticker)}</strong>
      <time datetime="${escapeHtml(item.date)}">${escapeHtml(item.date)}</time>
    </div>
    <p>${escapeHtml(item.name)}</p>
    <dl>
      <div><dt>行业</dt><dd>${escapeHtml(item.sector || '—')}</dd></div>
      <div><dt>每股收益预估</dt><dd>${fmtEps(item.epsEstimate)}</dd></div>
      <div><dt>营收预估</dt><dd>${fmtLargeMoney(item.revenueEstimate)}</dd></div>
      <div><dt>市值</dt><dd>${fmtLargeMoney(item.marketCap)}</dd></div>
    </dl>
    <span>点击卡片打开关联研究</span>
  `;
}

function bindTooltip(root, byTicker) {
  const list = root?.querySelector('#earnings-events-list');
  const tooltip = root?.querySelector('#earnings-tooltip');
  if (!list || !tooltip) return;
  if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

  const targetFromEvent = (event) => event.target.closest('.earnings-event[data-ticker]');
  let pointerX = 0;
  let pointerY = 0;
  let placementFrame = 0;
  const show = (card) => {
    const item = card ? byTicker.get(card.dataset.ticker) : null;
    if (!item) return;
    tooltip.innerHTML = tooltipMarkup(item);
    tooltip.hidden = false;
  };
  const hide = () => {
    tooltip.hidden = true;
    if (placementFrame) cancelAnimationFrame(placementFrame);
    placementFrame = 0;
  };
  const placeByPoint = () => {
    placementFrame = 0;
    const rect = tooltip.getBoundingClientRect();
    const gap = 14;
    const left = pointerX + rect.width + gap > window.innerWidth ? pointerX - rect.width - gap : pointerX + gap;
    const top = pointerY + rect.height + gap > window.innerHeight ? pointerY - rect.height - gap : pointerY + gap;
    tooltip.style.transform = `translate3d(${Math.max(8, left)}px, ${Math.max(8, top)}px, 0)`;
  };
  const schedulePlacement = (x, y) => {
    pointerX = x;
    pointerY = y;
    if (!placementFrame) placementFrame = requestAnimationFrame(placeByPoint);
  };

  list.addEventListener('pointerover', (event) => {
    const card = targetFromEvent(event);
    if (card && !card.contains(event.relatedTarget)) {
      show(card);
      schedulePlacement(event.clientX, event.clientY);
    }
  });
  list.addEventListener('pointermove', (event) => {
    if (!tooltip.hidden) schedulePlacement(event.clientX, event.clientY);
  });
  list.addEventListener('pointerout', (event) => {
    const card = targetFromEvent(event);
    if (card && !card.contains(event.relatedTarget)) hide();
  });
}

function renderDataError(root, message) {
  const timeline = root?.querySelector('#earnings-timeline-track');
  const list = root?.querySelector('#earnings-events-list');
  const count = root?.querySelector('#earnings-events-count');
  const summary = root?.querySelector('#earnings-summary');
  if (timeline) {
    timeline.setAttribute('aria-busy', 'false');
    timeline.innerHTML = '<div class="earnings-timeline__error">财报日期暂时不可用</div>';
  }
  if (list) {
    list.setAttribute('aria-busy', 'false');
    list.innerHTML = `
      <div class="earnings-empty-state earnings-empty-state--error">
        <strong>没有取得财报数据</strong>
        <span>${escapeHtml(message)}</span>
        <button type="button" data-earnings-retry>重新读取财报</button>
      </div>
    `;
  }
  if (count) count.textContent = '不可用';
  if (summary) summary.textContent = '财报服务暂时不可用，没有改动你的任何数据。';
}

export async function renderEarnings() {
  const mountGeneration = ++activeEarningsGeneration;
  impactLoadToken += 1;
  try {
    await ensureEarningsStylesheet();
  } catch (error) {
    console.warn('Earnings stylesheet load error:', error);
  }
  if (mountGeneration !== activeEarningsGeneration || window.location.hash.replace(/^#/, '').split('/')[0] !== 'earnings') return;

  renderShell(mountGeneration);
  const root = rootForGeneration(mountGeneration);
  if (!root) return;

  let earnings;
  try {
    const payload = await api.earnings();
    if (!isEarningsMounted(mountGeneration)) return;
    const today = localToday();
    earnings = normalizeEarnings(payload).filter((item) => item.dateObj >= today);
  } catch (error) {
    if (!isEarningsMounted(mountGeneration)) return;
    renderDataError(root, error?.message || '财报接口请求失败，请稍后重试。');
    root.querySelector('[data-earnings-retry]')?.addEventListener('click', () => renderEarnings());
    return;
  }

  const today = localToday();
  const todayISO = isoFromDate(today);
  const byDate = new Map();
  const byTicker = new Map();
  earnings.forEach((item) => {
    if (!byDate.has(item.date)) byDate.set(item.date, []);
    byDate.get(item.date).push(item);
    byTicker.set(item.ticker, item);
  });
  renderHeaderSummary(root, earnings, today);

  let windowStart = today;
  let selectedDate = todayISO;
  let selectedTicker = '';
  const latestDate = earnings.length ? earnings[earnings.length - 1].dateObj : today;

  const nextEventAfter = (dateISO) => earnings.find((item) => item.date > dateISO) || null;
  const firstEventInWindow = (start) => {
    const end = addDays(start, DAYS_IN_TIMELINE - 1);
    return earnings.find((item) => item.dateObj >= start && item.dateObj <= end) || null;
  };
  const updateWeekButtons = () => {
    const previous = root.querySelector('[data-week-action="previous"]');
    const next = root.querySelector('[data-week-action="next"]');
    if (previous) previous.disabled = windowStart <= today;
    if (next) next.disabled = addDays(windowStart, DAYS_IN_TIMELINE - 1) >= latestDate;
  };
  const renderSelectedDay = () => {
    renderEvents(root, selectedDate, byDate.get(selectedDate) || [], selectedTicker, nextEventAfter(selectedDate), today);
  };
  const renderWindow = () => {
    renderTimeline(root, windowStart, selectedDate, byDate, today);
    renderSelectedDay();
    updateWeekButtons();
  };
  const moveToDate = (dateISO, { focus = false } = {}) => {
    const date = parseISO(dateISO);
    if (!date) return;
    if (date < windowStart || date > addDays(windowStart, DAYS_IN_TIMELINE - 1)) {
      windowStart = date < today ? today : date;
    }
    selectedDate = dateISO;
    selectedTicker = '';
    closeImpact(root);
    renderWindow();
    if (focus) root.querySelector(`[data-earnings-date="${dateISO}"]`)?.focus();
  };

  renderWindow();
  bindTooltip(root, byTicker);

  root.querySelector('#earnings-timeline-track')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-earnings-date]');
    if (button) moveToDate(button.dataset.earningsDate);
  });

  root.querySelector('#earnings-timeline-track')?.addEventListener('keydown', (event) => {
    const button = event.target.closest('[data-earnings-date]');
    if (!button || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...root.querySelectorAll('[data-earnings-date]')];
    const index = buttons.indexOf(button);
    if (index < 0) return;
    event.preventDefault();
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
    moveToDate(buttons[nextIndex].dataset.earningsDate, { focus: true });
  });

  root.querySelector('.earnings-week-nav')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-week-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.weekAction;
    if (action === 'today') {
      windowStart = today;
      selectedDate = todayISO;
    } else {
      const direction = action === 'next' ? DAYS_IN_TIMELINE : -DAYS_IN_TIMELINE;
      windowStart = addDays(windowStart, direction);
      if (windowStart < today) windowStart = today;
      selectedDate = firstEventInWindow(windowStart)?.date || isoFromDate(windowStart);
    }
    selectedTicker = '';
    closeImpact(root);
    renderWindow();
  });

  const eventsList = root.querySelector('#earnings-events-list');
  const openEventImpact = (card) => {
    if (!card?.dataset.earningsImpact) return;
    selectedTicker = card.dataset.earningsImpact;
    loadImpact(root, selectedTicker, byTicker, mountGeneration);
  };

  eventsList?.addEventListener('click', (event) => {
    const jump = event.target.closest('[data-jump-date]');
    if (jump) {
      moveToDate(jump.dataset.jumpDate);
      return;
    }
    const card = event.target.closest('.earnings-event[data-earnings-impact]');
    if (!card) return;
    const nestedInteractive = event.target.closest('a, button, input, select, textarea, summary, [role="button"], [role="link"], [contenteditable="true"]');
    if (nestedInteractive && nestedInteractive !== card) return;
    openEventImpact(card);
  });

  eventsList?.addEventListener('keydown', (event) => {
    const card = event.target.closest('.earnings-event[data-earnings-impact]');
    if (!card || event.target !== card || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    openEventImpact(card);
  });

  root.querySelector('#earnings-impact-panel')?.addEventListener('click', (event) => {
    if (event.target.closest('[data-impact-close]')) {
      const returnTarget = root.querySelector('.earnings-event.is-selected');
      selectedTicker = '';
      closeImpact(root);
      returnTarget?.focus();
      return;
    }
    const detail = event.target.closest('[data-detail-ticker]');
    if (detail) {
      navigateToDetail(detail.dataset.detailTicker);
      return;
    }
    const retry = event.target.closest('[data-impact-retry]');
    if (retry) loadImpact(root, retry.dataset.impactRetry, byTicker, mountGeneration);
  });

  if (!earnings.length) {
    const summary = root.querySelector('#earnings-summary');
    if (summary) summary.textContent = '未来时间范围内暂无已确认财报，新的安排会自动补充。';
  }
}
