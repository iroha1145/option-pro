/**
 * Header（design.md §7.1）· sticky top-0 z-50 · 毛玻璃
 * Logo | 01–06 编号导航（滑动下划线） | ⌘K 触发 | 时段LED+纽约时钟 | AI 点（owner）| 登录/退出
 * 移动端折叠为 48px：Logo + ⌘K + 时钟。
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router';
import { cn } from '@/lib/utils';
import { useNow } from '@/hooks/useNow';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { fmtNyTime } from '@/lib/format';
import Icon from '@/components/icons';
import { SessionDot } from '@/components/shared/SessionLED';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { t } from '../i18n/core.ts';

export const NAV_ITEMS = [
  { no: '01', label: t('自选'), path: '/watchlist' },
  { no: '02', label: t('选股'), path: '/screener' },
  { no: '03', label: t('雷达'), path: '/breakouts' },
  { no: '04', label: t('板块'), path: '/sectors' },
  { no: '05', label: t('财报'), path: '/earnings' },
  { no: '06', label: t('催化'), path: '/catalysts' },
] as const;

function NyClock({ className }: { className?: string }) {
  const now = useNow(1000);
  return (
    <span className={cn('font-mono text-micro text-ink-500 tnum', className)} suppressHydrationWarning>
      {fmtNyTime(new Date(now))} ET
    </span>
  );
}

export default function Navbar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { isOwner, aiEnabled, aiAvailable, aiReason, logout } = useAccess();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { data: status } = usePolling(() => marketApi.status(), 60_000);
  const session = status?.session ?? 'closed';

  const navRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState<{ left: number } | null>(null);

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const active = nav.querySelector<HTMLElement>('[data-active="true"]');
    if (active) {
      setIndicator({ left: active.offsetLeft + active.offsetWidth / 2 - 12 });
    }
  }, [location.pathname]);

  const handleLogout = async () => {
    await logout();
    toast.info(t('已退出 Owner 模式'), t('当前为访客只读模式'));
    navigate('/watchlist');
  };

  return (
    <header className="glass sticky top-0 z-50 border-b border-line">
      <div className="mx-auto flex h-12 max-w-shell items-center gap-3 px-4 md:h-16 md:gap-5 md:px-8">
        {/* Logo */}
        <Link to="/watchlist" className="flex shrink-0 items-center gap-2.5" aria-label={t("Optix Pro 首页")}>
          <img src="/logo.svg" alt="" className="size-7 md:size-8" />
          <span className="hidden flex-col leading-none sm:flex">
            <span className="font-display text-[17px] font-bold text-ink-900">Optix Pro</span>
            <span className="eyebrow mt-0.5 text-[9px]">US EQUITY DESK</span>
          </span>
        </Link>

        {/* 编号导航（桌面） */}
        <nav ref={navRef} className="relative mx-auto hidden h-full items-center gap-1 xl:flex" aria-label={t("主导航")}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              data-active={location.pathname.startsWith(item.path)}
              className={({ isActive }) =>
                cn(
                  'flex h-full items-center gap-1.5 whitespace-nowrap px-2.5 text-body-s transition-colors duration-fast 2xl:px-3.5',
                  isActive ? 'font-medium text-brand-600' : 'text-ink-500 hover:text-ink-800',
                )
              }
            >
              <span className="font-mono text-[11px] text-ink-400">{item.no}</span>
              {item.label}
            </NavLink>
          ))}
          {/* 滑动下划线指示器（2px × 24px，260ms） */}
          <span
            aria-hidden="true"
            className="absolute bottom-0 h-0.5 w-6 rounded-full bg-brand-600 transition-[left,opacity] duration-ui ease-paper"
            style={indicator ? { left: indicator.left, opacity: 1 } : { opacity: 0 }}
          />
        </nav>

        {/* 右侧操作区 */}
        <div className="ml-auto flex items-center gap-2.5 md:gap-3.5 xl:ml-0">
          <button
            onClick={onOpenPalette}
            className="hidden h-8 w-[220px] items-center gap-2 rounded-md border border-line bg-card-warm px-3 text-caption text-ink-400 transition-colors duration-fast hover:border-line-strong hover:text-ink-500 md:flex"
            aria-label={t("打开命令面板")}
          >
            <Icon name="search" size={14} />
            <span className="flex-1 truncate text-left">{t('搜索代码或功能…')}</span>
            <kbd className="flex items-center gap-0.5 font-mono text-[10px] text-ink-400">
              <Icon name="command" size={11} />K
            </kbd>
          </button>
          <button
            onClick={onOpenPalette}
            className="flex size-9 items-center justify-center rounded-md border border-line bg-card-warm text-ink-500 md:hidden"
            aria-label={t("搜索")}
          >
            <Icon name="search" size={16} />
          </button>

          <span className="hidden items-center gap-2 md:flex" aria-label={t('市场时段：{label}', { label: status?.label ?? t('休市') })}>
            <SessionDot session={session} />
            <NyClock />
          </span>
          <span className="flex items-center gap-1.5 md:hidden">
            <SessionDot session={session} />
          </span>

          {isOwner && (
            <span
              className={cn(
                'hidden items-center gap-1.5 rounded-pill border px-2 py-0.5 text-micro md:flex',
                aiAvailable
                  ? 'border-ai-600/20 bg-ai-50 text-ai-600'
                  : aiEnabled
                    ? 'border-warn-600/25 bg-warn-50 text-warn-600'
                    : 'border-line bg-card-warm text-ink-400',
              )}
              title={
                aiAvailable
                  ? t('分析服务可用')
                  : aiEnabled && ['analysis_in_progress', 'global_concurrency_limit', 'queue_busy'].includes(aiReason ?? '')
                    ? t('分析任务处理中')
                    : aiEnabled
                      ? t('分析服务暂不可用')
                      : t('分析服务未开启')
              }
            >
              <Icon name="spark-ai" size={12} />
              AI
            </span>
          )}

          <LanguageSwitcher className="hidden md:block" />

          {isOwner ? (
            <button
              onClick={handleLogout}
              className="flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-line bg-card px-3 text-caption text-ink-500 transition-colors hover:text-ink-800"
            >
              <Icon name="logout" size={14} />
              {t('退出')}
            </button>
          ) : (
            <Link
              to="/login"
              className="flex h-8 shrink-0 items-center whitespace-nowrap rounded-md bg-brand-600 px-3.5 text-caption font-medium text-white shadow-sh-1 transition-[transform,background-color] duration-fast hover:bg-brand-700 active:scale-[0.98]"
            >
              {t('登录')}
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
