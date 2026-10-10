import { Component, lazy, Suspense, useMemo, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { isMock } from '@/api/client';
import type { MarketBrief } from '@/api/modules/marketBrief';
import MenuSelect from '@/components/shared/MenuSelect';
import { useAppearance } from '@/hooks/useAppearance';
import { useColorMode } from '@/hooks/useColorMode';
import { t } from '@/i18n/core';
import { createMarketBriefOpenUIPreview } from '@/mocks/marketBriefOpenUI';
import { SERVER_BRIEF_ARCHIVE } from '@/mocks/marketBriefServerArchive';
import { useMarketBriefArchiveSelection } from './marketBriefPreview';

const OpenIntelligentUIOutput = lazy(() => import('../open-intelligent-ui/renderer'));

class ReportRendererBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

// Only selected presentation tokens cross into the opaque report frame.
const REPORT_TOKENS = {
  ink: '--ink-900',
  body: '--ink-600',
  muted: '--ink-500',
  paper: '--card',
  soft: '--paper-2',
  line: '--line',
  brand: '--brand-600',
  up: '--up-700',
  down: '--down-700',
  warn: '--warn-700',
  ok: '--ok-700',
  'radius-control': '--r-control',
  'radius-pill': '--r-pill',
} as const;

function reportThemeCss(): string {
  if (typeof document === 'undefined') return '';
  const style = getComputedStyle(document.documentElement);
  return `:root{${Object.entries(REPORT_TOKENS)
    .map(([name, token]) => `--report-${name}:${style.getPropertyValue(token).trim()};`)
    .join('')}--report-radius-card:18px;--report-font:${getComputedStyle(document.body).fontFamily};}`;
}

/** Changes only the model narrative; the card, coverage and sources stay with the host. */
export default function MarketBriefModelOutput({ brief, children }: {
  brief: MarketBrief;
  children: ReactNode;
}) {
  useAppearance();
  useColorMode();
  const [params, setParams] = useSearchParams();
  const archive = useMarketBriefArchiveSelection();
  // A local mock preview is explicitly requested by the URL. Never substitute it in live mode.
  const preview = import.meta.env.DEV && isMock
    && params.get('report') === 'openui';
  const content = useMemo(
    () => preview ? createMarketBriefOpenUIPreview(brief, archive?.visuals) : null,
    [preview, brief, archive],
  );

  if (!content) return children;
  // 只在预览时读主题样式；生产里卡片每次渲染都走上面的 return，不碰 getComputedStyle。
  const themeCss = reportThemeCss();
  return (
    <>
      {archive && (
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-caption text-ink-500">
          <span>{t('服务器研判存档')}</span>
          <MenuSelect
            ariaLabel={t('服务器研判存档')}
            value={archive.runId}
            className="max-w-full"
            triggerClassName="min-h-10 rounded-[10px] border-line px-3 py-2 text-ink-800"
            options={SERVER_BRIEF_ARCHIVE.map(item => ({ value: item.runId, label: item.label }))}
            onChange={runId => {
              const next = new URLSearchParams(params);
              next.set('run', runId);
              setParams(next, { replace: true, preventScrollReset: true });
            }}
          />
          <span>{t('原文保留 · 图表取自同次证据')}</span>
        </div>
      )}
      <ReportRendererBoundary key={brief.runId} fallback={children}>
        <Suspense fallback={children}>
          <OpenIntelligentUIOutput
            content={content}
            fallback={children}
            title={t('市场报告图文正文')}
            themeCss={themeCss}
          />
        </Suspense>
      </ReportRendererBoundary>
    </>
  );
}
