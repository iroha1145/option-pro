/** Non-streaming JSON transport. The deadline includes response BODY download.
 * Keep the original Response (URL, headers, status, clone behaviour): drain a
 * clone under the deadline, so the original's queued body is ready for callers.
 * An upper bound prevents a malformed upstream from buffering forever.
 */
export class TransportTimeoutError extends Error {
  constructor() { super('Response deadline exceeded'); this.name = 'TransportTimeoutError'; }
}
export class ResponseLimitError extends Error {
  constructor() { super('Response exceeded byte limit'); this.name = 'ResponseLimitError'; }
}
export const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

export function apiHeaders(input: HeadersInit | undefined, write: boolean): Headers {
  const headers = new Headers(input);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (write) headers.set('X-Optix-Action', '1');
  return headers;
}

/** Both Retry-After forms: delta seconds and HTTP-date. Invalid is NOT zero. */
export function parseRetryAfter(value: unknown, now = Date.now()): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) && value >= 0 ? value : undefined;
  if (typeof value !== 'string' || !value.trim()) return undefined;
  const text = value.trim();
  if (/^\d+(?:\.\d+)?$/.test(text)) {
    const seconds = Number(text);
    return Number.isFinite(seconds) ? seconds : undefined;
  }
  // Date.parse accepts surprising inputs such as "-1" or "1e3". Only attempt
  // a textual HTTP date, not arbitrary numeric strings from an upstream.
  if (!/[a-z]{3}/i.test(text) || !/GMT$/i.test(text)) return undefined;
  const stamp = Date.parse(text);
  return Number.isFinite(stamp) && Number.isFinite(now) ? Math.max(0, Math.ceil((stamp - now) / 1000)) : undefined;
}

type TransportHold = {
  reader?: ReadableStreamDefaultReader<Uint8Array>;
  original?: Response;
};

async function runTransport(
  timeoutMs: number,
  limit: number,
  external: AbortSignal | null | undefined,
  work: (controller: AbortController, hold: TransportHold) => Promise<Response>,
): Promise<Response> {
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0 || !Number.isFinite(limit) || limit <= 0) {
    throw new RangeError('Invalid transport budget');
  }
  const controller = new AbortController();
  const abortExternal = () => controller.abort(external?.reason);
  const hold: TransportHold = {};
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectAbort: (reason: unknown) => void = () => {};
  const aborted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const onAbort = () => rejectAbort(controller.signal.reason ?? new DOMException('Aborted', 'AbortError'));
  controller.signal.addEventListener('abort', onAbort, { once: true });
  if (external?.aborted) abortExternal();
  else external?.addEventListener('abort', abortExternal, { once: true });
  if (timeoutMs > 0) timer = setTimeout(() => controller.abort(new TransportTimeoutError()), timeoutMs);

  try {
    return await Promise.race([work(controller, hold), aborted]);
  } catch (error) {
    // Cancellation of one tee alone can wait for the other branch. Do not
    // await these cancellations; abort the network and cancel BOTH branches.
    controller.abort(error);
    void hold.reader?.cancel(error).catch(() => {});
    void hold.original?.body?.cancel(error).catch(() => {});
    throw error;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    external?.removeEventListener('abort', abortExternal);
    controller.signal.removeEventListener('abort', onAbort);
    // The reader can still have a pending read on an aborted synthetic stream;
    // releaseLock is intentionally not required for garbage collection here.
  }
}

async function drainResponseBody(
  original: Response,
  controller: AbortController,
  hold: TransportHold,
  limit: number,
): Promise<Response> {
  controller.signal.throwIfAborted();
  if (!original.body) return original;
  // A response clone tees the stream; never leave the original branch
  // buffering an unbounded payload while waiting for the second branch.
  hold.reader = original.clone().body!.getReader();
  let bytes = 0;
  while (true) {
    controller.signal.throwIfAborted();
    const next = await hold.reader.read();
    if (next.done) break;
    bytes += next.value.byteLength;
    if (bytes > limit) throw new ResponseLimitError();
  }
  return original;
}

/** Apply the same body deadline, cancel and size bound to an already-started Response. */
export function bufferExistingResponse(
  original: Response,
  timeoutMs: number,
  limit = MAX_RESPONSE_BYTES,
  signal?: AbortSignal | null,
): Promise<Response> {
  return runTransport(timeoutMs, limit, signal, async (controller, hold) => {
    hold.original = original;
    if (controller.signal.aborted) {
      void original.body?.cancel(controller.signal.reason).catch(() => {});
      controller.signal.throwIfAborted();
    }
    return drainResponseBody(original, controller, hold, limit);
  });
}

export async function fetchBuffered(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
  limit = MAX_RESPONSE_BYTES,
): Promise<Response> {
  return runTransport(timeoutMs, limit, init.signal, async (controller, hold) => {
    controller.signal.throwIfAborted();
    hold.original = await fetch(input, { ...init, signal: controller.signal });
    if (controller.signal.aborted) {
      // Also discard a late response from a fetch adapter that ignored abort.
      void hold.original.body?.cancel(controller.signal.reason).catch(() => {});
      controller.signal.throwIfAborted();
    }
    return drainResponseBody(hold.original, controller, hold, limit);
  });
}
