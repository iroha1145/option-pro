/**
 * 个股整页 S0 头部（原 StockDrawerBody 抽屉头，抽屉撤除后由整页独占）
 * TickerLogo/名称/真实价格与更新反馈/ChangeBadge/时段 chip/quote_as_of
 */
import SoftBadge from '@/components/shared/SoftBadge';
import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { LivePrice } from '@/components/shared/LiveQuote';
import { quoteLabel, preferLiveQuote } from '@/lib/liveQuotes';
import { motion } from 'framer-motion';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtTimeHHMMSS } from '@/lib/format';
import TickerLogo from '@/components/shared/TickerLogo';
import { InsightValue } from '@/components/shared/InsightCard';
import SessionLED from '@/components/shared/SessionLED';
import StrengthBar from '@/components/shared/StrengthBar';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import type { StockDetail } from '@/api/types';
import { t, t as __t } from '../../i18n/core.ts';

/** live 缺失数值字段（类型为 number 但运行时可为 null）如实显「—」 */
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const compactOr = (v: number | null | undefined): string => (isNum(v) ? fmtCompact(v) : '—');

export default function PriceHeader({ detail, symbol: requestedSymbol }: { detail?: StockDetail | null; symbol?: string }) {
  const symbol = requestedSymbol ?? detail?.ticker ?? '';
  const quote = useLiveQuote(symbol);
  const quoteStatus = useQuoteStatus();
  const quoteSession = quoteStatus.market_session ?? quote?.session;
  const { data: market } = usePolling(() => marketApi.status(), 60_000, []);
  const useLive = preferLiveQuote(quote, isNum(detail?.price) && detail.price > 0, detail?.updatedAt);
  const updatedAt = useLive ? quote?.trade_at : detail?.updatedAt;

  return (
    <motion.header
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <TickerLogo ticker={symbol} size={40} />
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-baseline gap-x-2.5">
            <span className="font-display text-[22px] leading-[28px] font-bold text-ink-900">{symbol}</span>
            <span className="text-body-s text-ink-500">{detail?.name ?? symbol}</span>
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <SoftBadge>
              {detail?.sector ?? t('个股行情')}
            </SoftBadge>
            {market && <SessionLED session={quoteSession === 'postmarket' ? 'afterhours' : quoteSession ?? market.session} label={quote ? quoteLabel(quote, quoteStatus.market_session) : t('{label} · 延迟 15 分钟', { label: market.label })} />}
          </div>
        </div>
        <div className="ml-auto text-right">
          <p className="eyebrow">
            {__t('强度分')}
            <InfoHint hint={SCORE_HINTS.strengthComposite} side="bottom" align="end" size={12} className="ml-1" />
          </p>
          <StrengthBar score={detail?.strengthScore ?? Number.NaN} width={72} className="mt-1.5" />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        {/* Insight Cards 的数值块口径：大读数 + 涨跌 + 绝对变动 + **比较基准**。
            基准不是装饰——只给「+2.57%」而不说跟谁比，读者只能猜；tick-flash
            仍要贴在价格本体上，所以外面再包一层承接闪动类名。 */}
        <div
          className={cn(
            'tick-flash min-w-0 max-w-full rounded-sm px-1',
          )}
        >
          <InsightValue
            size="xl"
            value={<LivePrice symbol={symbol} fallback={detail?.price} fallbackAt={detail?.updatedAt} prefix="$" indicator={false} />}
            changePct={useLive ? quote?.change_pct : detail?.changePct}
            change={useLive ? quote?.change : detail?.change ?? null}
            basis={__t('vs 昨收')}
          />
        </div>
        <p className="pb-1.5 text-right font-mono text-micro text-ink-500 tnum">
          {__t('成交量')} {compactOr(detail?.volume)} {__t('· 市值')} {isNum(detail?.marketCap) ? `$${fmtCompact(detail?.marketCap)}` : '—'}
        </p>
      </div>

      <p className="mt-2 text-micro text-ink-400">
        {__t('报价更新于')} <span className="font-mono tnum">{updatedAt ? fmtTimeHHMMSS(new Date(updatedAt)) : '—'}</span>
        {quote ? ` · ${quoteLabel(quote, quoteStatus.market_session)}` : __t(' · 延迟行情')}
      </p>
    </motion.header>
  );
}
