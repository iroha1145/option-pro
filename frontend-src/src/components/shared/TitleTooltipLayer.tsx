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
 * - 借走写空串而不是删属性；自己的写入不参与监听。React 清空或删掉 title 时
 *   收起旧提示，补齐 title 时安排显示，离开后恢复的总是组件最后一次提供的值。
 * - 按下、滚动、Esc、窗口失焦只收起浮层，title 仍借着，直到指针离开，免得
 *   点击后原生提示又冒出来。
 * - 只接管鼠标；借用期间通过 aria-description 保留标题说明，已有的作者说明
 *   优先级不变。只能从 title 获得名称的元素保留原生属性。未悬停时 DOM 不动。
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
const WATCHED_ATTRIBUTES = ['title', 'aria-description', 'aria-label', 'aria-labelledby'];

/** 不借走元素唯一的命名来源。不能确认有独立名称时，保留原生 title。 */
function canBorrowTitle(element: HTMLElement): boolean {
  if (element.getAttribute('aria-label')?.trim()) return true;
  if (element.getAttribute('aria-labelledby')?.split(/\s+/).some((id) => document.getElementById(id)?.textContent?.trim())) return true;
  if (element.innerText.trim()) return true;
  if (element instanceof HTMLImageElement && element.alt.trim()) return true;
  if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) {
    return Array.from(element.labels ?? []).some((label) => label.textContent?.trim());
  }
  return false;
}

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
    let dismissed = false;
    let lastHiddenAt = -Infinity;
    let description: { owner: HTMLElement; value: string; original: string | null } | null = null;
    const observe = (element: HTMLElement) => watcher?.observe(element, {
      attributes: true,
      attributeFilter: WATCHED_ATTRIBUTES,
      childList: true,
      characterData: true,
      subtree: true,
    });

    const restoreDescription = () => {
      const owned = description;
      description = null;
      if (!owned || owned.owner.getAttribute('aria-description') !== owned.value) return;
      if (owned.original === null) owned.owner.removeAttribute('aria-description');
      else owned.owner.setAttribute('aria-description', owned.original);
    };
    const preserveDescription = (element: HTMLElement, value: string) => {
      restoreDescription();
      const original = element.getAttribute('aria-description');
      if (!value.trim() || original?.trim()) return;
      // aria-describedby 本来就比 aria-description 优先；不追加或替换作者的关联说明。
      element.setAttribute('aria-description', value);
      description = { owner: element, value, original };
    };

    const borrow = (element: HTMLElement) => {
      const value = element.getAttribute('title') ?? '';
      // 自己写空串时暂停监听，才能把组件主动写入的空串当作真正的清空。
      watcher?.disconnect();
      preserveDescription(element, value);
      element.setAttribute(BORROWED, value);
      element.setAttribute('title', '');
      observe(element);
      return value.trim();
    };
    const hide = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (visible) lastHiddenAt = performance.now();
      visible = false;
      setText(null);
    };
    const release = () => {
      hide();
      watcher?.disconnect();
      watcher = null;
      restoreDescription();
      if (alive !== null) {
        window.clearInterval(alive);
        alive = null;
      }
      const owner = ownerRef.current;
      ownerRef.current = null;
      if (!owner) return;
      const value = owner.getAttribute(BORROWED);
      owner.removeAttribute(BORROWED);
      // 仍是借走时的空串才还回；悬停期间被 React 删掉或改写过，就以 React 为准。
      if (value !== null && owner.getAttribute('title') === '') owner.setAttribute('title', value);
    };

    const schedule = (target: HTMLElement) => {
      if (dismissed || timer !== null || visible || !target.getAttribute(BORROWED)?.trim()) return;
      const warm = performance.now() - lastHiddenAt < WARM_WINDOW_MS;
      timer = window.setTimeout(() => {
        timer = null;
        if (!target.isConnected) {
          release();
          return;
        }
        visible = true;
        setText(target.getAttribute(BORROWED)?.trim() || null);
      }, warm ? WARM_DELAY_MS : SHOW_DELAY_MS);
    };
    const dismiss = () => {
      dismissed = true;
      hide();
    };

    const onPointer = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const target = titledElement(event.target);
      if (target === ownerRef.current) return;
      release();
      if (!target || !canBorrowTitle(target)) return;
      ownerRef.current = target;
      dismissed = false;
      borrow(target);
      watcher = new MutationObserver((changes) => {
        if (changes.some((change) => change.target === target && change.attributeName === 'aria-description')) {
          // 组件接管了说明属性，哪怕写入相同文字，也不再由提示层负责撤销。
          description = null;
        }
        if (!canBorrowTitle(target)) {
          release();
          return;
        }
        if (!changes.some((change) => change.target === target && change.attributeName === 'title')) {
          watcher?.disconnect();
          preserveDescription(target, target.getAttribute(BORROWED) ?? '');
          observe(target);
          return;
        }
        if (!target.hasAttribute('title')) {
          // React 删掉了 title：放弃借用，正在显示的旧文字也收起。
          target.removeAttribute(BORROWED);
          release();
          return;
        }
        const next = borrow(target);
        if (!next) hide();
        else if (visible) setText(next);
        else schedule(target);
      });
      observe(target);
      alive = window.setInterval(() => {
        if (!target.isConnected) release();
      }, ALIVE_CHECK_MS);
      schedule(target);
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
      if (event.key === 'Escape') dismiss();
    };

    /* pointermove 兜住悬停后才出现的 title（如点星标后才有「移出自选」）：同一元素上
       早退，所以按下或 Esc 收起后不会再弹，直到指针离开。 */
    document.addEventListener('pointerover', onPointer);
    document.addEventListener('pointermove', onPointer);
    document.addEventListener('pointerout', onOut);
    document.addEventListener('pointerdown', dismiss, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('blur', release);
    return () => {
      document.removeEventListener('pointerover', onPointer);
      document.removeEventListener('pointermove', onPointer);
      document.removeEventListener('pointerout', onOut);
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', dismiss, true);
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
      /* 标题说明在所属元素的 aria-description 中保留，浮层不重复朗读。 */
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
