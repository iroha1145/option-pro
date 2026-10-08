/** 评分方法卡：说明当前评分规则，读取失败时保留重试入口。 */
import { useId, useState } from 'react';
import type { StrengthProfile } from '@/api/types';
import Icon from '@/components/icons';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { t as __t } from '../../i18n/core.ts';

export function MethodCard({
  profile,
  loading = false,
  error = false,
  onRetry,
}: {
  profile: StrengthProfile | null;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const profileUnknown = profile === null;
  return (
    <div className="card-surface t-acc p-5" data-open={open ? 'true' : 'false'}>
      <button
        type="button"
        className="t-acc-head flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
      >
        <span className="eyebrow">
          {__t('评分方法 ·')}{' '}
          {profile ? profile.name : loading ? __t('读取中') : __t('评分设置未知')}
        </span>
        <span className="t-acc-chevron text-ink-400">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path d="M4 6.5L8 10.5L12 6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        </span>
      </button>
      <div id={panelId} className="t-acc-panel" aria-hidden={!open} inert={!open}>
        <div className="t-acc-panel-inner">
          {profileUnknown && loading ? (
            <div className="mt-4 space-y-2.5" aria-hidden="true">
              <span className="skeleton-shimmer block h-3 w-full rounded-xs" />
              <span className="skeleton-shimmer block h-3 w-full rounded-xs" />
            </div>
          ) : profileUnknown && error ? (
            <div className="mt-4">
              <p className="text-caption leading-[18px] text-ink-500">
                {__t('评分设置读取失败，请重试。')}
              </p>
              {onRetry && (
                <button onClick={onRetry} className="control-button mt-2">
                  <Icon name="refresh" size={12} />
                  {__t('重试')}
                </button>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              <p className="text-caption leading-[18px] text-ink-500">{SCORE_HINTS.strengthComposite.body}</p>
              {SCORE_HINTS.strengthComposite.note && (
                <p className="text-micro leading-[16px] text-ink-400">{SCORE_HINTS.strengthComposite.note}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
