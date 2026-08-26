/**
 * 命令面板（design.md §7.2 · Paper Terminal 皮肤）
 * ⌘K / Ctrl+K · 640px · 距顶 18vh · 纸面卡片（bg-card + line-strong 边 + sh-3）
 * 分组：股票（防抖搜索）/ 功能 / 最近（本地 5 条）· ↑↓ 循环 · Enter 打开 · ESC 关闭
 * 字体口径：中文走正文 sans，mono 仅用于代码/序号/快捷键；选中行 brand-50 底 + 左 2px brand 竖条。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { ApiError } from '@/api/client';
import { stocksApi } from '@/api/modules/stocks';
import { useAccess } from '@/hooks/useAccess';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { cn } from '@/lib/utils';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import { pushRecent, readRecent } from '@/lib/recentTickers';
import Icon, { type IconName } from '@/components/icons';
import { NAV_ITEMS } from '@/components/Navbar';
import { t, t as __t } from '../i18n/core.ts';

interface PaletteProps {
  open: boolean;
  onClose: () => void;
  onOpenTicker: (ticker: string) => void;
  onForceRefresh?: () => void;
}

interface Entry {
  id: string;
  group: '股票' | '功能' | '最近';
  no?: string;
  title: string;
  mono?: boolean;
  hint?: string;
  icon: IconName;
  action: () => void;
}

// 最近查看记录的读写与校验见 lib/recentTickers（审计 P1-09：一个损坏的本地
// 存储值曾足以让整站白屏，校验逻辑不该藏在始终挂载的 UI 组件里）。


function searchErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 429) {
      return error.retryAfter
        ? t('搜索请求较多，请 {n} 秒后重试', { n: Math.ceil(error.retryAfter) })
        : __t('搜索请求较多，请稍后重试');
    }
    if (error.code === 401) return __t('登录状态已失效，请重新登录');
    if (error.code === 503) return __t('股票目录暂不可用，请稍后重试');
    return error.message || __t('股票搜索失败，请稍后重试');
  }
  return error instanceof Error && error.message
    ? error.message
    : __t('股票搜索失败，请稍后重试');
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded-xs border border-line bg-card-warm px-1.5 py-0.5 font-mono text-[10px] leading-[14px] text-ink-400">
      {children}
    </kbd>
  );
}

/**
 * 结果列表的滑行高亮（beautifului.dev Search 的 GlideMenu 手感）：
 * 一个绝对定位的高亮块在行间滑动，transform/height 走 CSS 缓动（弹簧见
 * --ease-snap），首绘不补间。不用 framer layoutId——chrome 覆盖层保持
 * catalog CSS 单一动效语言（motion-tokens 契约）。
 */
function placeListGlide(el: HTMLElement, row: { offsetTop: number; offsetHeight: number }, animate: boolean): void {
  if (!animate) {
    const prev = el.style.transition;
    el.style.transition = 'none';
    el.style.transform = `translateY(${row.offsetTop}px)`;
    el.style.height = `${row.offsetHeight}px`;
    void el.offsetWidth;
    el.style.transition = prev;
    return;
  }
  el.style.transform = `translateY(${row.offsetTop}px)`;
  el.style.height = `${row.offsetHeight}px`;
}

export default function CommandPalette({ open, onClose, onOpenTicker, onForceRefresh }: PaletteProps) {
  const navigate = useNavigate();
  const { isOwner, isSignedIn, username, logout } = useAccess();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<{ ticker: string; name: string; sector: string }[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  /* 焦点圈定 + 关闭后归还焦点（审计 P3-3）：旧实现只聚焦输入框，继续按 Tab 会
     走进遮罩后方的页面，关闭后也不回到触发点。 */
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const glideRef = useRef<HTMLSpanElement>(null);
  const glidePainted = useRef(false);
  useFocusTrap(panelRef, open, { initialFocusRef: inputRef });
  const closeMs = readRootDurationMs('--modal-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);

  /* 全局快捷键 ⌘K / Ctrl+K 由 Layout 绑定；此处处理打开后逻辑 */
  useEffect(() => {
    if (open) {
      setQuery('');
      setResults([]);
      setSearchError(null);
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  /* 防抖 200ms 搜索 */
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearching(false);
      setSearchError(null);
      return;
    }
    setResults([]);
    setSearching(true);
    setSearchError(null);
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const next = await stocksApi.search(q);
        if (!cancelled) {
          setResults(next);
          setSearchError(null);
        }
      } catch (cause) {
        if (!cancelled) {
          setResults([]);
          setSearchError(searchErrorText(cause));
        }
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query]);

  const pickTicker = useCallback(
    (t: string) => {
      pushRecent(t);
      onClose();
      onOpenTicker(t);
    },
    [onClose, onOpenTicker],
  );

  const entries = useMemo<Entry[]>(() => {
    const list: Entry[] = [];
    if (query.trim()) {
      results.forEach((r) =>
        list.push({
          id: `s-${r.ticker}`,
          group: '股票',
          title: r.ticker,
          mono: true,
          hint: `${r.name} · ${r.sector}`,
          icon: 'candle',
          action: () => pickTicker(r.ticker),
        }),
      );
    } else {
      readRecent().forEach((t) =>
        list.push({ id: `r-${t}`, group: '最近', title: t, mono: true, hint: __t('最近查看'), icon: 'clock-ny', action: () => pickTicker(t) }),
      );
      NAV_ITEMS.forEach((n) =>
        list.push({
          id: `f-${n.path}`,
          group: '功能',
          no: n.no,
          title: n.label,
          hint: __t('前往{label}页', { label: n.label }),
          icon: 'chevron-right',
          action: () => {
            onClose();
            navigate(n.path);
          },
        }),
      );
      if (isOwner) {
        list.push({
          id: 'f-refresh',
          group: '功能',
          title: __t('强制刷新自选'),
          hint: __t('重新获取自选行情'),
          icon: 'refresh',
          action: () => {
            onClose();
            onForceRefresh?.();
          },
        });
        list.push({
          id: 'f-logout',
          group: '功能',
          title: __t('退出 Owner 登录'),
          hint: __t('结束本机会话'),
          icon: 'shield',
          action: () => {
            onClose();
            void logout();
          },
        });
      } else if (isSignedIn) {
        // 客户账号：给出结束会话的入口（原先这里只有「登录 Owner」，
        // 客户会话在整个 UI 里无处退出）
        list.push({
          id: 'f-logout',
          group: '功能',
          title: username ? __t('退出 {name}', { name: username }) : __t('退出登录'),
          hint: __t('结束本机会话'),
          icon: 'shield',
          action: () => {
            onClose();
            void logout();
          },
        });
      } else {
        list.push({
          id: 'f-login',
          group: '功能',
          title: __t('登录'),
          hint: __t('Owner 或客户账号'),
          icon: 'shield',
          action: () => {
            onClose();
            navigate('/login');
          },
        });
      }
    }
    return list;
  }, [query, results, navigate, onClose, onForceRefresh, pickTicker, isOwner, isSignedIn, username, logout]);

  const flat = entries;
  const clampedActive = Math.min(active, Math.max(0, flat.length - 1));

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => (flat.length ? (a + 1) % flat.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => (flat.length ? (a - 1 + flat.length) % flat.length : 0));
    } else if (e.key === 'Enter') {
      /* 原生交互元素（清除钮/结果行按钮）让浏览器派发原生 click——面板级
         Enter 只服务输入框。否则 preventDefault 会吃掉清除钮的点击行为，
         还误把当前高亮结果的 action() 执行掉（审查 #113 阻断 2）。 */
      const target = e.target as HTMLElement;
      if (target.closest('button, a, [role="button"]')) return;
      e.preventDefault();
      flat[clampedActive]?.action();
    } else if (e.key === 'Escape') {
      /* 只关命令面板：不拦截的话原生 keydown 继续冒到 document，命中下层
         Drawer 的 Escape 监听，把已打开的股票抽屉一起关掉（审计 #61）。 */
      e.preventDefault();
      e.stopPropagation();
      onClose();
    }
  };

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${clampedActive}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [clampedActive]);

  /* 滑行高亮定位：跟随 active 行（键盘 ↑↓ 与鼠标悬停同一套），首绘/列表
     换批时瞬放不补间，行内切换才滑行。
     依赖必须带 mounted（审查 #113 阻断 1）：面板关闭时组件未挂载、高亮
     节点不存在，effect 提前返回；随后打开时若其余依赖没变，effect 不再
     执行，高亮会永远停在 height:0/opacity:0。 */
  useLayoutEffect(() => {
    if (!mounted) {
      glidePainted.current = false;
      return;
    }
    const glide = glideRef.current;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-idx="${clampedActive}"]`);
    if (!glide || !row || flat.length === 0) {
      if (glide) glide.style.opacity = '0';
      glidePainted.current = false;
      return;
    }
    glide.style.opacity = '1';
    placeListGlide(glide, row, glidePainted.current);
    glidePainted.current = true;
  }, [mounted, clampedActive, flat.length, entries]);

  /* 面板打开时锁背景滚动（审计 2.2.14）：与 Drawer 同口径，滚轮不再穿透
     到背后的页面。关闭动画期间仍锁，避免背后页面跟着滚。 */
  useEffect(() => {
    if (!mounted) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [mounted]);

  /* 分组渲染 */
  const groups: { name: string; items: (Entry & { idx: number })[] }[] = [];
  flat.forEach((e, idx) => {
    const g = groups.find((x) => x.name === e.group);
    const item = { ...e, idx };
    if (g) g.items.push(item);
    else groups.push({ name: e.group, items: [item] });
  });

  if (!mounted) return null;

  return (
        <>
          <div
            className={cn('t-backdrop fixed inset-0 z-[80] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]', phase === 'open' && 'is-open')}
            onClick={onClose}
          />
          {/* 居中交给外层 translate，避免 t-modal 的 scale 顶掉 -translate-x-1/2；
              外壳 pointer-events-none：关闭动画期间不挡背板点击（面板自身由
              t-modal.is-open 恢复 pointer-events）。 */}
          <div className="pointer-events-none fixed left-1/2 top-[18vh] z-[81] w-[640px] max-w-[calc(100vw-24px)] -translate-x-1/2">
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={__t("命令面板")}
            /* 键盘逻辑挂在面板上（审计 2.2.13）：焦点 Tab 进结果按钮后，
               ↑↓/Enter/Esc 依然生效（事件冒泡到这里统一处理）。 */
            onKeyDown={onKeyDown}
            className={cn(
              't-modal overflow-hidden rounded-lg border border-line-strong bg-card/[0.88] backdrop-blur-xl backdrop-saturate-150 shadow-overlay',
              overlayClassName(phase),
            )}
          >
            {/* 顶边 brand 发丝线（纸面卡片签名） */}
            <div className="flex h-12 items-center gap-2.5 border-b border-line px-4">
              <Icon name="search" size={16} className="shrink-0 text-ink-400" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setSearchError(null);
                  setActive(0);
                }}
                placeholder={__t("搜索股票代码、名称或功能…")}
                className="h-full flex-1 bg-transparent text-body text-ink-800 outline-none placeholder:text-ink-300 focus-visible:!shadow-none"
                role="combobox"
                aria-expanded={flat.length > 0}
                aria-controls="command-palette-listbox"
                aria-activedescendant={flat.length ? `command-palette-option-${clampedActive}` : undefined}
                aria-label={__t("搜索股票或功能")}
              />
              {searching ? (
                <span className="size-4 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-label={__t("搜索中")} />
              ) : query ? (
                /* beautifului Search 的清除钮：fade-in 150ms 进场，点后清空并回焦 */
                <button
                  type="button"
                  aria-label={__t('清除搜索')}
                  onClick={() => {
                    setQuery('');
                    setSearchError(null);
                    setActive(0);
                    inputRef.current?.focus();
                  }}
                  className="anim-fade-in flex size-6 shrink-0 items-center justify-center rounded-full text-ink-400 transition-colors duration-fast hover:bg-line/70 hover:text-ink-800"
                >
                  <Icon name="x" size={12} />
                </button>
              ) : (
                <Kbd>ESC</Kbd>
              )}
            </div>

            {/* listbox/option + activedescendant（审计 2.5.3）：读屏跟随高亮播报，
                不再只有肉眼可见的背景色变化 */}
            <div id="command-palette-listbox" role="listbox" ref={listRef} className="relative max-h-[46vh] overflow-y-auto py-1.5">
              {/* 滑行高亮（GlideMenu 手感）：绝对定位在行间滑动，brand-50 底 +
                  左 2px brand 竖条随行；active 由键盘/悬停共同驱动 */}
              <span
                ref={glideRef}
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-1.5 top-0 z-0 rounded-md bg-brand-50"
                style={{
                  height: 0,
                  opacity: 0,
                  transition: 'transform 250ms var(--ease-snap), height 250ms var(--ease-snap), opacity 160ms',
                }}
              >
                <span className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-brand-600" />
              </span>
              {searching && flat.length === 0 && (
                <div className="flex flex-col items-center py-10 text-center" role="status">
                  <span className="size-5 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-hidden="true" />
                  <p className="mt-3 text-body-s text-ink-400">{__t('正在搜索股票目录…')}</p>
                </div>
              )}
              {!searching && searchError && (
                <div className="flex flex-col items-center px-6 py-10 text-center" role="alert" aria-live="assertive">
                  <span className="flex size-9 items-center justify-center rounded-full bg-down-50 text-down-700">
                    <Icon name="x" size={15} />
                  </span>
                  <p className="mt-3 text-body-s font-medium text-ink-700">{__t('搜索未完成')}</p>
                  <p className="mt-1 max-w-sm text-micro leading-5 text-ink-400">{searchError}</p>
                </div>
              )}
              {!searching && !searchError && flat.length === 0 && (
                <div className="anim-fade-in flex flex-col items-center py-10 text-center">
                  {/* beautifului Search 空态：内嵌图标砖 + 主文案 + 提示 */}
                  <span className="flex size-9 items-center justify-center rounded-lg border border-line bg-card-warm text-ink-300 shadow-[inset_0_1px_2px_rgba(16,24,40,.05)]">
                    <Icon name="search" size={16} />
                  </span>
                  <p className="mt-3 text-body-s font-medium text-ink-700">{__t('没有匹配的结果')}</p>
                  <p className="mt-1 text-micro text-ink-400">
                    {__t('试试代码')} <span className="font-mono">NVDA</span> {__t('或中文名（英伟达）')}
                  </p>
                </div>
              )}
              {groups.map((g) => (
                <div key={g.name}>
                  <p className="eyebrow px-4 pb-1 pt-2.5">{__t(g.name)}</p>
                  {g.items.map((e) => (
                    <button
                      key={e.id}
                      id={`command-palette-option-${e.idx}`}
                      role="option"
                      aria-selected={e.idx === clampedActive}
                      data-idx={e.idx}
                      onClick={e.action}
                      onMouseEnter={() => setActive(e.idx)}
                      className={cn(
                        'relative z-10 flex w-full items-center gap-2.5 rounded-md px-4 py-2 text-left transition-colors duration-fast',
                      )}
                    >
                      <Icon name={e.icon} size={15} className={cn('shrink-0', e.idx === clampedActive ? 'text-brand-600' : 'text-ink-400')} />
                      {e.no && <span className="font-mono text-micro text-ink-400 tnum">{e.no}</span>}
                      <span
                        className={cn(
                          e.mono ? 'font-mono text-body-s font-medium' : 'text-body-s',
                          e.idx === clampedActive ? 'text-ink-900' : 'text-ink-700',
                        )}
                      >
                        {e.title}
                      </span>
                      {e.hint && <span className="ml-auto shrink-0 truncate text-micro text-ink-400">{e.hint}</span>}
                    </button>
                  ))}
                </div>
              ))}
            </div>

            <div className="flex items-center gap-3 border-t border-line bg-card-warm px-4 py-2 text-micro text-ink-400">
              <span className="flex items-center gap-1.5"><Kbd>↑↓</Kbd> {__t('选择')}</span>
              <span className="flex items-center gap-1.5"><Kbd>Enter</Kbd> {__t('打开')}</span>
              <span className="flex items-center gap-1.5"><Kbd>Esc</Kbd> {__t('关闭')}</span>
            </div>
          </div>
          </div>
        </>
  );
}
