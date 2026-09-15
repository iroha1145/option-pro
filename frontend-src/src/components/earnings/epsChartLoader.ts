import chartChunkUrl from 'virtual:eps-chart-url';

let recoveryGeneration = 0;
let loadedChart: typeof import('./EpsHatchChart') | null = null;

/** Shared across remounts: never reuse a previously failed recovery URL. */
export function epsChartHref(retry: boolean): string {
  const url = new URL(chartChunkUrl, window.location.href);
  // Isolate even the first EPS failure from other routes' static chart imports.
  // Their canonical URL must retain an independent browser module record.
  url.searchParams.set('eps', '1');
  if (retry) url.searchParams.set('recover', String(++recoveryGeneration));
  return url.href;
}

export function importEpsChart(retry: boolean) {
  if (loadedChart) return Promise.resolve(loadedChart);
  return (import(/* @vite-ignore */ epsChartHref(retry)) as Promise<typeof import('./EpsHatchChart')>)
    .then((chart) => {
      // Once recovery succeeds, later visits reuse it instead of importing the
      // initial EPS URL whose failed module record still lives in this document.
      loadedChart ??= chart;
      return loadedChart;
    });
}
