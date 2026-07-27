/**
 * /login（login.md 精修版）· 编辑式登录封面
 * L0 纸白 + 加密点阵 + login-motif 主视觉（漂移 24s + 手绘 K 线循环生长 6s/停 3s/淡 1s）
 * L1 左 7 列：Logo + 衬线 Display-XL 逐字大标 + 副文 + 特性三行 + 脚注
 * L2 右 5 列：毛玻璃密码卡（r-xl + sh-3 + backdrop-blur）
 *   提交→mock 登录成功(owner)→/watchlist；空密码/错误 nudge-shake
 *   错误映射：login_cooldown→连续登录失败，请稍后再试 / https_required→密码模式需 HTTPS / 其他→密码不正确
 * L3 移动单栏；「返回公开研究页面」链接；reduced-motion 降级；页面不可见暂停 K 线循环
 */
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { motion, useReducedMotion } from 'framer-motion';
import { useAccess } from '@/hooks/useAccess';
import { accessApi } from '@/api/modules/access';
import { ApiError } from '@/api/client';
import { useToast } from '@/components/Toast';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import type { IconName } from '@/components/icons';
import { t } from '../i18n/core.ts';

/* ---------------- L0 主视觉（内联 login-motif + K 线循环） ---------------- */
const MOTIF_STYLE = `
  .login-kline-loop {
    stroke-dasharray: 1000;
    stroke-dashoffset: 1000;
    animation: login-kline-loop 10s cubic-bezier(.16,1,.3,1) infinite;
  }
  @keyframes login-kline-loop {
    0% { stroke-dashoffset: 1000; opacity: 0; animation-timing-function: cubic-bezier(.16,1,.3,1); }
    8% { opacity: 1; }
    60% { stroke-dashoffset: 0; opacity: 1; }
    88% { stroke-dashoffset: 0; opacity: 1; }
    100% { stroke-dashoffset: 0; opacity: 0; }
  }
  .login-kline-blip {
    transform-box: fill-box;
    transform-origin: center;
    opacity: 0;
    animation: login-kline-blip 10s ease-out infinite;
  }
  @keyframes login-kline-blip {
    0%, 58% { transform: scale(.15); opacity: 0; }
    63% { opacity: .8; }
    80% { transform: scale(1); opacity: 0; }
    100% { transform: scale(1); opacity: 0; }
  }
  .login-drift { animation: login-drift 24s cubic-bezier(.45,0,.15,1) infinite alternate; }
  @keyframes login-drift {
    from { transform: translate(-8px, -6px); }
    to { transform: translate(8px, 8px); }
  }
  .login-paused .login-drift,
  .login-paused .login-kline-loop,
  .login-paused .login-kline-blip { animation-play-state: paused; }
  @media (prefers-reduced-motion: reduce) {
    .login-drift, .login-kline-loop, .login-kline-blip { animation: none !important; }
    .login-kline-loop { stroke-dashoffset: 0; opacity: 1; }
  }
`;

function LoginMotif({ className }: { className?: string }) {
  return (
    <div className={cn('login-drift', className)} aria-hidden="true">
      <style>{MOTIF_STYLE}</style>
      <svg viewBox="0 0 1200 900" fill="none" className="h-auto w-full">
        <defs>
          <pattern id="login-motif-dots" width="18" height="18" patternUnits="userSpaceOnUse">
            <circle cx="4" cy="4" r="1.6" fill="#182338" opacity="0.32" />
          </pattern>
          <clipPath id="login-globe-clip">
            <circle cx="430" cy="430" r="238" />
          </clipPath>
        </defs>

        <g clipPath="url(#login-globe-clip)">
          <rect x="192" y="192" width="476" height="476" fill="url(#login-motif-dots)" />
          <g stroke="#182338" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill="#F6F7F9">
            <path d="M300 300c34-26 84-30 112-12 20 13 14 38-6 48-26 13-20 40-44 52-22 11-52-2-62-26-9-21-18-44 0-62Z" />
            <path d="M430 480c26-10 58 0 64 24 5 22-14 44-38 42-22-2-38-22-36-42 1-10 4-19 10-24Z" />
            <path d="M500 330c30-16 72-12 90 10 14 18 2 42-22 48-28 7-62-6-72-28-5-11-3-23 4-30Z" />
          </g>
        </g>
        <circle cx="430" cy="430" r="238" stroke="#182338" strokeWidth="1.6" fill="none" />
        <ellipse cx="430" cy="430" rx="238" ry="96" stroke="#182338" strokeWidth="1.6" fill="none" opacity="0.45" />
        <path d="M430 192v476" stroke="#182338" strokeWidth="1.6" opacity="0.3" />

        <g stroke="#182338" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="880" cy="640" rx="118" ry="30" />
          <path d="M762 640v-44M998 640v-44" opacity="0.8" />
          <ellipse cx="880" cy="596" rx="118" ry="30" />
          <path d="M774 566v-40M986 566v-40" opacity="0.8" />
          <ellipse cx="880" cy="526" rx="106" ry="27" />
          <path d="M788 500v-38M972 500v-38" opacity="0.8" />
          <ellipse cx="880" cy="462" rx="92" ry="24" />
        </g>
        <g stroke="#2E46E0" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="880" cy="392" r="42" />
          <path d="M880 350l22 24-21 20-23-19 22-25Z" opacity="0.85" />
        </g>

        {/* 手绘 K 线：循环生长（6s 绘制 → 3s 停留 → 1s 淡出） */}
        <path
          className="login-kline-loop"
          d="M150 640 300 560l96 44 120-110 108 56 130-96 120 40 76-60 74-54"
          pathLength={1000}
          stroke="#2E46E0"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <g stroke="#2E46E0" strokeWidth="1.6">
          <path d="M486 512v-20M486 556v16" />
          <rect x="476" y="492" width="20" height="64" rx="3" fill="#F6F7F9" />
          <path d="M716 452v-18M716 508v14" />
          <rect x="706" y="434" width="20" height="74" rx="3" fill="#2E46E0" fillOpacity="0.14" />
        </g>
        {/* 末端 up 绿点缀 + 拖尾涟漪 */}
        <path d="M1074 340l-2 22M1074 340l-20 6" stroke="#0E9F6E" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="1074" cy="340" r="5" fill="#0E9F6E" />
        <circle className="login-kline-blip" cx="1074" cy="340" r="14" fill="none" stroke="#0E9F6E" strokeWidth="1.6" />

        <g stroke="#182338" strokeWidth="1.6" strokeLinecap="round" opacity="0.5">
          <path d="M150 760h120M150 778h72" />
        </g>
      </svg>
    </div>
  );
}

/* ---------------- 逐字入场大标 ---------------- */
function CharStagger({ text, className, delayBase = 0 }: { text: string; className?: string; delayBase?: number }) {
  return (
    <span className={className} aria-label={text} role="text">
      {Array.from(text).map((ch, i) => (
        <motion.span
          key={`${ch}-${i}`}
          aria-hidden="true"
          className="inline-block"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1], delay: delayBase + i * 0.03 }}
        >
          {ch}
        </motion.span>
      ))}
    </span>
  );
}

const FEATURES: { icon: IconName; title: string; desc: string }[] = [
  { icon: 'radar', title: t('突破雷达'), desc: t('价格越界的瞬间，已经在你的雷达上。') },
  { icon: 'layers', title: t('板块透视'), desc: t('热力、强度、IV 排名，一屏定位资金方向。') },
  { icon: 'spark-ai', title: t('财报 AI'), desc: t('财报落地前，先看清涟漪往哪传。') },
];

/* ---------------- 眼睛切换（手绘细线，与图标库同工艺） ---------------- */
function EyeIcon({ off }: { off?: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.8 12S6.2 5.8 12 5.8 21.2 12 21.2 12 17.8 18.2 12 18.2 2.8 12 2.8 12Z" />
      <circle cx="12" cy="12" r="2.6" />
      {off && <path d="M4.5 19.5 19.5 4.5" />}
    </svg>
  );
}

/* ---------------- 页面 ---------------- */
type SubmitState = 'idle' | 'verifying' | 'success' | 'error';

export default function Login() {
  const navigate = useNavigate();
  const { login, register, isOwner, isCustomer, loading } = useAccess();
  const toast = useToast();
  const reduced = useReducedMotion();

  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [state, setState] = useState<SubmitState>('idle');
  const [statusMsg, setStatusMsg] = useState<{ text: string; tone: 'error' | 'warn' } | null>(null);
  const [shake, setShake] = useState(false);
  const [pwError, setPwError] = useState(false);
  const [serviceDown, setServiceDown] = useState(false);

  /* 已登录（管理员或客户）→ 直接进工作台（无闪烁） */
  useEffect(() => {
    if (!loading && (isOwner || isCustomer)) navigate('/watchlist', { replace: true });
  }, [loading, isOwner, isCustomer, navigate]);

  /* access/status 失败 → 顶部警告条 + 禁用 */
  useEffect(() => {
    let alive = true;
    accessApi.status().catch(() => {
      if (alive) setServiceDown(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  /* 页面不可见时暂停 K 线循环 */
  useEffect(() => {
    const root = document.getElementById('login-root');
    if (!root) return;
    const onVis = () => root.classList.toggle('login-paused', document.visibilityState !== 'visible');
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  const doShake = () => {
    setShake(true);
    window.setTimeout(() => setShake(false), 420);
  };

  /* 账号相关的错误后端已给出中文说明，直接用；其余保持原有映射。 */
  const ACCOUNT_ERROR_CODES = new Set([
    'username_required',
    'username_too_long',
    'username_invalid_characters',
    'username_reserved',
    'username_taken',
    'password_required',
    'password_too_long',
    'password_invalid_characters',
    'registration_closed',
    'invalid_credentials',
  ]);

  const mapError = (e: unknown): { text: string; tone: 'error' | 'warn' } => {
    if (e instanceof ApiError) {
      if (e.bizCode === 'login_cooldown') return { text: t('连续登录失败，请稍后再试'), tone: 'warn' };
      if (e.bizCode === 'registration_rate_limited') {
        return { text: e.message || t('注册过于频繁，请稍后再试'), tone: 'warn' };
      }
      if (e.bizCode === 'https_required') return { text: e.message || t('登录需要 HTTPS'), tone: 'warn' };
      if (e.bizCode && ACCOUNT_ERROR_CODES.has(e.bizCode)) {
        return { text: e.message || t('用户名或密码不正确'), tone: 'error' };
      }
      if (e.code === 429 || e.code >= 500) return { text: t('服务暂时不可用，稍后重试'), tone: 'warn' };
    }
    return { text: mode === 'register' ? t('注册失败，请重试') : t('用户名或密码不正确'), tone: 'error' };
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (state === 'verifying' || state === 'success' || serviceDown) return;
    if (!username.trim()) {
      setStatusMsg({ text: t('请输入用户名'), tone: 'error' });
      doShake();
      return;
    }
    if (!password.trim()) {
      setStatusMsg({ text: t('请输入密码'), tone: 'error' });
      setPwError(true);
      doShake();
      window.setTimeout(() => setPwError(false), 1200);
      return;
    }
    setState('verifying');
    setStatusMsg(null);
    try {
      const name = username.trim();
      if (mode === 'register') await register(name, password);
      else await login(name, password);
      setState('success');
      const isAdmin = name.toLowerCase() === 'admin';
      toast.success(
        mode === 'register' ? t('账号已创建') : t('欢迎回来'),
        isAdmin ? t('管理员已登录') : t('已登录 {name}', { name }),
      );
      navigate('/watchlist', { replace: true });
    } catch (err) {
      setState('error');
      setStatusMsg(mapError(err));
      setPwError(true);
      doShake();
      window.setTimeout(() => setPwError(false), 1200);
    }
  };

  if (loading || isOwner || isCustomer) {
    return (
      <div className="dot-grid-dense flex min-h-[100dvh] items-center justify-center bg-paper">
        <div className="size-8 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-label={t("加载中")} />
      </div>
    );
  }

  return (
    <div id="login-root" className="dot-grid-dense relative min-h-[100dvh] overflow-hidden bg-paper">
      {/* v8 清新：顶部右侧极淡 pastel 群青晕染（Tally 感，不动结构） */}
      <div
        className="pointer-events-none absolute inset-0"
        aria-hidden="true"
        style={{ background: 'radial-gradient(60% 40% at 85% 0%, rgba(46,70,224,.05), transparent 70%)' }}
      />
      {/* L0 主视觉层：桌面右下 40% / 移动顶部裁切 45% */}
      <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-[52%] items-end justify-end opacity-90 lg:flex" aria-hidden="true">
        <LoginMotif className="w-[min(720px,100%)] translate-y-[6%]" />
      </div>
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[45dvh] overflow-hidden opacity-50 lg:hidden" aria-hidden="true">
        <LoginMotif className="mx-auto w-[560px] max-w-none" />
      </div>

      <div className="relative z-10 mx-auto grid min-h-[100dvh] w-full max-w-shell grid-cols-1 items-center gap-10 px-6 py-12 lg:grid-cols-12 lg:gap-12 lg:px-16">
        {/* L1 左侧编辑区 */}
        <div className="lg:col-span-7">
          <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
            className="flex items-center gap-3"
          >
            <img src="/logo.svg" alt="" className="size-10" />
            <div>
              <p className="font-display text-[20px] font-bold leading-[24px] text-ink-900">Optix Pro</p>
              <p className="eyebrow mt-0.5">US EQUITY RESEARCH DESK</p>
            </div>
          </motion.div>

          <h1 className="mt-10 font-display text-[40px] leading-[46px] font-bold text-ink-900 lg:text-display-xl">
            <CharStagger text={t("把")} delayBase={0.1} />
            <span className="marker"><CharStagger text={t("市场")} delayBase={0.13} /></span>
            <br />
            <CharStagger text={t("讲给你听。")} className="text-brand-600" delayBase={0.22} />
          </h1>

          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1], delay: 0.3 }}
            className="mt-5 max-w-[460px] text-[15px] leading-[26px] text-ink-600 max-lg:line-clamp-2"
          >
            {t('突破雷达、强度选股、板块透视、财报 AI、新闻催化剂 —— 一套终端，看懂今晚的美股。数据延迟 15 分钟，仅供研究参考。')}
          </motion.p>

          {/* 特性三行（移动：横滑 chips） */}
          <div className="no-scrollbar mt-8 flex gap-6 max-lg:overflow-x-auto max-lg:pb-1 lg:flex-col lg:gap-5">
            {FEATURES.map((f, i) => (
              <motion.div
                key={f.title}
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: 0.42 + i * 0.12 }}
                className="flex items-start gap-3 max-lg:min-w-[220px] max-lg:rounded-lg max-lg:border max-lg:border-line max-lg:bg-card/80 max-lg:p-3"
              >
                <span className="mt-0.5 text-brand-600">
                  <Icon name={f.icon} size={20} />
                </span>
                <div>
                  <p className="font-display text-[15px] font-semibold text-ink-900">{f.title}</p>
                  <p className="mt-0.5 text-caption text-ink-500">{f.desc}</p>
                </div>
              </motion.div>
            ))}
          </div>

          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.56, delay: 0.9 }}
            className="mt-10 border-t border-line pt-4 text-caption text-ink-400"
          >
            {t('交互研究版 · 延迟行情 · 不构成投资建议 ◆')}
          </motion.p>
        </div>

        {/* L2 右侧登录卡 */}
        <div className="flex justify-center lg:col-span-5">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: reduced ? 0 : 0.2 }}
            className={cn(
              'glass w-full max-w-[400px] rounded-xl border border-line p-9 shadow-sh-3 max-lg:p-6',
              shake && 'animate-nudge-shake',
            )}
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 text-brand-600">
                <Icon name="command" size={20} />
              </span>
              <div>
                <h2 className="font-display text-[22px] font-semibold leading-[28px] text-ink-900">{t('进入终端')}</h2>
                <p className="mt-0.5 text-caption text-ink-400">{t('登录后自选股保存在账号里 · 访客可只读浏览')}</p>
              </div>
            </div>

            {/* 登录 / 注册切换：滑动指示条，沿用页面既有动效曲线 */}
            <div className="mt-5 grid grid-cols-2 rounded-sm border border-line-strong bg-card p-1">
              {(['login', 'register'] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setMode(value);
                    setStatusMsg(null);
                  }}
                  aria-pressed={mode === value}
                  className={cn(
                    'relative h-8 rounded-xs text-caption font-medium transition-colors duration-fast',
                    mode === value ? 'text-white' : 'text-ink-500 hover:text-ink-800',
                  )}
                >
                  {mode === value && (
                    <motion.span
                      layoutId="login-mode-pill"
                      className="absolute inset-0 rounded-xs bg-brand-600"
                      transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
                    />
                  )}
                  <span className="relative">{value === 'login' ? t('登录') : t('注册')}</span>
                </button>
              ))}
            </div>

            {serviceDown && (
              <p role="status" className="mt-4 rounded-xs border border-warn-600/30 bg-warn-50 px-2.5 py-1.5 text-caption text-warn-600">
                {t('无法连接服务，登录暂不可用')}
              </p>
            )}

            <form onSubmit={onSubmit} className="mt-5" noValidate>
              <label className="mb-4 block">
                <span className="mb-1.5 block text-caption font-medium text-ink-500">{t('用户名')}</span>
                <div
                  className={cn(
                    'flex h-12 items-center gap-2 rounded-sm border border-line-strong bg-card px-3',
                    'transition-[box-shadow,border-color] duration-fast',
                    'focus-within:border-brand-600 focus-within:shadow-focus-ring',
                  )}
                >
                  <Icon name="command" size={16} className="shrink-0 text-ink-400" />
                  <input
                    type="text"
                    value={username}
                    disabled={serviceDown || state === 'verifying' || state === 'success'}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder={mode === 'register' ? t('起一个用户名') : t('用户名')}
                    maxLength={32}
                    className="h-full min-w-0 flex-1 bg-transparent text-[16px] text-ink-800 outline-none placeholder:text-ink-300 disabled:opacity-60"
                    autoComplete="username"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    aria-label={t("用户名")}
                  />
                </div>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-caption font-medium text-ink-500">{t('密码')}</span>
                <div
                  className={cn(
                    'flex h-12 items-center gap-2 rounded-sm border bg-card px-3 transition-[box-shadow,border-color] duration-fast',
                    'focus-within:border-brand-600 focus-within:shadow-focus-ring',
                    pwError ? 'border-down-600' : 'border-line-strong',
                  )}
                >
                  <Icon name="shield" size={16} className="shrink-0 text-ink-400" />
                  <input
                    type={showPw ? 'text' : 'password'}
                    value={password}
                    disabled={serviceDown || state === 'verifying' || state === 'success'}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                    onKeyUp={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                    placeholder={mode === 'register' ? t('设置密码') : t('输入密码')}
                    className="h-full min-w-0 flex-1 bg-transparent font-mono text-[16px] text-ink-800 outline-none placeholder:text-ink-300 disabled:opacity-60"
                    autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                    aria-label={t("密码")}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPw((v) => !v)}
                    className="shrink-0 rounded-xs p-1 text-ink-400 transition-colors hover:text-ink-600"
                    aria-label={showPw ? t('隐藏密码') : t('显示密码')}
                  >
                    <EyeIcon off={!showPw} />
                  </button>
                </div>
              </label>
              <p className={cn('mt-1.5 h-4 text-caption text-warn-600 transition-opacity', capsLock ? 'opacity-100' : 'opacity-0')}>
                {t('Caps Lock 已开启')}
              </p>

              <button
                type="submit"
                disabled={serviceDown || state === 'verifying' || state === 'success'}
                className={cn(
                  'flex h-12 w-full items-center justify-center gap-2 rounded-md font-mono text-[14px] tracking-[0.02em] text-white shadow-sh-1',
                  'transition-[transform,filter,background-color] duration-fast',
                  state === 'success'
                    ? 'bg-up-600'
                    : 'bg-brand-600 hover:-translate-y-px hover:brightness-[1.06] active:translate-y-0 active:brightness-95 active:duration-instant',
                  'disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:translate-y-0',
                )}
              >
                {state === 'verifying' ? (
                  <>
                    <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true" />
                    {t('验证中…')}
                  </>
                ) : state === 'success' ? (
                  <>
                    <Icon name="check" size={16} strokeWidth={1.45} />
                    {mode === 'register' ? t('已创建') : t('验证通过')}
                  </>
                ) : mode === 'register' ? (
                  t('注册并登录')
                ) : (
                  t('登录')
                )}
              </button>

              {/* 状态行（预留 20px 避免跳动） */}
              {/* 常驻 live region（审计 2.5.11）：节点先于内容存在，冷却/HTTPS/
                  服务不可用等 warn 语调的提示也会被播报；在已存在节点上动态
                  挂 role=alert 往往不触发播报，所以 role 恒为 status、错误
                  升为 assertive。 */}
              <p
                className={cn(
                  'mt-3 h-5 text-caption',
                  statusMsg?.tone === 'warn' ? 'text-warn-600' : 'text-down-700',
                  !statusMsg && 'opacity-0',
                )}
                role="status"
                aria-live={statusMsg?.tone === 'error' ? 'assertive' : 'polite'}
              >
                {statusMsg ? statusMsg.text : '·'}
              </p>
            </form>

            {/* 访客入口 */}
            <div className="mt-2 flex items-center gap-3">
              <span className="h-px flex-1 bg-line" />
              <span className="text-caption text-ink-400">{t('或')}</span>
              <span className="h-px flex-1 bg-line" />
            </div>
            <button
              onClick={() => navigate('/watchlist')}
              className="mt-4 flex h-11 w-full items-center justify-center gap-2 rounded-md border border-line-strong bg-transparent text-body-s font-medium text-ink-600 transition-colors duration-fast hover:bg-paper-2 hover:text-ink-800"
            >
              {t('以访客身份浏览（只读）')}
            </button>

            <p className="mt-5 text-center text-micro leading-[18px] text-ink-400">
              {mode === 'register'
                ? t('账号只用于保存你的自选股，不改变数据权限')
                : t('登录即同意研究用途条款 · 登录状态保留 30 天')}
            </p>

            <div className="mt-4 border-t border-line pt-3 text-center">
              <Link
                to="/watchlist"
                className="inline-flex items-center gap-1 text-caption font-medium text-brand-600 transition-colors hover:text-brand-700"
              >
                <Icon name="chevron-right" size={13} className="rotate-180" />
                {t('返回公开研究页面')}
              </Link>
            </div>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
