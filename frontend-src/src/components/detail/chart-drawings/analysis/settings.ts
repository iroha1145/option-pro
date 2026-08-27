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

/** 存储格式版本。v2 = 质量门槛 0.45 时代；v2 起旧默认迁移不再执行（一次性）。 */
const LAYER_SETTINGS_VERSION = 2;

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


/**
 * 旧质量门槛的存量迁移。0.50 / 0.55 / 0.70 是**旧预设常量**，不是用户深思
 * 熟虑填的数——而检测器闸门已降到 0.45（旧常量定在真实行情够不到的位置，
 * 上线以来一条形态都没画出来过）。设置按账号持久化，不迁移的话老用户把
 * 「自动趋势线」勾着也永远看不到线，还以为功能是坏的。真正手填的其他值
 * （如 0.60）原样保留。
 */
const STALE_QUALITY_DEFAULTS = new Set([0.5, 0.55, 0.7]);

function migrateStaleQualityGate(value: number): number {
  for (const stale of STALE_QUALITY_DEFAULTS) {
    if (Math.abs(value - stale) < 1e-9) return DEFAULT_LAYER_SETTINGS.minShapeQuality;
  }
  return value;
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
  /* 版本 < 2 的存量才迁移：否则用户日后**有意**把滑杆放回 70% 会在下次
     加载时被再次改掉——迁移必须一次性，靠版本号封口。 */
  const storedVersion = num(row.schemaVersion, 1);
  const qualityRaw = Math.min(1, Math.max(0, num(row.minShapeQuality, DEFAULT_LAYER_SETTINGS.minShapeQuality)));
  return {
    preset: preset === 'custom' || row.preset === 'custom' ? 'custom' : preset,
    enabled,
    minShapeQuality: storedVersion >= LAYER_SETTINGS_VERSION ? qualityRaw : migrateStaleQualityGate(qualityRaw),
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
    store.setItem(layersStorageKey(identity), JSON.stringify({ ...settings, schemaVersion: LAYER_SETTINGS_VERSION }));
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
