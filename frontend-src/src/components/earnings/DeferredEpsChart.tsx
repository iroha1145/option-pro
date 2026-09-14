import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { EarningsRow } from './types';
import { t } from '../../i18n/core.ts';

const EpsHatchChart = lazy(() => import('./EpsHatchChart'));

const PLACEHOLDER_HEIGHT_PX = 320;
const PRELOAD_ROOT_MARGIN = '100% 0px';

interface DeferredEpsChartProps {
  items: EarningsRow[];
}

export default function DeferredEpsChart({ items }: DeferredEpsChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);
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
