/**
 * 「市场报告」图文版：把一份报告拼成静态 HTML/CSS，由 MarketBriefModelOutput 放进 Shadow DOM。
 *
 * - 输出沿用 OpenIntelligentUI 的完成态协议（open-intelligent-ui/schema.ts），只有 html 与 css，
 *   jsFunctions 为空串、jsExpressions 为空数组：报告里没有脚本。
 * - 模型写的正文转义后原样放入，不经 t()；界面文字与程序给的标签（指数、主题、宏观分项名）经 t()。
 * - 图表数值只来自 brief.visuals（后端从该次证据包现算），不从正文里抽数字；缺哪张图就不画哪张。
 * - 颜色直接读宿主页面的 CSS 变量：自定义属性会穿过影子边界继承，深浅色与涨跌配色一切换就生效。
 */
import type { BriefChartRow, MarketBrief, MarketBriefVisuals } from '@/api/modules/marketBrief';
import type { OpenGenUIContent } from '@/components/open-intelligent-ui/schema';
import { fmtPct, fmtSigned } from '@/lib/format';
import {
  BREADTH_LABEL,
  BREADTH_TONE,
  CHANGE_LABEL,
  CHANGE_TONE,
  PRICED_IN_LABEL,
  PRICED_IN_TONE,
  REGIME_LABEL,
  REGIME_TONE,
  SUFFICIENCY_LABEL,
  SUFFICIENCY_TONE,
  VERDICT_LABEL,
  VERDICT_TONE,
  throughStamp,
} from './marketBriefText';
import { t } from '../../i18n/core.ts';

const MISSING = '—';
const SEP = ' · ';

function escapeHtml(value: string | null | undefined): string {
  return (value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

type Unit = 'percent' | 'points' | 'score';

interface ChartSpec {
  id: 'market' | 'sectors' | 'macro';
  title: string;
  caption: string;
  unit: Unit;
  rows: BriefChartRow[];
}

/** 正负对称坐标的上限：取 1、2、5、10 乘 10 的整数次幂里第一个不小于最大绝对值的数。 */
function axisLimit(values: number[]): number {
  const max = Math.max(0, ...values.map(Math.abs));
  if (!max) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  return Number((([1, 2, 5, 10].find((step) => step * magnitude >= max) ?? 10) * magnitude).toPrecision(3));
}

function display(value: number, unit: Unit): string {
  if (unit === 'percent') return fmtPct(value, 2);
  if (unit === 'points') return fmtSigned(value, 2);
  return value.toFixed(1);
}

/** 图注里的数据时点：ISO 时刻写成纽约时间，只有日期的原样。 */
function stampText(raw: string | null, year?: string): string | null {
  return raw ? throughStamp(raw, year)?.text ?? null : null;
}

function chartHtml(spec: ChartSpec | null): string {
  if (!spec) return '';
  const rows = spec.rows.filter((row) => Number.isFinite(row.value));
  if (!rows.length) return '';
  const signed = spec.unit !== 'score';
  const scale = signed ? axisLimit(rows.map((row) => row.value)) : 100;
  const bars = rows
    .map(({ id, label, value }) => {
      const width = Math.min(100, (Math.abs(value) / scale) * (signed ? 50 : 100));
      const left = signed ? (value < 0 ? 50 - width : 50) : 0;
      const tone = signed ? (value > 0 ? 'up' : value < 0 ? 'down' : 'flat') : 'score';
      return `<div class="chart-row" data-id="${escapeHtml(id)}"><span class="row-label">${escapeHtml(t(label))}</span><span class="bar-track${signed ? ' signed' : ''}" aria-hidden="true"><span class="bar ${tone}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></span></span><span class="row-value ${tone}">${escapeHtml(display(value, spec.unit))}</span></div>`;
    })
    .join('');
  const suffix = spec.unit === 'percent' ? '%' : '';
  const axis = signed ? [`−${scale}${suffix}`, '0', `+${scale}${suffix}`] : ['0', '50', '100'];
  const axisRow = `<div class="chart-row axis" aria-hidden="true"><span></span><span class="axis-values">${axis.map((tick) => `<span>${tick}</span>`).join('')}</span><span></span></div>`;
  const unitHeader = spec.unit === 'percent' ? t('涨跌幅（%）') : spec.unit === 'points' ? t('超额收益（百分点）') : t('评分（0–100）');
  const table = rows
    .map(({ label, value }) => `<tr><th scope="row">${escapeHtml(t(label))}</th><td>${escapeHtml(display(value, spec.unit))}</td></tr>`)
    .join('');
  return `<figure class="chart" data-chart="${spec.id}"><figcaption><span class="chart-title">${escapeHtml(spec.title)}</span><span class="caption">${escapeHtml(spec.caption)}</span></figcaption><div class="chart-rows">${bars}${axisRow}</div><details class="chart-details"><summary>${escapeHtml(t('查看明细'))}</summary><div class="table-wrap"><table><thead><tr><th scope="col">${escapeHtml(t('项目'))}</th><th scope="col">${escapeHtml(unitHeader)}</th></tr></thead><tbody>${table}</tbody></table></div></details></figure>`;
}

function marketSpec(market: MarketBriefVisuals['market'], year?: string): ChartSpec | null {
  if (!market) return null;
  const time = stampText(market.asOf, year);
  const caption = [
    t('单位：%'),
    time ? t('数据时点 纽约时间 {time}', { time }) : null,
    market.stale ? t('快照可能不是最新') : null,
  ].filter(Boolean).join(SEP);
  return { id: 'market', title: t('美股指数涨跌幅'), caption, unit: 'percent', rows: market.rows };
}

function sectorsSpec(sectors: MarketBriefVisuals['sectors'], year?: string): ChartSpec | null {
  if (!sectors) return null;
  const day = stampText(sectors.servedSession, year);
  const caption = [
    t('单位：百分点，相对标普 500'),
    day ? t('数据交易日 {day}', { day }) : null,
    t('只列报告引用的主题'),
  ].filter(Boolean).join(SEP);
  return { id: 'sectors', title: t('主题一个月超额收益'), caption, unit: 'points', rows: sectors.rows };
}

function macroSpec(macro: MarketBriefVisuals['macro'], year?: string): ChartSpec | null {
  if (!macro) return null;
  const day = stampText(macro.dataThrough, year);
  const caption = [t('0–100 分，越高越宽松、越支持风险资产'), day ? t('数据截至 {day}', { day }) : null]
    .filter(Boolean)
    .join(SEP);
  return { id: 'macro', title: t('宏观分项评分'), caption, unit: 'score', rows: macro.rows };
}

function breadthHtml(breadth: MarketBriefVisuals['breadth'], year?: string): string {
  if (!breadth) return '';
  const { above, total, expected } = breadth;
  if (!(Number.isInteger(above) && Number.isInteger(total) && Number.isInteger(expected))) return '';
  if (!(total > 0 && above >= 0 && above <= total && total <= expected && expected <= 100)) return '';
  const share = `${((above / total) * 100).toFixed(1)}%`;
  const time = stampText(breadth.asOf, year);
  const caption = [
    t('{total} 只行业 ETF 中 {above} 只站上 50 日均线', { total, above }),
    total < expected ? t('覆盖 {total}/{expected}', { total, expected }) : null,
    time ? t('数据时点 纽约时间 {time}', { time }) : null,
    breadth.stale ? t('快照可能不是最新') : null,
  ].filter(Boolean).join(SEP);
  const segments = Array.from({ length: total }, (_, index) => `<span${index < above ? ' class="filled"' : ''}></span>`).join('');
  return `<div class="breadth" data-chart="breadth"><div class="breadth-head"><span>${escapeHtml(t('行业广度'))}</span><span class="breadth-value">${above}/${total}<span class="muted">${SEP}${share}</span></span></div><div class="breadth-track" aria-hidden="true">${segments}</div><p class="caption">${escapeHtml(caption)}</p></div>`;
}

function badge(text: string, tone: string, title?: string): string {
  return `<span class="badge ${tone}"${title ? ` title="${escapeHtml(title)}"` : ''}>${escapeHtml(text)}</span>`;
}

function bullets(items: readonly string[]): string {
  return items.length ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '';
}

/** 支撑要点默认收起：图已经把主要数字画出来了，正文先只看结论。 */
function supporting(items: readonly string[]): string {
  return items.length
    ? `<details class="supporting"><summary>${escapeHtml(t('数据依据'))} <span class="count">${items.length}</span></summary>${bullets(items)}</details>`
    : '';
}

/** 宏观数据在报告交易日之前就截止时单独提醒：图上的分数不是报告当天的读数。 */
function macroLagNote(brief: MarketBrief): string {
  const through = brief.visuals?.macro?.dataThrough?.slice(0, 10);
  const trading = brief.tradingDate;
  if (!through || !trading || through >= trading) return '';
  return `<p class="note">${escapeHtml(t('宏观数据截至 {day}，早于报告交易日 {trading}，不是报告当天的读数。', { day: through, trading }))}</p>`;
}

function section(title: string, badgeHtml: string, body: string): string {
  return `<section class="section"><div class="section-head"><h3>${escapeHtml(title)}</h3>${badgeHtml}</div>${body}</section>`;
}

export const REPORT_CSS = `
:host{display:block;container-type:inline-size;color:var(--ink-900);overflow-wrap:anywhere}
*{box-sizing:border-box}
h3,h4,p,figure,ul,dl,dd{margin:0}
.report{font-size:15px;line-height:24px}
.source-strip{font-size:13px;line-height:20px;padding:8px 12px;margin:0 0 12px;border-radius:var(--r-control,10px);background:var(--paper-2);color:var(--ink-500)}
.lead{display:flex;flex-wrap:wrap;align-items:flex-start;gap:8px 16px}
.headline{flex:1 1 22rem;min-width:0;font-size:20px;line-height:26px;font-weight:500;color:var(--ink-900)}
.headline.missing{color:var(--ink-400)}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding-top:4px}
.badge{display:inline-flex;align-items:center;max-width:100%;white-space:nowrap;font-size:12px;line-height:16px;font-weight:500;padding:1px 8px;border:1px solid var(--line);border-radius:var(--r-chip,999px);background:var(--paper-2);color:var(--ink-600);font-variant-numeric:tabular-nums}
.badge.ok{background:var(--ok-50);color:var(--ok-700);border-color:color-mix(in srgb,var(--ok-600) 20%,transparent)}
.badge.warn{background:var(--warn-50);color:var(--warn-700);border-color:color-mix(in srgb,var(--warn-600) 25%,transparent)}
.badge.danger{background:var(--danger-50);color:var(--danger-700);border-color:color-mix(in srgb,var(--danger-600) 20%,transparent)}
.badge.brand{background:var(--brand-50);color:var(--brand-700);border-color:color-mix(in srgb,var(--brand-600) 15%,transparent)}
.badge.up{background:var(--up-50);color:var(--up-700);border-color:color-mix(in srgb,var(--up-600) 20%,transparent)}
.badge.down{background:var(--down-50);color:var(--down-700);border-color:color-mix(in srgb,var(--down-600) 20%,transparent)}
.sections{display:grid;grid-template-columns:minmax(0,1fr);gap:24px;margin-top:24px}
@container (min-width:880px){.sections{grid-template-columns:repeat(2,minmax(0,1fr));gap:28px 32px}}
.section{min-width:0}
.section-head{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
h3{font-size:14px;line-height:21px;font-weight:500;color:var(--ink-900)}
h4{font-size:14px;line-height:21px;font-weight:500;color:var(--ink-800)}
.summary{margin-top:8px;color:var(--ink-800)}
ul{margin-top:8px;padding-left:18px;font-size:14px;line-height:21px;color:var(--ink-600)}
li+li{margin-top:6px}
.chart{margin-top:16px;padding:16px 16px 10px;border:1px solid var(--line);border-radius:var(--r-group,14px);background:var(--card);min-width:0}
figcaption span{display:block}
.chart-title{font-size:14px;line-height:21px;font-weight:500;color:var(--ink-900)}
.caption{font-size:12px;line-height:18px;margin-top:2px;color:var(--ink-500)}
.chart-rows{margin-top:14px}
.chart-row{display:grid;grid-template-columns:minmax(72px,112px) minmax(0,1fr) 64px;gap:10px;align-items:center;font-size:13px;line-height:19px}
.chart-row+.chart-row{margin-top:10px}
.row-label{color:var(--ink-600);min-width:0}
.bar-track{display:block;height:10px;position:relative;border-radius:var(--r-pill,999px);background:var(--paper-2);overflow:hidden}
.bar-track.signed::before{content:"";position:absolute;left:50%;top:0;bottom:0;border-left:1px solid var(--line);z-index:1}
.bar{position:absolute;top:0;height:100%;border-radius:var(--r-pill,999px)}
.bar.up{background:var(--up-600);border-top-left-radius:0;border-bottom-left-radius:0}
.bar.down{background:var(--down-600);border-top-right-radius:0;border-bottom-right-radius:0}
.bar.score{background:var(--brand-600)}
.row-value{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--ink-600)}
.row-value.up{color:var(--up-700)}
.row-value.down{color:var(--down-700)}
.row-value.score{color:var(--ink-800)}
.chart-row.axis{font-size:11px;line-height:16px;color:var(--ink-400);margin-top:6px}
.axis-values{display:flex;justify-content:space-between;gap:2px;font-variant-numeric:tabular-nums}
.breadth{margin-top:12px;padding:12px 16px;border-radius:var(--r-control,10px);background:var(--paper-2)}
.breadth-head{display:flex;justify-content:space-between;gap:12px;font-size:13px;line-height:19px;color:var(--ink-800)}
.breadth-value{font-variant-numeric:tabular-nums;white-space:nowrap}
.muted{color:var(--ink-500)}
.breadth-track{display:flex;gap:4px;margin:8px 0}
.breadth-track span{flex:1 1 0;height:8px;border-radius:var(--r-pill,999px);background:var(--line)}
.breadth-track .filled{background:var(--brand-600)}
details{font-size:13px;line-height:19px;color:var(--ink-500)}
summary{cursor:pointer;width:fit-content;max-width:100%;padding:6px 8px;margin-left:-8px;border-radius:var(--r-control,10px)}
summary:hover{background:var(--paper-2);color:var(--ink-800)}
summary:focus-visible{outline:2px solid var(--brand-600);outline-offset:2px}
.supporting,.chart-details{margin-top:8px}
.count{display:inline-block;font-size:12px;line-height:16px;padding:0 6px;border-radius:var(--r-pill,999px);background:var(--paper-2);color:var(--ink-500);font-variant-numeric:tabular-nums}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;margin-top:4px;font-size:13px;color:var(--ink-600)}
th,td{font-weight:400;text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
td{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
thead th{color:var(--ink-500)}
.note{margin-top:10px;padding:10px 12px;border-radius:var(--r-control,10px);background:var(--warn-50);color:var(--warn-700);font-size:13px;line-height:19px}
.item{margin-top:14px}
.item-head{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.item p{margin-top:2px;font-size:14px;line-height:21px;color:var(--ink-600)}
.news-label{margin-top:20px;font-size:13px;line-height:18px;color:var(--ink-500)}
.watch{padding:12px 0;border-bottom:1px solid var(--line)}
.watch:first-of-type{padding-top:4px}
.watch dl{font-size:14px;line-height:21px}
.watch dt{font-size:12px;line-height:18px;color:var(--ink-500);margin-top:6px}
.watch dd{color:var(--ink-600)}
.invalidators{margin-top:16px;padding:10px 12px;border-radius:var(--r-control,10px);background:var(--paper-2)}
.invalidators h4{font-size:13px;line-height:19px;font-weight:400;color:var(--ink-700)}
@container (max-width:420px){.chart{padding:14px 12px 8px}.chart-row{grid-template-columns:minmax(60px,84px) minmax(0,1fr) 58px;gap:6px}.breadth{padding:12px}}
@media (pointer:coarse){summary{min-height:44px;display:flex;align-items:center;gap:6px}}
`;

export interface ReportOptions {
  /** 演示模式：报告顶部标明图表与正文都是示例。 */
  demo?: boolean;
  /** 当前年份（纽约时间）：日期与它相同时省略年份，和卡片其他地方一致。 */
  year?: string;
}

/**
 * 有 visuals 才生成图文版；返回完成态协议的内容（由调用方再经 normalizeOpenGenUIContent 校验）。
 * 没有可画的图时返回 null，调用方显示文字版。
 */
export function createMarketBriefReport(brief: MarketBrief, options: ReportOptions = {}): OpenGenUIContent | null {
  const visuals = brief.visuals;
  if (!visuals) return null;
  const { year } = options;
  const result = brief.result;
  const sufficiency = result.evidence_sufficiency;
  const headline = result.headline
    ? `<p class="headline">${escapeHtml(result.headline)}</p>`
    : `<p class="headline missing">${MISSING}</p>`;
  const lead = `<div class="lead">${headline}<div class="meta">${badge(REGIME_LABEL[result.regime], REGIME_TONE[result.regime])}${badge(sufficiency ? SUFFICIENCY_LABEL[sufficiency] : t('证据充分度：—'), sufficiency ? SUFFICIENCY_TONE[sufficiency] : 'neutral')}</div></div>`;
  const summary = (value: string | null) => `<p class="summary">${escapeHtml(value ?? MISSING)}</p>`;

  const internals = section(
    t('大盘与内部结构'),
    badge(BREADTH_LABEL[result.internals.breadth_vs_index], BREADTH_TONE[result.internals.breadth_vs_index], t('广度与指数是否一致')),
    `${summary(result.internals.summary)}${chartHtml(marketSpec(visuals.market, year))}${breadthHtml(visuals.breadth, year)}${supporting(result.internals.points)}`,
  );
  const macro = section(
    t('宏观与跨资产验证'),
    badge(VERDICT_LABEL[result.macro_check.verdict], VERDICT_TONE[result.macro_check.verdict], t('宏观与跨资产是否支持当前的股票叙事')),
    `${summary(result.macro_check.summary)}${chartHtml(macroSpec(visuals.macro, year))}${macroLagNote(brief)}${supporting(result.macro_check.points)}`,
  );

  const sectorNotes = result.sectors
    .map((read) => `<div class="item"><div class="item-head"><h4>${escapeHtml(read.name)}</h4>${badge(CHANGE_LABEL[read.change], CHANGE_TONE[read.change])}</div>${read.note ? `<p>${escapeHtml(read.note)}</p>` : ''}</div>`)
    .join('');
  const newsItem = (news: MarketBrief['result']['key_news'][number]) =>
    `<div class="item"><div class="item-head"><h4>${escapeHtml(news.title_zh)}</h4>${badge(PRICED_IN_LABEL[news.priced_in], PRICED_IN_TONE[news.priced_in], t('是否已反映在价格中'))}</div>${news.what_is_new ? `<p>${escapeHtml(news.what_is_new)}</p>` : ''}</div>`;
  const news = result.key_news;
  const newsHtml = news.length
    ? `<p class="news-label">${escapeHtml(t('关键新闻'))}</p>${news.slice(0, 2).map(newsItem).join('')}${news.length > 2 ? `<details class="supporting"><summary>${escapeHtml(t('其余 {n} 条新闻', { n: news.length - 2 }))}</summary>${news.slice(2).map(newsItem).join('')}</details>` : ''}`
    : '';
  const sectorsChart = chartHtml(sectorsSpec(visuals.sectors, year));
  const sectors = section(
    t('行业与关键新闻'),
    '',
    sectorsChart || sectorNotes || newsHtml ? `${sectorsChart}${sectorNotes}${newsHtml}` : summary(null),
  );

  const watches = result.watch_items
    .map((watch) => `<div class="watch"><h4>${escapeHtml(watch.what)}</h4><dl>${watch.why ? `<dt>${escapeHtml(t('为何关注'))}</dt><dd>${escapeHtml(watch.why)}</dd>` : ''}${watch.revise_if ? `<dt>${escapeHtml(t('改判条件'))}</dt><dd>${escapeHtml(watch.revise_if)}</dd>` : ''}</dl></div>`)
    .join('');
  const invalidators = result.invalidators.length
    ? `<div class="invalidators"><h4>${escapeHtml(t('撤回条件'))}</h4>${bullets(result.invalidators)}</div>`
    : '';
  const watch = section(t('后续观察与反证'), '', watches || invalidators ? `${watches}${invalidators}` : summary(null));

  const strip = options.demo ? `<p class="source-strip">${escapeHtml(t('演示数据：图表数值与正文均为示例'))}</p>` : '';
  return {
    generating: false,
    css: REPORT_CSS,
    cssComplete: true,
    html: [`<article class="report">${strip}${lead}<div class="sections">${internals}${macro}${sectors}${watch}</div></article>`],
    htmlComplete: true,
    jsFunctions: '',
    jsFunctionsComplete: true,
    jsExpressions: [],
    jsExpressionsComplete: true,
  };
}
