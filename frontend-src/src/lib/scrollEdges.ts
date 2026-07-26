/**
 * 横向滚动容器的边缘判定。
 *
 * 单独放在 .ts 里有两个理由：它是纯算术，不该和 DOM 管道绑在一起；而且
 * 滚动事件在后台标签页里根本不派发（实测自建监听器也是 0 次触发），
 * 「滚到中间左箭头该出现」这类迁移只能靠直接测这个函数来保证。
 */
export interface ScrollEdges {
  /** 左侧还有内容（已经滚动过一段）。 */
  left: boolean;
  /** 右侧还有内容。 */
  right: boolean;
}

/**
 * 1px 容差：子像素宽度会让 scrollWidth 恒比 clientWidth 大一点点，
 * 不留容差会在完全没有溢出时也画出箭头。
 */
export function computeScrollEdges(
  scrollLeft: number,
  scrollWidth: number,
  clientWidth: number,
): ScrollEdges {
  const maxScroll = scrollWidth - clientWidth;
  if (maxScroll <= 1) return { left: false, right: false };
  return {
    left: scrollLeft > 1,
    right: scrollLeft < maxScroll - 1,
  };
}
