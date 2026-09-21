import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../../api/client.ts';
import { getSecurityDiagnostics } from '../../api/modules/strengthDiagnostics.ts';
import {
  DIAGNOSTIC_FACTORS,
  diagnosticAtrPolicy,
  diagnosticAtrReference,
  diagnosticDataStatus,
  diagnosticCoverageStatus,
  diagnosticDisplayReason,
  diagnosticFamily,
  diagnosticNumber,
  diagnosticPercent,
  diagnosticReason,
  diagnosticRecord,
  diagnosticTheme,
  diagnosticTrack,
  pathStatus,
  type DiagnosticProfile,
  type DiagnosticRecord,
  type DiagnosticTimeframe,
  type SecurityDiagnostic,
  type SecurityDiagnosticPath,
} from '../../lib/eodDiagnostics.ts';
import { t } from '../../i18n/core.ts';

export interface SecurityDiagnosticsProps {
  profile: DiagnosticProfile;
  timeframe: DiagnosticTimeframe;
  publicationKey?: string | number | null;
  ticker?: string;
  onOpenDetail?: (ticker: string) => void;
}

type DiagnosticResult = { key: string; payload: SecurityDiagnostic };
type DiagnosticFailure = { key: string; message: string };

const TICKER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9.-]{0,31}$/;
const factorNames: Record<string, string> = {
  T: t('趋势'), M: t('动量'), S: t('结构'), R: t('稳定性'),
  B: t('突破'), P: t('趋势回调'), V: t('成交量'), G: t('行业相对强度'),
};
const gateNames: Record<string, string> = {
  venue: t('证券资格'), currently_tradable: t('可交易'), price: t('价格'),
  adv20: t('二十日平均成交额'), atr: t('波动幅度'), extension: t('价格延伸'),
  structure: t('结构'), history: t('历史长度'), upthrust: t('冲高回落'),
  invalidation: t('形态失效'), score: t('分数'), coverage: t('因子覆盖'),
};

function textValue(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : t('未提供');
}

function money(value: unknown, maximumFractionDigits = 0): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `$${new Intl.NumberFormat(undefined, { minimumFractionDigits: maximumFractionDigits, maximumFractionDigits }).format(value)}`
    : t('未提供');
}

function gateText(value: unknown): string {
  if (value === true) return t('通过');
  if (value === false) return t('未通过');
  return t('未核实');
}

function verifiedText(value: unknown): string {
  return value === true ? t('已核实') : t('未核实');
}

function pctPoint(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : t('未提供');
}

function pathKey(path: SecurityDiagnosticPath, index: number): string {
  return `${path.theme_id ?? path.sector_context ?? ''}:${path.algorithm_id ?? ''}:${index}`;
}

function safeMap(value: unknown): DiagnosticRecord {
  return diagnosticRecord(value) ?? {};
}

function factorValue(value: unknown): string {
  return diagnosticNumber(value, 1);
}

function FactorDetails({ path, sources }: {
  path: SecurityDiagnosticPath;
  sources: Record<string, DiagnosticRecord>;
}) {
  const factors = safeMap(path.factors);
  const configured = safeMap(path.configured_weights);
  const track = safeMap(path.track_weights);
  const effective = safeMap(path.effective_weights);
  const components = safeMap(path.score_components);
  const scoreGate = safeMap(path.score_gate_checks);
  const source = typeof path.weight_provenance_id === 'string' ? sources[path.weight_provenance_id] : undefined;
  return (
    <section className="mt-4 border-t border-line pt-4">
      <h5 className="text-body-s font-semibold text-ink-800">{t('因子与分数构成')}</h5>
      <p className="mt-1 text-micro text-ink-500">{t('有效权重是本次实际参与计算的比例；贡献是各因子计入总分的分值。缺失项保持未提供。')}</p>
      {Object.keys(scoreGate).length > 0 && <div className="mt-3 rounded-md border border-line bg-paper-2 p-3 text-micro text-ink-600">
        <h6 className="font-semibold text-ink-800">{t('最终评分门（当前轨道）')}</h6>
        <div className="mt-1 grid gap-x-4 gap-y-1 sm:grid-cols-2">
          <span>{t('因子覆盖率')}{t('：')}{diagnosticPercent(scoreGate.coverage_ratio)} / {t('最低要求')} {diagnosticPercent(scoreGate.coverage_min)} · {gateText(scoreGate.coverage_passed)}</span>
          <span>{t('最低分')}{t('：')}{diagnosticNumber(scoreGate.score_floor)} · {gateText(scoreGate.score_floor_passed)}</span>
          <span>{t('必需因子')}{t('：')}{gateText(scoreGate.required_factors_passed)}</span>
        </div>
      </div>}
      {Object.keys(scoreGate).length === 0 && <p className="mt-2 text-micro text-ink-500">{t('本路径缺少当前轨道评分门数据，不能判断是否达到最低要求。')}</p>}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {DIAGNOSTIC_FACTORS.map((factor) => (
          <div key={factor} className="min-w-0 rounded-md border border-line bg-paper-2 p-3">
            <div className="flex items-baseline justify-between gap-2">
              <strong className="text-body-s text-ink-800">{factorNames[factor]} <span className="font-mono text-ink-500">{factor}</span></strong>
              <span className="font-mono text-body-s text-ink-800">{factorValue(factors[factor])}</span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 text-micro text-ink-500">
              <span>{t('配置权重')}{t('：')}{diagnosticPercent(configured[factor])}</span>
              <span>{t('轨道权重')}{t('：')}{diagnosticPercent(track[factor])}</span>
              <span>{t('有效权重')}{t('：')}{diagnosticPercent(effective[factor])}</span>
              <span>{t('分数贡献')}{t('：')}{diagnosticNumber(components[factor], 2)}</span>
            </div>
            {factor === 'G' && (path.track === 'PRICE_ONLY_DIAGNOSTIC' || path.track === 'D_MARKET_RESIDUAL_DIAGNOSTIC') && <p className="mt-1 text-micro text-ink-500">{t('当前诊断轨道禁用行业因子，不把缺失的行业数据当作零分。')}</p>}
            {factor === 'R' && <p className="mt-1 text-micro text-ink-500">{t('稳定性衡量走势中波动和回撤的平稳程度，不表示未来收益有保证。')}</p>}
          </div>
        ))}
      </div>
      {source && <p className="mt-3 text-micro text-ink-500">
        {t('权重来源')}{t('：')}{source.source === 'base_times_prior_normalized_v1'
          ? t('家族基础权重乘主题偏好后归一化一次')
          : source.source === 'base_only_v1' ? t('家族基础权重') : t('来源未说明')}
        {source.verification === 'stored_weights_match_formula' ? ` · ${t('已核对存储权重与公式一致')}` : ''}
        {typeof source.registry_sha256 === 'string' ? ` · ${t('配置指纹')} ${source.registry_sha256.slice(0, 12)}` : ''}
        {typeof source.id === 'string' ? ` · ${t('权重记录')} ${source.id.slice(0, 12)}` : ''}
      </p>}
    </section>
  );
}

function PathCard({ path, sources }: {
  path: SecurityDiagnosticPath;
  sources: Record<string, DiagnosticRecord>;
}) {
  const reasons = Array.isArray(path.rejection_reasons) ? path.rejection_reasons : [];
  const gates = safeMap(path.common_gate_checks);
  const flags = safeMap(path.capability_flags);
  const theme = path.theme_id ?? path.sector_context;
  return (
    <details className="min-w-0 rounded-md border border-line bg-paper-1 p-3 sm:p-4">
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <span className="min-w-0 basis-full text-body-s font-semibold text-ink-800 sm:basis-0 sm:flex-1">{diagnosticTheme(theme)} · {diagnosticFamily(path.algorithm_id)}</span>
          <span className="whitespace-nowrap font-mono text-body-s text-ink-800">{t('分数')} {diagnosticNumber(path.score)}</span>
          <span className="whitespace-nowrap text-micro text-ink-500">{pathStatus(path.status)}</span>
          <span className="whitespace-nowrap text-micro text-brand-600">{t('查看路径')}</span>
        </div>
        {reasons.length > 0 && <p className="mt-1 text-micro text-ink-500">{diagnosticReason(reasons[0])}{reasons.length > 1 ? ` · ${t('另有条件未通过')}` : ''}</p>}
      </summary>
      <div className="mt-4 space-y-4 text-body-s text-ink-700">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-micro text-ink-500">
          <span>{t('主题代码')}{t('：')}{textValue(path.theme_id ?? path.sector_context)}</span>
          <span>{t('家族代码')}{t('：')}{textValue(path.algorithm_id)}</span>
          <span>{t('评分轨道')}{t('：')}{diagnosticTrack(path.track)}</span>
          <span>{t('证券类型')}{t('：')}{path.stock_or_etf_track === 'etf' ? t('基金') : path.stock_or_etf_track === 'stock' ? t('股票') : textValue(path.stock_or_etf_track)}</span>
          <span>{t('价格')}{t('：')}{money(path.price, 2)}</span>
        </div>
        <div>
          <h5 className="font-semibold text-ink-800">{t('未通过或待核实的条件')}</h5>
          {reasons.length ? <ul className="mt-1 list-disc space-y-1 pl-5">{reasons.map((reason, reasonIndex) => <li key={`${reason}-${reasonIndex}`}>{diagnosticReason(reason)}</li>)}</ul>
            : <p className="mt-1 text-ink-500">{path.status === 'eligible' ? t('这条路径没有记录拒绝原因。') : t('该路径没有提供具体原因。')}</p>}
        </div>
        <div>
          <h5 className="font-semibold text-ink-800">{t('波动与延伸')}</h5>
          <div className="mt-1 grid gap-x-4 gap-y-1 text-micro text-ink-500 sm:grid-cols-2">
            <span>{t('实际波动幅度')}{t('：')}{pctPoint(path.atr_pct)}</span>
            <span>{t('参照波动幅度')}{t('：')}{pctPoint(path.sector_median_atr_pct)}</span>
            <span>{t('波动门槛')}{t('：')}{pctPoint(path.atr_threshold_pct)}</span>
            <span>{t('参照成员数')}{t('：')}{diagnosticNumber(path.atr_reference_n, 0)}</span>
            <span>{t('参照来源')}{t('：')}{diagnosticAtrReference(path.atr_reference_source)}</span>
            <span>{t('参照规则')}{t('：')}{diagnosticAtrPolicy(path.atr_reference_policy)}</span>
            <span>{t('价格延伸')}{t('：')}{diagnosticNumber(path.extension_atr, 2)} ATR</span>
            <span>{t('延伸上限')}{t('：')}{diagnosticNumber(path.extension_limit_atr, 2)} ATR</span>
          </div>
        </div>
        <div>
          <h5 className="font-semibold text-ink-800">{t('资格与成交额')}</h5>
          <p className="mt-1 text-micro text-ink-500">{t('二十日平均成交额')}{t('：')}{money(path.adv20)} · {t('成交额数据可作参考，但代理口径尚未认证，不代表已通过流动性门。')}</p>
          <p className="mt-1 text-micro text-ink-500">{t('成交额资格')}{t('：')}{verifiedText(flags.dollar_liquidity_verified)} · {t('成交量时段')}{t('：')}{verifiedText(flags.volume_session_verified)}</p>
          {Object.keys(gates).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(gates).filter(([key]) => key !== 'score' && key !== 'coverage').map(([key, value]) => <span key={key} className="rounded border border-line px-2 py-1 text-micro text-ink-500">{gateNames[key] ?? t('其他条件')}: {key === 'adv20' && value === true && flags.dollar_liquidity_verified !== true ? t('代理值达到数值门槛，资格未认证') : gateText(value)}</span>)}
          </div>}
          {path.gate_results_source === 'upstream_full_model' && <p className="mt-1 text-micro text-ink-500">{t('原始模型的形态门仅供参考；最终评分门以上方当前轨道结果为准。')}</p>}
        </div>
        <FactorDetails path={path} sources={sources} />
      </div>
    </details>
  );
}

export default function SecurityDiagnostics({ profile, timeframe, publicationKey, ticker, onOpenDetail }: SecurityDiagnosticsProps) {
  const fixedTicker = ticker?.trim() ?? '';
  const [input, setInput] = useState(fixedTicker);
  const [pending, setPending] = useState<{ key: string; seq: number } | null>(null);
  const [result, setResult] = useState<DiagnosticResult | null>(null);
  const [failure, setFailure] = useState<DiagnosticFailure | null>(null);
  const seq = useRef(0);
  const symbol = fixedTicker || input.trim();
  const key = `${profile}|${timeframe}|${String(publicationKey ?? '')}|${symbol}`;
  const loading = pending?.key === key;

  useEffect(() => {
    seq.current += 1;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setPending(null);
      setResult(null);
      setFailure(null);
    });
    return () => { active = false; };
  }, [profile, timeframe, publicationKey, fixedTicker]);

  function query(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestKey = key;
    const requestSeq = ++seq.current;
    setResult(null);
    setFailure(null);
    if (!TICKER_PATTERN.test(symbol)) {
      setPending(null);
      setFailure({ key: requestKey, message: t('请输入有效代码，最多三十二个字母、数字、点或连字符。') });
      return;
    }
    setPending({ key: requestKey, seq: requestSeq });
    void getSecurityDiagnostics(symbol, profile, timeframe, publicationKey).then((payload) => {
      if (seq.current !== requestSeq) return;
      if ((payload.requested_ticker ?? payload.ticker) !== symbol || payload.profile !== profile || payload.horizon !== timeframe) {
        setFailure({ key: requestKey, message: t('返回的数据与所查代码或条件不符，请重试。') });
        return;
      }
      setResult({ key: requestKey, payload });
    }).catch((error: unknown) => {
      if (seq.current !== requestSeq) return;
      let message = t('读取失败，请稍后重试。');
      if (error instanceof ApiError && error.code === 404) message = t('该代码不在本批次证券目录中。');
      else if (error instanceof ApiError && error.code === 503) message = t('该批次尚无完整诊断，请等待扫描完成后重试。');
      else if (error instanceof ApiError && error.code === 409) message = t('该代码对应多个不同证券，无法唯一识别。');
      else if (error instanceof ApiError && error.code === 429) {
        message = typeof error.retryAfter === 'number' && Number.isFinite(error.retryAfter) && error.retryAfter > 0
          ? t('选股诊断查询过于频繁，请 {n} 秒后重试。', { n: Math.ceil(error.retryAfter) })
          : t('选股诊断查询过于频繁，请稍后重试。');
      }
      setFailure({ key: requestKey, message });
    }).finally(() => {
      if (seq.current === requestSeq) setPending(null);
    });
  }

  const shown = result?.key === key ? result.payload : null;
  const shownError = failure?.key === key ? failure.message : null;
  const display = safeMap(shown?.display);
  const sources = shown?.weight_provenance_sources ?? {};
  const paths = Array.isArray(shown?.paths) ? shown.paths : [];
  return (
    <section className="card-surface min-w-0 p-4 sm:p-5" aria-label={t('按代码查询选股诊断')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-title-s font-semibold text-ink-900">{t('按代码查询选股诊断')}</h3>
          <p className="mt-1 text-body-s text-ink-500">{t('输入证券代码，查看本批次所有主题与家族的评分和筛选原因；未上榜也可查询。')}</p>
        </div>
        <span className="rounded border border-line px-2 py-1 text-micro text-ink-500">{t('偏好')}{t('：')}{profile === 'balanced' ? t('均衡') : profile === 'conservative' ? t('稳健') : t('进取')} · {t('周期')}{t('：')}{timeframe === 'short' ? t('短期') : timeframe === 'mid' ? t('中期') : t('长期')}</span>
      </div>
      <form onSubmit={query} className="mt-4 flex flex-wrap items-end gap-2">
        <label className="min-w-[140px] flex-1 text-body-s text-ink-700">
          <span className="mb-1 block">{t('证券代码')}</span>
          <input value={fixedTicker || input} onChange={(event) => { if (!fixedTicker) { seq.current += 1; setPending(null); setResult(null); setFailure(null); setInput(event.target.value); } }} readOnly={Boolean(fixedTicker)}
            autoComplete="off" spellCheck={false} maxLength={32} placeholder={t('例如 AAPL 或 SPY')}
            className="w-full rounded-md border border-line bg-paper-1 px-3 py-2 font-mono text-body-s text-ink-800 outline-none focus:border-brand-500" />
        </label>
        <button type="submit" disabled={loading} className="rounded-md bg-brand-600 px-4 py-2 text-body-s font-semibold text-white disabled:opacity-60">{loading ? t('查询中') : t('查询诊断')}</button>
      </form>
      {loading && <p className="mt-4 text-body-s text-ink-500" role="status">{t('正在读取本批次诊断…')}</p>}
      {shownError && <p className="mt-4 rounded-md border border-line bg-paper-2 p-3 text-body-s text-ink-700" role="alert">{shownError}</p>}
      {shown && <div className="mt-5 space-y-4">
        <div className="rounded-md border border-line bg-paper-2 p-3 sm:p-4">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="font-mono text-title-s text-ink-900">{shown.ticker}</strong>
            <span className="text-body-s text-ink-700">{diagnosticDataStatus(shown.data_status)}</span>
            {shown._stale && <span className="rounded bg-paper-1 px-2 py-0.5 text-micro text-ink-700">{t('旧数据')}</span>}
            {shown.historical_example && <span className="rounded bg-paper-1 px-2 py-0.5 text-micro text-ink-700">{t('历史示例')}</span>}
            {shown.synthetic && <span className="rounded bg-paper-1 px-2 py-0.5 text-micro text-ink-700">{t('合成数据')}</span>}
            {onOpenDetail && shown.data_status !== 'out_of_scope' && <button type="button" onClick={() => onOpenDetail(shown.ticker)} className="ml-auto text-body-s text-brand-600 underline">{t('查看股票详情')}</button>}
          </div>
          {Object.keys(display).length > 0 && <p className="mt-2 text-body-s text-ink-700">{t('结果归属')}{t('：')}{diagnosticDisplayReason(display.display_reason)}
            {display.in_observation === true && display.observation_rank != null ? ` · ${t('全资产观察名次（筛选前）')} ${display.observation_rank}` : ''}
            {display.in_observation === true && display.track_observation_rank != null ? ` · ${t('同类观察名次（筛选前）')} ${display.track_observation_rank}` : ''}
            {display.in_composite === true && display.composite_rank != null ? ` · ${t('综合名次（筛选前）')} ${display.composite_rank}` : ''}
          </p>}
          <div className="mt-2 grid gap-1 text-micro text-ink-500 sm:grid-cols-2">
            <span>{t('数据截止交易日')}{t('：')}{textValue(shown.served_session ?? shown.score_data_through)}</span>
            <span>{t('快照保存时间')}{t('：')}{textValue(shown.snapshot_saved_at)}</span>
            <span>{t('计算版本')}{t('：')}{textValue(shown.compute_version)}</span>
            <span>{t('特征版本')}{t('：')}{textValue(shown.feature_version)}</span>
            <span className="break-all sm:col-span-2">{t('来源标识')}{t('：')}{textValue(shown.source_hash)}</span>
          </div>
          {shown.coverage && <p className="mt-2 text-micro text-ink-500">{t('目录覆盖状态')}{t('：')}{diagnosticCoverageStatus(shown.coverage.status)}</p>}
          {shown._stale && <p className="mt-2 text-body-s text-ink-700">{t('此结果不是最新收盘批次，请核对截止日期。')}</p>}
        </div>
        <div>
          <h4 className="text-body-s font-semibold text-ink-800">{t('完整评分路径')} <span className="font-mono">{paths.length}</span></h4>
          <p className="mt-1 text-micro text-ink-500">{t('每条路径对应一个主题和一个家族。展开后可查看拒绝原因、波动门槛及因子贡献。')}</p>
          {paths.length ? <div className="mt-3 space-y-2">{paths.map((path, index) => <PathCard key={pathKey(path, index)} path={path} sources={sources} />)}</div>
            : <p className="mt-3 rounded-md border border-line p-3 text-body-s text-ink-500">{t('本批次没有这只证券的评分路径。请查看上方目录状态和数据截止日。')}</p>}
        </div>
      </div>}
    </section>
  );
}
