import type { Dict } from './types';

export const STOCK_DATA: Dict = {
  '数据覆盖': ['Data coverage', 'データの取得状況'],
  '行情': ['Quotes', '株価'],
  '状态读取失败，稍后自动重试': ['Status unavailable; retrying automatically', '状態を取得できません。後で自動再試行します'],
  "正在检查数据": ["Checking data", "データを確認中"],
  '数据已就绪': ['Data ready', 'データ準備完了'],
  "正在获取 {n}": ["Fetching {n}", "取得中 {n}"],
  '部分缺失 {n}': ['Incomplete {n}', '一部未取得 {n}'],
  '待更新 {n}': ['Awaiting update {n}', '更新待ち {n}'],
  "获取失败 {n}": ["Failed to fetch {n}", "取得失敗 {n}"],
  '状态未知 {n}': ['Unknown status {n}', '状態不明 {n}'],
  '暂无日线走势，准备状态读取失败': ['Daily chart unavailable; readiness check failed', '日足データがなく、準備状況も確認できません'],
  "日线获取失败，稍后自动重试": ["Daily data could not be loaded; retrying automatically", "日足を取得できません。後で自動再試行します"],
  "日线已获取，正在更新图表": ["Daily data loaded; updating chart", "日足を取得済み。チャートを更新中"],
  '日线读取失败，请稍后重试': ['Daily chart could not be read; please retry later', '日足チャートを取得できません。後で再試行してください'],
  "正在获取日线，完成后自动显示": ["Loading daily data; the chart will appear when ready", "日足を取得中。完了すると自動表示されます"],
  '暂无日线走势': ['Daily chart unavailable', '日足チャートは未取得です'],
  '{ticker} 日线迷你 K 线图': ['{ticker} daily candlestick mini chart', '{ticker} 日足ミニローソク足チャート'],
  '日线 · 最多 30 个交易日': ['Daily · up to 30 sessions', '日足 · 最大30取引日'],
};
