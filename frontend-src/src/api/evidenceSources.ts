import type { EvidenceSource } from './types.ts';

export const MAX_EVIDENCE_SOURCES = 10;

/** Public links only: bounded, deduplicated, and free of embedded credentials. */
export function normalizeEvidenceSources(raw: unknown): EvidenceSource[] {
  if (!Array.isArray(raw)) return [];
  const sources: EvidenceSource[] = [];
  const seen = new Set<string>();
  for (const item of raw.slice(0, MAX_EVIDENCE_SOURCES * 5)) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    if ((row.type !== 'web_search' && row.type !== 'web_fetch')
      || typeof row.url !== 'string' || typeof row.title !== 'string') continue;
    const address = row.url.trim();
    if (!address || address.length > 2048 || [...address].some((character) => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127)) continue;
    let url: URL;
    try { url = new URL(address); } catch { continue; }
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) continue;
    const normalized = url.href;
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    sources.push({
      title: [...row.title.trim().slice(0, 240)].map((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127 ? ' ' : character).join('').trim() || url.hostname,
      url: normalized,
      type: row.type,
    });
    if (sources.length === MAX_EVIDENCE_SOURCES) break;
  }
  return sources;
}
