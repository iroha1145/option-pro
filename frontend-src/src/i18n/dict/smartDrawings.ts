import type { Dict } from './types.ts';

export const SMART_DRAWINGS: Dict = {
  "支撑": ["Support", "サポート"],
  "阻力": ["Resistance", "レジスタンス"],
  "实线为水平价位；淡色点线为已失效价位": [
    "Solid: horizontal level; faint dotted: broken level",
    "実線：水平水準、薄い点線：無効化された水準"
  ],
  "智能画线": [
    "Smart lines",
    "スマート描画"
  ],
  "基于已收盘 K 线补充识别、合并重复线；不改变后端信号评分": [
    "Detect and deduplicate on closed bars; backend signal scores are unchanged",
    "確定足で補助検出・重複除去。サーバー側のシグナル評価は変更しません"
  ],
  "实线为结构边界，虚线为延伸；淡色点线为已失效结构": [
    "Solid: fitted boundary; dashed: extension; faint dotted: broken structure",
    "実線：構造境界、破線：延長、薄い点線：無効化された構造"
  ],
  "智能标注仅辅助读图，不是买卖信号": [
    "Smart annotations assist chart reading; they are not trade signals",
    "スマート注釈はチャート読解の補助であり、売買シグナルではありません"
  ],
  "水平支撑": [
    "Horizontal support",
    "水平サポート"
  ],
  "水平阻力": [
    "Horizontal resistance",
    "水平レジスタンス"
  ],
  "下降支撑": [
    "Falling support",
    "下降サポート"
  ],
  "上升阻力": [
    "Rising resistance",
    "上昇レジスタンス"
  ],
  "水平通道": [
    "Sideways channel",
    "横ばいチャネル"
  ]
};
