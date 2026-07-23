import { Navigate, Route, Routes } from 'react-router';
import Layout from '@/components/Layout';
import { AccessProvider } from '@/hooks/useAccess';
import { ToastProvider } from '@/components/Toast';
import Watchlist from '@/pages/Watchlist';
import Login from '@/pages/Login';
import Screener from '@/pages/Screener';
import Breakouts from '@/pages/Breakouts';
import Sectors from '@/pages/Sectors';
import Earnings from '@/pages/Earnings';
import Catalysts from '@/pages/Catalysts';
import StockDetail from '@/pages/StockDetail';
import Market from '@/pages/Market';

export default function App() {
  return (
    <AccessProvider>
      <ToastProvider>
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
      </ToastProvider>
    </AccessProvider>
  );
}
