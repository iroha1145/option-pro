export interface NewsAnalysisProgress {
  status: 'idle' | 'active' | 'completed';
  scope: 'latest_submission_batch';
  batchId: string | null;
  batchSource: 'manual' | 'scheduled' | null;
  total: number;
  finished: number;
  succeeded: number;
  awaitingValidation: number;
  rejected: number;
  failed: number;
  waiting: number;
  inProgress: number;
  cancelled: number;
  insufficientContext: number;
  budgetBlocked: number;
  progressPercent: number;
  currentIndex: number | null;
  currentNewsId: number | null;
  currentPhase: 'provider_queued' | 'provider_processing' | null;
  queueTotal: number;
  queueWaiting: number;
  queueInProgress: number;
  startedAt: string | null;
  lastUpdatedAt: string | null;
  asOf: string;
}

type Rec = Record<string, unknown>;

const asRecord = (value: unknown): Rec =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Rec)
    : {};

function requiredCount(record: Rec, key: string): number {
  const value = record[key];
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new Error(`新闻分析进度字段无效：${key}`);
  }
  return value;
}

function optionalPositiveInteger(record: Rec, key: string): number | null {
  const value = record[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) {
    throw new Error(`新闻分析进度字段无效：${key}`);
  }
  return value;
}

function optionalText(record: Rec, key: string): string | null {
  const value = record[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string' || !value) {
    throw new Error(`新闻分析进度字段无效：${key}`);
  }
  return value;
}

export function normalizeNewsAnalysisProgress(raw: unknown): NewsAnalysisProgress {
  const record = asRecord(raw);
  const status = record.status;
  if (status !== 'idle' && status !== 'active' && status !== 'completed') {
    throw new Error('新闻分析进度状态无效');
  }
  if (record.scope !== 'latest_submission_batch') {
    throw new Error('新闻分析进度统计范围无效');
  }
  const batchSource = record.batch_source;
  if (batchSource !== null && batchSource !== 'manual' && batchSource !== 'scheduled') {
    throw new Error('新闻分析进度任务来源无效');
  }

  const total = requiredCount(record, 'total');
  const finished = requiredCount(record, 'finished');
  const succeeded = requiredCount(record, 'succeeded');
  const awaitingValidation = requiredCount(record, 'awaiting_validation');
  const rejected = requiredCount(record, 'rejected');
  const failed = requiredCount(record, 'failed');
  const waiting = requiredCount(record, 'waiting');
  const inProgress = requiredCount(record, 'in_progress');
  const cancelled = requiredCount(record, 'cancelled');
  const insufficientContext = requiredCount(record, 'insufficient_context');
  const budgetBlocked = requiredCount(record, 'budget_blocked');
  const progressPercent = requiredCount(record, 'progress_percent');
  const queueTotal = requiredCount(record, 'queue_total');
  const queueWaiting = requiredCount(record, 'queue_waiting');
  const queueInProgress = requiredCount(record, 'queue_in_progress');
  const currentIndex = optionalPositiveInteger(record, 'current_index');
  const currentNewsId = optionalPositiveInteger(record, 'current_news_id');
  const currentPhase = record.current_phase;
  if (
    currentPhase !== null &&
    currentPhase !== 'provider_queued' &&
    currentPhase !== 'provider_processing'
  ) {
    throw new Error('新闻分析当前阶段无效');
  }

  if (finished + waiting + inProgress !== total) {
    throw new Error('新闻分析进度总数不一致');
  }
  if (
    succeeded +
      awaitingValidation +
      rejected +
      failed +
      cancelled +
      insufficientContext +
      budgetBlocked !==
    finished
  ) {
    throw new Error('新闻分析结束数不一致');
  }
  if (queueWaiting + queueInProgress !== queueTotal) {
    throw new Error('新闻分析队列数不一致');
  }
  const expectedPercent = total > 0 ? Math.round((finished * 100) / total) : 0;
  if (progressPercent !== expectedPercent || progressPercent > 100) {
    throw new Error('新闻分析百分比不一致');
  }
  if (
    (currentIndex !== null ||
      currentNewsId !== null ||
      currentPhase !== null) &&
    inProgress !== 1
  ) {
    throw new Error('新闻分析当前条目无法唯一确定');
  }
  if (
    inProgress === 1 &&
    (currentIndex === null || currentPhase === null)
  ) {
    throw new Error('新闻分析当前条目字段缺失');
  }
  if (currentIndex !== null && currentIndex > total) {
    throw new Error('新闻分析当前序号超出范围');
  }

  const asOf = record.as_of;
  if (typeof asOf !== 'string' || !asOf) {
    throw new Error('新闻分析进度更新时间无效');
  }
  return {
    status,
    scope: 'latest_submission_batch',
    batchId: optionalText(record, 'batch_id'),
    batchSource,
    total,
    finished,
    succeeded,
    awaitingValidation,
    rejected,
    failed,
    waiting,
    inProgress,
    cancelled,
    insufficientContext,
    budgetBlocked,
    progressPercent,
    currentIndex,
    currentNewsId,
    currentPhase,
    queueTotal,
    queueWaiting,
    queueInProgress,
    startedAt: optionalText(record, 'started_at'),
    lastUpdatedAt: optionalText(record, 'last_updated_at'),
    asOf,
  };
}
