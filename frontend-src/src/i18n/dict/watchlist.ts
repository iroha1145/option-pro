/**
 * 自选观察页（Watchlist.tsx）：页头带、概览统计条、排序下拉、表格/卡片视图、
 * 增删自选、空态 / 错误态 / 覆盖缺口提示、侧栏（市场信号 / 强度分布 / 市场时钟）。
 */
import type { Dict } from './types';

export const WATCHLIST: Dict = {
  '行情暂时读取失败，自选名单已保留。': ['Quotes could not be loaded. Your watchlist is still saved.', '相場を読み込めません。自選リストは保存されています。'],
  "管理自选": ["Manage watchlist", "自選を管理"],
  "批量添加或移除股票，保存后生效。": ["Add or remove stocks in bulk. Changes take effect when saved.", "銘柄をまとめて追加・削除し、保存すると反映されます。"],
  "添加股票代码": ["Add stock tickers", "銘柄コードを追加"],
  "用逗号、空格或换行分隔，重复代码会自动合并。": ["Separate with commas, spaces or new lines. Duplicates are merged.", "コンマ、空白、改行で区切って入力。重複は自動でまとめられます。"],
  "加入列表": ["Add to list", "リストに追加"],
  "添加默认 4 只": ["Add 4 default tickers", "既定の4銘柄を追加"],
  "全选": ["Select all", "すべて選択"],
  "移除所选（{count}）": ["Remove selected ({count})", "選択を削除（{count}）"],
  "选择 {ticker}": ["Select {ticker}", "{ticker} を選択"],
  "列表为空，也可以保存。": ["The list is empty. You can save an empty watchlist.", "リストは空です。空の状態でも保存できます。"],
  "新增 {add} · 移除 {remove}": ["Add {add} · Remove {remove}", "追加 {add} · 削除 {remove}"],
  "正在保存…": ["Saving…", "保存中…"],
  "保存自选": ["Save watchlist", "自選を保存"],
  "代码格式不正确：{tickers}": ["Invalid ticker format: {tickers}", "コードの形式が正しくありません：{tickers}"],
  "最多保存 {count} 只股票，请先移除一些代码": ["Up to {count} stocks. Remove some tickers first.", "上限は {count} 銘柄です。先に一部のコードを削除してください。"],
  "自选列表返回异常，请重试": ["Invalid watchlist response. Please retry.", "自選データの応答が正しくありません。再試行してください。"],
  "自选修改尚未确认，请重试": ["Watchlist changes could not be confirmed. Please retry.", "自選の変更を確認できませんでした。再試行してください。"],
  "请等待当前操作完成": ["Please wait for the current operation to finish.", "処理が終わるまでお待ちください。"],
  "身份暂时无法确认，请稍后重试": ["Unable to confirm your sign-in status. Please retry shortly.", "ログイン状態を確認できません。しばらくしてから再試行してください。"],
  "登录后加入自选": ["Sign in to add to watchlist", "ログインして自選に追加"],
  "正在读取自选…": ["Loading watchlist…", "自選を読み込み中…"],
  "重试读取自选": ["Retry watchlist", "自選の読み込みを再試行"],
  "加入自选": ["Add to watchlist", "自選に追加"],
  "自选已保存": ["Watchlist saved", "自選を保存しました"],
  "登录后管理自选": ["Sign in to manage watchlist", "ログインして自選を管理"],
  "暂时读不到你的自选列表，请重试。": ["Unable to load your watchlist. Please retry.", "自選リストを読み込めません。再試行してください。"],
  "默认关注 AAPL、MSFT、NVDA、SPY，共 4 只。": ["Default coverage: AAPL, MSFT, NVDA and SPY — 4 tickers.", "既定の注目銘柄は AAPL、MSFT、NVDA、SPY の4銘柄です。"],
  "登录后可保存自己的自选列表。": ["Sign in to save your own watchlist.", "ログインすると自分の自選リストを保存できます。"],
  "自选读取失败": ["Unable to load watchlist", "自選の読み込みに失敗しました"],
  "点击管理自选，添加股票或一次导入多个代码。": ["Open Manage watchlist to add stocks or import multiple tickers.", "「自選を管理」から銘柄の追加やコードの一括入力ができます。"],
  "暂无行情": ["No quotes yet", "相場データなし"],
  '指数': ['Index', '指数'],
  '当日': ['Today', '当日'],
  '日线走势': ['Daily trend', '日足の推移'],
  '近 {count} 个交易日': ['Last {count} trading days', '直近 {count} 営業日'],
  '区间': ['Period', '期間'],
  '暂无日线走势，打开详情后可更新': ['No daily trend yet. Open details to update.', '日足データがありません。詳細画面から更新できます。'],
  '{ticker} 日线走势，{start} 至 {end}，区间涨跌 {change}%': ['{ticker} daily trend, {start} to {end}, period change {change}%', '{ticker} の日足、{start}〜{end}、期間騰落率 {change}%'],

  '只（默认关注池）': ['tickers (default pool)', '銘柄（デフォルト注目プール）'],
  '自选暂时都不在行情覆盖范围内': ['None of your watchlist is covered by quotes yet', 'ウォッチリストの銘柄はまだ相場データの対象外です'],
  '上方列出的代码已保存在账号里，行情覆盖后会自动出现': ['The tickers listed above are saved to your account and will appear once covered', '上に列挙したティッカーはアカウントに保存済みで、対象になり次第表示されます'],
  /* ---------------- B0 页头带 ---------------- */
  '自选观察': ['Your watchlist', 'マイウォッチリスト'],
  '你盯住的票，今天谁在动。': [
    "The stocks you're watching — see who's moving today.",
    '自選銘柄の、今日の値動きをひと目で。',
  ],
  '重新计算完整自选数据': ['Recompute the complete watchlist dataset', '自選データを完全再計算'],
  '登录 Owner 后可强制刷新': ['Sign in as Owner to force-refresh', 'オーナーとしてサインインすると強制更新できます'],
  '强制刷新': ['Force refresh', '強制更新'],

  /* ---------------- B1 概览统计条 ---------------- */
  '市场概览': ['Market overview', '市場概況'],
  '顶部风险分': ['Topping-risk score', '天井リスク・スコア'],
  '市场信号模型': ['Market signal model', '市場シグナルモデル'],
  '底部修复分': ['Bottom-formation score', '底打ちスコア'],
  '上涨 / 下跌': ['Advancers / decliners', '値上がり / 値下がり'],
  '平': ['flat', '横ばい'],
  '全市场平均强度': ['Market-wide average strength', '全市場の平均強度'],

  /* ---------------- B2 工具行：视图切换 / 排序 / 增加自选 ---------------- */
  '自选列表': ['Watchlist', 'ウォッチリスト'],
  '表格': ['Table', 'テーブル'],
  '卡片': ['Cards', 'カード'],
  /* 排序菜单五项，保持终止词一致以呈现整齐的并列集合 */
  '默认排序': ['Default order', 'デフォルト順'],
  '涨幅优先': ['Gainers first', '上昇率順'],
  '跌幅优先': ['Losers first', '下落率順'],
  '强度优先': ['Strength first', '強度順'],
  '按代码 A–Z': ['Ticker A–Z', 'コード順 A–Z'],
  '加自选': ['Add ticker', 'ティッカーを追加'],
  '添加自选股票代码': ['Add a ticker to your watchlist', '自選銘柄のティッカーを追加'],
  '添加': ['Add', '追加'],
  '只标的': ['ticker||tickers', '銘柄'],
  '/ 上限': ['/ max', '/ 上限'],

  /* ---------------- 增删自选：toast ---------------- */
  '移出自选': ['Remove from watchlist', '自選から削除'],
  '已加入自选': ['Added to watchlist', '自選に追加しました'],
  '加入失败': ['Failed to add', '追加に失敗しました'],
  '请稍后再试': ['Please try again shortly.', 'しばらくしてから再試行してください。'],
  '已移出自选': ['Removed from watchlist', '自選から削除しました'],
  '移除失败': ['Failed to remove', '削除に失敗しました'],

  /* ---------------- 强制刷新流程：toast ---------------- */
  '正在重新计算完整自选数据': ['Recomputing the complete watchlist dataset…', '自選データを完全再計算しています…'],
  '刷新任务未能启动': ['The refresh job failed to start', '更新ジョブを開始できませんでした'],
  '自选已更新': ['Watchlist updated', '自選を更新しました'],
  '已读取最新行情数据': ['Latest quotes loaded', '最新の相場データを取得しました'],
  '自选刷新失败': ['Watchlist refresh failed', '自選の更新に失敗しました'],
  '刷新暂时不可用': ['Refresh is temporarily unavailable', '更新は一時的に利用できません'],

  /* ---------------- 覆盖缺口 / 个人自选读取失败 / 默认池提示横幅 ---------------- */
  '暂无行情：': ['No quotes available:', '相場データなし：'],
  '（不在当前覆盖范围内，可在个股页手动获取）': [
    '(Outside current coverage — you can fetch it manually on the ticker page)',
    '（現在のカバー範囲外です。個別銘柄ページで手動取得できます）',
  ],
  '读不到你的自选列表，下面显示的是系统默认关注池。': [
    "We couldn't load your watchlist, so the list below shows the system's default coverage pool.",
    '自選リストを読み込めませんでした。以下はシステムの既定の注目銘柄プールです。',
  ],
  '你还没有自己的自选，下面是系统默认关注池。': [
    "You haven't built a watchlist yet — the list below is the system's default coverage pool.",
    'まだ自分の自選リストがありません。以下はシステムの既定の注目銘柄プールです。',
  ],
  '上方输入代码即可开始建立自己的列表。': [
    'Enter a ticker above to start building your own list.',
    '上部にティッカーを入力すると、自分のリストを作成できます。',
  ],

  /* ---------------- 空态（items.length === 0） ---------------- */
  '清单还是空的': ['Your watchlist is empty', 'ウォッチリストは空です'],
  '在上方输入股票代码，加入你的第一只自选': [
    'Enter a ticker above to add your first stock to your watchlist.',
    '上部にティッカーを入力して、最初の自選銘柄を追加しましょう。',
  ],
  '登录后可以把自选股保存在账号里，换设备也还在': [
    "Sign in to save your watchlist to your account — it'll follow you across devices.",
    'サインインすると自選銘柄がアカウントに保存され、別の端末でも引き継がれます。',
  ],
  '搜索代码': ['Search tickers', 'ティッカーを検索'],
  '还有': ['Another', 'あと'],

  /* ---------------- 表格列标题 ---------------- */
  '最新价': ['Last price', '現在値'],
  '涨跌幅': ['Change %', '騰落率'],
  '今日分时': ['Intraday', '日中値動き'],

  /* ---------------- 侧栏：市场信号 / 市场时钟 ---------------- */
  '市场时钟 · 纽约': ['Market clock · New York', 'マーケットクロック · ニューヨーク'],
  '美东时间 ET': ['Eastern Time (ET)', '米国東部時間（ET）'],
  /* 「距」+「开盘」或「收盘」两段相邻拼接（无占位符模板），译文需要能各自拼出
     自然短语：英文「距」译成带尾随空格的 "Until "；日文语序相反，用「次の」作
     前缀，拼成「次の寄り付き / 次の大引け」，回避"まで"必须后置的语法冲突。 */
  '距': ['Until ', '次の'],
  '开盘': ['Open', '寄り付き'],
  '收盘': ['Close', '大引け'],
  '市场信号 · 模型指标': ['Market signals · model metrics', '市場シグナル · モデル指標'],
  '自选行情读取失败，涨跌家数不可用': ['Watchlist quotes failed to load; advance/decline counts unavailable', 'ウォッチリストの相場取得に失敗したため、騰落銘柄数は利用できません'],
  '市场信号读取失败': ['Failed to load market signals', '市場シグナルの読み込みに失敗しました'],
};
