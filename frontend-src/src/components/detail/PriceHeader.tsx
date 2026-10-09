/**
 * 个股整页 S0 头部（原 StockDrawerBody 抽屉头，抽屉撤除后由整页独占）
 * TickerLogo/名称/真实价格与更新反馈/ChangeBadge/时段 chip/quote_as_of
 */
import SoftBadge from '@/components/shared/SoftBadge';
import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { LivePrice } from '@/components/shared/LiveQuote';
import { displayedQuoteLabel, preferLiveQuote } from '@/lib/liveQuotes';
import { motion } from 'framer-motion';
import { DUR_SECTION, EASE_PAPER } from '@/lib/motion';
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
  const priceLabel = quote ? displayedQuoteLabel(quote, quoteStatus, useLive || !isNum(detail?.price)) : null;

  return (
    <motion.header
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <TickerLogo ticker={symbol} size={44} />
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-baseline gap-x-2.5">
            <span className="font-display text-[26px] leading-[32px] font-semibold tracking-[-0.02em] text-ink-900">{symbol}</span>
            <span className="text-body text-ink-500">{detail?.name ?? symbol}</span>
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <SoftBadge>
              {detail?.sector ? t(detail.sector) : t('个股行情')}
            </SoftBadge>
            {market && <SessionLED session={quoteSession === 'postmarket' ? 'afterhours' : quoteSession ?? market.session} label={priceLabel ?? t('{label} · 延迟 15 分钟', { label: market.label })} />}
          </div>
        </div>
        <div className="ml-auto text-right">
          <p className="eyebrow">
            {__t('评分')}
            <InfoHint hint={SCORE_HINTS.strengthComposite} side="bottom" align="end" size={12} className="ml-1" />
          </p>
          <StrengthBar score={detail?.strengthScore ?? Number.NaN} width={72} className="mt-1.5" />
        </div>
      </div>

      {/* 2026-10-09 Arc 改版（行情终端式页头）：左边大号价格 + 涨跌 + 比较基准，右边一组小指标
          （标签在上、数值在下），原先散在三行的成交量、市值、报价时间收进同一组。 */}
      <div className="mt-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
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
            basis={__t('较昨收')}
          />
        </div>
        <dl className="flex flex-wrap items-end gap-x-7 gap-y-3 pb-1.5" data-price-header-stats="">
          <div className="min-w-0">
            <dt className="text-micro text-ink-400">{__t('成交量')}</dt>
            <dd className="mt-0.5 text-body-s text-ink-900 tnum">{compactOr(detail?.volume)}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-micro text-ink-400">{__t('市值')}</dt>
            <dd className="mt-0.5 text-body-s text-ink-900 tnum">{isNum(detail?.marketCap) ? `$${fmtCompact(detail?.marketCap)}` : '—'}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-micro text-ink-400">{__t('报价时间')}</dt>
            <dd className="mt-0.5 text-body-s text-ink-900">
              <span className="tnum">{updatedAt ? fmtTimeHHMMSS(new Date(updatedAt)) : '—'}</span>
              <span className="text-ink-500">{priceLabel ? ` · ${priceLabel}` : __t(' · 延迟行情')}</span>
            </dd>
          </div>
        </dl>
      </div>
    </motion.header>
  );
}
