import { useEffect, useMemo, useRef, useState } from 'react';
import type { OptionChain } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtPrice } from '@/lib/format';
import Icon from '@/components/icons';
import FilterButton from '@/components/shared/FilterButton';
import SelectionViewport from '@/components/shared/SelectionViewport';
import { t } from '../../../i18n/core.ts';
import { contractsForChain, selectContracts, type ChainContract, type ContractScope, type ContractSide } from './chainMetrics.ts';

const number = (n: number | null) => n === null ? '—' : fmtCompact(n);
const price = (n: number | null) => n === null ? '—' : `$${fmtPrice(n)}`;
// Do not round a 102.5 strike to 103: it identifies a different contract.
const strikeText = (n: number) => String(n);
const sideName = (side: ContractSide) => side === 'call' ? t('看涨（Call）') : t('看跌（Put）');
const contractName = (c: ChainContract) => `${sideName(c.side)} · $${strikeText(c.strike)}`;

function reason(c: ChainContract): string {
  if (c.activity.includes('ratio') && c.volOi.kind === 'ratio') {
    return t('成交量为持仓量的 {ratio} 倍', { ratio: c.volOi.ratio.toFixed(1) });
  }
  if (c.activity.includes('zero_oi')) return t('零持仓有成交 · 待核对');
  if (c.activity.includes('volume')) return t('成交量达到 5,000 张');
  if (c.activity.includes('premium')) return t('估算成交金额达到 50 万美元');
  return t('未达到关注阈值');
}

function SideLabel({ side }: { side: ContractSide }) {
  return <span className={cn('inline-flex items-center rounded px-2 py-1 text-caption font-medium',
    side === 'call' ? 'bg-brand-50 text-brand-700' : 'bg-ai-50 text-ai-600')}>{sideName(side)}</span>;
}

function Ratio({ contract: c }: { contract: ChainContract }) {
  return <span className={cn('font-mono tnum', c.activity.includes('ratio') && 'font-semibold text-warn-700')}>
    {c.volOi.kind === 'ratio' ? `${c.volOi.ratio.toFixed(1)}×` : c.volOi.kind === 'new_opening' ? t('不可比') : '—'}
  </span>;
}

function ContractDetail({ contract: c, onClose }: { contract: ChainContract; onClose: () => void }) {
  return <section className="rounded-lg border border-brand-100 bg-brand-50/40 p-4" aria-label={t('合约报价明细')}>
    <div className="flex items-center justify-between gap-3">
      <h4 className="text-body-s font-semibold text-ink-900">{contractName(c)}</h4>
      <button type="button" onClick={onClose} className="flex min-h-9 items-center gap-1 rounded-md px-2 text-caption text-ink-600 hover:bg-card" aria-label={t('收起合约明细')}>
        {t('收起')}<Icon name="chevron-down" className="rotate-180" size={13} />
      </button>
    </div>
    <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4">
      {[
        [t('买方报价'), price(c.bid)], [t('卖方报价'), price(c.ask)],
        [t('隐含波动率'), c.iv === null ? '—' : `${(c.iv * 100).toFixed(1)}%`],
        [t('估算成交金额'), c.premium === null ? '—' : `$${fmtCompact(c.premium)}`],
      ].map(([label, value]) => <div key={label}><dt className="text-caption text-ink-500">{label}</dt><dd className="mt-1 font-mono text-body-s text-ink-900 tnum">{value}</dd></div>)}
    </dl>
    <p className="mt-3 text-caption text-ink-600">{reason(c)}</p>
    <p className="mt-2 text-micro leading-relaxed text-ink-500">{t('参考价为买卖报价中值，不保证成交；金额按中价 × 成交量 × 100 估算，不是实际资金流入。隐含波动率可能包含模型估算。')}</p>
  </section>;
}

export default function ChainBrowser({ chain }: { chain: OptionChain }) {
  const contracts = useMemo(() => contractsForChain(chain), [chain]);
  const [scope, setScope] = useState<ContractScope>('near');
  const [side, setSide] = useState<ContractSide | 'all'>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const openDetail = (id: string, button: HTMLButtonElement) => {
    openerRef.current = button;
    setSelectedId((previous) => previous === id ? null : id);
  };
  const closeDetail = () => {
    setSelectedId(null);
    openerRef.current?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!selectedId) return;
    detailRef.current?.focus({ preventScroll: true });
    detailRef.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }, [selectedId]);
  const visible = useMemo(() => selectContracts(contracts, scope, side, chain.spot), [contracts, scope, side, chain.spot]);
  const alerts = useMemo(() => selectContracts(contracts, 'alerts', 'all', chain.spot), [contracts, chain.spot]);
  const selected = contracts.find((c) => c.id === selectedId) ?? null;
  const maxVolume = Math.max(1, ...contracts.map((c) => c.volume ?? 0));
  const hasSpot = chain.spot !== null && Number.isFinite(chain.spot) && chain.spot > 0;
  const nearest = hasSpot ? [...new Set(contracts.map((c) => c.strike))].sort((a, b) => Math.abs(a - chain.spot!) - Math.abs(b - chain.spot!))[0] : null;

  return <div className="mt-5 space-y-5">
    <section className="overflow-hidden rounded-lg border border-line" aria-label={t('成交关注')}>
      <div className="flex items-center justify-between gap-3 border-b border-line bg-paper-2/60 px-4 py-3">
        <div className="flex items-center gap-2"><Icon name="bolt" size={16} className="text-warn-600" /><h4 className="text-body-s font-semibold text-ink-900">{t('成交关注')}</h4><span className="font-mono text-caption text-ink-500">{alerts.length}</span></div>
        {alerts.length > 0 && <button type="button" onClick={() => { setScope('alerts'); setSide('all'); setSelectedId(null); }} className="min-h-9 rounded-md px-2 text-caption font-medium text-brand-700 hover:bg-brand-50">{t('查看全部异动')}<span aria-hidden="true"> →</span></button>}
      </div>
      {alerts.length > 0 ? <ul className="divide-y divide-line">
        {alerts.slice(0, 3).map((c) => <li key={c.id}>
          <button type="button" onClick={(event) => openDetail(c.id, event.currentTarget)} aria-label={t('查看 {contract} 明细', { contract: contractName(c) })} className="grid min-h-16 w-full grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-3 text-left transition-colors hover:bg-paper-2 sm:grid-cols-[minmax(170px,1fr)_1.5fr_auto]">
            <span className="flex flex-wrap items-center gap-2"><SideLabel side={c.side} /><span className="font-mono text-body-s font-semibold text-ink-900">${strikeText(c.strike)}</span></span>
            <span className="col-start-1 text-caption text-warn-700 sm:col-auto">{reason(c)}</span>
            <span className="col-start-2 row-start-1 row-end-3 text-right sm:col-auto sm:row-auto"><span className="block font-mono text-body-s text-ink-900 tnum">{number(c.volume)}</span><span className="text-micro text-ink-500">{t('成交张数')}</span></span>
          </button>
        </li>)}
      </ul> : <p className="px-4 py-4 text-caption text-ink-500">{t('当前到期日没有达到关注规则的合约，仍可查看完整期权链。')}</p>}
      <p className="border-t border-line px-4 py-2.5 text-micro leading-relaxed text-ink-500">{t('标记依据成交量、成交量与持仓量之比及估算金额；看涨、看跌是合约类型，不代表买卖方向。')}</p>
    </section>

    {selected && <div ref={detailRef} tabIndex={-1} className="scroll-mt-24" onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); closeDetail(); } }}><ContractDetail contract={selected} onClose={closeDetail} /></div>}

    <section aria-label={t('期权合约列表')}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SelectionViewport>
        <div className="filter-group" role="group" aria-label={t('合约范围')}>
          {([['near', t('现价附近')], ['alerts', t('仅看异动')], ['all', t('全部合约')]] as const).map(([value, label]) => <FilterButton key={value} active={scope === value} onClick={() => { setScope(value); setSelectedId(null); }} >{label}</FilterButton>)}
        </div>
        </SelectionViewport>
        <SelectionViewport>
        <div className="filter-group" role="group" aria-label={t('合约类型')}>
          {([['all', t('全部')], ['call', t('看涨（Call）')], ['put', t('看跌（Put）')]] as const).map(([value, label]) => <FilterButton key={value} active={side === value} onClick={() => { setSide(value); setSelectedId(null); }} >{label}</FilterButton>)}
        </div>
        </SelectionViewport>
      </div>
      <div className="my-3 flex flex-wrap items-center justify-between gap-2 text-caption text-ink-500">
        <p>{scope === 'near' ? hasSpot ? t('展示距离现价最近的 11 档行权价') : t('现价缺失，展示全部行权价') : scope === 'alerts' ? t('按成交量从高到低排列') : t('按行权价从低到高排列')}</p>
        <span>{t('{n} 份合约', { n: visible.length })}</span>
      </div>
      {visible.length === 0 ? <p className="rounded-lg border border-dashed border-line-strong px-4 py-8 text-center text-body-s text-ink-500">{t('当前条件下没有合约')}</p> : <>
        <div data-options-scroll className="hidden max-h-[520px] overflow-auto rounded-lg border border-line md:block">
          <table className="w-full min-w-[740px] border-collapse text-caption" aria-label={t('期权合约列表')}>
            <thead className="sticky top-0 z-10 bg-paper-2 text-ink-500"><tr>
              {[t('合约类型'), t('行权价'), t('参考价（每股）'), t('成交量'), t('持仓量'), t('成交 / 持仓'), t('详情')].map((label, i) => <th key={label} scope="col" className={cn('border-b border-line px-3 py-3 font-medium', i === 0 || i === 6 ? 'text-left' : 'text-right')}>{label}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-line">{visible.map((c) => <tr key={c.id} className={cn('hover:bg-paper-2/70', selectedId === c.id && 'bg-brand-50')}>
              <td className="px-3 py-3"><SideLabel side={c.side} /></td>
              <td className="px-3 py-3 text-right"><span className="font-mono font-semibold text-ink-900">${strikeText(c.strike)}</span>{c.strike === nearest && <span className="mt-0.5 block text-[10px] text-ink-500">{t('最接近现价')}</span>}</td>
              <td className="px-3 py-3 text-right font-mono text-ink-800 tnum">{price(c.mid)}</td>
              <td className="min-w-28 px-3 py-3 text-right"><span className="font-mono text-ink-900 tnum">{number(c.volume)}</span><div className="mt-1.5 h-1 overflow-hidden rounded-sm bg-line" aria-hidden="true"><div className={cn('h-full', c.side === 'call' ? 'bg-brand-500/60' : 'bg-ai-600/55')} style={{ width: `${(c.volume ?? 0) / maxVolume * 100}%` }} /></div></td>
              <td className="px-3 py-3 text-right font-mono text-ink-600 tnum">{number(c.openInterest)}</td>
              <td className="px-3 py-3 text-right"><Ratio contract={c} />{c.activity.length > 0 && <span className="mt-0.5 block text-[10px] text-warn-700">{t('需关注')}</span>}</td>
              <td className="px-3 py-3"><button type="button" className="min-h-9 rounded-md border border-line-strong bg-card px-2.5 text-caption text-ink-600 hover:border-brand-400 hover:text-brand-700" onClick={(event) => openDetail(c.id, event.currentTarget)} aria-expanded={selectedId === c.id} aria-label={t('查看 {contract} 明细', { contract: contractName(c) })}>{t('明细')}</button></td>
            </tr>)}</tbody>
          </table>
        </div>
        <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line md:hidden" aria-label={t('期权合约列表')}>
          {visible.map((c) => <li key={c.id} className="p-3.5">
            <div className="flex items-center justify-between gap-2"><span className="flex items-center gap-2"><SideLabel side={c.side} /><strong className="font-mono text-body-s text-ink-900">${strikeText(c.strike)}</strong></span><button type="button" onClick={(event) => openDetail(c.id, event.currentTarget)} aria-expanded={selectedId === c.id} aria-label={t('查看 {contract} 明细', { contract: contractName(c) })} className="min-h-9 rounded-md border border-line-strong px-3 text-caption text-ink-600">{t('明细')}</button></div>
            <dl className="mt-3 grid grid-cols-3 gap-2">{[[t('参考价（每股）'), price(c.mid)], [t('成交量'), number(c.volume)], [t('持仓量'), number(c.openInterest)]].map(([label, value]) => <div key={label}><dt className="text-micro text-ink-500">{label}</dt><dd className="mt-1 font-mono text-caption text-ink-900 tnum">{value}</dd></div>)}</dl>
            {c.activity.length > 0 && <p className="mt-3 rounded-md bg-warn-50 px-2 py-1.5 text-caption text-warn-700">{reason(c)}</p>}
          </li>)}
        </ul>
      </>}
      <details className="mt-3 rounded-md border border-line px-3 py-2 text-caption text-ink-500">
        <summary className="cursor-pointer py-1 font-medium text-ink-600">{t('这些数字怎么读？')}</summary>
        <dl className="mt-2 grid gap-3 pb-2 sm:grid-cols-2">
          <div><dt className="font-medium text-ink-800">{t('行权价')}</dt><dd className="mt-1 leading-relaxed">{t('合约约定的股票交易价格；最接近现价不等于现价本身。')}</dd></div>
          <div><dt className="font-medium text-ink-800">{t('成交量与持仓量')}</dt><dd className="mt-1 leading-relaxed">{t('成交量是当日累计交易张数；持仓量是上次更新时尚未了结的张数，两者时间口径不同。')}</dd></div>
          <div><dt className="font-medium text-ink-800">{t('关注规则')}</dt><dd className="mt-1 leading-relaxed">{t('成交量至少为持仓量的 3 倍、零持仓有成交、成交至少 5,000 张，或估算成交金额至少 50 万美元。规则只帮助筛选，不判断买卖方向。')}</dd></div>
          <div><dt className="font-medium text-ink-800">{t('缺失与估算')}</dt><dd className="mt-1 leading-relaxed">{t('「—」表示缺失，不是零。零持仓无法计算倍数，也不能说明全部是新开仓。报价为延迟数据。')}</dd></div>
        </dl>
      </details>
    </section>
  </div>;
}
