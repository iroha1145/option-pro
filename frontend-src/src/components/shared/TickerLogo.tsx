/** Company logo; keep the ticker letter visible until an image actually paints. */
import { memo, useState } from 'react';
import { isMock } from '@/api/client';
import { companyLogoSources, companySymbol } from '@/lib/companyLogo';
import { cn } from '@/lib/utils';

interface Props {
  ticker: string;
  size?: number;
  className?: string;
  /** First-screen marks skip lazy-loading so the image starts with the row. */
  priority?: boolean;
}

function CompanyMark({ ticker, size = 32, className, priority = false }: Props) {
  const sources = companyLogoSources(ticker, isMock);
  const [{ index, loaded }, setImage] = useState({ index: 0, loaded: false });
  const source = sources[index];
  const fallback = ticker.replace(/[^A-Z0-9]/g, '').slice(0, 1) || '—';
  return (
    <span
      data-company-logo={ticker}
      data-logo-state={source ? loaded ? 'loaded' : 'loading' : 'fallback'}
      className={cn('relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden rounded-md border border-line/70 bg-card font-semibold text-ink-500', className)}
      style={{ width: size, height: size, fontSize: size * 0.45, lineHeight: 1 }}
      aria-hidden="true"
    >
      <span className={cn(loaded && source && 'invisible')}>{fallback}</span>
      {source ? (
        <img
          key={source}
          src={source}
          alt=""
          loading={priority ? 'eager' : 'lazy'}
          fetchPriority={priority ? 'high' : undefined}
          decoding="async"
          referrerPolicy="no-referrer"
          draggable={false}
          onLoad={() => setImage((state) => ({ ...state, loaded: true }))}
          onError={() => setImage((state) => ({ index: state.index + 1, loaded: false }))}
          className="absolute inset-0 h-full w-full object-contain p-1"
          style={{ visibility: loaded ? 'visible' : 'hidden' }}
        />
      ) : null}
    </span>
  );
}

const TickerLogo = memo(function TickerLogo(props: Props) {
  const ticker = companySymbol(props.ticker);
  return <CompanyMark key={ticker} {...props} ticker={ticker} />;
});
export default TickerLogo;
