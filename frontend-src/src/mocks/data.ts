/** 静态数据池：代码池 / 中文名 / 板块 / 新闻句式（真实风格） */

export interface TickerInfo {
  ticker: string;
  name: string;
  sector: string;
  base: number; // 基准价
}

export const SECTORS = [
  { id: 'tech', name: '信息技术' },
  { id: 'semi', name: '半导体' },
  { id: 'comm', name: '通信服务' },
  { id: 'disc', name: '可选消费' },
  { id: 'staple', name: '必需消费' },
  { id: 'health', name: '医疗保健' },
  { id: 'fin', name: '金融' },
  { id: 'energy', name: '能源' },
  { id: 'indu', name: '工业' },
  { id: 'mat', name: '原材料' },
  { id: 'util', name: '公用事业' },
] as const;

export const TICKER_POOL: TickerInfo[] = [
  { ticker: 'NVDA', name: '英伟达', sector: '半导体', base: 178.4 },
  { ticker: 'TSLA', name: '特斯拉', sector: '可选消费', base: 246.8 },
  { ticker: 'AAPL', name: '苹果', sector: '信息技术', base: 232.1 },
  { ticker: 'AMD', name: '超威半导体', sector: '半导体', base: 162.3 },
  { ticker: 'MSFT', name: '微软', sector: '信息技术', base: 428.5 },
  { ticker: 'META', name: 'Meta 平台', sector: '通信服务', base: 585.2 },
  { ticker: 'AMZN', name: '亚马逊', sector: '可选消费', base: 205.7 },
  { ticker: 'GOOGL', name: '谷歌 A', sector: '通信服务', base: 178.9 },
  { ticker: 'AVGO', name: '博通', sector: '半导体', base: 172.6 },
  { ticker: 'SMCI', name: '超微电脑', sector: '信息技术', base: 42.8 },
  { ticker: 'TSM', name: '台积电', sector: '半导体', base: 196.4 },
  { ticker: 'ARM', name: 'Arm 控股', sector: '半导体', base: 138.2 },
  { ticker: 'MU', name: '美光科技', sector: '半导体', base: 108.6 },
  { ticker: 'INTC', name: '英特尔', sector: '半导体', base: 23.4 },
  { ticker: 'NFLX', name: '奈飞', sector: '通信服务', base: 912.3 },
  { ticker: 'CRM', name: '赛富时', sector: '信息技术', base: 332.8 },
  { ticker: 'PLTR', name: 'Palantir', sector: '信息技术', base: 78.5 },
  { ticker: 'COIN', name: 'Coinbase', sector: '金融', base: 264.9 },
  { ticker: 'HOOD', name: 'Robinhood', sector: '金融', base: 46.2 },
  { ticker: 'SPY', name: '标普500 ETF', sector: '金融', base: 596.4 },
  { ticker: 'QQQ', name: '纳指100 ETF', sector: '金融', base: 524.8 },
  { ticker: 'ORCL', name: '甲骨文', sector: '信息技术', base: 176.3 },
  { ticker: 'ADBE', name: 'Adobe', sector: '信息技术', base: 468.1 },
  { ticker: 'NOW', name: 'ServiceNow', sector: '信息技术', base: 985.4 },
  { ticker: 'SHOP', name: 'Shopify', sector: '信息技术', base: 112.7 },
  { ticker: 'UBER', name: '优步', sector: '工业', base: 72.4 },
  { ticker: 'ABNB', name: '爱彼迎', sector: '可选消费', base: 131.6 },
  { ticker: 'DASH', name: 'DoorDash', sector: '可选消费', base: 178.3 },
  { ticker: 'LLY', name: '礼来', sector: '医疗保健', base: 782.5 },
  { ticker: 'UNH', name: '联合健康', sector: '医疗保健', base: 512.8 },
  { ticker: 'JPM', name: '摩根大通', sector: '金融', base: 244.6 },
  { ticker: 'V', name: '维萨', sector: '金融', base: 309.2 },
  { ticker: 'XOM', name: '埃克森美孚', sector: '能源', base: 118.4 },
  { ticker: 'CVX', name: '雪佛龙', sector: '能源', base: 152.7 },
  { ticker: 'CAT', name: '卡特彼勒', sector: '工业', base: 368.9 },
  { ticker: 'GE', name: 'GE 航空航天', sector: '工业', base: 186.2 },
  { ticker: 'DE', name: '迪尔', sector: '工业', base: 452.3 },
  { ticker: 'WMT', name: '沃尔玛', sector: '必需消费', base: 92.8 },
  { ticker: 'COST', name: '好市多', sector: '必需消费', base: 928.4 },
  { ticker: 'PG', name: '宝洁', sector: '必需消费', base: 168.5 },
  { ticker: 'KO', name: '可口可乐', sector: '必需消费', base: 68.9 },
  { ticker: 'NEE', name: '新纪元能源', sector: '公用事业', base: 76.3 },
  { ticker: 'SO', name: '南方电力', sector: '公用事业', base: 88.1 },
  { ticker: 'LIN', name: '林德', sector: '原材料', base: 462.7 },
  { ticker: 'FCX', name: '自由港麦克莫兰', sector: '原材料', base: 44.6 },
  { ticker: 'MRVL', name: '迈威尔科技', sector: '半导体', base: 72.8 },
  { ticker: 'SNOW', name: 'Snowflake', sector: '信息技术', base: 168.3 },
  { ticker: 'DDOG', name: 'Datadog', sector: '信息技术', base: 128.9 },
];

export const NEWS_SOURCES = ['华尔街日报', '彭博社', '路透社', 'CNBC', 'MarketWatch', '巴伦周刊', '金融时报', '雅虎财经'];

const NEWS_TEMPLATES: { title: string; summary: string }[] = [
  { title: '{t}盘前异动，成交量放大至日均两倍', summary: '市场人士指出，{t}盘前成交明显放大，机构买单集中在开盘前半小时，短线资金关注度快速升温。' },
  { title: '{t}获多家投行上调目标价，AI 需求被反复提及', summary: '包括摩根士丹利在内的三家投行本周上调{t}目标价，研报普遍认为其 AI 相关业务能见度延长至明年。' },
  { title: '美联储官员再放鸽派信号，成长股集体走强', summary: '隔夜美联储多位官员暗示对降息持开放态度，纳指期货走高，大型成长股普遍上涨。' },
  { title: '{t}宣布扩大回购规模，董事会授权新增百亿美元', summary: '{t}公告称董事会批准新增回购额度，管理层表示当前股价未能充分反映长期现金流价值。' },
  { title: '半导体板块掀涨价潮，{t}供应链订单排至明年中', summary: '产业链调研显示，先进制程产能持续紧张，{t}主要客户已锁定未来四个季度产能。' },
  { title: '{t}季度营收超预期，但指引谨慎引发盘后震荡', summary: '财报电话会上管理层对下一季度指引偏保守，分析师分歧加大，盘后股价一度波动超过 5%。' },
  { title: '期权市场大额买单现身 {t}，押注财报前波动放大', summary: '期权异动监测显示，{t}本周到期看涨合约成交激增，单笔权利金超过千万美元。' },
  { title: '{t}遭空头机构点名，公司回应「严重失实」', summary: '做空报告质疑{t}收入确认方式，公司盘后发布逐条反驳声明，并表示将保留法律权利。' },
  { title: '美元指数回落，离岸资金回流美股科技板块', summary: '外汇交易员称，美元走弱推动新兴市场基金回补美股科技仓位，龙头标的率先受益。' },
  { title: '{t}管理层变动：CFO 宣布退休，市场关注接班安排', summary: '{t}宣布首席财务官将于本季度末退休，公司已开始外部物色继任者，分析师称过渡风险可控。' },
  { title: '十年期美债收益率回落，成长股估值压力缓解', summary: '债券交易员表示，通胀数据低于预期带动收益率下行，长久期资产重获资金青睐。' },
  { title: '{t}与下游大客户签署多年期供货协议', summary: '公告称协议金额覆盖未来三年产能规划，锁定了核心产品线的基础需求。' },
  { title: '散户交易热度榜：{t}单日买入量跃居前三', summary: '零售券商数据显示，{t}连续两日位居散户净买入前列，社交讨论热度同步攀升。' },
  { title: '{t}突破关键技术位，量化资金跟进加仓', summary: '技术派交易员指出，{t}放量站上年内压力位，趋势跟踪策略触发买入信号。' },
  { title: '监管文件显示：多家主权基金增持 {t}', summary: '最新 13F 披露显示，两家主权财富基金在上一季度合计增持{t}逾千万股。' },
];

const HOTSPOTS = [
  { theme: 'AI 算力资本开支', tickers: ['NVDA', 'AMD', 'AVGO', 'TSM', 'MU'] },
  { theme: '大型科技股财报季', tickers: ['AAPL', 'MSFT', 'META', 'GOOGL', 'AMZN'] },
  { theme: '降息预期与利率路径', tickers: ['SPY', 'QQQ', 'JPM', 'V'] },
  { theme: '电动车价格战升级', tickers: ['TSLA'] },
  { theme: '加密资产回暖', tickers: ['COIN', 'HOOD'] },
  { theme: '减肥药产业链', tickers: ['LLY', 'UNH'] },
] as const;

export { NEWS_TEMPLATES, HOTSPOTS };
