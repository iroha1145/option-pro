/**
 * 热点追踪卡的长文本整理（components/catalysts/focusText.ts）。
 *
 * 生产上的公开结果里 headline_summary、summary_zh、market_summary 一字不差，都是全部已核实
 * 事件摘要用换行拼起来的（超过 3000 字截断），dominant_events 只取前 8 条。这里锁住：
 * 拆条规则（序号、句末标点、括号保护、句首标签、去重）与卡片的去重与归位规则。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildFocusView, splitFocusText } from '../src/components/catalysts/focusText.ts';

const plain = (points) => points.map((point) => `${point.label ?? ''}${point.text}`);

test('按（一）（二）序号分条，序号去掉，「某某类事件：」留作标签', () => {
  const points = splitFocusText('（一）公司类事件：甲公司上调指引。已证实：公告与两家媒体一致。（二）宏观类事件：通胀回落。推断：降息预期升温；需注意：数据可能修正。');
  assert.deepEqual(points, [
    { label: '公司类事件：', text: '甲公司上调指引。' },
    { label: '已证实：', text: '公告与两家媒体一致。' },
    { label: '宏观类事件：', text: '通胀回落。' },
    { label: '推断：', text: '降息预期升温；' },
    { label: '需注意：', text: '数据可能修正。' },
  ]);
});

test('「一、」「1.」「①」「事件一：」都算分条；小数、数值单位与词语里的顿号不拆', () => {
  assert.deepEqual(plain(splitFocusText('一、收入增长2.9%。二、毛利率11.1个百分点。')), ['收入增长2.9%。', '毛利率11.1个百分点。']);
  assert.deepEqual(plain(splitFocusText('推断：订单能见度延长；1. 短期利好设备；2. 中期看折旧。')), ['推断：订单能见度延长；', '短期利好设备；', '中期看折旧。']);
  assert.deepEqual(plain(splitFocusText('①眼压下降11.1 mmHg。②对照组7.4 mmHg。')), ['眼压下降11.1 mmHg。', '对照组7.4 mmHg。']);
  assert.deepEqual(plain(splitFocusText('事件一：美联储按兵不动。事件二：油价上涨。')), ['美联储按兵不动。', '油价上涨。']);
  assert.deepEqual(plain(splitFocusText('监管统一、规范了披露，影响一、二级市场联动。')), ['监管统一、规范了披露，影响一、二级市场联动。']);
  assert.deepEqual(plain(splitFocusText('依据第（一）项规定执行。')), ['依据第（一）项规定执行。']);
});

test('括号和引号里的句号不断句，句末的收尾引号归到同一句', () => {
  assert.deepEqual(plain(splitFocusText('他称“需求仍强。供给偏紧。”随后股价上涨。星巴克上调股息（此前0.62美元。）。')), [
    '他称“需求仍强。供给偏紧。”随后股价上涨。',
    '星巴克上调股息（此前0.62美元。）。',
  ]);
  assert.deepEqual(plain(splitFocusText('分析师说：“前景乐观！”下一句。')), ['分析师说：“前景乐观！”下一句。']);
});

test('引述开头（「公司表示：」「数据显示：」）与带引号的说明不算标签', () => {
  assert.deepEqual(splitFocusText('公司表示：将继续回购。'), [{ label: null, text: '公司表示：将继续回购。' }]);
  assert.deepEqual(splitFocusText('股息汇总显示：星巴克上调股息。'), [{ label: null, text: '股息汇总显示：星巴克上调股息。' }]);
  assert.deepEqual(splitFocusText('已证实的是“报道存在”：多家媒体转述。'), [{ label: null, text: '已证实的是“报道存在”：多家媒体转述。' }]);
  assert.deepEqual(splitFocusText('传导推断：若属实，生态扩大。'), [{ label: '传导推断：', text: '若属实，生态扩大。' }]);
});

test('只有标签的条目并到下一条前面；重复条目只留一次', () => {
  assert.deepEqual(splitFocusText('已证实：（1）收入创新高；（2）指引上调。'), [
    { label: '已证实：', text: '收入创新高；' },
    { label: null, text: '指引上调。' },
  ]);
  assert.deepEqual(plain(splitFocusText('需求回暖。\n需求回暖。\n需求回暖；价格企稳。')), ['需求回暖。', '价格企稳。']);
  assert.deepEqual(splitFocusText(''), []);
  assert.deepEqual(splitFocusText(null), []);
});

/* ---------------- 卡片的去重与归位 ---------------- */

const EVENT_TEXTS = Array.from({ length: 12 }, (_, i) => (
  `（${'一二三四五六七八九十'[i % 10]}）公司类事件：第${i + 1}家公司宣布一项交易，金额${i + 1}亿美元。已证实：公司公告与两家媒体一致。推断：对第${i + 1}家公司的业绩影响有限；需注意：第${i + 1}项交易仍需监管批准。`
));

function productionCycle(events = EVENT_TEXTS, dominantCount = 8) {
  const joined = events.join('\n');
  return {
    dominantEvent: '市场热点分析',
    headline: joined,
    summary: joined,
    marketSummary: joined,
    dominantEvents: events.slice(0, dominantCount).map((summary, i) => ({
      eventGroupId: `evt_${i}`,
      summary,
      affectedSectors: ['半导体', '数据中心', '电力设备', '光通信', '半导体'].slice(0, (i % 5) + 1),
    })),
    eventVerifications: [],
  };
}

test('生产形状：三段相同、由事件拼成时不出导语，8 条主导事件，其余 4 条折在后面', () => {
  const view = buildFocusView(productionCycle());
  assert.equal(view.lead, null, '总摘要全部是事件，导语不再重复事件开头');
  assert.equal(view.events.length, 8);
  assert.equal(view.extraEvents.length, 4);
  assert.deepEqual(view.events[0].title, { label: '公司类事件：', text: '第1家公司宣布一项交易，金额1亿美元。' });
  assert.deepEqual(view.events[0].preview, { label: '已证实：', text: '公司公告与两家媒体一致。' });
  assert.deepEqual(plain(view.events[0].more), ['推断：对第1家公司的业绩影响有限；', '需注意：第1项交易仍需监管批准。']);
  assert.match(view.extraEvents[0].title.text, /^第9家公司/);
  assert.deepEqual(view.extraEvents[0].sectors, []);
  const titles = [...view.events, ...view.extraEvents].map((event) => event.title.text);
  assert.equal(new Set(titles).size, 12, '12 个事件各出现一次，折叠的 4 条不重复主导事件');
});

test('板块最多显示 3 个，多出的折成「+N」，重复的板块只算一次', () => {
  const view = buildFocusView(productionCycle());
  assert.deepEqual(view.events[3].sectors, ['半导体', '数据中心', '电力设备']);
  assert.deepEqual(view.events[3].hiddenSectors, ['光通信']);
  assert.deepEqual(view.events[4].hiddenSectors, ['光通信'], '第五个是重复的「半导体」，不算');
  assert.deepEqual(view.events[0].hiddenSectors, []);
});

test('只有一个事件、总摘要等于事件摘要时只在事件里显示', () => {
  const view = buildFocusView(productionCycle(EVENT_TEXTS.slice(0, 1), 1));
  assert.equal(view.lead, null);
  assert.equal(view.events.length, 1);
  assert.deepEqual(view.extraEvents, []);
});

test('核实结论按 event_group_id 对上；没有核实记录时不给结论', () => {
  const cycle = productionCycle();
  cycle.eventVerifications = [
    { eventGroupId: 'evt_0', verdict: 'supported' },
    { eventGroupId: 'evt_1', verdict: 'contradicted' },
    { eventGroupId: 'evt_2', verdict: 'unverifiable' },
  ];
  const verdicts = buildFocusView(cycle).events.map((event) => event.verdict);
  assert.deepEqual(verdicts.slice(0, 4), ['supported', 'contradicted', 'unverifiable', null]);
  assert.deepEqual(buildFocusView(productionCycle()).events.map((event) => event.verdict), Array(8).fill(null));
});

test('有独立的导语时：标题句与导语句各显示一次，三段互相包含只留一份', () => {
  const headline = '科技资本开支继续上修，算力链条订单能见度延长。';
  const summary = `${headline}资金沿算力、散热、电力扩散。需注意：拥挤度同步抬升；后续看下批云厂商指引。`;
  const view = buildFocusView({
    dominantEvent: '财报季 · 科技资本开支验证',
    headline,
    summary,
    marketSummary: summary,
    dominantEvents: [],
  });
  assert.deepEqual(plain(view.lead.preview), [headline, '资金沿算力、散热、电力扩散。']);
  assert.deepEqual(plain(view.lead.more), ['需注意：拥挤度同步抬升；', '后续看下批云厂商指引。']);
});

test('事件段落之前的正文才是导语；和事件重复的句子不进导语', () => {
  const cycle = productionCycle(EVENT_TEXTS.slice(0, 3), 3);
  const intro = '本轮热点集中在并购与监管审批。三起交易均已公告。';
  cycle.summary = `${intro}\n${cycle.summary}`;
  const view = buildFocusView(cycle);
  assert.deepEqual(plain(view.lead.preview), ['本轮热点集中在并购与监管审批。', '三起交易均已公告。']);
  assert.deepEqual(view.lead.more, []);
  assert.equal(view.events.length, 3);
  assert.deepEqual(view.extraEvents, []);
});

test('与标题相同的摘要不再重复（「当前暂无可展示热点」）', () => {
  const view = buildFocusView({
    dominantEvent: '当前暂无可展示热点',
    headline: '当前暂无可展示热点。',
    summary: '当前暂无可展示热点。',
    marketSummary: '当前暂无可展示热点。',
    dominantEvents: [],
  });
  assert.deepEqual(view, { lead: null, events: [], extraEvents: [] });
});

test('总摘要被 3000 字截断时，截断的最后一段补省略号', () => {
  const events = Array.from({ length: 20 }, (_, i) => `（${i + 1}）第${i + 1}家公司公告回购计划，规模${i + 1}亿美元，${'资金来自经营现金流。'.repeat(18)}`);
  const joined = events.join('\n').slice(0, 3000);
  assert.ok(joined.endsWith('资金来自经营现金'), '样本必须截在半句处');
  const view = buildFocusView({
    dominantEvent: '市场热点分析',
    headline: joined,
    summary: joined,
    marketSummary: joined,
    dominantEvents: events.slice(0, 8).map((summary, i) => ({ eventGroupId: `e${i}`, summary, affectedSectors: [] })),
  });
  const last = view.extraEvents.at(-1);
  const points = [last.title, last.preview, ...last.more].filter(Boolean);
  assert.match(points.at(-1).text, /…$/);
  assert.ok(!view.extraEvents.slice(0, -1).some((event) => [event.title, event.preview, ...event.more].filter(Boolean).at(-1).text.endsWith('…')));
});
