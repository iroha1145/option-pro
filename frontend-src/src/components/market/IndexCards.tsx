/**
 * B1 指数概览，卡片与首页共用 shared/IndexCard。
 * 这里负责栅格、价格闪烁、加载/出错/空态，以及 ?index= 指定卡的高亮与滚动定位。
 * 本页其余读数只算美股，所以指数按市场分两组：美股指数在前，日经、上证这类其他市场另列一组。
 */
import { useEffect, useRef, type ReactNode } from 'react';
import { isMock, type ApiError } from '@/api/client';
import type { IndexQuote } from '@/api/types';
import { getIndexIntraday } from '@/mocks/marketPulse';
import { isUsIndexSymbol, quoteSymbol } from '@/lib/quoteSymbol';
import { useTickFlash } from '@/hooks/useTickFlash';
import EmptyState from '@/components/shared/EmptyState';
import IndexCard, { IndexCardSkeleton } from '@/components/shared/IndexCard';
import { BusyIcon } from '@/components/shared/IconSwap';
import { t } from '../../i18n/core.ts';

/* 与首页指数带同一栅格：窄于 360px 时两列，其余手机三列，xl 起按最小 170px 自动排 */
/* 手机两三列、平板多列时是独立小卡；xl 起排成一行，合成一条指标带（参照 uiarc.dev 的 KPI 条）：
   外框一圈发丝线，格子之间用 1px 间隙露出线色分隔，格子本身去掉边框和圆角。auto-fit 收起空轨道，不会出现空格。 */
const INDEX_GRID = 'grid grid-cols-2 gap-2 min-[360px]:grid-cols-3 sm:gap-3 xl:[grid-template-columns:repeat(auto-fit,minmax(170px,1fr))] xl:gap-px xl:overflow-hidden xl:rounded-[16px] xl:border xl:border-line xl:bg-line';

function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="eyebrow mb-3">{children}</p>;
}

const indexKey = (quote: IndexQuote) => quote.code;
const indexPrice = (quote: IndexQuote) => quote.price;

export default function IndexCards({
  data,
  loading,
  error,
  focus,
  onRetry,
  refreshing,
  onOpen,
}: {
  data: IndexQuote[] | null;
  loading: boolean;
  error: ApiError | null;
  focus: string | null;
  onRetry: () => void;
  refreshing: boolean;
  onOpen: (code: string) => void;
}) {
  const flashes = useTickFlash(data, indexKey, indexPrice);

  /* ?index= 滚动定位 */
  const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  /* 每个 focus 值只滚动一次：deps 里的 data 每 60s 轮询都换新引用，
     不加一次性守卫会周期性把页面强行拽回这张卡。 */
  const scrolledForRef = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || !data) return;
    const code = focus.toUpperCase();
    if (scrolledForRef.current === code) return;
    const el = cardRefs.current[code];
    if (el) {
      scrolledForRef.current = code;
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }, [focus, data]);

  /* 加载、出错、空态时还不知道有哪些市场，标题沿用「指数概览」 */
  const overview = <Eyebrow>{t('指数概览')} · {t('延迟行情')}</Eyebrow>;

  if (loading) {
    return (
      <>
        {overview}
        <div className={INDEX_GRID}>
          {Array.from({ length: 6 }, (_, i) => (
            <IndexCardSkeleton key={i} />
          ))}
        </div>
      </>
    );
  }
  const retry = (
    <button
      onClick={onRetry}
      disabled={refreshing}
      aria-busy={refreshing}
      className="btn-primary"
    >
      <BusyIcon busy={refreshing} size={14} tone="on-accent" />
      {t('重试')}
    </button>
  );
  if (error) {
    return (
      <>
        {overview}
        <div className="card-surface">
          <EmptyState
            variant="error"
            image="/empty-chart.svg"
            title={error.code === 503 ? t('数据暂不可用') : t('加载失败')}
            description={error.code === 503 ? t('暂无指数数据') : error.message}
            action={retry}
          />
        </div>
      </>
    );
  }
  if (!data?.length) {
    /* 成功但 0 条也要留空态（审计 2.2.19）：只留一个「指数概览」小标题、下面什么都没有，
       看起来像页面坏了。 */
    return (
      <>
        {overview}
        <div className="card-surface">
          <EmptyState
            image="/empty-chart.svg"
            title={t('暂无指数数据')}
            description={t('暂未取得指数行情，请稍后重试')}
            action={retry}
          />
        </div>
      </>
    );
  }

  const groups = [
    { name: t('美股指数'), label: <>{t('美股指数')} · {t('延迟行情')}</>, quotes: data.filter((q) => isUsIndexSymbol(q.symbol || q.code)) },
    { name: t('其他市场'), label: t('其他市场'), quotes: data.filter((q) => !isUsIndexSymbol(q.symbol || q.code)) },
  ].filter((group) => group.quotes.length > 0);

  return (
    /* xl 起两组并排成两条指标带，宽度按卡片数分配，两组的卡一样宽；更窄时上下排。 */
    <div className="space-y-6 xl:flex xl:gap-4 xl:space-y-0">
      {groups.map((group, g) => {
        /* 入场错峰的序号跨组连续 */
        const offset = groups.slice(0, g).reduce((n, prev) => n + prev.quotes.length, 0);
        return (
          <div key={group.name} role="group" aria-label={group.name} className="min-w-0 xl:basis-0" style={{ flexGrow: group.quotes.length }}>
            <Eyebrow>{group.label}</Eyebrow>
            <div className={INDEX_GRID}>
              {group.quotes.map((quote, i) => (
                /* 点卡片开该指数的详情页，与全站「点代码开详情」一致；统一传真实符号 ^GSPC，
                   使跳转与缓存键保持一致。?index= 只用于从顶部 tape 跳进来时高亮定位。 */
                <IndexCard
                  key={quote.code}
                  quote={quote}
                  index={offset + i}
                  flash={flashes[quote.code]}
                  focused={focus?.toUpperCase() === quote.code}
                  ref={(el) => {
                    cardRefs.current[quote.code] = el;
                  }}
                  /* live 没有指数 K 线端点，小图只在演示模式画 */
                  spark={isMock && quote.changePct !== null ? getIndexIntraday(quote.code, quote.changePct) : null}
                  onOpen={() => onOpen(quoteSymbol(quote.symbol || quote.code))}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
