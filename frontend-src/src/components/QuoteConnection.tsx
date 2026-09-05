import { useEffect } from 'react';
import { isMock } from '@/api/client';
import { useAccess } from '@/hooks/useAccess';
import { quoteStore } from '@/lib/liveQuotes';

/** Mounted once below identity provider; no keys or owner credentials enter URLs. */
export default function QuoteConnection() {
  const { isOwner, loading, identityUnavailable, username } = useAccess();
  useEffect(() => {
    if (isMock || loading || identityUnavailable) return;
    quoteStore.setVisible(!document.hidden);
    const stop = quoteStore.start(isOwner);
    const onVisibility = () => quoteStore.setVisible(!document.hidden);
    const onPageHide = () => quoteStore.setVisible(false);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onVisibility);
    return () => {
      stop(); document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide); window.removeEventListener('pageshow', onVisibility);
    };
  }, [isOwner, loading, identityUnavailable, username]);
  return null;
}
