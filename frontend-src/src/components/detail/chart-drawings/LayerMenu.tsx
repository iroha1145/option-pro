import Drawer from '@/components/Drawer';
import { cn } from '@/lib/utils';
import InfoHint from '@/components/shared/InfoHint';
import { LAYER_HINTS } from './hints';
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
  const patch = (next: Partial<LayerSettings>) => onChange({ ...settings, preset: 'custom', ...next });
  return (
    <Drawer open={open} onClose={onClose} label={t('算法与图层')} title={t('算法与图层')} width={420}>
      <div className="flex flex-col gap-5 p-1 text-micro">
        {/* 预设：标题行右侧留一个「恢复默认」，与检查器卡片的「标题 + 动作」同构 */}
        <section>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="font-medium text-ink-600">{t('预设')}</h3>
            <button
              type="button"
              onClick={() => onChange(settingsFromPreset('minimal'))}
              className="rounded-xs px-1.5 py-0.5 text-ink-400 underline-offset-2 transition-colors duration-fast hover:text-brand-600 hover:underline"
            >
              {t('恢复默认')}
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {PRESET_ORDER.map((id) => (
              <button
                key={id}
                type="button"
                aria-pressed={settings.preset === id}
                aria-label={PRESETS[id].label}
                onClick={() => onChange(settingsFromPreset(id))}
                className={cn(
                  'rounded-full border px-2.5 py-1 transition-colors duration-fast',
                  settings.preset === id
                    ? 'border-brand-400 bg-brand-50 text-brand-700'
                    : 'border-line text-ink-500 hover:border-line-strong hover:text-ink-700',
                )}
              >
                {PRESETS[id].label}
              </button>
            ))}
          </div>
        </section>

        {GROUPS.map((group) => (
          <section key={group.id}>
            <h3 className="mb-1.5 border-b border-line pb-1 font-medium text-ink-600">{group.label}</h3>
            <ul className="flex flex-col">
              {LAYERS.filter((layer) => layer.group === group.id).map((layer) => {
                const on = settings.enabled.includes(layer.id);
                const gate = layerInputEnabled(layer, mode);
                const reason = gate.reason === 'area_no_panes_or_ma'
                  ? t('面积图不支持副图与均线叠加')
                  : null;
                const hint = LAYER_HINTS[layer.id];
                return (
                  <li key={layer.id}>
                    {/* 检查器行式：左边标签（带悬停解释），右边控件对齐 */}
                    <div className={cn('flex min-h-8 items-center gap-2 py-0.5', !gate.enabled && 'text-ink-400')}>
                      <label className="flex min-w-0 flex-1 items-center gap-1.5">
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
                          className="size-3.5 shrink-0 accent-brand-600"
                        />
                        <span className="truncate">{layer.label}</span>
                      </label>
                      {hint && <InfoHint hint={hint} side="bottom" align="end" size={12} className="shrink-0" />}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}

        <section>
          <h3 className="mb-1.5 border-b border-line pb-1 font-medium text-ink-600">{t('高级')}</h3>

          {/* 0–100 的旋钮给滑杆 + 实时读数：数字框要先选中再改、还看不出量程，
              而这两个值的手感本来就是「拖着找一个合适的松紧」。 */}
          <div className="flex items-center gap-2 py-1">
            <span className="flex-1">{t('最低几何质量')}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              aria-label={t('最低几何质量')}
              value={Math.round(settings.minShapeQuality * 100)}
              onChange={(event) => patch({
                minShapeQuality: clampInput(event.target.value, 0, 100, Math.round(settings.minShapeQuality * 100)) / 100,
              })}
              className="w-32 accent-brand-600"
            />
            <span className="w-10 text-right font-mono text-ink-600 tnum">
              {Math.round(settings.minShapeQuality * 100)}%
            </span>
          </div>

          <div className="flex items-center gap-2 py-1">
            <span className="flex-1">{t('标签密度')}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={10}
              aria-label={t('标签密度')}
              value={Math.round(settings.labelDensity * 100)}
              onChange={(event) => patch({
                labelDensity: clampInput(event.target.value, 0, 100, Math.round(settings.labelDensity * 100)) / 100,
              })}
              className="w-32 accent-brand-600"
            />
            <span className="w-10 text-right font-mono text-ink-600 tnum">
              {Math.round(settings.labelDensity * 100)}%
            </span>
          </div>

          <div className="flex items-center gap-2 py-1">
            <span className="flex-1">{t('最大形态数')}</span>
            <input
              type="number"
              min={0}
              max={24}
              aria-label={t('最大形态数')}
              value={settings.maxPatterns}
              onChange={(event) => patch({
                maxPatterns: Math.round(clampInput(event.target.value, 0, 24, settings.maxPatterns)),
              })}
              className="w-16 rounded-xs border border-line px-1.5 py-0.5 text-right font-mono tnum"
            />
          </div>

          <label className="flex min-h-8 items-center gap-1.5 py-0.5">
            <input
              type="checkbox"
              checked={settings.onlyActive}
              aria-label={t('仅当前有效')}
              onChange={(event) => patch({ onlyActive: event.target.checked })}
              className="size-3.5 shrink-0 accent-brand-600"
            />
            <span className="flex-1">{t('仅当前有效')}</span>
          </label>
          <label className="flex min-h-8 items-center gap-1.5 py-0.5">
            <input
              type="checkbox"
              checked={settings.showInvalidated}
              aria-label={t('显示已失效')}
              onChange={(event) => patch({ showInvalidated: event.target.checked })}
              className="size-3.5 shrink-0 accent-brand-600"
            />
            <span className="flex-1">{t('显示已失效')}</span>
          </label>
        </section>

        {families && (
          <section>
            <h3 className="mb-1.5 border-b border-line pb-1 font-medium text-ink-600">{t('选股上下文')}</h3>
            <p className="mb-2 text-ink-400">{t('几何质量不是胜率')}</p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
              {['short', 'mid', 'long', 'trend', 'breakout', 'price_action'].map((name) => (
                <div key={name} className="flex items-center justify-between gap-2">
                  <dt className="truncate text-ink-500">{name}</dt>
                  <dd className="font-mono text-ink-700 tnum">
                    {families[name]?.score == null ? '—' : families[name]?.score}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}
      </div>
    </Drawer>
  );
}
