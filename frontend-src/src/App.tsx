import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router';
import Layout from '@/components/Layout';
import { AccessProvider } from '@/hooks/useAccess';
import { ToastProvider } from '@/components/Toast';
import PageFallback from '@/components/shared/PageFallback';

/* 路由级代码分割：页面按需加载，echarts 等重依赖不进首屏包 */
const Watchlist = lazy(() => import('@/pages/Watchlist'));
const Login = lazy(() => import('@/pages/Login'));
const Screener = lazy(() => import('@/pages/Screener'));
const Breakouts = lazy(() => import('@/pages/Breakouts'));
const Sectors = lazy(() => import('@/pages/Sectors'));
const Earnings = lazy(() => import('@/pages/Earnings'));
const Catalysts = lazy(() => import('@/pages/Catalysts'));
const StockDetail = lazy(() => import('@/pages/StockDetail'));
const Market = lazy(() => import('@/pages/Market'));

export default function App() {
  return (
    <AccessProvider>
      <ToastProvider>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<Layout />}>
              <Route index element={<Navigate to="/watchlist" replace />} />
              <Route path="/watchlist" element={<Watchlist />} />
              <Route path="/screener" element={<Screener />} />
              <Route path="/breakouts" element={<Breakouts />} />
              <Route path="/sectors" element={<Sectors />} />
              <Route path="/earnings" element={<Earnings />} />
              <Route path="/catalysts" element={<Catalysts />} />
              <Route path="/market" element={<Market />} />
              <Route path="/stock/:ticker" element={<StockDetail />} />
              <Route path="*" element={<Navigate to="/watchlist" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </ToastProvider>
    </AccessProvider>
  );
}
