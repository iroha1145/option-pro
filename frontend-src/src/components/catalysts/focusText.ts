/**
 * 热点追踪卡的正文整理。纯函数，不依赖界面与词典，单测见 tests/focus-text.test.mjs。
 *
 * 生产上的公开结果（后端 focus_verification.public_focus_result）里，headline_summary、
 * summary_zh、market_summary 三段一模一样，都是全部已核实事件的摘要用换行拼起来的
 * （超过 3000 字截断）；dominant_events 只取其中前 8 条，每条的 summary 就是其中一段。
 * 卡片按下面的规则整理：
 *   1. 三段两两相同或互相包含时只留一段；和标题相同的句子不再重复。
 *   2. 与某个主导事件摘要相同（或互相包含）的段落只在事件里显示。
 *   3. 第一个事件段落之前、不属于任何事件的段落才是导语；之后的其余段落是没进主导事件的
 *      事件（超出 8 条上限的那几条），单列在事件后面，不进导语。
 *   4. 每段再按「（一）」「一、」「1.」「①」「事件一：」这类分条序号和句末标点（。！？；）
 *      拆成短条，保留「已证实：」「推断：」「需注意：」这类句首标签，去掉重复条目。
 */
import type { FocusEventVerification, MarketFocusCycle } from '@/mocks/fixtures2';

export type FocusVerdict = FocusEventVerification['verdict'];

export interface FocusPoint {
  /** 句首标签，连同原文里的冒号（「推断：」）；没有标签为 null。 */
  label: string | null;
  text: string;
}

export interface FocusEventView {
  key: string;
  /** 核实结论；没有核实记录时为 null（不显示徽标）。 */
  verdict: FocusVerdict | null;
  /** 摘要的第一句，作为标题。 */
  title: FocusPoint;
  /** 收起时标题下的一条。 */
  preview: FocusPoint | null;
  /** 展开后才显示的其余各条。 */
  more: FocusPoint[];
  /** 收起时显示的板块，最多 SECTOR_PREVIEW 个。 */
  sectors: string[];
  /** 折叠成「+N」的其余板块。 */
  hiddenSectors: string[];
}

export interface FocusLeadView {
  /** 收起时显示的前两句。 */
  preview: FocusPoint[];
  more: FocusPoint[];
}

export interface FocusView {
  lead: FocusLeadView | null;
  events: FocusEventView[];
  /** 总摘要里有、主导事件里没有的事件。 */
  extraEvents: FocusEventView[];
}

type FocusCycleText = Pick<
  MarketFocusCycle,
  'dominantEvent' | 'headline' | 'summary' | 'marketSummary' | 'dominantEvents' | 'eventVerifications'
>;

const SECTOR_PREVIEW = 3;
const LEAD_PREVIEW = 2;
/* 后端公开结果的总摘要按 3000 字截断；截断处的最后一段没有句末标点，补一个省略号。 */
const SUMMARY_CAP = 3000;
/* 互相包含只在较长的文本之间判断，免得一句短话恰好出现在事件里就被当成事件。 */
const MIN_CONTAINED_KEY = 16;

/* 分条序号只认段首或标点、空白之后的位置：「2.9%」「11.1 mmHg」「第（一）项」「统一、」不会被拆开。
   带括号的序号也认逗号之后；「一、」「1、」不认逗号之后（「一、二级市场」「1、2月」）。 */
const PAREN_ORDINAL = /(?<=^|[\s。！？!?；;：:，,])[（(](?:[一二三四五六七八九十]{1,3}|\d{1,2})[）)]\s*/g;
const LIST_ORDINAL = /(?<=^|[\s。！？!?；;：:])(?:[一二三四五六七八九十]{1,3}、|\d{1,2}[.．、](?!\d)|[①-⑩]|事件[一二三四五六七八九十]{1,3}[：:，,、])\s*/g;
/* 括号与引号里的句号不断句；内层先配对，最多处理四层嵌套。 */
const BRACKETED = /（[^（）]*）|\([^()]*\)|“[^“”]*”|「[^「」]*」|『[^『』]*』|《[^《》]*》|【[^【】]*】/g;
const SENTENCE_END = /[。！？!?；;]/;
/* 句末标点后紧跟的收尾符号归到同一句。 */
const SENTENCE_TAIL = /[。！？!?；;”’」』）)]/;
const PARAGRAPH_END = /[。！？!?…”’」』）)]$/;

/* 句首标签：冒号前 2 到 6 个汉字（「已证实：」「传导推断：」），或「某某类事件：」。
   「公司表示：」「数据显示：」这类引述开头不算标签。 */
const LABEL_HEAD = /^([^\s：:，,。；;！？!?（）()“”「」《》]{1,10})([：:])\s*/;
const KNOWN_LABEL = /^(?:已证实|已核实|已确认|未证实|待核实|无法核实|核验结果|推断|传导推断|影响推断|含义|影响|需注意|需关注|值得注意|风险|风险提示|局限|不确定性|仍需观察|后续观察|结论|背景)$/;
const CATEGORY_LABEL = /^[一-鿿]{1,6}类事件$/;
const HAN_LABEL = /^[一-鿿]{2,6}$/;
const SPEECH_TAIL = /(?:表示|称|说|指出|认为|强调|透露|提到|宣布|写道|显示|表明|报道|发现|提示|警告|坦言)$/;

function textKey(text: string): string {
  return text.normalize('NFKC').replace(/\s+/g, '');
}

/** 去重用的比较键：忽略空白、全角半角与句末标点。 */
function pointKey(point: FocusPoint): string {
  return textKey(point.text || point.label || '').replace(/[。.!?;,、:…]+$/u, '');
}

function paragraphsOf(text: string): string[] {
  return text
    .replace(/\r\n?/g, '\n')
    .split(/\n+/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function protectedPositions(text: string): boolean[] {
  const mask = new Array<boolean>(text.length).fill(false);
  let work = text;
  for (let round = 0; round < 4; round += 1) {
    let changed = false;
    work = work.replace(BRACKETED, (match: string, offset: number) => {
      for (let i = offset + 1; i < offset + match.length - 1; i += 1) mask[i] = true;
      changed = true;
      return '\u0001'.repeat(match.length);
    });
    if (!changed) break;
  }
  return mask;
}

function sentencesOf(item: string): string[] {
  const mask = protectedPositions(item);
  const out: string[] = [];
  let start = 0;
  for (let i = 0; i < item.length; i += 1) {
    if (mask[i] || !SENTENCE_END.test(item[i])) continue;
    let end = i + 1;
    while (end < item.length && !mask[end] && SENTENCE_TAIL.test(item[end])) end += 1;
    out.push(item.slice(start, end));
    start = end;
    i = end - 1;
  }
  out.push(item.slice(start));
  return out.map((sentence) => sentence.trim()).filter(Boolean);
}

function toPoint(sentence: string): FocusPoint {
  const text = sentence.replace(/[\s，,、]+$/u, '');
  const head = LABEL_HEAD.exec(text);
  if (head) {
    const word = head[1];
    if (KNOWN_LABEL.test(word) || CATEGORY_LABEL.test(word) || (HAN_LABEL.test(word) && !SPEECH_TAIL.test(word))) {
      return { label: word + head[2], text: text.slice(head[0].length).trim() };
    }
  }
  return { label: null, text };
}

/** 只有标签、没有正文的条目（「已证实：」后面紧跟分条序号）并到下一条前面。 */
function mergeLabelOnly(points: FocusPoint[]): FocusPoint[] {
  const out: FocusPoint[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const point = points[i];
    const next = points[i + 1];
    if (point.label && !point.text && next && !next.label) {
      out.push({ label: point.label, text: next.text });
      i += 1;
    } else {
      out.push(point);
    }
  }
  return out;
}

function dedupePoints(points: FocusPoint[]): FocusPoint[] {
  const seen = new Set<string>();
  return points.filter((point) => {
    const key = pointKey(point);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * 把一段长文本拆成短条：先按换行分段，再按分条序号和句末标点断开。
 * 分条序号本身去掉（列表的先后顺序已经表达了序号）；句首标签单独放在 label 里。
 */
export function splitFocusText(text: string | null | undefined): FocusPoint[] {
  if (!text) return [];
  const points: FocusPoint[] = [];
  for (const paragraph of paragraphsOf(text)) {
    const items = paragraph.replace(PAREN_ORDINAL, '\n').replace(LIST_ORDINAL, '\n').split('\n');
    for (const item of items) {
      for (const sentence of sentencesOf(item)) points.push(toPoint(sentence));
    }
  }
  return dedupePoints(mergeLabelOnly(points));
}

/** 两两相同或一段包含另一段时只留较长（或较早）的那段。 */
function distinctTexts(texts: readonly (string | null | undefined)[]): string[] {
  const items = texts
    .map((text) => (text ?? '').trim())
    .filter(Boolean)
    .map((text) => ({ text, key: textKey(text) }));
  return items
    .filter((item, index) => !items.some((other, j) => j !== index
      && other.key.includes(item.key)
      && (other.key.length > item.key.length || j < index)))
    .map((item) => item.text);
}

function eventView(
  summary: string,
  key: string,
  verdict: FocusVerdict | null,
  sectors: readonly string[],
): FocusEventView | null {
  const [title, preview = null, ...more] = splitFocusText(summary);
  if (!title) return null;
  const unique = [...new Set(sectors.map((sector) => sector.trim()).filter(Boolean))];
  return {
    key,
    verdict,
    title,
    preview,
    more,
    sectors: unique.slice(0, SECTOR_PREVIEW),
    hiddenSectors: unique.slice(SECTOR_PREVIEW),
  };
}

/** 整理一轮热点分析：导语、主导事件、没进主导事件的其余事件。 */
export function buildFocusView(cycle: FocusCycleText): FocusView {
  const verdicts = new Map((cycle.eventVerifications ?? []).map((entry) => [entry.eventGroupId, entry.verdict]));
  const seenEvents = new Set<string>();
  const dominant = (cycle.dominantEvents ?? []).filter((event) => {
    const key = textKey(event.summary);
    if (!key || seenEvents.has(key)) return false;
    seenEvents.add(key);
    return true;
  });
  const events = dominant.flatMap((event, index) => {
    const view = eventView(
      event.summary,
      event.eventGroupId || `event-${index}`,
      verdicts.get(event.eventGroupId) ?? null,
      event.affectedSectors,
    );
    return view ? [view] : [];
  });

  const eventKeys = [...seenEvents].filter((key) => key.length >= MIN_CONTAINED_KEY);
  const eventPointKeys = new Set(
    events.flatMap((event) => [event.title, ...(event.preview ? [event.preview] : []), ...event.more]).map(pointKey),
  );
  const covered = (paragraph: string): boolean => {
    const key = textKey(paragraph);
    if (key.length >= MIN_CONTAINED_KEY && eventKeys.some((eventKey) => eventKey.includes(key) || key.includes(eventKey))) {
      return true;
    }
    const points = splitFocusText(paragraph);
    return points.length > 0 && points.every((point) => eventPointKeys.has(pointKey(point)));
  };

  const overall = distinctTexts([cycle.headline, cycle.summary, cycle.marketSummary]);
  /* 截断在半句处的最后一段补省略号，读者能看出后面还有内容。 */
  const cut = new Set(overall
    .filter((text) => text.length >= SUMMARY_CAP)
    .map((text) => paragraphsOf(text).at(-1) ?? '')
    .filter((paragraph) => paragraph && !PARAGRAPH_END.test(paragraph)));
  const paragraphs = [...new Set(overall.flatMap(paragraphsOf))];
  const firstCovered = paragraphs.findIndex(covered);
  const narrative = firstCovered === -1 ? paragraphs : paragraphs.slice(0, firstCovered);
  const leftovers = firstCovered === -1 ? [] : paragraphs.slice(firstCovered).filter((paragraph) => !covered(paragraph));
  const finish = (paragraph: string) => (cut.has(paragraph) ? `${paragraph}…` : paragraph);

  const titleKey = pointKey({ label: null, text: cycle.dominantEvent ?? '' });
  const leadPoints = splitFocusText(narrative.map(finish).join('\n')).filter((point) => {
    const key = pointKey(point);
    return key !== titleKey && !eventPointKeys.has(key);
  });
  const extraEvents = leftovers.flatMap((paragraph, index) => {
    const view = eventView(finish(paragraph), `extra-${index}`, null, []);
    return view ? [view] : [];
  });

  return {
    lead: leadPoints.length
      ? { preview: leadPoints.slice(0, LEAD_PREVIEW), more: leadPoints.slice(LEAD_PREVIEW) }
      : null,
    events,
    extraEvents,
  };
}
