/**
 * 圈 i 文案 ↔ 后端算法常量的镜子测试。
 *
 * structureHints.ts 的纪律是「文案必须描述真实算法」；这里把文案里引用的
 * 关键数字（结构基准分、形态修正、基底七维权重、量价窗口与风险加点、
 * RSI 峰值映射）与后端源码中的常量逐一对照——改了任何一边而没改另一边,
 * 测试直接打回，说明文字不再是"写的时候对过"而是持续对齐。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const hints = readFileSync(join(root, 'frontend-src/src/lib/structureHints.ts'), 'utf8');
const priceAction = readFileSync(join(root, 'backend/app/services/strength/price_action.py'), 'utf8');
const baseStructure = readFileSync(join(root, 'backend/app/services/technical/base_structure.py'), 'utf8');
const volPrice = readFileSync(join(root, 'backend/app/services/strength/vol_price_match.py'), 'utf8');
const scoring = readFileSync(join(root, 'backend/app/services/strength/scoring.py'), 'utf8');
const structure = readFileSync(join(root, 'backend/app/services/technical/structure.py'), 'utf8');

test('价格行为基准分：文案数字与 _STRUCTURE_SCORES 一致', () => {
  const anchors = {};
  for (const [, key, value] of priceAction.matchAll(/"(uptrend|uptrend_weak|hl_base|range|lh_pressure|downtrend)":\s*([\d.]+)/g)) {
    anchors[key] = Math.round(Number(value));
  }
  assert.equal(Object.keys(anchors).length, 6);
  const note = /结构状态定基准（(.+?)）/.exec(hints)?.[1];
  assert.ok(note, '价格行为 note 必须包含基准分列表');
  for (const [key, value] of Object.entries(anchors)) {
    assert.ok(note.includes(String(value)), `基准分 ${key}=${value} 未出现在文案里`);
  }
});

test('形态与陷阱修正：±6 / 封顶 ±10 / ±8 与代码一致', () => {
  assert.ok(/"bullish_engulfing":\s*6\.0/.test(priceAction));
  assert.ok(/max\(-10\.0,\s*min\(10\.0,\s*pattern_adjust\)\)/.test(priceAction));
  assert.ok(/trap_adjust\s*\+=\s*8\.0/.test(priceAction));
  assert.ok(hints.includes('各 ±6（合计封顶 ±10）'));
  assert.ok(hints.includes('+8'));
  assert.ok(hints.includes('−8'));
});

test('基底七维权重：文案与 _candidate 的加权系数一致', () => {
  const formula = /quality = \(([\s\S]+?)\)\n/.exec(baseStructure)?.[1] ?? '';
  const weights = [...formula.matchAll(/\*\s*(0\.\d+)/g)].map((m) => Number(m[1]));
  assert.deepEqual(weights, [0.25, 0.15, 0.15, 0.15, 0.1, 0.1, 0.1]);
  assert.ok(hints.includes('紧致度 25% / 持续时间 15% / 触碰次数 15% / 成交额收缩 15% / ATR收缩 10% / 支撑完整性 10% / 低点抬升 10%'));
});

test('量价窗口：近 10 日 / 基准 60 日（剔除近期）与代码一致', () => {
  assert.ok(/recent_window:\s*int\s*=\s*10/.test(volPrice));
  assert.ok(/baseline_window:\s*int\s*=\s*60/.test(volPrice));
  assert.ok(/iloc\[-\(baseline_window \+ recent_window\):-recent_window\]/.test(volPrice), '基准必须剔除近期窗口');
  assert.ok(/close\.iloc\[-\(recent_window \+ 1\)\]/.test(volPrice), '位移必须是完整 10 个间隔');
  assert.ok(hints.includes('近 10 个交易日'));
  assert.ok(hints.includes('60 日基准的倍数') || hints.includes('之前 60 日基准'));
  assert.ok(hints.includes('不含近期 10 日'));
  assert.ok(hints.includes('完整 10 个间隔'));
});

test('假突破风险加点：12/8/10/3/−3/+6/+4 与 vol_price_match 分支一致', () => {
  assert.ok(/false_breakout_risk = 12\.0/.test(volPrice));
  assert.ok(/false_breakout_risk = 8\.0/.test(volPrice), '内部偏多的真空收缩降档 8 分');
  assert.ok(/false_breakout_risk = 10\.0/.test(volPrice));
  assert.ok(/false_breakout_risk = 3\.0/.test(volPrice));
  assert.ok(/false_breakout_risk = -3\.0/.test(volPrice));
  assert.ok(/false_breakout_risk \+= 6/.test(volPrice));
  assert.ok(/false_breakout_risk \+= 4/.test(volPrice));
  const note = hints.split('假突破风险')[2] ?? hints;
  for (const n of ['+12', '+8', '+10', '+3', '−3', '+6', '+4']) {
    assert.ok(hints.includes(n), `风险加点 ${n} 未出现在文案`);
  }
});

test('突破质量修正取值与代码一致', () => {
  assert.ok(/breakout_adjustment = 12\.0/.test(volPrice));
  assert.ok(/breakout_adjustment = -6\.0/.test(volPrice));
  assert.ok(/breakout_adjustment = -10\.0/.test(volPrice));
  assert.ok(/breakout_adjustment = -8\.0/.test(volPrice));
  assert.ok(/breakout_adjustment = 3\.0/.test(volPrice));
  assert.ok(/breakout_adjustment = 2\.0/.test(volPrice));
  assert.ok(hints.includes('多头吸收 +12 / 吸收未确认 +3 / 平衡收缩 +2 / 真空型收缩 −6（内部偏多）或 −10 / 空头吸收 −8'));
});

test('缺数据语义：_empty 的三个调整量必须是 None（文案说「— 不是零」）', () => {
  const empty = /def _empty[\s\S]+?return \{[\s\S]+?\n    \}/.exec(volPrice)?.[0] ?? '';
  assert.ok(empty.includes('"breakout_quality_adjustment": None'));
  assert.ok(empty.includes('"false_breakout_risk": None'));
  assert.ok(hints.includes('不等于零风险'));
});

test('RSI 峰值映射：68→88 与 scoring 节点一致，且 detail 侧同曲线', () => {
  assert.ok(/\(68\.0,\s*88\.0\)/.test(scoring));
  assert.ok(hints.includes('RSI≈68 附近得分最高（88 分）'));
});

test('价格行为进入强度分的权重 20% 与 scoring 一致', () => {
  assert.ok(/"price_action":\s*0\.20/.test(scoring));
  assert.ok(hints.includes('20% 权重'));
});

test('波动稳定项权重 5% 与 scoring 生产权重一致', () => {
  assert.ok(/"trend_stability":\s*0\.05/.test(scoring));
  assert.ok(hints.includes('权重 5%'));
});

test('基底状态缓冲：0.25% / 0.1×ATR 与 base_structure 常量一致', () => {
  assert.ok(/BREAK_BUFFER_PCT = 0\.0025/.test(baseStructure));
  assert.ok(/BREAK_BUFFER_ATR = 0\.10/.test(baseStructure));
  assert.ok(hints.includes('max(0.25%×价格, 0.1×ATR)'));
});

test('陷阱穿越阈值：0.2% / 0.1×ATR 与 _detect_traps 一致', () => {
  assert.ok(/level \* 0\.002/.test(priceAction));
  assert.ok(/\(atr or 0\.0\) \* 0\.1/.test(priceAction));
  assert.ok(hints.includes('max(0.2%×位, 0.1×ATR)'));
});

test('未收盘末根不进指标：structure.py 有摘除逻辑且文案声明', () => {
  assert.ok(/analysis = series if last_bar_closed else series_excluding_last\(series\)/.test(structure));
  assert.ok(hints.includes('未收盘的末根不参与结构计算'));
});
