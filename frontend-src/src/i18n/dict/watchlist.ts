/**
 * 自选观察页（Watchlist.tsx）：页头带、概览统计条、排序下拉、表格/卡片视图、
 * 增删自选、空态 / 错误态 / 覆盖缺口提示、侧栏（市场信号 / 市场时钟）。
 */
import type { Dict } from './types';

export const WATCHLIST: Dict = {
  '撤销信息无效，请重新读取自选': ['Undo data is invalid. Please reload your watchlist.', '元に戻す情報が無効です。ウォッチリストを再読み込みしてください。'],
  '无法撤销，请重新读取关注列表': ['Cannot undo. Please reload your watchlist.', '元に戻せません。ウォッチリストを再読み込みしてください。'],
  '登录身份已变化，请重新操作': ['Your sign-in identity has changed. Please try again.', 'ログイン情報が変わりました。もう一度操作してください。'],
  '登录账号已变化，请重新操作': ['Your sign-in identity has changed. Please try again.', 'ログイン情報が変わりました。もう一度操作してください。'],
  '已恢复关注': ['Restored to watchlist', 'ウォッチリストに戻しました'],
  '恢复失败': ['Could not restore', '元に戻せませんでした'],
  '行情暂时读取失败，关注列表已保留。': ['Quotes could not be loaded. Your watchlist is still saved.', '相場を読み込めません。ウォッチリストは保存されています。'],
  "管理关注": ["Manage watchlist", "ウォッチリストを管理"],
  "添加股票代码": ["Add stock tickers", "銘柄コードを追加"],
  "用逗号、空格或换行分隔，重复代码会自动合并。": ["Separate with commas, spaces or new lines. Duplicates are merged.", "コンマ、空白、改行で区切って入力。重複は自動でまとめられます。"],
  "加入列表": ["Add to list", "リストに追加"],
  "添加默认 4 只": ["Add 4 default tickers", "既定の4銘柄を追加"],
  "全选": ["Select all", "すべて選択"],
  "移除所选（{count}）": ["Remove selected ({count})", "選択を削除（{count}）"],
  "选择 {ticker}": ["Select {ticker}", "{ticker} を選択"],
  "保存后关注列表将为空。": ["Saving will leave your watchlist empty.", "保存するとウォッチリストは空になります。"],
  "新增 {add} · 移除 {remove}": ["Add {add} · Remove {remove}", "追加 {add} · 削除 {remove}"],
  "正在保存…": ["Saving…", "保存中…"],
  "保存关注": ["Save watchlist", "ウォッチリストを保存"],
  "代码格式不正确：{tickers}": ["Invalid ticker format: {tickers}", "コードの形式が正しくありません：{tickers}"],
  "股票代码格式不正确": ["Invalid ticker format.", "銘柄コードの形式が正しくありません。"],
  "请求无法完成": ["The request could not be completed.", "リクエストを完了できませんでした。"],
  "请求无法完成，请重试": ["The request could not be completed. Please retry.", "リクエストを完了できませんでした。再試行してください。"],
  "最多保存 {count} 只股票，请先移除一些代码": ["Up to {count} stocks. Remove some tickers first.", "上限は {count} 銘柄です。先に一部のコードを削除してください。"],
  "最多保存 {count} 只股票，请先移除部分股票": ["Up to {count} stocks. Remove some tickers first.", "上限は {count} 銘柄です。先に一部のコードを削除してください。"],
  "关注列表返回异常，请重试": ["Invalid watchlist response. Please retry.", "ウォッチリストデータの応答が正しくありません。再試行してください。"],
  "关注修改尚未确认，请重试": ["Watchlist changes could not be confirmed. Please retry.", "ウォッチリストの変更を確認できませんでした。再試行してください。"],
  "请等待当前操作完成": ["Please wait for the current operation to finish.", "処理が終わるまでお待ちください。"],
  "身份暂时无法确认，请稍后重试": ["Unable to confirm your sign-in status. Please retry shortly.", "ログイン状態を確認できません。しばらくしてから再試行してください。"],
  "请先登录": ["Please sign in first.", "先にログインしてください。"],
  "登录后加入关注": ["Sign in to add to watchlist", "ログインしてウォッチリストに追加"],
  "正在读取关注…": ["Loading watchlist…", "ウォッチリストを読み込み中…"],
  "重新读取": ["Reload", "再読み込み"],
  "加入关注": ["Add to watchlist", "ウォッチリストに追加"],
  "关注已保存": ["Watchlist saved", "ウォッチリストを保存しました"],
  "登录后管理关注": ["Sign in to manage watchlist", "ログインしてウォッチリストを管理"],
  "暂时读不到关注列表，请重试。": ["Unable to load your watchlist. Please retry.", "ウォッチリストを読み込めません。再試行してください。"],
  "关注读取失败": ["Unable to load watchlist", "ウォッチリストの読み込みに失敗しました"],
  "点击管理关注，添加股票或一次输入多个代码。": ["Open Manage watchlist to add stocks or import multiple tickers.", "「ウォッチリストを管理」から銘柄の追加やコードの一括入力ができます。"],
  "暂无行情": ["No quotes yet", "相場データなし"],
  '指数': ['Index', '指数'],
  '当日': ['Today', '当日'],
  '每日走势': ['Daily trend', '日足の推移'],
  '近 {count} 个交易日': ['Last {count} trading days', '直近 {count} 営業日'],
  '区间': ['Period', '期間'],
  '近半年': ['Past 6 months', '過去6か月'],
  '{start} 至 {end}，区间涨跌 {change}%': ['{start} to {end}, period change {change}%', '{start}〜{end}、期間騰落率 {change}%'],
  '{ticker} 每日走势，{start} 至 {end}，区间涨跌 {change}%': ['{ticker} daily trend, {start} to {end}, period change {change}%', '{ticker} の日足、{start}〜{end}、期間騰落率 {change}%'],

  "只（默认关注）": ["stocks (default watchlist)", "銘柄（標準リスト）"],
  /* ---------------- B0 页头带 ---------------- */
  "更新关注股票的行情与评分": ["Update watchlist quotes and scores", "ウォッチリストの株価とスコアを更新"],
  "管理员登录后可更新数据": ["Sign in as an administrator to update data", "管理者としてログインするとデータを更新できます"],
  '更新数据': ['Update data', 'データを更新'],

  /* ---------------- B1 概览统计条 ---------------- */
  '市场概览': ['Market overview', '市場概況'],
  '顶部风险分': ['Topping-risk score', '天井リスク・スコア'],
  '市场信号模型': ['Market signal model', '市場シグナルモデル'],
  '底部修复分': ['Bottom-formation score', '底打ちスコア'],
  '上涨 / 下跌': ['Advancers / decliners', '値上がり / 値下がり'],
  '平': ['flat', '横ばい'],

  /* ---------------- B2 工具行：视图切换 / 排序 / 增加自选 ---------------- */
  '关注列表': ['Watchlist', 'ウォッチリスト'],
  '表格': ['Table', 'テーブル'],
  '卡片': ['Cards', 'カード'],
  /* 排序菜单五项，保持终止词一致以呈现整齐的并列集合 */
  '默认排序': ['Default order', 'デフォルト順'],
  '涨幅优先': ['Gainers first', '上昇率順'],
  '跌幅优先': ['Losers first', '下落率順'],
  '评分优先': ['Score first', 'スコア順'],
  '代码顺序': ['Ticker A–Z', 'コード順 A–Z'],
  '添加': ['Add', '追加'],
  '只标的': ['ticker||tickers', '銘柄'],
  '/ 上限': ['/ max', '/ 上限'],

  /* ---------------- 增删自选：toast ---------------- */
  '移除关注': ['Remove from watchlist', 'ウォッチリストから削除'],
  '取消关注': ['Remove from watchlist', 'ウォッチリストから削除'],
  '已关注': ['Added to watchlist', 'ウォッチリストに追加しました'],
  '加入关注失败': ['Failed to add to watchlist', 'ウォッチリストへの追加に失敗しました'],
  '请稍后再试': ['Please try again shortly.', 'しばらくしてから再試行してください。'],
  '已移除关注': ['Removed from watchlist', 'ウォッチリストから削除しました'],
  '移除失败': ['Failed to remove', '削除に失敗しました'],

  /* ---------------- 强制刷新流程：toast ---------------- */
  "正在更新关注股票的行情与评分": ["Updating watchlist quotes and scores", "ウォッチリストの株価とスコアを更新中"],
  '更新任务未能启动': ['The refresh job failed to start', '更新ジョブを開始できませんでした'],
  '关注数据已更新': ['Watchlist updated', 'ウォッチリストを更新しました'],
  '已读取最新行情数据': ['Latest quotes loaded', '最新の相場データを取得しました'],
  '关注数据更新失败': ['Watchlist refresh failed', 'ウォッチリストの更新に失敗しました'],
  '数据更新暂不可用': ['Refresh is temporarily unavailable', '更新は一時的に利用できません'],

  /* ---------------- 覆盖缺口 / 个人自选读取失败 / 默认池提示横幅 ---------------- */
  '暂无行情：': ['No quotes available:', '相場データなし：'],
  '（暂无覆盖数据，可在股票详情页手动获取）': [
    '(Outside current coverage — you can fetch it manually on the ticker page)',
    '（現在のカバー範囲外です。個別銘柄ページで手動取得できます）',
  ],

  /* ---------------- 空态（items.length === 0） ---------------- */
  '暂无关注': ['Your watchlist is empty', 'ウォッチリストは空です'],
  '登录后可将关注股票保存在账号里，换设备也能查看。': [
    "Sign in to save your watchlist to your account — it'll follow you across devices.",
    'サインインすると登録銘柄がアカウントに保存され、別の端末でも引き継がれます。',
  ],
  '搜索代码': ['Search tickers', 'ティッカーを検索'],
  '还有 {n} 只': ['{n} more', 'あと {n} 銘柄'],

  /* ---------------- 表格列标题 ---------------- */
  '最新价': ['Last price', '現在値'],
  '涨跌幅': ['Change %', '騰落率'],

  /* ---------------- 侧栏：市场信号 / 市场时钟 ---------------- */
  '市场时钟 · 纽约': ['Market clock · New York', 'マーケットクロック · ニューヨーク'],
  /* 「距」+「开盘」或「收盘」两段相邻拼接（无占位符模板），译文需要能各自拼出
     自然短语：英文「距」译成带尾随空格的 "Until "；日文语序相反，用「次の」作
     前缀，拼成「次の寄り付き / 次の大引け」，回避"まで"必须后置的语法冲突。 */
  '距': ['Until ', '次の'],
  '开盘': ['Open', '寄り付き'],
  '收盘': ['Close', '大引け'],
  '市场分析': ['Market analysis', '市場分析'],
  '关注行情读取失败，暂不显示涨跌家数': ['Watchlist quotes failed to load; advance/decline counts unavailable', 'ウォッチリストの相場取得に失敗したため、騰落銘柄数は利用できません'],
  '市场分析读取失败': ['Failed to load market analysis', '市場分析の読み込みに失敗しました'],
  '正在读取交易时段…': ["Loading trading session…", "取引時間帯を取得中…"],
  '股票代码': ['Ticker', '銘柄'],
};
