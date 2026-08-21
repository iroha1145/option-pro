/**
 * Drawer 基座（design.md §7.3 + transitions.dev panel reveal）
 * 右侧/底部面板用 `.t-panel-slide`（open 400ms / close 350ms + 交叉模糊）。
 * 移动端变全屏 bottom sheet；ESC/点背板关闭。
 */
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import {
  overlayDataOpen,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import { t } from '../i18n/core.ts';

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  /** dialog 的可访问名（审计 2.5.4）：title 是任意节点、不参与名字计算，
      读屏没有它只能念出一个匿名 "dialog"。 */
  label?: string;
  children: ReactNode;
  width?: number;
}

export default function Drawer({ open, onClose, title, label, children, width = 560 }: DrawerProps) {
  /* 单实例（审计 2.6.5）：以前桌面/移动两块面板同时挂载、仅靠 CSS 断点互斥，
     children 整棵渲染两遍——组件各自持有独立定时器与图表实例，framer 的
     layoutId 也在两棵树里撞名；DOM 中还同时存在两个 aria-modal dialog。
     现在由 useIsMobile（matchMedia 同步初值）选择唯一形态。 */
  const isMobile = useIsMobile();
  const panelsRef = useRef<HTMLDivElement>(null);
  const closeMs = readRootDurationMs('--panel-close-dur', 350);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);
  useFocusTrap(panelsRef, open);

  useEffect(() => {
    if (!mounted) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [mounted, onClose]);

  if (!mounted) return null;

  return (
    <>
      <div
        className={cn('t-backdrop fixed inset-0 z-[70] bg-[rgba(13,22,38,.45)] backdrop-blur-[2px]', phase === 'open' && 'is-open')}
        onClick={onClose}
        aria-hidden="true"
      />
      <div ref={panelsRef} className="contents">
        <aside
          role="dialog"
          aria-modal="true"
          aria-label={label}
          data-open={overlayDataOpen(phase)}
          className={cn(
            't-panel-slide fixed z-[71] flex flex-col bg-card shadow-sh-3',
            isMobile
              ? 'inset-x-0 bottom-0 top-14 rounded-t-xl border-t border-line pb-[env(safe-area-inset-bottom)]'
              : 'inset-y-0 right-0 w-full border-l border-line',
          )}
          style={isMobile ? undefined : { maxWidth: width }}
        >
          {isMobile && (
            <div className="flex flex-col items-center pb-1 pt-2 text-ink-300">
              <Icon name="dots-grid" size={18} aria-hidden="true" />
            </div>
          )}
          <div
            className={cn(
              'flex items-center justify-between border-b border-line',
              isMobile ? 'px-4 py-3' : 'px-6 py-4',
            )}
          >
            <div className="min-w-0 flex-1">{title}</div>
            <button
              onClick={onClose}
              className="rounded-sm p-1.5 text-ink-400 transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-600 active:scale-95"
              aria-label={t('关闭抽屉')}
            >
              <Icon name="x" size={16} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </aside>
      </div>
    </>
  );
}
