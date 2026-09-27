/**
 * 跑马灯的副本数与定位。
 *
 * 动画每轮向左平移一套内容的宽度。平移到末尾时，后面的副本仍要把轨道铺满，
 * 所以套数是「轨道宽 ÷ 单套宽」向上取整再加一。原先固定两套：单套比轨道窄时，
 * 每轮末尾右侧都会露出空白（1920 宽屏上约 540px）。上限防止单套极窄时铺出几十份。
 */
export const MAX_MARQUEE_COPIES = 6;

export function marqueeCopies(trackWidth: number, copyWidth: number): number {
  if (!(trackWidth > 0) || !(copyWidth > 0)) return 2;
  return Math.min(MAX_MARQUEE_COPIES, Math.max(2, Math.ceil(trackWidth / copyWidth) + 1));
}

/** 让单套内第 offset 像素处的内容停在轨道起点时，动画应处的时间点（毫秒）。 */
export function marqueeTimeAt(offset: number, copyWidth: number, duration: number): number {
  if (!(copyWidth > 0) || !(duration > 0) || !Number.isFinite(offset)) return 0;
  const wrapped = ((offset % copyWidth) + copyWidth) % copyWidth;
  return (wrapped / copyWidth) * duration;
}
