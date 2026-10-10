/** UMD loader adapted from OpenIntelligentUI, f6e4388 (MIT). */
export interface SandboxInstance {
  iframe: HTMLIFrameElement;
  promise: Promise<unknown>;
  run: (code: string) => Promise<unknown>;
  destroy: () => void;
}
export interface WebsandboxModule {
  create: (localApi: Record<string, (args: unknown) => unknown>, options: {
    frameContainer: HTMLElement;
    frameContent: string;
    sandboxAdditionalAttributes: string;
    allowAdditionalAttributes: string;
  }) => SandboxInstance;
}
type WebsandboxNamespace = { default?: WebsandboxModule & { default?: WebsandboxModule } };
export async function loadWebsandbox(): Promise<WebsandboxModule> {
  const mod = (await import('@jetbrains/websandbox')) as WebsandboxNamespace;
  const sandbox = mod.default?.default ?? mod.default;
  if (!sandbox || typeof sandbox.create !== 'function') throw new Error('Report renderer unavailable');
  return sandbox;
}
