/** Data-only layer catalog mirrored from backend layer_registry.py. */
import { t } from '../../../../i18n/core.ts';

export const LAYER_STORAGE_PREFIX = 'option-pro:chart-layers:v1';

export type LayerGroup = 'price' | 'event' | 'pane' | 'strength';
export type PresetId = 'minimal' | 'structure' | 'breakout' | 'momentum' | 'volume' | 'all' | 'custom';

export interface LayerDef {
  id: string;
  group: LayerGroup;
  kind: string;
  label: string;
}

export const LAYERS: LayerDef[] = [
  { id: 'ma20', group: 'price', kind: 'ma', label: t('MA20') },
  { id: 'ma50', group: 'price', kind: 'ma', label: t('MA50') },
  { id: 'ma200', group: 'price', kind: 'ma', label: t('MA200') },
  { id: 'swings', group: 'price', kind: 'swing', label: t('摆动点') },
  { id: 'support_resistance', group: 'price', kind: 'level', label: t('支撑阻力') },
  { id: 'bases', group: 'price', kind: 'box', label: t('整理区') },
  { id: 'pivots', group: 'price', kind: 'pivot', label: t('pivot/invalidation') },
  { id: 'auto_patterns', group: 'price', kind: 'pattern', label: t('自动趋势线/通道/三角形/楔形') },
  { id: 'candles', group: 'event', kind: 'candle', label: t('K线形态') },
  { id: 'traps', group: 'event', kind: 'trap', label: t('Spring/Upthrust') },
  { id: 'breakouts', group: 'event', kind: 'breakout', label: t('突破触发/确认/回踩/失败') },
  { id: 'rsi', group: 'pane', kind: 'rsi', label: t('RSI') },
  { id: 'macd', group: 'pane', kind: 'macd', label: t('MACD') },
  { id: 'obv', group: 'pane', kind: 'obv', label: t('OBV') },
  { id: 'clv', group: 'pane', kind: 'clv', label: t('CLV') },
  { id: 'range_persistence', group: 'pane', kind: 'range', label: t('Range Persistence') },
  { id: 'spy_rs', group: 'pane', kind: 'rs', label: t('SPY Relative Strength') },
  { id: 'strength_short', group: 'strength', kind: 'score', label: t('short') },
  { id: 'strength_mid', group: 'strength', kind: 'score', label: t('mid') },
  { id: 'strength_long', group: 'strength', kind: 'score', label: t('long') },
  { id: 'strength_trend', group: 'strength', kind: 'score', label: t('trend') },
  { id: 'strength_breakout', group: 'strength', kind: 'score', label: t('breakout') },
  { id: 'strength_price_action', group: 'strength', kind: 'score', label: t('price_action') },
  { id: 'strength_percentiles', group: 'strength', kind: 'score', label: t('global/sector percentile') },
  { id: 'strength_contributions', group: 'strength', kind: 'score', label: t('factor contributions') },
];

export const GROUPS: { id: LayerGroup; label: string }[] = [
  { id: 'price', label: t('价格图层') },
  { id: 'event', label: t('事件') },
  { id: 'pane', label: t('副图') },
  { id: 'strength', label: t('选股上下文') },
];

export interface PresetDef {
  label: string;
  enabled: string[];
  maxPatterns: number;
  maxLabels: number;
  minShapeQuality: number;
  onlyActive: boolean;
  showInvalidated: boolean;
  labelDensity: number;
}

export const PRESETS: Record<Exclude<PresetId, 'custom'>, PresetDef> = {
  minimal: {
    label: t('极简'),
    enabled: ['ma20', 'auto_patterns'],
    maxPatterns: 3,
    maxLabels: 6,
    minShapeQuality: 0.7,
    onlyActive: true,
    showInvalidated: false,
    labelDensity: 0.4,
  },
  structure: {
    label: t('结构分析'),
    enabled: ['swings', 'support_resistance', 'bases', 'pivots', 'auto_patterns', 'candles', 'traps'],
    maxPatterns: 8,
    maxLabels: 10,
    minShapeQuality: 0.55,
    onlyActive: false,
    showInvalidated: false,
    labelDensity: 0.7,
  },
  breakout: {
    label: t('突破交易'),
    enabled: ['bases', 'pivots', 'breakouts', 'auto_patterns', 'obv', 'clv'],
    maxPatterns: 6,
    maxLabels: 8,
    minShapeQuality: 0.55,
    onlyActive: true,
    showInvalidated: true,
    labelDensity: 0.6,
  },
  momentum: {
    label: t('动量'),
    enabled: ['ma20', 'ma50', 'ma200', 'rsi', 'macd', 'spy_rs', 'strength_trend'],
    maxPatterns: 0,
    maxLabels: 4,
    minShapeQuality: 0.7,
    onlyActive: true,
    showInvalidated: false,
    labelDensity: 0.4,
  },
  volume: {
    label: t('量价'),
    enabled: ['obv', 'clv', 'range_persistence', 'traps', 'breakouts'],
    maxPatterns: 4,
    maxLabels: 6,
    minShapeQuality: 0.55,
    onlyActive: false,
    showInvalidated: false,
    labelDensity: 0.5,
  },
  all: {
    label: t('全部'),
    enabled: LAYERS.map((layer) => layer.id),
    maxPatterns: 12,
    maxLabels: 16,
    minShapeQuality: 0.5,
    onlyActive: false,
    showInvalidated: true,
    labelDensity: 1,
  },
};

export const OVERLAY_LAYER_BY_KIND: Record<string, string> = {
  ma: 'ma20',
  swing: 'swings',
  level: 'support_resistance',
  box: 'bases',
  pivot: 'pivots',
  support_trend: 'auto_patterns',
  resistance_trend: 'auto_patterns',
  channel: 'auto_patterns',
  triangle: 'auto_patterns',
  wedge: 'auto_patterns',
  candle: 'candles',
  trap: 'traps',
  breakout: 'breakouts',
  volume_setup: 'breakouts',
};

export function layersStorageKey(identity: string): string {
  return `${LAYER_STORAGE_PREFIX}:${identity}`;
}
