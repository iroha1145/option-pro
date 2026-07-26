/**
 * 命令面板（design.md §7.2 · Paper Terminal 皮肤）
 * ⌘K / Ctrl+K · 640px · 距顶 18vh · 纸面卡片（bg-card + line-strong 边 + sh-3）
 * 分组：股票（防抖搜索）/ 功能 / 最近（本地 5 条）· ↑↓ 循环 · Enter 打开 · ESC 关闭
 * 字体口径：中文走正文 sans，mono 仅用于代码/序号/快捷键；选中行 brand-50 底 + 左 2px brand 竖条。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { stocksApi } from '@/api/modules/stocks';
import { useAccess } from '@/hooks/useAccess';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { cn } from '@/lib/utils';
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

export default function CommandPalette({ open, onClose, onOpenTicker, onForceRefresh }: PaletteProps) {
  const navigate = useNavigate();
  const { isOwner, logout } = useAccess();
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
  useFocusTrap(panelRef, open, { initialFocusRef: inputRef });

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
      } else {
        list.push({
          id: 'f-login',
          group: '功能',
          title: __t('登录 Owner'),
          hint: __t('解锁写操作与 AI 分析'),
          icon: 'shield',
          action: () => {
            onClose();
            navigate('/login');
          },
        });
      }
    }
    return list;
  }, [query, results, navigate, onClose, onForceRefresh, pickTicker, isOwner, logout]);

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
      e.preventDefault();
      flat[clampedActive]?.action();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${clampedActive}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [clampedActive]);

  /* 分组渲染 */
  const groups: { name: string; items: (Entry & { idx: number })[] }[] = [];
  flat.forEach((e, idx) => {
    const g = groups.find((x) => x.name === e.group);
    const item = { ...e, idx };
    if (g) g.items.push(item);
    else groups.push({ name: e.group, items: [item] });
  });

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* 频率门禁：⌘K 为键盘高频动作，背板只保留 ≤80ms 淡入淡出 */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.08 }}
            className="fixed inset-0 z-[80] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]"
            onClick={onClose}
          />
          {/* 面板本体即时出现，无开/关动画 */}
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={__t("命令面板")}
            className="fixed left-1/2 top-[18vh] z-[81] w-[640px] max-w-[calc(100vw-24px)] -translate-x-1/2 overflow-hidden rounded-lg border border-line-strong bg-card shadow-sh-3"
          >
            {/* 顶边 brand 发丝线（纸面卡片签名） */}
            <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[2px] bg-brand-600/80" />
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
                onKeyDown={onKeyDown}
                placeholder={__t("搜索股票代码、名称或功能…")}
                className="h-full flex-1 bg-transparent text-body text-ink-800 outline-none placeholder:text-ink-300 focus-visible:!shadow-none"
                aria-label={__t("搜索股票或功能")}
              />
              {searching ? (
                <span className="size-4 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-label={__t("搜索中")} />
              ) : (
                <Kbd>ESC</Kbd>
              )}
            </div>

            <div ref={listRef} className="max-h-[46vh] overflow-y-auto py-1.5">
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
                <div className="flex flex-col items-center py-10 text-center">
                  <Icon name="search" size={30} className="text-ink-300" />
                  <p className="mt-3 text-body-s text-ink-400">{__t('没有匹配的结果')}</p>
                  <p className="mt-1 text-micro text-ink-300">
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
                      data-idx={e.idx}
                      onClick={e.action}
                      onMouseEnter={() => setActive(e.idx)}
                      className={cn(
                        'relative flex w-full items-center gap-2.5 px-4 py-2 text-left transition-colors duration-fast',
                        e.idx === clampedActive ? 'bg-brand-50' : 'hover:bg-paper-2/70',
                      )}
                    >
                      {e.idx === clampedActive && <span className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-brand-600" aria-hidden="true" />}
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
        </>
      )}
    </AnimatePresence>
  );
}
