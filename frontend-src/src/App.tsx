import { Suspense, lazy } from 'react';
import { MotionConfig } from 'framer-motion';
import { Navigate, Route, Routes } from 'react-router';
import Layout from '@/components/Layout';
import { AccessProvider } from '@/hooks/useAccess';
import { ToastProvider } from '@/components/Toast';
import AppErrorBoundary from '@/components/shared/AppErrorBoundary';
import PageFallback from '@/components/shared/PageFallback';
import NotFound from '@/pages/NotFound';

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
const CtaTrend = lazy(() => import('@/pages/CtaTrend'));

export default function App() {
  return (
    /* 顶级边界必须在 AccessProvider 之外：身份 Provider、命令面板与全局抽屉都在
       路由错误边界的作用范围之外，它们抛异常时旧结构会整站白屏（审计 P1-09）。 */
    <AppErrorBoundary>
      {/* framer 动画全局尊重系统「减少动态效果」（审计 2.5.2）：index.css 的
          prefers-reduced-motion 块只覆盖 CSS 动画，覆盖不到 framer 写入的
          内联 transform/opacity——整页转场、抽屉滑入、逐行 stagger 都要靠它。 */}
      <MotionConfig reducedMotion="user">
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
                <Route path="/cta" element={<CtaTrend />} />
                <Route path="/stock/:ticker" element={<StockDetail />} />
                {/* 未知路由显示 404，不再静默重定向到自选（审计 P3-6）：
                    重定向会掩盖失效链接与部署缺页，也会让自动化测试看不出路由问题。 */}
                <Route path="*" element={<NotFound />} />
              </Route>
            </Routes>
          </Suspense>
        </ToastProvider>
      </AccessProvider>
      </MotionConfig>
    </AppErrorBoundary>
  );
}
