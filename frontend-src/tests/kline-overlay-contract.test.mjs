/**
 * K 线叠加与显示口径的源码契约（2026-08-08 深修批）。
 *
 * 钉住的行为：
 * - 阻力带/失效位不得从图最左贯穿全史：必须以 base_start 定位起点；
 * - 结构负载与图表 bars 错版本时暂隐叠加（overlaysConsistentWithBars 闸门）；
 * - tooltip 同时给「开→收」与「较前收」两个涨跌口径；
 * - MA20 只吃常规时段、非仅报价 bar（与后端 moving_average_scope 同口径）；
 * - 面积图不再画全史最小二乘趋势线（拟合域≠显示域）；
 * - 页脚显示末根自身时间，读取时刻另行标注；
 * - 量价缺数据时风险/修正显「—」，不折成 0。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const kline = readFileSync(join(src, 'components/detail/KlineChart.tsx'), 'utf8');
const stocksApi = readFileSync(join(src, 'api/modules/stocks.ts'), 'utf8');
const structurePanel = readFileSync(join(src, 'components/detail/StructurePanel.tsx'), 'utf8');
const detailApi = readFileSync(join(src, 'components/detail/api.ts'), 'utf8');

test('阻力带以 base_start/base_end 定位，不再全图贯穿', () => {
  assert.match(kline, /const startIndex = locateDay\(overlays\.base_start\)/);
  assert.match(kline, /const endIndex = locateDay\(overlays\.base_end\)/);
  assert.match(kline, /xAxis: startIndex/);
  assert.match(kline, /\{ xAxis: endIndex, yAxis: overlays\.resistance_high \}/);
  // 失效位是线段（起点=基底起点），不是 yAxis-only 的全宽线
  assert.match(kline, /coord: \[startIndex, overlays\.invalidation_price\]/);
});

test('基底失效时带子转灰并标注状态', () => {
  assert.match(kline, /const failed = overlays\.base_status === 'failed'/);
  assert.match(kline, /failed \? CH\.ink400 : CH\.brand400/);
  assert.match(kline, /阻力带（基底已失效）/);
});

test('结构与图表错版本时暂隐叠加（一致性闸门在 levelsAvailable 里）', () => {
  assert.match(kline, /function overlaysConsistentWithBars\(/);
  assert.match(kline, /overlaysConsistentWithBars\(technical, bars\)/);
  assert.match(kline, /levelsAvailable = overlays !== null && range === '1d' && mode === 'candle' && overlaysConsistent/);
  assert.match(kline, /days\.length - 1 - position <= 2/);
});

test('tooltip 同时提供开→收与较前收两个口径', () => {
  assert.match(kline, /t\('开→收'\)/);
  assert.match(kline, /t\('较前收'\)/);
  assert.match(kline, /const prev = idx > 0 \? bars\[idx - 1\] : null/);
});

test('MA20 只吃常规时段、非仅报价 bar', () => {
  assert.match(stocksApi, /if \(bar\.ext === true \|\| bar\.quote_only === true\) return null/);
  // ext 标记要从契约一路透传（否则前端根本看不到盘前盘后）
  assert.match(stocksApi, /\.\.\.\(b\.ext === true \? \{ ext: true \} : \{\}\)/);
});

test('面积图不再有全史最小二乘趋势线', () => {
  assert.ok(!/sxy/.test(kline), '最小二乘拟合应已删除');
  assert.ok(!kline.includes('intercept + slope * i'), '趋势线序列应已删除');
});

test('页脚显示末根自身时间，读取时刻另行标注', () => {
  assert.match(kline, /function lastBarText\(/);
  assert.match(kline, /t\('读取于 \{at\}'/);
  assert.match(kline, /共 \{n\} 根 · 末根 \{at\}\{status\}/);
});

test('图例是组件化的真图例并覆盖不一致态', () => {
  assert.match(kline, /function OverlayLegend\(/);
  assert.match(kline, /结构分析与当前 K 线数据版本不一致/);
  assert.match(kline, /MA20 · 最近 20 根常规时段收盘的均线/);
});

test('结构卡：量价缺数据显「—」，基底状态章与暂定语义在位', () => {
  assert.match(structurePanel, /const vpmMeasured = vpm\.status === 'active'/);
  assert.match(structurePanel, /vpmMeasured \? signedOr\(vpm\.false_breakout_risk\) : '—'/);
  assert.match(structurePanel, /vpmMeasured \? signedOr\(vpm\.breakout_quality_adjustment\) : '—'/);
  assert.match(structurePanel, /（盘中暂定）/);
  assert.match(structurePanel, /计算截至 \{date\} 收盘/);
});

test('映射层：调整量 null 不折 0，base_state 与 last_bar 均有守卫映射', () => {
  assert.ok(!/pickN\(vp, 'false_breakout_risk'\) \?\? 0/.test(detailApi), 'null 不得折成 0');
  assert.match(detailApi, /breakout_quality_adjustment: pickN\(vp, 'breakout_quality_adjustment'\)/);
  assert.match(detailApi, /const BASE_STATUSES = new Set/);
  assert.match(detailApi, /closed: lastBarRaw\.closed === true/);
});
