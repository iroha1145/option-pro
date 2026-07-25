# Optix 宏观环境 · 运维

## 1. 启用

```bash
# 1) 在服务器上配置 FRED 密钥（从标准输入读，永不回显）
#    set 会当场校验 FRED 的确切形态（32 位小写字母数字），不符合就报出实际长度并拒收。
#    隐藏提示符不回显任何字符，盲输很容易重复粘贴；先确认长度更稳妥：
#      read -rs -p 'FRED key: ' K; echo; echo "长度 = ${#K}"   # 必须是 32
#      printf '%s\n' "$K" | ./personal.sh secrets set FRED_API_KEY && unset K
./personal.sh secrets set FRED_API_KEY

# 2) 确认已配置（只回布尔，不回值）
./personal.sh secrets status

# 3) 确认 config/personal.toml 的 [macro] 段
#    enabled / history_years / score_window_years / funding_ema_days /
#    refresh_times_et / manual_refresh_cooldown_seconds / scoring_version

# 4) 部署（会重建镜像；secrets set 只重建容器，不重建镜像）
./scripts/deploy.sh
```

`./personal.sh secrets set FRED_API_KEY` 只会重建/重启 `backend` 与 `worker`。

未配置密钥时：`macro_conditions` 任务返回 `disabled` +
`reason=fred_api_key_missing`，Worker 整体**仍然 healthy**；`GET /api/macro/conditions`
返回 `status=disabled`，页面显示配置提示且不显示任何密钥管理入口。

---

## 2. 配置

```toml
[macro]
enabled = true
history_years = 8
score_window_years = 5
funding_ema_days = 5
refresh_times_et = ["08:30", "18:30"]
manual_refresh_cooldown_seconds = 300
scoring_version = "optix-macro-score-v1"
```

校验规则：

1. `history_years >= score_window_years`
2. `history_years` ∈ [5, 15]
3. `score_window_years` ∈ [3, 10]
4. `refresh_times_et` 必须是合法 `HH:MM`
5. 时刻不允许重复
6. `scoring_version` 必须等于代码常量，**不能通过配置伪造算法版本**

**不可配置**（版本化常量，改动必须改代码并过测试）：Series ID、各公式、
stale threshold、`minimum_history`、模块最低因子数量、Regime 阈值、ON RRP 风险函数、
2% Breakeven 目标、63/21/252 日窗口、模块权重。

---

## 3. Worker 任务

统一 Worker 现在报告 **13 项**任务，`macro_conditions` 是第 13 项：

```
breakout · catalyst_sync · focus · ai_jobs · maintenance · stock_directory
public_home · earnings_analysis · macro_conditions
focus_refresh · strength_refresh · breakout_refresh · retention
```

- 定时与手动**共用这一个任务**（不存在第二个宏观任务名）。
- 每次返回时按 `refresh_times_et` 对齐到下一个 America/New_York 绝对时刻，
  运行时刻不随退避漂移。
- Supervisor 在有排队手动动作时会提前唤醒定时循环。
- 首次运行缺少历史时执行 8 年回填；之后只拉增量 + 180 天修订窗口。

健康检查：

```bash
./scripts/compose.sh exec -T worker python -m app.worker --healthcheck
```

---

## 4. 手动刷新

Owner 在 `/market` 页宏观区块点「刷新宏观数据」，或直接调用：

```bash
curl -sS -X POST https://<host>/api/macro/conditions/refresh \
  -H 'Content-Type: application/json' \
  -H 'X-Optix-Action: 1' \
  -H "Origin: https://<host>" \
  --cookie 'optix_owner=<session>' \
  -d '{}'
```

语义：

| 返回 | 含义 |
| --- | --- |
| `202` + `reason=queued` | 已入队 |
| `200` + `reason=idempotent` | 同一 `idempotency_key` 复用既有请求 |
| `200` + `reason=already_running` + `error_code=macro_refresh_in_progress` | 已有刷新在跑 |
| `200` + `reason=cooldown` + `error_code=macro_refresh_cooldown` | 300 秒冷却中 |
| `409` `fred_api_key_missing` / `macro_disabled` / `worker_task_disabled` | 未配置或已关闭 |
| `503` `worker_unavailable` / `worker_state_unavailable` | Worker 不可用 |

请求线程只入队，**不触网**：不请求 FRED、不下载 ETF。
普通 Customer Account 与匿名访客一律 `401`。

---

## 5. 权限矩阵

| 能力 | 匿名访客 | Customer Account | Owner |
| --- | --- | --- | --- |
| 读当前宏观数据 | ✅ | ✅ | ✅ |
| 读历史 / 模块 / 因子历史 | ✅ | ✅ | ✅ |
| 手动刷新 | ❌ | ❌ | ✅ |
| 查看 Worker 运行详情 | ❌ | ❌ | ✅ |
| 修改宏观设置 | ❌ | ❌ | ✅（服务器端） |

宏观数据**不按账号隔离**：三种身份读到完全相同的快照。
Customer Cookie 不会带来任何 Owner 权限。

---

## 6. 数据文件与备份

`/data/macro-conditions.db`（独立于 `optix.db`、`catalyst-cache.db`、`ai-jobs.db`、
`accounts.db`）。独立成库的原因：宏观历史与突破事件生命周期不同、避免耦合突破 Schema、
可独立备份与迁移，并且不需要新增服务。

- WAL、`busy_timeout`、`foreign_keys=ON`、`synchronous=FULL`
- 幂等原子迁移 + `integrity_check` + `foreign_key_check`
- 已加入 `maintenance`（每 6 小时备份）与 `retention`（裁剪前先备份）的库清单，
  备份文件名前缀 `macro-conditions-`

健康自检：

```bash
./scripts/compose.sh exec -T worker python -c '
from app.data_paths import get_data_paths
from app.services.macro_conditions.repository import MacroRepository
repository = MacroRepository(get_data_paths().macro_conditions_db)
repository.initialize()
print(repository.integrity_report())
'
```

---

## 7. 排障

| 现象 | 检查 |
| --- | --- |
| `status=disabled` | `./personal.sh secrets status` 看 `FRED_API_KEY`；`[macro].enabled` |
| `status=unavailable` | 还没有任何成功刷新；看 `macro_sync_runs` 最近一行 |
| `status=degraded` | `warnings` 会列出失败的 Series ID 与错误码 |
| `status=stale` | 快照超过 7 天或数据超过 14 天；看最近一次 sync run 的 `status` |
| `insufficient_history` | 数据存在但 5 年分位历史不足，等回填补齐 |
| `fred_api_key_invalid` | FRED 拒绝了这把 key（HTTP 400）。**不是上游故障**：跑 `./personal.sh secrets validate` 看 `FRED_API_KEY` 的 `format_valid`；最常见原因是隐藏提示符不回显导致重复粘贴，存进了 64 或 96 个字符 |
| `fred_rate_limited` | FRED 限流；客户端已尊重 `Retry-After`，等下一个时刻 |
| `fred_units_mismatch` | FRED 改了单位元数据，需要更新 registry 的单位族并过测试 |
| `etf_history_unavailable` | 看 Massive/Yahoo 是否可用；只影响相对收益类因子 |

查看最近刷新：

```bash
./scripts/compose.sh exec -T worker python -c '
from app.data_paths import get_data_paths
from app.services.macro_conditions.repository import MacroRepository
import json
repository = MacroRepository(get_data_paths().macro_conditions_db, read_only=True)
print(json.dumps(repository.recent_sync_runs(5), ensure_ascii=False, indent=2))
'
```

日志只含 Series ID 与安全错误码；**不含** API Key、请求 URL 或上游响应体。

---

## 8. 离线验收

```bash
# 后端全量（含宏观 138 项）
PYTHONPATH=backend python -m pytest -q
python -m compileall -q backend/app

# 前端
npm ci --prefix frontend-src --ignore-scripts --no-audit --no-fund
VITE_API_MODE=live npm run build --prefix frontend-src
diff -r frontend-src/dist frontend
node --experimental-strip-types --test frontend-src/tests/*.test.mjs
node frontend-src/tests/static_assertions.mjs
npm --prefix frontend-src run test:visual
```

CI 另有一步离线容器 smoke：注入 Fake FRED Transport 与合成 ETF fixture，
跑完一次 `MacroConditionsTask`，检查宏观数据库与 API，**不访问真实网络**。
