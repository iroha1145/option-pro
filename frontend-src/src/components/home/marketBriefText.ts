/**
 * 「市场综合研判」卡的界面文字与纯函数：枚举标签、时段写法、失败原因、覆盖条，
 * 以及「上一份」「最近一次失败」「手动生成是否有结果」的判定。放在 .ts 里：契约测试
 * 直接载入这里核对枚举是否齐全，组件文件只导出组件。
 *
 * 模型写的正文（结论、要点、新闻标题、观察项）不经过这里，也不经 t()。
 */
import type {
  BriefConsistency,
  BriefMacroVerdict,
  BriefPricedIn,
  BriefRegime,
  BriefSectorChange,
  BriefSlot,
  BriefSufficiency,
  MarketBrief,
  MarketBriefAttempt,
  MarketBriefCoverage,
  MarketBriefLatest,
  MarketBriefNextSlot,
  MarketBriefTriggerResult,
} from '@/api/modules/marketBrief';
import type { BadgeTone } from '@/components/shared/SoftBadge';
import { fmtNyDayKey, fmtNyHHmm } from '@/lib/format';
import { t } from '../../i18n/core.ts';

/* --------------------------------- 枚举标签 --------------------------------- */

export const SLOT_LABEL: Record<BriefSlot, string> = {
  pre_open: t('开盘前'),
  post_close: t('收盘后'),
};

export const REGIME_LABEL: Record<BriefRegime, string> = {
  broad_advance: t('广泛走强'),
  narrow_leadership: t('权重股撑盘'),
  rotation: t('行业轮动'),
  risk_off: t('风险偏好收缩'),
  mixed: t('多空交织'),
  uncertain: t('判断待定'),
};

/** 只有方向明确的两档用涨跌色；其余是结构类别，用中性色（§6「类别不借语义色」）。 */
export const REGIME_TONE: Record<BriefRegime, BadgeTone> = {
  broad_advance: 'up',
  narrow_leadership: 'neutral',
  rotation: 'neutral',
  risk_off: 'down',
  mixed: 'neutral',
  uncertain: 'neutral',
};

/* 「低 / 中 / 高」单字词条的日文是价格的「安値 / 高値」，这里用整句词条。 */
export const SUFFICIENCY_LABEL: Record<BriefSufficiency, string> = {
  low: t('证据充分度：低'),
  medium: t('证据充分度：中'),
  high: t('证据充分度：高'),
};

export const BREADTH_LABEL: Record<BriefConsistency, string> = {
  confirms: t('一致'),
  diverges: t('背离'),
  mixed: t('部分一致'),
  unknown: t('不明'),
};

export const VERDICT_LABEL: Record<BriefMacroVerdict, string> = {
  supports: t('支持'),
  contradicts: t('反驳'),
  mixed: t('部分支持'),
  unknown: t('不明'),
};

export const CHANGE_LABEL: Record<BriefSectorChange, string> = {
  substantive: t('实质变化'),
  noise: t('噪音'),
  unknown: t('待定'),
};

export const PRICED_IN_LABEL: Record<BriefPricedIn, string> = {
  yes: t('已反映'),
  partly: t('部分反映'),
  no: t('未反映'),
  unclear: t('不明'),
};

/*
 * 判断标签的颜色：同一种含义在各段用同一个颜色。一致、支持用 ok（绿）；「部分……」用 warn（琥珀）；
 * 背离、反驳用 danger（红）；值得留意的实质变化、新闻尚未反映在价格里用 brand（蓝）；
 * 不明、待定、噪音、已反映用中性灰。这些不是价格方向，不用涨跌色——红涨绿跌模式下也不该互换。
 */
export const SUFFICIENCY_TONE: Record<BriefSufficiency, BadgeTone> = {
  low: 'warn',
  medium: 'neutral',
  high: 'ok',
};

export const BREADTH_TONE: Record<BriefConsistency, BadgeTone> = {
  confirms: 'ok',
  diverges: 'danger',
  mixed: 'warn',
  unknown: 'neutral',
};

export const VERDICT_TONE: Record<BriefMacroVerdict, BadgeTone> = {
  supports: 'ok',
  contradicts: 'danger',
  mixed: 'warn',
  unknown: 'neutral',
};

export const CHANGE_TONE: Record<BriefSectorChange, BadgeTone> = {
  substantive: 'brand',
  noise: 'neutral',
  unknown: 'neutral',
};

export const PRICED_IN_TONE: Record<BriefPricedIn, BadgeTone> = {
  yes: 'neutral',
  partly: 'warn',
  no: 'brand',
  unclear: 'neutral',
};

/* ------------------------------- 失败原因码 ------------------------------- */

/**
 * 运行失败的原因码（latest_attempt.error_code、手动生成被拒的 bizCode）→ 短句。
 * 运行失败码见 backend/app/services/market_brief/errors.py 的 RUN_ERROR_CODES。
 */
export const ATTEMPT_ERROR_TEXT: Record<string, string> = {
  provider_auth_failed: t('密钥无效'),
  provider_rate_limited: t('模型服务繁忙'),
  provider_request_rejected: t('模型服务拒绝了请求'),
  provider_server_error: t('模型服务故障'),
  provider_unavailable: t('无法连接'),
  provider_usage_incomplete: t('模型用量未完整确认，请勿重复提交'),
  submission_outcome_unknown: t('提交结果和费用尚未确认，请勿重复提交'),
  provider_stream_incomplete: t('模型回复未完整结束，未生成研判'),
  provider_invalid_tool_response: t('模型返回的内容不完整或格式有误，未生成研判'),
  provider_refusal: t('模型拒绝了本次请求'),
  output_truncated: t('输出被截断'),
  output_not_json: t('输出格式错误'),
  unexpected_stop_reason: t('模型意外停止'),
  continuation_limit: t('续写次数已用完'),
  schema_validation_failed: t('输出未通过校验'),
  evidence_unavailable: t('证据不足未生成'),
  budget_exceeded: t('本次研判的输出用量已达上限'),
  run_deadline_exceeded: t('超出运行时长'),
  runtime_error: t('程序出错'),
  anthropic_api_key_missing: t('服务器未配置模型密钥'),
  daily_budget_usd_reached: t('共享模型日预算不足，东京 09:00 重置后再试'),
  daily_run_limit_reached: t('今日次数已用完'),
  market_brief_in_progress: t('研判正在生成，请等待结果'),
};

/** 认识的码给短句，带后缀的（provider_refusal:cyber）按前缀认；其余显示原码。 */
export function attemptErrorText(code: string | null | undefined): string {
  const raw = String(code ?? '').trim();
  if (!raw) return t('原因未知');
  const base = raw.split(/[:(\s]/, 1)[0];
  return ATTEMPT_ERROR_TEXT[raw] ?? ATTEMPT_ERROR_TEXT[base] ?? raw;
}

const WORKER_DOWN = t('后台服务暂不可用，请稍后重试。');

/** POST /runs 拒绝受理的原因码（backend/app/api/market_brief.py）。 */
export const TRIGGER_REFUSAL_TEXT: Record<string, string> = {
  shared_budget_unavailable: t('共享预算暂时无法核对，请稍后重试'),
  market_brief_disabled: t('研判功能未启用'),
  worker_task_disabled: t('后台研判任务已停用'),
  worker_unavailable: WORKER_DOWN,
  worker_state_unavailable: WORKER_DOWN,
  worker_task_unavailable: WORKER_DOWN,
};

/** 「现在生成」被拒时的提示。 */
export function triggerFailureText(
  error: { code?: number; bizCode?: string; message?: string } | null | undefined,
): string {
  const code = error?.bizCode ?? '';
  const known = ATTEMPT_ERROR_TEXT[code] ?? TRIGGER_REFUSAL_TEXT[code];
  if (known) return known;
  if (error?.code === 429) return t('请求过于频繁，请稍后再试');
  return error?.message || t('请求未成功');
}

/**
 * POST /runs 受理之后怎么办：follow 为是就开始跟进 latest；title 非空时弹一条提示。
 * 冷却中（200 + reason=cooldown）没有排上队，不跟进；同一分钟的重复提交若那次已结束，
 * 也不跟进，只重读一次。
 */
export function triggerReply(
  result: MarketBriefTriggerResult,
  nowMs: number,
): { follow: boolean; refresh: boolean; title: string | null; description: string | null } {
  if (result.reason === 'cooldown' || result.errorCode === 'market_brief_cooldown') {
    const until = result.cooldownUntil ? Date.parse(result.cooldownUntil) : Number.NaN;
    const minutes = Number.isFinite(until) ? Math.max(1, Math.ceil((until - nowMs) / 60_000)) : null;
    return {
      follow: false,
      refresh: false,
      title: t('手动生成冷却中'),
      description: minutes === null ? null : t('约 {n} 分钟后可再次生成', { n: minutes }),
    };
  }
  if (result.reason === 'already_running' || result.errorCode === 'market_brief_in_progress') {
    return { follow: true, refresh: false, title: t('研判正在生成'), description: t('完成后自动显示') };
  }
  if (result.reason === 'idempotent' && result.status !== 'queued' && result.status !== 'running') {
    return { follow: false, refresh: true, title: null, description: null };
  }
  return { follow: true, refresh: false, title: null, description: null };
}

/* --------------------------------- 时间写法 --------------------------------- */

const ET_CLOCK = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** 美东挂钟：日期（YYYY-MM-DD）、当天分钟数、星期（0 = 周日）。 */
export function etClock(ms: number): { day: string; minutes: number; weekday: number } | null {
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(ET_CLOCK.formatToParts(date).map((part) => [part.type, part.value]));
  return {
    day: `${parts.year}-${parts.month}-${parts.day}`,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
    weekday: WEEKDAYS.indexOf(parts.weekday),
  };
}

const DAY_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** 'YYYY-MM-DD' → 'MM-DD'；与当前年份不同时保留年份。按字符串切，不经 Date，避免跨时区串日。 */
export function shortDate(day: string | null | undefined, year?: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day ?? '');
  if (!m) return '—';
  return year && m[1] !== year ? `${m[1]}-${m[2]}-${m[3]}` : `${m[2]}-${m[3]}`;
}

/** 「收盘后 · 10-08」：交易日取 trading_date，不取生成时间。 */
export function slotTag(slot: BriefSlot | null, day: string | null, year?: string): string {
  return `${slot ? SLOT_LABEL[slot] : '—'} · ${shortDate(day, year)}`;
}

/** 「生成于 美东 23:41」；生成日与交易日不同（次日补发）时带上日期。 */
export function generatedText(iso: string | null, tradingDate: string | null, year?: string): string | null {
  if (!iso) return null;
  const day = fmtNyDayKey(iso);
  if (!day) return null;
  const time = fmtNyHHmm(iso);
  return t('生成于 纽约时间 {time}', { time: day === tradingDate ? time : `${shortDate(day, year)} ${time}` });
}

/** 「下一个时段：开盘前 · 10-09 美东 08:40」。 */
export function nextSlotText(next: MarketBriefNextSlot | null, year?: string): string | null {
  if (!next) return null;
  const day = fmtNyDayKey(next.at);
  if (!day) return null;
  return t('下一个时段：{slot} 纽约时间 {time}', { slot: slotTag(next.slot, day, year), time: fmtNyHHmm(next.at) });
}

/* ---------------------------------- 覆盖条 ---------------------------------- */

const BREADTH_BASIS_LABEL: Record<string, string> = {
  sector_etf_proxy_11: t('11 只行业 ETF 代理'),
};

/** 证据块名（coverage.missing_blocks，对应 evidence.py 的 _SOURCES）。认不出的块名原样显示。 */
export const BLOCK_LABEL: Record<string, string> = {
  indices: t('指数'),
  market_signals: t('市场信号'),
  market_regime: t('走势评分'),
  breadth_counts: t('全市场扫描'),
  themes: t('主题'),
  sector_iv: t('行业波动率快照'),
  breakouts: t('突破雷达'),
  macro: t('宏观'),
  news: t('新闻'),
  earnings: t('财报'),
  economic_calendar: t('经济日历'),
  prior_brief: t('上一份研判'),
};

/**
 * 数据截止（coverage.data_through）的来源名，顺序兼作同一时刻的先后：全市场扫描
 * 最能说明这份研判的数据新旧，排最前。没有登记的来源不进覆盖条。
 */
export const THROUGH_LABEL: Record<string, string> = {
  eod_batch: t('全市场扫描截至'),
  macro: t('宏观截至'),
  market_regime: t('走势评分截至'),
  market_signals: t('市场信号截至'),
  indices: t('指数截至'),
  sector_iv: t('行业波动率截至'),
  breakouts: t('突破雷达截至'),
  news: t('新闻截至'),
  earnings: t('财报截至'),
  calendar: t('经济日历截至'),
};
const THROUGH_ORDER = Object.keys(THROUGH_LABEL);

/** 只列最早的两项：最旧的来源决定这份研判最多能说到哪天。 */
const THROUGH_SHOWN = 2;

export interface CoverageItem {
  key: string;
  label: string;
  value: string;
  warn?: boolean;
}

/** 日期按字符串原样取（日线批次、宏观快照只有日期），ISO 时刻换成美东。 */
function throughStamp(raw: string, year?: string): { sortKey: string; text: string } | null {
  if (DAY_ONLY.test(raw)) return { sortKey: `${raw} 00:00`, text: shortDate(raw, year) };
  const day = fmtNyDayKey(raw);
  if (!day) return null;
  const time = fmtNyHHmm(raw);
  return { sortKey: `${day} ${time}`, text: `${shortDate(day, year)} ${time}` };
}

const fmtCount = (value: number) => value.toLocaleString('en-US');

export function coverageItems(coverage: MarketBriefCoverage, year?: string): CoverageItem[] {
  const items: CoverageItem[] = [];
  if (coverage.universeSize !== null) {
    items.push({ key: 'universe', label: t('股票范围'), value: fmtCount(coverage.universeSize) });
  }
  if (coverage.scoredCount !== null) {
    items.push({ key: 'scored', label: t('已评分'), value: fmtCount(coverage.scoredCount) });
  }
  if (coverage.breadthBasis) {
    items.push({
      key: 'basis',
      label: t('广度口径'),
      value: BREADTH_BASIS_LABEL[coverage.breadthBasis] ?? coverage.breadthBasis,
    });
  }
  const through = Object.entries(coverage.dataThrough)
    .flatMap(([block, raw]) => {
      const label = THROUGH_LABEL[block];
      const stamp = label ? throughStamp(raw, year) : null;
      return label && stamp ? [{ block, label, ...stamp }] : [];
    })
    .sort((a, b) => a.sortKey.localeCompare(b.sortKey) || THROUGH_ORDER.indexOf(a.block) - THROUGH_ORDER.indexOf(b.block))
    .slice(0, THROUGH_SHOWN);
  for (const item of through) {
    items.push({ key: `through-${item.block}`, label: item.label, value: item.text });
  }
  if (coverage.missingBlocks.length > 0) {
    items.push({
      key: 'missing',
      label: t('缺'),
      value: coverage.missingBlocks.map((item) => BLOCK_LABEL[item.block] ?? item.block).join(t('、')),
      warn: true,
    });
  }
  return items;
}

/* ---------------------------------- 判定 ---------------------------------- */

/** 开盘前那份默认 08:40 生成，留 20 分钟余量后才把昨天的研判标成「上一份」。 */
const PRE_OPEN_EXPECTED_MINUTES = 9 * 60;
/** 收盘后那份最早 13:45（半日市），这之前仍在交易时段内。 */
const SESSION_END_MINUTES = 16 * 60;

/**
 * 显示的研判是否早于现在应有的最新一份：工作日美东 09:00 之后，交易日仍不是今天。
 *
 * 周末不标（周五收盘后那份就是最新的）。节假日用 next_slot 排除：交易时段内若下一个
 * 时段已经是改天的开盘前，说明今天不开市。节假日收盘后的时段仍会误标，概率低，接受。
 */
export function isPreviousBrief(
  brief: Pick<MarketBrief, 'tradingDate'>,
  nextSlot: MarketBriefNextSlot | null,
  nowMs: number,
): boolean {
  const clock = etClock(nowMs);
  if (!clock || !brief.tradingDate) return false;
  if (clock.weekday === 0 || clock.weekday === 6) return false;
  if (brief.tradingDate >= clock.day || clock.minutes < PRE_OPEN_EXPECTED_MINUTES) return false;
  const nextDay = nextSlot ? fmtNyDayKey(nextSlot.at) : null;
  const closedToday =
    nextSlot?.slot === 'pre_open' && nextDay !== null && nextDay > clock.day && clock.minutes < SESSION_END_MINUTES;
  return !closedToday;
}

/**
 * latest_attempt 在公开投影里只记失败的运行（store.py 的 latest_public），不带 status；
 * 带了 status 且是成功或进行中的，才不算失败。
 */
const NOT_FAILED_STATUSES = new Set(['completed', 'queued', 'running']);

export function attemptFailed(attempt: MarketBriefAttempt): boolean {
  return attempt.errorCode !== null || attempt.status === null || !NOT_FAILED_STATUSES.has(attempt.status);
}

function slotOrder(day: string | null, slot: BriefSlot | null): string {
  return `${day ?? ''}|${slot === 'post_close' ? 1 : 0}`;
}

/** 比当前研判更新的那次失败运行；没有失败、或失败早于当前研判时为 null。 */
export function failedAttemptAfter(
  attempt: MarketBriefAttempt | null,
  brief: MarketBrief | null,
): MarketBriefAttempt | null {
  if (!attempt || !attemptFailed(attempt)) return null;
  if (!brief) return attempt;
  if (attempt.runId && attempt.runId === brief.runId) return null;
  const at = attempt.at ? Date.parse(attempt.at) : Number.NaN;
  const generated = brief.generatedAt ? Date.parse(brief.generatedAt) : Number.NaN;
  if (Number.isFinite(at) && Number.isFinite(generated)) return at > generated ? attempt : null;
  return slotOrder(attempt.tradingDate, attempt.slot) >= slotOrder(brief.tradingDate, brief.slot) ? attempt : null;
}

/** 「最近一次 开盘前 · 10-09 生成失败：密钥无效」。 */
export function attemptFailureText(attempt: MarketBriefAttempt, year?: string): string {
  const reason = attemptErrorText(attempt.errorCode);
  if (!attempt.slot || !attempt.tradingDate) return t('最近一次生成失败：{reason}', { reason });
  return t('最近一次 {slot} 生成失败：{reason}', { slot: slotTag(attempt.slot, attempt.tradingDate, year), reason });
}

/* ------------------------------ 手动生成的跟进 ------------------------------ */

export interface FollowBaseline {
  brief: string | null;
  attempt: string | null;
  /** 点击时刻（毫秒）：早于它的研判或失败记录不算这次的结果。 */
  since: number;
}

/** 服务器与浏览器的时钟误差余量。 */
const CLOCK_SKEW_MS = 10 * 60_000;

function briefKey(latest: MarketBriefLatest | null): string | null {
  const brief = latest?.brief;
  return brief ? brief.runId ?? brief.generatedAt ?? '' : null;
}

function attemptKey(latest: MarketBriefLatest | null): string | null {
  const attempt = latest?.latestAttempt;
  return attempt ? [attempt.runId, attempt.at, attempt.status, attempt.errorCode].join('|') : null;
}

/** 生成时间明显早于点击的不是这次的结果；没有时间时无从判断，照常算。 */
function notBefore(iso: string | null, since: number): boolean {
  const at = iso ? Date.parse(iso) : Number.NaN;
  return !Number.isFinite(at) || at >= since - CLOCK_SKEW_MS;
}

/** 点「现在生成」那一刻的读数与时刻，用来判断之后有没有出结果。 */
export function followBaseline(latest: MarketBriefLatest | null, since: number): FollowBaseline {
  return { brief: briefKey(latest), attempt: attemptKey(latest), since };
}

/**
 * 跟进结果：出现新研判（run_id 变了）为 ready；出现新的失败运行为 failed；
 * 其余（包括后端先写一条进行中的记录）继续等。点击时读不到数据、之后才读到早就
 * 存在的研判，按生成时间排除，不误报成新结果。
 */
export function followOutcome(baseline: FollowBaseline, latest: MarketBriefLatest | null): 'ready' | 'failed' | null {
  if (!latest) return null;
  const brief = briefKey(latest);
  if (brief !== null && brief !== baseline.brief && notBefore(latest.brief?.generatedAt ?? null, baseline.since)) {
    return 'ready';
  }
  const attempt = latest.latestAttempt;
  if (attempt && attemptKey(latest) !== baseline.attempt && attemptFailed(attempt) && notBefore(attempt.at, baseline.since)) {
    return 'failed';
  }
  return null;
}
