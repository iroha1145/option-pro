/** 图表分析算法共用的数值小工具。 */
export const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
export const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n));
