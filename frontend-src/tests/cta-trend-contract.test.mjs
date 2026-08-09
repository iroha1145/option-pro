/**
 * CTA 趋势资金的语义契约（2026-08-08；同日随 /cta 独立页改造更新路径）。
 *
 * 钉住 brief 的产品红线：
 * - 一律「代理估算/模型触发位」措辞，绝不出现「必买/必卖/真实仓位」类断言；
 * - 仓位与资金流分开呈现，且趋势流/波动率流拆分可见；
 * - 数据不足显示诚实空态，不折中性值；
 * - 触发位需收盘确认，盘中穿越只挂暂定章；
 * - CTA 读数不得混入 market_regime（只做并排联动解释）。
 *
 * 面板已从 src/components/market/CtaTrendPanel.tsx 迁移至 src/components/cta/**
 * 并挂载在独立页 src/pages/CtaTrend.tsx（/cta）；大盘页只留 /cta 引导卡。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
/* 旧面板已拆分为 cta/ 目录多个组件：语义断言面向它们的拼接体 */
const panel = [
  'components/cta/ctaMeta.ts',
  'components/cta/CtaDeepDive.tsx',
  'components/cta/TriggerLadder.tsx',
  'components/cta/ScenarioChart.tsx',
  'components/cta/CtaOverviewStrip.tsx',
  'components/cta/PositionHistoryChart.tsx',
]
  .map((f) => readFileSync(join(src, f), 'utf8'))
  .join('\n');
const ctaPage = readFileSync(join(src, 'pages/CtaTrend.tsx'), 'utf8');
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

test('断点区需收盘确认，盘中穿越只挂暂定章', () => {
  assert.match(panel, /模型断点区（需收盘确认）/);
  assert.match(panel, /盘中已穿越 · 待收盘确认/);
  assert.match(panel, /crossed_zone_ids/);
  assert.match(panel, /\.includes\(zone\.id\)/);
  assert.match(panel, /盘中读数为暂定，不入正式历史/);
});

test('v3 标签 = 真实状态迁移：翻仓键在场，rank 深度标签绝迹', () => {
  for (const key of [
    'short_cover', 'flip_to_long', 'reopen_long', 'add_long',
    'trim_long', 'flip_to_short', 'reopen_short', 'add_short',
    /* 冲突标签：「转多/转空」只在趋势读数过零时用；饱和途中用 firming/fading */
    'trend_up_vol_dominates', 'trend_firming_vol_dominates',
    'trend_down_vol_dominates', 'trend_fading_vol_dominates',
  ]) {
    assert.ok(panel.includes(`${key}:`) || panel.includes(`'${key}'`), `缺少触发标签 ${key}`);
  }
  /* 审计 v3：Δ 变小仍叫「加速」、两端同号仍叫「翻空」的 rank 标签已撤销 */
  for (const retired of ['buy_accelerate', 'add_further', 'sell_accelerate', 'flip_further']) {
    assert.ok(!panel.includes(retired), `已撤销的深度标签仍在：${retired}`);
  }
});

test('v3 kind 细分：过零/饱和分开标注，饱和不冒充翻转', () => {
  for (const kind of ['trend_cross', 'trend_saturation', 'trend_cross_and_saturation']) {
    assert.ok(panel.includes(`${kind}:`), `缺少 kind ${kind}`);
  }
  assert.match(panel, /趋势信号过零/);
  assert.match(panel, /趋势信号饱和/);
  assert.ok(!panel.includes('趋势模型翻转'), 'v2 的统称「趋势模型翻转」不得再出现');
  /* 状态迁移四元组入 UI：标签可当场核验 */
  assert.match(panel, /仓位 \{a\} → \{b\}/);
  assert.match(panel, /position_before/);
  /* 垫衬贴现价时区分缓冲边界与真实阈值 */
  assert.match(panel, /nearest_event_distance_pct/);
  assert.match(panel, /最近断点 \{v\}%/);
  /* 方向三角中性化：价格位置不用涨绿跌红（红绿只留净 Δ） */
  assert.match(panel, /border-b-ink-400/);
  assert.match(panel, /border-t-ink-400/);
  assert.ok(!panel.includes('border-b-up-600'), '上方三角不得再用 up 色');
  assert.ok(!panel.includes('border-t-down-600'), '下方三角不得再用 down 色');
});

test('v3 同向计数走后端下发（前端阈值只作旧快照回退）', () => {
  assert.match(panel, /aligned_models/);
  assert.match(panel, /active_models/);
  assert.match(panel, /权重 \{p\}%/);
  /* 落后（非「晚于」）于最近交易日的措辞修正 */
  assert.match(panel, /落后于最近交易日/);
  assert.ok(!panel.includes('晚于最近交易日'));
  /* 标的名按 instrument 键映射，不再拿后端中文 label 当 i18n 键 */
  assert.match(panel, /instrumentName\(row\.instrument/);
});

test('v2 读数拆解与新鲜度：强度/覆盖/最新交易日/快照时刻/换标的重挂', () => {
  assert.match(panel, /trend_strength/);
  assert.match(panel, /active_model_weight/);
  assert.match(panel, /market_data_current/);
  assert.match(panel, /已是最新交易日/);
  /* 触发阶梯换标的必须重挂：区间 id（above-1…）每只都复用 */
  assert.match(panel, /<TriggerLadder key=\{row\.instrument\}/);
  /* 页头时间 = 快照落盘时刻，不是浏览器请求时刻 */
  assert.match(ctaPage, /snapshot_saved_at/);
  assert.match(ctaPage, /快照 \{time\}/);
  assert.doesNotMatch(ctaPage, /\{t\('更新'\)\} \{fmtTimeHHMMSS\(ctaQ\.lastUpdatedAt\)\}/);
});

test('CTA 只做并排联动，不混入 regime 或 Strength', () => {
  /* 旧面板已剥离：大盘页不再渲染 <CtaTrendPanel>、不再轮询 ctaTrend，
     只留指向 /cta 的引导卡。 */
  assert.doesNotMatch(marketPage, /CtaTrendPanel/);
  assert.doesNotMatch(marketPage, /ctaQ/);
  assert.match(marketPage, /to="\/cta"/);
  /* regimeMean 只读传入深读面板；页面的 bias/mean 计算不引用 CTA 数据。 */
  assert.match(ctaPage, /regimeMean=\{mean\}/);
  assert.match(ctaPage, /marketApi\.ctaTrend\(\)/);
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

test('120 日仓位历史已接入独立页（原面板未使用的 history 字段）', () => {
  assert.match(panel, /仓位历史/);
  assert.match(panel, /history\.map/);
  assert.match(panel, /markArea/);
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
