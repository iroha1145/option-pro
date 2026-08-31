/**
 * 算法与图层：独立居中小窗（transitions.dev modal，同 ConfirmDialog 骨架）。
 * 之前是右侧 Drawer——用户明确要"单独小窗口弹窗，不是右侧的"；而且白卡片
 * 贴在白抽屉底上发丝边几乎不可见。窗体正文用 paper-2 灰底衬白卡。
 *
 * 对齐制度（用户抓包：胶囊里 CJK 文本靠 padding 凑行高会浮沉、激活态加粗
 * 还会把 chip 撑宽）：所有按钮/胶囊一律 固定高度 + inline-flex 居中 +
 * leading-none，激活态只换色不换字重；行统一 h-8，控件右缘对齐。
 * 动效走 transitions.dev 目录：开关 27-toggle（双段回弹），读数 02-number-pop-in。
 */
import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
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

const FOCUS_RING = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30';

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

/**
 * 读数徽章（02-number-pop-in）：值变了才 pop，首帧不动——弹窗开场已有
 * t-modal 缩放，六个徽章再齐跳一次就成了烟花。换值在渲染期调 state
 * （React 认可的 adjust-on-render）：变更当帧就直接以 is-animating 重挂载；
 * 若放 useEffect 里 bump，会先画一帧静止的新值、下一帧再从头 pop，闪一下。
 */
function PopValue({ text }: { text: string }) {
  const [prev, setPrev] = useState(text);
  const [runId, setRunId] = useState(0);
  if (prev !== text) {
    setPrev(text);
    setRunId((n) => n + 1);
  }
  const chars = [...text];
  const n = chars.length;
  return (
    <span
      key={runId}
      className={cn('t-digit-group', runId > 0 && 'is-animating')}
      style={{ '--digit-dur': '250ms', '--digit-distance': '5px' } as CSSProperties}
    >
      {chars.map((ch, i) => (
        <span
          key={i}
          className="t-digit"
          data-stagger={i === n - 2 ? '1' : i === n - 1 ? '2' : undefined}
        >
          {ch}
        </span>
      ))}
    </span>
  );
}

/** 27-toggle 拨杆：轨道 32×18、拇指 14，行程 = 32 − 2×2 − 14 = 14px。
    is-init 只在首次交互后加，否则开场每个"开"态开关都要空放一次回弹。 */
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
  const [init, setInit] = useState(false);
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
        if (disabled) return;
        setInit(true);
        onToggle();
      }}
      data-on={checked ? 'true' : 'false'}
      style={{ '--toggle-travel': '14px' } as CSSProperties}
      className={cn(
        't-toggle relative h-[18px] w-8 shrink-0 rounded-pill shadow-track',
        FOCUS_RING,
        init && 'is-init',
        checked ? 'bg-brand-600' : 'bg-ink-300',
        disabled && 'cursor-not-allowed opacity-40',
      )}
    >
      <span className="t-toggle-thumb absolute left-[2px] top-[2px] size-[14px] rounded-full bg-card shadow-knob" />
    </button>
  );
}

/** 读数徽章的壳：灰底小牌，内容走 PopValue。宽度按内容定——「100%」要 w-12，
    两位数步进值 w-9 就够；壳等宽（tnum 等宽数字）pop 时牌子本身不晃。 */
function ValueBadge({ text, className }: { text: string; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center justify-center rounded-sm bg-paper-2 font-mono leading-none text-ink-700 tnum',
        className,
      )}
    >
      <PopValue text={text} />
    </span>
  );
}

/**
 * 0–100 的旋钮行：滑杆 + 实时读数。数字框要先选中再改、还看不出量程，
 * 而这两个值的手感本来就是「拖着找一个合适的松紧」。原生 range 保住
 * role=slider（取证断言不动），外观在 index.css 的 .ft-range。
 */
function SliderRow({
  label,
  value,
  step,
  onApply,
}: {
  label: string;
  value: number;
  step: number;
  onApply: (next: number) => void;
}) {
  return (
    <div className="flex h-8 items-center gap-2.5 rounded-md px-1.5 transition-colors duration-fast hover:bg-paper-2/70">
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <input
        type="range"
        min={0}
        max={100}
        step={step}
        aria-label={label}
        value={value}
        onChange={(event) => onApply(clampInput(event.target.value, 0, 100, value))}
        className="ft-range w-28 shrink-0 cursor-pointer"
        style={{ '--fill': `${value}%` } as CSSProperties}
      />
      <ValueBadge text={`${value}%`} className="w-12 shrink-0" />
    </div>
  );
}

/** 最大形态数上限（沿用旧数字框的 max={24}）。 */
const MAX_PATTERNS = 24;

const STEPPER_BUTTON = cn(
  'inline-flex size-6 shrink-0 items-center justify-center rounded-md border border-line bg-card leading-none text-ink-500 shadow-btn transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40',
  FOCUS_RING,
);

/** −/+ 步进器：比数字框少一次「选中」，边界即禁用态，右缘与滑杆徽章收在同一条线上。 */
function StepperRow({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (next: number) => void;
}) {
  return (
    <div className="flex h-8 items-center justify-between gap-2 rounded-md px-1.5 transition-colors duration-fast hover:bg-paper-2/70">
      <span className="min-w-0 truncate">{label}</span>
      <span className="flex items-center gap-1">
        <button
          type="button"
          aria-label={t('减少')}
          disabled={value <= 0}
          onClick={() => onChange(Math.max(0, value - 1))}
          className={STEPPER_BUTTON}
        >
          −
        </button>
        <ValueBadge text={String(value)} className="w-9 shrink-0" />
        <button
          type="button"
          aria-label={t('增加')}
          disabled={value >= MAX_PATTERNS}
          onClick={() => onChange(Math.min(MAX_PATTERNS, value + 1))}
          className={STEPPER_BUTTON}
        >
          +
        </button>
      </span>
    </div>
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
        'flex h-8 items-center justify-between gap-2 rounded-md px-1.5 transition-colors duration-fast',
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

/** 灰底正文里的一张白纸卡：Fine-tune Card 的分组单元。meta 放右上（如 2/8）。 */
function Card({ title, meta, children, className }: { title: string; meta?: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn('rounded-lg border border-line bg-card p-2 shadow-card', className)}>
      <div className="mb-1 flex h-6 items-center justify-between px-1.5 pt-0.5">
        <h3 className="font-medium leading-none text-ink-600">{title}</h3>
        {meta && <span className="font-mono text-micro leading-none text-ink-300 tnum">{meta}</span>}
      </div>
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
  /* shadcn Dialog 语义：标题走 aria-labelledby、副标题走 aria-describedby，
     比光秃秃一个 aria-label 多给读屏一句「这窗是干什么的」。 */
  const titleId = useId();
  const descId = useId();
  const closeMs = readRootDurationMs('--modal-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);
  useFocusTrap(panelRef, open);

  useEffect(() => {
    if (!mounted) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [mounted, onClose]);

  if (!mounted) return null;

  /* 与 DrawingWorkspace 同规：portal 到 body。组件挂在 KlineChart 里，头顶是
     .page-enter 的路由进场 transform——transform ≠ none 的祖先会接管 fixed 的
     包含块，弹窗就相对整页而不是视口定位（移动端实测 shellTop 飘到 2300+）。 */

  const [primaryGroup, ...secondaryGroups] = GROUPS;
  const groupCard = (group: (typeof GROUPS)[number]) => {
    const rows = LAYERS.filter((layer) => layer.group === group.id);
    const onCount = rows.filter((layer) => settings.enabled.includes(layer.id)).length;
    return (
      <Card key={group.id} title={group.label} meta={`${onCount}/${rows.length}`}>
        <ul className="flex flex-col">
          {rows.map((layer) => {
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
  };

  return createPortal(
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
          aria-labelledby={titleId}
          aria-describedby={descId}
          className={cn(
            't-modal flex max-h-[86vh] flex-col overflow-hidden rounded-xl border border-line bg-paper-2 shadow-sh-3',
            overlayClassName(phase),
          )}
        >
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-card px-5 py-3">
            <div className="min-w-0">
              <h2 id={titleId} className="truncate text-body font-medium leading-tight text-ink-900">{t('算法与图层')}</h2>
              <p id={descId} className="mt-0.5 truncate text-micro text-ink-400">{t('选择预设，或逐层微调算法与图层。')}</p>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                onClick={() => onChange(settingsFromPreset('minimal'))}
                className={cn(
                  'inline-flex h-7 items-center rounded-md border border-line bg-card px-2.5 text-micro leading-none text-ink-500 shadow-btn transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-700 active:scale-95',
                  FOCUS_RING,
                )}
              >
                {t('恢复默认')}
              </button>
              <button
                type="button"
                onClick={onClose}
                className={cn(
                  'inline-flex size-7 items-center justify-center rounded-md text-ink-400 transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-600 active:scale-95',
                  FOCUS_RING,
                )}
                aria-label={t('关闭')}
              >
                <Icon name="x" size={16} />
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 text-micro">
            <div className="flex flex-wrap items-center gap-1.5">
              {PRESET_ORDER.map((id) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={settings.preset === id}
                  aria-label={PRESETS[id].label}
                  onClick={() => onChange(settingsFromPreset(id))}
                  className={cn(
                    'inline-flex h-7 items-center rounded-full border px-3 leading-none transition-colors duration-fast',
                    FOCUS_RING,
                    settings.preset === id
                      /* shadow-chip 而非 shadow-btn：btn 的 75% 白内高光是给白底
                         描边次按钮的，压在蓝底实心胶囊上就是一道白边，还把填充
                         视觉压低 1px 显得没对齐（用户两轮截图抓的就是它）。
                         chip 是设计系统里选中态胶囊的专用档（ResultTable 页码同款）。 */
                      ? 'border-brand-600 bg-brand-600 text-white shadow-chip'
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
              <SliderRow
                label={t('最低几何质量')}
                value={Math.round(settings.minShapeQuality * 100)}
                step={5}
                onApply={(next) => patch({ minShapeQuality: next / 100 })}
              />
              <SliderRow
                label={t('标签密度')}
                value={Math.round(settings.labelDensity * 100)}
                step={10}
                onApply={(next) => patch({ labelDensity: next / 100 })}
              />
              <StepperRow
                label={t('最大形态数')}
                value={settings.maxPatterns}
                onChange={(next) => patch({ maxPatterns: next })}
              />

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
    </>,
    document.body,
  );
}
