/**
 * Earnings Calendar page — month grid with ticker chips, hover for details,
 * click to load per-company AI impact analysis (which other stocks will move).
 */
import { api } from '../api.js';
let activeEarningsGeneration = 0;

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[c]);
}

function navigateToDetail(ticker) {
  if (ticker) location.hash = `#detail/${encodeURIComponent(String(ticker).toUpperCase())}`;
}

function fmtLargeMoney(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString()}`;
}
function fmtEps(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `$${n.toFixed(2)}`;
}

function normalizeEarnings(payload) {
  const items = Array.isArray(payload) ? payload : (payload?.earnings ?? []);
  return items.map((it) => ({
    ticker: String(it.ticker || '').toUpperCase(),
    name: it.name || it.company || it.ticker || '',
    date: it.earnings_date || it.date || '',
    sector: it.sector || '',
    epsEstimate: it.eps_estimate ?? null,
    revenueEstimate: it.revenue_estimate ?? null,
    marketCap: it.market_cap ?? null,
  })).filter(it => it.ticker && it.date);
}

// ── Date math (all LOCAL time — avoids UTC drift in UTC+9) ──
function parseISO(s) {
  const [y, m, d] = String(s).split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}
function todayISO() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}
function ymKey(year, month) {  // month is 0-11
  return `${year}-${String(month + 1).padStart(2, '0')}`;
}
function monthLabelFromYM(year, month) {
  return `${year} 年 ${month + 1} 月`;
}
function firstDayOfMonth(year, month) {
  return new Date(year, month, 1);
}
function daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}
function lastDayOfMonth(year, month) {
  return new Date(year, month + 1, 0);
}

// ── Calendar grid ──
function buildCalendar(year, month, byDate) {
  const first = firstDayOfMonth(year, month);
  // We want Mon-first columns (0=Mon..6=Sun). getDay: Sun=0..Sat=6
  const firstWeekday = (first.getDay() + 6) % 7;
  const days = daysInMonth(year, month);
  const todayStr = todayISO();
  const cells = [];
  for (let i = 0; i < firstWeekday; i++) cells.push({ blank: true });
  for (let d = 1; d <= days; d++) {
    const iso = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    cells.push({
      day: d,
      iso,
      items: byDate.get(iso) || [],
      isToday: iso === todayStr,
      isPast: iso < todayStr,
    });
  }
  while (cells.length % 7 !== 0) cells.push({ blank: true });
  return cells;
}

function colorForCount(n) {
  if (n === 0) return 'transparent';
  if (n <= 2) return 'rgba(5,150,105,0.08)';
  if (n <= 5) return 'rgba(5,150,105,0.18)';
  return 'rgba(5,150,105,0.32)';
}

function renderShell(generation) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = `
    <section class="earnings-page" data-earnings-mount="${generation}" aria-labelledby="earnings-title">
      <header class="terminal-header">
        <div>
          <span class="label-caps">财报日历</span>
          <h1 id="earnings-title">即将发布的财报</h1>
          <p>按月份查看财报日历，鼠标悬停查看详情，点击代码分析联动公司。</p>
        </div>
        <div id="earnings-month-nav" style="display:flex;align-items:center;gap:8px"></div>
      </header>

      <section class="earnings-table-card" aria-labelledby="earnings-cal-title">
        <div class="section-card-heading" style="display:flex;justify-content:space-between;align-items:center">
          <div><span class="label-caps">日历</span><h2 id="earnings-cal-title" style="margin:0">财报月历</h2></div>
          <div id="earnings-month-label" class="label-caps" style="font-weight:600"></div>
        </div>
        <div id="earnings-cal" style="padding:16px"></div>
      </section>

      <section id="earnings-impact-panel" class="earnings-table-card" style="margin-top:16px;display:none">
        <div class="section-card-heading"><span class="label-caps">AI 联动分析</span><h2 id="earnings-impact-title" style="margin:0">点击代码查看影响</h2></div>
        <div id="earnings-impact-body" style="padding:20px"></div>
      </section>
    </section>

    <div id="earnings-tooltip" style="position:fixed;display:none;background:#fff;border:1px solid var(--color-border);border-radius:8px;padding:12px 14px;box-shadow:0 8px 24px rgba(0,0,0,0.08);z-index:9999;max-width:280px;pointer-events:none;font-size:12px"></div>
  `;
}

function renderMonthNav(months, currentIdx, onPick, todayIdx) {
  const nav = document.getElementById('earnings-month-nav');
  if (!nav) return;
  const yearSpan = months.length && months[0].year !== months[months.length - 1].year;
  const buttons = months.map((m, i) => {
    const label = yearSpan ? `${String(m.year).slice(-2)}/${m.month + 1}` : `${m.month + 1} 月`;
    const isToday = i === todayIdx;
    return `<button type="button" data-month-idx="${i}"
      class="ethos-timeframe-button ${i === currentIdx ? 'active' : ''}"
      style="min-width:60px;position:relative">${label}${isToday ? '<span style="position:absolute;top:-3px;right:-3px;width:6px;height:6px;background:var(--color-emerald);border-radius:50%"></span>' : ''}</button>`;
  }).join('');
  const todayBtn = todayIdx >= 0 && todayIdx !== currentIdx
    ? `<button type="button" data-go-today
        class="ethos-timeframe-button"
        style="min-width:60px;background:var(--color-emerald);color:#fff;border-color:var(--color-emerald)">今天</button>`
    : '';
  nav.innerHTML = buttons + todayBtn;
  nav.querySelectorAll('[data-month-idx]').forEach(b => {
    b.addEventListener('click', () => onPick(Number(b.dataset.monthIdx)));
  });
  const goToday = nav.querySelector('[data-go-today]');
  if (goToday) goToday.addEventListener('click', () => onPick(todayIdx));
}

function renderCalendar(year, month, byDate) {
  const cal = document.getElementById('earnings-cal');
  const label = document.getElementById('earnings-month-label');
  if (!cal || !label) return;
  label.textContent = monthLabelFromYM(year, month);

  const cells = buildCalendar(year, month, byDate);
  const weekHeader = ['一', '二', '三', '四', '五', '六', '日']
    .map(w => `<div style="padding:8px 4px;text-align:center;font-size:11px;color:var(--color-muted);font-weight:600;letter-spacing:0.05em;text-transform:uppercase">周${w}</div>`)
    .join('');

  const cellsHtml = cells.map(c => {
    if (c.blank) {
      return `<div style="background:#fafaf8;min-height:96px;border-radius:6px"></div>`;
    }
    const bg = colorForCount(c.items.length);
    const opacity = c.isPast ? '0.55' : '1';
    const border = c.isToday
      ? '2px solid #000'
      : '1px solid var(--color-border)';
    const dayLabel = c.isToday
      ? `<span style="background:#000;color:#fff;padding:1px 6px;border-radius:10px;font-size:11px">${c.day} · 今天</span>`
      : `<span style="color:var(--color-on-surface)">${c.day}</span>`;
    const chips = c.items.slice(0, 4).map(it => `
      <button type="button"
        class="mono earnings-chip"
        data-ticker="${escapeHtml(it.ticker)}"
        data-iso="${escapeHtml(it.iso)}"
        style="display:inline-block;background:#fff;border:1px solid var(--color-border);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;margin:1px 2px 1px 0">
        ${escapeHtml(it.ticker)}
      </button>
    `).join('');
    const more = c.items.length > 4
      ? `<div style="font-size:10px;color:var(--color-muted);margin-top:2px">+${c.items.length - 4} 家</div>`
      : '';
    return `
      <div data-day-iso="${escapeHtml(c.iso)}" ${c.isToday ? 'data-today="1"' : ''}
        style="background:${bg};min-height:96px;border-radius:6px;padding:6px 8px;border:${border};display:flex;flex-direction:column;opacity:${opacity}">
        <div style="font-size:12px;font-weight:700;margin-bottom:4px">${dayLabel}</div>
        <div style="flex:1;display:flex;flex-wrap:wrap;align-content:flex-start">${chips}${more}</div>
      </div>
    `;
  }).join('');

  cal.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;margin-bottom:4px">
      ${weekHeader}
    </div>
    <div style="display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px">
      ${cellsHtml}
    </div>
  `;
}

function bindHover(byTicker) {
  const tip = document.getElementById('earnings-tooltip');
  if (!tip) return;
  const cal = document.getElementById('earnings-cal');
  cal.addEventListener('mouseover', (e) => {
    const btn = e.target.closest('.earnings-chip');
    if (!btn) return;
    const t = btn.dataset.ticker;
    const item = byTicker.get(t);
    if (!item) return;
    tip.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:8px">
        <strong class="mono" style="font-size:14px">${escapeHtml(t)}</strong>
        <span style="color:var(--color-muted)">${escapeHtml(item.date)}</span>
      </div>
      <div style="font-weight:600;margin-bottom:6px;color:var(--color-on-surface)">${escapeHtml(item.name)}</div>
      <div style="display:grid;grid-template-columns:auto 1fr;gap:4px 12px;color:var(--color-muted);font-size:11px">
        <span>行业</span><span style="color:var(--color-on-surface)">${escapeHtml(item.sector || '—')}</span>
        <span>EPS 预估</span><span class="mono" style="color:var(--color-on-surface)">${fmtEps(item.epsEstimate)}</span>
        <span>营收预估</span><span class="mono" style="color:var(--color-on-surface)">${fmtLargeMoney(item.revenueEstimate)}</span>
        <span>市值</span><span class="mono" style="color:var(--color-on-surface)">${fmtLargeMoney(item.marketCap)}</span>
      </div>
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--color-border);font-size:11px;color:var(--color-emerald);font-weight:600">点击查看 AI 联动分析 →</div>
    `;
    tip.style.display = 'block';
  });
  cal.addEventListener('mousemove', (e) => {
    if (tip.style.display === 'none') return;
    const pad = 14;
    const rect = tip.getBoundingClientRect();
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    if (x + rect.width > window.innerWidth - 8) x = e.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = e.clientY - rect.height - pad;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  });
  cal.addEventListener('mouseout', (e) => {
    if (!e.target.closest('.earnings-chip')) return;
    tip.style.display = 'none';
  });
}

function relationLabel(rel) {
  return {
    competitor: '同行竞争',
    supplier: '上游供应',
    customer: '下游客户',
    etf: '生态联动',
    opposing: '对手交易',
  }[rel] || rel || '关联';
}

function directionBadge(dir) {
  if (dir === 'bullish') return `<span style="color:var(--color-emerald);font-weight:700">↑ 利好</span>`;
  if (dir === 'bearish') return `<span style="color:var(--color-crimson);font-weight:700">↓ 利空</span>`;
  return `<span style="color:var(--color-muted);font-weight:700">~ 中性</span>`;
}

function renderImpactCard(ticker, item, result) {
  const summary = escapeHtml(result.summary || '');
  const expectation = result.expectation ? `<div style="margin-top:6px;color:var(--color-muted);font-size:12px">${escapeHtml(result.expectation)}</div>` : '';
  const impacted = Array.isArray(result.impacted) ? result.impacted : [];

  const groups = impacted.reduce((acc, x) => {
    const r = x.relation || 'other';
    if (!acc[r]) acc[r] = [];
    acc[r].push(x);
    return acc;
  }, {});

  const groupsHtml = Object.keys(groups).map(rel => `
    <div style="margin-bottom:14px">
      <div class="label-caps" style="font-size:11px;color:var(--color-muted);margin-bottom:6px">${escapeHtml(relationLabel(rel))}</div>
      <div style="display:flex;flex-direction:column;gap:6px">
        ${groups[rel].map(x => `
          <div style="display:flex;gap:10px;align-items:start;padding:8px 10px;background:var(--color-surface);border:1px solid var(--color-border);border-radius:6px">
            <button type="button" data-ticker="${escapeHtml(x.ticker)}"
              class="mono"
              style="background:#000;color:#fff;border:0;padding:3px 7px;border-radius:4px;font-weight:700;font-size:12px;cursor:pointer;flex-shrink:0;min-width:54px;text-align:center">
              ${escapeHtml(x.ticker)}
            </button>
            <div style="flex:1;min-width:0">
              <div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:2px">
                <strong style="font-size:13px">${escapeHtml(x.name || x.ticker)}</strong>
                ${directionBadge(x.direction)}
              </div>
              <div style="font-size:12px;color:var(--color-muted);line-height:1.5">${escapeHtml(x.reason || '')}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');

  return `
    <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:14px;gap:16px">
      <div>
        <div style="display:flex;gap:10px;align-items:baseline;margin-bottom:4px">
          <span class="mono" style="font-size:18px;font-weight:800">${escapeHtml(ticker)}</span>
          <span style="font-size:14px;color:var(--color-on-surface)">${escapeHtml(item?.name || '')}</span>
        </div>
        <p style="margin:0;font-size:13px;color:var(--color-on-surface);line-height:1.55">${summary}</p>
        ${expectation}
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div class="label-caps" style="font-size:11px;color:var(--color-muted)">财报日期</div>
        <div class="mono" style="font-weight:700">${escapeHtml(item?.date || '—')}</div>
      </div>
    </div>
    <hr style="border:0;border-top:1px solid var(--color-border);margin:14px 0">
    <div class="label-caps" style="font-size:11px;color:var(--color-muted);margin-bottom:10px">受影响的相关公司 (${impacted.length})</div>
    ${groupsHtml || '<div style="color:var(--color-muted);font-size:12px">AI 未识别到显著联动公司</div>'}
  `;
}

let __impactLoadToken = 0;
async function loadImpact(ticker, byTicker, mountGeneration) {
  const panel = document.getElementById('earnings-impact-panel');
  const title = document.getElementById('earnings-impact-title');
  const body = document.getElementById('earnings-impact-body');
  if (!panel || !body) return;
  const myToken = ++__impactLoadToken;
  panel.style.display = 'block';
  title.textContent = `${ticker} · AI 联动分析`;
  body.innerHTML = `<div style="padding:32px;text-align:center;color:var(--color-muted)"><div style="width:24px;height:24px;border:2px solid #000;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 12px"></div>AI 分析联动公司中…（约 10-20 秒）</div>`;
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  try {
    const result = await api.earningsImpact(ticker);
    if (myToken !== __impactLoadToken || mountGeneration !== activeEarningsGeneration || !body.isConnected) return;
    body.innerHTML = renderImpactCard(ticker, byTicker.get(ticker), result);
    body.querySelectorAll('[data-ticker]').forEach(b => {
      b.addEventListener('click', () => navigateToDetail(b.dataset.ticker));
    });
  } catch (e) {
    if (myToken !== __impactLoadToken || mountGeneration !== activeEarningsGeneration || !body.isConnected) return;
    body.innerHTML = `<div style="padding:24px;text-align:center;color:var(--color-crimson)">AI 分析失败: ${escapeHtml(e.message || '未知错误')}</div>`;
  }
}

export async function renderEarnings() {
  const mountGeneration = ++activeEarningsGeneration;
  __impactLoadToken += 1;
  renderShell(mountGeneration);
  const cal = document.getElementById('earnings-cal');
  cal.innerHTML = '<div style="padding:48px;text-align:center;color:var(--color-muted)">加载财报日历中…</div>';

  let earnings;
  try {
    const payload = await api.earnings();
    if (mountGeneration !== activeEarningsGeneration || !cal.isConnected) return;
    earnings = normalizeEarnings(payload);
    if (!earnings.length) throw new Error('no earnings');
  } catch (e) {
    if (mountGeneration !== activeEarningsGeneration || !cal.isConnected) return;
    cal.innerHTML = `<div style="padding:48px;text-align:center;color:var(--color-muted)"><strong style="display:block;margin-bottom:6px;color:var(--color-crimson)">数据暂不可用</strong>财报 API 请求失败 <button type="button" data-earnings-retry>重试</button></div>`;
    cal.querySelector('[data-earnings-retry]')?.addEventListener('click', () => renderEarnings());
    return;
  }

  // Group by date & ticker
  const byDate = new Map();
  const byTicker = new Map();
  earnings.forEach(e => {
    if (!byDate.has(e.date)) byDate.set(e.date, []);
    byDate.get(e.date).push(e);
    byTicker.set(e.ticker, e);
  });

  // ── Find months in data + ensure current month is included ──
  const today = new Date();
  const todayYM = today.getFullYear() * 100 + today.getMonth();
  const monthSet = new Map();
  // Always include current month so the "今天" button has a target
  monthSet.set(ymKey(today.getFullYear(), today.getMonth()), {
    year: today.getFullYear(), month: today.getMonth(),
  });
  earnings.forEach(e => {
    const d = parseISO(e.date);
    if (!d) return;
    const ym = d.getFullYear() * 100 + d.getMonth();
    // Skip fully-past months (their last day is before today)
    if (ym < todayYM) return;
    const key = ymKey(d.getFullYear(), d.getMonth());
    if (!monthSet.has(key)) {
      monthSet.set(key, { year: d.getFullYear(), month: d.getMonth() });
    }
  });
  const months = Array.from(monthSet.values()).sort((a, b) =>
    (a.year - b.year) * 100 + (a.month - b.month)
  );

  // todayIdx = position of current month in the button list
  const todayIdx = months.findIndex(m =>
    m.year === today.getFullYear() && m.month === today.getMonth()
  );
  let currentIdx = todayIdx >= 0 ? todayIdx : 0;

  const pick = (idx) => {
    if (idx < 0 || idx >= months.length) return;
    currentIdx = idx;
    renderMonthNav(months, currentIdx, pick, todayIdx);
    renderCalendar(months[idx].year, months[idx].month, byDate);
    cal.querySelectorAll('.earnings-chip').forEach(btn => {
      btn.addEventListener('click', () => loadImpact(btn.dataset.ticker, byTicker, mountGeneration));
    });
    // Scroll today into view if visible
    if (idx === todayIdx) {
      const todayCell = cal.querySelector('[data-today="1"]');
      todayCell?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  };

  renderMonthNav(months, currentIdx, pick, todayIdx);
  renderCalendar(months[currentIdx].year, months[currentIdx].month, byDate);
  cal.querySelectorAll('.earnings-chip').forEach(btn => {
    btn.addEventListener('click', () => loadImpact(btn.dataset.ticker, byTicker, mountGeneration));
  });
  bindHover(byTicker);
}
