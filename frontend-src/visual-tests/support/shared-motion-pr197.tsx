import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import CollapsePresence from '@/components/shared/CollapsePresence';
import IconSwap, { BusyIcon } from '@/components/shared/IconSwap';
import Spinner from '@/components/shared/Spinner';
import '@/styles/transitions-root.css';
import '@/index.css';
import '@/styles/transitions-catalog.css';

export default function Probe() {
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState(false);
  return <main>
    <button id="toggle" onClick={() => setOpen(value => !value)}>展开或收起</button>
    <CollapsePresence open={open} id="panel">
      <button id="inside">打开详情</button>
      <a href="#destination" id="inside-link">相关突破事件</a>
    </CollapsePresence>
    <button id="after">下一个按钮</button>
    <button id="busy-toggle" onClick={() => setBusy(value => !value)}>切换加载</button>
    <BusyIcon busy={busy} />
    <span id="nested-check"><IconSwap state={busy ? 'b' : 'a'} a={<IconSwap state="b" a={<span>+</span>} b={<span>✓</span>} />} b={<Spinner />} /></span>
    <span id="nested-busy"><IconSwap state={busy ? 'b' : 'a'} a={<span>准备</span>} b={<BusyIcon busy />} /></span>
  </main>;
}

createRoot(document.getElementById('root')!).render(<Probe />);
