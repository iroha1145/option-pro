# 选股 / 突破雷达研究

本目录是本次历史回测与有效性审计的仓库入口。大行情文件、逐日轨迹和净值序列放在隔离数据目录，不进入 Git。

## 冻结基线

- 远程：`origin/main`
- 提交：`31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`
- 分支：`cursor/screener-radar-backtest-5ee5`
- worktree：`/opt/cursor/research/screener-radar`
- 数据目录：`/opt/cursor/research/screener-radar-data`
- 协议版本：`screener-radar-research-protocol-v1`

## 文档

- [任务书与授权边界](task-brief.md)
- [实现地图](implementation-map.md)
- [数据能力表](data-capability.md)
- [冻结实验协议](protocol.md)
- [当前状态](status.md)
- [实验台账说明](experiment-registry.md)

## 命令

研究执行层只读离线数据，不在回放里联网。

```bash
# 协议与哈希
PYTHONPATH=backend python -m app.services.research.cli protocol

# 有界下载（与回放分离）
python scripts/research/fetch_daily_ohlcv.py \
  --out /opt/cursor/research/screener-radar-data/yahoo-daily \
  --start 2018-01-01

# 数据审计
PYTHONPATH=backend python -m app.services.research.cli audit-data \
  --dataset /opt/cursor/research/screener-radar-data/yahoo-daily \
  --out /opt/cursor/research/screener-radar-data/runs/data-coverage.json

# 开发区选股点时重建（默认不会打开封存区）
PYTHONPATH=backend python -m app.services.research.cli screener-replay \
  --dataset /opt/cursor/research/screener-radar-data/yahoo-daily \
  --split development \
  --out /opt/cursor/research/screener-radar-data/runs/dev-screener.json \
  --registry /opt/cursor/research/screener-radar-data \
  --step 5

# 开发区雷达日线重建
PYTHONPATH=backend python -m app.services.research.cli radar-replay \
  --dataset /opt/cursor/research/screener-radar-data/yahoo-daily \
  --split development \
  --out /opt/cursor/research/screener-radar-data/runs/dev-radar.json \
  --registry /opt/cursor/research/screener-radar-data \
  --step 5
```

专项测试：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
  python3 -m pytest -q \
  tests/test_research_screener_radar.py \
  tests/test_breakout_research.py \
  tests/test_breakout_research_validation.py \
  tests/test_strength_score_scope_invariance.py
```
