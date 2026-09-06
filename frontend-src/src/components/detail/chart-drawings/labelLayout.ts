/** Pixel-space label packing. Does not modify any price/time coordinates. */
export interface LabelRect { x: number; y: number; width: number; height: number }
export interface LabelRequest {
  id: string; anchorX: number; anchorY: number; width: number; height: number; priority: number;
}
export interface LabelPlacement extends LabelRect { id: string; anchorX: number; anchorY: number }
export function rectanglesOverlap(a: LabelRect, b: LabelRect, gap = 3): boolean {
  return a.x < b.x + b.width + gap && a.x + a.width + gap > b.x
    && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
}
/** Highest-priority labels reserve space first. If no slot exists, omit the label
 * rather than covering another label or moving its underlying line. Stable ties
 * mean renderItem invocation order never determines the result.
 */
export function packEndLabels(requests: readonly LabelRequest[], bounds: LabelRect, obstacles: readonly LabelRect[] = []): LabelPlacement[] {
  if (![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite) || bounds.width <= 0 || bounds.height <= 0) return [];
  const occupied = obstacles.filter(r => [r.x, r.y, r.width, r.height].every(Number.isFinite) && r.width > 0 && r.height > 0).map(r => ({ ...r }));
  const result: LabelPlacement[] = [];
  const sorted = [...requests].filter(r => [r.anchorX, r.anchorY, r.width, r.height, r.priority].every(Number.isFinite)
    && r.width > 0 && r.height > 0 && r.width <= bounds.width && r.height <= bounds.height)
    .sort((a, b) => b.priority - a.priority || a.anchorY - b.anchorY || a.id.localeCompare(b.id, 'en'));
  for (const r of sorted) {
    const x = Math.max(bounds.x, Math.min(bounds.x + bounds.width - r.width, r.anchorX - r.width - 5));
    const desiredY = r.anchorY - r.height - 5;
    const candidates = [desiredY, r.anchorY + 5, bounds.y, bounds.y + bounds.height - r.height];
    // All free interval boundaries are candidates; avoid a fixed four-lane limit.
    for (const obstacle of occupied) {
      candidates.push(obstacle.y - r.height - 4, obstacle.y + obstacle.height + 4);
    }
    const ys = [...new Set(candidates.map(y => Math.max(bounds.y, Math.min(bounds.y + bounds.height - r.height, y))))]
      .sort((a, b) => Math.abs(a - desiredY) - Math.abs(b - desiredY) || a - b);
    const maxMove = Math.min(110, bounds.height * 0.35);
    const y = ys.find(y => Math.abs(y - desiredY) <= maxMove && !occupied.some(o => rectanglesOverlap({ x, y, width: r.width, height: r.height }, o)));
    if (y === undefined) continue;
    const placement = { ...r, x, y };
    result.push(placement); occupied.push(placement);
  }
  return result;
}
let context: CanvasRenderingContext2D | null | undefined;
const widths = new Map<string, number>();
/** Browser font measurement, with a deterministic conservative SSR/test fallback. */
export function measureLabel(text: string, size = 11, lineHeight = 14): { width: number; height: number } {
  const key = `${size}:${text}`;
  let width = widths.get(key);
  if (width === undefined) {
    if (context === undefined) {
      try { context = typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d'); }
      catch { context = null; }
    }
    if (context) { context.font = `${size}px sans-serif`; width = context.measureText(text).width; }
    else width = [...text].reduce((n, c) => n + (c.charCodeAt(0) > 255 ? size : size * 0.64), 0);
    if (widths.size > 512) widths.clear();
    widths.set(key, width);
  }
  return { width: Math.ceil(width) + 12, height: Math.max(size + 4, lineHeight) + 4 };
}
