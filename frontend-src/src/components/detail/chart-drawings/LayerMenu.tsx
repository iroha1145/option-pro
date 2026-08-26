import Drawer from '@/components/Drawer';
import { cn } from '@/lib/utils';
import { t } from '../../../i18n/core.ts';
import { GROUPS, LAYERS, PRESETS, type PresetId } from './analysis/registry.ts';
import { settingsFromPreset, toggleLayer, type LayerSettings } from './analysis/settings.ts';

const PRESET_ORDER: Exclude<PresetId, 'custom'>[] = [
  'minimal',
  'structure',
  'breakout',
  'momentum',
  'volume',
  'all',
];

export default function LayerMenu({
  open,
  onClose,
  settings,
  onChange,
  strengthContext,
}: {
  open: boolean;
  onClose: () => void;
  settings: LayerSettings;
  onChange: (next: LayerSettings) => void;
  strengthContext?: Record<string, unknown> | null;
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
                return (
                  <li key={layer.id}>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={on}
                        aria-label={layer.label}
                        onChange={() => onChange(toggleLayer(settings, layer.id))}
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
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              aria-label={t('最低几何质量')}
              value={settings.minShapeQuality}
              onChange={(event) =>
                onChange({ ...settings, preset: 'custom', minShapeQuality: Number(event.target.value) })}
              className="w-20 rounded-xs border border-line px-1 py-0.5 font-mono"
            />
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
              onChange={(event) =>
                onChange({ ...settings, preset: 'custom', maxPatterns: Number(event.target.value) })}
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
              onChange={(event) =>
                onChange({ ...settings, preset: 'custom', labelDensity: Number(event.target.value) })}
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
