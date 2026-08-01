/** calendar 面板：经济日历（impact 分级色条 + impact_zh + forecast/previous/actual，按日期分组） */
import { useMemo } from 'react';
import { usePolling } from '@/hooks/usePolling';
import { browserCalendarQuery, catalystsContract } from './api';
import type { EconomicEvent } from './api';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonRows } from '@/components/shared/Skeleton';
import { cn } from '@/lib/utils';
import { t as __t } from '../../i18n/core.ts';

const IMPACT_STYLE: Record<EconomicEvent['impact'], { bar: string; chip: string; dots: number }> = {
  high: { bar: 'bg-down-600', chip: 'bg-down-50 text-down-700', dots: 3 },
  medium: { bar: 'bg-warn-600', chip: 'bg-warn-50 text-warn-600', dots: 2 },
  low: { bar: 'bg-brand-400', chip: 'bg-brand-50 text-brand-600', dots: 1 },
  holiday: { bar: 'bg-ink-300', chip: 'bg-paper-2 text-ink-500 border border-line', dots: 0 },
};

function ImpactChip({ ev }: { ev: EconomicEvent }) {
  const s = IMPACT_STYLE[ev.impact];
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-xs px-1.5 py-0.5 text-micro font-medium', s.chip)}>
      {s.dots > 0 && (
        <span className="flex gap-0.5" aria-hidden="true">
          {Array.from({ length: 3 }, (_, i) => (
            <span key={i} className={cn('size-1 rounded-full', i < s.dots ? 'bg-current' : 'bg-current opacity-25')} />
          ))}
        </span>
      )}
      {ev.impactZh}
    </span>
  );
}

export default function CalendarPanel({ refreshToken }: { refreshToken: number }) {
  const q = usePolling(
    () => catalystsContract.calendar(browserCalendarQuery()),
    300_000,
    [refreshToken],
  );

  const groups = useMemo(() => {
    const map = new Map<string, EconomicEvent[]>();
    (q.data ?? []).forEach((ev) => {
      const d = new Date(ev.scheduledAt); // 按本地日期分组（ISO 为 UTC，直接 slice 会串天）
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      const arr = map.get(key) ?? [];
      arr.push(ev);
      map.set(key, arr);
    });
    return [...map.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([date, events]) => [
        date,
        [...events].sort(
          (left, right) =>
            Date.parse(left.scheduledAt) - Date.parse(right.scheduledAt),
        ),
      ] as const);
  }, [q.data]);

  const todayKey = useMemo(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }, []);

  if (q.loading && !q.data) {
    return (
      <div className="card-surface">
        <SkeletonRows rows={8} />
      </div>
    );
  }
  if (q.error) {
    return (
      <div className="card-surface">
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={q.error.code === 503 ? __t('日历数据暂不可用') : __t('加载失败')}
          description={__t("稍后刷新再试")}
          action={
            <button
              onClick={q.refresh}
              className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
            >
              {__t('重试')}
            </button>
          }
        />
      </div>
    );
  }

  if (groups.length === 0) {
    /* 成功但为空（假期/无事件窗口）需要空态（审计 2.2.16）：一张空白塌陷的
       卡片没法区分「没有事件」和「渲染坏了」。 */
    return (
      <div className="card-surface">
        <EmptyState
          icon="doc-quote"
          title={__t('本窗口暂无经济事件')}
          description={__t('切换时间范围或稍后再看')}
        />
      </div>
    );
  }

  return (
    <div className="card-surface overflow-hidden">
      {groups.map(([date, events], gi) => {
        const d = new Date(`${date}T00:00:00`);
        const isToday = date === todayKey;
        return (
          <div key={date} className={cn(gi > 0 && 'border-t border-line')}>
            {/* 日期分组头 */}
            <div className={cn('flex items-center justify-between px-5 py-2.5', isToday ? 'bg-brand-50' : 'bg-card-warm')}>
              <p className={cn('font-mono text-caption font-semibold tnum', isToday ? 'text-brand-700' : 'text-ink-600')}>
                {d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', weekday: 'short' })}
                {isToday && <span className="ml-2 rounded-xs bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white">{__t('今日')}</span>}
              </p>
              <span className="font-mono text-micro text-ink-400 tnum">{events.length} {__t('项')}</span>
            </div>
            <div className="divide-y divide-line">
              {/* 后续区块 rise-in 减量：直接呈现 */}
              {events.map((ev) => {
                const s = IMPACT_STYLE[ev.impact];
                const t = new Date(ev.scheduledAt);
                const allDay = t.getHours() === 0 && t.getMinutes() === 0;
                return (
                  <div
                    key={ev.eventId}
                    className="flex items-stretch gap-3 px-5 py-3"
                  >
                    {/* 重要度分级色条 */}
                    <span className={cn('w-[3px] shrink-0 rounded-full', s.bar)} aria-hidden="true" />
                    <div className="flex w-12 shrink-0 flex-col justify-center">
                      <span className="font-mono text-[11px] leading-[14px] text-ink-500 tnum">
                        {allDay ? __t('全天') : t.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })}
                      </span>
                      <span className="text-[10px] leading-[14px] text-ink-300">{ev.country}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-body-s font-medium text-ink-800">{ev.title}</p>
                        <ImpactChip ev={ev} />
                      </div>
                      <p className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-micro text-ink-400 tnum">
                        <span>{__t('预期')} <span className="text-ink-600">{ev.forecast}</span></span>
                        <span>{__t('前值')} <span className="text-ink-600">{ev.previous}</span></span>
                        <span>
                          {__t('实际')}{' '}
                          {ev.actual !== null ? (
                            <span className="font-medium text-brand-600">{ev.actual}</span>
                          ) : ev.releaseStatus === 'awaiting_source' ? (
                            <span className="text-warn-600">{__t('数据源未回填')}</span>
                          ) : (
                            <span className="text-ink-300">{__t('待公布')}</span>
                          )}
                        </span>
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
