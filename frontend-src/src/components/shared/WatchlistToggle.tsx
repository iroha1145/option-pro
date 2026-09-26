import { useState } from 'react';
import { Link, useLocation } from 'react-router';
import { usePersonalWatchlist } from '@/hooks/usePersonalWatchlist';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/hooks/useToast';
import { watchlistErrorMessage } from '@/api/modules/account';
import { parseWatchlistInput } from '@/lib/personalWatchlist';
import Icon from '@/components/icons';
import IconSwap from '@/components/shared/IconSwap';
import Spinner from '@/components/shared/Spinner';
import { t } from '@/i18n/core';

export default function WatchlistToggle({ ticker }: { ticker: string }) {
  const personal = usePersonalWatchlist();
  const { canManageWatchlist } = useAccess();
  const location = useLocation();
  const toast = useToast();
  const selected = personal.tickers?.includes(ticker) ?? false;
  /* 只有「这一次点击把它加进来」才播成功勾（transitions.dev 10-success-check）；
     进页时本来就在自选里的票显示静止的勾，不空放一次庆祝。写入前就置位：
     写入完成与 busy 回落往往在同一次渲染里，等 await 之后再置位就晚了一拍。 */
  const [justAdded, setJustAdded] = useState(false);
  const style = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-line-strong bg-card px-3 text-caption font-medium text-ink-600 shadow-btn transition-[color,border-color,transform] duration-fast ease-snap hover:border-brand-400 hover:text-brand-600 active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-50';
  if (!canManageWatchlist && !personal.loading && !personal.error) {
    return <Link className={style} to="/login" state={{ from: location.pathname }}><Icon name="plus" size={15} />{t('登录后加入自选')}</Link>;
  }
  const toggle = async () => {
    if (personal.error) { await personal.refresh(); return; }
    setJustAdded(!selected);
    try {
      await personal.edit(selected ? [] : [ticker], selected ? [ticker] : []);
      toast.success(selected ? t('已移出自选') : t('已加入自选'), ticker);
    } catch (error) {
      setJustAdded(false);
      toast.error(selected ? t('移除失败') : t('加入失败'), watchlistErrorMessage(error, personal.maxTickers));
    }
  };
  /* 勾只在「刚加入且已确认」时挂成庆祝版：写入回来时 tickers 与 busy 在同一次渲染
     里翻转，这时换挂载才能让关键帧与外层淡入同时播放，而不是在加载圈底下空放掉。 */
  const check = justAdded && selected
    ? <span className="t-success-check is-inline" data-state="in"><Icon name="check" size={15} /></span>
    : <Icon name="check" size={15} />;
  return <button className={style} aria-pressed={selected} aria-busy={personal.busy} disabled={personal.loading || personal.busy || !parseWatchlistInput(ticker).tickers.length}
    title={selected ? t('移出自选') : undefined} onClick={() => void toggle()}>
    {/* 加号 ↔ 勾在同一格交叉淡换（09-icon-swap），写入在途时外层再换成加载圈，
        三种状态共用一个图标格，按钮宽度与文字位置都不跳（beUI button-stateful 的节奏）。 */}
    <IconSwap
      state={personal.busy ? 'b' : 'a'}
      a={<IconSwap state={selected ? 'b' : 'a'} a={<Icon name="plus" size={15} />} b={check} />}
      b={<Spinner size={13} tone="brand" />}
    />
    {personal.loading ? t('正在读取自选…') : personal.error ? t('重试读取自选') : selected ? t('已加入自选') : t('加入自选')}
  </button>;
}
