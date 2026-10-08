/**
 * 首页「市场综合研判」演示数据：GET /api/market-brief/latest 的完整响应（snake_case 原样），
 * 与 tests/fixtures/market_brief_sample.json 是同一份样例（market-brief-mock-contract 测试逐字段比对），
 * 由 api/modules/marketBrief.ts 的 mapLatest 归一。
 *
 * live 构建会把这里的每个导出替换成 undefined（vite.config 的 strip-mocks），所以只导出常量与函数。
 */

export const MARKET_BRIEF_SAMPLE = {
  status: 'ok',
  schema_version: 'market-brief-v1',
  brief: {
    run_id: 'mb_20261008_post_close_5f1c3a9e',
    trading_date: '2026-10-08',
    slot: 'post_close',
    trigger: 'scheduled',
    generated_at: '2026-10-09T03:41:12Z',
    model: {
      id: 'claude-opus-5-5',
      label: 'Claude Opus 5.5',
      effort: 'xhigh',
    },
    coverage: {
      universe_size: 5894,
      scored_count: 4410,
      quotes_valid: 5,
      breadth_basis: 'sector_etf_proxy_11',
      data_through: {
        indices: '2026-10-08T20:05:12Z',
        market_signals: '2026-10-08T20:02:40Z',
        eod_batch: '2026-10-08',
        macro: '2026-10-08',
        news: '2026-10-09T03:20:00Z',
        calendar: '2026-10-09T03:20:00Z',
      },
      missing_blocks: [
        {
          block: 'sector_iv',
          reason: 'snapshot_missing',
        },
      ],
      evidence_bytes: 41234,
    },
    result: {
      output_language: 'zh-CN',
      headline: '权重股撑盘、广度未跟上：指数新高由少数大盘科技股推动，证据充分度中等。',
      regime: 'narrow_leadership',
      evidence_sufficiency: 'medium',
      internals: {
        summary: '标普500与纳指收在20日均线之上并刷新阶段高点，但等权指数相对市值加权指数连续五日走弱，11个行业板块中只有6个站上50日均线。突破雷达里近五个交易日新触发的事件多数仍处观察状态，确认比例偏低，说明上涨集中在少数权重股。',
        breadth_vs_index: 'diverges',
        points: ['标普500收涨0.6%，纳指收涨0.9%，道指基本持平。', '等权对市值加权的5日相对强弱为负，小盘对大盘同样偏弱。', '波动率指数回落至16附近，处于一年分位的低位。'],
        evidence_ids: ['idx:^GSPC', 'idx:^IXIC', 'sig:rsp_spy_5d', 'sig:sectors_above_50dma', 'regime:market'],
      },
      macro_check: {
        summary: '宏观综合分位处于中性偏松区间，流动性与信用模块近一周改善，利率模块走弱。10年期收益率回落与信用利差收窄支持风险偏好，但美元走强对海外收入占比高的板块构成压力，整体对当前股票叙事是部分支持。',
        verdict: 'mixed',
        points: ['宏观综合分位58，较一周前上升2分。', '10年期收益率20日下行，信用风险读数改善。', '美元指数走强，与大宗商品回落同向。'],
        evidence_ids: ['macro:composite', 'sig:yield_10y_20d_change', 'sig:credit_risk'],
      },
      sectors: [
        {
          name: '半导体',
          change: 'substantive',
          note: '近一个月相对标普500超额收益扩大，主题平均强度居首，领涨股集中在设备与存储环节。',
          evidence_ids: ['theme:semiconductors'],
        },
        {
          name: '能源',
          change: 'noise',
          note: '单日反弹主要跟随油价波动，一个月与三个月收益仍落后基准。',
          evidence_ids: ['theme:energy'],
        },
      ],
      key_news: [
        {
          evidence_id: 'news:9f3c2b7e1a',
          title_zh: '存储芯片现货价格连续第三周上涨',
          what_is_new: '本周新增的信息是合约价谈判提前，此前市场只计入现货端涨价。',
          priced_in: 'partly',
          tickers: ['MU', 'WDC'],
        },
        {
          evidence_id: 'hot:evt_2c91',
          title_zh: '联邦公开市场委员会会议纪要显示多数官员倾向观望',
          what_is_new: '与上月表态一致，没有新增信息，债市反应平淡。',
          priced_in: 'yes',
          tickers: [],
        },
      ],
      watch_items: [
        {
          what: '等权指数与市值加权指数的相对强弱能否止跌',
          why: '广度持续背离时，指数新高的可持续性依赖少数权重股的财报与指引。',
          revise_if: '若等权指数连续三日跑赢，应把判断从权重股撑盘改为广泛走强。',
        },
        {
          what: '明日盘前公布的生产者物价指数',
          why: '利率模块是目前宏观里最弱的一环，通胀数据直接决定收益率方向。',
          revise_if: '若数据高于预期且10年期收益率回到前高之上，风险偏好读数需要下调。',
        },
      ],
      invalidators: ['波动率指数单日上升超过20%且信用利差同步走阔。', '标普500收回20日均线之下且板块50日均线占比跌破一半。'],
      prior_review: '上一份开盘前研判提示的半导体领涨与广度偏弱在今日盘中得到延续，所列的观察项中财报指引一项尚未兑现。',
    },
    external_sources: [
      {
        url: 'https://www.federalreserve.gov/monetarypolicy/fomcminutes20261001.htm',
        title: 'FOMC Minutes, October 2026',
      },
      {
        url: 'https://www.bls.gov/schedule/news_release/ppi.htm',
        title: 'PPI release schedule',
      },
    ],
    validation_warnings: [],
    web_search_count: 6,
  },
  latest_attempt: null,
  next_slot: {
    slot: 'pre_open',
    at: '2026-10-09T12:40:00Z',
  },
  snapshot_saved_at: '2026-10-09T03:41:13Z',
};

/** 演示「现在生成」：提交约 8 秒后，下一次读取换成一份手动生成的研判。 */
const MOCK_RUN_MS = 8_000;
let pendingSince: number | null = null;
let manualRuns = 0;
let manualBrief: typeof MARKET_BRIEF_SAMPLE.brief | null = null;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function getMarketBriefLatest() {
  const now = Date.now();
  if (pendingSince !== null && now - pendingSince >= MOCK_RUN_MS) {
    pendingSince = null;
    manualRuns += 1;
    manualBrief = {
      ...clone(MARKET_BRIEF_SAMPLE.brief),
      run_id: `${MARKET_BRIEF_SAMPLE.brief.run_id}_manual_${manualRuns}`,
      trigger: 'manual',
      generated_at: new Date(now).toISOString(),
    };
  }
  const body = clone(MARKET_BRIEF_SAMPLE);
  if (!manualBrief) return body;
  return { ...body, brief: clone(manualBrief), snapshot_saved_at: manualBrief.generated_at };
}

/** 与 POST /api/market-brief/runs 同形：新排队 reason 为空；还在跑时 reason=already_running。 */
export function triggerMarketBrief() {
  const running = pendingSince !== null;
  pendingSince ??= Date.now();
  return {
    request_id: `mock-market-brief-${manualRuns + 1}`,
    status: running ? 'running' : 'queued',
    reason: running ? 'already_running' : '',
    cooldown_until: null,
    error_code: running ? 'market_brief_in_progress' : null,
  };
}
