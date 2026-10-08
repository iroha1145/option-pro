/**
 * Header（design.md §7.1）· sticky top-0 z-50 · 毛玻璃
 * Logo | 一级导航 6 项（滑动下划线） | ⌘K 触发 | 时段LED+纽约时钟 | 显示设置 | 登录/退出
 * 移动端折叠为 48px：Logo + ⌘K + 显示设置 + 登录/退出。
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router';
import { cn } from '@/lib/utils';
import { NAV_GROUPS, NAV_PAGES, isNavGroupActive } from '@/lib/navigation';
import { useNow } from '@/hooks/useNow';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/hooks/useToast';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { fmtNyTime } from '@/lib/format';
import { placeGlide } from '@/lib/transitions';
import Icon from '@/components/icons';
import { SessionDot } from '@/components/shared/SessionLED';
import SettingsMenu from '@/components/SettingsMenu';
import { t } from '../i18n/core.ts';
import { routeIntentHandlers } from '../lib/prefetchRouteChunk.ts';

/* 命令面板逐页列出全部九个页面；顶栏只放 6 个一级入口（见 lib/navigation.ts）。 */
export const NAV_ITEMS = NAV_PAGES;

function NyClock({ className }: { className?: string }) {
  const now = useNow(1000);
  return (
    <span className={cn('shrink-0 whitespace-nowrap text-micro text-ink-500 tnum', className)} suppressHydrationWarning>
      {fmtNyTime(new Date(now))} ET
    </span>
  );
}

export default function Navbar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { isOwner, isSignedIn, username, logout } = useAccess();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { data: status } = usePolling(() => marketApi.status(), 60_000);
  // 读不到时段时点是浅灰「未知」而不是「休市」（与审计 2.2.1 同根因）
  const session = status?.session ?? null;


  const navRef = useRef<HTMLElement>(null);
  const glideRef = useRef<HTMLSpanElement>(null);
  const hoverRef = useRef<HTMLSpanElement>(null);
  const hoverShownRef = useRef(false);
  const glideReadyRef = useRef(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const activePath = NAV_GROUPS.find((group) => isNavGroupActive(location.pathname, group))?.path ?? '';

  const alignRef = useRef<(animate: boolean) => void>(() => undefined);
  useLayoutEffect(() => {
    const nav = navRef.current;
    const bar = glideRef.current;
    if (!nav || !bar) return;
    alignRef.current = (animate: boolean) => {
      const label = nav.querySelector('[data-active="true"] [data-nav-label]') as HTMLElement | null;
      if (!label) {
        bar.style.width = '0px';
        return;
      }
      // 相对 nav 盒测量：整条导航被右侧簇推移时偏移自消，不再靠像素绝对值。
      const navBox = nav.getBoundingClientRect();
      const box = label.getBoundingClientRect();
      placeGlide(bar, { offset: box.left - navBox.left, size: box.width }, { axis: 'x', animate });
    };
    /* ResizeObserver 只挂一次：每次 observe() 都会在同一渲染帧投递一次初始观察，
       若随 activePath 重建，那次初始回调会紧跟 align(true) 用 transition:none 把刚起步
       的补间当帧掐断——「滑行」永远只是瞬移（复审实锤）。首帧投递也要跳过：初始
       定位由下方按 activePath 的 effect 负责。nav 是内容定宽的 flex 盒，字体加载、
       2xl 编号出现、语言切换都会改它的尺寸而触发 RO；整体位移不改尺寸也不需要重对齐。 */
    let primed = false;
    const ro = new ResizeObserver(() => {
      if (!primed) {
        primed = true;
        return;
      }
      alignRef.current(false);
    });
    ro.observe(nav);
    return () => ro.disconnect();
  }, []);
  useLayoutEffect(() => {
    alignRef.current(glideReadyRef.current);
    glideReadyRef.current = true;
  }, [activePath]);
  /* beUI shared-layout-bg：鼠标在主导航各项之间移动时，一块浅底跟着滑过去，
     离开导航淡出。首次出现只淡入不补间（从上次的位置滑来会像是飞进来的），
     之后在各项之间用 placeGlide 补间位置与宽度。只响应鼠标，不响应触摸。 */
  const showNavHover = (target: HTMLElement) => {
    const nav = navRef.current;
    const pill = hoverRef.current;
    if (!nav || !pill) return;
    const navBox = nav.getBoundingClientRect();
    const box = target.getBoundingClientRect();
    placeGlide(pill, { offset: box.left - navBox.left, size: box.width }, { axis: 'x', animate: hoverShownRef.current });
    pill.style.opacity = '1';
    hoverShownRef.current = true;
  };
  const hideNavHover = () => {
    const pill = hoverRef.current;
    if (pill) pill.style.opacity = '0';
    hoverShownRef.current = false;
  };

  const handleLogout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
      // The cookie write succeeded; its separate identity confirmation may still be unavailable.
      toast.info(isOwner ? t('已退出管理员账号') : t('已退出登录'));
      navigate('/watchlist');
    } catch (error) {
      // 登出失败以前完全静默：按钮按了没反应，会话还挂着
      toast.error(t('退出失败'), error instanceof Error ? error.message : t('请稍后再试'));
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <header className="glass sticky top-0 z-50 border-b border-line">
      <div className="mx-auto flex h-12 max-w-shell items-center gap-3 px-4 md:h-16 md:gap-5 md:px-8">
        {/* Logo */}
        <Link to="/" className="flex shrink-0 items-center gap-2.5" aria-label={t("Optix Pro 首页")}>
          <img src="/logo.svg" alt="" className="size-7 md:size-8 dark:brightness-0 dark:invert" />
          {/* 2026-10-06 第二轮：只留品牌名，去掉下方「US EQUITY DESK」小字 */}
          <span className="hidden font-display text-[17px] font-bold leading-none text-ink-900 sm:inline">Optix Pro</span>
        </Link>

        {/* 编号导航（桌面） */}
        <nav
          ref={navRef}
          className="relative mx-auto hidden h-full items-center gap-1 xl:flex"
          aria-label={t("主导航")}
          onPointerLeave={hideNavHover}
        >
          <span ref={hoverRef} aria-hidden="true" className="nav-hover" />
          <span ref={glideRef} data-nav-glide="" aria-hidden="true" className="nav-glide" />
          {NAV_GROUPS.map((item) => {
            /* 选股、市场两组：任一子页打开时一级入口亮起；单页入口按自身路径边界匹配。 */
            const active = isNavGroupActive(location.pathname, item);
            const intent = routeIntentHandlers(item.path);
            return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              data-active={active}
              {...intent}
              onPointerEnter={(event) => {
                intent.onPointerEnter();
                if (event.pointerType === 'mouse') showNavHover(event.currentTarget);
              }}
              className={cn(
                /* 一级入口收成 6 项后 1280 也放得下，统一 px-3。relative：盖在悬停浅底之上。 */
                'relative flex h-full items-center gap-1.5 whitespace-nowrap px-3 text-body-s transition-colors duration-fast',
                active ? 'font-medium text-brand-600' : 'text-ink-500 hover:text-ink-800',
              )}
            >
              {/* 2026-10-06 第二轮：导航只留文字标签，不再显示「01」「02」编号（命令面板同步去掉）。 */}
              {/* 标签盒是滑行下划线的测量锚：placeGlide 按它的 left/width 补间，
                  跨项滑动（beUI tabs / transitions.dev tabs-sliding）。 */}
              <span data-nav-label className="relative flex h-full items-center">
                {item.label}
              </span>
            </NavLink>
            );
          })}
        </nav>

        {/* 右侧操作区 */}
        {/* 手机上每个操作外面都有 44px 的透明触控区，按钮之间已隔开 12px，间距收到 4px */}
        <div className="ml-auto flex items-center gap-1 md:gap-3.5 xl:ml-0">
          <button
            onClick={onOpenPalette}
            /* 一级入口收成 6 项后 xl 不再拥挤，文字搜索框从 md 起常显（此前 xl–2xl 只留图标）。 */
            className="touch-target hidden h-8 w-44 items-center gap-2 rounded-md border border-line bg-card-warm px-3 text-caption text-ink-400 transition-[border-color,box-shadow,color] duration-fast hover:border-line-strong hover:text-ink-500 focus-visible:border-brand-500 focus-visible:shadow-focus-ring md:flex 2xl:w-[220px]"
            aria-label={t("打开命令面板")}
          >
            <Icon name="search" size={14} />
            <span className="flex-1 truncate text-left">{t('搜索代码或功能…')}</span>
            <kbd className="flex items-center gap-0.5 font-mono text-micro text-ink-400">
              <Icon name="command" size={11} />K
            </kbd>
          </button>
          <button
            onClick={onOpenPalette}
            className="touch-target header-action md:hidden"
            aria-label={t("搜索")}
          >
            <span className="header-chip">
              <Icon name="search" size={15} />
            </span>
          </button>

          <span className="hidden shrink-0 items-center gap-2 md:flex" aria-label={t('市场时段：{label}', { label: status?.label ?? t('未知') })}>
            <SessionDot session={session} />
            <NyClock />
          </span>

          <SettingsMenu />

          {isSignedIn ? (
            <button
              onClick={handleLogout}
              disabled={loggingOut}
              className="touch-target header-action max-w-[140px] md:max-w-none"
            >
              <span className="header-chip px-3">
                <Icon name="logout" size={14} className="shrink-0" />
                <span className="truncate">{username ? t('退出 {name}', { name: username }) : t('退出')}</span>
              </span>
            </button>
          ) : (
            <Link
              to="/login"
              {...routeIntentHandlers('/login')}
              className="touch-target header-action"
            >
              <span className="header-chip header-chip-primary">{t('登录')}</span>
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
