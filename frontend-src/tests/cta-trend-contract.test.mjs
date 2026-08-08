/**
 * CTA 趋势资金卡的语义契约（2026-08-08）。
 *
 * 钉住 brief 的产品红线：
 * - 一律「代理估算/模型触发位」措辞，绝不出现「必买/必卖/真实仓位」类断言；
 * - 仓位与资金流分开呈现，且趋势流/波动率流拆分可见；
 * - 数据不足显示诚实空态，不折中性值；
 * - 触发位需收盘确认，盘中穿越只挂暂定章；
 * - CTA 读数不得混入 market_regime（只做并排联动解释）。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const panel = readFileSync(join(src, 'components/market/CtaTrendPanel.tsx'), 'utf8');
const marketPage = readFileSync(join(src, 'pages/Market.tsx'), 'utf8');
const marketModule = readFileSync(join(src, 'api/modules/market.ts'), 'utf8');
const hints = readFileSync(join(src, 'lib/ctaHints.ts'), 'utf8');
const backendConfig = readFileSync(
  join(src, '..', '..', 'backend/app/services/cta/config.py'),
  'utf8',
);

test('措辞红线：代理估算语义在场，禁用词绝迹', () => {
  assert.match(panel, /代理模型估算，非任何机构真实仓位披露/);
  assert.match(panel, /基于 ETF 趋势的代理估算/);
  for (const forbidden of ['必买', '必卖', 'CTA 真实仓位', '确定会买入']) {
    assert.ok(!panel.includes(forbidden), `禁用措辞出现：${forbidden}`);
    assert.ok(!hints.includes(forbidden), `hints 出现禁用措辞：${forbidden}`);
  }
});

test('仓位与资金流分开：拆分展示 + 前值', () => {
  assert.match(panel, /趋势 \{a\} · 波动率 \{b\}/);
  assert.match(panel, /前值 \{v\}/);
  assert.match(panel, /volatility_flow/);
});

test('数据不足诚实空态，不折中性', () => {
  assert.match(panel, /历史长度不足（\{bars\}\/\{req\} 根），未生成估算——不以中性值代替/);
  assert.ok(!/position_score \?\? 0/.test(marketModule), '仓位 null 不得折 0');
  assert.match(marketModule, /position_score: pickN\(rec, 'position_score'\)/);
});

test('触发位需收盘确认，盘中穿越只挂暂定章', () => {
  assert.match(panel, /模型触发位（需收盘确认）/);
  assert.match(panel, /盘中已穿越 · 待收盘确认/);
  assert.match(panel, /crossed_zone_ids\.includes\(zone\.id\)/);
  assert.match(panel, /盘中读数为暂定，不入正式历史/);
});

test('触发标签按当前仓位动态（回补/重新加多/恢复加仓齐备）', () => {
  for (const key of ['short_cover', 'reopen_long', 'add_long', 'buy_accelerate', 'trim_long', 'sell_accelerate']) {
    assert.ok(panel.includes(`${key}:`) || panel.includes(`'${key}'`), `缺少触发标签 ${key}`);
  }
});

test('CTA 只做并排联动，不混入 regime 或 Strength', () => {
  assert.match(marketPage, /<CtaTrendPanel/);
  assert.match(marketPage, /regimeMean=\{mean\}/);
  // regimeMean 只读传入面板；页面的 bias/mean 计算不引用 CTA 数据。
  assert.ok(!/ctaQ\.data/.test(marketPage.split('const bias')[1]?.split('return (')[0] ?? ''), 'bias 推导不得读 CTA');
  const regimePanel = readFileSync(join(src, 'components/market/RegimePanel.tsx'), 'utf8');
  assert.ok(!regimePanel.includes('Cta'), 'RegimePanel 不得引用 CTA');
});

test('快照只读：marketGet /market/cta，无直连供应商路径', () => {
  assert.match(marketModule, /marketGet\('\/market\/cta'/);
});

test('情景双曲线（完整敞口 vs 波动率冻结）都在图上', () => {
  assert.match(panel, /完整敞口（含波动率调整）/);
  assert.match(panel, /仅趋势（波动率冻结）/);
  assert.match(panel, /trend_only/);
});

test('hints 数字与后端 config 一致（镜子）', () => {
  assert.match(backendConfig, /TARGET_VOL_ANNUAL = 0\.15/);
  assert.match(backendConfig, /SCENARIO_SPAN_PCT = 0\.12/);
  assert.match(backendConfig, /TRIGGER_CLUSTER_ATR = 0\.40/);
  assert.match(backendConfig, /AGREEMENT_DIVERGENT = 0\.55/);
  assert.match(backendConfig, /SUBMODEL_ACTIVE_EPS = 0\.10/);
  assert.ok(hints.includes('0.4×ATR'));
  assert.ok(hints.includes('±12%'));
  assert.ok(hints.includes('55%'));
  assert.ok(hints.includes('0.1'));
  assert.ok(hints.includes('快 30% / 中 40% / 慢 30%'));
  assert.match(backendConfig, /SubmodelSpec\(\s*"fast", "快速（≈1 个月）", 0\.30/);
  assert.match(backendConfig, /SubmodelSpec\(\s*"medium", "中速（≈3 个月）", 0\.40/);
  assert.match(backendConfig, /"tsmom", 0\.40, horizon=21/);
  assert.ok(hints.includes('21/63/252'));
  assert.ok(hints.includes('8/24、16/48、32/96'));
});
