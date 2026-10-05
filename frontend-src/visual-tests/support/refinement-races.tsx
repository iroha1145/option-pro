import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import FactorDetails from '../../src/components/market/macro/FactorDetails';
import type { MacroModule } from '../../src/api/modules/macro';
import { prepareI18n } from '../../src/i18n/boot';
import '../../src/index.css';

const modules: MacroModule[] = [{
  moduleId: 'risk', nameZh: '风险', nameEn: 'RISK', score: 40,
  scoreChange7d: null, confidence: 1, validFactorCount: 1,
  totalFactorCount: 1, minimumValidFactors: 1,
  dataThrough: '2026-10-02', status: 'ok',
}];

await prepareI18n();
export function Harness() {
  const [snapshotKey, setSnapshotKey] = useState('old');
  return <main className="mx-auto max-w-4xl p-8">
    <button type="button" onClick={() => setSnapshotKey('new')}>切换新快照</button>
    <p role="status">快照：{snapshotKey}</p>
    <FactorDetails modules={modules} snapshotKey={snapshotKey} />
  </main>;
}
createRoot(document.getElementById('root')!).render(<Harness />);
