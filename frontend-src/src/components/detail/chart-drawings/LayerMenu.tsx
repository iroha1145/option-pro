import Drawer from '@/components/Drawer';
import { cn } from '@/lib/utils';
import { t } from '../../../i18n/core.ts';
import { GROUPS, LAYERS, PRESETS, type PresetId } from './analysis/registry.ts';
import { settingsFromPreset, toggleLayer, type LayerSettings } from './analysis/settings.ts';
import { layerInputEnabled, type ChartRenderMode } from './scopeLoad.ts';

const PRESET_ORDER: Exclude<PresetId, 'custom'>[] = [
  'minimal',
  'structure',
  'breakout',
  'momentum',
  'volume',
  'all',
];

/**
 * 数字输入的即时钳制：清空（Number('') === 0）和越界都不该被持久化。
 * 「最低几何质量」按 0–100 收，和标签条上的「置信度 87」同一把尺——
 * 以前控件是 0–1 而 UI 显 0–100，看到 87 填 60 就把全部形态滤没了。
 */
function clampInput(raw: string, min: number, max: number, fallback: number): number {
  if (raw.trim() === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, value));
}

export default function LayerMenu({
  open,
  onClose,
  settings,
  onChange,
  strengthContext,
  mode = 'candle',
}: {
  open: boolean;
  onClose: () => void;
  settings: LayerSettings;
  onChange: (next: LayerSettings) => void;
  strengthContext?: Record<string, unknown> | null;
  mode?: ChartRenderMode;
}) {
  const families = (strengthContext?.families ?? null) as Record<string, { score?: number | null }> | null;
  return (
    <Drawer open={open} onClose={onClose} label={t('算法与图层')} title={t('算法与图层')} width={420}>
      <div className="flex flex-col gap-4 p-1 text-micro">
        <section>
          <h3 className="mb-2 font-medium text-ink-600">{t('预设')}</h3>
          <div className="flex flex-wrap gap-1.5">
            {PRESET_ORDER.map((id) => (
              <button
                key={id}
                type="button"
                aria-pressed={settings.preset === id}
                aria-label={PRESETS[id].label}
                onClick={() => onChange(settingsFromPreset(id))}
                className={cn(
                  'rounded-xs border px-2 py-1',
                  settings.preset === id
                    ? 'border-brand-400 bg-brand-50 text-brand-700'
                    : 'border-line text-ink-500 hover:text-ink-700',
                )}
              >
                {PRESETS[id].label}
              </button>
            ))}
          </div>
        </section>
        {GROUPS.map((group) => (
          <section key={group.id}>
            <h3 className="mb-2 font-medium text-ink-600">{group.label}</h3>
            <ul className="flex flex-col gap-1">
              {LAYERS.filter((layer) => layer.group === group.id).map((layer) => {
                const on = settings.enabled.includes(layer.id);
                const gate = layerInputEnabled(layer, mode);
                const reason = gate.reason ? t(gate.reason) : null;
                return (
                  <li key={layer.id}>
                    <label className={cn('flex items-center gap-2', !gate.enabled && 'text-ink-400')}>
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={!gate.enabled}
                        aria-label={layer.label}
                        title={reason ?? undefined}
                        onChange={() => {
                          if (!gate.enabled) return;
                          onChange(toggleLayer(settings, layer.id));
                        }}
                      />
                      <span>{layer.label}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
        <section>
          <h3 className="mb-2 font-medium text-ink-600">{t('高级')}</h3>
          <label className="mb-1 flex items-center justify-between gap-2">
            <span>{t('最低几何质量')}</span>
            <span className="flex items-center gap-1">
              <input
                type="number"
                min={0}
                max={100}
                step={5}
                aria-label={t('最低几何质量')}
                value={Math.round(settings.minShapeQuality * 100)}
                onChange={(event) => onChange({
                  ...settings,
                  preset: 'custom',
                  minShapeQuality: clampInput(event.target.value, 0, 100, Math.round(settings.minShapeQuality * 100)) / 100,
                })}
                className="w-20 rounded-xs border border-line px-1 py-0.5 font-mono"
              />
              <span className="text-ink-400">%</span>
            </span>
          </label>
          <label className="mb-1 flex items-center gap-2">
            <input
              type="checkbox"
              checked={settings.onlyActive}
              aria-label={t('仅当前有效')}
              onChange={(event) => onChange({ ...settings, preset: 'custom', onlyActive: event.target.checked })}
            />
            {t('仅当前有效')}
          </label>
          <label className="mb-1 flex items-center gap-2">
            <input
              type="checkbox"
              checked={settings.showInvalidated}
              aria-label={t('显示已失效')}
              onChange={(event) => onChange({ ...settings, preset: 'custom', showInvalidated: event.target.checked })}
            />
            {t('显示已失效')}
          </label>
          <label className="mb-1 flex items-center justify-between gap-2">
            <span>{t('最大形态数')}</span>
            <input
              type="number"
              min={0}
              max={24}
              aria-label={t('最大形态数')}
              value={settings.maxPatterns}
              onChange={(event) => onChange({
                ...settings,
                preset: 'custom',
                maxPatterns: Math.round(clampInput(event.target.value, 0, 24, settings.maxPatterns)),
              })}
              className="w-20 rounded-xs border border-line px-1 py-0.5 font-mono"
            />
          </label>
          <label className="flex items-center justify-between gap-2">
            <span>{t('标签密度')}</span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.1}
              aria-label={t('标签密度')}
              value={settings.labelDensity}
              onChange={(event) => onChange({
                ...settings,
                preset: 'custom',
                labelDensity: clampInput(event.target.value, 0, 1, settings.labelDensity),
              })}
              className="w-20 rounded-xs border border-line px-1 py-0.5 font-mono"
            />
          </label>
        </section>
        {families && (
          <section>
            <h3 className="mb-2 font-medium text-ink-600">{t('选股上下文')}</h3>
            <p className="mb-2 text-ink-400">{t('几何质量不是胜率')}</p>
            <ul className="font-mono text-ink-600">
              {['short', 'mid', 'long', 'trend', 'breakout', 'price_action'].map((name) => (
                <li key={name}>
                  {name}
                  {': '}
                  {families[name]?.score == null ? '—' : families[name]?.score}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Drawer>
  );
}
