/**
 * 页头「扫描历史」幽灵钮 + popover（scale .96→1 spring-pop）
 * 列最近 5 次：时间（Mono）/ 参数摘要 / 结果数
 */
import SoftBadge from '@/components/shared/SoftBadge';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DUR_FAST, SPRING_POP } from '@/lib/motion';
import { fmtTimeHHMMSS } from '@/lib/format';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { isTopFocusScope } from '@/lib/focusScope';
import Icon from '@/components/icons';
import type { ScanHistoryEntry } from './types';
import { t } from '../../i18n/core.ts';

export default function ScanHistoryPopover({ history }: { history: ScanHistoryEntry[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  useFocusTrap(popoverRef, open);

  /* 右对齐按钮，并在打开、窗口和按钮尺寸变化时保留两侧 16px 边距。
     量 offsetWidth（不受入场 scale 影响），直接写样式，不触发重渲染。 */
  useLayoutEffect(() => {
    const wrap = ref.current;
    const pop = popoverRef.current;
    if (!open || !wrap || !pop) return;
    const gutter = 16;
    let frame: number | null = null;
    const place = () => {
      const viewport = document.documentElement.clientWidth;
      pop.style.maxWidth = `${Math.max(0, viewport - gutter * 2)}px`;
      const width = pop.offsetWidth;
      const naturalLeft = wrap.getBoundingClientRect().right - width;
      const left = Math.min(Math.max(naturalLeft, gutter), Math.max(gutter, viewport - width - gutter));
      pop.style.right = `${naturalLeft - left}px`;
    };
    // resize 事件中断点布局可能尚未落定；下一帧再读尺寸，避免沿用过渡时的边距。
    const schedulePlace = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
        place();
      });
    };
    place();
    window.addEventListener('resize', schedulePlace);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedulePlace);
    observer?.observe(wrap);
    observer?.observe(pop);
    // 页头内容宽度随断点内边距变化；按钮本身宽度不变，仍需跟随它最终的位置。
    const layout = wrap.closest('header') ?? wrap.parentElement;
    if (layout) observer?.observe(layout);
    return () => {
      window.removeEventListener('resize', schedulePlace);
      observer?.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing || e.keyCode === 229 || !isTopFocusScope(popoverRef.current)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          'flex h-9 items-center gap-1.5 rounded-md border px-3 text-caption shadow-btn transition-colors duration-fast',
          open ? 'border-brand-400 text-brand-600' : 'border-line bg-card text-ink-500 hover:text-ink-800',
        )}
      >
        <Icon name="clock-ny" size={14} />
        {t('扫描历史')}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            ref={popoverRef}
            role="dialog"
            aria-label={t("最近扫描记录")}
            initial={{ opacity: 0, scale: 0.96, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: -4, transition: { duration: DUR_FAST } }}
            transition={SPRING_POP}
            className="absolute right-0 top-11 z-40 w-[320px] max-w-[calc(100vw-2rem)] origin-top-right rounded-md border border-line bg-card p-2 shadow-sh-2"
          >
            <p className="px-2 pb-1.5 pt-1 eyebrow">{t('最近 5 次扫描')}</p>
            {history.length === 0 ? (
              <p className="px-2 py-4 text-center text-caption text-ink-400">{t('尚无扫描记录')}</p>
            ) : (
              <ul className="divide-y divide-line">
                {history.slice(0, 5).map((h, i) => (
                  <li key={i} className="flex items-center gap-3 px-2 py-2.5">
                    <span className="font-mono text-caption text-ink-800 tnum">{fmtTimeHHMMSS(h.at)}</span>
                    <span className="min-w-0 flex-1 truncate text-micro text-ink-500" title={h.summary}>
                      {h.summary}
                    </span>
                    <SoftBadge tone="brand" className="shrink-0">
                      {h.count} {t('只')}
                    </SoftBadge>
                  </li>
                ))}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
