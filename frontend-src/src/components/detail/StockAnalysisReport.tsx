import { Component, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import { normalizeOpenGenUIContent } from '../open-intelligent-ui/schema';
import { createStockAnalysisReport } from './stockReportTemplate';
class ReportBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
function TrustedReport({ html, css }: { html: string; css: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const root = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
    // Only createStockAnalysisReport's fixed template reaches this sink.
    root.innerHTML = `<style>${css.replace(/<\/style/gi, '<\\/style')}</style>${html}`;
  }, [html, css]);
  return <div ref={hostRef} data-stock-analysis-report="" />;
}
export default function StockAnalysisReport({ result, visuals, fallback }: { result: unknown; visuals: unknown; fallback: ReactNode }) {
  const content = useMemo(() => normalizeOpenGenUIContent(createStockAnalysisReport(result, visuals)), [result, visuals]);
  if (!content?.html || content.jsFunctions || content.jsExpressions?.length) return fallback;
  return <ReportBoundary key={content.html.join('')} fallback={fallback}><TrustedReport html={content.html.join('')} css={content.css ?? ''} /></ReportBoundary>;
}
