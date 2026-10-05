/**
 * TitleTooltipLayer — 站内 title 提示改由网页绘制（2026-10-06）。
 *
 * 浏览器原生 title 提示由操作系统绘制：字号、底色、延迟都不受控，和 InfoHint、
 * 图表提示是两套外观。逐个调用点改成 PointerTooltip 也不行——它是可聚焦的
 * role=button，放进整行链接（雷达行、报价角标）会形成交互嵌套，点击还会被它
 * 截停冒泡。所以在文档级统一接管：
 * - 鼠标进入（或在其上移动时遇到）带 title 的元素，立刻把 title 借走：原文存进
 *   data-title-borrowed，title 改成空串（原生提示就不会出现）；停留 SHOW_DELAY_MS
 *   后在 body 上画只读浮层；离开元素时把原文还回去。
 * - 借走写空串而不是删属性：悬停期间 React 删掉 title（如点了星标，「移出自选」
 *   不该再有）会留下变更记录，此时放弃借用、离开时不再把过时文字还回去；React
 *   改写 title（如报价时间每笔刷新）则重新借走，浮层跟着更新。
 * - 按下、滚动、Esc、窗口失焦只收起浮层，title 仍借着，直到指针离开，免得
 *   点击后原生提示又冒出来。
 * - 只接管鼠标：触屏本来就没有 title 提示；未悬停时 DOM 不动，读屏的可访问
 *   名称和 getByTitle 测试都不受影响。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const BORROWED = 'data-title-borrowed';
/** 首次停留 500ms 才出现（密集列表里扫过不闪）；刚收起一个提示后 WARM_WINDOW_MS 内移到
    相邻元素，只等 WARM_DELAY_MS——与系统提示「热身」后连看的手感一致。 */
const SHOW_DELAY_MS = 500;
const WARM_DELAY_MS = 80;
const WARM_WINDOW_MS = 400;
/** 显示期间检查元素是否已被卸载（换页、列表刷新）的间隔。 */
const ALIVE_CHECK_MS = 400;
const GAP = 8;
const GUTTER = 8;
const MAX_WIDTH = 280;
/** 与 InfoHint 同层：盖住抽屉 70、命令面板 80、确认框 85，低于 Toast 90。 */
const Z_INDEX = 88;

function titledElement(node: EventTarget | null): HTMLElement | null {
  let element = node instanceof Element ? node : null;
  while (element) {
    if (element instanceof HTMLElement && (element.hasAttribute('title') || element.hasAttribute(BORROWED))) {
      return element;
    }
    element = element.parentElement;
  }
  return null;
}

export default function TitleTooltipLayer() {
  const [text, setText] = useState<string | null>(null);
  const ownerRef = useRef<HTMLElement | null>(null);
  const tipRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    let timer: number | null = null;
    let alive: number | null = null;
    let watcher: MutationObserver | null = null;
    let visible = false;
    let lastHiddenAt = -Infinity;

    const borrow = (element: HTMLElement) => {
      const value = element.getAttribute('title') ?? '';
      element.setAttribute(BORROWED, value);
      element.setAttribute('title', '');
      return value.trim();
    };
    const hide = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (alive !== null) {
        window.clearInterval(alive);
        alive = null;
      }
      if (visible) lastHiddenAt = performance.now();
      visible = false;
      setText(null);
    };
    const release = () => {
      hide();
      watcher?.disconnect();
      watcher = null;
      const owner = ownerRef.current;
      ownerRef.current = null;
      if (!owner) return;
      const value = owner.getAttribute(BORROWED);
      owner.removeAttribute(BORROWED);
      // 仍是借走时的空串才还回；悬停期间被 React 删掉或改写过，就以 React 为准。
      if (value !== null && owner.getAttribute('title') === '') owner.setAttribute('title', value);
    };

    const onPointer = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const target = titledElement(event.target);
      if (target === ownerRef.current) return;
      release();
      if (!target) return;
      ownerRef.current = target;
      const value = borrow(target);
      watcher = new MutationObserver(() => {
        if (!target.hasAttribute('title')) {
          // React 删掉了 title：放弃借用，正在显示的旧文字也收起。
          target.removeAttribute(BORROWED);
          hide();
          return;
        }
        const next = target.getAttribute('title') ?? '';
        if (next === '') return; // 自己写入的空串
        borrow(target);
        if (visible) setText(next.trim() || null);
      });
      watcher.observe(target, { attributes: true, attributeFilter: ['title'] });
      if (!value) return;
      const warm = performance.now() - lastHiddenAt < WARM_WINDOW_MS;
      timer = window.setTimeout(() => {
        timer = null;
        if (!target.isConnected) {
          release();
          return;
        }
        visible = true;
        setText(target.getAttribute(BORROWED)?.trim() || null);
        alive = window.setInterval(() => {
          if (!target.isConnected) release();
        }, ALIVE_CHECK_MS);
      }, warm ? WARM_DELAY_MS : SHOW_DELAY_MS);
    };
    const onOut = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const owner = ownerRef.current;
      if (!owner) return;
      const next = event.relatedTarget;
      if (next instanceof Node && owner.contains(next)) return;
      release();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hide();
    };

    /* pointermove 兜住悬停后才出现的 title（如点星标后才有「移出自选」）：同一元素上
       早退，所以按下或 Esc 收起后不会再弹，直到指针离开。 */
    document.addEventListener('pointerover', onPointer);
    document.addEventListener('pointermove', onPointer);
    document.addEventListener('pointerout', onOut);
    document.addEventListener('pointerdown', hide, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', hide, true);
    window.addEventListener('blur', release);
    return () => {
      document.removeEventListener('pointerover', onPointer);
      document.removeEventListener('pointermove', onPointer);
      document.removeEventListener('pointerout', onOut);
      document.removeEventListener('pointerdown', hide, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', hide, true);
      window.removeEventListener('blur', release);
      release();
    };
  }, []);

  /* 先渲染再量高：上方放得下就放上方，否则翻到下方；水平夹在视口内。 */
  useLayoutEffect(() => {
    const tip = tipRef.current;
    const owner = ownerRef.current;
    if (!text || !tip || !owner) return;
    const rect = owner.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const centered = rect.left + rect.width / 2 - width / 2;
    const left = Math.min(Math.max(centered, GUTTER), Math.max(GUTTER, viewportWidth - width - GUTTER));
    const above = rect.top - GAP - height;
    const below = rect.bottom + GAP;
    const top = above >= GUTTER || below + height > viewportHeight - GUTTER ? Math.max(GUTTER, above) : below;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    tip.classList.add('is-shown');
  }, [text]);

  if (!text || typeof document === 'undefined') return null;
  return createPortal(
    <span
      ref={tipRef}
      role="tooltip"
      /* 内容就是元素自己的 title，读屏已从可访问名称/描述读到，不再重复朗读。 */
      aria-hidden="true"
      data-portal=""
      data-title-tooltip=""
      className="t-tt pointer-events-none w-max border border-line text-left text-caption text-ink-700"
      style={{
        zIndex: Z_INDEX,
        left: 0,
        top: 0,
        maxWidth: `min(${MAX_WIDTH}px, calc(100vw - ${GUTTER * 2}px))`,
        whiteSpace: 'pre-line',
      }}
    >
      {/* 日期、区间里数字间的连字符换成不断行连字符，避免「2026-」「10-05」拆到两行。 */}
      {text.replace(/(\d)-(?=\d)/g, '$1\u2011')}
    </span>,
    document.body,
  );
}
