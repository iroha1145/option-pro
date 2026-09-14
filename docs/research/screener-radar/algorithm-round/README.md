# 算法改进轮（2026-09-14）

独立于 PR #163 的合并审查。目标是改排序、时点和风险规则，并用同口径对照回答是否值得保留。

## 本轮基线

- 生产 `main`：`df1bd5d35e8128805d75291126341be06e38b1e6`（评分 `strength-v3`）
- 研究工具：从 PR #163 分支 `cursor/screener-radar-backtest-5ee5` 复用，不是当前线上默认
- 工作分支：`cursor/screener-radar-algorithm-1695`
- 数据：本机 `~/research/screener-radar-data`（旧 VM `/opt/cursor/research/...` 不存在）
- 设计分区：development 2019-01-02 至 2022-12-30
- 验证分区：冻结候选后再跑一次；封存区不揭盲

旧开发区 IC / 账本数字是 2026-09-14 历史报告值，必须重跑后才能当作本轮事实。

## 候选（默认关闭）

| ID | 层 | 规则 | 切换 |
|----|----|------|------|
| A0 | 排序 | `0.5*score_mid + 0.5*score_long`，缺一不可 | `_scan_sync(..., research_ranking="a0_mid_long")` |
| C0 | 风险-集中度 | 原排序 Top10 每主行业最多 2 只，空位保留 | `research_ranking="c0_sector_quota"` |
| T1 | 时点 | 日线强确认代理，T 收盘可知，T+1 开盘执行 | `evaluate_t1` |
| T2 | 时点 | T 与 T+1 收盘都守住 T 冻结阻力，T+2 开盘执行 | `evaluate_t2` |

生产默认 `research_ranking=None`，检测器公式不变。

## 命令

```bash
python scripts/research/fetch_daily_ohlcv.py \
  --out "$HOME/research/screener-radar-data/yahoo-daily" \
  --start 2017-01-01 --end 2024-06-29

PYTHONPATH=backend python -m app.services.research.cli screener-replay \
  --dataset "$HOME/research/screener-radar-data/yahoo-daily" \
  --split development \
  --out "$HOME/research/screener-radar-data/runs/dev-screener.json" \
  --registry "$HOME/research/screener-radar-data" \
  --workers 3 --resume

PYTHONPATH=backend python -m app.services.research.cli radar-replay \
  --dataset "$HOME/research/screener-radar-data/yahoo-daily" \
  --split development \
  --out "$HOME/research/screener-radar-data/runs/dev-radar.json" \
  --registry "$HOME/research/screener-radar-data" \
  --workers 3 --resume

python scripts/research/analyze_algorithm_round.py \
  --rows "$HOME/research/screener-radar-data/runs/dev-screener-rows.json" \
  --events "$HOME/research/screener-radar-data/runs/dev-radar-events.json" \
  --dataset "$HOME/research/screener-radar-data/yahoo-daily" \
  --out-dir "$HOME/research/screener-radar-data/runs/algorithm-round" \
  --registry "$HOME/research/screener-radar-data"
```

回滚：不要传 `research_ranking`，或不调用 T1/T2。
