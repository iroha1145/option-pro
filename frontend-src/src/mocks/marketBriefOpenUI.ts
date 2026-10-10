import type { MarketBrief } from '../api/modules/marketBrief';
import type { OpenGenUIContent } from '../components/open-intelligent-ui/schema';

export interface ReportChartRow { label: string; value: number; evidenceId?: string }
export interface ReportChart {
  title: string;
  caption: string;
  unit: 'percent' | 'percentage_points' | 'score';
  rows: ReportChartRow[];
}
export interface ReportVisuals {
  source: 'demo' | 'server';
  sourceLabel: string;
  market: ReportChart | null;
  sectors: ReportChart | null;
  macro: ReportChart | null;
  breadth: { above: number; total: number; expected: number; asOf: string } | null;
  notes: string[];
}

/** Numerical datasets are supplied separately: never guess chart values from model prose. */
const escapeHtml = (value: string | null | undefined): string => (value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const NEW_YORK_TIME = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
function sourceTimeText(value: string): string {
  return value.replace(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))(?:（协调世界时）)?/g, (original, stamp: string) => {
    const date = new Date(stamp);
    return Number.isFinite(date.getTime()) ? `${NEW_YORK_TIME.format(date)}（纽约时间）` : original;
  });
}

const demoRows = (rows: [string, number][]): ReportChartRow[] => rows.map(([label, value]) => ({ label, value }));
const DEMO_VISUALS: ReportVisuals = {
  source: 'demo',
  sourceLabel: '演示输出 · 图表数值与事件均为模拟数据',
  market: {
    title: '美股指数涨跌', caption: '演示数据 · 当日收盘涨跌幅 · 2026-10-08', unit: 'percent',
    rows: demoRows([['标普500', 0.6], ['纳斯达克', 0.9], ['道琼斯', 0]]),
  },
  sectors: {
    title: '主题一个月超额收益', caption: '演示数据 · 相对标普500的收益差 · 单位：百分点 · 2026-10-08', unit: 'percentage_points',
    rows: demoRows([['半导体', 8.4], ['软件', 4.2], ['工业', 1.6], ['医疗', -0.8], ['消费', -2.1], ['能源', -3.7]]),
  },
  macro: {
    title: '宏观分项', caption: '演示数据 · 0–100分，越高越支持风险资产 · 数据截至2026-10-08', unit: 'score',
    rows: demoRows([['流动性', 72], ['信用', 64], ['增长', 55], ['美元', 48], ['利率', 41]]),
  },
  breadth: { above: 6, total: 11, expected: 11, asOf: '2026-10-08' },
  notes: ['行业隐含波动率数据缺失，本报告未提供对应图表。'],
};

const INDEX_LABELS: Record<string, string> = {
  'idx:^GSPC': '标普500',
  'idx:^IXIC': '纳指综合',
  'idx:^DJI': '道琼斯',
  'idx:^RUT': '罗素2000',
};

function axisLimit(values: number[]): number {
  const max = Math.max(0, ...values.map(Math.abs));
  if (!max) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  return ([1, 2, 5, 10].find(step => step * magnitude >= max) ?? 10) * magnitude;
}

function chart(data: ReportChart | null): string {
  if (!data) return '';
  const rows = data.rows.filter(row => Number.isFinite(row.value));
  if (!rows.length) return '';
  const signed = data.unit !== 'score';
  const scale = signed ? axisLimit(rows.map(row => row.value)) : 100;
  const unit = data.unit === 'score' ? '分' : data.unit === 'percent' ? '%' : '';
  const display = (value: number) => `${signed && value > 0 ? '+' : ''}${value.toFixed(signed ? 2 : 1)}${unit}`;
  const bars = rows.map(({ label, value, evidenceId }) => {
    const width = Math.min(100, Math.abs(value) / scale * (signed ? 50 : 100));
    const left = signed ? (value < 0 ? 50 - width : 50) : 0;
    const tone = signed ? (value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral') : 'score';
    const rowLabel = (evidenceId && INDEX_LABELS[evidenceId]) || label;
    return `<div class="chart-row" data-evidence-id="${escapeHtml(evidenceId)}"><span class="row-label">${escapeHtml(rowLabel)}</span><div class="bar-track ${signed ? 'signed' : ''}" aria-hidden="true"><span class="bar ${tone}" style="left:${left}%;width:${width}%"></span></div><span class="chart-value ${tone}">${display(value)}</span></div>`;
  }).join('');
  const table = rows.map(({ label, value }) => `<tr><th scope="row">${escapeHtml(label)}</th><td>${display(value)}</td></tr>`).join('');
  const tableUnit = data.unit === 'percentage_points' ? '超额收益（百分点）' : data.unit === 'score' ? '分位分数（0–100）' : '涨跌幅（%）';
  const suffix = data.unit === 'percent' ? '%' : '';
  const axis = `<div class="chart-row axis" aria-hidden="true"><span></span><div class="axis-values"><span>${signed ? `−${scale}${suffix}` : '0'}</span><span>${signed ? '0' : '50'}</span><span>${signed ? '+' : ''}${scale}${suffix}</span></div><span></span></div>`;
  return `<figure><figcaption><span class="chart-title">${escapeHtml(data.title)}</span><span class="chart-caption">${escapeHtml(sourceTimeText(data.caption))}</span></figcaption><div class="chart-rows">${bars}${axis}</div><details class="chart-details"><summary>查看数据明细</summary><div class="table-wrap"><table><caption>${escapeHtml(data.title)}</caption><thead><tr><th scope="col">项目</th><th scope="col">${tableUnit}</th></tr></thead><tbody>${table}</tbody></table></div></details></figure>`;
}

function bullets(items: readonly string[]): string {
  return items.length ? `<ul>${items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '';
}
function supportingPoints(items: readonly string[]): string {
  return items.length ? `<details class="supporting"><summary>数据依据 <span class="count">${items.length}</span></summary>${bullets(items)}</details>` : '';
}
function breadth(visuals: ReportVisuals): string {
  const data = visuals.breadth;
  if (!data || !Number.isInteger(data.total) || data.total <= 0 || data.total > 100
    || !Number.isInteger(data.above) || data.above < 0 || data.above > data.total
    || !Number.isInteger(data.expected) || data.expected < data.total) return '';
  const ratio = (data.above / data.total * 100).toFixed(2);
  const segments = Array.from({ length: data.total }, (_, i) => `<span class="${i < data.above ? 'filled' : ''}"></span>`).join('');
  return `<div class="breadth"><div class="breadth-heading"><span>行业广度</span><span class="breadth-value">${data.above}/${data.total}<span class="muted"> · ${ratio}%</span></span></div><div class="breadth-track" style="grid-template-columns:repeat(${data.total},minmax(0,1fr))" aria-hidden="true">${segments}</div><p class="chart-caption">${data.total}只行业基金中，${data.above}只站上50日均线 · 覆盖${data.total}/${data.expected}<br>${escapeHtml(sourceTimeText(data.asOf))}</p></div>`;
}

const CSS = `
*{box-sizing:border-box}html,body{margin:0;max-width:100%;overflow-wrap:anywhere}
body{font-family:var(--report-font,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif);color:var(--report-ink,#111);background:var(--report-paper,#fff);font-size:15px;line-height:24px}
.report{width:100%;max-width:100%;padding:14px 0 0}h1,h2,h3,p,figure{margin:0}h1{font-size:20px;line-height:28px;font-weight:500;margin:16px 0 14px;text-wrap:pretty}h2{font-size:16px;line-height:24px;font-weight:500;margin-bottom:12px}h3{font-size:14px;line-height:22px;font-weight:500;margin:0 0 5px}
p,li{text-wrap:pretty}.summary-text{color:var(--report-body,#525252)}.muted,.chart-caption{color:var(--report-muted,#666)}
.source-strip{font-size:13px;line-height:20px;padding:10px 14px;border-radius:var(--report-radius-control,10px);background:var(--report-soft,#f5f5f5);color:var(--report-muted,#666)}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 22px}.badge,.tag,.count{font-size:12px;line-height:18px;border-radius:var(--report-radius-pill,999px);padding:2px 9px;background:var(--report-soft,#f5f5f5);color:var(--report-muted,#666)}.tag{display:inline-block;padding:1px 7px;margin-left:6px;vertical-align:baseline}.count{padding:0 6px}
.sections{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:28px}.section{min-width:0;padding-top:20px;border-top:1px solid var(--report-line,#ebebeb)}ul{margin:10px 0 0;padding-left:19px;font-size:14px;line-height:23px;color:var(--report-body,#525252)}li+li{margin-top:7px}
figure{margin:18px 0 0;padding:18px 16px 12px;border:1px solid var(--report-line,#ebebeb);border-radius:var(--report-radius-card,18px);min-width:0;background:var(--report-paper,#fff)}figcaption span{display:block}.chart-title{font-size:14px;line-height:22px;font-weight:500}.chart-caption{font-size:12px;line-height:19px;margin-top:4px}.chart-rows{margin-top:18px}.chart-row{display:grid;grid-template-columns:minmax(72px,104px) minmax(0,1fr) 62px;gap:9px;align-items:center;font-size:13px;line-height:20px}.chart-row+.chart-row{margin-top:11px}.row-label{color:var(--report-body,#525252)}.bar-track{height:12px;position:relative;border-radius:var(--report-radius-pill,999px);background:var(--report-soft,#f5f5f5);overflow:hidden}.signed:before{content:"";position:absolute;left:50%;height:100%;border-left:1px solid var(--report-line,#ebebeb);z-index:1}.bar{position:absolute;height:100%;top:0;border-radius:var(--report-radius-pill,999px)}.bar.positive{background:var(--report-up,#0b7a55);border-top-left-radius:0;border-bottom-left-radius:0}.bar.negative{background:var(--report-down,#c4302b);border-top-right-radius:0;border-bottom-right-radius:0}.bar.score{background:var(--report-brand,#2e46e0)}.chart-value{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.chart-value.positive{color:var(--report-up,#0b7a55)}.chart-value.negative{color:var(--report-down,#c4302b)}.chart-value.score{color:var(--report-brand,#2e46e0)}.neutral{color:var(--report-muted,#666)}.axis{font-size:11px;line-height:16px;color:var(--report-muted,#666)}.axis-values{display:flex;justify-content:space-between;gap:2px}
.breadth{margin-top:14px;padding:13px 16px;border-radius:var(--report-radius-control,10px);background:var(--report-soft,#f5f5f5)}.breadth-heading{display:flex;justify-content:space-between;gap:12px;font-size:13px}.breadth-value{font-variant-numeric:tabular-nums}.breadth-track{display:grid;gap:4px;margin:9px 0}.breadth-track span{height:8px;border-radius:var(--report-radius-pill,999px);background:var(--report-line,#ebebeb)}.breadth-track .filled{background:var(--report-brand,#2e46e0)}
details{font-size:13px;line-height:21px;color:var(--report-muted,#666)}summary{cursor:pointer;width:fit-content;max-width:100%;padding:6px 8px;border-radius:var(--report-radius-control,10px)}summary:hover{background:var(--report-soft,#f5f5f5)}summary:focus-visible{outline:2px solid var(--report-brand,#2e46e0);outline-offset:2px}.supporting{margin:9px 0 0}.supporting summary{margin-left:-8px}.chart-details{margin-top:10px}.chart-details summary{margin-left:-8px}.table-wrap{overflow:auto;border-radius:var(--report-radius-control,10px)}table{width:100%;border-collapse:collapse;margin-top:8px;color:var(--report-body,#525252);font-size:13px}caption{text-align:left;color:var(--report-muted,#666);margin-bottom:5px}th,td{font-weight:400;text-align:left;padding:7px 5px;border-bottom:1px solid var(--report-line,#ebebeb)}td{text-align:right;white-space:nowrap}
.sector-note,.news-item{margin-top:15px}.sector-note p,.news-item p{font-size:14px;line-height:23px;color:var(--report-body,#525252)}.news-heading{margin-top:24px;padding-top:17px;border-top:1px solid var(--report-line,#ebebeb)}.more-news{margin-top:12px}.notes{margin-top:14px;padding:12px 14px;border-radius:var(--report-radius-control,10px);background:var(--report-soft,#f5f5f5);font-size:13px;line-height:21px;color:var(--report-muted,#666)}.notes p+p{margin-top:6px}
.watch{padding:15px 0;border-bottom:1px solid var(--report-line,#ebebeb)}.watch:first-of-type{padding-top:0}.watch dl{margin:9px 0 0;font-size:14px;line-height:23px}.watch dt{font-size:12px;color:var(--report-muted,#666);margin-top:9px}.watch dd{margin:2px 0 0;color:var(--report-body,#525252)}.invalidators{margin-top:18px;padding:16px;border-radius:var(--report-radius-card,18px);background:var(--report-soft,#f5f5f5)}
@media(max-width:700px){.sections{grid-template-columns:minmax(0,1fr);gap:24px}.chart-row{grid-template-columns:72px minmax(0,1fr) 56px;gap:7px}figure{padding:16px 14px 10px}.breadth{padding:12px 14px}.report{padding-top:12px}.watch dl{font-size:14px}h1{font-size:19px;line-height:28px}}
@media(max-width:340px){.chart-row{grid-template-columns:64px minmax(0,1fr) 54px;gap:6px}.row-label,.chart-value{font-size:12px}figure{padding-left:12px;padding-right:12px}.axis{font-size:11px}}
@media(pointer:coarse){summary{min-height:44px;display:flex;align-items:center;gap:6px}}
`;

/** Existing prose is escaped and preserved; charts only consume the selected evidence snapshot. */
export function createMarketBriefOpenUIPreview(brief: MarketBrief, visuals: ReportVisuals = DEMO_VISUALS): OpenGenUIContent {
  const result = brief.result;
  const sourceLabel = visuals.source === 'server'
    ? `${visuals.sourceLabel} · 研判交易日 ${brief.tradingDate ?? '—'} · 生成于 ${sourceTimeText(brief.generatedAt ?? '—')}`
    : visuals.sourceLabel;
  const regime = { broad_advance: '普遍走强', narrow_leadership: '少数权重股领涨', rotation: '板块轮动', risk_off: '避险', mixed: '信号混杂', uncertain: '证据不足' }[result.regime];
  const sufficiency = result.evidence_sufficiency ? { low: '较低', medium: '中等', high: '较高' }[result.evidence_sufficiency] : '—';
  const sectorText = result.sectors.map(item => `<div class="sector-note"><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.note)}</p></div>`).join('');
  const pricedIn = { yes: '已计价', partly: '部分计价', no: '未计价', unclear: '计价程度不明' };
  const newsItem = (item: typeof result.key_news[number]) => `<div class="news-item"><h3>${escapeHtml(item.title_zh)}<span class="tag">${pricedIn[item.priced_in]}</span></h3><p>${escapeHtml(item.what_is_new)}</p></div>`;
  const news = result.key_news.length ? `<h3 class="news-heading">关键新闻</h3>${result.key_news.slice(0, 2).map(newsItem).join('')}${result.key_news.length > 2 ? `<details class="more-news"><summary>其余${result.key_news.length - 2}条新闻</summary>${result.key_news.slice(2).map(newsItem).join('')}</details>` : ''}` : '';
  const watches = result.watch_items.map(item => `<div class="watch"><h3>${escapeHtml(item.what)}</h3><dl>${item.why ? `<dt>为何关注</dt><dd>${escapeHtml(item.why)}</dd>` : ''}${item.revise_if ? `<dt>改判条件</dt><dd>${escapeHtml(item.revise_if)}</dd>` : ''}</dl></div>`).join('');
  const notes = visuals.notes.length ? `<div class="notes">${visuals.notes.map(note => `<p>${escapeHtml(sourceTimeText(note))}</p>`).join('')}</div>` : '';
  return {
    initialHeight: 1200, generating: false, cssComplete: true, htmlComplete: true,
    jsFunctionsComplete: true, jsExpressionsComplete: true,
    css: CSS, jsFunctions: '', jsExpressions: [],
    html: [`<article class="report"><div class="source-strip">${escapeHtml(sourceLabel)}</div><h1>${escapeHtml(result.headline ?? '市场报告')}</h1><div class="meta"><span class="badge">${regime}</span><span class="badge">证据充分度：${sufficiency}</span></div><div class="sections"><section class="section"><h2>大盘与内部结构</h2><p class="summary-text">${escapeHtml(result.internals.summary)}</p>${chart(visuals.market)}${breadth(visuals)}${supportingPoints(result.internals.points)}</section><section class="section"><h2>宏观与跨资产验证</h2><p class="summary-text">${escapeHtml(result.macro_check.summary)}</p>${chart(visuals.macro)}${supportingPoints(result.macro_check.points)}${notes}</section><section class="section"><h2>行业与关键新闻</h2>${chart(visuals.sectors)}${sectorText}${news}</section><section class="section"><h2>后续观察与反证</h2>${watches}${result.invalidators.length ? `<div class="invalidators"><h3>撤回当前判断的条件</h3>${bullets(result.invalidators)}</div>` : ''}</section></div></article>`],
  };
}
