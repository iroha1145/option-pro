# 本机烟雾比对输出（2026-09-28）

`smoke_compare.py` 与 `discovery_misses.py` 的 `summary.json`、`events.csv`、`events_replay_only.csv`，按运行分目录：

- `v1_delay0/`：代理第一版，K 线即时可见（DATA_SPEC 20.8、20.10）
- `v1_lag563/`：代理第一版，K 线延迟 563 秒（20.10）
- `v2_lag563/`：代理第二版（15 分钟延迟视图），K 线延迟 563 秒（20.11、20.12）；`misses_primary` 是主段的发现层缺失归因
- `rvol_window/`：回看 20 对 19 的 rvol 误差比对（20.10）

`primary` 是 09-18 到 09-25，`secondary` 是 09-08 到 09-15（生产侧去掉今天会剔除的杠杆基金），`_noetf` 是两边都去掉 ETF 的口径。完整的账本与快照在会话临时目录，没有进仓库。
