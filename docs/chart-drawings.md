# 专业技术分析绘图

手动画线和自动技术结构叠在现有 Apache ECharts 日 K / 面积图上。坐标只存时间和价格，不存屏幕像素。Finviz 仅作交互密度参考，界面仍是 Paper Terminal。

## 工具

| 工具 | 操作 |
| --- | --- |
| 选择 | 点击选中、拖锚点、拖整体；空白处取消；锁定不可拖；隐藏不可点 |
| 水平线 | 单击价格主图，右侧价格标签不压 y 轴刻度 |
| 趋势线段 | 两点；Shift 约束水平 / 垂直 / 45° |
| 向右射线 | 两点定斜率，裁剪在可见价格网格内 |
| 平行通道 | 第三点定宽度，始终保持平行 |
| 矩形 | 两点，反向拖动会规范化 |
| 斐波那契 | 0 / 0.236 / 0.382 / 0.5 / 0.618 / 0.786 / 1，以及 1.272 / 1.618；正反方向都成立 |
| 文字 | 纯文本，最多 240 字，空内容不保存 |
| 回撤尺 | 原有高—低 / 收盘—收盘口径保留；与绘图同一时间只能有一个主动工具 |

绘图只裁在价格主图，不进入成交量副图。K 线与面积模式共享同一组手绘。

## 快捷键

- `Escape`：取消当前绘制，或退出选择 / 全屏工作区
- `Delete` / `Backspace`：删除选中（文本框聚焦时不触发）
- `Ctrl/Cmd+Z`：撤销；`Ctrl/Cmd+Shift+Z` 与 `Ctrl+Y`：重做
- `Shift`：约束角度或大步长
- `Alt`：临时关闭吸附
- 方向键微调，`Shift+方向键` 更大步长

撤销栈只存在当前浏览器会话。普通保存（服务端回写 revision）不入栈。

## 存储与账户隔离

手绘是个人数据。

- 普通账户 Cookie 优先；否则所有者会话使用保留账户 `own_local`
- 两种主体的绘图完全隔离
- 未登录访客 **401**，读不到任何服务器个人绘图
- 访客使用本地键：`option-pro:chart-drawings:v1:anonymous:{ticker}:{range}:raw`
- 匿名数据不上传；登录后以服务器为准，不静默合并；可用「导入本机绘图」
- 范围按 `ticker + range + adjustment` 隔离，不默认跨周期迁移
- 锚点按稳定 `barKey` 重解析；解析不到进入未解析状态，不吸附到附近 K 线
- 日线 / 周线 `barKey` 为纽约交易日；分钟 / 小时用精确时间

自动形态是公开计算结果，**不写入** `accounts.db`。

## Schema

`schemaVersion: 1`。每个对象含 `id`（客户端 UUID）、`ticker`、`range`、`adjustment`、`kind`、`anchors[]`（`time` / `barKey` / `price`）、`style`（颜色十六进制或调色板、宽度 1–4、实线/虚线/点线、可选填充透明度）、可选 `text`、`locked`、`hidden`、`zOrder`、`revision`。

导入 JSON 做 schema / 数量 / 大小校验，不执行表达式、CSS 或 Apache ECharts option。

## API

```
GET    /api/account/chart-drawings?ticker=&range=&adjustment=
POST   /api/account/chart-drawings
POST   /api/account/chart-drawings/replace?ticker=&range=&adjustment=
PUT    /api/account/chart-drawings/{drawing_id}
DELETE /api/account/chart-drawings/{drawing_id}
DELETE /api/account/chart-drawings?ticker=&range=&adjustment=
```

- 严格 Pydantic，`extra="forbid"`
- 修改请求同源（`Origin` + `X-Optix-Action: 1`）
- `Cache-Control: no-store`
- 每范围最多 500 个；每账户 2000 个；单对象 payload ≤ 16KiB
- `revision` 乐观并发：创建为 1；更新带期望 revision；不匹配返回 **409 `revision_conflict`**，前端保留本地副本并让用户选择，不静默覆盖
- **409 不等于版本冲突**：配额满也是 409（`drawings_range_full` / `drawings_full`）。每个错误响应都带机器可读的 `code`，客户端必须按 `code` 分支——只有 `revision_conflict` 才是真冲突。把配额当冲突处理会让「保留本地」把必败的创建无限重放
- **重放幂等**：同一账户重复 POST 同一 `drawing_id` 返回已存的那行（不是 409）；DELETE 一行不存在的绘图是成功（无墓碑）。响应丢失后的重试因此能收敛，而不是把 outbox 卡死在 unsynced
- 导入当前范围是事务替换：先校验全部，再删旧插入新。编号与**同账户的其他范围**冲突时改发新 UUID；跨账户不再冲突（见下）。空列表会清空当前范围。

SQLite 表 `account_chart_drawings` 在 `accounts.db`，WAL、外键、账户删除级联。主键是 **`(user_id, drawing_id)` 复合键**：绘图编号只在账户内唯一，所以两个账户可以各自持有同一个编号，创建他人编号也不再能通过「404 还是 201」反推对方是否存在。该表随功能一起引入且尚未上线，旧结构由 `initialize()` 就地重建；一旦上线，再改主键必须走搬数据的迁移。

## 自动形态与统一图层

`/stocks/{ticker}/technical` 现在附带纯数据合同 `chart_analysis`（`ChartAnalysisBundle`）：`ticker`、`range`、`adjustment`、`dataThrough`、`dates`、`overlays`、`indicatorPanes`、`strengthContext`，以及指纹元信息 `fingerprintAlgorithm` / `barFingerprint` / `barCount` / `firstBarDate` / `lastBarDate`。算法不返回 Apache ECharts `option` / `graphic`。个股图路径不跑 Strength Scanner（`test_scanner_not_imported_by_chart_analysis` 钉住这条：可以用 `strength/scoring` 的 `score_intrinsic`，不能引入 scanner；两边共用的特征构造抽在 `strength/features.py`，避免详情页与雷达的家族分再次漂移）。

日期数组 `dates` **只下发一份**，overlay 几何与副图各自带 `startIndex` 索引进去（`values[i]` 对应 `dates[startIndex + i]`，前置 warmup 的 null 被裁掉：ma200 是 199、macd 35、rsi 14）。形态只经 `overlays` 下发——早先同一份形态编码三遍、日期数组重复十来份，一个响应约 157KB，现在约 69KB。

图层来源：

| 来源 | 画什么 |
| --- | --- |
| `price_action.py` | 摆动点、最近支撑阻力、HH/HL/LH/LL、K 线形态、Spring/Upthrust；每条事件带 `barKey` |
| `base_structure.py` | 整理区 / 箱体的**唯一**事实源（阻力带、支撑带、pivot、invalidation、窗口共识） |
| `vol_price_match.py` | 近 10 日量价摘要；副图提供 OBV / CLV 序列（美元成交额两侧 registry 都没登记，客户端必然丢弃，已停止计算与下发；要加得先补 registry 条目与 i18n） |
| `technical/indicators.py` | MA20/50/200 主图序列；RSI、MACD、Range Position 副图 |
| `strength/scoring.py` 的 `score_intrinsic` | short/mid/long/trend/breakout/price_action 家族分与有效权重只进侧栏 `strengthContext`；`finalScore` / 横截面百分位保持 `None`，**不进** `shapeQuality` |
| 日线突破 | 由基底状态映射 trigger / testing / failed |
| 分钟图（5m/15m/1h，随 K 线按需加载） | VWAP、开盘区间、time-of-day RVOL、末根 CLV、hold bars；挂在图表 payload 的 `chart_analysis` 上 |

自动形态 **v2**（`optix-auto-patterns-v2`）：先按 `data_through` 截断，丢掉未收盘末根后再算 ATR 与 span=2/3/5 摆动。两点只出候选，最终轨来自触点 Theil–Sen 稳健回归；残差用逐根局部 ATR 归一化；触点时间去重（≥3 根）。单支撑只允许 `broken_down`，单阻力只允许 `broken_up`，二者都没有测量目标。通道 / 三角 / 楔形要两侧触点、交替、主体在内、宽度与 apex 合理。箱体不在此模块重复检测。

独立分数（都不是胜率）：`shapeQuality`（几何）、`volumeConfirmation`、`trendAlignment`（只用 MA/RSI/MACD/趋势效率等原始量）、`recency`、`consensus`。显示优先级默认

`0.55 * shapeQuality + 0.15 * volumeConfirmation + 0.15 * trendAlignment + 0.10 * recency + 0.05 * consensus`

量价确认可以改 `displayPriority`，不能改几何。Strength 最终分从不进入 `shapeQuality`。

自动形态画在图上的是 Theil–Sen 拟合轨（`fitAnchors` / `supportRail` / `resistanceRail`），触点 `touchAnchors` 只作解释，不决定画线。

前端只在 `barFingerprint` + `ticker` + `range` + `adjustment` + `dataThrough` 与当前图一致时渲染。指纹是每根分析 K 线 `timestamp|open|high|low|close|volume|ext|quote_only` 的 SHA-256（算法串 `sha256-bar-ohlcv-v1`，随包下发；对不上就不画）。**这道闸门是失败即全隐，所以镜像必须逐位对齐**：后端哈希的是 `clean_series` 之后的行（会丢掉非有限值与 OHLC 不自洽的坏行），六位小数用显式的「远离零」半进位而不是 Python 默认的银行家舍入，两侧各钉同一个字面摘要做跨语言回归。包里带 `barCount` / `firstBarDate` / `lastBarDate`，前端据此按同一窗口取样，并在失配时显示可见的诊断行，而不是整套图层无声消失（CI 在闭市跑，盘中静默熄灯是看不见的）。待同步绘图队列按 `主体+ticker+range+adjustment` 持久化在 `option-pro:chart-drawing-outbox:v1`，与手绘文档和图层设置分开。SPY RS 只在能按日期对齐 SPY 收盘时下发，否则省略空副图。Strength 快照不一致时只显示快照日期，不生成价格几何。未收盘末根不进日线指标与形态。保留 `series_break_at`：断裂之后的一致段才分析。每条自动形态保留自己的 `volumeConfirmation`；量价模块只增加自己的 overlay。摆动点 HH/HL/LH/LL 由相邻已确认高低点比较得出，不是整段结构一个标签。

「算法与图层」菜单由 Layer Registry 生成（不是在 `KlineChart.tsx` 里为每个算法写死开关）。预设：极简 / 结构分析 / 突破交易 / 动量 / 量价 / 全部。极简最多 3 个自动形态、6 个文字标签。设置键 `option-pro:chart-layers:v1:{principal}`，与手绘 `option-pro:chart-drawings:v1:…` 分开；登录主体持久化，访客用 localStorage。RSI/MACD/OBV/CLV/Range Persistence/SPY RS 走独立副图；Strength 标量走侧栏。手绘永远叠在自动层之上。自动层淡色虚线；测试/突破可强调。移动端菜单是底部抽屉。

自动层不可编辑、独立开关、比手绘更淡更虚，且不覆盖现有技术点位开关。

## 运行测试

```bash
# 绘图 API + 自动形态
python -m pytest -q tests/test_account_chart_drawings.py tests/test_auto_technical_patterns.py

# 前端纯函数（几何 / 命中 / 历史 / schema）
node --experimental-strip-types --test frontend-src/tests/chart-drawings.test.mjs
```
