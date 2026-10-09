/**
 * 公司标志：有图就显示图，没有或加载失败显示首字母，失败后不重试；方框尺寸固定，加载前后不变。
 * 图片地址见 companyLogoSource：本地清单优先，清单外的代码只在登录会话里请求标志接口。
 */
import { memo, useState } from 'react';
import { isMock } from '@/api/client';
import { useSignedIn } from '@/hooks/useAccess';
import { companyLogoSource, companySymbol } from '@/lib/companyLogo';
import { cn } from '@/lib/utils';

interface Props { ticker: string; size?: number; className?: string }

function CompanyMark({ ticker, source, size = 32, className }: Props & { source: string | null }) {
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
  const signedIn = useSignedIn();
  const ticker = companySymbol(props.ticker);
  const source = companyLogoSource(ticker, { signedIn, mock: isMock });
  // 登录态确认后地址会变：按地址重新挂载，上一个地址的失败状态不带过来
  return <CompanyMark key={`${ticker} ${source ?? ''}`} {...props} ticker={ticker} source={source} />;
});
export default TickerLogo;
