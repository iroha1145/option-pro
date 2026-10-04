/**
 * B1 指数概览（6 卡：SPX/NDX/DJI/RUT/SOX/VIX），卡片与首页共用 shared/IndexCard。
 * 这里负责栅格、价格闪烁、加载/出错/空态，以及 ?index= 指定卡的高亮与滚动定位。
 */
import { useEffect, useRef } from 'react';
import { isMock, type ApiError } from '@/api/client';
import type { IndexQuote } from '@/api/types';
import { getIndexIntraday } from '@/mocks/marketPulse';
import { quoteSymbol } from '@/lib/quoteSymbol';
import { useTickFlash } from '@/hooks/useTickFlash';
import EmptyState from '@/components/shared/EmptyState';
import IndexCard, { IndexCardSkeleton } from '@/components/shared/IndexCard';
import { BusyIcon } from '@/components/shared/IconSwap';
import { t } from '../../i18n/core.ts';

/* 与首页指数带同一栅格：手机三列两行放下六张，xl 起按最小 170px 自动排 */
const INDEX_GRID = 'grid grid-cols-3 gap-2 sm:gap-3 xl:[grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]';

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

  if (loading) {
    return (
      <div className={INDEX_GRID}>
        {Array.from({ length: 6 }, (_, i) => (
          <IndexCardSkeleton key={i} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="card-surface">
        <EmptyState
          variant="error"
          image="/empty-chart.svg"
          title={error.code === 503 ? t('数据暂不可用') : t('加载失败')}
          description={error.code === 503 ? t('暂无指数数据') : error.message}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              aria-busy={refreshing}
              className="btn-primary"
            >
              <BusyIcon busy={refreshing} size={14} tone="on-accent" />
              {t('重试')}
            </button>
          }
        />
      </div>
    );
  }
  if (!data?.length) {
    /* 成功但 0 条也要留空态（审计 2.2.19）：外层固定渲染了「指数概览」小标题，
       返回 null 会留下一个指向空无一物的标题。 */
    return (
      <div className="card-surface">
        <EmptyState
          image="/empty-chart.svg"
          title={t('暂无指数数据')}
          description={t('暂未取得指数行情，请稍后重试')}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              aria-busy={refreshing}
              className="btn-primary"
            >
              <BusyIcon busy={refreshing} size={14} tone="on-accent" />
              {t('重试')}
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className={INDEX_GRID}>
      {data.map((quote, i) => (
        /* 点卡片开该指数的详情页，与全站「点代码开详情」一致；统一传真实符号 ^GSPC，
           使跳转与缓存键保持一致。?index= 只用于从顶部 tape 跳进来时高亮定位。 */
        <IndexCard
          key={quote.code}
          quote={quote}
          index={i}
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
  );
}
