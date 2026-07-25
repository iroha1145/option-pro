/**
 * 模态焦点圈定（GPT-5.6-Pro 审计 P3-2 / P3-3）。
 *
 * 抽屉与命令面板此前都实现了 Escape 关闭和 body 滚动锁，但都没有：
 *   - 打开时把焦点移进面板
 *   - 限制 Tab 逃到遮罩后方的页面
 *   - 关闭后把焦点还给触发它的元素
 *
 * 于是键盘用户打开抽屉后按 Tab 会走进背景内容，关闭后焦点落回文档开头，
 * 之前操作到哪一行完全丢失。这三件事是同一件事的三个部分，放在一个 hook 里。
 */
import { useEffect, type RefObject } from 'react';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableWithin(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (element) =>
      !element.hasAttribute('inert')
      && element.offsetParent !== null
      && element.getAttribute('aria-hidden') !== 'true',
  );
}

export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  options: { initialFocusRef?: RefObject<HTMLElement | null> } = {},
): void {
  const { initialFocusRef } = options;
  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    // 记住触发者，关闭后还回去。
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // 焦点移入：优先调用方指定的元素，否则第一个可聚焦项，再否则容器本身。
    const initial = initialFocusRef?.current ?? focusableWithin(container)[0] ?? container;
    if (initial === container && !container.hasAttribute('tabindex')) {
      container.setAttribute('tabindex', '-1');
    }
    initial.focus({ preventScroll: true });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const items = focusableWithin(container);
      if (items.length === 0) {
        // 面板内没有可聚焦元素时，Tab 也不能跑出去。
        event.preventDefault();
        container.focus({ preventScroll: true });
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      if (!event.shiftKey && current === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      } else if (event.shiftKey && (current === first || current === container)) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (current instanceof Node && !container.contains(current)) {
        // 焦点已经在外面（例如浏览器地址栏回来）：拉回面板内。
        event.preventDefault();
        (event.shiftKey ? last : first).focus({ preventScroll: true });
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      // 触发者可能已经被卸载（例如删除按钮），此时不强行抢焦点。
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [active, containerRef, initialFocusRef]);
}
