/**
 * 2026-09-26 动效与按钮统一后，组件新增的几处共享依赖。沙箱编译（transpile + vm）
 * 的测试对未声明的依赖一律报错，这里集中给出它们：
 *   - '@/lib/motion' 用真实令牌（数值只有一份来源，测试不另抄一遍）；
 *   - '@/lib/boundedReadRetry'、'@/lib/retryDelay'、'@/api/aiJobNormalize' 用真实实现
 *     （轮询节奏、退避与任务原因码同理）；
 *   - 其余是纯展示件，桩成元素类型字符串，渲染树里照常保留 props.children，
 *     文本断言（textOf）读到的仍是按钮/状态行里的原文。
 */
import * as motionTokens from '../../src/lib/motion.ts';
import * as aiJobNormalize from '../../src/api/aiJobNormalize.ts';
import * as boundedReadRetry from '../../src/lib/boundedReadRetry.ts';
import * as retryDelay from '../../src/lib/retryDelay.ts';

export const SHARED_UI_STUBS = {
  '@/lib/motion': motionTokens,
  '@/api/aiJobNormalize': aiJobNormalize,
  '@/lib/boundedReadRetry': boundedReadRetry,
  '@/lib/retryDelay': retryDelay,
  '@/components/shared/Spinner': { default: 'Spinner' },
  '@/components/shared/IconSwap': { default: 'IconSwap', BusyIcon: 'BusyIcon' },
  '@/components/shared/TextSwap': { default: ({ children }) => children },
  '@/components/shared/ThinkingLabel': { default: 'ThinkingLabel' },
  '@/components/shared/CollapsePresence': { default: 'CollapsePresence' },
  '@/components/shared/AutoHeight': { default: 'AutoHeight' },
  // 2026-10-06：选股行展开的近半年走势块用涨跌徽标显示区间涨跌。
  '@/components/shared/ChangeBadge': { default: 'ChangeBadge' },
};
