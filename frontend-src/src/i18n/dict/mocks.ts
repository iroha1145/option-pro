/**
 * src/mocks/ 词典：仅 VITE_API_MODE=mock（本地开发）下使用的 fixture 静态文案。
 * 覆盖 data.ts / fixtures.ts / fixtures2.ts / marketPulse.ts / session.ts。
 *
 * 边界：本文件刻意不包含「模拟 AI 生成正文」的字符串——财报影响分析长句
 * （IMPACT_SUMMARIES）、新闻标题/摘要模板池（NEWS_TEMPLATES / THEME_TEMPLATES /
 * GENERIC_TEMPLATES，供 titleZh/summaryZh 使用）、焦点周期 dominantEvent/summary
 * 及各标的 assessment.note（经 src/components/catalysts/api.ts 核实，对应后端
 * title_zh / summary / summary_zh 字段，属于 AI 自由文本，非固定 UI 文案）。
 * 这些内容始终由后端模型运行时生成，前端原样透传，翻译不了也不该翻译。
 */
import type { Dict } from './types';

export const MOCKS: Dict = {
  /* ---------------- data.ts：TICKER_POOL 公司名 ---------------- */
  '英伟达': ['NVIDIA', 'エヌビディア'],
  '特斯拉': ['Tesla', 'テスラ'],
  '苹果': ['Apple', 'アップル'],
  '超威半导体': ['AMD', 'AMD'],
  '微软': ['Microsoft', 'マイクロソフト'],
  'Meta 平台': ['Meta Platforms', 'メタ・プラットフォームズ'],
  '亚马逊': ['Amazon', 'アマゾン'],
  '谷歌 A': ['Alphabet (Google) Class A', 'アルファベット（グーグル）クラスA'],
  '博通': ['Broadcom', 'ブロードコム'],
  '超微电脑': ['Super Micro Computer (Supermicro)', 'スーパーマイクロコンピューター'],
  '台积电': ['TSMC', 'TSMC（台湾積体電路製造）'],
  'Arm 控股': ['Arm Holdings', 'アーム・ホールディングス'],
  '美光科技': ['Micron Technology', 'マイクロン・テクノロジー'],
  '英特尔': ['Intel', 'インテル'],
  '奈飞': ['Netflix', 'ネットフリックス'],
  '赛富时': ['Salesforce', 'セールスフォース'],
  '标普 500 ETF': ['S&P 500 ETF', 'S&P500 ETF'],
  '纳指 100 ETF': ['Nasdaq 100 ETF', 'ナスダック100 ETF'],
  '甲骨文': ['Oracle', 'オラクル'],
  '优步': ['Uber', 'ウーバー'],
  '爱彼迎': ['Airbnb', 'エアビーアンドビー'],
  '礼来': ['Eli Lilly', 'イーライリリー'],
  '联合健康': ['UnitedHealth Group', 'ユナイテッドヘルス・グループ'],
  '摩根大通': ['JPMorgan Chase', 'JPモルガン・チェース'],
  '维萨': ['Visa', 'ビザ'],
  '埃克森美孚': ['ExxonMobil', 'エクソンモービル'],
  '雪佛龙': ['Chevron', 'シェブロン'],
  '卡特彼勒': ['Caterpillar', 'キャタピラー'],
  'GE 航空航天': ['GE Aerospace', 'GE エアロスペース'],
  '迪尔': ['Deere & Company', 'ディア・アンド・カンパニー'],
  '沃尔玛': ['Walmart', 'ウォルマート'],
  '好市多': ['Costco', 'コストコ'],
  '宝洁': ['Procter & Gamble', 'プロクター・アンド・ギャンブル'],
  '可口可乐': ['Coca-Cola', 'コカ・コーラ'],
  '新纪元能源': ['NextEra Energy', 'ネクステラ・エナジー'],
  '南方电力': ['Southern Company', 'サザン・カンパニー'],
  '林德': ['Linde', 'リンデ'],
  '自由港麦克莫兰': ['Freeport-McMoRan', 'フリーポート・マクモラン'],
  '迈威尔科技': ['Marvell Technology', 'マーベル・テクノロジー'],

  /* data.ts：NEWS_SOURCES */
  '华尔街日报': ['The Wall Street Journal', 'ウォール・ストリート・ジャーナル'],
  '彭博社': ['Bloomberg', 'ブルームバーグ'],
  '路透社': ['Reuters', 'ロイター'],
  '巴伦周刊': ["Barron's", 'バロンズ'],
  '金融时报': ['Financial Times', 'フィナンシャル・タイムズ'],
  '雅虎财经': ['Yahoo Finance', 'Yahoo!ファイナンス'],

  /* data.ts：HOTSPOTS 热点主题（与 fixtures2.ts THEME_DEFS.theme 复用同一字符串） */
  'AI 算力资本开支': ['AI compute capex', 'AI関連の設備投資'],
  '大型科技股财报季': ['Mega-cap tech earnings season', '大型テック決算シーズン'],
  '降息预期与利率路径': ['Rate-cut expectations & the rate path', '利下げ観測と金利パス'],
  '电动车价格战升级': ['EV price war escalation', 'EV価格戦争の激化'],
  '加密资产回暖': ['Crypto rebound', '暗号資産の回復'],
  '减肥药产业链': ['GLP-1 weight-loss drug chain', 'GLP-1減量薬サプライチェーン'],

  /* ---------------- fixtures.ts：指数 tape ---------------- */
  '标普 500': ['S&P 500', 'S&P500'],
  '纳指 100': ['Nasdaq 100', 'ナスダック100'],
  '罗素 2000': ['Russell 2000', 'ラッセル2000'],
  '波动率指数': ['Volatility Index', 'ボラティリティ指数'],

  /* fixtures.ts：强度画像 profile（screener 权重预设） */
  '均衡动量': ['Balanced momentum', 'バランス型モメンタム'],
  '趋势/动量/量能/波动均衡加权，适合大多数市况。': [
    'Even weighting across trend, momentum, volume, and volatility — fits most market conditions.',
    'トレンド・モメンタム・出来高・ボラティリティを均等に加重し、大半の相場環境に対応します。',
  ],
  '突破猎手': ['Breakout hunter', 'ブレイクアウト・ハンター'],
  '加重动量与量能，捕捉放量突破早期的标的。': [
    'Overweights momentum and volume to catch names early in a high-volume breakout.',
    'モメンタムと出来高を重視し、出来高を伴うブレイクアウト初動の銘柄を捉えます。',
  ],
  '低波稳健': ['Low-vol stability', '低ボラティリティ安定型'],
  '偏好低波动与趋势延续，回撤优先。': [
    'Favors low volatility and trend continuation; drawdown control comes first.',
    '低ボラティリティとトレンド継続を選好し、ドローダウン抑制を優先します。',
  ],
  '风险资产相对避险': ['Risk assets vs. safe havens', 'リスク資産対安全資産'],

  /* fixtures.ts：趋势偏向四因子说明（getStockTrendBias） */
  '趋势结构': ['Trend structure', 'トレンド構造'],
  '价格站上 MA20，短均线呈多头排列，趋势分偏强': [
    'Price is above the 20-day MA with short-term MAs in bullish alignment — trend score skews strong.',
    '価格が20日移動平均線を上回り、短期線は強気配列です。トレンドスコアは強めです。',
  ],
  '价格跌破 MA20，均线拐头向下，趋势分偏弱': [
    'Price is below the 20-day MA and the MA has turned down — trend score skews weak.',
    '価格が20日移動平均線を割り込み、移動平均線は下向きに転換しています。トレンドスコアは弱めです。',
  ],
  '价格围绕 MA20 反复，趋势方向尚未确认': [
    'Price is chopping around the 20-day MA — trend direction is unconfirmed.',
    '価格は20日移動平均線付近でもみ合っており、トレンド方向はまだ確認できません。',
  ],
  '动量读数上行，近端斜率加速，追涨情绪占优': [
    'Momentum is rising and the near-term slope is accelerating — chase-the-rally sentiment dominates.',
    'モメンタムは上昇し、直近の傾きも加速しています。追随買いのセンチメントが優勢です。',
  ],
  '动量读数回落，近端斜率走弱，注意回撤风险': [
    'Momentum is falling and the near-term slope is weakening — watch for pullback risk.',
    'モメンタムは低下し、直近の傾きも鈍化しています。押し目リスクに注意してください。',
  ],
  '动量读数走平，多空力量暂时均衡': [
    'Momentum is flat — bulls and bears are roughly balanced for now.',
    'モメンタムは横ばいで、強気と弱気が拮抗しています。',
  ],
  '成交较 20 日均量明显放大，量能配合价格方向': [
    'Volume is running well above the 20-day average, confirming the price direction.',
    '出来高は20日平均を大きく上回り、価格の方向性を裏付けています。',
  ],
  '成交较 20 日均量萎缩，方向缺乏量能确认': [
    'Volume is running below the 20-day average — the move lacks volume confirmation.',
    '出来高は20日平均を下回り、方向性が出来高で裏付けられていません。',
  ],
  '量能维持在常态区间，未见异常放量': [
    'Volume is within its normal range, with no unusual surge.',
    '出来高は通常のレンジ内で推移しており、異常な急増は見られません。',
  ],
  '波动定价': ['Volatility pricing', 'ボラティリティ・プライシング'],
  'IV 百分位偏高，期权定价隐含较大波动预期': [
    'IV percentile is elevated — options pricing implies a larger expected move.',
    'IVパーセンタイルは高水準で、オプション価格は大きめの値動きを織り込んでいます。',
  ],
  'IV 百分位偏低，期权定价相对便宜': [
    'IV percentile is low — options are priced relatively cheap.',
    'IVパーセンタイルは低水準で、オプションは比較的割安です。',
  ],
  'IV 百分位处于中位，波动定价中性': [
    'IV percentile is mid-range — volatility pricing is neutral.',
    'IVパーセンタイルは中位で、ボラティリティ・プライシングは中立です。',
  ],

  /* ---------------- fixtures2.ts：突破雷达生命周期 / 错误 ---------------- */
  '价格越过触发位': ['Price crossed the trigger level', '価格がトリガー水準を突破'],
  '跌得失效价，信号作废': ['Price fell through the invalidation level — signal voided', '価格が無効化ラインを割り込み、シグナル無効化'],
  '超时未延续，归档': ['No follow-through within the time window — archived', '時間切れで継続せず、アーカイブ済み'],
  '突破事件不存在': ['Breakout event not found', 'ブレイクアウトイベントが見つかりません'],

  /* fixtures2.ts：IMPACT_RELATIONS 关系标签（chip 短标签，非生成正文） */
  '服务器整机': ['Server systems', 'サーバー完成機'],
  '晶圆代工': ['Wafer foundry', 'ウエハー受託製造'],
  'HBM 供应': ['HBM supply', 'HBM供給'],
  'IP 授权': ['IP licensing', 'IPライセンス'],
  '算力供应链': ['Compute supply chain', 'コンピューティング・サプライチェーン'],
  '云基建': ['Cloud infrastructure', 'クラウド基盤'],
  '企业软件': ['Enterprise software', 'エンタープライズソフトウェア'],
  '数据平台': ['Data platform', 'データプラットフォーム'],
  '出行生态': ['Mobility ecosystem', 'モビリティ・エコシステム'],
  '消费景气': ['Consumer spending', '個人消費'],
  '大盘成长联动': ['Broad growth-stock linkage', 'グロース株との連動'],
  '同板块联动': ['Same-sector linkage', '同セクター連動'],

  /* fixtures2.ts：催化剂日历事件标签（getCatalystsCalendar） */
  'CPI 数据公布': ['CPI release', 'CPI発表'],
  'FOMC 会议纪要': ['FOMC minutes', 'FOMC議事要旨'],
  '初请失业金人数': ['Initial jobless claims', '新規失業保険申請件数'],
  'PPI 数据公布': ['PPI release', 'PPI発表'],
  '零售销售月率': ['Retail sales m/m', '小売売上高 前月比'],

  /* fixtures2.ts：AI 任务 / Worker 心跳 */
  '任务不存在': ['Task not found', 'タスクが見つかりません'],
  'AI 分析完成：综合基本面、量价与期权定价，详见结果正文。': [
    'AI analysis complete: combines fundamentals, price/volume action, and options pricing — see the full result below.',
    'AI分析が完了しました。ファンダメンタルズ・値動き・オプション価格を総合的に評価しました。詳細は本文でご確認ください。',
  ],
  '指数行情采集': ['Index quote ingestion', '指数相場収集'],
  '自选股快照': ['Watchlist snapshot', 'ウォッチリストのスナップショット'],
  '强度分计算': ['Strength score calculation', '強度スコア計算'],
  '板块聚合': ['Sector aggregation', 'セクター集計'],
  '财报日历同步': ['Earnings calendar sync', '決算カレンダー同期'],
  '新闻抓取': ['News crawler', 'ニュース収集'],
  '热点聚类': ['Hotspot clustering', '注目テーマのクラスタリング'],
  '期权异动': ['Unusual options scan', 'オプション異常検知'],
  'AI 任务调度': ['AI job scheduler', 'AIジョブスケジューラ'],
  '运行正常': ['Running normally', '正常稼働'],
  '延迟略高于均值': ['Latency slightly above average', 'レイテンシがやや平均超過'],
  '队列积压清理中': ['Clearing queue backlog', 'キュー滞留を解消中'],
  '等待下一周期': ['Waiting for next cycle', '次のサイクル待ち'],

  /* fixtures2.ts：热点主题关键词标签（THEME_DEFS.keywords） */
  '算力': ['Compute', 'コンピューティング'],
  '资本开支': ['Capex', '資本支出'],
  '数据中心': ['Data center', 'データセンター'],
  '利润率': ['Margins', '利益率'],
  '降息': ['Rate cuts', '利下げ'],
  '美债收益率': ['Treasury yields', '米国債利回り'],
  '降价': ['Price cuts', '値下げ'],
  '毛利率': ['Gross margin', '粗利率'],
  '交付量': ['Deliveries', '納車台数'],
  '比特币': ['Bitcoin', 'ビットコイン'],
  'ETF 流入': ['ETF inflows', 'ETF資金流入'],
  '合规': ['Compliance', 'コンプライアンス'],
  '产能': ['Capacity', '生産能力'],
  '渠道': ['Channel', '販売チャネル'],

  /* fixtures2.ts：buildAnalysisResult 内的固定标签池（机制 / 时间窗口 chip，
     与该函数内 reason/headlineSummary/causalSummary 等生成正文字段严格区分） */
  '资本开支传导': ['Capex pass-through', '資本支出の波及'],
  '供应链定价权': ['Supply-chain pricing power', 'サプライチェーンの価格決定力'],
  '利润率重估': ['Margin re-rating', '利益率の再評価'],
  '估值倍数变化': ['Valuation multiple shift', 'バリュエーション倍率の変化'],
  '订单能见度改善': ['Order visibility improvement', '受注の見通し改善'],
  '利率敏感性': ['Rate sensitivity', '金利感応度'],
  '成本转嫁能力': ['Cost pass-through ability', 'コスト転嫁力'],
  '需求弹性': ['Demand elasticity', '需要弾力性'],
  '1–3 天': ['1–3 days', '1〜3日'],
  '1–2 周': ['1–2 weeks', '1〜2週間'],
  '市场焦点': ['Market focus', '市場の注目テーマ'],
  '模型输出校验失败，可带 force 重试': [
    'Model output failed validation — retry with force to override.',
    'モデル出力の検証に失敗しました。force 指定で再試行できます。',
  ],
  '新闻不存在': ['News item not found', 'ニュースが見つかりません'],

  /* fixtures2.ts：经济日历（getEconomicCalendar） */
  '纽约联储制造业指数': ['NY Fed Manufacturing Index', 'ニューヨーク連銀製造業景気指数'],
  'CPI 月率（10 月）': ['CPI m/m (Oct)', 'CPI 前月比（10月）'],
  '美联储理事沃勒讲话': ['Fed Governor Waller speech', 'ウォラーFRB理事講演'],
  '22.5 万': ['225K', '22.5万'],
  '22.1 万': ['221K', '22.1万'],
  '密歇根大学消费者信心指数': ['University of Michigan Consumer Sentiment', 'ミシガン大学消費者信頼感指数'],
  'PPI 月率（10 月）': ['PPI m/m (Oct)', 'PPI 前月比（10月）'],
  '零售销售月率（10 月）': ['Retail sales m/m (Oct)', '小売売上高 前月比（10月）'],
  '美联储主席鲍威尔讲话': ['Fed Chair Powell speech', 'パウエルFRB議長講演'],
  '感恩节 · 美股休市': ['Thanksgiving · US markets closed', '感謝祭 · 米国市場休場'],
  '非农就业人口变动（11 月）': ['Nonfarm payrolls (Nov)', '非農業部門雇用者数（11月）'],
  '18.5 万': ['185K', '18.5万'],
  '17.2 万': ['172K', '17.2万'],
  '失业率（11 月）': ['Unemployment rate (Nov)', '失業率（11月）'],
  'ISM 制造业 PMI（11 月）': ['ISM Manufacturing PMI (Nov)', 'ISM製造業PMI（11月）'],
  '中': ['Medium', '中'],
  '美国': ['United States', '米国'],
  '数据覆盖时间落后，采集流处于降级状态': [
    'Data coverage is lagging — the ingestion stream is degraded.',
    'データ取得に遅延があり、収集ストリームは劣化状態です。',
  ],

  /* ---------------- marketPulse.ts ---------------- */
  '常规时段已收盘 · 等待下一交易时段': ['Regular session closed · Waiting for the next session', '通常取引終了 · 次のセッションを待機中'],
  '周末休市 · 下一个交易日 9:30 ET 开盘': ['Weekend closure · Next session opens 9:30 ET', '週末休場 · 次の取引日は9:30 ET 開始'],

  /* ---------------- session.ts：设置变更审计日志 ---------------- */
  '开启 AI 分析开关': ['Enabled the AI analysis toggle', 'AI分析トグルをオンに変更'],
  '扫描间隔 10 → 15 分钟': ['Scan interval 10 → 15 min', 'スキャン間隔 10 → 15分'],
};
