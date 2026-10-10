/** Completion protocol adapted from OpenIntelligentUI, f6e4388 (MIT). */
export interface OpenGenUIContent {
  initialHeight?: number;
  generating?: boolean;
  css?: string;
  cssComplete?: boolean;
  html?: string[];
  htmlComplete?: boolean;
  jsFunctions?: string;
  jsFunctionsComplete?: boolean;
  jsExpressions?: string[];
  jsExpressionsComplete?: boolean;
}

export const MAX_REPORT_HEIGHT = 20_000;
export function clampReportedHeight(height: unknown): number | null {
  if (typeof height !== 'number' || !Number.isFinite(height)) return null;
  return Math.max(50, Math.min(Math.ceil(height), MAX_REPORT_HEIGHT));
}

/** A stable, whitelisted snapshot: unfinished or malformed output never runs. */
export function normalizeOpenGenUIContent(content: unknown): OpenGenUIContent | null {
  if (!content || typeof content !== 'object' || Array.isArray(content)) return null;
  const value = content as Record<string, unknown>;
  const strings = (items: unknown): items is string[] => Array.isArray(items) && items.length <= 4096
    && items.every(item => typeof item === 'string' && item.length <= 512_000);
  for (const flag of ['cssComplete', 'jsFunctionsComplete', 'jsExpressionsComplete']) {
    if (value[flag] !== undefined && value[flag] !== true) return null;
  }
  if (value.generating !== false || value.htmlComplete !== true || !strings(value.html) || !value.html.join('').trim()) return null;
  if (value.css !== undefined && (typeof value.css !== 'string' || value.cssComplete !== true)) return null;
  if (value.jsFunctions !== undefined && (typeof value.jsFunctions !== 'string' || value.jsFunctionsComplete !== true)) return null;
  if (value.jsExpressions !== undefined && (!strings(value.jsExpressions) || value.jsExpressionsComplete !== true)) return null;
  if (value.initialHeight !== undefined && clampReportedHeight(value.initialHeight) === null) return null;
  const snapshot: OpenGenUIContent = {
    initialHeight: clampReportedHeight(value.initialHeight) ?? 200,
    generating: false,
    css: (value.css as string | undefined) ?? '', cssComplete: true,
    html: value.html, htmlComplete: true,
    jsFunctions: (value.jsFunctions as string | undefined) ?? '', jsFunctionsComplete: true,
    jsExpressions: (value.jsExpressions as string[] | undefined) ?? [], jsExpressionsComplete: true,
  };
  const signature = JSON.stringify(snapshot);
  return signature.length <= 512_000 ? snapshot : null;
}

export function completedContentSignature(content: unknown): string | null {
  const normalized = normalizeOpenGenUIContent(content);
  return normalized ? JSON.stringify(normalized) : null;
}

/** Filter before upstream transport sees messages from generated code. */
export function isAllowedSandboxMessage(data: unknown): boolean {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  const value = data as Record<string, unknown>;
  if (value.type === '__ogui_resize') return clampReportedHeight(value.height) !== null;
  if (value.type === '__ogui_report_error') return true;
  const numericId = typeof value.callId === 'string' && /^\d{1,16}$/.test(value.callId);
  if (!numericId) return false;
  if (value.type === 'response') return typeof value.success === 'boolean';
  return value.type === 'service-message' && value.methodName === 'iframeInitialized'
    && Array.isArray(value.arguments) && value.arguments.length === 0;
}
