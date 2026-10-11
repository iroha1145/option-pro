/**
 * 报告正文：有图表数据（brief.visuals）时显示图文版，否则显示原有文字版（children）。
 *
 * 图文版是 marketBriefReport.ts 拼好的静态 HTML/CSS，写进本组件自己的 Shadow DOM：
 * - 在 useLayoutEffect 里同步写入，和卡片其余部分同一帧排版、上屏，之后高度不再变。
 *   PR #253 的 iframe 要先显示文字、就绪后再换成框架，换的那一下会把下面的卡片推走。
 * - 生产页面的 CSP（script-src 'self'）会被 srcdoc 沙箱框架继承，Websandbox 的内联启动脚本
 *   在生产里跑不起来。图文版本来就不需要脚本：完成态协议里 jsFunctions、jsExpressions 不为空
 *   就退回文字版。
 * - 样式封在影子根里；颜色读宿主的 CSS 变量（自定义属性穿过影子边界继承），深浅色与涨跌配色
 *   切换时即时生效，不用重新生成内容。同一份内容轮询回来不重写，展开的明细不会被收起。
 */
import { Component, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import { isMock } from '@/api/client';
import type { MarketBrief } from '@/api/modules/marketBrief';
import { normalizeOpenGenUIContent } from '@/components/open-intelligent-ui/schema';
import { createMarketBriefReport } from './marketBriefReport';

class ReportBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function ShadowReport({ html, css }: { html: string; css: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    // StrictMode 与热更新会重复执行这段：已有影子根就复用（attachShadow 不能调两次）。
    const root = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>${css.replace(/<\/style/gi, '<\\/style')}</style>${html}`;
  }, [html, css]);
  return <div ref={hostRef} className="mt-3" data-market-brief-report="" />;
}

export default function MarketBriefModelOutput({ brief, year, children }: {
  brief: MarketBrief;
  year?: string;
  children: ReactNode;
}) {
  const report = useMemo(() => {
    const content = normalizeOpenGenUIContent(createMarketBriefReport(brief, { demo: isMock, year }));
    if (!content?.html || content.jsFunctions || content.jsExpressions?.length) return null;
    return { html: content.html.join(''), css: content.css ?? '' };
  }, [brief, year]);

  if (!report) return children;
  return (
    <ReportBoundary key={brief.runId} fallback={children}>
      <ShadowReport html={report.html} css={report.css} />
    </ReportBoundary>
  );
}
