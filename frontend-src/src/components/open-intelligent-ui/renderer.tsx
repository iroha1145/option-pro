/** Completed-output renderer adapted from OpenIntelligentUI f6e4388 (MIT).
 * Retains JetBrains Websandbox and the final-frame execution lifecycle.
 * Streaming, chat/tool bridges, export and remote CDN support are omitted.
 */
import { memo, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { t } from '@/i18n/core';
import { buildFinalFrameContent, ERROR_MESSAGE_TYPE, MEASUREMENT_JS, RESIZE_MESSAGE_TYPE } from './frame-content';
import { clampReportedHeight, completedContentSignature, isAllowedSandboxMessage } from './schema';
import type { OpenGenUIContent } from './schema';
import { loadWebsandbox } from './websandbox-loader';
import type { SandboxInstance } from './websandbox-loader';

export type { OpenGenUIContent } from './schema';
export interface OpenIntelligentUIOutputProps {
  content?: unknown;
  fallback: ReactNode;
  title?: string;
  themeCss?: string;
}
export const RENDER_TIMEOUT_MS = 15_000;

const CompletedOutput = memo(function CompletedOutput({ signature, fallback, title, themeCss }: {
  signature: string; fallback: ReactNode; title: string; themeCss: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(() => (JSON.parse(signature) as OpenGenUIContent).initialHeight ?? 200);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const content = JSON.parse(signature) as OpenGenUIContent;
    let disposed = false;
    let failed = false;
    let sandbox: SandboxInstance | null = null;
    const destroy = () => {
      const current = sandbox;
      sandbox = null;
      if (current) {
        try { current.destroy(); } catch { current.iframe.remove(); }
      }
    };
    const fail = () => {
      if (disposed || failed) return;
      failed = true;
      clearTimeout(timeout);
      destroy();
      setStatus('failed');
    };
    const timeout = setTimeout(fail, RENDER_TIMEOUT_MS);
    const active = () => !disposed && !failed;
    const onMessage = (event: MessageEvent) => {
      if (!active() || !sandbox || event.source !== sandbox.iframe.contentWindow) return;
      if (!isAllowedSandboxMessage(event.data)) { event.stopImmediatePropagation(); return; }
      if (event.data?.type === ERROR_MESSAGE_TYPE) { fail(); return; }
      if (event.data?.type === RESIZE_MESSAGE_TYPE) {
        const next = clampReportedHeight(event.data.height);
        if (next !== null) setHeight(next);
      }
    };
    window.addEventListener('message', onMessage, true);
    // One caught async chain preserves functions-before-expressions order and
    // cancels between awaits. Each new snapshot owns a fresh sandbox.
    void (async () => {
      const Websandbox = await loadWebsandbox();
      if (!active()) return;
      sandbox = Websandbox.create(Object.create(null) as Record<string, (args: unknown) => unknown>, {
        frameContainer: container,
        frameContent: buildFinalFrameContent(content.html!.join(''), content.css, themeCss),
        sandboxAdditionalAttributes: '',
        allowAdditionalAttributes: '',
      });
      sandbox.iframe.title = title;
      sandbox.iframe.setAttribute('loading', 'eager');
      sandbox.iframe.setAttribute('referrerpolicy', 'no-referrer');
      sandbox.iframe.style.cssText = 'display:block;width:100%;height:100%;border:0;background:transparent;';
      const current = sandbox;
      await current.promise;
      if (!active()) return;
      if (content.jsFunctions) {
        await current.run(content.jsFunctions);
        if (!active()) return;
      }
      for (const expression of content.jsExpressions ?? []) {
        await current.run(expression);
        if (!active()) return;
      }
      await current.run(MEASUREMENT_JS);
      if (!active()) return;
      clearTimeout(timeout);
      setStatus('ready');
    })().catch(fail);
    return () => {
      disposed = true;
      clearTimeout(timeout);
      window.removeEventListener('message', onMessage, true);
      destroy();
    };
  }, [signature, themeCss, title]);

  return <div style={{ position: 'relative' }} aria-busy={status === 'loading'}>
    {status !== 'ready' && <>
      {status === 'failed' && <p role="status">{t('图文展示暂时无法打开，已显示文字报告。')}</p>}
      {status === 'loading' && <span role="status" className="sr-only">{t('正在准备图文报告')}</span>}
      {fallback}
    </>}
    <div ref={containerRef} aria-hidden={status !== 'ready'} style={{
      width: '100%', height: `${height}px`,
      position: status === 'ready' ? 'relative' : 'absolute',
      top: 0, visibility: status === 'ready' ? 'visible' : 'hidden',
      // The iframe keeps its own scrollbars for reports above 20,000px.
    }} />
  </div>;
});

export default function OpenIntelligentUIOutput({ content, fallback, title = t('市场报告'), themeCss = '' }: OpenIntelligentUIOutputProps) {
  const signature = completedContentSignature(content);
  if (signature === null) return <>{fallback}</>;
  // Object identities change on polling. Content bytes control the lifecycle.
  return <CompletedOutput key={JSON.stringify([signature, themeCss, title])} signature={signature} fallback={fallback} title={title} themeCss={themeCss} />;
}
