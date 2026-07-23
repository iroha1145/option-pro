/**
 * Layout（design.md §7）
 * sticky Header + IndexTape + 内容槽（<Outlet/>，page-fade 转场）+ Footer
 * 全局：命令面板（⌘K）、股票详情抽屉、移动 Dock、Toast。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import Navbar from '@/components/Navbar';
import IndexTape from '@/components/IndexTape';
import Footer from '@/components/Footer';
import MobileDock from '@/components/MobileDock';
import CommandPalette, { pushRecent } from '@/components/CommandPalette';
import Drawer from '@/components/Drawer';
import StockDrawerBody from '@/components/StockDrawerBody';

interface ShellContextValue {
  openPalette: () => void;
  openTicker: (ticker: string) => void;
}

const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error('useShell 必须在 <Layout> 内使用');
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
      <div className="flex min-h-[100dvh] flex-col">
        <Navbar onOpenPalette={openPalette} />
        <IndexTape />
        <main className="mx-auto w-full max-w-shell flex-1 px-4 pt-8 md:px-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.16 } }}
              transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
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
        title={
          drawerTicker && (
            <span className="flex items-baseline gap-2">
              <span className="font-display text-display-m text-ink-900">{drawerTicker}</span>
              <span className="eyebrow">STOCK DETAIL</span>
            </span>
          )
        }
      >
        {drawerTicker && <StockDrawerBody ticker={drawerTicker} />}
      </Drawer>
    </ShellContext.Provider>
  );
}
