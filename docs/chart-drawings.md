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
PUT    /api/account/chart-drawings/{drawing_id}
DELETE /api/account/chart-drawings/{drawing_id}
DELETE /api/account/chart-drawings?ticker=&range=&adjustment=
```

- 严格 Pydantic，`extra="forbid"`
- 修改请求同源（`Origin` + `X-Optix-Action: 1`）
- `Cache-Control: no-store`
- 每范围最多 500 个；每账户 2000 个；单对象 payload ≤ 16KiB
- `revision` 乐观并发：创建为 1；更新带期望 revision；不匹配返回 **409**，前端重新拉取，不静默覆盖

SQLite 表 `account_chart_drawings` 在 `accounts.db`，WAL、外键、账户删除级联。旧库原地 `CREATE TABLE IF NOT EXISTS` 升级。

## 自动形态

函数 `detect_auto_patterns` 只吃与 `/stocks/{ticker}/technical` 相同的 **1d + raw** 日线。无第三方、无 LLM、无定时任务、无前视。

识别：上升支撑、下降阻力、升/降通道、对称/上升/下降三角形、升/降楔形、水平箱体。

质量门：触碰次数、ATR 归一化残差、穿透、跨度、平行或收敛。置信度 0–100；界面默认只画 ≥ 70。测量目标标注为 **技术投影，不是价格预测**。算法版本 `optix-auto-patterns-v1`。结构数据与当前 K 线不同源时不画。

自动层不可编辑、独立开关、比手绘更淡更虚，且不覆盖现有技术点位开关。

## 运行测试

```bash
# 绘图 API + 自动形态
python -m pytest -q tests/test_account_chart_drawings.py tests/test_auto_technical_patterns.py

# 前端纯函数（几何 / 命中 / 历史 / schema）
node --experimental-strip-types --test frontend-src/tests/chart-drawings.test.mjs
```
