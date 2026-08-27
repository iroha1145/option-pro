/**
 * 算法与图层：独立居中小窗（transitions.dev modal，同 ConfirmDialog 骨架）。
 * 之前是右侧 Drawer——用户明确要"单独小窗口弹窗，不是右侧的"；而且白卡片
 * 贴在白抽屉底上发丝边几乎不可见。现在窗体正文用 paper-2 灰底衬白卡，
 * 开关走 FilterBar 的拨杆语言（role="switch"），才有 Fine-tune Card 的辨识度。
 */
import { useEffect, useRef, type ReactNode } from 'react';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import InfoHint from '@/components/shared/InfoHint';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import { LAYER_HINTS } from './hints';
import type { ScoreHint } from '@/lib/scoreHints';
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

/** FilterBar 同款拨杆；名字给 aria-label，禁用态由行头 title 给原因。 */
function Switch({
  checked,
  disabled,
  label,
  onToggle,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={(event) => {
        // 行本身也可点：拨杆自吃事件，避免冒泡到行再翻一次等于没动。
        event.stopPropagation();
        if (!disabled) onToggle();
      }}
      className={cn(
        'relative h-[18px] w-8 shrink-0 rounded-pill shadow-track transition-colors duration-ui',
        checked ? 'bg-brand-600' : 'bg-ink-300',
        disabled && 'cursor-not-allowed opacity-40',
      )}
    >
      <span
        className={cn(
          'absolute top-[2px] size-[14px] rounded-full bg-card shadow-knob transition-[left] duration-ui ease-paper',
          checked ? 'left-[16px]' : 'left-[2px]',
        )}
      />
    </button>
  );
}

function LayerRow({
  label,
  checked,
  disabled,
  reason,
  hint,
  onToggle,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  reason?: string | null;
  hint?: ScoreHint;
  onToggle: () => void;
}) {
  return (
    <div
      title={reason ?? undefined}
      onClick={() => {
        if (!disabled) onToggle();
      }}
      className={cn(
        'flex min-h-8 items-center justify-between gap-2 rounded-md px-1.5 py-0.5 transition-colors duration-fast',
        disabled ? 'cursor-not-allowed text-ink-400' : 'cursor-pointer hover:bg-paper-2/70',
      )}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="truncate">{label}</span>
        {hint && <InfoHint hint={hint} side="bottom" align="start" size={12} className="shrink-0" />}
      </span>
      <Switch checked={checked} disabled={disabled} label={label} onToggle={onToggle} />
    </div>
  );
}

/** 灰底正文里的一张白纸卡：Fine-tune Card 的分组单元。 */
function Card({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn('rounded-lg border border-line bg-card p-2 shadow-card', className)}>
      <h3 className="mb-1 px-1.5 pt-0.5 font-medium text-ink-600">{title}</h3>
      {children}
    </section>
  );
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

  const panelRef = useRef<HTMLDivElement>(null);
  const closeMs = readRootDurationMs('--modal-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);
  useFocusTrap(panelRef, open);

  useEffect(() => {
    if (!mounted) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [mounted, onClose]);

  if (!mounted) return null;

  const [primaryGroup, ...secondaryGroups] = GROUPS;
  const groupCard = (group: (typeof GROUPS)[number]) => (
    <Card key={group.id} title={group.label}>
      <ul className="flex flex-col">
        {LAYERS.filter((layer) => layer.group === group.id).map((layer) => {
          const gate = layerInputEnabled(layer, mode);
          return (
            <li key={layer.id}>
              <LayerRow
                label={layer.label}
                checked={settings.enabled.includes(layer.id)}
                disabled={!gate.enabled}
                reason={gate.reason === 'area_no_panes_or_ma' ? t('面积图不支持副图与均线叠加') : null}
                hint={LAYER_HINTS[layer.id]}
                onToggle={() => onChange(toggleLayer(settings, layer.id))}
              />
            </li>
          );
        })}
      </ul>
    </Card>
  );

  return (
    <>
      <div
        className={cn('t-backdrop fixed inset-0 z-[85] bg-[rgba(13,22,38,.34)] backdrop-blur-[2px]', phase === 'open' && 'is-open')}
        onClick={onClose}
        aria-hidden="true"
      />
      {/* 居中交给外壳 translate，t-modal 的 scale 留在内层——同 ConfirmDialog 的注释。 */}
      <div className="pointer-events-none fixed left-1/2 top-1/2 z-[86] w-[680px] max-w-[calc(100vw-24px)] -translate-x-1/2 -translate-y-1/2">
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={t('算法与图层')}
          className={cn(
            't-modal flex max-h-[86vh] flex-col overflow-hidden rounded-xl border border-line bg-paper-2 shadow-sh-3',
            overlayClassName(phase),
          )}
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line bg-card px-4 py-3">
            <h2 className="min-w-0 truncate text-body font-medium text-ink-900">{t('算法与图层')}</h2>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => onChange(settingsFromPreset('minimal'))}
                className="rounded-xs px-1.5 py-0.5 text-micro text-ink-400 underline-offset-2 transition-colors duration-fast hover:text-brand-600 hover:underline"
              >
                {t('恢复默认')}
              </button>
              <button
                onClick={onClose}
                className="rounded-sm p-1.5 text-ink-400 transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-600 active:scale-95"
                aria-label={t('关闭抽屉')}
              >
                <Icon name="x" size={16} />
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 text-micro">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-0.5 font-medium text-ink-600">{t('预设')}</span>
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
                      ? 'border-brand-600 bg-brand-600 font-medium text-white shadow-btn'
                      : 'border-line bg-card text-ink-500 hover:border-line-strong hover:text-ink-700',
                  )}
                >
                  {PRESETS[id].label}
                </button>
              ))}
            </div>

            {/* 双列瀑布：价格图层 8 行独占左列，事件+副图 9 行叠右列，高矮相当；
                grid 自动布局会按行对齐留出大片空白，所以用两根 flex 列。 */}
            <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-start">
              <div className="flex min-w-0 flex-1 flex-col gap-3">{groupCard(primaryGroup)}</div>
              <div className="flex min-w-0 flex-1 flex-col gap-3">{secondaryGroups.map(groupCard)}</div>
            </div>

            <Card title={t('高级')} className="mt-3">
              {/* 0–100 的旋钮给滑杆 + 实时读数：数字框要先选中再改、还看不出量程，
                  而这两个值的手感本来就是「拖着找一个合适的松紧」。 */}
              <div className="flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors duration-fast hover:bg-paper-2/70">
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
                <span className="w-12 rounded-sm bg-paper-2 px-1 py-0.5 text-center font-mono text-ink-700 tnum">
                  {Math.round(settings.minShapeQuality * 100)}%
                </span>
              </div>

              <div className="flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors duration-fast hover:bg-paper-2/70">
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
                <span className="w-12 rounded-sm bg-paper-2 px-1 py-0.5 text-center font-mono text-ink-700 tnum">
                  {Math.round(settings.labelDensity * 100)}%
                </span>
              </div>

              <div className="flex min-h-8 items-center gap-2 rounded-md px-1.5 py-1 transition-colors duration-fast hover:bg-paper-2/70">
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

              <LayerRow
                label={t('仅当前有效')}
                checked={settings.onlyActive}
                onToggle={() => patch({ onlyActive: !settings.onlyActive })}
              />
              <LayerRow
                label={t('显示已失效')}
                checked={settings.showInvalidated}
                onToggle={() => patch({ showInvalidated: !settings.showInvalidated })}
              />
            </Card>

            {families && (
              <Card title={t('选股上下文')} className="mt-3">
                <p className="mb-2 px-1.5 text-ink-400">{t('几何质量不是胜率')}</p>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 px-1.5">
                  {['short', 'mid', 'long', 'trend', 'breakout', 'price_action'].map((name) => (
                    <div key={name} className="flex items-center justify-between gap-2">
                      <dt className="truncate text-ink-500">{name}</dt>
                      <dd className="font-mono text-ink-700 tnum">
                        {families[name]?.score == null ? '—' : families[name]?.score}
                      </dd>
                    </div>
                  ))}
                </dl>
              </Card>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
