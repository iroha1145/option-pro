import type { Dict } from './types';

export const STOCK_DATA: Dict = {
  '数据覆盖': ['Data coverage', 'データの取得状況'],
  '行情': ['Quotes', '株価'],
  '状态读取失败，稍后自动重试': ['Status unavailable; retrying automatically', '状態を取得できません。後で自動再試行します'],
  '正在读取准备状态': ['Checking data readiness', 'データの準備状況を確認中'],
  '数据已就绪': ['Data ready', 'データ準備完了'],
  '后台准备中 {n}': ['Preparing {n}', '準備中 {n}'],
  '部分缺失 {n}': ['Incomplete {n}', '一部未取得 {n}'],
  '待更新 {n}': ['Awaiting update {n}', '更新待ち {n}'],
  '准备失败 {n}': ['Preparation failed {n}', '準備失敗 {n}'],
  '状态未知 {n}': ['Unknown status {n}', '状態不明 {n}'],
  '暂无日线走势，准备状态读取失败': ['Daily chart unavailable; readiness check failed', '日足データがなく、準備状況も確認できません'],
  '日线准备失败，后台将稍后重试': ['Daily data preparation failed; retrying later', '日足データの準備に失敗しました。後で再試行します'],
  '日线已准备，正在更新图表': ['Daily data ready; updating the chart', '日足データ取得済み。チャートを更新中'],
  '日线读取失败，请稍后重试': ['Daily chart could not be read; please retry later', '日足チャートを取得できません。後で再試行してください'],
  '后台正在准备日线，完成后自动显示': ['Preparing daily data; the chart will appear automatically', '日足データを準備中。完了すると自動表示されます'],
  '暂无日线走势': ['Daily chart unavailable', '日足チャートは未取得です'],
  '{ticker} 日线迷你 K 线图': ['{ticker} daily candlestick mini chart', '{ticker} 日足ミニローソク足チャート'],
  '日线 · 最多 30 个交易日': ['Daily · up to 30 sessions', '日足 · 最大30取引日'],
};
