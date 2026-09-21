import { marketGet } from '../marketRead.ts';
import type { DiagnosticProfile, DiagnosticTimeframe, SecurityDiagnostic } from '../../lib/eodDiagnostics.ts';

/** Read exactly one symbol from the latest published diagnostic generation. */
export function getSecurityDiagnostics(
  ticker: string,
  profile: DiagnosticProfile,
  timeframe: DiagnosticTimeframe,
  publicationKey?: string | number | null,
): Promise<SecurityDiagnostic> {
  const symbol = ticker.trim();
  const query = new URLSearchParams({ profile, timeframe });
  // A new published batch must not join a GET that began before publication.
  // FastAPI ignores this client-only query parameter.
  if (publicationKey !== undefined && publicationKey !== null) query.set('publication', String(publicationKey));
  return marketGet<SecurityDiagnostic>(`/strength/diagnostics/${encodeURIComponent(symbol)}?${query}`, {
    // A manual retry must ask the server again; an earlier publication cannot
    // be reused from the client cache, including on a 503 response.
    force: true,
    ttlMs: 0,
    staleMs: 0,
  });
}
