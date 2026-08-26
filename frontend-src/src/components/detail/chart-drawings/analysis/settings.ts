/** Layer settings persist per principal, never mixed with hand-drawing keys. */

import { LAYERS, OVERLAY_LAYER_BY_KIND, PRESETS, layersStorageKey, type PresetId } from './registry.ts';

export interface LayerSettings {
  preset: PresetId;
  enabled: string[];
  minShapeQuality: number;
  onlyActive: boolean;
  showInvalidated: boolean;
  maxPatterns: number;
  maxLabels: number;
  labelDensity: number;
}

export const DEFAULT_LAYER_SETTINGS: LayerSettings = {
  preset: 'minimal',
  enabled: [...PRESETS.minimal.enabled],
  minShapeQuality: PRESETS.minimal.minShapeQuality,
  onlyActive: PRESETS.minimal.onlyActive,
  showInvalidated: PRESETS.minimal.showInvalidated,
  maxPatterns: PRESETS.minimal.maxPatterns,
  maxLabels: PRESETS.minimal.maxLabels,
  labelDensity: PRESETS.minimal.labelDensity,
};

const VALID_IDS = new Set(LAYERS.map((layer) => layer.id));

export function settingsFromPreset(preset: Exclude<PresetId, 'custom'>): LayerSettings {
  const row = PRESETS[preset];
  return {
    preset,
    enabled: [...row.enabled],
    minShapeQuality: row.minShapeQuality,
    onlyActive: row.onlyActive,
    showInvalidated: row.showInvalidated,
    maxPatterns: row.maxPatterns,
    maxLabels: row.maxLabels,
    labelDensity: row.labelDensity,
  };
}

export function parseLayerSettings(raw: unknown): LayerSettings {
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_LAYER_SETTINGS, enabled: [...DEFAULT_LAYER_SETTINGS.enabled] };
  const row = raw as Record<string, unknown>;
  const preset = typeof row.preset === 'string' && row.preset in PRESETS ? (row.preset as Exclude<PresetId, 'custom'>) : 'custom';
  const enabled = Array.isArray(row.enabled)
    ? row.enabled.filter((id): id is string => typeof id === 'string' && VALID_IDS.has(id))
    : [...DEFAULT_LAYER_SETTINGS.enabled];
  const num = (value: unknown, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return {
    preset: preset === 'custom' || row.preset === 'custom' ? 'custom' : preset,
    enabled,
    minShapeQuality: Math.min(1, Math.max(0, num(row.minShapeQuality, DEFAULT_LAYER_SETTINGS.minShapeQuality))),
    onlyActive: row.onlyActive === true,
    showInvalidated: row.showInvalidated === true,
    maxPatterns: Math.max(0, Math.round(num(row.maxPatterns, DEFAULT_LAYER_SETTINGS.maxPatterns))),
    maxLabels: Math.max(0, Math.round(num(row.maxLabels, DEFAULT_LAYER_SETTINGS.maxLabels))),
    labelDensity: Math.min(1, Math.max(0, num(row.labelDensity, DEFAULT_LAYER_SETTINGS.labelDensity))),
  };
}

export function loadLayerSettings(identity: string, storage?: Storage | null): LayerSettings {
  const store = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
  if (!store) return settingsFromPreset('minimal');
  try {
    const raw = store.getItem(layersStorageKey(identity));
    if (!raw) return settingsFromPreset('minimal');
    return parseLayerSettings(JSON.parse(raw));
  } catch {
    return settingsFromPreset('minimal');
  }
}

export function saveLayerSettings(identity: string, settings: LayerSettings, storage?: Storage | null): void {
  const store = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
  if (!store) return;
  try {
    store.setItem(layersStorageKey(identity), JSON.stringify(settings));
  } catch {
    /* QuotaExceededError / SecurityError：私密模式或额度满时不把异常抛到图层菜单 */
  }
}

export function toggleLayer(settings: LayerSettings, layerId: string): LayerSettings {
  const enabled = settings.enabled.includes(layerId)
    ? settings.enabled.filter((id) => id !== layerId)
    : [...settings.enabled, layerId];
  return { ...settings, preset: 'custom', enabled };
}

export function layerIdForOverlay(kind: string, overlayId?: string): string | null {
  if (overlayId && (overlayId === 'ma20' || overlayId === 'ma50' || overlayId === 'ma200')) return overlayId;
  return OVERLAY_LAYER_BY_KIND[kind] ?? null;
}
