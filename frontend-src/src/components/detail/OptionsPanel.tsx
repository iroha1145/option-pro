/**
 * 期权链（stock-detail.md T3）
 * 到期日下拉 → Calls ｜ 行权价 ｜ Puts 三带表（行权价列高亮，ITM 侧浅底区分）
 * 异动行（vol/oi > 3）bolt 角标 + 倍数 chip + 权利金流（估算）
 * owner：「AI 期权解读」（option_alerts 任务 + 轮询 + 确认费用）；visitor 隐藏
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { isMock } from '@/api/client';
import { optionsApi } from '@/api/modules/options';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import { usePolling } from '@/hooks/usePolling';
import { useAccess } from '@/hooks/useAccess';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonRows } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtPrice } from '@/lib/format';
import { OPTION_SUPPORTED_LIST, optionsSupported } from '@/mocks/fixtures2';
import { AI_DISCLAIMER, useAiJob } from './useAiJob';
import type { OptionChainRow } from '@/api/types';

function dte(expiration: string): number {
  return Math.max(0, Math.round((new Date(`${expiration}T16:00:00`).getTime() - Date.now()) / 86_400_000));
}

const mid = (bid: number, ask: number) => (bid + ask) / 2;

interface RowMeta {
  callRatio: number;
  putRatio: number;
  callAlert: boolean;
  putAlert: boolean;
  callPremium: number; // 美元，估算
  putPremium: number;
}

function rowMeta(r: OptionChainRow): RowMeta {
  const callRatio = r.callOi > 0 ? r.callVol / r.callOi : 0;
  const putRatio = r.putOi > 0 ? r.putVol / r.putOi : 0;
  return {
    callRatio,
    putRatio,
    callAlert: callRatio > 3,
    putAlert: putRatio > 3,
    callPremium: r.callVol * mid(r.callBid, r.callAsk) * 100,
    putPremium: r.putVol * mid(r.putBid, r.putAsk) * 100,
  };
}

/* ---------------- AI 期权解读（owner） ---------------- */
function AiOptionInsight({ ticker, expiration }: { ticker: string; expiration: string | null }) {
  const { isOwner } = useAccess();
  const { job, error, start, cancel, reset } = useAiJob();
  const [confirming, setConfirming] = useState(false);

  if (!isOwner) return null;

  const running = job && (job.status === 'queued' || job.status === 'running');
  return (
    <div className="mt-4 rounded-md border border-ai-600/25 bg-ai-50 p-3.5">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-body-s font-medium text-ink-800">
          <Icon name="spark-ai" size={15} className="text-ai-600" />
          AI 期权解读
        </p>
        {!job && !confirming && (
          <button
            onClick={() => setConfirming(true)}
            className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-white transition-[filter] duration-fast hover:brightness-105"
          >
            生成解读
          </button>
        )}
      </div>

      {!job && confirming && (
        <div className="mt-2.5">
          <p className="text-caption text-ink-600">将对 {ticker} 当前期权链生成异动解读，消耗 1 次 AI 额度，确认继续？</p>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => {
                setConfirming(false);
                void start(() => aiJobsApi.createOptionAlerts({ tickers: [ticker] }));
              }}
              className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-white hover:brightness-105"
            >
              确认生成
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="rounded-md border border-line-strong px-3 py-1.5 text-caption text-ink-600 hover:bg-paper-2"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {running && (
        <div className="mt-2.5">
          <div className="flex items-center justify-between text-caption text-ink-500">
            <span className="flex items-center gap-1.5">
              <span className="size-1.5 animate-led-pulse rounded-full bg-ai-600" />
              {job!.status === 'queued' ? '排队中…' : `解读中 ${job!.progress}%`}
            </span>
            <button onClick={() => void cancel()} className="text-ink-400 hover:text-ink-600">取消任务</button>
          </div>
          <div className="mt-1.5 h-1 overflow-hidden rounded-pill bg-line">
            <div className="h-full rounded-pill bg-ai-600 transition-all duration-ui" style={{ width: `${job!.progress}%` }} />
          </div>
        </div>
      )}

      {error && <p className="mt-2.5 text-caption text-down-700">任务失败：{error}</p>}

      {job?.status === 'succeeded' && (
        <div className="mt-2.5">
          <p className="text-body-s leading-relaxed text-ink-600">
            {job.result && job.result.length > 24
              ? job.result
              : `${ticker} 期权链解读完成（到期 ${expiration ?? '—'}）：成交/持仓异动集中于近月价外行权价，bolt 角标行为 vol/oi 超过 3 倍的位置；权利金流以买卖中价估算，量级最大的行权价附近是关键博弈区。`}
          </p>
          <p className="mt-2 text-micro text-ink-400">
            {AI_DISCLAIMER} · 到期 {expiration ?? '—'}
          </p>
          <button onClick={reset} className="mt-2 text-caption font-medium text-ai-600 hover:text-ai-600/80">
            重新生成
          </button>
        </div>
      )}
      {(job?.status === 'failed' || job?.status === 'cancelled') && (
        <p className="mt-2.5 text-caption text-ink-500">
          任务{job.status === 'failed' ? '失败' : '已取消'} ·{' '}
          <button onClick={reset} className="font-medium text-ai-600">重试</button>
        </p>
      )}
    </div>
  );
}

/* ---------------- 链主体 ---------------- */
export default function OptionsPanel({ ticker }: { ticker: string }) {
  // 支持名单只属于 mock 数据集;live 由真实接口自证(到期日为空 → 诚实空态)
  const supported = !isMock || optionsSupported(ticker);
  const [expiration, setExpiration] = useState<string | null>(null);
  const { data: expirations, loading: expLoading } = usePolling(
    () => optionsApi.expirations(ticker),
    null,
    [ticker],
    );
  const exp = expiration ?? expirations?.[0] ?? null;
  const { data: chain, loading: chainLoading } = usePolling(
    () => (exp ? optionsApi.chain(ticker, exp) : Promise.resolve(null)),
    null,
    [ticker, exp],
  );

  const atmRef = useRef<HTMLTableRowElement>(null);
  const atmStrike = useMemo(() => {
    if (!chain || chain.rows.length === 0) return null;
    return chain.rows.reduce((best, r) =>
      Math.abs(r.strike - chain.spot) < Math.abs(best - chain.spot) ? r.strike : best,
    chain.rows[0].strike);
  }, [chain]);

  useEffect(() => {
    if (atmRef.current) {
      atmRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [atmStrike, exp]);

  if (!supported) {
    return (
      <EmptyState
        icon="doc-quote"
        title="期权链快照未覆盖该标的"
        description={`支持标的：${OPTION_SUPPORTED_LIST}`}
        variant="error"
        className="py-8"
      />
    );
  }

  if (expLoading) return <SkeletonRows rows={6} />;
  const expList = Array.from(new Set(expirations ?? []));
  if (expList.length === 0) {
    return <EmptyState icon="doc-quote" title="暂无到期日数据" description="期权到期日快照为空" className="py-8" />;
  }

  return (
    <div>
      {/* 到期日下拉 + DTE */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative inline-flex items-center">
          <select
            value={exp ?? ''}
            onChange={(e) => setExpiration(e.target.value)}
            className="h-9 appearance-none rounded-md border border-line-strong bg-card pl-3 pr-8 font-mono text-caption text-ink-800 tnum transition-shadow focus:shadow-focus-ring focus:outline-none"
            aria-label="选择到期日"
          >
            {expList.map((x) => (
              <option key={x} value={x}>
                {x} · DTE {dte(x)}
              </option>
            ))}
          </select>
          <Icon name="chevron-down" size={14} className="pointer-events-none absolute right-2.5 text-ink-400" />
        </label>
        {chain && (
          <p className="text-micro text-ink-400">
            标的价 <span className="font-mono text-ink-600 tnum">{fmtPrice(chain.spot)}</span>
          </p>
        )}
      </div>

      {/* 三带表 */}
      <div className="relative mt-3 max-h-[420px] overflow-auto rounded-lg border border-line">
        {chainLoading || !chain ? (
          <SkeletonRows rows={8} />
        ) : (
          <table className="min-w-[520px] w-full whitespace-nowrap border-collapse font-mono text-micro tnum">
            <thead className="sticky top-0 z-10">
              <tr className="bg-card-warm text-left font-sans text-micro text-ink-400">
                <th className="px-2 py-2 font-medium" colSpan={2}>CALLS · 量/持 · 权利金</th>
                <th className="px-2 py-2 text-center font-medium">行权价</th>
                <th className="px-2 py-2 text-right font-medium" colSpan={2}>权利金 · 量/持 · PUTS</th>
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false}>
                {chain.rows.map((r) => {
                  const m = rowMeta(r);
                  const isAtm = r.strike === atmStrike;
                  const callItm = r.strike < chain.spot;
                  const putItm = r.strike > chain.spot;
                  const alert = m.callAlert || m.putAlert;
                  return (
                    <motion.tr
                      key={`${exp}-${r.strike}`}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1, transition: { duration: 0.2 } }}
                      ref={isAtm ? atmRef : undefined}
                      className={cn(
                        'h-9 border-t border-line align-middle',
                        isAtm ? 'bg-brand-50' : alert ? 'bg-warn-50/50' : undefined,
                      )}
                      title={
                        alert
                          ? `成交异动 ${Math.max(m.callRatio, m.putRatio).toFixed(1)}× · 权利金流约 $${fmtCompact(
                              Math.max(m.callPremium, m.putPremium),
                            )}（估算）`
                          : undefined
                      }
                    >
                      {/* CALLS 侧 */}
                      <td className={cn('px-2 py-1.5', callItm && 'bg-paper-2')}>
                        <span className="text-ink-800">{fmtCompact(r.callVol)}</span>
                        <span className="text-ink-300"> / </span>
                        <span className="text-ink-500">{fmtCompact(r.callOi)}</span>
                      </td>
                      <td className={cn('px-2 py-1.5', callItm && 'bg-paper-2')}>
                        <span className="text-ink-800">{fmtPrice(mid(r.callBid, r.callAsk))}</span>
                        {m.callAlert && (
                          <span className="ml-1.5 rounded-xs bg-warn-50 px-1 py-px text-[10px] font-medium text-warn-600">
                            {m.callRatio.toFixed(1)}×
                          </span>
                        )}
                      </td>
                      {/* 行权价 */}
                      <td
                        className={cn(
                          'relative px-2 py-1.5 text-center',
                          isAtm ? 'font-semibold text-brand-700' : 'text-ink-600',
                        )}
                      >
                        {isAtm && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" aria-hidden="true" />}
                        {fmtPrice(r.strike, r.strike >= 100 ? 0 : 2)}
                      </td>
                      {/* PUTS 侧 */}
                      <td className={cn('px-2 py-1.5 text-right', putItm && 'bg-paper-2')}>
                        {m.putAlert && (
                          <span className="mr-1.5 rounded-xs bg-warn-50 px-1 py-px text-[10px] font-medium text-warn-600">
                            {m.putRatio.toFixed(1)}×
                          </span>
                        )}
                        <span className="text-ink-800">{fmtPrice(mid(r.putBid, r.putAsk))}</span>
                      </td>
                      <td className={cn('px-2 py-1.5 text-right', putItm && 'bg-paper-2')}>
                        <span className="text-ink-800">{fmtCompact(r.putVol)}</span>
                        <span className="text-ink-300"> / </span>
                        <span className="text-ink-500">{fmtCompact(r.putOi)}</span>
                        {alert && <Icon name="bolt" size={12} className="ml-1 inline text-warn-600" aria-label="成交异动" />}
                      </td>
                    </motion.tr>
                  );
                })}
              </AnimatePresence>
            </tbody>
          </table>
        )}
      </div>

      <p className="mt-2 text-micro text-ink-400">
        浅底为价内（ITM）侧 · 异动标注 vol/oi &gt; 3（倍数为该侧比值）· 权利金按买卖中价估算 · 非收益承诺
      </p>

      <AiOptionInsight ticker={ticker} expiration={exp} />
    </div>
  );
}
