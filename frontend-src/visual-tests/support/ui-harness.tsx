import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import { ToastProvider } from '../../src/components/Toast';
import { useToast } from '../../src/hooks/useToast';
import MenuSelect from '../../src/components/shared/MenuSelect';
import DataTable from '../../src/components/shared/DataTable';
import '../../src/index.css';
import '../../src/styles/transitions-root.css';
import '../../src/styles/transitions-catalog.css';

export function Harness() {
  const toast = useToast();
  const [value, setValue] = useState(0);
  const [row, setRow] = useState('');
  return <main className="p-8">
    <button onClick={() => { for (let i = 1; i <= 12; i++) toast.info(`通知 ${i}`); }}>批量通知</button>
    <button onClick={() => toast.error('加载失败', '请稍后重试')}>错误通知</button>
    <div className="mt-8 h-14 overflow-hidden border">
      <MenuSelect ariaLabel="测试选择" value={value} onChange={setValue} options={[
        { value: 0, label: '全部' }, { value: 10, label: '前十项' }, { value: 20, label: '前二十项' },
      ]} />
    </div>
    <output aria-label="选择结果">{value}</output>
    <DataTable rowKey={(r) => r.id} onRowClick={(r) => setRow(r.id)} rows={[{ id: 'A', score: 5 }, { id: 'B', score: NaN }, { id: 'C', score: 1 }]} columns={[
      { key: 'id', title: '标的', render: (r) => <button onClick={() => setRow(r.id)}>{r.id}</button> },
      { key: 'score', title: '强度', sortable: true, sortValue: (r) => r.score, render: (r) => Number.isFinite(r.score) ? r.score : '—' },
    ]} />
    <output aria-label="选中标的">{row}</output>
  </main>;
}
createRoot(document.getElementById('root')!).render(<ToastProvider><Harness /></ToastProvider>);
