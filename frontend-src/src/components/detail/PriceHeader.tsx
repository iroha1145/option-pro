/**
 * 个股整页 S0 头部（原 StockDrawerBody 抽屉头，抽屉撤除后由整页独占）
 * TickerLogo/名称/真实价格与更新反馈/ChangeBadge/时段 chip/quote_as_of
 */
import SoftBadge from '@/components/shared/SoftBadge';
import SameWidth from '@/components/shared/SameWidth';
import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { LivePrice } from '@/components/shared/LiveQuote';
import { delayedSessionLabel, displayedQuoteLabel, preferLiveQuote } from '@/lib/liveQuotes';
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

/* 小指标组每一项按「常见读数里最宽的一种」预留宽度（SameWidth：隐形样例叠在同一格，格子取最宽的）：
   「—」换成数值、说明换成读到的那句，这一项都不变宽。lg 起整组靠右，宽度一变左缘就动；sm 到 lg 之间
   各项靠左排，前一项变宽会把后一项推走。样例随语言与字体排版，不写死像素：数字用 8，说明取访客最常读到的
   「时段 · 延迟 15 分钟」三种。手机是两列网格，宽度由网格定，样例整格不排版（FROM_SM）；读数比样例更宽
   （罕见的长说明）时只撑开一次。 */
const FROM_SM = 'max-sm:hidden';
const VOLUME_SAMPLES = ['888.8M'];
const MARKET_CAP_SAMPLES = ['$888.88B'];
/* 时间的宽度手机上也要留：「—」换成 19:59:00 时，后面的说明不会被推走。 */
const QUOTE_TIME_SAMPLE = <span className="tnum">00:00:00</span>;
const QUOTE_TIME_SAMPLES = [QUOTE_TIME_SAMPLE];
const QUOTE_LINE_SAMPLES = (['regular', 'premarket', 'postmarket'] as const).map((session) => (
  <span key={session}>{QUOTE_TIME_SAMPLE}{` · ${delayedSessionLabel(session)}`}</span>
));

export default function PriceHeader({ detail, symbol: requestedSymbol }: { detail?: StockDetail | null; symbol?: string }) {
  const symbol = requestedSymbol ?? detail?.ticker ?? '';
  const quote = useLiveQuote(symbol);
  const quoteStatus = useQuoteStatus();
  const quoteSession = quoteStatus.market_session ?? quote?.session;
  const { data: market } = usePolling(() => marketApi.status(), 60_000, []);
  const useLive = preferLiveQuote(quote, isNum(detail?.price) && detail.price > 0, detail?.updatedAt);
  const updatedAt = useLive ? quote?.trade_at : detail?.updatedAt;
  const priceLabel = quote ? displayedQuoteLabel(quote, quoteStatus, useLive || !isNum(detail?.price)) : null;
  const hasScore = isNum(detail?.strengthScore);

  /* 不做入场位移：读取中与读到后是两棵树，页头会重挂，位移动画会在数据到达时再播一遍。 */
  return (
    <header>
      {/* 第一行的版式与名称长短、有没有评分无关：读取中页头只有代码，读到后名称可长可短，两种情况高度必须一样。
          名称块占剩余宽度（flex-1），标题固定一行、名称过长就截断（读屏仍读全名）；评分块手机上独占一行，
          从 sm 起与名称同一行。 */}
      <div className="flex flex-wrap items-center gap-3">
        <TickerLogo ticker={symbol} size={44} />
        <div className="min-w-0 flex-1">
          <h1 className="flex items-baseline gap-x-2.5">
            <span className="shrink-0 font-display text-[26px] leading-[32px] font-semibold tracking-[-0.02em] text-ink-900">{symbol}</span>
            <span className="truncate text-body text-ink-500">{detail?.name ?? symbol}</span>
          </h1>
          {/* 手机上行业徽标与交易时段各占一行，时段读到之前先留出这一行，读到前后页头同高。
              sm 以上两者同一行：详情读到后徽标从「个股行情」换成行业名、宽度会变，时段若先到就被推走，
              所以读到详情之前时段占位但不可见，读到后在最终位置出现；手机上它在徽标下一行，照常显示。 */}
          <div className="mt-1 flex flex-col items-start gap-1 sm:flex-row sm:flex-wrap sm:items-center sm:gap-2">
            <SoftBadge>
              {detail?.sector ? t(detail.sector) : t('个股行情')}
            </SoftBadge>
            {market ? (
              <SessionLED
                className={cn(!detail && 'sm:invisible')}
                session={quoteSession === 'postmarket' ? 'afterhours' : quoteSession ?? market.session}
                label={priceLabel ?? t('{label} · 延迟 15 分钟', { label: market.label })}
              />
            ) : (
              <span className="min-h-[1lh] text-caption sm:hidden" aria-hidden="true" />
            )}
          </div>
        </div>
        {/* 评分块始终占着位置：分数晚到（4 秒补取或下一轮轮询）时页头不能变高。没有分数时整块仍在版面里，
            只是看不见，读屏跳过。 */}
        <div className={cn('ml-auto basis-full text-right sm:basis-auto', !hasScore && 'invisible')} aria-hidden={hasScore ? undefined : true}>
          <p className="eyebrow">
            {__t('评分')}
            <InfoHint hint={SCORE_HINTS.strengthComposite} side="bottom" align="end" size={12} className="ml-1" />
          </p>
          <StrengthBar score={detail?.strengthScore} width={72} className="mt-1.5" />
        </div>
      </div>

      {/* 2026-10-09 Arc 改版（行情终端式页头）：左边大号价格 + 涨跌 + 比较基准，右边一组小指标
          （标签在上、数值在下），原先散在三行的成交量、市值、报价时间收进同一组。 */}
      <div className="mt-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        {/* Insight Cards 的数值块口径：大读数 + 涨跌 + 绝对变动 + **比较基准**。
            基准不是装饰——只给「+2.57%」而不说跟谁比，读者只能猜；tick-flash
            仍要贴在价格本体上，所以外面再包一层承接闪动类名。 */}
        {/* 手机上价格独占一行、比较基准再起一行：读取中只有「—」，读到后价格与涨跌变宽，两种情况下面的指标组都另起一行。 */}
        <div
          className={cn(
            'tick-flash min-w-0 max-w-full basis-full rounded-sm px-1 sm:basis-auto',
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
        {/* 手机上报价时间单独一行，读到后的长说明不会把这一组从一行挤成两行。
            从 lg 起这一组才与价格同一行；sm 到 lg 之间（w-full）它另起一行：能不能并排取决于读到的数据，
            读取中价格与涨跌只有「—」放得下，读到后变宽就掉到价格下面，页头会在数据到达时变高。
            各项按样例预留宽度（见文件顶部）：数据到达前后各项宽度不变，整组左缘不动。 */}
        <dl className="grid w-full grid-cols-2 items-end gap-x-7 gap-y-3 pb-1.5 sm:flex sm:flex-wrap lg:w-auto" data-price-header-stats="">
          <div className="min-w-0">
            <dt className="text-micro text-ink-400">{__t('成交量')}</dt>
            <dd className="mt-0.5 text-body-s text-ink-900 tnum">
              <SameWidth samples={VOLUME_SAMPLES} sampleClassName={FROM_SM}>{compactOr(detail?.volume)}</SameWidth>
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-micro text-ink-400">{__t('市值')}</dt>
            <dd className="mt-0.5 text-body-s text-ink-900 tnum">
              <SameWidth samples={MARKET_CAP_SAMPLES} sampleClassName={FROM_SM}>{isNum(detail?.marketCap) ? `$${fmtCompact(detail?.marketCap)}` : '—'}</SameWidth>
            </dd>
          </div>
          <div className="col-span-2 min-w-0">
            <dt className="text-micro text-ink-400">{__t('报价时间')}</dt>
            <dd className="mt-0.5 text-body-s text-ink-900">
              <SameWidth samples={QUOTE_LINE_SAMPLES} sampleClassName={FROM_SM}>
                <SameWidth samples={QUOTE_TIME_SAMPLES}>
                  <span className="tnum">{updatedAt ? fmtTimeHHMMSS(new Date(updatedAt)) : '—'}</span>
                </SameWidth>
                <span className="text-ink-500">{priceLabel ? ` · ${priceLabel}` : __t(' · 延迟行情')}</span>
              </SameWidth>
            </dd>
          </div>
        </dl>
      </div>
    </header>
  );
}
