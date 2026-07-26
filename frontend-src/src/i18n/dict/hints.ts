/**
 * 评分口径说明（src/lib/scoreHints.ts）
 * 这些是量化口径的正式说明文字，英日译文保留全部数字与权重，术语按各语言的行业惯用语。
 */
import type { Dict } from './types';

export const HINTS: Dict = {
  '数据缺失的分项不会按中性 50 计入，而是移出权重重新归一，并压低置信度。': [
    'Components with missing data are not scored as a neutral 50 — they are dropped from the weighting, the remaining weights are renormalized, and confidence is lowered.',
    'データが欠けている項目は中立の50点としては扱わず、ウェイトから外して残りを再正規化し、信頼度を引き下げます。',
  ],

  '综合评分（0–100）': ['Composite score (0–100)', '総合スコア（0–100）'],
  '排序分 = 个股自身强度 78% + 市场契合 8% + 风格契合 14%。个股自身强度只用该股价格/量能与对 SPY 的相对表现，由六个族加权：中期 24% / 短期 16% / 趋势 16% / 长期 14% / 突破质量 15% / 价格行为 15%。': [
    "Ranking score = the stock's own strength 78% + market fit 8% + style fit 14%. The stock's own strength uses only its price and volume plus its performance relative to SPY, weighted across six families: mid-term 24% / short-term 16% / trend 16% / long-term 14% / breakout quality 15% / price action 15%.",
    'ランキングスコア = 個別銘柄の強度 78% + 市場適合度 8% + スタイル適合度 14%。個別銘柄の強度は、その銘柄の価格・出来高と SPY に対する相対パフォーマンスのみを使い、6つのファミリーで加重します：中期 24% / 短期 16% / トレンド 16% / 長期 14% / ブレイクアウトの質 15% / プライスアクション 15%。',
  ],

  '短期分（0–100）': ['Short-term score (0–100)', '短期スコア（0–100）'],
  '五项加权：20 日收益 25%、5 日收益 20%、距 20 日均线 20%、RSI14 20%、相对量能 15%。RSI 用折线映射：68 附近得分最高（≈88），越过后逐步回落、78 以上明显走低；相对量能以 1 倍为中性。': [
    'Five weighted inputs: 20-day return 25%, 5-day return 20%, distance from the 20-day MA 20%, RSI(14) 20%, relative volume 15%. RSI is mapped piecewise: the score peaks near 68 (≈88), eases off beyond it, and drops clearly above 78. For relative volume, 1× is neutral.',
    '5項目の加重：20日リターン 25%、5日リターン 20%、20日移動平均との乖離 20%、RSI(14) 20%、相対出来高 15%。RSI は折れ線でマッピングし、68付近で最高点（約88）となり、それを超えると徐々に低下、78以上では明確に下がります。相対出来高は1倍を中立とします。',
  ],

  '中期分（0–100）': ['Mid-term score (0–100)', '中期スコア（0–100）'],
  '五项加权：63 日收益 28%、相对 SPY 63 日强度 27%、均线多头排列 20%、距 50 日均线 15%、MACD 方向 10%。衡量约一个季度维度的动量与相对强弱。': [
    'Five weighted inputs: 63-day return 28%, 63-day relative strength versus SPY 27%, bullish moving-average alignment 20%, distance from the 50-day MA 15%, MACD direction 10%. It measures momentum and relative strength over roughly one quarter.',
    '5項目の加重：63日リターン 28%、SPY に対する63日相対強度 27%、移動平均の上昇配列 20%、50日移動平均との乖離 15%、MACD の方向 10%。約1四半期のスパンでのモメンタムと相対的な強さを測ります。',
  ],

  '长期分（0–100）': ['Long-term score (0–100)', '長期スコア（0–100）'],
  '五项加权：126 日收益 26%、距 200 日均线 24%、252 日收益 22%、距历史高点距离 18%、均线排列 10%。高分代表长期趋势完好且接近前高。': [
    'Five weighted inputs: 126-day return 26%, distance from the 200-day MA 24%, 252-day return 22%, distance from the all-time high 18%, moving-average alignment 10%. A high score means the long-term trend is intact and price is close to its prior high.',
    '5項目の加重：126日リターン 26%、200日移動平均との乖離 24%、252日リターン 22%、過去最高値からの距離 18%、移動平均の配列 10%。高スコアは長期トレンドが崩れておらず、前回高値に近いことを意味します。',
  ],

  '突破质量（0–100）': ['Breakout quality (0–100)', 'ブレイクアウトの質（0–100）'],
  '基础分 = 距历史高点 40% + 20 日收益 40% + 价格行为结构 20%；再按量价匹配修正（吸筹加分、假突破风险减分）。「真空上涨」无跟进封顶 65 分，疑似出货未确认封顶 55 分。': [
    'Base score = distance from the all-time high 40% + 20-day return 40% + price-action structure 20%, then adjusted for volume/price agreement (accumulation adds, false-breakout risk subtracts). A "vacuum rally" with no follow-through is capped at 65; suspected but unconfirmed distribution is capped at 55.',
    '基礎スコア = 過去最高値からの距離 40% + 20日リターン 40% + プライスアクションの構造 20%。そこに出来高と値動きの整合性で補正を加えます（買い集めは加点、ダマシのリスクは減点）。追随のない「真空上昇」は65点、売り抜けの疑いが未確認の場合は55点を上限とします。',
  ],

  '价格行为分（0–100）': ['Price-action score (0–100)', 'プライスアクション・スコア（0–100）'],
  '纯 K 线结构打分（不含量能）：以确认摆动点判定 HH/HL 或 LH/LL 结构——上升结构锚定 82、低点抬升筑底 62、区间 50、下降结构 24；吞没/锤子线/射击之星等形态在锚点上 ±6 微调。': [
    'Scores candlestick structure alone, with no volume input: confirmed swing points classify the structure as HH/HL or LH/LL — an uptrend structure anchors at 82, a higher-low base at 62, a range at 50, and a downtrend structure at 24. Patterns such as engulfing, hammer, and shooting star adjust the anchor by ±6.',
    '出来高を含めず、ローソク足の構造のみで採点します。確定したスイングポイントから HH/HL または LH/LL の構造を判定し、上昇構造は82、安値切り上げの底固めは62、レンジは50、下降構造は24をアンカーとします。つつみ足・ハンマー・シューティングスターなどのパターンでアンカーを±6調整します。',
  ],

  '置信度（0–100%）': ['Confidence (0–100%)', '信頼度（0–100%）'],
  '不是另一种「好坏分」，而是数据覆盖率：配置权重中有多少被真实数据支撑。缺特征、缺历史都会直接压低它。': [
    'This is not a second "good or bad" score — it is data coverage: how much of the configured weighting is backed by real data. Missing features or missing history lower it directly.',
    'これは良し悪しを示す別のスコアではなく、データのカバレッジです。設定したウェイトのうち、どれだけが実データで裏付けられているかを表します。指標や履歴が欠けていれば、その分だけ直接下がります。',
  ],

  '市场环境分（0–100）': ['Market environment score (0–100)', '市場環境スコア（0–100）'],
  '六维加权：指数趋势 30% + 动量 20% + 宽度 20% + 量能 10% + 攻防价差 10% + 风险偏好 10%，再减去风险罚分×0.35。≥75 强风险偏好、≥60 温和偏强、≥40 中性震荡、<40 弱势高风险；核心数据缺失时不出正式分。': [
    'Six weighted dimensions: index trend 30% + momentum 20% + breadth 20% + volume 10% + offense/defense spread 10% + risk appetite 10%, minus the risk penalty × 0.35. 75 or above is strongly risk-on, 60 or above mildly positive, 40 or above neutral and choppy, and below 40 weak with high risk. No official score is published while core data is missing.',
    '6つの軸を加重します：指数トレンド 30% + モメンタム 20% + 市場の広がり 20% + 出来高 10% + 攻守スプレッド 10% + リスク選好 10%。そこからリスク・ペナルティ×0.35 を差し引きます。75以上は強いリスクオン、60以上はやや強気、40以上は中立のもみ合い、40未満は弱くリスクの高い状態です。中核データが欠けている間は正式なスコアを出しません。',
  ],
  '该分同时驱动选股权重的动态调节（如弱市自动降低突破类信号权重）。': [
    'This score also drives dynamic screener weighting — in a weak market, for example, breakout-type signals are automatically given less weight.',
    'このスコアはスクリーナーのウェイト調整も動かします（例：弱い相場ではブレイクアウト系シグナルのウェイトを自動的に下げます）。',
  ],

  '指数趋势（0–100）': ['Index trend (0–100)', '指数トレンド（0–100）'],
  '七个结构性事实的加权是非题：SPY 站上 50/200 日线（各 20%）、QQQ 站上 50/200 日线（各 15%）、IWM 与 RSP 站上 50 日线（各 10%）、SPY 200 日线斜率向上（10%）。是=100、否=0。': [
    'Seven structural yes/no facts, weighted: SPY above its 50- and 200-day MA (20% each), QQQ above its 50- and 200-day MA (15% each), IWM and RSP above their 50-day MA (10% each), and an upward-sloping SPY 200-day MA (10%). Yes = 100, no = 0.',
    '7つの構造的な事実を Yes/No で加重します：SPY が50日線・200日線の上（各20%）、QQQ が50日線・200日線の上（各15%）、IWM と RSP が50日線の上（各10%）、SPY の200日線の傾きが上向き（10%）。Yes=100、No=0 です。',
  ],

  '市场动量（0–100）': ['Market momentum (0–100)', '市場モメンタム（0–100）'],
  '20 日涨跌的加权映射：SPY 35%、QQQ 30%、IWM 15%，再加 QQQ−SPY 与 RSP−SPY 的 20 日相对差各 10%。50 为中性。': [
    'A weighted mapping of 20-day changes: SPY 35%, QQQ 30%, IWM 15%, plus the 20-day relative spreads QQQ−SPY and RSP−SPY at 10% each. 50 is neutral.',
    '20日騰落率を加重してマッピングします：SPY 35%、QQQ 30%、IWM 15%。さらに QQQ−SPY と RSP−SPY の20日相対差を各10%加えます。50が中立です。',
  ],

  '市场宽度（0–100）': ['Market breadth (0–100)', '市場の広がり（0–100）'],
  '11 个板块 ETF 中站上 50 日线的比例 40%、站上 200 日线的比例 25%，加 RSP−SPY 20%、IWM−SPY 15% 的 20 日相对差。衡量上涨是否由少数权重股撑起。': [
    'The share of the 11 sector ETFs above their 50-day MA 40% and above their 200-day MA 25%, plus the 20-day relative spreads RSP−SPY 20% and IWM−SPY 15%. It gauges whether a rally is being carried by only a handful of heavyweights.',
    '11本のセクター ETF のうち50日線を上回る比率 40%、200日線を上回る比率 25%、さらに RSP−SPY 20%、IWM−SPY 15% の20日相対差を加えます。上昇が少数の大型株だけで支えられていないかを測ります。',
  ],

  '量能配合（0–100）': ['Volume confirmation (0–100)', '出来高の裏付け（0–100）'],
  '看 SPY/QQQ 近 5 日方向与相对量能是否同向：上涨且放量加分（SPY +15 / QQQ +10），下跌且放量扣分（−20 / −15），上涨但未显著放量仅小幅加分。基准 50。': [
    'Checks whether the 5-day direction of SPY and QQQ agrees with relative volume: rising on expanding volume adds (SPY +15 / QQQ +10), falling on expanding volume subtracts (−20 / −15), and rising without a clear volume expansion adds only a little. Baseline 50.',
    'SPY と QQQ の直近5日の方向と相対出来高が一致しているかを見ます：上昇かつ出来高増は加点（SPY +15 / QQQ +10）、下落かつ出来高増は減点（−20 / −15）、上昇しても出来高が明確に伸びていない場合は小幅な加点にとどめます。基準は50です。',
  ],

  '攻防价差（0–100）': ['Offense/defense spread (0–100)', '攻守スプレッド（0–100）'],
  '十组进攻/防守资产对的 20 日相对价差矩阵加权（如 QQQ/SPY、SOXX/XLK、HYG/IEF、XLY/XLP、股票/避险资产等）。>50 资金偏进攻，<50 偏防守。': [
    'A weighted matrix of 20-day relative spreads across ten offense/defense asset pairs (QQQ/SPY, SOXX/XLK, HYG/IEF, XLY/XLP, equities versus safe havens, and so on). Above 50, money is leaning offensive; below 50, defensive.',
    '攻めと守りの資産ペア10組について、20日相対スプレッドの行列を加重します（QQQ/SPY、SOXX/XLK、HYG/IEF、XLY/XLP、株式と安全資産など）。50超は資金が攻めに傾き、50未満は守りに傾いていることを示します。',
  ],

  '风险偏好（0–100）': ['Risk appetite (0–100)', 'リスク選好（0–100）'],
  '八项加权：VIX 水平 18% 与一年分位 7%、HYG−TLT 15% 与 HYG−IEF 10% 信用价差、10 年期利率 20 日变化 10%、IEF−TLT 久期 10%、SPY 回撤 18%、QQQ 回撤 12%。VIX>25、信用走弱、深回撤等极端另计罚分（罚分再以 35% 折入总分）。': [
    'Eight weighted inputs: VIX level 18% and its one-year percentile 7%, the HYG−TLT 15% and HYG−IEF 10% credit spreads, the 20-day change in the 10-year yield 10%, IEF−TLT duration 10%, SPY drawdown 18%, QQQ drawdown 12%. Extremes such as VIX above 25, deteriorating credit, and deep drawdowns are counted as a separate penalty, which is folded into the total at 35%.',
    '8項目の加重：VIX の水準 18% と1年パーセンタイル 7%、HYG−TLT 15% と HYG−IEF 10% のクレジット・スプレッド、10年金利の20日変化 10%、IEF−TLT のデュレーション 10%、SPY のドローダウン 18%、QQQ のドローダウン 12%。VIX が25超、クレジットの悪化、深いドローダウンといった極端な状態は別途ペナルティとして計上し、35%を総合スコアに反映します。',
  ],

  '突破质量 · 基底（0–100）': ['Breakout quality · base (0–100)', 'ブレイクアウトの質 · ベース（0–100）'],
  '突破前的基底打分：紧致度 25%、构筑时长 15%、阻力触碰质量 15%、量能收缩 15%、ATR 收缩 10%、支撑完好 10%、相对强度结构 10%。基底越规整、越「收敛」分越高。': [
    'Scores the base built before the breakout: tightness 25%, duration 15%, quality of the resistance touches 15%, volume contraction 15%, ATR contraction 10%, intact support 10%, relative-strength structure 10%. The cleaner and more tightly coiled the base, the higher the score.',
    'ブレイクアウト前のベースを採点します：タイトさ 25%、形成期間 15%、抵抗線へのタッチの質 15%、出来高の収縮 15%、ATR の収縮 10%、サポートの健全性 10%、相対強度の構造 10%。ベースが整い、収縮しているほど高スコアになります。',
  ],

  '确认强度（0–100）': ['Confirmation strength (0–100)', '確認の強さ（0–100）'],
  '突破当下的确认证据：收盘越过阻力区质量 20%、分时相对量能 20%、收盘位置 15%、站稳时长 15%、K 线实体 10%、上影线 10%、相对强度确认 10%。区间保持度可 ±4 分微调。': [
    'The evidence confirming the breakout itself: quality of the close above the resistance zone 20%, intraday relative volume 20%, closing position 15%, time held above the level 15%, candle body 10%, upper wick 10%, relative-strength confirmation 10%. Range retention can adjust the result by ±4 points.',
    'ブレイクアウトそのものを裏付ける証拠：抵抗帯を上抜けた終値の質 20%、日中の相対出来高 20%、終値の位置 15%、上抜けを維持した時間 15%、ローソク足の実体 10%、上ヒゲ 10%、相対強度の確認 10%。レンジの維持度で±4点の微調整が入ります。',
  ],

  '数据可信度（0–100）': ['Data reliability (0–100)', 'データの信頼性（0–100）'],
  '衡量这条信号背后的数据完整度：基底/确认/流动性三族的数据覆盖率均值，再与市场形态置信度按 3:1 合成；缺市场形态时整体打 85 折。它不评价股票好坏，只评价证据是否齐全。': [
    'Measures how complete the data behind this signal is: the mean data coverage of the base, confirmation, and liquidity families, blended 3:1 with market-shape confidence. When market shape is missing, the whole score is scaled to 85%. It does not judge the stock — only whether the evidence is complete.',
    'このシグナルの裏側にあるデータの完全性を測ります：ベース・確認・流動性の3ファミリーのデータカバレッジの平均を取り、市場フォーメーションの信頼度と3:1で合成します。市場フォーメーションが欠ける場合は全体を85%に縮めます。銘柄の良し悪しではなく、証拠が揃っているかだけを評価します。',
  ],

  '追高风险（0–100，越高越危险）': ['Chase risk (0–100, higher is riskier)', '追いかけリスク（0–100、高いほど危険）'],
  '与其他分数方向相反：距枢轴价的涨幅 30%、距 VWAP 20%、跳空/ATR 15%、上影线 15%、短线加速 10%、流动性风险 10%。超过 50 的部分会按 25% 直接从优先级分里扣除。': [
    'This score runs in the opposite direction from the others: gain from the pivot price 30%, distance from VWAP 20%, gap/ATR 15%, upper wick 15%, short-term acceleration 10%, liquidity risk 10%. Whatever exceeds 50 is deducted from the priority score at 25%.',
    '他のスコアとは向きが逆です：ピボット価格からの上昇率 30%、VWAP との乖離 20%、ギャップ/ATR 15%、上ヒゲ 15%、短期の加速 10%、流動性リスク 10%。50を超えた分は25%換算で優先度スコアから直接差し引かれます。',
  ],

  '优先级分（0–100）': ['Priority score (0–100)', '優先度スコア（0–100）'],
  '排序用总分：突破质量 35% + 个股强度 25% + 市场契合 15% + 板块契合 10% + 数据可信度 10% + 事件新鲜度 5%，再减去追高罚分（0.25 × max(追高风险−50, 0)）。': [
    'The total used for ranking: breakout quality 35% + stock strength 25% + market fit 15% + sector fit 10% + data reliability 10% + event freshness 5%, minus the chase penalty (0.25 × max(chase risk − 50, 0)).',
    '並び替えに使う総合スコア：ブレイクアウトの質 35% + 個別銘柄の強度 25% + 市場適合度 15% + セクター適合度 10% + データの信頼性 10% + イベントの新しさ 5%。そこから追いかけペナルティ（0.25 × max(追いかけリスク−50, 0)）を差し引きます。',
  ],

  '突破质量 · 综合（0–100）': ['Breakout quality · composite (0–100)', 'ブレイクアウトの質 · 総合（0–100）'],
  '基底质量 45% + 确认强度 45% + 流动性质量 10% 的加权合成。': [
    'A weighted blend of base quality 45% + confirmation strength 45% + liquidity quality 10%.',
    'ベースの質 45% + 確認の強さ 45% + 流動性の質 10% を加重合成したものです。',
  ],

  '流动性质量（0–100）': ['Liquidity quality (0–100)', '流動性の質（0–100）'],
  '20 日均成交额 25%、当日成交额 20%、成交额分位 15%、点差 10%、盘中数据完整度 10%、市值 10%、资产类型 10%。低流动性标的即使形态好也会被压低。': [
    '20-day average turnover 25%, same-day turnover 20%, turnover percentile 15%, spread 10%, intraday data completeness 10%, market cap 10%, asset type 10%. Illiquid names are marked down even when the setup looks good.',
    '20日平均売買代金 25%、当日の売買代金 20%、売買代金のパーセンタイル 15%、スプレッド 10%、日中データの完全性 10%、時価総額 10%、資産タイプ 10%。流動性の低い銘柄は形が良くてもスコアを下げます。',
  ],

  '个股强度（0–100）': ['Stock strength (0–100)', '個別銘柄の強度（0–100）'],
  '与选股页同一套个股强度分（六族加权），只用该股价格/量能与对 SPY 的相对表现。': [
    'The same stock-strength score used on the Screener page (six weighted families), based only on the stock’s price and volume plus its performance relative to SPY.',
    'スクリーナーページと同じ個別銘柄の強度スコア（6ファミリーの加重）です。その銘柄の価格・出来高と SPY に対する相対パフォーマンスのみを使います。',
  ],

  '顶部分 / 底部分（各 0–100）': ['Top score / bottom score (0–100 each)', '天井スコア / 底スコア（各0–100）'],
  '每个指标独立输出两个证据分：「顶部分」= 该指标支持“过热/接近顶部”的证据强度；「底部分」= 支持“超卖/接近底部”的证据强度。二者互不排斥——例如放量对顶和底都是信号，宽度极弱既提示风险也提示接近底部。': [
    'Every indicator emits two independent evidence scores. The top score is how strongly that indicator supports "overheated / near a top"; the bottom score is how strongly it supports "oversold / near a bottom". The two are not mutually exclusive — a volume surge, for instance, is a signal at tops and bottoms alike, and very weak breadth flags risk while also hinting that a bottom is near.',
    '各指標は2つの証拠スコアを独立に出します。「天井スコア」はその指標が「過熱・天井圏」を支持する強さ、「底スコア」は「売られ過ぎ・底値圏」を支持する強さです。両者は排他ではありません。たとえば出来高の急増は天井でも底でもシグナルになり、市場の広がりが極端に弱い状態はリスクを示すと同時に底が近いことも示唆します。',
  ],
  '证据分只反映单一指标的读数映射，不构成买卖建议。': [
    "An evidence score only reflects how a single indicator's reading is mapped; it is not a buy or sell recommendation.",
    '証拠スコアは単一指標の読み値をマッピングしたものにすぎず、売買の推奨ではありません。',
  ],

  '热点分（0–100）': ['Hotspot score (0–100)', '注目度スコア（0–100）'],
  '事件组热度 = 时效 35%（发布每过 1 小时衰减 2 分）+ 来源广度 20%（30 + 18×来源数）+ 代码关联 15%（45 + 12×关联代码数）+ AI 市场相关性 30%。只加权有证据的分项，证据不足不补中性分。': [
    'Event-group heat = recency 35% (decaying 2 points per hour after publication) + source breadth 20% (30 + 18 × number of sources) + ticker linkage 15% (45 + 12 × number of linked tickers) + AI market relevance 30%. Only components with evidence are weighted; missing evidence is never filled in with a neutral value.',
    'イベントグループの注目度 = 鮮度 35%（公開から1時間ごとに2点減衰）+ ソースの広がり 20%（30 + 18×ソース数）+ ティッカーの紐づけ 15%（45 + 12×関連ティッカー数）+ AI による市場関連度 30%。証拠のある項目だけを加重し、証拠が足りない項目に中立値を埋めることはしません。',
  ],

  '影响分（−5 ～ +5）': ['Impact score (−5 to +5)', 'インパクトスコア（−5 〜 +5）'],
  'AI 分析对单只股票的方向×强度估计：模型输出 −100～+100，界面按 ÷20 显示为 ±5。正=利多、负=利空，绝对值代表预期影响幅度；它是模型判断，不是量价计算，更不是收益预测。': [
    'The AI analysis’s estimate of direction × strength for a single stock. The model outputs −100 to +100; the interface divides by 20 and shows ±5. Positive is bullish, negative is bearish, and the absolute value is the expected size of the impact. This is a model judgment, not a price/volume calculation, and certainly not a return forecast.',
    'AI 分析による個別銘柄の方向×強さの推定です。モデルの出力は −100〜+100 で、画面では20で割って ±5 で表示します。プラスは好材料、マイナスは悪材料で、絶対値は想定されるインパクトの大きさを表します。これはモデルの判断であり、価格・出来高の計算でも、リターンの予測でもありません。',
  ],
  'AI 模型对自身这次判断的把握程度（模型输出 0–100）。低置信度通常意味着新闻信息量不足或含糊，应降低参考权重。': [
    'How confident the AI model is in this particular judgment (the model outputs 0–100). Low confidence usually means the news carried little or ambiguous information, so it deserves less weight.',
    '今回の判断に対する AI モデル自身の確度です（モデル出力は0–100）。信頼度が低い場合、ニュースの情報量が乏しいか曖昧であることが多く、参考度合いを下げるべきです。',
  ],

  '净影响（−5 ～ +5 · 非收益）': ['Net impact (−5 to +5 · not a return)', 'ネット・インパクト（−5 〜 +5 · リターンではありません）'],
  '把窗口内该股全部已分析新闻的影响分聚合成一个净方向：正=利多证据占优，负=利空占优。它衡量新闻面的倾向强度，与股价涨跌幅无关。': [
    'Aggregates the impact scores of every analyzed news item for the stock within the window into a single net direction: positive means bullish evidence dominates, negative means bearish. It measures how the news flow leans, not how far the share price moved.',
    '対象期間内に分析済みのニュースのインパクトスコアを集約し、1つのネット方向にまとめます。プラスは好材料の証拠が優勢、マイナスは悪材料が優勢です。ニュースフローの傾きの強さを示すもので、株価の騰落率とは無関係です。',
  ],

  '趋势偏向分（0–100）': ['Trend bias score (0–100)', 'トレンド・バイアス・スコア（0–100）'],
  '三个指标的偏离合成：相对 SPY 强度×2、MACD 柱×100、(RSI−50)×0.4，求和后加在 50 上（缺项时按剩余项重新归一，至少 2 项才出分）。≥58 判偏多、≤42 判偏空，中间为中性。': [
    'Blends the deviations of three indicators: relative strength versus SPY × 2, the MACD histogram × 100, and (RSI − 50) × 0.4, summed and added to 50. Missing inputs are renormalized across the rest, and at least two are required before a score is produced. 58 or above reads bullish, 42 or below bearish, and anything between is neutral.',
    '3つの指標の偏差を合成します：SPY に対する相対強度×2、MACD ヒストグラム×100、(RSI−50)×0.4 を合計し、50に加算します（欠けた項目は残りで再正規化し、最低2項目そろわないとスコアを出しません）。58以上を強気、42以下を弱気、その間を中立と判定します。',
  ],

  '顶部风险分（0–100）': ['Topping-risk score (0–100)', '天井リスク・スコア（0–100）'],
  '把各指标的「顶部证据分」按六类因子加权聚合：价格过热（均线距离/RSI/20 日涨幅）、宽度背离、期权情绪（VIX 及分位）、波动拐点、利率压力、信用风险。分档解读：<20 顶部风险低，40+ 需要停止追高，60+ 阶段性顶部风险高，80+ 极端过热。': [
    'Aggregates each indicator’s top-evidence score across six factor groups: price overheating (MA distance / RSI / 20-day gain), breadth divergence, options sentiment (VIX and its percentile), volatility inflection, rate pressure, and credit risk. Reading the bands: below 20 topping risk is low, 40+ means stop chasing, 60+ means an interim top is a real risk, and 80+ is extreme overheating.',
    '各指標の「天井証拠スコア」を6つの因子グループで加重集約します：価格の過熱（移動平均との乖離／RSI／20日上昇率）、市場の広がりのダイバージェンス、オプション・センチメント（VIX とそのパーセンタイル）、ボラティリティの転換、金利の圧力、クレジットリスク。目安：20未満は天井リスクが低い、40以上は追いかけを止めるべき水準、60以上は当面の天井リスクが高い、80以上は極端な過熱です。',
  ],

  '底部修复分（0–100）': ['Bottom-formation score (0–100)', '底打ちスコア（0–100）'],
  '各指标「底部证据分」的加权聚合：恐慌释放（VIX）、技术收复（均线/RSI）、宽度修复、波动回落等。分档解读：<20 没有底部迹象，40+ 开始出现底部条件，60+ 阶段性底部概率较高，80+ 恐慌释放充分（仍需价格确认）。': [
    'A weighted aggregate of each indicator’s bottom-evidence score: panic washout (VIX), technical recovery (MA / RSI), breadth repair, cooling volatility, and so on. Reading the bands: below 20 there is no sign of a bottom, 40+ the conditions are starting to appear, 60+ an interim bottom is fairly likely, and 80+ panic has been fully flushed out — price confirmation is still required.',
    '各指標の「底証拠スコア」を加重集約します：パニックの消化（VIX）、テクニカルの回復（移動平均／RSI）、市場の広がりの改善、ボラティリティの低下など。目安：20未満は底の兆しなし、40以上は底の条件が出始めた状態、60以上は当面の底である可能性が高い、80以上はパニックが十分に消化された状態です（それでも価格による確認は必要）。',
  ],

  '数据质量（0–100%）': ['Data quality (0–100%)', 'データ品質（0–100%）'],
  '本次解读中有真实读数的指标占比。缺数据的指标不参与聚合也不冒充中性，占比低时应降低整套解读的参考权重。': [
    'The share of indicators in this reading that have real values. Indicators without data are left out of the aggregate rather than passed off as neutral; when the share is low, give the whole reading less weight.',
    '今回の解釈のうち、実データが取れた指標の比率です。データのない指標は集約に加えず、中立として扱うこともしません。比率が低いときは、解釈全体の参考度合いを下げてください。',
  ],

  '四因子子分（0–100）': ['Four-factor sub-scores (0–100)', '4ファクターのサブスコア（0–100）'],
  '把该股全部信号按 趋势 / 动量 / 量能 / 波动 四组分别聚合出的 0–100 分，用于看偏向分的构成来源。各组内同样只聚合有真实读数的指标。': [
    "Aggregates all of the stock's signals into four groups — trend, momentum, volume, and volatility — each on a 0–100 scale, so you can see where the bias score comes from. Within each group, only indicators with real values are aggregated.",
    'その銘柄の全シグナルを トレンド / モメンタム / 出来高 / ボラティリティ の4グループに分けて0–100で集約し、バイアス・スコアの構成要因を確認できるようにしたものです。各グループ内でも、実データのある指標のみを集約します。',
  ],

  '平均强度（0–100）': ['Average strength (0–100)', '平均強度（0–100）'],
  '组内成分股综合强度分的简单平均（没有分数的标的不计入）。用于横向比较组与组，个股仍以各自分数为准。': [
    "A simple average of the composite strength scores of the group's constituents (names without a score are excluded). Use it to compare one group against another; for an individual stock, go by its own score.",
    'グループ構成銘柄の総合強度スコアの単純平均です（スコアのない銘柄は含めません）。グループ同士の比較に使い、個別銘柄はそれぞれのスコアで判断してください。',
  ],

  '板块内 IV 分位（0–100）': ['IV percentile within the sector (0–100)', 'セクター内 IV パーセンタイル（0–100）'],
  '该股平值期权隐含波动率（ATM IV）在本板块成分股中的百分位：100 = 板块内 IV 最高。它比较的是同板块内的相对贵贱，不是该股自己的历史高低位。': [
    "Where the stock's at-the-money implied volatility (ATM IV) sits among the sector's constituents: 100 is the highest IV in the sector. This compares how expensive it is against its sector peers, not against its own history.",
    'その銘柄の ATM インプライド・ボラティリティ（ATM IV）が、セクター構成銘柄の中で何パーセンタイルに位置するかを示します。100 はセクター内で最も IV が高い状態です。比較しているのは同一セクター内の相対的な割高・割安であり、その銘柄自身の過去水準ではありません。',
  ],

  'ATM IV（%）': ['ATM IV (%)', 'ATM IV（%）'],
  '最接近现价的期权（平值）的隐含波动率年化值。数值越高，说明期权市场预期这只股票后市波动越大。': [
    'The annualized implied volatility of the option closest to the current price (at the money). The higher it is, the more movement the options market expects from this stock.',
    '現在値に最も近い（アット・ザ・マネーの）オプションのインプライド・ボラティリティを年率換算した値です。高いほど、オプション市場がこの銘柄の今後の変動を大きく見込んでいることを意味します。',
  ],

  /* 顶底证据指标逐条口径 */
  '距 20 日均线': ['Distance from the 20-day MA', '20日移動平均との乖離'],
  '现价相对 20 日均线的偏离（%）。大幅高于→顶部证据（×8），大幅低于→底部证据。': [
    'Deviation of the current price from the 20-day MA (%). Far above it counts as top evidence (×8); far below it counts as bottom evidence.',
    '現在値の20日移動平均からの乖離（%）です。大きく上にあれば天井証拠（×8）、大きく下にあれば底証拠として計上します。',
  ],
  '距 50 日均线': ['Distance from the 50-day MA', '50日移動平均との乖離'],
  '现价相对 50 日均线的偏离（%）。映射系数 ×5，方向同上。': [
    'Deviation of the current price from the 50-day MA (%). Mapping factor ×5; direction as above.',
    '現在値の50日移動平均からの乖離（%）です。マッピング係数は×5で、向きは上と同じです。',
  ],
  '距 200 日均线': ['Distance from the 200-day MA', '200日移動平均との乖離'],
  '现价相对 200 日均线的偏离（%）。映射系数 ×2.5，衡量长期趋势的过热/超卖。': [
    'Deviation of the current price from the 200-day MA (%). Mapping factor ×2.5; gauges long-term overheating or oversold conditions.',
    '現在値の200日移動平均からの乖離（%）です。マッピング係数は×2.5で、長期トレンドの過熱・売られ過ぎを測ります。',
  ],
  '高于 50 每一点计 3 分顶部证据，低于 50 每一点计 3 分底部证据；80 附近顶部证据即打满。': [
    'Each point above 50 adds 3 points of top evidence, and each point below 50 adds 3 points of bottom evidence; top evidence maxes out around 80.',
    '50を1ポイント上回るごとに天井証拠を3点、50を1ポイント下回るごとに底証拠を3点計上します。80付近で天井証拠は上限に達します。',
  ],
  '20 日涨跌': ['20-day change', '20日騰落率'],
  '近 20 个交易日涨跌幅（%）。上涨×3.5 计入顶部证据，下跌×3.5 计入底部证据。': [
    'Change over the last 20 trading days (%). Gains count toward top evidence at ×3.5; losses count toward bottom evidence at ×3.5.',
    '直近20営業日の騰落率（%）です。上昇分は×3.5で天井証拠、下落分は×3.5で底証拠に計上します。',
  ],
  'ATR 波动分位': ['ATR volatility percentile', 'ATR ボラティリティのパーセンタイル'],
  '当前波动率在一年中的分位。高分位（>60/70）时顶底证据同时上升——高波动常出现在拐点附近。': [
    'Where current volatility sits within the past year. At a high percentile (above 60–70) both top and bottom evidence rise — high volatility tends to cluster around turning points.',
    '現在のボラティリティが過去1年のどのパーセンタイルにあるかを示します。高パーセンタイル（60〜70超）では天井証拠と底証拠が同時に上がります。高いボラティリティは転換点付近で現れやすいためです。',
  ],
  '成交量 Z 分数': ['Volume z-score', '出来高の Z スコア'],
  '当日量相对 20 日均量的标准化偏离。放量同时增加顶部（×20）与底部（×10）证据：极端量能常伴随顶或底。': [
    "The standardized deviation of today's volume from the 20-day average. A volume surge raises both top (×20) and bottom (×10) evidence: extreme volume tends to accompany tops and bottoms alike.",
    '当日の出来高が20日平均からどれだけ標準化して離れているかを示します。出来高の急増は天井証拠（×20）と底証拠（×10）の両方を増やします。極端な出来高は天井にも底にも伴いやすいためです。',
  ],
  'OBV 背离': ['OBV divergence', 'OBV のダイバージェンス'],
  '20 日内 OBV 与价格的背离度。量在价先弱（正值）计顶部证据，量先强于价计底部证据。': [
    'How far OBV has diverged from price over 20 days. Volume weakening ahead of price (a positive value) counts as top evidence; volume strengthening ahead of price counts as bottom evidence.',
    '20日間の OBV と価格のダイバージェンスの度合いです。出来高が価格に先行して弱まる（プラス値）場合は天井証拠、出来高が価格より先に強まる場合は底証拠として計上します。',
  ],
  '相对 SPY 强度': ['Relative strength versus SPY', 'SPY に対する相対強度'],
  '对 SPY 的相对涨跌（%）。持续跑赢×6 计顶部证据（拥挤/过热），持续跑输计底部证据。': [
    'Change relative to SPY (%). Sustained outperformance counts toward top evidence at ×6 (crowding / overheating); sustained underperformance counts toward bottom evidence.',
    'SPY に対する相対騰落率（%）です。継続的なアウトパフォームは×6で天井証拠（過密・過熱）、継続的なアンダーパフォームは底証拠として計上します。',
  ],
  '收盘位置': ['Closing position', '終値の位置'],
  '收盘价在当日高低区间中的位置（0–100）。收在区间低位（<35）计顶部/转弱证据，收在区间高位（>65）计底部反转确认证据。': [
    "Where the close sits within the day's high–low range (0–100). Closing in the lower part of the range (below 35) counts as top / weakening evidence; closing in the upper part (above 65) counts as confirmation of a reversal off a bottom.",
    '終値が当日の高値〜安値レンジのどこに位置するか（0–100）です。レンジ下方（35未満）で引けた場合は天井・弱化の証拠、レンジ上方（65超）で引けた場合は底からの反転を確認する証拠として計上します。',
  ],
  'MACD 柱': ['MACD histogram', 'MACD ヒストグラム'],
  'MACD 柱状值。转负（动能向下）×150 计顶部证据，转正计底部证据。': [
    'The MACD histogram value. Turning negative (momentum rolling over) counts as top evidence at ×150; turning positive counts as bottom evidence.',
    'MACD ヒストグラムの値です。マイナスへ転じた（モメンタムが下向き）場合は×150で天井証拠、プラスへ転じた場合は底証拠として計上します。',
  ],
  'SPY 距 20 日线': ['SPY distance from its 20-day MA', 'SPY と20日線の乖離'],
  '大盘对 20 日均线的偏离（%）。高于→顶部证据（×8），低于→底部证据。': [
    'The index’s deviation from its 20-day MA (%). Above it counts as top evidence (×8); below it counts as bottom evidence.',
    '指数の20日移動平均からの乖離（%）です。上にあれば天井証拠（×8）、下にあれば底証拠として計上します。',
  ],
  'SPY 距 50 日线': ['SPY distance from its 50-day MA', 'SPY と50日線の乖離'],
  '偏离映射 ×5，衡量中期过热/超卖。': [
    'The deviation is mapped at ×5; it gauges mid-term overheating or oversold conditions.',
    '乖離を×5でマッピングし、中期の過熱・売られ過ぎを測ります。',
  ],
  'SPY 距 200 日线': ['SPY distance from its 200-day MA', 'SPY と200日線の乖離'],
  '偏离映射 ×2.5，衡量长期趋势位置。': [
    'The deviation is mapped at ×2.5; it gauges where the long-term trend stands.',
    '乖離を×2.5でマッピングし、長期トレンドの位置を測ります。',
  ],
  '高于 50 计顶部证据、低于 50 计底部证据，每点 ×3。': [
    'Above 50 counts as top evidence and below 50 as bottom evidence, at ×3 per point.',
    '50超は天井証拠、50未満は底証拠として、1ポイントあたり×3で計上します。',
  ],
  'SPY 20 日涨跌': ['SPY 20-day change', 'SPY の20日騰落率'],
  '上涨×4 计顶部证据，下跌×4 计底部证据。': [
    'Gains count toward top evidence at ×4; losses count toward bottom evidence at ×4.',
    '上昇分は×4で天井証拠、下落分は×4で底証拠に計上します。',
  ],
  'RSP−SPY 5 日': ['RSP−SPY, 5-day', 'RSP−SPY（5日）'],
  '等权落后于市值权重（负值）→龙头集中上涨，计顶部证据 ×15；等权领先计底部修复证据。': [
    'Equal weight lagging cap weight (a negative value) means the rally is concentrated in the leaders and counts as top evidence at ×15; equal weight leading counts as bottom-formation evidence.',
    '均等ウェイトが時価総額ウェイトに劣後する（マイナス値）場合は、上昇が主導銘柄に集中していることを示し、×15で天井証拠として計上します。均等ウェイトが優位な場合は底打ちの証拠になります。',
  ],
  'IWM−SPY 5 日': ['IWM−SPY, 5-day', 'IWM−SPY（5日）'],
  '小盘落后（负值）计顶部证据 ×15，小盘领先计底部证据——口径同 RSP。': [
    'Small caps lagging (a negative value) counts as top evidence at ×15; small caps leading counts as bottom evidence — the same convention as RSP.',
    '小型株が劣後する（マイナス値）場合は×15で天井証拠、小型株が優位な場合は底証拠として計上します。基準は RSP と同じです。',
  ],
  'QQQ−SPY 5 日': ['QQQ−SPY, 5-day', 'QQQ−SPY（5日）'],
  '成长强于大盘（正值）计顶部拥挤证据 ×8，反之计底部证据。': [
    'Growth outperforming the broad market (a positive value) counts as top-crowding evidence at ×8; the reverse counts as bottom evidence.',
    'グロースが市場全体より強い（プラス値）場合は×8で天井の過密証拠、逆の場合は底証拠として計上します。',
  ],
  '板块宽度': ['Sector breadth', 'セクターの広がり'],
  '站上 50 日线的板块比例。低于 45% 计顶部风险，高于 85% 计过热；低于 55% 同时累积底部证据。': [
    'The share of sectors above their 50-day MA. Below 45% counts as topping risk and above 85% as overheating; below 55% also accumulates bottom evidence.',
    '50日線を上回るセクターの比率です。45%未満は天井リスク、85%超は過熱として計上し、55%未満では同時に底証拠も積み上げます。',
  ],
  '低于 15 每点 ×5 计顶部自满证据；高于 20 每点 ×4 计底部恐慌证据。': [
    'Below 15, each point adds 5 points of top-complacency evidence; above 20, each point adds 4 points of bottom-panic evidence.',
    '15を下回ると1ポイントあたり×5で天井の楽観証拠、20を上回ると1ポイントあたり×4で底のパニック証拠として計上します。',
  ],
  'VIX 一年分位': ['VIX one-year percentile', 'VIX の1年パーセンタイル'],
  '分位越低顶部证据越强（×0.8），分位越高底部证据越强。': [
    'The lower the percentile, the stronger the top evidence (×0.8); the higher the percentile, the stronger the bottom evidence.',
    'パーセンタイルが低いほど天井証拠が強くなり（×0.8）、高いほど底証拠が強くなります。',
  ],
  'VIX 5 日变化': ['VIX 5-day change', 'VIX の5日変化'],
  '上行计顶部证据 ×3，下行计底部证据 ×3。': [
    'A rise counts as top evidence at ×3; a decline counts as bottom evidence at ×3.',
    '上昇は×3で天井証拠、下降は×3で底証拠として計上します。',
  ],
  '信用价差（HYG/TLT）': ['Credit spread (HYG/TLT)', 'クレジット・スプレッド（HYG/TLT）'],
  '信用债走弱（负值）计顶部风险证据 ×8；走强计底部修复证据 ×5。': [
    'Credit weakening (a negative value) counts as topping-risk evidence at ×8; credit strengthening counts as bottom-formation evidence at ×5.',
    'クレジットが弱含む（マイナス値）場合は×8で天井リスクの証拠、強含む場合は×5で底打ちの証拠として計上します。',
  ],
  '10 年期利率': ['10-year Treasury yield', '10年債利回り'],
  '高于 4% 每点 ×25 计顶部压力；显著低于 4% 计底部证据。': [
    'Above 4%, each point adds top-pressure evidence at ×25; well below 4% counts as bottom evidence.',
    '4%を上回ると1ポイントあたり×25で天井の圧力として計上し、4%を大きく下回る場合は底証拠として計上します。',
  ],
  '利率 20 日变化': ['Yield 20-day change', '金利の20日変化'],
  '利率上行 ×80 计顶部压力证据，下行计底部证据。': [
    'Rising yields count as top-pressure evidence at ×80; falling yields count as bottom evidence.',
    '金利の上昇は×80で天井の圧力証拠、下降は底証拠として計上します。',
  ],
};
