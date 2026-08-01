/**
 * Layout（design.md §7）
 * sticky Header + IndexTape + 内容槽（<Outlet/>，page-fade 转场）+ Footer
 * 全局：命令面板（⌘K）、股票详情抽屉、移动 Dock、Toast。
 */
import { Suspense, createContext, lazy, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router';
import Navbar from '@/components/Navbar';
import IndexTape from '@/components/IndexTape';
import Footer from '@/components/Footer';
import RouteErrorBoundary from '@/components/shared/RouteErrorBoundary';
import InlineFallback from '@/components/shared/InlineFallback';
import PageFallback from '@/components/shared/PageFallback';
import MobileDock from '@/components/MobileDock';
import CommandPalette from '@/components/CommandPalette';
import { pushRecent } from '@/lib/recentTickers';
import Drawer from '@/components/Drawer';
import { t as __t } from '../i18n/core.ts';

/* 抽屉体只在用户点开个股时才需要，独立分包 */
const StockDrawerBody = lazy(() => import('@/components/StockDrawerBody'));

interface ShellContextValue {
  openPalette: () => void;
  openTicker: (ticker: string) => void;
}

const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error(__t('useShell 必须在 <Layout> 内使用'));
  return ctx;
}

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerTicker, setDrawerTicker] = useState<string | null>(null);

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const openTicker = useCallback((t: string) => {
    pushRecent(t);
    setDrawerTicker(t);
  }, []);

  /* ⌘K / Ctrl+K 全局绑定 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const value = useMemo(() => ({ openPalette, openTicker }), [openPalette, openTicker]);

  return (
    <ShellContext.Provider value={value}>
      {/* overflow-x-clip：绝对定位的解释浮层即使处于 opacity-0 也占布局盒，
          窄屏时会把文档撑出横向滚动条。clip 只裁剪绘制，不建立滚动容器、
          不影响 sticky，也不裁剪 position:fixed 的 Dock 与抽屉；
          InfoHint 自身已把可见浮层收敛在视口内，因此这里裁不到真实内容。 */}
      <div className="flex min-h-[100dvh] flex-col overflow-x-clip">
        <Navbar onOpenPalette={openPalette} />
        <IndexTape />
        <main className="mx-auto w-full max-w-shell flex-1 px-4 pt-8 md:px-8">
          {/* page-fade 转场改纯 CSS（enter 240ms opacity + translateY(6px)）。
              原先 AnimatePresence mode="wait" + initial opacity:0 意味着新页面
              「默认不可见、靠一帧 JS 动画亮起来」——催化这类重页面在低端手机上
              挂载时主线程被塞死，动画迟迟不跑，用户看到的就是内容已挂载的全白
              页（手机菜单进催化白屏的根因之二）。CSS 动画基态即可见：动画只
              描述过渡，被饿死时最坏也只是直接出现。代价是去掉了 120ms 的退场
              淡出。 */}
          <div key={location.pathname} className="page-enter">
            {/* 按路由重建的错误边界:页面崩溃显示错误卡而非白屏,切页自动复位 */}
            <RouteErrorBoundary>
              <Suspense fallback={<PageFallback />}>
                <Outlet />
              </Suspense>
            </RouteErrorBoundary>
          </div>
        </main>
        <Footer />
        <MobileDock />
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onOpenTicker={(t) => setDrawerTicker(t)}
        onForceRefresh={() => navigate('/watchlist?force=1')}
      />

      {/* 全局股票详情抽屉（基座；内容由 stock-detail 代理完善） */}
      <Drawer
        open={drawerTicker !== null}
        onClose={() => setDrawerTicker(null)}
        label={drawerTicker ? __t('{ticker} 详情', { ticker: drawerTicker }) : undefined}
        title={
          drawerTicker && (
            <span className="flex items-baseline gap-2">
              <span className="font-display text-display-m text-ink-900">{drawerTicker}</span>
              <span className="eyebrow">STOCK DETAIL</span>
            </span>
          )
        }
      >
        {drawerTicker && (
          /* 抽屉内部用 InlineFallback：PageFallback 是整屏高的（为了压路由级 CLS），
             搬进抽屉只会先撑出一大片空白。 */
          <Suspense fallback={<InlineFallback />}>
            <StockDrawerBody ticker={drawerTicker} />
          </Suspense>
        )}
      </Drawer>
    </ShellContext.Provider>
  );
}
