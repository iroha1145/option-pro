/**
 * 热点追踪正文部件（components/catalysts/FocusDigest.tsx）在 React 桩里渲染：
 * 核实徽标、板块芯片的「+N」（宽屏三个、窄屏两个）、展开按钮出现的条件与展开后的内容。
 * 文字是否被行数截断要靠浏览器实测，沙箱里 ref 不挂真实节点，这里只验纯数据决定的部分。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';
import { SHARED_UI_STUBS } from './helpers/shared-ui-stubs.mjs';
import { buildFocusView } from '../src/components/catalysts/focusText.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.resolve(here, '../src/components/catalysts/FocusDigest.tsx'), 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

const invokingJsx = {
  jsx: (type, props) => (typeof type === 'function' ? type(props) : { type, props }),
  jsxs: (type, props) => (typeof type === 'function' ? type(props) : { type, props }),
  Fragment: 'Fragment',
};
const stubT = (msgid, vars) => (vars ? msgid.replace(/\{(\w+)\}/g, (whole, key) => String(vars[key] ?? whole)) : msgid);

function load() {
  const runner = createReactStub();
  let serial = 0;
  runner.React.useLayoutEffect = runner.React.useEffect;
  runner.React.useId = () => runner.React.useRef(`id-${(serial += 1)}`).current;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require(id) {
      const modules = {
        react: runner.React,
        'react/jsx-runtime': invokingJsx,
        'framer-motion': { motion: new Proxy({}, { get: (_, key) => `motion.${String(key)}` }) },
        '@/components/icons': { default: 'Icon' },
        '@/components/shared/SoftBadge': { default: 'SoftBadge' },
        '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
        '../../i18n/core.ts': { t: stubT },
      };
      if (id in modules) return modules[id];
      if (id in SHARED_UI_STUBS) return SHARED_UI_STUBS[id];
      throw new Error(`Unexpected import ${id}`);
    },
  });
  return { runner, parts: module.exports };
}

function walk(node, visit) {
  if (Array.isArray(node)) return node.forEach((child) => walk(child, visit));
  if (!node || typeof node !== 'object' || !node.props) return;
  visit(node);
  for (const value of Object.values(node.props)) {
    if (value && typeof value === 'object' && (Array.isArray(value) || 'props' in value)) walk(value, visit);
  }
}
function findAll(tree, predicate) {
  const out = [];
  walk(tree, (node) => { if (predicate(node)) out.push(node); });
  return out;
}
function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(text).join('');
  return node.props ? text(node.props.children) : '';
}

const EVENTS = [
  '（一）公司类事件：甲公司宣布收购。已证实：公告一致。推断：影响有限。需注意：仍需审批。',
  '（二）行业类事件：云厂商上调资本开支。',
  '（三）宏观类事件：通胀回落。推断：降息预期升温。',
];

function view(sectors, verdicts = []) {
  return buildFocusView({
    dominantEvent: '市场热点分析',
    headline: EVENTS.join('\n'),
    summary: EVENTS.join('\n'),
    marketSummary: EVENTS.join('\n'),
    dominantEvents: EVENTS.map((summary, index) => ({ eventGroupId: `evt_${index}`, summary, affectedSectors: sectors[index] })),
    eventVerifications: verdicts,
  });
}

function renderEvents(focusView) {
  const { runner, parts } = load();
  const read = runner.mount(() => parts.FocusEventList({ events: focusView.events, extraEvents: focusView.extraEvents }));
  const items = () => findAll(read(), (node) => node.type === 'motion.li');
  return { read, items };
}

const toggleOf = (item) => findAll(item, (node) => node.type === 'button' && node.props['aria-expanded'] !== undefined)[0] ?? null;
const chips = (item) => findAll(item, (node) => node.type === 'SoftBadge');

test('核实结论显示成带图标的徽标，没有核实记录时不显示', () => {
  const { items } = renderEvents(view([['半导体'], ['光通信'], []], [
    { eventGroupId: 'evt_0', verdict: 'supported' },
    { eventGroupId: 'evt_1', verdict: 'contradicted' },
  ]));
  const [first, second, third] = items();
  assert.equal(chips(first)[0].props.tone, 'ok');
  assert.equal(text(chips(first)[0]), '已证实');
  assert.equal(chips(second)[0].props.tone, 'danger');
  assert.equal(text(chips(second)[0]), '有矛盾');
  assert.equal(chips(third).length, 0, '没有核实记录、没有板块时不出标签行');
});

test('板块宽屏放三个、窄屏放两个，其余折成「+N」，展开后全部显示', () => {
  const { items } = renderEvents(view([['半导体', '数据中心', '电力设备', '光通信', '能源'], ['AI芯片', '半导体', '数据中心'], ['美国国债']]));
  const [many, three, one] = items();
  const plus = (item) => chips(item).filter((chip) => /^\+\d+$/.test(text(chip)));
  assert.deepEqual(plus(many).map((chip) => [text(chip), chip.props.className.includes('max-sm:hidden') ? 'wide' : 'narrow', chip.props.title]), [
    ['+2', 'wide', '光通信、能源'],
    ['+3', 'narrow', '电力设备、光通信、能源'],
  ]);
  assert.ok(chips(many).find((chip) => text(chip) === '电力设备').props.className.includes('max-sm:hidden'), '第三个板块窄屏隐藏');
  assert.deepEqual(plus(three).map(text), ['+1'], '恰好三个时只有窄屏的「+1」');
  assert.deepEqual(plus(one), []);

  toggleOf(many).props.onClick();
  const opened = items()[0];
  assert.equal(toggleOf(opened).props['aria-expanded'], true);
  assert.deepEqual(chips(opened).map(text), ['半导体', '数据中心', '电力设备', '光通信', '能源']);
  assert.ok(chips(opened).every((chip) => !chip.props.className.includes('hidden')));
});

test('展开按钮：有收起的正文或板块才出现；只为窄屏折起的第三个板块时只在窄屏出现', () => {
  const { items } = renderEvents(view([['半导体'], ['AI芯片', '半导体', '数据中心'], []]));
  const [withMore, narrowOnly, plain] = items();
  assert.equal(toggleOf(withMore).props.className.includes('sm:hidden'), false);
  assert.equal(toggleOf(withMore).props['aria-label'], '展开');
  assert.ok(toggleOf(narrowOnly).props.className.includes('sm:hidden'), '只有窄屏的「+1」可展开');
  assert.equal(toggleOf(plain), null, '一句标题加一句正文、没有板块，不给按钮（被截断时由实测补上）');
});

test('导语：其余句子收在「展开」后面，按钮指向导语正文', () => {
  const { runner, parts } = load();
  const lead = { preview: [{ label: null, text: '第一句。' }, { label: '推断：', text: '第二句。' }], more: [{ label: '需注意：', text: '第三句。' }] };
  const read = runner.mount(() => parts.FocusLead({ lead }));
  const button = findAll(read(), (node) => node.type === 'button')[0];
  assert.equal(button.props['aria-expanded'], false);
  const body = findAll(read(), (node) => node.props.id === button.props['aria-controls'])[0];
  assert.match(text(body), /第一句。推断：第二句。/);
  assert.equal(findAll(read(), (node) => node.type === 'CollapsePresence')[0].props.open, false);
  button.props.onClick();
  assert.equal(findAll(read(), (node) => node.type === 'CollapsePresence')[0].props.open, true);
  assert.match(text(findAll(read(), (node) => node.type === 'button')[0]), /收起/);
});

test('逐股评估说明：有风险时给展开按钮，展开后列出风险', () => {
  const { runner, parts } = load();
  const read = runner.mount(() => parts.FocusAssessmentNote({ note: '订单能见度延长', risks: ['估值偏高', '指引落空'] }));
  const button = findAll(read(), (node) => node.type === 'button')[0];
  assert.ok(button);
  button.props.onClick();
  const panel = findAll(read(), (node) => node.type === 'CollapsePresence')[0];
  assert.equal(panel.props.open, true);
  assert.match(text(panel), /风险估值偏高指引落空/);
  const { runner: second, parts: secondParts } = load();
  const readPlain = second.mount(() => secondParts.FocusAssessmentNote({ note: '订单能见度延长', risks: [] }));
  assert.equal(findAll(readPlain(), (node) => node.type === 'button').length, 0);
  assert.equal(text(findAll(readPlain(), (node) => node.type === 'p')[0]), '订单能见度延长');
});
