export type Tone = 'up' | 'down' | 'flat';

/** 平盘是第三种事实：0 不算涨，别让它拿到向上的箭头与绿色（同 ChangeBadge 口径）。 */
export function toneOf(value: number | null | undefined): Tone {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) return 'flat';
  return value > 0 ? 'up' : 'down';
}

