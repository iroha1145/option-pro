/** Trusted local report template. Model text is always text, never markup or code. */
import type { OpenGenUIContent } from '../open-intelligent-ui/schema.ts';
import type { StockReport, StockReportVisuals, StockReportChart } from './stockAnalysisTypes.ts';
import { t, localeTag } from '../../i18n/core.ts';
const rec = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown, limit = 12000) => typeof value === 'string' ? value.trim().slice(0, limit) : '';
const strings = (value: unknown, max = 30) => Array.isArray(value) ? value.slice(0, max).map(v => text(v)).filter(Boolean) : [];
export const escapeReportText = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const esc = escapeReportText;
const SECTION_IDS = new Set(['company', 'financials', 'valuation', 'technical', 'options', 'events', 'market']);
export function parseStockReport(result: unknown): StockReport | null {
  const raw = rec(rec(result).report);
  if (raw.version !== 'stock-report-v1' || !text(raw.headline) || !Array.isArray(raw.sections)) return null;
  const seen = new Set<string>();
  const sections = raw.sections.slice(0, 7).flatMap(value => {
    const row = rec(value), id = text(row.id);
    if (!SECTION_IDS.has(id) || seen.has(id) || !text(row.title) || !text(row.summary)) return [];
    seen.add(id);
    return [{ id, title: text(row.title), summary: text(row.summary), points: strings(row.points), evidenceIds: strings(row.evidence_ids) }];
  });
  if (!sections.length) return null;
  return { headline: text(raw.headline), sections,
    watchItems: Array.isArray(raw.watch_items) ? raw.watch_items.slice(0, 20).flatMap(value => {
      const row = rec(value); return text(row.what) ? [{ what: text(row.what), why: text(row.why), reviseIf: text(row.revise_if) }] : [];
    }) : [], dataGaps: strings(raw.data_gaps) };
}
const UNITS = new Set(['usd', 'percent', 'score', 'shares', 'ratio', 'count']);
const stamp = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
export function parseStockVisuals(value: unknown): StockReportVisuals | null {
  const raw = rec(value);
  if (raw.version !== 'stock-report-visuals-v1' || !stamp(raw.as_of)) return null;
  const seen = new Set<string>();
  const charts: StockReportChart[] = Array.isArray(raw.charts) ? raw.charts.slice(0, 6).flatMap(value => {
    const row = rec(value), id = text(row.id, 80);
    if (!id || seen.has(id) || !text(row.title) || !text(row.caption) || (row.kind !== 'line' && row.kind !== 'bar') || !UNITS.has(String(row.unit)) || !Array.isArray(row.rows)) return [];
    // Structure scores have an absolute 0–100 scale, never a sample-relative scale.
    if (row.unit === 'score' && row.rows.some(value => {
      const score = rec(value).value;
      return typeof score === 'number' && (!Number.isFinite(score) || score < 0 || score > 100);
    })) return [];
    const rows = row.rows.slice(0, row.kind === 'line' ? 250 : 16).flatMap(value => {
      const item = rec(value);
      if (!text(item.label, 100) || typeof item.value !== 'number' || !Number.isFinite(item.value) || Math.abs(item.value) > 1e15) return [];
      return [{ label: text(item.label, 100), value: item.value, evidenceId: text(item.evidence_id, 100) || undefined }];
    });
    if (!rows.length || (row.kind === 'line' && rows.length < 2)) return [];
    seen.add(id);
    return [{ id, title: text(row.title), caption: text(row.caption), kind: row.kind, unit: row.unit as StockReportChart['unit'], rows }];
  }) : [];
  return { asOf: raw.as_of as string, charts, coverage: Array.isArray(raw.coverage) ? raw.coverage.slice(0, 24).flatMap(value => {
    const row = rec(value);
    return text(row.label) && text(row.status) ? [{ key: text(row.key), label: text(row.label), status: text(row.status), asOf: stamp(row.as_of) ? row.as_of as string : undefined }] : [];
  }) : [], notes: strings(raw.notes) };
}
export function coverageStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    available: t('可用'), ok: t('可用'), missing: t('缺失'), stale: t('已过期'),
    partial: t('部分资料'), unavailable: t('暂无资料'), timeout: t('资料读取超时'),
    not_applicable: t('不适用'), omitted_size: t('因资料过长已省略'),
  };
  return Object.hasOwn(labels, status) ? labels[status] : t('状态未知');
}
const list = (values: string[]) => values.length ? `<ul>${values.map(value => `<li>${esc(value)}</li>`).join('')}</ul>` : '';
const section = (title: string, body: string) => body ? `<section><h4>${esc(title)}</h4>${body}</section>` : '';
const number = (value: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value);
const chartNumber = (value: number) => Math.abs(value) >= 1_000_000
  ? new Intl.NumberFormat(localeTag(), { notation: 'compact', maximumFractionDigits: 2 }).format(value)
  : number(value);
const unitLabel = (unit: StockReportChart['unit']) => ({ usd: t('美元'), percent: '%', score: t('分'), shares: t('股'), ratio: t('比例'), count: t('数量') })[unit];
/** Source timestamps only; report prose and date-only reporting periods stay intact. */
export function evidenceTimeLabel(value: string): string {
  return value.replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})/g, stamp => {
    const date = new Date(stamp);
    if (!Number.isFinite(date.getTime())) return stamp;
    return `${new Intl.DateTimeFormat(localeTag(), { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date)} ${t('纽约时间')}`;
  });
}
type ChartTone = 'price' | 'revenue' | 'volume' | 'estimate' | 'neutral' | 'positive' | 'caution' | 'negative';
function chartTone(chart: StockReportChart): ChartTone {
  switch (chart.id) {
    case 'price_history': return 'price';
    case 'quarterly_revenue': return 'revenue';
    case 'daily_volume': return 'volume';
    case 'analyst_targets': return 'estimate';
    default: return 'neutral';
  }
}
function rowAppearance(chart: StockReportChart, row: StockReportChart['rows'][number]): { tone: ChartTone; meaning?: string } {
  // Match saved evidence IDs, never translated labels. These bands follow the
  // existing scoring service; price, revenue and volume have no good/bad bands.
  if (chart.id === 'structure_scores' && chart.unit === 'score') {
    const value = row.value;
    switch (row.evidenceId) {
      case 'scores.top_score':
        if (value < 30) return { tone: 'positive', meaning: t('顶部风险低') };
        if (value < 50) return { tone: 'caution', meaning: t('正常震荡风险') };
        if (value < 65) return { tone: 'caution', meaning: t('需要停止追高') };
        if (value < 80) return { tone: 'negative', meaning: t('阶段性顶部风险高') };
        return { tone: 'negative', meaning: t('极端过热，等待反转确认') };
      case 'scores.bottom_score':
        if (value < 30) return { tone: 'neutral', meaning: t('没有底部迹象') };
        if (value < 50) return { tone: 'caution', meaning: t('可能只是超跌') };
        if (value < 65) return { tone: 'caution', meaning: t('开始出现底部条件') };
        if (value < 80) return { tone: 'positive', meaning: t('阶段性底部概率较高') };
        return { tone: 'caution', meaning: t('恐慌释放充分，仍需价格确认') };
      case 'scores.dip_buy_quality':
        if (value < 30) return { tone: 'negative', meaning: t('回调买点弱') };
        if (value < 50) return { tone: 'caution', meaning: t('回调承接一般') };
        if (value < 70) return { tone: 'caution', meaning: t('趋势回调买点中等') };
        return { tone: 'positive', meaning: t('高质量趋势回调') };
      case 'scores.data_quality':
        return { tone: 'price', meaning: t('综合资料质量') };
    }
  }
  if (chart.id === 'earnings_surprise' && chart.unit === 'ratio') {
    if (row.value > 0) return { tone: 'positive', meaning: t('超出预期') };
    if (row.value < 0) return { tone: 'negative', meaning: t('低于预期') };
    return { tone: 'neutral', meaning: t('符合预期') };
  }
  return { tone: chartTone(chart) };
}
function chartHtml(chart: StockReportChart): string {
  const values = chart.rows.map(row => row.value);
  const low = chart.unit === 'score' ? 0 : Math.min(0, ...values);
  const high = chart.unit === 'score' ? 100 : Math.max(0, ...values);
  const span = high - low || 1;
  const table = `<details><summary>${esc(t('查看明细'))}</summary><div class="table-wrap"><table><thead><tr><th>${esc(t('项目'))}</th><th>${esc(unitLabel(chart.unit))}</th></tr></thead><tbody>${chart.rows.map(row => `<tr><td>${esc(row.label)}</td><td>${esc(number(row.value))}</td></tr>`).join('')}</tbody></table></div></details>`;
  let picture: string;
  if (chart.kind === 'line') {
    const min = chart.unit === 'score' ? 0 : Math.min(...values);
    const max = chart.unit === 'score' ? 100 : Math.max(...values);
    const range = max - min || 1;
    const points = chart.rows.map((row, index) => `${(30 + index * 540 / (chart.rows.length - 1)).toFixed(2)},${(150 - (row.value - min) * 120 / range).toFixed(2)}`).join(' ');
    picture = `<svg viewBox="0 0 600 180" role="img" aria-label="${esc(chart.title)}"><path d="M30 25V150H570" class="axis"/><polyline points="${points}" class="series"/><text x="30" y="173">${esc(chart.rows[0].label)}</text><text x="570" y="173" text-anchor="end">${esc(chart.rows.at(-1)!.label)}</text><text x="30" y="18">${esc(chartNumber(max))}</text><text x="570" y="18" text-anchor="end">${esc(unitLabel(chart.unit))}</text></svg>`;
  } else {
    picture = `<div class="bars">${chart.rows.map(row => {
      const zero = (0 - low) / span * 100, end = (row.value - low) / span * 100;
      const appearance = rowAppearance(chart, row);
      return `<div class="bar-row tone-${appearance.tone}${appearance.meaning ? ' has-meaning' : ''}"><span class="bar-label">${esc(row.label)}${appearance.meaning ? `<small>${esc(appearance.meaning)}</small>` : ''}</span><div class="track" aria-hidden="true"><i style="left:${Math.min(zero, end).toFixed(3)}%;width:${Math.abs(end - zero).toFixed(3)}%"></i></div><b>${esc(chartNumber(row.value))}</b></div>`;
    }).join('')}</div>`;
  }
  return `<figure class="tone-${chartTone(chart)}"><figcaption><strong>${esc(chart.title)} · ${esc(unitLabel(chart.unit))}</strong><span>${esc(evidenceTimeLabel(chart.caption))}</span></figcaption>${picture}${table}</figure>`;
}
/** Old persisted reports remain readable; display their actual fields, without invented charts. */
function legacyBody(result: unknown): string {
  const raw = rec(result);
  const blocks: [string, unknown][] = [
    [t('主要判断'), [raw.horizon, raw.dominant_regime].filter(v => typeof v === 'string')],
    [t('顶部风险依据'), raw.top_evidence], [t('底部机会依据'), raw.bottom_evidence],
    [t('回调机会依据'), raw.dip_buy_evidence], [t('下跌风险依据'), raw.bearish_evidence],
    [t('相反证据'), raw.contradictions], [t('确认条件'), raw.confirmation_signals],
    [t('失效条件'), raw.invalidation_signals], [t('事件风险'), raw.event_risks], [t('资料缺口'), raw.data_quality_notes],
  ];
  const levels = rec(raw.key_levels), flow = rec(raw.options_flow_read);
  blocks.push([t('支撑位'), levels.support], [t('阻力位'), levels.resistance], [t('成交均价依据'), levels.vwap_levels],
    [t('期权关键位置'), levels.options_levels], [t('期权看涨依据'), flow.bullish_flow_evidence],
    [t('期权看跌依据'), flow.bearish_flow_evidence], [t('期权方向未明'), flow.unknown_or_neutral_flow], [t('期权资料限制'), flow.warnings]);
  const scores: [string, unknown][] = [
    [t('趋势判断置信度'), raw.trend_bias_confidence], [t('顶部风险置信度'), raw.top_risk_confidence],
    [t('底部机会置信度'), raw.bottom_opportunity_confidence], [t('回调机会质量'), raw.dip_buy_quality],
    [t('跌破风险'), raw.breakdown_risk], [t('资料质量'), raw.data_quality], [t('期权判断置信度'), flow.confidence],
  ];
  const scoreList = scores.flatMap(([label, value]) => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? [`${label}: ${value}/100`] : []);
  const bias: Record<string, string> = {
    bullish_continuation: t('上涨延续'), healthy_rotation: t('良性轮动'), trend_pullback: t('趋势回调'),
    range_consolidation: t('区间整理'), tactical_top_risk: t('阶段顶部风险'), dip_buy_setup: t('回调机会'),
    capitulation_bottom_setup: t('抛售后的底部机会'), bearish_breakdown: t('向下破位'), insufficient_data: t('资料不足'),
  };
  const direction: Record<string, string> = { bullish: t('偏多'), bearish: t('偏空'), mixed: t('方向混合'), unknown: t('方向未知') };
  const judgment = [bias[text(raw.final_bias)], direction[text(flow.net_direction)]].filter(Boolean);
  return section(t('技术判断与评分'), list([...judgment, ...scoreList])) + blocks.map(([title, values]) => section(title, list(strings(values)))).join('');
}
export function createStockAnalysisReport(result: unknown, rawVisuals: unknown): OpenGenUIContent | null {
  const report = parseStockReport(result), visuals = parseStockVisuals(rawVisuals);
  const summary = typeof result === 'string' ? text(result) : text(rec(result).summary);
  if (!report && !summary) return null;
  const fold = (title: string, body: string) => body ? `<details class="report-section"><summary>${esc(title)}</summary><div class="section-body">${body}</div></details>` : '';
  const lead = report
    ? `<h3>${esc(report.headline)}</h3>${summary ? `<p>${esc(summary)}</p>` : ''}`
    : `<p>${esc(summary)}</p>${legacyBody(result)}`;
  const chapters = report ? report.sections.map(row => fold(row.title,
    `<p>${esc(row.summary)}</p>${list(row.points)}${row.evidenceIds.length ? `<details><summary>${esc(t('证据编号'))}</summary>${list(row.evidenceIds)}</details>` : ''}`,
  )).join('') : '';
  const watch = report ? fold(t('后续观察与反证'), report.watchItems.map(item => `<div class="watch"><strong>${esc(item.what)}</strong>${item.why ? `<p>${esc(item.why)}</p>` : ''}${item.reviseIf ? `<p>${esc(t('改判条件'))}: ${esc(item.reviseIf)}</p>` : ''}</div>`).join('')) : '';
  const gaps = report ? fold(t('资料缺口'), list(report.dataGaps)) : '';
  const technical = report ? `<details class="technical-detail"><summary>${esc(t('技术依据与关键位置'))}</summary>${legacyBody(result)}</details>` : '';
  const charts = visuals?.charts.slice(0, 2).map(chartHtml).join('') ?? '';
  const moreCharts = fold(t('更多图表'), visuals?.charts.slice(2).map(chartHtml).join('') ?? '');
  const coverage = visuals ? fold(t('资料时效与覆盖'), `<p class="muted">${esc(t('证据截至 {time}', { time: evidenceTimeLabel(visuals.asOf) }))}</p>${list(visuals.coverage.map(row => `${row.label} · ${coverageStatusLabel(row.status)}${row.asOf ? ` · ${evidenceTimeLabel(row.asOf)}` : ''}`))}${list(visuals.notes)}`) : '';
  return { generating: false, htmlComplete: true, cssComplete: true, jsFunctionsComplete: true, jsExpressionsComplete: true,
    html: [`<article class="stock-report">${lead}${charts}${moreCharts}${chapters}${watch}${gaps}${technical}${coverage}</article>`], css: STOCK_REPORT_CSS, jsFunctions: '', jsExpressions: [] };
}
export const STOCK_REPORT_CSS = `
.tone-price{--chart-color:var(--cat-sky-600)}.tone-revenue{--chart-color:var(--ai-600)}.tone-volume{--chart-color:var(--cat-amber-600)}.tone-estimate{--chart-color:var(--cat-orange-600)}.tone-neutral{--chart-color:var(--ink-400)}.tone-positive{--chart-color:var(--ok-600)}.tone-caution{--chart-color:var(--warn-600)}.tone-negative{--chart-color:var(--danger-600)}
figcaption strong{display:flex;align-items:center;gap:7px}figcaption strong:before{content:"";width:8px;height:8px;border-radius:50%;background:var(--chart-color);flex-shrink:0}.bar-row.has-meaning{grid-template-columns:minmax(0,1fr) auto;row-gap:3px;margin:12px 0}.bar-label{min-width:0;overflow-wrap:anywhere}.has-meaning .bar-label{grid-column:1/-1;display:flex;justify-content:space-between;flex-wrap:wrap;gap:2px 10px}.bar-label small{font-size:11px;color:var(--ink-500)}.has-meaning .track{grid-column:1}.has-meaning b{grid-column:2}
:host{display:block;min-width:0;color:var(--ink-800);font:inherit}.stock-report{font-size:14px;line-height:1.7;overflow-wrap:anywhere}h3{font-size:16px;margin:0 0 12px}h4{font-size:14px;margin:0 0 6px;color:var(--ink-900)}p{margin:6px 0;white-space:pre-wrap}section{margin:18px 0 0;padding-top:12px;border-top:1px solid var(--line)}ul{margin:8px 0;padding-left:20px}li{margin:5px 0;white-space:pre-wrap}.muted,figcaption span{color:var(--ink-500);font-size:12px}.watch{margin:12px 0}figure{min-width:0;margin:16px 0;padding:14px;border:1px solid var(--line);border-radius:18px;background:var(--card)}figcaption{display:flex;flex-direction:column;gap:3px}svg{width:100%;height:auto;display:block;margin:10px 0;color:var(--ink-500)}svg text{fill:currentColor;font-size:11px}.axis{fill:none;stroke:var(--line-strong)}.series{fill:none;stroke:var(--chart-color);stroke-width:2.5;stroke-linejoin:round}.bars{margin:12px 0}.bar-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(40px,1.2fr) minmax(64px,.7fr);gap:8px;align-items:center;font-size:12px;margin:7px 0}.bar-row b{white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums;font-weight:500}.track{height:8px;background:var(--paper-2);border-radius:5px;position:relative}.track i{height:8px;background:var(--chart-color);position:absolute;border-radius:5px}details{margin:10px 0;font-size:12px}summary{cursor:pointer;color:var(--ink-600);min-height:32px;align-content:center}summary:focus-visible{outline:2px solid var(--ai-600);outline-offset:3px;border-radius:5px}.report-section{margin:0;border-top:1px solid var(--line);font-size:14px}.report-section>summary{min-height:44px;padding:6px 0;font-weight:600;color:var(--ink-800)}.section-body{padding-bottom:12px}.table-wrap{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;margin-top:8px}th,td{text-align:left;padding:5px;border-bottom:1px solid var(--line)}td:last-child,th:last-child{text-align:right;font-variant-numeric:tabular-nums}
`;
