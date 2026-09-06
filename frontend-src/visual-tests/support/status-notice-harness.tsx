import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import IvPanel from '../../src/components/sectors/IvPanel';
import StaleStrip from '../../src/components/shared/StaleStrip';
import type { IvMetaVm, IvRowVm } from '../../src/components/sectors/model';
import '../../src/index.css';
import '../../src/styles/transitions-root.css';
import '../../src/styles/transitions-catalog.css';

const rows: IvRowVm[] = [
  { ticker: 'AAPL', name: '苹果', price: 218.42, priceProvider: 'fixture', rank: 12, atmIv: 23.4, stale: true, asOf: '2026-09-04T20:00:00Z' },
  { ticker: 'MSFT', name: '微软', price: 441.25, priceProvider: 'fixture', rank: 68, atmIv: 35.1, stale: true, asOf: '2026-09-04T20:00:00Z' },
];
const meta: IvMetaVm = { status: 'stale', stale: true, asOf: '2026-09-04T20:00:00Z',
  dataLimited: false, successCount: 2, requestedCount: 2, successRate: 1,
  failedSymbols: [], snapshotSource: 'fixture', snapshotOrigin: null, providers: ['fixture'] };

export function Harness() {
  const [selected, setSelected] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  return <main className="mx-auto max-w-6xl px-4 py-8 sm:px-8">
    <p className="mb-4 text-caption text-ink-500">模拟过期数据 · 不连接行情服务</p>
    <IvPanel sectors={[{ id: 'technology', name: '软件基础设施' }]} sectorId="technology"
      onSectorChange={() => undefined} data={rows} meta={meta} loading={false} error={null}
      onRetry={() => setRefreshing(true)} onOpenTicker={setSelected} />
    <StaleStrip className="mt-4" refreshing={refreshing} onRetry={() => setRefreshing(true)} />
    <output aria-label="选中股票" className="mt-4 block">{selected}</output>
    <output aria-label="刷新状态">{refreshing ? '刷新中' : '待刷新'}</output>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Harness />);
