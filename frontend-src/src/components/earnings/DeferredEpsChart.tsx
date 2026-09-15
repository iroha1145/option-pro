import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { EarningsRow } from './types';
import { t } from '../../i18n/core.ts';
import ChartLoadErrorBoundary from './ChartLoadErrorBoundary';
import { importEpsChart } from './epsChartLoader';

const PLACEHOLDER_HEIGHT_PX = 320;
const PRELOAD_ROOT_MARGIN = '100% 0px';

interface DeferredEpsChartProps {
  items: EarningsRow[];
}

const loadEpsHatchChart = () => lazy(() => importEpsChart(false));
const retryEpsHatchChart = () => lazy(() => importEpsChart(true));

export default function DeferredEpsChart({ items }: DeferredEpsChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);
  // 初始化与重试都在 render 之外换新 lazy()：被拒绝的工厂不能复用，
  // 也不能在 render 里 useMemo 出新组件（eslint react-hooks/static-components）。
  const [EpsHatchChart, setEpsHatchChart] = useState(loadEpsHatchChart);
  const hasRows = items.some((row) => row.epsEstimate != null || row.epsActual != null);

  useEffect(() => {
    if (mounted || !hasRows) return;
    const node = hostRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) setMounted(true);
      },
      { root: null, rootMargin: PRELOAD_ROOT_MARGIN, threshold: 0 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [mounted, hasRows]);

  if (!hasRows) return null;

  return (
    <div
      ref={hostRef}
      style={{ minHeight: PLACEHOLDER_HEIGHT_PX }}
      data-eps-chart-slot=""
    >
      {mounted ? (
        <ChartLoadErrorBoundary
          onRetry={() => setEpsHatchChart(retryEpsHatchChart)}
        >
          <Suspense
            fallback={(
              <section
                className="card-surface p-5"
                style={{ minHeight: PLACEHOLDER_HEIGHT_PX }}
                aria-label={t('EPS 图表加载中')}
              />
            )}
          >
            <EpsHatchChart items={items} />
          </Suspense>
        </ChartLoadErrorBoundary>
      ) : (
        <section
          className="card-surface p-5"
          style={{ minHeight: PLACEHOLDER_HEIGHT_PX }}
          aria-label={t('EPS 预期与实际对照图')}
        />
      )}
    </div>
  );
}
