/** 板块页：热力矩阵、板块详情、IV 横截面排名面板、侧栏 */
import type { Dict } from './types';

export const SECTORS: Dict = {
  /* DetailBand.tsx —— 板块详情展开带 */
  '板块详情 · 成分股汇总': ['Sector detail · Constituent summary', 'セクター詳細 · 構成銘柄サマリー'],
  '查看该板块扫描结果': ["View this sector's scan results", 'このセクターのスキャン結果を見る'],
  '平均强度': ['Avg strength', '平均強度'],
  '统计覆盖': ['Stats coverage', '統計カバレッジ'],
  '强度领先标的': ['Strength leaders', '強度上位銘柄'],
  '暂无领先标的数据。': ['No leader data available yet.', '上位銘柄データはまだありません。'],
  '强度': ['Strength', '強度'],
  '板块目录成分': ['Sector directory constituents', 'セクターリストの構成銘柄'],
  '板块目录尚未返回成分代码。': ['The sector directory has not returned any constituent tickers yet.', 'セクターリストはまだ構成銘柄のティッカーを返していません。'],
  '本页暂不提供板块资金流、相关性与历史趋势。': ['This page does not yet cover sector fund flows, correlation, or historical trends.', 'このページでは、セクターの資金フロー・相関・過去のトレンドはまだ提供していません。'],

  /* HeatMatrix.tsx —— 板块平均收益热力矩阵 */
  '暂无': ['N/A', 'データなし'],
  '未覆盖': ['No coverage', 'カバレッジなし'],
  '平均收益': ['Avg return', '平均リターン'],
  '· 成分股汇总': ['· Constituent summary', '· 構成銘柄サマリー'],
  '强度领先': ['Strength leader', '強度上位'],
  '板块平均收益热力矩阵': ['Sector average return heatmap', 'セクター平均リターン・ヒートマップ'],

  /* IvPanel.tsx —— 板块 IV 横截面排名面板 */
  '板块 IV 横截面排名': ['Sector IV cross-sectional ranking', 'セクター IV 横断面ランキング'],
  '更新于': ['Updated', '更新'],
  '降序': ['Descending', '降順'],
  '升序': ['Ascending', '昇順'],
  '排位': ['Rank', '順位'],
  '当前 ATM IV 较高的成分在前': ['Constituents with higher current ATM IV listed first', '現在の ATM IV が高い銘柄から表示'],
  '当前 ATM IV 较低的成分在前': ['Constituents with lower current ATM IV listed first', '現在の ATM IV が低い銘柄から表示'],
  '数据暂未刷新，以下为最近一次结果': ['Data has not refreshed yet — showing the most recent result.', 'データはまだ更新されていません。以下は直近の結果です。'],
  'IV 排名暂不可用': ['IV ranking temporarily unavailable', 'IV ランキングは一時的に利用できません'],
  'IV 排名加载失败': ['Failed to load IV ranking', 'IV ランキングの読み込みに失敗しました'],
  '该板块暂无 IV 排名数据': ['No IV ranking data for this sector', 'このセクターの IV ランキングデータはありません'],
  '该板块成分暂无可用的期权样本，可切换板块或重新加载': ["This sector's constituents have no usable options samples right now. Try switching sectors or reloading.", 'このセクターの構成銘柄には現在利用できるオプションのサンプルがありません。セクターを切り替えるか、再読み込みしてください。'],
  '板块 IV 横截面排名表': ['Sector IV cross-sectional ranking table', 'セクター IV 横断面ランキング表'],
  '价': ['Price', '価格'],
  '板块排位': ['Rank within sector', 'セクター内順位'],
  '操作': ['Actions', '操作'],
  '板块排位是同板块成分之间的横向比较，不是该股自己的历史高低位；期权与价格均为延迟数据': ["Sector rank is a cross-sectional comparison among that sector's constituents, not where the stock sits within its own historical range; options and prices are both delayed data.", 'セクター順位は同じセクター内の構成銘柄同士を横断的に比較したものであり、その銘柄自身の過去の高値・安値ではありません。オプションと価格はいずれも遅延データです。'],

  /* SectorChips.tsx —— 板块切换条 */
  '板块切换': ['Sector switcher', 'セクター切り替え'],

  /* SideRail.tsx —— 侧栏：IV 高位卡 + 覆盖卡 */
  '当前 IV 横截面 · 高位': ['Current IV cross-section · Top', '現在の IV 横断面 · 上位'],
  '搜索更多代码并加入自选': ['Search for more tickers to add to your watchlist', '他のティッカーを検索してウォッチリストに追加'],
  '当前没有可用的 IV 样本。': ['No IV samples available right now.', '現在利用できる IV サンプルはありません。'],
  '数据正常': ['Data OK', 'データ正常'],
  'IV 数据覆盖': ['IV data coverage', 'IV データカバレッジ'],
  '所选板块': ['Selected sector', '選択中のセクター'],
  '成功样本': ['Valid samples', '成功サンプル'],
  '横截面最高': ['Cross-section high', '横断面最高'],
  '横截面最低': ['Cross-section low', '横断面最低'],
  '数据时间暂缺。': ['Data timestamp not available yet.', 'データ時刻はまだ取得できていません。'],
  '排位只比较当前板块成分的 ATM IV，不代表一年历史百分位。': ["This rank only compares ATM IV across the current sector's constituents, not a one-year historical percentile.", 'この順位は現在のセクター構成銘柄間の ATM IV を比較するものであり、1 年ヒストリカル・パーセンタイルではありません。'],

  /* model.ts —— 视图模型：周期标签、来源状态 */
  '数据不足': ['Insufficient data', 'データ不足'],
  '1 个月': ['1 month', '1ヶ月'],
  '3 个月': ['3 months', '3ヶ月'],
  '6 个月': ['6 months', '6ヶ月'],
  '数据不完整': ['Incomplete data', 'データ不完全'],
  '数据过期': ['Stale data', 'データ期限切れ'],

  /* Sectors.tsx —— 页面：头部、统计周期条、总览、IV 横截面区、侧栏区 */
  '比较真实的板块平均收益、平均强度与成分覆盖。': ['Compare real sector average returns, average strength, and constituent coverage.', '実際のセクター平均リターン・平均強度・構成銘柄カバレッジを比較します。'],
  '统计时间 —': ['Stats as of —', '統計時点 —'],
  '热力': ['Heat', 'ヒート'],
  '列表': ['List', 'リスト'],
  '收益统计周期': ['Return period', 'リターン集計期間'],
  '数值由板块成分股汇总得出': ['Figures are aggregated from sector constituent stocks.', '数値はセクターの構成銘柄を集計して算出しています。'],
  '板块目录已加载，但收益与强度聚合暂不可用；缺失位置保持为空。': ['The sector directory has loaded, but return and strength aggregation is temporarily unavailable; missing values are left blank.', 'セクターリストは読み込み済みですが、リターンと強度の集計は一時的に利用できません。欠損箇所は空欄のままになります。'],
  '板块总览': ['Sector overview', 'セクター概要'],
  '板块目录加载失败': ['Failed to load sector directory', 'セクターリストの読み込みに失敗しました'],
  '暂无板块目录': ['No sector directory available yet', 'セクターリストはまだありません'],
  '板块目录暂时为空，重试可重新拉取。': ['The sector directory is currently empty. Retry to fetch it.', 'セクターリストは現在空です。再試行すると再取得できます。'],
  'IV 横截面排名': ['IV cross-sectional ranking', 'IV 横断面ランキング'],
  'IV 排名联动暂停': ['IV ranking sync paused', 'IV ランキングの連動を一時停止'],
  '板块目录不可用，无法确定查询范围；恢复目录后自动继续。': ['The sector directory is unavailable, so the query scope cannot be determined; it will resume automatically once the directory recovers.', 'セクターリストが利用できないため、照会範囲を確定できません。リストが復旧すると自動的に再開します。'],
  '暂无可查询的板块': ['No sector available to query', '照会できるセクターがありません'],
  '板块目录没有返回有效编号，未发起 IV 排名请求。': ['The sector directory did not return a valid ID, so no IV ranking request was sent.', 'セクターリストが有効な ID を返さなかったため、IV ランキングのリクエストを送信していません。'],
  '板块 IV 数据': ['Sector IV data', 'セクター IV データ'],
  'IV 数据暂不可用': ['IV data temporarily unavailable', 'IV データは一時的に利用できません'],
  '板块目录不可用，保持空状态；恢复目录后自动继续。': ['The sector directory is unavailable, so this stays empty; it will resume automatically once the directory recovers.', 'セクターリストが利用できないため、空の状態のままです。リストが復旧すると自動的に再開します。'],
  '暂无板块 IV 数据': ['No sector IV data yet', 'セクター IV データはまだありません'],
  '等待板块目录提供有效查询范围，可重试拉取目录。': ['Waiting for the sector directory to provide a valid query scope; you can retry fetching the directory.', 'セクターリストが有効な照会範囲を返すのを待っています。リストの再取得を再試行できます。'],
};
