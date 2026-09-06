import { Link, useLocation } from 'react-router';
import { usePersonalWatchlist } from '@/hooks/usePersonalWatchlist';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/hooks/useToast';
import { parseWatchlistInput } from '@/lib/personalWatchlist';
import Icon from '@/components/icons';
import { t } from '@/i18n/core';

export default function WatchlistToggle({ ticker }: { ticker: string }) {
  const personal = usePersonalWatchlist();
  const { canManageWatchlist } = useAccess();
  const location = useLocation();
  const toast = useToast();
  const selected = personal.tickers?.includes(ticker) ?? false;
  const style = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-line-strong bg-card px-3 text-caption font-medium text-ink-600 shadow-btn hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50';
  if (!canManageWatchlist && !personal.loading && !personal.error) {
    return <Link className={style} to="/login" state={{ from: location.pathname }}><Icon name="plus" size={15} />{t('登录后加入自选')}</Link>;
  }
  const toggle = async () => {
    if (personal.error) { await personal.refresh(); return; }
    try {
      await personal.edit(selected ? [] : [ticker], selected ? [ticker] : []);
      toast.success(selected ? t('已移出自选') : t('已加入自选'), ticker);
    } catch (error) {
      toast.error(selected ? t('移除失败') : t('加入失败'), error instanceof Error ? error.message : t('请稍后再试'));
    }
  };
  return <button className={style} aria-pressed={selected} disabled={personal.loading || personal.busy || !parseWatchlistInput(ticker).tickers.length}
    title={selected ? t('移出自选') : undefined} onClick={() => void toggle()}>
    <Icon name={selected ? 'check' : 'plus'} size={15} />
    {personal.loading ? t('正在读取自选…') : personal.error ? t('重试读取自选') : selected ? t('已加入自选') : t('加入自选')}
  </button>;
}
