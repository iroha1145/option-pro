import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactECharts from '@/components/charts/ReactECharts';
import AutoHeight from '@/components/shared/AutoHeight';
import type { ChartOption, EChartsInstance } from '@/lib/chart';
import '@/styles/transitions-root.css';
import '@/index.css';
import '@/styles/transitions-catalog.css';
import '@/styles/mobile-performance.css';

const option: ChartOption = {
  animation: true,
  xAxis: { type: 'category', data: ['A', 'B', 'C', 'D'] },
  yAxis: { type: 'value' },
  dataZoom: [{ type: 'inside', start: 10, end: 90 }],
  series: [{ type: 'line', data: [2, 4, 3, 6] }],
};
const metrics = { sets: 0, resizes: 0, chart: null as EChartsInstance | null };
Object.assign(window, { mobileChart: metrics });
function init(chart: EChartsInstance) {
  metrics.chart = chart;
  const set = chart.setOption.bind(chart);
  const resize = chart.resize.bind(chart);
  chart.setOption = (...args: Parameters<typeof set>) => { metrics.sets++; return set(...args); };
  chart.resize = (...args: Parameters<typeof resize>) => { metrics.resizes++; return resize(...args); };
}
export function Probe() {
  const [step, setStep] = useState(1);
  const [clicks, setClicks] = useState(0);
  const [list, setList] = useState(false);
  const [mounted, setMounted] = useState(true);
  return <main>
    <button onClick={() => setStep(value => value + 1)}>Change handler</button>
    <button onClick={() => setStep(0)}>Remove handler</button>
    <button onClick={() => setList(value => !value)}>Resize content</button>
    <button onClick={() => setMounted(false)}>Unmount chart</button>
    <output>{clicks}</output>
    <AutoHeight><div style={{ height: list ? 400 : 40 }}>Details</div></AutoHeight>
    {mounted && <div id="chart-wrap" style={{ width: '100%', height: 220 }}>
      <ReactECharts option={option} onInit={init} onClick={step ? () => setClicks(value => value + step) : undefined} ariaLabel="Performance chart" />
    </div>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Probe />);
