import { useEffect } from 'react';
import { isMock } from '@/api/client';
import { useAccess } from '@/hooks/useAccess';
import { quoteStore } from '@/lib/liveQuotes';

/** Mounted once below identity provider; no keys or owner credentials enter URLs. */
export default function QuoteConnection() {
  const { isOwner, loading, hasConfirmedIdentity, identityUnavailable, username } = useAccess();
  useEffect(() => {
    if (isMock || loading || !hasConfirmedIdentity || identityUnavailable) return;
    quoteStore.setVisible(!document.hidden);
    const stop = quoteStore.start(isOwner);
    const onVisibility = () => quoteStore.setVisible(!document.hidden);
    const onPageHide = () => quoteStore.setVisible(false);
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) quoteStore.setVisible(!document.hidden);
    };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      stop(); document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide); window.removeEventListener('pageshow', onPageShow);
    };
  }, [isOwner, loading, hasConfirmedIdentity, identityUnavailable, username]);
  return null;
}
