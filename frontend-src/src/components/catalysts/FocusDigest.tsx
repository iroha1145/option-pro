/**
 * 热点追踪卡的正文部件：导语、主导事件列表、逐股评估的说明。
 *
 * 文本先由 focusText 拆条、去重；这里只管排版和展开收起。模型写的正文原样显示，不经 t()。
 * 收起时标题、导语和第一条正文按行数截断。有被收起的条目、板块或风险时一定给展开按钮；
 * 只有一段文字超出行数时，由实测（scrollHeight）决定要不要按钮，不会出现截断了却展不开的情况。
 */
import { useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { motion } from 'framer-motion';
import Icon from '@/components/icons';
import CollapsePresence from '@/components/shared/CollapsePresence';
import SoftBadge, { type BadgeTone } from '@/components/shared/SoftBadge';
import TextSwap from '@/components/shared/TextSwap';
import { DUR_SECTION, EASE_PAPER } from '@/lib/motion';
import { cn } from '@/lib/utils';
import type { FocusEventView, FocusLeadView, FocusPoint, FocusVerdict } from './focusText';
import { t } from '../../i18n/core.ts';

/* 核实结论按含义着色（同市场综合研判的判断标签）：支持用 ok、反证用 danger、无法判断用中性；
   再配一个图标，和同一行的灰色板块芯片区分开。 */
const VERDICT: Record<FocusVerdict, { label: string; tone: BadgeTone; icon: 'check' | 'x' | 'minus' }> = {
  supported: { label: t('已证实'), tone: 'ok', icon: 'check' },
  contradicted: { label: t('有矛盾'), tone: 'danger', icon: 'x' },
  unverifiable: { label: t('无法核实'), tone: 'neutral', icon: 'minus' },
};
/* 板块芯片用常规字重、浅一档的字色，读起来比核实结论轻。收起时手机上只放两个、每个最宽 6rem，
   和核实结论、展开按钮挤在一行里也不折行；其余折进「+N」，展开后全部显示。 */
const SECTOR_CHIP = 'font-normal text-ink-500';
const NARROW_SECTOR_PREVIEW = 2;

/** 收起状态下文字是否被行数截断。展开期间不再测，沿用收起时的结论，按钮要留着才能收回去。 */
function useClamped(ref: RefObject<HTMLElement | null>, collapsed: boolean): boolean {
  const [clamped, setClamped] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!collapsed || !element) return;
    const measure = () => setClamped(element.scrollHeight > element.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, collapsed]);
  return clamped;
}

/** 句首标签（「已证实：」「推断：」）用深一档的字重，正文照原样。 */
function PointText({ point, labelClassName = 'font-medium text-ink-800' }: { point: FocusPoint; labelClassName?: string }) {
  return (
    <>
      {point.label && <span className={labelClassName}>{point.label}</span>}
      {point.text}
    </>
  );
}

/* 列表里每条只放一个图标按钮，不重复写「展开」两个字；看得见的是 32px，触控区用透明伪元素补到 44px。 */
function ChevronToggle({ open, controls, onToggle, className }: {
  open: boolean;
  controls: string;
  onToggle: () => void;
  className?: string;
}) {
  const label = open ? t('收起') : t('展开');
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      aria-label={label}
      title={label}
      onClick={onToggle}
      className={cn(
        "relative inline-flex size-8 shrink-0 items-center justify-center rounded-md text-ink-400 transition-colors duration-fast after:absolute after:-inset-1.5 after:content-[''] hover:bg-paper-2 hover:text-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600",
        className,
      )}
    >
      <Icon name="chevron-down" size={15} className={cn('transition-transform duration-ui', open && 'rotate-180')} />
    </button>
  );
}

/** 导语：收起时最多三行，其余句子和超出的部分点「展开」再看。 */
export function FocusLead({ lead, compact = false }: { lead: FocusLeadView; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);
  const clamped = useClamped(ref, !open);
  const bodyId = useId();
  const expandable = lead.more.length > 0 || clamped;
  return (
    <div className="min-w-0">
      <div id={bodyId}>
        <p ref={ref} className={cn('break-words text-ink-800', compact ? 'text-body-s' : 'text-body', !open && 'line-clamp-3')}>
          {lead.preview.map((point, index) => <PointText key={index} point={point} labelClassName="font-medium" />)}
        </p>
        {lead.more.length > 0 && (
          <CollapsePresence open={open}>
            <div className="space-y-1.5 pt-2">
              {lead.more.map((point, index) => (
                <p key={index} className={cn('break-words text-ink-600', compact ? 'text-caption' : 'text-body-s')}>
                  <PointText point={point} />
                </p>
              ))}
            </div>
          </CollapsePresence>
        )}
      </div>
      {expandable && (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="touch-target mt-1 inline-flex items-center gap-1 rounded-sm text-caption text-ink-500 transition-colors duration-fast hover:text-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          <TextSwap swapKey={open ? 'open' : 'closed'}>{open ? t('收起') : t('展开')}</TextSwap>
          <Icon name="chevron-down" size={14} className={cn('shrink-0 transition-transform duration-ui', open && 'rotate-180')} />
        </button>
      )}
    </div>
  );
}

function EventItem({ event, index, fadeIn }: { event: FocusEventView; index: number; fadeIn: boolean }) {
  const [open, setOpen] = useState(false);
  const titleRef = useRef<HTMLParagraphElement>(null);
  const previewRef = useRef<HTMLParagraphElement>(null);
  const titleClamped = useClamped(titleRef, !open);
  const previewClamped = useClamped(previewRef, !open);
  const bodyId = useId();
  const expandable = event.more.length > 0 || event.hiddenSectors.length > 0 || titleClamped || previewClamped;
  const verdict = event.verdict ? VERDICT[event.verdict] : null;
  const sectors = open ? [...event.sectors, ...event.hiddenSectors] : event.sectors;
  const narrowHidden = [...event.sectors.slice(NARROW_SECTOR_PREVIEW), ...event.hiddenSectors];
  const hasTags = verdict !== null || sectors.length > 0;
  /* 手机上第三个板块也折进了「+N」：只为它展开时，按钮只在窄屏出现。 */
  const narrowOnly = !expandable && narrowHidden.length > 0;
  const toggle = expandable || narrowOnly ? (
    <ChevronToggle
      open={open}
      controls={bodyId}
      onToggle={() => setOpen((value) => !value)}
      className={cn('-my-1.5 -mr-1.5', narrowOnly && !open && 'sm:hidden')}
    />
  ) : null;

  return (
    <motion.li
      initial={fadeIn ? { opacity: 0 } : false}
      animate={{ opacity: 1 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER, delay: 0.1 + Math.min(index * 0.05, 0.3) }}
      className="min-w-0 py-3 first:pt-0 last:pb-0"
    >
      <div id={bodyId} className="min-w-0">
        {hasTags && (
          <div className="mb-1.5 flex items-start gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
              {verdict && (
                <SoftBadge tone={verdict.tone}>
                  <Icon name={verdict.icon} size={11} strokeWidth={2} className="shrink-0" />
                  {verdict.label}
                </SoftBadge>
              )}
              {sectors.map((sector, sectorIndex) => (
                <SoftBadge
                  key={sector}
                  title={sector}
                  className={cn(
                    SECTOR_CHIP,
                    !open && 'max-w-[6rem] sm:max-w-[11rem]',
                    !open && sectorIndex >= NARROW_SECTOR_PREVIEW && 'max-sm:hidden',
                  )}
                >
                  <span className="truncate">{sector}</span>
                </SoftBadge>
              ))}
              {!open && event.hiddenSectors.length > 0 && (
                <SoftBadge className={cn(SECTOR_CHIP, 'max-sm:hidden')} title={event.hiddenSectors.join('、')}>
                  +{event.hiddenSectors.length}
                </SoftBadge>
              )}
              {!open && narrowHidden.length > 0 && (
                <SoftBadge className={cn(SECTOR_CHIP, 'sm:hidden')} title={narrowHidden.join('、')}>
                  +{narrowHidden.length}
                </SoftBadge>
              )}
            </div>
            {toggle}
          </div>
        )}
        <div className={cn(!hasTags && 'flex items-start gap-2')}>
          <p ref={titleRef} className={cn('min-w-0 flex-1 break-words text-body-s font-medium text-ink-900', !open && 'line-clamp-2')}>
            <PointText point={event.title} labelClassName="font-normal text-ink-500" />
          </p>
          {!hasTags && toggle}
        </div>
        {event.preview && (
          <p ref={previewRef} className={cn('mt-1 break-words text-body-s text-ink-600', !open && 'line-clamp-2')}>
            <PointText point={event.preview} />
          </p>
        )}
        {event.more.length > 0 && (
          <CollapsePresence open={open}>
            <div className="space-y-1 pt-1">
              {event.more.map((point, pointIndex) => (
                <p key={pointIndex} className="break-words text-body-s text-ink-600">
                  <PointText point={point} />
                </p>
              ))}
            </div>
          </CollapsePresence>
        )}
      </div>
    </motion.li>
  );
}

/** 主导事件列表；总摘要里有、主导事件里没有的事件折叠在列表末尾。 */
export function FocusEventList({ events, extraEvents, compact = false }: {
  events: FocusEventView[];
  extraEvents: FocusEventView[];
  compact?: boolean;
}) {
  const [extraOpen, setExtraOpen] = useState(false);
  const extraId = useId();
  if (!events.length && !extraEvents.length) return null;
  return (
    <section aria-label={t('主导事件')} className="min-w-0">
      <h4 className="mb-3 text-caption font-medium text-ink-900">{t('主导事件')}</h4>
      <ul className="divide-y divide-line">
        {events.map((event, index) => <EventItem key={event.key} event={event} index={index} fadeIn={!compact} />)}
      </ul>
      {extraEvents.length > 0 && (
        <div className="mt-3 border-t border-line">
          <button
            type="button"
            onClick={() => setExtraOpen((value) => !value)}
            aria-expanded={extraOpen}
            aria-controls={extraOpen ? extraId : undefined}
            className="flex min-h-10 w-full items-center justify-between gap-3 text-left text-caption text-ink-500 transition-colors duration-fast hover:text-ink-800"
          >
            <span>{t('另有 {n} 条事件', { n: extraEvents.length })}</span>
            <Icon name="chevron-down" size={14} className={cn('shrink-0 transition-transform duration-ui', extraOpen && 'rotate-180')} />
          </button>
          <CollapsePresence open={extraOpen} id={extraId}>
            <ul className="divide-y divide-line pb-1">
              {extraEvents.map((event, index) => <EventItem key={event.key} event={event} index={index} fadeIn={false} />)}
            </ul>
          </CollapsePresence>
        </div>
      )}
    </section>
  );
}

/** 逐股评估的说明：收起时两行，展开看全文和模型列出的风险。 */
export function FocusAssessmentNote({ note, risks }: { note: string; risks: readonly string[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);
  const clamped = useClamped(ref, !open);
  const bodyId = useId();
  if (!note && !risks.length) return null;
  const expandable = risks.length > 0 || clamped;
  return (
    <div className="flex min-w-0 items-start gap-2">
      <div id={bodyId} className="min-w-0 flex-1">
        {note && (
          <p ref={ref} className={cn('break-words text-caption text-ink-500', !open && 'line-clamp-2')}>{note}</p>
        )}
        {risks.length > 0 && (
          <CollapsePresence open={open}>
            <div className="pt-1.5">
              <p className="text-micro text-ink-400">{t('风险')}</p>
              <ul className="mt-0.5 space-y-0.5">
                {risks.map((risk, index) => (
                  <li key={index} className="flex gap-1.5 text-caption text-ink-500">
                    <span className="mt-2 size-1 shrink-0 rounded-full bg-ink-300" aria-hidden="true" />
                    <span className="min-w-0 break-words">{risk}</span>
                  </li>
                ))}
              </ul>
            </div>
          </CollapsePresence>
        )}
      </div>
      {expandable && (
        <ChevronToggle open={open} controls={bodyId} onToggle={() => setOpen((value) => !value)} className="-my-1.5 -mr-1.5" />
      )}
    </div>
  );
}
