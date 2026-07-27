/**
 * 宏观适配的展示层规则（纯函数，可单测）。
 *
 * 一条底线贯穿全文件：**没读到 ≠ 中性**。后端在覆盖度不足时返回 null，而不是 50；
 * 这里也就必须把 null 一路当「无」传下去，不能在任何地方兜成 50 或「中性」。
 *
 * 顺风/中性/逆风的分界线来自后端 linkage.py 的 TAILWIND_STRONG / TAILWIND_WEAK。
 * 后端已经把标签算好随字段一起下发，所以正常路径直接用它的；本地分档只在
 * 后端没给标签、但给了分数时用来兜底，且分界线和它一致（有测试盯着）。
 *
 * 本文件刻意保持零依赖（tests/macro-fit-presentation.test.mjs 断言这一点：沙箱
 * 不给 require，加了导入会立刻报错）。这里的字符串因此保持原文中文，翻译发生在
 * 各消费处渲染时（如 MacroFitBadge.tsx / MacroFitPanel.tsx 对这些导出值调用 t()）。
 */

/** 一个宏观驱动因素：后端连同因子 id 一起下发中文名，前端不再自己映射一份。 */
export interface MacroFitDriver {
  factor_id: string;
  label: string;
}

export type MacroTone = 'tailwind' | 'neutral' | 'headwind';

/** 与后端 linkage.py 同源的分界线；tests/macro-fit-presentation 断言两边一致。 */
export const MACRO_TAILWIND_STRONG = 65;
export const MACRO_TAILWIND_WEAK = 35;

export const MACRO_TONE_LABEL: Record<MacroTone, string> = {
  tailwind: '顺风',
  neutral: '中性',
  headwind: '逆风',
};

/** 分数 → 分档。null 返回 null：说不出来就是说不出来。 */
export function macroTone(score: number | null | undefined): MacroTone | null {
  if (typeof score !== 'number' || !Number.isFinite(score)) return null;
  if (score >= MACRO_TAILWIND_STRONG) return 'tailwind';
  if (score <= MACRO_TAILWIND_WEAK) return 'headwind';
  return 'neutral';
}

/** 后端下发的中文标签优先；只有它缺失时才用本地分档兜底。 */
export function macroToneOf(
  score: number | null | undefined,
  backendLabel: string | null | undefined,
): MacroTone | null {
  for (const tone of ['tailwind', 'neutral', 'headwind'] as MacroTone[]) {
    if (backendLabel === MACRO_TONE_LABEL[tone]) return tone;
  }
  return macroTone(score);
}

export const MACRO_TONE_CLASS: Record<MacroTone, string> = {
  tailwind: 'border-up-600/30 bg-up-50 text-up-700',
  neutral: 'border-line-strong bg-card-warm text-ink-500',
  headwind: 'border-down-600/30 bg-down-50 text-down-700',
};

/**
 * 技术 × 宏观的二维状态。
 *
 * 增量任务里明确要求的是**二维**，不是一个混合分：技术强而宏观弱、宏观先改善而
 * 价格没跟上，这两种才是值得看的，混成一个数正好把它们藏掉。
 *
 * 阈值只用于措辞，不参与任何评分；两边任一缺失就不给状态，不猜。
 */
export type MacroQuadrant =
  | 'both_strong'
  | 'price_ahead'
  | 'macro_ahead'
  | 'both_weak';

/**
 * 措辞刻意**不用**顺风/逆风。
 *
 * 象限在 50 分处一分为二，而顺风/逆风的分界线是 65 / 35 —— 两套线不一样。一个 53 分
 * 的读数在徽标上是「中性」，如果象限同时说它「宏观顺风」，同一个数字在同一屏上
 * 就有两种说法（实测 53.2 就是这样）。所以象限用偏强/偏弱，说的是它真正在说的事：
 * 相对 50 的哪一边。
 */
export const MACRO_QUADRANT_LABEL: Record<MacroQuadrant, string> = {
  both_strong: '技术偏强 · 宏观偏强',
  price_ahead: '技术偏强 · 宏观偏弱',
  macro_ahead: '技术偏弱 · 宏观偏强',
  both_weak: '技术偏弱 · 宏观偏弱',
};

export const MACRO_QUADRANT_HINT: Record<MacroQuadrant, string> = {
  both_strong: '趋势与环境共振',
  price_ahead: '技术上涨，宏观基础偏弱',
  macro_ahead: '宏观先行改善，等待技术修复',
  both_weak: '广义风险收缩',
};

/** 象限的分界口径，写在界面上，免得读者拿它和顺风/逆风的线混起来。 */
export const MACRO_QUADRANT_NOTE = '象限以 50 分为界；顺风/逆风的分界线是 65 / 35，两者不同。';

/**
 * 象限的分界线。50 —— 两侧都是 0–100 的分数，中值就是 50。
 *
 * 这条线和顺风/逆风的 65/35 不是一回事，所以象限的措辞不复用那两个词，见上面。
 */
const QUADRANT_MIDPOINT = 50;

export function macroQuadrant(
  technical: number | null | undefined,
  macro: number | null | undefined,
): MacroQuadrant | null {
  if (typeof technical !== 'number' || !Number.isFinite(technical)) return null;
  if (typeof macro !== 'number' || !Number.isFinite(macro)) return null;
  const strongPrice = technical >= QUADRANT_MIDPOINT;
  const strongMacro = macro >= QUADRANT_MIDPOINT;
  if (strongPrice) return strongMacro ? 'both_strong' : 'price_ahead';
  return strongMacro ? 'macro_ahead' : 'both_weak';
}

/**
 * 技术领先宏观多少分。正数＝价格跑在环境前面。
 *
 * 后端已经算了 macro_technical_gap（技术市场适配 − 结构性宏观），这个函数只在
 * 后端没下发时按同一定义补算，用于板块页的技术强度 vs 宏观适配。
 */
export function macroGap(
  technical: number | null | undefined,
  macro: number | null | undefined,
): number | null {
  if (typeof technical !== 'number' || !Number.isFinite(technical)) return null;
  if (typeof macro !== 'number' || !Number.isFinite(macro)) return null;
  return Math.round((technical - macro) * 10) / 10;
}

/**
 * 驱动因素列表 → 「流动性、实际利率」；空列表返回 null，让调用方决定不渲染。
 * `translate` 可选：逐个因子名过一遍（比如界面语言翻译），不传就原样透传——
 * 保持这个函数本身零依赖，翻译函数由调用方传入而不是在这里 import。
 */
export function driverText(
  drivers: MacroFitDriver[] | null | undefined,
  translate: (label: string) => string = (label) => label,
): string | null {
  if (!drivers || drivers.length === 0) return null;
  const names = drivers.map((d) => translate(d.label || d.factor_id)).filter(Boolean);
  return names.length > 0 ? names.join('、') : null;
}

/**
 * 「为什么没有分数」的人话。
 *
 * 三种情况后端分得清（macro_shadow_status），前端也不能把它们并成一句「暂无」——
 * 没有快照、这只票不在主题表里、这个板块的暴露看不够，是三件不同的事。
 */
export const MACRO_MISSING_REASON: Record<string, string> = {
  unavailable: '宏观模块未启用',
  macro_module_unavailable: '宏观模块未启用',
  macro_snapshot_unavailable: '暂无宏观快照',
  sector_unclassified: '该标的未归入板块，无暴露画像',
  exposure_coverage_low: '该板块暴露观测不足，不给分',
};

export function macroMissingReason(status: string | null | undefined): string | null {
  if (!status || status === 'ok') return null;
  return MACRO_MISSING_REASON[status] ?? '暂无宏观读数';
}

/**
 * 口径说明（InfoHint 的 ScoreHint 形状）。
 *
 * 三件事必须说清楚，因为界面上一个 0–100 的分数默认会被当成评分的一部分：
 * 它是**板块级**暴露画像、它是历史分位而不是预测、它**不参与排名**。
 */
export const MACRO_SHADOW_HINT = {
  title: '宏观适配（0–100）',
  body:
    '当前宏观环境与该股票所属板块暴露画像的匹配度：把每个宏观因子的历史分位中心化后，'
    + '按该板块对这个因子的确定性暴露加权。65 以上记顺风，35 以下记逆风。',
  note:
    '影子字段：不参与排名，不改变强度分、突破质量分或事件生命周期。覆盖度不足时不给分，'
    + '也不按中性 50 计。分数是历史分位，不是预测。',
};

/** 同一句话的纯文本版，用于原生 title 属性（那里只能放字符串）。 */
export const MACRO_SHADOW_TITLE_ATTR =
  `${MACRO_SHADOW_HINT.title}：${MACRO_SHADOW_HINT.body}${MACRO_SHADOW_HINT.note}`;
