# Optix 宏观环境 · 架构

中文名：**Optix 宏观环境**　英文名：**Optix Macro Conditions**

一套独立、确定性、可审计的宏观金融环境模块。分数是**过去 5 年滚动历史分位**，
不是预测概率，不代表市场一定上涨，也不构成买入、卖出、仓位或目标价建议。

> 因子范围参考用户提供的公开报告结构，但公式与评分均由 Optix 独立定义。
> 本模块不复刻任何第三方私有算法或权重。

---

## 1. v1 的边界

| 是 | 不是 |
| --- | --- |
| 展示与研究 | 交易信号 |
| 同模块内因子等权、有效模块等权 | 相关性去重、机器学习权重 |
| 独立 SQLite 库 | 新服务、新容器、新 Provider |
| 扩展现有 Market Focus 输入 | 新的 AI Job 类型或 `/api/macro/ai` |

**v1 不写入任何正式股票评分**：`strength` 选股排序与 `breakouts` 突破排名完全不读宏观分。

---

## 2. 组件

```
backend/app/services/macro_conditions/
├── registry.py      唯一事实源：24 个 FRED Series、8 个 ETF、30 个因子、7 个模块、
│                    窗口/阈值/门槛/Regime 分界，全部是版本化常量
├── models.py        不可变模型、状态枚举、History Basis、错误码
├── fred_client.py   官方 FRED API（固定 Origin、单并发、有界响应、指数退避）
├── market_proxy.py  复用现有 Massive→Yahoo 日线链读 8 个 ETF 复权收盘
├── repository.py    /data/macro-conditions.db：Schema、迁移、Revision、快照原子发布
├── alignment.py     统一日度 Score Grid、backward as-of join、新鲜度
├── calculations.py  30 个原始因子
├── scoring.py       5 年滚动分位、模块聚合、Funding EMA(5)、综合分、7 日变化
├── formatting.py    确定性显示单位元数据（UI 不靠因子名猜单位）
└── service.py       回填 / 增量刷新 / 候选快照发布 / API 查询 / AI 上下文
```

```
backend/app/api/macro_conditions.py     5 个端点（4 读 + 1 Owner 刷新）
backend/app/worker/tasks.py             MacroConditionsTask（定时与手动共用一个任务）
frontend-src/src/api/modules/macro.ts   严格字段映射，null 不冒充 0
frontend-src/src/components/market/macro/  面板与子组件
```

---

## 3. 数据流

```
FRED 官方 API ─┐
               ├→ repository（Revision 追加，旧值永不删除）
Massive/Yahoo ─┘        │
   （现有日线链）        ↓
                    alignment（统一日度网格 + backward as-of + 新鲜度）
                        ↓
                    calculations（30 个原始因子）
                        ↓
                    scoring（5 年分位 → 模块 → 综合分 → 7 日变化）
                        ↓
                    候选快照 ──一个事务──→ macro_factor/module/composite_snapshots
                        ↓
              ┌─────────┴─────────┐
        GET /api/macro/**      Market Focus 输入的紧凑宏观块
        （只读 SQLite）          （由确定性代码算好，模型不得修改）
```

**只有 Worker 触网。** 所有 HTTP 读取只打开本地 SQLite：不请求 FRED、不下载 ETF、
不创建 Worker 动作、不花模型额度。

---

## 4. 进程与调度

- 不新增容器。`macro_conditions` 是统一 Worker 里的第 13 个任务。
- 定时与手动**共用同一个任务名**。任务每次返回时按 `config/personal.toml` 的
  `macro.refresh_times_et` 重新对齐到下一个 America/New_York 绝对时刻，所以运行时刻
  不会随失败退避而逐日漂移；Supervisor 本来就会在有排队手动动作时提前唤醒定时循环。
- 减法在 UTC 完成：Python 对同一时区的两个 aware datetime 相减会忽略时区，
  直接相减会在夏令时切换日提前一小时触发。
- `FRED_API_KEY` 缺失时任务返回 `disabled` + `reason=fred_api_key_missing`。
  健康检查只把 `degraded/failed/interrupted` 计为异常，因此**未配置不会让 Worker 变成
  unhealthy**。
- 进程内一把互斥锁 + Worker 动作表的 `already_running` 语义，保证同一时间只有一次刷新。

---

## 5. 失败隔离

| 失败 | 结果 |
| --- | --- |
| 单个 Series 拉取失败 | 其余 Series 正常入库；只影响依赖它的因子；`warnings` 列出 Series ID |
| 单个 ETF 缺失 | 只影响需要它的相对收益因子 |
| 模块有效因子低于门槛 | 该模块不出分（**不补 50**） |
| 有效模块少于 5 个 | 不发布正式综合分；上一份快照继续可读并标 stale/degraded |
| 刷新整体失败 | 旧快照原样保留、可读；不靠删除旧数据来"修复" |
| 宏观库读不出来 | Market Focus 照原样构建输入；宏观块省略而不是补造 |

FRED 失败、ETF 失败、SQLite 失败使用**不同**错误码，绝不混为一谈。

---

## 6. 安全

- `FRED_API_KEY` 是服务端密钥，只存在于 `secrets.env`（0600）与容器环境变量中。
- 网页最多得到 `{"fred": {"configured": true}}`：不含长度、前后缀、Hash、后四位。
- 日志只记录 Series ID 与安全错误码，不记录 Key、请求 URL 或上游响应体。
- 公开响应不含堆栈、上游响应体、Key、数据库路径。
- FRED 客户端 `trust_env=false`、`follow_redirects=false`、固定 HTTPS Origin：
  没有 `FRED_BASE_URL` 环境变量，也没有自定义代理开关。测试用 `httpx.MockTransport`
  替换网络。

---

## 7. 相关文档

- [数据源](data-sources.md)
- [30 个因子](factors.md)
- [评分](scoring.md)
- [点时语义](point-in-time.md)
- [运维](operations.md)
- [现状集成地图](current-integration-map.md)
