import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useBodyScrollLock } from '@/hooks/useBodyScrollLock';
import { isTopFocusScope } from '@/lib/focusScope';
import { DEFAULT_WATCHLIST_TICKERS, parseWatchlistInput, watchlistDelta } from '@/lib/personalWatchlist';
import Icon from '@/components/icons';
import { t } from '@/i18n/core';

interface Props {
  tickers: string[];
  maxTickers: number;
  busy: boolean;
  onSave: (add: string[], remove: string[]) => Promise<unknown>;
  onClose: () => void;
}

export default function WatchlistManager({ tickers, maxTickers, busy, onSave, onClose }: Props) {
  const [original] = useState(() => [...tickers]);
  const [draft, setDraft] = useState(() => [...tickers]);
  const [input, setInput] = useState('');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState('');
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  useFocusTrap(panelRef, true, { initialFocusRef: inputRef });
  useBodyScrollLock(true);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || !isTopFocusScope(panelRef.current)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!busy) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const append = (raw: string, clearInput = true): string[] | null => {
    const parsed = parseWatchlistInput(raw);
    if (parsed.invalid.length) {
      setError(t('代码格式不正确：{tickers}', { tickers: parsed.invalid.join('、') }));
      return null;
    }
    const next = [...new Set([...draft, ...parsed.tickers])];
    if (next.length > maxTickers) {
      setError(t('最多保存 {count} 只股票，请先移除一些代码', { count: maxTickers }));
      return null;
    }
    setError('');
    setDraft(next);
    if (clearInput) setInput('');
    return next;
  };

  const save = async () => {
    const next = input.trim() ? append(input) : draft;
    if (!next) return;
    const changes = watchlistDelta(original, next);
    try {
      await onSave(changes.add, changes.remove);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('请稍后再试'));
    }
  };
  const delta = watchlistDelta(original, draft);
  const changed = delta.add.length + delta.remove.length > 0 || input.trim().length > 0;
  const allSelected = draft.length > 0 && selected.size === draft.length;
  const secondary = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-line-strong bg-card px-3 text-caption font-medium text-ink-600 hover:bg-paper-2 disabled:cursor-not-allowed disabled:opacity-50';

  return createPortal(
    <>
      <div className="fixed inset-0 z-[85] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]" data-focus-backdrop={id} aria-hidden="true" onClick={() => !busy && onClose()} />
      <div className="pointer-events-none fixed inset-0 z-[86] flex items-center justify-center p-3 sm:p-6">
        <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} data-focus-overlay={id}
          className="pointer-events-auto flex max-h-[90dvh] w-full max-w-[640px] flex-col overflow-hidden rounded-xl border border-line bg-card shadow-sh-3">
          <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line p-4 sm:px-6">
            <div className="min-w-0">
              <h2 id={`${id}-title`} className="text-h3 text-ink-900">{t('管理自选')}</h2>
              <p id={`${id}-description`} className="mt-1 text-caption leading-relaxed text-ink-500">{t('批量添加或移除股票，保存后生效。')}</p>
            </div>
            <button className="inline-flex size-11 shrink-0 items-center justify-center rounded-md text-ink-400 hover:bg-paper-2 disabled:opacity-50" aria-label={t('关闭')} onClick={onClose} disabled={busy}><Icon name="x" size={18} /></button>
          </header>
          <div className="min-h-0 overflow-y-auto overscroll-contain p-4 sm:px-6">
            <label className="text-caption font-medium text-ink-700" htmlFor={`${id}-input`}>{t('添加股票代码')}</label>
            <textarea ref={inputRef} id={`${id}-input`} value={input} onChange={(event) => { setInput(event.target.value); setError(''); }} disabled={busy}
              rows={2} maxLength={2000} placeholder="AAPL, MSFT, NVDA, SPY" aria-describedby={`${id}-hint`}
              className="mt-2 w-full resize-y rounded-md border border-line-strong bg-paper px-3 py-2 font-mono text-body-s uppercase text-ink-800 outline-none focus:border-brand-600 focus:shadow-focus-ring disabled:opacity-50" />
            <p id={`${id}-hint`} className="mt-1 text-caption text-ink-400">{t('用逗号、空格或换行分隔，重复代码会自动合并。')}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button className={secondary} onClick={() => append(input)} disabled={busy || !input.trim()}><Icon name="plus" size={15} />{t('加入列表')}</button>
              <button className={secondary} onClick={() => append(DEFAULT_WATCHLIST_TICKERS.join(','), false)} disabled={busy || DEFAULT_WATCHLIST_TICKERS.every((symbol) => draft.includes(symbol))}>{t('添加默认 4 只')}</button>
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-y border-line py-2">
              <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 text-caption text-ink-700">
                <input type="checkbox" checked={allSelected} disabled={busy || !draft.length} onChange={() => setSelected(allSelected ? new Set() : new Set(draft))} className="size-4 accent-brand-600" />{t('全选')}
                <span className="font-mono text-ink-400">{draft.length} / {maxTickers}</span>
              </label>
              <button className={secondary} disabled={busy || !selected.size} onClick={() => { setDraft(draft.filter((symbol) => !selected.has(symbol))); setSelected(new Set()); setError(''); }}>{t('移除所选（{count}）', { count: selected.size })}</button>
            </div>
            {draft.length ? <div className="mt-2 grid grid-cols-2 gap-x-3 sm:grid-cols-3">
              {draft.map((symbol) => <label key={symbol} className="flex min-h-11 min-w-0 cursor-pointer items-center gap-2 border-b border-line/70 px-1 text-caption text-ink-700">
                <input type="checkbox" checked={selected.has(symbol)} aria-label={t('选择 {ticker}', { ticker: symbol })} disabled={busy} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(symbol)) next.delete(symbol); else next.add(symbol); return next; })} className="size-4 shrink-0 accent-brand-600" />
                <span className="truncate font-mono font-medium">{symbol}</span>
              </label>)}
            </div> : <p className="py-6 text-center text-caption text-ink-400">{t('列表为空，也可以保存。')}</p>}
            {error && <p role="alert" className="mt-3 break-words rounded-md bg-down-50 px-3 py-2 text-caption text-down-700">{error}</p>}
          </div>
          <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-line bg-card-warm p-4 sm:px-6">
            <p className="text-caption text-ink-500" aria-live="polite">{t('新增 {add} · 移除 {remove}', { add: delta.add.length, remove: delta.remove.length })}</p>
            <div className="ml-auto flex gap-2">
              <button className={secondary} onClick={onClose} disabled={busy}>{t('取消')}</button>
              <button className="inline-flex min-h-11 items-center justify-center rounded-md bg-brand-600 px-4 text-caption font-medium text-white shadow-btn-hi hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => void save()} disabled={busy || !changed}>{busy ? t('正在保存…') : t('保存自选')}</button>
            </div>
          </footer>
        </div>
      </div>
    </>, document.body,
  );
}
