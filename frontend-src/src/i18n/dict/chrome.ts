/** 全局外壳：导航、命令面板、页脚、Toast、共享空态与错误态 */
import type { Dict } from './types';

export const CHROME: Dict = {
  /* 导航与页面名 */
  '自选': ['Watchlist', 'ウォッチリスト'],
  '选股': ['Screener', 'スクリーナー'],
  '雷达': ['Radar', 'レーダー'],
  '板块': ['Sectors', 'セクター'],
  '财报': ['Earnings', '決算'],
  '催化': ['Catalysts', 'カタリスト'],
  '财报日历': ['Earnings calendar', '決算カレンダー'],
  '即将公布 × AI 影响': ['Upcoming reports × AI impact', '発表予定 × AI インパクト'],
  '新闻催化': ['News catalysts', 'ニュース・カタリスト'],
  '热点 · 情绪新闻流': ['Hotspots · sentiment news feed', '注目テーマ · センチメント付きニュース'],
  'Optix Pro 首页': ['Optix Pro home', 'Optix Pro ホーム'],
  '主导航': ['Main navigation', 'メインナビゲーション'],
  '移动端导航': ['Mobile navigation', 'モバイルナビゲーション'],
  '更多': ['More', 'その他'],
  '更多功能': ['More pages', 'その他の機能'],

  /* 登录状态 */
  '登录': ['Sign in', 'サインイン'],
  '退出': ['Sign out', 'サインアウト'],
  'Owner 已登录': ['Signed in as Owner', 'オーナーとしてサインイン中'],
  '访客只读模式': ['Visitor · read-only', 'ゲスト · 閲覧のみ'],
  '可执行写操作': ['Write actions enabled', '書き込み操作が可能'],
  '登录后可强制刷新与 AI 分析': ['Sign in to force-refresh and run AI analysis', 'サインインすると強制更新と AI 分析が使えます'],
  '已退出 Owner 模式': ['Signed out of Owner mode', 'オーナーモードからサインアウトしました'],
  '当前为访客只读模式': ['Now browsing as a read-only visitor', '現在はゲストの閲覧のみモードです'],

  /* AI 状态胶囊 */
  '分析服务可用': ['Analysis service available', '分析サービスは利用可能'],
  '分析任务处理中': ['Analysis job in progress', '分析ジョブを実行中'],
  '分析服务暂不可用': ['Analysis service temporarily unavailable', '分析サービスは一時的に利用できません'],
  '分析服务未开启': ['Analysis service is off', '分析サービスは無効です'],

  /* 命令面板 */
  '命令面板': ['Command palette', 'コマンドパレット'],
  '打开命令面板': ['Open the command palette', 'コマンドパレットを開く'],
  '搜索': ['Search', '検索'],
  '搜索代码或功能…': ['Search tickers or actions…', 'ティッカーや操作を検索…'],
  '搜索股票代码、名称或功能…': ['Search tickers, names, or actions…', 'ティッカー・銘柄名・操作を検索…'],
  '搜索股票或功能': ['Search tickers or actions', 'ティッカーまたは操作を検索'],
  '搜索中': ['Searching', '検索中'],
  '正在搜索股票目录…': ['Searching the ticker directory…', '銘柄リストを検索中…'],
  '搜索未完成': ['Search did not complete', '検索が完了しませんでした'],
  '没有匹配的结果': ['No matches', '一致する結果がありません'],
  '试试代码': ['Try a ticker', 'ティッカーで検索'],
  '或中文名（英伟达）': ['or a company name (NVIDIA)', 'または銘柄名（エヌビディア）'],
  '股票': ['Stocks', '銘柄'],
  '最近': ['Recent', '最近'],
  '最近查看': ['Recently viewed', '最近見た銘柄'],
  '功能': ['Actions', '操作'],
  '选择': ['Select', '選択'],
  '打开': ['Open', '開く'],
  '关闭': ['Close', '閉じる'],
  '关闭抽屉': ['Close panel', 'パネルを閉じる'],
  '关闭通知': ['Dismiss notification', '通知を閉じる'],
  '强制刷新自选': ['Force-refresh the watchlist', 'ウォッチリストを強制更新'],
  '重新获取自选行情': ['Re-fetch watchlist quotes', 'ウォッチリストの相場を再取得'],
  '退出 Owner 登录': ['Sign out of Owner', 'オーナーからサインアウト'],
  '结束本机会话': ['End the session on this device', 'この端末のセッションを終了'],
  '登录 Owner': ['Sign in as Owner', 'オーナーとしてサインイン'],
  '解锁写操作与 AI 分析': ['Unlock write actions and AI analysis', '書き込み操作と AI 分析を解除'],
  '搜索请求较多，请稍后重试': ['Too many searches. Please try again shortly.', '検索リクエストが多すぎます。しばらくしてから再試行してください。'],
  '搜索请求较多，请 {n} 秒后重试': [
    'Too many searches. Please try again in {n} seconds.',
    '検索リクエストが多すぎます。{n} 秒後に再試行してください。',
  ],
  '登录状态已失效，请重新登录': ['Your session has expired. Please sign in again.', 'セッションの有効期限が切れました。もう一度サインインしてください。'],
  '股票目录暂不可用，请稍后重试': ['The ticker directory is unavailable. Please try again shortly.', '銘柄リストを取得できません。しばらくしてから再試行してください。'],
  '股票搜索失败，请稍后重试': ['Ticker search failed. Please try again shortly.', '銘柄検索に失敗しました。しばらくしてから再試行してください。'],
  '前往{label}页': ['Go to {label}', '{label}へ移動'],

  /* 页脚 / 数据来源声明 */
  '行情为延迟数据 · 仅供研究参考，不构成投资建议': [
    'Quotes are delayed · for research only, not investment advice',
    '相場データは遅延しています · 調査目的のみで、投資助言ではありません',
  ],
  '行情为延迟数据 · 仅供研究参考': ['Quotes are delayed · for research only', '相場データは遅延 · 調査目的のみ'],
  '延迟行情': ['Delayed quotes', '遅延データ'],

  /* 通用空态 / 错误态 */
  '稍后刷新再试': ['Refresh and try again later', '後で更新して再試行してください'],
  '页面加载中': ['Loading page…', 'ページを読み込み中…'],
  '页面渲染出错了': ['This page failed to render', 'このページの表示中にエラーが発生しました'],
  '已拦截为错误卡而非白屏;控制台保留了完整堆栈。': [
    'Caught and shown as an error card instead of a blank screen; the full stack trace is in the console.',
    '白画面にせずエラーカードとして表示しています。完全なスタックトレースはコンソールに出力済みです。',
  ],
  '重新加载': ['Reload', '再読み込み'],
  '未知错误': ['Unknown error', '不明なエラー'],
  '请求失败': ['Request failed', 'リクエストに失敗しました'],
  '市场数据请求过于频繁，请稍后重试': [
    'Too many market-data requests. Please try again shortly.',
    '市場データのリクエストが多すぎます。しばらくしてから再試行してください。',
  ],
  '后台扫描失败': ['The background scan failed', 'バックグラウンドスキャンが失敗しました'],
  '后台扫描等待超时': ['Timed out waiting for the background scan', 'バックグラウンドスキャンの待機がタイムアウトしました'],
  '{feature}暂不可用': ['{feature} is unavailable', '{feature}は利用できません'],

  /* 共享小部件 */
  '涨跌数据缺失': ['Change data unavailable', '騰落データがありません'],
  '涨': ['Up', '上昇'],
  '跌': ['Down', '下落'],
  '实际': ['Actual', '実績'],
  '预估': ['Estimate', '予想'],
  '强度分缺失': ['Strength score unavailable', '強度スコアがありません'],
  '刚刚': ['just now', 'たった今'],
  '{n} 分钟前': ['{n} min ago', '{n}分前'],
  '{n} 小时前': ['{n} h ago', '{n}時間前'],
  '{n} 天前': ['{n} d ago', '{n}日前'],

  /* 指数与时段 */
  '标普500': ['S&P 500', 'S&P500'],
  '纳指综合': ['Nasdaq Composite', 'ナスダック総合'],
  '纳指100': ['Nasdaq 100', 'ナスダック100'],
  '道琼斯': ['Dow Jones', 'ダウ平均'],
  '罗素2000': ['Russell 2000', 'ラッセル2000'],
  '日经225': ['Nikkei 225', '日経225'],
  '上证综指': ['Shanghai Composite', '上海総合'],
  '恐慌指数': ['VIX', 'VIX（恐怖指数）'],
  '费城半导体': ['PHLX Semiconductor', 'SOX（半導体株指数）'],
  '盘中': ['Open', '取引時間中'],
  '盘前': ['Pre-market', 'プレマーケット'],
  '盘后': ['After-hours', '時間外取引'],
  '休市': ['Closed', '休場'],
  '市场时段：{label}': ['Market session: {label}', '市場セッション：{label}'],

  /* 强度分项与预设 */
  '短期': ['Short-term', '短期'],
  '中期': ['Mid-term', '中期'],
  '长期': ['Long-term', '長期'],
  '突破质量': ['Breakout quality', 'ブレイクアウトの質'],
  '稳健': ['Conservative', '安定重視'],
  '均衡': ['Balanced', 'バランス'],
  '进取': ['Aggressive', '積極'],
  '顶部风险': ['Topping risk', '天井リスク'],
  '底部修复': ['Bottom formation', '底打ち'],

  /* 财报影响契约 */
  '财报影响分析返回字段不完整': ['The earnings-impact analysis response is missing fields.', '決算インパクト分析のレスポンスに欠けているフィールドがあります。'],
  '财报影响分析的关联标的字段不完整': ['The related-tickers field of the earnings-impact analysis is incomplete.', '決算インパクト分析の関連銘柄フィールドが不完全です。'],
  '暂无分析摘要': ['No analysis summary yet', '分析サマリーはまだありません'],
  '本地演示关联项': ['Local demo related item', 'ローカルデモの関連項目'],
  '本地演示数据': ['Local demo data', 'ローカルデモデータ'],

  /* 语言切换器 */
  '语言': ['Language', '言語'],
  '界面语言': ['Interface language', '表示言語'],
  '切换界面语言': ['Change interface language', '表示言語を変更'],
  '切换语言后页面会重新加载，以保证全站文案一致': [
    'The page reloads after switching so every label matches the chosen language.',
    '全体の表記をそろえるため、切り替え後にページを再読み込みします。',
  ],
};
