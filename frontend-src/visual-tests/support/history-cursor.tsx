import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { useEffect, useState } from 'react';
import MacroHistoryChart from '../../src/components/market/macro/MacroHistoryChart';
import PositionHistoryChart from '../../src/components/cta/PositionHistoryChart';
import { echarts } from '../../src/lib/chart';
import type { MacroHistoryPoint } from '../../src/api/modules/macro';
import '../../src/index.css';

function makePoints(count: number, start = 0): MacroHistoryPoint[] {
  return Array.from({ length: count }, (_, i) => ({
    date: new Date(Date.UTC(2026, 0, start + i + 1)).toISOString().slice(0, 10),
    score: i === 1 ? null : 30 + i % 20,
    confidence: 1,
    regime: null,
    historyBasis: i < count / 2 ? 'latest_revised_backfill' : 'local_point_in_time',
    moduleScores: {},
  }));
}

declare global {
  interface Window {
    historyCursorHarness: {
      replace(count: number, start?: number): string;
      replaceAfterResize(kind: 'macro' | 'position', count: number, start: number): string;
      refreshThenReplace(kind: 'macro' | 'position', count: number, start: number): string;
      refresh(): void;
      inspect(kind: 'macro' | 'position'): { pointerStatus: unknown; emphasized: boolean };
      pixel(kind: 'macro' | 'position', index: number): { x: number; y: number };
    };
  }
}

function instance(kind: 'macro' | 'position') {
  const element = document.querySelector<HTMLElement>(`[data-testid="${kind}-chart"] [role="img"]`)!;
  return { element, chart: echarts.getInstanceByDom(element)! };
}

export function Harness() {
  const [points, setPoints] = useState(() => makePoints(120));
  useEffect(() => {
    window.historyCursorHarness = {
      // Represents a pending history request resolving, without moving keyboard or pointer focus.
      replace(count, start = 0) {
        const next = makePoints(count, start);
        setPoints(next);
        return next.at(-1)?.date ?? '';
      },
      replaceAfterResize(kind, count, start) {
        // Resize queues ECharts' tooltip refresh; commit the response before that callback runs.
        instance(kind).chart.resize();
        const next = makePoints(count, start);
        flushSync(() => setPoints(next));
        return next.at(-1)?.date ?? '';
      },
      refreshThenReplace(kind, count, start) {
        instance(kind).chart.resize();
        flushSync(() => setPoints((current) => current.map((point) => ({ ...point, score: 70 }))));
        const next = makePoints(count, start);
        flushSync(() => setPoints(next));
        return next.at(-1)?.date ?? '';
      },
      refresh() {
        flushSync(() => setPoints((current) => current.map((point) => ({ ...point, score: 70 }))));
      },
      inspect(kind) {
        const { chart } = instance(kind);
        const axis = (chart.getOption().xAxis as { axisPointer?: { status?: unknown } }[])[0];
        return {
          pointerStatus: axis.axisPointer?.status,
          emphasized: chart.getZr().storage.getDisplayList().some((element) => element.currentStates.includes('emphasis')),
        };
      },
      pixel(kind, index) {
        const { element, chart } = instance(kind);
        const bounds = element.getBoundingClientRect();
        return { x: bounds.x + (chart.convertToPixel({ xAxisIndex: 0 }, index) as number), y: bounds.y + chart.getHeight() / 2 };
      },
    };
  }, []);
  return <main className="grid grid-cols-2 gap-6 p-8">
    <div data-testid="macro-chart"><MacroHistoryChart points={points} modules={[]} range="1Y" loading={false} onRangeChange={() => {}} /></div>
    <div data-testid="position-chart"><PositionHistoryChart history={points.map((point) => ({ date: point.date, position: (point.score ?? 30) - 60 }))} /></div>
    <button type="button">离开图表</button>
  </main>;
}

createRoot(document.getElementById('root')!).render(<Harness />);
