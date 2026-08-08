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

export const ZONE_LABELS: Record<string, string> = {
  short_cover: t('空头回补'),
  reopen_long: t('重新加多'),
  add_long: t('恢复加仓'),
  buy_accelerate: t('买盘加速'),
  add_further: t('进一步加仓'),
  trim_long: t('多头减仓'),
  reopen_short: t('重新加空'),
  add_short: t('继续加空'),
  sell_accelerate: t('卖盘加速'),
  flip_further: t('进一步翻空'),
};

export const ZONE_KIND: Record<string, string> = {
  trend_flip: t('趋势模型翻转'),
  vol_delever: t('波动率去杠杆'),
  mixed: t('趋势+波动率'),
};

export const MODEL_SHORT: Record<string, string> = {
  fast: t('快'),
  medium: t('中'),
  slow: t('慢'),
};

export const signed = (v: number | null, digits = 1): string =>
  v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;
