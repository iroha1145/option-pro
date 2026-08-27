/**
 * 绘图工具与分析图层的悬停说明。
 *
 * 为什么单独一份：工具条上只有图标，图层菜单里只有一行标签，用户没有任何途径
 * 知道「Spring/Upthrust」「CLV」「pivot/invalidation」到底是什么、画出来的线该怎么读。
 *
 * 写作纪律：只描述**实现真正做的事**（口径来自 docs/chart-drawings.md 与各
 * 算法模块），不写效果承诺，更不写胜率——形态分数是几何质量，不是概率。
 */
import type { ScoreHint } from '@/lib/scoreHints';
import { t } from '../../../i18n/core.ts';

export const TOOL_HINTS: Record<string, ScoreHint> = {
  select: {
    title: t('选择'),
    body: t('点击选中图形，拖锚点改形状、拖本体整体移动；空白处点击取消选中。锁定的图形不能拖，隐藏的图形点不中。'),
  },
  horizontal: {
    title: t('水平线'),
    body: t('在价格主图上单击一次，画一条贯穿全图的水平价位线，右侧显示该价格标签。'),
  },
  segment: {
    title: t('趋势线'),
    body: t('两点定一条线段。按住 Shift 可把方向约束到水平、垂直或 45°。'),
  },
  ray: {
    title: t('射线'),
    body: t('两点定斜率，向右无限延伸，并裁剪在可见的价格网格内。'),
  },
  channel: {
    title: t('平行通道'),
    body: t('先两点定基准线，第三点定通道宽度；两条边始终保持平行。'),
  },
  rectangle: {
    title: t('矩形'),
    body: t('两点定一个区域，用来框住整理区或事件窗口；反向拖动会自动规范化。'),
  },
  fibonacci: {
    title: t('斐波那契'),
    body: t('回撤位 0 / 0.236 / 0.382 / 0.5 / 0.618 / 0.786 / 1，外加扩展位 1.272 / 1.618；由高到低、由低到高两个方向都成立。'),
  },
  text: {
    title: t('文字'),
    body: t('在图上留一条纯文本批注，最多 240 字；内容为空不会保存。'),
  },
};

export const LAYER_HINTS: Record<string, ScoreHint> = {
  ma20: { title: t('MA20'), body: t('20 日收盘均线，看短期方向。') },
  ma50: { title: t('MA50'), body: t('50 日收盘均线，看中期方向。') },
  ma200: { title: t('MA200'), body: t('200 日收盘均线，看长期方向；前 199 根没有读数，曲线从第 200 根才开始。') },
  swings: {
    title: t('摆动点'),
    body: t('已确认的局部高低点，并由相邻两点比较标出 HH/HL/LH/LL。是逐点比较的结果，不是给整段行情下一个结论。'),
  },
  support_resistance: {
    title: t('支撑阻力'),
    body: t('由近期摆动点归纳出的水平价位带，价格多次在附近受阻或获支撑。'),
  },
  bases: {
    title: t('整理区'),
    body: t('识别出的横盘箱体，给出阻力带、支撑带与窗口区间。整理区的判定只有这一个来源，图上别处不会重复检测。'),
  },
  pivots: {
    title: t('pivot / invalidation'),
    body: t('整理区的关键触发价（pivot）与失效价（invalidation）：前者是突破参考位，后者是该结构被证伪的位置。'),
  },
  auto_patterns: {
    title: t('自动趋势线 / 通道 / 三角形 / 楔形'),
    body: t('从摆动点自动拟合的形态：两点只作候选，最终轨线由触点的 Theil–Sen 稳健回归定出，触点需满足最少数量与时间间隔。'),
    note: t('标注的「几何质量」只衡量形状拟合得有多干净，不是上涨或下跌的概率。'),
  },
  candles: {
    title: t('K线形态'),
    body: t('单根或两根 K 线的经典形态标记（如吞没、锤子），标在事件发生的那根上。'),
  },
  traps: {
    title: t('Spring / Upthrust'),
    body: t('假突破标记：Spring 是向下破位后迅速收回，Upthrust 是向上破位后迅速回落，两者都指向被证伪的突破。'),
  },
  breakouts: {
    title: t('突破触发 / 测试 / 失败'),
    body: t('由整理区状态映射出的突破生命周期：触发、回踩测试、以及失败。'),
  },
  rsi: {
    title: t('RSI'),
    body: t('14 日相对强弱指数副图，0–100。前 14 根是预热期不出读数。'),
  },
  macd: {
    title: t('MACD'),
    body: t('12/26 快慢均线之差与其 9 日信号线及柱状图。前 35 根是预热期不出读数，避免用种子值冒充真实读数。'),
  },
  obv: {
    title: t('OBV'),
    body: t('能量潮：按收盘涨跌把成交量累加或累减，看量能是否与价格同向。'),
  },
  clv: {
    title: t('CLV'),
    body: t('收盘位置值：每根 K 线的收盘价落在当日高低区间的什么位置，衡量买卖压力。'),
  },
  range_persistence: {
    title: t('60日区间位置'),
    body: t('当前收盘价在最近 60 日高低区间中的相对位置，0 是区间底、1 是区间顶。'),
  },
  spy_rs: {
    title: t('SPY Relative Strength'),
    body: t('相对标普 500 的强弱。只有能按日期对齐 SPY 收盘时才下发，否则这条副图整体省略而不是补空值。'),
  },
};
