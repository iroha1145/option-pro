/** 公司标志：本地清单里有图就显示图，没有或加载失败显示首字母；方框尺寸固定，加载前后不变。 */
import { memo, useState } from 'react';
import { companyLogoSource, companySymbol } from '@/lib/companyLogo';
import { cn } from '@/lib/utils';

interface Props { ticker: string; size?: number; className?: string }

function CompanyMark({ ticker, size = 32, className }: Props) {
  const source = companyLogoSource(ticker);
  const [image, setImage] = useState<'loading' | 'loaded' | 'failed'>('loading');
  const showImage = source !== null && image !== 'failed';
  return (
    <span
      data-company-logo={ticker}
      data-logo-state={showImage ? image : 'fallback'}
      className={cn('relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden rounded-md border border-line/70 text-ink-500', showImage ? 'bg-[var(--logo-plate)]' : 'bg-card', className)}
      style={{ width: size, height: size, fontSize: size * 0.45, lineHeight: 1 }}
      aria-hidden="true"
    >
      {showImage ? (
        <img
          src={source}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          draggable={false}
          onLoad={() => setImage('loaded')}
          onError={() => setImage('failed')}
          className="h-full w-full object-contain p-1"
          style={{ visibility: image === 'loaded' ? 'visible' : 'hidden' }}
        />
      ) : ticker.replace(/[^A-Z0-9]/g, '').slice(0, 1) || '—'}
    </span>
  );
}

const TickerLogo = memo(function TickerLogo(props: Props) {
  const ticker = companySymbol(props.ticker);
  return <CompanyMark key={ticker} {...props} ticker={ticker} />;
});
export default TickerLogo;
