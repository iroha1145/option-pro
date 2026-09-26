/**
 * ThinkingLabel —— 模型任务在途时的状态文字扫光（transitions.dev 15-shimmer-text；
 * beautifului ThinkingState、beUI thinking-shimmer 同一手法）。
 *
 * 只在任务真的在跑（live）时扫光：暂停、待确认、已请求取消这些「等人处理」的
 * 状态保持静止文字，否则一条停住的任务也显得在忙。扫光只动 ::before 上的渐变，
 * 文字本身不位移、不模糊，读数始终清楚；减少动态偏好下扫光关闭。
 */
import { cn } from '@/lib/utils';

export default function ThinkingLabel({
  children,
  live = true,
  tone = 'ai',
  className,
}: {
  /** 状态文字；同时作为扫光层（::before 的 attr(data-text)）的副本。 */
  children: string;
  live?: boolean;
  /** ai：青瓷底色（模型任务）；ink：墨色底（一般的进行中文案）。 */
  tone?: 'ai' | 'ink';
  className?: string;
}) {
  if (!live) return <span className={className}>{children}</span>;
  return (
    <span className={cn('t-shimmer', tone === 'ai' && 'is-ai', className)} data-text={children}>
      {children}
    </span>
  );
}
