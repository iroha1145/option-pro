/**
 * 股票详情页（StockDrawerBody / 个股 K 线、手动拉取、新闻、期权链、信号、趋势偏向、AI 股票分析）
 */
import type { Dict } from './types';

export const DETAIL: Dict = {
  /* src/components/StockDrawerBody.tsx */
  '成交量': ['Volume', '出来高'],
  '登录状态已失效': ['Session expired', 'セッションの有効期限切れ'],
  '正在重试': ['Retrying', '再試行中'],
  '信号': ['Signals', 'シグナル'],
  '期权链': ['Options chain', 'オプションチェーン'],
  '· 市值': ['· Market cap', '· 時価総額'],
  '报价更新于': ['Quote updated at', '相場更新'],
  ' · 延迟行情': [' · Delayed quotes', ' · 遅延データ'],
  '近 72 小时无事件 · 雷达仍在盯': ['No events in the past 72 hours · Radar is still watching', '過去72時間はイベントなし · レーダーは監視中'],
  '代码不存在': ['Ticker not found', '銘柄が見つかりません'],
  '请求较频繁': ['Too many requests', 'リクエストが多すぎます'],
  '该标的暂无完整数据': ['No complete data for this ticker', 'この銘柄の完全なデータはありません'],
  '行情服务暂不可用': ['Quote service temporarily unavailable', '相場サービスは一時的に利用できません'],
  '请重新登录后读取个股详情': ['Sign in again to view stock details', '個別銘柄の詳細を見るには、もう一度サインインしてください'],
  '该股票暂无数据，可手动获取最新行情、日线与技术指标': ['This stock has no data yet — you can manually pull the latest quote, daily bars, and technical indicators', 'この銘柄にはまだデータがありません。最新の相場・日足・テクニカル指標を手動で取得できます。'],
  '暂时取不到该股票的行情数据': ['Can\'t fetch this stock\'s quote data right now', '現在この銘柄の相場データを取得できません'],
  '返回自选': ['Back to Watchlist', 'ウォッチリストに戻る'],
  '重新登录': ['Sign in again', 'もう一度サインイン'],
  '当前只有筛选结果里的基础行情，日线与技术指标按实际情况显示': ['Only the basic quote from the screener result is available right now; daily bars and technical indicators are shown as far as they\'re available.', '現在はスクリーナー結果の基礎的な相場データのみです。日足とテクニカル指標は取得できた分だけ表示します。'],
  '行情为延迟数据': ['Quotes are delayed', '相場データは遅延しています'],
  '影响分表示新闻方向，不是收益预测': ['Impact score reflects news direction, not a return forecast', 'インパクトスコアはニュースの方向性を示すもので、リターン予測ではありません'],
  '置信度是模型的把握程度，不是胜率': ['Confidence reflects the model\'s certainty, not a win rate', '信頼度はモデルの確信度を示すもので、勝率ではありません'],

  /* src/components/detail/AiAnalysisCard.tsx */
  'AI 股票分析': ['AI stock analysis', 'AI 株式分析'],
  '开始分析': ['Start analysis', '分析開始'],
  '去登录': ['Sign in', 'サインイン'],
  '将综合': ['This will combine price/volume action, trend bias, and options pricing for', '銘柄'],
  '的量价、趋势偏向与期权定价生成分析报告，消耗 1 次 AI 额度，是否继续？': ['into one analysis report, using 1 AI credit. Continue?', 'の値動き・出来高、トレンドバイアス、オプション価格を総合し、分析レポートを生成します。AIクレジットを1回消費しますが、よろしいですか？'],
  '排队中…': ['Queued…', 'キュー待ち…'],
  '模型正在处理 · 暂无进度百分比': ['Model is processing · No progress percentage available', 'モデル処理中 · 進捗率は未取得'],
  '任务已完成，但未返回可显示的结构化摘要': ['Task completed, but returned no displayable structured summary', 'ジョブは完了しましたが、表示可能な構造化サマリーが返されませんでした。'],

  /* src/components/detail/KeyStats.tsx */
  '今开': ['Open', '始値'],
  '昨收': ['Prev Close', '前日終値'],
  '均量': ['Avg Vol', '平均出来高'],
  '市盈率': ['P/E', 'PER'],
  'IV 百分位': ['IV Percentile', 'IV パーセンタイル'],
  '关键数据': ['Key Stats', '主要指標'],
  '52 周区间': ['52W Range', '52週レンジ'],

  /* src/components/detail/KlineChart.tsx */
  ' · <span style="color:#E8930C">仅报价</span>': [' · <span style="color:#E8930C">Quote only</span>', ' · <span style="color:#E8930C">現在値のみ</span>'],
  '开': ['Open', '始値'],
  '收': ['Close', '終値'],
  '涨跌': ['Chg', '騰落'],
  '量': ['Vol', '出来高'],
  'K 线': ['Candlestick', 'ローソク足'],
  '面积': ['Area', '面グラフ'],
  '数据暂未刷新 · 显示最近一次结果（延迟行情）': ['Data hasn\'t refreshed yet · showing the last available result (delayed quotes)', 'データ未更新 · 直近の結果を表示（遅延データ）'],
  'K 线暂不可用': ['Candlestick chart unavailable', 'ローソク足チャートは利用できません'],
  '为仅报价 bar': ['is a quote-only bar', '現在値のみのバー'],
  '已收齐': ['closed', '確定'],
  '共 {n} 根 · 末根{status}': ['{n} bars total · last bar {status}', '合計{n}本 · 最終足は{status}'],

  /* src/components/detail/ManualStockPull.tsx */
  '日线': ['Daily', '日足'],
  '基础行情': ['Basic quote', '基本株価データ'],
  '技术信号': ['Technical signals', 'テクニカルシグナル'],
  '拉取失败': ['Pull failed', '取得失敗'],
  '已拉取，保存失败': ['Pulled, but failed to save', '取得済み、保存に失敗'],
  '正在拉取真实数据': ['Pulling live data', '実データを取得中'],
  '重新拉取': ['Pull again', '再取得'],
  '拉取真实数据': ['Pull live data', '実データを取得'],
  '获取该股票的最新价格、日线与技术指标': ['Fetch this stock\'s latest price, daily bars, and technical indicators', 'この銘柄の最新価格・日足・テクニカル指標を取得します'],
  '正在更新基础行情、日线与技术信号 · 共 3 项': ['Updating basic quote, daily bars, and technical signals · 3 items total', '基本株価データ・日足・テクニカルシグナルを更新中 · 全3項目'],
  '已完成': ['Done', '完了'],
  ' · 服务器保存失败，重启后可能失效': [' · Server-side save failed — may not survive a restart', ' · サーバー側保存に失敗、再起動後に失われる可能性があります'],

  /* src/components/detail/NewsPanel.tsx */
  'AI 财报影响': ['AI earnings impact', 'AI 決算インパクト'],
  '查看财报页': ['View earnings page', '決算ページを見る'],
  '预期：': ['Expected: ', '予想：'],
  '72 小时内无相关新闻': ['No related news in the past 72 hours', '過去72時間の関連ニュースなし'],
  '新的催化剂出现后将在电报纸上呈现': ['New catalysts will show up on the ticker tape once they land', '新しいカタリストが入り次第、ここに表示されます'],
  '去催化剂页浏览新闻流': ['Browse the news feed on the Catalysts page', 'カタリストページでニュースフィードを見る'],
  '更多相关新闻': ['More related news', '関連ニュースをもっと見る'],

  /* src/components/detail/OptionsPanel.tsx */
  '多空混合': ['Mixed', '強弱混在'],
  '方向未知': ['Direction unknown', '方向不明'],
  '证据一致性高': ['High evidence consistency', '根拠の一貫性：高'],
  '证据一致性中等': ['Medium evidence consistency', '根拠の一貫性：中'],
  '证据一致性低': ['Low evidence consistency', '根拠の一貫性：低'],
  'AI 期权解读': ['AI options insight', 'AIオプション解読'],
  '当前期权链没有达到异动阈值的合约': ['No contracts in the current chain meet the unusual-activity threshold', '現在のオプションチェーンに取引急増のしきい値を満たす銘柄がありません'],
  '生成解读': ['Generate insight', '解読生成'],
  '暂无异动': ['No unusual activity', '取引急増なし'],
  '当前到期日没有达到成交量、成交量/持仓量或估算权利金阈值的合约，未创建付费任务。': ['This expiration has no contracts meeting the volume, volume/open-interest, or estimated-premium thresholds, so no paid task was created.', 'この限月には、出来高・出来高/建玉比率・推定プレミアムのいずれのしきい値も満たす銘柄がなく、有料ジョブは作成されませんでした。'],
  '将提交': ['This will submit real unusual-activity evidence for', '実際の取引急増の根拠を送信します。対象：'],
  '当前到期日的': ['— current expiration,', 'の現在の限月。件数：'],
  '条真实异动证据、标的价和到期日，消耗 1 次模型额度，是否继续？': ['pieces total, plus the underlying price and expiration date — using 1 model credit. Continue?', '件。加えて原資産価格と限月も送信します。モデルクレジットを1回消費しますが、よろしいですか？'],
  '任务失败：': ['Task failed: ', 'ジョブ失敗：'],
  '缺少成交主动方，方向不可判定': ['Missing the trade\'s aggressor side, so direction can\'t be determined', '約定の主導側が不明なため、方向を判定できません'],
  '关键行权价': ['Key strikes', '注目の権利行使価格'],
  '风险说明：': ['Risk note: ', 'リスク注記：'],
  '· 到期': ['· Expiration', '· 限月'],
  '· 输入证据': ['· Evidence input', '· 入力根拠'],
  '重新生成': ['Regenerate', '再生成'],
  '分析已完成，但没有返回可展示的结果。': ['Analysis finished, but returned no displayable result.', '分析は完了しましたが、表示可能な結果が返されませんでした。'],
  '任务': ['Task ', 'ジョブ'],
  '该标的暂无期权数据': ['No options data for this ticker', 'この銘柄のオプションデータはありません'],
  '期权链请求较频繁': ['Too many options-chain requests', 'オプションチェーンのリクエストが多すぎます'],
  '期权数据暂不可用': ['Options data temporarily unavailable', 'オプションデータは一時的に利用できません'],
  '请重新登录后查看期权数据': ['Sign in again to view options data', 'オプションデータを見るには、もう一度サインインしてください'],
  '暂无到期日数据': ['No expiration data', '限月データがありません'],
  '暂未获取到该标的的期权到期日': ['Couldn\'t fetch expiration dates for this ticker', 'この銘柄の限月を取得できませんでした'],
  '选择到期日': ['Select expiration', '限月を選択'],
  '标的价': ['Underlying price', '原資産価格'],
  '· 标的现价不可用，价内侧与平值行未标注': ['· Underlying price unavailable — the ITM side and ATM row aren\'t marked', '· 原資産の現在値が取得できないため、ITM側とATM行はマークされません'],
  ' · 暂未刷新，显示最近一次结果': [' · not yet refreshed — showing the last available result', ' · 未更新、直近の結果を表示'],
  'CALLS · 量/持 · 权利金': ['CALLS · Vol/OI · Premium', 'CALLS · 出来高/建玉 · プレミアム'],
  '行权价': ['Strike', '権利行使価格'],
  '权利金 · 量/持 · PUTS': ['Premium · Vol/OI · PUTS', 'プレミアム · 出来高/建玉 · PUTS'],
  ' · 权利金不可估算（缺买卖价）': [' · Premium not estimable (missing bid/ask)', ' · プレミアムは推定不可（気配値なし）'],
  '成交异动': ['Unusual activity', '取引急増'],
  '浅底为价内（ITM）侧 · 异动标注 vol/oi &gt; 3（倍数为该侧比值）；持仓量为 0 而当日有成交标 ∞（全部新开仓）· 「—」表示上游未提供该字段，不代表 0 · 权利金按买卖中价估算 · 非收益承诺': [
    'Shaded background = the in-the-money (ITM) side · unusual-activity flag when vol/oi > 3 (the multiple is that side\'s ratio); open interest of 0 with same-day trading is flagged ∞ (entirely new positions) · "—" means the upstream feed didn\'t provide this field — it isn\'t 0 · premium is estimated from the bid/ask midpoint · not a promise of returns',
    '背景が薄い方がイン・ザ・マネー（ITM）側 · 出来高/建玉比が3倍を超えると取引急増マーク（倍率はその側の比率）。建玉が0で当日約定があれば∞（すべて新規建玉）· 「—」は上流フィードがこの項目を提供しなかったことを示し、0を意味しません · プレミアムは買値・売値の中値で推定 · リターンを約束するものではありません',
  ],

  /* src/components/detail/SignalList.tsx */
  '已达成': ['Hit', '達成'],
  '近期无信号 · 雷达仍在盯': ['No signals recently · Radar is still watching', '直近のシグナルなし · レーダーは監視中'],
  '突破 / 放量 / 回踩等触发后将在此出现': ['Breakout, volume surge, pullback, and other triggers will show up here', 'ブレイクアウト・出来高急増・押し目などのトリガーが発生すると、ここに表示されます'],

  /* src/components/detail/TrendBiasPanel.tsx */
  '部分指标缺失': ['Some indicators missing', '一部指標が欠測'],
  '数据不足 · 结果仅供参考': ['Insufficient data · Result for reference only', 'データ不足 · 結果は参考値です'],
  '趋势偏向分数据不足': ['Trend bias score data insufficient', 'トレンドバイアススコアのデータ不足'],
  '暂无技术信号数据': ['No technical signal data', 'テクニカルシグナルのデータがありません'],
  '暂无可展示的真实信号因子': ['No real signal factors to show', '表示できる実測シグナル要因がありません'],
  '分项由该股实测信号换算': ['Sub-scores are derived from this stock\'s measured signals', '各項目はこの銘柄の実測シグナルから算出'],
  ' · 缺项显示 — · 非收益预测 · 更新于 ': [' · Missing components show as — · Not a return forecast · Updated at ', ' · 欠測項目は「—」で表示 · リターン予測ではありません · 更新 '],

  /* src/components/detail/api.ts */
  '5分': ['5m', '5分'],
  '15分': ['15m', '15分'],
  '1小时': ['1h', '1時間'],
  '周线': ['Weekly', '週足'],

  /* src/components/detail/optionAnalysis.ts */
  '成交量较高且持仓量较低，待后续持仓量确认': ['Volume is relatively high while open interest is relatively low — pending open-interest confirmation', '出来高が多い一方で建玉が少なく、建玉の確認待ち'],
  '持仓量不可用，无法计算量持比': ['Open interest unavailable — can\'t compute the volume/open-interest ratio', '建玉が取得できないため、出来高/建玉比を計算できません'],
  '缺少成交主动方，无法判断真实交易方向': ['Missing the trade\'s aggressor side — can\'t determine the actual trade direction', '約定の主導側が不明なため、実際の取引方向を判定できません'],

  /* src/components/detail/useAiJob.ts */
  '任务查询失败': ['Failed to check task status', 'ジョブの状態確認に失敗しました'],
  'AI 输出仅供研究 · 影响分为方向性估计 · 非收益预测 · 置信度非胜率': ['AI output is for research only · Impact score is a directional estimate, not a return forecast · Confidence is not a win rate', 'AI出力は研究目的のみです · インパクトスコアは方向性の推定であり、リターン予測ではありません · 信頼度は勝率ではありません'],

  /* src/pages/StockDetail.tsx */
  '行情、技术信号、期权链与相关新闻的全上下文视图 · 数据延迟 15 分钟': ['Full context view of quotes, technical signals, options chain, and related news · Data delayed 15 minutes', '相場・テクニカルシグナル・オプションチェーン・関連ニュースを網羅したビュー · データは15分遅延'],
  '返回': ['Back', '戻る'],
};
