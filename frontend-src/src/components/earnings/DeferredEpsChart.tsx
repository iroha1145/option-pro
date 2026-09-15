import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { EarningsRow } from './types';
import { t } from '../../i18n/core.ts';
import ChartLoadErrorBoundary from './ChartLoadErrorBoundary';

const PLACEHOLDER_HEIGHT_PX = 320;
const PRELOAD_ROOT_MARGIN = '100% 0px';

interface DeferredEpsChartProps {
  items: EarningsRow[];
}

function chartChunkHref(generation: number) {
  const load = () => import('./EpsHatchChart');
  const fromImporter = String(load).match(/["'`]([^"'`]*EpsHatchChart[^"'`?]*)["'`]/)?.[1];
  let resolved = import.meta.resolve('./EpsHatchChart');
  if (fromImporter && /\.js$/i.test(fromImporter)) {
    if (/^https?:/i.test(fromImporter) || fromImporter.startsWith('/')) {
      resolved = fromImporter;
    } else if (fromImporter.includes('assets/')) {
      resolved = new URL(fromImporter.replace(/^\.\//, ''), `${window.location.origin}/`).href;
    } else {
      resolved = new URL(fromImporter.replace(/^\.\//, ''), new URL('/assets/', window.location.origin)).href;
    }
  }
  const url = new URL(resolved, window.location.href);
  url.searchParams.set('recover', String(generation));
  return url.href;
}

function loadEpsHatchChart(generation = 0) {
  // First load stays a static import so Vite emits one async chunk.
  // Retry resolves that same chunk and changes the query; browsers cache a
  // rejected specifier and will not refetch the identical module URL.
  return lazy(() => (
    generation === 0
      ? import('./EpsHatchChart')
      : import(/* @vite-ignore */ chartChunkHref(generation))
  ));
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
