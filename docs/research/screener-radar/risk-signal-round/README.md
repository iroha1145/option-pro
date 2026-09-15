# 风险与信号优先级轮（2026-09-15）

从 PR #164 执行 SHA `adac675c272fb0d3900f275c1ae0195b4f74a7c5` 继续。本轮先修会改变判断的统计口径，再评估 C1 持仓行业预算与 T1 优先级。

## 冻结

- 不改线上默认评分，候选默认关闭
- 不揭盲封存区
- 不追加 F1/F2 搜索
- 不改 A0 权重、Top-K、期限、T1 阈值
- 50bps 尾部容差仍对应 `daily_worst_name_mean`（每日 Top10 最差一只的平均 20 日收益）
- C1 行业名义上限 20%；T1 优先默认关注名额 K=3
- 验证区 2023–2024-05 已看过，只作已用追踪，不再冒称独立验证

## 命令

```bash
PYTHONPATH=backend:. .venv/bin/pytest -q tests/test_risk_signal_round.py tests/test_algorithm_candidates.py

PYTHONPATH=backend python scripts/research/analyze_risk_signal_round.py \
  --rows "$HOME/research/screener-radar-data/runs/dev-screener-rows.json" \
  --events "$HOME/research/screener-radar-data/runs/algorithm-round-radar/radar-events-with-t1-t2.json" \
  --dataset "$HOME/research/screener-radar-data/yahoo-daily" \
  --prior-a0-signals "$HOME/research/screener-radar-data/runs/algorithm-round/signals-a0-top10.json" \
  --out-dir "$HOME/research/screener-radar-data/runs/risk-signal-round"
```

回滚：不调用 `simulate_c1_sector_budget` / `compare_t1_priority`，生产 `research_ranking=None`。
