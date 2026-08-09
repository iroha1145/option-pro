/**
 * CTA 趋势资金估算的共享语义映射（/cta 页各组件唯一来源，勿再复制漂移）。
 *
 * 语义纪律（与后端 cta-proxy 模型一一对应）：
 * - 这是**代理估算**：多周期趋势模型群的机械仓位，不是任何机构的真实仓位
 *   披露，也不输出美元流量——文案与标签一律用「估算/代理/模型触发位」。
 * - 「仓位」与「资金流」分开呈现：净多但在减仓 ≠ 翻空；波动率去杠杆
 *   （trend_flow / volatility_flow 拆分）单独标注。
 * - 触发位全部「需收盘确认」；盘中穿越只挂暂定章。
 * - 数据缺失显示诚实空态，不折中性值；快照未发布 → 说明 + 等 worker。
 * - 不改变 market_regime / Strength 排名；与市场环境只做并排联动解释。
 */
import { t } from '../../i18n/core.ts';

/* 定义处 t()（RESULT_META 惯例）；渲染处直接用，不二次翻译 */
export const POSITION_META: Record<string, { label: string; cls: string }> = {
  strong_long: { label: t('强净多'), cls: 'bg-up-50 text-up-700' },
  net_long: { label: t('净多'), cls: 'bg-up-50 text-up-700' },
  divergent: { label: t('模型分歧'), cls: 'bg-warn-50 text-warn-600' },
  neutral: { label: t('中性'), cls: 'bg-paper-2 text-ink-600' },
  net_short: { label: t('净空'), cls: 'bg-down-50 text-down-700' },
  strong_short: { label: t('强净空'), cls: 'bg-down-50 text-down-700' },
};

export const FLOW_META: Record<string, string> = {
  long_add: t('多头加仓'),
  long_trim: t('多头减仓'),
  short_add: t('空头加仓'),
  short_cover: t('空头回补'),
  rebuilding: t('重新建多'),
  reducing: t('转向减持'),
  steady: t('边际持稳'),
};

/* v3：标签由区间前后**真实状态迁移**生成（后端下发 position_before/after）——
   「翻空/翻多」必须真穿越中性带；rank 深度标签「加速/进一步翻空」已撤销
   （GPT-5.6-Pro 审计：SPY 下方第二层 Δ 比第一层还小却叫「卖盘加速」）。 */
export const ZONE_LABELS: Record<string, string> = {
  short_cover: t('空头回补'),
  flip_to_long: t('翻为多头'),
  reopen_long: t('重新加多'),
  add_long: t('多头加仓'),
  trim_long: t('多头减仓'),
  flip_to_short: t('翻为空头'),
  reopen_short: t('重新加空'),
  add_short: t('空头加仓'),
  /* 冲突标签：趋势变化与净变化相反或被波动率项吃平。「转多/转空」只在趋势
     读数真正过零时使用；已在同向途中继续增强/走弱用 firming/fading 变体，
     饱和途中的信号不再冒充「转向」（审计 v3）。 */
  trend_up_vol_dominates: t('趋势转多·波动率去杠杆占优'),
  trend_firming_vol_dominates: t('趋势增强·波动率去杠杆占优'),
  trend_down_vol_dominates: t('趋势转空·波动率回杠杆占优'),
  trend_fading_vol_dominates: t('趋势走弱·波动率回杠杆占优'),
};

/* v3：底层事件本就分过零与饱和——饱和沿是「分量停止继续变化」的价格，
   不是新一轮买卖被触发；统称「翻转」已撤销（trend_flip 仅旧快照兜底）。 */
export const ZONE_KIND: Record<string, string> = {
  trend_cross: t('趋势信号过零'),
  trend_saturation: t('趋势信号饱和'),
  trend_cross_and_saturation: t('趋势过零+饱和'),
  vol_delever: t('波动率去杠杆'),
  mixed: t('趋势+波动率'),
  trend_flip: t('趋势信号变化'),
};

/* 标的显示名按 instrument 键映射（audit：后端中文 label 直渲染会漏翻——
   英文界面出现「纳斯达克 100」残留；不再依赖后端显示名当 i18n 键）。 */
export const INSTRUMENT_NAMES: Record<string, string> = {
  sp500: t('标普 500'),
  nasdaq100: t('纳斯达克 100'),
  russell2000: t('罗素 2000'),
  dow: t('道琼斯'),
};

export const instrumentName = (instrument: string, fallback: string): string =>
  INSTRUMENT_NAMES[instrument] ?? t(fallback);

export const MODEL_SHORT: Record<string, string> = {
  fast: t('快'),
  medium: t('中'),
  slow: t('慢'),
};

export const signed = (v: number | null, digits = 1): string =>
  v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;
