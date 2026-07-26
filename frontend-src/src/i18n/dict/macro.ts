/**
 * 宏观环境模块（Macro Conditions）— 30 因子 / 7 模块宏观分位仪表盘。
 * 覆盖 MacroTechnicalMatrix / macro/* / lib/macroFit.ts / mocks/macro.ts /
 * shared/MacroFitBadge、MacroFitPanel 的展示层文案与 mock 因子名称。
 *
 * 术语口径（跨条目必须一致，勿逐条改写）：
 * - 「按当前修订值回算」= recomputed on latest revisions / 最新修正値による遡及計算
 * - 「本地点时快照」= local point-in-time snapshot / ローカル時点スナップショット
 * - 七模块名：Liquidity/Funding/Treasury/Rates/Credit/Risk/External shocks
 *   （流動性/ファンディング/国債/金利/クレジット/リスク/外部ショック）
 * - 顺风/逆风 = Tailwind/Headwind（65/35 分界）；象限的技术偏强/宏观偏强等
 *   四象限标签刻意不用 tailwind/headwind，用 strong/weak 系措辞，避免与
 *   65/35 分界混淆（象限分界线是 50，两套口径不同，源码注释已说明）。
 */
import type { Dict } from './types';

export const MACRO: Dict = {
  /* ============ src/components/market/MacroTechnicalMatrix.tsx ============ */
  '流动性': ['Liquidity', '流動性'],
  '技术 × 结构性宏观': ['Technical × structural macro', 'テクニカル × 構造的マクロ'],
  '技术侧为市场形态六维均值；宏观侧为结构性宏观（流动性、融资、国债、利率）的加权均值。': [
    'The technical side is the average of the six market-regime dimensions; the macro side is the weighted average of structural macro (liquidity, funding, treasury, rates).',
    'テクニカル側は市場レジーム6指標の平均値、マクロ側は構造的マクロ（流動性・ファンディング・国債・金利）の加重平均です。',
  ],
  '信用与风险不计入结构性宏观：它们与技术形态读的是同一批工具（HYG/LQD/KRE/VIX/': [
    'Credit and risk are excluded from structural macro: they read the same instruments as the technical side (HYG/LQD/KRE/VIX/',
    '信用とリスクは構造的マクロに含みません。テクニカル側と同じ銘柄群（HYG/LQD/KRE/VIX/',
  ],
  'SPY-TLT/IWM-SPY），再算一次等于同一个信号计两次权。此卡仅展示，不改变任何评分，': [
    "SPY-TLT/IWM-SPY), so counting them again would weight the same signal twice. This card is display-only and doesn't change any score,",
    'SPY-TLT/IWM-SPY）を参照しているため、もう一度加えると同じシグナルを二重にウェイト付けすることになります。このカードは表示専用でスコアを一切変更せず、',
  ],
  '融资': ['Funding', 'ファンディング'],
  '国债': ['Treasury', '国債'],
  '利率': ['Rates', '金利'],
  '技术 × 结构性宏观 · TECHNICAL × MACRO': ['Technical × structural macro · TECHNICAL × MACRO', 'テクニカル × 構造的マクロ · TECHNICAL × MACRO'],
  '暂无二维读数': ['No two-axis reading yet', '二軸データなし'],
  '技术形态': ['Technical', 'テクニカル'],
  '结构性宏观': ['Structural macro', '構造的マクロ'],
  '差值': ['Gap', '乖離'],
  ' · 宏观环境领先价格改善': [' · Macro backdrop improving ahead of price', ' · マクロ環境が価格に先行して改善'],
  ' · 两者大致同步': [' · Roughly in sync', ' · おおむね同期'],
  '市场形态与宏观快照都暂不可用': ["Both market-regime and macro snapshot data are unavailable", '市場レジームとマクロスナップショットのいずれも現在利用できません'],
  '市场形态六维暂不可用': ['The six market-regime dimensions are unavailable', '市場レジームの6指標は現在利用できません'],
  '宏观快照暂不可用': ['Macro snapshot unavailable', 'マクロスナップショットは利用できません'],
  ' · 不按中性计': [' · not treated as neutral', ' · 中立扱いにはしません'],
  '结构性宏观 =': ['Structural macro =', '構造的マクロ ='],

  /* ============ src/components/market/macro/CompositeCard.tsx ============ */
  '历史区间按当前修订值回算': ['Historical range recomputed on latest revisions', '履歴期間は最新修正値による遡及計算'],
  '本地点时快照': ['Local point-in-time snapshot', 'ローカル時点スナップショット'],
  '混合：部分区间按当前修订值回算': ['Mixed: part of the range is recomputed on latest revisions', '混在：一部区間は最新修正値による遡及計算'],
  '宏观环境综合分': ['Macro conditions composite score', 'マクロ環境総合スコア'],
  '综合分 · COMPOSITE': ['Composite', '総合スコア · COMPOSITE'],
  '历史分位': ['Historical percentile', 'ヒストリカル・パーセンタイル'],
  '环境标签暂不可用': ['Regime label unavailable', '環境ラベルは利用できません'],
  '7 日变化': ['7-day change', '7日間の変化'],
  '有效模块': ['Valid modules', '有効モジュール'],
  '数据截止': ['Data as of', 'データ基準日'],
  '分数是过去 5 年的历史分位，不是预测。高分表示当前金融环境相对历史更支持风险资产，\n        不代表市场一定上涨，也不构成买入、卖出、仓位或目标价建议。': [
    "The score is a historical percentile over the past 5 years, not a forecast. A high score means the current financial environment is more supportive of risk assets relative to history — it doesn't mean the market will necessarily rise, and it is not a buy, sell, position, or price-target recommendation.",
    'このスコアは過去5年間のヒストリカル・パーセンタイルであり、予測ではありません。高スコアは現在の金融環境が過去と比べて相対的にリスク資産を支持しやすいことを示すものであり、相場が必ず上昇することを意味せず、買い・売り・ポジション・目標株価のいずれの推奨も構成しません。',
  ],

  /* ============ src/components/market/macro/FactorDetails.tsx ============ */
  '重试': ['Retry', '再試行'],
  '因子当前值、历史分位与 7 日变化': ['Factor current values, historical percentiles, and 7-day changes', 'ファクターの現在値・ヒストリカル分位・7日間の変化'],
  '因子': ['Factor', 'ファクター'],
  '当前值': ['Current value', '現在値'],
  '7 日原值变化': ['7-day raw change', '7日間の実値変化'],
  '7 日分数变化': ['7-day score change', '7日間のスコア変化'],
  '因子详情暂不可用': ['Factor details unavailable', 'ファクターの詳細は利用できません'],
  '因子详情': ['Factor details', 'ファクター詳細'],
  '该模块暂无因子快照。': ['No factor snapshot for this module yet.', 'このモジュールにはまだファクターのスナップショットがありません。'],

  /* ============ src/components/market/macro/FactorRow.tsx ============ */
  '历史不足': ['Insufficient history', '履歴不足'],
  '数据陈旧': ['Stale data', '古いデータ'],
  '数据缺失': ['Missing data', 'データ欠落'],
  '带符号': ['Signed', '符号付き'],

  /* ============ src/components/market/macro/MacroConditionsPanel.tsx ============ */
  '冷却中': ['Cooling down', 'クールダウン中'],
  '宏观数据来自 FRED、纽约联储、联储理事会、芝加哥联储和 Cboe；跨资产代理使用 Option Pro 当前股票日线数据源。分数为过去 5 年历史分位，不是预测。': [
    "Macro data comes from FRED, the New York Fed, the Federal Reserve Board, the Chicago Fed, and Cboe; cross-asset proxies use Option Pro's current daily equity data feed. Scores are 5-year historical percentiles, not forecasts.",
    'マクロデータは FRED、ニューヨーク連銀、FRB（連邦準備制度理事会）、シカゴ連銀、Cboe から取得しています。クロスアセットの代理指標には Option Pro の現行株式日足データソースを使用します。スコアは過去5年間のヒストリカル・パーセンタイルであり、予測ではありません。',
  ],
  '部分数据缺失': ['Partial data missing', '一部データ欠落'],
  '暂无快照': ['No snapshot yet', 'スナップショットなし'],
  '未启用': ['Disabled', '無効'],
  '刷新宏观数据': ['Refresh macro data', 'マクロデータを更新'],
  '正在提交…': ['Submitting…', '送信中…'],
  '已排入刷新队列': ['Queued for refresh', '更新キューに登録済み'],
  '正在刷新': ['Refreshing', '更新中'],
  '刷新未成功': ['Refresh failed', '更新に失敗'],
  '宏观数据源尚未配置': ['Macro data source not yet configured', 'マクロデータソース未設定'],
  '宏观环境未启用': ['Macro conditions disabled', 'マクロ環境は無効です'],
  '需要在服务器上配置 FRED 数据源密钥后，宏观环境才会开始积累快照。': [
    'Macro conditions will start accumulating snapshots once a FRED data-source key is configured on the server.',
    'サーバー側で FRED データソースキーを設定すると、マクロ環境のスナップショット蓄積が始まります。',
  ],
  '本功能在配置中处于关闭状态。': ['This feature is turned off in the current configuration.', 'この機能は現在の設定でオフになっています。'],
  '配置只能在服务器端完成；页面不显示任何密钥信息。': [
    'Configuration can only be done server-side; this page never displays key information.',
    '設定はサーバー側でのみ行えます。本ページに鍵情報が表示されることはありません。',
  ],
  '刷新冷却中。': ['Refresh is cooling down.', '更新はクールダウン中です。'],
  '服务器尚未配置宏观数据源密钥。': ['The server has not configured a macro data-source key yet.', 'サーバー側でマクロデータソースキーがまだ設定されていません。'],
  'Worker 当前不可用，稍后再试。': ['Worker is currently unavailable — try again later.', 'Worker は現在利用できません。しばらくしてから再度お試しください。'],
  '刷新请求未成功。': ['The refresh request did not succeed.', '更新リクエストは失敗しました。'],
  '宏观环境暂不可用': ['Macro conditions unavailable', 'マクロ環境は利用できません'],
  '宏观环境 · MACRO CONDITIONS': ['Macro conditions', 'マクロ環境 · MACRO CONDITIONS'],
  '联储流动性、融资、国债、利率、信用、风险与外部冲击的 5 年历史分位。': [
    '5-year historical percentiles for Fed liquidity, funding, Treasury, rates, credit, risk, and external shocks.',
    'FRBの流動性・ファンディング・国債・金利・クレジット・リスク・外部ショックに関する過去5年間のヒストリカル・パーセンタイル。',
  ],
  '登录后可手动刷新': ['Sign in to refresh manually', 'ログインすると手動更新が可能です'],
  '上游告警：': ['Upstream warnings: ', 'アップストリーム警告：'],
  '。\n          面板继续显示上一份有效快照。': [
    '. The panel continues to show the last valid snapshot.',
    '。パネルには直近の有効なスナップショットが引き続き表示されます。',
  ],
  '暂无正式综合分': ['No official composite score', '正式な総合スコアなし'],
  '有效模块不足 5 个时不输出正式综合分，也不会用 50 或上一次的分数顶替。': [
    'No official composite score is published when fewer than 5 modules are valid — it is never backfilled with 50 or the previous score.',
    '有効モジュールが5個未満の場合、正式な総合スコアは出力されません。50点や前回スコアで代用することもありません。',
  ],
  '改善最多 · IMPROVING': ['Most improved', '改善幅トップ · IMPROVING'],
  '7 日分数改善最多': ['Biggest 7-day score improvement', '7日間のスコア改善幅トップ'],
  '暂无可比的 7 日历史快照，或本期没有分数上升的因子。缺少比较对象时这里留空，不显示 0。': [
    "No comparable 7-day historical snapshot, or no factors improved this period. When there's nothing to compare against, this is left blank rather than shown as 0.",
    '比較可能な7日前のヒストリカル・スナップショットがないか、今期スコアが上昇したファクターがありません。比較対象がない場合は空欄のままとし、0とは表示しません。',
  ],
  '恶化最多 · DETERIORATING': ['Most deteriorated', '悪化幅トップ · DETERIORATING'],
  '7 日分数恶化最多': ['Biggest 7-day score decline', '7日間のスコア悪化幅トップ'],
  '暂无可比的 7 日历史快照，或本期没有分数下降的因子。缺少比较对象时这里留空，不显示 0。': [
    "No comparable 7-day historical snapshot, or no factors declined this period. When there's nothing to compare against, this is left blank rather than shown as 0.",
    '比較可能な7日前のヒストリカル・スナップショットがないか、今期スコアが下落したファクターがありません。比較対象がない場合は空欄のままとし、0とは表示しません。',
  ],
  '「按当前修订值回算」的历史区间使用今天能看到的最新修订数据，不代表当时市场已知的分数；\n        本地部署后每次实际抓取形成的快照才具备真实的点时语义。': [
    'Historical ranges labeled "recomputed on latest revisions" use the most recent revised data available today — they do not represent the score as known to the market at the time. Only the snapshots actually captured live after this feature launched carry true point-in-time meaning.',
    '「最新修正値による遡及計算」とラベル表示された履歴期間は、本日時点で参照できる最新の修正済みデータを使って計算したものであり、当時市場が実際に知り得たスコアではありません。本機能の稼働後にローカルで実際に取得して作成されたスナップショットのみが、真の時点データとしての意味を持ちます。',
  ],

  /* ============ src/components/market/macro/MacroHistoryChart.tsx ============ */
  '按当前修订值回算': ['Recomputed on latest revisions', '最新修正値による遡及計算'],
  '混合基础': ['Mixed basis', '混在ベース'],
  '综合分（回算）': ['Composite (recomputed)', '総合スコア（遡及計算）'],
  '中性 50': ['Neutral 50', '中立 50'],
  '综合分（本地点时）': ['Composite (local point-in-time)', '総合スコア（ローカル時点）'],
  '宏观环境历史': ['Macro conditions history', 'マクロ環境の履歴'],
  '综合分历史 · COMPOSITE HISTORY': ['Composite history', '総合スコア履歴 · COMPOSITE HISTORY'],
  '历史区间': ['History range', '履歴期間'],
  '历史数据读取失败：': ['Failed to load historical data: ', '履歴データの読み込みに失敗しました：'],
  '历史正在积累：本地快照攒够之后这里会显示综合分曲线。': [
    'History is still accumulating: once enough local snapshots have been captured, the composite score curve will appear here.',
    '履歴データを蓄積中です。ローカルのスナップショットが十分に蓄積されると、ここに総合スコアの推移曲線が表示されます。',
  ],
  '宏观环境综合分历史曲线': ['Macro conditions composite score history chart', 'マクロ環境総合スコアの推移チャート'],
  '叠加模块线': ['Overlay module lines', 'モジュール別ラインを重ねる'],
  '虚线段表示该区间按当前修订值回算，不是当时市场已知的分数；实线段来自本地点时快照。': [
    'Dashed segments indicate that range is recomputed on latest revisions rather than the score as known to the market at the time; solid segments come from local point-in-time snapshots.',
    '破線区間は最新修正値による遡及計算であり、当時市場が知り得たスコアではないことを示します。実線区間はローカル時点スナップショットによるものです。',
  ],

  /* ============ src/components/market/macro/ModuleCard.tsx ============ */
  '有效因子 —': ['Valid factors —', '有効ファクター —'],
  '截止': ['As of', '基準日'],
  '有效因子不足': ['Fewer than', '有効ファクターが'],
  '个门槛，本模块不出分（不按 50 补齐）。': [
    "valid factors, so this module doesn't output a score (it is not backfilled with 50).",
    '件未満のため、このモジュールはスコアを出しません（50点で補うこともありません）。',
  ],

  /* ============ src/components/market/macro/ModuleGrid.tsx ============ */
  '暂无模块分数。数据接入后这里会显示七个模块。': [
    'No module scores yet. Once data is connected, the seven modules will appear here.',
    'モジュールスコアはまだありません。データ接続後、ここに7つのモジュールが表示されます。',
  ],

  /* ============ src/lib/macroFit.ts ============ */
  '中性': ['Neutral', '中立'],
  '顺风': ['Tailwind', '追い風'],
  '逆风': ['Headwind', '逆風'],
  '技术偏强 · 宏观偏强': ['Technical strong · Macro strong', 'テクニカル優勢 · マクロ優勢'],
  '技术偏强 · 宏观偏弱': ['Technical strong · Macro weak', 'テクニカル優勢 · マクロ劣勢'],
  '技术偏弱 · 宏观偏强': ['Technical weak · Macro strong', 'テクニカル劣勢 · マクロ優勢'],
  '技术偏弱 · 宏观偏弱': ['Technical weak · Macro weak', 'テクニカル劣勢 · マクロ劣勢'],
  '趋势与环境共振': ['Trend and backdrop align', 'トレンドと環境が同調'],
  '技术上涨，宏观基础偏弱': ['Price rising, macro foundation weak', '価格は上昇、マクロの基盤は軟調'],
  '宏观先行改善，等待技术修复': ['Macro improving first, technicals yet to follow', 'マクロが先行して改善、テクニカルの修復待ち'],
  '广义风险收缩': ['Broad-based risk contraction', '広範なリスク収縮'],
  '象限以 50 分为界；顺风/逆风的分界线是 65 / 35，两者不同。': [
    'The quadrant splits at a score of 50; the tailwind/headwind threshold is 65/35 — the two are different.',
    '象限は50点を境界とします。追い風・逆風の境界線は65/35であり、両者は異なります。',
  ],
  '宏观模块未启用': ['Macro module disabled', 'マクロモジュールは無効です'],
  '暂无宏观快照': ['No macro snapshot yet', 'マクロスナップショットなし'],
  '该标的未归入板块，无暴露画像': ["This ticker isn't classified into a sector, so there's no exposure profile", 'この銘柄はセクターに分類されていないため、エクスポージャー・プロファイルがありません'],
  '该板块暴露观测不足，不给分': ["This sector's exposure observations are insufficient, so no score is given", 'このセクターのエクスポージャー観測が不足しているため、スコアを算出しません'],
  '宏观适配（0–100）': ['Macro fit (0–100)', 'マクロ適合度（0–100）'],
  '当前宏观环境与该股票所属板块暴露画像的匹配度：把每个宏观因子的历史分位中心化后，': [
    "How well the current macro backdrop matches the exposure profile of the stock's sector: each macro factor's historical percentile is centered,",
    '現在のマクロ環境が、その銘柄が属するセクターのエクスポージャー・プロファイルとどれだけ一致しているかを示します。各マクロファクターのヒストリカル・パーセンタイルを中心化した上で、',
  ],
  '按该板块对这个因子的确定性暴露加权。65 以上记顺风，35 以下记逆风。': [
    "then weighted by that sector's deterministic exposure to the factor. 65 or above is recorded as a tailwind, 35 or below as a headwind.",
    'そのセクターの当該ファクターに対する確定的エクスポージャーで加重します。65以上は追い風、35以下は逆風として記録します。',
  ],
  '影子字段：不参与排名，不改变强度分、突破质量分或事件生命周期。覆盖度不足时不给分，': [
    "Shadow field: it does not participate in ranking and does not change the strength score, breakout quality score, or event lifecycle. No score is given when coverage is insufficient,",
    'シャドウフィールド：ランキングには関与せず、強度スコア・ブレイクアウトの質・イベントのライフサイクルも変更しません。カバレッジが不十分な場合はスコアを算出せず、',
  ],
  '也不按中性 50 计。分数是历史分位，不是预测。': [
    'nor is it backfilled as a neutral 50. The score is a historical percentile, not a forecast.',
    '中立の50点として扱うこともありません。スコアはヒストリカル・パーセンタイルであり、予測ではありません。',
  ],

  /* ============ src/mocks/macro.ts ============ */
  '联储净流动性': ['Fed net liquidity', 'FRB純流動性'],
  '实际利率水平': ['Real rate level', '実質金利水準'],
  '信用': ['Credit', 'クレジット'],
  '风险': ['Risk', 'リスク'],
  '外部冲击': ['External shocks', '外部ショック'],
  '十亿美元': ['USD bn', '10億ドル'],
  '银行准备金': ['Bank reserves', '銀行準備金'],
  '净流动性 13 周动量': ['Net liquidity 13-week momentum', '純流動性13週モメンタム'],
  'TGA 偏离一年中位数': ['TGA deviation from 1-year median', 'TGAの1年中央値からの乖離'],
  '隔夜逆回购缓冲风险': ['Overnight reverse repo buffer risk', '翌日物リバースレポ・バッファーリスク'],
  '抵押品回购摩擦': ['Collateral repo friction', '担保レポ・フリクション'],
  '个百分点': ['pp', 'ポイント'],
  '利率走廊摩擦（SOFR−IORB）': ['Rate corridor friction (SOFR−IORB)', '金利コリドー・フリクション（SOFR−IORB）'],
  '利率走廊摩擦（SOFR−ON RRP）': ['Rate corridor friction (SOFR−ON RRP)', '金利コリドー・フリクション（SOFR−ON RRP）'],
  'EFFR−IORB 价差': ['EFFR−IORB spread', 'EFFR−IORBスプレッド'],
  '商业票据−国库券价差': ['CP−T-bill spread', 'CP−Tビル・スプレッド'],
  '融资分化度（21 日）': ['Funding fragmentation (21-day)', 'ファンディング分断度（21日）'],
  '30 年−10 年期限斜率': ['30Y−10Y term slope', '30年−10年ターム・スロープ'],
  '10 年期利率波动（21 日）': ['10Y rate volatility (21-day)', '10年金利ボラティリティ（21日）'],
  '曲线曲率绝对值': ['Curve curvature (absolute value)', 'カーブ曲率の絶対値'],
  '实际利率曲线（10 年−5 年）': ['Real rate curve (10Y−5Y)', '実質金利カーブ（10年−5年）'],
  '10 年期通胀预期': ['10Y breakeven inflation', '10年ブレークイーブン・インフレ率'],
  '全国金融条件指数': ['National Financial Conditions Index (NFCI)', '全米金融状況指数（NFCI）'],
  '高收益债相对强度': ['High-yield credit relative strength', 'ハイイールド債の相対強度'],
  '投资级债相对强度': ['Investment-grade credit relative strength', '投資適格債の相対強度'],
  '区域银行相对大盘': ['Regional banks vs. broad market', '地方銀行の市場全体対比'],
  'VIX 波动率': ['VIX volatility', 'VIXボラティリティ'],
  'VIX 期限结构': ['VIX term structure', 'VIX期間構造'],
  '风险资产相对避险资产': ['Risk assets relative to safe havens', 'リスク資産の安全資産対比'],
  '高贝塔偏好': ['High-beta preference', '高ベータ選好'],
  '美元广义指数': ['Broad dollar index', 'ブロード・ドル指数'],
  '美元已实现波动（63 日）': ['Dollar realized volatility (63-day)', 'ドル実現ボラティリティ（63日）'],
  'WTI 原油价格': ['WTI crude oil price', 'WTI原油価格'],
  '美元/桶': ['$/bbl', 'ドル/バレル'],
  '原油波动率偏离': ['Oil volatility deviation', '原油ボラティリティ乖離'],
  '天然气价格': ['Natural gas price', '天然ガス価格'],
  '美元/百万英热': ['$/MMBtu', 'ドル/MMBtu'],
  '明显宽松': ['Clearly loose', '明確に緩和的'],
  '偏松': ['Somewhat loose', 'やや緩和的'],
  '偏紧': ['Somewhat tight', 'やや引き締め的'],
  '明显收紧': ['Clearly tight', '明確に引き締め的'],
  'FRED（合成）': ['FRED (synthetic)', 'FRED（合成）'],
  '合成 mock 数据 · 不是真实来源': ['Synthetic mock data · not a real source', '合成モックデータ · 実データではありません'],

  /* ============ src/components/shared/MacroFitBadge.tsx ============ */
  '暂无宏观读数': ['No macro reading yet', 'マクロ読み取りデータなし'],
  '无读数': ['No reading', 'データなし'],

  /* ============ src/components/shared/MacroFitPanel.tsx ============ */
  '宏观适配 · MACRO FIT': ['Macro fit', 'マクロ適合度 · MACRO FIT'],
  '置信度': ['Confidence', '信頼度'],
  '正面': ['Positive', 'ポジティブ'],
  '：': [': ', '：'],
  '负面': ['Negative', 'ネガティブ'],
  '该板块各宏观因子方向不明显': ["No clear directional signal from this sector's macro factors", 'このセクターのマクロファクターに明確な方向性はありません'],
  '技术 − 结构性宏观 =': ['Technical − structural macro =', 'テクニカル − 構造的マクロ ='],
  ' · 价格明显跑在环境前面': [' · Price is clearly running ahead of the backdrop', ' · 価格が環境に明らかに先行'],
  ' · 宏观先行改善，价格未跟上': [" · Macro improving first, price hasn't caught up", ' · マクロが先に改善、価格が追いついていない'],
  '· 不按中性计': ['· not treated as neutral', '· 中立扱いにはしません'],
  '影子字段 · 不参与排名': ['Shadow field · not included in ranking', 'シャドウフィールド · ランキング対象外'],

  /* ============ MacroConditionsPanel.tsx：刷新状态提示（不在原 msgid 清单里，人工补录） ============ */
  '刷新仍未在预期时间内完成，面板会在下一次轮询更新。': [
    "The refresh hasn't finished within the expected time — the panel will update on the next poll.",
    '想定時間内に更新が完了しませんでした。次回のポーリングでパネルが更新されます。',
  ],
  '宏观快照已更新。': ['The macro snapshot has been updated.', 'マクロスナップショットを更新しました。'],
  '刷新冷却中，{n} 秒内只允许一次。': [
    'Refresh is cooling down — only one request is allowed every {n} seconds.',
    '更新はクールダウン中です。{n}秒に1回のみ実行できます。',
  ],
  '已有一次宏观刷新在进行，本次复用同一任务。': [
    'A macro refresh is already in progress — this request reuses that same job.',
    'マクロデータの更新が既に進行中のため、今回のリクエストは同じジョブを再利用します。',
  ],
  '已排入 Worker 队列，完成后面板会在下一次轮询更新。': [
    'Queued on the worker — the panel will update on the next poll once it finishes.',
    'ワーカーのキューに登録しました。完了後、次回のポーリングでパネルが更新されます。',
  ],
};
