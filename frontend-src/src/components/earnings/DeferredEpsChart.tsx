import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { EarningsRow } from './types';
import { t } from '../../i18n/core.ts';
import ChartLoadErrorBoundary from './ChartLoadErrorBoundary';

const PLACEHOLDER_HEIGHT_PX = 320;
const PRELOAD_ROOT_MARGIN = '100% 0px';

interface DeferredEpsChartProps {
  items: EarningsRow[];
}

function withRecoverQuery(href: string, generation: number) {
  const url = new URL(href, window.location.href);
  url.searchParams.set('recover', String(generation));
  return url.href;
}

function loadEpsHatchChart(generation = 0) {
  // First load stays a static import so Vite still emits one async chunk.
  // Retry must change the module URL: browsers cache a rejected specifier.
  return lazy(async () => {
    if (generation === 0) {
      return import('./EpsHatchChart');
    }
    const href = (await import('./EpsHatchChart?url')).default;
    return import(/* @vite-ignore */ withRecoverQuery(href, generation));
  });
}

export default function DeferredEpsChart({ items }: DeferredEpsChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const recoverGen = useRef(0);
  const [mounted, setMounted] = useState(false);
  // 初始化与重试都在 render 之外换新 lazy()：被拒绝的工厂不能复用，
  // 也不能在 render 里 useMemo 出新组件（eslint react-hooks/static-components）。
  const [EpsHatchChart, setEpsHatchChart] = useState(() => loadEpsHatchChart(0));
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
          onRetry={() => {
            recoverGen.current += 1;
            setEpsHatchChart(loadEpsHatchChart(recoverGen.current));
          }}
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
