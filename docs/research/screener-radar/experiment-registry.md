# 实验台账

权威登记文件在数据目录，不进 Git：

`/opt/cursor/research/screener-radar-data/experiment_registry.jsonl`

每行必须包含：trial_id、layer（original / repair / candidate / baseline）、family、split、commit、数据哈希、参数、输出路径、样本剔除原因、指标、决定。

恢复上下文时先读本文件与 `status.md`，不要把已失败搜索重新包装成独立验证。
